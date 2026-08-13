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
from app.jobs.scheduler import build_scheduler


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
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Quantitative MLB forecasting and transparent virtual-bankroll research API.",
        lifespan=lifespan,
    )
    application.add_middleware(SecurityHeadersMiddleware)
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
