"""The NFL checkpoint always produces an outcome, and in the right order.

TWO THINGS ARE BEING PINNED
---------------------------
First, that a checkpoint never passes silently. "Qualified", "declined" and
"blocked" are three different answers and each carries its reason; a checkpoint
that came and went with no record is indistinguishable from a job that never
ran, which is the one state a reader cannot act on.

Second, that eligibility is evaluated BEFORE any price arithmetic. If the order
were reversed, a missing producer binding would surface only after an edge had
been computed and displayed -- and an edge that is shown and then refused
invites exactly the wrong question.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services import nfl_paper_decision as decision
from app.services import nfl_paper_policy as policy


def test_the_checkpoint_window_is_the_producers_own(session):
    # 30 to 15 minutes before kickoff, unchanged. NFL adds no exception to the
    # existing pregame timing.
    assert decision.WINDOW_OPENS == timedelta(minutes=30)
    assert decision.WINDOW_CLOSES == timedelta(minutes=15)


def test_the_freshness_limit_is_the_existing_one(session):
    # Not a new rule invented for NFL: the same 240 minutes the rest of the
    # platform enforces.
    assert decision.MAX_QUOTE_AGE_MINUTES == 240


def test_no_game_at_its_checkpoint_is_reported_rather_than_silent(session, settings):
    from app.services.nfl_activation import activate

    activate(session, settings, now=datetime(2026, 9, 14, 16, 50, tzinfo=UTC))
    result = decision.evaluate(session, settings,
                               now=datetime(2026, 9, 14, 12, 0, tzinfo=UTC))
    assert result["records"] == 0
    assert result["reason"] == "NO_NFL_GAME_AT_ITS_CHECKPOINT"
    assert result["decisions"] == []


def test_an_inactive_policy_is_named_rather_than_treated_as_no_games(session, settings):
    # Without activation the answer must be "the policy is not running", not
    # "there were no games" -- those are different facts.
    result = decision.evaluate(session, settings,
                               now=datetime(2026, 9, 14, 20, 0, tzinfo=UTC))
    assert result["reason"] == "NFL_PAPER_POLICY_IS_NOT_ACTIVE"
    assert result["records"] == 0


class _Session:
    """Answers the one lookup the replacement rule makes: an existing position."""

    def __init__(self, position_id=None):
        self.position_id = position_id

    def scalar(self, _statement):
        return self.position_id


def _prior(recommendation, reason):
    from types import SimpleNamespace

    return SimpleNamespace(id="decision-1", recommendation=recommendation,
                           qualification_reasons={"reason": reason, "outcome": recommendation})


def test_a_missing_permission_block_yields_to_a_now_eligible_evaluation():
    # A tick that found no exact supplement read no price and recorded no
    # position; once the supplement is imported inside the same window, the
    # eligible evaluation is recorded instead of being discarded.
    for reason in ("NO_PAPER_RESEARCH_ELIGIBILITY_RECORD", "AWAITING_SUCCESSOR_ELIGIBILITY"):
        assert decision._supersedes_transient_state(
            _Session(), _prior("BLOCKED", reason),
            {"outcome": "DECLINED", "reason": "QUOTE_TOO_STALE"}) is True


def test_a_repeated_missing_permission_reuses_the_stored_block():
    assert decision._supersedes_transient_state(
        _Session(), _prior("BLOCKED", "AWAITING_SUCCESSOR_ELIGIBILITY"),
        {"outcome": "BLOCKED", "reason": "AWAITING_SUCCESSOR_ELIGIBILITY"}) is False


def test_a_priced_or_positioned_decision_is_never_re_evaluated():
    eligible = {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED"}
    # Any other stored outcome stays the one decision for the phase.
    assert decision._supersedes_transient_state(
        _Session(), _prior("DECLINED", "NO_QUALIFYING_VALUE"), eligible) is False
    assert decision._supersedes_transient_state(
        _Session(), _prior("QUALIFIED_BET", "ALL_GATES_PASSED"), eligible) is False
    # A defective permission is not a missing one.
    assert decision._supersedes_transient_state(
        _Session(), _prior("BLOCKED", "ELIGIBILITY_RECORD_GRANTS_EXECUTION"), eligible) is False
    # And a block that somehow carries a position is never replaced.
    assert decision._supersedes_transient_state(
        _Session(position_id="p-1"), _prior("BLOCKED", "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD"),
        eligible) is False


def test_expected_value_uses_the_push_and_the_conditioned_kelly(session):
    """The two NFL-specific pieces of arithmetic, on the real DEN at KC numbers."""

    market = policy.TiedMarket(win_probability=0.5753, tie_probability=0.0430,
                               decimal_odds=policy.american_to_decimal(124))
    # A tie is a push: it contributes neither profit nor loss.
    assert round(policy.moneyline_ev(market), 6) == round(
        0.5753 * 1.24 - 0.3817, 6)
    # Quarter Kelly conditions on the game being decided.
    assert round(policy.kelly_fraction(market) * policy.KELLY_FRACTION, 4) == 0.0699
    # And the 0.5% cap is what actually binds on a $1,000 bankroll.
    assert policy.proposed_stake(market) == 5.00
