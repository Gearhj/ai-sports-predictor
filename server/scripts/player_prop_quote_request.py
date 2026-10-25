"""Plan (default) or execute ONE bounded player-prop quote request.

    python -m scripts.player_prop_quote_request --game-id <uuid>
    python -m scripts.player_prop_quote_request --game-id <uuid> --execute

Without ``--execute`` nothing is written and nothing is called: the command
prints the exact request, its predicted credits and every budget check.
``--execute`` still refuses unless PLAYER_PROP_LIVE_ENABLED is true, the
player-prop research lane has an allocation, and every check passes. One game,
one market (default batter_hits), region us. The API key is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.player_props.collector import PlanRefused, execute, plan  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="player_prop_quote_request")
    parser.add_argument("--game-id", required=True, help="Canonical MLB game id.")
    parser.add_argument("--market", default="batter_hits", help="Exactly one supported player-prop market.")
    parser.add_argument("--execute", action="store_true", help="Make the single live request (flag-gated).")
    args = parser.parse_args(argv)

    settings = get_settings()
    with SessionLocal() as session:
        try:
            if args.execute:
                payload = asyncio.run(execute(session, settings, args.game_id, markets=[args.market], confirm=True))
            else:
                payload = plan(session, settings, args.game_id, markets=[args.market])
                session.rollback()
        except PlanRefused as exc:
            print(json.dumps({"refused": str(exc), "provider_calls_made": 0}, indent=2))
            return 2
    print(json.dumps({"command": "player_prop_quote_request", **payload}, indent=2, default=str))
    return 0 if payload.get("mode") in ("DRY_RUN", "EXECUTED") else 3


if __name__ == "__main__":
    raise SystemExit(main())
