"""Point-in-time-safe MLB training rows and immutable dataset manifests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.markets.odds import no_vig_probabilities
from app.models import (
    FeatureVersion,
    Forecast,
    ForecastStatus,
    Game,
    GameFeatureSnapshot,
    GameStateObservation,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
)


TARGET_DEFINITIONS = {
    "home_win": "1 when append-only final home_score > away_score; otherwise 0",
    "home_runs": "append-only final home score",
    "away_runs": "append-only final away score",
    "run_differential": "home_runs - away_runs",
    "total_runs": "home_runs + away_runs",
}
FORBIDDEN_FEATURE_TOKENS = (
    "final_score",
    "home_score",
    "away_score",
    "home_win",
    "game_result",
    "winner",
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return _aware(value).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class TrainingRow:
    game_id: str
    scheduled_first_pitch: str
    data_as_of: str
    lifecycle: str
    feature_snapshot_id: str
    feature_snapshot_checksum: str
    feature_version_id: str | None
    feature_version: str
    feature_schema_version: str
    source_mode: str
    source_evidence: list[dict[str, Any]]
    home_team_id: str
    away_team_id: str
    features: dict[str, Any]
    missing_features: list[str]
    stale_features: list[str]
    provisional_features: list[str]
    feature_completeness: float
    home_win: int
    home_runs: int
    away_runs: int
    run_differential: int
    total_runs: int
    market_home_probability: float | None
    market_timestamp: str | None
    market_books: int
    closing_home_probability: float | None
    closing_timestamp: str | None


@dataclass(frozen=True)
class TrainingDatasetManifestData:
    dataset_id: str
    created_at: str
    date_start: str | None
    date_end: str | None
    seasons: list[int]
    feature_schema_version: str | None
    feature_version: str | None
    feature_version_id: str | None
    feature_names: list[str]
    row_count: int
    excluded_row_count: int
    exclusion_reasons: dict[str, int]
    source_modes: list[str]
    rows_checksum: str
    checksum: str
    target_definitions: dict[str, str]
    training_cutoff: str
    leakage_checks: dict[str, bool]
    feature_family_coverage: dict[str, float]
    artifact_uri: str | None = None


@dataclass(frozen=True)
class DatasetBuildResult:
    manifest: TrainingDatasetManifestData
    rows: tuple[TrainingRow, ...]
    exclusions: tuple[dict[str, str], ...]

    @property
    def trainable(self) -> bool:
        return self.manifest.row_count > 0 and all(self.manifest.leakage_checks.values())


class GovernedTrainingDatasetBuilder:
    """Build one labeled LIVE row per game from frozen pregame evidence.

    Targets are read only from an append-only FINAL observation that became
    available by the training cutoff. Features are read only from the exact
    immutable snapshot referenced by a published pregame forecast.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def build(
        self,
        *,
        training_cutoff: datetime | None = None,
        feature_schema_version: str | None = None,
    ) -> DatasetBuildResult:
        cutoff = _aware(training_cutoff or datetime.now(UTC))
        games = list(self.session.scalars(select(Game).order_by(Game.starts_at, Game.id)))
        rows: list[TrainingRow] = []
        exclusions: list[dict[str, str]] = []
        schemas: Counter[str] = Counter()

        for game in games:
            if game.source_mode != "LIVE":
                exclusions.append(self._excluded(game, "non_live_source_mode"))
                continue
            final = self._final_observation(game.id, cutoff)
            if final is None:
                exclusions.append(self._excluded(game, "final_outcome_unavailable"))
                continue
            candidate = self._pregame_candidate(game.id, final.scheduled_at, cutoff)
            if candidate is None:
                exclusions.append(self._excluded(game, "pregame_snapshot_unavailable"))
                continue
            forecast, snapshot = candidate
            schemas[snapshot.feature_schema_version] += 1
            requested_schema = feature_schema_version
            if requested_schema and snapshot.feature_schema_version != requested_schema:
                exclusions.append(self._excluded(game, "feature_schema_mismatch"))
                continue
            snapshot_state = self.session.get(GameStateObservation, snapshot.game_state_observation_id)
            if snapshot_state is None:
                exclusions.append(self._excluded(game, "snapshot_game_state_missing"))
                continue
            if _aware(snapshot_state.scheduled_at) != _aware(final.scheduled_at):
                exclusions.append(self._excluded(game, "schedule_identity_changed"))
                continue
            if snapshot.source_mode != "LIVE" or any(
                item.get("source_mode") not in {None, "LIVE"}
                for item in snapshot.source_evidence or []
            ):
                exclusions.append(self._excluded(game, "mixed_source_mode"))
                continue
            if not _evidence_within_cutoff(snapshot.source_evidence, snapshot.data_as_of):
                exclusions.append(self._excluded(game, "source_evidence_after_snapshot_cutoff"))
                continue
            if self._contains_forbidden_feature(snapshot.feature_values):
                exclusions.append(self._excluded(game, "target_leakage_feature_name"))
                continue
            if final.home_team_id is None or final.away_team_id is None:
                exclusions.append(self._excluded(game, "observed_team_identity_missing"))
                continue
            market = self._market_probability(
                snapshot.market_snapshot_ids,
                final.home_team_id,
                final.away_team_id,
                as_of=snapshot.data_as_of,
            )
            closing = self._closing_probability(game.id, final.home_team_id, final.away_team_id, final.scheduled_at)
            feature_version = self.session.get(FeatureVersion, snapshot.feature_version_id) if snapshot.feature_version_id else None
            home_runs, away_runs = int(final.home_score), int(final.away_score)
            row = TrainingRow(
                game_id=str(game.id),
                scheduled_first_pitch=_iso(final.scheduled_at),
                data_as_of=_iso(snapshot.data_as_of),
                lifecycle=forecast.lifecycle.value.upper(),
                feature_snapshot_id=str(snapshot.id),
                feature_snapshot_checksum=snapshot.checksum,
                feature_version_id=str(snapshot.feature_version_id) if snapshot.feature_version_id else None,
                feature_version=f"{feature_version.name}/{feature_version.version}" if feature_version else "unknown",
                feature_schema_version=snapshot.feature_schema_version,
                source_mode="LIVE",
                source_evidence=list(snapshot.source_evidence or []),
                home_team_id=str(final.home_team_id),
                away_team_id=str(final.away_team_id),
                features=_model_ready_features(snapshot, snapshot_state),
                missing_features=sorted(str(value) for value in snapshot.missing_features or []),
                stale_features=sorted(str(value) for value in snapshot.stale_features or []),
                provisional_features=sorted(str(value) for value in snapshot.provisional_features or []),
                feature_completeness=float(snapshot.feature_completeness),
                home_win=int(home_runs > away_runs),
                home_runs=home_runs,
                away_runs=away_runs,
                run_differential=home_runs - away_runs,
                total_runs=home_runs + away_runs,
                market_home_probability=market[0],
                market_timestamp=_iso(market[1]) if market[1] else None,
                market_books=market[2],
                closing_home_probability=closing[0],
                closing_timestamp=_iso(closing[1]) if closing[1] else None,
            )
            rows.append(row)

        selected_schema = feature_schema_version or (schemas.most_common(1)[0][0] if schemas else None)
        if selected_schema:
            retained: list[TrainingRow] = []
            for row in rows:
                if row.feature_schema_version == selected_schema:
                    retained.append(row)
                else:
                    exclusions.append({"game_id": row.game_id, "reason": "feature_schema_mismatch"})
            rows = retained
        rows.sort(key=lambda row: (row.scheduled_first_pitch, row.game_id))
        reasons = Counter(item["reason"] for item in exclusions)
        manifest = self._manifest(rows, exclusions, reasons, cutoff, selected_schema)
        return DatasetBuildResult(manifest=manifest, rows=tuple(rows), exclusions=tuple(exclusions))

    def _final_observation(self, game_id: Any, cutoff: datetime) -> GameStateObservation | None:
        candidates = list(self.session.scalars(
            select(GameStateObservation).where(
                GameStateObservation.game_id == game_id,
                GameStateObservation.source_mode == "LIVE",
                GameStateObservation.available_as_of <= cutoff,
                GameStateObservation.observed_at <= cutoff,
                GameStateObservation.home_score.is_not(None),
                GameStateObservation.away_score.is_not(None),
            ).order_by(
                GameStateObservation.available_as_of.desc(),
                GameStateObservation.observed_at.desc(),
                GameStateObservation.id.desc(),
            )
        ))
        return next(
            (row for row in candidates if str(row.status).lower() in {"final", "game_over", "completed"}),
            None,
        )

    def _pregame_candidate(
        self,
        game_id: Any,
        scheduled_at: datetime,
        cutoff: datetime,
    ) -> tuple[Forecast, GameFeatureSnapshot] | None:
        rows = list(self.session.execute(
            select(Forecast, GameFeatureSnapshot)
            .join(GameFeatureSnapshot, GameFeatureSnapshot.id == Forecast.feature_snapshot_id)
            .where(
                Forecast.game_id == game_id,
                Forecast.status == ForecastStatus.PUBLISHED,
                Forecast.source_mode == "LIVE",
                Forecast.published_at.is_not(None),
                Forecast.published_at < _aware(scheduled_at),
                Forecast.generated_at < _aware(scheduled_at),
                Forecast.available_as_of < _aware(scheduled_at),
                Forecast.available_as_of <= cutoff,
                GameFeatureSnapshot.source_mode == "LIVE",
                GameFeatureSnapshot.data_as_of < _aware(scheduled_at),
                GameFeatureSnapshot.generated_at < _aware(scheduled_at),
            )
            .order_by(
                GameFeatureSnapshot.data_as_of.desc(),
                Forecast.version.desc(),
                Forecast.id.desc(),
            )
        ))
        return rows[0] if rows else None

    def _market_probability(
        self,
        raw_ids: Iterable[Any],
        home_team_id: Any,
        away_team_id: Any,
        *,
        closing_only: bool = False,
        as_of: datetime | None = None,
    ) -> tuple[float | None, datetime | None, int]:
        ids: list[UUID] = []
        for value in raw_ids or []:
            try:
                ids.append(value if isinstance(value, UUID) else UUID(str(value)))
            except (TypeError, ValueError):
                continue
        if not ids:
            return None, None, 0
        query = (
            select(Market, MarketOutcome, OddsSnapshot)
            .join(MarketOutcome, MarketOutcome.market_id == Market.id)
            .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
            .where(
                OddsSnapshot.id.in_(ids),
                OddsSnapshot.source_mode == "LIVE",
                Market.market_type == MarketType.MONEYLINE,
            )
        )
        if closing_only:
            query = query.where(OddsSnapshot.is_closing.is_(True))
        if as_of is not None:
            query = query.where(
                OddsSnapshot.available_as_of <= _aware(as_of),
                OddsSnapshot.observed_at <= _aware(as_of),
            )
        by_book: dict[Any, dict[Any, OddsSnapshot]] = {}
        for _, outcome, snapshot in self.session.execute(query):
            if outcome.participant_team_id not in {home_team_id, away_team_id}:
                continue
            by_book.setdefault(snapshot.sportsbook_id, {})[outcome.participant_team_id] = snapshot
        probabilities: list[float] = []
        timestamps: list[datetime] = []
        for outcomes in by_book.values():
            if home_team_id not in outcomes or away_team_id not in outcomes:
                continue
            home, away = outcomes[home_team_id], outcomes[away_team_id]
            no_vig = no_vig_probabilities(
                {"home": home.decimal_odds, "away": away.decimal_odds},
                american=False,
            )
            probabilities.append(float(no_vig.probabilities["home"]))
            timestamps.append(max(_aware(home.observed_at), _aware(away.observed_at)))
        if not probabilities:
            return None, None, 0
        return sum(probabilities) / len(probabilities), max(timestamps), len(probabilities)

    def _closing_probability(
        self,
        game_id: Any,
        home_team_id: Any,
        away_team_id: Any,
        scheduled_at: datetime,
    ) -> tuple[float | None, datetime | None]:
        ids = list(self.session.scalars(
            select(OddsSnapshot.id)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .where(
                Market.game_id == game_id,
                Market.market_type == MarketType.MONEYLINE,
                OddsSnapshot.source_mode == "LIVE",
                OddsSnapshot.is_closing.is_(True),
                OddsSnapshot.available_as_of < _aware(scheduled_at),
                OddsSnapshot.observed_at < _aware(scheduled_at),
            )
        ))
        probability, observed_at, _ = self._market_probability(
            ids,
            home_team_id,
            away_team_id,
            closing_only=True,
            as_of=scheduled_at,
        )
        return probability, observed_at

    @staticmethod
    def _contains_forbidden_feature(features: dict[str, Any]) -> bool:
        return any(token in str(key).lower() for key in features for token in FORBIDDEN_FEATURE_TOKENS)

    @staticmethod
    def _excluded(game: Game, reason: str) -> dict[str, str]:
        return {"game_id": str(game.id), "reason": reason}

    def _manifest(
        self,
        rows: list[TrainingRow],
        exclusions: list[dict[str, str]],
        reasons: Counter[str],
        cutoff: datetime,
        selected_schema: str | None,
    ) -> TrainingDatasetManifestData:
        row_dicts = [asdict(row) for row in rows]
        rows_checksum = hashlib.sha256(b"\n".join(_canonical(row) for row in row_dicts)).hexdigest()
        feature_names = sorted({key for row in rows for key in row.features})
        feature_version_ids = sorted({row.feature_version_id for row in rows if row.feature_version_id})
        feature_versions = sorted({row.feature_version for row in rows})
        dates = [datetime.fromisoformat(row.scheduled_first_pitch.replace("Z", "+00:00")).date() for row in rows]
        source_modes = sorted({row.source_mode for row in rows})
        coverage = _feature_family_coverage(rows)
        leakage_checks = {
            "one_row_per_game": len({row.game_id for row in rows}) == len(rows),
            "pregame_cutoff": all(row.data_as_of < row.scheduled_first_pitch for row in rows),
            "live_source_only": all(row.source_mode == "LIVE" for row in rows),
            "no_target_named_features": all(not self._contains_forbidden_feature(row.features) for row in rows),
            "final_scores_targets_only": all(
                row.run_differential == row.home_runs - row.away_runs
                and row.total_runs == row.home_runs + row.away_runs
                and row.home_win == int(row.home_runs > row.away_runs)
                for row in rows
            ),
            "feature_schema_consistent": len({row.feature_schema_version for row in rows}) <= 1,
            "snapshot_checksum_present": all(len(row.feature_snapshot_checksum) == 64 for row in rows),
            "source_evidence_cutoff": all(
                _evidence_within_cutoff(row.source_evidence, _parse_iso(row.data_as_of))
                for row in rows
            ),
            "market_cutoff": all(
                row.market_timestamp is None or row.market_timestamp <= row.data_as_of
                for row in rows
            ),
        }
        identity = {
            "rows_checksum": rows_checksum,
            "row_count": len(rows),
            "excluded_row_count": len(exclusions),
            "exclusion_reasons": dict(sorted(reasons.items())),
            "feature_schema_version": selected_schema,
            "feature_version_ids": feature_version_ids,
            "feature_names": feature_names,
            "target_definitions": TARGET_DEFINITIONS,
            "training_cutoff": _iso(cutoff),
            "leakage_checks": leakage_checks,
        }
        manifest_checksum = _checksum(identity)
        return TrainingDatasetManifestData(
            dataset_id=f"mlb-gate3-{manifest_checksum[:16]}",
            created_at=_iso(datetime.now(UTC)),
            date_start=min(dates).isoformat() if dates else None,
            date_end=max(dates).isoformat() if dates else None,
            seasons=sorted({value.year for value in dates}),
            feature_schema_version=selected_schema,
            feature_version=feature_versions[0] if len(feature_versions) == 1 else None,
            feature_version_id=feature_version_ids[0] if len(feature_version_ids) == 1 else None,
            feature_names=feature_names,
            row_count=len(rows),
            excluded_row_count=len(exclusions),
            exclusion_reasons=dict(sorted(reasons.items())),
            source_modes=source_modes,
            rows_checksum=rows_checksum,
            checksum=manifest_checksum,
            target_definitions=dict(TARGET_DEFINITIONS),
            training_cutoff=_iso(cutoff),
            leakage_checks=leakage_checks,
            feature_family_coverage=coverage,
        )


class TrainingDatasetArtifactStore:
    """Atomic, immutable, root-confined storage for governed dataset artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def save(self, result: DatasetBuildResult) -> TrainingDatasetManifestData:
        self.root.mkdir(parents=True, exist_ok=True)
        final = self._contained(self.root / result.manifest.dataset_id)
        if final.exists():
            existing = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
            if existing.get("checksum") != result.manifest.checksum:
                raise FileExistsError(f"Dataset identity collision at {final}")
            return TrainingDatasetManifestData(**existing)
        temporary = self.root / f".dataset-{uuid4().hex}"
        temporary.mkdir(mode=0o777, exist_ok=False)
        try:
            rows_path = temporary / "rows.jsonl"
            exclusions_path = temporary / "exclusions.json"
            manifest_path = temporary / "manifest.json"
            with rows_path.open("xb") as handle:
                for row in result.rows:
                    handle.write(_canonical(asdict(row)) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            rows_checksum = hashlib.sha256(rows_path.read_bytes().rstrip(b"\n")).hexdigest()
            if rows_checksum != result.manifest.rows_checksum:
                raise ValueError("Serialized training rows do not match the governed checksum")
            exclusions_path.write_bytes(_canonical(list(result.exclusions)))
            relative = final.relative_to(self.root.parent.parent).as_posix()
            manifest = TrainingDatasetManifestData(**{
                **asdict(result.manifest),
                "artifact_uri": relative,
            })
            manifest_path.write_text(json.dumps(asdict(manifest), indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temporary, final)
            return manifest
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def load(self, dataset_id: str) -> tuple[TrainingDatasetManifestData, tuple[TrainingRow, ...]]:
        if not dataset_id.startswith("mlb-gate3-") or any(value in dataset_id for value in ("/", "\\", "..")):
            raise ValueError("Invalid dataset identifier")
        directory = self._contained(self.root / dataset_id)
        manifest = TrainingDatasetManifestData(**json.loads((directory / "manifest.json").read_text(encoding="utf-8")))
        rows_path = directory / "rows.jsonl"
        actual = hashlib.sha256(rows_path.read_bytes().rstrip(b"\n")).hexdigest()
        if actual != manifest.rows_checksum:
            raise ValueError("Training dataset checksum mismatch")
        rows = tuple(
            TrainingRow(**json.loads(line))
            for line in rows_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        return manifest, rows

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("Artifact path escapes the governed root")
        return resolved


def _feature_family_coverage(rows: list[TrainingRow]) -> dict[str, float]:
    families = {
        "team_strength": ("home_rating", "away_rating", "home_runs_per_game", "away_runs_per_game"),
        "starting_pitcher": ("home_starter_era", "away_starter_era", "home_starter_xera", "away_starter_xera"),
        "lineup": ("home_lineup_xwoba", "away_lineup_xwoba"),
        "bullpen": ("home_bullpen_availability", "away_bullpen_availability"),
        "statcast": ("home_statcast_xwoba", "away_statcast_xwoba"),
        "recent_form": ("home_recent_runs", "away_recent_runs"),
        "park_weather": ("park_run_factor", "temperature_f", "wind_speed_mph"),
        "rest_travel": ("home_days_since_previous_game", "away_days_since_previous_game"),
        "defense_baserunning": ("defense", "baserunning"),
        "opponent_adjustment": ("opponent_adjustment",),
    }
    if not rows:
        return {family: 0.0 for family in families}
    result: dict[str, float] = {}
    for family, keys in families.items():
        observed = 0
        possible = len(rows) * len(keys)
        for row in rows:
            missing = set(row.missing_features)
            observed += sum(
                key not in missing
                and row.features.get(key) is not None
                and not (isinstance(row.features.get(key), float) and not math.isfinite(row.features[key]))
                for key in keys
            )
        result[family] = observed / possible if possible else 0.0
    return result


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _aware(parsed)


def _evidence_within_cutoff(raw_evidence: Iterable[dict[str, Any]], cutoff: datetime) -> bool:
    boundary = _aware(cutoff)
    for item in raw_evidence or []:
        available = item.get("available_as_of")
        observed = item.get("observed_at")
        if available is None or observed is None:
            return False
        try:
            if _parse_iso(str(available)) > boundary or _parse_iso(str(observed)) > boundary:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _model_ready_features(
    snapshot: GameFeatureSnapshot,
    state: GameStateObservation,
) -> dict[str, Any]:
    values = dict(snapshot.feature_values)
    values["feature_completeness"] = float(snapshot.feature_completeness)
    penalties = len(snapshot.missing_features or []) + len(snapshot.stale_features or [])
    values["sample_reliability"] = max(
        0.0,
        min(1.0, float(snapshot.feature_completeness) - penalties * 0.02),
    )
    values["lineups_confirmed"] = float(
        str(values.get("home_lineup_status", "")).upper() == "CONFIRMED"
        and str(values.get("away_lineup_status", "")).upper() == "CONFIRMED"
    )
    values["starters_confirmed"] = float(
        str(state.home_starter_confirmation or "").upper() == "CONFIRMED"
        and str(state.away_starter_confirmation or "").upper() == "CONFIRMED"
    )
    return values
