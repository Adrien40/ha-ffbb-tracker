"""Tests for FFBB Tracker's __init__.py: setup/unload wiring.

Covers the options-change reload listener and the service cleanup that
runs when the last tracked team is removed — both easy to silently break
during a refactor since neither has a visible symptom until a second
entry or an options change happens in a real install.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker import _async_update_listener
from custom_components.ffbb_tracker.const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DOMAIN,
)

MINIMAL_POULE_PAYLOAD = {
    "id": "poule-1",
    "nom": "Poule A",
    "rencontres": [],
    "classements": [],
}


def _make_entry(
    engagement_id: str = "123456", poule_id: str = "poule-1"
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=engagement_id,
        data={
            CONF_ENGAGEMENT_ID: engagement_id,
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_POULE_ID: poule_id,
            CONF_ORGANISME_ID: "org-1",
        },
    )


SERVICES = ("refresh", "get_next_matches", "get_standings")


# ---------------------------------------------------------------------------
# async_setup_entry: full end-to-end bootstrap
# ---------------------------------------------------------------------------


async def test_async_setup_entry_loads_coordinator_platforms_and_services(hass):
    """Setting up an entry wires everything: a working coordinator ends up
    on entry.runtime_data, every platform's entities exist, and the three
    services are registered -- the actual real-world path every user goes
    through, as opposed to only unit-testing the pieces in isolation.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.ffbb_tracker.FFBBClient.get_poule_data",
        AsyncMock(return_value=MINIMAL_POULE_PAYLOAD),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not None
    assert entry.runtime_data.data is not None

    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    platforms = [e.entity_id.split(".")[0] for e in entries]
    assert platforms.count("sensor") == 12
    assert platforms.count("binary_sensor") == 2
    assert platforms.count("button") == 1
    assert platforms.count("calendar") == 1
    assert platforms.count("event") == 2

    assert hass.services.has_service(DOMAIN, "refresh")
    assert hass.services.has_service(DOMAIN, "get_next_matches")
    assert hass.services.has_service(DOMAIN, "get_standings")


async def test_update_listener_reloads_the_entry(hass):
    """Changing an option (e.g. polling interval) must trigger a reload."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(return_value=True)
    ) as mock_reload:
        await _async_update_listener(hass, entry)

    mock_reload.assert_awaited_once_with(entry.entry_id)


# ---------------------------------------------------------------------------
# Service actions: registered once in async_setup, never torn down
# ---------------------------------------------------------------------------


async def test_services_are_registered_without_any_config_entry(hass):
    """Actions must exist as soon as the integration is set up.

    Home Assistant's `action-setup` rule: they are registered in
    `async_setup`, not `async_setup_entry`, so automations that reference
    them validate even while no team is loaded (startup, connection error).
    """
    assert await async_setup_component(hass, DOMAIN, {})

    for service in SERVICES:
        assert hass.services.has_service(DOMAIN, service)


async def test_services_survive_unloading_the_last_entry(hass):
    """Unloading the last team (what every options change does, as part of
    a reload) must not remove the actions: an automation calling one in that
    window would otherwise fail with 'service not found'."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ffbb_tracker.FFBBClient.get_poule_data",
        AsyncMock(return_value=MINIMAL_POULE_PAYLOAD),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    for service in SERVICES:
        assert hass.services.has_service(DOMAIN, service)
    # The shared rate limiter is kept, so request pacing survives a reload.
    assert "_rate_limiter" in hass.data[DOMAIN]
    # Without a target, a call while nothing is loaded is a harmless no-op.
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)


@pytest.mark.parametrize("service", SERVICES)
async def test_targeting_an_unloaded_entry_raises_a_clear_error(hass, service):
    """An explicit entry_id whose team isn't loaded gets `entry_not_loaded`,
    not a silent no-op and not an empty result."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ffbb_tracker.FFBBClient.get_poule_data",
        AsyncMock(return_value=MINIMAL_POULE_PAYLOAD),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            service,
            {"entry_id": entry.entry_id},
            blocking=True,
            return_response=service != "refresh",
        )

    assert err.value.translation_key == "entry_not_loaded"
    assert err.value.translation_placeholders == {"entry_id": entry.entry_id}


# ---------------------------------------------------------------------------
# Poule cache: dropped with its last user, kept while a sibling still uses it
# ---------------------------------------------------------------------------


async def _setup(hass, entry) -> None:
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ffbb_tracker.FFBBClient.get_poule_data",
        AsyncMock(return_value=MINIMAL_POULE_PAYLOAD),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_unloading_the_last_user_of_a_poule_drops_its_cache(hass):
    entry = _make_entry()
    await _setup(hass, entry)
    assert "poule-1" in hass.data[DOMAIN]["_poule_cache"]

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert "poule-1" not in hass.data[DOMAIN]["_poule_cache"]


async def test_unloading_one_of_two_teams_keeps_the_shared_poule_cache(hass):
    first = _make_entry("111", "poule-1")
    second = _make_entry("222", "poule-1")
    await _setup(hass, first)
    await _setup(hass, second)

    assert await hass.config_entries.async_unload(first.entry_id)

    assert "poule-1" in hass.data[DOMAIN]["_poule_cache"]
    assert second.state is ConfigEntryState.LOADED
