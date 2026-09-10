"""Reconcile the V2 play-level publication against its V1 predecessor.

WHAT THIS PROVES, AND WHY IT IS WORTH PROVING
---------------------------------------------
V2 adds one field. Everything else is produced by the same frozen model from
the same seeds and must be identical -- so if any of it moved, the "supplement"
quietly republished the forecast, and every number AISP has already shown for
these games changed underneath it. That is the failure this checks for.

It also re-derives the one internal claim the block makes about itself:
`populationSpatial.playCount` is defined exactly as `populationAccounting.plays`
and its cells sum to it. Nothing downstream can check the block against the
plays -- 994 of the 1,000 games ship none -- so this is the check that exists.

    python -m scripts.verify_nfl_population_spatial <predecessor-index.json>
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

EXCHANGE = Path(r"C:\Users\james\asp-model-exchange")
FAMILY = "nfl-labs-play-level-supplements"

#: Produced by the same frozen model from the same seeds. Any difference here
#: means the population itself changed, not just its summary.
MUST_MATCH = (
    "eventId", "homeTeam", "awayTeam", "model", "scheduledStartUtc", "sourceCutoffUtc",
    "drawCount", "seed", "populationFinals", "simulatedGames", "representativeFinals",
    "scoreSummary", "quantiles", "jointScoreDistribution", "scoreMarginDistribution",
    "totalScoreDistribution", "reachingOvertimeProbability", "finalTieProbability",
    "populationAccounting", "scenario", "scenarioDelta", "m1Comparison",
    "generatingLaw", "inputTreatment", "limitations", "sourceRights",
    "relatedM1ForecastId", "explainsM1Forecast", "sourceForecast",
)

#: Expected to differ. A new schema is a new artifact identity, and it is
#: published at its own moment against its own contract.
MAY_DIFFER = frozenset({
    "schemaVersion", "forecastId", "contract", "declaration", "validationReceipt",
    "forecastCreatedUtc", "inferenceStartedUtc", "publishedAtUtc", "populationSpatial",
    "sourceAvailability", "sourceM1Supplement", "historicalEvidenceThroughDate",
    "historicalEvidencePrecision",
})


def load(index: Path) -> dict[tuple[str, str], dict]:
    out = {}
    for item in json.loads(index.read_bytes())["artifacts"]:
        body = json.loads(Path(item["artifact"]["path"]).read_bytes())
        out[(body["eventId"], body["scenario"]["scenarioId"])] = body
    return out


def check_spatial(body: dict) -> list[str]:
    spatial = body.get("populationSpatial")
    if spatial is None:
        return ["no populationSpatial"]
    problems = []
    plays = body["populationAccounting"]["plays"]
    if spatial["playCount"] != plays:
        problems.append(f"playCount {spatial['playCount']} != populationAccounting.plays {plays}")
    binned = sum(cell["count"] for cell in spatial["cells"])
    if binned != spatial["playCount"]:
        problems.append(f"cells sum {binned} != playCount {spatial['playCount']}")
    keys = Counter((c["playType"], str(c["start"]), str(c["yards"])) for c in spatial["cells"])
    if any(count > 1 for count in keys.values()):
        problems.append("duplicate cells")
    # The count must EXCLUDE period-end markers, or it is not the same quantity
    # the accounting reports and the two will disagree by exactly that number.
    by_type = body["populationAccounting"]["playTypeCounts"]
    if sum(by_type.values()) - by_type.get("PERIOD_END", 0) != spatial["playCount"]:
        problems.append("playCount does not equal all plays minus PERIOD_END markers")
    return problems


def main(argv: list[str]) -> int:
    predecessor = Path(argv[1]) if len(argv) > 1 else None
    if predecessor is None or not predecessor.is_file():
        print("usage: verify_nfl_population_spatial.py <predecessor-index.json>")
        return 2

    old = load(predecessor)
    new = load(EXCHANGE / FAMILY / "current.json")

    print(f"predecessor artifacts : {len(old)}")
    print(f"current artifacts     : {len(new)}")

    v2 = {key: body for key, body in new.items()
          if body["schemaVersion"] == "NFL_LABS_PLAY_LEVEL_SIMULATION_V2"}
    v1 = {key: body for key, body in new.items()
          if body["schemaVersion"] == "NFL_LABS_PLAY_LEVEL_SIMULATION_V1"}
    print(f"  regenerated at V2   : {len(v2)}")
    print(f"  carried forward V1  : {len(v1)}")

    missing = sorted(set(old) - set(new))
    if missing:
        print(f"\nFAIL: {len(missing)} population(s) disappeared from the index:")
        for key in missing[:10]:
            print("   ", key)
        return 1
    print("\nno population disappeared from the index: OK")

    failures = 0
    drifted: list[str] = []
    for key, body in sorted(v2.items()):
        before = old.get(key)
        if before is None:
            print(f"NOTE new population with no predecessor: {key}")
            continue
        for field in MUST_MATCH:
            if before.get(field) != body.get(field):
                drifted.append(f"{key[0]}/{key[1]}: {field}")
        for problem in check_spatial(body):
            failures += 1
            print(f"FAIL {key[0]}/{key[1]}: {problem}")

    if drifted:
        print(f"\nFAIL: {len(drifted)} field(s) changed that must not:")
        for entry in drifted[:20]:
            print("   ", entry)
        return 1
    print(f"byte-for-byte preservation across {len(MUST_MATCH)} fields x {len(v2)} populations: OK")

    for key, body in sorted(v1.items()):
        if body.get("populationSpatial") is not None:
            print(f"FAIL carried-forward {key} unexpectedly carries populationSpatial")
            failures += 1

    if failures:
        print(f"\n{failures} reconciliation failure(s)")
        return 1

    total_plays = sum(b["populationSpatial"]["playCount"] for b in v2.values())
    total_cells = sum(b["populationSpatial"]["cellCount"] for b in v2.values())
    print(f"populationSpatial reconciles on all {len(v2)} V2 populations")
    print(f"  plays summarised : {total_plays:,}")
    print(f"  cells            : {total_cells:,}")
    if v1:
        print(f"  {len(v1)} population(s) remain at V1 and carry no summary, as expected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
