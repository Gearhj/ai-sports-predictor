# AI Sports Predictor — Deep Adversarial Security Audit (Findings)

**Date:** 2026-08-18
**Auditor model:** Claude Code (Fable 5)
**Scope:** `C:\Users\james\ai-sports-predictor` only. ASP Model Studio, ds-workflow-starter, and financial ATLAS were **not** accessed.
**Standard:** OWASP ASVS / OWASP API Security, code-path traced (not checklist-only).
**Governing question:** *If AISP were exposed to the public internet tomorrow, how could a real attacker compromise it?*

---

## 0. Baseline (captured before any change)

| Item | Value |
|------|-------|
| `git rev-parse HEAD` | `9f3ba31621ea3284d34bc92f17a3c3e66fe793c5` |
| `git status --short` | only `?? docs/AI_SPORTS_PREDICTOR_DEEP_SECURITY_AUDIT.md` (the brief) |
| `git diff --stat` | empty (clean tree) |
| `git remote -v` | **none configured** (no push target exists) |

No reset/clean/stash/restore/checkout/push was performed. No external provider, Stripe, or Patreon calls were made. No credentials were printed or rotated.

---

## 1. Executive summary

**Overall posture: strong for a pre-public codebase.** The authentication core, provider denial-of-wallet controls, the immutable dataset-exchange trust boundary, secret hygiene, and SQL access are all built defensively and hold up under adversarial tracing. The realistic current attack surface is small: the app today is a mostly read-only forecasting API with register/login/me and two admin-only job routes. Stripe, Patreon, premium entitlements, virtual bankroll, leaderboards, and cross-boundary *model* (estimator) imports are **not yet implemented**, so most of the threat model is forward-looking.

- **P0 (critical): none found.** No authentication bypass, no cross-customer data path, no remotely reachable RCE, no payment/entitlement logic to manipulate yet.
- **P1 (serious): none currently exploitable.** Role authorization is server-side and re-read from the database, so a forged role claim gains nothing; there are no user-owned resources to IDOR; no SQL injection or SSRF path exists.
- **P2 (hardening / production-readiness):** missing rate limiting (**fixed in this pass**); latent pickle/`joblib` deserialization that is safe today but must never be pointed at an imported bundle.
- **P3 (minor):** JWT-in-`sessionStorage` (migration plan, not a rewrite), no server-side token revocation, permissive-when-misconfigured CORS credentials, unauthenticated operational disclosure, weakish password policy, public OpenAPI docs.

**Change made this pass:** implemented a bounded, non-distributed, server-side rate-limiter on `auth/login`, `auth/register`, and the admin job trigger, plus 12 adversarial regression tests. No auth rewrite was performed (deliberately).

---

## 2. Route / security matrix (generated from code)

Source: [server/app/api/routes.py](../server/app/api/routes.py), [server/app/api/dependencies.py](../server/app/api/dependencies.py), [server/app/main.py](../server/app/main.py). Prefix: `/api/v1`.

| Method | Path | Auth | Admin | User-owned | Rate-limited | External cost | Mutates | Sensitive resp. |
|--------|------|------|-------|-----------|--------------|---------------|---------|-----------------|
| GET | `/public/config` | no | no | – | no | no | no | no |
| GET | `/dashboard/today` | no | no | – | no | no | no | no |
| GET | `/games/{game_id}` | no | no | – (public data) | no | no | no | no |
| GET | `/markets/board` | no | no | – | no | no | no | no |
| GET | `/performance` | no | no | – | no | no | no | no |
| GET | `/research/history` | no | no | – | no | no | no | no |
| GET | `/research/history/games` | no | no | – | no | no | no | no |
| GET | `/bankroll` | no | no | – (read-only transparency) | no | no | no | no |
| GET | `/health/sources` | no | no | – | no | no | no | operational (quota remaining, jobs) |
| GET | `/health` | no | no | – | no | no | no | DB reachability |
| GET | `/system/ping` | no | no | – | no | no | no | no |
| POST | `/auth/register` | no | no | – | **yes (added)** | no | creates user | issues token |
| POST | `/auth/login` | no | no | – | **yes (added)** | no | last_login_at | issues token |
| GET | `/auth/me` | **yes** | no | self | no | no | no | own profile |
| GET | `/admin/jobs` | yes | **yes** | – | no | no | no | job history |
| POST | `/admin/jobs/{job_name}` | yes | **yes** | – | **yes (added)** | **yes (Odds API)** | runs pipeline | job result |
| GET | `/healthz` (app root) | no | no | – | no | no | no | no |

Observations: there are **no routes that accept a user/forecast/bet/subscription/entitlement ID owned by another user** — the only object-ID route (`/games/{game_id}`) serves public forecast data. There is therefore **no IDOR surface** today. The only provider-cost trigger reachable over HTTP is admin-gated.

---

## 3. Findings

### P2-1 — No rate limiting on authentication / admin routes  *(FIXED this pass)*

**Where:** previously absent across [routes.py](../server/app/api/routes.py).
**Risk:** `auth/login` allowed unlimited attempts → brute force / credential stuffing; `auth/register` allowed unlimited account creation; `admin/jobs/{job_name}` (once an admin token is held) could be hammered to spend Odds-API quota faster than the per-day ledger intends.
**Exploitability:** direct, no auth required for the login/register vectors.

**Remediation implemented:**
- New [server/app/core/rate_limit.py](../server/app/core/rate_limit.py): thread-safe, in-process **sliding-window** limiter. Keyed on the **socket peer** (`request.client.host`); `X-Forwarded-For` is honored **only** when `rate_limit_trust_forwarded_for=true` (off by default) so a spoofable header cannot widen the budget. Fails **open** on internal error (an availability control must not self-DoS).
- New settings in [config.py](../server/app/core/config.py): `rate_limit_enabled` (default `true`), `auth_rate_limit_per_minute` (default `10`), `admin_rate_limit_per_minute` (default `60`), `rate_limit_trust_forwarded_for` (default `false`).
- Wired as FastAPI dependencies on `auth/login`, `auth/register`, `admin/jobs/{job_name}`; returns **429 + `Retry-After`** when exceeded.
- Test isolation via an autouse reset fixture in [conftest.py](../server/tests/conftest.py).

**Classified explicitly as NON-DISTRIBUTED.** Counters live in one process — correct for local / private beta. **Production requirement:** move the store to Redis (or equivalent) shared across instances, and terminate TLS behind a trusted proxy that sets a reliable client address before enabling `rate_limit_trust_forwarded_for`. Also extend coverage to future password-reset and export endpoints.

### P2-2 — Pickle/`joblib` deserialization is safe today but is a latent RCE at the import boundary

**Where:** [forecasting/registry.py:75](../server/app/forecasting/registry.py#L75) (`joblib.load`), [training/artifacts.py:215-223](../server/app/training/artifacts.py#L215), [training/gate3b_artifacts.py:347](../server/app/training/gate3b_artifacts.py#L347).
**Analysis:** `joblib.load` is pickle-based → executes arbitrary code if fed an attacker-controlled file. `ModelArtifactStore.load` verifies a SHA-256 **against a manifest sitting in the same directory**, so the checksum guards *corruption*, not a malicious writer who controls both files. **Not remotely exploitable now**: every load path reads local `.runtime/...` artifacts produced by this same app. There is no code path where an externally supplied artifact is `joblib.load`-ed.
**Why it matters:** the threat model calls for importing **model bundles from ASP Model Studio**. The moment an imported estimator is loaded via `joblib`/`pickle`, this becomes a cross-boundary RCE.
**Requirement (do NOT wire an importer to these loaders):** the existing **dataset** exchange is the correct template — it is parquet/JSON/markdown only, never pickle (see §5). A future *estimator* exchange must (a) reject pickle/joblib/`dill`/`marshal`/`torch.load`, (b) prefer a data-only serialization (e.g. ONNX, or explicit coefficient/param JSON), (c) pin per-file hashes in a **signed** manifest verified before any load, and (d) reject unknown/downgraded versions.

### P3-1 — JWT stored in `sessionStorage` (XSS token-theft exposure)

**Where:** [client/src/services/api.ts:28-38](../client/src/services/api.ts#L28), attached as `Authorization: Bearer` at [api.ts:56](../client/src/services/api.ts#L56).
**Analysis:** a token in `sessionStorage` is readable by any successful XSS or a malicious browser extension. Mitigating factors observed: React auto-escapes; **no** `dangerouslySetInnerHTML`/`innerHTML` sinks exist in the client; no `javascript:` URL rendering was found. Residual risk is real but bounded.
**Decision — migration plan, not a rewrite (per brief §3):** the same backend is slated to serve a **mobile client**, which needs bearer tokens; a hard move to HttpOnly-cookie-only auth would destabilize that and add CSRF surface. Recommended production architecture:
- Web: short-lived access token in memory + refresh token in an **HttpOnly, Secure, SameSite=Strict** cookie; add CSRF protection (double-submit or `SameSite` + origin check) once cookies carry auth.
- Mobile: continue bearer tokens in the platform secure store (Keychain/Keystore).
- Keep authorization **server-side and identical** across both (it already is — see §4).
- Add a strict `Content-Security-Policy` to the SPA host ([client/nginx.conf](../client/nginx.conf)) to shrink XSS surface; the API already sets `default-src 'none'` for `/api/`.

### P3-2 — No server-side token revocation / logout is client-only

**Where:** logout clears `sessionStorage` only ([useAuth.tsx:50-53](../client/src/hooks/useAuth.tsx#L50)); tokens are valid until `exp` (default ≤60 min, [config.py](../server/app/core/config.py#L34)).
**Analysis:** acceptable for beta given short TTL, but there is no way to invalidate a leaked/stolen token before expiry, and disabling a user only takes effect on their **next** request (status is re-checked from DB each request — good — but the current token still authenticates until it expires only because status is re-read; note: `get_current_user` *does* re-check `UserStatus.ACTIVE`, so a disabled user IS locked out immediately — see positives §4). **Production:** add a token version / `jti` denylist (or rotate `secret_key` on mass revocation) and an explicit `/auth/logout` that revokes the refresh token once cookies are adopted.

### P3-3 — CORS `allow_credentials=True` with no production guard against wildcard origins

**Where:** [main.py:42-48](../server/app/main.py#L42), origins from `cors_origins` ([config.py](../server/app/core/config.py#L35)).
**Analysis:** defaults are explicit localhost origins (safe). But `production_guards` ([config.py:85](../server/app/core/config.py#L85)) does not reject `CORS_ORIGINS=*`. Browsers block `*`+credentials, but a future move to cookie auth makes a permissive origin list dangerous. **Recommendation:** in `production_guards`, reject `*` and any non-HTTPS origin.

### P3-4 — Unauthenticated operational disclosure

**Where:** `/health/sources` returns `odds_quota_remaining`, recent job history, source freshness; `/health` returns DB reachability.
**Analysis:** low sensitivity, but quota-remaining and job telemetry aid an attacker planning a denial-of-wallet or timing an ingestion window. **Recommendation:** gate `/health/sources` behind auth (or admin) for public deployment; keep a minimal unauthenticated `/healthz` liveness probe (already exists).

### P3-5 — Password policy and default admin credentials

**Where:** `password: min_length=8` ([schemas/api.py:538](../server/app/schemas/api.py#L538)); default `admin@example.com` / `REDACTED-CREDENTIAL` ([config.py:77-78](../server/app/core/config.py#L77)).
**Analysis:** 8-char minimum with no breach/complexity check is on the weak side. The **production guard already blocks** the default admin password and short secrets in prod ([config.py:85-95](../server/app/core/config.py#L85)) — good. **Recommendation:** raise to ≥12 and add a breached-password (k-anonymity) check before public launch; Argon2 via `pwdlib` recommended defaults are already used ([security.py:11](../server/app/core/security.py#L11)).

### P3-6 — OpenAPI docs public

**Where:** FastAPI defaults expose `/docs`, `/redoc`, `/openapi.json`.
**Analysis:** reveals the full API schema to anyone. Minor. **Recommendation:** disable or auth-gate docs in production (`FastAPI(docs_url=None, redoc_url=None)` when `app_env=="production"`).

---

## 4. What is done well (do not "fix" these)

- **JWT validation is correct.** Algorithm is pinned on decode (`algorithms=["HS256"]`, [security.py:48](../server/app/core/security.py#L48)) → `alg=none` and algorithm-substitution are rejected; `type` and `sub` claims are enforced; wrong-signature/expired tokens raise cleanly. Verified by new regression tests.
- **Authorization is server-authoritative.** `require_admin` re-reads the user from the DB and checks `user.role` ([dependencies.py:47](../server/app/api/dependencies.py#L47)); the JWT `role` claim is **not** trusted for authz. A forged `role=admin` claim yields **403** (test added). Disabled/deleted users are rejected on every request (`UserStatus.ACTIVE` check, [dependencies.py:39](../server/app/api/dependencies.py#L39)).
- **No mass-assignment.** `RegisterRequest` carries only email/password/display_name; role is hardcoded `MEMBER` ([auth_service.py:47](../server/app/services/auth_service.py#L47)). Admin bootstrap **refuses** to escalate an existing non-admin account ([auth_service.py:74](../server/app/services/auth_service.py#L74)).
- **Account-enumeration / timing defense.** Login verifies against a fixed dummy hash when the user is missing ([auth_service.py:26,59](../server/app/services/auth_service.py#L59)).
- **Denial-of-wallet controls are mature.** Provider calls are reachable only via admin job or the (default-off) scheduler; a bounded executor enforces retry caps, pacing, and a circuit breaker ([request_policy.py](../server/app/data_sources/request_policy.py)); an append-only `ProviderRequestLedger` reserves an **exactly-once** request keyed to request-shape + UTC budget day, with quota floor and daily budget ([jobs/orchestrator.py:355-413](../server/app/jobs/orchestrator.py#L355)); the Gate-2 controlled path adds strict single-request preflights ([orchestrator.py:644](../server/app/jobs/orchestrator.py#L644)).
- **No SSRF.** Every outbound host is hardcoded HTTPS (`api.the-odds-api.com`, `statsapi.mlb.com`, `baseballsavant.mlb.com`, `api.open-meteo.com`); no user input reaches host/scheme/port/path.
- **No SQL injection.** All access is SQLAlchemy ORM; every raw `text()` call uses a static string or bound parameters (`:timeout`), never string interpolation of user input.
- **Secret hygiene.** `.env` is gitignored, absent from local git history, and contains the only live secret (a 32-char Odds key); `.env.example` ships empty placeholders. A logging filter redacts `apiKey`/`api_key` query values, and httpx/httpcore transport logging is forced to WARNING ([core/logging.py](../server/app/core/logging.py)).
- **The dataset-exchange consumer is hardened** — see §5.
- **Immutable audit guards** are installed at startup ([main.py:21](../server/app/main.py#L21), [server/app/audit/immutability.py](../server/app/audit/immutability.py)).

---

## 5. Model / data exchange trust boundary (§15 of brief)

The implemented consumer is the **immutable sports *dataset* exchange** ([server/app/gen2/exchange/validator.py](../server/app/gen2/exchange/validator.py), [filesystem.py](../server/app/gen2/exchange/filesystem.py)). Traced against the brief's checklist:

| Requirement | Status |
|-------------|--------|
| Manifest identity/hash verified | ✅ `verify_manifest_identity` on canonical bytes |
| Every file hash recomputed | ✅ `sha256_file` per partition + per semantic artifact |
| Exact file inventory (no extra/missing) | ✅ set-equality of declared vs actual |
| Schema validated | ✅ parquet footer schema + registry fingerprint reconciliation |
| Path containment enforced | ✅ `resolve_under` + `validate_portable_uri` + `relative_to(root)` |
| Symlink / junction / reparse-point rejected | ✅ `_assert_no_links` (incl. Windows `FILE_ATTRIBUTE_REPARSE_POINT`) |
| Hardlink escape rejected | ✅ `st_nlink != 1` check |
| Unsupported file type rejected | ✅ only parquet/JSON/markdown; **no pickle** |
| Case-collision paths rejected | ✅ casefold dedupe in `iter_relative_files` |
| Runtime bound / no provider or DB access | ✅ deadline guard; report asserts `database_accessed=false` |

This is an exemplary boundary. **Still required for a future *estimator/model-bundle* exchange** (ASP Model Studio → AISP): a **signed** manifest (not just hashed) with model ID, model version, feature-contract version, training-data authority, temporal-policy authority, algorithm authority, and per-artifact hashes; a staging→validation→activation transaction with rollback and **downgrade prevention**; and an explicit **ban on pickle/joblib** (see P2-2). Do not route model bundles through `ModelArtifactStore.load`.

---

## 6. Forward-looking architecture requirements (payments, entitlements, ledgers)

These features are not implemented; the audit's job is to ensure they can be added safely. Requirements distilled:

**Stripe (§7).** Browser → AISP backend → Stripe Checkout; Stripe → **signed webhook** → backend → server-side subscription ledger → entitlement decision. The **browser must never grant premium**. Verify webhook **signature on the raw body**, enforce timestamp tolerance, **dedupe by event ID**, make handlers **idempotent**, and reconcile subscription lifecycle (cancel/refund/failed-payment/dispute/expiry) into the ledger. Server is authoritative for prices/products/amounts. Use restricted API keys + webhook signing secret + test/live separation. **Never store PAN/CVV/bank credentials** — processor-hosted flows only; if bank linkage is ever needed, store only processor tokens.

**Patreon (§8).** Verify tier **server-side via the Patreon API**; never trust client-supplied tier, email alone, redirect params, or frontend state. Reconcile into the same entitlement ledger.

**Entitlements (§5).** Reuse the proven `ProviderRequestLedger` pattern: an **append-only, server-authoritative** entitlement ledger. Every premium route must gate on a server-side entitlement check **re-read from the DB per request** — never from JWT claims, request body, or frontend hiding. Admin state must not implicitly bypass entitlement checks.

**Virtual bankroll / leaderboard (§17-18).** Append-only ledger; **immutable** wager timestamp + odds snapshot at selection; event-start authority (reject picks after first pitch); **idempotent settlement** (no replay/double-settle); server-authoritative bankroll (no negative/infinite stakes, decimal rounding fixed). Keep **three separate ledgers**: customer subscription, virtual bankroll, and (future) company real-money bankroll — subscribers never own a share of company funds.

**Mobile (§21).** Keep the bearer-token API path; do not hard-migrate to cookie-only. Authorization stays server-side and identical across web/mobile.

---

## 7. Actions taken vs. recommended

**Implemented (with tests):**
- Bounded in-process rate limiter on login/register/admin-jobs; 429 + `Retry-After`; configurable; fails open; non-distributed and documented as such.
- 12 adversarial regressions in [server/tests/test_security_adversarial.py](../server/tests/test_security_adversarial.py): missing/malformed/tampered/`alg=none`/wrong-secret/expired/wrong-type tokens; unauthenticated admin; member→admin denial; **forged role-claim non-escalation**; login rate-limit trip; rate-limit disable path. Autouse limiter-reset fixture added for isolation.
- Verified: new tests pass; existing auth-pipeline, read-boundary, bankroll, and scheduler suites pass; full suite collects with no import errors.

**Recommended (not done — require product decisions or would exceed a safe bounded change):**
1. **Rotate the live Odds API key** in `.env` (it exists on disk / referenced by prior audits). *Not rotated automatically per instructions.*
2. Move rate-limit state to Redis + trusted-proxy config before multi-instance production.
3. Adopt the JWT-storage migration plan (P3-1) at web/mobile split.
4. Add token revocation (`jti` denylist) (P3-2).
5. Harden `production_guards`: reject `*`/non-HTTPS CORS origins; disable/auth-gate OpenAPI docs; auth-gate `/health/sources`.
6. Raise password minimum to ≥12 + breached-password check.
7. When building the estimator exchange, replicate the dataset-exchange hardening and **ban pickle/joblib** at the import boundary.

---

## 8. Method & limitations

Traced actual code paths for authentication, authorization, the route surface, provider/denial-of-wallet, SSRF, SQL, archive/filesystem handling, the exchange consumer, secret handling, and the frontend token lifecycle. Time-boxed per brief. No dynamic exploitation against a live provider, Stripe, or Patreon was performed (prohibited and unnecessary — findings are from code). Payment/bankroll logic was assessed as *design requirements* because it is unimplemented. This review supersedes the prior ecosystem review as the dedicated AISP audit; its conclusions are consistent with, and extend, that earlier evidence.

---

## 9. Authenticated-Beta Hardening Pass (2026-08-18)

A second, bounded pass implemented the smallest safe set of production-boundary controls to move AISP from *safe for private POC* toward *ready for authenticated beta*. The rate limiter and adversarial tests from the audit above were preserved unchanged. No Stripe/Patreon/provider/bank calls; no credential rotation; no commits.

**Controls implemented (all with regression tests):**

| Area | Change | Evidence |
|------|--------|----------|
| **Production CORS** | `production_guards` now rejects wildcard origins and any non-`https://` origin in production (fail closed). Dev localhost unchanged. | [config.py](../server/app/core/config.py) `production_guards` |
| **OpenAPI docs** | `/docs`, `/redoc`, `/openapi.json` disabled when `app_env=="production"`; retained in dev. `create_app(settings)` now injectable for testing. | [main.py](../server/app/main.py) |
| **Health separation** | `/health/sources` (provider quota, source freshness, job history) and `/health` (DB reachability) are now **AdminUser**-gated → 401 unauth / 403 member. Public liveness stays on unauthenticated `/healthz` and `/system/ping`. | [routes.py](../server/app/api/routes.py) |
| **Security headers** | Existing `nosniff`/`X-Frame-Options: DENY`/`Referrer-Policy`/`Permissions-Policy`/API-`CSP` retained; **HSTS** (`max-age=63072000; includeSubDomains`) added **production-only** (never on plaintext dev). | [middleware.py](../server/app/core/middleware.py) |
| **Password policy** | Registration minimum raised **8 → 12** chars (length-only, no composition rules). Login floor left at 8 so pre-existing accounts still authenticate. | [schemas/api.py](../server/app/schemas/api.py) `RegisterRequest` |
| **Token revocation** | Added `users.token_version` (migration `20260818_0016`); every issued token carries a `ver` claim + unpredictable `jti`; `get_current_user` rejects a token whose `ver` ≠ the user's current `token_version`. | [security.py](../server/app/core/security.py), [auth_service.py](../server/app/services/auth_service.py), [dependencies.py](../server/app/api/dependencies.py) |
| **Logout** | `POST /api/v1/auth/logout` (auth required) increments `token_version` → the presented token (and all others for that user) is rejected on next use. Idempotent. | [routes.py](../server/app/api/routes.py) |

**Revocation architecture chosen — `token_version` (design A).** Rationale: smallest change that fits the current single-instance code cleanly — one integer column, one JWT claim, one comparison; no new session table, no unbounded denylist to garbage-collect, and it composes with the existing per-request DB re-read of user status/role (both remain authoritative). It is coarse by design: logout / a future password reset / role change / account disable revoke **all** of a user's outstanding tokens by calling `revoke_user_tokens()`. A finer per-`jti` denylist can be layered later using the `jti` already embedded in every token. Existing tokens issued before this pass carry no `ver` and are therefore rejected (forced re-login) — acceptable given ≤60-minute TTL and pre-beta status.

**Security-sensitive rotation events (§9 of the hardening brief):** `revoke_user_tokens()` is the single hook. It is wired into logout now. Password-change/reset, role-change, and account-disable **workflows do not yet exist**; when built they MUST call `revoke_user_tokens()`. Documented as a forward requirement.

**Rate limiter review:** verified concurrency-safe (lock-guarded), keyed on socket peer (spoofable `X-Forwarded-For` ignored unless `rate_limit_trust_forwarded_for`), consistent 429 + `Retry-After`, health endpoints not rate-limited, per-test isolation intact. No defect found; left unchanged. Classification retained: **`NON_DISTRIBUTED_PRIVATE_BETA_CONTROL`**. `REDIS_OR_SHARED_STORE_REQUIRED_FOR_MULTI_INSTANCE_PRODUCTION`.

**Admin job defense:** re-verified — auth + admin + rate limit + `ProviderRequestLedger` reservation all still enforced; member → 403, unauth → 401 (tested). No real provider call.

**sessionStorage migration — status unchanged (design only, no rewrite):** current bearer-token-in-`sessionStorage` is labeled `ACCEPTABLE_FOR_PRIVATE_POC` and `MIGRATION_REQUIRED_BEFORE_PUBLIC_PAYMENT_CONNECTED_BETA`. Target split (web: in-memory access token + HttpOnly/Secure/SameSite refresh cookie + CSRF; mobile: bearer in OS secure storage; server: identical authorization) is specified in §3/§6 above and the gate doc. The future mobile bearer path is preserved.

**Production secret guards (re-confirmed fail-closed):** placeholder/short `SECRET_KEY`, default/short `ADMIN_PASSWORD`, `DEMO_MODE=true`, non-PostgreSQL `DATABASE_URL`, and now unsafe CORS all raise at settings load in production. Provider secrets remain backend-only (no `VITE_` prefix; not in the client bundle); log redaction and suppressed httpx/httpcore verbosity remain active. **`ROTATE_ODDS_API_KEY_BEFORE_PUBLIC_BETA`** (not rotated automatically).

**Tests added (this pass):** [test_security_hardening.py](../server/tests/test_security_hardening.py) — CORS wildcard/HTTP/HTTPS/dev guards, prod docs disabled / dev docs present, security headers + HSTS-only-in-prod, register 11-reject / 12-accept / legacy-login-unaffected, `/health/sources` 401+403, public liveness open, logout revokes token, logout requires auth, issued-token carries `jti`+`ver`. The forged-admin-role adversarial test was updated to carry a matching `ver` so it still reaches (and is blocked by) the DB role check with 403. Existing `/health/sources` call sites in `test_phase15_backend_contract.py`, `test_gate3b_historical_research_contract.py`, and `test_api_auth_pipeline.py` were updated to authenticate as admin.

**Remaining blockers before PAYMENT-CONNECTED beta:** Stripe + Patreon server-authoritative integration with signed/deduped/idempotent webhooks; server-side entitlement ledger; JWT web→cookie migration + CSRF; breached-password check; distributed rate-limit store.
**Remaining blockers before PUBLIC PRODUCTION:** the above, plus external penetration test, multi-instance abuse controls behind a trusted proxy, CSP for the SPA host, monitoring/alerting on auth + denial-of-wallet events, and a documented incident/rotation runbook.

**Readiness classification after this pass: `READY_FOR_AUTHENTICATED_BETA`** (single-instance / private beta). Payment-connected and public-production readiness are explicitly **not** granted.

### 9a. Frontend alignment (closeout)

The one product-alignment gap left by the backend health gating is now closed, so operational data health is **admin-only end-to-end**:

- **Route guard** `RequireAdmin` ([client/src/components/RequireAdmin.tsx](../client/src/components/RequireAdmin.tsx)) wraps the live `/admin` route ([App.tsx](../client/src/App.tsx)). Non-admins never mount `HealthPage` and therefore never call the protected `/health/sources`; they get a clean "Administrator access required" panel (with a Sign-in link when anonymous) instead of a misleading "no data" state. The backend remains authoritative — the guard is UX, not the security control. The illustrative `/demo/admin` page (static `demoHealth`, no API call) is intentionally left ungated.
- **Navigation gating** ([AppShell.tsx](../client/src/components/AppShell.tsx)): the "Data health" nav item renders only for `role === 'ADMIN'` (or in demo mode for the illustrative page).
- **Logout integration** ([useAuth.tsx](../client/src/hooks/useAuth.tsx), [api.ts](../client/src/services/api.ts)): the client `logout()` now best-effort calls `POST /api/v1/auth/logout` (server-side `token_version` revocation) before clearing local state; an already-expired/revoked token is swallowed so the user is never stranded signed-in. This is *not* a sessionStorage→cookie migration (still deferred).
- **Password field** ([AuthPage.tsx](../client/src/pages/AuthPage.tsx)): registration now requires 12 chars client-side to mirror the backend; login stays at 8 for legacy accounts.
- **Tests:** [App.adminAccess.test.tsx](../client/src/App.adminAccess.test.tsx) (admin reaches page + sees nav; member gets access-controlled state and no `/health/sources` call; anonymous gets sign-in and no nav) and [App.logout.test.tsx](../client/src/App.logout.test.tsx) (logout posts to `/auth/logout` and clears the session). `App.routes.test.tsx` updated for the `/admin` guard.
- **Verification:** frontend focused tests pass; `tsc -b` clean; `vite build` succeeds; backend security gate + migration head (`20260818_0016`) green. (Two `App.routes` cases can time out only under heavy concurrent test load; they pass in isolation — environmental, not a regression.)
