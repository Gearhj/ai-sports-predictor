# Gate 3B Implementation Plan

## Purpose and authorization

Gate 3B develops one bounded Generation 1 MLB research model from the already
governed 2025 strict Tier A warehouse, exposes that warehouse separately from
live product data, and performs one deliberate current-day live refresh. It
does not relax the existing promotion gate, acquire another historical season,
expand Statcast, infer historical markets, publish bankroll positions, or
authorize a CHAMPION model.

The research policy identity is `MLB_GEN1_RESEARCH_POLICY_V1`. It is separate
from `gate3-promotion-v1`. Passing it can produce only a research decision:
`RESEARCH_CANDIDATE`, `RESEARCH_CHALLENGER`, or `REJECTED_GEN1`.

## Pre-implementation audit

Recorded on 2026-08-14 before Gate 3B source changes or provider calls:

| Item | Baseline |
| --- | --- |
| Git root | `C:/Users/james/ai-sports-predictor` |
| Branch / HEAD | `main` / `ce5efb3b6e14954d3f4685caebac3585bc340b7e` |
| Worktree | clean |
| Strict dataset | `mlb-gate3-a2-3059f81db1d0e278` |
| Strict manifest DB ID | `d4087299-8a04-4176-bad2-a9ec19128b62` |
| Strict manifest identity | `3059f81db1d0e2783d0357cf765fbc027958a1d0786c8da83611bae82dca1748` |
| Strict rows / excluded | 2,428 / 0, all Tier A |
| Enhanced shadow | 2,430 Tier B rows; research-only and training-ineligible |
| Event-date groups | 184 |
| Feature schema | `mlb_historical_pregame_a2_v2` |
| Feature version | `a2-tier-a-strict-v6` (`7cdfa80f-725d-4061-8ca5-5967d9ea5701`) |
| Schema checksum | `e29cf4f37487590ff09266f189323f9c42f9c8e99f0d64d8d61bdf41d7b9b9ff` |
| Values / paired outputs | 272 / 544 |
| Existing model registry | one untrained `asp_mlb_ensemble` DEVELOPMENT model; no model evaluations |
| Existing Gate 3 behavior | promotion-oriented pipeline, currently blocked by intentionally strict missing-family coverage |
| Current live data | nine stale August 13 LIVE games; Today for August 14 has zero games |
| Public performance | zero forward games; offline evaluation `NOT_RUN` |
| Scheduler / event scheduler | disabled / disabled |
| Odds polling / Statcast / positions | disabled / disabled / disabled |
| Weather | enabled for explicit refresh only |
| DEMO setting | enabled, but controlled live path must reject DEMO rows |
| Odds key | configured; value not inspected or disclosed |
| Odds budget / floor | one controlled request per UTC day / floor 10 |
| Odds ledger | prior request cost 3; 497 remaining |

Strict family coverage from the committed A2 evidence is: team 99.5881%,
recent 99.5881%, talent 99.6470%, rating 100%, hitting 99.7352%, pitching
99.7426%, bullpen 100%, schedule/rest 88.3397%, lifecycle 100%, and target
context 0% by design. Starting-pitcher publication, lineup publication,
Statcast Tier A, park/weather, defense/baserunning, opponent adjustment, and
historical market data are unavailable and excluded.

## Gen-1 research admission policy

`MLB_GEN1_RESEARCH_POLICY_V1` will fail closed unless all of the following are
true:

- the exact strict A2 manifest and feature identity are verified;
- all rows are LIVE, retrospective reconstruction, Tier A, one row per game;
- the population exceeds 2,000 games and 150 event dates;
- both outcome classes exist;
- a same-date-grouped chronological holdout and at least five expanding folds
  can be formed;
- target exclusion, cutoff, compact-lineage, checksum, no-market, no-CLV,
  no-Statcast, and no-Tier-B checks pass;
- team, rating, recent, hitting, pitching, and bullpen meet their declared
  coverage thresholds; and
- every excluded family remains explicitly absent from the frozen schema.

This admission permits offline research only. The unchanged promotion policy
continues to require the missing families before production promotion review.

## Frozen Gen-1 feature contract

The Gen-1 schema will be content-addressed and derived only from non-Tier-B A2
specifications in these families: `team`, `talent`, `rating`, `recent`,
`hitting`, `pitching`, `bullpen`, and `schedule`. Lifecycle constants and
target-context fields are excluded from predictive inputs. Each entry records
its A2 source feature, family, derivation/window, neutral value, expected
training range, missingness semantics, A2 feature version, and strict temporal
rule. Paired missing indicators are retained where evaluated by the
missingness ablation. The schema checksum binds ordered definitions and output
names.

For the seven predeclared family ablations, A2 `talent` is grouped with
`team`, A2 `recent` is labeled `recent_form`, and A2 `schedule` is labeled
`rest_travel`; the original A2 family remains in every feature definition for
lineage.

Three recent-form variants will share the same games and folds:

1. no recent-form inputs;
2. raw legitimate 14/30-day inputs;
3. Bayesian/shrunk recent-form inputs and reliability terms.

No Tier B starter/lineup context, Statcast, market, park/weather,
defense/baserunning, opponent adjustment, or DEMO feature may enter.

## Temporal protocol and holdout lock

Games are ordered by scheduled first pitch and grouped by event date. The
newest 20% of date groups form a locked final holdout. The committed structural
audit predicts 147 development groups and 37 holdout groups (about 505 games),
which the implementation must independently verify and persist.

Development uses at least five expanding rolling-origin folds. For every fold
the artifact records train/validation date bounds, row counts, class balance,
and non-overlap. Preprocessing, tuning, feature selection, ablations,
calibrator selection, stack weights, and thresholds use development/OOF data
only. A frozen candidate manifest containing model members, feature schema,
preprocessing, hyperparameters, calibrator choice, weights, and seeds is
written before the single holdout-label access.

## Model candidates

The bounded candidate set is:

- historical home-field prior;
- strict chronological Elo/rating probability;
- transparent run-strength baseline;
- standardized L2-regularized logistic regression;
- Poisson home/away run regressors and derived win probability;
- negative-binomial run models retained only if validation overdispersion
  evidence and run/probability metrics justify them;
- an empirical-Bayesian partial-pooling team strength classifier; and
- one restrained, seeded XGBoost classifier search.

No LightGBM, CatBoost, neural network, or broad model zoo is authorized.

## Calibration, ablations, and ensemble policy

Raw OOF probabilities are compared with identity, Platt, and—only with enough
support—isotonic calibration. Calibration is fit and selected on temporal OOF
evidence, never holdout outcomes.

Mandatory validation analyses are:

- no/raw/Bayesian recent-form variants on identical folds;
- leave-one-family-out rating, team, recent, hitting, pitching, bullpen, and
  rest/travel studies;
- paired A2-neutral values with missing indicators versus fold-training median
  imputation with indicators removed, plus model-native missing handling for
  XGBoost where applicable; and
- fold consistency and time-aware paired uncertainty.

Any ensemble is fit from temporal OOF member predictions with nonnegative
weights constrained to sum to one. It is retained only when Brier improves,
log loss and calibration do not materially regress, and fold behavior is
stable. Otherwise the strongest single model is frozen.

## Evaluation and decision

Primary win metrics are Brier score, log loss, ECE, and calibration
intercept/slope; accuracy and ROC-AUC are secondary. Run models report
home/away/total/differential MAE plus useful RMSE. The single locked holdout is
evaluated on identical games against the home prior, Elo, and logistic
baseline. Date-block bootstrap intervals cover candidate metrics and paired
deltas. Probability bands and governed subgroups disclose sample sizes and
mark small samples insufficient.

The final decision is one of `RESEARCH_CANDIDATE`, `RESEARCH_CHALLENGER`, or
`REJECTED_GEN1`. `CHAMPION`, official positions, superiority, betting ROI,
historical edge, CLV, and market comparisons are prohibited.

## Artifact and registry policy

Ignored runtime storage under `.runtime/gate3` will contain the immutable
training manifest, frozen feature schema, preprocessing, estimator(s),
calibrator, ensemble configuration, OOF and holdout predictions, evaluation,
explainability summaries, and SHA-256 checksums. Executable joblib content is
loaded only from the root-confined trusted artifact store. Database registry
rows bind the exact strict dataset, feature version, code revision, seeds,
artifact checksums, evaluation identity, and research decision. Large model or
dataset binaries are not committed.

## Product visibility

The backend will expose a compact, read-only historical research contract and
optional game explorer. The client will add `/research/history` with the 2025
warehouse counts, strict coverage, leakage status, missing families, filters,
and selected-game evidence. Data Health will present `Current Live Data` and
`Historical Research Warehouse` separately. Performance will keep `Offline
Chronological Evaluation` separate from `Live Forward Performance` and will
show a failed/no-promotion result honestly if necessary.

Today remains current-date LIVE-only. After the controlled refresh it must
show current matchups even when forecasts are unavailable, with provenance,
`data_as_of`, readiness, and forecast status. DEMO and 2025 historical rows are
never substituted.

## Controlled live refresh

Only after model code and provider-free tests are stable, the operator-only
path will run one deliberate August 14 live refresh in this order: schedule,
current MLB context, weather, one Odds snapshot, features, forecasts. It must
preflight LIVE-only provenance, disabled scheduler/polling/Statcast/positions,
the daily Odds reservation, quota floor, and a clean target date. No continuous
process is enabled. Exact MLB, weather, Odds, and Statcast counts plus Odds
credits before/used/remaining will be recorded.

The historical candidate may be registered as a `GEN1_RESEARCH` challenger
only if its offline decision and inference-contract tests pass. It does not
replace the operational DEVELOPMENT forecast automatically.

## Testing and QA

Provider-free automated tests cover research authorization; strict feature
isolation; Tier A/source/cutoff/lineage checks; grouped split and holdout lock;
train-only preprocessing; folds; recent/family/missingness ablations;
calibration and stack leakage; artifact identity and tamper detection; research
status; historical and performance APIs; live/historical/DEMO separation;
Today forecast-unavailable behavior; Data Health separation; and disabled
official positions.

After the controlled refresh, desktop 1440×900 and mobile 390×844 QA will
verify real current games, separate historical research, visible statuses, no
DEMO/historical leakage, no console errors, and no page-level overflow.

## Success and stop conditions

Gate 3B succeeds only with a reproducible one-time research decision, complete
leakage and artifact lineage, honest uncertainty and limitations, visible live
and historical data in separate surfaces, exact provider accounting, and no
official-position activation. Any integrity, holdout-lock, source-isolation,
artifact, quota, or provider-preflight failure stops the affected operation
without silently weakening the policy.
