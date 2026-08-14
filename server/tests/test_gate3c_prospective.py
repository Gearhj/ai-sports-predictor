from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import ImmutableRecordError
from app.audit.immutability import _APPEND_ONLY_TYPES
from app.models import (
    ForecastTrack,
    ProspectiveEvaluationClaim as ProspectiveEvaluationClaimRecord,
)
from app.models import (
    ProspectiveEvaluationClaimBinding as ProspectiveEvaluationClaimBindingRecord,
)
from app.models import (
    ProspectiveEvaluationGameAssignment as ProspectiveEvaluationGameAssignmentRecord,
)
from app.models import (
    ProspectiveEvaluationLabelAccess as ProspectiveEvaluationLabelAccessRecord,
)
from app.models import (
    ProspectiveEvaluationPlan as ProspectiveEvaluationPlanRecord,
)

from app.gen2.prospective import (
    ALLOWED_GEN2_TERMINAL_DECISIONS,
    ASP_INDEPENDENT_FORECAST_FAMILY,
    ASP_MARKET_AUGMENTED_FORECAST_FAMILY,
    CORRECTION_POLICY,
    CURRENT_DEVELOPMENT_FORECAST_TRACK,
    DECLARED_FORECAST_LIFECYCLES,
    ELIGIBILITY_POLICY,
    EXCLUSION_POLICY,
    FINAL_COMPLETED_GAME_THRESHOLD,
    GEN2_PROSPECTIVE_REGISTRATION_STATUS,
    GEN2_RESEARCH_FORECAST_TRACK,
    Gen2ResearchDecision,
    INTERIM_COMPLETED_GAME_THRESHOLD,
    POSTPONEMENT_POLICY,
    PRIMARY_PROSPECTIVE_LIFECYCLE,
    PROSPECTIVE_POLICY_IDENTITY_CHECKSUM,
    PROSPECTIVE_POLICY_VERSION,
    REQUIRED_FROZEN_COMPONENTS,
    ClaimedTargetLabel,
    EligibleProspectiveGame,
    ForecastLifecycle,
    FrozenComponentIdentity,
    ProspectiveCheckpoint,
    ProspectiveClaimError,
    ProspectiveDecisionRecord,
    ProspectiveEvaluationPlan,
    ProspectiveExclusionReason,
    ProspectiveForecastSnapshot,
    ProspectiveGovernanceError,
    ProspectiveGovernanceLedger,
    ProspectiveLabelAccessError,
    ProspectivePlanDriftError,
    RuntimeIdentitySnapshot,
    TargetRevisionReference,
    assess_forecast_eligibility,
    prospective_game_assignment_checksum,
    require_gen2_research_decision,
    target_revision_commitment_checksum,
)
from app.gen2.prospective_persistence import (
    ProspectivePersistenceError,
    SqlAlchemyProspectiveLabelAccessGate,
)


BASE = datetime(2026, 4, 1, 12, tzinfo=UTC)


def _sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _runtime(suffix: str = "v1") -> RuntimeIdentitySnapshot:
    return RuntimeIdentitySnapshot(
        code_revision="0e83c521a1dbfbc7bb0deae50b4e55c2a9c178bb",
        code_checksum=_sha(f"code:{suffix}"),
        schema_bundle_version=f"mlb_gen2_schema_bundle_{suffix}",
        schema_bundle_checksum=_sha(f"schema:{suffix}"),
        components=tuple(
            FrozenComponentIdentity(
                name=name,
                version=f"mlb_gen2_{name}_{suffix}",
                checksum=_sha(f"{name}:{suffix}"),
            )
            for name in REQUIRED_FROZEN_COMPONENTS
        ),
    )


def _alembic_config(database_url: str) -> Config:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _plan(runtime: RuntimeIdentitySnapshot | None = None, *, plan_id: str = "plan-2026-v1"):
    return ProspectiveEvaluationPlan(
        plan_id=plan_id,
        created_at=BASE,
        evaluation_starts_at=BASE + timedelta(hours=1),
        runtime_identity=runtime or _runtime(),
    )


def _forecast(
    plan: ProspectiveEvaluationPlan,
    *,
    game_id: str = "game-0001",
    occurrence_id: str = "occurrence-0001",
    **changes: object,
) -> ProspectiveForecastSnapshot:
    values: dict[str, object] = {
        "game_id": game_id,
        "occurrence_id": occurrence_id,
        "forecast_id": f"forecast-{game_id}",
        "generated_at": BASE + timedelta(hours=2),
        "scheduled_first_pitch": BASE + timedelta(hours=3),
        "season": 2026,
        "game_type": "R",
        "evidence_mode": "LIVE",
        "lifecycle": ForecastLifecycle.T_MINUS_10,
        "forecast_track": GEN2_RESEARCH_FORECAST_TRACK,
        "forecast_family": ASP_INDEPENDENT_FORECAST_FAMILY,
        "plan_identity_checksum": plan.plan_identity_checksum,
        "model_identity_checksum": plan.model_identity.checksum,
        "lineage_checksum": _sha(f"lineage:{game_id}"),
    }
    values.update(changes)
    return ProspectiveForecastSnapshot(**values)  # type: ignore[arg-type]


def _games(plan: ProspectiveEvaluationPlan, count: int) -> tuple[EligibleProspectiveGame, ...]:
    rows = []
    for index in range(count):
        game_id = f"game-{index:04d}"
        assessment = assess_forecast_eligibility(
            plan,
            (_forecast(plan, game_id=game_id, occurrence_id=f"occurrence-{index:04d}"),),
        )
        assert assessment.eligible and assessment.binding is not None
        rows.append(assessment.binding)
    return tuple(rows)


def _revisions(
    games: tuple[EligibleProspectiveGame, ...],
    *,
    available_at: datetime = BASE + timedelta(hours=5),
) -> tuple[TargetRevisionReference, ...]:
    rows = []
    for game in games:
        revision_id = f"target-{game.game_id}-0"
        rows.append(
            TargetRevisionReference(
                game_id=game.game_id,
                occurrence_id=game.occurrence_id,
                revision_id=revision_id,
                revision_checksum=target_revision_commitment_checksum(
                    game_id=game.game_id,
                    occurrence_id=game.occurrence_id,
                    revision_id=revision_id,
                    revision_sequence=0,
                    payload=_label_payload(),
                ),
                revision_sequence=0,
                available_at=available_at,
            )
        )
    return tuple(rows)


def _label_payload():
    return {"home_runs": 4, "away_runs": 3}


def _labels_for(references):
    return tuple(
        ClaimedTargetLabel(
            game_id=item.game_id,
            occurrence_id=item.occurrence_id,
            revision_id=item.revision_id,
            revision_checksum=item.revision_checksum,
            revision_sequence=item.revision_sequence,
            payload=_label_payload(),
        )
        for item in references
    )


def test_frozen_policy_declares_exact_lifecycle_tracks_thresholds_and_decisions() -> None:
    assert DECLARED_FORECAST_LIFECYCLES == (
        "Overnight",
        "Morning",
        "T-6",
        "T-3",
        "T-60",
        "T-30",
        "T-10",
        "Postgame",
    )
    assert PRIMARY_PROSPECTIVE_LIFECYCLE == "FINAL_T_MINUS_10"
    assert INTERIM_COMPLETED_GAME_THRESHOLD == 300
    assert FINAL_COMPLETED_GAME_THRESHOLD == 500
    assert ProspectiveCheckpoint.INTERIM_300.minimum_completed_games == 300
    assert ProspectiveCheckpoint.FINAL_500.minimum_completed_games == 500
    assert CURRENT_DEVELOPMENT_FORECAST_TRACK != GEN2_RESEARCH_FORECAST_TRACK
    assert CURRENT_DEVELOPMENT_FORECAST_TRACK == ForecastTrack.CURRENT_DEVELOPMENT.value
    assert (
        GEN2_RESEARCH_FORECAST_TRACK
        == ForecastTrack.GEN2_RESEARCH_CHALLENGER.value
    )
    assert ASP_INDEPENDENT_FORECAST_FAMILY != ASP_MARKET_AUGMENTED_FORECAST_FAMILY
    assert GEN2_PROSPECTIVE_REGISTRATION_STATUS == "GEN2_PROSPECTIVE_RESEARCH_CHALLENGER"
    assert ALLOWED_GEN2_TERMINAL_DECISIONS == (
        "GEN2_RESEARCH_CANDIDATE",
        "GEN2_RESEARCH_CHALLENGER",
        "GEN2_REJECTED",
    )
    assert require_gen2_research_decision("GEN2_RESEARCH_CHALLENGER") is (
        Gen2ResearchDecision.GEN2_RESEARCH_CHALLENGER
    )
    with pytest.raises(ValueError, match="Gate 3C decisions are limited"):
        require_gen2_research_decision("CHAMPION")


def test_policy_identity_freezes_eligibility_exclusions_corrections_and_postponements() -> None:
    plan = _plan()
    assert plan.policy_version == PROSPECTIVE_POLICY_VERSION
    assert plan.policy_identity_checksum == PROSPECTIVE_POLICY_IDENTITY_CHECKSUM
    assert len(plan.policy_identity_checksum) == 64
    assert "GOVERNED_OFFICIAL_FINAL_REQUIRED_AT_CLAIM" in ELIGIBILITY_POLICY.rules
    assert "LATE_OR_POST_START_SCORE" in EXCLUSION_POLICY.rules
    assert "CLAIM_BINDS_LATEST_ELIGIBLE_REVISION_AVAILABLE_AT_CLAIM_TIME" in (
        CORRECTION_POLICY.rules
    )
    assert "CANONICAL_RESCHEDULED_OCCURRENCE_MAY_EARN_NEW_ELIGIBILITY" in (
        POSTPONEMENT_POLICY.rules
    )


def test_plan_identity_is_deterministic_immutable_and_runtime_drift_fails_closed() -> None:
    runtime = _runtime()
    first = _plan(runtime)
    second = _plan(runtime)
    assert first.plan_identity_checksum == second.plan_identity_checksum
    assert len(first.plan_identity_checksum) == 64
    with pytest.raises(FrozenInstanceError):
        first.target_season = 2025  # type: ignore[misc]
    first.require_current_runtime(runtime)
    with pytest.raises(ProspectivePlanDriftError, match="drift invalidated"):
        first.require_current_runtime(replace(runtime, code_checksum=_sha("changed-code")))
    with pytest.raises(ProspectivePlanDriftError, match="drift invalidated"):
        first.require_current_runtime(
            replace(runtime, schema_bundle_checksum=_sha("changed-schema"))
        )


def test_eligibility_keeps_research_track_and_timing_risks_explicit() -> None:
    plan = _plan()
    valid = assess_forecast_eligibility(plan, (_forecast(plan),))
    assert valid.eligible and valid.binding is not None

    wrong_track = assess_forecast_eligibility(
        plan,
        (_forecast(plan, forecast_track=CURRENT_DEVELOPMENT_FORECAST_TRACK),),
    )
    assert wrong_track.exclusion_reasons == (
        ProspectiveExclusionReason.WRONG_FORECAST_TRACK,
    )
    bad = assess_forecast_eligibility(
        plan,
        (
            _forecast(
                plan,
                evidence_mode="DEMO",
                lineage_complete=False,
                generated_at=BASE + timedelta(hours=4),
                postponed_before_start=True,
            ),
        ),
    )
    assert set(bad.exclusion_reasons) >= {
        ProspectiveExclusionReason.NON_LIVE_EVIDENCE,
        ProspectiveExclusionReason.INCOMPLETE_LINEAGE,
        ProspectiveExclusionReason.LATE_OR_POST_START_SCORE,
        ProspectiveExclusionReason.PRESTART_POSTPONED_OLD_OCCURRENCE,
    }


def test_rescheduled_occurrence_can_be_eligible_but_replacement_after_suspension_cannot() -> None:
    plan = _plan()
    rescheduled = assess_forecast_eligibility(
        plan,
        (
            _forecast(
                plan,
                occurrence_id="rescheduled-occurrence",
                rescheduled_from_occurrence_id="old-occurrence",
            ),
        ),
    )
    assert rescheduled.eligible
    replacement = assess_forecast_eligibility(
        plan,
        (
            _forecast(
                plan,
                suspended_original_occurrence=True,
                replacement_after_suspension=True,
            ),
        ),
    )
    assert replacement.exclusion_reasons == (
        ProspectiveExclusionReason.POST_SUSPENSION_REPLACEMENT,
    )


def test_claim_threshold_is_fail_closed_and_failed_claim_does_not_consume_key() -> None:
    runtime = _runtime()
    plan = _plan(runtime)
    ledger = ProspectiveGovernanceLedger()
    ledger.register_plan(plan, runtime=runtime)
    games = _games(plan, 299)
    with pytest.raises(ProspectiveClaimError, match="requires at least 300"):
        ledger.claim_checkpoint(
            plan_identity_checksum=plan.plan_identity_checksum,
            checkpoint=ProspectiveCheckpoint.INTERIM_300,
            eligible_games=games,
            target_revisions=_revisions(games),
            runtime=runtime,
            claimed_at=BASE + timedelta(hours=6),
        )
    assert ledger.claims == ()


def test_claim_revalidates_carried_eligibility_proof_instead_of_trusting_binding() -> None:
    runtime = _runtime()
    plan = _plan(runtime)
    ledger = ProspectiveGovernanceLedger()
    ledger.register_plan(plan, runtime=runtime)
    games = list(_games(plan, 300))
    original = games[0]
    forged = replace(original)
    object.__setattr__(
        forged,
        "assessed_forecast",
        replace(original.assessed_forecast, evidence_mode="DEMO"),
    )
    games[0] = forged
    with pytest.raises(ProspectiveClaimError, match="verified eligibility proof"):
        ledger.claim_checkpoint(
            plan_identity_checksum=plan.plan_identity_checksum,
            checkpoint=ProspectiveCheckpoint.INTERIM_300,
            eligible_games=games,
            target_revisions=_revisions(tuple(games)),
            runtime=runtime,
            claimed_at=BASE + timedelta(hours=6),
        )
    assert ledger.claims == ()


def test_claim_binds_latest_available_correction_and_is_globally_single_use() -> None:
    runtime = _runtime()
    plan = _plan(runtime)
    ledger = ProspectiveGovernanceLedger()
    ledger.register_plan(plan, runtime=runtime)
    games = _games(plan, 300)
    revisions = list(_revisions(games))
    first = games[0]
    revisions.extend(
        (
            TargetRevisionReference(
                game_id=first.game_id,
                occurrence_id=first.occurrence_id,
                revision_id="correction-1",
                revision_checksum=_sha("correction-1"),
                revision_sequence=1,
                available_at=BASE + timedelta(hours=5, minutes=30),
            ),
            TargetRevisionReference(
                game_id=first.game_id,
                occurrence_id=first.occurrence_id,
                revision_id="future-correction-2",
                revision_checksum=_sha("future-correction-2"),
                revision_sequence=2,
                available_at=BASE + timedelta(hours=7),
            ),
        )
    )
    claim = ledger.claim_checkpoint(
        plan_identity_checksum=plan.plan_identity_checksum,
        checkpoint=ProspectiveCheckpoint.INTERIM_300,
        eligible_games=tuple(reversed(games)),
        target_revisions=revisions,
        runtime=runtime,
        claimed_at=BASE + timedelta(hours=6),
    )
    assert len(claim.bindings) == 300
    assert claim.bindings[0].target_revision.revision_id == "correction-1"
    assert len(claim.global_claim_identity_checksum) == 64
    assert len(claim.eligible_set_checksum) == 64
    assert len(claim.claim_identity_checksum) == 64
    with pytest.raises(ProspectiveClaimError, match="consumed global claim"):
        ledger.claim_checkpoint(
            plan_identity_checksum=plan.plan_identity_checksum,
            checkpoint=ProspectiveCheckpoint.INTERIM_300,
            eligible_games=games,
            target_revisions=revisions,
            runtime=runtime,
            claimed_at=BASE + timedelta(hours=6),
        )


def test_label_loader_is_never_called_before_exact_claim_or_after_runtime_drift() -> None:
    runtime = _runtime()
    plan = _plan(runtime)
    games = _games(plan, 300)
    revisions = _revisions(games)
    foreign = ProspectiveGovernanceLedger()
    foreign.register_plan(plan, runtime=runtime)
    foreign_claim = foreign.claim_checkpoint(
        plan_identity_checksum=plan.plan_identity_checksum,
        checkpoint=ProspectiveCheckpoint.INTERIM_300,
        eligible_games=games,
        target_revisions=revisions,
        runtime=runtime,
        claimed_at=BASE + timedelta(hours=6),
    )
    ledger = ProspectiveGovernanceLedger()
    ledger.register_plan(plan, runtime=runtime)
    loader_calls = 0

    def loader(references):
        nonlocal loader_calls
        loader_calls += 1
        assert all(not hasattr(item, "home_runs") for item in references)
        return _labels_for(references)

    with pytest.raises(ProspectiveLabelAccessError, match="exact stored"):
        ledger.read_claimed_labels(foreign_claim, runtime=runtime, loader=loader)
    assert loader_calls == 0

    claim = ledger.claim_checkpoint(
        plan_identity_checksum=plan.plan_identity_checksum,
        checkpoint=ProspectiveCheckpoint.INTERIM_300,
        eligible_games=games,
        target_revisions=revisions,
        runtime=runtime,
        claimed_at=BASE + timedelta(hours=6),
    )
    drifted = replace(runtime, schema_bundle_checksum=_sha("schema-drift"))
    with pytest.raises(ProspectiveLabelAccessError, match="drift blocks label access"):
        ledger.read_claimed_labels(claim, runtime=drifted, loader=loader)
    assert loader_calls == 0
    labels = ledger.read_claimed_labels(claim, runtime=runtime, loader=loader)
    assert len(labels) == 300
    assert loader_calls == 1
    assert len(ledger.label_accesses) == 1
    with pytest.raises(ProspectiveLabelAccessError, match="single-use"):
        ledger.read_claimed_labels(claim, runtime=runtime, loader=loader)
    assert loader_calls == 1


@pytest.mark.parametrize("mutation", ("reorder", "substitution"))
def test_label_loader_rejects_reordered_or_substituted_revision_bindings(
    mutation: str,
) -> None:
    runtime = _runtime()
    plan = _plan(runtime)
    ledger = ProspectiveGovernanceLedger()
    ledger.register_plan(plan, runtime=runtime)
    games = _games(plan, 300)
    claim = ledger.claim_checkpoint(
        plan_identity_checksum=plan.plan_identity_checksum,
        checkpoint=ProspectiveCheckpoint.INTERIM_300,
        eligible_games=games,
        target_revisions=_revisions(games),
        runtime=runtime,
        claimed_at=BASE + timedelta(hours=6),
    )

    def mismatched_loader(references):
        labels = list(_labels_for(references))
        if mutation == "reorder":
            labels.reverse()
        else:
            labels[0] = labels[1]
        return labels

    with pytest.raises(ProspectiveLabelAccessError, match="does not match its claimed"):
        ledger.read_claimed_labels(
            claim,
            runtime=runtime,
            loader=mismatched_loader,
            consumed_at=BASE + timedelta(hours=6, minutes=1),
        )
    assert len(ledger.label_accesses) == 1
    with pytest.raises(ProspectiveLabelAccessError, match="single-use"):
        ledger.read_claimed_labels(
            claim,
            runtime=runtime,
            loader=_labels_for,
            consumed_at=BASE + timedelta(hours=6, minutes=2),
        )


def test_label_loader_rejects_payload_not_bound_to_revision_commitment() -> None:
    runtime = _runtime()
    plan = _plan(runtime)
    ledger = ProspectiveGovernanceLedger()
    ledger.register_plan(plan, runtime=runtime)
    games = _games(plan, 300)
    claim = ledger.claim_checkpoint(
        plan_identity_checksum=plan.plan_identity_checksum,
        checkpoint=ProspectiveCheckpoint.INTERIM_300,
        eligible_games=games,
        target_revisions=_revisions(games),
        runtime=runtime,
        claimed_at=BASE + timedelta(hours=6),
    )

    def tampered_loader(references):
        labels = list(_labels_for(references))
        labels[0] = replace(
            labels[0],
            payload={"home_runs": 3, "away_runs": 4},
        )
        return labels

    with pytest.raises(ProspectiveLabelAccessError, match="payload does not match"):
        ledger.read_claimed_labels(
            claim,
            runtime=runtime,
            loader=tampered_loader,
            consumed_at=BASE + timedelta(hours=6, minutes=1),
        )
    assert len(ledger.label_accesses) == 1
    with pytest.raises(ProspectiveLabelAccessError, match="single-use"):
        ledger.read_claimed_labels(
            claim,
            runtime=runtime,
            loader=_labels_for,
            consumed_at=BASE + timedelta(hours=6, minutes=2),
        )


def test_final_claim_is_separate_500_game_key_and_cross_plan_reuse_is_forbidden() -> None:
    runtime = _runtime()
    plan = _plan(runtime)
    ledger = ProspectiveGovernanceLedger()
    ledger.register_plan(plan, runtime=runtime)
    games = _games(plan, 500)
    revisions = _revisions(games)
    interim = ledger.claim_checkpoint(
        plan_identity_checksum=plan.plan_identity_checksum,
        checkpoint=ProspectiveCheckpoint.INTERIM_300,
        eligible_games=games[:300],
        target_revisions=revisions,
        runtime=runtime,
        claimed_at=BASE + timedelta(hours=6),
    )
    final = ledger.claim_checkpoint(
        plan_identity_checksum=plan.plan_identity_checksum,
        checkpoint=ProspectiveCheckpoint.FINAL_500,
        eligible_games=games,
        target_revisions=revisions,
        runtime=runtime,
        claimed_at=BASE + timedelta(hours=6),
    )
    assert interim.global_claim_identity_checksum != final.global_claim_identity_checksum
    assert len(final.bindings) == 500

    second_runtime = _runtime("v2")
    second_plan = _plan(second_runtime, plan_id="plan-2026-v2")
    ledger.register_plan(second_plan, runtime=second_runtime)
    reused = _games(second_plan, 300)
    with pytest.raises(ProspectiveClaimError, match="another model/window"):
        ledger.claim_checkpoint(
            plan_identity_checksum=second_plan.plan_identity_checksum,
            checkpoint=ProspectiveCheckpoint.INTERIM_300,
            eligible_games=reused,
            target_revisions=revisions,
            runtime=second_runtime,
            claimed_at=BASE + timedelta(hours=6),
        )


def test_append_only_plan_id_and_research_only_decision_contract() -> None:
    runtime = _runtime()
    plan = _plan(runtime)
    ledger = ProspectiveGovernanceLedger()
    assert ledger.register_plan(plan, runtime=runtime) is plan
    assert ledger.register_plan(plan, runtime=runtime) is plan
    replacement = replace(plan, evaluation_starts_at=BASE + timedelta(hours=2))
    with pytest.raises(ProspectiveGovernanceError, match="cannot be replaced"):
        ledger.register_plan(replacement, runtime=runtime)

    record = ProspectiveDecisionRecord(
        plan_identity_checksum=plan.plan_identity_checksum,
        claim_identity_checksum=_sha("claim"),
        checkpoint=ProspectiveCheckpoint.INTERIM_300,
        decision=Gen2ResearchDecision.GEN2_RESEARCH_CHALLENGER,
        completed_eligible_games=300,
        decided_at=BASE + timedelta(days=90),
    )
    assert len(record.decision_identity_checksum) == 64
    with pytest.raises(ValueError, match="cannot enable positions"):
        replace(record, official_positions_enabled=True)
    with pytest.raises(ValueError, match="Gate 3C research decision"):
        replace(record, decision="CHAMPION")  # type: ignore[arg-type]


def _plan_record(plan: ProspectiveEvaluationPlan) -> ProspectiveEvaluationPlanRecord:
    runtime = plan.runtime_identity
    return ProspectiveEvaluationPlanRecord(
        plan_id=plan.plan_id,
        schema_version=plan.schema_version,
        policy_version=plan.policy_version,
        policy_identity_checksum=plan.policy_identity_checksum,
        research_policy_version=plan.research_policy_version,
        created_at=plan.created_at,
        evaluation_starts_at=plan.evaluation_starts_at,
        target_season=plan.target_season,
        lifecycle=plan.lifecycle,
        forecast_lifecycle=plan.forecast_lifecycle.value,
        forecast_track=plan.forecast_track,
        forecast_family=plan.forecast_family,
        registration_status=plan.registration_status,
        interim_completed_games=plan.interim_completed_games,
        final_completed_games=plan.final_completed_games,
        metrics=list(plan.metrics),
        baselines=list(plan.baselines),
        policy_manifest=plan.semantic_payload()["rules"],
        code_revision=runtime.code_revision,
        code_checksum=runtime.code_checksum,
        schema_bundle_version=runtime.schema_bundle_version,
        schema_bundle_checksum=runtime.schema_bundle_checksum,
        component_identities=[asdict(item) for item in runtime.components],
        plan_identity_checksum=plan.plan_identity_checksum,
    )


def _claim_record(
    plan_record: ProspectiveEvaluationPlanRecord,
    claim,
) -> ProspectiveEvaluationClaimRecord:
    return ProspectiveEvaluationClaimRecord(
        evaluation_plan_id=plan_record.id,
        plan_identity_checksum=claim.plan_identity_checksum,
        schema_version=claim.schema_version,
        status=claim.status,
        checkpoint=claim.checkpoint.value,
        minimum_completed_games=claim.checkpoint.minimum_completed_games,
        eligible_game_count=len(claim.bindings),
        claimed_at=claim.claimed_at,
        eligible_set_checksum=claim.eligible_set_checksum,
        code_revision=claim.code_revision,
        code_checksum=claim.code_checksum,
        schema_bundle_version=claim.schema_bundle_version,
        schema_bundle_checksum=claim.schema_bundle_checksum,
        global_claim_identity_checksum=claim.global_claim_identity_checksum,
        claim_identity_checksum=claim.claim_identity_checksum,
    )


def _assignment_records(plan_record, claim):
    return [
        ProspectiveEvaluationGameAssignmentRecord(
            evaluation_plan_id=plan_record.id,
            plan_identity_checksum=claim.plan_identity_checksum,
            game_id=item.game.game_id,
            occurrence_id=item.game.occurrence_id,
            assigned_at=claim.claimed_at,
            assignment_identity_checksum=prospective_game_assignment_checksum(
                plan_identity_checksum=claim.plan_identity_checksum,
                game_id=item.game.game_id,
                occurrence_id=item.game.occurrence_id,
            ),
        )
        for item in claim.bindings
    ]


def _binding_records(claim_record, claim, assignments):
    assignment_by_key = {
        (item.game_id, item.occurrence_id): item for item in assignments
    }
    return [
        ProspectiveEvaluationClaimBindingRecord(
            evaluation_claim_id=claim_record.id,
            evaluation_plan_id=claim_record.evaluation_plan_id,
            plan_identity_checksum=claim.plan_identity_checksum,
            checkpoint=claim.checkpoint.value,
            game_assignment_id=assignment_by_key[
                (item.game.game_id, item.game.occurrence_id)
            ].id,
            binding_ordinal=index,
            game_id=item.game.game_id,
            occurrence_id=item.game.occurrence_id,
            forecast_id=item.game.forecast_id,
            forecast_identity_checksum=item.game.forecast_identity_checksum,
            forecast_evidence=item.game.assessed_forecast.to_record(),
            eligibility_policy_checksum=item.game.eligibility_policy_checksum,
            eligibility_proof_checksum=item.game.eligibility_proof_checksum,
            target_revision_id=item.target_revision.revision_id,
            target_revision_checksum=item.target_revision.revision_checksum,
            target_revision_sequence=item.target_revision.revision_sequence,
            target_available_at=item.target_revision.available_at,
            target_status="OFFICIAL_FINAL",
            schema_version=item.schema_version,
            binding_identity_checksum=item.binding_identity_checksum,
        )
        for index, item in enumerate(claim.bindings, start=1)
    ]


def test_linear_0011_upgrade_matches_models_and_round_trips(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{(artifact_root / 'gate3c-prospective.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("THE_ODDS_API_KEY", "")
    monkeypatch.setenv("ODDS_POLLING_ENABLED", "false")
    monkeypatch.setenv("OPEN_METEO_ENABLED", "false")
    monkeypatch.setenv("STATCAST_ENABLED", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    config = _alembic_config(database_url)

    command.upgrade(config, "20260814_0010")
    engine = create_engine(database_url)
    prospective_tables = {
        "prospective_evaluation_plans",
        "prospective_evaluation_claims",
        "prospective_evaluation_game_assignments",
        "prospective_evaluation_claim_bindings",
        "prospective_evaluation_label_accesses",
    }
    assert prospective_tables.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, "20260814_0011")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert prospective_tables <= set(schema.get_table_names())
    assert {
        "ix_prospective_plan_window",
        "ix_prospective_plan_registration",
    } == {item["name"] for item in schema.get_indexes("prospective_evaluation_plans")}
    assert {
        "ix_prospective_claim_plan_time",
        "ix_prospective_claim_checkpoint",
    } == {item["name"] for item in schema.get_indexes("prospective_evaluation_claims")}
    assert {"ix_prospective_assignment_plan"} == {
        item["name"]
        for item in schema.get_indexes("prospective_evaluation_game_assignments")
    }
    assert {"ix_prospective_binding_claim", "ix_prospective_binding_game"} == {
        item["name"]
        for item in schema.get_indexes("prospective_evaluation_claim_bindings")
    }
    assert {"ix_prospective_label_access_time"} == {
        item["name"]
        for item in schema.get_indexes("prospective_evaluation_label_accesses")
    }
    assert {row["referred_table"] for row in schema.get_foreign_keys(
        "prospective_evaluation_claims"
    )} == {"prospective_evaluation_plans"}
    plan_checks = {
        item["name"] for item in schema.get_check_constraints("prospective_evaluation_plans")
    }
    claim_checks = {
        item["name"] for item in schema.get_check_constraints("prospective_evaluation_claims")
    }
    assert {
        "ck_prospective_plan_versions",
        "ck_prospective_plan_season",
        "ck_prospective_plan_lifecycle",
        "ck_prospective_plan_research_track",
        "ck_prospective_plan_thresholds",
        "ck_prospective_plan_window",
        "ck_prospective_plan_checksums",
    } <= plan_checks
    assert {
        "ck_prospective_claim_version_status",
        "ck_prospective_claim_checkpoint",
        "ck_prospective_claim_threshold",
        "ck_prospective_claim_checksums",
    } <= claim_checks
    assignment_uniques = {
        item["name"]
        for item in schema.get_unique_constraints(
            "prospective_evaluation_game_assignments"
        )
    }
    binding_uniques = {
        item["name"]
        for item in schema.get_unique_constraints(
            "prospective_evaluation_claim_bindings"
        )
    }
    access_uniques = {
        item["name"]
        for item in schema.get_unique_constraints(
            "prospective_evaluation_label_accesses"
        )
    }
    assert "uq_prospective_assignment_global_occurrence" in assignment_uniques
    assert {
        "uq_prospective_binding_claim_game",
        "uq_prospective_binding_claim_ordinal",
        "uq_prospective_binding_claim_forecast",
    } <= binding_uniques
    assert "uq_prospective_label_access_claim" in access_uniques
    with engine.connect() as connection:
        trigger_names = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
    assert "trg_prospective_label_access_complete_claim" in trigger_names

    with Session(engine) as db:
        plan = _plan()
        plan_record = _plan_record(plan)
        db.add(plan_record)
        db.flush()
        claim_record = ProspectiveEvaluationClaimRecord(
            evaluation_plan_id=plan_record.id,
            plan_identity_checksum=plan.plan_identity_checksum,
            schema_version="mlb_gen2_prospective_evaluation_claim_v1",
            status="PROSPECTIVE_LABEL_ACCESS_AUTHORIZED",
            checkpoint=ProspectiveCheckpoint.INTERIM_300.value,
            minimum_completed_games=300,
            eligible_game_count=300,
            claimed_at=BASE + timedelta(hours=6),
            eligible_set_checksum=_sha("migration-eligible-set"),
            code_revision=plan.runtime_identity.code_revision,
            code_checksum=plan.runtime_identity.code_checksum,
            schema_bundle_version=plan.runtime_identity.schema_bundle_version,
            schema_bundle_checksum=plan.runtime_identity.schema_bundle_checksum,
            global_claim_identity_checksum=_sha("migration-global-claim"),
            claim_identity_checksum=_sha("migration-claim"),
        )
        db.add(claim_record)
        db.commit()
        db.add(
            ProspectiveEvaluationLabelAccessRecord(
                evaluation_claim_id=claim_record.id,
                plan_identity_checksum=claim_record.plan_identity_checksum,
                global_claim_identity_checksum=claim_record.global_claim_identity_checksum,
                claim_identity_checksum=claim_record.claim_identity_checksum,
                consumed_at=BASE + timedelta(hours=6, minutes=1),
                code_revision=plan.runtime_identity.code_revision,
                code_checksum=plan.runtime_identity.code_checksum,
                schema_bundle_version=plan.runtime_identity.schema_bundle_version,
                schema_bundle_checksum=plan.runtime_identity.schema_bundle_checksum,
                schema_version="mlb_gen2_prospective_label_access_v1",
                status="PROSPECTIVE_LABEL_ACCESS_CONSUMED",
                access_identity_checksum=_sha("migration-incomplete-access"),
            )
        )
        with pytest.raises(IntegrityError, match="binding set is incomplete"):
            db.commit()
        db.rollback()
    engine.dispose()

    # 0011 remains the prospective schema boundary; advance through the
    # independent 0012 forecast-track migration before checking head drift.
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "20260814_0010")
    engine = create_engine(database_url)
    assert prospective_tables.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()


def test_orm_plan_and_claim_are_append_only_with_db_single_use_constraints(session) -> None:
    runtime = _runtime()
    plan = _plan(runtime)
    plan_record = _plan_record(plan)
    session.add(plan_record)
    session.commit()
    assert {
        ProspectiveEvaluationPlanRecord,
        ProspectiveEvaluationClaimRecord,
        ProspectiveEvaluationGameAssignmentRecord,
        ProspectiveEvaluationClaimBindingRecord,
        ProspectiveEvaluationLabelAccessRecord,
    } <= set(_APPEND_ONLY_TYPES)

    ledger = ProspectiveGovernanceLedger()
    ledger.register_plan(plan, runtime=runtime)
    games = _games(plan, 300)
    claim = ledger.claim_checkpoint(
        plan_identity_checksum=plan.plan_identity_checksum,
        checkpoint=ProspectiveCheckpoint.INTERIM_300,
        eligible_games=games,
        target_revisions=_revisions(games),
        runtime=runtime,
        claimed_at=BASE + timedelta(hours=6),
    )
    claim_record = _claim_record(plan_record, claim)
    session.add(claim_record)
    session.commit()
    assert claim_record.eligible_game_count == 300
    assignments = _assignment_records(plan_record, claim)
    session.add_all(assignments)
    session.flush()
    binding_records = _binding_records(claim_record, claim, assignments)
    session.add_all(binding_records)
    session.commit()
    assert session.query(ProspectiveEvaluationGameAssignmentRecord).count() == 300
    assert session.query(ProspectiveEvaluationClaimBindingRecord).filter_by(
        evaluation_claim_id=claim_record.id
    ).count() == claim_record.eligible_game_count

    duplicate_binding = _binding_records(claim_record, claim, assignments)[0]
    session.add(duplicate_binding)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    duplicate = _claim_record(plan_record, claim)
    duplicate.global_claim_identity_checksum = _sha("another-global-identity")
    duplicate.claim_identity_checksum = _sha("another-claim-identity")
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    second_runtime = _runtime("db-v2")
    second_plan = _plan(second_runtime, plan_id="db-plan-v2")
    second_plan_record = _plan_record(second_plan)
    session.add(second_plan_record)
    session.commit()
    duplicate_assignment = ProspectiveEvaluationGameAssignmentRecord(
        evaluation_plan_id=second_plan_record.id,
        plan_identity_checksum=second_plan.plan_identity_checksum,
        game_id=assignments[0].game_id,
        occurrence_id=assignments[0].occurrence_id,
        assigned_at=BASE + timedelta(hours=6),
        assignment_identity_checksum=_sha("cross-plan-duplicate-assignment"),
    )
    session.add(duplicate_assignment)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    loader_calls = 0

    def persisted_loader(references):
        nonlocal loader_calls
        loader_calls += 1
        return _labels_for(references)

    with pytest.raises(ProspectivePersistenceError, match="code/schema/component drift"):
        SqlAlchemyProspectiveLabelAccessGate(session).consume_and_read(
            claim_identity_checksum=claim.claim_identity_checksum,
            runtime=replace(runtime, code_checksum=_sha("persisted-code-drift")),
            loader=persisted_loader,
            consumed_at=BASE + timedelta(hours=6, minutes=1),
        )
    assert loader_calls == 0
    labels = SqlAlchemyProspectiveLabelAccessGate(session).consume_and_read(
        claim_identity_checksum=claim.claim_identity_checksum,
        runtime=runtime,
        loader=persisted_loader,
        consumed_at=BASE + timedelta(hours=6, minutes=1),
    )
    assert len(labels) == 300
    assert loader_calls == 1
    assert session.query(ProspectiveEvaluationLabelAccessRecord).count() == 1
    with Session(session.get_bind()) as restarted:
        with pytest.raises(ProspectiveLabelAccessError, match="already consumed"):
            SqlAlchemyProspectiveLabelAccessGate(restarted).consume_and_read(
                claim_identity_checksum=claim.claim_identity_checksum,
                runtime=runtime,
                loader=persisted_loader,
                consumed_at=BASE + timedelta(hours=6, minutes=2),
            )
    assert loader_calls == 1

    persisted_claim = session.get(ProspectiveEvaluationClaimRecord, claim_record.id)
    assert persisted_claim is not None
    persisted_claim.eligible_game_count = 301
    with pytest.raises(
        ImmutableRecordError,
        match="ProspectiveEvaluationClaim records are append-only",
    ):
        session.commit()
    session.rollback()

    persisted_plan = session.get(ProspectiveEvaluationPlanRecord, plan_record.id)
    assert persisted_plan is not None
    session.delete(persisted_plan)
    with pytest.raises(
        ImmutableRecordError,
        match="ProspectiveEvaluationPlan records are append-only",
    ):
        session.commit()
    session.rollback()


def test_database_rejects_wrong_claim_threshold_and_non_research_plan(session) -> None:
    plan = _plan()
    invalid_plan = _plan_record(plan)
    invalid_plan.registration_status = "CHAMPION"
    session.add(invalid_plan)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    plan_record = _plan_record(plan)
    session.add(plan_record)
    session.commit()
    invalid_claim = ProspectiveEvaluationClaimRecord(
        evaluation_plan_id=plan_record.id,
        plan_identity_checksum=plan.plan_identity_checksum,
        schema_version="mlb_gen2_prospective_evaluation_claim_v1",
        status="PROSPECTIVE_LABEL_ACCESS_AUTHORIZED",
        checkpoint=ProspectiveCheckpoint.INTERIM_300.value,
        minimum_completed_games=299,
        eligible_game_count=299,
        claimed_at=BASE + timedelta(hours=6),
        eligible_set_checksum=_sha("eligible"),
        code_revision=plan.runtime_identity.code_revision,
        code_checksum=plan.runtime_identity.code_checksum,
        schema_bundle_version=plan.runtime_identity.schema_bundle_version,
        schema_bundle_checksum=plan.runtime_identity.schema_bundle_checksum,
        global_claim_identity_checksum=_sha("global"),
        claim_identity_checksum=_sha("claim"),
    )
    session.add(invalid_claim)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
