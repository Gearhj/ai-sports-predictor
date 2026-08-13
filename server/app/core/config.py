from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ASP_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime settings loaded from the project ``.env`` and process environment.

    PostgreSQL is deliberately the default. Automated tests explicitly override
    ``database_url`` with an isolated SQLite database.
    """

    model_config = SettingsConfigDict(
        env_file=str(ASP_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AI Sports Predictor API"
    app_env: Literal["development", "test", "production"] = "development"
    api_v1_prefix: str = "/api/v1"
    display_timezone: str = "America/New_York"
    database_url: str = "postgresql+psycopg://asp:REDACTED-CREDENTIAL@localhost:5433/asp"
    secret_key: str = "change-me-before-any-shared-deployment"
    access_token_minutes: int = Field(default=60, ge=5, le=1440)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    the_odds_api_key: str = ""
    odds_polling_enabled: bool = False
    odds_poll_interval_minutes: int = Field(default=60, ge=5)
    odds_markets: str = "h2h,spreads,totals"
    odds_regions: str = "us"
    odds_manual_cooldown_minutes: int = Field(default=5, ge=1)
    odds_quota_floor: int = Field(default=10, ge=0)
    odds_daily_request_budget: int = Field(default=1, ge=0, le=100)
    open_meteo_enabled: bool = True
    statcast_enabled: bool = False
    demo_mode: bool = True
    enable_arbitrage_analytics: bool = False
    official_positions_enabled: bool = False
    official_max_stake_percentage: float = Field(default=0.025, gt=0, le=0.10)

    simulation_draws: int = Field(default=20_000, ge=100, le=1_000_000)
    simulation_seed: int = 20250601
    forecast_min_readiness: float = Field(default=55.0, ge=0, le=100)
    ensemble_baseline_weight: float = Field(default=0.30, ge=0, le=1)
    ensemble_run_weight: float = Field(default=0.45, ge=0, le=1)
    ensemble_boosting_weight: float = Field(default=0.25, ge=0, le=1)
    opportunity_watch_threshold: float = Field(default=0.02, ge=0, le=1)
    opportunity_standard_threshold: float = Field(default=0.04, ge=0, le=1)
    opportunity_strong_threshold: float = Field(default=0.07, ge=0, le=1)

    scheduler_enabled: bool = False
    event_relative_scheduler_enabled: bool = False
    raw_data_root: Path = ASP_ROOT / "data" / "raw"
    curated_data_root: Path = ASP_ROOT / "data" / "curated"
    feature_data_root: Path = ASP_ROOT / "data" / "features"
    admin_email: str = "admin@example.com"
    admin_password: str = "REDACTED-CREDENTIAL"

    @field_validator("the_odds_api_key", mode="before")
    @classmethod
    def normalize_optional_key(cls, value: object) -> str:
        return "" if value is None else str(value).strip()

    @model_validator(mode="after")
    def production_guards(self) -> "Settings":
        if self.app_env == "production":
            if self.secret_key == "change-me-before-any-shared-deployment" or len(self.secret_key) < 32:
                raise ValueError("Production SECRET_KEY must be a unique value of at least 32 characters")
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("Production DATABASE_URL must use PostgreSQL")
            if self.demo_mode:
                raise ValueError("DEMO_MODE must be false in production")
            if self.admin_password == "REDACTED-CREDENTIAL" or len(self.admin_password) < 12:
                raise ValueError("Production ADMIN_PASSWORD must be a unique value of at least 12 characters")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def odds_market_list(self) -> list[str]:
        return [market.strip() for market in self.odds_markets.split(",") if market.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
