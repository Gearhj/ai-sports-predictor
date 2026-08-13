# AI Sports Predictor

AI Sports Predictor (ASP) is a local-first sports intelligence and forecasting laboratory. Phase 1 focuses on MLB while keeping games, markets, forecasts, users, and model registry concepts sport-neutral for the planned NFL expansion.

The application combines current schedule and context data, optional multi-book odds, reproducible quantitative forecasts, market disagreement analysis, immutable forecast history, model evaluation, and a transparent virtual $1,000 bankroll experiment. It does not place bets or connect to sportsbook accounts.

## What is included

- Live-capable MLB Stats API schedule/probable-pitcher adapter.
- Open-Meteo weather adapter using maintained ballpark coordinates.
- The Odds API adapter for `h2h`, `spreads`, and `totals`, with quota tracking and polling disabled by default.
- Explicit, development-only demo routes so examples never replace unavailable live data.
- Canonical, time-aware SQLAlchemy/PostgreSQL model and Alembic migration.
- Transparent rating/run/boosting-compatible ensemble and seeded Monte Carlo simulation.
- No-vig, best-line, consensus, market-edge, arbitrage-math, and bankroll utilities.
- Versioned, append-only forecasts and odds observations.
- Email/password authentication, member/admin roles, and audited admin controls.
- React/TypeScript analytical UI for all eight Phase 1 screens.
- Automated backend and frontend tests plus Docker and native Windows workflows.

## Data provenance states

Every major analytical payload declares its state rather than treating any
successful HTTP response as evidence of live data:

- **LIVE** — backed by applicable persisted live records.
- **EMPTY** — the API and database are reachable, but no applicable live record
  exists yet.
- **UNAVAILABLE** — the API resource could not be reached or failed; the client
  does not replace it with examples.
- **DEMO** — illustrative client fixtures selected deliberately through `/demo`.

Demo pages carry the persistent label **ILLUSTRATIVE DEMO — NOT LIVE RESULTS**.
An unknown game remains not found, and live routes never enter demo because of
an API or provider failure.

## Required software

- [Python 3.12 or newer](https://www.python.org/downloads/)
- [Node.js 22 LTS](https://nodejs.org/) (Node 20 also works with the selected packages)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) for the recommended PostgreSQL workflow
- VS Code and Git are recommended

Check installations from PowerShell:

```powershell
python --version
node --version
npm --version
docker --version
docker compose version
```

## Configure the environment and your Odds API key

The project already contains a local, Git-ignored environment file at:

`C:\Users\james\ai-sports-predictor\.env`

Open that file in VS Code and paste the key after the equals sign on this line:

```dotenv
THE_ODDS_API_KEY=your_actual_key_here
```

Do not add the key to `.env.example`, source code, screenshots, logs, commits, issues, or chat. Confirm Git ignores the local file:

```powershell
cd C:\Users\james\ai-sports-predictor
git check-ignore -v .env
```

Live polling is deliberately off even when a key is present. After the app is running, use the admin screen's one-time **Refresh Odds** action, or intentionally set:

```dotenv
ODDS_POLLING_ENABLED=true
```

Keep `ODDS_POLL_INTERVAL_MINUTES=60` (or higher) during development. Three markets in one US region consume more quota than a single market; the provider reports the exact cost and remaining balance in response headers, which ASP records.

## Recommended startup: Docker Compose

From a new PowerShell window:

```powershell
cd C:\Users\james\ai-sports-predictor
docker compose up --build
```

After Compose reports the database and API healthy and the client is available, open:

- Web application: [http://localhost:5173](http://localhost:5173)
- Interactive API documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
- API health: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

Compose applies migrations and creates only the administrator account. It does
not seed fixtures or refresh any provider. Use the admin controls to ingest live
sources deliberately. Illustrative examples are available only through the
explicit `/demo` routes in development; an API or provider failure never enables
demo mode.

Compose persists PostgreSQL state in the named `asp_postgres_data` volume, so ordinary restarts do not erase forecasts, odds history, users, or bankroll records.

Stop the stack without deleting its database:

```powershell
docker compose down
```

Do not add `--volumes` unless you intentionally want to destroy all local database state.

## Native Windows development

Use Docker only for PostgreSQL:

```powershell
cd C:\Users\james\ai-sports-predictor
docker compose up -d db
```

Set up Python and JavaScript dependencies:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Apply the database migration:

```powershell
cd C:\Users\james\ai-sports-predictor\server
..\.venv\Scripts\python.exe -m alembic upgrade head
```

Create the administrator without seeding sports data:

```powershell
..\.venv\Scripts\python.exe -m app.cli create-admin
```

Use the administrator controls to ingest the current schedule and other live
sources. To exercise the backend fixture pipeline for isolated development or
tests, run `seed-demo` deliberately against a disposable database. Frontend
illustrative pages do not require database fixtures or provider requests.

Start the API in that terminal:

```powershell
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open a second PowerShell terminal and start the client:

```powershell
cd C:\Users\james\ai-sports-predictor\client
npm run dev
```

Then open [http://localhost:5173](http://localhost:5173).

The initial local administrator identity and password are configured only in the
Git-ignored `.env`. Set unique values before creating the account in any shared
environment. Production configuration rejects the documented development
placeholders, non-PostgreSQL databases, and demo mode.

## First live data and forecast run

1. Sign in as the local administrator.
2. Open **Data Health / Admin**.
3. Run **Refresh Today's Schedule**.
4. Run **Refresh MLB Data** and **Refresh Weather**. Statcast acquisition is
   disabled by default; enable and refresh it only after reviewing the upstream
   access and licensing requirements described in `docs/DATA_SOURCES.md`.
5. Run **Refresh Odds** once after your key is configured. The response displays records processed and updated quota state.
6. Run **Rebuild Features**, then **Generate Forecasts**. Forecast generation
   includes the seeded simulations; the separate **Run Simulations** control is
   an operational alias and should not be run immediately afterward unless you
   intentionally want another immutable forecast version.
7. If you intentionally enabled the virtual experiment with
   `OFFICIAL_POSITIONS_ENABLED=true`, use **Publish Official Virtual Positions**
   (API route `POST /api/v1/admin/jobs/publish-official-positions`). Publication
   is explicit, auditable, idempotent, and does not place a real wager.
8. Return to **Today's Forecasts** and open a matchup.

Every job is recorded with its ID, timestamps, status, counts, warnings, errors,
and source. A missing optional field lowers readiness; it is never silently
invented. A provider or API failure produces an unavailable state; it never
substitutes illustrative data. Use the explicit `/demo` routes when you intend
to inspect examples.

## Run tests and production builds

Preferred full verification (forces provider integrations, polling, and the
scheduler off for the test process):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

Backend tests use isolated SQLite databases; application defaults and production guards remain PostgreSQL-based:

```powershell
cd C:\Users\james\ai-sports-predictor\server
New-Item -ItemType Directory -Force ..\.test-artifacts | Out-Null
..\.venv\Scripts\python.exe -m pytest --basetemp=..\.test-artifacts\backend
```

Frontend tests and build:

```powershell
cd C:\Users\james\ai-sports-predictor\client
npm test
npm run build
```

Tests do not call The Odds API and do not consume quota.

## Repository map

```text
ai-sports-predictor/
├── server/app/          FastAPI API and domain services
├── server/tests/        Backend unit/integration tests
├── client/src/          React UI, pages, charts, services, types
├── data/raw/            Ignored immutable provider snapshots
├── data/curated/        Ignored normalized analytical extracts
├── data/features/       Ignored feature artifacts
├── docs/                Architecture, methodology, sources, dictionary, roadmap
├── notebooks/research/  Non-production exploration
├── scripts/             Windows setup and operational helpers
├── .env                 Local-only secrets/configuration (ignored)
├── .env.example         Credential-free configuration reference
└── docker-compose.yml   PostgreSQL, API, and web application
```

Read [the implementation plan](docs/PHASE_1_IMPLEMENTATION_PLAN.md), [methodology](docs/METHODOLOGY.md), [data-source notes](docs/DATA_SOURCES.md), and [roadmap](docs/ROADMAP.md) before changing models or ingestion behavior.

## Configuration reference

| Variable | Purpose | Development default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy PostgreSQL connection | Local Compose PostgreSQL on port 5433 |
| `THE_ODDS_API_KEY` | The Odds API credential | Empty; local `.env` only |
| `ODDS_POLLING_ENABLED` | Enables scheduled odds requests | `false` |
| `ODDS_POLL_INTERVAL_MINUTES` | Minimum scheduled polling interval | `60` |
| `ODDS_MARKETS` | Provider market keys | `h2h,spreads,totals` |
| `ODDS_REGIONS` | Provider book regions | `us` |
| `ODDS_MANUAL_COOLDOWN_MINUTES` | Blocks accidental repeated manual calls | `5` |
| `ODDS_QUOTA_FLOOR` | Stops live requests at/below the retained-credit floor | `10` |
| `OPEN_METEO_ENABLED` | Enables weather requests | `true` |
| `STATCAST_ENABLED` | Enables isolated Baseball Savant acquisition | `false` pending source review |
| `DEMO_MODE` | Allows deliberate development-only demo tooling; never API-error fallback | `true` |
| `APP_ENV` | Runtime safety profile | `development` |
| `DISPLAY_TIMEZONE` | Date/day boundary used by the daily dashboard | `America/New_York` |
| `SECRET_KEY` | Token-signing secret | Development-only placeholder |
| `CORS_ORIGINS` | Allowed browser origins | Local Vite origins |
| `ENABLE_ARBITRAGE_ANALYTICS` | Exposes basic analytics (never execution) | `false` |
| `OFFICIAL_POSITIONS_ENABLED` | Allows explicit publication to the virtual-only ASP ledger | `false` |
| `OFFICIAL_MAX_STAKE_PERCENTAGE` | Hard cap for one simulated position as a share of bankroll | `0.025` |
| `SIMULATION_DRAWS` | Monte Carlo draws | `20000` |
| `FORECAST_MIN_READINESS` | Minimum score for publication | `55` |
| `SCHEDULER_ENABLED` | Enables background schedule | `false` |
| `LLM_PROVIDER`, `LLM_API_KEY` | Optional future narrative provider | Empty; not required |
| `VITE_API_URL` | Browser API base | `http://localhost:8000/api/v1` |

## Troubleshooting

**`docker` is not recognized**

Install and start Docker Desktop, then reopen PowerShell. For an existing native PostgreSQL 16 service, update `DATABASE_URL` and skip `docker compose up -d db`.

**Port 5433, 8000, or 5173 is already in use**

Stop the conflicting service or edit the host-side port in `docker-compose.yml`; if the API port changes, update `VITE_API_URL` and `CORS_ORIGINS`.

**Database connection refused**

Wait for `docker compose ps` to show `db` healthy. Native development must use `localhost:5433`; the server container intentionally overrides the hostname to `db:5432`.

**No live odds appear**

Verify the key exists only in `.env`, restart the API after editing it, sign in as admin, and manually refresh odds. Scheduled polling remains off unless explicitly enabled. Inspect Data Health for provider errors and quota headers.

**No forecast is published**

Open the readiness panel. A game below `FORECAST_MIN_READINESS` remains visible but is not misrepresented as complete. Refresh missing sources or wait for probable-pitcher/market information.

**MLB or weather source error**

Use the recorded job error and raw archive reference. Live pages remain empty or
unavailable until a successful refresh. Illustrative examples are opt-in under
`/demo`; retry the provider rather than presenting examples as live.

**Resetting the development database**

Database deletion is intentionally not automated here. Export any evidence you need, then explicitly remove the Compose volume only if a full destructive reset is desired.

## Responsible use and known limitations

ASP is a forecasting experiment for analytics, education, research, and entertainment. Probabilities are estimates; market disagreement is not a guaranteed “value” or return. Do not chase losses. The application has no real-money transaction or wager-execution capability.

Public provider availability, probable pitchers, lineups, ballpark conditions, historical Statcast access, and odds licensing can limit completeness. Closing-line and historical-market fields remain null when actual observations do not exist. See the methodology and data-source documentation for the full limitations and commercial licensing review list.
