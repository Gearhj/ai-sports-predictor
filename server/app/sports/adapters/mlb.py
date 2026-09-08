"""MLB read adapter.

Projects the EXISTING governed MLB board (ReadService.dashboard) and the
immutable experiment ledger into the shared multi-sport contract. It owns no
modelling, qualification, or staking logic: every state it emits is derived
from an authority that already decided it.

Baseball-specific vocabulary (starter, lineup, run line, first five) stays on
this side of the boundary — the shared shell never sees it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.sports.contracts import (
    NormalizedDailyDecisionState as DState,
    NormalizedFundingState as FState,
    NormalizedSelection,
    SportSection,
)
from app.sports.registry import MLB

# The MLB funding experiments support the full-game moneyline only.
FUNDED_MARKET_PATTERNS = ("moneyline", "money line", "h2h")
MARKET_NOT_SUPPORTED_NOTE = "Market not supported by this experiment (moneyline only)"


def _market_evidence_state(view: Any, market_supported: bool) -> str:
    """What sportsbook evidence stands behind this selection.

    Read straight off the priced view the board already built: a missing quote
    and a stale quote are different operational facts and must not both render
    as "waiting".
    """

    if not market_supported:
        return "MARKET_NOT_SUPPORTED"
    if view is None or getattr(view, "quote_observed_at", None) is None:
        return "QUOTE_MISSING"
    if bool(getattr(view, "is_stale", False)):
        return "QUOTE_STALE"
    return "QUOTE_PRESENT"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _text(value: Any) -> str | None:
    """Normalise a persisted enum/string to an upper-case token, or None.

    StrEnum members stringify to their value, so this keeps the wire contract
    stable whether the adapter is handed an enum or an already-serialised
    string. An empty value stays None rather than becoming ``""``.
    """

    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    return text.upper() or None


def _probability(value: Any) -> float | None:
    """Percentage 0-100, or None. Never coerces a missing value to zero."""

    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _is_funded_market(market_type: str | None) -> bool:
    lowered = (market_type or "").lower()
    return any(token in lowered for token in FUNDED_MARKET_PATTERNS)


@dataclass(frozen=True)
class _FinalAuthority:
    """Exact immutable authority for one final-pregame projection."""

    decision: Any
    experiment: Any
    position: Any | None


class MlbTodayAdapter:
    """Builds the MLB SportSection for TODAY_ALL_SPORTS_READ_MODEL_V1."""

    definition = MLB

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- section -----------------------------------------------------------
    def build_section(self, event_date: date, *, now: datetime | None = None) -> SportSection:
        from app.services.read_service import ReadService

        now = _aware(now) or datetime.now(UTC)
        read = ReadService(self.session, self.settings)
        dashboard = read.dashboard(event_date)
        # ReadService.dashboard is a shared live-slate read. Pair cards only
        # through its authoritative per-opportunity league identity; an
        # unidentifiable or non-MLB card fails closed instead of defaulting MLB.
        opportunities = {
            item.game_id: item
            for item in (dashboard.opportunities or [])
            if str(getattr(item, "sport", "")).upper() == "MLB"
        }
        games = {
            str(game.id): game
            for game in (dashboard.games or [])
            if str(game.id) in opportunities
        }
        latest_forecast = self._latest_forecast_at(list(games))

        selections: list[NormalizedSelection] = []
        for game_id, game in games.items():
            selections.extend(self._selections(game_id, game, opportunities.get(game_id), now))

        counts = {state: 0 for state in DState}
        for item in selections:
            counts[item.decision_state] = counts.get(item.decision_state, 0) + 1
        funded = [s for s in selections if s.funding_state == FState.OFFICIAL_VIRTUAL_POSITION]
        open_count, settled_count = self._position_counts(event_date)
        readiness = [
            float(game.data_readiness)
            for game in games.values()
            if getattr(game, "data_readiness", None) is not None
        ]

        summary = dashboard.opportunity_summary
        return SportSection(
            sport_code=MLB.sport_code,
            league_code=MLB.league_code,
            display_name=MLB.display_name,
            availability_state=MLB.availability,
            scheduled_event_count=len(games),
            valid_forecast_count=sum(
                bool(getattr(game, "forecast_available", False))
                for game in games.values()
            ),
            official_position_count=len(funded),
            final_qualified_count=counts.get(DState.FINAL_QUALIFIED, 0),
            awaiting_final_evidence_count=counts.get(DState.AWAITING_FINAL_EVIDENCE, 0),
            provisional_count=counts.get(DState.PROVISIONAL_EDGE, 0),
            watch_count=counts.get(DState.WATCH, 0),
            pass_count=counts.get(DState.PASS, 0) + counts.get(DState.FINAL_NO_BET, 0),
            open_position_count=open_count,
            settled_today_count=settled_count,
            virtual_stake=round(sum(s.virtual_stake or 0.0 for s in funded), 2),
            data_health=(sum(readiness) / len(readiness)) if readiness else None,
            latest_forecast_at=latest_forecast,
            latest_market_at=self._latest_market_at(list(opportunities.values())),
            engine_label=MLB.engine_label,
            can_create_official_positions=True,
            official_positions_blocked_reason=None,
            degraded=bool(summary and not getattr(summary, "odds_scheduler_running", False)),
            degraded_reason=(
                "Automatic odds refresh is not running; quotes may be stale."
                if summary and not getattr(summary, "odds_scheduler_running", False) else None
            ),
            selections=selections,
        )

    # -- one event ---------------------------------------------------------
    def _selection(self, game_id: str, game: Any, opportunity: Any, now: datetime) -> NormalizedSelection:
        """Backward-compatible single-selection view for direct callers.

        The section builder uses :meth:`_selections` so simultaneous governed
        experiments remain distinct.  A direct caller gets its current policy
        when present, otherwise the first deterministic authority/board row.
        """

        selections = self._selections(game_id, game, opportunity, now)
        preferred_policy = self._preferred_policy(opportunity)
        return next(
            (
                item
                for item in selections
                if preferred_policy is not None and item.policy_version == preferred_policy
            ),
            selections[0],
        )

    def _selections(
        self,
        game_id: str,
        game: Any,
        opportunity: Any,
        now: datetime,
    ) -> list[NormalizedSelection]:
        # ForecastGame's public contract names this field ``start_time``.  Keep
        # that exact value (including an existing offset); a genuinely missing
        # source timestamp remains missing rather than being reconstructed.
        starts_at = _aware(getattr(game, "start_time", None))
        from app.services.shadow_experiment_service import FINAL_PREGAME_CUTOFF_MINUTES

        cutoff = starts_at - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES) if starts_at else None
        now = _aware(now) or datetime.now(UTC)
        label = self._event_label(game)
        base = dict(
            sport_code=MLB.sport_code,
            league_code=MLB.league_code,
            event_id=game_id,
            event_label=label,
            starts_at=starts_at,
            final_decision_cutoff=cutoff,
            source_label=MLB.engine_label,
            model_status="DEVELOPMENT",
            can_ever_fund_official_position=True,
            detail_href=f"/games/{game_id}",
            # Already-loaded lifecycle evidence from the same dashboard read.
            # Projected, never derived: a consumer must not have to infer that
            # an event finished by comparing its start time to a clock.
            event_status=_text(getattr(game, "status", None)),
            lifecycle_stage=_text(getattr(game, "lifecycle_stage", None)),
            next_scheduled_refresh_at=_aware(
                getattr(game, "next_scheduled_refresh_at", None)
            ),
            # The current published forecast, carried independently of any
            # sportsbook quote. Without this a quote-less game shows no
            # probability at all even though one is persisted and published.
            current_forecast_probability=_probability(
                getattr(game, "home_win_probability", None)
            ),
            current_forecast_generated_at=_aware(
                getattr(game, "forecast_generated_at", None)
            ),
            current_forecast_model=_text(
                getattr(game, "forecast_model_label", None)
            ),
        )

        # Even an already-persisted row is not presented as the event's final
        # public state before the governed cutoff arrives.
        active_experiments = (
            self._active_experiments(starts_at)
            if cutoff is not None and now >= cutoff
            else []
        )
        authorities = self._final_authorities(
            game_id,
            cutoff,
            active_experiments=active_experiments,
        ) if active_experiments else []
        if authorities:
            selections = [
                self._authority_selection(authority, base)
                for authority in authorities
            ]
            authority_experiment_ids = {
                authority.experiment.id for authority in authorities
            }
            selections.extend(
                self._missing_final_authority_selection(experiment, base)
                for experiment in active_experiments
                if experiment.id not in authority_experiment_ids
            )
            return selections

        if opportunity is None:
            return [NormalizedSelection(
                decision_state=DState.NOT_EVALUATED,
                funding_state=FState.NOT_EVALUATED,
                status_note="No governed evaluation exists for this event yet.",
                **base,
            )]

        # A board row can exist even when there is no forecast (for example a
        # quote-less NO_BET placeholder).  It is not a completed evaluation.
        if getattr(game, "forecast_available", None) is False:
            return [NormalizedSelection(
                decision_state=DState.NOT_EVALUATED,
                funding_state=FState.NOT_EVALUATED,
                status_note="No governed forecast exists for this event yet.",
                **base,
            )]

        v2 = getattr(opportunity, "v2", None)
        best = getattr(opportunity, "best", None)
        alternates = list(getattr(opportunity, "alternates", []) or [])
        moneyline = next(
            (item for item in ([best] if best else []) + alternates if _is_funded_market(getattr(item, "market_type", None))),
            None,
        )
        before_cutoff = cutoff is None or now < cutoff

        # A V2.1 display evaluation is indicative.  Even if its computed view
        # says FINAL_*, only a persisted FINAL_PREGAME row may make that state
        # final here.  The authority lookup above already handled such a row.
        if v2 is not None and moneyline is not None and getattr(v2, "active", False):
            state = (
                DState.AWAITING_FINAL_EVIDENCE
                if before_cutoff
                else DState.NOT_EVALUATED
            )
            funding = (
                FState.ELIGIBLE_NOT_FUNDED
                if before_cutoff
                else FState.NOT_EVALUATED
            )
            note = (
                "Awaiting the persisted final-pregame decision."
                if before_cutoff
                else (
                    "No cutoff-safe persisted final-pregame decision exists; "
                    "the mutable V2.1 board is not final authority."
                )
            )
            return [NormalizedSelection(
                decision_state=state,
                funding_state=funding,
                market_evidence_state=_market_evidence_state(moneyline, True),
                evidence_tier=getattr(v2, "tier", None),
                adjusted_edge=getattr(v2, "adjusted_edge", None),
                expected_value=getattr(v2, "adjusted_expected_value", None),
                outstanding_gates=list(getattr(v2, "reasons", []) or []),
                status_note=note,
                **self._pricing(moneyline),
                **base,
            )]

        # No persisted final authority: this is a board-only opportunity.  It
        # may be provisional or watched, but it can never become final merely
        # because the mutable board currently says QUALIFIED_BET or NO_BET.
        board_state = getattr(opportunity, "recommendation_state", "NO_BET")
        selection_view = best
        market_supported = (
            selection_view is None
            or _is_funded_market(getattr(selection_view, "market_type", None))
        )
        funding = (
            FState.MARKET_NOT_SUPPORTED
            if not market_supported
            else FState.ELIGIBLE_NOT_FUNDED
        )
        if not before_cutoff:
            # Once the cutoff has passed there is nothing left to await, but a
            # mutable board label still cannot manufacture PASS/final authority.
            state = DState.NOT_EVALUATED
            if market_supported:
                funding = FState.NOT_EVALUATED
        elif board_state in ("QUALIFIED_BET", "NO_BET"):
            state = DState.AWAITING_FINAL_EVIDENCE
        elif board_state in ("PROVISIONAL_EDGE", "EXTREME_DISAGREEMENT"):
            state = DState.PROVISIONAL_EDGE
        elif board_state == "WATCH":
            state = DState.WATCH
        else:
            state = DState.AWAITING_FINAL_EVIDENCE
        return [NormalizedSelection(
            decision_state=state,
            funding_state=funding,
            market_evidence_state=_market_evidence_state(selection_view, market_supported),
            outstanding_gates=list(getattr(opportunity, "qualification_reasons", []) or []),
            status_note=(
                MARKET_NOT_SUPPORTED_NOTE
                if not market_supported
                else (
                    (
                        "No cutoff-safe persisted final-pregame decision exists; "
                        "the mutable board is not final authority."
                    )
                    if not before_cutoff
                    else getattr(opportunity, "recommendation_reason", None)
                )
            ),
            **self._pricing(selection_view),
            **{
                **base,
                "can_ever_fund_official_position": market_supported,
            },
        )]

    @staticmethod
    def _preferred_policy(opportunity: Any) -> str | None:
        """Policy whose current candidate should be paired with final evidence."""

        from app.services.shadow_experiment_service import DECISION_POLICY

        v2 = getattr(opportunity, "v2", None) if opportunity is not None else None
        if v2 is not None and getattr(v2, "active", False):
            return getattr(v2, "policy_version", None)
        return DECISION_POLICY

    def _active_experiments(self, starts_at: datetime) -> list[Any]:
        """Latest effective active MLB experiment for each governed policy."""

        from app.services.shadow_experiment_service import ShadowExperimentService

        operational_date = starts_at.astimezone(
            ZoneInfo(self.settings.display_timezone)
        ).date()
        return [
            experiment
            for experiment in ShadowExperimentService(
                self.session, self.settings,
            ).active_experiments()
            if experiment.sport == "MLB" and experiment.start_date <= operational_date
        ]

    def _final_authorities(
        self,
        game_id: str,
        cutoff: datetime | None,
        *,
        active_experiments: list[Any],
    ) -> list[_FinalAuthority]:
        """Return one cutoff-safe persisted final authority per experiment.

        Board recommendation fields and experiment-status badges are
        deliberately absent from this query.  A final state comes only from an
        immutable FINAL_PREGAME decision; official funding additionally
        requires the canonical position row linked to that exact decision.
        Active V1 and V2.1 experiments are independent bankroll instruments,
        so neither is collapsed into the other.
        """

        if cutoff is None:
            return []
        try:
            governed_game_id = UUID(str(game_id))
        except (TypeError, ValueError):
            return []

        from app.models import (
            ExperimentDecisionPhase,
            OfficialAspPosition,
            ShadowExperiment,
            ShadowExperimentDecision,
        )
        from app.services.shadow_experiment_service import SUPERSEDED_POLICIES

        active_experiment_ids = [experiment.id for experiment in active_experiments]
        if not active_experiment_ids:
            return []

        rows = list(self.session.execute(
            select(ShadowExperimentDecision, ShadowExperiment, OfficialAspPosition)
            .join(
                ShadowExperiment,
                ShadowExperiment.id == ShadowExperimentDecision.experiment_id,
            )
            .outerjoin(
                OfficialAspPosition,
                OfficialAspPosition.shadow_decision_id == ShadowExperimentDecision.id,
            )
            .where(
                ShadowExperimentDecision.game_id == governed_game_id,
                ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
                ShadowExperimentDecision.source_mode == "LIVE",
                ShadowExperiment.id.in_(active_experiment_ids),
                ShadowExperiment.sport == "MLB",
                ShadowExperiment.is_active.is_(True),
            )
        ))
        candidates = [
            _FinalAuthority(decision, experiment, position)
            for decision, experiment, position in rows
            if experiment.decision_policy not in SUPERSEDED_POLICIES
            and _aware(decision.recommended_at) is not None
            and _aware(decision.recommended_at) <= cutoff
        ]
        if not candidates:
            return []

        by_experiment: dict[UUID, _FinalAuthority] = {}
        for item in candidates:
            current = by_experiment.get(item.experiment.id)
            item_key = (
                item.position is not None,
                _aware(item.decision.recommended_at) or datetime.min.replace(tzinfo=UTC),
            )
            current_key = (
                current.position is not None,
                _aware(current.decision.recommended_at)
                or datetime.min.replace(tzinfo=UTC),
            ) if current is not None else None
            if current_key is None or item_key > current_key:
                by_experiment[item.experiment.id] = item

        return sorted(
            by_experiment.values(),
            key=lambda item: (
                int(item.experiment.version),
                str(item.experiment.id),
            ),
        )

    def _authority_selection(
        self,
        authority: _FinalAuthority,
        base: dict[str, Any],
    ) -> NormalizedSelection:
        decision = authority.decision
        experiment = authority.experiment
        position = authority.position
        recommendation = str(decision.recommendation or "NO_BET").upper()
        market_supported = _is_funded_market(decision.market)
        if not market_supported:
            # The experiment contract funds the full-game moneyline only.  A
            # malformed persisted decision/position for another market remains
            # observable evidence but can never enter a qualified/official lane.
            state = DState.NOT_EVALUATED
            funding = FState.MARKET_NOT_SUPPORTED
        elif position is not None:
            state = DState.FINAL_QUALIFIED
            funding = FState.OFFICIAL_VIRTUAL_POSITION
        elif recommendation == "QUALIFIED_BET":
            state = DState.FINAL_QUALIFIED
            funding = FState.ELIGIBLE_NOT_FUNDED
        elif recommendation == "NO_BET":
            state = DState.FINAL_NO_BET
            funding = FState.ELIGIBLE_NOT_FUNDED
        else:
            state = DState.PASS
            funding = FState.ELIGIBLE_NOT_FUNDED

        evidence = decision.evidence or {}

        def percentage(value: Any) -> float | None:
            return round(float(value) * 100, 6) if value is not None else None

        return NormalizedSelection(
            decision_state=state,
            funding_state=funding,
            market_label=decision.market,
            selection_label=decision.selection,
            line=decision.line,
            price_american=decision.american_odds,
            sportsbook=decision.sportsbook,
            model_probability=percentage(decision.model_probability),
            no_vig_probability=percentage(decision.no_vig_probability),
            adjusted_edge=percentage(evidence.get("adjusted_edge")),
            expected_value=percentage(
                evidence.get("adjusted_expected_value", decision.expected_value)
            ),
            evidence_tier=evidence.get("tier"),
            virtual_stake=(
                float(position.stake)
                if market_supported and position is not None
                else None
            ),
            experiment_id=str(experiment.id),
            experiment_name=experiment.name,
            policy_version=experiment.decision_policy,
            decision_timestamp=_aware(decision.recommended_at),
            official_position_id=(
                str(position.id)
                if market_supported and position is not None
                else None
            ),
            # Ledger lifecycle for the backing position, when one exists. This
            # is what separates an open virtual position from a settled one;
            # it is never inferred from the event clock.
            position_status=(
                _text(getattr(position, "status", None))
                if market_supported and position is not None
                else None
            ),
            position_result=(
                _text(getattr(position, "result", None))
                if market_supported and position is not None
                else None
            ),
            outstanding_gates=list(decision.qualification_reasons or []),
            status_note=(
                decision.recommendation_reason
                if market_supported
                else MARKET_NOT_SUPPORTED_NOTE
            ),
            **{
                **base,
                "can_ever_fund_official_position": market_supported,
            },
        )

    @staticmethod
    def _missing_final_authority_selection(
        experiment: Any,
        base: dict[str, Any],
    ) -> NormalizedSelection:
        """Fail-closed identity for an active policy that missed final freeze."""

        return NormalizedSelection(
            decision_state=DState.NOT_EVALUATED,
            funding_state=FState.NOT_EVALUATED,
            market_label="MONEYLINE",
            experiment_id=str(experiment.id),
            experiment_name=experiment.name,
            policy_version=experiment.decision_policy,
            status_note=(
                "No cutoff-safe persisted FINAL_PREGAME decision exists for this "
                "active experiment; mutable board evidence was not substituted."
            ),
            **base,
        )

    @staticmethod
    def _pricing(view: Any) -> dict[str, Any]:
        if view is None:
            return {}
        return {
            "market_label": getattr(view, "market_type", None),
            "selection_label": getattr(view, "selection", None),
            "line": getattr(view, "line", None),
            "price_american": getattr(view, "american_odds", None),
            "sportsbook": getattr(view, "sportsbook", None),
            "model_probability": getattr(view, "model_probability", None),
            "no_vig_probability": getattr(view, "no_vig_probability", None),
        }

    @staticmethod
    def _latest_market_at(opportunities: list[Any]) -> datetime | None:
        timestamps = [
            _aware(getattr(selection, "quote_observed_at", None))
            for opportunity in opportunities
            for selection in (
                [getattr(opportunity, "best", None)]
                + list(getattr(opportunity, "alternates", []) or [])
            )
            if selection is not None
            and getattr(selection, "quote_observed_at", None) is not None
        ]
        return max(timestamps, default=None)

    def _position_counts(self, event_date: date) -> tuple[int, int]:
        """Open positions and positions settled on this operational date,
        across active governed MLB experiments (never pooled with another sport)."""
        from app.models import (
            OfficialAspPosition,
            PositionSettlement,
            PositionStatus,
            ShadowExperimentDecision,
        )
        from app.services.shadow_experiment_service import ShadowExperimentService

        experiment_ids = [
            experiment.id
            for experiment in ShadowExperimentService(
                self.session, self.settings,
            ).active_experiments()
            if experiment.sport == "MLB" and experiment.start_date <= event_date
        ]
        if not experiment_ids:
            return 0, 0
        open_count = len(list(self.session.scalars(
            select(OfficialAspPosition.id).where(
                OfficialAspPosition.shadow_experiment_id.in_(experiment_ids),
                OfficialAspPosition.status == PositionStatus.OPEN,
            )
        )))
        settled = list(self.session.scalars(
            select(PositionSettlement.id)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .join(ShadowExperimentDecision, ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
            .where(
                OfficialAspPosition.shadow_experiment_id.in_(experiment_ids),
                ShadowExperimentDecision.slate_date == event_date,
            )
        ))
        return open_count, len(settled)

    def _latest_forecast_at(self, game_ids: list[str]) -> datetime | None:
        """Exact forecast freshness, independent from sportsbook freshness."""
        if not game_ids:
            return None
        from app.models import Forecast

        governed_ids: list[UUID] = []
        for value in game_ids:
            try:
                governed_ids.append(UUID(value))
            except (TypeError, ValueError):
                continue
        if not governed_ids:
            return None

        return _aware(self.session.scalar(
            select(func.max(Forecast.generated_at)).where(
                Forecast.game_id.in_(governed_ids),
                Forecast.source_mode == "LIVE",
            )
        ))

    @staticmethod
    def _event_label(game: Any) -> str:
        away = getattr(game, "away_team", None)
        home = getattr(game, "home_team", None)

        def name(team: Any) -> str:
            if team is None:
                return "—"
            return getattr(team, "abbreviation", None) or getattr(team, "name", None) or "—"

        return f"{name(away)} @ {name(home)}"


__all__ = ["MlbTodayAdapter"]
