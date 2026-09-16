"""Write AISP's additive consumer acknowledgement of the market-probability contract.

Run by the deployment owner after the contract is verified. It re-runs the
golden vectors through AISP's vendored module, reads the running backend's
population bindings (read-only HTTP to 127.0.0.1:8010), and writes one new
JSON file. It never edits an existing file, imports nothing into the database,
promotes no model, changes no forecast and grants no authority.

Usage:
  python scripts/write_probability_acknowledgement.py --implementation-sha <sha> --out <dir> [--game SPORT=uuid ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from app.probability import adapter, identity  # noqa: E402
from app.probability.market_probabilities import (  # noqa: E402
    JointScorePopulation, evaluate_market, evaluate_same_game_joint,
)

FIXTURES = ROOT / "server" / "tests" / "fixtures" / "market_probability_contract_v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def golden() -> dict:
    fixture = json.loads((FIXTURES / "golden-vectors.json").read_bytes())
    population = JointScorePopulation.from_cells(**fixture["population"])
    results = []
    for vector in fixture["vectors"]:
        result = (evaluate_market(population, vector["leg"]) if vector["kind"] == "single"
                  else evaluate_same_game_joint(population, vector["legs"]))
        results.append({"name": vector["name"], "expected": vector["expectedCounts"], "actual": result["counts"],
                        "exact": result["counts"] == vector["expectedCounts"]})
    return {"populationSha256": population.population_sha256,
            "expectedPopulationSha256": fixture["expectedPopulationSha256"],
            "vectors": results,
            "allExact": all(r["exact"] for r in results)
            and population.population_sha256 == fixture["expectedPopulationSha256"]}


def live_binding(game_id: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:8010/api/v1/probability/games/{game_id}", timeout=60) as response:
        body = json.loads(response.read())
    out = []
    for channel in body["channels"]:
        out.append({k: channel.get(k) for k in ("channel", "status", "binding", "authority", "error", "forecast_id",
                                                  "draw_count", "capabilities")})
    return {"gameId": game_id, "sport": body["sport"], "channels": out, "crossGame": body["cross_game"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--implementation-sha", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--tested-at", required=True)
    parser.add_argument("--tests", required=True, help="short test summary")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--game", action="append", default=[], help="SPORT=canonical-game-uuid")
    args = parser.parse_args()

    if sha(Path(adapter.__file__).with_name("market_probabilities.py")) != identity.PRODUCER_MODULE_SHA256:
        raise SystemExit("vendored module differs from the producer commit")
    check = golden()
    if not check["allExact"]:
        raise SystemExit("golden vectors differ")

    document = {
        "schemaVersion": "AISP_MARKET_PROBABILITY_CONSUMER_ACKNOWLEDGEMENT_V1",
        "nature": "ADDITIVE_CONSUMER_ACKNOWLEDGEMENT_NOT_AN_IMPORT_OR_PROMOTION",
        "createdAtUtc": datetime.now(UTC).isoformat(),
        "producer": {
            "repository": identity.PRODUCER_REPOSITORY,
            "branch": identity.PRODUCER_CONTRACT_BRANCH,
            "contractSha": identity.PRODUCER_CONTRACT_SHA,
            "implementationSha": identity.PRODUCER_IMPLEMENTATION_SHA,
            "claudeHandoffSha256": identity.PRODUCER_HANDOFF_SHA256,
            "moduleSha256": identity.PRODUCER_MODULE_SHA256,
            "requestSchemaSha256": identity.REQUEST_SCHEMA_SHA256,
            "responseSchemaSha256": identity.RESPONSE_SCHEMA_SHA256,
            "capabilityMatrixSha256": identity.CAPABILITY_MATRIX_SHA256,
            "goldenVectorsSha256": identity.GOLDEN_VECTORS_SHA256,
        },
        "crossGameValidation": {
            "branch": identity.CROSS_GAME_STUDY_BRANCH,
            "sha": identity.CROSS_GAME_STUDY_SHA,
            "resultsSha256": identity.CROSS_GAME_RESULTS_SHA256,
            "consumerDisposition": "FAIL_CLOSED",
            "stableReason": identity.CROSS_GAME_CALIBRATION_NOT_VALIDATED,
            "notes": [
                "No cross-game probability, fair price, edge or expected return is served or displayed.",
                "The producer INDEPENDENCE_ASSUMPTION calculation is not reachable from any AISP route or page.",
                "POST /api/v1/probability/cross-game and any crossGameMode other than REJECT return 422.",
            ],
        },
        "consumer": {
            "repository": "Gearhj/ai-sports-predictor",
            "branch": args.branch,
            "implementationSha": args.implementation_sha,
            "vendoredModule": "server/app/probability/market_probabilities.py (byte-identical)",
            "adapter": "server/app/probability/adapter.py",
            "testedAtUtc": args.tested_at,
            "tests": args.tests,
        },
        "goldenVectors": check,
        "supportedPopulations": {
            sport: {k: v for k, v in admitted.items()} for sport, admitted in adapter.ADMITTED.items()
        } | {"NBA_POINT_ELO": {"support": "WINNER_CHANNEL_ONLY", "drawCount": 0,
                                "unsupported": ["SPREAD", "TOTAL", "SAME_GAME_PARLAY"]}},
        "sameGame": {
            "method": "PER_DRAW_SAME_GAME_INTERSECTION",
            "consumerDisplayLabel": identity.SAME_GAME_DISPLAY_METHOD,
            "requirement": "every leg evaluated against one re-verified population of one canonical game and forecast revision",
            "sportsbookParlayQuotesStored": 0,
            "edgeAndExpectedReturn": "only against a recorded sportsbook combined quote; none exists, so none is shown",
            "createsDecisionsOrPositions": False,
        },
        "liveVerification": [live_binding(value.split("=", 1)[1]) for value in args.game],
        "authority": {
            "modelChanged": False, "forecastChanged": False, "authorityGranted": False,
            "decisionPolicyChanged": False, "positionsCreated": False, "providerCalls": 0,
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = args.out / f"aisp-market-probability-acknowledgement-{args.implementation_sha[:12]}-{stamp}.json"
    if path.exists():
        raise SystemExit(f"{path} already exists")
    raw = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with open(path, "xb") as handle:
        handle.write(raw)
    print(path)
    print(hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    main()
