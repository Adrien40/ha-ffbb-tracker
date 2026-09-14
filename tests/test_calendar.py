"""Tests for calendar.py.

Nothing in this platform had test coverage before. The riskiest parts are
not obvious from a quick read: `_get_labels` silently falls back to
English for any language it doesn't recognize (so a typo'd language code
never crashes, but also never warns); `event` must skip matches without
a `match_date` instead of crashing on `None + timedelta`; and
`_create_calendar_event` flips the "vs" order based on `is_home` and
only appends the result line for *played* matches with a score, which
is easy to get backwards without anyone noticing until a Home or Away
game looks swapped on the calendar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.calendar import FFBBCalendarEntity, _get_labels
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
# _get_labels
# ---------------------------------------------------------------------------


def test_get_labels_returns_french_for_french_instance(hass):
    """When hass.config.language is 'fr', French labels must be used."""
    hass.config.language = "fr"

    labels = _get_labels(hass)

    assert labels["competition"] == "🏆 Compétition"
    assert labels["win"] == "victoire"


def test_get_labels_defaults_to_english_for_other_languages(hass):
    """An unsupported language code must fall back to English, not crash."""
    hass.config.language = "de"

    labels = _get_labels(hass)

    assert labels["competition"] == "🏆 Competition"


def test_get_labels_defaults_to_english_when_hass_is_none():
    """No hass object at all (e.g. entity not yet added) must not raise."""
    assert _get_labels(None)["competition"] == "🏆 Competition"


# ---------------------------------------------------------------------------
# FFBBCalendarEntity.event: "next upcoming" lookup
# ---------------------------------------------------------------------------


def test_event_is_none_without_fixtures(hass):
    """No coordinator data / no fixtures at all must yield no active event."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(fixtures=[])
    entity = FFBBCalendarEntity(coordinator)

    assert entity.event is None


def test_event_skips_matches_without_a_date(hass):
    """A fixture with match_date=None must be skipped, not crash on None + timedelta."""
    coordinator = _make_coordinator(hass)
    now = datetime.now(UTC)
    undated = _make_match(match_id="undated", match_date=None)
    dated = _make_match(match_id="dated", match_date=now + timedelta(days=1))
    coordinator.data = _make_team_data(fixtures=[undated, dated])
    entity = FFBBCalendarEntity(coordinator)

    event = entity.event

    assert event is not None
    assert event.uid == "ffbb_match_dated"


def test_event_returns_first_fixture_not_yet_ended(hass):
    """A match that ended more than 2h ago must not be reported as 'the' event."""
    coordinator = _make_coordinator(hass)
    now = datetime.now(UTC)
    finished_long_ago = _make_match(match_id="past", match_date=now - timedelta(days=1))
    upcoming = _make_match(match_id="upcoming", match_date=now + timedelta(hours=3))
    coordinator.data = _make_team_data(fixtures=[finished_long_ago, upcoming])
    entity = FFBBCalendarEntity(coordinator)

    event = entity.event

    assert event is not None
    assert event.uid == "ffbb_match_upcoming"


def test_event_still_reports_match_within_its_2h_window(hass):
    """A match that started 1h ago (still 'live', within the 2h event window) counts."""
    coordinator = _make_coordinator(hass)
    now = datetime.now(UTC)
    ongoing = _make_match(match_id="ongoing", match_date=now - timedelta(hours=1))
    coordinator.data = _make_team_data(fixtures=[ongoing])
    entity = FFBBCalendarEntity(coordinator)

    event = entity.event

    assert event is not None
    assert event.uid == "ffbb_match_ongoing"


# ---------------------------------------------------------------------------
# FFBBCalendarEntity.async_get_events: date-range filtering
# ---------------------------------------------------------------------------


async def test_async_get_events_includes_overlapping_fixtures(hass):
    """A fixture whose 2h window overlaps the requested range must be returned."""
    coordinator = _make_coordinator(hass)
    match_date = datetime(2026, 3, 1, 18, 0, tzinfo=UTC)
    match = _make_match(match_id="in-range", match_date=match_date)
    coordinator.data = _make_team_data(fixtures=[match])
    entity = FFBBCalendarEntity(coordinator)

    events = await entity.async_get_events(
        hass,
        start_date=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        end_date=datetime(2026, 3, 2, 0, 0, tzinfo=UTC),
    )

    assert [e.uid for e in events] == ["ffbb_match_in-range"]


async def test_async_get_events_excludes_fixtures_outside_range(hass):
    """A fixture entirely outside the requested range must not be returned."""
    coordinator = _make_coordinator(hass)
    match = _make_match(
        match_id="out-of-range", match_date=datetime(2026, 5, 1, 18, 0, tzinfo=UTC)
    )
    coordinator.data = _make_team_data(fixtures=[match])
    entity = FFBBCalendarEntity(coordinator)

    events = await entity.async_get_events(
        hass,
        start_date=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        end_date=datetime(2026, 3, 2, 0, 0, tzinfo=UTC),
    )

    assert events == []


async def test_async_get_events_skips_undated_fixtures(hass):
    """A fixture with no match_date must never appear in a ranged lookup."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(
        fixtures=[_make_match(match_id="undated", match_date=None)]
    )
    entity = FFBBCalendarEntity(coordinator)

    events = await entity.async_get_events(
        hass,
        start_date=datetime(2020, 1, 1, tzinfo=UTC),
        end_date=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert events == []


async def test_async_get_events_empty_without_coordinator_data(hass):
    """No coordinator data at all must yield an empty list, not raise."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(fixtures=[])
    entity = FFBBCalendarEntity(coordinator)

    events = await entity.async_get_events(
        hass,
        start_date=datetime(2020, 1, 1, tzinfo=UTC),
        end_date=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert events == []


# ---------------------------------------------------------------------------
# _create_calendar_event: summary orientation, localization, result line
# ---------------------------------------------------------------------------


def test_create_event_summary_for_home_match(hass):
    """A home match must read '<team> vs <opponent>'."""
    coordinator = _make_coordinator(hass)
    match = _make_match(is_home=True)
    coordinator.data = _make_team_data(fixtures=[match])
    entity = FFBBCalendarEntity(coordinator)

    event = entity._create_calendar_event(match)

    assert event.summary == "Basket Landes vs US Mont-de-Marsan"


def test_create_event_summary_for_away_match(hass):
    """An away match must read '<opponent> vs <team>' (order flipped)."""
    coordinator = _make_coordinator(hass)
    match = _make_match(is_home=False)
    coordinator.data = _make_team_data(fixtures=[match])
    entity = FFBBCalendarEntity(coordinator)

    event = entity._create_calendar_event(match)

    assert event.summary == "US Mont-de-Marsan vs Basket Landes"


def test_create_event_description_uses_hass_language(hass):
    """The description must switch to French labels when hass.config.language is 'fr'."""
    hass.config.language = "fr"
    coordinator = _make_coordinator(hass)
    match = _make_match()
    coordinator.data = _make_team_data(
        fixtures=[match], poule_name="Excellence Régionale - Poule A"
    )
    entity = FFBBCalendarEntity(coordinator)
    entity.hass = hass

    event = entity._create_calendar_event(match)

    assert "🏆 Compétition : Excellence Régionale" in event.description
    assert "📌 Poule : Excellence Régionale - Poule A" in event.description


def test_create_event_omits_result_line_for_unplayed_match(hass):
    """An unplayed match must not show a result line at all."""
    coordinator = _make_coordinator(hass)
    match = _make_match(is_played=False, team_score=None, opponent_score=None)
    coordinator.data = _make_team_data(fixtures=[match])
    entity = FFBBCalendarEntity(coordinator)

    event = entity._create_calendar_event(match)

    assert "Result" not in event.description
    assert "Résultat" not in event.description


def test_create_event_includes_localized_result_for_played_match(hass):
    """A played match must show the score and the *localized* result word."""
    hass.config.language = "fr"
    coordinator = _make_coordinator(hass)
    match = _make_match(is_played=True, team_score=68, opponent_score=54, result="win")
    coordinator.data = _make_team_data(fixtures=[match])
    entity = FFBBCalendarEntity(coordinator)
    entity.hass = hass

    event = entity._create_calendar_event(match)

    assert "📊 Résultat : 68 - 54 (victoire)" in event.description


def test_create_event_falls_back_to_raw_result_for_unknown_value(hass):
    """An unrecognized result string must be shown as-is instead of crashing."""
    coordinator = _make_coordinator(hass)
    match = _make_match(
        is_played=True, team_score=1, opponent_score=1, result="forfeit"
    )
    coordinator.data = _make_team_data(fixtures=[match])
    entity = FFBBCalendarEntity(coordinator)

    event = entity._create_calendar_event(match)

    assert "(forfeit)" in event.description


def test_create_event_uid_and_location(hass):
    """The uid must be namespaced per match, and location must be the formatted address."""
    coordinator = _make_coordinator(hass)
    match = _make_match(match_id="abc-123")
    coordinator.data = _make_team_data(fixtures=[match])
    entity = FFBBCalendarEntity(coordinator)

    event = entity._create_calendar_event(match)

    assert event.uid == "ffbb_match_abc-123"
    assert event.location == match.formatted_address


def test_create_event_uses_current_time_when_match_date_missing(hass):
    """A match without a date must still produce a valid (non-crashing) event."""
    coordinator = _make_coordinator(hass)
    match = _make_match(match_date=None)
    coordinator.data = _make_team_data(fixtures=[match])
    entity = FFBBCalendarEntity(coordinator)

    event = entity._create_calendar_event(match)

    assert event.end == event.start + timedelta(hours=2)
