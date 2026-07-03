"""Typed, validated application configuration.

Non-secret tunables come from ``config/config.toml``; secrets (OpenAI API key,
MT5 account credentials) come from environment variables / a ``.env`` file.
Call :func:`load_settings` exactly once at startup and pass the resulting
``Settings`` object explicitly into every module that needs it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

Timeframe = Literal["M5", "M15"]
OrderSide = Literal["BUY", "SELL"]


class TradingSettings(BaseModel):
    """Trading parameters from the ``[trading]`` config.toml section."""

    symbol: str
    timeframes: list[Timeframe]
    risk_usd: float
    reward_usd: float
    lot_min: float
    lot_max: float
    preferred_lot_size: float
    max_opportunities_per_day: int = 1
    deviation_points: int = 20
    magic_number: int = 234_000
    poll_interval_seconds: float = 10.0

    @model_validator(mode="after")
    def check_bounds(self) -> TradingSettings:
        """Reject nonsensical risk/reward/lot configuration early."""
        if self.risk_usd <= 0 or self.reward_usd <= 0:
            raise ValueError("risk_usd and reward_usd must be positive")
        if self.lot_min <= 0 or self.lot_max <= 0:
            raise ValueError("lot_min and lot_max must be positive")
        if self.lot_min > self.lot_max:
            raise ValueError("lot_min must not exceed lot_max")
        if not (self.lot_min <= self.preferred_lot_size <= self.lot_max):
            raise ValueError("preferred_lot_size must be within [lot_min, lot_max]")
        if self.max_opportunities_per_day < 1:
            raise ValueError("max_opportunities_per_day must be at least 1")
        if self.deviation_points <= 0:
            raise ValueError("deviation_points must be positive")
        if not (0 < self.magic_number < 2_147_483_647):
            raise ValueError("magic_number must be a positive 32-bit integer")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        return self


class AISettings(BaseModel):
    """OpenAI parameters from the ``[ai]`` config.toml section."""

    model: str
    temperature: float
    max_retries: int
    timeout_seconds: int
    m5_candle_count: int
    m15_candle_count: int


class DatabaseSettings(BaseModel):
    """SQLite parameters from the ``[database]`` config.toml section."""

    path: Path


class LoggingSettings(BaseModel):
    """Logging parameters from the ``[logging]`` config.toml section."""

    level: str
    dir: Path
    max_bytes: int
    backup_count: int


class AccountCredentials(BaseModel):
    """Login details for one MT5 terminal/account."""

    account_id: int
    side: OrderSide
    login: int
    password: SecretStr
    server: str
    terminal_path: str


class Settings(BaseSettings):
    """Root application configuration.

    Source priority (highest to lowest): constructor kwargs, real environment
    variables, ``.env`` file, ``config/config.toml`` (path overridable via the
    ``CONFIG_PATH`` env var), field defaults.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: SecretStr

    mt5_account_1_side: OrderSide
    mt5_account_1_login: int
    mt5_account_1_password: SecretStr
    mt5_account_1_server: str
    mt5_account_1_terminal_path: str

    mt5_account_2_side: OrderSide
    mt5_account_2_login: int
    mt5_account_2_password: SecretStr
    mt5_account_2_server: str
    mt5_account_2_terminal_path: str

    mt5_account_3_side: OrderSide
    mt5_account_3_login: int
    mt5_account_3_password: SecretStr
    mt5_account_3_server: str
    mt5_account_3_terminal_path: str

    mt5_account_4_side: OrderSide
    mt5_account_4_login: int
    mt5_account_4_password: SecretStr
    mt5_account_4_server: str
    mt5_account_4_terminal_path: str

    trading: TradingSettings
    ai: AISettings
    database: DatabaseSettings
    logging: LoggingSettings

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Layer env vars and .env over config.toml, which sits above defaults."""
        toml_path = Path(os.environ.get("CONFIG_PATH", "config/config.toml"))
        toml_source = TomlConfigSettingsSource(settings_cls, toml_file=toml_path)
        return (init_settings, env_settings, dotenv_settings, toml_source, file_secret_settings)

    @property
    def accounts(self) -> tuple[AccountCredentials, AccountCredentials, AccountCredentials, AccountCredentials]:
        """Assemble the 4 flat env-loaded account fields into typed objects."""
        return (
            AccountCredentials(
                account_id=1,
                side=self.mt5_account_1_side,
                login=self.mt5_account_1_login,
                password=self.mt5_account_1_password,
                server=self.mt5_account_1_server,
                terminal_path=self.mt5_account_1_terminal_path,
            ),
            AccountCredentials(
                account_id=2,
                side=self.mt5_account_2_side,
                login=self.mt5_account_2_login,
                password=self.mt5_account_2_password,
                server=self.mt5_account_2_server,
                terminal_path=self.mt5_account_2_terminal_path,
            ),
            AccountCredentials(
                account_id=3,
                side=self.mt5_account_3_side,
                login=self.mt5_account_3_login,
                password=self.mt5_account_3_password,
                server=self.mt5_account_3_server,
                terminal_path=self.mt5_account_3_terminal_path,
            ),
            AccountCredentials(
                account_id=4,
                side=self.mt5_account_4_side,
                login=self.mt5_account_4_login,
                password=self.mt5_account_4_password,
                server=self.mt5_account_4_server,
                terminal_path=self.mt5_account_4_terminal_path,
            ),
        )


def load_settings() -> Settings:
    """Load and validate application settings from env vars, ``.env``, and ``config.toml``.

    Call this exactly once at startup; pass the returned object explicitly into
    every module that needs configuration rather than re-instantiating it.
    """
    return Settings()  # type: ignore[call-arg]
