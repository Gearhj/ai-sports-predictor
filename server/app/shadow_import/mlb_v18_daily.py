"""Verify and import immutable MLB V18 daily-slate forecast objects.

This is an offline consumer. It verifies Model Studio's aggregate handoff and
every referenced object before any database write, then stores only precomputed
research-shadow evidence. It never loads or executes a model, calls a provider,
changes Gen 1, or grants wagering authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, ShadowForecast, ShadowForecastBundle
from app.shadow_import.mlb_v18 import ENGINE_ID, SHADOW_STATUS, path_digest
from app.shadow_import.mlb_v18_play_geometry import (
    VerifiedGeometry,
    register_geometry,
    verify_supplement,
)
from app.shadow_import.mlb_v18_population_geometry import (
    VerifiedPopulation,
    register_population,
    verify_population,
)

HANDOFF_SCHEMA = "AISP_MLB_V18_DAILY_SLATE_HANDOFF_V1"
HANDOFF_SCHEMA_V2 = "AISP_MLB_V18_DAILY_SLATE_HANDOFF_V2"
RECEIPT_SCHEMA = "MLB_V18_DAILY_SLATE_RECEIPT_V1"
OBJECT_SCHEMA = "MLB_V18_DAILY_GAME_OBJECT_MANIFEST_V1"
FORECAST_SCHEMA = "MLB_V18_DAILY_GAME_FORECAST_V1"
PATH_SCHEMA = "MLB_V18_DAILY_REPRESENTATIVE_PATH_V1"
CONTRACT_VERSION = "AISP_MLB_V18_DAILY_SLATE_HANDOFF_V1"
SIMULATOR_ID = "V18_WHOLE_GAME_MONTE_CARLO"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DailyV18ImportError(RuntimeError):
    """The handoff, receipt, object, or requested registration is unsafe."""


@dataclass(frozen=True)
class VerifiedDailyForecast:
    object_sha256: str
    object_root: Path
    manifest: dict[str, Any]
    forecast: dict[str, Any]
    evidence: dict[str, Any]
    representative_path: dict[str, Any]
    play_geometry: VerifiedGeometry | None = None
    population_geometry: VerifiedPopulation | None = None


@dataclass(frozen=True)
class VerifiedDailySlate:
    handoff_path: Path
    receipt_path: Path
    handoff: dict[str, Any]
    receipt: dict[str, Any]
    forecasts: tuple[VerifiedDailyForecast, ...]
    publisher_skips: tuple[dict[str, Any], ...]

    @property
    def handoff_sha256(self) -> str:
        return str(self.handoff["handoffSha256"])


@dataclass
class DailyRegistrationOutcome:
    imported: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    publisher_skips: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    play_geometry_registered: list[str] = field(default_factory=list)
    play_geometry_already_present: list[str] = field(default_factory=list)
    population_registered: list[str] = field(default_factory=list)
    population_already_present: list[str] = field(default_factory=list)
    bundle_created: bool = False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DailyV18ImportError(f"Required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DailyV18ImportError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DailyV18ImportError(f"Expected a JSON object in {path}")
    return value


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DailyV18ImportError("Evidence is not canonically serializable") from exc


def _sha_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if value in (None, ""):
        raise DailyV18ImportError("A required timestamp is missing")
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T", 1))
    except ValueError as exc:
        raise DailyV18ImportError(f"Invalid timestamp: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _require_sha(value: Any, label: str) -> str:
    digest = str(value or "").lower()
    if not _SHA256.fullmatch(digest):
        raise DailyV18ImportError(f"{label} is not a lowercase SHA-256 digest")
    return digest


def _safe_child(root: Path, relative: str) -> Path:
    if not relative or PurePosixPath(relative).is_absolute():
        raise DailyV18ImportError(f"Unsafe relative artifact path: {relative!r}")
    path = (root / PurePosixPath(relative)).resolve()
    resolved_root = root.resolve()
    if path != resolved_root and resolved_root not in path.parents:
        raise DailyV18ImportError(f"Artifact escapes its immutable root: {relative}")
    return path


def verify_daily_slate(
    handoff_path: str | Path,
    receipt_path: str | Path,
) -> VerifiedDailySlate:
    """Verify the aggregate, receipt, and all referenced objects fail-closed."""

    handoff_path = Path(handoff_path).resolve()
    receipt_path = Path(receipt_path).resolve()
    handoff = _read_json(handoff_path)
    handoff_schema = handoff.get("schemaVersion")
    if handoff_schema not in {HANDOFF_SCHEMA, HANDOFF_SCHEMA_V2}:
        raise DailyV18ImportError(
            f"Unsupported handoff schema {handoff.get('schemaVersion')!r}"
        )
    handoff_sha = _require_sha(handoff.get("handoffSha256"), "handoffSha256")
    if handoff_path.parent.name.lower() != handoff_sha:
        raise DailyV18ImportError("Handoff directory does not match handoffSha256")
    handoff_body = {
        key: value for key, value in handoff.items()
        if key not in {"handoffId", "handoffSha256"}
    }
    if _sha_json(handoff_body) != handoff_sha:
        raise DailyV18ImportError("Aggregate handoff checksum mismatch")
    _assert_research_only(handoff, execution_key="executionAllowedInAISP")

    # .../<output-root>/handoffs/sha256/<sha>/aisp-handoff.json
    try:
        output_root = handoff_path.parents[3]
    except IndexError as exc:
        raise DailyV18ImportError("Handoff path lacks its immutable output root") from exc

    entries = handoff.get("forecasts")
    if not isinstance(entries, list) or not entries:
        raise DailyV18ImportError("Aggregate handoff contains no forecasts")
    forecasts = tuple(
        _verify_object(
            output_root,
            entry,
            require_views=handoff_schema == HANDOFF_SCHEMA_V2,
        )
        for entry in entries
    )
    object_shas = [item.object_sha256 for item in forecasts]
    game_ids = [str(item.forecast.get("gameId") or "") for item in forecasts]
    if len(set(object_shas)) != len(object_shas):
        raise DailyV18ImportError("Aggregate handoff repeats an objectSha256")
    if len(set(game_ids)) != len(game_ids):
        raise DailyV18ImportError("Aggregate handoff repeats a gameId")

    receipt = _read_json(receipt_path)
    if receipt.get("schemaVersion") != RECEIPT_SCHEMA:
        raise DailyV18ImportError(
            f"Unsupported receipt schema {receipt.get('schemaVersion')!r}"
        )
    receipt_sha = _require_sha(receipt.get("receiptSha256"), "receiptSha256")
    if receipt_path.parent.name.lower() != receipt_sha:
        raise DailyV18ImportError("Receipt directory does not match receiptSha256")
    receipt_body = {
        key: value for key, value in receipt.items()
        if key not in {"receiptId", "receiptSha256"}
    }
    if _sha_json(receipt_body) != receipt_sha:
        raise DailyV18ImportError("Slate receipt checksum mismatch")
    _assert_research_only(receipt, execution_key="executionAllowedInAISP")
    if receipt.get("handoffSha256") != handoff_sha:
        raise DailyV18ImportError("Receipt references a different aggregate handoff")
    if receipt.get("slateDate") != handoff.get("slateDate"):
        raise DailyV18ImportError("Receipt and handoff disagree on slateDate")

    receipt_published = {
        (str(item.get("gameId")), str(item.get("objectSha256")))
        for item in (receipt.get("games") or [])
        if item.get("status") == "PUBLISHED"
    }
    expected = {(game_id, sha) for game_id, sha in zip(game_ids, object_shas)}
    if receipt_published != expected:
        raise DailyV18ImportError("Receipt and handoff disagree on published forecasts")
    skips = tuple(
        dict(item) for item in (receipt.get("games") or [])
        if item.get("status") == "SKIPPED"
    )
    if int(receipt.get("gamesSkipped", len(skips))) != len(skips):
        raise DailyV18ImportError("Receipt skip count does not match its skip evidence")

    return VerifiedDailySlate(
        handoff_path=handoff_path,
        receipt_path=receipt_path,
        handoff=handoff,
        receipt=receipt,
        forecasts=forecasts,
        publisher_skips=skips,
    )


def _assert_research_only(document: dict[str, Any], *, execution_key: str) -> None:
    expected = {
        "researchShadowOnly": True,
        "wageringAuthority": False,
        "promotionStatus": "NOT_PROMOTED",
        execution_key: False,
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise DailyV18ImportError(
                f"Research-only safety field {key} must be {value!r}"
            )


def _verify_object(
    output_root: Path,
    entry: Any,
    *,
    require_views: bool = False,
) -> VerifiedDailyForecast:
    if not isinstance(entry, dict):
        raise DailyV18ImportError("Malformed forecast pointer in aggregate handoff")
    object_sha = _require_sha(entry.get("objectSha256"), "objectSha256")
    object_root = _safe_child(output_root, str(entry.get("relativePath") or ""))
    if object_root.name.lower() != object_sha or not object_root.is_dir():
        raise DailyV18ImportError("Forecast object path does not match objectSha256")

    manifest_name = str(entry.get("manifestFile") or "")
    forecast_name = str(entry.get("forecastFile") or "")
    manifest = _read_json(_safe_child(object_root, manifest_name))
    if manifest.get("schemaVersion") != OBJECT_SCHEMA:
        raise DailyV18ImportError(
            f"Unsupported object manifest schema {manifest.get('schemaVersion')!r}"
        )
    if manifest.get("objectSha256") != object_sha:
        raise DailyV18ImportError("Object manifest names a different objectSha256")
    body = {
        key: value for key, value in manifest.items()
        if key not in {"objectId", "objectSha256"}
    }
    if _sha_json(body) != object_sha:
        raise DailyV18ImportError(f"Object manifest checksum mismatch: {object_sha}")
    _assert_research_only(manifest, execution_key="executionAllowedInAISP")

    verified_files: set[str] = set()
    for artifact in manifest.get("artifacts") or []:
        relative = str(artifact.get("relativePath") or "")
        expected_sha = _require_sha(artifact.get("sha256"), f"artifact {relative} sha256")
        path = _safe_child(object_root, relative)
        try:
            payload = path.read_bytes()
        except FileNotFoundError as exc:
            raise DailyV18ImportError(f"Referenced artifact is missing: {relative}") from exc
        if len(payload) != int(artifact.get("bytes", -1)):
            raise DailyV18ImportError(f"Artifact byte count mismatch: {relative}")
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise DailyV18ImportError(f"Artifact checksum mismatch: {relative}")
        verified_files.add(relative)

    required = {forecast_name, "pregame-evidence.json", "representative-path.json"}
    if not required.issubset(verified_files):
        missing = sorted(required - verified_files)
        raise DailyV18ImportError(f"Required object artifacts are unverified: {missing}")
    forecast = _read_json(_safe_child(object_root, forecast_name))
    evidence = _read_json(_safe_child(object_root, "pregame-evidence.json"))
    representative_path = _read_json(
        _safe_child(object_root, "representative-path.json")
    )
    _validate_forecast(entry, manifest, forecast, evidence, representative_path)
    play_geometry = None
    population_geometry = None
    if entry.get("playGeometry"):
        play_root = _view_root(output_root, entry["playGeometry"], "Play Geometry")
        try:
            play_geometry = verify_supplement(play_root)
        except Exception as exc:
            raise DailyV18ImportError(f"Play Geometry verification failed: {exc}") from exc
        if play_geometry.parent_bundle_sha256 != object_sha:
            raise DailyV18ImportError("Play Geometry names a different daily forecast object")
        path = next(
            (
                value for value in play_geometry.paths
                if str(value.get("gameId")) == str(forecast.get("gameId"))
            ),
            None,
        )
        source_events = representative_path.get("events") or []
        geometry_events = (path or {}).get("events") or []
        if len(source_events) != len(geometry_events):
            raise DailyV18ImportError("Play Geometry does not cover every representative-path PA")
        for source, enriched in zip(source_events, geometry_events, strict=True):
            if any(enriched.get(key) != value for key, value in source.items()):
                raise DailyV18ImportError("Play Geometry altered persisted representative-path state")
            geometry = enriched.get("geometry") or {}
            if geometry.get("landingOrFieldingPoint") is not None and not enriched.get("ballTrace"):
                raise DailyV18ImportError("A supported Play Geometry contact has no ball path")
            if any(not move.get("anonymousRunnerId") for move in enriched.get("runnerMovements") or []):
                raise DailyV18ImportError("Play Geometry carries a non-anonymous runner movement")
    if entry.get("populationGeometry"):
        population_root = _view_root(
            output_root, entry["populationGeometry"], "Simulation Population"
        )
        try:
            population_geometry = verify_population(population_root)
        except Exception as exc:
            raise DailyV18ImportError(f"Simulation Population verification failed: {exc}") from exc
        if population_geometry.parent_bundle_sha256 != object_sha:
            raise DailyV18ImportError("Simulation Population names a different daily forecast object")
        if play_geometry is None or (
            population_geometry.play_geometry_sha256
            != play_geometry.supplement_sha256
        ):
            raise DailyV18ImportError("Simulation Population names a different Play Geometry parent")
        _validate_population_reproduction(forecast, population_geometry.summary)
    if require_views and (play_geometry is None or population_geometry is None):
        raise DailyV18ImportError("Three-view daily handoff is missing a required view artifact")
    return VerifiedDailyForecast(
        object_sha256=object_sha,
        object_root=object_root,
        manifest=manifest,
        forecast=forecast,
        evidence=evidence,
        representative_path=representative_path,
        play_geometry=play_geometry,
        population_geometry=population_geometry,
    )


def _view_root(output_root: Path, reference: Any, label: str) -> Path:
    if not isinstance(reference, dict):
        raise DailyV18ImportError(f"Malformed {label} reference")
    digest = _require_sha(reference.get("supplementSha256"), f"{label} supplementSha256")
    root = _safe_child(output_root, str(reference.get("relativePath") or ""))
    if root.name.lower() != digest or not root.is_dir():
        raise DailyV18ImportError(f"{label} path does not match its checksum identity")
    if reference.get("manifestFile") != "manifest.json":
        raise DailyV18ImportError(f"{label} manifest is not explicitly named")
    return root


def _validate_population_reproduction(
    forecast: dict[str, Any], summary: dict[str, Any]
) -> None:
    final = summary.get("finalPopulation") or {}
    expected = {
        "homeWinProbability": forecast["probabilities"]["homeWin"],
        "meanAwayRuns": forecast["expectations"]["awayRuns"],
        "meanHomeRuns": forecast["expectations"]["homeRuns"],
        "meanTotalRuns": forecast["expectations"]["totalRuns"],
        "meanRunDifferential": forecast["expectations"]["homeMinusAwayRuns"],
        "oneRunRate": forecast["rates"]["oneRunGame"],
        "extraInningRate": forecast["rates"]["extraInning"],
        "simulationCount": forecast["simulation"]["count"],
        "masterSeed": forecast["simulation"]["seed"],
        "illegalStateCount": forecast["simulation"]["illegalStateCount"],
        "nonterminationCount": forecast["simulation"]["nonterminationCount"],
    }
    for key, value in expected.items():
        actual = final.get(key) if key in final else summary.get(key)
        if actual is None or abs(float(actual) - float(value)) > 1e-12:
            raise DailyV18ImportError(
                f"Simulation Population does not reproduce forecast metric {key}"
            )
    reconstruction = summary.get("reconstruction") or {}
    if (
        reconstruction.get("allMetricsMatch") is not True
        or reconstruction.get("deterministicReplay") is not True
    ):
        raise DailyV18ImportError("Simulation Population lacks exact deterministic reproduction")


def _validate_forecast(
    entry: dict[str, Any],
    manifest: dict[str, Any],
    forecast: dict[str, Any],
    evidence: dict[str, Any],
    representative_path: dict[str, Any],
) -> None:
    if forecast.get("schemaVersion") != FORECAST_SCHEMA:
        raise DailyV18ImportError(
            f"Unsupported daily forecast schema {forecast.get('schemaVersion')!r}"
        )
    _assert_research_only(forecast, execution_key="executionAllowedInAISP")
    game_id = str(forecast.get("gameId") or "")
    if game_id != str(entry.get("gameId")) or game_id != str(manifest.get("gameId")):
        raise DailyV18ImportError("Forecast, manifest, and handoff game IDs differ")
    try:
        uuid.UUID(game_id)
    except ValueError as exc:
        raise DailyV18ImportError(f"Forecast gameId is not a UUID: {game_id!r}") from exc
    simulation = forecast.get("simulation") or {}
    if simulation.get("count") != 5000:
        raise DailyV18ImportError("Daily forecast must contain exactly 5,000 simulations")
    if simulation.get("illegalStateCount") != 0:
        raise DailyV18ImportError("Daily forecast contains illegal states")
    if simulation.get("nonterminationCount") != 0:
        raise DailyV18ImportError("Daily forecast contains nonterminating simulations")
    if simulation.get("deterministicReplay") is not True:
        raise DailyV18ImportError("Daily forecast lacks deterministic replay verification")
    captured = _aware(forecast.get("capturedAt"))
    scheduled = _aware(forecast.get("scheduledStart"))
    if captured >= scheduled:
        raise DailyV18ImportError("Daily forecast was captured at or after first pitch")
    if evidence.get("game", {}).get("game_id") != game_id:
        raise DailyV18ImportError("Pregame evidence names a different game")
    if evidence.get("contentSha256") != forecast.get("snapshotContentSha256"):
        raise DailyV18ImportError("Forecast and pregame evidence digests differ")
    if evidence.get("providerCalls") != 0 or evidence.get("aispRowsModified") != 0:
        raise DailyV18ImportError("Pregame evidence reports provider calls or AISP writes")
    if representative_path.get("schemaVersion") != PATH_SCHEMA:
        raise DailyV18ImportError("Unsupported representative-path schema")
    if representative_path.get("gameId") != game_id:
        raise DailyV18ImportError("Representative path names a different game")
    if not representative_path.get("events"):
        raise DailyV18ImportError("Daily forecast has no representative sequential path")


def forecast_identity(object_sha256: str) -> str:
    """Object-addressed identity: the deduplication key is the complete SHA."""

    return f"mlb-v18-daily-{object_sha256}"


def register_daily_slate(
    session: Session,
    verified: VerifiedDailySlate,
    *,
    now: datetime | None = None,
) -> DailyRegistrationOutcome:
    """Register verified daily objects idempotently without touching Gen 1."""

    now = now or datetime.now(UTC)
    outcome = DailyRegistrationOutcome(
        publisher_skips=[dict(item) for item in verified.publisher_skips]
    )
    game_by_id: dict[str, Game] = {}
    requested_ids = {
        str(item.forecast["gameId"]) for item in verified.forecasts
    } | {str(item.get("gameId")) for item in verified.publisher_skips}
    for game_id in sorted(requested_ids):
        try:
            game = session.get(Game, uuid.UUID(game_id))
        except ValueError:
            game = None
        if game is None:
            outcome.rejected.append({
                "gameId": game_id,
                "reason": "no exact AISP game ID exists",
            })
        else:
            game_by_id[game_id] = game

    bundle = session.scalar(
        select(ShadowForecastBundle).where(
            ShadowForecastBundle.bundle_sha256 == verified.handoff_sha256
        )
    )
    if bundle is None:
        bundle = ShadowForecastBundle(
            bundle_sha256=verified.handoff_sha256,
            contract_version=str(verified.handoff.get("schemaVersion") or CONTRACT_VERSION),
            bundle_id=str(verified.handoff.get("handoffId") or ""),
            logical_uri=str(verified.handoff_path),
            source_fixture_id=str(verified.receipt.get("receiptSha256") or ""),
            slate_date=datetime.fromisoformat(str(verified.handoff["slateDate"])).date(),
            league="MLB",
            sport="MLB",
            status="RESEARCH_SHADOW",
            research_shadow_only=True,
            generated_at=_aware(verified.handoff.get("generatedAt")),
            forecast_count=len(verified.forecasts),
            quote_count=0,
            candidate_count=0,
            evidence={
                "schemaVersion": "AISP_MLB_V18_DAILY_SLATE_IMPORT_EVIDENCE_V1",
                "handoffSha256": verified.handoff_sha256,
                "receiptSha256": verified.receipt.get("receiptSha256"),
                "slateDate": verified.handoff.get("slateDate"),
                "publisherSkips": [dict(item) for item in verified.publisher_skips],
                "wageringAuthority": False,
                "promotionStatus": "NOT_PROMOTED",
                "executionAllowedInAISP": False,
                "gen1HistoryMutated": False,
            },
            imported_at=now,
        )
        session.add(bundle)
        session.flush()
        outcome.bundle_created = True

    registered_items: list[VerifiedDailyForecast] = []
    for item in verified.forecasts:
        game_id = str(item.forecast["gameId"])
        if game_id not in game_by_id:
            continue
        game = game_by_id[game_id]
        captured = _aware(item.forecast.get("capturedAt"))
        actual_start = game.starts_at
        if actual_start is None:
            outcome.rejected.append({
                "gameId": game_id,
                "reason": "AISP game has no first-pitch timestamp",
            })
            continue
        actual_start = actual_start if actual_start.tzinfo else actual_start.replace(tzinfo=UTC)
        if captured >= actual_start:
            outcome.rejected.append({
                "gameId": game_id,
                "reason": "forecast capture is at or after AISP first pitch",
            })
            continue

        identity = forecast_identity(item.object_sha256)
        existing = session.scalar(
            select(ShadowForecast).where(ShadowForecast.forecast_id == identity)
        )
        if existing is not None:
            lineage = existing.lineage_detail or {}
            if (
                str(existing.canonical_game_id) != game_id
                or lineage.get("objectSha256") != item.object_sha256
            ):
                raise DailyV18ImportError(
                    f"Existing object-addressed forecast conflicts: {identity}"
                )
            outcome.already_present.append(game_id)
        else:
            session.add(_forecast_row(item, game, bundle.id))
            outcome.imported.append(game_id)
        registered_items.append(item)

    session.flush()
    for item in registered_items:
        if item.play_geometry is None and item.population_geometry is None:
            continue
        if item.play_geometry is None or item.population_geometry is None:
            raise DailyV18ImportError("Daily forecast carries only one of two required view artifacts")
        play = register_geometry(session, item.play_geometry, now=now)
        if play["skipped"]:
            raise DailyV18ImportError(f"Play Geometry registration skipped: {play['skipped']}")
        outcome.play_geometry_registered.extend(play["registered"])
        outcome.play_geometry_already_present.extend(play["alreadyRegistered"])
        population = register_population(session, item.population_geometry, now=now)
        if population["skipped"]:
            raise DailyV18ImportError(
                f"Simulation Population registration skipped: {population['skipped']}"
            )
        outcome.population_registered.extend(population["registered"])
        outcome.population_already_present.extend(population["alreadyRegistered"])
    session.flush()
    return outcome


def _distribution(rows: Any) -> dict[str, float]:
    return {
        str(item["value"]): float(item["probability"])
        for item in (rows or [])
    }


def _forecast_row(
    item: VerifiedDailyForecast,
    game: Game,
    bundle_id: uuid.UUID,
) -> ShadowForecast:
    forecast = item.forecast
    simulation = forecast["simulation"]
    probabilities = forecast["probabilities"]
    expectations = forecast["expectations"]
    rates = forecast.get("rates") or {}
    uncertainty = forecast.get("monteCarloUncertainty") or {}
    parent = forecast.get("parentAuthority") or {}
    events = list(item.representative_path.get("events") or [])
    captured = _aware(forecast["capturedAt"])
    scheduled = _aware(forecast["scheduledStart"])
    teams = forecast.get("teams") or {}
    lineup_authorities = forecast.get("lineupAuthorities") or {}
    starters = forecast.get("starters") or {}
    distributions = forecast.get("distributions") or {}

    return ShadowForecast(
        bundle_id=bundle_id,
        canonical_game_id=game.id,
        forecast_id=forecast_identity(item.object_sha256),
        forecast_schema_version=FORECAST_SCHEMA,
        snapshot_type="PROSPECTIVE",
        snapshot_version=1,
        engine_id=ENGINE_ID,
        engine_version=str(parent.get("bundleId") or "MLB_V18_DAILY"),
        model_artifact_sha256=str(parent.get("paModelSha256") or ""),
        model_manifest_sha256=item.object_sha256,
        feature_vector_sha256=str(forecast.get("snapshotContentSha256") or "") or None,
        simulator_id=SIMULATOR_ID,
        simulator_version=FORECAST_SCHEMA,
        simulator_seed=int(simulation["seed"]),
        event_id=str(game.id),
        source_event_id=str(forecast.get("sourceGameId") or game.id),
        event_date=scheduled.date(),
        season=scheduled.year,
        season_type="REGULAR",
        home_team_id=str((teams.get("HOME") or {}).get("uuid") or ""),
        away_team_id=str((teams.get("AWAY") or {}).get("uuid") or ""),
        is_ready=True,
        readiness_score=1.0,
        readiness_threshold=1.0,
        status=SHADOW_STATUS,
        scheduled_start_utc=scheduled,
        schedule_as_of_utc=captured,
        source_cutoff_utc=captured,
        forecast_cutoff_utc=captured,
        available_as_of_utc=captured,
        full_home_win_probability=float(probabilities["homeWin"]),
        full_away_win_probability=float(probabilities["awayWin"]),
        full_expected_home_score=float(expectations["homeRuns"]),
        full_expected_away_score=float(expectations["awayRuns"]),
        f5_home_win_probability=None,
        f5_away_win_probability=None,
        f5_expected_home_score=None,
        f5_expected_away_score=None,
        distributions={
            "homeRunDistribution": _distribution(distributions.get("homeRuns")),
            "awayRunDistribution": _distribution(distributions.get("awayRuns")),
            "totalRunDistribution": _distribution(distributions.get("totalRuns")),
            "runDifferentialDistribution": _distribution(
                distributions.get("homeMinusAwayRuns")
            ),
            "meanTotalRuns": expectations.get("totalRuns"),
            "meanRunDifferential": expectations.get("homeMinusAwayRuns"),
            "oneRunProbability": rates.get("oneRunGame"),
            "extraInningProbability": rates.get("extraInning"),
        },
        uncertainty={
            "simulationCount": simulation["count"],
            "monteCarloStandardError": uncertainty.get(
                "homeWinProbabilityStandardError"
            ),
            "monteCarlo95HalfWidth": (
                (uncertainty.get("homeWinProbabilityInterval") or [None, None])[1]
                - float(probabilities["homeWin"])
                if (uncertainty.get("homeWinProbabilityInterval") or [None, None])[1]
                is not None
                else None
            ),
            "monteCarloMethod": uncertainty.get("method"),
            "seedPolicy": simulation.get("seedPolicy"),
            "deterministicReplayVerified": simulation.get("deterministicReplay"),
            "illegalStateCount": simulation.get("illegalStateCount"),
            "nonterminationCount": simulation.get("nonterminationCount"),
        },
        readiness_detail={
            "gate": "PUBLISHED_DAILY_V18_PROSPECTIVE_EVIDENCE",
            "capturedBeforeScheduledStart": True,
            "lineupModes": [
                (lineup_authorities.get(side) or {}).get("status")
                for side in ("AWAY", "HOME")
            ],
            "sides": {
                side: {
                    "team": (teams.get(side) or {}).get("abbreviation"),
                    "lineupStatus": (lineup_authorities.get(side) or {}).get("status"),
                    "lineupObservedAt": (lineup_authorities.get(side) or {}).get(
                        "observedAt"
                    ),
                    "lineupSource": (lineup_authorities.get(side) or {}).get("source"),
                    "starter": (starters.get(side) or {}).get("name"),
                    "starterState": (starters.get(side) or {}).get("confirmationState"),
                    "starterObservedAt": (starters.get(side) or {}).get("observedAt"),
                }
                for side in ("AWAY", "HOME")
            },
            "limitations": forecast.get("knownLimitations") or [],
        },
        identity_evidence={
            "gameId": str(game.id),
            "sourceGameId": forecast.get("sourceGameId"),
            "homeTeam": (teams.get("HOME") or {}).get("abbreviation"),
            "awayTeam": (teams.get("AWAY") or {}).get("abbreviation"),
            "pregameSnapshotSha256": forecast.get("snapshotContentSha256"),
        },
        lineage_detail={
            "bundleId": str(item.manifest.get("objectId") or ""),
            "bundleSha256": item.object_sha256,
            "objectSha256": item.object_sha256,
            "parentBundleId": parent.get("bundleId"),
            "parentBundleSha256": parent.get("bundleSha256"),
            "acceptanceProtocolV2Sha256": parent.get("acceptanceProtocolV2Sha256"),
            "evidenceCutoff": captured.isoformat(),
            "sourceArtifact": str(item.object_root),
            "inputReceipts": {
                "pregameSnapshotCapturedAt": item.evidence.get("capturedAt"),
                "captureMode": item.evidence.get("captureMode"),
                "providerCalls": item.evidence.get("providerCalls"),
                "marketDataRead": item.evidence.get("marketDataRead"),
                "outcomesRead": item.evidence.get("outcomesRead"),
                "aispRowsModified": item.evidence.get("aispRowsModified"),
            },
            "pathDigest": path_digest([events]),
            "sequentialPath": [events],
            "availableViews": {
                "broadcast": True,
                "geometry": False,
                "population": False,
            },
            "participationPolicy": {
                "executionAllowed": False,
                "wageringAuthority": False,
                "promotionStatus": "NOT_PROMOTED",
                "status": "RESEARCH_SHADOW",
            },
            "gen1HistoryMutated": False,
            "wageringAuthority": False,
        },
    )


__all__ = [
    "DailyRegistrationOutcome",
    "DailyV18ImportError",
    "VerifiedDailyForecast",
    "VerifiedDailySlate",
    "forecast_identity",
    "register_daily_slate",
    "verify_daily_slate",
]
