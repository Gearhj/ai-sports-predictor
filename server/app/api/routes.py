from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from app.services.auth_service import (
    AuthenticationError,
    DuplicateEmailError,
    authenticate_user,
    public_user,
    register_user,
    revoke_user_tokens,
)
from app.services.read_service import ReadService
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


@router.get("/games/{game_id}", response_model=GameAnalysisData, tags=["forecasts"])
def game_analysis(game_id: UUID, session: DatabaseSession, settings: AppSettings) -> GameAnalysisData:
    result = ReadService(session, settings).game_analysis(game_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
    return result


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
def bankroll(session: DatabaseSession, settings: AppSettings) -> BankrollData:
    return ReadService(session, settings).bankroll()


@router.get("/shadow-board", response_model=ShadowBoardData, tags=["transparency"])
def shadow_board(
    session: DatabaseSession,
    settings: AppSettings,
    event_date: date | None = Query(None, alias="date"),
) -> ShadowBoardData:
    # Read-only research-shadow board. Reads persisted decisions only; it never
    # calls a provider and never exposes executable or promotion authority.
    from app.services.shadow_board_service import ShadowBoardService

    day = event_date or local_today(settings.display_timezone)
    return ShadowBoardService(session, settings).board(day)


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
