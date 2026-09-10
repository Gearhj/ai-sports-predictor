from __future__ import annotations

from pathlib import Path
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.dependencies import AdminUser, AppSettings, CurrentUser, DatabaseSession
from app.core.rate_limit import rate_limit
from app.core.time import local_today
from app.jobs import AVAILABLE_JOBS, JobOrchestrator
from app.models import (
    Forecast,
    ForecastTrack,
    Game,
    JobRun,
    OddsSnapshot,
    OfficialAspPosition,
)
from app.schemas.api import (
    AuthResponse,
    AuthUser,
    BankrollData,
    DashboardData,
    GameAnalysisData,
    HealthData,
    HistoricalGameExplorerPage,
    HistoricalResearchData,
    JobTriggerResponse,
    LoginRequest,
    MarketBoardData,
    PerformanceData,
    ProspectiveRescoreBoard,
    PublicConfigData,
    RegisterRequest,
    ShadowBoardData,
)
from app.prospective.contracts import AvailabilityEventInput
from app.sports.contracts import TodayAllSportsReadModel
from app.services.auth_service import (
    AuthenticationError,
    DuplicateEmailError,
    authenticate_user,
    public_user,
    register_user,
    revoke_user_tokens,
)
from app.services.labs_read_service import DEFAULT_LIMIT, LabsReadService
from app.services.read_service import ReadService
from app.shadow_import.mlb_v18_play_geometry import geometry_payload
from app.shadow_import.mlb_v18_population_geometry import (
    population_layers,
    population_payload,
    population_representatives,
)
from app.shadow_import.mlb_v18_read import sequential_path, shadow_comparison
from app.services.scenario_service import run_scenario, scenario_capabilities
from app.services.simulation_replay_service import replay_official_simulation
from app.services.historical_research_read_service import HistoricalResearchReadService


router = APIRouter()


def _today(value: date | None, settings: AppSettings) -> date:
    return value or local_today(settings.display_timezone)


def _current_public_date(value: date | None, settings: AppSettings) -> date:
    """Keep the public Today surface bound to the actual local calendar day."""

    current = local_today(settings.display_timezone)
    if value is not None and value != current:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Today endpoint serves the current local date only.",
        )
    return current


@router.get("/public/config", response_model=PublicConfigData, tags=["system"])
def public_config(session: DatabaseSession, settings: AppSettings) -> PublicConfigData:
    weights = {
        "baseline": settings.ensemble_baseline_weight,
        "run": settings.ensemble_run_weight,
        "boosting": settings.ensemble_boosting_weight,
    }
    total = sum(weights.values())
    normalized = {key: (value / total if total > 0 else 0.0) for key, value in weights.items()}
    sports_records = sum(
        int(session.scalar(select(func.count()).select_from(model)) or 0)
        for model in (Game, OddsSnapshot)
    )
    sports_records += int(session.scalar(
        select(func.count()).select_from(Forecast).where(
            Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT
        )
    ) or 0)
    sports_records += int(session.scalar(
        select(func.count())
        .select_from(OfficialAspPosition)
        .join(Forecast, Forecast.id == OfficialAspPosition.forecast_id)
        .where(Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT)
    ) or 0)
    if not sports_records:
        data_mode = "empty"
    else:
        has_demo = bool(session.scalar(select(Game.id).where(Game.source_mode == "DEMO").limit(1)))
        has_live = bool(session.scalar(select(Game.id).where(Game.source_mode == "LIVE").limit(1)))
        data_mode = "live" if has_live else "demo" if has_demo else "empty"
    return PublicConfigData(
        data_mode=data_mode,
        model_status="DEVELOPMENT",
        component_weights=normalized,
        display_timezone=settings.display_timezone,
        operational_date=local_today(settings.display_timezone),
        bankroll_policy={
            "kelly_fraction": 0.25,
            "maximum_stake_percentage": settings.official_max_stake_percentage,
            "standard_edge_threshold": settings.opportunity_standard_threshold,
            "label": "PROVISIONAL PHASE 1 BANKROLL POLICY",
        },
    )


@router.get("/dashboard/today", response_model=DashboardData, tags=["forecasts"])
def dashboard_today(session: DatabaseSession, settings: AppSettings, event_date: date | None = Query(None, alias="date")) -> DashboardData:
    return ReadService(session, settings).dashboard(
        _current_public_date(event_date, settings)
    )


@router.get("/games/slate", response_model=DashboardData, tags=["forecasts"])
def games_forecast_slate(session: DatabaseSession, settings: AppSettings, event_date: date | None = Query(None, alias="date")) -> DashboardData:
    """Recorded MLB slate by date; never generate a forecast or change Today semantics."""
    return ReadService(session, settings).dashboard(_today(event_date, settings))


@router.get("/today", response_model=TodayAllSportsReadModel, tags=["forecasts"])
def today_all_sports(
    session: DatabaseSession,
    settings: AppSettings,
    event_date: date | None = Query(None, alias="date"),
    sport: str | None = Query(None),
) -> TodayAllSportsReadModel:
    """TODAY_ALL_SPORTS_READ_MODEL_V1 — the all-sports decision hub.

    Database-only: assembled from existing read services, never a provider.
    Accepts ALL or an active league code; a PLANNED sport is rejected rather
    than served as an empty live surface.
    """
    from app.sports.read_service import (
        TodayAllSportsReadService,
        is_supported_filter,
        supported_league_filters,
    )

    if not is_supported_filter(sport):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported sport filter '{sport}'. "
                f"Supported in this phase: {', '.join(supported_league_filters())}."
            ),
        )
    return TodayAllSportsReadService(session, settings).build(
        _today(event_date, settings), league_filter=sport,
    )


@router.get("/sports/registry", tags=["system"])
def sports_registry() -> dict:
    """SPORT_REGISTRY_V1 — the canonical multi-sport registry, including
    PLANNED sports (declared for honesty, never navigable)."""
    from app.sports.contracts import SPORT_REGISTRY_VERSION
    from app.sports.read_service import supported_league_filters
    from app.sports.registry import registry_manifest

    return {
        "version": SPORT_REGISTRY_VERSION,
        "sports": registry_manifest(),
        "supported_filters": supported_league_filters(),
    }


@router.get("/games/{game_id}", response_model=GameAnalysisData, tags=["forecasts"])
def game_analysis(game_id: UUID, session: DatabaseSession, settings: AppSettings) -> GameAnalysisData:
    result = ReadService(session, settings).game_analysis(game_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
    return result


@router.get("/labs/games", tags=["forecasts"])
def labs_games(
    session: DatabaseSession,
    settings: AppSettings,
    league: str = "MLB",
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Games an interactive lab can actually open, with the tracks each offers.

    Read-only. A game appears only when its own persisted simulation supports a
    replay, and a V18 action appears only where a research-shadow path exists
    for that specific game.
    """

    return LabsReadService(session, settings).eligible_games(league, limit=limit)


@router.get("/games/{game_id}/shadow-forecasts", tags=["forecasts"])
def game_shadow_forecasts(game_id: UUID, session: DatabaseSession) -> dict:
    """Official Gen 1 forecast beside any registered research-shadow forecast.

    Read-only. Returns both tracks with their own timestamps and model
    identity; a comparison is only paired when both satisfy the acceptance
    contract's pregame timing rule. Grants no wagering authority.
    """

    payload = shadow_comparison(session, game_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
    return payload


@router.get("/shadow-forecasts/{forecast_id}/geometry", tags=["forecasts"])
def shadow_forecast_geometry(forecast_id: str, session: DatabaseSession) -> dict:
    """Registered Play Geometry for one research-shadow forecast.

    Read-only. Returns 404 when no checksum-verified supplement is registered,
    so the consumer fails closed to the Broadcast Replay instead of drawing a
    synthesised trajectory.
    """

    payload = geometry_payload(session, forecast_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No verified play-geometry supplement is registered for this forecast",
        )
    return payload


@router.get("/shadow-forecasts/{forecast_id}/population", tags=["forecasts"])
def shadow_forecast_population(forecast_id: str, session: DatabaseSession) -> dict:
    """Stage one of the registered population supplement: summary and frames.

    Read-only, and deliberately small: the accumulation frames and the accepted
    population metrics only. Returns 404 when no checksum-verified population
    supplement is registered, so the consumer falls back to the Broadcast
    Replay rather than inventing an aggregate.
    """

    payload = population_payload(session, forecast_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No verified population-geometry supplement is registered for this forecast",
        )
    return payload


@router.get("/shadow-forecasts/{forecast_id}/population/layers", tags=["forecasts"])
def shadow_forecast_population_layers(forecast_id: str, session: DatabaseSession) -> dict:
    """Stage two: the compact contact, ball-path and runner-traffic layers."""

    payload = population_layers(session, forecast_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No verified population-geometry supplement is registered for this forecast",
        )
    return payload


@router.get("/shadow-forecasts/{forecast_id}/population/representatives", tags=["forecasts"])
def shadow_forecast_population_representatives(
    forecast_id: str, session: DatabaseSession
) -> dict:
    """Stage three: the six distinct representative simulated games."""

    payload = population_representatives(session, forecast_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No verified population-geometry supplement is registered for this forecast",
        )
    return payload


@router.get("/shadow-forecasts/{forecast_id}/path", tags=["forecasts"])
def shadow_forecast_path(forecast_id: str, session: DatabaseSession) -> dict:
    """The persisted V18 sequential plate-appearance path for one forecast."""

    payload = sequential_path(session, forecast_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No research-shadow path exists for this forecast",
        )
    return payload


@router.get("/games/{game_id}/simulation", tags=["forecasts"])
def game_simulation(game_id: UUID, session: DatabaseSession, settings: AppSettings) -> dict:
    """Read-only Monte Carlo evidence for one game's official forecast.

    Deterministically replays the official simulation from the inputs the
    forecast already persists and verifies the replay against the stored
    aggregates. Performs no write, no provider call and no forecast mutation.
    """

    payload = replay_official_simulation(session, game_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No forecast exists for this game",
        )
    return payload


@router.get("/games/{game_id}/simulation/capabilities", tags=["forecasts"])
def game_scenario_capabilities(game_id: UUID, session: DatabaseSession, settings: AppSettings) -> dict:
    """Which What-If controls this game can honestly support, and why not.

    Read-only. A control is SUPPORTED only when changing it flows through the
    same feature mapping, ensemble and Monte Carlo the official forecast used.
    """

    payload = scenario_capabilities(session, game_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No forecast with a feature snapshot exists for this game",
        )
    return payload


class ScenarioRequest(BaseModel):
    """Structured hypothetical assumptions. Unsupported keys are rejected."""

    overrides: dict[str, float] = Field(default_factory=dict)


@router.post("/games/{game_id}/simulation/scenario", tags=["forecasts"])
def game_scenario(
    game_id: UUID,
    request: ScenarioRequest,
    session: DatabaseSession,
    settings: AppSettings,
) -> dict:
    """Recompute a HYPOTHETICAL forecast under the supplied assumptions.

    POST carries structured inputs; it is still strictly read-only. It writes
    no row, creates no forecast, decision or position, touches no ledger, makes
    no provider request and starts no scheduler job. The official forecast is
    read and never modified.
    """

    payload = run_scenario(session, game_id, request.overrides, settings)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No forecast with a feature snapshot exists for this game",
        )
    return payload


@router.get("/markets/board", response_model=MarketBoardData, tags=["markets"])
def market_board(session: DatabaseSession, settings: AppSettings, event_date: date | None = Query(None, alias="date")) -> MarketBoardData:
    return ReadService(session, settings).market_board(_today(event_date, settings))


@router.get("/performance", response_model=PerformanceData, tags=["transparency"])
def performance(session: DatabaseSession, settings: AppSettings) -> PerformanceData:
    return ReadService(session, settings).performance()


@router.get(
    "/research/history",
    response_model=HistoricalResearchData,
    tags=["transparency"],
)
def historical_research(session: DatabaseSession) -> HistoricalResearchData:
    return HistoricalResearchReadService(session).history()


@router.get(
    "/research/history/games",
    response_model=HistoricalGameExplorerPage,
    tags=["transparency"],
)
def historical_game_explorer(
    session: DatabaseSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    team: str | None = Query(None, min_length=1, max_length=80),
    month: int | None = Query(None, ge=1, le=12),
    tier: Literal["A", "B"] = Query("A"),
    source: str | None = Query(None, min_length=1, max_length=80),
) -> HistoricalGameExplorerPage:
    return HistoricalResearchReadService(session).game_explorer(
        page=page,
        page_size=page_size,
        team=team,
        month=month,
        tier=tier,
        source=source,
    )


@router.get("/bankroll", response_model=BankrollData, tags=["transparency"])
def bankroll(
    session: DatabaseSession,
    settings: AppSettings,
    experiment: Literal["v1", "v2"] = Query("v1"),
) -> BankrollData:
    return ReadService(session, settings).bankroll(experiment)


# -- Market intelligence: the tape, the RFF research index, information events --
#
# All three are read models over evidence AISP already stores. None calls a
# provider, spends quota, writes a row, or is an input to any forecast,
# threshold, decision or position.
def _games_on(session, day: date, league: str | None, timezone_name: str) -> list[UUID]:
    """Games whose local start date is ``day``.

    The local date, not the UTC one: a 20:20 Eastern kickoff is tomorrow in UTC,
    and putting it on tomorrow's board would silently disagree with every other
    surface in the product.
    """

    from app.models import League, Season

    query = (
        select(Game.id)
        .join(Season, Season.id == Game.season_id)
        .join(League, League.id == Season.league_id)
        .where(func.date(func.timezone(timezone_name, Game.starts_at)) == day)
        .order_by(Game.starts_at)
    )
    if league:
        query = query.where(func.upper(League.code) == league.upper())
    return list(session.scalars(query))


@router.get("/markets/tape/{game_id}", tags=["markets"])
def market_tape(
    session: DatabaseSession,
    game_id: UUID,
    market: Literal["MONEYLINE", "SPREAD", "TOTAL"] = Query("MONEYLINE"),
) -> dict:
    """One game's price movement, reconstructed from persisted snapshots."""

    from app.services.market_tape import MarketTapeService

    return MarketTapeService(session).tape(game_id=game_id, market_type=market)


@router.get("/markets/rff/{game_id}", tags=["markets"])
def rff_for_game(
    session: DatabaseSession,
    game_id: UUID,
    market: Literal["MONEYLINE", "SPREAD", "TOTAL"] = Query("MONEYLINE"),
    include_tape: bool = Query(False),
) -> dict:
    """Provisional RFF for one game. Research index; affects no decision."""

    from app.services.rff_index import RffService

    return RffService(session).for_game(
        game_id=game_id, market_type=market, include_tape=include_tape,
    )


@router.get("/markets/rff", tags=["markets"])
def rff_board(
    session: DatabaseSession,
    settings: AppSettings,
    event_date: date | None = Query(None, alias="date"),
    league: Literal["MLB", "NFL"] | None = Query(None),
    market: Literal["MONEYLINE", "SPREAD", "TOTAL"] = Query("MONEYLINE"),
    limit: int = Query(12, ge=1, le=40),
) -> dict:
    """Provisional RFF across a day's games, most unsettled first."""

    from app.services.rff_index import RffService

    day = _today(event_date, settings)
    game_ids = _games_on(session, day, league, settings.display_timezone)
    board = RffService(session).board(game_ids=game_ids, market_type=market, limit=limit)
    board["date"] = day.isoformat()
    board["league"] = league
    board["games_considered"] = len(game_ids)
    return board


@router.get("/information-events/contract", tags=["markets"])
def information_event_contract(session: DatabaseSession) -> dict:
    """The versioned contract an information-event producer must satisfy."""

    from app.information_events import InformationEventReadService

    return InformationEventReadService(session).contract()


@router.get("/information-events/{game_id}", tags=["markets"])
def information_events_for_game(
    session: DatabaseSession,
    game_id: UUID,
    market: Literal["MONEYLINE", "SPREAD", "TOTAL"] = Query("MONEYLINE"),
) -> dict:
    """Information events for one game, projected into the versioned contract."""

    from app.information_events import InformationEventReadService

    return InformationEventReadService(session).for_game(game_id=game_id, market_type=market)


@router.get("/experiment/comparison", tags=["experiment"])
def experiment_comparison(session: DatabaseSession, settings: AppSettings) -> dict:
    """Read-only V1-vs-V2 aggregates. Ledgers are never pooled."""
    return ReadService(session, settings).experiment_comparison()


# -- NFL product -----------------------------------------------------------
@router.get("/nfl/markets/{season}/{week}", tags=["nfl"])
def nfl_markets(season: int, week: int, session: DatabaseSession, settings: AppSettings) -> dict:
    """Stored model/book/crowd evidence; no refresh, execution or decisions."""
    from app.services.unified_market_read import nfl_market_board

    return nfl_market_board(session, settings, season, week)


@router.get("/nfl/weeks", tags=["nfl"])
def nfl_weeks(session: DatabaseSession, settings: AppSettings) -> dict:
    """Every imported NFL week with its event count, and the current week by the runtime clock."""

    from app.services.nfl_read_service import NflReadService

    return NflReadService(session, settings).weeks()


@router.get("/nfl/weeks/{season}/{week}", tags=["nfl"])
def nfl_week(season: int, week: int, session: DatabaseSession, settings: AppSettings) -> dict:
    """One card per event: latest valid snapshot per engine, kickoff, state, lab availability.

    Read-only research shadow. A started game keeps its last published pregame
    forecast; nothing is generated after kickoff.
    """

    from app.services.nfl_read_service import NflReadService

    return NflReadService(session, settings).week_overview(season, week)


@router.get("/nfl/games/{game_id}", tags=["nfl"])
def nfl_game(game_id: UUID, session: DatabaseSession, settings: AppSettings) -> dict:
    """Full NFL game detail: engines, joint-score explorer, market comparison, supplements."""

    from app.services.nfl_read_service import NflReadService

    payload = NflReadService(session, settings).game_detail(game_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No NFL research forecast for this game")
    return payload


@router.get("/nfl/games/{game_id}/play-theater", tags=["nfl"])
def nfl_play_theater(game_id: UUID, session: DatabaseSession, settings: AppSettings) -> dict:
    """Play-level scenarios, representative index and pairing. Carries no plays."""

    from app.services.nfl_play_theater import NflPlayTheaterService
    from app.services.nfl_read_service import NflReadService

    event_id = NflReadService(session, settings).event_id_for_game(game_id)
    payload = NflPlayTheaterService(session, settings).theater_index(event_id) if event_id else None
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No play-level simulation evidence for this game")
    return payload


@router.get("/home/featured", tags=["home"])
def home_featured(session: DatabaseSession, settings: AppSettings) -> dict:
    """The matchup the home page leads with, and the rule that chose it.

    The rule travels with the answer in `selection_criteria` so the page can
    state it. Nothing about a game's result takes part in the choice.
    """

    from app.services.home_featured import HomeFeaturedService

    return HomeFeaturedService(session, settings).featured()


@router.get("/nfl/games/{game_id}/play-population/{scenario_id}", tags=["nfl"])
def nfl_play_population(
    game_id: UUID, scenario_id: str,
    session: DatabaseSession, settings: AppSettings,
) -> dict:
    """Every drawable play for one scenario: the six representative games.

    The scenario simulated a thousand games; six carry play paths. The payload
    states both counts in `coverage` so a field full of moving lines can never
    be read as a thousand games on screen.
    """

    from app.services.nfl_play_theater import NflPlayTheaterService
    from app.services.nfl_read_service import NflReadService

    event_id = NflReadService(session, settings).event_id_for_game(game_id)
    payload = (NflPlayTheaterService(session, settings).population(event_id, scenario_id)
               if event_id else None)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No play-level simulation evidence for this scenario")
    return payload


@router.get("/nfl/games/{game_id}/play-theater/{scenario_id}/{position}", tags=["nfl"])
def nfl_play_theater_game(
    game_id: UUID, scenario_id: str, position: int,
    session: DatabaseSession, settings: AppSettings,
) -> dict:
    """ONE representative game with its plays and modeled geometry."""

    from app.services.nfl_play_theater import NflPlayTheaterService
    from app.services.nfl_read_service import NflReadService

    event_id = NflReadService(session, settings).event_id_for_game(game_id)
    payload = (NflPlayTheaterService(session, settings).representative(event_id, scenario_id, position)
               if event_id else None)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such play-level scenario for this game")
    return payload


@router.get("/nfl/research", tags=["nfl"])
def nfl_research(session: DatabaseSession, settings: AppSettings) -> dict:
    """Imported machine-readable model benchmark studies (never a hand-typed table)."""

    from app.services.nfl_read_service import NflReadService

    return NflReadService(session, settings).research_results()


@router.get("/nfl/consumer", tags=["nfl"])
def nfl_consumer(session: DatabaseSession, settings: AppSettings) -> dict:
    """Last successful/failed research-artifact import and registered counts."""

    from app.services.nfl_read_service import NflReadService

    return NflReadService(session, settings).consumer_status()


@router.get("/shadow-board", response_model=ShadowBoardData, tags=["transparency"])
def shadow_board(
    session: DatabaseSession,
    settings: AppSettings,
    event_date: date | None = Query(None, alias="date"),
    sport: Literal["BASEBALL", "NFL"] | None = Query(None),
) -> ShadowBoardData:
    # Read-only research-shadow board. Reads persisted decisions only; it never
    # calls a provider and never exposes executable or promotion authority.
    from app.services.shadow_board_service import ShadowBoardService

    day = event_date or local_today(settings.display_timezone)
    return ShadowBoardService(session, settings).board(day, sport=sport)


@router.get("/shadow-board/rescores", response_model=ProspectiveRescoreBoard, tags=["transparency"])
def shadow_rescores(
    session: DatabaseSession,
    settings: AppSettings,
    event_date: date | None = Query(None, alias="date"),
) -> ProspectiveRescoreBoard:
    # Read-only prospective rescore evidence. Reads persisted rows only; it never
    # calls a provider and exposes no executable authority.
    from app.services.prospective_shadow_service import ProspectiveShadowService

    day = event_date or local_today(settings.display_timezone)
    return ProspectiveShadowService(session, settings).rescores(day)


@router.get(
    "/admin/live-shadow/status",
    tags=["administration"],
    dependencies=[Depends(rate_limit("admin:live-shadow", category="admin"))],
)
def live_shadow_status(session: DatabaseSession, settings: AppSettings, user: AdminUser) -> dict:
    # Admin-only operational status. Reads persisted state only; no provider call.
    from app.services.live_shadow_service import LiveShadowOperator

    return LiveShadowOperator(session, settings).status()


@router.post(
    "/admin/availability-events",
    tags=["administration"],
    dependencies=[Depends(rate_limit("admin:availability", category="admin"))],
)
def submit_availability_event(
    body: AvailabilityEventInput,
    session: DatabaseSession,
    settings: AppSettings,
    user: AdminUser,
) -> dict:
    # Manual admin availability-event adapter (MANUAL_ADMIN provenance). Persists
    # the immutable event and runs the full rescore path for the affected game.
    from app.services.prospective_shadow_service import ProspectiveShadowService, RescoreError

    try:
        return ProspectiveShadowService(session, settings).process_event(body)
    except RescoreError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/health/sources", response_model=HealthData, tags=["operations"])
def source_health(
    session: DatabaseSession,
    settings: AppSettings,
    user: AdminUser,
    event_date: date | None = Query(None, alias="date"),
) -> HealthData:
    # Operational telemetry (provider quota remaining, source freshness, recent
    # job history) is administrator-only. Public liveness is served by the
    # unauthenticated ``/healthz`` and ``/system/ping`` endpoints instead.
    return ReadService(session, settings).health(_today(event_date, settings))


@router.get("/admin/odds-collection", tags=["operations"])
def admin_odds_collection(
    session: DatabaseSession, settings: AppSettings, user: AdminUser,
) -> dict:
    """Operator view of odds credit consumption and the collection strategy.

    Admin-gated on purpose. Credit balances, burn rate, projected exhaustion and
    the provider's cost structure are operational facts, not public product
    detail; the public read model already carries only whether the market feed
    is blocked and why, with no numbers attached to money.

    Read-only: it makes no provider request and spends no credit.
    """

    from app.data_sources import provider_registry
    from app.data_sources.sportsgameodds import SportsGameOddsAdapter
    from app.jobs.odds_quota_recovery import quota_state
    from app.services import odds_budget
    from app.services.odds_collection_service import OddsCollectionService

    service = OddsCollectionService(session, settings)
    status_payload = service.status()
    floor = quota_state(
        session,
        floor=settings.odds_quota_floor,
        probe_after_hours=settings.odds_quota_probe_after_hours,
        probe_cooldown_hours=settings.odds_quota_probe_cooldown_hours,
        renewal_days=settings.odds_quota_renewal_days,
    )
    window_start = odds_budget.billing_window()[0].date()

    # THE OPERATIONAL QUESTION IS COVERAGE, NOT SPEND.
    # A balance and a burn rate say how much is left; they do not say whether
    # tonight's games will have a price when the decision is made. The coverage
    # report answers that per game, and names one exact cause for every game it
    # cannot answer for.
    from app.services.odds_budget_lanes import lane_budget
    from app.services.quote_coverage import QuoteCoverageService

    coverage = QuoteCoverageService(session, settings).daily_report(league_code="MLB")
    lanes = lane_budget(
        session,
        monthly_cap=settings.odds_monthly_credit_cap,
        reserve=settings.odds_monthly_credit_reserve,
        daily_cap=settings.odds_daily_credit_budget,
        floor=settings.odds_quota_floor,
        display_timezone=settings.display_timezone,
    )
    return {
        **status_payload,
        "quote_coverage": coverage,
        "budget_lanes": lanes.as_dict(),
        "provider_floor_guard": floor.as_dict(),
        "spend_by_day": odds_budget.spend_by_day(session, since=window_start),
        "providers": provider_registry.describe(),
        "governed_sources": sorted(provider_registry.GOVERNED_SOURCES),
        # Pass the real configuration: readiness that reports a configured
        # credential as absent tells an operator the opposite of the truth about
        # what still blocks the fallback.
        "fallback_provider": SportsGameOddsAdapter(
            enabled=getattr(settings, "sportsgameodds_enabled", False),
            api_key=getattr(settings, "sportsgameodds_api_key", ""),
            base_url=getattr(settings, "sportsgameodds_base_url", ""),
            mapping_path=(Path(settings.sportsgameodds_mapping_path)
                          if getattr(settings, "sportsgameodds_mapping_path", "") else None),
        ).readiness(),
    }


@router.get("/health", tags=["operations"])
def application_health(session: DatabaseSession, user: AdminUser) -> dict:
    # Database-reachability detail is operational; gate it behind admin and use
    # ``/healthz`` for public liveness probes.
    from sqlalchemy import text
    session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable", "timestamp": datetime.now(UTC)}


@router.post(
    "/auth/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["authentication"],
    dependencies=[Depends(rate_limit("auth:register", category="auth"))],
)
def register(body: RegisterRequest, session: DatabaseSession, settings: AppSettings) -> AuthResponse:
    try:
        return register_user(session, settings, body.email, body.password, body.display_name)
    except DuplicateEmailError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/auth/login",
    response_model=AuthResponse,
    tags=["authentication"],
    dependencies=[Depends(rate_limit("auth:login", category="auth"))],
)
def login(body: LoginRequest, session: DatabaseSession, settings: AppSettings) -> AuthResponse:
    try:
        return authenticate_user(session, settings, body.email, body.password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.get("/auth/me", response_model=AuthUser, tags=["authentication"])
def me(user: CurrentUser) -> AuthUser:
    return public_user(user)


@router.post("/auth/logout", tags=["authentication"])
def logout(user: CurrentUser, session: DatabaseSession) -> dict:
    # Server-side revocation: bumps the user's session generation so every
    # outstanding access token (including the one presented here) is rejected
    # on its next use. Idempotent from the client's perspective — it always
    # ends with no valid session.
    revoke_user_tokens(session, user)
    return {"status": "logged_out"}


@router.get("/admin/jobs", tags=["administration"])
def admin_jobs(session: DatabaseSession, user: AdminUser) -> dict:
    runs = list(session.scalars(select(JobRun).order_by(JobRun.created_at.desc()).limit(50)))
    return {
        "available_jobs": [{"key": key, "label": label} for key, label in AVAILABLE_JOBS.items()],
        "runs": [{
            "id": str(run.id), "job": run.job_name, "status": run.status.value,
            "started_at": run.started_at, "completed_at": run.completed_at,
            "result": run.result, "error": run.error_message,
        } for run in runs],
    }


@router.post(
    "/admin/jobs/{job_name}",
    response_model=JobTriggerResponse,
    tags=["administration"],
    dependencies=[Depends(rate_limit("admin:jobs", category="admin"))],
)
async def trigger_job(job_name: str, session: DatabaseSession, settings: AppSettings, user: AdminUser) -> JobTriggerResponse:
    try:
        run = await JobOrchestrator(session, settings).run(job_name, requested_by_user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return JobTriggerResponse(id=str(run.id), job_id=str(run.id), job=run.job_name, status=run.status.value, result=run.result)


@router.get("/system/ping", tags=["system"])
def ping() -> dict:
    return {"status": "ok", "service": "AI Sports Predictor", "timestamp": datetime.now(UTC)}


# ==========================================================================
# Prediction Market Observer — public reads (DB-only; ZERO provider calls) and
# admin mapping/semantics review. No route exposes any trading capability.
# ==========================================================================
@router.get("/prediction-markets/board", tags=["prediction-markets"])
def prediction_market_board(
    session: DatabaseSession, settings: AppSettings,
    event_date: date | None = Query(None, alias="date"),
    sport: str | None = Query(None),
):
    from app.prediction_markets.read_service import PredictionMarketReadService

    return PredictionMarketReadService(session, settings).board(_today(event_date, settings), sport=sport)


@router.get("/prediction-markets/games/{game_id}", tags=["prediction-markets"])
def prediction_market_game(game_id: UUID, session: DatabaseSession, settings: AppSettings):
    from app.prediction_markets.read_service import PredictionMarketReadService

    detail = PredictionMarketReadService(session, settings).game_detail(game_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
    return detail


@router.get("/prediction-markets/games/{game_id}/timeline", tags=["prediction-markets"])
def prediction_market_timeline(game_id: UUID, session: DatabaseSession, settings: AppSettings):
    from app.prediction_markets.read_service import PredictionMarketReadService

    detail = PredictionMarketReadService(session, settings).game_detail(game_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
    return {"game_id": str(game_id), "timeline": detail.timeline, "lead_lag_note": detail.lead_lag_note}


@router.get("/prediction-markets/alerts", tags=["prediction-markets"])
def prediction_market_alerts(
    session: DatabaseSession, settings: AppSettings,
    event_date: date | None = Query(None, alias="date"), sport: str | None = Query(None),
):
    from app.prediction_markets.read_service import PredictionMarketReadService

    return PredictionMarketReadService(session, settings).alerts(_today(event_date, settings), sport=sport)


@router.get("/prediction-markets/health-summary", tags=["prediction-markets"])
def prediction_market_health(session: DatabaseSession, settings: AppSettings):
    from app.prediction_markets.read_service import PredictionMarketReadService

    return PredictionMarketReadService(session, settings).health_summary()


@router.get("/prediction-markets/capture-coverage", tags=["prediction-markets"])
def prediction_market_capture_coverage(
    session: DatabaseSession, settings: AppSettings,
    event_date: date | None = Query(None, alias="date"),
):
    """Pregame snapshot coverage matrix: one disjoint state per (game, provider,
    milestone) from persisted observations and scheduler runs. Read-only; zero
    provider calls; a future milestone is NOT_DUE, never missed."""
    from app.prediction_markets.capture_coverage import PregameCaptureCoverageService

    return PregameCaptureCoverageService(session, settings).coverage(_today(event_date, settings))


@router.get("/prediction-markets/semantic-templates", tags=["prediction-markets"])
def prediction_market_semantic_templates(session: DatabaseSession, settings: AppSettings) -> dict:
    """Inspectable review packet: one versioned template per provider contract
    family with its conditions, source hash, status and the single decision a
    human must make. Read-only; nothing is approved here."""
    from app.prediction_markets.semantics import SemanticTemplateAuthority

    authority = SemanticTemplateAuthority(session)
    authority.ensure_seeded()
    return {"templates": authority.review_packet(), "decision_count": sum(1 for t in authority.all_latest() if t.status == "HELD_CLOSED")}


@router.post(
    "/admin/prediction-markets/semantic-templates/decide",
    tags=["administration"],
    dependencies=[Depends(rate_limit("admin:pm-mappings", category="admin"))],
)
def admin_decide_semantic_template(
    session: DatabaseSession, settings: AppSettings, user: AdminUser,
    provider: str = Query(...), template_key: str = Query(...),
    status: str = Query(..., pattern="^(APPROVED|REJECTED|HELD_CLOSED)$"),
    notes: str = Query("Manual admin decision"),
):
    """Append one audited decision for a provider contract-family template.
    Exact markets satisfying every template condition inherit the decision;
    ambiguous markets stay held. Never a mutation, never a trading effect."""
    from app.models import AuditEvent
    from app.prediction_markets.semantics import SemanticTemplateAuthority

    authority = SemanticTemplateAuthority(session)
    authority.ensure_seeded()
    row = authority.decide(provider=provider, template_key=template_key, status=status,
                           decided_by_user_id=user.id, notes=notes)
    session.add(AuditEvent(
        actor_user_id=user.id, action="prediction_market.semantic_template.decided",
        entity_type="prediction_market_semantic_template", entity_id=row.id,
        metadata_json={"provider": provider, "template_key": template_key, "status": status, "version": row.version},
    ))
    session.commit()
    return {"status": row.status, "template_key": row.template_key, "version": row.version, "id": str(row.id)}


@router.get(
    "/admin/prediction-markets/mappings",
    tags=["administration"],
    dependencies=[Depends(rate_limit("admin:pm-mappings", category="admin"))],
)
def admin_prediction_market_mappings(session: DatabaseSession, settings: AppSettings, user: AdminUser):
    """Admin-only mapping/semantics review surface (no trading controls)."""
    from app.models import PredictionMarketMapping

    rows = session.execute(
        select(PredictionMarketMapping).order_by(PredictionMarketMapping.decided_at.desc()).limit(200)
    ).scalars()
    latest: dict[tuple[str, str], dict] = {}
    for m in rows:
        key = (m.provider, m.provider_event_id)
        if key in latest:
            continue
        latest[key] = {
            "provider": m.provider, "provider_event_id": m.provider_event_id,
            "canonical_game_id": str(m.canonical_game_id) if m.canonical_game_id else None,
            "mapping_status": m.mapping_status, "semantics_status": m.semantics_status,
            "reason": m.reason, "start_time_delta_seconds": m.start_time_delta_seconds,
            "candidate_game_ids": m.candidate_game_ids or [], "decided_at": m.decided_at,
        }
    return {"mappings": list(latest.values()), "count": len(latest)}


@router.post(
    "/admin/prediction-markets/mappings/approve",
    tags=["administration"],
    dependencies=[Depends(rate_limit("admin:pm-mappings", category="admin"))],
)
def admin_approve_mapping(
    session: DatabaseSession, settings: AppSettings, user: AdminUser,
    provider: str = Query(...), provider_event_id: str = Query(...),
    canonical_game_id: UUID = Query(...),
    semantics_status: str = Query("SEMANTICS_MATCHED"),
    reason: str = Query("Manual admin approval"),
):
    """Append a MANUAL_APPROVED mapping decision. Audited, append-only, reversible
    only by a NEW decision — never a mutation or deletion. No trading effect."""
    from app.models import AuditEvent, PredictionMarketMapping

    prior = session.scalar(select(PredictionMarketMapping).where(
        PredictionMarketMapping.provider == provider,
        PredictionMarketMapping.provider_event_id == provider_event_id,
    ).order_by(PredictionMarketMapping.decided_at.desc()))
    decision = PredictionMarketMapping(
        provider=provider, provider_event_id=provider_event_id, canonical_game_id=canonical_game_id,
        mapping_status="MANUAL_APPROVED", semantics_status=semantics_status, decision_source="MANUAL",
        reason=reason, decided_by_user_id=user.id, decided_at=datetime.now(UTC),
        supersedes_id=prior.id if prior else None,
    )
    session.add(decision)
    session.add(AuditEvent(
        actor_user_id=user.id, action="prediction_market.mapping.manual_approved",
        entity_type="prediction_market_mapping", entity_id=None,
        metadata_json={"provider": provider, "provider_event_id": provider_event_id,
                       "canonical_game_id": str(canonical_game_id), "semantics_status": semantics_status},
    ))
    session.commit()
    return {"status": "approved", "mapping_id": str(decision.id), "supersedes": str(prior.id) if prior else None}


# ==========================================================================
# Qualification transparency — public, DB-only reads (ZERO provider calls).
# Read-only funnel + exact active policy + starter-gate audit. Never loosens a
# threshold, never creates a position, never inspects an outcome.
# ==========================================================================
@router.get("/experiment/policy", tags=["experiment"])
def experiment_policy(session: DatabaseSession, settings: AppSettings):
    from app.services.qualification_service import QualificationService

    return QualificationService(session, settings).policy_report()


@router.get("/experiment/funnel", tags=["experiment"])
def experiment_funnel(
    session: DatabaseSession, settings: AppSettings,
    days: int | None = Query(None, ge=1, le=365),
) -> dict:
    """Where the experiment's games go, and the one reason each dropped out."""

    from app.services.experiment_funnel import ExperimentFunnelService

    return ExperimentFunnelService(session, settings).funnel(days=days)


@router.get("/experiment/qualification-funnel", tags=["experiment"])
def experiment_qualification_funnel(
    session: DatabaseSession,
    settings: AppSettings,
    event_date: date | None = Query(None, alias="date"),
    policy: Literal["v1", "v2"] = Query("v1"),
):
    from app.services.qualification_service import QualificationService

    service = QualificationService(session, settings)
    day = _today(event_date, settings)
    return service.funnel_v2(day) if policy == "v2" else service.funnel(day)


@router.get(
    "/admin/experiment/policy-counterfactual",
    tags=["administration"],
    dependencies=[Depends(rate_limit("admin:counterfactual", category="admin"))],
)
def experiment_policy_counterfactual(
    session: DatabaseSession, settings: AppSettings, user: AdminUser,
    event_date: date | None = Query(None, alias="date"),
):
    """Read-only policy counterfactual (V1 strict / probable-stable-only /
    lineup-pending-only / practical V2 / expanded-market read-only). Creates
    no position, mutates nothing, uses no hindsight outcome."""
    from app.services.policy_counterfactual import PolicyCounterfactualService

    service = PolicyCounterfactualService(session, settings)
    return service.run(dates=[event_date] if event_date else None)


@router.get("/experiment/starter-analysis", tags=["experiment"])
def experiment_starter_analysis(
    session: DatabaseSession, settings: AppSettings, event_date: date | None = Query(None, alias="date"),
):
    from app.services.qualification_service import QualificationService

    return QualificationService(session, settings).starter_gate_analysis(_today(event_date, settings))
