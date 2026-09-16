"""Import published SHOW_PREVIEW packages and write the consumer acknowledgement.

Read-only against the Model Exchange; writes only AISP's own preview rows and
one acknowledgement file under ``outputs/show-preview-import-v1/``. Makes no
provider request and generates no forecast.

    python scripts/import_show_preview_packages.py --index <sha256> [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.show_preview.exchange import acknowledgement, import_published_previews, verify_index  # noqa: E402

DEFAULT_ROOT = Path.home() / "asp-model-exchange" / "show-preview-forecasts-v1"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "outputs" / "show-preview-import-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, help="discovery index SHA-256")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="exchange directory")
    parser.add_argument("--dry-run", action="store_true", help="verify only; store nothing")
    args = parser.parse_args()

    settings = get_settings()
    root = Path(args.root)
    now = datetime.now(UTC)

    index, verified = verify_index(root, args.index)
    print(f"index {args.index[:16]}… verified · {len(verified)} packages · producer created {index.get('created_utc')}")
    for package in verified:
        print(f"  verified {package.sport:4} {package.slate_date} {package.source_game_ref:20} {package.forecast_sha256[:12]}…")
    if args.dry_run:
        print("\ndry run: nothing stored")
        return 0

    if not settings.show_preview_enabled:
        print("\nSHOW_PREVIEW_ENABLED is false; nothing imported (fail closed)")
        return 2

    with SessionLocal() as session:
        outcome = import_published_previews(session, settings, root=root, index_sha256=args.index, now=now)
        session.commit()
    print(f"\nimported {len(outcome.imported)} · already held {len(outcome.already_held)} · rejected {len(outcome.rejected)}")
    for rejection in outcome.rejected:
        print(f"  REJECTED {rejection['source_game_ref']}: {rejection['reason']}")

    ack = acknowledgement(outcome, index, now=now)
    body = json.dumps(ack, indent=2, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"consumer-acknowledgement-{digest}.json"
    path.write_bytes(body)
    latest = OUTPUT_DIR / "latest.json"
    latest.write_bytes(body)
    print(f"acknowledgement sha256 {digest}\nwritten {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
