"""A daily source whose answer changes, captured twice.

The September 12 slate produced this exactly. A canonical pass at 19:39 EDT
captured the schedule with nine games final. A later pass, after more games
finished, captured a legitimately different response for the same request and
could not store it: the archive addressed a capture by REQUEST alone, so two
different observations mapped onto one immutable object and the second was
refused. No governed final could then be written for the later finishers.

Waiting for the day to end would not have fixed it. An official scoring
correction lands days later and reproduces the same collision.

Nothing here relaxes the archive's prohibition: captured bytes are never
rewritten, no original is deleted, no timestamp is reused, and an object whose
stored metadata contradicts its request still fails closed.
"""
from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.ingestion.archive import RawArchive, RawArchiveConflictError

PARTITION = date(2026, 9, 12)
FINGERPRINT = "f" * 64
NINE_FINAL = {"dates": [{"games": [{"gamePk": n, "status": "Final"} for n in range(9)]}]}
FIFTEEN_FINAL = {"dates": [{"games": [{"gamePk": n, "status": "Final"} for n in range(15)]}]}


def _write(archive: RawArchive, payload: dict) -> object:
    return archive.write_historical(
        "MLB_STATS_API", payload,
        partition_date=PARTITION, request_fingerprint=FINGERPRINT,
        schema_version="mlb_statsapi_historical_schedule_range_v1",
        checkpoint_id="checkpoint-1", run_id="run-1",
    )


def test_a_changed_daily_response_becomes_a_new_revision(tmp_path: Path) -> None:
    archive = RawArchive(tmp_path / "raw")

    first = _write(archive, NINE_FINAL)
    second = _write(archive, FIFTEEN_FINAL)

    assert (first.revision, second.revision) == (1, 2)
    assert second.supersedes_payload_checksum == first.payload_checksum
    assert first.payload_checksum != second.payload_checksum
    assert first.raw_file_reference != second.raw_file_reference


def test_the_first_capture_is_never_overwritten_or_removed(tmp_path: Path) -> None:
    """The nine-final observation stays exactly as it was captured."""

    archive = RawArchive(tmp_path / "raw")
    first = _write(archive, NINE_FINAL)
    original_bytes = Path(first.raw_file_reference).read_bytes()
    original_retrieved = first.ingestion_timestamp

    _write(archive, FIFTEEN_FINAL)

    assert Path(first.raw_file_reference).exists()
    assert Path(first.raw_file_reference).read_bytes() == original_bytes
    with gzip.open(first.raw_file_reference, "rt", encoding="utf-8") as stream:
        metadata = json.load(stream)["metadata"]
    assert metadata["checksum"] == first.payload_checksum
    assert metadata["ingestion_timestamp"] == original_retrieved.isoformat()


def test_replaying_the_same_capture_is_idempotent(tmp_path: Path) -> None:
    """Re-running a pass over unchanged bytes creates nothing."""

    archive = RawArchive(tmp_path / "raw")
    first = _write(archive, NINE_FINAL)
    second = _write(archive, FIFTEEN_FINAL)

    replay_first = _write(archive, NINE_FINAL)
    replay_second = _write(archive, FIFTEEN_FINAL)

    assert replay_first.idempotent_reuse is True
    assert replay_second.idempotent_reuse is True
    assert replay_first.raw_file_reference == first.raw_file_reference
    assert replay_second.raw_file_reference == second.raw_file_reference
    assert replay_first.revision == 1 and replay_second.revision == 2
    # Two observations, four writes, and still exactly two stored objects.
    captures = sorted(Path(first.raw_file_reference).parent.glob("*.json.gz"))
    assert len(captures) == 2


def test_the_lineage_records_every_observation_in_order(tmp_path: Path) -> None:
    archive = RawArchive(tmp_path / "raw")
    first = _write(archive, NINE_FINAL)
    second = _write(archive, FIFTEEN_FINAL)

    revisions = archive.historical_revisions(
        "MLB_STATS_API", partition_date=PARTITION, request_fingerprint=FINGERPRINT)

    assert [row["revision"] for row in revisions] == [1, 2]
    assert [row["payload_checksum"] for row in revisions] == [
        first.payload_checksum, second.payload_checksum]
    assert revisions[0]["supersedes_payload_checksum"] is None
    assert revisions[1]["supersedes_payload_checksum"] == first.payload_checksum
    # Each revision names the exact file a derived final can be traced back to.
    assert revisions[1]["raw_file_reference"] == second.raw_file_reference
    for row in revisions:
        assert row["retrieved_at"] and row["request_fingerprint"] == FINGERPRINT


def test_the_pointer_selects_a_version_and_holds_no_evidence(tmp_path: Path) -> None:
    """Losing the pointer must not lose or alter a capture."""

    archive = RawArchive(tmp_path / "raw")
    first = _write(archive, NINE_FINAL)
    second = _write(archive, FIFTEEN_FINAL)
    ledger = next(Path(first.raw_file_reference).parent.glob("*.revisions.json"))

    document = json.loads(ledger.read_text(encoding="utf-8"))
    assert document["current_payload_checksum"] == second.payload_checksum

    ledger.write_text("{ this is not json", encoding="utf-8")
    replay = _write(archive, FIFTEEN_FINAL)

    assert replay.idempotent_reuse is True
    assert replay.raw_file_reference == second.raw_file_reference
    assert Path(first.raw_file_reference).exists()


def test_a_stored_capture_that_contradicts_its_request_still_fails_closed(
    tmp_path: Path,
) -> None:
    archive = RawArchive(tmp_path / "raw")
    first = _write(archive, NINE_FINAL)

    path = Path(first.raw_file_reference)
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        document = json.load(stream)
    document["metadata"]["request_fingerprint"] = "0" * 64
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(document, stream)

    with pytest.raises(RawArchiveConflictError, match="contradicts the request"):
        _write(archive, NINE_FINAL)


def test_a_pre_revision_capture_is_adopted_as_revision_one(tmp_path: Path) -> None:
    """Archives written before this change keep their bytes and their place.

    The legacy path carries no payload checksum in its name, so it is adopted
    into the lineage using its OWN recorded retrieval time rather than a fresh
    clock, and the next observation supersedes it.
    """

    archive = RawArchive(tmp_path / "raw")
    first = _write(archive, NINE_FINAL)
    legacy = Path(first.raw_file_reference)
    # Re-create the pre-revision filename from the same captured bytes.
    legacy_name = legacy.name.rsplit("_", 1)[0] + ".json.gz"
    legacy_path = legacy.with_name(legacy_name)
    legacy_path.write_bytes(legacy.read_bytes())
    legacy.unlink()
    next(legacy.parent.glob("*.revisions.json")).unlink()

    # Unchanged bytes resolve to the legacy object rather than duplicating it.
    reuse = _write(archive, NINE_FINAL)
    assert reuse.idempotent_reuse is True
    assert Path(reuse.raw_file_reference) == legacy_path

    changed = _write(archive, FIFTEEN_FINAL)
    assert changed.revision == 2
    assert changed.supersedes_payload_checksum == first.payload_checksum
    assert legacy_path.exists()

    revisions = archive.historical_revisions(
        "MLB_STATS_API", partition_date=PARTITION, request_fingerprint=FINGERPRINT)
    assert revisions[0]["adopted_from_pre_revision_path"] is True
    assert revisions[0]["retrieved_at"] == first.ingestion_timestamp.isoformat()
    assert datetime.fromisoformat(revisions[0]["retrieved_at"]).tzinfo is not None
    assert revisions[0]["retrieved_at"] != revisions[1]["retrieved_at"]
    assert datetime.now(UTC) >= datetime.fromisoformat(revisions[1]["retrieved_at"])
