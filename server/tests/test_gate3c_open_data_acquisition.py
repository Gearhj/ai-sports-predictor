from __future__ import annotations

import hashlib
import io
import json
import stat
import tarfile
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.gen2.open_data_acquisition import (
    ArchiveFormat,
    DEFAULT_SOURCE_POLICIES,
    DownloadDisposition,
    DownloadRequest,
    ExtractionLimits,
    OpenDataAcquirer,
    OpenDataIntegrityError,
    OpenDataInterruptedError,
    OpenDataPolicyError,
    OpenDataSourcePolicy,
    OpenDataStorageLimitError,
    SourcePolicyState,
    TransportRequest,
    TransportResponse,
    UnsafeArchiveError,
)


SOURCE = "TEST_OPEN_ARCHIVES"
URL = "https://data.example.test/files/sample.bin"
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


class TickClock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(seconds=1)
        return result


class FakeTransport:
    def __init__(self, *responses: TransportResponse | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[TransportRequest] = []

    def fetch(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected transport call")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _policy(
    *,
    maximum_file_bytes: int = 1_000_000,
    formats: tuple[ArchiveFormat, ...] = (
        ArchiveFormat.NONE,
        ArchiveFormat.ZIP,
        ArchiveFormat.TAR_GZ,
    ),
) -> OpenDataSourcePolicy:
    return OpenDataSourcePolicy(
        source_key=SOURCE,
        state=SourcePolicyState.AUTHORIZED_OPEN_DATA,
        official_hosts=("data.example.test",),
        allowed_path_prefixes=("/files/",),
        allowed_mime_types=(
            "application/gzip",
            "application/octet-stream",
            "application/zip",
        ),
        allowed_archive_formats=formats,
        maximum_file_bytes=maximum_file_bytes,
        policy_reference="project-policy://tests/open-data",
        decision_basis="Explicit offline-test authorization for an injected transport.",
        authorization_identity_checksum="a" * 64,
    )


def _response(
    payload: bytes,
    *,
    status: int = 200,
    mime: str = "application/octet-stream",
    headers: dict[str, str] | None = None,
    body=None,
    final_url: str | None = None,
) -> TransportResponse:
    values = {
        "Content-Length": str(len(payload)),
        "Content-Type": mime,
        "X-Checksum-Sha256": hashlib.sha256(payload).hexdigest(),
        **(headers or {}),
    }
    return TransportResponse(
        status_code=status,
        headers=values,
        body=(payload,) if body is None else body,
        final_url=final_url,
    )


def _acquirer(
    root: Path,
    transport: FakeTransport,
    *,
    policy: OpenDataSourcePolicy | None = None,
    maximum_run_bytes: int = 1_000_000,
    free_bytes: int = 1_000_000_000,
    reserve: int = 0,
) -> OpenDataAcquirer:
    selected = policy or _policy()
    return OpenDataAcquirer(
        root,
        transport=transport,
        policies={selected.source_key: selected},
        maximum_run_bytes=maximum_run_bytes,
        minimum_free_bytes_after_write=reserve,
        free_space=lambda _: free_bytes,
        clock=TickClock(),
    )


def _request(
    *,
    object_key: str = "sample.bin",
    url: str = URL,
    archive_format: ArchiveFormat = ArchiveFormat.NONE,
    payload: bytes | None = None,
) -> DownloadRequest:
    return DownloadRequest(
        source_key=SOURCE,
        object_key=object_key,
        url=url,
        archive_format=archive_format,
        expected_sha256=hashlib.sha256(payload).hexdigest() if payload is not None else None,
        expected_content_length=len(payload) if payload is not None else None,
    )


def test_default_policies_are_explicit_blocked_or_prohibited_and_never_call_transport(
    artifact_root: Path,
) -> None:
    assert DEFAULT_SOURCE_POLICIES["RETROSHEET_EVENT_ARCHIVES"].state is (
        SourcePolicyState.BLOCKED_SOURCE_AUTHORIZATION
    )
    assert DEFAULT_SOURCE_POLICIES["CHADWICK_REGISTER_ARCHIVES"].state is (
        SourcePolicyState.BLOCKED_SOURCE_AUTHORIZATION
    )
    assert DEFAULT_SOURCE_POLICIES["MLB_STATS_API"].state is SourcePolicyState.PROHIBITED
    assert DEFAULT_SOURCE_POLICIES["BASEBALL_SAVANT_STATCAST"].state is (
        SourcePolicyState.PROHIBITED
    )
    assert DEFAULT_SOURCE_POLICIES["PYBASEBALL_SCRAPER"].state is (
        SourcePolicyState.PROHIBITED
    )
    assert "codeload.github.com" in DEFAULT_SOURCE_POLICIES[
        "CHADWICK_REGISTER_ARCHIVES"
    ].official_hosts
    assert DEFAULT_SOURCE_POLICIES["SABR_LAHMAN_BOX_DOWNLOADS"].allowed_query_keys == (
        "file_id",
        "rm",
        "shared_name",
    )

    transport = FakeTransport()
    acquirer = OpenDataAcquirer(
        artifact_root / "blocked",
        transport=transport,
        minimum_free_bytes_after_write=0,
    )
    with pytest.raises(OpenDataPolicyError, match="not authorized"):
        acquirer.download(DownloadRequest(
            source_key="RETROSHEET_EVENT_ARCHIVES",
            object_key="2024-events.zip",
            url="https://www.retrosheet.org/events/2024eve.zip",
            archive_format=ArchiveFormat.ZIP,
        ))
    with pytest.raises(OpenDataPolicyError, match="prohibited"):
        acquirer.download(DownloadRequest(
            source_key="MLB_STATS_API",
            object_key="schedule.json",
            url="https://statsapi.mlb.com/api/v1/schedule",
        ))
    assert transport.requests == []

    with pytest.raises(OpenDataPolicyError, match="rejected scraper"):
        OpenDataSourcePolicy(
            source_key="PYBASEBALL_SCRAPER",
            state=SourcePolicyState.AUTHORIZED_OPEN_DATA,
            official_hosts=("data.example.test",),
            allowed_path_prefixes=("/files/",),
            allowed_mime_types=("application/octet-stream",),
            allowed_archive_formats=(ArchiveFormat.NONE,),
            maximum_file_bytes=10,
            policy_reference="project-policy://invalid/scraper",
            decision_basis="Attempted bypass.",
            authorization_identity_checksum="b" * 64,
        )


def test_exact_safe_query_keys_are_canonicalized_but_redacted_from_manifests(
    artifact_root: Path,
) -> None:
    policy = OpenDataSourcePolicy(
        source_key=SOURCE,
        state=SourcePolicyState.AUTHORIZED_OPEN_DATA,
        official_hosts=("sabr.app",),
        allowed_path_prefixes=("/download",),
        allowed_mime_types=("application/octet-stream",),
        allowed_archive_formats=(ArchiveFormat.NONE,),
        maximum_file_bytes=1_000,
        policy_reference="project-policy://tests/sabr-query",
        decision_basis="Offline test of exact benign query keys.",
        allowed_query_keys=("file_id", "shared_name"),
        authorization_identity_checksum="c" * 64,
    )
    payload = b"lahman"
    transport = FakeTransport(_response(payload))
    root = artifact_root / "safe-query"
    acquirer = _acquirer(root, transport, policy=policy)
    request = _request(
        url="https://sabr.app/download?shared_name=Lahman+Data&file_id=42",
        payload=payload,
    )
    artifact = acquirer.download(request)

    assert transport.requests[0].url == (
        "https://sabr.app/download?file_id=42&shared_name=Lahman+Data"
    )
    assert artifact.sanitized_url == "https://sabr.app/download"
    manifest_text = acquirer.resolve(artifact.manifest_logical_uri).read_text(
        encoding="utf-8"
    )
    assert "file_id" not in manifest_text
    assert "shared_name" not in manifest_text
    assert "Lahman" not in manifest_text
    assert "?" not in manifest_text

    invalid_urls = (
        "https://sabr.app/download?file_id=42&file_id=43&shared_name=x",
        "https://sabr.app/download?file_id=&shared_name=x",
        "https://sabr.app/download?file_id=42&shared_name=",
        "https://sabr.app/download?unknown=42",
        "https://sabr.app/download?File_ID=42&shared_name=x",
        "https://sabr.app/download?file_id=token%3Dsigned&shared_name=x",
    )
    for index, url in enumerate(invalid_urls):
        rejected_transport = FakeTransport()
        with pytest.raises(OpenDataPolicyError):
            _acquirer(
                artifact_root / f"bad-query-{index}",
                rejected_transport,
                policy=policy,
            ).download(_request(url=url))
        assert rejected_transport.requests == []

    signed_redirect_transport = FakeTransport(_response(
        payload,
        final_url=(
            "https://sabr.app/download?file_id=42&shared_name=x&token=signed-value"
        ),
    ))
    with pytest.raises(OpenDataPolicyError):
        _acquirer(
            artifact_root / "signed-redirect",
            signed_redirect_transport,
            policy=policy,
        ).download(_request(
            url="https://sabr.app/download?file_id=42&shared_name=x",
        ))
    assert not list((artifact_root / "signed-redirect").rglob("*.json"))

    with pytest.raises(ValueError, match="Secret-like query keys"):
        OpenDataSourcePolicy(
            source_key=SOURCE,
            state=SourcePolicyState.AUTHORIZED_OPEN_DATA,
            official_hosts=("sabr.app",),
            allowed_path_prefixes=("/",),
            allowed_mime_types=("application/octet-stream",),
            allowed_archive_formats=(ArchiveFormat.NONE,),
            maximum_file_bytes=1_000,
            policy_reference="project-policy://tests/unsafe-query-policy",
            decision_basis="Attempt to allowlist a secret key.",
            allowed_query_keys=("api_key",),
            authorization_identity_checksum="c" * 64,
        )
    with pytest.raises(OpenDataPolicyError, match="MLB/Savant hosts"):
        OpenDataSourcePolicy(
            source_key="OTHER_OPEN_ARCHIVE",
            state=SourcePolicyState.AUTHORIZED_OPEN_DATA,
            official_hosts=("baseballsavant.mlb.com",),
            allowed_path_prefixes=("/files/",),
            allowed_mime_types=("application/octet-stream",),
            allowed_archive_formats=(ArchiveFormat.NONE,),
            maximum_file_bytes=10,
            policy_reference="project-policy://invalid/host",
            decision_basis="Attempted bypass.",
            authorization_identity_checksum="b" * 64,
        )


def test_codeload_github_surface_is_usable_only_through_an_authorized_clone(
    artifact_root: Path,
) -> None:
    candidate = DEFAULT_SOURCE_POLICIES["CHADWICK_REGISTER_ARCHIVES"]
    policy = replace(
        candidate,
        state=SourcePolicyState.AUTHORIZED_OPEN_DATA,
        authorization_identity_checksum="d" * 64,
    )
    payload = b"zip-placeholder"
    transport = FakeTransport(_response(payload, mime="application/zip"))
    acquirer = OpenDataAcquirer(
        artifact_root / "codeload",
        transport=transport,
        policies={policy.source_key: policy},
        maximum_run_bytes=1_000,
        minimum_free_bytes_after_write=0,
        free_space=lambda _: 1_000_000,
        clock=TickClock(),
    )
    artifact = acquirer.download(DownloadRequest(
        source_key=policy.source_key,
        object_key="register.zip",
        url=(
            "https://codeload.github.com/chadwickbureau/register/zip/refs/heads/master"
        ),
        archive_format=ArchiveFormat.ZIP,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_content_length=len(payload),
    ))
    assert artifact.revision == 1
    assert transport.requests[0].url.startswith("https://codeload.github.com/")


def test_chunked_official_response_records_measured_length_without_weakening_caps(
    artifact_root: Path,
) -> None:
    payload = b"official chunked notice"
    response = TransportResponse(
        status_code=200,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Checksum-Sha256": hashlib.sha256(payload).hexdigest(),
        },
        body=(payload[:8], payload[8:]),
    )
    artifact = _acquirer(
        artifact_root / "chunked",
        FakeTransport(response),
        maximum_run_bytes=len(payload),
    ).download(_request())
    assert artifact.content_length == len(payload)
    assert artifact.attempts[0].response_content_length is None
    assert artifact.attempts[0].received_bytes == len(payload)

    too_large = TransportResponse(
        status_code=200,
        headers={"Content-Type": "application/octet-stream"},
        body=(b"12345", b"67890"),
    )
    with pytest.raises(OpenDataStorageLimitError, match="source file cap"):
        _acquirer(
            artifact_root / "chunked-cap",
            FakeTransport(too_large),
            policy=_policy(maximum_file_bytes=9),
        ).download(_request())


@pytest.mark.parametrize(
    "url",
    [
        "http://data.example.test/files/sample.bin",
        "https://evil.example.test/files/sample.bin",
        "https://data.example.test/private/sample.bin",
        "https://data.example.test/files/%2e%2e/private.bin",
        "https://data.example.test/files/sample.bin?token=secret",
        "https://user:password@data.example.test/files/sample.bin",
        "https://127.0.0.1/files/sample.bin",
    ],
)
def test_url_policy_is_exact_and_rejected_before_transport(
    artifact_root: Path,
    url: str,
) -> None:
    transport = FakeTransport()
    with pytest.raises(OpenDataPolicyError):
        _acquirer(artifact_root / "urls", transport).download(_request(url=url))
    assert transport.requests == []


def test_interrupted_download_resumes_with_range_and_complete_accounting(
    artifact_root: Path,
) -> None:
    payload = b"abcdef"

    def interrupted_body():
        yield payload[:3]
        raise ConnectionError("simulated connection loss")

    first = _response(payload, body=interrupted_body())
    second = _response(
        payload,
        status=206,
        headers={
            "Content-Length": "3",
            "Content-Range": "bytes 3-5/6",
        },
        body=(payload[3:],),
    )
    transport = FakeTransport(first, second)
    root = artifact_root / "resume"
    acquirer = _acquirer(root, transport)
    request = _request(payload=payload)

    with pytest.raises(OpenDataInterruptedError, match="resumable"):
        acquirer.download(request)
    assert not list(root.rglob("*.blob"))
    partial = next(root.rglob("*.part"))
    checkpoint = next(root.rglob("*.checkpoint.json"))
    assert partial.read_bytes() == b"abc"
    checkpoint_text = checkpoint.read_text(encoding="utf-8")
    assert "simulated connection loss" not in checkpoint_text
    assert "token" not in checkpoint_text.lower()

    artifact = acquirer.download(request)
    assert artifact.disposition is DownloadDisposition.CREATED_REVISION
    assert artifact.revision == 1
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
    assert artifact.content_length == len(payload)
    assert artifact.mime_type == "application/octet-stream"
    assert artifact.http_status == 206
    assert artifact.physical_attempt_count == 2
    assert artifact.transport_bytes_received == 6
    assert [item.outcome for item in artifact.attempts] == ["INTERRUPTED", "SUCCEEDED"]
    assert [item.received_bytes for item in artifact.attempts] == [3, 3]
    assert artifact.started_at == NOW.isoformat()
    assert artifact.completed_at == (NOW + timedelta(seconds=3)).isoformat()
    assert [item.range_start for item in transport.requests] == [0, 3]
    assert transport.requests[1].headers == {"Range": "bytes=3-"}
    assert acquirer.resolve(artifact.content_logical_uri).read_bytes() == payload
    manifest = json.loads(
        acquirer.resolve(artifact.manifest_logical_uri).read_text(encoding="utf-8")
    )
    assert manifest["authorization_identity_checksum"] == "a" * 64
    assert "headers" not in json.dumps(manifest).lower()
    assert "?" not in json.dumps(manifest)
    assert acquirer.run_received_bytes == 6
    assert not list(root.rglob("*.part"))
    assert not list(root.rglob("*.checkpoint.json"))


def test_interruption_before_response_resumes_from_zero_without_staging_collision(
    artifact_root: Path,
) -> None:
    payload = b"complete"
    transport = FakeTransport(ConnectionError("no response"), _response(payload))
    root = artifact_root / "zero-resume"
    acquirer = _acquirer(root, transport)
    request = _request(payload=payload)
    with pytest.raises(OpenDataInterruptedError, match="before response"):
        acquirer.download(request)
    assert next(root.rglob("*.part")).stat().st_size == 0

    artifact = acquirer.download(request)
    assert artifact.physical_attempt_count == 2
    assert [item.requested_range_start for item in artifact.attempts] == [0, 0]
    assert [item.status_code for item in artifact.attempts] == [None, 200]
    assert acquirer.resolve(artifact.content_logical_uri).read_bytes() == payload


def test_corrupted_resume_staging_fails_closed_without_another_request(
    artifact_root: Path,
) -> None:
    payload = b"abcdef"

    def interrupted_body():
        yield payload[:3]
        raise ConnectionError("stop")

    transport = FakeTransport(_response(payload, body=interrupted_body()))
    root = artifact_root / "tampered-resume"
    acquirer = _acquirer(root, transport)
    with pytest.raises(OpenDataInterruptedError):
        acquirer.download(_request(payload=payload))
    next(root.rglob("*.part")).write_bytes(b"tampered")
    with pytest.raises(OpenDataIntegrityError, match="byte count|SHA-256"):
        acquirer.download(_request(payload=payload))
    assert len(transport.requests) == 1


def test_same_bytes_reuse_revision_and_changed_bytes_create_new_revision(
    artifact_root: Path,
) -> None:
    first_bytes = b"revision-one"
    second_bytes = b"revision-two"
    transport = FakeTransport(
        _response(first_bytes),
        _response(first_bytes),
        _response(second_bytes),
    )
    root = artifact_root / "revisions"
    acquirer = _acquirer(root, transport)
    request = _request()

    first = acquirer.download(request)
    unchanged = acquirer.download(request)
    changed = acquirer.download(request)

    assert first.revision == unchanged.revision == 1
    assert first.disposition is DownloadDisposition.CREATED_REVISION
    assert unchanged.disposition is DownloadDisposition.UNCHANGED_BYTES
    assert changed.revision == 2
    assert changed.disposition is DownloadDisposition.CREATED_REVISION
    assert changed.sha256 != first.sha256
    assert acquirer.resolve(first.content_logical_uri).read_bytes() == first_bytes
    assert acquirer.resolve(changed.content_logical_uri).read_bytes() == second_bytes
    assert len(list(root.rglob("*.blob"))) == 2
    assert len(list(root.rglob("acquisition-*.json"))) == 3
    latest = json.loads(next(root.rglob("latest.json")).read_text(encoding="utf-8"))
    assert latest["revision"] == 2
    assert latest["sha256"] == changed.sha256


def test_status_mime_length_sha_caps_and_free_space_fail_closed(
    artifact_root: Path,
) -> None:
    payload = b"four"
    cases = [
        (
            _response(payload, status=404),
            _policy(),
            100,
            1_000,
            0,
            OpenDataIntegrityError,
            "Expected HTTP 200",
        ),
        (
            _response(payload, mime="text/html"),
            _policy(),
            100,
            1_000,
            0,
            OpenDataIntegrityError,
            "MIME",
        ),
        (
            _response(payload, body=(b"x",)),
            _policy(),
            100,
            1_000,
            0,
            OpenDataIntegrityError,
            "Content-Length",
        ),
        (
            _response(payload, headers={"X-Checksum-Sha256": "0" * 64}),
            _policy(),
            100,
            1_000,
            0,
            OpenDataIntegrityError,
            "SHA-256",
        ),
        (
            _response(payload),
            _policy(maximum_file_bytes=3),
            100,
            1_000,
            0,
            OpenDataStorageLimitError,
            "file cap",
        ),
        (
            _response(payload),
            _policy(),
            3,
            1_000,
            0,
            OpenDataStorageLimitError,
            "run byte cap",
        ),
        (
            _response(payload),
            _policy(),
            100,
            5,
            2,
            OpenDataStorageLimitError,
            "free-space reserve",
        ),
    ]
    for index, (response, policy, run_cap, free, reserve, error, match) in enumerate(cases):
        transport = FakeTransport(response)
        acquirer = _acquirer(
            artifact_root / f"failure-{index}",
            transport,
            policy=policy,
            maximum_run_bytes=run_cap,
            free_bytes=free,
            reserve=reserve,
        )
        with pytest.raises(error, match=match):
            acquirer.download(_request())
        assert not list((artifact_root / f"failure-{index}").rglob("*.blob"))


def _zip_bytes(entries: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, value in entries.items():
            bundle.writestr(name, value)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, "target.txt")
    return output.getvalue()


def _tar_with_link() -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        info = tarfile.TarInfo("unsafe-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        bundle.addfile(info)
    return output.getvalue()


def test_safe_zip_extraction_is_atomic_bounded_and_reusable(artifact_root: Path) -> None:
    payload = _zip_bytes({"season/games.csv": b"game_id\n1\n", "README.txt": b"open"})
    transport = FakeTransport(_response(payload, mime="application/zip"))
    acquirer = _acquirer(artifact_root / "extract-good", transport)
    download = acquirer.download(_request(
        object_key="season.zip",
        url="https://data.example.test/files/season.zip",
        archive_format=ArchiveFormat.ZIP,
        payload=payload,
    ))

    extraction = acquirer.extract_archive(download)
    destination = acquirer.resolve(extraction.extraction_logical_uri)
    assert extraction.member_count == 2
    assert extraction.expanded_bytes == len(b"game_id\n1\n") + len(b"open")
    assert {item.relative_path for item in extraction.members} == {
        "README.txt",
        "season/games.csv",
    }
    assert (destination / "season" / "games.csv").read_bytes() == b"game_id\n1\n"
    assert len(extraction.identity_checksum) == 64
    assert acquirer.extract_archive(download).reused is True
    (destination / "season" / "games.csv").write_bytes(b"tampered")
    with pytest.raises(OpenDataIntegrityError, match="[Ii]mmutable content"):
        acquirer.extract_archive(download)
    assert not list((artifact_root / "extract-good").rglob(".extract-*.tmp"))


@pytest.mark.parametrize(
    ("payload", "archive_format", "mime", "match"),
    [
        (
            _zip_bytes({"../escape.txt": b"escape"}),
            ArchiveFormat.ZIP,
            "application/zip",
            "traversal",
        ),
        (
            _zip_bytes({}, symlink="unsafe-link"),
            ArchiveFormat.ZIP,
            "application/zip",
            "links",
        ),
        (
            _tar_with_link(),
            ArchiveFormat.TAR_GZ,
            "application/gzip",
            "regular archive files",
        ),
    ],
)
def test_unsafe_archive_paths_and_links_never_escape_staging(
    artifact_root: Path,
    payload: bytes,
    archive_format: ArchiveFormat,
    mime: str,
    match: str,
) -> None:
    transport = FakeTransport(_response(payload, mime=mime))
    root = artifact_root / (
        f"unsafe-{archive_format.value.lower()}-{hashlib.sha1(payload).hexdigest()}"
    )
    acquirer = _acquirer(root, transport)
    artifact = acquirer.download(_request(
        object_key=f"unsafe-{archive_format.value.lower()}",
        archive_format=archive_format,
        payload=payload,
    ))
    with pytest.raises(UnsafeArchiveError, match=match):
        acquirer.extract_archive(artifact)
    assert not (artifact_root / "escape.txt").exists()
    assert not list(root.rglob(".extract-*.tmp"))


def test_archive_member_expansion_limits_fail_before_publication(artifact_root: Path) -> None:
    payload = _zip_bytes({"large.txt": b"12345"})
    transport = FakeTransport(_response(payload, mime="application/zip"))
    root = artifact_root / "archive-limit"
    acquirer = _acquirer(root, transport)
    artifact = acquirer.download(_request(
        object_key="limited.zip",
        archive_format=ArchiveFormat.ZIP,
        payload=payload,
    ))
    with pytest.raises(UnsafeArchiveError, match="size cap|expanded bytes"):
        acquirer.extract_archive(
            artifact,
            limits=ExtractionLimits(
                maximum_members=10,
                maximum_member_bytes=4,
                maximum_expanded_bytes=4,
                maximum_expansion_ratio=100,
            ),
        )
    assert not list(root.rglob(".extract-*.tmp"))
