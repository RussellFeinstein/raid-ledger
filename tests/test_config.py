"""Tests for configuration loading."""

from __future__ import annotations

from raid_ledger.config import AppConfig, load_config


class TestLoadConfig:
    def test_loads_default_toml(self):
        config = load_config()
        assert config.guild.region == "us"
        assert config.collection.hours_after_reset == 3
        assert config.collection.request_delay_seconds == 0.5
        assert config.collection.max_retries == 3
        assert config.benchmarks.default_min_mplus_runs == 8
        assert config.benchmarks.default_min_key_level == 10
        assert config.analysis.chronic_fail_threshold == 3
        assert config.analysis.chronic_lookback_weeks == 5
        assert config.wowaudit.base_url == "https://wowaudit.com"

    def test_missing_config_uses_defaults(self, tmp_path):
        config = load_config(config_path=tmp_path / "nonexistent.toml")
        assert config.guild.region == "us"
        assert config.benchmarks.default_min_mplus_runs == 8

    def test_database_url_default(self):
        config = AppConfig()
        assert config.database_url == "sqlite:///raid_ledger.db"

    def test_database_url_from_env(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
        config = AppConfig()
        assert config.database_url == "postgresql://user:pass@host/db"

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("WOWAUDIT_API_KEY", "test-secret-key")
        config = load_config()
        assert config.wowaudit.api_key == "test-secret-key"

    def test_collect_statuses_from_toml(self):
        config = load_config()
        assert config.collection.collect_statuses == ["core", "trial"]

    def test_config_is_frozen(self):
        config = load_config()
        try:
            config.guild = None
            raised = False
        except (AttributeError, TypeError, Exception):
            raised = True
        assert raised
