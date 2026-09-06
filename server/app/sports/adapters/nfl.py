"""NFL research-shadow read adapter.

Projects the EXISTING frozen NFL research shadow (imported Model Studio
forecasts joined to governed odds) into the shared multi-sport contract.

Hard invariant: NFL is RESEARCH_SHADOW in this phase. No NFL selection may ever
map to FINAL_QUALIFIED or to OFFICIAL_VIRTUAL_POSITION, because no funding
experiment covers football — every NFL selection is RESEARCH_ONLY and
``can_ever_fund_official_position`` is False. A research "qualified shadow"
recommendation is a research result, not a governed funding qualification, and
is therefore surfaced as a provisional research opportunity.

Football vocabulary (kickoff, quarterback, spread, margin) stays on this side
of the boundary.
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
from app.sports.registry import NFL

RESEARCH_NOTE = "NFL research shadow — no official bankroll"
FINAL_PREGAME_SNAPSHOT = "FINAL_PREGAME"
# The bundle's stored sport value ('NFL'); mapped, never rewritten.
STORED_SPORT = "NFL"


def _text(value: Any) -> str | None:
    """Normalise a persisted enum/string token, or None when absent."""

    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    return text.upper() or None


def _aware(value: Any) -> datetime | None:
    """Coerce a board timestamp to an aware datetime.

    ``ShadowBoardService.board`` returns a plain dict, so timestamps may arrive
    as ISO strings rather than datetimes. An unparseable value yields None
    rather than raising — a missing timestamp is reported honestly.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class NflTodayAdapter:
    """Builds the NFL SportSection for TODAY_ALL_SPORTS_READ_MODEL_V1."""

    definition = NFL

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def build_section(self, event_date: date, *, now: datetime | None = None) -> SportSection:
        from app.services.shadow_board_service import ShadowBoardService

        now = _aware(now) or datetime.now(UTC)
        board = ShadowBoardService(self.session, self.settings).board(event_date, sport=STORED_SPORT)
        games = self._board_games(board)

        selections: list[NormalizedSelection] = []
        latest_forecast: datetime | None = None
        for game in games:
            selection, cutoff_at = self._selection(game, now)
            selections.append(selection)
            if cutoff_at is not None and (latest_forecast is None or cutoff_at > latest_forecast):
                latest_forecast = cutoff_at

        counts: dict[DState, int] = {}
        for item in selections:
            counts[item.decision_state] = counts.get(item.decision_state, 0) + 1
        with_forecast = sum(1 for game in games if self._engines(game))
        latest_market = self._latest_market_at(event_date)

        return SportSection(
            sport_code=NFL.sport_code,
            league_code=NFL.league_code,
            display_name=NFL.display_name,
            availability_state=NFL.availability,
            scheduled_event_count=len(games),
            valid_forecast_count=with_forecast,
            # Structurally zero: research shadow can never produce a governed
            # final qualification or an official position.
            final_qualified_count=0,
            awaiting_final_evidence_count=counts.get(DState.AWAITING_FINAL_EVIDENCE, 0),
            provisional_count=counts.get(DState.PROVISIONAL_EDGE, 0),
            watch_count=counts.get(DState.WATCH, 0),
            pass_count=counts.get(DState.PASS, 0),
            open_position_count=0,
            settled_today_count=0,
            virtual_stake=0.0,
            data_health=None,
            latest_forecast_at=latest_forecast,
            latest_market_at=latest_market,
            engine_label=NFL.engine_label,
            can_create_official_positions=False,
            official_positions_blocked_reason=NFL.official_positions_note,
            degraded=bool(games and latest_market is None),
            degraded_reason=(
                "No governed NFL market observation is persisted for this slate."
                if games and latest_market is None else None
            ),
            selections=selections,
        )

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _board_games(board: Any) -> list[Any]:
        if board is None:
            return []
        games = board.get("games") if isinstance(board, dict) else getattr(board, "games", None)
        return list(games or [])

    @staticmethod
    def _field(obj: Any, name: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    def _engines(self, game: Any) -> list[Any]:
        return list(self._field(game, "engines", []) or [])

    def _selection(self, game: Any, now: datetime) -> tuple[NormalizedSelection, datetime | None]:
        event_id = str(self._field(game, "event_id", ""))
        kickoff = _aware(self._field(game, "scheduled_start_utc"))
        engines = self._engines(game)
        engine = engines[0] if engines else None
        snapshot_type = str(self._field(engine, "snapshot_type", "") or "") if engine else ""
        forecast_cutoff = _aware(self._field(engine, "forecast_cutoff_utc")) if engine else None
        decisions = list(self._field(engine, "decisions", []) or []) if engine else []
        # The board's own ranking already ordered decisions; take its lead item.
        decision = decisions[0] if decisions else None

        base = dict(
            sport_code=NFL.sport_code,
            league_code=NFL.league_code,
            event_id=event_id,
            event_label=self._event_label(game),
            starts_at=kickoff,
            # Kickoff is the research shadow's legitimate evidence cutoff.
            final_decision_cutoff=kickoff,
            source_label=NFL.engine_label,
            model_status="RESEARCH_SHADOW",
            # Invariant: never fundable in this phase.
            can_ever_fund_official_position=False,
            detail_href=(
                f"/shadow-board?date={self._field(game, 'event_date')}&sport=NFL"
                f"#event-{event_id}"
            ),
            # Only projected when the research board actually persists it. NFL
            # is research-shadow and unfunded, so there is never a position
            # lifecycle to report here.
            event_status=_text(self._field(game, "status")),
        )
        if engine is None or decision is None:
            return (
                NormalizedSelection(
                    decision_state=DState.NOT_EVALUATED,
                    funding_state=FState.RESEARCH_ONLY,
                    status_note=f"{RESEARCH_NOTE} — no imported research decision for this event.",
                    **base,
                ),
                forecast_cutoff,
            )

        recommendation = str(self._field(decision, "recommendation", "NO_BET") or "NO_BET").upper()
        pre_cutoff = kickoff is not None and now < kickoff and snapshot_type != FINAL_PREGAME_SNAPSHOT
        if pre_cutoff:
            state = DState.AWAITING_FINAL_EVIDENCE
            note = f"{RESEARCH_NOTE} — awaiting the final pregame research snapshot."
        elif recommendation in ("QUALIFIED_BET", "QUALIFIED_SHADOW", "PROVISIONAL_EDGE", "REVIEW_REQUIRED"):
            # A research qualification is NOT a governed funding qualification.
            state = DState.PROVISIONAL_EDGE
            note = f"{RESEARCH_NOTE} — research result only, never an official position."
        elif recommendation == "WATCH":
            state = DState.WATCH
            note = RESEARCH_NOTE
        else:
            state = DState.PASS
            note = RESEARCH_NOTE

        return (
            NormalizedSelection(
                decision_state=state,
                funding_state=FState.RESEARCH_ONLY,
                market_label=self._field(decision, "market"),
                selection_label=self._field(decision, "selection"),
                line=self._field(decision, "line"),
                price_american=self._field(decision, "american_odds"),
                sportsbook=self._field(decision, "book"),
                model_probability=self._pct(self._field(decision, "model_probability")),
                no_vig_probability=self._pct(self._field(decision, "no_vig_probability")),
                adjusted_edge=self._pct(self._field(decision, "uncertainty_adjusted_edge")),
                expected_value=self._pct(self._field(decision, "expected_value")),
                evidence_tier=snapshot_type or None,
                # Research stakes are simulation-only and never funded.
                virtual_stake=None,
                decision_timestamp=forecast_cutoff,
                status_note=note,
                outstanding_gates=(
                    ["Awaiting final pregame research snapshot"] if pre_cutoff
                    else ([] if self._field(decision, "has_quote", True) else ["No governed quote"])
                ),
                **base,
            ),
            forecast_cutoff,
        )

    @staticmethod
    def _pct(value: Any) -> float | None:
        """Board decisions carry unit-interval probabilities/edges; the shared
        contract displays percentage points like the MLB projection."""
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return round(numeric * 100, 2) if -1.0 <= numeric <= 1.0 else round(numeric, 2)

    def _event_label(self, game: Any) -> str:
        from app.models import Team

        def name(team_id: Any) -> str:
            if not team_id:
                return "—"
            try:
                governed_id = UUID(str(team_id))
            except (TypeError, ValueError):
                # NFL research bundles retain canonical provider/team codes
                # such as SF and LAR. They are valid display identities, not
                # database UUIDs; never issue a malformed UUID query.
                return str(team_id)
            team = self.session.get(Team, governed_id)
            if team is None:
                return str(team_id)[:8]
            return team.abbreviation or team.name or "—"

        away = name(self._field(game, "away_team_id"))
        home = name(self._field(game, "home_team_id"))
        return f"{away} @ {home}"

    def _latest_market_at(self, event_date: date) -> datetime | None:
        """Newest persisted NFL quote used by the research-shadow board."""
        from app.models import ShadowBoardDecision, ShadowForecastBundle

        return _aware(self.session.scalar(
            select(func.max(ShadowBoardDecision.quote_observed_at))
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowBoardDecision.bundle_id)
            .where(
                ShadowForecastBundle.slate_date == event_date,
                ShadowForecastBundle.sport == STORED_SPORT,
            )
        ))


__all__ = ["NflTodayAdapter"]
