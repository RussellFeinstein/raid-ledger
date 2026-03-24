"""Tests for the weekly collector — integration with mocked API + in-memory DB."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from raid_ledger.api.raiderio import RaiderioClient
from raid_ledger.config import AppConfig, CollectionConfig, RaiderioConfig
from raid_ledger.db.repositories import (
    BenchmarkRepo,
    CollectionRunRepo,
    PlayerRepo,
    SnapshotRepo,
)
from raid_ledger.engine.collector import NoBenchmarkError, WeeklyCollector
from raid_ledger.models.benchmark import WeeklyBenchmark
from raid_ledger.models.player import Player, PlayerStatus
from raid_ledger.models.snapshot import SnapshotStatus

FIXTURES = Path(__file__).parent / "fixtures"
WEEK = date(2026, 3, 17)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _config() -> AppConfig:
    return AppConfig(
        collection=CollectionConfig(
            request_delay_seconds=0,
            max_retries=3,
            timeout_seconds=5,
        ),
        raiderio=RaiderioConfig(base_url="https://raider.io/api/v1"),
    )


def _add_players(session, names: list[str]) -> list[Player]:
    repo = PlayerRepo(session)
    players = []
    for name in names:
        p = repo.create(Player(
            name=name, realm="tichondrius", class_name="Mage",
            role="dps", status=PlayerStatus.CORE, joined_date=date(2026, 3, 1),
        ))
        players.append(p)
    session.commit()
    return players


def _add_benchmark(session, week_of: date = WEEK, **overrides) -> WeeklyBenchmark:
    defaults = {
        "week_of": week_of,
        "min_mplus_runs": 8,
        "min_key_level": 10,
        "min_ilvl": None,
        "min_vault_slots": 3,
        "set_by": "Officer",
        "set_at": datetime(2026, 3, 17, 18, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    repo = BenchmarkRepo(session)
    b = repo.create_or_update(WeeklyBenchmark(**defaults))
    session.commit()
    return b


class TestCollectorFullFlow:
    @respx.mock
    @pytest.mark.anyio
    async def test_three_players_success(self, db_session):
        """Full flow: 3 players, mocked API -> correct snapshots."""
        _add_players(db_session, ["P1", "P2", "P3"])
        _add_benchmark(db_session)
        fixture = _load_fixture("raiderio_character.json")

        respx.get("https://raider.io/api/v1/characters/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            result = await collector.collect(WEEK)

        assert result.players_collected == 3
        assert result.api_errors == 0
        assert result.status == "completed"

        snap_repo = SnapshotRepo(db_session)
        snaps = snap_repo.get_by_week(WEEK)
        assert len(snaps) == 3
        for s in snaps:
            assert s.status is SnapshotStatus.PASS

    @respx.mock
    @pytest.mark.anyio
    async def test_upsert_rerun(self, db_session):
        """Collect twice for same week -> only 1 snapshot per player."""
        _add_players(db_session, ["P1"])
        _add_benchmark(db_session)
        fixture = _load_fixture("raiderio_character.json")

        respx.get("https://raider.io/api/v1/characters/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            await collector.collect(WEEK)
            await collector.collect(WEEK)

        snap_repo = SnapshotRepo(db_session)
        snaps = snap_repo.get_by_week(WEEK)
        assert len(snaps) == 1

    @pytest.mark.anyio
    async def test_no_benchmark_ever_set(self, db_session):
        """Collection aborts with NoBenchmarkError."""
        _add_players(db_session, ["P1"])

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            with pytest.raises(NoBenchmarkError):
                await collector.collect(WEEK)

        # Verify collection run logged as failed
        run_repo = CollectionRunRepo(db_session)
        runs = run_repo.get_by_week(WEEK)
        assert len(runs) == 1
        assert runs[0]["status"] == "failed"

    @respx.mock
    @pytest.mark.anyio
    async def test_benchmark_copy_forward(self, db_session):
        """No benchmark for this week -> copies most recent."""
        _add_players(db_session, ["P1"])
        _add_benchmark(db_session, week_of=date(2026, 3, 10))  # previous week
        fixture = _load_fixture("raiderio_character.json")

        respx.get("https://raider.io/api/v1/characters/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            result = await collector.collect(WEEK)

        assert result.players_collected == 1

        # Benchmark was copied
        bench_repo = BenchmarkRepo(db_session)
        copied = bench_repo.get_by_week(WEEK)
        assert copied is not None
        assert copied.min_mplus_runs == 8


class TestCollectorErrors:
    @respx.mock
    @pytest.mark.anyio
    async def test_partial_failure(self, db_session):
        """1 of 3 API calls fails -> 2 collected + 1 flagged, status=partial."""
        _add_players(db_session, ["P1", "P2", "P3"])
        _add_benchmark(db_session)
        fixture = _load_fixture("raiderio_character.json")

        route = respx.get("https://raider.io/api/v1/characters/profile")
        route.side_effect = [
            httpx.Response(200, json=fixture),
            httpx.ReadTimeout("timed out"),
            httpx.ReadTimeout("timed out"),
            httpx.ReadTimeout("timed out"),
            httpx.Response(200, json=fixture),
        ]

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            result = await collector.collect(WEEK)

        assert result.players_collected == 2
        assert result.api_errors == 1
        assert result.status == "partial"

        snap_repo = SnapshotRepo(db_session)
        snaps = snap_repo.get_by_week(WEEK)
        assert len(snaps) == 3  # 2 pass + 1 flagged
        statuses = {s.status for s in snaps}
        assert SnapshotStatus.FLAG in statuses

    @respx.mock
    @pytest.mark.anyio
    async def test_all_api_calls_fail(self, db_session):
        """All API calls fail -> status=failed, all players flagged."""
        _add_players(db_session, ["P1", "P2"])
        _add_benchmark(db_session)

        respx.get("https://raider.io/api/v1/characters/profile").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            result = await collector.collect(WEEK)

        assert result.players_collected == 0
        assert result.api_errors == 2
        assert result.status == "failed"

        run_repo = CollectionRunRepo(db_session)
        runs = run_repo.get_by_week(WEEK)
        assert runs[0]["status"] == "failed"

    @respx.mock
    @pytest.mark.anyio
    async def test_429_retry_succeeds(self, db_session):
        """Rate limit on player 2, retries, all 3 succeed."""
        _add_players(db_session, ["P1", "P2", "P3"])
        _add_benchmark(db_session)
        fixture = _load_fixture("raiderio_character.json")

        route = respx.get("https://raider.io/api/v1/characters/profile")
        route.side_effect = [
            httpx.Response(200, json=fixture),
            httpx.Response(429, text="Rate limited"),
            httpx.Response(200, json=fixture),
            httpx.Response(200, json=fixture),
        ]

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            result = await collector.collect(WEEK)

        assert result.players_collected == 3
        assert result.api_errors == 0


class TestCollectorEdgeCases:
    @respx.mock
    @pytest.mark.anyio
    async def test_player_zero_runs(self, db_session):
        """Player with 0 M+ runs -> fail with INSUFFICIENT_KEYS."""
        _add_players(db_session, ["P1"])
        _add_benchmark(db_session)
        fixture = _load_fixture("raiderio_character_empty.json")

        respx.get("https://raider.io/api/v1/characters/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            await collector.collect(WEEK)

        snap_repo = SnapshotRepo(db_session)
        snap = snap_repo.get_by_week(WEEK)[0]
        assert snap.status is SnapshotStatus.FAIL
        assert snap.mplus_runs_total == 0
        assert snap.vault_slots_earned == 0

    @respx.mock
    @pytest.mark.anyio
    async def test_runs_all_below_key_level(self, db_session):
        """Player has runs but all below min_key_level."""
        _add_players(db_session, ["P1"])
        _add_benchmark(db_session, min_key_level=10)

        fixture = _load_fixture("raiderio_character_partial.json")
        # partial fixture has 1 run at mythic_level 8

        respx.get("https://raider.io/api/v1/characters/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            await collector.collect(WEEK)

        snap_repo = SnapshotRepo(db_session)
        snap = snap_repo.get_by_week(WEEK)[0]
        assert snap.status is SnapshotStatus.FAIL
        assert snap.mplus_runs_total == 1
        assert snap.mplus_runs_at_level == 0

    @pytest.mark.anyio
    async def test_empty_roster(self, db_session):
        """No active players -> collection completes with 0 collected."""
        _add_benchmark(db_session)

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            result = await collector.collect(WEEK)

        assert result.players_collected == 0
        assert result.status == "completed"

    @respx.mock
    @pytest.mark.anyio
    async def test_collection_run_metadata(self, db_session):
        """Collection run is logged with correct metadata."""
        _add_players(db_session, ["P1"])
        _add_benchmark(db_session)
        fixture = _load_fixture("raiderio_character.json")

        respx.get("https://raider.io/api/v1/characters/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        config = _config()
        async with httpx.AsyncClient() as http:
            client = RaiderioClient(
                raiderio_config=config.raiderio,
                collection_config=config.collection,
                http_client=http,
            )
            collector = WeeklyCollector(db_session, client, config)
            await collector.collect(WEEK)

        run_repo = CollectionRunRepo(db_session)
        runs = run_repo.get_by_week(WEEK)
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        assert runs[0]["players_collected"] == 1
        assert runs[0]["completed_at"] is not None


class TestCollectWeeklyScript:
    def test_most_recent_tuesday(self):
        from scripts.collect_weekly import _most_recent_tuesday

        # Tuesday itself
        assert _most_recent_tuesday(date(2026, 3, 17)) == date(2026, 3, 17)
        # Wednesday
        assert _most_recent_tuesday(date(2026, 3, 18)) == date(2026, 3, 17)
        # Monday (day before next Tuesday)
        assert _most_recent_tuesday(date(2026, 3, 23)) == date(2026, 3, 17)
        # Sunday
        assert _most_recent_tuesday(date(2026, 3, 22)) == date(2026, 3, 17)
