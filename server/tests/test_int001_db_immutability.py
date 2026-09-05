"""INT-001: database-enforced authoritative-history immutability (PostgreSQL).

These tests prove the fix for the finding that bulk/Core/raw SQL bypasses the ORM
``before_flush`` guard. They install the production triggers
(``app.audit.immutability_sql``) on a disposable PostgreSQL cluster and assert that
*direct SQL* UPDATE/DELETE against every protected authority table is rejected,
while legitimate append/terminal-transition paths still work.

They are skipped unless ``AISP_TEST_PG_URL`` names a disposable PostgreSQL database
(never the operational one). The fast SQLite suite continues to cover the ORM
guard in ``test_persistence_integrity.py``.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.audit import install_immutability_guards
from app.audit.immutability_sql import (
    GUARDED_TABLES,
    drop_postgresql_guards,
    install_postgresql_guards,
)
from app.core.config import Settings
from app.db.base import Base
from app.jobs import seed_demo_data
from app.models import (
    Forecast,
    ForecastStatus,
    Game,
    MarketOutcome,
    ModelVersion,
    OfficialAspPosition,
    PositionResult,
    PositionSettlement,
    PositionStatus,
    ShadowExperiment,
    ShadowExperimentDecision,
)
from app.models.entities import ExperimentDecisionPhase

PG_URL = os.getenv("AISP_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="AISP_TEST_PG_URL not set; INT-001 DB triggers require a disposable PostgreSQL cluster",
)

TODAY = date(2026, 8, 12)


@pytest.fixture(scope="module")
def pg_sessionmaker(tmp_path_factory):
    root = tmp_path_factory.mktemp("int001")
    settings = Settings(
        app_env="test",
        database_url=PG_URL,
        demo_mode=True,
        the_odds_api_key="",
        sportsdataio_api_key="",
        odds_polling_enabled=False,
        open_meteo_enabled=False,
        statcast_enabled=False,
        scheduler_enabled=False,
        simulation_draws=200,
        simulation_seed=314159,
        raw_data_root=root / "raw",
        curated_data_root=root / "curated",
        feature_data_root=root / "features",
        secret_key="test-secret-with-at-least-thirty-two-characters",
        admin_email="admin@example.com",
        admin_password="TestAdministrator123!",
    )
    engine = create_engine(PG_URL, future=True)
    # Fresh schema on the disposable cluster.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    install_immutability_guards()  # ORM guard (harmless; process-global)
    with engine.begin() as connection:
        drop_postgresql_guards(connection)
        install_postgresql_guards(connection)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture(scope="module")
def seeded(pg_sessionmaker):
    """Seed a coherent demo slice plus one experiment/decision/position/settlement."""
    import asyncio

    factory = pg_sessionmaker
    settings = Settings(
        app_env="test",
        database_url=PG_URL,
        demo_mode=True,
        scheduler_enabled=False,
        odds_polling_enabled=False,
        open_meteo_enabled=False,
        statcast_enabled=False,
        simulation_draws=200,
        simulation_seed=314159,
        secret_key="test-secret-with-at-least-thirty-two-characters",
        admin_password="TestAdministrator123!",
    )
    with factory() as session:
        asyncio.run(seed_demo_data(session, settings, TODAY))
        session.commit()

        forecast = session.scalar(
            select(Forecast).where(Forecast.status == ForecastStatus.PUBLISHED).limit(1)
        )
        assert forecast is not None, "demo seed produced no published forecast"
        game = session.get(Game, forecast.game_id)
        outcome = session.scalar(select(MarketOutcome).limit(1))
        assert outcome is not None, "demo seed produced no market outcome"
        model_version = session.scalar(select(ModelVersion).limit(1))
        assert model_version is not None, "demo seed produced no model version"

        experiment = ShadowExperiment(
            name="INT-001 experiment",
            version=1,
            started_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
            start_date=TODAY,
            starting_bankroll=1000.0,
            sport="MLB",
            recommendation_policy_version="REC_V1",
            staking_policy_version="STAKE_V1",
            decision_policy="POLICY_V1",
            controlling_engine="AISP_BASELINE",
            supported_markets=["MONEYLINE"],
            kelly_fraction=0.25,
            unit_percentage=0.01,
            max_position_percentage=0.01,
            max_same_game_percentage=0.02,
            max_daily_percentage=0.05,
            code_commit="a" * 40,
            code_identity=f"git:{'a' * 40}",
            is_active=True,
        )
        session.add(experiment)
        session.flush()

        decision = ShadowExperimentDecision(
            experiment_id=experiment.id,
            game_id=game.id,
            slate_date=TODAY,
            phase=ExperimentDecisionPhase.FINAL_PREGAME,
            sport="BASEBALL_MLB",
            market="MONEYLINE",
            period="Full game",
            selection="HOME",
            recommended_at=datetime(2026, 8, 12, 16, tzinfo=UTC),
            recommendation="BET",
            recommendation_reason="INT-001 seed decision",
            qualification_reasons=["seed"],
            evidence_fingerprint="int001-decision-fp",
            source_mode="DEMO",
        )
        session.add(decision)
        session.flush()

        position = OfficialAspPosition(
            forecast_id=forecast.id,
            market_outcome_id=outcome.id,
            placed_at=decision.recommended_at,
            stake=12.5,
            decimal_odds=1.87,
            result=PositionResult.PENDING,
            status=PositionStatus.OPEN,
            strategy="STAKE_V1",
            shadow_experiment_id=experiment.id,
            shadow_decision_id=decision.id,
            bankroll_before_position=1000.0,
            recommendation_policy_version="REC_V1",
            staking_policy_version="STAKE_V1",
        )
        session.add(position)
        session.flush()

        settlement = PositionSettlement(
            position_id=position.id,
            result=PositionResult.LOSS,
            settled_at=datetime(2026, 8, 12, 23, tzinfo=UTC),
            profit_loss=-12.5,
            bankroll_before=1000.0,
            bankroll_after=987.5,
        )
        session.add(settlement)
        session.commit()

        return {
            "forecast_id": str(forecast.id),
            "model_version_id": str(model_version.id),
            "experiment_id": str(experiment.id),
            "decision_id": str(decision.id),
            "position_id": str(position.id),
            "settlement_id": str(settlement.id),
            "outcome_id": str(outcome.id),
            "game_id": str(game.id),
        }


# ---------------------------------------------------------------------------
# Direct-SQL mutation attempts (the exact bypass INT-001 reported) must fail.
# ---------------------------------------------------------------------------

def _raw_sql_rejected(session: Session, statement: str, params: dict) -> str:
    """Execute a raw SQL statement expected to be rejected by a trigger."""
    with pytest.raises(DBAPIError) as excinfo:
        session.execute(text(statement), params)
        session.flush()
    session.rollback()
    return str(excinfo.value)


def test_raw_update_published_forecast_probability_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        msg = _raw_sql_rejected(
            session,
            "UPDATE forecasts SET home_win_probability = 0.999 WHERE id = :id",
            {"id": seeded["forecast_id"]},
        )
        assert "published forecasts cannot be modified" in msg
        # unchanged
        prob = session.execute(
            text("SELECT home_win_probability FROM forecasts WHERE id = :id"),
            {"id": seeded["forecast_id"]},
        ).scalar_one()
        assert prob != pytest.approx(0.999)


def test_raw_delete_published_forecast_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        msg = _raw_sql_rejected(
            session,
            "DELETE FROM forecasts WHERE id = :id",
            {"id": seeded["forecast_id"]},
        )
        assert "published forecasts cannot be deleted" in msg


def test_core_delete_loss_settlement_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        with pytest.raises(DBAPIError):
            session.execute(delete(PositionSettlement).where(
                PositionSettlement.id == seeded["settlement_id"]
            ))
            session.flush()
        session.rollback()
        remaining = session.scalar(select(func.count()).select_from(PositionSettlement))
        assert remaining == 1


def test_core_update_settlement_pnl_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        with pytest.raises(DBAPIError):
            session.execute(update(PositionSettlement).where(
                PositionSettlement.id == seeded["settlement_id"]
            ).values(profit_loss=1000.0))
            session.flush()
        session.rollback()


def test_raw_update_position_stake_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        msg = _raw_sql_rejected(
            session,
            "UPDATE official_asp_positions SET stake = 9999 WHERE id = :id",
            {"id": seeded["position_id"]},
        )
        assert "immutable" in msg


def test_raw_delete_experiment_position_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        msg = _raw_sql_rejected(
            session,
            "DELETE FROM official_asp_positions WHERE id = :id",
            {"id": seeded["position_id"]},
        )
        assert "cannot be deleted" in msg


def test_raw_update_model_identity_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        msg = _raw_sql_rejected(
            session,
            "UPDATE model_versions SET artifact_checksum = 'forged' WHERE id = :id",
            {"id": seeded["model_version_id"]},
        )
        assert "immutable" in msg


def test_raw_delete_model_version_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        msg = _raw_sql_rejected(
            session,
            "DELETE FROM model_versions WHERE id = :id",
            {"id": seeded["model_version_id"]},
        )
        assert "cannot be deleted" in msg


def test_raw_update_experiment_decision_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        msg = _raw_sql_rejected(
            session,
            "UPDATE shadow_experiment_decisions SET recommendation = 'PASS' WHERE id = :id",
            {"id": seeded["decision_id"]},
        )
        assert "append-only" in msg


def test_raw_delete_experiment_decision_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        msg = _raw_sql_rejected(
            session,
            "DELETE FROM shadow_experiment_decisions WHERE id = :id",
            {"id": seeded["decision_id"]},
        )
        assert "append-only" in msg


def test_raw_update_experiment_rejected(pg_sessionmaker, seeded):
    with pg_sessionmaker() as session:
        msg = _raw_sql_rejected(
            session,
            "UPDATE shadow_experiments SET starting_bankroll = 1000000 WHERE id = :id",
            {"id": seeded["experiment_id"]},
        )
        assert "append-only" in msg


# ---------------------------------------------------------------------------
# Legitimate append / terminal-transition paths must still succeed.
# ---------------------------------------------------------------------------

def test_legitimate_draft_forecast_publish_and_settlement(pg_sessionmaker, seeded):
    """A draft forecast may transition to published; a new position may settle once."""
    with pg_sessionmaker() as session:
        game_id = seeded["game_id"]
        model_version_id = seeded["model_version_id"]
        published = session.scalar(
            select(Forecast).where(Forecast.status == ForecastStatus.PUBLISHED).limit(1)
        )
        feature_version_id = str(published.feature_version_id)

        # Insert a fresh draft forecast, then publish it (draft -> published is lawful).
        session.execute(text(
            "INSERT INTO forecasts (id, game_id, model_version_id, feature_version_id, "
            "forecast_track, version, generated_at, available_as_of, status, lifecycle, "
            "home_win_probability, away_win_probability, source_mode, created_at) VALUES "
            "(gen_random_uuid(), :game, :model, :feature, 'CURRENT_DEVELOPMENT', 999, now(), "
            "now(), 'DRAFT', 'PREGAME', 0.5, 0.5, 'DEMO', now())"
        ), {"game": game_id, "model": model_version_id, "feature": feature_version_id})
        session.commit()
        draft_id = session.execute(text(
            "SELECT id FROM forecasts WHERE version = 999"
        )).scalar_one()

        session.execute(text(
            "UPDATE forecasts SET status = 'PUBLISHED', published_at = now() WHERE id = :id"
        ), {"id": draft_id})
        session.commit()
        status = session.execute(text(
            "SELECT status FROM forecasts WHERE id = :id"
        ), {"id": draft_id}).scalar_one()
        assert status == "PUBLISHED"


def test_legitimate_open_position_settles_once(pg_sessionmaker, seeded):
    """An OPEN position may transition to SETTLED with result/payout set (once)."""
    with pg_sessionmaker() as session:
        outcome_id = seeded["outcome_id"]
        # A fresh open, non-experiment position (no shadow_experiment_id).
        session.execute(text(
            "INSERT INTO official_asp_positions (id, market_outcome_id, placed_at, stake, "
            "decimal_odds, result, status, strategy, created_at) VALUES "
            "(gen_random_uuid(), :outcome, now(), 10.0, 2.0, 'PENDING', 'OPEN', 'flat', now())"
        ), {"outcome": outcome_id})
        session.commit()
        pos_id = session.execute(text(
            "SELECT id FROM official_asp_positions WHERE stake = 10.0 AND status = 'OPEN' "
            "ORDER BY created_at DESC LIMIT 1"
        )).scalar_one()

        # Lawful terminal transition: open -> settled, set result/payout/settled_at.
        session.execute(text(
            "UPDATE official_asp_positions SET status = 'SETTLED', result = 'WIN', "
            "payout = 20.0, settled_at = now() WHERE id = :id"
        ), {"id": pos_id})
        session.commit()

        # Append the single settlement record.
        session.execute(text(
            "INSERT INTO position_results (id, position_id, result, settled_at, profit_loss, created_at) "
            "VALUES (gen_random_uuid(), :pid, 'WIN', now(), 10.0, now())"
        ), {"pid": pos_id})
        session.commit()

        status = session.execute(text(
            "SELECT status FROM official_asp_positions WHERE id = :id"
        ), {"id": pos_id}).scalar_one()
        assert status == "SETTLED"

        # But a settled position can no longer be modified.
        with pytest.raises(DBAPIError):
            session.execute(text(
                "UPDATE official_asp_positions SET payout = 999 WHERE id = :id"
            ), {"id": pos_id})
            session.flush()
        session.rollback()


def test_legitimate_model_metrics_update_allowed(pg_sessionmaker, seeded):
    """The forward-performance metrics carve-out remains writable."""
    with pg_sessionmaker() as session:
        session.execute(text(
            "UPDATE model_versions SET metrics = '{\"log_loss\": 0.5}'::json WHERE id = :id"
        ), {"id": seeded["model_version_id"]})
        session.commit()
        metrics = session.execute(text(
            "SELECT metrics FROM model_versions WHERE id = :id"
        ), {"id": seeded["model_version_id"]}).scalar_one()
        assert metrics is not None


def test_transaction_rollback_leaves_no_partial_evidence(pg_sessionmaker, seeded):
    """A rejected mutation inside a multi-statement transaction leaves nothing behind."""
    with pg_sessionmaker() as session:
        before = session.scalar(select(func.count()).select_from(PositionSettlement))
        with pytest.raises(DBAPIError):
            # A legal insert followed by an illegal update in the same transaction.
            session.execute(text(
                "INSERT INTO position_results (id, position_id, result, settled_at, profit_loss, created_at) "
                "VALUES (gen_random_uuid(), :pid, 'PUSH', now(), 0.0, now())"
            ), {"pid": seeded["position_id"]})
            session.execute(text(
                "UPDATE position_results SET profit_loss = 1 WHERE position_id = :pid"
            ), {"pid": seeded["position_id"]})
            session.flush()
        session.rollback()
        after = session.scalar(select(func.count()).select_from(PositionSettlement))
        assert after == before


# ---------------------------------------------------------------------------
# Runtime role vs migration owner: a least-privilege runtime role that holds
# UPDATE/DELETE still cannot rewrite protected evidence and cannot disable guards.
# ---------------------------------------------------------------------------

def test_runtime_role_cannot_bypass_or_disable_guards(pg_sessionmaker, seeded):
    owner_engine = pg_sessionmaker.kw["bind"]
    role = "asp_runtime_int001"
    password = "runtime_disposable_pw"
    with owner_engine.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        # Deliberately over-grant (the current broad shape): the runtime role gets
        # SELECT/INSERT/UPDATE/DELETE on every guarded table.
        for table in GUARDED_TABLES:
            conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}"))

    url = owner_engine.url.set(username=role, password=password)
    runtime_engine = create_engine(url, future=True)
    try:
        with runtime_engine.connect() as conn:
            # Even with UPDATE/DELETE granted, the trigger blocks the mutation.
            with pytest.raises(DBAPIError):
                conn.execute(text("DELETE FROM position_results WHERE id = :id"),
                             {"id": seeded["settlement_id"]})
            conn.rollback()
            # The runtime role cannot disable the trigger (not the table owner).
            with pytest.raises(DBAPIError):
                conn.execute(text("ALTER TABLE position_results DISABLE TRIGGER asp_immutable_position_results"))
            conn.rollback()
            # And cannot switch to replication role to skip triggers (not superuser).
            with pytest.raises(DBAPIError):
                conn.execute(text("SET session_replication_role = replica"))
            conn.rollback()
    finally:
        runtime_engine.dispose()
        with owner_engine.begin() as conn:
            for table in GUARDED_TABLES:
                conn.execute(text(f"REVOKE ALL ON {table} FROM {role}"))
            conn.execute(text(f"REVOKE USAGE ON SCHEMA public FROM {role}"))
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
