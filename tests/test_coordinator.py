"""Tests for the FFBB Tracker DataUpdateCoordinator.

These tests target the pure parsing logic (_process_poule_data,
_parse_match), the live-polling speedup logic, and the poule cache shared
across coordinators tracking the same pool. They exist primarily to catch
silent breakage: if the Directus API response schema changes (field
renamed, nesting changed), matches would simply stop matching the tracked
engagement_id and every sensor would silently show `unknown` with no error
raised anywhere. This suite pins down the exact field names the parser
depends on so such a regression fails loudly in CI instead of in a user's
log.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LIVE_SCAN_INTERVAL,
)
from custom_components.ffbb_tracker.coordinator import (
    _POULE_CACHE_TTL,
    FFBBDataUpdateCoordinator,
    _get_poule_cache,
)


def _make_coordinator(hass) -> FFBBDataUpdateCoordinator:
    """Build a coordinator wired to engagement-123 / Basket Landes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENGAGEMENT_ID: "engagement-123",
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry.add_to_hass(hass)
    return FFBBDataUpdateCoordinator(hass, client=None, entry=entry)


def test_filters_out_matches_from_other_engagements(hass, sample_poule_data):
    """Only rencontres involving the tracked engagement_id are kept."""
    coordinator = _make_coordinator(hass)

    result = coordinator._process_poule_data(sample_poule_data)

    match_ids = {m.match_id for m in result.fixtures}
    assert match_ids == {"match-1", "match-2"}
    assert "match-3" not in match_ids


def test_identifies_next_and_last_match(hass, sample_poule_data):
    """The played match becomes last_match, the unplayed one next_match."""
    coordinator = _make_coordinator(hass)

    result = coordinator._process_poule_data(sample_poule_data)

    assert result.last_match is not None
    assert result.last_match.match_id == "match-1"
    assert result.last_match.is_played is True

    assert result.next_match is not None
    assert result.next_match.match_id == "match-2"
    assert result.next_match.is_played is False


def test_home_away_and_score_orientation(hass, sample_poule_data):
    """team_score/opponent_score must be oriented from the tracked team's side.

    engagement-123 is équipe1 in match-1 (home) and équipe2 in match-2
    (away) — the parser must flip score1/score2 accordingly.
    """
    coordinator = _make_coordinator(hass)
    result = coordinator._process_poule_data(sample_poule_data)

    last = result.last_match
    assert last.is_home is True
    assert last.team_score == 68
    assert last.opponent_score == 54
    assert last.result == "win"
    assert last.opponent_name == "US Mont-de-Marsan"

    nxt = result.next_match
    assert nxt.is_home is False
    assert nxt.opponent_name == "Stade Montois"


def test_match_date_parsed_and_converted_to_utc(hass, sample_poule_data):
    """date_rencontre (ISO 8601, local offset) is parsed and stored as UTC."""
    coordinator = _make_coordinator(hass)
    result = coordinator._process_poule_data(sample_poule_data)

    match_date = result.last_match.match_date
    assert isinstance(match_date, datetime)
    assert match_date.tzinfo is not None
    # 2026-01-10T20:00:00+01:00 -> 19:00 UTC
    assert match_date.astimezone(UTC).hour == 19


def test_gym_fields_extracted_from_nested_salle(hass, sample_poule_data):
    """salle.libelle/adresse/codePostal/commune.libelle map to gym_* fields."""
    coordinator = _make_coordinator(hass)
    result = coordinator._process_poule_data(sample_poule_data)

    last = result.last_match
    assert last.gym_name == "Gymnase Andre Chavanne"
    assert last.gym_address == "1 rue du Stade"
    assert last.gym_postal_code == "40000"
    assert last.gym_city == "Mont-de-Marsan"

    assert last.formatted_address == (
        "Gymnase Andre Chavanne, 1 rue du Stade, 40000, Mont-de-Marsan"
    )


def test_gym_missing_returns_none_formatted_address(hass):
    """A match with no salle data must not crash and formatted_address is None."""
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-x",
            "numero": "1",
            "numeroJournee": "1",
            "resultatEquipe1": None,
            "resultatEquipe2": None,
            "joue": False,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire Inconnu",
            "date_rencontre": None,
            "idOrganismeEquipe2": None,
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.gym_name is None
    assert parsed.formatted_address is None
    assert parsed.match_date is None


def test_standings_matches_tracked_engagement(hass, sample_poule_data):
    """team_standing is populated only for the row matching engagement_id."""
    coordinator = _make_coordinator(hass)
    result = coordinator._process_poule_data(sample_poule_data)

    assert result.team_standing is not None
    assert result.team_standing.position == 1
    assert result.team_standing.points == 6
    assert result.team_standing.won == 3
    assert result.team_standing.lost == 0

    assert len(result.standings) == 2


def test_draw_result_when_scores_equal(hass):
    """A tied, played match is classified as 'draw', not left as None."""
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-draw",
            "numero": "5",
            "numeroJournee": "5",
            "resultatEquipe1": 70,
            "resultatEquipe2": 70,
            "joue": True,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire",
            "date_rencontre": "2026-02-01T20:00:00+01:00",
            "idOrganismeEquipe2": {"id": "org-x", "nom": "Adversaire"},
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.result == "draw"
    assert parsed.is_played is True


def test_is_played_safety_net_when_joue_false_but_score_present(hass):
    """`joue: False` with a real, non-zero score already posted (e.g. a
    forfeit/walkover the FFBB forgot to flag as played) must still be
    treated as played, so the match doesn't get stuck forever as
    `next_match` instead of moving to `last_match`.
    """
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-forfeit",
            "numero": "3",
            "numeroJournee": "3",
            "resultatEquipe1": 20,
            "resultatEquipe2": 0,
            "joue": False,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire",
            "date_rencontre": "2026-01-15T20:00:00+01:00",
            "idOrganismeEquipe2": {"id": "org-x", "nom": "Adversaire"},
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.is_played is True
    assert parsed.result == "win"


def test_is_played_stays_false_when_joue_false_and_no_score(hass):
    """The safety net must only fire when a real score is present -- it must
    NOT blanket-override `joue: False` for matches that simply haven't been
    played yet (no score at all).
    """
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-upcoming",
            "numero": "4",
            "numeroJournee": "4",
            "resultatEquipe1": None,
            "resultatEquipe2": None,
            "joue": False,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire",
            "date_rencontre": "2026-03-01T20:00:00+01:00",
            "idOrganismeEquipe2": {"id": "org-x", "nom": "Adversaire"},
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.is_played is False
    assert parsed.result is None


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, None),
        ("", None),
        ("42", 42),
        (42, 42),
        ("not-a-number", None),
    ],
)
def test_safe_int_handles_malformed_scores(raw_value, expected):
    """_safe_int must never raise on unexpected API payload values."""
    from custom_components.ffbb_tracker.coordinator import _safe_int

    assert _safe_int(raw_value) == expected


def _minimal_poule(rencontre: dict) -> dict:
    """Wrap a single rencontre dict into a full poule payload."""
    return {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": [rencontre],
        "classements": [],
    }


def test_next_match_fallback_when_result_not_yet_published(hass):
    """A match whose scheduled time is long past but is still marked
    `joue: False` (FFBB hasn't published the result yet) must surface as
    next_match instead of vanishing from both next_match and last_match.
    """
    coordinator = _make_coordinator(hass)
    stale_match = {
        "id": "match-old",
        "numero": "1",
        "numeroJournee": "1",
        "resultatEquipe1": None,
        "resultatEquipe2": None,
        "joue": False,
        "nomEquipe1": "Basket Landes",
        "nomEquipe2": "Adversaire",
        "date_rencontre": "2020-01-01T20:00:00+01:00",
        "idEngagementEquipe1": {"id": "engagement-123"},
        "idEngagementEquipe2": {"id": "engagement-456"},
        "idOrganismeEquipe2": {"id": "org-2", "nom": "Adversaire"},
        "salle": None,
    }

    result = coordinator._process_poule_data(_minimal_poule(stale_match))

    assert result.last_match is None
    assert result.next_match is not None
    assert result.next_match.match_id == "match-old"


def test_compute_update_interval_speeds_up_near_match_time(hass):
    """A match starting within the live window triggers fast polling."""
    coordinator = _make_coordinator(hass)
    soon = datetime.now(UTC) + timedelta(minutes=30)
    upcoming_match = {
        "id": "match-soon",
        "numero": "1",
        "numeroJournee": "1",
        "resultatEquipe1": None,
        "resultatEquipe2": None,
        "joue": False,
        "nomEquipe1": "Basket Landes",
        "nomEquipe2": "Adversaire",
        "date_rencontre": soon.isoformat(),
        "idEngagementEquipe1": {"id": "engagement-123"},
        "idEngagementEquipe2": {"id": "engagement-456"},
        "idOrganismeEquipe2": {"id": "org-2", "nom": "Adversaire"},
        "salle": None,
    }

    data = coordinator._process_poule_data(_minimal_poule(upcoming_match))

    assert coordinator._compute_update_interval(data) == timedelta(
        minutes=LIVE_SCAN_INTERVAL
    )


def test_compute_update_interval_awaiting_result_still_fast(hass):
    """A match that just ended and awaits a published result also polls fast."""
    coordinator = _make_coordinator(hass)
    just_ended = datetime.now(UTC) - timedelta(hours=1)
    recent_match = {
        "id": "match-recent",
        "numero": "1",
        "numeroJournee": "1",
        "resultatEquipe1": None,
        "resultatEquipe2": None,
        "joue": False,
        "nomEquipe1": "Basket Landes",
        "nomEquipe2": "Adversaire",
        "date_rencontre": just_ended.isoformat(),
        "idEngagementEquipe1": {"id": "engagement-123"},
        "idEngagementEquipe2": {"id": "engagement-456"},
        "idOrganismeEquipe2": {"id": "org-2", "nom": "Adversaire"},
        "salle": None,
    }

    data = coordinator._process_poule_data(_minimal_poule(recent_match))

    assert coordinator._compute_update_interval(data) == timedelta(
        minutes=LIVE_SCAN_INTERVAL
    )


def test_compute_update_interval_defaults_far_from_any_match(hass):
    """With no match nearby, the configured base interval is used untouched."""
    coordinator = _make_coordinator(hass)
    data = coordinator._process_poule_data(
        {"id": "poule-1", "nom": "Poule A", "rencontres": [], "classements": []}
    )

    assert coordinator._compute_update_interval(data) == timedelta(
        minutes=DEFAULT_SCAN_INTERVAL
    )


# ---------------------------------------------------------------------------
# Shared poule cache
# ---------------------------------------------------------------------------


async def test_poule_cache_shared_across_coordinators_same_poule(hass):
    """Two coordinators tracking the same poule must reuse one fetch.

    Without the shared cache, each team's coordinator would independently
    request the exact same ~1000-fixture payload from the FFBB API.
    """
    coordinator_a = _make_coordinator(hass)

    entry_b = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENGAGEMENT_ID: "engagement-456",
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: "US Mont-de-Marsan",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-2",
        },
    )
    entry_b.add_to_hass(hass)
    coordinator_b = FFBBDataUpdateCoordinator(hass, client=None, entry=entry_b)

    payload = {"id": "poule-1", "nom": "Poule A", "rencontres": [], "classements": []}
    coordinator_a.client = AsyncMock()
    coordinator_a.client.get_poule_data = AsyncMock(return_value=payload)
    coordinator_b.client = AsyncMock()
    coordinator_b.client.get_poule_data = AsyncMock(return_value=payload)

    data_a = await coordinator_a._async_fetch_poule_data()
    data_b = await coordinator_b._async_fetch_poule_data()

    assert data_a is data_b
    assert coordinator_a.client.get_poule_data.call_count == 1
    assert coordinator_b.client.get_poule_data.call_count == 0


async def test_poule_cache_independent_per_poule(hass):
    """Coordinators tracking *different* poules must never share a cache entry."""
    coordinator_a = _make_coordinator(hass)

    entry_b = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENGAGEMENT_ID: "engagement-789",
            CONF_POULE_ID: "poule-2",
            CONF_TEAM_NAME: "Autre Équipe",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-3",
        },
    )
    entry_b.add_to_hass(hass)
    coordinator_b = FFBBDataUpdateCoordinator(hass, client=None, entry=entry_b)

    coordinator_a.client = AsyncMock()
    coordinator_a.client.get_poule_data = AsyncMock(
        return_value={"id": "poule-1", "nom": "A", "rencontres": [], "classements": []}
    )
    coordinator_b.client = AsyncMock()
    coordinator_b.client.get_poule_data = AsyncMock(
        return_value={"id": "poule-2", "nom": "B", "rencontres": [], "classements": []}
    )

    data_a = await coordinator_a._async_fetch_poule_data()
    data_b = await coordinator_b._async_fetch_poule_data()

    assert data_a["nom"] == "A"
    assert data_b["nom"] == "B"
    assert coordinator_a.client.get_poule_data.call_count == 1
    assert coordinator_b.client.get_poule_data.call_count == 1


async def test_poule_cache_refetches_after_ttl_expires(hass):
    """A cache entry older than _POULE_CACHE_TTL must trigger a fresh fetch."""
    coordinator = _make_coordinator(hass)
    coordinator.client = AsyncMock()
    coordinator.client.get_poule_data = AsyncMock(
        side_effect=[
            {"id": "poule-1", "nom": "First", "rencontres": [], "classements": []},
            {"id": "poule-1", "nom": "Second", "rencontres": [], "classements": []},
        ]
    )

    first = await coordinator._async_fetch_poule_data()
    assert first["nom"] == "First"

    cache = _get_poule_cache(hass, coordinator.poule_id)
    cache.fetched_at = datetime.now(UTC) - _POULE_CACHE_TTL - timedelta(seconds=1)

    second = await coordinator._async_fetch_poule_data()
    assert second["nom"] == "Second"
    assert coordinator.client.get_poule_data.call_count == 2


async def test_poule_cache_lock_prevents_concurrent_duplicate_fetch(hass):
    """Two concurrent refreshes for the same poule must still hit the API once.

    This is the scenario the asyncio.Lock exists for: two coordinators (or
    two refresh cycles) racing to populate a cold cache at the same time.
    """
    coordinator = _make_coordinator(hass)
    call_count = 0

    async def _slow_fetch(poule_id):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return {"id": poule_id, "nom": "Slow", "rencontres": [], "classements": []}

    coordinator.client = AsyncMock()
    coordinator.client.get_poule_data = _slow_fetch

    results = await asyncio.gather(
        coordinator._async_fetch_poule_data(),
        coordinator._async_fetch_poule_data(),
    )

    assert call_count == 1
    assert results[0] is results[1]
