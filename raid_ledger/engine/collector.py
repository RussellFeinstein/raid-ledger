"""Weekly collection orchestrator.

Loads the active roster, fetches each player from Raider.io, evaluates
against the weekly benchmark, upserts snapshots, and logs the collection run.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from raid_ledger.api.raiderio import (
    CharacterData,
    CharacterNotFoundError,
    RaiderioClient,
)
from raid_ledger.config import AppConfig
from raid_ledger.db.repositories import (
    BenchmarkRepo,
    CollectionRunRepo,
    PlayerRepo,
    SnapshotRepo,
)
from raid_ledger.engine.rules import EvaluationResult, derive_vault_slots, evaluate
from raid_ledger.models.benchmark import WeeklyBenchmark
from raid_ledger.models.player import Player
from raid_ledger.models.snapshot import FlagReason, SnapshotStatus, WeeklySnapshot

logger = logging.getLogger(__name__)


class NoBenchmarkError(Exception):
    """No benchmark has ever been set — collection cannot proceed."""


@dataclass
class CollectionResult:
    """Summary of a collection run."""

    week_of: date
    players_collected: int = 0
    api_errors: int = 0
    status: str = "completed"
    errors: list[str] = field(default_factory=list)


class WeeklyCollector:
    """Orchestrates weekly data collection for all active players."""

    def __init__(
        self,
        session: Session,
        client: RaiderioClient,
        config: AppConfig,
    ) -> None:
        self._session = session
        self._client = client
        self._config = config

    async def collect(self, week_of: date) -> CollectionResult:
        """Run collection for the given reset week.

        Steps:
            1. Load active roster (core + trial)
            2. Load or copy-forward the benchmark for this week
            3. For each player: fetch, evaluate, upsert snapshot
            4. Log collection_run metadata

        Raises:
            NoBenchmarkError: No benchmark has ever been set.
        """
        player_repo = PlayerRepo(self._session)
        snapshot_repo = SnapshotRepo(self._session)
        benchmark_repo = BenchmarkRepo(self._session)
        run_repo = CollectionRunRepo(self._session)

        # Start collection run
        run_id = run_repo.create(week_of)
        self._session.commit()

        result = CollectionResult(week_of=week_of)

        # Load roster
        roster = player_repo.get_active()
        if not roster:
            logger.info("Empty roster — no players to collect")
            run_repo.update(run_id, status="completed", players_collected=0, completed=True)
            self._session.commit()
            return result

        # Load or copy-forward benchmark
        benchmark = benchmark_repo.get_by_week(week_of)
        if benchmark is None:
            most_recent = benchmark_repo.get_most_recent()
            if most_recent is None:
                run_repo.update(
                    run_id,
                    status="failed",
                    error_log="No benchmark has ever been set",
                    completed=True,
                )
                self._session.commit()
                raise NoBenchmarkError(
                    "Set weekly benchmarks before collecting."
                )
            # Copy forward
            benchmark = benchmark_repo.create_or_update(WeeklyBenchmark(
                week_of=week_of,
                min_mplus_runs=most_recent.min_mplus_runs,
                min_key_level=most_recent.min_key_level,
                min_ilvl=most_recent.min_ilvl,
                min_vault_slots=most_recent.min_vault_slots,
                set_by="system (copied forward)",
                set_at=datetime.now(tz=UTC),
            ))
            self._session.commit()
            logger.warning(
                "No benchmark for %s — copied from %s. Officers should review.",
                week_of, most_recent.week_of,
            )

        # Collect each player
        delay = self._config.collection.request_delay_seconds
        for player in roster:
            try:
                char_data = await self._fetch_player(player)
                eval_result = evaluate(char_data, benchmark)
                self._upsert_snapshot(
                    snapshot_repo, player, week_of, char_data, eval_result, benchmark,
                )
                result.players_collected += 1
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                logger.error("Failed to collect %s: %s", player.name, exc)
                result.api_errors += 1
                result.errors.append(f"{player.name}: {exc}")
                # Flag the player as NO_DATA
                self._upsert_flag_snapshot(
                    snapshot_repo, player, week_of, str(exc),
                )
            self._session.commit()

            # Courtesy delay between API calls
            if delay > 0:
                await asyncio.sleep(delay)

        # Finalize collection run
        if result.api_errors > 0 and result.players_collected == 0:
            result.status = "failed"
        elif result.api_errors > 0:
            result.status = "partial"

        run_repo.update(
            run_id,
            status=result.status,
            players_collected=result.players_collected,
            api_errors=result.api_errors,
            error_log="\n".join(result.errors) if result.errors else None,
            completed=True,
        )
        self._session.commit()

        logger.info(
            "Collection complete: %d collected, %d errors, status=%s",
            result.players_collected, result.api_errors, result.status,
        )
        return result

    async def _fetch_player(self, player: Player) -> CharacterData | None:
        """Fetch a single player's data from Raider.io."""
        try:
            return await self._client.fetch_character(
                player.region, player.realm, player.name,
            )
        except CharacterNotFoundError:
            logger.warning("Character not found: %s-%s", player.name, player.realm)
            return None

    def _upsert_snapshot(
        self,
        repo: SnapshotRepo,
        player: Player,
        week_of: date,
        char_data: CharacterData | None,
        eval_result: EvaluationResult,
        benchmark: WeeklyBenchmark,
    ) -> None:
        runs_at_level = (
            char_data.count_runs_at_level(benchmark.min_key_level) if char_data else 0
        )
        snapshot = WeeklySnapshot(
            player_id=player.player_id,
            week_of=week_of,
            mplus_runs_total=char_data.mplus_runs_total if char_data else 0,
            mplus_runs_at_level=runs_at_level,
            highest_key_level=char_data.highest_key_level if char_data else None,
            item_level=char_data.item_level if char_data else None,
            vault_slots_earned=derive_vault_slots(runs_at_level),
            raiderio_score=char_data.raiderio_score if char_data else None,
            status=eval_result.status,
            reasons=eval_result.reasons,
            data_source="raiderio",
            raw_api_response=char_data.raw_json if char_data else None,
        )
        repo.upsert(snapshot)

    def _upsert_flag_snapshot(
        self,
        repo: SnapshotRepo,
        player: Player,
        week_of: date,
        error_msg: str,
    ) -> None:
        snapshot = WeeklySnapshot(
            player_id=player.player_id,
            week_of=week_of,
            status=SnapshotStatus.FLAG,
            reasons=[FlagReason.NO_DATA],
            data_source="raiderio",
            raw_api_response=json.dumps({"error": error_msg}),
        )
        repo.upsert(snapshot)
