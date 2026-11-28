"""Team search shared by the config flow and the season rollover repair flow.

Both guide the user from "a club name, a team URL or a team ID" to the data of
one FFBB team (engagement): the pure parts and the API lookups, with their
error keys, live here so the two flows behave identically and can't drift.
The flows themselves (forms, steps, what happens at the end) stay in their own
modules.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import (
    FFBBApiError,
    FFBBClient,
    FFBBConnectionError,
    FFBBNotFoundError,
)
from .const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DOMAIN,
    FRESH_EVENTS,
    FRESH_RANK_EVOLUTION,
)
from .coordinator import (
    _engagement_id,
    as_dict,
    delete_team_issues,
    drop_unused_poule_cache,
    forget_not_found,
    str_or_empty,
)

_LOGGER = logging.getLogger(__name__)

MIN_SEARCH_QUERY_LENGTH = 2
# The engagement ID is the FIRST number after /equipe(s), taken as a whole path
# segment: a trailing number (".../equipes/123456/2025") is not the ID, and
# neither is the start of an intermediate segment (".../equipes/2025-2026/123456").
URL_ID_PATTERN = re.compile(
    r"/[eé]quipes?(?:/[^/\s]+)*?/(\d+)(?=/|[?#]|\s|$)", re.IGNORECASE
)
RAW_ID_PATTERN = re.compile(r"^\d+$")

# The team list can be long (a club easily has 30 to 50 teams). Above this many
# teams the team step offers a text filter, and every list is sorted.
CONF_TEAM_FILTER = "team_filter"
TEAM_FILTER_MIN_TEAMS = 6
TEAM_WORD_PATTERN = re.compile(r"[^\W\d_]+|\d+")
TEAM_WORD_PREFIX_LENGTH = 5


def engagement_id_from_query(query: str) -> str | None:
    """Return the engagement ID when the query is a team URL or a bare ID."""
    url_match = URL_ID_PATTERN.search(query)
    if url_match:
        return url_match.group(1)
    if RAW_ID_PATTERN.match(query):
        return query
    return None


def may_be_club_search(query: str, error: str | None) -> bool:
    """Tell whether a bare number the FFBB does not know could be a club search.

    A bare number is first taken for a team (engagement) ID. When the FFBB has
    no such team, the number may just as well be what the user wanted to search
    a club for ("40" for a department, a club code made of digits...). Only an
    unknown ID falls back to the club search: a connection problem or a team
    without a pool says nothing about the query, and a pasted URL is never a
    club search. A query too short to search a club for is not retried either.
    """
    return (
        error == "invalid_engagement"
        and RAW_ID_PATTERN.match(query) is not None
        and len(query) >= MIN_SEARCH_QUERY_LENGTH
    )


def club_options(clubs: list[dict[str, Any]]) -> dict[str, str]:
    """Return the club choices of the club step, keyed by club ID."""
    return {
        str_or_empty(club.get("id")): (
            f"{str_or_empty(club.get('nom')) or 'Club'} "
            f"({str_or_empty(club.get('code')) or 'N/A'})"
        )
        for club in clubs
    }


def team_label(engagement: dict[str, Any]) -> str:
    """Return the text a team is listed under: "team — competition (pool)"."""
    team_name = str_or_empty(engagement.get("nom")) or "Équipe"
    comp = as_dict(engagement.get("idCompetition"))
    comp_name = str_or_empty(comp.get("nom")) or "Compétition"
    poule = as_dict(engagement.get("idPoule"))
    poule_name = str_or_empty(poule.get("nom"))
    label = f"{team_name} — {comp_name}"
    if poule_name:
        label += f" ({poule_name})"
    return label


def team_options(engagements: list[dict[str, Any]]) -> dict[str, str]:
    """Return the choices of the team step, keyed by engagement ID."""
    return {str_or_empty(eng.get("id")): team_label(eng) for eng in engagements}


def _fold(text: str) -> str:
    """Lower-case a text and drop its accents, to compare it loosely."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _words(text: str) -> set[str]:
    """Return the meaningful words of a competition name, loosely normalised.

    Numbers are kept whole ("13" in "U13M"), single letters are dropped and
    words are cut short so that "masculin" and "masculine" compare equal.
    """
    words = set()
    for word in TEAM_WORD_PATTERN.findall(_fold(text)):
        if word.isdigit():
            words.add(word)
        elif len(word) > 1:
            words.add(word[:TEAM_WORD_PREFIX_LENGTH])
    return words


def competition_score(engagement: dict[str, Any], reference_competition: str) -> int:
    """Return how many words a team's competition shares with the reference."""
    comp = as_dict(engagement.get("idCompetition"))
    return len(_words(reference_competition) & _words(str_or_empty(comp.get("nom"))))


def sort_teams(
    engagements: list[dict[str, Any]], reference_competition: str = ""
) -> list[dict[str, Any]]:
    """Sort teams, the closest to the reference competition first.

    The FFBB's own order isn't stable between teams that share a name (all of a
    club's teams can carry the club's name), so the order is set here: by how
    much each competition resembles the reference (the competition of the team
    being replaced, when there is one), then by label.
    """
    return sorted(
        engagements,
        key=lambda eng: (
            -competition_score(eng, reference_competition),
            _fold(team_label(eng)),
        ),
    )


def filter_teams(engagements: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """Keep the teams whose label contains every word typed, in any order."""
    words = _fold(text).split()
    if not words:
        return list(engagements)
    return [
        eng
        for eng in engagements
        if all(word in _fold(team_label(eng)) for word in words)
    ]


class TeamChooser:
    """The team step's list: sorted, with a text filter when it is long.

    Shared by the config flow and the repair flows. A form can't react while
    the user types, so the filter works in two submissions: typing a filter and
    submitting redisplays the form with the matching teams; submitting again
    without changing it confirms the team chosen in the list.
    """

    def __init__(
        self, engagements: list[dict[str, Any]], reference_competition: str = ""
    ) -> None:
        """Sort the teams; remember whether the sort found a close match."""
        self._teams = sort_teams(engagements, reference_competition)
        self._ranked = bool(self._teams) and (
            competition_score(self._teams[0], reference_competition) > 0
        )
        self.team_filter = ""
        self.errors: dict[str, str] = {}

    @property
    def filterable(self) -> bool:
        """Whether the list is long enough to offer the filter."""
        return len(self._teams) > TEAM_FILTER_MIN_TEAMS

    @property
    def teams(self) -> list[dict[str, Any]]:
        """The teams to list: the matching ones, or all when nothing matches."""
        return filter_teams(self._teams, self.team_filter) or self._teams

    def apply_filter(self, user_input: dict[str, Any]) -> bool:
        """Take the filter of a submission; True when it changed.

        When it did, the submission only asked to filter: the form must be
        shown again, and the team in it is not a confirmation.
        """
        if not self.filterable:
            return False
        new_filter = str(user_input.get(CONF_TEAM_FILTER) or "").strip()
        if new_filter == self.team_filter:
            return False
        self.team_filter = new_filter
        no_match = bool(new_filter) and not filter_teams(self._teams, new_filter)
        self.errors = {CONF_TEAM_FILTER: "no_team_matches"} if no_match else {}
        return True

    def schema_fields(self, *, default_first: bool = False) -> dict[Any, Any]:
        """Return the form fields: the filter (if long), then the team.

        The first team is preselected when it is a close match, when a filter
        is applied, or when asked (`default_first`).
        """
        fields: dict[Any, Any] = {}
        if self.filterable:
            fields[
                vol.Optional(
                    CONF_TEAM_FILTER, description={"suggested_value": self.team_filter}
                )
            ] = str
        teams = self.teams
        default: Any = vol.UNDEFINED
        if (
            teams
            and not self.errors
            and (default_first or self._ranked or self.team_filter)
        ):
            default = str_or_empty(teams[0].get("id"))
        fields[vol.Required("engagement_id", default=default)] = vol.In(
            team_options(teams)
        )
        return fields


def build_entry(
    engagement: dict[str, Any], engagement_id: str, organisme_id: str
) -> tuple[dict[str, Any], str]:
    """Return the config entry data and title for a selected engagement."""
    team_name = str_or_empty(engagement.get("nom")) or "Équipe"
    competition = as_dict(engagement.get("idCompetition"))
    competition_name = str_or_empty(competition.get("nom")) or "Compétition"
    data = {
        CONF_ENGAGEMENT_ID: engagement_id,
        CONF_TEAM_NAME: team_name,
        CONF_COMPETITION_NAME: competition_name,
        CONF_POULE_ID: _engagement_id(engagement.get("idPoule")),
        CONF_ORGANISME_ID: organisme_id,
    }
    return data, f"{team_name} - {competition_name}"


# Most clubs offered in the selection list. The API cuts the list off silently
# (sorted by name), so one more is requested to tell "exactly this many" from
# "more than this": past the limit the user is asked to narrow the search
# instead of being shown a list that may not contain their club.
CLUB_SEARCH_LIMIT = 100


async def search_clubs(
    client: FFBBClient, query: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Search clubs. Returns (clubs, error key); the error key is None on success."""
    try:
        clubs = await client.search_clubs(query, limit=CLUB_SEARCH_LIMIT + 1)
    except FFBBConnectionError:
        return [], "cannot_connect"
    except FFBBApiError as err:
        _LOGGER.error("Error during club search: %s", err)
        return [], "unknown"
    if not clubs:
        return [], "no_clubs_found"
    if len(clubs) > CLUB_SEARCH_LIMIT:
        return [], "too_many_clubs"
    return clubs, None


async def club_teams(
    client: FFBBClient, club_id: str
) -> tuple[list[dict[str, Any]], str | None]:
    """List a club's teams that belong to a pool. Returns (teams, error key)."""
    try:
        engagements = await client.get_club_engagements(club_id)
    except FFBBConnectionError:
        return [], "cannot_connect"
    except FFBBApiError as err:
        _LOGGER.error("Error retrieving club engagements: %s", err)
        return [], "unknown"
    valid = [
        eng
        for eng in engagements
        if isinstance(eng, dict) and _engagement_id(eng.get("idPoule"))
    ]
    if not valid:
        return [], "no_teams_found"
    return valid, None


async def lookup_engagement(
    client: FFBBClient, engagement_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch one team by engagement ID. Returns (engagement, error key)."""
    try:
        engagement = await client.get_engagement(engagement_id)
    except FFBBNotFoundError:
        return None, "invalid_engagement"
    except FFBBConnectionError:
        return None, "cannot_connect"
    except FFBBApiError as err:
        _LOGGER.error("Direct engagement retrieval error: %s", err)
        return None, "unknown"
    if not _engagement_id(engagement.get("idPoule")):
        return None, "no_poule_found"
    return engagement, None


def migrate_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    new_engagement_id: str,
    new_poule_id: str | None = None,
) -> None:
    """Make the entry's entities and device follow the team to a new engagement.

    Entities and the device are keyed by engagement ID. Switching the entry to
    another team used to remove the device and let the integration recreate
    everything, which changed the entity IDs whenever the team or competition
    name changed (the usual case at a season rollover) and always lost the
    user's customizations. Moving the existing registry entries to the new
    engagement ID instead keeps their entity IDs, custom names, history and
    area: only the team they follow changes.

    Call it before the entry is updated and reloaded. A device or entity whose
    new ID is already taken is left as is (a warning is logged for entities),
    and any device that still doesn't belong to the new engagement is removed.
    If a device for the new engagement already exists, nothing is moved onto it
    (see below) and the entry's entities are recreated as before.
    """
    old_engagement_id = str_or_empty(entry.data.get(CONF_ENGAGEMENT_ID))
    old_poule_id = str_or_empty(entry.data.get(CONF_POULE_ID))
    team_changed = bool(old_engagement_id and old_engagement_id != new_engagement_id)
    pool_changed = bool(
        old_poule_id and new_poule_id is not None and old_poule_id != new_poule_id
    )

    if team_changed or pool_changed:
        forget_not_found(hass, old_engagement_id, old_poule_id)
        delete_team_issues(hass, old_engagement_id)
    if pool_changed:
        drop_unused_poule_cache(hass, old_poule_id, exclude_entry_id=entry.entry_id)

    if team_changed:
        if _team_device_exists(hass, new_engagement_id):
            # Home Assistant doesn't refuse two devices with the same
            # identifiers, it silently lets them coexist -- and entities can
            # then end up on the wrong one. Don't move anything onto it: fall
            # back to recreating the entry's entities under the new team.
            _LOGGER.info(
                "A device for team %s already exists; the entities of this "
                "entry will be recreated instead of moved",
                new_engagement_id,
            )
        else:
            _migrate_entity_ids(hass, entry, old_engagement_id, new_engagement_id)
            _migrate_device(hass, entry, old_engagement_id, new_engagement_id)
    elif pool_changed:
        _flag_pool_bound_entities(hass, entry, new_engagement_id)
    remove_stale_devices(hass, entry, new_engagement_id)


def _flag_fresh_key(hass: HomeAssistant, key: str, unique_id: str) -> None:
    """Mark a pool-bound entity so its old pool's state is not restored."""
    if key == "rank_evolution":
        hass.data.setdefault(DOMAIN, {}).setdefault(FRESH_RANK_EVOLUTION, set()).add(
            unique_id
        )
    elif key in ("match_finished", "rank_changed"):
        hass.data.setdefault(DOMAIN, {}).setdefault(FRESH_EVENTS, set()).add(unique_id)


def _flag_pool_bound_entities(
    hass: HomeAssistant, entry: ConfigEntry, engagement_id: str
) -> None:
    """Reset pool-bound entities when the team moves to a new pool under the same ID."""
    registry = er.async_get(hass)
    prefix = f"{engagement_id}_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id.startswith(prefix):
            _flag_fresh_key(
                hass, entity.unique_id.removeprefix(prefix), entity.unique_id
            )


def _team_device_exists(hass: HomeAssistant, engagement_id: str) -> bool:
    """Return whether any FFBB Tracker entry already has a device for this team.

    Scans the integration's own entries instead of calling
    DeviceRegistry.async_get_device, which Home Assistant deprecates (device
    identifiers are no longer unique across config entries). The newer
    by-identifier lookups don't exist yet on the oldest supported release.
    """
    registry = dr.async_get(hass)
    identifier = (DOMAIN, engagement_id)
    return any(
        identifier in device.identifiers
        for config_entry in hass.config_entries.async_entries(DOMAIN)
        for device in dr.async_entries_for_config_entry(registry, config_entry.entry_id)
    )


def _migrate_entity_ids(
    hass: HomeAssistant, entry: ConfigEntry, old_id: str, new_id: str
) -> None:
    """Give the entry's entities the unique ID of the new engagement."""
    registry = er.async_get(hass)
    prefix = f"{old_id}_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not entity.unique_id.startswith(prefix):
            continue
        key = entity.unique_id.removeprefix(prefix)
        new_unique_id = f"{new_id}_{key}"
        try:
            registry.async_update_entity(entity.entity_id, new_unique_id=new_unique_id)
        except (ValueError, HomeAssistantError) as err:
            _LOGGER.warning(
                "Could not keep %s for the new team (%s); it will be left "
                "unavailable and can be deleted: %s",
                entity.entity_id,
                new_unique_id,
                err,
            )
            continue
        _flag_fresh_key(hass, key, new_unique_id)


def _migrate_device(
    hass: HomeAssistant, entry: ConfigEntry, old_id: str, new_id: str
) -> None:
    """Point the entry's device at the new engagement, keeping its area etc."""
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if (DOMAIN, old_id) not in device.identifiers:
            continue
        try:
            registry.async_update_device(device.id, new_identifiers={(DOMAIN, new_id)})
        except (ValueError, HomeAssistantError) as err:
            # Defensive: the check in migrate_entities already rules out the
            # one conflict we know of (a device that has the new ID), and Home
            # Assistant doesn't raise here in practice. If it ever does, don't
            # fail the whole switch: log it and let remove_stale_devices
            # clean the device up.
            _LOGGER.debug("Device %s left for removal: %s", device.id, err)


def remove_stale_devices(
    hass: HomeAssistant, entry: ConfigEntry, engagement_id: str
) -> None:
    """Remove the entry's devices that don't belong to the new engagement.

    Entities are keyed by engagement ID, so after the team changes the old
    device and its entities would otherwise linger as unavailable.
    """
    dev_reg = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if (DOMAIN, engagement_id) not in device_entry.identifiers:
            dev_reg.async_remove_device(device_entry.id)
