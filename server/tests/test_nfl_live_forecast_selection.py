"""The live NFL channel and the scorer agree about which forecast controls.

Publication time is the authority clock. Import time never is: a snapshot that
arrived later cannot displace what an observer would have been looking at at
kickoff, and a snapshot published at or after kickoff is never selected at all.
The rule has one implementation (``forecast_evaluation.controlling_forecast``,
the scorer's own freeze), which the probability adapter calls.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.probability.adapter import ADMITTED, ProbabilityUnavailable, bind, clear_cache
from app.services.forecast_evaluation import CONTROLLING_REASON, controlling_forecast
from tests.test_nfl_outcome_scoring_readiness import CELLS, admitted_forecast
from tests.test_nfl_prospective_scoring import KICKOFF, forecast, world  # noqa: F401

M1 = ADMITTED["NFL"]


@pytest.fixture(autouse=True)
def _clear():
    clear_cache()
    yield
    clear_cache()


def channel(world, settings):
    return bind(world["session"], settings, world["game"].id)


def test_the_later_publication_controls_even_when_it_was_imported_first(world, settings):
    early = admitted_forecast(world, published=KICKOFF - timedelta(minutes=45), version=1,
                              cells=CELLS, snapshot_type="FINAL_PREGAME")
    late_import = admitted_forecast(world, published=KICKOFF - timedelta(days=3), version=7, cells=CELLS)
    assert late_import.created_at >= early.created_at, "the earlier publication was imported last"

    selection = controlling_forecast(world["session"], canonical_game_id=world["game"].id,
                                     engine_id=M1["engine_id"], engine_version=M1["configuration_id"])
    assert selection.row.id == early.id
    assert selection.reason == CONTROLLING_REASON

    described = channel(world, settings).describe()
    revision = described["controlling_revision"]
    assert revision["forecast_id"] == early.forecast_id
    assert revision["snapshot_type"] == "FINAL_PREGAME"
    assert revision["reason"] == CONTROLLING_REASON
    assert revision["hours_before_kickoff"] == pytest.approx(0.75)
    assert described["binding"]["forecastId"] == early.forecast_id


def test_a_final_pregame_revision_controls_when_it_is_genuinely_later(world, settings):
    admitted_forecast(world, published=KICKOFF - timedelta(days=2), version=8, cells=CELLS)
    final = admitted_forecast(world, published=KICKOFF - timedelta(minutes=20), version=1,
                              cells=[(20, 27, 6000), (27, 20, 4000)], snapshot_type="FINAL_PREGAME")
    described = channel(world, settings).describe()
    assert described["controlling_revision"]["forecast_id"] == final.forecast_id
    assert described["winner"]["home"] == pytest.approx(0.4)


def test_an_earlier_final_pregame_never_displaces_a_later_current(world, settings):
    admitted_forecast(world, published=KICKOFF - timedelta(days=3), version=1,
                      cells=CELLS, snapshot_type="FINAL_PREGAME")
    current = admitted_forecast(world, published=KICKOFF - timedelta(hours=2), version=9, cells=CELLS)
    described = channel(world, settings).describe()
    assert described["controlling_revision"]["forecast_id"] == current.forecast_id
    assert described["controlling_revision"]["snapshot_type"] == "CURRENT"


def test_a_snapshot_published_at_or_after_kickoff_is_never_selected(world, settings):
    good = admitted_forecast(world, published=KICKOFF - timedelta(hours=6), version=5, cells=CELLS)
    admitted_forecast(world, published=KICKOFF, version=6, cells=CELLS)
    admitted_forecast(world, published=KICKOFF + timedelta(minutes=30), version=7, cells=CELLS)
    described = channel(world, settings).describe()
    assert described["controlling_revision"]["forecast_id"] == good.forecast_id
    reasons = {entry["reason"] for entry in described["controlling_revision"]["excluded"]}
    assert "PUBLISHED_AFTER_KICKOFF" in reasons
    assert described["controlling_revision"]["snapshots_considered"] == 3


def test_a_game_with_only_late_forecasts_is_refused_rather_than_shown(world, settings):
    admitted_forecast(world, published=KICKOFF + timedelta(minutes=1), version=1, cells=CELLS)
    with pytest.raises(ProbabilityUnavailable) as raised:
        channel(world, settings)
    assert raised.value.code == "FORECAST_UNAVAILABLE"
    assert "published before kickoff" in raised.value.message


def test_an_unverified_package_is_refused_not_substituted(world, settings):
    # The admitted artifact is required: a forecast that is not the admitted M1
    # configuration is refused, and no other revision stands in for it.
    forecast(world, published=KICKOFF - timedelta(hours=3), version=4)  # model_artifact_sha256 = "1" * 64
    with pytest.raises(ProbabilityUnavailable) as raised:
        channel(world, settings)
    assert raised.value.code == "IDENTITY_MISMATCH"


def test_selection_is_deterministic_when_publication_times_tie(world, settings):
    published = KICKOFF - timedelta(hours=4)
    first = admitted_forecast(world, published=published, version=3, cells=CELLS)
    second = admitted_forecast(world, published=published, version=3, cells=CELLS)
    expected = max((first, second), key=lambda row: str(row.id)).forecast_id
    for _ in range(3):
        selection = controlling_forecast(world["session"], canonical_game_id=world["game"].id,
                                         engine_id=M1["engine_id"], engine_version=M1["configuration_id"])
        assert selection.row.forecast_id == expected


def test_the_scorer_and_the_live_channel_choose_the_same_revision(world, settings):
    admitted_forecast(world, published=KICKOFF - timedelta(days=1), version=6, cells=CELLS)
    admitted_forecast(world, published=KICKOFF - timedelta(minutes=30), version=1,
                      cells=CELLS, snapshot_type="FINAL_PREGAME")
    from app.services.forecast_evaluation import ForecastEvaluationService

    frozen = ForecastEvaluationService(world["session"]).freeze_eligible(canonical_game_id=world["game"].id)
    scorer_choice = frozen[f"{M1['engine_id']}::{M1['configuration_id']}"].row.forecast_id
    assert channel(world, settings).describe()["binding"]["forecastId"] == scorer_choice
