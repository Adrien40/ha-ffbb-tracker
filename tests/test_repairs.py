"""Tests for repairs.py -- the season_rollover fix flow.

season_rollover is the only fixable repair issue this integration raises (see
coordinator.py). The fix searches for the team again *inside the repair
dialog* and switches the existing config entry over to the team's new IDs.
An earlier version started the entry's reconfigure flow from here instead;
Home Assistant doesn't list flows started that way, so the user clicked and
nothing ever appeared. The end-to-end test at the bottom drives the flow
through Home Assistant's own repairs manager, which is what decides what the
user sees.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.api import (
    FFBBApiError,
    FFBBConnectionError,
    FFBBNotFoundError,
)
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

from .test_init import MINIMAL_POULE_PAYLOAD

CLIENT = "custom_components.ffbb_tracker.repairs.FFBBClient"
TEAM_URL = (
    "https://competitions.ffbb.com/ligues/naq/comites/0040/clubs/naq0040116/"
    "equipes/200000005374157"
)
CLUB = {"id": "8459", "nom": "UJSBP", "code": "NAQ0040116"}
ENGAGEMENT = {
    "id": "200000005374157",
    "nom": "UJSBP U13M",
    "idCompetition": {"nom": "Départementale U13"},
    "idPoule": {"id": "200000003060032", "nom": "D2 Poule B"},
    "idOrganisme": {"id": "8459"},
}
NEW_DATA = {
    CONF_ENGAGEMENT_ID: "200000005374157",
    CONF_TEAM_NAME: "UJSBP U13M",
    CONF_COMPETITION_NAME: "Départementale U13",
    CONF_POULE_ID: "200000003060032",
    CONF_ORGANISME_ID: "8459",
}


def _make_entry(hass, engagement_id: str = "engagement-123") -> MockConfigEntry:
    """Add a minimal, realistic config entry to hass and return it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=engagement_id,
        data={
            CONF_ENGAGEMENT_ID: engagement_id,
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry.add_to_hass(hass)
    return entry


def _flow(hass, entry_id: str) -> SeasonRolloverRepairFlow:
    flow = SeasonRolloverRepairFlow(entry_id)
    flow.hass = hass
    return flow


# --- creation ---------------------------------------------------------------


async def test_async_create_fix_flow_extracts_entry_id(hass):
    """async_create_fix_flow must read entry_id out of the issue's data dict
    (as set by coordinator._handle_not_found) and hand it to the flow."""
    flow = await async_create_fix_flow(
        hass, "season_rollover_engagement-123", {"entry_id": "abc123"}
    )

    assert isinstance(flow, SeasonRolloverRepairFlow)
    assert flow._entry_id == "abc123"


async def test_async_create_fix_flow_handles_missing_data(hass):
    """A malformed/legacy issue with no data must not take down the dialog."""
    flow = await async_create_fix_flow(hass, "season_rollover_x", None)

    assert flow._entry_id == ""


# --- confirm ----------------------------------------------------------------


async def test_fix_flow_shows_confirm_step_first_with_the_team_name(hass):
    """The form texts use {team_name}; Home Assistant doesn't fill it in for a
    custom repair flow, so the flow must pass it or users read the raw
    placeholder."""
    entry = _make_entry(hass)

    result = await _flow(hass, entry.entry_id).async_step_init()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {"team_name": "Basket Landes"}


async def test_the_data_home_assistant_starts_the_flow_with_is_not_a_confirmation(hass):
    """Home Assistant calls the first step with {"issue_id": ...} as user_input.
    Forwarding it to the confirm step made it look like the user had already
    confirmed, so the explanation was skipped."""
    entry = _make_entry(hass)

    result = await _flow(hass, entry.entry_id).async_step_init({"issue_id": "x"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"


async def test_confirming_goes_on_to_the_search_without_starting_another_flow(hass):
    """The search happens in this dialog: starting a reconfigure flow in the
    background is what made the old fix invisible."""
    entry = _make_entry(hass)
    hass.config_entries.flow.async_init = AsyncMock()

    result = await _flow(hass, entry.entry_id).async_step_confirm(user_input={})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "search"
    hass.config_entries.flow.async_init.assert_not_awaited()


async def test_confirming_with_removed_entry_does_not_crash(hass):
    """If the integration entry was deleted before the user got here, there is
    nothing left to fix: the repair just closes."""
    result = await _flow(hass, "does-not-exist").async_step_confirm(user_input={})

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_placeholders_are_empty_for_a_removed_entry(hass):
    result = await _flow(hass, "does-not-exist").async_step_confirm()

    assert result["description_placeholders"] == {"team_name": ""}


# --- search -----------------------------------------------------------------


async def test_a_too_short_query_is_refused(hass):
    entry = _make_entry(hass)

    result = await _flow(hass, entry.entry_id).async_step_search({"search_query": "a"})

    assert result["step_id"] == "search"
    assert result["errors"] == {"base": "query_too_short"}


@pytest.mark.parametrize("query", [TEAM_URL, "200000005374157"])
async def test_a_team_url_or_id_switches_the_entry_over(hass, query):
    entry = _make_entry(hass)

    with (
        patch(f"{CLIENT}.get_engagement", AsyncMock(return_value=ENGAGEMENT)),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await _flow(hass, entry.entry_id).async_step_search(
            {"search_query": query}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.data) == NEW_DATA
    assert entry.title == "UJSBP U13M - Départementale U13"
    assert entry.unique_id == "200000005374157"
    reload.assert_called_once_with(entry.entry_id)


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (FFBBNotFoundError("gone"), "invalid_engagement"),
        (FFBBConnectionError("down"), "cannot_connect"),
        (FFBBApiError("boom"), "unknown"),
    ],
)
async def test_lookup_errors_keep_the_search_form_and_the_entry(hass, error, key):
    entry = _make_entry(hass)
    before = dict(entry.data)

    with patch(f"{CLIENT}.get_engagement", AsyncMock(side_effect=error)):
        result = await _flow(hass, entry.entry_id).async_step_search(
            {"search_query": TEAM_URL}
        )

    assert result["step_id"] == "search"
    assert result["errors"] == {"base": key}
    assert dict(entry.data) == before


async def test_a_team_without_a_pool_is_refused(hass):
    entry = _make_entry(hass)

    with patch(
        f"{CLIENT}.get_engagement",
        AsyncMock(return_value={**ENGAGEMENT, "idPoule": None}),
    ):
        result = await _flow(hass, entry.entry_id).async_step_search(
            {"search_query": TEAM_URL}
        )

    assert result["errors"] == {"base": "no_poule_found"}


async def test_searching_by_name_lists_the_clubs(hass):
    entry = _make_entry(hass)

    with patch(f"{CLIENT}.search_clubs", AsyncMock(return_value=[CLUB])):
        result = await _flow(hass, entry.entry_id).async_step_search(
            {"search_query": "buglose"}
        )

    assert result["step_id"] == "club"
    assert result["description_placeholders"] == {"team_name": "Basket Landes"}


@pytest.mark.parametrize(
    ("outcome", "key"),
    [
        ([], "no_clubs_found"),
        (FFBBConnectionError("down"), "cannot_connect"),
        (FFBBApiError("boom"), "unknown"),
    ],
)
async def test_club_search_problems_keep_the_search_form(hass, outcome, key):
    entry = _make_entry(hass)
    mock = (
        AsyncMock(side_effect=outcome)
        if isinstance(outcome, Exception)
        else AsyncMock(return_value=outcome)
    )

    with patch(f"{CLIENT}.search_clubs", mock):
        result = await _flow(hass, entry.entry_id).async_step_search(
            {"search_query": "buglose"}
        )

    assert result["step_id"] == "search"
    assert result["errors"] == {"base": key}


# --- club and team ----------------------------------------------------------


async def test_choosing_a_club_lists_its_teams(hass):
    entry = _make_entry(hass)
    flow = _flow(hass, entry.entry_id)
    flow._clubs = [CLUB]

    with patch(f"{CLIENT}.get_club_engagements", AsyncMock(return_value=[ENGAGEMENT])):
        result = await flow.async_step_club({"club_id": "8459"})

    assert result["step_id"] == "team"
    assert result["description_placeholders"] == {"team_name": "Basket Landes"}


async def test_choosing_an_unknown_club_aborts(hass):
    entry = _make_entry(hass)
    flow = _flow(hass, entry.entry_id)
    flow._clubs = [CLUB]

    result = await flow.async_step_club({"club_id": "999"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "club_not_found"


@pytest.mark.parametrize(
    ("outcome", "key"),
    [
        ([{**ENGAGEMENT, "idPoule": None}], "no_teams_found"),
        ([], "no_teams_found"),
        (FFBBConnectionError("down"), "cannot_connect"),
        (FFBBApiError("boom"), "unknown"),
    ],
)
async def test_club_team_problems_keep_the_club_form(hass, outcome, key):
    entry = _make_entry(hass)
    flow = _flow(hass, entry.entry_id)
    flow._clubs = [CLUB]
    mock = (
        AsyncMock(side_effect=outcome)
        if isinstance(outcome, Exception)
        else AsyncMock(return_value=outcome)
    )

    with patch(f"{CLIENT}.get_club_engagements", mock):
        result = await flow.async_step_club({"club_id": "8459"})

    assert result["step_id"] == "club"
    assert result["errors"] == {"base": key}


async def test_choosing_a_team_switches_the_entry_over(hass):
    entry = _make_entry(hass)
    flow = _flow(hass, entry.entry_id)
    flow._selected_club = CLUB
    flow._engagements = [ENGAGEMENT]

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await flow.async_step_team({"engagement_id": "200000005374157"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.data) == NEW_DATA
    assert entry.unique_id == "200000005374157"
    reload.assert_called_once_with(entry.entry_id)


async def test_a_team_already_tracked_elsewhere_is_refused(hass):
    entry = _make_entry(hass)
    other = _make_entry(hass, engagement_id="200000005374157")
    before = dict(entry.data)
    flow = _flow(hass, entry.entry_id)
    flow._selected_club = CLUB
    flow._engagements = [ENGAGEMENT]

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await flow.async_step_team({"engagement_id": "200000005374157"})

    assert result["step_id"] == "team"
    assert result["errors"] == {"base": "already_configured"}
    assert dict(entry.data) == before
    assert other.unique_id == "200000005374157"
    reload.assert_not_called()


async def test_the_entry_vanishing_mid_flow_aborts_cleanly(hass):
    entry = _make_entry(hass)
    flow = _flow(hass, entry.entry_id)
    flow._selected_club = CLUB
    flow._engagements = [ENGAGEMENT]
    await hass.config_entries.async_remove(entry.entry_id)

    result = await flow.async_step_team({"engagement_id": "200000005374157"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_found"


async def test_the_teams_device_follows_it_to_the_new_ids(hass):
    """The device is kept (with its area and entities) and pointed at the new
    engagement, instead of being removed and recreated."""
    entry = _make_entry(hass)
    registry = dr.async_get(hass)
    old = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "engagement-123")},
        name="Old team",
    )
    flow = _flow(hass, entry.entry_id)
    flow._selected_club = CLUB
    flow._engagements = [ENGAGEMENT]

    with patch.object(hass.config_entries, "async_schedule_reload"):
        await flow.async_step_team({"engagement_id": "200000005374157"})

    moved = registry.async_get(old.id)
    assert moved is not None
    assert moved.identifiers == {(DOMAIN, "200000005374157")}


# --- end to end through Home Assistant's own repairs manager ----------------


async def test_the_whole_repair_works_through_home_assistants_repairs_manager(hass):
    """What the user actually goes through: open the repair, confirm, search,
    done -- driven by the manager that decides what the dialog shows.

    The previous implementation passed every direct-call test while showing
    the user nothing at all, so this is the test that matters.
    """
    entry = _make_entry(hass)
    # As in a real instance the integration is running: that is what registers
    # its repairs platform with Home Assistant (otherwise the manager falls
    # back to its own confirm-only flow and never uses ours).
    with patch(
        "custom_components.ffbb_tracker.FFBBClient.get_poule_data",
        AsyncMock(return_value=MINIMAL_POULE_PAYLOAD),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert await async_setup_component(hass, "repairs", {})
    issue_id = "season_rollover_engagement-123"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="season_rollover",
        translation_placeholders={"team_name": "Basket Landes"},
        data={"entry_id": entry.entry_id},
    )
    manager = repairs_flow_manager(hass)
    assert manager is not None

    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {"team_name": "Basket Landes"}
    # The flow is the one in progress: the user is looking at it.
    assert [flow["flow_id"] for flow in manager.async_progress()] == [result["flow_id"]]

    result = await manager.async_configure(result["flow_id"], {})
    assert result["step_id"] == "search"

    with (
        patch(f"{CLIENT}.get_engagement", AsyncMock(return_value=ENGAGEMENT)),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await manager.async_configure(
            result["flow_id"], {"search_query": TEAM_URL}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.data) == NEW_DATA
    assert entry.unique_id == "200000005374157"
    reload.assert_called_once_with(entry.entry_id)
    # Home Assistant itself closes the issue when the flow finishes.
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert manager.async_progress() == []
