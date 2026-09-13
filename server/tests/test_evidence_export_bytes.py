"""Publishing bytes: hash what is written, not what was intended.

The September 12 diagnostic export published a checksum for every derived file
that no consumer could reproduce. The bytes were produced as a string with LF,
hashed in memory, and then written with ``Path.write_text`` -- which on Windows
translates every "\\n" to "\\r\\n" on the way to disk. Studio measured 3,698 CRLF
replacements in one 76 KB file, and every physical hash disagreed while the
parsed JSON was identical.

The rule these tests hold: a published physical checksum must match the bytes a
consumer reads back, with no newline normalisation on either side. The logical
canonical-document hash is a separate value with a separate name, and the two
must not be conflated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

CANONICAL = {"b": 2, "a": [1, 2, 3], "note": "two lines\nsecond"}


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def readable_bytes(value: dict) -> bytes:
    text = json.dumps(value, indent=1, sort_keys=True, ensure_ascii=True)
    return (text + "\n").encode("utf-8")


def publish(path: Path, value: dict) -> dict:
    """The corrected publication path: bytes in, bytes hashed, bytes verified."""

    payload = readable_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    on_disk = path.read_bytes()
    if on_disk != payload:
        raise RuntimeError("written bytes differ from the bytes handed to write_bytes")
    return {
        "canonicalDocumentSha256": hashlib.sha256(canonical_bytes(value)).hexdigest(),
        "physicalFileSha256": hashlib.sha256(on_disk).hexdigest(),
        "physicalBytes": len(on_disk),
    }


def test_a_consumer_reproduces_the_published_physical_checksum(tmp_path: Path) -> None:
    path = tmp_path / "derived.json"

    published = publish(path, CANONICAL)

    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == published["physicalFileSha256"]
    assert len(raw) == published["physicalBytes"]
    # Verified without normalising anything.
    assert b"\r\n" not in raw


def test_write_text_is_what_broke_it(tmp_path: Path) -> None:
    """Pin the actual failure so the regression is recognisable, not folklore."""

    payload = readable_bytes(CANONICAL)
    intended = hashlib.sha256(payload).hexdigest()

    translated = tmp_path / "translated.json"
    # Text mode with the platform default is exactly what v1 did.
    with translated.open("w", encoding="utf-8", newline=None) as stream:
        stream.write(payload.decode("utf-8"))
    on_disk = translated.read_bytes()

    import os
    if os.linesep == "\r\n":
        assert b"\r\n" in on_disk
        assert hashlib.sha256(on_disk).hexdigest() != intended
        assert on_disk.replace(b"\r\n", b"\n") == payload
    assert json.loads(on_disk) == CANONICAL, "the VALUE always survived; the bytes did not"


def test_the_two_hashes_are_different_values_and_named_apart(tmp_path: Path) -> None:
    published = publish(tmp_path / "derived.json", CANONICAL)

    assert published["canonicalDocumentSha256"] != published["physicalFileSha256"]
    # The canonical hash is stable under reformatting; the physical one is not.
    reformatted = tmp_path / "reformatted.json"
    reformatted.write_bytes(
        (json.dumps(CANONICAL, indent=4, sort_keys=True, ensure_ascii=True) + "\n").encode())
    assert hashlib.sha256(canonical_bytes(json.loads(reformatted.read_bytes()))).hexdigest() == (
        published["canonicalDocumentSha256"])
    assert hashlib.sha256(reformatted.read_bytes()).hexdigest() != published["physicalFileSha256"]


def test_copying_the_file_preserves_the_published_checksum(tmp_path: Path) -> None:
    """Verified again after the copy into the exchange, as the receipt requires."""

    source = tmp_path / "source" / "derived.json"
    published = publish(source, CANONICAL)

    destination = tmp_path / "exchange" / "derived.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())

    assert hashlib.sha256(destination.read_bytes()).hexdigest() == (
        published["physicalFileSha256"])


@pytest.mark.parametrize("value", [
    {"ascii": "plain"},
    {"unicode": "Peña – Suárez"},
    {"nested": {"a": ["x\ny", 1.5, None, True]}},
])
def test_ascii_escaping_keeps_bytes_platform_independent(tmp_path: Path, value: dict) -> None:
    published = publish(tmp_path / "v.json", value)

    raw = (tmp_path / "v.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == published["physicalFileSha256"]
    assert b"\r" not in raw
    # ensure_ascii keeps every byte in the 7-bit range, so no encoding default
    # anywhere downstream can change them.
    assert all(byte < 128 for byte in raw)
