"""Config flow for FFBB Tracker integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from . import get_rate_limiter
from .api import (
    FFBBApiError,
    FFBBClient,
    FFBBConnectionError,
    FFBBNotFoundError,
)
from .const import (
    CONF_COMPETITION_NAME,
    CONF_ENGAGEMENT_ID,
    CONF_LIVE_POLLING,
    CONF_LIVE_SCAN_INTERVAL,
    CONF_LIVE_WINDOW_AFTER_HOURS,
    CONF_ORGANISME_ID,
    CONF_POULE_ID,
    CONF_SCAN_INTERVAL,
    CONF_TEAM_NAME,
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

MIN_SEARCH_QUERY_LENGTH = 2

_LOGGER = logging.getLogger(__name__)

URL_ID_PATTERN = re.compile(r"/[eé]quipes?(?:/[^/\s]+)*/(\d+)/?", re.IGNORECASE)
RAW_ID_PATTERN = re.compile(r"^\d+$")


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
            url_match = URL_ID_PATTERN.search(query)
            is_raw_id = RAW_ID_PATTERN.match(query)

            if not url_match and not is_raw_id and len(query) < MIN_SEARCH_QUERY_LENGTH:
                errors["base"] = "query_too_short"
            else:
                client = self._get_api_client()

                if url_match:
                    return await self._async_handle_engagement_id(url_match.group(1))

                if is_raw_id:
                    return await self._async_handle_engagement_id(query)

                try:
                    clubs = await client.search_clubs(query)
                    if not clubs:
                        errors["base"] = "no_clubs_found"
                    else:
                        self._clubs = clubs
                        return await self.async_step_club()
                except FFBBConnectionError:
                    errors["base"] = "cannot_connect"
                except FFBBApiError as err:
                    _LOGGER.error("Error during club search: %s", err)
                    errors["base"] = "unknown"

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

            client = self._get_api_client()
            try:
                engagements = await client.get_club_engagements(selected_id)
                valid_engagements = [
                    eng
                    for eng in engagements
                    if eng.get("idPoule") and eng["idPoule"].get("id")
                ]

                if not valid_engagements:
                    errors["base"] = "no_teams_found"
                else:
                    self._engagements = valid_engagements
                    return await self.async_step_team()
            except FFBBConnectionError:
                errors["base"] = "cannot_connect"
            except FFBBApiError as err:
                _LOGGER.error("Error retrieving club engagements: %s", err)
                errors["base"] = "unknown"

        club_options = {
            str(club["id"]): f"{club.get('nom', 'Club')} ({club.get('code', 'N/A')})"
            for club in self._clubs
        }

        schema = vol.Schema({vol.Required("club_id"): vol.In(club_options)})
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
                team_name = selected_engagement.get("nom", "Équipe")
                competition = selected_engagement.get("idCompetition") or {}
                competition_name = competition.get("nom", "Compétition")
                poule = selected_engagement.get("idPoule") or {}
                poule_id = str(poule.get("id"))
                organisme_id = (
                    str(self._selected_club.get("id")) if self._selected_club else ""
                )

                data = {
                    CONF_ENGAGEMENT_ID: engagement_id,
                    CONF_TEAM_NAME: team_name,
                    CONF_COMPETITION_NAME: competition_name,
                    CONF_POULE_ID: poule_id,
                    CONF_ORGANISME_ID: organisme_id,
                }
                title = f"{team_name} - {competition_name}"

                return await self._finalize_entry(engagement_id, data, title)

        team_options = {}
        for eng in self._engagements:
            team_name = eng.get("nom", "Équipe")
            comp = eng.get("idCompetition") or {}
            comp_name = comp.get("nom", "Compétition")
            poule = eng.get("idPoule") or {}
            poule_name = poule.get("nom", "")
            label = f"{team_name} — {comp_name}"
            if poule_name:
                label += f" ({poule_name})"
            team_options[str(eng["id"])] = label

        schema = vol.Schema({vol.Required("engagement_id"): vol.In(team_options)})
        return self.async_show_form(
            step_id="team",
            data_schema=schema,
            errors=errors,
        )

    async def _async_handle_engagement_id(self, engagement_id: str) -> ConfigFlowResult:
        """Create or update an entry from a validated engagement ID."""
        client = self._get_api_client()
        try:
            engagement = await client.get_engagement(engagement_id)
        except FFBBNotFoundError:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required("search_query"): str}),
                errors={"base": "invalid_engagement"},
            )
        except FFBBConnectionError:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required("search_query"): str}),
                errors={"base": "cannot_connect"},
            )
        except FFBBApiError as err:
            _LOGGER.error("Direct engagement retrieval error: %s", err)
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required("search_query"): str}),
                errors={"base": "unknown"},
            )

        poule = engagement.get("idPoule") or {}
        poule_id = poule.get("id")
        if not poule_id:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required("search_query"): str}),
                errors={"base": "no_poule_found"},
            )

        team_name = engagement.get("nom", "Équipe")
        competition = engagement.get("idCompetition") or {}
        competition_name = competition.get("nom", "Compétition")
        organisme = engagement.get("idOrganisme") or {}
        organisme_id = str(organisme.get("id", ""))

        data = {
            CONF_ENGAGEMENT_ID: engagement_id,
            CONF_TEAM_NAME: team_name,
            CONF_COMPETITION_NAME: competition_name,
            CONF_POULE_ID: str(poule_id),
            CONF_ORGANISME_ID: organisme_id,
        }
        title = f"{team_name} - {competition_name}"

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

            dev_reg = dr.async_get(self.hass)
            for device_entry in dr.async_entries_for_config_entry(
                dev_reg, self._reconfigure_entry.entry_id
            ):
                if (DOMAIN, engagement_id) not in device_entry.identifiers:
                    dev_reg.async_remove_device(device_entry.id)

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
