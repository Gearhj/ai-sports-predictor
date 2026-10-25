"""Wager Results read model: official funded positions against their recorded estimates.

Each test is one way the tab could mislead a reader:

* totals that drift from the experiment ledger for the same positions;
* a game counted once per checkpoint decision instead of once per position;
* House Divided copies or unfunded shadows leaking into the funded population;
* the home probability shown for an away selection;
* open exposure folded into settled results;
* a push counted as a loss, or in the win-rate denominator;
* a wager without a recorded return silently dropped instead of listed as missing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.models import (
    Game,
    GameStatus,
    League,
    Market,
    MarketOutcome,
    MarketType,
    MembershipRole,
    OddsSnapshot,
    OddsStatus,
    OfficialAspPosition,
    PositionResult,
    PositionSettlement,
    PositionStatus,
    ShadowExperiment,
    Sport,
    Sportsbook,
    Team,
)
from app.models.entities import ExperimentDecisionPhase, ShadowExperimentDecision
from app.models.house_divided import CompetitionWager
from app.services import command_center, sport_readiness
from app.services.experiment_accounts import ExperimentAccountService
from app.services.wager_results_read import (
    PROBABILITY_BANDS,
    RETURN_BANDS,
    band_of,
    wager_results,
    wilson,
)

NOW = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
ENGINES = {"MLB": "MLB_V18_WHOLE_GAME", "NFL": "NFL_M1_XGBOOST", "CFB": "CFB_GEN1_JOINT_BASELINE"}
POLICIES = {"MLB": "AISP_PRACTICAL_PREGAME_POLICY_V2_1", "NFL": "AISP_NFL_PAPER_POLICY_V1",
            "CFB": "AISP_CFB_PAPER_POLICY_V1"}


# -- builders ------------------------------------------------------------------

class World:
    def __init__(self, session, settings):
        self.session = session
        self.accounts = ExperimentAccountService(session, settings)
        self.version = 0
        self.book = Sportsbook(key=f"book-{uuid4().hex[:6]}", name="LowVig.ag", region="us")
        session.add(self.book)
        session.flush()
        self.leagues: dict[str, League] = {}

    def experiment(self, sport: str, *, role: MembershipRole = MembershipRole.FUNDED_ERA) -> ShadowExperiment:
        self.version += 1
        exp = ShadowExperiment(
            name=f"TEST {sport} ERA V{self.version}", version=self.version,
            started_at=datetime(2026, 9, 1, tzinfo=UTC), start_date=date(2026, 9, 1), starting_bankroll=1000,
            sport=sport, recommendation_policy_version=POLICIES[sport],
            staking_policy_version="AISP_QUARTER_KELLY_TEST", decision_policy=POLICIES[sport],
            controlling_engine=ENGINES[sport], supported_markets=["MONEYLINE"], kelly_fraction=0.25,
            unit_percentage=0.01, max_position_percentage=0.01, max_same_game_percentage=0.02,
            max_daily_percentage=0.05, code_commit="test", code_identity="test")
        self.session.add(exp)
        self.session.flush()
        account = self.accounts.ensure_account(sport, "2026")
        self.accounts.attach(account, exp, now=NOW, role=role, role_basis="test")
        return exp

    def game(self, sport: str, tag: str, *, kick: datetime = datetime(2026, 9, 20, 23, 0, tzinfo=UTC)):
        league = self.leagues.get(sport)
        if league is None:
            sp = Sport(code=f"SP{sport}", name=sport)
            self.session.add(sp)
            self.session.flush()
            league = League(sport_id=sp.id, code=sport, name=sport)
            self.session.add(league)
            self.session.flush()
            self.leagues[sport] = league
        home = Team(league_id=league.id, name=f"Home {tag}", abbreviation=f"H{tag}")
        away = Team(league_id=league.id, name=f"Away {tag}", abbreviation=f"A{tag}")
        self.session.add_all([home, away])
        self.session.flush()
        game = Game(league_id=league.id, starts_at=kick, status=GameStatus.SCHEDULED)
        self.session.add(game)
        self.session.flush()
        from app.models import GameTeam

        self.session.add_all([GameTeam(game_id=game.id, team_id=home.id, is_home=True),
                              GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
        market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="FULL_GAME")
        self.session.add(market)
        self.session.flush()
        outcomes = {}
        for side in ("HOME", "AWAY"):
            outcome = MarketOutcome(market_id=market.id, outcome_key=f"{side}:None", label=side.title())
            self.session.add(outcome)
            self.session.flush()
            outcomes[side] = outcome
        return game, outcomes

    def decision(self, exp, game, *, phase=ExperimentDecisionPhase.FINAL_PREGAME, selection="HOME",
                 evidence=None, slate=date(2026, 9, 20), american=110, model_p=None, ev=None, outcome=None):
        d = ShadowExperimentDecision(
            experiment_id=exp.id, game_id=game.id, slate_date=slate, phase=phase, sport=exp.sport,
            market="MONEYLINE", selection=selection, sportsbook="LowVig.ag", american_odds=american,
            decimal_odds=1 + american / 100 if american > 0 else 1 + 100 / abs(american),
            recommended_at=NOW - timedelta(hours=1), model_probability=model_p, expected_value=ev,
            recommendation="QUALIFIED_BET", recommendation_reason="test", evidence=evidence,
            market_outcome_id=outcome.id if outcome is not None else None)
        self.session.add(d)
        self.session.flush()
        return d

    def position(self, exp, outcome, decision, *, stake=2.44, decimal=2.10, american=110,
                 settled: tuple[PositionResult, float] | None = None):
        snap = OddsSnapshot(market_outcome_id=outcome.id, sportsbook_id=self.book.id, observed_at=NOW,
                            available_as_of=NOW, american_odds=american, decimal_odds=decimal,
                            status=OddsStatus.OPEN)
        self.session.add(snap)
        self.session.flush()
        pos = OfficialAspPosition(
            market_outcome_id=outcome.id, odds_snapshot_id=snap.id, placed_at=NOW, stake=stake,
            decimal_odds=decimal, result=PositionResult.PENDING, status=PositionStatus.OPEN, strategy="test",
            shadow_experiment_id=exp.id, shadow_decision_id=decision.id if decision else None,
            recommendation_policy_version=exp.recommendation_policy_version,
            staking_policy_version=exp.staking_policy_version)
        self.session.add(pos)
        self.session.flush()
        if settled:
            result, pnl = settled
            pos.status, pos.result, pos.settled_at = PositionStatus.SETTLED, result, NOW + timedelta(hours=5)
            pos.payout = stake + pnl
            self.session.add(PositionSettlement(position_id=pos.id, result=result,
                                                settled_at=NOW + timedelta(hours=5), profit_loss=pnl))
            self.session.flush()
        return pos


def mlb_evidence(raw_p: float, adj_p: float, ret: float | None, *, penalty: float = 0.02) -> dict:
    out = {"policy_version": POLICIES["MLB"], "raw_model_probability": raw_p, "adjusted_probability": adj_p,
           "market_probability": 0.45, "uncertainty_adjustment": 0.0, "starter_penalty": penalty,
           "lineup_penalty": 0.0, "uncertainty_status": "APPLIED", "adjusted_edge": adj_p - 0.45}
    if ret is not None:
        out["adjusted_expected_value"] = ret
    return out


def paper_evidence(side: str, win_p: float, ret: float, *, tie_p: float | None = None) -> dict:
    selection = {"side": side, "win_probability": win_p,
                 "terms": {"expected_return_per_dollar": ret, "no_vig_probability": 0.4, "edge": 0.05}}
    if tie_p is not None:
        selection = {"side": side, "win_probability": win_p, "tie_probability": tie_p, "expected_value": ret,
                     "settlement": "STAKE_REFUNDED_PUSH"}
    return {"outcome": "QUALIFIED", "selection": selection}


@pytest.fixture
def world(session, settings) -> World:
    return World(session, settings)


def _ledger_on_sqlite(session, monkeypatch):
    """Run the real experiment_ledger on SQLite, stubbing only its Postgres-only helpers."""

    def iso(moment):
        if isinstance(moment, str):
            moment = datetime.fromisoformat(moment)
        if moment is None:
            return None
        return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).isoformat()

    monkeypatch.setattr(command_center, "_matchups", lambda s, ids: {})
    monkeypatch.setattr(command_center, "_iso", iso)
    monkeypatch.setattr(sport_readiness, "_account", lambda s, sport: {"funded": False})
    return command_center.experiment_ledger(session, NOW)


# -- tests ---------------------------------------------------------------------

def test_totals_reconcile_to_the_experiment_ledger_for_the_same_positions(world, session, monkeypatch):
    mlb, nfl = world.experiment("MLB"), world.experiment("NFL")
    rows = [(PositionResult.WIN, 2.68), (PositionResult.LOSS, -2.44), (PositionResult.LOSS, -2.44), None]
    for i, settled in enumerate(rows):
        game, o = world.game("MLB", f"M{i}")
        d = world.decision(mlb, game, evidence=mlb_evidence(0.52, 0.50, 0.05))
        world.position(mlb, o["HOME"], d, settled=settled)
    game, o = world.game("NFL", "N1")
    d = world.decision(nfl, game, evidence=paper_evidence("HOME", 0.55, 0.06, tie_p=0.03))
    world.position(nfl, o["HOME"], d, stake=5.0, settled=(PositionResult.PUSH, 0.0))

    result = wager_results(session, now=NOW)
    ledger = _ledger_on_sqlite(session, monkeypatch)
    totals, summary = ledger["totals"], result["summary"]
    assert summary["settled"] == totals["settled_count"] == 4
    assert (summary["wins"], summary["losses"], summary["pushes"]) == (
        totals["record"]["WIN"], totals["record"]["LOSS"], totals["record"]["PUSH"])
    assert summary["net_profit"] == pytest.approx(totals["profit_loss"]) == pytest.approx(-2.20)
    assert summary["open_count"] == totals["open_count"] == 1
    assert summary["open_exposure"] == pytest.approx(totals["open_exposure"]) == pytest.approx(2.44)
    # SQLite hands raw-SQL uuids back as bare hex; compare the same identity.
    ledger_ids = {str(UUID(p["position_id"])) for s in ledger["sports"] for p in s["positions"]}
    assert ledger_ids == {r["position_id"] for r in result["rows"]}


def test_repeated_checkpoint_decisions_give_one_row_joined_to_the_funding_decision(world, session):
    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "C")
    world.decision(mlb, game, phase=ExperimentDecisionPhase.MORNING, evidence=mlb_evidence(0.60, 0.58, 0.22))
    world.decision(mlb, game, phase=ExperimentDecisionPhase.LINEUP_CONFIRMED,
                   evidence=mlb_evidence(0.58, 0.56, 0.18))
    funding = world.decision(mlb, game, evidence=mlb_evidence(0.55, 0.53, 0.08))
    world.position(mlb, o["HOME"], funding, settled=(PositionResult.LOSS, -2.44))

    result = wager_results(session, now=NOW)
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["decision_id"] == str(funding.id) and row["decision_phase"] == "FINAL_PREGAME"
    assert row["recorded_return"] == pytest.approx(0.08) and row["return_band"] == "5to10"
    assert row["raw_probability"] == pytest.approx(0.55) and row["adjusted_probability"] == pytest.approx(0.53)
    assert result["summary"]["settled"] == 1


def test_house_divided_copies_and_unfunded_shadows_are_excluded(world, session):
    funded = world.experiment("MLB")
    control = world.experiment("MLB", role=MembershipRole.UNFUNDED_CONTROL)
    game, o = world.game("MLB", "H")
    d = world.decision(funded, game, evidence=mlb_evidence(0.52, 0.50, 0.05))
    world.position(funded, o["HOME"], d, settled=(PositionResult.LOSS, -2.44))
    shadow_d = world.decision(control, game, evidence=mlb_evidence(0.52, 0.50, 0.05))
    world.position(control, o["HOME"], shadow_d, stake=50, settled=(PositionResult.WIN, 55.0))
    # A House Divided mirror of the funded decision, settled as a win.
    session.add(CompetitionWager(
        account_id=uuid4(), participant_id=uuid4(), sport="MLB", source="MODEL_MIRROR", status="SETTLED",
        game_id=game.id, market_outcome_id=o["HOME"].id, stake=Decimal("100.00"), decimal_odds=2.1,
        source_decision_id=d.id, source_experiment_id=funded.id, result="WIN", profit_loss=Decimal("110.00")))
    session.flush()

    result = wager_results(session, now=NOW)
    assert [r["decision_id"] for r in result["rows"]] == [str(d.id)]
    assert result["summary"]["net_profit"] == pytest.approx(-2.44)
    assert result["summary"]["settled_stake"] == pytest.approx(2.44)
    assert result["population"]["positions_outside_funded_eras"] == 1


def test_away_selection_uses_the_away_probability(world, session):
    mlb, cfb = world.experiment("MLB"), world.experiment("CFB")
    game, o = world.game("MLB", "A")
    # MLB records the selected side's probability; the home side would be 0.58.
    d = world.decision(mlb, game, selection="AWAY", evidence=mlb_evidence(0.42, 0.40, 0.03), model_p=0.42)
    world.position(mlb, o["AWAY"], d, settled=(PositionResult.WIN, 2.68))
    game2, o2 = world.game("CFB", "B")
    d2 = world.decision(cfb, game2, selection="AWAY", evidence=paper_evidence("AWAY", 0.35, 0.12))
    world.position(cfb, o2["AWAY"], d2, stake=4.9, decimal=3.2, american=220)
    # A decision whose recorded side disagrees with the position's outcome is never reported as that side.
    game3, o3 = world.game("CFB", "X")
    d3 = world.decision(cfb, game3, selection="HOME", evidence=paper_evidence("HOME", 0.70, 0.09))
    world.position(cfb, o3["AWAY"], d3, stake=1.0)

    rows = {r["decision_id"]: r for r in wager_results(session, now=NOW)["rows"]}
    mlb_row = rows[str(d.id)]
    assert mlb_row["selected_side"] == "AWAY" and mlb_row["selection"] == "AA ML"
    assert mlb_row["raw_probability"] == pytest.approx(0.42) and mlb_row["probability_band"] == "40to50"
    cfb_row = rows[str(d2.id)]
    assert cfb_row["selected_side"] == "AWAY" and cfb_row["raw_probability"] == pytest.approx(0.35)
    assert cfb_row["probability_band"] == "lt40" and cfb_row["adjusted_probability"] is None
    bad = rows[str(d3.id)]
    assert bad["binding_status"] == "CONFLICT_SIDE" and "CONFLICT_SIDE" in bad["flags"]
    assert bad["raw_probability"] is None and bad["probability_band"] == "missing"


def test_team_keyed_outcome_resolves_to_its_side_from_the_participant_team(world, session):
    """MLB outcomes are keyed by team, not HOME/AWAY: the side comes from the outcome's participant."""

    from sqlalchemy import select as sa_select

    from app.models import GameTeam

    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "T")
    away_team = session.scalar(sa_select(GameTeam.team_id).where(GameTeam.game_id == game.id,
                                                                  GameTeam.is_home.is_(False)))
    team_outcome = MarketOutcome(market_id=o["AWAY"].market_id, outcome_key=f"team:{away_team}",
                                 label="Away T", participant_team_id=away_team)
    session.add(team_outcome)
    session.flush()
    d = world.decision(mlb, game, selection="Away T", evidence=mlb_evidence(0.44, 0.42, 0.04))
    world.position(mlb, team_outcome, d)
    row = wager_results(session, now=NOW)["rows"][0]
    assert row["selected_side"] == "AWAY" and row["selection"] == "Away T"
    assert row["binding_status"] == "OK" and row["flags"] == []
    assert row["raw_probability"] == pytest.approx(0.44)
    assert row["comparison_probability"] == pytest.approx(0.44)  # MLB cannot tie: raw p unchanged


def _team_outcome(session, game, o, *, home: bool, label: str):
    from sqlalchemy import select as sa_select

    from app.models import GameTeam

    team = session.scalar(sa_select(GameTeam.team_id).where(GameTeam.game_id == game.id,
                                                             GameTeam.is_home.is_(home)))
    outcome = MarketOutcome(market_id=o["HOME"].market_id, outcome_key=f"{label.lower()}:None", label=label,
                            participant_team_id=team)
    session.add(outcome)
    session.flush()
    return outcome


def _withheld(result, decision_id, status):
    row = next(r for r in result["rows"] if r["decision_id"] == str(decision_id))
    assert row["binding_status"] == status and status in row["flags"]
    for key in ("raw_probability", "adjusted_probability", "recorded_return", "estimated_profit",
                "comparison_probability"):
        assert row[key] is None, key
    assert row["return_band"] == "missing" and row["probability_band"] == "missing"
    # The position and its settlement stay.
    assert row["settled"] and row["net_profit"] == pytest.approx(-2.44)
    return row


def test_home_mlb_decision_attached_to_an_away_position_contributes_no_estimate(world, session):
    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "W")
    away = _team_outcome(session, game, o, home=False, label="Away W")
    d = world.decision(mlb, game, selection="HW ML", evidence=mlb_evidence(0.70, 0.68, 0.30))
    world.position(mlb, away, d, settled=(PositionResult.LOSS, -2.44))
    result = wager_results(session, now=NOW)
    _withheld(result, d.id, "CONFLICT_SIDE")
    s = result["summary"]
    assert s["estimated_profit"] is None and s["expected_wins"] is None and s["avg_raw_probability"] is None
    assert s["settled"] == 1 and s["net_profit"] == pytest.approx(-2.44) and s["binding_excluded"] == 1
    bands = {b["key"]: b for b in result["return_bands"]}
    assert bands["missing"]["settled"] == 1 and bands["ge20"]["settled"] == 0


def test_decision_from_another_game_contributes_no_estimate(world, session):
    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "G1")
    other, _ = world.game("MLB", "G2")
    d = world.decision(mlb, other, evidence=mlb_evidence(0.61, 0.59, 0.12))
    world.position(mlb, o["HOME"], d, settled=(PositionResult.LOSS, -2.44))
    result = wager_results(session, now=NOW)
    _withheld(result, d.id, "CONFLICT_GAME")
    assert result["summary"]["estimated_profit"] is None and result["summary"]["expected_wins"] is None


def test_decision_from_another_experiment_contributes_no_estimate(world, session):
    mlb, other = world.experiment("MLB"), world.experiment("MLB")
    game, o = world.game("MLB", "X1")
    d = world.decision(other, game, evidence=mlb_evidence(0.61, 0.59, 0.12))
    world.position(mlb, o["HOME"], d, settled=(PositionResult.LOSS, -2.44))
    result = wager_results(session, now=NOW)
    _withheld(result, d.id, "CONFLICT_EXPERIMENT")
    assert result["summary"]["estimated_profit"] is None and result["summary"]["expected_wins"] is None


def _outcome(session, o, key: str, label: str, team):
    outcome = MarketOutcome(market_id=o["HOME"].market_id, outcome_key=key, label=label, participant_team_id=team)
    session.add(outcome)
    session.flush()
    return outcome


def _team(session, game, *, home: bool):
    from sqlalchemy import select as sa_select

    from app.models import GameTeam

    return session.scalar(sa_select(GameTeam.team_id).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(home)))


def test_position_outcome_with_contradictory_side_signals_is_withheld(world, session):
    """Codex WR-2B: a HOME:None key with the away team as participant and an AWAY label is no side."""

    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "Q")
    bad = _outcome(session, o, "HOME:alt", "AWAY", _team(session, game, home=False))
    d = world.decision(mlb, game, selection="HOME", evidence=mlb_evidence(0.70, 0.68, 0.30))
    world.position(mlb, bad, d, settled=(PositionResult.LOSS, -2.44))
    result = wager_results(session, now=NOW)
    row = _withheld(result, d.id, "CONFLICT_OUTCOME_SIDE_SIGNALS")
    assert row["selected_side"] is None
    assert result["summary"]["estimated_profit"] is None and result["summary"]["expected_wins"] is None


def test_decision_outcome_with_contradictory_side_signals_is_withheld(world, session):
    """The same contradictory outcome referenced as the decision's own outcome fails closed too."""

    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "R")
    bad = _outcome(session, o, "HOME:alt", "AWAY", _team(session, game, home=False))
    d = world.decision(mlb, game, selection="HOME", evidence=mlb_evidence(0.70, 0.68, 0.30), outcome=bad)
    world.position(mlb, o["HOME"], d, settled=(PositionResult.LOSS, -2.44))
    _withheld(wager_results(session, now=NOW), d.id, "CONFLICT_DECISION_OUTCOME_SIDE_SIGNALS")


def test_consistent_key_participant_and_label_bind(world, session):
    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "V")
    good = _outcome(session, o, "HOME:alt", "Home V", _team(session, game, home=True))
    d = world.decision(mlb, game, selection="HV ML", evidence=mlb_evidence(0.56, 0.54, 0.06), outcome=good)
    world.position(mlb, good, d, settled=(PositionResult.WIN, 2.68))
    row = wager_results(session, now=NOW)["rows"][0]
    assert row["binding_status"] == "OK" and row["selected_side"] == "HOME" and row["flags"] == []
    assert row["recorded_return"] == pytest.approx(0.06)


def test_invalid_nfl_win_tie_pair_is_flagged_and_not_compared(world, session):
    """Codex WR-1B: pWin .8 with pTie .4 is not a probability pair; nothing is derived, clamped or summed."""

    nfl = world.experiment("NFL")
    game, o = world.game("NFL", "D1")
    d = world.decision(nfl, game, evidence=paper_evidence("HOME", 0.8, 0.10, tie_p=0.4))
    world.position(nfl, o["HOME"], d, stake=5.0, settled=(PositionResult.WIN, 5.0))
    game2, o2 = world.game("NFL", "D2")
    d2 = world.decision(nfl, game2, evidence=paper_evidence("HOME", 0.55, 0.05, tie_p=0.03))
    world.position(nfl, o2["HOME"], d2, stake=5.0, settled=(PositionResult.LOSS, -5.0))
    result = wager_results(session, now=NOW)
    rows = {r["decision_id"]: r for r in result["rows"]}
    bad, good = rows[str(d.id)], rows[str(d2.id)]
    assert "INVALID_PROBABILITY_DOMAIN" in bad["flags"] and bad["comparison_probability"] is None
    assert bad["raw_probability"] == pytest.approx(0.8) and bad["tie_probability"] == pytest.approx(0.4)  # visible
    assert bad["probability_band"] == "missing"
    assert good["comparison_probability"] == pytest.approx(0.55 / 0.97) and good["flags"] == []
    s = result["summary"]
    assert s["comparison_n"] == 1 and s["expected_wins"] == pytest.approx(0.55 / 0.97)
    assert s["avg_raw_probability"] == pytest.approx(0.55)


@pytest.mark.parametrize(("p_win", "p_tie", "expected"), [
    (5e-10, 0.999999999999, None),  # Codex boundary: passes the pair tolerance, derives ~500
    (0.97, 0.03, 1.0),              # valid upper boundary
    (0.0, 0.5, 0.0),                # valid lower boundary
])
def test_derived_non_tie_probability_is_itself_bounded(world, session, p_win, p_tie, expected):
    nfl = world.experiment("NFL")
    game, o = world.game("NFL", "BD")
    d = world.decision(nfl, game, evidence=paper_evidence("HOME", p_win, 0.02, tie_p=p_tie))
    world.position(nfl, o["HOME"], d, stake=5.0, settled=(PositionResult.LOSS, -5.0))
    result = wager_results(session, now=NOW)
    row, s = result["rows"][0], result["summary"]
    assert row["raw_probability"] == pytest.approx(p_win) and row["tie_probability"] == pytest.approx(p_tie)
    assert row["settled"] and row["net_profit"] == pytest.approx(-5.0)
    if expected is None:
        assert "INVALID_PROBABILITY_DOMAIN" in row["flags"] and row["comparison_probability"] is None
        assert row["probability_band"] == "missing"
        assert s["comparison_n"] == 0 and s["expected_wins"] is None
    else:
        assert row["flags"] == [] and row["comparison_probability"] == pytest.approx(expected)
        assert s["comparison_n"] == 1 and s["expected_wins"] == pytest.approx(expected)


def test_out_of_range_raw_probability_is_flagged_for_any_sport(world, session):
    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "D3")
    d = world.decision(mlb, game, evidence=mlb_evidence(1.2, 1.1, 0.30))
    world.position(mlb, o["HOME"], d, settled=(PositionResult.WIN, 2.68))
    result = wager_results(session, now=NOW)
    row = result["rows"][0]
    assert "INVALID_PROBABILITY_DOMAIN" in row["flags"] and row["comparison_probability"] is None
    assert row["raw_probability"] == pytest.approx(1.2) and row["probability_band"] == "missing"
    assert result["summary"]["expected_wins"] is None and result["summary"]["avg_raw_probability"] is None


def test_open_positions_are_kept_out_of_settled_results(world, session):
    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "S")
    d = world.decision(mlb, game, evidence=mlb_evidence(0.52, 0.50, 0.05))
    world.position(mlb, o["HOME"], d, stake=2.00, settled=(PositionResult.LOSS, -2.00))
    game2, o2 = world.game("MLB", "O")
    d2 = world.decision(mlb, game2, evidence=mlb_evidence(0.60, 0.58, 0.25))
    world.position(mlb, o2["HOME"], d2, stake=3.00)

    s = wager_results(session, now=NOW)["summary"]
    assert (s["settled"], s["settled_stake"], s["net_profit"]) == (1, 2.00, -2.00)
    assert s["estimated_profit"] == pytest.approx(0.10)  # 2.00 x 0.05, settled only
    assert (s["open_count"], s["open_exposure"]) == (1, 3.00)
    assert s["open_estimated_profit"] == pytest.approx(0.75)
    bands = {b["key"]: b for b in wager_results(session, now=NOW)["return_bands"]}
    assert bands["ge20"]["settled"] == 0 and bands["ge20"]["open_count"] == 1


def test_push_refunds_the_stake_and_leaves_the_win_rate_denominator(world, session):
    nfl = world.experiment("NFL")
    for i, (res, pnl) in enumerate([(PositionResult.PUSH, 0.0), (PositionResult.WIN, 5.0),
                                    (PositionResult.LOSS, -5.0)]):
        game, o = world.game("NFL", f"P{i}")
        d = world.decision(nfl, game, evidence=paper_evidence("HOME", 0.55, 0.06, tie_p=0.03))
        world.position(nfl, o["HOME"], d, stake=5.0, decimal=2.0, american=100, settled=(res, pnl))

    s = wager_results(session, now=NOW)["summary"]
    assert (s["wins"], s["losses"], s["pushes"], s["settled"], s["decided"]) == (1, 1, 1, 3, 2)
    assert s["settled_stake"] == pytest.approx(15.0) and s["net_profit"] == pytest.approx(0.0)
    assert s["win_rate"] == pytest.approx(0.5)
    # Decided-only comparison: derived non-tie probability 0.55 / 0.97 on the two decided wagers.
    assert s["comparison_n"] == 2 and s["tie_derived_n"] == 2
    assert s["expected_wins"] == pytest.approx(2 * 0.55 / 0.97)
    assert s["avg_comparison_probability"] == pytest.approx(0.55 / 0.97)
    assert s["avg_raw_probability"] == pytest.approx(0.55)  # recorded raw p shown unchanged
    assert s["estimated_profit"] == pytest.approx(0.90)  # recorded tie-aware returns, all three stakes


def test_nfl_calibrated_ties_compare_on_the_same_denominator(world, session):
    """Codex fixture: pWin .4, pTie .2, 4W/4L/2P is calibrated and must read as such."""

    nfl = world.experiment("NFL")
    outcomes = [(PositionResult.WIN, 5.0)] * 4 + [(PositionResult.LOSS, -5.0)] * 4 + [(PositionResult.PUSH, 0.0)] * 2
    for i, (res, pnl) in enumerate(outcomes):
        game, o = world.game("NFL", f"K{i}")
        d = world.decision(nfl, game, evidence=paper_evidence("HOME", 0.4, 0.02, tie_p=0.2))
        world.position(nfl, o["HOME"], d, stake=5.0, decimal=2.0, american=100, settled=(res, pnl))
    s = wager_results(session, now=NOW)["summary"]
    assert (s["wins"], s["losses"], s["pushes"], s["decided"], s["comparison_n"]) == (4, 4, 2, 8, 8)
    assert s["expected_wins"] == pytest.approx(4.0) and s["actual_wins_with_probability"] == 4
    assert s["avg_comparison_probability"] == pytest.approx(0.5) and s["comparison_win_rate"] == pytest.approx(0.5)
    assert s["avg_raw_probability"] == pytest.approx(0.4)
    band = next(b for b in wager_results(session, now=NOW)["probability_bands"] if b["key"] == "40to50")
    assert band["expected_wins"] == pytest.approx(4.0) and band["comparison_win_rate"] == pytest.approx(0.5)


def test_nfl_without_a_recorded_tie_probability_is_left_out_of_the_comparison(world, session):
    nfl = world.experiment("NFL")
    game, o = world.game("NFL", "NT")
    evidence = {"outcome": "QUALIFIED", "selection": {"side": "HOME", "win_probability": 0.55, "expected_value": 0.05}}
    d = world.decision(nfl, game, evidence=evidence)
    world.position(nfl, o["HOME"], d, stake=5.0, settled=(PositionResult.WIN, 5.0))
    result = wager_results(session, now=NOW)
    row = result["rows"][0]
    assert "TIE_PROBABILITY_NOT_RECORDED" in row["flags"] and row["binding_status"] == "OK"
    assert row["raw_probability"] == pytest.approx(0.55) and row["comparison_probability"] is None
    assert row["recorded_return"] == pytest.approx(0.05)
    s = result["summary"]
    assert s["comparison_n"] == 0 and s["expected_wins"] is None and s["decided"] == 1


def test_band_assignment_including_missing(world, session):
    assert band_of(-0.01, RETURN_BANDS) == "lt0"
    assert band_of(0.0, RETURN_BANDS) == "0to5"
    assert band_of(0.05, RETURN_BANDS) == "5to10"
    assert band_of(0.1999, RETURN_BANDS) == "10to20"
    assert band_of(0.20, RETURN_BANDS) == "ge20"
    assert band_of(None, RETURN_BANDS) == "missing"
    assert band_of(0.3999, PROBABILITY_BANDS) == "lt40"
    assert band_of(0.70, PROBABILITY_BANDS) == "ge70"

    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "Z")
    d = world.decision(mlb, game, evidence=mlb_evidence(0.52, 0.50, None))
    world.position(mlb, o["HOME"], d, settled=(PositionResult.LOSS, -2.44))
    result = wager_results(session, now=NOW)
    row = result["rows"][0]
    assert row["recorded_return"] is None and row["return_band"] == "missing"
    assert "RETURN_NOT_RECORDED" in row["flags"]
    bands = {b["key"]: b for b in result["return_bands"]}
    assert [b["key"] for b in result["return_bands"]] == ["lt0", "0to5", "5to10", "10to20", "ge20", "missing"]
    assert bands["missing"]["settled"] == 1 and bands["missing"]["estimated_profit"] is None
    assert result["summary"]["estimated_n"] == 0


def test_scope_filters_and_labels(world, session):
    mlb, cfb = world.experiment("MLB"), world.experiment("CFB")
    game, o = world.game("MLB", "F1")
    d = world.decision(mlb, game, evidence=mlb_evidence(0.52, 0.50, 0.05), slate=date(2026, 9, 18))
    world.position(mlb, o["HOME"], d, settled=(PositionResult.WIN, 2.68))
    game2, o2 = world.game("CFB", "F2")
    d2 = world.decision(cfb, game2, evidence=paper_evidence("HOME", 0.6, 0.1), slate=date(2026, 9, 20))
    world.position(cfb, o2["HOME"], d2, stake=4.0, settled=(PositionResult.LOSS, -4.0))

    everything = wager_results(session, now=NOW)
    assert everything["scope"]["label"].startswith("All official funded positions — all sports")
    assert everything["summary"]["settled"] == 2
    only_mlb = wager_results(session, sport="MLB", engine="MLB_V18_WHOLE_GAME", now=NOW)
    assert only_mlb["scope"]["label"].startswith("Official funded positions — MLB · MLB V18 engine")
    assert [r["sport"] for r in only_mlb["rows"]] == ["MLB"]
    dated = wager_results(session, date_from="2026-09-19", date_to="2026-09-20", now=NOW)
    assert [r["sport"] for r in dated["rows"]] == ["CFB"]
    assert dated["date_basis"]["field"] == "slate_date"
    assert {e["id"] for e in everything["options"]["eras"]} == {str(mlb.id), str(cfb.id)}


def test_wilson_interval_is_bounded_and_centred():
    lo, hi = wilson(32, 85)
    assert 0.27 < lo < 0.29 and 0.47 < hi < 0.49
    assert wilson(0, 0) is None


def test_endpoint_is_public_read_only_and_rejects_an_unknown_sport(client, world, session):
    mlb = world.experiment("MLB")
    game, o = world.game("MLB", "E")
    d = world.decision(mlb, game, evidence=mlb_evidence(0.52, 0.50, 0.05))
    world.position(mlb, o["HOME"], d, settled=(PositionResult.LOSS, -2.44))
    session.commit()
    response = client.get("/api/v1/research/wager-results?sport=MLB")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "AISP_WAGER_RESULTS_V1"
    assert body["population"]["key"] == "OFFICIAL_FUNDED_POSITIONS"
    assert len(body["rows"]) == 1
    assert client.get("/api/v1/research/wager-results?sport=XFL").status_code == 404
