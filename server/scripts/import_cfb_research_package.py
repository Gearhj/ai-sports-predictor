"""Import one byte-verified College Football research package, optionally admitting its game.

    python -m scripts.import_cfb_research_package --package <dir> --dry-run
    python -m scripts.import_cfb_research_package --package <dir> --admit-game

``--dry-run`` performs the whole import and admission inside a transaction and
rolls it back, reporting what would have been written. The package directory
must be named by its physical index SHA-256 (verified by the importer). No
provider is called, no model is run, and no decision, position or eligibility
record is created.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.cfb.game_binding import admit_game  # noqa: E402
from app.cfb.research_import import import_package  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import CfbResearchForecast  # noqa: E402
from sqlalchemy import select  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="import_cfb_research_package")
    parser.add_argument("--package", required=True, type=pathlib.Path)
    parser.add_argument("--admit-game", action="store_true",
                        help="Also admit the forecast's CFBD game into the canonical layer.")
    parser.add_argument("--dry-run", action="store_true", help="Roll back instead of committing.")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        result = {"import": import_package(session, args.package.resolve())}
        if args.admit_game:
            row = session.scalar(select(CfbResearchForecast).where(
                CfbResearchForecast.forecast_id == result["import"]["forecast_id"]))
            result["admission"] = admit_game(session, row)
        if args.dry_run:
            session.rollback()
            result["committed"] = False
        else:
            session.commit()
            result["committed"] = True
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
