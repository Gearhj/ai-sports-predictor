"""The prospective validation plan is a predeclaration, so it is pinned.

A registered plan is only worth something if it cannot be quietly edited once
results exist. These tests read `docs/RFF_CORE_VALIDATION_PLAN.md` and assert
the choices that must be fixed *before* data arrives: the single primary
reading point, the unit of analysis, the first-look threshold, the incremental
framing of every hypothesis, the named estimator, and the standing
prohibitions.

They deliberately test the document rather than any runtime behaviour: the plan
governs a future analysis and touches no production code. Changing a pinned
value means changing this test in the same commit, which is exactly the visible
act a predeclaration is supposed to require.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PLAN = Path(__file__).resolve().parents[2] / "docs" / "RFF_CORE_VALIDATION_PLAN.md"


@pytest.fixture(scope="module")
def plan() -> str:
    """The plan with whitespace collapsed, so a phrase is pinned by its words
    rather than by where the paragraph happened to wrap."""

    assert PLAN.is_file(), f"the registered validation plan is missing: {PLAN}"
    return " ".join(PLAN.read_text(encoding="utf-8").split())


def test_there_is_exactly_one_predeclared_primary_reading(plan: str):
    assert "**The primary reading for a game is T-90m**" in plan
    assert "one game" in plan, "the unit of analysis is the game, not the reading"


def test_the_other_offsets_are_secondary_repeated_measures(plan: str):
    assert "**T-6h, T-3h and T-30m are secondary repeated measurements**" in plan
    assert "never pooled with the primary as" in plan


def test_the_first_look_is_counted_in_games_not_readings(plan: str):
    assert "**First formal look: 300 games with a publishable T-90m reading**" in plan
    assert "not 300 readings" in plan


def test_power_uses_games_as_the_unit_and_declares_a_design_effect(plan: str):
    assert "~206 games" in plan, "the n required for rho = 0.20 at 80% power"
    assert "DEFF = 1 + (m - 1)" in plan, "within-game correlation is accounted for"
    assert "effective sample size is reported alongside the nominal one" in plan


def test_every_hypothesis_is_incremental_against_a_predeclared_baseline(plan: str):
    assert "**there is no marginal test at all**" in plan
    for marker in ("**H1**", "**H2**", "**H3**", "**H4**"):
        assert marker in plan
    # The four incremental questions, in the words that make them incremental.
    assert "beyond movement already seen" in plan
    assert "beyond dispersion already present" in plan
    assert "beyond prior instability" in plan
    assert "beyond unresolved participants and our own evidence health" in plan
    assert "strictly at or before the reading instant" in plan


def test_the_incremental_estimator_is_named_before_any_result_exists(plan: str):
    assert "partial Spearman rank correlation, residual-on-residual" in plan
    assert "2,000 resamples" in plan and "resampling whole games" in plan
    assert "Specification lock" in plan
    assert "before the 300th qualifying game" in plan


def test_a_null_at_the_first_look_is_not_declared_a_null(plan: str):
    """300 games gives ~72% power at rho = 0.15, so absence is not evidence."""

    assert "**not yet informative**" in plan
    assert "do not declare a null here" in plan


def test_the_outcome_and_decision_prohibitions_survive(plan: str):
    assert "No outcome optimisation." in plan
    assert "No decision coupling." in plan
    for banned in ("profit", "closing-line value", "settled position"):
        assert banned in plan
    assert "produces no threshold that a decision could consume" in plan


def test_the_correction_is_recorded_rather_than_silently_applied(plan: str):
    assert "## 8. Change log" in plan
    assert "| 1 |" in plan and "| 2 |" in plan
    assert "No change to the index, weights, gates, interface, authority or " \
           "production behaviour." in plan
