"""Recover per-event NBA coverage from already charged immutable responses.

Only existing collector/checkpoint owners call this. No HTTP, reservation,
job, schedule edit or funding. Original response/book clocks are immutable;
later acceptance has its own clock and never makes the quote younger.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, DataError

from app.nba import gen1_common as common
from app.services.event_time_reconciliation import assess_clock, utc

MAX_ARCHIVES = 128
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 1000
_DENIED_PATHS: set[str] = set()


class RetainedArchiveRefused(ValueError):
    pass


def _wall_now() -> datetime:
    return datetime.now(UTC)


def _path(value: Any, settings: Any) -> Path:
    if not isinstance(value, str) or not value or value.startswith(("\\\\", "//")):
        raise RetainedArchiveRefused("INVALID_ORIGINAL_ARCHIVE_PATH")
    candidate = Path(value)
    base = Path(settings.raw_data_root).resolve() / "the_odds_api"
    if not candidate.is_absolute() or candidate.suffix.lower() != ".json":
        raise RetainedArchiveRefused("INVALID_ORIGINAL_ARCHIVE_PATH")
    candidate = candidate.resolve()
    if base.resolve() not in candidate.parents:
        raise RetainedArchiveRefused("ARCHIVE_OUTSIDE_ORIGINAL_SOURCE_ROOT")
    return candidate


def _load_archive(path: Path, metadata: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    key = str(path).casefold()
    if key in _DENIED_PATHS:
        raise RetainedArchiveRefused("TERMINAL_ARCHIVE_ACCESS_DENIED_NO_RETRY")
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise RetainedArchiveRefused("ARCHIVE_EXCEEDS_BOUNDED_READ")
        raw = path.read_bytes()
    except PermissionError:
        _DENIED_PATHS.add(key)
        raise RetainedArchiveRefused("TERMINAL_ARCHIVE_ACCESS_DENIED_NO_RETRY") from None
    physical = hashlib.sha256(raw).hexdigest()
    if metadata.get("archive_file_sha256") and metadata["archive_file_sha256"] != physical:
        raise RetainedArchiveRefused("ORIGINAL_ARCHIVE_FILE_HASH_CHANGED")
    if metadata.get("archive_bytes") is not None and int(metadata["archive_bytes"]) != len(raw):
        raise RetainedArchiveRefused("ORIGINAL_ARCHIVE_LENGTH_CHANGED")
    doc = json.loads(raw)
    if not isinstance(doc,dict):
        raise RetainedArchiveRefused("INVALID_ORIGINAL_ARCHIVE_DOCUMENT")
    archive_metadata = doc.get("metadata") or {}
    payload = doc.get("payload")
    digest = metadata.get("archive_payload_sha256")
    if (archive_metadata.get("source") != "THE_ODDS_API"
            or archive_metadata.get("status") != "success"
            or archive_metadata.get("historical") is not False
            or not isinstance(payload, list) or len(payload) > MAX_EVENTS
            or not isinstance(digest, str) or len(digest) != 64
            or archive_metadata.get("checksum") != digest):
        raise RetainedArchiveRefused("ORIGINAL_ARCHIVE_SOURCE_OR_PAYLOAD_BINDING_INVALID")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    if hashlib.sha256(canonical).hexdigest() != digest:
        raise RetainedArchiveRefused("ORIGINAL_PROVIDER_PAYLOAD_CHANGED")
    return payload, {"archive_path": str(path), "archive_payload_sha256": digest,
                     "archive_file_sha256": physical, "archive_bytes": len(raw),
                     "archived_at": utc(archive_metadata["ingestion_timestamp"])}


def _original_received(session, terminal, reservation, path: Path, digest: str) -> tuple[datetime, dict[str, Any]]:
    """Use an original receipt witness; archive creation is never receipt time."""
    from app.models import IngestionRun, OddsSnapshot, RunStatus

    metadata = terminal.response_metadata or {}
    if metadata.get("response_received_at"):
        return utc(metadata["response_received_at"]), {"kind": "TERMINAL_LEDGER_RECEIPT", "id": str(terminal.id)}
    rows = session.execute(select(OddsSnapshot.id, OddsSnapshot.raw_payload).where(
        OddsSnapshot.source == "THE_ODDS_API",
        OddsSnapshot.raw_payload["ledger_reservation_id"].as_string() == str(reservation.id)).limit(1001)).all()
    if len(rows) > 1000:
        raise RetainedArchiveRefused("ORIGINAL_CLOCK_WITNESS_EXCEEDS_BOUND")
    clocks = {}
    for snapshot_id, raw in rows:
        if (not isinstance(raw, dict) or raw.get("archive_payload_sha256") != digest
                or raw.get("archive_path") != str(path)):
            continue
        if utc(raw["requested_at"]) != common.aware(reservation.requested_at):
            raise RetainedArchiveRefused("ORIGINAL_REQUEST_WITNESS_CONFLICT")
        clocks[utc(raw["response_received_at"])] = {"kind": "ORIGINAL_ODDS_SNAPSHOT", "id": str(snapshot_id)}
    if len(clocks) == 1:
        clock = next(iter(clocks))
        return clock, clocks[clock]
    if clocks:
        raise RetainedArchiveRefused("ORIGINAL_RECEIPT_CLOCKS_CONFLICT")
    # Existing _ingestion_run explicitly stores envelope.fetched_at in started_at,
    # including a successful bulk response that bound ZERO events/snapshots.
    ingestions = list(session.scalars(select(IngestionRun).where(
        IngestionRun.source == "THE_ODDS_API", IngestionRun.job_name == "collect-nba-odds-api",
        IngestionRun.status == RunStatus.SUCCEEDED, IngestionRun.raw_reference == str(path)).limit(3)))
    if len(ingestions) != 1 or ingestions[0].started_at is None:
        raise RetainedArchiveRefused("ORIGINAL_RESPONSE_RECEIPT_NOT_PROVED")
    return common.aware(ingestions[0].started_at), {"kind": "ORIGINAL_INGESTION_FETCHED_AT", "id": str(ingestions[0].id)}


def admit_retained_quotes(session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Bounded local recovery before another governed buy or paper checkpoint.

    ``now`` is reported only. Runtime admission always takes a fresh wall clock.
    A row lock on the original reservation serializes existing owners; it does
    not create another writer or charge any provider credit.
    """
    from app.data_sources.odds import TheOddsAPIAdapter
    from app.models import ProviderRequestLedger
    from app.nba import odds_api_price as odds
    from app.services.research_paper_policy import NBA

    result = {"status": "NO_ELIGIBLE_RETAINED_ARCHIVE", "created": 0, "existing": 0,
              "provider_calls": 0, "in_flight": 0, "scan_complete": True, "archives": [], "events": [],
              "initial_job_clock": utc(now).isoformat() if now is not None else None}
    if not (getattr(settings, "nba_odds_api_enabled", False)
            and getattr(settings, "nba_production_consumer_enabled", False)):
        result["status"] = "RETAINED_ARCHIVE_SOURCE_DISABLED"
        return result
    started = _wall_now()
    # Canonical time may be later than a credible provider clock by up to 2h.
    games = odds._nba_games(session, started + NBA.window_closes,
                            started + odds.BUY_OPENS + timedelta(hours=2))
    if not games:
        return result
    earliest_allowed = started - timedelta(minutes=NBA.max_quote_age_minutes) - odds.CLOCK_SKEW
    terminals = list(session.scalars(select(ProviderRequestLedger).where(
        ProviderRequestLedger.provider == odds.PROVIDER,
        ProviderRequestLedger.endpoint_key == odds.ENDPOINT_KEY,
        ProviderRequestLedger.status.in_(("SUCCEEDED","FAILED")),
        ProviderRequestLedger.requested_at >= earliest_allowed,
        ProviderRequestLedger.requested_at <= started,
        ProviderRequestLedger.completed_at <= started).order_by(
        ProviderRequestLedger.completed_at.desc()).limit(MAX_ARCHIVES + 1)))
    if len(terminals) > MAX_ARCHIVES:
        result["status"] = "RETAINED_ARCHIVE_SCAN_BOUND_REACHED"
        result["scan_complete"] = False
        return result
    seen = set()
    for terminal in terminals:
        metadata = terminal.response_metadata or {}
        address = metadata.get("archive_payload_sha256")
        if (str(terminal.reservation_id), address) in seen:
            continue
        seen.add((str(terminal.reservation_id), address))
        try:
            reservation = session.scalar(select(ProviderRequestLedger).where(
                ProviderRequestLedger.id == terminal.reservation_id).with_for_update(skip_locked=True))
            if reservation is None:
                result["in_flight"] += 1
                continue
            if (reservation.provider != odds.PROVIDER or reservation.endpoint_key != odds.ENDPOINT_KEY
                    or reservation.status != "RESERVED"
                    or common.aware(reservation.requested_at) != common.aware(terminal.requested_at)):
                raise RetainedArchiveRefused("ORIGINAL_RESERVATION_BINDING_INVALID")
            if terminal.status == "FAILED" and not metadata.get("response_received"):
                continue
            path = _path(metadata.get("archive_path"), settings)
            payload, archive = _load_archive(path, metadata)
            received, witness = _original_received(session, terminal, reservation, path, address)
            requested = common.aware(reservation.requested_at)
            if not requested <= received <= archive["archived_at"] <= common.aware(terminal.completed_at) <= _wall_now():
                raise RetainedArchiveRefused("ORIGINAL_REQUEST_RECEIPT_ARCHIVE_ORDER_INVALID")
            parse_refusals = []
            events = []
            for raw in payload:
                if not isinstance(raw, dict) or raw.get("sport_key") != odds.SPORT_KEY:
                    continue
                try:
                    events.append(TheOddsAPIAdapter._normalize_event(raw, parse_refusals))
                except (ValueError, KeyError, TypeError) as error:
                    parse_refusals.append({"provider_event_id": str(raw.get("id")), "reason": type(error).__name__})
            bound, binding_receipts = odds.bind_events(session, events, received_at=received)
            by_game = {str(game["game_id"]): game for game in games}
            for event, game_id in bound:
                game = by_game.get(str(game_id))
                if game is None:
                    continue
                actual = _wall_now()
                clock = assess_clock(game["starts_at"], event.commence_time, actual, close_before=NBA.window_closes)
                from app.nba.gen1_paper import _operational_start
                operational = min(clock.earliest_start, _operational_start(session,game,actual)) if clock.accepted else None
                deadline = operational-NBA.window_closes if operational is not None else None
                if not clock.accepted or actual >= deadline or actual < operational - odds.BUY_OPENS:
                    result["events"].append({"game_id": str(game_id), "provider_event_id": str(event.source_event_id),
                                              "reason": clock.reason if not clock.accepted else "OUTSIDE_ORIGINAL_RECOVERY_WINDOW"})
                    continue
                if odds.quote_coverage(session, game_id, moment=actual)["complete"]:
                    continue
                books=[]
                for book in event.bookmakers:
                    markets=[market for market in book.markets if market.key == "h2h" and 0 <= (actual-utc(market.last_update)).total_seconds() <= NBA.max_quote_age_minutes*60]
                    if markets:
                        books.append(book.model_copy(update={"markets":markets}))
                if not books:
                    result["events"].append({"game_id":str(game_id),"provider_event_id":str(event.source_event_id),"reason":"RETAINED_QUOTES_STALE_OR_MISSING"})
                    continue
                retained_event=event.model_copy(update={"bookmakers":books})
                try:
                    with session.begin_nested():
                        imported = _wall_now()
                        if imported >= deadline:
                            raise RetainedArchiveRefused("ORIGINAL_CLOSE_REACHED_BEFORE_IMPORT")
                        written = odds.persist(session, [(retained_event, game_id)], requested_at=requested,
                            received_at=received, admission_asof=imported,
                            response={**{key: value for key, value in archive.items() if key != "archived_at"},
                                      "ledger_reservation_id": str(reservation.id),
                                      "original_clock_witness": witness, "retained_archive_admission": True})
                        if _wall_now() >= deadline:
                            raise RetainedArchiveRefused("ORIGINAL_CLOSE_CROSSED_DURING_IMPORT")
                    result["created"] += written["created"]
                    result["existing"] += written["existing"]
                    result["events"].append({"game_id": str(game_id), "provider_event_id": str(event.source_event_id),
                        "requested_at": requested.isoformat(), "response_received_at": received.isoformat(),
                        "admitted_at": imported.isoformat(), "clock": {**clock.receipt(),"earliest_start":operational.isoformat(),"decision_deadline":deadline.isoformat()}, **written})
                except (RetainedArchiveRefused, ValueError, TypeError, KeyError, IntegrityError, DataError) as error:
                    result["events"].append({"game_id": str(game_id), "provider_event_id": str(event.source_event_id),
                                              "reason": "RETAINED_EVENT_REFUSED", "error_type": type(error).__name__})
            result["archives"].append({"reservation_id": str(reservation.id), "archive_payload_sha256": address,
                "binding_receipts": binding_receipts, "parse_refusals": parse_refusals,
                "original_clock_witness": witness, "response_received_at": received.isoformat(), "original_terminal_status":terminal.status})
        except (RetainedArchiveRefused, ValueError, KeyError, TypeError, OSError) as error:
            result["archives"].append({"reservation_id": str(terminal.reservation_id), "reason": str(error)[:240],
                                       "error_type": type(error).__name__})
    result["status"] = "RETAINED_ARCHIVES_CHECKED" if result["archives"] else "NO_ELIGIBLE_RETAINED_ARCHIVE"
    return result
