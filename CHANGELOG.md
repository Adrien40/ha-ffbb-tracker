# FFBB Tracker - Changelog

## 0.9.0

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

This release makes switching a team to its new IDs (reconfiguring, or fixing a season rollover) keep its entities, and fixes the rank evolution sensor, which had never kept its value across restarts.

### 🐛 Bug fixes
- **Reconfiguring a team, or fixing a season rollover, recreated all its entities.** Measured on 18 entities: with the same team and competition names 17 entity IDs survived (the 18th, which had been renamed by hand, was reset); with a renamed team or competition, none did. A season rollover usually renames one or both (age category, division, season label), so the automations and dashboards using these entities broke, while the README claimed the opposite. The team's existing entities and device now follow it to its new engagement: same entity IDs, custom names and IDs, history and area. If something already uses the new ID, the entities are recreated as before (a warning says which one could not be kept).
- **The rank evolution sensor never kept its value across restarts.** It exposed the positions it needs to save under a name Home Assistant doesn't read, so nothing was ever saved and the evolution started over at every restart, although the README said it was preserved. Fixed: it is now kept across restarts (the first restart after upgrading still starts over, since nothing was ever stored). It still starts over when the entry is switched to another team, since positions in another pool can't be compared.

### 🧰 Maintenance
- Test suite grown from 473 to 488 tests, 99 % coverage. The migration is tested through the real reconfiguration (four cases: same names, renamed competition, renamed team, both) and through the repair, with a hand-customized entity, a device area, and the awkward cases (an entity or a device already using the new ID). The rank evolution is tested through Home Assistant's own save and restore, a layer the previous tests never reached: they checked the property and the restore logic separately, never that Home Assistant would call it.
- Home Assistant lets two devices share the same identifiers instead of refusing the second one, so the migration checks for a collision itself rather than relying on an error.

### 📚 Documentation
- README: what it says about reconfiguring a team, and about the rank evolution sensor after a restart, now describes what really happens.

### 📋 Upgrade notes
- Nothing to do. Entities that an earlier reconfiguration already recreated keep their current IDs: only reconfigurations from now on carry the existing entities over.
- On reload the device is renamed after the new team and competition; entity IDs don't change with it.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

## 0.8.9

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

This release makes updates more robust against hiccups of the FFBB API, fixes the season rollover repair (it never showed the user anything), and tells a new match from a change in the Telegram notifications.

### ✨ New features
- The `match_number` attribute, already on the next match date sensor, is now also on the next match opponent and location sensors. The notifications blueprint uses it (see below).
- The notifications blueprint can now be updated with *Re-import blueprint*, like the result blueprint: it declares its source URL.

### 🐛 Bug fixes
- **The season rollover repair did nothing visible.** Its button started the reconfiguration flow in the background, and Home Assistant doesn't show flows started that way. The team search now runs inside the repair dialog: search by club name, team URL or team ID, pick the team, and the existing entry is switched over to its new IDs (the old team's device is removed and the entry reloads). It reuses the config flow's search, so both behave the same. Two smaller defects of that dialog are fixed as well: the first step forwarded the data Home Assistant starts every flow with to the confirmation step, which took it for an answer and skipped the explanation, and the texts never received the team name.
- **The notifications blueprint said "Mise à jour du match" when a new match took the place of the previous one** (for instance right after a result), instead of "Nouveau match programmé". It now compares the match number before and after: another number is a new match, the same number with other values is an update.

### 🛡️ Hardening
- **Transient API errors are retried.** HTTP 429, 502, 503 and 504 are retried up to twice, after the delay the server asks for (`Retry-After`, in seconds or as a date) or, without one, after 1 then 2 seconds. A server asking for more than 10 seconds makes the update fail at once instead of holding it; the next scheduled poll tries again.
- **Each HTTP attempt has its own 15 s timeout.** The token refresh and the retry used to share the first request's budget.
- **Error messages are short.** An error page from a proxy or CDN is reduced to plain text and cut at 200 characters instead of filling the logs with HTML.
- **Teams of the same pool no longer wait for the same failure one after the other.** When a pool request fails, the teams that were waiting behind it get that failure immediately instead of each repeating the request and waiting for the same timeout. A refresh started afterwards always queries the API itself, so the *Refresh* button and the next poll are never blocked.

### 🧰 Maintenance
- Compatibility with the oldest supported Home Assistant is now checked: the suite passes on Home Assistant 2026.3.0 (the minimum in `hacs.json`) and 2026.3.1, as well as on the latest release, with no change to the integration. `tests.yaml` gained a second job running the suite on 2026.3.1 (`requirements_test_min.txt`); the test tool has no release for 2026.3.0 itself, so that is the first patch release it supports.
- Shared code: the five platforms build their device and the two location sensors their navigation links in one place (`entity.py`), and the team search is shared by the config flow and the repair (`team_picker.py`). Behaviour is unchanged. An unreachable guard in the poll code is removed.
- Test suite grown from 401 to 473 tests, 99 % coverage. The season rollover repair is covered end to end through Home Assistant's own repairs manager, the layer that decides what the dialog shows and that the previous tests never exercised. New consistency tests check the minimum-version test setup against `hacs.json` and the workflow.

### 📚 Documentation
- README: new *How the integration identifies itself* entry in the limitations, explaining the public access key and the browser-like headers it uses, and that it never uses credentials. The descriptions of the season rollover repair now match how it works.

### 📋 Upgrade notes
- **Update the notifications blueprint** (`match_notifications_telegram.yaml`) to get the "Nouveau match programmé" wording. Home Assistant doesn't update blueprints with the integration. It now declares its source URL, so after replacing your copy once, *Re-import blueprint* works for it too.
- In the worst case an update can now last longer when the API is struggling (up to two waits of 10 seconds). It succeeds more often instead of failing and waiting for the next poll.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

## 0.8.8

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

This release adds a safety net against outdated responses from the FFBB API, and a diagnostics report to find out why a score is late.

### ✨ New features
- **API diagnostics.** The diagnostics download (device menu ⋮ › *Download diagnostics*) now has an `api` section: when the FFBB API was last queried, the cache-related headers of its answers (`Age`, `Cache-Control`, `Date`, `ETag`, …), the matches of your team that started more than 3 hours ago and still have no result (match number, age, and the raw `joue` / score fields), and what the safety net below did. It contains no team name, address or other personal data.

### 🛡️ Hardening
- **Safety net against outdated API responses.** The FFBB API was observed, on one pool, answering the integration's usual request with a copy of the data that predated the published scores (and a schedule change) for several days, while answering correctly for a slightly different request. If a match of your team started between 3 hours and 7 days ago and still has no result, the integration now sends one extra request, formulated differently (same fields in another order, `Cache-Control: no-cache`), and uses its answer if it contains more results. It runs at most once an hour, only in that situation, and never fails the update if it errors. Normal requests are unchanged.
- This is a precaution: the cause of the outdated responses could not be identified, and it was no longer reproducible by the time this was written, so the safety net has not been seen working on a real outdated answer. The diagnostics will show whether it ever fires.

### 🧰 Maintenance
- Test suite grown from 350 to 401 tests, 99 % coverage. The normal pool request is pinned by a test (exact fields, parameters and headers), since the safety net relies on its extra request being different.
- The "has a result" rule is now a single function shared by the parser and the safety net (behaviour unchanged).

### 📚 Documentation
- README: new troubleshooting entry for a score that is late, explaining how to download the diagnostics.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

## 0.8.7

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

This release fixes five bugs found in an audit (the gym postal code, a recorder size limit, UTC times in the Telegram blueprints, the live-polling window and the service actions), and rebuilds the CI: one workflow per check, a coverage gate, and GitHub releases published from this changelog.

### 🐛 Bug fixes
- **Gym postal code was never retrieved.** The FFBB API exposes it on the gym's commune (`salle.commune.codePostal`), not on the gym itself, and the request never asked for it. Gym addresses, the navigation links (`geo:`, Google Maps, Waze) and the calendar event location now include the postal code.
- **Large sensor attributes no longer break history recording.** The full season `calendar` (pool sensor) and the full `standings` table (rank sensor) can exceed Home Assistant's 16 KB recorder limit, in which case *all* of the sensor's attributes were dropped from history with a warning in the logs. Both attributes are now excluded from the recorder; they remain fully available live to dashboards, templates and automations.
- **The Telegram blueprints showed UTC times.** Kick-off time and match day were formatted straight from the UTC timestamp, so a 20:00 match was announced at 19:00 (or 18:00 in summer), and the day could be wrong around midnight. The day-before reminder and the "in N days" banner also relied on the host system's time zone instead of Home Assistant's. Everything now follows the time zone configured in Home Assistant.
- **The "live window after match" option now works beyond 3 hours.** The options accept 1 to 6 hours, but an unplayed match was always dropped from the next match after a hardcoded 3 hours, so values of 4 to 6 hours had no effect: fast polling stopped early while the result was still awaited. The match now stays the next match for the longer of 3 hours and the configured window. Windows of 1 to 3 hours behave exactly as before.
- **The actions disappeared during a reload.** `refresh`, `get_next_matches` and `get_standings` were registered when a team was loaded and removed when the last team was unloaded. Since every options change reloads the entry, an automation calling one of them at that moment failed with "service not found", and automations referencing them could not be validated while no team was loaded. They are now registered once at startup, in `async_setup`, and never removed.

### 🛡️ Hardening
- Targeting a team that is not loaded (setup in progress or failed, entry disabled) with an explicit `entry_id` now raises a clear `entry_not_loaded` error instead of silently doing nothing or returning an empty result. Calls without `entry_id` are unchanged: teams that are not loaded are skipped.
- Unloading a team no longer clears the integration's shared state: the rate limiter that spaces out requests is kept across reloads, and a pool's cached data is only discarded once no loaded team tracks that pool any more.

### 🧰 Maintenance
- CI: one workflow per check, so each has its own badge: `tests.yaml` (pytest + coverage, 95 % minimum, the build fails below it; the Codecov upload is removed), `ruff.yaml`, `mypy.yaml`, `hassfest.yaml` and `hacs.yaml` (split out of the former `validate.yaml`), plus `release.yaml`. `codeql.yaml` is unchanged.
- Typing: CI now runs `mypy --strict` in its own *Typing* workflow, as the Platinum `strict-typing` rule already claimed (it was only run without `--strict` before). It passes with zero errors.
- Releases: pushing a version tag (`v0.8.7`) publishes the GitHub release from this changelog, with the French section folded in. The tag must match the manifest version and this file must contain a matching `## 0.8.7` section, otherwise the release fails instead of publishing empty notes (`scripts/release_notes.py`).
- `pyproject.toml` scopes coverage to the integration (`pytest --cov` with no argument), and `mypy` is added to `requirements_test.txt`.
- The manifest now declares `quality_scale: platinum`, matching the README badge. hassfest only checks `quality_scale.yaml` for core integrations, so tests keep it consistent instead: it must list exactly the 54 rules of Home Assistant 2026.9.2, with none left open and a reason for every exemption.
- Test suite grown from 256 to 350 tests, 99 % coverage: blueprint rendering tests (the real blueprint files, evaluated in the Europe/Paris time zone), service lifecycle tests, release script tests, and consistency tests that keep the README badges, the changelogs, the workflows and `quality_scale.yaml` in line with the repository.
- The test fixture now mirrors the real API shape for the gym postal code, which is why the missing field was never caught before.
- `quality_scale.yaml` updated to reflect where the actions are registered.

### 📚 Documentation
- README badges fixed and completed: the workflow badges pointed at `.yml` files while the workflows are `.yaml`, so none of them could display a status. There are now badges for tests, HACS, Hassfest, lint, `mypy --strict` and CodeQL, in both languages.
- Added `CHANGELOG.md` and `CHANGELOG.fr.md`.

### 📋 Upgrade notes
- **Update the blueprints.** Home Assistant copies blueprints when they are imported and does not update them with the integration. To get the time zone fix, update `match_notifications_telegram.yaml` and `match_result_notification_telegram.yaml`. Only the result blueprint declares a source URL, so it is the only one offering *Re-import blueprint* on the blueprint page; for the other, copy the file from `blueprints/automation/ffbb_tracker/` over your existing copy (or import it again from its GitHub URL).
- **Expect one extra notification.** The location sensor now includes the postal code, so its value changes once after the upgrade. If you use the "Match notifications" blueprint, that change is reported once as "Mise à jour du match". It does not repeat.
- Automations that pass an `entry_id` of a team that is disabled or failed to load now get an `entry_not_loaded` error instead of an empty result.
- The `calendar` and `standings` attributes are no longer stored in history. Data already recorded is not affected.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀
