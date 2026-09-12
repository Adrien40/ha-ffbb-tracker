"""Tests for diagnostics.py.

The main risk here isn't a crash, it's a silent privacy regression: a
future edit to `_match_summary` or `async_get_config_entry_diagnostics`
could start including the full raw match payload (street address, full
opponent org dict) in a diagnostics export a user pastes into a public
GitHub issue. These tests pin down exactly which fields are present so
that kind of change fails loudly in CI instead of shipping quietly.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
