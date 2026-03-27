"""Application configuration — Pydantic Settings loaded from TOML + env vars."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.toml"


class GuildConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = ""
    region: str = "us"
    realm: str = ""


class CollectionConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    hours_after_reset: int = 3
    request_delay_seconds: float = 0.5
    max_retries: int = 3
    timeout_seconds: int = 10
    collect_statuses: list[str] = ["core", "trial"]


class BenchmarkConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    default_min_mplus_runs: int = 8
    default_min_key_level: int = 10
    default_min_vault_slots: int = 3


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    chronic_fail_threshold: int = 3
    chronic_lookback_weeks: int = 5
    streak_warning_threshold: int = 2


class WowauditConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str = "https://wowaudit.com"
    api_key: str = ""


class AppConfig(BaseSettings):
    """Root configuration — merges TOML defaults with environment overrides."""

    model_config = ConfigDict(frozen=True)

    guild: GuildConfig = GuildConfig()
    collection: CollectionConfig = CollectionConfig()
    benchmarks: BenchmarkConfig = BenchmarkConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    wowaudit: WowauditConfig = WowauditConfig()
    wowaudit_api_key: str = ""
    database_url: str = "sqlite:///raid_ledger.db"


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load configuration from TOML file, with environment variable overrides.

    Priority: env vars > TOML file > Pydantic defaults.

    The ``WOWAUDIT_API_KEY`` env var is injected into the nested
    ``wowaudit`` config automatically by pydantic-settings.
    """
    path = config_path or _DEFAULT_CONFIG_PATH
    toml_data: dict = {}

    if path.exists():
        with open(path, "rb") as f:
            toml_data = tomllib.load(f)

    config = AppConfig(**toml_data)

    # Inject top-level api_key into nested wowaudit config if set
    if config.wowaudit_api_key and not config.wowaudit.api_key:
        wowaudit_data = config.wowaudit.model_dump()
        wowaudit_data["api_key"] = config.wowaudit_api_key
        config = config.model_copy(update={"wowaudit": WowauditConfig(**wowaudit_data)})

    return config
