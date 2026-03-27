"""Shared test fixtures: in-memory SQLite, sample data factories."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from raid_ledger.db.schema import Base
from raid_ledger.models.benchmark import WeeklyBenchmark
from raid_ledger.models.player import Player, PlayerStatus
from raid_ledger.models.snapshot import SnapshotStatus, WeeklySnapshot


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def db_session(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def sample_player() -> Player:
    return Player(
        name="Testchar",
        realm="tichondrius",
        region="us",
        class_name="Death Knight",
        spec_name="Frost",
        role="dps",
        status=PlayerStatus.CORE,
        joined_date=date(2026, 3, 1),
    )


@pytest.fixture()
def sample_benchmark() -> WeeklyBenchmark:
    return WeeklyBenchmark(
        week_of=date(2026, 3, 17),
        min_mplus_runs=8,
        min_key_level=10,
        min_ilvl=None,
        min_vault_slots=3,
        set_by="OfficerA",
        set_at=datetime(2026, 3, 17, 18, 0, 0, tzinfo=UTC),
    )


@pytest.fixture()
def sample_snapshot() -> WeeklySnapshot:
    return WeeklySnapshot(
        player_id=1,
        week_of=date(2026, 3, 17),
        mplus_runs_total=10,
        mplus_runs_at_level=8,
        highest_key_level=12,
        item_level=None,
        vault_slots_earned=3,
        raiderio_score=None,
        status=SnapshotStatus.PASS,
        reasons=[],
        data_source="wowaudit",
    )
