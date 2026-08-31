"""MLB read adapter.

Projects the EXISTING governed MLB board (ReadService.dashboard) and the
immutable experiment ledger into the shared multi-sport contract. It owns no
modelling, qualification, or staking logic: every state it emits is derived
from an authority that already decided it.

Baseball-specific vocabulary (starter, lineup, run line, first five) stays on
this side of the boundary — the shared shell never sees it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

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
FINAL_PREGAME_CUTOFF_MINUTES = 15
MARKET_NOT_SUPPORTED_NOTE = "Market not supported by this experiment (moneyline only)"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _is_funded_market(market_type: str | None) -> bool:
    lowered = (market_type or "").lower()
    return any(token in lowered for token in FUNDED_MARKET_PATTERNS)


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
        opportunities = {item.game_id: item for item in (dashboard.opportunities or [])}
        games = {str(game.id): game for game in dashboard.games or []}
        latest_forecast = self._latest_forecast_at(list(games))

        selections: list[NormalizedSelection] = []
        for game_id, game in games.items():
            selections.append(self._selection(game_id, game, opportunities.get(game_id), now))

        counts = {state: 0 for state in DState}
        for item in selections:
            counts[item.decision_state] = counts.get(item.decision_state, 0) + 1
        funded = [s for s in selections if s.funding_state == FState.OFFICIAL_VIRTUAL_POSITION]
        open_count, settled_count = self._position_counts(event_date)

        summary = dashboard.opportunity_summary
        return SportSection(
            sport_code=MLB.sport_code,
            league_code=MLB.league_code,
            display_name=MLB.display_name,
            availability_state=MLB.availability,
            scheduled_event_count=len(games),
            valid_forecast_count=int(getattr(dashboard.summary, "complete_forecasts", 0) or 0),
            final_qualified_count=counts.get(DState.FINAL_QUALIFIED, 0),
            awaiting_final_evidence_count=counts.get(DState.AWAITING_FINAL_EVIDENCE, 0),
            provisional_count=counts.get(DState.PROVISIONAL_EDGE, 0),
            watch_count=counts.get(DState.WATCH, 0),
            pass_count=counts.get(DState.PASS, 0) + counts.get(DState.FINAL_NO_BET, 0),
            open_position_count=open_count,
            settled_today_count=settled_count,
            virtual_stake=round(sum(s.virtual_stake or 0.0 for s in funded), 2),
            data_health=getattr(dashboard.summary, "overall_data_health", None),
            latest_forecast_at=latest_forecast,
            latest_market_at=_aware(getattr(summary, "odds_last_refresh", None)) if summary else None,
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
        starts_at = _aware(getattr(game, "scheduled_at", None) or getattr(game, "starts_at", None))
        cutoff = starts_at - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES) if starts_at else None
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
        )
        if opportunity is None:
            return NormalizedSelection(
                decision_state=DState.NOT_EVALUATED,
                funding_state=FState.NOT_EVALUATED,
                status_note="No governed evaluation exists for this event yet.",
                **base,
            )

        v2 = getattr(opportunity, "v2", None)
        best = getattr(opportunity, "best", None)
        alternates = list(getattr(opportunity, "alternates", []) or [])
        moneyline = next(
            (item for item in ([best] if best else []) + alternates if _is_funded_market(getattr(item, "market_type", None))),
            None,
        )
        experiment_status = getattr(opportunity, "experiment_status", None)
        stake = getattr(opportunity, "experiment_stake", None)
        has_position = experiment_status in ("FROZEN", "SETTLED")

        # 1) An immutable official position is the strongest authority.
        if has_position:
            return NormalizedSelection(
                decision_state=DState.FINAL_QUALIFIED,
                funding_state=FState.OFFICIAL_VIRTUAL_POSITION,
                virtual_stake=float(stake) if stake is not None else None,
                status_note="Official virtual position frozen from the final-pregame decision.",
                **self._pricing(moneyline or best),
                **self._experiment_identity(),
                **base,
            )

        # 2) The V2.1 challenger lifecycle is the explicit final/pending authority.
        if v2 is not None and moneyline is not None:
            lifecycle = getattr(v2, "lifecycle", None)
            if lifecycle == "FINAL_QUALIFIED":
                state, funding = DState.FINAL_QUALIFIED, FState.ELIGIBLE_NOT_FUNDED
                note = "Final governed qualification; no virtual position was created (caps or challenger not yet effective)."
            elif lifecycle == "FINAL_NO_BET":
                state, funding = DState.FINAL_NO_BET, FState.ELIGIBLE_NOT_FUNDED
                note = "Final decision reached: no position."
            else:
                state, funding = DState.AWAITING_FINAL_EVIDENCE, FState.ELIGIBLE_NOT_FUNDED
                note = "Awaiting the final-pregame decision."
            identity = self._experiment_identity()
            # The V2 challenger's own policy version is the authority for a V2
            # lifecycle decision; fall back to the active experiment's policy.
            identity["policy_version"] = getattr(v2, "policy_version", None) or identity.get("policy_version")
            return NormalizedSelection(
                decision_state=state,
                funding_state=funding,
                evidence_tier=getattr(v2, "tier", None),
                adjusted_edge=getattr(v2, "adjusted_edge", None),
                expected_value=getattr(v2, "adjusted_expected_value", None),
                outstanding_gates=list(getattr(v2, "reasons", []) or []),
                status_note=note,
                **self._pricing(moneyline),
                **identity,
                **base,
            )

        # 3) No funded-market candidate: a board-only opportunity.
        board_state = getattr(opportunity, "recommendation_state", "NO_BET")
        selection_view = best
        funding = (
            FState.MARKET_NOT_SUPPORTED
            if selection_view is not None and not _is_funded_market(getattr(selection_view, "market_type", None))
            else FState.ELIGIBLE_NOT_FUNDED
        )
        pre_cutoff = cutoff is not None and now < cutoff
        if board_state == "QUALIFIED_BET":
            state = DState.FINAL_QUALIFIED if not pre_cutoff else DState.AWAITING_FINAL_EVIDENCE
        elif board_state in ("PROVISIONAL_EDGE", "EXTREME_DISAGREEMENT"):
            state = DState.PROVISIONAL_EDGE
        elif board_state == "WATCH":
            state = DState.WATCH
        else:
            # Never call a pre-cutoff candidate a final no-bet.
            state = DState.AWAITING_FINAL_EVIDENCE if pre_cutoff and selection_view is not None else DState.PASS
        return NormalizedSelection(
            decision_state=state,
            funding_state=funding,
            outstanding_gates=list(getattr(opportunity, "qualification_reasons", []) or []),
            status_note=(
                MARKET_NOT_SUPPORTED_NOTE if funding == FState.MARKET_NOT_SUPPORTED
                else getattr(opportunity, "recommendation_reason", None)
            ),
            **self._pricing(selection_view),
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

    def _experiment_identity(self) -> dict[str, Any]:
        from app.services.shadow_experiment_service import ShadowExperimentService

        experiment = ShadowExperimentService(self.session, self.settings).active_experiment()
        if experiment is None:
            return {}
        return {
            "experiment_id": str(experiment.id),
            "experiment_name": experiment.name,
            "policy_version": experiment.decision_policy,
        }

    def _position_counts(self, event_date: date) -> tuple[int, int]:
        """Open positions and positions settled on this operational date,
        across every registered MLB experiment (never pooled with another sport)."""
        from app.models import (
            OfficialAspPosition,
            PositionSettlement,
            PositionStatus,
            ShadowExperiment,
            ShadowExperimentDecision,
        )

        experiment_ids = list(self.session.scalars(
            select(ShadowExperiment.id).where(ShadowExperiment.sport == "MLB")
        ))
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
