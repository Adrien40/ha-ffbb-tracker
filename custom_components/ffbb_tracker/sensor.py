"""Sensor platform for the FFBB Tracker integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FFBBConfigEntry
from .const import (
    ATTR_GOOGLE_MAPS_URL,
    ATTR_GYM_ADDRESS,
    ATTR_GYM_CITY,
    ATTR_GYM_NAME,
    ATTR_IS_HOME,
    ATTR_MATCH_DATE,
    ATTR_NAVIGATION_URL,
    ATTR_OPPONENT_SCORE,
    ATTR_STANDINGS,
    ATTR_TEAM_SCORE,
    ATTR_WAZE_URL,
    DOMAIN,
)
from .coordinator import FFBBDataUpdateCoordinator

# Entities are read-only views over coordinator.data (a single shared poll
# per team); there is no per-entity network I/O to throttle here.
PARALLEL_UPDATES = 0


def _safe_int_value(val: Any) -> int | None:
    """Defensively parse integer values from raw payloads or restored state."""
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FFBBConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FFBB Tracker sensors based on a config entry."""
    coordinator = entry.runtime_data

    async_add_entities(
        [
            FFBBNextMatchDateSensor(coordinator),
            FFBBNextMatchOpponentSensor(coordinator),
            FFBBNextMatchVenueTypeSensor(coordinator),
            FFBBNextMatchLocationSensor(coordinator),
            FFBBLastMatchDateSensor(coordinator),
            FFBBLastMatchOpponentSensor(coordinator),
            FFBBLastMatchResultSensor(coordinator),
            FFBBLastMatchScoreSensor(coordinator),
            FFBBRankingSensor(coordinator),
            FFBBRankingEvolutionSensor(coordinator),
            FFBBPouleSensor(coordinator),
        ]
    )


class FFBBSensorBase(CoordinatorEntity[FFBBDataUpdateCoordinator], SensorEntity):
    """Base class for FFBB Tracker sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FFBBDataUpdateCoordinator,
        sensor_type: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._sensor_type = sensor_type
        self._attr_unique_id = f"{coordinator.engagement_id}_{sensor_type}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.engagement_id)},
            name=f"{coordinator.team_name} - {coordinator.competition_name}",
            manufacturer="FFBB",
            model=coordinator.competition_name,
            entry_type=DeviceEntryType.SERVICE,
        )


class FFBBNextMatchDateSensor(FFBBSensorBase):
    """Sensor tracking the scheduled date and time of the next match."""

    _attr_translation_key = "next_match_date"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the next match date sensor."""
        super().__init__(coordinator, "next_match_date")

    @property
    def native_value(self) -> datetime | None:
        """Return the date and time of the next match."""
        if self.coordinator.data and self.coordinator.data.next_match:
            return self.coordinator.data.next_match.match_date
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return fixture details."""
        if not self.coordinator.data or not self.coordinator.data.next_match:
            return {}

        match = self.coordinator.data.next_match
        return {
            "round": match.round_number,
            "match_number": match.match_number,
            ATTR_GYM_NAME: match.gym_name,
            ATTR_GYM_ADDRESS: match.gym_address,
            ATTR_GYM_CITY: match.gym_city,
        }


class FFBBNextMatchOpponentSensor(FFBBSensorBase):
    """Sensor tracking the opponent of the next match with venue metadata."""

    _attr_translation_key = "next_match_opponent"
    _attr_icon = "mdi:account-group"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the next match opponent sensor."""
        super().__init__(coordinator, "next_match_opponent")

    @property
    def native_value(self) -> str | None:
        """Return the name of the next opponent."""
        if self.coordinator.data and self.coordinator.data.next_match:
            return self.coordinator.data.next_match.opponent_name
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return opponent attributes and venue navigation links."""
        if not self.coordinator.data or not self.coordinator.data.next_match:
            return {}

        match = self.coordinator.data.next_match
        attrs: dict[str, Any] = {
            "opponent_club_id": match.opponent_club_id,
            ATTR_IS_HOME: match.is_home,
            ATTR_GYM_NAME: match.gym_name,
            ATTR_GYM_ADDRESS: match.gym_address,
            ATTR_GYM_CITY: match.gym_city,
            "formatted_address": match.formatted_address,
            ATTR_NAVIGATION_URL: None,
            ATTR_GOOGLE_MAPS_URL: None,
            ATTR_WAZE_URL: None,
        }

        if match.formatted_address:
            encoded = quote_plus(match.formatted_address)
            attrs[ATTR_NAVIGATION_URL] = f"geo:0,0?q={encoded}"
            attrs[ATTR_GOOGLE_MAPS_URL] = (
                f"https://www.google.com/maps/dir/?api=1&destination={encoded}"
            )
            attrs[ATTR_WAZE_URL] = f"https://waze.com/ul?q={encoded}&navigate=yes"

        return attrs


class FFBBNextMatchVenueTypeSensor(FFBBSensorBase):
    """Sensor tracking whether the next match is played at home or away."""

    _attr_translation_key = "next_match_venue_type"
    _attr_icon = "mdi:home-switch"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the venue type sensor."""
        super().__init__(coordinator, "next_match_venue_type")

    @property
    def native_value(self) -> str | None:
        """Return home or away indicator."""
        if self.coordinator.data and self.coordinator.data.next_match:
            return "home" if self.coordinator.data.next_match.is_home else "away"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return pitch type attributes."""
        if not self.coordinator.data or not self.coordinator.data.next_match:
            return {}

        match = self.coordinator.data.next_match
        return {
            ATTR_IS_HOME: match.is_home,
            ATTR_GYM_NAME: match.gym_name,
            ATTR_GYM_CITY: match.gym_city,
        }


class FFBBNextMatchLocationSensor(FFBBSensorBase):
    """Sensor exposing the formatted address of the next match gym."""

    _attr_translation_key = "next_match_location"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the next match location sensor."""
        super().__init__(coordinator, "next_match_location")

    @property
    def native_value(self) -> str | None:
        """Return formatted address within state character limit."""
        if self.coordinator.data and self.coordinator.data.next_match:
            address = self.coordinator.data.next_match.formatted_address
            if address:
                return address[:255]
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return gym location details and navigation links."""
        if not self.coordinator.data or not self.coordinator.data.next_match:
            return {}

        match = self.coordinator.data.next_match
        attrs: dict[str, Any] = {
            ATTR_IS_HOME: match.is_home,
            ATTR_GYM_NAME: match.gym_name,
            ATTR_GYM_ADDRESS: match.gym_address,
            ATTR_GYM_CITY: match.gym_city,
            ATTR_NAVIGATION_URL: None,
            ATTR_GOOGLE_MAPS_URL: None,
            ATTR_WAZE_URL: None,
        }

        if match.formatted_address:
            encoded = quote_plus(match.formatted_address)
            attrs[ATTR_NAVIGATION_URL] = f"geo:0,0?q={encoded}"
            attrs[ATTR_GOOGLE_MAPS_URL] = (
                f"https://www.google.com/maps/dir/?api=1&destination={encoded}"
            )
            attrs[ATTR_WAZE_URL] = f"https://waze.com/ul?q={encoded}&navigate=yes"

        return attrs


class FFBBLastMatchDateSensor(FFBBSensorBase):
    """Sensor tracking the date and time of the last played match."""

    _attr_translation_key = "last_match_date"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-check"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the last match date sensor."""
        super().__init__(coordinator, "last_match_date")

    @property
    def native_value(self) -> datetime | None:
        """Return the date and time of the last match."""
        if self.coordinator.data and self.coordinator.data.last_match:
            return self.coordinator.data.last_match.match_date
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return last match fixture details."""
        if not self.coordinator.data or not self.coordinator.data.last_match:
            return {}

        match = self.coordinator.data.last_match
        return {
            "round": match.round_number,
            "match_number": match.match_number,
            ATTR_GYM_NAME: match.gym_name,
            ATTR_GYM_CITY: match.gym_city,
        }


class FFBBLastMatchOpponentSensor(FFBBSensorBase):
    """Sensor tracking the opponent of the last played match."""

    _attr_translation_key = "last_match_opponent"
    _attr_icon = "mdi:account-group-outline"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the last match opponent sensor."""
        super().__init__(coordinator, "last_match_opponent")

    @property
    def native_value(self) -> str | None:
        """Return the name of the last opponent."""
        if self.coordinator.data and self.coordinator.data.last_match:
            return self.coordinator.data.last_match.opponent_name
        return None


class FFBBLastMatchResultSensor(FFBBSensorBase):
    """Sensor tracking the result of the last played match."""

    _attr_translation_key = "last_match_result"
    _attr_icon = "mdi:trophy-outline"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the last match result sensor."""
        super().__init__(coordinator, "last_match_result")

    @property
    def native_value(self) -> str | None:
        """Return the result (win, loss, draw)."""
        if self.coordinator.data and self.coordinator.data.last_match:
            return self.coordinator.data.last_match.result
        return None


class FFBBLastMatchScoreSensor(FFBBSensorBase):
    """Sensor tracking the final score of the last played match."""

    _attr_translation_key = "last_match_score"
    _attr_icon = "mdi:scoreboard-outline"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the last match score sensor."""
        super().__init__(coordinator, "last_match_score")

    @property
    def native_value(self) -> str | None:
        """Return the formatted score."""
        if (
            self.coordinator.data
            and self.coordinator.data.last_match
            and self.coordinator.data.last_match.team_score is not None
            and self.coordinator.data.last_match.opponent_score is not None
        ):
            match = self.coordinator.data.last_match
            return f"{match.team_score} - {match.opponent_score}"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return team and opponent score values."""
        if not self.coordinator.data or not self.coordinator.data.last_match:
            return {}

        match = self.coordinator.data.last_match
        return {
            ATTR_TEAM_SCORE: match.team_score,
            ATTR_OPPONENT_SCORE: match.opponent_score,
            ATTR_IS_HOME: match.is_home,
            ATTR_MATCH_DATE: (
                match.match_date.isoformat() if match.match_date else None
            ),
        }


class FFBBRankingSensor(FFBBSensorBase):
    """Sensor tracking the ranking position of the team in the pool."""

    _attr_translation_key = "ranking"
    _attr_icon = "mdi:format-list-numbered"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the ranking sensor."""
        super().__init__(coordinator, "ranking")

    @property
    def native_value(self) -> int | None:
        """Return the current rank as a native integer."""
        if self.coordinator.data and self.coordinator.data.team_standing:
            return _safe_int_value(self.coordinator.data.team_standing.position)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return pool standings table and team stats."""
        if not self.coordinator.data:
            return {}

        standing = self.coordinator.data.team_standing
        attrs: dict[str, Any] = {
            "poule_name": self.coordinator.data.poule_name,
            ATTR_STANDINGS: self.coordinator.data.standings,
        }

        if standing:
            attrs.update(
                {
                    "points": standing.points,
                    "played": standing.played,
                    "won": standing.won,
                    "lost": standing.lost,
                }
            )

        return attrs


@dataclass
class FFBBRankingEvolutionExtraData(ExtraStoredData):
    """Extra data stored for ranking evolution restoration."""

    current_position: int | None
    previous_position: int | None

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation for storage."""
        return {
            "current_position": self.current_position,
            "previous_position": self.previous_position,
        }

    @classmethod
    def from_dict(
        cls, restored: dict[str, Any]
    ) -> FFBBRankingEvolutionExtraData | None:
        """Reconstruct from stored dict, or None if malformed."""
        if not isinstance(restored, dict):
            return None
        return cls(
            current_position=_safe_int_value(restored.get("current_position")),
            previous_position=_safe_int_value(restored.get("previous_position")),
        )


class FFBBRankingEvolutionSensor(FFBBSensorBase, RestoreSensor):
    """Sensor tracking ranking progression or regression."""

    _attr_translation_key = "ranking_evolution"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the ranking evolution sensor."""
        super().__init__(coordinator, "ranking_evolution")
        self._current_position: int | None = None
        self._previous_position: int | None = None

    @property
    def extra_restore_data(self) -> FFBBRankingEvolutionExtraData:
        """Return data to be restored on next startup."""
        return FFBBRankingEvolutionExtraData(
            current_position=self._current_position,
            previous_position=self._previous_position,
        )

    async def async_added_to_hass(self) -> None:
        """Restore previous positions on integration startup."""
        await super().async_added_to_hass()

        last_extra_data = await self.async_get_last_extra_data()
        if last_extra_data is not None:
            restored = FFBBRankingEvolutionExtraData.from_dict(
                last_extra_data.as_dict()
            )
            if restored is not None:
                self._current_position = restored.current_position
                self._previous_position = restored.previous_position

        if self.coordinator.data and self.coordinator.data.team_standing:
            pos = _safe_int_value(self.coordinator.data.team_standing.position)
            if pos is not None:
                # Architectural choice: when initializing without previous restore data,
                # both current and previous positions are set to the current rank.
                # This establishes a valid zero-differential baseline instead of leaving
                # previous_position as None, which would stall rank calculation.
                if self._current_position is None:
                    self._current_position = pos
                    self._previous_position = pos
                elif self._current_position != pos:
                    self._previous_position = self._current_position
                    self._current_position = pos

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data and self.coordinator.data.team_standing:
            new_pos = _safe_int_value(self.coordinator.data.team_standing.position)
            if new_pos is not None:
                # Architectural choice: if the sensor starts up while the team has a rank
                # but current_position was None (e.g. cold start), initialize both positions
                # to avoid leaving previous_position unassigned.
                if self._current_position is None:
                    self._current_position = new_pos
                    self._previous_position = new_pos
                elif new_pos != self._current_position:
                    self._previous_position = self._current_position
                    self._current_position = new_pos
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> str:
        """Return the evolution delta (+X, -X, 0) or '-' if no ranking yet."""
        # UX and stability choice: returning None causes Home Assistant to display
        # the entity as "Unavailable" / "Indisponible" (grayed out with a strike icon).
        # During pre-season, qualification tournaments, or for youth categories
        # lacking published standings, users frequently mistake "Unavailable" for an
        # integration bug or broken entity and attempt to delete it. Returning "-" keeps
        # the entity fully functional and cleanly indicates an awaiting status.
        if self._current_position is None or self._previous_position is None:
            return "-"

        diff = self._previous_position - self._current_position
        if diff > 0:
            return f"+{diff}"
        if diff < 0:
            return str(diff)
        return "0"

    @property
    def icon(self) -> str:
        """Return dynamic icon based on rank movement."""
        if self._current_position is not None and self._previous_position is not None:
            diff = self._previous_position - self._current_position
            if diff > 0:
                return "mdi:arrow-up-bold"
            if diff < 0:
                return "mdi:arrow-down-bold"
        return "mdi:minus"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return position tracking attributes."""
        attrs: dict[str, Any] = {
            "current_position": self._current_position,
            "previous_position": self._previous_position,
        }
        # UX choice: explicitly explain in the state attributes why the value is '-'
        # so users inspecting the entity know the federation has not published rankings yet.
        if self._current_position is None:
            attrs["status"] = "En attente du premier classement officiel"
        return attrs


class FFBBPouleSensor(FFBBSensorBase):
    """Sensor displaying the assigned pool name."""

    _attr_translation_key = "poule"
    _attr_icon = "mdi:tournament"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the pool sensor."""
        super().__init__(coordinator, "poule")

    @property
    def native_value(self) -> str | None:
        """Return the pool name."""
        if self.coordinator.data:
            return self.coordinator.data.poule_name or None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return competition details."""
        if not self.coordinator.data:
            return {}

        return {
            "competition": self.coordinator.data.competition_name,
            "team": self.coordinator.data.team_name,
        }
