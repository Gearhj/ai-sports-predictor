# Gate 3 Implementation Plan — MLB Model Training, Calibration & Validation

## Audit boundary and current evidence

This plan was prepared on 2026-08-13 before Gate 3 implementation. The audit
was read-only, stayed inside `C:\Users\james\ai-sports-predictor`, and made no
provider or network calls.

The persisted PostgreSQL development database currently contains:

- 9 `LIVE` MLB games, all scheduled on 2026-08-13 and none final;
- 18 immutable Gate 2 feature snapshots (two versions per game);
- 18 forecasts and 36 market-comparison rows;
- 472 Odds snapshots from the single controlled Gate 2 request;
- 18 team-feature rows, 18 starter observations, 18 rating observations, and
  9 game-state observations;
- zero historical team-game or player-game stat rows;
- zero final score observations;
- zero lineup, bullpen, Statcast, or player-talent observations; and
- no legitimate closing-line history.

Therefore the current governed historical training sample is **zero eligible
labeled games**. Gate 3 will build and test the training system with isolated,
deterministic test fixtures, but the controlled real-data run must stop before
fitting when the persisted dataset remains insufficient. Test fixtures are not
historical evidence and may not be registered as a live trained model.

## Existing implementation audit

### Feature schema and production parity

Gate 2 snapshots use `mlb_point_in_time_v2` and retain a feature version,
knowledge cutoff, evidence identifiers/checksums, source mode, missing/stale/
provisional inventories, Bayesian metadata, and a semantic checksum. The
feature contract includes team scoring and prevention, ratings, probable
starters, lineup and player-talent interfaces, bullpen availability, Statcast,
park/weather, rest/travel, defense, baserunning, and opponent-adjustment fields.

Only features present in a frozen snapshot are eligible for Gate 3. A field
that cannot be reconstructed with the live definition at `data_as_of` remains
missing or is excluded; later mutable `Game`/`GameTeam` state is never used as
a historical feature source.

### Current models and utilities

- The operational ensemble combines a descriptive record-strength baseline, a
  Poisson run component, and a feature-contract-compatible challenger.
- The challenger can accept an estimator, but the active Gate 2 path is a
  deterministic untrained fallback and is labeled as such.
- `xgboost_model.py` fits a real `XGBClassifier`, but its single 70/15/15 split
  is not an adequate Gate 3 selection protocol and must not expose the last
  block as a tunable test set.
- Evaluation currently provides Brier score, log loss, calibration bins,
  chronological splitting, and MAE. It lacks governed rolling-origin folds,
  ECE, RMSE, bootstrap uncertainty, calibration selection, subgroups, OOD,
  ablation, and untouched-holdout enforcement.
- `ModelArtifactStore` writes immutable checksum-verified joblib artifacts, but
  its manifest does not bind the dataset, calibration, ensemble, code revision,
  target definition, or seed policy.
- `ModelVersion` stores a general training window, parameters, metrics, and one
  artifact checksum. It needs governed manifest/evaluation lineage and a
  content-derived identity. The status vocabulary also needs `CANDIDATE`.
- Public performance correctly avoids fabricated results, but it currently
  scores settled public forecasts rather than presenting governed validation
  and holdout evidence from a model-development run.

### Source and market limitations

Historical Odds may be benchmarked only when timestamp-matched moneyline pairs
exist before first pitch for the same eligible games. The current database has
one same-day pregame sample but no outcomes. `is_closing` is not populated with
a legitimate historical closing series, so closing-line and CLV evaluation
remain unavailable. No provider acquisition is authorized for Gate 3 training.

## Governed training dataset

The builder will create at most one row per eligible `LIVE` MLB game. It will
select a frozen feature snapshot with `data_as_of < scheduled first pitch` and
use an append-only final game-state observation solely as the target. Required
row fields are:

- game ID, scheduled first pitch, lifecycle, and `data_as_of`;
- snapshot ID/checksum, feature schema/version, source mode/evidence;
- home and away team IDs;
- the frozen feature values plus explicit missingness indicators;
- `home_win`, home/away runs, run differential, and total runs; and
- pregame market probability/timestamp only for legitimate matched evidence.

Duplicate semantic snapshots for one cutoff are deduplicated. Rows are excluded
for missing/final-invalid outcomes, non-LIVE mode, no pregame snapshot,
post-first-pitch cutoff, schema mismatch, identity mismatch, non-finite targets,
or failed leakage checks. No odds, score, or mutable postgame field is imputed.

## Immutable training manifest

Each dataset build will write an immutable canonical JSONL artifact under the
ignored `.runtime/gate3/` tree and persist a manifest containing:

- content-derived dataset ID and SHA-256 checksum;
- creation time, date range, seasons, row and exclusion counts/reasons;
- feature schema/version and ordered feature names;
- target definitions and source modes;
- training cutoff and source-evidence digest;
- feature-family availability/missingness and mapping coverage; and
- explicit leakage-check results.

An empty or insufficient dataset still produces an auditable diagnostic result,
but it is not a trainable manifest and cannot be linked to a promoted model.

## Targets and model families

Classification target: `home_win`.

Regression targets: `home_runs`, `away_runs`, `run_differential`, and
`total_runs`. Classification and run models will be selected independently.

The bounded candidate set is:

1. historical home-field prior;
2. point-in-time rating logistic baseline (not retroactive current Elo);
3. simple offense/run-prevention run-strength baseline;
4. standardized, regularized logistic regression with training-only
   preprocessing and recorded coefficients;
5. Poisson run regressions plus a Negative-Binomial overdispersion diagnostic
   and predictive-distribution comparison;
6. a real XGBoost classifier using a small validation-only search; and
7. an empirical-Bayesian partial-pooling team-strength classifier as a
   structurally different component.

LightGBM, CatBoost, MCMC, and a broad model zoo are out of scope unless future
evidence establishes a need. Existing scikit-learn, NumPy, XGBoost, and joblib
dependencies are sufficient; no new training library is planned.

## Temporal validation and preprocessing

Games are ordered by scheduled first pitch, with same-day games kept together
when possible. The newest 20% (and at least the configured minimum) is reserved
as the final untouched holdout. The earlier development period uses expanding
rolling-origin folds: train through block A, validate on the next block, then
advance. Hyperparameters, feature families, calibration method, missing-data
strategy, recency weighting, lifecycle policy, and ensemble weights use only
these development folds.

All scaling, imputation, encoding, transformations, and feature selection are
fit inside each training fold. Validation and holdout receive frozen transforms.
The holdout interface is single-use within a training run and records an access
audit. A run with too few rows, too few outcomes per class, inadequate season
coverage, or an undersized holdout stops without fitting or promotion.

Season handling will compare uniform weighting with documented exponential or
season decay on validation only. Offseason reset/league-environment terms are
retained only if multiple seasons exist and validation supports them.

## Calibration and uncertainty

Raw validation probabilities will be evaluated with Brier, log loss, ECE,
reliability bins, slope/intercept where identifiable, and accuracy/ROC-AUC as
secondary diagnostics. Platt and isotonic calibration are fit only to
out-of-fold validation predictions. The selected frozen calibrator is applied
once to the holdout; the calibrator never receives holdout labels.

Uncertainty is not renamed probability. Gate 3 will report ensemble dispersion,
run-distribution width, readiness/missingness, OOD range violations, and
effective sample reliability as separate diagnostics. Bootstrap intervals will
quantify metric and paired-model uncertainty when sample size permits.

## Ensemble selection

Candidate out-of-fold validation probabilities may feed a non-negative,
normalized stacking fit. Weights, intercept policy, seed, and member identities
are persisted. The holdout cannot influence weights. The ensemble is retained
only if it improves the prespecified primary validation criteria without a
material calibration regression; otherwise the best credible single model is
kept.

## Ablation and lifecycle studies

On identical validation folds, Gate 3 will compare:

- season/long-term only;
- raw recent windows, only when the exact point-in-time raw definition is
  present and production-reconstructible; and
- Bayesian/shrunk recent form with reliability metadata.

Feature-family leave-one-out ablations cover starter, lineup, bullpen,
Statcast, recent form, platoon, park/weather, team strength, rest/travel,
defense/baserunning, and opponent adjustment only when the family meets a
prespecified coverage threshold. Sparse families are reported as unavailable,
not assigned a zero-value conclusion.

A unified model with lifecycle/missingness indicators is compared with morning
and late-pregame models only when every lifecycle subset meets minimum training
and holdout counts. Otherwise lifecycle-specific fitting is skipped.

## Benchmarks and secondary research

Every ASP-versus-market comparison uses the exact intersection of games with
valid ASP, market, and outcome evidence. Closing line and CLV remain unavailable
without legitimately captured history. Edge thresholds are selected on
validation only and evaluated unchanged on holdout. Flat stake, fixed bankroll
percentage, and capped quarter-Kelly simulations are secondary research with
bootstrap uncertainty; they cannot drive model selection or activate the
public bankroll experiment.

Subgroups include favorite strength, total bucket, starter hand, lifecycle,
readiness, month, park, team, and market bucket only when sample counts are
large enough. Small groups are labeled descriptive/insufficient.

## Registry, evidence, replay, and identity

New append-only records will persist dataset manifests and evaluation metrics by
model, manifest, split, metric, sample size, subgroup, and timestamp. Model
identity will hash the training manifest, feature version, algorithm, target,
hyperparameters, artifact checksum, calibrator checksum, ensemble members and
weights, simulation/seed policy, and code revision.

Backtest replay will call Gate 2 `get_game_state(game_id, as_of)`, build a
frozen snapshot, score a registered artifact, and write only a DRAFT forecast
labeled `BACKTEST`. Public performance queries will explicitly exclude research
and backtest forecasts.

Artifacts live under ignored `.runtime/gate3/` paths. The database and report
retain checksums and relative URIs. Large trained binaries and generated
datasets will not be committed.

## Promotion criteria

No automatic `CHAMPION` transition is implemented. A candidate may be labeled
`CANDIDATE` only when all of the following are recorded:

1. every leakage and reproducibility check passes;
2. manifest, artifact, calibrator, identity, and code lineage are complete;
3. holdout contains at least 200 eligible games and required subgroups are not
   critically sparse;
4. Brier and log loss beat the home prior and restrained logistic baseline on
   the identical holdout sample;
5. paired bootstrap intervals do not show a material degradation versus the
   best required baseline;
6. ECE is at most 0.05 with no severe calibration slope/intercept failure;
7. run models improve the simple run-strength baseline or are excluded;
8. no major subgroup, OOD, or missingness instability remains unexplained; and
9. an explicit human governance review approves promotion.

Thresholds are policy defaults chosen for material sample and calibration
discipline, not evidence that the current model achieves them. `CHAMPION`
requires a later explicit approval action and is never set by the training CLI.

## Expected risks and controlled-run decision

Primary risks are absent historical outcomes/snapshots, sparse advanced feature
families, survivor and selection bias, season/run-environment shift, mutable
legacy targets, incomplete market matching, calibration overfit, team leakage,
and misleading conclusions from small subgroups or ROI.

The controlled local training run will use persisted data only and make zero
provider calls. With the current zero-row eligible sample, the expected formal
decision is **NO PROMOTION — INSUFFICIENT HISTORICAL EVIDENCE**. Gate 3 code can
be complete and verified while the evidence gate remains correctly closed.
