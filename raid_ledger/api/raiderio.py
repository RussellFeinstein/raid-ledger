"""Raider.io enrichment — lightweight client for ilvl and M+ score.

Used as an optional second pass after wowaudit batch collection.
Only fetches the fields wowaudit doesn't provide: item level and M+ score.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://raider.io/api/v1"
_FIELDS = "gear,mythic_plus_scores_by_season:current"


async def fetch_ilvl_and_score(
    name: str,
    realm: str,
    region: str = "us",
    *,
    timeout: int = 10,
) -> tuple[float | None, float | None]:
    """Fetch a character's equipped ilvl and M+ score from Raider.io.

    Returns:
        Tuple of (item_level, raiderio_score). Either may be None on error.
    """
    url = f"{_BASE_URL}/characters/profile"
    params = {
        "region": region,
        "realm": realm,
        "name": name,
        "fields": _FIELDS,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params)

            if response.status_code != 200:
                logger.debug(
                    "Raider.io %d for %s-%s", response.status_code, name, realm,
                )
                return None, None

            data = response.json()

            # ilvl
            gear = data.get("gear") or {}
            ilvl_raw = gear.get("item_level_equipped")
            ilvl = float(ilvl_raw) if ilvl_raw is not None else None

            # M+ score
            score: float | None = None
            seasons = data.get("mythic_plus_scores_by_season") or []
            if seasons:
                score_raw = seasons[0].get("scores", {}).get("all")
                score = float(score_raw) if score_raw is not None else None

            return ilvl, score

    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("Raider.io enrichment failed for %s-%s: %s", name, realm, exc)
        return None, None


async def enrich_roster(
    players: list[dict],
    region: str = "us",
    delay: float = 0.1,
    timeout: int = 10,
) -> dict[int, tuple[float | None, float | None]]:
    """Fetch ilvl and score for a list of players from Raider.io.

    Args:
        players: List of dicts with keys 'player_id', 'name', 'realm'.
        region: WoW region (default "us").
        delay: Seconds between requests to avoid rate limits.
        timeout: HTTP timeout per request.

    Returns:
        Dict mapping player_id to (item_level, raiderio_score).
    """
    results: dict[int, tuple[float | None, float | None]] = {}

    for p in players:
        ilvl, score = await fetch_ilvl_and_score(
            p["name"], p["realm"], region, timeout=timeout,
        )
        results[p["player_id"]] = (ilvl, score)

        if delay > 0 and p != players[-1]:
            await asyncio.sleep(delay)

    return results
