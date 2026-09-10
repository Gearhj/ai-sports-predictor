"""Read-only verification and append-only registration of NFL_LABS_PLAY_LEVEL_CONTRACT_V2.

WHY THIS IS A SEPARATE FAMILY
-----------------------------
The play-level engine is a third, independent research track. It does not
explain, replace or rank against M1, and it does not supersede the drive
engine; the contract says so and this importer refuses any artifact that
claims otherwise. Its only privilege is that it may be *animated*, because it
is the first family that actually carries plays.

WHAT IS VERIFIED, IN ORDER, FAIL CLOSED
---------------------------------------
1.  The pointer is canonical JSON, declares the expected schema, and is
    governance-clean (research shadow, unpromoted, no execution, no wagering).
2.  The coordination CONTRACT and the producer HANDOFF are verified by bytes
    and length before either is read.
3.  Per artifact: the content address is a well-formed, unrepeated 64-hex
    string; the directory holds exactly manifest.json and artifact.json; the
    manifest verifies, agrees with the pointer, and is governance-clean; the
    artifact verifies BEFORE it is interpreted as data.
4.  Identity binding is declared, never inferred: sport, event, forecast and
    engine must match the pointer entry, and the artifact must name its related
    M1 forecast while explicitly disclaiming that it explains it.
5.  The published distribution reconciles to its own population: joint, margin
    and total distributions, the expected scores, and the outcome probabilities.
6.  THE SEQUENCE INTEGRITY RULES ARE RE-DERIVED FROM THE PLAYS THEMSELVES.
    The producer's own validationReceipt is evidence that it checked; it is not
    a reason for us not to. Every adjacent state must join exactly, play numbers
    must be contiguous, drive numbers nondecreasing, the clock must reconcile to
    the consumed seconds, the score must reconcile to the point increments, and
    the replay must finish FINAL on the score its population entry declares.
7.  Geometry must stay inside its declared frame and keep its anonymity flags.

WHERE THE BYTES LIVE
--------------------
The 112 artifacts are 316 MB, and 92% of that is ``simulatedGames``. Copying
that into a JSON column would put a third of a gigabyte of visualization data
into a table whose purpose is identity and evidence. So the row stores the
evidence -- summaries, distributions, population, governance, limitations, and
a light index of the representative games -- and the plays are read back from
the immutable content-addressed file on demand, with the bytes re-hashed
against the address recorded on the row. The file cannot drift without
detection, because the address IS the checksum.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.forecast_exchange.nfl_labs_v1 import _integer, _verified_file, _verify_distribution
from app.forecast_exchange.supplements import SupplementImportError, _iso, _load_canonical
from app.models import ResearchSupplement, ShadowForecast

#: The producer bumped the contract to V2 to add `populationSpatial`. An event
#: past kickoff cannot be regenerated -- the producer refuses inference after
#: kickoff -- so its V1 artifacts are carried forward unchanged and the index
#: legitimately lists both versions. Both are accepted; the newer contract is
#: required, because it is the one that describes what the index now contains.
CONTRACT = "NFL_LABS_PLAY_LEVEL_CONTRACT_V2"
SUPPORTED_CURRENT = ("NFL_LABS_PLAY_LEVEL_CURRENT_V2", "NFL_LABS_PLAY_LEVEL_CURRENT_V1")
CURRENT = SUPPORTED_CURRENT[0]
MANIFEST = "NFL_LABS_ARTIFACT_MANIFEST_V1"
ARTIFACT = "NFL_LABS_PLAY_LEVEL_SIMULATION_V2"
#: Ordered newest first. A V1 artifact carries no populationSpatial and must
#: never be rendered as though it did.
SUPPORTED_ARTIFACTS = ("NFL_LABS_PLAY_LEVEL_SIMULATION_V2", "NFL_LABS_PLAY_LEVEL_SIMULATION_V1")
SPATIAL = "NFL_POPULATION_SPATIAL_V1"
GAME = "NFL_PLAY_LEVEL_GAME_V1"
GEOMETRY = "NFL_MODELED_PLAY_GEOMETRY_V1"
PLAY_ENGINE = "NFL_DEVELOPMENT_HIERARCHICAL_PLAY_V1"
M1 = "NFL_M1_XGBOOST"
ROOT_NAME = "nfl-labs-play-level-supplements"

#: The supplement kind. Deliberately distinct from DRIVE_SIMULATION: a reader
#: that filters for drives must not silently pick up plays, and vice versa.
KIND_PLAY = "PLAY_LEVEL_SIMULATION"

PLAY_TYPES = frozenset({
    "RUSH", "QB_SNEAK", "QB_SCRAMBLE", "PASS_COMPLETE", "PASS_INCOMPLETE", "SACK",
    "INTERCEPTION", "PUNT", "FIELD_GOAL", "EXTRA_POINT", "TWO_POINT", "KICKOFF",
    "KNEEL", "SPIKE", "PENALTY", "PERIOD_END",
})
PHASES = frozenset({"SCRIMMAGE", "KICKOFF", "TRY", "FINAL"})
SCORING_TYPES = frozenset({"TD", "FG", "XP", "TWO_POINT", "SAFETY"})
STATE_FIELDS = (
    "possession", "quarter", "clockSeconds", "gameSecondsRemaining", "down",
    "distance", "yardline100", "homeScore", "awayScore", "phase",
)
#: PERIOD_END is the only event that may move the quarter or hold the clock.
BOUNDARY = "PERIOD_END"


@dataclass(frozen=True)
class VerifiedPlayArtifact:
    address: str
    artifact: dict[str, Any]
    files: dict[str, Any]
    receipt: dict[str, Any]

    @property
    def event_id(self) -> str:
        return str(self.artifact["eventId"])

    @property
    def scenario_id(self) -> str:
        return str((self.artifact.get("scenario") or {}).get("scenarioId"))


def _governance(doc: dict[str, Any], *, label: str) -> None:
    if doc.get("status") != "RESEARCH_SHADOW" or doc.get("promotionStatus") != "NOT_PROMOTED":
        raise SupplementImportError(f"{label} must remain unpromoted research shadow")
    if doc.get("executionAllowedInAISP") is not False or doc.get("wageringAuthority") is not False:
        raise SupplementImportError(f"{label} execution and wagering authority must be false")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SupplementImportError(f"{label} must be a finite number")
    return float(value)


def _state(raw: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SupplementImportError(f"{label} is not a state object")
    for field in STATE_FIELDS:
        if field not in raw:
            raise SupplementImportError(f"{label} is missing required state field '{field}'")
    if raw["possession"] not in ("HOME", "AWAY"):
        raise SupplementImportError(f"{label} possession is not HOME or AWAY")
    if raw["phase"] not in PHASES:
        raise SupplementImportError(f"{label} phase is unsupported")
    quarter = _integer(raw["quarter"], f"{label} quarter")
    if not 1 <= quarter <= 5:
        raise SupplementImportError(f"{label} quarter is out of range")
    if not 0 <= _number(raw["clockSeconds"], f"{label} clockSeconds") <= 900:
        raise SupplementImportError(f"{label} clock is out of range")
    if not 0 <= _number(raw["yardline100"], f"{label} yardline100") <= 100:
        raise SupplementImportError(f"{label} yard line is out of range")
    if not 0 <= _integer(raw["down"], f"{label} down") <= 4:
        raise SupplementImportError(f"{label} down is out of range")
    for field in ("homeScore", "awayScore"):
        if _integer(raw[field], f"{label} {field}") < 0:
            raise SupplementImportError(f"{label} {field} is negative")
    return raw


def _verify_geometry(geometry: Any, *, play_type: str, label: str) -> None:
    """The frame, the anonymity flags and the bounds -- nothing else is trusted."""
    if not isinstance(geometry, dict):
        raise SupplementImportError(f"{label} has no geometry object")
    if geometry.get("schemaVersion") != GEOMETRY:
        raise SupplementImportError(f"{label} geometry schema is unsupported")
    if geometry.get("classification") != "MODELED_VISUALIZATION_NOT_MEASURED_TRACKING":
        raise SupplementImportError(f"{label} geometry must declare itself modeled, not tracked")
    if geometry.get("measuredTracking") is not False or geometry.get("playerIdentities") is not None:
        raise SupplementImportError(f"{label} geometry must carry no measured tracking or player identity")
    if geometry.get("coordinateSystem") != "X_YARDS_FROM_LEFT_GOAL_Y_NORMALIZED_SCHEMATIC_WIDTH":
        raise SupplementImportError(f"{label} geometry coordinate system is unsupported")
    if geometry.get("possessionDirection") not in ("LEFT", "RIGHT"):
        raise SupplementImportError(f"{label} geometry possession direction is invalid")
    if geometry.get("playType") != play_type:
        raise SupplementImportError(f"{label} geometry play type differs from the play")

    def point(raw: Any, where: str) -> None:
        if not isinstance(raw, dict):
            raise SupplementImportError(f"{label} {where} is not a point")
        if not 0 <= _number(raw.get("x"), f"{label} {where}.x") <= 100:
            raise SupplementImportError(f"{label} {where} x escapes the field")
        if not 0 <= _number(raw.get("y"), f"{label} {where}.y") <= 1:
            raise SupplementImportError(f"{label} {where} y escapes the renderer width")

    segments = geometry.get("segments")
    if not isinstance(segments, list):
        raise SupplementImportError(f"{label} geometry segments are missing")
    if play_type == BOUNDARY and segments:
        raise SupplementImportError(f"{label} is a period boundary and must have no ball path")
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise SupplementImportError(f"{label} segment {index} is not an object")
        if segment.get("interpolation") not in ("LINEAR", "POLYLINE", "CUBIC_BEZIER"):
            raise SupplementImportError(f"{label} segment {index} interpolation is unsupported")
        if segment.get("possession") not in ("HOME", "AWAY"):
            raise SupplementImportError(f"{label} segment {index} possession is invalid")
        if segment.get("physicalTrajectory") is not False:
            raise SupplementImportError(f"{label} segment {index} must not claim a physical trajectory")
        if segment.get("lateralBasis") != "SCHEMATIC_LAYOUT_NOT_FITTED_ROUTE":
            raise SupplementImportError(f"{label} segment {index} must declare a schematic lateral basis")
        point(segment.get("start"), f"segment {index} start")
        point(segment.get("end"), f"segment {index} end")
        for control_index, control in enumerate(segment.get("controlPoints") or []):
            point(control, f"segment {index} control {control_index}")
    for index, marker in enumerate(geometry.get("markers") or []):
        if not isinstance(marker, dict) or not marker.get("kind"):
            raise SupplementImportError(f"{label} marker {index} has no kind")
        point(marker.get("point"), f"marker {index}")


def _verify_game(game: dict[str, Any], *, population: list[dict[str, Any]], label: str) -> dict[str, int]:
    """Re-derive every sequence-integrity rule from the plays themselves."""
    if game.get("schemaVersion") != GAME:
        raise SupplementImportError(f"{label} game schema is unsupported")
    index = _integer(game.get("simulationIndex"), f"{label} simulationIndex")
    if not 0 <= index < len(population):
        raise SupplementImportError(f"{label} simulation index is outside its population")
    member = population[index]
    if _integer(game.get("seed"), f"{label} seed") != _integer(member["seed"], "population seed"):
        raise SupplementImportError(f"{label} seed differs from its population member")
    for field in ("homeScore", "awayScore"):
        if _integer(game.get(field), f"{label} {field}") != _integer(member[field], f"population {field}"):
            raise SupplementImportError(f"{label} final score differs from its population member")
    if not str(game.get("label") or "").strip():
        raise SupplementImportError(f"{label} has no representative label")

    plays = game.get("plays")
    if not isinstance(plays, list) or not plays:
        raise SupplementImportError(f"{label} has no plays")

    previous_after: dict[str, Any] | None = None
    previous_drive = 0
    scoring = 0
    for position, play in enumerate(plays, 1):
        where = f"{label} play {position}"
        if not isinstance(play, dict):
            raise SupplementImportError(f"{where} is not an object")
        play_type = play.get("playType")
        if play_type not in PLAY_TYPES:
            raise SupplementImportError(f"{where} play type is unsupported")
        if _integer(play.get("playNumber"), f"{where} playNumber") != position:
            raise SupplementImportError(f"{where} play numbers are not contiguous from one")
        drive = _integer(play.get("driveNumber"), f"{where} driveNumber")
        if drive < previous_drive:
            raise SupplementImportError(f"{where} drive number decreased")
        previous_drive = drive
        if not str(play.get("description") or "").strip():
            raise SupplementImportError(f"{where} has no description")

        before = _state(play.get("stateBefore"), label=f"{where} stateBefore")
        after = _state(play.get("stateAfter"), label=f"{where} stateAfter")
        # Rule 1: no hidden possession, placement, clock or score reset between plays.
        if previous_after is not None:
            for field in STATE_FIELDS:
                if previous_after[field] != before[field]:
                    raise SupplementImportError(
                        f"{where} stateBefore.{field} does not continue the previous "
                        f"stateAfter ({previous_after[field]!r} then {before[field]!r})"
                    )
        previous_after = after

        outcome = play.get("outcome")
        if not isinstance(outcome, dict):
            raise SupplementImportError(f"{where} has no outcome")
        for field in ("endYardline100", "seconds", "homePointsAdded", "awayPointsAdded"):
            if field not in outcome:
                raise SupplementImportError(f"{where} outcome is missing '{field}'")
        seconds = _number(outcome["seconds"], f"{where} outcome.seconds")
        if seconds < 0:
            raise SupplementImportError(f"{where} consumed negative time")
        for field in ("endYardline100", "turnoverYardline100", "returnEndYardline100"):
            value = outcome.get(field)
            if value is not None and not 0 <= _number(value, f"{where} {field}") <= 100:
                raise SupplementImportError(f"{where} {field} escapes the field")
        scoring_type = outcome.get("scoringType")
        if scoring_type is not None and scoring_type not in SCORING_TYPES:
            raise SupplementImportError(f"{where} scoring type is unsupported")

        # Rule 3/4: only a period boundary may move the quarter or hold the clock.
        if play_type == BOUNDARY:
            if seconds != 0:
                raise SupplementImportError(f"{where} is a period boundary and must consume zero seconds")
        else:
            if before["quarter"] != after["quarter"]:
                raise SupplementImportError(f"{where} changed quarter without a period boundary")
            consumed = _number(before["clockSeconds"], "clockBefore") - _number(after["clockSeconds"], "clockAfter")
            if not math.isclose(consumed, seconds, abs_tol=1e-6):
                raise SupplementImportError(
                    f"{where} clock consumed {consumed} but the outcome declares {seconds}"
                )

        # Rule 5: both increments equal after minus before.
        for side, field in (("home", "homePointsAdded"), ("away", "awayPointsAdded")):
            added = _integer(outcome[field], f"{where} {field}")
            actual = _integer(after[f"{side}Score"], "after") - _integer(before[f"{side}Score"], "before")
            if added != actual:
                raise SupplementImportError(
                    f"{where} {field} is {added} but the score moved by {actual}"
                )
            if added:
                scoring += 1

        _verify_geometry(play.get("geometry"), play_type=play_type, label=where)

    # Rule 6: the replay finishes FINAL on the score its population declares.
    assert previous_after is not None
    if previous_after["phase"] != "FINAL":
        raise SupplementImportError(f"{label} does not finish in the FINAL phase")
    for field in ("homeScore", "awayScore"):
        if _integer(previous_after[field], field) != _integer(game[field], field):
            raise SupplementImportError(f"{label} replay does not reconcile to its own final score")
    return {"plays": len(plays), "scoring_plays": scoring, "drives": previous_drive}


def _verify_population(doc: dict[str, Any]) -> list[dict[str, Any]]:
    population = doc.get("populationFinals") or []
    draws = _integer(doc.get("drawCount"), "drawCount")
    if draws <= 0 or len(population) != draws:
        raise SupplementImportError("population does not match the declared draw count")
    for position, member in enumerate(population):
        if _integer(member.get("simulationIndex"), "simulationIndex") != position:
            raise SupplementImportError("population is not ordered by simulation index")
        for field in ("homeScore", "awayScore", "seed"):
            if _integer(member.get(field), field) < 0:
                raise SupplementImportError(f"population {field} is negative")
    summary = doc.get("scoreSummary") or {}
    home = sum(m["homeScore"] > m["awayScore"] for m in population) / draws
    away = sum(m["awayScore"] > m["homeScore"] for m in population) / draws
    tie = sum(m["homeScore"] == m["awayScore"] for m in population) / draws
    for key, value in (("homeWinProbability", home), ("awayWinProbability", away), ("tieProbability", tie)):
        if not math.isclose(float(summary.get(key, float("nan"))), value, abs_tol=1e-6):
            raise SupplementImportError(f"{key} differs from its own declared population")
    return population


def _verify_population_spatial(doc: dict, schema: str) -> None:
    """Re-derive the producer's own reconciliation rather than trusting it.

    `populationSpatial` summarises 1,000 games, of which only six ship play
    paths -- so nothing downstream can check it against the plays themselves.
    The one check available is the one the producer states: its play count is
    defined exactly as `populationAccounting.plays`, and its cells must sum to
    that. Both are recomputed here.
    """
    spatial = doc.get("populationSpatial")
    if schema == "NFL_LABS_PLAY_LEVEL_SIMULATION_V1":
        if spatial is not None:
            raise SupplementImportError("a V1 play-level artifact must not carry populationSpatial")
        return
    if not isinstance(spatial, dict):
        raise SupplementImportError("play-level artifact is missing populationSpatial")
    if spatial.get("schemaVersion") != SPATIAL:
        raise SupplementImportError("unsupported populationSpatial schema")
    if spatial.get("basis") != "ALL_SIMULATED_PLAYS_IN_POPULATION":
        raise SupplementImportError("populationSpatial does not claim the whole population")

    accounting_plays = _integer((doc.get("populationAccounting") or {}).get("plays"),
                                "populationAccounting.plays")
    stated = _integer(spatial.get("playCount"), "populationSpatial.playCount")
    if accounting_plays < 1 or stated < 1:
        raise SupplementImportError("play counts must be positive")
    if stated != accounting_plays:
        raise SupplementImportError(
            f"populationSpatial.playCount {stated} does not equal populationAccounting.plays {accounting_plays}")

    cells = spatial.get("cells")
    if not isinstance(cells, list) or not cells:
        raise SupplementImportError("populationSpatial carries no cells")
    seen: set[tuple] = set()
    binned = 0
    for cell in cells:
        key = (cell.get("playType"), cell.get("start"), cell.get("yards"))
        if None in key[:1] or key in seen:
            raise SupplementImportError("populationSpatial has a duplicate or unlabelled cell")
        seen.add(key)
        if cell.get("playType") not in PLAY_TYPES:
            raise SupplementImportError(f"populationSpatial names an unknown play type {cell.get('playType')!r}")
        count = _integer(cell.get("count"), "populationSpatial.cells[].count")
        if count < 1:
            raise SupplementImportError("populationSpatial cell count must be positive")
        binned += count
    if binned != stated:
        raise SupplementImportError(f"populationSpatial cells sum to {binned}, not playCount {stated}")


def verify_play_level_current(exchange_root: Path) -> list[VerifiedPlayArtifact]:
    """Verify the pointer, the contract, the handoff and every byte of every artifact."""
    root = Path(exchange_root).resolve()
    directory = root / ROOT_NAME
    pointer = _load_canonical(directory / "current.json", label="play-level current.json")
    if pointer.get("schemaVersion") not in SUPPORTED_CURRENT:
        raise SupplementImportError("unsupported play-level pointer schema")
    _governance(pointer, label="play-level pointer")

    contract_path = root / "contracts" / f"{CONTRACT}.json"
    _verified_file(pointer.get("contract") or {}, contract_path)
    contract = _load_canonical(contract_path, label=CONTRACT)
    if contract.get("schemaVersion") != CONTRACT or contract.get("artifactVersion") != ARTIFACT:
        raise SupplementImportError("unsupported play-level contract")
    _governance(contract, label="play-level contract")

    # The handoff is the producer's own delivery receipt. It is verified so the
    # exact bytes the producer published can be named in the import record.
    handoff = pointer.get("handoff") or {}
    handoff_path = Path(str(handoff.get("path", "")))
    if not handoff_path.is_file():
        raise SupplementImportError("play-level handoff receipt is missing")
    _verified_file(handoff, handoff_path)

    verified: list[VerifiedPlayArtifact] = []
    seen: set[str] = set()
    for entry in pointer.get("artifacts") or []:
        reference = entry.get("artifact") or {}
        address = str(reference.get("sha256", ""))
        if len(address) != 64 or any(c not in "0123456789abcdef" for c in address):
            raise SupplementImportError("invalid play-level content address")
        if address in seen:
            raise SupplementImportError("repeated play-level content address")
        seen.add(address)
        artifact_dir = directory / "sha256" / address
        if directory not in artifact_dir.resolve().parents:
            raise SupplementImportError("play-level artifact directory escapes its family")
        if {p.name for p in artifact_dir.iterdir()} != {"manifest.json", "artifact.json"}:
            raise SupplementImportError("play-level artifact inventory is invalid")

        _verified_file(entry.get("manifest") or {}, artifact_dir / "manifest.json")
        manifest = _load_canonical(artifact_dir / "manifest.json", label="play-level manifest")
        if manifest.get("schemaVersion") != MANIFEST or manifest.get("files") != [reference]:
            raise SupplementImportError("play-level manifest differs from the current pointer")
        _governance(manifest, label="play-level manifest")

        _verified_file(reference, artifact_dir / "artifact.json")
        doc = _load_canonical(artifact_dir / "artifact.json", label="play-level artifact")
        _governance(doc, label="play-level artifact")

        schema = doc.get("schemaVersion")
        if schema not in SUPPORTED_ARTIFACTS:
            raise SupplementImportError("unsupported play-level artifact schema")
        # The version must agree in all three places. A pointer that advertises
        # V2 for a document that is V1 would let a consumer expect a field the
        # artifact does not carry.
        if schema != entry.get("artifactSchemaVersion") or schema != manifest.get("artifactSchemaVersion"):
            raise SupplementImportError("play-level schema binding mismatch")
        _verify_population_spatial(doc, schema)
        if doc.get("sport") != "NFL":
            raise SupplementImportError("play-level artifact is not NFL")
        if doc.get("eventId") != entry.get("eventId") or doc.get("forecastId") != entry.get("forecastId"):
            raise SupplementImportError("play-level event or forecast binding mismatch")
        engine = (doc.get("model") or {}).get("engineId")
        if engine != entry.get("modelId") or engine != PLAY_ENGINE:
            raise SupplementImportError("play-level artifact does not name the play engine")
        if doc.get("explainsM1Forecast") is not False or not doc.get("relatedM1ForecastId"):
            raise SupplementImportError(
                "play-level artifact must name its related M1 forecast and disclaim explaining it"
            )
        scenario = doc.get("scenario") or {}
        if not str(scenario.get("scenarioId") or "").strip():
            raise SupplementImportError("play-level artifact has no scenario identity")
        for key in ("sourceCutoffUtc", "forecastCreatedUtc", "scheduledStartUtc", "publishedAtUtc"):
            if key == "sourceCutoffUtc" and doc.get(key) is None:
                continue
            _iso(doc.get(key), label=key)

        _verify_distribution(doc)
        population = _verify_population(doc)
        games = doc.get("simulatedGames") or []
        if len(games) < 6:
            raise SupplementImportError("play-level artifact must publish at least six representatives")
        totals = {"plays": 0, "scoring_plays": 0, "drives": 0}
        for position, game in enumerate(games):
            counted = _verify_game(
                game, population=population,
                label=f"{doc['eventId']}/{scenario['scenarioId']}/game {position}",
            )
            for key, value in counted.items():
                totals[key] += value

        verified.append(VerifiedPlayArtifact(
            address=address, artifact=doc,
            files={"artifact": reference, "manifest": entry["manifest"],
                   "contract": pointer["contract"], "handoff": handoff},
            receipt={"representative_games": len(games), **totals,
                     "population": len(population), "verified_sequence_integrity": True},
        ))

    if not verified:
        raise SupplementImportError("play-level pointer has no artifacts")
    if len(verified) != pointer.get("artifactCount"):
        raise SupplementImportError("play-level artifact count differs from the pointer")
    if len({item.event_id for item in verified}) != pointer.get("eventCount"):
        raise SupplementImportError("play-level event count differs from the pointer")
    return verified


def _representative_index(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """A light row per representative game: enough to choose one, not to draw it."""
    index = []
    for position, game in enumerate(doc.get("simulatedGames") or []):
        diagnostics = game.get("diagnostics") or {}
        index.append({
            "position": position,
            "label": game["label"],
            "simulationIndex": game["simulationIndex"],
            "seed": game["seed"],
            "homeScore": game["homeScore"],
            "awayScore": game["awayScore"],
            "regulationHomeScore": game.get("regulationHomeScore"),
            "regulationAwayScore": game.get("regulationAwayScore"),
            "reachedOvertime": game.get("reachedOvertime"),
            "finalTie": game.get("finalTie"),
            "playCount": len(game.get("plays") or []),
            "driveCount": diagnostics.get("drives"),
            "turnovers": diagnostics.get("turnovers"),
            "sacks": diagnostics.get("sacks"),
            "playTypeCounts": diagnostics.get("playTypeCounts"),
        })
    return index


def _payload(doc: dict[str, Any]) -> dict[str, Any]:
    """Everything the pages need EXCEPT the plays, which stay in the file."""
    keep = (
        "eventId", "forecastId", "sport", "scheduledStartUtc", "publishedAtUtc",
        "forecastCreatedUtc", "sourceCutoffUtc", "homeTeam", "awayTeam", "model",
        "generatingLaw", "scenario", "scenarioDelta", "scoreSummary", "quantiles",
        "jointScoreDistribution", "scoreMarginDistribution", "totalScoreDistribution",
        "populationFinals", "reachingOvertimeProbability", "finalTieProbability",
        "limitations", "m1Comparison", "relatedM1ForecastId", "explainsM1Forecast",
        "populationAccounting",
        # populationSpatial is deliberately NOT stored here. It is ~57 KB per
        # artifact and `_rows()` loads whole ResearchSupplement rows to build
        # the cheap scenario index, so keeping it would put 5.7 MB of JSONB in
        # the path of a page that never reads it. The population endpoint reads
        # it from the verified artifact file, where it already lives.
        "historicalEvidencePrecision", "historicalEvidenceThroughDate",
        "inputTreatment", "sourceRights", "drawCount", "seed", "schemaVersion",
        "status", "promotionStatus", "executionAllowedInAISP", "wageringAuthority",
    )
    artifact = {key: doc[key] for key in keep if key in doc}
    return {
        "summaries": dict(doc["scoreSummary"]),
        "artifact": artifact,
        "representatives": _representative_index(doc),
        # The plays are deliberately absent. This says so, out loud, to any
        # reader that goes looking for them.
        "playsStoredInline": False,
    }


def register_play_level_current(
    session: Session, verified: list[VerifiedPlayArtifact], *, dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Append-only ResearchSupplement inserts. Bindings are checked before any write."""
    if not dry_run and session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 0x4E464C504C4159})

    parents: list[ShadowForecast] = []
    for item in verified:
        doc = item.artifact
        row = session.scalar(select(ShadowForecast).where(
            ShadowForecast.forecast_id == doc["relatedM1ForecastId"],
            ShadowForecast.event_id == doc["eventId"],
            ShadowForecast.engine_id == M1,
        ))
        if row is None:
            raise SupplementImportError(
                f"related M1 forecast is not registered for {doc['eventId']} "
                f"({doc['relatedM1ForecastId']})"
            )
        if (row.home_team_id, row.away_team_id) != (doc["homeTeam"]["teamId"], doc["awayTeam"]["teamId"]):
            raise SupplementImportError("play-level home/away identity differs from the registered forecast")
        kickoff = row.scheduled_start_utc
        if kickoff is not None and kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=UTC)
        if _iso(doc["scheduledStartUtc"], label="scheduledStartUtc") != kickoff:
            raise SupplementImportError("play-level kickoff differs from the registered forecast")
        parents.append(row)

    records = []
    for item, parent in zip(verified, parents, strict=True):
        doc = item.artifact
        existing = session.scalar(select(ResearchSupplement).where(
            ResearchSupplement.supplement_sha256 == item.address))
        records.append({
            "supplement_sha256": item.address, "kind": KIND_PLAY, "event_id": item.event_id,
            "engine_id": PLAY_ENGINE, "forecast_id": doc["forecastId"],
            "scenario_id": item.scenario_id, "idempotent_reuse": existing is not None,
            "plays": item.receipt["plays"],
        })
        if existing is not None or dry_run:
            continue
        session.add(ResearchSupplement(
            supplement_sha256=item.address,
            supplement_id=f"nfl-play-level:sha256:{item.address}",
            contract_version=CONTRACT, kind=KIND_PLAY, sport="NFL",
            event_id=doc["eventId"], engine_id=PLAY_ENGINE, forecast_id=doc["forecastId"],
            # The parent is the M1 forecast this event relates to. The play
            # engine does not explain it, so the row records the relationship
            # without inheriting M1's identity.
            shadow_forecast_id=None,
            artifact_version=ARTIFACT, simulation_kind="MONTE_CARLO",
            simulation_count=doc["drawCount"], simulation_seed=doc["seed"],
            source_cutoff_utc=_iso(doc["sourceCutoffUtc"], label="sourceCutoffUtc") if doc.get("sourceCutoffUtc") else None,
            published_utc=_iso(doc["publishedAtUtc"], label="publishedAtUtc"),
            status="RESEARCH_SHADOW", research_shadow_only=True,
            payload=_payload(doc),
            evidence={
                "files": item.files, "manifest_schema": MANIFEST,
                "related_m1_forecast_id": parent.forecast_id,
                "independent_play_engine": True,
                "scenario_id": item.scenario_id,
                "verification_receipt": item.receipt,
                "plays_read_from": "content_addressed_artifact_file",
                "verified_at_utc": datetime.now(UTC).isoformat(),
            },
        ))
        session.flush()
    return records


class PlayEvidenceUnavailable(RuntimeError):
    """The immutable artifact file backing a play-level row cannot be trusted.

    Raised with an exact cause rather than degrading to an empty animation: a
    theater that silently shows nothing is indistinguishable from a game with
    no plays, and that is the failure mode this project refuses.
    """


def read_artifact(supplement: ResearchSupplement) -> dict[str, Any]:
    """Read and integrity-check the whole artifact behind a supplement row.

    The address on the row is the checksum of the file, so re-hashing the bytes
    is a complete integrity check: the file cannot have drifted, been swapped or
    been truncated without this failing.
    """
    reference = ((supplement.evidence or {}).get("files") or {}).get("artifact") or {}
    path = Path(str(reference.get("path", "")))
    if not path.is_file():
        raise PlayEvidenceUnavailable(
            f"PLAY_ARTIFACT_FILE_MISSING: {path} is not present on this host"
        )
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != supplement.supplement_sha256:
        raise PlayEvidenceUnavailable(
            f"PLAY_ARTIFACT_CHECKSUM_MISMATCH: {path} no longer hashes to "
            f"{supplement.supplement_sha256}"
        )
    if len(raw) != reference.get("byteCount"):
        raise PlayEvidenceUnavailable(f"PLAY_ARTIFACT_BYTE_COUNT_MISMATCH: {path}")
    return json.loads(raw.decode("utf-8"))


def read_representative_game(
    supplement: ResearchSupplement, *, label: str | None = None, position: int | None = None,
) -> dict[str, Any]:
    """Read ONE representative game back from its verified content-addressed file.

    The address on the row is the checksum of the file, so re-hashing the bytes
    is a complete integrity check: the file cannot have drifted, been swapped or
    been truncated without this failing.
    """
    games = read_artifact(supplement).get("simulatedGames") or []
    for index, game in enumerate(games):
        if (label is not None and game.get("label") == label) or (position is not None and index == position):
            return game
    raise PlayEvidenceUnavailable(
        f"PLAY_REPRESENTATIVE_NOT_FOUND: label={label!r} position={position!r}"
    )
