"""Diagnostics support for the FFBB Tracker integration."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import FFBBConfigEntry
from .coordinator import MatchDetails

# The config entry itself holds no secret (the Directus token lives in
# const.py / is refreshed at runtime, never stored on the entry), but we
# redact defensively in case that ever changes.
TO_REDACT = {"token", "Authorization"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FFBBConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    data: dict[str, Any] | None = None
    if coordinator.data:
        team_data = coordinator.data
        data = {
            "team_name": team_data.team_name,
            "competition_name": team_data.competition_name,
            "poule_name": team_data.poule_name,
            "fixtures_count": len(team_data.fixtures),
            "next_match": _match_summary(team_data.next_match),
            "last_match": _match_summary(team_data.last_match),
            "team_standing": (
                dataclasses.asdict(team_data.team_standing)
                if team_data.team_standing
                else None
            ),
            "standings_count": len(team_data.standings),
        }

    diagnostics = {
        "entry_data": dict(entry.data),
        "entry_options": dict(entry.options),
        "engagement_id": coordinator.engagement_id,
        "poule_id": coordinator.poule_id,
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None
        ),
        "last_update_success": coordinator.last_update_success,
        "data": data,
    }

    return async_redact_data(diagnostics, TO_REDACT)


def _match_summary(match: MatchDetails | None) -> dict[str, Any] | None:
    """Return a compact summary of a match, deliberately excluding the
    full raw payload (gym street address, full opponent org, etc.) so the
    diagnostics stay short and don't leak more than needed to debug."""
    if match is None:
        return None
    return {
        "match_date": match.match_date.isoformat() if match.match_date else None,
        "is_home": match.is_home,
        "is_played": match.is_played,
        "team_score": match.team_score,
        "opponent_score": match.opponent_score,
        "result": match.result,
        "gym_city": match.gym_city,
    }
