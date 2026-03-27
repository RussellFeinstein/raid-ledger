"""Weekly snapshot model and status/reason enums."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SnapshotStatus(StrEnum):
    """Three-state verdict for a weekly snapshot.

    PASS — met all requirements.
    FAIL — played but didn't meet requirements.
    FLAG — needs officer attention (not a failure, but not confirmable as a pass).
    """

    PASS = "pass"
    FAIL = "fail"
    FLAG = "flag"


class FailureReason(StrEnum):
    """Why a snapshot was marked as FAIL."""

    INSUFFICIENT_KEYS = "insufficient_keys"
    LOW_ILVL = "low_ilvl"
    MANUAL_FAIL = "manual_fail"


class FlagReason(StrEnum):
    """Why a snapshot was marked as FLAG."""

    NO_DATA = "no_data"
    DATA_ANOMALY = "data_anomaly"
    APPROVED_ABSENCE = "approved_absence"
    MANUAL_FLAG = "manual_flag"


class WeeklySnapshot(BaseModel):
    """One player's performance record for one reset week.

    The combination (player_id, week_of) is unique — one record per player per week.
    week_of is always a Tuesday (WoW US reset day).
    """

    model_config = ConfigDict(frozen=True)

    snapshot_id: int | None = None
    player_id: int
    week_of: date
    mplus_runs_total: int = 0
    mplus_runs_at_level: int = 0
    highest_key_level: int | None = None
    item_level: float | None = None
    vault_slots_earned: int = 0
    raiderio_score: float | None = None
    status: SnapshotStatus
    reasons: list[str] = []
    override_by: str | None = None
    data_source: str = "wowaudit"
    raw_api_response: str | None = None
    collected_at: datetime | None = None
