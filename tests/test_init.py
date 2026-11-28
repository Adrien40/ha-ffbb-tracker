"""Tests for FFBB Tracker's __init__.py: setup/unload wiring.

Covers the reload behaviour and the service cleanup that
runs when the last tracked team is removed — both easy to silently break
during a refactor since neither has a visible symptom until a second
entry or an options change happens in a real install.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ffbb_tracker import async_remove_entry
from custom_components.ffbb_tracker.api import FFBBNotFoundError
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
    assert platforms.count("binary_sensor") == 3
    assert platforms.count("button") == 1
    assert platforms.count("calendar") == 1
    assert platforms.count("event") == 2

    assert hass.services.has_service(DOMAIN, "refresh")
    assert hass.services.has_service(DOMAIN, "get_next_matches")
    assert hass.services.has_service(DOMAIN, "get_standings")


async def _setup_loaded_entry(hass, get_poule: AsyncMock) -> MockConfigEntry:
    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch("custom_components.ffbb_tracker.FFBBClient.get_poule_data", get_poule):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_season_rollover_alert_survives_a_home_assistant_restart(
    hass, hass_storage
):
    """The team has been returning 404 for four days according to the saved
    streak, and Home Assistant has just restarted: the very first refresh must
    raise the repair issue instead of starting the delay over.
    """
    key = "ffbb_tracker.not_found_since"
    since = dt_util.utcnow() - timedelta(days=4)
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"123456:poule-1": since.isoformat()},
    }
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.ffbb_tracker.FFBBClient.get_poule_data",
        AsyncMock(side_effect=FFBBNotFoundError("gone")),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "season_rollover_123456") is not None
    )


async def test_removing_an_entry_forgets_its_not_found_clock(hass, hass_storage):
    key = "ffbb_tracker.not_found_since"
    entry = _make_entry()
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})["_not_found_since"] = {
        "123456:poule-1": dt_util.utcnow(),
        "other:poule-9": dt_util.utcnow(),
    }

    await async_remove_entry(hass, entry)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()

    assert list(hass_storage[key]["data"]) == ["other:poule-9"]


async def test_removing_an_entry_deletes_all_its_repair_issues(hass):
    """Deleting a tracked team must clear every repair alert raised for it."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    for prefix in ("season_rollover", "phase_change", "token_refresh_failing"):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{prefix}_123456",
            is_fixable=prefix != "token_refresh_failing",
            severity=ir.IssueSeverity.WARNING,
            translation_key=prefix,
            translation_placeholders={"team_name": "Basket Landes"},
        )
    ir.async_create_issue(
        hass,
        DOMAIN,
        "season_rollover_other",
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="season_rollover",
        translation_placeholders={"team_name": "Other"},
    )

    await async_remove_entry(hass, entry)

    registry = ir.async_get(hass)
    for prefix in ("season_rollover", "phase_change", "token_refresh_failing"):
        assert registry.async_get_issue(DOMAIN, f"{prefix}_123456") is None
    assert registry.async_get_issue(DOMAIN, "season_rollover_other") is not None


async def test_changing_options_reloads_the_entry_once(hass):
    """Saving the options form reloads the entry exactly once."""
    get_poule = AsyncMock(return_value=MINIMAL_POULE_PAYLOAD)
    entry = await _setup_loaded_entry(hass, get_poule)
    assert get_poule.await_count == 1

    with patch("custom_components.ffbb_tracker.FFBBClient.get_poule_data", get_poule):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 15}
        )
        await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.state is ConfigEntryState.LOADED
    assert get_poule.await_count == 2


async def test_updating_entry_then_scheduling_a_reload_reloads_once(hass):
    """Repairs and reconfiguration update the entry and reload it themselves;
    nothing else may reload it again (no update listener).
    """
    get_poule = AsyncMock(return_value=MINIMAL_POULE_PAYLOAD)
    entry = await _setup_loaded_entry(hass, get_poule)

    with patch("custom_components.ffbb_tracker.FFBBClient.get_poule_data", get_poule):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_POULE_ID: "poule-2"}
        )
        hass.config_entries.async_schedule_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert get_poule.await_count == 2


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
