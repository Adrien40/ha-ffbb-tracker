"""Sensor platform for the FFBB Tracker integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from urllib.parse import quote

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
    ATTR_IS_STALE,
    ATTR_MATCH_DATE,
    ATTR_NAVIGATION_URL,
    ATTR_OPPONENT_LOGO_URL,
    ATTR_OPPONENT_SCORE,
    ATTR_STANDINGS,
    ATTR_TEAM_LOGO_URL,
    ATTR_TEAM_SCORE,
    ATTR_WAZE_URL,
    DOMAIN,
)
from .coordinator import FFBBDataUpdateCoordinator

PARALLEL_UPDATES = 0

_FORM_WINDOW: Final = 5

_FORM_LETTERS: dict[str, dict[str, str]] = {
    "fr": {"win": "V", "loss": "D", "draw": "N"},
    "en": {"win": "W", "loss": "L", "draw": "D"},
}


def _get_form_letters(hass: HomeAssistant | None) -> dict[str, str]:
    """Return the win/loss/draw letter codes matching the instance language."""
    language: str | None = getattr(getattr(hass, "config", None), "language", None)
    return (
        _FORM_LETTERS.get(language, _FORM_LETTERS["en"])
        if language
        else _FORM_LETTERS["en"]
    )


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
            FFBBRankSensor(coordinator),
            FFBBRankEvolutionSensor(coordinator),
            FFBBPouleSensor(coordinator),
            FFBBFormSensor(coordinator),
        ]
    )


class FFBBSensorBase(CoordinatorEntity[FFBBDataUpdateCoordinator], SensorEntity):
    """Base class for FFBB Tracker sensors."""

    _attr_has_entity_name = True
    _attr_attribution = "Données fournies par competitions.ffbb.com"

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
            ATTR_IS_STALE: match.is_stale,
        }


class FFBBNextMatchOpponentSensor(FFBBSensorBase):
    """Sensor tracking the opponent of the next match with venue metadata."""

    _attr_translation_key = "next_match_opponent"

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
    def entity_picture(self) -> str | None:
        """Return the opponent club's logo, so it renders natively across HA."""
        if self.coordinator.data and self.coordinator.data.next_match:
            return self.coordinator.data.next_match.opponent_logo_url
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return opponent attributes and venue navigation links."""
        if not self.coordinator.data or not self.coordinator.data.next_match:
            return {}

        match = self.coordinator.data.next_match
        attrs: dict[str, Any] = {
            "opponent_club_id": match.opponent_club_id,
            "team_url": match.team_url,
            "opponent_url": match.opponent_url,
            ATTR_IS_HOME: match.is_home,
            ATTR_GYM_NAME: match.gym_name,
            ATTR_GYM_ADDRESS: match.gym_address,
            ATTR_GYM_CITY: match.gym_city,
            "formatted_address": match.formatted_address,
            ATTR_NAVIGATION_URL: None,
            ATTR_GOOGLE_MAPS_URL: None,
            ATTR_WAZE_URL: None,
            ATTR_TEAM_LOGO_URL: match.team_logo_url,
            ATTR_OPPONENT_LOGO_URL: match.opponent_logo_url,
            ATTR_IS_STALE: match.is_stale,
        }

        if match.formatted_address:
            encoded = quote(match.formatted_address)
            attrs[ATTR_NAVIGATION_URL] = f"geo:0,0?q={encoded}"
            attrs[ATTR_GOOGLE_MAPS_URL] = (
                f"https://www.google.com/maps/dir/?api=1&destination={encoded}"
            )
            attrs[ATTR_WAZE_URL] = f"https://www.waze.com/ul?q={encoded}&navigate=yes"

        return attrs


class FFBBNextMatchVenueTypeSensor(FFBBSensorBase):
    """Sensor tracking whether the next match is played at home or away."""

    _attr_translation_key = "next_match_venue_type"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["home", "away"]  # noqa: RUF012

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
            ATTR_TEAM_LOGO_URL: match.team_logo_url,
            ATTR_OPPONENT_LOGO_URL: match.opponent_logo_url,
            ATTR_IS_STALE: match.is_stale,
        }


class FFBBNextMatchLocationSensor(FFBBSensorBase):
    """Sensor exposing the formatted address of the next match gym."""

    _attr_translation_key = "next_match_location"

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
            ATTR_TEAM_LOGO_URL: match.team_logo_url,
            ATTR_OPPONENT_LOGO_URL: match.opponent_logo_url,
            ATTR_IS_STALE: match.is_stale,
        }

        if match.formatted_address:
            encoded = quote(match.formatted_address)
            attrs[ATTR_NAVIGATION_URL] = f"geo:0,0?q={encoded}"
            attrs[ATTR_GOOGLE_MAPS_URL] = (
                f"https://www.google.com/maps/dir/?api=1&destination={encoded}"
            )
            attrs[ATTR_WAZE_URL] = f"https://www.waze.com/ul?q={encoded}&navigate=yes"

        return attrs


class FFBBLastMatchDateSensor(FFBBSensorBase):
    """Sensor tracking the date and time of the last played match."""

    _attr_translation_key = "last_match_date"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

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
            ATTR_IS_HOME: match.is_home,
            ATTR_TEAM_LOGO_URL: match.team_logo_url,
            ATTR_OPPONENT_LOGO_URL: match.opponent_logo_url,
        }


class FFBBLastMatchOpponentSensor(FFBBSensorBase):
    """Sensor tracking the opponent of the last played match."""

    _attr_translation_key = "last_match_opponent"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the last match opponent sensor."""
        super().__init__(coordinator, "last_match_opponent")

    @property
    def native_value(self) -> str | None:
        """Return the name of the last opponent."""
        if self.coordinator.data and self.coordinator.data.last_match:
            return self.coordinator.data.last_match.opponent_name
        return None

    @property
    def entity_picture(self) -> str | None:
        """Return the opponent club's logo, so it renders natively across HA."""
        if self.coordinator.data and self.coordinator.data.last_match:
            return self.coordinator.data.last_match.opponent_logo_url
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return both clubs' logo URLs and team links for the last played match."""
        if not self.coordinator.data or not self.coordinator.data.last_match:
            return {}

        match = self.coordinator.data.last_match
        return {
            "team_url": match.team_url,
            "opponent_url": match.opponent_url,
            ATTR_TEAM_LOGO_URL: match.team_logo_url,
            ATTR_OPPONENT_LOGO_URL: match.opponent_logo_url,
        }


class FFBBLastMatchResultSensor(FFBBSensorBase):
    """Sensor tracking the result of the last played match."""

    _attr_translation_key = "last_match_result"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["win", "loss", "draw"]  # noqa: RUF012

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


class FFBBRankSensor(FFBBSensorBase):
    """Sensor tracking the rank position of the team in the pool."""

    _attr_translation_key = "rank"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the rank sensor."""
        super().__init__(coordinator, "rank")

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
class FFBBRankEvolutionExtraData(ExtraStoredData):
    """Extra data stored for rank evolution restoration."""

    current_position: int | None
    previous_position: int | None

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation for storage."""
        return {
            "current_position": self.current_position,
            "previous_position": self.previous_position,
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> FFBBRankEvolutionExtraData | None:
        """Reconstruct from stored dict, or None if malformed."""
        if not isinstance(restored, dict):
            return None
        return cls(
            current_position=_safe_int_value(restored.get("current_position")),
            previous_position=_safe_int_value(restored.get("previous_position")),
        )


class FFBBRankEvolutionSensor(FFBBSensorBase, RestoreSensor):
    """Sensor tracking rank progression or regression."""

    _attr_translation_key = "rank_evolution"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the rank evolution sensor."""
        super().__init__(coordinator, "rank_evolution")
        self._current_position: int | None = None
        self._previous_position: int | None = None

    @property
    def extra_restore_data(self) -> FFBBRankEvolutionExtraData:
        """Return data to be restored on next startup."""
        return FFBBRankEvolutionExtraData(
            current_position=self._current_position,
            previous_position=self._previous_position,
        )

    async def async_added_to_hass(self) -> None:
        """Restore previous positions on integration startup."""
        await super().async_added_to_hass()

        last_extra_data = await self.async_get_last_extra_data()
        if last_extra_data is not None:
            restored = FFBBRankEvolutionExtraData.from_dict(last_extra_data.as_dict())
            if restored is not None:
                self._current_position = restored.current_position
                self._previous_position = restored.previous_position

        if self.coordinator.data and self.coordinator.data.team_standing:
            pos = _safe_int_value(self.coordinator.data.team_standing.position)
            if pos is not None:
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
                if self._current_position is None:
                    self._current_position = new_pos
                    self._previous_position = new_pos
                elif new_pos != self._current_position:
                    self._previous_position = self._current_position
                    self._current_position = new_pos
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> str:
        """Return the evolution delta (+X, -X, 0) or '-' if no rank yet."""
        if self._current_position is None or self._previous_position is None:
            return "-"

        diff = self._previous_position - self._current_position
        if diff > 0:
            return f"+{diff}"
        if diff < 0:
            return str(diff)
        return "0"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return position tracking attributes."""
        attrs: dict[str, Any] = {
            "current_position": self._current_position,
            "previous_position": self._previous_position,
        }
        if self._current_position is None:
            attrs["status"] = "En attente du premier classement officiel"
        return attrs


class FFBBPouleSensor(FFBBSensorBase):
    """Sensor displaying the assigned pool name."""

    _attr_translation_key = "poule"

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
            "url": f"https://competitions.ffbb.com/poule/{self.coordinator.poule_id}",
        }


class FFBBFormSensor(FFBBSensorBase):
    """Sensor summarizing the team's recent results as a compact form string."""

    _attr_translation_key = "form"

    def __init__(self, coordinator: FFBBDataUpdateCoordinator) -> None:
        """Initialize the form sensor."""
        super().__init__(coordinator, "form")

    def _recent_played_matches(self) -> list[Any]:
        """Return the last _FORM_WINDOW played matches, most recent first."""
        if not self.coordinator.data:
            return []
        played = [
            match
            for match in self.coordinator.data.fixtures
            if match.is_played and match.result
        ]
        return list(reversed(played[-_FORM_WINDOW:]))

    @property
    def native_value(self) -> str | None:
        """Return the compact form string, most recent match first."""
        recent = self._recent_played_matches()
        if not recent:
            return None

        letters = _get_form_letters(getattr(self, "hass", None))
        return "-".join(letters[match.result] for match in recent)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return win/loss/draw counts and the current streak."""
        recent = self._recent_played_matches()
        if not recent:
            return {}

        letters = _get_form_letters(getattr(self, "hass", None))

        streak_result = recent[0].result
        streak_length = 0
        for match in recent:
            if match.result != streak_result:
                break
            streak_length += 1

        return {
            "matches_considered": len(recent),
            "wins": sum(1 for match in recent if match.result == "win"),
            "losses": sum(1 for match in recent if match.result == "loss"),
            "draws": sum(1 for match in recent if match.result == "draw"),
            "current_streak": f"{streak_length}{letters[streak_result]}",
        }
