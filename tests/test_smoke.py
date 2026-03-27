"""Smoke tests — all modules import without error."""

from __future__ import annotations


class TestRaidLedgerImports:
    def test_models(self):
        from raid_ledger.models import (  # noqa: F401
            FailureReason,
            FlagReason,
            Player,
            PlayerStatus,
            SnapshotStatus,
            WeeklyBenchmark,
            WeeklySnapshot,
        )

    def test_config(self):
        from raid_ledger.config import AppConfig, load_config  # noqa: F401

    def test_db(self):
        from raid_ledger.db import get_engine, get_session_factory, init_db  # noqa: F401
        from raid_ledger.db.repositories import (  # noqa: F401
            BenchmarkRepo,
            CollectionRunRepo,
            NoteRepo,
            PlayerRepo,
            SettingsRepo,
            SnapshotRepo,
        )

    def test_api(self):
        from raid_ledger.api import (  # noqa: F401
            ParseError,
            RateLimitError,
            WowauditApiError,
            WowauditAuthError,
            WowauditCharacter,
            WowauditClient,
            WowauditRosterMember,
        )

    def test_engine(self):
        from raid_ledger.engine import (  # noqa: F401
            EvaluationResult,
            FailureAnalyzer,
            derive_vault_slots,
            evaluate,
        )
        from raid_ledger.engine.collector import NoBenchmarkError, WeeklyCollector  # noqa: F401


class TestDashboardImports:
    def test_auth(self):
        from dashboard.auth import check_password  # noqa: F401

    def test_data_loader(self):
        from dashboard.data_loader import (  # noqa: F401
            get_active_players,
            get_all_players,
            get_chronic_underperformers,
            get_collected_weeks,
            get_current_streaks,
            get_failure_breakdown,
            get_failure_rate,
            get_player_history,
            get_player_notes,
            get_weekly_summary,
        )

    def test_components(self):
        from dashboard.components.filters import apply_filters  # noqa: F401
        from dashboard.components.status_badge import (  # noqa: F401
            reason_display,
            status_color,
            status_icon,
            status_label,
        )
