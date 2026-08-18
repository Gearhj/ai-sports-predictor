# AI Sports Predictor — Deep Security & Financial-Integration Readiness Audit

**Type:** Adversarial security review (OWASP ASVS / API Security), bounded pass
**Date:** 2026-08-18
**Repository:** `C:\Users\james\ai-sports-predictor` (only)
**Sibling repos (`ds-workflow-starter`, financial ATLAS):** not accessed.
**Read-only review:** no code repairs were made in this repository (no P0/P1 found);
findings are documented with recommended fixes.

---

## 1. Executive verdict

AI Sports Predictor is a FastAPI (Postgres) backend + React SPA with a
**strong, fail-closed security foundation**: Argon2 password hashing, JWT (HS256)
with **algorithm pinning**, a fail-closed auth dependency, server-side admin and
**server-side entitlement ledger**, a production config guard that rejects weak
secrets/demo/default admin, hardcoded provider egress (no SSRF), constant/ORM SQL
(no injection), and a **content-addressed model-exchange verifier** with per-file
SHA-256, pinned expected authority digests (downgrade prevention), and
symlink/hardlink/reparse/escape rejection. Imported exchange bundles are **data
(parquet/JSON), not executable models** — so there is no bundle-deserialization RCE.

Payments/Patreon are **intentionally deferred** (stated in the UI); the entitlement
table already exists server-side, which is the correct foundation.

**No P0 findings. No P1 findings.** Findings are P2/P3 hardening and
production-readiness items.

| Severity | Count |
|----------|-------|
| P0 | 0 |
| P1 | 0 |
| P2 | 4 |
| P3 | 3 |

**Standalone readiness:** **SAFE FOR PRIVATE POC / LOCAL DEVELOPMENT.** Approaching
**AUTHENTICATED BETA** once the P2 items (rate limiting, token-storage decision,
production secret provisioning) are addressed. Not payment-connected.

---

## 2. Baseline

- **HEAD:** `9f3ba31621ea3284d34bc92f17a3c3e66fe793c5`; branch `main`; worktree clean
  (0 tracked changes). This audit adds only this report (untracked). No commits.

---

## 3. Architecture / attack surface

- Backend `server/app/`: `api`, `core` (security/config), `db` (SQLAlchemy/Postgres,
  Alembic), `data_sources` (MLB StatsAPI, The Odds API, Open-Meteo), `ingestion`,
  `jobs` (scheduler, off by default), `forecasting`/`modeling`/`training` (Gate 3
  offline), `bankroll` (virtual), `markets`, `audit`, and `gen2/exchange`
  (identity-bridge model/dataset exchange — the ASP→AISP trust boundary).
- Frontend `client/` (React/Vite); JWT bearer stored in `sessionStorage`.
- Postgres; `entitlements` table (MEMBER/ADMIN/PREMIUM) already present.

---

## 4. Authentication & session (ASVS V2/V3) — strong

- **Password hashing:** `pwdlib` `PasswordHash.recommended()` → Argon2. Verify wraps
  exceptions → constant behavior.
- **JWT:** HS256; `decode_access_token` pins `algorithms=[ALGORITHM]` (blocks
  alg-confusion / `alg=none`), validates `type=="access"` and `sub`, enforces `exp`.
- **Auth dependency** (`api/dependencies.py`): requires `Bearer`, verifies the token,
  loads the user, and requires `status == ACTIVE`; any failure → 401. **No absent
  context becomes a user or admin.** `require_admin` enforces `role == ADMIN` → 403.
- **Config production guard** (`core/config.py::production_guards`) **fails closed**:
  in `production` it rejects the placeholder/short `SECRET_KEY` (<32), non-Postgres
  `DATABASE_URL`, `DEMO_MODE=true`, and the default/short `ADMIN_PASSWORD`.

**P2-1 (token in `sessionStorage`).** The JWT is stored in `sessionStorage` and sent
as `Authorization: Bearer` (`client/src/services/api.ts`). Any XSS would expose it.
This is the standard SPA bearer tradeoff and `sessionStorage` is better than
`localStorage`, but for a membership product consider an **HttpOnly, Secure,
SameSite cookie** for the token (plus CSRF defense) to remove XSS token theft.
Pair with a strict Content-Security-Policy.

**P3 (no idle-timeout / refresh-rotation).** Access token TTL is 5–1440 min
(default 60). There is no refresh-token rotation or idle timeout; acceptable for POC,
revisit for beta.

---

## 5. Authorization / tenant isolation (ASVS V4) — sound

- Admin actions gated by `require_admin` server-side.
- **Entitlements are server-side** (Postgres `entitlements`, unique per user+kind).
  Access to premium/member content must be decided from this ledger, never from a
  client claim — the table + server dependency model supports this.
- User objects are looked up by authenticated `sub`; no user-id is taken from client
  input for object access in the reviewed paths.

**P2-2 (verify entitlement checks on every premium route).** The ledger exists; a
full route-by-route confirmation that every premium/official-position endpoint calls
a server-side entitlement check (not just `get_current_user`) was **not exhaustively
completed** in this bounded pass. Recommend an explicit `require_entitlement(kind)`
dependency applied to every gated route + a test that a MEMBER-less user is 403.

---

## 6. Model-exchange trust boundary (ASP → AISP) — strong

`server/app/gen2/exchange/identity_bridge/*` validates imported bridge packages with:
- **Content addressing:** each package file's `sha256` is **recomputed from bytes**
  (`rows.py`) and the **semantic manifest digest** is recomputed and every derived
  identifier verified (`canonical.py`); mismatch raises.
- **Pinned expected authority** (`contracts.py` vs `trust.EXPECTED_BRIDGE_MANIFEST_SHA256`,
  `EXPECTED_ALIAS_AUTHORITY_SHA256`, `EXPECTED_DIMENSION_AUTHORITY_SHA256`) →
  **rollback/downgrade prevention** (only a known-good authority is accepted).
- **Filesystem hardening** (`filesystem.py`): rejects symlinks, junctions/reparse
  points, hardlinks (`st_nlink != 1`), and path escape (`resolve(strict)` +
  containment); **parquet-only**, forbidden file types rejected, ASP runtime paths
  rejected (`security.py`).
- **No `pickle`/`joblib`/executable deserialization** anywhere in the exchange path —
  bundles are **data, not code**. There is therefore **no tampered-bundle RCE**.
- Validator is **package-only** (no DB/provider/publisher import path), so a
  malicious package cannot pivot into runtime authority.

**Verdict:** meets the required exchange controls (immutable manifest, SHA-256,
schema validation, source/feature authority, downgrade prevention, tamper rejection).
**P3:** add an explicit signature (e.g., detached signature over the manifest digest)
if bundles will ever transit an untrusted channel — content-addressing + pinned
digests already prevent tamper/downgrade for the local trust model.

---

## 7. Provider egress / SSRF / quota — sound

- Outbound HTTP uses **hardcoded base URLs** (`https://statsapi.mlb.com/...`, The Odds
  API, Open-Meteo). No API route fetches a **user-supplied URL** → no SSRF.
- **Quota discipline:** `ODDS_DAILY_REQUEST_BUDGET=1`, `ODDS_POLLING_ENABLED=false`,
  manual cooldown, quota floor. Strong denial-of-wallet protection for the metered
  provider. `SCHEDULER_ENABLED=false` / `EVENT_RELATIVE_SCHEDULER_ENABLED=false` by
  default.

---

## 8. Secrets — no committed secrets; one live local key to protect

- **`.env` is untracked** (gitignored via `.env`/`.env.*` with `!.env.example`); the
  `.gitignore` is comprehensive (keys, pem, service-accounts, dbs, parquet).
- The **live `.env` contains a real The Odds API key** (masked: `3ae6…59`, `.env:5`).
  It is **not committed** and not exposed to the frontend (backend-only; `VITE_*` vars
  contain only the API URL). Operationally acceptable, but treat as a live credential:
  never commit/log it, and rotate if it has ever been shared.
- `SECRET_KEY` and `ADMIN_PASSWORD` in `.env` are local-dev values; production is
  gated (see §4). No secret leaks via responses (exchange withholds DB URLs/creds) or
  the client bundle.

---

## 9. Payment / Patreon / Stripe readiness (deferred — greenfield)

No Stripe/Patreon/webhook/billing code exists; the UI states membership integration
is intentionally deferred. The **server-side entitlement ledger already exists**,
which is the correct base. Required architecture when billing is added:

- Client → **AISP backend** → **Stripe Checkout / Customer Portal** (Stripe-hosted).
- Secret key **server-side only**, outside Git; restricted key where possible;
  **test/live isolation**.
- **Signed webhook** → verify `Stripe-Signature` on the **raw body** before parsing →
  **event-ID dedup** + **event-type allowlist** → update the **server-side entitlement
  ledger** (already present) → entitlement decides access. **Never** grant from a
  redirect/success page.
- **Never trust** client-provided amount/price/subscription status; reconcile from
  Stripe. **No card/bank data stored**; future ACH via Stripe Financial Connections
  (tokenized), never raw bank credentials or routing/account numbers.

**Readiness verdict:** *NOT payment-connected; architecture specified.*

---

## 10. Bankroll / public-output integrity

- **Virtual bankroll / official positions** are gated: `OFFICIAL_POSITIONS_ENABLED=false`,
  `OFFICIAL_MAX_STAKE_PERCENTAGE` bounded (≤10%), `ENABLE_ARBITRAGE_ANALYTICS=false`
  by default. Stakes/positions are server-computed from persisted evidence
  (Gate 3 offline training reads persisted evidence only; never calls a provider or
  publishes official positions).
- **P3 (leaderboard/public-forecast abuse):** when public forecasts / leaderboards /
  a real ASP Model Bankroll display are exposed, add per-IP rate limiting and ensure
  all displayed numbers derive from the **server-side** ledger (no client-submitted
  bankroll/history). Hypothetical/virtual histories must be clearly labeled and
  server-authoritative.

---

## 11. Demo mode / honest states

Demo data is **explicit seeded fixtures** (`seed-demo` CLI, `source_mode == "DEMO"`,
`DemoSportsSource`); `data_mode` is derived from what is actually persisted
(live/demo/empty). **API failures do not silently activate demo.** Production rejects
`DEMO_MODE=true`. Honest-state discipline is good.

---

## 12. SQL / database

- SQLAlchemy ORM (`session.get`, `select(...)`); the only raw `text()` uses are
  constant (`SELECT 1`, `SET TRANSACTION READ ONLY`, `SHOW transaction_read_only`,
  and a module-constant `_SNAPSHOT_QUERIES` list). **No string-interpolated user SQL**
  → no injection surface found.
- Alembic migrations present; entitlements have a unique constraint (user_id, kind).

---

## 13. Rate limiting / abuse

**P2-3 (no general rate limiting).** Provider quota is well controlled, but there is
no per-IP/per-user throttle on `/api/v1/auth/*` (login/registration) or expensive
endpoints. Add before authenticated/public beta (credential-stuffing / DoS /
denial-of-wallet once public forecasts or payments exist).

---

## 14. Dependency / supply chain

- Python deps managed (pyproject/requirements); `jwt` (PyJWT), `pwdlib` (Argon2),
  `httpx`, SQLAlchemy, pydantic-settings — modern, appropriate choices.
- Client: React/Vite. Recommend adding `pip-audit`/`npm audit` to CI (follow-up, not a
  blocker for POC). No broad CVE scan performed (per scope).

---

## 15. Findings summary & production blockers

- **P2-1** JWT in `sessionStorage` → prefer HttpOnly cookie + CSP (or accept with
  strong CSP + no XSS sinks).
- **P2-2** Add explicit `require_entitlement` on every premium route + a 403 test.
- **P2-3** Add rate limiting on auth/expensive endpoints.
- **P2-4 (production secret provisioning)** Set real `SECRET_KEY` (≥32) and
  `ADMIN_PASSWORD`, `DEMO_MODE=false`, Postgres URL — already **machine-enforced** by
  `production_guards`; listed as the go-live checklist item.
- **P3** exchange bundle signature (if untrusted transit), refresh-token rotation /
  idle timeout, public-output/leaderboard rate limiting.

**Payments:** implement §9 before any billing. **Model-exchange:** verified strong.

---

## 16. Confirmations

Real Stripe calls = 0 · real bank calls = 0 · sports-provider calls = 0 (no live
polling triggered; review was static/local) · no real payment objects created · no
full credentials printed (masked only) · no Git commits/pushes/remote changes ·
financial ATLAS / `ds-workflow-starter` not accessed. This report is the only
untracked addition; no code was modified in this repository.
