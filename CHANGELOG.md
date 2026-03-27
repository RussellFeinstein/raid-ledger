# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] — 2026-03-27

### Changed

- **Replaced Raider.io with wowaudit API as the data source.** Single batch API call fetches all characters' weekly M+ data instead of per-player requests. Requires `WOWAUDIT_API_KEY` env var.
- Vault slots now come from real wowaudit vault data instead of derived estimates
- Collector uses name+realm matching (case-insensitive) to pair wowaudit data with roster players
- Removed Score column from Weekly Overview (raiderio_score no longer populated)
- Removed M+ Score Trend chart from Player Timeline

### Added

- `raid_ledger/api/wowaudit.py` — new wowaudit HTTP client (WowauditClient, WowauditCharacter, WowauditRosterMember)
- `docs/wowaudit-api.md` — reverse-engineered API reference for wowaudit endpoints
- `WowauditConfig` in config with `base_url` and `api_key` fields

### Removed

- `raid_ledger/api/raiderio.py` — Raider.io client (fully replaced)
- `RaiderioConfig` from config
- Item level and Raider.io score tracking (columns remain nullable in schema for historical data)

## [0.6.1] — 2026-03-23

### Fixed

- Fix editable install failure: add explicit package discovery for `raid_ledger` and `dashboard`, excluding `config/`
- Fix deprecated `project.license` table format in pyproject.toml (use SPDX string)
- Fix `st.Page` paths in dashboard app (resolve relative to script directory, not CWD)

## [0.6.0] — 2026-03-23

**Milestone M5: Dashboard Views**

### Added

- Streamlit dashboard entrypoint (`dashboard/app.py`) with sidebar: officer name, week selector, status/role filters, pass rate metric
- Password gate (`dashboard/auth.py`) via `st.secrets` (no secret = open access for local dev)
- Cached data loader (`dashboard/data_loader.py`) wrapping analyzer and repository queries
- Weekly Overview page: color-coded roster table (flags/fails/passes), CSV export, first-run onboarding card
- Player Timeline page: 12-week heatmap strip, M+ score trend, per-week detail cards with reasons and officer notes
- Status badge component: icons + text + color for WCAG 2.1 AA (never color alone)
- Shared filter component for sidebar status/role filtering
- 22 new tests: smoke imports, auth gate (4 scenarios), data loader (11 scenarios with empty DB coverage)

## [0.5.1] — 2026-03-23

### Fixed

- Replaced 6 hardcoded status strings in analyzer SQL with `SnapshotStatus`/`PlayerStatus` enum values

### Added

- FLAG snapshot test coverage: ordering, failure rate exclusion, streak-breaking, breakdown reasons, chronic exclusion
- Multi-reason failure breakdown test
- "Flagged" player in seeded test dataset (P P ? P F ? P P pattern)

## [0.5.0] — 2026-03-23

**Milestone M4: Analyzer**

### Added

- Failure analyzer (`raid_ledger/engine/analyzer.py`) with 7 pattern detection queries:
  - `get_weekly_summary` — all players' pass/fail/flag for a week, joined with player info, sorted flags-first
  - `get_player_history` — per-player timeline with parsed reasons
  - `get_failure_rate` — "failed X of last N" with flag exclusion and short-history respect
  - `get_chronic_underperformers` — roster-wide chronic failure detection (configurable threshold/lookback)
  - `get_current_streaks` — consecutive pass/fail streak per active player
  - `get_failure_breakdown` — count by failure reason for a week
  - `get_trial_flags` — trial players with any failures in their first N weeks
- Result dataclasses: `PlayerWeekSummary`, `FailureRate`, `PlayerStreak`
- 29 new tests with seeded 6-player x 8-week dataset covering all queries and edge cases

## [0.4.1] — 2026-03-23

### Fixed

- 429 rate-limit exhaustion now raises `RateLimitError` instead of misleading `TimeoutException`
- README project structure referenced nonexistent `cli.py`

### Added

- `RateLimitError` exception for exhausted rate-limit retries
- Warning log on unknown role values during guild roster import
- Test for 429 exhaustion in API client
- Test for 404 (CharacterNotFoundError) flagging in collector flow

## [0.4.0] — 2026-03-23

**Milestone M3: Rules Engine + Collector**

### Added

- Rules engine (`raid_ledger/engine/rules.py`): OR-logic pass/fail/flag evaluation against weekly benchmarks
- `evaluate()` function: checks M+ runs at level and ilvl (when set), returns `EvaluationResult(status, reasons)`
- `derive_vault_slots()`: 1/4/8 runs = 1/2/3 Great Vault slots
- Weekly collector (`raid_ledger/engine/collector.py`): orchestrates fetch-evaluate-upsert for all active players
- Benchmark copy-forward: automatically copies the most recent benchmark when none exists for the current week
- `NoBenchmarkError`: clear failure when no benchmark has ever been set
- `scripts/collect_weekly.py`: standalone entry point for GitHub Actions cron and manual collection
- 36 new tests: exhaustive rules scenarios (pass/fail/flag/edge cases), collector integration with mocked API, vault derivation, script smoke test

## [0.3.0] — 2026-03-23

**Milestone M2: Raider.io Client**

### Added

- Async Raider.io API client (`raid_ledger/api/raiderio.py`) with two endpoints:
  - `fetch_character()` — weekly M+ runs, ilvl, and score via `mythic_plus_previous_weekly_highest_level_runs`
  - `fetch_guild_members()` — full guild roster for import (name, realm, class, spec, role, rank)
- `CharacterData` and `GuildMember` dataclasses for typed API responses
- `CharacterNotFoundError`, `GuildNotFoundError`, `ParseError` exceptions
- Exponential backoff on 429 rate limits, configurable retry on timeouts
- Role normalization (HEALING -> healer, TANK -> tank, DPS -> dps)
- `CharacterData.count_runs_at_level(min_key)` helper for threshold evaluation
- JSON test fixtures: character (full, empty runs, partial fields), guild (14 members across 6 ranks)
- 15 new tests via respx mocks: success parsing, 404/429/timeout handling, malformed JSON, role mapping

## [0.2.0] — 2026-03-23

**Milestone M1: Models + DB**

### Added

- Pydantic domain models: `Player`, `WeeklySnapshot`, `WeeklyBenchmark` (all frozen)
- Enums: `PlayerStatus`, `SnapshotStatus`, `FailureReason`, `FlagReason`
- SQLAlchemy 2.0 ORM schema with 6 tables: `players`, `weekly_benchmarks`, `weekly_snapshots`, `officer_notes`, `collection_runs`, `settings`
- All indexes per design: composite unique constraints, status indexes, week lookups
- Repository classes: `PlayerRepo`, `SnapshotRepo`, `BenchmarkRepo`, `NoteRepo`, `CollectionRunRepo`, `SettingsRepo`
- Snapshot upsert on `UNIQUE(player_id, week_of)` for safe re-runs
- `AppConfig` with Pydantic Settings loading from `config/default.toml` + env var overrides
- SQLite (dev) and PostgreSQL (prod) dual support via `DATABASE_URL`
- 59 tests covering models, config, and all repository CRUD operations
- README, CHANGELOG, CLAUDE.md

## [0.1.0] — 2026-03-23

### Added

- Project scaffold: directory structure, pyproject.toml, config/default.toml
- Dependencies: pydantic, sqlalchemy, httpx, typer, python-dotenv
- Dev dependencies: pytest, pytest-cov, ruff, respx
- .gitignore, LICENSE (MIT), .env.example
- GitHub Actions CI pipeline (pytest + ruff)
