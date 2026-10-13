"""Repair flows for the FFBB Tracker integration.

Only `season_rollover` is fixable. At a season rollover the FFBB assigns new
engagement and pool IDs, so the only real "fix" is to search for the team
again. That search runs here, inside the repair dialog, using the same team
search as the config flow (see team_picker.py): the user finds the team under
its new IDs, and the existing config entry is switched over to it.

(An earlier version started the entry's reconfigure flow from here. Home
Assistant doesn't list flows started that way, so the user never saw it.)

`token_refresh_failing` stays `is_fixable=False`: the description already
points at the two things a user can actually do (update the integration, or
report it), and there's no flow to hand off to.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import get_rate_limiter
from .api import FFBBClient
from .const import CONF_TEAM_NAME, DOMAIN
from .team_picker import (
    MIN_SEARCH_QUERY_LENGTH,
    build_entry,
    club_options,
    club_teams,
    engagement_id_from_query,
    lookup_engagement,
    migrate_entities,
    search_clubs,
    team_options,
)


class SeasonRolloverRepairFlow(RepairsFlow):
    """Find the team again under its new IDs, then switch the entry over."""

    def __init__(self, entry_id: str) -> None:
        """Store the config entry the issue was raised for."""
        self._entry_id = entry_id
        self._client: FFBBClient | None = None
        self._clubs: list[dict[str, Any]] = []
        self._selected_club: dict[str, Any] | None = None
        self._engagements: list[dict[str, Any]] = []

    def _get_api_client(self) -> FFBBClient:
        """Return the API client, sharing the integration's rate limiter."""
        if self._client is None:
            self._client = FFBBClient(
                async_get_clientsession(self.hass),
                rate_limiter=get_rate_limiter(self.hass),
            )
        return self._client

    def _placeholders(self) -> dict[str, str]:
        """Return the team name for the step texts.

        Home Assistant doesn't add the issue's placeholders to the forms of a
        custom repair flow, so every form passes them explicitly.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return {"team_name": str(entry.data.get(CONF_TEAM_NAME, "")) if entry else ""}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """First step: explain what is about to happen.

        Home Assistant calls this with the data the flow was started with
        ({"issue_id": ...}) as `user_input`. It must not be passed on: the
        confirm step would take it for the user's confirmation and skip the
        explanation. (Home Assistant's own confirm-only repair flow does the
        same.)
        """
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Explain, then move on to the team search."""
        if user_input is not None:
            if self.hass.config_entries.async_get_entry(self._entry_id) is None:
                # The integration entry was deleted before the user got here:
                # nothing left to fix, so just close the repair.
                return self.async_create_entry(data={})
            return await self.async_step_search()

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=self._placeholders(),
        )

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Search by club name, team URL or team ID."""
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input["search_query"].strip()
            engagement_id = engagement_id_from_query(query)

            if engagement_id is None and len(query) < MIN_SEARCH_QUERY_LENGTH:
                errors["base"] = "query_too_short"
            elif engagement_id is not None:
                engagement, error = await lookup_engagement(
                    self._get_api_client(), engagement_id
                )
                if engagement is None:
                    errors["base"] = error or "unknown"
                else:
                    organisme = engagement.get("idOrganisme") or {}
                    data, title = build_entry(
                        engagement, engagement_id, str(organisme.get("id", ""))
                    )
                    result, error = self._switch_entry(engagement_id, data, title)
                    if result is not None:
                        return result
                    errors["base"] = error or "unknown"
            else:
                clubs, error = await search_clubs(self._get_api_client(), query)
                if error:
                    errors["base"] = error
                else:
                    self._clubs = clubs
                    return await self.async_step_club()

        return self.async_show_form(
            step_id="search",
            data_schema=vol.Schema({vol.Required("search_query"): str}),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    async def async_step_club(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Choose the club among the search results."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_id = str(user_input["club_id"])
            self._selected_club = next(
                (club for club in self._clubs if str(club.get("id")) == selected_id),
                None,
            )
            if self._selected_club is None:
                return self.async_abort(reason="club_not_found")

            engagements, error = await club_teams(self._get_api_client(), selected_id)
            if error:
                errors["base"] = error
            else:
                self._engagements = engagements
                return await self.async_step_team()

        return self.async_show_form(
            step_id="club",
            data_schema=vol.Schema(
                {vol.Required("club_id"): vol.In(club_options(self._clubs))}
            ),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    async def async_step_team(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Choose the team, which switches the config entry over to it."""
        errors: dict[str, str] = {}

        if user_input is not None:
            engagement_id = str(user_input["engagement_id"])
            selected = next(
                (
                    eng
                    for eng in self._engagements
                    if str(eng.get("id")) == engagement_id
                ),
                None,
            )
            if selected is not None:
                organisme_id = (
                    str(self._selected_club.get("id")) if self._selected_club else ""
                )
                data, title = build_entry(selected, engagement_id, organisme_id)
                result, error = self._switch_entry(engagement_id, data, title)
                if result is not None:
                    return result
                errors["base"] = error or "unknown"

        return self.async_show_form(
            step_id="team",
            data_schema=vol.Schema(
                {vol.Required("engagement_id"): vol.In(team_options(self._engagements))}
            ),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    def _switch_entry(
        self, engagement_id: str, data: dict[str, Any], title: str
    ) -> tuple[data_entry_flow.FlowResult | None, str | None]:
        """Point the config entry at the newly found team.

        Returns (result, None) when done, or (None, error key) when the form
        should be shown again with that error. Same effect as reconfiguring
        the entry: its entities and device follow the team to the new IDs
        (see team_picker.migrate_entities), then new data, title and unique
        ID, then a reload. Home Assistant removes the repair issue itself
        when this flow finishes.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="entry_not_found"), None

        existing = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, engagement_id
        )
        if existing is not None and existing.entry_id != entry.entry_id:
            return None, "already_configured"

        migrate_entities(self.hass, entry, engagement_id)
        self.hass.config_entries.async_update_entry(
            entry, data=data, title=title, unique_id=engagement_id
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_create_entry(data={}), None


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the repair flow for a given issue_id.

    Only season_rollover issues reach here with is_fixable=True (see
    coordinator.py); token_refresh_failing issues are is_fixable=False
    and never trigger this.
    """
    entry_id = (data or {}).get("entry_id", "")
    return SeasonRolloverRepairFlow(entry_id)
