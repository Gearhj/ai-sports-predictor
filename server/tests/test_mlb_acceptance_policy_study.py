"""Focused checks for the defects this acceptance study actually repaired.

Only three things are tested, each for a concrete defect:

1. The selected-team-to-final-score join. The previous study reported no
   outcomes because it read the sealed ``game_final_outcomes`` table, which is
   empty for the live window. The repaired join resolves the selected team
   through the market outcome and must get the winner right from either side.
2. The decision of record. Position creation consumes the LAST final-pregame
   row at or before the cutoff; reading any other row would let a candidate
   pick a better moment in hindsight.
3. Overlapping rejection causes. The evaluator returns at its first failed
   gate, so a later gate missing from ``gates_passed`` is unobserved, not
   failed -- the waterfall must not count it as a cause.

Nothing here touches the database or the live policy.
"""

from __future__ import annotations

from typing import Any

from scripts.mlb_acceptance_policy_analysis import Cohort, _selection_won, waterfall

HOME_TEAM = "11111111-1111-1111-1111-111111111111"
AWAY_TEAM = "22222222-2222-2222-2222-222222222222"
GAME = "33333333-3333-3333-3333-333333333333"
HOME_OUTCOME = "44444444-4444-4444-4444-444444444444"
AWAY_OUTCOME = "55555555-5555-5555-5555-555555555555"


def _payload(*, status: str = "FINAL", home_score: int = 6, away_score: int = 1,
             decisions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "experiments": [{
            "version": 3, "starting_bankroll": 1000.0, "kelly_fraction": 0.25,
            "max_position_percentage": 0.01, "max_same_game_percentage": 0.02,
            "max_daily_percentage": 0.05, "policy_config": {},
        }],
        "games": [{
            "id": GAME, "starts_at": "2026-09-01T18:45:00-04:00", "status": status,
            "home_score": home_score, "away_score": away_score,
            "home_team_id": HOME_TEAM, "away_team_id": AWAY_TEAM,
            "home_abbr": "BOS", "away_abbr": "SEA",
        }],
        "market_outcomes": [
            {"id": HOME_OUTCOME, "participant_team_id": HOME_TEAM, "team_abbr": "BOS"},
            {"id": AWAY_OUTCOME, "participant_team_id": AWAY_TEAM, "team_abbr": "SEA"},
        ],
        "positions": [],
        "moneyline_quote_coverage": [
            {"game_id": GAME, "market_outcome_id": HOME_OUTCOME,
             "sportsbook_id": "book", "has_closing": False},
        ],
        "moneyline_snapshots": [],
        "decisions": decisions or [],
    }


def _decision(recommended_at: str, *, recommendation: str = "QUALIFIED_BET",
              gates: list[str] | None = None, edge: float | None = 0.03) -> dict[str, Any]:
    return {
        "experiment_version": 3, "game_id": GAME, "phase": "FINAL_PREGAME",
        "recommended_at": recommended_at, "slate_date": "2026-09-01",
        "market_outcome_id": HOME_OUTCOME, "recommendation": recommendation,
        "recommendation_reason": "", "readiness": 67.5,
        "evidence": {
            "gates_passed": gates if gates is not None else [
                "valid_current_forecast",
                "model_required_inputs_valid_or_governed_fallback",
                "complete_two_sided_moneyline_quote", "fresh_quote",
                "market_probabilities_available", "not_extreme_disagreement",
                "no_unresolved_scratch", "starters_confirmed_or_probable_stable",
                "lineup_confirmed_adequate_or_pending_allowed",
            ],
            "critical_evidence_complete": True, "adjusted_edge": edge,
            "starter_home": {"state": "PROBABLE_STABLE"},
            "starter_away": {"state": "PROBABLE_STABLE"},
            "lineup": {"state": "CONFIRMED_COMPLETE"},
        },
    }


def test_the_selected_team_join_resolves_the_winner_from_either_side() -> None:
    """The repair: the winner comes from the selected team, not from 'home'."""

    cohort = Cohort(_payload(home_score=6, away_score=1))
    assert _selection_won(cohort, {"game_id": GAME, "market_outcome_id": HOME_OUTCOME}) is True
    assert _selection_won(cohort, {"game_id": GAME, "market_outcome_id": AWAY_OUTCOME}) is False

    # An unfinished game has no outcome at all -- never a default of "lost".
    unsettled = Cohort(_payload(status="LIVE"))
    assert _selection_won(unsettled, {"game_id": GAME, "market_outcome_id": HOME_OUTCOME}) is None


def test_the_decision_of_record_is_the_last_row_at_or_before_the_cutoff() -> None:
    """Cutoff is first pitch minus 15 minutes: 18:30. 18:31 must not be read."""

    cohort = Cohort(_payload(decisions=[
        _decision("2026-09-01T17:58:16-04:00"),
        _decision("2026-09-01T18:28:22-04:00", recommendation="NO_BET"),
        _decision("2026-09-01T18:31:00-04:00"),  # after the cutoff: hindsight
    ]))
    record = cohort.decision_of_record(GAME)
    assert record is not None
    assert record["recommended_at"] == "2026-09-01T18:28:22-04:00"
    assert record["recommendation"] == "NO_BET"


def test_a_gate_after_the_first_failure_is_not_counted_as_a_cause() -> None:
    """The evaluator short-circuits, so later gates are unobserved, not failed."""

    cohort = Cohort(_payload(decisions=[
        _decision("2026-09-01T18:28:22-04:00", recommendation="NO_BET", gates=[
            "valid_current_forecast",
            "model_required_inputs_valid_or_governed_fallback",
        ], edge=None),
    ]))
    flow = waterfall(cohort)
    causes = flow["overlapping_causes"]
    # The quote is the real cause; starter and lineup evidence were reduced and
    # are eligible, so neither may appear.
    assert causes.get("Priced, but no fresh complete two-sided quote at the cutoff") == 1
    assert "Starter evidence not CONFIRMED/PROBABLE_STABLE" not in causes
    assert "Lineup evidence ineligible" not in causes
    assert flow["first_blocking_cause"] == {
        "Complete same-book two-sided MONEYLINE quote": 1,
    }
