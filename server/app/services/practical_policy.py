"""AISP_PRACTICAL_PREGAME_POLICY_V2 — the practical pregame challenger policy.

A separately versioned, immutable challenger to the V1 strict control. It
replaces two structurally unsatisfiable V1 gates with governed evidence states:

- starter status CONFIRMED  ->  CONFIRMED or PROBABLE_STABLE (state machine)
- lineup confirmation       ->  PENDING_ALLOWED_FOR_MONEYLINE (moneyline only)

Every other V1 hard requirement is retained by component (never via the blunt
readiness>=70 composite): current forecast, fresh complete same-book two-sided
quote, no unresolved availability event, no extreme disagreement, no other
critical readiness component missing, positive adjusted edge and EV.

Incomplete-but-governed evidence carries an empirically derived, conservative
decision-level penalty (audited from immutable forecast revisions, never from
outcomes), a 50% evidence stake multiplier, and a 0.5% position cap.

Nothing here mutates the V1 experiment, reads a game outcome, or calls a
provider.
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
    StarterEvidence,
    lineup_evidence,
    starter_evidence,
)

POLICY_VERSION = "AISP_PRACTICAL_PREGAME_POLICY_V2"
STAKING_VERSION = "AISP_QUARTER_KELLY_EVIDENCE_V2"
PENALTY_CALIBRATION_VERSION = "AISP_EVIDENCE_PENALTIES_V1"

# Evidence tiers (component-level; never a composite readiness threshold).
TIER_A = "TIER_A_FULL_EVIDENCE"
TIER_B = "TIER_B_PROBABLE_STABLE"
TIER_C = "TIER_C_LINEUP_PENDING"
TIER_D = "TIER_D_INELIGIBLE"

# ---------------------------------------------------------------------------
# Frozen empirical penalty calibration (2026-08-31 audit; see audit block).
#
# Derived from immutable intraday forecast revisions only — NEVER from game
# outcomes. Starter history is sparse (n=8 < 30 eligible revision events), so
# the mandated conservative default of 2.0 percentage points applies. Lineup
# history is adequate (n=57): the 95th-percentile absolute home-probability
# movement across lineup-bracketed revisions is 0.000 points (98.2% moved
# < 0.1, max 0.535), but incomplete evidence is never treated as zero-risk —
# a conservative floor of 0.5 points is applied.
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

# Staking: quarter-Kelly base; penalized-evidence tiers halve the stake and cap
# the position at 0.5% of the current V2 bankroll. Tier A retains V1's cap.
EVIDENCE_STAKE_MULTIPLIER = 0.5
PENALIZED_TIER_POSITION_CAP = 0.005
MIN_ADJUSTED_EDGE = 0.02  # unchanged final threshold after all penalties


def frozen_policy_config(settings: Settings) -> dict[str, Any]:
    """The immutable V2 policy_config persisted on the experiment row."""

    config = {
        "policy_version": POLICY_VERSION,
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
    """One immutable V2 policy evaluation for a game's moneyline selection."""

    tier: str = TIER_D
    recommendation: str = "NO_BET"
    reason: str = ""
    reasons: list[str] = field(default_factory=list)
    starter_home: StarterEvidence | None = None
    starter_away: StarterEvidence | None = None
    lineup: LineupEvidence | None = None
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
            "starter_home": self.starter_home.as_json() if self.starter_home else None,
            "starter_away": self.starter_away.as_json() if self.starter_away else None,
            "lineup": self.lineup.as_json() if self.lineup else None,
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
    """Deterministic, point-in-time V2 evaluation from the same evidence
    snapshot the V1 board uses. Read-only; the caller persists the result."""

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
    ) -> V2Evaluation:
        ev = V2Evaluation()

        if forecast is None:
            return self._blocked(ev, "Forecast unavailable")

        # -- governed evidence states: always computed for transparency, even
        # when an earlier hard block decides the recommendation --------------
        home_gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
        away_gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(False)))
        if home_gt is None or away_gt is None:
            return self._blocked(ev, "Canonical game team identities unavailable")
        ev.starter_home = starter_evidence(
            self.session, game, home_gt.team_id, cutoff=cutoff,
            forecast_generated_at=forecast.generated_at,
        )
        ev.starter_away = starter_evidence(
            self.session, game, away_gt.team_id, cutoff=cutoff,
            forecast_generated_at=forecast.generated_at,
        )
        starters_eligible = ev.starter_home.eligible and ev.starter_away.eligible
        quote_ok = selection is not None and not getattr(selection, "is_stale", False)
        context = LineupContext(
            market_is_moneyline=True,
            starters_eligible=starters_eligible,
            quote_fresh_and_complete=quote_ok,
            forecast_current=True,  # unresolved-event forecasts are caught by the state machines
            before_final_cutoff=_aware(now) < _aware(cutoff),
            dependency_allows_pending=True,  # audited bounded movement; see PENALTY_CALIBRATION
        )
        ev.lineup = lineup_evidence(
            self.session, game, home_gt.team_id, away_gt.team_id,
            cutoff=cutoff, forecast_generated_at=forecast.generated_at, context=context,
        )

        # -- hard blocks shared with V1 (component-explicit, never composite) --
        if selection is None:
            return self._blocked(ev, "No complete two-sided governed MONEYLINE quote")
        if getattr(selection, "is_stale", False):
            return self._blocked(ev, "Latest quote is stale")
        if _aware(now) >= _aware(cutoff):
            return self._blocked(ev, "Past the final-pregame decision cutoff")
        other_missing = self._non_forgiven_critical_missing(forecast)
        if other_missing:
            return self._blocked(ev, f"Critical non-forgiven evidence incomplete: {', '.join(sorted(other_missing))}")
        model_p = getattr(selection, "model_probability_fraction", None)
        no_vig = getattr(selection, "no_vig_probability_fraction", None)
        if model_p is None or no_vig is None:
            return self._blocked(ev, "Model or market probability unavailable")
        if (model_p - no_vig) >= self.settings.opportunity_extreme_disagreement_threshold:
            return self._blocked(ev, "Extreme model-market disagreement — review required")
        last_event = str(getattr(availability, "last_availability_event", "") or "")
        if "SCRATCH" in last_event.upper():
            return self._blocked(ev, "Unresolved late scratch")
        if not starters_eligible:
            detail = []
            for label, side in (("home", ev.starter_home), ("away", ev.starter_away)):
                if not side.eligible:
                    why = f" ({'; '.join(side.reasons)})" if side.reasons else ""
                    detail.append(f"{label} starter {side.state}{why}")
            return self._blocked(ev, f"Starter evidence not CONFIRMED/PROBABLE_STABLE: {'; '.join(detail)}")
        if not ev.lineup.eligible_for_moneyline:
            why = f" ({'; '.join(ev.lineup.reasons)})" if ev.lineup.reasons else ""
            return self._blocked(ev, f"Lineup evidence {ev.lineup.state}{why}")

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
        if ev.adjusted_expected_value is None or ev.adjusted_expected_value <= 0:
            blockers.append("Adjusted expected value is not positive")
        if blockers:
            ev.recommendation = "NO_BET"
            ev.reasons = blockers
            ev.reason = (
                f"V2 {ev.tier}: MONEYLINE selection {getattr(selection, 'selection', '—')} does not clear the "
                f"adjusted thresholds — {'; '.join(blockers)}."
            )
            return ev

        ev.recommendation = "QUALIFIED_BET"
        ev.reason = (
            f"V2 {ev.tier}: MONEYLINE selection {getattr(selection, 'selection', '—')} qualifies with adjusted edge "
            f"{ev.adjusted_edge * 100:+.1f} pts (raw {ev.raw_edge * 100:+.1f}; uncertainty "
            f"-{uncertainty * 100:.1f}; starter -{ev.starter_penalty * 100:.1f}; lineup -{ev.lineup_penalty * 100:.1f}) "
            f"and positive adjusted EV under {POLICY_VERSION}."
        )
        return ev

    @staticmethod
    def _blocked(ev: V2Evaluation, reason: str) -> V2Evaluation:
        ev.tier = TIER_D
        ev.recommendation = "NO_BET"
        ev.reason = f"V2 {TIER_D}: {reason}."
        ev.reasons = [reason]
        return ev

    @staticmethod
    def _non_forgiven_critical_missing(forecast: Forecast) -> list[str]:
        """Critical readiness components that V2 does NOT forgive.

        Only the two structurally unsatisfiable components (pitcher
        confirmation, lineup confirmation) are governed by the V2 evidence
        state machines; every other critical gap remains a hard block."""

        forgiven = {"pitcher_confirmation", "lineups"}
        missing: list[str] = []
        for key, raw in (forecast.readiness_components or {}).items():
            if key.startswith("_") or not isinstance(raw, dict) or key in forgiven:
                continue
            if raw.get("critical") and str(raw.get("status", "")).lower() in {"missing", "stale"}:
                missing.append(key)
        return missing


__all__ = [
    "EVIDENCE_STAKE_MULTIPLIER",
    "LINEUP_PENALTY_FRACTION",
    "MIN_ADJUSTED_EDGE",
    "PENALIZED_TIER_POSITION_CAP",
    "PENALTY_CALIBRATION",
    "PENALTY_CALIBRATION_VERSION",
    "POLICY_VERSION",
    "PracticalPolicyEvaluator",
    "STAKING_VERSION",
    "STARTER_PENALTY_FRACTION",
    "TIER_A",
    "TIER_B",
    "TIER_C",
    "TIER_D",
    "V2Evaluation",
    "frozen_policy_config",
]
