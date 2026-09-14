[![Français](https://img.shields.io/badge/Langue-Fran%C3%A7ais-blue)](README.fr.md) [![English](https://img.shields.io/badge/Language-English-red)](#)

# FFBB Tracker for Home Assistant 🏀
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/Adrien40/ha-ffbb-tracker)](https://github.com/Adrien40/ha-ffbb-tracker/releases)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

A **comprehensive Home Assistant integration** to track match results, schedules, and standings for basketball teams competing in FFBB (French Basketball Federation) championships, with no user account or private API key required. 🛡️

> ℹ️ **Good to know**: This integration queries the public Directus API used by the official `competitions.ffbb.com` web app, reusing the same public, read-only access token and browser User-Agent as the official site. No personal account or credentials are required. It fetches fixtures, results, and pool standings in a single optimized request.

If you find this project useful, you can support its development 🙏

<a href="https://www.buymeacoffee.com/adrien40"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" width="160"></a>

---

## ⚡ Overview
- 🏀 Full tracking of your favorite teams (Departmental, Regional, National)
- 📅 Complete match schedule natively synchronized in Home Assistant
- ⏱️ Live next match details: date, opponent, home or away status
- 🏀 Ready-to-use "Game day" and "Match in progress" binary sensors for simple automations, no templating needed
- 📍 One-click directions: direct Google Maps and Waze gym links generated in entity attributes
- 🏆 Last played match: final score, opponent, and outcome (win, loss, draw)
- 📊 Pool standings and dynamic rank evolution tracking (+1, -2, 0) with persistence across restarts
- 📈 Recent form sensor summarizing the last 5 results (e.g. `W-W-L-W-D`) with current streak
- ⚡ "Match finished" and "Rank change" event entities to trigger automations the instant something happens, not on every polling cycle
- 🧩 Ready-to-import automation blueprints for Telegram notifications, no templating required
- 🛠️ Three action services ready for automations and notification scripts (Telegram, pre-game alerts)
- 🔧 Automatic repair notification if a team can no longer be found (season rollover), pointing you straight to Reconfigure
- 🔍 Fast setup: search by club name, federal club code, or direct team URL copy-paste
- ⚙️ Simple 2-minute installation via HACS

---

## 📸 Preview in Home Assistant

### 🔍 Entities Overview
<p align="center">
  <img src="docs/screenshots/entities_overview.png" width="415" alt="Entities Overview">
</p>
<p align="center">
  <em>🔍 Entities automatically created for each tracked team</em>
</p>

### 📅 Official Calendar
<p align="center">
  <img src="docs/screenshots/calendar.png" width="475" alt="Official Calendar Event">
</p>
<p align="center">
  <em>📅 Match fixtures, venue address, and results directly in your calendar</em>
</p>

---

### 💡 Why this integration?
Designed for basketball players, parents, coaches, and supporters wishing to integrate team schedules and results into their smart home:

* **🛡️ Zero account needed:** No personal credentials or private tokens required; the integration utilizes the public token provided for federal mobile web apps.
* **🚗 Instant GPS navigation:** No more searching for gym addresses on game days; complete street addresses, postal codes, and direct GPS launch links are available in your dashboard and notification engines.
* **📈 Reliable rank tracking:** Position changes (+1, -2, 0) are saved to Home Assistant permanent storage and persist across system restarts without losing track of previous standings.
* **⚡ Lightweight and polite:** Polling is centralized per pool to minimize requests and prevent unnecessary load on federal servers.

---

### ✅ Compatibility and Requirements
* 🏷️ **Supported competitions**: All teams and championships available on `competitions.ffbb.com` (seniors, youth, departmental, regional, and national divisions).
* ⚙️ **Required Home Assistant version**: Version 2026.2.3 or higher.
* 🌐 **Internet connection**: Required to fetch data from federation servers.
* 🔍 **Search methods**:
  * By club or city name (e.g., *Basket Landes*, *Paris*).
  * By official club code (e.g., *NAQ0040141*).
  * By full team URL copied from `competitions.ffbb.com` or raw numeric engagement ID.

---

### ✨ Key Features
* 📅 **Native Home Assistant Calendar**: Browse the entire season in your calendar dashboard with tip-off times, round numbers, final scores, and gym details.
* 🚗 **Integrated GPS Navigation**: Ready-to-use Waze and Google Maps deep links in sensor attributes to start navigation in one tap.
* 📈 **Rank Evolution Sensor**: Detects rank shifts between updates, featuring an adaptive dynamic icon (arrow up, arrow down, or neutral dash).
* 📊 **Recent Form Sensor**: Compact string of the last 5 results (e.g. `W-W-L-W-D`), with win/loss/draw counts and the current streak in its attributes.
* ⚡ **Event Entities for Instant Automations**: `event.*_match_finished` fires exactly once per new result (win/loss/draw), and `event.*_rank_changed` fires exactly once when the pool position moves — both survive Home Assistant restarts without re-firing on old data, unlike triggering on a sensor's state.
* 🔄 **Seamless Reconfiguration**: Switch team or pool directly using the "Reconfigure" button without removing the entry or leaving orphaned devices behind.
* 🛠️ **Dedicated Actions (Services)**:
  * `ffbb_tracker.refresh`: Triggers an immediate manual data update.
  * `ffbb_tracker.get_next_matches`: Returns upcoming fixtures as a structured dictionary for automations.
  * `ffbb_tracker.get_standings`: Returns full pool standings (points, wins, losses, played games).
* 🔄 **One-Click Manual Refresh**: An explicit `button` entity to trigger an on-demand update anytime without waiting for the next polling cycle.
* ⚙️ **Dynamic Polling & Live Match Options**: Adjust the regular polling interval (15 to 1440 minutes), enable fast polling on match days (2 to 15 minutes), and set the score-wait window.

---

### 🚀 Installation

#### Via HACS (Recommended)
While waiting for inclusion in the default HACS repository list, you can easily add this as a custom repository:

1. Open **HACS** in your Home Assistant web interface.
2. Click the 3 dots in the top-right corner and select **Custom repositories**.
3. In the **Repository** field, paste: `https://github.com/Adrien40/ha-ffbb-tracker`
4. In the **Type** dropdown, select **Integration**, then click **Add**.
5. Once added, click **Download** on the integration card that appears (select the latest release).
6. **Restart Home Assistant completely**.
7. Go to **Settings** > **Devices & services** > **Add integration**, and search for **FFBB Tracker**.

#### Manual Installation
1. Download the latest release zip archive from the [Releases](https://github.com/Adrien40/ha-ffbb-tracker/releases) page.
2. Copy the `custom_components/ffbb_tracker` folder into your Home Assistant `custom_components` directory.
3. Restart Home Assistant.

---

### 📊 Available Sensors and Entities

Each configured team creates a dedicated device exposing 12 sensors, 2 binary sensors, 2 event entities, 1 button, and 1 calendar:

| Entity | Class / Unit | Description |
| :--- | :--- | :--- |
| 🔄 **Refresh** | Button | Manually triggers an immediate coordinator update. |
| 🗓️ **Schedule Calendar** | Calendar | Official calendar containing all scheduled and completed fixtures for the pool. |
| 🏀 **Game Day** | Binary sensor | On whenever the tracked team has a match scheduled for today (local date). |
| 🏟️ **Match in Progress** | Binary sensor | On from shortly before kickoff until the result is published (or the waiting window expires). |
| ⚡ **Match Finished** | Event | Fires once when a new match result is published, with opponent, score, point difference, venue (home/away), round, and gym details in event data. |
| ⚡ **Rank Change** | Event | Fires once when the pool position actually moves, with the old and new position in event data (`event.<team>_rank_changed`). |
| 📊 **Rank** | Integer | Current position of the team in its pool (`sensor.<team>_rank`). *(Includes full standings table in attributes)* |
| 📈 **Rank Evolution** | Text | Position difference (`+1`, `-2`, `0`) with dynamic icon (`sensor.<team>_rank_evolution`). |
| 📉 **Recent Form** | Text | Last 5 results as a compact string (e.g. `W-W-L-W-D`). *(Includes win/loss/draw counts and current streak in attributes)* |
| 📅 **Last Match: Date** | Timestamp | Date and time of the last played game. |
| 👥 **Last Match: Opponent** | Text | Name of the last opponent faced. |
| 🏆 **Last Match: Result** | Status | Outcome of the last match: **Win** (`win`), **Loss** (`loss`), or **Draw** (`draw`). |
| 🔢 **Last Match: Score** | Text | Formatted final score (e.g., `78 - 65`). |
| 🏆 **Pool** | Text | Official name of the pool and current championship phase. |
| 📅 **Next Match: Date** | Timestamp | Scheduled tip-off date and time of the upcoming match. |
| 👥 **Next Match: Opponent** | Text | Name of the upcoming opponent. *(Contains gym details, Maps and Waze URLs in attributes)* |
| 📍 **Next Match: Location** | Text | Full formatted address of the gym where the game will take place. |
| 🏟️ **Next Match: Venue** | Status | Indicates whether the game is played at **Home** (`home`) or **Away** (`away`). |

---

### 🗺️ GPS Navigation Attributes

The `next_match_location` and `next_match_opponent` entities expose attributes designed for travel planning:

* `gym_name`: venue or sports facility name
* `gym_address`: street address
* `gym_city`: municipality
* `google_maps_url`: direct Google Maps navigation URL
* `waze_url`: direct Waze navigation launch link

---

### 🚀 Configuration

1. Go to **Settings** > **Devices & services**.
2. Click **Add integration** and search for **FFBB Tracker**.
3. Fill in the search form:
   * **Club search**: enter a few characters of the club or city name (e.g., `Bordeaux` or `Basket Landes`). Select your club, then pick the team from the retrieved list.
   * **Direct search**: paste the team page URL copied from `competitions.ffbb.com` (e.g., `https://competitions.ffbb.com/equipes/123456789`) or provide the raw numeric engagement ID.
4. Pool and championship details are detected and configured automatically.

---

### 🛠️ Actions and Automations (Examples)

#### Example: Game day morning reminder using the binary sensor
```yaml
alias: "Basketball - Game Day Reminder"
trigger:
  - trigger: time
    at: "09:00:00"
condition:
  - condition: state
    entity_id: binary_sensor.my_team_game_day
    state: "on"
action:
  - action: notify.send_message
    target:
      entity_id: notify.telegram_home
    data:
      message: >-
        🏀 Game day today!
        Opponent: {{ states('sensor.my_team_next_match_opponent') }}
        Venue: {{ 'Home' if is_state('sensor.my_team_next_match_venue_type', 'home') else 'Away' }}
        Tip-off: {{ as_timestamp(states('sensor.my_team_next_match_date')) | timestamp_custom('%H:%M') }}
        Location: {{ states('sensor.my_team_next_match_location') or 'Not specified' }}

        Directions: {{ state_attr('sensor.my_team_next_match_location', 'waze_url') }}
```

#### Example: Query next 3 matches in a script
```yaml
action: ffbb_tracker.get_next_matches
data:
  limit: 3
response_variable: basketball_schedule
```

#### Ready-made blueprints (no templating needed)

Prefer clicking over writing YAML? Three automation blueprints ship in [`blueprints/automation/ffbb_tracker`](blueprints/automation/ffbb_tracker), built on the `event` entities above so they fire once per result/rank change and never replay on restart:

* **Full Telegram notification pack**: Matchday-eve reminder plus interactive buttons (Refresh, Maps, Waze) for one team.
  [![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAdrien40%2Fha-ffbb-tracker%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fffbb_tracker%2Fmatch_notifications_telegram.yaml)
* **Match result notification**: Telegram message with opponent, score, and venue as soon as a new result comes in.
  [![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAdrien40%2Fha-ffbb-tracker%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fffbb_tracker%2Fmatch_result_notification_telegram.yaml)
* **Rank change notification**: Telegram message with the old and new pool position as soon as it moves.
  [![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAdrien40%2Fha-ffbb-tracker%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fffbb_tracker%2Frank_changed_notification_telegram.yaml)

---

### ⚠️ Known Limitations

* **Engagement ID changes between seasons**: The FFBB re-assigns a new internal engagement ID to each team every season. If a tracked team's entities stop updating and stay unavailable for several days while the season is clearly still active, the most likely cause is that the team's engagement ID has changed. When this persists for 3 days, the integration now raises a repair notification automatically (**Settings** > **System** > **Repairs**) pointing you to the fix. Either way, use **Settings** > **Devices & services** > **FFBB Tracker** > **Reconfigure** to search for the team again and pick it up under its new ID — this keeps your existing automations and dashboard cards working, since the device and entity IDs are not affected by this operation.
* **No official API**: This integration relies on the public Directus endpoints used by the official web app rather than a documented, stable API. Breaking changes on the FFBB's side (schema changes, stricter bot filtering) can affect the integration without notice; see the [Troubleshooting](#-troubleshooting) section and open an [issue](https://github.com/Adrien40/ha-ffbb-tracker/issues) if something stops working.

---

### 🗑️ Uninstalling

1. Go to **Settings** > **Devices & services**, open the **FFBB Tracker** integration, and remove each configured team (three-dot menu > **Delete**). This also removes the associated device and all its entities from the entity registry.
2. If installed via HACS: open **HACS** > **FFBB Tracker**, and select **Remove**.
3. If installed manually: delete the `custom_components/ffbb_tracker` folder from your Home Assistant configuration directory.
4. Restart Home Assistant.

No credentials, tokens, or external accounts are created by this integration, so there is nothing to revoke elsewhere.

---

### 🐛 Troubleshooting

<details>
<summary>⚠️ Frequently Asked Questions</summary>

* **No teams found when searching for a club**: Some sports associations have not yet registered their rosters for the next phase, or the pool schedule has not been officially released by the committee or league.
* **The GPS link directs to the town center instead of the gym**: In smaller facilities, the municipality may not have provided a precise street address to the federation. In this case, the link combines the gym name and town name to optimize routing.
* **The rank evolution sensor shows 0 after a restart**: This is expected during initial setup; as soon as the next standings update occurs, the actual shift (+1, -1, etc.) will be computed and preserved.

</details>

---

### 🌐 Supported Languages

The integration is fully available in **French** <img src="https://hatscripts.github.io/circle-flags/flags/fr.svg" width="16" valign="middle"> and **English** <img src="https://hatscripts.github.io/circle-flags/flags/gb.svg" width="16" valign="middle"> (configuration flow, entities, and calendar).

If you would like to see the integration translated into another language or contribute to a translation, feel free to open an [issue](https://github.com/Adrien40/ha-ffbb-tracker/issues) or contact me directly on GitHub.

---

### 🤝 Contributions and Support
For bug reports or feature requests, please open an [Issue](https://github.com/Adrien40/ha-ffbb-tracker/issues) on this repository.

### ⚖️ License and Disclaimer
This project is licensed under the **GPLv3**. It is an independent, open-source project and is not officially affiliated with the French Basketball Federation (FFBB). Use of this software is at your own discretion.

---

**Developed with ❤️ by @Adrien40**

<a href="https://www.buymeacoffee.com/adrien40"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" width="180"></a>

<!-- Keywords: Home Assistant custom integration, FFBB, Basketball, scores, standings, calendar, sports tracker, local automation -->
