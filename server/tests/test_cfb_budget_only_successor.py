"""CFB record(): budget-only decline successor (MAIN CFB_CAP_MISSED_ROWS_AND_STICKY_DECLINE_REVIEW 556b9a42).

A prior NO_QUALIFYING_EDGE row may be followed by a successor only when its retained
best terms show positive qualifying value with an exhausted daily cap as the sole
binding constraint, no position exists, and a NEW natural evaluation genuinely
qualifies under a different, currently effective shared allowance. Everything
else keeps the existing reuse. Offline, SQLite only.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.cfb import paper_decision
from app.cfb.paper_decision import budget_only_successor
from app.models import OfficialAspPosition, PositionStatus, ShadowExperimentDecision
from app.services import shared_daily_allowance as sda
from tests.test_cfb_consumer_activation import era, world  # noqa: F401
from tests.test_cfb_daily_limit_amendment import (  # noqa: F401
    _decision_401, _enter, _qualifying_price, checkpoint,
)

CLOCK = "2026-10-10T19:00:00+00:00"
#: The prior row's own recorded columns for a genuine value decline.
ROW = {"prior_recommendation": "DECLINED", "prior_reason": "NO_QUALIFYING_EDGE"}


def _prior(**over):
    terms = {"edge": 0.05, "expected_return_per_dollar": 0.08,
             "limits": {"quarter_kelly": 20.0, "position_cap": 5.0, "max_stake": 5.0, "same_game_cap": 10.0,
                        "daily_cap": 0.0}}
    terms.update(over.pop("terms", {}))
    ev = {"outcome": "DECLINED", "reason": "NO_QUALIFYING_EDGE", "best": {"terms": terms}}
    ev.update(over)
    return ev


def _current(**over):
    d = {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED",
         "shared_daily_allowance": {"effective_from": CLOCK, "room_usd": 50.0}}
    d.update(over)
    return d


# -- the predicate's exact boundary -----------------------------------------------------
def test_budget_only_decline_under_a_newly_effective_shared_policy_qualifies_for_a_successor():
    assert budget_only_successor(_prior(), _current(), **ROW) is True


@pytest.mark.parametrize("prior, why", [
    (_prior(terms={"edge": 0.01}), "edge below the policy minimum (true no-edge)"),
    (_prior(terms={"expected_return_per_dollar": 0.0}), "no positive expected return"),
    (_prior(terms={"limits": {"quarter_kelly": 20.0, "position_cap": 5.0, "max_stake": 5.0,
                              "same_game_cap": 0.0, "daily_cap": 0.0}}), "another limit also exhausted"),
    (_prior(terms={"limits": {"quarter_kelly": 20.0, "position_cap": 5.0, "max_stake": 5.0,
                              "same_game_cap": 10.0, "daily_cap": 2.0}}), "daily cap not exhausted"),
    (_prior(reason="NO_TWO_SIDED_QUOTE"), "missing pair, not a value decline"),
    (_prior(best=None), "no retained terms"),
    (_prior(shared_daily_allowance={"effective_from": CLOCK}), "same shared policy as before"),
    ("not evidence", "unreadable evidence"),
])
def test_everything_else_keeps_the_existing_reuse(prior, why):
    assert budget_only_successor(prior, _current(), **ROW) is False, why


@pytest.mark.parametrize("current, why", [
    (_current(outcome="DECLINED", reason="NO_QUALIFYING_EDGE"), "the new evaluation did not qualify"),
    (_current(shared_daily_allowance=None), "no shared policy currently effective"),
    ({"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED"}, "pre-clock / non-effective transition"),
])
def test_no_successor_without_a_genuinely_qualified_effective_shared_evaluation(current, why):
    assert budget_only_successor(_prior(), current, **ROW) is False, why


# -- end to end through the natural CFB checkpoint -----------------------------------------
def _setup_budget_decline(world, era, checkpoint):
    moment, game = checkpoint
    world.settings.cfb_daily_limit_amendment_effective_from = moment - timedelta(days=2)
    _enter(world, era, moment, [PositionStatus.OPEN] * 10)                    # $50 entered: the $50 limit is spent
    _qualifying_price(world, game, moment)
    first = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert (first["outcome"], first["reason"]) == ("DECLINED", "NO_QUALIFYING_EDGE")
    assert first["best"]["terms"]["limits"]["daily_cap"] == 0.0 and first["best"]["terms"]["edge"] >= 0.02
    return moment, game, first


def test_a_later_natural_evaluation_under_the_new_shared_policy_appends_a_successor(world, era, checkpoint):
    moment, game, first = _setup_budget_decline(world, era, checkpoint)
    later = moment + timedelta(minutes=2)                                     # still inside T-30..T-15
    setattr(world.settings, sda.SETTING, later - timedelta(minutes=1))       # shared $100 became effective
    second = _decision_401(paper_decision.evaluate(world.session, world.settings, now=later))
    assert second["persisted"] == "RECORDED" and second["outcome"] == "QUALIFIED" and second["position_id"]
    assert second["decision_id"] != first["decision_id"]
    assert second["supersedes_decision_id"] == str(first["decision_id"])
    assert second["shared_daily_allowance"]["room_usd"] == 50.0
    old = world.session.get(ShadowExperimentDecision, uuid.UUID(str(first["decision_id"])))
    assert old.recommendation == "DECLINED" and old.recommended_at.replace(tzinfo=None) == moment.replace(tzinfo=None)
    assert world.session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 11


def test_without_an_effective_shared_policy_the_decline_is_reused_as_before(world, era, checkpoint):
    moment, game, first = _setup_budget_decline(world, era, checkpoint)
    setattr(world.settings, sda.SETTING, moment + timedelta(hours=1))       # not effective yet
    again = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment + timedelta(minutes=2)))
    assert again["persisted"] == "REUSED_EXISTING_DECISION" and again["position_id"] is None
    assert world.session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 10


def test_after_the_successor_holds_a_position_later_ticks_reuse_it(world, era, checkpoint):
    moment, game, first = _setup_budget_decline(world, era, checkpoint)
    later = moment + timedelta(minutes=2)
    setattr(world.settings, sda.SETTING, later - timedelta(minutes=1))
    second = _decision_401(paper_decision.evaluate(world.session, world.settings, now=later))
    third = _decision_401(paper_decision.evaluate(world.session, world.settings, now=later + timedelta(minutes=2)))
    assert third["persisted"] == "REUSED_EXISTING_DECISION" and third["decision_id"] == second["decision_id"]
    assert world.session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 11


# -- MAIN S1 (ac546a1c): missing cause / incomplete or malformed evidence fails closed ------------
def test_s1_main_counterexample_partial_terms_without_recorded_cause_is_rejected():
    evidence = {"best": {"terms": {"edge": 0.05, "expected_return_per_dollar": 0.08,
                                   "limits": {"daily_cap": 0, "quarter_kelly": 20}}}}
    assert budget_only_successor(evidence, _current(), **ROW) is False
    assert budget_only_successor(evidence, _current()) is False


@pytest.mark.parametrize("row, why", [
    ({}, "row cause not recorded"),
    ({"prior_recommendation": "DECLINED", "prior_reason": None}, "row reason missing"),
    ({"prior_recommendation": None, "prior_reason": "NO_QUALIFYING_EDGE"}, "row recommendation missing"),
    ({"prior_recommendation": "DECLINED", "prior_reason": "NO_TWO_SIDED_QUOTE"}, "row reason is another cause"),
    ({"prior_recommendation": "QUALIFIED_BET", "prior_reason": "NO_QUALIFYING_EDGE"}, "row was not a decline"),
])
def test_s1_the_rows_own_cause_must_be_an_explicit_value_decline(row, why):
    assert budget_only_successor(_prior(), _current(), **row) is False, why


@pytest.mark.parametrize("evidence, why", [
    ({k: v for k, v in _prior().items() if k != "reason"}, "evidence reason missing"),
    ({k: v for k, v in _prior().items() if k != "outcome"}, "evidence outcome missing"),
    (_prior(reason="NO_TWO_SIDED_QUOTE"), "evidence reason inconsistent with the row"),
    (_prior(outcome="QUALIFIED"), "evidence outcome inconsistent with the row"),
    (_prior(terms={"limits": {"quarter_kelly": 20.0, "position_cap": 5.0, "max_stake": 5.0, "daily_cap": 0.0}}),
     "same_game_cap missing (incomplete canonical limits)"),
    (_prior(terms={"limits": {"daily_cap": 0.0, "quarter_kelly": 20.0}}), "partial limits"),
    (_prior(terms={"edge": "0.05"}), "edge not numeric"),
    (_prior(terms={"expected_return_per_dollar": True}), "boolean is not a number"),
    (_prior(terms={"limits": {"quarter_kelly": 20.0, "position_cap": None, "max_stake": 5.0,
                              "same_game_cap": 10.0, "daily_cap": 0.0}}), "null limit"),
    (_prior(best=["terms"]), "best is a list"),
    (_prior(best={"terms": "edge=0.05"}), "terms is a string"),
    (_prior(best={"terms": {"edge": 0.05, "expected_return_per_dollar": 0.08, "limits": [0, 20]}}),
     "limits is a list"),
    (_prior(shared_daily_allowance="2026-10-10"), "prior shared policy malformed"),
])
def test_s1_incomplete_or_malformed_evidence_returns_false_without_raising(evidence, why):
    assert budget_only_successor(evidence, _current(), **ROW) is False, why


@pytest.mark.parametrize("current", [
    _current(shared_daily_allowance={"effective_from": None}),
    _current(shared_daily_allowance={"effective_from": 123}),
    _current(shared_daily_allowance="CLOCK"),
    "not a decision",
])
def test_s1_malformed_current_shared_policy_is_rejected(current):
    assert budget_only_successor(_prior(), current, **ROW) is False
