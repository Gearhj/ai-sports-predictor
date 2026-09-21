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
    # No shared credential is shipped. Every non-test installation must supply an
    # explicit DATABASE_URL (environment or .env); startup fails closed without one
    # (see ``require_explicit_credentials``). Tests inject an explicit disposable URL.
    database_url: str = ""
    # Connection pool for PostgreSQL. FastAPI runs sync ``def`` handlers on a
    # 40-slot threadpool; SQLAlchemy's default pool (5 + 10 overflow) is smaller
    # than that, so modest concurrency queued on ``pool_timeout`` (30 s default)
    # instead of failing fast. Defaults below cover the threadpool with margin
    # against PostgreSQL's 100-connection default; tune per replica count.
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=200)
    db_pool_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    # No signing key is shipped: a published default would let anyone mint tokens.
    # Required outside tests (see ``require_explicit_credentials``).
    secret_key: str = ""
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
    # Bounded recovery from a latched quota guard. A LOW reading is respected
    # while it is fresh; once it is older than this it is no longer evidence
    # about the balance now, and exactly one probe may re-read it. See
    # app/jobs/odds_quota_recovery.py. Neither value lowers the floor.
    odds_quota_probe_after_hours: float = Field(default=24.0, ge=1.0)
    odds_quota_probe_cooldown_hours: float = Field(default=24.0, ge=1.0)
    # The provider's own quota period. A latched reading older than this can no
    # longer describe the balance now, so the guard reports that the quota may
    # have renewed instead of implying a permanent block. It authorizes no
    # request by itself and never lowers the floor.
    odds_quota_renewal_days: float = Field(default=30.0, ge=1.0)
    # Model Exchange forecast-bundle consumption. The location is read-only to
    # AISP; automatic consumption stays OFF until it is explicitly enabled, so
    # a scheduler can be wired up without importing anything by surprise.
    # Where the V18 producer publishes its content-addressed slate packages.
    # Empty falls back to a sibling of the bundle directory, which is where it
    # actually publishes today.
    model_exchange_v18_dir: str = ""
    model_exchange_forecast_bundle_dir: str = Field(
        default=str(Path.home() / 'asp-model-exchange' / 'aisp-daily-forecast-bundles')
    )
    model_exchange_auto_import_enabled: bool = False
    # Root of the immutable model exchange the probability adapter re-reads
    # retained paired score populations from (read-only; never written).
    probability_exchange_root: str = Field(default=str(Path.home() / 'asp-model-exchange'))
    # --- AISP DAY-BEFORE SHOW PREVIEW V1 ----------------------------------------
    # A research/editorial Show Board for the day-before 5:00 PM ET recording.
    # All three switches default OFF and fail closed: a missing or unparseable
    # value is False. Even when on, preview provider collection is not
    # implemented in V1 and refuses by name (see app/show_preview/collection.py).
    show_preview_enabled: bool = False
    show_preview_provider_collection_enabled: bool = False
    show_preview_auto_freeze_enabled: bool = False
    #: The show-production clock, local times on the day BEFORE the slate, in
    #: ``show_preview_timezone``. Documentation of intent until the flags above
    #: are enabled by the owner; nothing is registered from them while off.
    show_preview_timezone: str = "America/New_York"
    show_preview_schedule_sync_time: str = Field(default="15:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    show_preview_forecast_import_time: str = Field(default="15:15", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    show_preview_market_collection_time: str = Field(default="16:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    show_preview_build_time: str = Field(default="16:15", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    show_preview_freeze_time: str = Field(default="16:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    show_preview_recording_time: str = Field(default="17:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    #: A preview pair whose OLDER leg is older than this at the board's evidence
    #: time is shown with its capture time but is not used for a comparison.
    show_preview_quote_max_age_hours: float = Field(default=18.0, gt=0, le=96)
    #: The MLB V18 package path has its own switch. The flag above also
    #: turns on the NFL bundle, supplement and lab-study consumers, and
    #: enabling MLB must not silently enable those too.
    mlb_v18_exchange_auto_import_enabled: bool = False
    #: Cadence for the recurring research-artifact consumer once it is enabled.
    model_exchange_import_interval_minutes: int = Field(default=15, ge=5, le=240)
    #: Model Studio NFL overnight model laboratory (machine-readable study).
    model_exchange_lab_study_dir: str = Field(
        default=str(Path.home() / 'asp-model-studio' / 'outputs' / 'nfl-overnight-model-laboratory-v1')
    )
    #: When false, this process registers ONLY the flag-gated research consumer,
    #: so a second process can own that one job without duplicating the core
    #: daily/finalize owners that another supervised process already runs.
    scheduler_core_jobs_enabled: bool = True
    odds_daily_request_budget: int = Field(default=1, ge=0, le=100)
    # --- Event-aware, quota-budgeted collection (AISP_ODDS_CHECKPOINT_V1) -----
    # The provider plan is 500 credits per UTC calendar month and one odds
    # request costs (markets x regions) credits. Blanket polling at 40
    # requests/day x 3 markets burned 120 credits/day and exhausted the month in
    # about four days. These settings bound spend independently of the provider
    # floor, which they never relax.
    odds_checkpoint_collection_enabled: bool = False
    #: WHICH PROCESS MAY SPEND CREDITS.
    #: Every backend on this host reads the same .env, so a flag in .env alone
    #: would arm collection in every one of them -- and this host has repeatedly
    #: had three or four backends listening at once. The owner token is
    #: therefore matched against ASP_ODDS_COLLECTION_OWNER, which is exported by
    #: the supervisor script and by nothing else, so a second process reading
    #: the same .env registers no collection job. Empty on both sides means no
    #: process owns collection and none is scheduled.
    odds_collection_owner: str = ""
    #: The legacy blanket pregame poller. It ran every ten minutes over three
    #: markets, consulted no budget, and consumed 495 of September's 500 credits
    #: in four days. It is off by default and is superseded by the event-aware
    #: collector; the flag exists so disabling it is an explicit, auditable act
    #: rather than a side effect of turning off live shadow operation.
    odds_legacy_pregame_refresh_enabled: bool = False
    odds_monthly_credit_cap: int = Field(default=500, ge=0)
    #: Never spent. Kept back for a manual refresh or an unexpected slate.
    odds_monthly_credit_reserve: int = Field(default=150, ge=0)
    #: Sized from the replayed schedule, not a guess: the worst MLB day needs 4
    #: checkpoints (4 credits) and the worst NFL day 4 (12 credits), so an
    #: overlapping late-September day peaks at 16. 18 leaves headroom for a
    #: doubleheader without letting a runaway spend the month in a day.
    odds_daily_credit_budget: int = Field(default=18, ge=0, le=500)
    #: Comma-separated per-league market overrides, e.g. "MLB=h2h;NFL=h2h,spreads,totals".
    #: Player-prop quote collection (app.player_props.collector). Inactive by
    #: default: a live single-event request needs the flag AND a nonzero
    #: research-lane allocation AND an explicit --execute.
    player_prop_live_enabled: bool = False
    player_prop_research_monthly_credits: int = Field(default=0, ge=0, le=50)
    player_prop_research_daily_credits: int = Field(default=0, ge=0, le=5)
    #: Empty keeps the compiled defaults in app.services.odds_collection_plan.
    odds_market_overrides: str = ""
    # --- Research fallback provider (disabled; no account, no credential) -----
    # Enabling this flag alone does NOT authorise a provider request: the
    # adapter additionally requires a field-mapping manifest bound to a real
    # observed response, which cannot exist until a call is authorised.
    sportsgameodds_enabled: bool = False
    sportsgameodds_api_key: str = ""
    sportsgameodds_base_url: str = ""
    sportsgameodds_mapping_path: str = ""
    #: The last day the research fallback may serve a mandatory MLB moneyline.
    #: It exists so the fallback expires by construction rather than by anyone
    #: remembering: from this date The Odds API is primary again. Empty means no
    #: authorized window at all, not an open one.
    sportsgameodds_fallback_until: str = ""
    #: Whether the MLB fallback names the provider events AISP itself bound to
    #: the selected games (``eventIDs``; see sgo_collection.EVENT_ID_FILTER_PARAM
    #: for the retained provider documentation). OFF: never; an ambiguous
    #: selection is deferred. VALIDATE: IDs for selections the window rule
    #: already makes unambiguous (evidence only); the FIRST ambiguous
    #: all-verified selection is sent by ID once as the owner-authorised
    #: DISCRIMINATING_TEST. If it passes, VALIDATE behaves as ON from then on
    #: (transition recorded once in the ledger); if it fails, VALIDATE stays
    #: deferral-only. ON: whenever every selected game has a verified ID.
    #: Every ID request is checked locally before sending and before ingesting,
    #: and every ID purchase records exact_event_selection evidence.
    sgo_exact_event_selection_mode: Literal["OFF", "VALIDATE", "ON"] = "OFF"
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
    # Frozen NFL M1 prospective track. Import/read jobs are provider-free;
    # Odds API polling is separately armed and remains off by default.
    nfl_shadow_enabled: bool = False
    nfl_shadow_bundle_import_dir: str = ""
    nfl_shadow_odds_enabled: bool = False
    # Automatic NFL final ingestion from the free nflverse schedules release.
    # This is the kill switch: off means completed games stop entering AISP by
    # themselves and prospective scoring waits for an operator again. It spends
    # nothing -- a public GitHub asset, no key, no metered allowance -- so it
    # defaults on, unlike every credit-spending path in this file.
    nfl_final_ingest_enabled: bool = True
    nfl_final_ingest_interval_minutes: int = Field(default=60, ge=15, le=1440)
    # Future Codex NFL M1 production handoff (fail-closed; see
    # app/forecast_exchange/nfl_production_handoff.py). Both empty means "not
    # received": no production label is shown. The SHA-256 is the operator's
    # pin of the exact handoff bytes; a file that does not hash to it is refused.
    nfl_production_handoff_path: str = ""
    nfl_production_handoff_sha256: str = ""
    #: STAGED, NOT ACTIVE: the CFB Gen1 slate-publisher handoff. Empty means
    #: NOT_RECEIVED; a pinned handoff that declares consumerActivated=false stays
    #: STAGED_AWAITING_ACTIVATION, and ACTIVE also needs the consumer switch.
    cfb_production_handoff_path: str = ""
    cfb_production_handoff_sha256: str = ""
    cfb_production_consumer_enabled: bool = False
    #: The discovery pointer the consumer reads; the pinned handoff must bind a
    #: manifest reachable from it (same release lineage).
    cfb_discovery_pointer_path: str = Field(default=str(
        Path.home() / 'asp-model-exchange' / 'cfb-gen1-production-v1' / 'slate-discovery' / 'current.json'))
    #: Independent orientation evidence: a raw CFBD /games capture (native
    #: homeId/awayId). Never the producer's own adapted input.
    cfb_independent_schedule_path: str = Field(default=str(
        Path.home() / 'asp-model-studio' / 'outputs' / 'cfb-gen1-current-v1' / 'private-sources' / 'cfbd'
        / 'games-2026.json'))
    #: The producer's retained final-result evidence pointer. No network: the
    #: finals consumer only reads what Codex already captured.
    cfb_final_evidence_pointer_path: str = Field(default=str(
        Path.home() / 'asp-model-exchange' / 'cfb-gen1-production-v1' / 'final-result-evidence' / 'current.json'))
    #: Activation control for AISP_CFB_PAPER_POLICY_V1 positions. Off means the
    #: checkpoint still evaluates and records its reason, but never positions.
    cfb_paper_decisions_enabled: bool = False
    #: SportsGameOdds CFB featured-slate lane: at most this many objects in
    #: total for the 2026-09-18 assignment, and only from capacity both budget
    #: guards leave uncommitted after MLB/NFL projected need.
    cfb_sgo_lane_enabled: bool = False
    #: Unresolved production identities for the CFB price adapter. Empty means
    #: unresolved and the adapter requests nothing; they are pinned only from a
    #: live observation (see app/cfb/sgo_price.py LIVE_VALIDATION) through a
    #: reviewed binding artifact (app/cfb/sgo_binding.py), never guessed.
    cfb_sgo_league_id: str = ""
    cfb_sgo_team_map_path: str = ""
    cfb_sgo_lane_object_cap: int = 10
    #: An owner's per-day bound on the CFB lane. Unset keeps the total-only cap
    #: above. It can only lower what the capacity gate allows; the lane itself
    #: stays off (CFB_SGO_LANE_ENABLED) until the owner decides otherwise.
    cfb_sgo_lane_daily_object_cap: int | None = Field(default=None, ge=0, le=20)
    #: Objects of month headroom always left to MLB/NFL (projection error) before
    #: any CFB object is uncommitted. Owner, 2026-09-18: retain the four objects
    #: the September projection leaves beyond MLB/NFL demand for those commitments.
    cfb_sgo_protected_headroom: int = 10
    #: The ONE live validation request that observes the provider's real CFB
    #: league and team identifiers (app/cfb/sgo_price.py LIVE_VALIDATION). Off by
    #: default; even when on, it spends only through the lane (switched on, with
    #: capacity), at most ``cfb_sgo_validation_max_requests`` requests in total,
    #: and it prices nothing: it writes a binding review artifact a person
    #: reviews before CFB_SGO_LEAGUE_ID and the team map are pinned.
    cfb_sgo_validation_enabled: bool = False
    #: The provider's DOCUMENTED College Football league key (its OpenAPI example
    #: data, retained 2026-09-19). A request filter only -- never a pinned identity.
    cfb_sgo_validation_league_candidate: str = "NCAAF"
    cfb_sgo_validation_max_requests: int = Field(default=1, ge=0, le=3)
    # The V18 producer's own daily slate receipts, read-only, so a card with no
    # forecast can show the producer's actual per-game reason.
    mlb_v18_slate_receipts_root: str = str(
        Path.home() / 'asp-model-studio' / 'outputs' / 'mlb-v18-current-state-pipeline-v1' / 'slates'
    )
    cfb_sgo_lane_authorized_until: str = "2026-10-01T00:00:00+00:00"
    # -- NBA Gen1 (app/nba/gen1_*). EVERY switch here defaults OFF and every pin
    # defaults EMPTY. Nothing NBA is imported, decided, requested or spent until
    # the owner sets these deliberately; see docs/NBA_LAUNCH_PLAN_20260919.md.
    #: The selected model handoff: a DIRECTORY, so the path is its index.json and
    #: the SHA-256 pins that index (which in turn pins every member). Empty means
    #: NOT_RECEIVED and no production label is shown.
    nba_production_handoff_path: str = ""
    nba_production_handoff_sha256: str = ""
    #: The consumer switch. Off: the exchange consumer skips NBA production
    #: packages entirely, even with a pin.
    nba_production_consumer_enabled: bool = False
    #: The producer's exchange tree. V2 slate discovery (slate-discovery/current.json)
    #: is preferred; the V1 single-game discovery/current.json is read when no V2
    #: pointer exists. Both are verified from bytes.
    nba_exchange_root: str = Field(default=str(
        Path.home() / 'asp-model-exchange' / 'nba-gen1-production-v1'))
    #: HISTORICAL evidence (read-only, pinned). Model Studio's 2025-26 fixed
    #: assessment receipt and its joint-residual season receipt; each binds its
    #: artifacts by SHA-256. Empty means the historical import refuses to run.
    nba_historical_assessment_receipt_path: str = ""
    nba_historical_assessment_receipt_sha256: str = ""
    nba_historical_joint_receipt_path: str = ""
    nba_historical_joint_receipt_sha256: str = ""
    #: THE PRODUCTION FINAL PATH: the source-confirmed finals exchange lane
    #: (finals-discovery/current.json -> NBA_GEN1_FINAL_DISCOVERY_V1 ->
    #: NBA_GEN1_SOURCE_CONFIRMED_FINAL_V1 records). Empty uses
    #: <NBA_EXCHANGE_ROOT>/finals-discovery/current.json. An absent pointer is a
    #: no-op, never an error; the lane is on because it is the delivery path, and
    #: it spends nothing and requests nothing.
    nba_finals_exchange_pointer: str = ""
    nba_finals_exchange_enabled: bool = True
    #: The SEPARATE HISTORICAL lane: retained ESPN-derived schedule objects
    #: (content-addressed parquet files Model Studio captured). Finals are
    #: re-read from these bytes only; no request is ever made. ';'-separated
    #: "<path>|<sha256>" pairs. This lane reconstructs finished seasons; it is
    #: not the production path.
    nba_final_source_objects: str = ""
    #: Activation control for AISP_NBA_PAPER_POLICY_V1 positions. Off: the NBA
    #: checkpoint still evaluates and records its reason, but never positions.
    nba_paper_decisions_enabled: bool = False
    #: The producer's OPTIONAL, release-bound recurring paper grant (schema
    #: NBA_RECURRING_PAPER_AUTHORIZATION_V1). Empty = no paper authority; the
    #: checkpoint blocks NO_PAPER_AUTHORIZATION and everything else still
    #: imports. A grant must be the one THIS package's slate release binds.
    nba_paper_policy_grant_path: str = ""
    nba_paper_policy_grant_sha256: str = ""
    #: SportsGameOdds NBA price lane. Off, and the league id and reviewed team map
    #: are unresolved: SGO's NBA identities have never been observed by AISP, so
    #: nothing is guessed and nothing is requested. See app/nba/sgo_price.py.
    nba_sgo_lane_enabled: bool = False
    nba_sgo_league_id: str = ""
    nba_sgo_team_map_path: str = ""
    #: Objects the lane may ever spend under its current authority. 0 = none.
    #: An October allowance is an owner decision (docs/NBA_LAUNCH_PLAN_20260919.md).
    nba_sgo_lane_object_cap: int = 0
    nba_sgo_lane_authorized_until: str = ""
    # Optional local orchestration of the bounded Model Studio producer.  AISP
    # never computes NFL features itself: when explicitly armed, the existing
    # scheduler launches the pinned producer CLI without a shell, then imports
    # only through the independently verified exchange above.
    nfl_shadow_producer_enabled: bool = False
    nfl_model_studio_root: str = ""
    nfl_model_studio_python: str = ""
    nfl_shadow_producer_timeout_seconds: int = Field(default=3600, ge=60, le=7200)
    shadow_virtual_bankroll: float = Field(default=1000.0, gt=0)
    shadow_qualified_edge: float = Field(default=0.02, ge=0, le=1)
    shadow_kelly_fraction: float = Field(default=0.25, gt=0, le=1)
    shadow_unit_percentage: float = Field(default=0.005, gt=0, le=0.1)
    shadow_max_position_percentage: float = Field(default=0.01, gt=0, le=0.1)
    shadow_max_same_game_percentage: float = Field(default=0.02, gt=0, le=0.25)
    shadow_max_daily_percentage: float = Field(default=0.05, gt=0, le=0.5)
    # Code/commit identity stamped onto the immutable experiment-start record.
    build_commit: str = ""

    # ------------------------------------------------------------------
    # Prediction Market Observer (Polymarket + Kalshi) — READ-ONLY research.
    # Provider access stays off unless explicitly armed; public display is a
    # second, independent gate. No trading, ever. No key is committed.
    # ------------------------------------------------------------------
    prediction_market_observer_enabled: bool = False
    prediction_market_public_display_enabled: bool = False
    polymarket_enabled: bool = False
    polymarket_market_stream_enabled: bool = False
    polymarket_sports_stream_enabled: bool = False
    polymarket_gamma_base_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_base_url: str = "https://clob.polymarket.com"
    polymarket_market_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    kalshi_enabled: bool = False
    kalshi_rest_polling_enabled: bool = False
    kalshi_market_data_ws_enabled: bool = False
    # Public market data (series/events/markets/orderbooks) needs no API key.
    kalshi_rest_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    kalshi_ws_url: str = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
    kalshi_api_key_id: str = ""
    kalshi_private_key_path: str = ""
    prediction_market_sports: str = "MLB,NFL"
    prediction_market_types: str = "MONEYLINE,SPREAD,TOTAL"
    prediction_market_discovery_minutes: int = Field(default=60, ge=1, le=1440)
    prediction_market_snapshot_minutes: int = Field(default=10, ge=1, le=240)
    prediction_market_max_active_markets: int = Field(default=200, ge=1, le=5000)
    prediction_market_max_requests_per_hour: int = Field(default=600, ge=1, le=20000)
    prediction_market_stale_seconds: int = Field(default=300, ge=10, le=86400)
    prediction_market_min_depth: float = Field(default=50.0, ge=0)
    prediction_market_max_spread: float = Field(default=0.10, gt=0, le=1)

    # ------------------------------------------------------------------
    # V18 vs fresh-market prospective pilot: consumer pregame packet export.
    # Private research only (protocol f75aeccd..., frozen 2026-09-18). OFF by
    # default and inert when off: nothing is registered, nothing is written.
    # When on, the sealer reads retained rows and writes write-once packets to
    # its own directory -- no provider request, no decision, no position.
    # ------------------------------------------------------------------
    v18_market_pilot_export_enabled: bool = False
    #: Where sealed packets, their .sha256 files and the append-only attempt
    #: ledger are written. Created on first write, never overwritten.
    v18_market_pilot_packet_dir: str = Field(default=str(
        Path.home() / "asp-model-exchange" / "mlb-v18-market-prospective-v1" / "consumer-packets"))
    #: Sealer cadence. The protocol allows T-15 .. T-5, so ticks stay short.
    v18_market_pilot_interval_minutes: int = Field(default=2, ge=1, le=2)
    #: The producer's frozen protocol object, cross-checked against the
    #: byte-exact vendored copy when readable. Never written.
    v18_market_pilot_protocol_dir: str = Field(default=str(
        Path.home() / "asp-model-studio" / "outputs" / "mlb-v18-market-prospective-v1" / "objects"
        / "sha256" / "f75aeccd8f0e472b399d1f470c29d6a443d77078f7c69333023292019d956bf7"))
    #: The whole-game MLB quarantine ledger applied before any packet is built.
    v18_market_pilot_quarantine_ledger_path: str = Field(default=str(
        Path.home() / "asp-model-studio" / "server" / "artifacts" / "governance"
        / "mlb-final-oot-quarantine-v1.json"))
    # -- Model Studio's readable canonical originals (SOURCE_ACCESS_REPAIR_PROPOSAL
    # f69c0bf6aca205926bf707ccc8c665d89a86d711e07d1f34987fdbfbdb7e0572, 2026-09-19).
    # The exchange package the importer read is ACL-restricted to elevated tokens,
    # so a packet's object/input/receipt references resolve to files the producer's
    # own private reader cannot open. These roots hold the SAME bytes, already
    # readable unelevated. They are only ever read; a reference moves to them only
    # when the physically read digest and length equal the identity the packet
    # already binds, and never for an already-sealed packet.
    #: <root>/<objectSHA>/manifest.json, pregame-evidence.json, execution-inputs.json.
    v18_market_pilot_canonical_object_root: str = Field(default=str(
        Path.home() / "asp-model-studio" / "outputs" / "mlb-v18-current-state-pipeline-v1"
        / "objects" / "sha256"))
    #: <root>/<logicalHandoffSHA>/aisp-handoff.json. Read only to learn the exact
    #: member file names the handoff names; never emitted as a packet reference.
    v18_market_pilot_canonical_handoff_root: str = Field(default=str(
        Path.home() / "asp-model-studio" / "outputs" / "mlb-v18-current-state-pipeline-v1"
        / "handoffs" / "sha256"))
    #: <root>/<receiptSHA>/incremental-publication.json: the per-object atomic
    #: publication receipt whose packageExposedAtUtc is the exchange-directory rename.
    v18_market_pilot_publication_receipt_root: str = Field(default=str(
        Path.home() / "asp-model-studio" / "outputs" / "mlb-v18-unattended-v1"
        / "objects" / "sha256"))
    #: The same pilot's consumer-admitted outcomes (Model Studio's "same-final
    #: export"). A SEPARATE switch, OFF by default and inert when off. When on,
    #: a 10-minute research-export job reads only episodes with an authentic
    #: SEALED packet, under a read-only transaction, and writes write-once
    #: outcome records to its own directory -- never into the packets directory.
    v18_market_pilot_outcomes_export_enabled: bool = False
    v18_market_pilot_outcomes_dir: str = Field(default=str(
        Path.home() / "asp-model-exchange" / "mlb-v18-market-prospective-v1" / "consumer-outcomes"))
    #: Non-PENDING outcomes (DECISIVE/TIE/VOID/NO_CONTEST) AISP admitted but
    #: whose source supplied no finalAtUtc: Model Studio's frozen reader parses
    #: finalAtUtc for every non-PENDING status, so they are kept OUT of the
    #: input lane above, write-once, with their evidence and reason.
    #: Must not overlap the outcomes or packets directory.
    v18_market_pilot_outcomes_withheld_dir: str = Field(default=str(
        Path.home() / "asp-model-exchange" / "mlb-v18-market-prospective-v1" / "consumer-outcomes-withheld"))
    #: V2 export for the accepted outcome amendment 7508ae72... (source-confirmed
    #: terminal, capture-bound, finalAtUtc null with an explicit basis). A
    #: SEPARATE switch, OFF by default and inert when off; V1 is unchanged. It
    #: writes only its own sibling lane, never the packets, V1 or withheld dirs.
    v18_market_pilot_outcomes_v2_enabled: bool = False
    v18_market_pilot_outcomes_v2_dir: str = Field(default=str(
        Path.home() / "asp-model-exchange" / "mlb-v18-market-prospective-v1" / "consumer-outcomes-v2"))

    # ------------------------------------------------------------------
    # Prospective model-market capture (app.research_exports.prospective_capture).
    # Model Studio's contract IS frozen now (MODEL_MARKET_OBSERVATION_V1, contract
    # sha256 e0ee599e..., protocol sha256 fcb530fc...), and the exporter is mapped
    # onto it -- but it stays OFF until Codex passes the mapping. Off means inert:
    # no row is read and no file is opened.
    #
    # There is deliberately NO scheduler entry for it. Its invocation path is
    # PREPARED_NOT_ACTIVATED: a trailing step of the EXISTING research-export
    # owner `export-v18-market-pilot-outcomes-v2` (`asp-rx-*`), which creates no
    # new scheduler, job or owner and never touches the decision-checkpoint lane.
    # Turning this on while that owner is registered is what activates it.
    # It never writes a decision, a position or any production record.
    # ------------------------------------------------------------------
    #: How often the dedicated NFL FINAL pair import runs, in minutes. One
    #: minute: a FINAL published at the producer's operational target must be
    #: imported before the last checkpoint tick inside its T-30..T-15 window,
    #: and the broad pass's five-minute cadence cannot guarantee that.
    nfl_final_import_interval_minutes: int = 1
    prospective_capture_enabled: bool = False
    #: Its own lane. Must not overlap any V18 market-pilot directory; the
    #: exporter refuses to run if it does.
    prospective_capture_dir: str = Field(default=str(
        Path.home() / "asp-model-exchange" / "prospective-research-capture-v1"))

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
    # No administrator password is shipped. It is only used to bootstrap an absent
    # administrator (``ensure_admin`` refuses a missing or short value); the live
    # credential is the stored hash, not this setting.
    admin_password: str = ""

    @field_validator("the_odds_api_key", mode="before")
    @classmethod
    def normalize_optional_key(cls, value: object) -> str:
        return "" if value is None else str(value).strip()

    @field_validator("sgo_exact_event_selection_mode", mode="before")
    @classmethod
    def normalize_exact_selection_mode(cls, value: object) -> str:
        """``validate`` and ``VALIDATE`` are the same; empty is OFF."""
        text = "" if value is None else str(value).strip().upper()
        return text or "OFF"

    @model_validator(mode="after")
    def require_explicit_credentials(self) -> "Settings":
        """Fail closed when credentials are not supplied outside tests (SEC-DB-001).

        Only ``app_env == "test"`` may run without explicit values; the test harness
        always injects a disposable URL and key. Development and production must be
        given installation-specific values rather than inheriting shipped defaults.
        """
        if self.app_env != "test":
            if not self.database_url.strip():
                raise ValueError(
                    "DATABASE_URL must be set explicitly; no shared database default is "
                    "shipped. Provide an installation-specific PostgreSQL URL via the "
                    "environment or .env before starting the application."
                )
            if not self.secret_key.strip():
                raise ValueError(
                    "SECRET_KEY must be set explicitly; no signing key is shipped. "
                    "Provide a unique value of at least 32 characters via the environment or .env."
                )
        return self

    @model_validator(mode="after")
    def production_guards(self) -> "Settings":
        if self.app_env == "production":
            if self.secret_key == "change-me-before-any-shared-deployment" or len(self.secret_key) < 32:
                raise ValueError("Production SECRET_KEY must be a unique value of at least 32 characters")
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("Production DATABASE_URL must use PostgreSQL")
            if self.demo_mode:
                raise ValueError("DEMO_MODE must be false in production")
            if len(self.admin_password) < 12:
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
    def owns_odds_collection(self) -> bool:
        """True only in the single process designated to spend odds credits.

        Fail-closed twice over: an unset token on either side means no owner,
        and a mismatch means this process is not it.
        """
        import os

        declared = os.environ.get("ASP_ODDS_COLLECTION_OWNER", "").strip()
        configured = (self.odds_collection_owner or "").strip()
        return bool(configured) and declared == configured

    @property
    def owns_nfl_final_ingest(self) -> bool:
        """True only in the single supervised process, and only when armed.

        Bound to the SAME process-identity token as odds collection. That token
        is not really about odds -- it is how this host names the one backend
        the supervisor started, and this machine has repeatedly had three or
        four listening at once. Two of them ingesting finals would race on the
        same append-only rows for no benefit.

        Fail-closed twice over, exactly like the odds owner: an unset token on
        either side means no owner, and a mismatch means this process is not it.
        """

        return self.nfl_final_ingest_enabled and self.owns_odds_collection

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def odds_market_list(self) -> list[str]:
        return [market.strip() for market in self.odds_markets.split(",") if market.strip()]

    @property
    def prediction_market_sport_list(self) -> list[str]:
        return [s.strip().upper() for s in self.prediction_market_sports.split(",") if s.strip()]

    @property
    def prediction_market_type_list(self) -> list[str]:
        return [t.strip().upper() for t in self.prediction_market_types.split(",") if t.strip()]

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
