# Daily briefing — configure, run, and schedule `agentos brief`

The daily briefing assembles a single morning digest from your local AgentOS
state: weather, today's plan, meetings, deadlines, pipeline/cron health, open
tasks, recent agent runs, the inbox, and a couple of personal touches. It is
**deterministic and offline** — every section is computed from local files or a
no-auth public API, and each section degrades independently so one failing
source never breaks the whole brief.

By the end of this page you will:

1. **Configure** your identity and weather location in `config/user.yaml`.
2. **Run** `agentos brief` and read today's digest.
3. **Schedule** it to run every morning via a launchd agent.

> Prereq: you've done the one-time setup (`agentos init`). See the
> [README](../README.md) quickstart. The brief itself spends nothing — it makes
> no model calls.

---

## Step 1 — Configure `config/user.yaml`

The brief reads your identity and weather location from `config/user.yaml`
(gitignored; the tracked template is
[`config/user.yaml.example`](../config/user.yaml.example)). If you ran
`agentos init` this already exists; otherwise copy the template:

```bash
cp config/user.yaml.example config/user.yaml
```

The fields the brief uses:

| Field            | Used for                                                         |
| ---------------- | ---------------------------------------------------------------- |
| `name`           | greeting / generated artifacts                                   |
| `email`          | the weather API `User-Agent` (NWS asks for a contact)            |
| `timezone`       | calendar/plan section date handling                              |
| `cos_dir`        | today's time-blocks from the chief-of-staff weekly plan          |
| `personal_dir`   | the deadline radar's markdown scan                               |
| `weather_lat`    | latitude for the weather forecast                                |
| `weather_lon`    | longitude for the weather forecast                               |
| `weather_label`  | the human-readable name shown in the brief's Weather header      |

### Set your weather location

Weather comes from the **US National Weather Service** (`api.weather.gov`, no
auth), so use **US coordinates**. Look up your city's latitude/longitude at
<https://www.latlong.net> and set:

```yaml
weather_lat: 37.7749
weather_lon: -122.4194
weather_label: "San Francisco, CA"
```

There is **no hardcoded city** in the code — if you omit these fields the brief
falls back to the San Francisco placeholder in `_USER_DEFAULTS`
(`orchestrator/agentos/core/config.py`), and the header always reflects whatever
`weather_label` resolves to. (Outside the US, the NWS lookup simply fails and the
Weather section degrades to "weather unavailable" — every other section still
renders.)

---

## Step 2 — Run it

```bash
cd orchestrator
.venv/bin/agentos brief
```

This writes the digest to `~/agentos/briefings/<date>.md`, prints it to the
terminal, and fires a macOS notification. Useful flags:

```bash
agentos brief --quiet      # write the file, skip printing the body
agentos brief --no-notify  # skip the macOS notification (good for cron/CI)
```

The Weather header will read `## 🌤 Weather — <your weather_label>`, confirming
the location resolved from your config rather than any built-in city.

---

## Step 3 — Schedule it every morning (launchd)

A launchd agent runs `agentos brief` on a calendar schedule. The template is
[`config/launchd/com.agentos.dailybrief.plist.example`](../config/launchd/com.agentos.dailybrief.plist.example)
(defaults to 8:00 AM local time).

launchd does **not** expand `~`, so copy the template and replace `~/agentos`
with your absolute AgentOS path, then install:

```bash
# from the agentos root
cp config/launchd/com.agentos.dailybrief.plist.example \
   config/launchd/com.agentos.dailybrief.plist

# edit the copy: replace every ~/agentos with your absolute path, e.g.
#   /Users/you/agentos/orchestrator/.venv/bin/agentos

cp config/launchd/com.agentos.dailybrief.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.agentos.dailybrief.plist
```

To change the time, edit the `StartCalendarInterval` `Hour`/`Minute` keys. To
remove the schedule:

```bash
launchctl unload -w ~/Library/LaunchAgents/com.agentos.dailybrief.plist
```

Output and errors land in `~/agentos/logs/dailybrief.log` and
`dailybrief.err` (paths set in the plist). Two sibling templates live next to
this one — `com.agentos.dayend.plist.example` and
`com.agentos.dashboard.plist.example` — and install the same way.

---

## How a fresh user gets a working brief

- `agentos brief` runs with **zero configuration** — defaults give a valid weather
  location and every other section reads local state (empty is fine: "No open
  tasks", "Inbox clear", etc.).
- The first thing to personalize is the **weather location** (Step 1). Everything
  else fills in as you onboard projects, register pipelines, and run the
  chief-of-staff weekly planner.
- Nothing here makes a model call or touches the network beyond the no-auth NWS
  forecast, so it is safe to schedule daily without cost.
