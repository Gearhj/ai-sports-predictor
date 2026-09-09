"""Focused checks for the post-study operational repair.

Only behaviour changed by that assignment: the policy-study receipt, the
readiness unavailable-reason path, and the quota guard's anchoring and renewal
awareness. Nothing here spends provider credit or touches a decision.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.features.readiness import (
    REASON_NO_SOURCE_EVIDENCE,
    ReadinessStatus,
    calculate_readiness,
)
from app.jobs.odds_quota_recovery import (
    GUARD_KNOWN_BELOW_FLOOR,
    GUARD_MAY_HAVE_RENEWED_PROBE_REQUIRED,
    PROVIDER,
    latest_successful_run,
    quota_state,
)
from app.models import IngestionRun, RunStatus
from app.shadow_import.mlb_policy_study import (
    AUDIT_ACTION,
    PolicyStudyImportError,
    record_policy_study,
    recorded_policy_studies,
    verify_policy_study,
)

RETAINED_RULES = {
    "policyId": "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
    "minimumReadiness": 70.0,
    "minimumAdjustedEdge": 0.02,
}


def _write_study(root: Path, **overrides) -> Path:
    study = {
        "schemaVersion": "MLB_SELECTION_POLICY_OPTIMIZATION_STUDY_V1",
        "verdict": "MLB_STRICT_POLICY_RETAINED",
        "evidenceTimestamp": "2026-09-08T00:02:54.260756+00:00",
        "decision": {
            "outcome": "KEEP_STRICT_CONTROL_UNCHANGED",
            "recommendedChallenger": None,
            "aispShadowPolicyHandoff": None,
            "reasons": ["Only nine actual control positions are settled."],
        },
        "authority": {
            "researchOnly": True,
            "wageringAuthorityGranted": False,
            "officialPolicyChanged": False,
            "finalOotOpened": False,
            "aispWrites": 0,
            "providerCalls": 0,
            "positionsCreatedAlteredOrSettled": 0,
            "modelFits": 0,
        },
        "retainedControlRules": dict(RETAINED_RULES),
        "source": {"baseCommit": "3cc9d67"},
        "limitations": {},
    }
    study.update(overrides)
    body = json.dumps(study, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    study["studySha256"] = digest
    study["studyId"] = f"mlb-selection-policy-study-{digest[:24]}"
    directory = root / digest
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "study.json"
    path.write_text(json.dumps(study, indent=1), encoding="utf-8")
    return path


def test_a_retained_study_verifies_and_records_once(session, tmp_path: Path) -> None:
    verified = verify_policy_study(_write_study(tmp_path))
    assert verified.verdict == "MLB_STRICT_POLICY_RETAINED"

    first = record_policy_study(session, verified)
    assert first["recorded"] is True
    assert first["policyActivated"] is False
    session.flush()

    second = record_policy_study(session, verified)
    assert second["recorded"] is False
    assert second["alreadyRecorded"] is True

    receipts = recorded_policy_studies(session)
    assert len(receipts) == 1
    assert receipts[0]["verdict"] == "MLB_STRICT_POLICY_RETAINED"
    assert receipts[0]["policy_activated"] is False
    assert receipts[0]["challenger_created"] is False
    # The receipt is one immutable audit row: it creates no decision or position.
    from sqlalchemy import func, select

    from app.models import AuditEvent, OfficialAspPosition, ShadowExperimentDecision

    assert session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.action == AUDIT_ACTION)
    ) == 1
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 0
    assert session.scalar(select(func.count()).select_from(ShadowExperimentDecision)) == 0


@pytest.mark.parametrize("override,match", [
    ({"verdict": "MLB_RELAXED_POLICY_RECOMMENDED"}, "Only MLB_STRICT_POLICY_RETAINED"),
    ({"decision": {"outcome": "KEEP_STRICT_CONTROL_UNCHANGED",
                   "recommendedChallenger": "GEN1_EDGE_0175", "reasons": []}},
     "recommends a challenger"),
    ({"authority": {"researchOnly": True, "wageringAuthorityGranted": False,
                    "officialPolicyChanged": True, "finalOotOpened": False,
                    "aispWrites": 0, "providerCalls": 0,
                    "positionsCreatedAlteredOrSettled": 0, "modelFits": 0}},
     "officialPolicyChanged"),
    ({"retainedControlRules": {"policyId": "AISP_OPPORTUNITY_POLICY_V1"}},
     "not the live"),
])
def test_a_study_that_would_change_policy_is_refused(tmp_path: Path, override, match) -> None:
    with pytest.raises(PolicyStudyImportError, match=match):
        verify_policy_study(_write_study(tmp_path, **override))


def test_an_edited_study_no_longer_hashes_to_its_identity(tmp_path: Path) -> None:
    path = _write_study(tmp_path)
    study = json.loads(path.read_text(encoding="utf-8"))
    study["evidenceTimestamp"] = "2026-01-01T00:00:00+00:00"
    path.write_text(json.dumps(study, indent=1), encoding="utf-8")
    with pytest.raises(PolicyStudyImportError, match="hash to its published identity"):
        verify_policy_study(path)


def test_an_unavailable_component_names_its_reason_without_earning_points() -> None:
    result = calculate_readiness(
        {"bullpen": ReadinessStatus.MISSING, "schedule": ReadinessStatus.READY},
        {},
        reasons={"bullpen": REASON_NO_SOURCE_EVIDENCE},
        weights={"bullpen": 5.0, "schedule": 10.0},
        critical=frozenset(),
    )
    bullpen = next(item for item in result.components if item.key == "bullpen")
    schedule = next(item for item in result.components if item.key == "schedule")
    # The reason explains the gap; it never closes it.
    assert bullpen.unavailable_reason == REASON_NO_SOURCE_EVIDENCE
    assert bullpen.status is ReadinessStatus.MISSING
    assert bullpen.score == 0.0
    assert bullpen.weight == 5.0
    assert schedule.unavailable_reason is None
    assert result.score == pytest.approx(100 * 10.0 / 15.0, abs=0.05)


def test_a_run_without_a_reading_cannot_clear_a_latched_quota(session) -> None:
    now = datetime.now(UTC)
    session.add(IngestionRun(
        source=PROVIDER, job_name="refresh-odds", status=RunStatus.SUCCEEDED,
        started_at=now - timedelta(hours=40), completed_at=now - timedelta(hours=40),
        source_metadata={"requests_remaining": 5},
    ))
    # A later success that says nothing about the balance must not become the
    # anchor: absence of evidence is not evidence of quota.
    session.add(IngestionRun(
        source=PROVIDER, job_name="refresh-odds", status=RunStatus.SUCCEEDED,
        started_at=now - timedelta(hours=1), completed_at=now - timedelta(hours=1),
        source_metadata={"event_count": 3},
    ))
    session.flush()

    anchor = latest_successful_run(session)
    assert (anchor.source_metadata or {}).get("requests_remaining") == 5

    state = quota_state(
        session, floor=10, probe_after_hours=24.0, probe_cooldown_hours=24.0,
        renewal_days=30.0, now=now,
    )
    assert state.latched is True
    assert state.guard_state == GUARD_KNOWN_BELOW_FLOOR
    assert state.reading_may_have_renewed is False


def test_renewal_follows_the_monthly_reset_not_an_elapsed_window(session) -> None:
    """The provider resets credits on the 1st, so obsolescence is calendar-based.

    A reading taken inside the current billing month is still current however
    old it is; one taken before the most recent 1st is unconfirmed, which is
    not the same as saying credits are available.
    """

    now = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
    # 40 days old but inside no earlier month than the reset: taken 2026-09-02,
    # after the 2026-09-01 reset, so it is still a current statement.
    session.add(IngestionRun(
        source=PROVIDER, job_name="refresh-odds", status=RunStatus.SUCCEEDED,
        started_at=datetime(2026, 9, 2, tzinfo=UTC),
        completed_at=datetime(2026, 9, 2, tzinfo=UTC),
        source_metadata={"requests_remaining": 5},
    ))
    session.flush()
    fresh = quota_state(session, floor=10, probe_after_hours=24.0,
                        probe_cooldown_hours=24.0, now=now)
    assert fresh.guard_state == GUARD_KNOWN_BELOW_FLOOR
    assert fresh.reading_may_have_renewed is False
    assert fresh.as_dict()["balance_confirmed"] is True


def test_a_reading_from_before_the_monthly_reset_is_unconfirmed(session) -> None:
    now = datetime.now(UTC)
    session.add(IngestionRun(
        source=PROVIDER, job_name="refresh-odds", status=RunStatus.SUCCEEDED,
        started_at=now - timedelta(days=45), completed_at=now - timedelta(days=45),
        source_metadata={"requests_remaining": 5},
    ))
    session.flush()

    state = quota_state(
        session, floor=10, probe_after_hours=24.0, probe_cooldown_hours=24.0,
        renewal_days=30.0, now=now,
    )
    assert state.latched is True
    assert state.guard_state == GUARD_MAY_HAVE_RENEWED_PROBE_REQUIRED
    assert state.reading_may_have_renewed is True
    reason = state.probe_blocked_reason or ""
    assert "monthly credit reset" in reason
    # Crossing the boundary makes the number unconfirmed, never available.
    assert "does not establish that credits are available" in reason
    assert state.as_dict()["balance_confirmed"] is False
    # The floor still holds and nothing is spent automatically.
    assert state.floor == 10
    assert state.as_dict()["renewal_basis"] == "PROVIDER_MONTHLY_CREDIT_RESET_ON_THE_1ST"
    assert state.as_dict()["authorized_probe_command"].startswith(
        "python -m scripts.odds_quota_recovery_probe --balance-only"
    )
