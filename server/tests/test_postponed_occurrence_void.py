"""AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1: settlement and the duplicate guard.

The incident: a CLE ML +101 paper position ($2.42) placed for Fri 2026-10-16
20:00 ET was settled LOSS on the Sunday makeup (same games row), and Sunday's
own QUALIFIED decision was blocked by the Friday position. Here:

* a postponed game not played on the Eastern date AISP recorded at placement
  settles VOID (stake returned, P&L 0) with an honest note -- never W/L on the
  makeup;
* a delayed, suspended-and-resumed or same-day-made-up game settles W/L;
* when the original date cannot be verified the position settles the legacy
  way, never silently: a payload receipt and, for the incident class, one
  operating alert; nothing is ever left open indefinitely;
* CANCELED still voids exactly as before; other sports keep their rules;
* the duplicate guard never writes, keeps blocking on the same occurrence and
  until the database's live-outcome index exists, and the original date's
  decision can never fund the makeup;
* a uniqueness refusal on one game's position never rolls back the freeze run.

The migration that narrows the database uniqueness (20261019_0055) and the
tests of the makeup actually receiving its own position live on the separate
migration branch; this release ships no migration.

SQLite only; no provider call. Times are UTC-aware.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.bankroll import occurrence as occ
from app.cfb.settlement_corrections import correct_settled_positions
from app.models import (
    AuditEvent, ExperimentDecisionPhase, GameStateObservation, GameStatus, OfficialAspPosition, PositionResult,
    PositionSettlement, PositionSettlementAdjustment, PositionStatus, ShadowExperimentDecision,
)
from app.services import shadow_experiment_service as ses
from app.services.shadow_experiment_service import FINAL_PREGAME_CUTOFF_MINUTES, ShadowExperimentService
from tests.postponement_fixtures import (
    FRIDAY_START, PLACED_AT, SETTLED_AT, STAKE, SUNDAY_START, build_incident, gso, mlb_experiment,
)

ET = occ.ET
#: This release ships no migration: the model (and so every test database)
#: still has the full UNIQUE(shadow_experiment_id, market_outcome_id). The
#: separate migration branch swaps in the live-outcome partial index; the
#: tests below read which schema they run on instead of assuming it.
MODEL_HAS_LIVE_INDEX = any(index.name == ses.LIVE_OUTCOME_INDEX
                           for index in OfficialAspPosition.__table__.indexes)


def _settle(session, settings, now):
    return ShadowExperimentService(session, settings).settle(now=now)


def _only_settlement(session, position):
    rows = list(session.scalars(select(PositionSettlement).where(PositionSettlement.position_id == position.id)))
    assert len(rows) <= 1
    return rows[0] if rows else None


def _payload(result, incident):
    return result["experiments"][incident.experiment.decision_policy]


def _alerts(session):
    return list(session.scalars(select(AuditEvent).where(AuditEvent.action == occ.REVIEW_ALERT_ACTION)))


# ---------------------------------------------------------------- the date --

def test_the_original_date_is_the_eastern_date_of_the_placement_schedule(session):
    incident = build_incident(session)
    original = occ.original_occurrence(session, game=incident.game, placed_at=incident.position.placed_at,
                                       decision=incident.decision)
    # 20:00 ET Friday is 00:00Z Saturday: the UTC date would name the wrong slate.
    assert FRIDAY_START.date() == date(2026, 10, 17)
    assert original.et_date == date(2026, 10, 16)
    assert original.basis == occ.BASIS_SLATE_OBSERVATION
    assert original.observation_id == incident.basis_observation.id
    assert occ.et_date(datetime(2026, 10, 17, 0, 0)) == date(2026, 10, 16)            # naive is UTC
    assert occ.et_date(datetime(2026, 10, 16, 20, 0, tzinfo=ET)) == date(2026, 10, 16)  # ET-aware (Postgres)
    # A Sunday placement made while the source still flapped is dated Sunday:
    # postponed copies at the Friday time are never a placement basis.
    sunday = occ.original_occurrence(
        session, game=incident.game, placed_at=datetime(2026, 10, 17, 9, 30, tzinfo=UTC),
        decision=SimpleNamespace(slate_date=date(2026, 10, 18), shadow_forecast_id=None))
    assert sunday.et_date == date(2026, 10, 18)


def test_the_slate_zone_is_the_eastern_zone_the_rule_is_defined_in(settings):
    """slate_date is written in display_timezone; the rule dates occurrences in ET."""

    assert str(occ.ET) == settings.display_timezone == "America/New_York"


def test_an_early_announced_rainout_is_dated_by_the_slate_not_the_makeup_listing(session, settings):
    """Review F1: the makeup's listing was seen BEFORE placement; the bet is still Friday's."""

    incident = build_incident(session, flapping=False)
    gso(session, incident.game, available=datetime(2026, 10, 16, 23, 35, tzinfo=UTC), scheduled=FRIDAY_START,
        status="postponed")
    gso(session, incident.game, available=datetime(2026, 10, 16, 23, 40, tzinfo=UTC),
        scheduled=datetime(2026, 10, 17, 21, 5, tzinfo=UTC), status="scheduled")   # Saturday 17:05 ET
    session.commit()
    original = occ.original_occurrence(session, game=incident.game, placed_at=PLACED_AT,
                                       decision=incident.decision)
    assert (original.et_date, original.observation_id) == (date(2026, 10, 16), incident.basis_observation.id)

    result = _settle(session, settings, SETTLED_AT)
    settlement = _only_settlement(session, incident.position)
    assert settlement.result == PositionResult.VOID and float(settlement.profit_loss) == 0.0
    assert _payload(result, incident)["voided_not_played_on_original_date"][0]["original"]["et_date"] == "2026-10-16"
    assert _alerts(session) == []


def test_unverifiable_dates_on_a_postponed_game_settle_legacy_but_never_silently(session, settings):
    """Review F1 (c): ambiguity is reported, alerted and correctable -- never a silent W/L."""

    incident = build_incident(session, slate_date=date(2026, 10, 15))
    verdict = occ.occurrence_verdict(session, position=incident.position, decision=incident.decision,
                                     game=incident.game, now=SETTLED_AT)
    assert (verdict.kind, verdict.reason) == (occ.SETTLE, occ.REASON_AMBIGUOUS_POSTPONED)
    assert verdict.unverified and verdict.review_required
    assert verdict.postponement_observation_id == incident.postponed_observation.id

    result = _settle(session, settings, SETTLED_AT)
    settlement = _only_settlement(session, incident.position)
    assert settlement.result == PositionResult.LOSS and float(settlement.profit_loss) == -STAKE
    assert settlement.settlement_note.startswith("Settled deterministically from canonical final score; ")
    assert occ.RULE_ID in settlement.settlement_note and occ.REASON_AMBIGUOUS_POSTPONED in settlement.settlement_note
    [receipt] = _payload(result, incident)["legacy_settled_unverified"]
    assert receipt["reason"] == occ.REASON_AMBIGUOUS_POSTPONED and receipt["result"] == "LOSS"
    assert receipt["operating_alert"] is True and receipt["unverified"] is True
    [alert] = _alerts(session)
    assert alert.entity_id == incident.position.id and alert.metadata_json["reason"] == occ.REASON_AMBIGUOUS_POSTPONED

    # The owner can still void it: the governed correction evaluates the rule
    # on the original date the owner names (and refuses a date the evidence contradicts).
    from app.bankroll import postponement_corrections as pc

    entry = pc.GovernedVoidCorrection(
        position_id=str(incident.position.id), expected_original_settlement_id=str(settlement.id),
        expected_previous_result="LOSS", expected_stake=STAKE, expected_profit_loss=-STAKE,
        owner_decision="TEST", mode=pc.MODE_APPLY)
    refused = pc.plan(session, entry, now=SETTLED_AT + timedelta(days=1))
    assert refused["refusal"] == pc.RULE_NOT_MET + ":" + occ.REASON_AMBIGUOUS_POSTPONED
    pinned = pc.plan(session, replace(entry, expected_original_et_date="2026-10-16"),
                     now=SETTLED_AT + timedelta(days=1))
    assert pinned["status"] == pc.READY and pinned["verdict"]["original"]["basis"] == occ.BASIS_OWNER_PINNED
    assert pinned["profit_loss_delta"] == 2.42


def test_unverifiable_dates_without_postponement_settle_legacy_with_a_receipt_only(session, settings):
    incident = build_incident(session, history="delayed", slate_date=date(2026, 10, 15))
    result = _settle(session, settings, datetime(2026, 10, 19, tzinfo=UTC))
    assert _only_settlement(session, incident.position).result == PositionResult.LOSS
    [receipt] = _payload(result, incident)["legacy_settled_unverified"]
    assert receipt["reason"] == occ.REASON_AMBIGUOUS and receipt["operating_alert"] is False
    assert _alerts(session) == []


# ----------------------------------------------------------- the incident --

def test_postponed_and_played_on_a_later_date_settles_void(session, settings):
    incident = build_incident(session)
    result = _settle(session, settings, SETTLED_AT)

    settlement = _only_settlement(session, incident.position)
    assert result["settled"] == 1
    assert settlement.result == PositionResult.VOID
    assert float(settlement.profit_loss) == 0.0
    assert float(settlement.bankroll_before) == float(settlement.bankroll_after) == 1000.0
    assert settlement.game_home_score is None and settlement.game_away_score is None
    # No close exists for an occurrence that was never played (the old path
    # recorded SUNDAY's +113 as Friday's close).
    assert settlement.closing_odds_snapshot_id is None and settlement.closing_line_value is None
    assert settlement.closing_american_odds is None and settlement.beat_close is None
    note = settlement.settlement_note
    assert note.startswith(occ.RULE_ID)
    for fragment in ("original ET date 2026-10-16", str(incident.postponed_observation.id),
                     str(incident.basis_observation.id), "played 2026-10-18 instead",
                     "stake returned, P&L 0"):
        assert fragment in note, fragment
    position = incident.position
    assert (position.status, position.result) == (PositionStatus.VOID, PositionResult.VOID)
    assert float(position.payout) == STAKE
    receipt = _payload(result, incident)["voided_not_played_on_original_date"][0]
    assert receipt["reason"] == occ.REASON_PLAYED_ELSEWHERE
    assert receipt["original"]["et_date"] == "2026-10-16"
    assert "legacy_settled_unverified" not in _payload(result, incident)
    service = ShadowExperimentService(session, settings)
    assert service.current_bankroll(incident.experiment) == pytest.approx(1000.0)
    ledger = service.ledger_view(incident.experiment)
    assert ledger["betting"]["voids"] == 1 and ledger["betting"]["losses"] == 0
    assert ledger["positions"][0]["result"] == "VOID" and ledger["positions"][0]["profit_loss"] == 0.0
    assert ledger["current"] == 1000.0

    # Idempotent: a rerun writes nothing more.
    assert _settle(session, settings, SETTLED_AT + timedelta(hours=1))["settled"] == 0
    assert _only_settlement(session, position) is settlement


def test_an_unplayed_original_date_voids_early_but_never_on_the_date_itself(session, settings):
    incident = build_incident(session, played=False, flapping=False)
    # MLB has re-listed the game for Sunday; it has not been played.
    gso(session, incident.game, available=datetime(2026, 10, 17, 4, 54, tzinfo=UTC), scheduled=SUNDAY_START,
        status="scheduled")
    incident.game.starts_at, incident.game.status = SUNDAY_START, GameStatus.SCHEDULED
    session.commit()
    end_of_friday = datetime(2026, 10, 17, 4, 0, tzinfo=UTC)          # midnight ET

    result = _settle(session, settings, datetime(2026, 10, 17, 2, 0, tzinfo=UTC))   # 22:00 ET Friday
    assert result["settled"] == 0 and incident.position.status == PositionStatus.OPEN
    result = _settle(session, settings, end_of_friday + occ.EARLY_VOID_GRACE - timedelta(minutes=1))
    assert result["settled"] == 0 and incident.position.status == PositionStatus.OPEN
    waiting = _payload(result, incident)["awaiting_occurrence"][0]
    assert waiting["reason"] == occ.REASON_AWAITING

    result = _settle(session, settings, end_of_friday + occ.EARLY_VOID_GRACE + timedelta(minutes=1))
    settlement = _only_settlement(session, incident.position)
    assert result["settled"] == 1 and settlement.result == PositionResult.VOID
    assert "not yet played (currently scheduled 2026-10-18)" in settlement.settlement_note
    # The early void returned the reserved stake.
    service = ShadowExperimentService(session, settings)
    assert service._available_bankroll(incident.experiment) == pytest.approx(1000.0)


# --------------------------------------------- moved, but played that date --

@pytest.mark.parametrize("history", ["delayed", "same_day", "suspended"])
def test_a_game_played_on_its_original_date_settles_win_or_loss(session, settings, history):
    incident = build_incident(session, history=history)
    verdict = occ.occurrence_verdict(session, position=incident.position, decision=incident.decision,
                                     game=incident.game, now=datetime(2026, 10, 19, tzinfo=UTC))
    assert verdict.kind == occ.SETTLE and not verdict.unverified, verdict.as_dict()
    result = _settle(session, settings, datetime(2026, 10, 19, tzinfo=UTC))
    settlement = _only_settlement(session, incident.position)
    assert settlement.result == PositionResult.LOSS                      # TB 3, CLE 1
    assert float(settlement.profit_loss) == -STAKE
    assert settlement.settlement_note == "Settled deterministically from canonical final score"
    assert set(_payload(result, incident)) == {"settled", "current_bankroll"}
    assert _alerts(session) == []


def test_a_final_on_another_date_without_postponement_evidence_settles_legacy_and_alerts(session, settings):
    """Review MEDIUM-4: no open-ended hold -- legacy settlement, a receipt and one alert."""

    incident = build_incident(session, history="silent_move")
    result = _settle(session, settings, datetime(2026, 10, 19, 3, tzinfo=UTC))
    assert result["settled"] == 1
    settlement = _only_settlement(session, incident.position)
    assert settlement.result == PositionResult.LOSS and incident.position.status == PositionStatus.SETTLED
    assert occ.REASON_FINAL_WITHOUT_EVIDENCE in settlement.settlement_note
    [receipt] = _payload(result, incident)["legacy_settled_unverified"]
    assert receipt["reason"] == occ.REASON_FINAL_WITHOUT_EVIDENCE and receipt["played_et_date"] == "2026-10-18"
    assert receipt["operating_alert"] is True
    assert len(_alerts(session)) == 1
    # The alert is written once per position, never repeated.
    assert occ.record_review_alert(session, position=incident.position, game=incident.game,
                                   verdict=occ.occurrence_verdict(session, position=incident.position,
                                                                  decision=incident.decision, game=incident.game,
                                                                  now=SETTLED_AT),
                                   now=SETTLED_AT) is False
    assert len(_alerts(session)) == 1


def test_a_cancelled_game_still_voids_on_the_existing_path(session, settings):
    incident = build_incident(session, played=False)
    incident.game.status = GameStatus.CANCELED
    session.commit()
    _settle(session, settings, datetime(2026, 10, 17, 2, tzinfo=UTC))
    settlement = _only_settlement(session, incident.position)
    assert settlement.result == PositionResult.VOID and float(settlement.profit_loss) == 0.0
    assert settlement.settlement_note == "CANCELLED — virtual stake voided"
    assert incident.position.status == PositionStatus.VOID


def test_a_game_with_no_schedule_history_keeps_legacy_settlement(session, settings):
    incident = build_incident(session)
    for row in session.scalars(select(GameStateObservation)):
        session.expunge(row)
    session.execute(text("DELETE FROM game_state_observations"))
    session.commit()
    result = _settle(session, settings, SETTLED_AT)
    assert _only_settlement(session, incident.position).result == PositionResult.LOSS
    assert set(_payload(result, incident)) == {"settled", "current_bankroll"}


def test_other_sports_keep_their_own_rules_for_a_moved_game(session, settings):
    """The rule is MLB's. An NFL-sport experiment holding the same outcome settles as before."""

    incident = build_incident(session)
    nfl = mlb_experiment(session, version=2, sport="NFL", policy="TEST_NFL_PAPER_POLICY", era=None)
    decision = ShadowExperimentDecision(**{
        column.key: getattr(incident.decision, column.key)
        for column in ShadowExperimentDecision.__mapper__.column_attrs
        if column.key not in ("id", "created_at", "experiment_id", "evidence_fingerprint")
    }, experiment_id=nfl.id, evidence_fingerprint=uuid4().hex)
    session.add(decision)
    session.flush()
    position = OfficialAspPosition(
        market_outcome_id=incident.home_outcome.id, odds_snapshot_id=incident.friday_snapshot.id,
        placed_at=PLACED_AT, stake=STAKE, decimal_odds=2.01, result=PositionResult.PENDING,
        status=PositionStatus.OPEN, strategy=nfl.staking_policy_version, shadow_experiment_id=nfl.id,
        shadow_decision_id=decision.id)
    session.add(position)
    session.commit()

    _settle(session, settings, SETTLED_AT)
    assert _only_settlement(session, incident.position).result == PositionResult.VOID
    assert _only_settlement(session, position).result == PositionResult.LOSS


def test_a_score_correction_never_turns_a_postponement_void_back_into_a_result(session, settings):
    incident = build_incident(session)
    _settle(session, settings, SETTLED_AT)
    final = session.scalar(select(GameStateObservation).where(GameStateObservation.status == "final"))
    assert correct_settled_positions(session, incident.game, observation_id=final.id, now=SETTLED_AT) == []
    assert session.scalar(select(PositionSettlementAdjustment.id)) is None


def test_a_cancellation_void_is_still_corrected_exactly_as_before(session):
    """Review LOW-6: the VOID skip is scoped to the postponement rule; CFB/NBA keep their behaviour."""

    incident = build_incident(session)        # game FINAL 1-3 on the row; position OPEN
    position = incident.position
    session.add(PositionSettlement(
        position_id=position.id, result=PositionResult.VOID, settled_at=SETTLED_AT, profit_loss=0.0,
        settlement_note="CANCELLED — virtual stake voided", bankroll_before=1000.0, bankroll_after=1000.0))
    session.flush()
    final = session.scalar(select(GameStateObservation).where(GameStateObservation.status == "final"))
    [corrected] = correct_settled_positions(session, incident.game, observation_id=final.id, now=SETTLED_AT)
    assert (corrected["previous"], corrected["corrected"]) == ("VOID", "LOSS")
    session.rollback()


# ------------------------------------------------- the duplicate guard ----

def _flagged(settings, on=True):
    return settings.model_copy(update={"mlb_rescheduled_occurrence_positions_enabled": on})


def _sunday(incident, decision_id=None):
    return SimpleNamespace(id=decision_id or uuid4(), market_outcome_id=incident.home_outcome.id)


def _void_friday(session, settings, incident):
    incident.game.starts_at, incident.game.status = SUNDAY_START, GameStatus.SCHEDULED
    incident.game.home_score = incident.game.away_score = None
    session.commit()
    _settle(session, settings, datetime(2026, 10, 17, 8, tzinfo=UTC))      # 04:00 ET Saturday: early void
    assert incident.position.status == PositionStatus.VOID


def test_guard_never_writes_and_an_open_prior_always_blocks(session, settings):
    """Review MEDIUM-5 / F6 / F7: no inline void; finalize-games owns voiding."""

    incident = build_incident(session, played=False)
    incident.game.starts_at, incident.game.status = SUNDAY_START, GameStatus.SCHEDULED
    session.commit()
    service = ShadowExperimentService(session, _flagged(settings))
    assert service._prior_position_blocks(incident.experiment, _sunday(incident), date(2026, 10, 18),
                                          incident.game) is True
    assert incident.position.status == PositionStatus.OPEN
    assert _only_settlement(session, incident.position) is None


def test_guard_a_void_of_another_occurrence_stops_blocking_only_once_the_index_exists(
        session, settings, monkeypatch):
    incident = build_incident(session, played=False)
    _void_friday(session, settings, incident)
    service = ShadowExperimentService(session, _flagged(settings))
    # The catalog read reports exactly the schema the database has.
    assert ses.live_outcome_index_present(session) is MODEL_HAS_LIVE_INDEX
    # Without the index (the full unique constraint still in place) the guard
    # keeps blocking: turning the flag on before the migration is inert.
    monkeypatch.setattr(ses, "live_outcome_index_present", lambda session: False)
    assert service._prior_position_blocks(incident.experiment, _sunday(incident), date(2026, 10, 18),
                                          incident.game) is True
    monkeypatch.setattr(ses, "live_outcome_index_present", lambda session: True)
    assert service._prior_position_blocks(incident.experiment, _sunday(incident), date(2026, 10, 18),
                                          incident.game) is False


def test_guard_a_void_on_the_same_occurrence_or_decision_still_blocks(session, settings, monkeypatch):
    """Review F2 / HIGH-3: a cancellation VOID that flips back must not re-open the outcome."""

    monkeypatch.setattr(ses, "live_outcome_index_present", lambda session: True)
    incident = build_incident(session, played=False)
    _void_friday(session, settings, incident)
    service = ShadowExperimentService(session, _flagged(settings))
    friday = date(2026, 10, 16)
    # The very decision that funded the VOID position.
    assert service._prior_position_blocks(incident.experiment, _sunday(incident, incident.decision.id), friday,
                                          incident.game) is True
    # Another decision for the same occurrence (Friday).
    assert service._prior_position_blocks(incident.experiment, _sunday(incident), friday, incident.game) is True


def test_guard_blocks_behind_a_settled_win_or_loss_kept_as_action(session, settings, monkeypatch):
    monkeypatch.setattr(ses, "live_outcome_index_present", lambda session: True)
    incident = build_incident(session, settled=True)      # the owner may keep Friday's LOSS as action
    service = ShadowExperimentService(session, _flagged(settings))
    assert service._prior_position_blocks(incident.experiment, _sunday(incident), date(2026, 10, 18),
                                          incident.game) is True


# ----------------------------------------- end to end through freeze ------

async def _rainout(session, settings, monkeypatch, *, capture_only=False):
    from tests.test_shadow_experiment import _add_home_value, _frozen_primary, _live_slate, _opportunities, _start

    if capture_only:
        games, event_date, now, start = await _live_slate(session, settings)
        evidence = _add_home_value(session, games[0], observed_at=now)
        _opportunities(monkeypatch, {games[0].id: evidence})
        service = ShadowExperimentService(session, settings)
        _start(service, event_date, now)
        service.freeze(event_date, phase=ExperimentDecisionPhase.FINAL_PREGAME,
                       now=start - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES + 10))
        game, position = games[0], None
    else:
        service, game, position, _decision, event_date, start, _snapshot = await _frozen_primary(
            session, settings, monkeypatch)
        assert position is not None
    start = start if start.tzinfo else start.replace(tzinfo=UTC)
    gso(session, game, available=start - timedelta(minutes=90), scheduled=start, status="scheduled")
    gso(session, game, available=start + timedelta(minutes=20), scheduled=start, status="postponed")
    makeup = start + timedelta(days=2)
    gso(session, game, available=start + timedelta(hours=5), scheduled=makeup, status="scheduled")
    game.starts_at, game.status = makeup, GameStatus.SCHEDULED
    session.commit()
    return game, position, makeup


async def _makeup_freeze(session, settings, monkeypatch, game, makeup, *, capture=True, also=None):
    from tests.test_shadow_experiment import _add_home_value, _opportunities

    evidence = _add_home_value(session, game, observed_at=makeup - timedelta(minutes=40), american=+250)
    by_game = {game.id: evidence}
    if also is not None:
        by_game[also.id] = _add_home_value(session, also, observed_at=makeup - timedelta(minutes=40), american=+250)
    _opportunities(monkeypatch, by_game)
    service = ShadowExperimentService(session, settings)
    makeup_date = makeup.astimezone(ET).date()
    if capture:
        service.freeze(makeup_date, phase=ExperimentDecisionPhase.FINAL_PREGAME,
                       now=makeup - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES + 10))
    return service.freeze(makeup_date, phase=ExperimentDecisionPhase.FINAL_PREGAME,
                          now=makeup - timedelta(minutes=10)), by_game


def _positions_on(session, outcome_id):
    return list(session.scalars(select(OfficialAspPosition).where(
        OfficialAspPosition.market_outcome_id == outcome_id).order_by(OfficialAspPosition.placed_at)))


def _spy_guard(monkeypatch):
    calls: list[bool] = []
    original = ShadowExperimentService._prior_position_blocks

    def spy(self, *args, **kwargs):
        answer = original(self, *args, **kwargs)
        calls.append(answer)
        return answer

    monkeypatch.setattr(ShadowExperimentService, "_prior_position_blocks", spy)
    return calls


def _receipts(session, decision_ids):
    return [row.metadata_json for row in session.scalars(select(AuditEvent).where(
        AuditEvent.action == ses.FUNDING_RECEIPT_ACTION, AuditEvent.entity_id.in_(decision_ids)))]


@pytest.mark.asyncio
async def test_flag_on_without_the_migration_the_makeup_is_refused_cleanly(session, settings, monkeypatch):
    """Review HIGH-2 (a): the flag on before 20261019_0055 changes nothing and raises nothing."""

    monkeypatch.setattr(ses, "live_outcome_index_present", lambda session: False)   # no migration yet
    game, friday, makeup = await _rainout(session, settings, monkeypatch)
    _settle(session, settings, makeup - timedelta(hours=6))
    assert friday.status == PositionStatus.VOID

    calls = _spy_guard(monkeypatch)
    positioned, _ = await _makeup_freeze(session, _flagged(settings), monkeypatch, game, makeup)
    assert positioned["primary"] == 0
    assert calls and all(calls), "the guard was consulted and blocked"
    assert [row.status for row in _positions_on(session, friday.market_outcome_id)] == [PositionStatus.VOID]


@pytest.mark.asyncio
async def test_a_uniqueness_refusal_is_one_ineligible_game_never_a_rolled_back_slate(
        session, settings, monkeypatch):
    """Review HIGH-2 (b): the INSERT's own SAVEPOINT keeps every other game's outcome."""

    game, friday, makeup = await _rainout(session, settings, monkeypatch)
    other = next(row for row in session.scalars(select(type(game)).where(type(game).id != game.id)))
    other.starts_at, other.status = makeup, GameStatus.SCHEDULED
    session.commit()
    assert friday.status == PositionStatus.OPEN       # finalize-games has not voided it
    # A guard that wrongly lets the makeup through: the database's backstop
    # (one non-VOID position per experiment and outcome, under either schema)
    # refuses the INSERT, and only that INSERT is undone.
    monkeypatch.setattr(ShadowExperimentService, "_prior_position_blocks", lambda self, *a, **k: False)

    positioned, evidence = await _makeup_freeze(session, _flagged(settings), monkeypatch, game, makeup, also=other)
    assert positioned["primary"] == 1                                    # the other game
    assert [row.status for row in _positions_on(session, friday.market_outcome_id)] == [PositionStatus.OPEN]
    assert len(_positions_on(session, evidence[other.id][0].id)) == 1
    makeup_decisions = [row.id for row in session.scalars(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.game_id == game.id,
        ShadowExperimentDecision.slate_date == makeup.astimezone(ET).date()))]
    assert makeup_decisions, "the makeup's own decisions survive the refused INSERT"
    reasons = {(receipt["disposition"], receipt["reason"]) for receipt in _receipts(session, makeup_decisions)}
    assert (ses.POSITION_INELIGIBLE, ses.POSITION_WRITE_CONFLICT) in reasons
    session.commit()                                                     # the transaction is healthy


@pytest.mark.asyncio
async def test_a_cancellation_void_that_flips_back_is_never_re_funded(session, settings, monkeypatch):
    """Review F2 / HIGH-3: cancel after the position, settle, re-list, freeze again."""

    monkeypatch.setattr(ses, "live_outcome_index_present", lambda session: True)
    from tests.test_shadow_experiment import _frozen_primary

    service, game, position, decision, event_date, start, _snapshot = await _frozen_primary(
        session, _flagged(settings), monkeypatch)
    assert position is not None
    game.status = GameStatus.CANCELED
    session.commit()
    _settle(session, settings, start - timedelta(minutes=8))
    assert position.status == PositionStatus.VOID
    game.status = GameStatus.SCHEDULED
    session.commit()
    calls = _spy_guard(monkeypatch)
    result = ShadowExperimentService(session, _flagged(settings)).freeze(
        event_date, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=start - timedelta(minutes=5))
    assert result["primary"] == 0
    assert calls == [True], "the guard was reached and blocked the same decision"
    assert [row.status for row in _positions_on(session, position.market_outcome_id)] == [PositionStatus.VOID]


@pytest.mark.asyncio
async def test_flag_off_the_guard_is_exactly_as_before(session, settings, monkeypatch):
    game, friday, makeup = await _rainout(session, settings, monkeypatch)
    _settle(session, settings, makeup - timedelta(hours=6))
    positioned, _ = await _makeup_freeze(session, _flagged(settings, on=False), monkeypatch, game, makeup)
    assert positioned["primary"] == 0
    assert len(_positions_on(session, friday.market_outcome_id)) == 1


@pytest.mark.asyncio
async def test_the_original_dates_decision_never_funds_the_makeup(session, settings, monkeypatch):
    """Friday captured a QUALIFIED decision but no position; Sunday has no decision of its own."""

    game, _none, makeup = await _rainout(session, settings, monkeypatch, capture_only=True)
    friday_decisions = list(session.scalars(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.game_id == game.id,
        ShadowExperimentDecision.recommendation == "QUALIFIED_BET")))
    assert friday_decisions, "the Friday decision must exist for this test to mean anything"
    service = ShadowExperimentService(session, _flagged(settings))
    result = service.freeze(makeup.astimezone(ET).date(), phase=ExperimentDecisionPhase.FINAL_PREGAME,
                            now=makeup - timedelta(minutes=10))
    assert result["primary"] == 0
    assert list(session.scalars(select(OfficialAspPosition))) == []


# ------------------------------------------- the shared priority reservation --

def test_reservation_ignores_another_occurrences_qualified_decision(session, settings):
    """Review LOW-8: only a decision for THIS occurrence can be funded, so only it holds room."""

    from app.services.shared_priority_reservation import _mlb_pending

    incident = build_incident(session, played=False)
    incident.game.starts_at, incident.game.status = SUNDAY_START, GameStatus.SCHEDULED
    session.commit()
    session.execute(text("DELETE FROM official_asp_positions"))           # Friday captured, never funded
    session.commit()
    games = [(incident.game.id, SUNDAY_START, "MLB", "LIVE")]
    moment = SUNDAY_START - timedelta(minutes=10)                          # after the makeup's cutoff
    assert _mlb_pending(session, settings, games, moment) == []            # Friday's QUALIFIED holds nothing
    assert session.scalar(select(func.count()).select_from(ShadowExperimentDecision)) == 1
