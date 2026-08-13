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

Gate 3 training is provider-free and uses only persisted, immutable `LIVE`
evidence. The dataset builder requires one published pregame feature snapshot and
one append-only final observation per game. It stops before fitting when fewer
than 200 eligible labeled games exist. The current local sample contains nine
scheduled games, zero final outcomes, and therefore zero eligible labels: no
real model was fitted and the promotion decision is **NO PROMOTION -
INSUFFICIENT HISTORICAL EVIDENCE**.

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

Run the offline audit only after migrations are current:

```powershell
cd C:\Users\james\ai-sports-predictor\server
..\.venv\Scripts\python.exe -m app.cli train-mlb-models --as-of 2026-08-13T16:00:00Z
```

See `docs/GATE_3_MLB_MODEL_TRAINING_VALIDATION_REPORT.md` for the complete data,
leakage, artifact, promotion, and public-reporting boundary.

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
