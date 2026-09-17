"""Tests for FFBBClient.

Focus on the retry-on-expired-token logic and error mapping, since these
are the parts most likely to regress silently (e.g. re-introducing the
duplicated retry code, or losing the retry=False guard and causing an
infinite loop).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.ffbb_tracker.api import (
    FFBBApiError,
    FFBBClient,
    FFBBConnectionError,
    FFBBNotFoundError,
    FFBBRateLimiter,
)
from custom_components.ffbb_tracker.const import DEFAULT_DIRECTUS_TOKEN


def _mock_response(status: int, json_data: dict | None = None, text: str = ""):
    """Build a mock aiohttp response usable as an async context manager."""
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=json_data or {})
    response.text = AsyncMock(return_value=text)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_request_returns_data_on_success():
    """A 200 response returns the 'data' key from the JSON payload."""
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(200, {"data": [{"id": "1"}]}))
    client = FFBBClient(session)

    result = await client._request("items/foo")

    assert result == [{"id": "1"}]


@pytest.mark.asyncio
async def test_request_raises_not_found_on_404():
    """A 404 response raises FFBBNotFoundError, not a generic error."""
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(404))
    client = FFBBClient(session)

    with pytest.raises(FFBBNotFoundError):
        await client._request("items/missing")


@pytest.mark.asyncio
async def test_request_retries_once_on_401_then_succeeds():
    """On 401, the client refreshes the token and retries exactly once."""
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            _mock_response(401),
            _mock_response(200, {"data": {"key_directus_website": "new-token"}}),
            _mock_response(200, {"data": {"id": "ok"}}),
        ]
    )
    client = FFBBClient(session)

    result = await client._request("items/foo")

    assert result == {"id": "ok"}
    assert session.get.call_count == 3


@pytest.mark.asyncio
async def test_request_does_not_retry_twice():
    """A second 401 after the retry must raise, never loop indefinitely."""
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            _mock_response(401),
            _mock_response(200, {"data": {"key_directus_website": "new-token"}}),
            _mock_response(401),
        ]
    )
    client = FFBBClient(session)

    with pytest.raises(FFBBApiError):
        await client._request("items/foo")

    assert session.get.call_count == 3


@pytest.mark.asyncio
async def test_request_wraps_timeout_as_connection_error():
    """A timeout must surface as FFBBConnectionError, not raw TimeoutError."""
    session = MagicMock()
    session.get = MagicMock(side_effect=TimeoutError())
    client = FFBBClient(session)

    with pytest.raises(FFBBConnectionError):
        await client._request("items/foo")


@pytest.mark.asyncio
async def test_request_wraps_client_error_as_connection_error():
    """An aiohttp.ClientError must surface as FFBBConnectionError."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientConnectionError())
    client = FFBBClient(session)

    with pytest.raises(FFBBConnectionError):
        await client._request("items/foo")


@pytest.mark.asyncio
async def test_request_wraps_malformed_json_as_api_error():
    """A body with a JSON content-type that isn't valid JSON must surface as
    FFBBApiError, not leak the raw ValueError from response.json().

    aiohttp raises a plain ValueError (not aiohttp.ClientError) in this
    case -- e.g. a proxy or captive-portal error page served with a JSON
    content-type header. Left unwrapped, this would bypass
    DataUpdateCoordinator's single-log-per-outage behavior and log on
    every failed poll instead of once.
    """
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(side_effect=ValueError("Expecting value: line 1"))

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=cm)
    client = FFBBClient(session)

    with pytest.raises(FFBBApiError):
        await client._request("items/foo")


@pytest.mark.asyncio
async def test_request_rejects_non_dict_payload():
    """A syntactically valid but unexpectedly-shaped JSON body (e.g. a bare
    list instead of an object) must surface as FFBBApiError rather than an
    unhandled AttributeError from calling .get() on it.
    """
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(200, json_data=[1, 2, 3]))
    client = FFBBClient(session)

    with pytest.raises(FFBBApiError):
        await client._request("items/foo")


@pytest.mark.asyncio
async def test_search_clubs_returns_empty_list_on_non_list_payload():
    """search_clubs defensively returns [] if 'data' isn't a list."""
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(200, {"data": None}))
    client = FFBBClient(session)

    result = await client.search_clubs("Landes")

    assert result == []


@pytest.mark.asyncio
async def test_get_engagement_raises_not_found_on_non_dict_payload():
    """get_engagement raises FFBBNotFoundError if 'data' isn't a dict."""
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(200, {"data": None}))
    client = FFBBClient(session)

    with pytest.raises(FFBBNotFoundError):
        await client.get_engagement("engagement-123")


@pytest.mark.asyncio
async def test_get_engagement_returns_dict_on_success():
    """The happy path of get_engagement itself, not just its guard clause --
    every existing config_flow test patches this method wholesale, so
    nothing else exercises its real body.
    """
    session = MagicMock()
    payload = {"data": {"id": "123456", "nom": "Basket Landes"}}
    session.get = MagicMock(return_value=_mock_response(200, payload))
    client = FFBBClient(session)

    result = await client.get_engagement("123456")

    assert result == {"id": "123456", "nom": "Basket Landes"}


def test_base_url_property_strips_trailing_slash():
    """coordinator.py relies on client.base_url to build logo asset URLs --
    pin down that it's the same normalized value _request() itself uses.
    """
    session = MagicMock()
    client = FFBBClient(session, base_url="https://api.ffbb.app/")

    assert client.base_url == "https://api.ffbb.app"


@pytest.mark.asyncio
async def test_get_engagement_requests_organisme_logo_field():
    """get_engagement must ask Directus for idOrganisme.logo -- without it,
    the coordinator can never resolve the tracked team's own logo URL.
    """
    session = MagicMock()
    payload = {"data": {"id": "123456", "nom": "Basket Landes"}}
    session.get = MagicMock(return_value=_mock_response(200, payload))
    client = FFBBClient(session)

    await client.get_engagement("123456")

    _, kwargs = session.get.call_args
    assert "idOrganisme.logo" in kwargs["params"]["fields"]


@pytest.mark.asyncio
async def test_get_club_engagements_returns_list():
    """get_club_engagements' real body, likewise never exercised by the
    config_flow tests that patch it wholesale.
    """
    session = MagicMock()
    payload = {"data": [{"id": "1", "nom": "Basket Landes"}]}
    session.get = MagicMock(return_value=_mock_response(200, payload))
    client = FFBBClient(session)

    result = await client.get_club_engagements("org-1")

    assert result == [{"id": "1", "nom": "Basket Landes"}]


@pytest.mark.asyncio
async def test_get_club_engagements_returns_empty_list_on_non_list_payload():
    """Defensive fallback, mirroring search_clubs' own guard."""
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(200, {"data": None}))
    client = FFBBClient(session)

    result = await client.get_club_engagements("org-1")

    assert result == []


@pytest.mark.asyncio
async def test_get_poule_data_raises_not_found_on_non_dict_payload():
    """get_poule_data raises FFBBNotFoundError if 'data' isn't a dict --
    the actual signal the coordinator's season-rollover detection
    (_handle_not_found) depends on.
    """
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(200, {"data": None}))
    client = FFBBClient(session)

    with pytest.raises(FFBBNotFoundError):
        await client.get_poule_data("poule-1")


@pytest.mark.asyncio
async def test_get_poule_data_requests_organisme_logo_fields():
    """get_poule_data must ask Directus for both sides' organisme.logo --
    without it, the coordinator can never resolve opponent logo URLs.
    """
    session = MagicMock()
    payload = {"data": {"id": "poule-1", "nom": "Poule A"}}
    session.get = MagicMock(return_value=_mock_response(200, payload))
    client = FFBBClient(session)

    await client.get_poule_data("poule-1")

    _, kwargs = session.get.call_args
    fields = kwargs["params"]["fields"]
    assert "rencontres.idOrganismeEquipe1.logo" in fields
    assert "rencontres.idOrganismeEquipe2.logo" in fields


# --- FFBBRateLimiter.throttle() ---------------------------------------------


@pytest.mark.asyncio
async def test_throttle_does_not_wait_when_delay_is_disabled():
    """delay <= 0 means rate limiting is off: must return immediately
    without touching the lock or the timestamp.
    """
    limiter = FFBBRateLimiter(delay=0)

    await limiter.throttle()

    assert limiter._last_request_timestamp == 0.0


@pytest.mark.asyncio
async def test_throttle_does_not_wait_on_first_call():
    """With no prior request recorded, elapsed time is huge: no sleep."""
    limiter = FFBBRateLimiter(delay=0.5)
    sleep_calls: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleep_calls.append(duration)

    with patch.object(asyncio, "sleep", fake_sleep):
        await limiter.throttle()

    assert sleep_calls == []
    assert limiter._last_request_timestamp > 0.0


@pytest.mark.asyncio
async def test_throttle_waits_when_called_again_before_delay_elapses():
    """A second call right after the first must sleep for the remaining
    gap, not just skip straight through -- this is the actual pacing
    mechanism shared across every FFBBClient instance's requests.
    """
    limiter = FFBBRateLimiter(delay=0.5)
    sleep_calls: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleep_calls.append(duration)

    with patch.object(asyncio, "sleep", fake_sleep):
        await limiter.throttle()
        await limiter.throttle()

    assert len(sleep_calls) == 1
    assert 0 < sleep_calls[0] <= 0.5


@pytest.mark.asyncio
async def test_request_calls_throttle_when_rate_limiter_configured():
    """When a FFBBClient is built with a rate_limiter, _request() must
    call its throttle() before issuing the HTTP GET -- this is the only
    thing wiring the limiter to actual outbound requests, as opposed to
    the limiter's own pacing logic (tested in isolation above).
    """
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(200, {"data": []}))
    limiter = FFBBRateLimiter(delay=0)
    limiter.throttle = AsyncMock(wraps=limiter.throttle)
    client = FFBBClient(session, rate_limiter=limiter)

    await client._request("items/foo")

    limiter.throttle.assert_awaited_once()


# --- _refresh_token error handling ------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_keeps_default_on_connection_error():
    """A failure fetching the dynamic token must not crash setup or the
    coordinator -- it's a best-effort refresh, so falling back to
    DEFAULT_DIRECTUS_TOKEN and logging a warning is the correct outcome,
    not propagating the error.
    """
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientConnectionError())
    client = FFBBClient(session)

    await client._refresh_token()

    assert client._token == DEFAULT_DIRECTUS_TOKEN


# --- get_poule_data pagination-limit warnings ------------------------------
#
# Directus silently truncates `rencontres`/`classements` to the requested
# `_limit` instead of erroring, so a response that lands exactly on the page
# size is the only signal we have that fixtures or standings might be
# missing. These tests pin down that the warning actually fires (and only
# fires) when it should -- a silent regression here means a team's schedule
# quietly loses matches with nothing in the log to explain why.


def _poule_payload(rencontres: list[dict], classements: list[dict]) -> dict:
    """Build a minimal get_poule_data() response with the given list sizes."""
    return {
        "data": {
            "id": "poule-1",
            "nom": "Poule A",
            "rencontres": rencontres,
            "classements": classements,
        }
    }


@pytest.mark.asyncio
async def test_get_poule_data_warns_when_rencontres_hit_pagination_limit(caplog):
    """A 'rencontres' list landing exactly on the page limit logs a warning."""
    session = MagicMock()
    limit = FFBBClient._RENCONTRES_LIMIT
    payload = _poule_payload(
        rencontres=[{"id": str(i)} for i in range(limit)],
        classements=[],
    )
    session.get = MagicMock(return_value=_mock_response(200, payload))
    client = FFBBClient(session)

    with caplog.at_level(logging.WARNING):
        await client.get_poule_data("poule-1")

    assert "Fixture list" in caplog.text
    assert "hit the API limit" in caplog.text


@pytest.mark.asyncio
async def test_get_poule_data_warns_when_classements_hit_pagination_limit(caplog):
    """A 'classements' list landing exactly on the page limit logs a warning."""
    session = MagicMock()
    limit = FFBBClient._CLASSEMENTS_LIMIT
    payload = _poule_payload(
        rencontres=[],
        classements=[{"id": str(i)} for i in range(limit)],
    )
    session.get = MagicMock(return_value=_mock_response(200, payload))
    client = FFBBClient(session)

    with caplog.at_level(logging.WARNING):
        await client.get_poule_data("poule-1")

    assert "Standings" in caplog.text
    assert "hit the API limit" in caplog.text


@pytest.mark.asyncio
async def test_get_poule_data_no_warning_below_pagination_limit(caplog):
    """A normal-sized response must not trigger the truncation warning."""
    session = MagicMock()
    payload = _poule_payload(
        rencontres=[{"id": "1"}, {"id": "2"}],
        classements=[{"id": "1"}, {"id": "2"}],
    )
    session.get = MagicMock(return_value=_mock_response(200, payload))
    client = FFBBClient(session)

    with caplog.at_level(logging.WARNING):
        await client.get_poule_data("poule-1")

    assert "hit the API limit" not in caplog.text
