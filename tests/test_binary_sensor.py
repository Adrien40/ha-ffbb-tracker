"""Tests for binary_sensor.py.

Both binary sensors are thin wrappers around coordinator properties
(`is_game_day`, `is_match_in_progress`) that are themselves the same condition
already used to speed up polling around match time. The risk worth
guarding against isn't the entity wiring (unique_id/device_info, copied
from the button pattern) — it's the boundary conditions of that shared
condition: exactly-now, just-before-the-window, just-after-the-window,
and "score already published" correctly turning the sensor back off
even while still inside the time window.
"""

from __future__ import annotations

import copy
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ffbb_tracker.binary_sensor import (
    FFBBGameDayBinarySensor,
    FFBBMatchInProgressBinarySensor,
    FFBBPhaseChangeBinarySensor,
)
from custom_components.ffbb_tracker.const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DEFAULT_LIVE_WINDOW_AFTER_HOURS,
    DOMAIN,
    LIVE_WINDOW_BEFORE_MINUTES,
)
from custom_components.ffbb_tracker.coordinator import (
    FFBBDataUpdateCoordinator,
    FFBBTeamData,
    MatchDetails,
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
        "match_date": dt_util.utcnow() + timedelta(days=1),
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
# Entity wiring (unique_id / device_info), same pattern as test_button.py
# ---------------------------------------------------------------------------


def test_game_day_unique_id_and_device_info(hass):
    """The game-day sensor must have a stable, namespaced unique_id."""
    coordinator = _make_coordinator(hass)
    entity = FFBBGameDayBinarySensor(coordinator)

    assert entity.unique_id == "engagement-123_game_day"
    assert entity.device_info["identifiers"] == {(DOMAIN, "engagement-123")}


def test_match_in_progress_unique_id_and_device_info(hass):
    """The match-in-progress sensor must have a stable, namespaced unique_id."""
    coordinator = _make_coordinator(hass)
    entity = FFBBMatchInProgressBinarySensor(coordinator)

    assert entity.unique_id == "engagement-123_match_in_progress"
    assert entity.device_info["identifiers"] == {(DOMAIN, "engagement-123")}


# ---------------------------------------------------------------------------
# binary_sensor.game_day
# ---------------------------------------------------------------------------


def test_game_day_is_on_when_next_match_is_today(hass):
    """A next match later today (any hour) must turn the sensor on."""
    coordinator = _make_coordinator(hass)
    today_evening = dt_util.now().replace(hour=20, minute=0, second=0, microsecond=0)
    coordinator.data = _make_team_data(
        next_match=_make_match(match_date=dt_util.as_utc(today_evening))
    )

    entity = FFBBGameDayBinarySensor(coordinator)
    assert entity.is_on is True


def test_game_day_is_off_when_next_match_is_tomorrow(hass):
    """A next match on a different calendar day must not trip the sensor."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(match_date=dt_util.utcnow() + timedelta(days=1))
    )

    entity = FFBBGameDayBinarySensor(coordinator)
    assert entity.is_on is False


def test_game_day_is_off_with_no_next_match(hass):
    """No scheduled next match must not raise and must read as off."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(next_match=None)

    entity = FFBBGameDayBinarySensor(coordinator)
    assert entity.is_on is False


def test_game_day_is_off_with_no_coordinator_data_yet(hass):
    """A coordinator that hasn't refreshed yet must not raise."""
    coordinator = _make_coordinator(hass)
    assert coordinator.data is None

    entity = FFBBGameDayBinarySensor(coordinator)
    assert entity.is_on is False


# ---------------------------------------------------------------------------
# binary_sensor.match_in_progress
# ---------------------------------------------------------------------------


def test_match_in_progress_is_off_long_before_kickoff(hass):
    """A match far in the future must not trigger the live window."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(match_date=dt_util.utcnow() + timedelta(hours=5))
    )

    entity = FFBBMatchInProgressBinarySensor(coordinator)
    assert entity.is_on is False


def test_match_in_progress_is_off_just_before_kickoff(hass):
    """Even inside the polling window, a match that has not started is not live."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(
            match_date=dt_util.utcnow()
            + timedelta(minutes=LIVE_WINDOW_BEFORE_MINUTES - 5)
        )
    )

    entity = FFBBMatchInProgressBinarySensor(coordinator)
    assert entity.is_on is False


def test_match_in_progress_is_off_one_minute_before_kickoff(hass):
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(match_date=dt_util.utcnow() + timedelta(minutes=1))
    )

    entity = FFBBMatchInProgressBinarySensor(coordinator)
    assert entity.is_on is False


def test_match_in_progress_is_on_right_at_kickoff(hass):
    """The kick-off instant itself must count as live."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(match_date=dt_util.utcnow())
    )

    entity = FFBBMatchInProgressBinarySensor(coordinator)
    assert entity.is_on is True


def test_match_in_progress_is_on_after_kickoff_awaiting_result(hass):
    """Started, unplayed, and within the after-match window: still live."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(
            match_date=dt_util.utcnow() - timedelta(hours=1),
            is_played=False,
        )
    )

    entity = FFBBMatchInProgressBinarySensor(coordinator)
    assert entity.is_on is True


def test_match_in_progress_is_off_once_result_is_published(hass):
    """Even inside the after-match window, a published result turns it off."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(
            match_date=dt_util.utcnow() - timedelta(hours=1),
            is_played=True,
            result="win",
        )
    )

    entity = FFBBMatchInProgressBinarySensor(coordinator)
    assert entity.is_on is False


def test_match_in_progress_is_off_beyond_the_after_match_window(hass):
    """Long after kickoff and still unplayed (delayed result), sensor times out."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        next_match=_make_match(
            match_date=dt_util.utcnow()
            - timedelta(hours=DEFAULT_LIVE_WINDOW_AFTER_HOURS + 1),
            is_played=False,
        )
    )

    entity = FFBBMatchInProgressBinarySensor(coordinator)
    assert entity.is_on is False


def test_match_in_progress_is_off_with_no_next_match(hass):
    """No scheduled next match must not raise and must read as off."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(next_match=None)

    entity = FFBBMatchInProgressBinarySensor(coordinator)
    assert entity.is_on is False


def test_match_in_progress_is_off_with_no_coordinator_data_yet(hass):
    """A coordinator that hasn't refreshed yet must not raise."""
    coordinator = _make_coordinator(hass)
    assert coordinator.data is None

    entity = FFBBMatchInProgressBinarySensor(coordinator)
    assert entity.is_on is False


# ---------------------------------------------------------------------------
# binary_sensor.phase_change_suspected
# ---------------------------------------------------------------------------


def test_phase_change_unique_id_and_device_info(hass):
    """The phase change sensor must have a stable, namespaced unique_id."""
    coordinator = _make_coordinator(hass)
    entity = FFBBPhaseChangeBinarySensor(coordinator)

    assert entity.unique_id == "engagement-123_phase_change_suspected"
    assert entity.device_info["identifiers"] == {(DOMAIN, "engagement-123")}


def test_phase_change_is_on_when_nothing_is_left_after_the_delay(hass):
    """All matches played, the last one older than the delay: phase is over."""
    coordinator = _make_coordinator(hass)
    last = _make_match(is_played=True, match_date=dt_util.utcnow() - timedelta(days=4))
    coordinator.data = _make_team_data(last_match=last, fixtures=[last])

    assert FFBBPhaseChangeBinarySensor(coordinator).is_on is True


def test_phase_change_is_off_before_the_delay(hass):
    """A match played yesterday is not yet a reason to worry."""
    coordinator = _make_coordinator(hass)
    last = _make_match(is_played=True, match_date=dt_util.utcnow() - timedelta(days=1))
    coordinator.data = _make_team_data(last_match=last, fixtures=[last])

    assert FFBBPhaseChangeBinarySensor(coordinator).is_on is False


def test_phase_change_is_off_while_a_match_is_scheduled(hass):
    """An upcoming match means the team still has something to play."""
    coordinator = _make_coordinator(hass)
    old = _make_match(is_played=True, match_date=dt_util.utcnow() - timedelta(days=30))
    upcoming = _make_match(match_id="match-2")
    coordinator.data = _make_team_data(
        next_match=upcoming, last_match=old, fixtures=[old, upcoming]
    )

    assert FFBBPhaseChangeBinarySensor(coordinator).is_on is False


def test_phase_change_is_off_with_an_empty_calendar(hass):
    """No fixture at all proves nothing: the calendar may not be out yet."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(fixtures=[])

    assert FFBBPhaseChangeBinarySensor(coordinator).is_on is False


def test_phase_change_is_off_when_no_fixture_has_a_date(hass):
    """Fixtures without a date can't be aged."""
    coordinator = _make_coordinator(hass)
    undated = _make_match(is_played=True, match_date=None)
    coordinator.data = _make_team_data(fixtures=[undated])

    assert FFBBPhaseChangeBinarySensor(coordinator).is_on is False


def test_phase_change_is_off_with_no_coordinator_data_yet(hass):
    """A coordinator that hasn't refreshed yet must not raise."""
    coordinator = _make_coordinator(hass)
    assert coordinator.data is None

    assert FFBBPhaseChangeBinarySensor(coordinator).is_on is False


def test_phase_change_follows_the_configured_delay(hass):
    """The delay comes from the options: 10 days is not reached after 4."""
    coordinator = _make_coordinator(hass)
    coordinator._phase_change_after = timedelta(days=10)
    last = _make_match(is_played=True, match_date=dt_util.utcnow() - timedelta(days=4))
    coordinator.data = _make_team_data(last_match=last, fixtures=[last])

    assert FFBBPhaseChangeBinarySensor(coordinator).is_on is False


async def test_game_day_turns_on_at_midnight_without_any_refresh(
    hass, freezer, sample_poule_data
):
    """The match is tomorrow evening. At local midnight the sensor must flip
    to on by itself, not at the next coordinator refresh (up to an hour later).
    """
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to("2026-03-14T22:59:50+00:00")  # 23:59:50 in Paris
    payload = copy.deepcopy(sample_poule_data)
    payload["rencontres"][1]["date_rencontre"] = "2026-03-15T19:00:00+01:00"
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
    get_poule = AsyncMock(return_value=payload)

    with patch("custom_components.ffbb_tracker.FFBBClient.get_poule_data", get_poule):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = er.async_get(hass).async_get_entity_id(
            "binary_sensor", DOMAIN, "engagement-123_game_day"
        )
        assert hass.states.get(entity_id).state == "off"

        freezer.move_to("2026-03-14T23:00:01+00:00")  # 00:00:01 in Paris
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        assert hass.states.get(entity_id).state == "on"
        assert get_poule.await_count == 1
