"""SQLAlchemy-backed, restart-safe prospective claim and label-access gate."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.gen2.prospective import (
    PROSPECTIVE_CLAIM_BINDING_SCHEMA_VERSION,
    PROSPECTIVE_CLAIM_SCHEMA_VERSION,
    ClaimedTargetLabel,
    ELIGIBILITY_POLICY,
    ProspectiveCheckpoint,
    ProspectiveEvaluationPlan as FrozenProspectiveEvaluationPlan,
    ProspectiveForecastSnapshot,
    ProspectiveLabelAccessConsumption,
    ProspectiveLabelAccessError,
    RuntimeIdentitySnapshot,
    TargetRevisionReference,
    assess_forecast_eligibility,
    canonical_checksum,
    prospective_game_assignment_checksum,
    validate_claimed_target_labels,
)
from app.models import (
    ProspectiveEvaluationClaim,
    ProspectiveEvaluationClaimBinding,
    ProspectiveEvaluationGameAssignment,
    ProspectiveEvaluationLabelAccess,
    ProspectiveEvaluationPlan,
)


class ProspectivePersistenceError(RuntimeError):
    """Raised when persisted prospective semantics fail closed."""


def _db_utc(value: datetime) -> datetime:
    """Treat SQLite's timezone-naive round trip as the stored UTC instant."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqlAlchemyProspectiveLabelAccessGate:
    """Validate a persisted claim and durably consume one target read.

    The supplied session should be dedicated to this operation.  The access row
    is committed before ``loader`` is invoked.  A loader exception therefore
    leaves the claim consumed, because target values may already have been seen.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def consume_and_read(
        self,
        *,
        claim_identity_checksum: str,
        runtime: RuntimeIdentitySnapshot,
        loader: Callable[
            [tuple[TargetRevisionReference, ...]], Sequence[ClaimedTargetLabel]
        ],
        consumed_at: datetime | None = None,
    ) -> tuple[ClaimedTargetLabel, ...]:
        claim, references = self._validated_claim(claim_identity_checksum, runtime)
        consumption = ProspectiveLabelAccessConsumption(
            plan_identity_checksum=claim.plan_identity_checksum,
            global_claim_identity_checksum=claim.global_claim_identity_checksum,
            claim_identity_checksum=claim.claim_identity_checksum,
            consumed_at=consumed_at or datetime.now(UTC),
            code_revision=runtime.code_revision,
            code_checksum=runtime.code_checksum,
            schema_bundle_version=runtime.schema_bundle_version,
            schema_bundle_checksum=runtime.schema_bundle_checksum,
        )
        self.session.add(
            ProspectiveEvaluationLabelAccess(
                evaluation_claim_id=claim.id,
                plan_identity_checksum=consumption.plan_identity_checksum,
                global_claim_identity_checksum=consumption.global_claim_identity_checksum,
                claim_identity_checksum=consumption.claim_identity_checksum,
                consumed_at=consumption.consumed_at,
                code_revision=consumption.code_revision,
                code_checksum=consumption.code_checksum,
                schema_bundle_version=consumption.schema_bundle_version,
                schema_bundle_checksum=consumption.schema_bundle_checksum,
                schema_version=consumption.schema_version,
                status=consumption.status,
                access_identity_checksum=consumption.access_identity_checksum,
            )
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ProspectiveLabelAccessError(
                "Persisted prospective label access is invalid or already consumed"
            ) from exc
        return validate_claimed_target_labels(references, loader(references))

    def _validated_claim(
        self,
        claim_identity_checksum: str,
        runtime: RuntimeIdentitySnapshot,
    ) -> tuple[ProspectiveEvaluationClaim, tuple[TargetRevisionReference, ...]]:
        claim = self.session.scalar(
            select(ProspectiveEvaluationClaim).where(
                ProspectiveEvaluationClaim.claim_identity_checksum
                == claim_identity_checksum
            )
        )
        if claim is None:
            raise ProspectivePersistenceError("Prospective claim does not exist")
        plan = self.session.get(ProspectiveEvaluationPlan, claim.evaluation_plan_id)
        if plan is None or plan.plan_identity_checksum != claim.plan_identity_checksum:
            raise ProspectivePersistenceError("Prospective claim plan identity is invalid")
        expected_components = [asdict(item) for item in runtime.components]
        runtime_values = (
            runtime.code_revision,
            runtime.code_checksum,
            runtime.schema_bundle_version,
            runtime.schema_bundle_checksum,
        )
        if runtime_values != (
            claim.code_revision,
            claim.code_checksum,
            claim.schema_bundle_version,
            claim.schema_bundle_checksum,
        ) or runtime_values != (
            plan.code_revision,
            plan.code_checksum,
            plan.schema_bundle_version,
            plan.schema_bundle_checksum,
        ) or plan.component_identities != expected_components:
            raise ProspectivePersistenceError(
                "Persisted prospective plan is invalid under code/schema/component drift"
            )
        frozen_plan = FrozenProspectiveEvaluationPlan(
            plan_id=plan.plan_id,
            created_at=_db_utc(plan.created_at),
            evaluation_starts_at=_db_utc(plan.evaluation_starts_at),
            runtime_identity=runtime,
        )
        if (
            plan.plan_identity_checksum != frozen_plan.plan_identity_checksum
            or plan.schema_version != frozen_plan.schema_version
            or plan.policy_version != frozen_plan.policy_version
            or plan.policy_identity_checksum != frozen_plan.policy_identity_checksum
            or plan.research_policy_version != frozen_plan.research_policy_version
            or plan.target_season != frozen_plan.target_season
            or plan.lifecycle != frozen_plan.lifecycle
            or plan.forecast_lifecycle != frozen_plan.forecast_lifecycle.value
            or plan.forecast_track != frozen_plan.forecast_track
            or plan.forecast_family != frozen_plan.forecast_family
            or plan.registration_status != frozen_plan.registration_status
            or plan.interim_completed_games != frozen_plan.interim_completed_games
            or plan.final_completed_games != frozen_plan.final_completed_games
            or tuple(plan.metrics) != frozen_plan.metrics
            or tuple(plan.baselines) != frozen_plan.baselines
            or canonical_checksum(plan.policy_manifest)
            != canonical_checksum(frozen_plan.semantic_payload()["rules"])
        ):
            raise ProspectivePersistenceError(
                "Persisted prospective plan identity or frozen policy is invalid"
            )
        try:
            checkpoint = ProspectiveCheckpoint(claim.checkpoint)
        except ValueError as exc:
            raise ProspectivePersistenceError(
                "Persisted prospective checkpoint vocabulary is invalid"
            ) from exc
        expected_global_identity = canonical_checksum(
            {
                "schema_version": PROSPECTIVE_CLAIM_SCHEMA_VERSION,
                "plan_identity_checksum": claim.plan_identity_checksum,
                "checkpoint": checkpoint,
            }
        )
        expected_claim_identity = canonical_checksum(
            {
                "schema_version": claim.schema_version,
                "status": claim.status,
                "global_claim_identity_checksum": claim.global_claim_identity_checksum,
                "eligible_set_checksum": claim.eligible_set_checksum,
                "claimed_at": _db_utc(claim.claimed_at),
                "code_revision": claim.code_revision,
                "code_checksum": claim.code_checksum,
                "schema_bundle_version": claim.schema_bundle_version,
                "schema_bundle_checksum": claim.schema_bundle_checksum,
            }
        )
        if (
            claim.schema_version != PROSPECTIVE_CLAIM_SCHEMA_VERSION
            or claim.global_claim_identity_checksum != expected_global_identity
            or claim.claim_identity_checksum != expected_claim_identity
            or claim.minimum_completed_games != checkpoint.minimum_completed_games
            or claim.eligible_game_count < checkpoint.minimum_completed_games
        ):
            raise ProspectivePersistenceError(
                "Persisted prospective claim identity or threshold is invalid"
            )

        bindings = tuple(
            self.session.scalars(
                select(ProspectiveEvaluationClaimBinding)
                .where(
                    ProspectiveEvaluationClaimBinding.evaluation_claim_id == claim.id
                )
                .order_by(ProspectiveEvaluationClaimBinding.binding_ordinal)
            )
        )
        if len(bindings) != claim.eligible_game_count:
            raise ProspectivePersistenceError(
                "Persisted prospective binding count differs from its frozen claim"
            )
        if tuple(item.binding_ordinal for item in bindings) != tuple(
            range(1, claim.eligible_game_count + 1)
        ):
            raise ProspectivePersistenceError(
                "Persisted prospective binding ordinals are not complete"
            )
        assignment_ids = {item.game_assignment_id for item in bindings}
        assignments = {
            item.id: item
            for item in self.session.scalars(
                select(ProspectiveEvaluationGameAssignment).where(
                    ProspectiveEvaluationGameAssignment.id.in_(assignment_ids)
                )
            )
        }
        if len(assignments) != len(bindings):
            raise ProspectivePersistenceError(
                "Persisted prospective claim lacks unique game assignments"
            )

        eligible_payload: list[dict[str, object]] = []
        references: list[TargetRevisionReference] = []
        for binding in bindings:
            assignment = assignments.get(binding.game_assignment_id)
            try:
                assessed_forecast = ProspectiveForecastSnapshot.from_record(
                    binding.forecast_evidence
                )
            except ValueError as exc:
                raise ProspectivePersistenceError(
                    "Persisted prospective forecast evidence is invalid"
                ) from exc
            assessment = assess_forecast_eligibility(
                frozen_plan,
                (assessed_forecast,),
            )
            if (
                assignment is None
                or assignment.evaluation_plan_id != claim.evaluation_plan_id
                or assignment.plan_identity_checksum != claim.plan_identity_checksum
                or assignment.game_id != binding.game_id
                or assignment.occurrence_id != binding.occurrence_id
                or assignment.assignment_identity_checksum
                != prospective_game_assignment_checksum(
                    plan_identity_checksum=assignment.plan_identity_checksum,
                    game_id=assignment.game_id,
                    occurrence_id=assignment.occurrence_id,
                )
                or binding.plan_identity_checksum != claim.plan_identity_checksum
                or binding.checkpoint != claim.checkpoint
                or binding.game_id != assessed_forecast.game_id
                or binding.occurrence_id != assessed_forecast.occurrence_id
                or binding.forecast_id != assessed_forecast.forecast_id
                or binding.forecast_identity_checksum
                != assessed_forecast.forecast_identity_checksum
                or binding.eligibility_policy_checksum
                != ELIGIBILITY_POLICY.identity_checksum
                or not assessment.eligible
                or assessment.binding is None
                or assessment.binding.eligibility_proof_checksum
                != binding.eligibility_proof_checksum
                or binding.schema_version != PROSPECTIVE_CLAIM_BINDING_SCHEMA_VERSION
                or binding.target_status != "OFFICIAL_FINAL"
                or _db_utc(binding.target_available_at) > _db_utc(claim.claimed_at)
            ):
                raise ProspectivePersistenceError(
                    "Persisted prospective assignment/binding semantics are invalid"
                )
            binding_identity = canonical_checksum(
                {
                    "schema_version": binding.schema_version,
                    "plan_identity_checksum": binding.plan_identity_checksum,
                    "game_id": binding.game_id,
                    "occurrence_id": binding.occurrence_id,
                    "forecast_id": binding.forecast_id,
                    "forecast_identity_checksum": binding.forecast_identity_checksum,
                    "eligibility_policy_checksum": binding.eligibility_policy_checksum,
                    "eligibility_proof_checksum": binding.eligibility_proof_checksum,
                    "target_revision_id": binding.target_revision_id,
                    "target_revision_checksum": binding.target_revision_checksum,
                    "target_revision_sequence": binding.target_revision_sequence,
                }
            )
            if binding_identity != binding.binding_identity_checksum:
                raise ProspectivePersistenceError(
                    "Persisted prospective binding identity checksum is invalid"
                )
            eligible_payload.append(
                {
                    "game_id": binding.game_id,
                    "occurrence_id": binding.occurrence_id,
                    "forecast_identity_checksum": binding.forecast_identity_checksum,
                    "eligibility_proof_checksum": binding.eligibility_proof_checksum,
                    "target_revision_id": binding.target_revision_id,
                    "target_revision_checksum": binding.target_revision_checksum,
                    "binding_identity_checksum": binding.binding_identity_checksum,
                }
            )
            references.append(
                TargetRevisionReference(
                    game_id=binding.game_id,
                    occurrence_id=binding.occurrence_id,
                    revision_id=binding.target_revision_id,
                    revision_checksum=binding.target_revision_checksum,
                    revision_sequence=binding.target_revision_sequence,
                    available_at=_db_utc(binding.target_available_at),
                    official_final=True,
                )
            )
        if canonical_checksum(eligible_payload) != claim.eligible_set_checksum:
            raise ProspectivePersistenceError(
                "Persisted prospective eligible-set checksum is invalid"
            )
        return claim, tuple(references)
