"""Rendering tests for the Telegram automation blueprints.

Blueprints are YAML + Jinja, so a regression there never shows up in the
Python tests. These tests load the real blueprint files, pull out their
templates and render them with Home Assistant's own template engine, with
the instance time zone set to Europe/Paris.

The FFBB sensors expose timestamps in UTC. A blueprint that formats them
without converting to the instance's local time zone prints the wrong hour
(and sometimes the wrong day).
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import load_yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry

BLUEPRINT_DIR = (
    Path(__file__).parent.parent / "blueprints" / "automation" / "ffbb_tracker"
)

DATE_SENSOR = "sensor.next_match_date"
OPPONENT_SENSOR = "sensor.next_match_opponent"
LOCATION_SENSOR = "sensor.next_match_location"
EVENT_ENTITY = "event.match_finished"


def _load(name: str) -> dict[str, Any]:
    return load_yaml(str(BLUEPRINT_DIR / name))


def _find_action(blueprint: dict[str, Any], action: str) -> dict[str, Any]:
    return next(step for step in blueprint["actions"] if step.get("action") == action)


def _find_condition_template(blueprint: dict[str, Any]) -> str:
    return next(
        step["value_template"]
        for step in blueprint["actions"]
        if step.get("condition") == "template"
    )


async def _render(hass: HomeAssistant, template: str, variables: dict[str, Any]) -> str:
    return str(Template(template, hass).async_render(variables)).strip()


@pytest.fixture
async def paris(hass: HomeAssistant) -> HomeAssistant:
    """Run the instance in Europe/Paris (UTC+1 in January)."""
    await hass.config.async_set_time_zone("Europe/Paris")
    return hass


def _match_variables() -> dict[str, Any]:
    return {
        "team_title": "Basket Landes",
        "date_sensor": DATE_SENSOR,
        "opponent_sensor": OPPONENT_SENSOR,
        "location_sensor": LOCATION_SENSOR,
        "refresh_cmd": "/refresh_match",
    }


# --- match_notifications_telegram: kick-off time in the message -------------


async def test_notification_message_shows_local_kickoff_time(paris):
    """19:00 UTC is 20:00 in Paris -- the message must say 20h00."""
    paris.states.async_set(DATE_SENSOR, "2026-01-10T19:00:00+00:00")
    paris.states.async_set(OPPONENT_SENSOR, "US Montreal")
    paris.states.async_set(LOCATION_SENSOR, "Gymnase, Dax", {"is_home": True})
    blueprint = _load("match_notifications_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]

    rendered = await _render(paris, message, _match_variables())

    assert "20h00" in rendered
    assert "19h00" not in rendered
    assert "Samedi 10 Janvier" in rendered


async def test_notification_message_uses_local_day_across_midnight(paris):
    """23:30 UTC on the 10th is 00:30 on the 11th in Paris (a Sunday)."""
    paris.states.async_set(DATE_SENSOR, "2026-01-10T23:30:00+00:00")
    paris.states.async_set(OPPONENT_SENSOR, "US Montreal")
    paris.states.async_set(LOCATION_SENSOR, "Gymnase, Dax", {"is_home": False})
    blueprint = _load("match_notifications_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]

    rendered = await _render(paris, message, _match_variables())

    assert "Dimanche 11 Janvier" in rendered
    assert "00h30" in rendered


# --- match_notifications_telegram: day-before reminder condition ------------


@pytest.mark.parametrize(
    ("match_utc", "expected"),
    [
        # Sat 10 Jan 20:00 Paris -> it IS tomorrow when reminding on Fri 9th.
        ("2026-01-10T19:00:00+00:00", "True"),
        # Sun 11 Jan 00:30 Paris (still the 10th in UTC) -> NOT tomorrow.
        ("2026-01-10T23:30:00+00:00", "False"),
    ],
)
async def test_reminder_condition_uses_instance_time_zone(
    paris, freezer, match_utc, expected
):
    """'Is the match tomorrow?' must follow Home Assistant's time zone.

    `datetime.astimezone()` with no argument converts to the *host
    operating system's* zone, which is not necessarily the one configured
    in Home Assistant.
    """
    # Friday 9 January 2026, 19:00 in Paris == 18:00 UTC.
    freezer.move_to("2026-01-09T18:00:00+00:00")
    paris.states.async_set(DATE_SENSOR, match_utc)
    blueprint = _load("match_notifications_telegram.yaml")
    condition = _find_condition_template(blueprint)

    rendered = await _render(
        paris,
        condition,
        {"date_sensor": DATE_SENSOR, "trigger": {"id": "rappel_veille"}},
    )

    assert rendered == expected


async def test_reminder_status_counts_days_in_instance_time_zone(paris, freezer):
    """The 'Match demain !' banner must use the same local-date logic."""
    freezer.move_to("2026-01-09T18:00:00+00:00")  # Fri 9 Jan, 19:00 Paris
    paris.states.async_set(DATE_SENSOR, "2026-01-10T23:30:00+00:00")  # Sun 11, Paris
    paris.states.async_set(OPPONENT_SENSOR, "US Montreal")
    paris.states.async_set(LOCATION_SENSOR, "Gymnase, Dax", {"is_home": True})
    blueprint = _load("match_notifications_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]

    rendered = await _render(
        paris, message, {**_match_variables(), "trigger": {"id": "rappel_veille"}}
    )

    assert "Match dans 2 jours" in rendered
    assert "Match demain" not in rendered


# --- match_result_notification_telegram: match date in the message ----------


async def test_result_message_shows_local_match_time(paris):
    """The result message must show the local kick-off, not the UTC one."""
    paris.states.async_set(
        EVENT_ENTITY,
        "2026-01-10T21:30:00.000+00:00",
        {
            "event_type": "win",
            "opponent": "US Montreal",
            "team_score": 68,
            "opponent_score": 54,
            "point_difference": 14,
            "is_home": True,
            "match_date": "2026-01-10T19:00:00+00:00",
            "round": "3",
            "gym_name": "Gymnase",
            "gym_city": "Dax",
        },
    )
    blueprint = _load("match_result_notification_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]

    rendered = await _render(
        paris, message, {"team_title": "Basket Landes", "event_entity": EVENT_ENTITY}
    )

    assert "Samedi 10 Janvier à 20h00" in rendered
    assert "19h00" not in rendered


# --- match_notifications_telegram: "new match" vs "update" ------------------


def _alert_variables(old: State | None, new: State) -> dict[str, Any]:
    return {
        **_match_variables(),
        "trigger": {"id": "alerte_ffbb", "from_state": old, "to_state": new},
    }


async def _alert_message(paris, old: State | None, new: State) -> str:
    paris.states.async_set(DATE_SENSOR, "2026-10-10T13:45:00+00:00")
    paris.states.async_set(OPPONENT_SENSOR, "Biscarrosse")
    paris.states.async_set(LOCATION_SENSOR, "Salle, Biscarrosse", {"is_home": False})
    blueprint = _load("match_notifications_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]
    return await _render(paris, message, _alert_variables(old, new))


def _opponent(state: str, number: str | None = None) -> State:
    attributes = {} if number is None else {"match_number": number}
    return State(OPPONENT_SENSOR, state, attributes)


async def test_a_different_match_number_is_announced_as_a_new_match(paris):
    """Once a match is played the next one takes its place on the sensors:
    that is a new match, not a change to the previous one."""
    rendered = await _alert_message(
        paris,
        _opponent("Biaudos", "11527"),
        _opponent("Biscarrosse", "11549"),
    )

    assert "Nouveau match programmé" in rendered
    assert "Mise à jour du match" not in rendered


async def test_the_same_match_number_is_announced_as_an_update(paris):
    rendered = await _alert_message(
        paris,
        _opponent("Biscarrosse", "11549"),
        _opponent("Biscarrosse - 2", "11549"),
    )

    assert "Mise à jour du match" in rendered
    assert "Nouveau match programmé" not in rendered


@pytest.mark.parametrize("previous", ["unknown", "unavailable", ""])
async def test_a_match_appearing_from_nothing_is_a_new_match(paris, previous):
    rendered = await _alert_message(
        paris, _opponent(previous), _opponent("Biscarrosse", "11549")
    )

    assert "Nouveau match programmé" in rendered


async def test_without_match_numbers_a_change_stays_an_update(paris):
    """Older versions of the integration don't put match_number on these
    sensors: keep the previous behaviour rather than guessing."""
    rendered = await _alert_message(
        paris, _opponent("Biaudos"), _opponent("Biscarrosse")
    )

    assert "Mise à jour du match" in rendered


async def test_a_match_number_on_one_side_only_is_not_enough_to_tell(paris):
    rendered = await _alert_message(
        paris, _opponent("Biaudos"), _opponent("Biscarrosse", "11549")
    )

    assert "Mise à jour du match" in rendered


# --- source_url: lets Home Assistant offer "Re-import blueprint" ------------


@pytest.mark.parametrize(
    "filename", sorted(path.name for path in BLUEPRINT_DIR.glob("*.yaml"))
)
def test_blueprints_declare_the_url_of_their_own_file(filename):
    manifest = json.loads(
        (
            BLUEPRINT_DIR.parents[2]
            / "custom_components"
            / "ffbb_tracker"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    repo = manifest["documentation"]

    source_url = _load(filename)["blueprint"].get("source_url")

    assert source_url == (
        f"{repo}/blob/main/blueprints/automation/ffbb_tracker/{filename}"
    )


# --- match_notifications_telegram: no upcoming match -------------------------


async def test_notification_without_upcoming_match_says_not_provided(paris):
    """In an off-season, pressing "Actualiser" still sends the message.

    With no next match every sensor is unknown and has no attributes: the
    opponent and the venue must say "Non renseigné", not "Unknown" and
    "Extérieur" (a missing `is_home` is not the same as `False`).
    """
    for entity in (DATE_SENSOR, OPPONENT_SENSOR, LOCATION_SENSOR):
        paris.states.async_set(entity, "unknown", {})
    blueprint = _load("match_notifications_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]

    rendered = await _render(paris, message, _match_variables())

    assert "Adversaire :</b> Non renseigné" in rendered
    assert "Terrain :</b> Non renseigné" in rendered
    assert "Unknown" not in rendered
    assert "Extérieur" not in rendered


@pytest.mark.parametrize(
    ("is_home", "venue"), [(True, "🏠 Domicile"), (False, "🚗 Extérieur")]
)
async def test_notification_venue_follows_is_home(paris, is_home, venue):
    paris.states.async_set(DATE_SENSOR, "2026-01-10T19:00:00+00:00", {})
    paris.states.async_set(OPPONENT_SENSOR, "us montreal", {})
    paris.states.async_set(LOCATION_SENSOR, "unknown", {"is_home": is_home})
    blueprint = _load("match_notifications_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]

    rendered = await _render(paris, message, _match_variables())

    assert f"Terrain :</b> {venue}" in rendered
    assert "Adversaire :</b> Us Montreal" in rendered


# --- match_result_notification_telegram: score order -------------------------


def _finished_attributes(**overrides: Any) -> dict[str, Any]:
    return {
        "event_type": "win",
        "opponent": "US Montreal",
        "team_score": 68,
        "opponent_score": 54,
        "point_difference": 14,
        "is_home": True,
        "match_date": "2026-01-10T19:00:00+00:00",
        "round": "3",
        "gym_name": "Gymnase",
        "gym_city": "Dax",
    } | overrides


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        pytest.param({}, "68 - 54", id="home"),
        pytest.param({"is_home": False}, "54 - 68", id="away-win"),
        pytest.param(
            {
                "is_home": False,
                "event_type": "loss",
                "team_score": 50,
                "opponent_score": 61,
            },
            "61 - 50",
            id="away-loss",
        ),
        pytest.param(
            {"is_home": False, "team_score": 0, "opponent_score": 20},
            "20 - 0",
            id="away-with-a-zero",
        ),
        pytest.param({"is_home": None}, "68 - 54", id="venue-unknown"),
    ],
)
async def test_result_score_is_shown_home_first(paris, attributes, expected):
    """The score follows the official "home - away" order, whoever we are."""
    paris.states.async_set(
        EVENT_ENTITY,
        "2026-01-10T21:30:00.000+00:00",
        _finished_attributes(**attributes),
    )
    blueprint = _load("match_result_notification_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]

    rendered = await _render(
        paris, message, {"team_title": "Basket Landes", "event_entity": EVENT_ENTITY}
    )

    assert f"Score :</b> {expected}\n" in rendered + "\n"


async def test_result_without_scores_says_not_communicated(paris):
    paris.states.async_set(
        EVENT_ENTITY,
        "2026-01-10T21:30:00.000+00:00",
        {"event_type": "win", "opponent": "US Montreal", "is_home": False},
    )
    blueprint = _load("match_result_notification_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]

    rendered = await _render(
        paris, message, {"team_title": "Basket Landes", "event_entity": EVENT_ENTITY}
    )

    assert "Score :</b> Non communiqué" in rendered


# --- match_result_notification_telegram: finding the form sensor -------------


@pytest.mark.parametrize("club", ["landes", "perform"])
async def test_result_form_sensor_is_found_whatever_the_club_name(paris, club):
    """The form sensor is recognised by its `current_streak` attribute.

    Looking for "form" in the entity ID used to hijack every entity of a club
    whose name contains it ("Perform", "Informatique"...): the streak read
    "Non renseignée" and the form showed the rank instead.
    """
    entry = MockConfigEntry(domain="ffbb_tracker")
    entry.add_to_hass(paris)
    device = dr.async_get(paris).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("ffbb_tracker", club)},
        name=f"Basket {club}",
    )
    registry = er.async_get(paris)

    def add(domain: str, key: str) -> er.RegistryEntry:
        return registry.async_get_or_create(
            domain,
            "ffbb_tracker",
            f"{club}_{key}",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id=f"basket_{club}_{key}",
        )

    event = add("event", "match_finished")
    form = add("sensor", "form")
    rank = add("sensor", "rank")
    paris.states.async_set(
        event.entity_id, "2026-01-10T21:30:00.000+00:00", _finished_attributes()
    )
    paris.states.async_set(form.entity_id, "V-V-D", {"current_streak": "2V"})
    paris.states.async_set(rank.entity_id, "3", {})
    blueprint = _load("match_result_notification_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]

    rendered = await _render(
        paris, message, {"team_title": "Basket", "event_entity": event.entity_id}
    )

    assert "Série :</b> 2V (Forme : V-V-D)" in rendered


# --- match_result_notification_telegram: which entities it reads ------------


def _register_team(paris: HomeAssistant, club: str = "landes") -> dict[str, str]:
    """Create a team's device and return its entity IDs by key.

    Entities are registered in the order Home Assistant lists them for the
    device; the "last match" date sensor comes BEFORE the next-match sensor and
    the event, so an entity picked by mistake would override the right one.
    """
    entry = MockConfigEntry(domain="ffbb_tracker")
    entry.add_to_hass(paris)
    device = dr.async_get(paris).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("ffbb_tracker", club)},
        name=f"Basket {club}",
    )
    registry = er.async_get(paris)
    ids: dict[str, str] = {}
    for domain, key in (
        ("sensor", "dernier_match_date"),
        ("sensor", "dernier_match_adversaire"),
        ("sensor", "dernier_match_resultat"),
        ("sensor", "dernier_match_score"),
        ("sensor", "forme_recente"),
        ("sensor", "prochain_match_date"),
        ("event", "fin_de_match"),
    ):
        ids[key] = registry.async_get_or_create(
            domain,
            "ffbb_tracker",
            f"{club}_{key}",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id=f"basket_{club}_{key}",
        ).entity_id
    return ids


def _set_last_match_sensors(
    paris: HomeAssistant, ids: dict[str, str], *, away: bool = True
) -> None:
    """The permanent "last match" sensors: a 40-70 away defeat at New Club."""
    paris.states.async_set(
        ids["dernier_match_date"],
        "2026-01-17T19:00:00+00:00",
        {
            "round": "5",
            "gym_name": "Salle Dernier",
            "gym_city": "Dax",
            "gym_address": "1 rue de la Salle",
            "is_home": not away,
        },
    )
    paris.states.async_set(ids["dernier_match_adversaire"], "New Club", {})
    paris.states.async_set(ids["dernier_match_resultat"], "loss", {})
    paris.states.async_set(
        ids["dernier_match_score"],
        "40 - 70",
        {"team_score": 40, "opponent_score": 70},
    )
    paris.states.async_set(ids["forme_recente"], "L-W", {"current_streak": "1L"})


async def _result_message(
    paris: HomeAssistant, ids: dict[str, str], trigger: dict[str, Any] | None
) -> str:
    blueprint = _load("match_result_notification_telegram.yaml")
    message = _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]
    variables: dict[str, Any] = {
        "team_title": "Basket Landes",
        "event_entity": ids["fin_de_match"],
    }
    if trigger is not None:
        variables["trigger"] = trigger
    return await _render(paris, message, variables)


async def test_result_ignores_the_event_and_next_match_entities(paris):
    """Only the "last match" sensors fill in what the event does not carry.

    The event has a `round` and no `gym_address`, and the next-match sensor can
    have no address either: neither may be mistaken for the last match.
    """
    ids = _register_team(paris)
    _set_last_match_sensors(paris, ids)
    paris.states.async_set(
        ids["prochain_match_date"],
        "2026-02-07T19:00:00+00:00",
        {
            "round": "6",
            "gym_name": "Salle Prochain",
            "gym_address": None,
            "result_grace_hours": 3,
        },
    )
    # The fresh event carries the result but neither the gym nor the date.
    paris.states.async_set(
        ids["fin_de_match"],
        "2026-01-17T21:30:00.000+00:00",
        {
            "event_type": "loss",
            "opponent": "New Club",
            "team_score": 40,
            "opponent_score": 70,
            "round": "5",
        },
    )

    rendered = await _result_message(paris, ids, {"id": "match_finished"})

    assert "Salle Dernier" in rendered
    assert "Salle Prochain" not in rendered
    assert "Salle non renseignée" not in rendered
    assert "Journée 5" in rendered
    assert "Journée 6" not in rendered


async def test_result_finds_renamed_sensors_by_their_attributes(paris):
    """An entity renamed by hand is still found by what it carries."""
    ids = _register_team(paris, "renamed")
    registry = er.async_get(paris)
    renamed = registry.async_update_entity(
        ids["dernier_match_date"], new_entity_id="sensor.mon_match_precedent"
    ).entity_id
    ids["dernier_match_date"] = renamed
    _set_last_match_sensors(paris, ids)
    paris.states.async_set(
        ids["prochain_match_date"],
        "2026-02-07T19:00:00+00:00",
        {"round": "6", "gym_name": "Salle Prochain", "result_grace_hours": 3},
    )
    paris.states.async_set(ids["fin_de_match"], "unknown", {})

    rendered = await _result_message(paris, ids, {"id": "match_finished"})

    assert "Salle Dernier" in rendered
    assert "Salle Prochain" not in rendered


def _stale_event(paris: HomeAssistant, ids: dict[str, str]) -> None:
    """The event still holds the figures it fired with (a 60-50 win at Old Club)."""
    paris.states.async_set(
        ids["fin_de_match"],
        "2026-01-17T21:30:00.000+00:00",
        {
            "event_type": "win",
            "opponent": "Old Club",
            "team_score": 60,
            "opponent_score": 50,
            "point_difference": 10,
            "is_home": True,
            "match_date": "2026-01-17T19:00:00+00:00",
            "round": "5",
            "gym_name": "Salle Event",
            "gym_city": "Dax",
        },
    )


async def test_refresh_click_reads_the_sensors_before_the_event(paris):
    """A score corrected for the same match does not fire the event again: on
    "Actualiser" the permanent sensors, which follow the correction, win."""
    ids = _register_team(paris)
    _set_last_match_sensors(paris, ids)
    _stale_event(paris, ids)

    rendered = await _result_message(paris, ids, {"id": "callback_refresh"})

    assert "New Club" in rendered
    assert "Défaite" in rendered
    assert "Score :</b> 70 - 40\n" in rendered + "\n"
    assert "Old Club" not in rendered
    assert "Victoire" not in rendered
    assert "Salle Dernier" in rendered
    assert "Extérieur" in rendered


@pytest.mark.parametrize(
    "trigger",
    [{"id": "match_finished"}, None],
    ids=["new-result", "manual-run"],
)
async def test_the_event_stays_first_for_a_new_result(paris, trigger):
    """Just fired, the event is the freshest source: it keeps priority, on a
    real notification and on a manual run of the automation."""
    ids = _register_team(paris)
    _set_last_match_sensors(paris, ids)
    _stale_event(paris, ids)

    rendered = await _result_message(paris, ids, trigger)

    assert "Old Club" in rendered
    assert "Victoire" in rendered
    assert "Salle Event" in rendered
    assert "New Club" not in rendered


async def test_refresh_click_without_any_event_data_uses_the_sensors(paris):
    """Right after setup the event never fired: the sensors give the message."""
    ids = _register_team(paris)
    _set_last_match_sensors(paris, ids)
    paris.states.async_set(ids["fin_de_match"], "unknown", {})

    rendered = await _result_message(paris, ids, {"id": "callback_refresh"})

    assert "New Club" in rendered
    assert "Défaite" in rendered
    assert "Journée 5" in rendered


# --- rank_changed_notification_telegram ---------------------------------------

RANK_EVENT = "event.rank_changed"


def _rank_message() -> str:
    blueprint = _load("rank_changed_notification_telegram.yaml")
    return _find_action(blueprint, "telegram_bot.send_message")["data"]["message"]


def _position_line(rendered: str) -> str:
    return next(line for line in rendered.splitlines() if "Position" in line)


@pytest.mark.parametrize(
    ("old", "new", "event_type", "expected"),
    [
        pytest.param(4, 1, "up", "4e → 🥇 1er", id="reaching-first"),
        pytest.param(3, 2, "up", "🥉 3e → 🥈 2e", id="climbing-the-podium"),
        pytest.param(1, 2, "down", "🥇 1er → 🥈 2e", id="leaving-first"),
        pytest.param(3, 4, "down", "🥉 3e → 4e", id="leaving-the-podium"),
        pytest.param(5, 4, "up", "5e → 4e", id="plain"),
        pytest.param(11, 10, "up", "11e → 10e", id="eleventh-stays-e"),
        pytest.param(21, 22, "down", "21e → 22e", id="twenty-first-stays-e"),
    ],
)
async def test_rank_message_uses_french_ordinals(paris, old, new, event_type, expected):
    paris.states.async_set(
        RANK_EVENT,
        "2026-01-10T21:30:00.000+00:00",
        {"event_type": event_type, "old_position": old, "new_position": new},
    )

    rendered = await _render(
        paris, _rank_message(), {"team_title": "Basket", "event_entity": RANK_EVENT}
    )

    assert _position_line(rendered).endswith(expected)


@pytest.mark.parametrize(
    ("event_type", "status"),
    [("up", "Progression au classement"), ("down", "Recul au classement")],
)
async def test_rank_message_status_follows_the_direction(paris, event_type, status):
    paris.states.async_set(
        RANK_EVENT,
        "2026-01-10T21:30:00.000+00:00",
        {"event_type": event_type, "old_position": 3, "new_position": 2},
    )

    rendered = await _render(
        paris, _rank_message(), {"team_title": "Basket", "event_entity": RANK_EVENT}
    )

    assert status in rendered


async def test_rank_message_before_any_change_has_no_position_line(paris):
    """The event isn't restored: until it fires, there is nothing to report.

    Pressing "Actualiser" then must not show a "? → ?" line.
    """
    paris.states.async_set(RANK_EVENT, "unknown", {})

    rendered = await _render(
        paris, _rank_message(), {"team_title": "Basket", "event_entity": RANK_EVENT}
    )

    assert "Aucun changement de classement enregistré" in rendered
    assert "Position" not in rendered
    assert "?" not in rendered


def _rank_condition() -> str:
    return _find_condition_template(_load("rank_changed_notification_telegram.yaml"))


def _rank_trigger(
    event_type: str | None,
    *,
    old_state: str | None = None,
    age: timedelta = timedelta(seconds=1),
) -> dict[str, Any]:
    """Trigger data for an event entity that fired `age` ago."""
    fired = (dt_util.utcnow() - age).isoformat()
    old = State(RANK_EVENT, old_state or "2026-01-01T10:00:00+00:00", {})
    new = State(
        RANK_EVENT,
        fired,
        {} if event_type is None else {"event_type": event_type},
    )
    return {"id": "rank_changed", "from_state": old, "to_state": new}


@pytest.mark.parametrize(
    ("direction_filter", "event_type", "expected"),
    [
        ("both", "up", "True"),
        ("both", "down", "True"),
        ("up", "up", "True"),
        ("up", "down", "False"),
        ("down", "down", "True"),
        ("down", "up", "False"),
        ("both", None, "False"),
    ],
)
async def test_rank_condition_applies_the_direction_filter(
    paris, direction_filter, event_type, expected
):
    rendered = await _render(
        paris,
        _rank_condition(),
        {"direction_filter": direction_filter, "trigger": _rank_trigger(event_type)},
    )

    assert rendered == expected


async def test_rank_condition_ignores_the_return_from_an_outage(paris):
    """After an API outage the event entity goes unavailable, then comes back
    with its old timestamp and attributes: that is not a new rank change."""
    trigger = _rank_trigger("up", old_state="unavailable", age=timedelta(hours=3))

    rendered = await _render(
        paris, _rank_condition(), {"direction_filter": "both", "trigger": trigger}
    )

    assert rendered == "False"


async def test_rank_condition_keeps_a_change_that_lands_right_after_an_outage(paris):
    trigger = _rank_trigger("up", old_state="unavailable")

    rendered = await _render(
        paris, _rank_condition(), {"direction_filter": "both", "trigger": trigger}
    )

    assert rendered == "True"


async def test_rank_condition_rejects_a_state_that_is_not_a_timestamp(paris):
    trigger = _rank_trigger("up")
    trigger["to_state"] = State(RANK_EVENT, "unavailable", {"event_type": "up"})

    rendered = await _render(
        paris, _rank_condition(), {"direction_filter": "both", "trigger": trigger}
    )

    assert rendered == "False"


# --- match_result_notification_telegram: condition ----------------------------


def _result_condition() -> str:
    return _find_condition_template(_load("match_result_notification_telegram.yaml"))


def _result_trigger(
    event_type: str | None,
    *,
    old_state: str = "2026-01-01T10:00:00+00:00",
    age: timedelta = timedelta(seconds=1),
) -> dict[str, Any]:
    fired = (dt_util.utcnow() - age).isoformat()
    return {
        "id": "match_finished",
        "from_state": State(EVENT_ENTITY, old_state, {}),
        "to_state": State(
            EVENT_ENTITY,
            fired,
            {} if event_type is None else {"event_type": event_type},
        ),
    }


@pytest.mark.parametrize(
    ("notify_non_win_results", "event_type", "expected"),
    [
        (True, "win", "True"),
        (True, "loss", "True"),
        (True, "draw", "True"),
        (False, "win", "True"),
        (False, "loss", "False"),
        (False, "draw", "False"),
        (True, None, "False"),
    ],
)
async def test_result_condition_applies_the_non_win_filter(
    paris, notify_non_win_results, event_type, expected
):
    rendered = await _render(
        paris,
        _result_condition(),
        {
            "notify_non_win_results": notify_non_win_results,
            "trigger": _result_trigger(event_type),
        },
    )

    assert rendered == expected


async def test_result_condition_ignores_the_return_from_an_outage(paris):
    """Same result, same timestamp, back from unavailable: already notified."""
    trigger = _result_trigger("win", old_state="unavailable", age=timedelta(hours=3))

    rendered = await _render(
        paris, _result_condition(), {"notify_non_win_results": True, "trigger": trigger}
    )

    assert rendered == "False"


async def test_result_condition_keeps_a_result_that_lands_right_after_an_outage(paris):
    trigger = _result_trigger("loss", old_state="unavailable")

    rendered = await _render(
        paris, _result_condition(), {"notify_non_win_results": True, "trigger": trigger}
    )

    assert rendered == "True"


async def test_result_condition_accepts_a_first_result_from_unknown(paris):
    trigger = _result_trigger("win", old_state="unknown")

    rendered = await _render(
        paris, _result_condition(), {"notify_non_win_results": True, "trigger": trigger}
    )

    assert rendered == "True"


# --- match_notifications_telegram: alert condition ----------------------------


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("Biaudos", "Biscarrosse", "True"),
        ("unknown", "Biscarrosse", "True"),
        ("Biscarrosse", "Biscarrosse", "False"),
        ("unavailable", "Biscarrosse", "False"),
        ("Biscarrosse", "unavailable", "False"),
        ("Biscarrosse", "unknown", "False"),
    ],
)
async def test_alert_condition_ignores_outages_and_non_changes(
    paris, old, new, expected
):
    """A sensor coming back from unavailable with its old value is not a
    match change; unknown -> value (no upcoming match before) still is."""
    condition = _find_condition_template(_load("match_notifications_telegram.yaml"))

    rendered = await _render(
        paris, condition, _alert_variables(_opponent(old), _opponent(new))
    )

    assert rendered == expected


# --- phase_change_notification_telegram ---------------------------------------


def _phase_change_blueprint() -> dict[str, Any]:
    return _load("phase_change_notification_telegram.yaml")


async def test_phase_change_message_names_the_team_and_the_repair(paris):
    message = _find_action(_phase_change_blueprint(), "telegram_bot.send_message")[
        "data"
    ]["message"]

    rendered = await _render(paris, message, {"team_title": "Basket Landes"})

    assert "Basket Landes" in rendered
    assert "Plus aucun match prévu" in rendered
    assert "Corrections" in rendered
    assert "https://my.home-assistant.io/redirect/repairs/" in rendered


def test_phase_change_only_fires_when_the_sensor_turns_on():
    """off -> on only, so a restart (unknown -> on) never replays the alert."""
    trigger = _phase_change_blueprint()["triggers"][0]

    assert trigger["trigger"] == "state"
    assert trigger["from"] == "off"
    assert trigger["to"] == "on"


# --- the refresh button's callback command is made from the team's name -------

CALLBACK_BLUEPRINTS = [
    ("match_notifications_telegram.yaml", "/refresh_match"),
    ("match_result_notification_telegram.yaml", "/refresh_result"),
    ("rank_changed_notification_telegram.yaml", "/refresh_rank"),
]


def _command_templates(filename: str) -> tuple[str, str]:
    """The command template of trigger_variables, then the one of variables."""
    blueprint = _load(filename)
    return (
        blueprint["trigger_variables"]["refresh_cmd"],
        blueprint["variables"]["refresh_cmd"],
    )


def _command_variables(command: str = "", title: str = "", entity: str = "event.x"):
    return {
        "refresh_cmd_input": command,
        "team_title_input": title,
        "refresh_entity": entity,
    }


@pytest.mark.parametrize(("filename", "prefix"), CALLBACK_BLUEPRINTS)
def test_the_callback_command_is_optional(filename, prefix):
    field = _load(filename)["blueprint"]["input"]["refresh_command"]

    assert field["default"] == ""
    assert prefix in field["description"]


@pytest.mark.parametrize(("filename", "prefix"), CALLBACK_BLUEPRINTS)
def test_the_button_listens_for_the_computed_command(filename, prefix):
    """The trigger and the keyboard must use the very same command."""
    blueprint = _load(filename)
    trigger = next(
        t for t in blueprint["triggers"] if t.get("id") == "callback_refresh"
    )

    assert trigger["event_data"]["data"] == "{{ refresh_cmd }}"
    first, second = _command_templates(filename)
    assert first == second


@pytest.mark.parametrize(("filename", "prefix"), CALLBACK_BLUEPRINTS)
async def test_a_command_typed_by_the_user_is_kept_as_is(paris, filename, prefix):
    template, _ = _command_templates(filename)

    rendered = await _render(
        paris, template, _command_variables(command="  /mon_bouton  ")
    )

    assert rendered == "/mon_bouton"


@pytest.mark.parametrize(("filename", "prefix"), CALLBACK_BLUEPRINTS)
async def test_the_command_follows_the_custom_team_name(paris, filename, prefix):
    template, _ = _command_templates(filename)

    rendered = await _render(
        paris, template, _command_variables(title="Équipe U13M & Co")
    )

    assert rendered == f"{prefix}_equipe_u13m_co"


@pytest.mark.parametrize(("filename", "prefix"), CALLBACK_BLUEPRINTS)
async def test_without_a_name_the_command_follows_the_entity(paris, filename, prefix):
    template, _ = _command_templates(filename)

    rendered = await _render(
        paris, template, _command_variables(entity="event.ujsbp_u13m_rank_changed")
    )

    assert rendered == f"{prefix}_ujsbp_u13m_rank_changed"


@pytest.mark.parametrize(("filename", "prefix"), CALLBACK_BLUEPRINTS)
async def test_two_teams_get_two_different_commands(paris, filename, prefix):
    template, _ = _command_templates(filename)

    first = await _render(paris, template, _command_variables(title="U13M"))
    second = await _render(paris, template, _command_variables(title="U15F"))

    assert first != second


@pytest.mark.parametrize(("filename", "prefix"), CALLBACK_BLUEPRINTS)
async def test_a_long_team_name_keeps_the_command_within_telegrams_limit(
    paris, filename, prefix
):
    """Telegram refuses callback data over 64 bytes."""
    template, _ = _command_templates(filename)

    rendered = await _render(paris, template, _command_variables(title="a" * 200))

    assert rendered.startswith(f"{prefix}_a")
    assert len(rendered.encode()) <= 64


def test_the_full_pack_is_named_like_the_other_telegram_blueprints():
    name = _load("match_notifications_telegram.yaml")["blueprint"]["name"]

    assert name == "FFBB Tracker - Notifications (Telegram)"
