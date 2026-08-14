from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.audit import ImmutableRecordError
from app.audit.immutability import _APPEND_ONLY_TYPES
from app.models import HistoricalEvidenceStateAttestation


TABLE = "historical_evidence_state_attestations"


def _config(database_url: str) -> Config:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _attestation(*, identity: str = "1" * 64) -> HistoricalEvidenceStateAttestation:
    cutoff = datetime(2025, 3, 18, 4, tzinfo=UTC)
    return HistoricalEvidenceStateAttestation(
        game_id=uuid4(),
        season_id=uuid4(),
        feature_version_id=uuid4(),
        checkpoint_id=uuid4(),
        cutoff_at=cutoff,
        generated_at=datetime(2026, 8, 14, tzinfo=UTC),
        state_version="gate3a-a2-compact-v1",
        population="TIER_A_STRICT",
        outcome_history_count=0,
        team_history_count=0,
        player_history_count=0,
        statcast_event_count=0,
        evidence_start_at=None,
        evidence_end_at=None,
        eligible_max_at=None,
        family_counts={"league_prior": 1},
        family_checksums={"league_prior": "2" * 64},
        source_bundle=[{"role": "LEAGUE_PRIOR", "checksum": "2" * 64}],
        source_bundle_checksum="3" * 64,
        canonical_set_checksum="4" * 64,
        feature_values_checksum="5" * 64,
        target_excluded=False,
        temporal_valid=True,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum=identity,
    )


def _raw_values(*, identity_character: str, state_version: str) -> dict[str, object]:
    return {
        "game_id": "a" * 32,
        "season_id": "b" * 32,
        "feature_version_id": "c" * 32,
        "checkpoint_id": "d" * 32,
        "cutoff_at": "2025-03-18 04:00:00+00:00",
        "generated_at": "2026-08-14 00:00:00+00:00",
        "state_version": state_version,
        "population": "TIER_A_STRICT",
        "outcome_history_count": 0,
        "team_history_count": 0,
        "player_history_count": 0,
        "statcast_event_count": 0,
        "evidence_start_at": None,
        "evidence_end_at": None,
        "eligible_max_at": None,
        "family_counts": '{"league_prior": 1}',
        "family_checksums": '{"league_prior": "' + "e" * 64 + '"}',
        "source_bundle": '[{"role": "LEAGUE_PRIOR"}]',
        "source_bundle_checksum": "1" * 64,
        "canonical_set_checksum": "2" * 64,
        "feature_values_checksum": "3" * 64,
        "target_excluded": False,
        "temporal_valid": True,
        "source_mode": "LIVE",
        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
        "identity_checksum": identity_character * 64,
        "id": identity_character * 32,
    }


INSERT = text(
    f"INSERT INTO {TABLE} ("
    "game_id, season_id, feature_version_id, checkpoint_id, cutoff_at, generated_at, "
    "state_version, population, outcome_history_count, team_history_count, player_history_count, "
    "statcast_event_count, evidence_start_at, evidence_end_at, eligible_max_at, "
    "family_counts, family_checksums, source_bundle, source_bundle_checksum, "
    "canonical_set_checksum, feature_values_checksum, target_excluded, temporal_valid, "
    "source_mode, temporal_semantics, identity_checksum, id"
    ") VALUES ("
    ":game_id, :season_id, :feature_version_id, :checkpoint_id, :cutoff_at, "
    ":generated_at, :state_version, :population, :outcome_history_count, :team_history_count, "
    ":player_history_count, :statcast_event_count, :evidence_start_at, "
    ":evidence_end_at, :eligible_max_at, :family_counts, :family_checksums, "
    ":source_bundle, :source_bundle_checksum, :canonical_set_checksum, "
    ":feature_values_checksum, :target_excluded, :temporal_valid, :source_mode, "
    ":temporal_semantics, :identity_checksum, :id)"
)


def test_attestation_is_exported_append_only_and_accepts_zero_evidence_prior(
    session,
) -> None:
    assert HistoricalEvidenceStateAttestation in _APPEND_ONLY_TYPES
    record = _attestation()
    session.add(record)
    session.commit()

    assert record.family_counts == {"league_prior": 1}
    assert record.evidence_start_at is None
    assert record.evidence_end_at is None
    assert record.eligible_max_at is None

    record.canonical_set_checksum = "6" * 64
    with pytest.raises(
        ImmutableRecordError,
        match="HistoricalEvidenceStateAttestation records are append-only",
    ):
        session.commit()
    session.rollback()

    persisted = session.get(HistoricalEvidenceStateAttestation, record.id)
    assert persisted is not None
    session.delete(persisted)
    with pytest.raises(
        ImmutableRecordError,
        match="HistoricalEvidenceStateAttestation records are append-only",
    ):
        session.commit()
    session.rollback()


def test_attestation_model_contract_has_semantic_identity_and_temporal_checks() -> None:
    table = HistoricalEvidenceStateAttestation.__table__
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    check_sql = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if hasattr(constraint, "sqltext")
    }
    assert unique_names >= {
        "uq_hist_evidence_state_semantic",
        "uq_hist_evidence_state_identity",
    }
    assert "outcome_history_count >= 0" in check_sql["ck_hist_evidence_state_counts"]
    assert "evidence_start_at IS NULL" in check_sql[
        "ck_hist_evidence_state_bounds_presence"
    ]
    assert "evidence_end_at < cutoff_at" in check_sql[
        "ck_hist_evidence_state_bounds_order"
    ]
    assert "eligible_max_at >= evidence_end_at" in check_sql[
        "ck_hist_evidence_state_bounds_order"
    ]
    assert "TIER_B_ENHANCED" in check_sql["ck_hist_evidence_state_population"]
    assert "length(feature_values_checksum) = 64" in check_sql[
        "ck_hist_evidence_state_checksums"
    ]


def test_attestation_migration_fresh_sqlite_constraints_downgrade_and_check(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{(artifact_root / 'gate3a-a2-attestation.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = _config(database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    schema = inspect(engine)
    assert TABLE in schema.get_table_names()
    assert {column["name"] for column in schema.get_columns(TABLE)} >= {
        "game_id",
        "season_id",
        "feature_version_id",
        "checkpoint_id",
        "cutoff_at",
        "generated_at",
        "state_version",
        "population",
        "outcome_history_count",
        "team_history_count",
        "player_history_count",
        "statcast_event_count",
        "evidence_start_at",
        "evidence_end_at",
        "eligible_max_at",
        "family_counts",
        "family_checksums",
        "source_bundle",
        "source_bundle_checksum",
        "canonical_set_checksum",
        "feature_values_checksum",
        "target_excluded",
        "temporal_valid",
        "source_mode",
        "temporal_semantics",
        "identity_checksum",
    }
    assert {row["name"] for row in schema.get_unique_constraints(TABLE)} >= {
        "uq_hist_evidence_state_semantic",
        "uq_hist_evidence_state_identity",
    }
    materialization_unique = next(
        row
        for row in schema.get_unique_constraints("historical_feature_materializations")
        if row["name"] == "uq_hist_materialization_semantic"
    )
    assert materialization_unique["column_names"][-1] == "evidence_manifest_checksum"
    assert {row["name"] for row in schema.get_indexes(TABLE)} >= {
        "ix_hist_evidence_state_game_cutoff",
        "ix_hist_evidence_state_checkpoint",
        "ix_hist_evidence_state_season_population",
    }

    zero = _raw_values(identity_character="4", state_version="zero-prior-v1")
    with engine.begin() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 0
        connection.execute(INSERT, zero)

    positive_without_bounds = _raw_values(
        identity_character="5",
        state_version="positive-without-bounds-v1",
    )
    positive_without_bounds["team_history_count"] = 1
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(INSERT, positive_without_bounds)

    bounded = _raw_values(identity_character="6", state_version="bounded-v1")
    bounded.update({
        "team_history_count": 4,
        "player_history_count": 12,
        "evidence_start_at": "2025-03-01 00:00:00+00:00",
        "evidence_end_at": "2025-03-17 23:00:00+00:00",
        "eligible_max_at": "2025-03-17 23:00:00+00:00",
    })
    with engine.begin() as connection:
        connection.execute(INSERT, bounded)

    late_evidence = _raw_values(identity_character="7", state_version="late-v1")
    late_evidence.update({
        "team_history_count": 1,
        "evidence_start_at": "2025-03-17 23:00:00+00:00",
        "evidence_end_at": "2025-03-18 05:00:00+00:00",
        "eligible_max_at": "2025-03-18 05:00:00+00:00",
    })
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(INSERT, late_evidence)

    duplicate_semantic = dict(zero)
    duplicate_semantic.update({
        "identity_checksum": "8" * 64,
        "id": "8" * 32,
    })
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(INSERT, duplicate_semantic)

    corrected_state = dict(zero)
    corrected_state.update({
        "canonical_set_checksum": "7" * 64,
        "identity_checksum": "a" * 64,
        "id": "a" * 32,
    })
    with engine.begin() as connection:
        connection.execute(INSERT, corrected_state)

    short_checksum = _raw_values(identity_character="9", state_version="short-checksum-v1")
    short_checksum["feature_values_checksum"] = "short"
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(INSERT, short_checksum)

    with engine.connect() as connection:
        assert connection.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one() == 3
    engine.dispose()

    command.check(config)
    command.downgrade(config, "20260814_0007")
    engine = create_engine(database_url)
    downgraded = inspect(engine)
    assert TABLE not in downgraded.get_table_names()
    materialization_unique = next(
        row
        for row in downgraded.get_unique_constraints(
            "historical_feature_materializations"
        )
        if row["name"] == "uq_hist_materialization_semantic"
    )
    assert "evidence_manifest_checksum" not in materialization_unique["column_names"]
    engine.dispose()

    command.upgrade(config, "head")
    command.check(config)
