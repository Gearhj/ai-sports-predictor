"""Attach final outcomes to V18 shadow forecasts.

Settlement is read-only with respect to official history: it reads the existing
``game_final_outcomes`` authority and records the result inside the shadow
forecast's own ``lineage_detail``. It never writes a game outcome, never
touches a Gen 1 forecast or decision, and never creates a position.

A settlement is written once. Re-running the command re-confirms existing
settlements rather than rewriting them, so a recorded result stays immutable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import GameFinalOutcome, ShadowForecast
from app.shadow_import.mlb_v18 import ENGINE_ID

SETTLEMENT_KEY = "settlement"


def _lineage_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def settle_shadow_forecasts(
    session: Session,
    *,
    slate_date: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record final outcomes for eligible V18 shadow forecasts."""

    now = now or datetime.now(UTC)
    query = select(ShadowForecast).where(ShadowForecast.engine_id == ENGINE_ID)
    if slate_date is not None:
        query = query.where(ShadowForecast.event_date == slate_date)

    settled: list[str] = []
    already: list[str] = []
    pending: list[dict[str, str]] = []

    for row in session.scalars(query).all():
        lineage = dict(row.lineage_detail or {})
        if lineage.get(SETTLEMENT_KEY):
            already.append(row.forecast_id)
            continue
        if row.canonical_game_id is None:
            pending.append({
                "forecastId": row.forecast_id,
                "reason": "no canonical game is bound to this forecast",
            })
            continue

        outcome = session.scalar(
            select(GameFinalOutcome)
            .where(GameFinalOutcome.game_id == row.canonical_game_id)
            .order_by(GameFinalOutcome.revision.desc())
            .limit(1)
        )
        if outcome is None:
            pending.append({
                "forecastId": row.forecast_id,
                "reason": "no final outcome is persisted for this game yet",
            })
            continue

        settlement = {
            "settlementId": str(outcome.id),
            "gameId": str(row.canonical_game_id),
            "status": str(getattr(outcome.outcome_status, "value", outcome.outcome_status)),
            "settledAt": now.isoformat(),
            "immutable": True,
            "homeRuns": outcome.home_score,
            "awayRuns": outcome.away_score,
            "extraInnings": (
                bool(outcome.innings_played and outcome.scheduled_innings
                     and outcome.innings_played > outcome.scheduled_innings)
                if outcome.innings_played is not None else None
            ),
            "homeWin": outcome.home_win,
            "outcomeRevision": outcome.revision,
            "official": outcome.official,
            "source": "AISP_GAME_FINAL_OUTCOMES",
        }
        settlement["lineageSha256"] = _lineage_sha256(settlement)
        lineage[SETTLEMENT_KEY] = settlement
        row.lineage_detail = lineage
        flag_modified(row, "lineage_detail")
        settled.append(row.forecast_id)

    session.flush()
    return {
        "engineId": ENGINE_ID,
        "slateDate": slate_date.isoformat() if slate_date else None,
        "settled": settled,
        "alreadySettled": already,
        "pending": pending,
        "officialHistoryMutated": False,
    }
