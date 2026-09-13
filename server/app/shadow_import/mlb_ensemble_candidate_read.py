"""One honest side-by-side: the frozen baseline and the ensemble candidate.

WHAT THIS SHOWS
---------------
Two forecasts of the same game, from two separate research tracks, with their
difference stated plainly. Plus what the candidate actually did: which
components ran, which fell back and why, when its evidence was cut, and whether
it is a genuine pregame forecast or a labelled replay.

WHAT IT REFUSES TO SHOW
-----------------------
*   **Per-component probability contributions**, unless Model Studio supplied a
    real attribution. A difference of 1.8 points between a base and an ensemble
    is not "calibration contributed 1.2 and duration 0.6"; decomposing a
    composed model by subtraction invents an additivity the model does not
    have. Without a supplied attribution the surface shows which components ran
    and the total difference, and says the split is unavailable.
*   **A calibration band described as a difficult matchup.** A wide band is a
    statement about the calibration component's confidence, not a discovery
    about the two clubs. It is labelled as the former.
*   **Any suggestion of authority.** The candidate has none. Neither track here
    is the controlling model for official decisions, and the payload says which
    engine is.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.explain.formal_assessment import (
    AISP_SUMMED_BRIER,
    assessment_for,
    package_summary,
)
from app.models import ResearchForecastEvaluation, ShadowForecast
from app.models.entities import ModelChampionAuthority
from app.shadow_import.mlb_ensemble_candidate import (
    ENGINE_ID as CANDIDATE_ENGINE_ID,
    HISTORICAL_REPLAY,
    PROSPECTIVE,
    ROLE_BASE,
)
from app.shadow_import.mlb_v18 import ENGINE_ID as BASELINE_ENGINE_ID

SCHEMA_VERSION = "AISP_MLB_ENSEMBLE_CANDIDATE_COMPARISON_V1"
COHORT_SCHEMA_VERSION = "AISP_MLB_ENSEMBLE_CANDIDATE_COHORT_V1"

#: The attribution key Model Studio must supply for a per-component split to be
#: shown. Absent it, the surface shows the total difference only.
ATTRIBUTION_KEY = "componentAttribution"

REPLAY_NOTE = (
    "This candidate forecast is a labelled historical replay. It is evidence "
    "about the model, not a forecast that existed before the game. It is "
    "excluded from prospective performance, and it can reach no decision and "
    "no position."
)
PROSPECTIVE_NOTE = (
    "This candidate forecast was published before first pitch. It is research "
    "evidence only: the candidate holds no wagering authority and is not the "
    "controlling model for any official decision."
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _round(value: Any, places: int = 4) -> float | None:
    return round(float(value), places) if isinstance(value, (int, float)) else None


def _components(row: ShadowForecast) -> list[dict[str, Any]]:
    """What each declared component did on this game, fallbacks included."""

    detail = dict(row.readiness_detail or {})
    lineage = dict(row.lineage_detail or {})
    # Two producers, two shapes. The generic contract publishes `components` as
    # a list of {componentId, role, sha256, status}; Model Studio's release
    # candidate publishes it as a dict keyed by component. Either is read here,
    # and when neither carries a role the applied record's own role is used --
    # rather than showing a blank where a component's job should be.
    raw = lineage.get("components")
    declared: dict[str, Any] = {}
    if isinstance(raw, list):
        declared = {str(e.get("componentId")): e for e in raw if isinstance(e, dict)}
    elif isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            component_id = str(value.get("name") or key)
            declared[component_id] = {**value, "componentId": component_id}
    applied: list[dict[str, Any]] = []
    for entry in detail.get("componentsApplied") or lineage.get("componentsApplied") or []:
        if not isinstance(entry, dict):
            continue
        component_id = str(entry.get("componentId") or "")
        declaration = declared.get(component_id, {})
        applied.append({
            "component_id": component_id,
            "role": declaration.get("role") or entry.get("role"),
            "status": declaration.get("status") or entry.get("status"),
            "sha256": declaration.get("sha256"),
            "ran": bool(entry.get("ran")),
            "fallback_reason": entry.get("fallbackReason"),
            "detail": entry.get("detail"),
        })
    return applied


def _score_for(session: Session, row: ShadowForecast) -> dict[str, Any] | None:
    """The current research score for this forecast, when one exists."""

    evaluation = session.scalar(
        select(ResearchForecastEvaluation).where(
            ResearchForecastEvaluation.shadow_forecast_id == row.id,
            ResearchForecastEvaluation.is_current.is_(True),
        )
    )
    if evaluation is None:
        return None
    clock = (evaluation.detail or {}).get("publication_clock") or {}
    return {
        "evaluation_id": str(evaluation.id),
        "scoring_version": evaluation.scoring_version,
        "evaluated_at": _iso(evaluation.evaluated_at),
        "home_score": evaluation.home_score,
        "away_score": evaluation.away_score,
        "actual_outcome": evaluation.actual_outcome,
        "predicted_outcome": evaluation.predicted_outcome,
        "winner_correct": evaluation.winner_correct,
        "probability_of_actual": _round(evaluation.probability_of_actual),
        "log_loss": _round(evaluation.log_loss),
        "brier_score": _round(evaluation.brier_score),
        "margin_error": _round(evaluation.margin_error),
        "total_error": _round(evaluation.total_error),
        "crps_home": _round(evaluation.crps_home),
        "crps_away": _round(evaluation.crps_away),
        "crps_margin": _round(evaluation.crps_margin),
        "crps_total": _round(evaluation.crps_total),
        "energy_score": _round(evaluation.energy_score),
        # An absent distribution is absent, not zero. Both fields travel
        # together so a reader is never left to guess whether a blank CRPS
        # means "perfect" or "not computed".
        "distribution_available": bool(evaluation.distribution_available),
        "distribution_unavailable_reason": evaluation.distribution_unavailable_reason,
        "distribution_draws": evaluation.distribution_draws,
        # Which result this score was computed against. Two arms of one
        # comparison must carry the same value or the comparison is between
        # two different games.
        "result_identity": evaluation.result_identity,
        "result_source": evaluation.result_source,
        "result_tier": evaluation.result_tier,
        "result_observed_utc": _iso(evaluation.result_observed_utc),
        "publication_clock": clock.get("classification"),
        "publication_clock_verified": clock.get("verified"),
    }


def _side(session: Session, row: ShadowForecast, label: str, track: str) -> dict[str, Any]:
    lineage = dict(row.lineage_detail or {})
    artifact_type = str(lineage.get("artifactType") or PROSPECTIVE)
    home = row.full_home_win_probability
    return {
        "track": track,
        "label": label,
        "engine_id": row.engine_id,
        "engine_version": row.engine_version,
        "forecast_id": row.forecast_id,
        "model_artifact_sha256": row.model_artifact_sha256,
        "artifact_type": artifact_type,
        "is_replay": artifact_type == HISTORICAL_REPLAY,
        "home_win_probability": _round((home or 0.0) * 100, 4),
        "away_win_probability": _round((row.full_away_win_probability or 0.0) * 100, 4),
        "expected_home_runs": _round(row.full_expected_home_score),
        "expected_away_runs": _round(row.full_expected_away_score),
        "evidence_cutoff_utc": _iso(row.source_cutoff_utc),
        "published_utc": _iso(row.available_as_of_utc),
        "imported_utc": _iso(row.created_at),
        "scheduled_start_utc": _iso(row.scheduled_start_utc),
        "simulation_count": (row.uncertainty or {}).get("simulationCount")
        or (row.uncertainty or {}).get("count"),
        "wagering_authority": False,
        "execution_allowed_in_aisp": False,
        "score": _score_for(session, row),
        # Joined on THIS side's exact forecast id and game, never on the game
        # alone: a baseline and a candidate of one game are different forecasts
        # and must never receive each other's published scores.
        "formal_assessment": assessment_for(
            source_game_id=row.source_event_id,
            forecast_id=row.forecast_id,
            configuration_id=(
                (row.lineage_detail or {}).get("configurationId")
                or row.engine_version),
        ),
    }


#: Scores where a SMALLER number is the better one, so "candidate minus
#: baseline, negative favours the candidate" holds for every row -- and the
#: direction is still stated per metric rather than left to a sign.
#:
#: The third element says whether the stored value is already a loss or has to
#: be made into one. `margin_error` and `total_error` are stored SIGNED
#: (predicted minus observed, see forecast_evaluation.py), so ranking them
#: directly says a forecast that missed by -5.54 beat one that missed by -5.49.
#: It is the MAGNITUDE that is the loss, and taking it is what makes the
#: comparison mean what the column header says.
_LOSS_METRICS = (
    ("log_loss", "Log loss", False),
    ("brier_score", "Brier score", False),
    ("crps_home", "CRPS, home runs", False),
    ("crps_away", "CRPS, away runs", False),
    ("crps_margin", "CRPS, run differential", False),
    ("crps_total", "CRPS, total runs", False),
    ("energy_score", "Energy score", False),
    ("margin_error", "Absolute margin error", True),
    ("total_error", "Absolute total error", True),
)


def _loss_value(score: dict[str, Any] | None, key: str, absolute: bool) -> float | None:
    """The stored value as a loss, or None when this side did not compute it."""

    value = (score or {}).get(key)
    if not isinstance(value, (int, float)):
        return None
    return abs(float(value)) if absolute else float(value)


def _score_difference(
    candidate: dict[str, Any] | None, baseline: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Candidate minus baseline for every rule both sides actually scored.

    A metric one side did not compute is omitted rather than defaulted: a
    missing CRPS is not a CRPS of zero, and subtracting against zero would
    manufacture an improvement out of an absent distribution.
    """

    if not candidate or not baseline:
        return None
    metrics: list[dict[str, Any]] = []
    for key, label, absolute in _LOSS_METRICS:
        theirs = _loss_value(baseline, key, absolute)
        ours = _loss_value(candidate, key, absolute)
        if ours is None or theirs is None:
            continue
        delta = round(ours - theirs, 6)
        metrics.append({
            "key": key,
            "label": label,
            "candidate": ours,
            "baseline": theirs,
            "delta": delta,
            "favours": "CANDIDATE" if delta < 0 else "BASELINE" if delta > 0 else "NEITHER",
        })
    if not metrics:
        return None
    return {
        "convention": "candidate minus baseline",
        "direction": (
            "Every metric here is a loss, so a NEGATIVE difference means the "
            "candidate lost less than the baseline on this game. A positive "
            "difference means it lost more."
        ),
        "metrics": metrics,
    }


def _shared_final(
    candidate: dict[str, Any] | None, baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    """Whether both arms were scored against the same recorded result.

    Two scores computed against different results are not a comparison, and the
    difference between them would be partly a difference of outcome. Rather than
    assume one pass produced both, the identities are compared and reported.
    """

    ours = (candidate or {}).get("result_identity")
    theirs = (baseline or {}).get("result_identity")
    if ours is None or theirs is None:
        return {
            "status": "UNKNOWN",
            "candidate_result_identity": ours,
            "baseline_result_identity": theirs,
            "detail": "At least one side has no score yet, so there is nothing to compare.",
        }
    matched = str(ours) == str(theirs)
    return {
        "status": "SHARED" if matched else "DIVERGENT",
        "candidate_result_identity": ours,
        "baseline_result_identity": theirs,
        "detail": (
            "Both arms were scored against the same recorded final."
            if matched else
            "The two arms were scored against DIFFERENT recorded finals, so the "
            "difference between them is not a like-for-like comparison."
        ),
    }


def _governed_final(session: Session, game_id: uuid.UUID) -> dict[str, Any] | None:
    """The governed outcome record for this game, when one is persisted.

    Reported beside the score's own result identity rather than in place of it:
    the research scorer reads FINAL state observations, and this row is the
    separately governed, revisioned record Model Studio reads. Showing both is
    how a reader can tell they agree.
    """

    row = session.execute(text(
        "select revision, outcome_status, home_score, away_score, final_at,"
        " payload_checksum, source_record_id, id"
        " from game_final_outcomes where game_id = :g"
        " order by revision desc limit 1"
    ), {"g": str(game_id)}).first()
    if row is None:
        return None
    return {
        "revision": row[0],
        "outcome_status": row[1],
        "home_score": row[2],
        "away_score": row[3],
        "final_at_utc": _iso(row[4]),
        "payload_checksum": row[5],
        "source_record_id": row[6],
        # The producer's packages name this id and revision explicitly, so the
        # consumer can check it scored the same result rather than assume it.
        "source_outcome_id": str(row[7]),
    }


def _formal_assessment_block(
    session: Session,
    game_id: uuid.UUID,
    candidate: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The published assessment for this comparison, with its identity checked.

    Two things are reported rather than assumed. First, whether the governed
    final the producer scored against is the one AISP holds -- same outcome id,
    same revision. A package that scored a different revision would still
    display its numbers, clearly marked as bound to a different result, because
    silently showing them beside AISP's final is how two results become one.

    Second, that the two Brier conventions differ. The producer's is binary and
    AISP's own scorer stores the summed two-class form, exactly twice it. Both
    appear on the same screen, so the relationship is stated and neither value
    is converted.
    """

    attached = [
        row for row in (
            (candidate or {}).get("formal_assessment"),
            (baseline or {}).get("formal_assessment"),
        ) if row
    ]
    if not attached:
        return None

    governed = _governed_final(session, game_id)
    identities = {
        (
            str(row["final_identity"].get("source_outcome_id") or ""),
            row["final_identity"].get("source_revision"),
        )
        for row in attached
    }
    if governed is None:
        agreement = {
            "status": "NO_GOVERNED_FINAL_PERSISTED",
            "detail": (
                "AISP holds no governed final for this game, so the assessment's "
                "result identity cannot be confirmed against one."
            ),
        }
    elif len(identities) > 1:
        agreement = {
            "status": "ARMS_SCORED_DIFFERENT_FINALS",
            "detail": (
                "The attached arms name different final identities, so their "
                "differences would be partly differences of outcome."
            ),
        }
    else:
        outcome_id, revision = next(iter(identities))
        matches = (
            outcome_id == str(governed.get("source_outcome_id") or "")
            and revision == governed.get("revision")
        )
        agreement = {
            "status": "SAME_GOVERNED_FINAL" if matches else "DIFFERENT_GOVERNED_FINAL",
            "detail": (
                "The assessment scored the governed final revision AISP holds."
                if matches else
                "The assessment scored a different final revision from the one "
                "AISP holds; its scores belong to that revision, not this one."
            ),
            "assessment_outcome_id": outcome_id,
            "assessment_revision": revision,
            "aisp_outcome_id": governed.get("source_outcome_id"),
            "aisp_revision": governed.get("revision"),
        }

    summary = package_summary() or {}
    return {
        "available": True,
        "package": summary,
        "final_identity_agreement": agreement,
        "brier_conventions": {
            "published": attached[0].get("brier_convention"),
            "aisp_research_scorer": AISP_SUMMED_BRIER,
            "relationship": (
                "AISP's stored Brier is the summed two-class form and is exactly "
                "twice the published binary Brier. Both are shown as published; "
                "neither is halved or doubled to match the other."
            ),
        },
        "conclusions": attached[0].get("conclusions"),
        "promoted": False,
    }


def _latest(session: Session, game_id: uuid.UUID, engine_id: str) -> ShadowForecast | None:
    return session.scalar(
        select(ShadowForecast)
        .where(
            ShadowForecast.canonical_game_id == game_id,
            ShadowForecast.engine_id == engine_id,
        )
        .order_by(
            ShadowForecast.available_as_of_utc.desc(),
            ShadowForecast.created_at.desc(),
        )
        .limit(1)
    )


# ------------------------------------------------- configuration identity --
#
# One engine family can publish several configurations of the same game --
# survival and transition are two arms of one comparison, not two vintages of
# one thing. "Latest candidate" therefore picks an arbitrary arm, and the newest
# import silently becomes what every surface shows. Each configuration is
# addressed explicitly instead.
#
# The key is the candidate's own ``engine_version``: it is stored on every row,
# it is distinct per configuration by construction, and it is the producer's
# string rather than one AISP invented. ``configuration_id`` is the producer's
# label (V18_SURVIVAL_ONLY), shown when the package supplied it.


def _configuration_key(row: ShadowForecast) -> str:
    return str(row.engine_version or "")


def _score_limit(row: ShadowForecast) -> dict[str, Any] | None:
    """The filter this configuration applied, when it applied one.

    A SCORE_LIMIT configuration is not a component trial and not a fallback. It
    keeps a SUBSET of the baseline's own completed draws -- the ones that stayed
    inside the published limits -- and reports every summary over that subset.
    Nothing is clipped: an accepted draw is the baseline's draw unchanged.

    That makes it a conditional forecast. Comparing it against the baseline
    answers "how does this game look if it stays inside these limits", which is
    a different question from "what did this component do", and it must not be
    averaged into a component-effectiveness cohort.
    """

    lineage = dict(row.lineage_detail or {})
    limits = lineage.get("scoreLimits")
    if not limits:
        return None
    # The producer declares componentTrialSupported only for component trials, so
    # it is absent here and reads as False. Whether the filter RAN is a separate,
    # recorded fact -- take it from what actually ran rather than letting a
    # missing declaration imply the component sat out.
    # componentsApplied records ran-flags by componentId only; the role lives in
    # the components manifest, so the two are joined on that id.
    limit_ids = {
        str((entry or {}).get("componentId") or "")
        for entry in (lineage.get("components") or [])
        if str((entry or {}).get("role") or "") == "SCORE_LIMIT"
    }
    applied = [
        entry for entry in (lineage.get("componentsApplied") or [])
        if str((entry or {}).get("componentId") or "") in limit_ids
    ]
    source = lineage.get("sourceSimulationCount")
    retained = lineage.get("retainedSimulationCount")
    excluded = lineage.get("excludedSimulationCount")
    return {
        "label": "Score-limited research",
        "limits": limits,
        "component_ran": bool(applied) and all(bool(e.get("ran")) for e in applied),
        "components_applied": applied,
        "source_simulation_count": source,
        "retained_simulation_count": retained,
        "excluded_simulation_count": excluded,
        "exclusion_rate": lineage.get("exclusionRate"),
        "denominator_used": (row.uncertainty or {}).get("simulationCount"),
        "conditions_on": (
            "Games that stayed within the published scoring limits. Every "
            "probability and expected score below is computed over the "
            f"{format(retained, ',d') if isinstance(retained, int) else 'retained'} "
            f"draws that satisfied them, not over the baseline's "
            f"{format(source, ',d') if isinstance(source, int) else 'original'}."
        ),
        "not_clipped": (
            "Complete violating paths were excluded. No accepted score was "
            "clipped and no event sequence was modified, so an accepted draw is "
            "the baseline's own draw."
        ),
        "interpretation_limit": (
            "This is a conditional forecast, not a better unconditional one. It "
            "says nothing about how often the limits are exceeded, and it is not "
            "a promotion of this configuration."
        ),
    }


def candidate_configurations(
    session: Session, game_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Every candidate configuration published for this game, newest arm first.

    A fallback arm is labelled as one. Its numbers equal the baseline's because
    its component did not run, which is operational coverage, not a measured
    component effect -- counting it as evidence about the component would be
    reading a copy of the baseline as a result.
    """

    rows = session.scalars(
        select(ShadowForecast)
        .where(
            ShadowForecast.canonical_game_id == game_id,
            ShadowForecast.engine_id == CANDIDATE_ENGINE_ID,
        )
        .order_by(
            ShadowForecast.available_as_of_utc.desc(),
            ShadowForecast.created_at.desc(),
        )
    ).all()

    seen: set[str] = set()
    configurations: list[dict[str, Any]] = []
    for row in rows:
        key = _configuration_key(row)
        if key in seen:
            continue
        seen.add(key)
        lineage = dict(row.lineage_detail or {})
        eligibility = dict((row.readiness_detail or {}).get("eligibility") or {})
        supported = lineage.get("componentTrialSupported")
        if supported is None:
            supported = eligibility.get("componentTrialSupported")
        fell_back = lineage.get("fallbackToOriginalDraws")
        if fell_back is None:
            fell_back = bool((lineage.get("simulation") or {}).get("fallbackToOriginalDraws"))
        configurations.append({
            "key": key,
            "configuration_id": lineage.get("configurationId") or key,
            # Rows imported before the producer label was persisted are still
            # fully separated -- by the candidate's own per-configuration engine
            # version -- but the label they are keyed on is not the producer's
            # word, and a reader comparing against a Studio receipt needs to
            # know which one they are looking at.
            "configuration_identity_source": (
                "PRODUCER_CONFIGURATION_ID" if lineage.get("configurationId")
                else "CANDIDATE_ENGINE_VERSION"
            ),
            "actual_configuration": lineage.get("actualConfiguration"),
            "engine_version": row.engine_version,
            "forecast_id": row.forecast_id,
            "contract_version": lineage.get("contractVersion"),
            "component_trial_supported": bool(supported),
            "fell_back_to_baseline_draws": bool(fell_back),
            "score_limit": _score_limit(row),
            "published_utc": _iso(row.available_as_of_utc),
        })
    return configurations


def _candidate_row(
    session: Session, game_id: uuid.UUID, configuration: str | None,
) -> ShadowForecast | None:
    """The requested configuration's newest forecast, or the newest overall."""

    if not configuration:
        return _latest(session, game_id, CANDIDATE_ENGINE_ID)
    rows = session.scalars(
        select(ShadowForecast)
        .where(
            ShadowForecast.canonical_game_id == game_id,
            ShadowForecast.engine_id == CANDIDATE_ENGINE_ID,
        )
        .order_by(
            ShadowForecast.available_as_of_utc.desc(),
            ShadowForecast.created_at.desc(),
        )
    ).all()
    wanted = configuration.strip()
    for row in rows:
        lineage = dict(row.lineage_detail or {})
        if wanted in {_configuration_key(row), str(lineage.get("configurationId") or "")}:
            return row
    return None


def _bound_baseline(
    session: Session, game_id: uuid.UUID, candidate_row: ShadowForecast,
) -> tuple[ShadowForecast | None, str]:
    """The exact baseline this candidate was composed on, by stored identity.

    Reading "the latest baseline" instead would compare a frozen candidate
    against whichever V18 forecast happened to be newest at read time, so the
    displayed difference would drift after a baseline re-publication while the
    candidate never changed. The candidate names its baseline; that one is used.
    """

    identity = str((candidate_row.lineage_detail or {}).get("baselineForecastId") or "")
    if identity:
        bound = session.scalar(
            select(ShadowForecast).where(
                ShadowForecast.forecast_id == identity,
                ShadowForecast.engine_id == BASELINE_ENGINE_ID,
            )
        )
        if bound is not None:
            return bound, "BOUND_BY_CANDIDATE_BASELINE_FORECAST_ID"
        return None, "BOUND_BASELINE_NOT_FOUND"
    return _latest(session, game_id, BASELINE_ENGINE_ID), "LATEST_BASELINE_NO_BINDING_PUBLISHED"


def candidate_cohort(session: Session, *, configuration: str) -> dict[str, Any]:
    """A mean over the games where one configuration and its baseline both scored.

    WHAT IS DELIBERATELY NOT IN HERE
    --------------------------------
    *   **Fallback arms.** A configuration whose component did not run reused the
        baseline's own draws, so its score equals the baseline's exactly.
        Averaging those in would pull every difference towards zero and dress a
        copy of the baseline up as evidence about the component.
    *   **Conditioned populations.** A score-limited arm keeps only the baseline
        draws that stayed inside its published limits and reports every summary
        over that subset. Its component did run and its numbers do differ, so it
        is not a fallback -- but it answers a conditional question over a smaller
        denominator, and averaging it in would read a conditional forecast as a
        component effect.
    *   **Historical replays.** A rerun over a finished game is not a forecast
        that existed before it.
    *   **Pairs with no bound baseline**, and pairs scored against different
        recorded finals. Either makes the difference partly a difference of
        something other than the model.
    *   **A different horizon.** A candidate and a baseline that disagree about
        first pitch are not forecasting the same event.

    Each metric is averaged only over the games where BOTH sides computed it, and
    that count is reported beside it. A missing distribution shrinks a metric's
    cohort; it never enters as a zero.
    """

    rows = session.scalars(
        select(ShadowForecast)
        .where(
            ShadowForecast.engine_id == CANDIDATE_ENGINE_ID,
            ShadowForecast.engine_version == configuration,
        )
        .order_by(ShadowForecast.scheduled_start_utc)
    ).all()

    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    def drop(row: ShadowForecast, reason: str, detail: str) -> None:
        excluded.append({
            "forecast_identity": row.forecast_id,
            "source_game_id": row.source_event_id,
            "reason": reason,
            "detail": detail,
        })

    for row in rows:
        lineage = dict(row.lineage_detail or {})
        eligibility = dict((row.readiness_detail or {}).get("eligibility") or {})
        supported = lineage.get("componentTrialSupported")
        if supported is None:
            supported = eligibility.get("componentTrialSupported")
        limit = _score_limit(row)
        if limit is not None:
            # Excluded, but not as a failure. Its component ran and its numbers
            # differ from the baseline's; what disqualifies it from a
            # component-effectiveness average is that it answers a conditional
            # question over a different denominator. Calling this a fallback
            # would be false on both counts.
            drop(row, "CONDITIONED_POPULATION_NOT_COMPONENT_TRIAL",
                 "This configuration keeps only the baseline draws that stayed "
                 "within its published scoring limits and reports every summary "
                 "over that subset. It is a conditional forecast, not a measured "
                 "component effect, so it is not averaged into this cohort.")
            continue
        if not supported:
            drop(row, "FALLBACK_NOT_A_COMPONENT_TRIAL",
                 "This configuration's component did not run for this game, so its "
                 "numbers are the baseline's own draws rather than a measured result.")
            continue
        if str(lineage.get("artifactType") or PROSPECTIVE) == HISTORICAL_REPLAY:
            drop(row, "HISTORICAL_REPLAY",
                 "A labelled rerun over a finished game is not prospective evidence.")
            continue
        identity = str(lineage.get("baselineForecastId") or "")
        baseline_row = session.scalar(
            select(ShadowForecast).where(
                ShadowForecast.forecast_id == identity,
                ShadowForecast.engine_id == BASELINE_ENGINE_ID,
            )
        ) if identity else None
        if baseline_row is None:
            drop(row, "NO_BOUND_BASELINE",
                 "The candidate names no baseline forecast that resolves in AISP, so "
                 "there is nothing to difference it against.")
            continue
        ours = _aware_start(row)
        theirs = _aware_start(baseline_row)
        if ours is None or theirs is None or ours != theirs:
            drop(row, "HORIZON_MISMATCH",
                 f"Candidate first pitch {_iso(ours)} does not equal the baseline's "
                 f"{_iso(theirs)}, so the two are not forecasting the same event.")
            continue
        candidate_score = _score_for(session, row)
        baseline_score = _score_for(session, baseline_row)
        if candidate_score is None or baseline_score is None:
            drop(row, "PENDING_NOT_YET_SCORED",
                 "One or both sides have no current research evaluation yet: the game "
                 "has no eligible recorded final, or the scorer has not run since it did.")
            continue
        if str(candidate_score.get("result_identity")) != str(
                baseline_score.get("result_identity")):
            drop(row, "DIVERGENT_RESULT",
                 "The two arms were scored against different recorded finals.")
            continue
        included.append({
            "source_game_id": row.source_event_id,
            "canonical_game_id": str(row.canonical_game_id) if row.canonical_game_id else None,
            "candidate_forecast_id": row.forecast_id,
            "baseline_forecast_id": baseline_row.forecast_id,
            "scheduled_start_utc": _iso(ours),
            "candidate": candidate_score,
            "baseline": baseline_score,
        })

    metrics: list[dict[str, Any]] = []
    for key, label, absolute in _LOSS_METRICS:
        pairs = [
            (_loss_value(entry["candidate"], key, absolute),
             _loss_value(entry["baseline"], key, absolute))
            for entry in included
        ]
        pairs = [(ours, theirs) for ours, theirs in pairs
                 if ours is not None and theirs is not None]
        if not pairs:
            continue
        candidate_mean = sum(value for value, _ in pairs) / len(pairs)
        baseline_mean = sum(value for _, value in pairs) / len(pairs)
        delta = candidate_mean - baseline_mean
        metrics.append({
            "key": key,
            "label": label,
            "games": len(pairs),
            "candidate_mean": round(candidate_mean, 6),
            "baseline_mean": round(baseline_mean, 6),
            "delta": round(delta, 6),
            "favours": "CANDIDATE" if delta < 0 else "BASELINE" if delta > 0 else "NEITHER",
        })

    return {
        "schema_version": COHORT_SCHEMA_VERSION,
        "configuration": configuration,
        "games": len(included),
        "metrics": metrics,
        "included": included,
        "excluded": excluded,
        "convention": (
            "candidate minus baseline; every metric is a loss, so a negative "
            "difference favours the candidate"
        ),
        "claim_limit": (
            "This is a mean over a handful of observed games. It measures whether "
            "this configuration scored better or worse than its own frozen baseline "
            "on those games. It is not a significance test, it establishes no "
            "ordering between configurations, and it is not a promotion decision."
        ),
    }


def _aware_start(row: ShadowForecast) -> datetime | None:
    value = row.scheduled_start_utc
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def candidate_comparison(
    session: Session, game_id: uuid.UUID, *, configuration: str | None = None,
) -> dict[str, Any] | None:
    """Baseline beside one candidate configuration, or None when none exists.

    ``configuration`` accepts either the producer's label (``V18_SURVIVAL_ONLY``)
    or the candidate engine version. Omitted, the newest configuration is shown
    and the others stay listed in ``available_configurations`` -- so a second arm
    arriving cannot quietly replace the first on every surface.
    """

    candidate_row = _candidate_row(session, game_id, configuration)
    if candidate_row is None:
        return None
    baseline_row, baseline_binding = _bound_baseline(session, game_id, candidate_row)

    lineage = dict(candidate_row.lineage_detail or {})
    artifact_type = str(lineage.get("artifactType") or PROSPECTIVE)
    candidate = _side(session, candidate_row, "Ensemble release candidate", "RESEARCH_CANDIDATE")
    baseline = (
        _side(session, baseline_row, "V18 whole-game baseline (frozen)", "RESEARCH_SHADOW")
        if baseline_row is not None else None
    )

    difference: dict[str, Any] | None = None
    if baseline is not None:
        difference = {
            "home_win_probability_points": _round(
                (candidate["home_win_probability"] or 0.0)
                - (baseline["home_win_probability"] or 0.0)
            ),
            "expected_home_runs": _round(
                (candidate["expected_home_runs"] or 0.0)
                - (baseline["expected_home_runs"] or 0.0)
            ),
            "expected_away_runs": _round(
                (candidate["expected_away_runs"] or 0.0)
                - (baseline["expected_away_runs"] or 0.0)
            ),
            "convention": "candidate minus baseline",
        }

    # A per-component split is shown only when Model Studio supplied one. The
    # total difference is never divided up here: a composed model's output is
    # not the sum of its parts' effects, and pretending otherwise would put a
    # fabricated number in front of a reader who cannot tell.
    attribution = lineage.get(ATTRIBUTION_KEY)
    attribution_supplied = isinstance(attribution, dict) and bool(attribution)

    controlling = session.scalar(
        select(ModelChampionAuthority)
        .where(ModelChampionAuthority.state == "ACTIVE")
        .order_by(ModelChampionAuthority.effective_at.desc())
        .limit(1)
    )

    eligibility = dict((candidate_row.readiness_detail or {}).get("eligibility") or {})
    supported = lineage.get("componentTrialSupported")
    if supported is None:
        supported = eligibility.get("componentTrialSupported")
    fell_back = lineage.get("fallbackToOriginalDraws")
    if fell_back is None:
        fell_back = bool((lineage.get("simulation") or {}).get("fallbackToOriginalDraws"))

    return {
        "schema_version": SCHEMA_VERSION,
        "game_id": str(game_id),
        "artifact_type": artifact_type,
        "configuration": {
            "key": _configuration_key(candidate_row),
            "configuration_id": (
                lineage.get("configurationId") or _configuration_key(candidate_row)),
            "configuration_identity_source": (
                "PRODUCER_CONFIGURATION_ID" if lineage.get("configurationId")
                else "CANDIDATE_ENGINE_VERSION"
            ),
            "actual_configuration": lineage.get("actualConfiguration"),
            "contract_version": lineage.get("contractVersion"),
            "component_trial_supported": bool(supported),
            "fell_back_to_baseline_draws": bool(fell_back),
            "score_limit": _score_limit(candidate_row),
        },
        "available_configurations": candidate_configurations(session, game_id),
        "baseline_binding": baseline_binding,
        # What actually happened, and how each side scored against it.
        "score_difference": _score_difference(
            candidate.get("score"), (baseline or {}).get("score"),
        ),
        "shared_final": _shared_final(
            candidate.get("score"), (baseline or {}).get("score"),
        ),
        "governed_final": _governed_final(session, game_id),
        "formal_assessment": _formal_assessment_block(
            session, game_id, candidate, baseline),
        # A fallback arm is numerically identical to the baseline on purpose.
        # Saying so here is what stops the difference of 0.0000 below being read
        # as "the component made no difference" -- the component did not run.
        "fallback_note": (
            "This configuration's component did not run for this game, so it "
            "reused the baseline's own draws. Its numbers equal the baseline's "
            "by construction. That is operational coverage, not a measured "
            "component effect, and it is not evidence about the component."
        ) if fell_back else None,
        "is_replay": artifact_type == HISTORICAL_REPLAY,
        "prospective_eligible": artifact_type == PROSPECTIVE,
        "baseline": baseline,
        "candidate": candidate,
        "difference": difference,
        "components": _components(candidate_row),
        # The producer's published configuration table, passed through. A/B/C/D
        # are NOT derived here: AISP shows what Studio measured, or nothing.
        "configurations": lineage.get("configurations") or [],
        "default_configuration": lineage.get("defaultConfiguration"),
        "comparison_status": lineage.get("comparisonStatus"),
        "limitations": lineage.get("limitations") or [],
        "candidate_package_sha256": lineage.get("packageSha256"),
        "ordering_claim": (
            "This is ONE historical replay of ONE game. It establishes no "
            "prospective ordering between configurations and no superiority "
            "claim. The Monte Carlo standard errors quantify simulation "
            "sampling only, not out-of-sample performance."
        ),
        "component_attribution": attribution if attribution_supplied else None,
        "component_attribution_note": (
            None if attribution_supplied else
            "Model Studio supplied no per-component attribution, so the difference "
            "is shown as a total only. Splitting it by subtraction would assume the "
            "components contribute additively, which a composed model does not."
        ),
        "candidate_identity": {
            "engine_id": candidate_row.engine_id,
            "engine_version": candidate_row.engine_version,
            "model_artifact_sha256": candidate_row.model_artifact_sha256,
            "handoff_sha256": lineage.get("handoffSha256"),
            "object_sha256": lineage.get("objectSha256"),
            "baseline_forecast_id": lineage.get("baselineForecastId"),
        },
        "authority": {
            "candidate_wagering_authority": False,
            "candidate_execution_allowed_in_aisp": False,
            "candidate_activates_model": False,
            "controlling_engine_for_official_decisions": (
                controlling.engine_id if controlling is not None else "AISP_BASELINE"
            ),
            "controlling_engine_source": (
                "model_champion_authorities (ACTIVE)" if controlling is not None
                else "no ACTIVE champion authority; Gen 1 remains the controlling engine"
            ),
        },
        "note": REPLAY_NOTE if artifact_type == HISTORICAL_REPLAY else PROSPECTIVE_NOTE,
    }


__all__ = [
    "ATTRIBUTION_KEY", "SCHEMA_VERSION", "COHORT_SCHEMA_VERSION",
    "candidate_comparison", "candidate_configurations", "candidate_cohort",
]
