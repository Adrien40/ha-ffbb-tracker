"""Tests for sensor.py.

coordinator.py and api.py already have solid pure-logic coverage. This
suite targets the entity layer, which had none — in particular
FFBBRankEvolutionSensor, the only *stateful* sensor in the platform
(it persists across restarts via RestoreSensor and computes a signed
diff between two positions). A silent regression there is the kind of
bug a user only discovers when the "Rank: Evolution" sensor starts
showing an inverted arrow, or gets stuck on "Unavailable" — not
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

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.const import (
    ATTR_GYM_CITY,
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
    FFBBFormSensor,
    FFBBLastMatchDateSensor,
    FFBBLastMatchOpponentSensor,
    FFBBLastMatchResultSensor,
    FFBBLastMatchScoreSensor,
    FFBBNextMatchDateSensor,
    FFBBNextMatchLocationSensor,
    FFBBNextMatchOpponentSensor,
    FFBBPouleSensor,
    FFBBRankEvolutionExtraData,
    FFBBRankEvolutionSensor,
    FFBBRankSensor,
    _get_form_letters,
    _safe_int_value,
    async_setup_entry,
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
        "team_logo_url": None,
        "opponent_logo_url": None,
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
    ("raw_value", "expected"),
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
# async_setup_entry: registers every sensor
# ---------------------------------------------------------------------------


async def test_async_setup_entry_registers_all_twelve_sensors(hass):
    """A regression here (a sensor dropped from the list) would silently
    remove an entity from every user's dashboard without any error.
    """
    coordinator = _make_coordinator(hass)
    entry = coordinator.config_entry
    entry.runtime_data = coordinator
    added: list = []

    await async_setup_entry(hass, entry, added.extend)

    assert len(added) == 12


# ---------------------------------------------------------------------------
# Date/opponent/result sensors with no dedicated coverage yet: happy path
# and the "no data yet" branch (coordinator.data or .next_match/.last_match
# is None), which is what most users see between setup and the first
# successful refresh.
# ---------------------------------------------------------------------------


def test_next_match_date_sensor_reports_scheduled_datetime(hass):
    """native_value returns the next match's date, and its attrs expose the venue."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(
            match_date=datetime(2026, 3, 1, 20, 30, tzinfo=UTC), round_number="12"
        )
    )
    sensor = FFBBNextMatchDateSensor(coordinator)

    assert sensor.native_value == datetime(2026, 3, 1, 20, 30, tzinfo=UTC)
    assert sensor.extra_state_attributes["round"] == "12"


def test_next_match_date_sensor_none_when_no_upcoming_match(hass):
    """Between seasons, or with no fixture yet, this must be None, not crash."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(next_match=None)
    sensor = FFBBNextMatchDateSensor(coordinator)

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_match_date_sensor_none_before_first_refresh(hass):
    """Before the coordinator's first successful refresh, .data is None."""
    coordinator = _make_coordinator(hass)
    sensor = FFBBNextMatchDateSensor(coordinator)

    assert sensor.native_value is None


def test_next_match_opponent_sensor_none_when_no_upcoming_match(hass):
    """The opponent-name sensor mirrors the date sensor's 'no data' behavior."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(next_match=None)
    sensor = FFBBNextMatchOpponentSensor(coordinator)

    assert sensor.native_value is None


def test_next_match_opponent_sensor_entity_picture_reflects_logo(hass):
    """entity_picture surfaces the opponent's logo so it renders natively
    (e.g. in the logbook/history), and is None when there's no next match
    or no logo on file."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(opponent_logo_url="https://api.ffbb.app/assets/abc")
    )
    sensor = FFBBNextMatchOpponentSensor(coordinator)
    assert sensor.entity_picture == "https://api.ffbb.app/assets/abc"

    coordinator.data = _make_team_data(next_match=None)
    assert sensor.entity_picture is None


def test_last_match_date_sensor_reports_played_datetime(hass):
    """native_value returns the last match's date, and attrs expose the venue."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        last_match=_make_match(
            match_date=datetime(2026, 1, 5, 19, 0, tzinfo=UTC),
            is_played=True,
            gym_city="Dax",
        )
    )
    sensor = FFBBLastMatchDateSensor(coordinator)

    assert sensor.native_value == datetime(2026, 1, 5, 19, 0, tzinfo=UTC)
    assert sensor.extra_state_attributes[ATTR_GYM_CITY] == "Dax"


def test_last_match_date_sensor_none_before_any_match_played(hass):
    """Pre-season, with no match played yet, this must be None, not crash."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(last_match=None)
    sensor = FFBBLastMatchDateSensor(coordinator)

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_last_match_opponent_sensor_reports_name(hass):
    """native_value returns the last opponent's name."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        last_match=_make_match(opponent_name="Stade Montois", is_played=True)
    )
    sensor = FFBBLastMatchOpponentSensor(coordinator)

    assert sensor.native_value == "Stade Montois"


def test_last_match_opponent_sensor_none_before_any_match_played(hass):
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(last_match=None)
    sensor = FFBBLastMatchOpponentSensor(coordinator)

    assert sensor.native_value is None


def test_last_match_opponent_sensor_entity_picture_and_logo_attrs(hass):
    """entity_picture and extra_state_attributes both surface the logos for
    the last played match; both are None/empty with no match played yet."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        last_match=_make_match(
            is_played=True,
            team_logo_url="https://api.ffbb.app/assets/team",
            opponent_logo_url="https://api.ffbb.app/assets/opp",
        )
    )
    sensor = FFBBLastMatchOpponentSensor(coordinator)
    assert sensor.entity_picture == "https://api.ffbb.app/assets/opp"
    assert sensor.extra_state_attributes == {
        "team_logo_url": "https://api.ffbb.app/assets/team",
        "opponent_logo_url": "https://api.ffbb.app/assets/opp",
    }

    coordinator.data = _make_team_data(last_match=None)
    assert sensor.entity_picture is None
    assert sensor.extra_state_attributes == {}


@pytest.mark.parametrize("result", ["win", "loss", "draw"])
def test_last_match_result_sensor_reports_each_possible_result(hass, result):
    """Every value _attr_options declares (win/loss/draw) must round-trip."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        last_match=_make_match(is_played=True, result=result)
    )
    sensor = FFBBLastMatchResultSensor(coordinator)

    assert sensor.native_value == result


def test_last_match_result_sensor_none_before_any_match_played(hass):
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(last_match=None)
    sensor = FFBBLastMatchResultSensor(coordinator)

    assert sensor.native_value is None


def test_poule_sensor_reports_name_and_attrs(hass):
    """native_value returns the pool name; attrs expose competition/team."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(poule_name="Excellence - Poule A")
    sensor = FFBBPouleSensor(coordinator)

    assert sensor.native_value == "Excellence - Poule A"
    assert sensor.extra_state_attributes["competition"] == "Excellence Régionale"
    assert sensor.extra_state_attributes["team"] == "Basket Landes"


def test_poule_sensor_none_before_first_refresh(hass):
    coordinator = _make_coordinator(hass)
    sensor = FFBBPouleSensor(coordinator)

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


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
    """Google Maps / Waze / geo URLs are built from the URL-encoded address.

    Uses quote() (%20 for spaces), not quote_plus() (+ for spaces): Waze's
    own deep-link documentation requires %20, and its mobile app does not
    reliably decode '+' as a space.
    """
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

    encoded = "Gymnase%20Andr%C3%A9%20Chavanne%2C%201%20rue%20du%20Stade%2C%2040000%2C%20Mont-de-Marsan"
    assert attrs["navigation_url"] == f"geo:0,0?q={encoded}"
    assert (
        attrs["google_maps_url"]
        == f"https://www.google.com/maps/dir/?api=1&destination={encoded}"
    )
    assert attrs["waze_url"] == f"https://www.waze.com/ul?q={encoded}&navigate=yes"


def test_next_match_location_generates_navigation_urls(hass):
    """Same URL-building logic, duplicated on the location sensor -- must
    stay in sync with FFBBNextMatchOpponentSensor's version (nothing here
    is shared code, so a fix applied to one and not the other would
    otherwise go unnoticed).
    """
    coordinator = _make_coordinator(hass)
    match = _make_match(
        gym_name="Gymnase André Chavanne",
        gym_address="1 rue du Stade",
        gym_postal_code="40000",
        gym_city="Mont-de-Marsan",
    )
    coordinator.data = _make_team_data(next_match=match)

    sensor = FFBBNextMatchLocationSensor(coordinator)
    attrs = sensor.extra_state_attributes

    encoded = "Gymnase%20Andr%C3%A9%20Chavanne%2C%201%20rue%20du%20Stade%2C%2040000%2C%20Mont-de-Marsan"
    assert attrs["navigation_url"] == f"geo:0,0?q={encoded}"
    assert (
        attrs["google_maps_url"]
        == f"https://www.google.com/maps/dir/?api=1&destination={encoded}"
    )
    assert attrs["waze_url"] == f"https://www.waze.com/ul?q={encoded}&navigate=yes"


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
# FFBBRankEvolutionExtraData: the RestoreSensor payload contract
# ---------------------------------------------------------------------------


def test_rank_evolution_extra_data_round_trips():
    """as_dict() -> from_dict() must reproduce the original values exactly."""
    original = FFBBRankEvolutionExtraData(current_position=3, previous_position=5)

    restored = FFBBRankEvolutionExtraData.from_dict(original.as_dict())

    assert restored is not None
    assert restored.current_position == 3
    assert restored.previous_position == 5


def test_rank_evolution_extra_data_from_dict_sanitizes_string_positions():
    """Positions stored/restored as strings (legacy state) must still parse as int."""
    restored = FFBBRankEvolutionExtraData.from_dict(
        {"current_position": "3", "previous_position": "5"}
    )

    assert restored is not None
    assert restored.current_position == 3
    assert restored.previous_position == 5


@pytest.mark.parametrize("malformed", [None, "not-a-dict", 42, []])
def test_rank_evolution_extra_data_from_dict_rejects_non_dict(malformed):
    """A non-dict restored payload must return None, not raise."""
    assert FFBBRankEvolutionExtraData.from_dict(malformed) is None


def test_rank_evolution_extra_data_from_dict_handles_missing_keys():
    """Missing keys in the restored payload must fall back to None, not KeyError."""
    restored = FFBBRankEvolutionExtraData.from_dict({})

    assert restored is not None
    assert restored.current_position is None
    assert restored.previous_position is None


# ---------------------------------------------------------------------------
# FFBBRankEvolutionSensor: diff sign convention and dynamic icon
# ---------------------------------------------------------------------------


def _trigger_update(sensor: FFBBRankEvolutionSensor, monkeypatch) -> None:
    """Call _handle_coordinator_update without requiring a hass-registered entity."""
    monkeypatch.setattr(sensor, "async_write_ha_state", lambda: None)
    sensor._handle_coordinator_update()


def test_rank_evolution_first_update_establishes_zero_baseline(hass, monkeypatch):
    """On the very first update (cold start), current == previous, so diff is 0."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=5, points=10, played=5, won=5, lost=0, raw={}
        )
    )
    sensor = FFBBRankEvolutionSensor(coordinator)

    _trigger_update(sensor, monkeypatch)

    assert sensor.native_value == "0"
    assert sensor.extra_state_attributes["current_position"] == 5
    assert sensor.extra_state_attributes["previous_position"] == 5


def test_rank_evolution_shows_positive_diff_when_rank_improves(hass, monkeypatch):
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
    sensor = FFBBRankEvolutionSensor(coordinator)
    _trigger_update(sensor, monkeypatch)  # baseline: current=5, previous=5

    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=3, points=13, played=6, won=6, lost=0, raw={}
        )
    )
    _trigger_update(sensor, monkeypatch)

    assert sensor.native_value == "+2"


def test_rank_evolution_shows_negative_diff_when_rank_drops(hass, monkeypatch):
    """Moving from position 3 to position 6 (a worse rank) must show '-3' and a down arrow."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=3, points=13, played=6, won=6, lost=0, raw={}
        )
    )
    sensor = FFBBRankEvolutionSensor(coordinator)
    _trigger_update(sensor, monkeypatch)  # baseline: current=3, previous=3

    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=6, points=13, played=7, won=6, lost=1, raw={}
        )
    )
    _trigger_update(sensor, monkeypatch)

    assert sensor.native_value == "-3"


def test_rank_evolution_unchanged_position_keeps_zero(hass, monkeypatch):
    """Two consecutive updates at the same position must not drift the diff."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=4, points=8, played=4, won=4, lost=0, raw={}
        )
    )
    sensor = FFBBRankEvolutionSensor(coordinator)
    _trigger_update(sensor, monkeypatch)
    _trigger_update(sensor, monkeypatch)  # same position again

    assert sensor.native_value == "0"


def test_rank_evolution_dash_when_standings_not_yet_published(hass, monkeypatch):
    """Before any standings exist, the sensor must show '-' rather than 'Unavailable'.

    This is a deliberate UX choice documented in sensor.py: returning None here
    would render the entity greyed-out/"Unavailable", which users mistake for a
    broken integration during pre-season or for youth categories without a
    published ranking yet.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(team_standing=None)
    sensor = FFBBRankEvolutionSensor(coordinator)

    _trigger_update(sensor, monkeypatch)

    assert sensor.native_value == "-"
    assert "status" in sensor.extra_state_attributes


def test_rank_evolution_resumes_diffing_after_restore(hass, monkeypatch):
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
    sensor = FFBBRankEvolutionSensor(coordinator)

    # Simulate a restored state from a previous session, as async_added_to_hass
    # would set it from FFBBRankEvolutionExtraData.
    sensor._current_position = 4
    sensor._previous_position = 5

    _trigger_update(sensor, monkeypatch)  # position unchanged (4): no shift expected

    assert sensor.native_value == "+1"  # 5 - 4, preserved from the restored data


def _icon_for_range(ranges: dict[str, str], default: str, value: float) -> str:
    """Replicate HA's range-icon algorithm: highest range key <= value."""
    candidates = [
        (float(key), icon) for key, icon in ranges.items() if float(key) <= value
    ]
    if not candidates:
        return default
    return max(candidates, key=lambda pair: pair[0])[1]


def test_rank_evolution_icons_json_covers_every_possible_native_value():
    """icons.json must resolve the right icon for every value native_value can emit.

    The dynamic `icon` property was removed from FFBBRankEvolutionSensor in
    favor of icon translations (icons.json range-based icons, HA 2025.5+),
    which HA resolves by parsing the entity's *state string* as a float and
    picking the highest range key <= that value -- so this test drives the
    same algorithm against real native_value() outputs rather than trusting
    Python logic that no longer exists.
    """
    icons_path = (
        Path(__file__).parent.parent
        / "custom_components"
        / "ffbb_tracker"
        / "icons.json"
    )
    data = json.loads(icons_path.read_text())
    node = data["entity"]["sensor"]["rank_evolution"]
    default = node["default"]
    ranges = node["range"]

    # A team can climb/drop many places in one update (e.g. after a bye week
    # or a bulk results correction), not just +/-1 -- exercise a wider spread.
    assert _icon_for_range(ranges, default, float("+3")) == "mdi:arrow-up-bold"
    assert _icon_for_range(ranges, default, float("+1")) == "mdi:arrow-up-bold"
    assert _icon_for_range(ranges, default, float("0")) == "mdi:minus"
    assert _icon_for_range(ranges, default, float("-1")) == "mdi:arrow-down-bold"
    assert _icon_for_range(ranges, default, float("-8")) == "mdi:arrow-down-bold"
    # native_value() returns "-" (non-numeric) while awaiting the first
    # published ranking -- HA falls back to "default" when the state can't
    # be parsed as a number.
    assert default == "mdi:minus"


# ---------------------------------------------------------------------------
# FFBBRankSensor: sanity check that it stays independent of the evolution sensor
# ---------------------------------------------------------------------------


def test_rank_sensor_reports_current_position_and_standings(hass):
    """The plain rank sensor exposes position as state and the full table as attrs."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        team_standing=TeamStanding(
            position=2, points=10, played=5, won=5, lost=0, raw={}
        ),
        standings=[{"position": 1, "team_name": "Other"}],
    )

    sensor = FFBBRankSensor(coordinator)

    assert sensor.native_value == 2
    assert sensor.extra_state_attributes["standings"] == [
        {"position": 1, "team_name": "Other"}
    ]


def test_rank_sensor_none_when_no_standing(hass):
    """No team_standing (not yet ranked) must yield a None state, not an error."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(team_standing=None)

    sensor = FFBBRankSensor(coordinator)

    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# _get_form_letters
# ---------------------------------------------------------------------------


def test_get_form_letters_returns_french_for_french_instance(hass):
    """When hass.config.language is 'fr', French letter codes must be used."""
    hass.config.language = "fr"

    letters = _get_form_letters(hass)

    assert letters == {"win": "V", "loss": "D", "draw": "N"}


def test_get_form_letters_defaults_to_english_for_other_languages(hass):
    """An unsupported language code must fall back to English, not crash."""
    hass.config.language = "de"

    letters = _get_form_letters(hass)

    assert letters == {"win": "W", "loss": "L", "draw": "D"}


def test_get_form_letters_defaults_to_english_when_hass_is_none():
    """No hass object at all (e.g. entity not yet added) must not raise."""
    assert _get_form_letters(None) == {"win": "W", "loss": "L", "draw": "D"}


# ---------------------------------------------------------------------------
# FFBBFormSensor
# ---------------------------------------------------------------------------


def _played(match_id: str, result: str) -> MatchDetails:
    """Shorthand for a played fixture with only the fields the form
    sensor actually reads (is_played and result).
    """
    return _make_match(match_id=match_id, is_played=True, result=result)


def test_form_sensor_none_when_no_played_matches(hass):
    """A season with no played matches yet must yield a None state, not a
    crash or an empty-string form.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        fixtures=[_make_match(match_id="m1", is_played=False, result=None)]
    )

    sensor = FFBBFormSensor(coordinator)

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_form_sensor_orders_most_recent_match_first(hass):
    """fixtures is stored oldest-to-newest; the form string must read
    most-recent-first, matching how a person would say "we won our last
    match" (first) rather than burying it at the end.
    """
    coordinator = _make_coordinator(hass)
    hass.config.language = "en"
    coordinator.data = _make_team_data(
        fixtures=[
            _played("m1", "loss"),
            _played("m2", "win"),
        ]
    )

    sensor = FFBBFormSensor(coordinator)
    sensor.hass = hass

    assert sensor.native_value == "W-L"


def test_form_sensor_only_considers_the_last_five_played_matches(hass):
    """A team with more than 5 played matches must only be summarized by
    the 5 most recent ones -- older results must not leak into the form
    string or the win/loss/draw counts.
    """
    coordinator = _make_coordinator(hass)
    hass.config.language = "en"
    coordinator.data = _make_team_data(
        fixtures=[
            _played("m1", "loss"),  # oldest, must be excluded
            _played("m2", "win"),
            _played("m3", "win"),
            _played("m4", "loss"),
            _played("m5", "win"),
            _played("m6", "win"),  # most recent
        ]
    )

    sensor = FFBBFormSensor(coordinator)
    sensor.hass = hass

    assert sensor.native_value == "W-W-L-W-W"
    assert sensor.extra_state_attributes["matches_considered"] == 5
    assert sensor.extra_state_attributes["wins"] == 4
    assert sensor.extra_state_attributes["losses"] == 1
    assert sensor.extra_state_attributes["draws"] == 0


def test_form_sensor_excludes_unplayed_and_result_less_fixtures(hass):
    """Upcoming fixtures, and played matches without a computed result
    (the is_played=True-but-malformed-score edge case), must not appear
    in the form string.
    """
    coordinator = _make_coordinator(hass)
    hass.config.language = "en"
    coordinator.data = _make_team_data(
        fixtures=[
            _played("m1", "win"),
            _make_match(match_id="m2", is_played=True, result=None),  # malformed
            _make_match(match_id="m3", is_played=False, result=None),  # upcoming
            _played("m4", "loss"),
        ]
    )

    sensor = FFBBFormSensor(coordinator)
    sensor.hass = hass

    assert sensor.native_value == "L-W"


def test_form_sensor_current_streak_counts_consecutive_identical_results(hass):
    """A current_streak of "3W" means the 3 most recent played matches were
    all wins; it must stop counting at the first different result.
    """
    coordinator = _make_coordinator(hass)
    hass.config.language = "en"
    coordinator.data = _make_team_data(
        fixtures=[
            _played("m1", "loss"),
            _played("m2", "win"),
            _played("m3", "win"),
            _played("m4", "win"),
        ]
    )

    sensor = FFBBFormSensor(coordinator)
    sensor.hass = hass

    assert sensor.extra_state_attributes["current_streak"] == "3W"


def test_form_sensor_streak_of_one_when_result_just_changed(hass):
    """A single most-recent result different from the one before it must
    report a streak of 1, not a stale/leftover count.
    """
    coordinator = _make_coordinator(hass)
    hass.config.language = "en"
    coordinator.data = _make_team_data(
        fixtures=[
            _played("m1", "win"),
            _played("m2", "win"),
            _played("m3", "loss"),
        ]
    )

    sensor = FFBBFormSensor(coordinator)
    sensor.hass = hass

    assert sensor.extra_state_attributes["current_streak"] == "1L"
