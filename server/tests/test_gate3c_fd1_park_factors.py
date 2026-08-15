from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Mapping

import pytest

from app.gen2.fd1_materializer import (
    MATERIALIZED_FAMILY_SCHEMA_VERSIONS,
    RETROSHEET_OPEN_DATA,
    RETROSHEET_SOURCE_KEY,
    FamilyStagingPayload,
)
from app.gen2.fd1_park_factors import (
    Fd1ParkFactorAccumulator,
    Fd1ParkFactorError,
    form_fd1_prior_period_park_factors,
)
from app.gen2.retrosheet import RETROSHEET_REQUIRED_FILENAMES


SOURCE_VERSION = "retrosheet-csv-through-2025"
SOURCE_FILE_CHECKSUMS = MappingProxyType(
    {
        filename: sha256(filename.encode("ascii")).hexdigest()
        for filename in RETROSHEET_REQUIRED_FILENAMES
    }
)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def _checksum(value: object) -> str:
    return sha256(
        json.dumps(
            _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()


def _game_rows(
    *,
    game_id: str,
    game_date: date,
    park: str,
    home_runs: int,
    completion_date: date | None = None,
    relocated_completion_park: str | None = None,
) -> list[dict[str, Any]]:
    assert home_runs >= 0
    raw_game_id = game_id.removeprefix("retrosheet:game:")
    suspended = completion_date is not None
    final_date = completion_date or game_date
    outcomes = ["HOME_RUN"] * home_runs or ["BIP_OUT"]
    rows: list[dict[str, Any]] = []
    score = 0
    for index, outcome in enumerate(outcomes, 1):
        event_date = (
            completion_date
            if completion_date is not None and index == len(outcomes)
            else game_date
        )
        event_park = (
            relocated_completion_park
            if event_date == completion_date and relocated_completion_park is not None
            else park
        )
        # A non-HR scoring play proves that a positive run rate cannot mask a
        # missing home-run sample in the adapter's fail-closed report.
        runs = int(outcome == "HOME_RUN" or home_runs == 0)
        row: dict[str, Any] = {
            "source_key": RETROSHEET_SOURCE_KEY,
            "source_mode": RETROSHEET_OPEN_DATA,
            "source_version_id": SOURCE_VERSION,
            "game_id": game_id,
            "retrosheet_game_id": raw_game_id,
            "game_date": game_date.isoformat(),
            "event_date": game_date.isoformat(),
            "season": game_date.year,
            "month": game_date.month,
            "park_id": f"retrosheet:park:{park}",
            "pbp_quality": "FULL",
            "game_suspended": suspended,
            "suspension_completion_date": (
                completion_date.isoformat() if completion_date is not None else None
            ),
            "final_available_date": final_date.isoformat(),
            "historical_reconstruction": True,
            "available_only_after_game_final": True,
            "future_evidence_allowed": False,
            "retrosheet_event_date": event_date.isoformat(),
            "retrosheet_event_site": event_park,
            "event_park_id": f"retrosheet:park:{event_park}",
            "resumed_segment": event_date != game_date,
            "event_site_changed": event_park != park,
            "play_id": f"retrosheet:play:{raw_game_id}:{index}",
            "plate_appearance_id": f"retrosheet:pa:{raw_game_id}:{index}",
            "play_number": index,
            "pa_terminal_token": "hr" if outcome == "HOME_RUN" else "othout",
            "pa_outcome": outcome,
            "visiting_score_before": score,
            "home_score_before": 0,
            "visiting_score_after": score + runs,
            "home_score_after": 0,
            "runs_scored": runs,
            "source_filename": "plays.csv",
            "source_row_number": index + 1,
            "source_row_checksum": _checksum({"game": game_id, "play": index}),
            "source_file_sha256": SOURCE_FILE_CHECKSUMS["plays.csv"],
        }
        row["row_checksum"] = _checksum(row)
        rows.append(row)
        score += runs
    return rows


def _payload(season: int, rows: list[dict[str, Any]]) -> FamilyStagingPayload:
    rows = sorted(
        rows,
        key=lambda row: (
            row["game_date"],
            row["game_id"],
            str(row["play_number"]),
        ),
    )
    month = int(rows[0]["month"])
    bundle_checksum = _checksum({"season": season, "month": month, "bundle": True})
    schema_version = MATERIALIZED_FAMILY_SCHEMA_VERSIONS["play_transition"]
    partition_checksum = _checksum(
        {
            "family": "play_transition",
            "season": season,
            "month": month,
            "schema_version": schema_version,
            "source_bundle_checksum": bundle_checksum,
            "rows": rows,
        }
    )
    dates = [date.fromisoformat(str(row["game_date"])) for row in rows]
    return FamilyStagingPayload(
        family="play_transition",
        season=season,
        month=month,
        schema_version=schema_version,
        publication_scope="COMPLETE_MONTH_PARTITION",
        rows=tuple(MappingProxyType(row) for row in rows),
        row_count=len(rows),
        game_count=len({row["game_id"] for row in rows}),
        plate_appearance_count=len({row["plate_appearance_id"] for row in rows}),
        player_count=0,
        min_event_date=min(dates),
        max_event_date=max(dates),
        source_file_checksums=SOURCE_FILE_CHECKSUMS,
        source_bundle_checksum=bundle_checksum,
        partition_checksum=partition_checksum,
        leakage_policy="POSTGAME_PRIORS_ONLY",
        quality_report=MappingProxyType({}),
    )


def _four_season_payloads() -> tuple[FamilyStagingPayload, ...]:
    payloads: list[FamilyStagingPayload] = []
    for season in range(2021, 2025):
        rows: list[dict[str, Any]] = []
        for park, homers in (("HITTER", 2), ("PITCHER", 1)):
            for game_number in (1, 2):
                game_date = date(season, 4, game_number)
                completion = (
                    date(2022, 8, 26)
                    if season == 2022 and park == "HITTER" and game_number == 1
                    else None
                )
                rows.extend(
                    _game_rows(
                        game_id=(
                            f"retrosheet:game:{'HIT' if park == 'HITTER' else 'PIT'}"
                            f"{season}04{game_number:02d}0"
                        ),
                        game_date=game_date,
                        park=park,
                        home_runs=homers,
                        completion_date=completion,
                    )
                )
        payloads.append(_payload(season, rows))
    return tuple(payloads)


def test_exact_materialized_transitions_form_prior_only_park_factors() -> None:
    payloads = _four_season_payloads()
    direct = form_fd1_prior_period_park_factors(
        payloads,
        minimum_venue_games=2,
    )
    accumulator = Fd1ParkFactorAccumulator(minimum_venue_games=2)
    forwarded: list[FamilyStagingPayload] = []
    for payload in payloads:
        accumulator.accept(payload)
        forwarded.append(payload)
    build = accumulator.finalize()

    assert forwarded == list(payloads)
    assert accumulator.finalized
    assert accumulator.accepted_payload_count == 4
    assert accumulator.transition_row_count == 24
    assert accumulator.complete_game_count == 16
    assert build == direct
    assert build.build_checksum == direct.build_checksum
    assert build.formable
    assert build.factor_count == 6
    assert build.transition_row_count == 24
    assert build.complete_game_count == 16
    assert len(build.observations) == 16
    suspended = next(
        row for row in build.observations if row.game_id == "retrosheet:game:HIT202204010"
    )
    assert suspended.completed_at == datetime(2022, 8, 27, tzinfo=UTC)
    assert suspended.eligible_at == datetime(2022, 8, 27, tzinfo=UTC)

    target_2023 = build.season(2023)
    assert target_2023.summary.eligible_prior_game_count == 8
    assert target_2023.summary.target_or_future_game_count_excluded == 8
    assert target_2023.summary.prior_seasons == (2021, 2022)
    assert target_2023.summary.factor_count == 2
    assert target_2023.summary.factor_source_game_count == 8
    assert target_2023.summary.factor_sample_coverage == 1.0
    assert all(
        game_id not in factor.source_game_ids
        for factor in target_2023.factors
        for game_id in (
            "retrosheet:game:HIT202304010",
            "retrosheet:game:HIT202404010",
        )
    )
    factors = {factor.venue_id: factor for factor in target_2023.factors}
    assert factors["retrosheet:park:HITTER"].home_run_factor > 1
    assert factors["retrosheet:park:PITCHER"].home_run_factor < 1
    assert len(build.build_checksum) == 64



def test_incremental_accumulator_rejects_reorder_duplicate_and_incomplete_month() -> None:
    first, second, *_ = _four_season_payloads()

    reordered = Fd1ParkFactorAccumulator(minimum_venue_games=1)
    reordered.accept(second)
    with pytest.raises(Fd1ParkFactorError, match="strictly chronological"):
        reordered.accept(first)

    duplicate = Fd1ParkFactorAccumulator(minimum_venue_games=1)
    duplicate.accept(first)
    with pytest.raises(Fd1ParkFactorError, match="duplicate play-transition partition"):
        duplicate.accept(first)

    incomplete = Fd1ParkFactorAccumulator(minimum_venue_games=1)
    with pytest.raises(Fd1ParkFactorError, match="not a complete monthly partition"):
        incomplete.accept(
            replace(first, publication_scope="PILOT_OR_PARTIAL_MONTH_STAGING")
        )


def test_incremental_accumulator_cannot_finalize_or_accept_twice() -> None:
    first = _four_season_payloads()[0]
    accumulator = Fd1ParkFactorAccumulator(minimum_venue_games=1)
    accumulator.accept(first)
    result = accumulator.finalize(target_seasons=(2022,))

    assert result.formable
    with pytest.raises(Fd1ParkFactorError, match="already finalized"):
        accumulator.finalize(target_seasons=(2022,))
    with pytest.raises(Fd1ParkFactorError, match="already finalized"):
        accumulator.accept(first)


def test_relocated_suspended_game_is_reported_not_misattributed() -> None:
    ordinary = _game_rows(
        game_id="retrosheet:game:ORD202104010",
        game_date=date(2021, 4, 1),
        park="ORIGINAL",
        home_runs=1,
    )
    relocated = _game_rows(
        game_id="retrosheet:game:SUS202104020",
        game_date=date(2021, 4, 2),
        park="ORIGINAL",
        home_runs=2,
        completion_date=date(2021, 8, 1),
        relocated_completion_park="ALTERNATE",
    )

    build = form_fd1_prior_period_park_factors(
        (_payload(2021, ordinary + relocated),),
        target_seasons=(2022,),
        minimum_venue_games=1,
    )

    assert build.formable
    assert build.complete_game_count == 2
    assert len(build.observations) == 1
    assert [row.game_id for row in build.excluded_multi_venue_games] == [
        "retrosheet:game:SUS202104020"
    ]
    summary = build.season(2022).summary
    assert summary.multi_venue_prior_game_count_excluded == 1
    assert summary.eligible_prior_game_count == 1
    assert summary.factor_source_game_count == 1


def test_zero_home_run_evidence_fails_closed_with_sample_report() -> None:
    payload = _payload(
        2021,
        _game_rows(
        game_id="retrosheet:game:NHR202104010",
            game_date=date(2021, 4, 1),
            park="TEST",
            home_runs=0,
        ),
    )

    build = form_fd1_prior_period_park_factors(
        (payload,),
        target_seasons=(2022,),
        minimum_venue_games=1,
    )

    summary = build.season(2022).summary
    assert not build.formable
    assert not summary.formable
    assert summary.eligible_prior_game_count == 1
    assert summary.eligible_prior_home_runs == 0
    assert summary.factor_count == 0
    assert summary.failure_reason == (
        "ELIGIBLE_PRIOR_PERIOD_HOME_RUN_EVIDENCE_IS_NOT_POSITIVE"
    )


def test_mutated_materialized_transition_is_rejected_before_fitting() -> None:
    payload = _payload(
        2021,
        _game_rows(
            game_id="retrosheet:game:TMP202104010",
            game_date=date(2021, 4, 1),
            park="TEST",
            home_runs=1,
        ),
    )
    object.__setattr__(payload, "rows", (MappingProxyType({**payload.rows[0], "runs_scored": 9}),))

    with pytest.raises(Fd1ParkFactorError, match="checksum"):
        form_fd1_prior_period_park_factors(
            (payload,),
            target_seasons=(2022,),
            minimum_venue_games=1,
        )
