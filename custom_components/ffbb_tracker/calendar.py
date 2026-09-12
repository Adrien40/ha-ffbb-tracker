"""Calendar platform for the FFBB Tracker integration."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import FFBBConfigEntry
from .const import DOMAIN
from .coordinator import FFBBDataUpdateCoordinator, MatchDetails

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
    language: str | None = getattr(getattr(hass, "config", None), "language", None)
    return _LABELS.get(language, _LABELS["en"]) if language else _LABELS["en"]


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
    _attr_icon = "mdi:calendar-month"
    _attr_attribution = "Données fournies par competitions.ffbb.com"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the calendar entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.engagement_id}_calendar"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.engagement_id)},
            name=f"{coordinator.team_name} - {coordinator.competition_name}",
            manufacturer="FFBB",
            model=coordinator.competition_name,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming fixture event."""
        if not self.coordinator.data or not self.coordinator.data.fixtures:
            return None

        now = dt_util.utcnow()
        for match in self.coordinator.data.fixtures:
            if not match.match_date:
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
            if not match.match_date:
                continue

            event_start = match.match_date
            event_end = event_start + timedelta(hours=2)

            if event_start <= end_date and event_end >= start_date:
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

        if match.is_played and match.team_score is not None:
            result_word = labels.get(match.result or "", match.result or "")
            description_lines.append(
                f"{labels['result']} : {match.team_score} - "
                f"{match.opponent_score} ({result_word})"
            )

        return CalendarEvent(
            summary=summary,
            start=start,
            end=end,
            location=match.formatted_address,
            description="\n".join(description_lines),
            uid=f"ffbb_match_{match.match_id}",
        )
