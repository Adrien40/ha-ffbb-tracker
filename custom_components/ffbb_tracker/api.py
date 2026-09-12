"""API client for FFBB Directus REST endpoints with optional rate-limiting."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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
            except (
                aiohttp.ClientError,
                TimeoutError,
                KeyError,
                TypeError,
                ValueError,
            ) as err:
                _LOGGER.warning("Could not refresh token dynamically: %s", err)

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        retry: bool = True,
    ) -> Any:
        """Execute a GET request against the Directus API with automatic retry."""
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
                        endpoint, params=params, auth=True, retry=False
                    )

                if response.status != 200:
                    text = await response.text()
                    raise FFBBApiError(
                        f"FFBB API returned HTTP {response.status}: {text}"
                    )

                payload = await response.json()
                return payload.get("data")
        except TimeoutError as err:
            raise FFBBConnectionError(
                f"Timeout while connecting to FFBB API: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise FFBBConnectionError(
                f"Network error while connecting to FFBB API: {err}"
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
                "idOrganisme.id,idOrganisme.nom,idOrganisme.code,"
                "idCompetition.id,idCompetition.nom,idCompetition.code,"
                "idPoule.id,idPoule.nom"
            )
        }
        data = await self._request(endpoint, params=params)
        if not isinstance(data, dict):
            raise FFBBNotFoundError(f"Engagement {engagement_id} not found")
        return data

    async def get_poule_data(self, poule_id: str) -> dict[str, Any]:
        """Fetch fixtures and standings for a specific pool."""
        endpoint = f"items/ffbbserver_poules/{poule_id}"
        params = {
            "fields": (
                "id,nom,"
                "rencontres.id,rencontres.numero,rencontres.numeroJournee,"
                "rencontres.resultatEquipe1,rencontres.resultatEquipe2,rencontres.joue,"
                "rencontres.nomEquipe1,rencontres.nomEquipe2,rencontres.date_rencontre,"
                "rencontres.idEngagementEquipe1.id,rencontres.idEngagementEquipe2.id,"
                "rencontres.idOrganismeEquipe1.id,rencontres.idOrganismeEquipe1.nom,"
                "rencontres.idOrganismeEquipe2.id,rencontres.idOrganismeEquipe2.nom,"
                "rencontres.salle.id,rencontres.salle.libelle,rencontres.salle.adresse,"
                "rencontres.salle.commune.libelle,"
                "classements.id,classements.idEngagement.id,classements.idEngagement.nom,"
                "classements.matchJoues,classements.points,classements.position,"
                "classements.gagnes,classements.perdus"
            ),
            "deep[rencontres][_limit]": self._RENCONTRES_LIMIT,
            "deep[rencontres][_sort]": "date_rencontre",
            "deep[classements][_limit]": self._CLASSEMENTS_LIMIT,
            "deep[classements][_sort]": "position",
        }
        data = await self._request(endpoint, params=params)
        if not isinstance(data, dict):
            raise FFBBNotFoundError(f"Pool {poule_id} not found")

        rencontres = data.get("rencontres")
        if isinstance(rencontres, list) and len(rencontres) >= self._RENCONTRES_LIMIT:
            _LOGGER.warning(
                "Fixture list for pool %s hit the API limit (%d); some "
                "matches may be missing from this response",
                poule_id,
                self._RENCONTRES_LIMIT,
            )

        classements = data.get("classements")
        if (
            isinstance(classements, list)
            and len(classements) >= self._CLASSEMENTS_LIMIT
        ):
            _LOGGER.warning(
                "Standings for pool %s hit the API limit (%d); some teams "
                "may be missing from this response",
                poule_id,
                self._CLASSEMENTS_LIMIT,
            )

        return data
