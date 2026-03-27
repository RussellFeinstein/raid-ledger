"""Tests for Pydantic domain models and enums."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from raid_ledger.models.benchmark import WeeklyBenchmark
from raid_ledger.models.player import Player, PlayerStatus
from raid_ledger.models.snapshot import (
    FailureReason,
    FlagReason,
    SnapshotStatus,
    WeeklySnapshot,
)

# ---------------------------------------------------------------------------
# PlayerStatus enum
# ---------------------------------------------------------------------------

class TestPlayerStatus:
    def test_valid_values(self):
        assert PlayerStatus("core") is PlayerStatus.CORE
        assert PlayerStatus("trial") is PlayerStatus.TRIAL
        assert PlayerStatus("bench") is PlayerStatus.BENCH
        assert PlayerStatus("inactive") is PlayerStatus.INACTIVE

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            PlayerStatus("retired")


# ---------------------------------------------------------------------------
# SnapshotStatus / FailureReason / FlagReason enums
# ---------------------------------------------------------------------------

class TestSnapshotStatus:
    def test_valid_values(self):
        assert SnapshotStatus("pass") is SnapshotStatus.PASS
        assert SnapshotStatus("fail") is SnapshotStatus.FAIL
        assert SnapshotStatus("flag") is SnapshotStatus.FLAG

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            SnapshotStatus("warning")


class TestFailureReason:
    def test_valid_values(self):
        assert FailureReason("insufficient_keys") is FailureReason.INSUFFICIENT_KEYS
        assert FailureReason("low_ilvl") is FailureReason.LOW_ILVL
        assert FailureReason("manual_fail") is FailureReason.MANUAL_FAIL


class TestFlagReason:
    def test_valid_values(self):
        assert FlagReason("no_data") is FlagReason.NO_DATA
        assert FlagReason("data_anomaly") is FlagReason.DATA_ANOMALY
        assert FlagReason("approved_absence") is FlagReason.APPROVED_ABSENCE
        assert FlagReason("manual_flag") is FlagReason.MANUAL_FLAG


# ---------------------------------------------------------------------------
# Player model
# ---------------------------------------------------------------------------

class TestPlayer:
    def test_valid_construction(self, sample_player):
        assert sample_player.name == "Testchar"
        assert sample_player.realm == "tichondrius"
        assert sample_player.region == "us"
        assert sample_player.class_name == "Death Knight"
        assert sample_player.role == "dps"
        assert sample_player.status is PlayerStatus.CORE
        assert sample_player.player_id is None

    def test_defaults(self):
        p = Player(
            name="Alt",
            realm="area-52",
            class_name="Mage",
            role="dps",
            joined_date=date(2026, 3, 1),
        )
        assert p.region == "us"
        assert p.status is PlayerStatus.CORE
        assert p.spec_name is None

    def test_frozen(self, sample_player):
        with pytest.raises(ValidationError):
            sample_player.name = "Changed"

    def test_utf8_name(self):
        p = Player(
            name="Tëstñame",
            realm="tichondrius",
            class_name="Paladin",
            role="tank",
            joined_date=date(2026, 3, 1),
        )
        assert p.name == "Tëstñame"

    def test_hyphenated_realm(self):
        p = Player(
            name="Alt",
            realm="bleeding-hollow",
            class_name="Warrior",
            role="tank",
            joined_date=date(2026, 3, 1),
        )
        assert p.realm == "bleeding-hollow"


# ---------------------------------------------------------------------------
# WeeklySnapshot model
# ---------------------------------------------------------------------------

class TestWeeklySnapshot:
    def test_valid_construction(self, sample_snapshot):
        assert sample_snapshot.player_id == 1
        assert sample_snapshot.mplus_runs_at_level == 8
        assert sample_snapshot.status is SnapshotStatus.PASS
        assert sample_snapshot.reasons == []

    def test_fail_with_reasons(self):
        s = WeeklySnapshot(
            player_id=1,
            week_of=date(2026, 3, 17),
            status=SnapshotStatus.FAIL,
            reasons=["insufficient_keys", "low_ilvl"],
        )
        assert s.status is SnapshotStatus.FAIL
        assert len(s.reasons) == 2

    def test_flag_with_reason(self):
        s = WeeklySnapshot(
            player_id=1,
            week_of=date(2026, 3, 17),
            status=SnapshotStatus.FLAG,
            reasons=["no_data"],
        )
        assert s.status is SnapshotStatus.FLAG

    def test_frozen(self, sample_snapshot):
        with pytest.raises(ValidationError):
            sample_snapshot.status = SnapshotStatus.FAIL

    def test_defaults(self):
        s = WeeklySnapshot(
            player_id=1,
            week_of=date(2026, 3, 17),
            status=SnapshotStatus.PASS,
        )
        assert s.mplus_runs_total == 0
        assert s.mplus_runs_at_level == 0
        assert s.vault_slots_earned == 0
        assert s.highest_key_level is None
        assert s.item_level is None
        assert s.data_source == "wowaudit"
        assert s.override_by is None


# ---------------------------------------------------------------------------
# WeeklyBenchmark model
# ---------------------------------------------------------------------------

class TestWeeklyBenchmark:
    def test_valid_construction(self, sample_benchmark):
        assert sample_benchmark.week_of == date(2026, 3, 17)
        assert sample_benchmark.min_mplus_runs == 8
        assert sample_benchmark.min_key_level == 10
        assert sample_benchmark.min_ilvl is None
        assert sample_benchmark.min_vault_slots == 3
        assert sample_benchmark.set_by == "OfficerA"

    def test_with_ilvl(self):
        b = WeeklyBenchmark(
            week_of=date(2026, 3, 17),
            min_mplus_runs=8,
            min_key_level=10,
            min_ilvl=615,
            min_vault_slots=3,
            set_by="OfficerB",
            set_at=datetime(2026, 3, 17, 18, 0, 0, tzinfo=UTC),
        )
        assert b.min_ilvl == 615

    def test_frozen(self, sample_benchmark):
        with pytest.raises(ValidationError):
            sample_benchmark.min_key_level = 12
