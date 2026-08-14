from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.gen2.participants import (
    LineupEvidenceKind,
    LineupObservation,
    ParticipationError,
    ParticipationPopulation,
    ParticipantTemporalSemantics,
    ParticipationTarget,
    RosterObservation,
    RosterStatus,
    StarterEvidenceKind,
    StarterObservation,
    build_participation_snapshot,
)


FIRST_PITCH = datetime(2025, 7, 1, 23, tzinfo=UTC)
CUTOFF = FIRST_PITCH - timedelta(minutes=10)
TARGET = ParticipationTarget("game-1", "team-1", date(2025, 7, 1), FIRST_PITCH, CUTOFF)


def _roster(player: str, position: str, *, team: str = "team-1") -> RosterObservation:
    return RosterObservation(
        observation_id=f"roster-{team}-{player}",
        player_id=player,
        team_id=team,
        status=RosterStatus.ACTIVE,
        defensive_positions=(position.lower(),),
        observed_at=CUTOFF - timedelta(hours=2),
        eligible_at=CUTOFF - timedelta(hours=2),
        source_version_id="roster-v1",
        source_record_id=f"record-{team}-{player}",
        evidence_checksum="1" * 64,
    )


def _lineup(
    slot: int,
    player: str,
    *,
    kind: LineupEvidenceKind = LineupEvidenceKind.CONFIRMED,
    observed_at: datetime | None = None,
    replaces: str | None = None,
    scratched: str | None = None,
    game: str = "game-1",
) -> LineupObservation:
    return LineupObservation(
        observation_id=f"lineup-{game}-{slot}-{player}",
        game_id=game,
        team_id="team-1",
        player_id=player,
        batting_order=slot,
        defensive_position=f"P{slot}",
        kind=kind,
        observed_at=observed_at or CUTOFF - timedelta(minutes=5),
        eligible_at=observed_at or CUTOFF - timedelta(minutes=5),
        source_version_id="lineup-v1",
        source_record_id=f"lineup-record-{game}-{slot}-{player}",
        evidence_checksum=f"{slot + 10:064x}"[-64:],
        replaces_player_id=replaces,
        scratched_player_id=scratched,
        temporal_semantics=(
            ParticipantTemporalSemantics.RETROSPECTIVE_ACTUAL_TIMING_RISK
            if kind is LineupEvidenceKind.HISTORICAL_ACTUAL
            else ParticipantTemporalSemantics.POINT_IN_TIME_LIVE
        ),
    )


def _starter(
    *,
    kind: StarterEvidenceKind = StarterEvidenceKind.PROBABLE,
    observed_at: datetime | None = None,
) -> StarterObservation:
    return StarterObservation(
        observation_id=f"starter-{kind.value}",
        game_id="game-1",
        team_id="team-1",
        player_id="starter",
        kind=kind,
        observed_at=observed_at or CUTOFF - timedelta(hours=1),
        eligible_at=observed_at or CUTOFF - timedelta(hours=1),
        source_version_id="starter-v1",
        source_record_id=f"starter-record-{kind.value}",
        evidence_checksum="2" * 64,
        temporal_semantics=(
            ParticipantTemporalSemantics.RETROSPECTIVE_ACTUAL_TIMING_RISK
            if kind is StarterEvidenceKind.HISTORICAL_ACTUAL
            else ParticipantTemporalSemantics.POINT_IN_TIME_LIVE
        ),
    )


def _roster_rows(extra: tuple[RosterObservation, ...] = ()) -> tuple[RosterObservation, ...]:
    return tuple(_roster(f"b{i}", f"P{i}") for i in range(1, 10)) + (
        _roster("starter", "SP"),
    ) + extra


def test_complete_strict_snapshot_is_training_eligible_and_position_bound() -> None:
    snapshot = build_participation_snapshot(
        TARGET,
        roster_observations=_roster_rows(),
        lineup_observations=tuple(_lineup(i, f"b{i}") for i in range(1, 10)),
        starter_observations=(_starter(),),
        population=ParticipationPopulation.STRICT_PREGAME,
    )
    assert snapshot.complete_lineup
    assert snapshot.starter_available
    assert snapshot.training_eligible
    assert snapshot.research_comparison_eligible
    assert snapshot.evidence_tier == "A"
    assert snapshot.temporal_risk is None
    assert [entry.batting_order for entry in snapshot.lineup] == list(range(1, 10))
    assert snapshot.lineup[0].defensive_position == "P1"
    assert len(snapshot.checksum) == 64


def test_scratch_and_replacement_are_explicit_and_latest_slot_wins() -> None:
    original = _lineup(2, "b2", observed_at=CUTOFF - timedelta(minutes=8))
    replacement = _lineup(
        2,
        "b10",
        observed_at=CUTOFF - timedelta(minutes=2),
        replaces="b2",
        scratched="b2",
    )
    lineups = tuple(_lineup(i, f"b{i}") for i in range(1, 10) if i != 2) + (
        original,
        replacement,
    )
    snapshot = build_participation_snapshot(
        TARGET,
        roster_observations=_roster_rows((_roster("b10", "P2"),)),
        lineup_observations=lineups,
        starter_observations=(_starter(),),
        population=ParticipationPopulation.STRICT_PREGAME,
    )
    assert snapshot.lineup[1].player_id == "b10"
    assert snapshot.scratch_player_ids == ("b2",)
    assert snapshot.replacement_player_ids == ("b10",)


def test_historical_actual_population_is_tier_b_and_never_training_eligible() -> None:
    after_final = FIRST_PITCH + timedelta(hours=4)
    actual_lineup = tuple(
        _lineup(
            i,
            f"b{i}",
            kind=LineupEvidenceKind.HISTORICAL_ACTUAL,
            observed_at=after_final,
        )
        for i in range(1, 10)
    )
    actual_starter = _starter(
        kind=StarterEvidenceKind.HISTORICAL_ACTUAL,
        observed_at=after_final,
    )
    actual_roster = tuple(
        replace(
            row,
            observed_at=after_final,
            eligible_at=after_final,
            game_id="game-1",
            official_date=TARGET.official_date,
            temporal_semantics=ParticipantTemporalSemantics.RETROSPECTIVE_ACTUAL_TIMING_RISK,
        )
        for row in _roster_rows()
    )
    strict = build_participation_snapshot(
        TARGET,
        roster_observations=actual_roster,
        lineup_observations=actual_lineup,
        starter_observations=(actual_starter,),
        population=ParticipationPopulation.STRICT_PREGAME,
    )
    assert not strict.complete_lineup and not strict.starter_available
    enhanced = build_participation_snapshot(
        TARGET,
        roster_observations=actual_roster,
        lineup_observations=actual_lineup,
        starter_observations=(actual_starter,),
        population=ParticipationPopulation.ACTUAL_LINEUP_TIMING_RISK,
    )
    assert enhanced.complete_lineup and enhanced.starter_available
    assert enhanced.evidence_tier == "B"
    assert not enhanced.training_eligible
    assert enhanced.research_comparison_eligible
    assert enhanced.temporal_risk == "HISTORICAL_ACTUAL_LINEUP_TIMING_RISK"


def test_team_and_game_scope_prevent_trade_and_doubleheader_leakage() -> None:
    wrong_team = _roster("b1", "P1", team="old-team")
    other_game = _lineup(1, "b1", game="game-2")
    snapshot = build_participation_snapshot(
        TARGET,
        roster_observations=_roster_rows((wrong_team,)),
        lineup_observations=(other_game,) + tuple(_lineup(i, f"b{i}") for i in range(1, 10)),
        starter_observations=(_starter(),),
        population=ParticipationPopulation.STRICT_PREGAME,
    )
    assert "roster-old-team-b1" not in snapshot.evidence_observation_ids
    assert all("game-2" not in identity for identity in snapshot.evidence_observation_ids)


def test_inactive_or_ambiguous_participant_evidence_fails_closed() -> None:
    inactive = replace(_roster("b1", "P1"), status=RosterStatus.INJURED)
    rosters = (inactive,) + tuple(row for row in _roster_rows() if row.player_id != "b1")
    with pytest.raises(ParticipationError, match="not active"):
        build_participation_snapshot(
            TARGET,
            roster_observations=rosters,
            lineup_observations=tuple(_lineup(i, f"b{i}") for i in range(1, 10)),
            starter_observations=(_starter(),),
            population=ParticipationPopulation.STRICT_PREGAME,
        )


def test_actual_roster_must_bind_the_exact_target_game_and_snapshot_checksum_is_complete() -> None:
    after_final = FIRST_PITCH + timedelta(hours=4)
    actual_lineup = tuple(
        _lineup(
            index,
            f"b{index}",
            kind=LineupEvidenceKind.HISTORICAL_ACTUAL,
            observed_at=after_final,
        )
        for index in range(1, 10)
    )
    actual_starter = _starter(
        kind=StarterEvidenceKind.HISTORICAL_ACTUAL,
        observed_at=after_final,
    )
    wrong_game_roster = tuple(
        replace(
            row,
            observed_at=after_final,
            eligible_at=after_final,
            game_id="different-game",
            official_date=TARGET.official_date,
            temporal_semantics=ParticipantTemporalSemantics.RETROSPECTIVE_ACTUAL_TIMING_RISK,
        )
        for row in _roster_rows()
    )
    with pytest.raises(ParticipationError, match="lacks governed team roster evidence"):
        build_participation_snapshot(
            TARGET,
            roster_observations=wrong_game_roster,
            lineup_observations=actual_lineup,
            starter_observations=(actual_starter,),
            population=ParticipationPopulation.ACTUAL_LINEUP_TIMING_RISK,
        )

    correct_roster = tuple(
        replace(row, game_id="game-1") for row in wrong_game_roster
    )
    first = build_participation_snapshot(
        TARGET,
        roster_observations=correct_roster,
        lineup_observations=actual_lineup,
        starter_observations=(actual_starter,),
        population=ParticipationPopulation.ACTUAL_LINEUP_TIMING_RISK,
    )
    changed_roster = tuple(
        replace(row, defensive_positions=("CHANGED",)) if row.player_id == "b1" else row
        for row in correct_roster
    )
    with pytest.raises(ParticipationError, match="conflicts with roster evidence"):
        build_participation_snapshot(
            TARGET,
            roster_observations=changed_roster,
            lineup_observations=actual_lineup,
            starter_observations=(actual_starter,),
            population=ParticipationPopulation.ACTUAL_LINEUP_TIMING_RISK,
        )
    changed_status = tuple(
        replace(row, evidence_checksum="9" * 64) if row.player_id == "b1" else row
        for row in correct_roster
    )
    second = build_participation_snapshot(
        TARGET,
        roster_observations=changed_status,
        lineup_observations=actual_lineup,
        starter_observations=(actual_starter,),
        population=ParticipationPopulation.ACTUAL_LINEUP_TIMING_RISK,
    )
    assert first.checksum != second.checksum
    first = _lineup(1, "b1")
    ambiguous = replace(first, observation_id="ambiguous", player_id="b9")
    with pytest.raises(ParticipationError, match="ambiguous simultaneous"):
        build_participation_snapshot(
            TARGET,
            roster_observations=_roster_rows(),
            lineup_observations=(first, ambiguous),
            starter_observations=(_starter(),),
            population=ParticipationPopulation.STRICT_PREGAME,
        )
