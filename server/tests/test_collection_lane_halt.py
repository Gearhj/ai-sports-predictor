"""The first mismatch stops the lane, and nothing retries.

WHAT THIS GUARDS
----------------
A request that names its games can still be answered with a different one. That
fault leaves a receipt reading ``FALLBACK_COLLECTED`` -- objects charged, rows
written, no error anywhere -- so the only thing that distinguishes it is the
mismatch itself. Retrying would spend a second object re-asking a question the
provider has just declined to answer.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import CollectionLaneHalt
from app.services.collection_halt import (
    SELECTION_MISMATCH, active_halt, halt_lane, mismatch,
)

NOW = datetime(2026, 9, 14, 20, 26, 50, tzinfo=UTC)
SD_COL = "119b0fa1-f363-45b9-ac1f-b0a4ede29c0e"
CWS_CLE = "94e208da-7bf1-406a-9e2c-cd882aeaf4b8"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    CollectionLaneHalt.__table__.create(engine)
    with Session(engine) as db:
        yield db


# --- what counts as a mismatch ---------------------------------------------

def test_the_real_2026_09_14_fault_is_a_mismatch():
    """Selected SD at COL, answered with CWS at CLE, three times over."""

    found = mismatch([SD_COL], [CWS_CLE])

    assert found is not None
    assert found["unselected_games_returned"] == [CWS_CLE]
    assert found["selected_games_missing"] == [SD_COL]


def test_the_order_the_provider_returns_them_in_is_not_a_mismatch():
    """Today's good request: three selected, three returned, different order."""

    assert mismatch(["a", "b", "c"], ["a", "c", "b"]) is None


def test_a_short_answer_is_not_a_mismatch():
    """Fewer events than the limit allowed costs nothing unauthorised."""

    assert mismatch(["a", "b", "c"], ["a"]) is None


def test_one_unselected_game_among_correct_ones_still_trips_it():
    found = mismatch(["a", "b"], ["a", "b", CWS_CLE])

    assert found is not None
    assert found["unselected_games_returned"] == [CWS_CLE]
    assert found["selected_games_missing"] == []


# --- what a halt does -------------------------------------------------------

def test_a_halt_latches_and_survives_being_looked_up_again(session):
    halt_lane(session, lane="sgo_mlb_events", reason=SELECTION_MISMATCH,
              evidence={"mismatch": mismatch([SD_COL], [CWS_CLE])}, now=NOW)

    live = active_halt(session, lane="sgo_mlb_events")

    assert live is not None
    assert live.reason == SELECTION_MISMATCH
    assert live.active is True
    assert live.evidence["mismatch"]["unselected_games_returned"] == [CWS_CLE]


def test_a_second_mismatch_keeps_the_first_evidence(session):
    """The first halt is closest to the fault, so it is not overwritten."""

    first = halt_lane(session, lane="sgo_mlb_events", reason=SELECTION_MISMATCH,
                      evidence={"which": "first"}, now=NOW)
    second = halt_lane(session, lane="sgo_mlb_events", reason=SELECTION_MISMATCH,
                       evidence={"which": "second"}, now=NOW)

    assert second.id == first.id
    assert active_halt(session, lane="sgo_mlb_events").evidence == {"which": "first"}


def test_a_halt_on_one_lane_leaves_the_others_alone(session):
    """A stopped MLB lane must not disturb tonight's NFL reservation."""

    halt_lane(session, lane="sgo_mlb_events", reason=SELECTION_MISMATCH,
              evidence={}, now=NOW)

    assert active_halt(session, lane="sgo_nfl_events") is None


def test_nothing_here_lifts_a_halt():
    """Lifting is an owner judgement; no code path in this module offers it."""

    import app.services.collection_halt as module

    assert not [name for name in dir(module)
                if "lift" in name.lower() or "clear" in name.lower()
                or "resume" in name.lower()]
