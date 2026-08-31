"""Bounded CLI for the Prediction Market Observer.

    python -m app.prediction_markets.cli discover
    python -m app.prediction_markets.cli refresh
    python -m app.prediction_markets.cli backfill --days 1
    python -m app.prediction_markets.cli verify        # read-only; no provider I/O

`discover` / `refresh` / `backfill` perform provider I/O only when the observer
and the relevant provider flags are armed; otherwise they no-op. `verify` never
touches a provider — it prints persisted health and boundary checks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

from app.core.config import get_settings
from app.core.time import local_today
from app.db.session import SessionLocal
from app.jobs.orchestrator import JobOrchestrator


async def _run_job(job_name: str) -> dict:
    settings = get_settings()
    with SessionLocal() as session:
        run = await JobOrchestrator(session, settings).run(
            job_name, event_date=local_today(settings.display_timezone)
        )
        return {"job": run.job_name, "status": run.status.value, "result": run.result}


def _verify() -> dict:
    settings = get_settings()
    with SessionLocal() as session:
        from app.prediction_markets.read_service import PredictionMarketReadService

        health = PredictionMarketReadService(session, settings).health_summary()
        return {
            "classification": health.classification,
            "observer_enabled": health.observer_enabled,
            "public_display_enabled": health.public_display_enabled,
            "providers": [p.model_dump() for p in health.providers],
            "unresolved_mappings": health.unresolved_mappings,
            "review_required_mappings": health.review_required_mappings,
            "semantics_review_count": health.semantics_review_count,
            "stale_markets": health.stale_markets,
            "total_contracts": health.total_contracts,
            "total_snapshots": health.total_snapshots,
            "note": "verify is read-only: no Polymarket/Kalshi request was made.",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Sports Predictor — Prediction Market Observer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="Discover Polymarket + Kalshi sports markets (armed providers only)")
    sub.add_parser("refresh", help="Refresh order-book snapshots for active contracts")
    bf = sub.add_parser("backfill", help="Bounded historical backfill via official history endpoints")
    bf.add_argument("--days", type=int, default=1)
    sub.add_parser("verify", help="Read-only health + boundary verification (no provider I/O)")
    args = parser.parse_args()

    if args.command == "discover":
        out = asyncio.run(_run_job("discover-prediction-markets"))
    elif args.command == "refresh":
        out = asyncio.run(_run_job("refresh-prediction-market-snapshots"))
    elif args.command == "backfill":
        out = asyncio.run(_run_job("backfill-prediction-market-history"))
    elif args.command == "verify":
        out = _verify()
    else:  # pragma: no cover
        parser.error("unknown command")
        return
    print(json.dumps(out, default=str, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
