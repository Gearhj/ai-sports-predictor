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

    # Abuse controls. In-process only (non-distributed); a multi-instance public
    # deployment must move these counters to a shared store behind a trusted
    # proxy. See app/core/rate_limit.py and the deep security audit, section 11.
    rate_limit_enabled: bool = True
    auth_rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)
    admin_rate_limit_per_minute: int = Field(default=60, ge=1, le=5000)
    rate_limit_trust_forwarded_for: bool = False

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
    # A daily-board selection may only be labeled QUALIFIED when the forecast's
    # readiness clears this bar (above the publish floor). Below it a positive
    # edge is surfaced as WATCH, never as an actionable bet.
    opportunity_qualified_readiness: float = Field(default=70.0, ge=0, le=100)
    # A model–market disagreement (no-vig edge, as a probability) at/above this
    # bar is flagged EXTREME_DISAGREEMENT — REVIEW REQUIRED, never QUALIFIED,
    # until confirmed inputs, a fresh two-sided quote, and a successful rescore.
    opportunity_extreme_disagreement_threshold: float = Field(default=0.15, ge=0, le=1)

    # Live prospective shadow operation (armed provider polling; off by default so
    # the process never makes an unattended provider call until explicitly armed).
    live_shadow_enabled: bool = False
    availability_poll_slow_minutes: int = Field(default=10, ge=1)
    availability_poll_fast_minutes: int = Field(default=2, ge=1)
    pregame_fast_window_hours: int = Field(default=4, ge=1, le=24)
    odds_pregame_refresh_minutes: int = Field(default=5, ge=1)
    # Scheduled odds refreshes only spend quota when a monitored game is within
    # this many hours of first pitch, concentrating the daily budget on the
    # pregame window instead of exhausting it overnight. Event-triggered and
    # pipeline refreshes are not gated by this window.
    odds_pregame_window_hours: int = Field(default=4, ge=1, le=24)
    shadow_quote_burst_offsets_seconds: str = "15,60,300"

    # SportsDataIO availability adapter — the single injury/availability provider
    # for this phase. Disabled unless explicitly configured; the API key is read
    # from the environment (SPORTSDATAIO_API_KEY) and never committed or exposed.
    # Deferred for public use: the adapter and configuration are retained, but the
    # feed is disabled by default (the paid data is trial/scrambled and therefore
    # non-authoritative for local operation). Set SPORTSDATAIO_ENABLED=true to
    # activate it in an environment with a production key. Disabled or absent →
    # INJURY_FEED_NOT_CONFIGURED, the feed never polls, and the MANUAL_ADMIN /
    # free-source availability paths remain the only authority — nothing fabricated.
    sportsdataio_enabled: bool = False
    sportsdataio_api_key: str = ""
    sportsdataio_base_url: str = "https://api.sportsdata.io/v3/mlb"

    # Model Studio forecast-exchange shadow board (research-shadow, never executable).
    shadow_board_enabled: bool = True
    shadow_bundle_import_dir: str = ""
    shadow_virtual_bankroll: float = Field(default=1000.0, gt=0)
    shadow_qualified_edge: float = Field(default=0.02, ge=0, le=1)
    shadow_kelly_fraction: float = Field(default=0.25, gt=0, le=1)
    shadow_unit_percentage: float = Field(default=0.005, gt=0, le=0.1)
    shadow_max_position_percentage: float = Field(default=0.01, gt=0, le=0.1)
    shadow_max_same_game_percentage: float = Field(default=0.02, gt=0, le=0.25)
    shadow_max_daily_percentage: float = Field(default=0.05, gt=0, le=0.5)

    scheduler_enabled: bool = False
    event_relative_scheduler_enabled: bool = False
    raw_data_root: Path = ASP_ROOT / "data" / "raw"
    curated_data_root: Path = ASP_ROOT / "data" / "curated"
    feature_data_root: Path = ASP_ROOT / "data" / "features"
    gate3_artifact_root: Path = ASP_ROOT / ".runtime" / "gate3"
    gate3_min_training_rows: int = Field(default=200, ge=20)
    gate3_min_holdout_rows: int = Field(default=50, ge=10)
    gate3_promotion_min_holdout_rows: int = Field(default=200, ge=50)
    gate3_holdout_fraction: float = Field(default=0.20, gt=0, lt=0.5)
    gate3_rolling_folds: int = Field(default=3, ge=2, le=10)
    gate3_random_seed: int = 20260813
    gate3_bootstrap_iterations: int = Field(default=1000, ge=100, le=10000)
    gate3_feature_min_coverage: float = Field(default=0.70, ge=0, le=1)
    gate3_calibration_ece_threshold: float = Field(default=0.05, ge=0, le=0.25)
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
            # CORS must fail closed in production: no wildcard (especially with
            # credentials enabled) and no plaintext-HTTP external origins.
            for origin in self.cors_origin_list:
                if "*" in origin:
                    raise ValueError("Production CORS_ORIGINS must not contain a wildcard origin")
                if not origin.startswith("https://"):
                    raise ValueError("Production CORS_ORIGINS entries must be explicit https:// origins")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def odds_market_list(self) -> list[str]:
        return [market.strip() for market in self.odds_markets.split(",") if market.strip()]

    @property
    def quote_burst_offset_list(self) -> list[int]:
        offsets: list[int] = []
        for token in self.shadow_quote_burst_offsets_seconds.split(","):
            token = token.strip()
            if token:
                try:
                    offsets.append(int(token))
                except ValueError:
                    continue
        return offsets


@lru_cache
def get_settings() -> Settings:
    return Settings()
