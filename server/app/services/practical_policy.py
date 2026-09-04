"""AISP_PRACTICAL_PREGAME_POLICY_V2_1 — the practical pregame challenger policy.

A separately versioned, immutable challenger to the V1 strict control. V2.1
supersedes the never-activated AISP_PRACTICAL_PREGAME_POLICY_V2 registration
(zero decisions, zero positions) whose qualification contract inherited the
composite ``feature_completeness`` readiness gate. That composite is a coverage
fraction over ~36 snapshot features — most lineup-, starter-, statcast- or
placeholder-derived with registered training-consistent fallbacks — and is
structurally unsatisfiable in this configuration (Statcast disabled; four
structural placeholder families are unconditionally counted missing). Blocking
on it re-imposed the exact starter/lineup conditions V2 explicitly governs and
double-counted incompleteness the ensemble already prices into ``uncertainty``.

V2.1 therefore evaluates an explicit atomic hard-block whitelist (§ contract
below) plus the governed starter/lineup evidence states with their audited
penalties. Economic policy is byte-identical to V2: 2.0/0.5-point penalties,
2.0-point minimum adjusted edge, positive adjusted EV, quarter-Kelly, 50%
incomplete-evidence multiplier, 0.5%/1% caps.

Nothing here mutates V1 or the superseded V2 row, reads a game outcome, or
calls a provider.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Forecast, Game, GameTeam
from app.services.evidence_states import (
    LINEUP_PENDING_ALLOWED,
    LineupContext,
    LineupEvidence,
    STARTER_CONFIRMED,
    STARTER_CONFLICT,
    StarterEvidence,
    lineup_evidence,
    starter_evidence,
)

POLICY_VERSION = "AISP_PRACTICAL_PREGAME_POLICY_V2_1"
SUPERSEDED_POLICY_VERSION = "AISP_PRACTICAL_PREGAME_POLICY_V2"
STAKING_VERSION = "AISP_QUARTER_KELLY_EVIDENCE_V2"
PENALTY_CALIBRATION_VERSION = "AISP_EVIDENCE_PENALTIES_V1"
DECOMPOSITION_VERSION = "V2_FEATURE_COMPLETENESS_DECOMPOSITION_V1"

# Evidence tiers (component-level; never a composite readiness threshold).
TIER_A = "TIER_A_FULL_EVIDENCE"
TIER_B = "TIER_B_PROBABLE_STABLE"
TIER_C = "TIER_C_LINEUP_PENDING"
TIER_D = "TIER_D_INELIGIBLE"

# Evaluation lifecycle: indicative states through the day; only the
# final-pregame job creates a position.
LIFECYCLE_NOT_YET = "NOT_YET_EVALUATED"
LIFECYCLE_PENDING = "PENDING_FINAL_EVIDENCE"
LIFECYCLE_PROVISIONAL = "PROVISIONALLY_ELIGIBLE"
LIFECYCLE_FINAL_QUALIFIED = "FINAL_QUALIFIED"
LIFECYCLE_FINAL_NO_BET = "FINAL_NO_BET"

# ---------------------------------------------------------------------------
# Frozen empirical penalty calibration (2026-08-31 audit; unchanged from V2).
# ---------------------------------------------------------------------------
PENALTY_CALIBRATION: dict[str, Any] = {
    "version": PENALTY_CALIBRATION_VERSION,
    "derived_at": "2026-08-31",
    "outcome_free": True,
    "starter": {
        "penalty_points": 2.0,
        "basis": "sparse_history_conservative_default",
        "sample_size": 8,
        "empirical_p95_points": 0.271,
        "empirical_median_points": 0.072,
        "minimum_floor_points": 2.0,
    },
    "lineup": {
        "penalty_points": 0.5,
        "basis": "empirical_p95_with_conservative_floor",
        "sample_size": 57,
        "empirical_p95_points": 0.0,
        "empirical_max_points": 0.535,
        "no_material_change_share": 0.982,
        "minimum_floor_points": 0.5,
    },
    "lineup_dependency_classification": {
        "value": "DIRECT_MODEL_INPUT",
        "detail": (
            "The controlling ensemble consumes aggregate lineup features "
            "(home/away lineup xwOBA with league-average fallbacks); the audited "
            "lineup-confirmation revision distribution demonstrates bounded "
            "forecast movement (p95 0.000 pts, max 0.535 pts, n=57), which "
            "permits the moneyline-only pending-lineup allowance."
        ),
        "revision_distribution": {
            "n": 57, "median_points": 0.0, "p90_points": 0.0, "p95_points": 0.0,
            "max_points": 0.535, "no_material_change_share": 0.982,
            "moved_ge_0_5_points_share": 0.018, "moved_ge_1_0_points_share": 0.0,
        },
    },
    "starter_status_audit": {
        "live_games_observed": 88,
        "observations": 4122,
        "games_ever_confirmed_before_first_pitch": 0,
        "final_pre_pitch_states": {"PROBABLE": 158, "UNKNOWN": 4, "CONFIRMED": 0},
        "median_first_probable_lead_hours": 13.4,
        "median_identical_probable_observations": 33,
        "median_observation_gap_minutes": 30.0,
        "starter_identity_changes": 1,
    },
}

STARTER_PENALTY_FRACTION = PENALTY_CALIBRATION["starter"]["penalty_points"] / 100.0
LINEUP_PENALTY_FRACTION = PENALTY_CALIBRATION["lineup"]["penalty_points"] / 100.0

EVIDENCE_STAKE_MULTIPLIER = 0.5
PENALIZED_TIER_POSITION_CAP = 0.005
MIN_ADJUSTED_EDGE = 0.02  # unchanged final threshold after all penalties

# ---------------------------------------------------------------------------
# V2_FEATURE_COMPLETENESS_DECOMPOSITION_V1
#
# ``feature_completeness`` is a numeric coverage fraction over the frozen
# feature schema plus four structural placeholder families that are
# UNCONDITIONALLY appended to missing_features (defense, baserunning,
# opponent_adjustment, roof_orientation_adjustment — feature_snapshot_service).
# Every schema feature carries a registered training-consistent default
# (mlb_pregame_gate3_v1), and the ensemble consumes the completeness fraction
# itself as a CONFIDENCE input already reflected in forecast.uncertainty.
#
# Classification classes:
#   A MODEL_REQUIRED_NO_VALID_FALLBACK — V2 hard block
#   B MODEL_REQUIRED_GOVERNED_FALLBACK_USED — eligible (penalty where the
#     source evidence state is penalized)
#   C DECISION_REQUIRED — hard block (independently gated)
#   D OPTIONAL_CONTEXT — never a block
#   E DUPLICATE_DERIVED_GATE — never a second block (the dedicated V2
#     starter/lineup/availability state machines govern it)
# ---------------------------------------------------------------------------
CLASS_A = "MODEL_REQUIRED_NO_VALID_FALLBACK"
CLASS_B = "MODEL_REQUIRED_GOVERNED_FALLBACK_USED"
CLASS_C = "DECISION_REQUIRED"
CLASS_D = "OPTIONAL_CONTEXT"
CLASS_E = "DUPLICATE_DERIVED_GATE"

# Ordered substring rules; first match wins. (feature_substring, class,
# derived_from, has_governed_fallback)
FEATURE_CLASSIFICATION_RULES: tuple[tuple[str, str, str, bool], ...] = (
    ("lineup", CLASS_E, "lineup", True),          # lineup_xwoba etc. — V2 lineup state governs
    ("starter_player_id", CLASS_E, "starter", False),  # canonical identity — V2 starter state governs
    ("starter", CLASS_E, "starter", True),        # starter_era/xera — starter penalty governs
    ("statcast", CLASS_B, "statcast", True),      # disabled by configuration; league-average defaults
    ("bullpen", CLASS_B, "bullpen", True),
    ("recent_runs", CLASS_B, "team", True),
    ("rating", CLASS_A, "team", False),           # core team strength — no decision-grade fallback
    ("runs_per_game", CLASS_A, "team", False),
    ("runs_allowed", CLASS_A, "team", False),
    ("ops", CLASS_A, "team", False),
    ("park_run_factor", CLASS_B, "context", True),  # neutral_prior governed fallback
    ("baserunning", CLASS_D, "context", False),   # structural placeholder, never present
    ("defense", CLASS_D, "context", False),
    ("opponent_adjustment", CLASS_D, "context", False),
    ("roof_orientation", CLASS_D, "context", False),
    ("travel_distance", CLASS_D, "context", True),
    ("temperature", CLASS_D, "context", True),
    ("wind", CLASS_D, "context", True),
    ("precipitation", CLASS_D, "context", True),
    ("days_since", CLASS_D, "schedule", True),
    ("doubleheader", CLASS_D, "schedule", True),
    ("roster", CLASS_D, "availability", True),
)

# Readiness components: how each maps into the atomic contract.
READINESS_COMPONENT_CLASSIFICATION: dict[str, tuple[str, str]] = {
    "schedule": (CLASS_C, "forecast"),
    "team_stats": (CLASS_A, "team"),
    "probable_pitchers": (CLASS_E, "starter"),
    "pitcher_confirmation": (CLASS_E, "starter"),
    "lineups": (CLASS_E, "lineup"),
    "weather": (CLASS_D, "context"),
    "bullpen": (CLASS_B, "bullpen"),
    "feature_completeness": (CLASS_E, "composite"),  # decomposed per feature below
}


def classify_feature(name: str) -> tuple[str, str, bool]:
    lowered = name.lower()
    for pattern, cls, derived_from, fallback in FEATURE_CLASSIFICATION_RULES:
        if pattern in lowered:
            return cls, derived_from, fallback
    return CLASS_D, "unclassified", False


def decompose_feature_completeness(forecast: Forecast | Any) -> dict[str, Any]:
    """The exact per-component decomposition of the composite readiness gates.

    Returns the versioned diagnostic contract: every readiness component and
    every missing/stale snapshot feature, each with its class, derivation, and
    whether it hard-blocks V2. A published forecast is itself the evidence that
    every governed fallback was applied by the registered model."""

    components: list[dict[str, Any]] = []
    hard_blockers: list[str] = []
    raw = getattr(forecast, "readiness_components", None) or {}
    gate2 = raw.get("_gate2") or {}
    for key, value in raw.items():
        if key.startswith("_") or not isinstance(value, dict):
            continue
        status = str(value.get("status", "")).lower()
        cls, derived = READINESS_COMPONENT_CLASSIFICATION.get(key, (CLASS_D, "unclassified"))
        blocks = cls in (CLASS_A, CLASS_C) and status == "missing"
        components.append({
            "component": key, "source": f"readiness_components.{key}",
            "status": status or "unknown", "class": cls, "derived_from": derived,
            "critical_under_v1": bool(value.get("critical")),
            "hard_block_v2": blocks,
            "detail": value.get("detail"),
        })
        if blocks:
            hard_blockers.append(key)
    for bucket, statuses in (("missing_features", "missing"), ("stale_features", "stale"),
                             ("provisional_features", "provisional")):
        for name in gate2.get(bucket) or []:
            cls, derived, fallback = classify_feature(str(name))
            blocks = cls == CLASS_A and statuses == "missing"
            components.append({
                "component": str(name), "source": f"feature_snapshot.{bucket}",
                "status": statuses, "class": cls, "derived_from": derived,
                "governed_fallback": fallback,
                "hard_block_v2": blocks,
            })
            if blocks:
                hard_blockers.append(str(name))
    return {
        "version": DECOMPOSITION_VERSION,
        "components": components,
        "model_required_missing": sorted(set(hard_blockers)),
        "duplicate_components": sorted({c["component"] for c in components if c["class"] == CLASS_E}),
        "fallback_components": sorted({c["component"] for c in components if c["class"] == CLASS_B}),
        "optional_components": sorted({c["component"] for c in components if c["class"] == CLASS_D}),
    }


def frozen_policy_config(settings: Settings) -> dict[str, Any]:
    """The immutable V2.1 policy_config persisted on the experiment row."""

    config = {
        "policy_version": POLICY_VERSION,
        "supersedes": SUPERSEDED_POLICY_VERSION,
        "staking_version": STAKING_VERSION,
        "penalties": PENALTY_CALIBRATION,
        "starter_penalty_fraction": STARTER_PENALTY_FRACTION,
        "lineup_penalty_fraction": LINEUP_PENALTY_FRACTION,
        "evidence_stake_multiplier": EVIDENCE_STAKE_MULTIPLIER,
        "penalized_tier_position_cap": PENALIZED_TIER_POSITION_CAP,
        "min_adjusted_edge": MIN_ADJUSTED_EDGE,
        "tier_a_position_cap": settings.shadow_max_position_percentage,
        "supported_markets": ["MONEYLINE"],
        "eligible_starter_states": ["CONFIRMED", "PROBABLE_STABLE"],
        "pending_lineup_market_scope": ["MONEYLINE"],
        "feature_completeness_decomposition": {
            "version": DECOMPOSITION_VERSION,
            "readiness_components": {
                k: {"class": cls, "derived_from": d}
                for k, (cls, d) in READINESS_COMPONENT_CLASSIFICATION.items()
            },
            "feature_rules": [
                {"pattern": p, "class": c, "derived_from": d, "governed_fallback": f}
                for p, c, d, f in FEATURE_CLASSIFICATION_RULES
            ],
        },
        "hard_block_whitelist": [
            "no_valid_current_forecast",
            "forecast_identity_invalid",
            "exact_game_identity_unavailable",
            "unsupported_market",
            "incomplete_two_sided_moneyline_quote",
            "stale_quote",
            "game_already_started",
            "decision_after_final_pregame_cutoff",
            "starter_identity_missing",
            "starter_identity_conflicting",
            "starter_probable_unstable_at_final",
            "unresolved_material_starter_change",
            "unresolved_scratch_or_availability_event",
            "model_required_input_no_valid_fallback",
            "extreme_disagreement",
            "adjusted_edge_below_minimum",
            "adjusted_ev_not_positive",
            "adjusted_kelly_not_positive",
            "exposure_cap_exceeded",
        ],
    }
    checksum = hashlib.sha256(json.dumps(
        config, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return {**config, "checksum": checksum}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass
class V2Evaluation:
    """One immutable V2.1 policy evaluation for a game's moneyline selection."""

    tier: str = TIER_D
    lifecycle: str = LIFECYCLE_PENDING
    recommendation: str = "NO_BET"
    reason: str = ""
    reasons: list[str] = field(default_factory=list)
    gates_passed: list[str] = field(default_factory=list)
    critical_evidence_complete: bool = False
    starter_home: StarterEvidence | None = None
    starter_away: StarterEvidence | None = None
    lineup: LineupEvidence | None = None
    decomposition: dict[str, Any] | None = None
    raw_model_probability: float | None = None
    market_probability: float | None = None
    raw_edge: float | None = None
    uncertainty_adjustment: float | None = None
    starter_penalty: float = 0.0
    lineup_penalty: float = 0.0
    adjusted_probability: float | None = None
    adjusted_edge: float | None = None
    adjusted_expected_value: float | None = None
    evidence_multiplier: float = 1.0
    position_cap_fraction: float | None = None

    @property
    def qualified(self) -> bool:
        return self.recommendation == "QUALIFIED_BET"

    def evidence_json(self) -> dict[str, Any]:
        return {
            "policy_version": POLICY_VERSION,
            "staking_version": STAKING_VERSION,
            "penalty_calibration_version": PENALTY_CALIBRATION_VERSION,
            "tier": self.tier,
            "lifecycle": self.lifecycle,
            "critical_evidence_complete": self.critical_evidence_complete,
            "gates_passed": list(self.gates_passed),
            "starter_home": self.starter_home.as_json() if self.starter_home else None,
            "starter_away": self.starter_away.as_json() if self.starter_away else None,
            "lineup": self.lineup.as_json() if self.lineup else None,
            "feature_decomposition": self.decomposition,
            "raw_model_probability": self.raw_model_probability,
            "market_probability": self.market_probability,
            "raw_edge": self.raw_edge,
            "uncertainty_adjustment": self.uncertainty_adjustment,
            "starter_penalty": self.starter_penalty,
            "lineup_penalty": self.lineup_penalty,
            "adjusted_probability": self.adjusted_probability,
            "adjusted_edge": self.adjusted_edge,
            "adjusted_expected_value": self.adjusted_expected_value,
            "evidence_multiplier": self.evidence_multiplier,
            "position_cap_fraction": self.position_cap_fraction,
        }


class PracticalPolicyEvaluator:
    """Deterministic, point-in-time V2.1 evaluation from the same evidence
    snapshot the V1 board uses. Read-only; the caller persists the result.

    Hard blocks come exclusively from the atomic whitelist. The composite V1
    readiness score and ``feature_completeness`` fraction never block on their
    own — only an exactly classified MODEL_REQUIRED_NO_VALID_FALLBACK or
    DECISION_REQUIRED component does.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def evaluate(
        self,
        game: Game,
        forecast: Forecast | None,
        availability: Any | None,
        selection: Any | None,
        *,
        now: datetime,
        cutoff: datetime,
        final: bool | None = None,
    ) -> V2Evaluation:
        ev = V2Evaluation()
        is_final = bool(final) if final is not None else _aware(now) >= _aware(cutoff)

        if forecast is None:
            if selection is None and not is_final:
                ev.lifecycle = LIFECYCLE_NOT_YET
                ev.recommendation = "NO_BET"
                ev.reason = f"V2 {LIFECYCLE_NOT_YET}: the game has not reached its evaluation window."
                ev.reasons = ["Awaiting first forecast and quote"]
                return ev
            return self._blocked(ev, "No valid current forecast", is_final, pending=True)

        # -- governed evidence states: always computed for transparency ------
        from app.services.funnel_read_context import attached_context

        ctx = attached_context(self.session)
        if ctx is not None and game.id in ctx.games_by_id:
            home_gt = ctx.game_team(game.id, True)
            away_gt = ctx.game_team(game.id, False)
        else:
            home_gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
            away_gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(False)))
        if home_gt is None or away_gt is None:
            return self._blocked(ev, "Exact game identity unavailable", is_final)
        # Evidence is reduced and aged at the evaluation reference
        # min(now, cutoff): before the cutoff that is this instant, at/after it
        # the cutoff. Aging pre-cutoff evidence against a future cutoff was the
        # defect that branded fresh observations "stale" hours before first pitch.
        ev.starter_home = starter_evidence(
            self.session, game, home_gt.team_id, cutoff=cutoff,
            forecast_generated_at=forecast.generated_at, as_of=now,
        )
        ev.starter_away = starter_evidence(
            self.session, game, away_gt.team_id, cutoff=cutoff,
            forecast_generated_at=forecast.generated_at, as_of=now,
        )
        starters_eligible = ev.starter_home.eligible and ev.starter_away.eligible
        ev.lineup = lineup_evidence(
            self.session, game, home_gt.team_id, away_gt.team_id,
            cutoff=cutoff, forecast_generated_at=forecast.generated_at, as_of=now,
            context=LineupContext(
                market_is_moneyline=True,
                dependency_allows_pending=True,  # audited bounded movement; see PENALTY_CALIBRATION
                pregame=_aware(now) < _aware(game.starts_at) if game.starts_at else True,
            ),
        )
        ev.decomposition = decompose_feature_completeness(forecast)

        # -- atomic hard-block whitelist --------------------------------------
        starts_at = _aware(game.starts_at)
        if starts_at is not None and _aware(now) >= starts_at:
            return self._blocked(ev, "Game already started", True)
        gates: list[tuple[str, bool, str, bool]] = []  # (gate, passed, blocker text, pending-kind)
        gates.append(("valid_current_forecast", True, "", False))
        model_required = ev.decomposition["model_required_missing"]
        gates.append((
            "model_required_inputs_valid_or_governed_fallback",
            not model_required,
            f"Model-required input with no valid governed fallback: {', '.join(model_required)}",
            False,
        ))
        gates.append((
            "complete_two_sided_moneyline_quote",
            selection is not None,
            "No complete same-sportsbook two-sided MONEYLINE quote",
            True,
        ))
        gates.append((
            "fresh_quote",
            selection is not None and not getattr(selection, "is_stale", False),
            "Latest quote is stale",
            True,
        ))
        model_p = getattr(selection, "model_probability_fraction", None) if selection is not None else None
        no_vig = getattr(selection, "no_vig_probability_fraction", None) if selection is not None else None
        gates.append((
            "market_probabilities_available",
            selection is None or (model_p is not None and no_vig is not None),
            "Model or market probability unavailable",
            True,
        ))
        extreme = (
            model_p is not None and no_vig is not None
            and (model_p - no_vig) >= self.settings.opportunity_extreme_disagreement_threshold
        )
        gates.append(("not_extreme_disagreement", not extreme,
                      "Extreme model-market disagreement — review required", False))
        last_event = str(getattr(availability, "last_availability_event", "") or "")
        gates.append(("no_unresolved_scratch", "SCRATCH" not in last_event.upper(),
                      "Unresolved late scratch", False))
        starter_pending = not starters_eligible and not (
            ev.starter_home.state == STARTER_CONFLICT or ev.starter_away.state == STARTER_CONFLICT
        )
        starter_block = self._starter_block_text(ev)
        gates.append(("starters_confirmed_or_probable_stable", starters_eligible,
                      starter_block, starter_pending))
        gates.append(("lineup_confirmed_adequate_or_pending_allowed",
                      ev.lineup.eligible_for_moneyline,
                      f"Lineup evidence {ev.lineup.state}"
                      f"{f' ({chr(59).join(ev.lineup.reasons)})' if ev.lineup.reasons else ''}",
                      False))

        for gate, passed, blocker, pending in gates:
            if passed:
                ev.gates_passed.append(gate)
            else:
                return self._blocked(ev, blocker, is_final, pending=pending)

        ev.critical_evidence_complete = True  # V2_CRITICAL_EVIDENCE_COMPLETE

        # -- penalties + adjusted edge/EV -------------------------------------
        starter_penalized = ev.starter_home.state != STARTER_CONFIRMED or ev.starter_away.state != STARTER_CONFIRMED
        lineup_penalized = ev.lineup.penalized
        ev.starter_penalty = STARTER_PENALTY_FRACTION if starter_penalized else 0.0
        ev.lineup_penalty = LINEUP_PENALTY_FRACTION if lineup_penalized else 0.0
        uncertainty = float(forecast.uncertainty or 0.0)
        ev.raw_model_probability = float(model_p)
        ev.market_probability = float(no_vig)
        ev.raw_edge = round(float(model_p) - float(no_vig), 6)
        ev.uncertainty_adjustment = round(uncertainty, 6)
        adjusted_p = max(0.0, float(model_p) - uncertainty - ev.starter_penalty - ev.lineup_penalty)
        ev.adjusted_probability = round(adjusted_p, 6)
        ev.adjusted_edge = round(adjusted_p - float(no_vig), 6)
        decimal_odds = getattr(selection, "decimal_odds", None)
        if decimal_odds:
            ev.adjusted_expected_value = round(adjusted_p * (float(decimal_odds) - 1.0) - (1.0 - adjusted_p), 6)

        if lineup_penalized:
            ev.tier = TIER_C
        elif starter_penalized:
            ev.tier = TIER_B
        else:
            ev.tier = TIER_A
        penalized = starter_penalized or lineup_penalized
        ev.evidence_multiplier = EVIDENCE_STAKE_MULTIPLIER if penalized else 1.0
        ev.position_cap_fraction = (
            PENALIZED_TIER_POSITION_CAP if penalized else self.settings.shadow_max_position_percentage
        )

        blockers: list[str] = []
        if ev.adjusted_edge < MIN_ADJUSTED_EDGE:
            blockers.append(
                f"Adjusted edge {ev.adjusted_edge * 100:+.1f} pts below the {MIN_ADJUSTED_EDGE * 100:.1f} pt minimum "
                f"(penalties: starter {ev.starter_penalty * 100:.1f}, lineup {ev.lineup_penalty * 100:.1f})"
            )
        else:
            ev.gates_passed.append("adjusted_edge_minimum")
        if ev.adjusted_expected_value is None or ev.adjusted_expected_value <= 0:
            blockers.append("Adjusted expected value is not positive")
        else:
            ev.gates_passed.append("adjusted_ev_positive")
        if blockers:
            ev.recommendation = "NO_BET"
            ev.lifecycle = LIFECYCLE_FINAL_NO_BET if is_final else LIFECYCLE_PENDING
            ev.reasons = blockers
            ev.reason = (
                f"V2 {ev.tier}: MONEYLINE selection {getattr(selection, 'selection', '—')} does not clear the "
                f"adjusted thresholds — {'; '.join(blockers)}."
            )
            return ev

        ev.recommendation = "QUALIFIED_BET"
        ev.lifecycle = LIFECYCLE_FINAL_QUALIFIED if is_final else LIFECYCLE_PROVISIONAL
        ev.reason = (
            f"V2 {ev.tier}: MONEYLINE selection {getattr(selection, 'selection', '—')} qualifies with adjusted edge "
            f"{ev.adjusted_edge * 100:+.1f} pts (raw {ev.raw_edge * 100:+.1f}; uncertainty "
            f"-{uncertainty * 100:.1f}; starter -{ev.starter_penalty * 100:.1f}; lineup -{ev.lineup_penalty * 100:.1f}) "
            f"and positive adjusted EV under {POLICY_VERSION}."
        )
        return ev

    @staticmethod
    def _starter_block_text(ev: V2Evaluation) -> str:
        detail = []
        for label, side in (("home", ev.starter_home), ("away", ev.starter_away)):
            if side is not None and not side.eligible:
                why = f" ({'; '.join(side.reasons)})" if side.reasons else ""
                detail.append(f"{label} starter {side.state}{why}")
        return f"Starter evidence not CONFIRMED/PROBABLE_STABLE: {'; '.join(detail)}" if detail else ""

    @staticmethod
    def _blocked(ev: V2Evaluation, reason: str, is_final: bool, *, pending: bool = False) -> V2Evaluation:
        ev.tier = TIER_D
        ev.recommendation = "NO_BET"
        if is_final:
            ev.lifecycle = LIFECYCLE_FINAL_NO_BET
        else:
            # Pre-cutoff, nothing is finally ineligible: evidence may still
            # arrive and the final decision has not run.
            ev.lifecycle = LIFECYCLE_PENDING
        prefix = LIFECYCLE_PENDING if (not is_final and pending) else ev.tier
        ev.reason = f"V2 {prefix}: {reason}."
        ev.reasons = [reason]
        return ev


__all__ = [
    "CLASS_A", "CLASS_B", "CLASS_C", "CLASS_D", "CLASS_E",
    "DECOMPOSITION_VERSION",
    "EVIDENCE_STAKE_MULTIPLIER",
    "LIFECYCLE_FINAL_NO_BET", "LIFECYCLE_FINAL_QUALIFIED",
    "LIFECYCLE_NOT_YET", "LIFECYCLE_PENDING", "LIFECYCLE_PROVISIONAL",
    "LINEUP_PENALTY_FRACTION",
    "MIN_ADJUSTED_EDGE",
    "PENALIZED_TIER_POSITION_CAP",
    "PENALTY_CALIBRATION",
    "PENALTY_CALIBRATION_VERSION",
    "POLICY_VERSION",
    "PracticalPolicyEvaluator",
    "STAKING_VERSION",
    "STARTER_PENALTY_FRACTION",
    "SUPERSEDED_POLICY_VERSION",
    "TIER_A", "TIER_B", "TIER_C", "TIER_D",
    "V2Evaluation",
    "classify_feature",
    "decompose_feature_completeness",
    "frozen_policy_config",
]
