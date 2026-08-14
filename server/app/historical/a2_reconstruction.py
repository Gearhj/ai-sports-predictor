"""Provider-free chronological reconstruction for Gate 3A Stage A2.

The engine accepts already canonical, correction-resolved bulk inputs and
replays them through a single eligibility cursor.  It has no ORM, provider,
artifact, or model-training dependency.  Returned drafts are immutable and
contain compact content-addressed attestations; the exact checksum set is kept
only as a transient field and is omitted from persistence payloads.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import json
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Iterable, Iterator, Mapping

from app.historical.a2_schema import A2_SCHEMA, A2_SCHEMA_HASH, A2_SCHEMA_VERSION


A2_BUILDER_VERSION = "gate3a_a2_chronological_v6"
A2_LINEAGE_VERSION = "gate3a_a2_compact_lineage_v1"
A2_CUTOFF_POLICY_VERSION = "mlb_t_minus_6h_v1"
A2_TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
A2_SOURCE_MODE = "LIVE"
_MICROSECOND = timedelta(microseconds=1)
_CHECKSUM_CHARS = frozenset("0123456789abcdef")
_FAMILY_ORDER = (
    "league_prior",
    "rating",
    "team",
    "recent",
    "talent",
    "hitting",
    "pitching",
    "bullpen",
    "schedule",
    "target_context",
    "lifecycle",
)
_BATTING_STAT_FIELDS = (
    "batting_plate_appearances",
    "batting_at_bats",
    "batting_hits",
    "batting_walks",
    "batting_strikeouts",
    "batting_total_bases",
    "batting_home_runs",
)
_PITCHING_STAT_FIELDS = (
    "pitching_batters_faced",
    "pitching_hits_allowed",
    "pitching_walks_allowed",
    "pitching_strikeouts",
    "pitching_home_runs_allowed",
    "outs_recorded",
    "earned_runs",
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _checksum(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def canonical_identity_set_checksum(checksums: Iterable[str]) -> str:
    """Hash a set of canonical record identities without order sensitivity."""

    identities = tuple(sorted(set(checksums)))
    for item in identities:
        _validate_checksum("identity checksum", item)
    return _checksum({"identity_checksums": identities})


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A2 historical timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _validate_checksum(name: str, value: str) -> None:
    if len(value) != 64 or any(character not in _CHECKSUM_CHARS for character in value):
        raise ValueError(f"{name} must be a lowercase 64-character SHA-256 checksum")


def _required(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} is required")


def _finite(name: str, value: float, *, minimum: float | None = None) -> float:
    converted = float(value)
    if not isfinite(converted) or (minimum is not None and converted < minimum):
        qualifier = f" and >= {minimum}" if minimum is not None else ""
        raise ValueError(f"{name} must be finite{qualifier}")
    return converted


def _optional_finite(
    name: str,
    value: float | None,
    *,
    minimum: float | None = None,
) -> float | None:
    return None if value is None else _finite(name, value, minimum=minimum)


class A2Population(StrEnum):
    TIER_A_STRICT = "TIER_A_STRICT"
    TIER_B_ENHANCED = "TIER_B_ENHANCED"

    @property
    def allowed_evidence_tiers(self) -> frozenset[A2EvidenceTier]:
        if self is A2Population.TIER_A_STRICT:
            return frozenset((A2EvidenceTier.A,))
        return frozenset((A2EvidenceTier.A, A2EvidenceTier.B))

    @property
    def feature_version(self) -> str:
        if self is A2Population.TIER_A_STRICT:
            return "a2-tier-a-strict-v6"
        return "a2-tier-b-enhanced-v6"


class A2EvidenceTier(StrEnum):
    A = "A"
    B = "B"


class A2PlayerRole(StrEnum):
    STARTER = "STARTER"
    RELIEVER = "RELIEVER"
    BATTER = "BATTER"


@dataclass(frozen=True, slots=True)
class A2VenueInput:
    venue_id: str
    latitude: float | None
    longitude: float | None
    identity_checksum: str

    def __post_init__(self) -> None:
        _required("venue_id", self.venue_id)
        _validate_checksum("venue identity_checksum", self.identity_checksum)
        latitude = _optional_finite("venue latitude", self.latitude)
        longitude = _optional_finite("venue longitude", self.longitude)
        if (latitude is None) != (longitude is None):
            raise ValueError("Venue latitude and longitude must be supplied together")
        if latitude is not None and not -90.0 <= latitude <= 90.0:
            raise ValueError("Venue latitude must be between -90 and 90")
        if longitude is not None and not -180.0 <= longitude <= 180.0:
            raise ValueError("Venue longitude must be between -180 and 180")


@dataclass(frozen=True, slots=True)
class A2TargetGameInput:
    game_id: str
    season: int
    home_team_id: str
    away_team_id: str
    scheduled_first_pitch: datetime
    venue_id: str | None
    identity_checksum: str
    doubleheader_game_number: int | None = None
    doubleheader_identity_checksum: str | None = None
    official_date: date | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("game_id", self.game_id),
            ("home_team_id", self.home_team_id),
            ("away_team_id", self.away_team_id),
        ):
            _required(name, value)
        if self.season <= 0:
            raise ValueError("season must be positive")
        if self.home_team_id == self.away_team_id:
            raise ValueError("Target home and away teams must differ")
        object.__setattr__(self, "scheduled_first_pitch", _utc(self.scheduled_first_pitch))
        if self.official_date is not None:
            if not isinstance(self.official_date, date) or isinstance(
                self.official_date, datetime
            ):
                raise ValueError("Target official_date must be a date")
            if self.official_date.year != self.season:
                raise ValueError("Target official_date must belong to the target season")
        _validate_checksum("target identity_checksum", self.identity_checksum)
        supplied = (
            self.doubleheader_game_number is not None,
            self.doubleheader_identity_checksum is not None,
        )
        if supplied[0] != supplied[1]:
            raise ValueError("Doubleheader number and evidence checksum must be supplied together")
        if self.doubleheader_game_number is not None:
            if self.doubleheader_game_number not in (1, 2):
                raise ValueError("doubleheader_game_number must be 1 or 2")
            assert self.doubleheader_identity_checksum is not None
            _validate_checksum(
                "doubleheader_identity_checksum", self.doubleheader_identity_checksum
            )

    @property
    def cutoff(self) -> datetime:
        return self.scheduled_first_pitch - timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class A2FinalOutcomeInput:
    game_id: str
    season: int
    home_team_id: str
    away_team_id: str
    completed_at: datetime
    eligible_at: datetime
    home_score: int
    away_score: int
    innings: int | None
    venue_id: str | None
    identity_checksum: str
    evidence_tier: A2EvidenceTier = A2EvidenceTier.A
    revision: int = 1
    official_date: date | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("game_id", self.game_id),
            ("home_team_id", self.home_team_id),
            ("away_team_id", self.away_team_id),
        ):
            _required(name, value)
        if self.season <= 0 or self.revision <= 0:
            raise ValueError("season and outcome revision must be positive")
        if self.home_team_id == self.away_team_id:
            raise ValueError("Outcome home and away teams must differ")
        if self.home_score < 0 or self.away_score < 0:
            raise ValueError("Final scores must be non-negative")
        if self.innings is not None and self.innings <= 0:
            raise ValueError("Final innings must be positive when supplied")
        completed = _utc(self.completed_at)
        eligible = _utc(self.eligible_at)
        if eligible < completed:
            raise ValueError("A final outcome cannot be eligible before completion")
        object.__setattr__(self, "completed_at", completed)
        object.__setattr__(self, "eligible_at", eligible)
        if self.official_date is not None:
            if not isinstance(self.official_date, date) or isinstance(
                self.official_date, datetime
            ):
                raise ValueError("Outcome official_date must be a date")
            if self.official_date.year != self.season:
                raise ValueError("Outcome official_date must belong to the outcome season")
        object.__setattr__(self, "evidence_tier", A2EvidenceTier(self.evidence_tier))
        _validate_checksum("outcome identity_checksum", self.identity_checksum)


@dataclass(frozen=True, slots=True)
class A2TeamGameFactInput:
    fact_id: str
    game_id: str
    season: int
    team_id: str
    opponent_team_id: str
    event_at: datetime
    evidence_end_at: datetime
    eligible_at: datetime
    runs_scored: float
    runs_allowed: float
    identity_checksum: str
    evidence_tier: A2EvidenceTier = A2EvidenceTier.A
    revision: int = 1

    def __post_init__(self) -> None:
        for name, value in (
            ("fact_id", self.fact_id),
            ("game_id", self.game_id),
            ("team_id", self.team_id),
            ("opponent_team_id", self.opponent_team_id),
        ):
            _required(name, value)
        if self.season <= 0 or self.revision <= 0:
            raise ValueError("season and team-fact revision must be positive")
        if self.team_id == self.opponent_team_id:
            raise ValueError("Team fact team and opponent must differ")
        _finite("runs_scored", self.runs_scored, minimum=0.0)
        _finite("runs_allowed", self.runs_allowed, minimum=0.0)
        event_at = _utc(self.event_at)
        evidence_end = _utc(self.evidence_end_at)
        eligible = _utc(self.eligible_at)
        if evidence_end < event_at or eligible < evidence_end:
            raise ValueError("Team fact timing must satisfy event <= evidence end <= eligible")
        object.__setattr__(self, "event_at", event_at)
        object.__setattr__(self, "evidence_end_at", evidence_end)
        object.__setattr__(self, "eligible_at", eligible)
        object.__setattr__(self, "evidence_tier", A2EvidenceTier(self.evidence_tier))
        _validate_checksum("team fact identity_checksum", self.identity_checksum)


@dataclass(frozen=True, slots=True)
class A2PlayerGameFactInput:
    fact_id: str
    game_id: str
    season: int
    player_id: str
    team_id: str
    opponent_team_id: str
    role: A2PlayerRole
    event_at: datetime
    evidence_end_at: datetime
    eligible_at: datetime
    identity_checksum: str
    batting_plate_appearances: int | None = None
    batting_at_bats: int | None = None
    batting_hits: int | None = None
    batting_walks: int | None = None
    batting_strikeouts: int | None = None
    batting_total_bases: int | None = None
    batting_home_runs: int | None = None
    pitching_batters_faced: int | None = None
    pitching_hits_allowed: int | None = None
    pitching_walks_allowed: int | None = None
    pitching_strikeouts: int | None = None
    pitching_home_runs_allowed: int | None = None
    pitches: float | None = None
    outs_recorded: int | None = None
    earned_runs: float | None = None
    actual_starter: bool = False
    batting_order: int | None = None
    evidence_tier: A2EvidenceTier = A2EvidenceTier.A
    revision: int = 1
    official_date: date | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("fact_id", self.fact_id),
            ("game_id", self.game_id),
            ("player_id", self.player_id),
            ("team_id", self.team_id),
            ("opponent_team_id", self.opponent_team_id),
        ):
            _required(name, value)
        if self.season <= 0 or self.revision <= 0:
            raise ValueError("season and player-fact revision must be positive")
        if self.team_id == self.opponent_team_id:
            raise ValueError("Player fact team and opponent must differ")
        role = A2PlayerRole(self.role)
        if self.actual_starter and role is not A2PlayerRole.STARTER:
            raise ValueError("actual_starter facts must have the STARTER role")
        if self.batting_order is not None and not 1 <= self.batting_order <= 9:
            raise ValueError("batting_order must be between 1 and 9")
        for name in (
            "batting_plate_appearances",
            "batting_at_bats",
            "batting_hits",
            "batting_walks",
            "batting_strikeouts",
            "batting_total_bases",
            "batting_home_runs",
            "pitching_batters_faced",
            "pitching_hits_allowed",
            "pitching_walks_allowed",
            "pitching_strikeouts",
            "pitching_home_runs_allowed",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")
        for lesser_name, greater_name in (
            ("batting_at_bats", "batting_plate_appearances"),
            ("batting_hits", "batting_at_bats"),
            ("batting_walks", "batting_plate_appearances"),
            ("batting_strikeouts", "batting_plate_appearances"),
            ("batting_home_runs", "batting_hits"),
            ("pitching_hits_allowed", "pitching_batters_faced"),
            ("pitching_walks_allowed", "pitching_batters_faced"),
            ("pitching_strikeouts", "pitching_batters_faced"),
            ("pitching_home_runs_allowed", "pitching_hits_allowed"),
        ):
            lesser = getattr(self, lesser_name)
            greater = getattr(self, greater_name)
            if lesser is not None and greater is not None and lesser > greater:
                raise ValueError(f"{lesser_name} cannot exceed {greater_name}")
        if (
            self.batting_total_bases is not None
            and self.batting_hits is not None
            and not self.batting_hits <= self.batting_total_bases <= 4 * self.batting_hits
        ):
            raise ValueError("batting_total_bases must be between hits and four times hits")
        _optional_finite("pitches", self.pitches, minimum=0.0)
        _optional_finite("earned_runs", self.earned_runs, minimum=0.0)
        if self.outs_recorded is not None and self.outs_recorded < 0:
            raise ValueError("outs_recorded must be non-negative")
        event_at = _utc(self.event_at)
        evidence_end = _utc(self.evidence_end_at)
        eligible = _utc(self.eligible_at)
        if evidence_end < event_at or eligible < evidence_end:
            raise ValueError("Player fact timing must satisfy event <= evidence end <= eligible")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "event_at", event_at)
        object.__setattr__(self, "evidence_end_at", evidence_end)
        object.__setattr__(self, "eligible_at", eligible)
        if self.official_date is not None:
            if not isinstance(self.official_date, date) or isinstance(
                self.official_date, datetime
            ):
                raise ValueError("Player fact official_date must be a date")
            if self.official_date.year != self.season:
                raise ValueError("Player fact official_date must belong to the fact season")
        object.__setattr__(self, "evidence_tier", A2EvidenceTier(self.evidence_tier))
        _validate_checksum("player fact identity_checksum", self.identity_checksum)


def _is_batting_fact(item: A2PlayerGameFactInput) -> bool:
    return (
        item.role is A2PlayerRole.BATTER
        or item.batting_order is not None
        or any(getattr(item, name) is not None for name in _BATTING_STAT_FIELDS)
    )


def _is_pitching_fact(item: A2PlayerGameFactInput) -> bool:
    return item.role in (
        A2PlayerRole.STARTER,
        A2PlayerRole.RELIEVER,
    ) or any(getattr(item, name) is not None for name in _PITCHING_STAT_FIELDS)


@dataclass(frozen=True, slots=True)
class A2ReconstructionConfig:
    cutoff_hours: float = 6.0
    league_runs_prior: float = 4.4
    team_prior_games: float = 20.0
    pitcher_era_prior: float = 4.2
    pitcher_prior_outs: float = 60.0
    rate_prior_opportunities: float = 200.0
    batting_average_prior: float = 0.248
    batting_on_base_rate_prior: float = 0.320
    batting_slugging_prior: float = 0.400
    batting_walk_rate_prior: float = 0.085
    batting_strikeout_rate_prior: float = 0.225
    batting_home_run_rate_prior: float = 0.030
    pitching_hit_rate_prior: float = 0.225
    pitching_walk_rate_prior: float = 0.085
    pitching_strikeout_rate_prior: float = 0.225
    pitching_home_run_rate_prior: float = 0.030
    elo_initial_rating: float = 1500.0
    elo_k_factor: float = 20.0
    elo_home_field_advantage: float = 35.0
    elo_scale: float = 400.0
    bullpen_pool_days: int = 14
    league_prior_version: str = "mlb_2025_league_prior_v1"

    def __post_init__(self) -> None:
        for name, value in (
            ("cutoff_hours", self.cutoff_hours),
            ("league_runs_prior", self.league_runs_prior),
            ("team_prior_games", self.team_prior_games),
            ("pitcher_era_prior", self.pitcher_era_prior),
            ("pitcher_prior_outs", self.pitcher_prior_outs),
            ("rate_prior_opportunities", self.rate_prior_opportunities),
            ("elo_k_factor", self.elo_k_factor),
            ("elo_scale", self.elo_scale),
        ):
            if _finite(name, value) <= 0:
                raise ValueError(f"{name} must be positive")
        _finite("elo_initial_rating", self.elo_initial_rating)
        _finite("elo_home_field_advantage", self.elo_home_field_advantage)
        if self.cutoff_hours != 6.0:
            raise ValueError("The governed A2 cutoff policy is fixed at T-minus-six-hours")
        for name in (
            "batting_average_prior",
            "batting_on_base_rate_prior",
            "batting_slugging_prior",
            "batting_walk_rate_prior",
            "batting_strikeout_rate_prior",
            "batting_home_run_rate_prior",
            "pitching_hit_rate_prior",
            "pitching_walk_rate_prior",
            "pitching_strikeout_rate_prior",
            "pitching_home_run_rate_prior",
        ):
            value = _finite(name, getattr(self, name), minimum=0.0)
            if name != "batting_slugging_prior" and value > 1.0:
                raise ValueError(f"{name} must not exceed one")
        if self.bullpen_pool_days < 3:
            raise ValueError("bullpen_pool_days must be at least three")
        _required("league_prior_version", self.league_prior_version)

    def definition(self) -> dict[str, object]:
        return asdict(self)

    @property
    def league_prior_identity_checksum(self) -> str:
        return _checksum(
            {
                "kind": "A2_LEAGUE_PRIOR",
                "prior": self.definition(),
                "version": self.league_prior_version,
            }
        )

    @property
    def policy_identity_checksum(self) -> str:
        return _checksum(
            {
                "builder_version": A2_BUILDER_VERSION,
                "config": self.definition(),
                "cutoff_policy_version": A2_CUTOFF_POLICY_VERSION,
                "lineage_version": A2_LINEAGE_VERSION,
                "schema_hash": A2_SCHEMA_HASH,
            }
        )


@dataclass(frozen=True, slots=True)
class A2FeatureIdentity:
    population: A2Population
    feature_version: str
    schema_version: str
    schema_hash: str
    builder_version: str
    definition_checksum: str

    def to_dict(self) -> dict[str, str]:
        return {
            "population": self.population.value,
            "feature_version": self.feature_version,
            "schema_version": self.schema_version,
            "schema_hash": self.schema_hash,
            "builder_version": self.builder_version,
            "definition_checksum": self.definition_checksum,
        }


def feature_identity_for(
    population: A2Population,
    *,
    config: A2ReconstructionConfig = A2ReconstructionConfig(),
) -> A2FeatureIdentity:
    population = A2Population(population)
    definition = {
        "builder_version": A2_BUILDER_VERSION,
        "config": config.definition(),
        "feature_version": population.feature_version,
        "population": population.value,
        "schema_hash": A2_SCHEMA_HASH,
        "schema_version": A2_SCHEMA_VERSION,
        "target_context_policy": (
            "ABSENT"
            if population is A2Population.TIER_A_STRICT
            else "PROVISIONAL_IDENTITY_ONLY_TARGET_PERFORMANCE_FORBIDDEN"
        ),
    }
    return A2FeatureIdentity(
        population=population,
        feature_version=population.feature_version,
        schema_version=A2_SCHEMA_VERSION,
        schema_hash=A2_SCHEMA_HASH,
        builder_version=A2_BUILDER_VERSION,
        definition_checksum=_checksum(definition),
    )


@dataclass(frozen=True, slots=True)
class A2FamilyAttestation:
    family: str
    canonical_reference_count: int
    canonical_set_checksum: str

    def to_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "canonical_reference_count": self.canonical_reference_count,
            "canonical_set_checksum": self.canonical_set_checksum,
        }


@dataclass(frozen=True, slots=True)
class A2CompactLineage:
    lineage_version: str
    policy_identity_checksum: str
    canonical_reference_count: int
    canonical_set_checksum: str
    families: tuple[A2FamilyAttestation, ...]

    def family(self, name: str) -> A2FamilyAttestation:
        for item in self.families:
            if item.family == name:
                return item
        raise KeyError(name)

    def to_dict(self) -> dict[str, object]:
        return {
            "lineage_version": self.lineage_version,
            "policy_identity_checksum": self.policy_identity_checksum,
            "canonical_reference_count": self.canonical_reference_count,
            "canonical_set_checksum": self.canonical_set_checksum,
            "families": [item.to_dict() for item in self.families],
        }


@dataclass(frozen=True, slots=True)
class A2RowMetadata:
    source_mode: str
    temporal_semantics: str
    cutoff_policy_version: str
    target_identity_checksum: str
    target_venue_identity_checksum: str | None
    doubleheader_identity_checksum: str | None
    league_prior_identity_checksum: str
    team_history_count: int
    player_history_count: int
    outcome_history_count: int
    target_context_count: int
    evidence_start_at: datetime | None
    evidence_end_at: datetime | None
    eligible_max_at: datetime | None
    target_excluded: bool
    temporal_valid: bool

    def to_dict(self) -> dict[str, object]:
        values = asdict(self)
        for name in ("evidence_start_at", "evidence_end_at", "eligible_max_at"):
            value = values[name]
            values[name] = value.isoformat() if value is not None else None
        return values


@dataclass(frozen=True, slots=True)
class A2FeatureRowDraft:
    game_id: str
    season: int
    scheduled_first_pitch: datetime
    cutoff: datetime
    population: A2Population
    feature_identity: A2FeatureIdentity
    feature_items: tuple[tuple[str, float | None], ...]
    missing_feature_names: tuple[str, ...]
    missing_families: tuple[str, ...]
    provisional_families: tuple[str, ...]
    timing_risk_families: tuple[str, ...]
    metadata: A2RowMetadata
    compact_lineage: A2CompactLineage
    feature_values_checksum: str
    row_checksum: str
    _contributing_identity_checksums: frozenset[str] = field(repr=False)

    def __post_init__(self) -> None:
        if tuple(name for name, _ in self.feature_items) != A2_SCHEMA.value_names:
            raise ValueError("A2 row feature order does not match the frozen schema")
        for name, value in self.feature_items:
            if value is not None and not isfinite(float(value)):
                raise ValueError(f"A2 feature {name} must be finite or missing")
        for name, checksum in (
            ("feature_values_checksum", self.feature_values_checksum),
            ("row_checksum", self.row_checksum),
        ):
            _validate_checksum(name, checksum)
        if self.compact_lineage.canonical_reference_count != len(
            self._contributing_identity_checksums
        ):
            raise ValueError("Compact lineage count does not match transient identity set")
        if not self.metadata.target_excluded:
            raise ValueError("A2 row contains target-game evidence")
        if not self.metadata.temporal_valid:
            raise ValueError("A2 row contains evidence outside the governed cutoff")

    @property
    def feature_values(self) -> dict[str, float | None]:
        return dict(self.feature_items)

    @property
    def transient_contributing_identity_checksums(self) -> frozenset[str]:
        return self._contributing_identity_checksums

    def to_persistable_dict(self, *, include_row_checksum: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "game_id": self.game_id,
            "season": self.season,
            "scheduled_first_pitch": self.scheduled_first_pitch.isoformat(),
            "cutoff": self.cutoff.isoformat(),
            "population": self.population.value,
            "feature_identity": self.feature_identity.to_dict(),
            "feature_values": dict(self.feature_items),
            "missing_feature_names": list(self.missing_feature_names),
            "missing_families": list(self.missing_families),
            "provisional_families": list(self.provisional_families),
            "timing_risk_families": list(self.timing_risk_families),
            "metadata": self.metadata.to_dict(),
            "compact_lineage": self.compact_lineage.to_dict(),
            "feature_values_checksum": self.feature_values_checksum,
        }
        if include_row_checksum:
            payload["row_checksum"] = self.row_checksum
        return payload


@dataclass(frozen=True, slots=True)
class _Event:
    kind: str
    game_id: str
    event_at: datetime
    evidence_end_at: datetime
    eligible_at: datetime
    ready_at: datetime
    identity_checksum: str
    payload: A2FinalOutcomeInput | A2TeamGameFactInput | A2PlayerGameFactInput


@dataclass(slots=True)
class _TeamState:
    runs: float = 0.0
    allowed: float = 0.0
    games: int = 0
    season_facts: list[A2TeamGameFactInput] = field(default_factory=list)
    recent_facts: deque[A2TeamGameFactInput] = field(default_factory=deque)

    def add(self, item: A2TeamGameFactInput) -> None:
        self.runs += float(item.runs_scored)
        self.allowed += float(item.runs_allowed)
        self.games += 1
        self.season_facts.append(item)
        self.recent_facts.append(item)
        if len(self.recent_facts) > 1 and self.recent_facts[-2].event_at > item.event_at:
            self.recent_facts = deque(
                sorted(self.recent_facts, key=lambda row: (row.event_at, row.fact_id))
            )

    def prune(self, cutoff: datetime) -> None:
        lower = cutoff - timedelta(days=30)
        while self.recent_facts and self.recent_facts[0].event_at < lower:
            self.recent_facts.popleft()


@dataclass(slots=True)
class _BullpenState:
    season_facts: list[A2PlayerGameFactInput] = field(default_factory=list)
    recent_facts: deque[A2PlayerGameFactInput] = field(default_factory=deque)

    def add(self, item: A2PlayerGameFactInput) -> None:
        self.season_facts.append(item)
        self.recent_facts.append(item)
        if len(self.recent_facts) > 1 and self.recent_facts[-2].event_at > item.event_at:
            self.recent_facts = deque(
                sorted(self.recent_facts, key=lambda row: (row.event_at, row.fact_id))
            )

    def prune(self, cutoff: datetime, pool_days: int) -> None:
        lower = cutoff - timedelta(days=pool_days)
        while self.recent_facts and self.recent_facts[0].event_at < lower:
            self.recent_facts.popleft()


@dataclass(slots=True)
class _PlayerPitchState:
    facts: list[A2PlayerGameFactInput] = field(default_factory=list)


@dataclass(slots=True)
class _StatAggregate:
    fields: tuple[str, ...]
    row_count: int = 0
    totals: dict[str, float] = field(default_factory=dict)
    present_counts: dict[str, int] = field(default_factory=dict)

    def add(self, item: A2PlayerGameFactInput) -> None:
        self.row_count += 1
        for name in self.fields:
            value = getattr(item, name)
            if value is not None:
                self.totals[name] = self.totals.get(name, 0.0) + float(value)
                self.present_counts[name] = self.present_counts.get(name, 0) + 1

    def remove(self, item: A2PlayerGameFactInput) -> None:
        self.row_count -= 1
        if self.row_count < 0:
            raise RuntimeError("A2 rolling aggregate underflow")
        for name in self.fields:
            value = getattr(item, name)
            if value is not None:
                self.totals[name] = self.totals.get(name, 0.0) - float(value)
                self.present_counts[name] = self.present_counts.get(name, 0) - 1

    def strict_total(self, name: str) -> float | None:
        if self.row_count == 0 or self.present_counts.get(name, 0) != self.row_count:
            return None
        return self.totals.get(name, 0.0)


def _batting_aggregate() -> _StatAggregate:
    return _StatAggregate(_BATTING_STAT_FIELDS)


def _pitching_aggregate() -> _StatAggregate:
    return _StatAggregate(_PITCHING_STAT_FIELDS)


@dataclass(slots=True)
class _PlayerTeamState:
    season_batting: _StatAggregate = field(default_factory=_batting_aggregate)
    season_pitching: _StatAggregate = field(default_factory=_pitching_aggregate)
    batting_14d: _StatAggregate = field(default_factory=_batting_aggregate)
    pitching_14d: _StatAggregate = field(default_factory=_pitching_aggregate)
    batting_30d: _StatAggregate = field(default_factory=_batting_aggregate)
    pitching_30d: _StatAggregate = field(default_factory=_pitching_aggregate)
    recent_14d: deque[A2PlayerGameFactInput] = field(default_factory=deque)
    recent_30d: deque[A2PlayerGameFactInput] = field(default_factory=deque)
    hitting_refs: set[str] = field(default_factory=set)
    pitching_refs: set[str] = field(default_factory=set)

    def add(self, item: A2PlayerGameFactInput) -> None:
        is_batting = _is_batting_fact(item)
        is_pitching = _is_pitching_fact(item)
        if not is_batting and not is_pitching:
            return
        if is_batting:
            self.season_batting.add(item)
            self.batting_14d.add(item)
            self.batting_30d.add(item)
            self.hitting_refs.add(item.identity_checksum)
        if is_pitching:
            self.season_pitching.add(item)
            self.pitching_14d.add(item)
            self.pitching_30d.add(item)
            self.pitching_refs.add(item.identity_checksum)
        self.recent_14d.append(item)
        self.recent_30d.append(item)
        if len(self.recent_14d) > 1 and self.recent_14d[-2].event_at > item.event_at:
            self.recent_14d = deque(
                sorted(self.recent_14d, key=lambda row: (row.event_at, row.fact_id))
            )
        if len(self.recent_30d) > 1 and self.recent_30d[-2].event_at > item.event_at:
            self.recent_30d = deque(
                sorted(self.recent_30d, key=lambda row: (row.event_at, row.fact_id))
            )

    def prune(self, cutoff: datetime) -> None:
        for days, rows, batting, pitching in (
            (14, self.recent_14d, self.batting_14d, self.pitching_14d),
            (30, self.recent_30d, self.batting_30d, self.pitching_30d),
        ):
            lower = cutoff - timedelta(days=days)
            while rows and rows[0].event_at < lower:
                item = rows.popleft()
                if _is_batting_fact(item):
                    batting.remove(item)
                if _is_pitching_fact(item):
                    pitching.remove(item)


@dataclass(slots=True)
class _EloSeasonState:
    ratings: dict[str, float] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)
    outcomes: list[A2FinalOutcomeInput] = field(default_factory=list)
    rating_refs: dict[str, set[str]] = field(default_factory=dict)


class A2ChronologicalReconstructor:
    """Replay canonical A2 inputs with one monotonic eligibility cursor."""

    def __init__(self, config: A2ReconstructionConfig = A2ReconstructionConfig()) -> None:
        self.config = config

    def iter_rows(
        self,
        *,
        target_games: Iterable[A2TargetGameInput],
        final_outcomes: Iterable[A2FinalOutcomeInput] = (),
        team_facts: Iterable[A2TeamGameFactInput] = (),
        player_facts: Iterable[A2PlayerGameFactInput] = (),
        venues: Iterable[A2VenueInput] = (),
        population: A2Population = A2Population.TIER_A_STRICT,
    ) -> Iterator[A2FeatureRowDraft]:
        population = A2Population(population)
        targets = tuple(target_games)
        outcomes = tuple(final_outcomes)
        teams = tuple(team_facts)
        players = tuple(player_facts)
        venue_rows = tuple(venues)
        self._validate_inputs(targets, outcomes, teams, players, venue_rows)
        ordered_targets = tuple(
            sorted(
                targets,
                key=lambda item: (
                    self._cutoff(item.scheduled_first_pitch),
                    item.scheduled_first_pitch,
                    item.game_id,
                ),
            )
        )
        allowed = population.allowed_evidence_tiers
        events = self._events(outcomes, teams, players, allowed=allowed)
        context_by_game: dict[str, list[A2PlayerGameFactInput]] = defaultdict(list)
        if population is A2Population.TIER_B_ENHANCED:
            for item in players:
                if item.evidence_tier in allowed and (
                    item.actual_starter or item.batting_order is not None
                ):
                    context_by_game[item.game_id].append(item)
            for rows in context_by_game.values():
                rows.sort(key=lambda item: (item.team_id, item.player_id, item.fact_id))
        venue_by_id = {item.venue_id: item for item in venue_rows}
        temporal_by_checksum = self._temporal_index(events)

        team_state: dict[tuple[int, str], _TeamState] = defaultdict(_TeamState)
        bullpen_state: dict[tuple[int, str], _BullpenState] = defaultdict(_BullpenState)
        player_state: dict[tuple[int, str], _PlayerPitchState] = defaultdict(
            _PlayerPitchState
        )
        player_team_state: dict[tuple[int, str], _PlayerTeamState] = defaultdict(
            _PlayerTeamState
        )
        prior_batters: set[tuple[int, str]] = set()
        elo_state: dict[int, _EloSeasonState] = defaultdict(_EloSeasonState)
        last_outcome: dict[tuple[int, str], A2FinalOutcomeInput] = {}

        target_ids = {item.game_id for item in ordered_targets}
        unprocessed_target_ids = set(target_ids)
        held: dict[str, list[_Event]] = defaultdict(list)
        cursor = 0
        identity = feature_identity_for(population, config=self.config)

        def apply_event(event: _Event) -> None:
            item = event.payload
            if event.kind == "outcome":
                assert isinstance(item, A2FinalOutcomeInput)
                self._apply_outcome(item, elo_state[item.season])
                for team_id in (item.home_team_id, item.away_team_id):
                    key = (item.season, team_id)
                    previous = last_outcome.get(key)
                    if previous is None or (item.completed_at, item.game_id) > (
                        previous.completed_at,
                        previous.game_id,
                    ):
                        last_outcome[key] = item
            elif event.kind == "team":
                assert isinstance(item, A2TeamGameFactInput)
                team_state[(item.season, item.team_id)].add(item)
            else:
                assert isinstance(item, A2PlayerGameFactInput)
                player_team_state[(item.season, item.team_id)].add(item)
                if item.role is A2PlayerRole.RELIEVER:
                    bullpen_state[(item.season, item.team_id)].add(item)
                if item.role in (A2PlayerRole.STARTER, A2PlayerRole.RELIEVER):
                    player_state[(item.season, item.player_id)].facts.append(item)
                if item.batting_order is not None or item.role is A2PlayerRole.BATTER:
                    prior_batters.add((item.season, item.player_id))

        for target in ordered_targets:
            cutoff = self._cutoff(target.scheduled_first_pitch)
            while cursor < len(events) and events[cursor].ready_at <= cutoff:
                event = events[cursor]
                cursor += 1
                if event.game_id in unprocessed_target_ids:
                    held[event.game_id].append(event)
                else:
                    apply_event(event)

            row = self._build_row(
                target=target,
                cutoff=cutoff,
                population=population,
                identity=identity,
                context_rows=tuple(context_by_game.get(target.game_id, ())),
                venue_by_id=venue_by_id,
                team_state=team_state,
                bullpen_state=bullpen_state,
                player_state=player_state,
                player_team_state=player_team_state,
                prior_batters=prior_batters,
                elo_state=elo_state,
                last_outcome=last_outcome,
                temporal_by_checksum=temporal_by_checksum,
            )
            unprocessed_target_ids.remove(target.game_id)
            for event in held.pop(target.game_id, ()):
                apply_event(event)
            yield row

    def build_rows(self, **kwargs: object) -> tuple[A2FeatureRowDraft, ...]:
        """Materialize ``iter_rows`` for small callers and focused tests."""

        return tuple(self.iter_rows(**kwargs))  # type: ignore[arg-type]

    def _cutoff(self, scheduled_first_pitch: datetime) -> datetime:
        return _utc(scheduled_first_pitch) - timedelta(hours=self.config.cutoff_hours)

    def _events(
        self,
        outcomes: tuple[A2FinalOutcomeInput, ...],
        teams: tuple[A2TeamGameFactInput, ...],
        players: tuple[A2PlayerGameFactInput, ...],
        *,
        allowed: frozenset[A2EvidenceTier],
    ) -> tuple[_Event, ...]:
        rows: list[_Event] = []
        for item in outcomes:
            if item.evidence_tier not in allowed:
                continue
            rows.append(
                _Event(
                    kind="outcome",
                    game_id=item.game_id,
                    event_at=item.completed_at,
                    evidence_end_at=item.completed_at,
                    eligible_at=item.eligible_at,
                    ready_at=max(item.eligible_at, item.completed_at + _MICROSECOND),
                    identity_checksum=item.identity_checksum,
                    payload=item,
                )
            )
        for kind, inputs in (("team", teams), ("player", players)):
            for item in inputs:
                if item.evidence_tier not in allowed:
                    continue
                rows.append(
                    _Event(
                        kind=kind,
                        game_id=item.game_id,
                        event_at=item.event_at,
                        evidence_end_at=item.evidence_end_at,
                        eligible_at=item.eligible_at,
                        ready_at=max(
                            item.eligible_at,
                            item.event_at + _MICROSECOND,
                            item.evidence_end_at + _MICROSECOND,
                        ),
                        identity_checksum=item.identity_checksum,
                        payload=item,
                    )
                )
        kind_order = {"outcome": 0, "team": 1, "player": 2}
        rows.sort(
            key=lambda item: (
                item.ready_at,
                item.event_at,
                item.game_id,
                kind_order[item.kind],
                item.identity_checksum,
            )
        )
        return tuple(rows)

    @staticmethod
    def _temporal_index(
        events: tuple[_Event, ...],
    ) -> dict[str, tuple[str, datetime, datetime, datetime, str]]:
        return {
            item.identity_checksum: (
                item.kind,
                item.event_at,
                item.evidence_end_at,
                item.eligible_at,
                item.game_id,
            )
            for item in events
        }

    def _apply_outcome(self, item: A2FinalOutcomeInput, state: _EloSeasonState) -> None:
        state.outcomes.append(item)
        ordered = sorted(state.outcomes, key=lambda row: (row.completed_at, row.game_id))
        if ordered != state.outcomes:
            state.outcomes[:] = ordered
            state.ratings.clear()
            state.games.clear()
            state.rating_refs.clear()
            for outcome in ordered:
                self._apply_elo_update(outcome, state)
            return
        self._apply_elo_update(item, state)

    def _apply_elo_update(
        self,
        item: A2FinalOutcomeInput,
        state: _EloSeasonState,
    ) -> None:
        home = state.ratings.get(item.home_team_id, self.config.elo_initial_rating)
        away = state.ratings.get(item.away_team_id, self.config.elo_initial_rating)
        expected = self._expected_home(home, away)
        actual = 1.0 if item.home_score > item.away_score else 0.0
        if item.home_score == item.away_score:
            actual = 0.5
        change = self.config.elo_k_factor * (actual - expected)
        state.ratings[item.home_team_id] = home + change
        state.ratings[item.away_team_id] = away - change
        state.games[item.home_team_id] = state.games.get(item.home_team_id, 0) + 1
        state.games[item.away_team_id] = state.games.get(item.away_team_id, 0) + 1
        ancestry = (
            set(state.rating_refs.get(item.home_team_id, ()))
            | set(state.rating_refs.get(item.away_team_id, ()))
            | {item.identity_checksum}
        )
        state.rating_refs[item.home_team_id] = set(ancestry)
        state.rating_refs[item.away_team_id] = set(ancestry)

    def _expected_home(self, home: float, away: float) -> float:
        exponent = (
            away - (home + self.config.elo_home_field_advantage)
        ) / self.config.elo_scale
        return 1.0 / (1.0 + 10.0**exponent)

    def _build_row(
        self,
        *,
        target: A2TargetGameInput,
        cutoff: datetime,
        population: A2Population,
        identity: A2FeatureIdentity,
        context_rows: tuple[A2PlayerGameFactInput, ...],
        venue_by_id: Mapping[str, A2VenueInput],
        team_state: Mapping[tuple[int, str], _TeamState],
        bullpen_state: Mapping[tuple[int, str], _BullpenState],
        player_state: Mapping[tuple[int, str], _PlayerPitchState],
        player_team_state: Mapping[tuple[int, str], _PlayerTeamState],
        prior_batters: set[tuple[int, str]],
        elo_state: Mapping[int, _EloSeasonState],
        last_outcome: Mapping[tuple[int, str], A2FinalOutcomeInput],
        temporal_by_checksum: Mapping[
            str, tuple[str, datetime, datetime, datetime, str]
        ],
    ) -> A2FeatureRowDraft:
        values: dict[str, float | None] = {}
        family_refs: dict[str, set[str]] = {name: set() for name in _FAMILY_ORDER}
        prior_checksum = self.config.league_prior_identity_checksum
        for family in (
            "league_prior",
            "rating",
            "talent",
            "hitting",
            "pitching",
            "bullpen",
        ):
            family_refs[family].add(prior_checksum)

        season_elo = elo_state.get(target.season, _EloSeasonState())
        family_refs["rating"].update(
            season_elo.rating_refs.get(target.home_team_id, ())
        )
        family_refs["rating"].update(
            season_elo.rating_refs.get(target.away_team_id, ())
        )

        for side, team_id in (
            ("home", target.home_team_id),
            ("away", target.away_team_id),
        ):
            state = team_state.get((target.season, team_id))
            team_values, season_refs, recent_refs = self._team_features(
                side, state, cutoff=cutoff
            )
            values.update(team_values)
            family_refs["team"].update(season_refs)
            family_refs["recent"].update(recent_refs)
            family_refs["talent"].update(season_refs)

            player_team = player_team_state.get((target.season, team_id))
            form_values, hitting_refs, pitching_refs = self._player_form_features(
                side,
                player_team,
                cutoff=cutoff,
            )
            values.update(form_values)
            family_refs["hitting"].update(hitting_refs)
            family_refs["pitching"].update(pitching_refs)

            bullpen = bullpen_state.get((target.season, team_id))
            bullpen_values, bullpen_refs = self._bullpen_features(
                side,
                bullpen,
                cutoff=cutoff,
                target=target,
                team_id=team_id,
                team_games=season_elo.outcomes,
            )
            values.update(bullpen_values)
            family_refs["bullpen"].update(bullpen_refs)

            previous = last_outcome.get((target.season, team_id))
            schedule_values, schedule_refs = self._schedule_features(
                side,
                target=target,
                previous=previous,
                venue_by_id=venue_by_id,
            )
            values.update(schedule_values)
            family_refs["schedule"].update(schedule_refs)

        home_rating = season_elo.ratings.get(
            target.home_team_id, self.config.elo_initial_rating
        )
        away_rating = season_elo.ratings.get(
            target.away_team_id, self.config.elo_initial_rating
        )
        values.update(
            {
                "home_elo_rating": home_rating,
                "away_elo_rating": away_rating,
                "home_elo_games": float(season_elo.games.get(target.home_team_id, 0)),
                "away_elo_games": float(season_elo.games.get(target.away_team_id, 0)),
                "elo_expected_home_probability": self._expected_home(
                    home_rating, away_rating
                ),
            }
        )

        context_count = 0
        starters_confirmed = 0.0
        lineups_confirmed = 0.0
        if population is A2Population.TIER_B_ENHANCED:
            context_count = len(context_rows)
            context_values, context_refs, starters_confirmed, lineups_confirmed = (
                self._context_features(
                    target=target,
                    context_rows=context_rows,
                    cutoff=cutoff,
                    player_state=player_state,
                    prior_batters=prior_batters,
                )
            )
            values.update(context_values)
            family_refs["target_context"].update(context_refs)
            family_refs["lifecycle"].update(context_refs)
            if any(item.actual_starter for item in context_rows):
                family_refs["target_context"].add(prior_checksum)
        else:
            for side in ("home", "away"):
                values.update(self._empty_context_features(side))

        values.update(
            {
                "doubleheader_game_number": (
                    float(target.doubleheader_game_number)
                    if target.doubleheader_game_number is not None
                    else None
                ),
                "minutes_to_first_pitch": self.config.cutoff_hours * 60.0,
                "cutoff_offset_hours": self.config.cutoff_hours,
                "starters_confirmed": starters_confirmed,
                "lineups_confirmed": lineups_confirmed,
            }
        )

        feature_items = tuple((name, values[name]) for name in A2_SCHEMA.value_names)
        missing_names = tuple(name for name, value in feature_items if value is None)
        family_by_name = {item.name: item.family for item in A2_SCHEMA.features}
        missing_families = tuple(
            family
            for family in A2_SCHEMA.families
            if any(family_by_name[name] == family for name in missing_names)
        )
        context_used = population is A2Population.TIER_B_ENHANCED and bool(context_rows)
        provisional = ("target_context",) if context_used else ()
        timing_risk = ("target_context",) if context_used else ()

        lineage = self._lineage(family_refs)
        all_refs = frozenset().union(*family_refs.values())
        metadata = self._metadata(
            target=target,
            cutoff=cutoff,
            venue_by_id=venue_by_id,
            all_refs=all_refs,
            temporal_by_checksum=temporal_by_checksum,
            target_context_checksums={item.identity_checksum for item in context_rows},
        )
        feature_values_checksum = _checksum({"feature_items": feature_items})
        row = A2FeatureRowDraft(
            game_id=target.game_id,
            season=target.season,
            scheduled_first_pitch=target.scheduled_first_pitch,
            cutoff=cutoff,
            population=population,
            feature_identity=identity,
            feature_items=feature_items,
            missing_feature_names=missing_names,
            missing_families=missing_families,
            provisional_families=provisional,
            timing_risk_families=timing_risk,
            metadata=metadata,
            compact_lineage=lineage,
            feature_values_checksum=feature_values_checksum,
            row_checksum="0" * 64,
            _contributing_identity_checksums=all_refs,
        )
        return replace(
            row,
            row_checksum=_checksum(row.to_persistable_dict(include_row_checksum=False)),
        )

    def _team_features(
        self,
        side: str,
        state: _TeamState | None,
        *,
        cutoff: datetime,
    ) -> tuple[dict[str, float | None], set[str], set[str]]:
        if state is None:
            games = 0
            runs = allowed = 0.0
            recent_rows: tuple[A2TeamGameFactInput, ...] = ()
            season_refs: set[str] = set()
        else:
            state.prune(cutoff)
            games, runs, allowed = state.games, state.runs, state.allowed
            recent_rows = tuple(state.recent_facts)
            season_refs = {item.identity_checksum for item in state.season_facts}
        season_runs = runs / games if games else None
        season_allowed = allowed / games if games else None
        offense = (self.config.league_runs_prior * self.config.team_prior_games + runs) / (
            self.config.team_prior_games + games
        )
        prevention = (
            self.config.league_runs_prior * self.config.team_prior_games + allowed
        ) / (self.config.team_prior_games + games)
        reliability = games / (games + self.config.team_prior_games)
        result: dict[str, float | None] = {
            f"{side}_runs_per_game_season": season_runs,
            f"{side}_runs_allowed_per_game_season": season_allowed,
            f"{side}_team_sample_season": float(games),
            f"{side}_offense_true_talent": offense,
            f"{side}_run_prevention_true_talent": prevention,
            f"{side}_team_reliability": reliability,
        }
        recent_refs: set[str] = set()
        for days in (14, 30):
            lower = cutoff - timedelta(days=days)
            selected = tuple(item for item in recent_rows if item.event_at >= lower)
            sample = len(selected)
            recent_runs = (
                sum(float(item.runs_scored) for item in selected) / sample
                if sample
                else None
            )
            recent_allowed = (
                sum(float(item.runs_allowed) for item in selected) / sample if sample else None
            )
            result[f"{side}_runs_per_game_{days}d"] = recent_runs
            result[f"{side}_runs_allowed_per_game_{days}d"] = recent_allowed
            result[f"{side}_team_sample_{days}d"] = float(sample)
            result[f"{side}_offense_recent_delta_{days}d"] = (
                recent_runs - offense if recent_runs is not None else None
            )
            result[f"{side}_run_prevention_recent_delta_{days}d"] = (
                recent_allowed - prevention if recent_allowed is not None else None
            )
            recent_refs.update(item.identity_checksum for item in selected)
        return result, season_refs, recent_refs

    def _player_form_features(
        self,
        side: str,
        state: _PlayerTeamState | None,
        *,
        cutoff: datetime,
    ) -> tuple[dict[str, float | None], set[str], set[str]]:
        if state is None:
            windows = (
                ("season", _batting_aggregate(), _pitching_aggregate()),
                ("14d", _batting_aggregate(), _pitching_aggregate()),
                ("30d", _batting_aggregate(), _pitching_aggregate()),
            )
            hitting_refs: set[str] = set()
            pitching_refs: set[str] = set()
        else:
            state.prune(cutoff)
            windows = (
                ("season", state.season_batting, state.season_pitching),
                ("14d", state.batting_14d, state.pitching_14d),
                ("30d", state.batting_30d, state.pitching_30d),
            )
            hitting_refs = set(state.hitting_refs)
            pitching_refs = set(state.pitching_refs)
        values: dict[str, float | None] = {}
        for label, batting, pitching in windows:
            values.update(self._batting_window_features(side, label, batting))
            values.update(self._pitching_window_features(side, label, pitching))
        return values, hitting_refs, pitching_refs

    @staticmethod
    def _rate(numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator is None or denominator <= 0:
            return None
        return numerator / denominator

    def _posterior_rate(
        self,
        *,
        numerator: float | None,
        denominator: float | None,
        prior: float,
        no_rows: bool,
    ) -> float | None:
        if no_rows:
            return prior
        if numerator is None or denominator is None:
            return None
        return (
            prior * self.config.rate_prior_opportunities + numerator
        ) / (self.config.rate_prior_opportunities + denominator)

    def _batting_window_features(
        self,
        side: str,
        label: str,
        aggregate: _StatAggregate,
    ) -> dict[str, float | None]:
        pa = aggregate.strict_total("batting_plate_appearances")
        at_bats = aggregate.strict_total("batting_at_bats")
        hits = aggregate.strict_total("batting_hits")
        walks = aggregate.strict_total("batting_walks")
        strikeouts = aggregate.strict_total("batting_strikeouts")
        total_bases = aggregate.strict_total("batting_total_bases")
        home_runs = aggregate.strict_total("batting_home_runs")
        average = self._rate(hits, at_bats)
        reach_numerator = hits + walks if hits is not None and walks is not None else None
        reach_denominator = (
            at_bats + walks if at_bats is not None and walks is not None else None
        )
        on_base = self._rate(reach_numerator, reach_denominator)
        slugging = self._rate(total_bases, at_bats)
        walk_rate = self._rate(walks, pa)
        strikeout_rate = self._rate(strikeouts, pa)
        home_run_rate = self._rate(home_runs, pa)
        no_rows = aggregate.row_count == 0
        reliability = (
            0.0
            if no_rows
            else (
                None
                if pa is None
                else pa / (self.config.rate_prior_opportunities + pa)
            )
        )
        return {
            f"{side}_batting_pa_{label}": 0.0 if no_rows else pa,
            f"{side}_batting_average_{label}": average,
            f"{side}_batting_on_base_rate_{label}": on_base,
            f"{side}_batting_slugging_{label}": slugging,
            f"{side}_batting_walk_rate_{label}": walk_rate,
            f"{side}_batting_strikeout_rate_{label}": strikeout_rate,
            f"{side}_batting_home_run_rate_{label}": home_run_rate,
            f"{side}_batting_average_posterior_{label}": self._posterior_rate(
                numerator=hits,
                denominator=at_bats,
                prior=self.config.batting_average_prior,
                no_rows=no_rows,
            ),
            f"{side}_batting_on_base_rate_posterior_{label}": self._posterior_rate(
                numerator=reach_numerator,
                denominator=reach_denominator,
                prior=self.config.batting_on_base_rate_prior,
                no_rows=no_rows,
            ),
            f"{side}_batting_slugging_posterior_{label}": self._posterior_rate(
                numerator=total_bases,
                denominator=at_bats,
                prior=self.config.batting_slugging_prior,
                no_rows=no_rows,
            ),
            f"{side}_batting_walk_rate_posterior_{label}": self._posterior_rate(
                numerator=walks,
                denominator=pa,
                prior=self.config.batting_walk_rate_prior,
                no_rows=no_rows,
            ),
            f"{side}_batting_strikeout_rate_posterior_{label}": self._posterior_rate(
                numerator=strikeouts,
                denominator=pa,
                prior=self.config.batting_strikeout_rate_prior,
                no_rows=no_rows,
            ),
            f"{side}_batting_home_run_rate_posterior_{label}": self._posterior_rate(
                numerator=home_runs,
                denominator=pa,
                prior=self.config.batting_home_run_rate_prior,
                no_rows=no_rows,
            ),
            f"{side}_batting_rate_reliability_{label}": reliability,
        }

    def _pitching_window_features(
        self,
        side: str,
        label: str,
        aggregate: _StatAggregate,
    ) -> dict[str, float | None]:
        batters_faced = aggregate.strict_total("pitching_batters_faced")
        hits = aggregate.strict_total("pitching_hits_allowed")
        walks = aggregate.strict_total("pitching_walks_allowed")
        strikeouts = aggregate.strict_total("pitching_strikeouts")
        home_runs = aggregate.strict_total("pitching_home_runs_allowed")
        outs = aggregate.strict_total("outs_recorded")
        earned = aggregate.strict_total("earned_runs")
        era = earned * 27.0 / outs if earned is not None and outs and outs > 0 else None
        hit_rate = self._rate(hits, batters_faced)
        walk_rate = self._rate(walks, batters_faced)
        strikeout_rate = self._rate(strikeouts, batters_faced)
        home_run_rate = self._rate(home_runs, batters_faced)
        no_rows = aggregate.row_count == 0
        reliability = (
            0.0
            if no_rows
            else (
                None
                if batters_faced is None
                else batters_faced
                / (self.config.rate_prior_opportunities + batters_faced)
            )
        )
        era_posterior = (
            self.config.pitcher_era_prior
            if no_rows
            else (
                None
                if outs is None or earned is None
                else (
                    self.config.pitcher_era_prior * self.config.pitcher_prior_outs
                    + earned * 27.0
                )
                / (self.config.pitcher_prior_outs + outs)
            )
        )
        return {
            f"{side}_pitching_bf_{label}": 0.0 if no_rows else batters_faced,
            f"{side}_pitching_era_{label}": era,
            f"{side}_pitching_hit_rate_allowed_{label}": hit_rate,
            f"{side}_pitching_walk_rate_allowed_{label}": walk_rate,
            f"{side}_pitching_strikeout_rate_{label}": strikeout_rate,
            f"{side}_pitching_home_run_rate_allowed_{label}": home_run_rate,
            f"{side}_pitching_era_posterior_{label}": era_posterior,
            f"{side}_pitching_hit_rate_allowed_posterior_{label}": self._posterior_rate(
                numerator=hits,
                denominator=batters_faced,
                prior=self.config.pitching_hit_rate_prior,
                no_rows=no_rows,
            ),
            f"{side}_pitching_walk_rate_allowed_posterior_{label}": self._posterior_rate(
                numerator=walks,
                denominator=batters_faced,
                prior=self.config.pitching_walk_rate_prior,
                no_rows=no_rows,
            ),
            f"{side}_pitching_strikeout_rate_posterior_{label}": self._posterior_rate(
                numerator=strikeouts,
                denominator=batters_faced,
                prior=self.config.pitching_strikeout_rate_prior,
                no_rows=no_rows,
            ),
            f"{side}_pitching_home_run_rate_allowed_posterior_{label}": self._posterior_rate(
                numerator=home_runs,
                denominator=batters_faced,
                prior=self.config.pitching_home_run_rate_prior,
                no_rows=no_rows,
            ),
            f"{side}_pitching_rate_reliability_{label}": reliability,
        }

    def _bullpen_features(
        self,
        side: str,
        state: _BullpenState | None,
        *,
        cutoff: datetime,
        target: A2TargetGameInput,
        team_id: str,
        team_games: Iterable[A2FinalOutcomeInput],
    ) -> tuple[dict[str, float | None], set[str]]:
        if state is None:
            season_rows: tuple[A2PlayerGameFactInput, ...] = ()
            recent_rows: tuple[A2PlayerGameFactInput, ...] = ()
        else:
            state.prune(cutoff, self.config.bullpen_pool_days)
            season_rows = tuple(state.season_facts)
            recent_rows = tuple(state.recent_facts)
        result: dict[str, float | None] = {}
        pitch_windows: dict[int, float | None] = {}
        for days in (1, 2, 3):
            lower = cutoff - timedelta(days=days)
            selected = tuple(item for item in recent_rows if item.event_at >= lower)
            pitches = (
                None
                if any(item.pitches is None for item in selected)
                else sum(float(item.pitches or 0.0) for item in selected)
            )
            outs = (
                None
                if any(item.outs_recorded is None for item in selected)
                else float(sum(item.outs_recorded or 0 for item in selected))
            )
            pitch_windows[days] = pitches
            result[f"{side}_bullpen_pitches_{days}d"] = pitches
            result[f"{side}_bullpen_outs_{days}d"] = outs
            result[f"{side}_bullpen_innings_{days}d"] = (
                outs / 3.0 if outs is not None else None
            )
        consecutive, consecutive_game_refs = self._consecutive_relievers(
            target=target,
            team_id=team_id,
            team_games=team_games,
            recent_relief_rows=recent_rows,
        )
        pool = len({item.player_id for item in recent_rows})
        if any(pitch_windows[days] is None for days in (1, 2, 3)):
            fatigue = None
        else:
            fatigue = min(
                2.0,
                0.55 * float(pitch_windows[1] or 0.0) / 80.0
                + 0.30 * float(pitch_windows[2] or 0.0) / 140.0
                + 0.15 * float(pitch_windows[3] or 0.0) / 200.0,
            )
        quality_rows = tuple(
            item
            for item in season_rows
            if item.outs_recorded is not None and item.earned_runs is not None
        )
        observed_outs = sum(item.outs_recorded or 0 for item in quality_rows)
        observed_earned = sum(float(item.earned_runs or 0.0) for item in quality_rows)
        quality = (
            self.config.pitcher_era_prior * self.config.pitcher_prior_outs
            + observed_earned * 27.0
        ) / (self.config.pitcher_prior_outs + observed_outs)
        quality_reliability = observed_outs / (
            self.config.pitcher_prior_outs + observed_outs
        )
        result.update(
            {
                f"{side}_bullpen_consecutive_relievers": (
                    float(consecutive) if consecutive is not None else None
                ),
                f"{side}_bullpen_pool_count": float(pool),
                f"{side}_bullpen_fatigue": fatigue,
                f"{side}_bullpen_quality_posterior": quality,
                f"{side}_bullpen_quality_reliability": quality_reliability,
            }
        )
        return result, {
            item.identity_checksum for item in season_rows
        } | consecutive_game_refs

    @staticmethod
    def _consecutive_relievers(
        *,
        target: A2TargetGameInput,
        team_id: str,
        team_games: Iterable[A2FinalOutcomeInput],
        recent_relief_rows: Iterable[A2PlayerGameFactInput],
    ) -> tuple[int | None, set[str]]:
        """Count arms used in both immediately preceding, consecutive-date games.

        The official baseball dates, rather than UTC completion dates, define
        consecutive days.  The latest prior game must also be the calendar day
        before the target.  Because the two games come from complete team-game
        chronology, an intervening game with no relief facts correctly breaks
        the sequence instead of being skipped.
        """

        history = sorted(
            (
                item
                for item in team_games
                if team_id in (item.home_team_id, item.away_team_id)
            ),
            key=lambda item: (item.completed_at, item.game_id),
        )
        previous_games = tuple(history[-2:])
        refs = {item.identity_checksum for item in previous_games}
        if len(previous_games) < 2:
            return 0, refs
        earlier, latest = previous_games
        if (
            target.official_date is None
            or earlier.official_date is None
            or latest.official_date is None
        ):
            return None, refs
        if (target.official_date - latest.official_date).days != 1:
            return 0, refs
        if (latest.official_date - earlier.official_date).days != 1:
            return 0, refs

        relievers_by_game: dict[str, set[str]] = defaultdict(set)
        for item in recent_relief_rows:
            relievers_by_game[item.game_id].add(item.player_id)
        return (
            len(
                relievers_by_game.get(earlier.game_id, set())
                & relievers_by_game.get(latest.game_id, set())
            ),
            refs,
        )

    def _schedule_features(
        self,
        side: str,
        *,
        target: A2TargetGameInput,
        previous: A2FinalOutcomeInput | None,
        venue_by_id: Mapping[str, A2VenueInput],
    ) -> tuple[dict[str, float | None], set[str]]:
        structural_refs: set[str] = set()
        target_venue = venue_by_id.get(target.venue_id) if target.venue_id else None
        if target_venue is not None:
            structural_refs.add(target_venue.identity_checksum)
        if previous is None:
            return (
                {
                    f"{side}_hours_since_previous_game": None,
                    f"{side}_days_since_previous_game": None,
                    f"{side}_travel_miles": None,
                    f"{side}_previous_game_innings": None,
                },
                structural_refs,
            )
        previous_venue = (
            venue_by_id.get(previous.venue_id) if previous.venue_id else None
        )
        if previous_venue is not None:
            structural_refs.add(previous_venue.identity_checksum)
        hours = max(
            0.0,
            (target.scheduled_first_pitch - previous.completed_at).total_seconds() / 3600.0,
        )
        distance = self._travel_distance(previous.venue_id, target.venue_id, venue_by_id)
        return (
            {
                f"{side}_hours_since_previous_game": hours,
                f"{side}_days_since_previous_game": float(int(hours // 24.0)),
                f"{side}_travel_miles": distance,
                f"{side}_previous_game_innings": (
                    float(previous.innings) if previous.innings is not None else None
                ),
            },
            structural_refs | {previous.identity_checksum},
        )

    @staticmethod
    def _travel_distance(
        previous_venue_id: str | None,
        target_venue_id: str | None,
        venue_by_id: Mapping[str, A2VenueInput],
    ) -> float | None:
        if previous_venue_id is None or target_venue_id is None:
            return None
        previous = venue_by_id.get(previous_venue_id)
        target = venue_by_id.get(target_venue_id)
        if (
            previous is None
            or target is None
            or previous.latitude is None
            or previous.longitude is None
            or target.latitude is None
            or target.longitude is None
        ):
            return None
        lat1, lon1 = radians(previous.latitude), radians(previous.longitude)
        lat2, lon2 = radians(target.latitude), radians(target.longitude)
        dlat, dlon = lat2 - lat1, lon2 - lon1
        value = sin(dlat / 2.0) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2.0) ** 2
        return 2.0 * 3958.7613 * asin(sqrt(min(1.0, value)))

    def _context_features(
        self,
        *,
        target: A2TargetGameInput,
        context_rows: tuple[A2PlayerGameFactInput, ...],
        cutoff: datetime,
        player_state: Mapping[tuple[int, str], _PlayerPitchState],
        prior_batters: set[tuple[int, str]],
    ) -> tuple[dict[str, float | None], set[str], float, float]:
        result: dict[str, float | None] = {}
        refs = {item.identity_checksum for item in context_rows}
        starters_complete = True
        lineups_complete = True
        for side, team_id in (
            ("home", target.home_team_id),
            ("away", target.away_team_id),
        ):
            side_rows = tuple(item for item in context_rows if item.team_id == team_id)
            starters = tuple(item for item in side_rows if item.actual_starter)
            starter = starters[0] if len(starters) == 1 else None
            if starter is None:
                starters_complete = False
                result.update(self._empty_starter_features(side))
            else:
                history = player_state.get((target.season, starter.player_id))
                starter_values, starter_refs = self._starter_features(
                    side,
                    history.facts if history is not None else (),
                    cutoff=cutoff,
                    target=target,
                )
                result.update(starter_values)
                refs.update(starter_refs)
            lineup = tuple(item for item in side_rows if item.batting_order is not None)
            orders = {item.batting_order for item in lineup}
            if orders != set(range(1, 10)) or len(lineup) != 9:
                lineups_complete = False
            covered = sum(
                (target.season, item.player_id) in prior_batters for item in lineup
            )
            result[f"{side}_lineup_member_count"] = float(len(lineup))
            result[f"{side}_lineup_prior_appearance_coverage"] = (
                covered / len(lineup) if lineup else None
            )
        return result, refs, float(starters_complete), float(lineups_complete)

    def _starter_features(
        self,
        side: str,
        facts: Iterable[A2PlayerGameFactInput],
        *,
        cutoff: datetime,
        target: A2TargetGameInput,
    ) -> tuple[dict[str, float | None], set[str]]:
        rows = tuple(facts)
        starter_rows = tuple(item for item in rows if item.role is A2PlayerRole.STARTER)
        quality_rows = tuple(
            item
            for item in starter_rows
            if item.outs_recorded is not None and item.earned_runs is not None
        )
        outs = sum(item.outs_recorded or 0 for item in quality_rows)
        earned = sum(float(item.earned_runs or 0.0) for item in quality_rows)
        raw_era = earned * 27.0 / outs if outs > 0 else None
        posterior = (
            self.config.pitcher_era_prior * self.config.pitcher_prior_outs + earned * 27.0
        ) / (self.config.pitcher_prior_outs + outs)
        result: dict[str, float | None] = {
            f"{side}_starter_prior_era": raw_era,
            f"{side}_starter_quality_posterior": posterior,
            f"{side}_starter_quality_reliability": outs
            / (self.config.pitcher_prior_outs + outs),
            f"{side}_starter_prior_appearance_count": float(len(starter_rows)),
        }
        previous_start = max(
            starter_rows,
            key=lambda item: (item.event_at, item.fact_id),
            default=None,
        )
        days_since_previous_start: float | None = None
        if (
            previous_start is not None
            and target.official_date is not None
            and previous_start.official_date is not None
        ):
            calendar_days = (
                target.official_date - previous_start.official_date
            ).days
            if calendar_days < 0:
                raise ValueError("Previous starter official_date follows the target date")
            days_since_previous_start = float(calendar_days)
        result.update(
            {
                f"{side}_starter_days_since_previous_start": days_since_previous_start,
                f"{side}_starter_previous_start_pitches": (
                    float(previous_start.pitches)
                    if previous_start is not None and previous_start.pitches is not None
                    else None
                ),
                f"{side}_starter_previous_start_innings": (
                    float(previous_start.outs_recorded) / 3.0
                    if previous_start is not None
                    and previous_start.outs_recorded is not None
                    else None
                ),
            }
        )
        for days in (1, 3, 5):
            lower = cutoff - timedelta(days=days)
            selected = tuple(item for item in rows if item.event_at >= lower)
            result[f"{side}_starter_pitches_{days}d"] = (
                None
                if any(item.pitches is None for item in selected)
                else sum(float(item.pitches or 0.0) for item in selected)
            )
            result[f"{side}_starter_outs_{days}d"] = (
                None
                if any(item.outs_recorded is None for item in selected)
                else float(sum(item.outs_recorded or 0 for item in selected))
            )
        lower_7d = cutoff - timedelta(days=7)
        lower_14d = cutoff - timedelta(days=14)
        recent_7d = tuple(item for item in rows if item.event_at >= lower_7d)
        recent_14d = tuple(item for item in rows if item.event_at >= lower_14d)
        prior_7d = tuple(
            item for item in recent_14d if item.event_at < lower_7d
        )

        def pitches_or_missing(
            selected: tuple[A2PlayerGameFactInput, ...],
        ) -> float | None:
            if any(item.pitches is None for item in selected):
                return None
            return sum(float(item.pitches or 0.0) for item in selected)

        pitches_7d = pitches_or_missing(recent_7d)
        pitches_prior_7d = pitches_or_missing(prior_7d)
        outs_14d = (
            None
            if any(item.outs_recorded is None for item in recent_14d)
            else float(sum(item.outs_recorded or 0 for item in recent_14d))
        )
        result.update(
            {
                f"{side}_starter_pitches_7d": pitches_7d,
                f"{side}_starter_innings_14d": (
                    outs_14d / 3.0 if outs_14d is not None else None
                ),
                f"{side}_starter_appearances_14d": float(len(recent_14d)),
                f"{side}_starter_workload_trend_7d_vs_prior_7d": (
                    pitches_7d - pitches_prior_7d
                    if pitches_7d is not None and pitches_prior_7d is not None
                    else None
                ),
            }
        )
        used_rows = set(starter_rows) | set(recent_14d)
        return result, {item.identity_checksum for item in used_rows}

    def _empty_context_features(self, side: str) -> dict[str, None]:
        return {
            **self._empty_starter_features(side),
            f"{side}_lineup_member_count": None,
            f"{side}_lineup_prior_appearance_coverage": None,
        }

    @staticmethod
    def _empty_starter_features(side: str) -> dict[str, None]:
        values = {
            f"{side}_starter_prior_era": None,
            f"{side}_starter_quality_posterior": None,
            f"{side}_starter_quality_reliability": None,
            f"{side}_starter_prior_appearance_count": None,
            f"{side}_starter_days_since_previous_start": None,
            f"{side}_starter_previous_start_pitches": None,
            f"{side}_starter_previous_start_innings": None,
            f"{side}_starter_pitches_7d": None,
            f"{side}_starter_innings_14d": None,
            f"{side}_starter_appearances_14d": None,
            f"{side}_starter_workload_trend_7d_vs_prior_7d": None,
        }
        for days in (1, 3, 5):
            values[f"{side}_starter_pitches_{days}d"] = None
            values[f"{side}_starter_outs_{days}d"] = None
        return values

    def _lineage(self, family_refs: Mapping[str, set[str]]) -> A2CompactLineage:
        families = tuple(
            A2FamilyAttestation(
                family=family,
                canonical_reference_count=len(family_refs[family]),
                canonical_set_checksum=canonical_identity_set_checksum(family_refs[family]),
            )
            for family in _FAMILY_ORDER
        )
        all_refs = frozenset().union(*family_refs.values())
        return A2CompactLineage(
            lineage_version=A2_LINEAGE_VERSION,
            policy_identity_checksum=self.config.policy_identity_checksum,
            canonical_reference_count=len(all_refs),
            canonical_set_checksum=canonical_identity_set_checksum(all_refs),
            families=families,
        )

    def _metadata(
        self,
        *,
        target: A2TargetGameInput,
        cutoff: datetime,
        venue_by_id: Mapping[str, A2VenueInput],
        all_refs: frozenset[str],
        temporal_by_checksum: Mapping[
            str, tuple[str, datetime, datetime, datetime, str]
        ],
        target_context_checksums: set[str],
    ) -> A2RowMetadata:
        history = [
            temporal_by_checksum[checksum]
            for checksum in all_refs
            if checksum in temporal_by_checksum
            and checksum not in target_context_checksums
        ]
        event_times = [item[1] for item in history]
        end_times = [item[2] for item in history]
        eligible_times = [item[3] for item in history]
        target_excluded = not any(item[4] == target.game_id for item in history)
        temporal_valid = target_excluded and all(
            event < cutoff and end < cutoff and eligible <= cutoff
            for _, event, end, eligible, _ in history
        )
        venue_identity = (
            venue_by_id[target.venue_id].identity_checksum
            if target.venue_id in venue_by_id
            else None
        )
        return A2RowMetadata(
            source_mode=A2_SOURCE_MODE,
            temporal_semantics=A2_TEMPORAL_SEMANTICS,
            cutoff_policy_version=A2_CUTOFF_POLICY_VERSION,
            target_identity_checksum=target.identity_checksum,
            target_venue_identity_checksum=venue_identity,
            doubleheader_identity_checksum=target.doubleheader_identity_checksum,
            league_prior_identity_checksum=self.config.league_prior_identity_checksum,
            team_history_count=sum(item[0] == "team" for item in history),
            player_history_count=sum(item[0] == "player" for item in history),
            outcome_history_count=sum(item[0] == "outcome" for item in history),
            target_context_count=len(target_context_checksums),
            evidence_start_at=min(event_times) if event_times else None,
            evidence_end_at=max(end_times) if end_times else None,
            eligible_max_at=max(eligible_times) if eligible_times else None,
            target_excluded=target_excluded,
            temporal_valid=temporal_valid,
        )

    @staticmethod
    def _validate_inputs(
        targets: tuple[A2TargetGameInput, ...],
        outcomes: tuple[A2FinalOutcomeInput, ...],
        teams: tuple[A2TeamGameFactInput, ...],
        players: tuple[A2PlayerGameFactInput, ...],
        venues: tuple[A2VenueInput, ...],
    ) -> None:
        def unique(name: str, keys: Iterable[object]) -> None:
            rows = tuple(keys)
            if len(rows) != len(set(rows)):
                raise ValueError(
                    f"Duplicate {name}; correction-latest inputs must be pre-resolved"
                )

        unique("target game_id", (item.game_id for item in targets))
        unique("target identity", (item.identity_checksum for item in targets))
        unique("outcome game_id", (item.game_id for item in outcomes))
        unique("team fact_id", (item.fact_id for item in teams))
        unique("team game/team semantic key", ((item.game_id, item.team_id) for item in teams))
        unique("player fact_id", (item.fact_id for item in players))
        unique(
            "player game/team/player semantic key",
            ((item.game_id, item.team_id, item.player_id) for item in players),
        )
        unique("venue_id", (item.venue_id for item in venues))
        record_checksums = tuple(
            [item.identity_checksum for item in targets]
            + [item.identity_checksum for item in outcomes]
            + [item.identity_checksum for item in teams]
            + [item.identity_checksum for item in players]
            + [item.identity_checksum for item in venues]
        )
        unique("canonical identity checksum", record_checksums)


def build_rows(
    *,
    target_games: Iterable[A2TargetGameInput],
    final_outcomes: Iterable[A2FinalOutcomeInput] = (),
    team_facts: Iterable[A2TeamGameFactInput] = (),
    player_facts: Iterable[A2PlayerGameFactInput] = (),
    venues: Iterable[A2VenueInput] = (),
    population: A2Population = A2Population.TIER_A_STRICT,
    config: A2ReconstructionConfig = A2ReconstructionConfig(),
) -> tuple[A2FeatureRowDraft, ...]:
    """Convenience wrapper around the streaming engine for bounded inputs."""

    return A2ChronologicalReconstructor(config).build_rows(
        target_games=target_games,
        final_outcomes=final_outcomes,
        team_facts=team_facts,
        player_facts=player_facts,
        venues=venues,
        population=population,
    )


__all__ = [
    "A2_BUILDER_VERSION",
    "A2_CUTOFF_POLICY_VERSION",
    "A2_LINEAGE_VERSION",
    "A2ChronologicalReconstructor",
    "A2CompactLineage",
    "A2EvidenceTier",
    "A2FamilyAttestation",
    "A2FeatureIdentity",
    "A2FeatureRowDraft",
    "A2FinalOutcomeInput",
    "A2PlayerGameFactInput",
    "A2PlayerRole",
    "A2Population",
    "A2ReconstructionConfig",
    "A2RowMetadata",
    "A2TargetGameInput",
    "A2TeamGameFactInput",
    "A2VenueInput",
    "build_rows",
    "canonical_identity_set_checksum",
    "feature_identity_for",
]
