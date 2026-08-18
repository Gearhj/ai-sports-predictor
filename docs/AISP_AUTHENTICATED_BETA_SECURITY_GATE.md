# AISP Authenticated-Beta Security Gate

A concise, repeatable checklist of the security controls that must hold before
each deployment stage, plus the focused test command that verifies them. Keep
this list short and current; it is the pre-beta gate, not a framework.

Run the gate:

```powershell
# from repo root
pwsh scripts/security_gate.ps1
# or directly:
cd server; ../.venv/Scripts/python.exe -m pytest `
  tests/test_security_adversarial.py `
  tests/test_security_hardening.py `
  tests/test_api_auth_pipeline.py `
  -q
```

The script fails fast and returns a non-zero exit code on any security
regression.

## What the gate verifies

- Malformed / expired / wrong-signature / `alg=none` / wrong-type JWT rejected
- Forged `role=admin` claim does not escalate (DB role authoritative)
- Unauthenticated admin route → 401; member → 403
- Registration cannot choose a role (hardcoded MEMBER)
- Login / registration / admin-job rate limiting (429 + `Retry-After`)
- Production CORS fail-closed (no wildcard, HTTPS-only)
- Production OpenAPI docs disabled; dev docs present
- Security headers present; HSTS production-only
- Registration password minimum = 12 (login unaffected for legacy accounts)
- `/health/sources` and `/health` admin-only; `/healthz` + `/system/ping` public
- Operational Health page admin-only **end-to-end** (frontend route guard +
  nav gating; non-admins never call the protected endpoint)
- Client logout calls `POST /auth/logout` (server-side revocation)
- Logout / `token_version` revocation invalidates a still-unexpired token
- Issued tokens carry `jti` + `ver`

## Stage gates

### SAFE_FOR_LOCAL_DEVELOPMENT
Baseline only. No secrets in git; `.env` ignored.

### SAFE_FOR_PRIVATE_POC
Strong auth (Argon2, HS256 pinned, role re-read from DB), provider calls
admin/scheduler-gated with `ProviderRequestLedger`, hardcoded provider hosts,
parameterized SQL, hardened dataset exchange, in-process rate limiting.

### READY_FOR_AUTHENTICATED_BETA  ← current target (single instance)
Everything above, **plus** all controls in "What the gate verifies":
production fails closed (CORS / docs / secrets / demo), operational telemetry
admin-gated, security headers + production HSTS, 12-char registration floor,
server-side token revocation + logout. Single-instance / private; rate limiter
is non-distributed.

**Mandatory before authenticated beta:** rotate the Odds API key
(`ROTATE_ODDS_API_KEY_BEFORE_PUBLIC_BETA`); deploy over HTTPS; set a unique
`SECRET_KEY` (≥32) and `ADMIN_PASSWORD` (≥12); set `APP_ENV=production`,
`DEMO_MODE=false`, PostgreSQL `DATABASE_URL`, and explicit HTTPS `CORS_ORIGINS`.

### READY_FOR_PAYMENT_CONNECTED_BETA  ← NOT met
Requires (not yet implemented): Stripe + Patreon server-authoritative
integration with signed, timestamp-checked, deduplicated, idempotent webhooks;
a server-side entitlement ledger (no browser-granted premium); JWT web→cookie
migration (HttpOnly/Secure/SameSite refresh + CSRF) while preserving the mobile
bearer path; breached-password check (offline / k-anonymity, no external call
at request time); a distributed rate-limit store.

### READY_FOR_PUBLIC_PRODUCTION  ← NOT met
All of the above, plus: external penetration test; multi-instance abuse
controls behind a trusted proxy (`rate_limit_trust_forwarded_for` only then);
a CSP for the SPA host; monitoring/alerting on authentication and
denial-of-wallet events; documented incident-response and credential-rotation
runbooks.

## Standing forward requirements (do not lose these)

- `ROTATE_ODDS_API_KEY_BEFORE_PUBLIC_BETA`
- `REDIS_OR_SHARED_STORE_REQUIRED_FOR_MULTI_INSTANCE_PRODUCTION`
- `BREACHED_PASSWORD_CHECK_REQUIRED_BEFORE_PUBLIC_PRODUCTION`
- `SESSIONSTORAGE_MIGRATION_REQUIRED_BEFORE_PUBLIC_PAYMENT_CONNECTED_BETA`
- Any future password-reset / role-change / account-disable flow MUST call
  `revoke_user_tokens()`.
- Never load imported model bundles via `pickle`/`joblib` (see audit §5, P2-2).
