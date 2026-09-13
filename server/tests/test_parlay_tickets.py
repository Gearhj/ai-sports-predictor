"""Recording a ticket once, and settling it once, against governed finals.

These use a real session so the store-level guarantees are exercised rather than
described: the unique content address that makes recording idempotent, the
unique (ticket, game) pair that enforces one selection per game, and the
settlement path that has to keep a pushed leg out of the loss column.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import (
    AccountStatus,
    ExperimentAccount,
    Game,
    GameFinalOutcome,
    GameStatus,
    GameTeam,
    League,
    Market,
    MarketOutcome,
    MarketType,
    ParlayTicket,
    ParlayTicketLeg,
    PositionResult,
    PositionStatus,
    Season,
    Sport,
    Team,
)
from app.parlay.pricing import (
    PRICE_ESTIMATED_FROM_LEGS,
    PRICE_RECORDED_COMBINED,
    Leg,
)
from app.parlay.tickets import (
    NOTE_AWAITING_BOOK_REDUCTION,
    ParlayTicketError,
    ParlayTicketService,
    ticket_digest,
)
from app.parlay.performance import ParlayPerformanceReadService

NOW = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)
START = datetime(2026, 9, 14, 23, 10, tzinfo=UTC)


@pytest.fixture
def world(session: Session):
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=START - timedelta(days=160),
                    ends_at=START + timedelta(days=30))
    session.add(season)
    session.flush()

    games = {}
    for key, (home_name, home_abbr, away_name, away_abbr) in {
        "g1": ("Detroit Tigers", "DET", "Colorado Rockies", "COL"),
        "g2": ("New York Yankees", "NYY", "New York Mets", "NYM"),
        "g3": ("Boston Red Sox", "BOS", "Kansas City Royals", "KC"),
    }.items():
        home = Team(league_id=league.id, name=home_name, abbreviation=home_abbr)
        away = Team(league_id=league.id, name=away_name, abbreviation=away_abbr)
        session.add_all([home, away])
        session.flush()
        game = Game(league_id=league.id, season_id=season.id, starts_at=START,
                    status=GameStatus.SCHEDULED, source_mode="LIVE")
        session.add(game)
        session.flush()
        session.add_all([
            GameTeam(game_id=game.id, team_id=home.id, is_home=True),
            GameTeam(game_id=game.id, team_id=away.id, is_home=False),
        ])
        games[key] = {"game": game, "home": home, "away": away}
    session.flush()
    return {"session": session, "league": league, "season": season, "games": games}


def outcome_for(session: Session, game: Game, *, market_type, key, label, team=None):
    market = session.scalar(select(Market).where(
        Market.game_id == game.id, Market.market_type == market_type))
    if market is None:
        market = Market(game_id=game.id, market_type=market_type, period="full_game")
        session.add(market)
        session.flush()
    row = session.scalar(select(MarketOutcome).where(
        MarketOutcome.market_id == market.id, MarketOutcome.outcome_key == key))
    if row is None:
        row = MarketOutcome(market_id=market.id, outcome_key=key, label=label,
                            participant_team_id=team.id if team else None)
        session.add(row)
        session.flush()
    return row


def leg_for(
    world, key, *, market="MONEYLINE", selection=None, p_win=0.6, p_push=0.0,
    odds=2.0, book="Bovada", line=None, back_home=True,
) -> Leg:
    entry = world["games"][key]
    game = entry["game"]
    session = world["session"]
    team = entry["home"] if back_home else entry["away"]
    if market == "MONEYLINE":
        selection = selection or f"{team.abbreviation} ML"
        row = outcome_for(session, game, market_type=MarketType.MONEYLINE,
                          key=f"ML:{team.abbreviation}", label=team.name, team=team)
    else:
        selection = selection or f"OVER {line:g}"
        side = selection.split()[0].title()
        row = outcome_for(session, game, market_type=MarketType.TOTAL,
                          key=f"{side.upper()}:{line}", label=side)
    return Leg(
        game_id=str(game.id), market=market, selection=selection,
        p_win=p_win, p_push=p_push, p_loss=1.0 - p_win - p_push,
        decimal_odds=odds, sportsbook=book,
        forecast_id="mlb-v18-daily-test", engine_id="MLB_V18_WHOLE_GAME",
        starts_at=START.isoformat(), line=line,
        market_outcome_id=str(row.id),
    )


def record_final(world, key: str, *, home: int, away: int, revision: int = 1):
    """One governed final outcome row, built the way the A1 store requires.

    Settlement reads ``game_final_outcomes`` -- the separately governed,
    revisioned record -- rather than the game row's own scores, so the test has
    to write the real thing.
    """

    session = world["session"]
    entry = world["games"][key]
    game = entry["game"]
    retrieved_at = START + timedelta(hours=4)
    session.add(GameFinalOutcome(
        game_id=game.id,
        season_id=world["season"].id,
        home_team_id=entry["home"].id,
        away_team_id=entry["away"].id,
        # Provenance FKs are not the subject here; the settlement path reads
        # only the score, revision and status off this row.
        source_version_id=uuid.uuid4(),
        checkpoint_id=uuid.uuid4(),
        official_date=START.date(),
        scheduled_at=game.starts_at,
        final_at=START + timedelta(hours=3),
        reconstructed_at=retrieved_at,
        home_score=home,
        away_score=away,
        revision=revision,
        official=True,
        source_record_id=f"game:{key}:revision:{revision}",
        retrieved_at=retrieved_at,
        label_available_at=retrieved_at,
        availability_basis="ASP_ACQUISITION_TIME",
        payload_checksum=f"{key[-1]}{revision}".ljust(64, "0"),
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum=f"{key[-1]}{revision}".ljust(64, "f"),
    ))
    session.flush()


# -- recording ---------------------------------------------------------------

class TestRecordingIsDeliberateAndIdempotent:
    def test_records_a_ticket_with_its_legs_frozen(self, world):
        service = ParlayTicketService(world["session"])
        outcome = service.record(
            [leg_for(world, "g1"), leg_for(world, "g2")],
            stake=1.0, account_id=None, funded=False, now=NOW)
        assert outcome.created is True
        ticket = outcome.ticket
        assert ticket.leg_count == 2
        assert float(ticket.stake) == 1.0
        assert ticket.price_provenance == PRICE_ESTIMATED_FROM_LEGS
        legs = world["session"].scalars(select(ParlayTicketLeg).where(
            ParlayTicketLeg.ticket_id == ticket.id)).all()
        assert len(legs) == 2
        # Everything needed to re-read the decision later is on the row itself.
        assert all(leg.forecast_identity == "mlb-v18-daily-test" for leg in legs)
        assert all(leg.engine_id == "MLB_V18_WHOLE_GAME" for leg in legs)
        assert ticket.settlement_rules["version"] == "AISP_PARLAY_SETTLEMENT_RULES_V1"

    def test_recording_the_same_ticket_twice_writes_one_row(self, world):
        """The second call returns the first ticket and writes nothing."""

        session = world["session"]
        service = ParlayTicketService(session)
        legs = [leg_for(world, "g1"), leg_for(world, "g2")]
        first = service.record(legs, stake=1.0, account_id=None, funded=False, now=NOW)
        second = service.record(legs, stake=1.0, account_id=None, funded=False, now=NOW)
        assert first.created is True
        assert second.created is False
        assert second.ticket.id == first.ticket.id
        assert "already recorded" in second.detail
        assert session.scalar(select(func.count()).select_from(ParlayTicket)) == 1

    def test_leg_order_does_not_change_the_ticket_identity(self, world):
        """Same three selections, listed differently, are the same ticket."""

        session = world["session"]
        service = ParlayTicketService(session)
        a, b = leg_for(world, "g1"), leg_for(world, "g2")
        service.record([a, b], stake=1.0, account_id=None, funded=False, now=NOW)
        again = service.record([b, a], stake=1.0, account_id=None, funded=False, now=NOW)
        assert again.created is False
        assert session.scalar(select(func.count()).select_from(ParlayTicket)) == 1

    def test_a_different_stake_is_a_different_ticket(self, world):
        session = world["session"]
        service = ParlayTicketService(session)
        legs = [leg_for(world, "g1"), leg_for(world, "g2")]
        service.record(legs, stake=1.0, account_id=None, funded=False, now=NOW)
        service.record(legs, stake=2.0, account_id=None, funded=False, now=NOW)
        assert session.scalar(select(func.count()).select_from(ParlayTicket)) == 2

    def test_the_digest_is_over_the_decision_not_the_dict_order(self):
        """Key order must not change the address, or nothing would be idempotent."""

        a = ticket_digest({"legs": ["a", "b"], "stake": 1.0})
        b = ticket_digest({"stake": 1.0, "legs": ["a", "b"]})
        assert a == b

    def test_the_digest_changes_when_the_decision_changes(self):
        a = ticket_digest({"legs": ["a", "b"], "stake": 1.0})
        b = ticket_digest({"legs": ["a", "c"], "stake": 1.0})
        assert a != b

    def test_refuses_a_ticket_past_its_earliest_cutoff(self, world):
        service = ParlayTicketService(world["session"])
        with pytest.raises(ParlayTicketError, match="pregame cutoff"):
            service.record(
                [leg_for(world, "g1"), leg_for(world, "g2")],
                stake=1.0, account_id=None, funded=False,
                now=START - timedelta(minutes=5))

    def test_refuses_a_funded_ticket_with_no_account(self, world):
        """There is no second parlay bankroll to fall back on."""

        service = ParlayTicketService(world["session"])
        with pytest.raises(ParlayTicketError, match="no second parlay bankroll"):
            service.record(
                [leg_for(world, "g1"), leg_for(world, "g2")],
                stake=1.0, account_id=None, funded=True, now=NOW)

    def test_refuses_two_selections_from_one_game(self, world):
        service = ParlayTicketService(world["session"])
        with pytest.raises(ParlayTicketError, match="same game"):
            service.record(
                [leg_for(world, "g1"),
                 leg_for(world, "g1", market="TOTAL", line=8.5, p_win=0.5)],
                stake=1.0, account_id=None, funded=False, now=NOW)


# -- settlement --------------------------------------------------------------

class TestSettlement:
    def _record(self, world, legs, **kwargs):
        return ParlayTicketService(world["session"]).record(
            legs, stake=kwargs.pop("stake", 1.0), account_id=kwargs.pop("account_id", None),
            funded=kwargs.pop("funded", False), now=NOW, **kwargs)

    def test_a_ticket_stays_pending_while_any_game_lacks_a_final(self, world):
        session = world["session"]
        self._record(world, [leg_for(world, "g1"), leg_for(world, "g2")])
        record_final(world, "g1", home=5, away=1)
        report = ParlayTicketService(session).settle_open_tickets(now=NOW)
        assert report["settled"] == 0
        assert report["pending_missing_evidence"] == 1

    def test_both_moneyline_legs_winning_pays_the_product(self, world):
        session = world["session"]
        ticket = self._record(world, [
            leg_for(world, "g1", odds=2.0, back_home=True),
            leg_for(world, "g2", odds=2.5, back_home=True),
        ], stake=1.0).ticket
        record_final(world, "g1", home=5, away=1)
        record_final(world, "g2", home=7, away=2)
        ParlayTicketService(session).settle_open_tickets(now=NOW)
        session.refresh(ticket)
        assert ticket.result is PositionResult.WIN
        assert ticket.status is PositionStatus.SETTLED
        assert float(ticket.payout) == pytest.approx(5.0)
        assert float(ticket.profit_loss) == pytest.approx(4.0)

    def test_one_losing_leg_loses_the_ticket(self, world):
        session = world["session"]
        ticket = self._record(world, [
            leg_for(world, "g1", back_home=True),
            leg_for(world, "g2", back_home=True),
        ]).ticket
        record_final(world, "g1", home=5, away=1)
        record_final(world, "g2", home=2, away=7)  # away won
        ParlayTicketService(session).settle_open_tickets(now=NOW)
        session.refresh(ticket)
        assert ticket.result is PositionResult.LOSS
        assert float(ticket.profit_loss) == pytest.approx(-1.0)
        assert ticket.settled_decimal_odds is None

    def test_a_moneyline_leg_is_settled_from_the_stored_team_not_the_label(self, world):
        """Backing the away team wins when the away team wins."""

        session = world["session"]
        ticket = self._record(world, [
            leg_for(world, "g1", back_home=False, odds=2.0),
            leg_for(world, "g2", back_home=False, odds=2.0),
        ]).ticket
        record_final(world, "g1", home=1, away=5)
        record_final(world, "g2", home=2, away=7)
        ParlayTicketService(session).settle_open_tickets(now=NOW)
        session.refresh(ticket)
        assert ticket.result is PositionResult.WIN

    def test_an_integer_total_landing_on_the_line_pushes_and_the_ticket_reprices(
        self, world,
    ):
        """The case a naive settler gets wrong.

        The Over 8 leg lands exactly on 8, so it is removed. The ticket is not a
        loss; it pays at the surviving leg's price alone.
        """

        session = world["session"]
        ticket = self._record(world, [
            leg_for(world, "g1", market="TOTAL", line=8.0, selection="OVER 8",
                    p_win=0.45, p_push=0.10, odds=2.0),
            leg_for(world, "g2", odds=2.5, back_home=True),
        ], stake=1.0).ticket
        record_final(world, "g1", home=5, away=3)  # total 8
        record_final(world, "g2", home=7, away=2)
        ParlayTicketService(session).settle_open_tickets(now=NOW)
        session.refresh(ticket)

        legs = session.scalars(select(ParlayTicketLeg).where(
            ParlayTicketLeg.ticket_id == ticket.id).order_by(
            ParlayTicketLeg.ordinal)).all()
        pushed = [leg for leg in legs if leg.result is PositionResult.PUSH]
        assert len(pushed) == 1
        assert "equals the line" in pushed[0].settled_note

        assert ticket.result is PositionResult.WIN
        # Repriced over the surviving leg only: 2.5, not 5.0.
        assert ticket.settled_decimal_odds == pytest.approx(2.5)
        assert float(ticket.payout) == pytest.approx(2.5)
        assert float(ticket.profit_loss) == pytest.approx(1.5)
        assert "Reduced to 1 of 2 legs" in ticket.settlement_note

    def test_a_half_run_line_can_never_push(self, world):
        session = world["session"]
        ticket = self._record(world, [
            leg_for(world, "g1", market="TOTAL", line=8.5, selection="OVER 8.5",
                    p_win=0.45, odds=2.0),
            leg_for(world, "g2", odds=2.0, back_home=True),
        ]).ticket
        record_final(world, "g1", home=5, away=3)  # total 8
        record_final(world, "g2", home=7, away=2)
        ParlayTicketService(session).settle_open_tickets(now=NOW)
        session.refresh(ticket)
        # 8 is under 8.5, so the Over loses outright. No push branch exists.
        assert ticket.result is PositionResult.LOSS

    def test_every_leg_pushing_returns_the_stake(self, world):
        session = world["session"]
        ticket = self._record(world, [
            leg_for(world, "g1", market="TOTAL", line=8.0, selection="OVER 8",
                    p_win=0.45, p_push=0.10, odds=2.0),
            leg_for(world, "g2", market="TOTAL", line=9.0, selection="UNDER 9",
                    p_win=0.45, p_push=0.10, odds=2.0),
        ]).ticket
        record_final(world, "g1", home=5, away=3)  # 8
        record_final(world, "g2", home=5, away=4)  # 9
        ParlayTicketService(session).settle_open_tickets(now=NOW)
        session.refresh(ticket)
        assert ticket.result is PositionResult.PUSH
        assert float(ticket.payout) == pytest.approx(1.0)
        assert float(ticket.profit_loss) == pytest.approx(0.0)

    def test_a_recorded_combined_quote_with_a_push_is_not_guessed(self, world):
        """The honest stop.

        A real full-ticket quote does not say what the book pays after a leg is
        removed. Rather than assume the product of the survivors, the ticket
        stays open and records exactly why.
        """

        session = world["session"]
        ticket = ParlayTicketService(session).record(
            [
                leg_for(world, "g1", market="TOTAL", line=8.0, selection="OVER 8",
                        p_win=0.45, p_push=0.10, odds=2.0),
                leg_for(world, "g2", odds=2.5, back_home=True),
            ],
            stake=1.0, account_id=None, funded=False,
            recorded_combined_decimal_odds=4.6, now=NOW,
        ).ticket
        assert ticket.price_provenance == PRICE_RECORDED_COMBINED
        record_final(world, "g1", home=5, away=3)
        record_final(world, "g2", home=7, away=2)
        report = ParlayTicketService(session).settle_open_tickets(now=NOW)
        session.refresh(ticket)

        assert report["awaiting_book_reduction_rule"] == 1
        assert report["settled"] == 0
        assert ticket.status is PositionStatus.OPEN
        assert ticket.result is PositionResult.PENDING
        assert ticket.profit_loss is None
        assert NOTE_AWAITING_BOOK_REDUCTION.split(".")[0] in ticket.settlement_note

    def test_settling_twice_does_not_double_book(self, world):
        session = world["session"]
        ticket = self._record(world, [
            leg_for(world, "g1", odds=2.0), leg_for(world, "g2", odds=2.0),
        ]).ticket
        record_final(world, "g1", home=5, away=1)
        record_final(world, "g2", home=7, away=2)
        ParlayTicketService(session).settle_open_tickets(now=NOW)
        session.refresh(ticket)
        first = float(ticket.profit_loss)
        second = ParlayTicketService(session).settle_open_tickets(now=NOW)
        session.refresh(ticket)
        assert second["open_tickets"] == 0
        assert float(ticket.profit_loss) == first


# -- reporting ---------------------------------------------------------------

class TestPerformanceStaysSeparated:
    def _account(self, world) -> ExperimentAccount:
        account = ExperimentAccount(
            sport="MLB", season_id="2026", season_label="MLB 2026",
            season_opens_on=START.date() - timedelta(days=160),
            season_closes_on=START.date() + timedelta(days=30),
            boundary_basis="test fixture",
            planned_contribution=1000.0,
            status=AccountStatus.ACTIVE,
        )
        world["session"].add(account)
        world["session"].flush()
        return account

    def test_estimated_and_recorded_price_performance_never_merge(self, world, settings):
        session = world["session"]
        service = ParlayTicketService(session)
        service.record([leg_for(world, "g1"), leg_for(world, "g2")],
                       stake=1.0, account_id=None, funded=False, now=NOW)
        service.record(
            [leg_for(world, "g1", odds=2.2), leg_for(world, "g3", odds=2.2)],
            stake=1.0, account_id=None, funded=False,
            recorded_combined_decimal_odds=4.4, now=NOW)

        summary = ParlayPerformanceReadService(session, settings).summary()
        buckets = summary["performance"]["by_price_provenance"]
        assert buckets[PRICE_ESTIMATED_FROM_LEGS]["tickets"] == 1
        assert buckets[PRICE_RECORDED_COMBINED]["tickets"] == 1
        assert "not added into straight-bet performance" in \
            summary["performance"]["separation_note"]

    def test_an_untouched_bucket_reports_none_not_zero_percent(self, world, settings):
        session = world["session"]
        ParlayTicketService(session).record(
            [leg_for(world, "g1"), leg_for(world, "g2")],
            stake=1.0, account_id=None, funded=False, now=NOW)
        summary = ParlayPerformanceReadService(session, settings).summary()
        bucket = summary["performance"]["by_price_provenance"][PRICE_ESTIMATED_FROM_LEGS]
        # Nothing settled yet, so a return figure would be an invention.
        assert bucket["return_on_settled_stake_pct"] is None
        assert bucket["open"] == 1

    def test_shared_legs_are_reported_with_their_combined_exposure(
        self, world, settings,
    ):
        """Three $1 tickets that all need the same leg are a $3 bet on it."""

        session = world["session"]
        service = ParlayTicketService(session)
        service.record([leg_for(world, "g1"), leg_for(world, "g2")],
                       stake=1.0, account_id=None, funded=False, now=NOW)
        service.record([leg_for(world, "g1"), leg_for(world, "g3")],
                       stake=1.0, account_id=None, funded=False, now=NOW)

        shared = ParlayPerformanceReadService(session, settings).summary()["shared_legs"]
        assert len(shared) == 1
        assert shared[0]["ticket_count"] == 2
        assert shared[0]["total_stake_exposed"] == pytest.approx(2.0)

    def test_funded_and_research_tickets_reconcile_separately(self, world, settings):
        session = world["session"]
        account = self._account(world)
        service = ParlayTicketService(session)
        service.record([leg_for(world, "g1"), leg_for(world, "g2")],
                       stake=1.0, account_id=account.id, funded=True, now=NOW)
        service.record([leg_for(world, "g1", odds=2.2), leg_for(world, "g3")],
                       stake=1.0, account_id=None, funded=False, now=NOW)

        reconciliation = ParlayPerformanceReadService(session, settings) \
            .summary()["account_reconciliation"]
        assert reconciliation["funded_tickets"] == 1
        assert reconciliation["unfunded_research_tickets"] == 1
        assert reconciliation["parlay_reserved_stake"] == pytest.approx(1.0)
        assert reconciliation["parlay_realized_profit_loss"] == pytest.approx(0.0)
        assert reconciliation["account_id"] == str(account.id)
        assert "no second bankroll" in reconciliation["note"]

    def test_an_unfunded_ticket_reserves_nothing(self, world, settings):
        session = world["session"]
        self._account(world)
        ParlayTicketService(session).record(
            [leg_for(world, "g1"), leg_for(world, "g2")],
            stake=5.0, account_id=None, funded=False, now=NOW)
        reconciliation = ParlayPerformanceReadService(session, settings) \
            .summary()["account_reconciliation"]
        assert reconciliation["parlay_reserved_stake"] == pytest.approx(0.0)
