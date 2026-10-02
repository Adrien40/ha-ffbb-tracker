"""Tests for the FFBB Tracker DataUpdateCoordinator.

These tests target the pure parsing logic (_process_poule_data,
_parse_match), the live-polling speedup logic, and the poule cache shared
across coordinators tracking the same pool. They exist primarily to catch
silent breakage: if the Directus API response schema changes (field
renamed, nesting changed), matches would simply stop matching the tracked
engagement_id and every sensor would silently show `unknown` with no error
raised anywhere. This suite pins down the exact field names the parser
depends on so such a regression fails loudly in CI instead of in a user's
log.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, call

import pytest
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ffbb_tracker.api import (
    FFBBApiError,
    FFBBConnectionError,
    FFBBNotFoundError,
)
from custom_components.ffbb_tracker.const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_TEAM_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LIVE_SCAN_INTERVAL,
    SEASON_ROLLOVER_THRESHOLD_DAYS,
)
from custom_components.ffbb_tracker.coordinator import (
    _POULE_CACHE_TTL,
    FFBBDataUpdateCoordinator,
    _count_results,
    _engagement_id,
    _get_poule_cache,
    _is_played,
)


def _make_coordinator(hass) -> FFBBDataUpdateCoordinator:
    """Build a coordinator wired to engagement-123 / Basket Landes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENGAGEMENT_ID: "engagement-123",
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry.add_to_hass(hass)
    return FFBBDataUpdateCoordinator(hass, client=None, entry=entry)


class _StubClient:
    """Minimal stand-in for FFBBClient exposing only what logo-URL
    construction needs, without spinning up a real aiohttp session."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url


def _make_coordinator_with_client(hass, base_url: str) -> FFBBDataUpdateCoordinator:
    """Same as _make_coordinator, but wired to a stub client with a real
    base_url -- needed to exercise logo URL construction, which is a no-op
    when client is None (see _build_logo_url)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENGAGEMENT_ID: "engagement-123",
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: "Basket Landes",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry.add_to_hass(hass)
    return FFBBDataUpdateCoordinator(hass, client=_StubClient(base_url), entry=entry)


def test_filters_out_matches_from_other_engagements(hass, sample_poule_data):
    """Only rencontres involving the tracked engagement_id are kept."""
    coordinator = _make_coordinator(hass)

    result = coordinator._process_poule_data(sample_poule_data)

    match_ids = {m.match_id for m in result.fixtures}
    assert match_ids == {"match-1", "match-2"}
    assert "match-3" not in match_ids


def test_identifies_next_and_last_match(hass, sample_poule_data):
    """The played match becomes last_match, the unplayed one next_match."""
    coordinator = _make_coordinator(hass)

    result = coordinator._process_poule_data(sample_poule_data)

    assert result.last_match is not None
    assert result.last_match.match_id == "match-1"
    assert result.last_match.is_played is True

    assert result.next_match is not None
    assert result.next_match.match_id == "match-2"
    assert result.next_match.is_played is False


def test_home_away_and_score_orientation(hass, sample_poule_data):
    """team_score/opponent_score must be oriented from the tracked team's side.

    engagement-123 is équipe1 in match-1 (home) and équipe2 in match-2
    (away) — the parser must flip score1/score2 accordingly.
    """
    coordinator = _make_coordinator(hass)
    result = coordinator._process_poule_data(sample_poule_data)

    last = result.last_match
    assert last.is_home is True
    assert last.team_score == 68
    assert last.opponent_score == 54
    assert last.result == "win"
    assert last.opponent_name == "US Mont-de-Marsan"

    nxt = result.next_match
    assert nxt.is_home is False
    assert nxt.opponent_name == "Stade Montois"


def test_match_date_parsed_and_converted_to_utc(hass, sample_poule_data):
    """date_rencontre (ISO 8601, local offset) is parsed and stored as UTC."""
    coordinator = _make_coordinator(hass)
    result = coordinator._process_poule_data(sample_poule_data)

    match_date = result.last_match.match_date
    assert isinstance(match_date, datetime)
    assert match_date.tzinfo is not None
    # 2026-01-10T20:00:00+01:00 -> 19:00 UTC
    assert match_date.astimezone(UTC).hour == 19


def test_gym_fields_extracted_from_nested_salle(hass, sample_poule_data):
    """salle.libelle/adresse/codePostal/commune.libelle map to gym_* fields."""
    coordinator = _make_coordinator(hass)
    result = coordinator._process_poule_data(sample_poule_data)

    last = result.last_match
    assert last.gym_name == "Gymnase Andre Chavanne"
    assert last.gym_address == "1 rue du Stade"
    assert last.gym_postal_code == "40000"
    assert last.gym_city == "Mont-de-Marsan"

    assert last.formatted_address == (
        "Gymnase Andre Chavanne, 1 rue du Stade, 40000, Mont-de-Marsan"
    )


def test_gym_missing_returns_none_formatted_address(hass):
    """A match with no salle data must not crash and formatted_address is None."""
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-x",
            "numero": "1",
            "numeroJournee": "1",
            "resultatEquipe1": None,
            "resultatEquipe2": None,
            "joue": False,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire Inconnu",
            "date_rencontre": None,
            "idOrganismeEquipe2": None,
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.gym_name is None
    assert parsed.formatted_address is None
    assert parsed.match_date is None


def test_logo_urls_built_from_client_base_url(hass, sample_poule_data):
    """team_logo_url/opponent_logo_url are built from FFBBClient.base_url,
    oriented to the correct side of the fixture (home vs away)."""
    coordinator = _make_coordinator_with_client(hass, "https://api.ffbb.app")
    result = coordinator._process_poule_data(sample_poule_data)

    # match-1: engagement-123 is équipe1 (home) -> team=org-1, opponent=org-2
    last = result.last_match
    assert last.team_logo_url == (
        "https://api.ffbb.app/assets/team-logo-uuid?height=220&fit=contain&format=avif"
    )
    assert last.opponent_logo_url == (
        "https://api.ffbb.app/assets/opponent-logo-uuid"
        "?height=220&fit=contain&format=avif"
    )

    # match-2: engagement-123 is équipe2 (away) -> team is still org-1,
    # regardless of which side of the fixture it's listed on.
    nxt = result.next_match
    assert nxt.team_logo_url == (
        "https://api.ffbb.app/assets/team-logo-uuid?height=220&fit=contain&format=avif"
    )


def test_logo_url_none_when_club_has_no_logo_registered(hass, sample_poule_data):
    """A club with no `logo` field in the API response (common for smaller
    clubs) must resolve to None, not a broken URL."""
    coordinator = _make_coordinator_with_client(hass, "https://api.ffbb.app")
    result = coordinator._process_poule_data(sample_poule_data)

    # match-2's opponent (org-3, Stade Montois) has no "logo" key at all.
    assert result.next_match.opponent_logo_url is None


def test_logo_url_none_when_client_is_none(hass):
    """Coordinators built without a real client (as in most of this test
    module, to exercise pure parsing logic) must not crash and simply
    resolve no logos, rather than raising on self.client.base_url."""
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-x",
            "numero": "1",
            "numeroJournee": "1",
            "resultatEquipe1": None,
            "resultatEquipe2": None,
            "joue": False,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire",
            "date_rencontre": None,
            "idOrganismeEquipe1": {"id": "org-1", "logo": "some-uuid"},
            "idOrganismeEquipe2": None,
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.team_logo_url is None
    assert parsed.opponent_logo_url is None


@pytest.mark.parametrize(
    ("club_code", "engagement_id", "expected"),
    [
        (
            "NAQ0040141",
            "200000005334722",
            "https://competitions.ffbb.com/ligues/naq/comites/0040/clubs/naq0040141/equipes/200000005334722",
        ),
        (
            "idf0075001",
            "12345",
            "https://competitions.ffbb.com/ligues/idf/comites/0075/clubs/idf0075001/equipes/12345",
        ),
        ("", "12345", None),
        (None, "12345", None),
        ("NAQ0040141", "", None),
        ("NAQ0040141", None, None),
        ("SHORT", "12345", None),
    ],
)
def test_build_team_url(club_code, engagement_id, expected):
    """_build_team_url builds the full official hierarchy or returns None."""
    from custom_components.ffbb_tracker.coordinator import _build_team_url

    assert _build_team_url(club_code, engagement_id) == expected


def test_team_and_opponent_urls_built_for_matches(hass):
    """team_url and opponent_url are built from official FFBB website hierarchy."""
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-urls",
            "numero": "1",
            "numeroJournee": "1",
            "resultatEquipe1": None,
            "resultatEquipe2": None,
            "joue": False,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Stade Montois",
            "idEngagementEquipe1": {"id": "engagement-123"},
            "idEngagementEquipe2": {"id": "engagement-456"},
            "idOrganismeEquipe1": {"id": "org-1", "code": "NAQ0040141"},
            "idOrganismeEquipe2": {"id": "org-2", "code": "NAQ0040002"},
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.team_url == (
        "https://competitions.ffbb.com/ligues/naq/comites/0040/clubs/naq0040141/equipes/engagement-123"
    )
    assert parsed.opponent_url == (
        "https://competitions.ffbb.com/ligues/naq/comites/0040/clubs/naq0040002/equipes/engagement-456"
    )


def test_opponent_url_none_when_engagement_or_code_missing(hass):
    """When a fixture lacks opponent engagement id or club code, opponent_url is None."""
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-no-opp",
            "numero": "1",
            "numeroJournee": "1",
            "resultatEquipe1": None,
            "resultatEquipe2": None,
            "joue": False,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire",
            "idEngagementEquipe1": {"id": "engagement-123"},
            "idEngagementEquipe2": None,
            "idOrganismeEquipe1": {"id": "org-1", "code": "NAQ0040141"},
            "idOrganismeEquipe2": None,
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.team_url == (
        "https://competitions.ffbb.com/ligues/naq/comites/0040/clubs/naq0040141/equipes/engagement-123"
    )
    assert parsed.opponent_url is None


def test_standings_builds_team_url_when_code_available(hass):
    """Standings rows construct full team URLs when organism code is available."""
    coordinator = _make_coordinator(hass)
    data = {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": [
            {
                "id": "match-1",
                "idEngagementEquipe1": {"id": "engagement-flat"},
                "idOrganismeEquipe1": {"id": "org-flat", "code": "NAQ0040141"},
                "idEngagementEquipe2": {"id": "engagement-123"},
                "idOrganismeEquipe2": {"id": "org-1", "code": "NAQ0040001"},
            }
        ],
        "classements": [
            {
                "id": "rank-flat",
                "idEngagement": "engagement-flat",
                "nomEquipe": "Flat Team",
                "matchJoues": 1,
                "points": 2,
                "position": 1,
                "gagnes": 1,
                "perdus": 0,
            }
        ],
    }
    result = coordinator._process_poule_data(data)
    flat_row = result.standings[0]
    assert flat_row["url"] == (
        "https://competitions.ffbb.com/ligues/naq/comites/0040/clubs/naq0040141/equipes/engagement-flat"
    )
    assert flat_row["team_url"] == (
        "https://competitions.ffbb.com/ligues/naq/comites/0040/clubs/naq0040141/equipes/engagement-flat"
    )


def test_standings_reads_club_code_from_nested_idorganisme(hass):
    """When a classement row's idEngagement is a dict that itself embeds
    idOrganisme.code, that nested code must be used directly for the
    standing's team_url -- without needing the rencontres-derived
    engagement_to_code fallback map at all.
    """
    coordinator = _make_coordinator(hass)
    data = {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": [],
        "classements": [
            {
                "id": "rank-1",
                "idEngagement": {
                    "id": "engagement-nested",
                    "nom": "Nested Org Team",
                    "idOrganisme": {"id": "org-nested", "code": "NAQ0040999"},
                },
                "nomEquipe": "Nested Org Team",
                "matchJoues": 2,
                "points": 4,
                "position": 1,
                "gagnes": 2,
                "perdus": 0,
            }
        ],
    }

    result = coordinator._process_poule_data(data)

    row = result.standings[0]
    assert row["team_name"] == "Nested Org Team"
    assert row["url"] == (
        "https://competitions.ffbb.com/ligues/naq/comites/0040/"
        "clubs/naq0040999/equipes/engagement-nested"
    )
    assert row["team_url"] == row["url"]


def test_standings_handles_non_dict_idengagement_shapes(hass):
    """Directus sometimes returns idEngagement as a nested dict, but can
    also flatten it to a plain id string, or omit it entirely. Only the
    dict shape was exercised by existing tests (via sample_poule_data);
    this pins down the other two so a future schema change doesn't
    silently break standings parsing without a test catching it.
    """
    coordinator = _make_coordinator(hass)
    data = {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": [],
        "classements": [
            {
                "id": "rank-flat",
                "idEngagement": "engagement-flat",
                "nomEquipe": "Flat Team",
                "matchJoues": 1,
                "points": 2,
                "position": 3,
                "gagnes": 1,
                "perdus": 0,
            },
            {
                "id": "rank-missing",
                "idEngagement": None,
                "nomEquipe": "No Engagement Team",
                "matchJoues": 1,
                "points": 0,
                "position": 4,
                "gagnes": 0,
                "perdus": 1,
            },
        ],
    }

    result = coordinator._process_poule_data(data)

    flat_row = next(s for s in result.standings if s["team_name"] == "Flat Team")
    assert flat_row["position"] == 3
    assert flat_row["url"] is None
    assert flat_row["team_url"] is None

    missing_row = next(
        s for s in result.standings if s["team_name"] == "No Engagement Team"
    )
    assert missing_row["position"] == 4
    assert missing_row["url"] is None
    assert missing_row["team_url"] is None


def test_standings_matches_tracked_engagement(hass, sample_poule_data):
    """team_standing is populated only for the row matching engagement_id."""
    coordinator = _make_coordinator(hass)
    result = coordinator._process_poule_data(sample_poule_data)

    assert result.team_standing is not None
    assert result.team_standing.position == 1
    assert result.team_standing.points == 6
    assert result.team_standing.won == 3
    assert result.team_standing.lost == 0

    assert len(result.standings) == 2


def test_standings_builds_logo_url_from_nested_idorganisme(hass):
    """A classement row whose idEngagement.idOrganisme carries its own
    "logo" id builds logo_url directly from it, the same way team_url is
    built from that row's own code (test_standings_reads_club_code_from_
    nested_idorganisme) -- no rencontres fallback needed."""
    coordinator = _make_coordinator_with_client(hass, "https://api.ffbb.app")
    data = {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": [],
        "classements": [
            {
                "id": "rank-1",
                "idEngagement": {
                    "id": "engagement-nested",
                    "nom": "Nested Org Team",
                    "idOrganisme": {
                        "id": "org-nested",
                        "code": "NAQ0040999",
                        "logo": "club-logo-uuid",
                    },
                },
                "nomEquipe": "Nested Org Team",
                "matchJoues": 2,
                "points": 4,
                "position": 1,
                "gagnes": 2,
                "perdus": 0,
            }
        ],
    }

    result = coordinator._process_poule_data(data)

    row = result.standings[0]
    assert row["logo_url"] == (
        "https://api.ffbb.app/assets/club-logo-uuid?height=220&fit=contain&format=avif"
    )


def test_standings_falls_back_to_rencontres_logo_when_row_lacks_one(hass):
    """When a classement row's own idOrganisme has no "logo" (a common
    Directus shape -- see test_standings_builds_team_url_when_code_
    available for the same pattern with club_code), logo_url falls back
    to the org objects on this team's own matches, keyed by engagement id."""
    coordinator = _make_coordinator_with_client(hass, "https://api.ffbb.app")
    data = {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": [
            {
                "id": "match-1",
                "idEngagementEquipe1": {"id": "engagement-flat"},
                "idOrganismeEquipe1": {
                    "id": "org-flat",
                    "code": "NAQ0040141",
                    "logo": "flat-team-logo-uuid",
                },
                "idEngagementEquipe2": {"id": "engagement-123"},
                "idOrganismeEquipe2": {"id": "org-1", "code": "NAQ0040001"},
            }
        ],
        "classements": [
            {
                "id": "rank-flat",
                "idEngagement": "engagement-flat",  # flat id, no nested org at all
                "nomEquipe": "Flat Team",
                "matchJoues": 1,
                "points": 2,
                "position": 1,
                "gagnes": 1,
                "perdus": 0,
            }
        ],
    }

    result = coordinator._process_poule_data(data)

    flat_row = result.standings[0]
    assert flat_row["logo_url"] == (
        "https://api.ffbb.app/assets/flat-team-logo-uuid"
        "?height=220&fit=contain&format=avif"
    )


def test_standings_logo_url_none_when_club_has_no_logo_registered(hass):
    """A club with a code but no "logo" field (common for smaller clubs,
    same case as the match-level test_logo_url_none_when_club_has_no_
    logo_registered) resolves to None, not a broken URL."""
    coordinator = _make_coordinator_with_client(hass, "https://api.ffbb.app")
    data = {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": [],
        "classements": [
            {
                "id": "rank-1",
                "idEngagement": {
                    "id": "engagement-nested",
                    "idOrganisme": {"id": "org-nested", "code": "NAQ0040999"},
                },
                "nomEquipe": "No Logo Team",
                "matchJoues": 1,
                "points": 2,
                "position": 1,
            }
        ],
    }

    result = coordinator._process_poule_data(data)

    assert result.standings[0]["logo_url"] is None


def test_standings_logo_url_none_when_client_is_none(hass):
    """Coordinators built without a real client (base_url unavailable)
    resolve no standings logos either, mirroring the match-level
    test_logo_url_none_when_client_is_none, rather than raising."""
    coordinator = _make_coordinator(hass)
    data = {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": [],
        "classements": [
            {
                "id": "rank-1",
                "idEngagement": {
                    "id": "engagement-nested",
                    "idOrganisme": {
                        "id": "org-nested",
                        "code": "NAQ0040999",
                        "logo": "some-uuid",
                    },
                },
                "nomEquipe": "Some Team",
                "matchJoues": 1,
                "points": 2,
                "position": 1,
            }
        ],
    }

    result = coordinator._process_poule_data(data)

    assert result.standings[0]["logo_url"] is None


def test_draw_result_when_scores_equal(hass):
    """A tied, played match is classified as 'draw', not left as None."""
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-draw",
            "numero": "5",
            "numeroJournee": "5",
            "resultatEquipe1": 70,
            "resultatEquipe2": 70,
            "joue": True,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire",
            "date_rencontre": "2026-02-01T20:00:00+01:00",
            "idOrganismeEquipe2": {"id": "org-x", "nom": "Adversaire"},
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.result == "draw"
    assert parsed.is_played is True


def test_loss_result_when_score_lower(hass):
    """A played match with a lower score for the tracked team must resolve
    to 'loss' via the real _parse_match() scoring logic.

    Every other existing test covering the 'loss' outcome (e.g. in
    test_sensor.py) builds a MatchDetails by hand with result='loss'
    already set, bypassing this comparison entirely -- so a swapped
    </ > operator here would previously have gone undetected.
    """
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-loss",
            "numero": "6",
            "numeroJournee": "6",
            "resultatEquipe1": 55,
            "resultatEquipe2": 70,
            "joue": True,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire",
            "date_rencontre": "2026-02-08T20:00:00+01:00",
            "idOrganismeEquipe2": {"id": "org-x", "nom": "Adversaire"},
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.result == "loss"
    assert parsed.is_played is True


def test_is_played_safety_net_when_joue_false_but_score_present(hass):
    """`joue: False` with a real, non-zero score already posted (e.g. a
    forfeit/walkover the FFBB forgot to flag as played) must still be
    treated as played, so the match doesn't get stuck forever as
    `next_match` instead of moving to `last_match`.
    """
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-forfeit",
            "numero": "3",
            "numeroJournee": "3",
            "resultatEquipe1": 20,
            "resultatEquipe2": 0,
            "joue": False,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire",
            "date_rencontre": "2026-01-15T20:00:00+01:00",
            "idOrganismeEquipe2": {"id": "org-x", "nom": "Adversaire"},
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.is_played is True
    assert parsed.result == "win"


def test_is_played_stays_false_when_joue_false_and_no_score(hass):
    """The safety net must only fire when a real score is present -- it must
    NOT blanket-override `joue: False` for matches that simply haven't been
    played yet (no score at all).
    """
    coordinator = _make_coordinator(hass)
    parsed = coordinator._parse_match(
        {
            "id": "match-upcoming",
            "numero": "4",
            "numeroJournee": "4",
            "resultatEquipe1": None,
            "resultatEquipe2": None,
            "joue": False,
            "nomEquipe1": "Basket Landes",
            "nomEquipe2": "Adversaire",
            "date_rencontre": "2026-03-01T20:00:00+01:00",
            "idOrganismeEquipe2": {"id": "org-x", "nom": "Adversaire"},
            "salle": None,
        },
        is_home=True,
    )
    assert parsed.is_played is False
    assert parsed.result is None


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, None),
        ("", None),
        ("42", 42),
        (42, 42),
        ("not-a-number", None),
    ],
)
def test_safe_int_handles_malformed_scores(raw_value, expected):
    """_safe_int must never raise on unexpected API payload values."""
    from custom_components.ffbb_tracker.coordinator import _safe_int

    assert _safe_int(raw_value) == expected


def _minimal_poule(rencontre: dict) -> dict:
    """Wrap a single rencontre dict into a full poule payload."""
    return {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": [rencontre],
        "classements": [],
    }


def test_next_match_fallback_when_result_not_yet_published(hass):
    """A match whose scheduled time is long past but is still marked
    `joue: False` (FFBB hasn't published the result yet) must surface as
    next_match instead of vanishing from both next_match and last_match.
    """
    coordinator = _make_coordinator(hass)
    stale_match = {
        "id": "match-old",
        "numero": "1",
        "numeroJournee": "1",
        "resultatEquipe1": None,
        "resultatEquipe2": None,
        "joue": False,
        "nomEquipe1": "Basket Landes",
        "nomEquipe2": "Adversaire",
        "date_rencontre": "2020-01-01T20:00:00+01:00",
        "idEngagementEquipe1": {"id": "engagement-123"},
        "idEngagementEquipe2": {"id": "engagement-456"},
        "idOrganismeEquipe2": {"id": "org-2", "nom": "Adversaire"},
        "salle": None,
    }

    result = coordinator._process_poule_data(_minimal_poule(stale_match))

    assert result.last_match is None
    assert result.next_match is not None
    assert result.next_match.match_id == "match-old"


def test_compute_update_interval_speeds_up_near_match_time(hass):
    """A match starting within the live window triggers fast polling."""
    coordinator = _make_coordinator(hass)
    soon = datetime.now(UTC) + timedelta(minutes=30)
    upcoming_match = {
        "id": "match-soon",
        "numero": "1",
        "numeroJournee": "1",
        "resultatEquipe1": None,
        "resultatEquipe2": None,
        "joue": False,
        "nomEquipe1": "Basket Landes",
        "nomEquipe2": "Adversaire",
        "date_rencontre": soon.isoformat(),
        "idEngagementEquipe1": {"id": "engagement-123"},
        "idEngagementEquipe2": {"id": "engagement-456"},
        "idOrganismeEquipe2": {"id": "org-2", "nom": "Adversaire"},
        "salle": None,
    }

    data = coordinator._process_poule_data(_minimal_poule(upcoming_match))

    assert coordinator._compute_update_interval(data) == timedelta(
        minutes=LIVE_SCAN_INTERVAL
    )


def test_compute_update_interval_awaiting_result_still_fast(hass):
    """A match that just ended and awaits a published result also polls fast."""
    coordinator = _make_coordinator(hass)
    just_ended = datetime.now(UTC) - timedelta(hours=1)
    recent_match = {
        "id": "match-recent",
        "numero": "1",
        "numeroJournee": "1",
        "resultatEquipe1": None,
        "resultatEquipe2": None,
        "joue": False,
        "nomEquipe1": "Basket Landes",
        "nomEquipe2": "Adversaire",
        "date_rencontre": just_ended.isoformat(),
        "idEngagementEquipe1": {"id": "engagement-123"},
        "idEngagementEquipe2": {"id": "engagement-456"},
        "idOrganismeEquipe2": {"id": "org-2", "nom": "Adversaire"},
        "salle": None,
    }

    data = coordinator._process_poule_data(_minimal_poule(recent_match))

    assert coordinator._compute_update_interval(data) == timedelta(
        minutes=LIVE_SCAN_INTERVAL
    )


def test_compute_update_interval_defaults_far_from_any_match(hass):
    """With no match nearby, the configured base interval is used untouched."""
    coordinator = _make_coordinator(hass)
    data = coordinator._process_poule_data(
        {"id": "poule-1", "nom": "Poule A", "rencontres": [], "classements": []}
    )

    assert coordinator._compute_update_interval(data) == timedelta(
        minutes=DEFAULT_SCAN_INTERVAL
    )


def test_compute_update_interval_ignores_live_window_when_disabled(hass):
    """With live polling turned off, the base interval must be used even
    for a match starting within the live window -- the early return must
    fire before _is_match_live() is ever consulted.
    """
    coordinator = _make_coordinator(hass)
    coordinator._live_polling_enabled = False
    soon = datetime.now(UTC) + timedelta(minutes=30)
    upcoming_match = {
        "id": "match-soon",
        "numero": "1",
        "numeroJournee": "1",
        "resultatEquipe1": None,
        "resultatEquipe2": None,
        "joue": False,
        "nomEquipe1": "Basket Landes",
        "nomEquipe2": "Adversaire",
        "date_rencontre": soon.isoformat(),
        "idEngagementEquipe1": {"id": "engagement-123"},
        "idEngagementEquipe2": {"id": "engagement-456"},
        "idOrganismeEquipe2": {"id": "org-2", "nom": "Adversaire"},
        "salle": None,
    }

    data = coordinator._process_poule_data(_minimal_poule(upcoming_match))

    assert coordinator._compute_update_interval(data) == timedelta(
        minutes=DEFAULT_SCAN_INTERVAL
    )


# ---------------------------------------------------------------------------
# Shared poule cache
# ---------------------------------------------------------------------------


async def test_poule_cache_shared_across_coordinators_same_poule(hass):
    """Two coordinators tracking the same poule must reuse one fetch.

    Without the shared cache, each team's coordinator would independently
    request the exact same ~1000-fixture payload from the FFBB API.
    """
    coordinator_a = _make_coordinator(hass)

    entry_b = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENGAGEMENT_ID: "engagement-456",
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: "US Mont-de-Marsan",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-2",
        },
    )
    entry_b.add_to_hass(hass)
    coordinator_b = FFBBDataUpdateCoordinator(hass, client=None, entry=entry_b)

    payload = {"id": "poule-1", "nom": "Poule A", "rencontres": [], "classements": []}
    coordinator_a.client = AsyncMock()
    coordinator_a.client.get_poule_data = AsyncMock(return_value=payload)
    coordinator_b.client = AsyncMock()
    coordinator_b.client.get_poule_data = AsyncMock(return_value=payload)

    data_a = await coordinator_a._async_fetch_poule_data()
    data_b = await coordinator_b._async_fetch_poule_data()

    assert data_a is data_b
    assert coordinator_a.client.get_poule_data.call_count == 1
    assert coordinator_b.client.get_poule_data.call_count == 0


async def test_poule_cache_independent_per_poule(hass):
    """Coordinators tracking *different* poules must never share a cache entry."""
    coordinator_a = _make_coordinator(hass)

    entry_b = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENGAGEMENT_ID: "engagement-789",
            CONF_POULE_ID: "poule-2",
            CONF_TEAM_NAME: "Autre Équipe",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-3",
        },
    )
    entry_b.add_to_hass(hass)
    coordinator_b = FFBBDataUpdateCoordinator(hass, client=None, entry=entry_b)

    coordinator_a.client = AsyncMock()
    coordinator_a.client.get_poule_data = AsyncMock(
        return_value={
            "id": "poule-1",
            "nom": "A",
            "rencontres": [],
            "classements": [],
        }
    )
    coordinator_b.client = AsyncMock()
    coordinator_b.client.get_poule_data = AsyncMock(
        return_value={
            "id": "poule-2",
            "nom": "B",
            "rencontres": [],
            "classements": [],
        }
    )

    data_a = await coordinator_a._async_fetch_poule_data()
    data_b = await coordinator_b._async_fetch_poule_data()

    assert data_a["nom"] == "A"
    assert data_b["nom"] == "B"
    assert coordinator_a.client.get_poule_data.call_count == 1
    assert coordinator_b.client.get_poule_data.call_count == 1


async def test_poule_cache_refetches_after_ttl_expires(hass):
    """A cache entry older than _POULE_CACHE_TTL must trigger a fresh fetch."""
    coordinator = _make_coordinator(hass)
    coordinator.client = AsyncMock()
    coordinator.client.get_poule_data = AsyncMock(
        side_effect=[
            {"id": "poule-1", "nom": "First", "rencontres": [], "classements": []},
            {"id": "poule-1", "nom": "Second", "rencontres": [], "classements": []},
        ]
    )

    first = await coordinator._async_fetch_poule_data()
    assert first["nom"] == "First"

    cache = _get_poule_cache(hass, coordinator.poule_id)
    cache.fetched_at = datetime.now(UTC) - _POULE_CACHE_TTL - timedelta(seconds=1)

    second = await coordinator._async_fetch_poule_data()
    assert second["nom"] == "Second"
    assert coordinator.client.get_poule_data.call_count == 2


async def test_poule_cache_lock_prevents_concurrent_duplicate_fetch(hass):
    """Two concurrent refreshes for the same poule must still hit the API once.

    This is the scenario the asyncio.Lock exists for: two coordinators (or
    two refresh cycles) racing to populate a cold cache at the same time.
    """
    coordinator = _make_coordinator(hass)
    call_count = 0

    async def _slow_fetch(poule_id):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return {"id": poule_id, "nom": "Slow", "rencontres": [], "classements": []}

    coordinator.client = AsyncMock()
    coordinator.client.get_poule_data = _slow_fetch

    results = await asyncio.gather(
        coordinator._async_fetch_poule_data(),
        coordinator._async_fetch_poule_data(),
    )

    assert call_count == 1
    assert results[0] is results[1]


# ---------------------------------------------------------------------------
# Season-rollover repair issue
# ---------------------------------------------------------------------------
#
# The FFBB reassigns engagement/poule IDs at the start of every new season,
# so a 404 on the tracked poule isn't a bug -- it's an expected, if
# unannounced, event. These tests pin down that a single 404 doesn't yet
# alarm the user (a transient blip must not raise an issue), that a 404
# persisting past the threshold does, that a generic connection error is
# never counted towards that threshold, and that the issue clears itself
# automatically once the team is found again.


def _minimal_payload() -> dict:
    """A minimal valid get_poule_data() response, no matches or standings."""
    return {"id": "poule-1", "nom": "Poule A", "rencontres": [], "classements": []}


def _issue_id(coordinator: FFBBDataUpdateCoordinator) -> str:
    """Return the repair-issue id used for this coordinator's team."""
    return f"season_rollover_{coordinator.engagement_id}"


def _token_issue_id(coordinator: FFBBDataUpdateCoordinator) -> str:
    """Return the repair-issue id used for this coordinator's token health."""
    return f"token_refresh_failing_{coordinator.engagement_id}"


def test_check_token_refresh_health_raises_issue_past_threshold(hass):
    """Once the client reports token_refresh_failures at or above
    TOKEN_REFRESH_FAILURE_THRESHOLD, a token_refresh_failing repair issue
    must be raised with the team name available to the translated title.
    """
    coordinator = _make_coordinator(hass)
    coordinator.client = AsyncMock()
    coordinator.client.token_refresh_failures = 3

    coordinator._check_token_refresh_health()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, _token_issue_id(coordinator))
    assert issue is not None
    assert issue.translation_key == "token_refresh_failing"
    assert issue.translation_placeholders == {"team_name": coordinator.team_name}


def test_check_token_refresh_health_clears_issue_below_threshold(hass):
    """A successful refresh (failures back below the threshold) must clear
    any previously-raised token_refresh_failing issue.
    """
    coordinator = _make_coordinator(hass)
    coordinator.client = AsyncMock()
    coordinator.client.token_refresh_failures = 3
    coordinator._check_token_refresh_health()
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, _token_issue_id(coordinator))
        is not None
    )

    coordinator.client.token_refresh_failures = 0
    coordinator._check_token_refresh_health()

    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, _token_issue_id(coordinator)) is None
    )


async def test_single_not_found_does_not_raise_issue(hass):
    """One 404 must only start the tracker, not raise an issue yet -- a
    single failed refresh could just be a transient API hiccup.
    """
    coordinator = _make_coordinator(hass)
    coordinator.client = AsyncMock()
    coordinator.client.get_poule_data = AsyncMock(side_effect=FFBBNotFoundError("gone"))

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator._not_found_since is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(coordinator)) is None


async def test_not_found_raises_issue_once_threshold_elapsed(hass):
    """A 404 that has persisted for SEASON_ROLLOVER_THRESHOLD_DAYS must
    raise a season_rollover repair issue, with the team name available to
    the translated title/description.
    """
    coordinator = _make_coordinator(hass)
    coordinator.client = AsyncMock()
    coordinator.client.get_poule_data = AsyncMock(side_effect=FFBBNotFoundError("gone"))

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    # Backdate the first-seen timestamp past the threshold, same technique
    # as test_poule_cache_refetches_after_ttl_expires above.
    coordinator._not_found_since = dt_util.utcnow() - timedelta(
        days=SEASON_ROLLOVER_THRESHOLD_DAYS, seconds=1
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(coordinator))
    assert issue is not None
    assert issue.translation_key == "season_rollover"
    assert issue.translation_placeholders == {"team_name": coordinator.team_name}


async def test_not_found_issue_cleared_on_next_successful_refresh(hass):
    """Once the team is found again (reconfigured, or the 404 was
    transient after all), the next successful refresh must remove the
    issue automatically -- no manual dismissal required.
    """
    coordinator = _make_coordinator(hass)
    coordinator.client = AsyncMock()
    coordinator.client.get_poule_data = AsyncMock(side_effect=FFBBNotFoundError("gone"))

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    coordinator._not_found_since = dt_util.utcnow() - timedelta(
        days=SEASON_ROLLOVER_THRESHOLD_DAYS, seconds=1
    )
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(coordinator)) is not None
    )

    coordinator.client.get_poule_data = AsyncMock(return_value=_minimal_payload())
    await coordinator._async_update_data()

    assert coordinator._not_found_since is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(coordinator)) is None


async def test_connection_error_does_not_count_towards_season_rollover(hass):
    """A generic connection/network error must NOT be tracked towards the
    season-rollover threshold: only a 404 on the tracked resource signals
    that the team/poule ID no longer exists. A timeout says nothing about
    that and must not, even after many retries, raise this issue.
    """
    coordinator = _make_coordinator(hass)
    coordinator.client = AsyncMock()
    coordinator.client.get_poule_data = AsyncMock(
        side_effect=FFBBConnectionError("timeout")
    )

    for _ in range(3):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    assert coordinator._not_found_since is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(coordinator)) is None


async def test_api_error_wrapped_as_update_failed(hass):
    """A generic FFBBApiError (e.g. malformed JSON from Directus) must be
    wrapped as UpdateFailed too, same as FFBBConnectionError, so the
    coordinator surfaces it as a normal 'unavailable' state instead of an
    unhandled exception -- and must not count towards season rollover
    either, for the same reason a connection error doesn't.
    """
    coordinator = _make_coordinator(hass)
    coordinator.client = AsyncMock()
    coordinator.client.get_poule_data = AsyncMock(
        side_effect=FFBBApiError("malformed response")
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator._not_found_since is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(coordinator)) is None


def test_standings_rows_expose_draws_from_the_api(hass):
    """Every standings row's `draws` comes straight from the API's `nuls`
    field, so the dashboard card can fill its "N" column -- nothing derived
    or guessed, since the FFBB API communicates this directly."""
    coordinator = _make_coordinator(hass)
    data = {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": [],
        "classements": [
            {
                "idEngagement": "engagement-123",
                "nomEquipe": "Basket Landes",
                "position": 1,
                "points": 5,
                "matchJoues": 3,
                "gagnes": 2,
                "perdus": 1,
                "nuls": "0",
            },
            {
                "idEngagement": "engagement-other",
                "nomEquipe": "No Counts Yet",
                "position": 2,
                "points": 0,
            },
        ],
    }
    result = coordinator._process_poule_data(data)

    # Second row has no "nuls" key at all: None, same as any other missing
    # Directus field -- no local guesswork substituted for it.
    assert [row["draws"] for row in result.standings] == [0, None]
    assert result.standings[0]["won"] == 2
    assert result.standings[0]["lost"] == 1
    assert result.standings[0]["played"] == 3


# --- gym postal code (lives on salle.commune, not salle) ---------------------


def test_gym_postal_code_is_read_from_the_commune(hass):
    """The postal code must come from `salle.commune.codePostal`.

    That is where the FFBB API puts it; `ffbbserver_salles` has no
    `codePostal` field of its own.
    """
    coordinator = _make_coordinator(hass)
    rencontre = {
        "id": "match-cp",
        "numero": "1",
        "numeroJournee": "1",
        "joue": False,
        "nomEquipe1": "Basket Landes",
        "nomEquipe2": "Adversaire",
        "date_rencontre": "2030-01-10T20:00:00+01:00",
        "idEngagementEquipe1": {"id": "engagement-123"},
        "idEngagementEquipe2": {"id": "engagement-456"},
        "salle": {
            "libelle": "Gymnase Test",
            "adresse": "2 avenue de la Gare",
            "commune": {"libelle": "Dax", "codePostal": "40100"},
        },
    }

    data = coordinator._process_poule_data(_minimal_poule(rencontre))

    assert data.next_match is not None
    assert data.next_match.gym_postal_code == "40100"
    assert data.next_match.formatted_address == (
        "Gymnase Test, 2 avenue de la Gare, 40100, Dax"
    )


# --- live window beyond the 3 h next_match grace period ----------------------


def _unplayed_rencontre(match_id: str, started: datetime) -> dict:
    """Return an unplayed rencontre for the tracked team, starting at `started`."""
    return {
        "id": match_id,
        "numero": "1",
        "numeroJournee": "1",
        "resultatEquipe1": None,
        "resultatEquipe2": None,
        "joue": False,
        "nomEquipe1": "Basket Landes",
        "nomEquipe2": "Adversaire",
        "date_rencontre": started.isoformat(),
        "idEngagementEquipe1": {"id": "engagement-123"},
        "idEngagementEquipe2": {"id": "engagement-456"},
        "idOrganismeEquipe2": {"id": "org-2", "nom": "Adversaire"},
        "salle": None,
    }


def test_live_window_beyond_three_hours_keeps_match_as_next_match(hass):
    """With a 5 h window, a match that started 4 h ago must still count.

    Previously next_match dropped any unplayed match older than a hardcoded
    3 h, so windows of 4-6 h (allowed by the options flow) silently did
    nothing: the match vanished from next_match, is_match_live went False
    and polling fell back to the slow interval while the result was still
    awaited.
    """
    coordinator = _make_coordinator(hass)
    coordinator._live_window_after_hours = 5
    started = datetime.now(UTC) - timedelta(hours=4)

    data = coordinator._process_poule_data(
        _minimal_poule(_unplayed_rencontre("match-long", started))
    )

    assert data.next_match is not None
    assert data.next_match.match_id == "match-long"
    assert data.next_match.is_stale is False
    assert coordinator._is_match_live(data) is True
    assert coordinator._compute_update_interval(data) == timedelta(
        minutes=LIVE_SCAN_INTERVAL
    )


def test_default_window_still_drops_match_after_three_hours(hass):
    """With the default 3 h window nothing changes: after 3 h the unplayed
    match is no longer 'live' and only survives as a stale fallback."""
    coordinator = _make_coordinator(hass)
    started = datetime.now(UTC) - timedelta(hours=4)

    data = coordinator._process_poule_data(
        _minimal_poule(_unplayed_rencontre("match-old", started))
    )

    assert data.next_match is not None
    assert data.next_match.is_stale is True
    assert coordinator._is_match_live(data) is False
    assert coordinator._compute_update_interval(data) == timedelta(
        minutes=DEFAULT_SCAN_INTERVAL
    )


def test_short_live_window_does_not_shrink_next_match_grace(hass):
    """A 1 h live window must not make next_match drop the match after 1 h:
    the 3 h display grace is a floor, only longer windows extend it."""
    coordinator = _make_coordinator(hass)
    coordinator._live_window_after_hours = 1
    started = datetime.now(UTC) - timedelta(hours=2)

    data = coordinator._process_poule_data(
        _minimal_poule(_unplayed_rencontre("match-2h", started))
    )

    assert data.next_match is not None
    assert data.next_match.match_id == "match-2h"
    assert data.next_match.is_stale is False
    # ...but it is outside the 1 h live window, so polling stays slow.
    assert coordinator._is_match_live(data) is False


# --- results that should exist by now: detection ----------------------------


def _poule(*rencontres: dict) -> dict:
    return {
        "id": "poule-1",
        "nom": "Poule A",
        "rencontres": list(rencontres),
        "classements": [],
    }


def _played_rencontre(match_id: str, started: datetime, s1: int = 68, s2: int = 54):
    rencontre = _unplayed_rencontre(match_id, started)
    rencontre.update(joue=True, resultatEquipe1=s1, resultatEquipe2=s2)
    return rencontre


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"joue": True}, True),
        ({"joue": True, "resultatEquipe1": None, "resultatEquipe2": None}, True),
        ({"joue": False, "resultatEquipe1": "68", "resultatEquipe2": "54"}, True),
        ({"joue": None, "resultatEquipe1": 3, "resultatEquipe2": 0}, True),
        ({"joue": False, "resultatEquipe1": 0, "resultatEquipe2": 0}, False),
        ({"joue": False, "resultatEquipe1": None, "resultatEquipe2": None}, False),
        ({"joue": False, "resultatEquipe1": 68, "resultatEquipe2": None}, False),
        ({}, False),
    ],
)
def test_is_played_follows_the_flag_then_the_scores(raw, expected):
    assert _is_played(raw) is expected


def test_count_results_counts_only_matches_with_a_result():
    now = dt_util.utcnow()
    data = _poule(
        _played_rencontre("a", now),
        _unplayed_rencontre("b", now),
        _played_rencontre("c", now),
    )

    assert _count_results(data) == 2
    assert _count_results({}) == 0
    assert _count_results({"rencontres": None}) == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [({"id": 7}, "7"), ({}, ""), ("8", "8"), (9, "9"), (None, "")],
)
def test_engagement_id_accepts_a_nested_object_or_a_plain_id(raw, expected):
    assert _engagement_id(raw) == expected


@pytest.mark.parametrize(
    ("hours_ago", "pending"),
    [(2, False), (3.5, True), (24 * 6, True), (24 * 8, False)],
)
def test_a_match_is_pending_only_between_3_hours_and_7_days(hass, hours_ago, pending):
    coordinator = _make_coordinator(hass)
    started = dt_util.utcnow() - timedelta(hours=hours_ago)

    found = coordinator._find_pending_results(_poule(_unplayed_rencontre("m", started)))

    assert bool(found) is pending


def test_matches_with_a_result_are_never_pending(hass):
    coordinator = _make_coordinator(hass)
    started = dt_util.utcnow() - timedelta(hours=5)
    score_only = _unplayed_rencontre("m2", started)
    score_only.update(resultatEquipe1=60, resultatEquipe2=50)

    found = coordinator._find_pending_results(
        _poule(_played_rencontre("m1", started), score_only)
    )

    assert found == []


def test_other_teams_matches_are_never_pending(hass):
    coordinator = _make_coordinator(hass)
    rencontre = _unplayed_rencontre("m", dt_util.utcnow() - timedelta(hours=5))
    rencontre["idEngagementEquipe1"] = {"id": "other-1"}
    rencontre["idEngagementEquipe2"] = {"id": "other-2"}

    assert coordinator._find_pending_results(_poule(rencontre)) == []


@pytest.mark.parametrize("raw_date", [None, "", "not a date"])
def test_a_match_without_a_usable_date_is_never_pending(hass, raw_date):
    coordinator = _make_coordinator(hass)
    rencontre = _unplayed_rencontre("m", dt_util.utcnow())
    rencontre["date_rencontre"] = raw_date

    assert coordinator._find_pending_results(_poule(rencontre)) == []


def test_pending_matches_are_found_when_the_team_plays_away_too(hass):
    coordinator = _make_coordinator(hass)
    rencontre = _unplayed_rencontre("m", dt_util.utcnow() - timedelta(hours=5))
    rencontre["idEngagementEquipe1"], rencontre["idEngagementEquipe2"] = (
        rencontre["idEngagementEquipe2"],
        rencontre["idEngagementEquipe1"],
    )

    assert len(coordinator._find_pending_results(_poule(rencontre))) == 1


# --- results that should exist by now: the safety net -----------------------


class _FakeClient:
    """Stand-in client answering get_poule_data from a scripted list."""

    base_url = "https://api.example"
    token_refresh_failures = 0

    def __init__(self, *answers) -> None:
        self.get_poule_data = AsyncMock(side_effect=list(answers))


def _outdated_and_fresh(now: datetime) -> tuple[dict, dict]:
    """An outdated poule answer (the 4 h old match has no result) and the
    up-to-date one (it does)."""
    earlier = now - timedelta(days=7)
    started = now - timedelta(hours=4)
    outdated = _poule(
        _played_rencontre("m1", earlier), _unplayed_rencontre("m2", started)
    )
    fresh = _poule(
        _played_rencontre("m1", earlier), _played_rencontre("m2", started, 27, 51)
    )
    return outdated, fresh


async def test_recheck_replaces_an_outdated_answer_with_the_fresher_one(hass, freezer):
    coordinator = _make_coordinator(hass)
    now = dt_util.utcnow()
    outdated, fresh = _outdated_and_fresh(now)
    coordinator.client = _FakeClient(outdated, fresh)

    result = await coordinator._async_fetch_poule_data()

    assert result is fresh
    # Same request first, then the differently formulated one, salted by the hour.
    assert coordinator.client.get_poule_data.await_args_list == [
        call("poule-1"),
        call("poule-1", refresh=int(now.timestamp() // 3600)),
    ]
    assert coordinator.stale_recheck_attempts == 1
    assert coordinator.stale_responses_detected == 1
    assert coordinator.last_recheck_outcome == "fresher_response_used (+1 result(s))"
    assert coordinator.last_recheck_at == now
    assert coordinator.pending_results == []


async def test_recheck_keeps_the_original_answer_when_nothing_changes(hass, freezer):
    coordinator = _make_coordinator(hass)
    outdated, _ = _outdated_and_fresh(dt_util.utcnow())
    coordinator.client = _FakeClient(outdated, outdated)

    result = await coordinator._async_fetch_poule_data()

    assert result is outdated
    assert coordinator.stale_recheck_attempts == 1
    assert coordinator.stale_responses_detected == 0
    assert coordinator.last_recheck_outcome == "no_difference"
    assert [item["match_number"] for item in coordinator.pending_results] == ["1"]


@pytest.mark.parametrize(
    "error", [FFBBConnectionError("down"), FFBBNotFoundError("gone"), FFBBApiError("x")]
)
async def test_recheck_never_fails_the_update_when_the_extra_request_fails(
    hass, freezer, error
):
    coordinator = _make_coordinator(hass)
    outdated, _ = _outdated_and_fresh(dt_util.utcnow())
    coordinator.client = _FakeClient(outdated, error)

    result = await coordinator._async_fetch_poule_data()

    assert result is outdated
    assert coordinator.last_recheck_outcome == "failed"
    assert coordinator.stale_responses_detected == 0


async def test_no_extra_request_when_no_result_is_missing(hass, freezer):
    coordinator = _make_coordinator(hass)
    _, fresh = _outdated_and_fresh(dt_util.utcnow())
    coordinator.client = _FakeClient(fresh)

    result = await coordinator._async_fetch_poule_data()

    assert result is fresh
    coordinator.client.get_poule_data.assert_awaited_once_with("poule-1")
    assert coordinator.stale_recheck_attempts == 0
    assert coordinator.last_recheck_outcome is None
    assert coordinator.pending_results == []


async def test_no_extra_request_for_a_match_that_just_ended(hass, freezer):
    """Under 3 hours the club may simply not have entered the score yet."""
    coordinator = _make_coordinator(hass)
    just_ended = _poule(_unplayed_rencontre("m", dt_util.utcnow() - timedelta(hours=2)))
    coordinator.client = _FakeClient(just_ended)

    await coordinator._async_fetch_poule_data()

    coordinator.client.get_poule_data.assert_awaited_once_with("poule-1")


async def test_recheck_runs_at_most_once_an_hour(hass, freezer):
    coordinator = _make_coordinator(hass)
    outdated, fresh = _outdated_and_fresh(dt_util.utcnow())
    coordinator.client = _FakeClient(outdated, outdated, outdated, outdated, fresh)

    await coordinator._async_fetch_poule_data()  # normal + recheck
    assert coordinator.stale_recheck_attempts == 1

    freezer.tick(timedelta(minutes=10))
    await coordinator._async_fetch_poule_data()  # normal only: too soon
    assert coordinator.stale_recheck_attempts == 1
    assert coordinator.client.get_poule_data.await_count == 3

    freezer.tick(timedelta(minutes=55))  # 65 min after the first recheck
    result = await coordinator._async_fetch_poule_data()  # normal + recheck
    assert coordinator.stale_recheck_attempts == 2
    assert coordinator.client.get_poule_data.await_count == 5
    assert result is fresh


async def test_the_chosen_answer_is_shared_through_the_poule_cache(hass, freezer):
    coordinator = _make_coordinator(hass)
    outdated, fresh = _outdated_and_fresh(dt_util.utcnow())
    coordinator.client = _FakeClient(outdated, fresh)

    first = await coordinator._async_fetch_poule_data()
    second = await coordinator._async_fetch_poule_data()

    assert first is second is fresh
    assert coordinator.client.get_poule_data.await_count == 2
    assert _get_poule_cache(hass, "poule-1").data is fresh


async def test_last_api_fetch_is_recorded_only_when_the_api_was_queried(hass, freezer):
    coordinator = _make_coordinator(hass)
    _, fresh = _outdated_and_fresh(dt_util.utcnow())
    coordinator.client = _FakeClient(fresh)
    assert coordinator.last_api_fetch_at is None

    await coordinator._async_fetch_poule_data()
    fetched_at = coordinator.last_api_fetch_at
    assert fetched_at == dt_util.utcnow()

    freezer.tick(timedelta(seconds=10))  # within the cache lifetime
    await coordinator._async_fetch_poule_data()
    assert coordinator.last_api_fetch_at == fetched_at


async def test_the_missing_score_shows_up_in_the_team_data(hass, freezer):
    """End to end: with an outdated answer for the normal request, the match
    still ends up with its score in the data the entities read."""
    coordinator = _make_coordinator(hass)
    outdated, fresh = _outdated_and_fresh(dt_util.utcnow())
    coordinator.client = _FakeClient(outdated, fresh)

    data = await coordinator._async_update_data()

    assert data.last_match is not None
    assert data.last_match.match_id == "m2"
    assert (data.last_match.team_score, data.last_match.opponent_score) == (27, 51)


# --- teams sharing a poule: a failing fetch is shared with those queued ------


def _make_sibling(hass, engagement_id: str) -> FFBBDataUpdateCoordinator:
    """Another team of the same poule (poule-1), with its own client."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=engagement_id,
        data={
            CONF_ENGAGEMENT_ID: engagement_id,
            CONF_POULE_ID: "poule-1",
            CONF_TEAM_NAME: f"Team {engagement_id}",
            CONF_COMPETITION_NAME: "Excellence Régionale",
            CONF_ORGANISME_ID: "org-1",
        },
    )
    entry.add_to_hass(hass)
    return FFBBDataUpdateCoordinator(hass, client=_FakeClient(_poule()), entry=entry)


class _SlowFailingClient:
    """A client whose poule request fails, but only once `gate` is released,
    so other teams can queue behind it first."""

    base_url = "https://api.example"
    token_refresh_failures = 0

    def __init__(self, error: Exception) -> None:
        self.gate = asyncio.Event()
        self.calls = 0
        self._error = error

    async def get_poule_data(self, poule_id: str, **_kwargs):
        self.calls += 1
        await self.gate.wait()
        raise self._error


async def _queue_behind(hass, failing: _SlowFailingClient, *queued):
    """Start a failing fetch, queue `queued` coordinators behind it, then let it
    fail. Returns the results of (failing, *queued), exceptions included."""
    first = _make_coordinator(hass)
    first.client = failing
    tasks = [asyncio.create_task(first._async_fetch_poule_data())]
    await asyncio.sleep(0)  # `first` takes the cache lock and starts its request
    for coordinator in queued:
        tasks.append(asyncio.create_task(coordinator._async_fetch_poule_data()))
    await asyncio.sleep(0)  # they are now waiting for the lock
    failing.gate.set()
    return await asyncio.gather(*tasks, return_exceptions=True)


async def test_teams_queued_behind_a_failing_fetch_fail_fast(hass):
    """Each queued team used to repeat the same request and wait for the same
    timeout, one after the other."""
    failing = _SlowFailingClient(FFBBConnectionError("down"))
    second, third = _make_sibling(hass, "e2"), _make_sibling(hass, "e3")

    results = await _queue_behind(hass, failing, second, third)

    assert all(isinstance(r, FFBBConnectionError) for r in results)
    assert all(str(r) == "down" for r in results)
    assert failing.calls == 1
    second.client.get_poule_data.assert_not_awaited()
    third.client.get_poule_data.assert_not_awaited()


async def test_the_failure_type_is_preserved_for_queued_teams(hass):
    """A 'not found' must still count as not found for the team that shares it."""
    failing = _SlowFailingClient(FFBBNotFoundError("poule gone"))
    sibling = _make_sibling(hass, "e2")

    results = await _queue_behind(hass, failing, sibling)

    assert all(isinstance(r, FFBBNotFoundError) for r in results)
    assert str(results[1]) == "poule gone"


async def test_a_team_that_refreshes_after_the_failure_queries_the_api_itself(hass):
    failing = _SlowFailingClient(FFBBConnectionError("down"))
    await _queue_behind(hass, failing)
    later = _make_sibling(hass, "e2")

    data = await later._async_fetch_poule_data()

    later.client.get_poule_data.assert_awaited_once()
    assert data["id"] == "poule-1"


async def test_the_failing_team_can_retry_straight_away(hass):
    """The Refresh button and the refresh action must never be blocked by the
    previous failure."""
    coordinator = _make_coordinator(hass)
    coordinator.client = _FakeClient(FFBBConnectionError("down"), _poule())

    with pytest.raises(FFBBConnectionError):
        await coordinator._async_fetch_poule_data()
    data = await coordinator._async_fetch_poule_data()

    assert coordinator.client.get_poule_data.await_count == 2
    assert data["id"] == "poule-1"
    assert _get_poule_cache(hass, "poule-1").failure_type is None
