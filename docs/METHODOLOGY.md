# Forecasting Methodology

## What ASP measures

AI Sports Predictor (ASP) is a forecasting research application. Its Phase 1 output is a reproducible estimate of an MLB game's scoring distribution and win probabilities, followed by a transparent comparison with observed sportsbook prices. The official $1,000 experiment is a virtual simulation, not a real-money account.

An LLM does not invent the prediction. Numerical models produce the forecast; a deterministic narrative layer translates structured model drivers into readable observations when no optional language-model provider is configured.

## Point-in-time information

Each observation has both an event context and an availability time. Feature generation accepts an `as_of` cutoff and rejects information observed after that cutoff. Pregame forecasts cannot contain the current game's result. Rolling windows are calculated strictly from earlier completed games.

Forecasts record their model version, feature version, data cutoff, generation time, publication time, market snapshot, component outputs, readiness, and uncertainty. Publishing a refresh appends a new version; prior publications are not overwritten.

## Initial ensemble

Phase 1 intentionally uses a transparent ensemble rather than claiming a single definitive model:

- A rating component exposes the team-strength interface with a bounded home
  advantage. Current live forecasts use the neutral 1500 prior because the
  game-by-game rating update pipeline is not yet promoted.
- A statistical run component estimates scoring intensities from observed season
  team offense/opponent pitching and bounded park/weather context. Starter,
  bullpen-role, rest, and recent-form inputs remain neutral or unavailable until
  canonical sources populate them.
- A gradient-boosting-compatible component consumes the same versioned feature
  contract. Chronological training and artifact-registry utilities exist, but the
  live Phase 1 ForecastService currently uses the explicitly untrained
  deterministic development challenger and does not claim production training.

Component weights are versioned configuration. Expected runs feed a seeded Poisson Monte Carlo simulation (20,000 draws by default), producing home/away win rates, run-differential and total distributions, and probabilities for common spread and total lines.

## Fair probabilities and prices

For American odds `A`:

- Positive odds: `p = 100 / (A + 100)`
- Negative odds: `p = |A| / (|A| + 100)`

The two sides' raw probabilities usually sum above 1.0 because of sportsbook margin. ASP calculates overround as their sum minus 1.0 and normalizes each side by the sum to produce a no-vig market probability.

Model edge is always shown as:

`ASP forecast probability − consensus no-vig market probability`

Fair moneylines are the inverse price representation of ASP probabilities. “Edge” describes disagreement between estimates; it is not a guarantee or proof of profitability.

## Confidence and readiness

Forecast probability and confidence are different. Confidence is derived from
ensemble agreement, sample reliability, and data readiness. Historical
calibration can contribute only after persisted settled evidence exists; it is
omitted rather than replaced by an assumed score. Simulation/component
uncertainty is reported separately and is not currently folded into the
confidence score. Readiness separately scores source-specific freshness,
probable/confirmed pitchers, statistics/Statcast coverage, weather, odds
freshness, sportsbook breadth, and feature completeness. Missing inputs remain
visible and reduce the score.

## Evaluation

Gate 3 prospective training is provider-free and uses only persisted,
immutable, prospectively captured `LIVE` evidence. Its dataset builder requires
one published pregame feature snapshot and one append-only final observation
per game. It stops before fitting when fewer than 200 eligible labeled games
exist. At the frozen 2026-08-13 Gate 3 audit cutoff, the prospective sample
contained nine scheduled games, zero final outcomes, and therefore zero eligible
labels. That scoped prospective audit remains distinct from Gate 3B's separately
governed retrospective research model below.

Gate 3A Stage A1 produced corrected 15-row one-day v3 and 90-row seven-day v4
historical-reconstruction artifacts and a canonical 2025 core containing 2,430
unique official finals. Stage A2 then materializes the full 2,428-outcome
strict Tier A population with one chronological replay; its final admitted row
count and structural readiness status are `2,428` and
`NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT`. A separate enhanced shadow population covers
all 2,430 A+B targets but is classified Tier B research-only and cannot become
training- or evaluation-eligible. Neither population is relabeled as
prospective capture.

Stage A2's compatibility review was audit-only: it counted rows, date groups,
development-period classes, formable rolling folds, and a chronological
holdout by identity without dereferencing holdout outcomes or computing
holdout metrics. No estimator was fitted during A2. Its stricter promotion-gate
readiness result remains unchanged.

Gate 3B later used the separately authorized
`MLB_GEN1_RESEARCH_POLICY_V1`, which permits research on the supported strict
Tier A families without claiming that the missing production-promotion
families exist. That distinct authorization admitted only the exact frozen A2
artifact; it did not relabel the evidence as prospective or make the A2
promotion gate ready.

When a sufficient sample exists, games remain grouped by date and ordered by
first pitch. The latest 20% of date groups form a single-use locked holdout; the
earlier period supplies expanding rolling-origin folds. Preprocessing,
hyperparameters and feature inclusion use only training or temporal out-of-fold
validation evidence. The stacker is also evaluated with expanding-origin
cross-fitting over base-model OOF folds before deployable weights are fitted on
all base OOF rows. Probability calibration uses only that meta-cross-fitted
validation evidence.
Identity, Platt, and sufficiently sampled isotonic calibration are selected on
validation data before the frozen calibrator is applied to the holdout.

Probability models are evaluated with Brier score, log loss, expected
calibration error, reliability diagnostics, and paired uncertainty where sample
size permits. Run models use run-error metrics such as MAE. Market-relative
analysis uses only the exact intersection of model, timestamped market, and
outcome evidence. Missing closing odds remain null; ASP does not manufacture
historical or closing lines. Offline chronological evidence and live forward
performance are reported separately.

### Historical reconstruction boundary

Gate 3A reconstruction is a separate research population. Its deterministic
pregame cutoff is six hours before scheduled first pitch. Feature evidence must
resolve to an immutable canonical team/player history row, occur strictly
before the cutoff, be eligible by the cutoff, and exclude the target game.
Fetch time remains the real acquisition time. A reconstructed final label is
not available to ASP until its actual acquisition time (or a later proven
publication time), even though the historical final time can order prior-game
workload and rating updates. Historical rows never require a fabricated
Forecast, market snapshot, closing line, or CLV field.

Tier A is fully reconstructable historical evidence, Tier B is explicitly
partial evidence, and prospective ASP capture is the future Tier C gold
standard. Missing, forward-only, timing-invalid, DEMO, and backtest evidence is
excluded rather than neutralized into an observed value. A/B populations are
not mixed unless a later dataset build explicitly permits and reports it.

The A1 MLB feed did not expose an explicit authoritative final-publication time
for the acquired games. Outcomes therefore use the exact last recorded play's
`about.endTime` as `DERIVED_LAST_EVENT_COMPLETION_PROXY`, retain its limitation,
and keep actual acquisition/label availability in 2026. This timestamp orders
completed prior games; it is never represented as an authoritative publication
time or backdated ASP availability.

The corrected A1 one-day v3 and seven-day v4 artifacts contain 15 and 90 Tier A
rows with zero exclusions. They separate 100-point temporal integrity from
frozen-schema coverage and admission quality: 20 of 47 row inputs are observed,
while 16 of 43 non-derived coverage inputs yield 37.2093%. Their 47 inputs
expand to 94 frozen value/missing-indicator outputs. The earlier v1 100-point
`quality_score` and repeated-reference artifact layout are superseded; v4
compactly attests 174,649 canonical reference occurrences and stores 90 rows in
368,281 bytes. Actual starters and lineups are retained as postgame
participation facts but omitted from Tier A pregame features because their
historical publication time is not established. No provider call or model
training was invoked by either artifact rebuild.

The complete 2025 core has 2,428 Tier A outcomes and two Tier B rain-ended
outcomes (`777474` and `777188`). All 2,430 use the derived last-event completion
proxy; none is represented as an authoritative final-publication timestamp.
The one-day Statcast trial began as raw-only partial evidence and Stage A2
promoted it locally into a deterministic Tier B research artifact. Publication
time and historical eligibility remain unproven, so it contributes no strict
Tier A feature and is training/evaluation-ineligible.

### Stage A2 chronological materialization boundary

Stage A2 freezes 272 nullable raw values and 544 ordered
value/missing-indicator outputs under `mlb_historical_pregame_a2_v2`.
Targets are ordered by their six-hour pregame cutoff, and a single state cursor
admits prior team, player, and final facts only after their governed completion
and eligibility boundary. The target game is excluded by identity. Team
14-day, 30-day, and season state; Bayesian batting, pitching, and team state;
Elo; bullpen workload and quality proxies; rest; and supported travel evidence
are therefore derived without replaying the season separately for each game.
Unavailable facts remain missing, with a paired indicator in the model-ready
matrix.

Strict Tier A never uses historical actual-starter or actual-lineup identity
without an independently proven pregame publication time. The enhanced
2,430-target Tier B shadow population may use those identities as provisional
context but may not use target-game performance; all of its outputs remain
research-only. Compact append-only evidence-state attestations bind each row
to source/family counts, time bounds, canonical-set checksums, source-bundle
checksums, feature-value checksums, and target-exclusion/temporal-validity
assertions without serializing every source reference into every dataset row.

The already archived 2025-06-17 Statcast response may be promoted file-to-file
into a deterministic minimal Tier B research artifact. The archive's 2026
ingestion time is the earliest defensible availability because historical
publication timing is unproven. Its events and rolling aggregates are
training-ineligible and evaluation-ineligible, and contribute zero strict Tier
A features. The promotion itself makes no provider call.

Final coverage, dataset/checksum identities, performance/storage evidence,
provider reconciliation, and the decision on whether 2025 is sufficient for a
later bounded Gate 3B are recorded in
`docs/GATE_3A_A2_2025_FEATURE_MATERIALIZATION_REPORT.md`. Additional 2023-2024
seasons could improve prior depth and stability testing, but cannot repair
unproven starter, lineup, Statcast, market, or weather availability. Stage A2
does not acquire them.

Stage A2 must not invoke `train-mlb-models`. That command belongs to a later,
separately authorized modeling stage once the audit-only readiness decision has
been reviewed. See `docs/GATE_3_MLB_MODEL_TRAINING_VALIDATION_REPORT.md` for the
modeling, leakage, artifact, promotion, and public-reporting boundary.

### Gate 3B Gen-1 research model

Gate 3B freezes 230 supported strict A2 values and their paired missingness
indicators into 460 ordered model inputs. It excludes lifecycle and target
context, Tier B starter/lineup evidence, Statcast, park/weather,
defense/baserunning, opponent adjustment, markets, CLV, and DEMO data. A2
`talent` is analyzed with `team`, `recent` as `recent_form`, and `schedule` as
`rest_travel` while retaining original source-family lineage.

Of 184 grouped event dates, 147 dates and 1,923 games supply 16 expanding
rolling-origin development folds. The newest 37 dates and 505 games form one
locked holdout. Development alone chooses recent-form treatment, model and
hyperparameters, missingness strategy, calibration, and ensemble retention,
then freezes a deterministic decision rule. Phase 2 globally claims the exact
source-dataset/holdout identity, reloads and checksum-verifies the source, and
opens the labels once; a new candidate cannot reuse that evidence.

The completed run selected no 14/30-day recent-form variant, a run-strength
model, and Platt calibration. Its holdout Brier score was
0.24189382768082518, log loss was 0.6766197598504301, ECE was
0.08282638285346813, accuracy was 0.5584158415841585, and ROC-AUC was
0.5848393099315609. Point estimates improved versus the home prior, Elo, and
regularized logistic comparators, but the frozen calibration, OOD, run-model,
and bootstrap checks did not all clear. The predeclared result is therefore
`RESEARCH_CHALLENGER / RESEARCH_ONLY_NO_PROMOTION`, never `CHAMPION`.
Only the historical-home-prior paired Brier and log-loss improvements had
strictly positive 95% date-block bootstrap lower bounds. Intervals against Elo,
logistic, empirical-Bayesian team, XGBoost, raw run strength, Poisson, and NB2
crossed zero. Gate 3B therefore makes no comparator-wide superiority claim.

The first frozen candidate
`438d9fa23fef15fa434418cb320f59fd9a4ae88e567f2629401510bdca827795`
was invalidated before Phase 2 because its selected run-strength explanations
were unavailable. It created no holdout claim or evaluation. Replacement
candidate `0c207742c1b1d60661c6b92efcd5a4b8776bc12799cb83ef8827e87bb994dceb`
supplied independently reconciled selected-model drivers, and terminal artifact
`a03a7ae520e0b0bb16cb4de1987d78e1b97eb99cab9e6318b03226ae81d4bdd1`
records the only holdout evaluation.

Selected-model explanations use independently verified additive run-strength
log-odds terms, explicit clipping and calibration adjustments, and paired
missingness/neutral-fill disclosure. All 505 frozen driver records reconciled
with maximum numerical error `1.17e-15`. This explains model mechanics and
statistical association, not causation. Full identities, uncertainty, model
comparisons, and limitations are recorded in
`docs/GATE_3B_GEN1_MODEL_AND_LIVE_ACTIVATION_REPORT.md`.

### Gate 3C Gen-2 player/event research foundation

Gate 3C defines a new research path rather than adding player fields to the
Generation 1 game-row model. Its provider-free contracts cover chronological
hierarchical player posteriors, dynamic-state candidates, partially pooled
platoon effects, participant and pitching-usage distributions, an eight-class
PA outcome model, 24 base/out states, and a discrete-event game simulator.
Strict pregame evidence remains separate from
`HISTORICAL_ACTUAL_LINEUP_TIMING_RISK`, and every model/evaluation path remains
downstream of temporal/source/tier checks.

The retained one-day fixture proves canonicalization and cross-source alignment
for 15 games, but it is not a multi-season training population. No event model,
player state, transition posterior, residual model, calibration, ensemble, OOD
replacement, or Gen-2 forecast was fit or evaluated. Automated acquisition is
blocked by source authorization, so Gate 3C ends at
`PROVIDER_FREE_FOUNDATION_COMPLETE / BLOCKED_SOURCE_AUTHORIZATION` and makes no
performance or promotion claim. The exact method inventory and evidence matrix
are in `docs/GATE_3C_GEN2_PLAYER_LEVEL_MODEL_REPORT.md`.

### Historical research product boundary

Historical Research is a separate user-facing evidence surface, not a backfill
of Today. It summarizes the exact latest strict and enhanced manifests and
offers a paginated, filtered explorer over canonical pregame snapshots. It
keeps Tier A and Tier B populations separate and withholds rows when current
manifest lineage, target exclusion, or temporal attestations fail. Superseded
immutable materializations do not contaminate the current-manifest integrity
decision. The explorer never exposes labels or silently substitutes missing
starter, lineup, weather, Statcast, or market evidence.

### Controlled live activation and publication lineage

The one 2026-08-14 controlled run is operational evidence, not additional model
training. It loaded 14 LIVE games, 13 weather snapshots, and 756 current Odds
snapshots from exactly one Odds HTTP request; generated 14 feature snapshots;
and published 12 forecasts. Two games were skipped because probable pitchers
were unavailable. Schedulers, Odds polling, Statcast, and official positions
remained disabled. The Gen-1 research challenger did not replace the live
operational forecast model.

Forecast publication remains append-only. The first 12 version-1 forecasts
exposed a 500-character market-reference truncation and remain immutable audit
evidence. Migration `20260814_0009` uses unbounded text, and a provider-free
regeneration created 12 version-2 forecasts that each reference all 54 Odds
snapshot rows used for their market context. Public reads choose the latest
version. Downgrade is valid only if every stored reference fits 500 characters;
complete version-2 lineage must never be silently truncated to make a rollback
succeed.

Live operational reporting is still incomplete: no
`GameRefreshState.last_refresh` was written and the MLB rows have null
`ingestion_run_id`. These gaps must remain visible in Data Health and be fixed
prospectively. They do not authorize inferred timestamps or retroactive lineage.
Performance continues to expose the offline status as `RESEARCH_CHALLENGER` /
`NOT_PROMOTED`, separately from live forward outcomes.

## Virtual bankroll

The experiment starts at $1,000. Its provisional Phase 1 policy supports flat stake, fixed bankroll percentage, and capped full/half/quarter Kelly calculations. The default is deliberately conservative, centrally configurable, and clearly marked provisional. All official positions—including losses and pushes—remain in history. No money moves through the platform.

## Limitations

- Public feeds can be delayed, incomplete, or change schema.
- Probable pitchers, lineups, injuries, and bullpen availability may change close to first pitch.
- Weather forecasts do not fully model stadium-specific airflow or roof decisions.
- Early Phase 1 model evaluations have limited captured historical odds and should not be extrapolated as evidence of future returns.
- Feature importance describes statistical association in the model, not causation.
- Odds availability and terms depend on jurisdiction and provider licensing.

ASP is for analytics, education, research, and entertainment. It does not guarantee outcomes, recommend chasing losses, or execute wagers.
