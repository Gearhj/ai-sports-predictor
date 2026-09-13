"""The published September 13 formal assessment, verified and joined.

WHAT THIS PACKAGE IS
--------------------
Scores for forecasts that were already frozen, against finals that are already
governed. It creates no forecast, runs no simulation, fits nothing and promotes
nothing -- the index says so in its own ``authority`` block, and every arm in it
is explicitly not advanced. AISP reads it the way it reads the September 12
catalogue: verify the delivered bytes, join by exact identity, display, and
write nothing back.

It is additive. The earlier ``dc279581…`` catalogue, its explanation joins and
its qualified consumer reports are untouched; this package exists because that
one was published while two governed September 13 revisions were still missing,
and those revisions now exist.

THE JOIN, AND WHY IT NEEDS THREE PARTS
--------------------------------------
Exact ``gamePk`` plus exact ``forecastId`` -- and, where those two are still
ambiguous, the producer's own configuration label.

In this package they are never ambiguous: all five arms carry distinct forecast
ids, and each resolves to exactly one forecast AISP holds. The near miss is
worth noting, though. The survival and transition arms of COL-DET came out of a
single three-arm artifact and their ids differ only in a leading word --
``mlb-v18-survival-e2be2661…`` against ``mlb-v18-transition-e2be2661…`` -- so a
join written against a truncated id, or against the artifact digest, would
attach survival's numbers to transition's card. The arm is therefore part of the
key, and a lookup that reaches more than one row without a matching
``configurationId`` attaches nothing rather than picking one.

TWO BRIER CONVENTIONS, NEITHER CONVERTED
----------------------------------------
The package's MLB Brier is binary: ``(pHome - yHome)^2``, admitted after an
exact zero-tie check. AISP's own research scorer stores the summed two-class
form, which is exactly twice that. Both are correct and they are not the same
number, so both are labelled with their convention and neither is rewritten to
match the other. Halving a value that is already binary would silently improve
it, which is the specific error this module must not make.

DENOMINATORS ARE NOT NORMALISED EITHER
--------------------------------------
The score-limited arm reports over its own 4,722 retained draws; the baseline
reports over 5,000. The producer invented no equal-N pairing and neither does
this. Both counts travel with the numbers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

INDEX_SCHEMA = "MLB_RESEARCH_CLOSEOUT_EXPLANATION_HANDOFF_V1"
ASSESSMENT_SCHEMA = "MLB_EXISTING_PROSPECTIVE_FORMAL_CLOSEOUT_V1"
SCHEMA_VERSION = "AISP_MLB_FORMAL_ASSESSMENT_V1"

#: The published package. Overridable for tests; nothing on this path performs
#: a provider call, a simulation or a database write.
DEFAULT_INDEX = Path(
    r"C:\Users\james\asp-model-exchange\mlb-v18-ensemble-research-v1"
    r"\closeout-explanations-v1\packages\sha256"
    r"\67ea56a6620b13d80dd2020973993fe81abd0258faa9f1f67a502cb4d05181d1\index.json"
)
DEFAULT_INDEX_SHA256 = (
    "67ea56a6620b13d80dd2020973993fe81abd0258faa9f1f67a502cb4d05181d1")

ASSESSMENT_FILE = "formal-assessment.json"

#: Its stated convention, carried with every Brier this module returns so the
#: number can never be read against the other one by accident.
BINARY_BRIER = "BINARY_HOME_WIN_(pHome-yHome)^2"
AISP_SUMMED_BRIER = "AISP_RESEARCH_SCORER_SUMMED_TWO_CLASS"

#: What these two games do and do not establish. Stated here, once, because
#: every surface that shows the numbers has to show this beside them.
CONCLUSIONS = [
    "Survival and transition produced mixed results on COL-DET.",
    "Score limits improved the reported scores on CIN-MIL, while leaving the "
    "winning selection unchanged.",
    "These individual games establish no recurring improvement.",
    "Pitcher-allocation assessment remains unavailable.",
    "No research configuration is promoted.",
]


class FormalAssessmentError(ValueError):
    """The package cannot be verified or joined, and the message says why."""


@dataclass(frozen=True)
class FormalRow:
    """One arm of one game, scored against one governed final revision."""

    game: str
    source_game_id: str
    arm: str
    forecast_id: str
    correct_pick: bool
    winner_pick: str | None
    actual_away_home: list[int]
    final_identity: dict[str, Any]
    metrics: dict[str, Any]
    marginals: dict[str, Any]

    @property
    def is_baseline(self) -> bool:
        return self.arm == "BASELINE"

    @property
    def draw_count(self) -> int | None:
        value = self.metrics.get("simulations") or self.metrics.get("drawCount")
        return int(value) if isinstance(value, (int, float)) else None


def _sha256(path: Path) -> tuple[str, int]:
    raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest(), len(raw)


def verify_package(
    index_path: Path | str = DEFAULT_INDEX,
    *,
    expected_sha256: str | None = DEFAULT_INDEX_SHA256,
) -> dict[str, Any]:
    """Physical bytes first: the index, then every file it declares.

    No normalisation of any kind -- not line endings, not JSON re-serialisation.
    A file whose bytes or length differ from the manifest fails the whole
    package rather than being read anyway.
    """

    path = Path(index_path)
    if not path.is_file():
        raise FormalAssessmentError(f"no formal assessment index at {path}")
    digest, size = _sha256(path)
    if expected_sha256 and digest != expected_sha256:
        raise FormalAssessmentError(
            f"index hashes to {digest}, not the expected {expected_sha256}")

    index = json.loads(path.read_bytes())
    if index.get("schemaVersion") != INDEX_SCHEMA:
        raise FormalAssessmentError(
            f"index schema {index.get('schemaVersion')!r} is not {INDEX_SCHEMA}")

    root = path.parent
    verified: list[str] = []
    for entry in index.get("files") or []:
        member = root / entry["relativePath"]
        if not member.is_file():
            raise FormalAssessmentError(
                f"index names a missing file: {entry['relativePath']}")
        member_digest, member_size = _sha256(member)
        if member_digest != entry["sha256"] or member_size != entry["bytes"]:
            raise FormalAssessmentError(
                f"{entry['relativePath']} does not match its declared bytes")
        verified.append(entry["relativePath"])

    if ASSESSMENT_FILE not in verified:
        raise FormalAssessmentError(
            f"the index does not declare {ASSESSMENT_FILE}")

    assessment = json.loads((root / ASSESSMENT_FILE).read_bytes())
    if assessment.get("schemaVersion") != ASSESSMENT_SCHEMA:
        raise FormalAssessmentError(
            f"assessment schema {assessment.get('schemaVersion')!r} "
            f"is not {ASSESSMENT_SCHEMA}")

    # An index that claimed authority would be refused rather than displayed.
    authority = index.get("authority") or {}
    for claim in ("activatesModel", "changesPolicy", "promotionAllowed",
                  "wageringAuthority"):
        if authority.get(claim):
            raise FormalAssessmentError(
                f"index claims {claim}; this reader attaches research evidence only")

    return {
        "index": index,
        "assessment": assessment,
        "index_sha256": digest,
        "index_bytes": size,
        "files_verified": verified,
        "root": root,
    }


def build_rows(package: dict[str, Any]) -> dict[tuple[str, str, str], FormalRow]:
    """Every scored arm, keyed by (gamePk, forecastId, arm).

    The arm is part of the key because two arms of one game legitimately share a
    forecast id here. A lookup that does not know which arm it wants cannot
    reach a row at all, which is the intended outcome.
    """

    rows: dict[tuple[str, str, str], FormalRow] = {}
    for entry in package["assessment"].get("rows") or []:
        metrics = dict(entry.get("metrics") or {})
        marginals = dict(metrics.pop("marginals", {}) or {})
        key = (str(entry["gamePk"]), str(entry["forecastId"]), str(entry["arm"]))
        if key in rows:
            raise FormalAssessmentError(
                f"the assessment carries two rows for {key}")
        rows[key] = FormalRow(
            game=str(entry.get("game") or ""),
            source_game_id=str(entry["gamePk"]),
            arm=str(entry["arm"]),
            forecast_id=str(entry["forecastId"]),
            correct_pick=bool(entry.get("correctPick")),
            winner_pick=entry.get("winnerPick") or metrics.get("pick"),
            actual_away_home=list(entry.get("actualAwayHome") or []),
            final_identity=dict(entry.get("finalIdentity") or {}),
            metrics=metrics,
            marginals=marginals,
        )
    return rows


@lru_cache(maxsize=4)
def load_assessment(index_path: str | None = None) -> dict[str, Any] | None:
    """Verify and resolve once per process; None when the package is absent.

    Absence is not an error. AISP runs without this package and the comparison
    surface simply reports that no formal assessment is attached.
    """

    path = Path(index_path) if index_path else DEFAULT_INDEX
    if not path.is_file():
        return None
    package = verify_package(path, expected_sha256=(
        DEFAULT_INDEX_SHA256 if path == DEFAULT_INDEX else None))
    package["rows"] = build_rows(package)
    return package


def _crps(marginals: dict[str, Any], key: str) -> float | None:
    node = marginals.get(key) or {}
    value = node.get("crps")
    return float(value) if isinstance(value, (int, float)) else None


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    """Candidate minus baseline, on values the producer already published.

    This is arithmetic on supplied scores, not a new scoring run: it reproduces
    the difference table the package publishes in RESULTS.md, and the tests
    check it against those published figures.
    """

    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _metric_rows(row: FormalRow, baseline: FormalRow | None) -> list[dict[str, Any]]:
    """The published scores, each with its direction and its denominator."""

    def entry(key: str, label: str, value: Any, base: Any) -> dict[str, Any]:
        value = float(value) if isinstance(value, (int, float)) else None
        base = float(base) if isinstance(base, (int, float)) else None
        delta = None if row.is_baseline else _delta(value, base)
        return {
            "key": key,
            "label": label,
            "value": value,
            "baseline_value": base,
            "delta": delta,
            # Every score here is a loss, so lower is better and a negative
            # difference favours the candidate. Stated per metric rather than
            # left to a convention the reader has to remember.
            "favours": (
                None if delta is None or delta == 0
                else ("CANDIDATE" if delta < 0 else "BASELINE")),
        }

    base_metrics = baseline.metrics if baseline else {}
    base_marginals = baseline.marginals if baseline else {}
    return [
        entry("winner_log_loss", "Log loss, winner",
              row.metrics.get("winnerLogLoss"), base_metrics.get("winnerLogLoss")),
        entry("winner_brier", "Binary Brier, winner",
              row.metrics.get("winnerBrier"), base_metrics.get("winnerBrier")),
        entry("crps_away", "CRPS, away runs",
              _crps(row.marginals, "awayRuns"), _crps(base_marginals, "awayRuns")),
        entry("crps_home", "CRPS, home runs",
              _crps(row.marginals, "homeRuns"), _crps(base_marginals, "homeRuns")),
        entry("crps_total", "CRPS, total runs",
              _crps(row.marginals, "totalRuns"), _crps(base_marginals, "totalRuns")),
        entry("crps_margin", "CRPS, run differential",
              _crps(row.marginals, "homeMargin"), _crps(base_marginals, "homeMargin")),
        entry("joint_energy", "Joint energy score",
              row.metrics.get("jointEuclideanEnergyScore"),
              base_metrics.get("jointEuclideanEnergyScore")),
    ]


def assessment_for(
    *,
    source_game_id: str | None,
    forecast_id: str | None,
    configuration_id: str | None = None,
    index_path: str | None = None,
) -> dict[str, Any] | None:
    """The formal assessment for this EXACT game, forecast and arm, or nothing.

    ``configuration_id`` is the producer's own label as AISP stored it. It is
    required only where the game/forecast pair alone reaches more than one arm,
    and in that case an absent or unmatched label returns nothing rather than an
    arbitrary arm.
    """

    if not source_game_id or not forecast_id:
        return None
    package = load_assessment(index_path)
    if package is None:
        return None

    rows: dict[tuple[str, str, str], FormalRow] = package["rows"]
    candidates = [
        row for (game, forecast, _), row in rows.items()
        if game == str(source_game_id) and forecast == str(forecast_id)
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        matched = [row for row in candidates if row.arm == str(configuration_id or "")]
        if len(matched) != 1:
            # Two arms, one forecast id, no label to tell them apart. Attaching
            # either would be a coin flip presented as evidence.
            return None
        candidates = matched

    row = candidates[0]
    baseline = next(
        (other for (game, _, arm), other in rows.items()
         if game == row.source_game_id and arm == "BASELINE"),
        None,
    )
    assessment = package["assessment"]
    decisions = assessment.get("decisions") or {}
    score_limited = assessment.get("scoreLimited") or {}

    return {
        "schema_version": SCHEMA_VERSION,
        "assessment_schema_version": assessment.get("schemaVersion"),
        "index_sha256": package["index_sha256"],
        "created_at_utc": assessment.get("createdAtUtc"),
        "classification": assessment.get("classification"),
        "game": row.game,
        "source_game_id": row.source_game_id,
        "arm": row.arm,
        "forecast_id": row.forecast_id,
        "is_baseline": row.is_baseline,
        "baseline_forecast_id": baseline.forecast_id if baseline else None,
        "correct_pick": row.correct_pick,
        "winner_pick": row.winner_pick,
        "actual_away_home": row.actual_away_home,
        "draw_count": row.draw_count,
        "baseline_draw_count": baseline.draw_count if baseline else None,
        # Unequal N is a fact about the arm, not a defect to be normalised away.
        "equal_denominator": (
            None if baseline is None or row.draw_count is None
            else row.draw_count == baseline.draw_count),
        "home_win_probability": row.metrics.get("homeWinProbability"),
        "brier_convention": BINARY_BRIER,
        "brier_note": (
            "This Brier is binary, (pHome - yHome)^2. AISP's own research "
            "scorer stores the summed two-class form, which is exactly twice "
            "this value. Neither number is converted into the other."
        ),
        "metrics": _metric_rows(row, baseline),
        "final_identity": {
            "canonical_game_id": row.final_identity.get("gameId"),
            "source_outcome_id": row.final_identity.get("sourceOutcomeId"),
            "source_revision": row.final_identity.get("sourceRevision"),
            "status": row.final_identity.get("status"),
            "away_score": row.final_identity.get("awayScore"),
            "home_score": row.final_identity.get("homeScore"),
            "final_at_utc": row.final_identity.get("finalAt"),
            "lineage_basis": row.final_identity.get("lineageBasis"),
        },
        "decision": decisions.get(_decision_key(row.arm)),
        "conclusions": list(CONCLUSIONS),
        "score_limited": (
            {
                "original_draw_count": score_limited.get("originalDrawCount"),
                "retained_draw_count": score_limited.get("retainedDrawCount"),
                "excluded_draw_count": score_limited.get("excludedDrawCount"),
                "exclusion_rate": score_limited.get("exclusionRate"),
                "picks_changed": (
                    int(score_limited.get("rightToWrong") or 0)
                    + int(score_limited.get("wrongToRight") or 0)),
            }
            if row.arm == "SCORE_LIMITED" else None
        ),
        "pitcher_allocation": assessment.get("pitcherAllocationAssessment"),
        "promoted": False,
        "authority_note": (
            "Research evidence only. This assessment activates no model, "
            "changes no policy, promotes no configuration and carries no "
            "wagering authority."
        ),
        "sample_note": (
            "One game, one arm. A difference on a single realized result is not "
            "evidence of recurring improvement and says nothing about "
            "profitability at an offered price."
        ),
    }


def _decision_key(arm: str) -> str:
    return {
        "V18_SURVIVAL_ONLY": "survival",
        "V18_TRANSITION_ONLY": "transition",
        "SCORE_LIMITED": "scoreLimited",
    }.get(arm, "")


def package_summary(index_path: str | None = None) -> dict[str, Any] | None:
    """What was verified and acknowledged, for the operator-facing surfaces."""

    package = load_assessment(index_path)
    if package is None:
        return None
    index = package["index"]
    assessment = package["assessment"]
    coverage = index.get("coverage") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "index_sha256": package["index_sha256"],
        "index_bytes": package["index_bytes"],
        "files_verified": package["files_verified"],
        "created_at_utc": index.get("createdAtUtc"),
        "artifact_kind": index.get("artifactKind"),
        "governed_study_games_scored": coverage.get("governedStudyGamesScored"),
        "research_interventions": coverage.get("researchInterventions"),
        "original_baseline_draws_per_game": coverage.get("originalBaselineDrawsPerGame"),
        "score_limited_retained_draws": coverage.get("scoreLimitedRetainedDraws"),
        "previous_qualified_package_sha256": (
            (index.get("previousQualifiedPackage") or {}).get("sha256")),
        "qualification_resolved": index.get("qualificationResolved"),
        "decisions": assessment.get("decisions"),
        "conclusions": list(CONCLUSIONS),
        "pitcher_allocation": assessment.get("pitcherAllocationAssessment"),
        "promoted": False,
    }


__all__ = [
    "AISP_SUMMED_BRIER",
    "ASSESSMENT_SCHEMA",
    "BINARY_BRIER",
    "CONCLUSIONS",
    "DEFAULT_INDEX",
    "DEFAULT_INDEX_SHA256",
    "SCHEMA_VERSION",
    "FormalAssessmentError",
    "FormalRow",
    "assessment_for",
    "build_rows",
    "load_assessment",
    "package_summary",
    "verify_package",
]
