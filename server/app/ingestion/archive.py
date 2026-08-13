from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RawArchiveRecord:
    source: str
    ingestion_timestamp: datetime
    source_event_timestamp: datetime | None
    checksum: str
    status: str
    record_count: int
    raw_file_reference: str
    schema_version: str


class RawArchive:
    """Append-only raw JSON archive partitioned by source and UTC date."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(
        self,
        source: str,
        payload: dict[str, Any] | list[Any],
        *,
        source_event_timestamp: datetime | None = None,
        status: str = "success",
        schema_version: str = "1",
    ) -> RawArchiveRecord:
        timestamp = datetime.now(UTC)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        checksum = hashlib.sha256(canonical).hexdigest()
        directory = self.root / source.lower() / f"{timestamp:%Y}" / f"{timestamp:%m}" / f"{timestamp:%d}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{timestamp:%H%M%S_%f}_{uuid4().hex[:10]}.json"
        document = {
            "metadata": {
                "source": source,
                "ingestion_timestamp": timestamp.isoformat(),
                "source_event_timestamp": source_event_timestamp.isoformat() if source_event_timestamp else None,
                "checksum": checksum,
                "status": status,
                "record_count": len(payload) if isinstance(payload, list) else 1,
                "schema_version": schema_version,
            },
            "payload": payload,
        }
        # Exclusive creation and a unique filename make snapshots immutable.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        return RawArchiveRecord(
            source=source,
            ingestion_timestamp=timestamp,
            source_event_timestamp=source_event_timestamp,
            checksum=checksum,
            status=status,
            record_count=len(payload) if isinstance(payload, list) else 1,
            raw_file_reference=str(path),
            schema_version=schema_version,
        )
