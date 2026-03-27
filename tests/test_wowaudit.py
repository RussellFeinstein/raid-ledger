"""Tests for the wowaudit API client — mocked HTTP via respx."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from raid_ledger.api.wowaudit import (
    ParseError,
    RateLimitError,
    WowauditApiError,
    WowauditAuthError,
    WowauditCharacter,
    WowauditClient,
)
from raid_ledger.config import CollectionConfig, WowauditConfig

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://wowaudit.com"


def _load_fixture(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text())


def _make_client(mock_client: httpx.AsyncClient) -> WowauditClient:
    return WowauditClient(
        wowaudit_config=WowauditConfig(base_url=BASE_URL, api_key="test-key"),
        collection_config=CollectionConfig(max_retries=3, timeout_seconds=5),
        http_client=mock_client,
    )


# ---------------------------------------------------------------------------
# Roster endpoint
# ---------------------------------------------------------------------------


class TestFetchRoster:
    @respx.mock
    @pytest.mark.anyio
    async def test_successful_parse(self):
        fixture = _load_fixture("wowaudit_characters.json")
        respx.get(f"{BASE_URL}/v1/characters").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            members = await client.fetch_roster()

        assert len(members) == 4
        assert members[0].name == "Testplayer"
        assert members[0].realm == "Tichondrius"
        assert members[0].class_name == "Warlock"
        assert members[0].wowaudit_id == 100
        assert members[0].blizzard_id == 12345678

    @respx.mock
    @pytest.mark.anyio
    async def test_role_mapping(self):
        fixture = _load_fixture("wowaudit_characters.json")
        respx.get(f"{BASE_URL}/v1/characters").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            members = await client.fetch_roster()

        roles = {m.name: m.role for m in members}
        assert roles["Testplayer"] == "dps"    # Ranged -> dps
        assert roles["Tankyboy"] == "tank"     # Tank -> tank
        assert roles["Healsworth"] == "healer"  # Heal -> healer
        assert roles["Meleeguy"] == "dps"      # Melee -> dps

    @respx.mock
    @pytest.mark.anyio
    async def test_auth_error(self):
        respx.get(f"{BASE_URL}/v1/characters").mock(
            return_value=httpx.Response(401, json={"error": "Unauthorized"})
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(WowauditAuthError):
                await client.fetch_roster()

    @respx.mock
    @pytest.mark.anyio
    async def test_403_raises_auth_error(self):
        respx.get(f"{BASE_URL}/v1/characters").mock(
            return_value=httpx.Response(403, json={"error": "Forbidden"})
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(WowauditAuthError):
                await client.fetch_roster()


# ---------------------------------------------------------------------------
# Historical data (batch) endpoint
# ---------------------------------------------------------------------------


class TestFetchHistoricalData:
    @respx.mock
    @pytest.mark.anyio
    async def test_successful_parse(self):
        fixture = _load_fixture("wowaudit_historical_data.json")
        respx.get(f"{BASE_URL}/v1/historical_data").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            period, characters = await client.fetch_historical_data()

        assert period == 1055
        # 4 characters in fixture, but id=103 has data=null → excluded
        assert len(characters) == 3
        assert 100 in characters
        assert 103 not in characters

    @respx.mock
    @pytest.mark.anyio
    async def test_character_data_parsing(self):
        fixture = _load_fixture("wowaudit_historical_data.json")
        respx.get(f"{BASE_URL}/v1/historical_data").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            _, characters = await client.fetch_historical_data()

        char = characters[100]
        assert char.name == "Testplayer"
        assert char.realm == "Tichondrius"
        assert char.mplus_runs_total == 10
        assert char.highest_key_level == 14
        assert char.world_quests_done == 30
        assert char.regular_mythic_dungeons_done == 2
        assert char.raw_json  # non-empty

    @respx.mock
    @pytest.mark.anyio
    async def test_null_data_excluded(self):
        fixture = _load_fixture("wowaudit_historical_data.json")
        respx.get(f"{BASE_URL}/v1/historical_data").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            _, characters = await client.fetch_historical_data()

        # id=103 has data: null
        assert 103 not in characters

    @respx.mock
    @pytest.mark.anyio
    async def test_zero_runs_character(self):
        fixture = _load_fixture("wowaudit_historical_data.json")
        respx.get(f"{BASE_URL}/v1/historical_data").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            _, characters = await client.fetch_historical_data()

        char = characters[101]  # Tankyboy — zero keystones
        assert char.mplus_runs_total == 0
        assert char.highest_key_level is None
        assert char.count_runs_at_level(10) == 0
        assert char.regular_mythic_dungeons_done == 8

    @respx.mock
    @pytest.mark.anyio
    async def test_auth_error(self):
        respx.get(f"{BASE_URL}/v1/historical_data").mock(
            return_value=httpx.Response(401, json={"error": "Unauthorized"})
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(WowauditAuthError):
                await client.fetch_historical_data()

    @respx.mock
    @pytest.mark.anyio
    async def test_server_error(self):
        respx.get(f"{BASE_URL}/v1/historical_data").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(WowauditApiError):
                await client.fetch_historical_data()

    @respx.mock
    @pytest.mark.anyio
    async def test_malformed_json(self):
        respx.get(f"{BASE_URL}/v1/historical_data").mock(
            return_value=httpx.Response(200, text="not json {{{")
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(ParseError):
                await client.fetch_historical_data()

    @respx.mock
    @pytest.mark.anyio
    async def test_429_retries_then_succeeds(self):
        fixture = _load_fixture("wowaudit_historical_data.json")
        route = respx.get(f"{BASE_URL}/v1/historical_data")
        route.side_effect = [
            httpx.Response(429, text="Rate limited"),
            httpx.Response(200, json=fixture),
        ]

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            period, characters = await client.fetch_historical_data()

        assert period == 1055
        assert route.call_count == 2

    @respx.mock
    @pytest.mark.anyio
    async def test_429_retries_exhausted(self):
        respx.get(f"{BASE_URL}/v1/historical_data").mock(
            return_value=httpx.Response(429, text="Rate limited")
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(RateLimitError):
                await client.fetch_historical_data()

    @respx.mock
    @pytest.mark.anyio
    async def test_timeout_retries_exhausted(self):
        respx.get(f"{BASE_URL}/v1/historical_data").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            with pytest.raises(httpx.TimeoutException):
                await client.fetch_historical_data()


# ---------------------------------------------------------------------------
# Period endpoint
# ---------------------------------------------------------------------------


class TestFetchPeriod:
    @respx.mock
    @pytest.mark.anyio
    async def test_successful_parse(self):
        fixture = _load_fixture("wowaudit_period.json")
        respx.get(f"{BASE_URL}/v1/period").mock(
            return_value=httpx.Response(200, json=fixture)
        )

        async with httpx.AsyncClient() as http:
            client = _make_client(http)
            result = await client.fetch_period()

        assert result["current_period"] == 1055
        assert result["current_season"]["expansion"] == "Midnight"


# ---------------------------------------------------------------------------
# WowauditCharacter helpers
# ---------------------------------------------------------------------------


class TestWowauditCharacter:
    def test_count_runs_at_level(self):
        char = WowauditCharacter(
            wowaudit_id=1, name="Test", realm="Test",
            dungeons_done=[
                {"level": 10, "dungeon": 401},
                {"level": 12, "dungeon": 402},
                {"level": 8, "dungeon": 403},
                {"level": 15, "dungeon": 404},
            ],
        )
        assert char.count_runs_at_level(10) == 3
        assert char.count_runs_at_level(12) == 2
        assert char.count_runs_at_level(15) == 1
        assert char.count_runs_at_level(20) == 0

    def test_highest_key_level(self):
        char = WowauditCharacter(
            wowaudit_id=1, name="Test", realm="Test",
            dungeons_done=[
                {"level": 10, "dungeon": 401},
                {"level": 15, "dungeon": 402},
                {"level": 8, "dungeon": 403},
            ],
        )
        assert char.highest_key_level == 15

    def test_empty_runs(self):
        char = WowauditCharacter(
            wowaudit_id=1, name="Test", realm="Test",
        )
        assert char.mplus_runs_total == 0
        assert char.highest_key_level is None
        assert char.count_runs_at_level(10) == 0

    def test_vault_dungeon_slots(self):
        # 3 slots
        char = WowauditCharacter(
            wowaudit_id=1, name="Test", realm="Test",
            vault_options={
                "dungeons": {"option_1": 256, "option_2": 256, "option_3": 256},
            },
        )
        assert char.vault_dungeon_slots() == 3

        # 1 slot
        char2 = WowauditCharacter(
            wowaudit_id=2, name="Test2", realm="Test",
            vault_options={
                "dungeons": {"option_1": 256, "option_2": None, "option_3": None},
            },
        )
        assert char2.vault_dungeon_slots() == 1

        # 0 slots
        char3 = WowauditCharacter(
            wowaudit_id=3, name="Test3", realm="Test",
            vault_options={
                "dungeons": {"option_1": None, "option_2": None, "option_3": None},
            },
        )
        assert char3.vault_dungeon_slots() == 0

    def test_vault_dungeon_slots_missing_key(self):
        char = WowauditCharacter(
            wowaudit_id=1, name="Test", realm="Test",
            vault_options={},
        )
        assert char.vault_dungeon_slots() == 0

    def test_item_level_always_none(self):
        char = WowauditCharacter(
            wowaudit_id=1, name="Test", realm="Test",
        )
        assert char.item_level is None

    def test_auth_header_sent(self):
        """Verify the client sets the Bearer token header."""
        client = WowauditClient(
            wowaudit_config=WowauditConfig(base_url=BASE_URL, api_key="secret"),
        )
        assert client._cfg.api_key == "secret"
