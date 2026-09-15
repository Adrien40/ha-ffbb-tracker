"""Tests for repairs.py -- the season_rollover fix flow.

season_rollover is the only fixable repair issue this integration raises
(see coordinator.py); token_refresh_failing is deliberately
is_fixable=False and never reaches async_create_fix_flow. These tests
pin down that: the fix flow shows a confirm step before doing anything,
confirming it hands off to the entry's own reconfigure flow (rather than
trying to fix anything itself), and that a stale issue pointing at an
already-removed config entry doesn't crash the flow.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DOMAIN,
)
from custom_components.ffbb_tracker.repairs import (
    SeasonRolloverRepairFlow,
    async_create_fix_flow,
)


def _make_entry(hass) -> MockConfigEntry:
    """Add a minimal, realistic config entry to hass and return it."""
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
    return entry


async def test_async_create_fix_flow_extracts_entry_id(hass):
    """async_create_fix_flow must read entry_id out of the issue's data
    dict (as set by coordinator._handle_not_found) and hand it to the
    flow it returns.
    """
    flow = await async_create_fix_flow(
        hass, "season_rollover_engagement-123", {"entry_id": "abc123"}
    )

    assert isinstance(flow, SeasonRolloverRepairFlow)
    assert flow._entry_id == "abc123"


async def test_async_create_fix_flow_handles_missing_data(hass):
    """No `data` dict at all must not crash -- fall back to an empty
    entry_id rather than raising, so a malformed/legacy issue can't take
    down the repairs dialog.
    """
    flow = await async_create_fix_flow(hass, "season_rollover_x", None)

    assert flow._entry_id == ""


async def test_fix_flow_shows_confirm_step_first(hass):
    """Opening the flow (no user_input yet) must show a confirmation
    form, not act immediately -- the user should see what they're about
    to do before the reconfigure flow is launched underneath them.
    """
    entry = _make_entry(hass)
    flow = SeasonRolloverRepairFlow(entry.entry_id)
    flow.hass = hass

    result = await flow.async_step_init()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"


async def test_confirming_launches_reconfigure_flow_for_the_entry(hass):
    """Confirming must start a new flow on the *same* config entry with
    source=reconfigure -- this is the actual "fix": there's no code-level
    repair for a season rollover, only a guided hand-off into the normal
    team-search flow.
    """
    entry = _make_entry(hass)
    flow = SeasonRolloverRepairFlow(entry.entry_id)
    flow.hass = hass
    hass.config_entries.flow.async_init = AsyncMock(
        return_value={"type": FlowResultType.FORM}
    )

    result = await flow.async_step_confirm(user_input={})

    hass.config_entries.flow.async_init.assert_awaited_once_with(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_confirming_with_removed_entry_does_not_crash(hass):
    """If the config entry was already removed (e.g. the user deleted the
    integration instead of reconfiguring it) by the time they click
    through the repair, the flow must still complete instead of raising
    -- there's nothing left to reconfigure, so it's a silent no-op.
    """
    flow = SeasonRolloverRepairFlow(entry_id="does-not-exist")
    flow.hass = hass
    hass.config_entries.flow.async_init = AsyncMock()

    result = await flow.async_step_confirm(user_input={})

    hass.config_entries.flow.async_init.assert_not_awaited()
    assert result["type"] is FlowResultType.CREATE_ENTRY
