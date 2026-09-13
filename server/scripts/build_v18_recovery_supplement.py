"""Build the immutable recovery supplement for the V18 era that never opened.

The original boundary (2026-09-12T04:00:00Z) passed without the era opening,
because ``capture_boundary`` attempted an in-place mutation of an append-only
``ShadowExperiment`` row. The producer's own contract says what to do about a
missed boundary, in the original supplement's own words:

    "Claude accepts and schedules activation before the exact boundary; if
     missed, create a new immutable future-boundary supplement, never backdate"

So this builds exactly that: a new supplement carrying the ORIGINAL frozen
identity unchanged -- same estimator artifact, same champion manifest, same
frozen bindings, same development-integrity and registration receipts -- with a
new future boundary, a new era id, and a recovery block that records what was
intended, what failed, and what is actually happening.

It does not re-fit, re-validate or re-interpret anything. Every hash it writes
is copied from the original supplement after that supplement's own bytes have
been verified against the digest AISP recorded when it imported it.

Usage:
    python server/scripts/build_v18_recovery_supplement.py --effective-at 2026-09-12T16:00:00Z
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_PATH = Path(
    r"C:\Users\james\asp-model-studio\outputs\mlb-v18-conditional-champion-v1"
    r"\aisp-handoff\sha256\a3d6ea3fdb9ddecc42e7b5ecac975d0d132931c0ca7bfc0938bdcef205f2d300"
    r"\champion-handoff.json"
)
ORIGINAL_SHA256 = "a3d6ea3fdb9ddecc42e7b5ecac975d0d132931c0ca7bfc0938bdcef205f2d300"
ORIGINAL_BOUNDARY = "2026-09-12T04:00:00Z"
ORIGINAL_ERA_ID = "ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1"
RECOVERY_ERA_ID = "ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1_R1"

FAILURE_REASON = (
    "ShadowExperiment records are append-only. "
    "V18ChampionService.capture_boundary assigned boundary_capital, "
    "starting_bankroll and is_active on the armed ShadowExperiment row; the "
    "process-wide immutability guard (app/audit/immutability.py) refused the "
    "flush on every scheduled run from 2026-09-12T04:00:00Z onward."
)

#: Blocks copied verbatim from the original. Identity is not re-derived here --
#: it is carried, so a reader can diff the two supplements and see that nothing
#: about the model changed, only the boundary.
CARRIED = (
    "championManifest", "modelIdentity", "frozenBindings", "developmentIntegrity",
    "registrationReceipt", "targetAndFeatureContract", "forecastTimestampSemantics",
    "guardImplementation", "priorArtifactsAndResearchMetadataRemainUnchanged",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--effective-at", required=True,
        help="the new boundary, ISO-8601 UTC, e.g. 2026-09-12T16:00:00Z",
    )
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "outputs" / "v18-era-recovery"),
        help="output directory",
    )
    args = parser.parse_args()

    effective_at = datetime.fromisoformat(args.effective_at.replace("Z", "+00:00"))
    now = datetime.now(UTC)
    if effective_at <= now:
        raise SystemExit(
            f"refusing to build a supplement whose boundary {effective_at.isoformat()} "
            f"is not after now ({now.isoformat()}). Recover forward, never backdate."
        )

    blob = ORIGINAL_PATH.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != ORIGINAL_SHA256:
        raise SystemExit(
            f"the original supplement no longer hashes to the digest AISP imported: "
            f"read {digest}, expected {ORIGINAL_SHA256}"
        )
    original = json.loads(blob.decode("utf-8"))
    original_era = original["modelEra"]

    payload: dict = {
        "schemaVersion": original["schemaVersion"],
        "activation": {
            "authorized": True,
            "performedInAisp": False,
            "historicalIntegrity": original["activation"]["historicalIntegrity"],
            "failClosed": original["activation"]["failClosed"],
            "requiredClaudeImplementation": (
                "Import this recovery supplement and open the era at the new boundary "
                "using the append-only successor mechanism. The armed record of the "
                "original era and every failed boundary attempt are preserved as "
                "historical evidence and must not be edited."
            ),
        },
        "executionAllowedInAISP": False,
        "realMoneyExecutionAllowed": False,
        "modelEra": {
            **{k: v for k, v in original_era.items()
               if k not in {"experimentId", "effectiveAtUtc"}},
            "experimentId": RECOVERY_ERA_ID,
            "effectiveAtUtc": effective_at.isoformat().replace("+00:00", "Z"),
        },
        "recovery": {
            "supersedesEraId": ORIGINAL_ERA_ID,
            "supersedesHandoffSha256": ORIGINAL_SHA256,
            "intendedOriginalBoundaryUtc": ORIGINAL_BOUNDARY,
            "originalBoundaryOutcome": "PASSED_WITHOUT_OPENING",
            "failureReason": FAILURE_REASON,
            "recoveryDecision": (
                "A new immutable future-boundary supplement, per the original "
                "supplement's own activationPreconditions. Activation is NOT "
                "backdated: V18 did not control any decision between "
                f"{ORIGINAL_BOUNDARY} and the new boundary, and every Gen1 decision, "
                "position and settlement created in that window stays with Gen1."
            ),
            "actualEffectiveAtUtc": effective_at.isoformat().replace("+00:00", "Z"),
            "modelIdentityChanged": False,
            "authoredBy": "AISP, under the operator's explicit authorization",
            "authoredAtUtc": now.isoformat().replace("+00:00", "Z"),
            "note": (
                "Every model identity hash in this supplement is copied verbatim from "
                "the original after verifying the original's bytes against the digest "
                "AISP recorded at import. Nothing was re-fitted or re-validated, and "
                "the disclosed historical-validation review is unchanged and still open."
            ),
        },
    }
    for key in CARRIED:
        if key in original:
            payload[key] = original[key]
    payload["idempotencyIdentity"] = (
        f"mlb-v18-conditional-champion-recovery:"
        f"{original['championManifest']['sha256']}:"
        f"{payload['modelEra']['effectiveAtUtc']}"
    )

    out_dir = Path(args.out)
    body = json.dumps(payload, indent=1, sort_keys=True).encode("utf-8")
    supplement_sha = hashlib.sha256(body).hexdigest()
    target = out_dir / "sha256" / supplement_sha
    target.mkdir(parents=True, exist_ok=True)
    path = target / "champion-handoff-recovery.json"
    path.write_bytes(body)

    receipt = {
        "supplementPath": str(path),
        "supplementSha256": supplement_sha,
        "supplementBytes": len(body),
        "recoveryEraId": RECOVERY_ERA_ID,
        "effectiveAtUtc": payload["modelEra"]["effectiveAtUtc"],
        "supersedesEraId": ORIGINAL_ERA_ID,
        "supersedesHandoffSha256": ORIGINAL_SHA256,
        "builtAtUtc": now.isoformat().replace("+00:00", "Z"),
    }
    (out_dir / "recovery-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
