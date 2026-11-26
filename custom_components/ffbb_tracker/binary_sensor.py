"""Binary sensor platform for the FFBB Tracker integration."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FFBBConfigEntry
from .const import ATTRIBUTION
from .coordinator import FFBBDataUpdateCoordinator
from .entity import team_device_info

# Entities are read-only views over coordinator.data (a single shared poll
# per team); there is no per-entity network I/O to throttle here.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FFBBConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FFBB Tracker binary sensors based on a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            FFBBGameDayBinarySensor(coordinator),
            FFBBMatchInProgressBinarySensor(coordinator),
            FFBBPhaseChangeBinarySensor(coordinator),
        ]
    )


class FFBBBaseBinarySensor(
    CoordinatorEntity[FFBBDataUpdateCoordinator], BinarySensorEntity
):
    """Common device wiring shared by the FFBB Tracker binary sensors."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: FFBBDataUpdateCoordinator, key: str) -> None:
        """Initialize the binary sensor entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.engagement_id}_{key}"
        self._attr_device_info = team_device_info(coordinator)


class FFBBGameDayBinarySensor(FFBBBaseBinarySensor):
    """Whether the tracked team has a match scheduled today."""

    _attr_translation_key = "game_day"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the game day binary sensor."""
        super().__init__(coordinator, "game_day")

    async def async_added_to_hass(self) -> None:
        """Re-evaluate at every local midnight, whatever the polling interval.

        "Today" changes at midnight, not when the coordinator next refreshes
        (up to an hour later by default, a day with a long interval).
        """
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._handle_midnight, hour=0, minute=0, second=0
            )
        )

    @callback
    def _handle_midnight(self, _now: datetime) -> None:
        """Write the state again: the same data now means another day."""
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True if the next match is scheduled for today."""
        return self.coordinator.is_game_day


class FFBBMatchInProgressBinarySensor(FFBBBaseBinarySensor):
    """Whether the tracked team's match has started and has no result yet."""

    _attr_translation_key = "match_in_progress"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the match in progress binary sensor."""
        super().__init__(coordinator, "match_in_progress")

    @property
    def is_on(self) -> bool:
        """Return True from kick-off until the result is published."""
        return self.coordinator.is_match_in_progress


class FFBBPhaseChangeBinarySensor(FFBBBaseBinarySensor):
    """Whether the team has no match left and has moved on to a new phase."""

    _attr_translation_key = "phase_change_suspected"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the phase change binary sensor."""
        super().__init__(coordinator, "phase_change_suspected")

    @property
    def is_on(self) -> bool:
        """Return True once nothing is planned any more, after the set delay."""
        return self.coordinator.phase_change_suspected
