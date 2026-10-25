"""Keep the original bytes of every successful SportsGameOdds response.

WHY
---
AISP's SportsGameOdds rows store the pair's OLDER provider ``lastUpdatedAt`` on
both legs and a per-row checksum of the stored outcome. Neither the two legs'
own update stamps nor any identity of the response they came from survive into
the database, and the raw response used to be parsed and thrown away. The V18
market pilot needs both, and they can only come from the response itself.

WHAT
----
Every successful request's exact response body is kept through the existing
:class:`~app.ingestion.archive.RawArchive`, in the live layout the pilot exporter
links (``<raw_data_root>/sportsgameodds/YYYY/MM/DD/HHMMSS_ffffff_<hex>.json``,
wrapper with ``source``, ``ingestion_timestamp``, ``request_fingerprint`` and
``checksum``), plus the byte-exact body beside it (``.body.gz``).

WHAT IT MAY NOT DO
------------------
It never makes, repeats or delays a provider request, and it never fails one:
every error becomes a ``status`` in the returned record, which the caller files
in the job result and the ledger's terminal row. It never stores a credential:
the key travels only in the ``X-Api-Key`` header, the recorded filters are the
query parameters (which never carry it), no URL is stored, and a body that
contains the configured key is refused rather than written.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.ingestion.archive import RawArchive

ARCHIVE_SOURCE = "SPORTSGAMEODDS"
ARCHIVED = "ARCHIVED"
REFUSED = "REFUSED"
FAILED = "FAILED"
CREDENTIAL_NOTE = "sent only in the X-Api-Key header; not in the URL, the filters or this archive"


def archive_response(
    settings: Settings,
    body: bytes,
    *,
    requested_at: datetime,
    request_fingerprint: str | None,
    lane: str,
    league: str,
    filters: dict[str, Any],
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Archive one successful response. Never raises; returns what happened."""

    try:
        key = (getattr(settings, "sportsgameodds_api_key", "") or "").strip()
        if key and key.encode("utf-8") in bytes(body):
            return {"status": REFUSED, "reason": "RESPONSE_BODY_CONTAINS_THE_CONFIGURED_CREDENTIAL",
                    "detail": "Not written: the body carried the API key."}
        if key and any(key in str(value) for value in (filters or {}).values()):
            return {"status": REFUSED, "reason": "REQUEST_FILTERS_CONTAIN_THE_CONFIGURED_CREDENTIAL",
                    "detail": "Not written: the recorded filters carried the API key."}
        record = RawArchive(Path(root or settings.raw_data_root)).write_response(
            ARCHIVE_SOURCE, body, request_fingerprint=request_fingerprint,
            extra_metadata={
                "lane": lane,
                "league": league,
                "requested_at": requested_at.isoformat(),
                "requested_at_meaning": ("when AISP sent the request (the ledger's requested_at and the rows' "
                                         "available_as_of); not a receipt and not a quote update clock"),
                "request_filters": dict(filters or {}),
                "credential": CREDENTIAL_NOTE,
            },
        )
    except Exception as error:  # noqa: BLE001 - an archive fault must never fail the fetch
        return {"status": FAILED, "reason": type(error).__name__, "detail": str(error)[:300]}
    return {
        "status": ARCHIVED,
        "path": record.path,
        "sha256": record.sha256,
        "bytes": record.bytes,
        "responseBodyPath": record.body_path,
        "responseBodySha256": record.body_sha256,
        "responseBodyBytes": record.body_bytes,
        "archivedAtUtc": record.ingestion_timestamp.isoformat(),
        "requestFingerprint": record.request_fingerprint,
    }


__all__ = ["ARCHIVED", "ARCHIVE_SOURCE", "FAILED", "REFUSED", "archive_response"]
