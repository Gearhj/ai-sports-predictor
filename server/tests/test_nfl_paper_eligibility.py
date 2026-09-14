"""The paper-research eligibility gate, tested on the real delivered package.

WHY THE FIXTURE IS THE PUBLISHED BYTES
--------------------------------------
This gate previously had a fixture of my own invention, built from the field
names I had PROPOSED to the producer. The producer delivered a different and
better shape -- notably binding `experimentId` as the stable
`policy:sport:season` key rather than an AISP database UUID, because a producer
cannot observe AISP's database and should not claim a value it could not have
verified. A gate tested against its own proposal proves nothing about the
document it will actually receive, so the fixture is now read from the
published supplement.

WHAT IT MUST NEVER DO
---------------------
Mint the record. The supplement mechanism is content-addressed PRODUCER
evidence; writing one consumer-side would be fabricating provenance about
somebody else's model. The only outcomes are "bound" or a named refusal.
"""
from __future__ import annotations

import copy
import json
import pathlib
from datetime import UTC, datetime

import pytest

from app.models import ResearchSupplement
from app.services import nfl_paper_eligibility as gate

DELIVERED = pathlib.Path(
    r"C:\Users\james\asp-model-exchange\nfl-labs-supplements\sha256"
    r"\391240384408f975febde5c8ac369cd55a814c9d8bd652c4880e5be16aea7c9f"
    r"\supplement.json")

EVENT = "2026_01_DEN_KC"
FORECAST = ("asp-sports-game-forecast:sha256:"
            "2d9d0197aa7ef02c15a110cf7ce5f36ad2a87d2f1c2a92601d9989d5c487aec9")
EXPERIMENT_UUID = "c25f7272-39ef-4ad6-a196-bfae55efd3f2"
NOW = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    not DELIVERED.exists(),
    reason="the producer's published eligibility package is not present")


def document() -> dict:
    return json.loads(DELIVERED.read_bytes())


def store(session, doc, *, research_shadow_only=True):
    session.add(ResearchSupplement(
        supplement_sha256="3912403844" + "0" * 54,
        supplement_id=doc["supplementId"],
        contract_version=gate.CONTRACT_VERSION,
        kind=doc["kind"], sport=doc["sport"],
        event_id=doc["binding"]["eventId"],
        engine_id=doc["binding"]["engineId"],
        forecast_id=doc["binding"]["forecastId"],
        artifact_version=doc["artifactVersion"],
        simulation_kind=doc["simulation"]["kind"],
        imported_at=NOW, status="IMPORTED",
        research_shadow_only=research_shadow_only,
        payload=doc, evidence={}))
    session.flush()


def check(session):
    return gate.check(session, event_id=EVENT, forecast_id=FORECAST,
                      experiment_id=EXPERIMENT_UUID, now=NOW)


def test_no_record_blocks_and_names_what_the_producer_must_bind(session):
    result = check(session)
    assert result["eligible"] is False
    assert result["reason"] == "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD"
    assert result["blocks_position"] is True
    assert set(result["required_bindings"]) == set(gate.REQUIRED_BINDINGS)
    assert "fabricating provenance" in result["detail"]


def test_the_delivered_package_binds(session):
    store(session, document())
    result = check(session)
    assert result["eligible"] is True, result.get("detail") or result["reason"]
    assert result["reason"] == "PAPER_RESEARCH_ELIGIBILITY_BOUND"
    assert result["blocks_position"] is False
    # The producer's stable key, not a UUID it could not have observed.
    assert result["paper_experiment_key"] == "AISP_NFL_PAPER_POLICY_V1:NFL:2026"
    assert result["aisp_experiment_id"] == EXPERIMENT_UUID


def test_all_three_frozen_masses_travel_untouched(session):
    """A reader shown two of three cannot tell a zero from a dropped mass."""

    store(session, document())
    frozen = check(session)["frozen_probabilities"]
    assert frozen == {"away_win": 0.5753, "home_win": 0.3817, "tie": 0.043}
    assert round(sum(frozen.values()), 9) == 1.0


def test_a_record_missing_one_binding_is_refused_rather_than_narrowed(session):
    doc = document()
    del doc["summaries"]["authorization"]["policyVersion"]
    store(session, doc)
    result = check(session)
    assert result["eligible"] is False
    assert result["reason"] == "ELIGIBILITY_RECORD_INCOMPLETE"
    assert "summaries.authorization.policyVersion" in result["missing_bindings"]


@pytest.mark.parametrize("path", [
    "governance.executionAllowedInAisp",
    "governance.modelPromotionAllowed",
    "governance.wageringAuthority",
    "summaries.authorization.realMoneyAuthorized",
    "summaries.frozenConfiguration.executionAllowed",
])
def test_any_granted_authority_is_refused_outright(session, path):
    doc = document()
    node = doc
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = True
    store(session, doc)
    result = check(session)
    assert result["eligible"] is False
    assert result["reason"] == "ELIGIBILITY_RECORD_GRANTS_EXECUTION"
    # Refused, never quietly narrowed to something acceptable.
    assert result["blocks_position"] is True


def test_a_binding_to_another_experiment_is_not_a_binding_to_this_one(session):
    doc = document()
    doc["summaries"]["authorization"]["experimentId"] = "SOME_OTHER_POLICY:NFL:2026"
    store(session, doc)
    result = check(session)
    assert result["eligible"] is False
    assert result["reason"] == "ELIGIBILITY_BOUND_TO_ANOTHER_EXPERIMENT"


def test_a_binding_to_another_policy_is_refused(session):
    doc = document()
    doc["summaries"]["authorization"]["policyVersion"] = "AISP_PRACTICAL_PREGAME_POLICY_V2_1"
    store(session, doc)
    assert check(session)["reason"] == "ELIGIBILITY_BOUND_TO_ANOTHER_POLICY"


def test_a_record_freezing_another_forecast_is_refused(session):
    doc = document()
    doc["summaries"]["frozenConfiguration"]["forecastId"] = "asp-sports-game-forecast:sha256:" + "f" * 64
    store(session, doc)
    assert check(session)["reason"] == "ELIGIBILITY_FREEZES_ANOTHER_FORECAST"


def test_a_record_that_is_not_research_shadow_only_is_refused(session):
    store(session, document(), research_shadow_only=False)
    assert check(session)["reason"] == "RECORD_NOT_RESEARCH_SHADOW_ONLY"


def test_a_non_virtual_mode_is_refused(session):
    doc = document()
    doc["summaries"]["authorization"]["mode"] = "FUNDED"
    store(session, doc)
    assert check(session)["reason"] == "ELIGIBILITY_MODE_IS_NOT_VIRTUAL_RESEARCH"
