"""Every string a sport's paper path writes into a length-limited column fits the production (Postgres) length.

2026-10-17 00:30-00:40Z every NHL decision insert failed in production with
``StringDataRightTruncation`` (the period label was 41 characters for VARCHAR(40)) while every test passed:
SQLite does not enforce VARCHAR lengths. Three layers keep that from recurring, for MLB, NFL, CFB, NHL and NBA:

1. ``tests/postgres_column_lengths.py`` + the autouse fixture in ``conftest.py`` refuse, in EVERY test, a flush
   that writes an over-length string into ``shadow_experiment_decisions``, ``official_asp_positions``,
   ``shadow_experiments`` or ``audit_events`` (the MLB funding receipt) -- so each sport's existing writer tests
   run under the Postgres limits (the first tests below prove the guard is live and refuses).
2. Every literal the code passes to those models' constructors anywhere under ``app/`` -- including a
   conditional's branches and module constants such as ``DECISION_PERIOD`` -- is measured against its column.
3. Per sport, the vocabularies that reach those columns indirectly are measured: the outcomes stored as
   ``recommendation``, the reason codes stored as ``qualification_reason``/``execution_reason`` (the shared-$100
   reasons of AISP_SHARED_100_PRIORITY_MLB_NHL_V1 included), and the policy versions an era stamps on its
   positions (``strategy`` is VARCHAR(64) while the experiment's own column allows 80).
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    AuditEvent, JobRun, OfficialAspPosition, PositionSettlement, PositionSettlementAdjustment, ShadowExperiment,
    ShadowExperimentDecision,
)
from tests import postgres_column_lengths as lengths

APP = Path(__file__).resolve().parents[1] / "app"
MODELS = {m.__name__: m for m in (ShadowExperimentDecision, OfficialAspPosition, ShadowExperiment, AuditEvent,
                                  PositionSettlement, PositionSettlementAdjustment)}

#: The module that records each sport's paper decision (and its position).
WRITERS = {
    "MLB": "app.services.shadow_experiment_service",
    "NFL": "app.services.nfl_paper_decision",
    "CFB": "app.cfb.paper_decision",
    "NHL": "app.nhl.paper",
    "NBA": "app.nba.gen1_paper",
}


def _limit(model: Any, key: str) -> int | None:
    entry = lengths.limited_columns(model).get(key)
    return entry[1] if entry else None


# ---------------------------------------------------------------- 1. the guard --

def test_the_suite_wide_guard_is_installed_and_refuses_an_over_length_decision(session):
    assert lengths.installed()
    row = ShadowExperimentDecision(phase="FINAL_PREGAME", sport="NHL", market="MONEYLINE",
                                   period="full_game_including_overtime_and_shootout",   # 41 > 40 (the 10-17 bug)
                                   selection="PIT", recommendation="QUALIFIED_BET", source_mode="LIVE")
    session.add(row)
    with pytest.raises(lengths.PostgresLengthViolation, match=r"shadow_experiment_decisions\.period VARCHAR\(40\)"):
        session.flush()
    assert [v[:3] for v in lengths.drain()] == [("shadow_experiment_decisions", "period", 40)]
    session.rollback()


def test_the_guard_measures_positions_and_skips_enum_columns():
    position = OfficialAspPosition(strategy="X" * 65, recommendation_policy_version="AISP_NHL_PAPER_POLICY_V1")
    assert [(t, c, n) for t, c, n, _v in lengths.check_row(position)] == [("official_asp_positions", "strategy", 64)]
    assert "result" not in lengths.limited_columns(OfficialAspPosition)       # sa.Enum: validated by SQLAlchemy
    assert lengths.check_row(OfficialAspPosition(strategy="X" * 64)) == []


# ------------------------------------------------- 2. constructor literals in app/ --

def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(APP.parent).with_suffix("").parts)


def _resolve(node: ast.AST, module: Any) -> list[str] | None:
    """The string values an expression can take, when they are knowable statically (else None)."""

    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    if isinstance(node, ast.IfExp):
        out = []
        for branch in (node.body, node.orelse):
            got = _resolve(branch, module)
            if got:
                out.extend(got)
        return out or None
    if isinstance(node, ast.BoolOp):
        out = [v for value in node.values for v in (_resolve(value, module) or [])]
        return out or None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str" and node.args:
        return _resolve(node.args[0], module)
    if isinstance(node, (ast.Name, ast.Attribute)):
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name) or module is None or not hasattr(module, cur.id):
            return None
        value = getattr(module, cur.id)
        for attr in reversed(parts):
            if not hasattr(value, attr):
                return None
            value = getattr(value, attr)
        return [value] if isinstance(value, str) else None
    return None


def _constructor_literals() -> list[tuple[str, int, str, str, str]]:
    """(module, line, model, keyword, value) for every statically knowable string passed to a guarded model."""

    found = []
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(name + "(" in source for name in MODELS):
            continue
        tree = ast.parse(source)
        module = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else None)
            if name not in MODELS:
                continue
            if module is None:
                module = importlib.import_module(_module_name(path))
            for keyword in node.keywords:
                for value in _resolve(keyword.value, module) or []:
                    found.append((_module_name(path), node.lineno, name, keyword.arg, value))
    return found


CONSTRUCTOR_LITERALS = _constructor_literals()


def test_every_constructor_literal_fits_its_postgres_column():
    too_long = [(mod, line, model, key, value, _limit(MODELS[model], key))
                for mod, line, model, key, value in CONSTRUCTOR_LITERALS
                if _limit(MODELS[model], key) is not None and len(value) > _limit(MODELS[model], key)]
    assert not too_long, too_long


@pytest.mark.parametrize("sport", sorted(WRITERS))
def test_each_sports_writer_is_measured(sport):
    """The scan reaches each sport's decision constructor and resolves its fixed labels."""

    seen = {key: value for mod, _line, model, key, value in CONSTRUCTOR_LITERALS
            if mod == WRITERS[sport] and model == "ShadowExperimentDecision"}
    assert {"sport", "market", "period", "phase", "source_mode"} <= set(seen) or sport == "MLB", (sport, seen)
    assert {"sport", "market", "period"} <= set(seen), (sport, seen)
    for key, value in seen.items():
        assert len(value) <= (_limit(ShadowExperimentDecision, key) or 10 ** 6), (sport, key, value)


# --------------------------------------------------- 3. per-sport vocabularies --

CODE = re.compile(r"^[A-Z][A-Z0-9_]+$")     # a stored code; prose reasons go to Text columns


def _module_strings(module_name: str) -> tuple[set[str], set[str], dict[str, set[str]]]:
    """(outcomes, reason codes, {assigned variable: codes}) written in a module's source."""

    module = importlib.import_module(module_name)
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    outcomes, reasons, assigned = set(), set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in ("outcome", "reason"):
                    for got in _resolve(value, module) or []:
                        (outcomes if key.value == "outcome" else reasons).add(got)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            if target in ("state", "qualification_outcome", "qualification_reason", "post_reason",
                          "execution_outcome", "execution_reason"):
                assigned.setdefault(target, set()).update(_resolve(node.value, module) or [])
    for value in vars(module).values():                       # module-level reason vocabularies
        if isinstance(value, frozenset) and value and all(isinstance(v, str) for v in value):
            reasons.update(value)
    keep = lambda values: {v for v in values if CODE.match(v)}  # noqa: E731
    return keep(outcomes), keep(reasons), {k: keep(v) for k, v in assigned.items()}


@pytest.mark.parametrize("sport", sorted(WRITERS))
def test_each_sports_recorded_outcomes_and_reasons_fit(sport):
    outcomes, reasons, assigned = _module_strings(WRITERS[sport])
    recommendation = _limit(ShadowExperimentDecision, "recommendation")             # 24
    reason_cols = min(_limit(ShadowExperimentDecision, "qualification_reason"),
                      _limit(ShadowExperimentDecision, "execution_reason"))          # 80
    outcome_cols = min(_limit(ShadowExperimentDecision, "qualification_outcome"),
                       _limit(ShadowExperimentDecision, "execution_outcome"))        # 40
    assert outcomes or sport == "MLB", f"the {sport} outcome vocabulary was not found"
    # recommendation = "QUALIFIED_BET" or the decision's outcome (MLB: its `state`).
    stored = outcomes | assigned.get("state", set()) | {"QUALIFIED_BET", "NO_BET"}
    assert {o for o in stored if len(o) > recommendation} == set(), sport
    every_reason = reasons.union(*assigned.values()) if assigned else reasons
    assert every_reason, f"the {sport} reason vocabulary was not found"
    assert {r for r in every_reason if len(r) > reason_cols} == set(), sport
    for target in ("qualification_outcome", "execution_outcome"):
        assert {v for v in assigned.get(target, set()) if len(v) > outcome_cols} == set(), (sport, target)
    if sport == "NFL":   # the only writer of the two-question columns: qualification_outcome = the outcome
        assert {o for o in outcomes if len(o) > outcome_cols} == set()
        nfl = [(key, value) for mod, _line, model, key, value in CONSTRUCTOR_LITERALS
               if mod == WRITERS["NFL"] and key in ("execution_outcome", "execution_reason")]
        assert {"EXECUTED", "NOT_EXECUTED", "POSITION_TAKEN"} <= {v for _k, v in nfl}, nfl


def test_the_shared_100_budget_reasons_fit_every_reason_and_outcome_column():
    """AISP_SHARED_100_PRIORITY_MLB_NHL_V1's honest reasons are stored by NFL as execution_reason and by MLB in
    its funding receipt; SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS is exactly 40 characters."""

    from app.services import shared_daily_allowance as shared

    for code in (shared.ROOM_RESERVED, "SHARED_ALLOWANCE_EXHAUSTED", "SHARED_ALLOWANCE_NOT_EFFECTIVE"):
        for key in ("qualification_outcome", "execution_outcome", "qualification_reason", "execution_reason"):
            assert len(code) <= _limit(ShadowExperimentDecision, key), (code, key)
    assert len(shared.ROOM_RESERVED) == 40


def _era_versions() -> dict[str, tuple[str, str, str]]:
    """sport -> (experiment sport label, recommendation policy version, staking policy version)."""

    from app.nhl import paper as nhl
    from app.services import nfl_paper_policy as nfl
    from app.services import shadow_experiment_service as mlb
    from app.services.mlb_v18_funding_pause import AUTHORITY
    from app.services.research_paper_policy import POLICIES

    out = {"MLB": (mlb.SUPPORTED_SPORT, mlb.RECOMMENDATION_POLICY_VERSION, mlb.STAKING_POLICY_VERSION),
           "MLB_V18": (mlb.SUPPORTED_SPORT, str(AUTHORITY.get("recommendationPolicyVersion") or ""),
                       str(AUTHORITY["stakingPolicyVersion"])),
           "NFL": ("NFL", nfl.POLICY_VERSION, nfl.STAKING_VERSION),
           "NHL": (nhl.POLICY.sport, nhl.POLICY.policy_version, nhl.POLICY.staking_version)}
    for sport, policy in POLICIES.items():                      # CFB, NBA
        out[sport] = (policy.sport, policy.policy_version, policy.staking_version)
    return out


@pytest.mark.parametrize("sport", ["MLB", "MLB_V18", "NFL", "CFB", "NHL", "NBA"])
def test_each_eras_policy_versions_fit_the_position_columns(sport):
    label, recommendation_version, staking_version = _era_versions()[sport]
    assert len(label) <= _limit(ShadowExperiment, "sport")
    assert len(staking_version) <= _limit(OfficialAspPosition, "strategy")               # 64, tighter than 80
    assert len(staking_version) <= _limit(OfficialAspPosition, "staking_policy_version")
    assert len(recommendation_version) <= _limit(OfficialAspPosition, "recommendation_policy_version")


def test_the_postponement_void_vocabulary_fits_its_columns():
    """AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1: the settlement rows and the governed
    correction store result NAMES in VARCHAR(16) (adjustments) and the enum columns; the rule's
    prose (note, reason) goes to TEXT; the job and its audit actions fit job_runs / audit_events."""

    from app.bankroll import postponement_corrections as corrections
    from app.models import PositionResult

    for column in ("previous_result", "corrected_result"):
        limit = _limit(PositionSettlementAdjustment, column)
        assert limit == 16
        assert all(len(member.name) <= limit for member in PositionResult)
    assert "settlement_note" not in lengths.limited_columns(PositionSettlement)        # TEXT
    assert "reason" not in lengths.limited_columns(PositionSettlementAdjustment)       # TEXT
    for job in (corrections.JOB_NAME, corrections.PLAN_JOB_NAME):
        assert len(job) <= _limit(JobRun, "job_name")
        for suffix in ("succeeded", "failed"):
            assert len(f"job.{job}.{suffix}") <= _limit(AuditEvent, "action")
    assert len(corrections.AUDIT_ACTION) <= _limit(AuditEvent, "action")
    assert len(corrections.AUDIT_ENTITY) <= _limit(AuditEvent, "entity_type")
    # The settlement owner's operating alert for an unverified settlement.
    from app.bankroll import occurrence

    assert len(occurrence.REVIEW_ALERT_ACTION) <= _limit(AuditEvent, "action")
    assert len(occurrence.REVIEW_ALERT_ENTITY) <= _limit(AuditEvent, "entity_type")


def test_the_guard_measures_settlement_and_correction_rows():
    row = PositionSettlementAdjustment(previous_result="LOSS", corrected_result="X" * 17, reason="r")
    assert [(t, c, n) for t, c, n, _v in lengths.check_row(row)] == [
        ("position_settlement_adjustments", "corrected_result", 16)]
    assert lengths.check_row(PositionSettlementAdjustment(previous_result="LOSS", corrected_result="VOID")) == []
