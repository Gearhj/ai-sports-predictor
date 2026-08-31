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
from app.jobs.scheduler import build_scheduler, mark_scheduler_started, mark_scheduler_stopped


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging()
    install_immutability_guards()
    scheduler = None
    settings = get_settings()
    if settings.scheduler_enabled and settings.app_env != "test":
        scheduler = build_scheduler(settings)
        scheduler.start()
        application.state.scheduler = scheduler
        mark_scheduler_started(scheduler)
    yield
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

    @application.exception_handler(SourceError)
    async def source_error_handler(request: Request, exc: SourceError) -> JSONResponse:
        return JSONResponse(
            status_code=503 if exc.retryable else 424,
            content={"detail": str(exc), "source": exc.source, "retryable": exc.retryable},
        )

    return application


app = create_app()
