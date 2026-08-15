"""Bounded PostgreSQL rehearsal for FD1 recovery identity finalization.

This module is deliberately opt-in.  Set ``FD1_RECOVERY_TEST_DATABASE_URL`` to
the local PostgreSQL URL used for the rehearsal.  The fixture rejects non-local
hosts, creates a randomly named schema, pins every test connection's
``search_path`` to only that schema plus ``pg_catalog``, migrates the schema,
and drops exactly that schema afterward.  It never reads or writes the public
application schema.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import Engine, create_engine, make_url, text

from app.gen2.fd1_identity import RegistryIdentityMappingBatch
from app.gen2.fd1_recovery_identity import (
    IdentitySourceRelease,
    IdentitySpoolBinding,
    write_identity_spool,
)
from app.gen2.fd1_recovery_sql import (
    FD1RecoverySqlError,
    copy_identity_spool,
    explain_identity_reconciliation,
    promote_identity_mappings,
    reconcile_identity_stage,
)
from app.gen2.open_data_registry import OpenDataIdentityMappingSpec


_DATABASE_URL_ENV = "FD1_RECOVERY_TEST_DATABASE_URL"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_SCHEMA_PATTERN = re.compile(r"^fd1_recovery_it_[0-9a-f]{24}$")
_SOURCE_KEY = "FD1_RECOVERY_PG_TEST"
_SOURCE_RELEASE_CHECKSUM = "a" * 64
_CLEAN_ROW_COUNT = 3_008
_COPY_BATCH_SIZE = 1_000


class _SimulatedInterruption(RuntimeError):
    """Raised immediately after the first durable COPY checkpoint."""


@pytest.fixture
def isolated_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, str]]:
    configured = os.getenv(_DATABASE_URL_ENV, "").strip()
    if not configured:
        pytest.skip(f"set {_DATABASE_URL_ENV} to run the local PostgreSQL rehearsal")
    base_url = make_url(configured)
    if base_url.get_backend_name() != "postgresql":
        pytest.skip(f"{_DATABASE_URL_ENV} must use PostgreSQL")
    if base_url.host not in _LOCAL_HOSTS:
        pytest.skip(f"{_DATABASE_URL_ENV} must target a loopback host")

    admin_engine = create_engine(base_url, future=True, pool_pre_ping=True)
    schema = f"fd1_recovery_it_{uuid4().hex[:24]}"
    assert _SCHEMA_PATTERN.fullmatch(schema)
    created = False
    isolated_engine: Engine | None = None
    try:
        # This is the read-only safety preflight.  No schema is created until
        # PostgreSQL, locality, writability, ownership privilege, and absence
        # of the exact generated target have all been proven.
        with admin_engine.connect() as connection:
            preflight = connection.execute(
                text(
                    "SELECT current_setting('transaction_read_only') AS read_only,"
                    "pg_is_in_recovery() AS in_recovery,"
                    "current_setting('server_version_num')::integer AS version_num,"
                    "has_database_privilege(current_user,current_database(),'CREATE') "
                    "AS can_create_schema,"
                    "EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:schema) "
                    "AS target_exists"
                ),
                {"schema": schema},
            ).mappings().one()
        if preflight["read_only"] != "off" or preflight["in_recovery"]:
            pytest.skip("local PostgreSQL is read-only or in recovery")
        if int(preflight["version_num"]) < 120_000:
            pytest.skip("FD1 PostgreSQL rehearsal requires PostgreSQL 12+")
        if not preflight["can_create_schema"]:
            pytest.skip("database user cannot create an isolated rehearsal schema")
        assert preflight["target_exists"] is False

        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        created = True

        query = dict(base_url.query)
        query["options"] = f"-csearch_path={schema},pg_catalog"
        isolated_url = base_url.set(query=query)
        isolated_url_text = isolated_url.render_as_string(hide_password=False)
        isolated_engine = create_engine(
            isolated_url,
            future=True,
            pool_pre_ping=True,
        )
        with isolated_engine.connect() as connection:
            current_schema = connection.execute(text("SELECT current_schema()"))
            assert current_schema.scalar_one() == schema

        # Alembic's environment reads DATABASE_URL directly.  Point it at the
        # fail-closed search path before applying the real migration chain.
        alembic_url_text = isolated_url_text.replace("%", "%%")
        monkeypatch.setenv("DATABASE_URL", alembic_url_text)
        server_root = Path(__file__).resolve().parents[1]
        config = Config(str(server_root / "alembic.ini"))
        config.set_main_option("script_location", str(server_root / "alembic"))
        config.set_main_option("sqlalchemy.url", alembic_url_text)
        command.upgrade(config, "head")

        # Prove all relations used by COPY/reconciliation resolve to the new
        # schema before any fixture row is inserted.
        required_relations = {
            "alembic_version",
            "historical_source_versions",
            "open_data_identity_mappings",
            "fd1_recovery_runs",
            "fd1_recovery_stage_checkpoints",
            "fd1_recovery_identity_stage",
            "fd1_recovery_identity_quarantine",
        }
        with isolated_engine.connect() as connection:
            search_path = connection.execute(text("SHOW search_path")).scalar_one()
            assert search_path.replace(" ", "") == f"{schema},pg_catalog"
            relation_schemas = {
                str(row["relname"]): str(row["nspname"])
                for row in connection.execute(
                    text(
                        "SELECT c.relname,n.nspname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname=:schema AND c.relname = ANY(:relations) "
                        "AND c.relkind IN ('r','p')"
                    ),
                    {"schema": schema, "relations": sorted(required_relations)},
                ).mappings()
            }
        assert relation_schemas == {name: schema for name in required_relations}
        yield isolated_engine, schema
    finally:
        if isolated_engine is not None:
            isolated_engine.dispose()
        if created:
            if not _SCHEMA_PATTERN.fullmatch(schema):  # fail closed before destructive DDL
                raise AssertionError("refusing to clean an unexpected schema name")
            with admin_engine.begin() as connection:
                exists = bool(
                    connection.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_namespace "
                            "WHERE nspname=:schema)"
                        ),
                        {"schema": schema},
                    ).scalar_one()
                )
                if exists:
                    connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin_engine.dispose()


def _spec_payload(spec: OpenDataIdentityMappingSpec) -> dict[str, object]:
    return {
        "source_key": spec.source_key,
        "entity_type": spec.entity_type,
        "source_namespace": spec.source_namespace,
        "external_id": spec.external_id,
        "canonical_namespace": spec.canonical_namespace,
        "canonical_id": spec.canonical_id,
        "source_row_number": spec.source_row_number,
        "source_row_checksum": spec.source_row_checksum,
        "confidence": spec.confidence,
        "evidence_logical_uri": spec.evidence_logical_uri,
        "valid_from": spec.valid_from.isoformat() if spec.valid_from else None,
        "valid_through": spec.valid_through.isoformat() if spec.valid_through else None,
    }


def _batch(
    batch_number: int,
    specs: Sequence[OpenDataIdentityMappingSpec],
) -> RegistryIdentityMappingBatch:
    checksum = sha256(
        json.dumps(
            [_spec_payload(spec) for spec in specs],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return RegistryIdentityMappingBatch(batch_number, tuple(specs), checksum)


def _spec(
    *,
    row_number: int,
    external_id: str,
    canonical_id: str | None,
    confidence: str = "EXACT",
    checksum_tag: str | None = None,
) -> OpenDataIdentityMappingSpec:
    return OpenDataIdentityMappingSpec(
        source_key=_SOURCE_KEY,
        entity_type="person",
        source_namespace="fd1_recovery_pg_external",
        external_id=external_id,
        canonical_namespace="fd1_recovery_pg_canonical",
        canonical_id=canonical_id,
        source_row_number=row_number,
        source_row_checksum=sha256(
            (checksum_tag or f"source-row-{row_number}-{canonical_id}").encode("utf-8")
        ).hexdigest(),
        confidence=confidence,
        evidence_logical_uri=f"recovery-test/identity/{row_number}.csv",
    )


def _clean_specs() -> tuple[OpenDataIdentityMappingSpec, ...]:
    exact_final_reuse = _spec(
        row_number=1,
        external_id="existing-exact",
        canonical_id="canonical-existing",
    )
    repeated = _spec(
        row_number=2,
        external_id="stage-exact-duplicate",
        canonical_id="canonical-stage-duplicate",
    )
    unresolved = _spec(
        row_number=3,
        external_id="source-unresolved",
        canonical_id=None,
        confidence="UNRESOLVED",
    )
    source_ambiguity = _spec(
        row_number=4,
        external_id="source-declared-conflict",
        canonical_id=None,
        confidence="CONFLICT",
    )
    unique = tuple(
        _spec(
            row_number=10_000 + index,
            external_id=f"unique-{index}",
            canonical_id=f"canonical-{index}",
        )
        for index in range(5, _CLEAN_ROW_COUNT)
    )
    specs = (
        exact_final_reuse,
        repeated,
        repeated,
        unresolved,
        source_ambiguity,
        *unique,
    )
    assert len(specs) == _CLEAN_ROW_COUNT
    return specs


def _write_spool(
    root: Path,
    *,
    source_release: IdentitySourceRelease,
    specs: Sequence[OpenDataIdentityMappingSpec],
) -> IdentitySpoolBinding:
    return write_identity_spool(
        (_batch(1, specs),),
        source_releases=(source_release,),
        output_directory=root,
        expected_row_count=len(specs),
        min_free_bytes=0,
    )


def _seed_source_version(engine: Engine, source_version_id: UUID) -> None:
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO historical_source_versions "
                "(id,source_key,dataset_version,provider_name,source_mode,"
                "source_classification,temporal_semantics,quality_tier,"
                "training_eligible,evaluation_eligible,availability_policy,"
                "policy_version,governance,documentation_uri,effective_from,"
                "effective_to,supersedes_id,identity_checksum) VALUES "
                "(:id,:source_key,:dataset_version,'FD1 recovery PostgreSQL fixture',"
                "'LIVE','PUBLIC_SECONDARY','RETROSPECTIVE_RECONSTRUCTION','B',"
                "false,false,CAST(:availability_policy AS json),"
                "'fd1_recovery_pg_test_v1',CAST(:governance AS json),"
                "'recovery-test/local-postgresql',:effective_from,NULL,NULL,:checksum)"
            ),
            {
                "id": source_version_id,
                "source_key": _SOURCE_KEY,
                "dataset_version": f"isolated-{source_version_id}",
                "availability_policy": json.dumps({"kind": "isolated_test"}),
                "governance": json.dumps({"network_calls": 0, "provider_calls": 0}),
                "effective_from": now,
                "checksum": _SOURCE_RELEASE_CHECKSUM,
            },
        )


def _seed_recovery_run(
    engine: Engine,
    *,
    run_id: UUID,
    checkpoint_id: UUID,
    scope: str,
    binding: IdentitySpoolBinding,
) -> None:
    now = datetime.now(UTC)
    manifest_checksum = sha256(f"manifest-{run_id}".encode("utf-8")).hexdigest()
    hash_cache_checksum = sha256(f"hash-cache-{run_id}".encode("utf-8")).hexdigest()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO fd1_recovery_runs "
                "(id,manifest_checksum,manifest_schema_version,manifest_logical_uri,"
                "manifest_header,hash_cache_schema_version,hash_cache_checksum,"
                "generation_code_revision,finalizer_code_revision,"
                "identity_spool_logical_uri,identity_spool_sha256,"
                "identity_spool_row_count,status,active_stage,heartbeat_at,started_at,"
                "deadline_at,max_runtime_minutes,heartbeat_seconds,provider_calls,"
                "progress,performance_metrics,last_operation) VALUES "
                "(:id,:manifest_checksum,'fd1_recovery_pg_manifest_v1',"
                ":manifest_uri,CAST(:manifest_header AS json),"
                "'fd1_recovery_pg_hash_cache_v1',:hash_cache_checksum,:generation,"
                ":finalizer,:spool_uri,:spool_sha,:spool_rows,'RUNNING',"
                "'IDENTITY_STAGED',:now,:now,:deadline,120,30,0,"
                "CAST(:progress AS json),CAST(:performance AS json),'COPY rehearsal')"
            ),
            {
                "id": run_id,
                "manifest_checksum": manifest_checksum,
                "manifest_uri": (
                    f".runtime/gate3c-fd1/recovery/test-{run_id}-manifest.json"
                ),
                "manifest_header": json.dumps({"isolated_postgres_test": True}),
                "hash_cache_checksum": hash_cache_checksum,
                "generation": "1" * 40,
                "finalizer": "2" * 40,
                "spool_uri": f".runtime/gate3c-fd1/recovery/{binding.path.name}",
                "spool_sha": binding.sha256,
                "spool_rows": binding.row_count,
                "now": now,
                "deadline": now + timedelta(minutes=30),
                "progress": json.dumps({"mappings_staged": 0}),
                "performance": json.dumps({}),
            },
        )
        connection.execute(
            text(
                "INSERT INTO fd1_recovery_stage_checkpoints "
                "(id,run_id,scope,stage_sequence,stage_name,attempt,status,"
                "input_checksum,started_at,heartbeat_at,progress,performance_metrics) "
                "VALUES (:id,:run_id,:scope,2,'IDENTITY_STAGED',1,'RUNNING',"
                ":input_checksum,:now,:now,CAST(:progress AS json),"
                "CAST(:performance AS json))"
            ),
            {
                "id": checkpoint_id,
                "run_id": run_id,
                "scope": scope,
                "input_checksum": binding.sha256,
                "now": now,
                "progress": json.dumps({"mappings_staged": 0}),
                "performance": json.dumps({}),
            },
        )


def _copy(
    engine: Engine,
    *,
    run_id: UUID,
    checkpoint_id: UUID,
    scope: str,
    binding: IdentitySpoolBinding,
    progress: Any = None,
) -> Any:
    stat = binding.path.stat()
    return copy_identity_spool(
        engine,
        run_id=run_id,
        scope=scope,
        spool_path=binding.path,
        expected_sha256=binding.sha256,
        expected_size_bytes=stat.st_size,
        expected_mtime_ns=stat.st_mtime_ns,
        expected_rows=binding.row_count,
        batch_size=_COPY_BATCH_SIZE,
        checkpoint_id=checkpoint_id,
        reuse_verified_hash=True,
        progress=progress,
    )


def _plan_relations(node: Mapping[str, Any]) -> set[str]:
    relations: set[str] = set()
    relation = node.get("Relation Name")
    if isinstance(relation, str):
        relations.add(relation)
    for child in node.get("Plans", []):
        if isinstance(child, Mapping):
            relations.update(_plan_relations(child))
    return relations


def test_postgresql_copy_reconcile_resume_conflict_and_atomic_rollback(
    isolated_postgres: tuple[Engine, str],
    tmp_path: Path,
) -> None:
    engine, schema = isolated_postgres
    source_version_id = uuid4()
    source_release = IdentitySourceRelease(
        source_key=_SOURCE_KEY,
        source_version_id=str(source_version_id),
        source_release_identity_checksum=_SOURCE_RELEASE_CHECKSUM,
    )
    _seed_source_version(engine, source_version_id)

    clean_binding = _write_spool(
        tmp_path / "clean",
        source_release=source_release,
        specs=_clean_specs(),
    )
    clean_run_id = uuid4()
    clean_checkpoint_id = uuid4()
    _seed_recovery_run(
        engine,
        run_id=clean_run_id,
        checkpoint_id=clean_checkpoint_id,
        scope="REHEARSAL",
        binding=clean_binding,
    )

    progress_calls: list[tuple[int, int]] = []

    def interrupt_after_first_chunk(current: int, total: int) -> None:
        progress_calls.append((current, total))
        raise _SimulatedInterruption("simulated process exit after durable COPY chunk")

    with pytest.raises(_SimulatedInterruption):
        _copy(
            engine,
            run_id=clean_run_id,
            checkpoint_id=clean_checkpoint_id,
            scope="REHEARSAL",
            binding=clean_binding,
            progress=interrupt_after_first_chunk,
        )
    assert progress_calls == [(_COPY_BATCH_SIZE, _CLEAN_ROW_COUNT)]
    with engine.connect() as connection:
        interrupted = connection.execute(
            text(
                "SELECT (SELECT COUNT(*) FROM fd1_recovery_identity_stage "
                "WHERE run_id=:run_id AND scope='REHEARSAL') AS staged,"
                "(SELECT resume_token FROM fd1_recovery_stage_checkpoints "
                "WHERE run_id=:run_id AND scope='REHEARSAL' "
                "AND stage_name='IDENTITY_STAGED') AS resume_token"
            ),
            {"run_id": clean_run_id},
        ).mappings().one()
    assert int(interrupted["staged"]) == _COPY_BATCH_SIZE
    assert interrupted["resume_token"]["high_water_ordinal"] == _COPY_BATCH_SIZE
    durable_byte_offset = int(interrupted["resume_token"]["byte_offset"])
    assert clean_binding.data_byte_offset < durable_byte_offset < clean_binding.byte_count

    copy_metrics = _copy(
        engine,
        run_id=clean_run_id,
        checkpoint_id=clean_checkpoint_id,
        scope="REHEARSAL",
        binding=clean_binding,
    )
    assert copy_metrics.rows_staged == _CLEAN_ROW_COUNT
    assert copy_metrics.resumed_after_ordinal == _COPY_BATCH_SIZE
    assert copy_metrics.final_byte_offset == clean_binding.byte_count
    assert copy_metrics.chunks_committed == 3
    assert copy_metrics.staging_table_bytes > 0

    # Preload one byte-exact canonical row.  The reconciler must reuse it and
    # must separately collapse the two identical stage rows at ordinals 2/3.
    with engine.begin() as connection:
        inserted = connection.execute(
            text(
                "INSERT INTO open_data_identity_mappings "
                "(id,source_version_id,entity_type,source_namespace,external_id,"
                "canonical_namespace,canonical_id,source_row_number,"
                "source_row_checksum,confidence,valid_from,valid_through,"
                "evidence_logical_uri,identity_checksum) "
                "SELECT target_mapping_id,source_version_id,entity_type,"
                "source_namespace,external_id,canonical_namespace,canonical_id,"
                "source_row_number,source_row_checksum,confidence,valid_from,"
                "valid_through,evidence_logical_uri,identity_checksum "
                "FROM fd1_recovery_identity_stage WHERE run_id=:run_id "
                "AND scope='REHEARSAL' AND staging_ordinal=1"
            ),
            {"run_id": clean_run_id},
        )
    assert inserted.rowcount == 1

    reconciliation = reconcile_identity_stage(
        engine,
        run_id=clean_run_id,
        scope="REHEARSAL",
        statement_timeout_ms=120_000,
    )
    assert reconciliation.total_rows == _CLEAN_ROW_COUNT
    assert reconciliation.planned_insert == 3_004
    assert reconciliation.exact_reuse == 2
    assert reconciliation.unresolved == 1
    assert reconciliation.source_declared_conflicts == 1
    assert reconciliation.blocking_conflicts == 0
    assert reconciliation.quarantine_rows == 2
    with engine.connect() as connection:
        selected = {
            int(row["staging_ordinal"]): row
            for row in connection.execute(
                text(
                    "SELECT staging_ordinal,outcome,matched_mapping_id,"
                    "matched_stage_ordinal,reason_code "
                    "FROM fd1_recovery_identity_stage WHERE run_id=:run_id "
                    "AND scope='REHEARSAL' AND staging_ordinal<=5 "
                    "ORDER BY staging_ordinal"
                ),
                {"run_id": clean_run_id},
            ).mappings()
        }
    assert selected[1]["outcome"] == "REUSE"
    assert selected[1]["matched_mapping_id"] is not None
    assert selected[2]["outcome"] == "INSERT"
    assert selected[3]["outcome"] == "REUSE"
    assert int(selected[3]["matched_stage_ordinal"]) == 2
    assert selected[4]["reason_code"] == "SOURCE_DECLARED_UNRESOLVED"
    assert selected[5]["reason_code"] == "SOURCE_DECLARED_CONFLICT_QUARANTINED"

    plan = explain_identity_reconciliation(
        engine,
        run_id=clean_run_id,
        scope="REHEARSAL",
    )
    assert float(plan["Execution Time"]) >= 0
    assert _plan_relations(plan["Plan"]) >= {
        "fd1_recovery_identity_stage",
        "open_data_identity_mappings",
    }
    with engine.connect() as connection:
        indexes = {
            str(row["indexname"]): str(row["indexdef"])
            for row in connection.execute(
                text(
                    "SELECT indexname,indexdef FROM pg_indexes "
                    "WHERE schemaname=:schema AND tablename IN "
                    "('fd1_recovery_identity_stage','open_data_identity_mappings')"
                ),
                {"schema": schema},
            ).mappings()
        }
    assert "ix_fd1_recovery_identity_natural" in indexes
    assert "ix_fd1_recovery_identity_outcome" in indexes
    assert "uq_open_data_identity_mapping_natural" in indexes
    assert all(schema in definition for definition in indexes.values())

    # Promotion is caller-transactional.  A different backend cannot observe
    # any insert before commit, and rollback returns canonical row count to the
    # exact pre-promotion value.
    with engine.connect() as writer:
        transaction = writer.begin()
        writer_pid = int(writer.execute(text("SELECT pg_backend_pid()")).scalar_one())
        promotion = promote_identity_mappings(
            writer,
            run_id=clean_run_id,
            scope="REHEARSAL",
        )
        assert promotion.inserted == 3_004
        assert promotion.reused == 2
        assert promotion.unresolved == 1
        assert promotion.source_declared_conflicts == 1
        writer_count = int(
            writer.execute(text("SELECT COUNT(*) FROM open_data_identity_mappings"))
            .scalar_one()
        )
        assert writer_count == 3_005
        with engine.connect() as observer:
            observer_pid = int(
                observer.execute(text("SELECT pg_backend_pid()")).scalar_one()
            )
            observer_count = int(
                observer.execute(
                    text("SELECT COUNT(*) FROM open_data_identity_mappings")
                ).scalar_one()
            )
        assert observer_pid != writer_pid
        assert observer_count == 1
        transaction.rollback()
    with engine.connect() as connection:
        assert int(
            connection.execute(text("SELECT COUNT(*) FROM open_data_identity_mappings"))
            .scalar_one()
        ) == 1

    # A separate two-row run proves a true competing-canonical assertion is a
    # blocking conflict and that reconciliation commits diagnostic evidence
    # before raising.  A retry remains blocked without duplicating quarantine.
    conflict_specs = (
        _spec(
            row_number=900_001,
            external_id="true-competing-canonical",
            canonical_id="canonical-left",
            checksum_tag="true-conflict-left",
        ),
        _spec(
            row_number=900_001,
            external_id="true-competing-canonical",
            canonical_id="canonical-right",
            checksum_tag="true-conflict-right",
        ),
    )
    conflict_binding = _write_spool(
        tmp_path / "conflict",
        source_release=source_release,
        specs=conflict_specs,
    )
    conflict_run_id = uuid4()
    conflict_checkpoint_id = uuid4()
    _seed_recovery_run(
        engine,
        run_id=conflict_run_id,
        checkpoint_id=conflict_checkpoint_id,
        scope="FULL",
        binding=conflict_binding,
    )
    conflict_copy = _copy(
        engine,
        run_id=conflict_run_id,
        checkpoint_id=conflict_checkpoint_id,
        scope="FULL",
        binding=conflict_binding,
    )
    assert conflict_copy.rows_staged == 2
    with pytest.raises(FD1RecoverySqlError, match="2 blocking conflicts"):
        reconcile_identity_stage(
            engine,
            run_id=conflict_run_id,
            scope="FULL",
            statement_timeout_ms=120_000,
        )
    with engine.connect() as connection:
        durable_conflicts = list(
            connection.execute(
                text(
                    "SELECT s.staging_ordinal,s.outcome,s.reason_code,"
                    "q.evidence_checksum FROM fd1_recovery_identity_stage s "
                    "JOIN fd1_recovery_identity_quarantine q ON q.run_id=s.run_id "
                    "AND q.scope=s.scope AND q.staging_ordinal=s.staging_ordinal "
                    "WHERE s.run_id=:run_id AND s.scope='FULL' "
                    "ORDER BY s.staging_ordinal"
                ),
                {"run_id": conflict_run_id},
            ).mappings()
        )
    assert len(durable_conflicts) == 2
    assert {row["outcome"] for row in durable_conflicts} == {"CONFLICT"}
    assert {row["reason_code"] for row in durable_conflicts} == {
        "STAGE_NATURAL_KEY_CONFLICT"
    }
    assert all(len(str(row["evidence_checksum"])) == 64 for row in durable_conflicts)
    with pytest.raises(FD1RecoverySqlError, match="2 blocking conflicts"):
        reconcile_identity_stage(
            engine,
            run_id=conflict_run_id,
            scope="FULL",
            statement_timeout_ms=120_000,
        )
    with engine.connect() as connection:
        quarantine_count = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM fd1_recovery_identity_quarantine "
                    "WHERE run_id=:run_id AND scope='FULL'"
                ),
                {"run_id": conflict_run_id},
            ).scalar_one()
        )
        leaked_conflict_mappings = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM open_data_identity_mappings "
                    "WHERE external_id='true-competing-canonical'"
                )
            ).scalar_one()
        )
    assert quarantine_count == 2
    assert leaked_conflict_mappings == 0
