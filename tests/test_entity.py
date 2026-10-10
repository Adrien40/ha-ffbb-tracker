"""Tests for entity.py: what every platform shares.

The five platforms (sensor, binary sensor, button, calendar, event) used to
each build the same device and the two location sensors the same navigation
links; if one drifted, entities of one team would silently land on different
devices or carry different links.
"""

from __future__ import annotations

import pytest
from homeassistant.helpers.device_registry import DeviceEntryType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.binary_sensor import FFBBGameDayBinarySensor
from custom_components.ffbb_tracker.button import FFBBRefreshButtonEntity
from custom_components.ffbb_tracker.calendar import FFBBCalendarEntity
from custom_components.ffbb_tracker.const import (
    ATTR_GOOGLE_MAPS_URL,
    ATTR_NAVIGATION_URL,
    ATTR_WAZE_URL,
    ATTRIBUTION,
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DOMAIN,
)
from custom_components.ffbb_tracker.coordinator import FFBBDataUpdateCoordinator
from custom_components.ffbb_tracker.entity import (
    navigation_attributes,
    team_device_info,
)
from custom_components.ffbb_tracker.event import (
    FFBBMatchFinishedEvent,
    FFBBRankChangedEvent,
)
from custom_components.ffbb_tracker.sensor import (
    FFBBNextMatchDateSensor,
    FFBBPouleSensor,
)


def _coordinator(hass) -> FFBBDataUpdateCoordinator:
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


def test_team_device_info_describes_the_tracked_team(hass):
    info = team_device_info(_coordinator(hass))

    assert info["identifiers"] == {(DOMAIN, "engagement-123")}
    assert info["name"] == "Basket Landes - Excellence Régionale"
    assert info["manufacturer"] == "FFBB"
    assert info["model"] == "Excellence Régionale"
    assert info["entry_type"] is DeviceEntryType.SERVICE


def test_every_platform_attaches_its_entities_to_the_same_device(hass):
    coordinator = _coordinator(hass)
    expected = team_device_info(coordinator)

    entities = [
        FFBBNextMatchDateSensor(coordinator),
        FFBBPouleSensor(coordinator),
        FFBBGameDayBinarySensor(coordinator),
        FFBBRefreshButtonEntity(coordinator),
        FFBBCalendarEntity(coordinator),
        FFBBMatchFinishedEvent(coordinator),
        FFBBRankChangedEvent(coordinator),
    ]

    for entity in entities:
        assert entity.device_info == expected, type(entity).__name__
        assert entity.attribution == ATTRIBUTION, type(entity).__name__


def test_navigation_attributes_encode_the_address_for_each_app():
    attrs = navigation_attributes("Gymnase André, 1 rue du Stade, 40100, Dax")

    assert attrs == {
        ATTR_NAVIGATION_URL: "geo:0,0?q=Gymnase%20Andr%C3%A9%2C%201%20rue%20du%20Stade%2C%2040100%2C%20Dax",
        ATTR_GOOGLE_MAPS_URL: (
            "https://www.google.com/maps/dir/?api=1&destination="
            "Gymnase%20Andr%C3%A9%2C%201%20rue%20du%20Stade%2C%2040100%2C%20Dax"
        ),
        ATTR_WAZE_URL: (
            "https://www.waze.com/ul?q="
            "Gymnase%20Andr%C3%A9%2C%201%20rue%20du%20Stade%2C%2040100%2C%20Dax"
            "&navigate=yes"
        ),
    }


@pytest.mark.parametrize("address", [None, ""])
def test_navigation_attributes_are_all_none_without_an_address(address):
    assert navigation_attributes(address) == {
        ATTR_NAVIGATION_URL: None,
        ATTR_GOOGLE_MAPS_URL: None,
        ATTR_WAZE_URL: None,
    }


def test_navigation_attributes_keep_their_order():
    """Attribute order is what users see in the entity's attributes panel."""
    assert list(navigation_attributes("x")) == [
        ATTR_NAVIGATION_URL,
        ATTR_GOOGLE_MAPS_URL,
        ATTR_WAZE_URL,
    ]
