"""Event platform for the FFBB Tracker integration."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FFBBConfigEntry
from .const import (
    ATTR_GYM_CITY,
    ATTR_GYM_NAME,
    ATTR_IS_HOME,
    ATTR_MATCH_DATE,
    ATTR_OPPONENT,
    ATTR_OPPONENT_SCORE,
    ATTR_POINT_DIFFERENCE,
    ATTR_ROUND,
    ATTR_TEAM_SCORE,
    DOMAIN,
)
from .coordinator import FFBBDataUpdateCoordinator, FFBBTeamData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FFBBConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FFBB Tracker event entities based on a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            FFBBMatchFinishedEvent(coordinator),
            FFBBRankChangedEvent(coordinator),
        ]
    )


class FFBBBaseEvent(CoordinatorEntity[FFBBDataUpdateCoordinator], EventEntity):
    """Common device wiring shared by the FFBB Tracker event entities."""

    _attr_has_entity_name = True
    _attr_attribution = "Données fournies par competitions.ffbb.com"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator, key: str) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.engagement_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.engagement_id)},
            name=f"{coordinator.team_name} - {coordinator.competition_name}",
            manufacturer="FFBB",
            model=coordinator.competition_name,
            entry_type=DeviceEntryType.SERVICE,
        )


class FFBBMatchFinishedEvent(FFBBBaseEvent):
    """Fires once when a new match result is published.

    Distinct from sensor.last_match_result, which holds a permanent state
    that stays at "win"/"loss"/"draw" until the next match: this entity
    only fires the instant a *new* result appears, so automations can
    trigger on "a result just came in" without re-firing on every Home
    Assistant restart or coordinator refresh where nothing actually
    changed.
    """

    _attr_translation_key = "match_finished"
    _attr_event_types = ["win", "loss", "draw"]  # noqa: RUF012

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the match finished event entity."""
        super().__init__(coordinator, "match_finished")
        self._known_last_match_id = self._last_match_id(coordinator.data)

    @staticmethod
    def _last_match_id(data: FFBBTeamData | None) -> str | None:
        """Return the id of the current last_match, or None."""
        if data and data.last_match:
            return data.last_match.match_id
        return None

    def _handle_coordinator_update(self) -> None:
        """Fire the event exactly once when a new match result appears."""
        data = self.coordinator.data
        last_match = data.last_match if data else None
        new_id = self._last_match_id(data)

        if (
            last_match is not None
            and last_match.result is not None
            and new_id != self._known_last_match_id
        ):
            point_difference: int | None = None
            if (
                last_match.team_score is not None
                and last_match.opponent_score is not None
            ):
                point_difference = last_match.team_score - last_match.opponent_score

            self._trigger_event(
                last_match.result,
                {
                    ATTR_OPPONENT: last_match.opponent_name,
                    ATTR_TEAM_SCORE: last_match.team_score,
                    ATTR_OPPONENT_SCORE: last_match.opponent_score,
                    ATTR_POINT_DIFFERENCE: point_difference,
                    ATTR_IS_HOME: last_match.is_home,
                    ATTR_MATCH_DATE: (
                        last_match.match_date.isoformat()
                        if last_match.match_date
                        else None
                    ),
                    ATTR_ROUND: last_match.round_number,
                    ATTR_GYM_NAME: last_match.gym_name,
                    ATTR_GYM_CITY: last_match.gym_city,
                },
            )

        self._known_last_match_id = new_id
        super()._handle_coordinator_update()


class FFBBRankChangedEvent(FFBBBaseEvent):
    """Fires once when the team's pool rank position changes.

    Distinct from sensor.rank_evolution, which keeps showing the last
    delta (e.g. "+1") until the next standings update: this entity fires
    the instant the position actually moves, with both the old and the
    new position as event data -- a pair the sensor's single delta value
    doesn't retain.
    """

    _attr_translation_key = "rank_changed"
    _attr_event_types = ["up", "down"]  # noqa: RUF012

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the rank changed event entity."""
        super().__init__(coordinator, "rank_changed")
        self._known_position = self._position(coordinator.data)

    @staticmethod
    def _position(data: FFBBTeamData | None) -> int | None:
        """Return the team's current standing position, or None."""
        if data and data.team_standing:
            return data.team_standing.position
        return None

    def _handle_coordinator_update(self) -> None:
        """Fire the event exactly once when the standing position changes."""
        new_position = self._position(self.coordinator.data)

        if (
            new_position is not None
            and self._known_position is not None
            and new_position != self._known_position
        ):
            event_type = "up" if new_position < self._known_position else "down"
            self._trigger_event(
                event_type,
                {
                    "old_position": self._known_position,
                    "new_position": new_position,
                },
            )

        if new_position is not None:
            self._known_position = new_position
        super()._handle_coordinator_update()
