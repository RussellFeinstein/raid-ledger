# Raid Ledger

Weekly M+ accountability system for WoW CE progression guilds.
Track every raider every week. Flag who's behind, why, and whether it's a pattern.

## What It Does

- Automatically pulls M+ data from Raider.io after weekly reset
- Evaluates each raider against configurable weekly requirements
- Three-state verdicts: Pass / Fail / Flag (needs officer attention)
- Tracks failure patterns over time (chronic underperformers, streaks)
- Officer dashboard with full roster management, notes, and manual overrides

## Tech Stack

| Layer | Choice |
|-------|--------|
| Language | Python 3.11+ |
| API | Raider.io (free, no auth required) |
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
│   └── raiderio.py      # Raider.io HTTP client (character + guild endpoints)
├── engine/
│   ├── rules.py          # 3-state evaluation (pass/fail/flag), OR logic
│   └── collector.py      # Weekly collection orchestrator
└── cli.py                # Typer CLI (M7)
```

## Database

Six tables: `players`, `weekly_benchmarks`, `weekly_snapshots`, `officer_notes`, `collection_runs`, `settings`.

- SQLite for local development (zero config)
- PostgreSQL (Supabase) for production via `DATABASE_URL` env var
- All foreign keys use `ON DELETE RESTRICT` — players are deactivated, never deleted

## Raider.io API

The client fetches data from two Raider.io endpoints (free, no auth required, 200 req/min):

**Character profile** — weekly M+ data for collection:
- `mythic_plus_previous_weekly_highest_level_runs` — finalized runs from the completed week
- `gear.item_level_equipped` — ilvl snapshot
- `mythic_plus_scores_by_season` — M+ score

**Guild profile** — roster import:
- Returns all guild members with name, realm, class, spec, role, and rank
- Officers select active raiders from the full member list

Collection runs Tuesday evening (3 hours after US reset) to ensure data is finalized. The client uses exponential backoff on 429 rate limits and retries on timeouts.

## Collection & Rules Engine

### How Collection Works

1. Loads the active roster (core + trial players)
2. Loads the benchmark for this week (copies the most recent one if none is set)
3. For each player: fetches data from Raider.io, evaluates against the benchmark, upserts a snapshot
4. Logs collection run metadata (status, counts, errors)

Collection is safe to re-run — `UNIQUE(player_id, week_of)` upserts mean the latest data wins.

### How the Rules Engine Evaluates

OR-logic: failing ANY active check = failed week. All thresholds come from the weekly benchmark — nothing is hardcoded.

- **Pass**: Met all requirements
- **Fail**: `INSUFFICIENT_KEYS` (runs at level < minimum) and/or `LOW_ILVL` (ilvl < minimum, only checked when set)
- **Flag**: `NO_DATA` (API returned nothing — needs officer review)

Vault slots are derived from M+ count: 1/4/8 runs = 1/2/3 slots.

### Running Collection Manually

```bash
python scripts/collect_weekly.py                    # current week
python scripts/collect_weekly.py --week 2026-03-17  # specific week
```

## Configuration

`config/default.toml` provides seed values. After first run, the database `settings` table is the source of truth. All settings are editable through the dashboard Settings page.

## License

MIT
