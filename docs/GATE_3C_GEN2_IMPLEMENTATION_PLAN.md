# Gate 3C Gen-2 Player-Level MLB Forecasting Implementation Plan

## Status and governing decision

This plan was frozen on 2026-08-14 before Gate 3C implementation or provider
acquisition. The approved repository baseline is:

- root: `C:/Users/james/ai-sports-predictor`;
- branch: `main`;
- commit: `0e83c521a1dbfbc7bb0deae50b4e55c2a9c178bb`;
- worktree: clean;
- database: PostgreSQL at Alembic `20260814_0009`;
- authorization: `LOCAL_PRIVATE_RESEARCH` only;
- official positions: disabled;
- schedulers, event-relative scheduler, Odds polling, and Statcast: disabled.

Gate 3C will build a new player/event/simulation research track. It will not
silently extend or replace the current development forecast, promote a model,
activate positions, obtain historical odds, or describe 2025 as untouched
evidence.

### Acquisition authorization gate

Baseball Savant links to the current
[MLB.com Terms of Use](https://www.mlb.com/official-information/terms-of-use).
Those terms limit copying to personal, non-commercial use and expressly prohibit
automated scripts that collect information from MLB Digital Properties. Project
owner authorization is not MLB permission and is not a legal determination.

Therefore:

- no new automated MLB Stats API or Baseball Savant request is authorized by
  this plan;
- acquisition Stages A-G are `BLOCKED_SOURCE_AUTHORIZATION` until a documented,
  defensible permission or license permits the exact automated local-research
  workflow;
- technical endpoint availability, an HTTP 200 response, or prior acquisition
  does not satisfy that gate;
- already persisted raw/canonical evidence may be processed locally under the
  existing private-research boundary;
- manually supplied, lawfully obtained exports may be imported only through the
  same checksum, identity, provenance, and retention controls;
- raw data, derived event rows, and model artifacts may not be redistributed.

The existing `--approved-local-research` switch is not provider permission and
must not be sufficient to construct an adapter. Gate 3C will represent project
owner approval and independent provider-access authorization as separate,
immutable facts. A provider call requires both.

Existing-evidence derived use is a third decision, separate from acquisition.
The initial state is `LOCAL_PRIVATE_RESEARCH_DERIVED_USE_ONLY`: local parsing,
validation, and research-only derived artifacts are permitted for evidence
already in the project; redistribution, commercial use, source resale, and any
upgrade to strict eligibility are denied. If this derived-use decision is absent
or conflicts with a source record, local processing also fails closed.

This gate is fail-closed and applies before adapter construction. It does not
block provider-free schemas, parsers, Parquet infrastructure, player models,
usage models, simulation, tests, or analysis of existing local evidence.

## Mandatory baseline

### Historical warehouse

| Season | Finals | Team-game rows | Player-game rows | Actual lineup batters | Starters | Relief appearances |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2023 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025 | 2,430 | 4,860 | 127,872 | 43,740 | 4,860 | 16,010 |

The 2025 population contains 2,428 Tier A outcomes and two Tier B rain-ended
outcomes. Actual starters and batting orders are retrospective participation
facts and carry `HISTORICAL_ACTUAL_LINEUP_TIMING_RISK`; they are not proven
historical pregame lineup evidence. Historical pregame roster, bullpen
availability, weather, odds, and canonical Statcast coverage are zero.

### Existing Statcast evidence

One raw-only archive exists for 2025-06-17:

- compressed bytes: 2,523,608;
- archive SHA-256:
  `601af8d47af4dfe66102efd4df38ecd324e8ebf38449ddc8eb0c708337e79f2d`;
- payload SHA-256:
  `bcaa171d4adfb8666ab3ee85eb0490710103825ecf973f8b07a80e55c53a4251`;
- schema: `statcast-search-detail-csv-v1`;
- 4,375 pitches, 1,140 plate appearances, 15 games, 304 batters, and
  133 pitchers.

It has no governed Statcast source version and has not been admitted to strict
training. It remains Tier B research evidence with unproven historical
publication timing.

### Frozen A2 and Gen-1 lineage

- A2 manifest ID: `d4087299-8a04-4176-bad2-a9ec19128b62`;
- A2 identity:
  `3059f81db1d0e2783d0357cf765fbc027958a1d0786c8da83611bae82dca1748`;
- A2 rows checksum:
  `bc627c16f85ac3372cb2cb3fb1c9dab6767291704dac790fb5ffe07b8af406bb`;
- A2 source feature version: `7cdfa80f-725d-4061-8ca5-5967d9ea5701`;
- Gen-1 ModelVersion: `7c4366a1-a92e-49a7-8760-7ecf971289db`;
- Gen-1 evaluation: `34784593-4174-43b1-91d7-6bc0783703b6`;
- Gen-1 decision: `RESEARCH_CHALLENGER / NOT_PROMOTED`;
- consumed global holdout claim:
  `71f27c14b17ed74e2082cbcdef4a92c665548f487e933255cd58786220795ed4`.

The consumed 505-game 2025 holdout is retrospective comparison evidence only.

### Live and storage baseline

- 23 LIVE 2026 games across August 13-14;
- 42 immutable forecasts across 21 games, with 21 latest forecasts;
- zero completed/evaluable prospective games;
- zero Gen-2 forecasts or prospective Gen-2 plan;
- one incomplete lineup observation and zero bullpen-availability observations;
- current starters: 28 observations for 14 games, 26 probable, two unknown, and
  zero confirmed;
- 21 weather snapshots and 1,228 Odds snapshots across both dates;
- current Odds quota evidence: 494 remaining;
- raw data: 329,219,860 bytes / 2,624 files;
- runtime: 677,529,377 bytes;
- database: 774,659,095 bytes;
- Parquet: zero files / zero bytes;
- free disk: 270.86 GiB.

## Architecture boundary

Gen-2 will be file-backed for high-volume events and database-backed for
identity, manifests, governance, model/evaluation lineage, and prospective
snapshots.

### PostgreSQL responsibilities

- canonical game, team, player, venue, roster, lineup, starter, transaction,
  and final identities;
- source versions, checkpoints, request accounting, and raw references;
- append-only event-partition manifests;
- feature/model/evaluation identities;
- prospective evaluation plans and single-use checkpoint claims;
- separate Gen-2 research-forecast lineage.

### Parquet responsibilities

- pitch events;
- plate appearances and base/out transitions;
- player posterior/state histories;
- analytical event matrices and cached out-of-fold predictions.

Partitions use Zstandard compression and the layout:

```text
data/curated/gen2/season=YYYY/month=MM/family=pitch/part-....parquet
data/curated/gen2/season=YYYY/month=MM/family=plate_appearance/part-....parquet
data/features/gen2/season=YYYY/month=MM/family=player_state/part-....parquet
```

Each month/family should contain one or a small bounded number of files. Atomic
staging must occur inside the ignored target root. `*.parquet`, `*.arrow`, and
`*.feather` will also be globally ignored before any generated output exists.

### Versioned contracts

- pitch schema: `mlb_gen2_pitch_event_v1`;
- plate-appearance schema: `mlb_gen2_plate_appearance_v1`;
- partition manifest: `mlb_gen2_event_partition_manifest_v1`;
- taxonomy: `mlb_gen2_pa_outcome_taxonomy_v1`;
- true talent: `mlb_gen2_player_true_talent_eb_v1`;
- dynamic state: `mlb_gen2_player_dynamic_state_ewma_eb_v1`;
- platoon: `mlb_gen2_platoon_partial_pool_v1`;
- event baseline: `mlb_gen2_pa_hierarchical_multinomial_v1`;
- event challenger: `mlb_gen2_pa_xgboost_multiclass_v1`;
- starter usage: `mlb_gen2_starter_exit_hazard_v1`;
- bullpen usage: `mlb_gen2_bullpen_weighted_pool_v1`;
- transition model: `mlb_gen2_base_out_transition_v1`;
- simulator: `mlb_gen2_discrete_event_simulator_v1`;
- count baselines: `mlb_gen2_count_baselines_v1`;
- shared environment count model: `mlb_gen2_shared_environment_count_v1`;
- residual layer: `mlb_gen2_residual_xgboost_v1`;
- stack: `mlb_gen2_temporal_stack_v1`;
- probability calibration: `mlb_gen2_probability_calibration_v1`;
- event calibration: `mlb_gen2_multiclass_temperature_v1`;
- OOD: `mlb_gen2_ood_policy_v1`;
- umbrella policy: `MLB_GEN2_RESEARCH_POLICY_V1`;
- prospective policy: `mlb_gen2_prospective_policy_v1`;
- prospective registration: `GEN2_PROSPECTIVE_RESEARCH_CHALLENGER`.

Any semantic change creates a new version. Old artifacts remain immutable.

## Source acquisition strategy

### Target population

The intended governed core is 2023-2025 regular-season games. No 2022
acquisition or pooling is authorized. The intended pitch/PA population is
2023-2025, but expansion cannot start while the source authorization gate is
closed.

### Stage A - 2024 core pilot

Proposed normal-week window: 2024-06-10 through 2024-06-16. After source
authorization, discovery is exactly one range-schedule request. The result must
be archived before semantic normalization, then strict unique finals determine
the separately authorized acquisition cap. The acquisition run performs one
fresh range schedule plus one combined feed request per final: `N + 1` calls.
The two-phase pilot total is therefore exactly `N + 2` calls. No feed request
may occur until discovered `N`, the acquisition cap, and the retained discovery
checkpoint reconcile. `N` is never guessed from another season.

Validate schedules, item `officialDate`, resumptions/postponements, finals,
teams, players, starters, batting orders, pitcher appearances, completion
proxies, mappings, raw checksums, duplicates, and exact physical accounting.

### Stages B-C - full 2024 and 2023 core

Only after the Stage A report passes. Run one bounded season at a time with:

- exact schedule discovery checkpoint;
- exact unique-final count;
- one combined game feed per unique final;
- monthly or similarly bounded canonical transactions;
- resume from SQL checkpoints;
- no Odds, Statcast, or historical weather call path;
- 100 GiB minimum free-space reserve.

Do not begin 2023 until 2024 reconciliation passes.

### Stage D - 2024 Statcast month pilot

Proposed window: 2024-06-01 through 2024-06-30. This stage remains blocked by
source authorization. If unblocked, Stage D0 first requests only 2024-06-10
through 2024-06-16: seven paced logical date requests, then a mandatory halt and
report. Stage D1 may request the remaining 23 June dates only after D0 passes.
The whole month is 30 logical date requests, never one uncontrolled range
response. Persist an append-only checkpoint per date; stop on the first critical
schema, identity, mapping, or taxonomy error. Reconcile the response against
canonical games and MLB play-by-play.

### Stages E-G - full Statcast 2024, 2023, and 2025

Stage E (full 2024) begins only after Stage D passes. Stage F (full 2023) begins
only after Stage E's season report passes. Stage G (full 2025) begins only after
Stage F passes. Reuse the existing 2025-06-17 archive by semantic identity.
Never repeat an existing successful request solely to simplify code. No stage
automatically advances to the next season.

### Universal pilot and stage pass matrix

Every core and Statcast pilot reconciles schedules, completed games, final
scores, teams, players, actual starters, actual batting orders, every batter and
pitcher appearance, player-game rows, completion basis, native and canonical
identities, duplicates, missingness, taxonomy, event times, raw checksums,
Parquet checksums, leakage, storage, runtime, and exact request accounting.

Acquisition is resumable, idempotent, checkpointed, rate-limited, concurrency
one, bounded in retries, and safe to interrupt. A restart must recover the exact
stage and request cursor without repeating a successful semantic request.
Interrupted attempt accounting must remain durable. The pass report records the
next authorized resume point; there is no implicit continuation.

### Provider accounting

For every stage persist:

- attempts, successes, failures, retries, and rate-limit events;
- operation-level accounting;
- records received/accepted/rejected/duplicated;
- response and compressed-file checksums;
- bytes and elapsed time;
- checkpoint/run identity and next resume point.

Retries are bounded and disabled for semantic/schema/identity errors. A source
response that cannot be archived and accounted cannot be canonicalized.

## Canonical event model

### Pitch identity and fields

Pitch semantic identity is:

`(source_version_id, source_game_id, at_bat_number, pitch_number)`.

It also binds canonical game, batter, pitcher, batting/pitching team, event
timestamp when proven, inning/half, sequence, batter/pitcher handedness, pitch
type, velocity, movement, location, count, swing/contact/whiff/call/foul flags,
batted-ball fields, launch metrics, xwOBA estimate, barrel/hard-hit flags, and
terminal result. Score and win-expectancy fields are forbidden from pregame
feature materializations.

### Plate-appearance identity and transition

PA identity is:

`(source_version_id, source_game_id, at_bat_number)`.

It binds canonical identities, inning/half, outs/base mask/score before, terminal
taxonomy, runs/RBI where relevant, outs/base mask/score after, source event
time, evidence end, eligibility, and exact contributing checksums. MLB
`liveData.plays.allPlays` runner movements are the primary transition source.
Savant rows enrich a PA only after one-to-one batter/pitcher/inning/side mapping.
No base-after state is inferred from a pitch CSV.

The retained 2025-06-17 evidence proves why alignment must be sequence-aware:
Statcast has 4,375 pitch-key rows while MLB play-by-play has 4,354 physical
pitches plus 21 automatic count events. Within each PA, align ordered Statcast
rows against MLB `isPitch` rows plus count-changing `no_pitch` automatic-ball or
automatic-strike events. Validate batter, pitcher, inning/side, count,
description, terminal result, and uniqueness while retaining both native
identities. Any incomplete or ambiguous alignment blocks that game and day.

### Stable PA taxonomy

`mlb_gen2_pa_outcome_taxonomy_v1` uses the following deterministic source
mapping. Baseline counts are measured from the retained 2025-06-17 archive and
must be recomputed per partition; they are not multi-season prevalence claims.

| Category | Source terminal values | Baseline count | Target definition and validation |
| --- | --- | ---: | --- |
| `STRIKEOUT` | `strikeout`, `strikeout_double_play` | 237 | Batter is retired on a strikeout; exactly one terminal value and outs transition must agree. |
| `WALK_HBP_CI` | `walk`, `intent_walk`, `hit_by_pitch`, `catcher_interf` | 122 | Batter reaches without a batted-ball hit; base/run movement must match PBP runners. |
| `HOME_RUN` | `home_run` | 43 | Home run terminal result; batter and all forced/scoring runners must reconcile. |
| `SINGLE` | `single` | 151 | One-base hit terminal class; advancement comes from PBP, not a fixed rule. |
| `DOUBLE` | `double` | 51 | Two-base hit terminal class; advancement comes from PBP. |
| `TRIPLE` | `triple` | 3 | Three-base hit retained despite sparsity; no silent XBH pooling. |
| `BIP_OUT` | `field_out`, `force_out`, `grounded_into_double_play`, `double_play`, `sac_fly`, `sac_bunt` | 524 | Ball-in-play out family; exact outs/runners/runs transition must conserve state. |
| `OTHER_REACH` | `field_error`, `fielders_choice` | 8 | Non-hit reach/choice; must not be mislabeled as a hit or walk. |

The retained archive also contains one `truncated_pa`. It is not a modeled
terminal target. It may be counted and excluded only when the canonical final
proves an officially ended game and MLB PBP proves that the PA had no terminal
result. Otherwise it blocks the partition. Any other unmapped terminal value
blocks the partition.

Sacrifices remain `BIP_OUT`; advancement is captured by the transition model.
Every terminal provider result must map deterministically. An unmapped result
blocks the partition. Triples are retained in v1; any later combined-XBH
decision requires a new taxonomy and downstream restart.

## Player true-talent methodology

Use chronological empirical-Bayesian partial pooling, not raw player rates.

Hitter posteriors explicitly include strikeout, walk/HBP, home-run, single,
extra-base-hit, batted-ball-out, expected contact-quality, and platoon
components. Pitcher posteriors explicitly include strikeout, walk/HBP,
home-run tendency, contact-quality allowance, ground/fly tendency, pitch-mix,
velocity/whiff state, and platoon components.

- Dirichlet-multinomial posteriors for the PA event vector;
- Beta or normal-normal posteriors for binary/continuous underlying metrics;
- league, role, handedness, starter/reliever, and rookie/minimal-history priors;
- player overall posterior before platoon split;
- aggressive partial pooling for sparse platoon samples;
- posterior variance, reliability, effective sample, cutoff, model version, and
  evidence checksum persisted.

Each persisted estimate contains its prior vector, effective prior sample,
observed sufficient statistics and sample, posterior vector, posterior
variance/reliability, hierarchy/role/hand group, source/partition identities,
model version, and evidence cutoff.

For a 2024 event only eligible 2023 evidence may form the prior. For a 2025
event, 2023-2024 may form the prior and only earlier 2025 evidence may update it.
No future-season backward flow is permitted.

Compare one-season, weighted two-season, and weighted three-season priors on the
same temporal folds. Candidate decay weights are learned from development folds
only and frozen before later folds. If three governed prior seasons are absent,
the three-season candidate is `UNAVAILABLE`, never backfilled with future data.

## Dynamic-state methodology

Evaluate on identical temporal folds:

- A: static true talent;
- B: outcome-based exponentially decayed state, diagnostic only;
- C: underlying-skill exponentially decayed EB state using velocity, whiff,
  contact, chase, barrel, hard-hit, and contact-quality evidence where governed;
- D: change-point-aware state, deferred until C wins and sample size justifies a
  new version.

Predeclared C half-life candidates are 14, 30, and 60 days. Retain a dynamic
variant only if multiclass log loss improves, aggregate and classwise Brier are
not materially degraded, game-probability and score-distribution metrics are
nondegraded, and at least 60% of temporal folds are nonworse for both event and
game evaluation.
Persist uncertainty and effective sample. Never create momentum, streak, or
hot-hand flags.

## Lineup, roster, and availability populations

Keep two historical populations:

- strict deployable: only evidence with defensible pregame availability;
- enhanced: actual historical participation labeled
  `HISTORICAL_ACTUAL_LINEUP_TIMING_RISK`.

The populations have separate identities, manifests, feature versions, and
eligibility. Enhanced evidence cannot enter strict training. For live 2026,
projected/expected/confirmed lineup evidence must retain source, batting order,
defensive position, scratches, replacements, `observed_at`, and
`available_as_of`. Missing lineups remain missing.

Roster/transaction state must support active, injured, activated, optioned,
recalled, traded, released, suspended, unavailable, and unknown. Persist source,
canonical team, canonical player, status, effective time, observed time,
available-as-of, source record, checksum, and checkpoint. Availability changes
the participant distribution; it is not a generic injury penalty.

## Usage models

### Starting pitcher

Fit a discrete-time logistic exit hazard after each batter faced/inning using
only prior evidence. Predict expected batters faced/outs and inning-reach
probabilities. Compare against a Negative Binomial count baseline. Inputs may
include talent, rest, workload, team/manager tendency, opponent participant
distribution, handedness, times-through-order, and governed availability.

### Bullpen

Each reliever has player-level true talent, handedness, role, prior-day and
two/three-day workload, consecutive appearances, uncertainty, and availability.
Label workload inference `WORKLOAD_DERIVED_AVAILABILITY_PROXY`; never relabel it
as `OBSERVED_AVAILABLE` or `OBSERVED_UNAVAILABLE`.

The first usage model is a role/inning-weighted available pool predicting
appearance probability and expected batters faced. It must not substitute
overall bullpen ERA for player state.

## Park, weather, and secondary context

`mlb_gen2_park_factor_v1` is a versioned prior-period model with run and home-run
factors, optional defensible handedness splits, validity interval, source,
sample, uncertainty, and checksum. A season's completed results cannot be used
to apply a full-season factor backward within that season. A 2025 game may use
only an entering-2025 or legitimate earlier rolling factor.

Weather may include temperature, humidity, pressure, precipitation, wind speed
and direction, roof state, and altitude. Wind cannot become a directional run
adjustment without a versioned park-orientation bearing. Missing historical
weather remains missing and does not block the core event model.

Catcher, defense, and baserunning are nonblocking enrichment families. Framing,
blocking, throwing, OAA-type defense, sprint speed, steals, and advancement are
used only when governed evidence exists. Umpire identity/zone tendency is
`UNAVAILABLE` unless a reliable timestamped assignment and source are available.
No arbitrary-site scraping or invented proxy is allowed.

## Base/out transition model and simulator

Use outs 0-2 and base mask 0-7 for 24 ordinary states plus inning end. Transition
posteriors are Dirichlet-shrunk by event category, state, and outs. Sparse speed,
defense, park, or hand conditioning remains absent until evidence supports it.

`mlb_gen2_discrete_event_simulator_v1` must:

- cycle the actual/projected batting order;
- draw calibrated PA outcomes;
- conserve outs, runners, runs, and score;
- apply state transitions;
- sample starter exit and select an eligible reliever;
- handle bottom-nine skips, walk-offs, and 2023+ automatic-runner extra innings;
- terminate without a final tie;
- accept deterministic seeds;
- return joint home/away, total, margin, win/run-line/total probabilities, and
  quantiles.

Research default is 50,000 simulations/game, configurable to 100,000. Live use
requires p95 <= 10 seconds/game at 50,000 draws and <= 1 GiB RSS/worker. Failure
does not permit lowering scientific checks or silently using another model under
the Gen-2 label.

Simulator validation is layered:

- event: sampled PA frequencies versus input vectors, including classwise
  calibration;
- inning: runs, outs, base-state transitions, inning termination, and advancement
  versus held-out PBP;
- game: home/away/total/differential errors, score variance, zero-run and
  high-run tails, extra-inning frequency, interval coverage, and win-probability
  Brier/log loss.

A visually plausible score distribution is not sufficient.

## Event, count, residual, ensemble, and calibration models

The permanent event baseline is regularized multinomial logistic using player
posterior logits, pitcher effects, handedness interaction, league environment,
and only prior-period park/context evidence. The sole boosted challenger is
restrained XGBoost `multi:softprob`; CatBoost and LightGBM will not be added.
Persist baseline coefficients/latent player effects, schema/model identity,
training evidence checksum, and event-calibration evidence.

Game-level comparators remain home prior, Gen-1 run strength, Elo, independent
Poisson, Negative Binomial, and a shared-environment/bivariate count model.
Residual XGBoost may correct simulator mean/dispersion/logit only when temporal
OOF evidence retains it.

Count-model review explicitly tests overdispersion and home/away score
dependence. Independent Poisson is not assumed adequate. Residual models use
training-fold-only preprocessing and never fit transforms on validation,
historical comparison, or prospective rows.

Stack weights are nonnegative and sum to one, learned only from temporal OOF
predictions. Identity, Platt, isotonic, and a simple justified beta calibrator
are compared on development OOF evidence. Event probabilities may use
temperature scaling. A residual, stack, or calibrator is retained only if:

- Brier improves by at least 0.0005;
- log loss is nondegraded;
- ECE/calibration and score-distribution metrics are nondegraded;
- at least 60% of folds are nonworse.

The simpler simulator/identity path wins ties.

For every calibration candidate persist the method, fit sample identity and
size, selection sample identity and size, parameters, code/schema/checksum,
calibration slope/intercept, ECE, Brier, and log loss. No method is retained
merely because it changes probabilities.

## Historical validation structure

When governed 2023-2025 evidence exists, use expanding rolling-origin folds with
same-date games grouped. A representative sequence begins with early 2023
training, later-2023 validation, and expands through 2024-2025. All 2025 results
are `RETROSPECTIVE_RESEARCH_EVIDENCE`; no 2025 block is untouched proof.

Evaluate event multiclass log loss, classwise Brier/calibration, time stability,
home/away/total/differential MAE and RMSE, interval coverage, tail/zero-run/extra
inning frequency, Brier, log loss, slope/intercept, ECE, AUC, and accuracy.
Bootstrap by game-date blocks.

Generation-2 candidates compared on identical rows/folds are: home prior,
Gen-1 run strength, chronological Elo, Poisson, Negative Binomial/shared
environment, hierarchical-event simulator, boosted-event simulator, simulator
plus residual correction, and the constrained ensemble. Every candidate must
have a declared scientific role; there is no model zoo.

## OOD diagnosis and replacement

Before changing a threshold, reproduce the Gen-1 407/505 `CAUTION` population
and attribute triggers by feature, family, month, team, missingness, robust
z-score, rest/travel, doubleheader, season phase, and range violation. Gen-2 OOD
will include season-aware transforms, player/state reliability, missingness,
event uncertainty, simulator uncertainty, and true range violations. It will not
raise a threshold merely to reduce the count.

## Prospective 2026 evaluation policy

Before the first eligible Gen-2 live forecast, persist an append-only plan that
freezes model, feature/player-state/event/usage/simulator/residual/stack/
calibration/OOD/missingness versions and checksums, code identity, start time,
lifecycle, eligibility, corrections, postponements, exclusions, and baselines.

Recommended primary lifecycle is FINAL T-10. Interim evaluation requires 300
completed eligible games; the main research decision requires 500. Labels may
not be read until an atomic single-use claim for the declared checkpoint
succeeds. A model/code/schema change creates a new model and new window. The
checkpoint is not lowered if the remaining 2026 season is underpowered.

An eligible prospective game is a canonical 2026 regular-season LIVE game with
one immutable FINAL T-10 Gen-2 forecast produced before first pitch, complete
input/model lineage, and a governed official final. Games with no Gen-2 score,
late/post-start score, incomplete lineage, DEMO evidence, ambiguous identity, or
non-regular game type are excluded with a persisted reason and remain visible in
coverage. A pre-start postponement is excluded on the old occurrence and may
become eligible only under its canonical rescheduled occurrence. A suspended
game follows the predeclared original-occurrence policy and cannot receive a
post-suspension replacement forecast. Official corrections are append-only;
the claim binds the latest eligible revision available at claim time without
changing the frozen prediction.

The interim and main checkpoints are separate global single-use claims for the
same immutable plan. The 300-game claim does not authorize model changes inside
the 500-game window. If a new model is frozen after interim review, it starts a
new plan/window and cannot reuse prior games as prospective evidence. Claims
bind exact eligible IDs, target revision checksums, code/model identities, and
date-block bootstrap policy.

Predeclared metrics are Brier, log loss, slope/intercept, ECE, AUC, accuracy,
home/away/total/differential MAE and RMSE, predictive-interval coverage, and a
distribution score such as ranked probability score/CRPS where implemented.
Baselines are home prior, frozen Gen-1, Elo, and declared count models on the
same eligible games. Exclusions, corrections, and postponements are reported;
they cannot be silently dropped after outcomes are observed.

Allowed terminal decisions are `GEN2_RESEARCH_CANDIDATE`,
`GEN2_RESEARCH_CHALLENGER`, and `GEN2_REJECTED`. ORM status is never CHAMPION and
official positions remain disabled.

Internal objectives, not promises or industry standards, are approximately
AUC >= 0.62 on genuinely new data, statistically supported Brier improvement
versus home prior and Gen-1, supported or clearly nondegraded log loss,
ECE <= 0.05, slope 0.85-1.15, improved score-distribution accuracy, materially
less OOD `CAUTION` than Gen-1's 80.6%, and no major team/month/band instability.
No single objective forces promotion.

## Prospective event-relative collection

Collection remains disabled until independently authorized and operationally
tested. Its intended lifecycle is:

- Overnight: prior-day finals, player/team updates, bullpen workload,
  transactions, and model/data-quality monitoring;
- Morning: schedule, probable starters, projected lineups, roster, weather,
  first snapshot, and morning research forecast;
- T-6h: status, starter, roster, weather, snapshot, forecast;
- T-3h: same governed core update;
- T-60m: confirmed starters/lineups where available, scratches, bullpen state,
  weather, snapshot, forecast;
- T-30m: late changes, weather, and at most one separately budgeted optional
  Odds snapshot for the slate;
- T-10m: final evidence snapshot and immutable Gen-2 research forecast freeze;
- Postgame: final outcome, quality reconciliation, and evaluation eligibility.

Odds are never polled at every lifecycle stage under the current quota. They
remain a separately budgeted provider path and are not Gen-2 independent-model
inputs.

Compare a MORNING projected-lineup model, T-60 confirmed-lineup model, and FINAL
T-10 model only after sample coverage supports the comparison. Otherwise use one
model with explicit information-state/missingness indicators. A morning forecast
cannot claim confirmed-lineup evidence.

## Live integration

Current development and Gen-2 research forecasts must be stored and queried on
separate tracks. Today continues to hard-select `CURRENT_DEVELOPMENT`. Gen-2 is
shown separately only after artifact load, feature parity, participant/usage
resolution, runtime, lineage, and integration tests pass. Failure produces an
explicit Gen-2 unavailable reason; there is no fallback under the Gen-2 label.

Movement is append-only within a track and records previous/new probability,
score, changed evidence, cutoff, snapshot, and model. Wording is noncausal:
"Forecast changed after these inputs were updated."

## Deferred market-augmented model

`ASP Independent` remains the scientific center and has no market feature.
`ASP Market-Augmented` is deferred until legitimate timestamped prospective
market history exists. Historical market evidence remains
`HISTORICAL_MARKET_DATA = UNAVAILABLE_ON_CURRENT_PLAN`; no closing line or
historical no-vig prior is inferred.

## Provider-free implementation order

1. Add ignore hardening, event/PA schemas, canonical checksums, atomic Parquet
   store, append-only partition manifest migration, and tests.
2. Add independent immutable owner-approval and provider-permission gates plus
   append-only acquisition-stage/request-attempt accounting; default provider
   permission is denied.
3. Build provider-free MLB play-by-play PA/transition extraction and local
   one-day Savant reconciliation without changing its eligibility.
4. Build true-talent, platoon, and chronological dynamic-state primitives.
5. Build starter hazard, bullpen proxy/weighted-pool usage, and tests.
6. Build the 24-state transition model and deterministic simulator.
7. Add event/count models and multiclass/score-distribution metrics.
8. Add rolling OOF caches, residual/calibration/stack gates, and Gen-1 OOD
   diagnosis.
9. Add content-addressed Gen-2 freeze, prospective plans/claims, and separate
   research forecast track.
10. Add only the analytical Player State, Matchup, Usage, Simulation, and Model
   Comparison surfaces required for inspection.
11. Execute acquisition Stages A-G only if their authorization and preceding
    technical gates are independently satisfied.

Normal tests construct no adapters and make zero provider calls.

## Post-acquisition training sequence

Only after the required acquisition/materialization gates pass:

1. build chronological player priors;
2. build static and candidate dynamic player states;
3. build the event dataset;
4. fit the hierarchical event baseline;
5. fit the restrained boosted event challenger;
6. fit starter-duration and bullpen-usage models;
7. fit the transition model and validate the simulator;
8. fit Poisson, Negative Binomial, and shared-environment count baselines;
9. fit the residual challenger;
10. generate temporal OOF event/game/distribution predictions;
11. select event and probability calibration;
12. evaluate the constrained ensemble;
13. freeze the historical Gen-2 research candidate;
14. register the separate prospective challenger and plan.

Training is provider-free. It reads only checksum-verified terminal manifests,
never constructs an adapter, never queries Odds, and never modifies raw archives.

## Automated test and leakage matrix

### Data and storage

- pitch and PA native/canonical identity and deterministic checksum;
- exact duplicate idempotency and conflicting duplicate rejection;
- source-mode and source-version isolation;
- chronological season boundaries and off-date rejection;
- schema order/dtype/null/range validation and drift rejection;
- atomic Parquet write/read-back row, schema, SHA, and manifest verification;
- interrupted staging cleanup and resume without duplicate requests;
- player-team transaction effective-time changes;
- roster proxy versus actual batter/pitcher/starter/lineup classification;
- strict versus `HISTORICAL_ACTUAL_LINEUP_TIMING_RISK` separation.

### Leakage proofs

- a PA cannot enter its own predictors;
- a later pitch cannot enter an earlier player state;
- a current game cannot enter pregame player state;
- a future season cannot enter an earlier prior;
- a traded player follows canonical player identity while team membership changes
  only after effective evidence;
- prospective lineup evidence must have `available_as_of <= cutoff`;
- actual historical lineup cannot enter strict deployable features;
- same-day doubleheader Game 1 enters Game 2 only when final/evidence eligibility
  precede Game 2's cutoff;
- target scores, score differentials, win-expectancy, and historical markets are
  absent from all pregame matrices.

### Player and event models

- Dirichlet/Beta/normal shrinkage and posterior variance;
- rookie, role, hand, starter/reliever, and minimal-history priors;
- one/two/three-season prior chronology and decay selection;
- platoon partial pooling and unseen-player fallback;
- dynamic-state chronology, half-life candidates, and change-point deferral;
- deterministic taxonomy mapping and per-category counts;
- event probability vectors finite/nonnegative/sum to one;
- feature-order and unseen-category checks;
- multiclass calibration and fold-local preprocessing.

### Usage models

- starter hazard probabilities, duration distribution, and count comparator;
- prior pitch/rest/workload cutoffs;
- workload-derived versus observed bullpen availability labels;
- reliever eligibility, role weights, probability sum, and deterministic
  selection under a seed;
- no unavailable or post-cutoff reliever enters the pool.

### Simulation

- all 24 base/out states and inning-end transitions;
- runner, outs, runs, and score conservation;
- strikeout/walk/hit/out/DP/sacrifice transitions;
- inning termination, batting-order cycling, starter exit, reliever transition;
- home-half skip, walk-off, extra-inning automatic runner, and final no-tie;
- deterministic-seed byte stability;
- termination under a hard cap;
- one-million-draw event/distribution sanity within max(0.002, five standard
  errors) of declared probabilities;
- runtime/RSS gates and score-tail/variance sanity.

### Evaluation and governance

- same-date temporal folds and training-only transforms;
- event/game/score metrics, date-block bootstrap, calibration, and stacking;
- residual/calibrator/ensemble keep/drop thresholds;
- Gen-1 OOD trigger attribution and Gen-2 OOD statuses;
- immutable freeze identity and artifact tamper detection;
- prospective plan immutability, exact eligible-set binding, 300/500 claims,
  correction/postponement semantics, and cross-model single-use exclusion;
- label access before a valid claim fails closed;
- Gen-1 comparison is retrospective and the consumed 2025 holdout is never
  labeled untouched;
- public current-development reads cannot silently select a Gen-2 research row;
- official positions and CHAMPION promotion remain impossible.

## Runtime, memory, and storage budget

- incremental Gate 3C expected storage: 4-9 GiB;
- atomic/preflight allowance: 12 GiB;
- minimum disk reserve: 100 GiB;
- canonicalization/training peak memory: 8 GiB;
- simulator worker: 1 GiB;
- selected-column three-season Statcast/PA Parquet estimate: 0.6-1.5 GiB;
- player states/matrices/OOF cache estimate: 1-3 GiB;
- models/evaluations estimate: 0.5-2 GiB;
- one-month canonicalization target: <= 15 minutes;
- season canonicalization target: <= 90 minutes;
- player-state build target: 10-30 minutes/season;
- historical development estimate: 4-12 hours after evidence exists;
- live simulation target: p95 <= 10 seconds/game for 50,000 draws.

Actual runtime, bytes, peak memory, request accounting, and checkpoint identities
replace estimates in the final report.

Runtime evidence records event-model training time, player-state build time,
simulation latency/game, peak RSS, artifact size, database growth, Parquet
growth, and prospective scoring latency.

## Hard stop conditions

Stop the affected stage when any of the following occurs:

- adapter construction/acquisition is attempted without independent
  provider-access authorization;
- local processing is attempted without the existing-evidence derived-use
  decision, or that decision conflicts with the source record;
- provider rejection becomes persistent or a rate-limit/denial condition cannot
  be resolved within the predeclared bounded policy;
- a command would bypass source restrictions or access ATLAS/ds-workflow;
- a game/player/team mapping is missing, ambiguous, off-date, or conflicting;
- a semantic duplicate conflicts instead of matching byte/field identity;
- a required schema field disappears or a terminal PA result is unmapped;
- cross-source PA reconciliation is not one-to-one;
- a current PA/pitch/game, future event, or future season enters predictors;
- strict and actual-lineup timing-risk evidence mixes;
- Tier B enters strict Tier A;
- an event vector is nonfinite, negative, or differs from one by > 1e-12;
- a transition leaves base mask 0-7/outs 0-3, loses runners/outs/runs, or a
  randomized/exhaustive invariant test fails;
- a simulator seed is not byte-stable, a game does not terminate under the cap,
  a final tie remains, or 1,000,000-draw event frequencies miss the predeclared
  max(0.002, five-standard-error) tolerance;
- free-space projection breaches the 100 GiB reserve;
- a model artifact cannot be reproduced or an immutable identity collides;
- temporal folds mix same-date groups or prospective labels are accessed before
  a valid claim and threshold;
- integration attempts promotion, positions, historical odds, or silent
  fallback.

## Gate 3C success criteria

Gate 3C is complete only if evidence shows:

1. governed usable 2023-2025 game history;
2. canonical multi-season pitch and PA partitions;
3. chronological hierarchical player true talent;
4. dynamic state based on underlying skill rather than streak bonuses;
5. participant-driven lineup and availability state;
6. probabilistic starter duration;
7. player-level availability-aware bullpen usage;
8. calibrated/validated PA event probabilities;
9. a statistically validated base/out simulator and score distribution;
10. retained count baselines;
11. residual ML evaluated and retained only on evidence;
12. temporal OOF ensemble selection;
13. honest consumed-2025 labeling;
14. an immutable prospective 2026 plan;
15. complete separate-track Gen-2 live lineage;
16. no unsupported superiority, profitability, market, or licensing claim;
17. official positions disabled;
18. an evidence-driven Gen-2 decision.

If source authorization prevents items 1-2, the gate is reported blocked/partial,
not relabeled successful.

## Release and reporting

The final report is `docs/GATE_3C_GEN2_PLAYER_LEVEL_MODEL_REPORT.md`. Its required
sections are:

- Executive Result;
- Generation 1 Diagnosis;
- Source Authorization and Provider Accounting;
- 2023-2025 Historical Population;
- Pitch and Plate-Appearance Warehouse;
- Roster / Transaction / Lineup Evidence;
- Player True-Talent Model;
- Dynamic Player State;
- Bayesian Recent-State Evaluation;
- Platoon Model;
- Hierarchical Event Baseline;
- Boosted Event Model;
- Starter-Duration Model;
- Bullpen Availability and Usage;
- Park / Weather / Context;
- Base-Out Transition Model;
- Game Simulator;
- Count Baselines;
- Residual ML;
- Calibration;
- Ensemble Selection;
- Historical Rolling Evaluation;
- Generation 1 Comparison;
- Event-Level Metrics;
- Game-Level Metrics;
- Score-Distribution Metrics;
- OOD Diagnosis and Replacement;
- Prospective 2026 Evaluation Policy;
- Live Integration;
- Runtime and Storage;
- Test Results;
- Model Decision;
- Remaining Risks;
- Recommendation for Gate 4.

It distinguishes implemented/provider-free evidence, blocked acquisition,
historical research, and prospective evidence. It contains no unsupported
performance, profitability, market, licensing, or production claim.

Before the final commit: verify ignored `.env`, databases, raw, Parquet, model,
temporary, and runtime files; run a staged secret scan and `git diff --check`, confirm no
historical odds payloads or official positions, confirm ATLAS untouched, and
commit only source, migrations, tests, safe configuration, and documentation.
No push is authorized.
