"""NFL early-line research export -- ``NFL_EARLY_LINE_OBSERVATION_V1``.

Protocol ``NFL_EARLY_LINE_INFORMATION_EDGE_V1``, frozen by Model Studio at
2026-09-22T17:24:10.059686Z. This module maps that frozen contract; it invents no
schema of its own. Every field below exists because the contract or the frozen
protocol names it.

WHAT IT DOES
------------
For every NFL game first eligible after the freeze it writes, at most once per
game and horizon, one immutable content-addressed JSON record for each of:

* ``MARKET_OPEN_PROXY`` -- the earliest authentic retained response carrying a
  complete same-book two-sided moneyline pair. Never purchased for itself.
* ``EARLY_T24`` -- the retained response whose capture falls in
  [T-30h, T-18h], closest to T-24h. In practice this is the existing Show Prep
  preview purchase. Never a later response.
* ``LATE_T26`` -- a retained response satisfying the frozen T-26 / T-23 / T-21
  rules. The current production purchase (~T-66..-75) does not, so this horizon
  is recorded MISSING until a retime is separately authorized.

WHAT IT MAY NOT DO
------------------
It never calls a provider, never writes a decision, a position or any database
row, and never changes model, staking, threshold or bankroll state. Every read is
one PostgreSQL transaction confirmed READ ONLY; every file write happens after
that transaction has closed. It is OFF unless ``nfl_early_line_capture_enabled``
is true, and it runs only as a trailing step of an existing research-export
owner -- it registers nothing.

THE TWO PRICE CLOCKS
--------------------
*When we observed the price* (the archived response's capture) and *when the
book says it last changed it* (each leg's ``lastUpdatedAt``) are different facts
and are never collapsed. A price inside a freshly archived response is a fresh
observation of the currently offered price even if the book has not moved it for
hours, so an old ``lastUpdatedAt`` never makes a candidate ineligible.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    Game, GameTeam, JobRun, League, Market, MarketOutcome, NflFinalObservation, OddsSnapshot,
    ProviderRequestLedger, RunStatus, Season, ShadowForecast, ShadowForecastBundle, Team,
)
from app.research_exports import v18_market_pilot as pilot
from app.research_exports.prospective_capture import (
    BOUND_CONVENTIONS, assert_module_has_no_mutation_path, read_only_session,
)
from app.research_exports.v18_market_pilot_seal import write_once
from app.services import nfl_final_readiness as readiness

# ---------------------------------------------------------------------------
# Frozen identities. Re-hashed from disk before any record is built.
# ---------------------------------------------------------------------------
CONTRACT = "NFL_EARLY_LINE_OBSERVATION_V1"
PROTOCOL = "NFL_EARLY_LINE_INFORMATION_EDGE_V1"
PROTOCOL_SHA256 = "e0eeb8e1b6f18ccd01a11911f20f6b29bad7be16e3d94d4b18a9bec93f15c834"
CONTRACT_SHA256 = "bad67e3ee246b8eda8517013df1ffd04cc3883536bff432dff21d8b3b07b7bfb"
VALIDATOR_SHA256 = "06e1738a6a0f08ba6b84f436eecf75859a1fcaa3b457665769a26bcb57a2bc08"
FROZEN_AT = datetime(2026, 9, 22, 17, 24, 10, 59686, tzinfo=UTC)
CONTRACT_FILES = {
    "FROZEN_PROTOCOL.json": PROTOCOL_SHA256,
    "CONSUMER_EXPORT_CONTRACT.md": CONTRACT_SHA256,
    "validate_observation.py": VALIDATOR_SHA256,
}

#: Model Studio's producer evidence, by the contract each artifact declares.
#: Nothing here is read by a path a document states: every object is located at
#: its own content address and re-hashed, exactly as the NFL FINAL readiness
#: family is, so a moved or rewritten file cannot impersonate a published one.
CURRENT_EXPOSURE_CONTRACT = "ASP_NFL_M1_CURRENT_INDEX_EXPOSURE_V1"
SCHEDULE_IDENTITY_CONTRACT = "NFL_PREGAME_SCHEDULE_IDENTITY_V1"
CURRENT_EXPOSURE_DIR = ("aisp-daily-forecast-bundles", "nfl-current-exposure")
#: The ONLY status an exposure receipt may carry to be usable. An allowlist,
#: not a refusal list: a missing, unknown, synthetic or placeholder status fails
#: closed, so a receipt shape nobody anticipated cannot enrol anything by
#: default. Model Studio published this token for exactly that purpose.
OPERATIONAL_EXPOSURE_STATUS = "FUTURE_ONLY_PRODUCER_PUBLICATION_EVIDENCE"


def usable_exposure_receipt(receipt: dict[str, Any]) -> str | None:
    """None when the receipt may be used, otherwise the reason it may not.

    The same allowlist governs the receipt chosen as the exposure AND every
    receipt consulted when deciding which index was in force at the horizon.
    Applying it in only one of those places would let an unusable receipt decide
    that a forecast had been replaced -- or that it had not.
    """

    if receipt.get("contract") != CURRENT_EXPOSURE_CONTRACT:
        return "CURRENT_EXPOSURE_RECEIPT_CONTRACT_UNKNOWN"
    if str(receipt.get("status") or "") != OPERATIONAL_EXPOSURE_STATUS:
        return "CURRENT_EXPOSURE_RECEIPT_STATUS_NOT_OPERATIONAL"
    threshold = pilot.aware(receipt.get("futureOnlyAfterUtc"))
    if threshold is None:
        return "CURRENT_EXPOSURE_FUTURE_ONLY_THRESHOLD_MISSING"
    exposure = pilot.aware(receipt.get("producerExposureUpperBoundUtc"))
    if exposure is None:
        return "CURRENT_EXPOSURE_CLOCK_MISSING"
    if exposure < threshold:
        # No older publication acquires a receipt retroactively.
        return "CURRENT_EXPOSURE_RECEIPT_RETROACTIVE"
    return None


def _content_addressed(root: Path, *parts: str, filename: str) -> list[tuple[str, dict[str, Any]]]:
    """Every object under ``root/parts/sha256/<sha>/filename`` whose address holds."""

    found: list[tuple[str, dict[str, Any]]] = []
    base = root.joinpath(*parts) / "sha256"
    if not base.is_dir():
        return found
    for directory in sorted(base.iterdir()):
        path = directory / filename
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        # The directory name IS the claim. If the bytes do not hash to it, the
        # object is not what it says it is and is passed over in silence.
        if hashlib.sha256(raw).hexdigest() != directory.name:
            continue
        try:
            found.append((directory.name, json.loads(raw)))
        except json.JSONDecodeError:
            continue
    return found


def load_schedule_identity(directory: str | Path | None) -> tuple[dict[str, Any] | None, str | None]:
    """The newest point-in-time pregame schedule identity manifest, or why not.

    This one artifact carries BOTH of the things AISP cannot retain pregame:
    season type and week, and the narrow evidence-backed team alias set. It is
    resolved by content address and taken at its published contract; no local
    schedule is ever used in its place.
    """

    if directory is None:
        return None, "PREGAME_SCHEDULE_MANIFEST_NOT_SUPPLIED"
    found = [(sha, doc) for sha, doc in
             _content_addressed(Path(directory), "schedule", filename="SCHEDULE_IDENTITY.json")
             if doc.get("contract") == SCHEDULE_IDENTITY_CONTRACT]
    if not found:
        return None, "PREGAME_SCHEDULE_MANIFEST_NOT_SUPPLIED"
    sha, manifest = max(found, key=lambda item: str(item[1].get("createdAtUtc") or ""))
    return dict(manifest, manifestSha256=sha), None


def schedule_row(manifest: dict[str, Any] | None, game) -> dict[str, Any] | None:
    """The one manifest row that is unambiguously this game, or nothing.

    The manifest leaves ``aispCanonicalGameUuid`` and ``sportsGameOddsEventId``
    null and says so: a null id is UNRESOLVED, not a match. The join it asks for
    is the same kickoff and BOTH teams, which matters because an NFL Sunday puts
    a dozen games on the same kickoff -- matching on the clock alone would pick
    an arbitrary one of them. Anything less than a unique hit resolves to
    nothing, and the caller fails closed.
    """

    if not manifest or not game.home_abbr or not game.away_abbr:
        return None
    home, away = game.home_abbr.strip().upper(), game.away_abbr.strip().upper()
    rows = [row for row in manifest.get("games") or []
            if pilot.aware(row.get("scheduledKickoffUtc")) == game.starts_at
            and str((row.get("home") or {}).get("canonicalTeamCode") or "").upper() == home
            and str((row.get("away") or {}).get("canonicalTeamCode") or "").upper() == away]
    return rows[0] if len(rows) == 1 else None


#: Market-side identity failures. Each PREVENTS predictive eligibility: a price
#: we cannot tie to this game is not an observation of this game's market. The
#: game is still written, carrying the reason.
OPERATIVE_MARKET_IDENTITY = (
    "RESPONSE_EVENT_NOT_BOUND_TO_GAME",
    "PROVIDER_EVENT_ID_ABSENT_FOR_GAME",
    "PROVIDER_EVENT_ID_NOT_UNIQUE_FOR_GAME",
    "RESPONSE_HOME_TEAM_ALIAS_UNSUPPORTED",
    "RESPONSE_AWAY_TEAM_ALIAS_UNSUPPORTED",
    "RESPONSE_HOME_TEAM_UNVERIFIABLE",
    "RESPONSE_AWAY_TEAM_UNVERIFIABLE",
)
#: Forecast-side identity failures. Each PREVENTS binding, for the same reason.
OPERATIVE_CHILD_IDENTITY = (
    "CHILD_SCHEDULED_START_DISAGREES",
    "CHILD_EVENT_NOT_BOUND_TO_GAME",
    "CHILD_HOME_TEAM_ALIAS_UNSUPPORTED",
    "CHILD_AWAY_TEAM_ALIAS_UNSUPPORTED",
    "CHILD_HOME_TEAM_UNVERIFIABLE",
    "CHILD_AWAY_TEAM_UNVERIFIABLE",
)

EXPORT_IDENTITY = "AISP_NFL_EARLY_LINE_EXPORTER_V1"
LEDGER_NAME = "early-line-ledger.jsonl"
RECORDS_DIR = "records"
ENGINE_ID = "NFL_M1_XGBOOST"
LEAGUE = "NFL"

MARKET_OPEN_PROXY = "MARKET_OPEN_PROXY"
EARLY_T24 = "EARLY_T24"
LATE_T26 = "LATE_T26"
HORIZONS = (MARKET_OPEN_PROXY, EARLY_T24, LATE_T26)

# Frozen horizon geometry, in seconds before kickoff (FROZEN_PROTOCOL.horizons).
EARLY_TARGET = 86400
EARLY_EARLIEST = 108000   # T-30h, inclusive
EARLY_LATEST = 64800      # T-18h, inclusive
LATE_PRIMARY = 1560       # T-26, the target
#: The PREDECLARED primary request interval, [T-27, T-25] inclusive. Model Studio
#: rejected our earlier [T-28, T-23): a request arriving near T-23 could be taken
#: for a primary and so walk straight past the retry rule. Narrow and explicit.
LATE_PRIMARY_EARLIEST = 1620   # T-27
LATE_PRIMARY_LATEST = 1500     # T-25
LATE_RETRY = 1380         # T-23
LATE_HARD_STOP = 1260     # T-21: no request or response at or after it qualifies
LATE_CHECKPOINT = 900     # T-15, the late decision proxy

#: A horizon's record is written only once every response that could belong to
#: it must already exist, plus this settling margin. Records are write-once, so
#: writing early would seal an incomplete picture: the same failure that nearly
#: sealed every evening game EXCLUDED in the MODEL_MARKET_OBSERVATION_V1 exporter.
SETTLING = timedelta(minutes=10)

STATE_PREDICTIVE = "PREDICTIVE_MARKET_ELIGIBLE"
STATE_RETURN = "RETURN_SIMULATION_ELIGIBLE"
STATE_UNPRICED = "UNPRICED"
STATE_EXCLUDED = "EXCLUDED"

RETURN_CONVENTIONS = ("overtime", "tie", "postponement", "suspension", "settlement")
#: The registry binds rules under its own names; the contract names them thus.
_CONVENTION_NAMES = {
    "overtime": "overtimeRule", "tie": "settlementRule", "postponement": "postponementRule",
    "suspension": "suspensionRule", "settlement": "resultSource",
}

CLOCK_BASIS = {
    "providerRequestAtUtc": (
        "provider_request_ledger.requested_at: when AISP SENT the request. Not a receipt."),
    "providerResponseCapturedAtUtc": (
        "the raw archive's archivedAtUtc: the first retained instant of the provider's exact "
        "response bytes. The observation clock; acquisition age is measured from it."),
    "fetchedAtUtc": (
        "provider_request_ledger.completed_at: the collector finished fetching and storing this "
        "response. Always at or after capture."),
    "lastUpdatedAtUtc": (
        "the provider-stated time this book last changed (or confirmed) this leg. Source-update "
        "age is measured from it. An old value inside a freshly captured response is a current "
        "observation of an unchanged price -- never evidence that the price was unavailable."),
    "exposedAtUtc": (
        "the readiness family's OBSERVED exposure clock for this forecast "
        "(forecastLocatorExposure.visibleAtUtc inside its content-addressed receipt). "
        "shadow_forecasts.available_as_of_utc is a producer DECLARATION and is never "
        "used as exposure proof."),
    "generatedAtUtc": (
        "the package's own generatedAtUtc: a real generation/publication clock, never "
        "forecastCutoffUtc or sourceCutoffUtc, which are input cutoffs carried "
        "separately."),
    "consumerObservedOrImportCompletedAtUtc": (
        "UPPER bound on when AISP could read the forecast: the start of the NEXT run of the "
        "import job that committed it. Each import owner runs on a single-worker lane and a run "
        "returns only after its final commit, so the next run cannot start before the forecast "
        "was committed. The containing run's own completed_at is NOT used: it is stamped before "
        "that run's final commit. shadow_experiment_decisions.forecast_imported_at is NOT used: "
        "the NFL decision path writes the forecast's declared availability into it."),
    "availabilityAtUtc": "max(exposedAtUtc, consumerObservedOrImportCompletedAtUtc), per the protocol.",
}

#: Jobs that commit NFL forecast rows. A forecast whose row falls inside no run of
#: these jobs has no provable consumer bound and is never bound.
IMPORT_JOBS = ("import-nfl-final-pair", "consume-model-exchange-research-artifacts")

_EXCHANGE_URI = "asp-model-exchange://"


class ContractUnbound(RuntimeError):
    """The frozen contract files are missing or do not hash to the reviewed values."""


def verify_contract_files(directory: str | Path) -> dict[str, str]:
    """Re-hash the three frozen files from disk; refuse on any mismatch."""

    root = Path(directory)
    seen: dict[str, str] = {}
    for name, expected in CONTRACT_FILES.items():
        try:
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        except OSError as exc:
            raise ContractUnbound(f"{name} unreadable in {root}: {exc}") from exc
        if actual != expected:
            raise ContractUnbound(f"{name} hashes to {actual}, reviewed {expected}")
        seen[name] = actual
    return seen


# ---------------------------------------------------------------------------
# Exact arithmetic
# ---------------------------------------------------------------------------
def american_to_decimal(american: int) -> Decimal:
    """Exact decimal odds from integer American odds."""

    value = Decimal(int(american))
    if value >= 100:
        return Decimal(1) + value / Decimal(100)
    if value <= -100:
        return Decimal(1) + Decimal(100) / (-value)
    raise ValueError(f"American odds {american} are not a valid price")


def no_vig_home(home_decimal: Decimal, away_decimal: Decimal) -> Decimal:
    """q_home = (1/d_home) / ((1/d_home) + (1/d_away)), exactly as frozen."""

    inv_home, inv_away = Decimal(1) / home_decimal, Decimal(1) / away_decimal
    return inv_home / (inv_home + inv_away)


def median(values: list[Decimal]) -> Decimal | None:
    """The frozen consensus: median; for an even count, the mean of the central two."""

    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _parse_american(value: Any) -> int | None:
    try:
        number = int(str(value).strip().replace("+", ""))
    except (TypeError, ValueError):
        return None
    return number if abs(number) >= 100 else None


def _dstr(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
# Retained provider responses
# ---------------------------------------------------------------------------
#: How a leg's ``lastUpdatedAt`` was read. The RAW value is always retained
#: beside it: absence and unparseability are different facts, and neither may be
#: silently flattened into "no clock".
CLOCK_PARSED = "PARSED_UTC"
CLOCK_ABSENT = "ABSENT"
CLOCK_UNPARSEABLE = "UNPARSEABLE"


def _read_leg_clock(value: Any) -> tuple[datetime | None, str]:
    """The provider's update clock: raw in, (parsed-or-None, disposition) out."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return None, CLOCK_ABSENT
    parsed = pilot.aware(value)
    return (parsed, CLOCK_PARSED) if parsed is not None else (None, CLOCK_UNPARSEABLE)


@dataclass
class Leg:
    side: str
    leg_id: str
    american: int | None
    decimal: Decimal | None
    last_updated_raw: Any            # exactly what the provider sent, unaltered
    last_updated_at: datetime | None  # the parsed UTC value, or None
    clock_disposition: str            # PARSED_UTC | ABSENT | UNPARSEABLE
    available: bool


@dataclass
class Candidate:
    #: The provider's own key, byte-for-byte as it keyed the book. Never
    #: lowercased, trimmed or otherwise repaired in place: the normalized form
    #: is derived beside it, so both survive into the record.
    provider_sportsbook_key: str
    home: Leg | None
    away: Leg | None
    reasons: list[str] = field(default_factory=list)

    @property
    def normalized_key(self) -> str:
        """Casefolded and trimmed; the provider's original key is never altered."""

        return str(self.provider_sportsbook_key).strip().casefold()

    @property
    def eligible(self) -> bool:
        return not self.reasons


@dataclass
class Response:
    ledger_id: str
    requested_at: datetime
    completed_at: datetime | None
    captured_at: datetime | None
    objects_charged: int | None
    status: str
    request_fingerprint: str | None
    envelope_sha256: str | None
    body_sha256: str | None
    envelope_path: str | None
    body_path: str | None
    authentic: bool
    authenticity_detail: str
    events: list[dict[str, Any]] = field(default_factory=list)


def _load_response(row: Any) -> Response:
    """One ledger row, its archive re-read and re-hashed. Never trusted unread."""

    meta = row.response_metadata if isinstance(row.response_metadata, dict) else (
        json.loads(row.response_metadata) if row.response_metadata else {})
    archive = meta.get("raw_archive") or {}
    response = Response(
        ledger_id=str(row.id), requested_at=pilot.aware(row.requested_at),
        completed_at=pilot.aware(row.completed_at),
        captured_at=pilot.aware(archive.get("archivedAtUtc")),
        objects_charged=row.records_received, status=str(row.status),
        request_fingerprint=row.request_fingerprint,
        envelope_sha256=archive.get("sha256"), body_sha256=archive.get("responseBodySha256"),
        envelope_path=archive.get("path"), body_path=archive.get("responseBodyPath"),
        authentic=False, authenticity_detail="")
    if archive.get("status") != "ARCHIVED" or not response.envelope_path or not response.body_path:
        response.authenticity_detail = "NO_RAW_ARCHIVE"
        return response
    try:
        envelope_bytes = Path(response.envelope_path).read_bytes()
        body_bytes = gzip.decompress(Path(response.body_path).read_bytes())
    except OSError as exc:
        response.authenticity_detail = f"ARCHIVE_UNREADABLE: {exc}"
        return response
    if hashlib.sha256(envelope_bytes).hexdigest() != response.envelope_sha256:
        response.authenticity_detail = "ARCHIVE_ENVELOPE_HASH_MISMATCH"
        return response
    if hashlib.sha256(body_bytes).hexdigest() != response.body_sha256:
        response.authenticity_detail = "ARCHIVE_BODY_HASH_MISMATCH"
        return response
    envelope = json.loads(envelope_bytes)
    if envelope.get("metadata", {}).get("request_fingerprint") != response.request_fingerprint:
        response.authenticity_detail = "REQUEST_FINGERPRINT_MISMATCH"
        return response
    if response.captured_at is None or response.captured_at < response.requested_at:
        response.authenticity_detail = "CAPTURE_CLOCK_MISSING_OR_BEFORE_REQUEST"
        return response
    response.events = list(json.loads(body_bytes).get("data") or [])
    response.authentic = True
    response.authenticity_detail = "VERIFIED"
    return response


def _team_checks(prefix: str, stated: dict[str, Any], game, row: dict[str, Any] | None) -> list[str]:
    """Compare stated team codes with the canonical ones. Fail closed.

    An exact match against AISP's own abbreviation verifies. Anything else needs
    Model Studio's schedule-identity row, whose ``supportedProviderCodes`` is a
    narrow, witnessed alias set -- not a 32-team crosswalk. It is what makes a
    provider's ``LA`` acceptable for the Rams, whose canonical code is ``LAR``
    and whose AISP row is still named "St. Louis Rams". With no row, or with a
    code outside the supported set, the alias is UNSUPPORTED and nothing binds.
    """

    checks: list[str] = []
    for side, own in (("home", game.home_abbr), ("away", game.away_abbr)):
        value = str(stated.get(side) or "").strip().upper()
        entry = (row or {}).get(side) or {}
        canonical = str(entry.get("canonicalTeamCode") or own or "").strip().upper()
        if not value or not canonical:
            checks.append(f"{prefix}_{side.upper()}_TEAM_UNVERIFIABLE")
            continue
        supported = {str(code).strip().upper()
                     for code in entry.get("supportedProviderCodes") or []}
        if value == canonical or value in supported:
            continue
        # Either a different team, or the same team under a code no evidence
        # supports. We cannot tell which from here, and both fail closed.
        checks.append(f"{prefix}_{side.upper()}_TEAM_ALIAS_UNSUPPORTED")
    return checks


def _verify_response_identity(event: dict[str, Any], game, registry: Any = None) -> dict[str, Any]:
    """The chosen response's own event and teams, against the canonical game.

    Every check here is OPERATIVE: a price we cannot tie to this game does not
    price this game, so a failure prevents predictive eligibility rather than
    merely annotating the record. The game is still written, with its reason.
    """

    teams = event.get("teams") or {}
    stated = {side: ((teams.get(side) or {}).get("names") or {}).get("short")
              for side in ("home", "away")}
    checks: list[str] = []
    if game.sgo_event_ids and str(event.get("eventID")) not in game.sgo_event_ids:
        checks.append("RESPONSE_EVENT_NOT_BOUND_TO_GAME")
    checks += _team_checks("RESPONSE", stated, game, registry)
    return {
        "providerEventId": event.get("eventID"),
        "providerHomeShort": stated["home"], "providerAwayShort": stated["away"],
        "providerHomeTeamId": (teams.get("home") or {}).get("teamID"),
        "providerAwayTeamId": (teams.get("away") or {}).get("teamID"),
        "providerSeasonWeek": (event.get("info") or {}).get("seasonWeek"),
        "canonicalHomeAbbr": game.home_abbr, "canonicalAwayAbbr": game.away_abbr,
        "checks": sorted(set(checks)),
    }


def _candidates(event: dict[str, Any], captured_at: datetime) -> list[Candidate]:
    """Every book in the response with a leg on either side; ineligible ones keep reasons."""

    odds = event.get("odds") or {}
    sides: dict[str, dict[str, Any]] = {}
    for odd_id, block in odds.items():
        if "-game-ml-" not in odd_id or not odd_id.startswith("points-"):
            continue
        side = odd_id.rsplit("-", 1)[1]
        if side in ("home", "away"):
            sides[side] = (odd_id, block.get("byBookmaker") or {})
    books = sorted(set().union(*(set(v[1]) for v in sides.values()))) if sides else []
    out: list[Candidate] = []
    for book in books:
        legs: dict[str, Leg | None] = {}
        for side in ("home", "away"):
            if side not in sides or book not in sides[side][1]:
                legs[side] = None
                continue
            odd_id, by_book = sides[side]
            quote = by_book[book]
            american = _parse_american(quote.get("odds"))
            raw_clock = quote.get("lastUpdatedAt")
            parsed_clock, disposition = _read_leg_clock(raw_clock)
            legs[side] = Leg(
                side=side, leg_id=f"{odd_id}:{book}", american=american,
                decimal=american_to_decimal(american) if american is not None else None,
                last_updated_raw=raw_clock, last_updated_at=parsed_clock,
                clock_disposition=disposition,
                available=bool(quote.get("available", True)))
        candidate = Candidate(provider_sportsbook_key=str(book), home=legs["home"], away=legs["away"])
        for side, leg in legs.items():
            if leg is None:
                candidate.reasons.append(f"BOOK_{side.upper()}_LEG_MISSING")
                continue
            if leg.american is None:
                candidate.reasons.append(f"BOOK_{side.upper()}_ODDS_INVALID")
            if not leg.available:
                candidate.reasons.append(f"BOOK_{side.upper()}_LEG_NOT_AVAILABLE")
            # A null, unparseable, or post-capture update clock makes THIS
            # candidate ineligible and nothing more: the pair is kept with its
            # raw clock value and a disposition saying which of the three it
            # was, and its valid peers still support the observation. The
            # response is never skipped for it.
            if leg.clock_disposition == CLOCK_ABSENT:
                candidate.reasons += ["INVALID_CANDIDATE_CLOCK", f"BOOK_{side.upper()}_CLOCK_ABSENT"]
            elif leg.clock_disposition == CLOCK_UNPARSEABLE:
                candidate.reasons += ["INVALID_CANDIDATE_CLOCK",
                                      f"BOOK_{side.upper()}_CLOCK_UNPARSEABLE"]
            elif leg.last_updated_at > captured_at:
                candidate.reasons += ["INVALID_CANDIDATE_CLOCK",
                                      f"BOOK_{side.upper()}_CLOCK_AFTER_CAPTURE"]
        # Deliberately NO age test on lastUpdatedAt: see the module docstring.
        out.append(candidate)
    return out


# ---------------------------------------------------------------------------
# The game universe and identity
# ---------------------------------------------------------------------------
@dataclass
class GameRef:
    game_id: str
    starts_at: datetime
    status: str
    home_team_id: str | None
    away_team_id: str | None
    home_abbr: str | None
    away_abbr: str | None
    sgo_event_ids: list[str]
    nflverse_ids: list[str]           # authoritative, present only once final
    forecast_alias_ids: list[str] = field(default_factory=list)
    season: int | None = None
    season_type: str | None = None
    week: int | None = None


def _nfl_games(session: Session, *, starts_from: datetime, starts_to: datetime) -> list[GameRef]:
    """The scheduled-game population, built from AISP's own schedule.

    Disposition 2: the population is the SCHEDULE, never the forecast table. A
    scheduled game with no M1 forecast is a missing-forecast observation, not an
    unresolved identity. Season comes from the season row; nflverse and
    SportsGameOdds identities are recorded as ALIASES with their provenance, and
    are never what makes a game exist.

    Season type and week are not retained pregame anywhere in AISP: the nflverse
    capture keeps only the rows it acted on (finals), so before a game is final
    there is no authoritative week. They are left null with a reason rather than
    taken from a forecast, which would make identity depend on the model.
    """

    rows = session.execute(
        select(Game.id, Game.starts_at, Game.status, Season.name)
        .join(League, League.id == Game.league_id)
        .join(Season, Season.id == Game.season_id)
        .where(League.code == LEAGUE, Game.starts_at >= starts_from, Game.starts_at < starts_to)
        .order_by(Game.starts_at, Game.id)).all()
    games: list[GameRef] = []
    for game_id, starts_at, status, season_name in rows:
        sides = session.execute(
            select(GameTeam.is_home, Team.id, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id == game_id)).all()
        home = next((row for row in sides if row[0]), None)
        away = next((row for row in sides if not row[0]), None)
        sgo = session.execute(
            select(OddsSnapshot.source_event_id).distinct()
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .where(Market.game_id == game_id, OddsSnapshot.source == "SPORTSGAMEODDS",
                   OddsSnapshot.source_event_id.is_not(None))).scalars().all()
        # Authoritative only once the game is final; carried as an alias either way.
        final_rows = session.execute(
            select(NflFinalObservation.source_event_id, NflFinalObservation.season,
                   NflFinalObservation.game_type, NflFinalObservation.week)
            .where(NflFinalObservation.game_id == game_id)).all()
        forecast_aliases = session.execute(
            select(ShadowForecast.event_id).distinct()
            .where(ShadowForecast.canonical_game_id == game_id,
                   ShadowForecast.engine_id == ENGINE_ID)).scalars().all()
        try:
            season = int(str(season_name).strip()[:4])
        except (TypeError, ValueError):
            season = None
        games.append(GameRef(
            game_id=str(game_id), starts_at=pilot.aware(starts_at), status=str(status),
            home_team_id=str(home[1]) if home else None, away_team_id=str(away[1]) if away else None,
            home_abbr=home[2] if home else None, away_abbr=away[2] if away else None,
            sgo_event_ids=sorted({str(v) for v in sgo}),
            nflverse_ids=sorted({str(r[0]) for r in final_rows if r[0]}),
            forecast_alias_ids=sorted({str(v) for v in forecast_aliases if v}),
            season=season,
            season_type=(str(final_rows[0][2]) if final_rows and final_rows[0][2] else None),
            week=(int(final_rows[0][3]) if final_rows and final_rows[0][3] is not None else None)))
    return games


def _responses_for(session: Session, game: GameRef, *, captured_before: datetime) -> list[tuple[Response, dict[str, Any]]]:
    """Authentic NFL responses carrying THIS game's provider event, oldest first."""

    # With no retained provider event id there is nothing to match a response
    # against, and substituting another game's event is never acceptable. With
    # MORE than one the response is still retained -- the caller keeps it and its
    # whole candidate population, and disqualifies every candidate -- because
    # discarding it would hide the evidence that the ambiguity exists.
    if not game.sgo_event_ids:
        return []
    known = set(game.sgo_event_ids)
    every = session.execute(
        select(ProviderRequestLedger)
        .where(ProviderRequestLedger.provider == "SPORTSGAMEODDS",
               ProviderRequestLedger.endpoint_key == "sgo_nfl_events",
               ProviderRequestLedger.requested_at > FROZEN_AT,
               ProviderRequestLedger.requested_at < captured_before)
        .order_by(ProviderRequestLedger.requested_at)).scalars().all()
    # A reservation is one attempt; its terminal row, when one exists, IS that
    # attempt's outcome. A reservation with NO terminal row is an attempt whose
    # charge is UNKNOWN (the process died mid-request) -- it must stay visible,
    # or a later request would be mistaken for the first attempt and a retry
    # after an unknown charge would slip through.
    terminal_for = {row.reservation_id for row in every if row.status != "RESERVED"}
    rows = [row for row in every if row.status != "RESERVED" or row.id not in terminal_for]
    found: list[tuple[Response, dict[str, Any] | None]] = []
    for row in rows:
        response = _load_response(row)
        if response.authentic:
            event = next((e for e in response.events if str(e.get("eventID")) in known), None)
            if event is not None:
                found.append((response, event))
            continue
        # A failed or unarchived attempt carries no events, but the late retry
        # rule needs it: a T-23 retry is legitimate only after a terminal,
        # zero-charge failure of the primary. It is matched to the game by the
        # request's own window, never used as a price.
        meta = row.response_metadata if isinstance(row.response_metadata, dict) else {}
        filters = (meta.get("request_shape") or {}).get("filters") or {}
        after, before = pilot.aware(filters.get("startsAfter")), pilot.aware(filters.get("startsBefore"))
        if after and before and after <= game.starts_at <= before:
            found.append((response, None))
    return found


# ---------------------------------------------------------------------------
# Forecast binding
# ---------------------------------------------------------------------------
@dataclass
class ForecastBinding:
    disposition: str
    reason: str | None
    forecast: dict[str, Any] | None


def _exchange_path(uri: str | None, exchange_root: Path) -> Path | None:
    """A logical `asp-model-exchange://` URI resolved under our configured root."""

    if not uri:
        return None
    if uri.startswith(_EXCHANGE_URI):
        return exchange_root / uri[len(_EXCHANGE_URI):]
    return Path(uri)


def _containing_import_run(session: Session, created_at, bundle_sha256):
    """The successful run that committed this forecast row, and its successor start.

    Disposition 3: a row timestamp falling inside an unrelated run proves nothing.
    Two facts are required together -- the row was created inside the run's span,
    AND that run's own retained receipt names the exact package it imported. Only
    then is the successor run's start an upper bound, because each import owner is
    serialized through its commit on a single-worker lane.
    """

    if created_at is None or not bundle_sha256:
        return None
    runs = session.execute(
        select(JobRun).where(JobRun.job_name.in_(IMPORT_JOBS), JobRun.status == RunStatus.SUCCEEDED,
                             JobRun.started_at <= created_at, JobRun.completed_at >= created_at)
    ).scalars().all()
    matched = []
    for run in runs:
        result = run.result if isinstance(run.result, dict) else {}
        accepted = result.get("accepted") or []
        if any(str(entry.get("bundle_sha256") or "") == bundle_sha256
               for entry in accepted if isinstance(entry, dict)):
            matched.append(run)
    if len(matched) != 1:
        return None                     # no receipt, or an ambiguous one: unproved
    run = matched[0]
    successor = session.execute(
        select(func.min(JobRun.started_at))
        .where(JobRun.job_name == run.job_name, JobRun.started_at > run.completed_at)).scalar()
    return run, pilot.aware(successor)


def _import_receipt_entry(run, bundle_sha256: str) -> dict[str, Any]:
    result = run.result if isinstance(run.result, dict) else {}
    for entry in result.get("accepted") or []:
        if isinstance(entry, dict) and str(entry.get("bundle_sha256") or "") == bundle_sha256:
            return entry
    return {}


def _readiness_exposure(exchange_root: Path, forecast_id: str):
    """A FINAL's atomic exposure receipt: the readiness family's own observed clock."""

    index_path = exchange_root / readiness.READINESS_ROOT / readiness.READINESS_INDEX_FILENAME
    try:
        index = json.loads(index_path.read_bytes())
    except (OSError, json.JSONDecodeError):
        return None, {"detail": "READINESS_INDEX_UNREADABLE"}
    entry = (index.get("receipts") or {}).get(forecast_id)
    if not isinstance(entry, dict):
        return None, {"detail": "READINESS_INDEX_NO_ENTRY"}
    sha = str((entry.get("receiptRef") or {}).get("sha256") or "")
    path = exchange_root / readiness.READINESS_ROOT / readiness.OBJECTS_DIR / sha / "receipt.json"
    try:
        raw = path.read_bytes()
    except OSError:
        return None, {"detail": "READINESS_RECEIPT_UNREADABLE", "receipt": sha}
    if hashlib.sha256(raw).hexdigest() != sha:
        return None, {"detail": "READINESS_RECEIPT_ADDRESS_MISMATCH", "receipt": sha}
    receipt = json.loads(raw)
    visible = pilot.aware((receipt.get("forecastLocatorExposure") or {}).get("visibleAtUtc"))
    return visible, {
        "detail": "VERIFIED" if visible else "NO_VISIBLE_AT", "receipt": sha,
        "currentLocatorSnapshotSha256": (receipt.get("currentLocatorSnapshotRef") or {}).get("sha256"),
        "packageSha256": (receipt.get("bundleRef") or {}).get("sha256"),
    }


def canonical_child_sha256(package_bytes: bytes, forecast_id: str) -> str | None:
    """The child's own content address, derived from the package's ORIGINAL bytes.

    The producer computes it over a plain parse of its own package, so this
    re-parses those same bytes rather than re-serialising the Decimal tree the
    binder works with -- which would not be the same text, and would fail for
    the wrong reason.
    """

    try:
        package = json.loads(package_bytes)
    except json.JSONDecodeError:
        return None
    child = next((c for c in package.get("forecasts") or []
                  if c.get("forecastId") == forecast_id), None)
    if child is None:
        return None
    return hashlib.sha256(
        json.dumps(child, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _current_exposure(exchange_root: Path, forecast_id: str, *, package_sha256: str,
                      package_bytes: bytes, game, row: dict[str, Any] | None,
                      snapshot_version: Any, cutoff: datetime, child_event_id: Any):
    """A CURRENT snapshot's atomic index-exposure receipt, fully verified.

    This is the evidence whose absence left every EARLY forecast unbound. The
    producer now retains, on each publication, the exact index bytes at their
    content address and an immutable receipt naming the replacement clocks and
    the exact CURRENT membership. Everything below is re-derived from bytes: the
    receipt's address, the index snapshot's address, and the child's own content
    address against the membership the receipt asserts. The receipt's completion
    clock is a producer exposure UPPER BOUND -- the operating system exposes no
    single exact replacement instant -- and it is treated as one, never as the
    moment a consumer could first have seen it.
    """

    receipts = [(sha, doc) for sha, doc in
                _content_addressed(exchange_root, *CURRENT_EXPOSURE_DIR, "receipts",
                                   filename="receipt.json")
                if doc.get("contract") == CURRENT_EXPOSURE_CONTRACT]
    candidates = [(sha, doc, member) for sha, doc in receipts
                  for member in (doc.get("members") or [])
                  if str(member.get("forecastId")) == forecast_id]
    if not candidates:
        return None, {"detail": "CURRENT_INDEX_EXPOSURE_RECEIPT_ABSENT"}
    snapshots = dict(_content_addressed(exchange_root, *CURRENT_EXPOSURE_DIR, "indexes",
                                        filename="index.json"))
    child_sha = canonical_child_sha256(package_bytes, forecast_id)
    # THREE-WAY IDENTITY. The receipt member's canonicalGameId, the original
    # child's own eventId and the point-in-time schedule row's nflverseGameId
    # must be the same string. Two of the three agreeing proves nothing: the
    # producer could be consistent with itself about a game that is not this
    # one, and a schedule row alone cannot say which forecast it belongs to.
    schedule_event = str((row or {}).get("nflverseGameId") or "")
    passed: list[tuple[datetime, dict[str, Any]]] = []
    refused: list[dict[str, Any]] = []
    # A forecast that stays CURRENT across several publications has a receipt
    # for each. They are all checked, and the EARLIEST that fully verifies is
    # the exposure: the first publication that put this exact child in the
    # index. Taking the first one found instead would make the answer depend on
    # the order the directory happened to be read in.
    for sha, receipt, member in candidates:
        evidence: dict[str, Any] = {
            "receiptSha256": sha, "status": receipt.get("status"),
            "currentIndexSha256": receipt.get("currentIndexSha256"),
            "previousIndexSha256": receipt.get("previousIndexSha256"),
            "atomicReplaceStartedAtUtc": receipt.get("atomicReplaceStartedAtUtc"),
            "producerExposureUpperBoundUtc": receipt.get("producerExposureUpperBoundUtc"),
            "memberChildSha256": member.get("childSha256"),
            "memberPackageSha256": member.get("forecastPackageSha256"),
            "memberSnapshotVersion": member.get("snapshotVersion"),
            "memberCanonicalGameId": member.get("canonicalGameId"),
            "childEventId": child_event_id,
            "scheduleNflverseGameId": schedule_event or None,
            "producerRevision": (member.get("engine") or {}).get("producerRevision"),
            "clockBasis": ("producerExposureUpperBoundUtc is the atomic replacement's COMPLETION, "
                           "an upper bound on when the index could first have been visible. It is "
                           "never read as a consumer observation."),
        }
        index_sha = str(receipt.get("currentIndexSha256") or "")
        snapshot = snapshots.get(index_sha)
        current = [str(v) for v in ((snapshot or {}).get("currentBundleSha256s") or [])]
        evidence["indexCurrentBundleCount"] = len(current)
        exposure = pilot.aware(receipt.get("producerExposureUpperBoundUtc"))
        unusable = usable_exposure_receipt(receipt)
        member_checks = _team_checks(
            "MEMBER", {"home": member.get("homeTeamId"), "away": member.get("awayTeamId")},
            game, row)
        detail = None
        if unusable:
            detail = unusable
        elif snapshot is None:
            # The index the receipt names must exist at its own address, or the
            # receipt describes a publication nobody can inspect.
            detail = "CURRENT_INDEX_SNAPSHOT_UNVERIFIED"
        elif package_sha256 not in current:
            # The package must actually be CURRENT in the index the receipt
            # names, not merely named by the receipt. That is the difference
            # between "the producer says it published this" and "the published
            # index contains it".
            detail = "PACKAGE_NOT_CURRENT_IN_INDEX_SNAPSHOT"
        elif str(member.get("forecastPackageSha256") or "") != package_sha256:
            detail = "PACKAGE_CHILD_MEMBERSHIP_DISAGREES"
        elif child_sha is None or str(member.get("childSha256") or "") != child_sha:
            detail = "CHILD_CONTENT_ADDRESS_DISAGREES"
        elif str(member.get("snapshotType") or "").upper() != "CURRENT":
            detail = "MEMBER_IS_NOT_A_CURRENT_SNAPSHOT"
        elif str(member.get("snapshotVersion")) != str(snapshot_version):
            # The receipt must attest THIS version. A package can carry several
            # snapshots of one game, so a version that does not match is a
            # receipt for a different object, not for this one.
            detail = "MEMBER_SNAPSHOT_VERSION_DISAGREES"
        elif pilot.aware(member.get("scheduledStartUtc")) != game.starts_at:
            detail = "MEMBER_KICKOFF_DISAGREES"
        elif not schedule_event:
            # A missing or non-unique schedule row leaves identity unresolved.
            detail = "SCHEDULE_IDENTITY_NOT_UNIQUE_FOR_GAME"
        elif not (str(member.get("canonicalGameId") or "") == str(child_event_id or "")
                  == schedule_event):
            detail = "FORECAST_IDENTITY_THREE_WAY_DISAGREES"
        elif member_checks:
            detail = sorted(set(member_checks))[0]
        if detail:
            refused.append(dict(evidence, detail=detail))
        else:
            passed.append((exposure, dict(evidence, detail="VERIFIED")))
    if not passed:
        return None, sorted(refused, key=lambda e: str(e["detail"]))[0]
    # FIRST exposure: the earliest receipt that fully verifies is the earliest
    # instant this exact child is attested to have been in the published index.
    exposure, evidence = min(passed, key=lambda item: item[0])
    evidence = dict(evidence, receiptsConsidered=len(candidates))

    # STILL CURRENT AT THE HORIZON: a first exposure is not enough. The index in
    # force at the horizon is the one from the latest publication at or before
    # it, and this child must still be a CURRENT member of THAT one. Otherwise a
    # forecast the producer has since replaced would go on being treated as the
    # model's view at a time when it no longer was.
    # Only a receipt that passes the SAME allowlist may decide what was in force.
    in_force = [(pilot.aware(doc.get("producerExposureUpperBoundUtc")), doc)
                for _, doc in receipts
                if usable_exposure_receipt(doc) is None
                and pilot.aware(doc.get("producerExposureUpperBoundUtc")) <= cutoff]
    if in_force:
        at_horizon, governing = max(in_force, key=lambda item: item[0])
        evidence["indexInForceAtHorizonUtc"] = pilot.iso(at_horizon)
        evidence["governingIndexSha256"] = governing.get("currentIndexSha256")
        # A matching forecast id in the governing receipt is not enough: the
        # index it names must itself be retained, must still carry this package,
        # and its member must still be this exact child. Anything less would let
        # a republished object of the same id pass as the one we hold.
        governing_member = next((m for m in (governing.get("members") or [])
                                 if str(m.get("forecastId")) == forecast_id), None)
        governing_snapshot = snapshots.get(str(governing.get("currentIndexSha256") or ""))
        governing_current = [str(v) for v in
                             ((governing_snapshot or {}).get("currentBundleSha256s") or [])]
        still = (governing_member is not None
                 and governing_snapshot is not None
                 and package_sha256 in governing_current
                 and str(governing_member.get("forecastPackageSha256") or "") == package_sha256
                 and str(governing_member.get("childSha256") or "") == child_sha
                 and str(governing_member.get("snapshotVersion")) == str(snapshot_version))
        evidence["stillCurrentAtHorizon"] = still
        if not still:
            return None, dict(evidence, detail="NOT_CURRENT_IN_INDEX_AT_HORIZON")
    else:
        evidence["indexInForceAtHorizonUtc"] = None
        evidence["governingIndexSha256"] = None
        evidence["stillCurrentAtHorizon"] = None
    return exposure, evidence


def _verify_child(child: dict[str, Any], game, registry: Any = None) -> tuple[list[str], dict[str, Any]]:
    """The child object's own identity against the canonical schedule.

    Operative, like the market side: a child we cannot tie to this game is not
    this game's forecast, so any failure here prevents BINDING. The child's own
    event id is compared only where AISP holds an authoritative one (which it
    does only once the game is final); pregame that is an absence, not a
    mismatch, and Model Studio's package/child membership artifact is what will
    close it.
    """

    checks: list[str] = []
    evidence = {
        "eventId": child.get("eventId"), "sourceEventId": child.get("sourceEventId"),
        "season": child.get("season"), "seasonType": child.get("seasonType"),
        "week": child.get("week"),
        "homeTeam": (child.get("homeTeam") or {}).get("abbreviation"),
        "awayTeam": (child.get("awayTeam") or {}).get("abbreviation"),
        "scheduledStartUtc": child.get("scheduledStartUtc"),
    }
    start = pilot.aware(child.get("scheduledStartUtc"))
    if start is None or start != game.starts_at:
        checks.append("CHILD_SCHEDULED_START_DISAGREES")
    if game.nflverse_ids and str(child.get("eventId")) not in game.nflverse_ids:
        checks.append("CHILD_EVENT_NOT_BOUND_TO_GAME")
    stated = {side: (child.get(f"{side}Team") or {}).get("abbreviation")
              for side in ("home", "away")}
    checks += _team_checks("CHILD", stated, game, registry)
    return checks, evidence


def _read_package(bundle, forecast_id: str, exchange_root: Path):
    """The original package bytes at their content address, and the child inside."""

    path = _exchange_path(getattr(bundle, "logical_uri", None), exchange_root)
    if path is None:
        return {}, {}, b"", "FORECAST_PACKAGE_UNLOCATED"
    try:
        raw = path.read_bytes()
    except OSError:
        return {}, {}, b"", "FORECAST_PACKAGE_UNREADABLE"
    if bundle.bundle_sha256 and hashlib.sha256(raw).hexdigest() != bundle.bundle_sha256:
        return {}, {}, b"", "FORECAST_PACKAGE_HASH_MISMATCH"
    package = json.loads(raw, parse_float=Decimal)
    child = next((c for c in package.get("forecasts") or [] if c.get("forecastId") == forecast_id), None)
    if child is None:
        return package, {}, raw, "FORECAST_CHILD_NOT_IN_PACKAGE"
    return package, child, raw, None


def _child_probabilities(child: dict[str, Any]):
    """HOME/AWAY/TIE exactly as the producer published them."""

    try:
        values = {"home": Decimal(child["homeWinProbability"]),
                  "away": Decimal(child["awayWinProbability"]),
                  "tie": Decimal(child.get("tieProbability", 0))}
    except (KeyError, InvalidOperation, TypeError):
        return None, "FORECAST_PROBABILITIES_UNREADABLE"
    if any(v < 0 for v in values.values()) or sum(values.values()) != Decimal(1):
        return None, "PUBLISHED_PROBABILITIES_DO_NOT_SUM_TO_ONE"
    if values["home"] + values["away"] == 0:
        return None, "DECISIVE_DENOMINATOR_ZERO"
    return values, None


def bind_forecast(session: Session, game, horizon: str, cutoff: datetime,
                  exchange_root: Path, evidence_dir: str | Path | None = None) -> ForecastBinding:
    """The newest selected-M1 version genuinely available at the horizon, or nothing.

    Every component Model Studio requires must be physically present: the original
    package at its content address, the child inside it, the child's identity
    against the canonical schedule, engine/model/package identity, a real
    generation and publication clock, the import receipt naming this exact package
    inside the run that committed the row, a post-commit consumer bound, and --
    for FINAL -- the readiness family's observed exposure. Anything missing leaves
    the forecast UNBOUND with its reason; nothing is ever substituted.
    """

    manifest, _ = load_schedule_identity(evidence_dir)
    schedule = schedule_row(manifest, game)
    rows = session.execute(
        select(ShadowForecast).where(ShadowForecast.canonical_game_id == uuid.UUID(game.game_id),
                                     ShadowForecast.engine_id == ENGINE_ID)).scalars().all()
    if not rows:
        return ForecastBinding("MISSING_FORECAST", "NO_SELECTED_M1_FORECAST_FOR_GAME", None)
    eligible = []
    unproved_after = []
    other_types: list[str] = []
    reasons: list[str] = []
    for row in rows:
        if horizon == LATE_T26 and str(row.snapshot_type or "").upper() != "FINAL_PREGAME":
            continue
        if horizon == EARLY_T24 and str(row.snapshot_type or "").upper() != "CURRENT":
            # The approved decision is that the early horizon uses the forecast
            # genuinely available then, which is the CURRENT snapshot. It is also
            # the only type the producer's exposure receipts cover. A package can
            # carry an EARLY-typed snapshot too; version numbers are not
            # comparable across types, so it is excluded here by type rather than
            # left to lose a version comparison it was never in.
            other_types.append(str(row.snapshot_type or "").upper() or "UNKNOWN")
            continue
        bundle = session.get(ShadowForecastBundle, row.bundle_id) if row.bundle_id else None
        if bundle is None:
            reasons.append("FORECAST_PACKAGE_UNLOCATED")
            continue
        package, child, package_bytes, failure = _read_package(
            bundle, str(row.forecast_id), exchange_root)
        if failure:
            reasons.append(failure)
            continue
        identity_checks, identity = _verify_child(child, game, schedule)
        blocking = [c for c in identity_checks if c in OPERATIVE_CHILD_IDENTITY]
        if blocking:
            # Operative: a child whose identity we cannot establish is not this
            # game's forecast, so it is never bound. The reason is preserved.
            reasons.extend(blocking)
            continue
        found = _containing_import_run(session, pilot.aware(row.created_at), bundle.bundle_sha256)
        if found is None:
            reasons.append("IMPORT_RUN_LINKAGE_UNPROVED")
            unproved_after.append(pilot.aware(row.available_as_of_utc) or cutoff)
            continue
        run, successor = found
        if successor is None:
            reasons.append("CONSUMER_OBSERVATION_BOUND_NOT_YET_ESTABLISHED")
            unproved_after.append(pilot.aware(row.available_as_of_utc) or cutoff)
            continue
        receipt = _import_receipt_entry(run, bundle.bundle_sha256)
        published = pilot.aware(receipt.get("published_utc"))
        generated = pilot.aware(package.get("generatedAtUtc"))
        if published is None or generated is None:
            reasons.append("FORECAST_GENERATION_OR_PUBLICATION_CLOCK_MISSING")
            continue
        if str(row.snapshot_type or "").upper() == "FINAL_PREGAME":
            exposure, exposure_evidence = _readiness_exposure(exchange_root, str(row.forecast_id))
            if exposure is None:
                reasons.append("FORECAST_READINESS_EXPOSURE_UNPROVED")
                continue
        else:
            # A CURRENT snapshot now has an atomic index-exposure receipt, but
            # only for publications the producer made after it began retaining
            # them. The producer half proves WHAT was published and WHEN; it is
            # half of the requirement, and the consumer half below -- AISP's own
            # import evidence -- must pass independently. A receipt alone is
            # never enrollment.
            exposure, exposure_evidence = _current_exposure(
                exchange_root, str(row.forecast_id), package_sha256=bundle.bundle_sha256,
                package_bytes=package_bytes, game=game, row=schedule,
                snapshot_version=child.get("snapshotVersion"), cutoff=cutoff,
                child_event_id=child.get("eventId"))
            if exposure is None:
                reasons.append(exposure_evidence.get("detail")
                               or "FORECAST_INDEX_EXPOSURE_RECEIPT_UNAVAILABLE")
                continue
        availability = max(exposure, successor)
        if availability > cutoff:
            continue
        values, probability_failure = _child_probabilities(child)
        if values is None:
            reasons.append(probability_failure)
            continue
        decisive = values["home"] / (values["home"] + values["away"])
        sha = str(row.forecast_id or "").rsplit(":", 1)[-1]
        eligible.append(((int(child.get("snapshotVersion") or row.snapshot_version or 0), published, sha), {
            "objectSha256": sha,
            "objectIdentity": row.forecast_id,
            "packageSha256": bundle.bundle_sha256,
            "packageIdentity": package.get("bundleId"),
            "engineId": (child.get("engine") or {}).get("engineId") or row.engine_id,
            "modelVersion": (child.get("engine") or {}).get("engineVersion") or row.engine_version,
            "candidateId": (child.get("engine") or {}).get("candidateId"),
            "modelArtifactSha256": (child.get("engine") or {}).get("modelBinarySha256"),
            "snapshotType": child.get("snapshotType"),
            "snapshotVersion": child.get("snapshotVersion"),
            "homeProbabilityRaw": str(values["home"]),
            "awayProbabilityRaw": str(values["away"]),
            "tieProbabilityRaw": str(values["tie"]),
            "decisiveConditionedHomeProbability": str(decisive),
            "generatedAtUtc": pilot.iso(generated),
            "publishedAtUtc": pilot.iso(published),
            "sourceCutoffUtc": child.get("sourceCutoffUtc"),
            "forecastCutoffUtc": child.get("forecastCutoffUtc"),
            "exposedAtUtc": pilot.iso(exposure),
            "exposureEvidence": exposure_evidence,
            "consumerObservedOrImportCompletedAtUtc": pilot.iso(successor),
            "availabilityAtUtc": pilot.iso(availability),
            "childIdentity": identity,
            "identityChecks": sorted(set(identity_checks)),
            "importRun": {"jobRunId": str(run.id), "jobName": run.job_name,
                          "completedAtUtc": pilot.iso(run.completed_at),
                          "receiptImportedAtUtc": receipt.get("imported_at_utc"),
                          "receiptSourceEvidenceUtc": receipt.get("source_evidence_utc")},
            "clockBasis": {k: CLOCK_BASIS[k] for k in (
                "exposedAtUtc", "consumerObservedOrImportCompletedAtUtc", "availabilityAtUtc",
                "generatedAtUtc")},
        }))
    if not eligible:
        if not reasons and other_types:
            # Say which types were present but not usable, rather than reporting
            # a bare absence that hides what the package actually contained.
            reasons.append("FORECAST_SNAPSHOT_TYPE_NOT_USED_AT_THIS_HORIZON")
        return ForecastBinding("MISSING_FORECAST",
                               sorted(set(reasons))[0] if reasons else "NO_ELIGIBLE_FORECAST_BY_HORIZON",
                               None)
    # NEWEST VERSION, not the latest availability clock: sorting by availability
    # alone can pick an older version that merely happened to be proved later.
    _, chosen = max(eligible, key=lambda item: item[0])
    if any(moment > pilot.aware(chosen["availabilityAtUtc"]) for moment in unproved_after):
        return ForecastBinding("MISSING_FORECAST", "FORECAST_AVAILABILITY_UNPROVED", None)
    return ForecastBinding("BOUND", None, chosen)


# ---------------------------------------------------------------------------
# Horizon selection
# ---------------------------------------------------------------------------
def _seconds_before(start: datetime, moment: datetime | None) -> float | None:
    return None if moment is None else (start - moment).total_seconds()


def select_response(horizon: str, start: datetime,
                    found: list[tuple[Response, dict[str, Any]]]) -> tuple[Response, dict[str, Any]] | None:
    """The frozen selection rule for one horizon, or None (missing, never substituted)."""

    def complete(pair: tuple[Response, dict[str, Any]]) -> bool:
        # A COMPLETE same-book pair, eligible or not. A response whose every
        # candidate has a bad clock is still the authentic response for its
        # horizon; skipping it would silently substitute a different one.
        return any(c.home is not None and c.away is not None
                   for c in _candidates(pair[1], pair[0].captured_at))

    usable = [pair for pair in found
              if pair[1] is not None and pair[0].status == "SUCCEEDED" and complete(pair)]
    if horizon == MARKET_OPEN_PROXY:
        # A PREGAME observation must have been observed before kickoff. The
        # ledger filter bounds when the request was SENT, which does not bound
        # when the response came back: a request sent minutes before kickoff can
        # be captured after it, and those bytes describe a market that has begun
        # to settle. Such a response is not an opening price and is never
        # selected as one.
        pregame = [p for p in usable if p[0].captured_at is not None and p[0].captured_at < start]
        return min(pregame, key=lambda p: (p[0].captured_at, p[0].body_sha256)) if pregame else None
    if horizon == EARLY_T24:
        window = [p for p in usable
                  if EARLY_LATEST <= _seconds_before(start, p[0].captured_at) <= EARLY_EARLIEST]
        if not window:
            return None
        return min(window, key=lambda p: (abs(_seconds_before(start, p[0].captured_at) - EARLY_TARGET),
                                          p[0].captured_at, p[0].body_sha256))
    if horizon == LATE_T26:
        def before(response: Response) -> float | None:
            return _seconds_before(start, response.requested_at)

        # Frozen: primary at T-26, one retry at T-23, nothing at or after T-21.
        # A request counts as the PRIMARY only inside [T-26 - tolerance, T-23);
        # a request inside [T-23, T-21) is a RETRY and is legitimate only after
        # a primary whose failure is terminal AND ledger-bound as zero-charge.
        primaries = [p for p in found if before(p[0]) is not None
                     and LATE_PRIMARY_LATEST <= before(p[0]) <= LATE_PRIMARY_EARLIEST]
        retries = [p for p in found if before(p[0]) is not None
                   and LATE_HARD_STOP < before(p[0]) <= LATE_RETRY]
        if not primaries:
            return None                       # no primary means no legitimate retry either
        primary = min(primaries, key=lambda p: p[0].requested_at)
        if primary[1] is not None and primary in usable                 and _seconds_before(start, primary[0].captured_at) > LATE_HARD_STOP:
            return primary
        verified_uncharged = (primary[0].status == "FAILED" and primary[0].objects_charged == 0)
        if not verified_uncharged:
            return None                       # charged, succeeded-but-empty, or UNKNOWN: no retry
        for retry in sorted(retries, key=lambda p: p[0].requested_at):
            if retry in usable and _seconds_before(start, retry[0].captured_at) > LATE_HARD_STOP:
                return retry
        return None
    raise ValueError(horizon)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------
def observation_id(game: GameRef, horizon: str) -> str:
    payload = f"{CONTRACT}|{PROTOCOL_SHA256}|{game.game_id}|{horizon}|{pilot.iso(game.starts_at)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _conventions(book: str | None) -> tuple[dict[str, Any], bool, list[str]]:
    """The contract's convention block for one book, and whether returns may be simulated."""

    bound = BOUND_CONVENTIONS.get((LEAGUE, f"sgo:{book}"), {}) if book else {}
    block: dict[str, Any] = {name: bound.get(_CONVENTION_NAMES[name]) or "NOT_ESTABLISHED"
                             for name in RETURN_CONVENTIONS}
    document = bound.get("document") or {}
    block["reference"] = {k: document.get(k) for k in ("url", "sha256", "retrievedAtUtc")} if document else None
    reasons = [f"RETURN_CONVENTION_{name.upper()}_NOT_BOUND"
               for name in RETURN_CONVENTIONS if block[name] == "NOT_ESTABLISHED"]
    # The contract requires the exact book AND jurisdiction. Our one bound
    # document is a single state's rules, and the provider never says which
    # jurisdiction a quote comes from, so no quote can be return-eligible.
    if document.get("quoteJurisdiction") in (None, "NOT_STATED_BY_PROVIDER"):
        reasons.append("RETURN_QUOTE_JURISDICTION_NOT_STATED")
    return block, not reasons, reasons


def _leg_block(leg: Leg, response: Response, checkpoint: datetime) -> dict[str, Any]:
    """One leg, with the provider's update clock kept as three separate facts.

    ``lastUpdatedAtRaw`` is exactly what arrived, ``lastUpdatedAtUtc`` is the
    parsed value or null, and ``lastUpdatedAtDisposition`` says which it was.
    A study that finds a null parsed clock can then tell an absent field from a
    value we could not read, without going back to the archived bytes.
    """

    return {
        "legId": leg.leg_id,
        "americanOdds": leg.american,
        "decimalOdds": _dstr(leg.decimal),
        "lastUpdatedAtRaw": leg.last_updated_raw if isinstance(
            leg.last_updated_raw, (str, int, float, type(None))) else str(leg.last_updated_raw),
        "lastUpdatedAtUtc": pilot.iso(leg.last_updated_at),
        "lastUpdatedAtDisposition": leg.clock_disposition,
        "available": leg.available,
        "sourceUpdateAgeSeconds": (
            (checkpoint - leg.last_updated_at).total_seconds() if leg.last_updated_at else None),
        "providerResponseSha256": response.body_sha256,
    }


def build_record(session: Session, game: GameRef, horizon: str, *,
                 exchange_root: Path, evidence_dir: str | Path | None = None) -> dict[str, Any]:
    """One canonical record for one game and horizon. Reads only."""

    # One artifact carries season type, week AND the narrow team alias set.
    manifest, schedule_reason = load_schedule_identity(evidence_dir)
    schedule = schedule_row(manifest, game)
    start = game.starts_at
    checkpoint = start - timedelta(seconds=LATE_CHECKPOINT)
    reasons: list[str] = []
    # A game is EXCLUDED for identity only when its own identity is ambiguous --
    # never for lacking a forecast, and never for a week AISP does not retain.
    if game.home_team_id is None or game.away_team_id is None:
        reasons.append("IDENTITY_UNRESOLVED")
    # EXACTLY ONE retained SportsGameOdds event id, and the chosen response must
    # be that one. Zero, duplicates or a mismatch keep the response and its whole
    # candidate population on the record -- nothing is substituted, nothing is
    # dropped -- and make every candidate predictive-ineligible.
    if not game.sgo_event_ids:
        event_id_failure = "PROVIDER_EVENT_ID_ABSENT_FOR_GAME"
    elif len(game.sgo_event_ids) > 1:
        event_id_failure = "PROVIDER_EVENT_ID_NOT_UNIQUE_FOR_GAME"
    else:
        event_id_failure = None
    if event_id_failure:
        reasons.append(event_id_failure)
    # AISP retains no pregame row carrying season type or week, so they come
    # from the producer's schedule manifest when its row resolves -- never from
    # a forecast, which would make identity depend on the model.
    season = schedule.get("season") if schedule else game.season
    season_type = schedule.get("seasonType") if schedule else game.season_type
    week = schedule.get("week") if schedule else game.week
    if week is None:
        reasons.append("SCHEDULE_WEEK_NOT_RETAINED_PREGAME")
    if season_type is None:
        reasons.append("SCHEDULE_SEASON_TYPE_NOT_RETAINED_PREGAME")
    if schedule is None:
        reasons.append(schedule_reason or "SCHEDULE_IDENTITY_ROW_UNRESOLVED")
    found = _responses_for(session, game, captured_before=start)
    chosen = select_response(horizon, start, found)
    response, event = chosen if chosen else (None, None)
    capture = response.captured_at if response else None
    horizon_checkpoint = {
        MARKET_OPEN_PROXY: capture, EARLY_T24: capture, LATE_T26: checkpoint}[horizon]

    if horizon == MARKET_OPEN_PROXY and response is None and any(
            p[0].captured_at is not None and p[0].captured_at >= start for p in found):
        # A response that came back at or after kickoff is not a pregame
        # observation, whenever it was requested. Said explicitly, so the
        # missing open price is not mistaken for an absent purchase.
        reasons.append("OPEN_PROXY_RESPONSE_NOT_PREGAME")

    every_book = _candidates(event, capture) if event else []
    # The contract's candidate population is every COMPLETE same-book pair. A
    # book offering only one side is not a pair, so it is listed separately
    # rather than silently dropped. Nothing in the authentic response is omitted
    # from the record: malformed pairs stay, one-sided books stay, and the
    # counts below let a reader prove it against the archived bytes.
    response_identity = _verify_response_identity(event, game, schedule) if event else {}
    # ONE place decides what is operative. The provider-event-id failure goes
    # through the same filter as the response checks rather than being unioned
    # in afterwards, so OPERATIVE_MARKET_IDENTITY is the whole answer and cannot
    # drift out of agreement with the code that acts on it.
    identity_failures = sorted(
        {c for c in list(response_identity.get("checks", []))
         + ([event_id_failure] if event_id_failure else [])
         if c in OPERATIVE_MARKET_IDENTITY})
    if response_identity.get("checks"):
        reasons.extend(response_identity["checks"])
        if schedule_reason and any(c.endswith("_TEAM_ALIAS_UNSUPPORTED")
                                   for c in response_identity["checks"]):
            reasons.append(schedule_reason)
        elif schedule is None and any(c.endswith("_TEAM_ALIAS_UNSUPPORTED")
                                      for c in response_identity["checks"]):
            reasons.append("SCHEDULE_IDENTITY_ROW_UNRESOLVED")
    candidates = [c for c in every_book if c.home is not None and c.away is not None]
    incomplete = [c for c in every_book if c.home is None or c.away is None]
    if identity_failures:
        # OPERATIVE: no candidate of a response we cannot tie to this game may
        # be predictively eligible. Every candidate is still written, now
        # carrying the identity reason that disqualified it.
        for candidate in every_book:
            candidate.reasons.extend(identity_failures)
    eligible = [c for c in candidates if c.eligible]
    # Normalized keys decide selection and consensus; the provider's original key
    # is preserved on every candidate. Two different provider keys normalizing to
    # the same string would double-count the consensus and make selection
    # ambiguous, so such a collision is refused rather than resolved silently.
    normalized: dict[str, list[Candidate]] = {}
    for candidate in eligible:
        normalized.setdefault(candidate.normalized_key, []).append(candidate)
    collisions = sorted(k for k, group in normalized.items() if len(group) > 1)
    if collisions:
        eligible = []
        normalized = {}
    selected = min(normalized, default=None)
    no_vigs = {key: no_vig_home(group[0].home.decimal, group[0].away.decimal)
               for key, group in normalized.items()}
    consensus = median(list(no_vigs.values()))
    conventions, return_ok, return_reasons = _conventions(selected)

    if response is None:
        reasons.append({MARKET_OPEN_PROXY: "MISSING_OPEN_PRICE", EARLY_T24: "MISSING_EARLY_PRICE",
                        LATE_T26: "MISSING_LATE_PRICE"}[horizon])
    elif not eligible:
        reasons.append("NO_PREDICTIVE_ELIGIBLE_CANDIDATE")
    if "IDENTITY_UNRESOLVED" in reasons or identity_failures:
        state = STATE_EXCLUDED
    elif response is None or not eligible:
        state = STATE_UNPRICED
    else:
        state = STATE_RETURN if return_ok else STATE_PREDICTIVE
    if state in (STATE_PREDICTIVE,) and not return_ok:
        reasons.extend(return_reasons)

    if horizon == MARKET_OPEN_PROXY:
        binding = ForecastBinding("MISSING_FORECAST", "FORECAST_NOT_BOUND_AT_THIS_HORIZON", None)
    elif horizon == EARLY_T24 and capture is None:
        binding = ForecastBinding("MISSING_FORECAST", "NO_EARLY_CAPTURE_TO_BIND_AGAINST", None)
    else:
        cutoff = capture if horizon == EARLY_T24 else checkpoint
        binding = bind_forecast(session, game, horizon, cutoff, exchange_root, evidence_dir)
    if binding.reason:
        reasons.append(binding.reason)

    decision = None
    if (horizon == EARLY_T24 and binding.forecast and selected and consensus is not None
            and state != STATE_EXCLUDED):
        model = Decimal(binding.forecast["decisiveConditionedHomeProbability"])
        chosen_book = normalized[selected][0]
        # PRIMARY disagreement, and the shadow side, come from the deterministic
        # consensus across eligible books -- not from the book that happens to
        # price it. The selected-book disagreement is reported beside it, and the
        # execution price is still that selected book's quote.
        disagreement = model - consensus
        by_selected = model - no_vigs[selected]
        side = "HOME" if disagreement > 0 else ("AWAY" if disagreement < 0 else None)
        leg = chosen_book.home if side == "HOME" else (chosen_book.away if side == "AWAY" else None)
        decision = {
            "position": "SHADOW_ONLY",
            "executability": "SHADOW_ONLY_NOT_EXECUTABLE",
            "side": side,
            "stakeUnits": ["1", "5"],
            "sportsbookKey": selected,
            "providerSportsbookKey": chosen_book.provider_sportsbook_key,
            "americanOdds": leg.american if leg else None,
            "decimalOdds": _dstr(leg.decimal) if leg else None,
            "priceCapturedAtUtc": pilot.iso(capture),
            "modelDecisiveHome": str(model),
            "selectedBookNoVigHome": str(no_vigs[selected]),
            "consensusNoVigHome": _dstr(consensus),
            "earlyDisagreement": str(disagreement),
            "earlyDisagreementBasis": "model decisive HOME minus the eligible-book consensus no-vig HOME",
            "earlyDisagreementVsSelectedBook": str(by_selected),
            "sideBasis": "sign of the consensus disagreement; no side when it is exactly zero",
            "frozenFrom": ("only the evidence available at the early capture: the forecast "
                           "available by it and the prices inside it. No later forecast, report "
                           "or quote is attached, and this price is not claimed to have remained "
                           "available at any later time."),
        }

    record = {
        "contract": CONTRACT,
        "protocolSha256": PROTOCOL_SHA256,
        "contractSha256": CONTRACT_SHA256,
        "observationId": observation_id(game, horizon),
        "createdAtUtc": None,
        "game": {
            "gameId": game.game_id,
            "season": season,
            "seasonType": season_type,
            "week": week,
            "scheduledStartUtc": pilot.iso(start),
            "homeTeam": {"id": game.home_team_id, "abbr": game.home_abbr},
            "awayTeam": {"id": game.away_team_id, "abbr": game.away_abbr},
            "aliases": {
                "sportsgameodds": game.sgo_event_ids,
                "nflverse": game.nflverse_ids,
                "nflverseFromForecast": game.forecast_alias_ids,
                "nflverseFromScheduleManifest": (
                    [schedule["nflverseGameId"]] if schedule and schedule.get("nflverseGameId")
                    else []),
                "basis": ("AISP's nflverse id is authoritative only once the game is final; the "
                          "one from a forecast is producer-stated and is an alias only, never "
                          "identity. Pregame, season, seasonType and week come from the "
                          "producer's schedule identity manifest, and are null when its row does "
                          "not resolve."),
            },
        },
        "horizon": {
            "type": horizon,
            "scheduledTargetAtUtc": pilot.iso({
                MARKET_OPEN_PROXY: None, EARLY_T24: start - timedelta(seconds=EARLY_TARGET),
                LATE_T26: start - timedelta(seconds=LATE_PRIMARY)}[horizon]),
            "comparisonCheckpointAtUtc": pilot.iso(checkpoint),
            "actualCheckpointAtUtc": pilot.iso(horizon_checkpoint),
            "providerRequestAtUtc": pilot.iso(response.requested_at) if response else None,
            "providerResponseCapturedAtUtc": pilot.iso(capture),
            "fetchedAtUtc": pilot.iso(response.completed_at) if response else None,
            "acquisitionAgeSeconds": (
                (horizon_checkpoint - capture).total_seconds() if capture and horizon_checkpoint else None),
            "selectionBasis": {MARKET_OPEN_PROXY: "earliest-authentic-complete-pair",
                               EARLY_T24: "closest-to-target",
                               LATE_T26: "t26-primary-or-verified-uncharged-t23-retry"}[horizon],
            "clockBasis": {k: CLOCK_BASIS[k] for k in (
                "providerRequestAtUtc", "providerResponseCapturedAtUtc", "fetchedAtUtc")},
        },
        "forecast": binding.forecast,
        "market": {
            "provider": "SPORTSGAMEODDS",
            "providerEventId": event.get("eventID") if event else None,
            "retainedProviderEventIds": game.sgo_event_ids,
            "providerEventIdDisposition": event_id_failure or "EXACTLY_ONE_RETAINED",
            "responseIdentity": response_identity or None,
            "marketType": "FULL_GAME_MONEYLINE",
            "providerResponseSha256": response.body_sha256 if response else None,
            "originalBytesRef": ({"envelopePath": response.envelope_path,
                                  "envelopeSha256": response.envelope_sha256,
                                  "bodyPath": response.body_path,
                                  "bodySha256": response.body_sha256,
                                  "requestFingerprint": response.request_fingerprint,
                                  "ledgerRowId": response.ledger_id,
                                  "objectsCharged": response.objects_charged} if response else None),
            "selectedSportsbookKey": selected,
            "selectedProviderSportsbookKey": (
                normalized[selected][0].provider_sportsbook_key if selected else None),
            "selectionRule": ("lexicographically smallest NORMALIZED sportsbook key among "
                              "predictive-eligible complete pairs; independent of the model and "
                              "of any production decision"),
            "sportsbookKeyBasis": (
                "sportsbookKey is the NORMALIZED key, which is what selection and consensus use. "
                "providerSportsbookKey is the provider's own key, byte-for-byte as received and "
                "never repaired in place. Both are kept on every candidate so a reader can go "
                "back to the archived bytes without guessing how we folded the key."),
            "normalizedKeyCollisions": collisions,
            "population": {
                "booksInResponse": len(every_book),
                "completePairs": len(candidates),
                "oneSidedBooks": len(incomplete),
                "predictiveEligible": len(eligible),
                "basis": ("every book in the authentic response appears below: complete pairs in "
                          "candidates, one-sided books in incompleteBooks. Malformed and "
                          "ineligible pairs are retained with their reasons and are never "
                          "omitted from the population."),
            },
            "jurisdiction": None,
            "conventions": conventions,
            "consensusHomeNoVig": _dstr(consensus),
            "candidates": [{
                "sportsbookKey": c.normalized_key,
                "providerSportsbookKey": c.provider_sportsbook_key,
                "normalizedSportsbookKey": c.normalized_key,
                "predictiveEligible": c.eligible,
                "reasonCodes": sorted(set(c.reasons)),
                "jurisdiction": "NOT_STATED_BY_PROVIDER",
                "homeNoVig": _dstr(no_vigs.get(c.normalized_key)),
                "home": _leg_block(c.home, response, horizon_checkpoint or capture),
                "away": _leg_block(c.away, response, horizon_checkpoint or capture),
            } for c in candidates],
            "incompleteBooks": [{"sportsbookKey": c.normalized_key,
                                 "providerSportsbookKey": c.provider_sportsbook_key,
                                 "normalizedSportsbookKey": c.normalized_key,
                                 "reasonCodes": sorted(set(c.reasons)),
                                 "home": _leg_block(c.home, response, horizon_checkpoint or capture)
                                         if c.home else None,
                                 "away": _leg_block(c.away, response, horizon_checkpoint or capture)
                                         if c.away else None}
                                for c in incomplete],
            "legClockBasis": CLOCK_BASIS["lastUpdatedAtUtc"],
        },
        "eligibility": {
            "state": state,
            "returnEligible": state == STATE_RETURN,
            "forecastDisposition": binding.disposition,
            "reasonCodes": sorted(set(reasons)),
        },
        "hypotheticalDecision": decision,
        # Which producer artifacts this record could consult, and which are still
        # unbound. A reader can tell exactly which conclusions are limited by
        # missing producer evidence rather than by anything AISP chose.
        "producerEvidence": {
            "scheduleIdentityContract": SCHEDULE_IDENTITY_CONTRACT,
            "scheduleManifestSha256": (manifest or {}).get("manifestSha256"),
            "scheduleManifestCreatedAtUtc": (manifest or {}).get("createdAtUtc"),
            "scheduleRowResolved": schedule is not None,
            "scheduleRowDisposition": (
                schedule_reason or ("RESOLVED" if schedule else "SCHEDULE_IDENTITY_ROW_UNRESOLVED")),
            "nflverseGameId": (schedule or {}).get("nflverseGameId"),
            "supportedProviderCodes": {
                side: ((schedule or {}).get(side) or {}).get("supportedProviderCodes")
                for side in ("home", "away")},
            "aliasScope": (manifest or {}).get("aliasScope"),
            "currentExposureContract": CURRENT_EXPOSURE_CONTRACT,
            "basis": ("season, seasonType and week come from the producer's point-in-time "
                      "schedule identity manifest, never from a forecast. The manifest's own "
                      "aispCanonicalGameUuid and sportsGameOddsEventId are null, so the row is "
                      "joined by kickoff AND both canonical teams; anything less resolves to "
                      "nothing and this record fails closed."),
        },
        "lineage": {"supersedes": None, "conflicts": []},
        "outcome": None,
        "exporter": {"identity": EXPORT_IDENTITY, "moduleSha256": module_sha256()},
    }
    return record


# ---------------------------------------------------------------------------
# When may a horizon be written?
# ---------------------------------------------------------------------------
def writable_at(game: GameRef, horizon: str) -> datetime:
    """Every response that could belong to this horizon already exists by then."""

    if horizon == EARLY_T24:
        return game.starts_at - timedelta(seconds=EARLY_LATEST) + SETTLING
    return game.starts_at - timedelta(seconds=LATE_CHECKPOINT) + SETTLING


def in_universe(game: GameRef) -> bool:
    """First eligible after the freeze: every required horizon lies after it."""

    return game.starts_at - timedelta(seconds=EARLY_EARLIEST) > FROZEN_AT


# ---------------------------------------------------------------------------
# Write-once storage, its own hash-chained ledger
# ---------------------------------------------------------------------------
class Ledger:
    """Append-only, hash-chained JSONL carrying THIS protocol's identity."""

    def __init__(self, directory: Path) -> None:
        self.path = Path(directory) / LEDGER_NAME

    def entries(self) -> list[dict[str, Any]]:
        try:
            return [json.loads(line) for line in self.path.read_bytes().splitlines() if line.strip()]
        except FileNotFoundError:
            return []

    def append(self, entry: dict[str, Any], *, now: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            lines = [line for line in self.path.read_bytes().splitlines() if line.strip()]
        except FileNotFoundError:
            lines = []
        body = {"seq": len(lines) + 1,
                "prevLineSha256": hashlib.sha256(lines[-1]).hexdigest() if lines else None,
                "recordedAtUtc": pilot.iso(now), "protocolSha256": PROTOCOL_SHA256,
                "exportIdentity": EXPORT_IDENTITY, **entry}
        with open(self.path, "ab") as stream:
            stream.write(pilot.canonical_bytes(body) + b"\n")


#: Fields that describe STORAGE rather than the observation, and so cannot decide
#: whether two builds saw the same thing.
_BOOKKEEPING = ("createdAtUtc", "lineage")


def _content(record: dict[str, Any]) -> bytes:
    """The bytes that decide sameness: the observation, minus storage bookkeeping.

    The creation clock and the lineage block are written BY the store, so
    comparing them would make every rebuild differ from what it just wrote and
    turn adoption into an endless refusal.
    """

    return pilot.canonical_bytes({k: v for k, v in record.items() if k not in _BOOKKEEPING})


def write_record(directory: Path, record: dict[str, Any], *, ledger: Ledger, now: datetime,
                 correction_reason: str | None = None) -> dict[str, Any]:
    """Write once. Adopt identical bytes. NEVER rebind a closed horizon silently.

    Disposition 7: a horizon closes when its record is written. Later evidence
    arriving afterwards is ordinary lateness, not a correction, and must not
    quietly replace what was recorded. Different bytes are therefore refused and
    logged with the recomputed hash, unless an explicit ``correction_reason`` is
    supplied -- and then the revision carries the prior hash, the reason, and the
    whole retained chain.
    """

    game_dir = Path(directory) / RECORDS_DIR / record["game"]["gameId"] / record["horizon"]["type"]
    existing = sorted(game_dir.glob("*.json")) if game_dir.exists() else []
    chain: list[str] = []
    head = None
    if existing:
        superseded = {json.loads(p.read_bytes()).get("lineage", {}).get("supersedes") for p in existing}
        heads = [p for p in existing if p.stem not in superseded]
        head = heads[-1] if heads else existing[-1]
        chain = [p.stem for p in existing]
        if _content(json.loads(head.read_bytes())) == _content(record):
            return {"observationId": record["observationId"], "created": False,
                    "adopted": head.stem, "horizon": record["horizon"]["type"]}
        if not correction_reason:
            ledger.append({
                "kind": "HORIZON_CLOSED_LATER_EVIDENCE_NOT_WRITTEN",
                "observationId": record["observationId"], "gameId": record["game"]["gameId"],
                "horizon": record["horizon"]["type"], "priorRecordSha256": head.stem,
                "recomputedSha256": hashlib.sha256(_content(record)).hexdigest(),
                "rule": ("A closed horizon is never rebound by ordinary late evidence; a "
                         "correction needs an explicit reason and lineage."),
            }, now=now)
            return {"observationId": record["observationId"], "created": False,
                    "refused": "HORIZON_CLOSED_LATER_EVIDENCE_NOT_WRITTEN",
                    "priorRecordSha256": head.stem, "horizon": record["horizon"]["type"]}
    final = dict(record, createdAtUtc=pilot.iso(now), lineage={
        "supersedes": head.stem if head else None,
        "correctionReason": correction_reason,
        "priorRecordSha256": head.stem if head else None,
        "retainedChain": chain,
        "conflicts": [],
    })
    data = pilot.canonical_bytes(final)
    sha = hashlib.sha256(data).hexdigest()
    path = game_dir / f"{sha}.json"
    write_once(path, data)
    ledger.append({"kind": "CORRECTION_REVISION" if head else "WRITTEN",
                   "observationId": record["observationId"], "gameId": record["game"]["gameId"],
                   "horizon": record["horizon"]["type"], "recordSha256": sha,
                   "supersedes": head.stem if head else None,
                   "correctionReason": correction_reason,
                   "state": record["eligibility"]["state"]}, now=now)
    return {"observationId": record["observationId"], "created": True,
            "file": str(path.relative_to(directory)), "sha256": sha,
            "horizon": record["horizon"]["type"], "state": record["eligibility"]["state"]}


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------
def run_early_line_capture(session: Session, settings: Settings, *,
                           now: datetime | None = None) -> dict[str, Any]:
    """One tick. Inert unless enabled. Reads in one READ ONLY transaction, writes after it."""

    if not getattr(settings, "nfl_early_line_capture_enabled", False):
        return {"enabled": False, "records": 0,
                "note": "NFL_EARLY_LINE_CAPTURE_ENABLED is off; nothing read, nothing written."}
    verify_contract_files(settings.nfl_early_line_contract_dir)
    moment = pilot.aware(now) or datetime.now(UTC)
    directory = Path(settings.nfl_early_line_dir)
    exchange_root = Path(settings.probability_exchange_root)
    # THE PRODUCER EVIDENCE DIRECTORY, ACTUALLY PASSED.
    #
    # It was configured and readable from the first deployment, but the call
    # below omitted `evidence_dir`, so it defaulted to None and every record
    # resolved PREGAME_SCHEDULE_MANIFEST_NOT_SUPPLIED. The setting existed and
    # nothing read it, which is why the first natural ATL-GB record carries a
    # null season type, week, nflverse identity and alias set -- and why any
    # CURRENT exposure binding that needs the schedule row would have failed
    # closed no matter what the producer published.
    evidence_dir = Path(settings.nfl_early_line_producer_evidence_dir)
    built: list[dict[str, Any]] = []
    with read_only_session(session):
        for game in _nfl_games(session, starts_from=moment - timedelta(hours=12),
                               starts_to=moment + timedelta(hours=19)):
            if not in_universe(game):
                continue
            for horizon in HORIZONS:
                if moment < writable_at(game, horizon):
                    continue
                built.append(build_record(session, game, horizon,
                                          exchange_root=exchange_root,
                                          evidence_dir=evidence_dir))
    ledger = Ledger(directory)
    written = [write_record(directory, record, ledger=ledger, now=moment) for record in built]
    return {"enabled": True, "contract": CONTRACT, "protocolSha256": PROTOCOL_SHA256,
            "records": len(built), "created": sum(1 for w in written if w.get("created")),
            "written": written}


def research_export_tick_hook(session: Session, settings: Settings) -> dict[str, Any]:
    """Trailing step of the existing research-export owner. Inert until enabled."""

    return run_early_line_capture(session, settings)


def module_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


# The same guard the model-market exporter applies: no ORM mutation path may
# exist in this module at all, checked when it is imported.
assert_module_has_no_mutation_path(Path(__file__).read_text(encoding="utf-8"))

__all__ = [
    "CONTRACT", "CONTRACT_SHA256", "EARLY_T24", "FROZEN_AT", "HORIZONS", "LATE_T26",
    "MARKET_OPEN_PROXY", "PROTOCOL_SHA256", "VALIDATOR_SHA256", "american_to_decimal",
    "bind_forecast", "build_record", "median", "no_vig_home", "observation_id",
    "research_export_tick_hook", "run_early_line_capture", "select_response", "verify_contract_files",
    "write_record", "writable_at",
]
