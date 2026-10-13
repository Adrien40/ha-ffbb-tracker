"""Helpers shared by the platforms of FFBB Tracker."""

from __future__ import annotations

from urllib.parse import quote

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import (
    ATTR_GOOGLE_MAPS_URL,
    ATTR_NAVIGATION_URL,
    ATTR_WAZE_URL,
    DOMAIN,
)
from .coordinator import FFBBDataUpdateCoordinator


def team_device_info(coordinator: FFBBDataUpdateCoordinator) -> DeviceInfo:
    """Return the device every entity of a tracked team belongs to."""
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.engagement_id)},
        name=f"{coordinator.team_name} - {coordinator.competition_name}",
        manufacturer="FFBB",
        model=coordinator.competition_name,
        entry_type=DeviceEntryType.SERVICE,
    )


def navigation_attributes(formatted_address: str | None) -> dict[str, str | None]:
    """Return the navigation link attributes for a gym address.

    A `geo:` URI (opens the phone's default maps app), Google Maps and Waze.
    All three are None when the match has no address.
    """
    if not formatted_address:
        return {
            ATTR_NAVIGATION_URL: None,
            ATTR_GOOGLE_MAPS_URL: None,
            ATTR_WAZE_URL: None,
        }
    encoded = quote(formatted_address)
    return {
        ATTR_NAVIGATION_URL: f"geo:0,0?q={encoded}",
        ATTR_GOOGLE_MAPS_URL: (
            f"https://www.google.com/maps/dir/?api=1&destination={encoded}"
        ),
        ATTR_WAZE_URL: f"https://www.waze.com/ul?q={encoded}&navigate=yes",
    }
