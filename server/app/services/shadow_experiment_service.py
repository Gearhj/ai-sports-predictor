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
    GameTeam,
    Team,
    ExperimentDecisionPhase,
    Forecast,
    Game,
    GameStatus,
    Market,
    MarketOutcome,
    ModelVersion,
    OddsSnapshot,
    OfficialAspPosition,
    PositionResult,
    PositionSettlement,
    PositionStatus,
    ShadowExperiment,
    ShadowExperimentDecision,
)
from app.services.league_scope import MLB, LeagueMismatchError, require_league

FINAL_PREGAME_CUTOFF_MINUTES = 15
#: Canonical position-path dispositions. The literal values are the ones
#: app.services.mlb_v18_funding_pause names; they are repeated here so the
#: flag-off path imports nothing new.
POSITION_CREATED = "CREATED"
POSITION_INELIGIBLE = "INELIGIBLE"
POSITION_NOT_FUNDED = "NOT_FUNDED"
#: The durable funding receipt for a FINAL_PREGAME QUALIFIED_BET that reached
#: staking (audit 2026-10-16, item 13 / MLB PC2 gap). Decision rows are
#: append-only and are written before the cutoff, while the position is sized
#: after it, so the disposition, its honest reason and the shared-allowance
#: snapshot are kept as one append-only audit event per (decision, disposition,
#: reason, stake) -- the MLB counterpart of NFL's ``staking_detail``.
FUNDING_RECEIPT_ACTION = "MLB_FINAL_PREGAME_FUNDING_V1"
#: Funding-receipt reason when the database refused the position INSERT (a
#: uniqueness backstop fired). The position is not created; that one game is
#: INELIGIBLE and every other game in the freeze run keeps its own outcome.
POSITION_WRITE_CONFLICT = "POSITION_UNIQUENESS_CONFLICT"
POSITION_WRITE_REFUSED = "POSITION_WRITE_REFUSED"
#: The partial unique index migration 20261019_0055 creates in place of
#: UNIQUE(shadow_experiment_id, market_outcome_id): one non-VOID position per
#: experiment and market outcome. Only its presence lets a VOID original
#: occurrence and its makeup's position share one outcome.
LIVE_OUTCOME_INDEX = "uq_official_position_experiment_outcome_live"
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


#: The decision row persists a decimal price in the canonical four-place
#: representation while the odds snapshot keeps full precision, so an exact
#: float comparison rejected a quote that was the same price.
PRICE_CANONICAL_PLACES = 4


def same_quoted_price(snapshot: Any, decision: Any) -> bool:
    """Whether the frozen decision names the snapshot's own price.

    Identity is decided elsewhere (snapshot id, market outcome, observation
    time); this compares only the PRICE, in the canonical representation the
    decision was written with. The American price must agree exactly and the
    decimal price must agree once both are expressed at the contract's four
    places. A different quote still fails: it carries a different American
    price or a decimal that differs beyond rounding.
    """

    snapshot_decimal = snapshot.decimal_odds
    decision_decimal = decision.decimal_odds
    if snapshot_decimal is None or decision_decimal is None:
        return False
    snapshot_american = getattr(snapshot, "american_odds", None)
    decision_american = getattr(decision, "american_odds", None)
    if snapshot_american is not None and decision_american is not None:
        if int(snapshot_american) != int(decision_american):
            return False
    return (
        round(float(snapshot_decimal), PRICE_CANONICAL_PLACES)
        == round(float(decision_decimal), PRICE_CANONICAL_PLACES)
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def live_outcome_index_present(session: Session) -> bool:
    """True when the database has ``LIVE_OUTCOME_INDEX`` (migration 20261019_0055 applied).

    Read from the catalog on the session's own connection, inside its
    transaction. Asked only on the rare path where a VOID prior of another
    occurrence would otherwise stop blocking, so it is not cached: applying
    the migration takes effect on the next evaluation.
    """

    from sqlalchemy import inspect as sa_inspect

    indexes = sa_inspect(session.connection()).get_indexes(OfficialAspPosition.__tablename__)
    return any(index.get("name") == LIVE_OUTCOME_INDEX and index.get("unique") for index in indexes)


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
        latest: dict[tuple[str, str | None], ShadowExperiment] = {}
        for row in rows:
            if row.decision_policy in SUPERSEDED_POLICIES:
                continue
            # Keyed by policy AND model era. A model era reuses its
            # predecessor's decision policy on purpose -- the rules are
            # unchanged, only the forecasting engine moved -- so keying on
            # policy alone would let the successor evict the predecessor from
            # every active read, and the predecessor's open positions would
            # stop settling the moment the era began.
            latest.setdefault((row.decision_policy, row.model_era_id), row)
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
        # Original settlements plus any correction recorded beside them. No
        # adjustment exists for MLB/NFL today, so this adds exactly 0.0 there.
        from app.cfb.settlement_corrections import adjustment_total

        settlements = self._settlements(experiment)
        return (sum(float(row.profit_loss) for row in settlements)
                + adjustment_total(self.session, [row.position_id for row in settlements]))

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
        # THIS OWNER FREEZES THE MLB SLATE, AND ONLY MLB EXPERIMENTS.
        #
        # It reads MLB games and never checked the experiment's own sport, so
        # every active experiment was evaluated against baseball: by 2026-09-15
        # the NFL paper experiment had written 76 decision rows on MLB games.
        # None became a position, but nothing prevented an MLB wager being
        # recorded against the NFL account. Each non-MLB paper policy has its
        # own checkpoint owner; this one declines them by name.
        if (experiment.sport or "MLB").upper() != "MLB":
            return {"note": "not an MLB experiment; evaluated by its own sport's checkpoint",
                    "frozen": 0, "primary": 0, "policy": experiment.decision_policy,
                    "sport": experiment.sport}
        now = now or datetime.now(UTC)
        if event_date < experiment.start_date:
            return {"note": "no historical backfill", "frozen": 0, "primary": 0,
                    "policy": experiment.decision_policy}
        successor = self._active_successor_era(experiment, now)
        if successor is not None:
            # The cutover is one-directional: from the boundary onward this
            # experiment admits no new decision and no new position. Its
            # existing open positions keep settling in its own ledger, which is
            # why this stops admission rather than deactivating the experiment.
            return {"note": "closed to new decisions at the model-era boundary",
                    "frozen": 0, "primary": 0, "policy": experiment.decision_policy,
                    "closed_by_model_era": successor.model_era_id,
                    "boundary_utc": _aware(successor.started_at).isoformat(),
                    "existing_positions_still_settle": True}

        from app.services.read_service import ReadService

        read = ReadService(self.session, self.settings)
        games = read._live_games_for_date(event_date, MLB)
        games = [game for game in games if _aware(game.starts_at) >= _aware(experiment.started_at)]
        if not games:
            return {"note": "no live games", "frozen": 0, "primary": 0}
        cards = [read.game_card(game) for game in games]
        # Which engine's saved forecasts control this experiment. For a model
        # era there is no fallback: a game without a valid V18 forecast is an
        # explicit skip, never a Gen1 decision wearing a V18 label.
        forecasts, era_skips = self._controlling_forecasts(experiment, games, now)
        opportunities, _ = read._daily_opportunities(games, cards, forecasts)
        opportunity_by_game = {item.game_id: item for item in opportunities}
        is_v2 = experiment.decision_policy == V2_DECISION_POLICY

        frozen = 0
        primary_count = 0
        # MLB_V18_FUNDING_PAUSE_V1: a receipt exists only while the flag is on,
        # so the flag-off payload is exactly what it always was.
        funding_pause = None
        if self.settings.mlb_v18_funding_pause_enabled:
            from app.services.mlb_v18_funding_pause import FundingPauseReceipt

            funding_pause = FundingPauseReceipt(experiment_id=str(experiment.id))
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
                and self._create_canonical_position(
                    experiment, game, event_date, now, funding_pause=funding_pause)
            ):
                primary_count += 1

        self.session.commit()
        payload = {"frozen": frozen, "primary": primary_count, "phase": phase.value,
                   "games": len(games), "policy": experiment.decision_policy}
        if funding_pause is not None:
            payload["funding_pause"] = funding_pause.as_dict()
        if experiment.model_era_id:
            payload["model_era_id"] = experiment.model_era_id
            payload["controlling_engine"] = experiment.controlling_engine
            payload["skipped_for_no_v18_forecast"] = len(era_skips)
            payload["skips"] = era_skips
        return payload

    def _active_successor_era(
        self, experiment: ShadowExperiment, now: datetime,
    ) -> ShadowExperiment | None:
        """A model era that has succeeded this experiment and already begun."""

        if experiment.model_era_id:
            return None
        return self.session.scalar(
            select(ShadowExperiment).where(
                ShadowExperiment.predecessor_experiment_id == experiment.id,
                ShadowExperiment.model_era_id.is_not(None),
                ShadowExperiment.is_active.is_(True),
                ShadowExperiment.started_at <= now,
            ).order_by(ShadowExperiment.started_at.desc()).limit(1)
        )

    def _controlling_forecasts(
        self, experiment: ShadowExperiment, games: list[Game], now: datetime,
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        """Resolve one forecast per game from the experiment's controlling engine.

        Gen1 experiments keep the behaviour they have always had. A model-era
        experiment reads its own engine's saved forecasts and records, per game,
        the exact reason when there is not a usable one -- because the whole
        value of the era boundary is that a V18 decision was made on a V18
        forecast, and a silent substitution would destroy that claim while
        leaving the numbers looking fine.
        """

        from app.services.read_service import ReadService

        read = ReadService(self.session, self.settings)
        if not experiment.model_era_id:
            return [read._latest_forecast(game.id) for game in games], []

        from app.services.v18_forecast_view import resolve_for_decision

        forecasts: list[Any] = []
        skips: list[dict[str, Any]] = []
        for game in games:
            cutoff = _aware(game.starts_at) - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
            view, reason = resolve_for_decision(
                self.session, game.id, cutoff=cutoff,
                engine_id=experiment.controlling_engine,
            )
            forecasts.append(view)
            if view is None:
                # Both clocks, and the UTC field actually in UTC.
                #
                # This used to emit `cutoff.isoformat()` under the name
                # `cutoff_utc`, and `cutoff` keeps the tzinfo of the game's
                # start -- so a field called UTC rendered "…T19:25:00-04:00",
                # which is Eastern. The instant was always right; the label was
                # not, and reconciling it against a producer inventory in real
                # UTC is exactly where that costs somebody an hour.
                skips.append({
                    "game_id": str(game.id), "reason": reason,
                    "cutoff_utc": cutoff.astimezone(UTC).isoformat(),
                    "cutoff_local": cutoff.astimezone(
                        ZoneInfo("America/New_York")).isoformat(),
                    "cutoff_minutes_before_start": FINAL_PREGAME_CUTOFF_MINUTES,
                })
        return forecasts, skips

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

    def _is_mlb_game(self, game: Game, process: str) -> bool:
        """Every decision and position here is MLB; another league's game is
        refused (and logged) even if a caller hands it in directly."""

        try:
            require_league(self.session, game, MLB, process=process)
        except LeagueMismatchError:
            return False
        return True

    def _append_decision(
        self, experiment, game, forecast, phase, opportunity, now, *, v2=None,
    ) -> ShadowExperimentDecision | None:
        if not self._is_mlb_game(game, "MLB experiment decision"):
            return None
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
        # A producer forecast has no row in `forecasts`; it is identified by its
        # `shadow_forecasts` row instead. Exactly one of the two is ever set, so
        # the decision always names the forecast it actually used.
        shadow_forecast_id = getattr(forecast, "shadow_forecast_id", None) if forecast else None
        forecast_row_id = forecast.id if (forecast and shadow_forecast_id is None) else None
        payload = {
            "experiment": str(experiment.id), "game": str(game.id), "phase": phase.value,
            "forecast": str(forecast_row_id or shadow_forecast_id) if forecast else None,
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
            forecast_id=forecast_row_id,
            shadow_forecast_id=shadow_forecast_id,
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
            evidence=self._decision_evidence(forecast, v2),
        )
        self.session.add(decision)
        self.session.flush()
        return decision

    @staticmethod
    def _decision_evidence(forecast: Any, v2: Any) -> dict[str, Any] | None:
        """The V2 evidence block, plus the model lineage when one applies.

        A model-era decision has to be able to answer "which fitted model and
        which simulation produced this number" from its own row, without the
        reader having to trust that the engine label meant what it says.
        """

        evidence = dict(v2.evidence_json()) if v2 is not None else None
        lineage = getattr(forecast, "lineage", None)
        if callable(lineage):
            evidence = dict(evidence or {})
            evidence["model_lineage"] = lineage()
        return evidence

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

    def _account_staking_basis(self, experiment: ShadowExperiment) -> dict[str, Any] | None:
        """Season-account capital for this era, or None if it has no account."""

        from app.services.experiment_accounts import ExperimentAccountService

        return ExperimentAccountService(self.session, self.settings).staking_basis(experiment)

    def _stake_used(
        self,
        experiment: ShadowExperiment,
        *,
        game: Game,
        event_date: date,
        basis: dict[str, Any] | None,
    ) -> tuple[float, float]:
        """Stake already committed today, and on this game.

        Account-wide when the era has an account, so an era change mid-slate
        cannot reset a cap to zero; per-era otherwise, which is exactly the
        previous behaviour.
        """

        if basis is not None:
            from app.models import ExperimentAccount
            from app.services.experiment_accounts import ExperimentAccountService

            accounts = ExperimentAccountService(self.session, self.settings)
            account = self.session.get(ExperimentAccount, basis["account_id"])
            if account is not None:
                return (
                    accounts.account_stake_used(account, slate_date=event_date),
                    accounts.account_stake_used(account, game_id=game.id),
                )
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
        return daily_used, same_game_used

    def _create_canonical_position(
        self,
        experiment: ShadowExperiment,
        game: Game,
        event_date: date,
        now: datetime,
        *,
        funding_pause: Any = None,
    ) -> bool:
        """Create exactly one canonical virtual position from cutoff-safe evidence.

        True only when a position was created. The disposition itself (and,
        while MLB_V18_FUNDING_PAUSE_V1 is on, the receipt) says why not.
        """

        disposition = self._canonical_position_disposition(
            experiment, game, event_date, now, funding_pause=funding_pause,
        )
        if funding_pause is not None:
            funding_pause.count(disposition)
        return disposition == POSITION_CREATED

    def _canonical_position_disposition(
        self,
        experiment: ShadowExperiment,
        game: Game,
        event_date: date,
        now: datetime,
        *,
        funding_pause: Any = None,
    ) -> str:
        """One of CREATED / INELIGIBLE / NOT_FUNDED, or a funding-pause disposition.

        ORDER IS THE CONTRACT. Every decision-integrity guard runs first; then
        the MLB V18 funding pause (flag-gated); only then any account, bankroll,
        stake or cap read. A paused decision never reaches staking.
        """

        if not self._is_mlb_game(game, "MLB experiment position"):
            return POSITION_INELIGIBLE
        cutoff = _aware(game.starts_at) - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
        # Only a decision made for THIS occurrence may fund it. A rescheduled
        # game keeps its games row, so without the slate filter the original
        # date's QUALIFIED decision (older than any later cutoff) would pass
        # every check below and fund the makeup at the original date's price.
        # A same-day time change keeps the same slate date.
        occurrence_slate = _aware(game.starts_at).astimezone(ZoneInfo(self.settings.display_timezone)).date()
        decision = self.session.scalar(
            select(ShadowExperimentDecision).where(
                ShadowExperimentDecision.experiment_id == experiment.id,
                ShadowExperimentDecision.game_id == game.id,
                ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
                ShadowExperimentDecision.recommended_at <= cutoff,
                ShadowExperimentDecision.slate_date == occurrence_slate,
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
            or (decision.forecast_id is None and decision.shadow_forecast_id is None)
            or decision.decimal_odds is None
            or decision.model_probability is None
        ):
            return POSITION_INELIGIBLE
        if self.settings.mlb_rescheduled_occurrence_positions_enabled:
            if self._prior_position_blocks(experiment, decision, occurrence_slate, game):
                return POSITION_INELIGIBLE
        elif self.session.scalar(select(OfficialAspPosition.id).where(
            OfficialAspPosition.shadow_experiment_id == experiment.id,
            OfficialAspPosition.market_outcome_id == decision.market_outcome_id,
        )):
            return POSITION_INELIGIBLE
        snapshot = self.session.get(OddsSnapshot, decision.odds_snapshot_id)
        forecast = self._decision_forecast(decision)
        if (
            snapshot is None
            or forecast is None
            or snapshot.market_outcome_id != decision.market_outcome_id
            or snapshot.decimal_odds is None
            or not same_quoted_price(snapshot, decision)
            or _aware(snapshot.observed_at) != _aware(decision.quote_observed_at)
            or _aware(forecast.available_as_of) > cutoff
            or _aware(forecast.generated_at) > cutoff
            or decision.source_mode != "LIVE"
        ):
            return POSITION_INELIGIBLE

        # Oct19 owner funding-only restrictions. All original occurrence,
        # quote/forecast integrity and duplicate guards above remain binding.
        # Retain the decision and append the existing immutable funding receipt.
        from app.services.owner_funding_restrictions import mlb_position_restriction
        owner_restriction = mlb_position_restriction(
            starts_at=game.starts_at, decision=decision,
            moment=now, settings=self.settings,
        )
        if owner_restriction is not None:
            self._record_funding(
                experiment, game, decision, now, disposition=POSITION_NOT_FUNDED,
                reason=owner_restriction["reason"], stake=0.0,
                limits={"owner_funding_restriction": owner_restriction}, shared=None,
            )
            return POSITION_NOT_FUNDED

        # MLB_V18_FUNDING_PAUSE_V1 -- the decision is now proven real,
        # cutoff-safe and not a duplicate, and nothing below this point has run:
        # no account read, no bankroll, no stake, no cap. An exact-authority V18
        # QUALIFIED_BET stops here with an immutable pause record; a V18
        # authority mismatch stops here with an operating alert; anything else
        # continues exactly as before. Off by default.
        if self.settings.mlb_v18_funding_pause_enabled:
            from app.services.mlb_v18_funding_pause import funding_pause_disposition

            paused = funding_pause_disposition(
                self.session, experiment=experiment, game=game, decision=decision,
                forecast=forecast, snapshot=snapshot, now=now, receipt=funding_pause,
            )
            if paused is not None:
                return paused

        # Capital comes from the season account when the era has one. The
        # account is what holds the money: it spans model eras, so the
        # predecessor's open stake stays reserved while it is open and returns
        # to the season -- not to a retired era -- when it settles. Sizing and
        # capping against the era alone is what would lose it.
        basis = self._account_staking_basis(experiment)
        if basis is None:
            bankroll = self.current_bankroll(experiment)
            available = self._available_bankroll(experiment)
        else:
            bankroll = basis["bankroll"]
            available = basis["available"]
        stake = self.compute_position_stake(experiment, decision, bankroll)
        if stake is None:
            self._record_funding(experiment, game, decision, now, disposition=POSITION_NOT_FUNDED,
                                 reason="NO_POSITIVE_STAKE", stake=0.0, limits=None, shared=None)
            return POSITION_NOT_FUNDED
        daily_used, same_game_used = self._stake_used(
            experiment, game=game, event_date=event_date, basis=basis,
        )
        daily_room = max(0.0, bankroll * experiment.max_daily_percentage - daily_used)
        # The owner's shared cross-sport allowance, once effective, replaces this
        # aggregate daily ceiling: one $100 per Eastern slate date across all
        # official sports, taken under the cross-sport lock that is held until
        # this transaction commits the position. MLB is a PRIORITY sport
        # (AISP_SHARED_100_PRIORITY_MLB_NHL_V1, owner 2026-10-16): its room
        # excludes its own hold and counts only the holds of other undecided
        # MLB/NHL games ordered before it.
        from app.services.shared_daily_allowance import eastern_date, room as shared_room

        shared = shared_room(self.session, self.settings, day=eastern_date(game.starts_at),
                             moment=now or datetime.now(UTC), sport="MLB",
                             game_id=game.id) if getattr(game, "starts_at", None) else None
        if shared is not None:
            daily_room = shared["room_usd"]
        limits = {
            "policy_stake": round(stake, 2),
            "available": round(available, 2),
            "same_game_cap": round(max(0.0, bankroll * experiment.max_same_game_percentage - same_game_used), 2),
            "daily_cap": round(daily_room, 2),
        }
        stake = min(
            stake,
            available,
            max(0.0, bankroll * experiment.max_same_game_percentage - same_game_used),
            daily_room,
        )
        stake = round(stake, 2)
        if stake <= 0:
            from app.services.shared_daily_allowance import zero_room_reason

            shared_alone = (shared is not None and limits["daily_cap"] <= 0
                            and all(v > 0 for k, v in limits.items() if k != "daily_cap"))
            self._record_funding(experiment, game, decision, now, disposition=POSITION_NOT_FUNDED,
                                 reason=zero_room_reason(shared) if shared_alone else "STAKING_OR_EXPOSURE_CAP",
                                 stake=0.0, limits=limits, shared=shared)
            return POSITION_NOT_FUNDED

        position = OfficialAspPosition(
            forecast_id=decision.forecast_id,
            shadow_forecast_id=decision.shadow_forecast_id,
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
        )
        # The INSERT runs in its own SAVEPOINT. The database's uniqueness
        # backstops (one position per decision; one non-VOID position per
        # experiment and outcome) must refuse ONE game, never roll back the
        # whole freeze transaction -- every other game's decisions and
        # positions in this run. Everything already pending is flushed first,
        # outside the savepoint, so only this position can be undone here.
        from sqlalchemy.exc import IntegrityError

        self.session.flush()
        try:
            with self.session.begin_nested():
                self.session.add(position)
                self.session.flush()
        except IntegrityError as error:
            import logging

            logging.getLogger(__name__).warning(
                "official position INSERT refused by a uniqueness constraint: experiment=%s game=%s "
                "decision=%s error=%s", experiment.id, game.id, decision.id, str(error.orig)[:200])
            unique = "unique" in str(error.orig).lower() or "duplicate key" in str(error.orig).lower()
            self._record_funding(experiment, game, decision, now, disposition=POSITION_INELIGIBLE,
                                 reason=POSITION_WRITE_CONFLICT if unique else POSITION_WRITE_REFUSED,
                                 stake=0.0, limits=limits, shared=shared)
            return POSITION_INELIGIBLE
        self._record_funding(experiment, game, decision, now, disposition=POSITION_CREATED,
                             reason="POSITION_TAKEN", stake=stake, limits=limits, shared=shared,
                             position_id=position.id)
        return POSITION_CREATED

    def _prior_position_blocks(
        self, experiment: ShadowExperiment, decision: ShadowExperimentDecision, occurrence_slate: date,
        game: Game,
    ) -> bool:
        """The occurrence-aware duplicate guard (mlb_rescheduled_occurrence_positions_enabled).

        A game has one moneyline market, so a rescheduled game's makeup reuses
        the very market outcome its original occurrence's position named. A
        prior position on that outcome blocks a new one UNLESS it is VOID
        (stake returned) and provably belongs to ANOTHER occurrence: a
        different decision, whose slate date and placement-time original date
        are both known and both differ from this occurrence's Eastern date.

        Everything else blocks exactly as before: an OPEN prior on any
        occurrence (the settlement owner, finalize-games, voids an unplayed one
        at the end of its original date + grace -- this guard never writes), a
        settled W/L/PUSH -- including one the owner chose to keep as action or
        later corrected beside its settlement -- and a VOID on THIS occurrence
        (e.g. a cancellation that flipped back to scheduled): re-funding that
        would also re-select the same decision.

        Read-only, so it is safe ahead of the funding pause. A non-blocking
        answer also needs the database to allow it: migration 20261019_0055
        replaces UNIQUE(shadow_experiment_id, market_outcome_id) with a partial
        unique index over non-VOID rows. Without that index the guard keeps
        blocking, so the flag on before the migration changes nothing.
        """

        from app.bankroll import occurrence as occ

        priors = list(self.session.scalars(select(OfficialAspPosition).where(
            OfficialAspPosition.shadow_experiment_id == experiment.id,
            OfficialAspPosition.market_outcome_id == decision.market_outcome_id,
        ).order_by(OfficialAspPosition.placed_at)))
        if not priors:
            return False
        for prior in priors:
            if prior.status != PositionStatus.VOID:
                return True
            if prior.shadow_decision_id is None or prior.shadow_decision_id == decision.id:
                return True
            prior_decision = self.session.get(ShadowExperimentDecision, prior.shadow_decision_id)
            if prior_decision is None or prior_decision.slate_date in (None, occurrence_slate):
                return True
            original = occ.original_occurrence(self.session, game=game, placed_at=prior.placed_at,
                                               decision=prior_decision)
            if original.et_date is None or original.et_date == occurrence_slate:
                return True
        return not live_outcome_index_present(self.session)

    def _record_funding(self, experiment: ShadowExperiment, game: Game, decision: ShadowExperimentDecision,
                        now: datetime | None, *, disposition: str, reason: str, stake: float,
                        limits: dict[str, Any] | None, shared: dict[str, Any] | None,
                        position_id: Any = None) -> None:
        """Append the funding receipt (``FUNDING_RECEIPT_ACTION``) once per distinct outcome.

        A tick that repeats the same disposition, reason and stake writes
        nothing; a later tick that funds a decision the shared allowance held
        back writes its own receipt beside the earlier one.
        """

        from app.models import AuditEvent

        def _plain(value: Any) -> Any:
            if isinstance(value, dict):
                return {str(k): _plain(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [_plain(v) for v in value]
            if isinstance(value, (UUID, date, datetime)):
                return str(value) if isinstance(value, UUID) else value.isoformat()
            return value

        for (prior,) in self.session.execute(select(AuditEvent.metadata_json).where(
                AuditEvent.action == FUNDING_RECEIPT_ACTION, AuditEvent.entity_id == decision.id)).all():
            prior = prior if isinstance(prior, dict) else {}
            if (prior.get("disposition"), prior.get("reason"), prior.get("stake")) == (
                    disposition, reason, round(float(stake), 2)):
                return
        moment = _aware(now or datetime.now(UTC))
        self.session.add(AuditEvent(
            action=FUNDING_RECEIPT_ACTION, entity_type="shadow_experiment_decision", entity_id=decision.id,
            occurred_at=moment,
            metadata_json=_plain({
                "disposition": disposition, "reason": reason, "stake": round(float(stake), 2),
                "position_id": position_id, "decision_id": decision.id, "experiment_id": experiment.id,
                "game_id": game.id, "evaluated_at": moment, "limits": limits,
                "shared_daily_allowance": shared,
            })))
        self.session.flush()

    def _decision_forecast(self, decision: ShadowExperimentDecision) -> Any | None:
        """The forecast a decision was actually made on, from either table."""

        if decision.shadow_forecast_id is not None:
            from app.models.entities import ShadowForecast
            from app.services.v18_forecast_view import V18ForecastView

            row = self.session.get(ShadowForecast, decision.shadow_forecast_id)
            return V18ForecastView(row) if row is not None else None
        if decision.forecast_id is not None:
            return self.session.get(Forecast, decision.forecast_id)
        return None

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
        if experiment.sport == "NHL":
            # NHL positions have ONE owner: app.nhl.paper.settle, which settles from the
            # source-backed NHL result (official final incl. OT/SO), voids on the producer's
            # CANCELLED/POSTPONED schedule status and records corrections by result hash.
            # The NHL result import also marks the game FINAL, so without this guard the
            # generic score rule would settle the same positions a second way.
            return {"settled": 0, "owner": "app.nhl.paper.settle"}
        now = now or datetime.now(UTC)
        positions = list(self.session.scalars(select(OfficialAspPosition).where(
            OfficialAspPosition.shadow_experiment_id == experiment.id,
            OfficialAspPosition.status == PositionStatus.OPEN,
        ).order_by(OfficialAspPosition.placed_at)))
        running = self.current_bankroll(experiment)
        settled = 0
        voided: list[dict[str, Any]] = []
        awaiting: list[dict[str, Any]] = []
        unverified: list[dict[str, Any]] = []
        # AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1 -- MLB only. A
        # rained-out game keeps its games row and moves to the makeup date; a
        # position placed on the original occurrence is VOID, never settled on
        # the makeup's final. NFL/CFB/NBA keep their own market rules for a
        # moved game (documented, unchanged here); NHL has its own owner.
        occurrence_rule = (experiment.sport or "MLB").upper() == "MLB"
        from app.bankroll import occurrence as occ

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
            verdict = None
            if occurrence_rule and not cancelled:
                verdict = occ.occurrence_verdict(
                    self.session, position=position, decision=decision, game=game, now=now,
                )
                receipt = {"position_id": str(position.id), "game_id": str(game.id), **verdict.as_dict()}
                if verdict.kind == occ.WAIT:
                    if verdict.postponement_related:
                        awaiting.append(receipt)
                    continue
                if verdict.kind == occ.VOID:
                    running = self._apply_settlement(
                        position, result=PositionResult.VOID, status=PositionStatus.VOID,
                        note=verdict.void_note(), home_score=None, away_score=None,
                        closing=None, running=running, now=now,
                    )
                    voided.append(receipt)
                    settled += 1
                    continue
            if not cancelled and (
                game.status != GameStatus.FINAL
                or game.home_score is None
                or game.away_score is None
            ):
                continue
            result = PositionResult.VOID if cancelled else determine_result(self.session, game, market, outcome)
            note = ("CANCELLED — virtual stake voided"
                    if cancelled else "Settled deterministically from canonical final score")
            if verdict is not None and verdict.unverified:
                # The rule could not check this position against the occurrence
                # it was placed on, so it settles exactly as it always did --
                # but it says so, in the note, the payload and (for the
                # incident class) one operating alert. Never silent.
                note = (f"{note}; {occ.RULE_ID} could not verify the original occurrence "
                        f"({verdict.reason})")
            running = self._apply_settlement(
                position, result=result,
                status=PositionStatus.VOID if cancelled else PositionStatus.SETTLED,
                note=note,
                home_score=game.home_score, away_score=game.away_score,
                closing=self._closing_snapshot(position, game), running=running, now=now,
            )
            settled += 1
            if verdict is not None and verdict.unverified:
                alerted = (occ.record_review_alert(self.session, position=position, game=game,
                                                   verdict=verdict, now=now)
                           if verdict.review_required else False)
                unverified.append({**receipt, "result": result.name, "operating_alert": alerted})
        self.session.commit()
        payload: dict[str, Any] = {"settled": settled, "current_bankroll": round(running, 2)}
        # Present only when the occurrence rule acted, is waiting or could not
        # verify, so an ordinary night's payload is exactly what it always was.
        if voided:
            payload["voided_not_played_on_original_date"] = voided
        if awaiting:
            payload["awaiting_occurrence"] = awaiting
        if unverified:
            payload["legacy_settled_unverified"] = unverified
        return payload

    def _apply_settlement(
        self,
        position: OfficialAspPosition,
        *,
        result: PositionResult,
        status: PositionStatus,
        note: str,
        home_score: int | None,
        away_score: int | None,
        closing: OddsSnapshot | None,
        running: float,
        now: datetime,
    ) -> float:
        """Write one position's settlement (the position update plus its one result row).

        Returns the bankroll after it. A VOID returns the stake: profit 0,
        payout = stake, bankroll_before == bankroll_after. The single writer for
        a W/L/PUSH, a cancellation and a postponement VOID.
        """

        profit = settle_profit(
            float(position.stake),
            decimal_odds=float(position.decimal_odds),
            result=SettlementResult(result.value),
        )
        before = running
        after = round(before + profit, 2)
        clv, movement, beat_close, closing_american, closing_decimal = self._closing_line_value(position, closing)
        position.result = result
        position.status = status
        position.settled_at = now
        position.payout = round(float(position.stake) + profit, 2)
        position.closing_decimal_odds = closing_decimal
        self.session.add(PositionSettlement(
            position_id=position.id,
            result=result,
            profit_loss=round(profit, 2),
            bankroll_before=round(before, 2), bankroll_after=after,
            game_home_score=home_score,
            game_away_score=away_score,
            settlement_note=note,
            closing_odds_snapshot_id=closing.id if closing else None,
            closing_american_odds=closing_american,
            closing_decimal_odds=closing_decimal,
            implied_probability_movement=movement,
            closing_line_value=clv, beat_close=beat_close, settled_at=now,
        ))
        return after

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
    @staticmethod
    def _settlement_order(
        events: list[tuple[Any, Any]], starting: float,
        adjustments: list[tuple[datetime, float]] | tuple[()] = (),
    ) -> list[tuple[Any, Any]]:
        """Chronological settlement order with the persisted bankroll chain as tie-breaker.

        settled_at is the primary key. When several settlements carry the same
        timestamp (one settlement pass over several finals), the settlement
        engine's own persisted bankroll_before/bankroll_after chain decides
        which was applied first; recommended_at is only the last resort.
        A correction recorded before a settlement pass is part of the bankroll
        that pass started from, so its delta joins the running chain.
        """
        remaining = sorted(events, key=lambda item: (_aware(item[0].settled_at), _aware(item[1].recommended_at)))
        pending = sorted(((_aware(at), float(delta)) for at, delta in adjustments), key=lambda item: item[0])
        ordered: list[tuple[Any, Any]] = []
        running = float(starting)
        while remaining:
            head_time = _aware(remaining[0][0].settled_at)
            while pending and pending[0][0] < head_time:
                running = round(running + pending.pop(0)[1], 2)
            group = [event for event in remaining if _aware(event[0].settled_at) == head_time]
            pick = next(
                (event for event in group
                 if event[0].bankroll_before is not None and abs(float(event[0].bankroll_before) - running) < 0.005),
                group[0],
            )
            remaining.remove(pick)
            ordered.append(pick)
            running = round(running + float(pick[0].profit_loss), 2)
        return ordered

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
        # Matchup labels for the public ledger (one bulk read, never per row).
        game_ids = {decision.game_id for _, decision in position_rows}
        matchup_parts: dict[Any, dict[bool, str]] = {}
        if game_ids:
            for game_id, is_home, abbreviation in self.session.execute(
                select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation)
                .join(Team, Team.id == GameTeam.team_id)
                .where(GameTeam.game_id.in_(game_ids))
            ):
                matchup_parts.setdefault(game_id, {})[bool(is_home)] = abbreviation
        matchups = {
            game_id: f"{parts.get(False, '?')} @ {parts.get(True, '?')}" for game_id, parts in matchup_parts.items()
        }
        # The engine behind each decision, resolved from the forecast the
        # decision actually bound. Both binding columns are read: Gen 1
        # decisions carry forecast_id, research-shadow eras carry
        # shadow_forecast_id, and a season that spans a model change has both.
        decision_ids = [decision.id for _, decision in position_rows]
        engine_by_decision: dict[Any, tuple[str | None, str | None]] = {}
        forecast_identity_by_decision: dict[Any, str | None] = {}
        if decision_ids:
            from app.models import ShadowForecast

            for decision_id, engine_id, engine_version, forecast_identity in self.session.execute(
                select(
                    ShadowExperimentDecision.id, ShadowForecast.engine_id,
                    ShadowForecast.engine_version, ShadowForecast.forecast_id,
                )
                .join(ShadowForecast,
                      ShadowForecast.id == ShadowExperimentDecision.shadow_forecast_id)
                .where(ShadowExperimentDecision.id.in_(decision_ids))
            ):
                engine_by_decision[decision_id] = (engine_id, engine_version)
                forecast_identity_by_decision[decision_id] = forecast_identity
            for decision_id, name, version, forecast_id in self.session.execute(
                select(
                    ShadowExperimentDecision.id, ModelVersion.name, ModelVersion.version,
                    Forecast.id,
                )
                .join(Forecast, Forecast.id == ShadowExperimentDecision.forecast_id)
                .join(ModelVersion, ModelVersion.id == Forecast.model_version_id)
                .where(ShadowExperimentDecision.id.in_(decision_ids))
            ):
                engine_by_decision.setdefault(decision_id, (name, version))
                forecast_identity_by_decision.setdefault(decision_id, str(forecast_id))
        starting = float(experiment.starting_bankroll)
        balance = starting
        peak = starting
        max_dd = 0.0
        streak = 0
        longest_streak = 0
        wins = losses = pushes = voids = 0
        total_risked = 0.0
        settled_stake = 0.0
        open_exposure = 0.0
        payouts = 0.0
        trough = starting
        clvs: list[float] = []
        beats = 0
        rows: list[dict[str, Any]] = []
        # The equity curve is the settlement sequence itself: it starts at the
        # opening bankroll and advances exactly once per settlement, in
        # settlement order. Each point carries the settlement that moved it so
        # the public curve and the ledger reconcile point-for-point.
        equity: list[dict[str, Any]] = [{
            "date": experiment.start_date.isoformat(), "bankroll": round(starting, 2), "benchmark": round(starting, 2),
            "sequence": 0, "position_id": None, "selection": None, "result": None, "profit_loss": 0.0,
            "high_water": round(starting, 2), "drawdown": 0.0, "settled_at": None,
        }]
        bankroll_after_by_position: dict[Any, float] = {}
        settlement_sequence: dict[Any, int] = {}
        monthly: dict[str, dict[str, Any]] = {}
        breakdowns: dict[str, dict[str, dict[str, float]]] = {"tier": {}, "market": {}, "month": {}, "engine": {}, "sportsbook": {}}
        # Corrections recorded beside a settlement (position_settlement_adjustments).
        # The original settlement stays in the curve where it happened; each
        # correction is its own ADJUSTMENT point at the moment it was recorded,
        # and per-position figures use the corrected result. Empty today, in
        # which case every figure below is exactly what it always was.
        from app.cfb.settlement_corrections import effective_settlements

        effective = effective_settlements(self.session, list(settlements))
        selection_by_position = {position.id: decision.selection for position, decision in position_rows}
        adjustment_events = sorted(
            ((_aware(at), delta, corrected, position_id)
             for position_id, item in effective.items() for at, delta, corrected in item.events),
            key=lambda event: event[0],
        )
        settlement_events = self._settlement_order(
            [
                (settlement, decision)
                for position, decision in position_rows
                if (settlement := settlements.get(position.id)) is not None
            ],
            starting,
            [(at, delta) for at, delta, _corrected, _pid in adjustment_events],
        )
        chain_reconciled = True
        sequence = 0
        display_zone = ZoneInfo(self.settings.display_timezone)

        def _adjustment_point(event: tuple[datetime, float, str, Any]) -> None:
            # A correction is not a settlement: it moves the bankroll but takes
            # no settlement number, so every later settlement keeps its own
            # "Settlement #N" (sequence None; readers fall back to the index).
            nonlocal balance, peak, max_dd, trough
            at, delta, corrected, position_id = event
            balance = round(balance + delta, 2)
            peak = max(peak, balance)
            max_dd = max(max_dd, peak - balance)
            trough = min(trough, balance)
            local = at.astimezone(display_zone)
            equity.append({
                "date": local.date().isoformat(), "bankroll": balance, "benchmark": round(starting, 2),
                "sequence": None, "position_id": str(position_id),
                "selection": selection_by_position.get(position_id), "result": corrected,
                "profit_loss": round(delta, 2),
                "high_water": round(peak, 2), "drawdown": round(peak - balance, 2),
                "settled_at": local.isoformat(), "kind": "ADJUSTMENT",
            })

        pending_adjustments = list(adjustment_events)
        for settlement, event_decision in settlement_events:
            while pending_adjustments and pending_adjustments[0][0] < _aware(settlement.settled_at):
                _adjustment_point(pending_adjustments.pop(0))
            sequence += 1
            balance = round(balance + float(settlement.profit_loss), 2)
            peak = max(peak, balance)
            max_dd = max(max_dd, peak - balance)
            settled_local = _aware(settlement.settled_at).astimezone(display_zone)
            settled_date = settled_local.date().isoformat()
            if settlement.bankroll_after is not None and abs(float(settlement.bankroll_after) - balance) > 0.005:
                chain_reconciled = False
            bankroll_after_by_position[settlement.position_id] = balance
            settlement_sequence[settlement.position_id] = sequence
            equity.append({
                "date": settled_date, "bankroll": balance, "benchmark": round(starting, 2),
                "sequence": sequence, "position_id": str(settlement.position_id),
                "selection": event_decision.selection, "result": settlement.result.value.upper(),
                "profit_loss": round(float(settlement.profit_loss), 2),
                "high_water": round(peak, 2), "drawdown": round(peak - balance, 2),
                "settled_at": settled_local.isoformat(),
            })
            trough = min(trough, balance)
        for event in pending_adjustments:
            _adjustment_point(event)
        for position, decision in position_rows:
            settlement = settlements.get(position.id)
            stake = float(position.stake)
            total_risked += stake
            # Settled stake is the denominator a realized return belongs over.
            # `total_risked` includes money still on an unsettled position, and
            # dividing a REALIZED profit by it understates the return while
            # looking like a precise number -- it is the difference between
            # 11.50% and 10.62% on this ledger.
            corrected = effective.get(position.id)
            if settlement is not None:
                settled_stake += stake
                payouts += float(position.payout or 0.0) + (corrected.delta if corrected else 0.0)
            else:
                open_exposure += stake
            result = (corrected.result if corrected else settlement.result.value.upper()) if settlement else "OPEN"
            profit = (corrected.profit_loss if corrected else float(settlement.profit_loss)) if settlement else 0.0
            clv = (corrected.closing_line_value if corrected else settlement.closing_line_value) if settlement else None
            beat_close = (None if corrected is not None and corrected.result == "VOID"
                          else settlement.beat_close) if settlement else None
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
                if clv is not None:
                    clvs.append(float(clv))
                if beat_close:
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
                "closing_price": (None if corrected is not None and corrected.result == "VOID"
                                  else settlement.closing_american_odds) if settlement else None,
                "clv": clv,
                # Additive public-ledger evidence (all persisted at decision or
                # settlement time; nothing is derived from later data).
                "matchup": matchups.get(decision.game_id),
                "sportsbook": decision.sportsbook,
                "bankroll_after": bankroll_after_by_position.get(position.id),
                "settlement_sequence": settlement_sequence.get(position.id),
                "settled_at": (
                    _aware(settlement.settled_at).astimezone(ZoneInfo(self.settings.display_timezone)).isoformat()
                    if settlement else None
                ),
                "beat_close": beat_close,
                "model_probability": decision.model_probability,
                "market_probability": decision.no_vig_probability,
                "raw_edge": decision.edge,
                "adjusted_edge": (decision.evidence or {}).get("adjusted_edge") if isinstance(decision.evidence, dict) else None,
                "decision_id": str(decision.id),
                # Which model produced THIS position, from the forecast the
                # decision bound at the time. A page-level model name cannot
                # answer this once a season spans two engines, and a reader
                # comparing one row against another needs it per row.
                # The two edges the policy records are DIFFERENT quantities and
                # are carried separately: the minimum is applied to the no-vig
                # edge, while the stake is paid against the offered price. A
                # single "edge" field invited the two to be read as one.
                "raw_model_probability": (decision.evidence or {}).get("raw_model_probability")
                if isinstance(decision.evidence, dict) else None,
                "adjusted_probability": (decision.evidence or {}).get("adjusted_probability")
                if isinstance(decision.evidence, dict) else None,
                "offered_decimal_odds": float(position.decimal_odds)
                if position.decimal_odds else None,
                "offered_break_even_probability": (1.0 / float(position.decimal_odds))
                if position.decimal_odds else None,
                "adjusted_edge_vs_no_vig": (decision.evidence or {}).get("adjusted_edge")
                if isinstance(decision.evidence, dict) else None,
                "model_engine_id": engine_by_decision.get(decision.id, (None, None))[0],
                "model_engine_version": engine_by_decision.get(decision.id, (None, None))[1],
                "forecast_identity": forecast_identity_by_decision.get(decision.id),
                "decision_phase": decision.phase.value if hasattr(decision.phase, "value") else str(decision.phase),
                "recommended_at": _aware(decision.recommended_at).astimezone(ZoneInfo(self.settings.display_timezone)).isoformat(),
                # Only on a corrected position: the original settlement it
                # replaced, so a reader can show "LOSS -> VOID" honestly.
                **(corrected.as_dict() if corrected is not None else {}),
            })
        settled_count = len(settlements)
        net = balance - starting
        # Retained under its old name so no existing caller changes behaviour.
        # It is NOT displayed as "ROI" anywhere any more: see the two measures
        # below, which say what they are divided by.
        roi = (net / total_risked * 100) if total_risked else None
        #: Profit as a share of the money the experiment started with. This is
        #: what a reader means by "how is the bankroll doing" -- $1,000 to
        #: $1,003.45 is +0.35%, and nothing should ever present that as +11%.
        bankroll_return = (net / starting * 100) if starting else None
        #: Realized profit as a share of the stake that has actually resolved.
        #: A much larger number than the bankroll return because the stakes are
        #: tiny; useful, and meaningless unless the denominator is stated.
        return_on_settled_stake = (net / settled_stake * 100) if settled_stake else None
        #: Result of the most recent settlement, and how many in a row.
        streak_result: str | None = None
        streak_length = 0
        for point in reversed(equity[1:]):
            if point.get("kind") == "ADJUSTMENT":
                continue
            result = point.get("result")
            corrected = effective.get(UUID(point["position_id"])) if point.get("position_id") else None
            if corrected is not None:
                result = corrected.result
            if result in (None, "PUSH", "VOID"):
                continue
            if streak_result is None:
                streak_result = result
                streak_length = 1
            elif result == streak_result:
                streak_length += 1
            else:
                break
        #: How much of the realized profit rests on the single best result. At
        #: a small sample this is usually the most important number on the page
        #: and it is never flattering, which is exactly why it is computed.
        settled_profits = [
            effective[position_id].profit_loss if position_id in effective else float(settlement.profit_loss)
            for position_id, settlement in settlements.items()
        ]
        best_result = max(settled_profits) if settled_profits else None
        worst_result = min(settled_profits) if settled_profits else None
        wins_only = sorted((value for value in settled_profits if value > 0), reverse=True)
        concentration = (
            round(sum(wins_only[:2]) / net * 100, 1)
            if net > 0 and wins_only else None
        )
        slate_dates = set(self.session.scalars(select(ShadowExperimentDecision.slate_date).where(ShadowExperimentDecision.experiment_id == experiment.id)))
        primary_dates = {decision.slate_date for _, decision in position_rows}
        slate_dates.add(experiment.start_date)
        no_bet_days = len(slate_dates - primary_dates)
        betting = {
            "starting_bankroll": round(starting, 2), "current_bankroll": round(balance, 2),
            "net_profit_loss": round(net, 2), "total_risked": round(total_risked, 2),
            "settled_stake": round(settled_stake, 2),
            "open_exposure": round(open_exposure, 2),
            "payouts": round(payouts, 2),
            "roi": round(roi, 2) if roi is not None else None,
            "yield_pct": round(roi, 2) if roi is not None else None,
            "bankroll_return_pct": round(bankroll_return, 3) if bankroll_return is not None else None,
            "return_on_settled_stake_pct": (
                round(return_on_settled_stake, 2) if return_on_settled_stake is not None else None
            ),
            "peak_bankroll": round(peak, 2),
            "lowest_bankroll": round(trough, 2),
            "max_drawdown_pct_of_peak": round(max_dd / peak * 100, 3) if peak else 0.0,
            "streak_result": streak_result,
            "streak_length": streak_length,
            "best_settled_profit": round(best_result, 2) if best_result is not None else None,
            "worst_settled_profit": round(worst_result, 2) if worst_result is not None else None,
            "top_two_wins_share_of_profit_pct": concentration,
            "positions": len(position_rows), "settled": settled_count, "open": len(position_rows) - settled_count,
            "wins": wins, "losses": losses, "pushes": pushes, "voids": voids,
            "record": f"{wins}–{losses}–{pushes}",
            "max_drawdown": round(max_dd, 2), "longest_losing_streak": longest_streak,
            "closing_line_value": round(sum(clvs) / len(clvs), 3) if clvs else None,
            "beat_close_percentage": round(beats / len(clvs) * 100, 1) if clvs else None,
            "has_settled": settled_count > 0,
            # True when the running balance reproduces every persisted
            # bankroll_after in settlement order (the public curve and the
            # settlement engine's own chain agree point-for-point).
            "bankroll_chain_reconciled": chain_reconciled,
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
            "settled_stake": round(settled_stake, 2),
            "open_exposure": round(open_exposure, 2),
            "bankroll_return_pct": round(bankroll_return, 3) if bankroll_return is not None else None,
            "return_on_settled_stake_pct": (
                round(return_on_settled_stake, 2) if return_on_settled_stake is not None else None
            ),
            "lowest_bankroll": round(trough, 2),
            "no_bet_days": no_bet_days,
            "daily": self._daily_rows(experiment, position_rows, settlements, effective),
            "current_drawdown": round((peak - balance) / peak * 100, 3) if peak else 0.0,
            "closing_quote_coverage": {
                "positions_with_closing_quote": len(clvs),
                "settled_positions": settled_count,
                "percentage": round(len(clvs) / settled_count * 100, 1) if settled_count else None,
            },
        }

    def _daily_rows(self, experiment, position_rows, settlements, effective=None) -> list[dict[str, Any]]:
        # This table needs three columns off the decision row, and the row also
        # carries a ~2 KB `evidence` JSONB that none of them touch. Loading whole
        # ORM decisions meant decoding tens of megabytes of JSON on every read to
        # print a few sentences of explanation -- the single reason /bankroll
        # took ~20 seconds. Ask for the columns, not the objects.
        slate_dates = set(self.session.scalars(
            select(ShadowExperimentDecision.slate_date)
            .where(ShadowExperimentDecision.experiment_id == experiment.id)
            .distinct()
        ))
        dates = {experiment.start_date, *slate_dates}
        # Reasons are only ever rendered for a day that placed nothing, so only
        # those days' reasons are worth reading.
        quiet_dates = dates - {decision.slate_date for _, decision in position_rows}
        reasons_by_date: dict[date, list[str]] = {}
        if quiet_dates:
            for slate_date, qualification_reasons, recommendation_reason in self.session.execute(
                select(
                    ShadowExperimentDecision.slate_date,
                    ShadowExperimentDecision.qualification_reasons,
                    ShadowExperimentDecision.recommendation_reason,
                )
                .where(
                    ShadowExperimentDecision.experiment_id == experiment.id,
                    ShadowExperimentDecision.slate_date.in_(quiet_dates),
                )
                # `recommended_at` is not unique -- a slate can stamp fifteen
                # decisions on the same instant -- and the list is truncated to
                # eight, so without a total order which reasons an operator sees
                # depends on the query plan. It was stable only by luck. Break
                # the tie on the primary key so it is stable by construction.
                .order_by(
                    ShadowExperimentDecision.slate_date,
                    ShadowExperimentDecision.recommended_at,
                    ShadowExperimentDecision.id,
                )
            ):
                bucket = reasons_by_date.setdefault(slate_date, [])
                bucket.extend(str(value) for value in (qualification_reasons or []))
                if recommendation_reason:
                    bucket.append(recommendation_reason)
        balance = float(experiment.starting_bankroll)
        rows: list[dict[str, Any]] = []
        for slate_date in sorted(dates):
            day_positions = [(p, d) for p, d in position_rows if d.slate_date == slate_date]
            day_settlements = [settlements[p.id] for p, _ in day_positions if p.id in settlements]
            # A corrected position counts at its corrected P&L (original + delta).
            profit = sum(
                effective[item.position_id].profit_loss if effective and item.position_id in effective
                else float(item.profit_loss)
                for item in day_settlements
            )
            balance += profit
            reasons: list[str] = []
            if not day_positions:
                reasons = list(reasons_by_date.get(slate_date, ()))
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
