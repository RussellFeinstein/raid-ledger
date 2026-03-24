"""Tests for the Raider.io API client — mocked HTTP via respx."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from raid_ledger.api.raiderio import (
    CharacterData,
    CharacterNotFoundError,
    GuildNotFoundError,
    ParseError,
    RaiderioClient,
    RateLimitError,
)
from raid_ledger.config import CollectionConfig, RaiderioConfig

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://raider.io/api/v1"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_client(mock_client: httpx.AsyncClient) -> RaiderioClient:
    return RaiderioClient(
        raiderio_config=RaiderioConfig(base_url=BASE_URL),
        collection_config=CollectionConfig(max_retries=3, timeout_seconds=5),
        http_client=mock_client,
    )


# ---------------------------------------------------------------------------
# Character endpoint
# ---------------------------------------------------------------------------


class TestFetchCharacter:
    @respx.mock
    @pytest.mark.anyio
    async def test_successful_parse(self):
        fixture = _load_fixture("raiderio_character.json")
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            result = await client.fetch_character("us", "tichondrius", "Testchar")

        assert result.name == "Testchar"
        assert result.realm == "Tichondrius"
        assert result.class_name == "Death Knight"
        assert result.spec_name == "Frost"
        assert result.item_level == 619.5
        assert result.raiderio_score == 2450.0
        assert result.mplus_runs_total == 10
        assert result.highest_key_level == 13
        assert result.count_runs_at_level(10) == 10
        assert result.count_runs_at_level(11) == 6
        assert result.count_runs_at_level(12) == 3
        assert result.count_runs_at_level(13) == 1
        assert result.raw_json  # non-empty

    @respx.mock
    @pytest.mark.anyio
    async def test_zero_runs(self):
        fixture = _load_fixture("raiderio_character_empty.json")
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            result = await client.fetch_character("us", "tichondrius", "Altchar")

        assert result.mplus_runs_total == 0
        assert result.highest_key_level is None
        assert result.count_runs_at_level(10) == 0
        assert result.item_level == 580.0

    @respx.mock
    @pytest.mark.anyio
    async def test_missing_gear_field(self):
        fixture = _load_fixture("raiderio_character_partial.json")
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            result = await client.fetch_character("us", "tichondrius", "Partialchar")

        assert result.item_level is None
        assert result.mplus_runs_total == 1

    @respx.mock
    @pytest.mark.anyio
    async def test_missing_score_field(self):
        fixture = _load_fixture("raiderio_character_partial.json")
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            result = await client.fetch_character("us", "tichondrius", "Partialchar")

        assert result.raiderio_score is None

    @respx.mock
    @pytest.mark.anyio
    async def test_404_raises_character_not_found(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(404, json={"error": "Not Found"})
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(CharacterNotFoundError):
                await client.fetch_character("us", "tichondrius", "Nobody")

    @respx.mock
    @pytest.mark.anyio
    async def test_429_retries_then_succeeds(self):
        fixture = _load_fixture("raiderio_character.json")
        route = respx.get(f"{BASE_URL}/characters/profile")
        route.side_effect = [
            httpx.Response(429, text="Rate limited"),
            httpx.Response(200, json=fixture),
        ]

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            result = await client.fetch_character("us", "tichondrius", "Testchar")

        assert result.name == "Testchar"
        assert route.call_count == 2

    @respx.mock
    @pytest.mark.anyio
    async def test_429_retries_exhausted(self):
        """All retries get 429 — raises RateLimitError, not TimeoutException."""
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(429, text="Rate limited")
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(RateLimitError):
                await client.fetch_character("us", "tichondrius", "Testchar")

    @respx.mock
    @pytest.mark.anyio
    async def test_timeout_retries_exhausted(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(httpx.TimeoutException):
                await client.fetch_character("us", "tichondrius", "Testchar")

    @respx.mock
    @pytest.mark.anyio
    async def test_malformed_json_raises_parse_error(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(200, text="not json at all {{{")
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(ParseError):
                await client.fetch_character("us", "tichondrius", "Testchar")


# ---------------------------------------------------------------------------
# Guild endpoint
# ---------------------------------------------------------------------------


class TestFetchGuildMembers:
    @respx.mock
    @pytest.mark.anyio
    async def test_successful_parse(self):
        fixture = _load_fixture("raiderio_guild.json")
        respx.get(f"{BASE_URL}/guilds/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            members = await client.fetch_guild_members("us", "tichondrius", "Test Guild")

        assert len(members) == 14
        gm = members[0]
        assert gm.name == "Guildmaster"
        assert gm.class_name == "Paladin"
        assert gm.role == "healer"
        assert gm.rank == 0

    @respx.mock
    @pytest.mark.anyio
    async def test_role_mapping(self):
        fixture = _load_fixture("raiderio_guild.json")
        respx.get(f"{BASE_URL}/guilds/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            members = await client.fetch_guild_members("us", "tichondrius", "Test Guild")

        roles = {m.name: m.role for m in members}
        assert roles["Guildmaster"] == "healer"  # HEALING -> healer
        assert roles["OfficerOne"] == "tank"     # TANK -> tank
        assert roles["Raider1"] == "dps"         # DPS -> dps

    @respx.mock
    @pytest.mark.anyio
    async def test_404_raises_guild_not_found(self):
        respx.get(f"{BASE_URL}/guilds/profile").mock(
            return_value=httpx.Response(404, json={"error": "Not Found"})
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(GuildNotFoundError):
                await client.fetch_guild_members("us", "tichondrius", "Nonexistent")

    @respx.mock
    @pytest.mark.anyio
    async def test_all_ranks_preserved(self):
        fixture = _load_fixture("raiderio_guild.json")
        respx.get(f"{BASE_URL}/guilds/profile").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            members = await client.fetch_guild_members("us", "tichondrius", "Test Guild")

        ranks = {m.name: m.rank for m in members}
        assert ranks["Guildmaster"] == 0
        assert ranks["OfficerOne"] == 1
        assert ranks["Raider1"] == 2
        assert ranks["Trial1"] == 3
        assert ranks["Social1"] == 4
        assert ranks["Alt1"] == 5


# ---------------------------------------------------------------------------
# CharacterData helpers
# ---------------------------------------------------------------------------


class TestCharacterData:
    def test_count_runs_at_level(self):
        data = CharacterData(
            name="Test", realm="Test", region="us",
            class_name="Mage", spec_name="Fire", item_level=600.0,
            mplus_runs=[
                {"mythic_level": 10},
                {"mythic_level": 12},
                {"mythic_level": 8},
                {"mythic_level": 15},
            ],
        )
        assert data.count_runs_at_level(10) == 3
        assert data.count_runs_at_level(12) == 2
        assert data.count_runs_at_level(15) == 1
        assert data.count_runs_at_level(20) == 0

    def test_highest_key_level(self):
        data = CharacterData(
            name="Test", realm="Test", region="us",
            class_name="Mage", spec_name="Fire", item_level=600.0,
            mplus_runs=[
                {"mythic_level": 10},
                {"mythic_level": 15},
                {"mythic_level": 8},
            ],
        )
        assert data.highest_key_level == 15

    def test_empty_runs(self):
        data = CharacterData(
            name="Test", realm="Test", region="us",
            class_name="Mage", spec_name="Fire", item_level=600.0,
        )
        assert data.mplus_runs_total == 0
        assert data.highest_key_level is None
        assert data.count_runs_at_level(10) == 0
