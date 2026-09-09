"""Execute exactly ONE guarded Odds API quota-recovery probe.

This is a deliberate, human-invoked, single-shot command. It spends real
provider credit, so every safeguard is checked before the request and nothing
is retried afterwards:

* The guarded path is the normal ``refresh-odds`` job, so the request is
  reserved in the provider ledger, counted against the daily budget, subject to
  the manual cooldown, and its observations are persisted through the ordinary
  ingestion path. No special-case writer exists.
* A PostgreSQL advisory lock makes concurrent probes impossible.
* The recovery guard is evaluated first and reported. If it refuses, this exits
  without contacting the provider and prints the exact reason.
* Exactly one attempt. A failure is reported, never retried, because a retry
  would be a second request.
* The quota floor is not lowered and recurring polling is not enabled.

    python -m scripts.odds_quota_recovery_probe --balance-only [--dry-run]
    python -m scripts.odds_quota_recovery_probe [--date YYYY-MM-DD] [--dry-run]

``--balance-only`` reads the balance from the documented ``/v4/sports/``
endpoint, which returns the ``x-requests-*`` headers WITHOUT consuming a usage
credit (https://the-odds-api.com/liveapi/guides/v4/#get-sports). It fetches no
odds and persists no price; it exists to replace an obsolete reading with a
confirmed one. Prefer it: the full refresh above spends an odds credit.

``--dry-run`` evaluates and prints the guard decision and makes no request.

The API key is passed to the provider as a request parameter and is never
printed, logged or included in any output of this command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.time import local_today  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.jobs.odds_quota_recovery import PROBE_ENDPOINT_KEY, quota_state  # noqa: E402
from app.jobs.orchestrator import JobOrchestrator  # noqa: E402
from app.models import IngestionRun, OddsSnapshot, ProviderRequestLedger  # noqa: E402

#: Same namespace as the V18 command: no two provider-touching jobs at once.
ADVISORY_LOCK_KEY = 0x4D4C42563138


def _state(session, settings) -> dict:
    return quota_state(
        session,
        floor=settings.odds_quota_floor,
        probe_after_hours=settings.odds_quota_probe_after_hours,
        probe_cooldown_hours=settings.odds_quota_probe_cooldown_hours,
    ).as_dict()


async def _balance_only(session, settings, before: dict) -> dict:
    """Confirm the balance without spending a usage credit.

    The request is still ledgered, so it is counted and auditable exactly like
    any other provider contact, and the credentials never reach the output.
    """

    from app.data_sources.odds import TheOddsAPIAdapter

    orchestrator = JobOrchestrator(session, settings)
    orchestrator._reserve_provider_request(
        "THE_ODDS_API",
        PROBE_ENDPOINT_KEY,
        controlled_live_request=True,
        request_shape={"reason": "QUOTA_BALANCE_CHECK", "endpoint": "v4/sports", "credit_cost": 0},
    )
    adapter = TheOddsAPIAdapter(
        api_key=settings.the_odds_api_key,
        sport_key="baseball_mlb",
    )
    try:
        response = await adapter.get_quota_balance()
    except Exception as exc:  # noqa: BLE001 - reported once, never retried
        session.rollback()
        return {
            "command": "odds_quota_recovery_probe",
            "mode": "BALANCE_ONLY_ZERO_CREDIT",
            "requested": True,
            "balance_confirmed": False,
            # Type only: a provider message could echo the key.
            "error": type(exc).__name__,
            "state_before": before,
        }
    metadata = dict(response.envelope.metadata or {})
    session.commit()
    return {
        "command": "odds_quota_recovery_probe",
        "mode": "BALANCE_ONLY_ZERO_CREDIT",
        "requested": True,
        "credit_cost": 0,
        "balance_confirmed": metadata.get("requests_remaining") is not None,
        "requests_remaining": metadata.get("requests_remaining"),
        "requests_used": metadata.get("requests_used"),
        "quota_floor": settings.odds_quota_floor,
        "odds_persisted": 0,
        "state_before": before,
    }


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="odds_quota_recovery_probe")
    parser.add_argument("--date", help="Operational date (YYYY-MM-DD).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--balance-only", action="store_true",
        help="Read the quota balance from the zero-credit /v4/sports/ endpoint.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    event_date = (
        date.fromisoformat(args.date) if args.date
        else local_today(settings.display_timezone)
    )

    with SessionLocal() as session:
        if not session.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        ):
            print(json.dumps({"refused": "another provider job holds the lock"}))
            return 2
        try:
            before = _state(session, settings)
            snapshots_before = session.scalar(
                select(OddsSnapshot.id).order_by(OddsSnapshot.observed_at.desc()).limit(1)
            )
            observations_before = session.scalar(
                text("SELECT count(*) FROM odds_snapshots")
            )

            if not before["probe_allowed"]:
                print(json.dumps({
                    "command": "odds_quota_recovery_probe",
                    "requested": False,
                    "refused_by_guard": True,
                    "reason": before["probe_blocked_reason"],
                    "state_before": before,
                }, indent=2))
                return 3

            if args.dry_run:
                print(json.dumps({
                    "command": "odds_quota_recovery_probe",
                    "requested": False,
                    "dry_run": True,
                    "mode": "BALANCE_ONLY_ZERO_CREDIT" if args.balance_only
                            else "FULL_ODDS_REFRESH_SPENDS_ONE_CREDIT",
                    "state_before": before,
                }, indent=2))
                return 0

            if args.balance_only:
                # One request to the documented zero-credit endpoint. It reads
                # the quota headers only: no odds are fetched and no price is
                # persisted, so nothing downstream can mistake it for a quote.
                payload = await _balance_only(session, settings, before)
                print(json.dumps(payload, indent=2))
                return 0 if payload.get("balance_confirmed") else 4

            error: str | None = None
            result: dict | None = None
            try:
                # Exactly one attempt through the ordinary guarded job path.
                result = await JobOrchestrator(session, settings).refresh_odds(
                    event_date, controlled_live_request=True
                )
                session.commit()
            except Exception as exc:  # noqa: BLE001 - reported, never retried
                session.rollback()
                error = f"{type(exc).__name__}: {exc}"

            latest_run = session.scalar(
                select(IngestionRun)
                .where(IngestionRun.source == "THE_ODDS_API")
                .order_by(IngestionRun.started_at.desc())
                .limit(1)
            )
            ledger_rows = session.scalars(
                select(ProviderRequestLedger)
                .where(ProviderRequestLedger.provider == "THE_ODDS_API")
                .order_by(ProviderRequestLedger.requested_at.desc())
                .limit(4)
            ).all()
            observations_after = session.scalar(
                text("SELECT count(*) FROM odds_snapshots")
            )

            payload = {
                "command": "odds_quota_recovery_probe",
                "requested": True,
                "attempts": 1,
                "operational_date": event_date.isoformat(),
                "error": error,
                "job_result": result,
                "state_before": before,
                "state_after": _state(session, settings),
                "latest_run": (
                    {
                        "status": str(getattr(latest_run.status, "value", latest_run.status)),
                        "started_at": latest_run.started_at.isoformat() if latest_run.started_at else None,
                        "completed_at": latest_run.completed_at.isoformat() if latest_run.completed_at else None,
                        "requests_remaining": (latest_run.source_metadata or {}).get("requests_remaining"),
                        "requests_used": (latest_run.source_metadata or {}).get("requests_used"),
                        "requests_last": (latest_run.source_metadata or {}).get("requests_last"),
                        "event_count": (latest_run.source_metadata or {}).get("event_count"),
                    }
                    if latest_run else None
                ),
                "ledger": [
                    {
                        "requested_at": row.requested_at.isoformat(),
                        "endpoint_key": row.endpoint_key,
                        "status": row.status,
                        "quota_cost": row.quota_cost,
                        "quota_remaining": row.quota_remaining,
                        "records_received": row.records_received,
                        "controlled_live_request": row.controlled_live_request,
                    }
                    for row in ledger_rows
                ],
                "odds_snapshots_before": observations_before,
                "odds_snapshots_after": observations_after,
                "odds_snapshots_persisted": (observations_after or 0) - (observations_before or 0),
                "quota_floor_unchanged": settings.odds_quota_floor,
                "odds_polling_enabled": settings.odds_polling_enabled,
            }
            print(json.dumps(payload, indent=2, default=str))
            _ = snapshots_before
            return 0 if error is None else 1
        finally:
            session.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
            )
            session.commit()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
