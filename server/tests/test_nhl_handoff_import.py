"""Handoff-bound NHL import: every producer reference proved from bytes before anything is imported.

Uses the real producer handoff/exchange when present (read-only; copied to tmp for
negative cases); skipped elsewhere.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil

import pytest

from app.nhl.handoff_import import HandoffRefused, verify_handoff

ROOT = pathlib.Path(r"C:\Users\james\asp-model-studio\outputs\nhl-current-slate-producer-20261010-v1")
EXCHANGE = pathlib.Path(r"C:\Users\james\asp-model-exchange\nhl-gen1-research-v1")
PIN = "6d532897f1e6142e4091dd8fc1151f63a1f95c7c93e9815c792b6e5312024df7"
pytestmark = pytest.mark.skipif(not (ROOT / "HANDOFF.json").is_file() or not EXCHANGE.is_dir(),
                                reason="real NHL producer handoff not on this machine")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def test_the_real_handoff_verifies_and_names_twelve_new_and_two_preserved():
    ctx = verify_handoff(ROOT / "HANDOFF.json", PIN, EXCHANGE)
    classes = [m["class"] for m in ctx["membership"]]
    assert classes.count("NEW_CURRENT") == 12 and classes.count("PRESERVED_ORIGINAL") == 2
    assert all(m.get("earlier_manifest_sha256") for m in ctx["membership"] if m["class"] == "PRESERVED_ORIGINAL")
    assert ctx["manifest"]["sha256"] == "bedd8150d977107646e2a799a779288300317a13a85aa08ea8d9e57e4a1b4d79"
    assert ctx["manifest"]["publicationCommittedAtUtc"] == "2026-10-10T19:30:48.114715Z"
    assert set(ctx["producer_evidence"]) == {"3bcbf67177c36691b9a5a779f701ec360f5171a41c743511d4d56bcbc96d3b86"}
    assert {"currentInputRef", "sourceQualificationRef", "independentReviewRef", "publicationReceipt"} <= set(
        ctx["retained_refs"])
    assert all(v is False for v in ctx["authority"].values())


def test_a_wrong_pin_is_refused():
    with pytest.raises(HandoffRefused, match="PIN_MISMATCH"):
        verify_handoff(ROOT / "HANDOFF.json", "0" * 64, EXCHANGE)


def _mutated(tmp_path, mutate):
    """A copy of the handoff (same producer root for its relative refs) with ``mutate`` applied; returns (path, pin)."""

    data = json.loads((ROOT / "HANDOFF.json").read_bytes())
    mutate(data)
    # Absolute refs keep resolving against the real producer root.
    for key in ("publicationReceipt", "currentInputRef", "sourceQualificationRef", "independentReviewRef",
                "sourceCaptureReceipt"):
        ref = data.get(key)
        if isinstance(ref, dict) and not pathlib.Path(ref["path"]).is_absolute():
            ref["path"] = str(ROOT / ref["path"])
    raw = json.dumps(data).encode()
    path = tmp_path / "HANDOFF.json"
    path.write_bytes(raw)
    return path, _sha(raw)


@pytest.mark.parametrize("flag", ["wageringAuthority", "positionAuthority", "decisionAuthority",
                                  "modelPromotionAuthority"])
def test_any_true_or_missing_authority_flag_is_refused(tmp_path, flag):
    path, pin = _mutated(tmp_path, lambda d: d["authority"].__setitem__(flag, True))
    with pytest.raises(HandoffRefused, match="AUTHORITY"):
        verify_handoff(path, pin, EXCHANGE)
    path, pin = _mutated(tmp_path, lambda d: d["authority"].pop(flag))
    with pytest.raises(HandoffRefused, match="AUTHORITY"):
        verify_handoff(path, pin, EXCHANGE)


def test_a_dropped_new_game_breaks_the_declared_counts(tmp_path):
    path, pin = _mutated(tmp_path, lambda d: d["games"].pop())
    with pytest.raises(HandoffRefused, match="MEMBERSHIP_COUNTS_MISMATCH"):
        verify_handoff(path, pin, EXCHANGE)


def test_a_game_not_in_the_manifest_is_refused(tmp_path):
    def mutate(d):
        d["games"][0]["object"]["sha256"] = "f" * 64
    path, pin = _mutated(tmp_path, mutate)
    with pytest.raises(HandoffRefused, match="MEMBERSHIP_MISMATCH"):
        verify_handoff(path, pin, EXCHANGE)


def test_preserved_originals_must_be_proved_by_an_earlier_retained_manifest(tmp_path):
    # Same exchange, but without the earlier manifest: the two originals are no longer proved.
    copy = tmp_path / "exchange"
    shutil.copytree(EXCHANGE, copy)
    current = json.loads((copy / "current.json").read_bytes())["forecastManifest"]["sha256"]
    for d in (copy / "manifests" / "sha256").iterdir():
        if d.name != current:
            shutil.rmtree(d)
    with pytest.raises(HandoffRefused, match="PRESERVED_ORIGINALS_NOT_IN_A_RETAINED_EARLIER_MANIFEST"):
        verify_handoff(ROOT / "HANDOFF.json", PIN, copy)


def test_tampered_producer_evidence_is_refused(tmp_path):
    copy = tmp_path / "exchange"
    shutil.copytree(EXCHANGE, copy)
    ev = next((copy / "evidence" / "sha256").glob("*/producer-receipt.json"))
    ev.write_bytes(ev.read_bytes() + b" ")
    with pytest.raises(HandoffRefused, match="EVIDENCE"):
        verify_handoff(ROOT / "HANDOFF.json", PIN, copy)


# -- MAIN 1da8a50 H1: a pointer advance after verification cannot change what is imported ----------
def test_a_pointer_advance_after_verification_imports_only_the_pinned_manifest(session, tmp_path, monkeypatch):
    from app.models import NhlImportReceipt, NhlResearchForecast
    from app.nhl import handoff_import as hi

    copy = tmp_path / "exchange"
    shutil.copytree(EXCHANGE, copy)
    real_verify = hi.verify_handoff

    def verify_then_advance(handoff_path, pin, root):
        context = real_verify(handoff_path, pin, root)
        # A legitimate producer advance lands between verification and import.
        (pathlib.Path(root) / "current.json").write_text(json.dumps({"schema": "ADVANCED", "forecastManifest": {
            "path": "manifests/sha256/" + "e" * 64 + "/manifest.json", "sha256": "e" * 64}}))
        return context

    monkeypatch.setattr(hi, "verify_handoff", verify_then_advance)
    receipt = hi.import_from_handoff(session, handoff_path=ROOT / "HANDOFF.json", pin_sha256=PIN, exchange_root=copy)
    pinned = "bedd8150d977107646e2a799a779288300317a13a85aa08ea8d9e57e4a1b4d79"
    manifests = {r.manifest_sha256 for r in session.query(NhlImportReceipt).all()}
    assert manifests == {pinned}
    assert {r.kind for r in session.query(NhlImportReceipt).all()} == {"FORECAST"}
    assert session.query(NhlResearchForecast).count() == 14
    assert receipt["verified"]["manifest"]["sha256"] == pinned and not receipt["import"].get("problems")
