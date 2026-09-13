# Ensemble candidate integration — AISP side, 2026-09-12

**Branch** `claude/aisp-v18-and-price-coverage-v1` · **HEAD** `a357435` (working tree, nothing committed)
**Scoring repair** finished and running unattended
**Candidate integration** built, tested and live — **awaiting a package from Model Studio**

---

## 1. The scoring repair is finished

The scheduled job has been running unattended since last night's restart and
has scored everything in range.

| | Last report | Now |
|---|---:|---:|
| Distinct V18 games scored | 25 | **31** |
| `VERIFIED_PROSPECTIVE` | 11 | **17** |
| `QUALIFIED_DIAGNOSTIC` | 14 | **14** |
| `NOT_YET_SCORED` | 6 | **0** |
| Eligible finals in the population | 25 | **31** |

Overnight scheduled runs settle to `written 0, reused 31` — the legitimate
no-new-work result. No forecast identity carries two current evaluations.

**Export regenerated.** Manifest for Codex:

```
C:\Users\james\ai-sports-predictor\outputs\v18-error-analysis-evidence\manifest.json
```

Schema `AISP_V18_ERROR_ANALYSIS_EVIDENCE_V2`, export sha256
`722a7bbb1f8fa1fcc98ff59a3436ed92fc35fa7b9c88304cf6b537dc3e7e54e4`.
Reconciliation: **19 checks, 19 passed**.

The fourteen unresolved publication clocks stay qualified. Nothing was invented
to promote them, and the official gate was not weakened.

---

## 2. The candidate path is built and live

**No candidate package exists yet.** `asp-model-exchange` contains no ensemble
candidate directory, and nothing under `mlb-v18-current` declares one — today's
traffic there is the routine V18 daily slate. So the AISP side was built to the
contract instead, and the contract was published for Codex.

### Contract — agreed fields, published

`docs/contracts/AISP_MLB_ENSEMBLE_CANDIDATE_V1.md`, copied to
`C:\Users\james\asp-model-exchange\contracts\AISP_MLB_ENSEMBLE_CANDIDATE_V1.md`.

It mirrors the V18 daily-slate handoff so Studio's publisher should extend
rather than be replaced. The fields AISP requires:

| Group | Fields |
|---|---|
| Identity | `handoffSha256` (self-address), `bundleId`, `slateDate`, `generatedAt` |
| Artifact type | `artifactType`: `PROSPECTIVE` or `HISTORICAL_REPLAY` |
| Safety | `executionAllowed`, `wageringAuthority`, `activatesModel` — all false |
| Candidate | `engineId` (`MLB_ENSEMBLE_CANDIDATE`), `engineVersion`, `modelArtifactSha256` |
| Baseline | `engineId` (`MLB_V18_WHOLE_GAME`), `engineVersion`, `modelArtifactSha256` |
| Components | per component: `componentId`, `role` (BASE/CALIBRATION/DURATION), `sha256`, `status` |
| Per game | three clocks (`capturedAt`, `publishedAt`, `scheduledStart`), `baselineForecastId`, probabilities, expectations, optional marginal distributions, `simulation` (simulatorId/Version/seed), `eligibility`, `componentsApplied` with `ran` and `fallbackReason` |

### What the importer refuses

Fail-closed, in order, before any payload is interpreted as anything but bytes:

- a handoff that does not self-address;
- any safety flag not false — checked **first**, so a package cannot claim
  authority and be believed later;
- a candidate engine id equal to the baseline's;
- a BASE component whose digest is not the declared baseline artifact — that
  equality is what makes "composed on frozen V18" verifiable rather than asserted;
- a baseline artifact not already registered in AISP;
- a mutated artifact (checksum or byte count);
- a per-game `artifactType` contradicting the handoff's;
- a `PROSPECTIVE` forecast published at or after first pitch;
- a component that did not run and gives no `fallbackReason`.

### Baseline and candidate stay separate

Two engine ids, two sets of rows, never merged. `MLB_ENSEMBLE_CANDIDATE`
alongside `MLB_V18_WHOLE_GAME`. A valid import writes no authority row and
activates nothing — asserted by test.

### Replays cannot become prospective evidence

A replay registers with status `HISTORICAL_REPLAY_RESEARCH_SHADOW`, and the
research scorer refuses it outright with reason
`HISTORICAL_REPLAY_NOT_PROSPECTIVE_EVIDENCE` — rather than relying on its
publication timestamp happening to fall the right side of first pitch. It stays
fully visible in the comparison surface, marked as a replay.

---

## 3. The comparison surface

Added to the existing research surface — the `/games/{id}/shadow-forecasts`
payload gains a `candidate_comparison` block, and `ShadowForecastPanel` renders
`EnsembleCandidatePanel` from it. One endpoint, one round trip, no new page.

Shown: both sides' win probabilities and predicted scores; the difference
(candidate minus baseline); every component with whether it ran and its fallback
reason; evidence cutoff, publication and import times; candidate identity and
hashes; prospective-or-replay status; the scored result once eligible final
evidence exists; and the engine that actually controls official decisions.

**Two things it refuses to show.** It will not split the baseline-to-candidate
difference into per-component probability points — composing a calibration layer
onto a base is not additive, and a "+1.2 from calibration" caption derived by
subtraction would be a fabricated number. Supply `componentAttribution` and it
will render that instead. And a calibration band is described as the calibration
component's own confidence, never as a discovered difficult matchup.

---

## 4. Demonstration

`server/scripts/ensemble_candidate_trace.py` runs the whole path against an
**in-memory SQLite database** — nothing touches production. The package is a
fixture, labelled as one in the script's banner, its output and its filenames.

```
1. STUDIO -> PACKAGE     handoff.json written
2. EXCHANGE VERIFICATION handoffSha256 8357dd05…  artifactType PROSPECTIVE
                         verified games 1   rejected 0
3. AISP IMPORT           imported ['mlb-ensemble-fixture-prospective-825101']
                         bundle created True
4. IDEMPOTENT RE-IMPORT  imported [] (nothing new)
                         already present ['mlb-ensemble-fixture-prospective-825101']
                         bundle created False        candidate rows 1
5. VISIBLE COMPARISON    baseline   55.12%   4.18-4.71 (away-home)
                         candidate  58.12%   4.02-4.91 (away-home)
                         difference +3.0 pts home win, +0.2 home runs
                         V18_WHOLE_GAME            BASE         ran
                         CALIBRATION_ISOTONIC_V3   CALIBRATION  ran
                         STARTER_DURATION_ADAPTER  DURATION     DID NOT RUN
                           fallback: No fitted starter-duration curve for either
                           probable starter
                         attribution   not supplied
                         controlling   AISP_BASELINE
```

`--replay` produces the labelled-replay variant: the comparison still renders,
`prospective_eligible=False`, and the scorer's eligible set contains only the
baseline — `candidate eligible for prospective scoring: False`.

**The fallback case shown above is a fixture, not production evidence.** A
genuine fallback from Codex is one of the outstanding handoff items below.

### Live integration check

Backend restarted through the supported path at 10:26 (one listener, PID 72544,
one task, no second scheduler; 2h45m before the first of today's 15 scheduled
games, nothing in flight). The live endpoint now returns:

```
schema: AISP_MLB_V18_SHADOW_COMPARISON_V1
has candidate_comparison key: True
candidate_comparison value: None
```

Present and correctly inert. In the browser at `/games/4dab9d30-…`, the research
surface renders unchanged, the candidate panel is absent as expected with no
candidate imported, and the console is clean.

### Tests — added only for the new integration

- `server/tests/test_mlb_ensemble_candidate_import.py` — **12 passing**: package
  identity (self-address, mutated artifact, wrong BASE digest, unregistered
  baseline), authority separation (refuses a package claiming execution;
  a valid import creates no authority row and keeps the engine ids distinct),
  idempotent re-import, replay exclusion, fallback reason required.
- `client/src/components/EnsembleCandidatePanel.test.tsx` — **7 passing**: both
  sides and the difference, components with fallback reason, refusal to split
  the difference without an attribution, replay marking, controlling model named,
  scored result rendered, absent baseline reported rather than invented.
- Regression on touched paths: `test_mlb_v18_prospective_scoring.py`,
  `test_nfl_prospective_scoring.py`, `test_mlb_v18_research_shadow.py`,
  `GameAnalysisPage.lifecycle.test.tsx` — all pass. No full suites were run.

---

## 5. Blockers

### The exact missing handoff

**Model Studio has published no ensemble candidate package.** To complete the
trace with a real package, Codex needs to deliver a handoff conforming to
`AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1` (published in the exchange) containing:

1. The **candidate model artifact digest** and engine version.
2. A **BASE component whose sha256 equals the frozen V18 artifact**
   `44ec4f45…88d71c8b` — that artifact is registered here, so a package naming
   it will verify.
3. The **calibration component's** id, digest and tested status.
4. The **starter-duration adapter's** id, digest and status, plus at least one
   game where it genuinely fell back, with the real reason — so the fallback
   display is exercised on production evidence rather than on my fixture.
5. Per game: `canonicalGameId` (without it the row cannot join to a game and no
   comparison is possible), `baselineForecastId`, the three clocks,
   probabilities and expectations, `simulation.simulatorId/Version/seed`,
   `homeTeam`/`awayTeam`/`season`.
6. `componentAttribution` **only if** a valid decomposition exists. Absent it,
   the surface shows the total difference and says the split is unavailable.
7. An explicit `artifactType`. If only a replay can be supplied today, label it
   `HISTORICAL_REPLAY` and the replay path completes without any claim of
   prospective operation.

### A separate operational blocker, not mine to fix

**The V18 model era never opened.** `open-model-era-at-boundary` has been failing
every five minutes since the 00:00 ET boundary with
`ShadowExperiment records are append-only`:

```
2026-09-12 10:27:11  open-model-era-at-boundary  FAILED  ShadowExperiment records are append-only
2026-09-12 10:21:31  open-model-era-at-boundary  FAILED  ShadowExperiment records are append-only
2026-09-12 10:17:51  open-model-era-at-boundary  FAILED  ShadowExperiment records are append-only
```

The authority is still `ARMED` with `execution_allowed_in_aisp = false`, the V18
experiment is still `is_active = false`, and no capital snapshot was taken. The
boundary job appears to be attempting an in-place update of an append-only
`ShadowExperiment` row.

Model activation is explicitly outside this authorization, so I have not touched
it. Flagging it because it means **the official controlling model is still
`AISP_BASELINE` (Gen 1)** and will stay there until someone with that
authorization fixes the boundary job.

---

## Files changed

| File | Change |
|---|---|
| `server/app/shadow_import/mlb_ensemble_candidate.py` | new — verifier and registrar |
| `server/app/shadow_import/mlb_ensemble_candidate_read.py` | new — comparison read model |
| `server/app/services/forecast_evaluation.py` | replay exclusion in `freeze_eligible` |
| `server/app/api/routes.py` | `candidate_comparison` on the existing endpoint |
| `client/src/components/EnsembleCandidatePanel.tsx` | new — the comparison panel |
| `client/src/components/ShadowForecastPanel.tsx` | mounts the panel |
| `client/src/types/shadow.ts` | candidate comparison types |
| `client/src/styles.css` | candidate panel styles |
| `docs/contracts/AISP_MLB_ENSEMBLE_CANDIDATE_V1.md` | new — the contract (also in the exchange) |
| `server/scripts/ensemble_candidate_trace.py` | new — isolated end-to-end trace |
| `server/tests/test_mlb_ensemble_candidate_import.py` | new — 12 tests |
| `client/src/components/EnsembleCandidatePanel.test.tsx` | new — 7 tests |

Nothing committed, nothing pushed, no merge. The duration-pilot exchange
(`mlb-starter-duration-research-v1`) and its Studio owner were not touched. No
provider call, no spend, no bankroll change, no policy or threshold change, no
model activation, no new scheduler process.
