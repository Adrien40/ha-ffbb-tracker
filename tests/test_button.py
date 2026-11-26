"""Tests for button.py.

The refresh button had no dedicated coverage before. Its only real
behavior is delegating to the coordinator's refresh mechanism, but that
delegation is precisely the kind of one-line wiring that silently
breaks in a refactor (e.g. calling `async_refresh` instead of
`async_request_refresh`, which would drop the debounce and hammer the
FFBB API on every button tap). This suite also locks down the entity's
unique_id and device_info, since those drive entity registry identity
and a change there would orphan the entity for existing users.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.button import FFBBRefreshButtonEntity
from custom_components.ffbb_tracker.const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DOMAIN,
)
from custom_components.ffbb_tracker.coordinator import FFBBDataUpdateCoordinator


def _make_coordinator(hass) -> FFBBDataUpdateCoordinator:
    """Build a coordinator wired to engagement-123 / Basket Landes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENGAGEMENT_ID: "engagement-123",
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry.add_to_hass(hass)
    return FFBBDataUpdateCoordinator(hass, client=None, entry=entry)


async def test_press_requests_a_coordinator_refresh(hass):
    """Pressing the button must trigger the debounced refresh path.

    Specifically `async_request_refresh` (debounced, safe to spam) and
    not `async_refresh` (immediate, no debounce) — the latter would let
    a user hammering the button flood the FFBB API.
    """
    coordinator = _make_coordinator(hass)
    coordinator.async_request_refresh = AsyncMock()

    button = FFBBRefreshButtonEntity(coordinator)
    await button.async_press()

    coordinator.async_request_refresh.assert_awaited_once()


def test_unique_id_is_scoped_to_the_engagement(hass):
    """The unique_id must stay stable and namespaced per tracked team."""
    coordinator = _make_coordinator(hass)
    button = FFBBRefreshButtonEntity(coordinator)

    assert button.unique_id == "engagement-123_refresh"


def test_device_info_matches_the_tracked_team(hass):
    """The button must attach to the same device as the team's sensors."""
    coordinator = _make_coordinator(hass)
    button = FFBBRefreshButtonEntity(coordinator)

    device_info = button.device_info
    assert device_info is not None
    assert device_info["identifiers"] == {(DOMAIN, "engagement-123")}
    assert device_info["name"] == "Basket Landes - Excellence Régionale"
