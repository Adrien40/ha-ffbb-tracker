"""Diagnostics support for the FFBB Tracker integration."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import FFBBConfigEntry
from .coordinator import FFBBDataUpdateCoordinator, MatchDetails

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
        "api": _api_summary(coordinator),
        "data": data,
    }

    return async_redact_data(diagnostics, TO_REDACT)


def _api_summary(coordinator: FFBBDataUpdateCoordinator) -> dict[str, Any]:
    """Describe what the FFBB API last answered, to investigate delayed scores.

    Holds no personal data: only timestamps, cache-related response headers,
    match numbers and the raw result fields of past matches that still have no
    result, plus what the outdated-response safety net did (see
    FFBBDataUpdateCoordinator._async_recheck_missing_results).
    """
    fetched_at = coordinator.last_api_fetch_at
    rechecked_at = coordinator.last_recheck_at
    return {
        "last_fetch_at": fetched_at.isoformat() if fetched_at else None,
        "response_headers": getattr(coordinator.client, "last_poule_headers", {}),
        "pending_results": coordinator.pending_results,
        "stale_recheck": {
            "attempts": coordinator.stale_recheck_attempts,
            "outdated_responses_detected": coordinator.stale_responses_detected,
            "last_attempt_at": rechecked_at.isoformat() if rechecked_at else None,
            "last_outcome": coordinator.last_recheck_outcome,
        },
    }


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
