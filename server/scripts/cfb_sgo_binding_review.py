"""Review and pin SportsGameOdds College Football identities from RETAINED responses. No request is made.

    python -m scripts.cfb_sgo_binding_review review --archive <raw-archive .json> [--out <dir>]
    python -m scripts.cfb_sgo_binding_review apply  --artifact <review .json> --approve EV:home,EV:away \
        --reviewer "<name>" [--map <existing team map>] --out <new team map .json>
    python -m scripts.cfb_sgo_binding_review rebind --archive <raw-archive .json> --game <CFBD id> [--commit]

``review`` reads the exact retained bytes (verified against their recorded
sha256) and writes a content-addressed review artifact: per event, the CFBD
game it can only be (start within five minutes, unique both ways), both team-id
pairs with orientation, and every reason something was NOT proposed.

``apply`` writes the reviewed team map (``AISP_CFB_SGO_TEAM_MAP_V1``) from the
proposal ids a named reviewer approves. It never approves anything itself. The
owner then sets ``CFB_SGO_LEAGUE_ID`` to the artifact's ``league.pinnable`` and
``CFB_SGO_TEAM_MAP_PATH`` to the written map.

``rebind`` persists the quotes of a retained response once its identities are
pinned -- no request, the book's own clocks, and ``available_as_of`` = now
(never backdated). Without ``--commit`` it rolls back and reports.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.cfb import sgo_binding, sgo_price  # noqa: E402
from app.core.config import get_settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfb_sgo_binding_review")
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review")
    review.add_argument("--archive", required=True, type=pathlib.Path)
    review.add_argument("--out", type=pathlib.Path)
    apply = sub.add_parser("apply")
    apply.add_argument("--artifact", required=True, type=pathlib.Path)
    apply.add_argument("--approve", required=True, help="Comma-separated proposal ids, e.g. EVENT:home,EVENT:away")
    apply.add_argument("--reviewer", required=True)
    apply.add_argument("--map", type=pathlib.Path)
    apply.add_argument("--out", required=True, type=pathlib.Path)
    rebind = sub.add_parser("rebind")
    rebind.add_argument("--archive", required=True, type=pathlib.Path)
    rebind.add_argument("--game", required=True, help="CFBD game id")
    rebind.add_argument("--commit", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "review":
        body, meta = sgo_binding.body_from_archive(args.archive)
        artifact = sgo_binding.review_events(
            json.loads(body), schedule=sgo_binding.load_schedule(settings), binding=sgo_price.load_binding(settings),
            league_candidate=str((meta.get("request_filters") or {}).get("leagueID") or "") or None,
            source={**meta, "archive": args.archive.name})
        directory = args.out or sgo_binding.review_directory(settings)
        if directory is None:
            raise SystemExit("no raw data root is configured; pass --out")
        path = sgo_binding.write_artifact(artifact, directory)
        print(json.dumps({"artifact": path, "league": artifact["league"], "counts": artifact["counts"],
                          "proposals": [p["proposal_id"] for p in artifact["proposals"]]}, indent=1))
        return 0
    if args.command == "apply":
        artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
        existing = json.loads(args.map.read_text(encoding="utf-8")) if args.map else None
        document = sgo_binding.apply_review(artifact, approve=[p for p in args.approve.split(",") if p],
                                            reviewer=args.reviewer, existing=existing)
        if args.out.exists():
            raise SystemExit(f"{args.out} exists; write a new map file rather than overwrite one")
        args.out.write_text(json.dumps(document, indent=1, sort_keys=True), encoding="utf-8")
        print(json.dumps({"map": str(args.out), "league": document["leagueID"],
                          "entries": len(document["entries"])}, indent=1))
        return 0
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        result = sgo_price.persist_from_archive(session, settings, args.archive, source_game_id=args.game)
        if args.commit:
            session.commit()
        else:
            session.rollback()
        print(json.dumps({**result, "committed": bool(args.commit)}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
