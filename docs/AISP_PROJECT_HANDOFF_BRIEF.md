# AI Sports Predictor — Project Handoff Brief

_Prepared 2026-08-22 for a new project-management context. Ground-truth snapshot of what the project is, how it is built, its current state, and what remains._

---

## 1. One-paragraph snapshot

**AI Sports Predictor (ASP / "AISP")** is a local-first, single-developer **sports forecasting laboratory**. Phase 1 targets **MLB** while keeping the data model sport-neutral for a planned NFL expansion. It ingests current schedule/pitcher/weather/odds data, produces **reproducible probabilistic forecasts** (rating + run-expectancy + gradient-boosting ensemble with seeded Monte Carlo simulation), analyzes **market disagreement**, keeps **immutable versioned forecast history**, and runs a **transparent virtual $1,000 bankroll experiment**. It has **no real-money wagering and no sportsbook account integration** — it is analytics/research/education only. It is currently a **private, pre-public application** being hardened toward an authenticated beta.

---

## 2. Quick facts

| Item | Value |
|---|---|
| Owner / developer | James Gearheart (solo) |
| Domain (Phase 1) | MLB baseball forecasting; NFL planned |
| App type | Full-stack web app (FastAPI + React), local-first |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 16, Alembic |
| Frontend | React 18 + TypeScript, Vite 6, React Router 7, ECharts |
| Deployment | Docker Compose (db + api + client) or native Windows |
| Repo | `C:\Users\james\ai-sports-predictor`, git, branch `main`, **no remote configured** |
| Current readiness | **READY_FOR_AUTHENTICATED_BETA** (single-instance / private beta only) |
| Real-money capability | **None** (by design) |
| Test coverage | Large backend pytest suite (~100+ files) + frontend Vitest suite |
| Security | Full adversarial audit + hardening pass completed; 0 P0 / 0 P1 |

---

## 3. Product vision & scope

**What it IS:** a transparent, reproducible forecasting/research tool. Everything shows its work — probabilities not "picks," explicit data-provenance states, immutable audit trails, and honest "insufficient data" outcomes rather than invented values.

**What it explicitly is NOT (hard product guardrails):**
- No real-money wagering, no bet placement, no sportsbook account linkage.
- No "guaranteed value"/returns language — market disagreement ≠ profit.
- Never substitutes demo/illustrative data for a live/provider failure.
- Never silently invents a missing field; missing data lowers a readiness score instead.

**Data provenance model (core design principle).** Every analytical payload declares one of four states:
- **LIVE** — backed by persisted live records.
- **EMPTY** — API/DB reachable but no applicable record yet.
- **UNAVAILABLE** — resource failed/unreachable; the client does **not** fill it with examples.
- **DEMO** — illustrative fixtures, reachable only through explicit `/demo` routes in development, always labeled "ILLUSTRATIVE DEMO — NOT LIVE RESULTS."

---

## 4. Tech stack

**Backend (`server/`)**
- Python 3.12; FastAPI + Uvicorn; Pydantic v2 / pydantic-settings.
- SQLAlchemy 2.0 ORM; Alembic migrations; PostgreSQL 16 (prod/dev), SQLite (tests).
- httpx (async provider clients); APScheduler (background jobs, off by default).
- numpy, scikit-learn, xgboost, joblib, polars (modeling / dataset processing).
- PyJWT (HS256), pwdlib[argon2] (password hashing), structlog (JSON logging).
- pytest / pytest-asyncio / pytest-cov.

**Frontend (`client/`)**
- React 18 + TypeScript ~5.7; Vite 6; React Router 7.
- ECharts (`echarts-for-react`) for charts; lucide-react icons.
- Vitest + Testing Library + jsdom; served via nginx in the Docker image.

**Infra**
- Docker Compose: `db` (postgres:16-alpine, port 5433→5432), `api` (port 8000), `client` (port 5173→80). Postgres persisted in named volume `asp_postgres_data`.
- Native Windows workflow via `scripts/setup.ps1` / `scripts/verify.ps1`, `.venv`.

---

## 5. Repository layout

```
ai-sports-predictor/
├── server/app/            FastAPI API + domain services (see §6)
│   ├── api/               routes.py, dependencies.py (auth/DI)
│   ├── core/              config, security (JWT/hash), middleware, logging, rate_limit, time
│   ├── models/            entities.py (all SQLAlchemy tables)
│   ├── schemas/           api.py (Pydantic request/response contracts)
│   ├── services/          auth, read, forecast, game-state, historical-research read services
│   ├── data_sources/      provider adapters (mlb, odds, weather, statcast) + request_policy
│   ├── ingestion/         raw archive (append-only) + normalizer
│   ├── features/          feature engineering (bullpen, lineup, rolling, talent, readiness…)
│   ├── forecasting/       ensemble, simulation (Monte Carlo), registry, evaluation
│   ├── modeling/          estimators, calibration, stacking, metrics, pipeline (Gen-1)
│   ├── training/          Gate-3 dataset/artifacts/pipeline (provider-free training)
│   ├── historical/        Gate-3A retrospective reconstruction warehouse
│   ├── gen2/              Gate-3C player/event track + FD1 free-data + exchange/identity-bridge
│   ├── bankroll/          virtual position sizing + settlement
│   ├── jobs/              orchestrator (job catalog) + scheduler (APScheduler)
│   ├── audit/             immutability guards (append-only enforcement)
│   ├── analytics/, markets/  snapshots + odds math (no-vig, best line, edges)
│   ├── cli.py             operational CLI (see §12)
│   └── main.py            app factory (create_app), middleware, CORS, docs gating
├── server/tests/          backend unit/integration + security suites
├── server/alembic/        migrations (16 revisions, head 20260818_0016)
├── client/src/            React UI (pages, components, hooks, services, types)
├── data/{raw,curated,features}/   ignored provider snapshots & artifacts
├── docs/                  plans, gate reports, methodology, security (see §18)
├── notebooks/research/    non-production exploration
├── scripts/               setup.ps1, verify.ps1, security_gate.ps1
├── .env                   local-only secrets (GIT-IGNORED)
├── .env.example           credential-free reference
└── docker-compose.yml
```

---

## 6. Backend architecture (request flow)

- **API layer** (`api/routes.py`): thin FastAPI routes. Auth/authz via dependencies (`CurrentUser`, `AdminUser`) that decode the JWT and **re-read the user from the DB** (role/status are DB-authoritative, never trusted from token claims).
- **Read services** (`services/read_service.py`, `historical_research_read_service.py`): all public read endpoints go through these; they read persisted DB state only and **never trigger provider calls**.
- **Job orchestrator** (`jobs/orchestrator.py`): admin/scheduler-triggered pipeline steps that MAY call providers, guarded by a denial-of-wallet **ProviderRequestLedger** (exactly-once reservation, quota floor, daily budget) and a bounded retry/circuit-breaker executor (`data_sources/request_policy.py`).
- **Ingestion** (`ingestion/archive.py`): append-only, checksummed raw provider snapshots (immutable; path-sanitized).
- **Immutability guards** (`audit/immutability.py`): installed at startup to enforce append-only semantics on forecasts, odds, and evidence.
- **Everything time-aware / point-in-time:** forecasts, feature snapshots, and historical reconstruction all respect "as-of" cutoffs to prevent leakage.

---

## 7. Domain model (key entities)

Tables live in `server/app/models/entities.py`. The important concepts:

- **User** — email, argon2 `password_hash`, role (`ADMIN`/`MEMBER`), status (`ACTIVE`/`DISABLED`/`PENDING`), `token_version` (session revocation), `last_login_at`.
- **Entitlement** — per-user, per-kind entitlement ledger row (foundation for future premium tiers; not yet monetized).
- **Game / GameTeam / Team / Player** — canonical schedule and participants.
- **OddsSnapshot** — append-only multi-book odds observations (h2h/spreads/totals).
- **Forecast / ForecastTrack / ForecastOutcome** — versioned, append-only forecasts; separate tracks (current-development vs research) enforced in the DB.
- **OfficialAspPosition** — the transparent virtual $1,000 bankroll's published positions (idempotent, capped, virtual-only).
- **ModelVersion** — model registry with governance status (`DEVELOPMENT`/`CHAMPION`/etc.).
- **JobRun** — every pipeline job with id, timestamps, status, counts, warnings, errors, source.
- **ProviderRequestLedger** — exactly-once provider-request accounting for quota / denial-of-wallet control.
- **GameFeatureSnapshot / IngestionRun** — point-in-time feature captures and ingestion provenance.
- Plus extensive Gate-3A/3C historical + player/event reconstruction tables.

---

## 8. The "Gate" development methodology (important context)

Development has proceeded through numbered, heavily documented **gates**, each with a plan + evidence report in `docs/`. The new manager should understand these because most of the codebase's complexity is here, and each gate has a strict **evidence boundary** (what was and was NOT proven):

- **Phase 1** — the full vertical slice: adapters, model, ensemble, UI (8 screens), auth, tests.
- **Gate 2** — live vertical slice, point-in-time integrity, provenance uniqueness, lifecycle refresh.
- **Gate 3** — *provider-free* model training audit reading only persisted LIVE evidence. Result at the frozen cutoff: `INSUFFICIENT_DATA / NO_PROMOTION` (no prospective model promoted).
- **Gate 3A** — governed **historical warehouse** + point-in-time reconstruction of the 2025 MLB season (A1 raw finals/box scores; A2 chronological feature materialization). Strict Tier A population = **2,428 rows**; Tier B "enhanced" shadow is research-only, training-ineligible.
- **Gate 3B** — trained a Gen-1 run-strength model with Platt calibration under a strict promotion policy; classified **RESEARCH_CHALLENGER / RESEARCH_ONLY_NO_PROMOTION** (not promoted to production). Zero provider calls.
- **Gate 3C** — provider-free foundation for a **Gen-2 player/event research track** (pitch/PA partitions, base/out simulation, forecast-track separation). Status `PROVIDER_FREE_FOUNDATION_COMPLETE / BLOCKED_SOURCE_AUTHORIZATION`.
  - **FD1** — "free baseball data" acquisition/warehouse work (Retrosheet/open data), with recovery/finalization tooling.
  - **X1 / X2 exchange** — an **immutable dataset exchange** and **identity bridge** consumer: hardened importers that verify signed/hashed manifests, enforce path containment, reject symlinks/hardlinks/pickle, etc. This is the trust boundary where **ASP Model Studio** (a separate sibling project) can hand datasets/model identity to AISP.

**Key governance theme:** training/evaluation is deliberately **provider-free and fails closed**; nothing is promoted to "champion" without passing strict holdout/sample gates. Honest "not ready / not promoted" states are the norm and are intentional.

---

## 9. Data sources / providers

| Provider | Use | Default | Notes |
|---|---|---|---|
| **MLB Stats API** (`statsapi.mlb.com`) | schedule, probable pitchers, finals, box scores | on (manual) | free, hardcoded host |
| **The Odds API** (`api.the-odds-api.com`) | h2h/spreads/totals odds | **polling off** | paid/quota-limited; key in `.env` only; manual admin refresh; quota tracked |
| **Open-Meteo** (`api.open-meteo.com`) | ballpark weather | on | free |
| **Baseball Savant / Statcast** (`baseballsavant.mlb.com`) | pitch-level data | **off** | pending source/licensing review |
| **Retrosheet / open data** | historical (FD1) | manual | attribution required (`docs/OPEN_DATA_ATTRIBUTION.md`) |

All outbound hosts are hardcoded (no SSRF surface). Provider calls happen only via admin jobs or the (default-off) scheduler.

---

## 10. Modeling & forecasting

- **Ensemble:** transparent blend of a rating/baseline component, a run-expectancy component, and a gradient-boosting (XGBoost) component (default weights 0.30 / 0.45 / 0.25, normalized).
- **Simulation:** seeded Monte Carlo (default 20,000 draws, fixed seed) → run distributions, win probabilities, totals, run-difference.
- **Calibration:** Platt scaling; evaluation via Brier score, log loss, ECE, run MAE.
- **Market math:** no-vig fair prices, best-line, consensus, model edge, opportunity tiers (WATCH / STANDARD EDGE / STRONG EDGE / NO BET), optional arbitrage analytics (never execution).
- **Governance:** model registry + immutable artifacts; strict training/holdout floors; provider-free training; nothing auto-promoted.

---

## 11. API surface (prefix `/api/v1`)

**Public (unauthenticated) reads:** `/public/config`, `/dashboard/today`, `/games/{id}`, `/markets/board`, `/performance`, `/research/history`, `/research/history/games`, `/bankroll`, `/system/ping`. App-root `/healthz` = public liveness.

**Auth:** `POST /auth/register`, `POST /auth/login`, `GET /auth/me`, `POST /auth/logout` (all auth POSTs rate-limited).

**Admin-only:** `GET /admin/jobs`, `POST /admin/jobs/{job_name}` (provider-triggering, rate-limited), plus operational telemetry `GET /health` and `GET /health/sources` (now AdminUser-gated).

---

## 12. Operations — jobs & CLI

**Admin job catalog** (`AVAILABLE_JOBS`): `refresh-schedule`, `refresh-mlb-data`, `refresh-statcast`, `refresh-odds`, `refresh-weather`, `rebuild-features`, `generate-forecasts`, `run-simulations`, `publish-official-positions`, `settle-games`, `recalculate-performance`, `daily-pipeline`, `finalize-games`, `controlled-live-validation`.

**CLI** (`python -m app.cli …`): `create-admin`, `seed-demo`, `train-mlb-models --as-of …`, `backfill-mlb-history …`, `report-mlb-history --season …`, plus FD1 recovery/identity tooling.

**Typical first live run:** sign in as admin → Data Health → Refresh Schedule → Refresh MLB Data + Weather → Refresh Odds (once) → Rebuild Features → Generate Forecasts → view Today's Forecasts.

---

## 13. Frontend (8 Phase-1 screens + auth)

Pages (`client/src/pages/`): **Today's Forecasts** (`/`), **Historical Research** (`/research/history`), **Game Analysis** (`/games/:id`), **Market Board** (`/markets`), **Model Performance** (`/performance`), **$1,000 Experiment / Bankroll** (`/experiment`), **Methodology** (`/methodology`), **Data Health / Admin** (`/admin`, admin-only), **Account / Auth** (`/account`). Demo variants under `/demo/*` (dev only, static fixtures). Auth state via `useAuth` (bearer token in `sessionStorage`); role-gated navigation and an admin route guard.

---

## 14. Configuration (env, `.env` is git-ignored)

Key vars: `DATABASE_URL`, `THE_ODDS_API_KEY`, `ODDS_POLLING_ENABLED` (false), `ODDS_*` quota/cooldown controls, `OPEN_METEO_ENABLED`, `STATCAST_ENABLED` (false), `DEMO_MODE`, `APP_ENV` (development/test/production), `SECRET_KEY`, `CORS_ORIGINS`, `OFFICIAL_POSITIONS_ENABLED` + `OFFICIAL_MAX_STAKE_PERCENTAGE` (0.025 cap), `SIMULATION_DRAWS`, `FORECAST_MIN_READINESS` (55), `SCHEDULER_ENABLED` (false), `GATE3_*` training policy, rate-limit settings, `ADMIN_EMAIL`/`ADMIN_PASSWORD`, `VITE_API_URL`. **Production fails closed:** rejects placeholder/short `SECRET_KEY`, default/short `ADMIN_PASSWORD`, non-PostgreSQL DB, `DEMO_MODE=true`, and wildcard/non-HTTPS CORS.

---

## 15. Security posture (audit + hardening complete)

A dedicated adversarial audit and an authenticated-beta hardening pass are **done and committed** (`docs/AISP_SECURITY_AUDIT_FINDINGS_2026-08-18.md`, `docs/AISP_AUTHENTICATED_BETA_SECURITY_GATE.md`). **0 P0 / 0 P1.** Implemented controls:
- Argon2 hashing; JWT HS256 pinned; token type/subject validation; **role re-read from DB** (forged `role=admin` claim cannot escalate); registration hardcodes MEMBER; disabled users rejected immediately; login dummy-hash timing defense.
- **Rate limiting** (in-process, non-distributed) on login/register/admin-jobs → 429 + Retry-After; socket-peer keyed.
- **Token revocation foundation:** `token_version` + `jti`; `POST /auth/logout` revokes; client logout calls it.
- Production **fail-closed guards** (CORS, docs disabled, secrets, demo, DB); **HSTS** in production; standard security headers; API CSP.
- Operational health endpoints **admin-only end-to-end** (backend + frontend guard).
- Registration password minimum raised **8 → 12** (login floor unchanged for legacy accounts).
- Denial-of-wallet controls (ProviderRequestLedger, quota floor/budget, circuit breaker); hardcoded provider hosts (no SSRF); parameterized SQL; hardened dataset-exchange consumer (no pickle/joblib at that boundary); `.env` ignored + API-key log redaction.
- **Security gate:** `scripts/security_gate.ps1` runs the focused security suites and fails non-zero on regression.

---

## 16. Testing

- **Backend:** large pytest suite (~100+ files) covering gates, ingestion, modeling, exchange, and security. Tests use isolated SQLite; never call The Odds API / consume quota. Run: `scripts/verify.ps1` or `pytest` in `server/`.
- **Frontend:** Vitest + Testing Library (routes, pages, auth/admin access, logout). Run: `npm test` in `client/`. (Note: a couple of heavy route tests can time out only under concurrent load; they pass in isolation.)

---

## 17. Git state

- Branch `main`; **HEAD `92f92dd`** = `feat: harden AISP for authenticated beta`; worktree clean; **no git remote configured** (nothing is pushed anywhere).
- Recent lineage: harden-for-beta → publish MLB player identity bridge → publish immutable MLB dataset exchange → FD1 open-data warehouse → governed Gen-2 foundation.

---

## 18. Documentation index (`docs/`)

Plans/reports per gate: `PHASE_1_*`, `GATE_2_*`, `GATE_3_*`, `GATE_3A_*` (incl. A1/A2), `GATE_3B_*`, `GATE_3C_*` (incl. FD1, X1 exchange, X2 identity bridge). Cross-cutting: `METHODOLOGY.md`, `DATA_SOURCES.md`, `FREE_BASEBALL_DATA_SOURCE_INVENTORY.md`, `GEN2_FREE_DATA_FEATURE_MATRIX.md`, `OPEN_DATA_ATTRIBUTION.md`, `ROADMAP.md`, `FIRST_LIVE_LAUNCH_REPORT_2026-08-12.md`. Security: the three `AISP_*`/`AI_SPORTS_PREDICTOR_DEEP_*` docs. Subfolders: `architecture/`, `contracts/`, `data_dictionary/`, `screenshots/`.

---

## 19. Ecosystem boundaries (must respect)

AISP is one project in a **walled multi-project ecosystem**. These siblings are **separate repos and out of scope** for AISP work — do not read from or copy code between them:
- `C:\Users\james\asp-model-studio` — the model/dataset **producer**; hands datasets + model identity to AISP via the hardened **exchange / identity-bridge** consumer (Gate 3C X1/X2). This is the only sanctioned integration path.
- `C:\Users\james\ds-workflow-starter` — separate tooling.
- **Financial ATLAS** — a separate financial system; strictly off-limits.

Cross-project trust is one-directional and hash/manifest-verified at the exchange boundary.

---

## 20. Known issues / immediate follow-ups

1. **⚠️ Docker Compose healthcheck is broken by the health-endpoint hardening.** `docker-compose.yml` sets the `api` healthcheck to hit `/api/v1/health`, which is now **admin-only (401)**. The container will never report healthy, so `client` (which `depends_on: api healthy`) won't start. **Fix (one line):** point the healthcheck at the public `/healthz` instead. *Native Windows dev is unaffected.* (README's "API health" URL note also needs the same update.)
2. Frontend build emits a >500 kB chunk warning (ECharts) — pre-existing, cosmetic; consider manual chunking later.
3. Rate limiter is **in-process / non-distributed** — fine for single-instance beta; needs a shared store (Redis) behind a trusted proxy before multi-instance/public.
4. JWT is stored in `sessionStorage` — acceptable for private POC; a web→HttpOnly-cookie migration (+ CSRF) is required before a payment-connected/public beta (mobile keeps bearer tokens).

---

## 21. Roadmap / what's next

**Current classification: `READY_FOR_AUTHENTICATED_BETA` (single-instance / private).**

**Before payment-connected beta (all deferred, none implemented):**
- Stripe + Patreon **server-authoritative** integration with signed, timestamp-checked, deduplicated, idempotent webhooks; server-side **entitlement ledger** (browser never grants premium).
- JWT web→cookie migration + CSRF; distributed rate-limit store; breached-password check (offline/k-anonymity).

**Before public production (additionally):**
- External penetration test; multi-instance abuse controls behind a trusted proxy; SPA Content-Security-Policy; monitoring/alerting on auth + denial-of-wallet events; incident-response & credential-rotation runbooks.
- **Rotate the Odds API key before any beta** (it currently lives only in the local ignored `.env`; not rotated automatically).

**Product roadmap themes** (see `docs/ROADMAP.md`): promote a Gen-1 model out of research once evidence supports it; advance the Gen-2 player/event track (currently blocked on source authorization); expand historical/Statcast coverage subject to licensing; NFL expansion using the sport-neutral model; future virtual bankroll / community / leaderboard features (specified, not built) with strict separation between customer-subscription, virtual-bankroll, and any future company real-money ledgers.

---

## 22. How to talk to this project (conventions the manager should enforce)

- **Honesty over completeness:** never invent data; declare EMPTY/UNAVAILABLE; keep DEMO opt-in and labeled.
- **Provider frugality:** live provider calls are deliberate, admin-gated, quota-tracked; polling stays off in dev.
- **Reproducibility & immutability:** seeded simulations; append-only forecasts/odds; immutable audit records; point-in-time correctness (no leakage).
- **Governance gates:** no model promotion without passing strict sample/holdout gates; training is provider-free.
- **Security is DB-authoritative:** roles/entitlements re-checked server-side, never trusted from the client or token claims.
- **Ecosystem walls:** respect the boundaries with ASP Model Studio, ds-workflow-starter, and financial ATLAS.
