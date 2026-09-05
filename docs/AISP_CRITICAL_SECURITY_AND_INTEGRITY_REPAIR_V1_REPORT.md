# AISP critical security and integrity repair — V1 report

Bounded repair of four finding families from the independent AISP audit:
**SEC-DB-001** (committed shared database defaults), **INT-001** (bulk SQL bypasses
authoritative-history immutability), **INT-008** (conflicting per-game team records
abort recurring finalization), and **INT-007** (absent sportsbook evidence
published as a reviewed 0%). No unrelated scope was expanded. All database mutation
was done on a disposable PostgreSQL clone; the operational database was opened once,
read-only, only to capture protected counts.

## Summary of changes

| Repair | Mechanism |
| --- | --- |
| SEC-DB-001 | Removed the shared credential from every tracked runtime/config/doc path; database configuration now fails closed outside tests; scanner gate added. A separate operational credential found in five docs was also redacted. |
| INT-001 | PostgreSQL BEFORE UPDATE OR DELETE triggers on the six authority tables, mirroring the ORM contract, installed by migration `20260905_0027` (disposable PostgreSQL only). |
| INT-008 | `CanonicalNormalizer.ingest_schedule` buffers team-record candidates and resolves one canonical record per natural key deterministically, with observable conflict quarantine and idempotent retries. |
| INT-007 | The market reducers keep a missing sportsbook quote `null` (never 0.0) and require real evidence before returning a probability; the Market Pulse panel labels it unavailable. |

## Repair A — SEC-DB-001: eliminate shared database defaults

The shared default `postgresql+psycopg://asp:<password>@localhost:5433/asp` was
present in eight tracked paths (`server/app/core/config.py:32`,
`server/app/db/session.py:15`, `.env.example:2`, `docker-compose.yml:9,27`,
`server/alembic.ini:4`, and three prior audit documents).

- `Settings.database_url` now defaults to `""`; a `require_explicit_database_url`
  validator raises whenever `app_env != "test"` and the URL is empty, so
  development and production **fail closed**. Tests still inject an explicit
  disposable URL (`sqlite://` or a disposable PostgreSQL URL).
- The dead `DEFAULT_DATABASE_URL` constant is removed; `get_engine` raises a clear
  error if no URL resolves.
- `alembic.ini` ships a blank `sqlalchemy.url`; `env.py` already supplies it from
  `DATABASE_URL` / governed settings.
- `.env.example` ships a non-secret placeholder with setup instructions.
- `docker-compose.yml` builds `POSTGRES_PASSWORD` and `DATABASE_URL` from
  operator-supplied environment with fail-closed `${VAR:?...}` interpolation; no
  literal password remains, and the healthcheck uses the same variables.
- The three prior audit docs are redacted.

**Additional discovery.** A broader scan found a *different* credential — an
operational connection string using role `asp_user` — embedded in five tracked
integration/readiness reports. This is redacted as well. Redaction is not
rotation: the operational credential and role are unchanged. **The operational
`asp_user` password was disclosed in tracked docs and should be rotated by the
operator, with a review of database access logs.** History still contains all of
these values; see the sanitization plan.

A regression scanner test (`test_no_committed_credential_bearing_database_url`)
fails if any credential-bearing database URL is committed under `server/app`,
`server/alembic`, the config files, or `docs`. Startup tests cover missing,
explicit-test, and injected configuration. The production frontend bundle contains
no database credential (scanned after `vite build`).

## Repair B — INT-001: database-enforced immutability

The ORM `before_flush` guard in `app/audit/immutability.py` protects instance
mutations but never sees Core/bulk `session.execute(update/delete(...))` or raw
SQL, and the runtime role holds UPDATE/DELETE on every authority table. A
disposable-database test independently reproduced the finding: a same-natural-key
raw INSERT collides, and (per the audit) a Core UPDATE rewrote a published
probability and a Core DELETE removed a losing settlement.

`app/audit/immutability_sql.py` emits `BEFORE UPDATE OR DELETE` row triggers for
`forecasts`, `model_versions`, `shadow_experiments`,
`shadow_experiment_decisions`, `official_asp_positions`, and `position_results`.
The rules are a faithful mirror of the ORM contract, so no legitimate write path
that works today is broken (see `AISP_DATABASE_IMMUTABILITY_CONTRACT.csv`):

- published forecasts cannot be modified or deleted; `forecast_track` is immutable;
- model identity/governance columns are immutable (the `metrics` recalculation
  carve-out is preserved);
- experiment rows and settlements are append-only;
- official positions are immutable once settled/void; evidence columns (stake,
  odds, policy versions, bankroll-before) never change; open→settled is the only
  transition.

Trigger functions use a fixed `search_path` and schema-qualified names, compare
the persisted (uppercase) enum tokens, and compare JSON columns as text (the
`json` type has no equality operator). They fail closed with SQLSTATE
`restrict_violation`.

Migration `20260905_0027` validates the schema (aborting with a clear report on
drift, and refusing to install over duplicate settlements) then installs the
triggers; `downgrade` drops them. It is PostgreSQL-only and a no-op on SQLite.

**Proven in a disposable PostgreSQL 16 cluster (never the operational DB):**
`alembic upgrade head` installs all six triggers; downgrade→upgrade round-trips
cleanly. 16 tests assert that raw and Core UPDATE/DELETE are rejected for every
protected table, that legitimate append/terminal-transition paths still work, that
a rejected mutation inside a transaction leaves no partial evidence, and that a
least-privilege runtime role granted UPDATE/DELETE still cannot rewrite evidence,
`DISABLE TRIGGER`, or `SET session_replication_role = replica`.

## Repair C — INT-008: idempotent finalization

The dedup lookup in `_append_record_rating` keyed on `payload_checksum`, but
`uq_team_rating_observation` excludes it. A doubleheader team with a *differing*
record at the same batch timestamp bypassed the lookup and collided at INSERT,
raising IntegrityError and skipping freeze/settlement for the whole date. The
collision was independently reproduced on the disposable cluster.

`ingest_schedule` now buffers team-record rating candidates across the batch and
`_resolve_and_persist_record_ratings` writes exactly one canonical record per
natural key:

- the idempotency lookup matches the database natural key (no checksum), so
  identical repeats and retries collapse to one row;
- a differing record for the same natural key selects one canonical record
  deterministically — most games, then higher rating, then smallest checksum —
  which is independent of game processing order;
- conflicts are quarantined in `normalizer.rating_conflicts` (observable) and
  logged, never overwriting append-only evidence or aborting the batch.

Tests on SQLite and PostgreSQL prove one lawful rating per natural key, no partial
schedule loss, observable quarantine, order-independence (reversed game order),
idempotent identical retries, retained persisted evidence on a conflicting retry,
and that a genuinely newer point-in-time (distinct `available_as_of`) still
appends. No provider call is required by the repair.

## Repair D — INT-007: missing sportsbook evidence is not 0%

`ReadService._market_context` returned `home_probability: 0.0` when no book
reported, and `PredictionMarketReadService._sportsbook_home_probability` ignored
`has_moneyline` and turned that sentinel into a reviewed 0% quote that entered the
reviewed range and the ASP-market gap.

- `_market_context` now returns `home_probability: None` when there is no reviewed
  evidence; every in-service consumer already gates on `has_moneyline`.
- `_sportsbook_home_probability` requires `has_moneyline` and a non-zero
  `book_count` before returning a probability, so a missing quote is `None` and
  cannot contribute to range, gap, dispersion, or ranking.
- The Market Pulse panel renders the missing value as an em dash with an
  accessible "no reviewed sportsbook quote" label; the coverage matrix already
  reads "Not quoted". A genuine two-sided quote is preserved unchanged.

Backend tests (real ORM) confirm the reducers stay `null`; frontend component
tests confirm no 0% mark, a "Not quoted" matrix cell, and that a real quote is
still plotted. Existing Market Pulse suites remain green.

## Verification

- **Disposable PostgreSQL 16** cluster on `127.0.0.1:55433`; disposable databases
  created and dropped per run. Migration `20260905_0027` upgrade/downgrade proven
  there; **not applied to the operational database**.
- **Backend suite** run against SQLite plus the disposable PostgreSQL clone for the
  gated tests. All four repair suites pass. Every other non-pass reproduces in the
  independent audit's baseline at the same HEAD (`8826f4d`) and is classified
  PRE_EXISTING_REPRODUCIBLE / ENVIRONMENT_DEPENDENT / MISSING_LOCAL_TEST_DATA /
  GENUINE_UNRESOLVED_DEFECT there; the migration-head assertions that now read
  `20260905_0027` were already red at base (they hardcode the stale revision
  `20260815_0015`), and `test_gate2_migration` fails on the pre-existing SQLite
  "ALTER of constraints" limitation in migration `0021`, unrelated to `0027`. No
  application regression was introduced. See `AISP_CRITICAL_REPAIR_TEST_RECEIPT.json`.
- **Frontend**: the INT-007 component tests and the existing Market Pulse suites
  pass; the production Vite bundle builds and contains no credential. `tsc -b`
  reports one pre-existing error (`MarketPulsePage.reconciliation.test.tsx:25`,
  TS2537) in a test file this repair did not touch — reproduced at base and out of
  the four-family scope.
- **Browser acceptance** on an isolated demo stack (`vite dev`, no backend, no auth,
  no writes, no provider calls) at 1920×1080, 1024×768 and 390×844: Market Pulse,
  Market Board and Experiment render; no page-level horizontal overflow after load
  (the transient resize overflow settles, matching the audit note); no credential
  in the DOM; no fabricated 0% sportsbook text; Market Board and Market Pulse
  reconcile ("2 of 2 — reconciles"); the $1,000 experiment shows the honest ledger.
  The only console errors are the app shell's backend fetches failing because no
  backend runs in demo-only mode — not from the repair. Limitation: the specific
  missing-sportsbook "unavailable" rendering is certified by the component and
  backend integration tests rather than a live seeded no-odds game, because the
  demo dataset carries sportsbook quotes.
- **Protected evidence**: the operational database was opened once, read-only
  (`default_transaction_read_only=on`). The authoritative identity/ledger tables
  (`users`, `model_versions`, `shadow_experiments`, `official_asp_positions`,
  `position_results`) are unchanged in count from the independent audit's
  after-snapshot; the growth elsewhere is consistent with the live scheduler, which
  was left running. See `AISP_CRITICAL_REPAIR_PROTECTED_COUNTS.json`.

## Final report (items 1–46)

1. **Starting repository / branch / HEAD**: `C:\Users\james\ai-sports-predictor`,
   `codex/multisport-platform-architecture`, `8826f4d475cfa2fa644996b9da26f147f4acb01e` (verified).
2. **Repair worktree / branch / final HEAD**:
   `C:\Users\james\worktrees\aisp-critical-integrity-repair-v1`,
   `claude/aisp-critical-integrity-repair-v1`, final HEAD recorded in the commit
   receipt appended at publication (the docs commit).
3. **Main HEAD**: `92f92dde5d5ec6b8d1d68e9d69284368dbd0525b` (unchanged).
4. **Git status before/after**: primary clean except the two known untracked files;
   after, the primary checkout is untouched and the repair branch carries only the
   commits below. No primary modification, merge, or push.
5. **Preserved untracked files**: `docs/MONTE_CARLO_SIMULATION_THEATER.md`
   (`454e4d4d38296db56b6bf3bb0e72ed6fe3f052f700d8dc32a7c5e69efb499c17`),
   `install-supervision.ps1`
   (`7768030dfaf43783c481eeb226bbe01b694a4b8d1dc03c5aa4673c4798ef2c48`) — unchanged.
6. **Files changed**: 23 (18 source/config/doc edits + 5 new files); see the commit
   list. Application changes and the migration are only on the repair branch.
7. **Commits created**: five code/test commits plus one documentation commit
   (SHAs in the commit receipt): `fix(config)`, `feat(integrity)`, `fix(scheduler)`,
   `fix(markets)`, `test`, `docs`.
8. **Pushes performed**: none.
9. **Audit findings addressed**: SEC-DB-001, INT-001, INT-008, INT-007.
10. **Shared-default occurrences before**: eight tracked paths for the `asp` shared
    default (nine lines), plus five docs carrying the operational `asp_user` string.
11. **Shared-default occurrences after**: zero credential-bearing database URLs in
    the tracked tree (enforced by the scanner test).
12. **Operational credential changed**: no.
13. **Operational role changed**: no.
14. **History rewritten**: no (planned only; `AISP_DATABASE_CREDENTIAL_HISTORY_SANITIZATION_PLAN.md`).
15. **Current-tree secret-scan result**: clean — no credential-bearing database URL
    and no `REDACTED-CREDENTIAL` token in the tracked tree; the production bundle is clean.
16. **History-sanitization-plan location**: `docs/AISP_DATABASE_CREDENTIAL_HISTORY_SANITIZATION_PLAN.md`.
17. **Immutability tables covered**: `forecasts`, `model_versions`,
    `shadow_experiments`, `shadow_experiment_decisions`, `official_asp_positions`,
    `position_results` (contract in `AISP_DATABASE_IMMUTABILITY_CONTRACT.csv`).
18. **Direct SQL mutation attempts and results**: raw and Core UPDATE/DELETE on each
    protected table → rejected (`restrict_violation`); delete-loss-settlement →
    rejected; change stake/pnl/settlement → rejected; runtime role cannot bypass or
    disable the guards. All asserted in `test_int001_db_immutability.py`.
19. **Legitimate write-path tests**: draft→published forecast; open→settled position
    with a single settlement; model `metrics` recalculation; transaction rollback
    leaves no partial evidence — all pass.
20. **Migration identifier**: `20260905_0027`.
21. **Migration applied to live database**: no.
22. **Disposable upgrade/downgrade result**: PASS (upgrade installs six triggers;
    downgrade→upgrade round-trips; six triggers present at head).
23. **Finalization failure reproduction**: IntegrityError on
    `uq_team_rating_observation` reproduced on the disposable cluster for a
    same-natural-key/different-checksum pair.
24. **Finalization result after repair**: idempotent; one canonical rating per
    natural key; conflicts quarantined and observable; no IntegrityError; the batch
    and unaffected games persist.
25. **Repeated-run idempotency**: identical retry creates no duplicate rows;
    conflicting retry keeps the persisted evidence.
26. **Duplicate positions created**: none (repair does not create positions; the
    finalization path remains idempotent).
27. **Duplicate settlements created**: none (`position_results` is append-only, one
    per position).
28. **Missing sportsbook value before**: `0.0`, published as a reviewed 0% quote.
29. **Missing sportsbook value after**: `null` / unavailable; excluded from reviewed
    range, gap, dispersion, and ranking.
30. **Market Board / Market Pulse reconciliation**: both present the missing quote
    honestly (Board omits it; Pulse shows "Not quoted" / em dash); verified in the
    browser demo ("2 of 2 — reconciles").
31. **Provider calls**: 0.
32. **Operational forecasts created**: 0.
33. **Operational decisions created**: 0.
34. **Operational positions created**: 0.
35. **Operational settlements created**: 0.
36. **Protected live counts and digests**: `AISP_CRITICAL_REPAIR_PROTECTED_COUNTS.json`;
    identity/ledger tables unchanged from the audit baseline.
37. **Backend tests**: all four repair suites pass; every other non-pass is
    pre-existing at base (see receipt). Exact counts in
    `AISP_CRITICAL_REPAIR_TEST_RECEIPT.json`.
38. **Frontend tests**: INT-007 component tests and existing Market Pulse suites
    pass.
39. **TypeScript/build**: Vite production bundle builds (credential-free); `tsc -b`
    has one pre-existing TS2537 error in an untouched test file.
40. **Dependency and secret scans**: current-tree secret scan clean; dependency-audit
    posture unchanged from the audit (no new dependencies added).
41. **Browser acceptance**: pass on the isolated demo stack at all three viewports
    (rendering, reconciliation, no overflow, no credential, honest ledger), with the
    stated limitation.
42. **Known remaining P1 findings**: OPS-DR-001 (recovery), plus the security/quality
    P1s outside this batch (per the audit repair plan).
43. **Known remaining P2 findings**: INT-002/003/004/005/006, SEC-01..04, WF-001..005,
    CAP-001, etc. (per the audit repair plan) — not in this batch.
44. **Exact integration instructions**: review this branch; run the backend suite
    against a disposable PostgreSQL clone with `AISP_TEST_PG_URL` set; apply
    migration `20260905_0027` to each installation's database during a maintenance
    window (it installs triggers only; validate first); provide each installation an
    explicit `DATABASE_URL`; then rotate the disclosed operational credential and
    execute the history-sanitization plan as a separate authorized activity. Do not
    fast-forward this branch onto `main` without those operational steps.
45. **Exact recommended next AISP repair task**: OPS-DR-001 — recurring encrypted
    off-machine recovery of relational data and referenced artifacts with an
    isolated restore drill (the top remaining blocker in the audit repair plan).
46. **Final verdict**: `AISP_CRITICAL_SECURITY_AND_INTEGRITY_REPAIR_V1_READY`.
