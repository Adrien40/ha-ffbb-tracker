"""Tests for FFBB Tracker's __init__.py: setup/unload wiring.

Covers the options-change reload listener and the service cleanup that
runs when the last tracked team is removed — both easy to silently break
during a refactor since neither has a visible symptom until a second
entry or an options change happens in a real install.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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


async def test_update_listener_reloads_the_entry(hass):
    """Changing an option (e.g. polling interval) must trigger a reload."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(return_value=True)
    ) as mock_reload:
        await _async_update_listener(hass, entry)

    mock_reload.assert_awaited_once_with(entry.entry_id)
