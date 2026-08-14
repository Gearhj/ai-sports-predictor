from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.historical.a2_schema import A2_SCHEMA, A2_SCHEMA_HASH, A2_SCHEMA_VERSION
from app.modeling.gen1 import (
    Gen1ResearchPolicy,
    evaluate_gen1_holdout,
    run_gen1_development,
)
from app.models import (
    FeatureVersion,
    ModelEvaluation,
    ModelStatus,
    ModelVersion,
    TrainingDatasetManifest,
)
from app.services.read_service import ReadService
from app.training.gate3b import (
    GEN1_A2_CUTOFF_POLICY,
    GEN1_A2_STRICT_MANIFEST_NAME,
    GEN1_APPROVED_A2_IDENTITY_CHECKSUM,
    Gen1AdmissionError,
    _operator_code_identity,
    _operator_paths,
    feature_version_checksum,
    load_gen1_a2_admission,
    prospective_gen1_feature_version,
    register_terminal_gen1_research_evaluation,
    resume_terminal_gen1_registration,
)
from app.training.gate3b_artifacts import Gen1ArtifactError, Gen1ResearchArtifactStore

# These helpers generate a complete A2-shaped synthetic universe. Importing
# them avoids a second, subtly different test-only interpretation of the 544
# output A2 schema while keeping this suite wholly synthetic and provider-free.
from test_gate3b_gen1 import SOURCE_FEATURE_VERSION_ID, _policy, synthetic_rows


DATASET_IDENTITY = "a" * 64
DATASET_ROWS_CHECKSUM = "b" * 64
CODE_REVISION = "gate3b-content-" + "c" * 64
BASELINE_CODE_REVISION = "d" * 40


def _hex(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def synthetic_case() -> SimpleNamespace:
    rows = synthetic_rows()
    policy = _policy()
    development = run_gen1_development(
        rows,
        policy,
        source_dataset_rows_checksum=DATASET_ROWS_CHECKSUM,
        enable_xgboost=False,
        run_ablations=False,
    )
    holdout_ids = set(development.split.holdout_game_ids)
    evaluation = evaluate_gen1_holdout(
        development,
        tuple(row for row in rows if row.game_id in holdout_ids),
        verified_source_dataset_rows_checksum=DATASET_ROWS_CHECKSUM,
    )
    return SimpleNamespace(
        rows=rows,
        policy=policy,
        development=development,
        evaluation=evaluation,
    )


def _install_source_manifest(
    session: Session,
    *,
    artifact_uri: str,
) -> tuple[FeatureVersion, TrainingDatasetManifest]:
    source_feature = FeatureVersion(
        id=SOURCE_FEATURE_VERSION_ID,
        name="mlb_historical_pregame_a2",
        version="a2-tier-a-strict-v6",
        schema_hash=A2_SCHEMA_HASH,
        definition={
            "stage": "GATE_3A_A2",
            "population": "TIER_A_STRICT",
        },
    )
    session.add(source_feature)
    session.flush()
    manifest = TrainingDatasetManifest(
        name=GEN1_A2_STRICT_MANIFEST_NAME,
        version="mlb-gate3-a2-synthetic-operator",
        feature_version_id=source_feature.id,
        source_mode="LIVE",
        schema_version=A2_SCHEMA_VERSION,
        target="home_win",
        target_definition={"target": "official_home_win"},
        cutoff_policy={"policy_version": GEN1_A2_CUTOFF_POLICY},
        split_policy={"rolling_origin_folds": 5},
        feature_names=list(A2_SCHEMA.output_names),
        seasons=[2025],
        event_start_at=datetime(2025, 4, 1, 17, tzinfo=UTC),
        event_end_at=datetime(2025, 4, 20, 20, tzinfo=UTC),
        row_count=40,
        game_count=40,
        excluded_row_count=0,
        artifact_uri=artifact_uri,
        artifact_checksum=DATASET_ROWS_CHECKSUM,
        source_evidence=[{"kind": "SYNTHETIC_PROVIDER_FREE_TEST"}],
        quality_summary={
            "stage": "GATE_3A_A2",
            "population": "TIER_A_STRICT",
            "schema_hash": A2_SCHEMA_HASH,
            "quality_tiers": ["A"],
            "leakage_checks": {
                "pregame_cutoff": True,
                "target_excluded": True,
            },
        },
        code_revision="synthetic-a2-code",
        identity_checksum=DATASET_IDENTITY,
        built_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    session.add(manifest)
    session.flush()
    return source_feature, manifest


def _freeze_candidate(
    store: Gen1ResearchArtifactStore,
    synthetic_case: SimpleNamespace,
    source_feature: FeatureVersion,
    manifest: TrainingDatasetManifest,
    *,
    code_revision: str = CODE_REVISION,
):
    source_checksum = feature_version_checksum(source_feature)
    admission_identity = SimpleNamespace(
        source_feature_version_id=str(source_feature.id),
        source_feature_version_checksum=source_checksum,
    )
    derived = prospective_gen1_feature_version(
        development=synthetic_case.development,
        admission=admission_identity,
    )
    return store.freeze_candidate(
        synthetic_case.development,
        dataset_id=manifest.version,
        dataset_identity_checksum=manifest.identity_checksum,
        dataset_rows_checksum=manifest.artifact_checksum,
        source_feature_version_id=str(source_feature.id),
        source_feature_version_checksum=source_checksum,
        derived_feature_version_id=str(derived.id),
        derived_feature_version=derived.version,
        derived_feature_version_checksum=feature_version_checksum(derived),
        code_revision=code_revision,
        baseline_code_revision=BASELINE_CODE_REVISION,
        dependency_versions={
            "joblib": "test",
            "numpy": "test",
            "scikit-learn": "test",
            "xgboost": "disabled",
        },
    )


def _terminal_registration(
    session: Session,
    root: Path,
    synthetic_case: SimpleNamespace,
    *,
    research_decision: str,
):
    source_feature, manifest = _install_source_manifest(
        session,
        artifact_uri=(root / "synthetic-a2-rows.jsonl").as_posix(),
    )
    store = Gen1ResearchArtifactStore(
        root / "gate3b",
        minimum_free_bytes_after_write=0,
    )
    candidate = _freeze_candidate(
        store,
        synthetic_case,
        source_feature,
        manifest,
    )
    claim = store.claim_holdout_evaluation(candidate.artifact_identity_checksum)
    evaluation = replace(
        synthetic_case.evaluation,
        evaluation_identity_checksum=_hex(
            f"operator:{synthetic_case.evaluation.evaluation_identity_checksum}:"
            f"{research_decision}"
        ),
        research_decision=research_decision,
        research_decision_checks={"synthetic_operator_contract": True},
    )
    evaluation_artifact = store.complete_holdout_evaluation(claim, evaluation)
    model, record = register_terminal_gen1_research_evaluation(
        session,
        artifact_store=store,
        candidate=candidate,
        development=synthetic_case.development,
        evaluation_artifact=evaluation_artifact,
        evaluation=evaluation,
        code_revision=CODE_REVISION,
    )
    session.commit()
    return SimpleNamespace(
        store=store,
        source_feature=source_feature,
        manifest=manifest,
        candidate=candidate,
        evaluation=evaluation,
        evaluation_artifact=evaluation_artifact,
        model=model,
        record=record,
    )


def test_exact_approved_a2_manifest_pin_rejects_every_other_identity(
    session: Session,
    artifact_root: Path,
) -> None:
    unapproved_but_valid_sha256 = _hex("not-the-approved-a2-manifest")
    assert unapproved_but_valid_sha256 != GEN1_APPROVED_A2_IDENTITY_CHECKSUM

    with pytest.raises(
        Gen1AdmissionError,
        match="explicitly approved strict A2 identity",
    ):
        load_gen1_a2_admission(
            session,
            dataset_artifact_root=artifact_root / "must-not-be-read",
            manifest_identity_checksum=unapproved_but_valid_sha256,
            policy=Gen1ResearchPolicy(),
        )


def test_operator_content_code_identity_is_stable_and_content_addressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="frozen-head\n"),
    )
    stable_first = _operator_code_identity()
    stable_second = _operator_code_identity()
    assert stable_first == stable_second
    assert stable_first[0].startswith("gate3b-content-")
    assert stable_first[1] == "frozen-head"

    original_read_bytes = Path.read_bytes

    def governed_content_change(path: Path) -> bytes:
        payload = original_read_bytes(path)
        if path.as_posix().endswith("server/app/modeling/gen1.py"):
            return payload + b"\n# synthetic governed-byte change\n"
        return payload

    monkeypatch.setattr(Path, "read_bytes", governed_content_change)
    changed = _operator_code_identity()
    assert changed[0] != stable_first[0]
    assert changed[1] == stable_first[1]


def test_gate3b_operator_uses_only_the_canonical_configured_root(
    settings: Settings,
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canonical_root = (artifact_root / "canonical-gate3-root").resolve()
    configured = settings.model_copy(
        update={"gate3_artifact_root": canonical_root}
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: configured)
    dataset_root, research_root = _operator_paths()
    assert dataset_root == canonical_root / "datasets"
    assert research_root == canonical_root / "gate3b"

    # Gate 3B's two-phase operator has one entrypoint: this module.  The older
    # general CLI must not provide an alternate Phase-1/Phase-2 route.
    from app.cli import main as legacy_main

    monkeypatch.setattr(sys, "argv", ["app.cli", "phase1-freeze"])
    with pytest.raises(SystemExit) as rejected:
        legacy_main()
    assert rejected.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_phase1_operator_dispatch_reaches_fit_boundary_with_approved_source(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.training.gate3b as gate3b

    class FitBoundaryReached(RuntimeError):
        pass

    class SessionContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_: object) -> None:
            return None

    admission = SimpleNamespace(
        status=gate3b.GEN1_RESEARCH_ADMISSION_STATUS,
        policy=Gen1ResearchPolicy(),
        build=SimpleNamespace(rows=()),
        dataset_id=gate3b.GEN1_APPROVED_A2_DATASET_ID,
        dataset_identity_checksum=gate3b.GEN1_APPROVED_A2_IDENTITY_CHECKSUM,
        dataset_rows_checksum=gate3b.GEN1_APPROVED_A2_ROWS_CHECKSUM,
        source_feature_version_id=gate3b.GEN1_APPROVED_A2_FEATURE_VERSION_ID,
        row_count=gate3b.GEN1_APPROVED_A2_ROW_COUNT,
        event_date_count=gate3b.GEN1_APPROVED_A2_EVENT_DATE_COUNT,
    )
    dispatch: dict[str, object] = {}

    def fake_load(
        session: object,
        *,
        dataset_artifact_root: Path,
        manifest_identity_checksum: str,
    ) -> object:
        dispatch["session"] = session
        dispatch["dataset_artifact_root"] = dataset_artifact_root
        dispatch["manifest_identity_checksum"] = manifest_identity_checksum
        return admission

    def stop_before_fit(
        rows: object,
        *,
        policy: Gen1ResearchPolicy,
        source_dataset_rows_checksum: str,
        enable_xgboost: bool,
        run_ablations: bool,
    ) -> object:
        dispatch["fit_rows"] = rows
        dispatch["fit_policy"] = policy
        dispatch["source_dataset_rows_checksum"] = source_dataset_rows_checksum
        dispatch["enable_xgboost"] = enable_xgboost
        dispatch["run_ablations"] = run_ablations
        raise FitBoundaryReached

    dataset_root = (artifact_root / "canonical" / "datasets").resolve()
    research_root = (artifact_root / "canonical" / "gate3b").resolve()
    monkeypatch.setattr(gate3b, "_operator_paths", lambda: (dataset_root, research_root))
    monkeypatch.setattr(
        gate3b,
        "_operator_code_identity",
        lambda: (CODE_REVISION, BASELINE_CODE_REVISION),
    )
    monkeypatch.setattr(gate3b, "load_gen1_a2_admission", fake_load)
    monkeypatch.setattr(gate3b, "run_gen1_development", stop_before_fit)
    monkeypatch.setattr("app.audit.install_immutability_guards", lambda: None)
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: SessionContext())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "app.training.gate3b",
            "phase1-freeze",
            "--manifest-identity",
            gate3b.GEN1_APPROVED_A2_IDENTITY_CHECKSUM,
        ],
    )

    with pytest.raises(FitBoundaryReached):
        gate3b._operator_main()

    assert dispatch == {
        "session": dispatch["session"],
        "dataset_artifact_root": dataset_root,
        "manifest_identity_checksum": gate3b.GEN1_APPROVED_A2_IDENTITY_CHECKSUM,
        "fit_rows": (),
        "fit_policy": admission.policy,
        "source_dataset_rows_checksum": gate3b.GEN1_APPROVED_A2_ROWS_CHECKSUM,
        "enable_xgboost": True,
        "run_ablations": True,
    }
    assert not research_root.exists()


def test_terminal_registration_is_coherent_and_publicly_available(
    session: Session,
    settings: Settings,
    artifact_root: Path,
    synthetic_case: SimpleNamespace,
) -> None:
    terminal = _terminal_registration(
        session,
        artifact_root,
        synthetic_case,
        research_decision="RESEARCH_CHALLENGER",
    )

    features = list(session.scalars(select(FeatureVersion)))
    models = list(session.scalars(select(ModelVersion)))
    evaluations = list(session.scalars(select(ModelEvaluation)))
    assert len(features) == 2
    assert len(models) == len(evaluations) == 1
    assert terminal.model.feature_version_id != terminal.source_feature.id
    assert terminal.model.training_manifest_id == terminal.manifest.id
    assert terminal.record.model_version_id == terminal.model.id
    assert terminal.record.training_manifest_id == terminal.manifest.id
    assert terminal.model.parameters["source_feature_version_id"] == str(
        terminal.source_feature.id
    )
    assert terminal.model.parameters["derived_feature_version_id"] == str(
        terminal.model.feature_version_id
    )
    assert terminal.model.parameters["research_decision"] == "RESEARCH_CHALLENGER"
    assert set(terminal.record.metrics) >= {
        "validation",
        "holdout",
        "research_decision",
    }

    published = ReadService(session, settings).performance().offline_evaluation
    assert published is not None
    assert published.status == "AVAILABLE", published.status_reason
    assert published.research_decision == "RESEARCH_CHALLENGER"
    assert published.dataset_checksum == terminal.manifest.identity_checksum
    assert published.feature_set_checksum == terminal.model.parameters[
        "derived_feature_schema_hash"
    ]
    assert published.validation_metrics is not None
    assert published.validation_metrics.brier_score == pytest.approx(
        terminal.record.metrics["validation"]["brier_score"]
    )
    assert published.holdout_metrics is not None
    assert published.holdout_metrics.brier_score == pytest.approx(
        terminal.record.metrics["holdout"]["brier_score"]
    )
    assert published.promotion is not None
    assert published.promotion.decision == "NOT_PROMOTED"


def test_terminal_registration_resume_is_idempotent(
    session: Session,
    artifact_root: Path,
    synthetic_case: SimpleNamespace,
) -> None:
    terminal = _terminal_registration(
        session,
        artifact_root,
        synthetic_case,
        research_decision="RESEARCH_CHALLENGER",
    )
    before = (
        session.scalar(select(func.count()).select_from(FeatureVersion)),
        session.scalar(select(func.count()).select_from(ModelVersion)),
        session.scalar(select(func.count()).select_from(ModelEvaluation)),
    )

    first = resume_terminal_gen1_registration(
        session,
        artifact_store=terminal.store,
        candidate_artifact_identity_checksum=(
            terminal.candidate.artifact_identity_checksum
        ),
        code_revision=CODE_REVISION,
        baseline_code_revision=BASELINE_CODE_REVISION,
    )
    second = resume_terminal_gen1_registration(
        session,
        artifact_store=terminal.store,
        candidate_artifact_identity_checksum=(
            terminal.candidate.artifact_identity_checksum
        ),
        code_revision=CODE_REVISION,
        baseline_code_revision=BASELINE_CODE_REVISION,
    )
    after = (
        session.scalar(select(func.count()).select_from(FeatureVersion)),
        session.scalar(select(func.count()).select_from(ModelVersion)),
        session.scalar(select(func.count()).select_from(ModelEvaluation)),
    )
    assert before == after == (2, 1, 1)
    assert first.model_version_id == second.model_version_id == str(terminal.model.id)
    assert first.model_evaluation_id == second.model_evaluation_id == str(
        terminal.record.id
    )


def test_rejected_terminal_result_is_development_not_challenger(
    session: Session,
    artifact_root: Path,
    synthetic_case: SimpleNamespace,
) -> None:
    terminal = _terminal_registration(
        session,
        artifact_root,
        synthetic_case,
        research_decision="REJECTED_GEN1",
    )
    assert terminal.model.status == ModelStatus.DEVELOPMENT
    assert terminal.model.status != ModelStatus.CHALLENGER
    assert terminal.model.parameters["research_decision"] == "REJECTED_GEN1"
    assert terminal.record.metrics["research_decision"] == "REJECTED_GEN1"


def test_global_dataset_and_holdout_claim_blocks_a_second_candidate(
    session: Session,
    artifact_root: Path,
    synthetic_case: SimpleNamespace,
) -> None:
    source_feature, manifest = _install_source_manifest(
        session,
        artifact_uri=(artifact_root / "synthetic-a2-rows.jsonl").as_posix(),
    )
    store = Gen1ResearchArtifactStore(
        artifact_root / "gate3b",
        minimum_free_bytes_after_write=0,
    )
    first = _freeze_candidate(
        store,
        synthetic_case,
        source_feature,
        manifest,
        code_revision=CODE_REVISION,
    )
    second = _freeze_candidate(
        store,
        synthetic_case,
        source_feature,
        manifest,
        code_revision=CODE_REVISION + "-different-candidate",
    )
    assert first.artifact_identity_checksum != second.artifact_identity_checksum
    assert first.dataset_rows_checksum == second.dataset_rows_checksum
    assert first.holdout_identity_checksum == second.holdout_identity_checksum

    first_claim = store.claim_holdout_evaluation(
        first.artifact_identity_checksum
    )
    assert first_claim.global_holdout_claim_identity_checksum
    with pytest.raises(Gen1ArtifactError, match="consumed global claim"):
        store.claim_holdout_evaluation(second.artifact_identity_checksum)
