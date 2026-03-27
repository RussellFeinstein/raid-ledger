"""Tests for the weekly collector — integration with mocked wowaudit API + in-memory DB."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from raid_ledger.api.wowaudit import WowauditAuthError, WowauditCharacter, WowauditClient
from raid_ledger.config import AppConfig, CollectionConfig, WowauditConfig
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


def _load_fixture(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text())


def _config() -> AppConfig:
    return AppConfig(
        collection=CollectionConfig(
            request_delay_seconds=0,
            max_retries=3,
            timeout_seconds=5,
        ),
        wowaudit=WowauditConfig(base_url="https://wowaudit.com", api_key="test"),
    )


def _add_players(session, names_and_realms: list[tuple[str, str]]) -> list[Player]:
    """Add players with specific name+realm pairs for matching."""
    repo = PlayerRepo(session)
    players = []
    for name, realm in names_and_realms:
        p = repo.create(Player(
            name=name, realm=realm, class_name="Mage",
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


def _mock_client(historical_data: dict | None = None) -> WowauditClient:
    """Create a WowauditClient with mocked fetch_historical_data."""
    client = WowauditClient(
        wowaudit_config=WowauditConfig(base_url="https://wowaudit.com", api_key="test"),
        collection_config=CollectionConfig(max_retries=3, timeout_seconds=5),
    )

    if historical_data is not None:
        fixture = historical_data
    else:
        fixture = _load_fixture("wowaudit_historical_data.json")

    # Parse fixture into the return format
    period = fixture.get("period", 1055)
    characters: dict[int, WowauditCharacter] = {}
    for entry in fixture.get("characters", []):
        data = entry.get("data")
        if data is None:
            continue
        characters[entry["id"]] = WowauditCharacter(
            wowaudit_id=entry["id"],
            name=entry.get("name", ""),
            realm=entry.get("realm", ""),
            dungeons_done=data.get("dungeons_done") or [],
            vault_options=data.get("vault_options") or {},
            world_quests_done=data.get("world_quests_done", 0),
            regular_mythic_dungeons_done=data.get("regular_mythic_dungeons_done", 0),
            raw_json=json.dumps(entry),
        )

    client.fetch_historical_data = AsyncMock(return_value=(period, characters))
    return client


class TestCollectorFullFlow:
    @pytest.mark.anyio
    async def test_matched_players_success(self, db_session):
        """Players whose name+realm match wowaudit data get correct snapshots."""
        _add_players(db_session, [
            ("Testplayer", "Tichondrius"),
            ("Tankyboy", "Stormrage"),
            ("Healsworth", "Area 52"),
        ])
        _add_benchmark(db_session)

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        result = await collector.collect(WEEK)

        assert result.players_collected == 3
        assert result.api_errors == 0
        assert result.status == "completed"

        snap_repo = SnapshotRepo(db_session)
        snaps = snap_repo.get_by_week(WEEK)
        assert len(snaps) == 3

        # Testplayer has 10 runs, 9 at level 10+ -> passes (needs 8)
        testplayer_snap = next(s for s in snaps if s.mplus_runs_total == 10)
        assert testplayer_snap.status is SnapshotStatus.PASS
        assert testplayer_snap.data_source == "wowaudit"

    @pytest.mark.anyio
    async def test_unmatched_player_flagged(self, db_session):
        """Player not in wowaudit response gets flagged as NO_DATA."""
        _add_players(db_session, [
            ("Testplayer", "Tichondrius"),
            ("Unknown", "Sargeras"),  # not in fixture
        ])
        _add_benchmark(db_session)

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        result = await collector.collect(WEEK)

        assert result.players_collected == 2

        snap_repo = SnapshotRepo(db_session)
        snaps = snap_repo.get_by_week(WEEK)
        assert len(snaps) == 2
        statuses = {s.status for s in snaps}
        assert SnapshotStatus.FLAG in statuses

    @pytest.mark.anyio
    async def test_case_insensitive_matching(self, db_session):
        """Name+realm matching is case-insensitive."""
        _add_players(db_session, [("testplayer", "tichondrius")])
        _add_benchmark(db_session)

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        result = await collector.collect(WEEK)

        assert result.players_collected == 1
        snap_repo = SnapshotRepo(db_session)
        snap = snap_repo.get_by_week(WEEK)[0]
        assert snap.mplus_runs_total == 10  # matched to Testplayer

    @pytest.mark.anyio
    async def test_upsert_rerun(self, db_session):
        """Collect twice for same week -> only 1 snapshot per player."""
        _add_players(db_session, [("Testplayer", "Tichondrius")])
        _add_benchmark(db_session)

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        await collector.collect(WEEK)
        await collector.collect(WEEK)

        snap_repo = SnapshotRepo(db_session)
        snaps = snap_repo.get_by_week(WEEK)
        assert len(snaps) == 1

    @pytest.mark.anyio
    async def test_no_benchmark_ever_set(self, db_session):
        """Collection aborts with NoBenchmarkError."""
        _add_players(db_session, [("P1", "Tichondrius")])

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        with pytest.raises(NoBenchmarkError):
            await collector.collect(WEEK)

        run_repo = CollectionRunRepo(db_session)
        runs = run_repo.get_by_week(WEEK)
        assert len(runs) == 1
        assert runs[0]["status"] == "failed"

    @pytest.mark.anyio
    async def test_benchmark_copy_forward(self, db_session):
        """No benchmark for this week -> copies most recent."""
        _add_players(db_session, [("Testplayer", "Tichondrius")])
        _add_benchmark(db_session, week_of=date(2026, 3, 10))

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        result = await collector.collect(WEEK)

        assert result.players_collected == 1

        bench_repo = BenchmarkRepo(db_session)
        copied = bench_repo.get_by_week(WEEK)
        assert copied is not None
        assert copied.min_mplus_runs == 8


class TestCollectorErrors:
    @pytest.mark.anyio
    async def test_batch_fetch_failure_flags_all(self, db_session):
        """If the batch API call fails, all players get flagged."""
        _add_players(db_session, [("P1", "Tichondrius"), ("P2", "Stormrage")])
        _add_benchmark(db_session)

        client = _mock_client()
        client.fetch_historical_data = AsyncMock(
            side_effect=WowauditAuthError("Auth failed (401)")
        )
        collector = WeeklyCollector(db_session, client, _config())
        result = await collector.collect(WEEK)

        assert result.players_collected == 0
        assert result.api_errors == 2
        assert result.status == "failed"

        snap_repo = SnapshotRepo(db_session)
        snaps = snap_repo.get_by_week(WEEK)
        assert len(snaps) == 2
        assert all(s.status is SnapshotStatus.FLAG for s in snaps)

        run_repo = CollectionRunRepo(db_session)
        runs = run_repo.get_by_week(WEEK)
        assert runs[0]["status"] == "failed"

    @pytest.mark.anyio
    async def test_null_data_player_flagged(self, db_session):
        """Player with data=null in wowaudit response gets flagged."""
        # Meleeguy has data: null in the fixture
        _add_players(db_session, [("Meleeguy", "Illidan")])
        _add_benchmark(db_session)

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        result = await collector.collect(WEEK)

        assert result.players_collected == 1
        snap_repo = SnapshotRepo(db_session)
        snap = snap_repo.get_by_week(WEEK)[0]
        assert snap.status is SnapshotStatus.FLAG


class TestCollectorEdgeCases:
    @pytest.mark.anyio
    async def test_player_zero_keystones(self, db_session):
        """Player with 0 keystones (only mythic 0s) -> fail."""
        _add_players(db_session, [("Tankyboy", "Stormrage")])
        _add_benchmark(db_session)

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        await collector.collect(WEEK)

        snap_repo = SnapshotRepo(db_session)
        snap = snap_repo.get_by_week(WEEK)[0]
        assert snap.status is SnapshotStatus.FAIL
        assert snap.mplus_runs_total == 0
        assert snap.vault_slots_earned == 0

    @pytest.mark.anyio
    async def test_vault_slots_from_wowaudit(self, db_session):
        """Vault slots come from real wowaudit data, not derived."""
        _add_players(db_session, [("Testplayer", "Tichondrius")])
        _add_benchmark(db_session)

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        await collector.collect(WEEK)

        snap_repo = SnapshotRepo(db_session)
        snap = snap_repo.get_by_week(WEEK)[0]
        # Testplayer has 3 non-null dungeon vault options
        assert snap.vault_slots_earned == 3

    @pytest.mark.anyio
    async def test_empty_roster(self, db_session):
        """No active players -> collection completes with 0 collected."""
        _add_benchmark(db_session)

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        result = await collector.collect(WEEK)

        assert result.players_collected == 0
        assert result.status == "completed"

    @pytest.mark.anyio
    async def test_collection_run_metadata(self, db_session):
        """Collection run is logged with correct metadata."""
        _add_players(db_session, [("Testplayer", "Tichondrius")])
        _add_benchmark(db_session)

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        await collector.collect(WEEK)

        run_repo = CollectionRunRepo(db_session)
        runs = run_repo.get_by_week(WEEK)
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        assert runs[0]["players_collected"] == 1
        assert runs[0]["completed_at"] is not None

    @pytest.mark.anyio
    async def test_item_level_and_score_are_none(self, db_session):
        """Wowaudit snapshots have item_level=None and raiderio_score=None."""
        _add_players(db_session, [("Testplayer", "Tichondrius")])
        _add_benchmark(db_session)

        client = _mock_client()
        collector = WeeklyCollector(db_session, client, _config())
        await collector.collect(WEEK)

        snap_repo = SnapshotRepo(db_session)
        snap = snap_repo.get_by_week(WEEK)[0]
        assert snap.item_level is None
        assert snap.raiderio_score is None
        assert snap.data_source == "wowaudit"


class TestCollectWeeklyScript:
    def test_most_recent_tuesday(self):
        from scripts.collect_weekly import _most_recent_tuesday

        assert _most_recent_tuesday(date(2026, 3, 17)) == date(2026, 3, 17)
        assert _most_recent_tuesday(date(2026, 3, 18)) == date(2026, 3, 17)
        assert _most_recent_tuesday(date(2026, 3, 23)) == date(2026, 3, 17)
        assert _most_recent_tuesday(date(2026, 3, 22)) == date(2026, 3, 17)
