from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.audit import install_immutability_guards
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import SecurityHeadersMiddleware
from app.data_sources import SourceError
from app.jobs.scheduler import (
    build_scheduler,
    mark_scheduler_started,
    mark_scheduler_stopped,
    reconcile_abandoned_job_runs,
    scheduler_is_running,
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging()
    install_immutability_guards()
    scheduler = None
    settings = get_settings()
    if settings.scheduler_enabled and settings.app_env != "test":
        # This process is the single scheduler owner: any JobRun still RUNNING
        # was orphaned by a previous process and must be made visible as such
        # before new runs start (never left as an impossible "running forever").
        reconcile_abandoned_job_runs()
        scheduler = build_scheduler(settings)
        scheduler.start()
        application.state.scheduler = scheduler
        mark_scheduler_started(scheduler)
    warmer = None
    if settings.app_env != "test":
        import asyncio

        from app.api.read_cache import warm_forever

        warmer = asyncio.create_task(warm_forever(settings.api_v1_prefix))
    yield
    if warmer:
        warmer.cancel()
    if scheduler:
        mark_scheduler_stopped()
        scheduler.shutdown(wait=False)


def create_app(settings=None) -> FastAPI:
    settings = settings or get_settings()
    is_production = settings.app_env == "production"
    # Interactive API documentation and the machine-readable schema are
    # developer conveniences that leak the full surface; disable them in
    # production (fail closed) and keep them for local/dev work.
    docs_kwargs = (
        {"docs_url": None, "redoc_url": None, "openapi_url": None}
        if is_production
        else {}
    )
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Quantitative MLB forecasting and transparent virtual-bankroll research API.",
        lifespan=lifespan,
        **docs_kwargs,
    )
    if settings.app_env != "test":
        # Innermost: security headers and CORS still wrap every cached response.
        from app.api.read_cache import ReadCacheMiddleware

        application.add_middleware(ReadCacheMiddleware, prefix=settings.api_v1_prefix)
    application.add_middleware(SecurityHeadersMiddleware, hsts=is_production)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )
    application.include_router(router, prefix=settings.api_v1_prefix)

    @application.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @application.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        """Unauthenticated readiness probe for load balancers and supervisors.

        Reports only booleans a public probe may know (database reachable,
        scheduler owner alive) — never quota, credentials or job detail, which
        stay behind the admin-only ``/api/v1/health/sources``. 503 when the
        process must not receive traffic.
        """
        from sqlalchemy import text

        from app.db.session import SessionLocal

        checks: dict[str, str] = {}
        ready = True
        try:
            with SessionLocal() as session:
                session.execute(text("SELECT 1"))
            checks["database"] = "reachable"
        except Exception:
            checks["database"] = "unreachable"
            ready = False
        if not settings.scheduler_enabled or settings.app_env == "test":
            checks["scheduler"] = "disabled"
        elif scheduler_is_running():
            checks["scheduler"] = "running"
        else:
            checks["scheduler"] = "not_running"
            ready = False
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", **checks},
        )

    @application.exception_handler(SourceError)
    async def source_error_handler(request: Request, exc: SourceError) -> JSONResponse:
        return JSONResponse(
            status_code=503 if exc.retryable else 424,
            content={"detail": str(exc), "source": exc.source, "retryable": exc.retryable},
        )

    # Last, so the SPA fallback can never answer an API, probe or docs path.
    if settings.app_env != "test":
        from app.frontend import mount_frontend

        mount_frontend(application)

    return application


app = create_app()
