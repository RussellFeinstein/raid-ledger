"""Raider.io HTTP client — character profiles and guild roster import.

Two endpoints:
  - Character profile: M+ runs, ilvl, score for weekly collection
  - Guild profile: full member list for roster import

Rate limiting: configurable delay between calls, exponential backoff on 429.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

from raid_ledger.config import CollectionConfig, RaiderioConfig

logger = logging.getLogger(__name__)

_CHARACTER_FIELDS = (
    "mythic_plus_previous_weekly_highest_level_runs,"
    "gear,"
    "mythic_plus_scores_by_season:current"
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CharacterNotFoundError(Exception):
    """Raider.io returned 404 for a character lookup."""


class GuildNotFoundError(Exception):
    """Raider.io returned 404 for a guild lookup."""


class ParseError(Exception):
    """Response body could not be parsed as valid JSON."""


class RateLimitError(Exception):
    """All retries exhausted due to 429 rate limiting."""


# ---------------------------------------------------------------------------
# Data classes returned by the client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CharacterData:
    """Parsed character data from a Raider.io profile response."""

    name: str
    realm: str
    region: str
    class_name: str
    spec_name: str | None
    item_level: float | None
    mplus_runs: list[dict] = field(default_factory=list)
    raiderio_score: float | None = None
    raw_json: str = ""

    def count_runs_at_level(self, min_key_level: int) -> int:
        """Count M+ runs at or above the given key level."""
        return sum(
            1 for run in self.mplus_runs
            if run.get("mythic_level", 0) >= min_key_level
        )

    @property
    def mplus_runs_total(self) -> int:
        return len(self.mplus_runs)

    @property
    def highest_key_level(self) -> int | None:
        if not self.mplus_runs:
            return None
        return max(run.get("mythic_level", 0) for run in self.mplus_runs)


@dataclass(frozen=True)
class GuildMember:
    """A single member from a Raider.io guild profile response."""

    name: str
    realm: str
    class_name: str
    spec_name: str | None
    role: str
    rank: int


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class RaiderioClient:
    """Typed httpx client for the Raider.io API.

    Args:
        raiderio_config: API base URL configuration.
        collection_config: Timeout, retry, and delay settings.
        http_client: Optional pre-configured httpx.AsyncClient (for testing).
    """

    def __init__(
        self,
        raiderio_config: RaiderioConfig | None = None,
        collection_config: CollectionConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._rio = raiderio_config or RaiderioConfig()
        self._col = collection_config or CollectionConfig()
        self._external_client = http_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        return httpx.AsyncClient(timeout=self._col.timeout_seconds)

    async def _request_with_retry(self, url: str, params: dict) -> dict:
        """GET with exponential backoff on 429 and retries on timeout."""
        client = await self._get_client()
        owns_client = self._external_client is None

        try:
            last_exc: Exception | None = None
            rate_limited = False
            for attempt in range(self._col.max_retries):
                try:
                    response = await client.get(url, params=params)

                    if response.status_code == 429:
                        rate_limited = True
                        wait = 2 ** attempt
                        logger.warning("Rate limited (429), retrying in %ds", wait)
                        await asyncio.sleep(wait)
                        continue

                    if response.status_code == 404:
                        return {"_status": 404, "_url": str(response.url)}

                    response.raise_for_status()

                    try:
                        return response.json()
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise ParseError(
                            f"Malformed JSON from {response.url}"
                        ) from exc

                except httpx.TimeoutException as exc:
                    last_exc = exc
                    logger.warning(
                        "Timeout on attempt %d/%d for %s",
                        attempt + 1, self._col.max_retries, url,
                    )
                    continue

            if last_exc is not None:
                raise last_exc
            if rate_limited:
                raise RateLimitError(
                    f"Rate limited (429) on all {self._col.max_retries} attempts for {url}"
                )
            raise httpx.TimeoutException(
                f"All {self._col.max_retries} retries exhausted for {url}"
            )
        finally:
            if owns_client:
                await client.aclose()

    # ----- Character endpoint -----

    async def fetch_character(
        self, region: str, realm: str, name: str
    ) -> CharacterData:
        """Fetch a character's weekly M+ data, ilvl, and score.

        Uses ``mythic_plus_previous_weekly_highest_level_runs`` — the
        finalized data from the week that ended at the most recent reset.

        Raises:
            CharacterNotFoundError: Character does not exist (404).
            ParseError: Response body is not valid JSON.
            httpx.TimeoutException: All retries exhausted.
        """
        url = f"{self._rio.base_url}/characters/profile"
        params = {
            "region": region,
            "realm": realm,
            "name": name,
            "fields": _CHARACTER_FIELDS,
        }

        data = await self._request_with_retry(url, params)

        if data.get("_status") == 404:
            raise CharacterNotFoundError(
                f"Character not found: {name}-{realm} ({region})"
            )

        return self._parse_character(data)

    def _parse_character(self, data: dict) -> CharacterData:
        gear = data.get("gear") or {}
        ilvl = gear.get("item_level_equipped")

        runs = data.get("mythic_plus_previous_weekly_highest_level_runs") or []

        score: float | None = None
        seasons = data.get("mythic_plus_scores_by_season") or []
        if seasons:
            score = seasons[0].get("scores", {}).get("all")

        return CharacterData(
            name=data.get("name", ""),
            realm=data.get("realm", ""),
            region=data.get("region", ""),
            class_name=data.get("class", ""),
            spec_name=data.get("active_spec_name"),
            item_level=float(ilvl) if ilvl is not None else None,
            mplus_runs=runs,
            raiderio_score=float(score) if score is not None else None,
            raw_json=json.dumps(data),
        )

    # ----- Guild endpoint -----

    async def fetch_guild_members(
        self, region: str, realm: str, guild_name: str
    ) -> list[GuildMember]:
        """Fetch all members of a guild for roster import.

        Raises:
            GuildNotFoundError: Guild does not exist (404).
            ParseError: Response body is not valid JSON.
            httpx.TimeoutException: All retries exhausted.
        """
        url = f"{self._rio.base_url}/guilds/profile"
        params = {
            "region": region,
            "realm": realm,
            "name": guild_name,
            "fields": "members",
        }

        data = await self._request_with_retry(url, params)

        if data.get("_status") == 404:
            raise GuildNotFoundError(
                f"Guild not found: {guild_name} on {realm} ({region})"
            )

        return self._parse_guild_members(data)

    def _parse_guild_members(self, data: dict) -> list[GuildMember]:
        members_raw = data.get("members") or []
        members: list[GuildMember] = []

        for entry in members_raw:
            char = entry.get("character", {})
            role_raw = (char.get("role") or "dps").lower()
            if role_raw == "healing":
                role = "healer"
            elif role_raw in ("tank", "dps"):
                role = role_raw
            else:
                logger.warning(
                    "Unknown role '%s' for %s, defaulting to dps",
                    role_raw, char.get("name"),
                )
                role = "dps"

            members.append(GuildMember(
                name=char.get("name", ""),
                realm=char.get("realm", ""),
                class_name=char.get("class", ""),
                spec_name=char.get("active_spec_name"),
                role=role,
                rank=entry.get("rank", 0),
            ))

        return members
