"""Calendar platform for the FFBB Tracker integration."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import FFBBConfigEntry
from .const import ATTRIBUTION
from .coordinator import FFBBDataUpdateCoordinator, MatchDetails
from .entity import primary_language, team_device_info

# The calendar entity only reads coordinator.data; it performs no direct
# network I/O of its own, so there is nothing to throttle here.
PARALLEL_UPDATES = 0

# Calendar event descriptions are free text, not translated through HA's
# entity translation system, but we still want them to match the language
# the user picked for their Home Assistant instance rather than being
# hardcoded in a single language.
_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "competition": "🏆 Compétition",
        "poule": "📌 Poule",
        "round": "📅 Journée",
        "match": "Match n°",
        "result": "📊 Résultat",
        "win": "victoire",
        "loss": "défaite",
        "draw": "match nul",
    },
    "en": {
        "competition": "🏆 Competition",
        "poule": "📌 Pool",
        "round": "📅 Round",
        "match": "Match #",
        "result": "📊 Result",
        "win": "win",
        "loss": "loss",
        "draw": "draw",
    },
}


def _get_labels(hass: HomeAssistant | None) -> dict[str, str]:
    """Return the label set matching the instance language, defaulting to English."""
    return _LABELS.get(primary_language(hass), _LABELS["en"])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FFBBConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FFBB Tracker calendar based on a config entry."""
    coordinator = entry.runtime_data
    async_add_entities([FFBBCalendarEntity(coordinator)])


class FFBBCalendarEntity(CoordinatorEntity[FFBBDataUpdateCoordinator], CalendarEntity):
    """Calendar entity showing all scheduled and completed fixtures."""

    _attr_has_entity_name = True
    _attr_translation_key = "schedule"
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the calendar entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.engagement_id}_calendar"
        self._attr_device_info = team_device_info(coordinator)

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming fixture event."""
        if not self.coordinator.data or not self.coordinator.data.fixtures:
            return None

        now = dt_util.utcnow()
        for match in self.coordinator.data.fixtures:
            # A bye round is a fixture without an opponent: it is not a game
            # to attend, so it never becomes "the next event".
            if not match.match_date or match.is_exempt:
                continue

            event_end = match.match_date + timedelta(hours=2)
            if event_end >= now:
                return self._create_calendar_event(match)

        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return calendar events within a datetime range."""
        if not self.coordinator.data or not self.coordinator.data.fixtures:
            return []

        events: list[CalendarEvent] = []
        for match in self.coordinator.data.fixtures:
            # Bye rounds ("Exempt") have no opponent and no game to show.
            if not match.match_date or match.is_exempt:
                continue

            event_start = match.match_date
            event_end = event_start + timedelta(hours=2)

            # Home Assistant's range is [start_date, end_date): an event that
            # starts exactly at the end, or ends exactly at the start, is out.
            if event_start < end_date and event_end > start_date:
                events.append(self._create_calendar_event(match))

        return events

    def _create_calendar_event(self, match: MatchDetails) -> CalendarEvent:
        """Convert a match data structure into a Home Assistant CalendarEvent."""
        start = match.match_date or dt_util.utcnow()
        end = start + timedelta(hours=2)

        if match.is_home:
            summary = f"{match.team_name} vs {match.opponent_name}"
        else:
            summary = f"{match.opponent_name} vs {match.team_name}"

        poule_name = self.coordinator.data.poule_name if self.coordinator.data else ""
        labels = _get_labels(getattr(self, "hass", None))
        description_lines = [
            f"{labels['competition']} : {self.coordinator.competition_name}",
            f"{labels['poule']} : {poule_name}",
            (
                f"{labels['round']} : {match.round_number} "
                f"({labels['match']}{match.match_number})"
            ),
        ]

        if (
            match.is_played
            and match.team_score is not None
            and match.opponent_score is not None
        ):
            result_word = labels.get(match.result or "", match.result or "")
            # Same order as the title: home score first, away score second.
            home_score, away_score = (
                (match.team_score, match.opponent_score)
                if match.is_home
                else (match.opponent_score, match.team_score)
            )
            description_lines.append(
                f"{labels['result']} : {home_score} - {away_score} ({result_word})"
            )

        return CalendarEvent(
            summary=summary,
            start=start,
            end=end,
            location=match.formatted_address,
            description="\n".join(description_lines),
            uid=f"ffbb_match_{match.match_id}",
        )
