"""Filesystem safety primitives for the local immutable exchange."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath

from app.gen2.exchange.contracts import validate_portable_uri


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class ExchangePathError(RuntimeError):
    """A physical or logical path violates the exchange boundary."""


def governed_root(path: Path, *, create: bool = False) -> Path:
    """Return a local, absolute, link-free directory root."""

    lexical = Path(path).absolute()
    raw = str(lexical)
    if raw.startswith(("\\\\", "//")):
        raise ExchangePathError("UNC roots are forbidden")
    if create and not lexical.exists():
        _assert_no_link_ancestors(lexical.parent)
        lexical.mkdir(parents=True, exist_ok=False)
    _assert_no_link_ancestors(lexical)
    if not lexical.is_dir():
        raise ExchangePathError("Governed root is not a directory")
    return lexical.resolve(strict=True)


def resolve_under(
    root: Path,
    logical_uri: str,
    *,
    require_exists: bool = False,
    require_file: bool = False,
) -> Path:
    """Resolve one normalized consumer URI without following an escape link."""

    try:
        validate_portable_uri(logical_uri)
    except ValueError as exc:
        raise ExchangePathError(str(exc)) from exc
    pure = PurePosixPath(logical_uri)
    target = root.joinpath(*pure.parts).absolute()
    try:
        target.relative_to(root.absolute())
    except ValueError as exc:
        raise ExchangePathError("Path escapes its governed root") from exc
    _assert_no_links_between(root, target)
    if require_exists and not target.exists():
        raise ExchangePathError("Required governed path does not exist")
    if require_file and not target.is_file():
        raise ExchangePathError("Required governed path is not a regular file")
    return target


def assert_regular_independent_file(path: Path) -> os.stat_result:
    _assert_no_link_ancestors(path)
    if not path.is_file():
        raise ExchangePathError("Exchange path is not a regular file")
    info = path.stat()
    if info.st_size < 0:
        raise ExchangePathError("Exchange file stat is invalid")
    # A freshly copied package file must not be a hardlink into mutable storage.
    if int(getattr(info, "st_nlink", 1)) != 1:
        raise ExchangePathError("Exchange package files cannot be hardlinked")
    return info


def assert_no_links(path: Path) -> None:
    _assert_no_links(path)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Durably replace one staging metadata file within its existing directory."""

    parent = governed_root(path.parent)
    target = resolve_under(parent, path.name)
    if target.exists() or target.is_symlink():
        _assert_no_links(target)
        if not target.is_file():
            raise ExchangePathError("Metadata target is not a regular file")
    temporary = resolve_under(parent, f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _assert_no_links(target)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def sha256_file(
    path: Path,
    *,
    chunk_size: int = 1024 * 1024,
    on_chunk: Callable[[int], None] | None = None,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(chunk_size), b""):
            digest.update(block)
            if on_chunk is not None:
                on_chunk(len(block))
    return digest.hexdigest()


def iter_relative_files(root: Path) -> Iterator[tuple[str, Path]]:
    """Enumerate only a package tree, rejecting links and case collisions."""

    governed = governed_root(root)
    seen: set[str] = set()
    for directory, names, files in os.walk(governed, followlinks=False):
        base = Path(directory)
        _assert_no_links(base)
        for name in sorted((*names, *files), key=str.casefold):
            _assert_no_links(base / name)
        for name in sorted(files, key=str.casefold):
            path = base / name
            relative = path.relative_to(governed).as_posix()
            validate_portable_uri(relative)
            folded = relative.casefold()
            if folded in seen:
                raise ExchangePathError("Package contains a case-colliding path")
            seen.add(folded)
            yield relative, path


def _assert_no_links_between(root: Path, target: Path) -> None:
    try:
        relative = target.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise ExchangePathError("Path escapes its governed root") from exc
    _assert_no_links(root)
    current = root.absolute()
    for part in relative.parts:
        current /= part
        if current.exists() or current.is_symlink():
            _assert_no_links(current)


def _assert_no_link_ancestors(path: Path) -> None:
    for candidate in (*reversed(path.absolute().parents), path.absolute()):
        if candidate.exists() or candidate.is_symlink():
            _assert_no_links(candidate)


def _assert_no_links(path: Path) -> None:
    if path.is_symlink():
        raise ExchangePathError("Symlinks and junctions are forbidden")
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError as exc:
        raise ExchangePathError("Unable to inspect path safety") from exc
    if attributes & _REPARSE_POINT:
        raise ExchangePathError("Symlinks and junctions are forbidden")


__all__ = [
    "ExchangePathError",
    "assert_no_links",
    "assert_regular_independent_file",
    "atomic_write_bytes",
    "governed_root",
    "iter_relative_files",
    "resolve_under",
    "sha256_file",
]
