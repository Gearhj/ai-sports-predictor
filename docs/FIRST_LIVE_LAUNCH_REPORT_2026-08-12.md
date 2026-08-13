# AI Sports Predictor Phase 1 - First Live Launch Report

> Historical inspection record, retained as evidence of that attempt. It is not
> a description of the current Phase 1.5 provenance contract or current machine
> state.

**Inspection date:** 2026-08-12

**Repository:** `C:\Users\james\ai-sports-predictor`

**Outcome:** Launch stopped at the database prerequisite check. No provider refreshes, odds requests, forecasts, simulations, virtual positions, wagers, or browser QA actions were performed.

## Executive summary

The controlled live launch could not safely proceed because the documented PostgreSQL dependency is unavailable on this machine:

- Docker/Docker Compose is not installed or available on `PATH`.
- No native PostgreSQL service or `psql` client was found.
- The configured PostgreSQL endpoint, `localhost:5433`, is not listening and failed a TCP reachability check.

The repository documentation requires PostgreSQL for the application and reserves SQLite for isolated tests. The launch instructions explicitly say to stop if a local database dependency prevents startup. Accordingly, no substitute database, configuration change, demo seed, or destructive workaround was attempted.

**Recommendation: NOT READY FOR USER INSPECTION.** The application cannot be launched or verified until PostgreSQL 16 is available through Docker Desktop or an explicitly configured native PostgreSQL service.

## Scope and safety controls

| Check | Result |
|---|---|
| Resolved working directory | `C:\Users\james\ai-sports-predictor` |
| Resolved Git repository root | `C:\Users\james\ai-sports-predictor` |
| Exact root match | PASS |
| `.env` exists | PASS |
| `THE_ODDS_API_KEY` populated | PASS; value was never displayed, logged, or copied |
| `.env` Git-ignored | PASS; matched `.gitignore` |
| `ODDS_POLLING_ENABLED=false` | PASS |
| `STATCAST_ENABLED=false` | PASS |
| `SCHEDULER_ENABLED=false` | PASS |
| `OFFICIAL_POSITIONS_ENABLED=false` | PASS |
| ASP-only command scope | PASS; repository/runtime commands used the ASP repository as their working directory |
| ATLAS / `ds-workflow-starter` access | None |
| Git history changes | None |

No secrets are included in this report. The configured administrator email and password were only checked for presence; neither credential is reported.

## Environment

### Startup method selected

The preferred documented method is `docker compose up --build`, but Docker is unavailable. The documented native Windows method still uses Docker for PostgreSQL unless an existing native PostgreSQL 16 service is configured. No such service was found.

The safe native-development path was therefore evaluated and stopped before migrations or application startup because its database prerequisite was missing.

### Installed application prerequisites

| Component | Status |
|---|---|
| Python | 3.12.4; available |
| ASP virtual environment | `.venv\Scripts\python.exe`; present |
| Node.js | 24.3.0; available, but newer than the documented Node 22 target |
| npm | 11.6.4; available |
| Frontend dependencies | `client\node_modules`; present |
| Docker CLI / Compose | Unavailable |
| Native PostgreSQL service | Not found |
| `psql` CLI | Unavailable |

### Runtime status

| Component | Status |
|---|---|
| Database type configured | PostgreSQL via SQLAlchemy/psycopg |
| Database actually used | None; connection prerequisite unavailable |
| PostgreSQL endpoint | `localhost:5433`; unreachable |
| Backend | Not started |
| Frontend | Not started |
| API health | Not testable; API not running |
| Frontend load | Not testable; frontend not running |
| Administrator login | Not attempted; backend/database unavailable |
| Browser console/runtime errors | Not inspectable; application not running |
| Ports 8000 and 5173 | Free at inspection time |

The intended URLs after a successful startup remain:

- Application: `http://localhost:5173`
- API health: `http://localhost:8000/api/v1/health`
- API documentation: `http://localhost:8000/docs`

These URLs were not live during this inspection.

## Controlled live-data refresh

The refresh sequence was not started because application startup was blocked. This is intentionally reported as **NOT ATTEMPTED**, not as a successful zero-record refresh or a provider failure.

| Requested action | Result |
|---|---|
| 1. Refresh Today's Schedule | NOT ATTEMPTED |
| 2. Refresh MLB Data | NOT ATTEMPTED |
| 3. Refresh Weather | NOT ATTEMPTED |
| 4. Refresh Odds exactly once | NOT ATTEMPTED; zero Odds API requests made by this inspection |

### Live-data measurements

| Measurement | Result |
|---|---|
| MLB games ingested | Not measured |
| Probable pitchers available | Not measured |
| Weather records | Not measured |
| MLB games matched to odds | Not measured |
| Sportsbooks returned | Not measured |
| Moneyline observations | Not measured |
| Spread/run-line observations | Not measured |
| Total observations | Not measured |
| Odds API request success/failure | No request attempted |
| Odds API quota cost | No request/header available; this inspection made zero requests |
| Remaining monthly quota | Not queried |
| Unmatched games | Not measured |
| Malformed or unsupported markets | Not measured |
| Source warnings | No provider jobs were run |

Scheduled odds polling remained disabled. No retry was made or needed.

## Sportsbook normalization

No live odds records were created or available for database inspection, so the required three-game sportsbook spot check could not be performed.

Static inspection confirms the intended normalized representation preserves sportsbook identity, observation and availability timestamps, outcome/team identity, American odds, decimal odds, implied probability, and points. The forecast comparison path removes vig within complete two-outcome sportsbook markets and then aggregates across books. These implementation observations are not evidence that a live provider payload normalized correctly in this run.

The following require a successful live database run before they can be accepted:

- team assignment for moneylines;
- run-line point/price pairing;
- over/under point/price pairing;
- American-to-decimal conversion against real records;
- implied and no-vig probability calculations against real records;
- preservation of multiple bookmaker observations;
- best-price and consensus calculations;
- unmatched-event and unsupported-market reporting.

One static operational concern was identified: the Data Health read path currently reports `missing_games=0` unconditionally. That UI value cannot be used as proof that all live odds events matched canonical games.

## Feature rebuild and completeness

`Rebuild Features` was not run. No live per-game completeness scores were available.

Static inspection of the current Phase 1 implementation indicates the following expected state once the live pipeline runs:

| Feature group | Current implementation status |
|---|---|
| Team offense | Core season values can be populated by live MLB acquisition; unverified this run |
| Team pitching | Core season values can be populated by live MLB acquisition; unverified this run |
| Probable starters | Identity may come from the schedule, but canonical starter detail/stat snapshots are incomplete; unverified this run |
| Home/away context | Schedule/game context is implemented; unverified this run |
| Weather | Open-Meteo integration is enabled, but no records were fetched; wind is excluded from scoring until park orientation is verified |
| Sportsbook market context | Implemented, but no odds request was made |
| Bullpen features | Reliable role/workload/availability inputs remain missing or neutral; season team ERA may be used as a fallback |
| Recent-form features | Current live acquisition does not populate the target rolling windows; values remain neutral/unavailable |
| Statcast-derived features | Unavailable by design because `STATCAST_ENABLED=false`; no Statcast field is claimed as live |

The job named `rebuild-features` currently validates/counts games rather than performing a substantive feature reconstruction; MLB ingestion writes team feature rows directly. This should be understood before treating a successful job label as proof of rebuilt features.

## Forecasting

`Generate Forecasts` was not run. Consequently:

| Measurement | Result |
|---|---|
| New forecasts generated | 0 by this inspection; existing database state unavailable |
| Average/min/max readiness | Not measured |
| Average/min/max confidence | Not measured |
| Games below readiness threshold | Not measured |
| Markets with model comparisons | Not measured |
| Per-game component outputs | Not inspected |
| Per-game simulation outputs | Not inspected |

Static inspection confirms that `generate-forecasts` already runs the seeded Poisson simulation and stores its summary. The separate `run-simulations` job is an alias for the same forecast-generation handler and would create another immutable forecast version. It was not run.

### Developmental model limitations

- The team-strength component currently uses a neutral 1500 rating prior because the game-by-game rating update pipeline is not promoted.
- The boosting-compatible 25% challenger is an explicitly untrained deterministic fallback unless a validated estimator is injected.
- Recent-form, reliable starter detail, bullpen role/workload, and availability inputs remain neutral or unavailable in the live pipeline.
- Statcast-derived fields are absent while Statcast is disabled.
- Missing starter and bullpen ERA values use documented league-neutral fallbacks, reducing readiness/confidence.
- The simulation is a seeded independent-Poisson model with a synthetic extra-inning tiebreaker and only a limited set of run lines/totals.
- Wind is excluded from scoring because venue orientation has not been verified.
- Forecasts are skipped after first pitch or when critical readiness inputs are missing.

`OFFICIAL_POSITIONS_ENABLED` remained false. No official virtual bankroll positions were published and no real wagering capability was invoked.

## Analytical sanity checks

The requested strong-favorite, even-matchup, and model-vs-market-disagreement checks require real games, forecasts, simulations, and normalized market observations. None existed in an accessible running database, so no game was selected and no numeric result is claimed.

The following checks remain mandatory on the resumed launch:

- home plus away win probability approximately equals 100%;
- expected total equals projected home plus away runs;
- fair moneylines reproduce the modeled probabilities;
- market edge uses a same-book, no-vig market probability;
- simulation probability is reasonably consistent with the ensemble;
- confidence is distinct from win probability;
- readiness accurately reflects missing source inputs.

No suspicious live probabilities, prices, edges, or totals could be identified because no live outputs were generated.

## Visual QA

Browser inspection was not started because neither application service could be launched. No new screenshots were captured, and pre-existing repository screenshots were not represented as evidence from this run.

| Phase 1 page | Runtime inspection | Data classification for this run |
|---|---|---|
| Today's MLB Forecasts (`/`) | Not available | UNAVAILABLE |
| Game Analysis (`/games/:gameId`) | Not available | UNAVAILABLE |
| Market Board (`/markets`) | Not available | UNAVAILABLE |
| Model Performance (`/performance`) | Not available | UNAVAILABLE |
| $1,000 ASP Experiment (`/experiment`) | Not available | UNAVAILABLE |
| Methodology (`/methodology`) | Not available | UNAVAILABLE; implementation copy is PROVISIONAL |
| Data Health / Admin (`/admin`) | Not available | UNAVAILABLE |
| Login / Account (`/account`, `/login`) | Not available | UNAVAILABLE |

### LIVE versus DEMO static review

At the time of this historical inspection, absent provenance and failed API
requests could activate bundled demo data. Phase 1.5 superseded that behavior:
unprovenanced empty responses remain EMPTY, request failures are UNAVAILABLE,
and fixtures require explicit demo selection.

Potential demo/live and UX defects found by static inspection:

1. The pre-Phase 1.5 offline Game Analysis fallback could combine unrelated matchup content. Phase 1.5 replaced it with one canonical, explicit demo game and honest unknown-ID handling.
2. Bundled demo forecast component weights are 20%/35%/45%, while the Methodology page and backend defaults state 30%/45%/25%.
3. Demo bankroll copy states a 2.0% stake cap while the rendered policy/backend configuration use 2.5%.
4. A transient `/auth/me` failure clears the session token; outage-induced logout should be tested after startup.
5. Data Health's hard-coded zero unmatched-games value can falsely imply successful canonical matching.

These are static findings. Desktop readability, data density, responsive layout, interaction behavior, and browser-console health remain unverified. The requested six screenshots are outstanding.

## Data sanity

No live game spot checks were possible. The three required profiles and all requested calculations remain outstanding. Static unit/model structure is not treated as a replacement for live record validation.

## Operational issues

### Blocking issue

PostgreSQL 16 is unavailable. The configured endpoint `localhost:5433` is not listening. Docker/Docker Compose is unavailable, and no native PostgreSQL installation was found.

### Provider and quota status

- MLB Stats API: not called.
- Open-Meteo: not called.
- The Odds API: not called; zero requests were made by this inspection.
- Odds quota: no cost or remaining-quota headers were obtained.
- Statcast: disabled and not called.

### Non-blocking environment note

Node.js 24.3.0 is newer than the documented Node 22 LTS target. It was not exercised and is not identified as the launch blocker, but compatibility should be observed during the resumed frontend launch.

## Recommendation

### NOT READY FOR USER INSPECTION

Phase 1 cannot yet be classified as a live, inspectable system on this machine because its required PostgreSQL service is absent. Backend health, frontend loading, administrator login, controlled provider ingestion, normalized odds, feature completeness, forecasts, simulations, analytical sanity, and browser presentation all remain unverified.

To resume without changing architecture, provide one of the repository's documented database paths:

1. Install/start Docker Desktop, then use the documented PostgreSQL/Docker Compose workflow; or
2. Provide an existing native PostgreSQL 16 service and explicitly configure the ASP `DATABASE_URL` for it.

No machine-level installation was attempted because it requires user approval. Once PostgreSQL is available, rerun this inspection from Step 2 and preserve the requested exactly-once Odds API discipline.

**Exact intended local inspection URL:** `http://localhost:5173`

**Current availability:** unavailable until PostgreSQL, API, and frontend are started.
