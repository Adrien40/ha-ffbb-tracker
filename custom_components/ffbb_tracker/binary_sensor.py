"""Binary sensor platform for the FFBB Tracker integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FFBBConfigEntry
from .const import DOMAIN
from .coordinator import FFBBDataUpdateCoordinator

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
        ]
    )


class FFBBBaseBinarySensor(
    CoordinatorEntity[FFBBDataUpdateCoordinator], BinarySensorEntity
):
    """Common device wiring shared by the FFBB Tracker binary sensors."""

    _attr_has_entity_name = True
    _attr_attribution = "Données fournies par competitions.ffbb.com"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator, key: str) -> None:
        """Initialize the binary sensor entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.engagement_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.engagement_id)},
            name=f"{coordinator.team_name} - {coordinator.competition_name}",
            manufacturer="FFBB",
            model=coordinator.competition_name,
            entry_type=DeviceEntryType.SERVICE,
        )


class FFBBGameDayBinarySensor(FFBBBaseBinarySensor):
    """Whether the tracked team has a match scheduled today."""

    _attr_translation_key = "game_day"
    _attr_icon = "mdi:basketball"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the game day binary sensor."""
        super().__init__(coordinator, "game_day")

    @property
    def is_on(self) -> bool:
        """Return True if the next match is scheduled for today."""
        return self.coordinator.is_game_day


class FFBBMatchInProgressBinarySensor(FFBBBaseBinarySensor):
    """Whether the tracked team's match is starting soon or awaiting its result."""

    _attr_translation_key = "match_in_progress"
    _attr_icon = "mdi:basketball-hoop"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the match in progress binary sensor."""
        super().__init__(coordinator, "match_in_progress")

    @property
    def is_on(self) -> bool:
        """Return True while the match is live (starting soon or unplayed past kickoff)."""
        return self.coordinator.is_match_live
