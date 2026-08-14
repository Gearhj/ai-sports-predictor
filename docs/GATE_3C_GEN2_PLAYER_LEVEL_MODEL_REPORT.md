# Gate 3C Generation 2 Player-Level MLB Model Report

**Report date:** 2026-08-14

**Baseline commit:** `0e83c521a1dbfbc7bb0deae50b4e55c2a9c178bb`

**Terminal status:** `PROVIDER_FREE_FOUNDATION_COMPLETE / BLOCKED_SOURCE_AUTHORIZATION`

**Model status:** `GEN2_RESEARCH_NOT_FIT`

**Promotion status:** `NOT_PROMOTED`

**Provider calls made by Gate 3C:** `0`

## Executive Result

Gate 3C produced a tested, provider-free Generation 2 research foundation. It
did **not** produce a Generation 2 model, historical evaluation, prospective
evaluation, or live Generation 2 forecast. That distinction is the governing
result, not a documentation caveat.

Implemented evidence includes immutable event-partition contracts, atomic
Zstandard Parquet publication, source/acquisition governance records, a strict
eight-class plate-appearance taxonomy, local MLB play-by-play canonicalization,
one-day Statcast alignment, chronological player-state primitives, participant,
usage, park/weather, count-model, base/out, simulator, calibration, and
prospective-governance contracts, plus database-enforced separation between
operational and Generation 2 research forecasts.

Acquisition Stages A-G were not started. The source authorization gate remains
closed, no 2023 or 2024 history was acquired, no multi-season event warehouse
was created, and no Gen-2 fitting or evaluation was attempted. The existing
one-day Statcast archive remains Tier B local research evidence with unproven
historical availability; it was parsed and validated without changing its
eligibility.

| Release fact | Result |
| --- | --- |
| Local PostgreSQL migration | `20260814_0012 (head)` |
| Alembic schema drift | none |
| New Gate 3C governance/event rows | 0 |
| Forecast tracks after backfill | 42 `CURRENT_DEVELOPMENT`; 0 Gen-2 |
| Generated Parquet | 0 files / 0 bytes |
| Gen-2 model, evaluation, forecast, or position | none |
| Gate 3C tests | 122 passed; 1 Windows symlink test skipped |
| Full backend executable test bodies | 548 passed; 1 platform skip |
| Client tests/build | 47 passed; production build passed |
| Final Gate 3C decision | blocked/partial; remain in Gate 3C |

## Generation 1 Diagnosis

Generation 1 remains the immutable retrospective research challenger from Gate
3B. Its 505-game consumed 2025 holdout produced Brier
`0.24189382768082518`, log loss `0.6766197598504301`, and 407 `CAUTION`
OOD rows. It was not promoted and it has no forecast references.

Gen-2 therefore targets a different scientific representation: chronological
player posterior state, explicit participant and usage distributions,
plate-appearance events, base/out transitions, and joint score simulation. No
Gen-2 result exists yet with which to claim that this architecture improves on
Generation 1.

## Source Authorization and Provider Accounting

The [Baseball Savant CSV documentation](https://baseballsavant.mlb.com/csv-docs)
links to the current [MLB.com Terms of Use](https://www.mlb.com/official-information/terms-of-use).
The terms review did not establish defensible authorization for the requested
automated multi-season acquisition. Project-owner approval is not provider
permission and is not a legal conclusion.

Accordingly:

- Gate 3C made zero MLB Stats API, Baseball Savant, Odds, weather, or other
  provider requests;
- no adapter was constructed and no acquisition checkpoint was opened;
- owner approval, provider authorization, and existing-evidence derived-use
  decisions are represented as separate append-only record types;
- their live tables contain zero rows; default behavior is denial;
- the local source-authorization module is foundation code, not an operational
  authorization service.

Before acquisition can be enabled, a trusted persistence-backed decision
resolver must bind provider, source version, checkpoint, manifest, expiry, and
revocation at the adapter boundary. Cross-source relational constraints and raw
file read/URI verification also need to be closed. In-memory caller-created
decision objects are not accepted as evidence of real permission in this
report.

## 2023-2025 Historical Population

| Season | Official finals | Team-game rows | Player-game rows | Actual lineup batters | Starters | Relief appearances |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2023 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025 | 2,430 | 4,860 | 127,872 | 43,740 | 4,860 | 16,010 |

The 2025 outcomes remain 2,428 Tier A and two Tier B rain-ended games. Historical
pregame roster, canonical Statcast, bullpen-availability, park, weather,
platoon, defense, and baserunning coverage remains zero in the registered
warehouse. The post-migration provider-free warehouse report made zero provider
requests and reconciled all 2,430 official finals.

## Pitch and Plate-Appearance Warehouse

The provider-free contracts define:

- `mlb_gen2_pitch_event_v1`;
- `mlb_gen2_plate_appearance_v1`;
- `mlb_gen2_event_partition_manifest_v1`;
- monthly `season/month/family` Zstandard Parquet partitions;
- atomic read-back, schema, row-count, checksum, path, collision, capacity, and
  symlink/reparse-point validation;
- a frozen, non-secret raw-contributor metadata schema.

No Parquet partition was published. Provider-free validation of the retained
2025-06-17 inputs succeeded for all 15 games:

| Measure | Exact result |
| --- | ---: |
| Games canonicalized/aligned | 15 / 15 |
| Plate appearances | 1,140 |
| Modeled terminal PAs | 1,139 |
| Proven truncated PA exclusions | 1 |
| Count events | 4,375 |
| Physical pitch events | 4,354 |
| Automatic balls / strikes | 20 / 1 |
| Structural runner placements | 2 |
| Null movement placeholders suppressed | 1 |
| Dropped-third safe reaches | 1 |
| Pinch-runner substitutions | 2 |

Checksums:

- retained input archive envelope:
  `601af8d47af4dfe66102efd4df38ecd324e8ebf38449ddc8eb0c708337e79f2d`;
- exact response bytes:
  `932d42bec7a1536374648ae6bc99827f31267bdd56f02db1955638e24ae3b055`;
- retained metadata payload:
  `bcaa171d4adfb8666ab3ee85eb0490710103825ecf973f8b07a80e55c53a4251`;
- canonical game manifest:
  `a3fecfeecc930dec7c2d47f42ca800b90bf0b84fb8c6b19e6aef16b60771660c`;
- Statcast alignment manifest:
  `961f64dd5754f7002e59728c779184c8636a67e737b82e4d45a569f6dfda07af`.

This retained-day result is a parser/alignment proof, not a strict training
dataset. Full-season readiness is still blocked by source authorization and by
unreconciled coverage for rare terminal tokens, zero-pitch runner outs, legal
mid-PA substitutions, and complete official-date/game-type semantics.

## Roster / Transaction / Lineup Evidence

Provider-free participant snapshots now bind target game/date, roster status,
positions, scratches, replacements, observation timing, source identity, and
row-associated checksums. Strict pregame and
`HISTORICAL_ACTUAL_LINEUP_TIMING_RISK` populations remain separate.

Historical roster rows remain zero. Therefore the retrospective actual-lineup
population cannot be promoted into a defensible pregame participant population,
and no lineup-driven Gen-2 model was fit. Transaction and roster ingestion also
remain blocked with acquisition.

## Player True-Talent Model

The provider-free library implements hierarchical Dirichlet, Beta, and normal
posterior primitives with role separation, chronological cutoffs, source
identities, posterior uncertainty, effective sample, and one/two/three-season
prior candidates. Hitter and pitcher identities persist across team changes.

No multi-season prior population exists, no posterior table or Parquet state
was materialized, and no true-talent coefficients or predictive metrics are
claimed.

## Dynamic Player State

Static and exponentially weighted empirical-Bayes candidate builders exist for
predeclared 14/30/60-day half lives. They require governed static-state lineage,
strict cutoffs, finite typed observations, and uncertainty.

Variants A/B/C were not evaluated on temporal folds. Variant C is unavailable
without governed underlying-skill evidence; change-point Variant D remains
deferred.

## Bayesian Recent-State Evaluation

No Bayesian recent-state variant was selected. The implementation exposes the
candidate mechanics and chronology tests only. The required event-and-game
retention rule, fold-consistency gate, and historical score-distribution review
have no data on which to operate.

## Platoon Model

Partial-pooling primitives combine governed batter and pitcher split estimates
on the logit scale and carry cutoff/source lineage and propagated uncertainty.
Raw batter-versus-pitcher history is not used.

No historical platoon posterior population was built or evaluated.

## Hierarchical Event Baseline

The eight-class taxonomy `mlb_gen2_pa_outcome_taxonomy_v1` produced the exact
retained-day counts below:

| Outcome | Count |
| --- | ---: |
| Strikeout | 237 |
| Walk / HBP / catcher interference | 122 |
| Home run | 43 |
| Single | 151 |
| Double | 51 |
| Triple | 3 |
| Ball-in-play out | 524 |
| Other reach | 8 |

A regularized multinomial baseline contract, feature order, fold identity,
temporal evidence checksum, and coefficient-evidence representation are
implemented. No baseline was fit, so there are no coefficients, latent player
effects, or event metrics to register.

## Boosted Event Model

A restrained multiclass XGBoost challenger contract exists with deterministic
feature ordering and training-evidence checks. CatBoost and LightGBM were not
added. No XGBoost model was fit or compared.

## Starter-Duration Model

Discrete-time starter-exit hazard and duration-distribution primitives enforce
current-game exclusion, temporal/source/tier lineage, monotone workload state,
and prediction checksums. No starter-duration model or Negative Binomial
duration comparator was fit.

## Bullpen Availability and Usage

Player-level bullpen availability and weighted-pool usage contracts distinguish
observed availability from `WORKLOAD_DERIVED_AVAILABILITY_PROXY`, require
fresh/game-applicable evidence, prevent starter/reliever duplication and pitcher
re-entry, and retain profile lineage.

The historical warehouse has no governed bullpen-availability snapshots. No
bullpen usage model was fit or evaluated.

## Park / Weather / Context

Prior-period park-factor records now bind validity intervals, contributing
games, sample, uncertainty, computation time, and source evidence. Weather wind
orientation requires a governed park-bearing record rather than a bare angle.

No 2023-2025 park-factor population or historical weather population was built.
These context families remain missing, not neutralized into measured evidence.

## Base-Out Transition Model

The foundation models 24 ordinary base/out states plus inning end, with
Dirichlet-smoothed exact-state transitions, temporal/source/tier cutoffs,
current-game exclusion, and state/run/outs conservation. It does not transplant
absolute runner masks between incompatible states.

The retained-day parser exercised automatic runners, pinch runners, dropped
third strikes, and structural runner evidence, but no historical transition
posterior was fit or held-out inning evaluation performed.

## Game Simulator

The deterministic event simulator implements batting-order cycling, starter
exit, availability-weighted reliever selection without re-entry, bottom-half
skips, non-home-run walk-off run clipping, home-run walk-offs, regular-season
automatic runners, extra innings, hard termination, and checksum-bound game,
line, provider, and transition inputs.

Unit and invariant fixtures passed. The required fitted-event simulation,
one-million-draw frequency check, historical score-distribution validation,
50,000-draw live latency, and peak-RSS benchmark were not run. The simulator is
not live-ready.

## Count Baselines

Provider-free independent Poisson, NB2 diagnostics, and a shared-environment
joint count comparator are implemented. Joint distributions expose/fail on
excess truncated tail mass and bind training windows, source games, cutoffs,
tiers, and evidence checksums.

No count model was fit on a Gen-2 population and no overdispersion or home/away
dependence result is claimed.

## Residual ML

`mlb_gen2_residual_xgboost_v1` was not implemented or fit. It remains downstream
of governed historical OOF simulator evidence.

## Calibration

Typed out-of-fold multiclass predictions, training-population exclusion proofs,
and a temperature-calibration contract are implemented. No event temperature,
identity/Platt/isotonic/beta probability calibrator, or persisted calibration
comparison was fit for Gen-2.

## Ensemble Selection

The required nonnegative temporal stack and keep/drop orchestration were not
implemented. There are no Gen-2 OOF component predictions or ensemble weights.

## Historical Rolling Evaluation

No rolling-origin evaluation ran because governed 2023-2024 history and
multi-season event partitions do not exist. No 2025 evidence is labeled
untouched; the Generation 1 505-game holdout remains consumed retrospective
evidence.

## Generation 1 Comparison

No row-matched Gen-2 comparison exists. Generation 1 remains
`RESEARCH_CHALLENGER / NOT_PROMOTED`; Gen-2 remains not fit. Any claim that
Gen-2 improved Brier, log loss, calibration, OOD, or score distributions would
be unsupported.

## Event-Level Metrics

Available: deterministic taxonomy/alignment counts and parser invariants for one
retained Tier B day. Unavailable: multiclass log loss, classwise Brier,
calibration, fold stability, and boosted-versus-hierarchical comparison.

## Game-Level Metrics

Unavailable: Gen-2 Brier, log loss, ECE, calibration slope/intercept, AUC,
accuracy, run MAE/RMSE, and date-block bootstrap comparisons.

## Score-Distribution Metrics

Unavailable: ranked probability/CRPS, interval coverage, score variance,
zero-run and high-run tails, extra-inning frequency, and home/away/total/margin
distribution errors.

## OOD Diagnosis and Replacement

The Generation 1 baseline remains 98 `NORMAL`, 407 `CAUTION`, and 0
`LOW_RELIABILITY` on its frozen holdout. A Gen-2 OOD policy version is declared,
but the feature/family attribution pipeline, replacement detector, and
row-level Gen-2 statuses were not implemented or evaluated. No threshold was
relaxed.

## Prospective 2026 Evaluation Policy

Migration `20260814_0011` and the provider-free contracts implement immutable
plans, global occurrence assignment, exact forecast/target bindings, 300/500
checkpoint identities, one-shot label-access consumption, typed revision
envelopes, and canonical payload commitments. Label access is consumed before
the loader runs, and reordered, substituted, or payload-tampered labels fail
closed.

The protocol is not operational. It still requires a predeclared complete game
universe/census, a trusted immutable Forecast-to-snapshot adapter, authoritative
target-revision foreign-key lineage, independently proven FINAL T-10 timing,
and a persisted terminal decision record. The live prospective tables contain
zero plans, claims, bindings, assignments, or label accesses.

## Live Integration

Migration `20260814_0012` adds `forecast_track` with exact values
`CURRENT_DEVELOPMENT` and `GEN2_RESEARCH_CHALLENGER`. Operational forecast
creation, movement, Today, Health, Performance, positions, settlement, and
refresh reads hard-filter the current track. The downgrade refuses to remove
the boundary while any non-current row exists.

All 42 existing forecasts were backfilled as `CURRENT_DEVELOPMENT`. There are
zero Gen-2 forecasts, zero Gen-2 live snapshots, and zero Gen-2 positions.
Official positions remain disabled. No client page was added for an unfit
model.

## Runtime and Storage

| Measure | Terminal provider-free result |
| --- | ---: |
| PostgreSQL bytes | 775,545,359 |
| Raw bytes/files | 329,219,861 / 2,625 |
| Runtime bytes/files | 677,513,706 / 58 |
| Parquet bytes/files | 0 / 0 |
| C: free space | 291,613,941,760 bytes / 271.587 GiB |
| Event training time | not run |
| Player-state build time | not run |
| Simulation latency/RSS | not benchmarked |
| Gen-2 artifact size | 0 |

The 100 GiB reserve was preserved. Client build output, runtime data, raw data,
databases, model artifacts, and temporary test output remain ignored and are
not release files.

## Test Results

- `pytest tests -q -k gate3c`: 122 passed; one Windows-only symlink/reparse test
  skipped.
- Full backend collection: 549 tests. The bulk sandbox run completed 546 test
  bodies, skipped the same symlink test, and encountered two Windows temp-folder
  ACL setup errors. Both affected provider-free test bodies passed in isolated
  elevated reruns, so all 548 executable test bodies passed.
- Touched-core API, forecast-lineage, settlement, training-dataset, lifecycle,
  performance, and bankroll regressions: 66/66 passed.
- Client: 47/47 tests passed.
- Client production build: passed; the existing large-chunk warning remains
  informational.
- Python compileall for Gen-2 and migrations: passed.
- Alembic: sole head `20260814_0012`; live upgrade passed; `alembic check`
  reported no new operations.

Tests prove bounded contracts and retained-fixture behavior. They do not replace
the missing acquisition, multi-season materialization, model fitting,
historical evaluation, prospective evidence, or live runtime benchmarks.

## Model Decision

**Decision:** `GATE_3C_BLOCKED_SOURCE_AUTHORIZATION / GEN2_RESEARCH_NOT_FIT`.

| Gate 3C success criterion | Status |
| --- | --- |
| 1. Governed usable 2023-2025 history | not met |
| 2. Multi-season pitch/PA partitions | not met |
| 3. Chronological player true talent | primitive only |
| 4. Underlying-skill dynamic state | primitive; underlying source blocked |
| 5. Participant-driven state | contract only; historical roster absent |
| 6. Probabilistic starter duration | primitive only |
| 7. Player-level bullpen usage | primitive only |
| 8. Calibrated event probabilities | not fit |
| 9. Validated simulator/distribution | engine only; not statistically validated |
| 10. Count baselines | primitives only |
| 11. Residual ML | not implemented |
| 12. Temporal OOF ensemble | not implemented |
| 13. Honest consumed-2025 label | met as governance disclosure |
| 14. Immutable prospective plan | schema/contract only; no live plan |
| 15. Separate-track Gen-2 lineage | storage/read boundary implemented; no rows |
| 16. No unsupported claims | met |
| 17. Official positions disabled | met |
| 18. Evidence-driven Gen-2 decision | blocked/not-fit decision recorded |

No model registry row, research forecast, promotion decision, superiority claim,
or profitability claim was created.

## Remaining Risks

1. Provider acquisition authorization is unresolved; no automated acquisition
   may start.
2. Source authorization still needs a trusted database resolver, adapter-bound
   proof, tighter cross-source foreign keys, and raw-byte/URI verification.
3. 2023 and 2024 canonical history is absent.
4. The retained-day canonicalizer does not prove full-season coverage for every
   rare terminal, zero-pitch, substitution, official-date, and game-type shape.
5. Historical pregame rosters, transactions, bullpen state, weather, park, and
   strict Statcast remain absent.
6. No player states, event/count models, transitions, OOF predictions,
   calibrators, residual model, stack, or OOD replacement were materialized.
7. Simulator statistical accuracy and runtime/RSS limits are unproven.
8. Prospective universe, live forecast binding, target ledger integration, and
   terminal decision persistence are incomplete.
9. The retained Statcast evidence remains Tier B and cannot enter strict
   training or evaluation.

## Recommendation for Gate 4

**NO-GO for Gate 4.** Keep the project in Gate 3C.

The next authorized work should be governance, not acquisition: obtain and
record defensible source permission (or a lawful manual-export workflow), close
the trusted authorization/lineage boundary, and run a bounded provider-free
import pilot. Only after that pilot passes may the staged acquisition plan begin.
Multi-season materialization, fitting, OOF evaluation, freeze, and prospective
registration must then proceed in the frozen order. A blocked source gate must
not be converted into a model-success claim.
