# Gate 3 MLB Model Training and Validation Report

## Executive decision

**NO PROMOTION - INSUFFICIENT HISTORICAL EVIDENCE**

Gate 3 adds a provider-free, point-in-time training-data builder; bounded model
development components; immutable dataset, model, and evaluation provenance;
and an honest public offline-evaluation contract. The persisted local sample is
not a model-training sample, however. It contains nine scheduled `LIVE` games,
no `FINAL` games, and therefore zero eligible labels.

No real MLB model was fitted from the local data. No real validation or locked
holdout metrics exist. No Gate 3 model or calibrator artifact was produced, and
no `ModelEvaluation` may be interpreted as evidence of superiority. Synthetic
and fixture-based tests exercise contracts only; they are not historical
baseball evidence and cannot support an accuracy, calibration, market, or
profitability claim.

The expected controlled command result is:

- `status = INSUFFICIENT_DATA`;
- `promotion_decision = NO_PROMOTION`;
- `row_count = 0`;
- `model_version = null`;
- `evaluation_id = null`; and
- `provider_calls = 0`.

This is the correct result at `n=0`, not a failed model experiment.

## Scope and evidence boundary

This report records the Gate 3 implementation boundary in
`C:\Users\james\ai-sports-predictor` on 2026-08-13. It is based on the current
workspace, the pre-implementation plan, the persisted Gate 2 inventory, and the
implemented contracts. Preparing this report made no network or provider calls.

Gate 3 training is deliberately offline. The `train-mlb-models` command reads an
already-migrated database and does not:

- acquire MLB, Odds, weather, or Statcast data;
- construct provider adapters;
- run an ingestion or scheduler job;
- retry or fall back to demo data;
- create an administrator; or
- apply database migrations implicitly.

The operator must apply migrations separately and select an explicit evidence
cutoff for a reproducible run.

## Exact local sample inventory

The current persisted development evidence is:

| Evidence | Count | Gate 3 interpretation |
| --- | ---: | --- |
| MLB games | 9 | All `LIVE`, all on 2026-08-13 |
| Scheduled games | 9 | Pregame context only |
| `FINAL` games | 0 | No settled game target is available |
| Immutable feature snapshots | 18 | Two lifecycle snapshots per game |
| Published lifecycle forecasts | 18 | Two pregame forecast versions per game |
| Market-comparison rows | 36 | Pregame comparison evidence only |
| Odds observations | 472 | From one controlled Odds HTTP request; no matched outcomes |
| Team-feature rows | 18 | Current pregame team context |
| Starter observations | 18 | Current probable-starter context |
| Rating observations | 18 | Current point-in-time rating context |
| Game-state observations | 9 | Scheduled state; none is a final target |
| Historical team-game stat rows | 0 | No completed-game history |
| Historical player-game stat rows | 0 | No completed-game history |
| Final score observations | 0 | No labels |
| Lineup observations | 0 | Feature family unavailable |
| Bullpen workload observations | 0 usable | Feature family unavailable for training |
| Statcast observations | 0 | Statcast was disabled in the controlled run |
| Player-talent observations | 0 | Feature family unavailable |
| Legitimate closing-line series | 0 | Closing-line and CLV analysis unavailable |
| Eligible Gate 3 labeled rows | **0** | Training must stop before fitting |

For the current nine games, the dataset builder cannot find an append-only final
observation and therefore excludes each game as `final_outcome_unavailable`.
The 18 snapshots and forecasts prove lifecycle/provenance mechanics, not model
skill. The 472 Odds observations cannot become a benchmark without eligible
outcomes on the identical game set.

## Governed training-row contract

The builder creates at most one row per eligible MLB game. An eligible row must
combine:

1. a `LIVE` game;
2. an append-only `LIVE` final game-state observation whose `observed_at` and
   `available_as_of` are no later than the selected training cutoff;
3. a published `LIVE` pregame forecast;
4. the exact immutable feature snapshot referenced by that forecast;
5. strict pregame generation, publication, and snapshot cutoffs; and
6. stable game/team/schedule identity between the pregame and final evidence.

Each serialized row records:

- game ID, scheduled first pitch, lifecycle, and feature `data_as_of`;
- snapshot ID/checksum, feature version/schema, source mode, and source evidence;
- home and away team IDs;
- frozen feature values plus missing, stale, and provisional inventories;
- feature completeness;
- `home_win`, home runs, away runs, run differential, and total runs; and
- a pregame no-vig home probability only when a valid paired moneyline snapshot
  exists, plus a closing probability only when an explicitly captured
  `is_closing` observation exists before first pitch.

Missing market evidence remains null. Closing evidence is never inferred from
the latest available line, and current mutable `Game` or `GameTeam` fields are
not used as historical features or outcome authority.

## Leakage controls

The implemented controls fail closed at dataset, preprocessing, temporal split,
calibration, stacking, and evaluation boundaries.

### Dataset controls

- One row per game; duplicate game IDs are rejected.
- Game, forecast, feature snapshot, and accepted source evidence must remain
  `LIVE`; `DEMO` and mixed-mode rows are excluded.
- Targets come only from append-only final observations available by the
  training cutoff.
- The chosen forecast must be published and generated strictly before the
  scheduled first pitch.
- The chosen feature snapshot must be generated with `data_as_of` strictly
  before first pitch.
- The snapshot's schedule identity must match the final observation's scheduled
  identity, preventing a reschedule from joining unrelated cutoffs.
- Feature names containing final-score, winner, home-win, or result tokens are
  rejected.
- Target arithmetic is checked: differential equals home minus away runs, total
  equals their sum, and `home_win` matches the final scores.
- A build uses one feature-schema version and requires a 64-character snapshot
  checksum for every retained row.
- Dataset rows, exclusions, policy, feature names, target definitions, cutoff,
  and leakage results contribute to a content-derived manifest identity.

### Model-development controls

- Games are ordered by scheduled first pitch and grouped by event date.
- Same-date games are not split across development and final holdout boundaries.
- Rolling-origin training dates strictly precede their validation dates.
- Scaling and neutral-value/missing-indicator transforms fit on training rows
  only, retain their fitted row IDs, and cannot be refit silently.
- Feature order is frozen and checksum-addressed; inference fails on order or
  schema mismatch.
- Base learners predict only unique out-of-fold games whose model training
  cutoff is strictly earlier than each predicted game.
- The stacker is itself evaluated by expanding-origin cross-fitting: every
  reported stack probability uses weights fitted on earlier base-OOF folds and
  excludes that game's outcome. The deployable stacker is fitted on all base
  OOF rows only after validation evidence is fixed.
- Calibration selection, validation metrics, subgroup analysis, and paired
  bootstrap deltas use the meta-cross-fitted stack series, never resubstitution
  predictions or final-holdout labels.
- The locked holdout exposes covariates for prediction but permits target-based
  evaluation once per run.
- Research replay stays `BACKTEST`, remains `DRAFT`, makes zero provider calls,
  and is excluded from public forward-performance scoring.

At `n=0`, several manifest checks are vacuously true because there are no
retained rows. That is why row-count and temporal-coverage gates are independent
hard blockers; a true boolean leakage report does not make an empty dataset
trainable.

## Frozen feature contract

The upstream training row retains the exact Gate 2 snapshot schema/version and
full missing/stale/provisional evidence. Model development then projects the
snapshot into the frozen `mlb_pregame_gate3_v1` schema.

The model schema contains 47 named numeric inputs and a paired missingness
indicator for every input, yielding 94 ordered model columns. A configured
neutral value permits a matrix to be constructed, but its paired missing flag
preserves the fact that the source value was not observed. Neutral substitution
must never be described as measured availability or health.

| Family | Frozen inputs |
| --- | --- |
| Rating | Home and away rating |
| Team | Runs scored/allowed per game and OPS for both teams |
| Recent | Home and away 14-day recent runs |
| Starter | Home and away starter ERA and xERA |
| Bullpen | Home/away bullpen ERA, xERA, and availability |
| Lineup | Home/away lineup xwOBA and talent coverage |
| Statcast | Home/away xwOBA, barrel rate, hard-hit rate, and average exit velocity |
| Context | Temperature, wind speed, wind out, precipitation probability, and park run factor |
| Schedule | Days since previous game, travel miles, and doubleheader game number |
| Readiness | Feature completeness and sample reliability |
| Lifecycle | Minutes to first pitch, confirmed lineups, and confirmed starters |

The artifact records the ordered feature names and feature-schema hash. Coverage
is recorded per feature family. Families below the configured threshold are not
eligible for a family-level ablation or promotional interpretation; sparse
families are not awarded zero-valued evidence.

## Model families and current validation boundary

The bounded development library supports:

1. a smoothed historical home-field prior;
2. a point-in-time rating classifier;
3. a simple offense/run-prevention run-strength classifier;
4. standardized regularized logistic regression;
5. paired Poisson home/away run regressions when run targets exist;
6. a Negative-Binomial NB2 predictive distribution with training-fold-only
   dispersion diagnostics and the same restrained regularized log-mean fit;
7. an empirical-Bayesian partial-pooling team classifier when stable team IDs
   exist; and
8. a deliberately limited deterministic XGBoost classifier, enabled only when
   the development sample reaches its minimum size.

XGBoost is not imported by the zero-row controlled path. The Poisson and
Negative-Binomial implementations are exercised only with synthetic fixtures in
this gate; no real-data dispersion or predictive-distribution result exists.

Current automated evidence for these components comes from deterministic,
isolated fixtures. Those fixtures validate schema hashing and missingness,
training-only preprocessing, chronological splits, duplicate and source-mode
rejection, append-only governance, artifact checksums/path confinement, and the
zero-sample hard stop. They do not establish a fitted MLB coefficient, feature
importance, model ranking, calibration curve, or expected return.

## Temporal split policy

When an eligible sample exists, the policy is:

- reserve the latest 20% of date groups as the untouched final holdout;
- keep same-day games together;
- use expanding rolling-origin folds within the earlier development period;
- require every fold's training dates to precede its validation dates;
- tune preprocessing, hyperparameters, feature inclusion, calibration, and
  ensemble weights only from development/validation evidence; and
- access final-holdout labels once after the candidate is frozen.

Configuration defaults require at least 200 eligible rows before fitting, at
least 50 holdout rows for a development evaluation, three rolling folds, a fixed
training seed of `20260813`, 1,000 bootstrap iterations, and 70% feature-family
coverage where a family-level claim is attempted.

With zero eligible rows and only one event date, no development fold, validation
window, or holdout exists. The split fields and metrics must remain null.

## Calibration, stacking, and ablation policy

### Calibration

Identity, Platt, and isotonic candidates are selected entirely within temporal
out-of-fold validation evidence. Earlier validation dates fit each eligible
calibrator and later validation dates select by log loss. The selected method is
then refit on the complete out-of-fold validation series before application to
the still-locked holdout. Flexible isotonic calibration is unavailable below
its minimum sample threshold.

### Stacking

The stacker learns non-negative weights constrained to sum to one. Base-model
predictions are out of fold, and the meta-model is separately cross-fitted over
those folds so validation never scores weights against outcomes used to fit the
same weights. A final deployable stacker is fitted on all base-OOF rows only
after validation and calibration selection are complete. Holdout probabilities
and outcomes cannot choose members or weights. If stacking does not improve the
predefined validation criteria without a calibration regression, it is not
evidence for promotion.

### Ablations

Feature-family, single-feature, recent-window, and lifecycle ablation helpers are
predeclared so comparisons use identical rolling folds. Default bounded studies
cover recent-form and lifecycle families plus lineup xwOBA fields. A feature or
family must satisfy coverage requirements before its ablation can be interpreted.
At `n=0`, every ablation result is unavailable; no omitted family can be called
helpful, harmful, or neutral.

## Metrics and benchmark policy

Probability evaluation includes Brier score, log loss, expected calibration
error, reliability diagnostics, and secondary classification measures. Run
models use run-error metrics such as MAE. Paired, date-block bootstrap deltas and
subgroup/OOD diagnostics are produced only when sample size permits.

Market comparison uses the exact intersection of games with valid model,
pregame market, and final-outcome evidence. Sample sizes accompany baseline
metrics. The current intersection is empty, so market Brier, closing-line value,
ROI, and threshold simulations are unavailable. Secondary betting simulations
cannot select or promote a model and cannot start the public virtual experiment.

## Immutable artifacts and provenance

Gate 3 uses the Git-ignored `.runtime/gate3/` root.

### Dataset artifact

Every build, including an empty diagnostic build, writes an immutable
content-addressed directory containing:

- `rows.jsonl`;
- `exclusions.json`; and
- `manifest.json`.

The manifest records dataset/checksum identity, cutoff, date and season range,
row/exclusion counts and reasons, source modes, feature version/schema/names,
target definitions, source-evidence digest, leakage checks, and feature-family
coverage. Files are staged in a root-confined temporary directory, flushed,
checksum-verified, and atomically moved into place. Reusing an identical identity
is idempotent; an identity collision fails.

### Model artifact

A model directory is created only after the minimum training gate passes. Its
manifest binds the dataset checksum, model algorithm/target, feature names and
versions, temporal windows, hyperparameters, calibrator, ensemble weights,
simulation and seed policies, target definitions, Git lineage plus an exact
local server-source digest, dependencies,
metrics, predictions, and individual SHA-256 checksums. Model names and versions
are constrained to safe path segments and every load verifies root confinement
and hashes before deserializing internally generated joblib content.

Because `n=0`, no real model bundle, calibrator, prediction artifact, or model
version is expected from the controlled run.

### Database provenance

The Gate 3 governance schema adds append-only training-dataset manifests and
model evaluations, and links a model version to its training manifest. Model
identity, code revision, feature names, seed policy, target definition,
calibrator reference, ensemble weights, validation/holdout windows, and promotion
decision remain queryable. SQLAlchemy immutability guards reject mutation or
deletion of manifests and evaluations, and protect model lineage, artifact,
fitted-state, and promotion fields after registration. The legacy forward-
performance metrics field remains mutable for Gate 1 compatibility; governed
offline evidence lives in append-only evaluation rows. Production database
roles/triggers remain recommended defense in depth.

## Promotion gate

The first hard gate is mechanical: fewer than 200 eligible rows returns
`INSUFFICIENT_DATA` before any modeling import or fit. The current sample has
zero, so the decision is unconditionally **NO PROMOTION**.

For a future sufficient sample, the current machine recommendation checks:

- every manifest leakage check passes;
- the final holdout contains at least 200 eligible games;
- holdout Brier and log loss beat the restrained logistic baseline on the same
  games;
- holdout Brier and log loss beat the smoothed home prior on the same games;
- all required feature families meet the declared coverage threshold;
- every required subgroup has at least 30 games, no more than 0.03 Brier or
  0.10 log-loss degradation versus overall validation, and ECE at most 0.10;
- expected calibration error is at most 0.05, absolute calibration intercept
  is at most 0.10, and calibration slope remains between 0.80 and 1.20;
- run forecasts beat the simple run-strength baseline on identical holdout
  games whenever run models are included;
- no major OOD instability remains; and
- date-block paired-bootstrap intervals show no material degradation against
  any retained base model.

Complete model, manifest, feature, code, calibration, and prediction-artifact
lineage is also required before public metrics can become `AVAILABLE`; a broken
reference fails closed and suppresses metrics. Passing every machine check may
create only a candidate-for-review recommendation. Explicit human approval is
still mandatory. The training CLI does not set `CHAMPION`, and public status
remains `DEVELOPMENT` until a separate governance action is implemented and
approved.

## Public API and frontend contract

`GET /api/v1/performance` keeps live forward-performance metrics separate from
the optional `offline_evaluation` object.

The offline object exposes:

- status: `AVAILABLE`, `INSUFFICIENT_DATA`, `NOT_RUN`, or `FAILED`;
- status reason;
- model, dataset-manifest, feature, and evaluation-policy versions;
- rolling-origin fold count;
- training, validation, and locked-holdout date windows and game counts;
- validation and holdout Brier, log loss, ECE, and run MAE;
- matched baselines with their sample sizes; and
- promotion decision: `PROMOTED`, `NOT_PROMOTED`, `BLOCKED`, or `PENDING`, plus
  reason and criteria version.

The contract fails closed:

- no manifest/evaluation returns `NOT_RUN`;
- the current empty manifest returns `INSUFFICIENT_DATA` and `BLOCKED`;
- unfinished or failed records do not publish partial metric tables;
- only a completed immutable evaluation returns `AVAILABLE`; and
- absent `offline_evaluation` remains backward compatible and renders `NOT RUN`
  rather than invented zero metrics.

The Performance page visibly separates **Offline chronological evaluation** from
**Live forward performance**. It shows provenance and the block reason when data
is insufficient, and renders temporal tables/baselines only for `AVAILABLE`.
The Methodology page documents training, validation-only selection/calibration,
the locked holdout, matched comparisons, and explicit promotion. On narrow
mobile screens, the Gate 3 performance cards and evidence grids collapse to one
column while tables retain horizontal scrolling.

## Reproducible provider-free commands

Run from PowerShell. Applying the migration is a separate prerequisite; the
training command intentionally does not modify schema or create users.

```powershell
cd C:\Users\james\ai-sports-predictor\server
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m app.cli train-mlb-models --as-of 2026-08-13T16:00:00Z
```

The fixed cutoff is after the controlled Gate 2 ingestion and before the day's
first scheduled pitch. The command reads persisted evidence only. Its JSON
result includes status, promotion reason, dataset/manifest identity, row and
exclusion counts, artifact URI, runtime, and `provider_calls`.

Targeted Gate 3 backend verification:

```powershell
cd C:\Users\james\ai-sports-predictor\server
New-Item -ItemType Directory -Force ..\.test-artifacts\gate3 | Out-Null
..\.venv\Scripts\python.exe -m pytest `
  tests\test_gate3_model_governance.py `
  tests\test_gate3_modeling_contracts.py `
  tests\test_gate3_dataset_and_artifacts.py `
  --basetemp=..\.test-artifacts\gate3
```

Full repository verification, with providers disabled by the script:

```powershell
cd C:\Users\james\ai-sports-predictor
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

Frontend verification:

```powershell
cd C:\Users\james\ai-sports-predictor\client
npm test
npm run build
```

## Final integrated evidence

- Controlled training dataset ID: `mlb-gate3-e03e6b3008c08bcf`
- Controlled training manifest ID: `617604af-827c-4c01-ae15-5d22e3700aaa`
- Manifest checksum:
  `e03e6b3008c08bcf70d3dee9e657d8c4d46ff884ad276431b3d4ce8c4ab2015a`
- Controlled dataset artifacts: 3 (`manifest.json`, `rows.jsonl`, and
  `exclusions.json`)
- Eligible rows: 0; excluded games: 9, all
  `final_outcome_unavailable`
- Gate 3 model rows: 0; model-evaluation rows: 0; provider calls: 0
- Idempotency: a second final run at the identical cutoff reused the same
  dataset and manifest identity. The database contains two immutable diagnostic
  manifests total: this final hardened identity plus the earlier pre-hardening
  zero-row identity; neither has a model or evaluation.
- Backend suite: 191 passed with integrations disabled
- Frontend suite: 40 passed; production build passed
- Browser QA run: `gate3-browser-20260813-02`
- Browser evidence: 4 screenshots across Performance and Methodology at
  1280x720 and 390x844; no console warnings/errors or page-level horizontal
  overflow; the mobile navigation interaction passed

The controlled status was `INSUFFICIENT_DATA / NO_PROMOTION`, exactly as the
pre-run evidence audit predicted.

## Limitations

- There is no legitimate historical labeled training sample in the workspace.
- The local evidence covers one event date and one season only.
- No real temporal fold, calibrator, stack, ablation, bootstrap interval,
  subgroup, or OOD result exists.
- No real Gate 3 model, calibrator, prediction bundle, or evaluation has been
  produced.
- Sparse lineup, bullpen, Statcast, recent-form, travel, defense/baserunning, and
  opponent-adjustment history prevents those feature families from being judged.
- There is no legitimate historical closing-line series or CLV analysis.
- The existing live forward forecasts use a deterministic untrained development
  challenger; they are not a Gate 3 trained model.
- Synthetic and demo values are isolated and may not enter a `LIVE` manifest,
  public performance claim, or promotion decision.
- A joblib artifact is safe to load only after root confinement and checksum
  verification and only when produced by this trusted internal pipeline.
- Provider terms, retention, and redistribution review remains separate from
  model validity.

## Next data milestone

The next milestone is not another synthetic score. It is a governed history of
settled `LIVE` games with both an immutable pregame snapshot and an append-only
final outcome.

Operational milestones are:

1. reach at least 200 eligible labeled games across enough date groups and both
   outcome classes to permit the first development fit;
2. preserve at least 50 latest eligible games for a development holdout;
3. continue capture until the locked holdout alone contains at least 200 games
   for promotion review (approximately 1,000 total rows at a 20% holdout,
   subject to date grouping and exclusions);
4. expand across multiple seasons before making stability or environment-shift
   claims;
5. record feature-family coverage and preserve missingness instead of
   backfilling unavailable historical fields; and
6. capture timestamp-matched market and legitimate closing observations only
   under reviewed provider/storage terms if market-relative research is desired.

Until those milestones are met, the honest Gate 3 public state is
`INSUFFICIENT_DATA`, promotion is `BLOCKED`, and the model remains
`DEVELOPMENT`.
