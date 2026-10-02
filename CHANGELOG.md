# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.8.7] - 2026-10-02

### Fixed

- **Gym postal code was never retrieved.** The FFBB API exposes it on the
  gym's commune (`salle.commune.codePostal`), not on the gym itself, and the
  request never asked for it. Gym addresses, the navigation links (`geo:`,
  Google Maps, Waze) and the calendar event location now include the postal
  code.
- **Large sensor attributes no longer break history recording.** The full
  season `calendar` (pool sensor) and the full `standings` table (rank
  sensor) can exceed Home Assistant's 16 KB recorder limit, in which case
  *all* of the sensor's attributes were dropped from history with a warning in
  the logs. Both attributes are now excluded from the recorder. They remain
  fully available live to dashboards, templates and automations.
- **Telegram blueprints showed UTC times.** Kick-off time and match day were
  formatted straight from the UTC timestamp, so a 20:00 match was announced at
  19:00 (or 18:00 in summer), and the day could be wrong around midnight. The
  day-before reminder and the "in N days" banner also relied on the host
  system's time zone instead of Home Assistant's. Everything now follows the
  time zone configured in Home Assistant.
- **The "live window after match" option now works beyond 3 hours.** The
  options accept 1 to 6 hours, but an unplayed match was always dropped from
  the next match after a hardcoded 3 hours, so values of 4 to 6 hours had no
  effect: fast polling stopped early while the result was still awaited. The
  match now stays the next match for the longer of 3 hours and the configured
  window. Windows of 1 to 3 hours behave exactly as before.
- **Actions disappeared during a reload.** `refresh`, `get_next_matches` and
  `get_standings` were registered when a team was loaded and removed when the
  last team was unloaded. Since every options change reloads the entry, an
  automation calling one of them at that moment failed with "service not
  found", and automations referencing them could not be validated while no
  team was loaded. They are now registered once at startup and never removed.

### Changed

- Targeting a team that is not loaded (setup in progress or failed, entry
  disabled) with an explicit `entry_id` now raises a clear `entry_not_loaded`
  error instead of silently doing nothing or returning an empty result.
  Calls without `entry_id` are unchanged: teams that are not loaded are
  skipped.
- Unloading a team no longer clears the integration's shared state. The rate
  limiter that spaces out requests is kept across reloads, and a pool's cached
  data is only discarded once no loaded team tracks that pool any more.

### Upgrade notes

- **Update the blueprints.** Home Assistant copies blueprints when they are
  imported and does not update them with the integration. To get the time zone
  fix, update `match_notifications_telegram.yaml` and
  `match_result_notification_telegram.yaml`. Only the result blueprint declares
  a source URL, so it is the only one offering *Re-import blueprint* on the
  blueprint page; for the other, copy the file from
  `blueprints/automation/ffbb_tracker/` over your existing copy (or import it
  again from its GitHub URL).
- **Expect one extra notification.** The location sensor now includes the
  postal code, so its value changes once after the upgrade. If you use the
  "Match notifications" blueprint, that change is reported once as
  "Mise à jour du match". It does not repeat.
- Attributes `calendar` and `standings` are no longer stored in history. Data
  already recorded is not affected.

### Internal

- Test suite extended from 256 to 276 tests, including rendering tests that
  load the real blueprint files and evaluate their templates in the Europe/Paris
  time zone.
- The test fixture now mirrors the real API shape for the gym postal code,
  which is why the missing field was never caught before.
- `quality_scale.yaml` updated to reflect where the actions are registered.
