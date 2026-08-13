from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

import polars as pl


@dataclass(frozen=True)
class ParquetSnapshot:
    path: str
    checksum: str
    row_count: int
    created_at: datetime
    schema_version: str


class ParquetSnapshotWriter:
    """Immutable, partitioned Parquet snapshots for curated/features storage."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(
        self,
        dataset: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        event_date: datetime | None = None,
        schema_version: str = "1",
    ) -> ParquetSnapshot:
        materialized = list(rows)
        if not materialized:
            raise ValueError("A Parquet snapshot must contain at least one row")
        created_at = datetime.now(UTC)
        partition = event_date or created_at
        directory = self.root / dataset / f"year={partition:%Y}" / f"month={partition:%m}" / f"day={partition:%d}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"snapshot_{created_at:%H%M%S_%f}_{uuid4().hex[:10]}_v{schema_version}.parquet"
        if path.exists():
            raise FileExistsError(path)
        frame = pl.DataFrame(materialized)
        frame.write_parquet(path, compression="zstd", statistics=True)
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        return ParquetSnapshot(str(path), checksum, frame.height, created_at, schema_version)
