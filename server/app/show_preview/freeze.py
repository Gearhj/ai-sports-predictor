"""Freeze, verify, supersede and compare Show Preview snapshots.

IDENTITY
    A snapshot's identity is the SHA-256 of its canonical EVIDENCE: the board
    payload without the values that describe the act of freezing -- the cut
    instant, the clock and ages measured from it. Re-freezing unchanged evidence therefore
    returns the existing snapshot instead of creating a duplicate, and any change
    in a forecast, a price or a disclosure yields a different identity.

IMMUTABILITY
    Snapshot rows and their bindings are append-only (process-wide ORM guard).
    ``verify_snapshot`` recomputes the identity from the stored payload, so an
    edit made outside the ORM is detectable.

SUCCESSION
    Once a slate has a current snapshot, a different board is refused unless a
    reason is given; then a successor is written that points back to its
    predecessor. The predecessor is never touched.

AUTHORITY
    Freezing requires ``show_preview_enabled``. Nothing here reads or writes the
    final-pregame decision, position, bankroll or settlement tables except the
    read-only preview-versus-final comparison.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Game, OfficialAspPosition, PositionSettlement, ShadowExperimentDecision
from app.models.show_preview import (
    ShowBoardDraft, ShowPreviewDisclosure, ShowPreviewFinalComparison, ShowPreviewGameBinding,
    ShowPreviewMarketBinding, ShowPreviewSnapshot,
)
from app.show_preview.board import BOARD_SCHEMA, build_board
from app.show_preview.clock import planned_freeze_utc
from app.show_preview.contract import canonical_bytes, sha256_hex

#: Values that describe the act of freezing rather than the evidence: the cut
#: instant, the clock, and ages measured from that instant. Stripped at every
#: depth before hashing. A quote crossing the staleness limit still changes the
#: identity, because ``stale`` is kept.
VOLATILE_KEYS = frozenset({"evidence_as_of_utc", "clock", "older_leg_age_minutes"})


class ShowPreviewDisabled(RuntimeError):
    """The Show Preview workflow is switched off; nothing was written."""


class SnapshotAlreadyFrozen(RuntimeError):
    """The slate already has a different current snapshot and no supersede reason was given."""


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def evidence_identity(payload: dict[str, Any]) -> str:
    return sha256_hex(canonical_bytes(_strip(payload)))


def verify_snapshot(snapshot: ShowPreviewSnapshot) -> bool:
    return evidence_identity(snapshot.payload) == snapshot.snapshot_sha256


def snapshots_for(session: Session, slate_date: date) -> list[ShowPreviewSnapshot]:
    return list(session.scalars(
        select(ShowPreviewSnapshot).where(ShowPreviewSnapshot.slate_date == slate_date)
        .order_by(ShowPreviewSnapshot.frozen_utc, ShowPreviewSnapshot.created_at)
    ))


def current_snapshot(session: Session, slate_date: date) -> ShowPreviewSnapshot | None:
    rows = snapshots_for(session, slate_date)
    superseded = {row.predecessor_id for row in rows if row.predecessor_id is not None}
    heads = [row for row in rows if row.id not in superseded]
    return heads[-1] if heads else None


@dataclass
class FreezeResult:
    snapshot: ShowPreviewSnapshot
    created: bool
    superseded: ShowPreviewSnapshot | None


def freeze_show_board(session: Session, slate_date: date, settings: Settings, *, now: datetime, trigger: str,
                      supersede_reason: str | None = None) -> FreezeResult:
    if not settings.show_preview_enabled:
        raise ShowPreviewDisabled("SHOW_PREVIEW_ENABLED is false; no snapshot was frozen")
    moment = now.astimezone(UTC)
    payload = build_board(session, slate_date, settings, evidence_as_of=moment)
    identity = evidence_identity(payload)

    existing = session.scalar(select(ShowPreviewSnapshot).where(ShowPreviewSnapshot.snapshot_sha256 == identity))
    if existing is not None:
        return FreezeResult(existing, False, None)
    current = current_snapshot(session, slate_date)
    if current is not None and not supersede_reason:
        raise SnapshotAlreadyFrozen(
            f"slate {slate_date} already has snapshot {current.snapshot_sha256[:12]}; give a reason to supersede it")

    body = canonical_bytes(payload)
    snapshot = ShowPreviewSnapshot(
        snapshot_sha256=identity, schema_version=BOARD_SCHEMA, slate_date=slate_date, frozen_utc=moment,
        planned_freeze_utc=planned_freeze_utc(slate_date, settings), evidence_as_of_utc=moment,
        payload_bytes=len(body), payload=payload, predecessor_id=current.id if current else None,
        supersede_reason=supersede_reason if current else None, trigger=trigger,
    )
    session.add(snapshot)
    session.flush()
    ranks = {row["game_key"]: row["rank"] for row in payload["ranking"]}
    for game in payload["games"]:
        comparison = game["comparison"] or {}
        session.add(ShowPreviewGameBinding(
            snapshot_id=snapshot.id, game_key=game["game_key"],
            canonical_game_id=_uuid(game["canonical_game_id"]), sport=game["sport"],
            kickoff_utc=_when(game["kickoff_utc"]), readiness=game["readiness"],
            forecast_row_id=_uuid((game["forecast"] or {}).get("row_id")),
            forecast_content_sha256=(game["forecast"] or {}).get("content_sha256"),
            preview_rank=ranks.get(game["game_key"]), preview_selection=comparison.get("selection"),
            model_probability=comparison.get("model_probability"), no_vig_probability=comparison.get("no_vig_probability"),
            preview_difference=comparison.get("difference"), expected_return=comparison.get("expected_return"),
        ))
        for kind, pairs in game["markets"].items():
            for pair in pairs:
                session.add(ShowPreviewMarketBinding(
                    snapshot_id=snapshot.id, game_key=game["game_key"], market_type=kind, line_key=str(pair["line_key"]),
                    sportsbook_key=pair["sportsbook_key"], provider=pair["provider"], classification=pair["classification"],
                    complete=pair["complete"], stale=pair["stale"], older_leg_observed_utc=_when(pair["older_leg_observed_utc"]),
                    legs=pair["legs"], settlement=pair["settlement"],
                ))
    for item in payload["disclosures"]:
        session.add(ShowPreviewDisclosure(snapshot_id=snapshot.id, game_key=item.get("game_key"),
                                          code=item["code"], message=item["message"][:400]))
    session.flush()
    return FreezeResult(snapshot, True, current)


def save_draft(session: Session, slate_date: date, settings: Settings, *, now: datetime) -> ShowBoardDraft:
    """Persist the working draft for a slate. Replaceable; requires the workflow switch."""

    if not settings.show_preview_enabled:
        raise ShowPreviewDisabled("SHOW_PREVIEW_ENABLED is false; no draft was saved")
    payload = build_board(session, slate_date, settings, evidence_as_of=now)
    draft = session.scalar(select(ShowBoardDraft).where(ShowBoardDraft.slate_date == slate_date))
    if draft is None:
        draft = ShowBoardDraft(slate_date=slate_date)
        session.add(draft)
    draft.built_utc = now
    draft.evidence_as_of_utc = now
    draft.payload = payload
    draft.content_sha256 = evidence_identity(payload)
    session.flush()
    return draft


# --------------------------------------------------------- preview vs final --

def _uuid(value: Any) -> Any:
    import uuid
    return uuid.UUID(str(value)) if value else None


def _when(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _team_of(selection: str | None, game: dict[str, Any]) -> str | None:
    s = (selection or "").upper().replace(" ML", "").strip()
    if s == "HOME":
        return game["home"]["abbreviation"]
    if s == "AWAY":
        return game["away"]["abbreviation"]
    return s or None


def preview_vs_final(session: Session, payload: dict[str, Any], *, now: datetime) -> list[dict[str, Any]]:
    """Pair each previewed game with its final-pregame evidence, when that exists. Read-only.

    Neutral by design: a different selection or price on game day is recorded as
    a change, never as a preview error.
    """

    games = {g["canonical_game_id"]: g for g in payload["games"] if g["canonical_game_id"]}
    if not games:
        return []
    ids = [_uuid(key) for key in games]
    decisions = list(session.scalars(
        select(ShadowExperimentDecision).where(ShadowExperimentDecision.game_id.in_(ids))
        .order_by(ShadowExperimentDecision.recommended_at)
    ))
    finals: dict[str, ShadowExperimentDecision] = {}
    for row in decisions:
        if str(getattr(row.phase, "value", row.phase)).split(".")[-1].upper() != "FINAL_PREGAME":
            continue
        if row.recommended_at and row.recommended_at.replace(tzinfo=row.recommended_at.tzinfo or UTC) > now:
            continue
        key = str(row.game_id)
        # A qualified final decision is the one that could carry a position; otherwise the latest.
        if key not in finals or row.recommendation == "QUALIFIED_BET" or finals[key].recommendation != "QUALIFIED_BET":
            finals[key] = row
    positions = {str(p.shadow_decision_id): p for p in session.scalars(
        select(OfficialAspPosition).where(OfficialAspPosition.shadow_decision_id.in_([d.id for d in finals.values()]))
    )} if finals else {}
    settlements = {str(s.position_id): s for s in session.scalars(
        select(PositionSettlement).where(PositionSettlement.position_id.in_([p.id for p in positions.values()]))
    )} if positions else {}
    scores = {str(g.id): g for g in session.scalars(select(Game).where(Game.id.in_(ids)))}

    out = []
    for key, game in games.items():
        final = finals.get(key)
        c = game["comparison"] or {}
        preview = {
            "selection": c.get("team"), "model_probability": c.get("model_probability"),
            "no_vig_probability": c.get("no_vig_probability"), "difference": c.get("difference"),
            "american": c.get("american"), "sportsbook": c.get("sportsbook"), "price_observed_utc": c.get("price_observed_utc"),
            "forecast_status": game["forecast_status"],
            "consensus_no_vig_home": (game["market_summary"]["moneyline"] or {}).get("consensus_no_vig_home"),
        }
        record: dict[str, Any] = {"game_key": game["game_key"], "matchup": game["matchup"], "sport": game["sport"],
                                  "preview": preview, "final": None, "changes": None, "position": None, "outcome": None}
        if final is not None:
            team = _team_of(final.selection, game)
            record["final"] = {
                "decision_id": str(final.id), "selection": team, "model_probability": final.model_probability,
                "no_vig_probability": final.no_vig_probability, "american": final.american_odds,
                "sportsbook": final.sportsbook, "price_observed_utc": final.quote_observed_at.isoformat() if final.quote_observed_at else None,
                "disposition": final.recommendation, "decided_utc": final.recommended_at.isoformat() if final.recommended_at else None,
            }
            record["changes"] = {
                "selection_changed": bool(preview["selection"] and team and preview["selection"] != team),
                "model_probability_change": (round(final.model_probability - preview["model_probability"], 6)
                                             if final.model_probability is not None and preview["model_probability"] is not None
                                             and preview["selection"] == team else None),
                "price_change_american": (final.american_odds - preview["american"]
                                          if final.american_odds is not None and preview["american"] is not None
                                          and preview["selection"] == team else None),
            }
            position = positions.get(str(final.id))
            if position is not None:
                settled = settlements.get(str(position.id))
                record["position"] = {"position_id": str(position.id), "stake": float(position.stake),
                                      "status": "SETTLED" if settled else str(getattr(position.status, "value", position.status)),
                                      "result": str(getattr(settled.result, "value", settled.result)) if settled else None,
                                      "profit_loss": float(settled.profit_loss) if settled else None}
        g = scores.get(key)
        if g is not None and g.home_score is not None and g.away_score is not None:
            record["outcome"] = {"away_score": g.away_score, "home_score": g.home_score,
                                 "winner": game["home"]["abbreviation"] if g.home_score > g.away_score
                                 else game["away"]["abbreviation"] if g.away_score > g.home_score else "TIE"}
        out.append(record)
    return out


def record_final_comparisons(session: Session, snapshot: ShowPreviewSnapshot, settings: Settings, *,
                             now: datetime) -> list[ShowPreviewFinalComparison]:
    """Persist preview-versus-final rows for a frozen snapshot. Idempotent; requires the workflow switch."""

    if not settings.show_preview_enabled:
        raise ShowPreviewDisabled("SHOW_PREVIEW_ENABLED is false; no comparison was recorded")
    written = []
    for row in preview_vs_final(session, snapshot.payload, now=now):
        if row["final"] is None:
            continue
        digest = sha256_hex(canonical_bytes(row))
        exists = session.scalar(select(ShowPreviewFinalComparison).where(
            ShowPreviewFinalComparison.snapshot_id == snapshot.id, ShowPreviewFinalComparison.game_key == row["game_key"],
            ShowPreviewFinalComparison.comparison_sha256 == digest))
        if exists is not None:
            written.append(exists)
            continue
        record = ShowPreviewFinalComparison(
            snapshot_id=snapshot.id, game_key=row["game_key"], final_decision_id=_uuid(row["final"]["decision_id"]),
            position_id=_uuid((row["position"] or {}).get("position_id")), comparison_sha256=digest,
            compared_utc=now, payload=row)
        session.add(record)
        written.append(record)
    session.flush()
    return written
