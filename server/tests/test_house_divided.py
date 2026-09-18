"""A House Divided: the risks the owner named, each pinned.

independent balances - locked pregame terms - authorized entry - automatic
model copying - insufficient funds - duplicate imports - win/loss/push/void
settlement -- plus the accounting rule that a placed wager never dents the
bankroll chart, and that the official ledger is only ever read.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.models import (
    CompetitionAccount, CompetitionWager, ExperimentDecisionPhase, Game, GameStatus, GameTeam, League,
    Market, MarketOutcome, MarketType, OddsSnapshot, OfficialAspPosition, Season, ShadowExperiment,
    ShadowExperimentDecision, Sport, Sportsbook, Team, User, UserRole, UserStatus,
)
from app.services import house_divided as hd

T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)          # competition start
KICK = datetime(2026, 9, 18, 23, 0, tzinfo=UTC)        # first pitch


# -- fixtures ----------------------------------------------------------------
@pytest.fixture
def world(session: Session):
    ctx = {"session": session, "leagues": {}}
    for code, sport_code in (("MLB", "BASEBALL"), ("NFL", "FOOTBALL")):
        sport = Sport(code=sport_code, name=sport_code.title())
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code=code, name=code)
        session.add(league)
        session.flush()
        season = Season(league_id=league.id, name="2026", starts_at=datetime(2026, 3, 1, tzinfo=UTC),
                        ends_at=datetime(2027, 2, 1, tzinfo=UTC))
        session.add(season)
        session.flush()
        ctx["leagues"][code] = (league, season)
    book = Sportsbook(key="draftkings", name="DraftKings")
    session.add(book)
    session.flush()
    ctx["book"] = book
    ctx["experiment"] = ShadowExperiment(
        name="MLB V18", version=18, started_at=datetime(2026, 9, 12, tzinfo=UTC),
        start_date=date(2026, 9, 12), starting_bankroll=1000, sport="MLB",
        recommendation_policy_version=hd.MLB_POLICY, staking_policy_version="AISP_QUARTER_KELLY_EVIDENCE_V2",
        decision_policy=hd.MLB_POLICY, controlling_engine="V18", supported_markets=["MONEYLINE"],
        kelly_fraction=0.25, unit_percentage=0.01, max_position_percentage=0.02,
        max_same_game_percentage=0.03, max_daily_percentage=0.10, code_commit="test",
        code_identity="test", is_active=True, model_era_id="ERA_TEST")
    session.add(ctx["experiment"])
    session.flush()
    hd.ensure_competition(session)
    ctx["admin"] = _user(session, "admin@example.test", UserRole.ADMIN)
    return ctx


def _user(session, email, role=UserRole.MEMBER) -> User:
    user = User(email=email, password_hash=hash_password("x-not-a-real-password-1"), display_name=email.split("@")[0],
                role=role, status=UserStatus.ACTIVE)
    session.add(user)
    session.flush()
    return user


def add_game(ctx, code="MLB", *, kick=KICK, tag="A"):
    session = ctx["session"]
    league, season = ctx["leagues"][code]
    home = Team(league_id=league.id, name=f"Home{tag}", abbreviation=f"H{tag}")
    away = Team(league_id=league.id, name=f"Away{tag}", abbreviation=f"A{tag}")
    session.add_all([home, away])
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=kick, status=GameStatus.SCHEDULED,
                source_mode="LIVE")
    session.add(game)
    session.flush()
    session.add_all([GameTeam(game_id=game.id, team_id=home.id, is_home=True),
                     GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
    markets = {}
    for mtype in (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL):
        market = Market(game_id=game.id, market_type=mtype, period="full_game")
        session.add(market)
        session.flush()
        markets[mtype] = market
    outcomes = {
        "home_ml": MarketOutcome(market_id=markets[MarketType.MONEYLINE].id, outcome_key="home",
                                 label=home.name, participant_team_id=home.id),
        "away_ml": MarketOutcome(market_id=markets[MarketType.MONEYLINE].id, outcome_key="away",
                                 label=away.name, participant_team_id=away.id),
        "home_-1.5": MarketOutcome(market_id=markets[MarketType.SPREAD].id, outcome_key="home:-1.5",
                                   label=home.name, participant_team_id=home.id),
        "away_+1.5": MarketOutcome(market_id=markets[MarketType.SPREAD].id, outcome_key="away:1.5",
                                   label=away.name, participant_team_id=away.id),
        "over_8.5": MarketOutcome(market_id=markets[MarketType.TOTAL].id, outcome_key="over:8.5", label="Over"),
        "under_8.5": MarketOutcome(market_id=markets[MarketType.TOTAL].id, outcome_key="under:8.5", label="Under"),
        "over_9": MarketOutcome(market_id=markets[MarketType.TOTAL].id, outcome_key="over:9.0", label="Over"),
    }
    session.add_all(outcomes.values())
    session.flush()
    return game, outcomes


def quote(ctx, outcome, *, at, american=100, point=None):
    decimal = 1 + (american / 100 if american > 0 else 100 / -american)
    snap = OddsSnapshot(market_outcome_id=outcome.id, sportsbook_id=ctx["book"].id, observed_at=at,
                        available_as_of=at, american_odds=american, decimal_odds=decimal, point=point,
                        source="test")
    ctx["session"].add(snap)
    ctx["session"].flush()
    return snap


def start(ctx, sport="MLB"):
    hd.start_sport(ctx["session"], sport, actor=ctx["admin"], now=T0)


def link(ctx, key, user):
    participant = hd.participant_by_key(ctx["session"], key)
    participant.user_id = user.id
    ctx["session"].flush()


def final(game, home, away):
    game.status = GameStatus.FINAL
    game.home_score = home
    game.away_score = away


def place(ctx, key, snap, stake, *, now, actor=None):
    wager = hd.save_draft(ctx["session"], participant_key=key, odds_snapshot_id=snap.id, stake=stake,
                          actor=actor or ctx["admin"], now=now)
    return hd.submit(ctx["session"], wager_id=wager.id, actor=actor or ctx["admin"], now=now)


def account(ctx, key, sport="MLB"):
    return hd.account_for(ctx["session"], hd.participant_by_key(ctx["session"], key), sport)


def decision(ctx, game, outcome, snap, *, at, recommendation="QUALIFIED_BET",
             phase=ExperimentDecisionPhase.FINAL_PREGAME, experiment=None, sport="MLB", evidence=None):
    row = ShadowExperimentDecision(
        experiment_id=(experiment or ctx["experiment"]).id, game_id=game.id, slate_date=game.starts_at.date(),
        phase=phase, evidence=evidence,
        sport=sport, market="MONEYLINE", period="full_game", selection=outcome.label,
        sportsbook="draftkings", american_odds=snap.american_odds, decimal_odds=snap.decimal_odds,
        market_outcome_id=outcome.id, odds_snapshot_id=snap.id, quote_observed_at=snap.observed_at,
        forecast_version="v18", forecast_cutoff=at, recommended_at=at, model_probability=0.6,
        recommendation=recommendation, recommendation_reason="test", qualification_reasons=[],
        source_mode="LIVE")
    ctx["session"].add(row)
    ctx["session"].flush()
    return row


# -- start -------------------------------------------------------------------
def test_only_sports_with_an_official_pipeline_can_start_and_never_twice(world):
    start(world)
    with pytest.raises(hd.CompetitionError) as twice:
        start(world)
    assert twice.value.code == "ALREADY_STARTED"
    with pytest.raises(hd.CompetitionError) as nba:
        hd.start_sport(world["session"], "NBA", actor=world["admin"], now=T0)
    assert nba.value.code == "NO_ELIGIBLE_MODEL_PIPELINE"
    nba_card = hd.summary(world["session"], "NBA", now=T0)
    assert nba_card["status"] == "NOT_STARTED"
    assert all(card["bankroll"] == 1000.0 for card in nba_card["standings"])


# -- independent balances ----------------------------------------------------
def test_balances_are_independent_across_people_and_sports(world):
    start(world)
    game, o = add_game(world)
    snap = quote(world, o["home_ml"], at=T0 + timedelta(hours=1), american=150)
    place(world, "SCOTTY", snap, "100.00", now=T0 + timedelta(hours=2))
    s = hd.balance(world["session"], account(world, "SCOTTY"))
    assert (s.bankroll, s.available, s.open_stake) == (Decimal("1000.00"), Decimal("900.00"), Decimal("100.00"))
    for key, sport in (("RED_LOU", "MLB"), ("JAMES", "MLB"), ("SCOTTY", "NFL")):
        other = hd.balance(world["session"], account(world, key, sport))
        assert other.available == Decimal("1000.00"), (key, sport)


# -- locked pregame terms ----------------------------------------------------
def test_a_submitted_wager_is_locked_and_the_server_enforces_the_cutoff(world):
    start(world)
    game, o = add_game(world)
    snap = quote(world, o["home_ml"], at=KICK - timedelta(hours=1), american=-120)
    wager = place(world, "SCOTTY", snap, "50", now=KICK - timedelta(minutes=30))
    assert wager.status == "OPEN" and wager.american_odds == -120 and wager.odds_snapshot_id == snap.id
    with pytest.raises(hd.CompetitionError) as edit:
        hd.save_draft(world["session"], participant_key="SCOTTY", odds_snapshot_id=snap.id, stake="75",
                      actor=world["admin"], wager_id=wager.id, now=KICK - timedelta(minutes=29))
    assert edit.value.code == "LOCKED"
    with pytest.raises(hd.CompetitionError) as late:
        place(world, "RED_LOU", snap, "10", now=KICK - timedelta(minutes=14))
    assert late.value.code == "PAST_CUTOFF"


def test_a_draft_saved_early_cannot_be_submitted_after_the_cutoff(world):
    start(world)
    game, o = add_game(world)
    snap = quote(world, o["home_ml"], at=KICK - timedelta(hours=1))
    draft = hd.save_draft(world["session"], participant_key="RED_LOU", odds_snapshot_id=snap.id, stake="10",
                          actor=world["admin"], now=KICK - timedelta(minutes=40))
    with pytest.raises(hd.CompetitionError) as late:
        hd.submit(world["session"], wager_id=draft.id, actor=world["admin"], now=KICK - timedelta(minutes=10))
    assert late.value.code == "PAST_CUTOFF"
    assert world["session"].get(CompetitionWager, draft.id).status == "DRAFT"


def test_stale_and_superseded_prices_are_refused(world):
    start(world)
    game, o = add_game(world)
    old = quote(world, o["home_ml"], at=KICK - timedelta(hours=6))
    with pytest.raises(hd.CompetitionError) as stale:
        place(world, "SCOTTY", old, "10", now=KICK - timedelta(hours=1))
    assert stale.value.code == "STALE_PRICE"
    fresh = quote(world, o["away_ml"], at=KICK - timedelta(hours=2), american=110)
    draft = hd.save_draft(world["session"], participant_key="SCOTTY", odds_snapshot_id=fresh.id, stake="10",
                          actor=world["admin"], now=KICK - timedelta(hours=2))
    quote(world, o["away_ml"], at=KICK - timedelta(hours=1), american=105)  # the book moved
    with pytest.raises(hd.CompetitionError) as moved:
        hd.submit(world["session"], wager_id=draft.id, actor=world["admin"], now=KICK - timedelta(minutes=50))
    assert moved.value.code == "STALE_PRICE"


def test_picks_before_the_recorded_start_are_refused(world):
    game, o = add_game(world, kick=T0 - timedelta(hours=1), tag="E")
    start(world)
    snap = quote(world, o["home_ml"], at=T0 - timedelta(hours=2))
    with pytest.raises(hd.CompetitionError) as early:
        place(world, "SCOTTY", snap, "10", now=T0 - timedelta(hours=2))
    assert early.value.code in {"BEFORE_START", "PAST_CUTOFF"}


# -- authorized entry --------------------------------------------------------
def test_only_the_linked_login_or_an_admin_may_enter_and_both_are_kept(world):
    start(world)
    game, o = add_game(world)
    snap = quote(world, o["home_ml"], at=KICK - timedelta(hours=1))
    scotty = _user(world["session"], "scotty@example.test")
    stranger = _user(world["session"], "stranger@example.test")
    link(world, "SCOTTY", scotty)
    now = KICK - timedelta(minutes=45)
    own = place(world, "SCOTTY", snap, "10", now=now, actor=scotty)
    assert own.entered_by_user_id == scotty.id
    with pytest.raises(hd.CompetitionError) as denied:
        place(world, "SCOTTY", snap, "10", now=now, actor=stranger)
    assert denied.value.code == "NOT_AUTHORIZED"
    with pytest.raises(hd.CompetitionError) as cross:
        place(world, "RED_LOU", snap, "10", now=now, actor=scotty)
    assert cross.value.code == "NOT_AUTHORIZED"
    behalf = place(world, "RED_LOU", snap, "10", now=now)
    assert behalf.entered_by_user_id == world["admin"].id
    assert hd.participant_by_key(world["session"], "RED_LOU").id == behalf.participant_id
    with pytest.raises(hd.CompetitionError) as model:
        place(world, "JAMES", snap, "10", now=now)
    assert model.value.code == "MODEL_ACCOUNT"


# -- insufficient funds ------------------------------------------------------
def test_an_unfunded_stake_is_refused(world):
    start(world)
    game, o = add_game(world)
    snap = quote(world, o["home_ml"], at=KICK - timedelta(hours=1))
    now = KICK - timedelta(minutes=45)
    place(world, "SCOTTY", snap, "990", now=now)
    with pytest.raises(hd.CompetitionError) as broke:
        place(world, "SCOTTY", snap, "10.01", now=now)
    assert broke.value.code == "INSUFFICIENT_FUNDS"
    for bad in ("0", "-5", "1.005", "abc"):
        with pytest.raises(hd.CompetitionError):
            hd.save_draft(world["session"], participant_key="SCOTTY", odds_snapshot_id=snap.id, stake=bad,
                          actor=world["admin"], now=now)


# -- automatic model copying, once ------------------------------------------
def test_official_decisions_are_copied_once_after_the_cutoff_and_a_pass_stays_a_pass(world):
    start(world)
    game, o = add_game(world, tag="M")
    passed_game, po = add_game(world, tag="P")
    snap = quote(world, o["home_ml"], at=KICK - timedelta(minutes=40), american=100)
    psnap = quote(world, po["home_ml"], at=KICK - timedelta(minutes=40), american=100)
    decided = decision(world, game, o["home_ml"], snap, at=KICK - timedelta(minutes=20))
    decision(world, passed_game, po["home_ml"], psnap, at=KICK - timedelta(minutes=20), recommendation="NO_BET")

    before_cutoff = hd.mirror_model_decisions(world["session"], now=KICK - timedelta(minutes=16))
    assert before_cutoff["imported"] == 0, "not final until the cutoff passes"

    after = hd.mirror_model_decisions(world["session"], now=KICK - timedelta(minutes=10))
    again = hd.mirror_model_decisions(world["session"], now=KICK - timedelta(minutes=5))
    assert after["imported"] == 1 and again["imported"] == 0
    rows = list(world["session"].scalars(select(CompetitionWager).where(CompetitionWager.source == "MODEL_MIRROR")))
    assert len(rows) == 1
    wager = rows[0]
    assert wager.source_decision_id == decided.id and wager.odds_snapshot_id == snap.id
    assert wager.american_odds == 100 and wager.model_version == "v18"
    assert wager.submitted_at.replace(tzinfo=UTC) == KICK - timedelta(minutes=20)
    # Quarter-Kelly at p=.6 on evens is 5% -> capped by the 2% position cap.
    assert wager.stake == Decimal("20.00")

    board = hd.summary(world["session"], "MLB", now=KICK - timedelta(minutes=5))
    passed = next(g for g in board["games"] if g["game_id"] == str(passed_game.id))
    james = next(c for c in passed["cells"] if c["participant"] == "JAMES")
    assert james["state"]["code"] == "MODEL_PASSED" and james["wagers"] == []


def test_a_forecast_alone_or_an_early_phase_is_not_a_wager(world):
    start(world)
    game, o = add_game(world)
    snap = quote(world, o["home_ml"], at=KICK - timedelta(hours=3))
    decision(world, game, o["home_ml"], snap, at=KICK - timedelta(hours=3), phase=ExperimentDecisionPhase.MORNING)
    assert hd.mirror_model_decisions(world["session"], now=KICK)["imported"] == 0


def test_decisions_before_the_start_are_never_backfilled(world):
    game, o = add_game(world, kick=T0 + timedelta(minutes=30))
    snap = quote(world, o["home_ml"], at=T0 - timedelta(minutes=30))
    decision(world, game, o["home_ml"], snap, at=T0 - timedelta(minutes=10))
    start(world)
    assert hd.mirror_model_decisions(world["session"], now=T0 + timedelta(hours=1))["imported"] == 0


def test_a_decision_james_cannot_fund_is_recorded_unfunded(world):
    start(world)
    james = account(world, "JAMES")
    game, o = add_game(world)
    snap = quote(world, o["home_ml"], at=KICK - timedelta(minutes=40))
    decision(world, game, o["home_ml"], snap, at=KICK - timedelta(minutes=20))
    # Every dollar is already committed to an open wager elsewhere.
    other, oo = add_game(world, kick=KICK + timedelta(hours=1), tag="Z")
    world["session"].add(CompetitionWager(
        account_id=james.id, participant_id=james.participant_id, sport="MLB", source="MODEL_MIRROR",
        status="OPEN", game_id=other.id, stake=Decimal("1000.00"), decimal_odds=2.0,
        submitted_at=KICK - timedelta(hours=2)))
    world["session"].flush()
    report = hd.mirror_model_decisions(world["session"], now=KICK)
    assert report["unfunded"] == 1
    row = world["session"].scalar(select(CompetitionWager).where(CompetitionWager.source_decision_id.is_not(None)))
    assert row.status == "UNFUNDED" and row.stake == 0 and "funds" in row.unfunded_reason.lower()
    assert hd.mirror_model_decisions(world["session"], now=KICK)["unfunded"] == 0, "recorded once"


# -- settlement ---------------------------------------------------------------
def test_win_loss_push_and_void_settle_once_with_correct_money(world):
    start(world)
    now = KICK - timedelta(minutes=45)
    game, o = add_game(world, tag="S")
    ml = quote(world, o["home_ml"], at=now, american=150)
    rl = quote(world, o["away_+1.5"], at=now, american=-110, point=1.5)
    tot = quote(world, o["over_9"], at=now, american=-110, point=9.0)
    win = place(world, "SCOTTY", ml, "100", now=now)
    lose = place(world, "RED_LOU", rl, "110", now=now)
    push = place(world, "SCOTTY", tot, "50", now=now)
    cancelled, co = add_game(world, kick=KICK + timedelta(hours=1), tag="C")
    void = place(world, "RED_LOU", quote(world, co["home_ml"], at=now), "40", now=now)

    final(game, 6, 3)                   # home by 3: ML wins, away +1.5 loses, total 9 pushes
    cancelled.status = GameStatus.CANCELED
    positions_before = world["session"].scalar(select(func.count()).select_from(OfficialAspPosition))

    report = hd.settle_open(world["session"], now=KICK + timedelta(hours=4))
    assert report == {"settled": 4, "win": 1, "loss": 1, "push": 1, "void": 1}
    assert (win.result, win.profit_loss, win.payout) == ("WIN", Decimal("150.00"), Decimal("250.00"))
    assert (lose.result, lose.profit_loss, lose.payout) == ("LOSS", Decimal("-110.00"), Decimal("0.00"))
    assert (push.result, push.profit_loss, push.payout) == ("PUSH", Decimal("0.00"), Decimal("50.00"))
    assert (void.result, void.profit_loss, void.payout) == ("VOID", Decimal("0.00"), Decimal("40.00"))
    assert hd.settle_open(world["session"], now=KICK + timedelta(hours=5))["settled"] == 0

    scotty = hd.balance(world["session"], account(world, "SCOTTY"))
    red = hd.balance(world["session"], account(world, "RED_LOU"))
    assert (scotty.bankroll, scotty.available) == (Decimal("1150.00"), Decimal("1150.00"))
    assert (red.bankroll, red.available) == (Decimal("890.00"), Decimal("890.00"))
    assert world["session"].scalar(select(func.count()).select_from(OfficialAspPosition)) == positions_before


def test_a_two_way_moneyline_tie_is_a_push(world):
    start(world, "MLB")
    now = KICK - timedelta(minutes=45)
    game, o = add_game(world, tag="T")
    wager = place(world, "SCOTTY", quote(world, o["away_ml"], at=now, american=120), "25", now=now)
    final(game, 20, 20)
    hd.settle_open(world["session"], now=KICK + timedelta(hours=4))
    assert (wager.result, wager.profit_loss, wager.payout) == ("PUSH", Decimal("0.00"), Decimal("25.00"))


def test_placing_a_wager_does_not_dent_the_bankroll_chart(world):
    start(world)
    now = KICK - timedelta(minutes=45)
    game, o = add_game(world)
    place(world, "SCOTTY", quote(world, o["home_ml"], at=now), "300", now=now)
    board = hd.summary(world["session"], "MLB", now=now)
    scotty = next(s for s in board["chart"]["series"] if s["participant"] == "SCOTTY")
    assert [p["bankroll"] for p in scotty["points"]] == [1000.0, 1000.0]
    card = next(c for c in board["standings"] if c["participant"] == "SCOTTY")
    assert (card["bankroll"], card["available"], card["open_exposure"]) == (1000.0, 700.0, 300.0)
    assert board["label"] == "Virtual competition"


# -- the board ----------------------------------------------------------------
@pytest.mark.parametrize("a,b,expected", [
    ({"market_type": "MONEYLINE", "selection": "X", "line": None},
     {"market_type": "MONEYLINE", "selection": "Y", "line": None}, "DIRECTLY_OPPOSED"),
    ({"market_type": "MONEYLINE", "selection": "X", "line": None},
     {"market_type": "SPREAD", "selection": "Y", "line": 1.5}, "DIFFERENT_BETS"),
    ({"market_type": "SPREAD", "selection": "X", "line": -1.5},
     {"market_type": "SPREAD", "selection": "Y", "line": 1.5}, "DIRECTLY_OPPOSED"),
    ({"market_type": "SPREAD", "selection": "X", "line": -1.5},
     {"market_type": "SPREAD", "selection": "Y", "line": 2.5}, "DIFFERENT_LINES"),
    ({"market_type": "TOTAL", "selection": "Over", "line": 8.5},
     {"market_type": "TOTAL", "selection": "Under", "line": 9.0}, "DIFFERENT_LINES"),
    ({"market_type": "TOTAL", "selection": "Over", "line": 8.5},
     {"market_type": "TOTAL", "selection": "Under", "line": 8.5}, "DIRECTLY_OPPOSED"),
])
def test_only_the_same_market_and_line_count_as_directly_opposed(a, b, expected):
    assert hd._opposition(a, b, {}) == expected


def test_all_sports_states_the_actual_combined_starting_capital(world):
    start(world, "MLB")
    board = hd.summary(world["session"], "ALL", now=T0)
    assert board["started_sports"] == ["MLB"]
    assert board["combined_starting_capital_each"] == 1000.0
    assert all(row["starting_balance"] == 1000.0 for row in board["standings"])


# -- API ------------------------------------------------------------------------
def test_api_reads_are_public_and_writes_need_the_right_login(world, client, session, settings, admin_headers):
    start(world)
    now = datetime.now(UTC)
    game, o = add_game(world, kick=now + timedelta(hours=3), tag="API")
    snap = quote(world, o["home_ml"], at=now - timedelta(minutes=5))
    session.commit()

    board = client.get("/api/v1/house-divided?sport=MLB")
    assert board.status_code == 200 and board.json()["virtual"] is True
    quotes = client.get(f"/api/v1/house-divided/games/{game.id}/quotes").json()["quotes"]
    assert any(q["quote_id"] == str(snap.id) for q in quotes)

    payload = {"participant": "SCOTTY", "quote_id": str(snap.id), "stake": "25"}
    assert client.post("/api/v1/house-divided/drafts", json=payload).status_code == 401
    member = _user(session, "member@example.test")
    session.commit()
    token = create_access_token(str(member.id), settings.secret_key, 5,
                                claims={"role": "member", "ver": member.token_version})
    denied = client.post("/api/v1/house-divided/drafts", json=payload,
                         headers={"Authorization": f"Bearer {token}"})
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "NOT_AUTHORIZED"

    draft = client.post("/api/v1/house-divided/drafts", json=payload, headers=admin_headers)
    assert draft.status_code == 200, draft.text
    wager_id = draft.json()["wager_id"]
    submitted = client.post(f"/api/v1/house-divided/drafts/{wager_id}/submit", headers=admin_headers)
    assert submitted.status_code == 200 and submitted.json()["status"] == "OPEN"
    locked = client.post(f"/api/v1/house-divided/drafts/{wager_id}", json=payload, headers=admin_headers)
    assert locked.status_code == 409 and locked.json()["detail"]["code"] == "LOCKED"
    assert client.post("/api/v1/house-divided/sports/NFL/start").status_code == 401


def test_nfl_decisions_use_the_nfl_policy_sizing(world):
    session = world["session"]
    nfl = ShadowExperiment(
        name="NFL paper", version=301, started_at=datetime(2026, 9, 1, tzinfo=UTC), start_date=date(2026, 9, 1),
        starting_bankroll=1000, sport="NFL", recommendation_policy_version=hd.NFL_POLICY,
        staking_policy_version="NFL_QK", decision_policy=hd.NFL_POLICY, controlling_engine="NFL",
        supported_markets=["MONEYLINE"], kelly_fraction=0.25, unit_percentage=0.01,
        max_position_percentage=0.005, max_same_game_percentage=0.01, max_daily_percentage=0.05,
        code_commit="t", code_identity="t", is_active=True)
    session.add(nfl)
    session.flush()
    hd.start_sport(session, "NFL", actor=world["admin"], now=T0)
    game, o = add_game(world, "NFL", tag="N")
    snap = quote(world, o["home_ml"], at=KICK - timedelta(minutes=40), american=120)
    decision(world, game, o["home_ml"], snap, at=KICK - timedelta(minutes=20), experiment=nfl, sport="NFL",
             evidence={"selection": {"quarter_kelly": 0.03}})
    report = hd.mirror_model_decisions(session, now=KICK)
    assert report["sports"]["NFL"]["imported"] == 1
    wager = session.scalar(select(CompetitionWager).where(CompetitionWager.sport == "NFL"))
    # 3% quarter-Kelly would be $30; the NFL policy's 0.5% position cap binds at $5.
    assert wager.stake == Decimal("5.00") and wager.staking_detail["bound_by"] == "position_cap"


def test_the_finalize_step_never_raises(world, monkeypatch, settings):
    from app.jobs.orchestrator import JobOrchestrator
    import asyncio

    def boom(*_a, **_k):
        raise RuntimeError("competition fault")

    monkeypatch.setattr(hd, "run", boom)
    orchestrator = JobOrchestrator(world["session"], settings)
    report = asyncio.run(orchestrator.run_house_divided(date(2026, 9, 18)))
    assert report["records"] == 0 and "competition fault" in report["error"]


def test_ranking_is_by_bankroll_and_ties_share_a_rank_in_roster_order(world):
    start(world)
    board = hd.summary(world["session"], "MLB", now=T0)
    assert [(c["participant"], c["rank"]) for c in board["standings"]] == [
        ("JAMES", 1), ("SCOTTY", 1), ("RED_LOU", 1)]
    now = KICK - timedelta(minutes=45)
    game, o = add_game(world, tag="R")
    place(world, "RED_LOU", quote(world, o["home_ml"], at=now, american=200), "10", now=now)
    final(game, 5, 1)
    hd.settle_open(world["session"], now=KICK + timedelta(hours=4))
    board = hd.summary(world["session"], "MLB", now=KICK + timedelta(hours=4))
    assert [(c["participant"], c["rank"]) for c in board["standings"]] == [
        ("RED_LOU", 1), ("JAMES", 2), ("SCOTTY", 2)]
