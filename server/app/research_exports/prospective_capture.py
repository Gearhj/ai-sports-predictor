"""Prospective model-vs-market capture, mapped onto the FROZEN consumer contract.

STATUS: MAPPED_TO_FROZEN_CONTRACT_BUILT_NOT_ENABLED
---------------------------------------------------
Model Studio froze the protocol at ``2026-09-21T18:32:03.7468106Z``. This module
emits the record type that freeze names -- ``MODEL_MARKET_OBSERVATION_V1`` -- and
BINDS the exact SHA-256 of the contract file and of the protocol file into every
record it writes. It remains OFF: ``Settings.prospective_capture_enabled``
defaults to ``False`` and every entry point returns an inert result when it is
off, so nothing is read, nothing is written and no provider is called.

WHAT CHANGED WHEN THE CONTRACT FROZE
------------------------------------
The pre-freeze build guessed a schema. Codex's dispositions replaced the guesses,
and each one is enforced here rather than described:

1. RECORD TYPE AND HASH BINDING. :data:`CONTRACT` is ``MODEL_MARKET_OBSERVATION_V1``
   and every record carries :data:`CONTRACT_SHA256` and :data:`PROTOCOL_SHA256`.
   :func:`verify_contract_files` re-hashes the two files on disk and refuses when
   either differs, so a record can never claim a binding it did not check.

2. AN EXCLUSION IS NOT A SECOND FILE. There is ONE record shape. A game that
   cannot be priced or cannot be admitted carries ``admission.state`` of
   ``UNPRICED`` or ``EXCLUDED`` in the SAME record, with the same identity and
   clock envelope. :func:`build_observation` is the only builder;
   :func:`run_prospective_capture` writes exactly one canonical record per
   (game, checkpoint), keyed by :func:`observation_id`, and
   :func:`_admit_one_canonical` refuses a second canonical record for that key.

3. ``positionId`` IS JSON ``null`` WHEN THERE IS NO POSITION. The string
   ``NOT_APPLICABLE`` is gone from the module;
   :func:`assert_no_not_applicable_sentinel` walks the record and refuses it.

4. NULL GENUINE COMPLETION TIME. Accepted only for a source-confirmed terminal
   revision that carries its exact source and revision identity, under the single
   basis :data:`COMPLETION_TIME_UNAVAILABLE`. No retrieval, admission, ingestion
   or feed clock is ever promoted into the completion field.

5. HOME, AWAY AND TIE CHANNELS, AS PUBLISHED. ``forecast.homeWinProbabilityRaw``,
   ``awayWinProbabilityRaw`` and ``tieProbabilityRaw`` are the published channels
   in HOME orientation. MLB tie is ``0.0``. Selected-side values appear only under
   ``forecast.selectedSideAdditional``, never instead of home orientation.

6. NFL DECISIVE-CONDITIONED CHANNEL. NFL records carry the published three-way
   vector AND ``forecast.decisiveConditionedHomeProbability`` = pHome/(pHome+pAway),
   separately named. Official ties are refunds and the record says so in
   ``market.conventions.tieTreatment`` and ``outcome.tieDisposition``.

7. ``checkpointAtUtc`` IS A FIXED T-15. :func:`checkpoint_for` is scheduled start
   minus fifteen minutes and takes no decision clock. The decision's own clock is
   a separate field, ``decision.recommendedAtUtc``.

8. AMBIGUITY IS AN EXCLUSION. Two probability-bearing decision CHANNELS at the
   same tier is ``EXCLUDED`` with an immutable reason code. Recency never breaks
   the tie -- see :func:`deciding_row`.

9. LAYOUT. ``records/<gameId>/<sha256>.json`` plus the hash-chained append-only
   ledger, with a deterministic ``manifest`` entry per write so enumeration is
   complete without walking the tree.

DETERMINISTIC, MODEL-INDEPENDENT BOOK SELECTION
-----------------------------------------------
:func:`eligible_book_candidates` enumerates EVERY sportsbook that has a complete
same-book, same-fetch home/away moneyline pair visible at the checkpoint, each leg
observed at or before the checkpoint and at most fifteen minutes old there. The
selected book is ``min((older_leg_age_seconds, sportsbook_key))`` -- ascending, as
the freeze states. It never reads ``shadow_experiment_decisions.sportsbook`` or
``odds_snapshot_id``, so the choice cannot depend on the model or the decision.
The COMPLETE ordered candidate list is emitted in ``market.eligibleBookCandidates``
and every rejected book in ``market.ineligibleBookCandidates`` with its reason, so
the ordering is independently verifiable from the record alone.

CONVENTIONS: MISSING SEMANTICS YIELD ``UNPRICED``
-------------------------------------------------
:data:`BOUND_CONVENTIONS` is the ONLY place a settlement, extra-innings,
overtime or pitcher/action convention may come from, and it is EMPTY: no retained
provider response states any of them (see
``v18_market_pilot._conventions``). An unbound convention is therefore
``UNPRICED`` with an immutable reason code. Nothing is inferred and no default is
assumed.

THE READ-ONLY GUARANTEE (five properties, none of them a row count)
-------------------------------------------------------------------
A row-count diff cannot see an UPDATE that preserves counts and is confused by
legitimate concurrent production writes, so it is NOT the barrier. The barrier is:

A. :func:`read_only_session` opens an explicit PostgreSQL ``SET TRANSACTION READ
   ONLY``, CONFIRMS it with ``SHOW transaction_read_only``, and always rolls back.
B. :func:`assert_module_has_no_mutation_path` AST-walks THIS module's own source
   and refuses any ORM or SQL mutation path. It is called on import.
C. File output is content-addressed and append-only, and
   :func:`_refuse_write_inside_read_transaction` makes a write DURING the read
   transaction a refusal, so writes provably happen outside it.
D. :func:`read_only_session` installs ``before_flush`` and ``do_orm_execute``
   listeners, so a mutation attempted through the exporter's session FAILS on
   every dialect, SQLite included.
E. Nothing in the production path compares row counts, so a concurrent legitimate
   production write can never be mistaken for a write by this exporter.
   :func:`sqlite_row_count_probe` remains ONLY as a test aid and says so.

CLOCKS
------
The session timezone is ``America/New_York`` and psycopg hands back ET-aware
datetimes, so every clock goes through :func:`v18_market_pilot.iso`, which converts
to UTC BEFORE formatting. ``created_at`` is never used as a clock: in PostgreSQL it
is the TRANSACTION-START stamp.

POST-FREEZE EVIDENCE ONLY
-------------------------
:data:`FREEZE_INSTANT_UTC` is enforced, not asserted: a game whose checkpoint falls
before the freeze is ``EXCLUDED`` with ``PRE_FREEZE_EVIDENCE_NEVER_ENROLLED``. The
first genuine record this module can produce therefore rests on post-freeze
evidence by construction.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy import event, select
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
# The frozen contract. Everything Codex has to agree with lives in this block.
# ---------------------------------------------------------------------------
#: The record type the freeze names. Disposition 1.
CONTRACT = "MODEL_MARKET_OBSERVATION_V1"
#: SHA-256 of CONSUMER_EXPORT_CONTRACT.md, bound into every record. Disposition 1.
CONTRACT_SHA256 = "e0ee599e6c6efeea3ab7699f4c6682003b9ac23e6acc458641f719bfb2ac9fc5"
#: SHA-256 of FROZEN_PROTOCOL.json, bound alongside it.
PROTOCOL_SHA256 = "fcb530fc1d12d20f688ff1859fba1b4c5bb63115719cd3e629d8fbf05f10a9e3"
CONTRACT_FILE = "CONSUMER_EXPORT_CONTRACT.md"
PROTOCOL_FILE = "FROZEN_PROTOCOL.json"
#: FROZEN_PROTOCOL.json ``frozenAtUtc``. Evidence older than this never enrolls.
FREEZE_INSTANT_UTC = "2026-09-21T18:32:03.7468106Z"
FREEZE_INSTANT = datetime(2026, 9, 21, 18, 32, 3, 746810, tzinfo=UTC)
#: Enrollment closes here (FROZEN_PROTOCOL.json ``enrollmentEndUtc``).
ENROLLMENT_END = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

#: The status a genuine prospective record carries.
STATUS_PROSPECTIVE = "PROSPECTIVE_OBSERVATION"
#: The status the contract's own example uses; the synthetic example reuses it.
STATUS_SYNTHETIC = "SYNTHETIC_CONTRACT_EXAMPLE_NOT_EVIDENCE"

EXPORT_IDENTITY = "AISP_MODEL_MARKET_PROSPECTIVE_CONSUMER_EXPORT"
LEDGER_NAME = "observation-ledger.jsonl"
RECORDS_DIR = "records"
OUTCOMES_DIR = "outcome-revisions"

#: Disposition 7: the checkpoint is scheduled start minus fifteen minutes, fixed.
CHECKPOINT_MINUTES_BEFORE_START = 15
#: Each quote leg must be at most this old AT the checkpoint.
MAX_QUOTE_AGE_SECONDS = 15 * 60

#: Disposition 4: the only accepted basis for a null genuine completion time.
COMPLETION_TIME_UNAVAILABLE = "SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_UNAVAILABLE"

#: Sports in the study. FROZEN_PROTOCOL.json names exactly these two, and they
#: are never pooled (``fixedProhibitions``: "No cross-sport pooling").
SPORTS = ("MLB", "NFL")

ADMISSION_PRICED = "PRICED_ELIGIBLE"
ADMISSION_UNPRICED = "UNPRICED"
ADMISSION_EXCLUDED = "EXCLUDED"
ADMISSION_STATES = (ADMISSION_PRICED, ADMISSION_UNPRICED, ADMISSION_EXCLUDED)

#: IMMUTABLE reason codes. A code is added here or it is not emitted; the test
#: suite pins this set so a silent rename cannot happen.
EXCLUSION_REASON_CODES = frozenset({
    "PRE_FREEZE_EVIDENCE_NEVER_ENROLLED",
    "CHECKPOINT_AFTER_ENROLLMENT_END",
    "SCHEDULED_START_MISSING",
    "NO_FINAL_PREGAME_DECISION_AT_CHECKPOINT",
    "ALL_FINAL_PREGAME_DECISIONS_AFTER_CHECKPOINT",
    "NO_PROBABILITY_BEARING_DECISION_CHANNEL",
    "AMBIGUOUS_PROBABILITY_BEARING_DECISION_CHANNELS",
    "NO_CONTROLLING_FORECAST_NAMED",
    "CONTROLLING_FORECAST_ROW_MISSING",
    "CONTROLLING_IDENTITY_DISAGREES_WITH_FORECAST_ROW",
    "FORECAST_SNAPSHOT_TYPE_IS_NOT_FINAL_PREGAME",
    "FORECAST_EXPOSURE_CLOCK_MISSING",
    "FORECAST_EXPOSED_AFTER_CHECKPOINT",
    "FORECAST_IMPORT_CLOCK_MISSING",
    "FORECAST_IMPORTED_AFTER_CHECKPOINT",
    "PUBLISHED_PROBABILITIES_DO_NOT_SUM_TO_ONE",
    "NFL_ELIGIBILITY_SUPPLEMENT_MISSING",
    "GAME_ORIENTATION_NOT_RESOLVABLE",
})
UNPRICED_REASON_CODES = frozenset({
    "NO_MONEYLINE_MARKET_FOR_GAME",
    "NO_COMPLETE_SAME_BOOK_PAIR_AT_CHECKPOINT",
    "MARKET_CONVENTION_SETTLEMENT_NOT_ESTABLISHED",
    "MARKET_CONVENTION_EXTRA_INNINGS_NOT_ESTABLISHED",
    "MARKET_CONVENTION_OVERTIME_NOT_ESTABLISHED",
    "MARKET_CONVENTION_PITCHER_ACTION_NOT_ESTABLISHED",
})
#: Why one BOOK was not an eligible candidate. Emitted per book, not as an
#: admission code, so the deterministic ordering can be re-derived from the record.
BOOK_REASON_CODES = frozenset({
    "BOOK_LEG_MISSING_AT_CHECKPOINT",
    "BOOK_LEG_OBSERVED_AFTER_CHECKPOINT",
    "BOOK_LEG_QUOTE_AGE_OVER_FIFTEEN_MINUTES",
    "BOOK_LEG_PRICE_NOT_VALID_AMERICAN_ODDS",
    "BOOK_LEGS_FROM_DIFFERENT_PROVIDER",
    "BOOK_LEGS_FROM_DIFFERENT_FETCH_IDENTITY",
})
REASON_CODES = EXCLUSION_REASON_CODES | UNPRICED_REASON_CODES | BOOK_REASON_CODES

#: The ordered key set of a record. ``build_observation`` asserts against it and
#: :func:`validate_record` checks it on the way back in.
RECORD_FIELDS = (
    "contract", "contractSha256", "protocolSha256", "status",
    "observationId", "sport", "gameId", "sourceEventAliases",
    "scheduledStartUtc", "checkpointAtUtc", "checkpointBasis",
    "forecast", "market", "decision", "admission", "outcome",
    "evidenceWindow", "exporter", "clockMeanings",
)
OUTCOME_REVISION_FIELDS = (
    "contract", "contractSha256", "status", "observationId", "sport", "gameId",
    "checkpointAtUtc", "revisionOrdinal", "outcome", "exporter",
)
OUTCOME_REVISION_CONTRACT = "MODEL_MARKET_OUTCOME_REVISION_V1"

#: Disposition 3: this sentinel must never appear again. Pinned by test.
FORBIDDEN_SENTINEL = "NOT_APPLICABLE"

#: Settlement semantics may come from HERE and nowhere else, keyed by
#: ``(sport, sportsbook key)`` -- the key exactly as the selected pair carries it.
#:
#: KEYED BY THE BOOK, NOT THE FEED.
#: ---------------------------------
#: This registry used to be keyed by the provider SOURCE, ``SPORTSGAMEODDS``.
#: SportsGameOdds is an aggregator: one response relays nine books, and each
#: settles by its OWN house rules. Binding a convention at the feed level would
#: have stamped one operator's rules onto every book the feed carries. The
#: contract places the conventions beside ``sportsbookKey`` for this reason.
#:
#: WHAT MAY BE BOUND, AND WHAT MAY NOT.
#: ------------------------------------
#: An entry needs (a) a book key that unambiguously names a live operator and
#: (b) that operator's own published rule text, retained with its hash. Anything
#: less stays UNPRICED. Deliberately NOT bound, with the reason:
#:
#: * ``sgo:espnbet`` -- ESPN BET became theScore Bet on 2025-12-01. SGO still
#:   returns ``espnbet`` and never ``thescorebet``, and documents no alias, so
#:   which operator's rules govern that key is an inference, not a document.
#: * ``sgo:williamhill``, ``sgo:pointsbet``, ``sgo:unibet`` -- legacy brands
#:   (Caesars since 2021; Fanatics since 2024; exited the US market), with the
#:   same undocumented mapping problem. ``sgo:bovada`` -- offshore, not a US
#:   regulated operator.
#: * ``sgo:draftkings``, ``sgo:betmgm`` -- their rules pages returned only a
#:   script shell with no rule text when retained; ``sgo:caesars`` refused
#:   retrieval (HTTP 403) and was not circumvented. Bindable once retained.
#: * MLB -- nothing retained states extra-innings or listed-pitcher rules for
#:   any book (``v18_market_pilot._conventions`` documents the reads).
BOUND_CONVENTIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("NFL", "sgo:fanduel"): {
        "settlementRule": "TWO_WAY_MONEYLINE_TIE_VOIDED_STAKE_REFUNDED",
        "overtimeRule": "INCLUDED",
        "postponementRule": "VOID_UNLESS_RESUMED_OR_RESCHEDULED_WITHIN_60H_OF_INITIAL_KICKOFF",
        "suspensionRule": "VOID_IF_NOT_COMPLETED_WITHIN_24H_UNLESS_UNEQUIVOCALLY_DETERMINED",
        "resultSource": "LEAGUE_GOVERNING_BODY",
        "document": {
            "operator": "FanDuel Sportsbook",
            "title": "FanDuel Sportsbook House Rules (Ohio) -- American Football General Rules",
            "url": "https://www.fanduel.com/fanduel-sportsbook-house-rules-oh",
            "retrievedAtUtc": "2026-09-22T12:51:50Z",
            "sha256": "95a620d559415676de3a208a94ddaccda3f6d3423fa632d745f99c4deaae79b1",
            "bytes": 1004568,
            "retainedAt": (r"C:\Users\james\aisp-analysis\conventions-evidence-20260922"
                           r"\fanduel.html"),
            # The document is ONE state's house rules. SportsGameOdds never says
            # which jurisdiction a quote comes from, so this is recorded rather
            # than assumed away.
            "documentJurisdiction": "US-OH",
            "quoteJurisdiction": "NOT_STATED_BY_PROVIDER",
        },
        "verbatim": {
            "overtimeRule": "Overtime counts for all markets unless stated otherwise.",
            "settlementRule": "In the event of a tie, all money line wagers will be voided.",
            "postponementRule": (
                "Abandoned or postponed games must be resumed or rescheduled within 60 hours "
                "of the initial kickoff time for bets to have action, otherwise bets will be "
                "voided."),
            "suspensionRule": (
                "If a game starts and then is suspended prior to the above time and is not "
                "completed within 24 hours, all bets that are not unequivocally determined "
                "will be voided."),
            "resultSource": ("All settlements are based on results and statistics provided "
                             "by the relevant league's governing body."),
        },
    },
}

#: Which conventions each sport requires before a pair may be PRICED_ELIGIBLE.
REQUIRED_CONVENTIONS: dict[str, tuple[str, ...]] = {
    "MLB": ("settlementRule", "extraInningsRule", "pitcherActionRule"),
    "NFL": ("settlementRule", "overtimeRule"),
}
_CONVENTION_REASON = {
    "settlementRule": "MARKET_CONVENTION_SETTLEMENT_NOT_ESTABLISHED",
    "extraInningsRule": "MARKET_CONVENTION_EXTRA_INNINGS_NOT_ESTABLISHED",
    "overtimeRule": "MARKET_CONVENTION_OVERTIME_NOT_ESTABLISHED",
    "pitcherActionRule": "MARKET_CONVENTION_PITCHER_ACTION_NOT_ESTABLISHED",
}

#: The evidence keys that mark the MLB V2 probability-bearing decision channel.
EVIDENCE_PROBABILITY_KEYS = ("raw_model_probability", "adjusted_probability", "market_probability")

_TERMINAL_NFL_STATUS = {"FINAL", "FINAL_OVERTIME", "COMPLETED", "STATUS_FINAL"}
_TERMINAL_STATE_STATUS = {"FINAL", "COMPLETED", "GAME_OVER_FINAL"}
_PROBABILITY_TOLERANCE = 1e-6


class CaptureRefused(RuntimeError):
    """A condition under which no record may be written."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Definition strings. Every clock and channel arrives with the sentence that
# says how it was derived, so no reader has to guess.
# ---------------------------------------------------------------------------
CLOCK_MEANINGS = {
    "checkpointAtUtc": (
        "SCHEDULED START MINUS FIFTEEN MINUTES, fixed. It is not the decision's recommended_at, "
        "not a job clock and not created_at. games.starts_at is the only input."),
    "recommendedAtUtc": (
        "shadow_experiment_decisions.recommended_at: the instant AISP's decision was actually "
        "recorded. Carried separately from the checkpoint and never substituted for it."),
    "forecastGeneratedAtUtc": (
        "shadow_forecasts.forecast_cutoff_utc: the producer's stated generation cutoff for the "
        "snapshot. It is a generation clock, never an availability one."),
    "forecastExposedAtUtc": (
        "shadow_forecasts.available_as_of_utc: the forecast's own exposure clock, the instant the "
        "producer states it could be used. Must be at or before the checkpoint."),
    "forecastPublishedAtUtc": (
        "shadow_experiment_decisions.forecast_published_at: when the producer published the "
        "controlling snapshot, as the decision recorded it."),
    "forecastImportedAtOrBeforeUtc": (
        "shadow_experiment_decisions.forecast_imported_at: the IMPORT COMPLETION the decision "
        "recorded. Deliberately not the forecast row's created_at, which in PostgreSQL is the "
        "TRANSACTION-START stamp -- on 2026-09-20 eight FINAL rows carried created_at 12:41:07 "
        "while their transaction did not commit until 12:43:32. Must be at or before the checkpoint."),
    "forecastConsumerObservedAtUtc": (
        "shadow_experiment_decisions.recommended_at: the instant the consumer demonstrably had the "
        "forecast in hand, because it decided from it."),
    "quoteObservedAtUtc": (
        "odds_snapshots.observed_at: the provider-stated lastUpdatedAt for this book leg -- when the "
        "provider last recorded a change to (or confirmation of) this price. Leg age at the "
        "checkpoint is measured from it. An old value means the price had not been recorded as "
        "changing; it is NOT evidence that the price was unavailable, and must not be read as such."),
    "quoteConsumerReceiptAtUtc": (
        "odds_snapshots.available_as_of: the collector's REQUEST-SEND clock (the raw archive's "
        "requested_at). It precedes receipt: the response was received and archived later (the "
        "archive's ingestion_timestamp, not carried in this field), and the row became readable only "
        "when its transaction committed after that. A lower bound on when AISP held the price; never "
        "the book's clock and never a receipt instant."),
    "outcomeSourceCaptureAtUtc": (
        "The source-of-record row's own retrieval clock (nfl_final_observations.retrieved_at, or "
        "game_state_observations.available_as_of). A capture clock, never a completion time."),
    "outcomeSourceAdmissionAtUtc": (
        "When the retained observation row was admitted by AISP's ingest. Distinct from capture and "
        "never promoted into a completion time."),
    "outcomeSourceAvailabilityAtUtc": (
        "The availability bound of the admitted observation: the earliest instant the result could "
        "be read by a consumer."),
    "genuineCompletionAtUtc": (
        "The SOURCE's OWN completion field and nothing else. Null with basis "
        f"'{COMPLETION_TIME_UNAVAILABLE}' when the source confirmed a terminal state but supplied no "
        "completion time -- which is always the case for NFL, whose source of record "
        "(nfl_final_observations) has no completion column. Never a retrieval clock, never a "
        "scheduled start, never a feed's last-updated string, never created_at."),
    "sourceLastUpdatedRaw": (
        "nfl_final_observations.source_last_updated: an UNPARSED feed string, carried verbatim as "
        "the source wrote it. It is evidence, not a clock, and is never parsed into one."),
    "createdAtNotUsed": (
        "PostgreSQL created_at is the transaction-START stamp, not an insertion, commit or "
        "publication time. No clock in this record is ever taken from it."),
}

CHANNEL_MEANINGS = {
    "homeWinProbabilityRaw": (
        "The UNCHANGED model's published HOME win probability, exactly as the controlling forecast "
        "row states it (shadow_forecasts.full_home_win_probability). No policy adjustment, no "
        "uncertainty widening, no normalisation."),
    "tieProbabilityRaw": (
        "MLB: 0.0 -- the whole-game moneyline including extra innings has no tie. NFL: the published "
        "residual 1 - pHome - pAway, which is the tie mass the model prices, retained rather than "
        "folded into either side."),
    "decisiveConditionedHomeProbability": (
        "NFL ONLY, and separately named: pHome / (pHome + pAway), the model's home probability on "
        "the market's own conditional basis. FROZEN_PROTOCOL arm A. The binary comparison uses THIS "
        "channel; official ties refund and stay outside its denominator. Null for MLB, whose tie "
        "probability is zero and which therefore has no separate conditional channel."),
    "homeWinProbabilityAdjusted": (
        "The HOME probability after AISP's own policy adjustment. It is populated only when the "
        "deciding channel retained an adjusted probability AND that probability is already oriented "
        "to home (the decision selected HOME). It is NEVER derived as 1 - adjusted(away): the policy "
        "adjustment is not a complement and asserting one would invent a number. Otherwise null with "
        "a basis, and the selected-side value appears under selectedSideAdditional."),
    "homeWinProbabilityControlling": (
        "The probability the decision actually controlled on "
        "(shadow_experiment_decisions.model_probability), oriented to home under exactly the same "
        "rule as the adjusted channel: populated only when it is already home-oriented, otherwise "
        "null with a basis. Never substituted from the raw or adjusted channel."),
    "selectedSideAdditional": (
        "ADDITIONAL selected-side orientation, present so nothing retained is lost. It never "
        "replaces the home/away/tie channels above and the contract's scoring never reads it."),
    "marketImpliedNoVig": (
        "Two-way same-book no-vig home probability qHome = rHome/(rHome+rAway) with r = 1/decimal, "
        "computed by exact rational arithmetic on the American prices of the SELECTED pair. For a "
        "refund-on-tie market the two prices span 'home wins given somebody wins', so this is a "
        "CONDITIONAL two-way probability and is comparable only with the decisive-conditioned model "
        "channel, never with an unconditional three-way one."),
}


# ---------------------------------------------------------------------------
# Contract binding
# ---------------------------------------------------------------------------
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_contract_files(directory: str | Path) -> dict[str, Any]:
    """Re-hash the two frozen files and refuse any mismatch.

    A record binds :data:`CONTRACT_SHA256`; this is what makes that binding a
    CHECK rather than a claim. It reads two files and nothing else.
    """

    root = Path(directory)
    checked: dict[str, Any] = {}
    for name, expected in ((CONTRACT_FILE, CONTRACT_SHA256), (PROTOCOL_FILE, PROTOCOL_SHA256)):
        path = root / name
        try:
            actual = _sha(path.read_bytes())
        except OSError as error:
            raise CaptureRefused("FROZEN_CONTRACT_FILE_UNREADABLE", f"{path}: {error}") from error
        if actual != expected:
            raise CaptureRefused("FROZEN_CONTRACT_FILE_HASH_MISMATCH",
                                 f"{name} expected={expected} actual={actual}")
        checked[name] = {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}
    return checked


def contract_binding() -> dict[str, str]:
    return {"contract": CONTRACT, "contractSha256": CONTRACT_SHA256,
            "protocolSha256": PROTOCOL_SHA256}


# ---------------------------------------------------------------------------
# Property B of the read-only guarantee: no mutation path in this module.
# ---------------------------------------------------------------------------
#: ORM and SQL mutation entry points. If the module's own AST calls one of these
#: as a method, or imports one of the mutating SQLAlchemy constructors, the module
#: refuses to load.
FORBIDDEN_CALL_ATTRIBUTES = frozenset({
    "add", "add_all", "merge", "delete", "flush", "commit", "bulk_save_objects",
    "bulk_insert_mappings", "bulk_update_mappings", "bulk_update_query",
    "execute_insert", "executemany", "expunge_all",
})
FORBIDDEN_IMPORT_NAMES = frozenset({"insert", "update", "delete", "Insert", "Update", "Delete"})
FORBIDDEN_SQL_TOKENS = ("insert into", "update ", "delete from", "merge into", "truncate ",
                        "alter table", "drop table")
#: ``session.rollback`` and ``session.get``/``scalars``/``scalar``/``execute`` are
#: reads (or the mandated undo) and are allowed. ``Path.unlink``/``write_*`` never
#: appear here: file creation goes through ``write_once`` exclusively.


def module_source() -> str:
    return Path(inspect.getsourcefile(module_source) or __file__).read_text(encoding="utf-8")


def assert_module_has_no_mutation_path(source: str | None = None) -> dict[str, Any]:
    """AST-walk this module and refuse any INSERT/UPDATE/DELETE/ORM-mutation path.

    This is the SECOND property of the read-only guarantee and it is checked at
    import time, not only by a test: a future edit that adds ``session.add`` or
    imports ``sqlalchemy.update`` stops the module from loading at all.
    """

    text = source if source is not None else module_source()
    tree = ast.parse(text)
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in FORBIDDEN_CALL_ATTRIBUTES:
                findings.append(f"call .{node.func.attr}() at line {node.lineno}")
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy"):
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORT_NAMES:
                    findings.append(f"import sqlalchemy.{alias.name} at line {node.lineno}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            for token in FORBIDDEN_SQL_TOKENS:
                # A docstring may NAME the token; a token followed by an opening
                # paren or a table-ish word inside a non-docstring literal is SQL.
                if token in lowered and not _is_documentation_constant(tree, node):
                    findings.append(f"SQL token {token!r} at line {node.lineno}")
    if findings:
        raise CaptureRefused("EXPORTER_CONTAINS_A_MUTATION_PATH", "; ".join(sorted(set(findings))))
    return {"checked": True, "forbiddenCallAttributes": sorted(FORBIDDEN_CALL_ATTRIBUTES),
            "forbiddenImports": sorted(FORBIDDEN_IMPORT_NAMES),
            "forbiddenSqlTokens": list(FORBIDDEN_SQL_TOKENS),
            "sourceSha256": _sha(text.encode("utf-8"))}


def _is_documentation_constant(tree: ast.AST, node: ast.Constant) -> bool:
    """True when the string is a docstring or a module-level definition string.

    Prose is allowed to NAME a mutation; only executable SQL is refused. The
    distinction is made structurally: a constant that is the first statement of a
    module, class or function body, or that is an element of the module-level
    ``FORBIDDEN_SQL_TOKENS`` tuple, is documentation.
    """

    for parent in ast.walk(tree):
        if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(parent, "body", [])
            if body and isinstance(body[0], ast.Expr) and body[0].value is node:
                return True
        if isinstance(parent, (ast.Tuple, ast.List, ast.Set)) and node in parent.elts:
            return True
        if isinstance(parent, ast.Dict) and (node in parent.values or node in parent.keys):
            return True
        if isinstance(parent, ast.JoinedStr) and node in parent.values:
            return True
    return False


# ---------------------------------------------------------------------------
# Properties A, C and D: the read-only session.
# ---------------------------------------------------------------------------
#: Set in ``session.info`` for exactly as long as the read transaction is open.
#: ``_refuse_write_inside_read_transaction`` reads it, which is what proves file
#: output happens OUTSIDE the database transaction (property C).
READ_TX_MARKER = "aisp_prospective_capture_read_tx"


class _ReadOnlyViolation(CaptureRefused):
    """A mutation attempted through the exporter's session."""


def _refuse_flush(session: Session, *_: Any) -> None:
    raise _ReadOnlyViolation(
        "MUTATION_ATTEMPTED_THROUGH_READ_ONLY_SESSION",
        f"new={len(session.new)} dirty={len(session.dirty)} deleted={len(session.deleted)}")


def _refuse_non_select(state: Any) -> None:
    if getattr(state, "is_select", False):
        return
    if getattr(state, "is_insert", False) or getattr(state, "is_update", False) \
            or getattr(state, "is_delete", False):
        raise _ReadOnlyViolation("MUTATION_STATEMENT_ATTEMPTED_THROUGH_READ_ONLY_SESSION",
                                 type(getattr(state, "statement", None)).__name__)


@contextmanager
def read_only_session(session: Session):
    """Every read in one explicitly READ ONLY transaction, on every dialect.

    Three things happen here, and each is one of the five properties:

    A. :func:`v18_market_pilot_outcomes.read_only` issues PostgreSQL
       ``SET TRANSACTION READ ONLY``, CONFIRMS it with
       ``SHOW transaction_read_only`` and refuses when the server does not agree.
       It always rolls back.
    D. ``before_flush`` and ``do_orm_execute`` listeners are attached to THIS
       session, so an attempted mutation raises on SQLite too, where the
       PostgreSQL statement is a no-op. They are removed on the way out.
    C. ``session.info[READ_TX_MARKER]`` is set for the lifetime of the
       transaction, so :func:`_refuse_write_inside_read_transaction` can refuse a
       file write attempted while it is open.
    """

    event.listen(session, "before_flush", _refuse_flush)
    event.listen(session, "do_orm_execute", _refuse_non_select)
    try:
        with outcomes.read_only(session) as bound:
            session.info[READ_TX_MARKER] = True
            try:
                yield bound
            finally:
                session.info.pop(READ_TX_MARKER, None)
    finally:
        event.remove(session, "before_flush", _refuse_flush)
        event.remove(session, "do_orm_execute", _refuse_non_select)


def _refuse_write_inside_read_transaction(session: Session | None) -> None:
    """Property C: a file write may not happen inside the read transaction."""

    if session is not None and session.info.get(READ_TX_MARKER):
        raise CaptureRefused("FILE_WRITE_ATTEMPTED_INSIDE_READ_TRANSACTION", "")


def sqlite_row_count_probe(session: Session) -> tuple[int, int]:
    """TEST AID ONLY -- NOT the production write barrier.

    It counts ``shadow_experiment_decisions`` and ``official_asp_positions`` rows
    so a test can assert a specific fixture was untouched. It is deliberately not
    used anywhere in :func:`run_prospective_capture`, because a row-count diff
    CANNOT see an UPDATE that preserves counts and is confused by legitimate
    concurrent production writes -- a concurrent insert by the real decision path
    would make it accuse this exporter of a write it did not perform. The real
    barrier is properties A-D above.
    """

    from sqlalchemy import func as _func  # local: it exists for this aid alone

    return (
        int(session.scalar(select(_func.count(ShadowExperimentDecision.id))) or 0),
        int(session.scalar(select(_func.count(OfficialAspPosition.id))) or 0),
    )


READ_ONLY_PROOF = {
    "A_explicitReadOnlyTransaction": (
        "app.research_exports.v18_market_pilot_outcomes.read_only issues SET TRANSACTION READ ONLY "
        "on PostgreSQL, confirms it with SHOW transaction_read_only, refuses when the server does "
        "not agree, and always rolls back."),
    "B_noMutationPathInTheModule": (
        "assert_module_has_no_mutation_path AST-walks this module at import time and refuses any "
        "ORM mutation call, any mutating SQLAlchemy import and any executable SQL mutation literal."),
    "C_fileOutputOutsideTheTransaction": (
        "Records are content-addressed and created through write_once (exclusive create, then "
        "read-only). _refuse_write_inside_read_transaction makes a write attempted while the read "
        "transaction is open a refusal, so writes provably happen after it closes."),
    "D_mutationThroughTheSessionFails": (
        "read_only_session attaches before_flush and do_orm_execute listeners to the exporter's "
        "session, so an attempted insert, update, delete or flush raises on every dialect, SQLite "
        "included."),
    "E_notARowCountDiff": (
        "No production path compares row counts, so a legitimate concurrent production write can "
        "never be reported as a write by this exporter. sqlite_row_count_probe survives only as a "
        "test aid and is documented as one."),
}


# ---------------------------------------------------------------------------
# Disposition 3: the NOT_APPLICABLE sentinel is gone.
# ---------------------------------------------------------------------------
def _walk(value: Any, path: str) -> Iterable[tuple[str | None, str, Any]]:
    stack: list[tuple[Any, str]] = [(value, path)]
    while stack:
        item, where = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                yield key, f"{where}/{key}", child
                stack.append((child, f"{where}/{key}"))
        elif isinstance(item, list):
            for index, child in enumerate(item):
                yield None, f"{where}[{index}]", child
                stack.append((child, f"{where}[{index}]"))


def assert_no_not_applicable_sentinel(record: Any, where: str = "record") -> None:
    """Disposition 3. Absence is JSON ``null``; it is never a magic string."""

    for _, path, child in _walk(record, where):
        if isinstance(child, str) and child == FORBIDDEN_SENTINEL:
            raise CaptureRefused("FORBIDDEN_NOT_APPLICABLE_SENTINEL", path)


#: The comparison is Model Studio's to compute; shipping one would be doing their
#: job with our thumb on it. Nothing shaped like it may appear anywhere.
DERIVED_COMPARISON_KEYS = frozenset({
    "blend", "blended", "blendedProbability", "blendWeight", "brier", "brierScore", "logLoss",
    "logarithmicScore", "calibrationBin", "skillScore", "threshold", "chosenThreshold", "optimal",
    "optimalThreshold", "optimised", "optimized", "ranking", "modelBeatsMarket", "verdict", "grade",
})
#: Money belongs to the governed ledger, not to a forecast-quality record.
MONEY_KEYS = frozenset({
    "stake", "payout", "profitLoss", "bankrollBefore", "bankrollAfter", "closingLineValue",
    "closingDecimalOdds", "returns", "roi", "profit", "pnl", "netUnits", "units",
})


def assert_no_derived_comparison(record: Any, where: str = "record") -> None:
    for key, path, _ in _walk(record, where):
        if key in DERIVED_COMPARISON_KEYS:
            raise CaptureRefused("DERIVED_COMPARISON_FIELD_PRESENT", path)


def assert_no_money(record: Any, where: str = "record") -> None:
    for key, path, _ in _walk(record, where):
        if key in MONEY_KEYS:
            raise CaptureRefused("MONEY_FIELD_PRESENT", path)


# ---------------------------------------------------------------------------
# Clocks and the fixed checkpoint
# ---------------------------------------------------------------------------
def checkpoint_for(starts_at: Any) -> datetime | None:
    """Disposition 7: scheduled start minus fifteen minutes. Nothing else."""

    moment = pilot.aware(starts_at)
    return None if moment is None else moment - timedelta(minutes=CHECKPOINT_MINUTES_BEFORE_START)


def _at_or_before(moment: Any, bound: datetime) -> bool:
    at = pilot.aware(moment)
    return at is not None and at <= bound


def _age_seconds(moment: Any, bound: datetime) -> float | None:
    at = pilot.aware(moment)
    return None if at is None else round((bound - at).total_seconds(), 3)


def observation_id(sport: str, game_id: str, checkpoint: datetime) -> str:
    """The canonical key: ONE observation per sport, game and checkpoint.

    Deterministic and content-free -- it carries no export clock -- so repeated
    ticks over the same game compute the same id and cannot create a second
    canonical record (disposition 2 and 9).
    """

    payload = f"{CONTRACT}|{sport}|{game_id}|{pilot.iso(checkpoint)}".encode("utf-8")
    return f"obs-{_sha(payload)}"


# ---------------------------------------------------------------------------
# Reading the slate
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GameRow:
    game_id: str
    league_code: str
    sport: str
    starts_at: datetime | None
    home_team_id: str | None
    away_team_id: str | None
    home_abbreviation: str | None
    away_abbreviation: str | None


@dataclass(frozen=True)
class Leg:
    side: str
    snapshot_id: str
    market_outcome_id: str
    american_odds: int | None
    decimal_odds: Fraction | None
    observed_at: datetime | None
    receipt_at: datetime | None
    source: str | None
    source_mode: str | None
    source_event_id: str | None
    payload_checksum: str | None


@dataclass
class BookCandidate:
    sportsbook_key: str
    sportsbook_name: str | None
    home: Leg | None = None
    away: Leg | None = None
    older_leg_age_seconds: float | None = None
    older_leg_side: str | None = None
    reason_codes: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return not self.reason_codes and self.home is not None and self.away is not None

    def sort_key(self) -> tuple[float, str]:
        """The frozen deterministic ordering: (older_leg_age_seconds, sportsbook_key)."""

        return (float(self.older_leg_age_seconds or 0.0), self.sportsbook_key)


@dataclass
class Observation:
    game: GameRow
    checkpoint: datetime
    decision: ShadowExperimentDecision | None = None
    position: OfficialAspPosition | None = None
    forecast: ShadowForecast | None = None
    candidates: list[BookCandidate] = field(default_factory=list)
    selected: BookCandidate | None = None
    reason_codes: list[str] = field(default_factory=list)
    reason_detail: dict[str, str] = field(default_factory=dict)
    state: str = ADMISSION_PRICED

    def refuse(self, code: str, detail: str = "") -> None:
        """Record an immutable reason code and move the admission state.

        EXCLUDED dominates UNPRICED: a game that cannot be admitted at all is not
        merely unpriced, and the codes accumulate so nothing is lost either way.
        """

        if code in EXCLUSION_REASON_CODES:
            self.state = ADMISSION_EXCLUDED
        elif code in UNPRICED_REASON_CODES:
            if self.state != ADMISSION_EXCLUDED:
                self.state = ADMISSION_UNPRICED
        else:
            raise CaptureRefused("UNKNOWN_REASON_CODE", code)
        if code not in self.reason_codes:
            self.reason_codes.append(code)
        if detail:
            self.reason_detail[code] = detail


def universe(session: Session, *, window_start: datetime, window_end: datetime,
             sports: Iterable[str] = SPORTS) -> list[GameRow]:
    """Every game of the named leagues starting inside the window.

    The universe is the SLATE. A game that turns out to be unpriced still has to
    be accounted for, with a reason, or the coverage denominator is quietly AISP's
    own selection.
    """

    codes = sorted({str(code).upper() for code in sports})
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
            game_id=str(game_id), league_code=str(league_code).upper(),
            sport=str(league_code).upper(), starts_at=pilot.aware(starts_at),
            home_team_id=str(home[1]) if home else None,
            away_team_id=str(away[1]) if away else None,
            home_abbreviation=str(home[2]) if home else None,
            away_abbreviation=str(away[2]) if away else None))
    return out


def deciding_row(session: Session, game_id: str, checkpoint: datetime
                 ) -> tuple[ShadowExperimentDecision | None, str | None]:
    """The PROBABILITY-BEARING FINAL_PREGAME decision, chosen by CHANNEL.

    Two channels write FINAL_PREGAME rows for the same MLB game: a V1-strict owner
    that writes NO_BET with NO probabilities in evidence, and a V2 owner that
    writes rows WITH them. "The latest row" therefore picks the wrong one roughly
    half the time, and "has a probability somewhere" is not enough either -- a V1
    row that DID find a bet also carries a ``model_probability`` column.

    So the channel is identified by ``experiment_id``, which is what a channel
    actually is, and channels are preferred in a stated order: evidence-bearing
    first, then column-bearing (the NFL research channel retains no evidence
    probabilities at all). Within ONE channel the latest ``recommended_at`` is the
    current row. Two DIFFERENT channels bearing probabilities at the same tier is
    genuine ambiguity: disposition 8 refuses it and NEVER breaks the tie by
    recency.
    """

    rows = session.scalars(
        select(ShadowExperimentDecision)
        .where(ShadowExperimentDecision.game_id == uuid.UUID(game_id),
               ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME)
        .order_by(ShadowExperimentDecision.recommended_at, ShadowExperimentDecision.id)
    ).all()
    visible = [row for row in rows if _at_or_before(row.recommended_at, checkpoint)]
    if not visible:
        return None, ("NO_FINAL_PREGAME_DECISION_AT_CHECKPOINT" if not rows
                      else "ALL_FINAL_PREGAME_DECISIONS_AFTER_CHECKPOINT")
    for tier in (_evidence_bearing, _column_bearing):
        bearing = [row for row in visible if tier(row)]
        if not bearing:
            continue
        if len({row.experiment_id for row in bearing}) > 1:
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


# ---------------------------------------------------------------------------
# Deterministic, model-independent book selection
# ---------------------------------------------------------------------------
def _moneyline_market(session: Session, game_id: str) -> Market | None:
    return session.scalars(
        select(Market)
        .where(Market.game_id == uuid.UUID(game_id), Market.market_type == MarketType.MONEYLINE)
        .order_by(Market.id)
    ).first()


def _sides_for_market(session: Session, market_id: Any, game: GameRow) -> dict[str, MarketOutcome]:
    """Map HOME/AWAY to the market's two outcomes, by team id then by label."""

    rows = session.scalars(select(MarketOutcome).where(MarketOutcome.market_id == market_id)).all()
    mapped: dict[str, MarketOutcome] = {}
    for row in sorted(rows, key=lambda item: str(item.id)):
        side = None
        participant = str(row.participant_team_id) if row.participant_team_id else None
        if participant and participant == game.home_team_id:
            side = "HOME"
        elif participant and participant == game.away_team_id:
            side = "AWAY"
        else:
            text_ = f"{row.outcome_key or ''} {row.label or ''}".lower()
            side = "HOME" if "home" in text_ else ("AWAY" if "away" in text_ else None)
        if side and side not in mapped:
            mapped[side] = row
    return mapped


def _leg(session: Session, snapshot: OddsSnapshot, side: str, book_key: str) -> Leg:
    return Leg(
        side=side, snapshot_id=str(snapshot.id),
        market_outcome_id=str(snapshot.market_outcome_id),
        american_odds=snapshot.american_odds,
        decimal_odds=pilot.american_to_decimal_exact(snapshot.american_odds),
        observed_at=pilot.aware(snapshot.observed_at),
        receipt_at=pilot.aware(snapshot.available_as_of),
        source=snapshot.source, source_mode=snapshot.source_mode,
        source_event_id=snapshot.source_event_id,
        payload_checksum=snapshot.payload_checksum)


def _fetch_identity(leg: Leg) -> tuple[Any, ...]:
    """The exact-fetch identity of one leg.

    ``odds_snapshots`` retains no digest of the provider RESPONSE both legs came
    from -- ``payload_checksum`` is a PER-ROW checksum, not a response identity --
    so the pair's provenance is the exact fetch that produced both rows:
    provider, provider event id, source mode and the collector's availability
    clock. Two legs that do not share all four are not two halves of one quote.
    """

    return (leg.source, leg.source_event_id, leg.source_mode, pilot.iso(leg.receipt_at))


def eligible_book_candidates(session: Session, game: GameRow, checkpoint: datetime
                             ) -> list[BookCandidate] | None:
    """EVERY book's same-book pair at the checkpoint, ordered by the frozen rule.

    The ordering is ``(older_leg_age_seconds, sportsbook_key)`` ascending, applied
    to the eligible candidates. Nothing here reads the decision: not its
    ``sportsbook``, not its ``odds_snapshot_id``, not its selection. That is what
    makes the selection model-independent, and a test pins it by moving the
    decision's named book and asserting the selection does not move.

    Every book that is NOT eligible is still returned, carrying its reason codes,
    so a reader can re-derive the ordering from the record without the database.

    ``None`` means the game has no two-sided moneyline market at all, which is a
    different fact from "a market exists and no book offered a usable pair by the
    checkpoint" -- the empty list. The two carry different reason codes.
    """

    market = _moneyline_market(session, game.game_id)
    if market is None:
        return None
    sides = _sides_for_market(session, market.id, game)
    if "HOME" not in sides or "AWAY" not in sides:
        return None

    books: dict[str, BookCandidate] = {}
    for side, outcome in (("HOME", sides["HOME"]), ("AWAY", sides["AWAY"])):
        rows = session.scalars(
            select(OddsSnapshot)
            .where(OddsSnapshot.market_outcome_id == outcome.id,
                   OddsSnapshot.available_as_of <= checkpoint)
            .order_by(OddsSnapshot.observed_at.desc(), OddsSnapshot.available_as_of.desc(),
                      OddsSnapshot.id.desc())
        ).all()
        for snapshot in rows:
            book = session.get(Sportsbook, snapshot.sportsbook_id)
            key = str(book.key) if book is not None else f"sportsbook:{snapshot.sportsbook_id}"
            candidate = books.setdefault(
                key, BookCandidate(sportsbook_key=key,
                                   sportsbook_name=str(book.name) if book is not None else None))
            if getattr(candidate, side.lower()) is None:
                # The rows are already ordered newest-observed first, so the first
                # one this book contributes for this side is the latest quote that
                # was knowable at the checkpoint -- never the latest quote overall.
                setattr(candidate, side.lower(), _leg(session, snapshot, side, key))

    ordered: list[BookCandidate] = []
    for key in sorted(books):
        candidate = books[key]
        _score_candidate(candidate, checkpoint)
        ordered.append(candidate)
    eligible = sorted((row for row in ordered if row.eligible), key=BookCandidate.sort_key)
    rejected = sorted((row for row in ordered if not row.eligible),
                      key=lambda row: row.sportsbook_key)
    return eligible + rejected


def _score_candidate(candidate: BookCandidate, checkpoint: datetime) -> None:
    """Apply the clock and completeness gates to one book, in a fixed order."""

    legs = {"HOME": candidate.home, "AWAY": candidate.away}
    missing = [side for side, leg in legs.items() if leg is None]
    if missing:
        candidate.reason_codes.append("BOOK_LEG_MISSING_AT_CHECKPOINT")
        return
    ages: dict[str, float] = {}
    for side, leg in legs.items():
        assert leg is not None
        if leg.decimal_odds is None:
            candidate.reason_codes.append("BOOK_LEG_PRICE_NOT_VALID_AMERICAN_ODDS")
        age = _age_seconds(leg.observed_at, checkpoint)
        if age is None or age < 0:
            candidate.reason_codes.append("BOOK_LEG_OBSERVED_AFTER_CHECKPOINT")
            continue
        if age > MAX_QUOTE_AGE_SECONDS:
            candidate.reason_codes.append("BOOK_LEG_QUOTE_AGE_OVER_FIFTEEN_MINUTES")
        ages[side] = age
    home, away = legs["HOME"], legs["AWAY"]
    assert home is not None and away is not None
    if home.source != away.source:
        candidate.reason_codes.append("BOOK_LEGS_FROM_DIFFERENT_PROVIDER")
    elif _fetch_identity(home) != _fetch_identity(away):
        candidate.reason_codes.append("BOOK_LEGS_FROM_DIFFERENT_FETCH_IDENTITY")
    candidate.reason_codes = sorted(set(candidate.reason_codes))
    if ages:
        older = max(ages, key=lambda side: ages[side])
        candidate.older_leg_age_seconds = ages[older]
        candidate.older_leg_side = older


# ---------------------------------------------------------------------------
# Conventions: missing semantics yield UNPRICED, never a guess.
# ---------------------------------------------------------------------------
def conventions_for(sport: str, sportsbook_key: str | None,
                    provider: str | None = None) -> dict[str, Any]:
    """The bound conventions for this sport and SPORTSBOOK, or NOT_ESTABLISHED.

    Looked up by the selected pair's own sportsbook key, never by the feed that
    relayed it -- see :data:`BOUND_CONVENTIONS`. An unbound book is UNPRICED even
    when another book on the same feed is bound.
    """

    required = REQUIRED_CONVENTIONS.get(sport.upper(), ())
    bound = BOUND_CONVENTIONS.get((sport.upper(), str(sportsbook_key or "")), {})
    block: dict[str, Any] = {
        "source": ("BOUND_CONVENTION_REGISTRY" if bound else "NOT_ESTABLISHED"),
        "sportsbookKey": sportsbook_key,
        "provider": provider,
        "required": list(required),
        "tieTreatment": ("STAKE_REFUNDED_PUSH_OUTSIDE_BINARY_DENOMINATOR" if sport.upper() == "NFL"
                         else "MLB_WHOLE_GAME_MONEYLINE_HAS_NO_TIE"),
        "basis": ("Each rule below is the provider's own stated settlement semantics as retained. "
                  "An unstated rule is NOT_ESTABLISHED and makes the record UNPRICED; nothing is "
                  "inferred from AISP's ingest rules, which are admission rules, not settlement."),
    }
    missing: list[str] = []
    for name in required:
        value = bound.get(name)
        block[name] = value if value is not None else "NOT_ESTABLISHED"
        if value is None:
            missing.append(_CONVENTION_REASON[name])
    block["unestablished"] = sorted(missing)
    if bound:
        # Everything the document states beyond the required minimum, and the
        # citation itself, travel with the record so a reader can check the
        # binding against the retained bytes without asking us.
        for name in ("postponementRule", "suspensionRule", "resultSource"):
            if bound.get(name) is not None:
                block[name] = bound[name]
        block["document"] = dict(bound.get("document") or {})
        block["verbatim"] = dict(bound.get("verbatim") or {})
    return block


# ---------------------------------------------------------------------------
# Probability channels
# ---------------------------------------------------------------------------
def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def published_channels(sport: str, forecast: ShadowForecast) -> tuple[dict[str, Any], str | None]:
    """Home, away and tie EXACTLY as published, plus the NFL conditional channel.

    Returns ``(channels, refusal_code)``. MLB's tie is a stated zero, not a
    residual: an MLB pair whose published home and away do not sum to one is a
    contract mismatch and is EXCLUDED rather than normalised.
    """

    home = _float(forecast.full_home_win_probability)
    away = _float(forecast.full_away_win_probability)
    if home is None or away is None:
        return {}, "PUBLISHED_PROBABILITIES_DO_NOT_SUM_TO_ONE"
    if sport == "MLB":
        if abs(home + away - 1.0) > _PROBABILITY_TOLERANCE:
            return {}, "PUBLISHED_PROBABILITIES_DO_NOT_SUM_TO_ONE"
        tie, tie_basis = 0.0, "MLB_WHOLE_GAME_MONEYLINE_HAS_NO_TIE_STATED_ZERO"
        decisive, decisive_basis = None, (
            "MLB_TIE_PROBABILITY_IS_ZERO_SO_THERE_IS_NO_SEPARATE_DECISIVE_CHANNEL")
    else:
        residual = 1.0 - home - away
        if residual < -_PROBABILITY_TOLERANCE:
            return {}, "PUBLISHED_PROBABILITIES_DO_NOT_SUM_TO_ONE"
        tie = max(0.0, residual)
        tie_basis = "PUBLISHED_RESIDUAL_ONE_MINUS_HOME_MINUS_AWAY"
        denominator = home + away
        if denominator <= 0:
            return {}, "PUBLISHED_PROBABILITIES_DO_NOT_SUM_TO_ONE"
        decisive = home / denominator
        decisive_basis = "FROZEN_PROTOCOL_ARM_A_P_HOME_OVER_P_HOME_PLUS_P_AWAY"
    return {
        "homeWinProbabilityRaw": home,
        "awayWinProbabilityRaw": away,
        "tieProbabilityRaw": tie,
        "tieProbabilityBasis": tie_basis,
        "decisiveConditionedHomeProbability": decisive,
        "decisiveConditionedBasis": decisive_basis,
    }, None


def _selected_side(decision: ShadowExperimentDecision, game: GameRow) -> tuple[str | None, str]:
    """Which side the decision took -- METADATA only. It never selects a book."""

    stated = str(decision.selection or "").strip().upper()
    if stated in ("HOME", "AWAY"):
        return stated, "DECISION_SELECTION_LITERAL"
    token = stated.split()[0] if stated.split() else ""
    if token and token == str(game.home_abbreviation or "").upper():
        return "HOME", "DECISION_SELECTION_TEAM_ABBREVIATION"
    if token and token == str(game.away_abbreviation or "").upper():
        return "AWAY", "DECISION_SELECTION_TEAM_ABBREVIATION"
    return None, "NOT_RESOLVABLE"


def oriented_channels(decision: ShadowExperimentDecision, game: GameRow) -> dict[str, Any]:
    """Adjusted and controlling channels, WITHOUT substitution.

    Both are retained for the SELECTED side. Orienting them to home by taking a
    complement would invent a number: a policy adjustment is not a complement, and
    ``1 - adjusted(away)`` is a quantity the decision never recorded. So each is
    home-oriented ONLY when the decision selected home; otherwise it is null with
    a basis, and the selected-side value is carried additionally.
    """

    evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
    side, side_basis = _selected_side(decision, game)
    adjusted = _float(evidence.get("adjusted_probability"))
    controlling = _float(decision.model_probability)
    raw_selected = _float(evidence.get("raw_model_probability"))
    retained_market = _float(evidence.get("market_probability"))

    def orient(value: float | None, name: str) -> tuple[float | None, str]:
        if value is None:
            return None, f"{name}_NOT_RETAINED_BY_THIS_DECISION_CHANNEL"
        if side == "HOME":
            return value, f"{name}_RETAINED_AND_ALREADY_HOME_ORIENTED"
        if side == "AWAY":
            return None, (f"{name}_RETAINED_FOR_THE_AWAY_SELECTION_ONLY; a home value would be a "
                          "complement the decision never recorded and is not substituted")
        return None, f"{name}_SELECTED_SIDE_NOT_RESOLVABLE"

    adjusted_home, adjusted_basis = orient(adjusted, "ADJUSTED")
    controlling_home, controlling_basis = orient(controlling, "CONTROLLING")
    return {
        "homeWinProbabilityAdjusted": adjusted_home,
        "homeWinProbabilityAdjustedBasis": adjusted_basis,
        "homeWinProbabilityControlling": controlling_home,
        "homeWinProbabilityControllingBasis": controlling_basis,
        "selectedSideAdditional": {
            "selectedSide": side, "selectedSideBasis": side_basis,
            "rawModelProbability": raw_selected,
            "adjustedProbability": adjusted,
            "controllingProbability": controlling,
            "retainedMarketProbability": retained_market,
            "note": CHANNEL_MEANINGS["selectedSideAdditional"],
        },
    }


def no_vig(candidate: BookCandidate) -> dict[str, Any] | None:
    """Two-way same-book no-vig HOME probability, with every term shown."""

    if candidate.home is None or candidate.away is None:
        return None
    d_home, d_away = candidate.home.decimal_odds, candidate.away.decimal_odds
    if d_home is None or d_away is None:
        return None
    q_home, overround = pilot.no_vig_home(d_home, d_away)
    return {
        "formula": ("rHome = 1/dHome; rAway = 1/dAway; qHome = rHome/(rHome+rAway); "
                    "overround = rHome + rAway - 1"),
        "basis": CHANNEL_MEANINGS["marketImpliedNoVig"],
        "homeDecimalOdds": float(d_home), "awayDecimalOdds": float(d_away),
        "homeImpliedProbability": float(1 / d_home), "awayImpliedProbability": float(1 / d_away),
        "overround": float(overround),
        "homeNoVigProbability": float(q_home), "awayNoVigProbability": float(1 - q_home),
    }


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------
def outcome_for(session: Session, game: GameRow) -> dict[str, Any] | None:
    """The latest supported terminal revision, or ``None`` when there is none.

    NFL reads ``nfl_final_observations``, which IS the NFL source of record --
    ``game_final_outcomes`` is MLB-only and is never touched here. Everything else
    reads the sport-neutral ``game_state_observations``.

    Source-confirmed terminal STATUS and genuine COMPLETION TIME are separate
    fields, and a null completion time keeps :data:`COMPLETION_TIME_UNAVAILABLE`
    rather than borrowing a retrieval clock (disposition 4).
    """

    return _nfl_outcome(session, game) if game.sport == "NFL" else _state_outcome(session, game)


def outcome_revisions(session: Session, game: GameRow) -> list[dict[str, Any]]:
    """Every supported terminal revision, oldest first.

    A later correction APPENDS to this list. The canonical pregame observation is
    never rewritten: each revision is written to its own content-addressed file in
    :data:`OUTCOMES_DIR` naming the ``observationId`` it attaches to.
    """

    if game.sport == "NFL":
        rows = session.scalars(
            select(NflFinalObservation)
            .where(NflFinalObservation.game_id == uuid.UUID(game.game_id))
            .order_by(NflFinalObservation.retrieved_at, NflFinalObservation.id)
        ).all()
        return [_nfl_block(row) for row in rows
                if str(row.status or "").upper() in _TERMINAL_NFL_STATUS]
    rows = session.scalars(
        select(GameStateObservation)
        .where(GameStateObservation.game_id == uuid.UUID(game.game_id))
        .order_by(GameStateObservation.available_as_of, GameStateObservation.id)
    ).all()
    return [_state_block(row) for row in rows
            if str(row.status or "").upper().replace(" ", "_") in _TERMINAL_STATE_STATUS]


def _nfl_outcome(session: Session, game: GameRow) -> dict[str, Any] | None:
    revisions = outcome_revisions(session, game)
    return revisions[-1] if revisions else None


def _state_outcome(session: Session, game: GameRow) -> dict[str, Any] | None:
    revisions = outcome_revisions(session, game)
    return revisions[-1] if revisions else None


def _nfl_block(row: NflFinalObservation) -> dict[str, Any]:
    return {
        "source": "nfl_final_observations", "sourceMode": row.source_mode,
        "sourceName": row.source, "sourceEventId": row.source_event_id,
        "revisionIdentity": row.payload_checksum,
        "revisionIdentityBasis": ("nfl_final_observations.payload_checksum -- a checksum of the "
                                  "RESULT FACTS only, never the retrieval time and never the feed's "
                                  "own last-updated stamp, so it is the result identity"),
        "observationRowId": str(row.id),
        "status": str(row.status), "resultTier": row.result_tier,
        "homeScore": row.home_score, "awayScore": row.away_score,
        "overtime": row.overtime, "week": row.week, "gameType": row.game_type, "season": row.season,
        "sourceCaptureAtUtc": pilot.iso(row.retrieved_at),
        "sourceAdmissionAtUtc": pilot.iso(row.created_at),
        "sourceAdmissionBasis": ("nfl_final_observations.created_at is the ADMISSION transaction "
                                 "start, carried only as an admission bound and never as a clock "
                                 "for anything else"),
        "sourceAvailabilityAtUtc": pilot.iso(row.retrieved_at),
        # The NFL source of record has NO completion-time column. Nothing here is
        # promoted into one -- not retrieved_at, not source_last_updated.
        "genuineCompletionAtUtc": None,
        "genuineCompletionBasis": COMPLETION_TIME_UNAVAILABLE,
        "sourceLastUpdatedRaw": row.source_last_updated,
        "sourceLastUpdatedBasis": CLOCK_MEANINGS["sourceLastUpdatedRaw"],
        "feedChecksum": row.feed_checksum,
        "tieDisposition": ("An official NFL tie is a REFUND. It is counted separately and stays "
                           "outside the binary proper-score denominator."),
        "isTie": (None if row.home_score is None or row.away_score is None
                  else row.home_score == row.away_score),
    }


def _state_block(row: GameStateObservation) -> dict[str, Any]:
    final_at = pilot.iso(row.final_at)
    return {
        "source": "game_state_observations", "sourceMode": row.source_mode,
        "sourceName": row.source, "sourceEventId": getattr(row, "source_event_id", None),
        "revisionIdentity": row.payload_checksum,
        "revisionIdentityBasis": "game_state_observations.payload_checksum",
        "observationRowId": str(row.id),
        "status": str(row.status), "resultTier": None,
        "homeScore": row.home_score, "awayScore": row.away_score,
        "overtime": None, "week": None, "gameType": None, "season": None,
        "sourceCaptureAtUtc": pilot.iso(row.available_as_of),
        "sourceAdmissionAtUtc": pilot.iso(row.created_at),
        "sourceAdmissionBasis": ("game_state_observations.created_at is the ADMISSION transaction "
                                 "start, carried only as an admission bound"),
        "sourceAvailabilityAtUtc": pilot.iso(row.available_as_of),
        "genuineCompletionAtUtc": final_at,
        "genuineCompletionBasis": ("SOURCE_SUPPLIED_COMPLETION_TIME" if final_at
                                   else COMPLETION_TIME_UNAVAILABLE),
        "sourceLastUpdatedRaw": None,
        "sourceLastUpdatedBasis": CLOCK_MEANINGS["sourceLastUpdatedRaw"],
        "feedChecksum": None,
        "tieDisposition": ("MLB whole-game including extra innings has no tie; a VOID or NO_CONTEST "
                           "status is excluded and reported, never scored."),
        "isTie": (None if row.home_score is None or row.away_score is None
                  else row.home_score == row.away_score),
    }


# ---------------------------------------------------------------------------
# Evaluating one game
# ---------------------------------------------------------------------------
def evaluate_game(session: Session, game: GameRow) -> Observation | None:
    """Everything one game contributes at its fixed T-15 checkpoint. Reads only."""

    checkpoint = checkpoint_for(game.starts_at)
    if checkpoint is None:
        placeholder = Observation(game=game, checkpoint=datetime(1970, 1, 1, tzinfo=UTC))
        placeholder.refuse("SCHEDULED_START_MISSING", "")
        return placeholder
    observed = Observation(game=game, checkpoint=checkpoint)

    # Post-freeze evidence only. Enforced, not asserted.
    if checkpoint < FREEZE_INSTANT:
        observed.refuse("PRE_FREEZE_EVIDENCE_NEVER_ENROLLED",
                        f"checkpoint={pilot.iso(checkpoint)} freeze={FREEZE_INSTANT_UTC}")
    if checkpoint >= ENROLLMENT_END:
        observed.refuse("CHECKPOINT_AFTER_ENROLLMENT_END", pilot.iso(checkpoint) or "")
    if game.home_team_id is None or game.away_team_id is None:
        observed.refuse("GAME_ORIENTATION_NOT_RESOLVABLE", "")

    # The market side is model-independent, so it is read whatever the decision
    # says -- including for an EXCLUDED game, whose candidate list still documents
    # what the checkpoint could see.
    found = eligible_book_candidates(session, game, checkpoint)
    observed.candidates = found or []
    if found is None:
        observed.refuse("NO_MONEYLINE_MARKET_FOR_GAME", "")
    elif not found:
        observed.refuse("NO_COMPLETE_SAME_BOOK_PAIR_AT_CHECKPOINT",
                        "a moneyline market exists but no book had any quote visible at the "
                        "checkpoint")
    else:
        eligible = [row for row in observed.candidates if row.eligible]
        if not eligible:
            observed.refuse("NO_COMPLETE_SAME_BOOK_PAIR_AT_CHECKPOINT",
                            f"{len(observed.candidates)} book(s) examined")
        else:
            observed.selected = eligible[0]
            provider = observed.selected.home.source if observed.selected.home else None
            book = observed.selected.sportsbook_key
            for code in conventions_for(game.sport, book, provider)["unestablished"]:
                observed.refuse(code, f"sportsbook={book} provider={provider}")

    decision, why = deciding_row(session, game.game_id, checkpoint)
    if decision is None:
        observed.refuse(why or "NO_PROBABILITY_BEARING_DECISION_CHANNEL", "")
        return observed
    observed.decision = decision
    observed.position = position_for(session, decision)

    forecast = None
    if decision.shadow_forecast_id is not None:
        forecast = session.get(ShadowForecast, decision.shadow_forecast_id)
        if forecast is None:
            observed.refuse("CONTROLLING_FORECAST_ROW_MISSING", str(decision.shadow_forecast_id))
    else:
        observed.refuse("NO_CONTROLLING_FORECAST_NAMED",
                        "the decision names no shadow_forecasts row; forecasts.forecast_id is a "
                        "different store and is not substituted")
    if forecast is None:
        return observed
    observed.forecast = forecast

    if str(forecast.snapshot_type or "").upper() != "FINAL_PREGAME":
        observed.refuse("FORECAST_SNAPSHOT_TYPE_IS_NOT_FINAL_PREGAME",
                        f"snapshot_type={forecast.snapshot_type}; CURRENT is never substituted")
    stated = decision.controlling_forecast_identity
    if stated and forecast.forecast_id and stated != forecast.forecast_id:
        observed.refuse("CONTROLLING_IDENTITY_DISAGREES_WITH_FORECAST_ROW",
                        f"stated={stated} row={forecast.forecast_id}")
    if forecast.available_as_of_utc is None:
        observed.refuse("FORECAST_EXPOSURE_CLOCK_MISSING", "")
    elif not _at_or_before(forecast.available_as_of_utc, checkpoint):
        observed.refuse("FORECAST_EXPOSED_AFTER_CHECKPOINT",
                        f"exposed={pilot.iso(forecast.available_as_of_utc)}")
    if decision.forecast_imported_at is None:
        observed.refuse("FORECAST_IMPORT_CLOCK_MISSING", "")
    elif not _at_or_before(decision.forecast_imported_at, checkpoint):
        observed.refuse("FORECAST_IMPORTED_AFTER_CHECKPOINT",
                        f"imported={pilot.iso(decision.forecast_imported_at)}")
    if game.sport == "NFL" and not decision.eligibility_supplement_id:
        observed.refuse("NFL_ELIGIBILITY_SUPPLEMENT_MISSING",
                        "the frozen protocol requires the exact FINAL_PREGAME M1 forecast AND its "
                        "matching eligibility supplement")
    _, channel_failure = published_channels(game.sport, forecast)
    if channel_failure:
        observed.refuse(channel_failure,
                        f"home={forecast.full_home_win_probability} "
                        f"away={forecast.full_away_win_probability}")
    return observed


# ---------------------------------------------------------------------------
# Building the record
# ---------------------------------------------------------------------------
def _leg_block(leg: Leg | None, checkpoint: datetime) -> dict[str, Any] | None:
    if leg is None:
        return None
    return {
        "side": leg.side, "oddsSnapshotId": leg.snapshot_id,
        "marketOutcomeId": leg.market_outcome_id,
        "americanOdds": leg.american_odds,
        "decimalOdds": float(leg.decimal_odds) if leg.decimal_odds is not None else None,
        "quoteObservedAtUtc": pilot.iso(leg.observed_at),
        "quoteObservedAtBasis": CLOCK_MEANINGS["quoteObservedAtUtc"],
        "consumerReceiptAtUtc": pilot.iso(leg.receipt_at),
        "consumerReceiptAtBasis": CLOCK_MEANINGS["quoteConsumerReceiptAtUtc"],
        "ageSecondsAtCheckpoint": _age_seconds(leg.observed_at, checkpoint),
        "provider": leg.source, "providerMode": leg.source_mode,
        "providerEventId": leg.source_event_id,
        "rowPayloadChecksum": leg.payload_checksum,
    }


def _candidate_block(candidate: BookCandidate, checkpoint: datetime) -> dict[str, Any]:
    return {
        "sportsbookKey": candidate.sportsbook_key,
        "sportsbookName": candidate.sportsbook_name,
        "eligible": candidate.eligible,
        "olderLegAgeSecondsAtCheckpoint": candidate.older_leg_age_seconds,
        "olderLegSide": candidate.older_leg_side,
        "orderingKey": [candidate.older_leg_age_seconds, candidate.sportsbook_key],
        "reasonCodes": list(candidate.reason_codes),
        "homeLeg": _leg_block(candidate.home, checkpoint),
        "awayLeg": _leg_block(candidate.away, checkpoint),
    }


def _market_block(observed: Observation) -> dict[str, Any]:
    checkpoint = observed.checkpoint
    selected = observed.selected
    home = selected.home if selected else None
    away = selected.away if selected else None
    provider = home.source if home else None
    block: dict[str, Any] = {
        "marketKey": "h2h",
        "marketType": "MONEYLINE",
        "period": "FULL_GAME",
        "sportsbookKey": selected.sportsbook_key if selected else None,
        "sportsbookName": selected.sportsbook_name if selected else None,
        "homeAmericanOdds": home.american_odds if home else None,
        "awayAmericanOdds": away.american_odds if away else None,
        "homeQuoteObservedAtUtc": pilot.iso(home.observed_at) if home else None,
        "awayQuoteObservedAtUtc": pilot.iso(away.observed_at) if away else None,
        "olderLegAgeSecondsAtCheckpoint": selected.older_leg_age_seconds if selected else None,
        "olderLegSide": selected.older_leg_side if selected else None,
        "maxLegAgeSecondsAllowed": MAX_QUOTE_AGE_SECONDS,
        "selectionRule": ("Deterministic and model-independent: among books with a complete "
                          "same-book, same-fetch pair whose legs are each observed at or before the "
                          "checkpoint and at most 900 seconds old there, select "
                          "min((older_leg_age_seconds, sportsbook_key)) ascending. The decision's "
                          "own sportsbook and odds_snapshot_id are never read."),
        "selectedLeg": {"home": _leg_block(home, checkpoint), "away": _leg_block(away, checkpoint)},
        # Disposition: the COMPLETE list, so the ordering is verifiable from the
        # record alone, without the database.
        "eligibleBookCandidates": [_candidate_block(row, checkpoint)
                                   for row in observed.candidates if row.eligible],
        "ineligibleBookCandidates": [_candidate_block(row, checkpoint)
                                     for row in observed.candidates if not row.eligible],
        "booksExamined": len(observed.candidates),
        "commonResponseSha256": None,
        "commonResponseBasis": (
            "NOT RETAINED. odds_snapshots holds no digest of the provider RESPONSE the pair was "
            "read from; payload_checksum is a PER-ROW checksum and is not a response identity. The "
            "pair's provenance is therefore carried as the exact-fetch identity below, and both legs "
            "are required to match on all four of its fields."),
        "exactFetchIdentity": ({
            "provider": home.source, "providerEventId": home.source_event_id,
            "providerMode": home.source_mode,
            "consumerReceiptAtUtc": pilot.iso(home.receipt_at),
            "homeRowPayloadChecksum": home.payload_checksum,
            "awayRowPayloadChecksum": away.payload_checksum if away else None,
        } if home is not None else None),
        "conventions": conventions_for(
            observed.game.sport, selected.sportsbook_key if selected else None, provider),
        "noVig": no_vig(selected) if selected else None,
    }
    return block


def _forecast_block(observed: Observation) -> dict[str, Any] | None:
    forecast, decision = observed.forecast, observed.decision
    if forecast is None or decision is None:
        return None
    channels, failure = published_channels(observed.game.sport, forecast)
    identity = str(forecast.forecast_id or "")
    object_sha = identity.rsplit(":", 1)[-1] if ":" in identity else None
    block: dict[str, Any] = {
        "objectIdentity": identity or None,
        "objectSha256": object_sha if object_sha and len(object_sha) == 64 else None,
        "objectShaBasis": ("shadow_forecasts.forecast_id is content-addressed "
                           "(asp-sports-game-forecast:sha256:<hex>); the hex suffix IS the immutable "
                           "physical reference. Null when the identity is not in that form."),
        "objectRowId": str(forecast.id), "objectRowTable": "shadow_forecasts",
        "statedIdentity": decision.controlling_forecast_identity,
        "modelArtifactSha256": forecast.model_artifact_sha256,
        "engineId": forecast.engine_id,
        "modelVersion": forecast.engine_version,
        "snapshotType": forecast.snapshot_type,
        "snapshotVersion": forecast.snapshot_version,
        "snapshotPhase": decision.forecast_snapshot_phase,
        "eligibilitySupplementId": decision.eligibility_supplement_id,
        "generatedAtUtc": pilot.iso(forecast.forecast_cutoff_utc),
        "exposedAtUtc": pilot.iso(forecast.available_as_of_utc),
        "publishedAtUtc": pilot.iso(decision.forecast_published_at),
        "importedAtOrBeforeUtc": pilot.iso(decision.forecast_imported_at),
        "consumerObservedAtUtc": pilot.iso(decision.recommended_at),
        "channelMeanings": CHANNEL_MEANINGS,
    }
    if failure:
        block |= {"homeWinProbabilityRaw": None, "awayWinProbabilityRaw": None,
                  "tieProbabilityRaw": None, "tieProbabilityBasis": failure,
                  "decisiveConditionedHomeProbability": None,
                  "decisiveConditionedBasis": failure}
    else:
        block |= channels
    block |= oriented_channels(decision, observed.game)
    return block


def _decision_block(observed: Observation) -> dict[str, Any] | None:
    decision, position = observed.decision, observed.position
    if decision is None:
        return None
    return {
        "decisionId": str(decision.id),
        "decisionChannelExperimentId": str(decision.experiment_id),
        "decisionChannelBasis": ("the FINAL_PREGAME channel selected explicitly as the "
                                 "probability-bearing one, by experiment_id; never 'the latest row'"),
        "recommendedAtUtc": pilot.iso(decision.recommended_at),
        "recommendedAtBasis": CLOCK_MEANINGS["recommendedAtUtc"],
        "recommendation": decision.recommendation,
        "recommendationReason": decision.recommendation_reason,
        "qualificationOutcome": decision.qualification_outcome,
        "qualificationReason": decision.qualification_reason,
        "executionOutcome": decision.execution_outcome,
        "executionReason": decision.execution_reason,
        "funded": position is not None,
        # Disposition 3: JSON null, never a sentinel string.
        "positionId": str(position.id) if position is not None else None,
        "positionStatus": str(position.status) if position is not None else None,
        "positionResult": str(position.result) if position is not None else None,
        "declinedUnfundedNote": ("Declines and unfunded decisions REMAIN in the population. "
                                 "qualificationOutcome, executionOutcome and positionId answer three "
                                 "different questions and are never collapsed: a QUALIFIED game may "
                                 "be NOT_EXECUTED and carry a null positionId, and that is a real, "
                                 "capturable row."),
        "namedSportsbookNotUsedForSelection": decision.sportsbook,
        "namedOddsSnapshotNotUsedForSelection": (str(decision.odds_snapshot_id)
                                                 if decision.odds_snapshot_id else None),
    }


def _source_event_aliases(observed: Observation) -> dict[str, Any]:
    forecast, selected = observed.forecast, observed.selected
    return {
        "forecastEventId": forecast.event_id if forecast else None,
        "forecastSourceEventId": forecast.source_event_id if forecast else None,
        "marketProviderEventId": (selected.home.source_event_id
                                  if selected and selected.home else None),
        "canonicalGameId": observed.game.game_id,
        "homeAbbreviation": observed.game.home_abbreviation,
        "awayAbbreviation": observed.game.away_abbreviation,
    }


def build_observation(session: Session, observed: Observation, *,
                      status: str = STATUS_PROSPECTIVE) -> dict[str, Any]:
    """The ONE canonical record for this game and checkpoint, whatever its state.

    Disposition 2: an exclusion is not a second competing file. A game that cannot
    be priced or admitted gets THIS record with ``admission.state`` set and its
    immutable reason codes, carrying the same identity and clock envelope.
    """

    game = observed.game
    latest = outcome_for(session, game)
    record = {
        "contract": CONTRACT,
        "contractSha256": CONTRACT_SHA256,
        "protocolSha256": PROTOCOL_SHA256,
        "status": status,
        "observationId": observation_id(game.sport, game.game_id, observed.checkpoint),
        "sport": game.sport,
        "gameId": game.game_id,
        "sourceEventAliases": _source_event_aliases(observed),
        "scheduledStartUtc": pilot.iso(game.starts_at),
        "checkpointAtUtc": pilot.iso(observed.checkpoint),
        "checkpointBasis": "SCHEDULED_START_MINUS_15_MINUTES",
        "forecast": _forecast_block(observed),
        "market": _market_block(observed),
        "decision": _decision_block(observed),
        "admission": {
            "state": observed.state,
            "reasonCodes": sorted(set(observed.reason_codes)),
            "reasonDetail": dict(sorted(observed.reason_detail.items())),
            "states": list(ADMISSION_STATES),
            "immutableReasonCodes": True,
        },
        "outcome": latest,
        "evidenceWindow": {
            "freezeInstantUtc": FREEZE_INSTANT_UTC,
            "enrollmentEndUtc": pilot.iso(ENROLLMENT_END),
            "checkpointIsAfterFreeze": observed.checkpoint >= FREEZE_INSTANT,
            "rule": ("No pre-freeze game may be prospective evidence. A checkpoint before the freeze "
                     "instant is EXCLUDED with PRE_FREEZE_EVIDENCE_NEVER_ENROLLED, so the first "
                     "genuine record rests on post-freeze evidence by construction."),
            "outcomeRevisionRule": ("A later outcome correction APPENDS a new revision reference in "
                                    f"'{OUTCOMES_DIR}/<gameId>/<sha256>.json' naming this "
                                    "observationId. The pregame observation is never rewritten."),
        },
        "exporter": {
            "identity": EXPORT_IDENTITY,
            "module": "app.research_exports.prospective_capture",
            "readOnlyProof": READ_ONLY_PROOF,
            "oneCanonicalRecordPerGameAndCheckpoint": (
                "observationId = sha256(contract|sport|gameId|checkpointAtUtc). The writer admits "
                "exactly one canonical record per observationId; a repeated tick recomputes the same "
                "id and the same bytes and adopts the existing file."),
        },
        "clockMeanings": CLOCK_MEANINGS,
    }
    if tuple(record) != RECORD_FIELDS:  # pragma: no cover - structural guard
        raise CaptureRefused("RECORD_FIELDS_MISMATCH", str(tuple(record)))
    assert_no_not_applicable_sentinel(record)
    assert_no_derived_comparison(record)
    assert_no_money(record)
    return record


def build_outcome_revision(record: dict[str, Any], revision: dict[str, Any], ordinal: int
                           ) -> dict[str, Any]:
    """One appended outcome revision reference. It never rewrites the observation."""

    built = {
        "contract": OUTCOME_REVISION_CONTRACT,
        "contractSha256": CONTRACT_SHA256,
        "status": record["status"],
        "observationId": record["observationId"],
        "sport": record["sport"],
        "gameId": record["gameId"],
        "checkpointAtUtc": record["checkpointAtUtc"],
        "revisionOrdinal": ordinal,
        "outcome": revision,
        "exporter": {"identity": EXPORT_IDENTITY,
                     "rule": ("Append-only. A correction adds the next ordinal; nothing already "
                              "written is edited, and the pregame observation is untouched.")},
    }
    if tuple(built) != OUTCOME_REVISION_FIELDS:  # pragma: no cover - structural guard
        raise CaptureRefused("OUTCOME_REVISION_FIELDS_MISMATCH", str(tuple(built)))
    assert_no_not_applicable_sentinel(built)
    return built


# ---------------------------------------------------------------------------
# Validating a record against the contract
# ---------------------------------------------------------------------------
def validate_record(record: Any) -> list[str]:
    """Check one record against the frozen contract. Returns the failures.

    This is the same check the standalone validator runs, kept in the module so
    the exporter and the validator cannot drift.
    """

    failures: list[str] = []
    if not isinstance(record, dict):
        return ["record is not a JSON object"]
    # Key ORDER is not checked here: a record read back from its canonical bytes
    # has sorted keys by construction. The builder's own ordered guard covers the
    # writing side; the contract only fixes which fields exist.
    if set(record) != set(RECORD_FIELDS):
        missing = sorted(set(RECORD_FIELDS) - set(record))
        extra = sorted(set(record) - set(RECORD_FIELDS))
        failures.append(f"top-level fields differ: missing={missing} unexpected={extra}")
    if record.get("contract") != CONTRACT:
        failures.append(f"contract is {record.get('contract')!r}, expected {CONTRACT!r}")
    if record.get("contractSha256") != CONTRACT_SHA256:
        failures.append("contractSha256 does not bind the frozen contract file")
    if record.get("protocolSha256") != PROTOCOL_SHA256:
        failures.append("protocolSha256 does not bind the frozen protocol file")
    if record.get("status") not in (STATUS_PROSPECTIVE, STATUS_SYNTHETIC):
        failures.append(f"status {record.get('status')!r} is not a permitted status")
    if record.get("sport") not in SPORTS:
        failures.append(f"sport {record.get('sport')!r} is not MLB or NFL")

    start = pilot.aware(record.get("scheduledStartUtc"))
    checkpoint = pilot.aware(record.get("checkpointAtUtc"))
    if start is None or checkpoint is None:
        failures.append("scheduledStartUtc and checkpointAtUtc must both be present")
    elif start - checkpoint != timedelta(minutes=CHECKPOINT_MINUTES_BEFORE_START):
        failures.append("checkpointAtUtc is not scheduled start minus 15 minutes")
    if record.get("checkpointBasis") != "SCHEDULED_START_MINUS_15_MINUTES":
        failures.append("checkpointBasis must state the fixed T-15 rule")
    if checkpoint is not None and record.get("gameId") and record.get("sport") in SPORTS:
        expected = observation_id(str(record["sport"]), str(record["gameId"]), checkpoint)
        if record.get("observationId") != expected:
            failures.append("observationId is not the canonical sport/game/checkpoint key")

    admission = record.get("admission")
    if not isinstance(admission, dict):
        failures.append("admission block is missing")
    else:
        state = admission.get("state")
        if state not in ADMISSION_STATES:
            failures.append(f"admission.state {state!r} is not one of {ADMISSION_STATES}")
        codes = admission.get("reasonCodes")
        if not isinstance(codes, list):
            failures.append("admission.reasonCodes must be a list")
        else:
            for code in codes:
                if code not in REASON_CODES:
                    failures.append(f"admission reason code {code!r} is not an immutable code")
            if state == ADMISSION_PRICED and codes:
                failures.append("PRICED_ELIGIBLE must carry no reason codes")
            if state != ADMISSION_PRICED and not codes:
                failures.append(f"{state} must carry at least one reason code")

    decision = record.get("decision")
    if isinstance(decision, dict):
        if "positionId" not in decision:
            failures.append("decision.positionId must be present")
        elif decision["positionId"] is not None and not isinstance(decision["positionId"], str):
            failures.append("decision.positionId must be a string or JSON null")
        if not decision.get("recommendedAtUtc"):
            failures.append("decision.recommendedAtUtc must be present and separate from the checkpoint")

    forecast = record.get("forecast")
    if isinstance(forecast, dict):
        for key in ("homeWinProbabilityRaw", "awayWinProbabilityRaw", "tieProbabilityRaw"):
            if key not in forecast:
                failures.append(f"forecast.{key} must be present")
        if record.get("sport") == "MLB" and forecast.get("tieProbabilityRaw") not in (0.0, None):
            failures.append("MLB tieProbabilityRaw must be 0.0")
        if record.get("sport") == "NFL" and "decisiveConditionedHomeProbability" not in forecast:
            failures.append("NFL must carry a separately named decisiveConditionedHomeProbability")
        if forecast.get("snapshotType") not in (None, "FINAL_PREGAME"):
            failures.append("forecast.snapshotType must be FINAL_PREGAME; CURRENT is never substituted")
        exposed = pilot.aware(forecast.get("exposedAtUtc"))
        imported = pilot.aware(forecast.get("importedAtOrBeforeUtc"))
        if checkpoint is not None and record.get("admission", {}).get("state") == ADMISSION_PRICED:
            if exposed is None or exposed > checkpoint:
                failures.append("forecast exposure must occur at or before the checkpoint")
            if imported is None or imported > checkpoint:
                failures.append("forecast import must occur at or before the checkpoint")
    elif forecast is not None:
        failures.append("forecast must be an object or null")

    market = record.get("market")
    if not isinstance(market, dict):
        failures.append("market block is missing")
    else:
        for key in ("eligibleBookCandidates", "ineligibleBookCandidates"):
            if not isinstance(market.get(key), list):
                failures.append(f"market.{key} must be a list")
        eligible = market.get("eligibleBookCandidates") or []
        keys = [(row.get("olderLegAgeSecondsAtCheckpoint"), row.get("sportsbookKey"))
                for row in eligible if isinstance(row, dict)]
        if keys != sorted(keys, key=lambda item: (float(item[0] or 0.0), str(item[1]))):
            failures.append("eligibleBookCandidates are not in (older_leg_age_seconds, "
                            "sportsbook_key) ascending order")
        if eligible and market.get("sportsbookKey") != eligible[0].get("sportsbookKey"):
            failures.append("the selected sportsbookKey is not the first eligible candidate")
        if "commonResponseSha256" not in market:
            failures.append("market.commonResponseSha256 must be present (null with a basis is valid)")
        if market.get("commonResponseSha256") is None and not market.get("commonResponseBasis"):
            failures.append("a null commonResponseSha256 must carry a basis")
        for row in eligible:
            if not isinstance(row, dict):
                continue
            age = row.get("olderLegAgeSecondsAtCheckpoint")
            if age is None or float(age) < 0 or float(age) > MAX_QUOTE_AGE_SECONDS:
                failures.append(f"eligible candidate {row.get('sportsbookKey')!r} violates the "
                                "15-minute leg-age bound")
        conventions = market.get("conventions")
        if not isinstance(conventions, dict):
            failures.append("market.conventions must be present")
        elif (record.get("admission", {}).get("state") == ADMISSION_PRICED
              and conventions.get("unestablished")):
            failures.append("PRICED_ELIGIBLE with an unestablished convention: missing semantics "
                            "must yield UNPRICED")

    outcome = record.get("outcome")
    if isinstance(outcome, dict):
        if not outcome.get("revisionIdentity"):
            failures.append("outcome.revisionIdentity must be present")
        if "genuineCompletionAtUtc" not in outcome:
            failures.append("outcome.genuineCompletionAtUtc must be present, nullable")
        if outcome.get("genuineCompletionAtUtc") is None \
                and outcome.get("genuineCompletionBasis") != COMPLETION_TIME_UNAVAILABLE:
            failures.append("a null genuine completion time must carry the accepted basis "
                            f"{COMPLETION_TIME_UNAVAILABLE}")
        for key in ("sourceCaptureAtUtc", "sourceAdmissionAtUtc", "sourceAvailabilityAtUtc",
                    "status", "homeScore", "awayScore"):
            if key not in outcome:
                failures.append(f"outcome.{key} must be present")
        if record.get("sport") == "NFL":
            for key in ("overtime", "week", "gameType"):
                if key not in outcome:
                    failures.append(f"NFL outcome must carry {key}")
    elif outcome is not None:
        failures.append("outcome must be an object or null")

    try:
        assert_no_not_applicable_sentinel(record)
    except CaptureRefused as error:
        failures.append(f"forbidden NOT_APPLICABLE sentinel at {error.detail}")
    try:
        assert_no_derived_comparison(record)
    except CaptureRefused as error:
        failures.append(f"derived comparison field at {error.detail}")
    return failures


# ---------------------------------------------------------------------------
# Writing. Content-addressed, append-only, outside the read transaction.
# ---------------------------------------------------------------------------
def _write_once_same(path: Path, data: bytes) -> bool:
    """Create exactly once. Returns True when this call created the file.

    A path that already holds the SAME bytes is adopted (a crashed run resumes);
    a path that holds DIFFERENT bytes is a refusal. Nothing is ever overwritten,
    and because the filename IS the content hash, "different bytes at the same
    name" means a collision or tampering, not an update.
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


def revision_path(directory: Path, game_id: str, content_sha: str) -> Path:
    return Path(directory) / OUTCOMES_DIR / game_id / f"{content_sha}.json"


def _admitted_observation_ids(ledger: AttemptLedger) -> set[str]:
    """Disposition 2 and 9: exactly one canonical record per game and checkpoint.

    The ledger is the enumeration, so a crashed run recovers idempotently: an
    observationId already admitted is never admitted a second time.
    """

    return {str(entry.get("observationId")) for entry in ledger.entries()
            if entry.get("kind") in ("OBSERVATION_WRITTEN", "OBSERVATION_ALREADY_PRESENT")}


def write_observation(directory: Path, record: dict[str, Any], *, ledger: AttemptLedger,
                      now: datetime, session: Session | None = None,
                      admitted: set[str] | None = None) -> dict[str, Any]:
    _refuse_write_inside_read_transaction(session)
    observation = str(record["observationId"])
    game_id = str(record["gameId"])
    data = pilot.canonical_bytes(record)
    content_sha = _sha(data)
    path = record_path(directory, game_id, content_sha)
    if admitted is not None and observation in admitted and not path.exists():
        # This game and checkpoint ALREADY have their one canonical record, and a
        # later tick has recomputed different bytes -- almost always because an
        # outcome revision landed after the pregame observation was sealed. The
        # pregame observation is NEVER rewritten and a competing second canonical
        # record is never created: the new evidence goes to the append-only
        # revision lane instead, and the skip is recorded so it is not silent.
        ledger.append({
            "kind": "OBSERVATION_ALREADY_ADMITTED_LATER_EVIDENCE_NOT_WRITTEN",
            "exportIdentity": EXPORT_IDENTITY, "contract": CONTRACT,
            "observationId": observation, "gameId": game_id, "sport": record["sport"],
            "checkpointAtUtc": record["checkpointAtUtc"],
            "recomputedSha256": content_sha,
            "rule": ("One canonical record per game and checkpoint. A later correction appends an "
                     "outcome revision; it never rewrites or duplicates the pregame observation."),
        }, now=now)
        return {"observationId": observation, "gameId": game_id,
                "admissionState": record["admission"]["state"], "recordSha256": content_sha,
                "created": False, "skipped": True, "file": None}
    created = _write_once_same(path, data)
    ledger.append({
        "kind": "OBSERVATION_WRITTEN" if created else "OBSERVATION_ALREADY_PRESENT",
        "exportIdentity": EXPORT_IDENTITY, "contract": CONTRACT,
        "contractSha256": CONTRACT_SHA256, "protocolSha256": PROTOCOL_SHA256,
        "observationId": observation, "gameId": game_id, "sport": record["sport"],
        "checkpointAtUtc": record["checkpointAtUtc"],
        "admissionState": record["admission"]["state"],
        "reasonCodes": record["admission"]["reasonCodes"],
        "recordSha256": content_sha, "recordBytes": len(data),
        "recordFile": f"{RECORDS_DIR}/{game_id}/{content_sha}.json",
    }, now=now)
    if admitted is not None:
        # ``|=`` rather than ``.add``: the module's own AST guard forbids a bare
        # ``.add(...)`` call anywhere, because that is how an ORM insert looks.
        admitted |= {observation}
    return {"observationId": observation, "gameId": game_id,
            "admissionState": record["admission"]["state"], "recordSha256": content_sha,
            "created": created, "skipped": False,
            "file": f"{RECORDS_DIR}/{game_id}/{content_sha}.json"}


def write_outcome_revisions(directory: Path, record: dict[str, Any],
                            revisions: list[dict[str, Any]], *, ledger: AttemptLedger,
                            now: datetime, session: Session | None = None) -> list[dict[str, Any]]:
    _refuse_write_inside_read_transaction(session)
    game_id = str(record["gameId"])
    written: list[dict[str, Any]] = []
    for ordinal, revision in enumerate(revisions, start=1):
        built = build_outcome_revision(record, revision, ordinal)
        data = pilot.canonical_bytes(built)
        content_sha = _sha(data)
        path = revision_path(directory, game_id, content_sha)
        created = _write_once_same(path, data)
        ledger.append({
            "kind": "OUTCOME_REVISION_WRITTEN" if created else "OUTCOME_REVISION_ALREADY_PRESENT",
            "exportIdentity": EXPORT_IDENTITY, "contract": OUTCOME_REVISION_CONTRACT,
            "observationId": built["observationId"], "gameId": game_id,
            "revisionOrdinal": ordinal,
            "revisionIdentity": revision.get("revisionIdentity"),
            "recordSha256": content_sha, "recordBytes": len(data),
            "recordFile": f"{OUTCOMES_DIR}/{game_id}/{content_sha}.json",
        }, now=now)
        written.append({"revisionOrdinal": ordinal, "recordSha256": content_sha,
                        "created": created})
    return written


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------
#: How long after a game's T-15 checkpoint before it may be observed at all.
#:
#: A CANONICAL RECORD IS WRITE-ONCE, SO IT MUST NOT BE WRITTEN EARLY.
#: ------------------------------------------------------------------
#: The universe is every game starting in the window, and the window reaches 36
#: hours ahead. Without this guard the first tick of the day evaluates tonight's
#: games hours before their checkpoints, finds no decision yet, and seals each
#: one EXCLUDED -- permanently, because the next tick's real decision would then
#: be "later evidence" and never written. That is not a hypothetical edge: it is
#: what the very first tick after activation would have done to nearly every
#: game on the slate. It was found before activation, not after.
#:
#: The margin past the checkpoint also absorbs a decision row whose transaction
#: commits a moment after its stamp -- `created_at` is the transaction START --
#: so the one canonical read happens once the checkpoint's evidence is settled.
#: With a ten-minute owner and a twelve-hour look-back, it costs no coverage.
CAPTURE_SETTLING = timedelta(minutes=10)


def observable(game: GameRow, now: datetime | None) -> bool:
    """Has this game's checkpoint passed, with its settling margin, as of ``now``?"""

    if now is None:
        return True
    checkpoint = checkpoint_for(game.starts_at)
    if checkpoint is None:
        return True  # recorded as SCHEDULED_START_MISSING, never silently dropped
    return checkpoint + CAPTURE_SETTLING <= pilot.aware(now)


def build_all(session: Session, *, window_start: datetime, window_end: datetime,
              sports: Iterable[str] = SPORTS, now: datetime | None = None,
              ) -> list[tuple[Observation, dict[str, Any], list[dict[str, Any]]]]:
    """Every OBSERVABLE record for the window. Reads only; writes nothing anywhere.

    A game whose checkpoint has not yet passed (plus :data:`CAPTURE_SETTLING`) is
    not built at all -- not refused, not excluded, simply not yet observable. It
    is built by the first tick after that moment.
    """

    built: list[tuple[Observation, dict[str, Any], list[dict[str, Any]]]] = []
    for game in universe(session, window_start=window_start, window_end=window_end, sports=sports):
        if not observable(game, now):
            continue
        observed = evaluate_game(session, game)
        if observed is None:  # pragma: no cover - evaluate_game always returns
            continue
        record = build_observation(session, observed)
        built.append((observed, record, outcome_revisions(session, game)))
    return built


def run_prospective_capture(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    sports: Iterable[str] | None = None,
    directory: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Capture every eligible game in the window. OFF by default.

    Returns an inert summary when ``prospective_capture_enabled`` is false: no row
    is read, no file is opened, nothing is created, no provider is called.
    """

    if not getattr(settings, "prospective_capture_enabled", False):
        return {"enabled": False, "records": 0, "contract": CONTRACT,
                "contractSha256": CONTRACT_SHA256,
                "note": ("PROSPECTIVE_CAPTURE_ENABLED is off; nothing read and nothing written. "
                         "The invocation path is prepared and deliberately not activated.")}
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
    admitted = _admitted_observation_ids(ledger)

    # Property A + D: one explicitly read-only transaction, listeners attached.
    with read_only_session(session):
        built = build_all(session, window_start=start, window_end=end,
                          sports=sports or SPORTS, now=moment)

    # Property C: every file write happens OUT HERE, after the transaction closed.
    written: list[dict[str, Any]] = []
    revisions_written: list[dict[str, Any]] = []
    for _, record, revisions in built:
        written.append(write_observation(target, record, ledger=ledger,
                                         now=pilot.aware(clock()), session=session,
                                         admitted=admitted))
        if revisions:
            revisions_written.extend(write_outcome_revisions(
                target, record, revisions, ledger=ledger, now=pilot.aware(clock()),
                session=session))
    counts = {state: sum(1 for _, record, _ in built
                         if record["admission"]["state"] == state)
              for state in ADMISSION_STATES}
    return {
        "enabled": True, "contract": CONTRACT, "contractSha256": CONTRACT_SHA256,
        "protocolSha256": PROTOCOL_SHA256, "directory": str(target),
        "windowStartUtc": pilot.iso(start), "windowEndUtc": pilot.iso(end),
        "universe": len(built), "records": len(written),
        "outcomeRevisions": len(revisions_written),
        "created": sum(1 for row in written if row["created"]),
        "admission": counts, "written": written,
        "readOnlyProof": READ_ONLY_PROOF,
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
# The prepared -- and deliberately INACTIVE -- invocation path
# ---------------------------------------------------------------------------
#: The lane this work belongs on, and the one it must NEVER occupy.
#:
#: WHY ``research-export`` (``asp-rx-*``) AND NOT ``decision-checkpoints``:
#: the exporter reads retained rows and writes its own files. It calls no
#: provider, creates no decision and creates no position, so it has none of the
#: properties that earn a place on the decision lane -- and every reason to stay
#: off it. A checkpoint's window is fifteen minutes wide and cannot be moved; this
#: export has no deadline at all. Putting it on ``asp-dc-*`` would let a slow
#: export delay a decision, which is precisely the 2026-09-20 pathology the lanes
#: were built to remove.
#:
#: WHY NOT ``producer-import`` (``asp-im-*``): the import must complete BEFORE the
#: decision, and this export must run AFTER the decision-time evidence is fixed.
#: Sharing the import's single worker would add work to the one lane whose
#: lateness silently costs a decision (703.56 measured seconds on 2026-09-20).
INVOCATION_LANE = "research-export"
INVOCATION_JOB_PREFIX = "asp-rx-"
#: The EXISTING supervised owner this attaches to. No new scheduler id, no new
#: job, no new owner: ``export-v18-market-pilot-outcomes-v2`` already runs every
#: ten minutes on the research-export lane, already opens its own reader session,
#: already makes zero provider requests and already runs strictly AFTER the
#: decision-time evidence is fixed. The hook is a trailing step of that tick.
INVOCATION_OWNER_JOB = "export-v18-market-pilot-outcomes-v2"
INVOCATION_OWNER_SCHEDULER_ID = "asp-rx-v18-market-pilot-outcomes-v2"
INVOCATION_STATE = "PREPARED_NOT_ACTIVATED"


def research_export_tick_hook(session: Session, settings: Settings, *,
                              now: datetime | None = None) -> dict[str, Any]:
    """Trailing step of the EXISTING research-export owner. Inert until enabled.

    It is a no-op while ``prospective_capture_enabled`` is false, which is the
    default and the deployed value. Nothing is registered anywhere: the only way
    this runs is if the existing owner calls it AND the flag is on.

    The guarantees the owner needs, and where each comes from:

    * runs AFTER the canonical decision-time evidence is fixed -- the owner is the
      outcome lane, which runs strictly after the checkpoints it reads;
    * never delays a decision -- a different single-worker lane entirely;
    * zero provider requests -- this module imports no client and no data source;
    * zero database mutations -- :data:`READ_ONLY_PROOF` A to E;
    * does not compete with checkpoint execution -- ``asp-rx-*``, not ``asp-dc-*``;
    * at most one canonical record per eligible game -- :func:`observation_id` plus
      :func:`_admitted_observation_ids`;
    * recovers idempotently after interruption -- records are content-addressed and
      a byte-identical re-run adopts the existing file; the ledger enumerates what
      was already admitted;
    * preserves exclusions -- an unpriced or excluded game keeps its record and its
      immutable reason codes instead of having a value fabricated for it.
    """

    if not getattr(settings, "prospective_capture_enabled", False):
        return {"enabled": False, "state": INVOCATION_STATE, "lane": INVOCATION_LANE,
                "owner": INVOCATION_OWNER_JOB,
                "note": "Prepared invocation path; the flag is off so this tick did nothing."}
    return run_prospective_capture(session, settings, now=now)


# ---------------------------------------------------------------------------
# The manual entry point. No scheduler, no job registration.
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """``python -m app.research_exports.prospective_capture`` -- manual only."""

    parser = argparse.ArgumentParser(
        prog="prospective-capture",
        description=("Model-market prospective observation export (MODEL_MARKET_OBSERVATION_V1). "
                     "DISABLED by default: it does nothing unless PROSPECTIVE_CAPTURE_ENABLED is "
                     "true. It never writes a decision, a position or a scheduled job."))
    parser.add_argument("--window-start", help="ISO-8601 UTC; default now-12h")
    parser.add_argument("--window-end", help="ISO-8601 UTC; default now+36h")
    parser.add_argument("--now", help="ISO-8601 UTC evaluation moment; default the wall clock")
    parser.add_argument("--sports", default=",".join(SPORTS))
    parser.add_argument("--directory", default=None)
    parser.add_argument("--verify-contract", default=None,
                        help="Directory holding the two frozen contract files; re-hashes them.")
    args = parser.parse_args(argv)

    from app.core.config import get_settings
    from app.db.session import SessionLocal

    if args.verify_contract:
        print(json.dumps(verify_contract_files(args.verify_contract), indent=2, sort_keys=True))

    settings = get_settings()
    with SessionLocal() as session:
        summary = run_prospective_capture(
            session, settings,
            now=pilot.aware(args.now) if args.now else None,
            window_start=pilot.aware(args.window_start) if args.window_start else None,
            window_end=pilot.aware(args.window_end) if args.window_end else None,
            sports=[code.strip() for code in args.sports.split(",") if code.strip()],
            directory=args.directory)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


# Property B, checked at import time rather than only by a test: a future edit
# that adds an ORM mutation path stops this module from loading at all.
assert_module_has_no_mutation_path()


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())


__all__ = [
    "ADMISSION_EXCLUDED", "ADMISSION_PRICED", "ADMISSION_STATES", "ADMISSION_UNPRICED",
    "BOOK_REASON_CODES", "BOUND_CONVENTIONS", "CAPTURE_SETTLING", "CHANNEL_MEANINGS", "CLOCK_MEANINGS",
    "COMPLETION_TIME_UNAVAILABLE", "CONTRACT", "CONTRACT_FILE", "CONTRACT_SHA256",
    "ENROLLMENT_END", "EXCLUSION_REASON_CODES", "EXPORT_IDENTITY", "FORBIDDEN_SENTINEL",
    "FREEZE_INSTANT", "FREEZE_INSTANT_UTC", "INVOCATION_JOB_PREFIX", "INVOCATION_LANE",
    "INVOCATION_OWNER_JOB", "INVOCATION_OWNER_SCHEDULER_ID", "INVOCATION_STATE", "LEDGER_NAME",
    "MAX_QUOTE_AGE_SECONDS", "OUTCOMES_DIR", "OUTCOME_REVISION_CONTRACT", "PROTOCOL_FILE",
    "PROTOCOL_SHA256", "READ_ONLY_PROOF", "REASON_CODES", "RECORDS_DIR", "RECORD_FIELDS",
    "REQUIRED_CONVENTIONS", "SPORTS", "STATUS_PROSPECTIVE", "STATUS_SYNTHETIC",
    "UNPRICED_REASON_CODES", "BookCandidate", "CaptureRefused", "GameRow", "Leg", "Observation",
    "assert_module_has_no_mutation_path", "assert_no_derived_comparison", "assert_no_money",
    "assert_no_not_applicable_sentinel", "build_all", "build_observation",
    "build_outcome_revision", "checkpoint_for", "contract_binding", "conventions_for",
    "deciding_row", "eligible_book_candidates", "evaluate_game", "main", "no_vig",
    "observation_id", "oriented_channels", "outcome_for", "outcome_revisions",
    "position_for", "published_channels", "read_only_session", "record_path",
    "research_export_tick_hook", "revision_path", "run_prospective_capture",
    "sqlite_row_count_probe", "universe", "validate_record", "verify_contract_files",
    "write_observation", "write_outcome_revisions",
]
