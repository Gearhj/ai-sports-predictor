"""The published September 12 explanation catalogue, verified and joined.

WHAT THIS CATALOGUE IS
----------------------
References, not forecasts. It creates no forecast, no result revision and no
model authority; it points at diagnostics that already existed and says which
game and which exact forecast each one belongs to. So the import verifies bytes,
then joins by identity, and never writes anything to the evidence it describes.

THE JOIN RULE THAT MATTERS
--------------------------
Exact ``forecastId`` plus canonical/MLBAM game identity. No nearest match and no
latest-forecast fallback. A game can carry several forecast revisions, and
attaching September 12's observed events to a later revision would silently
credit one forecast with another's result -- the single most damaging thing this
module could do, and the easiest to do by accident.

THREE KINDS OF STATEMENT, KEPT APART
------------------------------------
*   **Observed contributor** -- a fact about the game. "New York hit six home
    runs." Always safe to state.
*   **Possible explanation** -- a fact located against the simulated population.
    "Six home runs sits in the top 0.24% of the 5,000 retained draws, which
    averaged 1.18." Informative, and still not a cause.
*   **Established model defect** -- not supported by anything here. The
    catalogue says so directly, and so does this module.

Only NYM-NYY 823496, PHI-ATL 824870 and PIT-CHC 824630 carry population
counters, and those three are retrospective reproductions captured after the
results were known and selected because they were interesting. They are not
independent validation, and a probability sum conditions on simulated exposures
rather than the plate appearances that actually happened -- so nothing here can
establish calibration on actual PAs.

The protected-inspection closeout travels in the same package and is
deliberately not read. Its log and protected-source references are not product
evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "MLB_EXPLANATION_EVIDENCE_JOINS_V1"
INDEX_SCHEMA = "MLB_RESEARCH_CLOSEOUT_EXPLANATION_HANDOFF_V1"

#: The published package. Overridable for tests; there is no provider call and
#: no network access on this path at any point.
DEFAULT_INDEX = Path(
    r"C:\Users\james\asp-model-exchange\mlb-v18-ensemble-research-v1"
    r"\closeout-explanations-v1\packages\sha256"
    r"\dc279581869935ce817fda246c3920001faf20114046b160c717944a74a8fb4b\index.json"
)
DEFAULT_INDEX_SHA256 = (
    "dc279581869935ce817fda246c3920001faf20114046b160c717944a74a8fb4b")

#: Never read as explanation data, whatever else the package carries.
EXCLUDED_FROM_INGESTION = {"PROTECTED_INSPECTION_CLOSEOUT.md"}

POPULATION_AVAILABILITY = "SCORE_TOTAL_PA_OBSERVED_AND_RETROSPECTIVE_POPULATION"


class CatalogueError(ValueError):
    """The catalogue cannot be verified or joined, and the message says why."""


@dataclass(frozen=True)
class GameEvidence:
    """Everything the catalogue supports for one game, already resolved."""

    source_game_id: str
    canonical_game_id: str
    forecast_id: str
    matchup: str
    availability: str
    actual: dict[str, Any]
    observed_events: dict[str, Any]
    observed_exposure: dict[str, Any]
    governed_final: dict[str, Any] | None
    population: dict[str, Any] | None
    caveats: list[str] = field(default_factory=list)

    @property
    def has_population_counters(self) -> bool:
        return self.population is not None


def _sha256(path: Path) -> tuple[str, int]:
    raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _pointer(document: Any, pointer: str) -> Any:
    """RFC 6901, resolved after verification and never before."""

    node = document
    for token in pointer.lstrip("/").split("/"):
        if token == "":
            continue
        token = token.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def verify_package(
    index_path: Path | str = DEFAULT_INDEX,
    *,
    expected_sha256: str | None = DEFAULT_INDEX_SHA256,
) -> dict[str, Any]:
    """Delivered bytes first: the index, its files, then every referenced artifact."""

    path = Path(index_path)
    if not path.is_file():
        raise CatalogueError(f"no catalogue index at {path}")
    digest, size = _sha256(path)
    if expected_sha256 and digest != expected_sha256:
        raise CatalogueError(
            f"catalogue index hashes to {digest}, not the expected {expected_sha256}")

    index = json.loads(path.read_bytes())
    if index.get("schemaVersion") != INDEX_SCHEMA:
        raise CatalogueError(
            f"index schema {index.get('schemaVersion')!r} is not {INDEX_SCHEMA}")

    root = path.parent
    verified: list[str] = []
    for entry in index.get("files") or []:
        member = root / entry["relativePath"]
        if not member.is_file():
            raise CatalogueError(f"index names a missing file: {entry['relativePath']}")
        member_digest, member_size = _sha256(member)
        if member_digest != entry["sha256"] or member_size != entry["bytes"]:
            raise CatalogueError(
                f"{entry['relativePath']} does not match its declared bytes")
        verified.append(entry["relativePath"])

    joins = json.loads((root / "explanation-joins.json").read_bytes())
    if joins.get("schemaVersion") != SCHEMA_VERSION:
        raise CatalogueError(
            f"joins schema {joins.get('schemaVersion')!r} is not {SCHEMA_VERSION}")

    artifacts: dict[str, Any] = {}
    for name, meta in (joins.get("artifacts") or {}).items():
        artifact_path = Path(meta["path"])
        if not artifact_path.is_file():
            raise CatalogueError(f"referenced artifact is absent: {name} -> {artifact_path}")
        artifact_digest, artifact_size = _sha256(artifact_path)
        if artifact_digest != meta["sha256"] or artifact_size != meta["bytes"]:
            raise CatalogueError(
                f"referenced artifact {name} does not match its declared bytes")
        artifacts[name] = json.loads(artifact_path.read_bytes())

    return {
        "index": index,
        "joins": joins,
        "artifacts": artifacts,
        "index_sha256": digest,
        "index_bytes": size,
        "files_verified": verified,
        "excluded_from_ingestion": sorted(EXCLUDED_FROM_INGESTION),
        "root": root,
    }


def build_evidence(package: dict[str, Any]) -> dict[tuple[str, str], GameEvidence]:
    """Resolve every game's pointers, keyed by (source game id, forecast id).

    Keying on both is the join rule made structural: a lookup that does not know
    the exact forecast cannot reach this evidence at all.
    """

    joins = package["joins"]
    artifacts = package["artifacts"]
    table = artifacts["diagnosticTable"]
    suffixes = joins["fieldLocations"]["diagnosticTableSuffixes"]
    caveats = list(joins.get("caveats") or [])

    evidence: dict[tuple[str, str], GameEvidence] = {}
    for game in joins.get("games") or []:
        base = game["diagnosticTableRowPointer"]

        def at(key: str) -> Any:
            try:
                return _pointer(table, base + suffixes[key])
            except (KeyError, IndexError, TypeError):
                return None

        population = None
        reference = game.get("populationComparison")
        if reference and game.get("availability") == POPULATION_AVAILABILITY:
            try:
                population = _pointer(
                    artifacts["populationComparison"], reference["pointer"])
            except (KeyError, IndexError, TypeError):
                population = None
            if population is not None:
                declared = str(reference.get("originalForecastId")
                               or population.get("originalForecastId") or "")
                if declared and declared != game["forecastId"]:
                    # The join rule, enforced rather than trusted.
                    raise CatalogueError(
                        f"population comparison for {game['sourceGameId']} names "
                        f"forecast {declared}, not {game['forecastId']}"
                    )
                population = {
                    "classification": reference.get("classification"),
                    "simulation_count": reference.get("simulationCount"),
                    "row": population,
                }

        evidence[(str(game["sourceGameId"]), str(game["forecastId"]))] = GameEvidence(
            source_game_id=str(game["sourceGameId"]),
            canonical_game_id=str(game["canonicalGameId"]),
            forecast_id=str(game["forecastId"]),
            matchup=str(game.get("matchup") or ""),
            availability=str(game.get("availability") or ""),
            actual=at("actualScores") or {},
            observed_events=at("observedEventCountsAndRates") or {},
            observed_exposure=at("observedStarterReliefAndInnings") or {},
            governed_final=game.get("governedFinal"),
            population=population,
            caveats=caveats,
        )
    return evidence


@lru_cache(maxsize=4)
def load_catalogue(
    index_path: str | None = None,
) -> dict[str, Any] | None:
    """Verify and resolve once per process; None when the package is absent.

    Cached because the diagnostic table is 1.5 MB and a slate opens fifteen
    panels. Absence is not an error: AISP runs without the catalogue and the
    outcome panel simply reports that deeper event analysis is unavailable.
    """

    path = Path(index_path) if index_path else DEFAULT_INDEX
    if not path.is_file():
        return None
    package = verify_package(path)
    package["evidence"] = build_evidence(package)
    return package


def evidence_for(
    *, source_game_id: str | None, forecast_id: str | None,
    index_path: str | None = None,
) -> GameEvidence | None:
    """The catalogue's evidence for this EXACT game and forecast, or nothing."""

    if not source_game_id or not forecast_id:
        return None
    package = load_catalogue(index_path)
    if package is None:
        return None
    return package["evidence"].get((str(source_game_id), str(forecast_id)))


__all__ = [
    "DEFAULT_INDEX",
    "DEFAULT_INDEX_SHA256",
    "EXCLUDED_FROM_INGESTION",
    "CatalogueError",
    "GameEvidence",
    "build_evidence",
    "evidence_for",
    "load_catalogue",
    "verify_package",
]
