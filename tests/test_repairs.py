"""Tests for repairs.py -- season_rollover and phase_change fix flows.

Both `season_rollover` and `phase_change` open a menu offering to follow the
team (proposing same-name club candidates first for phase_change, or searching
inside the repair dialog) or ignore the alert, then switch the existing config
entry over to the team's new IDs while keeping its entities and device.
The end-to-end tests drive the flows through Home Assistant's own repairs
manager, which is what decides what the user sees.
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
    PhaseChangeRepairFlow,
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


# --- init & follow ----------------------------------------------------------


async def test_init_closes_stale_issue_when_entry_was_removed(hass):
    """Opening an orphan repair whose entry was removed closes it cleanly."""
    result = await _flow(hass, "does-not-exist").async_step_init({"issue_id": "x"})

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_follow_goes_straight_to_search_with_the_team_name(hass):
    """Choosing 'follow' opens the search step directly with {team_name} filled
    in, without forwarding the menu payload as a submitted search query."""
    entry = _make_entry(hass)
    hass.config_entries.flow.async_init = AsyncMock()

    result = await _flow(hass, entry.entry_id).async_step_follow({"issue_id": "x"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "search"
    assert result["errors"] == {}
    assert result["description_placeholders"] == {"team_name": "Basket Landes"}
    hass.config_entries.flow.async_init.assert_not_awaited()


async def test_follow_with_removed_entry_closes_cleanly(hass):
    """If the integration entry was deleted before the user clicked 'follow',
    there is nothing left to fix: the repair just closes."""
    result = await _flow(hass, "does-not-exist").async_step_follow()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_placeholders_are_empty_for_a_removed_entry(hass):
    result = await _flow(hass, "does-not-exist").async_step_search()

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


async def test_a_number_that_is_no_team_falls_back_to_the_club_search(hass):
    entry = _make_entry(hass)

    with (
        patch(
            f"{CLIENT}.get_engagement", AsyncMock(side_effect=FFBBNotFoundError("x"))
        ),
        patch(f"{CLIENT}.search_clubs", AsyncMock(return_value=[CLUB])) as search,
    ):
        result = await _flow(hass, entry.entry_id).async_step_search(
            {"search_query": "40"}
        )

    assert result["step_id"] == "club"
    assert search.call_args.args[0] == "40"


@pytest.mark.parametrize(
    ("club_search", "error"),
    [
        ({"return_value": [CLUB] * 101}, "too_many_clubs"),
        ({"side_effect": FFBBConnectionError("down")}, "cannot_connect"),
        ({"side_effect": FFBBApiError("HTTP 500")}, "unknown"),
    ],
)
async def test_a_failing_fallback_club_search_reports_its_own_error(
    hass, club_search, error
):
    entry = _make_entry(hass)

    with (
        patch(
            f"{CLIENT}.get_engagement", AsyncMock(side_effect=FFBBNotFoundError("x"))
        ),
        patch(f"{CLIENT}.search_clubs", AsyncMock(**club_search)),
    ):
        result = await _flow(hass, entry.entry_id).async_step_search(
            {"search_query": "40"}
        )

    assert result["step_id"] == "search"
    assert result["errors"] == {"base": error}


async def test_an_unknown_number_without_clubs_keeps_the_team_error(hass):
    entry = _make_entry(hass)

    with (
        patch(
            f"{CLIENT}.get_engagement", AsyncMock(side_effect=FFBBNotFoundError("x"))
        ),
        patch(f"{CLIENT}.search_clubs", AsyncMock(return_value=[])),
    ):
        result = await _flow(hass, entry.entry_id).async_step_search(
            {"search_query": "40"}
        )

    assert result["step_id"] == "search"
    assert result["errors"] == {"base": "invalid_engagement"}


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


async def test_a_team_url_already_tracked_elsewhere_is_refused(hass):
    """Same refusal when the team is typed in (URL or ID) rather than picked."""
    entry = _make_entry(hass)
    other = _make_entry(hass, engagement_id="200000005374157")
    before = dict(entry.data)

    with (
        patch(f"{CLIENT}.get_engagement", AsyncMock(return_value=ENGAGEMENT)),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await _flow(hass, entry.entry_id).async_step_search(
            {"search_query": TEAM_URL}
        )

    assert result["step_id"] == "search"
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
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["follow", "ignore"]
    # The flow is the one in progress: the user is looking at it.
    assert [flow["flow_id"] for flow in manager.async_progress()] == [result["flow_id"]]

    result = await manager.async_configure(
        result["flow_id"], {"next_step_id": "follow"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "search"
    assert result["description_placeholders"] == {"team_name": "Basket Landes"}

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


# --- phase_change: follow the team to its next phase --------------------------

CANDIDATE = {
    "id": "200000005374157",
    "nom": "Basket Landes",
    "idCompetition": {"nom": "Excellence Régionale Phase 2"},
    "idPoule": {"id": "200000003060099", "nom": "Phase 2 Poule A"},
    "idOrganisme": {"id": "org-1"},
}
# What the club also lists: the team as already tracked, and another team.
CURRENT = {
    "id": "engagement-123",
    "nom": "Basket Landes",
    "idCompetition": {"nom": "Excellence Régionale"},
    "idPoule": {"id": "poule-1", "nom": "Poule A"},
}
OTHER_TEAM = {
    "id": "200000009999999",
    "nom": "Basket Landes 2",
    "idCompetition": {"nom": "Excellence Régionale"},
    "idPoule": {"id": "200000003060055", "nom": "Poule B"},
}


def _phase_flow(hass, entry_id: str) -> PhaseChangeRepairFlow:
    flow = PhaseChangeRepairFlow(entry_id)
    flow.hass = hass
    return flow


async def test_phase_change_issues_get_the_phase_change_flow(hass):
    flow = await async_create_fix_flow(
        hass, "phase_change_engagement-123", {"entry_id": "abc123"}
    )

    assert isinstance(flow, PhaseChangeRepairFlow)
    assert flow._entry_id == "abc123"


async def test_season_rollover_issues_keep_the_plain_flow(hass):
    flow = await async_create_fix_flow(hass, "season_rollover_x", {"entry_id": "a"})

    assert type(flow) is SeasonRolloverRepairFlow


async def test_the_new_team_of_the_club_is_proposed_first(hass):
    """Same name, other pool: proposed. The tracked team and other teams: not."""
    entry = _make_entry(hass)
    flow = _phase_flow(hass, entry.entry_id)

    with patch(
        f"{CLIENT}.get_club_engagements",
        AsyncMock(return_value=[CURRENT, CANDIDATE, OTHER_TEAM]),
    ):
        result = await flow.async_step_follow({"issue_id": "x"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "candidate"
    assert result["description_placeholders"] == {"team_name": "Basket Landes"}
    assert flow._candidates == [CANDIDATE]
    assert result["data_schema"]({}) == {
        "engagement_id": "200000005374157",
        "search_other": False,
    }


async def test_same_team_under_a_new_pool_is_proposed(hass):
    """The FFBB may keep the engagement ID and only change the pool."""
    entry = _make_entry(hass)
    moved = {**CURRENT, "idPoule": {"id": "poule-2", "nom": "Phase 2"}}

    with patch(f"{CLIENT}.get_club_engagements", AsyncMock(return_value=[moved])):
        result = await _phase_flow(hass, entry.entry_id).async_step_follow()

    assert result["step_id"] == "candidate"


@pytest.mark.parametrize(
    "outcome",
    [[CURRENT, OTHER_TEAM], FFBBConnectionError("down"), FFBBApiError("boom")],
)
async def test_without_a_candidate_the_search_comes_next(hass, outcome):
    entry = _make_entry(hass)
    mock = (
        AsyncMock(side_effect=outcome)
        if isinstance(outcome, Exception)
        else AsyncMock(return_value=outcome)
    )

    with patch(f"{CLIENT}.get_club_engagements", mock):
        result = await _phase_flow(hass, entry.entry_id).async_step_follow()

    assert result["step_id"] == "search"


async def test_an_entry_without_a_club_is_not_looked_up(hass):
    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_ORGANISME_ID: ""}
    )

    with patch(f"{CLIENT}.get_club_engagements", AsyncMock()) as lookup:
        result = await _phase_flow(hass, entry.entry_id).async_step_follow()

    lookup.assert_not_awaited()
    assert result["step_id"] == "search"


async def test_a_removed_entry_closes_the_phase_change_repair(hass):
    result = await _phase_flow(hass, "does-not-exist").async_step_follow()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_choosing_the_proposed_team_switches_the_entry_over(hass):
    entry = _make_entry(hass)
    flow = _phase_flow(hass, entry.entry_id)
    flow._candidates = [CANDIDATE]
    flow._organisme_id = "org-1"

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await flow.async_step_candidate(
            {"engagement_id": "200000005374157", "search_other": False}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_ENGAGEMENT_ID] == "200000005374157"
    assert entry.data[CONF_POULE_ID] == "200000003060099"
    assert entry.data[CONF_ORGANISME_ID] == "org-1"
    assert entry.title == "Basket Landes - Excellence Régionale Phase 2"
    assert entry.unique_id == "200000005374157"
    reload.assert_called_once_with(entry.entry_id)


async def test_asking_for_another_team_goes_to_the_search(hass):
    entry = _make_entry(hass)
    flow = _phase_flow(hass, entry.entry_id)
    flow._candidates = [CANDIDATE]

    result = await flow.async_step_candidate(
        {"engagement_id": "200000005374157", "search_other": True}
    )

    assert result["step_id"] == "search"
    assert dict(entry.data)[CONF_ENGAGEMENT_ID] == "engagement-123"


async def test_a_proposed_team_already_tracked_elsewhere_is_refused(hass):
    entry = _make_entry(hass)
    other = MockConfigEntry(domain=DOMAIN, unique_id="200000005374157", data={})
    other.add_to_hass(hass)
    flow = _phase_flow(hass, entry.entry_id)
    flow._candidates = [CANDIDATE]
    flow._organisme_id = "org-1"

    result = await flow.async_step_candidate(
        {"engagement_id": "200000005374157", "search_other": False}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "candidate"
    assert result["errors"] == {"base": "already_configured"}
    assert entry.data[CONF_ENGAGEMENT_ID] == "engagement-123"


async def test_a_team_outside_the_proposed_list_is_ignored(hass):
    entry = _make_entry(hass)
    flow = _phase_flow(hass, entry.entry_id)
    flow._candidates = [CANDIDATE]

    result = await flow.async_step_candidate(
        {"engagement_id": "somebody-else", "search_other": False}
    )

    assert result["step_id"] == "candidate"
    assert result["errors"] == {}
    assert entry.data[CONF_ENGAGEMENT_ID] == "engagement-123"


async def test_the_phase_change_repair_works_through_home_assistants_manager(hass):
    """The dialog the user sees: proposal, one click, entry switched over."""
    entry = _make_entry(hass)
    # The integration must be running: that is what registers its repairs
    # platform (see the season rollover test above).
    with patch(
        "custom_components.ffbb_tracker.FFBBClient.get_poule_data",
        AsyncMock(return_value=MINIMAL_POULE_PAYLOAD),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert await async_setup_component(hass, "repairs", {})
    issue_id = f"phase_change_{entry.unique_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="phase_change",
        translation_placeholders={"team_name": "Basket Landes", "days": "3"},
        data={"entry_id": entry.entry_id},
    )
    manager = repairs_flow_manager(hass)
    assert manager is not None

    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    with patch(
        f"{CLIENT}.get_club_engagements",
        AsyncMock(return_value=[CURRENT, CANDIDATE]),
    ):
        result = await manager.async_configure(
            result["flow_id"], {"next_step_id": "follow"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "candidate"

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await manager.async_configure(
            result["flow_id"],
            {"engagement_id": "200000005374157", "search_other": False},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_ENGAGEMENT_ID] == "200000005374157"
    reload.assert_called_once_with(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


# --- ignoring the alert ------------------------------------------------------


async def _running_manager(hass, entry):
    """Start the integration so its repairs platform is registered."""
    with patch(
        "custom_components.ffbb_tracker.FFBBClient.get_poule_data",
        AsyncMock(return_value=MINIMAL_POULE_PAYLOAD),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    return manager


def _raise_issue(hass, entry, issue_id, key):
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=key,
        translation_placeholders={"team_name": "Basket Landes", "days": "3"},
        data={"entry_id": entry.entry_id},
    )


@pytest.mark.parametrize(
    ("prefix", "key"),
    [("phase_change", "phase_change"), ("season_rollover", "season_rollover")],
)
async def test_the_alert_can_be_ignored_from_the_dialog(hass, prefix, key):
    """Ignoring marks the issue as ignored and keeps it: finishing the flow
    normally would make Home Assistant delete it, and it would come back."""
    entry = _make_entry(hass)
    manager = await _running_manager(hass, entry)
    issue_id = f"{prefix}_{entry.unique_id}"
    _raise_issue(hass, entry, issue_id, key)

    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert result["description_placeholders"] == {"team_name": "Basket Landes"}

    result = await manager.async_configure(
        result["flow_id"], {"next_step_id": "ignore"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "ignored"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None
    # Nothing was switched over.
    assert entry.data[CONF_ENGAGEMENT_ID] == "engagement-123"


async def test_an_ignored_alert_stays_ignored_when_it_is_raised_again(hass):
    """The coordinator raises the issue again on every refresh."""
    entry = _make_entry(hass)
    manager = await _running_manager(hass, entry)
    issue_id = f"phase_change_{entry.unique_id}"
    _raise_issue(hass, entry, issue_id, "phase_change")
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    await manager.async_configure(result["flow_id"], {"next_step_id": "ignore"})

    _raise_issue(hass, entry, issue_id, "phase_change")

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_ignoring_when_the_issue_is_already_gone_does_not_crash(hass):
    entry = _make_entry(hass)
    flow = _flow(hass, entry.entry_id)
    flow.issue_id = "season_rollover_gone"

    result = await flow.async_step_ignore()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "ignored"


# --- texts -------------------------------------------------------------------

_TEXT_FILES = [
    "strings.json",
    "translations/en.json",
    "translations/fr.json",
]


def _issue_texts(name):
    import json
    from pathlib import Path

    path = Path(__file__).parent.parent / "custom_components" / "ffbb_tracker" / name
    return json.loads(path.read_text(encoding="utf-8"))["issues"]


@pytest.mark.parametrize("name", _TEXT_FILES)
@pytest.mark.parametrize(
    "key", ["phase_change", "season_rollover", "token_refresh_failing"]
)
def test_the_alert_title_names_the_team(name, key):
    """Several teams can be tracked: the title alone must tell which one."""
    assert "{team_name}" in _issue_texts(name)[key]["title"]


@pytest.mark.parametrize("name", _TEXT_FILES)
@pytest.mark.parametrize("key", ["phase_change", "season_rollover"])
def test_the_menu_and_the_ignored_message_are_translated(name, key):
    fix_flow = _issue_texts(name)[key]["fix_flow"]
    assert set(fix_flow["step"]["init"]["menu_options"]) == {"follow", "ignore"}
    assert fix_flow["abort"]["ignored"]
