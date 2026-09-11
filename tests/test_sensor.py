"""Tests for sensor.py.

coordinator.py and api.py already have solid pure-logic coverage. This
suite targets the entity layer, which had none — in particular
FFBBRankingEvolutionSensor, the only *stateful* sensor in the platform
(it persists across restarts via RestoreSensor and computes a signed
diff between two positions). A silent regression there is the kind of
bug a user only discovers when the "Ranking: Evolution" sensor starts
showing a *inverted* arrow, or gets stuck on "Unavailable" — not
something that shows up as an exception in the log.

Entities are instantiated directly against a coordinator whose `.data`
is set manually, without going through async_setup_entry or a full
entity_platform/hass wiring, matching the "test the pure logic"
approach used in test_coordinator.py. Where a test needs to call
`_handle_coordinator_update` (which ends in `self.async_write_ha_state()`
and would otherwise require the entity to be actually added to hass),
`async_write_ha_state` is monkeypatched to a no-op — we're testing the
position/diff bookkeeping, not the entity registration machinery.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
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
from custom_components.ffbb_tracker.sensor import (
    FFBBLastMatchScoreSensor,
    FFBBNextMatchLocationSensor,
    FFBBNextMatchOpponentSensor,
    FFBBRankingEvolutionExtraData,
    FFBBRankingEvolutionSensor,
    FFBBRankingSensor,
    _safe_int_value,
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
# _safe_int_value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_value,expected",
    [
        (None, None),
        ("", None),
        ("3", 3),
        (3, 3),
        ("not-a-number", None),
    ],
)
def test_safe_int_value_handles_malformed_input(raw_value, expected):
    """_safe_int_value must never raise, including on restored garbage state."""
    assert _safe_int_value(raw_value) == expected


# ---------------------------------------------------------------------------
# Next match location: 255-char truncation guard
# ---------------------------------------------------------------------------


def test_next_match_location_truncates_to_255_chars(hass):
    """A formatted_address longer than 255 chars must be truncated for the state."""
    coordinator = _make_coordinator(hass)
    long_gym_name = "Gymnase " + ("Très Long Nom " * 20)  # comfortably over 255 chars
    match = _make_match(gym_name=long_gym_name)
    coordinator.data = _make_team_data(next_match=match)

    sensor = FFBBNextMatchLocationSensor(coordinator)

    assert len(match.formatted_address) > 255
    assert sensor.native_value == match.formatted_address[:255]
    assert len(sensor.native_value) == 255


def test_next_match_location_none_when_no_address(hass):
    """No next_match / no gym data must yield a None state, not an empty string."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(
            gym_name=None, gym_address=None, gym_postal_code=None, gym_city=None
        )
    )

    sensor = FFBBNextMatchLocationSensor(coordinator)

    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Next match opponent: GPS navigation links
# ---------------------------------------------------------------------------


def test_next_match_opponent_generates_navigation_urls(hass):
    """Google Maps / Waze / geo URLs are built from the URL-encoded address."""
    coordinator = _make_coordinator(hass)
    match = _make_match(
        gym_name="Gymnase André Chavanne",
        gym_address="1 rue du Stade",
        gym_postal_code="40000",
        gym_city="Mont-de-Marsan",
    )
    coordinator.data = _make_team_data(next_match=match)

    sensor = FFBBNextMatchOpponentSensor(coordinator)
    attrs = sensor.extra_state_attributes

    encoded = "Gymnase+Andr%C3%A9+Chavanne%2C+1+rue+du+Stade%2C+40000%2C+Mont-de-Marsan"
    assert attrs["navigation_url"] == f"geo:0,0?q={encoded}"
    assert (
        attrs["google_maps_url"]
        == f"https://www.google.com/maps/dir/?api=1&destination={encoded}"
    )
    assert attrs["waze_url"] == f"https://waze.com/ul?q={encoded}&navigate=yes"


def test_next_match_opponent_urls_are_none_without_address(hass):
    """With no gym data at all, navigation URLs must stay None, not raise."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(
            gym_name=None, gym_address=None, gym_postal_code=None, gym_city=None
        )
    )

    sensor = FFBBNextMatchOpponentSensor(coordinator)
    attrs = sensor.extra_state_attributes

    assert attrs["navigation_url"] is None
    assert attrs["google_maps_url"] is None
    assert attrs["waze_url"] is None


# ---------------------------------------------------------------------------
# Last match score
# ---------------------------------------------------------------------------


def test_last_match_score_formats_and_exposes_scores(hass):
    """The state is the formatted score; team/opponent scores also land in attrs."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        last_match=_make_match(is_played=True, team_score=68, opponent_score=54)
    )

    sensor = FFBBLastMatchScoreSensor(coordinator)

    assert sensor.native_value == "68 - 54"
    assert sensor.extra_state_attributes["team_score"] == 68
    assert sensor.extra_state_attributes["opponent_score"] == 54


def test_last_match_score_none_when_score_missing(hass):
    """A match without a final score must not render a partial/garbage string."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        last_match=_make_match(is_played=False, team_score=None, opponent_score=None)
    )

    sensor = FFBBLastMatchScoreSensor(coordinator)

    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# FFBBRankingEvolutionExtraData: the RestoreSensor payload contract
# ---------------------------------------------------------------------------


def test_ranking_evolution_extra_data_round_trips():
    """as_dict() -> from_dict() must reproduce the original values exactly."""
    original = FFBBRankingEvolutionExtraData(current_position=3, previous_position=5)

    restored = FFBBRankingEvolutionExtraData.from_dict(original.as_dict())

    assert restored is not None
    assert restored.current_position == 3
    assert restored.previous_position == 5


def test_ranking_evolution_extra_data_from_dict_sanitizes_string_positions():
    """Positions stored/restored as strings (legacy state) must still parse as int."""
    restored = FFBBRankingEvolutionExtraData.from_dict(
        {"current_position": "3", "previous_position": "5"}
    )

    assert restored is not None
    assert restored.current_position == 3
    assert restored.previous_position == 5


@pytest.mark.parametrize("malformed", [None, "not-a-dict", 42, []])
def test_ranking_evolution_extra_data_from_dict_rejects_non_dict(malformed):
    """A non-dict restored payload must return None, not raise."""
    assert FFBBRankingEvolutionExtraData.from_dict(malformed) is None


def test_ranking_evolution_extra_data_from_dict_handles_missing_keys():
    """Missing keys in the restored payload must fall back to None, not KeyError."""
    restored = FFBBRankingEvolutionExtraData.from_dict({})

    assert restored is not None
    assert restored.current_position is None
    assert restored.previous_position is None


# ---------------------------------------------------------------------------
# FFBBRankingEvolutionSensor: diff sign convention and dynamic icon
# ---------------------------------------------------------------------------


def _trigger_update(sensor: FFBBRankingEvolutionSensor, monkeypatch) -> None:
    """Call _handle_coordinator_update without requiring a hass-registered entity."""
    monkeypatch.setattr(sensor, "async_write_ha_state", lambda: None)
    sensor._handle_coordinator_update()


def test_ranking_evolution_first_update_establishes_zero_baseline(hass, monkeypatch):
    """On the very first update (cold start), current == previous, so diff is 0."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=5, points=10, played=5, won=5, lost=0, raw={}
        )
    )
    sensor = FFBBRankingEvolutionSensor(coordinator)

    _trigger_update(sensor, monkeypatch)

    assert sensor.native_value == "0"
    assert sensor.icon == "mdi:minus"
    assert sensor.extra_state_attributes["current_position"] == 5
    assert sensor.extra_state_attributes["previous_position"] == 5


def test_ranking_evolution_shows_positive_diff_when_rank_improves(hass, monkeypatch):
    """Moving from position 5 to position 3 (a better rank) must show '+2' and an up arrow.

    This pins down the sign convention: diff = previous_position - current_position,
    so a *numerically lower* (better) position produces a *positive* diff. Flipping
    this by accident would make the sensor say "+2" when a team actually dropped
    two spots, which is the kind of bug nobody notices until a user complains their
    team is "climbing" after a loss.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=5, points=10, played=5, won=5, lost=0, raw={}
        )
    )
    sensor = FFBBRankingEvolutionSensor(coordinator)
    _trigger_update(sensor, monkeypatch)  # baseline: current=5, previous=5

    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=3, points=13, played=6, won=6, lost=0, raw={}
        )
    )
    _trigger_update(sensor, monkeypatch)

    assert sensor.native_value == "+2"
    assert sensor.icon == "mdi:arrow-up-bold"


def test_ranking_evolution_shows_negative_diff_when_rank_drops(hass, monkeypatch):
    """Moving from position 3 to position 6 (a worse rank) must show '-3' and a down arrow."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=3, points=13, played=6, won=6, lost=0, raw={}
        )
    )
    sensor = FFBBRankingEvolutionSensor(coordinator)
    _trigger_update(sensor, monkeypatch)  # baseline: current=3, previous=3

    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=6, points=13, played=7, won=6, lost=1, raw={}
        )
    )
    _trigger_update(sensor, monkeypatch)

    assert sensor.native_value == "-3"
    assert sensor.icon == "mdi:arrow-down-bold"


def test_ranking_evolution_unchanged_position_keeps_zero(hass, monkeypatch):
    """Two consecutive updates at the same position must not drift the diff."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=4, points=8, played=4, won=4, lost=0, raw={}
        )
    )
    sensor = FFBBRankingEvolutionSensor(coordinator)
    _trigger_update(sensor, monkeypatch)
    _trigger_update(sensor, monkeypatch)  # same position again

    assert sensor.native_value == "0"
    assert sensor.icon == "mdi:minus"


def test_ranking_evolution_dash_when_standings_not_yet_published(hass, monkeypatch):
    """Before any standings exist, the sensor must show '-' rather than 'Unavailable'.

    This is a deliberate UX choice documented in sensor.py: returning None here
    would render the entity greyed-out/"Unavailable", which users mistake for a
    broken integration during pre-season or for youth categories without a
    published ranking yet.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(team_standing=None)
    sensor = FFBBRankingEvolutionSensor(coordinator)

    _trigger_update(sensor, monkeypatch)

    assert sensor.native_value == "-"
    assert sensor.icon == "mdi:minus"
    assert "status" in sensor.extra_state_attributes


def test_ranking_evolution_resumes_diffing_after_restore(hass, monkeypatch):
    """Restored positions (simulating async_added_to_hass) must seed the next diff.

    Simulates what async_added_to_hass does after a restart: the sensor starts
    with _current_position/_previous_position already populated from storage
    instead of None. The very next coordinator update must diff against that
    restored baseline, not silently reset it.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=4, points=8, played=4, won=4, lost=0, raw={}
        )
    )
    sensor = FFBBRankingEvolutionSensor(coordinator)

    # Simulate a restored state from a previous session, as async_added_to_hass
    # would set it from FFBBRankingEvolutionExtraData.
    sensor._current_position = 4
    sensor._previous_position = 5

    _trigger_update(sensor, monkeypatch)  # position unchanged (4): no shift expected

    assert sensor.native_value == "+1"  # 5 - 4, preserved from the restored data
    assert sensor.icon == "mdi:arrow-up-bold"


# ---------------------------------------------------------------------------
# FFBBRankingSensor: sanity check that it stays independent of the evolution sensor
# ---------------------------------------------------------------------------


def test_ranking_sensor_reports_current_position_and_standings(hass):
    """The plain ranking sensor exposes position as state and the full table as attrs."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=2, points=10, played=5, won=5, lost=0, raw={}
        ),
        standings=[{"position": 1, "team_name": "Other"}],
    )

    sensor = FFBBRankingSensor(coordinator)

    assert sensor.native_value == 2
    assert sensor.extra_state_attributes["standings"] == [
        {"position": 1, "team_name": "Other"}
    ]


def test_ranking_sensor_none_when_no_standing(hass):
    """No team_standing (not yet ranked) must yield a None state, not an error."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(team_standing=None)

    sensor = FFBBRankingSensor(coordinator)

    assert sensor.native_value is None
