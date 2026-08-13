# MLB Feature Contract — Version 1

Every feature is evaluated at a forecast's `data_as_of` boundary. A value may be used only when its source observation has `available_as_of <= data_as_of` and its event precedes the game being forecast. Missing values remain null in storage; the development model's documented neutral fallbacks reduce readiness and confidence. This is the versioned target contract, not a claim that every row is populated by the live Phase 1 pipeline. Current live acquisition populates core season team offense/pitching and weather fields; ratings, recent-form windows, canonical starter/bullpen detail, and Statcast fields remain neutral or null pending promotion.

| Feature | Entity/grain | Definition | Availability rule |
|---|---|---|---|
| `home_rating`, `away_rating` | Team before game | Pregame team-strength rating centered near 1500. | Rating update from a completed prior game only; never includes the current game. |
| `runs_per_game` | Team, season-to-date | Runs scored divided by completed games. | MLB team-stat snapshot fetched before cutoff. |
| `runs_allowed_per_game` | Team, season-to-date | Runs allowed divided by completed games. | MLB team-stat snapshot fetched before cutoff. |
| `recent_runs_7d` | Team, rolling | Mean runs in prior games whose event and publication times pass the point-in-time guard. | Strict `[cutoff−7d, cutoff)` window; current game excluded by ID. |
| `recent_runs_14d` | Team, rolling | Same calculation over 14 days. | Strict `[cutoff−14d, cutoff)` window. |
| `recent_runs_30d` | Team, rolling | Same calculation over 30 days. | Strict `[cutoff−30d, cutoff)` window. |
| `ops` | Team offense | On-base plus slugging. | Season/team-stat snapshot available before cutoff. |
| `obp` | Team offense | On-base percentage. | Season/team-stat snapshot available before cutoff. |
| `slg` | Team offense | Slugging percentage. | Season/team-stat snapshot available before cutoff. |
| `strikeout_rate` | Team offense | Strikeouts divided by plate appearances. | Numerator and denominator from the same pre-cutoff snapshot. |
| `walk_rate` | Team offense | Walks divided by plate appearances. | Numerator and denominator from the same pre-cutoff snapshot. |
| `xwoba` | Team/Statcast | Expected weighted on-base average from contact and plate-appearance outcomes. | Most recent completed Statcast extract available before cutoff; null when acquisition is disabled. |
| `barrel_rate` | Team/Statcast | Barrels divided by eligible batted-ball events. | Same point-in-time Statcast extract; sample size retained in raw metrics. |
| `hard_hit_rate` | Team/Statcast | Batted balls at the configured Statcast hard-hit threshold divided by eligible batted balls. | Same point-in-time Statcast extract. |
| `average_exit_velocity` | Team/Statcast | Mean exit velocity for eligible batted balls. | Same point-in-time Statcast extract. |
| `starter_era` | Starting pitcher | Season earned-run average for the probable starter. | Probable-pitcher identity and stat snapshot both known before cutoff. |
| `starter_xera` | Starting pitcher/Statcast | Statcast expected ERA where available. | Pre-cutoff Statcast snapshot; null rather than inferred when absent. |
| `starter_rest_days` | Starting pitcher | Calendar days since the pitcher's latest completed appearance. | Only completed appearances available before cutoff. |
| `starter_recent_workload` | Starting pitcher | Prior pitches/innings over the configured recent window. | Excludes current game and appearances unavailable by cutoff. |
| `bullpen_era` | Team bullpen | Season relief-pitching ERA. | Pre-cutoff team pitching snapshot. |
| `bullpen_recent_innings` | Team bullpen | Relief innings in the recent workload window. | Completed prior games only; null when reliable role-level data are absent. |
| `bullpen_availability_proxy` | Team bullpen | Bounded proxy derived from recent reliever workload. | Generated only from observed prior appearances; never from an invented injury/availability state. |
| `park_run_factor` | Venue/season | Neutral-centered multiplicative scoring environment. | Versioned venue reference known before the season/game; defaults to neutral and lowers completeness if unavailable. |
| `temperature_f` | Game context | Nearest hourly forecast temperature to scheduled first pitch. | Weather forecast fetched before cutoff; `forecast_for` retained separately. |
| `wind_speed_mph` | Game context | Nearest hourly forecast wind speed. | Same weather snapshot. |
| `wind_direction_degrees` | Game context | Meteorological wind direction in degrees. | Same weather snapshot; conversion to “out” requires park orientation and is null if unknown. |
| `precipitation_probability` | Game context | Nearest hourly precipitation probability. | Same weather snapshot. |
| `humidity_percent` | Game context | Nearest hourly relative humidity. | Same weather snapshot. |
| `pressure_hpa` | Game context | Nearest hourly mean sea-level pressure. | Same weather snapshot. |
| `home_field` | Game | Indicator for the canonical home participant. | Known from schedule before cutoff. |
| `probable_pitcher_present` | Game/team | Whether the schedule feed identifies a probable pitcher. | Separate from confirmation; a probable listing is not promoted to confirmed. |
| `pitcher_confirmed` | Game/team | Whether an independent lineup/club signal confirms the starter. | False until an explicit confirmation source is observed. |
| `feature_completeness` | Forecast input | Weighted share of required/important inputs present and fresh. | Computed at the same cutoff and recorded with component statuses. |
| `sample_reliability` | Forecast input | Bounded measure of relevant observation volume/stability. | Uses only pre-cutoff sample counts and never substitutes for forecast probability. |

## Versioning and promotion

Adding, redefining, imputing, or changing the window of a feature requires a new `FeatureVersion`. Training data, model artifacts, evaluation metrics, and forecasts must reference that exact version. Research-only features stay in notebooks until their definition, historical availability, leakage tests, and missing-value behavior are documented here.

## Gate 3A historical materialization

Historical reconstruction uses a separately versioned feature schema and a
fixed T−6h cutoff. Every non-null reconstructed feature family must cite an
immutable canonical team/player history UUID and must match that row's source
version, checkpoint/final lineage, source record ID, game ID, event and
eligibility times, evidence classification, quality tier, and payload checksum.
Unknown or caller-invented references fail closed. Target outcomes are linked
only as labels and never enter the feature payload or checksum.

Historical training rows preserve missing/provisional fields and add no market,
closing-line, or CLV values. Default selection is Tier A only. Tier B must be
requested explicitly, and mixed A/B datasets require a separate explicit flag.
The current operational training command still builds the prospective LIVE
population; Gate 3A does not train a model.
