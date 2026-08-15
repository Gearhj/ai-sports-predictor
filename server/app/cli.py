from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config

from app.audit import install_immutability_guards
from app.core.config import get_settings
from app.db.session import SessionLocal, get_engine


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return str(value)


async def _run(command: str, event_date: date | None) -> dict[str, Any]:
    # Keep provider-capable operational modules outside the import graph of the
    # dedicated provider-free FD1 recovery command.
    from app.jobs import JobOrchestrator, seed_demo_data
    from app.services.auth_service import ensure_admin

    settings = get_settings()
    _upgrade_database(settings.database_url)
    install_immutability_guards()
    with SessionLocal() as session:
        admin = ensure_admin(session, settings)
        if command == "seed-demo":
            result = await seed_demo_data(session, settings, event_date)
        elif command == "run-pipeline":
            run = await JobOrchestrator(session, settings).run("daily-pipeline", requested_by_user_id=admin.id, event_date=event_date)
            result = {"job_id": str(run.id), "status": run.status.value, "result": run.result}
        elif command == "controlled-live-validation":
            run = await JobOrchestrator(session, settings).run(
                "controlled-live-validation",
                requested_by_user_id=admin.id,
                event_date=event_date,
            )
            result = {"job_id": str(run.id), "status": run.status.value, "result": run.result}
        elif command == "create-admin":
            result = {"admin_id": str(admin.id), "email": admin.email, "password": "configured via ADMIN_PASSWORD"}
        else:
            result = {"status": "database initialized"}
    return result


def _run_training(training_cutoff: datetime | None) -> dict[str, Any]:
    """Run provider-free model development against an already-migrated database.

    Unlike operational commands, this path deliberately does not upgrade the
    database, create an administrator, start jobs, or construct data adapters.
    """

    from app.training.pipeline import Gate3TrainingPipeline

    settings = get_settings()
    install_immutability_guards()
    with SessionLocal() as session:
        result = Gate3TrainingPipeline(session, settings).run(training_cutoff=training_cutoff)
    return asdict(result)


async def _run_historical_backfill(args: argparse.Namespace) -> dict[str, Any]:
    """Run the governed Gate 3A A1 core archive/canonical path."""

    from app.historical.backfill import (
        HistoricalBackfillOptions,
        build_default_backfill_runner,
    )
    from app.historical.persistence import (
        A1_GOVERNANCE_DECISION,
        HistoricalCanonicalPersistenceService,
    )

    settings = get_settings()
    options = HistoricalBackfillOptions(
        start_date=args.start_date,
        end_date=args.end_date,
        season=args.season,
        resume=args.resume,
        source=args.source,
        chunk_size=args.chunk_size,
        dry_run=args.dry_run,
        discovery_only=args.discovery_only,
        expected_final_games=args.expected_final_games,
        max_requests=args.max_requests,
        # Optional sources remain deliberately disabled for the A1 core pass.
        skip_statcast=True,
        skip_weather=True,
        terms_status=("APPROVED" if args.approved_local_research else "LEGAL_REVIEW_REQUIRED"),
    )
    install_immutability_guards()
    with SessionLocal() as session:
        result = await build_default_backfill_runner(
            settings.raw_data_root,
            canonical_writer=HistoricalCanonicalPersistenceService(session),
            governance=A1_GOVERNANCE_DECISION,
        ).run(options)
    return asdict(result)


def _run_historical_report(season: int | None) -> dict[str, Any]:
    """Read local Gate 3A state without migrations, jobs, or source adapters."""

    from app.historical.reporting import HistoricalReportPaths, build_mlb_history_report

    settings = get_settings()
    paths = HistoricalReportPaths(
        raw_root=settings.raw_data_root,
        curated_root=settings.curated_data_root,
        feature_root=settings.feature_data_root,
        runtime_root=settings.gate3_artifact_root,
    )
    with SessionLocal() as session:
        return build_mlb_history_report(session, paths=paths, season=season)


def _select_historical_reconstruction_targets(
    session: Any,
    *,
    start_date: date,
    end_date: date,
) -> list[Any]:
    """Select the latest final revision by MLB official schedule date.

    ``scheduled_at`` is UTC and checkpoint windows may span several official
    dates, so neither is a safe MLB day boundary. A1 persists the provider's
    explicit ``officialDate`` for this exact bounded selection.
    """
    from sqlalchemy import select

    from app.models import GameFinalOutcome

    outcomes = list(session.scalars(
        select(GameFinalOutcome)
        .where(
            GameFinalOutcome.source_mode == "LIVE",
            GameFinalOutcome.temporal_semantics == "RETROSPECTIVE_RECONSTRUCTION",
            GameFinalOutcome.outcome_status == "FINAL",
            GameFinalOutcome.official.is_(True),
        )
        .order_by(
            GameFinalOutcome.game_id,
            GameFinalOutcome.revision.desc(),
            GameFinalOutcome.retrieved_at.desc(),
            GameFinalOutcome.id.desc(),
        )
    ))
    latest_by_game: dict[Any, Any] = {}
    for outcome in outcomes:
        latest_by_game.setdefault(outcome.game_id, outcome)
    return sorted(
        (
            outcome
            for outcome in latest_by_game.values()
            if outcome.official_date is not None
            and start_date <= outcome.official_date <= end_date
        ),
        key=lambda row: (row.official_date, row.scheduled_at, str(row.game_id)),
    )


def _run_historical_reconstruction(args: argparse.Namespace) -> dict[str, Any]:
    """Materialize a bounded local historical population without providers/models."""

    from app.historical.reconstruction import (
        HistoricalReconstructionService,
        ReconstructionPopulation,
        build_and_persist_historical_dataset,
    )
    from app.models import Game

    settings = get_settings()
    install_immutability_guards()
    population = ReconstructionPopulation(args.population)
    computed_at = datetime.now(UTC)
    with SessionLocal() as session:
        outcomes = _select_historical_reconstruction_targets(
            session,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        service = HistoricalReconstructionService(session)
        materializations = []
        for outcome in outcomes:
            game = session.get(Game, outcome.game_id)
            if game is None:
                raise RuntimeError("Historical outcome references a missing game")
            materializations.append(service.materialize_game(
                game,
                population=population,
                computed_at=computed_at,
                code_revision=args.code_revision,
            ))
        dataset = build_and_persist_historical_dataset(
            session,
            population=population,
            artifact_root=settings.gate3_artifact_root / "datasets",
            training_cutoff=computed_at,
            code_revision=args.code_revision,
        )
        session.commit()
        built = sum(row.reconstruction.status == "BUILT" for row in materializations)
        persisted = sum(row.persistence is not None for row in materializations)
        reasons: dict[str, int] = {}
        for row in materializations:
            if row.reconstruction.reason:
                reasons[row.reconstruction.reason] = reasons.get(row.reconstruction.reason, 0) + 1
        return {
            "status": dataset.status,
            "population": population.value,
            "requested_start_date": args.start_date.isoformat(),
            "requested_end_date": args.end_date.isoformat(),
            "computed_at": computed_at.isoformat(),
            "target_games": len(outcomes),
            "snapshots_built": built,
            "materializations_persisted_or_reused": persisted,
            "insufficient_reasons": reasons,
            "dataset_rows": dataset.build.manifest.row_count,
            "dataset_excluded_rows": dataset.build.manifest.excluded_row_count,
            "dataset_id": dataset.build.manifest.dataset_id,
            "artifact_uri": dataset.artifact.artifact_uri if dataset.artifact else None,
            "artifact_checksum": dataset.artifact.checksum if dataset.artifact else None,
            "database_manifest_id": str(dataset.manifest.id) if dataset.manifest else None,
            "provider_calls": 0,
            "model_training_invoked": False,
        }


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _upgrade_database(database_url: str) -> None:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def _run_fd1_recovery(args: argparse.Namespace) -> dict[str, Any]:
    """Finalize only the frozen interrupted FD1 Parquet corpus."""

    from uuid import UUID

    from app.gen2.fd1_recovery import (
        FD1RecoveryOptions,
        finalize_fd1_from_existing_parquet,
    )

    settings = get_settings()
    recovery_engine = get_engine(settings.database_url)
    try:
        options = FD1RecoveryOptions(
            manifest_path=args.manifest,
            store_root=(
                args.store_root
                if args.store_root is not None
                else settings.curated_data_root / "gen2-fd1"
            ),
            resume=args.resume,
            validate_only=args.validate_only,
            sample_rehearsal=args.sample_rehearsal,
            max_runtime_minutes=args.max_runtime_minutes,
            heartbeat_seconds=args.heartbeat_seconds,
            identity_batch_size=args.identity_batch_size,
            run_id=UUID(args.run_id) if args.run_id else None,
            active_identity_report=args.active_identity_report,
        )
        return finalize_fd1_from_existing_parquet(
            recovery_engine,
            options=options,
        ).as_dict()
    finally:
        recovery_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Sports Predictor backend operations")
    parser.add_argument(
        "command",
        choices=[
            "init-db",
            "seed-demo",
            "run-pipeline",
            "controlled-live-validation",
            "create-admin",
            "train-mlb-models",
            "backfill-mlb-history",
            "build-mlb-history-dataset",
            "report-mlb-history",
            "finalize-fd1-from-existing-parquet",
        ],
    )
    parser.add_argument("--date", dest="event_date", type=date.fromisoformat, help="Event date (YYYY-MM-DD)")
    parser.add_argument(
        "--as-of",
        dest="training_cutoff",
        type=_parse_datetime,
        help="Inclusive evidence cutoff for training data (ISO-8601; defaults to now)",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, help="Historical start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=date.fromisoformat, help="Historical end date (YYYY-MM-DD)")
    parser.add_argument("--season", type=int, help="MLB season for the requested range")
    parser.add_argument("--resume", action="store_true", help="Skip already-completed semantic chunks")
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Content-addressed FD1 recovery manifest",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the frozen warehouse without final-table promotion",
    )
    parser.add_argument(
        "--sample-rehearsal",
        action="store_true",
        help="Run the bounded COPY/promotion rehearsal and roll it back",
    )
    parser.add_argument(
        "--max-runtime-minutes",
        type=int,
        default=120,
        help="Hard recovery stop, capped at 120 minutes",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=30,
        help="Visible recovery heartbeat interval, capped at 30 seconds",
    )
    parser.add_argument(
        "--identity-batch-size",
        type=int,
        default=100_000,
        help="Bounded PostgreSQL COPY transaction size",
    )
    parser.add_argument("--run-id", help="Expected content-derived recovery UUID")
    parser.add_argument(
        "--active-identity-report",
        type=Path,
        help="Checksummed active-era identity coverage evidence",
    )
    parser.add_argument(
        "--store-root",
        type=Path,
        help="Governed existing FD1 Parquet root",
    )
    parser.add_argument("--source", choices=["MLB_STATS_API"], default="MLB_STATS_API")
    parser.add_argument("--chunk-size", type=int, default=1, help="Canonical/checkpoint chunk size in days")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; construct no adapter")
    parser.add_argument(
        "--discovery-only",
        action="store_true",
        help="Make one schedule-range call, archive it, and report the exact final-game count",
    )
    parser.add_argument(
        "--expected-final-games",
        type=int,
        help="Exact final-game count authorized for one feed call per game",
    )
    parser.add_argument(
        "--approved-local-research",
        action="store_true",
        help="Acknowledge the recorded 2026-08-13 project-owner local-research approval",
    )
    parser.add_argument("--max-requests", type=int, default=0, help="Hard cap on physical HTTP attempts")
    parser.add_argument(
        "--population",
        choices=["TIER_A_STRICT", "TIER_B_ENHANCED"],
        default="TIER_A_STRICT",
        help="Explicit reconstructed historical population",
    )
    parser.add_argument(
        "--code-revision",
        help="Required immutable code/content revision for historical materialization",
    )
    args = parser.parse_args()
    if args.command == "finalize-fd1-from-existing-parquet":
        if args.manifest is None:
            parser.error(
                "finalize-fd1-from-existing-parquet requires --manifest"
            )
        try:
            result = _run_fd1_recovery(args)
        except Exception as exc:
            from app.gen2.fd1_recovery_progress import FD1RecoveryStop

            if not isinstance(exc, FD1RecoveryStop):
                raise
            print(
                json.dumps(
                    {
                        "status": "STOPPED",
                        "reason": exc.reason,
                        "resume_token": exc.resume_token,
                        "exit_code": exc.exit_code,
                    },
                    indent=2,
                )
            )
            raise SystemExit(exc.exit_code) from exc
    elif args.command == "backfill-mlb-history":
        missing = [
            option
            for option in ("start_date", "end_date", "season")
            if getattr(args, option) is None
        ]
        if missing:
            parser.error(
                "backfill-mlb-history requires "
                + ", ".join("--" + value.replace("_", "-") for value in missing)
            )
        result = asyncio.run(_run_historical_backfill(args))
    elif args.command == "report-mlb-history":
        result = _run_historical_report(args.season)
    elif args.command == "build-mlb-history-dataset":
        if args.start_date is None or args.end_date is None or not args.code_revision:
            parser.error(
                "build-mlb-history-dataset requires --start-date, --end-date, and --code-revision"
            )
        if args.end_date < args.start_date:
            parser.error("--end-date must be on or after --start-date")
        result = _run_historical_reconstruction(args)
    elif args.command == "train-mlb-models":
        result = _run_training(args.training_cutoff)
    else:
        result = asyncio.run(_run(args.command, args.event_date))
    print(json.dumps(result, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
