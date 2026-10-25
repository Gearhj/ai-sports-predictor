from __future__ import annotations

import hashlib
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.house_divided_routes import router as house_divided_router
from app.api.routes import router
from app.audit import install_immutability_guards
from app.core.config import get_settings, settings_load_proof
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

_HEX_TOKEN = re.compile(r"^[0-9a-fA-F]{7,64}$")


def _proof_token(value: str) -> str:
    # Only bare hex tokens (commit SHAs, the supervisor's uuid4().hex) are
    # echoed; anything else is reduced to a short digest so no free-form value
    # can ever reach the log.
    value = (value or "").strip()
    if not value:
        return "EMPTY"
    if _HEX_TOKEN.match(value):
        return value
    return "sha256_12:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?\+00:00$")
_APP_ENVS = ("development", "test", "production")


def _proof_bool(value) -> str:
    return "true" if value is True else "false" if value is False else _proof_token(str(value))


def _proof_load_field(proof, key: str) -> str:
    # The load digests are 64-hex or UNREADABLE, the clocks ISO UTC; anything
    # else is digested. No recorded proof for this instance is UNRECORDED.
    if proof is None:
        return "UNRECORDED"
    value = str(proof.get(key, ""))
    if value == "UNREADABLE" or _HEX_TOKEN.match(value) or _ISO_UTC.match(value):
        return value
    return _proof_token(value)


def emit_runtime_settings_proof(settings, *, cached_before: bool | None) -> str | None:
    """Log ONE line proving which pin THIS process loaded. Never raises."""
    try:
        env_file = settings.model_config.get("env_file")
        env_path = Path(str(env_file)) if env_file else None
        try:
            env_sha = hashlib.sha256(env_path.read_bytes()).hexdigest()
        except Exception:
            env_sha = "UNREADABLE"
        override = any(
            key.upper() == "NFL_FINAL_REQUIRED_PRODUCER_REVISION" for key in os.environ
        )
        instance = os.environ.get("ASP_SCHEDULER_INSTANCE")
        load = settings_load_proof(settings)
        app_env = str(getattr(settings, "app_env", ""))
        line = (
            "RUNTIME_SETTINGS_PROOF"
            f" pid={os.getpid()} ppid={os.getppid()}"
            f" loaded_at_utc={datetime.now(timezone.utc).isoformat()}"
            " nfl_final_required_producer_revision="
            f"{_proof_token(settings.nfl_final_required_producer_revision)}"
            f" nfl_final_readiness_required={bool(settings.nfl_final_readiness_required)}"
            f" env_file_sha256={env_sha}"
            f" scheduler_instance={_proof_token(instance) if instance is not None else 'UNSET'}"
            f" settings_cached_before={cached_before}"
            f" revision_env_override={override}"
            f" settings_load_env_sha256_before={_proof_load_field(load, 'env_sha256_before')}"
            f" settings_load_env_sha256_after={_proof_load_field(load, 'env_sha256_after')}"
            f" settings_load_before_utc={_proof_load_field(load, 'before_utc')}"
            f" settings_load_after_utc={_proof_load_field(load, 'after_utc')}"
            " settings_load_interval_stable="
            f"{'true' if load is not None and load.get('interval_stable') is True else 'false'}"
            " nfl_late_checkpoint_retime_enabled="
            f"{_proof_bool(getattr(settings, 'nfl_late_checkpoint_retime_enabled', None))}"
            f" sportsgameodds_enabled={_proof_bool(getattr(settings, 'sportsgameodds_enabled', None))}"
            f" scheduler_enabled={_proof_bool(getattr(settings, 'scheduler_enabled', None))}"
            f" nfl_shadow_enabled={_proof_bool(getattr(settings, 'nfl_shadow_enabled', None))}"
            f" app_env={app_env if app_env in _APP_ENVS else _proof_token(app_env)}"
            f" env_file={env_path}"
        )
        proof_logger = logging.getLogger("app.runtime_proof")
        proof_logger.setLevel(logging.INFO)
        proof_logger.info(line)
        return line
    except Exception:
        return None


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging()
    install_immutability_guards()
    scheduler = None
    try:
        cached_before = get_settings.cache_info().currsize > 0
    except Exception:
        cached_before = None
    settings = get_settings()
    emit_runtime_settings_proof(settings, cached_before=cached_before)
    from app.services.runtime_source_proof import emit as emit_source_proof
    emit_source_proof(stage="APPLICATION_STARTUP")
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
    application.include_router(house_divided_router, prefix=settings.api_v1_prefix)

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
