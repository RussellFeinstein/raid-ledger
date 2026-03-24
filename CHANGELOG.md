# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
