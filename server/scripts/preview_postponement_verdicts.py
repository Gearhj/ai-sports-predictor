"""READ-ONLY preview of AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1 on a database.

Run it BEFORE deploying the rule: the first finalize-games run after the
deploy applies it to every OPEN MLB experiment position (no flag), so this
lists exactly what that run would do -- SETTLE, VOID (early or on the makeup's
final), WAIT, or a legacy settlement it cannot verify -- plus, with
``--settled-since``, the already-settled MLB positions the rule would have
voided (candidates for the governed correction; nothing is corrected here).

It never writes: on PostgreSQL the connection is opened with
``default_transaction_read_only=on`` and every transaction is rolled back.
The database URL is read from the environment (``DATABASE_URL``) or the
project ``.env`` via Settings, and is never printed.

    python scripts/preview_postponement_verdicts.py [--settled-since 2026-09-12]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.bankroll import occurrence as occ  # noqa: E402
from app.models import (  # noqa: E402
    Game, Market, MarketOutcome, OfficialAspPosition, PositionSettlement, PositionStatus, ShadowExperiment,
    ShadowExperimentDecision,
)


def _url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    from app.core.config import Settings

    return Settings().database_url


def _row(session: Session, position: OfficialAspPosition, now: datetime) -> dict:
    decision = session.get(ShadowExperimentDecision, position.shadow_decision_id)
    outcome = session.get(MarketOutcome, position.market_outcome_id)
    market = session.get(Market, outcome.market_id) if outcome else None
    game = session.get(Game, market.game_id) if market else None
    if game is None:
        return {"position_id": str(position.id), "error": "game not resolved"}
    verdict = occ.occurrence_verdict(session, position=position, decision=decision, game=game, now=now)
    settlement = session.scalar(select(PositionSettlement).where(PositionSettlement.position_id == position.id))
    return {
        "position_id": str(position.id), "game_id": str(game.id), "status": position.status.name,
        "recorded_result": settlement.result.name if settlement else None,
        "stake": float(position.stake), "placed_at": occ._iso(position.placed_at),
        "game_status": game.status.name, "game_starts_at": occ._iso(game.starts_at),
        "verdict": verdict.kind, "reason": verdict.reason, "unverified": verdict.unverified,
        "original_et_date": verdict.original.et_date.isoformat() if verdict.original.et_date else None,
        "basis": verdict.original.basis,
        "postponement_observation_id": (str(verdict.postponement_observation_id)
                                        if verdict.postponement_observation_id else None),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--settled-since", type=date.fromisoformat, default=None,
                        help="also list SETTLED (non-VOID) MLB positions settled on/after this date")
    args = parser.parse_args()
    url = _url()
    connect_args = {"options": "-c default_transaction_read_only=on"} if url.startswith("postgresql") else {}
    engine = create_engine(url, connect_args=connect_args)
    now = datetime.now(UTC)
    out: dict = {"evaluated_at": now.isoformat(), "rule_id": occ.RULE_ID, "open": [], "settled_would_void": []}
    try:
        with Session(engine) as session:
            mlb = list(session.scalars(select(ShadowExperiment.id).where(ShadowExperiment.sport == "MLB")))
            for position in session.scalars(select(OfficialAspPosition).where(
                    OfficialAspPosition.shadow_experiment_id.in_(mlb),
                    OfficialAspPosition.status == PositionStatus.OPEN).order_by(OfficialAspPosition.placed_at)):
                out["open"].append(_row(session, position, now))
            if args.settled_since is not None:
                for position in session.scalars(select(OfficialAspPosition).where(
                        OfficialAspPosition.shadow_experiment_id.in_(mlb),
                        OfficialAspPosition.status == PositionStatus.SETTLED,
                        OfficialAspPosition.settled_at >= datetime.combine(
                            args.settled_since, datetime.min.time(), tzinfo=occ.ET),
                ).order_by(OfficialAspPosition.settled_at)):
                    row = _row(session, position, now)
                    if row.get("verdict") == occ.VOID or row.get("unverified"):
                        out["settled_would_void"].append(row)
            session.rollback()
    finally:
        engine.dispose()
    counts: dict = {}
    for row in out["open"]:
        key = f"{row.get('verdict')}:{row.get('reason')}"
        counts[key] = counts.get(key, 0) + 1
    out["open_summary"] = counts
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
