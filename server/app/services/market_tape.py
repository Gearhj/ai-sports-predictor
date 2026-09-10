"""The market tape: one game's price movement, reconstructed from what was already stored.

WHY THIS IS A PROJECTION AND NOT A NEW TABLE
--------------------------------------------
Every price this returns is already in ``odds_snapshots``. A game we have
watched all day holds about a thousand of them, and the whole database holds a
quarter of a million; copying them into a second "tape" representation would
double the storage to add nothing, and would immediately introduce the worst
kind of bug -- two stores of the same prices that can disagree. So this is a
read model. It writes nothing, calls no provider, and spends no quota.

WHAT A FRAME IS
---------------
A frame is one instant at which at least one book published a price, together
with EVERY book's price as of that instant. Books do not tick together, so the
alternative -- showing only the book that moved -- would make a nine-book
market look like a one-book market at every step. A price stands until the book
changes it, so carrying it forward is what the market actually looked like;
each carried price keeps the timestamp it was really observed at, and a frame
records how old each book's quote was when the frame was taken.

THE PAIRING RULE, WHICH IS THE WHOLE POINT
------------------------------------------
A usable quote is TWO OUTCOMES OF ONE MARKET AT ONE BOOK. Never two books,
never two markets, and never two providers -- The Odds API's "betmgm" and the
SportsGameOdds fallback's "sgo:betmgm" are different rows, different providers
and are never paired with each other, because a no-vig probability derived from
one provider's over and another's under is not a price anyone could take. The
same rule the decision policy uses is the rule here, so a probability shown on
the replay is the probability the policy would have seen.

A pair also has a SKEW: its two sides were observed at slightly different
instants. Beyond the freshness window that pair is reported, but not counted
as usable -- which is the same distinction ``quote_coverage`` draws between a
quote existing and a quote being usable through the decision cutoff.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import fmean, pstdev
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.forecast_exchange.pricing import implied_probability, no_vig_two_sided
from app.models import (
    Forecast, Game, Market, MarketOutcome, OddsSnapshot, Sportsbook, Team,
)
from app.services.quote_coverage import (
    DECISION_LATENCY_MINUTES, FINAL_PREGAME_CUTOFF_MINUTES, MAX_QUOTE_STALENESS_MINUTES,
)

SCHEMA_VERSION = "AISP_MARKET_TAPE_V1"

#: The primary provider, and the fallback that is explicitly temporary. These
#: are provenance labels, never merged and never averaged together.
PRIMARY_PROVIDER = "THE_ODDS_API"
FALLBACK_PROVIDER = "SPORTSGAMEODDS"

#: A tape longer than this is thinned for transport. Thinning DROPS frames; it
#: never interpolates one, and the first, last and cutoff-adjacent frames are
#: always kept so the endpoints a reader compares are real observations.
MAX_FRAMES = 220

MARKET_TYPES = ("MONEYLINE", "SPREAD", "TOTAL")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    moment = _aware(value)
    return moment.isoformat() if moment else None


def provider_of(book: Sportsbook) -> str:
    """The provider a book's prices arrived through, from its own key."""

    return FALLBACK_PROVIDER if str(book.key or "").startswith("sgo:") else PRIMARY_PROVIDER


def display_name(book: Sportsbook) -> str:
    """The book, without the provenance parenthetical the badge already carries."""

    name = str(book.name or "")
    cut = name.find(" (")
    return name[:cut].strip() if cut > 0 else name


@dataclass(frozen=True)
class Observation:
    at: datetime
    american: int
    point: float | None
    snapshot_id: str
    source: str


@dataclass(frozen=True)
class PairedQuote:
    """One book's complete two-sided quote at one instant."""

    book_id: str
    a: Observation
    b: Observation

    @property
    def observed_at(self) -> datetime:
        return max(self.a.at, self.b.at)

    @property
    def skew_seconds(self) -> int:
        return int(abs((self.a.at - self.b.at).total_seconds()))

    def no_vig_a(self) -> float:
        return no_vig_two_sided(implied_probability(self.a.american), implied_probability(self.b.american))


class MarketTapeService:
    """Reconstructs one game's price movement. Read-only, no provider calls."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- public -----------------------------------------------------------
    def tape(
        self, *, game_id: UUID, market_type: str = "MONEYLINE", now: datetime | None = None,
    ) -> dict[str, Any]:
        now = _aware(now) or datetime.now(UTC)
        market_type = market_type.upper()
        if market_type not in MARKET_TYPES:
            raise ValueError(f"unsupported market type {market_type!r}")

        game = self.session.get(Game, game_id)
        if game is None:
            return self._empty(game_id, market_type, now, "This game is not registered.")

        starts_at = _aware(game.starts_at)
        cutoff = starts_at - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES) if starts_at else None

        rows = list(self.session.execute(
            select(OddsSnapshot, MarketOutcome, Market, Sportsbook)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .where(Market.game_id == game_id, Market.market_type == market_type)
            .order_by(OddsSnapshot.observed_at)
        ).all())
        if not rows:
            return self._empty(
                game_id, market_type, now,
                f"No {market_type.lower()} price has been persisted for this game.",
                game=game, cutoff=cutoff,
            )

        # Outcomes are per market; a market with anything other than two
        # outcomes cannot form the two-sided pair the policy prices from.
        by_market: dict[Any, set[Any]] = defaultdict(set)
        outcomes: dict[Any, MarketOutcome] = {}
        books: dict[Any, Sportsbook] = {}
        for _, outcome, market, book in rows:
            by_market[market.id].add(outcome.id)
            outcomes[outcome.id] = outcome
            books[book.id] = book
        market_id = max(by_market, key=lambda key: len(by_market[key]))
        pair_ids = sorted(by_market[market_id], key=lambda key: str(outcomes[key].outcome_key))
        if len(pair_ids) != 2:
            return self._empty(
                game_id, market_type, now,
                f"This {market_type.lower()} market has {len(pair_ids)} persisted outcome(s); "
                "a comparable price needs exactly two sides.",
                game=game, cutoff=cutoff,
            )
        side_a, side_b = pair_ids

        # side -> book -> ordered observations
        series: dict[Any, dict[Any, list[Observation]]] = {side_a: defaultdict(list), side_b: defaultdict(list)}
        instants: set[datetime] = set()
        for snapshot, outcome, market, book in rows:
            if market.id != market_id or outcome.id not in series:
                continue
            at = _aware(snapshot.observed_at)
            if at is None or snapshot.american_odds is None:
                continue
            series[outcome.id][book.id].append(Observation(
                at=at, american=int(snapshot.american_odds),
                point=float(snapshot.point) if snapshot.point is not None else None,
                snapshot_id=str(snapshot.id), source=str(snapshot.source or ""),
            ))
            instants.add(at)

        ordered = sorted(instants)
        kept, sampled = self._thin(ordered, cutoff)
        home, away = self._teams(game)
        home_side = self._home_side(outcomes[side_a], outcomes[side_b], home, away, market_type)

        built = [self._frame(at, series, side_a, side_b, home_side, books, cutoff) for at in kept]
        # Per-book prices ship as change points, not once per frame: a book that
        # has not moved in six hours is one entry, not two hundred identical
        # ones. The client forward-fills exactly as this service does, and every
        # entry keeps its own real observation time.
        book_series = self._book_series(built)
        frames = [{key: value for key, value in frame.items() if key != "books"} for frame in built]
        model = self._model_series(game_id)

        return {
            "schema_version": SCHEMA_VERSION,
            "data_mode": "live",
            "generated_at_utc": _iso(now),
            "game_id": str(game_id),
            "market": {
                "market_type": market_type,
                "period": "full_game",
                "side_a": self._side_label(outcomes[side_a], home, away),
                "side_b": self._side_label(outcomes[side_b], home, away),
                "home_side": home_side,
                "probability_meaning": (
                    "Two-sided no-vig probability of the home selection at one book"
                    if market_type == "MONEYLINE"
                    else "Two-sided no-vig probability of side A at one book, at that book's own line"
                ),
            },
            "event": {
                "home": home, "away": away,
                "starts_at_utc": _iso(starts_at),
                "decision_cutoff_utc": _iso(cutoff),
                "status": str(getattr(game.status, "value", game.status) or ""),
            },
            "providers": self._providers(books, rows),
            "books": [
                {
                    "sportsbook_id": str(book_id),
                    "name": display_name(book),
                    "key": book.key,
                    "provider": provider_of(book),
                    "observations": sum(len(series[side][book_id]) for side in (side_a, side_b)),
                    "series": book_series.get(str(book_id), []),
                }
                for book_id, book in sorted(books.items(), key=lambda item: display_name(item[1]))
                if book_id in series[side_a] or book_id in series[side_b]
            ],
            "contract": {
                "pairing": "same sportsbook, same market, exactly two outcomes; providers never mixed",
                "freshness_window_minutes": MAX_QUOTE_STALENESS_MINUTES,
                "decision_latency_minutes": DECISION_LATENCY_MINUTES,
                "cutoff_minutes_before_start": FINAL_PREGAME_CUTOFF_MINUTES,
                "carry_forward": "a book's price stands until that book changes it; carried "
                                 "prices keep their real observation time",
            },
            "frames": frames,
            "frame_count": len(frames),
            "observed_instants": len(ordered),
            "frames_sampled": sampled,
            "first_observed_utc": _iso(ordered[0]) if ordered else None,
            "last_observed_utc": _iso(ordered[-1]) if ordered else None,
            "model_probability_series": model,
            "milestones": self._milestones(frames, cutoff),
            "requirement": None,
        }

    # -- internals --------------------------------------------------------
    def _empty(
        self, game_id: UUID, market_type: str, now: datetime, requirement: str,
        *, game: Game | None = None, cutoff: datetime | None = None,
    ) -> dict[str, Any]:
        home, away = self._teams(game) if game is not None else (None, None)
        return {
            "schema_version": SCHEMA_VERSION, "data_mode": "live",
            "generated_at_utc": _iso(now), "game_id": str(game_id),
            "market": {"market_type": market_type, "period": "full_game",
                       "side_a": None, "side_b": None, "home_side": None,
                       "probability_meaning": None},
            "event": {
                "home": home, "away": away,
                "starts_at_utc": _iso(_aware(game.starts_at)) if game is not None else None,
                "decision_cutoff_utc": _iso(cutoff),
                "status": str(getattr(game.status, "value", game.status) or "") if game is not None else "",
            },
            "providers": {"primary": PRIMARY_PROVIDER, "fallback": FALLBACK_PROVIDER, "present": []},
            "books": [], "contract": {}, "frames": [], "frame_count": 0,
            "observed_instants": 0, "frames_sampled": False,
            "first_observed_utc": None, "last_observed_utc": None,
            "model_probability_series": [], "milestones": {},
            "requirement": requirement,
        }

    @staticmethod
    def _thin(instants: list[datetime], cutoff: datetime | None) -> tuple[list[datetime], bool]:
        if len(instants) <= MAX_FRAMES:
            return instants, False
        step = len(instants) / MAX_FRAMES
        kept = {instants[min(len(instants) - 1, int(index * step))] for index in range(MAX_FRAMES)}
        kept.add(instants[0])
        kept.add(instants[-1])
        if cutoff is not None:
            before = [at for at in instants if at <= cutoff]
            if before:
                kept.add(before[-1])
        return sorted(kept), True

    def _teams(self, game: Game) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        from app.models import GameTeam

        rows = list(self.session.execute(
            select(GameTeam, Team).join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game.id)
        ).all())
        sides: dict[str, dict[str, Any]] = {}
        for link, team in rows:
            sides["HOME" if link.is_home else "AWAY"] = {
                "team_id": str(team.id), "abbreviation": team.abbreviation, "name": team.name,
            }
        return sides.get("HOME"), sides.get("AWAY")

    @staticmethod
    def _side_label(outcome: MarketOutcome, home: dict | None, away: dict | None) -> dict[str, Any]:
        return {
            "market_outcome_id": str(outcome.id),
            "outcome_key": outcome.outcome_key,
            "label": outcome.label,
        }

    @staticmethod
    def _home_side(
        outcome_a: MarketOutcome, outcome_b: MarketOutcome,
        home: dict | None, away: dict | None, market_type: str,
    ) -> str | None:
        """Which stored outcome is the home selection, by declared identity only.

        Never guessed from a price. A total has no home side at all, and saying
        it did would put a run count on a win-probability axis.
        """

        if market_type == "TOTAL" or home is None:
            return None
        for key, outcome in (("a", outcome_a), ("b", outcome_b)):
            if outcome.participant_team_id is not None and str(outcome.participant_team_id) == home["team_id"]:
                return key
            label = str(outcome.label or "").strip().lower()
            if label and label == str(home["name"]).strip().lower():
                return key
            if str(outcome.outcome_key or "").upper().startswith("HOME"):
                return key
        return None

    def _frame(
        self, at: datetime,
        series: dict[Any, dict[Any, list[Observation]]],
        side_a: Any, side_b: Any, home_side: str | None,
        books: dict[Any, Sportsbook], cutoff: datetime | None,
    ) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        usable: list[PairedQuote] = []
        for book_id in books:
            a = _latest_at(series[side_a].get(book_id, []), at)
            b = _latest_at(series[side_b].get(book_id, []), at)
            if a is None or b is None:
                continue
            pair = PairedQuote(book_id=str(book_id), a=a, b=b)
            age = (at - pair.observed_at).total_seconds() / 60.0
            fresh = age <= MAX_QUOTE_STALENESS_MINUTES
            skew_ok = pair.skew_seconds <= MAX_QUOTE_STALENESS_MINUTES * 60
            entries.append({
                "sportsbook_id": str(book_id),
                "provider": provider_of(books[book_id]),
                "a": a.american, "b": b.american,
                "point_a": a.point, "point_b": b.point,
                "observed_at_utc": _iso(pair.observed_at),
                "age_minutes": round(age, 1),
                "skew_seconds": pair.skew_seconds,
                "usable": bool(fresh and skew_ok),
                "no_vig_a": round(pair.no_vig_a(), 6),
            })
            if fresh and skew_ok:
                usable.append(pair)

        probabilities = [pair.no_vig_a() for pair in usable]
        consensus = fmean(probabilities) if probabilities else None
        # "Best" is best for the bettor: the longest price on each side.
        best_a = max(usable, key=lambda pair: pair.a.american, default=None)
        best_b = max(usable, key=lambda pair: pair.b.american, default=None)
        return {
            "t": _iso(at),
            "before_cutoff": None if cutoff is None else at <= cutoff,
            "books": entries,
            "usable_books": len(usable),
            "consensus_no_vig_a": None if consensus is None else round(consensus, 6),
            "consensus_no_vig_home": (
                None if consensus is None or home_side is None
                else round(consensus if home_side == "a" else 1 - consensus, 6)
            ),
            "dispersion": None if len(probabilities) < 2 else round(pstdev(probabilities), 6),
            "range_a": (
                None if not probabilities
                else [round(min(probabilities), 6), round(max(probabilities), 6)]
            ),
            "best_a": None if best_a is None else {"american": best_a.a.american, "sportsbook_id": best_a.book_id},
            "best_b": None if best_b is None else {"american": best_b.b.american, "sportsbook_id": best_b.book_id},
        }


    @staticmethod
    def _book_series(frames: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """One entry per book per CHANGE, not per frame.

        Dropping the repeats is lossless because a price stands until the book
        moves it: the reader forward-fills and gets exactly the frame-by-frame
        series back, at a twentieth of the bytes.
        """

        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        previous: dict[str, tuple[Any, ...]] = {}
        for frame in frames:
            for entry in frame["books"]:
                book_id = entry["sportsbook_id"]
                signature = (entry["a"], entry["b"], entry["usable"], entry["point_a"], entry["point_b"])
                if previous.get(book_id) == signature:
                    continue
                previous[book_id] = signature
                out[book_id].append({
                    "t": frame["t"],
                    "a": entry["a"], "b": entry["b"],
                    "point_a": entry["point_a"], "point_b": entry["point_b"],
                    "observed_at_utc": entry["observed_at_utc"],
                    "usable": entry["usable"],
                    "skew_seconds": entry["skew_seconds"],
                    "no_vig_a": entry["no_vig_a"],
                })
        return dict(out)

    def _model_series(self, game_id: UUID) -> list[dict[str, Any]]:
        """Every published forecast for this game, as a step series.

        This is the model's own history, not a market observation, and the
        replay draws it as a separate line for exactly that reason.
        """

        rows = list(self.session.execute(
            select(Forecast.version, Forecast.generated_at, Forecast.available_as_of,
                   Forecast.home_win_probability, Forecast.status, Forecast.lifecycle)
            .where(Forecast.game_id == game_id)
            .order_by(Forecast.generated_at)
        ).all())
        return [
            {
                "version": version, "generated_at_utc": _iso(generated_at),
                "available_as_of_utc": _iso(available_as_of),
                "home_win_probability": None if probability is None else round(float(probability), 6),
                "status": str(getattr(status, "value", status) or ""),
                "lifecycle": str(getattr(lifecycle, "value", lifecycle) or ""),
            }
            for version, generated_at, available_as_of, probability, status, lifecycle in rows
            if probability is not None
        ]

    @staticmethod
    def _providers(books: dict[Any, Sportsbook], rows: list[Any]) -> dict[str, Any]:
        from app.data_sources.provider_registry import GOVERNED_FALLBACK_UNTIL

        present: dict[str, int] = defaultdict(int)
        for book_id, book in books.items():
            present[provider_of(book)] += 1
        return {
            "primary": PRIMARY_PROVIDER,
            "fallback": FALLBACK_PROVIDER,
            "fallback_is_temporary": True,
            "fallback_until": GOVERNED_FALLBACK_UNTIL.isoformat(),
            "present": [{"provider": name, "books": count} for name, count in sorted(present.items())],
            "note": "A pair is always one book at one provider. The Odds API and the "
                    "SportsGameOdds fallback are never combined into one quote.",
        }

    @staticmethod
    def _milestones(frames: list[dict[str, Any]], cutoff: datetime | None) -> dict[str, Any]:
        """Opening, latest and last-before-cutoff, with the movement between them."""

        priced = [frame for frame in frames if frame["consensus_no_vig_a"] is not None]
        if not priced:
            return {"opening": None, "latest": None, "at_cutoff": None, "movement": None}
        opening, latest = priced[0], priced[-1]
        at_cutoff = next(
            (frame for frame in reversed(priced) if frame["before_cutoff"]), None,
        ) if cutoff is not None else None
        span_hours = max(
            (datetime.fromisoformat(latest["t"]) - datetime.fromisoformat(opening["t"])).total_seconds() / 3600.0,
            1e-9,
        )
        move = latest["consensus_no_vig_a"] - opening["consensus_no_vig_a"]
        return {
            "opening": {"t": opening["t"], "consensus_no_vig_a": opening["consensus_no_vig_a"],
                        "usable_books": opening["usable_books"]},
            "latest": {"t": latest["t"], "consensus_no_vig_a": latest["consensus_no_vig_a"],
                       "usable_books": latest["usable_books"]},
            "at_cutoff": None if at_cutoff is None else {
                "t": at_cutoff["t"], "consensus_no_vig_a": at_cutoff["consensus_no_vig_a"],
                "usable_books": at_cutoff["usable_books"],
            },
            "movement": {
                "points": round(move * 100, 4),
                "velocity_points_per_hour": round((move * 100) / span_hours, 4),
                "hours_observed": round(span_hours, 3),
            },
        }


def _latest_at(observations: list[Observation], at: datetime) -> Observation | None:
    """The newest observation at or before ``at``; None before a book's first."""

    chosen: Observation | None = None
    for observation in observations:
        if observation.at > at:
            break
        chosen = observation
    return chosen


__all__ = [
    "FALLBACK_PROVIDER", "MAX_FRAMES", "MARKET_TYPES", "MarketTapeService",
    "Observation", "PRIMARY_PROVIDER", "PairedQuote", "SCHEMA_VERSION",
    "display_name", "provider_of",
]
