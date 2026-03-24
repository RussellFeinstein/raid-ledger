"""Tests for the rules engine — exhaustive pass/fail/flag scenarios."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from raid_ledger.api.raiderio import CharacterData
from raid_ledger.engine.rules import derive_vault_slots, evaluate
from raid_ledger.models.benchmark import WeeklyBenchmark
from raid_ledger.models.snapshot import FailureReason, FlagReason, SnapshotStatus


def _benchmark(
    min_runs: int = 8,
    min_key: int = 10,
    min_ilvl: int | None = None,
) -> WeeklyBenchmark:
    return WeeklyBenchmark(
        week_of=date(2026, 3, 17),
        min_mplus_runs=min_runs,
        min_key_level=min_key,
        min_ilvl=min_ilvl,
        min_vault_slots=3,
        set_by="Officer",
        set_at=datetime(2026, 3, 17, 18, 0, 0, tzinfo=UTC),
    )


def _char(
    runs: list[dict] | None = None,
    ilvl: float | None = 620.0,
) -> CharacterData:
    return CharacterData(
        name="Test",
        realm="tichondrius",
        region="us",
        class_name="Mage",
        spec_name="Fire",
        item_level=ilvl,
        mplus_runs=runs or [],
        raiderio_score=2400.0,
    )


def _runs_at_level(level: int, count: int) -> list[dict]:
    return [{"mythic_level": level} for _ in range(count)]


# ---------------------------------------------------------------------------
# Pass scenarios
# ---------------------------------------------------------------------------


class TestPass:
    def test_meets_all_thresholds(self):
        result = evaluate(_char(runs=_runs_at_level(10, 8)), _benchmark())
        assert result.status is SnapshotStatus.PASS
        assert result.reasons == []

    def test_exceeds_thresholds(self):
        result = evaluate(_char(runs=_runs_at_level(15, 12)), _benchmark())
        assert result.status is SnapshotStatus.PASS

    def test_exactly_at_threshold(self):
        """Exactly meeting the requirement = pass, not fail."""
        result = evaluate(_char(runs=_runs_at_level(10, 8)), _benchmark(min_runs=8, min_key=10))
        assert result.status is SnapshotStatus.PASS

    def test_no_mplus_requirement(self):
        """min_mplus_runs=0 means no M+ requirement — everyone passes."""
        result = evaluate(_char(runs=[]), _benchmark(min_runs=0))
        assert result.status is SnapshotStatus.PASS

    def test_passes_with_ilvl_check(self):
        result = evaluate(
            _char(runs=_runs_at_level(10, 8), ilvl=620.0),
            _benchmark(min_ilvl=615),
        )
        assert result.status is SnapshotStatus.PASS


# ---------------------------------------------------------------------------
# Fail scenarios
# ---------------------------------------------------------------------------


class TestFail:
    def test_insufficient_keys(self):
        result = evaluate(_char(runs=_runs_at_level(10, 5)), _benchmark(min_runs=8))
        assert result.status is SnapshotStatus.FAIL
        assert FailureReason.INSUFFICIENT_KEYS in result.reasons

    def test_zero_runs(self):
        result = evaluate(_char(runs=[]), _benchmark(min_runs=8))
        assert result.status is SnapshotStatus.FAIL
        assert FailureReason.INSUFFICIENT_KEYS in result.reasons

    def test_runs_below_key_level(self):
        """Player has runs but all below the threshold key level."""
        result = evaluate(_char(runs=_runs_at_level(8, 10)), _benchmark(min_key=10))
        assert result.status is SnapshotStatus.FAIL
        assert FailureReason.INSUFFICIENT_KEYS in result.reasons

    def test_low_ilvl(self):
        result = evaluate(
            _char(runs=_runs_at_level(10, 8), ilvl=610.0),
            _benchmark(min_ilvl=615),
        )
        assert result.status is SnapshotStatus.FAIL
        assert FailureReason.LOW_ILVL in result.reasons

    def test_both_keys_and_ilvl(self):
        result = evaluate(
            _char(runs=_runs_at_level(10, 3), ilvl=600.0),
            _benchmark(min_runs=8, min_ilvl=615),
        )
        assert result.status is SnapshotStatus.FAIL
        assert FailureReason.INSUFFICIENT_KEYS in result.reasons
        assert FailureReason.LOW_ILVL in result.reasons
        assert len(result.reasons) == 2


# ---------------------------------------------------------------------------
# Flag scenarios
# ---------------------------------------------------------------------------


class TestFlag:
    def test_no_data(self):
        result = evaluate(None, _benchmark())
        assert result.status is SnapshotStatus.FLAG
        assert FlagReason.NO_DATA in result.reasons

    def test_ilvl_none_with_ilvl_check(self):
        """API returned no ilvl but benchmark requires it — flag, not fail."""
        result = evaluate(
            _char(runs=_runs_at_level(10, 8), ilvl=None),
            _benchmark(min_ilvl=615),
        )
        assert result.status is SnapshotStatus.FLAG
        assert FlagReason.NO_DATA in result.reasons


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_ilvl_not_enforced(self):
        """min_ilvl=None means ilvl check is skipped entirely."""
        result = evaluate(
            _char(runs=_runs_at_level(10, 8), ilvl=100.0),
            _benchmark(min_ilvl=None),
        )
        assert result.status is SnapshotStatus.PASS

    def test_mixed_key_levels(self):
        """Only runs at or above min_key_level count."""
        runs = [
            {"mythic_level": 12},
            {"mythic_level": 10},
            {"mythic_level": 8},
            {"mythic_level": 11},
            {"mythic_level": 7},
            {"mythic_level": 10},
            {"mythic_level": 13},
            {"mythic_level": 9},
            {"mythic_level": 10},
            {"mythic_level": 6},
        ]
        result = evaluate(_char(runs=runs), _benchmark(min_runs=8, min_key=10))
        # Keys at level 10+: 12, 10, 11, 10, 13, 10 = 6
        assert result.status is SnapshotStatus.FAIL
        assert FailureReason.INSUFFICIENT_KEYS in result.reasons


# ---------------------------------------------------------------------------
# Vault derivation
# ---------------------------------------------------------------------------


class TestVaultDerivation:
    @pytest.mark.parametrize(
        ("runs", "expected"),
        [
            (0, 0),
            (1, 1),
            (2, 1),
            (3, 1),
            (4, 2),
            (5, 2),
            (7, 2),
            (8, 3),
            (10, 3),
            (100, 3),
        ],
    )
    def test_vault_slots(self, runs, expected):
        assert derive_vault_slots(runs) == expected
