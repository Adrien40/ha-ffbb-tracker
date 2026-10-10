"""Tests for FFBBClient.

Focus on the retry-on-expired-token logic and error mapping, since these
are the parts most likely to regress silently (e.g. re-introducing the
duplicated retry code, or losing the retry=False guard and causing an
infinite loop).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from multidict import CIMultiDict

from custom_components.ffbb_tracker.api import (
    FFBBApiError,
    FFBBClient,
    FFBBConnectionError,
    FFBBNotFoundError,
    FFBBRateLimiter,
)
from custom_components.ffbb_tracker.const import DEFAULT_DIRECTUS_TOKEN


def _mock_response(
    status: int,
    json_data: dict | None = None,
    text: str = "",
    headers: dict | None = None,
):
    """Build a mock aiohttp response usable as an async context manager."""
    response = MagicMock()
    response.status = status
    if headers is not None:
        response.headers = CIMultiDict(headers)
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


@pytest.mark.asyncio
async def test_get_poule_data_requests_gym_postal_code_through_commune():
    """The postal code lives on the commune, not on the salle itself.

    Directus' `ffbbserver_salles` collection has no `codePostal` field
    (it is on `ffbbserver_communes`), so the request must go through the
    `commune` relation -- and must NOT ask for a non-existent
    `salle.codePostal`, which Directus would reject for the whole request.
    """
    session = MagicMock()
    payload = {"data": {"id": "poule-1", "nom": "Poule A"}}
    session.get = MagicMock(return_value=_mock_response(200, payload))
    client = FFBBClient(session)

    await client.get_poule_data("poule-1")

    _, kwargs = session.get.call_args
    fields = kwargs["params"]["fields"].split(",")
    assert "rencontres.salle.commune.codePostal" in fields
    assert "rencontres.salle.codePostal" not in fields


# --- poule request: the normal request is pinned, the refresh variant differs --

# The exact fields of the normal request, in order. A normal poll must keep
# sending precisely this: its answers are the ones known to be up to date, and
# the outdated-response safety net relies on a *different* shape to bypass a
# stored copy. Change this list only on purpose.
NORMAL_POULE_FIELDS = (
    "id",
    "nom",
    "rencontres.id",
    "rencontres.numero",
    "rencontres.numeroJournee",
    "rencontres.resultatEquipe1",
    "rencontres.resultatEquipe2",
    "rencontres.joue",
    "rencontres.nomEquipe1",
    "rencontres.nomEquipe2",
    "rencontres.date_rencontre",
    "rencontres.idEngagementEquipe1.id",
    "rencontres.idEngagementEquipe2.id",
    "rencontres.idOrganismeEquipe1.id",
    "rencontres.idOrganismeEquipe1.nom",
    "rencontres.idOrganismeEquipe1.code",
    "rencontres.idOrganismeEquipe1.logo",
    "rencontres.idOrganismeEquipe2.id",
    "rencontres.idOrganismeEquipe2.nom",
    "rencontres.idOrganismeEquipe2.code",
    "rencontres.idOrganismeEquipe2.logo",
    "rencontres.salle.id",
    "rencontres.salle.libelle",
    "rencontres.salle.adresse",
    "rencontres.salle.commune.libelle",
    "rencontres.salle.commune.codePostal",
    "classements.id",
    "classements.idEngagement.id",
    "classements.idEngagement.nom",
    "classements.idEngagement.idOrganisme.code",
    "classements.matchJoues",
    "classements.points",
    "classements.position",
    "classements.gagnes",
    "classements.perdus",
    "classements.nuls",
    "classements.nombreForfaits",
    "classements.nombreDefauts",
    "classements.penalitesArbitrage",
    "classements.penalitesEntraineur",
    "classements.penalites",
    "classements.quotient",
    "classements.paniersMarques",
    "classements.paniersEncaisses",
    "classements.difference",
)


async def _poule_request(refresh: int | None = None, **response):
    """Run get_poule_data against a mocked session; return (client, call kwargs)."""
    session = MagicMock()
    payload = {"data": response.pop("data", {"id": "poule-1", "nom": "Poule A"})}
    session.get = MagicMock(return_value=_mock_response(200, payload, **response))
    client = FFBBClient(session)
    if refresh is None:
        await client.get_poule_data("poule-1")
    else:
        await client.get_poule_data("poule-1", refresh=refresh)
    return client, session.get.call_args.kwargs


@pytest.mark.asyncio
async def test_normal_poule_request_is_unchanged():
    _, kwargs = await _poule_request()

    params = kwargs["params"]
    assert params["fields"] == ",".join(NORMAL_POULE_FIELDS)
    assert params["deep[rencontres][_limit]"] == 1000
    assert params["deep[rencontres][_sort]"] == "date_rencontre"
    assert params["deep[classements][_limit]"] == 100
    assert params["deep[classements][_sort]"] == "position"
    assert "Cache-Control" not in kwargs["headers"]
    assert "Pragma" not in kwargs["headers"]


@pytest.mark.parametrize("refresh", [0, -3])
@pytest.mark.asyncio
async def test_non_positive_refresh_means_the_normal_request(refresh):
    _, kwargs = await _poule_request(refresh=refresh)

    assert kwargs["params"]["fields"] == ",".join(NORMAL_POULE_FIELDS)
    assert "Cache-Control" not in kwargs["headers"]


@pytest.mark.asyncio
async def test_refresh_request_asks_for_the_same_data_differently():
    _, kwargs = await _poule_request(refresh=490_000)

    refreshed = kwargs["params"]["fields"].split(",")
    assert sorted(refreshed) == sorted(NORMAL_POULE_FIELDS)
    assert refreshed != list(NORMAL_POULE_FIELDS)
    assert kwargs["headers"]["Cache-Control"] == "no-cache"
    assert kwargs["headers"]["Pragma"] == "no-cache"
    # Everything else about the request is unchanged.
    assert kwargs["params"]["deep[rencontres][_limit]"] == 1000
    assert kwargs["params"]["deep[rencontres][_sort]"] == "date_rencontre"


@pytest.mark.asyncio
async def test_successive_refresh_values_give_different_requests():
    """Reusing one shape would just hit the stored copy of the previous
    refresh, so the shape must change with the value passed in (the hour)."""
    shapes = set()
    for refresh in range(1, 500):
        _, kwargs = await _poule_request(refresh=refresh)
        shapes.add(
            (kwargs["params"]["fields"], kwargs["params"]["deep[classements][_limit]"])
        )

    assert len(shapes) == 499


@pytest.mark.asyncio
async def test_refresh_never_makes_the_standings_limit_unsafe():
    """A pool has far fewer than 50 teams, so the varied limit can't truncate."""
    for refresh in range(1, 3000, 7):
        _, kwargs = await _poule_request(refresh=refresh)

        assert 51 <= kwargs["params"]["deep[classements][_limit]"] <= 100


@pytest.mark.asyncio
async def test_refresh_truncation_warning_uses_the_limit_actually_requested(caplog):
    # refresh=1760 -> standings limit 60 (100 - 1760 // 44 % 50)
    data = {"id": "poule-1", "classements": [{"id": str(i)} for i in range(60)]}

    with caplog.at_level(logging.WARNING):
        _, kwargs = await _poule_request(refresh=1760, data=data)

    assert kwargs["params"]["deep[classements][_limit]"] == 60
    assert "hit the API limit (60)" in caplog.text


# --- poule request: cache-related response headers for the diagnostics --------


@pytest.mark.asyncio
async def test_cache_related_response_headers_are_captured():
    client, _ = await _poule_request(
        headers={
            "Age": "3600",
            "Cache-Control": "public, max-age=300",
            "Date": "Fri, 02 Oct 2026 10:00:00 GMT",
            "CF-Cache-Status": "HIT",
            "Set-Cookie": "session=secret",
            "Content-Type": "application/json",
        }
    )

    assert client.last_poule_headers == {
        "normal": {
            "age": "3600",
            "cache-control": "public, max-age=300",
            "date": "Fri, 02 Oct 2026 10:00:00 GMT",
            "cf-cache-status": "HIT",
        }
    }


@pytest.mark.asyncio
async def test_refresh_headers_are_recorded_separately():
    client, _ = await _poule_request(refresh=5, headers={"Age": "0"})

    assert client.last_poule_headers == {"recheck": {"age": "0"}}


@pytest.mark.asyncio
async def test_responses_without_usable_headers_record_an_empty_entry():
    """The default mock has non-string header values: they must be ignored."""
    client, _ = await _poule_request()

    assert client.last_poule_headers == {"normal": {}}


@pytest.mark.asyncio
async def test_last_poule_headers_returns_a_copy():
    client, _ = await _poule_request(headers={"Age": "1"})

    client.last_poule_headers["normal"]["age"] = "tampered"

    assert client.last_poule_headers["normal"]["age"] == "1"


@pytest.mark.asyncio
async def test_extra_headers_survive_the_401_retry():
    """The retry after a token refresh must still ask for a fresh answer, and
    the token refresh request itself must not carry the extra headers."""
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            _mock_response(401),
            _mock_response(200, {"data": {"key_directus_website": "new-token"}}),
            _mock_response(200, {"data": {"id": "ok"}}, headers={"Age": "0"}),
        ]
    )
    client = FFBBClient(session)
    seen: dict[str, str] = {}

    result = await client._request(
        "items/foo",
        extra_headers={"Cache-Control": "no-cache"},
        response_headers=seen,
    )

    calls = session.get.call_args_list
    assert result == {"id": "ok"}
    assert calls[0].kwargs["headers"]["Cache-Control"] == "no-cache"
    assert "Cache-Control" not in calls[1].kwargs["headers"]
    assert calls[2].kwargs["headers"]["Cache-Control"] == "no-cache"
    assert seen == {"age": "0"}


# --- transient HTTP failures: bounded retry, short error messages -----------

SLEEP = "custom_components.ffbb_tracker.api.asyncio.sleep"


def _client_with(*responses, rate_limiter=None) -> tuple[FFBBClient, MagicMock]:
    session = MagicMock()
    session.get = MagicMock(side_effect=list(responses))
    return FFBBClient(session, rate_limiter=rate_limiter), session


@pytest.mark.asyncio
async def test_rate_limited_request_waits_as_long_as_the_server_asks():
    client, session = _client_with(
        _mock_response(429, headers={"Retry-After": "3"}),
        _mock_response(200, {"data": {"id": "ok"}}),
    )

    with patch(SLEEP, new_callable=AsyncMock) as sleep:
        result = await client._request("items/foo")

    assert result == {"id": "ok"}
    sleep.assert_awaited_once_with(3.0)
    assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_unavailable_server_is_retried_with_exponential_backoff():
    client, _ = _client_with(
        _mock_response(503),
        _mock_response(503),
        _mock_response(200, {"data": {"id": "ok"}}),
    )

    with patch(SLEEP, new_callable=AsyncMock) as sleep:
        result = await client._request("items/foo")

    assert result == {"id": "ok"}
    assert [c.args[0] for c in sleep.await_args_list] == [1.0, 2.0]


@pytest.mark.asyncio
async def test_gives_up_after_the_retries_and_reports_the_status():
    client, session = _client_with(
        _mock_response(503, text="Service Unavailable"),
        _mock_response(503, text="Service Unavailable"),
        _mock_response(503, text="Service Unavailable"),
    )

    with (
        patch(SLEEP, new_callable=AsyncMock) as sleep,
        pytest.raises(FFBBApiError, match="HTTP 503: Service Unavailable"),
    ):
        await client._request("items/foo")

    assert session.get.call_count == 3  # the first attempt + 2 retries
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_a_retry_after_beyond_the_cap_fails_at_once_without_waiting():
    """Holding an update for minutes is worse than failing and letting the
    next scheduled poll try again."""
    client, session = _client_with(_mock_response(429, headers={"Retry-After": "120"}))

    with (
        patch(SLEEP, new_callable=AsyncMock) as sleep,
        pytest.raises(FFBBApiError, match=r"HTTP 429 \(Retry-After: 120\)"),
    ):
        await client._request("items/foo")

    sleep.assert_not_awaited()
    assert session.get.call_count == 1


@pytest.mark.asyncio
async def test_retry_after_can_be_an_http_date():
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=5), usegmt=True)
    client, _ = _client_with(
        _mock_response(429, headers={"Retry-After": when}),
        _mock_response(200, {"data": {}}),
    )

    with patch(SLEEP, new_callable=AsyncMock) as sleep:
        await client._request("items/foo")

    waited = sleep.await_args.args[0]
    assert 0 < waited <= 5


@pytest.mark.asyncio
async def test_retry_after_in_the_past_means_no_wait():
    when = format_datetime(datetime.now(UTC) - timedelta(minutes=5), usegmt=True)
    client, _ = _client_with(
        _mock_response(429, headers={"Retry-After": when}),
        _mock_response(200, {"data": {}}),
    )

    with patch(SLEEP, new_callable=AsyncMock) as sleep:
        await client._request("items/foo")

    sleep.assert_awaited_once_with(0.0)


@pytest.mark.parametrize("value", ["soon", "   ", "Mon, 99 Foo 2026"])
@pytest.mark.asyncio
async def test_unusable_retry_after_falls_back_to_the_backoff(value):
    client, _ = _client_with(
        _mock_response(503, headers={"Retry-After": value}),
        _mock_response(200, {"data": {}}),
    )

    with patch(SLEEP, new_callable=AsyncMock) as sleep:
        await client._request("items/foo")

    sleep.assert_awaited_once_with(1.0)


@pytest.mark.parametrize("status", [429, 502, 503, 504])
@pytest.mark.asyncio
async def test_transient_statuses_are_retried(status):
    client, session = _client_with(
        _mock_response(status), _mock_response(200, {"data": {}})
    )

    with patch(SLEEP, new_callable=AsyncMock):
        await client._request("items/foo")

    assert session.get.call_count == 2


@pytest.mark.parametrize("status", [400, 418, 500])
@pytest.mark.asyncio
async def test_other_error_statuses_are_not_retried(status):
    client, session = _client_with(_mock_response(status, text="nope"))

    with (
        patch(SLEEP, new_callable=AsyncMock) as sleep,
        pytest.raises(FFBBApiError, match=f"HTTP {status}: nope"),
    ):
        await client._request("items/foo")

    sleep.assert_not_awaited()
    assert session.get.call_count == 1


@pytest.mark.asyncio
async def test_error_pages_are_reduced_to_a_short_plain_text_message():
    page = "<html><head><title>x</title></head><body><h1>Bad gateway</h1>" + "y" * 5000
    client, _ = _client_with(_mock_response(500, text=page))

    with pytest.raises(FFBBApiError) as err:
        await client._request("items/foo")

    message = str(err.value)
    assert len(message) < 300
    assert "<" not in message
    assert "Bad gateway" in message
    assert message.endswith("…")


@pytest.mark.asyncio
async def test_a_short_plain_error_body_is_kept_as_is():
    client, _ = _client_with(_mock_response(400, text="Invalid query"))

    with pytest.raises(FFBBApiError, match=r"HTTP 400: Invalid query$"):
        await client._request("items/foo")


@pytest.mark.asyncio
async def test_rate_limiter_paces_each_retry():
    limiter = MagicMock()
    limiter.throttle = AsyncMock()
    client, _ = _client_with(
        _mock_response(503),
        _mock_response(503),
        _mock_response(200, {"data": {}}),
        rate_limiter=limiter,
    )

    with patch(SLEEP, new_callable=AsyncMock):
        await client._request("items/foo")

    assert limiter.throttle.await_count == 3


@pytest.mark.asyncio
async def test_every_http_attempt_has_its_own_timeout():
    """The token refresh and the retry used to run inside the first request's
    15 s budget; each attempt must now get its own, never nested."""
    depth = {"now": 0, "max": 0, "count": 0}

    class _SpyTimeout:
        def __init__(self, *_args) -> None:
            pass

        async def __aenter__(self):
            depth["now"] += 1
            depth["count"] += 1
            depth["max"] = max(depth["max"], depth["now"])

        async def __aexit__(self, *_exc):
            depth["now"] -= 1
            return False

    client, _ = _client_with(
        _mock_response(401),
        _mock_response(200, {"data": {"key_directus_website": "new-token"}}),
        _mock_response(200, {"data": {"id": "ok"}}),
    )

    with patch("custom_components.ffbb_tracker.api.asyncio.timeout", _SpyTimeout):
        await client._request("items/foo")

    assert depth["count"] == 3
    assert depth["max"] == 1


@pytest.mark.asyncio
async def test_a_401_after_a_transient_failure_still_refreshes_the_token():
    client, session = _client_with(
        _mock_response(503),
        _mock_response(401),
        _mock_response(200, {"data": {"key_directus_website": "new-token"}}),
        _mock_response(200, {"data": {"id": "ok"}}),
    )

    with patch(SLEEP, new_callable=AsyncMock):
        result = await client._request("items/foo")

    assert result == {"id": "ok"}
    assert session.get.call_count == 4


@pytest.mark.asyncio
async def test_a_retried_request_keeps_its_extra_headers_and_captures_the_answer():
    client, session = _client_with(
        _mock_response(503, headers={"Age": "999"}),
        _mock_response(200, {"data": {"id": "ok"}}, headers={"Age": "0"}),
    )
    seen: dict[str, str] = {}

    with patch(SLEEP, new_callable=AsyncMock):
        await client._request(
            "items/foo",
            extra_headers={"Cache-Control": "no-cache"},
            response_headers=seen,
        )

    for call in session.get.call_args_list:
        assert call.kwargs["headers"]["Cache-Control"] == "no-cache"
    assert seen == {"age": "0"}  # from the successful answer, not the 503
