"""Postgres VARCHAR(n) lengths, enforced in the SQLite test suite for the decision and position tables.

Production runs Postgres, which REFUSES a string longer than its column's declared length
(``StringDataRightTruncation``). The test suite runs SQLite, which stores it silently. That gap cost
the 2026-10-17 00:30-00:40Z NHL decisions: ``DECISION_PERIOD`` was 41 characters for a VARCHAR(40)
column, every NHL decision insert failed in production, and every test passed.

``install()`` (called once from ``tests/conftest.py``) listens to every ORM flush in the suite. For each
new or changed row of a GUARDED table it measures every length-limited string column against the
model's declared length and records each violation; the flush then raises, as Postgres would, and
the autouse fixture in ``conftest.py`` fails the test even when the code under test swallowed the
error. The guarded tables are the ones every sport's paper decision path writes (MLB, NFL, CFB, NHL,
NBA): the decision row, its position, the experiment that names the position's strategy, and the
audit row that carries the MLB funding receipt (AISP_SHARED_100_PRIORITY_MLB_NHL_V1).

``sqlalchemy.Enum`` columns are skipped: SQLAlchemy validates their members itself, and their length is
derived from the longest member.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

GUARDED_TABLES = frozenset({
    "shadow_experiment_decisions",
    "official_asp_positions",
    "shadow_experiments",
    "audit_events",
    # Settlement rows and the corrections recorded beside them
    # (AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1 writes both).
    "position_results",
    "position_settlement_adjustments",
    "job_runs",
})

#: Every violation seen since the last ``drain()``: (table, column, length, value).
VIOLATIONS: list[tuple[str, str, int, str]] = []


class PostgresLengthViolation(AssertionError):
    """A string the ORM was about to write is longer than its Postgres VARCHAR(n) column."""


def limited_columns(model: Any) -> dict[str, tuple[str, int]]:
    """``{attribute key: (column name, declared length)}`` for every length-limited, non-Enum string column."""

    mapper = inspect(model)
    out: dict[str, tuple[str, int]] = {}
    for column in model.__table__.columns:
        length = getattr(column.type, "length", None)
        if not length or isinstance(column.type, SAEnum):
            continue
        out[mapper.get_property_by_column(column).key] = (column.name, int(length))
    return out


def check_row(obj: Any) -> list[tuple[str, str, int, str]]:
    table = getattr(obj, "__table__", None)
    if table is None or table.name not in GUARDED_TABLES:
        return []
    found = []
    for key, (column, length) in limited_columns(type(obj)).items():
        value = getattr(obj, key, None)
        if isinstance(value, str) and len(value) > length:
            found.append((table.name, column, length, value))
    return found


def _before_flush(session: Session, _context: Any, _instances: Any) -> None:
    found = []
    for obj in list(session.new) + list(session.dirty):
        found.extend(check_row(obj))
    if found:
        VIOLATIONS.extend(found)
        raise PostgresLengthViolation(
            "Postgres would refuse this flush (VARCHAR too long): "
            + "; ".join(f"{t}.{c} VARCHAR({n}) <- {len(v)} chars {v!r}" for t, c, n, v in found))


def install() -> None:
    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)


def installed() -> bool:
    return event.contains(Session, "before_flush", _before_flush)


def drain() -> list[tuple[str, str, int, str]]:
    out = list(VIOLATIONS)
    VIOLATIONS.clear()
    return out
