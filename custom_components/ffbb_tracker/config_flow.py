"""Config flow for FFBB Tracker integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from . import get_rate_limiter
from .api import FFBBClient
from .const import (
    CONF_LIVE_POLLING,
    CONF_LIVE_SCAN_INTERVAL,
    CONF_LIVE_WINDOW_AFTER_HOURS,
    CONF_SCAN_INTERVAL,
    DEFAULT_LIVE_POLLING,
    DEFAULT_LIVE_SCAN_INTERVAL,
    DEFAULT_LIVE_WINDOW_AFTER_HOURS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_LIVE_SCAN_INTERVAL,
    MAX_LIVE_WINDOW_AFTER_HOURS,
    MAX_SCAN_INTERVAL,
    MIN_LIVE_SCAN_INTERVAL,
    MIN_LIVE_WINDOW_AFTER_HOURS,
    MIN_SCAN_INTERVAL,
)
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

_LOGGER = logging.getLogger(__name__)


class FFBBTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FFBB Tracker."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._client: FFBBClient | None = None
        self._clubs: list[dict[str, Any]] = []
        self._selected_club: dict[str, Any] | None = None
        self._engagements: list[dict[str, Any]] = []
        self._reconfigure_entry: ConfigEntry | None = None

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> FFBBTrackerOptionsFlow:
        """Return the options flow used to tweak polling behavior."""
        return FFBBTrackerOptionsFlow()

    def _get_api_client(self) -> FFBBClient:
        """Get or initialize the FFBB API client.

        Shares the same rate limiter as the coordinators (see __init__.py)
        so that club searches typed during setup/reconfiguration are paced
        together with the background polling, rather than bypassing it.
        """
        if self._client is None:
            session = async_get_clientsession(self.hass)
            rate_limiter = get_rate_limiter(self.hass)
            self._client = FFBBClient(session, rate_limiter=rate_limiter)
        return self._client

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: search query or direct URL/ID."""
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input["search_query"].strip()
            engagement_id = engagement_id_from_query(query)

            if engagement_id is None and len(query) < MIN_SEARCH_QUERY_LENGTH:
                errors["base"] = "query_too_short"
            else:
                client = self._get_api_client()

                if engagement_id is not None:
                    return await self._async_handle_engagement_id(engagement_id)

                clubs, error = await search_clubs(client, query)
                if error:
                    errors["base"] = error
                else:
                    self._clubs = clubs
                    return await self.async_step_club()

        schema = vol.Schema({vol.Required("search_query"): str})
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_club(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle club selection from search results."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_id = str(user_input["club_id"])
            self._selected_club = next(
                (club for club in self._clubs if str(club.get("id")) == selected_id),
                None,
            )

            if not self._selected_club:
                return self.async_abort(reason="club_not_found")

            engagements, error = await club_teams(self._get_api_client(), selected_id)
            if error:
                errors["base"] = error
            else:
                self._engagements = engagements
                return await self.async_step_team()

        schema = vol.Schema(
            {vol.Required("club_id"): vol.In(club_options(self._clubs))}
        )
        return self.async_show_form(
            step_id="club",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_team(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle team (engagement) selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            engagement_id = str(user_input["engagement_id"])
            selected_engagement = next(
                (
                    eng
                    for eng in self._engagements
                    if str(eng.get("id")) == engagement_id
                ),
                None,
            )

            if selected_engagement:
                organisme_id = (
                    str(self._selected_club.get("id")) if self._selected_club else ""
                )
                data, title = build_entry(
                    selected_engagement, engagement_id, organisme_id
                )
                return await self._finalize_entry(engagement_id, data, title)

        schema = vol.Schema(
            {vol.Required("engagement_id"): vol.In(team_options(self._engagements))}
        )
        return self.async_show_form(
            step_id="team",
            data_schema=schema,
            errors=errors,
        )

    async def _async_handle_engagement_id(self, engagement_id: str) -> ConfigFlowResult:
        """Create or update an entry from a validated engagement ID."""
        engagement, error = await lookup_engagement(
            self._get_api_client(), engagement_id
        )
        if engagement is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required("search_query"): str}),
                errors={"base": error or "unknown"},
            )

        organisme = engagement.get("idOrganisme") or {}
        data, title = build_entry(
            engagement, engagement_id, str(organisme.get("id", ""))
        )
        return await self._finalize_entry(engagement_id, data, title)

    async def _finalize_entry(
        self,
        engagement_id: str,
        data: dict[str, Any],
        title: str,
    ) -> ConfigFlowResult:
        """Create a new entry or update the one being reconfigured."""
        await self.async_set_unique_id(engagement_id)

        if self._reconfigure_entry:
            existing_entry = self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, engagement_id
            )
            if (
                existing_entry
                and existing_entry.entry_id != self._reconfigure_entry.entry_id
            ):
                return self.async_abort(reason="already_configured")

            migrate_entities(self.hass, self._reconfigure_entry, engagement_id)

            return self.async_update_reload_and_abort(
                self._reconfigure_entry,
                unique_id=engagement_id,
                title=title,
                data=data,
                reason="reconfigure_successful",
            )

        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=title,
            data=data,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing entry."""
        self._reconfigure_entry = self._get_reconfigure_entry()
        return await self.async_step_user(user_input)


class FFBBTrackerOptionsFlow(OptionsFlow):
    """Let the user tune polling intervals and live match tracking."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        current_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        current_live_enabled = options.get(CONF_LIVE_POLLING, DEFAULT_LIVE_POLLING)
        current_live_interval = options.get(
            CONF_LIVE_SCAN_INTERVAL, DEFAULT_LIVE_SCAN_INTERVAL
        )
        current_live_window_after = options.get(
            CONF_LIVE_WINDOW_AFTER_HOURS, DEFAULT_LIVE_WINDOW_AFTER_HOURS
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Coerce(int),
                ),
                vol.Required(
                    CONF_LIVE_POLLING, default=current_live_enabled
                ): BooleanSelector(),
                vol.Required(
                    CONF_LIVE_SCAN_INTERVAL, default=current_live_interval
                ): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_LIVE_SCAN_INTERVAL,
                            max=MAX_LIVE_SCAN_INTERVAL,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Coerce(int),
                ),
                vol.Required(
                    CONF_LIVE_WINDOW_AFTER_HOURS, default=current_live_window_after
                ): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_LIVE_WINDOW_AFTER_HOURS,
                            max=MAX_LIVE_WINDOW_AFTER_HOURS,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Coerce(int),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
