"""Installed Python sources carry no UTF-8-as-cp1252 mojibake (review 2026-10-23, HIGH-2).

WHAT HAPPENED
-------------
A release tool read UTF-8 sources as cp1252 and wrote them back as UTF-8, so
every en dash, em dash and middle dot in user-facing readiness labels became
``Ã¢â‚¬â€œ``, ``â€”`` and ``Ã‚Â·``/``Â·``. Live ``/api/v1/sports/readiness`` showed
``"Decision window ... 6:30 PM ET Ã¢â‚¬â€œ 6:45 PM ET"`` on every sport card.

THE CHECK
---------
The bytes of every module under ``app/`` are scanned for the three signatures
that mis-decoding produces. Each signature begins with a byte pair that no
English label, identifier or comment in this tree legitimately contains, so the
scan is exact for the damage that occurred and does not depend on which
characters a future label happens to use. The two modules the review named are
asserted by name as well, so a regression there is reported by file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

#: (signature bytes, what it is the wreck of)
MOJIBAKE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xc3\x83", "a double-encoded character ('Ã...': en dash, middle dot read twice as cp1252)"),
    (b"\xc3\xa2\xe2\x82\xac", "a once-encoded dash ('â€...': en/em dash read once as cp1252)"),
    (b"\xc3\x82\xc2\xb7", "a once-encoded middle dot ('Â·')"),
)

NAMED_MODULES = (
    APP_ROOT / "services" / "sport_readiness.py",
    APP_ROOT / "main.py",
)


def _hits(path: Path) -> list[tuple[int, str, str]]:
    data = path.read_bytes()
    found: list[tuple[int, str, str]] = []
    for number, line in enumerate(data.split(b"\n"), 1):
        for signature, meaning in MOJIBAKE_SIGNATURES:
            if signature in line:
                found.append((number, meaning, line.decode("utf-8", "replace").strip()[:120]))
    return found


@pytest.mark.parametrize("path", NAMED_MODULES, ids=lambda p: p.name)
def test_the_named_modules_carry_no_mojibake(path: Path):
    assert path.is_file()
    assert _hits(path) == []


def test_no_installed_module_carries_mojibake():
    offenders = {str(path.relative_to(APP_ROOT.parent)): hits
                 for path in sorted(APP_ROOT.rglob("*.py")) if (hits := _hits(path))}
    assert offenders == {}


def test_the_restored_labels_use_the_real_characters():
    source = (APP_ROOT / "services" / "sport_readiness.py").read_text(encoding="utf-8")
    assert "–" in source, "the decision-window label uses an en dash"
    assert "·" in source, "the component labels use a middle dot"
    assert "—" in source, "the awaiting-V18 label uses an em dash"
