"""Focused checks for per-sport, per-season $1,000 paper accounts.

These are the six properties the accounting has to hold, written as the way
each one actually breaks:

1. MLB's capital and transaction counts survive the change.
2. The Gen1→V18 transition does not duplicate funding.
3. One sport cannot reserve another sport's cash.
4. Opening a season twice contributes once.
5. A prior-season settlement lands in the season that produced it.
6. Return denominators are right, and an unstarted account shows no capital.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.experiments.season_calendar import (
    program_season_for,
    season_for_date,
    window_for,
)
from app.models import (
    AccountStatus,
    ExperimentAccountFunding,
    Game,
    GameStatus,
    League,
    Market,
    MarketOutcome,
    MarketType,
    MembershipRole,
    OfficialAspPosition,
    OddsSnapshot,
    OddsStatus,
    PositionResult,
    PositionSettlement,
    PositionStatus,
    ShadowExperiment,
    Sport,
    Sportsbook,
    Team,
    Venue,
)
from app.services.experiment_accounts import (
    ExperimentAccountProvisioner,
    ExperimentAccountService,
)

NOW = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)
TODAY = date(2026, 9, 12)


# -- builders ------------------------------------------------------------


def _experiment(
    session,
    *,
    version: int,
    sport: str = "MLB",
    policy: str = "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
    start: date = date(2026, 9, 1),
    starting_bankroll: float = 1000.00,
    era: str | None = None,
    active: bool = True,
) -> ShadowExperiment:
    experiment = ShadowExperiment(
        name=f"TEST EXPERIMENT V{version}",
        version=version,
        started_at=datetime.combine(start, datetime.min.time(), tzinfo=UTC),
        start_date=start,
        starting_bankroll=starting_bankroll,
        sport=sport,
        recommendation_policy_version=policy,
        staking_policy_version="AISP_QUARTER_KELLY_EVIDENCE_V2",
        decision_policy=policy,
        controlling_engine="AISP_BASELINE" if era is None else "MLB_V18_WHOLE_GAME",
        supported_markets=["MONEYLINE"],
        kelly_fraction=0.25,
        unit_percentage=0.01,
        max_position_percentage=0.01,
        max_same_game_percentage=0.02,
        max_daily_percentage=0.05,
        code_commit="test",
        code_identity="test",
        is_active=active,
        model_era_id=era,
    )
    session.add(experiment)
    session.flush()
    return experiment


def _outcome(session, label: str) -> MarketOutcome:
    """A market outcome with the minimum real graph behind it."""

    sport = Sport(code=f"S{label[:6]}", name=label)
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code=f"L{label[:6]}", name=label)
    session.add(league)
    session.flush()
    venue = Venue(name=f"V{label}", city="C", timezone="UTC")
    home = Team(league_id=league.id, name=f"H{label}", abbreviation=f"H{label[:4]}")
    away = Team(league_id=league.id, name=f"A{label}", abbreviation=f"A{label[:4]}")
    session.add_all([venue, home, away])
    session.flush()
    game = Game(
        league_id=league.id, venue_id=venue.id,
        starts_at=datetime(2026, 9, 12, 23, 0, tzinfo=UTC), status=GameStatus.SCHEDULED,
    )
    session.add(game)
    session.flush()
    suffix = uuid4().hex[:6]
    book = Sportsbook(key=f"book-{label}-{suffix}", name=f"Book {label} {suffix}", region="us")
    session.add(book)
    session.flush()
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="FULL_GAME")
    session.add(market)
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="HOME:None", label="Home")
    session.add(outcome)
    session.flush()
    snapshot = OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id,
        observed_at=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
        available_as_of=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
        american_odds=-110, decimal_odds=1.9091, status=OddsStatus.OPEN,
    )
    session.add(snapshot)
    session.flush()
    return outcome


def _position(
    session,
    experiment: ShadowExperiment,
    *,
    stake: float,
    label: str,
    settled: tuple[PositionResult, float] | None = None,
    placed_at: datetime = NOW,
) -> OfficialAspPosition:
    outcome = _outcome(session, label)
    position = OfficialAspPosition(
        market_outcome_id=outcome.id,
        placed_at=placed_at,
        stake=stake,
        decimal_odds=1.9091,
        result=PositionResult.PENDING,
        status=PositionStatus.OPEN,
        strategy="test",
        shadow_experiment_id=experiment.id,
    )
    session.add(position)
    session.flush()
    if settled is not None:
        result, profit_loss = settled
        position.status = PositionStatus.SETTLED
        position.result = result
        position.settled_at = placed_at + timedelta(hours=4)
        session.add(PositionSettlement(
            position_id=position.id, result=result,
            settled_at=placed_at + timedelta(hours=4), profit_loss=profit_loss,
        ))
        session.flush()
    return position


@pytest.fixture
def accounts(session, settings) -> ExperimentAccountService:
    return ExperimentAccountService(session, settings)


# -- 1. MLB capital and transaction counts survive -----------------------


def test_mlb_capital_and_transaction_counts_are_preserved(session, settings, accounts):
    """The season reports one contribution, every settlement, and every stake.

    This is the shape of the real ledger at the boundary: a Gen1 era that
    settled 18 positions for -3.98 with one still open at 2.50, and a V18
    successor that opened two more for 3.52.
    """

    gen1 = _experiment(session, version=3)
    v18 = _experiment(
        session, version=6, start=date(2026, 9, 12), starting_bankroll=993.52,
        era="ASP_MLB_V18_TEST",
    )
    for index in range(18):
        _position(session, gen1, stake=2.50, label=f"s{index}",
                  settled=(PositionResult.LOSS, -3.98 / 18))
    _position(session, gen1, stake=2.50, label="open-gen1")
    _position(session, v18, stake=1.76, label="v18a")
    _position(session, v18, stake=1.76, label="v18b")

    account = accounts.ensure_account("MLB", "2026")
    for experiment in (gen1, v18):
        accounts.attach(account, experiment, now=NOW)
    accounts.open_season(
        account, now=NOW, executed_by="test", tracking_started_on=date(2026, 9, 1),
    )

    ledger = accounts.ledger(account)
    assert ledger.original_contribution == 1000.00
    assert ledger.settled_net_pl == pytest.approx(-3.98, abs=0.01)
    assert ledger.realized_bankroll == pytest.approx(996.02, abs=0.01)
    # 2.50 still open under Gen1 plus 3.52 open under V18 — both reserved, from
    # the one account, because the account is what holds the money.
    assert ledger.reserved_stake == pytest.approx(6.02, abs=0.01)
    assert ledger.available_capital == pytest.approx(990.00, abs=0.01)
    assert ledger.settled_positions == 18
    assert ledger.open_positions == 3


def test_partial_season_records_the_real_start_not_the_season_opening(
    session, settings, accounts,
):
    gen1 = _experiment(session, version=3, start=date(2026, 9, 1))
    account = accounts.ensure_account("MLB", "2026")
    accounts.attach(account, gen1, now=NOW)
    accounts.open_season(
        account, now=NOW, executed_by="test", tracking_started_on=date(2026, 9, 1),
    )
    # The MLB window opens 1 March; tracking actually began 1 September.
    assert account.season_opens_on == date(2026, 3, 1)
    assert account.tracking_started_on == date(2026, 9, 1)
    assert account.partial_season is True
    # And no curve point is drawn before the real start.
    ledger = accounts.ledger(account)
    assert ledger.equity_curve[0]["date"] == "2026-09-01"


# -- 2. No duplicated funding across the era transition ------------------


def test_era_transition_does_not_duplicate_funding(session, settings, accounts):
    """Two eras, one contribution.

    The successor's ``starting_bankroll`` is carried available capital. Summing
    era openings would report 1,993.52 contributed; the account reports the
    1,000.00 that was.
    """

    gen1 = _experiment(session, version=3, starting_bankroll=1000.00)
    v18 = _experiment(
        session, version=6, start=date(2026, 9, 12), starting_bankroll=993.52,
        era="ASP_MLB_V18_TEST",
    )
    _position(session, gen1, stake=2.50, label="g1", settled=(PositionResult.LOSS, -3.98))
    account = accounts.ensure_account("MLB", "2026")
    for experiment in (gen1, v18):
        accounts.attach(account, experiment, now=NOW)
    accounts.open_season(account, now=NOW, executed_by="test")

    naive = float(gen1.starting_bankroll) + float(v18.starting_bankroll)
    assert naive == pytest.approx(1993.52)
    assert accounts.original_contribution(account) == 1000.00
    assert session.scalar(
        select(func.count()).select_from(ExperimentAccountFunding)
        .where(ExperimentAccountFunding.account_id == account.id)
    ) == 1


def test_predecessor_settlement_returns_capital_to_the_season(session, settings, accounts):
    """The bug this design exists to prevent.

    Under per-era capital the successor opened at realized-minus-reserved and
    never saw the predecessor's stake come back. Here the settlement lands in
    the account, so the capital returns.
    """

    gen1 = _experiment(session, version=3)
    v18 = _experiment(
        session, version=6, start=date(2026, 9, 12), starting_bankroll=993.52,
        era="ASP_MLB_V18_TEST",
    )
    open_position = _position(session, gen1, stake=2.50, label="carry")
    account = accounts.ensure_account("MLB", "2026")
    for experiment in (gen1, v18):
        accounts.attach(account, experiment, now=NOW)
    accounts.open_season(account, now=NOW, executed_by="test")

    before = accounts.available_capital(account)
    assert before == pytest.approx(997.50)  # 1000 realized, 2.50 reserved

    # The predecessor's position finally wins under the successor's era.
    open_position.status = PositionStatus.SETTLED
    open_position.result = PositionResult.WIN
    session.add(PositionSettlement(
        position_id=open_position.id, result=PositionResult.WIN,
        settled_at=NOW + timedelta(days=1), profit_loss=2.27,
    ))
    session.flush()

    after = accounts.available_capital(account)
    # Stake released (+2.50) and profit recognised (+2.27). Nothing stranded in
    # the retired era, and the reserve is not subtracted a second time.
    assert after == pytest.approx(1002.27)
    assert accounts.reserved_stake(account) == 0.0


# -- 3. Cross-sport reservation isolation --------------------------------


def test_one_sport_cannot_reserve_another_sports_capital(session, settings, accounts):
    mlb = _experiment(session, version=3, sport="MLB")
    nfl = _experiment(session, version=7, sport="NFL", start=date(2026, 9, 10))

    mlb_account = accounts.ensure_account("MLB", "2026")
    nfl_account = accounts.ensure_account("NFL", "2026")
    accounts.attach(mlb_account, mlb, now=NOW)
    accounts.attach(nfl_account, nfl, now=NOW)
    accounts.open_season(mlb_account, now=NOW, executed_by="test")
    accounts.open_season(nfl_account, now=NOW, executed_by="test")

    _position(session, mlb, stake=400.00, label="big-mlb")

    assert accounts.reserved_stake(mlb_account) == pytest.approx(400.00)
    assert accounts.available_capital(mlb_account) == pytest.approx(600.00)
    # NFL is untouched: its reserve and its capital are its own.
    assert accounts.reserved_stake(nfl_account) == 0.0
    assert accounts.available_capital(nfl_account) == pytest.approx(1000.00)


def test_staking_basis_is_the_accounts_own_capital(session, settings, accounts):
    mlb = _experiment(session, version=3, sport="MLB")
    nfl = _experiment(session, version=7, sport="NFL", start=date(2026, 9, 10))
    mlb_account = accounts.ensure_account("MLB", "2026")
    nfl_account = accounts.ensure_account("NFL", "2026")
    accounts.attach(mlb_account, mlb, now=NOW)
    accounts.attach(nfl_account, nfl, now=NOW)
    accounts.open_season(mlb_account, now=NOW, executed_by="test")
    accounts.open_season(nfl_account, now=NOW, executed_by="test")
    _position(session, mlb, stake=100.00, label="mlb-open")

    assert accounts.staking_basis(mlb)["available"] == pytest.approx(900.00)
    assert accounts.staking_basis(nfl)["available"] == pytest.approx(1000.00)


def test_an_unfunded_control_never_draws_on_season_capital(session, settings, accounts):
    """The strict control must not size against funded money."""

    control = _experiment(session, version=1, policy="AISP_OPPORTUNITY_POLICY_V1")
    funded = _experiment(session, version=3)
    account = accounts.ensure_account("MLB", "2026")
    accounts.attach(account, control, now=NOW)
    accounts.attach(account, funded, now=NOW)
    accounts.open_season(account, now=NOW, executed_by="test")

    assert accounts.membership(control.id).role is MembershipRole.UNFUNDED_CONTROL
    # None means "keep the previous per-era behaviour" — the control is not
    # handed the season's capital.
    assert accounts.staking_basis(control) is None
    assert accounts.staking_basis(funded) is not None


def test_unfunded_control_is_excluded_from_funded_performance(session, settings, accounts):
    control = _experiment(session, version=1, policy="AISP_OPPORTUNITY_POLICY_V1")
    funded = _experiment(session, version=3)
    account = accounts.ensure_account("MLB", "2026")
    accounts.attach(account, control, now=NOW)
    accounts.attach(account, funded, now=NOW)
    accounts.open_season(account, now=NOW, executed_by="test")

    _position(session, control, stake=50.00, label="ctrl",
              settled=(PositionResult.WIN, 45.00))
    _position(session, funded, stake=10.00, label="fund",
              settled=(PositionResult.LOSS, -10.00))

    ledger = accounts.ledger(account)
    # The control's fictitious +45 does not reach the funded bankroll.
    assert ledger.settled_net_pl == pytest.approx(-10.00)
    assert ledger.realized_bankroll == pytest.approx(990.00)


# -- 4. Idempotent season opening ----------------------------------------


def test_opening_a_season_twice_contributes_once(session, settings, accounts):
    experiment = _experiment(session, version=3)
    account = accounts.ensure_account("MLB", "2026")
    accounts.attach(account, experiment, now=NOW)

    first, created_first = accounts.open_season(account, now=NOW, executed_by="test")
    second, created_second = accounts.open_season(account, now=NOW, executed_by="test")

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert accounts.original_contribution(account) == 1000.00


def test_provisioner_is_a_no_op_on_a_second_run(session, settings):
    _experiment(session, version=3)
    provisioner = ExperimentAccountProvisioner(session, settings)
    first = provisioner.run(today=TODAY, now=NOW, executed_by="test")
    second = provisioner.run(today=TODAY, now=NOW, executed_by="test")

    assert first.changed is True
    assert second.changed is False
    assert second.accounts_created == []
    assert second.accounts_funded == []
    assert second.memberships_created == []


def test_a_season_is_not_funded_before_it_opens(session, settings, accounts):
    """Funding a season early would place capital against games that cannot exist."""

    future = _experiment(session, version=9, sport="NHL", start=date(2026, 10, 5))
    account = accounts.ensure_account("NHL", "2026-27")
    accounts.attach(account, future, now=NOW)
    provisioner = ExperimentAccountProvisioner(session, settings)
    provisioner.run(today=date(2026, 9, 12), now=NOW, executed_by="test")
    assert accounts.funding(account) is None
    assert account.status is AccountStatus.PLANNED


def test_an_account_with_no_funded_era_is_not_funded(session, settings, accounts):
    """Capital with nothing able to reach it would be capital at risk of nothing."""

    provisioner = ExperimentAccountProvisioner(session, settings)
    provisioner.run(today=TODAY, now=NOW, executed_by="test")
    nfl = accounts.account("NFL", "2026")
    assert nfl is not None
    assert accounts.funding(nfl) is None
    assert nfl.status is AccountStatus.PLANNED


# -- 5. Prior-season settlement attribution after rollover ---------------


def test_a_prior_season_settlement_stays_in_the_season_that_produced_it(
    session, settings, accounts,
):
    """Rollover moves nothing. The old season keeps its liability and its result."""

    old_era = _experiment(session, version=3, start=date(2026, 9, 1))
    new_era = _experiment(session, version=11, start=date(2027, 3, 15))
    old = accounts.ensure_account("MLB", "2026")
    new = accounts.ensure_account("MLB", "2027")
    accounts.attach(old, old_era, now=NOW)
    accounts.attach(new, new_era, now=NOW)
    accounts.open_season(old, now=NOW, executed_by="test",
                         tracking_started_on=date(2026, 9, 1))
    accounts.open_season(new, now=datetime(2027, 3, 15, tzinfo=UTC), executed_by="test")

    # A position opened in the old season, settling after the new one started.
    carried = _position(session, old_era, stake=5.00, label="carried",
                        placed_at=datetime(2026, 11, 25, tzinfo=UTC))
    carried.status = PositionStatus.SETTLED
    carried.result = PositionResult.WIN
    session.add(PositionSettlement(
        position_id=carried.id, result=PositionResult.WIN,
        settled_at=datetime(2027, 3, 20, tzinfo=UTC), profit_loss=4.55,
    ))
    session.flush()

    old_ledger = accounts.ledger(old)
    new_ledger = accounts.ledger(new)
    # The result belongs to 2026. The new season opens at a clean 1,000.
    assert old_ledger.settled_net_pl == pytest.approx(4.55)
    assert old_ledger.realized_bankroll == pytest.approx(1004.55)
    assert new_ledger.settled_net_pl == 0.0
    assert new_ledger.realized_bankroll == pytest.approx(1000.00)
    # And no balance was transferred: the new season got a fresh contribution.
    assert new_ledger.original_contribution == 1000.00


def test_a_closed_season_with_an_open_position_is_not_archived(session, settings, accounts):
    """"Closed for new positions" and "finished" are different facts."""

    era = _experiment(session, version=3, start=date(2026, 9, 1))
    account = accounts.ensure_account("MLB", "2026")
    accounts.attach(account, era, now=NOW)
    accounts.open_season(account, now=NOW, executed_by="test")
    _position(session, era, stake=5.00, label="still-open")

    accounts.sync_status(account, today=date(2026, 12, 15))  # past 30 November
    assert account.status is AccountStatus.CLOSED_FOR_NEW_POSITIONS

    # Once nothing is open, it can be archived.
    position = session.scalars(select(OfficialAspPosition)).first()
    position.status = PositionStatus.SETTLED
    session.add(PositionSettlement(
        position_id=position.id, result=PositionResult.LOSS,
        settled_at=datetime(2026, 12, 1, tzinfo=UTC), profit_loss=-5.00,
    ))
    session.flush()
    accounts.sync_status(account, today=date(2026, 12, 15))
    assert account.status is AccountStatus.ARCHIVED


# -- 6. Return denominators and unstarted presentation -------------------


def test_season_return_divides_by_the_contribution_not_the_balance(
    session, settings, accounts,
):
    era = _experiment(session, version=3)
    account = accounts.ensure_account("MLB", "2026")
    accounts.attach(account, era, now=NOW)
    accounts.open_season(account, now=NOW, executed_by="test")
    _position(session, era, stake=40.00, label="a", settled=(PositionResult.LOSS, -40.00))

    ledger = accounts.ledger(account)
    # -40 on a 1,000 contribution is -4.00%, whatever the balance now reads.
    assert ledger.season_return_pct == pytest.approx(-4.00)
    # The same -40 over 40 of settled stake is -100%. A different question.
    assert ledger.settled_stake_roi_pct == pytest.approx(-100.00)


def test_zero_settled_stake_gives_an_unavailable_roi_not_a_zero(
    session, settings, accounts,
):
    era = _experiment(session, version=3)
    account = accounts.ensure_account("MLB", "2026")
    accounts.attach(account, era, now=NOW)
    accounts.open_season(account, now=NOW, executed_by="test")
    _position(session, era, stake=10.00, label="open-only")

    ledger = accounts.ledger(account)
    assert ledger.settled_stake == 0.0
    # Not 0.0 — there is no measurement to report.
    assert ledger.settled_stake_roi_pct is None
    # The season return is still defined: nothing has settled, so it is 0%.
    assert ledger.season_return_pct == pytest.approx(0.0)


def test_an_unstarted_account_reports_a_plan_and_no_capital(session, settings):
    from app.services.experiment_accounts_read import ExperimentAccountReadService

    ExperimentAccountProvisioner(session, settings).run(
        today=TODAY, now=NOW, executed_by="test",
    )
    data = ExperimentAccountReadService(session, settings).program(today=TODAY)
    planned = [row for row in data.accounts if row.status == "PLANNED"]
    assert planned, "the program should carry planned accounts for unstarted sports"
    for row in planned:
        assert row.planned_contribution == 1000.00
        # Every money field is absent, so no client can add it to a funded total.
        assert row.original_contribution is None
        assert row.realized_bankroll is None
        assert row.available_capital is None
        assert row.season_return_pct is None
        assert row.equity_curve == []
        assert row.status_note


def test_the_summary_keeps_planned_allocation_out_of_funded_capital(session, settings):
    from app.services.experiment_accounts_read import ExperimentAccountReadService

    _experiment(session, version=3)
    ExperimentAccountProvisioner(session, settings).run(
        today=TODAY, now=NOW, executed_by="test",
    )
    summary = ExperimentAccountReadService(session, settings).program(today=TODAY).summary

    assert summary.funded_accounts == 1
    assert summary.funded_contribution == 1000.00
    # Four unstarted sports at 1,000 each: proposed, never funded.
    assert summary.planned_accounts == 4
    assert summary.planned_allocation == 4000.00
    assert summary.funded_contribution != 5000.00
    assert "not funded" in summary.note


# -- season identity -----------------------------------------------------


def test_nfl_postseason_belongs_to_the_season_it_completes():
    assert season_for_date("NFL", date(2026, 9, 20)).season_id == "2026"
    # February playoffs complete the 2026 season, not a new 2027 one.
    assert season_for_date("NFL", date(2027, 2, 7)).season_id == "2026"


def test_split_year_sports_are_identified_by_both_years():
    assert season_for_date("NBA", date(2026, 11, 5)).season_id == "2026-27"
    assert season_for_date("NBA", date(2027, 4, 10)).season_id == "2026-27"
    assert season_for_date("NHL", date(2027, 1, 2)).season_id == "2026-27"


def test_mma_is_a_labelled_calendar_year_campaign():
    window = window_for("MMA", "2026")
    assert window.season_id == "2026"
    assert window.convention is not None
    assert "convention" in window.convention.lower()


def test_off_season_shows_the_upcoming_season_not_the_finished_one():
    # 12 September is between the NBA's June close and its October opening.
    assert program_season_for("NBA", date(2026, 9, 12)).season_id == "2026-27"
    # But a settlement dated then still belongs to the season that produced it.
    assert season_for_date("NBA", date(2026, 9, 12)).season_id == "2025-26"


def test_exactly_one_era_is_marked_as_controlling(session, settings, accounts):
    """A predecessor stays active so its positions settle — it is not in control.

    Reading ``is_active`` as "in control" put "controlling now" on every active
    era at once, including the retired one and the unfunded control.
    """

    from app.services.experiment_accounts_read import ExperimentAccountReadService

    control = _experiment(session, version=1, policy="AISP_OPPORTUNITY_POLICY_V1")
    gen1 = _experiment(session, version=3)
    v18 = _experiment(
        session, version=6, start=date(2026, 9, 12), starting_bankroll=993.52,
        era="ASP_MLB_V18_TEST",
    )
    _position(session, gen1, stake=2.50, label="carry")   # keeps Gen1 active
    _position(session, v18, stake=1.76, label="v18")

    account = accounts.ensure_account("MLB", "2026")
    for experiment in (control, gen1, v18):
        accounts.attach(account, experiment, now=NOW)
    accounts.open_season(account, now=NOW, executed_by="test")

    data = ExperimentAccountReadService(session, settings).season("MLB", "2026", today=TODAY)
    eras = data.accounts[0].eras
    controlling = [era for era in eras if era.is_controlling]
    assert len(controlling) == 1
    # The successor, not the predecessor and not the control.
    assert controlling[0].version == 6
    # All three are still "active"; that is a different fact and stays true.
    assert sum(1 for era in eras if era.is_active) == 3
    assert data.accounts[0].controlling_era_id == "ASP_MLB_V18_TEST"
