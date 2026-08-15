"""Controlled Gate 3C-FD1 official open-data acquisition entry point.

This module is intentionally separate from ASP's live provider adapters.  It
can download only the exact independent open-source catalog frozen below; MLB
and Baseball Savant hosts are rejected by ``open_data_acquisition`` before a
transport can be invoked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.gen2.open_data_acquisition import (
    ArchiveFormat,
    DEFAULT_SOURCE_POLICIES,
    DownloadArtifact,
    DownloadRequest,
    ExtractionArtifact,
    ExtractionLimits,
    OpenDataAcquirer,
    OpenDataInterruptedError,
    OpenDataSourcePolicy,
    SourcePolicyState,
    TransportRequest,
    TransportResponse,
)


FD1_BASELINE_COMMIT = "96f5cdaa7cad4383b96acbccd478c8a527ae4aba"
CHADWICK_REVISION = "2e8e73355f9c77b963115377bd98c784cfeec10f"
LAHMAN_SHARED_NAME = "y1prhc795jk8zvmelfd3jq7tl389y6cd"
LAHMAN_FILE_ENDPOINT = "https://sabr.app.box.com/index.php"
DEFAULT_RAW_ROOT = Path(__file__).resolve().parents[3] / "data" / "raw"


def _checksum(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _authorization_identity(source_key: str, classification: str) -> str:
    return _checksum(
        {
            "gate": "3C-FD1",
            "baseline_commit": FD1_BASELINE_COMMIT,
            "source_key": source_key,
            "classification": classification,
            "scope": "OFFICIAL_OPEN_DATA_DOWNLOAD_AND_LOCAL_DERIVATION",
            "authorized_by": "project-owner-attached-master-prompt",
            "authorized_on": "2026-08-14",
        }
    )


def _authorize(
    policy: OpenDataSourcePolicy,
    *,
    classification: str,
    official_hosts: tuple[str, ...] | None = None,
    allowed_path_prefixes: tuple[str, ...] | None = None,
    allowed_mime_types: tuple[str, ...] | None = None,
    allowed_archive_formats: tuple[ArchiveFormat, ...] | None = None,
    maximum_file_bytes: int | None = None,
) -> OpenDataSourcePolicy:
    return replace(
        policy,
        state=SourcePolicyState.AUTHORIZED_OPEN_DATA,
        authorization_identity_checksum=_authorization_identity(
            policy.source_key, classification
        ),
        decision_basis=(
            "Explicit Gate 3C-FD1 project-owner authorization for this exact "
            "independent official source; required rights and attribution remain bound."
        ),
        official_hosts=official_hosts or policy.official_hosts,
        allowed_path_prefixes=allowed_path_prefixes or policy.allowed_path_prefixes,
        allowed_mime_types=allowed_mime_types or policy.allowed_mime_types,
        allowed_archive_formats=(
            allowed_archive_formats or policy.allowed_archive_formats
        ),
        maximum_file_bytes=maximum_file_bytes or policy.maximum_file_bytes,
    )


def _policy(
    *,
    source_key: str,
    classification: str,
    official_hosts: tuple[str, ...],
    allowed_path_prefixes: tuple[str, ...],
    allowed_mime_types: tuple[str, ...],
    allowed_archive_formats: tuple[ArchiveFormat, ...],
    maximum_file_bytes: int,
    allowed_query_keys: tuple[str, ...] = (),
) -> OpenDataSourcePolicy:
    return OpenDataSourcePolicy(
        source_key=source_key,
        state=SourcePolicyState.AUTHORIZED_OPEN_DATA,
        official_hosts=official_hosts,
        allowed_path_prefixes=allowed_path_prefixes,
        allowed_mime_types=allowed_mime_types,
        allowed_archive_formats=allowed_archive_formats,
        maximum_file_bytes=maximum_file_bytes,
        policy_reference="docs/FREE_BASEBALL_DATA_SOURCE_INVENTORY.md",
        decision_basis=(
            "Explicit Gate 3C-FD1 project-owner authorization for this exact "
            "independent official source; required rights and attribution remain bound."
        ),
        authorization_identity_checksum=_authorization_identity(
            source_key, classification
        ),
        allow_same_policy_redirects=True,
        allowed_query_keys=allowed_query_keys,
    )


FD1_SOURCE_POLICIES: Mapping[str, OpenDataSourcePolicy] = {
    "RETROSHEET_CSV_DOWNLOADS": _policy(
        source_key="RETROSHEET_CSV_DOWNLOADS",
        classification=(
            "FREE_DOWNLOAD_COMMERCIAL_DERIVED_USE_ALLOWED_REQUIRED_ATTRIBUTION"
        ),
        official_hosts=("www.retrosheet.org",),
        allowed_path_prefixes=("/downloads/", "/notice.txt"),
        allowed_mime_types=(
            "application/octet-stream",
            "application/zip",
            "text/plain",
        ),
        allowed_archive_formats=(ArchiveFormat.NONE, ArchiveFormat.ZIP),
        maximum_file_bytes=2 * 1024**3,
    ),
    "RETROSHEET_EVENT_ARCHIVES": _authorize(
        DEFAULT_SOURCE_POLICIES["RETROSHEET_EVENT_ARCHIVES"],
        classification=(
            "FREE_DOWNLOAD_COMMERCIAL_DERIVED_USE_ALLOWED_REQUIRED_ATTRIBUTION"
        ),
        allowed_mime_types=("application/octet-stream", "application/zip"),
        maximum_file_bytes=2 * 1024**3,
    ),
    "RETROSHEET_AUXILIARY_DOWNLOADS": _policy(
        source_key="RETROSHEET_AUXILIARY_DOWNLOADS",
        classification=(
            "FREE_DOWNLOAD_COMMERCIAL_DERIVED_USE_ALLOWED_REQUIRED_ATTRIBUTION"
        ),
        official_hosts=("www.retrosheet.org",),
        allowed_path_prefixes=(
            "/rosters.zip",
            "/biofile.zip",
            "/ballparks.zip",
            "/teams.zip",
            "/gamelogs/",
            "/schedule/",
        ),
        allowed_mime_types=("application/octet-stream", "application/zip"),
        allowed_archive_formats=(ArchiveFormat.ZIP,),
        maximum_file_bytes=2 * 1024**3,
    ),
    "CHADWICK_REGISTER_ARCHIVES": _authorize(
        DEFAULT_SOURCE_POLICIES["CHADWICK_REGISTER_ARCHIVES"],
        classification="ODC_BY_1_0_ATTRIBUTION_REQUIRED",
        allowed_mime_types=(
            "application/gzip",
            "application/octet-stream",
            "application/x-gzip",
            "application/zip",
        ),
    ),
    "SABR_LAHMAN_BOX_DOWNLOADS": _authorize(
        DEFAULT_SOURCE_POLICIES["SABR_LAHMAN_BOX_DOWNLOADS"],
        classification="CC_BY_SA_3_0_ATTRIBUTION_SHAREALIKE_REQUIRED",
        official_hosts=("sabr.app.box.com", "sabr.box.com"),
        allowed_path_prefixes=("/index.php", "/shared/static/"),
        allowed_mime_types=(
            "application/octet-stream",
            "application/zip",
            "text/csv",
            "text/plain",
        ),
        allowed_archive_formats=(ArchiveFormat.NONE, ArchiveFormat.ZIP),
    ),
}


@dataclass(frozen=True, slots=True)
class FD1DownloadSpec:
    stage: str
    request: DownloadRequest
    source_release: str
    license_identity: str
    attribution_identity: str


RETROSHEET_ATTRIBUTION_IDENTITY = _checksum(
    "The information used here was obtained free of\n"
    "charge from and is copyrighted by Retrosheet.  Interested\n"
    'parties may contact Retrosheet at "www.retrosheet.org".'
)
CHADWICK_ATTRIBUTION_IDENTITY = _checksum(
    "Chadwick Baseball Bureau Persons Register / ODC-By 1.0 / "
    + CHADWICK_REVISION
)
LAHMAN_ATTRIBUTION_IDENTITY = _checksum(
    "SABR Lahman Baseball Database Version 2025 / CC BY-SA 3.0"
)


def _lahman_spec(name: str, file_id: str, size: int) -> FD1DownloadSpec:
    url = (
        f"{LAHMAN_FILE_ENDPOINT}?file_id=f_{file_id}"
        "&rm=box_download_shared_file"
        f"&shared_name={LAHMAN_SHARED_NAME}"
    )
    return FD1DownloadSpec(
        stage="stage1",
        request=DownloadRequest(
            source_key="SABR_LAHMAN_BOX_DOWNLOADS",
            object_key=f"lahman-2025-{name.lower().replace('.', '-')}",
            url=url,
            archive_format=ArchiveFormat.NONE,
            expected_content_length=size,
            expected_mime_type="text/csv" if name.endswith(".csv") else "text/plain",
        ),
        source_release="lahman_1871-2025_csv",
        license_identity="CC_BY_SA_3_0",
        attribution_identity=LAHMAN_ATTRIBUTION_IDENTITY,
    )


_LAHMAN_FILES = (
    ("Batting.csv", "2084272468053", 7_999_073),
    ("BattingPost.csv", "2084267085161", 1_155_184),
    ("CollegePlaying.csv", "2084273807945", 429_375),
    ("Fielding.csv", "2084272585201", 8_767_478),
    ("FieldingPost.csv", "2084262844727", 905_643),
    ("HallOfFame.csv", "2084268925644", 314_822),
    ("HomeGames.csv", "2084264259926", 178_043),
    ("Managers.csv", "2084260192585", 169_548),
    ("ManagersHalf.csv", "2084274597499", 3_568),
    ("Parks.csv", "2084275487842", 22_638),
    ("People.csv", "2084263017537", 3_223_128),
    ("Pitching.csv", "2084261668691", 5_112_472),
    ("PitchingPost.csv", "2084272333397", 624_914),
    ("Salaries.csv", "2084273174150", 800_653),
    ("Schools.csv", "2084271352651", 66_713),
    ("SeriesPost.csv", "2084273716683", 13_628),
    ("Teams.csv", "2084266946255", 701_384),
    ("TeamsFranchises.csv", "2084266696141", 5_610),
    ("TeamsHalf.csv", "2084269785346", 3_782),
)


def _retrosheet_zip(
    stage: str,
    source_key: str,
    object_key: str,
    url: str,
    *,
    expected_content_length: int | None = None,
) -> FD1DownloadSpec:
    return FD1DownloadSpec(
        stage=stage,
        request=DownloadRequest(
            source_key=source_key,
            object_key=object_key,
            url=url,
            archive_format=ArchiveFormat.ZIP,
            expected_content_length=expected_content_length,
        ),
        source_release="retrosheet_through_2025",
        license_identity=(
            "RETROSHEET_COMMERCIAL_DERIVED_USE_ALLOWED_REQUIRED_ATTRIBUTION"
        ),
        attribution_identity=RETROSHEET_ATTRIBUTION_IDENTITY,
    )


DOWNLOAD_CATALOG: tuple[FD1DownloadSpec, ...] = (
    FD1DownloadSpec(
        stage="stage1",
        request=DownloadRequest(
            source_key="RETROSHEET_CSV_DOWNLOADS",
            object_key="retrosheet-notice",
            url="https://www.retrosheet.org/notice.txt",
            archive_format=ArchiveFormat.NONE,
            expected_mime_type="text/plain",
        ),
        source_release="retrosheet_through_2025",
        license_identity=(
            "RETROSHEET_COMMERCIAL_DERIVED_USE_ALLOWED_REQUIRED_ATTRIBUTION"
        ),
        attribution_identity=RETROSHEET_ATTRIBUTION_IDENTITY,
    ),
    FD1DownloadSpec(
        stage="stage1",
        request=DownloadRequest(
            source_key="CHADWICK_REGISTER_ARCHIVES",
            object_key=f"chadwick-register-{CHADWICK_REVISION[:12]}",
            url=(
                "https://codeload.github.com/chadwickbureau/register/tar.gz/"
                + CHADWICK_REVISION
            ),
            archive_format=ArchiveFormat.TAR_GZ,
        ),
        source_release=CHADWICK_REVISION,
        license_identity="ODC_BY_1_0",
        attribution_identity=CHADWICK_ATTRIBUTION_IDENTITY,
    ),
    FD1DownloadSpec(
        stage="stage1",
        request=DownloadRequest(
            source_key="SABR_LAHMAN_BOX_DOWNLOADS",
            object_key="lahman-2025-readme",
            url=(
                "https://sabr.box.com/shared/static/"
                "qtgh1olzcaauz5x234wqx8huixizff8l.txt"
            ),
            archive_format=ArchiveFormat.NONE,
            expected_sha256=(
                "8f3d8f12b638887e4d182561a668e8103e11fbe1483019cae6d12a4c67cc3aab"
            ),
            expected_content_length=47_982,
            expected_mime_type="text/plain",
        ),
        source_release="lahman_1871-2025_csv",
        license_identity="CC_BY_SA_3_0",
        attribution_identity=LAHMAN_ATTRIBUTION_IDENTITY,
    ),
    *(_lahman_spec(*item) for item in _LAHMAN_FILES),
    _retrosheet_zip(
        "stage2",
        "RETROSHEET_CSV_DOWNLOADS",
        "retrosheet-csv-through-2025",
        "https://www.retrosheet.org/downloads/csvdownloads.zip",
        expected_content_length=798_907_450,
    ),
    *(
        _retrosheet_zip(
            "stage3",
            "RETROSHEET_EVENT_ARCHIVES",
            f"retrosheet-{season}-regular-events",
            f"https://www.retrosheet.org/events/{season}eve.zip",
        )
        for season in range(2021, 2026)
    ),
    *(
        _retrosheet_zip(
            "stage3",
            "RETROSHEET_EVENT_ARCHIVES",
            f"retrosheet-{season}-box-events",
            f"https://www.retrosheet.org/events/{season}box.zip",
        )
        for season in range(2021, 2026)
    ),
    _retrosheet_zip(
        "stage3",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "retrosheet-rosters-through-2025",
        "https://www.retrosheet.org/rosters.zip",
    ),
    _retrosheet_zip(
        "stage3",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "retrosheet-biofile-through-2025",
        "https://www.retrosheet.org/biofile.zip",
    ),
    _retrosheet_zip(
        "stage3",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "retrosheet-ballparks-through-2025",
        "https://www.retrosheet.org/ballparks.zip",
    ),
    _retrosheet_zip(
        "stage3",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "retrosheet-teams-through-2025",
        "https://www.retrosheet.org/teams.zip",
    ),
    _retrosheet_zip(
        "stage3",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "retrosheet-gamelogs-2020-2025",
        "https://www.retrosheet.org/gamelogs/gl2020_25.zip",
    ),
    _retrosheet_zip(
        "stage3",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "retrosheet-schedules",
        "https://www.retrosheet.org/schedule/schedule.zip",
    ),
)


class OfficialHttpTransport:
    """Bounded streaming transport for the already-policy-validated catalog."""

    def __init__(self, *, timeout_seconds: float = 1_800.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    def fetch(self, request: TransportRequest) -> TransportResponse:
        client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={"User-Agent": "ASP-Gate3C-FD1/1.0 (+local governed research)"},
        )
        response = client.send(
            client.build_request("GET", request.url, headers=dict(request.headers)),
            stream=True,
        )
        try:
            self._validate_redirect_boundary(request.url, response)
        except Exception:
            response.close()
            client.close()
            raise

        def body() -> Iterable[bytes]:
            try:
                yield from response.iter_bytes(chunk_size=1024 * 1024)
            finally:
                response.close()
                client.close()

        # Never persist Box's ephemeral signed CDN URL.  The acquisition layer
        # already binds a SHA-256 of the exact allowlisted transport URL while
        # exposing only the query-free official locator in its manifest.
        return TransportResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body(),
            final_url=request.url,
        )

    @staticmethod
    def _validate_redirect_boundary(request_url: str, response: httpx.Response) -> None:
        requested_host = (urlsplit(request_url).hostname or "").lower()
        for hop in (*response.history, response):
            host = (hop.url.host or "").lower()
            if requested_host in {"sabr.box.com", "sabr.app.box.com"}:
                if not (
                    host.endswith(".box.com")
                    or host == "box.com"
                    or host.endswith(".boxcloud.com")
                    or host == "boxcloud.com"
                ):
                    raise RuntimeError("SABR/Box redirect left the official Box boundary")
            elif host != requested_host:
                raise RuntimeError("Official download redirected outside its source host")


@dataclass(frozen=True, slots=True)
class FD1AcquisitionResult:
    stage: str
    downloads: tuple[DownloadArtifact, ...]
    extractions: tuple[ExtractionArtifact, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": "3C-FD1",
            "stage": self.stage,
            "download_count": len(self.downloads),
            "physical_attempt_count": sum(
                item.physical_attempt_count for item in self.downloads
            ),
            "transport_bytes_received": sum(
                item.transport_bytes_received for item in self.downloads
            ),
            "content_bytes": sum(item.content_length for item in self.downloads),
            "downloads": [item.manifest() for item in self.downloads],
            "extractions": [asdict(item) for item in self.extractions],
            "mlb_savant_requests": 0,
            "model_fit_invoked": False,
            "model_promotion_invoked": False,
            "official_positions_invoked": False,
        }


def acquire_stage(
    stage: str,
    *,
    raw_root: Path = DEFAULT_RAW_ROOT,
    transport: OfficialHttpTransport | None = None,
    extract: bool = True,
    maximum_attempts_per_object: int = 3,
) -> FD1AcquisitionResult:
    if stage not in {"stage1", "stage2", "stage3"}:
        raise ValueError("stage must be stage1, stage2, or stage3")
    if maximum_attempts_per_object < 1:
        raise ValueError("maximum_attempts_per_object must be positive")
    acquirer = OpenDataAcquirer(
        raw_root,
        transport=transport or OfficialHttpTransport(),
        policies=FD1_SOURCE_POLICIES,
        maximum_run_bytes=15 * 1024**3,
        minimum_free_bytes_after_write=100 * 1024**3,
    )
    downloads: list[DownloadArtifact] = []
    extractions: list[ExtractionArtifact] = []
    for spec in (item for item in DOWNLOAD_CATALOG if item.stage == stage):
        for attempt in range(1, maximum_attempts_per_object + 1):
            try:
                artifact = acquirer.download(spec.request)
                break
            except OpenDataInterruptedError:
                if attempt == maximum_attempts_per_object:
                    raise
        downloads.append(artifact)
        if extract and artifact.archive_format is not ArchiveFormat.NONE:
            extractions.append(
                acquirer.extract_archive(
                    artifact,
                    limits=ExtractionLimits(
                        maximum_members=100_000,
                        maximum_member_bytes=10 * 1024**3,
                        maximum_expanded_bytes=20 * 1024**3,
                        maximum_expansion_ratio=250.0,
                    ),
                )
            )
    return FD1AcquisitionResult(
        stage=stage,
        downloads=tuple(downloads),
        extractions=tuple(extractions),
    )


def catalog_summary() -> dict[str, Any]:
    stages = {stage: [] for stage in ("stage1", "stage2", "stage3")}
    for spec in DOWNLOAD_CATALOG:
        stages[spec.stage].append(
            {
                "source_key": spec.request.source_key,
                "object_key": spec.request.object_key,
                "official_url": urlsplit(spec.request.url)._replace(query="").geturl(),
                "expected_content_length": spec.request.expected_content_length,
                "archive_format": spec.request.archive_format.value,
                "source_release": spec.source_release,
                "license_identity": spec.license_identity,
                "attribution_identity": spec.attribution_identity,
            }
        )
    known_bytes = sum(
        spec.request.expected_content_length or 0 for spec in DOWNLOAD_CATALOG
    )
    return {
        "gate": "3C-FD1",
        "baseline_commit": FD1_BASELINE_COMMIT,
        "stages": stages,
        "objects": len(DOWNLOAD_CATALOG),
        "known_content_bytes": known_bytes,
        "maximum_incremental_bytes": 25 * 1024**3,
        "minimum_free_reserve_bytes": 100 * 1024**3,
        "mlb_savant_automated_acquisition": "BLOCKED",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inspect-catalog")
    acquire = subparsers.add_parser("acquire")
    acquire.add_argument("--stage", required=True, choices=("stage1", "stage2", "stage3"))
    acquire.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    acquire.add_argument("--no-extract", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inspect-catalog":
        payload = catalog_summary()
    else:
        payload = acquire_stage(
            args.stage,
            raw_root=args.raw_root,
            extract=not args.no_extract,
        ).as_dict()
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by operator use
    raise SystemExit(main())


__all__ = [
    "CHADWICK_REVISION",
    "DOWNLOAD_CATALOG",
    "FD1AcquisitionResult",
    "FD1DownloadSpec",
    "FD1_SOURCE_POLICIES",
    "OfficialHttpTransport",
    "acquire_stage",
    "catalog_summary",
    "main",
]
