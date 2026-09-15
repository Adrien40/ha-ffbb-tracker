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

Additionally covers the full club-search path (async_step_club and
async_step_team end to end, not just the direct-URL/ID shortcut), every
error branch surfaced from the FFBB API at each step, and the stale
device cleanup that runs when reconfiguring an entry towards a
*different* team -- a data-deleting side effect that deserves its own
test, not just visual review.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.api import (
    FFBBApiError,
    FFBBConnectionError,
    FFBBNotFoundError,
)
from custom_components.ffbb_tracker.config_flow import (
    RAW_ID_PATTERN,
    URL_ID_PATTERN,
    FFBBTrackerConfigFlow,
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

SAMPLE_CLUBS = [
    {"id": "1", "nom": "Basket Landes", "code": "NAQ0040141"},
]

SAMPLE_CLUB_ENGAGEMENTS = [
    {
        "id": "123456",
        "nom": "Basket Landes",
        "idCompetition": {"id": "comp-1", "nom": "Excellence Régionale"},
        "idPoule": {"id": "poule-1", "nom": "Poule A"},
    }
]


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


@pytest.mark.asyncio
async def test_url_shaped_query_routes_directly_without_club_search(hass):
    """A pasted team URL must skip club search entirely (the URL_ID_PATTERN
    shortcut), going straight to engagement lookup like a raw ID would.
    """
    with (
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.get_engagement",
            return_value=SAMPLE_ENGAGEMENT,
        ) as mock_get_engagement,
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.search_clubs",
        ) as mock_search_clubs,
        patch(
            "custom_components.ffbb_tracker.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={
                "search_query": (
                    "https://competitions.ffbb.com/equipe/engagement/123456"
                )
            },
        )

    assert result["type"] == "create_entry"
    mock_get_engagement.assert_called_once_with("123456")
    mock_search_clubs.assert_not_called()


@pytest.mark.asyncio
async def test_query_too_short_shows_form_error(hass):
    """A free-text query under MIN_SEARCH_QUERY_LENGTH, that is neither a
    URL nor a raw numeric ID, must re-show the form with query_too_short
    instead of calling the API with a near-empty query.
    """
    with patch(
        "custom_components.ffbb_tracker.config_flow.FFBBClient.search_clubs",
    ) as mock_search_clubs:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "a"},
        )

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "query_too_short"
    mock_search_clubs.assert_not_called()


@pytest.mark.asyncio
async def test_club_search_no_clubs_found_shows_form_error(hass):
    """A free-text query that matches no club must show no_clubs_found."""
    with patch(
        "custom_components.ffbb_tracker.config_flow.FFBBClient.search_clubs",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "Équipe Inexistante"},
        )

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "no_clubs_found"


@pytest.mark.asyncio
async def test_club_search_connection_error_shows_cannot_connect(hass):
    """A network failure during club search must surface cannot_connect,
    not an unhandled exception.
    """
    with patch(
        "custom_components.ffbb_tracker.config_flow.FFBBClient.search_clubs",
        side_effect=FFBBConnectionError("timeout"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "Basket Landes"},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_club_search_generic_api_error_shows_unknown(hass):
    """A non-connection API error during club search must surface
    "unknown" rather than propagating and crashing the flow.
    """
    with patch(
        "custom_components.ffbb_tracker.config_flow.FFBBClient.search_clubs",
        side_effect=FFBBApiError("HTTP 500"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "Basket Landes"},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "unknown"


# ---------------------------------------------------------------------------
# Direct URL/ID lookup: error branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_id_not_found_shows_invalid_engagement(hass):
    """A raw ID or URL that the API 404s on must show invalid_engagement,
    not crash or silently do nothing.
    """
    with patch(
        "custom_components.ffbb_tracker.config_flow.FFBBClient.get_engagement",
        side_effect=FFBBNotFoundError("not found"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "999999"},
        )

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "invalid_engagement"


@pytest.mark.asyncio
async def test_direct_id_connection_error_shows_cannot_connect(hass):
    """A network failure on the direct ID/URL path must surface
    cannot_connect.
    """
    with patch(
        "custom_components.ffbb_tracker.config_flow.FFBBClient.get_engagement",
        side_effect=FFBBConnectionError("timeout"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "123456"},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_direct_id_generic_api_error_shows_unknown(hass):
    """A non-connection API error on the direct ID/URL path must surface
    "unknown".
    """
    with patch(
        "custom_components.ffbb_tracker.config_flow.FFBBClient.get_engagement",
        side_effect=FFBBApiError("HTTP 500"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "123456"},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "unknown"


# ---------------------------------------------------------------------------
# Full club -> team selection flow (async_step_club / async_step_team)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_club_search_flow_creates_entry(hass):
    """The full happy path: free-text search -> pick a club -> pick a
    team -> entry created. Not just the direct-URL/ID shortcut already
    covered above.
    """
    with (
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.search_clubs",
            return_value=SAMPLE_CLUBS,
        ),
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.get_club_engagements",
            return_value=SAMPLE_CLUB_ENGAGEMENTS,
        ),
        patch(
            "custom_components.ffbb_tracker.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "Basket Landes"},
        )
        assert result["type"] == "form"
        assert result["step_id"] == "club"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"club_id": "1"}
        )
        assert result["type"] == "form"
        assert result["step_id"] == "team"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"engagement_id": "123456"}
        )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_ENGAGEMENT_ID] == "123456"
    assert result["data"][CONF_TEAM_NAME] == "Basket Landes"
    assert result["data"][CONF_POULE_ID] == "poule-1"
    assert result["data"][CONF_ORGANISME_ID] == "1"


@pytest.mark.asyncio
async def test_club_selection_with_unknown_club_id_aborts(hass):
    """Defensive fallback: if self._selected_club can't be resolved from
    self._clubs, abort with club_not_found instead of raising.

    Note: this branch is NOT reachable through the normal UI path --
    club_id always comes from a vol.In(club_options) schema built from
    self._clubs, so hass.config_entries.flow.async_configure() rejects an
    out-of-list value at schema-validation time, before this code ever
    runs (confirmed: doing so raises
    homeassistant.data_entry_flow.InvalidData, not this abort). This test
    calls the step method directly to cover the fallback itself, as a
    safety net in case self._clubs and club_options were ever to fall out
    of sync.
    """
    flow = FFBBTrackerConfigFlow()
    flow.hass = hass
    flow._clubs = SAMPLE_CLUBS

    result = await flow.async_step_club(user_input={"club_id": "999"})

    assert result["type"] == "abort"
    assert result["reason"] == "club_not_found"


@pytest.mark.asyncio
async def test_club_engagements_no_teams_found_shows_form_error(hass):
    """A club with no valid poule-assigned engagements must show
    no_teams_found rather than an empty, confusing team list.
    """
    engagements_without_poule = [
        {"id": "1", "nom": "Basket Landes", "idPoule": {}},
    ]
    with (
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.search_clubs",
            return_value=SAMPLE_CLUBS,
        ),
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.get_club_engagements",
            return_value=engagements_without_poule,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "Basket Landes"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"club_id": "1"}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "club"
    assert result["errors"]["base"] == "no_teams_found"


@pytest.mark.asyncio
async def test_club_engagements_connection_error_shows_cannot_connect(hass):
    """A network failure while fetching a club's teams must surface
    cannot_connect.
    """
    with (
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.search_clubs",
            return_value=SAMPLE_CLUBS,
        ),
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.get_club_engagements",
            side_effect=FFBBConnectionError("timeout"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "Basket Landes"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"club_id": "1"}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "club"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_club_engagements_generic_api_error_shows_unknown(hass):
    """A non-connection API error while fetching a club's teams must
    surface "unknown".
    """
    with (
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.search_clubs",
            return_value=SAMPLE_CLUBS,
        ),
        patch(
            "custom_components.ffbb_tracker.config_flow.FFBBClient.get_club_engagements",
            side_effect=FFBBApiError("HTTP 500"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"search_query": "Basket Landes"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"club_id": "1"}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "club"
    assert result["errors"]["base"] == "unknown"


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


@pytest.mark.asyncio
async def test_reconfigure_to_new_engagement_removes_stale_device(hass):
    """Reconfiguring towards a different team must remove the device tied
    to the *old* engagement_id -- this is a data-deleting side effect,
    not just a display change, so it deserves its own explicit check
    rather than trusting visual review of the diff.
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

    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "123456")},
        name="Basket Landes - Excellence Régionale",
    )
    assert device_registry.async_get(old_device.id) is not None

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
    assert device_registry.async_get(old_device.id) is None


@pytest.mark.asyncio
async def test_reconfigure_to_same_engagement_keeps_device(hass):
    """Reconfiguring back to the *same* engagement_id must NOT remove the
    device -- only a genuine team change should trigger cleanup.
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

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "123456")},
        name="Basket Landes - Excellence Régionale",
    )

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
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
            data={"search_query": "123456"},
        )

    assert device_registry.async_get(device.id) is not None


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
