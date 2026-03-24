"""Entry point for weekly collection — used by GitHub Actions cron and manual runs.

Usage:
    python scripts/collect_weekly.py [--week YYYY-MM-DD]

If --week is omitted, uses the most recent Tuesday (current or past).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta

from raid_ledger.api.raiderio import RaiderioClient
from raid_ledger.config import load_config
from raid_ledger.db.connection import get_engine, get_session_factory, init_db
from raid_ledger.engine.collector import NoBenchmarkError, WeeklyCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _most_recent_tuesday(today: date | None = None) -> date:
    """Return the most recent Tuesday (including today if it's Tuesday)."""
    d = today or date.today()
    days_since_tuesday = (d.weekday() - 1) % 7
    return d - timedelta(days=days_since_tuesday)


async def _run(week_of: date) -> None:
    config = load_config()
    engine = get_engine(config.database_url)
    init_db(engine)

    session_factory = get_session_factory(engine)
    session = session_factory()

    client = RaiderioClient(
        raiderio_config=config.raiderio,
        collection_config=config.collection,
    )

    collector = WeeklyCollector(session, client, config)

    try:
        result = await collector.collect(week_of)
        logger.info(
            "Result: %d collected, %d errors (%s)",
            result.players_collected, result.api_errors, result.status,
        )
        if result.status == "failed":
            sys.exit(1)
    except NoBenchmarkError as exc:
        logger.error(str(exc))
        sys.exit(1)
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run weekly M+ data collection")
    parser.add_argument(
        "--week",
        type=date.fromisoformat,
        default=None,
        help="Reset week date (YYYY-MM-DD). Defaults to most recent Tuesday.",
    )
    args = parser.parse_args()

    week_of = args.week or _most_recent_tuesday()
    logger.info("Collecting for week of %s", week_of)

    asyncio.run(_run(week_of))


if __name__ == "__main__":
    main()
