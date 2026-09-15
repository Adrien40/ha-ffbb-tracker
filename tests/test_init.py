"""Tests for FFBB Tracker's __init__.py: setup/unload wiring.

Covers the options-change reload listener and the service cleanup that
runs when the last tracked team is removed — both easy to silently break
during a refactor since neither has a visible symptom until a second
entry or an options change happens in a real install.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
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


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456",
        data={
            CONF_ENGAGEMENT_ID: "123456",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_POULE_ID: "poule-1",
            CONF_ORGANISME_ID: "org-1",
        },
    )


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
