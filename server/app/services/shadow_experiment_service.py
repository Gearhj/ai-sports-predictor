"""Forward $1,000 virtual-bankroll shadow experiment.

A genuine forward-looking research experiment: it freezes the exact governed
price behind each game-day decision, only ever creates a virtual position from a
FINAL_PREGAME QUALIFIED_BET, settles idempotently from real game finals, and
tracks the bankroll before/after every settlement. Nothing here is executable
and no price is fabricated — a missing governed quote is a real NO_BET at zero
stake.

The primary bankroll instrument is the full-game moneyline on the model-favored
side (the same conservative instrument the official-position path uses), sized by
the governed quarter-Kelly policy with 1% / 2% / 5% position / same-game / daily
caps. Which games are eligible is decided by the existing opportunity board:
a primary position is frozen only for a game the board rates QUALIFIED_BET.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bankroll.service import (
    SettlementResult,
    StakePolicy,
    StakingStrategy,
    calculate_stake,
    settle_profit,
)
from app.bankroll.settlement import determine_result
from app.core.config import Settings
from app.markets.odds import decimal_to_american
from app.models import (
    ExperimentDecisionPhase,
    Forecast,
    Game,
    GameStatus,
    Market,
    MarketOutcome,
    OddsSnapshot,
    OfficialAspPosition,
    PositionResult,
    PositionSettlement,
    PositionStatus,
    ShadowExperiment,
    ShadowExperimentDecision,
)

FINAL_PREGAME_CUTOFF_MINUTES = 15
# The FINAL_PREGAME phase may only be frozen inside this window before the
# cutoff (three freeze cadences of 5 minutes, so a single missed run cannot
# leave a slate without a final evaluation). Versioned with the pipeline:
# AISP_FINAL_FREEZE_WINDOW_V1.
FINAL_FREEZE_WINDOW_MINUTES = 15
FINAL_FREEZE_WINDOW_VERSION = "AISP_FINAL_FREEZE_WINDOW_V1"


def in_final_window(cutoff: datetime, now: datetime) -> bool:
    """True when ``now`` lies in [cutoff - FINAL_FREEZE_WINDOW_MINUTES, cutoff]."""
    cutoff = _aware(cutoff)
    now = _aware(now)
    return cutoff - timedelta(minutes=FINAL_FREEZE_WINDOW_MINUTES) <= now <= cutoff
EXPERIMENT_NAME = "ASP MODEL BANKROLL — 2026 V1"
SUPPORTED_SPORT = "MLB"
RECOMMENDATION_POLICY_VERSION = "AISP_OPPORTUNITY_POLICY_V1"
STAKING_POLICY_VERSION = "AISP_QUARTER_KELLY_1_2_5_V1"
DECISION_POLICY = RECOMMENDATION_POLICY_VERSION
AISP_BASELINE_ENGINE = "AISP_BASELINE"
SUPPORTED_MARKETS = ["MONEYLINE"]

# Practical pregame challenger (separate immutable experiment; V1 untouched).
# V2.1 supersedes the never-activated V2 registration, whose qualification
# contract inherited the composite feature_completeness gate. ShadowExperiment
# rows are append-only, so supersession is recorded here (a deterministic,
# versioned code registry) plus an audited AuditEvent — never by mutating the
# preserved V2 row.
V2_EXPERIMENT_NAME = "ASP PRACTICAL PREGAME BANKROLL — 2026 V2.1"
V2_RECOMMENDATION_POLICY_VERSION = "AISP_PRACTICAL_PREGAME_POLICY_V2_1"
V2_STAKING_POLICY_VERSION = "AISP_QUARTER_KELLY_EVIDENCE_V2"
V2_DECISION_POLICY = V2_RECOMMENDATION_POLICY_VERSION
SUPERSEDED_POLICIES: dict[str, str] = {
    "AISP_PRACTICAL_PREGAME_POLICY_V2": "SUPERSEDED_BEFORE_ACTIVATION",
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ShadowExperimentService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- experiment start authority --------------------------------------
    def active_experiment(self, policy: str = DECISION_POLICY) -> ShadowExperiment | None:
        """The active experiment for one decision policy.

        Defaults to the V1 strict control so every pre-existing call site keeps
        its exact behavior; the V2 challenger never hijacks a V1 read."""

        from app.services.funnel_read_context import attached_context

        ctx = attached_context(self.session)
        if ctx is not None:
            # Request-scoped read model: the active list was loaded once for
            # the request (highest version per policy, same rule as below).
            matches = [e for e in ctx.active_experiments if e.decision_policy == policy]
            return max(matches, key=lambda e: e.version) if matches else None
        return self.session.scalar(
            select(ShadowExperiment).where(
                ShadowExperiment.is_active.is_(True),
                ShadowExperiment.decision_policy == policy,
            ).order_by(ShadowExperiment.version.desc()).limit(1)
        )

    def active_experiments(self) -> list[ShadowExperiment]:
        """Every operationally active experiment (V1 control first), one per
        decision policy. Superseded-before-activation policies are excluded
        from all scheduling, freezing, settlement, and default reads — their
        rows remain preserved and immutable."""

        rows = list(self.session.scalars(
            select(ShadowExperiment).where(ShadowExperiment.is_active.is_(True))
            .order_by(ShadowExperiment.version.desc())
        ))
        latest: dict[str, ShadowExperiment] = {}
        for row in rows:
            if row.decision_policy in SUPERSEDED_POLICIES:
                continue
            latest.setdefault(row.decision_policy, row)
        return sorted(latest.values(), key=lambda row: row.version)

    def superseded_experiments(self) -> list[tuple[ShadowExperiment, str]]:
        """Preserved registrations superseded before activation, with status."""

        rows = list(self.session.scalars(
            select(ShadowExperiment).where(
                ShadowExperiment.decision_policy.in_(list(SUPERSEDED_POLICIES)),
            ).order_by(ShadowExperiment.version)
        ))
        return [(row, SUPERSEDED_POLICIES[row.decision_policy]) for row in rows]

    def ensure_started(
        self, *, start_date: date, now: datetime | None = None, code_commit: str | None = None,
    ) -> ShadowExperiment:
        """Create the immutable experiment start record once; idempotent.

        The start date, bankroll and policy are never rewritten — a later policy
        change must create a new version instead."""

        existing = self.active_experiment()
        if existing is not None:
            return existing
        now = now or datetime.now(UTC)
        actual_local_date = _aware(now).astimezone(ZoneInfo(self.settings.display_timezone)).date()
        if start_date != actual_local_date:
            raise ValueError(
                f"Experiment start_date must be the actual local date {actual_local_date}; "
                "historical backfill is not permitted"
            )
        commit = (code_commit or self.settings.build_commit or self._local_commit()).strip()
        if not commit:
            raise RuntimeError("A code commit identity is required to start the experiment")
        next_version = (self.session.scalar(select(ShadowExperiment.version).order_by(ShadowExperiment.version.desc()).limit(1)) or 0) + 1
        experiment = ShadowExperiment(
            name=EXPERIMENT_NAME,
            version=next_version,
            started_at=now,
            start_date=start_date,
            starting_bankroll=self.settings.shadow_virtual_bankroll,
            sport=SUPPORTED_SPORT,
            recommendation_policy_version=RECOMMENDATION_POLICY_VERSION,
            staking_policy_version=STAKING_POLICY_VERSION,
            decision_policy=DECISION_POLICY,
            controlling_engine=AISP_BASELINE_ENGINE,
            supported_markets=list(SUPPORTED_MARKETS),
            kelly_fraction=self.settings.shadow_kelly_fraction,
            unit_percentage=self.settings.shadow_unit_percentage,
            max_position_percentage=self.settings.shadow_max_position_percentage,
            max_same_game_percentage=self.settings.shadow_max_same_game_percentage,
            max_daily_percentage=self.settings.shadow_max_daily_percentage,
            code_commit=commit,
            code_identity=f"git:{commit}",
            is_active=True,
        )
        self.session.add(experiment)
        self.session.flush()
        return experiment

    def ensure_v2_started(
        self, *, effective_date: date, now: datetime | None = None, code_commit: str | None = None,
    ) -> ShadowExperiment:
        """Register the V2 practical pregame challenger prospectively; idempotent.

        The effective date must be strictly in the future (never today or
        earlier): V2 launches only prospectively and can never backfill or
        replay a slate. Its frozen policy calibration (penalties, checksum,
        multiplier, caps) is persisted on the row and never rewritten.
        """

        from app.services.practical_policy import (
            POLICY_VERSION as V2_POLICY,
            STAKING_VERSION as V2_STAKING,
            frozen_policy_config,
        )

        existing = self.active_experiment(V2_DECISION_POLICY)
        if existing is not None:
            return existing
        now = now or datetime.now(UTC)
        actual_local_date = _aware(now).astimezone(ZoneInfo(self.settings.display_timezone)).date()
        if effective_date <= actual_local_date:
            raise ValueError(
                f"V2 effective_date must be after the current local date {actual_local_date}; "
                "the challenger starts prospectively only"
            )
        commit = (code_commit or self.settings.build_commit or self._local_commit()).strip()
        if not commit:
            raise RuntimeError("A code commit identity is required to start the V2 experiment")
        next_version = (self.session.scalar(select(ShadowExperiment.version).order_by(ShadowExperiment.version.desc()).limit(1)) or 0) + 1
        # Audit the supersession of any never-activated predecessor exactly
        # once. The predecessor row itself is append-only and stays untouched.
        from app.models import AuditEvent

        for predecessor, status in self.superseded_experiments():
            already = self.session.scalar(select(AuditEvent.id).where(
                AuditEvent.action == "experiment.superseded_before_activation",
                AuditEvent.entity_id == predecessor.id,
            ).limit(1))
            if already is None:
                self.session.add(AuditEvent(
                    actor_user_id=None,
                    action="experiment.superseded_before_activation",
                    entity_type="shadow_experiment",
                    entity_id=predecessor.id,
                    metadata_json={
                        "status": status,
                        "superseded_policy": predecessor.decision_policy,
                        "superseding_policy": V2_DECISION_POLICY,
                        "reason": (
                            "Qualification contract corrected: the composite "
                            "feature_completeness gate duplicated the governed "
                            "starter/lineup states and was structurally "
                            "unsatisfiable; the registration never activated "
                            "(zero decisions, zero positions)."
                        ),
                    },
                ))
        experiment = ShadowExperiment(
            name=V2_EXPERIMENT_NAME,
            version=next_version,
            started_at=now,
            start_date=effective_date,
            starting_bankroll=self.settings.shadow_virtual_bankroll,
            sport=SUPPORTED_SPORT,
            recommendation_policy_version=V2_POLICY,
            staking_policy_version=V2_STAKING,
            decision_policy=V2_DECISION_POLICY,
            controlling_engine=AISP_BASELINE_ENGINE,
            supported_markets=list(SUPPORTED_MARKETS),
            kelly_fraction=self.settings.shadow_kelly_fraction,
            unit_percentage=self.settings.shadow_unit_percentage,
            max_position_percentage=self.settings.shadow_max_position_percentage,
            max_same_game_percentage=self.settings.shadow_max_same_game_percentage,
            max_daily_percentage=self.settings.shadow_max_daily_percentage,
            code_commit=commit,
            code_identity=f"git:{commit}",
            is_active=True,
            policy_config=frozen_policy_config(self.settings),
        )
        self.session.add(experiment)
        self.session.flush()
        return experiment

    @staticmethod
    def _local_commit() -> str:
        try:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                check=True, timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    # -- bankroll accounting ---------------------------------------------
    def _settlements(self, experiment: ShadowExperiment) -> list[PositionSettlement]:
        return list(self.session.scalars(
            select(PositionSettlement)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id)
            .order_by(PositionSettlement.settled_at)
        ))

    def _realized_profit(self, experiment: ShadowExperiment) -> float:
        return sum(float(row.profit_loss) for row in self._settlements(experiment))

    def _reserved_open_stake(self, experiment: ShadowExperiment) -> float:
        return sum(
            float(row.stake) for row in self.session.scalars(
                select(OfficialAspPosition).where(
                    OfficialAspPosition.shadow_experiment_id == experiment.id,
                    OfficialAspPosition.status == PositionStatus.OPEN,
                )
            )
        )

    def current_bankroll(self, experiment: ShadowExperiment) -> float:
        return float(experiment.starting_bankroll) + self._realized_profit(experiment)

    def _available_bankroll(self, experiment: ShadowExperiment) -> float:
        return max(0.0, self.current_bankroll(experiment) - self._reserved_open_stake(experiment))

    # -- freezing decisions ----------------------------------------------
    def freeze(
        self,
        event_date: date,
        *,
        phase: ExperimentDecisionPhase,
        now: datetime | None = None,
        experiment: ShadowExperiment | None = None,
    ) -> dict[str, Any]:
        experiment = experiment or self.active_experiment()
        if experiment is None:
            return {"note": "no active experiment", "frozen": 0, "primary": 0}
        now = now or datetime.now(UTC)
        if event_date < experiment.start_date:
            return {"note": "no historical backfill", "frozen": 0, "primary": 0,
                    "policy": experiment.decision_policy}

        from app.services.read_service import ReadService

        read = ReadService(self.session, self.settings)
        games = read._live_games_for_date(event_date)
        games = [game for game in games if _aware(game.starts_at) >= _aware(experiment.started_at)]
        if not games:
            return {"note": "no live games", "frozen": 0, "primary": 0}
        cards = [read.game_card(game) for game in games]
        forecasts = [read._latest_forecast(game.id) for game in games]
        opportunities, _ = read._daily_opportunities(games, cards, forecasts)
        opportunity_by_game = {item.game_id: item for item in opportunities}
        is_v2 = experiment.decision_policy == V2_DECISION_POLICY

        frozen = 0
        primary_count = 0
        for game, card, forecast in zip(games, cards, forecasts):
            if phase == ExperimentDecisionPhase.LINEUP_CONFIRMED and not self._lineup_confirmed(card):
                continue  # capture this phase only once the lineup is actually confirmed
            cutoff = _aware(game.starts_at) - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
            if (
                phase == ExperimentDecisionPhase.FINAL_PREGAME
                and now <= cutoff
                and not in_final_window(cutoff, now)
            ):
                # The FINAL_PREGAME phase is the last evaluation before the
                # cutoff. Evaluating it all day (every run while now <= cutoff)
                # wrote hundreds of "final" rows per slate aged against a
                # future cutoff. Earlier snapshots remain the MORNING phase.
                # Post-cutoff runs fall through to position creation, which
                # consumes only the persisted at-cutoff decision.
                continue
            opportunity = opportunity_by_game.get(str(game.id))
            v2 = None
            if is_v2:
                # Same point-in-time snapshot as V1; independent evaluation.
                from app.services.practical_policy import PracticalPolicyEvaluator

                v2 = PracticalPolicyEvaluator(self.session, self.settings).evaluate(
                    game, forecast,
                    getattr(opportunity, "availability", None),
                    self._moneyline_selection(opportunity),
                    now=now, cutoff=cutoff,
                    final=(phase == ExperimentDecisionPhase.FINAL_PREGAME),
                )
                if now <= cutoff and self._refresh_for_newly_stable_identity(game, v2):
                    # One governed refresh when stability was established after
                    # the controlling forecast; re-evaluate with the new
                    # forecast. The refreshed forecast postdates the stable
                    # identity, so this can never repeat for the same streak.
                    from app.services.read_service import ReadService

                    forecast = ReadService(self.session, self.settings)._latest_forecast(game.id)
                    v2 = PracticalPolicyEvaluator(self.session, self.settings).evaluate(
                        game, forecast,
                        getattr(opportunity, "availability", None),
                        self._moneyline_selection(opportunity),
                        now=now, cutoff=cutoff,
                        final=(phase == ExperimentDecisionPhase.FINAL_PREGAME),
                    )
            if now <= cutoff:
                frozen += int(self._append_decision(
                    experiment, game, forecast, phase, opportunity, now, v2=v2,
                ) is not None)
            if (
                phase == ExperimentDecisionPhase.FINAL_PREGAME
                and cutoff <= now < _aware(game.starts_at)
                and self._create_canonical_position(experiment, game, event_date, now)
            ):
                primary_count += 1

        self.session.commit()
        return {"frozen": frozen, "primary": primary_count, "phase": phase.value,
                "games": len(games), "policy": experiment.decision_policy}

    def _refresh_for_newly_stable_identity(self, game: Game, v2: Any) -> bool:
        """§ starter-lifecycle rule: when PROBABLE_STABLE is first established
        and the controlling forecast does not yet contain that identity,
        trigger exactly ONE governed affected-game forecast refresh. Identical
        repeated observations never trigger anything."""

        stale_identity_reason = "Current forecast predates the stable starter identity"
        sides = [s for s in (getattr(v2, "starter_home", None), getattr(v2, "starter_away", None)) if s is not None]
        needs = any(
            list(side.reasons) == [stale_identity_reason]
            for side in sides
            if not side.eligible
        )
        if not needs:
            return False
        try:
            from app.services.forecast_service import ForecastService

            ForecastService(self.session, self.settings).generate_for_game(game)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            return False

    @staticmethod
    def _lineup_confirmed(card: Any) -> bool:
        away = card.away_lineup.status if getattr(card, "away_lineup", None) else None
        home = card.home_lineup.status if getattr(card, "home_lineup", None) else None
        return away == "CONFIRMED" and home == "CONFIRMED"

    @staticmethod
    def _moneyline_selection(opportunity: Any) -> Any | None:
        if opportunity is None:
            return None
        choices = [opportunity.best, *opportunity.alternates]
        return next((item for item in choices if item and item.market_type.upper() == "MONEYLINE"), None)

    @staticmethod
    def _selection_evidence(
        opportunity: Any, bet: Any | None, forecast: Forecast | None,
    ) -> tuple[str, list[str]]:
        """Describe the exact bound moneyline candidate, not another market."""

        if bet is None:
            if forecast is None:
                return "Forecast unavailable.", ["Forecast unavailable"]
            reason = "No complete two-sided governed MONEYLINE quote is available."
            return reason, [reason]

        state = bet.recommendation
        adjusted_edge = bet.uncertainty_adjusted_edge
        edge_text = f"{adjusted_edge:+.1f} pts" if adjusted_edge is not None else "unavailable"
        if state == "QUALIFIED_BET":
            reason = (
                f"Exact MONEYLINE selection {bet.selection} is QUALIFIED_BET under the existing "
                f"policy with uncertainty-adjusted edge {edge_text} and a fresh governed quote."
            )
        elif state == "PROVISIONAL_EDGE":
            reason = (
                f"Exact MONEYLINE selection {bet.selection} is PROVISIONAL_EDGE under the existing "
                f"policy; uncertainty-adjusted edge is {edge_text}."
            )
        elif state == "EXTREME_DISAGREEMENT":
            reason = (
                f"Exact MONEYLINE selection {bet.selection} is EXTREME_DISAGREEMENT under the "
                "existing policy and requires review; no position is permitted."
            )
        elif state == "WATCH":
            reason = (
                f"Exact MONEYLINE selection {bet.selection} is WATCH under the existing policy; "
                "no position is permitted."
            )
        else:
            reason = (
                f"Exact MONEYLINE selection {bet.selection} is {state} under the existing policy; "
                "no position is permitted."
            )

        inherited = list(opportunity.qualification_reasons or []) if opportunity else []
        reasons = [
            value for value in inherited
            if value not in {"Latest quote is stale", "Extreme model-market disagreement"}
        ]
        if bet.is_stale:
            reasons.append("Latest quote is stale")
        if state == "EXTREME_DISAGREEMENT":
            reasons.append("Extreme model-market disagreement")
        if state != "QUALIFIED_BET" and not reasons:
            reasons.append(f"Exact MONEYLINE selection classified {state}")
        return reason, list(dict.fromkeys(reasons))[:8]

    def _append_decision(
        self, experiment, game, forecast, phase, opportunity, now, *, v2=None,
    ) -> ShadowExperimentDecision | None:
        bet = self._moneyline_selection(opportunity)
        if v2 is not None:
            # V2 challenger: the recommendation and reasons come from the
            # independent practical-policy evaluation of the SAME snapshot.
            state = v2.recommendation
            reason, reasons = v2.reason, list(v2.reasons)
        else:
            state = bet.recommendation if bet is not None else "NO_BET"
            reason, reasons = self._selection_evidence(opportunity, bet, forecast)
        version = f"{forecast.lifecycle.value.upper()} · v{forecast.version}" if forecast else None
        payload = {
            "experiment": str(experiment.id), "game": str(game.id), "phase": phase.value,
            "forecast": str(forecast.id) if forecast else None,
            "outcome": bet.market_outcome_id if bet else None,
            "snapshot": bet.odds_snapshot_id if bet else None,
            "recommendation": state, "reasons": reasons,
        }
        if v2 is not None:
            payload["v2_evidence"] = v2.evidence_json()
        fingerprint = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        if self.session.scalar(select(ShadowExperimentDecision.id).where(
            ShadowExperimentDecision.evidence_fingerprint == fingerprint
        )):
            return None
        outcome_id = self._uuid(bet.market_outcome_id) if bet else None
        snapshot_id = self._uuid(bet.odds_snapshot_id) if bet else None
        decision = ShadowExperimentDecision(
            experiment_id=experiment.id,
            game_id=game.id,
            phase=phase,
            slate_date=(
                _aware(game.starts_at).astimezone(ZoneInfo(self.settings.display_timezone)).date()
                if game.starts_at else _aware(now).astimezone(ZoneInfo(self.settings.display_timezone)).date()
            ),
            sport="BASEBALL_MLB",
            market="MONEYLINE",
            period="Full game",
            selection=bet.selection if bet else "—",
            line=bet.line if bet else None,
            sportsbook=bet.sportsbook if bet else None,
            american_odds=bet.american_odds if bet else None,
            decimal_odds=bet.decimal_odds if bet else None,
            market_outcome_id=outcome_id,
            odds_snapshot_id=snapshot_id,
            quote_observed_at=bet.quote_observed_at if bet else None,
            forecast_id=forecast.id if forecast else None,
            forecast_generated_at=forecast.generated_at if forecast else None,
            forecast_cutoff=forecast.available_as_of if forecast else None,
            forecast_version=version,
            recommended_at=now,
            model_probability=bet.model_probability_fraction if bet else None,
            no_vig_probability=bet.no_vig_probability_fraction if bet else None,
            edge=bet.edge_fraction if bet else None,
            expected_value=bet.expected_value_fraction if bet else None,
            readiness=forecast.readiness_score if forecast else None,
            uncertainty=forecast.uncertainty if forecast else None,
            recommendation=state if (bet is not None or v2 is not None) else "NO_BET",
            recommendation_reason=reason,
            qualification_reasons=reasons,
            evidence_fingerprint=fingerprint,
            source_mode=game.source_mode,
            evidence=v2.evidence_json() if v2 is not None else None,
        )
        self.session.add(decision)
        self.session.flush()
        return decision

    @staticmethod
    def _uuid(value: Any) -> UUID | None:
        try:
            return UUID(str(value)) if value else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def compute_position_stake(experiment: Any, decision: Any, bankroll: float) -> float | None:
        """Pre-portfolio-cap stake for one decision under its experiment policy.

        V1: quarter-Kelly on the frozen model probability with the experiment's
        position cap. V2 (a decision carrying evidence): quarter-Kelly on the
        ADJUSTED probability (after uncertainty + evidence penalties), times the
        evidence multiplier, capped at the tier's position-cap fraction — all
        values frozen on the decision at recommendation time. Zero or negative
        Kelly remains NO_POSITION (returns None)."""

        evidence = decision.evidence or None
        if evidence is not None:
            staking_probability = float(evidence.get("adjusted_probability") or 0.0)
            cap_fraction = float(evidence.get("position_cap_fraction") or experiment.max_position_percentage)
            multiplier = float(evidence.get("evidence_multiplier") or 1.0)
        else:
            staking_probability = float(decision.model_probability)
            cap_fraction = experiment.max_position_percentage
            multiplier = 1.0
        if staking_probability <= 0:
            return None
        policy = StakePolicy(
            strategy=StakingStrategy.FRACTIONAL_KELLY,
            kelly_fraction=experiment.kelly_fraction,
            maximum_stake_percentage=cap_fraction,
        )
        stake = calculate_stake(
            bankroll,
            policy,
            model_probability=staking_probability,
            decimal_odds=float(decision.decimal_odds),
        ) * multiplier
        stake = min(stake, bankroll * cap_fraction)
        return stake if stake > 0 else None

    def _create_canonical_position(
        self,
        experiment: ShadowExperiment,
        game: Game,
        event_date: date,
        now: datetime,
    ) -> bool:
        """Create exactly one canonical virtual position from cutoff-safe evidence."""

        cutoff = _aware(game.starts_at) - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
        decision = self.session.scalar(
            select(ShadowExperimentDecision).where(
                ShadowExperimentDecision.experiment_id == experiment.id,
                ShadowExperimentDecision.game_id == game.id,
                ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
                ShadowExperimentDecision.recommended_at <= cutoff,
            ).order_by(ShadowExperimentDecision.recommended_at.desc()).limit(1)
        )
        if (
            decision is None
            or decision.recommendation != "QUALIFIED_BET"
            or decision.quote_observed_at is None
            or _aware(decision.quote_observed_at) > cutoff
            or decision.forecast_cutoff is None
            or _aware(decision.forecast_cutoff) > cutoff
            or decision.market_outcome_id is None
            or decision.odds_snapshot_id is None
            or decision.forecast_id is None
            or decision.decimal_odds is None
            or decision.model_probability is None
        ):
            return False
        if self.session.scalar(select(OfficialAspPosition.id).where(
            OfficialAspPosition.shadow_experiment_id == experiment.id,
            OfficialAspPosition.market_outcome_id == decision.market_outcome_id,
        )):
            return False
        snapshot = self.session.get(OddsSnapshot, decision.odds_snapshot_id)
        forecast = self.session.get(Forecast, decision.forecast_id)
        if (
            snapshot is None
            or forecast is None
            or snapshot.market_outcome_id != decision.market_outcome_id
            or snapshot.decimal_odds is None
            or abs(float(snapshot.decimal_odds) - float(decision.decimal_odds)) > 1e-9
            or _aware(snapshot.observed_at) != _aware(decision.quote_observed_at)
            or _aware(forecast.available_as_of) > cutoff
            or _aware(forecast.generated_at) > cutoff
            or decision.source_mode != "LIVE"
        ):
            return False

        bankroll = self.current_bankroll(experiment)
        available = self._available_bankroll(experiment)
        stake = self.compute_position_stake(experiment, decision, bankroll)
        if stake is None:
            return False
        daily_used = sum(float(row.stake) for row in self.session.scalars(
            select(OfficialAspPosition)
            .join(ShadowExperimentDecision, ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
            .where(
                OfficialAspPosition.shadow_experiment_id == experiment.id,
                ShadowExperimentDecision.slate_date == event_date,
            )
        ))
        same_game_used = sum(float(row.stake) for row in self.session.scalars(
            select(OfficialAspPosition)
            .join(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .where(
                OfficialAspPosition.shadow_experiment_id == experiment.id,
                Market.game_id == game.id,
            )
        ))
        stake = min(
            stake,
            available,
            max(0.0, bankroll * experiment.max_same_game_percentage - same_game_used),
            max(0.0, bankroll * experiment.max_daily_percentage - daily_used),
        )
        stake = round(stake, 2)
        if stake <= 0:
            return False

        self.session.add(OfficialAspPosition(
            forecast_id=decision.forecast_id,
            market_outcome_id=decision.market_outcome_id,
            odds_snapshot_id=decision.odds_snapshot_id,
            placed_at=decision.recommended_at,
            stake=stake,
            decimal_odds=float(decision.decimal_odds),
            result=PositionResult.PENDING,
            status=PositionStatus.OPEN,
            strategy=experiment.staking_policy_version,
            shadow_experiment_id=experiment.id,
            shadow_decision_id=decision.id,
            bankroll_before_position=round(available, 2),
            recommendation_policy_version=experiment.recommendation_policy_version,
            staking_policy_version=experiment.staking_policy_version,
        ))
        self.session.flush()
        return True

    # -- settlement ------------------------------------------------------
    def settle(self, event_date: date | None = None, *, now: datetime | None = None) -> dict[str, Any]:
        """Settle every active experiment's open positions independently."""

        experiments = self.active_experiments()
        if not experiments:
            return {"note": "no active experiment", "settled": 0}
        total = 0
        per_experiment: dict[str, Any] = {}
        for experiment in experiments:
            result = self._settle_experiment(experiment, now=now)
            total += result["settled"]
            per_experiment[experiment.decision_policy] = result
        return {"settled": total, "experiments": per_experiment}

    def _settle_experiment(self, experiment: ShadowExperiment, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        positions = list(self.session.scalars(select(OfficialAspPosition).where(
            OfficialAspPosition.shadow_experiment_id == experiment.id,
            OfficialAspPosition.status == PositionStatus.OPEN,
        ).order_by(OfficialAspPosition.placed_at)))
        running = self.current_bankroll(experiment)
        settled = 0
        for position in positions:
            if self.session.scalar(select(PositionSettlement.id).where(
                PositionSettlement.position_id == position.id
            )):
                continue
            decision = self.session.get(ShadowExperimentDecision, position.shadow_decision_id)
            outcome = self.session.get(MarketOutcome, position.market_outcome_id)
            market = self.session.get(Market, outcome.market_id) if outcome else None
            game = self.session.get(Game, market.game_id) if market else None
            if decision is None or outcome is None or market is None or game is None:
                continue
            cancelled = game.status == GameStatus.CANCELED
            if not cancelled and (
                game.status != GameStatus.FINAL
                or game.home_score is None
                or game.away_score is None
            ):
                continue
            result = PositionResult.VOID if cancelled else determine_result(self.session, game, market, outcome)
            profit = settle_profit(
                float(position.stake),
                decimal_odds=float(position.decimal_odds),
                result=SettlementResult(result.value),
            )
            before = running
            after = round(before + profit, 2)
            running = after
            closing = self._closing_snapshot(position, game)
            clv, movement, beat_close, closing_american, closing_decimal = self._closing_line_value(position, closing)
            position.result = result
            position.status = PositionStatus.VOID if cancelled else PositionStatus.SETTLED
            position.settled_at = now
            position.payout = round(float(position.stake) + profit, 2)
            position.closing_decimal_odds = closing_decimal
            self.session.add(PositionSettlement(
                position_id=position.id,
                result=result,
                profit_loss=round(profit, 2),
                bankroll_before=round(before, 2), bankroll_after=after,
                game_home_score=game.home_score,
                game_away_score=game.away_score,
                settlement_note=(
                    "CANCELLED — virtual stake voided"
                    if cancelled else "Settled deterministically from canonical final score"
                ),
                closing_odds_snapshot_id=closing.id if closing else None,
                closing_american_odds=closing_american,
                closing_decimal_odds=closing_decimal,
                implied_probability_movement=movement,
                closing_line_value=clv, beat_close=beat_close, settled_at=now,
            ))
            settled += 1
        self.session.commit()
        return {"settled": settled, "current_bankroll": round(running, 2)}

    def _closing_snapshot(self, position: OfficialAspPosition, game: Game) -> OddsSnapshot | None:
        """Last legitimate same-book price after recommendation and before first pitch."""
        recommendation = self.session.get(OddsSnapshot, position.odds_snapshot_id)
        if recommendation is None:
            return None
        first_pitch = _aware(game.starts_at)
        return self.session.scalar(
            select(OddsSnapshot).where(
                OddsSnapshot.market_outcome_id == position.market_outcome_id,
                OddsSnapshot.sportsbook_id == recommendation.sportsbook_id,
                OddsSnapshot.observed_at > recommendation.observed_at,
                OddsSnapshot.observed_at <= first_pitch,
                OddsSnapshot.source_mode == recommendation.source_mode,
            ).order_by(OddsSnapshot.observed_at.desc()).limit(1)
        )

    # -- read/ledger aggregation -----------------------------------------
    def ledger_view(self, experiment: ShadowExperiment) -> dict[str, Any]:
        """Aggregate the experiment ledger for the bankroll + scorecard reads."""
        position_rows = list(self.session.execute(
            select(OfficialAspPosition, ShadowExperimentDecision)
            .join(ShadowExperimentDecision, ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id)
            .order_by(OfficialAspPosition.placed_at)
        ))
        settlements = {row.position_id: row for row in self.session.scalars(
            select(PositionSettlement)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id)
        )}
        starting = float(experiment.starting_bankroll)
        balance = starting
        peak = starting
        max_dd = 0.0
        streak = 0
        longest_streak = 0
        wins = losses = pushes = voids = 0
        total_risked = 0.0
        clvs: list[float] = []
        beats = 0
        rows: list[dict[str, Any]] = []
        equity = [{"date": experiment.start_date.isoformat(), "bankroll": round(starting, 2), "benchmark": round(starting, 2)}]
        monthly: dict[str, dict[str, Any]] = {}
        breakdowns: dict[str, dict[str, dict[str, float]]] = {"tier": {}, "market": {}, "month": {}, "engine": {}, "sportsbook": {}}
        settlement_events = sorted(
            (
                (settlement, decision)
                for position, decision in position_rows
                if (settlement := settlements.get(position.id)) is not None
            ),
            key=lambda item: (_aware(item[0].settled_at), _aware(item[1].recommended_at)),
        )
        for settlement, _decision in settlement_events:
            balance = round(balance + float(settlement.profit_loss), 2)
            peak = max(peak, balance)
            max_dd = max(max_dd, peak - balance)
            settled_date = _aware(settlement.settled_at).astimezone(
                ZoneInfo(self.settings.display_timezone)
            ).date().isoformat()
            equity.append({"date": settled_date, "bankroll": balance, "benchmark": round(starting, 2)})
        for position, decision in position_rows:
            settlement = settlements.get(position.id)
            stake = float(position.stake)
            total_risked += stake
            result = settlement.result.value.upper() if settlement else "OPEN"
            profit = float(settlement.profit_loss) if settlement else 0.0
            if settlement is not None:
                if result == "WIN":
                    wins += 1
                    streak = 0
                elif result == "LOSS":
                    losses += 1
                    streak += 1
                    longest_streak = max(longest_streak, streak)
                elif result == "PUSH":
                    pushes += 1
                else:
                    voids += 1
                if settlement.closing_line_value is not None:
                    clvs.append(float(settlement.closing_line_value))
                if settlement.beat_close:
                    beats += 1
            month = decision.slate_date.strftime("%Y-%m")
            for dim, key in (("tier", decision.recommendation), ("market", decision.market), ("month", month),
                             ("engine", experiment.controlling_engine), ("sportsbook", decision.sportsbook or "—")):
                bucket = breakdowns[dim].setdefault(key, {"positions": 0, "settled": 0, "wins": 0, "losses": 0, "pushes": 0, "voids": 0, "profit_loss": 0.0, "staked": 0.0})
                bucket["positions"] += 1
                bucket["staked"] += stake
                if settlement is not None:
                    bucket["settled"] += 1
                    bucket["profit_loss"] += profit
                    bucket[{"WIN": "wins", "LOSS": "losses", "PUSH": "pushes", "VOID": "voids"}.get(result, "voids")] += 1
            mbucket = monthly.setdefault(month, {"month": month, "positions": 0, "settled": 0, "wins": 0, "losses": 0, "pushes": 0, "profit_loss": 0.0, "stake": 0.0})
            mbucket["positions"] += 1
            mbucket["stake"] += stake
            mbucket["profit_loss"] += profit
            if settlement is not None:
                mbucket["settled"] += 1
                if result == "WIN":
                    mbucket["wins"] += 1
                elif result == "LOSS":
                    mbucket["losses"] += 1
                elif result == "PUSH":
                    mbucket["pushes"] += 1
            rows.append({
                "id": str(position.id), "date": decision.slate_date, "game_id": str(decision.game_id),
                "selection": decision.selection, "market": decision.market.title(),
                "price": decision.american_odds or decimal_to_american(position.decimal_odds), "stake": round(stake, 2),
                "result": result, "profit_loss": round(profit, 2),
                "closing_price": settlement.closing_american_odds if settlement else None,
                "clv": settlement.closing_line_value if settlement else None,
            })
        settled_count = len(settlements)
        net = balance - starting
        roi = (net / total_risked * 100) if total_risked else None
        slate_dates = set(self.session.scalars(select(ShadowExperimentDecision.slate_date).where(ShadowExperimentDecision.experiment_id == experiment.id)))
        primary_dates = {decision.slate_date for _, decision in position_rows}
        slate_dates.add(experiment.start_date)
        no_bet_days = len(slate_dates - primary_dates)
        betting = {
            "starting_bankroll": round(starting, 2), "current_bankroll": round(balance, 2),
            "net_profit_loss": round(net, 2), "total_risked": round(total_risked, 2),
            "roi": round(roi, 2) if roi is not None else None,
            "yield_pct": round(roi, 2) if roi is not None else None,
            "positions": len(position_rows), "settled": settled_count, "open": len(position_rows) - settled_count,
            "wins": wins, "losses": losses, "pushes": pushes, "voids": voids,
            "record": f"{wins}–{losses}–{pushes}",
            "max_drawdown": round(max_dd, 2), "longest_losing_streak": longest_streak,
            "closing_line_value": round(sum(clvs) / len(clvs), 3) if clvs else None,
            "beat_close_percentage": round(beats / len(clvs) * 100, 1) if clvs else None,
            "has_settled": settled_count > 0,
            "breakdowns": {
                dim: [
                    {"key": key, "positions": b["positions"], "settled": b["settled"],
                     "record": f"{b['wins']}–{b['losses']}–{b['pushes']}",
                     "profit_loss": round(b["profit_loss"], 2), "staked": round(b["staked"], 2),
                     "roi": round(b["profit_loss"] / b["staked"] * 100, 2) if b["staked"] and b["settled"] else None}
                    for key, b in sorted(dim_map.items())
                ]
                for dim, dim_map in breakdowns.items()
            },
        }
        monthly_rows = [{
            "month": item["month"], "positions": item["positions"],
            "record": f"{item['wins']}–{item['losses']}–{item['pushes']}",
            "profit_loss": round(item["profit_loss"], 2),
            "roi": item["profit_loss"] / item["stake"] * 100 if item["stake"] else 0,
        } for item in monthly.values() if item["settled"]]
        return {
            "starting": starting, "current": round(balance, 2), "peak": round(peak, 2),
            "net": round(net, 2), "roi": roi or 0.0, "max_drawdown": round(max_dd, 2),
            "record": f"{wins}–{losses}–{pushes}", "positions": rows,
            "equity": equity, "monthly": monthly_rows, "betting": betting,
            "open_positions": len(position_rows) - settled_count, "settled_positions": settled_count,
            "no_bet_days": no_bet_days,
            "daily": self._daily_rows(experiment, position_rows, settlements),
            "current_drawdown": round((peak - balance) / peak * 100, 3) if peak else 0.0,
            "closing_quote_coverage": {
                "positions_with_closing_quote": len(clvs),
                "settled_positions": settled_count,
                "percentage": round(len(clvs) / settled_count * 100, 1) if settled_count else None,
            },
        }

    def _daily_rows(self, experiment, position_rows, settlements) -> list[dict[str, Any]]:
        decisions = list(self.session.scalars(select(ShadowExperimentDecision).where(
            ShadowExperimentDecision.experiment_id == experiment.id
        ).order_by(ShadowExperimentDecision.slate_date, ShadowExperimentDecision.recommended_at)))
        dates = {experiment.start_date, *(decision.slate_date for decision in decisions)}
        balance = float(experiment.starting_bankroll)
        rows: list[dict[str, Any]] = []
        for slate_date in sorted(dates):
            day_positions = [(p, d) for p, d in position_rows if d.slate_date == slate_date]
            day_settlements = [settlements[p.id] for p, _ in day_positions if p.id in settlements]
            profit = sum(float(item.profit_loss) for item in day_settlements)
            balance += profit
            reasons: list[str] = []
            if not day_positions:
                for decision in decisions:
                    if decision.slate_date != slate_date:
                        continue
                    reasons.extend(str(value) for value in (decision.qualification_reasons or []))
                    if decision.recommendation_reason:
                        reasons.append(decision.recommendation_reason)
                if not reasons:
                    reasons.append("No live game had complete cutoff-safe qualified evidence.")
            rows.append({
                "date": slate_date.isoformat(),
                "positions": len(day_positions),
                "settled": len(day_settlements),
                "amount_risked": round(sum(float(p.stake) for p, _ in day_positions), 2),
                "profit_loss": round(profit, 2),
                "bankroll_after": round(balance, 2),
                "no_bet": not day_positions,
                "no_bet_reasons": list(dict.fromkeys(reasons))[:8],
            })
        return rows

    def _closing_line_value(self, position, closing):
        if closing is None or closing.decimal_odds is None or not position.decimal_odds:
            return None, None, None, None, None
        closing_decimal = closing.decimal_odds
        closing_american = int(closing.american_odds) if closing.american_odds is not None else int(decimal_to_american(closing_decimal))
        # CLV: our locked decimal odds vs the closing decimal odds. A higher price
        # than the close is positive CLV — we beat the closing line.
        clv = round((float(position.decimal_odds) / closing_decimal - 1.0) * 100, 3)
        movement = round((1.0 / closing_decimal) - (1.0 / float(position.decimal_odds)), 6)
        beat_close = float(position.decimal_odds) > closing_decimal
        return clv, movement, beat_close, closing_american, closing_decimal
