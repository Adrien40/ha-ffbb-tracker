"""Tests for the three services registered in __init__.py.

These are the integration's public surface for automations
(`ffbb_tracker.refresh`, `.get_next_matches`, `.get_standings`), so a
regression here is user-visible in scripts/automations rather than in
a log line. Coverage focuses on:
  - each service reaching the right coordinator(s) via `entry_id`
    filtering (or all of them when omitted),
  - `get_next_matches` only returning *unplayed* fixtures, respecting
    `limit`, and skipping entries with no data yet,
  - `get_standings` shaping its response around `coordinator.data`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker import async_setup_services
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
)


def _make_entry_with_coordinator(
    hass, engagement_id: str, team_name: str = "Basket Landes"
) -> MockConfigEntry:
    """Add a config entry to hass with a coordinator wired as runtime_data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=engagement_id,
        data={
            CONF_ENGAGEMENT_ID: engagement_id,
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: team_name,
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
        "is_played": False,
        "team_score": None,
        "opponent_score": None,
        "result": None,
        "gym_name": "Gymnase Andre Chavanne",
        "gym_address": "1 rue du Stade",
        "gym_postal_code": "40000",
        "gym_city": "Mont-de-Marsan",
        "raw": {},
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


# ---------------------------------------------------------------------------
# ffbb_tracker.refresh
# ---------------------------------------------------------------------------


async def test_refresh_calls_all_coordinators_when_no_entry_id_given(hass):
    """With no `entry_id`, every tracked team must be refreshed."""
    entry_a = _make_entry_with_coordinator(hass, "engagement-a")
    entry_b = _make_entry_with_coordinator(hass, "engagement-b")
    entry_a.runtime_data.async_request_refresh = AsyncMock()
    entry_b.runtime_data.async_request_refresh = AsyncMock()

    async_setup_services(hass)
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)

    entry_a.runtime_data.async_request_refresh.assert_awaited_once()
    entry_b.runtime_data.async_request_refresh.assert_awaited_once()


async def test_refresh_targets_only_the_given_entry_id(hass):
    """Passing `entry_id` must refresh that team only, not siblings."""
    entry_a = _make_entry_with_coordinator(hass, "engagement-a")
    entry_b = _make_entry_with_coordinator(hass, "engagement-b")
    entry_a.runtime_data.async_request_refresh = AsyncMock()
    entry_b.runtime_data.async_request_refresh = AsyncMock()

    async_setup_services(hass)
    await hass.services.async_call(
        DOMAIN, "refresh", {"entry_id": entry_a.entry_id}, blocking=True
    )

    entry_a.runtime_data.async_request_refresh.assert_awaited_once()
    entry_b.runtime_data.async_request_refresh.assert_not_called()


async def test_refresh_skips_entries_without_a_coordinator(hass):
    """An entry that hasn't finished setup yet must not raise."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="engagement-a",
        data={
            CONF_ENGAGEMENT_ID: "engagement-a",
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry.add_to_hass(hass)
    # No runtime_data assigned, mirroring an entry mid-setup.

    async_setup_services(hass)
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    # Must not raise.


# ---------------------------------------------------------------------------
# ffbb_tracker.get_next_matches
# ---------------------------------------------------------------------------


async def test_get_next_matches_returns_only_unplayed_fixtures(hass):
    """Played matches must never leak into the "next matches" response."""
    entry = _make_entry_with_coordinator(hass, "engagement-123")
    entry.runtime_data.data = _make_team_data(
        fixtures=[
            _make_match(match_id="played", is_played=True),
            _make_match(match_id="upcoming-1", is_played=False),
            _make_match(match_id="upcoming-2", is_played=False),
        ]
    )

    async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN, "get_next_matches", {}, blocking=True, return_response=True
    )

    matches = response["teams"][0]["matches"]
    assert [m["opponent"] for m in matches] == [
        "US Mont-de-Marsan",
        "US Mont-de-Marsan",
    ]
    assert len(matches) == 2


async def test_get_next_matches_respects_limit(hass):
    """`limit` must cap the number of returned upcoming fixtures."""
    entry = _make_entry_with_coordinator(hass, "engagement-123")
    entry.runtime_data.data = _make_team_data(
        fixtures=[
            _make_match(match_id=f"upcoming-{i}", is_played=False) for i in range(5)
        ]
    )

    async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN,
        "get_next_matches",
        {"limit": 2},
        blocking=True,
        return_response=True,
    )

    assert len(response["teams"][0]["matches"]) == 2


async def test_get_next_matches_skips_entries_with_no_data_yet(hass):
    """An entry whose coordinator hasn't refreshed yet must be omitted."""
    entry = _make_entry_with_coordinator(hass, "engagement-123")
    assert entry.runtime_data.data is None

    async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN, "get_next_matches", {}, blocking=True, return_response=True
    )

    assert response["teams"] == []


async def test_get_next_matches_filters_by_entry_id(hass):
    """Only the targeted team must appear in the response."""
    entry_a = _make_entry_with_coordinator(hass, "engagement-a", "Basket Landes")
    entry_b = _make_entry_with_coordinator(hass, "engagement-b", "Stade Montois")
    entry_a.runtime_data.data = _make_team_data(team_name="Basket Landes")
    entry_b.runtime_data.data = _make_team_data(team_name="Stade Montois")

    async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN,
        "get_next_matches",
        {"entry_id": entry_a.entry_id},
        blocking=True,
        return_response=True,
    )

    assert len(response["teams"]) == 1
    assert response["teams"][0]["team_name"] == "Basket Landes"


# ---------------------------------------------------------------------------
# ffbb_tracker.get_standings
# ---------------------------------------------------------------------------


async def test_get_standings_returns_the_full_table(hass):
    """The service must expose the parsed standings list as-is."""
    entry = _make_entry_with_coordinator(hass, "engagement-123")
    standings = [
        {
            "position": 1,
            "team_name": "Basket Landes",
            "points": 6,
            "played": 3,
            "won": 3,
            "lost": 0,
        },
        {
            "position": 2,
            "team_name": "US Mont-de-Marsan",
            "points": 4,
            "played": 3,
            "won": 2,
            "lost": 1,
        },
    ]
    entry.runtime_data.data = _make_team_data(standings=standings)

    async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN, "get_standings", {}, blocking=True, return_response=True
    )

    result = response["standings"][0]
    assert result["team_name"] == "Basket Landes"
    assert result["poule"] == "Excellence Régionale - Poule A"
    assert result["standings"] == standings


async def test_get_standings_skips_entries_with_no_data_yet(hass):
    """An entry whose coordinator hasn't refreshed yet must be omitted."""
    _make_entry_with_coordinator(hass, "engagement-123")

    async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN, "get_standings", {}, blocking=True, return_response=True
    )

    assert response["standings"] == []


async def test_get_standings_filters_by_entry_id(hass):
    """Only the targeted team's standings must be returned."""
    entry_a = _make_entry_with_coordinator(hass, "engagement-a", "Basket Landes")
    entry_b = _make_entry_with_coordinator(hass, "engagement-b", "Stade Montois")
    entry_a.runtime_data.data = _make_team_data(team_name="Basket Landes")
    entry_b.runtime_data.data = _make_team_data(team_name="Stade Montois")

    async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN,
        "get_standings",
        {"entry_id": entry_b.entry_id},
        blocking=True,
        return_response=True,
    )

    assert len(response["standings"]) == 1
    assert response["standings"][0]["team_name"] == "Stade Montois"


# ---------------------------------------------------------------------------
# Service registration is idempotent
# ---------------------------------------------------------------------------


async def test_async_setup_services_is_idempotent(hass):
    """Calling setup twice (e.g. two config entries) must not re-register
    or raise, since HA forbids registering the same service twice."""
    async_setup_services(hass)
    async_setup_services(hass)

    assert hass.services.has_service(DOMAIN, "refresh")
    assert hass.services.has_service(DOMAIN, "get_next_matches")
    assert hass.services.has_service(DOMAIN, "get_standings")
