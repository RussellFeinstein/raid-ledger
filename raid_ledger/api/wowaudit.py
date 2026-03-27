"""Wowaudit HTTP client — batch roster and weekly M+ data.

Three endpoints used:
  - /v1/characters: team roster with class, role, rank
  - /v1/historical_data: batch weekly M+ data for all characters (current period)
  - /v1/period: current period number and season metadata

Auth: Bearer token or api_key query parameter.
See docs/wowaudit-api.md for full API reference.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

from raid_ledger.config import CollectionConfig, WowauditConfig

logger = logging.getLogger(__name__)

_ROLE_MAP: dict[str, str] = {
    "tank": "tank",
    "heal": "healer",
    "melee": "dps",
    "ranged": "dps",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WowauditAuthError(Exception):
    """Wowaudit returned 401/403 — invalid or missing API key."""


class WowauditApiError(Exception):
    """Wowaudit returned a non-200 response."""


class ParseError(Exception):
    """Response body could not be parsed as valid JSON."""


class RateLimitError(Exception):
    """All retries exhausted due to 429 rate limiting."""


# ---------------------------------------------------------------------------
# Data classes returned by the client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WowauditCharacter:
    """One character's weekly M+ data from the wowaudit historical_data endpoint.

    Duck-types with the old CharacterData interface so the rules engine
    can accept either via union type.
    """

    wowaudit_id: int
    name: str
    realm: str
    dungeons_done: list[dict] = field(default_factory=list)
    vault_options: dict = field(default_factory=dict)
    world_quests_done: int = 0
    regular_mythic_dungeons_done: int = 0
    raw_json: str = ""

    # Not available from the batch endpoint — always None
    item_level: float | None = None

    def count_runs_at_level(self, min_key_level: int) -> int:
        """Count keystones completed at or above the given key level."""
        return sum(
            1 for run in self.dungeons_done
            if run.get("level", 0) >= min_key_level
        )

    @property
    def mplus_runs_total(self) -> int:
        """Total keystones completed this period."""
        return len(self.dungeons_done)

    @property
    def highest_key_level(self) -> int | None:
        """Highest keystone level completed, or None if no runs."""
        if not self.dungeons_done:
            return None
        return max(run.get("level", 0) for run in self.dungeons_done)

    def vault_dungeon_slots(self) -> int:
        """Count non-null dungeon vault options (0-3)."""
        opts = self.vault_options.get("dungeons", {})
        return sum(
            1 for k in ("option_1", "option_2", "option_3")
            if opts.get(k) is not None
        )


@dataclass(frozen=True)
class WowauditRosterMember:
    """A character from the /v1/characters roster endpoint."""

    wowaudit_id: int
    name: str
    realm: str
    class_name: str
    role: str           # normalized to tank/healer/dps
    rank: str
    status: str
    blizzard_id: int | None = None
    tracking_since: str | None = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class WowauditClient:
    """Typed httpx client for the wowaudit API.

    Args:
        wowaudit_config: API base URL and key configuration.
        collection_config: Timeout and retry settings.
        http_client: Optional pre-configured httpx.AsyncClient (for testing).
    """

    def __init__(
        self,
        wowaudit_config: WowauditConfig | None = None,
        collection_config: CollectionConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cfg = wowaudit_config or WowauditConfig()
        self._col = collection_config or CollectionConfig()
        self._external_client = http_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        return httpx.AsyncClient(timeout=self._col.timeout_seconds)

    async def _request_with_retry(self, url: str) -> dict | list:
        """GET with auth header, exponential backoff on 429, timeout retries."""
        client = await self._get_client()
        owns_client = self._external_client is None
        headers = {}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"

        try:
            last_exc: Exception | None = None
            rate_limited = False
            for attempt in range(self._col.max_retries):
                try:
                    response = await client.get(url, headers=headers)

                    if response.status_code in (401, 403):
                        raise WowauditAuthError(
                            f"Auth failed ({response.status_code}) for {url}"
                        )

                    if response.status_code == 429:
                        rate_limited = True
                        wait = 2 ** attempt
                        logger.warning("Rate limited (429), retrying in %ds", wait)
                        await asyncio.sleep(wait)
                        continue

                    if response.status_code >= 400:
                        raise WowauditApiError(
                            f"HTTP {response.status_code} from {url}"
                        )

                    try:
                        return response.json()
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise ParseError(
                            f"Malformed JSON from {url}"
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

    # ----- Roster endpoint -----

    async def fetch_roster(self) -> list[WowauditRosterMember]:
        """Fetch the full team roster.

        Raises:
            WowauditAuthError: Invalid or missing API key.
            ParseError: Response body is not valid JSON.
            httpx.TimeoutException: All retries exhausted.
        """
        url = f"{self._cfg.base_url}/v1/characters"
        data = await self._request_with_retry(url)

        members: list[WowauditRosterMember] = []
        for entry in data:
            role_raw = (entry.get("role") or "").lower()
            role = _ROLE_MAP.get(role_raw, "dps")
            if role_raw not in _ROLE_MAP:
                logger.warning(
                    "Unknown role '%s' for %s, defaulting to dps",
                    entry.get("role"), entry.get("name"),
                )

            members.append(WowauditRosterMember(
                wowaudit_id=entry["id"],
                name=entry.get("name", ""),
                realm=entry.get("realm", ""),
                class_name=entry.get("class", ""),
                role=role,
                rank=entry.get("rank", ""),
                status=entry.get("status", ""),
                blizzard_id=entry.get("blizzard_id"),
                tracking_since=entry.get("tracking_since"),
            ))

        return members

    # ----- Period endpoint -----

    async def fetch_period(self) -> dict:
        """Fetch current period number and season metadata.

        Raises:
            WowauditAuthError: Invalid or missing API key.
            ParseError: Response body is not valid JSON.
            httpx.TimeoutException: All retries exhausted.
        """
        url = f"{self._cfg.base_url}/v1/period"
        return await self._request_with_retry(url)

    # ----- Historical data (batch) endpoint -----

    async def fetch_historical_data(self) -> tuple[int, dict[int, WowauditCharacter]]:
        """Fetch all characters' weekly M+ data for the current period.

        Returns:
            Tuple of (period_number, dict mapping wowaudit_id to WowauditCharacter).
            Characters whose ``data`` is null are excluded from the dict.

        Raises:
            WowauditAuthError: Invalid or missing API key.
            ParseError: Response body is not valid JSON.
            httpx.TimeoutException: All retries exhausted.
        """
        url = f"{self._cfg.base_url}/v1/historical_data"
        raw = await self._request_with_retry(url)

        period = raw.get("period", 0)
        characters: dict[int, WowauditCharacter] = {}

        for entry in raw.get("characters", []):
            data = entry.get("data")
            if data is None:
                continue

            characters[entry["id"]] = WowauditCharacter(
                wowaudit_id=entry["id"],
                name=entry.get("name", ""),
                realm=entry.get("realm", ""),
                dungeons_done=data.get("dungeons_done") or [],
                vault_options=data.get("vault_options") or {},
                world_quests_done=data.get("world_quests_done", 0),
                regular_mythic_dungeons_done=data.get("regular_mythic_dungeons_done", 0),
                raw_json=json.dumps(entry),
            )

        return period, characters
