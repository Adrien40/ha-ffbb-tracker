"""Constants for the FFBB Tracker integration."""

from typing import Final

DOMAIN: Final = "ffbb_tracker"

# Official public API base URL
API_BASE_URL: Final = "https://api.ffbb.app"

# Public Directus API token exposed client-side by the official competitions.ffbb.com web application.
# This is a public read-only token required by Directus, not a sensitive private credential.
DEFAULT_DIRECTUS_TOKEN: Final = "bvBdsqADOnQmvFNzDRQnzYYw2g2J1ad6"
DEFAULT_TIMEOUT: Final = 15

# Configuration keys
CONF_ENGAGEMENT_ID: Final = "engagement_id"
CONF_ORGANISME_ID: Final = "organisme_id"
CONF_TEAM_NAME: Final = "team_name"
CONF_COMPETITION_NAME: Final = "competition_name"
CONF_POULE_ID: Final = "poule_id"

# Coordinator base intervals (in minutes)
DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 1440

# Live match polling settings
LIVE_SCAN_INTERVAL: Final = 5
LIVE_WINDOW_BEFORE_MINUTES: Final = 60
LIVE_WINDOW_AFTER_HOURS: Final = 3

CONF_LIVE_POLLING: Final = "live_polling"
DEFAULT_LIVE_POLLING: Final = True

CONF_LIVE_SCAN_INTERVAL: Final = "live_scan_interval"
DEFAULT_LIVE_SCAN_INTERVAL: Final = LIVE_SCAN_INTERVAL
MIN_LIVE_SCAN_INTERVAL: Final = 2
MAX_LIVE_SCAN_INTERVAL: Final = 15

CONF_LIVE_WINDOW_AFTER_HOURS: Final = "live_window_after_hours"
DEFAULT_LIVE_WINDOW_AFTER_HOURS: Final = LIVE_WINDOW_AFTER_HOURS
MIN_LIVE_WINDOW_AFTER_HOURS: Final = 1
MAX_LIVE_WINDOW_AFTER_HOURS: Final = 6

# Options
CONF_SCAN_INTERVAL: Final = "scan_interval"

# Attributes and keys
ATTR_OPPONENT: Final = "opponent"
ATTR_MATCH_DATE: Final = "match_date"
ATTR_LOCATION: Final = "location"
ATTR_IS_HOME: Final = "is_home"
ATTR_GYM_NAME: Final = "gym_name"
ATTR_GYM_ADDRESS: Final = "gym_address"
ATTR_GYM_CITY: Final = "gym_city"
ATTR_NAVIGATION_URL = "navigation_url"
ATTR_GOOGLE_MAPS_URL: Final = "google_maps_url"
ATTR_WAZE_URL: Final = "waze_url"
ATTR_TEAM_SCORE: Final = "team_score"
ATTR_OPPONENT_SCORE: Final = "opponent_score"
ATTR_RESULT: Final = "result"
ATTR_STANDINGS: Final = "standings"
