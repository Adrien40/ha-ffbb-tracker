"""Data update coordinator for the FFBB Tracker integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import FFBBApiError, FFBBClient, FFBBConnectionError
from .const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_LIVE_POLLING,
    CONF_LIVE_SCAN_INTERVAL,
    CONF_LIVE_WINDOW_AFTER_HOURS,
    CONF_POULE_ID,
    CONF_SCAN_INTERVAL,
    CONF_TEAM_NAME,
    DEFAULT_LIVE_POLLING,
    DEFAULT_LIVE_SCAN_INTERVAL,
    DEFAULT_LIVE_WINDOW_AFTER_HOURS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LIVE_WINDOW_BEFORE_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

# How long a fetched poule payload may be reused by another coordinator
# tracking a different team in the *same* poule, instead of triggering a
# second identical request to the FFBB API.
_POULE_CACHE_TTL: Final = timedelta(seconds=45)


def _safe_int(value: Any) -> int | None:
    """Safely convert a value to int or return None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


class _PouleCacheEntry:
    """Holds the most recent poule payload fetched by any coordinator.

    Several tracked teams can belong to the same poule; without this,
    each of their coordinators would independently fetch the exact same
    ~1000-fixture payload from the FFBB API on every refresh cycle.
    """

    __slots__ = ("data", "fetched_at", "lock")

    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.fetched_at: datetime | None = None
        self.lock = asyncio.Lock()


def _get_poule_cache(hass: HomeAssistant, poule_id: str) -> _PouleCacheEntry:
    """Return the shared cache entry for a poule, creating it if needed."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    poule_caches: dict[str, _PouleCacheEntry] = domain_data.setdefault(
        "_poule_cache", {}
    )
    return poule_caches.setdefault(poule_id, _PouleCacheEntry())


@dataclass
class MatchDetails:
    """Class representing processed match information."""

    match_id: str
    match_number: str
    round_number: str
    match_date: datetime | None
    is_home: bool
    team_name: str
    opponent_name: str
    opponent_club_id: str | None
    is_played: bool
    team_score: int | None
    opponent_score: int | None
    result: str | None
    gym_name: str | None
    gym_address: str | None
    gym_postal_code: str | None
    gym_city: str | None
    raw: dict[str, Any]

    @property
    def formatted_address(self) -> str | None:
        """Return the gym's full address as a single formatted string, or None."""
        parts = [
            part
            for part in (
                self.gym_name,
                self.gym_address,
                self.gym_postal_code,
                self.gym_city,
            )
            if part
        ]
        return ", ".join(parts) if parts else None


@dataclass
class TeamStanding:
    """Class representing team position in pool standings."""

    position: int | None
    points: int | None
    played: int | None
    won: int | None
    lost: int | None
    raw: dict[str, Any]


@dataclass
class FFBBTeamData:
    """Class holding all processed coordinator data for a team."""

    engagement_id: str
    poule_id: str
    team_name: str
    competition_name: str
    poule_name: str
    next_match: MatchDetails | None
    last_match: MatchDetails | None
    team_standing: TeamStanding | None
    standings: list[dict[str, Any]]
    fixtures: list[MatchDetails]


class FFBBDataUpdateCoordinator(DataUpdateCoordinator[FFBBTeamData]):
    """Coordinator to fetch and process FFBB team data at regular intervals."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: FFBBClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.engagement_id = str(entry.data[CONF_ENGAGEMENT_ID])
        self.poule_id = str(entry.data[CONF_POULE_ID])
        self.team_name = str(entry.data[CONF_TEAM_NAME])
        self.competition_name = str(entry.data[CONF_COMPETITION_NAME])

        self._base_interval_minutes = entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        self._live_polling_enabled = entry.options.get(
            CONF_LIVE_POLLING, DEFAULT_LIVE_POLLING
        )
        self._live_scan_interval = entry.options.get(
            CONF_LIVE_SCAN_INTERVAL, DEFAULT_LIVE_SCAN_INTERVAL
        )
        self._live_window_after_hours = entry.options.get(
            CONF_LIVE_WINDOW_AFTER_HOURS, DEFAULT_LIVE_WINDOW_AFTER_HOURS
        )

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.engagement_id}",
            update_interval=timedelta(minutes=self._base_interval_minutes),
        )

    async def _async_update_data(self) -> FFBBTeamData:
        """Fetch fixtures and standings from the FFBB API."""
        try:
            poule_data = await self._async_fetch_poule_data()
        except FFBBConnectionError as err:
            raise UpdateFailed(f"Network error connecting to FFBB API: {err}") from err
        except FFBBApiError as err:
            raise UpdateFailed(f"Error communicating with FFBB API: {err}") from err

        processed = self._process_poule_data(poule_data)
        self.update_interval = self._compute_update_interval(processed)
        return processed

    async def _async_fetch_poule_data(self) -> dict[str, Any]:
        """Fetch poule data, reusing a very recent fetch from a sibling team.

        Several config entries can point at the same poule_id (e.g. two age
        groups of the same club engaged in the same pool). This avoids
        firing near-duplicate requests at the FFBB API within the same
        refresh window.
        """
        cache = _get_poule_cache(self.hass, self.poule_id)
        async with cache.lock:
            now = dt_util.utcnow()
            is_stale = (
                cache.data is None
                or cache.fetched_at is None
                or (now - cache.fetched_at) > _POULE_CACHE_TTL
            )
            if is_stale:
                cache.data = await self.client.get_poule_data(self.poule_id)
                cache.fetched_at = now

            if cache.data is None:
                # Should be unreachable: get_poule_data() either raises
                # (FFBBApiError/FFBBConnectionError) or returns a dict, so
                # cache.data can't still be None here. Raising explicitly
                # instead of relying on `assert` avoids a confusing
                # AttributeError downstream if this ever changes, and
                # survives Python running with optimizations (-O), which
                # strips asserts.
                raise FFBBApiError(
                    f"No poule data available for poule {self.poule_id} "
                    "after fetch attempt"
                )
            return cache.data

    def _compute_update_interval(self, data: FFBBTeamData) -> timedelta:
        """Shorten the polling interval automatically around match time.

        The user-configured interval (default 60 min) is fine most of the
        time. When live polling is enabled and next_match is approaching,
        or when it has already started and remains unplayed (awaiting published
        results within the configured window), switch to live scan interval.
        """
        if not self._live_polling_enabled:
            return timedelta(minutes=self._base_interval_minutes)

        now = dt_util.utcnow()
        candidate = data.next_match

        if candidate and candidate.match_date:
            time_until = candidate.match_date - now
            time_since = now - candidate.match_date
            starting_soon = (
                timedelta(0)
                <= time_until
                <= timedelta(minutes=LIVE_WINDOW_BEFORE_MINUTES)
            )
            awaiting_result = not candidate.is_played and (
                timedelta(0)
                <= time_since
                <= timedelta(hours=self._live_window_after_hours)
            )

            if starting_soon or awaiting_result:
                return timedelta(minutes=self._live_scan_interval)

        return timedelta(minutes=self._base_interval_minutes)

    def _process_poule_data(self, data: dict[str, Any]) -> FFBBTeamData:
        """Parse matches and standings for the tracked team."""
        poule_name = data.get("nom", "")
        raw_matches = data.get("rencontres") or []
        raw_standings = data.get("classements") or []

        team_matches: list[MatchDetails] = []
        for match in raw_matches:
            eq1_engagement = match.get("idEngagementEquipe1")
            eq2_engagement = match.get("idEngagementEquipe2")

            eq1_id = (
                str(eq1_engagement.get("id", ""))
                if isinstance(eq1_engagement, dict)
                else str(eq1_engagement or "")
            )
            eq2_id = (
                str(eq2_engagement.get("id", ""))
                if isinstance(eq2_engagement, dict)
                else str(eq2_engagement or "")
            )

            if self.engagement_id not in (eq1_id, eq2_id):
                continue

            parsed_match = self._parse_match(match, eq1_id == self.engagement_id)
            team_matches.append(parsed_match)

        team_matches.sort(
            key=lambda item: item.match_date or datetime.max.replace(tzinfo=UTC)
        )

        now = dt_util.utcnow()
        last_match: MatchDetails | None = None
        next_match: MatchDetails | None = None

        for match in team_matches:
            if match.is_played:
                last_match = match
            elif next_match is None and (
                match.match_date is None
                or match.match_date >= (now - timedelta(hours=3))
            ):
                next_match = match

        # Architectural choice: if no unplayed match falls within the normal
        # "now - 3h" window (e.g. a postponed/cancelled match left unplayed
        # far in the past, with no newer fixture yet scheduled), fall back to
        # the earliest unplayed match regardless of date. Showing a possibly
        # stale next_match is preferable to leaving the sensor at None, which
        # users read as a broken entity rather than an awaiting-reschedule state.
        if next_match is None:
            next_match = next(
                (match for match in team_matches if not match.is_played), None
            )

        team_standing: TeamStanding | None = None
        parsed_standings: list[dict[str, Any]] = []

        for row in raw_standings:
            raw_engagement = row.get("idEngagement")
            if isinstance(raw_engagement, dict):
                row_engagement_id = str(raw_engagement.get("id", ""))
                team_label = raw_engagement.get("nom") or row.get("nomEquipe", "N/A")
            elif raw_engagement is not None:
                row_engagement_id = str(raw_engagement)
                team_label = row.get("nomEquipe", "N/A")
            else:
                row_engagement_id = ""
                team_label = row.get("nomEquipe", "N/A")

            # Architectural choice: sanitize Directus string numbers into native integers
            # to guarantee compatibility with Home Assistant sensor state classes (measurement)
            # and avoid operand TypeError during math operations.
            pos = _safe_int(row.get("position"))
            pts = _safe_int(row.get("points"))
            played = _safe_int(row.get("matchJoues"))
            won = _safe_int(row.get("gagnes"))
            lost = _safe_int(row.get("perdus"))

            standing_entry = {
                "position": pos,
                "team_name": team_label,
                "points": pts,
                "played": played,
                "won": won,
                "lost": lost,
            }
            parsed_standings.append(standing_entry)

            if row_engagement_id == self.engagement_id:
                team_standing = TeamStanding(
                    position=pos,
                    points=pts,
                    played=played,
                    won=won,
                    lost=lost,
                    raw=row,
                )

        return FFBBTeamData(
            engagement_id=self.engagement_id,
            poule_id=self.poule_id,
            team_name=self.team_name,
            competition_name=self.competition_name,
            poule_name=poule_name,
            next_match=next_match,
            last_match=last_match,
            team_standing=team_standing,
            standings=parsed_standings,
            fixtures=team_matches,
        )

    def _parse_match(self, match: dict[str, Any], is_home: bool) -> MatchDetails:
        """Parse raw match dictionary into a MatchDetails structure."""
        match_id = str(match.get("id", ""))
        match_number = str(match.get("numero", ""))
        round_number = str(match.get("numeroJournee", ""))

        raw_date = match.get("date_rencontre")
        match_date: datetime | None = None
        if raw_date:
            parsed_dt = dt_util.parse_datetime(raw_date)
            if parsed_dt:
                match_date = dt_util.as_utc(parsed_dt)

        is_played = bool(match.get("joue", False))
        score1 = _safe_int(match.get("resultatEquipe1"))
        score2 = _safe_int(match.get("resultatEquipe2"))

        # The API is expected to set "joue" for completed matches, including
        # 0-0 walkovers/forfeits. As a safety net for payloads where that
        # flag is missing but a non-zero final score is already present,
        # treat the match as played too.
        if (
            not is_played
            and score1 is not None
            and score2 is not None
            and (score1 > 0 or score2 > 0)
        ):
            is_played = True

        if is_home:
            team_name = match.get("nomEquipe1") or self.team_name
            opponent_name = match.get("nomEquipe2") or "Adversaire"
            raw_org = match.get("idOrganismeEquipe2")
            opponent_org: dict[str, Any] = raw_org if isinstance(raw_org, dict) else {}
            team_score = score1
            opponent_score = score2
        else:
            team_name = match.get("nomEquipe2") or self.team_name
            opponent_name = match.get("nomEquipe1") or "Adversaire"
            raw_org = match.get("idOrganismeEquipe1")
            opponent_org = raw_org if isinstance(raw_org, dict) else {}
            team_score = score2
            opponent_score = score1

        result: str | None = None
        if is_played and team_score is not None and opponent_score is not None:
            if team_score > opponent_score:
                result = "win"
            elif team_score < opponent_score:
                result = "loss"
            else:
                result = "draw"

        raw_salle = match.get("salle")
        salle: dict[str, Any] = raw_salle if isinstance(raw_salle, dict) else {}

        raw_commune = salle.get("commune")
        commune: dict[str, Any] = raw_commune if isinstance(raw_commune, dict) else {}

        return MatchDetails(
            match_id=match_id,
            match_number=match_number,
            round_number=round_number,
            match_date=match_date,
            is_home=is_home,
            team_name=team_name,
            opponent_name=opponent_name,
            opponent_club_id=str(opponent_org.get("id"))
            if opponent_org.get("id")
            else None,
            is_played=is_played,
            team_score=team_score,
            opponent_score=opponent_score,
            result=result,
            gym_name=salle.get("libelle") or salle.get("nom"),
            gym_address=salle.get("adresse"),
            gym_postal_code=salle.get("codePostal"),
            gym_city=commune.get("libelle"),
            raw=match,
        )
