"""NHL_GEN1_FORECAST_CONTRACT_V1 -- semantic validation, from bytes, and nothing else.

The structural schema (``NHL_GEN1_FORECAST_CONTRACT_V1.schema.json``) says what
keys exist. This module enforces what they MEAN: canonical identity formats,
the content-hash forecast identity, clock order, research-only authority, the
regulation three-way and final two-way probability masses (kept separate), the
goal horizons, goalie status consistency, and the result rules (a regulation
tie goes to OT/SO; a final never ties; a shootout adds one goal to the winner).

Every check appends a named problem code. Nothing is coerced, nothing defaulted.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Any

FORECAST_SCHEMA = "NHL_GEN1_FORECAST_V1"
RESULT_SCHEMA = "NHL_GEN1_RESULT_V1"
FORECAST_MANIFEST_SCHEMA = "NHL_GEN1_FORECAST_MANIFEST_V1"
RESULT_MANIFEST_SCHEMA = "NHL_GEN1_RESULT_MANIFEST_V1"
POINTER_SCHEMA = "NHL_GEN1_CURRENT_POINTER_V1"
CANONICALIZATION = "SPORTS_CANONICAL_JSON_V1"
FORECAST_ID_PREFIX = "nhl-gen1-forecast:sha256:"
#: Fields excluded from the semantic identity: self-reference and the act of
#: publication, exactly as SPORTS_DATASET_MANIFEST_V1 excludes manifest_id/published_at.
IDENTITY_EXCLUDED = ("forecastId", "publishedAtUtc")
RESULT_IDENTITY_EXCLUDED = ("resultSha256",)

MASS_TOLERANCE = 1e-9
CONSISTENCY_TOLERANCE = 1e-6
DISTRIBUTION_TOLERANCE = 1e-6
DIAGONAL_TOLERANCE = 1e-4

GAME_TYPES = {"01": "PRESEASON", "02": "REGULAR", "03": "PLAYOFF"}
GOALIE_STATUSES = ("UNKNOWN", "PROJECTED", "CONFIRMED")
MISSING_INPUTS = frozenset({
    "HOME_GOALIE_UNKNOWN", "AWAY_GOALIE_UNKNOWN", "HOME_GOALIE_UNCONFIRMED", "AWAY_GOALIE_UNCONFIRMED",
    "LINEUPS_UNAVAILABLE", "INJURIES_UNAVAILABLE", "TRAVEL_REST_UNAVAILABLE", "MARKET_PRICES_NOT_USED",
})
REGULATION_HORIZON = "REGULATION_60_MINUTES"
FINAL_HORIZON = "FINAL_INCLUDING_OT_AND_SO_WINNER_GOAL"

_GAME_ID = re.compile(r"^nhl:game:(\d{10})$")
_TEAM_ID = re.compile(r"^nhl:team:(\d{1,3})$")
_PLAYER_ID = re.compile(r"^nhl:player:\d+$")
_FORECAST_ID = re.compile(r"^nhl-gen1-forecast:sha256:[0-9a-f]{64}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")

FORECAST_KEYS = frozenset({
    "schema", "canonicalization", "league", "sport", "forecastId", "supersedesForecastId", "model", "game",
    "researchStatus", "authority", "sourceCutoffUtc", "sourceRetrievedAtUtc", "createdAtUtc", "publishedAtUtc",
    "probabilities", "goals", "goalies", "missingInputs", "limitations", "provenance",
})
EVIDENCE_MODES = ("CONTEMPORANEOUS_RETAINED", "RECONSTRUCTED_HISTORY_CURRENT_FORECAST")
GOALIE_FEATURE_MODES = ("TEAM_LEVEL", "GOALIE_SPECIFIC")
#: A producer limitation marking a document as an invented example. Such a
#: document is never imported as a forecast; only the explicit synthetic lane
#: may hold it, labelled SYNTHETIC_FIXTURE.
SYNTHETIC_MARKER = "SYNTHETIC_FIXTURE_NOT_ADMISSIBLE"
RESULT_KEYS = frozenset({
    "schema", "canonicalGameId", "gameType", "homeCanonicalTeamId", "awayCanonicalTeamId", "regulation",
    "decidedIn", "overtime", "shootout", "final", "supersedesResultSha256", "resultSha256",
})
AUTHORITY_CLAIMS = ("wageringAuthority", "decisionAuthority", "positionAuthority", "modelPromotionAuthority")


# ----------------------------------------------------------------- canonical --
def canonical_bytes(value: Any, *, trailing_newline: bool = True) -> bytes:
    """SPORTS_CANONICAL_JSON_V1: sorted keys, compact separators, UTF-8, no NaN."""

    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return encoded + (b"\n" if trailing_newline else b"")


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def forecast_identity(document: dict[str, Any]) -> str:
    """``nhl-gen1-forecast:sha256:`` + SHA-256 of the canonical doc minus excluded fields."""

    body = {k: v for k, v in document.items() if k not in IDENTITY_EXCLUDED}
    return FORECAST_ID_PREFIX + sha256_hex(canonical_bytes(body, trailing_newline=False))


def result_identity(document: dict[str, Any]) -> str:
    body = {k: v for k, v in document.items() if k not in RESULT_IDENTITY_EXCLUDED}
    return sha256_hex(canonical_bytes(body, trailing_newline=False))


def with_identity(document: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with ``forecastId`` set to its content hash (producer helper, fixtures)."""

    out = copy.deepcopy(document)
    out["forecastId"] = forecast_identity(out)
    return out


def with_result_identity(document: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(document)
    out["resultSha256"] = result_identity(out)
    return out


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not _UTC.match(value):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _fraction(value: Any) -> float | None:
    number = _num(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


def _exact_keys(obj: Any, keys: set[str] | frozenset[str], label: str, problems: list[str]) -> bool:
    if not isinstance(obj, dict):
        problems.append(f"SCHEMA_INVALID:{label}_NOT_OBJECT")
        return False
    missing = sorted(set(keys) - set(obj))
    extra = sorted(set(obj) - set(keys))
    for key in missing:
        problems.append(f"SCHEMA_INVALID:{label}.{key}_MISSING")
    for key in extra:
        problems.append(f"UNKNOWN_FIELD:{label}.{key}")
    return not missing and not extra


# ------------------------------------------------------------------ identity --
def _check_team(team: Any, label: str, problems: list[str]) -> str | None:
    if not _exact_keys(team, {"canonicalTeamId", "sourceTeamId", "triCode", "name"}, label, problems):
        return None
    match = _TEAM_ID.match(str(team.get("canonicalTeamId")))
    if not match or match.group(1) != str(team.get("sourceTeamId")):
        problems.append(f"CANONICAL_ID_MISMATCH:{label}.canonicalTeamId")
        return None
    if not re.match(r"^[A-Z]{3}$", str(team.get("triCode"))):
        problems.append(f"SCHEMA_INVALID:{label}.triCode")
    return str(team["canonicalTeamId"])


def check_game(game: Any, problems: list[str]) -> None:
    keys = {"canonicalGameId", "sourceGameId", "season", "seasonStartYear", "gameType", "scheduledStartUtc",
            "home", "away", "neutralSite", "scheduleSource"}
    if not _exact_keys(game, keys, "game", problems):
        return
    match = _GAME_ID.match(str(game.get("canonicalGameId")))
    source_id = str(game.get("sourceGameId"))
    if not match or match.group(1) != source_id:
        problems.append("CANONICAL_ID_MISMATCH:game.canonicalGameId")
    else:
        year, type_code = int(source_id[:4]), source_id[4:6]
        if GAME_TYPES.get(type_code) != game.get("gameType"):
            problems.append("CANONICAL_ID_MISMATCH:game.gameType")
        if game.get("seasonStartYear") != year or game.get("season") != f"{year}-{str(year + 1)[-2:]}":
            problems.append("CANONICAL_ID_MISMATCH:game.season")
    if parse_utc(game.get("scheduledStartUtc")) is None:
        problems.append("SCHEMA_INVALID:game.scheduledStartUtc")
    home = _check_team(game.get("home"), "game.home", problems)
    away = _check_team(game.get("away"), "game.away", problems)
    if home and away and home == away:
        problems.append("INVALID_ORIENTATION")
    if not isinstance(game.get("neutralSite"), bool):
        problems.append("SCHEMA_INVALID:game.neutralSite")
    _exact_keys(game.get("scheduleSource"), {"source", "observedAtUtc", "retrievedAtUtc"},
                "game.scheduleSource", problems)


# --------------------------------------------------------------- probability --
def _vector(obj: Any, keys: tuple[str, ...], label: str, problems: list[str]) -> dict[str, float] | None:
    if not _exact_keys(obj, set(keys), label, problems):
        return None
    out: dict[str, float] = {}
    for key in keys:
        value = _fraction(obj.get(key))
        if value is None:
            problems.append(f"SCHEMA_INVALID:{label}.{key}_NOT_A_FRACTION")
            return None
        out[key] = value
    return out


def check_probabilities(probs: Any, game_type: Any, problems: list[str]) -> None:
    if not _exact_keys(probs, {"regulation", "final", "overtime"}, "probabilities", problems):
        return
    # v1 rev 2: a final-only model (e.g. Elo) supplies null regulation. Regulation
    # checks run only when the regulation channel is supplied; nothing is inferred.
    reg = (None if probs.get("regulation") is None else
           _vector(probs.get("regulation"), ("home", "tie", "away"), "probabilities.regulation", problems))
    fin = _vector(probs.get("final"), ("home", "away"), "probabilities.final", problems)
    if probs.get("regulation") is None and probs.get("overtime") is not None:
        problems.append("FINAL_ONLY_UNSUPPLIED_CHANNEL:overtime_without_regulation")
    if reg and abs(sum(reg.values()) - 1.0) > MASS_TOLERANCE:
        problems.append("REGULATION_MASS_NOT_ONE")
    if fin and abs(sum(fin.values()) - 1.0) > MASS_TOLERANCE:
        problems.append("FINAL_MASS_NOT_ONE")
    if reg and fin and (fin["home"] + MASS_TOLERANCE < reg["home"] or fin["away"] + MASS_TOLERANCE < reg["away"]):
        problems.append("FINAL_BELOW_REGULATION")
    ot = probs.get("overtime")
    if ot is None:
        return
    if not _exact_keys(ot, {"pOvertime", "pShootout", "conditionalOnTieAfterRegulation", "conditionalOnShootout"},
                       "probabilities.overtime", problems):
        return
    p_ot = _fraction(ot.get("pOvertime"))
    if p_ot is None:
        problems.append("SCHEMA_INVALID:probabilities.overtime.pOvertime")
        return
    if reg and abs(p_ot - reg["tie"]) > CONSISTENCY_TOLERANCE:
        problems.append("OVERTIME_INCONSISTENT:pOvertime_differs_from_regulation_tie")
    p_so_raw = ot.get("pShootout")
    if p_so_raw is not None:
        p_so = _fraction(p_so_raw)
        if p_so is None or p_so > p_ot + CONSISTENCY_TOLERANCE:
            problems.append("OVERTIME_INCONSISTENT:pShootout")
        elif game_type == "PLAYOFF" and p_so > 0:
            problems.append("PLAYOFF_SHOOTOUT_NONZERO")
    for key in ("conditionalOnTieAfterRegulation", "conditionalOnShootout"):
        cond_raw = ot.get(key)
        if cond_raw is None:
            continue
        cond = _vector(cond_raw, ("home", "away"), f"probabilities.overtime.{key}", problems)
        if cond and abs(sum(cond.values()) - 1.0) > MASS_TOLERANCE:
            problems.append(f"OVERTIME_INCONSISTENT:{key}_mass")
        if cond and key == "conditionalOnTieAfterRegulation" and reg and fin:
            implied = reg["home"] + p_ot * cond["home"]
            if abs(implied - fin["home"]) > CONSISTENCY_TOLERANCE:
                problems.append("OVERTIME_INCONSISTENT:regulation_plus_ot_differs_from_final")


# --------------------------------------------------------------------- goals --
def _distribution(dist: Any, label: str, problems: list[str]) -> list[list[float]] | None:
    if dist is None:
        return None
    if not _exact_keys(dist, {"maxGoals", "jointHomeAway"}, label, problems):
        return None
    size = dist.get("maxGoals")
    matrix = dist.get("jointHomeAway")
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 20 or not isinstance(matrix, list) \
            or len(matrix) != size + 1 or any(not isinstance(r, list) or len(r) != size + 1 for r in matrix):
        problems.append(f"GOAL_DISTRIBUTION_INVALID:{label}_shape")
        return None
    cells = [[_fraction(c) for c in row] for row in matrix]
    if any(c is None for row in cells for c in row):
        problems.append(f"GOAL_DISTRIBUTION_INVALID:{label}_cell")
        return None
    if abs(sum(sum(row) for row in cells) - 1.0) > DISTRIBUTION_TOLERANCE:  # type: ignore[arg-type]
        problems.append(f"GOAL_DISTRIBUTION_INVALID:{label}_mass")
        return None
    return cells  # type: ignore[return-value]


def check_goals(goals: Any, probs: Any, problems: list[str]) -> None:
    """Goal means are optional per horizon (v1 rev 2); every distribution key is null in V1."""

    if not _exact_keys(goals, {"regulation", "final"}, "goals", problems):
        return
    reg_probs = (probs or {}).get("regulation") if isinstance(probs, dict) else None
    if reg_probs is None and goals.get("regulation") is not None:
        problems.append("FINAL_ONLY_UNSUPPLIED_CHANNEL:regulation_goals_without_regulation_probabilities")
    horizons = {"regulation": REGULATION_HORIZON, "final": FINAL_HORIZON}
    expected: dict[str, tuple[float, float]] = {}
    for name, horizon in horizons.items():
        block = goals.get(name)
        if block is None:
            continue
        label = f"goals.{name}"
        if not _exact_keys(block, {"horizon", "expectedHome", "expectedAway", "distribution"}, label, problems):
            continue
        if block.get("horizon") != horizon:
            problems.append(f"GOALS_HORIZON_INVALID:{label}")
        if block.get("distribution") is not None:
            # A capped "N or more" matrix cannot identify tie/winner mass in its
            # overflow cell, so V1 transmits no distribution at all.
            problems.append(f"GOAL_DISTRIBUTION_UNSUPPORTED_V1:{label}")
        home, away = _num(block.get("expectedHome")), _num(block.get("expectedAway"))
        if home is None or away is None or home < 0 or away < 0:
            problems.append(f"SCHEMA_INVALID:{label}.expected")
            continue
        expected[name] = (home, away)
    if "regulation" in expected and "final" in expected:
        (rh, ra), (fh, fa) = expected["regulation"], expected["final"]
        if fh + 1e-9 < rh or fa + 1e-9 < ra:
            problems.append("GOALS_HORIZON_INVALID:final_expected_below_regulation")


def check_provenance(provenance: Any, problems: list[str], *,
                     evidence_modes: tuple[str, ...] = EVIDENCE_MODES) -> None:
    if not _exact_keys(provenance, {"evidenceMode", "producerEvidenceRef", "goalieFeatureMode"}, "provenance",
                       problems):
        return
    if provenance.get("evidenceMode") not in evidence_modes:
        problems.append("PROVENANCE_INVALID:evidenceMode")
    if provenance.get("goalieFeatureMode") not in GOALIE_FEATURE_MODES:
        problems.append("PROVENANCE_INVALID:goalieFeatureMode")
    ref = provenance.get("producerEvidenceRef")
    if _exact_keys(ref, {"path", "sha256", "bytes"}, "provenance.producerEvidenceRef", problems):
        size = ref.get("bytes")
        if not isinstance(ref.get("path"), str) or not ref["path"] or not _SHA.match(str(ref.get("sha256"))) \
                or not isinstance(size, int) or isinstance(size, bool) or size < 1:
            problems.append("PROVENANCE_INVALID:producerEvidenceRef")


# -------------------------------------------------------------------- goalies --
def check_goalies(goalies: Any, missing: Any, created: datetime | None, problems: list[str]) -> None:
    if not isinstance(missing, list) or any(m not in MISSING_INPUTS for m in missing) or len(set(missing)) != len(missing):
        problems.append("MISSING_INPUT_UNKNOWN")
        missing = [m for m in missing if isinstance(m, str)] if isinstance(missing, list) else []
    if not _exact_keys(goalies, {"home", "away"}, "goalies", problems):
        return
    for side in ("home", "away"):
        g = goalies.get(side)
        label = f"goalies.{side}"
        if not _exact_keys(g, {"status", "goalieId", "goalieName", "statusSource", "statusObservedAtUtc"},
                           label, problems):
            continue
        status, prefix = g.get("status"), side.upper()
        if status not in GOALIE_STATUSES:
            problems.append(f"GOALIE_STATUS_INCONSISTENT:{label}.status")
            continue
        detail = (g.get("goalieId"), g.get("goalieName"), g.get("statusSource"), g.get("statusObservedAtUtc"))
        if status == "UNKNOWN":
            if any(v is not None for v in detail) or f"{prefix}_GOALIE_UNKNOWN" not in missing:
                problems.append(f"GOALIE_STATUS_INCONSISTENT:{label}_unknown")
            continue
        observed = parse_utc(g.get("statusObservedAtUtc"))
        if not g.get("statusSource") or observed is None or (created and observed > created):
            problems.append(f"GOALIE_STATUS_INCONSISTENT:{label}_source_or_clock")
        if g.get("goalieId") is not None and not _PLAYER_ID.match(str(g.get("goalieId"))):
            problems.append(f"GOALIE_STATUS_INCONSISTENT:{label}.goalieId")
        if status == "PROJECTED" and f"{prefix}_GOALIE_UNCONFIRMED" not in missing:
            problems.append(f"GOALIE_STATUS_INCONSISTENT:{label}_projected_not_flagged")
        if status == "CONFIRMED" and ({f"{prefix}_GOALIE_UNCONFIRMED", f"{prefix}_GOALIE_UNKNOWN"} & set(missing)):
            problems.append(f"GOALIE_STATUS_INCONSISTENT:{label}_confirmed_but_flagged")


# ------------------------------------------------------------------ forecast --
def validate_forecast(document: Any, *, receipt_committed_at: datetime | None = None,
                      allow_unpublished: bool = False,
                      evidence_modes: tuple[str, ...] = EVIDENCE_MODES) -> list[str]:
    """Every problem with ``document``; an empty list means it may be imported.

    ``evidence_modes`` defaults to the producer modes, so an exchange delivery can
    never claim AISP's own in-process mode; only ``app.nhl.inprocess_generator``
    widens it, for the documents it generates itself.
    """

    problems: list[str] = []
    if not _exact_keys(document, FORECAST_KEYS, "forecast", problems):
        if not isinstance(document, dict):
            return problems
    if document.get("schema") != FORECAST_SCHEMA:
        problems.append(f"SCHEMA_INVALID:schema={document.get('schema')!r}")
    if document.get("canonicalization") != CANONICALIZATION:
        problems.append("SCHEMA_INVALID:canonicalization")
    if document.get("league") != "NHL" or document.get("sport") != "HOCKEY":
        problems.append("SCHEMA_INVALID:league_or_sport")
    fid = document.get("forecastId")
    if not isinstance(fid, str) or not _FORECAST_ID.match(fid) or fid != forecast_identity(document):
        problems.append("FORECAST_ID_NOT_CONTENT_HASH")
    sup = document.get("supersedesForecastId")
    if sup is not None and (not isinstance(sup, str) or not _FORECAST_ID.match(sup) or sup == fid):
        problems.append("SCHEMA_INVALID:supersedesForecastId")
    model = document.get("model")
    if _exact_keys(model, {"modelId", "modelVersion", "configurationSha256", "trainingCutoffUtc", "handoffSha256"},
                   "model", problems):
        if not re.match(r"^NHL_GEN1_[A-Z0-9_]+$", str(model.get("modelId"))) or not model.get("modelVersion"):
            problems.append("SCHEMA_INVALID:model.modelId_or_version")
        if not _SHA.match(str(model.get("configurationSha256"))):
            problems.append("SCHEMA_INVALID:model.configurationSha256")
        if model.get("handoffSha256") is not None and not _SHA.match(str(model.get("handoffSha256"))):
            problems.append("SCHEMA_INVALID:model.handoffSha256")
    if document.get("researchStatus") != "RESEARCH_ONLY":
        problems.append("RESEARCH_STATUS_INVALID")
    authority = document.get("authority")
    if not isinstance(authority, dict) or set(authority) != set(AUTHORITY_CLAIMS) or \
            any(authority.get(c) is not False for c in AUTHORITY_CLAIMS):
        problems.append("AUTHORITY_NOT_FALSE")
    game = document.get("game")
    check_game(game, problems)

    cutoff = parse_utc(document.get("sourceCutoffUtc"))
    retrieved = parse_utc(document.get("sourceRetrievedAtUtc"))
    created = parse_utc(document.get("createdAtUtc"))
    published_raw = document.get("publishedAtUtc")
    published = parse_utc(published_raw) if published_raw is not None else receipt_committed_at
    if None in (cutoff, retrieved, created):
        problems.append("CLOCK_ORDER:missing_or_malformed_clock")
    elif not (cutoff <= retrieved <= created):  # type: ignore[operator]
        problems.append("CLOCK_ORDER:cutoff_retrieved_created")
    if published_raw is not None and published is None:
        problems.append("CLOCK_ORDER:publishedAtUtc_malformed")
    elif published is None:
        if not allow_unpublished:
            problems.append("CLOCK_ORDER:no_publication_clock")
    elif created is not None and published < created:
        problems.append("CLOCK_ORDER:published_before_created")
    start = parse_utc((game or {}).get("scheduledStartUtc")) if isinstance(game, dict) else None
    if created is not None and start is not None and created >= start:
        problems.append("FORECAST_NOT_PREGAME")

    game_type = game.get("gameType") if isinstance(game, dict) else None
    check_probabilities(document.get("probabilities"), game_type, problems)
    check_goals(document.get("goals"), document.get("probabilities"), problems)
    check_goalies(document.get("goalies"), document.get("missingInputs"), created, problems)
    check_provenance(document.get("provenance"), problems, evidence_modes=evidence_modes)
    if not isinstance(document.get("limitations"), list) or \
            any(not isinstance(x, str) for x in document.get("limitations") or []):
        problems.append("SCHEMA_INVALID:limitations")
    # Deduplicate, keep order.
    return list(dict.fromkeys(problems))


# -------------------------------------------------------------------- result --
def validate_result(document: Any) -> list[str]:
    problems: list[str] = []
    if not _exact_keys(document, RESULT_KEYS, "result", problems) and not isinstance(document, dict):
        return problems
    if document.get("schema") != RESULT_SCHEMA:
        problems.append("SCHEMA_INVALID:schema")
    gm = _GAME_ID.match(str(document.get("canonicalGameId")))
    game_type = document.get("gameType")
    if not gm:
        problems.append("CANONICAL_ID_MISMATCH:canonicalGameId")
    elif GAME_TYPES.get(gm.group(1)[4:6]) != game_type:
        problems.append("CANONICAL_ID_MISMATCH:gameType")
    home_t, away_t = document.get("homeCanonicalTeamId"), document.get("awayCanonicalTeamId")
    if not _TEAM_ID.match(str(home_t)) or not _TEAM_ID.match(str(away_t)):
        problems.append("CANONICAL_ID_MISMATCH:team")
    elif home_t == away_t:
        problems.append("INVALID_ORIENTATION")
    if document.get("resultSha256") != result_identity(document):
        problems.append("RESULT_SHA_NOT_CONTENT_HASH")
    sup = document.get("supersedesResultSha256")
    if sup is not None and not _SHA.match(str(sup)):
        problems.append("SCHEMA_INVALID:supersedesResultSha256")

    reg = document.get("regulation")
    reg_ok = _exact_keys(reg, {"homeGoals", "awayGoals", "result", "source", "observedAtUtc", "retrievedAtUtc"},
                         "regulation", problems)
    final = document.get("final")
    fin_ok = _exact_keys(final, {"homeScore", "awayScore", "winner", "official", "source", "observedAtUtc",
                                 "retrievedAtUtc", "completedAtUtc"}, "final", problems)
    decided = document.get("decidedIn")
    if decided not in ("REGULATION", "OVERTIME", "SHOOTOUT"):
        problems.append("RESULT_INVALID:decidedIn")
        return list(dict.fromkeys(problems))
    if not (reg_ok and fin_ok):
        return list(dict.fromkeys(problems))

    def _int(v: Any) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None

    rh, ra = _int(reg.get("homeGoals")), _int(reg.get("awayGoals"))
    fh, fa = _int(final.get("homeScore")), _int(final.get("awayScore"))
    if None in (rh, ra, fh, fa):
        problems.append("RESULT_INVALID:goals_not_nonnegative_integers")
        return list(dict.fromkeys(problems))
    for block, label in ((reg, "regulation"), (final, "final")):
        if not block.get("source") or parse_utc(block.get("observedAtUtc")) is None or \
                parse_utc(block.get("retrievedAtUtc")) is None:
            problems.append(f"RESULT_INVALID:{label}_source_or_clock")
    if final.get("completedAtUtc") is not None and parse_utc(final.get("completedAtUtc")) is None:
        problems.append("RESULT_INVALID:final.completedAtUtc")
    expected_reg = "HOME" if rh > ra else "AWAY" if ra > rh else "TIE"  # type: ignore[operator]
    if reg.get("result") != expected_reg:
        problems.append("RESULT_INVALID:regulation_result_disagrees_with_goals")
    if (decided == "REGULATION") != (expected_reg != "TIE"):
        problems.append("RESULT_INVALID:regulation_tie_iff_extra_time")
    if fh == fa:
        problems.append("RESULT_INVALID:final_tied")
    winner = "HOME" if fh > fa else "AWAY"  # type: ignore[operator]
    if final.get("winner") != winner:
        problems.append("RESULT_INVALID:final_winner_disagrees_with_score")
    if not isinstance(final.get("official"), bool):
        problems.append("SCHEMA_INVALID:final.official")

    ot, so = document.get("overtime"), document.get("shootout")
    if decided == "REGULATION":
        if (ot not in (None,) and (not isinstance(ot, dict) or ot.get("played"))) or so is not None:
            problems.append("RESULT_INVALID:regulation_final_with_extra_time")
        if (fh, fa) != (rh, ra):
            problems.append("RESULT_INVALID:final_differs_from_regulation")
        return list(dict.fromkeys(problems))
    if not _exact_keys(ot, {"played", "homeGoals", "awayGoals", "periods"}, "overtime", problems) or \
            ot.get("played") is not True:
        problems.append("RESULT_INVALID:extra_time_without_overtime")
        return list(dict.fromkeys(problems))
    oh, oa = _int(ot.get("homeGoals")), _int(ot.get("awayGoals"))
    if oh is None or oa is None:
        problems.append("RESULT_INVALID:overtime_goals")
        return list(dict.fromkeys(problems))
    if decided == "OVERTIME":
        # Sudden death in every game type: exactly one overtime goal decides it.
        if so is not None or oh + oa != 1:
            problems.append("RESULT_INVALID:overtime_shape")
        if (fh, fa) != (rh + oh, ra + oa):  # type: ignore[operator]
            problems.append("RESULT_INVALID:final_differs_from_regulation_plus_ot")
    else:  # SHOOTOUT
        if game_type == "PLAYOFF":
            problems.append("RESULT_INVALID:playoff_shootout")
        if oh != 0 or oa != 0:
            problems.append("RESULT_INVALID:shootout_after_scoring_overtime")
        if not _exact_keys(so, {"played", "homeScored", "awayScored"}, "shootout", problems) or so.get("played") is not True:
            problems.append("RESULT_INVALID:shootout_missing")
            return list(dict.fromkeys(problems))
        sh, sa = _int(so.get("homeScored")), _int(so.get("awayScored"))
        if sh is None or sa is None or sh == sa:
            problems.append("RESULT_INVALID:shootout_undecided")
        else:
            # NHL scoring convention: the shootout winner is credited ONE goal.
            bonus = (1, 0) if sh > sa else (0, 1)
            if (fh, fa) != (rh + bonus[0], ra + bonus[1]):  # type: ignore[operator]
                problems.append("RESULT_INVALID:final_differs_from_regulation_plus_shootout_goal")
    return list(dict.fromkeys(problems))


__all__ = [
    "EVIDENCE_MODES", "GOALIE_FEATURE_MODES", "SYNTHETIC_MARKER", "check_provenance",
    "CANONICALIZATION", "FINAL_HORIZON", "FORECAST_ID_PREFIX", "FORECAST_MANIFEST_SCHEMA", "FORECAST_SCHEMA",
    "POINTER_SCHEMA", "REGULATION_HORIZON", "RESULT_MANIFEST_SCHEMA", "RESULT_SCHEMA", "canonical_bytes",
    "forecast_identity", "parse_utc", "result_identity", "sha256_hex", "validate_forecast", "validate_result",
    "with_identity", "with_result_identity",
]
