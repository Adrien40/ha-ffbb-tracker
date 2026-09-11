"""Tests for FFBBTrackerConfigFlow.

Covers the two scenarios that took the most back-and-forth during review:
- reconfiguring an entry towards an engagement_id already used by another
  entry must abort with "already_configured", not silently create a
  duplicate;
- reconfiguring an entry back to the *same* engagement_id it already had
  must succeed with "reconfigure_successful", not falsely abort.

Also covers the URL/raw-ID detection patterns, since a regression there
routes valid input into the wrong step silently, and the options flow
that lets the user tune the polling interval after setup.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_RECONFIGURE
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.config_flow import (
    RAW_ID_PATTERN,
    URL_ID_PATTERN,
)
from custom_components.ffbb_tracker.const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_LIVE_POLLING,
    CONF_LIVE_SCAN_INTERVAL,
    CONF_LIVE_WINDOW_AFTER_HOURS,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_SCAN_INTERVAL,
    CONF_TEAM_NAME,
    DEFAULT_LIVE_POLLING,
    DEFAULT_LIVE_SCAN_INTERVAL,
    DEFAULT_LIVE_WINDOW_AFTER_HOURS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
)

SAMPLE_ENGAGEMENT = {
    "id": "123456",
    "nom": "Basket Landes",
    "idOrganisme": {"id": "org-1", "nom": "Basket Landes", "code": "NAQ0040141"},
    "idCompetition": {"id": "comp-1", "nom": "Excellence Régionale"},
    "idPoule": {"id": "poule-1", "nom": "Poule A"},
}


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://competitions.ffbb.com/equipe/engagement/123456",
        "https://competitions.ffbb.com/equipes/123456",
        "https://competitions.ffbb.com/équipe/123456",
    ],
)
def test_url_id_pattern_matches_team_urls(url):
    """Various real-world-shaped team URLs must yield the numeric ID."""
    match = URL_ID_PATTERN.search(url)
    assert match is not None
    assert match.group(1) == "123456"


def test_url_id_pattern_does_not_match_unrelated_url():
    """A club-listing URL with no /equipe(s)/ segment must not match."""
    match = URL_ID_PATTERN.search("https://competitions.ffbb.com/club/456789")
    assert match is None


@pytest.mark.parametrize("raw", ["123456", "1", "999999999999999999"])
def test_raw_id_pattern_matches_pure_digits(raw):
    assert RAW_ID_PATTERN.match(raw) is not None


@pytest.mark.parametrize("raw", ["Basket Landes", "NAQ0040141", "123abc", ""])
def test_raw_id_pattern_rejects_non_digit_queries(raw):
    assert RAW_ID_PATTERN.match(raw) is None


# ---------------------------------------------------------------------------
# Creation flow (direct ID / URL)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_entry_from_raw_engagement_id(hass):
    """Pasting a raw numeric ID creates an entry without club/team steps."""
    with (
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.get_engagement",
            return_value=SAMPLE_ENGAGEMENT,
        ),
        patch(
            "custom_components.ffbb_tracker.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "123456"},
        )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_ENGAGEMENT_ID] == "123456"
    assert result["data"][CONF_TEAM_NAME] == "Basket Landes"
    assert result["data"][CONF_POULE_ID] == "poule-1"


@pytest.mark.asyncio
async def test_create_entry_aborts_without_poule(hass):
    """An engagement with no assigned poule shows a form error, not a crash."""
    engagement_without_poule = {**SAMPLE_ENGAGEMENT, "idPoule": {}}
    with patch(
        "custom_components.ffbb_tracker.config_flow.FFBBClient.get_engagement",
        return_value=engagement_without_poule,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "123456"},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "no_poule_found"


# ---------------------------------------------------------------------------
# Reconfiguration: anti-duplicate + same-team round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconfigure_to_already_tracked_engagement_aborts(hass):
    """Reconfiguring towards an engagement_id used by *another* entry aborts."""
    entry_a = MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456",
        data={
            CONF_ENGAGEMENT_ID: "123456",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_POULE_ID: "poule-1",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry_a.add_to_hass(hass)

    entry_b = MockConfigEntry(
        domain=DOMAIN,
        unique_id="456789",
        data={
            CONF_ENGAGEMENT_ID: "456789",
            CONF_TEAM_NAME: "Autre Équipe",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_POULE_ID: "poule-1",
            CONF_ORGANISME_ID: "org-2",
        },
    )
    entry_b.add_to_hass(hass)

    with patch(
        "custom_components.ffbb_tracker.config_flow.FFBBClient.get_engagement",
        return_value=SAMPLE_ENGAGEMENT,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry_b.entry_id},
            data={"search_query": "123456"},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    assert entry_b.data[CONF_ENGAGEMENT_ID] == "456789"


@pytest.mark.asyncio
async def test_reconfigure_to_same_engagement_succeeds(hass):
    """Reconfiguring an entry back to its own current engagement_id
    must succeed, not falsely trigger the anti-duplicate abort.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456",
        data={
            CONF_ENGAGEMENT_ID: "123456",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_POULE_ID: "poule-1",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.get_engagement",
            return_value=SAMPLE_ENGAGEMENT,
        ),
        patch(
            "custom_components.ffbb_tracker.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
            data={"search_query": "123456"},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ENGAGEMENT_ID] == "123456"


@pytest.mark.asyncio
async def test_reconfigure_to_new_engagement_updates_entry(hass):
    """Reconfiguring to a genuinely new, unused engagement_id updates the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456",
        data={
            CONF_ENGAGEMENT_ID: "123456",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_POULE_ID: "poule-1",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry.add_to_hass(hass)

    new_engagement = {
        **SAMPLE_ENGAGEMENT,
        "id": "999999",
        "nom": "Nouvelle Équipe",
    }
    with (
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.get_engagement",
            return_value=new_engagement,
        ),
        patch(
            "custom_components.ffbb_tracker.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
            data={"search_query": "999999"},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ENGAGEMENT_ID] == "999999"
    assert entry.unique_id == "999999"


# ---------------------------------------------------------------------------
# Options flow: polling intervals and live match tracking
# ---------------------------------------------------------------------------


def _make_tracked_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456",
        data={
            CONF_ENGAGEMENT_ID: "123456",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_POULE_ID: "poule-1",
            CONF_ORGANISME_ID: "org-1",
        },
    )


async def test_options_flow_defaults_to_default_scan_interval(hass):
    """With no option stored yet, the form pre-fills default options."""
    entry = _make_tracked_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    schema_defaults = result["data_schema"]({})
    assert schema_defaults[CONF_SCAN_INTERVAL] == DEFAULT_SCAN_INTERVAL
    assert schema_defaults[CONF_LIVE_POLLING] == DEFAULT_LIVE_POLLING
    assert schema_defaults[CONF_LIVE_SCAN_INTERVAL] == DEFAULT_LIVE_SCAN_INTERVAL
    assert (
        schema_defaults[CONF_LIVE_WINDOW_AFTER_HOURS] == DEFAULT_LIVE_WINDOW_AFTER_HOURS
    )


async def test_options_flow_prefills_current_stored_interval(hass):
    """Previously saved options must be shown as the form's defaults."""
    entry = _make_tracked_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_SCAN_INTERVAL: 30,
            CONF_LIVE_POLLING: False,
            CONF_LIVE_SCAN_INTERVAL: 10,
            CONF_LIVE_WINDOW_AFTER_HOURS: 4,
        },
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_defaults = result["data_schema"]({})

    assert schema_defaults[CONF_SCAN_INTERVAL] == 30
    assert schema_defaults[CONF_LIVE_POLLING] is False
    assert schema_defaults[CONF_LIVE_SCAN_INTERVAL] == 10
    assert schema_defaults[CONF_LIVE_WINDOW_AFTER_HOURS] == 4


async def test_options_flow_saves_valid_interval(hass):
    """Submitting a value inside the allowed range updates entry.options."""
    entry = _make_tracked_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_SCAN_INTERVAL: 15}
    )

    assert result["type"] == "create_entry"
    assert entry.options[CONF_SCAN_INTERVAL] == 15


async def test_options_flow_schema_rejects_out_of_range_interval(hass):
    """The schema itself must reject a value above MAX_SCAN_INTERVAL.

    This checks the vol.Range bound directly, since the exact error-surfacing
    behavior of HA's flow manager isn't something to assert on blindly.
    """
    entry = _make_tracked_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"]

    with pytest.raises(vol.MultipleInvalid):
        schema({CONF_SCAN_INTERVAL: MAX_SCAN_INTERVAL + 1})


async def test_options_flow_schema_coerces_string_input_to_int(hass):
    """A numeric string from the UI must be coerced to int, not rejected."""
    entry = _make_tracked_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"]

    assert schema({CONF_SCAN_INTERVAL: "20"})[CONF_SCAN_INTERVAL] == 20
