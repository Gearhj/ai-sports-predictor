"""Consumer contracts for AISP DAY-BEFORE SHOW PREVIEW V1.

1. PREVIEW FORECAST (``AISP_SHOW_PREVIEW_FORECAST_V1``)
   What a producer (Codex) must publish for AISP to show a model lean on the
   Show Board. AISP validates and stores it; AISP never generates one, never
   relabels a final forecast as a preview, and never substitutes another model.

2. PREVIEW MARKET PAIR
   A same-market, same-sportsbook, same-provider pair of legs, each leg with
   its own sportsbook observation time and retrieval time. Compatibility is
   checked leg against leg: a lone side, a moved line on one side, a different
   book, provider or market row is never a pair.

3. PREVIEW MODEL-MARKET DIFFERENCE
   Research-only arithmetic on an eligible preview forecast and a complete,
   non-stale moneyline pair: model probability, proportional no-vig market
   probability, their raw difference and the expected return at the recorded
   price under the recorded settlement rule. It is not an adjusted edge; no
   preview policy exists, and the FINAL_PREGAME policy is never applied here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

SHOW_PREVIEW = "SHOW_PREVIEW"
FORECAST_SCHEMA = "AISP_SHOW_PREVIEW_FORECAST_V1"
AVAILABILITY_STATES = ("CONFIRMED", "PROBABLE", "UNKNOWN", "NOT_APPLICABLE")
SPORTS = ("MLB", "NFL", "NBA", "CFB")
KICKOFF_TOLERANCE = timedelta(minutes=20)

FORECAST_CONTRACT: dict[str, Any] = {
    "schema_version": FORECAST_SCHEMA,
    "purpose": "Day-before Show Board research. Not a wager, not a governed decision, not final-pregame evidence.",
    "required": {
        "schema_version": FORECAST_SCHEMA,
        "forecast_id": "stable producer id, unique per forecast content",
        "horizon": "exactly SHOW_PREVIEW",
        "sport": "MLB | NFL | NBA | CFB",
        "league": "league code, e.g. MLB",
        "season": "e.g. 2026",
        "game": {
            "canonical_game_id": "AISP games.id when known, else null",
            "source_game_ref": "producer's own game identifier",
            "slate_date": "YYYY-MM-DD in America/New_York",
            "kickoff_utc": "ISO-8601 with offset",
            "away": {"abbreviation": "str", "name": "str"},
            "home": {"abbreviation": "str", "name": "str"},
        },
        "created_utc": "when the forecast was produced",
        "source_cutoff_utc": "latest evidence time used; must be <= created_utc",
        "model": {"model_id": "str", "model_version": "str", "artifact_sha256": "64 hex"},
        "expected_score": {"away": "float", "home": "float"},
        "probabilities": {"away_win": "0..1", "home_win": "0..1", "tie": "0..1; sum of three = 1 +/- 0.001"},
        "availability": {"starters": "|".join(AVAILABILITY_STATES), "lineups": "|".join(AVAILABILITY_STATES)},
        "missing_evidence": [{"code": "UPPER_SNAKE", "message": "plain sentence"}],
        "authority": {"research_only": True, "grants_wagering_authority": False, "final_pregame_evidence": False},
        "integrity": {
            "content_sha256": "SHA-256 of the canonical JSON of this document WITHOUT the integrity block",
            "physical_files": [{"path": "str", "sha256": "64 hex", "bytes": "int"}],
        },
    },
    "optional": {
        "distribution": {"margin_intervals": {"50|80|90": {"lower": "float", "upper": "float"}},
                         "total_intervals": {"50|80|90": {"lower": "float", "upper": "float"}}},
    },
    "rejected": [
        "horizon other than SHOW_PREVIEW (a FINAL or CURRENT forecast is never relabelled)",
        "any authority granting wagering, final-pregame use or position creation",
        "source_cutoff_utc after created_utc, or created_utc at or after kickoff",
        "probabilities outside 0..1 or not summing to 1",
        "content_sha256 that does not match the canonical content",
        "a canonical_game_id whose teams or kickoff disagree with AISP's schedule",
        "a second document under an existing forecast_id with different content",
    ],
}


class ContractViolation(ValueError):
    """A preview forecast that AISP must not store."""


def canonical_bytes(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(document: dict[str, Any]) -> str:
    body = {key: value for key, value in document.items() if key != "integrity"}
    return sha256_hex(canonical_bytes(body))


def _when(value: Any, field: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractViolation(f"{field} is not an ISO-8601 time") from error
    if moment.tzinfo is None:
        raise ContractViolation(f"{field} must carry a UTC offset")
    return moment.astimezone(UTC)


@dataclass(frozen=True)
class PreviewForecast:
    document: dict[str, Any]
    content_sha256: str
    content_bytes: int
    sport: str
    league: str
    season: str
    slate_date: date
    canonical_game_id: str | None
    source_game_ref: str
    away: str
    home: str
    kickoff_utc: datetime
    created_utc: datetime
    source_cutoff_utc: datetime


def validate_forecast(document: dict[str, Any], *, now: datetime) -> PreviewForecast:
    """Check a preview forecast against the contract. Raises ContractViolation."""

    if document.get("schema_version") != FORECAST_SCHEMA:
        raise ContractViolation(f"schema_version must be {FORECAST_SCHEMA}")
    if document.get("horizon") != SHOW_PREVIEW:
        raise ContractViolation("horizon must be exactly SHOW_PREVIEW; a final forecast is never relabelled as a preview")
    authority = document.get("authority") or {}
    if authority.get("research_only") is not True or authority.get("grants_wagering_authority") is not False \
            or authority.get("final_pregame_evidence") is not False:
        raise ContractViolation("authority must be research_only with no wagering or final-pregame authority")
    sport = str(document.get("sport") or "").upper()
    if sport not in SPORTS:
        raise ContractViolation(f"sport must be one of {', '.join(SPORTS)}")
    game = document.get("game") or {}
    for side in ("away", "home"):
        if not (game.get(side) or {}).get("abbreviation"):
            raise ContractViolation(f"game.{side}.abbreviation is required")
    kickoff = _when(game.get("kickoff_utc"), "game.kickoff_utc")
    created = _when(document.get("created_utc"), "created_utc")
    cutoff = _when(document.get("source_cutoff_utc"), "source_cutoff_utc")
    if cutoff > created:
        raise ContractViolation("source_cutoff_utc is after created_utc")
    if created >= kickoff:
        raise ContractViolation("created_utc is not before kickoff")
    if created > now + timedelta(minutes=5):
        raise ContractViolation("created_utc is in the future")
    model = document.get("model") or {}
    if not model.get("model_id") or not model.get("model_version") or len(str(model.get("artifact_sha256") or "")) != 64:
        raise ContractViolation("model.model_id, model.model_version and a 64-hex model.artifact_sha256 are required")
    probs = document.get("probabilities") or {}
    values = [probs.get("away_win"), probs.get("home_win"), probs.get("tie", 0.0)]
    if any(not isinstance(v, (int, float)) or v < 0 or v > 1 for v in values) or abs(sum(values) - 1) > 0.001:
        raise ContractViolation("probabilities must lie in 0..1 and sum to 1")
    score = document.get("expected_score") or {}
    if not all(isinstance(score.get(side), (int, float)) for side in ("away", "home")):
        raise ContractViolation("expected_score.away and expected_score.home are required")
    availability = document.get("availability") or {}
    if availability.get("starters") not in AVAILABILITY_STATES or availability.get("lineups") not in AVAILABILITY_STATES:
        raise ContractViolation("availability.starters and availability.lineups must be declared")
    if not isinstance(document.get("missing_evidence"), list):
        raise ContractViolation("missing_evidence must be a list (empty when nothing is missing)")
    integrity = document.get("integrity") or {}
    digest = content_hash(document)
    if integrity.get("content_sha256") != digest:
        raise ContractViolation("integrity.content_sha256 does not match the canonical content")
    try:
        slate = date.fromisoformat(str(game.get("slate_date")))
    except ValueError as error:
        raise ContractViolation("game.slate_date must be YYYY-MM-DD") from error
    return PreviewForecast(
        document=document, content_sha256=digest, content_bytes=len(canonical_bytes(document)),
        sport=sport, league=str(document.get("league") or sport).upper(), season=str(document.get("season") or ""),
        slate_date=slate, canonical_game_id=game.get("canonical_game_id"),
        source_game_ref=str(game.get("source_game_ref") or ""),
        away=str(game["away"]["abbreviation"]).upper(), home=str(game["home"]["abbreviation"]).upper(),
        kickoff_utc=kickoff, created_utc=created, source_cutoff_utc=cutoff,
    )


# ----------------------------------------------------------- disclosures ----

#: What an MLB preview forecast must say about itself wherever it is shown.
#:
#: These are consumer-side obligations, not a summary of whatever the producer
#: chose to declare. The MLB preview model is a TEAM-HISTORY model -- sixteen
#: score/date features, a logistic winner head and two independent Poisson score
#: heads -- and it is easy to read its expected runs as something it is not: a
#: joint score distribution, an Over/Under probability, or Final V18. Each line
#: below exists because the number beside it would otherwise be read as more
#: than it is.
#:
#: Producer statement of record: asp-model-studio
#: outputs/mlb-show-preview-feasibility-v1 (CLAUDE_HANDOFF.md, MODEL_CARD.md,
#: FROZEN_DESIGN.json), configuration
#: MLB_TEAM_LOGISTIC_C1_POISSON_A1_SHOW_PREVIEW_V1.
MLB_PREVIEW_DISCLOSURES: tuple[dict[str, str], ...] = (
    {"code": "TEAM_HISTORY_MODEL_ONLY",
     "message": ("Team score history only. No pitchers, lineups, injuries, park, "
                 "rest or market inputs.")},
    {"code": "STARTERS_AND_LINEUPS_UNKNOWN",
     "message": "Starters and lineups are unknown, not merely unconfirmed."},
    {"code": "NO_JOINT_SCORE_DISTRIBUTION",
     "message": ("Win probability and the two expected scores are estimated "
                 "separately. They are not one joint distribution and must not be "
                 "read as a simulated population.")},
    {"code": "NO_TOTAL_OR_RUN_LINE_PROBABILITY",
     "message": ("No total or run-line probability is produced. Expected runs are "
                 "not an Over/Under or run-line probability.")},
    {"code": "RESEARCH_ONLY",
     "message": ("Research and editorial only. It carries no decision, stake, "
                 "position or wagering authority.")},
    {"code": "NOT_FINAL_V18",
     "message": "Not the Final V18 forecast, and never a substitute for one.",
     },
)

#: Codes whose claim is conditional on what the package actually says. A
#: disclosure that contradicts the evidence is not a safeguard, it is a second
#: inaccuracy, so each of these is dropped when the package supplies the thing
#: it says is absent.
_CONDITIONAL = {"STARTERS_AND_LINEUPS_UNKNOWN", "NO_JOINT_SCORE_DISTRIBUTION"}


def mandatory_disclosures(sport: str, view: dict[str, Any] | None) -> list[dict[str, str]]:
    """What this sport's preview forecast must disclose wherever it is shown.

    Returns nothing when there is no forecast: an absent forecast is already
    reported as awaiting the producer, and attaching a model's limitations to a
    model nobody published would be describing something that does not exist.
    """

    if view is None or str(sport).upper() != "MLB":
        return []
    starters_known = str(view.get("starters") or "UNKNOWN").upper() != "UNKNOWN"
    lineups_known = str(view.get("lineups") or "UNKNOWN").upper() != "UNKNOWN"
    has_distribution = bool(view.get("distribution"))
    out: list[dict[str, str]] = []
    for item in MLB_PREVIEW_DISCLOSURES:
        code = item["code"]
        if code == "STARTERS_AND_LINEUPS_UNKNOWN" and (starters_known or lineups_known):
            continue
        if code == "NO_JOINT_SCORE_DISTRIBUTION" and has_distribution:
            continue
        out.append(dict(item))
    return out


# --------------------------------------------------------------- markets ----

def settlement_rule(sport: str, market_type: str, line: float | None) -> str:
    if market_type == "MONEYLINE":
        return "TIE_REFUNDS_STAKE" if sport in ("NFL", "CFB") else "TWO_WAY_NO_TIE"
    if line is None:
        return "UNKNOWN"
    return "EXACT_LINE_PUSH_REFUNDS" if float(line).is_integer() else "NO_PUSH_POSSIBLE"


def lines_compatible(market_type: str, legs: dict[str, dict[str, Any]]) -> bool:
    """Both legs describe the same proposition."""

    if market_type == "MONEYLINE":
        return True
    if market_type == "TOTAL":
        over, under = legs.get("OVER", {}).get("point"), legs.get("UNDER", {}).get("point")
        return over is not None and under is not None and abs(float(over) - float(under)) < 1e-9
    home, away = legs.get("HOME", {}).get("point"), legs.get("AWAY", {}).get("point")
    return home is not None and away is not None and abs(float(home) + float(away)) < 1e-9


def no_vig(decimal_side: float, decimal_other: float) -> float:
    a, b = 1 / decimal_side, 1 / decimal_other
    return a / (a + b)


def expected_return(p_win: float, p_loss: float, decimal_price: float) -> float:
    """Per unit staked; a push (tie refund) returns the stake and contributes zero."""

    return p_win * (decimal_price - 1) - p_loss


def preview_difference(*, side: str, probabilities: dict[str, float], decimal_side: float, decimal_other: float,
                       settlement: str) -> dict[str, float]:
    """The research-only Preview model-market difference for one moneyline side."""

    win = probabilities["home_win"] if side == "HOME" else probabilities["away_win"]
    loss = probabilities["away_win"] if side == "HOME" else probabilities["home_win"]
    tie = probabilities.get("tie", 0.0)
    two_way = win / (win + loss) if (win + loss) > 0 else 0.0
    market = no_vig(decimal_side, decimal_other)
    if settlement == "TIE_REFUNDS_STAKE":
        ev = expected_return(win, loss, decimal_side)
    else:
        # No tie can settle this market: the model's tie mass (normally zero) is
        # re-normalised onto the two sides rather than silently scored as a loss.
        ev = expected_return(two_way, 1 - two_way, decimal_side)
    return {
        "model_probability": round(two_way, 6),
        "model_probability_unconditional": round(win, 6),
        "tie_probability": round(tie, 6),
        "no_vig_probability": round(market, 6),
        "difference": round(two_way - market, 6),
        "expected_return": round(ev, 6),
    }
