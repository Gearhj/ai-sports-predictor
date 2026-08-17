"""Content-derived implementation revisions for bridge source lineage."""

from __future__ import annotations

import hashlib
from pathlib import Path


class IdentityBridgeRevisionError(RuntimeError):
    """The committed implementation inventory is unavailable or unsafe."""


def publishing_code_revision(project_root: Path) -> str:
    """Hash all bridge implementation modules except release-time trust anchors.

    Only ``release_trust.py`` is excluded: freezing a previously validated
    manifest/row authority must not create a semantic self-reference.  Semantic
    ``trust.py`` and every executable publisher, validator, extraction, row,
    and metadata module are included recursively.
    """

    root = Path(project_root) / "server" / "app" / "gen2" / "exchange"
    release_trust_path = root / "identity_bridge" / "release_trust.py"
    files = sorted(
        path
        for directory in (
            root / "identity_bridge",
            root / "identity_bridge_publisher",
            root / "identity_bridge_validator",
        )
        if directory.is_dir()
        for path in directory.rglob("*.py")
        if path.is_file()
        and path != release_trust_path
        and "__pycache__" not in path.parts
    )
    if not files:
        raise IdentityBridgeRevisionError("Identity-bridge implementation is missing")
    return _hash_files(files, Path(project_root))


def extraction_code_revision(project_root: Path) -> str:
    path = (
        Path(project_root)
        / "server"
        / "app"
        / "gen2"
        / "exchange"
        / "identity_bridge"
        / "source.py"
    )
    if not path.is_file():
        raise IdentityBridgeRevisionError("Identity extraction implementation is missing")
    return _hash_files([path], Path(project_root))


def _hash_files(files: list[Path], project_root: Path) -> str:
    digest = hashlib.sha256()
    for path in files:
        try:
            relative = path.relative_to(project_root).as_posix().encode("utf-8")
            payload = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise IdentityBridgeRevisionError(
                "Unable to hash identity-bridge implementation"
            ) from exc
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


__all__ = [
    "IdentityBridgeRevisionError",
    "extraction_code_revision",
    "publishing_code_revision",
]
