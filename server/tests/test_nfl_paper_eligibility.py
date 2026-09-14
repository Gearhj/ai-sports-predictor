"""The paper-research eligibility gate fails closed, and says which way.

WHY THIS GATE EXISTS
--------------------
The producer's 2026-09-14 handoff names the missing capability precisely: an
explicit binding to ONE separately authorized paper experiment. Until that
record exists, a forecast that is perfectly good research is still not
authorised for a paper wager, and the difference matters because the forecast
looks identical either way.

WHAT IT MUST NEVER DO
---------------------
Mint the record. The supplement mechanism is content-addressed PRODUCER
evidence; writing one consumer-side would be fabricating provenance about
somebody else's model. So the only outcomes here are "bound" or a named
refusal -- never "granted because it seemed fine".
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.models import ResearchSupplement
from app.services import nfl_paper_eligibility as gate

EVENT = "2026_01_DEN_KC"
FORECAST = ("asp-sports-game-forecast:sha256:"
            "2d9d0197aa7ef02c15a110cf7ce5f36ad2a87d2f1c2a92601d9989d5c487aec9")
EXPERIMENT = "c25f7272-39ef-4ad6-a196-bfae55efd3f2"
NOW = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)


def payload(**over):
    body = {field: f"value-for-{field}" for field in gate.REQUIRED_BINDINGS}
    body["paperExperimentId"] = EXPERIMENT
    body["eventId"] = EVENT
    body["forecastId"] = FORECAST
    body.update(over)
    return body


def supplement(session, *, body=None, research_shadow_only=True):
    row = ResearchSupplement(
        supplement_sha256="b" * 64,
        supplement_id="nfl-paper-eligibility-den-kc",
        contract_version=gate.CONTRACT_VERSION,
        kind=gate.KIND, sport="NFL", event_id=EVENT,
        engine_id="NFL_M1_XGBOOST", forecast_id=FORECAST,
        artifact_version="1", simulation_kind="NONE",
        imported_at=NOW, status="IMPORTED",
        research_shadow_only=research_shadow_only,
        payload=body if body is not None else payload(),
        evidence={},
    )
    session.add(row)
    session.flush()
    return row


def check(session):
    return gate.check(session, event_id=EVENT, forecast_id=FORECAST,
                      experiment_id=EXPERIMENT, now=NOW)


def test_no_record_blocks_and_names_every_field_the_producer_must_supply(session):
    result = check(session)
    assert result["eligible"] is False
    assert result["reason"] == "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD"
    assert result["blocks_position"] is True
    # The refusal is actionable: it lists exactly what a valid record binds.
    assert set(result["required_bindings"]) == set(gate.REQUIRED_BINDINGS)
    assert "fabricating provenance" in result["detail"]


def test_a_complete_producer_record_binds(session):
    supplement(session)
    result = check(session)
    assert result["eligible"] is True
    assert result["reason"] == "PAPER_RESEARCH_ELIGIBILITY_BOUND"
    assert result["blocks_position"] is False
    assert result["paper_experiment_id"] == EXPERIMENT


def test_a_record_missing_one_binding_is_refused_rather_than_narrowed(session):
    body = payload()
    del body["cohortIdentity"]
    supplement(session, body=body)
    result = check(session)
    assert result["eligible"] is False
    assert result["reason"] == "ELIGIBILITY_RECORD_INCOMPLETE"
    assert result["missing_bindings"] == ["cohortIdentity"]


def test_a_record_that_grants_execution_is_refused_outright(session):
    supplement(session, body=payload(executionAllowed=True))
    result = check(session)
    assert result["eligible"] is False
    assert result["reason"] == "ELIGIBILITY_RECORD_GRANTS_EXECUTION"
    # Refused, not quietly downgraded to a paper-only permission.
    assert result["blocks_position"] is True


def test_a_binding_to_another_experiment_is_not_a_binding_to_this_one(session):
    supplement(session, body=payload(paperExperimentId="some-other-experiment"))
    result = check(session)
    assert result["eligible"] is False
    assert result["reason"] == "ELIGIBILITY_BOUND_TO_ANOTHER_EXPERIMENT"


def test_a_record_that_is_not_research_shadow_only_is_refused(session):
    supplement(session, research_shadow_only=False)
    result = check(session)
    assert result["eligible"] is False
    assert result["reason"] == "RECORD_NOT_RESEARCH_SHADOW_ONLY"


def test_the_producer_request_names_the_kind_and_forbids_a_rename(session):
    request = gate.producer_request()
    assert request["kind"] == "PAPER_RESEARCH_ELIGIBILITY"
    assert request["contract_version"] == "ASP_NFL_PAPER_RESEARCH_ELIGIBILITY_V1"
    assert set(request["required_payload_fields"]) == set(gate.REQUIRED_BINDINGS)
    assert "researchShadowOnly" in request["flags_that_must_be_true"]
    joined = " ".join(request["must_not"])
    assert "rename an existing supplement kind" in joined
    assert "grant execution or promotion authority" in joined
