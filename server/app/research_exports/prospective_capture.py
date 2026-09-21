"""Append-only prospective research capture: the inputs and the outcomes.

STATUS: DESIGN_BUILT_NOT_ENABLED
--------------------------------
Model Studio (Codex) owns a forthcoming frozen contract for a prospective
comparison of three things across every eligible priced game: the UNCHANGED
model, the market, and a fixed 50/50 blend. AISP owns exactly two jobs in that
comparison -- supplying the inputs that were genuinely knowable at the
checkpoint, and supplying authentic outcomes. It does not own the comparison.

The contract is NOT frozen yet. So this module is built, tested, and left
OFF. ``Settings.prospective_capture_enabled`` defaults to ``False`` and every
entry point returns an inert result when it is off: nothing is read, nothing is
written, no provider is called. ``CONTRACT_STATUS`` below says so in the record
itself, and ``RECORD_FIELDS`` is one ordered tuple so the shape can be adapted
to whatever Codex freezes by editing one place.

WHAT IS CAPTURED
----------------
One append-only record per ELIGIBLE PRICED GAME per checkpoint evaluation --
including games that were DECLINED and games that were UNFUNDED, because a
comparison that only sees the games AISP bet is a comparison of AISP's
selection, not of the model. A game the exporter cannot capture is never
silently omitted: it gets an EXCLUSION record naming the reason.

THE SIX HARD RULES, AND WHERE EACH ONE IS ACTUALLY ENFORCED
-----------------------------------------------------------
1. APPEND-ONLY. Records are named by the sha256 of their own canonical bytes
   and written through :func:`app.research_exports.v18_market_pilot_seal.write_once`,
   which creates exclusively and then makes the file read-only. A byte-identical
   re-run adopts the existing file (:func:`_write_once_same`); a file that
   exists with DIFFERENT bytes is a refusal, never an overwrite. A later
   evaluation whose content differs hashes differently and therefore lands
   beside the earlier record, which is not touched. The ledger is the same
   hash-chained append-only JSONL the pilot sealer uses.

2. FREEZE THE PREGAME VIEW. Every pregame read is bounded by the checkpoint
   through :func:`_visible`, which drops any row whose availability clock is
   after it. The forecast and the priced leg are not re-chosen at all: the
   decision NAMES ``shadow_forecast_id`` and ``odds_snapshot_id``, and those
   exact rows are read, then bound-checked against the checkpoint. The opposing
   leg -- the one row the decision does not name -- is the latest same-book
   quote VISIBLE AT THE CHECKPOINT, never the latest quote. There is no code
   path that substitutes a later forecast or a later quote, and
   :func:`build_record` takes the checkpoint as an argument rather than reading
   a clock.

3. NO BETTING EFFECT. All reads happen inside
   :func:`app.research_exports.v18_market_pilot_outcomes.read_only` -- a
   PostgreSQL ``SET TRANSACTION READ ONLY`` that is confirmed, always rolled
   back, and refuses a session that already has pending changes. On top of that
   :func:`_no_write_guard` counts ``shadow_experiment_decisions`` and
   ``official_asp_positions`` rows before and after and refuses on any change,
   which catches a write on SQLite too, where the read-only statement is a
   no-op. The module imports no decision-path writer, recalculates no
   eligibility, and returns nothing that is fed back anywhere.

4. PREDICTIVE SCORING IS SEPARATE FROM MONEY. Forecast-quality inputs live in
   ``probabilities`` and ``outcome``. Money lives ONLY under
   ``ledgerSettlement``. :func:`assert_money_is_quarantined` walks the record
   and refuses a money-shaped key found anywhere else.
   :func:`assert_no_derived_comparison` refuses any blend-, score-, threshold-
   or optimisation-shaped key anywhere at all, because computing one would be
   doing Codex's job with our thumb on it. Nothing in this module computes a
   blend, picks a threshold, or scores a forecast.

5. NO NEW SCHEDULER. This module defines no scheduler id, registers nothing,
   and is not imported by ``app.jobs.scheduler``. It offers a callable,
   :func:`run_prospective_capture`, and a manual entry point,
   ``python -m app.research_exports.prospective_capture``, and nothing else.

6. CANONICAL JSON. :func:`app.research_exports.v18_market_pilot.canonical_bytes`
   -- sorted keys, no whitespace -- and the record carries NO export clock, so
   the same inputs produce the same bytes and the same filename forever. Export
   clocks live in the ledger, which is not the record.

CLOCKS
------
The session timezone is ``America/New_York`` and psycopg hands back ET-aware
datetimes, so every clock goes through :func:`v18_market_pilot.iso`, which
converts to UTC BEFORE formatting. Formatting an ET-aware stamp with a literal
"Z" is the bug that cost six provider objects on 2026-09-20.

``created_at`` is never used as a clock. In PostgreSQL it is the
TRANSACTION-START stamp: on 2026-09-20 eight FINAL forecast rows carried
``created_at`` 12:41:07 while their transaction did not commit until 12:43:32.
The record therefore carries ``forecastAvailableAsOfUtc`` (the forecast's own
availability clock) and ``forecastImportedAtUtc`` (the import COMPLETION the
decision recorded), and names ``created_at`` only inside a definition string
that says why it is not used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import logger
from app.models import (
    ExperimentDecisionPhase,
    Game,
    GameStateObservation,
    GameTeam,
    League,
    Market,
    MarketOutcome,
    MarketType,
    NflFinalObservation,
    OddsSnapshot,
    OfficialAspPosition,
    ShadowExperimentDecision,
    ShadowForecast,
    Sportsbook,
    Team,
)
from app.research_exports import v18_market_pilot as pilot
from app.research_exports import v18_market_pilot_outcomes as outcomes
from app.research_exports.v18_market_pilot_seal import AttemptLedger, write_once

# ---------------------------------------------------------------------------
# Identity. Everything Codex has to agree with lives in this block.
# ---------------------------------------------------------------------------
EXPORT_IDENTITY = "AISP_PROSPECTIVE_RESEARCH_CAPTURE_EXPORT"
RECORD_TYPE = "AISP_PROSPECTIVE_CAPTURE_V1"
EXCLUSION_TYPE = "AISP_PROSPECTIVE_CAPTURE_EXCLUSION_V1"
SCHEMA_VERSION = "1"
#: Stamped into every record. It changes to the contract's own identity the day
#: Model Studio freezes one; until then a reader can tell at a glance that the
#: shape is provisional.
CONTRACT_STATUS = "DESIGN_BUILT_NOT_ENABLED_AWAITING_MODEL_STUDIO_FREEZE"
LEDGER_NAME = "capture-ledger.jsonl"
RECORDS_DIR = "records"
#: The literal the contract asks for when there is no position. It is a value,
#: not a null, so "no position" can never be read as "field missing".
NOT_APPLICABLE = "NOT_APPLICABLE"
#: The accepted basis string for a source-confirmed terminal state whose source
#: supplied no completion time. Fixed by the outcome amendment AISP accepted on
#: 2026-09-19 (``v18_market_pilot_outcomes_v2.NULL_BASIS``); stated verbatim
#: here so the two lanes cannot drift, and pinned equal by test.
COMPLETION_TIME_UNAVAILABLE = "SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_UNAVAILABLE"

#: Leagues whose priced games form the capture universe. Kept as data so the
#: universe is stated, never inferred from whatever happens to be in the table.
DEFAULT_LEAGUES = ("NFL", "MLB")

#: Money may appear ONLY under this key. Rule 4.
LEDGER_BLOCK = "ledgerSettlement"
MONEY_KEYS = frozenset({
    "stake", "payout", "profit_loss", "profitLoss", "bankroll_before", "bankroll_after",
    "bankrollBefore", "bankrollAfter", "settlement", "settlements", "closing_line_value",
    "closingLineValue", "closing_decimal_odds", "closingDecimalOdds", "returns", "roi",
    "profit", "pnl", "netUnits", "net_units", "units",
})
#: Nothing shaped like the comparison Codex owns may appear anywhere. Rule 4.
DERIVED_COMPARISON_KEYS = frozenset({
    "blend", "blended", "blendedProbability", "blended_probability", "blendWeight", "blend_weight",
    "brier", "brierScore", "brier_score", "logLoss", "log_loss", "logarithmicScore",
    "calibration", "calibrationBin", "skillScore", "skill_score", "threshold", "thresholds",
    "chosenThreshold", "optimal", "optimalThreshold", "optimised", "optimized", "ranking",
    "modelBeatsMarket", "model_beats_market", "comparison", "verdict", "score", "grade",
})


class CaptureRefused(RuntimeError):
    """A condition under which no record may be written."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Definition strings. Every number the contract receives arrives with the
# sentence that says how it was derived, so no reader has to guess.
# ---------------------------------------------------------------------------
DEFINITIONS = {
    "rawModelProbability": (
        "The UNCHANGED model's win probability for the SELECTED side, exactly as the controlling "
        "forecast stated it at the checkpoint. Source order: the decision's retained evidence key "
        "'raw_model_probability'; otherwise shadow_forecasts.full_home_win_probability / "
        "full_away_win_probability oriented to the selected side. No policy adjustment, no "
        "uncertainty widening, no normalisation is applied here."),
    "policyAdjustedProbability": (
        "The model probability AFTER AISP's own policy adjustment (uncertainty widening, evidence "
        "penalties), for the SELECTED side, as the decision retained it: evidence key "
        "'adjusted_probability'. Null when the deciding channel retains no adjusted probability -- "
        "it is NOT back-filled from the raw probability, because that would assert an adjustment "
        "that was never recorded."),
    "marketNoVigProbability": (
        "The two-way no-vig probability of the SELECTED side, derived by AISP from BOTH legs of the "
        "same-book pair actually captured: rSel = 1/dSel, rOpp = 1/dOpp, qSel = rSel / (rSel + rOpp), "
        "overround = rSel + rOpp - 1. Exact rational arithmetic on the American prices; see "
        "market.deVig for every term. This is a two-way price normalised conditional on no tie, so "
        "it is not comparable to a three-way probability."),
    "retainedMarketProbability": (
        "The market probability the DECISION itself retained (evidence key 'market_probability'), "
        "carried as retained and never substituted for the derived value above. Null when the "
        "deciding channel retained none."),
    "checkpointAtUtc": (
        "The checkpoint time ACTUALLY USED for this record. When a probability-bearing FINAL_PREGAME "
        "decision exists it is that decision's recommended_at, because that is the instant AISP "
        "actually evaluated the game. Otherwise it is the evaluation moment the caller supplied. "
        "Never a job clock and never created_at."),
    "forecastAvailableAsOfUtc": (
        "shadow_forecasts.available_as_of_utc: the forecast's own availability clock, the instant "
        "the producer states it could be used."),
    "forecastImportedAtUtc": (
        "shadow_experiment_decisions.forecast_imported_at: the IMPORT COMPLETION the decision "
        "recorded. Deliberately not the forecast row's created_at, which in PostgreSQL is the "
        "TRANSACTION-START stamp -- on 2026-09-20 eight FINAL rows carried created_at 12:41:07 "
        "while their transaction did not commit until 12:43:32."),
    "forecastPublishedAtUtc": (
        "shadow_experiment_decisions.forecast_published_at: when the producer published the "
        "controlling snapshot, as the decision recorded it."),
    "createdAtNotUsed": (
        "PostgreSQL created_at is the transaction-START stamp, not an insertion, commit or "
        "publication time. No clock in this record is ever taken from it."),
    "legObservedAtUtc": (
        "odds_snapshots.observed_at: the BOOKMAKER's own last_update for that leg. The price was "
        "the book's as of this instant."),
    "legCaptureClockUtc": (
        "odds_snapshots.available_as_of: when AISP could first use the row (the collector's fetch "
        "clock). An availability bound, never the book's clock."),
    "outcomeCompletionTimeUtc": (
        "The SOURCE's own completion field and nothing else. Null with basis "
        f"'{COMPLETION_TIME_UNAVAILABLE}' when the source confirmed a terminal state but supplied no "
        "completion time -- which is always the case for NFL, whose source of record "
        "(nfl_final_observations) has no completion column. Never a retrieval clock, never a "
        "scheduled start, never a feed's last-updated string, never created_at."),
    "sourceLastUpdatedRaw": (
        "nfl_final_observations.source_last_updated: an UNPARSED feed string, carried verbatim as "
        "the source wrote it. It is evidence, not a clock, and is never parsed into one."),
    "qualificationStatus": (
        "The PRE-staking answer: did the game qualify on value and gates? "
        "shadow_experiment_decisions.qualification_outcome."),
    "fundingStatus": (
        "Whether staking actually funded the qualified game. "
        "shadow_experiment_decisions.execution_outcome. A value-qualified game that staking "
        "declined reads NOT_EXECUTED here while qualificationStatus still reads QUALIFIED."),
    "positionStatus": (
        "Whether a row exists in the official_asp_positions ledger, and its status if so. "
        "Distinct from both fields above: it is the ledger's answer, not the policy's."),
}

CLOCK_MEANINGS = {key: DEFINITIONS[key] for key in (
    "checkpointAtUtc", "forecastAvailableAsOfUtc", "forecastImportedAtUtc",
    "forecastPublishedAtUtc", "createdAtNotUsed", "legObservedAtUtc", "legCaptureClockUtc",
    "outcomeCompletionTimeUtc", "sourceLastUpdatedRaw",
)}

#: The ordered key set of a capture record. One place to adapt when Codex
#: freezes the contract; :func:`build_record` asserts against it.
RECORD_FIELDS = (
    "recordType", "schemaVersion", "exportIdentity", "contractStatus",
    "identity", "context", "checkpoint", "orientation", "probabilities", "market",
    "status", "ledgerSettlement", "outcome", "clockMeanings",
)
EXCLUSION_FIELDS = (
    "recordType", "schemaVersion", "exportIdentity", "contractStatus",
    "identity", "context", "checkpoint", "exclusion", "clockMeanings",
)


# ---------------------------------------------------------------------------
# Rule 4: money is quarantined, and the comparison is not ours to compute.
# ---------------------------------------------------------------------------
def _walk(value: Any, path: str) -> Iterable[tuple[str, str, Any]]:
    stack: list[tuple[Any, str]] = [(value, path)]
    while stack:
        item, where = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                yield key, f"{where}/{key}", child
                stack.append((child, f"{where}/{key}"))
        elif isinstance(item, list):
            for index, child in enumerate(item):
                stack.append((child, f"{where}[{index}]"))


def assert_money_is_quarantined(record: Any, where: str = "record") -> None:
    """Rule 4: a money-shaped key may appear only under ``ledgerSettlement``."""

    allowed = f"{where}/{LEDGER_BLOCK}"
    for key, path, _ in _walk(record, where):
        if key in MONEY_KEYS and not path.startswith(f"{allowed}/"):
            raise CaptureRefused("MONEY_FIELD_OUTSIDE_LEDGER_BLOCK", path)


def assert_no_derived_comparison(record: Any, where: str = "record") -> None:
    """Rule 4: Codex owns the comparison; we never ship one, anywhere."""

    for key, path, _ in _walk(record, where):
        if key in DERIVED_COMPARISON_KEYS:
            raise CaptureRefused("DERIVED_COMPARISON_FIELD_PRESENT", path)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Rule 3: the exporter may not touch the decision path.
# ---------------------------------------------------------------------------
def _decision_and_position_counts(session: Session) -> tuple[int, int]:
    return (
        int(session.scalar(select(func.count(ShadowExperimentDecision.id))) or 0),
        int(session.scalar(select(func.count(OfficialAspPosition.id))) or 0),
    )


class _no_write_guard:
    """Refuses if the decision or position tables changed while we read them.

    ``read_only`` already forbids it on PostgreSQL, but SQLite ignores
    ``SET TRANSACTION READ ONLY``, so the tests would not be testing anything.
    This counts rows either side and is dialect-independent.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def __enter__(self) -> "_no_write_guard":
        self.before = _decision_and_position_counts(self.session)
        return self

    def __exit__(self, *exc: Any) -> None:
        if exc[0] is not None:
            return
        after = _decision_and_position_counts(self.session)
        if after != self.before:
            raise CaptureRefused(
                "EXPORTER_CHANGED_DECISION_OR_POSITION_ROWS", f"{self.before} -> {after}")
        if self.session.new or self.session.dirty or self.session.deleted:
            raise CaptureRefused("EXPORTER_LEFT_PENDING_SESSION_CHANGES", "")


# ---------------------------------------------------------------------------
# Rule 2: the pregame view is bounded by the checkpoint.
# ---------------------------------------------------------------------------
def _visible(moment: datetime | None, checkpoint: datetime) -> bool:
    """Was a row carrying this availability clock knowable at the checkpoint?"""

    at = pilot.aware(moment)
    return at is not None and at <= checkpoint


# ---------------------------------------------------------------------------
# Reading one game
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GameRow:
    game_id: str
    league_code: str
    sport_code: str
    starts_at: datetime
    home_team_id: str | None
    away_team_id: str | None
    home_abbreviation: str | None
    away_abbreviation: str | None


@dataclass
class Leg:
    side: str
    snapshot_id: str
    market_outcome_id: str
    american_odds: int | None
    decimal_odds: Fraction | None
    observed_at: datetime | None
    capture_clock: datetime | None
    sportsbook_key: str | None
    sportsbook_name: str | None
    source: str | None
    source_mode: str | None


@dataclass
class Candidate:
    """Everything one game contributed, or the reason it contributed nothing."""

    game: GameRow
    checkpoint: datetime
    checkpoint_basis: str
    decision: ShadowExperimentDecision | None = None
    position: OfficialAspPosition | None = None
    forecast: ShadowForecast | None = None
    selected: Leg | None = None
    opposing: Leg | None = None
    selected_side: str | None = None
    selected_side_basis: str | None = None
    exclusion: tuple[str, str] | None = None
    notes: list[str] = field(default_factory=list)


def universe(session: Session, *, window_start: datetime, window_end: datetime,
             leagues: Iterable[str]) -> list[GameRow]:
    """Every game of the named leagues starting inside the window.

    The universe is the SLATE, not the games AISP bet and not the games it
    priced: a game that turns out to be unpriced still has to be accounted for,
    with a reason, or the coverage denominator is quietly AISP's own selection.
    """

    codes = sorted({str(code).upper() for code in leagues})
    rows = session.execute(
        select(Game.id, Game.starts_at, League.code)
        .join(League, League.id == Game.league_id)
        .where(League.code.in_(codes), Game.starts_at >= window_start, Game.starts_at < window_end)
        .order_by(Game.starts_at, Game.id)
    ).all()
    out: list[GameRow] = []
    for game_id, starts_at, league_code in rows:
        sides = session.execute(
            select(GameTeam.is_home, Team.id, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game_id)
        ).all()
        home = next((row for row in sides if row[0]), None)
        away = next((row for row in sides if not row[0]), None)
        out.append(GameRow(
            game_id=str(game_id), league_code=str(league_code),
            sport_code=_sport_for(str(league_code)),
            starts_at=pilot.aware(starts_at),
            home_team_id=str(home[1]) if home else None,
            away_team_id=str(away[1]) if away else None,
            home_abbreviation=str(home[2]) if home else None,
            away_abbreviation=str(away[2]) if away else None))
    return out


def _sport_for(league_code: str) -> str:
    return {"NFL": "FOOTBALL", "CFB": "FOOTBALL", "MLB": "BASEBALL", "NBA": "BASKETBALL"}.get(
        league_code.upper(), "UNKNOWN")


#: The evidence keys that mark the MLB V2 probability-bearing channel.
EVIDENCE_PROBABILITY_KEYS = ("raw_model_probability", "adjusted_probability", "market_probability")


def deciding_row(session: Session, game_id: str, checkpoint: datetime
                 ) -> tuple[ShadowExperimentDecision | None, str | None]:
    """The PROBABILITY-BEARING FINAL_PREGAME decision, chosen by CHANNEL.

    Two channels write FINAL_PREGAME rows for the same MLB game: a V1-strict
    owner that writes NO_BET with NO probabilities in evidence, and a V2 owner
    that writes rows WITH them. "The latest row" therefore picks the wrong one
    roughly half the time, and picking by "has a probability somewhere" is not
    enough either -- a V1 row that DID find a bet also carries a
    ``model_probability`` column.

    So the channel is identified by ``experiment_id``, which is what a channel
    actually is, and channels are preferred in a stated order:

    1. a channel whose EVIDENCE carries the V2 probability keys, then
    2. a channel whose ``model_probability`` column is populated (the NFL
       research channel retains no evidence probabilities at all).

    Within one channel the latest ``recommended_at`` is the current row -- NFL
    legitimately leaves a superseded prerequisite row in place. Two DIFFERENT
    channels bearing probabilities at the same tier is genuine ambiguity and is
    refused, never resolved by recency.
    """

    rows = session.scalars(
        select(ShadowExperimentDecision)
        .where(ShadowExperimentDecision.game_id == uuid.UUID(game_id),
               ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME)
        .order_by(ShadowExperimentDecision.recommended_at, ShadowExperimentDecision.id)
    ).all()
    visible = [row for row in rows if _visible(row.recommended_at, checkpoint)]
    if not visible:
        return None, ("NO_FINAL_PREGAME_DECISION_AT_CHECKPOINT" if not rows
                      else "ALL_FINAL_PREGAME_DECISIONS_AFTER_CHECKPOINT")
    for tier in (_evidence_bearing, _column_bearing):
        bearing = [row for row in visible if tier(row)]
        if not bearing:
            continue
        channels = {row.experiment_id for row in bearing}
        if len(channels) > 1:
            return None, "AMBIGUOUS_PROBABILITY_BEARING_DECISION_CHANNELS"
        return max(bearing, key=lambda row: (pilot.aware(row.recommended_at), str(row.id))), None
    return None, "NO_PROBABILITY_BEARING_DECISION_CHANNEL"


def _evidence_bearing(row: ShadowExperimentDecision) -> bool:
    evidence = row.evidence if isinstance(row.evidence, dict) else {}
    return any(evidence.get(key) is not None for key in EVIDENCE_PROBABILITY_KEYS)


def _column_bearing(row: ShadowExperimentDecision) -> bool:
    return row.model_probability is not None


def position_for(session: Session, decision: ShadowExperimentDecision) -> OfficialAspPosition | None:
    return session.scalars(
        select(OfficialAspPosition)
        .where(OfficialAspPosition.shadow_decision_id == decision.id)
    ).first()


def _sides_for_market(session: Session, market_id: Any, game: GameRow
                      ) -> dict[str, MarketOutcome]:
    """Map HOME/AWAY to the market's two outcomes, by team id then by label."""

    rows = session.scalars(select(MarketOutcome).where(MarketOutcome.market_id == market_id)).all()
    mapped: dict[str, MarketOutcome] = {}
    for row in rows:
        side = None
        participant = str(row.participant_team_id) if row.participant_team_id else None
        if participant and participant == game.home_team_id:
            side = "HOME"
        elif participant and participant == game.away_team_id:
            side = "AWAY"
        else:
            text = f"{row.outcome_key or ''} {row.label or ''}".lower()
            if "home" in text:
                side = "HOME"
            elif "away" in text:
                side = "AWAY"
        if side and side not in mapped:
            mapped[side] = row
    return mapped


def _leg(session: Session, snapshot: OddsSnapshot, side: str) -> Leg:
    book = session.get(Sportsbook, snapshot.sportsbook_id) if snapshot.sportsbook_id else None
    return Leg(
        side=side, snapshot_id=str(snapshot.id),
        market_outcome_id=str(snapshot.market_outcome_id),
        american_odds=snapshot.american_odds,
        decimal_odds=pilot.american_to_decimal_exact(snapshot.american_odds),
        observed_at=pilot.aware(snapshot.observed_at),
        capture_clock=pilot.aware(snapshot.available_as_of),
        sportsbook_key=str(book.key) if book else None,
        sportsbook_name=str(book.name) if book else None,
        source=snapshot.source, source_mode=snapshot.source_mode)


def same_book_pair(session: Session, candidate: Candidate) -> tuple[str, str] | None:
    """Both legs of the SAME-BOOK pair the decision actually priced.

    The selected leg is the exact ``odds_snapshot_id`` the decision named -- not
    re-chosen, not refreshed. The opposing leg is the only row here that has to
    be found, and it is the latest same-book quote VISIBLE AT THE CHECKPOINT.
    Returns ``(code, detail)`` on failure; the pair is set on the candidate on
    success.
    """

    decision, game, checkpoint = candidate.decision, candidate.game, candidate.checkpoint
    if decision is None or decision.odds_snapshot_id is None:
        return ("DECISION_NAMES_NO_ODDS_SNAPSHOT", "")
    snapshot = session.get(OddsSnapshot, decision.odds_snapshot_id)
    if snapshot is None:
        return ("NAMED_ODDS_SNAPSHOT_MISSING", str(decision.odds_snapshot_id))
    if not _visible(snapshot.available_as_of, checkpoint):
        return ("NAMED_ODDS_SNAPSHOT_NOT_VISIBLE_AT_CHECKPOINT", str(snapshot.id))
    outcome = session.get(MarketOutcome, snapshot.market_outcome_id)
    if outcome is None:
        return ("NAMED_MARKET_OUTCOME_MISSING", str(snapshot.market_outcome_id))
    market = session.get(Market, outcome.market_id)
    if market is None or market.market_type != MarketType.MONEYLINE:
        return ("NAMED_MARKET_IS_NOT_A_MONEYLINE", str(outcome.market_id))
    if str(market.game_id) != game.game_id:
        return ("NAMED_MARKET_BELONGS_TO_ANOTHER_GAME", str(market.game_id))

    sides = _sides_for_market(session, market.id, game)
    selected_side, basis = _selected_side(decision, outcome, sides, game)
    if selected_side is None:
        return ("SELECTED_SIDE_NOT_RESOLVABLE", str(decision.selection or ""))
    other_side = "AWAY" if selected_side == "HOME" else "HOME"
    other_outcome = sides.get(other_side)
    if other_outcome is None:
        return ("OPPOSING_MARKET_OUTCOME_MISSING", other_side)

    # Same BOOK and same PROVIDER SOURCE: a pair is keyed by
    # (market, sportsbook, source, side), so a leg from a different provider is
    # not the other half of this quote even when the book name matches.
    opposing = session.scalars(
        select(OddsSnapshot)
        .where(OddsSnapshot.market_outcome_id == other_outcome.id,
               OddsSnapshot.sportsbook_id == snapshot.sportsbook_id,
               OddsSnapshot.source == snapshot.source,
               OddsSnapshot.available_as_of <= checkpoint)
        .order_by(OddsSnapshot.available_as_of.desc(), OddsSnapshot.observed_at.desc(),
                  OddsSnapshot.id.desc())
    ).first()
    if opposing is None:
        return ("NO_SAME_BOOK_OPPOSING_LEG_VISIBLE_AT_CHECKPOINT", other_side)

    candidate.selected = _leg(session, snapshot, selected_side)
    candidate.opposing = _leg(session, opposing, other_side)
    candidate.selected_side = selected_side
    candidate.selected_side_basis = basis
    if candidate.selected.decimal_odds is None or candidate.opposing.decimal_odds is None:
        return ("LEG_PRICE_IS_NOT_VALID_AMERICAN_ODDS",
                f"selected={candidate.selected.american_odds} opposing={candidate.opposing.american_odds}")
    # The NFL channel retains the opposing leg's PRICE (evidence.selection
    # .opposite_american) but not its row ids, so the leg has to be re-derived.
    # Where the price was retained, the re-derivation is checked against it --
    # a mismatch means the row found is not the leg the decision priced.
    retained = _retained_opposite_american(decision)
    if retained is not None and retained != candidate.opposing.american_odds:
        return ("OPPOSING_LEG_DISAGREES_WITH_RETAINED_OPPOSITE_PRICE",
                f"retained={retained} rederived={candidate.opposing.american_odds}")
    return None


def _retained_opposite_american(decision: ShadowExperimentDecision) -> int | None:
    evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
    selection = evidence.get("selection")
    value = selection.get("opposite_american") if isinstance(selection, dict) else None
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _selected_side(decision: ShadowExperimentDecision, outcome: MarketOutcome,
                   sides: dict[str, MarketOutcome], game: GameRow) -> tuple[str | None, str | None]:
    for side, row in sides.items():
        if row.id == outcome.id:
            return side, "MARKET_OUTCOME_ORIENTATION"
    # NFL writes "HOME"/"AWAY"/"NONE"; MLB writes "{ABBR} ML" or the em dash.
    stated = str(decision.selection or "").strip().upper()
    if stated in ("HOME", "AWAY"):
        return stated, "DECISION_SELECTION_LITERAL"
    token = stated.split()[0] if stated.split() else ""
    if token and token == str(game.home_abbreviation or "").upper():
        return "HOME", "DECISION_SELECTION_TEAM_ABBREVIATION"
    if token and token == str(game.away_abbreviation or "").upper():
        return "AWAY", "DECISION_SELECTION_TEAM_ABBREVIATION"
    return None, None


# ---------------------------------------------------------------------------
# Probabilities. Raw, policy-adjusted and market, each with its definition.
# ---------------------------------------------------------------------------
def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def raw_model_probability(candidate: Candidate) -> tuple[float | None, str]:
    decision = candidate.decision
    evidence = decision.evidence if decision is not None and isinstance(decision.evidence, dict) else {}
    retained = _float(evidence.get("raw_model_probability"))
    if retained is not None:
        return retained, "DECISION_EVIDENCE_RAW_MODEL_PROBABILITY"
    forecast, side = candidate.forecast, candidate.selected_side
    if forecast is not None and side in ("HOME", "AWAY"):
        value = _float(forecast.full_home_win_probability if side == "HOME"
                       else forecast.full_away_win_probability)
        if value is not None:
            return value, "CONTROLLING_SHADOW_FORECAST_ORIENTED_TO_SELECTED_SIDE"
    return None, "NOT_RETAINED"


def policy_adjusted_probability(candidate: Candidate) -> tuple[float | None, str]:
    decision = candidate.decision
    evidence = decision.evidence if decision is not None and isinstance(decision.evidence, dict) else {}
    value = _float(evidence.get("adjusted_probability"))
    if value is not None:
        return value, "DECISION_EVIDENCE_ADJUSTED_PROBABILITY"
    # Deliberately NOT falling back to the raw probability: asserting "no
    # adjustment" is a claim the retained row does not make.
    return None, "NOT_RETAINED_BY_THIS_DECISION_CHANNEL"


def de_vig(selected: Leg, opposing: Leg) -> dict[str, Any]:
    """Two-way no-vig, with every term of the arithmetic shown."""

    d_sel, d_opp = selected.decimal_odds, opposing.decimal_odds
    q_sel, overround = pilot.no_vig_home(d_sel, d_opp)
    return {
        "formula": ("rSel = 1/dSel; rOpp = 1/dOpp; qSel = rSel/(rSel+rOpp); "
                    "qOpp = rOpp/(rSel+rOpp); overround = rSel + rOpp - 1"),
        "basis": "TWO_WAY_SAME_BOOK_MONEYLINE_NORMALISED_CONDITIONAL_ON_NO_TIE",
        "selectedDecimalOdds": float(d_sel),
        "opposingDecimalOdds": float(d_opp),
        "selectedImpliedProbability": float(1 / d_sel),
        "opposingImpliedProbability": float(1 / d_opp),
        "overround": float(overround),
        "selectedNoVigProbability": float(q_sel),
        "opposingNoVigProbability": float(1 - q_sel),
    }


# ---------------------------------------------------------------------------
# Outcomes. Source-confirmed terminal status, kept apart from completion time.
# ---------------------------------------------------------------------------
_NFL_TERMINAL = {"FINAL", "FINAL_OVERTIME", "COMPLETED", "STATUS_FINAL"}
_TERMINAL_STATE = {"FINAL", "COMPLETED", "GAME_OVER_FINAL"}


def outcome_for(session: Session, game: GameRow) -> dict[str, Any]:
    """The supported outcome, or an explicit unresolved one.

    NFL reads ``nfl_final_observations``, which IS the NFL source of record.
    ``game_final_outcomes`` is MLB-only (2609 rows, 0 NFL) and is never touched
    here. Everything else reads the sport-neutral ``game_state_observations``.

    Source-confirmed FINAL STATUS and genuine COMPLETION TIME are separate
    fields, and a null completion time keeps its basis rather than borrowing a
    retrieval clock.
    """

    if game.league_code.upper() == "NFL":
        return _nfl_outcome(session, game)
    return _state_outcome(session, game)


def _unresolved(source: str, code: str) -> dict[str, Any]:
    return {
        "resolved": False, "source": source, "sourceMode": None,
        "sourceConfirmedTerminalStatus": None, "terminalStatusBasis": code,
        "homeScore": None, "awayScore": None,
        "outcomeRevisionIdentity": None, "outcomeRevisionIdentityBasis": None,
        "completionTimeUtc": None, "completionTimeBasis": code,
        "sourceLastUpdatedRaw": None, "retrievedAtUtc": None, "observationId": None,
    }


def _nfl_outcome(session: Session, game: GameRow) -> dict[str, Any]:
    rows = session.scalars(
        select(NflFinalObservation)
        .where(NflFinalObservation.game_id == uuid.UUID(game.game_id))
        .order_by(NflFinalObservation.retrieved_at.desc(), NflFinalObservation.id.desc())
    ).all()
    terminal = [row for row in rows if str(row.status or "").upper() in _NFL_TERMINAL]
    if not terminal:
        return _unresolved("nfl_final_observations", "NO_SOURCE_CONFIRMED_TERMINAL_OBSERVATION")
    row = terminal[0]
    return {
        "resolved": True, "source": "nfl_final_observations", "sourceMode": row.source_mode,
        "sourceConfirmedTerminalStatus": str(row.status),
        "terminalStatusBasis": f"nfl_final_observations.status via {row.source}, result_tier={row.result_tier}",
        "homeScore": row.home_score, "awayScore": row.away_score,
        "outcomeRevisionIdentity": row.payload_checksum,
        "outcomeRevisionIdentityBasis": "nfl_final_observations.payload_checksum (result facts only)",
        # The NFL source of record has NO completion-time column. Nothing here
        # is promoted into one -- not retrieved_at, not source_last_updated.
        "completionTimeUtc": None, "completionTimeBasis": COMPLETION_TIME_UNAVAILABLE,
        "sourceLastUpdatedRaw": row.source_last_updated,
        "retrievedAtUtc": pilot.iso(row.retrieved_at), "observationId": str(row.id),
    }


def _state_outcome(session: Session, game: GameRow) -> dict[str, Any]:
    rows = session.scalars(
        select(GameStateObservation)
        .where(GameStateObservation.game_id == uuid.UUID(game.game_id))
        .order_by(GameStateObservation.available_as_of.desc(), GameStateObservation.id.desc())
    ).all()
    terminal = [row for row in rows
                if str(row.status or "").upper().replace(" ", "_") in _TERMINAL_STATE]
    if not terminal:
        return _unresolved("game_state_observations", "NO_SOURCE_CONFIRMED_TERMINAL_OBSERVATION")
    row = terminal[0]
    final_at = pilot.iso(row.final_at)
    return {
        "resolved": True, "source": "game_state_observations", "sourceMode": row.source_mode,
        "sourceConfirmedTerminalStatus": str(row.status),
        "terminalStatusBasis": f"game_state_observations.status via {row.source}",
        "homeScore": row.home_score, "awayScore": row.away_score,
        "outcomeRevisionIdentity": row.payload_checksum,
        "outcomeRevisionIdentityBasis": "game_state_observations.payload_checksum",
        "completionTimeUtc": final_at,
        "completionTimeBasis": ("SOURCE_SUPPLIED_COMPLETION_TIME" if final_at
                                else COMPLETION_TIME_UNAVAILABLE),
        "sourceLastUpdatedRaw": None,
        "retrievedAtUtc": pilot.iso(row.available_as_of), "observationId": str(row.id),
    }


# ---------------------------------------------------------------------------
# Building one record
# ---------------------------------------------------------------------------
def _identity(candidate: Candidate) -> dict[str, Any]:
    decision, position, forecast = candidate.decision, candidate.position, candidate.forecast
    row_id, row_table = None, None
    if decision is not None and decision.shadow_forecast_id is not None:
        row_id, row_table = str(decision.shadow_forecast_id), "shadow_forecasts"
    elif decision is not None and decision.forecast_id is not None:
        row_id, row_table = str(decision.forecast_id), "forecasts"
    stated = decision.controlling_forecast_identity if decision is not None else None
    derived = forecast.forecast_id if forecast is not None else None
    identity = stated or derived
    if stated and derived and stated != derived:
        candidate.notes.append("CONTROLLING_IDENTITY_DISAGREES_WITH_FORECAST_ROW")
    return {
        "gameId": candidate.game.game_id,
        "decisionId": str(decision.id) if decision is not None else NOT_APPLICABLE,
        "decisionChannelExperimentId": (str(decision.experiment_id) if decision is not None
                                        else NOT_APPLICABLE),
        "decisionChannelBasis": (
            "the FINAL_PREGAME channel selected explicitly as the probability-bearing one, by "
            "experiment_id; never 'the latest row'"),
        "positionId": str(position.id) if position is not None else NOT_APPLICABLE,
        "forecastRowId": row_id, "forecastRowTable": row_table,
        "forecastIdentity": identity,
        "forecastIdentityBasis": (
            "shadow_experiment_decisions.controlling_forecast_identity" if stated
            else ("shadow_forecasts.forecast_id" if derived else "NOT_RETAINED")),
        "forecastIdentityAgreesWithRow": (None if not (stated and derived) else stated == derived),
    }


def _context(candidate: Candidate) -> dict[str, Any]:
    decision, position, forecast = candidate.decision, candidate.position, candidate.forecast
    evidence = decision.evidence if decision is not None and isinstance(decision.evidence, dict) else {}
    policy = (position.recommendation_policy_version if position is not None else None) \
        or evidence.get("policy_version")
    staking = (position.staking_policy_version if position is not None else None) \
        or evidence.get("staking_version")
    return {
        "sport": (decision.sport if decision is not None and decision.sport else candidate.game.sport_code),
        "league": candidate.game.league_code,
        "engineId": forecast.engine_id if forecast is not None else None,
        "engineVersion": forecast.engine_version if forecast is not None else None,
        "modelArtifactSha256": forecast.model_artifact_sha256 if forecast is not None else None,
        "snapshotType": forecast.snapshot_type if forecast is not None else None,
        "snapshotVersion": forecast.snapshot_version if forecast is not None else None,
        "policyVersion": policy,
        "policyVersionBasis": ("official_asp_positions.recommendation_policy_version"
                               if position is not None and position.recommendation_policy_version
                               else ("decision.evidence.policy_version" if policy else "NOT_RETAINED")),
        "stakingVersion": staking,
        "stakingVersionBasis": ("official_asp_positions.staking_policy_version"
                                if position is not None and position.staking_policy_version
                                else ("decision.evidence.staking_version" if staking else "NOT_RETAINED")),
        "scheduledStartUtc": pilot.iso(candidate.game.starts_at),
    }


def _checkpoint(candidate: Candidate) -> dict[str, Any]:
    decision, forecast = candidate.decision, candidate.forecast
    return {
        "checkpointAtUtc": pilot.iso(candidate.checkpoint),
        "checkpointBasis": candidate.checkpoint_basis,
        "forecastSnapshotPhase": decision.forecast_snapshot_phase if decision is not None else None,
        "forecastAvailableAsOfUtc": pilot.iso(forecast.available_as_of_utc) if forecast is not None else None,
        "forecastCutoffUtc": pilot.iso(forecast.forecast_cutoff_utc) if forecast is not None else None,
        "forecastPublishedAtUtc": pilot.iso(decision.forecast_published_at) if decision is not None else None,
        "forecastImportedAtUtc": pilot.iso(decision.forecast_imported_at) if decision is not None else None,
        "createdAtNotUsed": DEFINITIONS["createdAtNotUsed"],
    }


def _orientation(candidate: Candidate) -> dict[str, Any]:
    game = candidate.game
    side = candidate.selected_side
    return {
        "orientationBasis": "game_teams.is_home",
        "homeTeamId": game.home_team_id, "homeAbbreviation": game.home_abbreviation,
        "awayTeamId": game.away_team_id, "awayAbbreviation": game.away_abbreviation,
        "selectedSide": side,
        "selectedSideBasis": candidate.selected_side_basis,
        "selectedTeamId": (game.home_team_id if side == "HOME"
                           else game.away_team_id if side == "AWAY" else None),
        "probabilityOrientation": (
            "Every probability in this record is stated FOR THE SELECTED SIDE named above, not for "
            "home. selectedSide is the side AISP's decision took; it is not an assertion about "
            "which side the market or the blend favours."),
    }


def _leg_block(leg: Leg) -> dict[str, Any]:
    return {
        "side": leg.side, "oddsSnapshotId": leg.snapshot_id,
        "marketOutcomeId": leg.market_outcome_id,
        "americanOdds": leg.american_odds,
        "decimalOdds": float(leg.decimal_odds) if leg.decimal_odds is not None else None,
        "observedAtUtc": pilot.iso(leg.observed_at),
        "observedAtBasis": DEFINITIONS["legObservedAtUtc"],
        "captureClockUtc": pilot.iso(leg.capture_clock),
        "captureClockBasis": DEFINITIONS["legCaptureClockUtc"],
        "source": leg.source, "sourceMode": leg.source_mode,
    }


def _market(candidate: Candidate) -> dict[str, Any]:
    selected, opposing = candidate.selected, candidate.opposing
    return {
        "marketType": "MONEYLINE", "period": "FULL_GAME",
        "sportsbookKey": selected.sportsbook_key, "sportsbookName": selected.sportsbook_name,
        "sameBook": selected.sportsbook_key == opposing.sportsbook_key,
        "sameBookBasis": "both legs carry the same odds_snapshots.sportsbook_id",
        "opposingLegBasis": ("latest same-book quote for the opposing outcome whose "
                             "available_as_of is at or before the checkpoint; never the latest "
                             "quote overall"),
        "selectedLeg": _leg_block(selected), "opposingLeg": _leg_block(opposing),
        "deVig": de_vig(selected, opposing),
    }


def _status(candidate: Candidate) -> dict[str, Any]:
    """Three DISTINCT fields. A value-qualified game with no position must not
    read as executed -- that conflation is exactly what put three capped games
    in the record as QUALIFIED_BET on 2026-09-20."""

    decision, position = candidate.decision, candidate.position
    return {
        "qualificationStatus": decision.qualification_outcome,
        "qualificationReason": decision.qualification_reason,
        "qualificationDefinition": DEFINITIONS["qualificationStatus"],
        "fundingStatus": decision.execution_outcome,
        "fundingReason": decision.execution_reason,
        "fundingDefinition": DEFINITIONS["fundingStatus"],
        "positionStatus": (str(position.status) if position is not None else "NO_POSITION"),
        "positionResult": (str(position.result) if position is not None else None),
        "positionDefinition": DEFINITIONS["positionStatus"],
        "postStakingRecommendation": decision.recommendation,
        "postStakingRecommendationReason": decision.recommendation_reason,
        "fieldsAreDistinct": (
            "qualificationStatus, fundingStatus and positionStatus answer three different "
            "questions and are never collapsed: a QUALIFIED game may be NOT_EXECUTED and carry "
            "NO_POSITION, and that combination is a real, capturable row."),
    }


def _ledger_settlement(candidate: Candidate) -> dict[str, Any]:
    """The ONLY place money may appear. Rule 4 keeps it out of everything else."""

    position = candidate.position
    if position is None:
        return {"present": False, "note": (
            "No ledger position. Forecast quality for this game is still fully scoreable from "
            "probabilities + outcome; the absence of money is not an absence of evidence.")}
    return {
        "present": True, "positionId": str(position.id),
        "placedAtUtc": pilot.iso(position.placed_at),
        "settledAtUtc": pilot.iso(position.settled_at),
        "stake": float(position.stake) if position.stake is not None else None,
        "decimalOdds": position.decimal_odds,
        "payout": float(position.payout) if position.payout is not None else None,
        "closingDecimalOdds": position.closing_decimal_odds,
        "note": ("Actual ledger settlement. It is NOT a forecast-quality field and is NOT a "
                 "hypothetical return; no hypothetical return is computed anywhere in this export."),
    }


def build_record(session: Session, candidate: Candidate) -> dict[str, Any]:
    """The capture record for one eligible priced game at one checkpoint."""

    record = {
        "recordType": RECORD_TYPE, "schemaVersion": SCHEMA_VERSION,
        "exportIdentity": EXPORT_IDENTITY, "contractStatus": CONTRACT_STATUS,
        "identity": _identity(candidate), "context": _context(candidate),
        "checkpoint": _checkpoint(candidate), "orientation": _orientation(candidate),
        "probabilities": _probabilities(candidate), "market": _market(candidate),
        "status": _status(candidate), "ledgerSettlement": _ledger_settlement(candidate),
        "outcome": outcome_for(session, candidate.game),
        "clockMeanings": CLOCK_MEANINGS,
    }
    if candidate.notes:
        record["identity"]["notes"] = sorted(set(candidate.notes))
    assert tuple(record) == RECORD_FIELDS, tuple(record)
    assert_money_is_quarantined(record)
    assert_no_derived_comparison(record)
    return record


def _probabilities(candidate: Candidate) -> dict[str, Any]:
    raw, raw_basis = raw_model_probability(candidate)
    adjusted, adjusted_basis = policy_adjusted_probability(candidate)
    evidence = candidate.decision.evidence if isinstance(candidate.decision.evidence, dict) else {}
    vig = de_vig(candidate.selected, candidate.opposing)
    return {
        "forSide": candidate.selected_side,
        "rawModelProbability": {
            "value": raw, "basis": raw_basis, "definition": DEFINITIONS["rawModelProbability"]},
        "policyAdjustedProbability": {
            "value": adjusted, "basis": adjusted_basis,
            "definition": DEFINITIONS["policyAdjustedProbability"]},
        "marketNoVigProbability": {
            "value": vig["selectedNoVigProbability"], "basis": "DERIVED_FROM_BOTH_CAPTURED_LEGS",
            "definition": DEFINITIONS["marketNoVigProbability"]},
        "retainedMarketProbability": {
            "value": _float(evidence.get("market_probability")),
            "basis": ("DECISION_EVIDENCE_MARKET_PROBABILITY"
                      if evidence.get("market_probability") is not None else "NOT_RETAINED"),
            "definition": DEFINITIONS["retainedMarketProbability"]},
    }


def build_exclusion(candidate: Candidate) -> dict[str, Any]:
    """An eligible game with no qualifying evidence still gets a row.

    Silent omission is the failure mode this exists to prevent: a comparison
    run over only the games that happened to resolve cleanly is a comparison
    over a filtered sample, and nothing downstream can tell.
    """

    code, detail = candidate.exclusion or ("UNSPECIFIED", "")
    record = {
        "recordType": EXCLUSION_TYPE, "schemaVersion": SCHEMA_VERSION,
        "exportIdentity": EXPORT_IDENTITY, "contractStatus": CONTRACT_STATUS,
        "identity": {
            "gameId": candidate.game.game_id,
            "decisionId": (str(candidate.decision.id) if candidate.decision is not None
                           else NOT_APPLICABLE),
            "positionId": (str(candidate.position.id) if candidate.position is not None
                           else NOT_APPLICABLE),
            "forecastRowId": None, "forecastRowTable": None,
            "forecastIdentity": None, "forecastIdentityBasis": "NOT_RESOLVED",
            "forecastIdentityAgreesWithRow": None,
        },
        "context": {
            "sport": candidate.game.sport_code, "league": candidate.game.league_code,
            "scheduledStartUtc": pilot.iso(candidate.game.starts_at),
            "homeAbbreviation": candidate.game.home_abbreviation,
            "awayAbbreviation": candidate.game.away_abbreviation,
        },
        "checkpoint": {
            "checkpointAtUtc": pilot.iso(candidate.checkpoint),
            "checkpointBasis": candidate.checkpoint_basis,
            "createdAtNotUsed": DEFINITIONS["createdAtNotUsed"],
        },
        "exclusion": {
            "excluded": True, "reasonCode": code, "reasonDetail": detail,
            "meaning": ("This game was in the eligible universe and is accounted for here rather "
                        "than dropped. It contributes to coverage and must not be counted as a "
                        "captured observation."),
        },
        "clockMeanings": CLOCK_MEANINGS,
    }
    assert tuple(record) == EXCLUSION_FIELDS, tuple(record)
    assert_money_is_quarantined(record)
    assert_no_derived_comparison(record)
    return record


# ---------------------------------------------------------------------------
# Evaluating the slate
# ---------------------------------------------------------------------------
def evaluate_game(session: Session, game: GameRow, *, moment: datetime) -> Candidate:
    """Everything one game contributes at one checkpoint. Reads only."""

    decision, why = deciding_row(session, game.game_id, moment)
    if decision is None:
        candidate = Candidate(game=game, checkpoint=moment,
                              checkpoint_basis="EVALUATION_MOMENT_NO_DECIDING_ROW")
        candidate.exclusion = (why or "NO_DECIDING_ROW", "")
        return candidate

    candidate = Candidate(
        game=game, checkpoint=pilot.aware(decision.recommended_at),
        checkpoint_basis="DECISION_RECOMMENDED_AT", decision=decision)
    candidate.position = position_for(session, decision)
    if decision.shadow_forecast_id is not None:
        forecast = session.get(ShadowForecast, decision.shadow_forecast_id)
        if forecast is not None and not _visible(forecast.available_as_of_utc, candidate.checkpoint):
            candidate.exclusion = ("CONTROLLING_FORECAST_NOT_AVAILABLE_AT_CHECKPOINT",
                                   str(forecast.forecast_id))
            return candidate
        candidate.forecast = forecast
    failure = same_book_pair(session, candidate)
    if failure is not None:
        candidate.exclusion = failure
        return candidate
    if candidate.decision.qualification_outcome is None or candidate.decision.execution_outcome is None:
        # Without both, qualification and funding cannot be stated as distinct
        # facts, and collapsing them is the exact defect this record exists to
        # avoid. It is an exclusion, not a guess.
        candidate.exclusion = ("DECISION_LACKS_DISTINCT_QUALIFICATION_AND_EXECUTION_OUTCOMES",
                               f"qualification={candidate.decision.qualification_outcome} "
                               f"execution={candidate.decision.execution_outcome}")
    return candidate


def build_all(session: Session, *, window_start: datetime, window_end: datetime,
              moment: datetime, leagues: Iterable[str] = DEFAULT_LEAGUES
              ) -> list[tuple[GameRow, dict[str, Any]]]:
    """Every record for the window, captures and exclusions alike. Reads only."""

    built: list[tuple[GameRow, dict[str, Any]]] = []
    for game in universe(session, window_start=window_start, window_end=window_end, leagues=leagues):
        candidate = evaluate_game(session, game, moment=moment)
        if candidate.exclusion is not None:
            built.append((game, build_exclusion(candidate)))
        else:
            built.append((game, build_record(session, candidate)))
    return built


# ---------------------------------------------------------------------------
# Writing. Rule 1 and rule 6.
# ---------------------------------------------------------------------------
def _write_once_same(path: Path, data: bytes) -> bool:
    """Create exactly once. Returns True when this call created the file.

    A path that already holds the SAME bytes is adopted (a crashed run resumes);
    a path that holds DIFFERENT bytes is a refusal. Nothing is ever overwritten,
    and because the filename IS the content hash, "different bytes at the same
    name" means a hash collision or tampering, not an update.
    """

    try:
        write_once(path, data)
        return True
    except FileExistsError:
        if path.read_bytes() != data:
            raise CaptureRefused("RECORD_EXISTS_WITH_DIFFERENT_BYTES", path.name)
        return False


def record_path(directory: Path, game_id: str, content_sha: str) -> Path:
    return Path(directory) / RECORDS_DIR / game_id / f"{content_sha}.json"


def write_record(directory: Path, game: GameRow, record: dict[str, Any], *,
                 ledger: AttemptLedger, now: datetime) -> dict[str, Any]:
    data = pilot.canonical_bytes(record)
    content_sha = _sha(data)
    path = record_path(directory, game.game_id, content_sha)
    created = _write_once_same(path, data)
    ledger.append({
        "kind": "RECORD_WRITTEN" if created else "RECORD_ALREADY_PRESENT",
        "exportIdentity": EXPORT_IDENTITY, "recordType": record["recordType"],
        "gameId": game.game_id, "league": game.league_code,
        "checkpointAtUtc": record["checkpoint"]["checkpointAtUtc"],
        "recordSha256": content_sha, "recordBytes": len(data),
        "recordFile": f"{RECORDS_DIR}/{game.game_id}/{content_sha}.json",
    }, now=now)
    return {"gameId": game.game_id, "recordType": record["recordType"],
            "recordSha256": content_sha, "created": created,
            "file": f"{RECORDS_DIR}/{game.game_id}/{content_sha}.json"}


# ---------------------------------------------------------------------------
# The callable (rule 5: this and the CLI below are the only entry points)
# ---------------------------------------------------------------------------
def run_prospective_capture(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    leagues: Iterable[str] | None = None,
    directory: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Capture every eligible priced game in the window. OFF by default.

    Returns an inert summary when ``prospective_capture_enabled`` is false:
    no row is read, no file is opened, nothing is created.
    """

    if not getattr(settings, "prospective_capture_enabled", False):
        return {"enabled": False, "records": 0, "contractStatus": CONTRACT_STATUS,
                "note": ("PROSPECTIVE_CAPTURE_ENABLED is off; nothing read and nothing written. "
                         "The Model Studio contract is not frozen yet.")}
    clock = clock or (lambda: datetime.now(UTC))
    moment = pilot.aware(now or clock())
    start = pilot.aware(window_start) or (moment - timedelta(hours=12))
    end = pilot.aware(window_end) or (moment + timedelta(hours=36))
    target = Path(directory) if directory is not None else Path(settings.prospective_capture_dir)
    forbidden = _directory_conflict(settings, target)
    if forbidden:
        logger.error("prospective_capture_directory_conflict", code=forbidden, directory=str(target))
        return {"enabled": True, "records": 0, "refused": forbidden}

    ledger = AttemptLedger(target, name=LEDGER_NAME)
    with outcomes.read_only(session), _no_write_guard(session):
        built = build_all(session, window_start=start, window_end=end, moment=moment,
                          leagues=leagues or DEFAULT_LEAGUES)

    written: list[dict[str, Any]] = []
    for game, record in built:
        written.append(write_record(target, game, record, ledger=ledger, now=pilot.aware(clock())))
    captures = [row for row in written if row["recordType"] == RECORD_TYPE]
    exclusions = [row for row in written if row["recordType"] == EXCLUSION_TYPE]
    return {
        "enabled": True, "contractStatus": CONTRACT_STATUS, "directory": str(target),
        "windowStartUtc": pilot.iso(start), "windowEndUtc": pilot.iso(end),
        "checkpointMomentUtc": pilot.iso(moment),
        "universe": len(built), "records": len(written),
        "captures": len(captures), "exclusions": len(exclusions),
        "created": sum(1 for row in written if row["created"]),
        "written": written,
    }


def _directory_conflict(settings: Settings, target: Path) -> str | None:
    """The capture lane may not share a directory with any pilot lane."""

    others = [
        getattr(settings, "v18_market_pilot_packet_dir", None),
        getattr(settings, "v18_market_pilot_outcomes_dir", None),
        getattr(settings, "v18_market_pilot_outcomes_withheld_dir", None),
        getattr(settings, "v18_market_pilot_outcomes_v2_dir", None),
    ]
    for other in others:
        if other and outcomes._overlap(target, Path(other)):
            return "CAPTURE_DIR_OVERLAPS_AN_EXISTING_EXPORT_LANE"
    return None


# ---------------------------------------------------------------------------
# The manual entry point. No scheduler, no job registration. Rule 5.
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """``python -m app.research_exports.prospective_capture`` -- manual only."""

    parser = argparse.ArgumentParser(
        prog="prospective-capture",
        description=("Append-only prospective research capture. DISABLED by default: it does "
                     "nothing unless PROSPECTIVE_CAPTURE_ENABLED is true. It never writes a "
                     "decision, a position or a scheduled job."))
    parser.add_argument("--window-start", help="ISO-8601 UTC; default now-12h")
    parser.add_argument("--window-end", help="ISO-8601 UTC; default now+36h")
    parser.add_argument("--now", help="ISO-8601 UTC evaluation moment; default the wall clock")
    parser.add_argument("--leagues", default=",".join(DEFAULT_LEAGUES))
    parser.add_argument("--directory", default=None)
    args = parser.parse_args(argv)

    from app.core.config import get_settings
    from app.db.session import SessionLocal

    settings = get_settings()
    with SessionLocal() as session:
        summary = run_prospective_capture(
            session, settings,
            now=pilot.aware(args.now) if args.now else None,
            window_start=pilot.aware(args.window_start) if args.window_start else None,
            window_end=pilot.aware(args.window_end) if args.window_end else None,
            leagues=[code.strip() for code in args.leagues.split(",") if code.strip()],
            directory=args.directory)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())


__all__ = [
    "CONTRACT_STATUS", "DEFAULT_LEAGUES", "DEFINITIONS", "EXCLUSION_FIELDS", "EXCLUSION_TYPE",
    "EXPORT_IDENTITY", "LEDGER_NAME", "NOT_APPLICABLE", "RECORD_FIELDS", "RECORD_TYPE",
    "RECORDS_DIR", "COMPLETION_TIME_UNAVAILABLE", "Candidate", "CaptureRefused", "GameRow", "Leg",
    "assert_money_is_quarantined", "assert_no_derived_comparison", "build_all", "build_exclusion",
    "build_record", "de_vig", "deciding_row", "evaluate_game", "main", "outcome_for",
    "policy_adjusted_probability", "raw_model_probability", "record_path",
    "run_prospective_capture", "same_book_pair", "universe", "write_record",
]
