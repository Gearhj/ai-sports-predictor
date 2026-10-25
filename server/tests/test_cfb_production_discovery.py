"""The staged CFB slate-discovery verifier refuses anything it cannot prove.

Synthetic fixtures only: packages are built in a temp directory with the real
schemas, never copied from or written to the producer's exchange.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.cfb import production_discovery as pd

KICK = datetime(2026, 9, 26, 19, 30, tzinfo=UTC)
NOW = KICK - timedelta(hours=10)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_addressed(parent: pathlib.Path, name: str, raw: bytes) -> pathlib.Path:
    folder = parent / _sha(raw)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(raw)
    return folder / name


def _ref(path: pathlib.Path) -> dict:
    raw = path.read_bytes()
    return {"path": str(path), "sha256": _sha(raw), "bytes": len(raw)}


def _package(tmp: pathlib.Path, game_id: int, home_id: int, away_id: int) -> tuple[pathlib.Path, dict]:
    staging = tmp / "staging" / str(game_id)
    staging.mkdir(parents=True)
    rng = __import__("random").Random(game_id)
    homes, aways = [], []
    while len(homes) < pd.DRAW_COUNT:
        h, a = rng.randint(0, 60), rng.randint(0, 60)
        if h != a:
            homes.append(h)
            aways.append(a)
    draws = pl.DataFrame({"home_score": homes, "away_score": aways,
                          "total": [h + a for h, a in zip(homes, aways)],
                          "home_margin": [h - a for h, a in zip(homes, aways)]})
    draws.write_parquet(staging / "baseline-joint-draws.parquet")
    rows = []
    for metric, series in (("home_score", homes), ("away_score", aways),
                           ("total", draws["total"].to_list()), ("home_margin", draws["home_margin"].to_list())):
        counts: dict[int, int] = {}
        for v in series:
            counts[v] = counts.get(v, 0) + 1
        rows += [{"metric": metric, "value": v, "count": c, "draw_count": pd.DRAW_COUNT} for v, c in sorted(counts.items())]
    pl.DataFrame(rows).write_parquet(staging / "baseline-pmfs.parquet")
    forecast_id = f"cfb-gen1:{game_id}"
    forecast = {"forecastId": forecast_id, "createdAt": (NOW - timedelta(minutes=5)).isoformat(),
                "sourceCutoff": (NOW - timedelta(minutes=8)).isoformat(),
                "game": {"game_id": game_id, "home_id": home_id, "away_id": away_id, "home_team": "H", "away_team": "A",
                         "scheduled_at": KICK.isoformat()},
                "baseline": {"drawCount": pd.DRAW_COUNT,
                             "jointHomeWinProbability": sum(1 for h, a in zip(homes, aways) if h > a) / pd.DRAW_COUNT}}
    (staging / "forecast.json").write_text(json.dumps(forecast), encoding="utf-8")
    files = []
    for name in ("forecast.json", "baseline-joint-draws.parquet", "baseline-pmfs.parquet"):
        raw = (staging / name).read_bytes()
        files.append({"path": name, "physicalSha256": _sha(raw), "bytes": len(raw)})
    index = json.dumps({"schemaVersion": "CFB_GEN1_PRIVATE_RESEARCH_PACKAGE_INDEX_V1", "researchOnly": True,
                        "aispAuthority": False, "wageringAuthority": False, "forecastId": forecast_id,
                        "gameId": game_id, "drawCount": pd.DRAW_COUNT, "configurationIdentity": pd.CONFIGURATION,
                        "files": files}, sort_keys=True).encode()
    root = tmp / "forecasts" / _sha(index)
    root.mkdir(parents=True)
    for name in ("forecast.json", "baseline-joint-draws.parquet", "baseline-pmfs.parquet"):
        (root / name).write_bytes((staging / name).read_bytes())
    (root / "index.json").write_bytes(index)
    committed = (NOW - timedelta(minutes=2)).isoformat()
    entry = {"gameId": str(game_id), "forecastId": forecast_id, "packageAddress": root.name,
             "packageDirectory": str(root), "index": _ref(root / "index.json"), "revisionKey": f"rev-{game_id}",
             "committedAtUtc": committed, "currentlyEligible": True,
             "eligibilityValidUntilUtc": (NOW + timedelta(minutes=15)).isoformat()}
    receipt = json.dumps({k: entry[k] for k in ("gameId", "forecastId", "packageAddress", "revisionKey", "committedAtUtc")}).encode()
    entry["publicationReceipt"] = _ref(_write_addressed(tmp / "receipts", "receipt.json", receipt))
    return root, entry


@pytest.fixture
def slate(tmp_path):
    release = _write_addressed(tmp_path / "releases", "release.json", b'{"release": 1}')
    inputs = _write_addressed(tmp_path / "inputs", "input.json", b'{"schedule": null}')
    _, first = _package(tmp_path, 401, home_id=10, away_id=20)
    _, second = _package(tmp_path, 402, home_id=30, away_id=40)

    def publish(entries):
        manifest = json.dumps({"schemaVersion": pd.MANIFEST_SCHEMA, "createdAtUtc": NOW.isoformat(),
                               "release": _ref(release), "inputVersion": _ref(inputs), "predecessor": None,
                               "packages": entries, "games": [{"gameId": e["gameId"], "disposition": "PUBLISHED_NEW_PROSPECTIVE"} for e in entries]}).encode()
        path = _write_addressed(tmp_path / "versions", "index.json", manifest)
        pointer = tmp_path / "current.json"
        pointer.write_text(json.dumps({"schemaVersion": pd.POINTER_SCHEMA, "manifest": _ref(path)}))
        return pointer

    return SimpleNamespace(tmp=tmp_path, entries=[first, second], publish=publish, release=_ref(release),
                           orientation={"401": (10, 20), "402": (30, 40)})


def test_a_clean_slate_verifies_every_package(slate):
    report = pd.verify_discovery(slate.publish(slate.entries), now=NOW, release_pin=slate.release["sha256"],
                                 orientation=slate.orientation, orientation_source="TEST")
    assert report["verified"] == 2 and report["current"] == 2 and not report["rejected"]
    assert {p["orientation"] for p in report["packages"]} == {"CONFIRMED"}


def test_a_reversed_home_and_away_is_refused(slate):
    report = pd.verify_discovery(slate.publish(slate.entries), now=NOW, orientation={"401": (20, 10), "402": (30, 40)})
    bad = next(p for p in report["packages"] if p["game_id"] == "401")
    assert "ORIENTATION_MISMATCH" in bad["problems"] and not bad["verified"]
    assert report["verified"] == 1, "one bad game never hides a good one"


def test_a_tampered_member_is_refused_before_it_is_read(slate):
    root = pathlib.Path(slate.entries[0]["packageDirectory"])
    (root / "baseline-pmfs.parquet").write_bytes(b"not the published bytes")
    report = pd.verify_discovery(slate.publish(slate.entries), now=NOW, orientation=slate.orientation)
    bad = next(p for p in report["packages"] if p["game_id"] == "401")
    assert not bad["verified"] and bad["problems"][0].startswith("UNVERIFIABLE")


def test_an_expired_lease_is_historical_not_current(slate):
    report = pd.verify_discovery(slate.publish(slate.entries), now=NOW + timedelta(minutes=20),
                                 orientation=slate.orientation)
    assert report["current"] == 0 and {p["lease"] for p in report["packages"]} == {"EXPIRED_LEASE"}


def test_a_receipt_that_names_another_clock_is_refused(slate):
    entry = dict(slate.entries[0])
    entry["committedAtUtc"] = (NOW - timedelta(minutes=1)).isoformat()   # receipt still says -2 min
    report = pd.verify_discovery(slate.publish([entry]), now=NOW, orientation=slate.orientation)
    assert "RECEIPT_COMMITTEDATUTC_MISMATCH" in report["packages"][0]["problems"]


def test_a_manifest_from_another_release_or_a_bad_pointer_refuses_the_whole_slate(slate):
    pointer = slate.publish(slate.entries)
    with pytest.raises(pd.DiscoveryRefused):
        pd.verify_discovery(pointer, now=NOW, release_pin="0" * 64, orientation=slate.orientation)
    doc = json.loads(pointer.read_text())
    doc["manifest"]["sha256"] = "f" * 64
    pointer.write_text(json.dumps(doc))
    with pytest.raises(pd.DiscoveryRefused):
        pd.verify_discovery(pointer, now=NOW, orientation=slate.orientation)


def test_production_status_is_not_received_until_pinned_and_staged_until_activated(tmp_path):
    assert pd.production_status(SimpleNamespace()).status == pd.NOT_RECEIVED
    release = _write_addressed(tmp_path / "releases", "release.json", b"{}")
    member = b'{"ready": false}'
    index = json.dumps({"schema": pd.HANDOFF_SCHEMA, "consumerActivated": False, "release": _ref(release),
                        "members": [{"relativePath": "READINESS.json", "sha256": _sha(member), "bytes": len(member)}]}).encode()
    folder = tmp_path / "handoffs" / _sha(index)
    folder.mkdir(parents=True)
    (folder / "index.json").write_bytes(index)
    (folder / "READINESS.json").write_bytes(member)
    settings = SimpleNamespace(cfb_production_handoff_path=str(folder / "index.json"),
                               cfb_production_handoff_sha256=folder.name, cfb_production_consumer_enabled=True)
    verdict = pd.production_status(settings)
    assert verdict.status == pd.STAGED and verdict.as_dict()["production_label_allowed"] is False
    settings.cfb_production_handoff_sha256 = "0" * 64
    assert pd.production_status(settings).status == pd.REJECTED
    (folder / "READINESS.json").write_bytes(b"tampered")
    settings.cfb_production_handoff_sha256 = folder.name
    assert pd.production_status(settings).status == pd.REJECTED
