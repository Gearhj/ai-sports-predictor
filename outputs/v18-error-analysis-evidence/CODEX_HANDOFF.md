# Handoff to Codex — consuming the V18 evidence export

**Study id** `AISP_MLB_V18_ERROR_ANALYSIS_EVIDENCE_V1`
**Schema** `AISP_V18_ERROR_ANALYSIS_EVIDENCE_V2`
**Directory** `C:\Users\james\ai-sports-predictor\outputs\v18-error-analysis-evidence\`
**Manifest** `C:\Users\james\ai-sports-predictor\outputs\v18-error-analysis-evidence\manifest.json`

## What changed since the first handoff

The first version of this export said there were **zero scored games**. That is
no longer true, and the reason it was true has been repaired.

The blocker was never missing outcomes. It was that the V18 settlement path
gated on `game_final_outcomes`, which is the **2025 retrospective training-label
store** and receives no 2026 row. The live finals were already sitting in
`game_state_observations` with full provenance — source, observation time,
knowledge time and a payload checksum — and the NFL research scorer already knew
how to read exactly that.

So the repair reused that scorer rather than manufacturing a second final store.
**Scores now exist, and a scheduled job keeps producing them.**

Read `REPAIR_REPORT.md` in this directory for what was changed and why.

## Read this first

Two cohorts, and they are not interchangeable. `cohort` on every record:

| Cohort | Meaning |
|---|---|
| `VERIFIED_PROSPECTIVE` | The forecast's publication time is recorded **separately** from its evidence cutoff. Use these for any claim about prospective accuracy. |
| `QUALIFIED_DIAGNOSTIC` | Publication time and evidence cutoff are the **same stored value**. The forecast is scored — it was published before first pitch by its own record — but that record is not independently evidenced. Equal timestamps neither prove nor disprove it. Diagnostic only. |
| `NOT_YET_SCORED` | No eligible final observation yet. Games still in progress or not started. |

The qualified cohort is not a lower-quality measurement; it is a
lower-confidence *provenance* claim about the same measurement. Do not pool the
two without saying you have. The distinction is also on each score at
`researchScore.publicationClock`.

The counts move as games finish. Take them from `manifest.json`
(`counts.cohorts`), never from this document.

## Files

| File | What it is |
|---|---|
| `manifest.json` | as-of time, selection rules, counts, cohorts, model era, extraction watermark, sha256 of every artifact, schema changelog. **Start here.** |
| `v18_forecast_evidence.jsonl` | one record per distinct game. The primary export. |
| `v18_lineage_blobs.jsonl` | per-game `populationGeometry`, `playGeometry`, `sequentialPath`, keyed by `shadowForecastId`. ~16 MB. Join only if you need them. |
| `omissions.json` | every record that could not be completed, with stage and reason. |
| `evidence_availability_audit.json` | the readiness / input-coverage diagnosis. |
| `reconciliation.json` | invariant checks against the database. |
| `REPORT.md` | the original diagnosis. Superseded on the scoring question; still correct on inventory and readiness. |
| `REPAIR_REPORT.md` | what the repair changed, and the evidence it works. |

Verify before use:

```bash
python server/scripts/v18_export_reconcile.py
```

It re-derives the population independently of the exporter, re-hashes every
file, and checks that the scores reconcile to the database with no forecast
identity carrying two current evaluations. Exit 0 means the snapshot is
internally consistent.

## Record shape

```
identity              canonical game id, MLB gamePk, date, teams, orientation, scheduled start
forecast              forecast/engine/era ids, model artifact + manifest + feature-vector hashes,
                      simulator id/version/seed, bundle key + sha256, contract version
clocks                sourceEvidenceCutoffUtc, publicationTimeUtc, forecastCutoffUtc,
                      firstVerifiableImportUtc, policyCutoffUtc, timingUncertainty{}
eligibility           scientificProspective{}, operationalDecision{ blockingReasons[] }
predictions           fullGame{}, firstFive{}, distributions{}, uncertainty{}
publisherReadiness    V18's own gate and its lineup/starter evidence
aispPregameEvidence   what AISP stored for this game (counts, last-observed times)
outcome               the eligible final: scores, source, tier, observation id, checksum
researchScore         the persisted evaluation -- NEW in V2
cohort                VERIFIED_PROSPECTIVE | QUALIFIED_DIAGNOSTIC | NOT_YET_SCORED -- NEW in V2
decisions / positions annotations, never filters
alternates            other versions of this game's forecast
lineage               scalars inline, bulky payloads referenced by sha256 in the sidecar
```

`researchScore` carries `outcome` (log loss, Brier, probability of actual,
predicted vs actual class), `points` (per-side, margin and total errors, signed
predicted-minus-actual), `distribution` (CRPS on home, away, margin and total,
plus interval coverage), `market`, and `supersededVersions`.

Orientation is stated on every record: **every probability and expected score is
HOME versus AWAY.**

## Six things that will bite if you skip them

1. **`energyScore` is always null for V18, deliberately.** The energy score is a
   joint statistic; V18 publishes marginals. Multiplying the marginals would
   assume the two sides are independent, which is the assumption a whole-game
   simulator exists to avoid. The reason string is on every record under
   `researchScore.distribution.intervalCoverage.energy_score_unavailable_reason`.
   CRPS on each marginal **is** real and is computed exactly, not sampled.

2. **`resultTier` is 2, not 1, and that is correct.** MLB's Stats API is the
   league's own wire, automated and well-identified, but it is not the governed
   odds provider. Tier 1 is reserved for that, and only tier 1 may be described
   as provider-verified. Nothing here grants wagering authority.

3. **`interval_coverage` on one game is a hit or a miss, not a coverage rate.**
   The rate is the aggregate across games. The note saying so travels in the
   payload.

4. **One game can carry several evaluations; exactly one is current.** The
   export gives you the current one and lists the rest under
   `researchScore.supersededVersions` with `supersedeReason` —
   `RESULT_CORRECTED` when the score itself changed, `PROVENANCE_UPGRADE` when a
   better source confirmed the same score. A provenance upgrade does **not**
   increase the count of scored games. Never count superseded rows.

5. **`aispPregameEvidence` describes AISP, not V18.** `bullpenObservations` is 0
   on every game because that AISP table has never held a row. That is not
   evidence that V18 lacked bullpen features — V18's own `readiness_detail`
   cites lineups and probable starters and nothing else.

6. **Zero decisions and zero positions is correct, not a join failure.** The
   model era is `ARMED`, `execution_allowed_in_aisp` false. Scoring is
   measurement; it granted no authority and moved no bankroll. The job's own
   result record asserts `model_era_activated: false` and
   `bankroll_mutated: false` on every run.

## Protected material — untouched

- The sealed **2026-08-14 .. 2026-08-27** Final OOT window was never queried.
  Moot in practice: no V18 forecast exists before 2026-09-07.
- gamePks **823343**, **824235**, **822693** are excluded in the SQL predicate
  itself — in the exporter, and in the scheduled scoring job's population query
  — not filtered out after reading. `reconciliation.json` asserts their absence,
  and a unit test asserts the job never scores one.
- **COL–DET paired measurement** stays with its existing Studio owner and
  scorer. No paired score was created, no protected paired outcome consumed
  through an alternative path, no starter BF acquired.
- `game_final_outcomes` was **not** written to. Its coverage of this population
  is reported in `manifest.trainingLabelStore` so the two provenance kinds stay
  visibly separate.

## As games finish

The scheduled job `evaluate-mlb-v18-research-forecasts` runs every 30 minutes
and scores whatever has become final since. Re-run the exporter to pick the new
scores up — no schema change, and `NOT_YET_SCORED` records become
`VERIFIED_PROSPECTIVE` or `QUALIFIED_DIAGNOSTIC` in place.

```bash
python server/scripts/v18_error_analysis_export.py
```
