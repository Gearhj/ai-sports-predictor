"""Parlay Watch: which combinations exist, what they are worth, and what is not known.

WHAT COUNTS AS AN ELIGIBLE LEG
------------------------------
Four gates, all of which already exist elsewhere in AISP and are reused rather
than reinvented:

1.  **A current forecast.** ``ForecastSelector`` must return V18 as AVAILABLE.
    A missing or superseded V18 does not silently become Gen 1 -- the game drops
    out of enumeration and says why.
2.  **A pregame cutoff.** First pitch minus ``FINAL_PREGAME_CUTOFF_MINUTES``,
    the same 15 minutes the funded experiment freezes on. A leg past its cutoff
    cannot be recorded, so it is not offered.
3.  **Quote freshness.** A price older than ``FRESH_MINUTES`` is carried with its
    age and excluded from enumeration, because a four-hour-old price is not a
    price a reader could act on.
4.  **One sportsbook.** A combination is priced only from legs the same book
    quoted. Mixing books produces a number no book would ever offer.

Every ticket takes its legs from **distinct games**. Two selections from one
game are correlated by construction and the independent product would be wrong,
not merely approximate; this release excludes them outright.

READS ONLY
----------
Enumeration touches stored rows and nothing else. No provider request is made
here, on this path or any path it calls, so refreshing the page or widening the
filters cannot spend provider credit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import local_day_bounds
from app.models import (
    Game,
    GameTeam,
    League,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    ShadowForecast,
    Sportsbook,
    Team,
)
from app.parlay.pricing import (
    ESTIMATED_PRICE_NOTE,
    INDEPENDENCE_NOTE,
    Leg,
    ParlayPricingError,
    price_ticket,
)
from app.services.forecast_selection import Availability, ForecastSelector
from app.services.shadow_experiment_service import FINAL_PREGAME_CUTOFF_MINUTES
from app.totals.pricing import (
    PROBABILITY_UNITS,
    TotalsDistribution,
    TotalsPricingError,
    price_line,
)
from app.totals.index_attachment import attachment_map
from app.totals.read_service import FRESH_MINUTES

SCHEMA_VERSION = "AISP_MLB_PARLAY_WATCH_V1"

#: How many priced combinations to return. Enumeration is cheap; serialising
#: thousands of near-identical tickets is not, and no reader browses them.
MAX_TICKETS = 60

MARKET_FILTERS = {"ALL", "OVERS_ONLY", "TOTALS_ONLY", "MIXED"}

FEATURE_HIGHEST_CHANCE = "HIGHEST_ESTIMATED_CHANCE"
FEATURE_BEST_VALUE = "BEST_ESTIMATED_VALUE"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class MlbParlayReadService:
    """Enumerates and prices research combinations for one slate."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.selector = ForecastSelector(session)

    # -- public ------------------------------------------------------------

    def watch(
        self,
        event_date: date,
        *,
        leg_count: int = 2,
        market_filter: str = "ALL",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        if leg_count not in (2, 3):
            raise ValueError("leg_count must be 2 or 3")
        chosen = market_filter.upper()
        if chosen not in MARKET_FILTERS:
            raise ValueError(f"market filter must be one of {sorted(MARKET_FILTERS)}")

        legs, excluded = self._eligible_legs(event_date, now=now)
        verified = attachment_map(self.session)
        candidates = self._combinations(legs, leg_count=leg_count, market_filter=chosen)
        featured = self._featured(candidates)

        return {
            "schema_version": SCHEMA_VERSION,
            "event_date": event_date.isoformat(),
            "generated_at": now.isoformat(),
            "leg_count": leg_count,
            "market_filter": chosen,
            "probability_units": PROBABILITY_UNITS,
            "independence_note": INDEPENDENCE_NOTE,
            "estimated_price_note": ESTIMATED_PRICE_NOTE,
            "pregame_cutoff_minutes": FINAL_PREGAME_CUTOFF_MINUTES,
            "legs_available": len(legs),
            # How much of the enumerated evidence a published totals index has
            # verified. Reported rather than enforced: a leg from a forecast no
            # index covers is still priced from the same stored field, and
            # saying so is more useful than withholding it.
            "index_verified_forecasts": sorted(
                {leg.forecast_id for leg in legs if leg.forecast_id in verified}),
            "legs_from_index_verified_forecasts": sum(
                1 for leg in legs if leg.forecast_id in verified),
            "candidate_count": len(candidates),
            "tickets": candidates[:MAX_TICKETS],
            "truncated": len(candidates) > MAX_TICKETS,
            "featured": featured,
            "excluded_games": excluded,
            "leg_inventory": self._inventory(legs),
        }

    # -- legs --------------------------------------------------------------

    def _eligible_legs(
        self, event_date: date, *, now: datetime,
    ) -> tuple[list[Leg], list[dict[str, Any]]]:
        legs: list[Leg] = []
        excluded: list[dict[str, Any]] = []
        for game in self._mlb_games(event_date):
            starts_at = _aware(game.starts_at)
            cutoff = (
                starts_at - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
                if starts_at else None
            )
            matchup, home, away = self._matchup(game.id)

            if cutoff is None or now >= cutoff:
                excluded.append({
                    "game_id": str(game.id), "matchup": matchup,
                    "reason": "PAST_PREGAME_CUTOFF",
                    "detail": (
                        "This game is inside the "
                        f"{FINAL_PREGAME_CUTOFF_MINUTES}-minute pregame cutoff, so "
                        "no leg from it can still be recorded."
                    ),
                })
                continue

            selection = self.selector.select(game, now=now)
            primary = selection.primary
            if primary is None or primary.availability is not Availability.AVAILABLE:
                excluded.append({
                    "game_id": str(game.id), "matchup": matchup,
                    "reason": (
                        "NO_CURRENT_V18" if primary is None
                        else f"V18_{primary.availability.value}"),
                    "detail": (
                        primary.availability_detail if primary
                        else "No V18 forecast exists for this game."
                    ) + " Gen 1 is not substituted for parlay legs.",
                })
                continue

            row = self.session.scalar(select(ShadowForecast).where(
                ShadowForecast.forecast_id == primary.forecast_id))
            before = len(legs)
            legs.extend(self._moneyline_legs(
                game, primary, row, matchup=matchup, home=home, away=away,
                starts_at=starts_at, now=now))
            legs.extend(self._total_legs(
                game, primary, row, matchup=matchup, starts_at=starts_at, now=now))
            if len(legs) == before:
                excluded.append({
                    "game_id": str(game.id), "matchup": matchup,
                    "reason": "NO_FRESH_PRICED_SELECTION",
                    "detail": (
                        "V18 is current for this game, but no sportsbook price "
                        f"newer than {FRESH_MINUTES} minutes is stored for either "
                        "market."
                    ),
                })
        return legs, excluded

    def _moneyline_legs(
        self, game: Game, primary: Any, row: ShadowForecast | None, *,
        matchup: str, home: dict[str, Any], away: dict[str, Any],
        starts_at: datetime | None, now: datetime,
    ) -> list[Leg]:
        # Read the fraction off the forecast row itself rather than off the
        # selector's display field. The stored columns are unambiguous -- they
        # sum to 1.0 -- whereas a display-facing number has been scaled to
        # percent on some surfaces and not others, and guessing the scale from
        # the magnitude ("greater than one, so divide") would silently mangle
        # any genuine probability near the boundary.
        if row is None:
            return []
        home_p = row.full_home_win_probability
        away_p = row.full_away_win_probability
        if home_p is None or away_p is None:
            return []
        home_p, away_p = float(home_p), float(away_p)
        if not (0.0 < home_p < 1.0 and 0.0 < away_p < 1.0):
            return []
        if abs(home_p + away_p - 1.0) > 1e-6:
            # MLB plays to a decision. Sides that do not exhaust the probability
            # are not a usable moneyline, and a leg built from them would carry
            # a p_loss that is not the other side's p_win.
            return []
        probabilities = {str(home["id"]): home_p, str(away["id"]): away_p}
        out: list[Leg] = []
        for quote in self._quotes(game.id, MarketType.MONEYLINE, now=now):
            team_id = quote["participant_team_id"]
            p_win = probabilities.get(team_id)
            if p_win is None or not 0.0 < p_win < 1.0:
                continue
            label = home if team_id == str(home["id"]) else away
            out.append(Leg(
                game_id=str(game.id),
                market="MONEYLINE",
                selection=f"{label['abbreviation']} ML",
                # MLB plays to a decision, so a moneyline cannot push. Stating
                # that as 0.0 rather than omitting it keeps every leg on the
                # same three-outcome shape the ticket arithmetic expects.
                p_win=p_win, p_push=0.0, p_loss=1.0 - p_win,
                decimal_odds=quote["decimal_odds"],
                american_odds=quote["american_odds"],
                sportsbook=quote["sportsbook"],
                forecast_id=primary.forecast_id,
                engine_id=primary.engine_id,
                quote_observed_at=quote["observed_at"],
                starts_at=starts_at.isoformat() if starts_at else None,
                odds_snapshot_id=quote["odds_snapshot_id"],
                market_outcome_id=quote["market_outcome_id"],
            ))
        return out

    def _total_legs(
        self, game: Game, primary: Any, row: ShadowForecast | None, *,
        matchup: str, starts_at: datetime | None, now: datetime,
    ) -> list[Leg]:
        if row is None:
            return []
        try:
            distribution = TotalsDistribution.from_stored(
                (row.distributions or {}).get("totalRunDistribution"),
                forecast_id=row.forecast_id,
                engine_id=row.engine_id,
                engine_version=row.engine_version,
                simulation_count=(row.uncertainty or {}).get("simulationCount"),
            )
        except TotalsPricingError:
            return []

        out: list[Leg] = []
        for quote in self._quotes(game.id, MarketType.TOTAL, now=now):
            side = (quote["label"] or "").strip().upper()
            line = quote["point"]
            if side not in {"OVER", "UNDER"} or line is None:
                continue
            try:
                priced = price_line(distribution, float(line))
                p_win, p_push, p_loss = priced.fraction(side)
            except TotalsPricingError:
                continue
            if p_win <= 0.0:
                continue
            out.append(Leg(
                game_id=str(game.id),
                market="TOTAL",
                selection=f"{side} {line:g}",
                line=float(line),
                p_win=p_win, p_push=p_push, p_loss=p_loss,
                decimal_odds=quote["decimal_odds"],
                american_odds=quote["american_odds"],
                sportsbook=quote["sportsbook"],
                forecast_id=primary.forecast_id,
                engine_id=primary.engine_id,
                quote_observed_at=quote["observed_at"],
                starts_at=starts_at.isoformat() if starts_at else None,
                odds_snapshot_id=quote["odds_snapshot_id"],
                market_outcome_id=quote["market_outcome_id"],
            ))
        return out

    # -- combinations ------------------------------------------------------

    def _combinations(
        self, legs: list[Leg], *, leg_count: int, market_filter: str,
    ) -> list[dict[str, Any]]:
        by_book: dict[str, list[Leg]] = {}
        for leg in legs:
            by_book.setdefault(leg.sportsbook, []).append(leg)

        tickets: list[dict[str, Any]] = []
        for book, book_legs in by_book.items():
            for chosen in combinations(book_legs, leg_count):
                if len({leg.game_id for leg in chosen}) != leg_count:
                    continue  # distinct games only
                if not self._matches_filter(chosen, market_filter):
                    continue
                try:
                    price = price_ticket(list(chosen))
                except ParlayPricingError:
                    continue
                tickets.append(self._ticket_payload(list(chosen), price, book))
        # Best estimated value first, so the list a reader scrolls is ordered by
        # the quantity the page is actually about.
        tickets.sort(
            key=lambda t: t["price"]["estimated_net_return_per_unit"], reverse=True)
        return tickets

    @staticmethod
    def _matches_filter(legs: tuple[Leg, ...], market_filter: str) -> bool:
        markets = {leg.market for leg in legs}
        if market_filter == "ALL":
            return True
        if market_filter == "TOTALS_ONLY":
            return markets == {"TOTAL"}
        if market_filter == "OVERS_ONLY":
            return all(
                leg.market == "TOTAL" and leg.selection.startswith("OVER")
                for leg in legs
            )
        if market_filter == "MIXED":
            return len(markets) > 1
        return True

    def _ticket_payload(
        self, legs: list[Leg], price: Any, book: str,
    ) -> dict[str, Any]:
        cutoffs = [
            _aware(datetime.fromisoformat(leg.starts_at)) - timedelta(
                minutes=FINAL_PREGAME_CUTOFF_MINUTES)
            for leg in legs if leg.starts_at
        ]
        return {
            "ticket_key": self._ticket_key(legs),
            "sportsbook": book,
            "legs": [leg.as_dict() for leg in legs],
            "price": price.as_dict(),
            "earliest_cutoff_utc": min(cutoffs).isoformat() if cutoffs else None,
            "games": sorted({leg.game_id for leg in legs}),
        }

    @staticmethod
    def _ticket_key(legs: list[Leg]) -> str:
        """A stable identity for one combination at one book.

        Sorted so the same three selections always produce the same key whatever
        order they were enumerated in -- this is what makes recording a ticket
        idempotent rather than order-dependent.
        """

        parts = sorted(
            f"{leg.game_id}:{leg.market}:{leg.selection}:{leg.sportsbook}"
            for leg in legs
        )
        return "|".join(parts)

    # -- featured ----------------------------------------------------------

    def _featured(self, tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Up to two tickets, and honestly fewer when two are not there.

        The two questions are different -- the likeliest ticket is usually the
        one with the worst price -- so they are selected independently and
        allowed to coincide. When they do, one entry carries both labels rather
        than the page showing the same ticket twice under two headings.
        """

        if not tickets:
            return []
        by_chance = max(
            tickets, key=lambda t: t["price"]["ticket_pays_probability_pct"])
        by_value = max(
            tickets, key=lambda t: t["price"]["estimated_net_return_per_unit"])

        if by_chance["ticket_key"] == by_value["ticket_key"]:
            return [{
                **by_chance,
                "feature_labels": [FEATURE_HIGHEST_CHANCE, FEATURE_BEST_VALUE],
                "feature_note": (
                    "The same combination leads on both measures. It is shown "
                    "once, not twice."
                ),
            }]
        return [
            {**by_chance, "feature_labels": [FEATURE_HIGHEST_CHANCE],
             "feature_note": (
                 "Highest estimated chance that the ticket returns more than the "
                 "stake. Not the best price.")},
            {**by_value, "feature_labels": [FEATURE_BEST_VALUE],
             "feature_note": (
                 "Best estimated value at the offered leg prices. Usually less "
                 "likely to win than the ticket above.")},
        ]

    def _inventory(self, legs: list[Leg]) -> dict[str, Any]:
        books: dict[str, int] = {}
        markets: dict[str, int] = {}
        for leg in legs:
            books[leg.sportsbook] = books.get(leg.sportsbook, 0) + 1
            markets[leg.market] = markets.get(leg.market, 0) + 1
        return {
            "by_sportsbook": dict(sorted(books.items())),
            "by_market": dict(sorted(markets.items())),
            "games": len({leg.game_id for leg in legs}),
        }

    # -- lookups -----------------------------------------------------------

    def _quotes(
        self, game_id: Any, market_type: MarketType, *, now: datetime,
    ) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(OddsSnapshot, MarketOutcome, Sportsbook)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .where(Market.game_id == game_id, Market.market_type == market_type)
            .order_by(OddsSnapshot.observed_at.desc())
        ).all()
        newest: dict[tuple[str, str], dict[str, Any]] = {}
        for snapshot, outcome, book in rows:
            key = (book.name, outcome.outcome_key)
            if key in newest:
                continue
            observed = _aware(snapshot.observed_at)
            age = (now - observed).total_seconds() / 60.0 if observed else None
            if age is None or age > FRESH_MINUTES:
                continue
            if snapshot.decimal_odds is None or snapshot.decimal_odds <= 1.0:
                continue
            newest[key] = {
                "sportsbook": book.name,
                "label": outcome.label,
                "outcome_key": outcome.outcome_key,
                "participant_team_id": (
                    str(outcome.participant_team_id)
                    if outcome.participant_team_id else None),
                "point": snapshot.point,
                "decimal_odds": float(snapshot.decimal_odds),
                "american_odds": snapshot.american_odds,
                "observed_at": observed.isoformat(),
                "quote_age_minutes": round(age, 1),
                "odds_snapshot_id": str(snapshot.id),
                "market_outcome_id": str(outcome.id),
            }
        return list(newest.values())

    def _mlb_games(self, event_date: date) -> list[Game]:
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        return list(self.session.scalars(
            select(Game)
            .join(League, League.id == Game.league_id)
            .where(League.code == "MLB", Game.starts_at >= start, Game.starts_at < end)
            .order_by(Game.starts_at)
        ))

    def _matchup(self, game_id: Any) -> tuple[str, dict[str, Any], dict[str, Any]]:
        rows = list(self.session.execute(
            select(GameTeam, Team)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game_id)
        ))
        def describe(is_home: bool) -> dict[str, Any]:
            for side, team in rows:
                if side.is_home is is_home:
                    return {"id": str(team.id), "abbreviation": team.abbreviation,
                            "name": team.name}
            return {"id": None, "abbreviation": "—", "name": "Unknown"}
        home, away = describe(True), describe(False)
        return f"{away['abbreviation']} @ {home['abbreviation']}", home, away


__all__ = [
    "FEATURE_BEST_VALUE",
    "FEATURE_HIGHEST_CHANCE",
    "MARKET_FILTERS",
    "MAX_TICKETS",
    "SCHEMA_VERSION",
    "MlbParlayReadService",
]
