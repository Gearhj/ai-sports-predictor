# AI Sports Predictor — Phase 1 Implementation Plan

## 1. Scope and product boundary

Phase 1 delivers a local-first MLB forecasting laboratory: live MLB schedule ingestion, weather, optional live sportsbook odds, reproducible quantitative forecasts, market comparison, immutable forecast history, a simulated $1,000 bankroll, authentication, operational controls, and a professional analytical web interface.

The platform does not place wagers, connect to sportsbook accounts, promise returns, or use an LLM as the prediction engine. NFL, fantasy, paid memberships, community competition, and advanced arbitrage remain documented extension points.

This implementation lives in its own independent repository and does not couple to the separate ATLAS application.

## 2. Architecture

```text
MLB Stats API       The Odds API       Open-Meteo
      |                    |                 |
      +----------- source adapters ----------+
                           |
              immutable raw JSON archive
                           |
                normalization + validation
                           |
             canonical SQLAlchemy data model
                           |
          feature builder (available-as-of guard)
                           |
        rating + run model + boosting-compatible
             ensemble + Monte Carlo simulation
                           |
          forecast registry + market comparison
                           |
                    FastAPI v1 API
                           |
              React / TypeScript / ECharts UI
```

Boundaries are defined with Python protocols so providers can be replaced without changing forecasting or API code. External identifiers are stored as mappings; internal entities use UUIDs. Time-sensitive records carry `observed_at`, `available_as_of`, or `generated_at` timestamps.

## 3. Dependency choices

- Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy 2, Alembic, httpx, NumPy, Polars, scikit-learn/joblib, XGBoost, APScheduler, PyJWT, and `pwdlib` with Argon2.
- PostgreSQL 16 for application persistence. SQLite is permitted only as an isolated automated-test database.
- Immutable JSON snapshots in `data/raw/<source>/YYYY/MM/DD/`; Parquet-ready curated and feature directories are reserved.
- React 18, TypeScript, Vite, React Router, a small abortable fetch-resource hook, Apache ECharts, Lucide, and plain design tokens/CSS.
- Docker Compose for PostgreSQL, API, and client. Native Windows startup scripts are also supplied.

Dependencies are pinned to compatible major/minor ranges in `server/requirements.txt` and `client/package.json`.

## 4. Canonical database schema

The first migration creates multi-sport core entities (`sports`, `leagues`, `seasons`, `teams`, `players`, `venues`, `games`, `game_teams`), external ID mappings, observations (`weather_snapshots`, `odds_snapshots`, markets/outcomes), versioned analytical records (`model_versions`, `feature_versions`, `forecasts`, `forecast_components`, `market_comparisons`), operations (`ingestion_runs`, `data_quality_checks`, `audit_events`, `job_runs`), authentication/entitlements, and virtual bankroll/position records.

MLB-only detail is kept in extensible feature snapshots rather than leaking baseball assumptions into `Game`, `Forecast`, `Team`, or `Market`.

Forecasts and odds snapshots are append-only at the service layer. Published forecast rows are never updated or deleted; refreshed information creates a new version.

## 5. Source adapters

- `MLBScheduleSource`: MLB Stats API schedule hydrate, probable pitchers, venue, status, records, and game identifiers.
- `MLBStatsSource`: team standings/stats and starter season summaries where available.
- `OddsSource`: The Odds API `baseball_mlb` events with h2h, spreads, and totals; API usage headers are captured. Polling defaults off to conserve quota.
- `WeatherSource`: Open-Meteo hourly forecast resolved from a maintained MLB venue coordinate reference.
- Fixture implementations: deterministic examples available only through explicit demo selection or a deliberate disposable-database seed.

Adapters return source-independent DTOs and typed errors for expected transport
failures. Missing inputs remain explicit and lower readiness rather than being
fabricated. Additional recorded schema-drift fixtures and consistent wrapping of
every malformed-provider-payload path remain production-hardening work.

## 6. Ingestion and jobs

For accepted adapter results, ingestion writes the provider payload to a
checksummed raw archive before canonical database normalization. The job ledger
records job ID, timestamps, status, count, warnings, and top-level errors;
successful/partial ingestion metadata also retains source, raw reference, and
schema version. Propagating every failed-job error into `IngestionRun.error_message`
remains hardening work. Upserts identify canonical games while observation tables
append. Transport payloads that fail before an adapter can return remain visible
as failed jobs but are not yet guaranteed to have a raw archive entry.

The orchestrator exposes repeat-safe jobs for canonical schedule/stat/weather
upserts and explicit append-producing jobs for raw observations, odds snapshots,
and forecast versions. Settlement and official-position publication enforce
idempotency constraints. Admin routes trigger these jobs and write audit events.
The **Run Simulations** control currently aliases forecast generation and therefore
creates a new immutable forecast version. A development scheduler refreshes
schedule/weather and only polls odds when explicitly enabled.

## 7. Forecast strategy

The initial transparent ensemble has three components:

1. Interpretable baseline strength interface using a neutral 1500 prior plus home
   advantage. Phase 1 does not yet run a persisted game-by-game rating updater.
2. Statistical scoring model using observed season team offense, opponent season
   pitching, and bounded weather/park context. Starter, bullpen-role, and recent
   form inputs remain neutral/missing when no canonical live observation exists.
3. Gradient-boosting-compatible component with separate chronological training
   and artifact-registry utilities. The live Phase 1 ForecastService does not yet
   promote/load those artifacts and explicitly runs the untrained deterministic
   development challenger.

Weights live in configuration. The ensemble produces expected runs, total, differential, win probabilities, fair moneylines, uncertainty, confidence, and model-driver records. A seeded, configurable 20,000-draw Poisson Monte Carlo engine returns win, run-line, total, and distribution summaries. Evaluation utilities calculate Brier score, log loss, calibration bins, and run errors using chronological splits.

## 8. Markets and bankroll

Odds utilities convert American/decimal prices, calculate raw and normalized no-vig probabilities and overround, find consensus/best prices, and detect simple two-way arbitrage behind a disabled feature flag. Market edge is always `ASP probability − market no-vig probability` and opportunity thresholds are centralized configuration.

The public experiment starts at $1,000 and supports flat, fixed-percentage, and capped full/half/quarter Kelly sizing. It is virtual-only. Position settlement is append-audited; loss records cannot be removed. Closing-line fields remain null when no actual closing snapshot exists.

## 9. Authentication and security

Users have email, password hash, display name, role, status, timestamps, and provider-neutral entitlements. Short-lived bearer tokens protect member routes; admin routes enforce `ADMIN`. Passwords are Argon2-hashed through `pwdlib`, configuration is validated, CORS and security headers are explicit, and admin actions are audited. The local `.env` is ignored and never contains a committed key.

## 10. Frontend pages

- Today's MLB Forecasts: command strip, sortable opportunity table/cards, freshness and demo/live labels.
- Game Analysis: matchup overview, forecast, market comparison, pitchers/offense/bullpen/Statcast context, weather, model drivers, simulation distributions, forecast history, data audit.
- Market Board: market tabs, book grid, best price, consensus, fair value, and edge.
- Model Performance: calibration, error metrics, model registry, version transparency.
- $1,000 ASP Experiment: bankroll curve, ROI/drawdown, complete position ledger, policy disclosure.
- Methodology: sources, equations, limitations, versioning, and responsible-use statement.
- Data Health/Admin: source/job/quota state and authenticated manual controls.
- Login/Account: registration, sign-in, session identity, and role.

Navigation includes an active MLB selector plus visibly unavailable NFL, NCAAF, NHL, WNBA, Fantasy, Community, and Leaderboards entries.

## 11. Test strategy

- Unit: odds conversions/no-vig/arbitrage, Kelly caps, settlement/pushes, deterministic simulation, readiness, matching, rolling-boundary/leakage guards, evaluation metrics, and serialization.
- Persistence: append-only odds, immutable published forecast versions, auth, role enforcement, API schemas, source error/missing-data behavior.
- Frontend: Vitest component and route-level tests for all eight pages, client state, accessibility, and demo/live provenance.
- Manual browser QA: navigation, login, dashboard-to-game drilldown, admin authorization, responsive layout, and visible demo/live provenance. Automated Playwright is a recommended hardening step once a browser CI runtime is selected.

All tests run without consuming Odds API quota.

## 12. Risks and mitigations

- Public source drift: adapters, typed validation, raw snapshots, health reporting, and explicit fixture test/demo harnesses that never replace live failures.
- Odds quota exhaustion: polling disabled by default, manual refresh, configurable interval/markets/regions, persisted quota headers.
- Historical Statcast acquisition cost/licensing: isolate acquisition, document allowed usage, cache immutable responses, and do not redistribute restricted data.
- Look-ahead leakage: require `available_as_of <= first_pitch` in builders and cover it with tests.
- Sparse or unconfirmed inputs: explicit nulls, readiness components, confidence penalties, and no silent forecasts below the configured floor.
- Untrained gradient model: clearly label development fallback; never claim out-of-sample performance that has not been measured.
- Local Docker availability: provide both Compose and native Windows instructions; PostgreSQL remains the production design.

## 13. Implementation sequence

1. Scaffold configuration, containers, canonical models, migration, and app factory.
2. Add source contracts, fixtures, raw archive, normalization, and health tracking.
3. Add features, ensemble, simulation, market math, registry, and bankroll.
4. Expose typed FastAPI routes, authentication, orchestration, and scheduler.
5. Build the React shell and eight Phase 1 pages.
6. Seed demo mode, document setup/methodology/roadmap, and add scripts.
7. Run automated tests/build, launch the stack, perform browser QA, and capture evidence.

## 14. Phase 1 completion interpretation

This first generation is considered operational when it launches locally, supports authentication, can ingest today's MLB schedule and weather, conditionally ingests independent sportsbook prices with a locally configured key, persists forecast versions and virtual bankroll state, exposes admin job controls, renders all required pages, and passes its automated test/build gates. Honest data-readiness states remain acceptable when a public provider does not supply a field.
