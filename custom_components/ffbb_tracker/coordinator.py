"""Data update coordinator for the FFBB Tracker integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import FFBBApiError, FFBBClient, FFBBConnectionError, FFBBNotFoundError
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
    LOGO_ASSET_FORMAT,
    LOGO_ASSET_HEIGHT,
    SEASON_ROLLOVER_THRESHOLD_DAYS,
    TOKEN_REFRESH_FAILURE_THRESHOLD,
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
    except ValueError, TypeError:
        return None



def _build_logo_url(base_url: str | None, logo_id: Any) -> str | None:
    """Turn a Directus asset id into an absolute, pre-sized logo URL.

    Returns None if the club has no logo registered with the federation
    (a common case for smaller clubs), or if no base_url is available,
    rather than a broken URL.
    """
    if not base_url or not logo_id or not isinstance(logo_id, str):
        return None
    return (
        f"{base_url}/assets/{logo_id}"
        f"?height={LOGO_ASSET_HEIGHT}&fit=contain&format={LOGO_ASSET_FORMAT}"
    )


def _build_team_url(club_code: Any, engagement_id: Any) -> str | None:
    """Build the official FFBB team URL from club code and engagement ID.

    The official website structure requires:
    https://competitions.ffbb.com/ligues/<ligue>/comites/<comite>/clubs/<club_code>/equipes/<engagement_id>
    where the first 3 characters of the club code represent the league, and characters 3 to 7
    represent the committee code.
    """
    if not club_code or not engagement_id:
        return None
    code_str = str(club_code).strip().lower()
    eng_str = str(engagement_id).strip()
    if len(code_str) < 7 or not eng_str:
        return None
    ligue = code_str[:3]
    comite = code_str[3:7]
    return (
        f"https://competitions.ffbb.com/ligues/{ligue}/comites/{comite}/"
        f"clubs/{code_str}/equipes/{eng_str}"
    )


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
    team_logo_url: str | None
    opponent_logo_url: str | None
    raw: dict[str, Any]
    team_url: str | None = None
    opponent_url: str | None = None
    is_stale: bool = False

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

        # See _handle_not_found(): tracks how long the tracked poule has
        # been returning 404, to distinguish a season rollover from a
        # transient blip.
        self._not_found_since: datetime | None = None

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
        except FFBBNotFoundError as err:
            self._handle_not_found()
            raise UpdateFailed(
                f"Pool or team not found on the FFBB API: {err}"
            ) from err
        except FFBBConnectionError as err:
            raise UpdateFailed(f"Network error connecting to FFBB API: {err}") from err
        except FFBBApiError as err:
            raise UpdateFailed(f"Error communicating with FFBB API: {err}") from err

        self._clear_not_found_issue()
        self._check_token_refresh_health()
        processed = self._process_poule_data(poule_data)
        self.update_interval = self._compute_update_interval(processed)
        return processed

    def _check_token_refresh_health(self) -> None:
        """Raise or clear a repair issue for a persistently failing token refresh.

        The public DEFAULT_DIRECTUS_TOKEN fallback (see const.py) keeps
        polling working even while the dynamic `/items/configuration`
        refresh is failing, so a real problem there (the FFBB API changed
        that endpoint's shape, for example) would otherwise only ever
        surface as a `_LOGGER.warning` on the client -- easy to miss.
        Surfacing it as a repair issue after a few consecutive failures
        makes it visible in the UI instead.
        """
        issue_id = f"token_refresh_failing_{self.engagement_id}"
        # In tests, `self.client` is commonly swapped for a bare AsyncMock()
        # without this attribute set, which would return a Mock rather
        # than an int here -- guard against that instead of assuming a
        # real FFBBClient.
        failures = getattr(self.client, "token_refresh_failures", 0)
        if not isinstance(failures, int):
            failures = 0
        if failures >= TOKEN_REFRESH_FAILURE_THRESHOLD:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="token_refresh_failing",
                translation_placeholders={"team_name": self.team_name},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _handle_not_found(self) -> None:
        """Track a 404 from the FFBB API and raise a repair issue once it
        has persisted for SEASON_ROLLOVER_THRESHOLD_DAYS.

        Scoped to FFBBNotFoundError specifically -- not FFBBConnectionError
        or the generic FFBBApiError -- because a 404 on the tracked
        poule/engagement is the concrete signal that the FFBB has
        reassigned IDs for a new season. A network error or a 500 from the
        API says nothing about whether the team still exists, so those
        must not count towards this threshold.
        """
        now = dt_util.utcnow()
        if self._not_found_since is None:
            self._not_found_since = now
            return

        if now - self._not_found_since >= timedelta(
            days=SEASON_ROLLOVER_THRESHOLD_DAYS
        ):
            # async_create_issue is idempotent: re-raising the same
            # issue_id on every subsequent failed refresh just refreshes
            # it in place rather than duplicating it.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"season_rollover_{self.engagement_id}",
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="season_rollover",
                translation_placeholders={"team_name": self.team_name},
                data={"entry_id": self.config_entry.entry_id},
            )

    def _clear_not_found_issue(self) -> None:
        """Reset the not-found tracker and remove the repair issue, if any.

        Called on every successful refresh, so a team that comes back
        (the 404 was transient, or the user reconfigured to new IDs)
        automatically clears the issue without any manual step.
        """
        if self._not_found_since is not None:
            self._not_found_since = None
            ir.async_delete_issue(
                self.hass, DOMAIN, f"season_rollover_{self.engagement_id}"
            )

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

    def _is_match_live(self, data: FFBBTeamData) -> bool:
        """Return True while the next match is starting soon or awaiting its result.

        Factored out of _compute_update_interval so the same "is this match
        live right now" condition can also back a binary_sensor, instead of
        living only inside the polling-interval decision.
        """
        candidate = data.next_match
        if not candidate or not candidate.match_date:
            return False

        now = dt_util.utcnow()
        time_until = candidate.match_date - now
        time_since = now - candidate.match_date

        starting_soon = (
            timedelta(0) <= time_until <= timedelta(minutes=LIVE_WINDOW_BEFORE_MINUTES)
        )
        awaiting_result = not candidate.is_played and (
            timedelta(0) <= time_since <= timedelta(hours=self._live_window_after_hours)
        )
        return starting_soon or awaiting_result

    def _compute_update_interval(self, data: FFBBTeamData) -> timedelta:
        """Shorten the polling interval automatically around match time.

        The user-configured interval (default 60 min) is fine most of the
        time. When live polling is enabled and next_match is approaching,
        or when it has already started and remains unplayed (awaiting published
        results within the configured window), switch to live scan interval.
        """
        if not self._live_polling_enabled:
            return timedelta(minutes=self._base_interval_minutes)

        if self._is_match_live(data):
            return timedelta(minutes=self._live_scan_interval)

        return timedelta(minutes=self._base_interval_minutes)

    @property
    def is_match_live(self) -> bool:
        """Whether the tracked team's next match is starting soon or awaiting its result.

        Exposed for the "match in progress" binary sensor. Independent of
        whether live_polling is enabled in options, since it's a factual
        statement about the match, not a polling-speed decision.
        """
        if not self.data:
            return False
        return self._is_match_live(self.data)

    @property
    def is_game_day(self) -> bool:
        """Whether the tracked team's next match is scheduled today (local time).

        Exposed for the "game day" binary sensor. Deliberately date-only
        (no time window) so it flips on at local midnight, unlike
        is_match_live which is scoped to the pre/post kickoff window.
        """
        if (
            not self.data
            or not self.data.next_match
            or not self.data.next_match.match_date
        ):
            return False
        match_local = dt_util.as_local(self.data.next_match.match_date)
        return match_local.date() == dt_util.now().date()

    def _process_poule_data(self, data: dict[str, Any]) -> FFBBTeamData:
        """Parse matches and standings for the tracked team."""
        poule_name = data.get("nom", "")
        raw_matches = data.get("rencontres") or []
        raw_standings = data.get("classements") or []

        engagement_to_code: dict[str, str] = {}
        for match in raw_matches:
            raw_org1 = match.get("idOrganismeEquipe1")
            if isinstance(raw_org1, dict) and raw_org1.get("code"):
                eng1 = match.get("idEngagementEquipe1")
                eng1_id = str(
                    eng1.get("id", "") if isinstance(eng1, dict) else eng1 or ""
                )
                if eng1_id:
                    engagement_to_code[eng1_id] = str(raw_org1["code"])

            raw_org2 = match.get("idOrganismeEquipe2")
            if isinstance(raw_org2, dict) and raw_org2.get("code"):
                eng2 = match.get("idEngagementEquipe2")
                eng2_id = str(
                    eng2.get("id", "") if isinstance(eng2, dict) else eng2 or ""
                )
                if eng2_id:
                    engagement_to_code[eng2_id] = str(raw_org2["code"])

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

            parsed_match = self._parse_match(
                match, eq1_id == self.engagement_id, engagement_to_code
            )
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
            if next_match is not None:
                next_match.is_stale = True

        team_standing: TeamStanding | None = None
        parsed_standings: list[dict[str, Any]] = []

        for row in raw_standings:
            raw_engagement = row.get("idEngagement")
            club_code: str | None = None
            if isinstance(raw_engagement, dict):
                row_engagement_id = str(raw_engagement.get("id", ""))
                team_label = raw_engagement.get("nom") or row.get("nomEquipe", "N/A")
                raw_org = raw_engagement.get("idOrganisme")
                if isinstance(raw_org, dict) and raw_org.get("code"):
                    club_code = str(raw_org["code"])
            elif raw_engagement is not None:
                row_engagement_id = str(raw_engagement)
                team_label = row.get("nomEquipe", "N/A")
            else:
                row_engagement_id = ""
                team_label = row.get("nomEquipe", "N/A")

            if not club_code and row_engagement_id:
                club_code = engagement_to_code.get(row_engagement_id)

            # Architectural choice: sanitize Directus string numbers into native integers
            # to guarantee compatibility with Home Assistant sensor state classes (measurement)
            # and avoid operand TypeError during math operations.
            pos = _safe_int(row.get("position"))
            pts = _safe_int(row.get("points"))
            played = _safe_int(row.get("matchJoues"))
            won = _safe_int(row.get("gagnes"))
            lost = _safe_int(row.get("perdus"))
            # Confirmed readable by the public Directus role by directly
            # querying the API (fields=classements.*, same token used
            # elsewhere in this file): it's real data, not derived.
            draws = _safe_int(row.get("nuls"))
            forfeits = _safe_int(row.get("nombreForfaits"))
            defaults = _safe_int(row.get("nombreDefauts"))
            referee_penalties = _safe_int(row.get("penalitesArbitrage"))
            coach_penalties = _safe_int(row.get("penalitesEntraineur"))
            total_penalties = _safe_int(row.get("penalites"))
            points_for = _safe_int(row.get("paniersMarques"))
            points_against = _safe_int(row.get("paniersEncaisses"))
            points_diff = _safe_int(row.get("difference"))
            quotient = row.get("quotient")

            standing_url = _build_team_url(club_code, row_engagement_id)

            standing_entry = {
                "position": pos,
                "team_name": team_label,
                "points": pts,
                "played": played,
                "won": won,
                "lost": lost,
                "draws": draws,
                "forfeits": forfeits,
                "defaults": defaults,
                "referee_penalties": referee_penalties,
                "coach_penalties": coach_penalties,
                "total_penalties": total_penalties,
                "points_for": points_for,
                "points_against": points_against,
                "points_diff": points_diff,
                "quotient": quotient,
                "url": standing_url,
                "team_url": standing_url,
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

    def _parse_match(
        self,
        match: dict[str, Any],
        is_home: bool,
        engagement_to_code: dict[str, str] | None = None,
    ) -> MatchDetails:
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

        raw_org_eq1 = match.get("idOrganismeEquipe1")
        org_eq1: dict[str, Any] = raw_org_eq1 if isinstance(raw_org_eq1, dict) else {}
        raw_org_eq2 = match.get("idOrganismeEquipe2")
        org_eq2: dict[str, Any] = raw_org_eq2 if isinstance(raw_org_eq2, dict) else {}

        code_map = engagement_to_code or {}

        if is_home:
            team_name = match.get("nomEquipe1") or self.team_name
            opponent_name = match.get("nomEquipe2") or "Adversaire"
            opponent_org = org_eq2
            team_org = org_eq1
            team_score = score1
            opponent_score = score2
            opponent_engagement_id = eq2_id
        else:
            team_name = match.get("nomEquipe2") or self.team_name
            opponent_name = match.get("nomEquipe1") or "Adversaire"
            opponent_org = org_eq1
            team_org = org_eq2
            team_score = score2
            opponent_score = score1
            opponent_engagement_id = eq1_id

        base_url = self.client.base_url if self.client else None
        team_logo_url = _build_logo_url(base_url, team_org.get("logo"))
        opponent_logo_url = _build_logo_url(base_url, opponent_org.get("logo"))

        team_code = team_org.get("code") or code_map.get(self.engagement_id)
        opponent_code = opponent_org.get("code") or code_map.get(opponent_engagement_id)

        team_url = _build_team_url(team_code, self.engagement_id)
        opponent_url = _build_team_url(opponent_code, opponent_engagement_id)

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
            team_logo_url=team_logo_url,
            opponent_logo_url=opponent_logo_url,
            raw=match,
            team_url=team_url,
            opponent_url=opponent_url,
        )
