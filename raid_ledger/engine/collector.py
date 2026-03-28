"""Weekly collection orchestrator.

Fetches all players' M+ data from wowaudit in a single batch call,
evaluates against the weekly benchmark, upserts snapshots, and logs
the collection run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from raid_ledger.api.raiderio import enrich_roster
from raid_ledger.api.wowaudit import (
    WowauditCharacter,
    WowauditClient,
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
        client: WowauditClient,
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
            3. Batch-fetch all characters from wowaudit
            4. Match characters to roster by name+realm, evaluate, upsert
            5. Log collection_run metadata

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

        # Batch fetch from wowaudit
        try:
            period, char_data = await self._client.fetch_historical_data()
            logger.info("Fetched period %d with %d characters", period, len(char_data))
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.error("Batch fetch failed: %s", exc)
            result.api_errors = len(roster)
            result.status = "failed"
            result.errors.append(f"Batch fetch failed: {exc}")
            # Flag all players as NO_DATA
            for player in roster:
                self._upsert_flag_snapshot(snapshot_repo, player, week_of, str(exc))
            self._session.commit()
            run_repo.update(
                run_id,
                status="failed",
                players_collected=0,
                api_errors=len(roster),
                error_log=str(exc),
                completed=True,
            )
            self._session.commit()
            return result

        # Build name+realm lookup
        char_lookup: dict[tuple[str, str], WowauditCharacter] = {}
        for char in char_data.values():
            key = (char.name.lower(), char.realm.lower())
            char_lookup[key] = char

        # Evaluate each roster player
        for player in roster:
            key = (player.name.lower(), player.realm.lower())
            matched = char_lookup.get(key)

            if matched is None:
                logger.warning(
                    "Player %s-%s not found in wowaudit response",
                    player.name, player.realm,
                )

            eval_result = evaluate(matched, benchmark)
            self._upsert_snapshot(
                snapshot_repo, player, week_of, matched, eval_result, benchmark,
            )
            result.players_collected += 1
            self._session.commit()

        # Raider.io enrichment — fetch ilvl and score if benchmark requires ilvl
        if benchmark.min_ilvl is not None:
            logger.info("Benchmark requires ilvl — enriching from Raider.io")
            player_dicts = [
                {"player_id": p.player_id, "name": p.name, "realm": p.realm}
                for p in roster
            ]
            try:
                enrichment = await enrich_roster(
                    player_dicts,
                    region=self._config.guild.region or "us",
                    delay=self._config.collection.request_delay_seconds,
                    timeout=self._config.collection.timeout_seconds,
                )
                # Re-evaluate with ilvl data and update snapshots
                for player in roster:
                    ilvl, score = enrichment.get(player.player_id, (None, None))
                    snap = snapshot_repo.get_by_player_week(player.player_id, week_of)
                    if snap is None:
                        continue

                    key = (player.name.lower(), player.realm.lower())
                    matched = char_lookup.get(key)

                    # Re-evaluate with ilvl injected
                    enriched = matched
                    if matched is not None and ilvl is not None:
                        enriched = WowauditCharacter(
                            wowaudit_id=matched.wowaudit_id,
                            name=matched.name,
                            realm=matched.realm,
                            dungeons_done=matched.dungeons_done,
                            vault_options=matched.vault_options,
                            world_quests_done=matched.world_quests_done,
                            regular_mythic_dungeons_done=matched.regular_mythic_dungeons_done,
                            raw_json=matched.raw_json,
                            item_level=ilvl,
                        )
                    elif ilvl is not None:
                        enriched = WowauditCharacter(
                            wowaudit_id=0,
                            name=player.name,
                            realm=player.realm,
                            item_level=ilvl,
                        )

                    eval_result = evaluate(enriched, benchmark)
                    self._upsert_snapshot(
                        snapshot_repo, player, week_of, enriched, eval_result,
                        benchmark, ilvl_override=ilvl, score_override=score,
                    )
                self._session.commit()
                logger.info("Raider.io enrichment complete for %d players", len(roster))
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                logger.warning("Raider.io enrichment failed: %s (continuing without ilvl)", exc)

        # Finalize collection run
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

    def _upsert_snapshot(
        self,
        repo: SnapshotRepo,
        player: Player,
        week_of: date,
        char_data: WowauditCharacter | None,
        eval_result: EvaluationResult,
        benchmark: WeeklyBenchmark,
        *,
        ilvl_override: float | None = None,
        score_override: float | None = None,
    ) -> None:
        runs_at_level = (
            char_data.count_runs_at_level(benchmark.min_key_level) if char_data else 0
        )
        # Prefer real vault data from wowaudit, fall back to derived
        vault_slots = (
            char_data.vault_dungeon_slots() if char_data
            else derive_vault_slots(runs_at_level)
        )
        snapshot = WeeklySnapshot(
            player_id=player.player_id,
            week_of=week_of,
            mplus_runs_total=char_data.mplus_runs_total if char_data else 0,
            mplus_runs_at_level=runs_at_level,
            highest_key_level=char_data.highest_key_level if char_data else None,
            item_level=ilvl_override or (char_data.item_level if char_data else None),
            vault_slots_earned=vault_slots,
            raiderio_score=score_override,
            status=eval_result.status,
            reasons=eval_result.reasons,
            data_source="wowaudit",
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
            data_source="wowaudit",
            raw_api_response=json.dumps({"error": error_msg}),
        )
        repo.upsert(snapshot)
