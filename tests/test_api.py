"""Tests for FFBBClient.

Focus on the retry-on-expired-token logic and error mapping, since these
are the parts most likely to regress silently (e.g. re-introducing the
duplicated retry code, or losing the retry=False guard and causing an
infinite loop).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.ffbb_tracker.api import (
    FFBBApiError,
    FFBBClient,
    FFBBConnectionError,
    FFBBNotFoundError,
)


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
