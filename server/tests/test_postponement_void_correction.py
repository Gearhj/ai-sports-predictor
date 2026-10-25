"""The governed VOID correction for an MLB position already settled on a makeup game.

0d721350 (CLE ML +101, $2.42) was settled LOSS -2.42 on Sunday's makeup of a
rained-out Friday game. If -- and only if -- the owner chooses VOID, a
reviewed commit names it in GOVERNED_VOID_CORRECTIONS; the admin job then
writes ONE audited position_settlement_adjustments row (LOSS -2.42 -> VOID
0.00, delta +2.42) beside the immutable original, and every bankroll reader
counts it. These tests replicate that position exactly; nothing here is run
against production and the registry in code stays empty.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.audit.immutability import ImmutableRecordError
from app.bankroll import occurrence as occ
from app.bankroll import postponement_corrections as pc
from app.models import (
    AuditEvent, JobRun, OfficialAspPosition, PositionResult, PositionSettlement, PositionSettlementAdjustment,
    PositionStatus, RunStatus,
)
from app.services.experiment_accounts import ExperimentAccountService
from app.services.shadow_experiment_service import ShadowExperimentService
from tests.postponement_fixtures import STAKE, build_incident, fund, mlb_experiment

NOW = datetime(2026, 10, 20, 16, 0, tzinfo=UTC)


def _entry(incident, **overrides):
    values = dict(
        position_id=str(incident.position.id),
        expected_original_settlement_id=str(incident.settlement.id) if incident.settlement else "",
        expected_previous_result="LOSS", expected_stake=STAKE, expected_profit_loss=-STAKE,
        owner_decision="TEST owner chose VOID 2026-10-20 (test reference)", mode=pc.MODE_APPLY,
    )
    values.update(overrides)
    return pc.GovernedVoidCorrection(**values)


def _adjustments(session):
    return list(session.scalars(select(PositionSettlementAdjustment)))


def _audits(session):
    return list(session.scalars(select(AuditEvent).where(AuditEvent.action == pc.AUDIT_ACTION)))


def test_the_registry_in_code_is_empty_and_the_jobs_are_inert():
    assert pc.GOVERNED_VOID_CORRECTIONS == ()
    from app.jobs.orchestrator import AVAILABLE_JOBS, JobOrchestrator

    assert AVAILABLE_JOBS[pc.JOB_NAME] == pc.JOB_LABEL
    assert AVAILABLE_JOBS[pc.PLAN_JOB_NAME] == pc.PLAN_JOB_LABEL
    assert hasattr(JobOrchestrator, "correct_postponed_position_voids")
    assert hasattr(JobOrchestrator, "plan_postponed_position_voids")


def test_the_dry_run_never_writes_even_for_an_apply_entry(session):
    incident = build_incident(session, settled=True)
    result = pc.run(session, now=NOW, registry=(_entry(incident),), dry_run=True)
    assert result["dry_run"] is True and result["mode"] == pc.MODE_DRY_RUN and result["records"] == 0
    assert result["plans"][0]["status"] == pc.READY and result["plans"][0]["profit_loss_delta"] == 2.42
    assert "nothing was written" in result["note"]
    assert _adjustments(session) == [] and _audits(session) == []


def test_an_owner_named_original_date_must_match_the_stored_evidence(session):
    incident = build_incident(session, settled=True)
    matched = pc.plan(session, _entry(incident, expected_original_et_date="2026-10-16"), now=NOW)
    assert matched["status"] == pc.READY
    assert matched["verdict"]["original"]["et_date"] == "2026-10-16"
    mismatch = pc.plan(session, _entry(incident, expected_original_et_date="2026-10-18"), now=NOW)
    assert mismatch["refusal"] == pc.EXPECTATION_MISMATCH
    assert mismatch["observed"]["original_et_date"] == "2026-10-16"
    bad = pc.plan(session, _entry(incident, expected_original_et_date="Friday"), now=NOW)
    assert bad["refusal"] == pc.INVALID_ENTRY


def test_a_write_the_database_refuses_for_another_reason_is_not_reported_as_already_adjusted(
        session, monkeypatch):
    """Review F8: only an existing (position, observation) row is ALREADY_ADJUSTED."""

    incident = build_incident(session, settled=True)
    real_plan = pc.plan
    monkeypatch.setattr(pc, "plan", lambda *a, **k: {**real_plan(*a, **k), "previous_result": None})
    result = pc.run(session, now=NOW, registry=(_entry(incident),))
    assert result["records"] == 0
    assert result["plans"][0]["refusal"] == pc.WRITE_FAILED
    assert "NOT NULL" in result["plans"][0]["detail"]
    assert _adjustments(session) == [] and _audits(session) == []


def test_a_dry_run_reports_the_exact_correction_and_writes_nothing(session):
    incident = build_incident(session, settled=True)
    result = pc.run(session, now=NOW, registry=(_entry(incident, mode=pc.MODE_DRY_RUN),))

    plan = result["plans"][0]
    assert result["records"] == 0 and result["mode"] == pc.MODE_DRY_RUN
    assert plan["status"] == pc.READY and plan["applied"] is False
    assert (plan["previous_result"], plan["corrected_result"]) == ("LOSS", "VOID")
    assert (plan["previous_profit_loss"], plan["corrected_profit_loss"], plan["profit_loss_delta"]) == (
        -2.42, 0.0, 2.42)
    assert plan["game_state_observation_id"] == str(incident.postponed_observation.id)
    assert plan["original_settlement_id"] == str(incident.settlement.id)
    assert (plan["previous_home_score"], plan["previous_away_score"]) == (1, 3)
    assert plan["reason"].startswith(occ.RULE_ID)
    assert "Original LOSS settlement" in plan["reason"] and "played 2026-10-18 instead" in plan["reason"]
    assert _adjustments(session) == [] and _audits(session) == []


def test_apply_writes_exactly_one_audited_adjustment_and_leaves_the_original_intact(session):
    incident = build_incident(session, settled=True)
    actor = None
    result = pc.run(session, now=NOW, registry=(_entry(incident),), actor_user_id=actor, job_run_id=uuid4())

    assert result["records"] == 1 and result["plans"][0]["status"] == pc.APPLIED
    [adjustment] = _adjustments(session)
    assert adjustment.position_id == incident.position.id
    assert adjustment.original_settlement_id == incident.settlement.id
    assert adjustment.game_state_observation_id == incident.postponed_observation.id
    assert (adjustment.previous_result, adjustment.corrected_result) == ("LOSS", "VOID")
    assert float(adjustment.previous_profit_loss) == -2.42
    assert float(adjustment.corrected_profit_loss) == 0.0
    assert float(adjustment.profit_loss_delta) == 2.42
    assert (adjustment.previous_home_score, adjustment.previous_away_score) == (1, 3)
    assert adjustment.corrected_home_score is None and adjustment.closing_line_value is None
    assert "TEST owner chose VOID" in adjustment.reason
    [audit] = _audits(session)
    assert audit.entity_id == incident.position.id and audit.entity_type == "official_asp_position"
    assert audit.metadata_json["adjustment_id"] == str(adjustment.id)
    assert audit.metadata_json["plan"]["profit_loss_delta"] == 2.42
    # The original settlement and the settled position are untouched.
    session.expire_all()
    settlement = session.get(PositionSettlement, incident.settlement.id)
    position = session.get(OfficialAspPosition, incident.position.id)
    assert settlement.result == PositionResult.LOSS and float(settlement.profit_loss) == -2.42
    assert (position.status, position.result) == (PositionStatus.SETTLED, PositionResult.LOSS)


def test_a_second_application_is_refused_and_the_database_refuses_a_duplicate(session):
    incident = build_incident(session, settled=True)
    pc.run(session, now=NOW, registry=(_entry(incident),))
    again = pc.run(session, now=NOW, registry=(_entry(incident),))
    assert again["records"] == 0 and again["plans"][0]["refusal"] == pc.ALREADY_ADJUSTED
    assert len(_adjustments(session)) == 1 and len(_audits(session)) == 1

    session.add(PositionSettlementAdjustment(
        position_id=incident.position.id, original_settlement_id=incident.settlement.id,
        game_state_observation_id=incident.postponed_observation.id, previous_result="LOSS",
        corrected_result="VOID", previous_profit_loss=-2.42, corrected_profit_loss=0.0, profit_loss_delta=2.42,
        reason="duplicate", adjusted_at=NOW))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_an_adjustment_is_append_only(session):
    incident = build_incident(session, settled=True)
    pc.run(session, now=NOW, registry=(_entry(incident),))
    [adjustment] = _adjustments(session)
    adjustment.reason = "rewritten"
    with pytest.raises(ImmutableRecordError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize("case", [
    "same_day_delay", "expectation_mismatch", "no_settlement", "already_void", "not_mlb", "not_found",
])
def test_refusals(session, settings, case):
    if case == "same_day_delay":
        incident = build_incident(session, settled=True, history="delayed")
        expected = pc.RULE_NOT_MET + ":" + occ.REASON_PLAYED
        entry = _entry(incident)
    elif case == "expectation_mismatch":
        incident = build_incident(session, settled=True)
        expected, entry = pc.EXPECTATION_MISMATCH, _entry(incident, expected_stake=2.43)
    elif case == "no_settlement":
        incident = build_incident(session)
        expected = pc.NO_ORIGINAL_SETTLEMENT
        entry = _entry(SimpleIncident(incident), expected_original_settlement_id=str(uuid4()))
    elif case == "already_void":
        incident = build_incident(session)
        ShadowExperimentService(session, settings).settle(now=NOW)        # the new rule voids it
        settlement = session.scalar(select(PositionSettlement))
        expected = pc.ALREADY_VOID
        entry = _entry(incident, expected_original_settlement_id=str(settlement.id))
    elif case == "not_mlb":
        incident = build_incident(session, settled=True, experiment=mlb_experiment(
            session, sport="NHL", policy="TEST_NHL_POLICY", era=None))
        expected, entry = pc.NOT_MLB, _entry(incident)
    else:
        incident = build_incident(session, settled=True)
        expected, entry = pc.POSITION_NOT_FOUND, _entry(incident, position_id=str(uuid4()))
    result = pc.run(session, now=NOW, registry=(entry,))
    assert result["records"] == 0
    assert result["plans"][0]["status"] == pc.REFUSED
    assert result["plans"][0]["refusal"] == expected
    assert _adjustments(session) == [] and _audits(session) == []


class SimpleIncident:
    """An incident whose settlement does not exist yet (open position)."""

    def __init__(self, incident):
        self.position = incident.position
        self.settlement = type("S", (), {"id": uuid4()})()


# ------------------------------------------------ every reader agrees ----

def _sqlite_ledger(session, monkeypatch, now):
    from app.services import command_center

    def iso(moment):
        if isinstance(moment, str):
            moment = datetime.fromisoformat(moment)
        if moment is None:
            return None
        return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).isoformat()

    monkeypatch.setattr(command_center, "_matchups", lambda s, ids: {})
    monkeypatch.setattr(command_center, "_iso", iso)
    return command_center.experiment_ledger(session, now)


def _sqlite_board(session, monkeypatch, incident):
    """The command-center decision board's position block, on SQLite.

    The board's SQL is PostgreSQL-flavoured only in its sportsbook-name lookup
    (``= any(:k)``) and in reading the funding-pause / integrity / forecast
    helpers, which are stubbed here; the position block under test is exactly
    the production code.
    """

    from app.services import command_center
    from app.services import mlb_v18_funding_pause

    real_text = command_center.text

    def aware(moment):
        if isinstance(moment, str):
            moment = datetime.fromisoformat(moment)
        if moment is None:
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=UTC)

    monkeypatch.setattr(command_center, "_aware", aware)
    monkeypatch.setattr(command_center, "_iso", lambda moment: aware(moment).isoformat() if moment else None)
    monkeypatch.setattr(command_center, "_matchups", lambda s, ids: {})
    monkeypatch.setattr(command_center, "_market_integrity", lambda s, ids: {})
    monkeypatch.setattr(command_center, "_forecast_check", lambda s, ids, now: {})
    monkeypatch.setattr(mlb_v18_funding_pause, "funding_pauses_for_decisions", lambda s, ids: {})
    monkeypatch.setattr(command_center, "text", lambda sql: real_text(
        "select key, name from sportsbooks where 0 = 1" if "any(:k)" in sql else sql))
    board = command_center.decision_board(session, incident.decision.slate_date, NOW)
    item = next(row for row in board["items"] if UUID(row["decision_id"]) == incident.decision.id)
    return item["position"]


def _readers(session, settings, monkeypatch, incident, account):
    from app.services import mission_control, sport_readiness, wager_results_read
    from app.services.v18_champion import V18ChampionService

    service = ShadowExperimentService(session, settings)
    accounts = ExperimentAccountService(session, settings)
    ledger = service.ledger_view(incident.experiment)
    account_ledger = accounts.ledger(account)
    command = _sqlite_ledger(session, monkeypatch, NOW)
    mlb = next(item for item in command["sports"] if item["sport"] == "MLB")
    ctx = mission_control._Ctx(session, settings, date(2026, 10, 18), NOW)
    rows, _unfunded = wager_results_read._load(session)
    from app.services.read_service import ReadService

    comparison = next(item for item in ReadService(session, settings).experiment_comparison()["experiments"]
                      if item["policy"] == incident.experiment.decision_policy)
    board = _sqlite_board(session, monkeypatch, incident)
    return {
        "comparison": (comparison["current_bankroll"], comparison["profit_loss"], comparison["record"]),
        "board_position": (board["result"], board["profit_loss"], board.get("adjusted"), board.get("original_result")),
        "era_bankroll": round(service.current_bankroll(incident.experiment), 2),
        "era_available": round(service._available_bankroll(incident.experiment), 2),
        "ledger_current": ledger["current"],
        "ledger_row": (ledger["positions"][0]["result"], ledger["positions"][0]["profit_loss"]),
        "ledger_record": (ledger["betting"]["wins"], ledger["betting"]["losses"], ledger["betting"]["voids"]),
        "ledger_reconciled": ledger["betting"]["bankroll_chain_reconciled"],
        "account_settled_net": round(accounts.settled_net_pl(account), 2),
        "account_ledger_net": account_ledger.settled_net_pl,
        "account_realized": account_ledger.realized_bankroll,
        "account_record": (account_ledger.wins, account_ledger.losses, account_ledger.pushes),
        "account_curve_last": account_ledger.equity_curve[-1]["bankroll"],
        "account_era_net": account_ledger.eras[0].settled_net_pl,
        "account_row": (account_ledger.positions[0]["result"], account_ledger.positions[0]["profit_loss"]),
        "staking_bankroll": accounts.staking_basis(incident.experiment)["bankroll"],
        "readiness_realized": sport_readiness._account(session, "MLB")["realized_bankroll"],
        "command_realized": mlb["realized_bankroll"],
        "command_reconciled": mlb["reconciled"],
        "command_row": (mlb["positions"][0]["result"], mlb["positions"][0]["profit_loss"]),
        "command_losses": mlb["record"]["LOSS"],
        "command_kinds": [point["kind"] for point in mlb["series"]],
        "mission_row": tuple(mission_control._positions(ctx, [str(incident.decision.id)])[str(incident.decision.id)][k]
                             for k in ("result", "profit_loss")),
        "champion_realized": round(V18ChampionService(session, settings)._realized_profit(incident.experiment), 2),
        "wager_row": (rows[0]["result"], rows[0]["net_profit"]),
    }


def test_every_bankroll_reader_counts_the_correction_once(session, settings, monkeypatch):
    incident = build_incident(session, settled=True)
    account = fund(session, settings, incident.experiment)
    before = _readers(session, settings, monkeypatch, incident, account)
    assert before["era_bankroll"] == before["account_realized"] == before["readiness_realized"] == 997.58
    assert before["ledger_current"] == before["command_realized"] == before["account_curve_last"] == 997.58
    assert before["ledger_row"] == ("LOSS", -2.42) and before["command_row"] == ("LOSS", -2.42)
    assert before["mission_row"] == ("LOSS", -2.42) and before["wager_row"] == ("LOSS", -2.42)
    assert before["command_kinds"] == ["FUNDING", "SETTLEMENT"]
    assert before["comparison"] == (997.58, -2.42, "0–1")
    assert before["board_position"] == ("LOSS", -2.42, None, None)

    pc.run(session, now=NOW, registry=(_entry(incident),))
    after = _readers(session, settings, monkeypatch, incident, account)

    # Original 997.58 + delta 2.42 everywhere.
    for key in ("era_bankroll", "era_available", "ledger_current", "account_realized", "account_curve_last",
                "staking_bankroll", "readiness_realized", "command_realized"):
        assert after[key] == 1000.0, (key, after[key])
    for key in ("account_settled_net", "account_ledger_net", "account_era_net", "champion_realized"):
        assert after[key] == 0.0, (key, after[key])
    assert after["ledger_row"] == ("VOID", 0.0)
    assert after["ledger_record"] == (0, 0, 1)
    assert after["ledger_reconciled"] is True
    assert after["comparison"] == (1000.0, 0.0, "0–0")
    assert after["board_position"] == ("VOID", 0.0, True, "LOSS")
    assert after["account_record"] == (0, 0, 1)
    assert after["account_row"] == ("void", 0.0)
    assert after["command_row"] == ("VOID", 0.0) and after["command_losses"] == 0
    assert after["command_reconciled"] is True
    assert after["command_kinds"] == ["FUNDING", "SETTLEMENT", "ADJUSTMENT"]
    assert after["mission_row"] == ("VOID", 0.0)
    assert after["wager_row"] == ("VOID", 0.0)
    ledger = ShadowExperimentService(session, settings).ledger_view(incident.experiment)
    row = ledger["positions"][0]
    assert (row["adjusted"], row["original_result"], row["original_profit_loss"], row["adjustment_delta"]) == (
        True, "LOSS", -2.42, 2.42)
    assert row["clv"] is None and row["closing_price"] is None
    assert [point.get("kind") for point in ledger["equity"]] == [None, None, "ADJUSTMENT"]
    assert ledger["equity"][-1]["bankroll"] == 1000.0
    # Review F4: a correction takes no settlement number.
    assert [point.get("sequence") for point in ledger["equity"]][1:] == [1, None]


def test_with_no_adjustment_every_reader_is_unchanged(session, settings, monkeypatch):
    """The new reads add nothing when no adjustment exists (production today)."""

    incident = build_incident(session, settled=True)
    account = fund(session, settings, incident.experiment)
    ledger = ShadowExperimentService(session, settings).ledger_view(incident.experiment)
    assert all("adjusted" not in row for row in ledger["positions"])
    assert all("kind" not in point for point in ledger["equity"])
    assert session.scalar(select(func.count()).select_from(PositionSettlementAdjustment)) == 0
    assert ExperimentAccountService(session, settings).ledger(account).settled_net_pl == -2.42


# -------------------------------------------------------------- the job ----

@pytest.mark.asyncio
async def test_the_admin_job_runs_through_the_orchestrator(session, settings, monkeypatch):
    from app.jobs.orchestrator import JobOrchestrator

    incident = build_incident(session, settled=True)
    empty = await JobOrchestrator(session, settings).run(pc.JOB_NAME)
    assert empty.status == RunStatus.SUCCEEDED
    assert empty.result["records"] == 0 and empty.result["registry_entries"] == 0
    assert _adjustments(session) == []

    monkeypatch.setattr(pc, "GOVERNED_VOID_CORRECTIONS", (_entry(incident),))
    applied = await JobOrchestrator(session, settings).run(pc.JOB_NAME)
    assert applied.result["records"] == 1
    [audit] = _audits(session)
    assert audit.metadata_json["job_run_id"] == str(applied.id)
    assert session.scalar(select(func.count()).select_from(JobRun).where(JobRun.job_name == pc.JOB_NAME)) == 2
    assert len(f"job.{pc.JOB_NAME}.succeeded") <= 160 and len(pc.JOB_NAME) <= 120
