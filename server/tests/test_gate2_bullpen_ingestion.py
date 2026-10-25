from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.data_sources import MLBStatsAPIAdapter
from app.data_sources.base import (
    BookmakerDTO,
    BullpenAvailabilityDTO,
    OddsEventDTO,
    ScheduleGameDTO,
    SourceEnvelope,
    TeamDTO,
    VenueDTO,
)
from app.ingestion import CanonicalNormalizer
from app.jobs.orchestrator import JobOrchestrator
from app.models import BullpenAvailabilityObservation, Player


TARGET_DATE = date(2031, 8, 12)
FIRST_PITCH = datetime(2031, 8, 12, 23, 5, tzinfo=UTC)


def _game() -> ScheduleGameDTO:
    return ScheduleGameDTO(
        source_game_id="bullpen-game",
        scheduled_at=FIRST_PITCH,
        event_date=TARGET_DATE,
        status="Scheduled",
        away_team=TeamDTO(source_id="147", name="Away Team", abbreviation="AWY"),
        home_team=TeamDTO(source_id="111", name="Home Team", abbreviation="HME"),
        venue=VenueDTO(source_id="1", name="Bullpen Test Park"),
        source_mode="LIVE",
    )


@pytest.mark.asyncio
async def test_mlb_bullpen_adapter_uses_date_range_and_transparent_score() -> None:
    requested: list[httpx.Request] = []
    payload = {
        "stats": [{
            "group": {"displayName": "pitching"},
            "totalSplits": 4,
            "splits": [
                {
                    "date": "2031-08-11",
                    "player": {
                        "id": 10,
                        "fullName": "Known Reliever",
                        "pitchHand": {"code": "R"},
                    },
                    "stat": {
                        "gamesPitched": 1,
                        "gamesStarted": 0,
                        "numberOfPitches": 20,
                        "inningsPitched": "1.2",
                    },
                },
                {
                    "date": "2031-08-10",
                    "player": {"id": 10, "fullName": "Known Reliever"},
                    "stat": {
                        "gamesPitched": 1,
                        "gamesStarted": 0,
                        "numberOfPitches": 15,
                        "inningsPitched": "1.0",
                    },
                },
                {
                    "date": "2031-08-11",
                    "player": {"id": 11, "fullName": "Sparse Reliever"},
                    "stat": {
                        "gamesPitched": 1,
                        "gamesStarted": 0,
                        "inningsPitched": "1.0",
                    },
                },
                {
                    "date": "2031-08-11",
                    "player": {"id": 12, "fullName": "Starter"},
                    "stat": {
                        "gamesPitched": 1,
                        "gamesStarted": 1,
                        "numberOfPitches": 92,
                        "inningsPitched": "6.0",
                    },
                },
            ],
        }],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await MLBStatsAPIAdapter(client=client).get_bullpen_workload("111", TARGET_DATE)

    assert len(requested) == 1
    params = requested[0].url.params
    assert requested[0].url.path.endswith("/stats")
    assert params["stats"] == "gameLog"
    assert params["group"] == "pitching"
    assert params["teamId"] == "111"
    assert params["startDate"] == "2031-08-09"
    assert params["endDate"] == "2031-08-11"
    assert params["playerPool"] == "all"
    assert params["limit"] == "1000"
    assert result.envelope.source_mode == "LIVE"
    assert result.envelope.metadata["target_day_excluded"] is True
    assert result.envelope.metadata["response_complete"] is True

    by_player = {row["source_player_id"]: row for row in result.envelope.data}
    assert set(by_player) == {"10", "11"}
    known = by_player["10"]
    assert known["pitches_previous_day"] == 20
    assert known["appearances_last_three_days"] == 2
    assert known["innings_last_three_days"] == pytest.approx(2 + 2 / 3)
    assert known["used_consecutive_days"] is True
    assert known["expected_available"] is None
    assert known["participation_probability"] == pytest.approx(0.452381)
    assert known["workload"]["score_status"] == "READY"
    assert known["workload"]["pitches_last_2d"] == 35
    assert known["workload"]["pitches_last_3d"] == 35
    assert known["workload"]["expected_usage_weight"] is None
    assert "0.55*P1/35" in known["workload"]["score_formula"]

    sparse = by_player["11"]
    assert sparse["pitches_previous_day"] is None
    assert sparse["participation_probability"] is None
    assert sparse["workload"]["score_status"] == "NOT_READY"
    assert sparse["workload"]["missing_fields"] == ["numberOfPitches"]


def test_bullpen_normalizer_persists_provenance_and_suppresses_unknown_score(session) -> None:
    normalizer = CanonicalNormalizer(session)
    game = normalizer.ingest_schedule(
        [_game()],
        "MLB_STATS_API",
        available_as_of=FIRST_PITCH - timedelta(hours=8),
    )[0]
    available = FIRST_PITCH - timedelta(hours=1)
    common = {
        "source_team_id": "111",
        "available_as_of": available,
        "evidence_start_date": TARGET_DATE - timedelta(days=3),
        "evidence_end_date": TARGET_DATE - timedelta(days=1),
        "role": "RELIEVER",
        "source_mode": "LIVE",
    }
    known = BullpenAvailabilityDTO(
        **common,
        source_player_id="10",
        full_name="Known Reliever",
        throws="R",
        pitches_previous_day=20,
        appearances_last_three_days=2,
        innings_last_three_days=2.666667,
        used_consecutive_days=True,
        participation_probability=0.452381,
        workload={"score_status": "READY", "availability_score": 0.452381},
    )
    sparse = BullpenAvailabilityDTO(
        **common,
        source_player_id="11",
        full_name="Sparse Reliever",
        participation_probability=0.99,
        workload={
            "score_status": "NOT_READY",
            "availability_score": None,
            "missing_fields": ["numberOfPitches"],
        },
    )

    assert normalizer.ingest_bullpen_availability(
        game.id, [known, sparse], "MLB_STATS_API"
    ) == 2
    assert normalizer.ingest_bullpen_availability(
        game.id, [known, sparse], "MLB_STATS_API"
    ) == 0
    session.commit()

    rows = list(session.execute(
        select(BullpenAvailabilityObservation, Player)
        .join(Player, Player.id == BullpenAvailabilityObservation.player_id)
        .order_by(Player.full_name)
    ))
    assert len(rows) == 2
    known_observation = next(row for row, player in rows if player.full_name == "Known Reliever")
    sparse_observation = next(row for row, player in rows if player.full_name == "Sparse Reliever")
    assert known_observation.source == "MLB_STATS_API"
    assert known_observation.source_mode == "LIVE"
    assert known_observation.available_as_of.replace(tzinfo=UTC) == available
    assert known_observation.participation_probability == pytest.approx(0.452381)
    assert known_observation.expected_available is None
    assert sparse_observation.participation_probability is None
    assert sparse_observation.workload["score_status"] == "NOT_READY"

    after_start = known.model_copy(update={"available_as_of": FIRST_PITCH})
    assert normalizer.ingest_bullpen_availability(
        game.id, [after_start], "MLB_STATS_API"
    ) == 0
    wrong_mode = known.model_copy(update={"source_mode": "DEMO"})
    with pytest.raises(ValueError, match="does not match game mode"):
        normalizer.ingest_bullpen_availability(game.id, [wrong_mode], "MLB_STATS_API")


@pytest.mark.asyncio
async def test_test_mode_refresh_never_calls_mlb_bullpen_provider(monkeypatch, session, settings) -> None:
    CanonicalNormalizer(session).ingest_schedule(
        [_game()],
        "MLB_STATS_API",
        available_as_of=FIRST_PITCH - timedelta(hours=8),
    )
    session.commit()

    async def network_forbidden(*_args, **_kwargs):
        raise AssertionError("MLB provider must not be called in APP_ENV=test")

    monkeypatch.setattr(MLBStatsAPIAdapter, "get_team_stats", network_forbidden)
    monkeypatch.setattr(MLBStatsAPIAdapter, "get_active_roster", network_forbidden)
    monkeypatch.setattr(MLBStatsAPIAdapter, "get_bullpen_workload", network_forbidden)
    monkeypatch.setattr(MLBStatsAPIAdapter, "get_game_lineups", network_forbidden)

    result = await JobOrchestrator(session, settings).refresh_mlb_data(TARGET_DATE)
    assert result["bullpen_observations"] == 0
    assert list(session.scalars(select(BullpenAvailabilityObservation))) == []


@pytest.mark.asyncio
async def test_demo_only_refresh_never_calls_live_mlb_provider(monkeypatch, session, settings) -> None:
    demo_game = _game().model_copy(update={"source_game_id": "demo-bullpen", "source_mode": "DEMO"})
    CanonicalNormalizer(session).ingest_schedule(
        [demo_game],
        "ASP_DEMO_FIXTURE",
        available_as_of=FIRST_PITCH - timedelta(hours=8),
    )
    session.commit()

    async def network_forbidden(*_args, **_kwargs):
        raise AssertionError("demo-only refresh must not call the live MLB provider")

    monkeypatch.setattr(MLBStatsAPIAdapter, "get_team_stats", network_forbidden)
    monkeypatch.setattr(MLBStatsAPIAdapter, "get_active_roster", network_forbidden)
    monkeypatch.setattr(MLBStatsAPIAdapter, "get_bullpen_workload", network_forbidden)
    monkeypatch.setattr(MLBStatsAPIAdapter, "get_game_lineups", network_forbidden)

    development_settings = settings.model_copy(update={"app_env": "development"})
    result = await JobOrchestrator(session, development_settings).refresh_mlb_data(TARGET_DATE)
    assert result["bullpen_observations"] == 0
    assert list(session.scalars(select(BullpenAvailabilityObservation))) == []


def test_source_modes_reject_values_outside_live_and_demo(session) -> None:
    with pytest.raises(ValidationError):
        SourceEnvelope(
            source="UNTRUSTED",
            source_mode="INTERNAL",
            fetched_at=datetime.now(UTC),
            data=[],
        )
    with pytest.raises(ValidationError):
        _game().model_copy(update={"source_mode": "INTERNAL"}, deep=True).__class__.model_validate(
            {**_game().model_dump(), "source_mode": "INTERNAL"}
        )

    event = OddsEventDTO(
        source_event_id="bad-mode",
        commence_time=FIRST_PITCH,
        home_team="Home Team",
        away_team="Away Team",
        bookmakers=[BookmakerDTO(
            key="empty-book",
            title="Empty Book",
            last_update=FIRST_PITCH - timedelta(hours=1),
            markets=[],
        )],
    )
    event.source_mode = "INTERNAL"  # exercise the normalizer's defense in depth
    with pytest.raises(ValueError, match="Unsupported odds event source mode"):
        CanonicalNormalizer(session).ingest_odds(
            [event],
            "TEST",
            FIRST_PITCH - timedelta(hours=1),
            league_code="MLB",
            provider_league="MLB",
        )
