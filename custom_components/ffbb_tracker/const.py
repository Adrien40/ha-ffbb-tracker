"""Constants for the FFBB Tracker integration."""

from typing import Final

DOMAIN: Final = "ffbb_tracker"

# Official public API base URL
API_BASE_URL: Final = "https://api.ffbb.app"

# Public Directus API token exposed client-side by the official competitions.ffbb.com web application.
# This is a public read-only token required by Directus, not a sensitive private credential.
DEFAULT_DIRECTUS_TOKEN: Final = "bvBdsqADOnQmvFNzDRQnzYYw2g2J1ad6"
DEFAULT_TIMEOUT: Final = 15

# Club logos are Directus assets served from the same host as the rest of
# the API (see FFBBClient.base_url) at /assets/<logo_id>. These query
# params ask Directus to pre-resize/transcode server-side rather than
# shipping a full-resolution source image to every dashboard.
LOGO_ASSET_HEIGHT: Final = 220
LOGO_ASSET_FORMAT: Final = "avif"

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

# An unplayed match stays the "next match" for at least this long after its
# scheduled start (late kick-offs, results published late). A live window
# longer than this extends it -- see FFBBDataUpdateCoordinator._process_poule_data.
NEXT_MATCH_GRACE_HOURS: Final = 3
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

# A 404 on the tracked poule/engagement persisting this many days is treated
# as a season rollover (the FFBB reassigns engagement/poule IDs every new
# season), not a transient outage. See
# FFBBDataUpdateCoordinator._handle_not_found in coordinator.py.
SEASON_ROLLOVER_THRESHOLD_DAYS: Final = 3

# Transient HTTP failures (rate limiting, gateway/availability errors) are
# retried a couple of times with a short, bounded wait before giving up, so a
# momentary hiccup doesn't turn into a failed update. A server asking us to
# wait longer than RETRY_MAX_WAIT seconds (Retry-After) is not waited for: the
# update fails and the next scheduled poll tries again.
RETRY_STATUSES: Final = (429, 502, 503, 504)
MAX_TRANSIENT_RETRIES: Final = 2
RETRY_BASE_DELAY: Final = 1.0
RETRY_MAX_WAIT: Final = 10.0
ERROR_BODY_MAX_LENGTH: Final = 200

# Safety net against an outdated poule response from the FFBB API (see
# FFBBDataUpdateCoordinator._async_recheck_missing_results). When a match of
# the tracked team started between STALE_RESULT_MIN_AGE_HOURS and
# STALE_RESULT_MAX_AGE_DAYS ago and still has no result, one extra request,
# formulated differently, checks whether the answer was an old copy. It runs
# at most once per STALE_RECHECK_MIN_INTERVAL_MINUTES.
STALE_RESULT_MIN_AGE_HOURS: Final = 3
STALE_RESULT_MAX_AGE_DAYS: Final = 7
STALE_RECHECK_MIN_INTERVAL_MINUTES: Final = 60

# Right after a match the score is what people are waiting for, and it is also
# when a stored copy of the normal request hurts most: a score published on the
# FFBB at 15:07 reached a user at 16:32, only once the 3 h rule above let the
# safety net run. So for a match that started more than
# STALE_RECHECK_EARLY_MIN_AGE_MINUTES ago and is still younger than
# STALE_RECHECK_EARLY_WINDOW_HOURS, the extra request is allowed much sooner
# and every STALE_RECHECK_EARLY_INTERVAL_MINUTES instead of once an hour. After
# that window it falls back to the rules above, so a match that was cancelled
# or forfeited does not keep costing a request every few minutes for a week.
STALE_RECHECK_EARLY_MIN_AGE_MINUTES: Final = 60
STALE_RECHECK_EARLY_WINDOW_HOURS: Final = 6
STALE_RECHECK_EARLY_INTERVAL_MINUTES: Final = 10

# Number of consecutive failed dynamic token refreshes (see
# FFBBClient.token_refresh_failures) after which the coordinator raises a
# repair issue warning that the integration may be running on a stale
# fallback token.
TOKEN_REFRESH_FAILURE_THRESHOLD: Final = 3

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

# Key in hass.data[DOMAIN] holding the unique IDs of rank evolution sensors that
# must not restore their last positions: their entity was just moved to another
# team (see team_picker.migrate_entities).
FRESH_RANK_EVOLUTION: Final = "_fresh_rank_evolution"

# Attribution shown on every entity.
ATTRIBUTION: Final = "Données fournies par competitions.ffbb.com"
ATTR_TEAM_SCORE: Final = "team_score"
ATTR_OPPONENT_SCORE: Final = "opponent_score"
ATTR_RESULT: Final = "result"
ATTR_STANDINGS: Final = "standings"
ATTR_ROUND: Final = "round"
ATTR_POINT_DIFFERENCE: Final = "point_difference"
ATTR_TEAM_LOGO_URL: Final = "team_logo_url"
ATTR_OPPONENT_LOGO_URL: Final = "opponent_logo_url"
ATTR_IS_STALE: Final = "is_stale"
