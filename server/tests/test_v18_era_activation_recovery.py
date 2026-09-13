"""Opening the V18 model era without mutating a single historical row.

The original boundary at 2026-09-12T04:00:00Z failed on every run with
``ShadowExperiment records are append-only``: ``capture_boundary`` tried to
flip ``boundary_capital``, ``starting_bankroll`` and ``is_active`` on the armed
row, and the immutability guard refused it. These tests pin the repair:

  * the era opens by INSERTING a successor, and the armed row is byte-for-byte
    what it was;
  * retrying writes nothing and opens nothing twice;
  * capital is the predecessor's realized bankroll minus its reserved open
    stake -- never a fresh bankroll, and never with Gen1's liabilities moved;
  * Gen1 history created after the failed boundary stays exactly where it is;
  * the successor is the successor of the GEN1 row, which is what closes Gen1
    to new decisions.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, League, Market, MarketOutcome, MarketType,
    OfficialAspPosition, PositionSettlement, PositionStatus, Season,
    ShadowExperiment, Sport, Team,
)
from app.models.entities import ModelChampionAuthority
from app.services.shadow_experiment_service import ShadowExperimentService
from app.services.v18_champion import (
    ACTIVE, ARMED, ENGINE_ID, SPORT, V18ChampionService,
)

ORIGINAL_BOUNDARY = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
RECOVERY_BOUNDARY = datetime(2026, 9, 12, 16, 30, tzinfo=UTC)
ARTIFACT = "44ec4f457741f1aaeff42b81af1be5fd2712e494709ff1a0a8f0742b88d71c8b"


def _gen1(session: Session, *, starting: float = 1000.0) -> ShadowExperiment:
    row = ShadowExperiment(
        name="ASP PRACTICAL PREGAME BANKROLL - 2026 V2.1", version=3,
        started_at=ORIGINAL_BOUNDARY - timedelta(days=12),
        start_date=(ORIGINAL_BOUNDARY - timedelta(days=12)).date(),
        starting_bankroll=starting, sport=SPORT,
        decision_policy="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
        controlling_engine="AISP_BASELINE", supported_markets=["MONEYLINE"],
        kelly_fraction=0.25, unit_percentage=0.005,
        max_position_percentage=0.01, max_same_game_percentage=0.02,
        max_daily_percentage=0.05, is_active=True,
        recommendation_policy_version="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
        staking_policy_version="AISP_QUARTER_KELLY_V1",
        code_commit="a357435", code_identity="fixture", policy_config={},
    )
    session.add(row)
    session.flush()
    return row


def _authority(session: Session, *, effective_at: datetime, digest: str,
               recovery: dict | None = None) -> ModelChampionAuthority:
    row = ModelChampionAuthority(
        handoff_sha256=digest, schema_version="AISP_MLB_V18_CONDITIONAL_CHAMPION_HANDOFF_V1",
        sport=SPORT, engine_id=ENGINE_ID, model_name="MLB_V18_HGB_PA", model_version="V18",
        model_artifact_sha256=ARTIFACT,
        champion_manifest_sha256="3e8dcc1c50dcdfd2d748e5e0de8e7595ad0b50a2c62eb9baadb4ffe9e5d640c2",
        model_era_id="ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1",
        effective_at=effective_at, historical_validation_under_review=True,
        execution_allowed_in_aisp=False, real_money_execution_allowed=False,
        gen1_fallback_allowed=False, position_backfill_allowed=False,
        minimum_adjusted_edge=0.02,
        recommendation_policy_version="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
        staking_policy_version="AISP_QUARTER_KELLY_V1",
        state=ARMED, imported_at=effective_at - timedelta(hours=10),
        evidence={"recovery": recovery} if recovery else {},
    )
    session.add(row)
    session.flush()
    return row


def _market_outcome(session: Session) -> MarketOutcome:
    """A fresh outcome per position.

    ``official_asp_positions`` is unique on (experiment, market_outcome): one
    experiment may hold at most one position per outcome. So each fixture
    position gets its own market, which is also what the real world looks like.
    """

    sport = session.scalar(select(Sport).where(Sport.code == "BASEBALL"))
    league = None
    if sport is None:
        sport = Sport(code="BASEBALL", name="Baseball")
        session.add(sport)
        session.flush()
    league = session.scalar(select(League).where(League.code == "MLB"))
    if league is None:
        league = League(sport_id=sport.id, code="MLB", name="MLB")
        session.add(league)
        session.flush()
    season = session.scalars(select(Season)).first()
    if season is None:
        season = Season(league_id=league.id, name="2026",
                        starts_at=ORIGINAL_BOUNDARY - timedelta(days=160),
                        ends_at=ORIGINAL_BOUNDARY + timedelta(days=30))
        session.add_all([season, Team(league_id=league.id, name="Miami Marlins",
                                      abbreviation="MIA")])
        session.flush()
    # A fresh game per position, so each gets its own moneyline market.
    game = Game(league_id=league.id, season_id=season.id,
                starts_at=ORIGINAL_BOUNDARY - timedelta(days=1),
                status=GameStatus.FINAL, source_mode="LIVE")
    session.add(game)
    session.flush()
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="FULL_GAME")
    session.add(market)
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="HOME", label="MIA")
    session.add(outcome)
    session.flush()
    return outcome


def _position(session: Session, experiment: ShadowExperiment, *, stake: float,
              settled: bool, profit: float = 0.0, placed_at: datetime | None = None,
              user_id=None) -> OfficialAspPosition:
    placed_at = placed_at or ORIGINAL_BOUNDARY - timedelta(days=1)
    position = OfficialAspPosition(
        user_id=user_id, forecast_id=None,
        market_outcome_id=_market_outcome(session).id,
        placed_at=placed_at, stake=stake, decimal_odds=2.0,
        status=PositionStatus.SETTLED if settled else PositionStatus.OPEN,
        result="WIN" if profit > 0 else ("LOSS" if settled else "PENDING"),
        settled_at=placed_at + timedelta(hours=4) if settled else None,
        shadow_experiment_id=experiment.id,
    )
    session.add(position)
    session.flush()
    if settled:
        session.add(PositionSettlement(
            position_id=position.id, profit_loss=profit,
            result="WIN" if profit > 0 else "LOSS",
            settled_at=placed_at + timedelta(hours=4),
        ))
        session.flush()
    return position


@pytest.fixture
def world(session: Session, settings):
    gen1 = _gen1(session)
    # Settled: two wins at +2.50 each, three losses at -2.50 each -> -2.50 net.
    for _ in range(2):
        _position(session, gen1, stake=2.50, settled=True, profit=2.50)
    for _ in range(3):
        _position(session, gen1, stake=2.50, settled=True, profit=-2.50)
    # One open position: 2.50 reserved, and it must NOT travel to V18.
    open_position = _position(session, gen1, stake=2.50, settled=False)
    authority = _authority(session, effective_at=RECOVERY_BOUNDARY, digest="a" * 64)
    service = V18ChampionService(session, settings)
    service.arm_experiment(now=RECOVERY_BOUNDARY - timedelta(hours=2))
    session.flush()
    armed = session.get(ShadowExperiment, authority.experiment_id)
    return {"session": session, "settings": settings, "gen1": gen1,
            "authority": authority, "armed": armed, "service": service,
            "open_position": open_position}


# -- the append-only repair --------------------------------------------------

def test_opening_the_era_inserts_a_successor_and_never_touches_the_armed_row(world):
    session, armed = world["session"], world["armed"]
    before = {
        "id": armed.id, "version": armed.version, "is_active": armed.is_active,
        "starting_bankroll": float(armed.starting_bankroll),
        "boundary_capital": armed.boundary_capital,
    }

    result = world["service"].capture_boundary(now=RECOVERY_BOUNDARY + timedelta(minutes=1))
    session.flush()

    assert result["captured"] is True
    assert result["state"] == ACTIVE
    assert result["armed_experiment_id"] == str(before["id"])
    assert result["experiment_id"] != str(before["id"])

    session.refresh(armed)
    assert armed.is_active is before["is_active"] is False
    assert float(armed.starting_bankroll) == before["starting_bankroll"]
    assert armed.boundary_capital is None
    assert armed.version == before["version"]

    successor = session.get(ShadowExperiment, uuid.UUID(result["experiment_id"]))
    assert successor.is_active is True
    assert successor.version == before["version"] + 1
    assert successor.boundary_capital["supersedes_armed_experiment_id"] == str(before["id"])
    assert successor.boundary_capital["transition_mechanism"] == "APPEND_ONLY_SUCCESSOR_EXPERIMENT"


def test_the_successor_succeeds_the_gen1_row_so_gen1_actually_closes(world):
    """Pointing the successor at the armed row would silently leave Gen1 open."""

    session = world["session"]
    result = world["service"].capture_boundary(now=RECOVERY_BOUNDARY + timedelta(minutes=1))
    session.flush()
    successor = session.get(ShadowExperiment, uuid.UUID(result["experiment_id"]))

    assert successor.predecessor_experiment_id == world["gen1"].id

    service = ShadowExperimentService(session, world["settings"])
    found = service._active_successor_era(world["gen1"], RECOVERY_BOUNDARY + timedelta(hours=1))
    assert found is not None and found.id == successor.id
    # And before the boundary the era does not close Gen1.
    assert service._active_successor_era(world["gen1"], RECOVERY_BOUNDARY - timedelta(hours=1)) is None


def test_retrying_after_the_boundary_opens_nothing_twice(world):
    session = world["session"]
    service = world["service"]

    first = service.capture_boundary(now=RECOVERY_BOUNDARY + timedelta(minutes=1))
    second = service.capture_boundary(now=RECOVERY_BOUNDARY + timedelta(minutes=6))
    third = service.capture_boundary(now=RECOVERY_BOUNDARY + timedelta(minutes=11))
    session.flush()

    assert first["captured"] is True
    assert second["captured"] is False and second["reused"] is True
    assert third["captured"] is False and third["reused"] is True
    assert second["experiment_id"] == third["experiment_id"] == first["experiment_id"]
    opened = session.scalars(select(ShadowExperiment).where(
        ShadowExperiment.model_era_id.is_not(None),
        ShadowExperiment.is_active.is_(True))).all()
    assert len(opened) == 1


def test_the_boundary_does_not_arrive_early(world):
    result = world["service"].capture_boundary(now=RECOVERY_BOUNDARY - timedelta(minutes=1))
    assert result["captured"] is False
    assert result["reason"] == "BOUNDARY_HAS_NOT_ARRIVED"
    assert result["state"] == ARMED


# -- capital reconciliation --------------------------------------------------

def test_capital_is_realized_bankroll_minus_reserved_open_stake(world):
    session = world["session"]
    result = world["service"].capture_boundary(now=RECOVERY_BOUNDARY + timedelta(minutes=1))
    session.flush()
    snapshot = result["boundary_capital"]

    # 1000 starting, +5.00 in wins, -7.50 in losses -> 997.50 realized.
    assert snapshot["predecessor_starting_bankroll"] == 1000.0
    assert snapshot["predecessor_realized_profit"] == -2.50
    assert snapshot["predecessor_realized_bankroll"] == 997.50
    # One open 2.50 stake stays behind so Gen1 can pay out.
    assert snapshot["predecessor_reserved_open_stake"] == 2.50
    assert snapshot["carried_forward_to_v18"] == 995.00
    assert snapshot["new_capital_created"] == 0.0
    assert snapshot["open_liabilities_stay_with"] == str(world["gen1"].id)

    successor = session.get(ShadowExperiment, uuid.UUID(result["experiment_id"]))
    assert float(successor.starting_bankroll) == 995.00
    # Emphatically not a second $1,000.
    assert float(successor.starting_bankroll) != 1000.00


def test_gen1_keeps_its_open_position_and_its_settlements(world):
    session = world["session"]
    before_positions = session.scalar(select(func.count()).select_from(OfficialAspPosition))
    before_settlements = session.scalar(select(func.count()).select_from(PositionSettlement))

    result = world["service"].capture_boundary(now=RECOVERY_BOUNDARY + timedelta(minutes=1))
    session.flush()
    successor_id = result["experiment_id"]

    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == before_positions
    assert session.scalar(select(func.count()).select_from(PositionSettlement)) == before_settlements
    # Nothing was transferred to the era.
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition).where(
        OfficialAspPosition.shadow_experiment_id == uuid.UUID(successor_id))) == 0
    session.refresh(world["open_position"])
    assert world["open_position"].shadow_experiment_id == world["gen1"].id
    assert world["open_position"].status is PositionStatus.OPEN


def test_gen1_history_created_after_the_failed_boundary_is_preserved(world):
    """A position placed between the failed and the recovery boundary is Gen1's."""

    session = world["session"]
    between = _position(
        session, world["gen1"], stake=1.75, settled=True, profit=1.75,
        placed_at=ORIGINAL_BOUNDARY + timedelta(hours=3),
    )
    result = world["service"].capture_boundary(now=RECOVERY_BOUNDARY + timedelta(minutes=1))
    session.flush()

    session.refresh(between)
    assert between.shadow_experiment_id == world["gen1"].id
    # And it counts toward Gen1's realized bankroll, not V18's opening capital.
    assert result["boundary_capital"]["predecessor_realized_profit"] == -0.75


# -- the recovery record -----------------------------------------------------

def test_a_recovery_block_travels_into_the_capital_snapshot(session: Session, settings):
    gen1 = _gen1(session)
    recovery = {
        "intendedOriginalBoundaryUtc": ORIGINAL_BOUNDARY.isoformat(),
        "failureReason": "ShadowExperiment records are append-only",
        "recoveryDecision": "new immutable future-boundary supplement; never backdated",
        "actualEffectiveAtUtc": RECOVERY_BOUNDARY.isoformat(),
    }
    _authority(session, effective_at=RECOVERY_BOUNDARY, digest="b" * 64, recovery=recovery)
    service = V18ChampionService(session, settings)
    service.arm_experiment(now=RECOVERY_BOUNDARY - timedelta(hours=2))
    result = service.capture_boundary(now=RECOVERY_BOUNDARY + timedelta(minutes=1))

    assert result["boundary_capital"]["recovery"] == recovery
    assert result["boundary_capital"]["boundary_utc"] == RECOVERY_BOUNDARY.isoformat()
    # The activation is not backdated to the boundary that failed.
    assert result["boundary_capital"]["boundary_utc"] != ORIGINAL_BOUNDARY.isoformat()


def test_a_supplement_whose_boundary_has_passed_is_refused(tmp_path: Path, session: Session, settings):
    """The contract's own rule: recover forward, never backdate."""

    from app.services.v18_champion import ChampionImportError

    payload = {
        "schemaVersion": "AISP_MLB_V18_CONDITIONAL_CHAMPION_HANDOFF_V1",
        "activation": {"authorized": True},
        "executionAllowedInAISP": False, "realMoneyExecutionAllowed": False,
        "championManifest": {"sha256": "3e8dcc1c" + "0" * 56},
        "modelIdentity": {"name": "MLB_V18_HGB_PA", "version": "V18", "artifactSha256": ARTIFACT},
        "modelEra": {
            "experimentId": "ERA", "effectiveAtUtc": "2026-09-12T04:00:00Z",
            "minimumAdjustedEdge": 0.02,
            "recommendationPolicyVersion": "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
            "stakingPolicyVersion": "AISP_QUARTER_KELLY_V1",
        },
    }
    path = tmp_path / "handoff.json"
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(body)
    import hashlib
    digest = hashlib.sha256(body).hexdigest()

    with pytest.raises(ChampionImportError, match="is not after"):
        V18ChampionService(session, settings).import_supplement(
            path, expected_sha256=digest,
            now=datetime(2026, 9, 12, 14, 0, tzinfo=UTC),
        )
