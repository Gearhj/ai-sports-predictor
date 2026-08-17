"""Fail-closed secret and local-path checks for portable bridge artifacts."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


_BANNED_FILE_NAMES = {
    ".env",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "security.db",
}
_BANNED_FILE_SUFFIXES = {
    ".db",
    ".dump",
    ".key",
    ".log",
    ".pem",
    ".sqlite",
    ".sqlite3",
}
_BANNED_BYTE_PATTERNS = (
    re.compile(rb"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:postgres(?:ql)?|mysql|mssql)\+?[^:\s]*://"),
    re.compile(rb"(?i)sqlite(?:\+[^:\s]*)?:///"),
    re.compile(rb"(?i)(?:DATABASE_URL|PASSWORD|API_KEY|ACCESS_TOKEN|SECRET_KEY)\s*="),
    re.compile(rb"(?i)\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"(?i)\b[A-Z]:[\\/](?:Users|ProgramData|Windows|Projects)[\\/]"),
    re.compile(rb"\\\\[^\\\s]+\\[^\\\s]+"),
    re.compile(rb"(?i)file://"),
)
_BANNED_TEXT_FRAGMENTS = (
    "/home/",
    "/users/",
    "\\users\\",
    ".runtime/",
    ".runtime\\",
    "data/curated/",
    "data\\curated\\",
)


class IdentityBridgeSecurityError(ValueError):
    """A package contains a forbidden file, path, credential, or secret marker."""


def validate_package_security(
    root: Path,
    inventory: Iterable[tuple[str, Path]],
    *,
    row_payloads: Iterable[Mapping[str, Any]] = (),
) -> None:
    """Scan the exact package inventory plus decoded row values."""

    governed = Path(root)
    for logical_uri, path in inventory:
        name = Path(logical_uri).name.casefold()
        if name in _BANNED_FILE_NAMES or Path(name).suffix in _BANNED_FILE_SUFFIXES:
            raise IdentityBridgeSecurityError("Package contains a forbidden file type")
        try:
            path.relative_to(governed)
            payload = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise IdentityBridgeSecurityError("Unable to scan package file") from exc
        # Compressed Parquet bytes are arbitrary binary and can coincidentally
        # contain byte sequences that resemble a Windows or UNC path.  The
        # validator has already decoded and structurally validated both
        # Parquet artifacts, so scan their decoded row values below instead of
        # applying text-oriented regular expressions to compressed bytes.
        if Path(logical_uri).suffix.casefold() != ".parquet":
            _scan_bytes(payload)
    for row in row_payloads:
        for value in _walk_values(row):
            if isinstance(value, str):
                _scan_bytes(value.encode("utf-8"))


def _scan_bytes(payload: bytes) -> None:
    if any(pattern.search(payload) for pattern in _BANNED_BYTE_PATTERNS):
        raise IdentityBridgeSecurityError(
            "Package contains a credential or non-portable path marker"
        )
    lowered = payload.lower()
    if any(fragment.encode("utf-8") in lowered for fragment in _BANNED_TEXT_FRAGMENTS):
        raise IdentityBridgeSecurityError("Package contains an ASP runtime path")


def _walk_values(value: Any) -> Iterable[Any]:
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


__all__ = ["IdentityBridgeSecurityError", "validate_package_security"]
