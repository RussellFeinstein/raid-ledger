"""Pass/fail/flag evaluation — OR-logic rules engine.

All thresholds come from the weekly benchmark. Failing ANY active check = failed week.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from raid_ledger.api.raiderio import CharacterData
from raid_ledger.models.benchmark import WeeklyBenchmark
from raid_ledger.models.snapshot import FailureReason, FlagReason, SnapshotStatus


@dataclass(frozen=True)
class EvaluationResult:
    """Result of evaluating a player's week against the benchmark."""

    status: SnapshotStatus
    reasons: list[str] = field(default_factory=list)


def derive_vault_slots(mplus_runs_at_level: int) -> int:
    """Derive Great Vault M+ slots from run count.

    1 run = 1 slot, 4 runs = 2 slots, 8 runs = 3 slots.
    """
    if mplus_runs_at_level >= 8:
        return 3
    if mplus_runs_at_level >= 4:
        return 2
    if mplus_runs_at_level >= 1:
        return 1
    return 0


def evaluate(
    char_data: CharacterData | None,
    benchmark: WeeklyBenchmark,
) -> EvaluationResult:
    """Evaluate a player's weekly data against the benchmark.

    Args:
        char_data: Parsed character data from Raider.io, or None if the API
            returned nothing (NO_DATA flag).
        benchmark: The officer-configured requirements for this week.

    Returns:
        EvaluationResult with status (pass/fail/flag) and list of reason strings.
    """
    # No data at all — flag for officer review
    if char_data is None:
        return EvaluationResult(
            status=SnapshotStatus.FLAG,
            reasons=[FlagReason.NO_DATA],
        )

    reasons: list[str] = []

    # M+ keys check
    runs_at_level = char_data.count_runs_at_level(benchmark.min_key_level)
    if runs_at_level < benchmark.min_mplus_runs:
        reasons.append(FailureReason.INSUFFICIENT_KEYS)

    # ilvl check — only when benchmark.min_ilvl is set
    if benchmark.min_ilvl is not None:
        if char_data.item_level is None:
            # Can't confirm ilvl — flag, don't fail
            return EvaluationResult(
                status=SnapshotStatus.FLAG,
                reasons=[FlagReason.NO_DATA],
            )
        if char_data.item_level < benchmark.min_ilvl:
            reasons.append(FailureReason.LOW_ILVL)

    if reasons:
        return EvaluationResult(status=SnapshotStatus.FAIL, reasons=reasons)

    return EvaluationResult(status=SnapshotStatus.PASS, reasons=[])
