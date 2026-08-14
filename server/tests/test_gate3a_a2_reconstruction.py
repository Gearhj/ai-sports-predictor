from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import random

import pytest

from app.historical.a2_reconstruction import (
    A2_BUILDER_VERSION,
    A2ChronologicalReconstructor,
    A2EvidenceTier,
    A2FinalOutcomeInput,
    A2PlayerGameFactInput,
    A2PlayerRole,
    A2Population,
    A2ReconstructionConfig,
    A2TargetGameInput,
    A2TeamGameFactInput,
    A2VenueInput,
    build_rows,
    canonical_identity_set_checksum,
    feature_identity_for,
)
from app.historical.a2_schema import A2_SCHEMA, A2_SCHEMA_HASH, A2_SCHEMA_VERSION


START = datetime(2025, 4, 10, 20, tzinfo=UTC)
CUTOFF = START - timedelta(hours=6)


def _ck(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _target(
    game_id: str = "target",
    *,
    start: datetime = START,
    home: str = "H",
    away: str = "A",
    venue: str | None = "v2",
    doubleheader: int | None = None,
    official_date: date | None = None,
) -> A2TargetGameInput:
    return A2TargetGameInput(
        game_id=game_id,
        season=2025,
        home_team_id=home,
        away_team_id=away,
        scheduled_first_pitch=start,
        venue_id=venue,
        identity_checksum=_ck(f"target:{game_id}"),
        doubleheader_game_number=doubleheader,
        doubleheader_identity_checksum=(
            _ck(f"doubleheader:{game_id}") if doubleheader is not None else None
        ),
        official_date=official_date or start.date(),
    )


def _outcome(
    game_id: str,
    *,
    completed: datetime,
    eligible: datetime | None = None,
    home: str = "H",
    away: str = "A",
    home_score: int = 5,
    away_score: int = 3,
    venue: str | None = "v1",
    tier: A2EvidenceTier = A2EvidenceTier.A,
    official_date: date | None = None,
) -> A2FinalOutcomeInput:
    return A2FinalOutcomeInput(
        game_id=game_id,
        season=2025,
        home_team_id=home,
        away_team_id=away,
        completed_at=completed,
        eligible_at=eligible or completed,
        home_score=home_score,
        away_score=away_score,
        innings=9,
        venue_id=venue,
        identity_checksum=_ck(f"outcome:{game_id}:{tier.value}"),
        evidence_tier=tier,
        official_date=official_date or completed.date(),
    )


def _team_fact(
    game_id: str,
    team: str,
    opponent: str,
    *,
    when: datetime,
    eligible: datetime | None = None,
    runs: float = 5.0,
    allowed: float = 3.0,
    tier: A2EvidenceTier = A2EvidenceTier.A,
) -> A2TeamGameFactInput:
    return A2TeamGameFactInput(
        fact_id=f"team:{game_id}:{team}:{tier.value}",
        game_id=game_id,
        season=2025,
        team_id=team,
        opponent_team_id=opponent,
        event_at=when,
        evidence_end_at=when,
        eligible_at=eligible or when,
        runs_scored=runs,
        runs_allowed=allowed,
        identity_checksum=_ck(f"team:{game_id}:{team}:{tier.value}"),
        evidence_tier=tier,
    )


def _player_fact(
    game_id: str,
    player: str,
    team: str,
    opponent: str,
    *,
    when: datetime,
    role: A2PlayerRole,
    tier: A2EvidenceTier = A2EvidenceTier.A,
    checksum_suffix: str = "",
    official_date: date | None = None,
    **values: object,
) -> A2PlayerGameFactInput:
    return A2PlayerGameFactInput(
        fact_id=f"player:{game_id}:{team}:{player}:{checksum_suffix}",
        game_id=game_id,
        season=2025,
        player_id=player,
        team_id=team,
        opponent_team_id=opponent,
        role=role,
        event_at=when,
        evidence_end_at=when,
        eligible_at=when,
        identity_checksum=_ck(f"player:{game_id}:{team}:{player}:{tier.value}:{checksum_suffix}"),
        evidence_tier=tier,
        official_date=official_date or when.date(),
        **values,
    )


def _venues() -> tuple[A2VenueInput, ...]:
    return (
        A2VenueInput("v1", 40.0, -74.0, _ck("venue:v1")),
        A2VenueInput("v2", 34.0, -118.0, _ck("venue:v2")),
    )


def test_schema_identity_and_first_game_priors_are_frozen_and_honest() -> None:
    row = build_rows(target_games=(_target(),), venues=_venues())[0]
    values = row.feature_values

    assert A2_SCHEMA_VERSION == "mlb_historical_pregame_a2_v2"
    assert A2_SCHEMA.version == A2_SCHEMA_VERSION
    assert len(A2_SCHEMA.value_names) == 272
    assert len(A2_SCHEMA.output_names) == 544
    assert A2_SCHEMA_HASH == (
        "e29cf4f37487590ff09266f189323f9c42f9c8e99f0d64d8d61bdf41d7b9b9ff"
    )
    assert A2_SCHEMA_HASH == A2_SCHEMA.schema_hash
    assert tuple(values) == A2_SCHEMA.value_names
    assert row.feature_identity.builder_version == A2_BUILDER_VERSION
    assert row.feature_identity.schema_hash == A2_SCHEMA_HASH
    assert feature_identity_for(A2Population.TIER_A_STRICT).definition_checksum != (
        feature_identity_for(A2Population.TIER_B_ENHANCED).definition_checksum
    )
    assert values["home_runs_per_game_season"] is None
    assert values["home_team_sample_season"] == 0.0
    assert values["home_offense_true_talent"] == pytest.approx(4.4)
    assert values["home_team_reliability"] == 0.0
    assert values["home_batting_average_season"] is None
    assert values["home_batting_average_posterior_season"] == pytest.approx(0.248)
    assert values["home_batting_rate_reliability_season"] == 0.0
    assert values["home_pitching_era_posterior_season"] == pytest.approx(4.2)
    assert values["home_bullpen_quality_posterior"] == pytest.approx(4.2)
    assert values["home_elo_rating"] == pytest.approx(1500.0)
    assert values["minutes_to_first_pitch"] == pytest.approx(360.0)
    assert row.metadata.evidence_start_at is None
    assert row.metadata.target_excluded is True
    assert row.metadata.temporal_valid is True


def test_strict_cursor_boundaries_and_current_game_exclusion() -> None:
    targets = (
        _target("before", start=CUTOFF + timedelta(hours=6, microseconds=-1)),
        _target("at", start=CUTOFF + timedelta(hours=6)),
        _target("after", start=CUTOFF + timedelta(hours=6, microseconds=1)),
    )
    fact = _team_fact(
        "prior",
        "H",
        "A",
        when=CUTOFF,
        eligible=CUTOFF,
        runs=8,
        allowed=1,
    )
    malicious = _team_fact(
        "after",
        "H",
        "A",
        when=CUTOFF - timedelta(days=2),
        runs=99,
        allowed=99,
    )
    rows = build_rows(target_games=reversed(targets), team_facts=(fact, malicious))

    assert [row.game_id for row in rows] == ["before", "at", "after"]
    assert rows[0].feature_values["home_team_sample_season"] == 0.0
    assert rows[1].feature_values["home_team_sample_season"] == 0.0
    assert rows[2].feature_values["home_team_sample_season"] == 1.0
    assert rows[2].feature_values["home_runs_per_game_season"] == 8.0
    assert malicious.identity_checksum not in rows[2].transient_contributing_identity_checksums


def test_incremental_elo_replays_in_completion_order() -> None:
    config = A2ReconstructionConfig(elo_home_field_advantage=0.0)
    prior = _outcome("prior", completed=CUTOFF - timedelta(days=1))
    row = build_rows(
        target_games=(_target(),),
        final_outcomes=(prior,),
        config=config,
    )[0]

    assert row.feature_values["home_elo_rating"] == pytest.approx(1510.0)
    assert row.feature_values["away_elo_rating"] == pytest.approx(1490.0)
    assert row.feature_values["home_elo_games"] == 1.0
    assert row.feature_values["elo_expected_home_probability"] == pytest.approx(
        1.0 / (1.0 + 10.0 ** ((1490.0 - 1510.0) / 400.0))
    )
    assert prior.identity_checksum in row.transient_contributing_identity_checksums


def test_additive_batting_pitching_rates_and_bayesian_posteriors() -> None:
    when = CUTOFF - timedelta(days=1)
    batter_one = _player_fact(
        "prior",
        "b1",
        "H",
        "A",
        when=when,
        role=A2PlayerRole.BATTER,
        batting_plate_appearances=5,
        batting_at_bats=4,
        batting_hits=2,
        batting_walks=1,
        batting_strikeouts=1,
        batting_total_bases=5,
        batting_home_runs=1,
    )
    batter_two = _player_fact(
        "prior",
        "b2",
        "H",
        "A",
        when=when,
        role=A2PlayerRole.BATTER,
        batting_plate_appearances=4,
        batting_at_bats=4,
        batting_hits=1,
        batting_walks=0,
        batting_strikeouts=2,
        batting_total_bases=1,
        batting_home_runs=0,
    )
    pitcher = _player_fact(
        "prior",
        "p1",
        "H",
        "A",
        when=when,
        role=A2PlayerRole.STARTER,
        pitching_batters_faced=20,
        pitching_hits_allowed=4,
        pitching_walks_allowed=2,
        pitching_strikeouts=6,
        pitching_home_runs_allowed=1,
        outs_recorded=15,
        earned_runs=2,
        pitches=80,
    )
    row = build_rows(
        target_games=(_target(),), player_facts=(batter_one, batter_two, pitcher)
    )[0]
    values = row.feature_values

    assert values["home_batting_pa_season"] == 9.0
    assert values["home_batting_average_season"] == pytest.approx(3 / 8)
    assert values["home_batting_slugging_14d"] == pytest.approx(6 / 8)
    assert values["home_batting_walk_rate_30d"] == pytest.approx(1 / 9)
    assert values["home_batting_average_posterior_season"] == pytest.approx(
        (0.248 * 200 + 3) / 208
    )
    assert values["home_pitching_bf_season"] == 20.0
    assert values["home_pitching_strikeout_rate_season"] == pytest.approx(0.3)
    assert values["home_pitching_era_season"] == pytest.approx(3.6)
    assert values["home_pitching_era_posterior_season"] == pytest.approx(
        (4.2 * 60 + 2 * 27) / 75
    )

    incomplete = replace(
        batter_one,
        batting_plate_appearances=None,
        identity_checksum=_ck("player:incomplete"),
    )
    missing = build_rows(
        target_games=(_target(),), player_facts=(incomplete, batter_two, pitcher)
    )[0]
    assert missing.feature_values["home_batting_pa_season"] is None
    assert missing.feature_values["home_batting_walk_rate_season"] is None
    assert missing.feature_values["home_batting_walk_rate_posterior_season"] is None


def test_bullpen_windows_never_infer_missing_pitch_counts_from_outs() -> None:
    recent = _player_fact(
        "g1",
        "r1",
        "H",
        "A",
        when=CUTOFF - timedelta(hours=12),
        role=A2PlayerRole.RELIEVER,
        pitches=20,
        outs_recorded=3,
        earned_runs=0,
    )
    missing_pitch = _player_fact(
        "g2",
        "r2",
        "H",
        "A",
        when=CUTOFF - timedelta(hours=36),
        role=A2PlayerRole.RELIEVER,
        pitches=None,
        outs_recorded=6,
        earned_runs=1,
    )
    old = _player_fact(
        "g3",
        "r1",
        "H",
        "A",
        when=CUTOFF - timedelta(hours=60),
        role=A2PlayerRole.RELIEVER,
        pitches=30,
        outs_recorded=3,
        earned_runs=0,
        checksum_suffix="old",
    )
    row = build_rows(
        target_games=(_target(),), player_facts=(missing_pitch, old, recent)
    )[0]
    values = row.feature_values

    assert values["home_bullpen_pitches_1d"] == 20.0
    assert values["home_bullpen_pitches_2d"] is None
    assert values["home_bullpen_pitches_3d"] is None
    assert values["home_bullpen_outs_2d"] == 9.0
    assert values["home_bullpen_innings_2d"] == 3.0
    assert values["home_bullpen_fatigue"] is None
    assert values["home_bullpen_pool_count"] == 2.0


def test_bullpen_consecutive_relievers_require_consecutive_team_games_and_dates() -> None:
    g1_completed = CUTOFF - timedelta(days=2, hours=2)
    g2_completed = CUTOFF - timedelta(days=1, hours=2)
    g1 = _outcome(
        "g1",
        completed=g1_completed,
        official_date=date(2025, 4, 8),
    )
    g2 = _outcome(
        "g2",
        completed=g2_completed,
        official_date=date(2025, 4, 9),
    )
    r1 = _player_fact(
        "g1",
        "r",
        "H",
        "A",
        when=g1_completed,
        role=A2PlayerRole.RELIEVER,
        pitches=18,
        outs_recorded=3,
        earned_runs=0,
        official_date=date(2025, 4, 8),
    )
    r2 = _player_fact(
        "g2",
        "r",
        "H",
        "A",
        when=g2_completed,
        role=A2PlayerRole.RELIEVER,
        pitches=20,
        outs_recorded=3,
        earned_runs=0,
        official_date=date(2025, 4, 9),
    )
    consecutive = build_rows(
        target_games=(_target(official_date=date(2025, 4, 10)),),
        final_outcomes=(g1, g2),
        player_facts=(r1, r2),
    )[0]
    assert consecutive.feature_values["home_bullpen_consecutive_relievers"] == 1.0

    two_day_gap = replace(
        g1,
        official_date=date(2025, 4, 7),
        identity_checksum=_ck("outcome:g1:two-day-gap"),
    )
    gap_row = build_rows(
        target_games=(_target(official_date=date(2025, 4, 10)),),
        final_outcomes=(two_day_gap, g2),
        player_facts=(r1, r2),
    )[0]
    assert gap_row.feature_values["home_bullpen_consecutive_relievers"] == 0.0

    intervening_no_relief = _outcome(
        "g3",
        completed=CUTOFF - timedelta(hours=2),
        official_date=date(2025, 4, 9),
        away="X",
    )
    older_g2 = replace(
        g2,
        completed_at=CUTOFF - timedelta(days=1, hours=4),
        eligible_at=CUTOFF - timedelta(days=1, hours=4),
        official_date=date(2025, 4, 8),
        identity_checksum=_ck("outcome:g2:older"),
    )
    no_relief_row = build_rows(
        target_games=(_target(official_date=date(2025, 4, 10)),),
        final_outcomes=(g1, older_g2, intervening_no_relief),
        player_facts=(r1, r2),
    )[0]
    assert no_relief_row.feature_values["home_bullpen_consecutive_relievers"] == 0.0

    stale_target = build_rows(
        target_games=(
            _target(
                start=START + timedelta(days=1),
                official_date=date(2025, 4, 11),
            ),
        ),
        final_outcomes=(g1, g2),
        player_facts=(r1, r2),
    )[0]
    assert stale_target.feature_values["home_bullpen_consecutive_relievers"] == 0.0


def test_bullpen_consecutive_relievers_are_missing_without_official_dates() -> None:
    g1 = replace(
        _outcome("g1", completed=CUTOFF - timedelta(days=2)),
        official_date=None,
    )
    g2 = _outcome("g2", completed=CUTOFF - timedelta(days=1))
    row = build_rows(
        target_games=(_target(),),
        final_outcomes=(g1, g2),
    )[0]
    assert row.feature_values["home_bullpen_consecutive_relievers"] is None


def test_tier_b_starter_history_follows_player_across_trade_without_moving_bullpen() -> None:
    prior = _player_fact(
        "old-team-game",
        "starter",
        "OLD",
        "X",
        when=CUTOFF - timedelta(days=3),
        role=A2PlayerRole.STARTER,
        pitches=90,
        outs_recorded=18,
        earned_runs=2,
        pitching_batters_faced=24,
        pitching_hits_allowed=5,
        pitching_walks_allowed=2,
        pitching_strikeouts=7,
        pitching_home_runs_allowed=1,
        actual_starter=True,
    )
    target_starter = _player_fact(
        "target",
        "starter",
        "NEW",
        "A",
        when=START + timedelta(hours=3),
        role=A2PlayerRole.STARTER,
        pitches=110,
        outs_recorded=27,
        earned_runs=9,
        actual_starter=True,
    )
    row = build_rows(
        target_games=(_target(home="NEW"),),
        player_facts=(prior, target_starter),
        population=A2Population.TIER_B_ENHANCED,
    )[0]

    assert row.feature_values["home_starter_prior_appearance_count"] == 1.0
    assert row.feature_values["home_starter_prior_era"] == pytest.approx(3.0)
    assert row.feature_values["home_starter_pitches_5d"] == 90.0
    assert row.feature_values["home_bullpen_pool_count"] == 0.0
    assert row.feature_values["home_pitching_bf_season"] == 0.0
    assert row.provisional_families == ("target_context",)
    assert row.timing_risk_families == ("target_context",)


def test_tier_b_starter_quality_counts_only_starts_but_workload_includes_relief() -> None:
    prior_start = _player_fact(
        "start-game",
        "mixed-role",
        "H",
        "A",
        when=CUTOFF - timedelta(days=9),
        role=A2PlayerRole.STARTER,
        pitches=90,
        outs_recorded=18,
        earned_runs=2,
        actual_starter=True,
        official_date=date(2025, 4, 1),
    )
    prior_relief = _player_fact(
        "relief-game",
        "mixed-role",
        "H",
        "A",
        when=CUTOFF - timedelta(days=2),
        role=A2PlayerRole.RELIEVER,
        pitches=20,
        outs_recorded=3,
        earned_runs=9,
        official_date=date(2025, 4, 8),
    )
    target_starter = _player_fact(
        "target",
        "mixed-role",
        "H",
        "A",
        when=START + timedelta(hours=3),
        role=A2PlayerRole.STARTER,
        pitches=120,
        outs_recorded=27,
        earned_runs=12,
        actual_starter=True,
        official_date=date(2025, 4, 10),
    )
    row = build_rows(
        target_games=(_target(official_date=date(2025, 4, 10)),),
        player_facts=(prior_start, prior_relief, target_starter),
        population=A2Population.TIER_B_ENHANCED,
    )[0]
    values = row.feature_values

    assert values["home_starter_prior_appearance_count"] == 1.0
    assert values["home_starter_prior_era"] == pytest.approx(3.0)
    assert values["home_starter_quality_posterior"] == pytest.approx(
        (4.2 * 60 + 2 * 27) / 78
    )
    assert values["home_starter_days_since_previous_start"] == 9.0
    assert values["home_starter_previous_start_pitches"] == 90.0
    assert values["home_starter_previous_start_innings"] == 6.0
    assert values["home_starter_pitches_7d"] == 20.0
    assert values["home_starter_innings_14d"] == 7.0
    assert values["home_starter_appearances_14d"] == 2.0
    assert values["home_starter_workload_trend_7d_vs_prior_7d"] == -70.0
    assert values["home_starter_pitches_3d"] == 20.0

    missing_relief_pitches = replace(
        prior_relief,
        pitches=None,
        identity_checksum=_ck("mixed-role:missing-relief-pitches"),
    )
    missing = build_rows(
        target_games=(_target(),),
        player_facts=(prior_start, missing_relief_pitches, target_starter),
        population=A2Population.TIER_B_ENHANCED,
    )[0]
    assert missing.feature_values["home_starter_pitches_7d"] is None
    assert (
        missing.feature_values["home_starter_workload_trend_7d_vs_prior_7d"]
        is None
    )
    assert missing.feature_values["home_starter_previous_start_pitches"] == 90.0


def test_doubleheader_uses_fixed_cutoff_and_prior_final_for_rest_travel() -> None:
    prior = _outcome("dh1", completed=CUTOFF - timedelta(hours=2))
    team = _team_fact(
        "dh1", "H", "A", when=CUTOFF - timedelta(hours=2), runs=4, allowed=2
    )
    row = build_rows(
        target_games=(_target("dh2", doubleheader=2),),
        final_outcomes=(prior,),
        team_facts=(team,),
        venues=_venues(),
    )[0]

    assert row.feature_values["doubleheader_game_number"] == 2.0
    assert row.feature_values["home_team_sample_season"] == 1.0
    assert row.feature_values["home_hours_since_previous_game"] == pytest.approx(8.0)
    assert row.feature_values["home_previous_game_innings"] == 9.0
    assert row.feature_values["home_travel_miles"] is not None

    late = replace(
        prior,
        completed_at=CUTOFF + timedelta(minutes=1),
        eligible_at=CUTOFF + timedelta(minutes=1),
        identity_checksum=_ck("outcome:late"),
    )
    excluded = build_rows(
        target_games=(_target("dh2", doubleheader=2),),
        final_outcomes=(late,),
        venues=_venues(),
    )[0]
    assert excluded.feature_values["home_hours_since_previous_game"] is None
    assert excluded.feature_values["home_elo_games"] == 0.0


def test_schedule_lineage_binds_both_venue_identities_without_expanding_time_bounds() -> None:
    prior = _outcome("prior", completed=CUTOFF - timedelta(days=1), venue="v1")
    venues = _venues()
    row = build_rows(
        target_games=(_target(venue="v2"),),
        final_outcomes=(prior,),
        venues=venues,
    )[0]
    schedule = row.compact_lineage.family("schedule")

    assert schedule.canonical_reference_count == 3
    assert prior.identity_checksum in row.transient_contributing_identity_checksums
    assert venues[0].identity_checksum in row.transient_contributing_identity_checksums
    assert venues[1].identity_checksum in row.transient_contributing_identity_checksums
    assert row.metadata.evidence_start_at == prior.completed_at
    assert row.metadata.evidence_end_at == prior.completed_at
    assert row.metadata.eligible_max_at == prior.eligible_at
    assert row.metadata.outcome_history_count == 1

    changed_venues = (
        replace(venues[0], identity_checksum=_ck("venue:v1:corrected-identity")),
        replace(venues[1], identity_checksum=_ck("venue:v2:corrected-identity")),
    )
    changed = build_rows(
        target_games=(_target(venue="v2"),),
        final_outcomes=(prior,),
        venues=changed_venues,
    )[0]
    assert changed.feature_items == row.feature_items
    assert changed.compact_lineage.family("schedule").canonical_set_checksum != (
        schedule.canonical_set_checksum
    )
    assert changed.row_checksum != row.row_checksum
    assert changed.metadata.evidence_start_at == row.metadata.evidence_start_at
    assert changed.metadata.evidence_end_at == row.metadata.evidence_end_at
    assert changed.metadata.eligible_max_at == row.metadata.eligible_max_at

    first_game = build_rows(
        target_games=(_target(venue="v2"),),
        venues=venues,
    )[0]
    assert first_game.compact_lineage.family("schedule").canonical_reference_count == 1
    assert first_game.metadata.evidence_start_at is None
    assert first_game.metadata.evidence_end_at is None
    assert first_game.metadata.eligible_max_at is None


def test_tier_a_b_are_isolated_and_target_performance_is_not_a_strict_feature() -> None:
    b_fact = _team_fact(
        "rain",
        "H",
        "A",
        when=CUTOFF - timedelta(days=1),
        runs=12,
        allowed=10,
        tier=A2EvidenceTier.B,
    )
    target_context = _player_fact(
        "target",
        "s",
        "H",
        "A",
        when=START + timedelta(hours=3),
        role=A2PlayerRole.STARTER,
        tier=A2EvidenceTier.B,
        actual_starter=True,
        pitches=120,
        outs_recorded=27,
        earned_runs=8,
    )
    strict = build_rows(
        target_games=(_target(),),
        team_facts=(b_fact,),
        player_facts=(target_context,),
        population=A2Population.TIER_A_STRICT,
    )[0]
    enhanced = build_rows(
        target_games=(_target(),),
        team_facts=(b_fact,),
        player_facts=(target_context,),
        population=A2Population.TIER_B_ENHANCED,
    )[0]

    assert strict.feature_values["home_team_sample_season"] == 0.0
    assert strict.feature_values["home_starter_quality_posterior"] is None
    assert strict.provisional_families == ()
    assert enhanced.feature_values["home_team_sample_season"] == 1.0
    assert enhanced.feature_values["home_starter_quality_posterior"] == pytest.approx(4.2)
    assert enhanced.feature_values["home_starter_prior_appearance_count"] == 0.0

    mutated = replace(
        target_context,
        pitches=1,
        outs_recorded=0,
        earned_runs=0,
        identity_checksum=_ck("mutated-target-context"),
    )
    strict_mutated = build_rows(
        target_games=(_target(),),
        team_facts=(b_fact,),
        player_facts=(mutated,),
        population=A2Population.TIER_A_STRICT,
    )[0]
    assert strict_mutated.feature_items == strict.feature_items
    assert strict_mutated.row_checksum == strict.row_checksum


def test_shuffle_determinism_and_compact_attestation_exactness() -> None:
    targets = [_target("t2", start=START + timedelta(days=1)), _target("t1")]
    outcomes = [_outcome("o1", completed=CUTOFF - timedelta(days=2))]
    teams = [
        _team_fact("o1", "H", "A", when=CUTOFF - timedelta(days=2)),
        _team_fact("o1", "A", "H", when=CUTOFF - timedelta(days=2), runs=3, allowed=5),
    ]
    players = [
        _player_fact(
            "o1",
            "r1",
            "H",
            "A",
            when=CUTOFF - timedelta(days=2),
            role=A2PlayerRole.RELIEVER,
            pitches=18,
            outs_recorded=3,
            earned_runs=0,
        )
    ]

    baseline = build_rows(
        target_games=targets,
        final_outcomes=outcomes,
        team_facts=teams,
        player_facts=players,
    )
    for seed in range(5):
        rng = random.Random(seed)
        shuffled = [list(items) for items in (targets, outcomes, teams, players)]
        for items in shuffled:
            rng.shuffle(items)
        result = build_rows(
            target_games=shuffled[0],
            final_outcomes=shuffled[1],
            team_facts=shuffled[2],
            player_facts=shuffled[3],
        )
        assert [row.row_checksum for row in result] == [row.row_checksum for row in baseline]

    row = baseline[0]
    exact = row.transient_contributing_identity_checksums
    assert row.compact_lineage.canonical_reference_count == len(exact)
    assert row.compact_lineage.canonical_set_checksum == canonical_identity_set_checksum(exact)
    for family in row.compact_lineage.families:
        assert len(family.canonical_set_checksum) == 64
        assert family.canonical_reference_count >= 0
    payload = row.to_persistable_dict()
    assert "_contributing_identity_checksums" not in payload
    assert "transient_contributing_identity_checksums" not in payload


def test_iter_rows_streams_a_full_mlb_season_without_retaining_result_collection() -> None:
    targets = tuple(
        _target(
            f"season-{index:04d}",
            start=START + timedelta(minutes=index),
            venue=None,
        )
        for index in range(2430)
    )
    engine = A2ChronologicalReconstructor()
    rows = engine.iter_rows(target_games=targets)
    assert iter(rows) is rows
    count = 0
    last: deque[object] = deque(maxlen=1)
    for row in rows:
        count += 1
        last.append(row)
    assert count == 2430
    assert len(last) == 1
    assert not hasattr(engine, "rows")


def test_pre_resolved_uniqueness_and_temporal_input_validation_fail_closed() -> None:
    target = _target()
    with pytest.raises(ValueError, match="pre-resolved"):
        build_rows(target_games=(target, replace(target, identity_checksum=_ck("duplicate"))))
    with pytest.raises(ValueError, match="timezone-aware"):
        _target(start=datetime(2025, 4, 10, 20))
    with pytest.raises(ValueError, match="checksum"):
        replace(target, identity_checksum="not-a-checksum")


def test_row_contract_rejects_target_or_late_evidence_metadata() -> None:
    row = build_rows(target_games=(_target(),))[0]
    with pytest.raises(ValueError, match="target-game evidence"):
        replace(row, metadata=replace(row.metadata, target_excluded=False))
    with pytest.raises(ValueError, match="outside the governed cutoff"):
        replace(row, metadata=replace(row.metadata, temporal_valid=False))
