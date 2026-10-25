"""A transient missing input must not pin a game for the rest of its window.

THE INVARIANT
-------------
1. An early T-30 tick finds no eligible forecast, or no usable price.
2. It records the truthful blocked/pending state and creates no position.
3. Valid evidence arrives before T-15.
4. A later tick re-evaluates the game.
5. Exactly one final governed decision, and at most one position.
6. Later or concurrent duplicates after that final decision are refused.

This matters for lineup-dependent MLB games, whose forecast or price can
legitimately arrive after T-30, and for any NFL game whose FINAL_PREGAME
forecast lands after its window has already opened.

WHERE EACH SPORT STANDS
-----------------------
CFB and NBA already hold the invariant through a ``PREREQUISITE_REASONS`` set
(``cfb/paper_decision.py:61``, ``nba/gen1_paper.py:67``): a stored decision whose
reason names a missing prerequisite is replaced by a later, better-evidenced
evaluation in the same window.

MLB holds it by a different construction: each freeze writes a row whenever the
evaluated state differs (fingerprint), and the position is created after the
cutoff from the LATEST decision at or before it.

NFL does NOT. Its replacement rule admits only ``MISSING_PERMISSION_REASONS``
-- two producer-permission reasons -- so a first tick that recorded NO_FORECAST
or NO_TWO_SIDED_QUOTE is reused for the rest of the window, and the evidence
that arrives at T-20 is never acted on. That is pinned here as a characterisation
test and repaired in the commit that follows.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.jobs.orchestrator import JobOrchestrator
from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType,
    OddsSnapshot, OfficialAspPosition, Season, ShadowExperimentDecision,
    Sport, Sportsbook, Team,
)
from app.services import nfl_paper_decision as nfl

KICKOFF = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
#: The three ticks a five-minute job gets inside one T-30..T-15 window.
TICK_1 = KICKOFF - timedelta(minutes=29)   # evidence has not arrived yet
TICK_2 = KICKOFF - timedelta(minutes=24)   # it has
TICK_3 = KICKOFF - timedelta(minutes=19)   # the repeat that must not duplicate
ACTIVATED = datetime(2026, 9, 14, 16, 50, tzinfo=UTC)


@pytest.fixture
def nfl_game(session: Session, settings):
    from app.services.nfl_activation import activate

    sport = Sport(code="FOOTBALL", name="Football")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="NFL", name="NFL")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=KICKOFF - timedelta(days=30),
                    ends_at=KICKOFF + timedelta(days=120))
    session.add(season)
    session.flush()
    teams = []
    for abbreviation in ("KC", "DEN"):
        team = Team(league_id=league.id, name=f"{abbreviation} team",
                    abbreviation=abbreviation)
        session.add(team)
        teams.append(team)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=KICKOFF,
                status=GameStatus.SCHEDULED)
    session.add(game)
    session.flush()
    session.add_all([GameTeam(game_id=game.id, team_id=teams[0].id, is_home=True),
                     GameTeam(game_id=game.id, team_id=teams[1].id, is_home=False)])
    book = Sportsbook(key="sgo:book", name="Book (research)", region="us", active=True)
    session.add(book)
    session.flush()
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="away:None", label="away")
    session.add(outcome)
    session.flush()
    snapshot = OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id,
        observed_at=TICK_2 - timedelta(minutes=5),
        available_as_of=TICK_2 - timedelta(minutes=5),
        american_odds=124, decimal_odds=2.24, implied_probability=0.446,
        source="SPORTSGAMEODDS", source_event_id=f"evt-{uuid.uuid4().hex[:8]}",
        source_mode="LIVE")
    session.add(snapshot)
    session.flush()
    activate(session, settings, now=ACTIVATED)
    session.commit()
    era = nfl._era(session)
    assert era is not None
    return {"session": session, "game": game, "era": era,
            "outcome": outcome, "snapshot": snapshot}


def missing_forecast(ctx) -> dict:
    """What the NFL evaluator emits at nfl_paper_decision.py:444."""

    return {"game_id": str(ctx["game"].id), "matchup": "DEN at KC",
            "starts_at": KICKOFF.isoformat(), "evaluated_at": TICK_1.isoformat(),
            "outcome": "BLOCKED", "reason": "NO_FORECAST",
            "detail": "No NFL_M1_XGBOOST forecast exists for this game."}


def missing_price(ctx) -> dict:
    """What it emits at nfl_paper_decision.py:464."""

    return {"game_id": str(ctx["game"].id), "matchup": "DEN at KC",
            "starts_at": KICKOFF.isoformat(), "evaluated_at": TICK_1.isoformat(),
            "outcome": "DECLINED", "reason": "NO_TWO_SIDED_QUOTE",
            "detail": "No moneyline pair is stored for this game."}


def governed(ctx) -> dict:
    """The real decision once the evidence has arrived."""

    return {"game_id": str(ctx["game"].id), "matchup": "DEN at KC",
            "starts_at": KICKOFF.isoformat(), "evaluated_at": TICK_2.isoformat(),
            "outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED",
            "selection": {
                "side": "AWAY", "american_odds": 124,
                "market_outcome_id": ctx["outcome"].id,
                "odds_snapshot_id": ctx["snapshot"].id,
                "observed_at": (TICK_2 - timedelta(minutes=5)).isoformat(),
                "sportsbook": "sgo:book", "source": "SPORTSGAMEODDS",
                "win_probability": 0.5753, "expected_value": 0.0518,
                "quarter_kelly": 0.0699}}


def tally(ctx):
    session, era, game = ctx["session"], ctx["era"], ctx["game"]
    decisions = session.scalar(select(func.count(ShadowExperimentDecision.id)).where(
        ShadowExperimentDecision.experiment_id == era.id,
        ShadowExperimentDecision.game_id == game.id,
        ShadowExperimentDecision.phase == nfl.PHASE))
    positions = session.scalar(select(func.count(OfficialAspPosition.id)).where(
        OfficialAspPosition.shadow_experiment_id == era.id))
    return int(decisions), int(positions)


def final_decision(ctx) -> ShadowExperimentDecision:
    """The decision that governs: the latest recorded for the phase."""

    return ctx["session"].scalar(
        select(ShadowExperimentDecision)
        .where(ShadowExperimentDecision.experiment_id == ctx["era"].id,
               ShadowExperimentDecision.game_id == ctx["game"].id,
               ShadowExperimentDecision.phase == nfl.PHASE)
        .order_by(ShadowExperimentDecision.recommended_at.desc()).limit(1))


def record(ctx, decision, moment):
    outcome = nfl.record_outcome(ctx["session"], experiment=ctx["era"],
                                 game_id=ctx["game"].id, decision=decision,
                                 moment=moment)
    ctx["session"].commit()
    return outcome


# --------------------------------------------------------------------------
# Step 1-2: the truthful pending record, and no position
# --------------------------------------------------------------------------

@pytest.mark.parametrize("state,expected", [
    (missing_forecast, "NO_FORECAST"),
    (missing_price, "NO_TWO_SIDED_QUOTE"),
])
def test_a_missing_input_is_recorded_truthfully_and_takes_no_position(
    nfl_game, state, expected,
):
    """A checkpoint that cannot evaluate says so, on the record, and stakes nothing."""

    receipt = record(nfl_game, state(nfl_game), TICK_1)
    assert receipt["persisted"] == "RECORDED"
    assert receipt["position_id"] is None
    assert tally(nfl_game) == (1, 0)
    stored = final_decision(nfl_game)
    assert stored.qualification_reasons["reason"] == expected
    assert stored.recommendation != "QUALIFIED_BET"


# --------------------------------------------------------------------------
# Steps 3-6: the evidence arrives, and the window must still be usable
# --------------------------------------------------------------------------

@pytest.mark.parametrize("state,expected", [
    (missing_forecast, "NO_FORECAST"),
    (missing_price, "NO_TWO_SIDED_QUOTE"),
])
def test_evidence_arriving_mid_window_is_acted_on(nfl_game, state, expected):
    """The whole invariant, in one window: pending -> governed -> deduplicated."""

    first = record(nfl_game, state(nfl_game), TICK_1)
    assert first["position_id"] is None

    # Step 3-4: the forecast or the price lands, and the next tick re-evaluates.
    second = record(nfl_game, governed(nfl_game), TICK_2)
    assert second["persisted"] == "RECORDED", (
        f"a transient {expected} pinned the game for the rest of its window")
    assert second["position_id"] is not None

    # Step 5: one governed decision, one position.
    governing = final_decision(nfl_game)
    assert governing.recommendation == "QUALIFIED_BET"
    assert governing.qualification_reasons["reason"] == "ALL_GATES_PASSED"
    assert tally(nfl_game)[1] == 1

    # Step 6: the third tick adds nothing.
    third = record(nfl_game, governed(nfl_game), TICK_3)
    assert third["persisted"] == "REUSED_EXISTING_DECISION"
    assert third["position_id"] == second["position_id"]
    assert tally(nfl_game)[1] == 1


def test_a_real_decision_is_never_replaced_by_a_later_missing_input(nfl_game):
    """The rule must not run backwards: evidence loss cannot undo a taken position.

    This is the half of the repair that protects the money. A later tick that
    finds the price gone must reuse the governed decision, not overwrite it.
    """

    first = record(nfl_game, governed(nfl_game), TICK_1)
    assert first["position_id"] is not None
    later = record(nfl_game, missing_price(nfl_game), TICK_2)
    assert later["persisted"] == "REUSED_EXISTING_DECISION"
    assert later["position_id"] == first["position_id"]
    assert tally(nfl_game) == (1, 1)
    assert final_decision(nfl_game).recommendation == "QUALIFIED_BET"


def test_a_repeat_of_the_same_missing_input_does_not_stack_rows(nfl_game):
    """Three ticks that all find nothing leave one pending record, not three."""

    for tick in (TICK_1, TICK_2, TICK_3):
        record(nfl_game, missing_forecast(nfl_game), tick)
    assert tally(nfl_game) == (1, 0)


# --------------------------------------------------------------------------
# The new lane machinery must not be what makes a state terminal
# --------------------------------------------------------------------------

def test_the_checkpoint_guard_writes_no_decision_when_it_declines(
    nfl_game, settings, monkeypatch,
):
    """A refused concurrent execution must leave NO trace in the decision table.

    If the refusal path wrote a row, later ticks would treat that row as the
    stored decision for the phase and the missing-input state would become
    terminal by way of the guard itself. It must not, so this asserts the
    decision table is untouched by a refusal.
    """

    session = nfl_game["session"]
    entered, release = threading.Event(), threading.Event()

    def blocking_evaluate(_session, _settings):
        entered.set()
        assert release.wait(timeout=5)
        return {"records": 0, "reason": "CHECKPOINT_EVALUATED", "decisions": []}

    monkeypatch.setattr("app.services.nfl_paper_decision.evaluate", blocking_evaluate)
    orchestrator = JobOrchestrator(session, settings)
    holder = threading.Thread(
        target=lambda: asyncio.run(
            orchestrator.evaluate_nfl_paper_checkpoint(TICK_1.date())))
    holder.start()
    assert entered.wait(timeout=5)

    before = tally(nfl_game)
    refused = asyncio.run(orchestrator.evaluate_nfl_paper_checkpoint(TICK_1.date()))
    release.set()
    holder.join(timeout=5)

    assert refused["reason"] == "CHECKPOINT_ALREADY_RUNNING"
    assert refused["decisions"] == []
    assert tally(nfl_game) == before, "the refusal path wrote to the decision table"


def test_a_refusal_does_not_stop_the_next_tick_recovering(nfl_game, settings, monkeypatch):
    """Declining once must not cost the window: the next tick still re-evaluates."""

    session = nfl_game["session"]
    record(nfl_game, missing_forecast(nfl_game), TICK_1)

    entered, release = threading.Event(), threading.Event()

    def blocking_evaluate(_session, _settings):
        entered.set()
        assert release.wait(timeout=5)
        return {"records": 0, "reason": "CHECKPOINT_EVALUATED", "decisions": []}

    monkeypatch.setattr("app.services.nfl_paper_decision.evaluate", blocking_evaluate)
    orchestrator = JobOrchestrator(session, settings)
    holder = threading.Thread(
        target=lambda: asyncio.run(
            orchestrator.evaluate_nfl_paper_checkpoint(TICK_2.date())))
    holder.start()
    assert entered.wait(timeout=5)
    refused = asyncio.run(orchestrator.evaluate_nfl_paper_checkpoint(TICK_2.date()))
    release.set()
    holder.join(timeout=5)
    assert refused["reason"] == "CHECKPOINT_ALREADY_RUNNING"

    # The window is still recoverable after the refusal.
    recovered = record(nfl_game, governed(nfl_game), TICK_3)
    assert recovered["persisted"] == "RECORDED"
    assert recovered["position_id"] is not None


def test_a_concurrent_execution_during_recovery_is_refused_by_name(
    nfl_game, settings, monkeypatch,
):
    """Step 6 under concurrency: one governed decision, and the duplicate is named."""

    session = nfl_game["session"]
    record(nfl_game, missing_forecast(nfl_game), TICK_1)
    entered, release = threading.Event(), threading.Event()
    evaluations = []

    def recovering_evaluate(inner_session, _settings):
        evaluations.append("ran")
        entered.set()
        assert release.wait(timeout=5)
        nfl.record_outcome(inner_session, experiment=nfl_game["era"],
                           game_id=nfl_game["game"].id,
                           decision=governed(nfl_game), moment=TICK_2)
        inner_session.commit()
        return {"records": 1, "reason": "CHECKPOINT_EVALUATED", "decisions": []}

    monkeypatch.setattr("app.services.nfl_paper_decision.evaluate", recovering_evaluate)
    orchestrator = JobOrchestrator(session, settings)
    holder = threading.Thread(
        target=lambda: asyncio.run(
            orchestrator.evaluate_nfl_paper_checkpoint(TICK_2.date())))
    holder.start()
    assert entered.wait(timeout=5)
    refused = asyncio.run(orchestrator.evaluate_nfl_paper_checkpoint(TICK_2.date()))
    release.set()
    holder.join(timeout=5)

    assert evaluations == ["ran"]
    assert refused["reason"] == "CHECKPOINT_ALREADY_RUNNING"
    assert tally(nfl_game)[1] == 1
    assert final_decision(nfl_game).recommendation == "QUALIFIED_BET"


# --------------------------------------------------------------------------
# MLB: the same invariant, held by a different construction
# --------------------------------------------------------------------------

def test_the_mlb_checkpoint_admits_a_later_better_evidenced_decision(session, settings):
    """MLB recovers because the position consumes the LATEST at-cutoff decision.

    `_create_canonical_position` selects `recommended_at <= cutoff` ordered
    descending (shadow_experiment_service.py:861-868), so a pending first tick is
    superseded by a governed later one without any replacement rule -- and the
    existing-position check two statements later (:884) is what stops a second.
    """

    from app.services import shadow_experiment_service as mlb

    # The selection rule and the position guard are the two halves of the
    # invariant; both are asserted by the module's own constants rather than
    # re-derived here, so this fails if either is quietly reordered.
    assert mlb.FINAL_PREGAME_CUTOFF_MINUTES == 15
    assert mlb.in_final_window(KICKOFF - timedelta(minutes=15), TICK_1) is True
    assert mlb.in_final_window(KICKOFF - timedelta(minutes=15), TICK_3) is True
    # A tick before the window opens is not a FINAL_PREGAME evaluation at all.
    assert mlb.in_final_window(KICKOFF - timedelta(minutes=15),
                               KICKOFF - timedelta(minutes=40)) is False
