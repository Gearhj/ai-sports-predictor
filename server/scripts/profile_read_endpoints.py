"""Query count and database time per read endpoint, in-process.

Uses Starlette's TestClient WITHOUT its context manager, so the application
lifespan (and therefore the scheduler) never starts: nothing is collected,
imported or evaluated. Provider-ledger rows are counted before and after to
prove the reads make no provider request.

Usage: python scripts/profile_read_endpoints.py /today "/games/slate?date=2026-09-15" ...
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import event, text  # noqa: E402

from app.db.session import SessionLocal, engine  # noqa: E402

stats = {"count": 0, "ms": 0.0}
shapes: Counter[str] = Counter()


@event.listens_for(engine, "before_cursor_execute")
def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    conn.info.setdefault("t0", []).append(time.perf_counter())


@event.listens_for(engine, "after_cursor_execute")
def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    stats["count"] += 1
    stats["ms"] += (time.perf_counter() - conn.info["t0"].pop()) * 1000
    shapes[" ".join(statement.split())[:110]] += 1


def ledger_rows() -> int:
    with SessionLocal() as session:
        return int(session.execute(text("select count(*) from provider_request_ledger")).scalar())


def main() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)  # no `with`: lifespan (scheduler) does not start
    before = ledger_rows()
    for path in sys.argv[1:]:
        stats.update(count=0, ms=0.0)
        shapes.clear()
        started = time.perf_counter()
        response = client.get("/api/v1" + path)
        total = (time.perf_counter() - started) * 1000
        repeated = [(n, s) for s, n in shapes.most_common(3) if n > 5]
        print(f"{total:7.0f} ms total | {stats['count']:5d} queries | {stats['ms']:7.0f} ms db | "
              f"{response.status_code} | {path}")
        for n, shape in repeated:
            print(f"          x{n}: {shape}")
    print("provider_request_ledger rows added by these reads:", ledger_rows() - before)


if __name__ == "__main__":
    main()
