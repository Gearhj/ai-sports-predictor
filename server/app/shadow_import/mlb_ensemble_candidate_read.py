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
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

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
        "crps_margin": _round(evaluation.crps_margin),
        "crps_total": _round(evaluation.crps_total),
        "result_source": evaluation.result_source,
        "result_tier": evaluation.result_tier,
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
        },
        "available_configurations": candidate_configurations(session, game_id),
        "baseline_binding": baseline_binding,
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
    "ATTRIBUTION_KEY", "SCHEMA_VERSION",
    "candidate_comparison", "candidate_configurations",
]
