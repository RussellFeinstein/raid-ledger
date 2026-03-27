# Raid Ledger

Weekly M+ accountability system for WoW CE progression guilds.
Track every raider every week. Flag who's behind, why, and whether it's a pattern.

## What It Does

- Automatically pulls M+ data from wowaudit after weekly reset (single batch API call)
- Evaluates each raider against configurable weekly requirements
- Three-state verdicts: Pass / Fail / Flag (needs officer attention)
- Tracks failure patterns over time (chronic underperformers, streaks)
- Officer dashboard with full roster management, notes, and manual overrides

## Tech Stack

| Layer | Choice |
|-------|--------|
| Language | Python 3.11+ |
| API | wowaudit (private team API, Bearer token auth) |
| ORM | SQLAlchemy 2.0 |
| DB (dev) | SQLite |
| DB (prod) | Supabase PostgreSQL |
| Dashboard | Streamlit Community Cloud |
| Config | Pydantic + TOML |
| HTTP | httpx |
| CI | GitHub Actions (pytest + ruff) |

## Local Development

```bash
python -m venv .venv
.venv/Scripts/activate    # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -e ".[dev,dashboard]"
```

### Environment Variables

```bash
# Required for data collection:
export WOWAUDIT_API_KEY="your-api-key-here"

# Optional (defaults to SQLite):
export DATABASE_URL="postgresql://user:pass@host/db"
```

Generate your wowaudit API key at your team's settings page:
`https://wowaudit.com/{region}/{realm}/{guild}/{team}/api`

### Run Tests

```bash
pytest --tb=short -q
```

### Lint

```bash
ruff check raid_ledger/ tests/
```

## Project Structure

```
raid_ledger/
├── __init__.py          # Package version
├── config.py            # Pydantic Settings (TOML + env vars)
├── models/
│   ├── player.py        # Player model, PlayerStatus enum
│   ├── snapshot.py       # WeeklySnapshot, SnapshotStatus, FailureReason, FlagReason
│   └── benchmark.py      # WeeklyBenchmark model
├── db/
│   ├── connection.py     # SQLAlchemy engine + session factory
│   ├── schema.py         # ORM tables (6 tables + indexes)
│   └── repositories.py   # CRUD repos returning Pydantic models
├── api/
│   └── wowaudit.py      # Wowaudit HTTP client (batch roster + M+ data)
├── engine/
│   ├── rules.py          # 3-state evaluation (pass/fail/flag), OR logic
│   ├── collector.py      # Weekly collection orchestrator (single batch fetch)
│   └── analyzer.py       # Pattern detection (chronic failures, streaks)
dashboard/
├── app.py                # Streamlit entrypoint, sidebar, page navigation
├── auth.py               # Password gate via st.secrets
├── data_loader.py        # Cached query wrappers for dashboard pages
├── pages/
│   ├── weekly_overview.py # Color-coded roster table, CSV export, onboarding
│   └── player_timeline.py # Per-player heatmap, detail cards
└── components/
    ├── status_badge.py    # Icons + text + color (WCAG: never color alone)
    └── filters.py         # Shared sidebar filter logic
scripts/
└── collect_weekly.py     # Entry point for cron + manual collection
docs/
└── wowaudit-api.md      # Wowaudit API reference (reverse-engineered)
```

## Database

Six tables: `players`, `weekly_benchmarks`, `weekly_snapshots`, `officer_notes`, `collection_runs`, `settings`.

- SQLite for local development (zero config)
- PostgreSQL (Supabase) for production via `DATABASE_URL` env var
- All foreign keys use `ON DELETE RESTRICT` — players are deactivated, never deleted

## Wowaudit API

The client fetches data from the wowaudit team API (Bearer token auth). See `docs/wowaudit-api.md` for the full reference.

**Batch historical data** (`/v1/historical_data`) — single call returns all characters' weekly data:
- `dungeons_done` — keystones completed with key level (used for evaluation)
- `vault_options` — real Great Vault slot data (raids, dungeons, world)
- `regular_mythic_dungeons_done` — mythic-0 count (tracked, not evaluated)

**Roster** (`/v1/characters`) — team roster with class, role, rank, tracking status.

**Period metadata** (`/v1/period`) — current period number and season info.

## Collection & Rules Engine

### How Collection Works

1. Loads the active roster (core + trial players)
2. Loads the benchmark for this week (copies the most recent one if none is set)
3. Batch-fetches all characters from wowaudit (single API call)
4. Matches wowaudit characters to roster by name+realm (case-insensitive)
5. Evaluates each player against the benchmark, upserts a snapshot
6. Logs collection run metadata (status, counts, errors)

Collection is safe to re-run — `UNIQUE(player_id, week_of)` upserts mean the latest data wins.

### How the Rules Engine Evaluates

OR-logic: failing ANY active check = failed week. All thresholds come from the weekly benchmark — nothing is hardcoded.

- **Pass**: Met all requirements
- **Fail**: `INSUFFICIENT_KEYS` (runs at level < minimum) and/or `LOW_ILVL` (ilvl < minimum, only checked when set)
- **Flag**: `NO_DATA` (API returned nothing — needs officer review)

Vault slots come from wowaudit's real vault data (non-null dungeon options = slots earned).

### Running Collection Manually

```bash
python scripts/collect_weekly.py                    # current week
python scripts/collect_weekly.py --week 2026-03-17  # specific week
```

## Analysis

The analyzer detects failure patterns across weeks. All thresholds are configurable parameters.

| Query | Description |
|-------|-------------|
| `get_weekly_summary(week_of)` | All players' pass/fail/flag for a week, sorted flags-first |
| `get_player_history(player_id, weeks)` | Per-player timeline, most recent first |
| `get_failure_rate(player_id, lookback)` | "Failed X of last N" — flags excluded, short history respected |
| `get_chronic_underperformers(threshold, lookback)` | Active players who failed >= threshold of last N weeks |
| `get_current_streaks()` | Consecutive pass/fail streak per active player |
| `get_failure_breakdown(week_of)` | Count by failure reason for a week |
| `get_trial_flags(lookback)` | Trial players with any failures in their first N weeks |

Default thresholds (configurable via Settings): chronic = 3 failures in 5 weeks, streak warning at 2.

## Dashboard

Run locally with `streamlit run dashboard/app.py`. Password gate via `st.secrets` (no secret = open access for local dev).

- **Weekly Overview**: Color-coded roster table (flags first, then fails, then passes). CSV export. First-run onboarding card when no data exists.
- **Player Timeline**: Per-player 12-week heatmap strip, per-week detail cards with reasons and officer notes.

All status indicators use icons + text + color (never color alone) for WCAG 2.1 AA compliance.

## Configuration

`config/default.toml` provides seed values. After first run, the database `settings` table is the source of truth. All settings are editable through the dashboard Settings page.

## License

MIT
