# Gate 3A Implementation Plan — Historical MLB Evidence Warehouse

## Decision and invariant

Gate 3A builds governed historical evidence. It does not train or promote a
model. The Gate 3 result remains **INSUFFICIENT_DATA / NO_PROMOTION** until a
later Gate 3B run satisfies the unchanged sample, temporal, leakage,
calibration, artifact, and human-promotion controls.

This plan was written before historical acquisition on 2026-08-13. The audit
was read-only, stayed inside `C:\Users\james\ai-sports-predictor`, did not print
secrets, and made no provider-data requests. Reviewing public provider terms
and documentation did not consume provider quota.

## Pre-backfill audit

| Item | Observed state |
| --- | --- |
| Git root | `C:/Users/james/ai-sports-predictor` |
| Branch / HEAD | `main` / `4ca843d277138f558986010376414aae0785f27f` |
| Worktree | Clean before Gate 3A implementation |
| Database | PostgreSQL, 14,646,295 bytes, Alembic `20260813_0004` |
| Free disk | 287,909,740,544 bytes free of 1,023,529,164,800 bytes |
| Raw archive | 74 files / 3,751,772 bytes: MLB 64, weather 8, Odds 1, Statcast 0 |
| Runtime artifacts | 34 ignored files / 1,543,938 bytes |
| Historical event rows | 0 team-game, 0 player-game, 0 pitch-event rows |
| Current governed labels | 0 final games; both Gate 3 manifests contain 0 rows |
| Current evidence | 9 LIVE scheduled games, 18 snapshots, 18 forecasts, 472 odds rows |
| Provider switches | Odds polling, Statcast, schedulers, and official positions off; Open-Meteo on |
| Odds credential | Configured; value not inspected or disclosed |

The current schema has useful identity and point-in-time primitives, but the
existing historical-stat tables do not carry sufficient provenance and the
Gate 3 builder accepts only prospectively observed LIVE forecasts. Historical
rows therefore require an explicit, separately governed path; they cannot be
made eligible by backdating `available_as_of` or fabricating published
forecasts.

## Target population and stages

The intended core population is MLB regular-season games in 2023, 2024, and
2025. The 2025 regular season is the first pilot. Completed 2026 games may be
added through an explicit cutoff only after the core policy is validated and
without replacing prospective Gate 2 evidence. A 2021–2022 stretch is a later
decision, not part of the first acquisition.

The stages are deliberately gated:

1. **A0 — contracts and dry run:** schema, provenance, checkpointing, CLI,
   deterministic fixtures, and leakage tests; zero provider calls.
2. **A1 — 2025 core pilot:** one MLB date, then at most seven dates, only after
   the MLB terms/service review is approved. Stop and validate.
3. **A2 — 2025 regular season:** schedules, final outcomes, box scores, player
   participation, starters, and event-derived workloads. Stop and validate.
4. **B — 2023–2025 core:** expand only after 2025 identity, outcome,
   idempotency, leakage, storage, and query checks pass.
5. **C — Statcast trial:** one historical day or small range after a separate
   terms review; expand only after schema/mapping/rate/archive validation.
6. **D — optional context:** historical weather and other secondary sources
   only when plan entitlement, attribution, and temporal meaning are approved.
7. **E — 2021–2022 stretch:** only after a written value/storage/source-stability
   decision.

Expected regular-season scale is approximately 2,430 played games per full
season, or roughly 7,290 games for 2023–2025 before legitimate exclusions.
Counts are validation guides, never targets to be forced by inventing records.

## Source classification vocabulary

Every feature-family record carries one of these evidence classifications:

- **RECONSTRUCTABLE_POINT_IN_TIME:** a structural fact or dated event can be
  shown to have occurred and become eligible before the game cutoff.
- **PARTIALLY_RECONSTRUCTABLE:** useful historical evidence exists, but its
  exact pregame publication/knowledge time is not demonstrable.
- **FORWARD_ONLY:** retrospective reconstruction cannot establish the feature;
  it remains missing until ASP captures it prospectively.

Operational source mode and research provenance are separate dimensions:

- `source_mode=LIVE` means real, non-synthetic upstream evidence. It does not
  by itself claim that ASP captured the record prospectively.
- `source_mode=DEMO` is illustrative and never trainable.
- `research_provenance=HISTORICAL_RECONSTRUCTION` plus
  `temporal_semantics=RETROSPECTIVE_RECONSTRUCTION` identifies governed
  retrospective evidence.
- `BACKTEST` identifies model output, never provider evidence or public picks.

The implementation will persist the research provenance and evidence class
without weakening LIVE/DEMO isolation. Historical reconstruction cannot enter
forward public performance, official positions, or the public bankroll.

## Source and terms matrix

| Source | Intended historical use | Technical state | Evidence class | Terms/status | Gate 3A action |
| --- | --- | --- | --- | --- | --- |
| MLB Stats API | Schedule identity, finals, box scores, actual participants/starters, dated transactions | Current adapter supports one schedule date plus several current/context endpoints; historical box-score/event adapters are needed | Finals and completed event facts are reconstructable as historical events; publication-timing claims are not | `LEGAL_REVIEW_REQUIRED`. MLB's current Terms of Use prohibit automated scripts against MLB Digital Properties and prohibit unreasonable load; applicability to this interface and archival/derived use needs owner/legal review | Implement capped/resumable client and fixtures, but make no bulk acquisition until review is recorded |
| Baseball Savant / Statcast | Pitch and batted-ball events for rolling pregame aggregates | Current adapter is season-leaderboard only; date/event backfill is not implemented | Prior completed events can be reconstructable; later-fetched full-season leaderboards are retrospective and cannot be PIT features | `LEGAL_REVIEW_REQUIRED`. Official CSV documentation confirms fields/access, but automated archival, retention, redistribution, and commercial-use rights are not established | Implement a disabled, controlled-trial seam; no multi-season call until review |
| Open-Meteo | Historical weather/reanalysis or archived forecast state | Current adapter uses only the live forecast endpoint | Reanalysis is partial context, not proof of a forecast known pregame; archived issued forecasts may support stricter evidence where available | Data is CC BY 4.0 with attribution, but current pricing says historical APIs require a Professional plan and the free API is non-commercial | `HISTORICAL_WEATHER = UNAVAILABLE_ON_CURRENT_PLAN`; preserve missingness and make zero calls |
| The Odds API | Historical market benchmark | Current adapter implements only present `/sports/baseball_mlb/odds` | Forward ASP captures are valid; no local historical series exists | Official docs state historical endpoints are paid-plan only and cost 10 credits per region/market | `HISTORICAL_MARKET_DATA = UNAVAILABLE_ON_CURRENT_PLAN`; zero calls and zero credits |
| ASP prospective archive | Exact future lifecycle, lineup, starter, bullpen, weather, market, forecast, and outcome state | Existing Gate 2 lifecycle/raw archive | Forward Gold Standard | Internal controlled evidence; upstream source restrictions still apply | Continue separately; never relabel reconstruction as prospective capture |

No legal conclusion is made here. A provider review record must identify the
reviewed URL/version/date, service tier, purpose, attribution, caching,
retention, redistribution/derived-data terms, commercial-use status, rate/quota
limits, reviewer, approval status, replacement plan, and next review date.
Unknown is not permission.

## Historical knowledge-time policy

For scheduled first pitch `T_game`, the strict historical cutoff is
`T_cutoff = T_game - 6 hours` unless a later policy version is explicitly
approved. Six hours is conservative, deterministic, works across time zones,
and avoids pretending ASP observed a T−10-minute state years later.

An event-derived feature is eligible only when all of the following hold:

1. its source event identity is resolved;
2. its event/end timestamp is strictly before `T_cutoff`;
3. its conservative `eligible_at` is at or before `T_cutoff`;
4. its source/evidence class is allowed by the selected research tier;
5. it does not reference the target game's post-start events or outcomes; and
6. its raw/canonical checksum and reconstruction-policy version are recorded.

Fetch time remains the actual acquisition timestamp. Historical source events
are never made to look prospectively observed by assigning an old
`available_as_of`. Final outcomes are append-only
`HISTORICAL_RECONSTRUCTION` targets whose acquisition timestamp is current and
whose event/final timestamp is historical. A target label becomes available to
ASP no earlier than its actual acquisition time (or a later proven publication
time); this is distinct from the historical final time used to order prior
event-derived features. Schedule corrections use the latest eligible schedule
observation. Suspended, canceled, exhibition, Spring
Training, All-Star, and ambiguous-settlement games are excluded or separately
classified. Postseason is excluded from the initial population.

Game 1 of a doubleheader may affect Game 2 only when Game 1's legitimate final
and player workload became eligible before Game 2's cutoff. Dates alone are not
used for this decision.

## Reconstructable feature matrix

| Family | Initial class | Temporal derivation |
| --- | --- | --- |
| Game identity / venue / regular-season type | Reconstructable | Versioned schedule facts effective before cutoff |
| Final outcome / innings | Reconstructable target only | Authoritative final event; never a pregame feature |
| Team game history | Reconstructable | Completed prior games with `eligible_at <= cutoff`; current game excluded |
| Player game history | Reconstructable | Prior completed participation only; canonical player/team IDs |
| Season-to-date / 14d / 30d | Reconstructable | Event-level aggregation ending strictly before cutoff |
| Actual starting pitcher | Partial by default | Historical participation proves who started, not announcement time; separate starter-known variant |
| Actual lineup | Partial / timing risk | Preserve as `HISTORICAL_ACTUAL_LINEUP`; Tier B upper-bound only |
| Roster / dated transactions | Partial until endpoint evidence is validated | Effective and eligible intervals; no inference from later roster state |
| Injury / day-to-day availability | Forward-only except dated IL transactions | Never infer exact injury knowledge from final roster state |
| Bullpen workload | Reconstructable proxy | Prior relief appearances/pitches only; label `WORKLOAD_DERIVED_AVAILABILITY_PROXY` |
| Starter workload | Reconstructable | Prior appearances, pitches and innings only |
| Statcast rolling skill | Reconstructable if event feed is approved | Pitch/event records strictly before cutoff; no full-season retrospective aggregate |
| Bayesian talent / platoon | Reconstructable | Prior seasons plus current-season and 14/30d evidence truncated at cutoff; shrink small samples |
| Team Elo | Reconstructable | Store pregame rating, then update only after an eligible completed game |
| Park factor | Partial initially | Prior-season or entering-season version only; no future-season games |
| Weather | Partial / unavailable on current plan | Reanalysis is labeled as reconstructed conditions, not issued forecast |
| Rest / travel | Reconstructable | Prior eligible completed game, venue coordinates, doubleheader/extra innings |
| Defense / baserunning | Partial until an approved event definition is implemented | Missing rather than fabricated |
| Opponent adjustment | Partial initially | Chronological-only iterations; no future rounds |
| Market / closing line / CLV | Forward-only locally | `UNAVAILABLE` until legitimately captured historical timestamps exist |

## Warehouse and materialization design

PostgreSQL owns canonical identity, append-only outcomes, event summaries,
source policies, checkpoints, reconstruction manifests, and immutable snapshot
lineage. Compressed raw evidence lives under ignored
`data/raw/historical/<source>/YYYY/MM/`. Large pitch/event analytical records
and rolling aggregates use Parquet under ignored, versioned partitions in
`data/curated/historical/` and `data/features/historical/`.

The materialization flow is:

`raw evidence → canonical game/player events → versioned rolling aggregates → historical pregame materialization → Gate 3 training rows`

Partitions are by source/season/month or feature-family/season/month. The
pipeline avoids both one monolithic file and per-row files. Each artifact has a
canonical manifest, SHA-256 checksum, row count, source range, schema/policy
version, and upstream evidence references. Formula changes create a new feature
version; existing artifacts and snapshots are immutable.

Each historical snapshot records:

- game, first pitch, deterministic cutoff, policy and builder versions;
- research provenance and training tier;
- ordered feature schema/version and deterministic checksum;
- complete/partial/unavailable/timing-risk families;
- evidence references and classifications;
- missing/provisional fields without invented neutral evidence; and
- a reconstruction quality score that is not model confidence.

Quality uses fixed, versioned family weights and monotonic credits. Complete
Tier A evidence receives full credit; partial Tier B evidence receives reduced
credit; missing, excluded/timing-invalid evidence, and neutral fallback receive
zero. Removing evidence or downgrading a class can never increase quality.
The snapshot's tier is the worst critical-family tier, while per-family tiers
remain visible. Tier A is the primary Gate 3B population; Tier B is enhanced
research/upper-bound; Tier C is prospective gold-standard naming only for
future exact ASP captures and is never silently mixed with A/B.

## Resumability, request discipline, and raw archives

`python -m app.cli backfill-mlb-history` will be provider-specific and will not
upgrade the database, create an administrator, run operational jobs, start a
scheduler, call Odds, or activate demo fixtures. It supports date/season,
source, resume, chunk size, dry run, request cap, and optional-source skips.

Every request/chunk has an append-only checkpoint with source, endpoint/date
range, request-shape fingerprint, start/end time, status, records received and
normalized, duplicates, errors/retries, raw references/checksums, and next
resume point. Re-running a completed semantic request is a no-op. Source event
IDs, effective timestamps, source mode/classification, and checksums protect
canonical idempotency.

Initial policy defaults:

- concurrency 1 per source;
- at least 1 second between MLB/Statcast requests;
- 15–30 second request timeout;
- at most 3 attempts for transient failures with bounded exponential backoff
  and `Retry-After` support;
- no retry for schema, identity, terms, entitlement, authentication, or other
  non-retryable failures;
- circuit break after 3 consecutive provider rejections/rate-limit failures;
- dry run and request caps are enforced before adapter construction; and
- archive writes are atomic, compressed, checksummed, and Git-ignored.

One failed chunk cannot roll back already completed chunks. A resumed job starts
from the first incomplete safe checkpoint.

## Pilot request, storage, and runtime budget

Until the legal/service reviews are approved, the authorized request budget is
**zero** and only fixtures/dry runs may execute.

After approval, the first 2025 pilot is one schedule date capped at two MLB HTTP
requests, including optional pitcher enrichment, followed by no more than seven
dates / 14 requests. Estimated raw size is 0.7–2 MB and elapsed time under two
minutes at conservative pacing. The pilot intentionally makes zero Odds,
weather, or Statcast calls.

A full 2025 core season is expected to involve hundreds of schedule chunks and
thousands of game/box-score requests. Before it runs, a dry-run manifest must
calculate its exact request cap and a measured extrapolation from the seven-day
pilot. Planning envelopes, not promises, are:

- core JSON archives/canonical summaries: 0.5–3 GB per season;
- pitch-level Statcast CSV/Parquet: 0.2–1.5 GB per season after compression;
- 2023–2025 total working reserve: 15 GB including temporary and duplicate-safe
  staging; and
- expected runtime: hours, run in resumable bounded chunks rather than one
  uninterrupted job.

The minimum free-space reserve is 100 GB. Preflight refuses acquisition below
the reserve or when projected bytes would cross it. Disk usage, database size,
raw/Parquet size, row counts, duration, and representative extraction latency
are reported before and after each season.

## Validation gates

The 2025 pilot must stop after acquisition and prove:

- canonical MLB game IDs are unique and reschedules/doubleheaders retain source
  identity;
- regular-season population and legitimate exclusions are explicit;
- final scores, innings, teams, venues, starters, team/player rows, and raw
  checksums reconcile for the sampled games;
- repeated ingestion and resume create no duplicates;
- all provenance timestamps distinguish historical event time from present
  acquisition time;
- no target/current-game/future evidence enters a pregame materialization;
- storage growth and representative date/player/team/cutoff queries are within
  the planned envelope; and
- provider accounting reports exact attempts, successes, failures, retries,
  records, bytes, and elapsed time.

The mandatory leakage suite covers current-game final/team/player/Statcast
exclusion; future-game, season-end, and next-season exclusion; trade and IL
effective dates; timestamp-valid doubleheader sequencing; pregame/postgame Elo;
future park versions; actual-lineup timing classification; DEMO exclusion; and
LIVE/reconstruction isolation. Additional deterministic tests cover
cutoff/checksum stability, schedule corrections, rest/travel, bullpen workload,
source shuffling, quality monotonicity, and Bayesian shrinkage for small/large
samples, early-season priors, rookies, missing recent windows, noisy streaks,
and underlying-skill changes.

Season reports include separate percentages for final outcome, starter, actual
lineup, roster, team/player history, bullpen, Statcast, park, weather,
rest/travel, platoon, defense, baserunning, opponent adjustment, and Tier A/B
eligibility. At least ten deterministic validation games per season must span
teams, home/away, a doubleheader where available, a trade, and a rookie.

## Stop conditions

Stop a source while preserving completed work when any of these occurs:

- terms/entitlement review is missing, rejected, or incompatible;
- repeated 403/429/provider rejection, persistent rate limiting, or a circuit
  breaker trip;
- schema drift prevents safe normalization;
- provider identity cannot be resolved without guessing;
- raw archive/checksum/checkpoint failure;
- point-in-time rules or source-mode isolation fail;
- unexpected DEMO/LIVE/reconstruction contamination;
- request cap, runtime cap, or projected storage limit is reached; or
- free space would fall below 100 GB.

No bypass of blocking, rate limits, access controls, or paid entitlements is
permitted.

## Deliverables and Gate 3A exit

Gate 3A can ship its fail-closed warehouse framework even when external
acquisition remains blocked. Its report must state what was implemented versus
actually acquired, exact request accounting (including zero), season-by-season
coverage, unresolved mappings, validation samples, storage/query evidence,
source/legal limitations, and why Gate 3 remains unpromoted.

Gate 3A exits successfully only when the warehouse is reproducible and honest.
It does not exit by changing minimum samples, calling reconstructed evidence
LIVE, manufacturing market history, or training a model on insufficient data.
