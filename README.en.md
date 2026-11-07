# Garmin MCP

[中文](README.md) | [English](README.en.md)

Bring your **Garmin Connect** training and health data into your AI assistant. Ask
"how much did I run this month", "analyse yesterday's run", "am I overtraining", or
"how did I sleep last night" — and get answers straight from your own account.

> Read-only. It never modifies or deletes your records, and **never touches your password**.
> Works with both `connect.garmin.com` (global) and `connect.garmin.cn` (China).

---

## What it can do

| You ask | Tool it calls |
|---|---|
| How far did I run yesterday? | `list_activities` |
| Analyse yesterday's run, was my pace steady? | `get_activity` |
| Chart my splits and heart rate | `render_chart` |
| How much this month / this year? | `get_period_stats` |
| Am I overtraining? | `get_training_status` |
| How did I sleep, what's my resting HR? | `get_health` |
| What are my 5K / half-marathon PBs? | `get_personal_records` |
| VO2max, HR zones, my devices | `get_profile` |

Charts are rendered server-side as PNG — no browser automation needed on your side.

## Supported clients

| Client | Config file |
|---|---|
| **Claude Code** | `~/.claude.json` or project `.mcp.json` (also `claude mcp add`) |
| **Codex CLI** | `~/.codex/config.toml` → `[mcp_servers.garmin]` |
| **ZCode** | `~/.zcode/cli/config.json` (`mcp.servers`) |
| **WorkBuddy** | `~/.workbuddy/mcp.json` |
| **DeepSeek Harness** | profile `cordis.patch.yml` |
| Anything else | `~/.agents/mcp.json` |

Per-client instructions live in [`docs/`](docs/).

## Quick start

```bash
git clone https://github.com/hongshunanhai/garmin-mcp.git
cd garmin-mcp
./install.sh              # macOS / Linux; add --site=global for the global site
```

Or manually:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python garmin_mcp.py login
```

Then point your client at:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "/abs/path/garmin-mcp/.venv/bin/python",
      "args": ["/abs/path/garmin-mcp/garmin_mcp.py"]
    }
  }
}
```

## How login works

Garmin is trickier than most apps:

1. The session cookies (`session`, `JWT_WEB`, `SESSIONID`) are **HttpOnly** — `document.cookie` cannot see them.
2. Every request also needs `Connect-Csrf-Token` (from `<meta name="csrf-token">`) and `Sec-Fetch-Site: same-origin`, otherwise you get 403.
3. Sessions last only ~2 hours, but they can be refreshed silently.

So the login flow is:

```
garmin_mcp.py login
  → launches a dedicated Chrome window (its own profile, your daily browser is untouched)
  → opens connect.garmin.com / .cn → redirects to Garmin SSO
  → you sign in yourself (email/phone + password + MFA)
  → the tool reads the HttpOnly cookies over the Chrome DevTools Protocol
  → validates, then stores them in ~/.garmin-mcp/session.json (mode 600)
```

After that:

- **Auto-refresh**: before each call the tool checks the expiry and calls
  `POST /modern/di-oauth/refreshToken` when needed (Garmin rotates the refresh token —
  it is single-use, so the tool persists the new cookies immediately).
- **Self-healing**: if the session really expired, the next tool call **pops up the login
  window** again; usually the SSO cookie is still there and one click is enough.
- **Switch account**: `garmin_mcp.py logout` then `login`.

Your password is only ever typed into Garmin's own page. The tool stores nothing but the
session cookies, locally.

### China vs global

Both sites ship the **same front-end code**, so API paths are identical — only the domain
and SSO host differ:

| | China | Global |
|---|---|---|
| Site | `connect.garmin.cn` | `connect.garmin.com` |
| SSO | `sso.garmin.cn` | `sso.garmin.com` |
| Command | `login` | `login --site=global` |

## Tools (24; read-only by default, writes are opt-in)

**Core** — `list_activities` · `get_activity` · `get_period_stats` · `get_calendar` ·
`get_training_status` · `get_health` · `get_personal_records` · `get_profile` · `search_events`

**Training & gear** — `get_training` (plans + workouts) · `get_gear` · `get_devices`

**Social & awards** — `get_achievements` · `get_challenges` · `get_social`

**Other** — `render_chart` · `download_activity` (fit/gpx/tcx/kml/csv) · `login`

**Workout & event authoring (opt-in: set `GARMIN_ALLOW_WRITE=1`)** —
`create_workout` · `schedule_workout` · `push_workout_to_device` · `delete_workout` ·
`create_event` · `delete_event`.
Every write tool is dry-run by default and requires `confirm=true` to actually submit.

No write endpoints are exposed (no upload, delete, rename, comment).

## License (important)

This project is licensed under the **PolyForm Noncommercial License 1.0.0**:

- ✅ Free for personal use, study, hobby projects, and sharing with friends
- ✅ Free for schools, charities, public research, and government
- ❌ **No commercial use** — you may not sell it, host it as a paid service, or use it in a commercial product

> In one line: **use it freely, don't make money from it.** Contact me for a commercial license.

This is **source-available**, not OSI open source. If you fork or redistribute,
keep the `Required Notice` line in `LICENSE`.

## Privacy

Requests go straight from your machine to Garmin's servers. Nothing is proxied by a third
party. However, whatever a tool returns becomes part of your AI client's conversation — with
a cloud model that data is processed under that provider's terms, exactly as if you had
pasted it into the chat.

Local state: `~/.garmin-mcp/session.json` (mode 600) and the Chrome profile used for login.
Delete either to log out.

## License

[PolyForm Noncommercial License 1.0.0](LICENSE) — noncommercial use only.
Not affiliated with Garmin.
