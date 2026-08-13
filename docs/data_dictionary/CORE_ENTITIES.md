# Core Entity Dictionary

| Domain | Entity | Purpose and important time fields |
|---|---|---|
| Competition | `sport`, `league`, `season` | Generic competition hierarchy; MLB is the only active Phase 1 league. |
| Participants | `team`, `player`, `venue` | Canonical UUID entities; venues include coordinates and time zone. |
| Identity | `source_mapping` | Maps a provider and entity type to its external ID without making it the primary key. |
| Games | `game`, `game_team` | Scheduled event, participants, status, scores, `event_date`, and `scheduled_at`. |
| Baseball observations | team/player game stats and feature snapshots | Pregame-eligible performance evidence with `available_as_of`. |
| Context | `weather_snapshot`, `game_context` | Append-only observed/forecast context and `observed_at`. |
| Markets | `sportsbook`, `market`, `market_outcome`, `odds_snapshot` | Extensible market definitions and append-only book observations with `observed_at`. |
| Modeling | `model_version`, `feature_version` | Immutable algorithm/config/training metadata and artifact location. |
| Forecasting | `forecast`, `forecast_component` | Versioned expected runs/probabilities/lines plus component evidence, `data_as_of`, `generated_at`, and `published_at`. |
| Comparison | `market_comparison` | Referenced forecast and market snapshot, no-vig consensus, edge, and opportunity class. |
| Experiment | `virtual_bankroll`, `official_asp_position`, `position_result` | Starting/current balance, captured price/stake, immutable history, settlement, ROI, and drawdown. |
| Users | `user`, `entitlement` | Hashed credentials, roles/status, and provider-neutral access grants. |
| Future community | bankroll/position/rating/league foundations | Schema extension points; no unfinished Phase 1 UI. |
| Operations | `ingestion_run`, `data_quality_check`, `job_run`, `audit_event` | Traceable source/job health, counts, warnings/errors, and administrative actions. |

Nullable source fields mean “not observed,” never zero or a fabricated default. Monetary amounts use fixed-precision decimals; probabilities are constrained to `[0, 1]`; all operational timestamps are UTC.
