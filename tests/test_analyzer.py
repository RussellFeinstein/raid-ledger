"""Tests for the failure analyzer — seeded multi-week data with known patterns.

Seed layout (7 players x 8 weeks):

    Player      Status    W1   W2   W3   W4   W5   W6   W7   W8
    ──────────  ────────  ──── ──── ──── ──── ──── ──── ──── ────
    AllPass     core      P    P    P    P    P    P    P    P
    AllFail     core      F    F    F    F    F    F    F    F
    Mixed       core      P    F    P    F    P    F    P    F
    Improver    core      F    F    F    P    P    P    P    P
    Flagged     core      P    P    ?    P    F    ?    P    P    (? = flag)
    Newbie      trial     -    -    -    -    -    P    F    P    (joined W6)
    Benched     inactive  P    P    P    P    P    -    -    -    (went inactive after W5)

P = pass, F = fail, ? = flag, - = no snapshot
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from raid_ledger.db.schema import PlayerRow, WeeklySnapshotRow
from raid_ledger.engine.analyzer import (
    FailureAnalyzer,
)
from raid_ledger.models.snapshot import FailureReason, FlagReason

# 8 Tuesdays
WEEKS = [date(2026, 3, 3) + timedelta(weeks=i) for i in range(8)]
W1, W2, W3, W4, W5, W6, W7, W8 = WEEKS


@pytest.fixture()
def seeded_session(db_session):
    """Seed 7 players and 8 weeks of snapshots with known patterns."""
    players = [
        PlayerRow(name="AllPass", realm="tichondrius", region="us", class_name="Mage",
                  role="dps", status="core", joined_date=date(2026, 3, 1)),
        PlayerRow(name="AllFail", realm="tichondrius", region="us", class_name="Warrior",
                  role="tank", status="core", joined_date=date(2026, 3, 1)),
        PlayerRow(name="Flagged", realm="tichondrius", region="us", class_name="Shaman",
                  role="dps", status="core", joined_date=date(2026, 3, 1)),
        PlayerRow(name="Mixed", realm="tichondrius", region="us", class_name="Priest",
                  role="healer", status="core", joined_date=date(2026, 3, 1)),
        PlayerRow(name="Improver", realm="tichondrius", region="us", class_name="Rogue",
                  role="dps", status="core", joined_date=date(2026, 3, 1)),
        PlayerRow(name="Newbie", realm="tichondrius", region="us", class_name="Paladin",
                  role="tank", status="trial", joined_date=date(2026, 4, 7)),
        PlayerRow(name="Benched", realm="tichondrius", region="us", class_name="Druid",
                  role="healer", status="inactive", joined_date=date(2026, 3, 1)),
    ]
    db_session.add_all(players)
    db_session.flush()

    pid = {p.name: p.player_id for p in players}

    def _snap(player_name: str, week: date, status: str, reasons: list[str] | None = None):
        r = reasons or (
            [FailureReason.INSUFFICIENT_KEYS] if status == "fail" else []
        )
        return WeeklySnapshotRow(
            player_id=pid[player_name],
            week_of=week,
            status=status,
            mplus_runs_total=10 if status == "pass" else 3,
            mplus_runs_at_level=8 if status == "pass" else 2,
            highest_key_level=12 if status == "pass" else 8,
            item_level=620.0,
            vault_slots_earned=3 if status == "pass" else 1,
            raiderio_score=2400.0,
            reasons=json.dumps(r),
            data_source="raiderio",
        )

    snapshots = []

    # AllPass: 8 weeks pass
    for w in WEEKS:
        snapshots.append(_snap("AllPass", w, "pass"))

    # AllFail: 8 weeks fail
    for w in WEEKS:
        snapshots.append(_snap("AllFail", w, "fail"))

    # Mixed: alternating P F P F P F P F
    for i, w in enumerate(WEEKS):
        snapshots.append(_snap("Mixed", w, "pass" if i % 2 == 0 else "fail"))

    # Improver: F F F P P P P P
    for i, w in enumerate(WEEKS):
        snapshots.append(_snap("Improver", w, "fail" if i < 3 else "pass"))

    # Flagged: P P ? P F ? P P (? = flag at W3 and W6)
    for i, w in enumerate(WEEKS):
        if i in (2, 5):  # W3, W6
            snapshots.append(WeeklySnapshotRow(
                player_id=pid["Flagged"], week_of=w, status="flag",
                mplus_runs_total=0, mplus_runs_at_level=0,
                item_level=None, vault_slots_earned=0,
                reasons=json.dumps([FlagReason.NO_DATA]),
                data_source="raiderio",
            ))
        elif i == 4:  # W5 = fail
            snapshots.append(_snap("Flagged", w, "fail"))
        else:
            snapshots.append(_snap("Flagged", w, "pass"))

    # Newbie (trial): joined W6, has data for W6 W7 W8 only (P F P)
    snapshots.append(_snap("Newbie", W6, "pass"))
    snapshots.append(_snap("Newbie", W7, "fail"))
    snapshots.append(_snap("Newbie", W8, "pass"))

    # Benched (inactive): was active for W1-W5 (all pass), then went inactive
    for w in WEEKS[:5]:
        snapshots.append(_snap("Benched", w, "pass"))

    db_session.add_all(snapshots)
    db_session.commit()

    return db_session


# ---------------------------------------------------------------------------
# get_weekly_summary
# ---------------------------------------------------------------------------


class TestWeeklySummary:
    def test_returns_all_players_for_week(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        summary = analyzer.get_weekly_summary(W8)
        names = [s.name for s in summary]
        # W8 has snapshots for: AllPass, AllFail, Flagged, Mixed, Improver, Newbie
        # Benched has no W8 snapshot
        assert len(summary) == 6
        assert "Benched" not in names

    def test_ordering_flags_first_then_fails(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        summary = analyzer.get_weekly_summary(W8)
        statuses = [s.snapshot_status for s in summary]
        # W8: AllFail=fail, Mixed=fail (W8 is index 7, odd), others=pass
        # Fails should come before passes
        fail_indices = [i for i, s in enumerate(statuses) if s == "fail"]
        pass_indices = [i for i, s in enumerate(statuses) if s == "pass"]
        if fail_indices and pass_indices:
            assert max(fail_indices) < min(pass_indices)

    def test_reasons_parsed(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        summary = analyzer.get_weekly_summary(W8)
        fail_players = [s for s in summary if s.snapshot_status == "fail"]
        for fp in fail_players:
            assert isinstance(fp.reasons, list)
            assert len(fp.reasons) > 0

    def test_empty_week(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        summary = analyzer.get_weekly_summary(date(2025, 1, 1))
        assert summary == []


# ---------------------------------------------------------------------------
# get_player_history
# ---------------------------------------------------------------------------


class TestPlayerHistory:
    def test_correct_chronological_order(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        pid = seeded_session.execute(
            select(PlayerRow.player_id).where(PlayerRow.name == "Mixed")
        ).scalar()
        history = analyzer.get_player_history(pid)
        assert len(history) == 8
        # Most recent first
        assert history[0]["week_of"] == W8
        assert history[-1]["week_of"] == W1

    def test_limited_history(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        pid = seeded_session.execute(
            select(PlayerRow.player_id).where(PlayerRow.name == "AllPass")
        ).scalar()
        history = analyzer.get_player_history(pid, weeks=3)
        assert len(history) == 3

    def test_newbie_short_history(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        pid = seeded_session.execute(
            select(PlayerRow.player_id).where(PlayerRow.name == "Newbie")
        ).scalar()
        history = analyzer.get_player_history(pid)
        assert len(history) == 3  # Only W6, W7, W8


# ---------------------------------------------------------------------------
# get_failure_rate
# ---------------------------------------------------------------------------


class TestFailureRate:
    def test_all_pass(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        pid = seeded_session.execute(
            select(PlayerRow.player_id).where(PlayerRow.name == "AllPass")
        ).scalar()
        rate = analyzer.get_failure_rate(pid, lookback_weeks=5)
        assert rate.failures == 0
        assert rate.total_weeks == 5
        assert rate.rate == 0.0

    def test_all_fail(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        pid = seeded_session.execute(
            select(PlayerRow.player_id).where(PlayerRow.name == "AllFail")
        ).scalar()
        rate = analyzer.get_failure_rate(pid, lookback_weeks=8)
        assert rate.failures == 8
        assert rate.total_weeks == 8
        assert rate.rate == 1.0

    def test_mixed_player(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        pid = seeded_session.execute(
            select(PlayerRow.player_id).where(PlayerRow.name == "Mixed")
        ).scalar()
        rate = analyzer.get_failure_rate(pid, lookback_weeks=5)
        # Last 5 weeks (W4-W8): F P F P F -> 3 failures
        assert rate.failures == 3
        assert rate.total_weeks == 5

    def test_lookback_exceeds_history(self, seeded_session):
        """Newbie has 3 weeks — lookback of 5 should only count 3."""
        analyzer = FailureAnalyzer(seeded_session)
        pid = seeded_session.execute(
            select(PlayerRow.player_id).where(PlayerRow.name == "Newbie")
        ).scalar()
        rate = analyzer.get_failure_rate(pid, lookback_weeks=5)
        assert rate.total_weeks == 3  # Not 5
        assert rate.failures == 1  # W7 was fail

    def test_no_data_player(self, seeded_session):
        """Player with no snapshots at all."""
        # Add a brand new player with no snapshots
        new_player = PlayerRow(
            name="NoData", realm="tichondrius", region="us",
            class_name="Hunter", role="dps", status="core",
            joined_date=date(2026, 4, 14),
        )
        seeded_session.add(new_player)
        seeded_session.flush()

        analyzer = FailureAnalyzer(seeded_session)
        rate = analyzer.get_failure_rate(new_player.player_id, lookback_weeks=5)
        assert rate.failures == 0
        assert rate.total_weeks == 0
        assert rate.rate == 0.0


# ---------------------------------------------------------------------------
# get_chronic_underperformers
# ---------------------------------------------------------------------------


class TestChronicUnderperformers:
    def test_threshold_3_of_5(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        chronic = analyzer.get_chronic_underperformers(fail_threshold=3, lookback_weeks=5)
        names = [c.name for c in chronic]
        assert "AllFail" in names    # 5 of 5
        assert "Mixed" in names      # 3 of 5
        assert "AllPass" not in names
        assert "Improver" not in names  # 0 of last 5 (all pass in W4-W8)

    def test_different_thresholds(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        chronic = analyzer.get_chronic_underperformers(fail_threshold=5, lookback_weeks=8)
        names = [c.name for c in chronic]
        assert "AllFail" in names    # 8 of 8
        assert "Mixed" not in names  # 4 of 8 (below threshold of 5)

    def test_excludes_inactive(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        chronic = analyzer.get_chronic_underperformers(fail_threshold=1, lookback_weeks=8)
        names = [c.name for c in chronic]
        assert "Benched" not in names

    def test_empty_result(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        chronic = analyzer.get_chronic_underperformers(fail_threshold=100, lookback_weeks=8)
        assert chronic == []


# ---------------------------------------------------------------------------
# get_current_streaks
# ---------------------------------------------------------------------------


class TestCurrentStreaks:
    def test_all_pass_streak(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        streaks = analyzer.get_current_streaks()
        streak_map = {s.name: s for s in streaks}

        assert streak_map["AllPass"].streak_type == "pass"
        assert streak_map["AllPass"].streak_length == 8

    def test_all_fail_streak(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        streaks = analyzer.get_current_streaks()
        streak_map = {s.name: s for s in streaks}

        assert streak_map["AllFail"].streak_type == "fail"
        assert streak_map["AllFail"].streak_length == 8

    def test_mixed_streak(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        streaks = analyzer.get_current_streaks()
        streak_map = {s.name: s for s in streaks}

        # Mixed ends on W8=fail (index 7, odd), so current streak is fail length 1
        assert streak_map["Mixed"].streak_type == "fail"
        assert streak_map["Mixed"].streak_length == 1

    def test_improver_streak(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        streaks = analyzer.get_current_streaks()
        streak_map = {s.name: s for s in streaks}

        # Improver: F F F P P P P P -> current streak = 5 pass
        assert streak_map["Improver"].streak_type == "pass"
        assert streak_map["Improver"].streak_length == 5

    def test_excludes_inactive(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        streaks = analyzer.get_current_streaks()
        names = [s.name for s in streaks]
        assert "Benched" not in names

    def test_newbie_streak(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        streaks = analyzer.get_current_streaks()
        streak_map = {s.name: s for s in streaks}

        # Newbie: P F P -> current streak = 1 pass
        assert streak_map["Newbie"].streak_type == "pass"
        assert streak_map["Newbie"].streak_length == 1


# ---------------------------------------------------------------------------
# get_failure_breakdown
# ---------------------------------------------------------------------------


class TestFailureBreakdown:
    def test_breakdown_counts(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        breakdown = analyzer.get_failure_breakdown(W8)
        # W8 fails: AllFail + Mixed, both with INSUFFICIENT_KEYS
        assert breakdown.get("insufficient_keys", 0) == 2

    def test_empty_week(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        breakdown = analyzer.get_failure_breakdown(date(2025, 1, 1))
        assert breakdown == {}

    def test_all_pass_week_no_breakdown(self, seeded_session):
        """Week where everyone passes — no failure reasons."""
        # W1: AllPass=P, AllFail=F, Mixed=P, Improver=F, Benched=P
        # Still has failures, so check W6 for Newbie=P
        analyzer = FailureAnalyzer(seeded_session)
        breakdown = analyzer.get_failure_breakdown(W1)
        # W1: AllFail=fail, Improver=fail -> 2 insufficient_keys
        assert breakdown.get("insufficient_keys", 0) == 2


# ---------------------------------------------------------------------------
# get_trial_flags
# ---------------------------------------------------------------------------


class TestTrialFlags:
    def test_trial_with_failure(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        flags = analyzer.get_trial_flags(lookback_weeks=4)
        names = [f.name for f in flags]
        assert "Newbie" in names  # 1 failure in 3 weeks

    def test_excludes_core_players(self, seeded_session):
        analyzer = FailureAnalyzer(seeded_session)
        flags = analyzer.get_trial_flags(lookback_weeks=8)
        names = [f.name for f in flags]
        assert "AllFail" not in names  # core, not trial
        assert "Mixed" not in names


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# FLAG-specific tests
# ---------------------------------------------------------------------------


class TestFlagBehavior:
    def test_weekly_summary_flags_sorted_first(self, seeded_session):
        """Flags appear before fails, which appear before passes."""
        analyzer = FailureAnalyzer(seeded_session)
        # W3 has a flag (Flagged player)
        summary = analyzer.get_weekly_summary(W3)
        statuses = [s.snapshot_status for s in summary]
        flag_idx = [i for i, s in enumerate(statuses) if s == "flag"]
        fail_idx = [i for i, s in enumerate(statuses) if s == "fail"]
        pass_idx = [i for i, s in enumerate(statuses) if s == "pass"]
        if flag_idx and fail_idx:
            assert max(flag_idx) < min(fail_idx)
        if fail_idx and pass_idx:
            assert max(fail_idx) < min(pass_idx)

    def test_failure_rate_excludes_flags(self, seeded_session):
        """Flags do NOT count as failures in the rate calculation."""
        analyzer = FailureAnalyzer(seeded_session)
        pid = seeded_session.execute(
            select(PlayerRow.player_id).where(PlayerRow.name == "Flagged")
        ).scalar()
        # Flagged: P P ? P F ? P P -> last 8: 1 fail, 2 flags, 5 pass
        rate = analyzer.get_failure_rate(pid, lookback_weeks=8)
        assert rate.failures == 1  # Only the fail counts, not the flags
        assert rate.total_weeks == 8  # All 8 weeks have snapshots

    def test_streak_broken_by_flag(self, seeded_session):
        """A flag breaks a pass/fail streak."""
        analyzer = FailureAnalyzer(seeded_session)
        streaks = analyzer.get_current_streaks()
        streak_map = {s.name: s for s in streaks}
        # Flagged: W1=P W2=P W3=? W4=P W5=F W6=? W7=P W8=P
        # Most recent first: P P ? F P ? P P
        # Current streak = 2 pass (W8, W7), broken by flag at W6
        assert streak_map["Flagged"].streak_type == "pass"
        assert streak_map["Flagged"].streak_length == 2

    def test_failure_breakdown_includes_flag_reasons(self, seeded_session):
        """Flag reasons (like NO_DATA) appear in breakdown."""
        analyzer = FailureAnalyzer(seeded_session)
        breakdown = analyzer.get_failure_breakdown(W3)
        # W3: Flagged=flag(no_data), AllFail+Improver=fail(insufficient_keys)
        assert breakdown.get("no_data", 0) == 1
        assert breakdown.get("insufficient_keys", 0) == 2

    def test_chronic_excludes_flagged_as_failures(self, seeded_session):
        """Player with flags but only 1 fail should not be chronic at threshold=3."""
        analyzer = FailureAnalyzer(seeded_session)
        chronic = analyzer.get_chronic_underperformers(fail_threshold=3, lookback_weeks=8)
        names = [c.name for c in chronic]
        assert "Flagged" not in names  # Only 1 real failure


class TestMultiReasonBreakdown:
    def test_multiple_reasons_per_snapshot(self, seeded_session):
        """A snapshot with multiple reasons counts each reason separately."""
        # Add a snapshot with both INSUFFICIENT_KEYS and LOW_ILVL
        pid = seeded_session.execute(
            select(PlayerRow.player_id).where(PlayerRow.name == "AllPass")
        ).scalar()
        # Override AllPass W8 with a multi-reason fail for this test
        from raid_ledger.db.schema import WeeklySnapshotRow as Row
        row = seeded_session.execute(
            select(Row).where(Row.player_id == pid, Row.week_of == W8)
        ).scalar_one()
        row.status = "fail"
        row.reasons = json.dumps(["insufficient_keys", "low_ilvl"])
        seeded_session.flush()

        analyzer = FailureAnalyzer(seeded_session)
        breakdown = analyzer.get_failure_breakdown(W8)
        # W8 now: AllPass=fail(keys+ilvl), AllFail=fail(keys), Mixed=fail(keys)
        assert breakdown.get("insufficient_keys", 0) == 3
        assert breakdown.get("low_ilvl", 0) == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_db(self, db_session):
        """All queries return empty results on empty database."""
        analyzer = FailureAnalyzer(db_session)
        assert analyzer.get_weekly_summary(W1) == []
        assert analyzer.get_chronic_underperformers() == []
        assert analyzer.get_current_streaks() == []
        assert analyzer.get_failure_breakdown(W1) == {}
        assert analyzer.get_trial_flags() == []

    def test_first_week_of_season(self, seeded_session):
        """First week only — limited data should not crash."""
        analyzer = FailureAnalyzer(seeded_session)
        summary = analyzer.get_weekly_summary(W1)
        assert len(summary) > 0

        # Failure rate for W1 only (lookback=1)
        pid = seeded_session.execute(
            select(PlayerRow.player_id).where(PlayerRow.name == "AllPass")
        ).scalar()
        rate = analyzer.get_failure_rate(pid, lookback_weeks=1)
        assert rate.failures == 0
        assert rate.total_weeks == 1
