"""Prospective capture of RFF Core readings at four registered offsets.

WHY THIS EXISTS
---------------
The 2026-09-17 capture audit showed a T-90m reading cannot be reconstructed
after the fact: no read path in the calculation bounds on knowledge time, the
frame sampler is path-dependent on evidence that arrives later, and nothing
records which code produced a reading. Recomputing the four games that had
already started, 4 of 4 used evidence 140-295 minutes newer than the as-of
instant.

So the reading is captured while it is true, and the stored row is the
authoritative record. A later recomputation is a diagnostic comparison, never a
correction.

WHAT IT MAY NOT DO
------------------
It makes no provider request, adds no scheduler, and reads only what is already
persisted. It calls the deployed ``RffService`` unchanged: no weight, gate,
band, threshold or interface is touched here. Nothing it writes is read by a
forecast, a decision, a stake, a position or a settlement.

FAIL-CLOSED TIMING
------------------
The host job ticks every two minutes, so a target instant is normally reached
within 120 seconds. A reading captured more than ``MAX_CAPTURE_LAG_SECONDS``
after its target is NOT the reading that existed at the target, so it is not
calculated and not stored as valid: an append-only ``MISSED_CAPTURE_WINDOW``
receipt is written instead and is never backfilled.

Offsets whose target instant precedes ``ACTIVATED_AT_UTC`` are inadmissible and
produce nothing at all -- not even a receipt. There was no capture system then,
and inventing a record of one would be the fabrication this module exists to
prevent.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Forecast, Game, League, LineupObservation, Market, MarketOutcome, OddsSnapshot,
    RffReadingSnapshot, Sportsbook, StarterObservation,
)
from app.services.rff_index import (
    CORE_COMPONENTS, MAX_CATEGORY_INTERVAL_POINTS, MIN_COMPONENTS_PUBLISHED,
    MIN_COVERAGE_PROVISIONAL, MIN_COVERAGE_PUBLISHED, MIN_FAMILIES_PUBLISHED,
    SCHEMA_VERSION, WEIGHTS, RffService,
)

logger = logging.getLogger(__name__)

#: The four registered reading points of the v2 validation plan. T-90m is the
#: primary; the other three are secondary repeated measurements.
OFFSETS: tuple[tuple[str, int], ...] = (
    ("T-6h", 360),
    ("T-3h", 180),
    ("T-90m", 90),
    ("T-30m", 30),
)
PRIMARY_OFFSET = "T-90m"

#: The host job runs every two minutes. 180 seconds leaves one tick of slack for
#: a slow tick or a restart, and still bounds the error well inside the spacing
#: between offsets. A reading later than this is refused, not adjusted.
MAX_CAPTURE_LAG_SECONDS = 180

#: Nothing before this instant is admissible. Set once, at deployment, and never
#: moved backwards: moving it back would licence inventing readings for games
#: that passed their offsets before capture existed.
ACTIVATED_AT_UTC = datetime(2026, 9, 17, 20, 40, 0, tzinfo=UTC)

STATUS_ACCEPTED = "ACCEPTED"
STATUS_MISSED = "MISSED_CAPTURE_WINDOW"

#: How far ahead to look for games whose offsets could be due.
LOOKAHEAD_HOURS = 12

#: The physical files that determine the number. Hashing them means a silent
#: edit to a weight, a gate or a component cannot masquerade as the same
#: calculation.
_SOURCE_FILES = ("app/services/rff_index.py", "app/services/market_tape.py")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _server_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _source_hashes() -> dict[str, str]:
    """SHA-256 of each calculation source file, by its repository path."""

    root = _server_root()
    out: dict[str, str] = {}
    for relative in _SOURCE_FILES:
        path = root / relative
        try:
            out[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:  # pragma: no cover - a missing source file is not a capture failure
            out[relative] = ""
    return out


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(_server_root().parent),
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    sha = (result.stdout or "").strip()
    return sha or None


def calculation_identity() -> dict[str, Any]:
    """Everything needed to say which calculation produced a reading."""

    files = _source_hashes()
    combined = _sha256("|".join(f"{name}:{digest}" for name, digest in sorted(files.items())))
    weights = _sha256(json.dumps(
        {key: WEIGHTS[key] for key in sorted(WEIGHTS)}, sort_keys=True, separators=(",", ":")))
    policy = _sha256(json.dumps({
        "min_coverage_provisional": MIN_COVERAGE_PROVISIONAL,
        "min_coverage_published": MIN_COVERAGE_PUBLISHED,
        "min_components_published": MIN_COMPONENTS_PUBLISHED,
        "min_families_published": MIN_FAMILIES_PUBLISHED,
        "max_category_interval_points": MAX_CATEGORY_INTERVAL_POINTS,
        "max_capture_lag_seconds": MAX_CAPTURE_LAG_SECONDS,
    }, sort_keys=True, separators=(",", ":")))
    return {
        "calculation_version": SCHEMA_VERSION,
        "calculation_git_sha": _git_sha(),
        "calculation_source_sha256": combined,
        "weights_sha256": weights,
        "policy_sha256": policy,
        "source_files": files,
        "core_components": list(CORE_COMPONENTS),
        "weights": dict(WEIGHTS),
    }


def _evidence(session: Session, game_id: UUID, as_of: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    """Hashes over the evidence visible at capture, plus the decisive row ids.

    The full sets are hashed and counted; the identities kept in full are the
    small decisive ones -- the forecast, the lineup and starter rows, the
    opening quote and the latest quote per book, which is what consensus and
    cross-book dispersion actually stand on.
    """

    quotes = list(session.execute(
        select(OddsSnapshot.id, OddsSnapshot.observed_at, OddsSnapshot.available_as_of,
               Sportsbook.key)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id == game_id, Market.market_type == "MONEYLINE",
               OddsSnapshot.available_as_of <= as_of)
        .order_by(OddsSnapshot.observed_at)
    ).all())
    lineups = list(session.execute(
        select(LineupObservation.id, LineupObservation.team_id, LineupObservation.observed_at)
        .where(LineupObservation.game_id == game_id, LineupObservation.available_as_of <= as_of)
        .order_by(LineupObservation.observed_at)
    ).all())
    starters = list(session.execute(
        select(StarterObservation.id, StarterObservation.team_id, StarterObservation.observed_at)
        .where(StarterObservation.game_id == game_id, StarterObservation.available_as_of <= as_of)
        .order_by(StarterObservation.observed_at)
    ).all())
    forecast = session.execute(
        select(Forecast.id, Forecast.version, Forecast.generated_at, Forecast.available_as_of,
               Forecast.home_win_probability)
        .where(Forecast.game_id == game_id, Forecast.available_as_of <= as_of)
        .order_by(Forecast.available_as_of.desc(), Forecast.generated_at.desc()).limit(1)
    ).first()

    def digest(ids: list[Any]) -> str:
        return _sha256("|".join(sorted(str(value) for value in ids)))

    latest_per_book: dict[str, dict[str, Any]] = {}
    for snapshot_id, observed_at, available_as_of, book in quotes:
        latest_per_book[str(book)] = {
            "sportsbook": str(book), "snapshot_id": str(snapshot_id),
            "observed_at_utc": observed_at.isoformat() if observed_at else None,
            "available_as_of_utc": available_as_of.isoformat() if available_as_of else None,
        }

    hashes = {
        "quotes": {
            "sha256": digest([row[0] for row in quotes]), "rows": len(quotes),
            "first_available_as_of_utc": quotes[0][2].isoformat() if quotes else None,
            "last_available_as_of_utc": quotes[-1][2].isoformat() if quotes else None,
        },
        "lineups": {"sha256": digest([row[0] for row in lineups]), "rows": len(lineups)},
        "starters": {"sha256": digest([row[0] for row in starters]), "rows": len(starters)},
        "forecast": {
            "sha256": _sha256("|".join(str(value) for value in forecast)) if forecast else None,
        },
        "note": "Hashes cover every row visible at as_of_utc. Lineup and starter timestamps are "
                "AISP INGESTION time, not proven source-publication time: available_as_of equals "
                "observed_at for every row in those tables, so the source's own clock is not "
                "recoverable and a bulk re-ingest restamps them.",
    }
    identities = {
        "quote_opening": ({
            "snapshot_id": str(quotes[0][0]), "sportsbook": str(quotes[0][3]),
            "observed_at_utc": quotes[0][1].isoformat() if quotes[0][1] else None,
        } if quotes else None),
        "quote_latest_per_book": list(latest_per_book.values()),
        "lineup_ids": [str(row[0]) for row in lineups],
        "starter_ids": [str(row[0]) for row in starters],
        "forecast": ({
            "forecast_id": str(forecast[0]), "version": forecast[1],
            "generated_at_utc": forecast[2].isoformat() if forecast[2] else None,
            "available_as_of_utc": forecast[3].isoformat() if forecast[3] else None,
            "home_win_probability": (None if forecast[4] is None else float(forecast[4])),
        } if forecast else None),
    }
    return hashes, identities


def due_targets(
    session: Session, *, now: datetime, activated_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Every (game, offset) whose target instant has passed and has no row yet.

    Read-only. Offsets before activation are omitted entirely rather than
    reported as missed: there was no capture system then.
    """

    activated_at = activated_at or ACTIVATED_AT_UTC
    horizon = now + timedelta(hours=LOOKAHEAD_HOURS)
    games = list(session.execute(
        select(Game.id, Game.starts_at, League.code)
        .join(League, League.id == Game.league_id)
        .where(Game.starts_at >= now - timedelta(hours=LOOKAHEAD_HOURS),
               Game.starts_at <= horizon,
               # NHL is research-only (forecasts imported, no RFF inputs): never captured.
               League.code != "NHL")
        .order_by(Game.starts_at)
    ).all())
    if not games:
        return []
    existing = {
        (row[0], row[1]) for row in session.execute(
            select(RffReadingSnapshot.game_id, RffReadingSnapshot.target_offset)
            .where(RffReadingSnapshot.game_id.in_([game[0] for game in games]))
        ).all()
    }

    due: list[dict[str, Any]] = []
    for game_id, starts_at, league_code in games:
        start = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=UTC)
        for label, minutes in OFFSETS:
            target = start.astimezone(UTC) - timedelta(minutes=minutes)
            if target > now:
                continue
            if target < activated_at:
                continue
            if (game_id, label) in existing:
                continue
            due.append({
                "game_id": game_id, "league_code": str(league_code or ""),
                "target_offset": label, "target_instant_utc": target,
                "lag_seconds": int((now - target).total_seconds()),
            })
    return due


def capture_due_rff_snapshots(
    session: Session, *, now: datetime | None = None, activated_at: datetime | None = None,
) -> dict[str, Any]:
    """Capture every due reading. Never raises into the host job.

    Each target is written in its own SAVEPOINT, so one bad game cannot roll
    back another game's row or poison the host job's transaction.
    """

    now = now or datetime.now(UTC)
    summary: dict[str, Any] = {
        "accepted": 0, "missed": 0, "failed": 0, "due": 0,
        "max_capture_lag_seconds": MAX_CAPTURE_LAG_SECONDS,
    }
    try:
        targets = due_targets(session, now=now, activated_at=activated_at)
    except Exception:  # pragma: no cover - a read failure is never fatal here
        logger.exception("rff capture: could not determine due targets")
        summary["failed"] = 1
        return summary

    summary["due"] = len(targets)
    if not targets:
        summary["reason"] = "NOT_DUE"
        return summary

    identity = calculation_identity()
    for target in targets:
        try:
            with session.begin_nested():
                row = _capture_one(session, target=target, now=now, identity=identity)
            summary["accepted" if row.status == STATUS_ACCEPTED else "missed"] += 1
        except IntegrityError:
            # The unique constraint did its job: another tick or process already
            # recorded this (game, offset). That is success, not an error.
            session.rollback()
            summary.setdefault("already_recorded", 0)
            summary["already_recorded"] += 1
        except Exception:
            logger.exception("rff capture failed for %s %s", target["game_id"], target["target_offset"])
            summary["failed"] += 1
    return summary


def _capture_one(
    session: Session, *, target: dict[str, Any], now: datetime, identity: dict[str, Any],
) -> RffReadingSnapshot:
    lag = int((now - target["target_instant_utc"]).total_seconds())
    common = {
        "game_id": target["game_id"],
        "league_code": target["league_code"],
        "target_offset": target["target_offset"],
        "target_instant_utc": target["target_instant_utc"],
        "captured_at_utc": now,
        "capture_lag_seconds": lag,
    }

    if lag > MAX_CAPTURE_LAG_SECONDS:
        # Fail closed. No calculation is run at all: a reading taken now is not
        # the reading that existed at the target, and there is no honest way to
        # label it as one.
        row = RffReadingSnapshot(
            **common, as_of_utc=None, status=STATUS_MISSED,
            missed_reason=(f"Captured {lag}s after the target instant, over the "
                           f"{MAX_CAPTURE_LAG_SECONDS}s limit. Not calculated, never backfilled."),
        )
        session.add(row)
        session.flush()
        return row

    payload = RffService(session).for_game(game_id=target["game_id"], now=now)
    hashes, identities = _evidence(session, target["game_id"], now)
    row = RffReadingSnapshot(
        **common,
        as_of_utc=now,
        status=STATUS_ACCEPTED,
        calculation_version=identity["calculation_version"],
        calculation_git_sha=identity["calculation_git_sha"],
        calculation_source_sha256=identity["calculation_source_sha256"],
        weights_sha256=identity["weights_sha256"],
        policy_sha256=identity["policy_sha256"],
        calculation_identity=identity,
        components=[
            {
                "key": component["key"], "score": component["score"],
                "available": component["available"], "weight": component["weight"],
                "family": component["family"],
                "unavailable_reason": component["unavailable_reason"],
                "observed_utc": component["observed_utc"], "stale": component["stale"],
            }
            for component in payload["components"]
        ],
        known_contribution=payload["known_contribution"],
        evidence_coverage=payload["evidence_coverage"],
        lower_bound=payload["lower_bound"],
        upper_bound=payload["upper_bound"],
        interval_width=payload["interval_width"],
        publication_tier=payload["status"],
        band_key=payload["band"]["key"],
        band_label=payload["band"]["label"],
        emphasize_point=payload["emphasize_point"],
        rankable=payload["rankable"],
        available_components=payload["available_components"],
        evidence_health=payload["evidence_health"],
        source_hashes=hashes,
        source_row_identities=identities,
    )
    session.add(row)
    session.flush()
    return row


__all__ = [
    "ACTIVATED_AT_UTC", "MAX_CAPTURE_LAG_SECONDS", "OFFSETS", "PRIMARY_OFFSET",
    "STATUS_ACCEPTED", "STATUS_MISSED", "calculation_identity",
    "capture_due_rff_snapshots", "due_targets",
]
