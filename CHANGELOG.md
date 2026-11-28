# FFBB Tracker - Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0] — 2026-11-28

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

### ✨ New features
- Expose `formatted_address` consistently across `next_match_location`, `last_match_date` (along with `gym_address`), every fixture row in the `poule` sensor's `calendar` attribute, and `ffbb_tracker.get_next_matches` responses (along with `postal_code`) so Lovelace cards and automations always have full venue addresses.
- Expose `initial_points` (plus `irregularities` for the Lovelace card's **`I`** column) from Directus's `classements.pointsInitiaux`, as well as `engagement_id` on the `poule` sensor, every `standings` row, and every `calendar` fixture (`home_engagement_id` / `away_engagement_id`).
- Label odd-sized pool bye rows (`idEngagement: null`) in standings as `"Exempt"` instead of `"N/A"`, matching calendar bye fixtures.

### 🔄 Changed
- Streamline `season_rollover` and `phase_change` repair flows by removing the redundant intermediate confirmation step and navigating straight to the candidate proposal or team search while preserving entity-retention guidance.
- Include `{team_name}` in the `token_refresh_failing` repair issue title so multi-team installations immediately identify which tracked entry is affected.

### 🐛 Fixed
- **Standings club logos**: request `classements.idEngagement.idOrganisme.logo` in `_POULE_FIELDS` so standings logos are populated immediately at the start of a season or phase, even before fixtures are published.
- **Coordinator polling on error**: reset `update_interval` to the configured base scan interval whenever `_async_update_data` raises `UpdateFailed`, preventing polling from staying stuck at a 30 s boundary wake-up or 2 min live interval during outages, HTTP 429 rate limits, or end-of-season 404s.
- **Token refresh health issue**: check `token_refresh_failures` even when `_async_update_data` fails with `FFBBApiError` (e.g. when both dynamic token refresh and the fallback token fail with HTTP 401/403).
- **Outdated-response safety net (`recheck`)**: rotate the `refresh` request shape every 10 minutes instead of hourly during the post-match early window, run missing-result rechecks on shared pool cache hits for sibling teams (while sharing the pool-level cooldown), adopt fresher responses when a pending match was postponed to a future date, and ignore `"Exempt"` bye rounds when checking for pending results.
- **Repair & entity migration lifecycle**: delete all team repair issues (`season_rollover`, `phase_change`, `token_refresh_failing`) and persisted 404 streaks when an entry is removed, reconfigured, or switched via repairs; auto-close stale repair flows if their config entry no longer exists; drop unused pool caches before updating `poule_id`; reset `rank_evolution` and discard restored `match_finished` / `rank_changed` event state on both engagement switches and same-engagement pool switches.
- **`match_finished` event**: only record `_known_last_match_id` once `last_match.result` is populated so matches flagged as played before scores are entered still fire the event when scores arrive.
- **404 persistence startup**: guard `_async_load_not_found` with an `asyncio.Lock` so concurrent coordinator startups wait for disk loading to complete before reading in-memory streaks.
- **Fixtures, time zones, calendar & form**: interpret naive FFBB `date_rencontre` timestamps in `Europe/Paris` regardless of the Home Assistant host time zone; sort undated played fixtures (forfeits) before dated played fixtures so they never stick as `last_match` all season; prevent bye rounds flagged as played from becoming `last_match`; format calendar event result lines in `home - away` order matching the summary and require both scores; compute `FFBBFormSensor`'s `current_streak` across the full season; support regional BCP-47 language subtags (`fr-FR`, `fr-BE`, `fr-CA`) in `form` and `calendar`.
- **JSON sanitization, team search & state bounds**: anchor `URL_ID_PATTERN` to full numeric path segments after `/equipe(s)`; surface real fallback club-search errors (`too_many_clubs`, `cannot_connect`, `unknown`); avoid preselecting the first team when a filter matches nothing; guard against non-string dates or address fields, booleans in `_safe_int`, flattened scalar Directus relation IDs, and free-text sensor states over 255 characters.
- **Telegram Blueprints & documentation**: add explicit selector descriptions to the full Telegram notification pack, restrict result blueprint entity discovery to `sensor.*` with exact `_dernier_match_*` / `_last_match_*` matching and `result_grace_hours` filtering, prioritize permanent `last_match_*` sensors on `Actualiser` callbacks, switch result and rank-change blueprints to `mode: queued` so filtered events never cancel manual refresh callbacks, and convert UTC timestamps via `as_local(...)` in the README Companion App examples.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

## [1.0.0] — 2026-11-27

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

### ✨ New features
- First stable release.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀
