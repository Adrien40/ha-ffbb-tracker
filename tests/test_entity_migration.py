"""Tests for what happens to a team's entities when its entry is switched.

Reconfiguring a team (or fixing a season rollover) points the existing config
entry at another engagement. Entities and the device are keyed by engagement
ID, so this used to remove the device and recreate every entity: all entity
IDs changed whenever the team or competition name did (the usual case at a
season rollover), and anything the user customized was lost, while the README
claimed the opposite. These tests pin the real behaviour: the existing
registry entries follow the team.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DOMAIN,
    FRESH_RANK_EVOLUTION,
)
from custom_components.ffbb_tracker.repairs import SeasonRolloverRepairFlow
from custom_components.ffbb_tracker.team_picker import migrate_entities

from .test_init import MINIMAL_POULE_PAYLOAD

OLD_ID = "100"
NEW_ID = "200"
TEAM = "UJSBP U13M"
COMPETITION = "Départementale U13"
ENTITY_COUNT = 18


@pytest.fixture
def api():
    """Serve poule data so the integration can run (and reload)."""
    with patch(
        "custom_components.ffbb_tracker.FFBBClient.get_poule_data",
        AsyncMock(return_value=MINIMAL_POULE_PAYLOAD),
    ):
        yield


def _engagement(team: str, competition: str) -> dict:
    return {
        "id": NEW_ID,
        "nom": team,
        "idCompetition": {"nom": competition},
        "idPoule": {"id": "poule-2"},
        "idOrganisme": {"id": "8459"},
    }


async def _start(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=OLD_ID,
        data={
            CONF_ENGAGEMENT_ID: OLD_ID,
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: TEAM,
            CONF_COMPETITION_NAME: COMPETITION,
            CONF_ORGANISME_ID: "8459",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entities(
    hass: HomeAssistant, entry: MockConfigEntry
) -> dict[str, er.RegistryEntry]:
    """The entry's registry entries, by entity key (the part after the team ID)."""
    registry = er.async_get(hass)
    return {
        e.unique_id.split("_", 1)[1]: e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry, engagement: dict):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    with patch(
        "custom_components.ffbb_tracker.config_flow.FFBBClient.get_engagement",
        AsyncMock(return_value=engagement),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"search_query": engagement["id"]}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()


# --- the whole thing, as the user does it -----------------------------------


@pytest.mark.parametrize(
    ("team", "competition"),
    [
        pytest.param(TEAM, COMPETITION, id="same-names"),
        pytest.param(TEAM, "Départementale U13 - Division 1", id="competition-renamed"),
        pytest.param("UJSBP U14M", COMPETITION, id="team-renamed"),
        pytest.param("UJSBP U14M", "Régionale U14", id="both-renamed"),
    ],
)
async def test_reconfiguring_keeps_every_entity_whatever_the_new_names(
    hass, api, team, competition
):
    """Measured before the fix: with the same names 17 of 18 entity IDs
    survived, with a renamed team or competition none did, and the user's own
    customization was lost every time."""
    entry = await _start(hass)
    registry = er.async_get(hass)
    devices = dr.async_get(hass)

    # What a user does by hand: a custom entity ID and name, and an area.
    custom = _entities(hass, entry)["next_match_date"]
    registry.async_update_entity(
        custom.entity_id,
        name="Mon prochain match",
        new_entity_id="sensor.mon_prochain_match_perso",
    )
    device = next(iter(dr.async_entries_for_config_entry(devices, entry.entry_id)))
    area = ar.async_get(hass).async_create("Salle")
    devices.async_update_device(device.id, area_id=area.id)
    await hass.async_block_till_done()
    before = _entities(hass, entry)
    assert len(before) == ENTITY_COUNT

    await _reconfigure(hass, entry, _engagement(team, competition))

    after = _entities(hass, entry)
    assert set(after) == set(before)
    for key, was in before.items():
        now = after[key]
        assert now.entity_id == was.entity_id, key
        assert now.id == was.id, f"{key} was recreated"
        assert now.unique_id == f"{NEW_ID}_{key}"
        assert hass.states.get(now.entity_id) is not None, key
    # The customization survived.
    assert after["next_match_date"].entity_id == "sensor.mon_prochain_match_perso"
    assert after["next_match_date"].name == "Mon prochain match"
    # One device, the same one, now following the new team, with its area.
    moved = dr.async_entries_for_config_entry(devices, entry.entry_id)
    assert [d.id for d in moved] == [device.id]
    assert moved[0].identifiers == {(DOMAIN, NEW_ID)}
    assert moved[0].area_id == area.id
    assert moved[0].name == f"{team} - {competition}"
    assert entry.unique_id == NEW_ID


async def test_the_season_rollover_repair_keeps_the_entities_too(hass, api):
    entry = await _start(hass)
    before = _entities(hass, entry)
    flow = SeasonRolloverRepairFlow(entry.entry_id)
    flow.hass = hass

    with (
        patch(
            "custom_components.ffbb_tracker.repairs.FFBBClient.get_engagement",
            AsyncMock(return_value=_engagement("UJSBP U14M", "Régionale U14")),
        ),
        patch.object(hass.config_entries, "async_schedule_reload"),
    ):
        result = await flow.async_step_search({"search_query": NEW_ID})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    after = _entities(hass, entry)
    assert {k: (e.entity_id, e.id) for k, e in after.items()} == {
        k: (e.entity_id, e.id) for k, e in before.items()
    }
    assert all(e.unique_id == f"{NEW_ID}_{k}" for k, e in after.items())


# --- the rank evolution starts over for the new team ------------------------


def _give_rank_history(hass: HomeAssistant, entity_id: str) -> None:
    """Make the running sensor hold positions, as after a few matchdays."""
    sensor = hass.data["sensor"].get_entity(entity_id)
    sensor._current_position = 1
    sensor._previous_position = 4


async def test_the_rank_evolution_starts_over_for_the_new_team(hass, api):
    """Its restored positions belong to the old team's pool: carried over, the
    first standings of the new pool would show a bogus climb or fall."""
    entry = await _start(hass)
    entity_id = _entities(hass, entry)["rank_evolution"].entity_id
    _give_rank_history(hass, entity_id)

    await _reconfigure(hass, entry, _engagement(TEAM, "Régionale U14"))

    assert hass.states.get(entity_id).state == "-"
    assert hass.data[DOMAIN][FRESH_RANK_EVOLUTION] == set()  # consumed


async def test_a_plain_reload_still_restores_the_rank_evolution(hass, api):
    """Control: the reset is for a team switch only -- an ordinary reload (or
    restart) must keep restoring the evolution."""
    entry = await _start(hass)
    entity_id = _entities(hass, entry)["rank_evolution"].entity_id
    _give_rank_history(hass, entity_id)
    assert hass.states.get(entity_id).state == "-"  # not written yet

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "+3"


# --- migrate_entities on its own: the awkward cases -------------------------


def _bare_entry(hass: HomeAssistant, engagement_id: str = OLD_ID) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=engagement_id,
        data={CONF_ENGAGEMENT_ID: engagement_id},
    )
    entry.add_to_hass(hass)
    return entry


def _add_entity(hass, entry, unique_id: str, device=None) -> er.RegistryEntry:
    return er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        config_entry=entry,
        device_id=device.id if device else None,
        suggested_object_id=unique_id,
    )


def _add_device(hass, entry, *identifiers: tuple[str, str]) -> dr.DeviceEntry:
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=set(identifiers),
        name="device",
    )


async def test_switching_to_the_same_team_changes_nothing(hass):
    entry = _bare_entry(hass)
    device = _add_device(hass, entry, (DOMAIN, OLD_ID))
    entity = _add_entity(hass, entry, f"{OLD_ID}_rank", device)

    migrate_entities(hass, entry, OLD_ID)

    assert er.async_get(hass).async_get(entity.entity_id).unique_id == f"{OLD_ID}_rank"
    assert dr.async_get(hass).async_get(device.id).identifiers == {(DOMAIN, OLD_ID)}
    assert FRESH_RANK_EVOLUTION not in hass.data.get(DOMAIN, {})


async def test_only_the_teams_entities_are_moved(hass):
    entry = _bare_entry(hass)
    other = _bare_entry(hass, "300")
    ours = _add_entity(hass, entry, f"{OLD_ID}_rank")
    foreign = _add_entity(hass, entry, "something_else")
    theirs = _add_entity(hass, other, "300_rank")

    migrate_entities(hass, entry, NEW_ID)

    registry = er.async_get(hass)
    assert registry.async_get(ours.entity_id).unique_id == f"{NEW_ID}_rank"
    assert registry.async_get(foreign.entity_id).unique_id == "something_else"
    assert registry.async_get(theirs.entity_id).unique_id == "300_rank"


async def test_only_the_rank_evolution_is_flagged_to_start_over(hass):
    entry = _bare_entry(hass)
    _add_entity(hass, entry, f"{OLD_ID}_rank")
    _add_entity(hass, entry, f"{OLD_ID}_rank_evolution")

    migrate_entities(hass, entry, NEW_ID)

    assert hass.data[DOMAIN][FRESH_RANK_EVOLUTION] == {f"{NEW_ID}_rank_evolution"}


async def test_an_entity_whose_new_id_is_taken_is_left_and_the_rest_still_moves(
    hass, caplog
):
    entry = _bare_entry(hass)
    squatter = _bare_entry(hass, "999")
    blocked = _add_entity(hass, entry, f"{OLD_ID}_rank")
    movable = _add_entity(hass, entry, f"{OLD_ID}_form")
    _add_entity(hass, squatter, f"{NEW_ID}_rank")

    with caplog.at_level(logging.WARNING):
        migrate_entities(hass, entry, NEW_ID)

    registry = er.async_get(hass)
    assert registry.async_get(blocked.entity_id).unique_id == f"{OLD_ID}_rank"
    assert registry.async_get(movable.entity_id).unique_id == f"{NEW_ID}_form"
    assert blocked.entity_id in caplog.text


async def test_nothing_is_moved_onto_a_device_that_already_has_the_new_id(hass):
    """Home Assistant doesn't refuse two devices with the same identifiers: it
    lets them coexist, and entities can end up on the wrong one. Measured, so
    the check has to be ours."""
    entry = _bare_entry(hass)
    squatter = _bare_entry(hass, "999")
    mine = _add_device(hass, entry, (DOMAIN, OLD_ID))
    taken = _add_device(hass, squatter, (DOMAIN, NEW_ID))
    entity = _add_entity(hass, entry, f"{OLD_ID}_rank", mine)

    migrate_entities(hass, entry, NEW_ID)

    devices = dr.async_get(hass)
    assert devices.async_get(mine.id) is None  # recreated, not moved
    assert devices.async_get(taken.id).identifiers == {(DOMAIN, NEW_ID)}
    owners = [
        d
        for d in dr.async_entries_for_config_entry(devices, squatter.entry_id)
        if (DOMAIN, NEW_ID) in d.identifiers
    ]
    assert [d.id for d in owners] == [taken.id]
    # Not moved either (its device is gone, so it goes with it).
    entity_after = er.async_get(hass).async_get(entity.entity_id)
    assert entity_after is None or entity_after.unique_id == f"{OLD_ID}_rank"


async def test_devices_that_belong_to_no_team_are_removed(hass):
    entry = _bare_entry(hass)
    mine = _add_device(hass, entry, (DOMAIN, OLD_ID))
    stray = _add_device(hass, entry, (DOMAIN, "leftover"))

    migrate_entities(hass, entry, NEW_ID)

    devices = dr.async_get(hass)
    assert devices.async_get(mine.id).identifiers == {(DOMAIN, NEW_ID)}
    assert devices.async_get(stray.id) is None


async def test_an_entry_without_a_stored_engagement_is_left_alone(hass):
    """A malformed entry must not crash the flow that is trying to repair it."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    migrate_entities(hass, entry, NEW_ID)
