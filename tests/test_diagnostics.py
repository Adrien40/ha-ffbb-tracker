"""Tests for diagnostics.py.

The main risk here isn't a crash, it's a silent privacy regression: a
future edit to `_match_summary` or `async_get_config_entry_diagnostics`
could start including the full raw match payload (street address, full
opponent org dict) in a diagnostics export a user pastes into a public
GitHub issue. These tests pin down exactly which fields are present so
that kind of change fails loudly in CI instead of shipping quietly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DOMAIN,
)
from custom_components.ffbb_tracker.coordinator import (
    FFBBDataUpdateCoordinator,
    FFBBTeamData,
    MatchDetails,
    TeamStanding,
)
from custom_components.ffbb_tracker.diagnostics import (
    async_get_config_entry_diagnostics,
)


def _make_entry_with_coordinator(hass) -> MockConfigEntry:
    """Add a config entry to hass with a coordinator wired as runtime_data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="engagement-123",
        data={
            CONF_ENGAGEMENT_ID: "engagement-123",
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry.add_to_hass(hass)
    coordinator = FFBBDataUpdateCoordinator(hass, client=None, entry=entry)
    entry.runtime_data = coordinator
    return entry


def _make_match(**overrides) -> MatchDetails:
    """Build a MatchDetails with sane defaults, overridable per test."""
    defaults: dict = {
        "match_id": "match-1",
        "match_number": "1",
        "round_number": "3",
        "match_date": datetime(2026, 1, 10, 19, 0, tzinfo=UTC),
        "is_home": True,
        "team_name": "Basket Landes",
        "opponent_name": "US Mont-de-Marsan",
        "opponent_club_id": "org-2",
        "is_played": True,
        "team_score": 68,
        "opponent_score": 54,
        "result": "win",
        "gym_name": "Gymnase Andre Chavanne",
        "gym_address": "1 rue du Stade",
        "gym_postal_code": "40000",
        "gym_city": "Mont-de-Marsan",
        "team_logo_url": None,
        "opponent_logo_url": None,
        "raw": {"sensitive_internal_field": "should-never-leak"},
    }
    defaults.update(overrides)
    return MatchDetails(**defaults)


def _make_team_data(**overrides) -> FFBBTeamData:
    """Build an FFBBTeamData with sane defaults, overridable per test."""
    defaults: dict = {
        "engagement_id": "engagement-123",
        "poule_id": "poule-1",
        "team_name": "Basket Landes",
        "competition_name": "Excellence Régionale",
        "poule_name": "Excellence Régionale - Poule A",
        "next_match": None,
        "last_match": None,
        "team_standing": None,
        "standings": [],
        "fixtures": [],
    }
    defaults.update(overrides)
    return FFBBTeamData(**defaults)


async def test_diagnostics_include_high_level_team_summary(hass):
    """Basic identifying info about the tracked team must be present."""
    entry = _make_entry_with_coordinator(hass)
    entry.runtime_data.data = _make_team_data(
        fixtures=[_make_match()],
        last_match=_make_match(),
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["engagement_id"] == "engagement-123"
    assert diagnostics["poule_id"] == "poule-1"
    assert diagnostics["data"]["team_name"] == "Basket Landes"
    assert diagnostics["data"]["competition_name"] == "Excellence Régionale"
    assert diagnostics["data"]["fixtures_count"] == 1


async def test_diagnostics_omit_sensitive_match_details(hass):
    """Full addresses, raw payloads and opponent org data must not leak.

    Only a compact summary (date, home/away, score, result, city) should
    make it into the diagnostics export.
    """
    entry = _make_entry_with_coordinator(hass)
    entry.runtime_data.data = _make_team_data(
        last_match=_make_match(gym_address="1 rue du Stade", gym_city="Mont-de-Marsan"),
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    last_match = diagnostics["data"]["last_match"]
    assert last_match["gym_city"] == "Mont-de-Marsan"
    assert last_match["team_score"] == 68
    assert last_match["opponent_score"] == 54
    assert last_match["result"] == "win"

    # Fields deliberately excluded from the summary:
    assert "gym_address" not in last_match
    assert "gym_name" not in last_match
    assert "raw" not in last_match
    assert "opponent_club_id" not in last_match

    # And the raw dict's contents must never surface anywhere in the export.
    assert "should-never-leak" not in str(diagnostics)


async def test_diagnostics_handle_no_match_data_gracefully(hass):
    """next_match/last_match being None must not raise."""
    entry = _make_entry_with_coordinator(hass)
    entry.runtime_data.data = _make_team_data()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["data"]["next_match"] is None
    assert diagnostics["data"]["last_match"] is None


async def test_diagnostics_handle_coordinator_without_data_yet(hass):
    """A freshly created entry whose coordinator hasn't refreshed yet
    must still produce a diagnostics dict instead of raising."""
    entry = _make_entry_with_coordinator(hass)
    assert entry.runtime_data.data is None

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["data"] is None
    assert diagnostics["engagement_id"] == "engagement-123"


async def test_diagnostics_include_team_standing_when_present(hass):
    """The tracked team's own standing row must be surfaced verbatim."""
    entry = _make_entry_with_coordinator(hass)
    standing = TeamStanding(
        position=1, points=6, played=3, won=3, lost=0, raw={"id": "rank-1"}
    )
    entry.runtime_data.data = _make_team_data(team_standing=standing)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["data"]["team_standing"]["position"] == 1
    assert diagnostics["data"]["team_standing"]["points"] == 6


# --- api section: what the FFBB API last answered ---------------------------


async def test_diagnostics_api_section_is_empty_before_the_first_fetch(hass):
    entry = _make_entry_with_coordinator(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["api"] == {
        "last_fetch_at": None,
        "response_headers": {},
        "pending_results": [],
        "stale_recheck": {
            "attempts": 0,
            "outdated_responses_detected": 0,
            "last_attempt_at": None,
            "last_outcome": None,
        },
    }


async def test_diagnostics_api_section_reports_the_safety_net_state(hass):
    entry = _make_entry_with_coordinator(hass)
    coordinator = entry.runtime_data
    coordinator.last_api_fetch_at = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    coordinator.last_recheck_at = datetime(2026, 10, 2, 10, 0, 1, tzinfo=UTC)
    coordinator.stale_recheck_attempts = 3
    coordinator.stale_responses_detected = 1
    coordinator.last_recheck_outcome = "fresher_response_used (+1 result(s))"
    coordinator.pending_results = [
        {
            "match_number": "11527",
            "round": "2",
            "date": "2026-09-26T16:00:00",
            "age_hours": 138.0,
            "joue": False,
            "resultatEquipe1": None,
            "resultatEquipe2": None,
        }
    ]
    coordinator.client = type(
        "Client",
        (),
        {
            "last_poule_headers": {
                "normal": {"age": "3600", "cache-control": "max-age=60"}
            }
        },
    )()

    api = (await async_get_config_entry_diagnostics(hass, entry))["api"]

    assert api["last_fetch_at"] == "2026-10-02T10:00:00+00:00"
    assert api["response_headers"] == {
        "normal": {"age": "3600", "cache-control": "max-age=60"}
    }
    assert api["pending_results"][0]["match_number"] == "11527"
    assert api["stale_recheck"] == {
        "attempts": 3,
        "outdated_responses_detected": 1,
        "last_attempt_at": "2026-10-02T10:00:01+00:00",
        "last_outcome": "fresher_response_used (+1 result(s))",
    }


async def test_diagnostics_api_section_holds_no_identifying_data(hass):
    """It is pasted into public issues: only timestamps, cache headers, match
    numbers and the raw result fields are allowed -- no names or addresses."""
    entry = _make_entry_with_coordinator(hass)
    coordinator = entry.runtime_data
    coordinator.pending_results = [
        coordinator._summarize_pending(
            {
                "numero": "11527",
                "numeroJournee": "2",
                "date_rencontre": "2026-09-26T16:00:00",
                "joue": False,
                "nomEquipe1": "SECRET HOME TEAM",
                "nomEquipe2": "SECRET AWAY TEAM",
                "salle": {"libelle": "SECRET GYM", "adresse": "1 secret street"},
            },
            age=timedelta(hours=5),
        )
    ]

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    dumped = json.dumps(diagnostics)
    assert "SECRET" not in dumped
    assert set(diagnostics["api"]["pending_results"][0]) == {
        "match_number",
        "round",
        "date",
        "age_hours",
        "joue",
        "resultatEquipe1",
        "resultatEquipe2",
    }


async def test_diagnostics_stay_json_serializable(hass):
    entry = _make_entry_with_coordinator(hass)
    entry.runtime_data.last_api_fetch_at = datetime(2026, 10, 2, tzinfo=UTC)

    json.dumps(await async_get_config_entry_diagnostics(hass, entry))
