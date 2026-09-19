"""Consumer pregame evidence packets for the FROZEN V18 vs fresh-market pilot.

WHAT THIS IS
------------
Protocol ``f75aeccd...`` (MLB_V18_FRESH_MARKET_PROSPECTIVE_PROTOCOL_V1, frozen
2026-09-18 by the producer) compares the exact raw V18 home-win forecast used
at a pregame checkpoint with a genuinely fresh, same-book, two-sided moneyline.
It needs the consumer (AISP) to export one private pregame packet per game,
matching ``PACKET_SCHEMA.json``, sealed by T-5 and before first pitch. This
module is the pure, read-only builder of that packet. The sealing writer and
the scheduled job live in :mod:`app.research_exports.v18_market_pilot_seal`.

WHAT IT MAY NOT DO
------------------
It never writes to the database, never calls a provider, never creates or
alters a decision or position, and never reads an outcome, score or settlement
column: every table is read column by column, and the columns that carry
results (``games.home_score``/``away_score``/``final_at``/``status``,
``game_teams.score``, position results/payouts, closing flags) are never
selected. A packet or evidence document containing an outcome-shaped key is
refused before it can be sealed.

HOW EACH REQUIRED FIELD IS SOURCED
----------------------------------
game
  ``games`` (id, starts_at), ``game_teams`` (orientation), ``external_id_mappings``
  (MLBAM gamePk, team ids, other provider ids), the latest
  ``game_state_observations`` identity row available at the lock (schedule
  revision), and the earliest such row (first seen). ``games`` has no game-type
  column, so ``phase`` comes ONLY from AISP's retained raw MLB Stats API
  schedule capture (``<raw_data_root>/mlb_stats_api``) archived at or before the
  lock, reading the whitelisted ``gameType`` of that gamePk. No capture -> the
  phase is ``UNKNOWN`` with a reason code; it is never inferred from the date
  or from the importer's constant ``season_type="REGULAR"``.
anchor
  ``shadow_experiment_decisions`` of the V18 experiment, phase FINAL_PREGAME,
  ``recommended_at`` in [T-30, T-15]: the decision behind a position if one
  exists, otherwise the latest (ties by decision id ascending); otherwise the
  labelled ``RESEARCH_T_MINUS_15`` cutoff.
forecast
  ``shadow_forecasts`` (engine MLB_V18_WHOLE_GAME) and its
  ``shadow_forecast_bundles`` row (the producer's hash-bound slate handoff).
  ``created_at`` is PostgreSQL ``now()`` -- the START of the import transaction
  -- and is reported only under that name, never as an insertion, commit or
  publication time. The consumer import receipt is the importing
  ``consume-model-exchange-research-artifacts`` job run's ``completed_at``.
selection
  The anchor decision's recommendation as of the lock (QUALIFIED_BET ->
  SELECTED), from the complete decision table for the experiment and game;
  position ids are supplementary (a position row is materialised after the
  cutoff from the locked decision).
market
  ``odds_snapshots``/``market_outcomes``/``markets``/``sportsbooks``, grouped
  into same-response two-sided full-game moneylines. A leg's availability
  bound is a completed receipt: ``provider_request_ledger`` (SUCCEEDED row
  whose ``requested_at`` equals the rows' ``available_as_of``) and, when the
  request names its job run, that run's ``completed_at`` -- the later of the
  two, an upper bound on the commit. ``available_as_of`` alone (SGO: the
  fetch's ``requested_at``) is never treated as a receipt.
market: per-leg clocks, response identity and conventions (SGO)
  AISP's SGO rows store the pair's OLDER provider ``lastUpdatedAt`` on both
  legs (``observed_at``) and a per-row ``payload_checksum`` of the stored
  outcome, so neither per-leg clocks nor a response identity live in the
  database. Each SGO pair is therefore linked to the retained raw response of
  its OWN fetch: a ``RawArchive`` file under ``<raw_data_root>/sportsgameodds``
  archived inside that fetch's completed-receipt window, last modified by the
  anchor, whose event/book shows both exact full-game moneyline oddIDs at the
  stored prices with the older ``lastUpdatedAt`` equal to the stored
  ``observed_at``. Only then does each leg carry its own provider clock (and
  the 0..15-minute rule read it), and the pair's ``sourceResponseRef`` name
  that file (path, sha256, bytes; fetch clock and archive clock labelled as
  such). Otherwise every field stays null with a reason code and the leg keeps
  the stored older-side clock, flagged ``quoteUpdatedAtIsPerLeg=false``.
  Conventions are carried exactly as the retained response states them (cited
  by JSON locator); what it does not state -- extra innings, tie handling,
  pitcher/action -- stays null. The collector's own ingest-time skew record
  (``job_runs.result.fallback.skew``) is carried as evidence, never as a clock.

Every database row a packet relies on is copied (identity columns only) into a
write-once evidence document beside the packet, and the packet's refs point
into it by JSON pointer, so each ref carries a real file digest and byte count.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ExperimentDecisionPhase,
    ExternalIdMapping,
    Game,
    GameStateObservation,
    GameTeam,
    JobRun,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    OfficialAspPosition,
    ProviderRequestLedger,
    ShadowExperiment,
    ShadowExperimentDecision,
    ShadowForecast,
    ShadowForecastBundle,
    Sportsbook,
)
from app.services.league_scope import MLB, in_league

# ---------------------------------------------------------------------------
# Frozen identities (verified against the vendored byte-exact copies at load)
# ---------------------------------------------------------------------------
PROTOCOL_SHA256 = "f75aeccd8f0e472b399d1f470c29d6a443d77078f7c69333023292019d956bf7"
PACKET_SCHEMA_SHA256 = "f8a7458b8a908ca21d05130a9ed0fdb25d7c84907e6f3e828e95bac728f88594"
PROTOCOL_RULES_SHA256 = "5f8157d80178cfd219922904b4df1af7fe35274c59c8c612ee102e69c727c326"
QUARANTINE_LEDGER_SHA256_AT_FREEZE = "360bc83743d666842cd429fc6105b8f69ccab2007b78c645e27b03e3b1cd09dc"
FROZEN_DIR = Path(__file__).resolve().parent / "frozen" / "mlb_v18_market_prospective_v1"

V18_EXPERIMENT_ID = "b7532da1-27b2-4d7a-b373-bbfb3166bd3a"
ENGINE_ID = "MLB_V18_WHOLE_GAME"
FROZEN_MODEL = {
    "engine": ENGINE_ID,
    "paSha256": "44ec4f457741f1aaeff42b81af1be5fd2712e494709ff1a0a8f0742b88d71c8b",
    "transitionSha256": "0e5e480f897ddefa5f9dddbc229a8a43bf29f7b81443c25ece611d9d4032b253",
    "parentBundleSha256": "a6e7e99122f1b60701a4728b97b32fcc11e03405bfd456ed6cd54f69b69adb27",
    "treatmentManifestSha256": "4d158e01a4a7d84fe6e7641f61edf0e9848f1775e025a2cec6c29cda22d479f3",
    "calibration": "RETAIN_RAW_V18",
    "simulationCount": 5000,
}
WINDOW_OPEN_UTC = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
WINDOW_CLOSE_UTC = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
LOCK_MINUTES_BEFORE_START = 15
SEAL_MINUTES_BEFORE_START = 5
DECISION_WINDOW_MINUTES = 15          # FINAL_PREGAME decisions in [T-30, T-15]
MAX_QUOTE_AGE = timedelta(minutes=15)
SIMULATION_COUNT = 5000

EXPORT_IDENTITY = "AISP_V18_MARKET_PILOT_CONSUMER_EXPORT_V1"
EVIDENCE_SCHEMA = "AISP_V18_MARKET_PILOT_CONSUMER_EVIDENCE_V1"
V18_IMPORT_JOB = "consume-model-exchange-research-artifacts"
FREEZE_JOB = "freeze-shadow-experiment"
SELECTING_RECOMMENDATION = "QUALIFIED_BET"
NON_SELECTING_RECOMMENDATIONS = frozenset({
    "NO_BET", "WATCH", "PROVISIONAL_EDGE", "EXTREME_DISAGREEMENT", "PASS", "NO_EDGE",
})
SGO = "SPORTSGAMEODDS"
EASTERN = ZoneInfo("America/New_York")

#: MLB Stats API gameType -> packet phase. Anything unlisted is OTHER.
POSTSEASON_GAME_TYPES = frozenset({"F", "D", "L", "W"})
REGULAR_GAME_TYPE = "R"

MODE_PROSPECTIVE = "PROSPECTIVE_SEAL"
MODE_FEASIBILITY = "RETROSPECTIVE_FEASIBILITY_NOT_ENROLLMENT"

#: Deterministic first-failure order. Limitations (``LIMITATION_*``) never fail.
FIRST_FAILURE_ORDER: tuple[str, ...] = (
    "IDENTITY_QUARANTINE_EXCLUDED",
    "IDENTITY_INCOMPLETE",
    "PHASE_NOT_CERTIFIED_R",
    "ANCHOR_AMBIGUOUS",
    "FORECAST_MISSING",
    "FORECAST_INTEGRITY_FAILED",
    "FORECAST_NOT_AVAILABLE_BY_ANCHOR",
    "MARKET_NO_QUOTE_FETCHED_BY_ANCHOR",
    "MARKET_NO_TWO_SIDED_SAME_RESPONSE_PAIR",
    "MARKET_PAIR_INVALID",
    "MARKET_NO_RECEIPT_BY_ANCHOR",
    "MARKET_QUOTE_AGE_NEGATIVE",
    "MARKET_QUOTE_OLDER_THAN_15_MINUTES",
)

#: Keys that would carry an outcome. None may appear in a packet or its evidence.
FORBIDDEN_OUTCOME_KEYS = frozenset({
    "home_score", "away_score", "homeScore", "awayScore", "score", "scores", "final_at",
    "finalAt", "result", "results", "winner", "linescore", "settled_at", "settledAt",
    "settlement", "settlements", "profit_loss", "profitLoss", "payout", "closing_decimal_odds",
    "closing_line_value", "beat_close", "yHome", "isFinal", "is_final", "finalScore",
    "outcomeResult", "gameStatus", "game_status",
})


class FrozenProtocolError(RuntimeError):
    """The vendored or external frozen protocol does not match its identity."""


class OutcomeFieldError(RuntimeError):
    """A packet or evidence document contains an outcome-shaped key."""


# ---------------------------------------------------------------------------
# Frozen protocol
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FrozenProtocol:
    sha256: str
    schema_sha256: str
    protocol: dict[str, Any]
    schema: dict[str, Any]
    external_check: str
    protocol_bytes: int
    schema_bytes: int


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_frozen_protocol(external_dir: str | Path | None = None) -> FrozenProtocol:
    """Load the vendored frozen protocol + schema; fail closed on any mismatch.

    When ``external_dir`` (the producer's frozen object) is readable it must be
    byte-identical as well. Unreadable is recorded, never treated as a match.
    """

    protocol_bytes = (FROZEN_DIR / "protocol.json").read_bytes()
    schema_bytes = (FROZEN_DIR / "PACKET_SCHEMA.json").read_bytes()
    if _sha(protocol_bytes) != PROTOCOL_SHA256:
        raise FrozenProtocolError("Vendored protocol.json does not match the frozen protocol SHA-256")
    if _sha(schema_bytes) != PACKET_SCHEMA_SHA256:
        raise FrozenProtocolError("Vendored PACKET_SCHEMA.json does not match the frozen schema SHA-256")
    protocol = json.loads(protocol_bytes)
    schema = json.loads(schema_bytes)
    if (protocol.get("packetSchema") or {}).get("sha256") != PACKET_SCHEMA_SHA256:
        raise FrozenProtocolError("Frozen protocol names a different packet schema")
    window = protocol.get("window") or {}
    if (_parse_ts(window.get("openingUtc")) != WINDOW_OPEN_UTC
            or _parse_ts(window.get("closeUtc")) != WINDOW_CLOSE_UTC
            or int(window.get("decisionLockMinutesBeforeStart", -1)) != LOCK_MINUTES_BEFORE_START
            or int(window.get("sealDeadlineMinutesBeforeStart", -1)) != SEAL_MINUTES_BEFORE_START
            or int(protocol.get("priceMaximumAgeMinutes", -1)) != 15):
        raise FrozenProtocolError("Frozen protocol window or price-age rule differs from this exporter")
    if ((protocol.get("rulesDocument") or protocol.get("ruleDocument") or {}).get("sha256")
            not in (None, PROTOCOL_RULES_SHA256)):
        raise FrozenProtocolError("Frozen protocol names a different rule document")

    external_check = "NOT_CONFIGURED"
    if external_dir:
        root = Path(external_dir)
        results = []
        for name, expected in (("protocol.json", PROTOCOL_SHA256), ("PACKET_SCHEMA.json", PACKET_SCHEMA_SHA256)):
            try:
                data = (root / name).read_bytes()
            except OSError:
                results.append("UNREADABLE")
                continue
            if _sha(data) != expected:
                raise FrozenProtocolError(f"External frozen {name} differs from the frozen identity")
            results.append("MATCHED")
        external_check = "MATCHED" if all(r == "MATCHED" for r in results) else "EXTERNAL_UNREADABLE"
    return FrozenProtocol(
        sha256=PROTOCOL_SHA256,
        schema_sha256=PACKET_SCHEMA_SHA256,
        protocol=protocol,
        schema=schema,
        external_check=external_check,
        protocol_bytes=len(protocol_bytes),
        schema_bytes=len(schema_bytes),
    )


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def aware(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return _parse_ts(value)


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def iso(value: Any) -> str | None:
    moment = aware(value)
    if moment is None:
        return None
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def research_lock(starts_at: datetime) -> datetime:
    return aware(starts_at) - timedelta(minutes=LOCK_MINUTES_BEFORE_START)


def seal_deadline(starts_at: datetime) -> datetime:
    return aware(starts_at) - timedelta(minutes=SEAL_MINUTES_BEFORE_START)


def in_enrollment_window(lock: datetime) -> bool:
    lock = aware(lock)
    return WINDOW_OPEN_UTC <= lock < WINDOW_CLOSE_UTC


def episode_key(game_id: Any, game_pk: str | None, lock: datetime) -> str:
    """Write-once game/schedule-episode key: a new start time is a new episode."""

    pk = re.sub(r"[^0-9A-Za-z]", "", str(game_pk or "")) or "NOPK"
    return f"MLB-{pk}-{game_id}-LOCK-{aware(lock):%Y%m%dT%H%M%SZ}"


def _minutes(delta: timedelta | None) -> float | None:
    return None if delta is None else round(delta.total_seconds() / 60.0, 4)


# ---------------------------------------------------------------------------
# Pure rules (unit tested directly)
# ---------------------------------------------------------------------------
def quote_age_failure(quote_updated_at: datetime | None, anchor: datetime) -> str | None:
    """None when the leg's book update clock is 0..15 minutes old at the anchor, inclusive."""

    if quote_updated_at is None:
        return "LEG_QUOTE_UPDATE_CLOCK_MISSING"
    age = aware(anchor) - aware(quote_updated_at)
    if age < timedelta(0):
        return "LEG_QUOTE_AGE_NEGATIVE"
    if age > MAX_QUOTE_AGE:
        return "LEG_QUOTE_AGE_OVER_15_MINUTES"
    return None


def american_to_decimal_exact(american: Any) -> Fraction | None:
    """Exact American -> decimal; None for anything that is not valid American odds."""

    if isinstance(american, bool) or american is None:
        return None
    try:
        value = int(american)
    except (TypeError, ValueError):
        return None
    if value != american and not (isinstance(american, float) and float(value) == american):
        return None
    if value >= 100:
        decimal = Fraction(value, 100) + 1
    elif value <= -100:
        decimal = Fraction(100, -value) + 1
    else:
        return None
    return decimal if decimal > 1 else None


def no_vig_home(decimal_home: Fraction, decimal_away: Fraction) -> tuple[Fraction, Fraction]:
    """(qHome, overround) with rH=1/dH, rA=1/dA, qH=rH/(rH+rA)."""

    r_home = 1 / decimal_home
    r_away = 1 / decimal_away
    return r_home / (r_home + r_away), r_home + r_away - 1


@dataclass(frozen=True)
class DecisionCandidate:
    id: str
    recommended_at: datetime
    recommendation: str
    shadow_forecast_id: str | None
    forecast_id: str | None = None


@dataclass(frozen=True)
class PositionLink:
    id: str
    decision_id: str | None
    shadow_forecast_id: str | None = None


@dataclass(frozen=True)
class AnchorChoice:
    kind: str                       # ACTUAL_DECISION | RESEARCH_T_MINUS_15 | NONE_EXCLUDED
    at: datetime | None
    decision: DecisionCandidate | None
    candidates: tuple[DecisionCandidate, ...]
    reason_codes: tuple[str, ...]
    #: The checkpoint that states the selection as of the lock. Usually the
    #: anchor decision; a checkpoint that names no V18 forecast still states
    #: the selection even though it cannot bind a forecast.
    selection_decision: DecisionCandidate | None = None


def choose_anchor(
    decisions: Iterable[DecisionCandidate],
    positions: Iterable[PositionLink],
    lock: datetime,
) -> AnchorChoice:
    """The protocol's anchor rule, deterministically.

    Candidates are FINAL_PREGAME decisions with ``recommended_at`` in
    [T-30, T-15]. A decision behind a position wins; otherwise the latest, ties
    by decision id ascending. More than one position-backed decision with
    conflicting decision/forecast identity is an ambiguity exclusion. A chosen
    checkpoint that bound no V18 forecast is not a usable consumer decision:
    the anchor becomes the labelled T-15 research cutoff, and the checkpoint is
    kept only as the selection statement.
    """

    lock = aware(lock)
    window_start = lock - timedelta(minutes=DECISION_WINDOW_MINUTES)
    all_decisions = list(decisions)
    in_window = sorted(
        (d for d in all_decisions if window_start <= aware(d.recommended_at) <= lock),
        key=lambda d: d.id,
    )
    reasons: list[str] = []
    position_list = list(positions)
    position_decision_ids = {p.decision_id for p in position_list if p.decision_id}
    backed = [d for d in in_window if d.id in position_decision_ids]
    outside = [p for p in position_list if p.decision_id and p.decision_id not in {d.id for d in in_window}]
    if outside:
        reasons.append("POSITION_DECISION_OUTSIDE_T30_T15_WINDOW")
    if len(backed) > 1:
        identities = {(d.id, d.shadow_forecast_id) for d in backed}
        if len(identities) > 1:
            return AnchorChoice("NONE_EXCLUDED", None, None, tuple(in_window),
                                tuple(reasons + ["ANCHOR_AMBIGUOUS_MULTIPLE_POSITION_DECISIONS"]), None)
    if backed:
        chosen = backed[0]
        reasons.append("ANCHOR_IS_DECISION_BEHIND_POSITION")
        latest = _latest_decision(in_window)
        if latest is not None and latest.id != chosen.id:
            reasons.append("POSITION_DECISION_IS_NOT_THE_LATEST_CHECKPOINT")
    else:
        chosen = _latest_decision(in_window)
        if chosen is not None:
            ties = [d for d in in_window if aware(d.recommended_at) == aware(chosen.recommended_at)]
            if len(ties) > 1:
                reasons.append("ANCHOR_TIE_BROKEN_BY_DECISION_ID_ASCENDING")
    if chosen is None:
        reasons.append("NO_FINAL_PREGAME_DECISION_IN_T30_T15")
        return AnchorChoice("RESEARCH_T_MINUS_15", lock, None, tuple(in_window), tuple(reasons), None)
    if not chosen.shadow_forecast_id:
        # The checkpoint exists but bound no V18 forecast (the consumer had
        # none to use). It is not a usable consumer decision for the model
        # comparison, so the labelled research cutoff is the anchor; an earlier
        # checkpoint is never substituted. The checkpoint still states the
        # selection as of the lock.
        reasons.append("ANCHOR_CHECKPOINT_NAMES_NO_V18_FORECAST_RESEARCH_CUTOFF_USED")
        return AnchorChoice("RESEARCH_T_MINUS_15", lock, None, tuple(in_window), tuple(reasons), chosen)
    return AnchorChoice("ACTUAL_DECISION", aware(chosen.recommended_at), chosen, tuple(in_window),
                        tuple(reasons), chosen)


def _latest_decision(decisions: list[DecisionCandidate]) -> DecisionCandidate | None:
    if not decisions:
        return None
    newest = max(aware(d.recommended_at) for d in decisions)
    return min((d for d in decisions if aware(d.recommended_at) == newest), key=lambda d: d.id)


def selection_status(
    anchor: AnchorChoice,
    *,
    experiment_present: bool,
    export_complete: bool,
) -> tuple[str, list[str]]:
    """SELECTED / EVALUATED_NOT_SELECTED / NO_FINAL_DECISION / UNRESOLVED as of the lock."""

    if not experiment_present:
        return "UNRESOLVED", ["SELECTION_V18_EXPERIMENT_NOT_FOUND"]
    if not export_complete:
        return "UNRESOLVED", ["SELECTION_EXPORT_INCOMPLETE_FREEZE_RUN_IN_FLIGHT_AT_LOCK"]
    if anchor.kind == "NONE_EXCLUDED":
        return "UNRESOLVED", ["SELECTION_AMBIGUOUS_POSITION_DECISIONS"]
    checkpoint = anchor.selection_decision
    if checkpoint is None:
        return "NO_FINAL_DECISION", ["SELECTION_NO_V18_FINAL_PREGAME_DECISION_IN_T30_T15"]
    extra = [] if checkpoint.shadow_forecast_id else ["SELECTION_CHECKPOINT_BOUND_NO_V18_FORECAST"]
    recommendation = str(checkpoint.recommendation or "").upper()
    if recommendation == SELECTING_RECOMMENDATION:
        return "SELECTED", ["SELECTION_FINAL_PREGAME_QUALIFIED_BET_AT_LOCK"] + extra
    if recommendation in NON_SELECTING_RECOMMENDATIONS:
        return "EVALUATED_NOT_SELECTED", [f"SELECTION_FINAL_PREGAME_{recommendation}_AT_LOCK"] + extra
    return "UNRESOLVED", [f"SELECTION_UNRECOGNIZED_RECOMMENDATION_{recommendation or 'EMPTY'}"]


def phase_from_game_type(game_type: str | None) -> str:
    value = str(game_type or "").strip().upper()
    if not value:
        return "UNKNOWN"
    if value == REGULAR_GAME_TYPE:
        return "R"
    if value in POSTSEASON_GAME_TYPES:
        return "POSTSEASON"
    return "OTHER"


def first_failure(reason_codes: Iterable[str]) -> str:
    codes = set(reason_codes)
    for category in FIRST_FAILURE_ORDER:
        if category in codes:
            return category
    return "NONE"


# ---------------------------------------------------------------------------
# Market snapshots, legs and book choice
# ---------------------------------------------------------------------------
@dataclass
class Leg:
    row_id: str
    side: str                        # HOME | AWAY
    team_id: str | None
    american: int | None
    decimal_stored: float | None
    observed_at: datetime | None
    fetch_clock: datetime            # available_as_of (NOT a receipt)
    status: str
    payload_checksum: str | None
    source_event_id: str | None
    created_at: datetime | None
    receipt_ledger_id: str | None = None
    receipt_completed_at: datetime | None = None
    job_run_id: str | None = None
    job_completed_at: datetime | None = None
    receipt_reason: str | None = None
    #: This leg's OWN provider update clock (SGO ``byBookmaker[book].lastUpdatedAt``),
    #: read from the retained raw response of the fetch that produced the row.
    #: None unless that response was retained and matched the stored rows.
    provider_updated_at: datetime | None = None
    #: JSON locator of ``provider_updated_at`` inside the retained response.
    provider_updated_field: str | None = None

    @property
    def quote_clock(self) -> datetime | None:
        """The update clock the 0..15-minute rule reads for this leg.

        The genuine per-leg provider stamp when it was recovered from the
        retained response; otherwise the stored ``observed_at`` (for SGO, the
        pair's OLDER side stamp written on both legs), flagged as not per-leg.
        A fetch or ``available_as_of`` clock is never used.
        """

        return self.provider_updated_at or self.observed_at

    @property
    def per_leg_clock(self) -> bool:
        return self.provider_updated_at is not None

    @property
    def available_by(self) -> datetime | None:
        """Completed-receipt upper bound: the later of ledger completion and job completion."""

        bounds = [b for b in (self.receipt_completed_at, self.job_completed_at) if b is not None]
        if self.receipt_completed_at is None:
            return None
        return max(bounds)


@dataclass
class Snapshot:
    """One response's legs for one book: the unit a pair must come from."""

    snapshot_id: str
    book_key: str                    # canonical (provider namespace removed)
    provider_key: str
    sportsbook_key: str              # AISP sportsbooks.key
    market_id: str
    source_event_id: str | None
    fetch_clock: datetime
    home: Leg | None = None
    away: Leg | None = None
    duplicates: bool = False
    #: Row-level defects found while grouping (checked with the stage-1 rules).
    row_failures: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    stage: int = 0
    q_home: Fraction | None = None
    overround: Fraction | None = None
    #: The retained raw response this snapshot was linked to (SGO only).
    raw_link: "SgoRawLink | None" = None
    #: The collector's ingest-time skew receipt for this fetch (SGO only).
    skew_receipt: dict[str, Any] | None = None

    @property
    def older_observed(self) -> datetime | None:
        clocks = [leg.quote_clock for leg in (self.home, self.away) if leg and leg.quote_clock]
        return min(clocks) if len(clocks) == 2 else None

    @property
    def available_by(self) -> datetime | None:
        bounds = [leg.available_by for leg in (self.home, self.away) if leg]
        if len(bounds) != 2 or any(b is None for b in bounds):
            return None
        return max(bounds)

    @property
    def eligible(self) -> bool:
        return not self.failures


#: Market stage codes, in the order a snapshot must pass them.
_STAGE_FAILURE = {
    0: "MARKET_NO_TWO_SIDED_SAME_RESPONSE_PAIR",
    1: "MARKET_PAIR_INVALID",
    2: "MARKET_NO_RECEIPT_BY_ANCHOR",
    3: "MARKET_QUOTE_OLDER_THAN_15_MINUTES",
}


def evaluate_snapshot(snapshot: Snapshot, anchor: datetime, home_team_id: str | None,
                      away_team_id: str | None) -> Snapshot:
    """Apply every leg rule to one same-response snapshot, recording each failure."""

    anchor = aware(anchor)
    failures: list[str] = []
    stage = 0
    if snapshot.home is None or snapshot.away is None or snapshot.duplicates:
        if snapshot.duplicates:
            failures.append("PAIR_DUPLICATE_SIDE_IN_RESPONSE")
        if snapshot.home is None:
            failures.append("PAIR_HOME_LEG_MISSING_IN_RESPONSE")
        if snapshot.away is None:
            failures.append("PAIR_AWAY_LEG_MISSING_IN_RESPONSE")
        snapshot.failures, snapshot.stage = failures, 0
        return snapshot
    stage = 1
    invalid: list[str] = list(snapshot.row_failures)
    decimals: dict[str, Fraction] = {}
    for leg, team in ((snapshot.home, home_team_id), (snapshot.away, away_team_id)):
        exact = american_to_decimal_exact(leg.american)
        if exact is None:
            invalid.append(f"LEG_{leg.side}_PRICE_INVALID")
        else:
            decimals[leg.side] = exact
            if leg.decimal_stored is not None and abs(float(exact) - float(leg.decimal_stored)) > 1e-9:
                invalid.append(f"LEG_{leg.side}_STORED_DECIMAL_DISAGREES_WITH_AMERICAN")
        if not team or leg.team_id != team:
            invalid.append(f"LEG_{leg.side}_ORIENTATION_UNPROVED")
        if str(leg.status or "").upper() != "OPEN":
            invalid.append(f"LEG_{leg.side}_STATUS_{str(leg.status or 'MISSING').upper()}")
    if snapshot.home.source_event_id != snapshot.away.source_event_id:
        invalid.append("PAIR_SOURCE_EVENT_MISMATCH")
    if invalid:
        snapshot.failures, snapshot.stage = invalid, stage
        return snapshot
    snapshot.q_home, snapshot.overround = no_vig_home(decimals["HOME"], decimals["AWAY"])
    stage = 2
    receipt: list[str] = []
    for leg in (snapshot.home, snapshot.away):
        if leg.available_by is None:
            receipt.append(f"LEG_{leg.side}_{leg.receipt_reason or 'RECEIPT_NOT_ESTABLISHED'}")
        elif leg.available_by > anchor:
            receipt.append(f"LEG_{leg.side}_RECEIPT_AFTER_ANCHOR")
    if receipt:
        snapshot.failures, snapshot.stage = receipt, stage
        return snapshot
    stage = 3
    ages: list[str] = []
    for leg in (snapshot.home, snapshot.away):
        # The leg's own recovered provider clock where one exists; otherwise the
        # stored older-side clock (unchanged behaviour, flagged in the packet).
        failure = quote_age_failure(leg.quote_clock, anchor)
        if failure:
            ages.append(f"{failure}_{leg.side}")
    snapshot.failures, snapshot.stage = ages, (stage if ages else 4)
    return snapshot


def snapshot_failure_category(snapshot: Snapshot) -> str | None:
    if snapshot.eligible:
        return None
    if snapshot.stage == 3 and any("NEGATIVE" in code for code in snapshot.failures):
        return "MARKET_QUOTE_AGE_NEGATIVE"
    return _STAGE_FAILURE.get(snapshot.stage, "MARKET_PAIR_INVALID")


def choose_book_snapshot(snapshots: list[Snapshot]) -> Snapshot | None:
    """The snapshot that represents one book/provider.

    Eligible first: latest by older-leg observation, then local availability,
    then snapshot id ascending. Otherwise the most advanced failure (the same
    ordering inside the furthest stage reached), so the recorded reason is the
    one closest to success.
    """

    if not snapshots:
        return None
    eligible = [s for s in snapshots if s.eligible]
    pool = eligible or [s for s in snapshots if s.stage == max(x.stage for x in snapshots)]
    floor = datetime.min.replace(tzinfo=UTC)
    # Stable sorts: snapshot id ascending first, then (older-leg observation,
    # availability) descending keeps the id order among exact ties.
    ordered = sorted(pool, key=lambda s: s.snapshot_id)
    ordered = sorted(
        ordered,
        key=lambda s: (s.older_observed or floor, s.available_by or floor),
        reverse=True,
    )
    return ordered[0]


def choose_primary_book(representatives: list[Snapshot]) -> Snapshot | None:
    """Lexicographically first canonical book key, then provider key, among eligible pairs."""

    eligible = [s for s in representatives if s.eligible]
    if not eligible:
        return None
    return min(eligible, key=lambda s: (s.book_key, s.provider_key))


def canonical_book_key(sportsbook_key: str, provider: str) -> str:
    key = str(sportsbook_key or "").strip().lower()
    if provider == SGO and key.startswith("sgo:"):
        return key[4:]
    return key


# ---------------------------------------------------------------------------
# Retained SportsGameOdds raw responses: per-leg clocks, identity, conventions
# ---------------------------------------------------------------------------
#: ``RawArchive`` partition for SportsGameOdds (``<raw_data_root>/sportsgameodds``).
SGO_RAW_DIRECTORY = "sportsgameodds"
SGO_HOME_ODD_ID = "points-home-game-ml-home"
SGO_AWAY_ODD_ID = "points-away-game-ml-away"
#: The oddID/period/bet type AISP's SGO parser required before writing a row.
SGO_PERIOD_ID = "game"
SGO_BET_TYPE_ID = "ml"
#: Identity fields of one SGO odds entry that may be copied. Deliberately NOT
#: ``score``, ``started``/``ended``, ``closeBookOdds``/``closeFairOdds``,
#: consensus odds or anything under ``results``/``teams``/``status``.
_SGO_ODD_IDENTITY_FIELDS = ("oddID", "opposingOddID", "marketName", "statID", "statEntityID",
                            "periodID", "betTypeID", "sideID", "cancelled")
_SGO_AMERICAN = re.compile(r"^[+-]?\d+$")

#: Link outcomes. Only LINKED carries per-leg clocks.
RAW_LINKED = "LINKED"
RAW_NOT_RETAINED = "NOT_RETAINED"
RAW_NO_RECEIPT_WINDOW = "NOT_BINDABLE_NO_COMPLETED_RECEIPT"
RAW_EVENT_ABSENT = "RETAINED_RESPONSE_DOES_NOT_CONTAIN_EVENT"
RAW_DISAGREES = "RETAINED_RESPONSE_DISAGREES_WITH_STORED_LEGS"
RAW_AMBIGUOUS = "AMBIGUOUS_MULTIPLE_RETAINED_RESPONSES_MATCH"


@dataclass(frozen=True)
class RetainedResponse:
    """One retained raw SGO response file bound to one fetch's receipt window."""

    path: str
    sha256: str
    bytes: int
    archived_at: datetime
    archive_clock_source: str
    file_modified_at: datetime
    payload_checksum: str | None
    request_fingerprint: str | None
    payload: dict[str, Any] = field(repr=False, compare=False)


@dataclass
class SgoRawLink:
    """How one stored SGO pair relates to the retained raw response of its fetch."""

    status: str
    reason_codes: list[str]
    candidates_examined: int = 0
    response: RetainedResponse | None = None
    event_id: str | None = None
    provider_book_key: str | None = None
    home_updated_at: datetime | None = None
    away_updated_at: datetime | None = None
    home_field: str | None = None
    away_field: str | None = None
    #: Provider-stated identity of the two odds entries (whitelisted fields only).
    stated: dict[str, Any] | None = None
    #: Other moneyline-style periods the same event lists (periodID/betTypeID/marketName).
    other_moneyline_markets: list[dict[str, Any]] | None = None

    @property
    def linked(self) -> bool:
        return self.status == RAW_LINKED


def sgo_retained_responses(raw_root: str | Path | None, window_start: datetime | None,
                           window_end: datetime | None, *, not_after: datetime,
                           request_fingerprint: str | None = None) -> list[RetainedResponse]:
    """Retained SGO responses archived inside ONE fetch's completed-receipt window.

    A file is a candidate only when all of these hold:

    * it sits in the ``RawArchive`` layout ``sportsgameodds/YYYY/MM/DD/HHMMSS_ffffff_<hex>.json``
      and its name stamp lies in ``[window_start, window_end]`` -- the fetch's
      ``requested_at`` and its completed receipt;
    * a ``RawArchive`` wrapper's ``metadata.source`` is SPORTSGAMEODDS, its
      ``metadata.ingestion_timestamp`` lies in the same window and any
      ``metadata.request_fingerprint`` equals the ledger's; a bare provider
      body (``{"data": [...]}``) is dated by its name stamp;
    * the file was last modified no later than ``not_after`` (the anchor), so a
      file written after the anchor can never supply a clock.
    """

    start, end, cap = aware(window_start), aware(window_end), aware(not_after)
    if raw_root is None or start is None or end is None or end < start:
        return []
    base = Path(raw_root) / SGO_RAW_DIRECTORY
    found: list[RetainedResponse] = []
    day = start.date()
    while day <= end.date():
        folder = base / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        try:
            names = sorted(p.name for p in folder.iterdir() if _RAW_NAME.match(p.name))
        except OSError:
            names = []
        for name in names:
            match = _RAW_NAME.match(name)
            stamp = datetime.strptime(f"{day:%Y%m%d}{match.group(1)}{match.group(2)}",
                                      "%Y%m%d%H%M%S%f").replace(tzinfo=UTC)
            if not start <= stamp <= end:
                continue
            path = folder / name
            try:
                data = path.read_bytes()
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if modified > cap:
                continue
            try:
                document = json.loads(data)
            except ValueError:
                continue
            if not isinstance(document, dict):
                continue
            metadata = document.get("metadata")
            if isinstance(metadata, dict) and isinstance(document.get("payload"), dict):
                if str(metadata.get("source") or "").upper() != SGO:
                    continue
                archived = aware(metadata.get("ingestion_timestamp"))
                if archived is None or not start <= archived <= end:
                    continue
                fingerprint = metadata.get("request_fingerprint")
                if fingerprint and request_fingerprint and str(fingerprint) != str(request_fingerprint):
                    continue
                payload, clock_source = document["payload"], "RAW_ARCHIVE_METADATA_INGESTION_TIMESTAMP"
                checksum = metadata.get("checksum")
            elif isinstance(document.get("data"), list):
                payload, archived, clock_source = document, stamp, "RAW_ARCHIVE_FILE_NAME_STAMP"
                checksum, fingerprint = None, None
            else:
                continue
            if not isinstance(payload.get("data"), list):
                continue
            found.append(RetainedResponse(
                path=str(path), sha256=_sha(data), bytes=len(data), archived_at=archived,
                archive_clock_source=clock_source, file_modified_at=modified,
                payload_checksum=str(checksum) if checksum else None,
                request_fingerprint=str(fingerprint) if fingerprint else None, payload=payload))
        day += timedelta(days=1)
    return found


def _sgo_american(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _SGO_AMERICAN.match(value.strip()):
        return int(value.strip())
    return None


def _odd_locator(event_id: str, odd_id: str, book: str | None = None, name: str | None = None) -> str:
    text = f"data[eventID={event_id}].odds[{odd_id}]"
    if book is not None:
        text += f".byBookmaker[{book}]"
    return f"{text}.{name}" if name else text


def _sgo_moneyline_from_event(event: dict[str, Any], event_id: str, book_key: str
                              ) -> tuple[dict[str, Any] | None, str | None]:
    """Whitelisted moneyline fields of one book in one event, or the reason it is unusable."""

    odds = event.get("odds")
    if not isinstance(odds, dict):
        return None, "EVENT_HAS_NO_ODDS_OBJECT"
    home, away = odds.get(SGO_HOME_ODD_ID), odds.get(SGO_AWAY_ODD_ID)
    if not isinstance(home, dict) or not isinstance(away, dict):
        return None, "MONEYLINE_ODD_ID_ABSENT"
    if (home.get("oddID") != SGO_HOME_ODD_ID or home.get("opposingOddID") != SGO_AWAY_ODD_ID
            or away.get("oddID") != SGO_AWAY_ODD_ID or away.get("opposingOddID") != SGO_HOME_ODD_ID):
        return None, "ODD_ID_PAIRING_DOES_NOT_RECONCILE"
    for entry in (home, away):
        if entry.get("periodID") != SGO_PERIOD_ID or entry.get("betTypeID") != SGO_BET_TYPE_ID:
            return None, "PERIOD_OR_BET_TYPE_DIFFERS"
    sides: dict[str, Any] = {}
    for side, entry, odd_id in (("home", home, SGO_HOME_ODD_ID), ("away", away, SGO_AWAY_ODD_ID)):
        books = entry.get("byBookmaker")
        keys = [k for k in (books or {}) if str(k).strip().lower() == book_key] if isinstance(books, dict) else []
        if len(keys) != 1:
            return None, "BOOKMAKER_NOT_ON_BOTH_SIDES"
        raw = books[keys[0]]
        if not isinstance(raw, dict):
            return None, "BOOKMAKER_ENTRY_MALFORMED"
        updated = aware(raw.get("lastUpdatedAt")) if isinstance(raw.get("lastUpdatedAt"), str) else None
        sides[side] = {
            "providerBookKey": keys[0],
            "american": _sgo_american(raw.get("odds")),
            "oddsAsSent": raw.get("odds") if isinstance(raw.get("odds"), (str, int)) else None,
            "lastUpdatedAt": raw.get("lastUpdatedAt") if isinstance(raw.get("lastUpdatedAt"), str) else None,
            "lastUpdatedAtUtc": updated,
            "available": raw.get("available") if isinstance(raw.get("available"), bool) else None,
            "stated": {name: entry.get(name) for name in _SGO_ODD_IDENTITY_FIELDS
                       if name in entry and isinstance(entry.get(name), (str, bool))},
            "clockField": _odd_locator(event_id, odd_id, keys[0], "lastUpdatedAt"),
        }
    return sides, None


def _other_moneyline_markets(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Distinct (periodID, betTypeID, marketName) of the event's run-moneyline entries, as stated."""

    seen: set[tuple[str, str, str]] = set()
    odds = event.get("odds") if isinstance(event.get("odds"), dict) else {}
    for entry in odds.values():
        if not isinstance(entry, dict) or entry.get("statID") != "points":
            continue
        if entry.get("betTypeID") not in ("ml", "ml3way"):
            continue
        seen.add((str(entry.get("periodID") or ""), str(entry.get("betTypeID") or ""),
                  str(entry.get("marketName") or "")))
    return [{"periodID": p, "betTypeID": b, "marketName": m} for p, b, m in sorted(seen)]


def link_sgo_pair(candidates: list[RetainedResponse], *, event_id: str | None, book_key: str,
                  home_american: int | None, away_american: int | None,
                  stored_observed: datetime | None) -> SgoRawLink:
    """Tie one stored SGO pair to the retained response it came from, or say why not.

    A response matches only when the same event and book appear on BOTH sides
    of the exact full-game moneyline oddIDs, both prices equal the stored
    American prices, both sides carry ``lastUpdatedAt`` and are available, and
    the OLDER of the two stamps equals the stored ``observed_at`` -- the value
    AISP's parser wrote on both legs. Anything else leaves the pair unlinked.
    """

    if not candidates:
        return SgoRawLink(RAW_NOT_RETAINED, ["RAW_RESPONSE_NOT_RETAINED_FOR_THIS_FETCH"], 0)
    matches: list[tuple[RetainedResponse, dict[str, Any], dict[str, Any]]] = []
    problems: list[str] = []
    containing = 0
    for response in candidates:
        events = [e for e in response.payload.get("data") or []
                  if isinstance(e, dict) and str(e.get("eventID") or "") == str(event_id or "")]
        if not events:
            continue
        containing += 1
        if len(events) > 1:
            problems.append("EVENT_ID_REPEATED_IN_RESPONSE")
            continue
        sides, problem = _sgo_moneyline_from_event(events[0], str(event_id), book_key)
        if problem:
            problems.append(problem)
            continue
        home, away = sides["home"], sides["away"]
        if home["american"] is None or away["american"] is None:
            problems.append("RETAINED_PRICE_INVALID")
        elif (home["american"], away["american"]) != (home_american, away_american):
            problems.append("RETAINED_PRICE_DIFFERS_FROM_STORED")
        elif home["lastUpdatedAtUtc"] is None or away["lastUpdatedAtUtc"] is None:
            problems.append("RETAINED_LAST_UPDATED_AT_MISSING")
        elif home["available"] is not True or away["available"] is not True:
            problems.append("RETAINED_SIDE_NOT_AVAILABLE")
        elif stored_observed is None or min(home["lastUpdatedAtUtc"], away["lastUpdatedAtUtc"]) != aware(stored_observed):
            problems.append("RETAINED_OLDER_SIDE_STAMP_DIFFERS_FROM_STORED_OBSERVED_AT")
        else:
            matches.append((response, sides, events[0]))
    if not containing:
        return SgoRawLink(RAW_EVENT_ABSENT, ["RAW_RESPONSE_RETAINED_BUT_EVENT_ABSENT"], len(candidates))
    if not matches:
        return SgoRawLink(RAW_DISAGREES, sorted(set(problems)) or ["RAW_RESPONSE_DISAGREES"], len(candidates))
    if len({m[0].sha256 for m in matches}) > 1:
        return SgoRawLink(RAW_AMBIGUOUS, ["RAW_RESPONSE_AMBIGUOUS_MULTIPLE_FILES_MATCH"], len(candidates))
    response, sides, event = matches[0]
    return SgoRawLink(
        RAW_LINKED, [], len(candidates), response=response, event_id=str(event_id),
        provider_book_key=sides["home"]["providerBookKey"],
        home_updated_at=sides["home"]["lastUpdatedAtUtc"], away_updated_at=sides["away"]["lastUpdatedAtUtc"],
        home_field=sides["home"]["clockField"], away_field=sides["away"]["clockField"],
        stated={side: {**sides[side]["stated"], "oddsAsSent": sides[side]["oddsAsSent"],
                       "lastUpdatedAt": sides[side]["lastUpdatedAt"], "available": sides[side]["available"]}
                for side in ("home", "away")},
        other_moneyline_markets=_other_moneyline_markets(event))


def sgo_skew_receipt(job: Any, ledger: Any, succeeded_rows_for_job: int) -> dict[str, Any] | None:
    """The collector's ingest-time skew receipt for one fetch, when it names that fetch alone.

    ``collect-odds-checkpoint`` records ``result.fallback.skew`` -- pairs,
    pairs whose two sides carried different ``lastUpdatedAt`` values, and the
    largest difference -- over every two-sided moneyline pair it parsed from
    the response. It is attributed to a fetch only when the job made exactly
    one successful SGO request and its recorded filters equal the ledger's.
    It is carried as evidence; it is never used as a per-leg clock.
    """

    result = getattr(job, "result", None)
    fallback = result.get("fallback") if isinstance(result, dict) else None
    skew = fallback.get("skew") if isinstance(fallback, dict) else None
    if not isinstance(skew, dict) or succeeded_rows_for_job != 1:
        return None
    pairs, with_skew, largest = skew.get("pairs"), skew.get("pairs_with_skew"), skew.get("max_skew_seconds")
    if not isinstance(pairs, int) or not isinstance(with_skew, int):
        return None
    shape = ((getattr(ledger, "response_metadata", None) or {}).get("request_shape") or {}).get("filters")
    filters_match = shape is not None and shape == fallback.get("request_filters")
    if not filters_match:
        return None
    return {
        "jobRunId": str(job.id),
        "jobName": job.job_name,
        "jobCompletedAtUtc": iso(job.completed_at),
        "field": "job_runs.result.fallback.skew",
        "pairs": pairs,
        "pairsWithSkew": with_skew,
        "maxSkewSeconds": largest,
        "simultaneityClaimed": skew.get("simultaneity_claimed"),
        "zeroSkewForEveryPair": bool(pairs > 0 and with_skew == 0 and largest in (0, 0.0)),
        "requestFiltersMatchLedger": True,
        "meaning": ("Recorded by the collector when it parsed this response: over every two-sided moneyline "
                    "pair it held, pairsWithSkew pairs had differing home/away lastUpdatedAt and the largest "
                    "difference was maxSkewSeconds. An ingest-time receipt, not re-verifiable from retained "
                    "bytes; carried as evidence and never used as a per-leg clock."),
    }


# ---------------------------------------------------------------------------
# Evidence document and refs
# ---------------------------------------------------------------------------
def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=_json_default).encode("utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Fraction):
        return float(value)
    if hasattr(value, "value"):
        return value.value
    return str(value)


@dataclass(frozen=True)
class EvidencePointer:
    """A ref into the evidence document, resolved once its digest is known."""

    locator: str


class EvidenceDocument:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.files: dict[str, dict[str, Any]] = {}
        self.notes: dict[str, Any] = {}

    def add(self, table: str, row_id: Any, data: dict[str, Any]) -> EvidencePointer:
        key = str(row_id)
        self.rows.setdefault(table, {})[key] = _plain(data)
        return EvidencePointer(f"/rows/{table}/{key}")

    def add_file(self, name: str, data: dict[str, Any]) -> EvidencePointer:
        self.files[name] = _plain(data)
        return EvidencePointer(f"/files/{name}")

    def note(self, name: str, data: Any) -> EvidencePointer:
        self.notes[name] = _plain(data)
        return EvidencePointer(f"/notes/{name}")


def _plain(value: Any) -> Any:
    return json.loads(canonical_bytes(value))


def file_ref(path: str | Path, locator: str | None = None) -> dict[str, Any] | None:
    """A ref with the real digest and byte count, or None when the file cannot be read."""

    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return {"path": str(path), "sha256": _sha(data), "bytes": len(data), "locator": locator}


def _resolve_pointers(value: Any, ref_for: dict[str, Any]) -> Any:
    if isinstance(value, EvidencePointer):
        return {**ref_for, "locator": value.locator}
    if isinstance(value, dict):
        return {k: _resolve_pointers(v, ref_for) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_pointers(v, ref_for) for v in value]
    return value


def assert_no_outcome_fields(value: Any, where: str = "packet") -> None:
    """Refuse any outcome-shaped key anywhere in the document."""

    stack: list[tuple[Any, str]] = [(value, where)]
    while stack:
        item, path = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if key in FORBIDDEN_OUTCOME_KEYS:
                    raise OutcomeFieldError(f"Outcome-shaped key {key!r} at {path}")
                stack.append((child, f"{path}/{key}"))
        elif isinstance(item, list):
            stack.extend((child, f"{path}[{i}]") for i, child in enumerate(item))


# ---------------------------------------------------------------------------
# Schema validation: jsonschema when installed, else a minimal structural check
# ---------------------------------------------------------------------------
_MINIMAL_KEYWORDS = frozenset({
    "$schema", "$id", "title", "type", "additionalProperties", "required", "properties", "enum",
    "const", "$ref", "anyOf", "items", "minimum", "maximum", "exclusiveMinimum", "minLength",
    "pattern", "format", "$defs",
})
_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")


def validate_packet(packet: dict[str, Any], schema: dict[str, Any]) -> tuple[str, list[str]]:
    """(validator name, errors). Uses ``jsonschema`` if importable."""

    try:
        from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-not-found]
    except ImportError:
        return "MINIMAL_STRUCTURAL_CHECK_JSONSCHEMA_UNAVAILABLE", _minimal_validate(packet, schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = [f"{'/'.join(str(p) for p in error.path)}: {error.message}"
              for error in validator.iter_errors(packet)]
    return "jsonschema.Draft202012Validator", errors


def _minimal_validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _check_keywords(schema, errors, "#")
    if errors:
        return errors
    _validate_node(instance, schema, schema, "", errors)
    return errors


def _check_keywords(node: Any, errors: list[str], path: str) -> None:
    if isinstance(node, dict):
        for key, child in node.items():
            if key not in _MINIMAL_KEYWORDS:
                errors.append(f"{path}: schema keyword {key!r} unsupported by the minimal validator")
            if key in ("properties", "$defs"):
                for name, sub in (child or {}).items():
                    _check_keywords(sub, errors, f"{path}/{key}/{name}")
            elif key in ("items", "additionalProperties") and isinstance(child, dict):
                _check_keywords(child, errors, f"{path}/{key}")
            elif key == "anyOf":
                for i, sub in enumerate(child):
                    _check_keywords(sub, errors, f"{path}/anyOf/{i}")


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate_node(value: Any, node: dict[str, Any], root: dict[str, Any], path: str,
                   errors: list[str]) -> None:
    if "$ref" in node:
        ref = str(node["$ref"])
        if not ref.startswith("#/"):
            errors.append(f"{path}: non-local $ref {ref}")
            return
        target: Any = root
        for part in ref[2:].split("/"):
            target = target[part]
        _validate_node(value, target, root, path, errors)
    if "anyOf" in node:
        if not any(not _collect(value, sub, root, path) for sub in node["anyOf"]):
            errors.append(f"{path}: matches no anyOf branch")
    if "type" in node:
        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not any(_type_ok(value, t) for t in types):
            errors.append(f"{path}: expected {types}, got {type(value).__name__}")
            return
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{path}: {value!r} not in {node['enum']}")
    if "const" in node and value != node["const"]:
        errors.append(f"{path}: {value!r} != const {node['const']!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in node and value < node["minimum"]:
            errors.append(f"{path}: {value} < minimum {node['minimum']}")
        if "maximum" in node and value > node["maximum"]:
            errors.append(f"{path}: {value} > maximum {node['maximum']}")
        if "exclusiveMinimum" in node and value <= node["exclusiveMinimum"]:
            errors.append(f"{path}: {value} <= exclusiveMinimum {node['exclusiveMinimum']}")
    if isinstance(value, str):
        if "minLength" in node and len(value) < node["minLength"]:
            errors.append(f"{path}: shorter than {node['minLength']}")
        if "pattern" in node and not re.search(node["pattern"], value):
            errors.append(f"{path}: does not match {node['pattern']}")
        if node.get("format") == "date-time" and not _DATE_TIME.match(value):
            errors.append(f"{path}: not an RFC 3339 date-time")
    if isinstance(value, dict):
        for name in node.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required {name!r}")
        properties = node.get("properties", {})
        for name, child in value.items():
            if name in properties:
                _validate_node(child, properties[name], root, f"{path}/{name}", errors)
            elif node.get("additionalProperties") is False:
                errors.append(f"{path}: additional property {name!r} not allowed")
    if isinstance(value, list) and isinstance(node.get("items"), dict):
        for i, child in enumerate(value):
            _validate_node(child, node["items"], root, f"{path}[{i}]", errors)


def _collect(value: Any, node: dict[str, Any], root: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    _validate_node(value, node, root, path, errors)
    return errors


# ---------------------------------------------------------------------------
# Readers (identity columns only; never an outcome column)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GameIdentity:
    id: str
    league_id: str
    starts_at: datetime
    source_mode: str
    scheduled_innings: int | None
    created_at: datetime | None


def read_game(session: Session, game_id: Any) -> GameIdentity | None:
    row = session.execute(
        select(Game.id, Game.league_id, Game.starts_at, Game.source_mode, Game.scheduled_innings,
               Game.created_at).where(Game.id == _uuid(game_id))
    ).first()
    if row is None:
        return None
    return GameIdentity(str(row.id), str(row.league_id), aware(row.starts_at), row.source_mode,
                        row.scheduled_innings, aware(row.created_at))


def mlb_games_with_locks_between(session: Session, start: datetime, end: datetime) -> list[GameIdentity]:
    """LIVE MLB games whose research lock is in [start, end). Identity columns only."""

    offset = timedelta(minutes=LOCK_MINUTES_BEFORE_START)
    rows = session.execute(
        select(Game.id, Game.league_id, Game.starts_at, Game.source_mode, Game.scheduled_innings,
               Game.created_at)
        .where(in_league(MLB), Game.source_mode == "LIVE",
               Game.starts_at >= aware(start) + offset, Game.starts_at < aware(end) + offset)
        .order_by(Game.starts_at, Game.id)
    ).all()
    return [GameIdentity(str(r.id), str(r.league_id), aware(r.starts_at), r.source_mode,
                         r.scheduled_innings, aware(r.created_at)) for r in rows]


def _uuid(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return value


# ---------------------------------------------------------------------------
# Phase evidence: the retained raw MLB Stats API schedule capture
# ---------------------------------------------------------------------------
_RAW_NAME = re.compile(r"^(\d{6})_(\d{6})_[0-9a-f]+\.json$")
_PHASE_FIELDS = ("gamePk", "gameType", "gameDate", "officialDate", "doubleHeader", "gameNumber",
                 "seriesDescription", "season")


def find_phase_evidence(raw_root: str | Path, game_pk: str | None, lock: datetime,
                        *, days_back: int = 4) -> dict[str, Any] | None:
    """The latest retained schedule capture archived at or before the lock naming this gamePk.

    Only whitelisted identity fields of that one game are read (never a
    linescore, status or score). The capture must be archived -- by its own
    metadata, its file name and its file modification time -- at or before the
    lock.
    """

    if not game_pk:
        return None
    lock = aware(lock)
    base = Path(raw_root) / "mlb_stats_api"
    needle = str(game_pk).encode()
    for back in range(days_back + 1):
        day = (lock - timedelta(days=back)).date()
        folder = base / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        try:
            names = sorted((p.name for p in folder.iterdir() if _RAW_NAME.match(p.name)), reverse=True)
        except OSError:
            continue
        for name in names:
            match = _RAW_NAME.match(name)
            stamp = datetime.strptime(f"{day:%Y%m%d}{match.group(1)}{match.group(2)}",
                                      "%Y%m%d%H%M%S%f").replace(tzinfo=UTC)
            if stamp > lock:
                continue
            path = folder / name
            try:
                data = path.read_bytes()
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if needle not in data or modified > lock:
                continue
            try:
                document = json.loads(data)
            except ValueError:
                continue
            metadata = document.get("metadata") or {}
            archived = aware(metadata.get("ingestion_timestamp"))
            if archived is None or archived > lock or str(metadata.get("source")) != "MLB_STATS_API":
                continue
            schedule = (document.get("payload") or {}).get("schedule") or {}
            for day_block in schedule.get("dates") or []:
                for game in day_block.get("games") or []:
                    if str(game.get("gamePk")) != str(game_pk):
                        continue
                    teams = game.get("teams") or {}
                    fields = {key: game.get(key) for key in _PHASE_FIELDS}
                    fields["homeTeamId"] = str(((teams.get("home") or {}).get("team") or {}).get("id") or "")
                    fields["awayTeamId"] = str(((teams.get("away") or {}).get("team") or {}).get("id") or "")
                    return {
                        "path": str(path),
                        "sha256": _sha(data),
                        "bytes": len(data),
                        "archivedAtUtc": iso(archived),
                        "fileModifiedAtUtc": iso(modified),
                        "payloadChecksum": metadata.get("checksum"),
                        "fields": fields,
                    }
    return None


# ---------------------------------------------------------------------------
# Quarantine gate
# ---------------------------------------------------------------------------
def quarantine_gate(ledger_path: str | Path, game_pk: str | None, local_date: date) -> dict[str, Any]:
    """Apply the whole-game quarantine ledger to identity metadata only."""

    reasons: list[str] = []
    try:
        data = Path(ledger_path).read_bytes()
    except OSError:
        return {"ledgerSha256": None, "eligible": False, "reasonCodes": ["QUARANTINE_LEDGER_UNREADABLE"],
                "ledgerPath": str(ledger_path), "ledgerReadable": False}
    digest = _sha(data)
    try:
        ledger = json.loads(data)
    except ValueError:
        return {"ledgerSha256": digest, "eligible": False, "reasonCodes": ["QUARANTINE_LEDGER_UNPARSEABLE"],
                "ledgerPath": str(ledger_path), "ledgerReadable": True}
    if digest != QUARANTINE_LEDGER_SHA256_AT_FREEZE:
        reasons.append("LIMITATION_QUARANTINE_LEDGER_DIFFERS_FROM_FREEZE_VERSION_APPLIED_AS_FOUND")
    excluded = {str(game.get("mlbamGamePk")) for game in ledger.get("games") or []}
    eligible = True
    if not game_pk:
        eligible = False
        reasons.append("QUARANTINE_NO_MLBAM_GAME_PK_TO_CHECK")
    elif str(game_pk) in excluded:
        eligible = False
        reasons.append("QUARANTINE_GAME_PERMANENTLY_EXCLUDED")
    window = ledger.get("originalFinalOot") or {}
    start, end = window.get("startDate"), window.get("endDate")
    if start and end and str(start) <= local_date.isoformat() <= str(end):
        eligible = False
        reasons.append("QUARANTINE_GAME_IN_SEALED_FINAL_OOT_WINDOW")
    return {"ledgerSha256": digest, "eligible": eligible, "reasonCodes": reasons,
            "ledgerPath": str(ledger_path), "ledgerReadable": True,
            "ledgerSha256AtFreeze": QUARANTINE_LEDGER_SHA256_AT_FREEZE}


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
@dataclass
class BuildInputs:
    protocol: FrozenProtocol
    quarantine_ledger_path: Path
    raw_data_root: Path
    experiment_id: str = V18_EXPERIMENT_ID
    mode: str = MODE_PROSPECTIVE


@dataclass
class PacketBuild:
    episode_key: str
    packet: dict[str, Any]
    packet_bytes: bytes
    evidence: dict[str, Any]
    evidence_bytes: bytes
    evidence_name: str
    diagnostics: dict[str, Any]
    validator: str
    schema_errors: list[str]


def build_packet(
    session: Session,
    game: GameIdentity,
    *,
    inputs: BuildInputs,
    built_at: datetime,
    attempt_ref: dict[str, Any],
) -> PacketBuild:
    """Assemble one packet for the game's T-15 research lock. Read-only."""

    built_at = aware(built_at)
    start = aware(game.starts_at)
    lock = research_lock(start)
    deadline = seal_deadline(start)
    evidence = EvidenceDocument()
    limitations: list[str] = []

    # -- identity -----------------------------------------------------------
    mappings = session.execute(
        select(ExternalIdMapping.entity_type, ExternalIdMapping.entity_id, ExternalIdMapping.source,
               ExternalIdMapping.external_id, ExternalIdMapping.created_at)
        .where(ExternalIdMapping.entity_type == "game", ExternalIdMapping.entity_id == _uuid(game.id))
        .order_by(ExternalIdMapping.source, ExternalIdMapping.external_id)
    ).all()
    source_ids: dict[str, Any] = {}
    for m in mappings:
        source_ids.setdefault(m.source, []).append(m.external_id)
        evidence.add("external_id_mappings", f"game:{m.source}:{m.external_id}", {
            "entity_type": m.entity_type, "entity_id": m.entity_id, "source": m.source,
            "external_id": m.external_id, "created_at_transaction_start": m.created_at})
    source_ids = {k: (v[0] if len(v) == 1 else v) for k, v in source_ids.items()}
    game_pk = source_ids.get("MLB_STATS_API") if isinstance(source_ids.get("MLB_STATS_API"), str) else None
    key = episode_key(game.id, game_pk, lock)

    sides = session.execute(
        select(GameTeam.team_id, GameTeam.is_home).where(GameTeam.game_id == _uuid(game.id))
    ).all()
    home_team = next((str(s.team_id) for s in sides if s.is_home), None)
    away_team = next((str(s.team_id) for s in sides if not s.is_home), None)
    team_ids = [t for t in (home_team, away_team) if t]
    team_sources: dict[str, dict[str, str]] = {}
    if team_ids:
        for m in session.execute(
            select(ExternalIdMapping.entity_id, ExternalIdMapping.source, ExternalIdMapping.external_id)
            .where(ExternalIdMapping.entity_type == "team",
                   ExternalIdMapping.entity_id.in_([_uuid(t) for t in team_ids]))
        ).all():
            team_sources.setdefault(str(m.entity_id), {})[m.source] = m.external_id
    game_ptr = evidence.add("games", game.id, {
        "id": game.id, "league_id": game.league_id, "starts_at": game.starts_at,
        "source_mode": game.source_mode, "scheduled_innings": game.scheduled_innings,
        "created_at_transaction_start": game.created_at,
        "game_teams": {"home_team_id": home_team, "away_team_id": away_team},
        "team_source_ids": team_sources,
    })

    observations = session.execute(
        select(GameStateObservation.id, GameStateObservation.observed_at, GameStateObservation.available_as_of,
               GameStateObservation.created_at, GameStateObservation.source, GameStateObservation.source_record_id,
               GameStateObservation.payload_checksum, GameStateObservation.scheduled_at,
               GameStateObservation.doubleheader_status, GameStateObservation.game_number,
               GameStateObservation.home_team_id, GameStateObservation.away_team_id)
        .where(GameStateObservation.game_id == _uuid(game.id), GameStateObservation.source_mode == "LIVE")
        .order_by(GameStateObservation.available_as_of, GameStateObservation.id)
    ).all()
    first_seen = next((aware(o.available_as_of) for o in observations), None)
    at_lock = [o for o in observations if aware(o.available_as_of) <= lock]
    revision = at_lock[-1] if at_lock else None
    identity_reasons: list[str] = []
    if first_seen is None:
        first_seen = game.created_at
        identity_reasons.append("LIMITATION_FIRST_SEEN_IS_GAMES_CREATED_AT_TRANSACTION_START")
    if first_seen is not None and first_seen > lock:
        identity_reasons.append("IDENTITY_FIRST_SEEN_AFTER_LOCK_LATE_DISCOVERY")
    if revision is not None:
        revision_ptr = evidence.add("game_state_observations", revision.id, {
            "id": revision.id, "observed_at": revision.observed_at, "available_as_of": revision.available_as_of,
            "created_at_transaction_start": revision.created_at, "source": revision.source,
            "source_record_id": revision.source_record_id, "payload_checksum": revision.payload_checksum,
            "scheduled_at": revision.scheduled_at, "doubleheader_status": revision.doubleheader_status,
            "game_number": revision.game_number, "home_team_id": revision.home_team_id,
            "away_team_id": revision.away_team_id,
        })
        schedule_revision = (f"games.starts_at={iso(start)};game_state_observation={revision.id};"
                             f"payload_checksum={revision.payload_checksum}")
        if aware(revision.scheduled_at) != start:
            identity_reasons.append("LIMITATION_SCHEDULE_REVISION_AT_LOCK_NAMES_A_DIFFERENT_START")
    else:
        revision_ptr = None
        schedule_revision = f"games.starts_at={iso(start)};game_state_observation=NONE_BY_LOCK"
        identity_reasons.append("LIMITATION_NO_SCHEDULE_OBSERVATION_AVAILABLE_BY_LOCK")
    if not home_team or not away_team:
        identity_reasons.append("IDENTITY_INCOMPLETE")
        identity_reasons.append("IDENTITY_GAME_TEAMS_MISSING")
    if not game_pk:
        identity_reasons.append("IDENTITY_INCOMPLETE")
        identity_reasons.append("IDENTITY_MLBAM_GAME_PK_MISSING")

    local_date = start.astimezone(EASTERN).date()
    gate = quarantine_gate(inputs.quarantine_ledger_path, game_pk, local_date)
    if not gate["eligible"]:
        identity_reasons.append("IDENTITY_QUARANTINE_EXCLUDED")
    ledger_file_ref = file_ref(inputs.quarantine_ledger_path) if gate.get("ledgerReadable") else None

    phase_evidence = find_phase_evidence(inputs.raw_data_root, game_pk, lock)
    if phase_evidence is None:
        phase = "UNKNOWN"
        phase_ref = None
        identity_reasons.append("PHASE_NO_RETAINED_SCHEDULE_CAPTURE_WITH_GAME_TYPE_BY_LOCK")
    else:
        phase = phase_from_game_type(phase_evidence["fields"].get("gameType"))
        phase_ref = {"path": phase_evidence["path"], "sha256": phase_evidence["sha256"],
                     "bytes": phase_evidence["bytes"],
                     "locator": (f"payload.schedule.dates[].games[gamePk={game_pk}].gameType="
                                 f"{phase_evidence['fields'].get('gameType')};archivedAtUtc="
                                 f"{phase_evidence['archivedAtUtc']}")}
        evidence.add_file("phaseEvidence", phase_evidence)
        home_src = team_sources.get(home_team or "", {}).get("MLB_STATS_API")
        away_src = team_sources.get(away_team or "", {}).get("MLB_STATS_API")
        if home_src and phase_evidence["fields"]["homeTeamId"] and home_src != phase_evidence["fields"]["homeTeamId"]:
            identity_reasons.append("IDENTITY_INCOMPLETE")
            identity_reasons.append("IDENTITY_SCHEDULE_CAPTURE_HOME_TEAM_DISAGREES")
        if away_src and phase_evidence["fields"]["awayTeamId"] and away_src != phase_evidence["fields"]["awayTeamId"]:
            identity_reasons.append("IDENTITY_INCOMPLETE")
            identity_reasons.append("IDENTITY_SCHEDULE_CAPTURE_AWAY_TEAM_DISAGREES")
    if phase != "R":
        identity_reasons.append("PHASE_NOT_CERTIFIED_R")

    # -- decisions, positions, selection ----------------------------------
    experiment = session.execute(
        select(ShadowExperiment.id, ShadowExperiment.started_at, ShadowExperiment.decision_policy,
               ShadowExperiment.recommendation_policy_version, ShadowExperiment.controlling_engine,
               ShadowExperiment.model_era_id, ShadowExperiment.version)
        .where(ShadowExperiment.id == _uuid(inputs.experiment_id))
    ).first()
    decision_rows = session.scalars(
        select(ShadowExperimentDecision).where(
            ShadowExperimentDecision.experiment_id == _uuid(inputs.experiment_id),
            ShadowExperimentDecision.game_id == _uuid(game.id),
            ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
        ).order_by(ShadowExperimentDecision.recommended_at, ShadowExperimentDecision.id)
    ).all()
    candidates = [DecisionCandidate(
        id=str(d.id), recommended_at=aware(d.recommended_at), recommendation=str(d.recommendation),
        shadow_forecast_id=str(d.shadow_forecast_id) if d.shadow_forecast_id else None,
        forecast_id=str(d.forecast_id) if d.forecast_id else None,
    ) for d in decision_rows]
    decision_ids = [d.id for d in decision_rows]
    position_rows = session.execute(
        select(OfficialAspPosition.id, OfficialAspPosition.shadow_decision_id,
               OfficialAspPosition.shadow_experiment_id, OfficialAspPosition.shadow_forecast_id,
               OfficialAspPosition.market_outcome_id, OfficialAspPosition.odds_snapshot_id,
               OfficialAspPosition.placed_at, OfficialAspPosition.created_at)
        .where(OfficialAspPosition.shadow_experiment_id == _uuid(inputs.experiment_id),
               OfficialAspPosition.shadow_decision_id.in_(decision_ids) if decision_ids else False)
        .order_by(OfficialAspPosition.id)
    ).all()
    positions = [PositionLink(str(p.id), str(p.shadow_decision_id) if p.shadow_decision_id else None,
                              str(p.shadow_forecast_id) if p.shadow_forecast_id else None)
                 for p in position_rows]
    anchor = choose_anchor(candidates, positions, lock)

    window_start = lock - timedelta(minutes=DECISION_WINDOW_MINUTES)
    in_flight = session.execute(
        select(JobRun.id, JobRun.started_at, JobRun.status)
        .where(JobRun.job_name == FREEZE_JOB, JobRun.started_at <= lock,
               JobRun.started_at >= window_start - timedelta(minutes=10),
               JobRun.completed_at.is_(None))
    ).all()
    freeze_runs = session.execute(
        select(JobRun.id, JobRun.started_at, JobRun.completed_at, JobRun.status)
        .where(JobRun.job_name == FREEZE_JOB, JobRun.started_at >= window_start - timedelta(minutes=10),
               JobRun.started_at <= lock)
        .order_by(JobRun.started_at)
    ).all()
    export_complete = not in_flight
    status, selection_reasons = selection_status(
        anchor, experiment_present=experiment is not None, export_complete=export_complete)
    if not freeze_runs:
        selection_reasons.append("LIMITATION_NO_FREEZE_JOB_RUN_STARTED_IN_DECISION_WINDOW")
    for d in decision_rows:
        evidence.add("shadow_experiment_decisions", d.id, {
            "id": d.id, "experiment_id": d.experiment_id, "game_id": d.game_id, "phase": d.phase,
            "recommended_at": d.recommended_at, "created_at_transaction_start": d.created_at,
            "recommendation": d.recommendation, "recommendation_reason": d.recommendation_reason,
            "qualification_reasons": d.qualification_reasons, "market": d.market, "period": d.period,
            "selection": d.selection, "sportsbook": d.sportsbook, "american_odds": d.american_odds,
            "decimal_odds": d.decimal_odds, "market_outcome_id": d.market_outcome_id,
            "odds_snapshot_id": d.odds_snapshot_id, "quote_observed_at": d.quote_observed_at,
            "shadow_forecast_id": d.shadow_forecast_id, "forecast_id": d.forecast_id,
            "forecast_generated_at": d.forecast_generated_at, "forecast_cutoff": d.forecast_cutoff,
            "model_probability": d.model_probability, "no_vig_probability": d.no_vig_probability,
            "evidence_fingerprint": d.evidence_fingerprint, "source_mode": d.source_mode,
            "inDecisionWindowT30T15": any(c.id == str(d.id) for c in anchor.candidates),
        })
    for p in position_rows:
        evidence.add("official_asp_positions", p.id, {
            "id": p.id, "shadow_decision_id": p.shadow_decision_id,
            "shadow_experiment_id": p.shadow_experiment_id, "shadow_forecast_id": p.shadow_forecast_id,
            "market_outcome_id": p.market_outcome_id, "odds_snapshot_id": p.odds_snapshot_id,
            "placed_at": p.placed_at, "created_at_transaction_start": p.created_at,
        })
        if aware(p.created_at) and aware(p.created_at) > lock:
            selection_reasons.append("LIMITATION_POSITION_ROW_MATERIALISED_AFTER_LOCK_FROM_LOCKED_DECISION")
    if status == "SELECTED" and not position_rows:
        selection_reasons.append("LIMITATION_SELECTED_DECISION_HAS_NO_POSITION_ROW_AT_BUILD")
    for run in freeze_runs:
        evidence.add("job_runs", run.id, {"id": run.id, "job_name": FREEZE_JOB, "status": run.status,
                                          "started_at": run.started_at, "completed_at": run.completed_at})
    candidates_ptr = evidence.note("candidateDecisions", {
        "experimentId": inputs.experiment_id,
        "window": {"startUtc": iso(window_start), "endUtc": iso(lock)},
        "candidateDecisionIds": [c.id for c in anchor.candidates],
        "allFinalPregameDecisionIds": [str(d) for d in decision_ids],
        "positionIds": [p.id for p in positions],
        "anchorRule": "POSITION_DECISION_ELSE_LATEST_RECOMMENDED_AT_TIE_DECISION_ID_ASC",
    })
    selection_export_ptr = evidence.note("selectionExport", {
        "experimentId": inputs.experiment_id,
        "experiment": dict(experiment._mapping) if experiment is not None else None,
        "freezeRunsInFlightAtBuild": [str(r.id) for r in in_flight],
        "completeAsOfExport": export_complete,
        "source": "shadow_experiment_decisions (all FINAL_PREGAME rows for experiment and game)",
    })

    # -- forecast ------------------------------------------------------------
    anchor_at = anchor.at
    forecast_block, forecast_reasons, forecast_diag = _forecast_block(
        session, game, anchor, anchor_at, lock, deadline, evidence)

    # -- market ---------------------------------------------------------------
    if anchor_at is not None:
        market_block, market_reasons, market_diag = _market_block(
            session, game, anchor_at, home_team, away_team, evidence, raw_data_root=inputs.raw_data_root)
    else:
        market_block = {"status": "UNPRICED", "candidates": [], "selectedBookKey": None,
                        "selectedProviderKey": None, "pair": None, "qHome": None,
                        "reasonCodes": ["ANCHOR_AMBIGUOUS"]}
        market_reasons, market_diag = ["ANCHOR_AMBIGUOUS"], {}

    # -- eligibility ---------------------------------------------------------
    anchor_reasons = list(anchor.reason_codes)
    if anchor.kind == "NONE_EXCLUDED":
        anchor_reasons.append("ANCHOR_AMBIGUOUS")
    all_reasons = list(dict.fromkeys(
        identity_reasons + list(gate["reasonCodes"]) + anchor_reasons + forecast_reasons
        + market_reasons + selection_reasons + limitations))
    forecast_eligible = forecast_block is not None and not any(
        r in ("FORECAST_MISSING", "FORECAST_INTEGRITY_FAILED", "FORECAST_NOT_AVAILABLE_BY_ANCHOR")
        for r in forecast_reasons)
    price_evaluable = market_block["status"] == "FRESH_PAIRED"
    eligibility = {
        "forecastEligible": bool(forecast_eligible),
        "priceEvaluable": bool(price_evaluable),
        "selectionAscertained": status != "UNRESOLVED",
        "reasonCodes": all_reasons,
        "firstFailure": first_failure(all_reasons),
        "primaryPopulationPhaseR": phase == "R",
        "identityEligible": gate["eligible"] and "IDENTITY_INCOMPLETE" not in identity_reasons,
    }

    source_refs: list[Any] = [EvidencePointer("")]
    if phase_ref:
        source_refs.append(phase_ref)
    if ledger_file_ref:
        source_refs.append(ledger_file_ref)
    protocol_ref = {"path": str(FROZEN_DIR / "protocol.json"), "sha256": PROTOCOL_SHA256,
                    "bytes": inputs.protocol.protocol_bytes, "locator": "vendored byte-exact copy"}
    schema_ref = {"path": str(FROZEN_DIR / "PACKET_SCHEMA.json"), "sha256": PACKET_SCHEMA_SHA256,
                  "bytes": inputs.protocol.schema_bytes, "locator": "vendored byte-exact copy"}
    source_refs.extend([protocol_ref, schema_ref])
    for ref in forecast_diag.pop("exchangeFileRefs", []):
        source_refs.append(ref)

    packet: dict[str, Any] = {
        "protocolSha256": PROTOCOL_SHA256,
        "episodeKey": key,
        "sealedAtUtc": iso(built_at),
        "attemptLedgerRef": attempt_ref,
        "game": {
            "canonicalGameId": game.id,
            "sourceGameIds": source_ids,
            "homeId": home_team or "",
            "awayId": away_team or "",
            "homeSourceIds": team_sources.get(home_team or "", {}),
            "awaySourceIds": team_sources.get(away_team or "", {}),
            "scheduleRevision": schedule_revision,
            "scheduledStartUtc": iso(start),
            "localDate": local_date.isoformat(),
            "phase": phase,
            "phaseEvidenceRef": phase_ref,
            "firstSeenAtUtc": iso(first_seen) or iso(built_at),
            "quarantineIdentityGate": gate,
            "identityRef": game_ptr,
            "scheduleRevisionRef": revision_ptr,
            "reasonCodes": list(dict.fromkeys(identity_reasons)),
        },
        "anchor": {
            "kind": anchor.kind,
            "atUtc": iso(anchor_at),
            "researchLockUtc": iso(lock),
            "sealDeadlineUtc": iso(deadline),
            "decisionId": anchor.decision.id if anchor.decision else None,
            "selectionCheckpointDecisionId": (anchor.selection_decision.id
                                              if anchor.selection_decision else None),
            "candidateDecisionsRef": candidates_ptr,
            "decisionWindowUtc": [iso(window_start), iso(lock)],
            "leadTimeMinutesBeforeStart": _minutes(start - anchor_at) if anchor_at else None,
            "reasonCodes": anchor_reasons,
        },
        "forecast": forecast_block,
        "selection": {
            "statusAtResearchLock": status,
            "recordedAtUtc": iso(built_at),
            "positionIds": [p.id for p in positions],
            "consumerExportRef": selection_export_ptr,
            "completeAsOfExport": bool(export_complete and experiment is not None),
            "reasonCodes": list(dict.fromkeys(selection_reasons)),
            "experimentId": inputs.experiment_id,
            "policy": getattr(experiment, "decision_policy", None) if experiment is not None else None,
            "consumerExportIdentity": EXPORT_IDENTITY,
        },
        "market": market_block,
        "eligibility": eligibility,
        "sourceRefs": source_refs,
    }

    evidence_doc = {
        "schemaVersion": EVIDENCE_SCHEMA,
        "episodeKey": key,
        "protocolSha256": PROTOCOL_SHA256,
        "exportIdentity": EXPORT_IDENTITY,
        "mode": inputs.mode,
        "builtAtUtc": iso(built_at),
        "clockConventions": {
            "created_at_transaction_start": "PostgreSQL now(): the start of the inserting transaction; "
                                            "not an insertion, commit or publication time",
            "odds_available_as_of": "SGO: fetch.requested_at; not a receipt and never an update clock",
            "odds_observed_at": "SGO: the OLDER of the pair's two provider lastUpdatedAt stamps, written by "
                                "the ingest parser on BOTH legs; not per-leg",
            "leg_quoteUpdatedAtUtc": "the leg's own SGO byBookmaker lastUpdatedAt when recovered from the "
                                     "retained raw response of its fetch (quoteUpdatedAtIsPerLeg=true); "
                                     "otherwise odds_observed_at (quoteUpdatedAtIsPerLeg=false)",
            "leg_availableByUtc": "max(provider_request_ledger.completed_at, job_runs.completed_at of the "
                                  "requesting job) -- a completed-receipt upper bound on commit",
            "retained_response_archivedAtUtc": "RawArchive metadata.ingestion_timestamp (or its file-name "
                                               "stamp); when AISP wrote the bytes, not an update clock",
        },
        "rows": evidence.rows,
        "files": evidence.files,
        "notes": evidence.notes,
    }
    assert_no_outcome_fields(evidence_doc, "evidence")
    evidence_bytes = canonical_bytes(evidence_doc)
    evidence_sha = _sha(evidence_bytes)
    evidence_name = f"{key}.evidence-{evidence_sha[:16]}.json"
    ref_for = {"path": evidence_name, "sha256": evidence_sha, "bytes": len(evidence_bytes)}
    packet = _resolve_pointers(packet, ref_for)
    packet["sourceRefs"][0]["locator"] = None
    packet = _plain(packet)
    assert_no_outcome_fields(packet, "packet")
    validator, errors = validate_packet(packet, inputs.protocol.schema)
    packet_bytes = canonical_bytes(packet)

    diagnostics = {
        "episodeKey": key,
        "gamePk": game_pk,
        "lock": iso(lock),
        "anchorKind": anchor.kind,
        "anchorAt": iso(anchor_at),
        "selection": status,
        "phase": phase,
        **forecast_diag,
        **market_diag,
    }
    return PacketBuild(key, packet, packet_bytes, evidence_doc, evidence_bytes, evidence_name,
                       diagnostics, validator, errors)


# ---------------------------------------------------------------------------
# Forecast block
# ---------------------------------------------------------------------------
def _forecast_block(session: Session, game: GameIdentity, anchor: AnchorChoice, anchor_at: datetime | None,
                    lock: datetime, deadline: datetime, evidence: EvidenceDocument
                    ) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    diag: dict[str, Any] = {"forecastIdentity": None, "forecastPublishedAtUtc": None,
                            "forecastImportReceiptUtc": None, "exchangeFileRefs": []}
    if anchor_at is None:
        return None, ["ANCHOR_AMBIGUOUS"], diag
    rows = session.execute(
        select(ShadowForecast, ShadowForecastBundle)
        .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
        .where(ShadowForecast.canonical_game_id == _uuid(game.id), ShadowForecast.engine_id == ENGINE_ID)
        .order_by(ShadowForecast.forecast_id)
    ).all()
    reasons: list[str] = []
    chosen = None
    if anchor.kind == "ACTUAL_DECISION":
        named = anchor.decision.shadow_forecast_id if anchor.decision else None
        if named is None:
            reasons.extend(["FORECAST_MISSING", "FORECAST_DECISION_NAMES_NO_V18_SHADOW_FORECAST"])
            return None, reasons, diag
        chosen = next(((f, b) for f, b in rows if str(f.id) == named), None)
        if chosen is None:
            reasons.extend(["FORECAST_MISSING", "FORECAST_DECISION_NAMES_A_NON_V18_OR_ABSENT_ROW"])
            return None, reasons, diag
    else:
        available = [(f, b) for f, b in rows
                     if aware(f.available_as_of_utc) and aware(f.available_as_of_utc) <= lock
                     and aware(b.generated_at) and aware(b.generated_at) <= lock]
        legitimate = [(f, b) for f, b in available if not _integrity_failures(f, b)]
        pool = legitimate or available
        if not pool:
            reasons.extend(["FORECAST_MISSING", "FORECAST_NO_V18_PRODUCER_HANDOFF_AVAILABLE_BY_LOCK"])
            if rows:
                reasons.append("LIMITATION_V18_FORECAST_EXISTS_BUT_PUBLISHED_AFTER_LOCK")
            return None, reasons, diag
        newest = max(aware(b.generated_at) for _, b in pool)
        chosen = min(((f, b) for f, b in pool if aware(b.generated_at) == newest), key=lambda fb: fb[0].forecast_id)
        reasons.append("ANCHOR_RESEARCH_FORECAST_IS_LATEST_HANDOFF_BY_LOCK_TIE_FORECAST_ID_ASC")
    forecast, bundle = chosen
    lineage = forecast.lineage_detail or {}
    uncertainty = forecast.uncertainty or {}
    integrity = _integrity_failures(forecast, bundle)
    if integrity:
        reasons.append("FORECAST_INTEGRITY_FAILED")
        reasons.extend(integrity)
    created = aware(forecast.available_as_of_utc)
    cutoff = aware(forecast.source_cutoff_utc)
    published = aware(bundle.generated_at)
    receipt = _import_receipt(session, bundle)
    clock_failures: list[str] = []
    if cutoff is None or cutoff > anchor_at:
        clock_failures.append("FORECAST_SOURCE_CUTOFF_AFTER_ANCHOR_OR_MISSING")
    if created is None or created > anchor_at:
        clock_failures.append("FORECAST_CREATED_AFTER_ANCHOR_OR_MISSING")
    if published is None or published > anchor_at:
        clock_failures.append("FORECAST_PRODUCER_HANDOFF_COMPLETED_AFTER_ANCHOR_OR_MISSING")
    import_completed = aware(receipt.completed_at) if receipt is not None else None
    if anchor.kind == "ACTUAL_DECISION":
        if import_completed is None:
            reasons.append("LIMITATION_IMPORT_RECEIPT_NOT_LOCATED_DECISION_BINDING_PROVES_LOCAL_AVAILABILITY")
        elif import_completed > anchor_at:
            clock_failures.append("FORECAST_IMPORT_RECEIPT_AFTER_DECISION")
    else:
        if import_completed is None:
            reasons.append("LIMITATION_IMPORT_RECEIPT_NOT_LOCATED")
        elif import_completed > deadline:
            clock_failures.append("FORECAST_NOT_RETAINED_BY_SEAL_DEADLINE")
    if clock_failures:
        reasons.append("FORECAST_NOT_AVAILABLE_BY_ANCHOR")
        reasons.extend(clock_failures)
    if forecast.scheduled_start_utc and aware(forecast.scheduled_start_utc) != aware(game.starts_at):
        reasons.append("LIMITATION_FORECAST_SCHEDULED_START_DIFFERS_FROM_CURRENT_START")

    population = lineage.get("populationGeometry") or {}
    play = lineage.get("playGeometry") or {}
    forecast_ptr = evidence.add("shadow_forecasts", forecast.id, {
        "id": forecast.id, "forecast_id": forecast.forecast_id, "canonical_game_id": forecast.canonical_game_id,
        "engine_id": forecast.engine_id, "engine_version": forecast.engine_version,
        "forecast_schema_version": forecast.forecast_schema_version, "snapshot_type": forecast.snapshot_type,
        "model_artifact_sha256": forecast.model_artifact_sha256,
        "model_manifest_sha256": forecast.model_manifest_sha256,
        "feature_vector_sha256": forecast.feature_vector_sha256, "simulator_id": forecast.simulator_id,
        "simulator_version": forecast.simulator_version, "simulator_seed": forecast.simulator_seed,
        "source_event_id": forecast.source_event_id, "event_date": forecast.event_date,
        "season_type_importer_constant": forecast.season_type,
        "home_team_id": forecast.home_team_id, "away_team_id": forecast.away_team_id,
        "status": forecast.status, "scheduled_start_utc": forecast.scheduled_start_utc,
        "schedule_as_of_utc": forecast.schedule_as_of_utc, "source_cutoff_utc": forecast.source_cutoff_utc,
        "forecast_cutoff_utc": forecast.forecast_cutoff_utc,
        "available_as_of_utc_producer_forecast_created": forecast.available_as_of_utc,
        "created_at_transaction_start": forecast.created_at,
        "full_home_win_probability": forecast.full_home_win_probability,
        "full_away_win_probability": forecast.full_away_win_probability,
        "uncertainty": uncertainty, "readiness_detail": forecast.readiness_detail,
        "identity_evidence": forecast.identity_evidence,
        "lineage": {k: lineage.get(k) for k in (
            "bundleId", "bundleSha256", "objectSha256", "parentBundleId", "parentBundleSha256",
            "acceptanceProtocolV2Sha256", "evidenceCutoff", "sourceArtifact", "inputReceipts", "pathDigest")},
        "playGeometry": {k: play.get(k) for k in ("supplementId", "supplementSha256", "parentBundleSha256")}
        if play else None,
        "populationGeometry": {k: population.get(k) for k in (
            "schemaVersion", "supplementId", "supplementSha256", "parentBundleId", "parentBundleSha256",
            "playGeometrySupplementSha256", "populationDigest", "executionAuthoritySha256", "registeredAt",
            "artifactsVerified")} if population else None,
    })
    bundle_ptr = evidence.add("shadow_forecast_bundles", bundle.id, {
        "id": bundle.id, "bundle_sha256_handoff": bundle.bundle_sha256, "contract_version": bundle.contract_version,
        "bundle_id": bundle.bundle_id, "logical_uri": bundle.logical_uri,
        "receipt_sha256": bundle.source_fixture_id, "slate_date": bundle.slate_date, "status": bundle.status,
        "generated_at_producer_handoff": bundle.generated_at, "imported_at_registration_clock": bundle.imported_at,
        "created_at_transaction_start": bundle.created_at, "forecast_count": bundle.forecast_count,
        "evidence": bundle.evidence,
    })
    receipt_ptr = None
    if receipt is not None:
        receipt_ptr = evidence.add("job_runs", receipt.id, {
            "id": receipt.id, "job_name": V18_IMPORT_JOB, "status": receipt.status,
            "started_at": receipt.started_at, "completed_at": receipt.completed_at,
            "names_handoff_sha256": bundle.bundle_sha256})

    object_root = lineage.get("sourceArtifact")
    exchange_refs: list[dict[str, Any]] = []
    handoff_ref = file_ref(bundle.logical_uri, "aisp-handoff.json (producer slate handoff)") \
        if bundle.logical_uri else None
    evidence_ref = file_ref(Path(object_root) / "pregame-evidence.json", "pregame-evidence.json") \
        if object_root else None
    object_ref = None
    if handoff_ref is not None and object_root:
        manifest_name = _object_manifest_name(bundle.logical_uri, lineage.get("objectSha256"))
        if manifest_name:
            object_ref = file_ref(Path(object_root) / manifest_name, f"object manifest {manifest_name}")
    for ref in (handoff_ref, evidence_ref, object_ref):
        if ref:
            exchange_refs.append(ref)
    if not exchange_refs:
        reasons.append("LIMITATION_EXCHANGE_FILES_UNREADABLE_BY_EXPORTER_DB_CONTENT_ADDRESSES_USED")
    evidence.note("exchangeFiles", {"readable": exchange_refs, "objectRoot": object_root,
                                    "handoffPath": bundle.logical_uri})

    se = uncertainty.get("monteCarloStandardError")
    p_home = float(forecast.full_home_win_probability)
    binomial = math.sqrt(p_home * (1 - p_home) / SIMULATION_COUNT) if 0 <= p_home <= 1 else None
    if se is None:
        reasons.append("LIMITATION_SAMPLING_ERROR_FIELD_ABSENT")
    if not population.get("populationDigest"):
        # AISP retains a hash-bound population reference only for slates whose
        # handoff carried the Simulation Population view. Otherwise the paired
        # scores live solely inside the immutable producer object; the ref then
        # names that object's content address. Nothing is rerun or rebuilt.
        reasons.append("LIMITATION_SCORE_POPULATION_NOT_RETAINED_BY_CONSUMER_RESOLVE_VIA_PRODUCER_OBJECT")
    source_clocks = []
    for side, detail in ((forecast.readiness_detail or {}).get("sides") or {}).items():
        for label, key in (("lineupObservedAt", "lineupObservedAt"), ("starterObservedAt", "starterObservedAt")):
            if detail.get(key):
                source_clocks.append({"side": side, "field": label, "atUtc": iso(detail.get(key)),
                                      "status": detail.get("lineupStatus" if "lineup" in key else "starterState"),
                                      "precedesSourceCutoff": bool(aware(detail.get(key)) and cutoff
                                                                   and aware(detail.get(key)) <= cutoff)})
    inputs_receipts = lineage.get("inputReceipts") or {}
    if inputs_receipts.get("pregameSnapshotCapturedAt"):
        source_clocks.append({"field": "pregameSnapshotCapturedAt",
                              "atUtc": iso(inputs_receipts.get("pregameSnapshotCapturedAt")),
                              "meaning": "producer evidence capture (source cutoff)"})
    if any(c.get("precedesSourceCutoff") is False for c in source_clocks):
        reasons.append("LIMITATION_A_SOURCE_OBSERVATION_POSTDATES_THE_DECLARED_CUTOFF")

    block = {
        "forecastIdentity": forecast.forecast_id,
        "objectRef": object_ref or evidence.note("objectContentAddress", {
            "objectSha256": lineage.get("objectSha256"), "objectRoot": object_root,
            "meaning": "content address of the producer object manifest body (not file bytes); the "
                       "object files were not readable by this exporter"}),
        "modelIdentity": {
            "engine": forecast.engine_id,
            "paSha256": forecast.model_artifact_sha256 or None,
            "transitionSha256": None,
            "parentBundleSha256": lineage.get("parentBundleSha256"),
            "treatmentManifestSha256": None,
            "calibration": None,
            "engineVersion": forecast.engine_version,
            "reasonCodes": ["LIMITATION_TRANSITION_SHA_NOT_RETAINED_BY_AISP_BOUND_VIA_PARENT_BUNDLE",
                            "LIMITATION_TREATMENT_MANIFEST_SHA_NOT_RETAINED_BY_AISP_BOUND_VIA_PARENT_BUNDLE",
                            "LIMITATION_CALIBRATION_NOT_RETAINED_BY_AISP"],
        },
        "inputSnapshotRef": evidence_ref or evidence.note("inputSnapshotContentAddress", {
            "snapshotContentSha256": forecast.feature_vector_sha256,
            "pregameSnapshotCapturedAt": inputs_receipts.get("pregameSnapshotCapturedAt")}),
        "createdAtUtc": iso(created) or iso(forecast.created_at),
        "aispImportTransactionStartedAtUtc": iso(forecast.created_at),
        "publicationReceiptRef": handoff_ref or bundle_ptr,
        "publicationCompletedAtUtc": iso(published) or iso(created) or iso(forecast.created_at),
        "publicationClockMeaning": "shadow_forecast_bundles.generated_at: the producer's hash-bound slate "
                                   "handoff generatedAt (handoffSha256 in the evidence), an upper bound on "
                                   "producer availability; not a consumer receipt",
        "sourceCutoffUtc": iso(cutoff) or iso(created) or iso(forecast.created_at),
        "importReceiptRef": receipt_ptr,
        "importReceiptCompletedAtUtc": iso(import_completed),
        "pHome": p_home,
        "pAway": float(forecast.full_away_win_probability),
        "scorePopulationRef": EvidencePointer(f"/rows/shadow_forecasts/{forecast.id}/populationGeometry")
        if population.get("populationDigest") else evidence.note("scorePopulationViaProducerObject", {
            "objectSha256": lineage.get("objectSha256"), "objectRoot": object_root,
            "meaning": "AISP holds no population digest for this forecast; the 5,000 paired scores are "
                       "bound only inside the immutable producer object named here"}),
        "scorePopulationBinding": {
            "supplementSha256": population.get("supplementSha256"),
            "populationDigest": population.get("populationDigest"),
        } if population else None,
        "simulationCount": SIMULATION_COUNT if uncertainty.get("simulationCount") == SIMULATION_COUNT
        else uncertainty.get("simulationCount"),
        "seed": forecast.simulator_seed,
        "samplingError": {
            "producerField": "monteCarloUncertainty.homeWinProbabilityStandardError "
                             "(stored as shadow_forecasts.uncertainty.monteCarloStandardError)",
            "value": se,
            "meaning": ("Monte Carlo standard error of pHome as recorded by the producer"
                        if se is not None else "PRODUCER_FIELD_ABSENT: not recorded, not recomputed"),
            "binomialSqrtPQOver5000": binomial,
        },
        "sourceClockEvidence": source_clocks,
        "operationalEvidence": {
            "forecastRef": forecast_ptr,
            "bundleRef": bundle_ptr,
            "status": forecast.status,
            "isReady": forecast.is_ready,
            "illegalStateCount": uncertainty.get("illegalStateCount"),
            "nonterminationCount": uncertainty.get("nonterminationCount"),
            "deterministicReplayVerified": uncertainty.get("deterministicReplayVerified"),
            "monteCarloMethod": uncertainty.get("monteCarloMethod"),
            "integrityFailures": integrity,
        },
        "deploymentAttestation": None,
        "reasonCodes": list(dict.fromkeys(
            [r for r in reasons if r.startswith(("FORECAST_", "LIMITATION_"))]
            + ["LIMITATION_NO_PER_FORECAST_DEPLOYMENT_ATTESTATION_RETAINED"])),
    }
    if block["simulationCount"] != SIMULATION_COUNT:
        # The schema pins 5,000; a different count is not representable, so the
        # forecast is recorded in the evidence and the packet forecast is null.
        reasons.append("FORECAST_INTEGRITY_FAILED")
        reasons.append("FORECAST_SIMULATION_COUNT_NOT_5000")
        return None, reasons, diag
    diag.update({
        "forecastIdentity": forecast.forecast_id,
        "forecastPublishedAtUtc": iso(published),
        "forecastCreatedAtUtc": iso(created),
        "forecastImportReceiptUtc": iso(import_completed),
        "forecastHasScorePopulation": bool(population),
        "exchangeFileRefs": exchange_refs,
    })
    return block, reasons, diag


def _integrity_failures(forecast: ShadowForecast, bundle: ShadowForecastBundle) -> list[str]:
    failures: list[str] = []
    lineage = forecast.lineage_detail or {}
    uncertainty = forecast.uncertainty or {}
    if forecast.engine_id != ENGINE_ID:
        failures.append("FORECAST_ENGINE_NOT_V18")
    if (forecast.model_artifact_sha256 or "") != FROZEN_MODEL["paSha256"]:
        failures.append("FORECAST_PA_MODEL_SHA_DIFFERS_FROM_FROZEN")
    if lineage.get("parentBundleSha256") != FROZEN_MODEL["parentBundleSha256"]:
        failures.append("FORECAST_PARENT_BUNDLE_SHA_DIFFERS_FROM_FROZEN")
    if uncertainty.get("simulationCount") != SIMULATION_COUNT:
        failures.append("FORECAST_SIMULATION_COUNT_NOT_5000")
    if uncertainty.get("illegalStateCount") not in (0, None) or uncertainty.get("nonterminationCount") not in (0, None):
        failures.append("FORECAST_ILLEGAL_OR_NONTERMINATING_PATHS")
    if uncertainty.get("illegalStateCount") is None or uncertainty.get("nonterminationCount") is None:
        failures.append("FORECAST_PATH_LEGALITY_NOT_RECORDED")
    if uncertainty.get("deterministicReplayVerified") is not True:
        failures.append("FORECAST_DETERMINISTIC_REPLAY_NOT_VERIFIED")
    p_home, p_away = forecast.full_home_win_probability, forecast.full_away_win_probability
    if (p_home is None or p_away is None or not (0.0 <= p_home <= 1.0) or not (0.0 <= p_away <= 1.0)
            or abs(p_home + p_away - 1.0) > 1e-9):
        failures.append("FORECAST_PROBABILITIES_NOT_NORMALIZED")
    se = uncertainty.get("monteCarloStandardError")
    if se is not None and p_home is not None and 0.0 <= p_home <= 1.0:
        expected = math.sqrt(p_home * (1 - p_home) / SIMULATION_COUNT)
        if abs(float(se) - expected) > max(1e-9, 1e-6 * expected):
            failures.append("FORECAST_SAMPLING_ERROR_INCONSISTENT_WITH_PHOME_AND_5000")
    if not forecast.forecast_id:
        failures.append("FORECAST_IDENTITY_MISSING")
    created = aware(forecast.available_as_of_utc)
    cutoff = aware(forecast.source_cutoff_utc)
    start = aware(forecast.scheduled_start_utc)
    if created is not None and cutoff is not None and cutoff > created:
        failures.append("FORECAST_SOURCE_CUTOFF_AFTER_PUBLICATION")
    if created is not None and start is not None and created >= start:
        failures.append("FORECAST_PUBLISHED_AT_OR_AFTER_SCHEDULED_START")
    return failures


def _import_receipt(session: Session, bundle: ShadowForecastBundle) -> Any | None:
    """The consumer job run that imported this handoff (completed_at bounds the commit)."""

    created = aware(bundle.created_at)
    registered = aware(bundle.imported_at) or created
    if created is None:
        return None
    rows = session.execute(
        select(JobRun.id, JobRun.status, JobRun.started_at, JobRun.completed_at, JobRun.result)
        .where(JobRun.job_name == V18_IMPORT_JOB, JobRun.started_at <= created,
               JobRun.completed_at.is_not(None), JobRun.completed_at >= registered)
        .order_by(JobRun.started_at.desc()).limit(3)
    ).all()
    for row in rows:
        if bundle.bundle_sha256 and bundle.bundle_sha256 in json.dumps(row.result or {}, default=str):
            return row
    return None


def _object_manifest_name(handoff_path: str, object_sha: str | None) -> str | None:
    try:
        handoff = json.loads(Path(handoff_path).read_bytes())
    except (OSError, ValueError):
        return None
    for entry in handoff.get("forecasts") or []:
        if str(entry.get("objectSha256")) == str(object_sha):
            name = str(entry.get("manifestFile") or "")
            return name if name and "/" not in name and "\\" not in name and ".." not in name else None
    return None


# ---------------------------------------------------------------------------
# Market block
# ---------------------------------------------------------------------------
def _market_block(session: Session, game: GameIdentity, anchor_at: datetime, home_team: str | None,
                  away_team: str | None, evidence: EvidenceDocument, *, raw_data_root: str | Path | None = None
                  ) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    rows = session.execute(
        select(OddsSnapshot.id, OddsSnapshot.market_outcome_id, OddsSnapshot.sportsbook_id,
               OddsSnapshot.observed_at, OddsSnapshot.available_as_of, OddsSnapshot.american_odds,
               OddsSnapshot.decimal_odds, OddsSnapshot.point, OddsSnapshot.status, OddsSnapshot.source,
               OddsSnapshot.source_event_id, OddsSnapshot.payload_checksum, OddsSnapshot.created_at,
               MarketOutcome.participant_team_id, MarketOutcome.outcome_key, MarketOutcome.label,
               Market.id.label("market_id"), Market.period, Sportsbook.key.label("sportsbook_key"))
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id == _uuid(game.id), Market.market_type == MarketType.MONEYLINE,
               Market.period == "full_game", OddsSnapshot.source_mode == "LIVE",
               OddsSnapshot.available_as_of <= anchor_at)
        .order_by(OddsSnapshot.available_as_of, OddsSnapshot.id)
    ).all()
    diag: dict[str, Any] = {"quoteRowsFetchedByAnchor": len(rows)}
    if not rows:
        block = {"status": "UNPRICED", "candidates": [], "selectedBookKey": None, "selectedProviderKey": None,
                 "pair": None, "qHome": None, "reasonCodes": ["MARKET_NO_QUOTE_FETCHED_BY_ANCHOR"],
                 "anchorUtc": iso(anchor_at), "maxQuoteAgeMinutes": 15}
        diag.update({"freshestLegAgeMinutes": None, "freshestAvailableLegAgeMinutes": None,
                     "freshestReceiptedPairOlderLegAgeMinutes": None, "latestFetchClockUtc": None,
                     "minutesLatestFetchBeforeAnchor": None, "books": 0, "booksWithCompletePair": 0,
                     "eligibleBooks": 0, "eligibleBooksLedgerOnlyBound": 0,
                     "marketFailureCategory": "MARKET_NO_QUOTE_FETCHED_BY_ANCHOR", "primaryBook": None,
                     "primaryOverround": None, **_raw_diagnostics([], None)})
        return block, ["MARKET_NO_QUOTE_FETCHED_BY_ANCHOR"], diag

    # Completed receipts for every fetch clock, by exact requested_at match.
    receipts: dict[tuple[str, datetime], list[Any]] = {}
    by_source: dict[str, set[datetime]] = {}
    for r in rows:
        by_source.setdefault(r.source, set()).add(aware(r.available_as_of))
    for source, clocks in by_source.items():
        for ledger in session.execute(
            select(ProviderRequestLedger.id, ProviderRequestLedger.provider, ProviderRequestLedger.endpoint_key,
                   ProviderRequestLedger.requested_at, ProviderRequestLedger.completed_at,
                   ProviderRequestLedger.status, ProviderRequestLedger.request_fingerprint,
                   ProviderRequestLedger.records_received, ProviderRequestLedger.job_run_id,
                   ProviderRequestLedger.reservation_id, ProviderRequestLedger.response_metadata)
            .where(ProviderRequestLedger.provider == source, ProviderRequestLedger.status == "SUCCEEDED",
                   ProviderRequestLedger.requested_at.in_(sorted(clocks)))
        ).all():
            receipts.setdefault((source, aware(ledger.requested_at)), []).append(ledger)
    job_ids = {l.job_run_id for group in receipts.values() for l in group if l.job_run_id}
    jobs = {}
    succeeded_per_job: dict[Any, int] = {}
    if job_ids:
        for job in session.execute(
            select(JobRun.id, JobRun.job_name, JobRun.status, JobRun.started_at, JobRun.completed_at,
                   JobRun.result)
            .where(JobRun.id.in_(list(job_ids)))
        ).all():
            jobs[job.id] = job
        for job_id, count in session.execute(
            select(ProviderRequestLedger.job_run_id, func.count())
            .where(ProviderRequestLedger.job_run_id.in_(list(job_ids)), ProviderRequestLedger.provider == SGO,
                   ProviderRequestLedger.status == "SUCCEEDED")
            .group_by(ProviderRequestLedger.job_run_id)
        ).all():
            succeeded_per_job[job_id] = int(count)

    snapshots: dict[str, Snapshot] = {}
    for r in rows:
        fetch = aware(r.available_as_of)
        provider = r.source
        snap_id = f"{provider}|{iso(fetch)}|{r.source_event_id or ''}|{r.sportsbook_key}"
        snap = snapshots.get(snap_id)
        if snap is None:
            snap = snapshots[snap_id] = Snapshot(
                snapshot_id=snap_id, book_key=canonical_book_key(r.sportsbook_key, provider),
                provider_key=provider, sportsbook_key=r.sportsbook_key, market_id=str(r.market_id),
                source_event_id=r.source_event_id, fetch_clock=fetch)
        participant = str(r.participant_team_id) if r.participant_team_id else None
        side = "HOME" if participant and participant == home_team else (
            "AWAY" if participant and participant == away_team else None)
        leg = Leg(row_id=str(r.id), side=side or "UNKNOWN", team_id=participant, american=r.american_odds,
                  decimal_stored=r.decimal_odds, observed_at=aware(r.observed_at), fetch_clock=fetch,
                  status=_enum_value(r.status), payload_checksum=r.payload_checksum,
                  source_event_id=r.source_event_id, created_at=aware(r.created_at))
        group = receipts.get((provider, fetch), [])
        if len(group) == 1:
            ledger = group[0]
            leg.receipt_ledger_id = str(ledger.id)
            leg.receipt_completed_at = aware(ledger.completed_at)
            if ledger.job_run_id and ledger.job_run_id in jobs:
                job = jobs[ledger.job_run_id]
                leg.job_run_id = str(job.id)
                leg.job_completed_at = aware(job.completed_at)
                if job.completed_at is None:
                    leg.receipt_completed_at = None
                    leg.receipt_reason = "RECEIPT_JOB_RUN_NOT_COMPLETED"
        elif len(group) > 1:
            leg.receipt_reason = "RECEIPT_AMBIGUOUS_MULTIPLE_LEDGER_ROWS"
        else:
            leg.receipt_reason = "RECEIPT_NOT_ESTABLISHED_NO_COMPLETED_LEDGER_ROW"
        if r.point is not None:
            snap.row_failures.append("LEG_MONEYLINE_ROW_CARRIES_A_POINT")
        if side is None:
            # An unoriented row is kept as a failing leg on whichever side is
            # free; the stage-1 orientation rule then names it.
            side = "HOME" if snap.home is None else "AWAY"
            leg.side = side
            leg.team_id = None
        current = snap.home if side == "HOME" else snap.away
        if current is not None:
            snap.duplicates = True
        elif side == "HOME":
            snap.home = leg
        else:
            snap.away = leg

    # Link every SGO pair to the retained raw response of ITS fetch (bound by
    # that fetch's completed receipt window). Only a matched response supplies
    # per-leg clocks; everything else keeps the stored older-side clock.
    candidates_by_fetch: dict[tuple[str, datetime], list[RetainedResponse]] = {}
    for snap in snapshots.values():
        if snap.provider_key != SGO:
            continue
        group = receipts.get((snap.provider_key, snap.fetch_clock), [])
        ledger = group[0] if len(group) == 1 else None
        if ledger is not None and ledger.job_run_id in jobs:
            snap.skew_receipt = sgo_skew_receipt(jobs[ledger.job_run_id], ledger,
                                                 succeeded_per_job.get(ledger.job_run_id, 0))
        _link_snapshot(snap, ledger, raw_data_root, anchor_at, candidates_by_fetch)

    for snap in snapshots.values():
        evaluate_snapshot(snap, anchor_at, home_team, away_team)
        snap.limitations = _snapshot_limitations(snap)

    by_book: dict[tuple[str, str], list[Snapshot]] = {}
    for snap in snapshots.values():
        by_book.setdefault((snap.book_key, snap.provider_key), []).append(snap)
    representatives = [choose_book_snapshot(group) for _, group in sorted(by_book.items())]
    representatives = [r for r in representatives if r is not None]
    primary = choose_primary_book(representatives)

    candidates = [_book_pair(snap, anchor_at, evidence, receipts, jobs) for snap in representatives]
    categories = [snapshot_failure_category(s) for s in representatives]
    market_reasons: list[str] = []
    if primary is None:
        stage_order = list(FIRST_FAILURE_ORDER)
        failing = [c for c in categories if c]
        best = max(failing, key=stage_order.index) if failing else "MARKET_NO_TWO_SIDED_SAME_RESPONSE_PAIR"
        market_reasons.append(best)
        market_reasons.append("MARKET_NO_ELIGIBLE_BOOK")
    # Limitations never fail a pair; they say, per candidate, what could not be
    # proved (per-leg clocks, the retained response, provider-stated conventions).
    limitations = list(dict.fromkeys(code for s in representatives for code in s.limitations))
    primary_pair = None
    q_home = None
    if primary is not None:
        primary_pair = next(c for c, s in zip(candidates, representatives) if s is primary)
        q_home = float(primary.q_home)
    block = {
        "status": "FRESH_PAIRED" if primary is not None else "UNPRICED",
        "candidates": candidates,
        "selectedBookKey": primary.book_key if primary else None,
        "selectedProviderKey": primary.provider_key if primary else None,
        "pair": primary_pair,
        "qHome": q_home,
        "reasonCodes": list(dict.fromkeys(market_reasons + limitations)),
        "anchorUtc": iso(anchor_at),
        "maxQuoteAgeMinutes": 15,
        "bookRule": "LEXICOGRAPHIC_CANONICAL_BOOK_THEN_PROVIDER_AMONG_FRESH_ELIGIBLE_PAIRS",
        "snapshotRule": "LATEST_ELIGIBLE_BY_OLDER_LEG_OBSERVATION_THEN_AVAILABILITY_THEN_SNAPSHOT_ID_ASC",
    }

    # Feasibility diagnostics (never part of an eligibility rule).
    all_legs = [leg for s in snapshots.values() for leg in (s.home, s.away) if leg and leg.quote_clock]
    available_legs = [leg for leg in all_legs if leg.available_by is not None and leg.available_by <= anchor_at]
    complete = [s for s in snapshots.values() if s.stage >= 2]
    receipted = [s for s in snapshots.values() if s.stage >= 3]
    ledger_only = 0
    for group in by_book.values():
        if any(_eligible_with_ledger_only(s, anchor_at) for s in group):
            ledger_only += 1
    diag.update(_raw_diagnostics(list(snapshots.values()), primary))
    diag.update({
        "freshestLegAgeMinutes": _minutes(anchor_at - max(l.quote_clock for l in all_legs)) if all_legs else None,
        "freshestAvailableLegAgeMinutes": _minutes(anchor_at - max(l.quote_clock for l in available_legs))
        if available_legs else None,
        "freshestReceiptedPairOlderLegAgeMinutes": _minutes(anchor_at - max(s.older_observed for s in receipted))
        if receipted else None,
        "latestFetchClockUtc": iso(max(s.fetch_clock for s in snapshots.values())),
        "minutesLatestFetchBeforeAnchor": _minutes(anchor_at - max(s.fetch_clock for s in snapshots.values())),
        "books": len(by_book),
        "booksWithCompletePair": len({(s.book_key, s.provider_key) for s in complete}),
        "eligibleBooks": sum(1 for r in representatives if r.eligible),
        "eligibleBooksLedgerOnlyBound": ledger_only,
        "marketFailureCategory": market_reasons[0] if market_reasons else None,
        "primaryBook": primary.book_key if primary else None,
        "primaryOverround": float(primary.overround) if primary else None,
    })
    return block, market_reasons + limitations, diag


def _eligible_with_ledger_only(snapshot: Snapshot, anchor_at: datetime) -> bool:
    """Sensitivity: would this snapshot pass if the ledger completion alone bounded availability?"""

    if snapshot.stage < 2 or snapshot.home is None or snapshot.away is None:
        return False
    for leg in (snapshot.home, snapshot.away):
        if leg.receipt_completed_at is None or leg.receipt_completed_at > anchor_at:
            return False
        if quote_age_failure(leg.quote_clock, anchor_at):
            return False
    return True


def _link_snapshot(snap: Snapshot, ledger: Any | None, raw_root: str | Path | None, anchor_at: datetime,
                   cache: dict[Any, list[RetainedResponse]]) -> None:
    """Attach the retained raw response (and per-leg clocks) of this SGO pair's own fetch."""

    if snap.home is None or snap.away is None or snap.duplicates:
        snap.raw_link = SgoRawLink("NOT_ATTEMPTED_PAIR_INCOMPLETE", ["RAW_LINK_NOT_ATTEMPTED_PAIR_INCOMPLETE"])
        return
    if ledger is None or ledger.completed_at is None:
        snap.raw_link = SgoRawLink(RAW_NO_RECEIPT_WINDOW,
                                   ["RAW_RESPONSE_NOT_BINDABLE_WITHOUT_ONE_COMPLETED_LEDGER_RECEIPT"])
        return
    bounds = [aware(ledger.completed_at)] + [leg.job_completed_at for leg in (snap.home, snap.away)
                                             if leg.job_completed_at is not None]
    key = str(ledger.id)
    if key not in cache:
        cache[key] = sgo_retained_responses(raw_root, aware(ledger.requested_at), max(bounds),
                                            not_after=anchor_at, request_fingerprint=ledger.request_fingerprint)
    stored = [leg.observed_at for leg in (snap.home, snap.away) if leg.observed_at is not None]
    link = link_sgo_pair(cache[key], event_id=snap.source_event_id, book_key=snap.book_key,
                         home_american=snap.home.american, away_american=snap.away.american,
                         stored_observed=min(stored) if len(stored) == 2 else None)
    snap.raw_link = link
    if link.linked:
        snap.home.provider_updated_at, snap.home.provider_updated_field = link.home_updated_at, link.home_field
        snap.away.provider_updated_at, snap.away.provider_updated_field = link.away_updated_at, link.away_field


def _snapshot_limitations(snap: Snapshot) -> list[str]:
    codes: list[str] = []
    if snap.provider_key == SGO:
        link = snap.raw_link
        status = link.status if link is not None else RAW_NOT_RETAINED
        if link is None or not link.linked:
            codes.append("LIMITATION_RAW_PROVIDER_RESPONSE_NOT_ARCHIVED" if status == RAW_NOT_RETAINED
                         else f"LIMITATION_RAW_PROVIDER_RESPONSE_{status}")
            codes.append("LIMITATION_SGO_PER_LEG_PROVIDER_STAMPS_NOT_RETAINED_OLDER_SIDE_APPLIED_TO_BOTH")
    codes.extend(_conventions(snap.provider_key, snap.raw_link)["reasonCodes"])
    return list(dict.fromkeys(codes))


def _raw_diagnostics(snaps: list[Snapshot], primary: Snapshot | None) -> dict[str, Any]:
    """Feasibility counters for retained-response linkage (never an eligibility rule)."""

    sgo = [s for s in snaps if s.provider_key == SGO and s.home is not None and s.away is not None]
    statuses = Counter(s.raw_link.status for s in sgo if s.raw_link is not None)
    return {
        "sgoCompletePairSnapshots": len(sgo),
        "sgoRawLinkStatusCounts": dict(sorted(statuses.items())),
        "sgoSnapshotsRawLinked": statuses.get(RAW_LINKED, 0),
        "sgoLegsPerLegClockRecovered": sum(1 for s in sgo for leg in (s.home, s.away) if leg.per_leg_clock),
        "sgoSnapshotsWithSkewReceipt": sum(1 for s in sgo if s.skew_receipt),
        "sgoSnapshotsWithZeroSkewReceipt": sum(1 for s in sgo if s.skew_receipt
                                               and s.skew_receipt.get("zeroSkewForEveryPair")),
        "primaryRawLinkStatus": primary.raw_link.status if primary is not None and primary.raw_link else None,
        "primaryPerLegClocksRecovered": bool(primary is not None and primary.home is not None
                                             and primary.away is not None and primary.home.per_leg_clock
                                             and primary.away.per_leg_clock),
        "primaryZeroSkewReceipt": bool(primary is not None and primary.skew_receipt
                                       and primary.skew_receipt.get("zeroSkewForEveryPair")),
        "primaryConventionsProved": bool(primary is not None
                                         and _conventions(primary.provider_key, primary.raw_link)["proved"]),
    }


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _book_pair(snapshot: Snapshot, anchor_at: datetime, evidence: EvidenceDocument,
               receipts: dict[tuple[str, datetime], list[Any]], jobs: dict[Any, Any]) -> dict[str, Any]:
    ledger_rows = receipts.get((snapshot.provider_key, snapshot.fetch_clock), [])
    for ledger in ledger_rows:
        evidence.add("provider_request_ledger", ledger.id, {
            "id": ledger.id, "provider": ledger.provider, "endpoint_key": ledger.endpoint_key,
            "requested_at": ledger.requested_at, "completed_at": ledger.completed_at, "status": ledger.status,
            "request_fingerprint": ledger.request_fingerprint, "records_received": ledger.records_received,
            "job_run_id": ledger.job_run_id, "reservation_id": ledger.reservation_id})
        job = jobs.get(ledger.job_run_id)
        if job is not None:
            row = {"id": job.id, "job_name": job.job_name, "status": job.status,
                   "started_at": job.started_at, "completed_at": job.completed_at}
            if snapshot.skew_receipt and snapshot.skew_receipt.get("jobRunId") == str(job.id):
                row["ingest_parse_skew_receipt"] = snapshot.skew_receipt
            evidence.add("job_runs", job.id, row)
    link = snapshot.raw_link
    response = link.response if link is not None and link.linked else None
    identity = _response_identity(snapshot, link, ledger_rows)
    response_ptr = evidence.note(f"response:{snapshot.snapshot_id}", {
        "snapshotId": snapshot.snapshot_id, "provider": snapshot.provider_key,
        "sourceEventId": snapshot.source_event_id, "fetchClockUtc_notAReceipt": iso(snapshot.fetch_clock),
        "ledgerRowIds": [str(l.id) for l in ledger_rows],
        "requestFingerprints": [l.request_fingerprint for l in ledger_rows],
        "legRowIds": [leg.row_id for leg in (snapshot.home, snapshot.away) if leg],
        "retainedResponse": identity,
        "providerStatedLegFields": link.stated if response is not None else None,
    })
    if response is not None:
        # The pair's common response identity is the retained file itself.
        source_ref: Any = {"path": response.path, "sha256": response.sha256, "bytes": response.bytes,
                           "locator": (f"data[eventID={snapshot.source_event_id}].odds[{SGO_HOME_ODD_ID}|"
                                       f"{SGO_AWAY_ODD_ID}].byBookmaker[{link.provider_book_key}]")}
    else:
        source_ref = response_ptr

    def leg_block(leg: Leg | None) -> dict[str, Any] | None:
        if leg is None or leg.observed_at is None:
            return None
        evidence.add("odds_snapshots", leg.row_id, {
            "id": leg.row_id, "side": leg.side, "participant_team_id": leg.team_id, "american_odds": leg.american,
            "decimal_odds_stored": leg.decimal_stored, "observed_at": leg.observed_at,
            "available_as_of_fetch_clock_not_receipt": leg.fetch_clock, "status": leg.status,
            "source": snapshot.provider_key, "source_event_id": leg.source_event_id,
            "payload_checksum": leg.payload_checksum, "sportsbook_key": snapshot.sportsbook_key,
            "created_at_transaction_start": leg.created_at,
            "receipt_ledger_id": leg.receipt_ledger_id, "receipt_job_run_id": leg.job_run_id,
            "provider_last_updated_at_from_retained_response": leg.provider_updated_at,
            "provider_last_updated_at_field": leg.provider_updated_field})
        exact = american_to_decimal_exact(leg.american)
        if exact is None:
            return None
        available = leg.available_by
        if leg.per_leg_clock:
            clock_text = (f"quoteUpdatedAtUtc = this leg's own provider lastUpdatedAt, read from the retained raw "
                          f"response ({leg.provider_updated_field}); odds_snapshots.observed_at holds the pair's "
                          f"older side stamp. ")
        elif snapshot.provider_key == SGO:
            clock_text = ("quoteUpdatedAtUtc = odds_snapshots.observed_at: the pair's OLDER provider side update "
                          "applied to both legs; this leg's own stamp was not recovered (no matching retained raw "
                          "response). ")
        else:
            clock_text = "quoteUpdatedAtUtc = odds_snapshots.observed_at as stored for this provider. "
        return {
            "teamId": leg.team_id or "",
            "american": int(leg.american),
            "decimal": float(exact),
            "quoteUpdatedAtUtc": iso(leg.quote_clock),
            "quoteUpdatedAtIsPerLeg": leg.per_leg_clock,
            "perLegClock": {
                "status": ("RECOVERED_FROM_RETAINED_RESPONSE" if leg.per_leg_clock
                           else "NOT_RECOVERED" if snapshot.provider_key == SGO
                           else "NOT_ASSESSED_NON_SGO_PROVIDER"),
                "providerLastUpdatedAtUtc": iso(leg.provider_updated_at),
                "providerField": leg.provider_updated_field,
                "retainedResponseSha256": response.sha256 if response is not None and leg.per_leg_clock else None,
                "reasonCode": (None if leg.per_leg_clock else
                               f"PER_LEG_CLOCK_NOT_RECOVERED_{link.status}" if link is not None
                               else "PER_LEG_CLOCK_NOT_ASSESSED"),
                "storedObservedAtUtc": iso(leg.observed_at),
                "storedObservedAtMeaning": (
                    "odds_snapshots.observed_at: the OLDER of the pair's two provider lastUpdatedAt stamps, "
                    "written on both legs by the SGO ingest parser" if snapshot.provider_key == SGO
                    else "odds_snapshots.observed_at as stored"),
            },
            "availableByUtc": iso(available) if available else iso(leg.fetch_clock),
            "availableByIsReceipt": available is not None,
            "availabilityReceiptRef": EvidencePointer(
                f"/rows/provider_request_ledger/{leg.receipt_ledger_id}" if leg.receipt_ledger_id
                else f"/rows/odds_snapshots/{leg.row_id}"),
            "clockMeaning": clock_text + (
                "availableByUtc = max(provider_request_ledger.completed_at, requesting job_runs.completed_at), a "
                "completed-receipt upper bound on commit" if available is not None else
                "NO COMPLETED RECEIPT: availableByUtc shows only the fetch clock (available_as_of = "
                "requested_at), which is NOT proof of receipt or commit"),
            "status": leg.status,
            "ageMinutesAtAnchor": _minutes(anchor_at - leg.quote_clock),
            "receiptLedgerCompletedAtUtc": iso(leg.receipt_completed_at),
            "receiptJobCompletedAtUtc": iso(leg.job_completed_at),
            "rowRef": EvidencePointer(f"/rows/odds_snapshots/{leg.row_id}"),
        }

    home, away = leg_block(snapshot.home), leg_block(snapshot.away)
    both_per_leg = bool(snapshot.home and snapshot.away and snapshot.home.per_leg_clock
                        and snapshot.away.per_leg_clock)
    if both_per_leg:
        skew: float | None = abs((snapshot.home.provider_updated_at
                                  - snapshot.away.provider_updated_at).total_seconds())
        skew_meaning = "difference of the two legs' own provider lastUpdatedAt stamps in the retained response"
    elif snapshot.provider_key == SGO:
        # SGO rows carry the pair's older side stamp on BOTH legs, so their
        # stored difference is zero by construction, not a measured skew.
        skew = None
        skew_meaning = ("NOT_MEASURABLE_FROM_RETAINED_BYTES: per-side provider stamps not recovered (older side "
                        "stored on both legs); see ingestParseSkewReceipt for the collector's own record")
    else:
        skew = (abs((snapshot.home.observed_at - snapshot.away.observed_at).total_seconds())
                if snapshot.home and snapshot.away and snapshot.home.observed_at and snapshot.away.observed_at
                else None)
        skew_meaning = "difference of the two stored leg clocks"
    pair = {
        "bookKey": snapshot.book_key,
        "providerKey": snapshot.provider_key,
        "marketId": snapshot.market_id,
        "period": "full_game",
        "conventions": _conventions(snapshot.provider_key, link),
        "sourceResponseRef": source_ref,
        "sourceResponse": identity,
        "home": home,
        "away": away,
        "eligible": snapshot.eligible,
        "reasonCodes": list(dict.fromkeys(snapshot.failures + snapshot.limitations)),
        "snapshotId": snapshot.snapshot_id,
        "sportsbookKey": snapshot.sportsbook_key,
        "sourceEventId": snapshot.source_event_id,
        "olderLegObservedAtUtc": iso(snapshot.older_observed),
        "perLegClocksRecovered": both_per_leg,
        "legSkewSeconds": skew,
        "legSkewMeaning": skew_meaning,
        "ingestParseSkewReceipt": snapshot.skew_receipt,
        "qHome": float(snapshot.q_home) if snapshot.q_home is not None else None,
        "overround": float(snapshot.overround) if snapshot.overround is not None else None,
    }
    return pair


def _response_identity(snapshot: Snapshot, link: SgoRawLink | None, ledger_rows: list[Any]) -> dict[str, Any]:
    """The pair's common response identity: the retained file when there is one, else explicit nulls."""

    response = link.response if link is not None and link.linked else None
    ledger = ledger_rows[0] if len(ledger_rows) == 1 else None
    return {
        "status": link.status if link is not None else ("NOT_ASSESSED_NON_SGO_PROVIDER"
                                                        if snapshot.provider_key != SGO else RAW_NOT_RETAINED),
        "retained": response is not None,
        "path": response.path if response else None,
        "sha256": response.sha256 if response else None,
        "bytes": response.bytes if response else None,
        "archivedAtUtc": iso(response.archived_at) if response else None,
        "archiveClockSource": response.archive_clock_source if response else None,
        "fileModifiedAtUtc": iso(response.file_modified_at) if response else None,
        "rawArchivePayloadChecksum": response.payload_checksum if response else None,
        "fetchClockUtc": iso(snapshot.fetch_clock),
        "fetchClockMeaning": ("provider_request_ledger.requested_at = odds_snapshots.available_as_of: when AISP "
                              "sent the request. Not a receipt and never a quote update clock."),
        "ledgerRowId": str(ledger.id) if ledger is not None else None,
        "requestFingerprint": ledger.request_fingerprint if ledger is not None else None,
        "providerEventId": snapshot.source_event_id,
        "providerBookmakerKey": link.provider_book_key if response else None,
        "candidatesExamined": link.candidates_examined if link is not None else 0,
        "reasonCodes": list(link.reason_codes) if link is not None else [],
        "meaning": ("sha256/bytes are of the retained raw response file both legs were read from; "
                    "odds_snapshots.payload_checksum is a per-row checksum and is not a response identity"),
    }


#: What the protocol needs proved before a pair's conventions match the V18 target.
#: The keys are the ones earlier packets used, so readers keep working; each now
#: holds a status string, never an asserted value.
_CONVENTION_FIELDS = ("extraInnings", "settlementRule", "pitcherActionRule")


def _conventions(provider: str, link: SgoRawLink | None = None) -> dict[str, Any]:
    """Conventions exactly as the provider states them, each unstated one null with a reason.

    Nothing here is asserted by AISP on the provider's behalf. The retained SGO
    responses state the oddIDs, ``periodID``, ``betTypeID``, ``sideID`` and
    ``marketName`` of each side (cited by JSON locator); no field in them
    states whether period ``game`` includes extra innings, how a tie or
    postponement is handled, or any listed-pitcher/action rule. Those carry a
    status string at their usual keys and a null value in ``conventionDetails``.
    """

    base = {"market": "two-way full-game moneyline (AISP market_type h2h, period full_game)"}
    if provider != SGO:
        reasons = [f"LIMITATION_{name.upper()}_NOT_ESTABLISHED_FOR_PROVIDER" for name in _CONVENTION_FIELDS]
        return {**base, "providerStated": None, "providerStatedSource": None,
                **{name: "NOT_ESTABLISHED" for name in _CONVENTION_FIELDS},
                "conventionDetails": {name: {"status": "NOT_ESTABLISHED", "value": None}
                                      for name in _CONVENTION_FIELDS},
                "proved": False, "unprovedFields": list(_CONVENTION_FIELDS), "reasonCodes": reasons}
    retained = link is not None and link.linked and link.response is not None
    stated = None
    if retained:
        stated = {
            side: {
                "fields": {k: v for k, v in link.stated[side].items() if k in _SGO_ODD_IDENTITY_FIELDS},
                "source": _odd_locator(str(link.event_id),
                                       SGO_HOME_ODD_ID if side == "home" else SGO_AWAY_ODD_ID),
            } for side in ("home", "away")
        }
    # Linked: read the response and it does not say. Unlinked: no matching
    # retained response of this fetch was available to read.
    status = "NOT_STATED_BY_PROVIDER" if retained else "UNKNOWN_NO_MATCHING_RETAINED_RESPONSE"
    suffix = "NOT_STATED_BY_PROVIDER" if retained else "NOT_RETAINED"
    missing = "No matching retained raw response of this fetch was available to read"
    details = {
        "extraInnings": {
            "status": status, "value": None,
            "note": ("The response states periodID='game' and marketName='Moneyline' for these oddIDs and lists "
                     "other periods separately (see otherMoneylineMarketsListed); no field states whether 'game' "
                     "includes extra innings." if retained else
                     f"{missing}; only AISP's ingest rule (periodID='game') is known."),
        },
        "settlementRule": {
            "status": status, "value": None,
            "note": ("Two sides (sideID home/away, reciprocal opposingOddID) are stated; no field states how a "
                     "tie, postponement or shortened game is handled." if retained else f"{missing}."),
        },
        "pitcherActionRule": {
            "status": status, "value": None,
            "note": ("No field of the response names a listed-pitcher, action or pitcher-change rule." if retained
                     else f"{missing}; no retained SGO response of any fetch carries such a field."),
        },
    }
    reasons = [f"LIMITATION_EXTRA_INNINGS_INCLUSION_{suffix}", f"LIMITATION_SETTLEMENT_TIE_HANDLING_{suffix}",
               f"LIMITATION_PITCHER_ACTION_CONVENTION_{suffix}",
               "LIMITATION_CONVENTIONS_UNPROVED_NOT_ENFORCED_BY_CONSUMER_PRICE_EVALUABILITY"]
    return {
        **base,
        "providerOddIds": [SGO_HOME_ODD_ID, SGO_AWAY_ODD_ID],
        # Verified from the provider's own periodID field by the ingest parser
        # (it refuses any other period); a period identity, not a settlement rule.
        "providerPeriodId": SGO_PERIOD_ID,
        "extraInnings": status,
        "settlementRule": status,
        "pitcherActionRule": status,
        "conventionDetails": details,
        "providerStated": stated,
        "providerStatedSource": ({"path": link.response.path, "sha256": link.response.sha256,
                                  "bytes": link.response.bytes} if retained else None),
        "otherMoneylineMarketsListed": link.other_moneyline_markets if retained else None,
        "ingestEnforced": {
            "oddIDs": [SGO_HOME_ODD_ID, SGO_AWAY_ODD_ID],
            "opposingOddIDReciprocal": True,
            "periodID": SGO_PERIOD_ID,
            "betTypeID": SGO_BET_TYPE_ID,
            "enforcedBy": ("app.data_sources.sgo_parser.parse_events at ingestion: an AISP admission rule, not a "
                           "provider statement of settlement"),
        },
        "proved": False,
        "unprovedFields": list(_CONVENTION_FIELDS),
        "eligibilityEffect": ("NOT_ENFORCED_BY_THIS_EXPORTER: unproved conventions are recorded here and as "
                              "LIMITATION codes; priceEvaluable is unchanged by them"),
        "reasonCodes": reasons,
    }


__all__ = [
    "BuildInputs", "DecisionCandidate", "FrozenProtocol", "FrozenProtocolError", "GameIdentity",
    "MODE_FEASIBILITY", "MODE_PROSPECTIVE", "OutcomeFieldError", "PROTOCOL_SHA256", "PacketBuild",
    "PositionLink", "Snapshot", "Leg", "V18_EXPERIMENT_ID", "WINDOW_CLOSE_UTC", "WINDOW_OPEN_UTC",
    "american_to_decimal_exact", "assert_no_outcome_fields", "build_packet", "choose_anchor",
    "choose_book_snapshot", "choose_primary_book", "episode_key", "evaluate_snapshot", "file_ref",
    "find_phase_evidence", "first_failure", "in_enrollment_window", "iso", "link_sgo_pair",
    "load_frozen_protocol", "mlb_games_with_locks_between", "no_vig_home", "phase_from_game_type",
    "quarantine_gate", "quote_age_failure", "read_game", "research_lock", "RetainedResponse", "seal_deadline",
    "selection_status", "SgoRawLink", "sgo_retained_responses", "sgo_skew_receipt", "validate_packet",
]
