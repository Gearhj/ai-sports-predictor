"""Post-deploy check for the Monday Morning QB endpoint (read-only: one HTTP GET, no database, no writes).

    python server/scripts/mmqb_postdeploy_check.py                       # http://127.0.0.1:8010, NFL 2026 week 3
    python server/scripts/mmqb_postdeploy_check.py --base-url http://127.0.0.1:8013 \
        --expect-generator bfdb585 --expect-content-sha256 <sha> [--expect-positions 15]

Asserts, for the requested week:
  * game_evidence_status == VERIFIED, and its file sha256 equals the vendored manifest's;
  * exactly --expect-dives deep dives (default 3), each with an OBSERVED finalized play-by-play block;
  * every REQUIRED decision id listed in the vendored manifest (Week 3: the 14 reviewed Sunday positions) is present
    as a position. Additional positions in the week window (e.g. Monday night) are allowed and reported with their
    status. --expect-positions N optionally demands an exact total instead;
  * the payload carries a data cutoff, pg snapshot and content hash; if given, the content hash and generator
    version match the expected identity.
Exits 0 when every check passes, 1 otherwise (each failure printed). The content hash legitimately changes when the
week's data changes (e.g. the Monday-night game finalises), so pin it only for a known snapshot.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

MANIFEST = Path(__file__).resolve().parents[1] / "app" / "data" / "mmqb" / "manifest.json"


def load_manifest_entry(league: str, season: int, week: int, manifest: Path = MANIFEST) -> dict[str, Any]:
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    key = f"{league}|{season}|{week}"
    return {"artifact": (doc.get("artifacts") or {}).get(key),
            "required": (doc.get("required_positions") or {}).get(key)}


def evaluate(body: dict[str, Any], *, artifact: dict[str, Any] | None, required: dict[str, Any] | None,
             expect_dives: int = 3, expect_positions: int | None = None, expect_content_sha256: str | None = None,
             expect_generator: str | None = None) -> list[tuple[bool, str]]:
    """Every check as (passed, message). Pure: the caller fetched ``body``."""

    out: list[tuple[bool, str]] = []
    st = body.get("game_evidence_status") or {}
    out.append((st.get("status") == "VERIFIED",
                f"game_evidence_status == VERIFIED (got {st.get('status')}: {st.get('reason')})"))
    out.append((bool(artifact) and st.get("file_sha256") == artifact.get("file_sha256"),
                "evidence file sha256 matches the vendored manifest"
                + (f" ({artifact['file_sha256'][:16]}…)" if artifact else " (no manifest entry)")))
    dives = body.get("deep_dives") or []
    out.append((len(dives) == expect_dives, f"{expect_dives} deep dives (got {len(dives)})"))
    for d in dives:
        o = d.get("observed") or {}
        out.append((o.get("label") == "OBSERVED" and o.get("play_by_play_status") == "FINAL_PLAY_BY_PLAY_RETAINED"
                    and bool(o.get("scoring_sequence")), f"{d.get('matchup')}: OBSERVED finalized play-by-play block"))
    slate = body.get("slate") or []
    present = {s.get("decision_id") for s in slate}
    needed = list((required or {}).get("decision_ids") or [])
    missing = [x for x in needed if x not in present]
    out.append((bool(needed) and not missing,
                f"all {len(needed)} required positions present ({(required or {}).get('scope', 'no required list')})"
                + (f"; MISSING {', '.join(m[:8] for m in missing)}" if missing else "")))
    extra = [s for s in slate if s.get("decision_id") not in set(needed)]
    for s in extra:
        out.append((True, f"additional week position (allowed): {s.get('matchup')} {s.get('kickoff_z')} "
                          f"{s.get('selected_team')} — {s.get('result') or s.get('status')}"))
    if expect_positions is not None:
        out.append((len(slate) == expect_positions, f"exactly {expect_positions} positions (got {len(slate)})"))
    snap = body.get("snapshot") or {}
    cutoff = (body.get("data_cutoff") or {}).get("db_clock_utc")
    out.append((bool(cutoff and snap.get("pg_snapshot") and snap.get("content_sha256")),
                f"identity present (cutoff {cutoff}, snapshot {snap.get('pg_snapshot')}, "
                f"sha256 {str(snap.get('content_sha256'))[:16]}…)"))
    if expect_content_sha256:
        out.append((snap.get("content_sha256") == expect_content_sha256, "content sha256 equals the expected identity"))
    if expect_generator:
        out.append((str(snap.get("generator_commit") or "").startswith(expect_generator),
                    f"generator {snap.get('generator_commit')} starts with {expect_generator}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://127.0.0.1:8010")
    ap.add_argument("--league", default="NFL")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=3)
    ap.add_argument("--expect-positions", type=int, help="optional exact total (default: required ids only)")
    ap.add_argument("--expect-dives", type=int, default=3)
    ap.add_argument("--expect-content-sha256")
    ap.add_argument("--expect-generator", help="commit (prefix) the endpoint must report")
    ap.add_argument("--timeout", type=float, default=90.0)
    a = ap.parse_args()

    url = f"{a.base_url.rstrip('/')}/api/v1/research/mmqb?league={a.league}&season={a.season}&week={a.week}"
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=a.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL request {url}: {exc}")
        return 1
    try:
        entry = load_manifest_entry(a.league, a.season, a.week)
    except (OSError, ValueError) as exc:
        print(f"FAIL vendored manifest not readable: {exc}")
        return 1
    results = evaluate(body, artifact=entry["artifact"], required=entry["required"], expect_dives=a.expect_dives,
                       expect_positions=a.expect_positions, expect_content_sha256=a.expect_content_sha256,
                       expect_generator=a.expect_generator)
    for ok, message in results:
        print(("PASS " if ok else "FAIL ") + message)
    failures = sum(1 for ok, _ in results if not ok)
    print("RESULT", "OK" if not failures else f"{failures} FAILURE(S)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
