"""Form leakage-safe park factors from governed FD1 Retrosheet transitions.

This module is deliberately an adapter, not another data source.  It accepts the exact
``play_transition`` staging payloads produced by :mod:`app.gen2.fd1_materializer`,
verifies their content and source lineage, reduces each completed game to one bounded
park observation, and delegates fitting to the existing governed implementation in
``context.fit_prior_period_park_factors``.

Retrosheet provides a date, rather than a governed final timestamp.  An observation is
therefore eligible at 00:00 UTC on the *following* date.  Completed suspended games use
their explicit completion date.  A suspended game played at more than one park cannot
be represented honestly by the one-venue ``ParkGameObservation`` contract and is
reported as excluded instead of being attributed to an arbitrary park.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from app.gen2.context import ParkFactor, ParkGameObservation, fit_prior_period_park_factors
from app.gen2.event_taxonomy import PlateAppearanceOutcome
from app.gen2.fd1_materializer import (
    MATERIALIZED_FAMILY_SCHEMA_VERSIONS,
    RETROSHEET_OPEN_DATA,
    RETROSHEET_SOURCE_KEY,
    FamilyStagingPayload,
    Fd1MaterializationError,
    verify_payload_checksum,
)
from app.gen2.retrosheet import (
    RETROSHEET_REQUIRED_FILENAMES,
    RetrosheetImportError,
    source_game_id,
    source_play_id,
)


PARK_FACTOR_ADAPTER_VERSION = "retrosheet_fd1_park_factor_adapter_v1"
DEFAULT_TARGET_SEASONS = (2023, 2024, 2025)
DEFAULT_MAXIMUM_PAYLOADS = 100
DEFAULT_MAXIMUM_TRANSITION_ROWS = 5_000_000
DEFAULT_MAXIMUM_GAMES = 20_000
DEFAULT_MAXIMUM_TRANSITIONS_PER_GAME = 1_000
_PLAY_TRANSITION_SCHEMA = MATERIALIZED_FAMILY_SCHEMA_VERSIONS["play_transition"]
_VALID_PBP_QUALITIES = frozenset({"FULL", "DEDUCED"})
_OUTCOMES = frozenset(outcome.value for outcome in PlateAppearanceOutcome)


class Fd1ParkFactorError(ValueError):
    """Raised when materialized park evidence is incomplete or not trustworthy."""


@dataclass(frozen=True, slots=True)
class ExcludedMultiVenueGame:
    """A completed game that the one-venue factor contract cannot represent."""

    game_id: str
    season: int
    official_date: date
    final_available_date: date
    event_park_ids: tuple[str, ...]

    @property
    def eligible_at(self) -> datetime:
        return _date_only_eligible_at(self.final_available_date)

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "season": self.season,
            "official_date": self.official_date.isoformat(),
            "final_available_date": self.final_available_date.isoformat(),
            "eligible_at": self.eligible_at.isoformat(),
            "event_park_ids": list(self.event_park_ids),
            "reason": "MULTI_VENUE_GAME_CANNOT_BE_ASSIGNED_TO_ONE_PARK",
        }


@dataclass(frozen=True, slots=True)
class ParkFactorSeasonSummary:
    """Auditable sample accounting for one target-season park-factor fit."""

    target_season: int
    computed_at: datetime
    complete_game_count: int
    target_or_future_game_count_excluded: int
    prior_game_count: int
    prior_not_yet_eligible_count: int
    multi_venue_prior_game_count_excluded: int
    eligible_prior_game_count: int
    eligible_prior_venue_count: int
    eligible_prior_total_runs: int
    eligible_prior_home_runs: int
    prior_seasons: tuple[int, ...]
    minimum_venue_games: int
    factor_count: int
    factor_source_game_count: int
    factor_sample_coverage: float
    formable: bool
    failure_reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_season": self.target_season,
            "computed_at": self.computed_at.isoformat(),
            "complete_game_count": self.complete_game_count,
            "target_or_future_game_count_excluded": (
                self.target_or_future_game_count_excluded
            ),
            "prior_game_count": self.prior_game_count,
            "prior_not_yet_eligible_count": self.prior_not_yet_eligible_count,
            "multi_venue_prior_game_count_excluded": (
                self.multi_venue_prior_game_count_excluded
            ),
            "eligible_prior_game_count": self.eligible_prior_game_count,
            "eligible_prior_venue_count": self.eligible_prior_venue_count,
            "eligible_prior_total_runs": self.eligible_prior_total_runs,
            "eligible_prior_home_runs": self.eligible_prior_home_runs,
            "prior_seasons": list(self.prior_seasons),
            "minimum_venue_games": self.minimum_venue_games,
            "factor_count": self.factor_count,
            "factor_source_game_count": self.factor_source_game_count,
            "factor_sample_coverage": self.factor_sample_coverage,
            "formable": self.formable,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True, slots=True)
class ParkFactorSeasonBuild:
    summary: ParkFactorSeasonSummary
    factors: tuple[ParkFactor, ...]


@dataclass(frozen=True, slots=True)
class Fd1ParkFactorBuild:
    """Provider-free park observations, fits, and their source accounting."""

    version: str
    source_version_id: str
    source_file_checksums: Mapping[str, str]
    source_bundle_checksums: tuple[str, ...]
    partition_checksums: tuple[str, ...]
    transition_row_count: int
    complete_game_count: int
    observations: tuple[ParkGameObservation, ...]
    excluded_multi_venue_games: tuple[ExcludedMultiVenueGame, ...]
    seasons: tuple[ParkFactorSeasonBuild, ...]
    build_checksum: str

    @property
    def formable(self) -> bool:
        return bool(self.seasons) and all(row.summary.formable for row in self.seasons)

    @property
    def factor_count(self) -> int:
        return sum(len(row.factors) for row in self.seasons)

    def season(self, target_season: int) -> ParkFactorSeasonBuild:
        for row in self.seasons:
            if row.summary.target_season == target_season:
                return row
        raise KeyError(target_season)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_version_id": self.source_version_id,
            "source_file_checksums": dict(self.source_file_checksums),
            "source_bundle_checksums": list(self.source_bundle_checksums),
            "partition_checksums": list(self.partition_checksums),
            "transition_row_count": self.transition_row_count,
            "complete_game_count": self.complete_game_count,
            "usable_game_observation_count": len(self.observations),
            "excluded_multi_venue_game_count": len(self.excluded_multi_venue_games),
            "excluded_multi_venue_games": [
                row.as_dict() for row in self.excluded_multi_venue_games
            ],
            "factor_count": self.factor_count,
            "formable": self.formable,
            "seasons": [row.summary.as_dict() for row in self.seasons],
            "build_checksum": self.build_checksum,
        }


@dataclass(slots=True)
class _GameRows:
    game_id: str
    rows: list[Mapping[str, Any]]
    partition_checksum: str


class Fd1ParkFactorAccumulator:
    """Bounded one-pass accumulator for publication-time transition batches.

    ``accept`` verifies and reduces one complete monthly partition without retaining the
    payload.  Callers may therefore pass the same object onward to the warehouse writer
    immediately.  Accepted periods must be strictly chronological; a failed accumulator
    is poisoned, and a finalized accumulator cannot be reused.
    """

    def __init__(
        self,
        *,
        prior_strength_games: float = 50.0,
        minimum_venue_games: int = 10,
        maximum_payloads: int = DEFAULT_MAXIMUM_PAYLOADS,
        maximum_transition_rows: int = DEFAULT_MAXIMUM_TRANSITION_ROWS,
        maximum_games: int = DEFAULT_MAXIMUM_GAMES,
        maximum_transitions_per_game: int = DEFAULT_MAXIMUM_TRANSITIONS_PER_GAME,
    ) -> None:
        _positive_limit(maximum_payloads, "maximum_payloads")
        _positive_limit(maximum_transition_rows, "maximum_transition_rows")
        _positive_limit(maximum_games, "maximum_games")
        _positive_limit(maximum_transitions_per_game, "maximum_transitions_per_game")
        if minimum_venue_games <= 0:
            raise Fd1ParkFactorError("minimum_venue_games must be positive")
        if not isfinite(prior_strength_games) or prior_strength_games <= 0:
            raise Fd1ParkFactorError("prior_strength_games must be finite and positive")
        self._prior_strength_games = prior_strength_games
        self._minimum_venue_games = minimum_venue_games
        self._maximum_payloads = maximum_payloads
        self._maximum_transition_rows = maximum_transition_rows
        self._maximum_games = maximum_games
        self._maximum_transitions_per_game = maximum_transitions_per_game
        self._expected_file_checksums: dict[str, str] | None = None
        self._source_version_id: str | None = None
        self._source_bundle_checksums: set[str] = set()
        self._partition_checksums: list[str] = []
        self._partition_keys: set[tuple[str, int, int]] = set()
        self._last_period: tuple[int, int] | None = None
        self._seen_games: set[str] = set()
        self._observations: list[ParkGameObservation] = []
        self._excluded: list[ExcludedMultiVenueGame] = []
        self._transition_row_count = 0
        self._complete_game_count = 0
        self._payload_count = 0
        self._failed = False
        self._finalized = False

    @property
    def accepted_payload_count(self) -> int:
        return self._payload_count

    @property
    def transition_row_count(self) -> int:
        return self._transition_row_count

    @property
    def complete_game_count(self) -> int:
        return self._complete_game_count

    @property
    def finalized(self) -> bool:
        return self._finalized

    def accept(self, payload: FamilyStagingPayload) -> None:
        """Consume one verified complete-month play-transition partition."""

        if self._finalized:
            raise Fd1ParkFactorError("park-factor accumulator is already finalized")
        if self._failed:
            raise Fd1ParkFactorError("park-factor accumulator is poisoned after failure")
        try:
            self._accept(payload)
        except Exception:
            self._failed = True
            raise

    def _accept(self, payload: FamilyStagingPayload) -> None:
        _validate_payload_contract(payload)
        if payload.partition_key in self._partition_keys:
            raise Fd1ParkFactorError(
                f"duplicate play-transition partition {payload.partition_key!r}"
            )
        period = (payload.season, payload.month)
        if self._last_period is not None and period <= self._last_period:
            raise Fd1ParkFactorError(
                "play-transition partitions must be accepted in strictly "
                f"chronological order; got {period!r} after {self._last_period!r}"
            )
        if self._payload_count + 1 > self._maximum_payloads:
            raise Fd1ParkFactorError(
                f"payload count exceeds governed maximum {self._maximum_payloads}"
            )
        checksums = _validated_file_checksums(payload.source_file_checksums)
        if self._expected_file_checksums is None:
            self._expected_file_checksums = checksums
        elif checksums != self._expected_file_checksums:
            raise Fd1ParkFactorError(
                "play-transition partitions do not share one immutable source release"
            )
        try:
            verify_payload_checksum(payload)
        except Fd1MaterializationError as exc:
            raise Fd1ParkFactorError(str(exc)) from exc

        batch_game_count = 0
        batch_dates: list[date] = []
        current: _GameRows | None = None
        closed_in_payload: set[str] = set()
        for row in payload.rows:
            self._transition_row_count += 1
            if self._transition_row_count > self._maximum_transition_rows:
                raise Fd1ParkFactorError(
                    "transition row count exceeds governed maximum "
                    f"{self._maximum_transition_rows}"
                )
            row_game_id = _required_string(row, "game_id")
            if current is None or current.game_id != row_game_id:
                if current is not None:
                    game_date = self._accept_game(current, payload)
                    closed_in_payload.add(current.game_id)
                    batch_game_count += 1
                    batch_dates.append(game_date)
                if row_game_id in closed_in_payload:
                    raise Fd1ParkFactorError(
                        f"game {row_game_id!r} is not contiguous within its partition"
                    )
                current = _GameRows(row_game_id, [], payload.partition_checksum)
            current.rows.append(row)
            if len(current.rows) > self._maximum_transitions_per_game:
                raise Fd1ParkFactorError(
                    f"game {row_game_id!r} exceeds governed transition maximum "
                    f"{self._maximum_transitions_per_game}"
                )
        if current is not None:
            batch_dates.append(self._accept_game(current, payload))
            batch_game_count += 1
        if batch_game_count != payload.game_count:
            raise Fd1ParkFactorError(
                f"{payload.partition_key!r} game count does not reconcile "
                f"({batch_game_count}!={payload.game_count})"
            )
        if not batch_dates:
            raise Fd1ParkFactorError("empty play-transition payload is not allowed")
        if min(batch_dates) != payload.min_event_date or max(batch_dates) != payload.max_event_date:
            raise Fd1ParkFactorError(
                f"{payload.partition_key!r} event-date bounds do not reconcile"
            )
        self._source_bundle_checksums.add(
            _sha(payload.source_bundle_checksum, "source bundle")
        )
        self._partition_checksums.append(_sha(payload.partition_checksum, "partition"))
        self._partition_keys.add(payload.partition_key)
        self._last_period = period
        self._payload_count += 1

    def _accept_game(self, game: _GameRows, payload: FamilyStagingPayload) -> date:
        assert self._expected_file_checksums is not None
        observation, rejection, row_source_version, game_date = _reduce_game(
            game,
            self._expected_file_checksums,
            maximum_transitions_per_game=self._maximum_transitions_per_game,
        )
        if game_date.year != payload.season or game_date.month != payload.month:
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} is assigned to the wrong partition"
            )
        self._source_version_id = _merge_source_version(
            self._source_version_id, row_source_version
        )
        _append_game_result(
            game.game_id,
            observation,
            rejection,
            seen_games=self._seen_games,
            observations=self._observations,
            excluded=self._excluded,
            maximum_games=self._maximum_games,
        )
        self._complete_game_count += 1
        return game_date

    def finalize(
        self,
        *,
        target_seasons: Iterable[int] = DEFAULT_TARGET_SEASONS,
    ) -> Fd1ParkFactorBuild:
        """Close the accumulator and return prior-period factors and accounting."""

        if self._finalized:
            raise Fd1ParkFactorError("park-factor accumulator is already finalized")
        if self._failed:
            raise Fd1ParkFactorError("park-factor accumulator is poisoned after failure")
        targets = _target_seasons(target_seasons)
        if (
            self._payload_count == 0
            or self._expected_file_checksums is None
            or self._source_version_id is None
        ):
            raise Fd1ParkFactorError("at least one play-transition payload is required")
        ordered_observations = tuple(
            sorted(self._observations, key=lambda row: (row.official_date, row.game_id))
        )
        ordered_excluded = tuple(
            sorted(self._excluded, key=lambda row: (row.official_date, row.game_id))
        )
        season_builds = tuple(
            _fit_target_season(
                target,
                ordered_observations,
                ordered_excluded,
                complete_game_count=self._complete_game_count,
                prior_strength_games=self._prior_strength_games,
                minimum_venue_games=self._minimum_venue_games,
            )
            for target in targets
        )
        build_document = {
            "version": PARK_FACTOR_ADAPTER_VERSION,
            "source_version_id": self._source_version_id,
            "source_file_checksums": self._expected_file_checksums,
            "source_bundle_checksums": sorted(self._source_bundle_checksums),
            "partition_checksums": sorted(self._partition_checksums),
            "transition_row_count": self._transition_row_count,
            "complete_game_count": self._complete_game_count,
            "observations": [
                {
                    "game_id": row.game_id,
                    "venue_id": row.venue_id,
                    "season": row.season,
                    "official_date": row.official_date.isoformat(),
                    "completed_at": row.completed_at.isoformat(),
                    "eligible_at": row.eligible_at.isoformat(),
                    "total_runs": row.total_runs,
                    "home_runs": row.home_runs,
                    "evidence_checksum": row.evidence_checksum,
                }
                for row in ordered_observations
            ],
            "excluded_multi_venue_games": [row.as_dict() for row in ordered_excluded],
            "seasons": [
                {
                    **row.summary.as_dict(),
                    "factor_identity_checksums": [
                        factor.identity_checksum for factor in row.factors
                    ],
                }
                for row in season_builds
            ],
        }
        result = Fd1ParkFactorBuild(
            version=PARK_FACTOR_ADAPTER_VERSION,
            source_version_id=self._source_version_id,
            source_file_checksums=MappingProxyType(
                dict(self._expected_file_checksums)
            ),
            source_bundle_checksums=tuple(sorted(self._source_bundle_checksums)),
            partition_checksums=tuple(sorted(self._partition_checksums)),
            transition_row_count=self._transition_row_count,
            complete_game_count=self._complete_game_count,
            observations=ordered_observations,
            excluded_multi_venue_games=ordered_excluded,
            seasons=season_builds,
            build_checksum=_checksum(build_document),
        )
        self._finalized = True
        return result


def form_fd1_prior_period_park_factors(
    payloads: Iterable[FamilyStagingPayload],
    *,
    target_seasons: Iterable[int] = DEFAULT_TARGET_SEASONS,
    prior_strength_games: float = 50.0,
    minimum_venue_games: int = 10,
    maximum_payloads: int = DEFAULT_MAXIMUM_PAYLOADS,
    maximum_transition_rows: int = DEFAULT_MAXIMUM_TRANSITION_ROWS,
    maximum_games: int = DEFAULT_MAXIMUM_GAMES,
    maximum_transitions_per_game: int = DEFAULT_MAXIMUM_TRANSITIONS_PER_GAME,
) -> Fd1ParkFactorBuild:
    """Verify chronological FD1 transitions and fit prior-season park factors."""

    accumulator = Fd1ParkFactorAccumulator(
        prior_strength_games=prior_strength_games,
        minimum_venue_games=minimum_venue_games,
        maximum_payloads=maximum_payloads,
        maximum_transition_rows=maximum_transition_rows,
        maximum_games=maximum_games,
        maximum_transitions_per_game=maximum_transitions_per_game,
    )
    for payload in payloads:
        accumulator.accept(payload)
    return accumulator.finalize(target_seasons=target_seasons)


def _validate_payload_contract(payload: FamilyStagingPayload) -> None:
    if payload.family != "play_transition":
        raise Fd1ParkFactorError(
            f"park adapter requires play_transition payloads, got {payload.family!r}"
        )
    if payload.schema_version != _PLAY_TRANSITION_SCHEMA:
        raise Fd1ParkFactorError(
            f"unsupported play-transition schema {payload.schema_version!r}"
        )
    if payload.publication_scope != "COMPLETE_MONTH_PARTITION":
        raise Fd1ParkFactorError(
            f"{payload.partition_key!r} is not a complete monthly partition"
        )
    if payload.row_count != len(payload.rows) or payload.row_count <= 0:
        raise Fd1ParkFactorError(
            f"{payload.partition_key!r} row count does not reconcile"
        )
    if payload.game_count <= 0:
        raise Fd1ParkFactorError(
            f"{payload.partition_key!r} must contain at least one game"
        )


def _validated_file_checksums(values: Mapping[str, str]) -> dict[str, str]:
    expected = set(RETROSHEET_REQUIRED_FILENAMES)
    if set(values) != expected:
        missing = sorted(expected - set(values))
        extra = sorted(set(values) - expected)
        raise Fd1ParkFactorError(
            "source file checksum inventory must exactly match the Retrosheet bundle; "
            f"missing={missing}, extra={extra}"
        )
    return {name: _sha(values[name], f"{name} source file") for name in sorted(values)}


def _reduce_game(
    game: _GameRows,
    source_file_checksums: Mapping[str, str],
    *,
    maximum_transitions_per_game: int,
) -> tuple[
    ParkGameObservation | None,
    ExcludedMultiVenueGame | None,
    str,
    date,
]:
    if not game.rows:
        raise Fd1ParkFactorError(f"game {game.game_id!r} has no transitions")
    if len(game.rows) > maximum_transitions_per_game:
        raise Fd1ParkFactorError(
            f"game {game.game_id!r} exceeds governed transition maximum"
        )
    first = game.rows[0]
    source_version_id = _required_string(first, "source_version_id")
    raw_game_id = _required_string(first, "retrosheet_game_id")
    try:
        expected_game_id = source_game_id(raw_game_id)
    except RetrosheetImportError as exc:
        raise Fd1ParkFactorError(
            f"game {game.game_id!r} has an invalid Retrosheet identity"
        ) from exc
    if game.game_id != expected_game_id:
        raise Fd1ParkFactorError(
            f"game {game.game_id!r} does not match its Retrosheet source identity"
        )
    season = _required_int(first, "season", minimum=1876)
    game_date = _required_date(first, "game_date")
    if game_date.year != season:
        raise Fd1ParkFactorError(
            f"game {game.game_id!r} season does not match its official date"
        )
    park_id = _required_string(first, "park_id")
    game_suspended = _required_bool(first, "game_suspended")
    completion_date = _optional_date(first, "suspension_completion_date")
    final_available_date = _required_date(first, "final_available_date")
    if game_suspended:
        if completion_date is None or completion_date <= game_date:
            raise Fd1ParkFactorError(
                f"suspended game {game.game_id!r} lacks a later completion date"
            )
        if final_available_date != completion_date:
            raise Fd1ParkFactorError(
                f"suspended game {game.game_id!r} final eligibility is not its completion"
            )
    elif completion_date is not None or final_available_date != game_date:
        raise Fd1ParkFactorError(
            f"ordinary game {game.game_id!r} has contradictory completion eligibility"
        )

    common_fields = (
        "source_key",
        "source_mode",
        "source_version_id",
        "game_id",
        "retrosheet_game_id",
        "game_date",
        "season",
        "month",
        "park_id",
        "pbp_quality",
        "game_suspended",
        "suspension_completion_date",
        "final_available_date",
        "historical_reconstruction",
        "available_only_after_game_final",
        "future_evidence_allowed",
    )
    event_park_ids: set[str] = set()
    transition_rows: list[tuple[int, Mapping[str, Any]]] = []
    row_checksums: list[str] = []
    terminal_pa_ids: set[str] = set()
    home_runs = 0
    completion_segment_present = False
    for row in game.rows:
        for field in common_fields:
            if row.get(field) != first.get(field):
                raise Fd1ParkFactorError(
                    f"game {game.game_id!r} has conflicting {field!r} values"
                )
        if row.get("source_key") != RETROSHEET_SOURCE_KEY:
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} has the wrong source key"
            )
        if row.get("source_mode") != RETROSHEET_OPEN_DATA:
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} is not Retrosheet open-data evidence"
            )
        if row.get("pbp_quality") not in _VALID_PBP_QUALITIES:
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} lacks FULL/DEDUCED play-by-play evidence"
            )
        if row.get("historical_reconstruction") is not True:
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} is not labeled historical reconstruction"
            )
        if row.get("available_only_after_game_final") is not True:
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} is missing post-final eligibility semantics"
            )
        if row.get("future_evidence_allowed") is not False:
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} permits future evidence"
            )
        if _required_int(row, "month", minimum=1) != game_date.month:
            raise Fd1ParkFactorError(f"game {game.game_id!r} month is inconsistent")
        if _required_string(row, "source_filename") != "plays.csv":
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} transition lacks plays.csv lineage"
            )
        if (
            _sha(_required_string(row, "source_file_sha256"), "source_file_sha256")
            != source_file_checksums["plays.csv"]
        ):
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} source-file checksum is not release-bound"
            )
        _sha(_required_string(row, "source_row_checksum"), "source_row_checksum")
        row_checksums.append(_sha(_required_string(row, "row_checksum"), "row_checksum"))
        _required_int(row, "source_row_number", minimum=2)

        event_date = _required_date(row, "retrosheet_event_date")
        event_site = _required_string(row, "retrosheet_event_site")
        event_park_id = _required_string(row, "event_park_id")
        if event_park_id != f"retrosheet:park:{event_site}":
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} event park does not match its source site"
            )
        event_park_ids.add(event_park_id)
        resumed = _required_bool(row, "resumed_segment")
        site_changed = _required_bool(row, "event_site_changed")
        if resumed != (event_date != game_date):
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} has contradictory resumed-segment labeling"
            )
        if site_changed != (event_park_id != park_id):
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} has contradictory event-site labeling"
            )
        if game_suspended:
            if event_date not in {game_date, completion_date}:
                raise Fd1ParkFactorError(
                    f"suspended game {game.game_id!r} has an ungoverned event date"
                )
            completion_segment_present |= event_date == completion_date
        elif event_date != game_date:
            raise Fd1ParkFactorError(
                f"ordinary game {game.game_id!r} contains a resumed segment"
            )

        play_number = _required_int(row, "play_number", minimum=1)
        try:
            expected_play_id = source_play_id(raw_game_id, play_number)
        except RetrosheetImportError as exc:
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} has an invalid play identity"
            ) from exc
        if _required_string(row, "play_id") != expected_play_id:
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} play identity does not match its source row"
            )
        transition_rows.append((play_number, row))
        token = row.get("pa_terminal_token")
        outcome = row.get("pa_outcome")
        if token is None:
            if outcome is not None:
                raise Fd1ParkFactorError(
                    f"game {game.game_id!r} has an outcome on a non-terminal play"
                )
        else:
            if not str(token).strip() or outcome not in _OUTCOMES:
                raise Fd1ParkFactorError(
                    f"game {game.game_id!r} lacks explicit terminal outcome evidence"
                )
            pa_id = _required_string(row, "plate_appearance_id")
            if pa_id in terminal_pa_ids:
                raise Fd1ParkFactorError(
                    f"game {game.game_id!r} repeats terminal plate appearance {pa_id!r}"
                )
            terminal_pa_ids.add(pa_id)
            home_runs += outcome == PlateAppearanceOutcome.HOME_RUN.value

    if game_suspended and not completion_segment_present:
        raise Fd1ParkFactorError(
            f"suspended game {game.game_id!r} has no completion-segment evidence"
        )
    if not terminal_pa_ids:
        raise Fd1ParkFactorError(
            f"game {game.game_id!r} has no explicit plate-appearance outcomes"
        )
    transition_rows.sort(key=lambda item: item[0])
    numbers = [number for number, _ in transition_rows]
    if len(numbers) != len(set(numbers)):
        raise Fd1ParkFactorError(f"game {game.game_id!r} repeats a play number")
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        raise Fd1ParkFactorError(
            f"game {game.game_id!r} has non-contiguous play-transition evidence"
        )
    prior_scores: tuple[int, int] | None = None
    for _, row in transition_rows:
        before = (
            _required_int(row, "visiting_score_before", minimum=0),
            _required_int(row, "home_score_before", minimum=0),
        )
        after = (
            _required_int(row, "visiting_score_after", minimum=0),
            _required_int(row, "home_score_after", minimum=0),
        )
        if prior_scores is not None and before != prior_scores:
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} has a discontinuous score transition"
            )
        if after[0] < before[0] or after[1] < before[1]:
            raise Fd1ParkFactorError(f"game {game.game_id!r} score decreases")
        if _required_int(row, "runs_scored", minimum=0) != (
            after[0] + after[1] - before[0] - before[1]
        ):
            raise Fd1ParkFactorError(
                f"game {game.game_id!r} run transition does not conserve score"
            )
        prior_scores = after
    assert prior_scores is not None
    total_runs = prior_scores[0] + prior_scores[1]
    if home_runs > total_runs:
        raise Fd1ParkFactorError(
            f"game {game.game_id!r} home-run count exceeds total scoring"
        )
    if len(event_park_ids) != 1 or next(iter(event_park_ids)) != park_id:
        return (
            None,
            ExcludedMultiVenueGame(
                game_id=game.game_id,
                season=season,
                official_date=game_date,
                final_available_date=final_available_date,
                event_park_ids=tuple(sorted(event_park_ids)),
            ),
            source_version_id,
            game_date,
        )
    eligible_at = _date_only_eligible_at(final_available_date)
    evidence_checksum = _checksum(
        {
            "adapter_version": PARK_FACTOR_ADAPTER_VERSION,
            "source_key": RETROSHEET_SOURCE_KEY,
            "source_version_id": source_version_id,
            "source_file_sha256": source_file_checksums["plays.csv"],
            "partition_checksum": game.partition_checksum,
            "game_id": game.game_id,
            "row_checksums": row_checksums,
        }
    )
    return (
        ParkGameObservation(
            game_id=game.game_id,
            venue_id=park_id,
            season=season,
            official_date=game_date,
            completed_at=eligible_at,
            eligible_at=eligible_at,
            total_runs=total_runs,
            home_runs=home_runs,
            evidence_checksum=evidence_checksum,
        ),
        None,
        source_version_id,
        game_date,
    )


def _append_game_result(
    game_id: str,
    observation: ParkGameObservation | None,
    rejection: ExcludedMultiVenueGame | None,
    *,
    seen_games: set[str],
    observations: list[ParkGameObservation],
    excluded: list[ExcludedMultiVenueGame],
    maximum_games: int,
) -> None:
    if game_id in seen_games:
        raise Fd1ParkFactorError(f"game {game_id!r} appears in multiple partitions")
    seen_games.add(game_id)
    if len(seen_games) > maximum_games:
        raise Fd1ParkFactorError(f"game count exceeds governed maximum {maximum_games}")
    if (observation is None) == (rejection is None):
        raise AssertionError("a reduced game must be either usable or explicitly excluded")
    if observation is not None:
        observations.append(observation)
    else:
        assert rejection is not None
        excluded.append(rejection)


def _fit_target_season(
    target_season: int,
    observations: Sequence[ParkGameObservation],
    excluded: Sequence[ExcludedMultiVenueGame],
    *,
    complete_game_count: int,
    prior_strength_games: float,
    minimum_venue_games: int,
) -> ParkFactorSeasonBuild:
    computed_at = datetime(target_season, 1, 1, tzinfo=UTC)
    prior = tuple(row for row in observations if row.season < target_season)
    eligible = tuple(
        row
        for row in prior
        if row.completed_at < computed_at and row.eligible_at <= computed_at
    )
    prior_excluded = tuple(row for row in excluded if row.season < target_season)
    eligible_excluded = tuple(
        row
        for row in prior_excluded
        if row.eligible_at < computed_at
    )
    target_or_future = sum(row.season >= target_season for row in observations) + sum(
        row.season >= target_season for row in excluded
    )
    prior_count = len(prior) + len(prior_excluded)
    prior_not_eligible = (len(prior) - len(eligible)) + (
        len(prior_excluded) - len(eligible_excluded)
    )
    venues = {row.venue_id for row in eligible}
    total_runs = sum(row.total_runs for row in eligible)
    home_runs = sum(row.home_runs for row in eligible)
    factors: tuple[ParkFactor, ...] = ()
    failure: str | None = None
    if not eligible:
        failure = "NO_ELIGIBLE_PRIOR_PERIOD_GAME_OBSERVATIONS"
    elif total_runs <= 0:
        failure = "ELIGIBLE_PRIOR_PERIOD_RUN_RATE_IS_NOT_POSITIVE"
    elif home_runs <= 0:
        failure = "ELIGIBLE_PRIOR_PERIOD_HOME_RUN_EVIDENCE_IS_NOT_POSITIVE"
    else:
        venue_counts = {
            venue: sum(row.venue_id == venue for row in eligible) for venue in venues
        }
        if not any(count >= minimum_venue_games for count in venue_counts.values()):
            failure = "NO_VENUE_MEETS_GOVERNED_MINIMUM_GAME_SAMPLE"
        else:
            factors = fit_prior_period_park_factors(
                observations,
                target_season=target_season,
                computed_at=computed_at,
                prior_strength_games=prior_strength_games,
                minimum_venue_games=minimum_venue_games,
            )
    factor_game_ids = {
        game_id for factor in factors for game_id in factor.source_game_ids
    }
    eligible_ids = {row.game_id for row in eligible}
    if not factor_game_ids <= eligible_ids:
        raise Fd1ParkFactorError(
            f"target {target_season} factor fit consumed non-prior evidence"
        )
    for factor in factors:
        if factor.target_season != target_season or any(
            season >= target_season for season in factor.prior_seasons
        ):
            raise Fd1ParkFactorError(
                f"target {target_season} factor contains target/future evidence"
            )
    coverage = len(factor_game_ids) / len(eligible) if eligible else 0.0
    summary = ParkFactorSeasonSummary(
        target_season=target_season,
        computed_at=computed_at,
        complete_game_count=complete_game_count,
        target_or_future_game_count_excluded=target_or_future,
        prior_game_count=prior_count,
        prior_not_yet_eligible_count=prior_not_eligible,
        multi_venue_prior_game_count_excluded=len(eligible_excluded),
        eligible_prior_game_count=len(eligible),
        eligible_prior_venue_count=len(venues),
        eligible_prior_total_runs=total_runs,
        eligible_prior_home_runs=home_runs,
        prior_seasons=tuple(sorted({row.season for row in eligible})),
        minimum_venue_games=minimum_venue_games,
        factor_count=len(factors),
        factor_source_game_count=len(factor_game_ids),
        factor_sample_coverage=coverage,
        formable=bool(factors),
        failure_reason=failure,
    )
    return ParkFactorSeasonBuild(summary=summary, factors=factors)


def _merge_source_version(current: str | None, observed: str) -> str:
    if current is not None and current != observed:
        raise Fd1ParkFactorError(
            "play-transition rows do not share one Retrosheet source version"
        )
    return observed


def _date_only_eligible_at(value: date) -> datetime:
    return datetime.combine(value + timedelta(days=1), time.min, tzinfo=UTC)


def _target_seasons(values: Iterable[int]) -> tuple[int, ...]:
    raw_targets = tuple(values)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in raw_targets):
        raise Fd1ParkFactorError("target seasons must be integers")
    targets = tuple(sorted(set(raw_targets)))
    if not targets:
        raise Fd1ParkFactorError("at least one target season is required")
    if len(targets) > 10 or any(value < 1876 for value in targets):
        raise Fd1ParkFactorError("target seasons are invalid or exceed the governed maximum")
    return targets


def _positive_limit(value: int, field: str) -> None:
    if value <= 0:
        raise Fd1ParkFactorError(f"{field} must be positive")


def _required_string(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise Fd1ParkFactorError(f"{field} must be a nonempty string")
    return value.strip()


def _required_bool(row: Mapping[str, Any], field: str) -> bool:
    value = row.get(field)
    if not isinstance(value, bool):
        raise Fd1ParkFactorError(f"{field} must be boolean")
    return value


def _required_int(row: Mapping[str, Any], field: str, *, minimum: int) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise Fd1ParkFactorError(f"{field} must be an integer >= {minimum}")
    return value


def _required_date(row: Mapping[str, Any], field: str) -> date:
    value = _required_string(row, field)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Fd1ParkFactorError(f"{field} must be an ISO date") from exc


def _optional_date(row: Mapping[str, Any], field: str) -> date | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise Fd1ParkFactorError(f"{field} must be null or an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Fd1ParkFactorError(f"{field} must be null or an ISO date") from exc


def _sha(value: str, field: str) -> str:
    token = value.lower()
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        raise Fd1ParkFactorError(f"{field} must be SHA-256")
    return token


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _checksum(value: object) -> str:
    return sha256(
        json.dumps(
            _plain_json(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "DEFAULT_MAXIMUM_GAMES",
    "DEFAULT_MAXIMUM_PAYLOADS",
    "DEFAULT_MAXIMUM_TRANSITION_ROWS",
    "DEFAULT_MAXIMUM_TRANSITIONS_PER_GAME",
    "DEFAULT_TARGET_SEASONS",
    "ExcludedMultiVenueGame",
    "Fd1ParkFactorAccumulator",
    "Fd1ParkFactorBuild",
    "Fd1ParkFactorError",
    "PARK_FACTOR_ADAPTER_VERSION",
    "ParkFactorSeasonBuild",
    "ParkFactorSeasonSummary",
    "form_fd1_prior_period_park_factors",
]
