"""Repair flows for the FFBB Tracker integration.

Only `season_rollover` is fixable. There's no code-level fix for a
season rollover -- the FFBB has assigned new engagement/pool IDs, so
the only real "fix" is re-running the team search -- but instead of
just telling the user to go do that manually (Settings > Devices &
services > Reconfigure), this flow launches the config entry's own
`async_step_reconfigure` directly from the repair dialog.

`token_refresh_failing` stays `is_fixable=False`: the description
already points at the two things a user can actually do (update the
integration, or report it), and there's no flow to hand off to.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant


class SeasonRolloverRepairFlow(RepairsFlow):
    """Confirm, then hand off to the integration's reconfigure flow."""

    def __init__(self, entry_id: str) -> None:
        """Store the config entry the issue was raised for."""
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """First (and only) step: confirm before opening the search again."""
        return await self.async_step_confirm(user_input)

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """On confirm, start the reconfigure flow for the stale entry."""
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None:
                await self.hass.config_entries.flow.async_init(
                    entry.domain,
                    context={
                        "source": config_entries.SOURCE_RECONFIGURE,
                        "entry_id": self._entry_id,
                    },
                )
            return self.async_create_entry(data={})

        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


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
