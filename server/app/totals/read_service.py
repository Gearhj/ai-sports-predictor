"""The MLB totals board: V18's own distribution beside whatever the book quoted.

WHAT THIS PAGE HAS TO KEEP APART
--------------------------------
*   **A model probability and an offered price.** The probabilities come from
    V18's retained total-runs distribution. The prices come from a sportsbook
    observation with its own clock. They are different objects with different
    provenance and they never merge into one "value" number without both being
    visible.
*   **A recorded sportsbook line and a line the reader typed.** A scenario line
    is genuinely useful -- it is the only way to interrogate the distribution
    when no book quote is held -- but it is labelled ``SCENARIO`` everywhere and
    it carries no price, because there is no price to carry.
*   **Absent evidence and zero.** No stored line means the row says so and names
    what is missing. It never renders 0.0, and it never silently borrows
    yesterday's line.

WHICH FORECAST
--------------
``ForecastSelector``, the same one ``/games`` and ``/markets`` already use, so
this page cannot disagree with them about which forecast a game has. When V18 is
missing, superseded or invalidated, the row says which and prices nothing.
Gen 1 is never substituted: it has no comparable retained total-runs mass
function, and quietly pricing a total off a different engine would attribute
V18's authority to numbers it did not produce.

The score-limited research candidate is not eligible here. It conditions on
games staying inside published scoring limits, which is a different question
from what an unrestricted Over is asking.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
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
from app.explain.opportunity import explain_totals_opportunity
from app.services.forecast_selection import Availability, ForecastSelector
from app.totals.index_attachment import CONSUMER_FIELD, attachment_map
from app.totals.pricing import (
    PROBABILITY_UNITS,
    TotalsDistribution,
    TotalsPricingError,
    TotalsProbabilities,
    american_from_decimal,
    expected_net_return,
    fair_decimal_odds,
    price_line,
)
from app.core.time import local_day_bounds

SCHEMA_VERSION = "AISP_MLB_TOTALS_BOARD_V1"

#: A quote older than this is shown with its age and excluded from anything that
#: would read as a current offer. Matches the board's own freshness convention.
FRESH_MINUTES = 240

#: Why a game cannot be priced. Each names the missing thing exactly.
NO_FORECAST = "NO_V18_FORECAST"
FORECAST_NOT_CURRENT = "V18_NOT_CURRENT"
NO_DISTRIBUTION = "NO_TOTAL_RUNS_DISTRIBUTION"
NO_MARKET_LINE = "NO_RECORDED_TOTAL_LINE"

LINE_RECORDED = "RECORDED_SPORTSBOOK_LINE"
LINE_SCENARIO = "USER_SELECTED_SCENARIO"

#: Whether a published totals index has verified this exact forecast's PMF.
#:
#: Advisory, never a gate. A forecast published after an index's inventory
#: freeze is the same engine writing the same field through the same importer;
#: withholding it because an earlier snapshot predates it would hide a current
#: forecast behind an expired inventory. The board prices either way and says
#: which is which.
VERIFIED_BY_INDEX = "VERIFIED_AGAINST_PUBLISHED_INDEX"
NOT_IN_INDEX = "NOT_COVERED_BY_A_PUBLISHED_INDEX"


@dataclass(frozen=True)
class _Quote:
    book: str
    side: str
    line: float
    decimal_odds: float
    american_odds: int | None
    observed_at: datetime
    snapshot_id: str
    market_outcome_id: str


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _age_minutes(observed_at: datetime | None, now: datetime) -> float | None:
    observed = _aware(observed_at)
    if observed is None:
        return None
    return round((now - observed).total_seconds() / 60.0, 1)


class MlbTotalsReadService:
    """Builds the totals board for one slate date."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.selector = ForecastSelector(session)

    # -- public ------------------------------------------------------------

    def board(
        self,
        event_date: date,
        *,
        overs_only: bool = False,
        scenario_lines: dict[str, float] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        scenarios = scenario_lines or {}
        games = self._mlb_games(event_date)
        # One query for the whole board. Per row it would be fifteen extra round
        # trips for a field that only decorates each one.
        attachments = attachment_map(self.session)
        rows = [
            self._game_row(
                game, now=now, scenario_line=scenarios.get(str(game.id)),
                attachments=attachments,
            )
            for game in games
        ]

        priced = [row for row in rows if row["selections"]]
        with_recorded_line = [
            row for row in rows if row["market"]["line_source"] == LINE_RECORDED
        ]
        usable = sum(
            1 for row in rows for sel in row["selections"]
            if sel["price"] is not None
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "event_date": event_date.isoformat(),
            "generated_at": now.isoformat(),
            "probability_units": PROBABILITY_UNITS,
            "overs_only": overs_only,
            # The filter changes what is browsed and nothing else. Stated in the
            # payload so the claim survives into any client that renders it.
            "filter_note": (
                "Showing Over selections only. This changes which selections are "
                "listed; it does not change any probability, price or estimate, "
                "and it is not a claim that Overs offer better value."
            ) if overs_only else None,
            "rows": [self._apply_filter(row, overs_only) for row in rows],
            "coverage": {
                "games": len(rows),
                "games_with_v18_distribution": sum(
                    1 for row in rows if row["forecast"]["distribution_available"]),
                "games_index_verified": sum(
                    1 for row in rows
                    if row["forecast"]["totals_verification"]["status"]
                    == VERIFIED_BY_INDEX),
                "games_with_recorded_line": len(with_recorded_line),
                "games_priceable": len(priced),
                "selections_with_usable_price": usable,
            },
            "market_availability": self._market_availability(now),
        }

    # -- per game ----------------------------------------------------------

    def _game_row(
        self, game: Game, *, now: datetime, scenario_line: float | None,
        attachments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        home, away = self._teams(game.id)
        selection = self.selector.select(game, now=now)
        primary = selection.primary

        forecast_block: dict[str, Any] = {
            "engine_id": primary.engine_id if primary else None,
            "engine_version": primary.engine_version if primary else None,
            "model_label": primary.model_label if primary else None,
            "forecast_id": primary.forecast_id if primary else None,
            "availability": primary.availability.value if primary else NO_FORECAST,
            "availability_detail": (
                primary.availability_detail if primary
                else "No forecast of any engine exists for this game."
            ),
            "decision_eligible": bool(primary and primary.decision_eligible),
            "distribution_available": False,
            "expected_total_runs": None,
            "simulation_count": None,
            "support": None,
            "unavailable_reason": None,
        }

        distribution = self._distribution(game, primary)
        if distribution is None:
            forecast_block["unavailable_reason"] = (
                NO_FORECAST if primary is None
                else FORECAST_NOT_CURRENT
                if primary.availability is not Availability.AVAILABLE
                else NO_DISTRIBUTION
            )
        else:
            forecast_block.update({
                "distribution_available": True,
                "expected_total_runs": round(distribution.expected_total_runs, 4),
                "simulation_count": distribution.simulation_count,
                "support": list(distribution.support),
            })
        forecast_block["totals_verification"] = self._verification(
            primary, attachments or {})

        market = self._market_block(game, now=now, scenario_line=scenario_line)
        selections = self._selections(
            distribution, market, now=now,
            matchup=f"{away['abbreviation']} at {home['abbreviation']}",
            forecast_facts={
                "expected_total_runs": forecast_block.get("expected_total_runs"),
                "simulation_count": forecast_block.get("simulation_count"),
                "forecast_id": forecast_block.get("forecast_id"),
                "engine_id": forecast_block.get("engine_id"),
                "engine_version": forecast_block.get("engine_version"),
            },
        )

        return {
            "game_id": str(game.id),
            "matchup": f"{away['abbreviation']} @ {home['abbreviation']}",
            "away_team": away,
            "home_team": home,
            "starts_at": _aware(game.starts_at).isoformat() if game.starts_at else None,
            "status": game.status.value if game.status else None,
            "forecast": forecast_block,
            "market": market,
            "selections": selections,
        }

    @staticmethod
    def _verification(primary: Any, attachments: dict[str, Any]) -> dict[str, Any]:
        """Which published index verified this forecast's PMF, if any."""

        identity = primary.forecast_id if primary else None
        record = attachments.get(identity) if identity else None
        if record is None:
            return {
                "status": NOT_IN_INDEX,
                "detail": (
                    "No published totals index has verified this exact forecast. "
                    "It is priced from the same stored field a verified forecast "
                    "uses; it simply postdates, or falls outside, every index "
                    "AISP has attached."
                ),
                "index_sha256": None,
                "attached_at_utc": None,
                "source_field": CONSUMER_FIELD,
            }
        return {
            "status": VERIFIED_BY_INDEX,
            "detail": (
                f"A published index verified this forecast's stored "
                f"{CONSUMER_FIELD} bin for bin against the producer's own bytes, "
                f"over {record.original_simulation_count:,} original paired draws "
                "with the complete scoring tail and extra innings included."
            ),
            "index_sha256": record.index_sha256,
            "index_schema_version": record.index_schema_version,
            "attached_at_utc": record.attached_at_utc.isoformat(),
            "derivative_published_at_utc": (
                record.derivative_published_at_utc.isoformat()
                if record.derivative_published_at_utc else None),
            "original_simulation_count": record.original_simulation_count,
            "extra_innings_included": record.extra_innings_included,
            "tail_truncated": record.tail_truncated,
            "score_limited": record.score_limited,
            "mlbam_game_pk": record.mlbam_game_pk,
            "source_field": CONSUMER_FIELD,
        }

    def _apply_filter(self, row: dict[str, Any], overs_only: bool) -> dict[str, Any]:
        if not overs_only:
            return row
        # A copy, so the unfiltered row stays intact for coverage counting. The
        # Under is hidden from the list, never deleted from the evidence.
        filtered = dict(row)
        filtered["selections"] = [
            sel for sel in row["selections"] if sel["side"] == "OVER"
        ]
        filtered["hidden_by_filter"] = [
            sel["side"] for sel in row["selections"] if sel["side"] != "OVER"
        ]
        return filtered

    # -- market ------------------------------------------------------------

    def _market_block(
        self, game: Game, *, now: datetime, scenario_line: float | None,
    ) -> dict[str, Any]:
        quotes = self._total_quotes(game.id)
        if quotes:
            line = self._primary_line(quotes)
            by_side: dict[str, list[_Quote]] = defaultdict(list)
            for quote in quotes:
                if quote.line == line:
                    by_side[quote.side].append(quote)
            observed = max(q.observed_at for q in quotes if q.line == line)
            return {
                "line": line,
                "line_source": LINE_RECORDED,
                "line_note": None,
                "books": {
                    side: [
                        {
                            "sportsbook": q.book,
                            "decimal_odds": round(q.decimal_odds, 4),
                            "american_odds": q.american_odds,
                            "observed_at": _aware(q.observed_at).isoformat(),
                            "quote_age_minutes": _age_minutes(q.observed_at, now),
                            "stale": (_age_minutes(q.observed_at, now) or 0) > FRESH_MINUTES,
                            "odds_snapshot_id": q.snapshot_id,
                            "market_outcome_id": q.market_outcome_id,
                        }
                        for q in sorted(rows, key=lambda r: -r.decimal_odds)
                    ]
                    for side, rows in by_side.items()
                },
                "observed_at": _aware(observed).isoformat(),
                "quote_age_minutes": _age_minutes(observed, now),
                "other_lines": sorted({q.line for q in quotes if q.line != line}),
                "unavailable_reason": None,
            }

        if scenario_line is not None:
            return {
                "line": float(scenario_line),
                "line_source": LINE_SCENARIO,
                "line_note": (
                    "This line was chosen by you, not quoted by a sportsbook. The "
                    "probabilities below are the model's at that line. There is no "
                    "price, so there is no return to estimate."
                ),
                "books": {},
                "observed_at": None,
                "quote_age_minutes": None,
                "other_lines": [],
                "unavailable_reason": NO_MARKET_LINE,
            }

        return {
            "line": None,
            "line_source": None,
            "line_note": None,
            "books": {},
            "observed_at": None,
            "quote_age_minutes": None,
            "other_lines": [],
            "unavailable_reason": NO_MARKET_LINE,
        }

    def _selections(
        self,
        distribution: TotalsDistribution | None,
        market: dict[str, Any],
        *,
        now: datetime,
        matchup: str = "",
        forecast_facts: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        line = market.get("line")
        if distribution is None or line is None:
            return []
        try:
            probabilities = price_line(distribution, float(line))
        except TotalsPricingError:
            return []

        forecast_facts = forecast_facts or {}
        rows: list[dict[str, Any]] = []
        for side in ("OVER", "UNDER"):
            p_win, p_push, p_loss = probabilities.fraction(side)
            best = self._best_quote(market, side)
            price_block = None
            if best is not None:
                price_block = {
                    "sportsbook": best["sportsbook"],
                    "decimal_odds": best["decimal_odds"],
                    "american_odds": best["american_odds"],
                    "observed_at": best["observed_at"],
                    "quote_age_minutes": best["quote_age_minutes"],
                    "stale": best["stale"],
                    "odds_snapshot_id": best["odds_snapshot_id"],
                    "market_outcome_id": best["market_outcome_id"],
                    # Model-implied, and the field name says so. This is an
                    # expectation under V18's distribution at the offered price,
                    # not a projection of realised profit and not an offer.
                    "model_implied_net_return_per_unit": round(
                        expected_net_return(p_win, p_loss, best["decimal_odds"]), 6),
                    "estimate_basis": (
                        "p_win x (decimal odds - 1) - p_loss, using V18's model "
                        "probabilities. A push returns the stake and contributes "
                        "zero. No calibration adjustment is applied."
                    ),
                }
            fair = fair_decimal_odds(p_win, p_push)
            selection = {
                "side": side,
                "line": probabilities.line,
                "line_source": market["line_source"],
                "win_probability_pct": (
                    probabilities.over_pct if side == "OVER" else probabilities.under_pct),
                "push_probability_pct": probabilities.push_pct,
                "loss_probability_pct": (
                    probabilities.under_pct if side == "OVER" else probabilities.over_pct),
                "probability_units": PROBABILITY_UNITS,
                "push_possible": probabilities.push_possible,
                "model_fair_decimal_odds": round(fair, 4) if fair else None,
                "model_fair_american_odds": american_from_decimal(fair),
                "price": price_block,
                "price_unavailable_reason": None if price_block else (
                    NO_MARKET_LINE if market["line_source"] == LINE_SCENARIO
                    else "NO_RECORDED_PRICE_FOR_THIS_SIDE"
                ),
            }
            # Built from THIS record, so the probability and the price on screen
            # can never belong to different lines. Books disagree about the
            # number as well as the price, and an explanation assembled from a
            # second lookup is exactly how Over 8.0's probability ends up beside
            # a price for Over 8.5.
            selection["explanation"] = explain_totals_opportunity(
                matchup=matchup,
                side=side,
                line=probabilities.line,
                line_source=market["line_source"],
                win_probability_pct=selection["win_probability_pct"],
                push_probability_pct=selection["push_probability_pct"],
                loss_probability_pct=selection["loss_probability_pct"],
                price=price_block,
                forecast=forecast_facts,
                model_fair_american=selection["model_fair_american_odds"],
            )
            rows.append(selection)
        return rows

    @staticmethod
    def _best_quote(market: dict[str, Any], side: str) -> dict[str, Any] | None:
        books = (market.get("books") or {}).get(side) or []
        return books[0] if books else None

    @staticmethod
    def _primary_line(quotes: list[_Quote]) -> float:
        """The line the most books agree on; ties break to the most quoted side.

        Books disagree about the number itself, not just the price, and picking
        "the newest" would make the displayed line jump between 8.5 and 9.0 on
        refresh. The modal line is stable and is the one a reader would call
        "the line".
        """

        counts: dict[float, int] = defaultdict(int)
        for quote in quotes:
            counts[quote.line] += 1
        return max(counts.items(), key=lambda item: (item[1], item[0]))[0]

    # -- evidence ----------------------------------------------------------

    def _distribution(
        self, game: Game, primary: Any,
    ) -> TotalsDistribution | None:
        if primary is None or primary.availability is not Availability.AVAILABLE:
            return None
        row = self.session.scalar(
            select(ShadowForecast).where(
                ShadowForecast.forecast_id == primary.forecast_id,
                ShadowForecast.canonical_game_id == game.id,
            )
        )
        if row is None:
            return None
        raw = (row.distributions or {}).get("totalRunDistribution")
        try:
            return TotalsDistribution.from_stored(
                raw,
                forecast_id=row.forecast_id,
                engine_id=row.engine_id,
                engine_version=row.engine_version,
                simulation_count=(row.uncertainty or {}).get("simulationCount"),
            )
        except TotalsPricingError:
            # A distribution that cannot price a line is treated as absent, not
            # patched. The row then reports NO_TOTAL_RUNS_DISTRIBUTION.
            return None

    def _total_quotes(self, game_id: Any) -> list[_Quote]:
        """The newest quote per (book, side, line) for this game's total."""

        rows = self.session.execute(
            select(OddsSnapshot, MarketOutcome, Sportsbook)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .where(
                Market.game_id == game_id,
                Market.market_type == MarketType.TOTAL,
                OddsSnapshot.point.is_not(None),
            )
            .order_by(OddsSnapshot.observed_at.desc())
        ).all()
        newest: dict[tuple[str, str, float], _Quote] = {}
        for snapshot, outcome, book in rows:
            side = outcome.label.strip().upper()
            if side not in {"OVER", "UNDER"}:
                continue
            key = (book.name, side, float(snapshot.point))
            if key in newest:
                continue  # ordered newest-first, so the first is the one to keep
            newest[key] = _Quote(
                book=book.name,
                side=side,
                line=float(snapshot.point),
                decimal_odds=float(snapshot.decimal_odds),
                american_odds=snapshot.american_odds,
                observed_at=_aware(snapshot.observed_at),
                snapshot_id=str(snapshot.id),
                market_outcome_id=str(outcome.id),
            )
        return list(newest.values())

    def _market_availability(self, now: datetime) -> dict[str, Any]:
        """When MLB totals were last captured, and from where.

        This exists because "no line" has two very different causes -- a book
        that has not posted one yet, and a capture path that stopped supplying
        the market at all. The second is the current state, and a reader who is
        not told will read an empty board as a model failure.
        """

        newest = self.session.execute(
            select(OddsSnapshot.observed_at, OddsSnapshot.source)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Game, Game.id == Market.game_id)
            .join(League, League.id == Game.league_id)
            .where(League.code == "MLB", Market.market_type == MarketType.TOTAL)
            .order_by(OddsSnapshot.observed_at.desc())
            .limit(1)
        ).first()
        if newest is None:
            return {
                "latest_total_quote_at": None,
                "latest_total_quote_source": None,
                "age_hours": None,
                "detail": "AISP holds no MLB totals quote from any source.",
            }
        observed, source = newest
        age = (now - _aware(observed)).total_seconds() / 3600.0
        return {
            "latest_total_quote_at": _aware(observed).isoformat(),
            "latest_total_quote_source": source,
            "age_hours": round(age, 1),
            "detail": (
                f"The newest MLB totals quote AISP holds was observed "
                f"{_aware(observed).date().isoformat()} from {source}."
            ),
        }

    # -- lookups -----------------------------------------------------------

    def _mlb_games(self, event_date: date) -> list[Game]:
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        return list(self.session.scalars(
            select(Game)
            .join(League, League.id == Game.league_id)
            .where(League.code == "MLB", Game.starts_at >= start, Game.starts_at < end)
            .order_by(Game.starts_at)
        ))

    def _teams(self, game_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        rows = list(self.session.execute(
            select(GameTeam, Team)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game_id)
        ))
        def describe(is_home: bool) -> dict[str, Any]:
            for side, team in rows:
                if side.is_home is is_home:
                    return {
                        "id": str(team.id),
                        "abbreviation": team.abbreviation,
                        "name": team.name,
                    }
            return {"id": None, "abbreviation": "—", "name": "Unknown"}
        return describe(True), describe(False)


__all__ = [
    "FRESH_MINUTES",
    "LINE_RECORDED",
    "LINE_SCENARIO",
    "NO_DISTRIBUTION",
    "NO_MARKET_LINE",
    "SCHEMA_VERSION",
    "MlbTotalsReadService",
]
