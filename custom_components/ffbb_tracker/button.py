"""Button platform for the FFBB Tracker integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FFBBConfigEntry
from .const import DOMAIN
from .coordinator import FFBBDataUpdateCoordinator

# The button only requests a (debounced) coordinator refresh; it performs
# no direct network I/O itself, so there is nothing to throttle here.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FFBBConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FFBB Tracker button based on a config entry."""
    coordinator = entry.runtime_data
    async_add_entities([FFBBRefreshButtonEntity(coordinator)])


class FFBBRefreshButtonEntity(
    CoordinatorEntity[FFBBDataUpdateCoordinator], ButtonEntity
):
    """Button entity allowing manual data refresh."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the refresh button entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.engagement_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.engagement_id)},
            name=f"{coordinator.team_name} - {coordinator.competition_name}",
            manufacturer="FFBB",
            model=coordinator.competition_name,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        """Handle the button press to trigger an immediate data refresh."""
        await self.coordinator.async_request_refresh()
