"""The FFBB Tracker integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FFBBClient, FFBBRateLimiter
from .const import DOMAIN
from .coordinator import FFBBDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.CALENDAR,
    Platform.BUTTON,
]

SERVICE_REFRESH = "refresh"
SERVICE_GET_NEXT_MATCHES = "get_next_matches"
SERVICE_GET_STANDINGS = "get_standings"

SERVICE_NEXT_MATCHES_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("limit", default=3): cv.positive_int,
    }
)

SERVICE_STANDINGS_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
    }
)

type FFBBConfigEntry = ConfigEntry[FFBBDataUpdateCoordinator]


def get_rate_limiter(hass: HomeAssistant) -> FFBBRateLimiter:
    """Return the shared rate limiter instance for the integration domain.

    Public (no leading underscore) because it is also used by config_flow.py
    to throttle club/engagement searches during setup and reconfiguration,
    so that every FFBBClient instance created anywhere in the integration
    shares the same request pacing, instead of only the coordinators.
    """
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    if "_rate_limiter" not in domain_data:
        domain_data["_rate_limiter"] = FFBBRateLimiter()
    return domain_data["_rate_limiter"]


async def async_setup_entry(hass: HomeAssistant, entry: FFBBConfigEntry) -> bool:
    """Set up FFBB Tracker from a config entry."""
    session = async_get_clientsession(hass)
    rate_limiter = get_rate_limiter(hass)
    client = FFBBClient(session, rate_limiter=rate_limiter)

    coordinator = FFBBDataUpdateCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: FFBBConfigEntry) -> None:
    """Reload the entry when its options change (e.g. polling interval)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FFBBConfigEntry) -> bool:
    """Unload a config entry and clean up services when the last entry is removed."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        loaded_entries = [
            e
            for e in hass.config_entries.async_loaded_entries(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not loaded_entries:
            for service in (
                SERVICE_REFRESH,
                SERVICE_GET_NEXT_MATCHES,
                SERVICE_GET_STANDINGS,
            ):
                hass.services.async_remove(DOMAIN, service)
            hass.data.pop(DOMAIN, None)

    return unload_ok


def async_setup_services(hass: HomeAssistant) -> None:
    """Register FFBB Tracker integration actions."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def handle_refresh(call: ServiceCall) -> None:
        """Handle manual coordinator data refresh."""
        target_entry_id = call.data.get("entry_id")
        entries = hass.config_entries.async_entries(DOMAIN)

        for entry in entries:
            if target_entry_id and entry.entry_id != target_entry_id:
                continue
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator:
                await coordinator.async_request_refresh()

    async def handle_get_next_matches(call: ServiceCall) -> dict[str, Any]:
        """Handle returning upcoming matches as action response."""
        target_entry_id = call.data.get("entry_id")
        limit = call.data.get("limit", 3)
        entries = hass.config_entries.async_entries(DOMAIN)

        results: list[dict[str, Any]] = []
        for entry in entries:
            if target_entry_id and entry.entry_id != target_entry_id:
                continue
            coordinator = getattr(entry, "runtime_data", None)
            if not coordinator or not coordinator.data:
                continue

            upcoming = [
                {
                    "date": match.match_date.isoformat() if match.match_date else None,
                    "team": match.team_name,
                    "opponent": match.opponent_name,
                    "is_home": match.is_home,
                    "gym": match.gym_name,
                    "address": match.gym_address,
                    "city": match.gym_city,
                }
                for match in coordinator.data.fixtures
                if not match.is_played
            ][:limit]

            results.append(
                {
                    "team_name": coordinator.data.team_name,
                    "competition": coordinator.data.competition_name,
                    "matches": upcoming,
                }
            )

        return {"teams": results}

    async def handle_get_standings(call: ServiceCall) -> dict[str, Any]:
        """Handle returning pool standings as action response."""
        target_entry_id = call.data.get("entry_id")
        entries = hass.config_entries.async_entries(DOMAIN)

        results: list[dict[str, Any]] = []
        for entry in entries:
            if target_entry_id and entry.entry_id != target_entry_id:
                continue
            coordinator = getattr(entry, "runtime_data", None)
            if not coordinator or not coordinator.data:
                continue

            results.append(
                {
                    "team_name": coordinator.data.team_name,
                    "competition": coordinator.data.competition_name,
                    "poule": coordinator.data.poule_name,
                    "standings": coordinator.data.standings,
                }
            )

        return {"standings": results}

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH,
        handle_refresh,
        schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_NEXT_MATCHES,
        handle_get_next_matches,
        schema=SERVICE_NEXT_MATCHES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_STANDINGS,
        handle_get_standings,
        schema=SERVICE_STANDINGS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
