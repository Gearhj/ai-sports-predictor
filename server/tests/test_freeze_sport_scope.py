"""The MLB freeze owner never evaluates another sport's experiment.

By 2026-09-15 the NFL paper experiment had 76 decision rows on MLB games,
because freeze() read the MLB slate for every active experiment regardless of
its sport. No cross-sport position resulted, but nothing prevented one.
"""
from datetime import date
from types import SimpleNamespace

import pytest

from app.models import ExperimentDecisionPhase
from app.services.shadow_experiment_service import ShadowExperimentService


def service():
    svc = ShadowExperimentService.__new__(ShadowExperimentService)
    svc.session = SimpleNamespace()
    svc.settings = SimpleNamespace()
    return svc


@pytest.mark.parametrize("sport", ["NFL", "NBA", "CFB"])
def test_a_non_mlb_experiment_is_declined_before_any_read(sport):
    experiment = SimpleNamespace(sport=sport, decision_policy=f"AISP_{sport}_PAPER_POLICY_V1")
    result = service().freeze(date(2026, 9, 15), phase=ExperimentDecisionPhase.FINAL_PREGAME,
                              experiment=experiment)
    assert result["frozen"] == 0 and result["primary"] == 0
    assert result["sport"] == sport
