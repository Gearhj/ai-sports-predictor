from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.historical.elo import CompletedGameResult, EloConfig, replay_pregame_elo
from app.historical.policy import EvidenceClassification, EvidenceTier
from app.historical.talent import (
    HistoricalTalentEvidence,
    build_historical_platoon_posterior,
    build_historical_talent_posterior,
)
from app.historical.workload import (
    AppearanceRole,
    HistoricalGameStatus,
    PlayerAppearanceEvent,
    TeamGameEvent,
    derive_bullpen_workloads,
    derive_rest_travel,
    derive_starter_workload,
)


CUTOFF = datetime(2025, 7, 2, 12, tzinfo=UTC)


def _result(
    game_id: str,
    completed_at: datetime,
    *,
    home: str = "H",
    away: str = "A",
    home_score: int = 5,
    away_score: int = 3,
    eligible_at: datetime | None = None,
) -> CompletedGameResult:
    return CompletedGameResult(
        game_id=game_id,
        home_team_id=home,
        away_team_id=away,
        completed_at=completed_at,
        eligible_at=eligible_at or completed_at + timedelta(minutes=5),
        home_score=home_score,
        away_score=away_score,
    )


def test_elo_excludes_target_and_future_outcomes_even_if_scores_change() -> None:
    prior = _result("prior", CUTOFF - timedelta(days=1))
    malicious_target = _result("target", CUTOFF - timedelta(days=2), home_score=99, away_score=0)
    future = _result("future", CUTOFF + timedelta(hours=1), home_score=99, away_score=0)

    first = replay_pregame_elo(
        (prior, malicious_target, future),
        target_game_id="target",
        home_team_id="H",
        away_team_id="A",
        cutoff=CUTOFF,
    )
    changed = replay_pregame_elo(
        (
            prior,
            _result("target", CUTOFF - timedelta(days=2), home_score=0, away_score=99),
            _result("future", CUTOFF + timedelta(hours=1), home_score=0, away_score=99),
        ),
        target_game_id="target",
        home_team_id="H",
        away_team_id="A",
        cutoff=CUTOFF,
    )

    assert first.processed_game_ids == ("prior",)
    assert changed.home_rating == first.home_rating
    assert changed.away_rating == first.away_rating
    assert changed.expected_home_win == first.expected_home_win


def test_elo_doubleheader_game_one_enters_game_two_only_after_eligibility() -> None:
    completed = CUTOFF - timedelta(minutes=30)
    eligible = _result("game-1", completed, eligible_at=CUTOFF - timedelta(minutes=5))
    late = _result("game-1", completed, eligible_at=CUTOFF + timedelta(minutes=5))

    with_game_one = replay_pregame_elo(
        [eligible],
        target_game_id="game-2",
        home_team_id="H",
        away_team_id="A",
        cutoff=CUTOFF,
        config=EloConfig(home_field_advantage=0),
    )
    without_game_one = replay_pregame_elo(
        [late],
        target_game_id="game-2",
        home_team_id="H",
        away_team_id="A",
        cutoff=CUTOFF,
        config=EloConfig(home_field_advantage=0),
    )

    assert with_game_one.processed_game_ids == ("game-1",)
    assert with_game_one.home_rating > 1500
    assert without_game_one.processed_game_ids == ()
    assert without_game_one.home_rating == 1500


def _appearance(
    event_id: str,
    game_id: str,
    player_id: str,
    completed_at: datetime,
    pitches: float,
    *,
    role: AppearanceRole = AppearanceRole.RELIEVER,
    team_id: str = "H",
    eligible_at: datetime | None = None,
) -> PlayerAppearanceEvent:
    return PlayerAppearanceEvent(
        event_id=event_id,
        game_id=game_id,
        player_id=player_id,
        team_id=team_id,
        role=role,
        completed_at=completed_at,
        eligible_at=eligible_at or completed_at + timedelta(minutes=5),
        pitches=pitches,
        outs_recorded=3,
    )


def test_bullpen_and_starter_workload_use_prior_eligible_events_only() -> None:
    appearances = (
        _appearance("r-valid", "prior", "reliever", CUTOFF - timedelta(hours=12), 24),
        _appearance("r-target", "target", "reliever", CUTOFF - timedelta(hours=10), 100),
        _appearance("r-future", "future", "reliever", CUTOFF + timedelta(hours=1), 100),
        _appearance(
            "r-late",
            "late",
            "reliever",
            CUTOFF - timedelta(hours=8),
            100,
            eligible_at=CUTOFF + timedelta(minutes=1),
        ),
        _appearance("starter-valid", "prior-2", "starter", CUTOFF - timedelta(days=2), 85, role=AppearanceRole.STARTER),
        _appearance("starter-relief", "prior-3", "starter", CUTOFF - timedelta(hours=20), 9),
        _appearance("traded", "prior-4", "reliever", CUTOFF - timedelta(hours=3), 70, team_id="OTHER"),
    )

    bullpen = derive_bullpen_workloads(
        appearances,
        team_id="H",
        cutoff=CUTOFF,
        current_game_id="target",
    )
    starter = derive_starter_workload(
        appearances,
        starter_id="starter",
        team_id="H",
        cutoff=CUTOFF,
        current_game_id="target",
    )

    assert tuple(item.player_id for item in bullpen.relievers) == ("reliever", "starter")
    reliever = next(item for item in bullpen.relievers if item.player_id == "reliever")
    assert reliever.window(1).pitches == 24
    assert reliever.window(1).event_ids == ("r-valid",)
    assert "r-target" not in bullpen.contributing_event_ids
    assert starter.window(1).pitches == 9
    assert starter.window(3).pitches == 94


def test_rest_travel_uses_latest_final_eligible_game_not_schedule_projection() -> None:
    target_start = CUTOFF + timedelta(hours=6)
    games = (
        TeamGameEvent(
            "final",
            "H",
            CUTOFF - timedelta(days=1, hours=4),
            CUTOFF - timedelta(days=1),
            CUTOFF - timedelta(days=1) + timedelta(minutes=5),
            HistoricalGameStatus.FINAL,
            "old-park",
            40.7128,
            -74.0060,
            11,
        ),
        TeamGameEvent(
            "postponed",
            "H",
            CUTOFF - timedelta(hours=12),
            CUTOFF - timedelta(hours=10),
            CUTOFF - timedelta(hours=9),
            HistoricalGameStatus.POSTPONED,
            "near-park",
            39.9526,
            -75.1652,
            None,
        ),
        TeamGameEvent(
            "target",
            "H",
            target_start,
            target_start + timedelta(hours=3),
            target_start + timedelta(hours=3, minutes=5),
            HistoricalGameStatus.FINAL,
            "target-park",
            41.8781,
            -87.6298,
            9,
        ),
    )

    snapshot = derive_rest_travel(
        games,
        team_id="H",
        target_game_id="target",
        target_first_pitch=target_start,
        cutoff=CUTOFF,
        target_venue_latitude=41.8781,
        target_venue_longitude=-87.6298,
    )

    assert snapshot.previous_game_id == "final"
    assert snapshot.hours_since_previous_completion == pytest.approx(30.0)
    assert snapshot.full_24h_rest_periods == 1
    assert snapshot.previous_game_innings == 11
    assert snapshot.travel_miles is not None and snapshot.travel_miles > 700


def _talent_evidence(
    value: float,
    sample: float,
    identifier: str,
    *,
    end: datetime,
    game_ids: tuple[str, ...] = (),
    classification: EvidenceClassification = EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME,
) -> HistoricalTalentEvidence:
    return HistoricalTalentEvidence(
        value=value,
        sample_size=sample,
        event_end_at=end,
        eligible_at=end + timedelta(minutes=5),
        evidence_ids=(identifier,),
        game_ids=game_ids,
        classification=classification,
    )


def test_historical_bayes_preserves_early_season_and_missing_recent_shrinkage() -> None:
    prior = _talent_evidence(0.320, 200, "prior", end=CUTOFF - timedelta(days=100))
    tiny_season = _talent_evidence(0.500, 10, "season", end=CUTOFF - timedelta(days=1))
    tiny = build_historical_talent_posterior(
        target_game_id="target",
        cutoff=CUTOFF,
        prior=prior,
        season=tiny_season,
    )
    rookie = build_historical_talent_posterior(
        target_game_id="target",
        cutoff=CUTOFF,
        prior=prior,
    )

    assert tiny.estimate.value == pytest.approx((0.320 * 200 + 0.500 * 10) / 210)
    assert abs(tiny.estimate.value - 0.320) < 0.01
    assert rookie.estimate.value == pytest.approx(0.320)
    assert rookie.component_names == ("prior",)
    assert not rookie.estimate.recent_evidence_used


def test_historical_bayes_rejects_target_future_and_forward_only_evidence() -> None:
    prior = _talent_evidence(0.320, 200, "prior", end=CUTOFF - timedelta(days=100))
    target = _talent_evidence(
        0.900,
        20,
        "target",
        end=CUTOFF - timedelta(hours=1),
        game_ids=("target",),
    )
    future = _talent_evidence(0.900, 20, "future", end=CUTOFF + timedelta(hours=1))
    forward = _talent_evidence(
        0.900,
        20,
        "forward",
        end=CUTOFF - timedelta(hours=1),
        classification=EvidenceClassification.FORWARD_ONLY,
    )

    for invalid in (target, future, forward):
        with pytest.raises(ValueError):
            build_historical_talent_posterior(
                target_game_id="target",
                cutoff=CUTOFF,
                prior=prior,
                season=invalid,
            )


def test_historical_platoon_split_is_shrunk_and_partial_tier_is_visible() -> None:
    prior = _talent_evidence(0.330, 200, "prior", end=CUTOFF - timedelta(days=100))
    overall = build_historical_talent_posterior(
        target_game_id="target",
        cutoff=CUTOFF,
        prior=prior,
    )
    split = _talent_evidence(
        0.500,
        5,
        "split",
        end=CUTOFF - timedelta(days=1),
        classification=EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
    )
    posterior = build_historical_platoon_posterior(overall=overall, split=split)

    assert abs(posterior.estimate.value - 0.330) < 0.02
    assert posterior.tier is EvidenceTier.B
    assert posterior.component_names[-1] == "platoon_split"
