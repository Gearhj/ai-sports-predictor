from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import inspect as python_inspect
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

import app.historical.a2_statcast_workflow as workflow
from app.historical.statcast_promotion import StatcastPromotionError
from app.models import (
    ExternalIdMapping,
    Game,
    GameFinalOutcome,
    GameStatus,
    GameTeam,
    League,
    Player,
    ProviderRequestLedger,
    Season,
    Sport,
    Team,
)


TARGET_DATE = date(2025, 6, 17)


def _outcome(
    *,
    game: Game,
    season: Season,
    home: Team,
    away: Team,
    revision: int,
    source_version_id,
) -> GameFinalOutcome:
    final_at = game.starts_at + timedelta(hours=3, minutes=revision)
    retrieved_at = datetime(2026, 8, 14, revision, tzinfo=UTC)
    return GameFinalOutcome(
        game_id=game.id,
        season_id=season.id,
        home_team_id=home.id,
        away_team_id=away.id,
        source_version_id=source_version_id,
        checkpoint_id=uuid4(),
        official_date=TARGET_DATE,
        scheduled_at=game.starts_at,
        final_at=final_at,
        completion_time_basis="DERIVED_LAST_EVENT_COMPLETION_PROXY",
        completion_time_source_field="liveData.plays.allPlays[-1].about.endTime",
        completion_time_method="last complete play after FINAL validation",
        completion_time_limitation="not an explicit final-publication timestamp",
        evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
        quality_tier="A",
        reconstructed_at=retrieved_at,
        home_score=5,
        away_score=3,
        revision=revision,
        official=True,
        source_record_id=f"game:777:revision:{revision}",
        retrieved_at=retrieved_at,
        label_available_at=retrieved_at,
        availability_basis="ASP_ACQUISITION_TIME",
        payload_checksum=str(revision) * 64,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum=hex(revision)[2:] * 64,
    )


def _seed(
    session,
    *,
    include_game_mapping: bool = True,
    include_player_mappings: bool = True,
    swap_game_teams: bool = False,
) -> dict[str, object]:
    sport = Sport(code="MLB-A2-SC", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB-A2-SC", name="MLB")
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        name="2025",
        starts_at=datetime(2025, 3, 1, tzinfo=UTC),
        ends_at=datetime(2025, 11, 15, tzinfo=UTC),
    )
    # The A0 warehouse can retain an exact MLB-ID placeholder abbreviation;
    # the A2 wrapper must resolve the canonical full name through the same
    # governed MLB normalization map used by ingestion.
    home = Team(
        league_id=league.id,
        name="New York Yankees",
        abbreviation="MLB147",
    )
    away = Team(league_id=league.id, name="Boston Red Sox", abbreviation="BOS")
    session.add_all([season, home, away])
    session.flush()
    game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=datetime(2025, 6, 17, 23, 5, tzinfo=UTC),
        status=GameStatus.FINAL,
        home_score=5,
        away_score=3,
        final_at=datetime(2025, 6, 18, 2, 7, tzinfo=UTC),
        source_mode="LIVE",
    )
    batter = Player(team_id=home.id, full_name="Exact Batter", position="RF")
    pitcher = Player(team_id=away.id, full_name="Exact Pitcher", position="P")
    session.add_all([game, batter, pitcher])
    session.flush()
    session.add_all([
        GameTeam(
            game_id=game.id,
            team_id=away.id if swap_game_teams else home.id,
            is_home=True,
        ),
        GameTeam(
            game_id=game.id,
            team_id=home.id if swap_game_teams else away.id,
            is_home=False,
        ),
    ])
    source_version_id = uuid4()
    first = _outcome(
        game=game,
        season=season,
        home=home,
        away=away,
        revision=1,
        source_version_id=source_version_id,
    )
    second = _outcome(
        game=game,
        season=season,
        home=home,
        away=away,
        revision=2,
        source_version_id=source_version_id,
    )
    second.supersedes_outcome_id = first.id
    session.add_all([first, second])
    if include_game_mapping:
        session.add(ExternalIdMapping(
            entity_type="game",
            entity_id=game.id,
            source="MLB_STATS_API",
            external_id="777",
        ))
    if include_player_mappings:
        session.add_all([
            ExternalIdMapping(
                entity_type="player",
                entity_id=batter.id,
                source="MLB_STATS_API",
                external_id="101",
            ),
            ExternalIdMapping(
                entity_type="player",
                entity_id=pitcher.id,
                source="MLB_STATS_API",
                external_id="202",
            ),
        ])
    session.commit()
    return {
        "game": game,
        "home": home,
        "away": away,
        "batter": batter,
        "pitcher": pitcher,
        "latest": second,
    }


def _counts(session) -> dict[str, int]:
    models = (
        ExternalIdMapping,
        Game,
        GameFinalOutcome,
        GameTeam,
        Player,
        ProviderRequestLedger,
        Team,
    )
    return {
        model.__tablename__: session.scalar(select(func.count()).select_from(model)) or 0
        for model in models
    }


def test_promote_existing_statcast_day_resolves_exact_latest_identities_without_writes(
    session,
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed(session)
    before = _counts(session)
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_promote(options, *, game_mappings, player_mappings):
        captured.update({
            "options": options,
            "games": tuple(game_mappings),
            "players": tuple(player_mappings),
        })
        return sentinel

    monkeypatch.setattr(workflow, "promote_statcast_archive", fake_promote)
    result = workflow.promote_existing_statcast_day(
        session,
        archive_path=artifact_root / "existing-raw.json.gz",
        artifact_root=artifact_root / "promoted",
        target_date=TARGET_DATE,
        source_version="statcast-2025-06-17-raw-v1",
    )

    assert result is sentinel
    games = captured["games"]
    assert len(games) == 1
    assert games[0].source_game_id == "777"
    assert games[0].game_id == str(seeded["game"].id)
    assert games[0].home_team_code == "NYY"
    assert games[0].away_team_code == "BOS"
    expected_final = seeded["latest"].final_at
    if expected_final.tzinfo is None:
        expected_final = expected_final.replace(tzinfo=UTC)
    assert games[0].final_at == expected_final.astimezone(UTC)
    assert {
        (mapping.source_player_id, mapping.player_id)
        for mapping in captured["players"]
    } == {
        ("101", str(seeded["batter"].id)),
        ("202", str(seeded["pitcher"].id)),
    }
    assert _counts(session) == before
    assert not session.new and not session.dirty and not session.deleted
    assert "app.data_sources" not in python_inspect.getsource(workflow)
    assert not any("Adapter" in name for name in workflow.__dict__)


def test_statcast_team_code_uses_exact_source_alias() -> None:
    assert workflow._team_code(
        "MLB109",
        "Arizona Diamondbacks",
        "away",
        uuid4(),
    ) == "AZ"


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("missing_game", "Missing exact MLB game mapping"),
        ("missing_players", "No exact MLB player mappings"),
        ("ambiguous_game", "Ambiguous MLB game mappings"),
        ("ambiguous_player", "Ambiguous MLB player mappings"),
        ("team_mismatch", "team identity mismatch"),
    ],
)
def test_promote_existing_statcast_day_fails_closed_before_promotion(
    session,
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    message: str,
) -> None:
    seeded = _seed(
        session,
        include_game_mapping=scenario != "missing_game",
        include_player_mappings=scenario != "missing_players",
        swap_game_teams=scenario == "team_mismatch",
    )
    if scenario == "ambiguous_game":
        session.add(ExternalIdMapping(
            entity_type="game",
            entity_id=seeded["game"].id,
            source="MLB_STATS_API",
            external_id="778",
        ))
        session.commit()
    if scenario == "ambiguous_player":
        session.add(ExternalIdMapping(
            entity_type="player",
            entity_id=seeded["batter"].id,
            source="MLB_STATS_API",
            external_id="102",
        ))
        session.commit()

    before = _counts(session)
    calls = 0

    def forbidden_promotion(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("promotion must not run after mapping failure")

    monkeypatch.setattr(workflow, "promote_statcast_archive", forbidden_promotion)
    with pytest.raises(StatcastPromotionError, match=message):
        workflow.promote_existing_statcast_day(
            session,
            archive_path=artifact_root / "existing-raw.json.gz",
            artifact_root=artifact_root / "promoted",
            target_date=TARGET_DATE,
            source_version="statcast-2025-06-17-raw-v1",
        )
    assert calls == 0
    assert _counts(session) == before
    assert not session.new and not session.dirty and not session.deleted


def test_promote_existing_statcast_day_rejects_ambiguous_latest_outcome(
    session,
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed(session)
    duplicate_latest = _outcome(
        game=seeded["game"],
        season=session.scalar(select(Season).where(Season.name == "2025")),
        home=seeded["home"],
        away=seeded["away"],
        revision=2,
        source_version_id=uuid4(),
    )
    duplicate_latest.identity_checksum = "f" * 64
    duplicate_latest.source_record_id = "game:777:alternate-source"
    session.add(duplicate_latest)
    session.commit()

    calls = 0

    def forbidden_promotion(*args, **kwargs):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(workflow, "promote_statcast_archive", forbidden_promotion)
    with pytest.raises(StatcastPromotionError, match="Ambiguous latest official outcome"):
        workflow.promote_existing_statcast_day(
            session,
            archive_path=artifact_root / "existing-raw.json.gz",
            artifact_root=artifact_root / "promoted",
            target_date=TARGET_DATE,
            source_version="statcast-2025-06-17-raw-v1",
        )
    assert calls == 0
