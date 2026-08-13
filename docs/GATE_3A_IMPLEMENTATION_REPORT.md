# Gate 3A Implementation Report — Historical MLB Evidence Backfill

## Verdict

Gate 3A's provider-free A0 foundation is implemented and verified. Historical
acquisition did not start. The 2025 pilot, 2023–2025 expansion, Statcast trial,
weather history, historical markets, materialized production dataset, model
training, and promotion remain unavailable or blocked.

The controlling result is:

`HISTORICAL_ROWS = 0 / INSUFFICIENT_DATA / NO_TRAINING / NO_PROMOTION`

This is intentional. MLB source terms still require owner/legal review, the
current MLB responses do not supply the authoritative completion timestamp
required by canonical outcome persistence, and the public backfill command is
explicitly raw-archive-only. No minimum sample, cutoff, or source label was
relaxed to manufacture progress.

## Implemented

### Historical warehouse and governance

Alembic revision `20260814_0005` adds six append-only tables:

- `historical_source_versions`
- `history_backfill_checkpoints`
- `game_final_outcomes`
- `team_game_history_observations`
- `player_game_history_observations`
- `historical_feature_materializations`

The schema separates source mode, reconstruction semantics, historical event
time, actual retrieval time, label availability, source publication where
proven, evidence class, quality tier, policy/version identity, raw/canonical
checksums, checkpoint lineage, and correction revisions. Source versions are
training/evaluation-ineligible by default; only an explicit approved governance
record with reviewer and review time can enable them.

### Safe acquisition infrastructure

The historical MLB adapter exposes schedule, final-feed, and box-score
normalization behind an injected client. The request executor enforces a hard
physical-attempt cap, single-source pacing, at most three attempts, bounded
backoff, `Retry-After`, and a rejection circuit breaker. Authentication,
entitlement, schema, identity, and other non-retryable failures stop immediately.

Raw historical writes are atomic gzip files under the Git-ignored
`data/raw/historical/<source>/YYYY/MM/` tree. Semantic request fingerprints make
replay idempotent and changed bytes conflict rather than overwrite. Payload and
compressed-file SHA-256 values are distinct. JSONL and SQLAlchemy checkpoint
repositories support append-only terminal records and failed-then-successful
retry history.

The public `backfill-mlb-history` command is deliberately limited to raw
schedule archival. A completed raw chunk returns
`ARCHIVED_CANONICAL_PENDING`, with zero canonical records. It does not call
Odds, Statcast, or weather. Non-dry execution has no approval bypass and is
currently blocked before adapter construction.

### Canonical facts and point-in-time reconstruction

The provider-free canonical service resolves exact MLB game, team, player, and
venue IDs; preserves doubleheader identities and trade participation; appends
final corrections; and commits a chunk atomically. It refuses DEMO or mixed
provenance, non-final truth, unknown completion time, forward-only evidence,
unknown IDs, and conflicting schedule identity. Partial box-score evidence is
Tier B rather than promoted to Tier A.

An official historical target uses:

- `final_at`: authoritative historical completion time;
- `retrieved_at`: actual ASP acquisition time; and
- `label_available_at`: the later of acquisition and any proven publication
  time, never a backdated historical final time.

Prior team/player facts may use the authoritative completion time as their
versioned reconstruction eligibility while retaining current retrieval time.
That distinction lets a prior completed game contribute to a later target
without claiming ASP possessed the label years ago.

Pure feature helpers implement exact T−6h cutoffs, chronological Elo, strict
rolling windows, starter/bullpen workloads, rest/travel, Bayesian talent and
platoon shrinkage, quality scoring, and canonical snapshots. Persisted FEATURE
references must resolve to real immutable team/player history rows and match
their source, record, game, timestamps, class, tier, final/checkpoint lineage,
and checksum. Target/current/future, arbitrary, aliased, or tampered evidence
fails closed.

The separate historical dataset builder consumes materializations without
creating or querying Forecast rows. It defaults to Tier A, forbids markets and
CLV, and requires explicit authorization for Tier B or mixed A/B populations.
The operational training command remains connected only to the prospective
LIVE builder, so this Gate made no training or model change.

### Provider-free reporting

`report-mlb-history` reports per-season games, all requested feature-family
coverage, checkpoint/accounting basis, exclusions, unresolved mappings,
deterministic official-final validation samples, feature-drift availability,
database/raw/Parquet/runtime storage, and zero provider calls made by report
execution. Empty denominators return `UNAVAILABLE`, not a fabricated zero-rate
claim.

## Controlled evidence on 2026-08-13

| Check | Result |
| --- | --- |
| Seven-day 2025 dry run | `DRY_RUN`; 7 chunks; 14 planned maximum requests; adapter not constructed |
| Dry-run physical attempts / successes / retries | `0 / 0 / 0` |
| Dry-run Odds / Statcast / weather requests | `0 / 0 / 0` |
| One-day non-dry attempt | `BLOCKED: LEGAL_REVIEW_REQUIRED` before adapter construction |
| Non-dry physical attempts | `0` |
| New Gate 3A historical archive files | `0` (`data/raw/historical` absent) |
| 2025 normalized historical games / official finals | `0 / 0` |
| 2025 Tier A/B materializations / validation games | `0 / 0` |
| Historical Parquet artifacts | `0 files / 0 bytes` |
| Existing raw archive, all earlier sources | `74 files / 3,751,772 bytes` |
| PostgreSQL size after migration | `15,268,887 bytes` |
| Ignored runtime artifacts | `20 files / 1,539,007 bytes` |
| Historical Odds requests / credits consumed | `0 / 0` |

Every 2025 coverage family correctly reports `UNAVAILABLE`: finals, starters,
actual lineups, roster, team/player history, bullpen, Statcast, park, weather,
rest/travel, platoon, defense, baserunning, opponent adjustment, and Tier A/B
eligibility. No ten-game sample or multi-season drift result exists.

## Verification

- Gate 3A provider-disabled suite: **87 passed**.
- Full provider-disabled backend suite: **278 passed** in 54 seconds.
- Python compilation: passed.
- Fresh SQLite upgrade through `20260814_0005`: passed.
- SQLite `alembic check`: no new upgrade operations.
- SQLite downgrade to `20260813_0004`, re-upgrade, and second check: passed.
- Local PostgreSQL upgraded from `20260813_0004` to `20260814_0005`; current
  revision is head and `alembic check` reports no drift.
- `git diff --check`: passed apart from line-ending conversion notices.
- External provider data/API calls during implementation and verification:
  **0**. Public terms/documentation pages were reviewed without consuming
  provider quota.

The first full pytest invocation completed its tests but Windows denied pytest's
cleanup of a mode-0700 temporary directory. The repeat used the established
0777 temp-directory wrapper and exited zero; this was a local ACL harness issue,
not an application failure.

## Source and entitlement decisions

| Source | Decision |
| --- | --- |
| MLB Stats API | `LEGAL_REVIEW_REQUIRED`; no historical request authorized |
| Baseball Savant / Statcast | `LEGAL_REVIEW_REQUIRED`; no trial or bulk request |
| Open-Meteo history | `HISTORICAL_WEATHER = UNAVAILABLE_ON_CURRENT_PLAN` |
| The Odds API history | `HISTORICAL_MARKET_DATA = UNAVAILABLE_ON_CURRENT_PLAN` |
| Historical closing lines / CLV | `UNAVAILABLE` |

The detailed terms, caching, attribution, commercial-use, and replacement-source
review is in `DATA_SOURCES.md`. This report records engineering controls, not a
legal conclusion.

## Remaining blockers and exact next stage

1. Record an approved MLB terms/service decision with reviewer, review time,
   allowed purpose, retention, attribution, redistribution, rate cap, and next
   review. The code intentionally has no CLI override today.
2. Select and document an authoritative final-completion timestamp. Current
   historical MLB normalizers preserve it as unknown; canonical outcome
   persistence therefore rejects rather than using first pitch, acquisition
   time, or end-of-day.
3. Connect the raw archive stage to final/box-score acquisition, SQL checkpoint
   accounting, canonical persistence, evidence-derived snapshot construction,
   materialization, and the historical dataset builder. The components exist,
   but the production command does not claim that end-to-end path yet.
4. Resolve the one-to-one materialization/feature-snapshot schema policy for a
   later corrected final. Current behavior marks the old label stale and rejects
   rebinding; it never silently changes an immutable row.
5. After blockers 1–4, run one approved 2025 date, stop, reconcile every game
   and request, then expand to at most seven dates. Only after that validation
   may 2025 or 2023–2025 be considered.
6. Keep Gate 3 at DEVELOPMENT. Do not train, calibrate, claim superiority, or
   promote until the governed multi-season dataset and later locked evaluation
   gates actually exist.

Gate 3A A0 is release-ready as fail-closed infrastructure. Gate 3A historical
data acquisition is not complete, and this report does not present it as such.
