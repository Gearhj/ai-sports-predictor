from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.gen2.usage import (
    AvailabilityBasis,
    AvailabilityObservation,
    AvailabilityStatus,
    BullpenRole,
    PitchingAppearance,
    PitchingRole,
    RelieverProfile,
    StarterHazardExample,
    StarterUsageContext,
    UsageError,
    derive_reliever_availability,
    fit_starter_exit_hazard,
    predict_bullpen_usage,
    predict_starter_duration,
)


CUTOFF = datetime(2025, 7, 10, 18, tzinfo=UTC)
TARGET_DATE = date(2025, 7, 10)


def _profile(
    player_id: str,
    role: BullpenRole,
    talent: float,
    reliability: float,
    *,
    checksum_digit: str,
    evidence_cutoff: datetime | None = None,
) -> RelieverProfile:
    return RelieverProfile(
        player_id=player_id,
        team_id="T",
        role=role,
        talent_score=talent,
        reliability=reliability,
        evidence_cutoff=evidence_cutoff or CUTOFF - timedelta(hours=1),
        evidence_checksum=checksum_digit * 64,
        source_evidence_ids=(f"profile-source-{player_id}",),
    )


def _appearance(
    identity: str,
    player: str,
    days_ago: int,
    pitches: int,
    *,
    game: str | None = None,
    team: str = "T",
    role: PitchingRole = PitchingRole.RELIEVER,
    eligible_shift: timedelta = timedelta(),
) -> PitchingAppearance:
    completed = CUTOFF - timedelta(days=days_ago, hours=2)
    return PitchingAppearance(
        appearance_id=identity,
        game_id=game or f"g-{identity}",
        player_id=player,
        team_id=team,
        role=role,
        official_date=TARGET_DATE - timedelta(days=days_ago),
        completed_at=completed,
        eligible_at=completed + eligible_shift,
        pitches=pitches,
        batters_faced=max(1, pitches // 4),
        outs_recorded=max(0, pitches // 6),
    )


def test_workload_proxy_excludes_current_future_late_and_old_team_evidence() -> None:
    rows = (
        _appearance("d1", "p", 1, 25),
        _appearance("d2", "p", 2, 20),
        _appearance("target", "p", 1, 100, game="target"),
        _appearance("old-team", "p", 1, 100, team="OLD"),
        _appearance("starter", "p", 1, 100, role=PitchingRole.STARTER),
        _appearance("late", "p", 1, 100, eligible_shift=timedelta(days=2)),
    )
    result = derive_reliever_availability(
        rows,
        (),
        player_id="p",
        team_id="T",
        current_game_id="target",
        cutoff=CUTOFF,
        target_official_date=TARGET_DATE,
    )
    assert result.basis is AvailabilityBasis.WORKLOAD_DERIVED_AVAILABILITY_PROXY
    assert result.pitches_1d == 25
    assert result.pitches_2d == 45
    assert result.pitches_3d == 45
    assert result.consecutive_days == 2
    assert result.contributing_appearance_ids == ("d2", "d1")
    assert 0.05 <= result.availability_probability < 0.5


def test_observed_availability_is_not_laundered_into_workload_proxy() -> None:
    observed = AvailabilityObservation(
        observation_id="obs",
        player_id="p",
        team_id="T",
        status=AvailabilityStatus.UNAVAILABLE,
        observed_at=CUTOFF - timedelta(minutes=10),
        available_as_of=CUTOFF - timedelta(minutes=5),
        source="TEAM_REPORT",
        applicable_game_id="target",
        effective_through=CUTOFF + timedelta(hours=2),
        evidence_checksum="6" * 64,
    )
    result = derive_reliever_availability(
        (),
        (observed,),
        player_id="p",
        team_id="T",
        current_game_id="target",
        cutoff=CUTOFF,
        target_official_date=TARGET_DATE,
    )
    assert result.basis is AvailabilityBasis.OBSERVED_UNAVAILABLE
    assert result.availability_probability == 0
    assert result.observation_id == "obs"


def test_bullpen_pool_excludes_unavailable_and_normalizes_role_weights() -> None:
    profiles = (
        _profile("closer", BullpenRole.CLOSER, 0.8, 0.9, checksum_digit="1"),
        _profile("middle", BullpenRole.MIDDLE, 0.5, 0.7, checksum_digit="2"),
        _profile("out", BullpenRole.SETUP, 0.9, 0.9, checksum_digit="3"),
    )
    snapshots = {
        player: derive_reliever_availability(
            (),
            (
                AvailabilityObservation(
                    f"obs-{player}",
                    player,
                    "T",
                    status,
                    CUTOFF - timedelta(minutes=10),
                        CUTOFF - timedelta(minutes=5),
                        "TEAM_REPORT",
                        "target",
                        CUTOFF + timedelta(hours=2),
                        ("7" if player == "closer" else "8" if player == "middle" else "9")
                        * 64,
                ),
            ),
            player_id=player,
            team_id="T",
            current_game_id="target",
            cutoff=CUTOFF,
            target_official_date=TARGET_DATE,
        )
        for player, status in (
            ("closer", AvailabilityStatus.AVAILABLE),
            ("middle", AvailabilityStatus.AVAILABLE),
            ("out", AvailabilityStatus.UNAVAILABLE),
        )
    }
    prediction = predict_bullpen_usage(
        profiles,
        snapshots,
        team_id="T",
        inning=9,
        score_differential=1,
        cutoff=CUTOFF,
        target_official_date=TARGET_DATE,
    )
    assert tuple(row.player_id for row in prediction.entries) == ("closer", "middle")
    assert sum(row.appearance_probability for row in prediction.entries) == pytest.approx(1)
    assert prediction.entries[0].appearance_probability > prediction.entries[1].appearance_probability


def _hazard_examples() -> tuple[StarterHazardExample, ...]:
    rows = []
    for game_index in range(8):
        for opportunity in range(1, 6):
            evidence_end = CUTOFF - timedelta(days=10 - game_index, minutes=6 - opportunity)
            rows.append(
                StarterHazardExample(
                    example_id=f"e-{game_index}-{opportunity}",
                    game_id=f"g-{game_index}",
                    starter_id=f"s-{game_index % 2}",
                    evidence_end_at=evidence_end,
                    eligible_at=evidence_end,
                    inning=max(1, opportunity),
                    batters_faced_so_far=opportunity * 4,
                    outs_recorded_so_far=opportunity * 3,
                    pitches_so_far=opportunity * 16,
                    days_rest=4,
                    prior_7d_pitches=80 if game_index % 2 else 20,
                    times_through_order=opportunity / 3,
                    runs_allowed=opportunity // 2,
                    exited_after_opportunity=opportunity == 5 or (game_index % 3 == 0 and opportunity == 4),
                    source_evidence_ids=(f"source-{game_index}-{opportunity}",),
                    evidence_checksum=f"{game_index * 10 + opportunity:064x}"[-64:],
                )
            )
    return tuple(rows)


def test_starter_hazard_is_deterministic_and_excludes_current_game() -> None:
    rows = _hazard_examples()
    first = fit_starter_exit_hazard(rows, cutoff=CUTOFF, current_game_id="g-7")
    changed_current = tuple(
        row
        if row.game_id != "g-7"
        else StarterHazardExample(**{**row.__dict__, "runs_allowed": 99})
        for row in rows
    )
    second = fit_starter_exit_hazard(changed_current, cutoff=CUTOFF, current_game_id="g-7")
    assert first.training_checksum == second.training_checksum
    assert first.coefficients == second.coefficients
    assert all(not identity.startswith("e-7-") for identity in first.training_example_ids)
    changed_time = (
        StarterHazardExample(
            **{
                **rows[0].__dict__,
                "evidence_end_at": rows[0].evidence_end_at - timedelta(seconds=1),
                "eligible_at": rows[0].eligible_at - timedelta(seconds=1),
            }
        ),
    ) + rows[1:]
    assert (
        fit_starter_exit_hazard(changed_time, cutoff=CUTOFF, current_game_id="g-7").training_checksum
        != first.training_checksum
    )


def test_starter_duration_distribution_sums_to_one_and_reports_inning_reach() -> None:
    model = fit_starter_exit_hazard(
        _hazard_examples(), cutoff=CUTOFF, current_game_id="target-game"
    )
    contexts = tuple(
        StarterUsageContext(
            inning=5 + index // 3,
            batters_faced_so_far=20 + index,
            outs_recorded_so_far=12 + index,
            pitches_so_far=75 + index * 4,
            days_rest=4,
            prior_7d_pitches=15,
            times_through_order=2.0 + index / 9,
            runs_allowed=2,
        )
        for index in range(8)
    )
    result = predict_starter_duration(model, contexts)
    assert sum(value for _, value in result.exit_after_batter_probabilities) == pytest.approx(1)
    assert tuple(inning for inning, _ in result.reach_inning_probabilities) == (5, 6, 7)
    assert 20 <= result.expected_batters_faced <= 27
    assert 12 <= result.expected_outs <= 19


def test_usage_rejects_naive_timestamps_and_missing_availability() -> None:
    with pytest.raises(UsageError, match="timezone-aware"):
        _appearance("x", "p", 1, 10).__class__(
            **{
                **_appearance("x", "p", 1, 10).__dict__,
                "completed_at": datetime(2025, 7, 9, 12),
            }
        )
    with pytest.raises(UsageError, match="Missing same-team availability"):
        predict_bullpen_usage(
            (_profile("p", BullpenRole.MIDDLE, 0.5, 0.5, checksum_digit="4"),),
            {},
            team_id="T",
            inning=7,
            score_differential=0,
            cutoff=CUTOFF,
            target_official_date=TARGET_DATE,
        )


def test_generator_observations_still_reject_duplicate_ids_and_future_profiles() -> None:
    duplicate = AvailabilityObservation(
        observation_id="duplicate",
        player_id="p",
        team_id="T",
        status=AvailabilityStatus.AVAILABLE,
        observed_at=CUTOFF - timedelta(minutes=10),
        available_as_of=CUTOFF - timedelta(minutes=5),
        source="TEAM_REPORT",
        applicable_game_id="target",
        effective_through=CUTOFF + timedelta(hours=2),
        evidence_checksum="a" * 64,
    )
    with pytest.raises(UsageError, match="unique"):
        derive_reliever_availability(
            (),
            (row for row in (duplicate, duplicate)),
            player_id="p",
            team_id="T",
            current_game_id="target",
            cutoff=CUTOFF,
            target_official_date=TARGET_DATE,
        )

    snapshot = derive_reliever_availability(
        (),
        (duplicate,),
        player_id="p",
        team_id="T",
        current_game_id="target",
        cutoff=CUTOFF,
        target_official_date=TARGET_DATE,
    )
    with pytest.raises(UsageError, match="Future reliever profile"):
        predict_bullpen_usage(
            (
                _profile(
                    "p",
                    BullpenRole.MIDDLE,
                    0.5,
                    0.5,
                    checksum_digit="5",
                    evidence_cutoff=CUTOFF + timedelta(seconds=1),
                ),
            ),
            {"p": snapshot},
            team_id="T",
            inning=7,
            score_differential=0,
            cutoff=CUTOFF,
            target_official_date=TARGET_DATE,
        )
