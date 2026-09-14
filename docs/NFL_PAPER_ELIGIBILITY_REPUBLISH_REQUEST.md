# NFL paper eligibility package — one-character republication request

**Status:** the delivered package passes every content gate and fails one byte
gate. Nothing about its evidence, bindings or governance is in question.

**Package:** `nfl-labs-supplements/sha256/391240384408f975febde5c8ac369cd55a814c9d8bd652c4880e5be16aea7c9f/`
· producer commit `144fb92` · published `2026-09-14T17:12:12Z`

## What verifies

| Check | Result |
| --- | --- |
| `manifest.json` bytes | 617, SHA-256 `50c3e4d6…` — exact match |
| `supplement.json` bytes | 34,050, SHA-256 `1587765782…` — exact match |
| `manifest.json` canonical JSON | passes |
| Governance (researchShadowOnly, executionAllowedInAisp, modelPromotionAllowed, wageringAuthority) | passes |
| Binding to the stored forecast (event, engine, forecast id, model artifact SHA) | passes |
| Consumer eligibility gate, run on the parsed document | **PAPER_RESEARCH_ELIGIBILITY_BOUND** |
| Frozen masses 0.5753 / 0.3817 / 0.0430, summing to 1 | passes |
| `supplement.json` canonical JSON | **FAILS** |

## The one failure

`supplement.json` is not in AISP's canonical form. The entire difference is one
character, at byte 4000, inside `experimentName` — the em-dash is delivered as
the six-byte escape `—` where the canonical form uses the three-byte UTF-8
character directly.

AISP's canonical form is:

```python
json.dumps(value, ensure_ascii=False, allow_nan=False,
           sort_keys=True, separators=(",", ":")).encode("utf-8")
```

The producer writer used `ensure_ascii=True`. Delivered 34,050 bytes; canonical
34,047. The three-byte difference is exactly that one character.

The parsed documents are **semantically identical** — verified by comparing the
deserialised objects, which compare equal.

## Why this was never hit before

All 112 previously delivered artifacts under `nfl-labs-simulation-supplements/`
are canonical because **none contains a single non-ASCII byte**, so escaping
never arose. This package is the first to carry one, and it carries it because
the AISP era name the producer correctly transcribed contains an em-dash:

```python
ERA_NAME = "ASP NFL RESEARCH PAPER BANKROLL — 2026 V1"
```

The root cause is therefore shared: AISP chose a name with a non-ASCII
character, and that exposed a producer-side encoding mismatch.

## The fix

Re-encode `supplement.json` with `ensure_ascii=False` and republish. The address
changes, because the address is the content hash:

- new `supplement.json` SHA-256:
  `7fac31dc0bec36ded4b363bc5e8b9bc4a4b8311fa5b671ab32f00820686ffa18` (34,047 bytes)
- update `manifest.json` with that digest and byte count, then recompute the
  supplement address from the manifest's canonical identity.

Nothing else changes. No forecast, model, probability, clock or binding is
affected.

If it would be preferable to avoid the character entirely, AISP can rename the
era to ASCII — but that is a second change on top of this one, not a substitute
for it, since the published document already contains the name.

## What AISP did not do

- **Did not relax the canonical gate.** A container that accepts two byte
  strings for one document is not content-addressed, which is the single
  property this mechanism exists to provide.
- **Did not build a second importer.** The delivered contract does not require
  one and the producer's delivery receipt says so
  (`newSupplementImporterRequiredToActivateExistingPaperPolicy: false`). The new
  kind was admitted explicitly in the existing importer, which is what
  `newKindExplicitConsumerSupportRequiredForGenericImport: true` asks for.
- **Did not mint the record locally.** It is producer evidence.

## Consumer readiness

`PAPER_RESEARCH_ELIGIBILITY` / `ASP_NFL_PAPER_RESEARCH_ELIGIBILITY_V1` is
registered in the existing importer with its own validation, and the eligibility
gate is retargeted to the delivered field paths — including binding
`experimentId` as the stable `AISP_NFL_PAPER_POLICY_V1:NFL:2026` key rather than
an AISP database UUID, exactly as the package specifies. On republication the
package imports and the checkpoint unblocks with no further consumer work.
