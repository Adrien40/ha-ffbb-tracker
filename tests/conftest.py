"""Shared pytest fixtures for the FFBB Tracker test suite."""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for every test in this suite."""
    yield


@pytest.fixture
def sample_poule_data() -> dict:
    """Return a realistic Directus 'ffbbserver_poules' API payload.

    Mirrors the exact field names requested in FFBBClient.get_poule_data,
    with one played match (engagement is home), one upcoming match
    (engagement is away), and one standings row for the tracked engagement.
    """
    return {
        "id": "poule-1",
        "nom": "Excellence Régionale - Poule A",
        "rencontres": [
            {
                "id": "match-1",
                "numero": "1",
                "numeroJournee": "3",
                "resultatEquipe1": 68,
                "resultatEquipe2": 54,
                "joue": True,
                "nomEquipe1": "Basket Landes",
                "nomEquipe2": "US Mont-de-Marsan",
                "date_rencontre": "2026-01-10T20:00:00+01:00",
                "idEngagementEquipe1": {"id": "engagement-123"},
                "idEngagementEquipe2": {"id": "engagement-456"},
                "idOrganismeEquipe1": {"id": "org-1", "nom": "Basket Landes"},
                "idOrganismeEquipe2": {"id": "org-2", "nom": "US Mont-de-Marsan"},
                "salle": {
                    "id": "salle-1",
                    "libelle": "Gymnase Andre Chavanne",
                    "adresse": "1 rue du Stade",
                    "codePostal": "40000",
                    "commune": {"libelle": "Mont-de-Marsan"},
                },
            },
            {
                "id": "match-2",
                "numero": "2",
                "numeroJournee": "4",
                "resultatEquipe1": None,
                "resultatEquipe2": None,
                "joue": False,
                "nomEquipe1": "Stade Montois",
                "nomEquipe2": "Basket Landes",
                "date_rencontre": "2026-09-20T18:00:00+02:00",
                "idEngagementEquipe1": {"id": "engagement-789"},
                "idEngagementEquipe2": {"id": "engagement-123"},
                "idOrganismeEquipe1": {"id": "org-3", "nom": "Stade Montois"},
                "idOrganismeEquipe2": {"id": "org-1", "nom": "Basket Landes"},
                "salle": {
                    "id": "salle-2",
                    "libelle": "Gymnase Municipal",
                    "adresse": "12 avenue de la République",
                    "codePostal": "40280",
                    "commune": {"libelle": "Saint-Pierre-du-Mont"},
                },
            },
            {
                # A match belonging to a different engagement entirely;
                # must be filtered out and never appear in team_matches.
                "id": "match-3",
                "numero": "1",
                "numeroJournee": "3",
                "resultatEquipe1": 40,
                "resultatEquipe2": 60,
                "joue": True,
                "nomEquipe1": "Autre Club A",
                "nomEquipe2": "Autre Club B",
                "date_rencontre": "2026-01-10T20:00:00+01:00",
                "idEngagementEquipe1": {"id": "engagement-999"},
                "idEngagementEquipe2": {"id": "engagement-888"},
                "idOrganismeEquipe1": {"id": "org-9", "nom": "Autre Club A"},
                "idOrganismeEquipe2": {"id": "org-8", "nom": "Autre Club B"},
                "salle": None,
            },
        ],
        "classements": [
            {
                "id": "rank-1",
                "idEngagement": {"id": "engagement-123", "nom": "Basket Landes"},
                "matchJoues": 3,
                "points": 6,
                "position": 1,
                "gagnes": 3,
                "perdus": 0,
            },
            {
                "id": "rank-2",
                "idEngagement": {"id": "engagement-456", "nom": "US Mont-de-Marsan"},
                "matchJoues": 3,
                "points": 4,
                "position": 2,
                "gagnes": 2,
                "perdus": 1,
            },
        ],
    }
