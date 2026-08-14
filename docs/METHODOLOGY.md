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
exist. The current prospective sample contains nine scheduled games, zero
final outcomes, and therefore zero eligible labels.

Gate 3A Stage A1 produced corrected 15-row one-day v3 and 90-row seven-day v4
historical-reconstruction artifacts and a canonical 2025 core containing 2,430
unique official finals. Stage A2 then materializes the full 2,428-outcome
strict Tier A population with one chronological replay; its final admitted row
count and structural readiness status are `2,428` and
`NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT`. A separate enhanced shadow population covers
all 2,430 A+B targets but is classified Tier B research-only and cannot become
training- or evaluation-eligible. Neither population is relabeled as
prospective capture.

Stage A2's compatibility review is audit-only: it may count rows, date groups,
development-period classes, formable rolling folds, and a chronological
holdout by identity, but it does not dereference holdout outcomes or compute
holdout metrics. No estimator was fitted, no calibrator was selected, and no
model was promoted. An audit result of
`READY_FOR_GATE_3B_MODEL_DEVELOPMENT` would authorize only a later, separately
governed modeling stage; it is not evidence of predictive performance.

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
