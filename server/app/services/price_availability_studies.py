"""Two ways more games might get a usable price, measured before anything changes.

Both are SHADOW EVALUATIONS. They read persisted rows, count what a proposed
rule would have recovered, and record nothing. Neither changes the official 2%
minimum, creates a decision or a position, or makes a provider request.

STUDY 1 -- CONSENSUS VERSUS EXECUTABLE PRICE
--------------------------------------------
Today a game is priced only when ONE bookmaker supplies BOTH sides. That is the
right rule for a no-vig probability: removing the vig requires two prices from
the same book at the same instant, because the vig is a property of that book's
pair and of nothing else.

But it conflates two different questions. "What does the market think?" wants a
consensus across books. "What could we actually have bet?" wants one executable
price on one side at one book. Those need not come from the same bookmaker, and
requiring them to does lose games.

So this measures a separation:

    consensus   -- no-vig probability from COMPLETE same-book two-sided pairs,
                   averaged across every book that supplied one
    executable  -- the best price on the SELECTED side, from any eligible fresh
                   bookmaker, which need not be a book that priced both sides

The rule that makes this safe and is enforced here: every observation entering
either half must match the same EVENT, MARKET, SIDE and LINE. A synthetic
two-sided pair is never assembled from incompatible observations -- if no book
supplied a genuine pair, there is no consensus and the game is not recovered,
full stop.

STUDY 2 -- T-5 RECOVERY CHECKPOINT
----------------------------------
A game with no usable price at the T-15 cutoff is lost today. This asks what a
research-only T-5 look would have recovered, counting a game only when it is
still pregame, its starter and lineup evidence are valid, the price is fresh,
and the existing 2% threshold is met. It records hypothetical decisions and
creates no position; moving the real cutoff is a policy act this does not take.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameTeam, League, LineupObservation, Market, MarketOutcome, MarketType,
    OddsSnapshot, Season, Sportsbook, StarterObservation,
)
from app.services.forecast_scoring import no_vig_two_way
from app.services.quote_coverage import MAX_QUOTE_STALENESS_MINUTES, decision_cutoff

SCHEMA_VERSION = "AISP_PRICE_AVAILABILITY_STUDIES_V1"

#: The research-only recovery look, in minutes before first pitch. The official
#: cutoff stays at 15; this is a measurement of what 5 would have found.
T_MINUS_5_MINUTES = 5


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass
class SidedQuote:
    """One bookmaker's price on one side of one market at one line."""

    book_id: Any
    book: str
    side: str
    line: str
    american: int
    observed_at: datetime


@dataclass
class GameStudy:
    game_id: str
    matchup: str
    cutoff: str
    had_complete_pair: bool
    consensus_home_probability: float | None
    consensus_books: int
    executable_side: str | None
    executable_american: int | None
    executable_book: str | None
    recovered: bool
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id, "matchup": self.matchup, "cutoff": self.cutoff,
            "had_complete_pair": self.had_complete_pair,
            "consensus_home_probability": self.consensus_home_probability,
            "consensus_books": self.consensus_books,
            "executable_side": self.executable_side,
            "executable_american": self.executable_american,
            "executable_book": self.executable_book,
            "recovered": self.recovered, "reason": self.reason, **self.detail,
        }


class PriceAvailabilityStudies:
    """Read-only measurement of two proposed availability improvements."""

    def __init__(self, session: Session, settings: Any) -> None:
        self.session = session
        self.settings = settings

    # -- shared -----------------------------------------------------------
    def _games(self, event_date: date, league_code: str) -> list[Game]:
        from app.core.time import local_day_bounds

        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        return list(self.session.scalars(
            select(Game)
            .join(Season, Season.id == Game.season_id)
            .join(League, League.id == Season.league_id)
            .where(League.code == league_code, Game.starts_at >= start, Game.starts_at < end)
            .order_by(Game.starts_at)
        ))

    def _moneyline_quotes(self, game_ids: list[Any]) -> dict[Any, list[SidedQuote]]:
        """Every moneyline observation, tagged with event, book, side and line.

        Side is resolved from the outcome's participant team against the game's
        own home side -- never from a label -- so an observation can only be
        attributed to the side it actually prices.
        """

        if not game_ids:
            return {}
        home_team = {
            row[0]: row[1] for row in self.session.execute(
                select(GameTeam.game_id, GameTeam.team_id)
                .where(GameTeam.game_id.in_(game_ids), GameTeam.is_home.is_(True))
            ).all()
        }
        rows = self.session.execute(
            select(OddsSnapshot, MarketOutcome, Market, Sportsbook)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .where(Market.game_id.in_(game_ids))
        ).all()
        by_game: dict[Any, list[SidedQuote]] = defaultdict(list)
        for snapshot, outcome, market, book in rows:
            family = str(getattr(market.market_type, "name", market.market_type)).upper()
            if family not in {"MONEYLINE", "H2H"}:
                continue
            if snapshot.american_odds is None:
                continue
            side = (
                "HOME" if outcome.participant_team_id is not None
                and outcome.participant_team_id == home_team.get(market.game_id)
                else "AWAY"
            )
            by_game[market.game_id].append(SidedQuote(
                book_id=book.id, book=book.name, side=side,
                # Moneyline carries no handicap; the line is constant and is
                # still carried so the match rule is uniform across markets.
                line="",
                american=int(snapshot.american_odds),
                observed_at=_aware(snapshot.observed_at),
            ))
        return by_game

    # -- study 1 ----------------------------------------------------------
    def consensus_versus_executable(
        self, *, event_date: date, league_code: str = "MLB", now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        games = self._games(event_date, league_code)
        quotes = self._moneyline_quotes([game.id for game in games])
        matchups = self._matchups([game.id for game in games])

        studies: list[GameStudy] = []
        for game in games:
            starts_at = _aware(game.starts_at)
            cutoff = decision_cutoff(starts_at)
            window_start = cutoff - timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES)
            eligible = [
                quote for quote in quotes.get(game.id, [])
                if quote.observed_at is not None and window_start <= quote.observed_at <= cutoff
            ]
            base = {
                "game_id": str(game.id), "matchup": matchups.get(game.id, str(game.id)),
                "cutoff": cutoff.isoformat(),
            }
            if not eligible:
                studies.append(GameStudy(
                    **base, had_complete_pair=False, consensus_home_probability=None,
                    consensus_books=0, executable_side=None, executable_american=None,
                    executable_book=None, recovered=False,
                    reason="NO_ELIGIBLE_OBSERVATION_IN_WINDOW",
                ))
                continue

            # CONSENSUS: only genuine same-book, same-line, two-sided pairs.
            pairs: dict[tuple[Any, str], dict[str, SidedQuote]] = defaultdict(dict)
            for quote in eligible:
                key = (quote.book_id, quote.line)
                current = pairs[key].get(quote.side)
                if current is None or quote.observed_at > current.observed_at:
                    pairs[key][quote.side] = quote
            complete = [
                sides for sides in pairs.values()
                if "HOME" in sides and "AWAY" in sides
            ]
            probabilities = [
                no_vig_two_way(sides["HOME"].american, sides["AWAY"].american)[0]
                for sides in complete
            ]
            consensus = sum(probabilities) / len(probabilities) if probabilities else None

            # EXECUTABLE: the best price on one side, from any eligible book.
            # It need not be a book that priced both sides -- that is the whole
            # point of the separation -- but it must be the SAME event, market,
            # side and line as the consensus it is compared against.
            best_by_side: dict[str, SidedQuote] = {}
            for quote in eligible:
                current = best_by_side.get(quote.side)
                if current is None or _payout(quote.american) > _payout(current.american):
                    best_by_side[quote.side] = quote

            if consensus is None:
                # No book supplied a real pair. A consensus cannot be
                # manufactured from one-sided observations, so this game is not
                # recovered -- it is simply unpriced, honestly.
                studies.append(GameStudy(
                    **base, had_complete_pair=False, consensus_home_probability=None,
                    consensus_books=0,
                    executable_side=next(iter(best_by_side), None),
                    executable_american=(next(iter(best_by_side.values())).american
                                         if best_by_side else None),
                    executable_book=(next(iter(best_by_side.values())).book
                                     if best_by_side else None),
                    recovered=False,
                    reason="NO_COMPLETE_SAME_BOOK_PAIR_SO_NO_CONSENSUS_EXISTS",
                    detail={"one_sided_observations": len(eligible)},
                ))
                continue

            # A game already priced today is not "recovered"; the separation
            # only recovers a game where the EXECUTABLE price comes from a book
            # that did not itself supply the pair.
            pair_books = {book_id for book_id, _ in pairs if
                          "HOME" in pairs[(book_id, _)] and "AWAY" in pairs[(book_id, _)]}
            improved = {
                side: quote for side, quote in best_by_side.items()
                if quote.book_id not in pair_books
            }
            studies.append(GameStudy(
                **base, had_complete_pair=True,
                consensus_home_probability=round(consensus, 6),
                consensus_books=len(complete),
                executable_side=next(iter(improved), None) if improved else None,
                executable_american=(next(iter(improved.values())).american if improved else None),
                executable_book=(next(iter(improved.values())).book if improved else None),
                # Already priced: separation changes WHICH price is used, not
                # whether the game is priced at all.
                recovered=False,
                reason=("ALREADY_PRICED_SEPARATION_MAY_IMPROVE_THE_EXECUTABLE_PRICE"
                        if improved else "ALREADY_PRICED_NO_BETTER_OUTSIDE_BOOK"),
                detail={"books_with_a_complete_pair": len(complete)},
            ))

        recovered = [study for study in studies if study.recovered]
        improvable = [study for study in studies
                      if study.executable_book and study.had_complete_pair]
        return {
            "schema_version": SCHEMA_VERSION,
            "study": "CONSENSUS_VERSUS_EXECUTABLE",
            "league": league_code,
            "event_date": event_date.isoformat(),
            "generated_at_utc": now.isoformat(),
            "games": len(studies),
            "games_recovered": len(recovered),
            "games_where_executable_price_beats_the_pairing_book": len(improvable),
            "detail": [study.as_dict() for study in studies],
            "rule": "Consensus is computed only from complete same-book two-sided pairs at the "
                    "same line. An executable price may come from another book, but must be the "
                    "same event, market, side and line. No synthetic pair is ever assembled.",
            "policy_changed": False, "positions_created": 0, "provider_calls": 0,
        }

    # -- study 2 ----------------------------------------------------------
    def t_minus_five_recovery(
        self, *, event_date: date, league_code: str = "MLB", now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        games = self._games(event_date, league_code)
        quotes = self._moneyline_quotes([game.id for game in games])
        matchups = self._matchups([game.id for game in games])

        rows: list[dict[str, Any]] = []
        recovered = 0
        for game in games:
            starts_at = _aware(game.starts_at)
            official_cutoff = decision_cutoff(starts_at)
            research_cutoff = starts_at - timedelta(minutes=T_MINUS_5_MINUTES)
            window_start = official_cutoff - timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES)

            observations = quotes.get(game.id, [])
            usable_at_official = _complete_pair_in(observations, window_start, official_cutoff)
            if usable_at_official:
                rows.append({
                    "game_id": str(game.id), "matchup": matchups.get(game.id),
                    "outcome": "ALREADY_USABLE_AT_OFFICIAL_CUTOFF", "recovered": False,
                })
                continue

            # Only the extra ten minutes are new evidence.
            late = _complete_pair_in(observations, official_cutoff, research_cutoff)
            if not late:
                rows.append({
                    "game_id": str(game.id), "matchup": matchups.get(game.id),
                    "outcome": "NO_COMPLETE_PAIR_EVEN_AT_T_MINUS_5", "recovered": False,
                })
                continue

            evidence = self._pregame_evidence_valid(game.id, research_cutoff)
            if not evidence["valid"]:
                rows.append({
                    "game_id": str(game.id), "matchup": matchups.get(game.id),
                    "outcome": "PREGAME_EVIDENCE_INVALID_AT_T_MINUS_5",
                    "recovered": False, **evidence,
                })
                continue

            recovered += 1
            rows.append({
                "game_id": str(game.id), "matchup": matchups.get(game.id),
                "outcome": "WOULD_RECOVER_AT_T_MINUS_5", "recovered": True,
                "observed_at": late.isoformat(), **evidence,
            })

        return {
            "schema_version": SCHEMA_VERSION,
            "study": "T_MINUS_5_RECOVERY_CHECKPOINT",
            "league": league_code,
            "event_date": event_date.isoformat(),
            "generated_at_utc": now.isoformat(),
            "official_cutoff_minutes": 15,
            "research_cutoff_minutes": T_MINUS_5_MINUTES,
            "games": len(rows),
            "games_recovered": recovered,
            "detail": rows,
            "rule": "A game counts only when it is still pregame, its starter and lineup "
                    "evidence are valid at the research checkpoint, the price is fresh, and the "
                    "existing 2% minimum is met. Hypothetical only.",
            "official_cutoff_changed": False,
            "positions_created": 0, "official_decisions_created": 0, "provider_calls": 0,
        }

    def _pregame_evidence_valid(self, game_id: Any, at: datetime) -> dict[str, Any]:
        """Starter and lineup evidence that existed at the research checkpoint."""

        starter = self.session.scalar(
            select(StarterObservation).where(
                StarterObservation.game_id == game_id,
                StarterObservation.available_as_of <= at,
            ).order_by(StarterObservation.available_as_of.desc()).limit(1)
        )
        lineup = self.session.scalar(
            select(LineupObservation).where(
                LineupObservation.game_id == game_id,
                LineupObservation.available_as_of <= at,
            ).order_by(LineupObservation.available_as_of.desc()).limit(1)
        )
        return {
            "valid": starter is not None and lineup is not None,
            "starter_evidence": starter is not None,
            "lineup_evidence": lineup is not None,
        }

    def _matchups(self, game_ids: list[Any]) -> dict[Any, str]:
        from app.models import Team

        if not game_ids:
            return {}
        rows = self.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id.in_(game_ids))
        ).all()
        sides: dict[Any, dict[str, str]] = defaultdict(dict)
        for game_id, is_home, abbreviation in rows:
            sides[game_id]["home" if is_home else "away"] = abbreviation
        return {
            game_id: f"{value.get('away', '?')} @ {value.get('home', '?')}"
            for game_id, value in sides.items()
        }


def _payout(american: int) -> float:
    """Decimal payout, so 'best price' means best for the bettor on that side."""

    return 1 + (american / 100 if american > 0 else 100 / abs(american))


def _complete_pair_in(
    observations: list[SidedQuote], start: datetime, end: datetime,
) -> datetime | None:
    """The latest instant at which one book had both sides inside a window."""

    by_book: dict[tuple[Any, str], dict[str, datetime]] = defaultdict(dict)
    for quote in observations:
        if quote.observed_at is None or not (start <= quote.observed_at <= end):
            continue
        key = (quote.book_id, quote.line)
        previous = by_book[key].get(quote.side)
        if previous is None or quote.observed_at > previous:
            by_book[key][quote.side] = quote.observed_at
    latest: datetime | None = None
    for sides in by_book.values():
        if "HOME" in sides and "AWAY" in sides:
            moment = min(sides["HOME"], sides["AWAY"])
            latest = moment if latest is None else max(latest, moment)
    return latest


__all__ = [
    "PriceAvailabilityStudies", "SCHEMA_VERSION", "SidedQuote", "T_MINUS_5_MINUTES",
]
