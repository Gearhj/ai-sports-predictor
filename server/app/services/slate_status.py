"""Every scheduled game, and the precise reason each one has no position.

WHY THIS EXISTS
---------------
"No position" is the same words for five completely different situations, and a
board that renders them identically teaches a reader that the model declined
five bets it never saw:

    NOT_YET_EVALUATED   the game has not reached its evaluation window
    FORECAST_PENDING    no eligible forecast has been published yet
    PRICE_UNAVAILABLE   a forecast exists; no usable quote does
    POLICY_INACTIVE     no activated policy covers this sport at all
    NO_QUALIFYING_VALUE the policy looked at a real price and declined
    POSITION_RECORDED   there is one

Only the fifth is a rejected bet. The first four are missing inputs or a missing
activation, and the difference is the whole point of this service.

THE DATE WINDOW IS STATED, NOT ASSUMED
--------------------------------------
A slate is a local calendar day in the display timezone, not a UTC day and not a
rolling window. A 10pm Pacific first pitch belongs to the evening it is played.
Every payload names the window it used, because "19 scheduled games" and "10 MLB
games today" can both be true of the same database and mean different things.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

SCHEMA_VERSION = "AISP_SLATE_STATUS_V1"

#: How old a moneyline quote may be and still satisfy the existing policy. Read
#: from the collector's own constant so this cannot drift from what qualifies.
from app.services.quote_coverage import MAX_QUOTE_STALENESS_MINUTES  # noqa: E402

#: The states, ordered from "nothing has happened yet" to "something has".
STATE_ORDER = (
    "POSITION_RECORDED",
    "NO_QUALIFYING_VALUE",
    "PRICE_UNAVAILABLE",
    "FORECAST_PENDING",
    "NOT_YET_EVALUATED",
    "POLICY_INACTIVE",
)


@dataclass(frozen=True)
class Window:
    """The exact span a slate covers, and the clock it was measured on."""

    day: date
    timezone: str
    start_utc: datetime
    end_utc: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "local_date": self.day.isoformat(),
            "timezone": self.timezone,
            "start_utc": self.start_utc.isoformat(),
            "end_utc": self.end_utc.isoformat(),
            "description": (
                f"Games starting on {self.day.isoformat()} local time in "
                f"{self.timezone}. Not a UTC day and not a rolling window."
            ),
        }


def slate_window(day: date, timezone: str = "America/New_York") -> Window:
    """One local calendar day, converted to the UTC span it actually covers."""

    zone = ZoneInfo(timezone)
    start_local = datetime.combine(day, datetime.min.time(), tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    return Window(
        day=day, timezone=timezone,
        start_utc=start_local.astimezone(UTC), end_utc=end_local.astimezone(UTC),
    )


def _leagues_with_active_policy(session: Session) -> set[str]:
    """Which sports have an activated paper policy at all.

    A sport with none is not declining wagers; it has no evaluator. NFL is in
    that position today and the board has to say so rather than showing an empty
    decision column.
    """

    from app.models import ShadowExperiment

    rows = session.scalars(
        select(ShadowExperiment.sport).where(ShadowExperiment.is_active.is_(True))
    ).all()
    return {str(value).upper() for value in rows if value}


def slate_status(
    session: Session, *, day: date | None = None,
    timezone: str = "America/New_York", now: datetime | None = None,
) -> dict[str, Any]:
    """One row per scheduled game, with the reason it stands where it does."""

    from app.models import (
        Game, GameTeam, League, MarketOutcome, Market, OddsSnapshot,
        OfficialAspPosition, ShadowExperimentDecision, ShadowForecast, Sportsbook, Team,
    )

    moment = now or datetime.now(UTC)
    target = day or moment.astimezone(ZoneInfo(timezone)).date()
    window = slate_window(target, timezone)
    active_policies = _leagues_with_active_policy(session)

    games = session.execute(
        select(Game.id, League.code, Game.starts_at, Game.status)
        .join(League, League.id == Game.league_id)
        .where(Game.starts_at >= window.start_utc, Game.starts_at < window.end_utc)
        .order_by(Game.starts_at)
    ).all()
    game_ids = [row[0] for row in games]
    if not game_ids:
        return {
            "schema_version": SCHEMA_VERSION, "window": window.as_dict(),
            "games": [], "counts": {}, "generated_at": moment.isoformat(),
        }

    sides: dict[Any, dict[str, str]] = {}
    for game_id, is_home, abbreviation in session.execute(
        select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation)
        .join(Team, Team.id == GameTeam.team_id)
        .where(GameTeam.game_id.in_(game_ids))
    ).all():
        sides.setdefault(game_id, {})["home" if is_home else "away"] = abbreviation

    forecasts = {
        row.canonical_game_id: row
        for row in session.scalars(
            select(ShadowForecast)
            .where(ShadowForecast.canonical_game_id.in_(game_ids))
            .order_by(ShadowForecast.canonical_game_id,
                      ShadowForecast.available_as_of_utc.desc())
        ).all()[::-1]
    }

    quotes: dict[Any, Any] = {}
    for row in session.execute(
        # The side is selected too: this query takes whichever outcome sorts
        # first, and a price with no side named is a number nobody can use.
        select(Market.game_id, OddsSnapshot.observed_at, Sportsbook.key,
               OddsSnapshot.american_odds, OddsSnapshot.source, Team.abbreviation)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .outerjoin(Team, Team.id == MarketOutcome.participant_team_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id.in_(game_ids), Market.market_type == "MONEYLINE")
        .order_by(Market.game_id, OddsSnapshot.observed_at.desc())
    ).all():
        quotes.setdefault(row[0], row)

    decisions: dict[Any, Any] = {}
    for row in session.scalars(
        select(ShadowExperimentDecision)
        .where(ShadowExperimentDecision.game_id.in_(game_ids))
        .order_by(ShadowExperimentDecision.game_id,
                  ShadowExperimentDecision.recommended_at.desc())
    ).all():
        decisions.setdefault(row.game_id, row)

    # Entry terms come from the positions themselves. The row's `quote` is the
    # latest market for the game -- a different number, and the one that used
    # to sit beside "Position recorded" as though it were the execution price.
    from app.services.position_entry_terms import entry_terms

    position_rows = session.execute(
        select(Market.game_id, OfficialAspPosition.id)
        .join(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(Market.game_id.in_(game_ids))
        .order_by(OfficialAspPosition.placed_at)
    ).all()
    terms = entry_terms(session, [pid for _, pid in position_rows])
    recorded: dict[Any, list[dict[str, Any]]] = {}
    for gid, pid in position_rows:
        if str(pid) in terms:
            recorded.setdefault(gid, []).append(terms[str(pid)])
    positions = {gid: len(items) for gid, items in recorded.items()}

    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for game_id, league, starts_at, status in games:
        starts = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=UTC)
        forecast = forecasts.get(game_id)
        quote = quotes.get(game_id)
        decision = decisions.get(game_id)
        held = int(positions.get(game_id, 0))

        quote_age_minutes = None
        if quote is not None:
            observed = quote[1] if quote[1].tzinfo else quote[1].replace(tzinfo=UTC)
            quote_age_minutes = (moment - observed).total_seconds() / 60.0
        quote_fresh = (
            quote_age_minutes is not None
            and quote_age_minutes <= MAX_QUOTE_STALENESS_MINUTES
        )

        state, explanation = _classify(
            league=league, forecast=forecast, quote=quote,
            quote_fresh=quote_fresh, quote_age_minutes=quote_age_minutes,
            decision=decision, held=held, starts=starts, moment=moment,
            active_policies=active_policies,
        )
        counts[state] = counts.get(state, 0) + 1

        rows.append({
            "game_id": str(game_id),
            "league_code": league,
            "teams": sides.get(game_id, {}),
            "starts_at": starts.isoformat(),
            "game_status": str(status),
            "forecast": None if forecast is None else {
                "forecast_id": forecast.forecast_id,
                "engine_id": forecast.engine_id,
                "created_at": _iso(forecast.created_at),
                "available_as_of": _iso(forecast.available_as_of_utc),
                "is_ready": bool(forecast.is_ready),
                "status": forecast.status,
            },
            "quote": None if quote is None else {
                "observed_at": _iso(quote[1]),
                "age_minutes": round(quote_age_minutes, 1),
                "sportsbook": quote[2],
                "american_odds": quote[3],
                "source": quote[4],
                "side": quote[5],
                "satisfies_policy_freshness": quote_fresh,
                "freshness_limit_minutes": MAX_QUOTE_STALENESS_MINUTES,
                # Named for what it is. Not the price any recorded position took.
                "role": "LATEST_MARKET_PRICE",
            },
            "policy": {
                "active_for_sport": league in active_policies,
                "note": (
                    None if league in active_policies else
                    f"No activated paper policy covers {league}. Games are "
                    "forecast and displayed; nothing evaluates them for a wager."
                ),
            },
            "latest_evaluation": None if decision is None else {
                "recommendation": decision.recommendation,
                "reason": decision.recommendation_reason,
                "phase": decision.phase,
                "recommended_at": _iso(decision.recommended_at),
            },
            "next_checkpoint": _next_checkpoint(starts, moment),
            "positions": held,
            "recorded_positions": recorded.get(game_id, []),
            "state": state,
            "explanation": explanation,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "window": window.as_dict(),
        "generated_at": moment.isoformat(),
        "games": rows,
        "counts": counts,
        "by_league": _league_counts(rows),
        "legend": {
            "POSITION_RECORDED": "A virtual position exists for this game.",
            "NO_QUALIFYING_VALUE": (
                "The policy evaluated a real price and declined. This is the "
                "only state that is a rejected wager."),
            "PRICE_UNAVAILABLE": (
                "A forecast exists; no quote fresh enough to qualify does."),
            "FORECAST_PENDING": (
                "No eligible forecast has been published for this game yet."),
            "NOT_YET_EVALUATED": (
                "The game has not reached its evaluation window."),
            "POLICY_INACTIVE": (
                "No activated paper policy covers this sport, so nothing "
                "evaluates the game."),
        },
    }


def _classify(
    *, league: str, forecast: Any, quote: Any, quote_fresh: bool,
    quote_age_minutes: float | None, decision: Any, held: int,
    starts: datetime, moment: datetime, active_policies: set[str],
) -> tuple[str, str]:
    """The state, and a sentence a reader can act on.

    Order matters. A recorded position outranks everything; an inactive policy
    outranks a missing input, because a sport nothing evaluates cannot be said
    to be waiting for a price.
    """

    if held:
        return "POSITION_RECORDED", f"{held} recorded position(s) for this game."

    if league not in active_policies:
        return "POLICY_INACTIVE", (
            f"No activated paper policy covers {league}, so no evaluator has "
            "run. This is not a declined wager."
        )

    if forecast is None:
        return "FORECAST_PENDING", (
            "No eligible forecast has been published for this game yet, so "
            "there is nothing to price against."
        )

    if quote is None:
        return "PRICE_UNAVAILABLE", (
            "A forecast exists, but no sportsbook moneyline has been collected "
            "for this game. No book has declined to price it; AISP has not "
            "captured one."
        )

    if not quote_fresh:
        age = "" if quote_age_minutes is None else f" ({quote_age_minutes/60:.1f}h old)"
        return "PRICE_UNAVAILABLE", (
            f"The only recorded price{age} is past the freshness the policy "
            "requires, so it cannot qualify."
        )

    if decision is not None and str(decision.recommendation).upper() not in {
            "", "NONE", "NOT_YET_EVALUATED"}:
        reason = decision.recommendation_reason or ""
        # A NO_BET whose reason is a missing input is not a declined wager.
        missing = any(word in reason.lower() for word in (
            "unavailable", "not_yet_evaluated", "no complete", "not reached"))
        if missing:
            return "NOT_YET_EVALUATED", reason
        return "NO_QUALIFYING_VALUE", (
            reason or "The policy evaluated this price and found no qualifying edge.")

    return "NOT_YET_EVALUATED", (
        "The game has a forecast and a usable price but has not been evaluated "
        "yet."
    )


def _next_checkpoint(starts: datetime, moment: datetime) -> dict[str, Any] | None:
    """The next scheduled decision point, when one is still ahead.

    The final pregame evaluation is the checkpoint that matters to a reader:
    after it, the game's decision is fixed.
    """

    window = MAX_QUOTE_STALENESS_MINUTES

    final_cutoff = starts - timedelta(minutes=15)
    if moment >= final_cutoff:
        return None
    opens = final_cutoff - timedelta(minutes=window)
    return {
        "name": "FINAL_PREGAME_EVALUATION",
        "at_utc": final_cutoff.isoformat(),
        "collection_window_opens_utc": opens.isoformat(),
        "minutes_away": round((final_cutoff - moment).total_seconds() / 60.0, 1),
        "window_open": moment >= opens,
    }


def _league_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        league = out.setdefault(row["league_code"], {"games": 0})
        league["games"] += 1
        league[row["state"]] = league.get(row["state"], 0) + 1
    return out


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.isoformat()


__all__ = ["SCHEMA_VERSION", "STATE_ORDER", "Window", "slate_status", "slate_window"]
