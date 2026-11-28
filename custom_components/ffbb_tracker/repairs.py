"""Repair flows for the FFBB Tracker integration.

Only `season_rollover` and `phase_change` are fixable. At a season rollover
the FFBB assigns new engagement and pool IDs, so the only real "fix" is to
search for the team again. That search runs here, inside the repair dialog,
using the same team search as the config flow (see team_picker.py): the user
finds the team under its new IDs, and the existing config entry is switched
over to it.

(An earlier version started the entry's reconfigure flow from here. Home
Assistant doesn't list flows started that way, so the user never saw it.)

Both flows open on a small menu: follow the team (the steps described above)
or ignore the alert. Ignoring marks the issue as ignored in Home Assistant's
issue registry and ends the flow with an abort: a normal finish (create_entry)
would make Home Assistant delete the issue, and the coordinator would raise
it again on the next refresh. An ignored issue stays ignored for as long as it
keeps being raised, and disappears by itself once matches are scheduled again.

`token_refresh_failing` stays `is_fixable=False`: the description already
points at the two things a user can actually do (update the integration, or
report it), and there's no flow to hand off to.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import get_rate_limiter
from .api import FFBBClient
from .const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DOMAIN,
)
from .coordinator import _engagement_id, str_or_empty
from .team_picker import (
    MIN_SEARCH_QUERY_LENGTH,
    TeamChooser,
    build_entry,
    club_options,
    club_teams,
    engagement_id_from_query,
    lookup_engagement,
    may_be_club_search,
    migrate_entities,
    search_clubs,
)


class SeasonRolloverRepairFlow(RepairsFlow):
    """Find the team again under its new IDs, then switch the entry over."""

    def __init__(self, entry_id: str) -> None:
        """Store the config entry the issue was raised for."""
        self._entry_id = entry_id
        self._client: FFBBClient | None = None
        self._clubs: list[dict[str, Any]] = []
        self._selected_club: dict[str, Any] | None = None
        self._engagements: list[dict[str, Any]] = []
        self._chooser: TeamChooser | None = None

    def _get_api_client(self) -> FFBBClient:
        """Return the API client, sharing the integration's rate limiter."""
        if self._client is None:
            self._client = FFBBClient(
                async_get_clientsession(self.hass),
                rate_limiter=get_rate_limiter(self.hass),
            )
        return self._client

    def _reference_competition(self) -> str:
        """Return the competition of the tracked team, to sort the list by."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return str_or_empty(entry.data.get(CONF_COMPETITION_NAME)) if entry else ""

    def _placeholders(self) -> dict[str, str]:
        """Return the team name for the step texts.

        Home Assistant doesn't add the issue's placeholders to the forms of a
        custom repair flow, so every form passes them explicitly.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return {
            "team_name": str_or_empty(entry.data.get(CONF_TEAM_NAME)) if entry else ""
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """First step: follow the team, or ignore the alert.

        Home Assistant calls this with the data the flow was started with
        ({"issue_id": ...}) as `user_input`; it is not a choice and is not
        used. If the config entry was removed before the user opened this
        dialog, close the stale repair issue straight away.
        """
        if self.hass.config_entries.async_get_entry(self._entry_id) is None:
            return self.async_create_entry(data={})
        return self.async_show_menu(
            step_id="init",
            menu_options=["follow", "ignore"],
            description_placeholders=self._placeholders(),
        )

    async def async_step_follow(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Follow the team: open the team search directly.

        `user_input` must not be forwarded to `async_step_search`, which would
        treat the menu payload as a submitted search form.
        """
        if self.hass.config_entries.async_get_entry(self._entry_id) is None:
            return self.async_create_entry(data={})
        return await self.async_step_search()

    async def async_step_ignore(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Ignore the alert: it stays in the registry, hidden, until it is over."""
        if ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id) is not None:
            ir.async_ignore_issue(self.hass, DOMAIN, self.issue_id, True)
        return self.async_abort(reason="ignored")

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Search by club name, team URL or team ID."""
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input["search_query"].strip()
            engagement_id = engagement_id_from_query(query)

            if engagement_id is None and len(query) < MIN_SEARCH_QUERY_LENGTH:
                errors["base"] = "query_too_short"
            elif engagement_id is not None:
                engagement, error = await lookup_engagement(
                    self._get_api_client(), engagement_id
                )
                if engagement is None:
                    # Not a known team: a bare number may be a club search.
                    if may_be_club_search(query, error):
                        clubs, club_error = await search_clubs(
                            self._get_api_client(), query
                        )
                        if not club_error:
                            self._clubs = clubs
                            return await self.async_step_club()
                        if club_error != "no_clubs_found":
                            # Too many clubs, connection problem...: that is the
                            # real reason, not an invalid team ID.
                            error = club_error
                    errors["base"] = error or "unknown"
                else:
                    data, title = build_entry(
                        engagement,
                        engagement_id,
                        _engagement_id(engagement.get("idOrganisme")),
                    )
                    result, error = self._switch_entry(engagement_id, data, title)
                    if result is not None:
                        return result
                    errors["base"] = error or "unknown"
            else:
                clubs, error = await search_clubs(self._get_api_client(), query)
                if error:
                    errors["base"] = error
                else:
                    self._clubs = clubs
                    return await self.async_step_club()

        return self.async_show_form(
            step_id="search",
            data_schema=vol.Schema({vol.Required("search_query"): str}),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    async def async_step_club(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Choose the club among the search results."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_id = str_or_empty(user_input.get("club_id"))
            self._selected_club = next(
                (
                    club
                    for club in self._clubs
                    if str_or_empty(club.get("id")) == selected_id
                ),
                None,
            )
            if self._selected_club is None:
                return self.async_abort(reason="club_not_found")

            engagements, error = await club_teams(self._get_api_client(), selected_id)
            if error:
                errors["base"] = error
            else:
                self._engagements = engagements
                self._chooser = None
                return await self.async_step_team()

        return self.async_show_form(
            step_id="club",
            data_schema=vol.Schema(
                {vol.Required("club_id"): vol.In(club_options(self._clubs))}
            ),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    async def async_step_team(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Choose the team, which switches the config entry over to it."""
        if self._chooser is None:
            self._chooser = TeamChooser(
                self._engagements, self._reference_competition()
            )
        chooser = self._chooser
        errors: dict[str, str] = {}

        if user_input is not None and not chooser.apply_filter(user_input):
            engagement_id = str_or_empty(user_input.get("engagement_id"))
            selected = next(
                (
                    eng
                    for eng in self._engagements
                    if str_or_empty(eng.get("id")) == engagement_id
                ),
                None,
            )
            if selected is not None:
                organisme_id = (
                    str_or_empty(self._selected_club.get("id"))
                    if self._selected_club
                    else ""
                )
                data, title = build_entry(selected, engagement_id, organisme_id)
                result, error = self._switch_entry(engagement_id, data, title)
                if result is not None:
                    return result
                errors["base"] = error or "unknown"
        else:
            errors = dict(chooser.errors)

        return self.async_show_form(
            step_id="team",
            data_schema=vol.Schema(chooser.schema_fields()),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    def _switch_entry(
        self, engagement_id: str, data: dict[str, Any], title: str
    ) -> tuple[data_entry_flow.FlowResult | None, str | None]:
        """Point the config entry at the newly found team.

        Returns (result, None) when done, or (None, error key) when the form
        should be shown again with that error. Same effect as reconfiguring
        the entry: its entities and device follow the team to the new IDs
        (see team_picker.migrate_entities), then new data, title and unique
        ID, then a reload. Home Assistant removes the repair issue itself
        when this flow finishes.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="entry_not_found"), None

        existing = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, engagement_id
        )
        if existing is not None and existing.entry_id != entry.entry_id:
            return None, "already_configured"

        migrate_entities(
            self.hass, entry, engagement_id, new_poule_id=data[CONF_POULE_ID]
        )
        self.hass.config_entries.async_update_entry(
            entry, data=data, title=title, unique_id=engagement_id
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_create_entry(data={}), None


class PhaseChangeRepairFlow(SeasonRolloverRepairFlow):
    """Follow a team to its next phase, starting from the club's own list.

    The tracked team has nothing left to play. Before asking the user to search
    for it, look among the club's teams for one with the same name that is not
    the one already tracked: that is the team under its new pool (and, if the
    FFBB assigned one, its new ID). The user only has to confirm. With no such
    team, or when it isn't the right one, the usual search takes over.
    """

    def __init__(self, entry_id: str) -> None:
        """Store the config entry and prepare the candidate list."""
        super().__init__(entry_id)
        self._candidates: list[dict[str, Any]] = []
        self._candidate_chooser: TeamChooser | None = None
        self._organisme_id = ""

    async def async_step_follow(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Look for the team's new entry, then propose it or fall back."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_create_entry(data={})
        self._organisme_id = str_or_empty(entry.data.get(CONF_ORGANISME_ID))
        self._candidates = await self._async_find_candidates(
            self._organisme_id,
            str_or_empty(entry.data.get(CONF_TEAM_NAME)),
            (
                str_or_empty(entry.data.get(CONF_ENGAGEMENT_ID)),
                str_or_empty(entry.data.get(CONF_POULE_ID)),
            ),
        )
        if self._candidates:
            self._candidate_chooser = TeamChooser(
                self._candidates, self._reference_competition()
            )
            return await self.async_step_candidate()
        return await self.async_step_search()

    async def _async_find_candidates(
        self, organisme_id: str, team_name: str, current: tuple[str, str]
    ) -> list[dict[str, Any]]:
        """Return the club's teams that carry this name under another pool/ID.

        A lookup problem simply means no candidate: the search that follows
        reports the same problem if it persists.
        """
        if not organisme_id:
            return []
        engagements, error = await club_teams(self._get_api_client(), organisme_id)
        if error:
            return []
        wanted = team_name.strip().casefold()
        return [
            eng
            for eng in engagements
            if str_or_empty(eng.get("nom")).strip().casefold() == wanted
            and (
                str_or_empty(eng.get("id")),
                _engagement_id(eng.get("idPoule")),
            )
            != current
        ]

    async def async_step_candidate(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Propose the team(s) found, or let the user search for another."""
        if self._candidate_chooser is None:
            self._candidate_chooser = TeamChooser(
                self._candidates, self._reference_competition()
            )
        chooser = self._candidate_chooser
        errors: dict[str, str] = {}

        if user_input is not None and not chooser.apply_filter(user_input):
            if user_input.get("search_other"):
                return await self.async_step_search()
            engagement_id = str_or_empty(user_input.get("engagement_id"))
            selected = next(
                (
                    eng
                    for eng in self._candidates
                    if str_or_empty(eng.get("id")) == engagement_id
                ),
                None,
            )
            if selected is not None:
                data, title = build_entry(selected, engagement_id, self._organisme_id)
                result, error = self._switch_entry(engagement_id, data, title)
                if result is not None:
                    return result
                errors["base"] = error or "unknown"
        else:
            errors = dict(chooser.errors)

        return self.async_show_form(
            step_id="candidate",
            data_schema=vol.Schema(
                {
                    **chooser.schema_fields(default_first=True),
                    vol.Optional("search_other", default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders=self._placeholders(),
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the repair flow for a given issue_id.

    Only season_rollover and phase_change issues reach here with
    is_fixable=True (see coordinator.py); token_refresh_failing issues are
    is_fixable=False and never trigger this.
    """
    entry_id = (data or {}).get("entry_id", "")
    if issue_id.startswith("phase_change"):
        return PhaseChangeRepairFlow(entry_id)
    return SeasonRolloverRepairFlow(entry_id)
