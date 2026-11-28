"""Tests for the team list of team_picker.py: sorting and the text filter."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol

from custom_components.ffbb_tracker.team_picker import (
    CLUB_SEARCH_LIMIT,
    CONF_TEAM_FILTER,
    TEAM_FILTER_MIN_TEAMS,
    TeamChooser,
    build_entry,
    club_options,
    competition_score,
    filter_teams,
    search_clubs,
    sort_teams,
    team_label,
    team_options,
)


def _team(
    engagement_id: int, competition: str, pool: str = "Poule A"
) -> dict[str, Any]:
    """A team of a club whose teams all carry the club's name."""
    return {
        "id": str(engagement_id),
        "nom": "UNION JEUN SP BUGLOSE PONTONX",
        "idCompetition": {"nom": competition},
        "idPoule": {"id": f"poule-{engagement_id}", "nom": pool},
    }


SENIORS = _team(1, "Régionale masculine seniors - Division 1")
U13M = _team(2, "Départementale U13 masculin", "Phase 2")
U15F = _team(3, "Départementale U15 féminine")
U11 = _team(4, "Départementale U11 mixte")


def _many_teams() -> list[dict[str, Any]]:
    """A club with more teams than the filter threshold."""
    return [_team(100 + i, f"Challenge {i}") for i in range(TEAM_FILTER_MIN_TEAMS)] + [
        SENIORS,
        U13M,
    ]


def test_label_shows_team_competition_and_pool():
    assert team_label(U13M) == (
        "UNION JEUN SP BUGLOSE PONTONX — Départementale U13 masculin (Phase 2)"
    )


def test_options_are_keyed_by_engagement_id():
    assert team_options([SENIORS, U13M]) == {
        "1": team_label(SENIORS),
        "2": team_label(U13M),
    }


def test_the_closest_competition_comes_first():
    """A U13 team is replaced by the U13 team, whatever order the FFBB sends."""
    for order in ([SENIORS, U15F, U13M, U11], [U11, U13M, U15F, SENIORS]):
        ranked = sort_teams(order, "Départementale U13M - Phase 1")

        assert ranked[0] is U13M


def test_accents_and_endings_do_not_matter_when_ranking():
    ranked = sort_teams([U15F, SENIORS], "regionale masculin seniors")

    assert ranked[0] is SENIORS


def test_without_a_reference_the_order_is_stable():
    assert sort_teams([U15F, SENIORS, U13M, U11]) == sort_teams(
        [U11, U13M, SENIORS, U15F]
    )


def test_filter_matches_every_word_in_any_order_ignoring_accents():
    teams = [SENIORS, U13M, U15F]

    assert filter_teams(teams, "u13") == [U13M]
    assert filter_teams(teams, "FEMININE u15") == [U15F]
    assert filter_teams(teams, "regionale") == [SENIORS]
    assert filter_teams(teams, "u13 seniors") == []
    assert filter_teams(teams, "  ") == teams


def test_a_short_list_has_no_filter_field():
    chooser = TeamChooser([SENIORS, U13M])

    assert chooser.filterable is False
    assert chooser.apply_filter({CONF_TEAM_FILTER: "u13"}) is False
    assert len(chooser.schema_fields()) == 1


def test_a_long_list_offers_the_filter_field():
    chooser = TeamChooser(_many_teams())

    assert chooser.filterable is True
    assert any(
        isinstance(key, vol.Optional) and key.schema == CONF_TEAM_FILTER
        for key in chooser.schema_fields()
    )


def test_submitting_a_new_filter_narrows_the_list_without_confirming():
    chooser = TeamChooser(_many_teams())

    changed = chooser.apply_filter({CONF_TEAM_FILTER: "u13", "engagement_id": "1"})

    assert changed is True
    assert chooser.teams == [U13M]
    assert chooser.errors == {}
    schema = vol.Schema(chooser.schema_fields())
    assert schema({})["engagement_id"] == "2"


def test_submitting_the_same_filter_again_confirms_the_team():
    chooser = TeamChooser(_many_teams())
    chooser.apply_filter({CONF_TEAM_FILTER: "u13"})

    assert chooser.apply_filter({CONF_TEAM_FILTER: "u13"}) is False


def test_clearing_the_filter_brings_the_whole_list_back():
    chooser = TeamChooser(_many_teams())
    chooser.apply_filter({CONF_TEAM_FILTER: "u13"})

    assert chooser.apply_filter({}) is True
    assert len(chooser.teams) == len(_many_teams())


def test_a_filter_with_no_match_says_so_and_keeps_the_whole_list():
    chooser = TeamChooser(_many_teams())

    chooser.apply_filter({CONF_TEAM_FILTER: "zzz"})

    assert chooser.errors == {CONF_TEAM_FILTER: "no_team_matches"}
    assert len(chooser.teams) == len(_many_teams())
    # Nothing matched, so the first team of the full list must NOT be preselected.
    with pytest.raises(vol.Invalid):
        vol.Schema(chooser.schema_fields())({})


def test_the_closest_team_is_preselected_only_when_it_is_a_close_match():
    ranked = TeamChooser([SENIORS, U13M, U15F], "Départementale U13M")
    schema = vol.Schema(ranked.schema_fields())
    assert schema({})["engagement_id"] == "2"

    unranked = TeamChooser([SENIORS, U13M, U15F])
    with pytest.raises(vol.Invalid):
        vol.Schema(unranked.schema_fields())({})

    nothing_close = TeamChooser([SENIORS, U13M], "Coupe de France")
    with pytest.raises(vol.Invalid):
        vol.Schema(nothing_close.schema_fields())({})


def test_default_first_preselects_the_first_team_of_the_sorted_list():
    chooser = TeamChooser([SENIORS, U13M])

    schema = vol.Schema(chooser.schema_fields(default_first=True))

    assert schema({})["engagement_id"] == chooser.teams[0]["id"]


# --- club search: no silent truncation ---------------------------------------


def _clubs(count: int) -> list[dict[str, Any]]:
    return [{"id": str(i), "nom": f"CLUB {i}", "code": f"C{i}"} for i in range(count)]


async def test_a_search_within_the_limit_returns_every_club():
    client = MagicMock()
    client.search_clubs = AsyncMock(return_value=_clubs(CLUB_SEARCH_LIMIT))

    clubs, error = await search_clubs(client, "Basket")

    assert error is None
    assert len(clubs) == CLUB_SEARCH_LIMIT
    # One more than the limit is requested, to tell "exactly" from "more".
    client.search_clubs.assert_awaited_once_with("Basket", limit=CLUB_SEARCH_LIMIT + 1)


async def test_a_search_past_the_limit_asks_to_narrow_it_down():
    """The API cuts the list off silently, sorted by name: showing the first
    clubs would hide the user's own club if it sorts late."""
    client = MagicMock()
    client.search_clubs = AsyncMock(return_value=_clubs(CLUB_SEARCH_LIMIT + 1))

    clubs, error = await search_clubs(client, "Basket")

    assert clubs == []
    assert error == "too_many_clubs"


# --- null values coming from the API ------------------------------------------


def test_a_null_name_falls_back_instead_of_showing_none():
    team = {
        "nom": None,
        "idCompetition": {"nom": None},
        "idPoule": {"id": None, "nom": None},
    }

    assert team_label(team) == "Équipe — Compétition"
    data, title = build_entry(team, "eng-1", "org-1")
    assert title == "Équipe - Compétition"
    assert "None" not in data.values()
    assert data["team_name"] == "Équipe"
    assert data["poule_id"] == ""
    assert club_options([{"id": "1", "nom": None, "code": None}]) == {"1": "Club (N/A)"}


def test_ranking_a_team_with_a_null_competition_name_does_not_count_none():
    team = {"nom": "X", "idCompetition": {"nom": None}, "idPoule": {"id": "p"}}

    assert competition_score(team, "None") == 0


async def test_flattened_scalar_relations_do_not_raise():
    """Scalar Directus relation IDs (`idCompetition`, `idPoule`) must not raise."""
    from custom_components.ffbb_tracker.team_picker import club_teams, lookup_engagement

    flat = {
        "id": "42",
        "nom": 123,
        "idCompetition": "flat-comp-id",
        "idPoule": "flat-poule-id",
    }
    assert team_label(flat) == "123 — Compétition"
    assert competition_score(flat, "U13") == 0
    data, title = build_entry(flat, "42", "org-1")
    assert title == "123 - Compétition"
    assert data["poule_id"] == "flat-poule-id"

    client = MagicMock()
    client.get_club_engagements = AsyncMock(return_value=[flat])
    teams, err = await club_teams(client, "org-1")
    assert err is None
    assert teams == [flat]

    client.get_engagement = AsyncMock(return_value=flat)
    eng, err = await lookup_engagement(client, "42")
    assert err is None
    assert eng == flat
