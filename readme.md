# TournamentBot (D2 Hustlers) — README

Discord tournament/event bot backed by MySQL, with:
- Slash commands for running events (registrations → teams → bracket → reporting → leaderboards)
- Deterministic bracket generation (single + double elimination)
- Text bracket view for quick checking in-chat
- PNG bracket rendering for “real bracket” visuals
- Smoke-test scripts to validate DB + repo flows before going live

---

## 1) Quick start

### Prereqs
- Python 3.11+ (you’re on 3.13; that’s fine)
- MySQL 8+ (or compatible)
- Discord bot token + application created in Discord Developer Portal

### Install
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
```

### Configure `.env`
Create a `.env` in the project root (same folder as `main.py`). Example:
```env
DISCORD_TOKEN=YOUR_TOKEN_HERE
DEV_GUILD_ID=123456789012345678
ANNOUNCE_CHANNEL_ID=123456789012345678

DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=
DB_NAME=d2_discord_bot

DB_POOL_MIN=1
DB_POOL_MAX=5
DB_CONNECT_TIMEOUT=10

COMMAND_PREFIX=!
LOG_LEVEL=INFO
```

> Note: `config.py` reads environment variables. If you want `.env` auto-loaded when running tools/smoke scripts, make sure you load it at runtime (see Troubleshooting section below).

### Run the bot
```bash
python main.py
```

---

## 2) Core workflow (operator runbook)

Typical event flow:

1. **Create event**
   - `/event create name:<...> format:<single_elim|double_elim> team_size:<1..4> max_players:<...>`

2. **Open registrations**
   - `/event open event_id:<id>`

3. **Players join**
   - `/event join event_id:<id>`

4. **Lock registrations**
   - `/event lock event_id:<id>`

5. **Randomize teams**
   - `/event randomize_teams event_id:<id>`

6. **Create bracket**
   - `/event create_bracket event_id:<id>`

7. **View bracket**
   - `/event bracket event_id:<id>` (text view)
   - `/event bracket_image event_id:<id>` (PNG view)

8. **Report match winners**
   - New reporting should be match-code based (example):  
     `/event report event_id:<id> match_code:W1-01 winner_seed:3`

9. **Leaderboards**
   - `/event leaderboard event_id:<id>`

---

## 3) Architecture (high-level)

This project is organized as a clean layered system:

- **Cogs (`cogs/`)**: Discord slash-command controllers. They validate inputs/permissions and call services.
- **Services (`services/`)**: Business logic and orchestration (e.g., creating brackets, recording results, updating status).
- **Repositories (`repositories/`)**: Database access only (SQL in/out), no business logic.
- **Renderers (`renderers/`)**: Output formatting only (embeds, monospace tables, PNG bracket image).
- **Domain (`domain/`)**: Shared types + deterministic bracket math to keep services and renderers consistent.
- **DB (`db/`)**: Pool lifecycle + transaction helper.
- **Tools (`tools/smoke/`)**: Runbook scripts to validate DB and repository flows quickly.
- **Utils (`utils/`)**: Text/time helpers used across layers.

---

## 4) Folder/file map (one-line TL;DR per file)

### Root
- `.env` — Local environment variables (token, DB creds, guild IDs, etc.).
- `.gitignore` — Git exclusions.
- `config.py` — Reads/validates env vars and returns typed `BotConfig`.
- `logging_setup.py` — Central logging configuration helpers.
- `main.py` — Bot entrypoint: initializes pool/repos/services/renderers, loads cogs, syncs slash commands.

### `cogs/`
- `cogs/__init__.py` — Package marker.
- `cogs/admin_cog.py` — Admin-only slash commands (server config + management actions).
- `cogs/events_cog.py` — Tournament/event commands (create/open/lock/join/drop/teams/brackets/report/leaderboards/images).
- `cogs/ladder_reset_cog.py` — Ladder reset workflow commands.

### `db/`
- `db/__init__.py` — Package marker.
- `db/pool.py` — aiomysql pool lifecycle (`start`, `ping`, `close`).
- `db/tx.py` — Async transaction context manager for atomic DB operations.

### `domain/`
- `domain/__init__.py` — Package marker.
- `domain/enums.py` — Enums/constants (bracket keys, event formats).
- `domain/models.py` — Bracket primitives/helpers (nodes, seeding, match codes, power-of-two logic).

### `migrations/`
- `001_discord_tournaments.sql` — Base schema for tournament objects.
- `002_teams_events_stats.sql` — Teams/events/matches/stats schema extensions.
- `12312025-Set.sql` — Dated patch migration.

### `renderers/`
- `renderers/__init__.py` — Package marker.
- `renderers/embeds.py` — Central embed theme + helper methods.
- `renderers/bracket_view.py` — Text bracket snapshot for Discord.
- `renderers/bracket_diagram.py` — PNG bracket renderer (PIL drawing).
- `renderers/leaderboard_view.py` — Monospace tables for player/team leaderboards.

### `repositories/`
- `repositories/__init__.py` — Package marker.
- `repositories/base_repo.py` — Shared SQL helpers + JSON serialization.
- `repositories/identity_repo.py` — Discord identity mapping DB operations.
- `repositories/team_repo.py` — Base team DB operations.
- `repositories/event_repo.py` — Event registrations, event teams, rosters, matches.
- `repositories/stats_repo.py` — Aggregation queries for leaderboards and W/L records.

### `services/`
- `services/__init__.py` — Package marker.
- `services/identity_service.py` — Higher-level identity workflows (wraps IdentityRepo).
- `services/team_service.py` — Higher-level team workflows (wraps TeamRepo).
- `services/event_service.py` — Higher-level event workflows (wraps EventRepo).
- `services/bracket_service.py` — Bracket creation + advancement logic.
- `services/stats_service.py` — Match result reporting + per-player stats + event status updates.

### `tools/smoke/`
- `tools/smoke/__init__.py` — Package marker.
- `00_imports.py` — Import sanity check.
- `01_ping_db.py` — DB connectivity check (SELECT 1).
- `02_identity_smoke.py` — Identity repo smoke test.
- `03_team_repo_smoke.py` — Team repo smoke test.
- `04_event_repo_smoke.py` — Event repo smoke test.
- `99_cleanup_smoke.py` — Cleanup smoke-test data.

### `utils/`
- `utils/__init__.py` — Package marker.
- `utils/text.py` — Text formatting helpers (truncate/pad/etc).
- `utils/time.py` — Time/date helpers.

---

## 5) Running smoke tests (recommended before “real” events)

From the project root:

```bash
python -m tools.smoke.00_imports
python -m tools.smoke.01_ping_db
python -m tools.smoke.02_identity_smoke
python -m tools.smoke.03_team_repo_smoke
python -m tools.smoke.04_event_repo_smoke
python -m tools.smoke.99_cleanup_smoke
```

Why `python -m ...`?
- It runs modules from the project root reliably
- It avoids import/path weirdness when running inside `tools/smoke`

---

## 6) Permissions model (Discord)

Most event “management” commands should be limited to trusted ranks (example roles you named):
- Iron Wolf
- Council
- Overseer
- Prime Evils
- Event Coordinator

Implementation options:
- Check Discord role IDs by name (simple, but name changes can break)
- Store role IDs in DB or `.env` and check IDs (recommended)

Keep this rule: “Players can join/drop themselves; staff can manage registrations/teams/brackets.”

---

## 7) Match reporting model (recommended)

Human-friendly reporting should key off:
- **event_id**
- **match_code** (W1-01, L2-03, GF-01)
- **winner_seed** (seed number shown in bracket output)

Example:
```text
/event report event_id:1 match_code:W1-02 winner_seed:3
```

This avoids needing to look up internal DB match IDs mid-event.

---

## 8) Bracket images

The PNG renderer (`renderers/bracket_diagram.py`) draws:
- Winners bracket (top)
- Losers bracket (bottom, if double elim)
- Grand Final (far right)

If your output is blank but no errors:
- The renderer may be drawing off-canvas or producing a large canvas with content in a corner
- Or nodes are being created but never receiving the correct seeded/team text due to missing seed mapping

---

## 9) Troubleshooting

### A) “Missing DISCORD_TOKEN environment variable” when running smoke tools
`config.py` reads the process environment; Python won’t automatically load `.env` unless you do it.

Fix options:
1) Install and use python-dotenv in scripts:
```bash
pip install python-dotenv
```
Then add at top of each tool script:
```python
from dotenv import load_dotenv
load_dotenv()
```

2) Or set env vars in your shell before running tests.

### B) “Privileged message content intent missing” warning
You are using slash commands, so this is usually safe to ignore unless you later add prefix commands that read message content.

### C) “PyNaCl is not installed” warning
Only relevant for voice features; safe to ignore for tournaments.

### D) Slash commands not updating
Use `DEV_GUILD_ID` during development for fast sync; global sync can take time.

---

## 10) Database migrations

Run your SQL migrations in order on your DB:
1) `migrations/001_discord_tournaments.sql`
2) `migrations/002_teams_events_stats.sql`
3) `migrations/12312025-Set.sql` (if applicable to your current schema)

---

## 11) Design rules (to keep the system maintainable)

- Cogs: input validation + permission checks + call a service + send renderer output
- Services: business logic and orchestration (no Discord code, minimal SQL)
- Repos: SQL only (no bracket logic, no rendering)
- Renderers: formatting only (no DB writes, no bracket state changes)
- Domain: deterministic helpers shared by service + renderer so match codes and seeding stay consistent

---

## 12) Next planned enhancements (optional)

- Admin commands to list + edit registrations before team generation
- “Fake registrations” command for scale testing (N fake players, auto-register)
- Better double-elim bracket layout logic (avoid “duplicated” LB appearance; enforce canonical LB shape)
- Pagination/interactive views for very large events
