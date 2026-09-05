"""Database-level (PostgreSQL) enforcement of authoritative-history immutability.

The ORM ``before_flush`` guards in :mod:`app.audit.immutability` protect every
SQLAlchemy *instance* mutation, but they never see Core/bulk
``session.execute(update(...))`` / ``delete(...)`` or raw SQL, and the runtime
role otherwise holds UPDATE/DELETE on every authority table (finding INT-001).

This module emits ``BEFORE UPDATE OR DELETE`` row triggers that enforce the *same*
contract below the ORM, so bulk/Core/raw SQL cannot rewrite or delete published
forecasts, model identity, experiment decisions, positions, or settlements. The
triggers fire for every role (including the table owner); only a superuser
(``session_replication_role = replica``) or the owner disabling the trigger can
bypass them, which is why the runtime role must be a non-owner least-privilege
role (see the immutability contract).

The guards are intentionally a faithful mirror of the ORM contract so that no
legitimate write path that works today is broken: anything these triggers reject
is already rejected by the ORM guard.

PostgreSQL only. SQLite test databases rely on the ORM guard; the INT-001
regression suite exercises these triggers against a disposable PostgreSQL cluster.
"""

from __future__ import annotations

from sqlalchemy.engine import Connection
from sqlalchemy import text


# SQLAlchemy's non-native Enum (native_enum=False) persists each enum's *member
# name* (uppercase), not its StrEnum value, and the CHECK constraints store the
# same tokens (e.g. 'PUBLISHED', 'SETTLED', 'VOID'). The trigger bodies compare
# those persisted tokens.
_FORECAST_PUBLISHED = "PUBLISHED"
_POSITION_TERMINAL = ("SETTLED", "VOID")

# Columns whose change is rejected on UPDATE. Mirrors app.audit.immutability.
_MODEL_VERSION_IMMUTABLE = (
    "name", "version", "sport_id", "feature_version_id", "algorithm", "status",
    "training_window", "hyperparameters", "artifact_uri", "artifact_checksum",
    "parameters", "trained_at", "target", "training_manifest_id",
    "validation_window", "holdout_window", "calibrator_uri", "calibrator_checksum",
    "ensemble_weights", "code_revision", "identity_checksum", "feature_names",
    "seed_policy", "target_definition", "promotion_decision", "promotion_reason",
)
_POSITION_IMMUTABLE = (
    "forecast_id", "market_outcome_id", "odds_snapshot_id", "placed_at", "stake",
    "decimal_odds", "strategy", "shadow_experiment_id", "shadow_decision_id",
    "bankroll_before_position", "recommendation_policy_version",
    "staking_policy_version",
)

# Every trigger/function name this module owns, for idempotent create/drop.
GUARDED_TABLES = (
    "forecasts",
    "model_versions",
    "shadow_experiments",
    "shadow_experiment_decisions",
    "official_asp_positions",
    "position_results",
)


def _fn(table: str) -> str:
    return f"asp_guard_{table}"


def _trg(table: str) -> str:
    return f"asp_immutable_{table}"


def _immutable_column_check(cols: tuple[str, ...]) -> str:
    # Cast every column to text before comparing: the ``json`` type (used by several
    # model_versions columns) has no equality operator, so ``IS DISTINCT FROM`` must
    # operate on a text projection. Casting is uniform and universally defined, and
    # any textual change to a governance column is treated as a rejected mutation.
    parts = [f"OLD.{c}::text IS DISTINCT FROM NEW.{c}::text" for c in cols]
    return "\n           OR ".join(parts)


def _append_only_body(table: str) -> str:
    return (
        f"        RAISE EXCEPTION '% on {table} is prohibited: this table is "
        f"append-only authoritative evidence', TG_OP\n"
        f"            USING ERRCODE = 'restrict_violation';\n"
    )


def _function_sql(table: str) -> str:
    """Return the CREATE OR REPLACE FUNCTION statement for one guarded table."""
    header = (
        f"CREATE OR REPLACE FUNCTION {_fn(table)}() RETURNS trigger\n"
        f"LANGUAGE plpgsql\n"
        f"SET search_path = pg_catalog, pg_temp\n"
        f"AS $asp$\nBEGIN\n"
    )
    footer = "END;\n$asp$;"

    if table in ("shadow_experiments", "shadow_experiment_decisions", "position_results"):
        body = _append_only_body(table) + "    RETURN NULL;\n"
        return header + body + footer

    if table == "model_versions":
        checks = _immutable_column_check(_MODEL_VERSION_IMMUTABLE)
        body = (
            "    IF TG_OP = 'DELETE' THEN\n"
            "        RAISE EXCEPTION 'model_versions registry rows cannot be deleted'\n"
            "            USING ERRCODE = 'restrict_violation';\n"
            "    END IF;\n"
            f"    IF {checks}\n"
            "    THEN\n"
            "        RAISE EXCEPTION 'model_versions identity/governance columns are "
            "immutable after creation (only metrics may be recalculated)'\n"
            "            USING ERRCODE = 'restrict_violation';\n"
            "    END IF;\n"
            "    RETURN NEW;\n"
        )
        return header + body + footer

    if table == "forecasts":
        body = (
            "    IF TG_OP = 'DELETE' THEN\n"
            f"        IF OLD.status = '{_FORECAST_PUBLISHED}' THEN\n"
            "            RAISE EXCEPTION 'published forecasts cannot be deleted'\n"
            "                USING ERRCODE = 'restrict_violation';\n"
            "        END IF;\n"
            "        RETURN OLD;\n"
            "    END IF;\n"
            "    IF OLD.forecast_track IS DISTINCT FROM NEW.forecast_track THEN\n"
            "        RAISE EXCEPTION 'forecast_track is immutable after creation'\n"
            "            USING ERRCODE = 'restrict_violation';\n"
            "    END IF;\n"
            f"    IF OLD.status = '{_FORECAST_PUBLISHED}' THEN\n"
            "        RAISE EXCEPTION 'published forecasts cannot be modified; create a "
            "new version'\n"
            "            USING ERRCODE = 'restrict_violation';\n"
            "    END IF;\n"
            "    RETURN NEW;\n"
        )
        return header + body + footer

    if table == "official_asp_positions":
        checks = _immutable_column_check(_POSITION_IMMUTABLE)
        terminal = ", ".join(f"'{v}'" for v in _POSITION_TERMINAL)
        body = (
            "    IF TG_OP = 'DELETE' THEN\n"
            f"        IF OLD.shadow_experiment_id IS NOT NULL OR OLD.status IN ({terminal}) THEN\n"
            "            RAISE EXCEPTION 'experiment and settled official positions "
            "cannot be deleted'\n"
            "                USING ERRCODE = 'restrict_violation';\n"
            "        END IF;\n"
            "        RETURN OLD;\n"
            "    END IF;\n"
            f"    IF {checks}\n"
            "    THEN\n"
            "        RAISE EXCEPTION 'official position evidence columns are immutable "
            "after creation'\n"
            "            USING ERRCODE = 'restrict_violation';\n"
            "    END IF;\n"
            f"    IF OLD.status IN ({terminal}) THEN\n"
            "        RAISE EXCEPTION 'settled/void official positions cannot be modified'\n"
            "            USING ERRCODE = 'restrict_violation';\n"
            "    END IF;\n"
            "    RETURN NEW;\n"
        )
        return header + body + footer

    raise ValueError(f"No guard body defined for table {table!r}")


def _trigger_sql(table: str) -> str:
    return (
        f"CREATE TRIGGER {_trg(table)}\n"
        f"    BEFORE UPDATE OR DELETE ON {table}\n"
        f"    FOR EACH ROW EXECUTE FUNCTION {_fn(table)}();"
    )


def create_statements() -> list[str]:
    """All DDL statements to install the guards (idempotent for functions)."""
    statements: list[str] = []
    for table in GUARDED_TABLES:
        statements.append(_function_sql(table))
        statements.append(f"DROP TRIGGER IF EXISTS {_trg(table)} ON {table};")
        statements.append(_trigger_sql(table))
    return statements


def drop_statements() -> list[str]:
    statements: list[str] = []
    for table in GUARDED_TABLES:
        statements.append(f"DROP TRIGGER IF EXISTS {_trg(table)} ON {table};")
        statements.append(f"DROP FUNCTION IF EXISTS {_fn(table)}();")
    return statements


def install_postgresql_guards(connection: Connection) -> None:
    """Install the immutability triggers. No-op on non-PostgreSQL dialects."""
    if connection.dialect.name != "postgresql":
        return
    for statement in create_statements():
        connection.execute(text(statement))


def drop_postgresql_guards(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    for statement in drop_statements():
        connection.execute(text(statement))
