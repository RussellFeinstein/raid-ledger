"""Failure pattern detection queries.

All thresholds are configurable — passed as parameters, not hardcoded.
Queries use the repository layer and work on both SQLite and PostgreSQL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from raid_ledger.db.schema import PlayerRow, WeeklySnapshotRow
from raid_ledger.models.player import PlayerStatus
from raid_ledger.models.snapshot import SnapshotStatus

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerWeekSummary:
    """One player's status for a single week, with player context."""

    player_id: int
    name: str
    realm: str
    class_name: str
    role: str
    player_status: str
    snapshot_status: str
    mplus_runs_at_level: int
    highest_key_level: int | None
    item_level: float | None
    vault_slots_earned: int
    raiderio_score: float | None
    reasons: list[str]


@dataclass(frozen=True)
class FailureRate:
    """Failure rate for a single player over a lookback window."""

    player_id: int
    name: str
    failures: int
    total_weeks: int

    @property
    def rate(self) -> float:
        return self.failures / self.total_weeks if self.total_weeks > 0 else 0.0


@dataclass(frozen=True)
class PlayerStreak:
    """Current consecutive streak for a player."""

    player_id: int
    name: str
    streak_type: str  # "pass", "fail", or "flag"
    streak_length: int


_ACTIVE_STATUSES = [PlayerStatus.CORE, PlayerStatus.TRIAL]


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class FailureAnalyzer:
    """Pattern detection across weekly snapshots.

    All methods accept threshold parameters — nothing is hardcoded.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_weekly_summary(self, week_of: date) -> list[PlayerWeekSummary]:
        """All players' pass/fail/flag for a given week, joined with player info.

        Returns flagged players first, then failures, then passes.
        """
        stmt = (
            select(
                PlayerRow.player_id,
                PlayerRow.name,
                PlayerRow.realm,
                PlayerRow.class_name,
                PlayerRow.role,
                PlayerRow.status.label("player_status"),
                WeeklySnapshotRow.status.label("snapshot_status"),
                WeeklySnapshotRow.mplus_runs_at_level,
                WeeklySnapshotRow.highest_key_level,
                WeeklySnapshotRow.item_level,
                WeeklySnapshotRow.vault_slots_earned,
                WeeklySnapshotRow.raiderio_score,
                WeeklySnapshotRow.reasons,
            )
            .join(PlayerRow, WeeklySnapshotRow.player_id == PlayerRow.player_id)
            .where(WeeklySnapshotRow.week_of == week_of)
            .order_by(
                case(
                    (WeeklySnapshotRow.status == SnapshotStatus.FLAG, 0),
                    (WeeklySnapshotRow.status == SnapshotStatus.FAIL, 1),
                    else_=2,
                ),
                PlayerRow.name,
            )
        )
        rows = self._session.execute(stmt).all()
        return [
            PlayerWeekSummary(
                player_id=r.player_id,
                name=r.name,
                realm=r.realm,
                class_name=r.class_name,
                role=r.role,
                player_status=r.player_status,
                snapshot_status=r.snapshot_status,
                mplus_runs_at_level=r.mplus_runs_at_level,
                highest_key_level=r.highest_key_level,
                item_level=r.item_level,
                vault_slots_earned=r.vault_slots_earned,
                raiderio_score=r.raiderio_score,
                reasons=json.loads(r.reasons) if r.reasons else [],
            )
            for r in rows
        ]

    def get_player_history(
        self, player_id: int, weeks: int | None = None
    ) -> list[dict]:
        """Per-player timeline, most recent first.

        Returns dicts with snapshot fields + parsed reasons.
        """
        stmt = (
            select(WeeklySnapshotRow)
            .where(WeeklySnapshotRow.player_id == player_id)
            .order_by(WeeklySnapshotRow.week_of.desc())
        )
        if weeks is not None:
            stmt = stmt.limit(weeks)

        rows = self._session.execute(stmt).scalars().all()
        return [
            {
                "week_of": r.week_of,
                "status": r.status,
                "mplus_runs_total": r.mplus_runs_total,
                "mplus_runs_at_level": r.mplus_runs_at_level,
                "highest_key_level": r.highest_key_level,
                "item_level": r.item_level,
                "vault_slots_earned": r.vault_slots_earned,
                "raiderio_score": r.raiderio_score,
                "reasons": json.loads(r.reasons) if r.reasons else [],
                "override_by": r.override_by,
            }
            for r in rows
        ]

    def get_failure_rate(
        self, player_id: int, lookback_weeks: int = 5
    ) -> FailureRate:
        """'Failed X of last N weeks' for a single player.

        Flags do NOT count as failures. Only weeks where the player has
        data are counted (a player who joined 3 weeks ago is measured
        as 'X of 3', not 'X of 5').
        """
        stmt = (
            select(WeeklySnapshotRow.status)
            .where(WeeklySnapshotRow.player_id == player_id)
            .order_by(WeeklySnapshotRow.week_of.desc())
            .limit(lookback_weeks)
        )
        rows = self._session.execute(stmt).scalars().all()

        # Get player name
        player = self._session.get(PlayerRow, player_id)
        name = player.name if player else ""

        total = len(rows)
        failures = sum(1 for s in rows if s == SnapshotStatus.FAIL)

        return FailureRate(
            player_id=player_id,
            name=name,
            failures=failures,
            total_weeks=total,
        )

    def get_chronic_underperformers(
        self,
        fail_threshold: int = 3,
        lookback_weeks: int = 5,
    ) -> list[FailureRate]:
        """All active players who failed >= fail_threshold of last lookback_weeks.

        Only includes core/trial players. Flags do NOT count as failures.
        """
        active_players = (
            self._session.execute(
                select(PlayerRow)
                .where(PlayerRow.status.in_(_ACTIVE_STATUSES))
                .order_by(PlayerRow.name)
            )
            .scalars()
            .all()
        )

        results: list[FailureRate] = []
        for player in active_players:
            rate = self.get_failure_rate(player.player_id, lookback_weeks)
            if rate.failures >= fail_threshold:
                results.append(rate)

        return results

    def get_current_streaks(self) -> list[PlayerStreak]:
        """Current consecutive pass/fail streak for each active player.

        Walks each player's history from most recent week backward.
        A streak ends when the status changes. Flags break streaks.
        """
        active_players = (
            self._session.execute(
                select(PlayerRow)
                .where(PlayerRow.status.in_(_ACTIVE_STATUSES))
                .order_by(PlayerRow.name)
            )
            .scalars()
            .all()
        )

        streaks: list[PlayerStreak] = []
        for player in active_players:
            snapshots = (
                self._session.execute(
                    select(WeeklySnapshotRow.status)
                    .where(WeeklySnapshotRow.player_id == player.player_id)
                    .order_by(WeeklySnapshotRow.week_of.desc())
                )
                .scalars()
                .all()
            )

            if not snapshots:
                streaks.append(PlayerStreak(
                    player_id=player.player_id,
                    name=player.name,
                    streak_type="pass",
                    streak_length=0,
                ))
                continue

            first_status = snapshots[0]
            count = 1
            for s in snapshots[1:]:
                if s == first_status:
                    count += 1
                else:
                    break

            streaks.append(PlayerStreak(
                player_id=player.player_id,
                name=player.name,
                streak_type=first_status,
                streak_length=count,
            ))

        return streaks

    def get_failure_breakdown(self, week_of: date) -> dict[str, int]:
        """Count of each failure/flag reason for a given week.

        Parses the JSON reasons column and counts each reason string.
        """
        stmt = (
            select(WeeklySnapshotRow.reasons)
            .where(
                WeeklySnapshotRow.week_of == week_of,
                WeeklySnapshotRow.status.in_([
                    SnapshotStatus.FAIL, SnapshotStatus.FLAG,
                ]),
            )
        )
        rows = self._session.execute(stmt).scalars().all()

        counts: dict[str, int] = {}
        for raw in rows:
            if not raw:
                continue
            reasons = json.loads(raw)
            for reason in reasons:
                counts[reason] = counts.get(reason, 0) + 1

        return counts

    def get_trial_flags(
        self, lookback_weeks: int = 4
    ) -> list[FailureRate]:
        """Trial players with any failures in their first N weeks.

        Returns all trial players who have at least 1 failure.
        """
        trial_players = (
            self._session.execute(
                select(PlayerRow)
                .where(PlayerRow.status == PlayerStatus.TRIAL)
                .order_by(PlayerRow.name)
            )
            .scalars()
            .all()
        )

        results: list[FailureRate] = []
        for player in trial_players:
            rate = self.get_failure_rate(player.player_id, lookback_weeks)
            if rate.failures >= 1:
                results.append(rate)

        return results
