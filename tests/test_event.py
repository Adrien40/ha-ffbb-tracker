"""Tests for event.py.

Both event entities fire exactly once per real transition, detected by
comparing the previous and current coordinator data inside
`_handle_coordinator_update()`. The risk worth guarding against isn't the
entity wiring (unique_id/device_info, copied from the binary_sensor
pattern) -- it's the cold-start trap: since the coordinator has already
completed its first refresh by the time these entities are constructed
(`async_config_entry_first_refresh()` runs before platforms are set up),
a naive "previous value defaults to None" baseline would treat the
already-known last match / standing as brand new on the very next
refresh after a Home Assistant restart, firing a spurious event. These
tests pin down that the baseline is seeded from the data already present
at construction time, and that repeated refreshes with unchanged data
never re-fire.
"""

from __future__ import annotations

from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
    mock_restore_cache_with_extra_data,
)

from custom_components.ffbb_tracker.const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DOMAIN,
)
from custom_components.ffbb_tracker.coordinator import (
    FFBBDataUpdateCoordinator,
    FFBBTeamData,
    MatchDetails,
    TeamStanding,
)
from custom_components.ffbb_tracker.event import (
    FFBBMatchFinishedEvent,
    FFBBRankChangedEvent,
)


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


def _make_match(**overrides) -> MatchDetails:
    """Build a MatchDetails with sane defaults, overridable per test."""
    defaults: dict = {
        "match_id": "match-1",
        "match_number": "1",
        "round_number": "3",
        "match_date": None,
        "is_home": True,
        "team_name": "Basket Landes",
        "opponent_name": "US Mont-de-Marsan",
        "opponent_club_id": "org-2",
        "is_played": True,
        "team_score": 65,
        "opponent_score": 58,
        "result": "win",
        "gym_name": "Gymnase Andre Chavanne",
        "gym_address": "1 rue du Stade",
        "gym_postal_code": "40000",
        "gym_city": "Mont-de-Marsan",
        "team_logo_url": None,
        "opponent_logo_url": None,
        "raw": {},
    }
    defaults.update(overrides)
    return MatchDetails(**defaults)


def _make_standing(**overrides) -> TeamStanding:
    """Build a TeamStanding with sane defaults, overridable per test."""
    defaults: dict = {
        "position": 4,
        "points": 20,
        "played": 10,
        "won": 8,
        "lost": 2,
        "raw": {},
    }
    defaults.update(overrides)
    return TeamStanding(**defaults)


def _make_team_data(**overrides) -> FFBBTeamData:
    """Build an FFBBTeamData with sane defaults, overridable per test."""
    defaults: dict = {
        "engagement_id": "engagement-123",
        "poule_id": "poule-1",
        "team_name": "Basket Landes",
        "competition_name": "Excellence Régionale",
        "poule_name": "Excellence Régionale - Poule A",
        "next_match": None,
        "last_match": None,
        "team_standing": None,
        "standings": [],
        "fixtures": [],
    }
    defaults.update(overrides)
    return FFBBTeamData(**defaults)


# ---------------------------------------------------------------------------
# Entity wiring (unique_id / device_info), same pattern as test_button.py
# ---------------------------------------------------------------------------


def test_match_finished_unique_id_and_device_info(hass):
    """The match-finished event must have a stable, namespaced unique_id."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data()
    entity = FFBBMatchFinishedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    assert entity.unique_id == "engagement-123_match_finished"
    assert entity.device_info["identifiers"] == {(DOMAIN, "engagement-123")}


def test_rank_changed_unique_id_and_device_info(hass):
    """The rank-changed event must have a stable, namespaced unique_id."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data()
    entity = FFBBRankChangedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    assert entity.unique_id == "engagement-123_rank_changed"
    assert entity.device_info["identifiers"] == {(DOMAIN, "engagement-123")}


# ---------------------------------------------------------------------------
# event.match_finished
# ---------------------------------------------------------------------------


def test_match_finished_does_not_fire_on_cold_start(hass):
    """A refresh right after setup, with the same last_match already known
    at construction time, must NOT fire -- this is the cold-start trap:
    the coordinator's first refresh already happened before the entity
    was built, so nothing has actually changed.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(last_match=_make_match(match_id="match-1"))
    entity = FFBBMatchFinishedEvent(coordinator)
    entity.async_write_ha_state = lambda: None
    assert entity.state is None

    # Simulate a subsequent refresh that fetched the exact same last_match.
    coordinator.data = _make_team_data(last_match=_make_match(match_id="match-1"))
    entity._handle_coordinator_update()

    assert entity.state is None


def test_match_finished_fires_once_when_a_new_result_appears(hass):
    """A genuinely new last_match (different match_id) must fire exactly
    once, with the result as the event_type and match details as data.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(last_match=_make_match(match_id="match-1"))
    entity = FFBBMatchFinishedEvent(coordinator)
    entity.async_write_ha_state = lambda: None
    assert entity.state is None

    coordinator.data = _make_team_data(
        last_match=_make_match(
            match_id="match-2",
            opponent_name="Stade Montois",
            team_score=70,
            opponent_score=60,
            result="win",
            round_number="7",
            gym_name="Gymnase Municipal",
            gym_city="Mont-de-Marsan",
        )
    )
    entity._handle_coordinator_update()

    assert entity.state is not None
    attrs = entity.state_attributes
    assert attrs["event_type"] == "win"
    assert attrs["opponent"] == "Stade Montois"
    assert attrs["team_score"] == 70
    assert attrs["opponent_score"] == 60
    assert attrs["point_difference"] == 10
    assert attrs["is_home"] is True
    assert attrs["round"] == "7"
    assert attrs["gym_name"] == "Gymnase Municipal"
    assert attrs["gym_city"] == "Mont-de-Marsan"


def test_match_finished_point_difference_is_negative_on_a_loss(hass):
    """A loss must carry a negative point_difference, not just an
    absolute gap -- automations/blueprints rely on the sign to tell a
    win from a loss without re-reading event_type.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(last_match=_make_match(match_id="match-1"))
    entity = FFBBMatchFinishedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    coordinator.data = _make_team_data(
        last_match=_make_match(
            match_id="match-2",
            team_score=58,
            opponent_score=65,
            result="loss",
        )
    )
    entity._handle_coordinator_update()

    assert entity.state_attributes["point_difference"] == -7


def test_match_finished_point_difference_is_none_without_a_score(hass):
    """A published result without a parsed score (rare malformed payload)
    must not crash computing the difference, and must report it as
    unknown rather than a misleading 0.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(last_match=_make_match(match_id="match-1"))
    entity = FFBBMatchFinishedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    coordinator.data = _make_team_data(
        last_match=_make_match(
            match_id="match-2",
            team_score=None,
            opponent_score=None,
            result="win",
        )
    )
    entity._handle_coordinator_update()

    assert entity.state_attributes["point_difference"] is None


def test_match_finished_does_not_refire_on_the_next_unchanged_refresh(hass):
    """Once fired for a match, further refreshes with the same last_match
    must not fire again.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(last_match=_make_match(match_id="match-1"))
    entity = FFBBMatchFinishedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    coordinator.data = _make_team_data(
        last_match=_make_match(match_id="match-2", result="loss")
    )
    entity._handle_coordinator_update()
    first_state = entity.state

    coordinator.data = _make_team_data(
        last_match=_make_match(match_id="match-2", result="loss")
    )
    entity._handle_coordinator_update()

    assert entity.state == first_state
    assert entity.state_attributes["event_type"] == "loss"


def test_match_finished_ignores_a_result_less_played_match(hass):
    """A match flagged is_played but without a computed result (malformed
    score payload) must not fire -- there is nothing meaningful to report.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(last_match=_make_match(match_id="match-1"))
    entity = FFBBMatchFinishedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    coordinator.data = _make_team_data(
        last_match=_make_match(
            match_id="match-2",
            team_score=None,
            opponent_score=None,
            result=None,
        )
    )
    entity._handle_coordinator_update()

    assert entity.state is None


def test_match_finished_handles_no_last_match_yet(hass):
    """A team with no played match yet must not raise and must not fire."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(last_match=None)
    entity = FFBBMatchFinishedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    coordinator.data = _make_team_data(last_match=None)
    entity._handle_coordinator_update()

    assert entity.state is None


# ---------------------------------------------------------------------------
# event.rank_changed
# ---------------------------------------------------------------------------


async def test_match_finished_event_restores_its_last_event_after_a_restart(hass):
    """Home Assistant restores the last event of an event entity.

    After a restart, "Fin de match" therefore still carries the figures of the
    last match it fired for (event type, opponent, score, round...), even if a
    newer result was published while Home Assistant was off: the integration
    treats that newer result as already known and does not fire again.

    This is why the blueprint reads the permanent "last match" sensors first on
    a click on "Actualiser": the restored event can be one match behind. (An
    event that never fired, on a freshly added team, restores nothing and has
    no attributes at all.)
    """
    entity = FFBBMatchFinishedEvent(_make_coordinator(hass))
    entity.hass = hass
    entity.platform = MockEntityPlatform(hass, domain="event", platform_name=DOMAIN)
    entity.entity_id = "event.basket_landes_fin_de_match"
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(entity.entity_id, "2026-01-17T21:30:00.000+00:00"),
                {
                    "last_event_type": "win",
                    "last_event_attributes": {
                        "opponent": "Old Club",
                        "team_score": 60,
                        "opponent_score": 50,
                        "round": "5",
                    },
                },
            )
        ],
    )

    await entity.async_internal_added_to_hass()

    attributes = entity.state_attributes
    assert attributes["event_type"] == "win"
    assert attributes["opponent"] == "Old Club"
    assert attributes["team_score"] == 60
    assert attributes["round"] == "5"


async def test_match_finished_event_that_never_fired_restores_nothing(hass):
    """A new team (or one whose event never fired) has no attributes at all."""
    entity = FFBBMatchFinishedEvent(_make_coordinator(hass))
    entity.hass = hass
    entity.platform = MockEntityPlatform(hass, domain="event", platform_name=DOMAIN)
    entity.entity_id = "event.basket_landes_fin_de_match"

    await entity.async_internal_added_to_hass()

    assert entity.state_attributes.get("event_type") is None
    assert "opponent" not in entity.state_attributes


def test_rank_changed_does_not_fire_on_cold_start(hass):
    """A refresh right after setup, with the same position already known
    at construction time, must NOT fire.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(team_standing=_make_standing(position=4))
    entity = FFBBRankChangedEvent(coordinator)
    entity.async_write_ha_state = lambda: None
    assert entity.state is None

    coordinator.data = _make_team_data(team_standing=_make_standing(position=4))
    entity._handle_coordinator_update()

    assert entity.state is None


def test_rank_changed_fires_up_when_position_improves(hass):
    """A numerically lower position (e.g. 4 -> 3) is an improvement and
    must fire "up" with both the old and new position as event data.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(team_standing=_make_standing(position=4))
    entity = FFBBRankChangedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    coordinator.data = _make_team_data(team_standing=_make_standing(position=3))
    entity._handle_coordinator_update()

    attrs = entity.state_attributes
    assert attrs["event_type"] == "up"
    assert attrs["old_position"] == 4
    assert attrs["new_position"] == 3


def test_rank_changed_fires_down_when_position_worsens(hass):
    """A numerically higher position (e.g. 3 -> 5) is a regression and
    must fire "down".
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(team_standing=_make_standing(position=3))
    entity = FFBBRankChangedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    coordinator.data = _make_team_data(team_standing=_make_standing(position=5))
    entity._handle_coordinator_update()

    attrs = entity.state_attributes
    assert attrs["event_type"] == "down"
    assert attrs["old_position"] == 3
    assert attrs["new_position"] == 5


def test_rank_changed_does_not_fire_when_position_is_first_published(hass):
    """Going from "no standing yet" to a first published position must not
    fire -- there is no meaningful "old" position to compare against, so
    this is a baseline, not a movement.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(team_standing=None)
    entity = FFBBRankChangedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    coordinator.data = _make_team_data(team_standing=_make_standing(position=6))
    entity._handle_coordinator_update()

    assert entity.state is None


def test_rank_changed_does_not_refire_on_the_next_unchanged_refresh(hass):
    """Once fired, further refreshes at the same position must not re-fire."""
    coordinator = _make_coordinator(hass)
    coordinator.data = _make_team_data(team_standing=_make_standing(position=4))
    entity = FFBBRankChangedEvent(coordinator)
    entity.async_write_ha_state = lambda: None

    coordinator.data = _make_team_data(team_standing=_make_standing(position=3))
    entity._handle_coordinator_update()
    first_state = entity.state

    coordinator.data = _make_team_data(team_standing=_make_standing(position=3))
    entity._handle_coordinator_update()

    assert entity.state == first_state
