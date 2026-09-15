"""The Show Board read model served to the page. Read-only.

Mode, chosen per slate date:
  FROZEN         a snapshot exists: its stored payload, verified against its identity
  DRAFT          before the planned freeze: built now from persisted evidence
  RECONSTRUCTED  after the planned freeze with no snapshot: rebuilt from evidence
                 cut at the planned freeze time, labelled as not frozen

The page response carries a compact board (complete, non-stale pairs and
counts); every leg with its snapshot id, retrieval time and request match is
served per game on demand, so the first view stays small.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.show_preview.board import build_board
from app.show_preview.clock import clock_for, next_relevant_slate_date, planned_freeze_utc
from app.show_preview.contract import FORECAST_CONTRACT
from app.show_preview.freeze import current_snapshot, preview_vs_final, snapshots_for, verify_snapshot

RESPONSE_SCHEMA = "AISP_SHOW_BOARD_RESPONSE_V1"


def flags(settings: Settings) -> dict[str, bool]:
    return {
        "show_preview_enabled": bool(settings.show_preview_enabled),
        "provider_collection_enabled": bool(settings.show_preview_enabled and settings.show_preview_provider_collection_enabled),
        "auto_freeze_enabled": bool(settings.show_preview_enabled and settings.show_preview_auto_freeze_enabled),
    }


def _lite_game(game: dict[str, Any]) -> dict[str, Any]:
    markets = {}
    for kind, pairs in game["markets"].items():
        usable = [p for p in pairs if p["complete"] and not p["stale"]]
        markets[kind] = {
            "pairs": [{"sportsbook": p["sportsbook"], "provider": p["provider"], "classification": p["classification"],
                       "line": p["line"], "older_leg_observed_utc": p["older_leg_observed_utc"], "no_vig_home": p["no_vig_home"],
                       "settlement": p["settlement"], "legs": [{"side": leg["side"], "american": leg["american"], "point": leg["point"]}
                                                               for leg in p["legs"]]} for p in usable],
            "complete": len(usable),
            "stale": sum(1 for p in pairs if p["complete"] and p["stale"]),
            "partial": sum(1 for p in pairs if not p["complete"]),
            "latest_capture_utc": max((p["older_leg_observed_utc"] for p in pairs if p["older_leg_observed_utc"]), default=None),
        }
    return {**game, "markets": markets}


def show_board_response(session: Session, settings: Settings, *, slate_date: date | None, now: datetime | None = None) -> dict[str, Any]:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    default = next_relevant_slate_date(session, moment, settings)
    target = slate_date or default
    freeze_at = planned_freeze_utc(target, settings)
    snapshot = current_snapshot(session, target)
    history = snapshots_for(session, target)
    if snapshot is not None:
        mode, board = "FROZEN", snapshot.payload
    elif moment >= freeze_at:
        mode, board = "RECONSTRUCTED", build_board(session, target, settings, evidence_as_of=freeze_at)
    else:
        mode, board = "DRAFT", build_board(session, target, settings, evidence_as_of=moment)
    kickoffs = [g["kickoff_utc"] for g in board["games"] if g["kickoff_utc"]]
    comparison = preview_vs_final(session, board, now=moment) if kickoffs and moment.isoformat() >= min(kickoffs) else []
    return {
        "schema_version": RESPONSE_SCHEMA,
        "generated_at_utc": moment.isoformat(),
        "slate_date": target.isoformat(),
        "default_slate_date": default.isoformat(),
        "mode": mode,
        "mode_label": {"FROZEN": "Frozen show snapshot", "DRAFT": "Draft show board",
                       "RECONSTRUCTED": "Reconstructed from retained evidence — not a frozen snapshot"}[mode],
        "planned_freeze_utc": freeze_at.isoformat(),
        "flags": flags(settings),
        "snapshot": None if snapshot is None else {
            "snapshot_sha256": snapshot.snapshot_sha256, "frozen_utc": snapshot.frozen_utc.isoformat(),
            "trigger": snapshot.trigger, "verified": verify_snapshot(snapshot),
            "predecessor_sha256": next((h.snapshot_sha256 for h in history if h.id == snapshot.predecessor_id), None),
            "supersede_reason": snapshot.supersede_reason, "payload_bytes": snapshot.payload_bytes,
        },
        "history": [{"snapshot_sha256": h.snapshot_sha256, "frozen_utc": h.frozen_utc.isoformat(), "trigger": h.trigger,
                     "predecessor": bool(h.predecessor_id)} for h in history],
        "board": {**board, "games": [_lite_game(g) for g in board["games"]], "clock": clock_for(target, settings)},
        "preview_vs_final": comparison,
        "provider_calls_made": 0,
    }


def show_board_game(session: Session, settings: Settings, *, slate_date: date, game_key: str, now: datetime | None = None) -> dict[str, Any] | None:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    snapshot = current_snapshot(session, slate_date)
    freeze_at = planned_freeze_utc(slate_date, settings)
    board = snapshot.payload if snapshot else build_board(session, slate_date, settings, evidence_as_of=min(moment, freeze_at) if moment >= freeze_at else moment)
    game = next((g for g in board["games"] if g["game_key"] == game_key), None)
    if game is None:
        return None
    return {"schema_version": "AISP_SHOW_BOARD_GAME_V1", "slate_date": slate_date.isoformat(),
            "evidence_as_of_utc": board["evidence_as_of_utc"], "snapshot_sha256": snapshot.snapshot_sha256 if snapshot else None,
            "game": game, "provider_calls_made": 0}


def contract_document(settings: Settings) -> dict[str, Any]:
    return {"schema_version": "AISP_SHOW_PREVIEW_CONTRACTS_V1", "forecast": FORECAST_CONTRACT, "flags": flags(settings),
            "clock_local": [{"key": s["key"], "label": s["label"], "local_time": s["local_time"], "provider_calls": s["provider_calls"],
                             "implemented": s["implemented"], "note": s["note"]} for s in clock_for(date.today(), settings)]}
