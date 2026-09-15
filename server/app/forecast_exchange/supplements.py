"""Verify and register research supplements (AISP_RESEARCH_SUPPLEMENT_V1).

WHAT THIS IS
------------
Forecast bundles enforce an exact three-file inventory, so simulation and
benchmark evidence cannot be dropped into them. A SUPPLEMENT is the separate,
content-addressed container for that evidence:

    <exchange>/nfl-labs-supplements/nfl-labs-supplement-index.json
    <exchange>/nfl-labs-supplements/sha256/<64-hex>/manifest.json
    <exchange>/nfl-labs-supplements/sha256/<64-hex>/supplement.json

Four kinds are supported, each with its own required summaries:

    JOINT_SCORE_SIMULATION   a joint final-score distribution for one event
    DRIVE_SIMULATION         persisted possessions for one simulated game
    SCENARIO_COMPARISON      precomputed alternate-assumption results
    MODEL_BENCHMARK_STUDY    machine-readable model comparison results
    PAPER_RESEARCH_ELIGIBILITY  producer attestation that one forecast may be
                             used by one authorised VIRTUAL paper experiment

WHAT IT MAY NOT DO
------------------
It executes no model, calls no provider, grants no wagering authority and
promotes nothing. Every supplement must declare research-shadow-only status
and must bind itself to its event/engine/forecast by explicit identifiers;
the importer never infers a binding.

VERIFICATION ORDER (fail closed at the first violation)
-------------------------------------------------------
1. The directory name is a 64-hex address.
2. The directory holds exactly ``manifest.json`` and ``supplement.json``.
3. ``manifest.json`` is canonical JSON, declares that address, and its own
   canonical identity (the manifest minus ``supplementSha256``) recomputes to it.
4. Every artifact the manifest lists exists and its bytes hash and length match
   exactly, BEFORE any file is interpreted as data.
5. ``supplement.json`` is canonical JSON and agrees with the manifest on id,
   kind, sport and contract.
6. Governance flags are fail-closed in both documents.
7. The kind-specific summaries are internally consistent (a distribution sums
   to one and reproduces its own stated outcome probabilities; a drive replay
   reconciles to its own final score; a scenario names the inputs it changed).
8. At registration an event-bound supplement must match a registered shadow
   forecast on event, engine AND forecast id.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.gen2.exchange.canonical_json import canonical_json_bytes, canonical_sha256
from app.models import ResearchSupplement, ShadowForecast

CONTRACT_VERSION = "AISP_RESEARCH_SUPPLEMENT_V1"
MANIFEST_SCHEMA = "AISP_RESEARCH_SUPPLEMENT_MANIFEST_V1"
INDEX_SCHEMA = "AISP_NFL_LABS_SUPPLEMENT_INDEX_V1"
INDEX_FILENAME = "nfl-labs-supplement-index.json"
SUPPLEMENT_ROOT = "nfl-labs-supplements"

KIND_JOINT = "JOINT_SCORE_SIMULATION"
KIND_DRIVE = "DRIVE_SIMULATION"
KIND_SCENARIO = "SCENARIO_COMPARISON"
KIND_STUDY = "MODEL_BENCHMARK_STUDY"
#: Producer attestation that one forecast may be used by one authorised paper
#: experiment. Admitted explicitly, as its own kind: reusing JOINT_SCORE_
#: SIMULATION would make a distribution and a permission indistinguishable in
#: the same column, and an older importer that does not know this name must
#: keep rejecting it rather than importing a permission it cannot check.
KIND_PAPER_ELIGIBILITY = "PAPER_RESEARCH_ELIGIBILITY"
KINDS = frozenset({KIND_JOINT, KIND_DRIVE, KIND_SCENARIO, KIND_STUDY,
                   KIND_PAPER_ELIGIBILITY})
EVENT_BOUND_KINDS = frozenset({KIND_JOINT, KIND_DRIVE, KIND_SCENARIO,
                               KIND_PAPER_ELIGIBILITY})

#: The artifactVersion a paper-eligibility supplement must declare. A different
#: version is a different contract and is refused rather than read loosely.
PAPER_ELIGIBILITY_VERSION = "ASP_NFL_PAPER_RESEARCH_ELIGIBILITY_V1"

#: One exact eligibility contract per sport. The version is dispatched on the
#: supplement's OWN declared sport, so an NBA document carrying the CFB version
#: -- or the NFL version -- is refused rather than checked against the wrong
#: rules.
PAPER_ELIGIBILITY_VERSIONS = {
    "NFL": PAPER_ELIGIBILITY_VERSION,
    "NBA": "ASP_NBA_PAPER_RESEARCH_ELIGIBILITY_V1",
    "CFB": "ASP_CFB_PAPER_RESEARCH_ELIGIBILITY_V1",
}

#: Every tree a research supplement may live in. The NFL tree is unchanged; the
#: NBA and CFB eligibility trees are where Model Studio publishes those sports.
SUPPLEMENT_ROOTS = (
    SUPPLEMENT_ROOT,
    "nba-gen1-research-v1/paper-eligibility",
    "cfb-gen1-research-v1/paper-eligibility",
)

SIMULATION_KINDS = frozenset({"EXACT_DISTRIBUTION", "MONTE_CARLO", "DETERMINISTIC", "NONE"})
DRIVE_RESULTS = frozenset({
    "TOUCHDOWN", "FIELD_GOAL", "MISSED_FIELD_GOAL", "PUNT", "TURNOVER", "TURNOVER_ON_DOWNS",
    "SAFETY", "END_OF_HALF", "END_OF_GAME", "KNEEL",
})
BENCHMARK_TIMING_CLASSES = frozenset({"HISTORICAL_UNTIMED", "TIMESTAMP_MATCHED", "NONE"})

_ADDRESS = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_FILES = frozenset({"manifest.json", "supplement.json"})
_PROBABILITY_TOLERANCE = 5e-4


class SupplementImportError(RuntimeError):
    """A supplement is missing, corrupted, non-canonical, unbound or unsafe."""


@dataclass
class VerifiedSupplement:
    address: str
    manifest: dict[str, Any]
    supplement: dict[str, Any]
    files: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return str(self.supplement["kind"])

    @property
    def binding(self) -> dict[str, Any]:
        return dict(self.supplement.get("binding") or {})


# ---------------------------------------------------------------------------
# loading helpers
# ---------------------------------------------------------------------------

def _load_canonical(path: Path, *, label: str) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SupplementImportError(f"{label} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise SupplementImportError(f"{label} must be a JSON object")
    if raw != canonical_json_bytes(value):
        raise SupplementImportError(f"{label} is not encoded with canonical exchange JSON")
    return value


def _require(mapping: dict[str, Any], key: str, *, label: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise SupplementImportError(f"{label} is missing required field '{key}'")
    return mapping[key]


def _iso(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise SupplementImportError(f"{label} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise SupplementImportError(f"{label} must carry a timezone")
    return parsed.astimezone(UTC)


def _governance_ok(doc: dict[str, Any], *, label: str) -> None:
    gov = doc.get("governance")
    if not isinstance(gov, dict):
        raise SupplementImportError(f"{label} declares no governance block")
    if str(gov.get("status", "")).upper() != "RESEARCH_SHADOW":
        raise SupplementImportError(f"{label} is not RESEARCH_SHADOW")
    if gov.get("researchShadowOnly") is not True:
        raise SupplementImportError(f"{label} is not research-shadow-only")
    for flag in ("executionAllowedInAisp", "modelPromotionAllowed", "wageringAuthority"):
        if gov.get(flag) is not False:
            raise SupplementImportError(f"{label} must declare {flag}=false")


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verify_supplement(directory: Path) -> VerifiedSupplement:
    directory = Path(directory)
    address = directory.name.lower()
    if not _ADDRESS.fullmatch(address):
        raise SupplementImportError("Supplement directory is not a 64-hex content address")
    if not directory.is_dir():
        raise SupplementImportError("Supplement directory does not exist")
    names = {child.name for child in directory.iterdir()}
    if names != _REQUIRED_FILES:
        raise SupplementImportError(
            f"Supplement must contain exactly {sorted(_REQUIRED_FILES)}; found {sorted(names)}"
        )

    manifest = _load_canonical(directory / "manifest.json", label="manifest.json")
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA:
        raise SupplementImportError("manifest.json schemaVersion is not supported")
    declared = str(_require(manifest, "supplementSha256", label="manifest.json")).lower()
    if declared != address:
        raise SupplementImportError("manifest.json declares a different supplement address")
    identity = {k: v for k, v in manifest.items() if k != "supplementSha256"}
    if canonical_sha256(identity) != address:
        raise SupplementImportError("manifest.json canonical identity does not recompute to its address")
    if manifest.get("contractVersion") != CONTRACT_VERSION:
        raise SupplementImportError("manifest.json contractVersion is not supported")
    _governance_ok(manifest, label="manifest.json")

    # Hash every listed artifact BEFORE interpreting any of it.
    artifacts = _require(manifest, "artifacts", label="manifest.json")
    if not isinstance(artifacts, list) or not artifacts:
        raise SupplementImportError("manifest.json lists no artifacts")
    files: dict[str, dict[str, Any]] = {}
    for item in artifacts:
        name = str(_require(item, "file", label="manifest artifact"))
        if name not in _REQUIRED_FILES or name == "manifest.json":
            raise SupplementImportError(f"manifest.json lists an unsupported artifact '{name}'")
        raw = (directory / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != str(item.get("sha256", "")).lower():
            raise SupplementImportError(f"{name} bytes do not hash to the manifest digest")
        if int(item.get("bytes", -1)) != len(raw):
            raise SupplementImportError(f"{name} length does not match the manifest")
        files[name] = {"sha256": digest, "bytes": len(raw)}
    if "supplement.json" not in files:
        raise SupplementImportError("manifest.json does not list supplement.json")

    supplement = _load_canonical(directory / "supplement.json", label="supplement.json")
    if supplement.get("schemaVersion") != CONTRACT_VERSION:
        raise SupplementImportError("supplement.json schemaVersion is not supported")
    for key in ("supplementId", "kind", "sport"):
        if supplement.get(key) != manifest.get(key):
            raise SupplementImportError(f"supplement.json and manifest.json disagree on '{key}'")
    _governance_ok(supplement, label="supplement.json")

    kind = str(supplement["kind"])
    if kind not in KINDS:
        raise SupplementImportError(f"Unsupported supplement kind '{kind}'")
    _require(supplement, "artifactVersion", label="supplement.json")
    _iso(_require(supplement, "sourceCutoffUtc", label="supplement.json"), label="sourceCutoffUtc")
    _iso(_require(supplement, "publishedUtc", label="supplement.json"), label="publishedUtc")
    simulation = _require(supplement, "simulation", label="supplement.json")
    if str(simulation.get("kind")) not in SIMULATION_KINDS:
        raise SupplementImportError("simulation.kind is not supported")
    if simulation["kind"] == "MONTE_CARLO":
        if int(simulation.get("count") or 0) <= 0:
            raise SupplementImportError("A Monte Carlo supplement must state a positive draw count")
        if "seed" not in simulation:
            raise SupplementImportError("A Monte Carlo supplement must state its seed")
    if kind in EVENT_BOUND_KINDS:
        binding = _require(supplement, "binding", label="supplement.json")
        for key in ("eventId", "engineId", "forecastId"):
            _require(binding, key, label="supplement.json binding")

    summaries = _require(supplement, "summaries", label="supplement.json")
    _verify_kind(kind, supplement, summaries)
    return VerifiedSupplement(address=address, manifest=manifest, supplement=supplement, files=files)


def _verify_kind(kind: str, supplement: dict[str, Any], summaries: dict[str, Any]) -> None:
    if kind == KIND_JOINT:
        _verify_joint(summaries)
    elif kind == KIND_DRIVE:
        _verify_drives(summaries)
    elif kind == KIND_SCENARIO:
        _verify_scenarios(summaries)
    elif kind == KIND_STUDY:
        _verify_study(summaries)
    elif kind == KIND_PAPER_ELIGIBILITY:
        _verify_paper_eligibility(supplement, summaries)


def _verify_paper_eligibility(
    supplement: dict[str, Any], summaries: dict[str, Any],
) -> None:
    """A permission must name what it permits, and permit nothing more.

    Two separate things are checked. That the document actually binds an
    experiment, a policy and a model -- a permission that names none of them is
    a general authority wearing a specific label. And that it grants no
    execution: the governance block is already verified by `_governance_ok`,
    but a paper eligibility document is exactly the place someone would put a
    wagering flag, so it is checked again here against its own declarations.
    """

    sport = str(supplement.get("sport") or "").upper()
    expected_version = PAPER_ELIGIBILITY_VERSIONS.get(sport)
    if expected_version is None:
        raise SupplementImportError(
            f"No paper-eligibility contract is registered for sport '{sport}'")
    if supplement.get("artifactVersion") != expected_version:
        raise SupplementImportError(
            "A paper-eligibility supplement must declare artifactVersion "
            f"{expected_version}")

    authorization = _require(summaries, "authorization",
                             label="paper-eligibility summaries")
    for key in ("experimentId", "policyVersion", "mode",
                "consumerVirtualExperimentAuthorized"):
        _require(authorization, key, label="paper-eligibility authorization")

    if authorization.get("consumerVirtualExperimentAuthorized") is not True:
        raise SupplementImportError(
            "A paper-eligibility supplement must state that the consumer "
            "virtual experiment is authorised")
    if str(authorization.get("mode")) != "VIRTUAL_RESEARCH_ONLY":
        raise SupplementImportError(
            "A paper-eligibility supplement may only authorise "
            "VIRTUAL_RESEARCH_ONLY")
    if authorization.get("realMoneyAuthorized") is not False:
        raise SupplementImportError(
            "A paper-eligibility supplement may never authorise real money")

    # The frozen model it attests to, and the probabilities it freezes.
    frozen = _require(summaries, "frozenConfiguration",
                      label="paper-eligibility summaries")
    for key in ("forecastId", "homeWinProbability", "awayWinProbability",
                "tieProbability"):
        _require(frozen, key, label="paper-eligibility frozenConfiguration")
    if frozen.get("executionAllowed") is not False:
        raise SupplementImportError(
            "A paper-eligibility supplement may not report executionAllowed")
    if frozen.get("modelPromotionAllowed") is not False:
        raise SupplementImportError(
            "A paper-eligibility supplement may not promote a model")

    # Three masses, and they must be a probability distribution. A permission
    # that carries renormalised or two-way probabilities would quietly change
    # the settlement arithmetic downstream of it.
    total = (float(frozen["homeWinProbability"])
             + float(frozen["awayWinProbability"])
             + float(frozen["tieProbability"]))
    if abs(total - 1.0) > _PROBABILITY_TOLERANCE:
        raise SupplementImportError(
            f"The frozen three-way probabilities sum to {total}, not 1")

    semantics = _require(summaries, "probabilitySemantics",
                         label="paper-eligibility summaries")
    if semantics.get("settlementMustBeExplicit") is not True:
        raise SupplementImportError(
            "A paper-eligibility supplement must require explicit settlement")

    if sport in ("NBA", "CFB"):
        _verify_research_paper_terms(supplement, summaries, authorization, frozen, sport)


def _verify_research_paper_terms(
    supplement: dict[str, Any], summaries: dict[str, Any],
    authorization: dict[str, Any], frozen: dict[str, Any], sport: str,
) -> None:
    """NBA and CFB: the document must bind AISP's own registered policy, exactly.

    Checked against the consumer's policy constants rather than against the
    document itself, so a supplement cannot authorise a larger cap, a second
    market or a different controlling model by stating one. NFL is not routed
    here; its path above is unchanged.
    """

    from app.services.nfl_paper_eligibility import experiment_key
    from app.services.research_paper_policy import POLICIES

    policy = POLICIES[sport]
    expected_key = experiment_key(policy.policy_version, policy.sport, policy.season_id)
    if authorization.get("experimentId") != expected_key:
        raise SupplementImportError(
            f"Eligibility binds experiment {authorization.get('experimentId')}, not {expected_key}")
    if authorization.get("policyVersion") != policy.policy_version:
        raise SupplementImportError(
            f"Eligibility names policy {authorization.get('policyVersion')}, not {policy.policy_version}")
    for flag in ("realMoneyExecutionAllowed", "wageringOutsideAispAllowed"):
        if authorization.get(flag) is not False:
            raise SupplementImportError(f"Eligibility must declare {flag}=false")
    if list(authorization.get("marketScope") or []) != list(policy.markets):
        raise SupplementImportError(
            f"Eligibility market scope {authorization.get('marketScope')} is not {list(policy.markets)}")
    terms = {
        "kellyFraction": policy.kelly_fraction,
        "positionCapFraction": policy.max_position_percentage,
        "initialPositionCapUsd": policy.max_stake,
        "sameGameCapFraction": policy.max_same_game_percentage,
        "dailyCapFraction": policy.max_daily_percentage,
        "minimumAdjustedEdgeFraction": policy.min_adjusted_edge,
    }
    for key, value in terms.items():
        if authorization.get(key) is None or abs(float(authorization[key]) - float(value)) > 1e-12:
            raise SupplementImportError(
                f"Eligibility {key}={authorization.get(key)} does not match the policy's {value}")

    if abs(float(frozen["tieProbability"])) > _PROBABILITY_TOLERANCE:
        raise SupplementImportError(f"{sport} finals have no tie; tieProbability must be 0")
    if frozen.get("engineId") != policy.controlling_engine:
        raise SupplementImportError(
            f"Eligibility controls on {frozen.get('engineId')}, not {policy.controlling_engine}")
    if frozen.get("configurationIdentity") != policy.controlling_configuration:
        raise SupplementImportError(
            f"Eligibility configuration {frozen.get('configurationIdentity')} is not "
            f"{policy.controlling_configuration}")

    binding = _require(supplement, "binding", label="supplement.json")
    for key in ("forecastId", "engineId", "configurationIdentity", "modelArtifactSha256"):
        if binding.get(key) != frozen.get(key):
            raise SupplementImportError(f"binding.{key} and frozenConfiguration.{key} disagree")

    channels = _require(summaries, "probabilityChannels", label="paper-eligibility summaries")
    joint = channels.get("controllingJoint") or {}
    for mine, theirs in (("homeWinProbability", "homeWin"), ("awayWinProbability", "awayWin"),
                         ("tieProbability", "tie")):
        if joint.get(theirs) is None or abs(float(joint[theirs]) - float(frozen[mine])) > _PROBABILITY_TOLERANCE:
            raise SupplementImportError(
                f"controllingJoint.{theirs} does not equal frozenConfiguration.{mine}")

    # Chronology: evidence, then forecast, then its publication, then this
    # permission. A permission dated before the forecast it permits is refused.
    clocks = _require(summaries, "originalClocks", label="paper-eligibility summaries")
    names = ("sourceCutoffUtc", "forecastCreatedAtUtc", "originalExchangePublishedAtUtc",
             "eligibilityPublishedAtUtc")
    order = [(name, _iso(clocks.get(name), label=name)) for name in names]
    for (earlier, first), (later, second) in zip(order, order[1:]):
        if first > second:
            raise SupplementImportError(f"Chronology violated: {earlier} is after {later}")
    if _iso(supplement["sourceCutoffUtc"], label="sourceCutoffUtc") != order[0][1]:
        raise SupplementImportError("supplement sourceCutoffUtc disagrees with originalClocks")


def joint_outcomes(cells: list[dict[str, Any]]) -> dict[str, float]:
    """Home/away/tie mass of a joint distribution, plus its expected scores."""

    home = away = tie = 0.0
    exp_home = exp_away = 0.0
    total = 0.0
    for cell in cells:
        p = float(cell["probability"])
        h, a = int(cell["homeScore"]), int(cell["awayScore"])
        total += p
        exp_home += p * h
        exp_away += p * a
        if h > a:
            home += p
        elif a > h:
            away += p
        else:
            tie += p
    return {
        "total": total, "home": home, "away": away, "tie": tie,
        "expectedHome": exp_home, "expectedAway": exp_away,
    }


def _verify_joint(summaries: dict[str, Any]) -> None:
    cells = _require(summaries, "jointScoreDistribution", label="summaries")
    if not isinstance(cells, list) or not cells:
        raise SupplementImportError("jointScoreDistribution is empty")
    seen: set[tuple[int, int]] = set()
    for cell in cells:
        key = (int(cell["homeScore"]), int(cell["awayScore"]))
        if key in seen:
            raise SupplementImportError(f"jointScoreDistribution repeats score {key}")
        seen.add(key)
        if not (0.0 <= float(cell["probability"]) <= 1.0):
            raise SupplementImportError("jointScoreDistribution carries a probability outside [0, 1]")
    outcome = joint_outcomes(cells)
    if abs(outcome["total"] - 1.0) > 1e-3:
        raise SupplementImportError(f"jointScoreDistribution sums to {outcome['total']:.6f}, not 1")
    # The stated outcome probabilities must be the distribution's own.
    for stated, derived in (("homeWinProbability", "home"), ("awayWinProbability", "away"),
                            ("tieProbability", "tie")):
        value = summaries.get(stated)
        if value is None:
            raise SupplementImportError(f"summaries is missing '{stated}'")
        if abs(float(value) - outcome[derived]) > _PROBABILITY_TOLERANCE:
            raise SupplementImportError(
                f"{stated} {float(value):.4f} is not the joint distribution's own {outcome[derived]:.4f}"
            )


def _verify_drives(summaries: dict[str, Any]) -> None:
    drives = _require(summaries, "drives", label="summaries")
    final = _require(summaries, "finalScore", label="summaries")
    if not isinstance(drives, list) or not drives:
        raise SupplementImportError("drives is empty")
    home = away = 0
    previous_index = -1
    for drive in drives:
        index = int(_require(drive, "driveIndex", label="drive"))
        if index != previous_index + 1:
            raise SupplementImportError("drives are not contiguous from 0")
        previous_index = index
        if str(drive.get("offense")) not in ("HOME", "AWAY"):
            raise SupplementImportError("drive.offense must be HOME or AWAY")
        for key in ("startYardLine", "endYardLine"):
            yard = float(_require(drive, key, label="drive"))
            if not (0.0 <= yard <= 100.0):
                raise SupplementImportError(f"drive.{key} must be within 0..100")
        if str(drive.get("result")) not in DRIVE_RESULTS:
            raise SupplementImportError(f"drive.result '{drive.get('result')}' is not supported")
        int(_require(drive, "period", label="drive"))
        home_after = int(_require(drive, "homeScoreAfter", label="drive"))
        away_after = int(_require(drive, "awayScoreAfter", label="drive"))
        if home_after < home or away_after < away:
            raise SupplementImportError("a drive lowered a score")
        home, away = home_after, away_after
    if int(final.get("home", -1)) != home or int(final.get("away", -1)) != away:
        raise SupplementImportError("the drive replay does not reconcile to its own final score")


def _verify_scenarios(summaries: dict[str, Any]) -> None:
    baseline = _require(summaries, "baseline", label="summaries")
    scenarios = _require(summaries, "scenarios", label="summaries")
    inputs = _require(summaries, "inputsUsed", label="summaries")
    if not isinstance(inputs, list) or not inputs:
        raise SupplementImportError("inputsUsed must name at least one model input")
    for key in ("homeWinProbability", "awayWinProbability", "tieProbability"):
        _require(baseline, key, label="baseline")
    if not isinstance(scenarios, list) or not scenarios:
        raise SupplementImportError("scenarios is empty")
    used = set(map(str, inputs))
    ids: set[str] = set()
    for scenario in scenarios:
        sid = str(_require(scenario, "scenarioId", label="scenario"))
        if sid in ids:
            raise SupplementImportError(f"scenario '{sid}' repeats")
        ids.add(sid)
        changes = _require(scenario, "changedAssumptions", label="scenario")
        if not isinstance(changes, list) or not changes:
            raise SupplementImportError(f"scenario '{sid}' changes nothing")
        for change in changes:
            name = str(_require(change, "input", label="changed assumption"))
            if name not in used:
                raise SupplementImportError(
                    f"scenario '{sid}' changes '{name}', which the engine does not declare as an input"
                )
        result = _require(scenario, "result", label="scenario")
        for key in ("homeWinProbability", "awayWinProbability", "tieProbability"):
            _require(result, key, label=f"scenario '{sid}' result")


def _verify_study(summaries: dict[str, Any]) -> None:
    rows = _require(summaries, "leaderboard", label="summaries")
    if not isinstance(rows, list) or not rows:
        raise SupplementImportError("leaderboard is empty")
    cohort = _require(summaries, "cohort", label="summaries")
    int(_require(cohort, "games", label="cohort"))
    timing = str(_require(summaries, "benchmarkTimingClass", label="summaries"))
    if timing not in BENCHMARK_TIMING_CLASSES:
        raise SupplementImportError("benchmarkTimingClass is not supported")
    for row in rows:
        _require(row, "pipelineId", label="leaderboard row")


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def current_supplement_addresses(root: Path) -> set[str] | None:
    """Addresses the index declares current; None when there is no index."""

    path = Path(root) / SUPPLEMENT_ROOT / INDEX_FILENAME
    if not path.is_file():
        return None
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(index, dict) or index.get("schemaVersion") != INDEX_SCHEMA:
        return None
    found: set[str] = set()
    for value in index.get("currentSupplementSha256s") or []:
        text = str(value).lower()
        if _ADDRESS.fullmatch(text):
            found.add(text)
    return found


def discover_supplements(root: Path) -> list[Path]:
    """Every content-addressed supplement directory across all supplement trees."""

    resolved_root = Path(root).resolve()
    found: list[Path] = []
    for tree in SUPPLEMENT_ROOTS:
        base = Path(root) / tree / "sha256"
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not _ADDRESS.fullmatch(entry.name.lower()):
                continue
            candidate = entry.resolve()
            if resolved_root not in candidate.parents or not candidate.is_dir():
                continue
            found.append(candidate)
    return found


def chain_heads(verified: list[VerifiedSupplement]) -> tuple[list[VerifiedSupplement], list[str]]:
    """Split verified supplements into successor-chain heads and superseded addresses.

    Model Studio publishes an eligibility successor beside its predecessor, in
    the same tree, with no index saying which is current. Importing both would
    leave two permissions for one forecast. A supplement named as another's
    ``predecessorSupplementAddress`` is superseded and is not imported.
    """

    named = {str(v.supplement.get("predecessorSupplementAddress") or "").lower() for v in verified}
    heads = [v for v in verified if v.address not in named]
    superseded = sorted(v.address for v in verified if v.address in named)
    return heads, superseded


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def register_supplement(session: Session, verified: VerifiedSupplement) -> dict[str, Any]:
    """Persist one verified supplement; idempotent by content address."""

    existing = session.scalar(
        select(ResearchSupplement).where(ResearchSupplement.supplement_sha256 == verified.address)
    )
    if existing is not None:
        return {"supplement_sha256": existing.supplement_sha256, "kind": existing.kind,
                "idempotent_reuse": True}

    doc = verified.supplement
    binding = verified.binding
    sport = str(doc.get("sport") or "").upper()

    # Different bytes under a supplement id AISP already holds is a producer
    # conflict, not an update. A successor carries a new id and names its
    # predecessor; anything else is refused.
    same_id = session.scalar(select(ResearchSupplement).where(
        ResearchSupplement.supplement_id == str(doc["supplementId"])))
    if same_id is not None:
        raise SupplementImportError(
            f"supplementId {doc['supplementId']} is already registered at "
            f"{same_id.supplement_sha256}; conflicting bytes at {verified.address} are refused")

    forecast: ShadowForecast | None = None
    if verified.kind == KIND_PAPER_ELIGIBILITY and sport in ("NBA", "CFB"):
        _bind_research_forecast(session, verified, sport)
    elif verified.kind in EVENT_BOUND_KINDS:
        forecast = session.scalar(select(ShadowForecast).where(
            ShadowForecast.forecast_id == str(binding["forecastId"]),
            ShadowForecast.event_id == str(binding["eventId"]),
            ShadowForecast.engine_id == str(binding["engineId"]),
        ))
        if forecast is None:
            raise SupplementImportError(
                "Supplement binds to a forecast AISP has not registered "
                f"({binding['engineId']} / {binding['eventId']} / {binding['forecastId']})"
            )
        declared_artifact = binding.get("modelArtifactSha256")
        if declared_artifact and str(declared_artifact).lower() != forecast.model_artifact_sha256:
            raise SupplementImportError("Supplement binds to a different model artifact than the forecast")

    simulation = doc["simulation"]
    row = ResearchSupplement(
        supplement_sha256=verified.address,
        supplement_id=str(doc["supplementId"]),
        contract_version=CONTRACT_VERSION,
        kind=verified.kind,
        sport=str(doc["sport"]).upper(),
        event_id=str(binding["eventId"]) if binding.get("eventId") else None,
        engine_id=str(binding["engineId"]) if binding.get("engineId") else None,
        forecast_id=str(binding["forecastId"]) if binding.get("forecastId") else None,
        shadow_forecast_id=forecast.id if forecast else None,
        artifact_version=str(doc["artifactVersion"]),
        simulation_kind=str(simulation["kind"]),
        simulation_count=int(simulation["count"]) if simulation.get("count") is not None else None,
        simulation_seed=int(simulation["seed"]) if simulation.get("seed") is not None else None,
        source_cutoff_utc=_iso(doc["sourceCutoffUtc"], label="sourceCutoffUtc"),
        published_utc=_iso(doc["publishedUtc"], label="publishedUtc"),
        status="RESEARCH_SHADOW",
        research_shadow_only=True,
        payload={
            "summaries": doc.get("summaries") or {},
            "paths": doc.get("paths") or [],
            "scenarios": doc.get("scenarios") or [],
            "producer": doc.get("producer") or verified.manifest.get("producer") or {},
        },
        evidence={
            "files": verified.files,
            "manifest_schema": MANIFEST_SCHEMA,
            "binding": binding,
            "governance": doc.get("governance"),
            "verified_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    session.add(row)
    session.flush()
    return {"supplement_sha256": verified.address, "kind": verified.kind,
            "event_id": row.event_id, "engine_id": row.engine_id,
            "forecast_id": row.forecast_id, "idempotent_reuse": False}


def _bind_research_forecast(session: Session, verified: VerifiedSupplement, sport: str) -> None:
    """NBA and CFB forecasts live in their own research tables, not shadow_forecasts.

    Every identity the document asserts is compared with what AISP imported:
    forecast digest, source package, configuration, probabilities, cutoff,
    provider game and model artifact. A document that binds a forecast AISP does
    not hold -- or holds differently -- is refused.
    """

    from sqlalchemy import text

    doc = verified.supplement
    binding = verified.binding
    summaries = doc["summaries"]
    frozen = summaries["frozenConfiguration"]
    frozen_forecast = summaries.get("frozenForecast") or {}
    cutoff = _iso(doc["sourceCutoffUtc"], label="sourceCutoffUtc")

    def close(a: Any, b: Any) -> bool:
        return a is not None and b is not None and abs(float(a) - float(b)) <= _PROBABILITY_TOLERANCE

    if sport == "NBA":
        row = session.execute(text(
            "select a.forecast_sha256, a.package_sha256, a.configuration_identity, a.arm, "
            "a.home_win_probability, a.away_win_probability, a.tie_probability, "
            "a.source_cutoff_utc, a.canonical_game_id, a.supplements_forecast_id "
            "from nba_joint_comparison_arms a where a.forecast_id = :f"),
            {"f": str(binding["forecastId"])}).mappings().first()
        if row is None:
            raise SupplementImportError(
                f"NBA eligibility binds forecast {binding['forecastId']}, which AISP has not imported")
        mapped = session.execute(text(
            "select entity_id from external_id_mappings where entity_type = 'game' "
            "and source = 'NBA_ESPN' and external_id = :x"),
            {"x": str(binding.get("providerGameId"))}).scalar()
        artifact = session.execute(text(
            "select detail->'bindings'->'pointModelAndPreprocessor'->>'sha256' "
            "from nba_research_forecasts where forecast_id = :f"),
            {"f": str(row["supplements_forecast_id"])}).scalar()
        checks = {
            "arm is the baseline": row["arm"] == "baseline",
            "forecast digest": row["forecast_sha256"] == frozen_forecast.get("forecastSha256"),
            "source package": row["package_sha256"] == binding.get("sourcePackageAddress"),
            "configuration": row["configuration_identity"] == binding.get("configurationIdentity"),
            "home probability": close(row["home_win_probability"], frozen["homeWinProbability"]),
            "away probability": close(row["away_win_probability"], frozen["awayWinProbability"]),
            "tie probability": close(row["tie_probability"] or 0.0, frozen["tieProbability"]),
            "source cutoff": (row["source_cutoff_utc"] is not None
                              and row["source_cutoff_utc"].astimezone(UTC) == cutoff),
            "provider game": (str(binding.get("eventId")) == f"nba-espn:game:{binding.get('providerGameId')}"
                              and mapped is not None and str(mapped) == str(row["canonical_game_id"])),
            "model artifact": artifact == binding.get("modelArtifactSha256"),
        }
    else:
        row = session.execute(text(
            "select forecast_sha256, package_index_sha256, baseline_configuration, source_game_id, "
            "joint_home_win_probability, joint_away_win_probability, tie_probability, "
            "source_cutoff_utc, model_bindings->'selectedModel'->>'physicalSha256' as artifact "
            "from cfb_research_forecasts where forecast_id = :f"),
            {"f": str(binding["forecastId"])}).mappings().first()
        if row is None:
            raise SupplementImportError(
                f"CFB eligibility binds forecast {binding['forecastId']}, which AISP has not imported")
        checks = {
            "forecast digest": row["forecast_sha256"] == frozen_forecast.get("forecastSha256"),
            "source package": row["package_index_sha256"] == binding.get("sourcePackageAddress"),
            "configuration": row["baseline_configuration"] == binding.get("configurationIdentity"),
            "home probability": close(row["joint_home_win_probability"], frozen["homeWinProbability"]),
            "away probability": close(row["joint_away_win_probability"], frozen["awayWinProbability"]),
            "tie probability": close(row["tie_probability"], frozen["tieProbability"]),
            "source cutoff": row["source_cutoff_utc"].astimezone(UTC) == cutoff,
            "provider game": (str(binding.get("providerGameId")) == row["source_game_id"]
                              and str(binding.get("eventId")) == f"cfbd:{row['source_game_id']}"),
            "model artifact": row["artifact"] == binding.get("modelArtifactSha256"),
        }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise SupplementImportError(
            f"{sport} eligibility does not match AISP's imported forecast: {', '.join(failed)}")


def import_named_supplements(session: Session, root: Path, addresses: list[str]) -> dict[str, Any]:
    """Import exactly the named supplement addresses, successor-aware and idempotent.

    Each address is located under the known supplement trees, verified in full
    and registered through the same path the consumer uses. A named address that
    another present supplement supersedes is refused rather than imported.
    """

    wanted = [a.lower() for a in addresses]
    directories = {d.name.lower(): d for d in discover_supplements(root)}
    present: list[VerifiedSupplement] = []
    for directory in directories.values():
        try:
            present.append(verify_supplement(directory))
        except SupplementImportError:
            continue
    _, superseded = chain_heads(present)
    receipts: list[dict[str, Any]] = []
    for address in wanted:
        if address not in directories:
            receipts.append({"supplement_sha256": address, "result": "REJECTED",
                             "reason": "not found under any supplement tree"})
            continue
        try:
            item = verify_supplement(directories[address])
        except SupplementImportError as error:
            receipts.append({"supplement_sha256": address, "result": "REJECTED", "reason": str(error)})
            continue
        if address in superseded:
            receipts.append({"supplement_sha256": address, "result": "REJECTED",
                             "reason": "superseded by a successor present in the exchange"})
            continue
        try:
            with session.begin_nested():
                record = register_supplement(session, item)
        except SupplementImportError as error:
            receipts.append({"supplement_sha256": address, "result": "REJECTED", "reason": str(error)})
            continue
        receipts.append({**record,
                         "result": "ALREADY_PRESENT" if record["idempotent_reuse"] else "IMPORTED",
                         "supplement_id": item.supplement.get("supplementId"),
                         "sport": item.supplement.get("sport"),
                         "artifact_version": item.supplement.get("artifactVersion"),
                         "predecessor": item.supplement.get("predecessorSupplementAddress"),
                         "files": item.files})
    return {"receipts": receipts, "superseded_present": superseded}


__all__ = [
    "CONTRACT_VERSION", "INDEX_SCHEMA", "KIND_DRIVE", "KIND_JOINT", "KIND_SCENARIO", "KIND_STUDY",
    "SupplementImportError", "VerifiedSupplement", "current_supplement_addresses",
    "PAPER_ELIGIBILITY_VERSIONS", "SUPPLEMENT_ROOTS", "chain_heads", "discover_supplements",
    "import_named_supplements", "joint_outcomes", "register_supplement", "verify_supplement",
]
