"""API client for FFBB Directus REST endpoints with optional rate-limiting."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

import aiohttp

from .const import API_BASE_URL, DEFAULT_DIRECTUS_TOKEN, DEFAULT_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# Modern Chrome user agent to bypass front-end bot filtering
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ORIGIN_URL = "https://competitions.ffbb.com"
REFERER_URL = "https://competitions.ffbb.com/"

# Default spacing between outbound requests when a rate limiter is active
DEFAULT_RATE_LIMIT_DELAY = 0.5

# Response headers kept for diagnostics: they tell whether an answer came from
# a cache, and how old it is.
_DIAGNOSTIC_RESPONSE_HEADERS: Final = (
    "age",
    "cache-control",
    "date",
    "etag",
    "expires",
    "last-modified",
    "vary",
    "via",
    "x-cache",
    "cf-cache-status",
)

# Request headers asking servers and intermediaries not to serve a stored copy.
NO_CACHE_HEADERS: Final = {"Cache-Control": "no-cache", "Pragma": "no-cache"}

# Fields requested for a poule, in the order of the normal request. Keep this
# exact order for normal requests: their answers are known to be up to date
# (see FFBBClient.get_poule_data's `refresh` argument).
_POULE_FIELDS: Final = (
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


class FFBBApiError(Exception):
    """Base exception for FFBB API errors."""


class FFBBConnectionError(FFBBApiError):
    """Exception raised when connection to the API fails."""


class FFBBNotFoundError(FFBBApiError):
    """Exception raised when a requested resource is not found."""


class FFBBRateLimiter:
    """Rate limiter to space out requests across coordinators."""

    def __init__(self, delay: float = DEFAULT_RATE_LIMIT_DELAY) -> None:
        """Initialize the rate limiter with a minimum delay in seconds."""
        self.delay = delay
        self._last_request_timestamp: float = 0.0
        self._lock = asyncio.Lock()

    async def throttle(self) -> None:
        """Wait if necessary to ensure minimum delay between requests."""
        if self.delay <= 0:
            return

        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            elapsed = now - self._last_request_timestamp
            if elapsed < self.delay:
                await asyncio.sleep(self.delay - elapsed)
            self._last_request_timestamp = loop.time()


class FFBBClient:
    """Async API client for FFBB competitions platform."""

    # Directus silently truncates results past `_limit` instead of erroring,
    # so a full page is our only signal that fixtures/standings might be
    # missing from a get_poule_data() response.
    _RENCONTRES_LIMIT = 1000
    _CLASSEMENTS_LIMIT = 100

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str = API_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        rate_limiter: FFBBRateLimiter | None = None,
    ) -> None:
        """Initialize the client with an existing session and an optional rate limiter."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._rate_limiter = rate_limiter
        self._token: str = DEFAULT_DIRECTUS_TOKEN
        self._auth_lock = asyncio.Lock()
        self._token_refresh_failures: int = 0
        # Poule IDs already warned about hitting a pagination limit, so the
        # warning fires once per outage instead of on every successful poll
        # while a poule stays above the limit (discarded once it drops
        # back below, so a later re-occurrence warns again).
        self._warned_rencontres_limit: set[str] = set()
        self._warned_classements_limit: set[str] = set()
        # Cache-related headers of the last poule answers ("normal" request,
        # and "recheck" request of the outdated-response safety net).
        self._last_poule_headers: dict[str, dict[str, str]] = {}

    @property
    def last_poule_headers(self) -> dict[str, dict[str, str]]:
        """Return the cache-related headers of the last poule answers.

        Keyed by request kind: "normal" and, when the safety net ran,
        "recheck". Exposed in the diagnostics.
        """
        return {
            kind: dict(headers) for kind, headers in self._last_poule_headers.items()
        }

    @property
    def token_refresh_failures(self) -> int:
        """Return the number of consecutive failed dynamic token refreshes.

        Reset to 0 as soon as `/items/configuration` is reachable and
        returns a usable token again. Used by the coordinator to raise a
        repair issue if the integration has been silently running on the
        hardcoded DEFAULT_DIRECTUS_TOKEN fallback for too long -- which
        would otherwise only surface as a `_LOGGER.warning` on refresh.
        """
        return self._token_refresh_failures

    @property
    def base_url(self) -> str:
        """Return the configured Directus API base URL (no trailing slash).

        Exposed publicly so callers building derived URLs -- e.g. the
        `/assets/<id>` logo URLs in coordinator.py -- reuse the exact same
        host as the rest of the client instead of hardcoding a second one.
        """
        return self._base_url

    async def _refresh_token(self) -> None:
        """Fetch updated public Directus token from configuration endpoint."""
        async with self._auth_lock:
            try:
                data = await self._request(
                    "items/configuration", auth=False, retry=False
                )
                if isinstance(data, dict):
                    self._token = (
                        data.get("key_directus_website")
                        or data.get("key_dh")
                        or DEFAULT_DIRECTUS_TOKEN
                    )
                    self._token_refresh_failures = 0
            except (
                FFBBApiError,
                aiohttp.ClientError,
                TimeoutError,
                KeyError,
                TypeError,
                ValueError,
            ) as err:
                # _request() itself already re-wraps aiohttp.ClientError,
                # TimeoutError, and malformed-JSON ValueErrors into
                # FFBBConnectionError/FFBBApiError before they get here --
                # so FFBBApiError is what actually needs catching for this
                # to be the best-effort refresh it's meant to be. The raw
                # exception types are kept too, in case _request's own
                # wrapping is ever bypassed or extended.
                self._token_refresh_failures += 1
                _LOGGER.warning("Could not refresh token dynamically: %s", err)

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        retry: bool = True,
        extra_headers: dict[str, str] | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> Any:
        """Execute a GET request against the Directus API with automatic retry.

        `extra_headers` are added to the request. When `response_headers` is
        given, the cache-related headers of the answer are copied into it.
        """
        if self._rate_limiter:
            await self._rate_limiter.throttle()

        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
            "Origin": ORIGIN_URL,
            "Referer": REFERER_URL,
        }

        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if extra_headers:
            headers.update(extra_headers)

        try:
            async with (
                asyncio.timeout(DEFAULT_TIMEOUT),
                self._session.get(url, params=params, headers=headers) as response,
            ):
                if response.status == 404:
                    raise FFBBNotFoundError(f"Resource not found at {url}")

                if response.status in (401, 403) and auth and retry:
                    _LOGGER.debug(
                        "Received HTTP %s, refreshing token and retrying",
                        response.status,
                    )
                    await self._refresh_token()
                    return await self._request(
                        endpoint,
                        params=params,
                        auth=True,
                        retry=False,
                        extra_headers=extra_headers,
                        response_headers=response_headers,
                    )

                if response.status != 200:
                    text = await response.text()
                    raise FFBBApiError(
                        f"FFBB API returned HTTP {response.status}: {text}"
                    )

                if response_headers is not None:
                    for name in _DIAGNOSTIC_RESPONSE_HEADERS:
                        value = response.headers.get(name)
                        if isinstance(value, str):
                            response_headers[name] = value

                payload = await response.json()
                if not isinstance(payload, dict):
                    raise FFBBApiError(
                        f"Unexpected response shape from FFBB API at {url}: "
                        f"expected a JSON object, got {type(payload).__name__}"
                    )
                return payload.get("data")
        except TimeoutError as err:
            raise FFBBConnectionError(
                f"Timeout while connecting to FFBB API: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise FFBBConnectionError(
                f"Network error while connecting to FFBB API: {err}"
            ) from err
        except ValueError as err:
            # aiohttp raises this (a plain ValueError, not aiohttp.ClientError)
            # when the body has a JSON content-type but isn't valid JSON --
            # e.g. a proxy/captive-portal error page served with the wrong
            # header. Without this, it would propagate as an unhandled
            # exception, bypassing DataUpdateCoordinator's single-log-per-
            # outage behavior and logging on every failed poll instead.
            raise FFBBApiError(
                f"FFBB API returned a malformed JSON response from {url}: {err}"
            ) from err

    async def search_clubs(self, query: str, limit: int = 15) -> list[dict[str, Any]]:
        """Search for clubs by name or official code."""
        endpoint = "items/ffbbserver_organismes"
        clean_query = query.strip()
        params = {
            "filter[_or][0][nom][_icontains]": clean_query,
            "filter[_or][1][code][_icontains]": clean_query,
            "fields": "id,nom,code",
            "limit": limit,
            "sort": "nom",
        }
        data = await self._request(endpoint, params=params)
        return data if isinstance(data, list) else []

    async def get_club_engagements(self, organisme_id: str) -> list[dict[str, Any]]:
        """Fetch all registered teams (engagements) for a given club."""
        endpoint = "items/ffbbserver_engagements"
        params = {
            "filter[idOrganisme][_eq]": organisme_id,
            "fields": (
                "id,nom,numeroEquipe,"
                "idCompetition.id,idCompetition.nom,idCompetition.code,"
                "idPoule.id,idPoule.nom"
            ),
            "limit": 100,
            "sort": "nom",
        }
        data = await self._request(endpoint, params=params)
        return data if isinstance(data, list) else []

    async def get_engagement(self, engagement_id: str) -> dict[str, Any]:
        """Fetch a single engagement by its unique ID."""
        endpoint = f"items/ffbbserver_engagements/{engagement_id}"
        params = {
            "fields": (
                "id,nom,numeroEquipe,"
                "idOrganisme.id,idOrganisme.nom,idOrganisme.code,idOrganisme.logo,"
                "idCompetition.id,idCompetition.nom,idCompetition.code,"
                "idPoule.id,idPoule.nom"
            )
        }
        data = await self._request(endpoint, params=params)
        if not isinstance(data, dict):
            raise FFBBNotFoundError(f"Engagement {engagement_id} not found")
        return data

    async def get_poule_data(
        self, poule_id: str, *, refresh: int = 0
    ) -> dict[str, Any]:
        """Fetch fixtures and standings for a specific pool.

        The default request is the one every poll uses. A non-zero `refresh`
        asks for the same data in a differently formulated request -- the same
        fields in another order, a slightly different standings limit, and
        `Cache-Control: no-cache` -- so that a stored copy of the normal
        request cannot be served back. The coordinator uses it as a safety net
        when a match result seems to be missing (the FFBB API once kept
        serving a poule snapshot that predated published scores for one
        request shape while answering correctly for another). Pass a value
        that changes over time (e.g. the hour of the day) so successive
        requests do not repeat the same shape.
        """
        endpoint = f"items/ffbbserver_poules/{poule_id}"
        ordered_fields = list(_POULE_FIELDS)
        classements_limit = self._CLASSEMENTS_LIMIT
        extra_headers: dict[str, str] | None = None
        if refresh > 0:
            rotation = 1 + refresh % (len(ordered_fields) - 1)
            ordered_fields = ordered_fields[rotation:] + ordered_fields[:rotation]
            classements_limit -= (refresh // (len(ordered_fields) - 1)) % 50
            extra_headers = dict(NO_CACHE_HEADERS)
        params = {
            "fields": ",".join(ordered_fields),
            "deep[rencontres][_limit]": self._RENCONTRES_LIMIT,
            "deep[rencontres][_sort]": "date_rencontre",
            "deep[classements][_limit]": classements_limit,
            "deep[classements][_sort]": "position",
        }
        headers_seen: dict[str, str] = {}
        data = await self._request(
            endpoint,
            params=params,
            extra_headers=extra_headers,
            response_headers=headers_seen,
        )
        if not isinstance(data, dict):
            raise FFBBNotFoundError(f"Pool {poule_id} not found")
        self._last_poule_headers["recheck" if refresh > 0 else "normal"] = headers_seen

        rencontres = data.get("rencontres")
        if isinstance(rencontres, list) and len(rencontres) >= self._RENCONTRES_LIMIT:
            if poule_id not in self._warned_rencontres_limit:
                _LOGGER.warning(
                    "Fixture list for pool %s hit the API limit (%d); some "
                    "matches may be missing from this response",
                    poule_id,
                    self._RENCONTRES_LIMIT,
                )
                self._warned_rencontres_limit.add(poule_id)
        else:
            self._warned_rencontres_limit.discard(poule_id)

        classements = data.get("classements")
        if isinstance(classements, list) and len(classements) >= classements_limit:
            if poule_id not in self._warned_classements_limit:
                _LOGGER.warning(
                    "Standings for pool %s hit the API limit (%d); some teams "
                    "may be missing from this response",
                    poule_id,
                    classements_limit,
                )
                self._warned_classements_limit.add(poule_id)
        else:
            self._warned_classements_limit.discard(poule_id)

        return data
