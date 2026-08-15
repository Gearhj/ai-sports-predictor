from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
import json
from uuid import UUID, uuid4

import pytest

from app.gen2.fd1_identity import (
    NamespaceCount,
    PersonCrosswalkReport,
    PersonNamespaceAssertion,
    ResolutionStatus,
    RetrosheetOrganizationCrosswalkReport,
    RetrosheetTeamSeasonCrosswalk,
    SourceRowEvidence,
)
from app.gen2.fd1_reconciliation import (
    ASP_GAME_NUMBER_DERIVED_SINGLETON,
    ASP_GAME_NUMBER_DERIVED_START_TIME,
    ASP_GAME_NUMBER_SOURCE_EXPLICIT,
    AspGameEvidence,
    ComparisonStatus,
    ExactIdentityCrosswalk,
    Fd1ReconciliationError,
    LAHMAN_TEAM_SEASON_MAPPING_SOURCE,
    RetrosheetGameEvidence,
    build_2025_exact_identity_crosswalk,
    build_2025_schedule_fingerprint_crosswalk,
    load_asp_2025_live_evidence,
    normalize_asp_schedule_game_numbers,
    reconcile_2025_retrosheet_to_asp,
    retrosheet_evidence_from_materialized_rows,
)
from app.models import (
    ExternalIdMapping,
    Game,
    GameFinalOutcome,
    GameStatus,
    GameTeam,
    League,
    Season,
    Sport,
    Team,
    Venue,
)


DAY = date(2025, 6, 17)


def _asp(
    *,
    game_id: str = "asp-1",
    scheduled_at: datetime = datetime(2025, 6, 17, 17, 5, tzinfo=UTC),
    game_number: int | None = 1,
    home_score: int = 5,
    away_score: int = 3,
    venue_id: str | None = "venue-1",
) -> AspGameEvidence:
    return AspGameEvidence(
        game_id=game_id,
        official_date=DAY,
        scheduled_at=scheduled_at,
        home_team_id="asp-home",
        away_team_id="asp-away",
        home_score=home_score,
        away_score=away_score,
        source_record_id=f"mlb-{game_id}",
        source_version_id="source-v1",
        game_number=game_number,
        doubleheader_id="dh-1" if game_number == 2 else None,
        venue_id=venue_id,
        home_starter_id="asp-hp",
        away_starter_id="asp-ap",
        home_lineup_ids=frozenset({"asp-h1", "asp-h2"}),
        away_lineup_ids=frozenset({"asp-a1", "asp-a2"}),
        batting_participant_ids=frozenset({"asp-h1", "asp-h2", "asp-a1", "asp-a2"}),
        pitching_participant_ids=frozenset({"asp-hp", "asp-ap"}),
        player_participant_ids=frozenset(
            {"asp-h1", "asp-h2", "asp-a1", "asp-a2", "asp-hp", "asp-ap"}
        ),
    )


def _retro(
    *,
    raw_game_id: str = "HOM202506170",
    game_number: int = 1,
    home_score: int = 5,
    away_score: int = 3,
    park_raw_id: str = "retro-park",
) -> RetrosheetGameEvidence:
    return RetrosheetGameEvidence(
        raw_game_id=raw_game_id,
        source_game_id=f"retrosheet:game:{raw_game_id}",
        game_date=DAY,
        home_team_raw_id="retro-home",
        away_team_raw_id="retro-away",
        game_number=game_number,
        doubleheader_id="dh-1" if game_number == 2 else None,
        park_raw_id=park_raw_id,
        home_score=home_score,
        away_score=away_score,
        home_starter_raw_id="retro-hp",
        away_starter_raw_id="retro-ap",
        home_lineup_raw_ids=frozenset({"retro-h1", "retro-h2"}),
        away_lineup_raw_ids=frozenset({"retro-a1", "retro-a2"}),
        batting_participant_raw_ids=frozenset(
            {"retro-h1", "retro-h2", "retro-a1", "retro-a2"}
        ),
        pitching_participant_raw_ids=frozenset({"retro-hp", "retro-ap"}),
        player_participant_raw_ids=frozenset(
            {
                "retro-h1",
                "retro-h2",
                "retro-a1",
                "retro-a2",
                "retro-hp",
                "retro-ap",
            }
        ),
        score_evidence="GAMEINFO_FINAL",
    )


def _crosswalk(*, venue: str = "venue-1") -> ExactIdentityCrosswalk:
    return ExactIdentityCrosswalk(
        team_ids={"retro-home": "asp-home", "retro-away": "asp-away"},
        player_ids={
            "retro-hp": "asp-hp",
            "retro-ap": "asp-ap",
            "retro-h1": "asp-h1",
            "retro-h2": "asp-h2",
            "retro-a1": "asp-a1",
            "retro-a2": "asp-a2",
        },
        venue_ids={"retro-park": venue},
    )


def _schedule_fingerprint_population(
    *, score_conflict_index: int | None = None
) -> tuple[list[AspGameEvidence], list[RetrosheetGameEvidence], list[str], list[str]]:
    raw_team_ids = [f"retrosheet:team:R{index:02d}" for index in range(30)]
    asp_team_ids = [f"asp-team-{index:02d}" for index in range(30)]
    asp_games: list[AspGameEvidence] = []
    retro_games: list[RetrosheetGameEvidence] = []
    game_index = 0
    for round_index in range(162):
        game_date = date(2025, 3, 1) + timedelta(days=round_index)
        for slot in range(15):
            home_index = (round_index + slot) % 30
            away_index = (round_index + 29 - slot) % 30
            home_score = 2 + (game_index * 7) % 9
            away_score = 1 + (game_index * 5) % 8
            if home_score == away_score:
                home_score += 1
            raw_game_id = f"R{round_index:03d}{slot:02d}0"
            asp_games.append(
                AspGameEvidence(
                    game_id=f"asp-game-{game_index:04d}",
                    official_date=game_date,
                    scheduled_at=datetime(
                        game_date.year,
                        game_date.month,
                        game_date.day,
                        17 + slot % 4,
                        tzinfo=UTC,
                    ),
                    home_team_id=asp_team_ids[home_index],
                    away_team_id=asp_team_ids[away_index],
                    home_score=home_score,
                    away_score=away_score,
                    source_record_id=f"mlb-game-{game_index:04d}",
                    source_version_id="asp-source-v1",
                    game_number=1,
                )
            )
            retro_games.append(
                RetrosheetGameEvidence(
                    raw_game_id=raw_game_id,
                    source_game_id=f"retrosheet:game:{raw_game_id}",
                    game_date=game_date,
                    home_team_raw_id=raw_team_ids[home_index],
                    away_team_raw_id=raw_team_ids[away_index],
                    game_number=1,
                    source_game_number=0,
                    home_score=(
                        home_score + 1
                        if score_conflict_index == game_index
                        else home_score
                    ),
                    away_score=away_score,
                )
            )
            game_index += 1
    return asp_games, retro_games, raw_team_ids, asp_team_ids


def _schedule_asp(
    game_id: str,
    game_date: date,
    home_team_id: str,
    away_team_id: str,
    *,
    game_number: int | None = 1,
) -> AspGameEvidence:
    return AspGameEvidence(
        game_id=game_id,
        official_date=game_date,
        scheduled_at=datetime(
            game_date.year,
            game_date.month,
            game_date.day,
            17 + (game_number or 1),
            tzinfo=UTC,
        ),
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_score=5,
        away_score=3,
        source_record_id=f"mlb-{game_id}",
        source_version_id="asp-source-v1",
        game_number=game_number,
    )


def _schedule_retro(
    raw_game_id: str,
    game_date: date,
    home_team_id: str,
    away_team_id: str,
    *,
    game_number: int = 1,
    source_game_number: int = 0,
    doubleheader_id: str | None = None,
) -> RetrosheetGameEvidence:
    return RetrosheetGameEvidence(
        raw_game_id=raw_game_id,
        source_game_id=f"retrosheet:game:{raw_game_id}",
        game_date=game_date,
        home_team_raw_id=home_team_id,
        away_team_raw_id=away_team_id,
        game_number=game_number,
        source_game_number=source_game_number,
        doubleheader_id=doubleheader_id,
        home_score=5,
        away_score=3,
    )


def _identity_source_row(index: int) -> SourceRowEvidence:
    return SourceRowEvidence(
        source_key="CHADWICK_REGISTER_DOWNLOADS",
        source_release="test-release",
        logical_table="people-a.csv",
        file_sha256="a" * 64,
        row_number=index + 2,
        row_sha256=f"{index + 1:064x}",
    )


def _person_report(
    pairs: list[tuple[str, str, str]],
) -> PersonCrosswalkReport:
    assertions: list[PersonNamespaceAssertion] = []
    for index, (retrosheet_id, mlbam_id, canonical_uuid) in enumerate(pairs):
        source_rows = (_identity_source_row(index),)
        for namespace, external_id in (
            ("MLBAM", mlbam_id),
            ("RETROSHEET", retrosheet_id),
        ):
            assertions.append(
                PersonNamespaceAssertion(
                    namespace=namespace,
                    external_id=external_id,
                    resolution_status=ResolutionStatus.EXACT,
                    canonical_chadwick_uuid=canonical_uuid,
                    source_rows=source_rows,
                    derivation_checksum="b" * 64,
                )
            )
    ordered = tuple(
        sorted(
            assertions,
            key=lambda row: (
                row.namespace,
                row.external_id,
                row.source_rows[0].logical_table,
                row.source_rows[0].row_number,
                row.source_rows[0].row_sha256,
            ),
        )
    )
    return PersonCrosswalkReport(
        chadwick_source_rows=len(pairs),
        chadwick_unique_uuids=len(pairs),
        lahman_source_rows=0,
        lahman_people=0,
        exact_count=0,
        unresolved_count=0,
        conflict_count=0,
        duplicate_identifier_claims=0,
        name_based_match_count=0,
        identifier_counts=(
            NamespaceCount("MLBAM", len(pairs)),
            NamespaceCount("RETROSHEET", len(pairs)),
        ),
        source_files=(),
        links=(),
        namespace_assertions=ordered,
    )


def _organization_report(
    team_codes: list[str],
) -> RetrosheetOrganizationCrosswalkReport:
    teams = tuple(
        RetrosheetTeamSeasonCrosswalk(
            season=2025,
            retrosheet_team_id=code,
            canonical_team_season_id=f"LAHMAN_TEAM_SEASON:2025:T{index:02d}",
            resolution_status=ResolutionStatus.EXACT,
            source_rows=(_identity_source_row(index),),
            derivation_checksum="c" * 64,
        )
        for index, code in enumerate(team_codes)
    )
    return RetrosheetOrganizationCrosswalkReport(
        team_season_count=len(teams),
        exact_team_season_count=len(teams),
        unresolved_team_season_count=0,
        conflict_team_season_count=0,
        park_count=0,
        exact_park_count=0,
        unresolved_park_count=0,
        conflict_park_count=0,
        unlinked_retrosheet_team_id_count=0,
        unlinked_lahman_park_count=0,
        name_based_match_count=0,
        source_files=(),
        team_seasons=teams,
        parks=(),
    )


def _team_mapping(index: int, entity_id: str) -> ExternalIdMapping:
    return ExternalIdMapping(
        entity_type="team",
        entity_id=UUID(entity_id),
        source=LAHMAN_TEAM_SEASON_MAPPING_SOURCE,
        external_id=f"LAHMAN_TEAM_SEASON:2025:T{index:02d}",
    )


def test_exact_crosswalk_builder_proves_actual_shaped_2430_mapping_contract() -> None:
    team_codes = [f"R{index:02d}" for index in range(30)]
    team_ids = [str(uuid4()) for _ in team_codes]
    player_pairs = [
        ("retro-home-player", "900001", str(uuid4())),
        ("retro-away-player", "900002", str(uuid4())),
    ]
    canonical_player_ids = [str(uuid4()), str(uuid4())]
    mappings = [
        *(_team_mapping(index, team_id) for index, team_id in enumerate(team_ids)),
        *(
            ExternalIdMapping(
                entity_type="player",
                entity_id=UUID(canonical_player_ids[index]),
                source="MLB_STATS_API",
                external_id=mlbam_id,
            )
            for index, (_, mlbam_id, _) in enumerate(player_pairs)
        ),
    ]
    asp_games: list[AspGameEvidence] = []
    retro_games: list[RetrosheetGameEvidence] = []
    game_index = 0
    for round_index in range(162):
        game_date = date(2025, 3, 1) + timedelta(days=round_index)
        for slot in range(15):
            home_index = (round_index + slot) % 30
            away_index = (round_index + 29 - slot) % 30
            raw_game_id = f"R{round_index:03d}{slot:02d}0"
            scheduled_at = datetime(
                game_date.year,
                game_date.month,
                game_date.day,
                17 + (slot % 4),
                tzinfo=UTC,
            )
            asp_games.append(
                AspGameEvidence(
                    game_id=f"asp-{game_index:04d}",
                    official_date=game_date,
                    scheduled_at=scheduled_at,
                    home_team_id=team_ids[home_index],
                    away_team_id=team_ids[away_index],
                    home_score=5,
                    away_score=3,
                    source_record_id=f"mlb-{game_index:04d}",
                    source_version_id="source-v1",
                    game_number=1,
                    home_starter_id=canonical_player_ids[0],
                    away_starter_id=canonical_player_ids[1],
                    home_lineup_ids=frozenset({canonical_player_ids[0]}),
                    away_lineup_ids=frozenset({canonical_player_ids[1]}),
                    batting_participant_ids=frozenset(canonical_player_ids),
                    pitching_participant_ids=frozenset(canonical_player_ids),
                    player_participant_ids=frozenset(canonical_player_ids),
                )
            )
            retro_games.append(
                RetrosheetGameEvidence(
                    raw_game_id=raw_game_id,
                    source_game_id=f"retrosheet:game:{raw_game_id}",
                    game_date=game_date,
                    home_team_raw_id=f"retrosheet:team:{team_codes[home_index]}",
                    away_team_raw_id=f"retrosheet:team:{team_codes[away_index]}",
                    game_number=1,
                    source_game_number=0,
                    park_raw_id="retrosheet:park:UNBRIDGED",
                    home_score=5,
                    away_score=3,
                    home_starter_raw_id=player_pairs[0][0],
                    away_starter_raw_id=player_pairs[1][0],
                    home_lineup_raw_ids=frozenset({player_pairs[0][0]}),
                    away_lineup_raw_ids=frozenset({player_pairs[1][0]}),
                    batting_participant_raw_ids=frozenset(
                        {player_pairs[0][0], player_pairs[1][0]}
                    ),
                    pitching_participant_raw_ids=frozenset(
                        {player_pairs[0][0], player_pairs[1][0]}
                    ),
                    player_participant_raw_ids=frozenset(
                        {player_pairs[0][0], player_pairs[1][0]}
                    ),
                )
            )
            game_index += 1

    build = build_2025_exact_identity_crosswalk(
        asp_games,
        retro_games,
        person_crosswalk=_person_report(player_pairs),
        organization_crosswalk=_organization_report(team_codes),
        canonical_external_mappings=mappings,
    )

    assert game_index == 2_430
    assert len(build.crosswalk.team_ids) == 30
    assert len(build.crosswalk.player_ids) == 2
    assert build.crosswalk.venue_ids == {}
    assert build.not_comparable_venue_raw_ids == ("retrosheet:park:UNBRIDGED",)
    assert build.as_dict()["name_based_match_count"] == 0

    result = reconcile_2025_retrosheet_to_asp(
        asp_games,
        retro_games,
        build.crosswalk,
    )
    assert result.complete_target_mapping is True
    assert result.matched_game_count == 2_430
    assert result.family_counts["venue"][ComparisonStatus.NOT_COMPARABLE.value] == 2_430
    assert result.conflict_count == 0


def test_schedule_bridge_maps_2430_and_preserves_score_conflict() -> None:
    asp_games, retro_games, raw_team_ids, asp_team_ids = (
        _schedule_fingerprint_population(score_conflict_index=137)
    )

    build = build_2025_schedule_fingerprint_crosswalk(asp_games, retro_games)

    assert len(build.crosswalk.team_ids) == 30
    assert len(build.game_ids) == 2_430
    assert build.score_match_game_count == 2_429
    assert build.score_conflict_game_count == 1
    assert build.crosswalk.team_ids == dict(
        zip(raw_team_ids, asp_team_ids, strict=True)
    )
    assert build.crosswalk.team_matching_method == (
        "EXACT_FULL_SEASON_SCHEDULE_FINGERPRINT_NO_NAMES"
    )
    assert build.as_dict(include_team_matches=False)["name_based_match_count"] == 0
    assert sum(row.score_conflict_game_count for row in build.team_matches) == 2
    assert len({row.schedule_fingerprint_sha256 for row in build.team_matches}) == 30

    result = reconcile_2025_retrosheet_to_asp(
        asp_games,
        retro_games,
        build.crosswalk,
    )

    assert result.complete_target_mapping is True
    assert result.matched_game_count == 2_430
    assert result.family_counts["final_score"][ComparisonStatus.CONFLICT.value] == 1
    conflict = next(
        row
        for row in result.games
        if row.comparisons["final_score"].status is ComparisonStatus.CONFLICT
    )
    assert conflict.asp.home_score + 1 == conflict.retrosheet.home_score


def test_actual_missing_number_distribution_normalizes_without_mutating_source() -> (
    None
):
    games: list[AspGameEvidence] = []
    first_day = date(2025, 1, 1)
    for index in range(2_374):
        game_date = first_day + timedelta(days=index % 300)
        games.append(
            AspGameEvidence(
                game_id=f"asp-single-{index:04d}",
                official_date=game_date,
                scheduled_at=datetime(
                    game_date.year,
                    game_date.month,
                    game_date.day,
                    18,
                    tzinfo=UTC,
                ),
                home_team_id=f"asp-single-home-{index:04d}",
                away_team_id=f"asp-single-away-{index:04d}",
                home_score=3,
                away_score=1,
                source_record_id=f"mlb-single-{index:04d}",
                source_version_id="asp-source-v1",
                game_number=None,
            )
        )
    doubleheader_first_day = date(2025, 11, 1)
    for index in range(28):
        game_date = doubleheader_first_day + timedelta(days=index)
        for ordinal, hour in ((1, 13), (2, 19)):
            games.append(
                AspGameEvidence(
                    game_id=f"asp-double-{index:02d}-{ordinal}",
                    official_date=game_date,
                    scheduled_at=datetime(
                        game_date.year,
                        game_date.month,
                        game_date.day,
                        hour,
                        tzinfo=UTC,
                    ),
                    home_team_id=f"asp-double-home-{index:02d}",
                    away_team_id=f"asp-double-away-{index:02d}",
                    home_score=ordinal + 2,
                    away_score=ordinal,
                    source_record_id=f"mlb-double-{index:02d}-{ordinal}",
                    source_version_id="asp-source-v1",
                    game_number=None,
                )
            )
    source_fields_before = tuple(
        (game.game_id, game.game_number, game.doubleheader_id) for game in games
    )

    normalization = normalize_asp_schedule_game_numbers(tuple(reversed(games)))
    repeated = normalize_asp_schedule_game_numbers(games)

    assert normalization.game_count == 2_430
    assert normalization.method_counts == {
        ASP_GAME_NUMBER_DERIVED_SINGLETON: 2_374,
        ASP_GAME_NUMBER_DERIVED_START_TIME: 56,
    }
    assert Counter(normalization.canonical_game_numbers.values()) == {
        1: 2_402,
        2: 28,
    }
    assert normalization.evidence_sha256 == repeated.evidence_sha256
    assert len(normalization.evidence_sha256) == 64
    assert normalization.as_dict()["source_explicit_count"] == 0
    assert normalization.as_dict()["source_missing_derived_count"] == 2_430
    assert tuple(
        (game.game_id, game.game_number, game.doubleheader_id) for game in games
    ) == source_fields_before


def test_schedule_bridge_uses_checksummed_missing_number_derivation() -> None:
    asp_first = _schedule_asp(
        "asp-first", DAY, "asp-home", "asp-away", game_number=None
    )
    asp_second = replace(
        _schedule_asp(
            "asp-second", DAY, "asp-home", "asp-away", game_number=None
        ),
        scheduled_at=datetime(2025, 6, 17, 23, tzinfo=UTC),
    )
    retro_first = _schedule_retro(
        "RETRO1",
        DAY,
        "retro-home",
        "retro-away",
        game_number=1,
        source_game_number=1,
        doubleheader_id="retro-dh",
    )
    retro_second = _schedule_retro(
        "RETRO2",
        DAY,
        "retro-home",
        "retro-away",
        game_number=2,
        source_game_number=2,
        doubleheader_id="retro-dh",
    )
    normalization = normalize_asp_schedule_game_numbers([asp_second, asp_first])

    build = build_2025_schedule_fingerprint_crosswalk(
        [asp_second, asp_first],
        [retro_second, retro_first],
        asp_game_number_normalization=normalization,
        expected_target_games=2,
        expected_team_count=2,
    )

    assert build.game_ids == {"RETRO1": "asp-first", "RETRO2": "asp-second"}
    assert build.asp_game_number_normalization is normalization
    normalized = build.as_dict(include_team_matches=False)[
        "asp_game_number_normalization"
    ]
    assert normalized["asp_source_rows_mutated"] is False
    assert normalized["method_counts"] == {
        ASP_GAME_NUMBER_DERIVED_START_TIME: 2
    }
    assert asp_first.game_number is None
    assert asp_second.game_number is None


def test_schedule_bridge_rejects_normalization_from_a_different_cohort() -> None:
    asp = _schedule_asp("asp-1", DAY, "asp-home", "asp-away", game_number=None)
    normalization = normalize_asp_schedule_game_numbers([asp])
    changed = replace(
        asp,
        scheduled_at=asp.scheduled_at + timedelta(minutes=1),
    )

    with pytest.raises(Fd1ReconciliationError, match="does not match the source cohort"):
        build_2025_schedule_fingerprint_crosswalk(
            [changed],
            [_schedule_retro("RETRO1", DAY, "retro-home", "retro-away")],
            asp_game_number_normalization=normalization,
            expected_target_games=1,
            expected_team_count=2,
        )


def test_missing_singleton_number_derives_canonical_one() -> None:
    game = _asp(game_number=None)

    normalization = normalize_asp_schedule_game_numbers([game])

    assert normalization.canonical_game_numbers == {game.game_id: 1}
    assert normalization.method_counts == {ASP_GAME_NUMBER_DERIVED_SINGLETON: 1}
    assert game.game_number is None


def test_explicit_game_number_is_preserved_and_labeled() -> None:
    game = _asp(game_number=1)

    normalization = normalize_asp_schedule_game_numbers([game])

    assert normalization.canonical_game_numbers == {game.game_id: 1}
    assert normalization.method_counts == {ASP_GAME_NUMBER_SOURCE_EXPLICIT: 1}


def test_game_number_normalization_rejects_mixed_availability() -> None:
    games = [
        _asp(
            game_id="asp-first",
            scheduled_at=datetime(2025, 6, 17, 17, tzinfo=UTC),
            game_number=1,
        ),
        _asp(
            game_id="asp-second",
            scheduled_at=datetime(2025, 6, 17, 23, tzinfo=UTC),
            game_number=None,
        ),
    ]

    with pytest.raises(Fd1ReconciliationError, match="mixed ASP game-number"):
        normalize_asp_schedule_game_numbers(games)


def test_game_number_normalization_rejects_tied_start_times() -> None:
    same_time = datetime(2025, 6, 17, 17, tzinfo=UTC)
    games = [
        _asp(game_id="asp-first", scheduled_at=same_time, game_number=None),
        _asp(game_id="asp-second", scheduled_at=same_time, game_number=None),
    ]

    with pytest.raises(Fd1ReconciliationError, match="start times are not unique"):
        normalize_asp_schedule_game_numbers(games)


def test_game_number_normalization_rejects_noncontiguous_explicit_numbers() -> None:
    games = [
        _asp(game_id="asp-first", game_number=1),
        _asp(
            game_id="asp-second",
            scheduled_at=datetime(2025, 6, 17, 23, tzinfo=UTC),
            game_number=3,
        ),
    ]

    with pytest.raises(Fd1ReconciliationError, match=r"expected \[1, 2\]"):
        normalize_asp_schedule_game_numbers(games)


def test_game_number_normalization_rejects_more_than_two_same_day_games() -> None:
    games = [
        _asp(
            game_id=f"asp-{index}",
            scheduled_at=datetime(2025, 6, 17, 12 + index, tzinfo=UTC),
            game_number=None,
        )
        for index in range(3)
    ]

    with pytest.raises(Fd1ReconciliationError, match="maximum is 2"):
        normalize_asp_schedule_game_numbers(games)


def test_schedule_fingerprint_builder_fails_on_team_signature_collision() -> None:
    asp_games = [
        _schedule_asp("asp-1", DAY, "asp-a", "asp-b"),
        _schedule_asp("asp-2", DAY, "asp-c", "asp-d"),
    ]
    retro_games = [
        _schedule_retro("R1", DAY, "retro-a", "retro-b"),
        _schedule_retro("R2", DAY, "retro-c", "retro-d"),
    ]

    with pytest.raises(
        Fd1ReconciliationError, match="ASP team schedule fingerprint collision"
    ):
        build_2025_schedule_fingerprint_crosswalk(
            asp_games,
            retro_games,
            expected_target_games=2,
            expected_team_count=4,
        )


def test_schedule_fingerprint_builder_fails_on_schedule_mismatch() -> None:
    second_day = DAY + timedelta(days=1)
    different_second_day = DAY + timedelta(days=2)
    asp_games = [
        _schedule_asp("asp-1", DAY, "asp-a", "asp-b"),
        _schedule_asp("asp-2", second_day, "asp-c", "asp-d"),
    ]
    retro_games = [
        _schedule_retro("R1", DAY, "retro-a", "retro-b"),
        _schedule_retro("R2", different_second_day, "retro-c", "retro-d"),
    ]

    with pytest.raises(Fd1ReconciliationError, match="fingerprint sets do not match"):
        build_2025_schedule_fingerprint_crosswalk(
            asp_games,
            retro_games,
            expected_target_games=2,
            expected_team_count=4,
        )


def test_schedule_fingerprint_builder_fails_on_opponent_mismatch() -> None:
    second_day = DAY + timedelta(days=1)
    third_day = DAY + timedelta(days=2)
    asp_games = [
        _schedule_asp("asp-1", DAY, "asp-a", "asp-b"),
        _schedule_asp("asp-2", DAY, "asp-c", "asp-d"),
        _schedule_asp("asp-3", second_day, "asp-a", "asp-c"),
        _schedule_asp("asp-4", second_day, "asp-d", "asp-b"),
        _schedule_asp("asp-5", third_day, "asp-a", "asp-d"),
        _schedule_asp("asp-6", third_day, "asp-b", "asp-c"),
    ]
    retro_games = [
        _schedule_retro("R1", DAY, "retro-a", "retro-d"),
        _schedule_retro("R2", DAY, "retro-c", "retro-b"),
        _schedule_retro("R3", second_day, "retro-a", "retro-c"),
        _schedule_retro("R4", second_day, "retro-d", "retro-b"),
        _schedule_retro("R5", third_day, "retro-a", "retro-d"),
        _schedule_retro("R6", third_day, "retro-b", "retro-c"),
    ]

    with pytest.raises(
        Fd1ReconciliationError,
        match="opponent-aware canonical schedule game keys do not match",
    ):
        build_2025_schedule_fingerprint_crosswalk(
            asp_games,
            retro_games,
            expected_target_games=6,
            expected_team_count=4,
        )


def test_schedule_fingerprint_builder_fails_on_duplicate_doubleheader_number() -> None:
    asp_games = [
        _schedule_asp("asp-1", DAY, "asp-a", "asp-b", game_number=1),
        _schedule_asp("asp-2", DAY, "asp-a", "asp-b", game_number=2),
    ]
    retro_games = [
        _schedule_retro(
            "R1",
            DAY,
            "retro-a",
            "retro-b",
            game_number=1,
            source_game_number=1,
            doubleheader_id="retro-dh",
        ),
        _schedule_retro(
            "R2",
            DAY,
            "retro-a",
            "retro-b",
            game_number=1,
            source_game_number=1,
            doubleheader_id="retro-dh",
        ),
    ]

    with pytest.raises(
        Fd1ReconciliationError,
        match="Retrosheet canonical game-number sequence",
    ):
        build_2025_schedule_fingerprint_crosswalk(
            asp_games,
            retro_games,
            expected_target_games=2,
            expected_team_count=2,
        )


def test_crosswalk_builder_fails_closed_when_canonical_team_mapping_is_missing() -> (
    None
):
    asp = _asp(venue_id=None)
    retro = RetrosheetGameEvidence(
        raw_game_id="HOM202506170",
        source_game_id="retrosheet:game:HOM202506170",
        game_date=DAY,
        home_team_raw_id="retrosheet:team:HOM",
        away_team_raw_id="retrosheet:team:AWY",
        game_number=1,
        source_game_number=0,
        home_score=5,
        away_score=3,
    )
    report = _organization_report(["HOM", "AWY"])
    mappings = [
        ExternalIdMapping(
            entity_type="team",
            entity_id="asp-home",
            source=LAHMAN_TEAM_SEASON_MAPPING_SOURCE,
            external_id="LAHMAN_TEAM_SEASON:2025:T00",
        )
    ]

    with pytest.raises(
        Fd1ReconciliationError,
        match="missing canonical Lahman team-season mapping",
    ):
        build_2025_exact_identity_crosswalk(
            [asp],
            [retro],
            person_crosswalk=_person_report([]),
            organization_crosswalk=report,
            canonical_external_mappings=mappings,
            expected_target_games=1,
        )


def test_crosswalk_builder_fails_closed_when_player_mapping_is_missing() -> None:
    asp = _asp(venue_id=None)
    retro = RetrosheetGameEvidence(
        raw_game_id="HOM202506170",
        source_game_id="retrosheet:game:HOM202506170",
        game_date=DAY,
        home_team_raw_id="retrosheet:team:HOM",
        away_team_raw_id="retrosheet:team:AWY",
        game_number=1,
        source_game_number=0,
        home_score=5,
        away_score=3,
        home_starter_raw_id="retro-home-player",
    )
    player_pair = ("retro-home-player", "900001", str(uuid4()))
    mappings = [
        ExternalIdMapping(
            entity_type="team",
            entity_id=team_id,
            source=LAHMAN_TEAM_SEASON_MAPPING_SOURCE,
            external_id=f"LAHMAN_TEAM_SEASON:2025:T{index:02d}",
        )
        for index, team_id in enumerate(("asp-home", "asp-away"))
    ]

    with pytest.raises(
        Fd1ReconciliationError,
        match="missing canonical MLBAM player mapping",
    ):
        build_2025_exact_identity_crosswalk(
            [asp],
            [retro],
            person_crosswalk=_person_report([player_pair]),
            organization_crosswalk=_organization_report(["HOM", "AWY"]),
            canonical_external_mappings=mappings,
            expected_target_games=1,
        )


def test_crosswalk_builder_rejects_ambiguous_canonical_mapping_rows() -> None:
    asp = _asp(venue_id=None)
    retro = RetrosheetGameEvidence(
        raw_game_id="HOM202506170",
        source_game_id="retrosheet:game:HOM202506170",
        game_date=DAY,
        home_team_raw_id="retrosheet:team:HOM",
        away_team_raw_id="retrosheet:team:AWY",
        game_number=1,
        source_game_number=0,
        home_score=5,
        away_score=3,
    )
    mappings = [
        ExternalIdMapping(
            entity_type="team",
            entity_id="asp-home",
            source=LAHMAN_TEAM_SEASON_MAPPING_SOURCE,
            external_id="LAHMAN_TEAM_SEASON:2025:T00",
        ),
        ExternalIdMapping(
            entity_type="team",
            entity_id="different-team",
            source=LAHMAN_TEAM_SEASON_MAPPING_SOURCE,
            external_id="LAHMAN_TEAM_SEASON:2025:T00",
        ),
    ]

    with pytest.raises(
        Fd1ReconciliationError,
        match="ambiguous canonical external mapping",
    ):
        build_2025_exact_identity_crosswalk(
            [asp],
            [retro],
            person_crosswalk=_person_report([]),
            organization_crosswalk=_organization_report(["HOM", "AWY"]),
            canonical_external_mappings=mappings,
            expected_target_games=1,
        )


def test_exact_reconciliation_is_json_ready_and_preserves_both_sources() -> None:
    asp = _asp()
    retro = _retro()
    result = reconcile_2025_retrosheet_to_asp(
        [asp], [retro], _crosswalk(), expected_target_games=1
    )

    assert result.complete_target_mapping is True
    assert result.conflict_count == 0
    assert result.matched_game_count == 1
    record = result.games[0]
    assert record.asp is asp
    assert record.retrosheet is retro
    assert all(
        comparison.status is ComparisonStatus.MATCH
        for comparison in record.comparisons.values()
    )
    document = result.as_dict()
    assert document["writes_performed"] == 0
    assert document["ambiguous_mapping_count"] == 0
    assert document["authority_policy"]["final_score"]["resolution"] == (
        "REPORT_CONFLICT_NEVER_OVERWRITE"
    )
    assert json.loads(result.to_json())["games"][0]["asp"]["home_score"] == 5


def test_conflicts_are_reported_without_overwriting_either_source() -> None:
    asp = _asp(home_score=5, away_score=3, venue_id="venue-1")
    retro = _retro(home_score=4, away_score=3)
    crosswalk = _crosswalk(venue="different-venue")

    result = reconcile_2025_retrosheet_to_asp(
        [asp], [retro], crosswalk, expected_target_games=1
    )
    record = result.games[0]

    assert record.comparisons["final_score"].status is ComparisonStatus.CONFLICT
    assert record.comparisons["venue"].status is ComparisonStatus.CONFLICT
    assert record.comparisons["final_score"].asp_value == {"home": 5, "away": 3}
    assert record.comparisons["final_score"].retrosheet_value == {
        "home": 4,
        "away": 3,
    }
    assert record.asp.home_score == 5
    assert record.retrosheet.home_score == 4


def test_exact_ordinal_disambiguates_doubleheader_when_asp_number_is_unavailable() -> (
    None
):
    asp_first = _asp(
        game_id="asp-first",
        scheduled_at=datetime(2025, 6, 17, 17, tzinfo=UTC),
        game_number=None,
    )
    asp_second = _asp(
        game_id="asp-second",
        scheduled_at=datetime(2025, 6, 17, 23, tzinfo=UTC),
        game_number=None,
    )
    retro_first = _retro(raw_game_id="HOM202506170", game_number=1)
    retro_second = _retro(raw_game_id="HOM202506172", game_number=2)

    result = reconcile_2025_retrosheet_to_asp(
        [asp_second, asp_first],
        [retro_second, retro_first],
        _crosswalk(),
        expected_target_games=2,
    )

    assert [row.asp.game_id for row in result.games] == ["asp-first", "asp-second"]
    assert [row.retrosheet.game_number for row in result.games] == [1, 2]
    assert result.mapping_method_counts == {
        "EXACT_DATE_HOME_AWAY_ORDINAL_START_ORDER": 2
    }
    assert all(
        row.comparisons["team_date_number"].status is ComparisonStatus.MATCH
        for row in result.games
    )


def test_ambiguous_doubleheader_fails_closed() -> None:
    same_time = datetime(2025, 6, 17, 17, tzinfo=UTC)
    asp_rows = [
        _asp(game_id="asp-a", scheduled_at=same_time, game_number=None),
        _asp(game_id="asp-b", scheduled_at=same_time, game_number=None),
    ]
    retro_rows = [
        _retro(raw_game_id="HOM202506170", game_number=1),
        _retro(raw_game_id="HOM202506172", game_number=2),
    ]

    with pytest.raises(Fd1ReconciliationError, match="start times are not unique"):
        reconcile_2025_retrosheet_to_asp(
            asp_rows, retro_rows, _crosswalk(), expected_target_games=2
        )


def test_missing_team_crosswalk_fails_before_game_matching() -> None:
    crosswalk = ExactIdentityCrosswalk(
        team_ids={"retro-home": "asp-home"}, player_ids={}, venue_ids={}
    )
    with pytest.raises(Fd1ReconciliationError, match="away team crosswalk"):
        reconcile_2025_retrosheet_to_asp(
            [_asp()], [_retro()], crosswalk, expected_target_games=1
        )


def test_materialized_row_adapter_builds_box_participation_and_final_state() -> None:
    common = {
        "retrosheet_game_id": "HOM202506170",
        "game_id": "retrosheet:game:HOM202506170",
        "game_date": "2025-06-17",
        "home_team_id": "retrosheet:team:HOM",
        "visiting_team_id": "retrosheet:team:VIS",
        "game_number": 1,
        "doubleheader_id": None,
        "park_id": "retrosheet:park:PARK1",
        "game_type": "regular",
    }
    play_rows = [
        {
            **common,
            "play_number": 1,
            "visiting_score_after": 1,
            "home_score_after": 0,
        },
        {
            **common,
            "play_number": 99,
            "visiting_score_after": 3,
            "home_score_after": 5,
        },
    ]
    starters = [
        {
            **common,
            "team_id": "retrosheet:team:HOM",
            "retrosheet_player_id": "homep001",
            "actual_starter_target": True,
        },
        {
            **common,
            "team_id": "retrosheet:team:VIS",
            "retrosheet_player_id": "visitp01",
            "actual_starter_target": True,
        },
    ]
    batting = [
        {
            **common,
            "team_id": "retrosheet:team:HOM",
            "retrosheet_player_id": "homeb001",
            "is_point_estimate": True,
            "actual_starting_lineup": True,
        },
        {
            **common,
            "team_id": "retrosheet:team:VIS",
            "retrosheet_player_id": "visitb01",
            "is_point_estimate": True,
            "actual_starting_lineup": True,
        },
    ]
    pitching = [
        {
            **common,
            "team_id": "retrosheet:team:HOM",
            "retrosheet_player_id": "homep001",
            "is_point_estimate": True,
        },
        {
            **common,
            "team_id": "retrosheet:team:VIS",
            "retrosheet_player_id": "visitp01",
            "is_point_estimate": True,
        },
    ]

    games = retrosheet_evidence_from_materialized_rows(
        play_transition_rows=play_rows,
        starter_usage_rows=starters,
        player_game_batting_rows=batting,
        player_game_pitching_rows=pitching,
    )

    assert len(games) == 1
    game = games[0]
    assert (game.away_score, game.home_score) == (3, 5)
    assert game.game_number == 1
    assert game.source_game_number == 1
    assert game.home_starter_raw_id == "homep001"
    assert game.home_lineup_raw_ids == frozenset({"homeb001"})
    assert game.batting_participant_raw_ids == frozenset({"homeb001", "visitb01"})
    assert game.pitching_participant_raw_ids == frozenset({"homep001", "visitp01"})
    assert game.player_participant_raw_ids == frozenset(
        {"homeb001", "visitb01", "homep001", "visitp01"}
    )


def test_actual_2025_source_number_distribution_normalizes_only_single_games() -> None:
    rows: list[dict[str, object]] = []
    first_day = date(2025, 3, 18)
    for index in range(2_374):
        game_date = first_day + timedelta(days=index % 180)
        raw_game_id = f"SNG{index:04d}"
        rows.append(
            {
                "retrosheet_game_id": raw_game_id,
                "game_id": f"retrosheet:game:{raw_game_id}",
                "game_date": game_date.isoformat(),
                "home_team_id": f"retrosheet:team:H{index:04d}",
                "visiting_team_id": f"retrosheet:team:V{index:04d}",
                "game_number": 0,
                "doubleheader_id": None,
                "park_id": "retrosheet:park:PARK1",
                "game_type": "regular",
                "play_number": 1,
                "visiting_score_after": 0,
                "home_score_after": 1,
            }
        )
    doubleheader_first_day = date(2025, 8, 1)
    for index in range(28):
        game_date = doubleheader_first_day + timedelta(days=index)
        doubleheader_id = f"retrosheet:doubleheader:{game_date.isoformat()}:HOM"
        for source_number in (1, 2):
            raw_game_id = f"DHB{index:02d}{source_number}"
            rows.append(
                {
                    "retrosheet_game_id": raw_game_id,
                    "game_id": f"retrosheet:game:{raw_game_id}",
                    "game_date": game_date.isoformat(),
                    "home_team_id": "retrosheet:team:HOM",
                    "visiting_team_id": "retrosheet:team:VIS",
                    "game_number": source_number,
                    "doubleheader_id": doubleheader_id,
                    "park_id": "retrosheet:park:PARK1",
                    "game_type": "regular",
                    "play_number": 1,
                    "visiting_score_after": source_number,
                    "home_score_after": 0,
                }
            )

    games = retrosheet_evidence_from_materialized_rows(play_transition_rows=rows)

    assert len(games) == 2_430
    assert Counter(game.source_game_number for game in games) == {
        0: 2_374,
        1: 28,
        2: 28,
    }
    assert Counter(game.game_number for game in games) == {1: 2_402, 2: 28}
    singles = [game for game in games if game.source_game_number == 0]
    assert all(game.game_number == 1 for game in singles)
    assert all(game.doubleheader_id is None for game in singles)
    first_document = singles[0].as_dict()
    assert first_document["source_game_number"] == 0
    assert first_document["game_number_semantics"] == (
        "SOURCE_0_SINGLE_NORMALIZED_TO_CANONICAL_1"
    )


def test_duplicate_doubleheader_source_numbers_fail_closed_after_adapter() -> None:
    common = {
        "game_date": DAY.isoformat(),
        "home_team_id": "retrosheet:team:HOM",
        "visiting_team_id": "retrosheet:team:VIS",
        "game_number": 1,
        "doubleheader_id": "retrosheet:doubleheader:2025-06-17:HOM",
        "park_id": "retrosheet:park:PARK1",
        "game_type": "regular",
        "play_number": 1,
        "visiting_score_after": 3,
        "home_score_after": 5,
    }
    retro_rows = retrosheet_evidence_from_materialized_rows(
        play_transition_rows=[
            {
                **common,
                "retrosheet_game_id": "HOM202506171",
                "game_id": "retrosheet:game:HOM202506171",
            },
            {
                **common,
                "retrosheet_game_id": "HOM20250617X",
                "game_id": "retrosheet:game:HOM20250617X",
            },
        ]
    )
    asp_rows = [
        _asp(
            game_id="asp-first",
            scheduled_at=datetime(2025, 6, 17, 17, tzinfo=UTC),
            game_number=1,
        ),
        _asp(
            game_id="asp-second",
            scheduled_at=datetime(2025, 6, 17, 23, tzinfo=UTC),
            game_number=2,
        ),
    ]
    crosswalk = ExactIdentityCrosswalk(
        team_ids={
            "retrosheet:team:HOM": "asp-home",
            "retrosheet:team:VIS": "asp-away",
        },
        player_ids={},
        venue_ids={},
    )

    with pytest.raises(Fd1ReconciliationError, match="Retrosheet game numbers"):
        reconcile_2025_retrosheet_to_asp(
            asp_rows,
            retro_rows,
            crosswalk,
            expected_target_games=2,
        )


def test_database_loader_reads_latest_live_final_and_checks_canonical_sides(
    session,
) -> None:
    sport = Sport(code="FD1-REC", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="FD1-REC", name="MLB")
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        name="2025",
        starts_at=datetime(2025, 3, 1, tzinfo=UTC),
        ends_at=datetime(2025, 11, 15, tzinfo=UTC),
    )
    home = Team(league_id=league.id, name="Home", abbreviation="HOM")
    away = Team(league_id=league.id, name="Away", abbreviation="AWY")
    venue = Venue(name="Park", timezone="America/New_York")
    session.add_all([season, home, away, venue])
    session.flush()
    scheduled = datetime(2025, 6, 17, 23, 5, tzinfo=UTC)
    game = Game(
        league_id=league.id,
        season_id=season.id,
        venue_id=venue.id,
        starts_at=scheduled,
        status=GameStatus.FINAL,
        home_score=5,
        away_score=3,
        final_at=scheduled + timedelta(hours=3),
        source_mode="LIVE",
    )
    session.add(game)
    session.flush()
    session.add_all(
        [
            GameTeam(game_id=game.id, team_id=home.id, is_home=True, score=5),
            GameTeam(game_id=game.id, team_id=away.id, is_home=False, score=3),
            ExternalIdMapping(
                entity_type="game",
                entity_id=game.id,
                source="MLB_STATS_API",
                external_id="777001",
            ),
            ExternalIdMapping(
                entity_type="team",
                entity_id=home.id,
                source="MLB_STATS_API",
                external_id="100",
            ),
            ExternalIdMapping(
                entity_type="team",
                entity_id=away.id,
                source="MLB_STATS_API",
                external_id="200",
            ),
            ExternalIdMapping(
                entity_type="venue",
                entity_id=venue.id,
                source="MLB_STATS_API",
                external_id="300",
            ),
        ]
    )
    retrieved = datetime(2026, 8, 14, tzinfo=UTC)
    session.add(
        GameFinalOutcome(
            game_id=game.id,
            season_id=season.id,
            home_team_id=home.id,
            away_team_id=away.id,
            venue_id=venue.id,
            source_version_id=uuid4(),
            checkpoint_id=uuid4(),
            official_date=DAY,
            scheduled_at=scheduled,
            final_at=scheduled + timedelta(hours=3),
            completion_time_basis="AUTHORITATIVE_FINAL_AT",
            completion_time_source_field="fixture.finalAt",
            completion_time_method="fixture",
            completion_time_limitation="fixture",
            evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
            quality_tier="A",
            reconstructed_at=retrieved,
            home_score=5,
            away_score=3,
            outcome_status="FINAL",
            revision=1,
            official=True,
            source_record_id="777001",
            retrieved_at=retrieved,
            label_available_at=retrieved,
            availability_basis="ACTUAL_ACQUISITION_TIME_NOT_BACKDATED",
            payload_checksum="a" * 64,
            source_mode="LIVE",
            temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
            identity_checksum="b" * 64,
        )
    )
    session.flush()

    rows = load_asp_2025_live_evidence(session)

    assert len(rows) == 1
    row = rows[0]
    assert row.game_id == str(game.id)
    assert row.home_team_id == str(home.id)
    assert row.away_team_id == str(away.id)
    assert row.venue_id == str(venue.id)
    assert row.team_external_ids[str(home.id)] == ("100",)
    assert row.venue_external_ids == ("300",)
    assert row.home_starter_id is None
    assert row.home_lineup_ids is None
    assert row.player_participant_ids is None
