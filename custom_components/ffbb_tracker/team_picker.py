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
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

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
)

_LOGGER = logging.getLogger(__name__)

MIN_SEARCH_QUERY_LENGTH = 2
URL_ID_PATTERN = re.compile(r"/[eé]quipes?(?:/[^/\s]+)*/(\d+)/?", re.IGNORECASE)
RAW_ID_PATTERN = re.compile(r"^\d+$")


def engagement_id_from_query(query: str) -> str | None:
    """Return the engagement ID when the query is a team URL or a bare ID."""
    url_match = URL_ID_PATTERN.search(query)
    if url_match:
        return url_match.group(1)
    if RAW_ID_PATTERN.match(query):
        return query
    return None


def club_options(clubs: list[dict[str, Any]]) -> dict[str, str]:
    """Return the club choices of the club step, keyed by club ID."""
    return {
        str(club["id"]): f"{club.get('nom', 'Club')} ({club.get('code', 'N/A')})"
        for club in clubs
    }


def team_options(engagements: list[dict[str, Any]]) -> dict[str, str]:
    """Return the team choices of the team step, keyed by engagement ID."""
    options = {}
    for eng in engagements:
        team_name = eng.get("nom", "Équipe")
        comp = eng.get("idCompetition") or {}
        comp_name = comp.get("nom", "Compétition")
        poule = eng.get("idPoule") or {}
        poule_name = poule.get("nom", "")
        label = f"{team_name} — {comp_name}"
        if poule_name:
            label += f" ({poule_name})"
        options[str(eng["id"])] = label
    return options


def build_entry(
    engagement: dict[str, Any], engagement_id: str, organisme_id: str
) -> tuple[dict[str, Any], str]:
    """Return the config entry data and title for a selected engagement."""
    team_name = engagement.get("nom", "Équipe")
    competition = engagement.get("idCompetition") or {}
    competition_name = competition.get("nom", "Compétition")
    poule = engagement.get("idPoule") or {}
    data = {
        CONF_ENGAGEMENT_ID: engagement_id,
        CONF_TEAM_NAME: team_name,
        CONF_COMPETITION_NAME: competition_name,
        CONF_POULE_ID: str(poule.get("id")),
        CONF_ORGANISME_ID: organisme_id,
    }
    return data, f"{team_name} - {competition_name}"


async def search_clubs(
    client: FFBBClient, query: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Search clubs. Returns (clubs, error key); the error key is None on success."""
    try:
        clubs = await client.search_clubs(query)
    except FFBBConnectionError:
        return [], "cannot_connect"
    except FFBBApiError as err:
        _LOGGER.error("Error during club search: %s", err)
        return [], "unknown"
    if not clubs:
        return [], "no_clubs_found"
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
        eng for eng in engagements if eng.get("idPoule") and eng["idPoule"].get("id")
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
    if not (engagement.get("idPoule") or {}).get("id"):
        return None, "no_poule_found"
    return engagement, None


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
