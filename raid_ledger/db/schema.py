"""SQLAlchemy 2.0 ORM table definitions.

Six tables: players, weekly_benchmarks, weekly_snapshots, officer_notes,
collection_runs, settings.  All foreign keys use ON DELETE RESTRICT —
players are deactivated, never deleted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Base(DeclarativeBase):
    pass


class PlayerRow(Base):
    __tablename__ = "players"

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    realm: Mapped[str] = mapped_column(String(50), nullable=False)
    region: Mapped[str] = mapped_column(String(5), nullable=False, default="us")
    class_name: Mapped[str] = mapped_column(String(30), nullable=False)
    spec_name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="core")
    joined_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("name", "realm", "region", name="uq_player_identity"),
        Index("ix_players_status", "status"),
    )


class WeeklyBenchmarkRow(Base):
    __tablename__ = "weekly_benchmarks"

    benchmark_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_of: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    min_mplus_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    min_key_level: Mapped[int] = mapped_column(Integer, nullable=False)
    min_ilvl: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_vault_slots: Mapped[int] = mapped_column(Integer, nullable=False)
    set_by: Mapped[str] = mapped_column(String(50), nullable=False)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_benchmarks_week", "week_of"),)


class WeeklySnapshotRow(Base):
    __tablename__ = "weekly_snapshots"

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("players.player_id", ondelete="RESTRICT"),
        nullable=False,
    )
    week_of: Mapped[date] = mapped_column(Date, nullable=False)
    mplus_runs_total: Mapped[int] = mapped_column(Integer, default=0)
    mplus_runs_at_level: Mapped[int] = mapped_column(Integer, default=0)
    highest_key_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    vault_slots_earned: Mapped[int] = mapped_column(Integer, default=0)
    raiderio_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    data_source: Mapped[str] = mapped_column(String(20), default="wowaudit")
    raw_api_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("player_id", "week_of", name="uq_snapshot_player_week"),
        Index("ix_snapshots_week", "week_of"),
        Index("ix_snapshots_player", "player_id"),
        Index("ix_snapshots_status", "week_of", "status"),
    )


class OfficerNoteRow(Base):
    __tablename__ = "officer_notes"

    note_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("players.player_id", ondelete="RESTRICT"),
        nullable=False,
    )
    week_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (Index("ix_notes_player_week", "player_id", "week_of"),)


class CollectionRunRow(Base):
    __tablename__ = "collection_runs"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_of: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    players_collected: Mapped[int] = mapped_column(Integer, default=0)
    api_errors: Mapped[int] = mapped_column(Integer, default=0)
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_collection_week", "week_of"),)


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    set_by: Mapped[str] = mapped_column(String(50), nullable=False)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
