"""Preview fixtures for the NHL Gen1 contract. NOT real games, forecasts or results.

Builds content-addressed exchange trees exactly as the contract describes, so the
importer is exercised on bytes, not on dictionaries. Scenarios:

* ``FUTURE``      a scheduled game with a forecast (two revisions; the second
                  confirms the home goalie);
* ``REG_FINAL``   a regulation final with a forecast;
* ``OT_FINAL``    an overtime final with a forecast;
* ``SO_FINAL``    a shootout final with a forecast (the winner is credited one goal);
* ``NO_FORECAST`` a scheduled game with no forecast;
* revised forecast, identical re-import and a conflicting payload under an
  identity already held (``conflict_tree``).

Every fixture game is created with ``games.source_mode = 'FIXTURE'`` and the UI
labels it as such. Team ids are the NHL's own; scores are invented.
"""

from __future__ import annotations

import copy
import json
import math
import pathlib
from datetime import datetime, timedelta
from typing import Any

from app.nhl import contract as c

MODEL = {"modelId": "NHL_GEN1_PREVIEW_FIXTURE", "modelVersion": "0.0.1-fixture",
         "configurationSha256": "0" * 63 + "1", "trainingCutoffUtc": None, "handoffSha256": None}

TEAMS = {
    "TOR": ("10", "Toronto Maple Leafs"), "MTL": ("8", "Montreal Canadiens"), "BOS": ("6", "Boston Bruins"),
    "NYR": ("3", "New York Rangers"), "EDM": ("22", "Edmonton Oilers"), "CGY": ("20", "Calgary Flames"),
    "COL": ("21", "Colorado Avalanche"), "VGK": ("54", "Vegas Golden Knights"), "DAL": ("25", "Dallas Stars"),
    "MIN": ("30", "Minnesota Wild"),
}


#: The producer-evidence receipt every fixture binds. Written into each tree at
#: EVIDENCE_PATH so the importer can actually verify the binding.
EVIDENCE_PATH = "evidence/preview-fixture-producer-receipt.json"
EVIDENCE_BYTES = c.canonical_bytes({"schema": "PREVIEW_FIXTURE_PRODUCER_RECEIPT", "note":
                                    "Invented for the AISP NHL preview. Binds nothing real."})
PROVENANCE = {"evidenceMode": "RECONSTRUCTED_HISTORY_CURRENT_FORECAST",
              "producerEvidenceRef": {"path": EVIDENCE_PATH, "sha256": c.sha256_hex(EVIDENCE_BYTES),
                                      "bytes": len(EVIDENCE_BYTES)},
              "goalieFeatureMode": "TEAM_LEVEL"}


def z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def team(code: str) -> dict[str, Any]:
    tid, name = TEAMS[code]
    return {"canonicalTeamId": f"nhl:team:{tid}", "sourceTeamId": tid, "triCode": code, "name": name}


def game_block(source_id: str, home: str, away: str, start: datetime) -> dict[str, Any]:
    year = int(source_id[:4])
    return {"canonicalGameId": f"nhl:game:{source_id}", "sourceGameId": source_id, "season": f"{year}-{str(year + 1)[-2:]}",
            "seasonStartYear": year, "gameType": c.GAME_TYPES[source_id[4:6]], "scheduledStartUtc": z(start),
            "home": team(home), "away": team(away), "neutralSite": False,
            "scheduleSource": {"source": "PREVIEW_FIXTURE", "observedAtUtc": None, "retrievedAtUtc": None}}


def poisson_matrix(mu_home: float, mu_away: float, size: int = 10) -> list[list[float]]:
    def pmf(mu: float) -> list[float]:
        out = [math.exp(-mu) * mu ** k / math.factorial(k) for k in range(size)]
        return out + [max(0.0, 1.0 - sum(out))]

    ph, pa = pmf(mu_home), pmf(mu_away)
    return [[ph[h] * pa[a] for a in range(size + 1)] for h in range(size + 1)]


def goalie(status: str, *, gid: str | None = None, name: str | None = None, observed: datetime | None = None) -> dict[str, Any]:
    if status == "UNKNOWN":
        return {"status": "UNKNOWN", "goalieId": None, "goalieName": None, "statusSource": None,
                "statusObservedAtUtc": None}
    return {"status": status, "goalieId": f"nhl:player:{gid}" if gid else None, "goalieName": name,
            "statusSource": "PREVIEW_FIXTURE_GOALIE_FEED", "statusObservedAtUtc": z(observed) if observed else None}


def forecast(game: dict[str, Any], *, created: datetime, home_mu: float, away_mu: float, ot_home: float,
             goalies: dict[str, Any], missing: list[str], with_distribution: bool = False,
             supersedes: str | None = None, published: datetime | None = None,
             final_only: bool = False, goalie_mode: str = "TEAM_LEVEL") -> dict[str, Any]:
    """A contract-valid forecast built from a regulation Poisson matrix (so every mass is consistent).

    ``final_only`` mimics a final-only Elo model: regulation, overtime and both goal
    horizons are explicit null; only the final home/away probability is supplied.
    V1 transmits no goal distribution (``with_distribution`` is kept for API
    compatibility and ignored).
    """

    matrix = poisson_matrix(home_mu, away_mu)
    n = len(matrix)
    home = sum(matrix[h][a] for h in range(n) for a in range(n) if h > a)
    tie = sum(matrix[i][i] for i in range(n))
    away = 1.0 - home - tie
    final_home = home + tie * ot_home
    playoff = game["gameType"] == "PLAYOFF"
    p_so = 0.0 if playoff else tie * 0.42
    reg_h = sum(h * matrix[h][a] for h in range(n) for a in range(n))
    reg_a = sum(a * matrix[h][a] for h in range(n) for a in range(n))
    doc = {
        "schema": c.FORECAST_SCHEMA, "canonicalization": c.CANONICALIZATION, "league": "NHL", "sport": "HOCKEY",
        "forecastId": None, "supersedesForecastId": supersedes, "model": copy.deepcopy(MODEL), "game": game,
        "researchStatus": "RESEARCH_ONLY",
        "authority": {"wageringAuthority": False, "decisionAuthority": False, "positionAuthority": False,
                      "modelPromotionAuthority": False},
        "sourceCutoffUtc": z(created - timedelta(minutes=40)), "sourceRetrievedAtUtc": z(created - timedelta(minutes=30)),
        "createdAtUtc": z(created), "publishedAtUtc": z(published or created + timedelta(minutes=5)),
        "probabilities": {
            "regulation": {"home": home, "tie": tie, "away": away},
            "final": {"home": final_home, "away": 1.0 - final_home},
            "overtime": {"pOvertime": tie, "pShootout": p_so,
                         "conditionalOnTieAfterRegulation": {"home": ot_home, "away": 1.0 - ot_home},
                         "conditionalOnShootout": None if playoff else {"home": 0.5, "away": 0.5}},
        },
        "goals": {
            "regulation": {"horizon": c.REGULATION_HORIZON, "expectedHome": reg_h, "expectedAway": reg_a,
                           "distribution": None},
            "final": {"horizon": c.FINAL_HORIZON, "expectedHome": reg_h + tie * ot_home,
                      "expectedAway": reg_a + tie * (1.0 - ot_home), "distribution": None},
        },
        "goalies": goalies, "missingInputs": missing,
        "limitations": ["PREVIEW FIXTURE: invented numbers for exercising the importer and the pages."],
        "provenance": {**copy.deepcopy(PROVENANCE), "goalieFeatureMode": goalie_mode},
    }
    if final_only:
        doc["probabilities"]["regulation"] = None
        doc["probabilities"]["overtime"] = None
        doc["goals"] = {"regulation": None, "final": None}
        doc["limitations"].append("FINAL_ONLY_NO_REGULATION_OR_GOAL_MODEL")
    return c.with_identity(doc)


def result(game: dict[str, Any], *, reg: tuple[int, int], decided: str, ot: tuple[int, int] | None = None,
           so: tuple[int, int] | None = None, observed: datetime) -> dict[str, Any]:
    rh, ra = reg
    fh, fa = rh, ra
    if decided == "OVERTIME":
        fh, fa = rh + ot[0], ra + ot[1]  # type: ignore[index]
    if decided == "SHOOTOUT":
        fh, fa = (rh + 1, ra) if so[0] > so[1] else (rh, ra + 1)  # type: ignore[index]
    reg_result = "HOME" if rh > ra else "AWAY" if ra > rh else "TIE"
    doc = {
        "schema": c.RESULT_SCHEMA, "canonicalGameId": game["canonicalGameId"], "gameType": game["gameType"],
        "homeCanonicalTeamId": game["home"]["canonicalTeamId"], "awayCanonicalTeamId": game["away"]["canonicalTeamId"],
        "regulation": {"homeGoals": rh, "awayGoals": ra, "result": reg_result, "source": "PREVIEW_FIXTURE_BOXSCORE",
                       "observedAtUtc": z(observed), "retrievedAtUtc": z(observed + timedelta(minutes=2))},
        "decidedIn": decided,
        "overtime": ({"played": True, "homeGoals": ot[0] if ot else 0, "awayGoals": ot[1] if ot else 0, "periods": 1}
                     if decided != "REGULATION" else None),
        "shootout": {"played": True, "homeScored": so[0], "awayScored": so[1]} if decided == "SHOOTOUT" else None,
        "final": {"homeScore": fh, "awayScore": fa, "winner": "HOME" if fh > fa else "AWAY", "official": True,
                  "source": "PREVIEW_FIXTURE_BOXSCORE", "observedAtUtc": z(observed + timedelta(minutes=20)),
                  "retrievedAtUtc": z(observed + timedelta(minutes=22)), "completedAtUtc": None},
        "supersedesResultSha256": None, "resultSha256": None,
    }
    return c.with_result_identity(doc)


# ---------------------------------------------------------------- writing --
def write_object(root: pathlib.Path, document: dict[str, Any], name: str, *, raw: bytes | None = None) -> dict[str, Any]:
    raw = raw if raw is not None else c.canonical_bytes(document)
    sha = c.sha256_hex(raw)
    folder = root / "objects" / "sha256" / sha
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(raw)
    entry = {"relativePath": f"objects/sha256/{sha}/{name}", "sha256": sha, "bytes": len(raw),
             "canonicalGameId": document.get("canonicalGameId") or (document.get("game") or {}).get("canonicalGameId")}
    if name == "forecast.json":
        entry["forecastId"] = document.get("forecastId")
        entry["publishedAtUtc"] = document.get("publishedAtUtc")
    return entry


def write_manifest(root: pathlib.Path, schema: str, entries: list[dict[str, Any]], created: datetime) -> dict[str, Any]:
    kind = "forecasts" if schema == c.FORECAST_MANIFEST_SCHEMA else "results"
    manifest = {"schema": schema, "manifestCreatedAtUtc": z(created), "producer": "PREVIEW_FIXTURE",
                "modelId": MODEL["modelId"] if kind == "forecasts" else None,
                "modelVersion": MODEL["modelVersion"] if kind == "forecasts" else None,
                "researchStatus": "RESEARCH_ONLY", "publicationReceipt": None, "objects": entries,
                "counts": {kind: len(entries)}}
    raw = c.canonical_bytes(manifest)
    sha = c.sha256_hex(raw)
    folder = root / "manifests" / "sha256" / sha
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_bytes(raw)
    return {"path": f"manifests/sha256/{sha}/manifest.json", "sha256": sha, "bytes": len(raw)}


def write_pointer(root: pathlib.Path, forecast_ref: dict[str, Any] | None, result_ref: dict[str, Any] | None) -> None:
    evidence = root / EVIDENCE_PATH
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_bytes(EVIDENCE_BYTES)
    (root / "current.json").write_bytes(c.canonical_bytes(
        {"schema": c.POINTER_SCHEMA, "forecastManifest": forecast_ref, "resultManifest": result_ref}))


def scenario(now: datetime) -> dict[str, Any]:
    """The fixture slate relative to ``now``: finals in the past, scheduled games ahead."""

    day = now.replace(hour=23, minute=0, second=0, microsecond=0)
    future_start = day + timedelta(days=1) if day - now < timedelta(hours=6) else day
    games = {
        "REG_FINAL": game_block("2026010071", "TOR", "MTL", day - timedelta(days=3)),
        "OT_FINAL": game_block("2026010072", "BOS", "NYR", day - timedelta(days=2)),
        "SO_FINAL": game_block("2026010073", "EDM", "CGY", day - timedelta(days=1)),
        "FUTURE": game_block("2026010080", "COL", "VGK", future_start),
        "NO_FORECAST": game_block("2026010081", "DAL", "MIN", future_start + timedelta(hours=1, minutes=30)),
    }
    gstart = {k: c.parse_utc(g["scheduledStartUtc"]) for k, g in games.items()}
    t = lambda k, h: gstart[k] - timedelta(hours=h)  # noqa: E731
    future_v1 = forecast(games["FUTURE"], created=t("FUTURE", 20), home_mu=3.15, away_mu=2.85, ot_home=0.53,
                         goalies={"home": goalie("PROJECTED", gid="8480382", name="Projected home goalie (fixture)",
                                                 observed=t("FUTURE", 21)),
                                  "away": goalie("UNKNOWN")},
                         missing=["HOME_GOALIE_UNCONFIRMED", "AWAY_GOALIE_UNKNOWN", "MARKET_PRICES_NOT_USED"],
                         goalie_mode="GOALIE_SPECIFIC")
    future_v2 = forecast(games["FUTURE"], created=t("FUTURE", 6), home_mu=3.05, away_mu=2.95, ot_home=0.52,
                         goalies={"home": goalie("CONFIRMED", gid="8480382", name="Projected home goalie (fixture)",
                                                 observed=t("FUTURE", 6.5)),
                                  "away": goalie("PROJECTED", gid="8478406", name="Projected away goalie (fixture)",
                                                 observed=t("FUTURE", 6.5))},
                         missing=["AWAY_GOALIE_UNCONFIRMED", "MARKET_PRICES_NOT_USED"],
                         supersedes=future_v1["forecastId"], goalie_mode="GOALIE_SPECIFIC")
    confirmed = lambda k: {"home": goalie("CONFIRMED", gid="8475883", name="Home goalie (fixture)",  # noqa: E731
                                          observed=t(k, 3)),
                           "away": goalie("CONFIRMED", gid="8476945", name="Away goalie (fixture)", observed=t(k, 3))}
    forecasts = {
        "REG_FINAL": forecast(games["REG_FINAL"], created=t("REG_FINAL", 2), home_mu=3.3, away_mu=2.6, ot_home=0.54,
                              goalies=confirmed("REG_FINAL"), missing=["MARKET_PRICES_NOT_USED"],
                              goalie_mode="GOALIE_SPECIFIC"),
        "OT_FINAL": forecast(games["OT_FINAL"], created=t("OT_FINAL", 2), home_mu=2.9, away_mu=2.8, ot_home=0.51,
                             goalies=confirmed("OT_FINAL"), missing=["MARKET_PRICES_NOT_USED"]),
        # Final-only (Elo-like): regulation not modelled, team-level goalies.
        "SO_FINAL": forecast(games["SO_FINAL"], created=t("SO_FINAL", 2), home_mu=3.4, away_mu=3.0, ot_home=0.52,
                             goalies={"home": goalie("UNKNOWN"), "away": goalie("UNKNOWN")},
                             missing=["HOME_GOALIE_UNKNOWN", "AWAY_GOALIE_UNKNOWN", "MARKET_PRICES_NOT_USED"],
                             final_only=True),
        "FUTURE_V1": future_v1,
        "FUTURE_V2": future_v2,
    }
    results = {
        "REG_FINAL": result(games["REG_FINAL"], reg=(4, 2), decided="REGULATION", observed=gstart["REG_FINAL"] + timedelta(hours=2, minutes=40)),
        "OT_FINAL": result(games["OT_FINAL"], reg=(2, 2), decided="OVERTIME", ot=(0, 1), observed=gstart["OT_FINAL"] + timedelta(hours=2, minutes=40)),
        "SO_FINAL": result(games["SO_FINAL"], reg=(3, 3), decided="SHOOTOUT", ot=(0, 0), so=(2, 1), observed=gstart["SO_FINAL"] + timedelta(hours=2, minutes=50)),
    }
    return {"games": games, "forecasts": forecasts, "results": results}


def conflicting(document: dict[str, Any]) -> dict[str, Any]:
    """Same ``forecastId``, different content: what the importer must refuse."""

    bad = copy.deepcopy(document)
    reg = bad["probabilities"]["regulation"]
    reg["home"], reg["away"] = reg["away"], reg["home"]
    return bad


def build_trees(base: pathlib.Path, now: datetime) -> dict[str, Any]:
    """Write three exchange trees: delivery 1, delivery 2 (revision + re-import), and a conflict delivery."""

    s = scenario(now)
    f, r = s["forecasts"], s["results"]
    d1, d2, d3 = base / "delivery-1", base / "delivery-2", base / "delivery-3-conflict"
    first = [write_object(d1, f[k], "forecast.json") for k in ("REG_FINAL", "OT_FINAL", "SO_FINAL", "FUTURE_V1")]
    res = [write_object(d1, r[k], "result.json") for k in ("REG_FINAL", "OT_FINAL", "SO_FINAL")]
    write_pointer(d1, write_manifest(d1, c.FORECAST_MANIFEST_SCHEMA, first, now - timedelta(hours=20)),
                  write_manifest(d1, c.RESULT_MANIFEST_SCHEMA, res, now - timedelta(hours=1)))
    # Delivery 2 re-sends every delivery-1 forecast (identical: duplicates) plus the revision.
    second = [write_object(d2, f[k], "forecast.json")
              for k in ("REG_FINAL", "OT_FINAL", "SO_FINAL", "FUTURE_V1", "FUTURE_V2")]
    write_pointer(d2, write_manifest(d2, c.FORECAST_MANIFEST_SCHEMA, second, now - timedelta(hours=6)), None)
    bad = conflicting(f["FUTURE_V1"])
    third = [write_object(d3, bad, "forecast.json")]
    write_pointer(d3, write_manifest(d3, c.FORECAST_MANIFEST_SCHEMA, third, now - timedelta(hours=5)), None)
    return {"scenario": s, "trees": [d1, d2, d3]}


def dump(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True)


__all__ = ["build_trees", "conflicting", "forecast", "game_block", "result", "scenario", "write_manifest",
           "write_object", "write_pointer"]
