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

from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util.yaml import load_yaml

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
