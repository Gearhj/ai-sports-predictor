"""The NFL paper policy's decision owner, run at the final-pregame checkpoint.

WHAT IT PRODUCES
----------------
One outcome per NFL game in its checkpoint window, and always an outcome: a
recorded virtual position when every requirement passes, or the exact reason it
did not. "Nothing happened" is not an acceptable answer for a checkpoint that
came and went, because a reader cannot tell it apart from a job that failed to
run.

THE ORDER OF THE GATES IS THE POINT
-----------------------------------
Eligibility is checked FIRST, before any price arithmetic. A reader who sees an
edge computed and then a refusal will reasonably ask why the edge was not taken;
a reader who sees "not eligible" understands that the arithmetic was never the
question. It also means a missing producer binding cannot be quietly rescued by
a big number.

Nothing here calls a provider. It reads the frozen forecast and the stored
quote, both of which some other owner already paid for.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, OddsSnapshot,
    ShadowExperiment, ShadowForecast, Sportsbook, Team,
)
from app.services import nfl_paper_policy as policy
from app.services import nfl_paper_eligibility as eligibility

#: The producer's own FINAL_PREGAME eligibility: 30 to 15 minutes before
#: kickoff. Unchanged here; NFL adds no exception to the existing timing.
WINDOW_OPENS = timedelta(minutes=30)
WINDOW_CLOSES = timedelta(minutes=15)

#: The existing freshness rule, not a new one.
MAX_QUOTE_AGE_MINUTES = 240


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _era(session: Session) -> ShadowExperiment | None:
    return session.scalar(
        select(ShadowExperiment).where(
            ShadowExperiment.sport == "NFL",
            ShadowExperiment.decision_policy == policy.POLICY_VERSION,
            ShadowExperiment.is_active.is_(True),
        )
    )


def games_at_checkpoint(
    session: Session, *, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """NFL games inside the final-pregame window right now."""

    moment = now or datetime.now(UTC)
    rows = session.execute(
        select(Game.id, Game.starts_at)
        .join(League, League.id == Game.league_id)
        .where(League.code == "NFL",
               Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= moment + WINDOW_CLOSES,
               Game.starts_at <= moment + WINDOW_OPENS)
        .order_by(Game.starts_at)
    ).all()
    out = []
    for game_id, starts_at in rows:
        sides = dict(session.execute(
            select(GameTeam.is_home, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game_id)
        ).all())
        out.append({
            "game_id": str(game_id),
            "starts_at": _aware(starts_at).isoformat(),
            "away": sides.get(False), "home": sides.get(True),
        })
    return out


def _forecast(session: Session, game_id: Any) -> ShadowForecast | None:
    """The frozen final-pregame forecast, falling back to the current one."""

    for snapshot in ("FINAL_PREGAME", None):
        query = select(ShadowForecast).where(
            ShadowForecast.canonical_game_id == game_id,
            ShadowForecast.engine_id == "NFL_M1_XGBOOST",
        )
        if snapshot:
            query = query.where(ShadowForecast.snapshot_type == snapshot)
        row = session.scalar(
            query.order_by(ShadowForecast.available_as_of_utc.desc()).limit(1))
        if row is not None:
            return row
    return None


def _best_quote(session: Session, game_id: Any) -> list[dict[str, Any]]:
    """Every side's freshest stored moneyline, newest first. No provider call."""

    rows = session.execute(
        select(OddsSnapshot.observed_at, Sportsbook.key, MarketOutcome.outcome_key,
               OddsSnapshot.american_odds, OddsSnapshot.source)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id == game_id, Market.market_type == "MONEYLINE")
        .order_by(OddsSnapshot.observed_at.desc())
    ).all()
    return [{
        "observed_at": _aware(row[0]).isoformat(), "sportsbook": row[1],
        "side": row[2].split(":")[0], "american_odds": row[3], "source": row[4],
    } for row in rows]


def evaluate(
    session: Session, settings: Settings, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Run the checkpoint. Returns one decision per game, always with a reason."""

    moment = now or datetime.now(UTC)
    era = _era(session)
    if era is None:
        return {"records": 0, "reason": "NFL_PAPER_POLICY_IS_NOT_ACTIVE",
                "decisions": []}

    due = games_at_checkpoint(session, now=moment)
    if not due:
        return {"records": 0, "reason": "NO_NFL_GAME_AT_ITS_CHECKPOINT",
                "decisions": [], "experiment_id": str(era.id)}

    decisions: list[dict[str, Any]] = []
    for game in due:
        decisions.append(_decide(session, era, game, moment))
    return {
        "records": sum(1 for d in decisions if d["outcome"] == "POSITION_RECORDED"),
        "reason": "CHECKPOINT_EVALUATED",
        "experiment_id": str(era.id),
        "policy_version": policy.POLICY_VERSION,
        "decisions": decisions,
    }


def _decide(
    session: Session, era: ShadowExperiment, game: dict[str, Any], moment: datetime,
) -> dict[str, Any]:
    base = {"game_id": game["game_id"], "matchup": f"{game['away']} at {game['home']}",
            "starts_at": game["starts_at"], "evaluated_at": moment.isoformat()}

    forecast = _forecast(session, game["game_id"])
    if forecast is None:
        return {**base, "outcome": "BLOCKED", "reason": "NO_FORECAST",
                "detail": "No NFL_M1_XGBOOST forecast exists for this game."}

    # Eligibility first, deliberately. See the module docstring.
    binding = eligibility.check(
        session, event_id=forecast.event_id, forecast_id=forecast.forecast_id,
        experiment_id=str(era.id), now=moment)
    if not binding["eligible"]:
        return {**base, "outcome": "BLOCKED", "reason": binding["reason"],
                "detail": binding.get("detail"),
                "forecast_id": forecast.forecast_id,
                "eligibility": binding}

    quotes = _best_quote(session, game["game_id"])
    if not quotes:
        return {**base, "outcome": "DECLINED", "reason": "NO_STORED_QUOTE",
                "detail": ("No moneyline quote has been captured for this game. "
                           "This is AISP holding no price, not a sportsbook "
                           "withholding one."),
                "forecast_id": forecast.forecast_id}

    freshest = quotes[0]
    age_minutes = (moment - datetime.fromisoformat(
        freshest["observed_at"])).total_seconds() / 60
    if age_minutes > MAX_QUOTE_AGE_MINUTES:
        return {**base, "outcome": "DECLINED", "reason": "QUOTE_TOO_STALE",
                "detail": (f"The freshest stored quote is {age_minutes / 60:.1f} "
                           f"hours old, past the {MAX_QUOTE_AGE_MINUTES / 60:.0f}"
                           "-hour freshness limit."),
                "quote_age_minutes": round(age_minutes, 1),
                "forecast_id": forecast.forecast_id}

    p_home = forecast.full_home_win_probability
    p_away = forecast.full_away_win_probability
    p_tie = max(0.0, 1.0 - (p_home or 0) - (p_away or 0))

    best: dict[str, Any] | None = None
    for quote in quotes:
        if quote["american_odds"] is None:
            continue
        if (moment - datetime.fromisoformat(quote["observed_at"])
                ).total_seconds() / 60 > MAX_QUOTE_AGE_MINUTES:
            continue
        win = p_away if quote["side"] == "AWAY" else p_home
        market = policy.TiedMarket(
            win_probability=win or 0.0, tie_probability=p_tie,
            decimal_odds=policy.american_to_decimal(int(quote["american_odds"])))
        expected = policy.moneyline_ev(market)
        if best is None or expected > best["expected_value"]:
            best = {**quote, "win_probability": win, "tie_probability": p_tie,
                    "expected_value": expected,
                    "quarter_kelly": policy.kelly_fraction(market) * policy.KELLY_FRACTION,
                    "stake": policy.proposed_stake(
                        market, bankroll=float(era.starting_bankroll))}

    if best is None or best["expected_value"] < policy.MIN_ADJUSTED_EDGE:
        return {**base, "outcome": "DECLINED", "reason": "NO_QUALIFYING_VALUE",
                "detail": ("No side cleared the "
                           f"{policy.MIN_ADJUSTED_EDGE * 100:.1f}-point minimum."),
                "best": best, "forecast_id": forecast.forecast_id}

    # Everything passed. Recording the position itself is the next owner's job
    # and is deliberately not done inline here.
    return {**base, "outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED",
            "forecast_id": forecast.forecast_id, "selection": best,
            "eligibility": binding}


__all__ = ["MAX_QUOTE_AGE_MINUTES", "WINDOW_CLOSES", "WINDOW_OPENS",
           "evaluate", "games_at_checkpoint"]
