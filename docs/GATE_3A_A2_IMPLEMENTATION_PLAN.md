# Gate 3A Stage A2 Implementation Plan

## Purpose and governing boundary

Stage A2 converts the governed 2025 MLB core into a full-season,
point-in-time model-development dataset. It does not fit, calibrate, evaluate,
promote, or publish a model. It does not inspect outcome-based holdout metrics,
activate the virtual bankroll, acquire historical market data, or begin another
sport or product phase.

The operational source mode remains `LIVE`, while every reconstructed row is
explicitly marked `RETROSPECTIVE_RECONSTRUCTION`. Historical Odds and CLV stay
unavailable and are never imputed. Raw archives, databases, and generated
training artifacts remain local and Git-ignored.

## Mandatory pre-run evidence

The read-only audit began from:

| Evidence | Baseline |
| --- | --- |
| Git root | `C:/Users/james/ai-sports-predictor` |
| Branch / HEAD | `main` / `58631df40729131afa9b6761ec8714d33bdbc54f` |
| Worktree | Clean |
| PostgreSQL | 515,038,231 bytes |
| Historical raw archive | 2,438 files / 319,590,230 bytes |
| Configured raw root | 2,512 files / 323,342,002 bytes |
| Gate 3 runtime | 35 files / 479,245,075 bytes |
| Entire runtime | 49 files / 479,250,936 bytes |
| Free disk | 281,622,319,104 bytes (262.281223 GiB) |
| Canonical finals | 2,430, all with official dates |
| Strict / partial outcomes | 2,428 Tier A / 2 Tier B |
| Canonical team / player facts | 4,860 / 127,872 |
| Canonical checkpoints | 184 succeeded; 2,435 attempts; zero retries/rejects/duplicates |
| Current materializations | v1 90, v3 15, v4 90 |
| Current strict dataset | 90 rows, zero exclusions |
| Current completeness | 16/43 non-derived fields (37.2093%) |
| One-day Statcast | 4,375 raw-only rows, 15 games, 436 players, 119 fields |

All 2,430 labels use a documented
`DERIVED_LAST_EVENT_COMPLETION_PROXY`; retrieval and label availability remain
their real 2026 acquisition times. Games `777474` and `777188` use prior
complete-play proxies and remain Tier B. They are excluded from the strict
Tier A dataset.

Gate 3's unchanged audit thresholds include at least 200 rows, 50 locked
holdout rows, 41 event dates, 40 development date groups, three rolling-origin
folds, two outcome classes, and a 20% date-group holdout. The 70% required
family-coverage rule is a later promotion gate, not permission to fabricate
missing evidence in A2.

## Target populations and immutable identities

The principal population is the 2,428 Tier A reconstructable official finals.
Each eligible game receives one immutable
`HISTORICAL_RECONSTRUCTED_PREGAME` snapshot at the versioned T-minus-six-hour
cutoff. Early-season games may use only a declared population prior plus the
evidence genuinely available before their cutoff; missing history stays
missing and reliability remains low.

A separate Tier B enhanced population may use historical actual-starter and
actual-lineup identities as timing-risk context. It never changes a Tier A row,
never treats final-boxscore participation as proven pregame publication, and
never imports same-game performance. The two rain-ended labels remain Tier B.

A2 creates new content-derived feature, materializer, lineage, and dataset
versions. A1 v1/v3/v4 rows and artifacts remain immutable audit evidence and
are not overwritten.

## Season-scale performance design

The current v4 path must not be run over the season. It re-queries and replays
the season for each target, retains every full snapshot result, and serializes
the same evidence references into both snapshot and materialization JSON.
Read-only projection found 7,537,220 references for the full target universe,
with a 6,274-reference maximum row. The naive path would add about 4.4 GB of
duplicated compressed manifest JSON, retain potentially more than 20 GB of
Python/ORM state, and take at least five to six hours.

A2 therefore uses a chronological season replay:

1. Load the exact 2025 target games, latest eligible final revisions, team
   facts, player facts, venues, teams, players, checkpoints, and approved source
   versions in bounded set-based queries.
2. Sort targets by cutoff and facts by historical eligibility time.
3. Advance one incremental state cursor. A prior game becomes eligible only
   after its final-completion proxy is strictly before the target cutoff.
4. Maintain rolling team, player, Elo, workload, and Bayesian accumulators;
   avoid replaying the season from the beginning for each game.
5. Persist compact, checksum-addressed evidence-state attestations rather than
   millions of repeated evidence dictionaries. The attestation records counts,
   time bounds, family/source checksums, latest-revision policy, target
   exclusion, and source bundle; canonical facts remain independently
   resolvable in the warehouse.
6. Stream materialization results and commit resumable date-sized batches.
   Return counters and identities, never an in-memory tuple of every snapshot.
7. Build the final compact JSONL or measured Parquet matrix plus a canonical
   JSON manifest. The existing reader format is retained unless measured JSONL
   size or parse time justifies Parquet.

Performance tests will cap SQL query growth, memory retention, artifact bytes
per row, and duplicate serialization. Before/after runtime and query counts are
recorded. A dry projection precedes the final artifact write.

## Feature schema and coverage targets

The A2 frozen schema records ordered inputs, value/missing outputs, dtypes,
families, neutral values, window definitions, prior policy, and derivation
versions. Every row separately retains:

- temporal-integrity result;
- feature completeness;
- admission quality;
- reconstruction tier;
- missing, provisional, and timing-risk families;
- compact source-lineage attestation.

Coverage is reported, not optimized by changing the denominator after seeing
results. The report explicitly states whether average strict coverage exceeds
45%, 55%, and 65%. Integrity wins over every coverage threshold.

## Chronological team state

For both clubs, the replay produces season-to-date, prior-30-day, and
prior-14-day runs scored and allowed, with exact sample sizes and reliability.
Same-game values are excluded by game identity and event time. Team offense and
run-prevention posteriors use a documented league prior and strong
early-season shrinkage. Recent-form features are posterior deltas rather than
streak or momentum bonuses.

Real Elo starts from the documented 1500 population prior because 2024 has not
been acquired. The home-field adjustment and K factor are frozen in the
feature version. Each target stores pregame home/away ratings, games played,
and expected home probability. Updates occur only after an eligible final;
same-day ordering uses completion time and deterministic game identity.

## Starter and bullpen reconstruction

Strict Tier A does not use actual historical starter identity unless its
pregame publication time is independently proven. Tier B may use that identity
as provisional context and derive days rest, previous-start innings/pitches,
seven-day pitches, fourteen-day innings/appearances, and workload trend.
Missing pitch counts remain missing; outs are never converted into an invented
pitch count.

Tier A bullpen state is reconstructed from prior relief appearances and is
labeled `WORKLOAD_DERIVED_AVAILABILITY_PROXY`, never manager-confirmed
availability. Planned values include prior-one/two/three-day pitches and outs,
three-day innings, consecutive-day relievers, recent-pool count, a transparent
fatigue score, and a shrunk reliever-pool quality estimate. The formula,
role/pool policy, sample size, uncertainty, and evidence checksum are versioned.
Current-game appearances are prohibited.

## Bayesian player and team evidence

Supported batting and pitching statistics use an empirical-Bayes contract that
stores the prior, prior effective sample, season estimate/sample, recent
estimate/sample, posterior, posterior reliability, and posterior delta. The
2025 early-season prior is a league/population prior only. No 2024 values are
invented and no later 2025 observations can influence an earlier row.

Team batting, run prevention, and bullpen pool posteriors aggregate only
players with prior eligible team participation. Player talent follows player
identity across teams; membership in the target team's pool follows effective
prior participation and never moves backward across a trade. Small samples are
shrunk aggressively.

## Rest, travel, context, and deferred families

Rest/travel uses the latest eligible completed game, exact completion time,
venue coordinates when present, consecutive-day status, prior innings, and
doubleheader ordering. Venue coordinates are never fabricated. Missing
doubleheader schedule evidence must remain missing unless recovered from an
existing governed raw archive or a new versioned canonical field.

Park factor stays missing/provisional unless a pre-2025, attributable,
versioned factor is available. A full-2025 factor is never applied backward.
Weather is optional and cannot delay the core dataset. Platoon, defense,
baserunning, and opponent adjustment are added only when their chronological
evidence and definitions are defensible; otherwise they remain unavailable.

## Controlled Statcast promotion

The existing 2025-06-17 archive is promoted before any new request. Promotion
must verify exact archive and CSV checksums, schema fingerprint, game/player
mapping, event identity/time, duplicates, canonical subset formulas, and
same-game exclusion. The deliberate canonical subset covers game, event,
batter, pitcher, event time/sequence, launch speed/angle, estimated wOBA,
barrel/hard-hit flags, outcome, pitch type/velocity, swinging strike/whiff,
strikeout, walk, and contact-quality evidence needed by declared features.

Because retrospective acquisition does not itself prove historical publication
time, promoted events remain separately classified until the eligibility policy
is justified. They cannot silently enter strict Tier A. Point-in-time hitter
and pitcher aggregates use season-to-date, 30-day, and 14-day windows, exclude
the target game, and enforce doubleheader completion/cutoff ordering.

Only after one-day canonical promotion and duplicate/idempotency tests pass may
new Statcast acquisition proceed: seven dates, then approximately 30 dates,
then full 2025 only if mapping, schema, request behavior, storage, and temporal
checks remain within their stop gates. Each phase has a dry-run plan, exact
request cap, concurrency one, conservative pacing, raw-first archiving, and
separate attempts/successes/failures/retries/rate-limit/rows/bytes accounting.
Historical Odds remains zero requests.

## Storage and runtime safety

The existing artifact cap is 128 MiB and the minimum post-write free-space
reserve is 100 GiB. A2 preserves both. The core compact dataset is expected to
remain in tens of MiB; a projected artifact over 128 MiB fails before writing.
The run stops if free space would cross the reserve, database lineage becomes
unbounded, memory exceeds the measured batch budget, or optional Statcast
growth threatens completion of the core materialization.

Raw payloads, promoted event artifacts, matrices, checkpoints, temporary
files, and database files remain ignored. Only code, migrations, tests, and
documentation may enter Git.

## Validation and audit-only compatibility

Automated tests cover chronological state advancement, target exclusion,
current-game player/Statcast exclusion, Elo update order, Bayesian chronology,
starter/bullpen windows, trades, doubleheaders, rain-tier isolation, compact
attestation integrity, correction invalidation, artifact caps, idempotency, and
source-mode isolation. Normal tests construct no provider adapter.

Before acceptance, at least 50 games are deterministically sampled across
April through September, multiple clubs, doubleheaders, extra-inning prior
games, road trips, rookies/minimal-history players, trades, bullpen-heavy
games, and the two Tier B rain games. Exact exclusions are reported by reason;
duplicates, mapping failures, timing failures, lineage failures, and quality
failures cannot be silent.

The final provider-free compatibility audit may inspect row count, date-group
count, class presence, manifest/schema resolution, leakage gates, and whether a
chronological holdout can be formed. It must not fit, calibrate, optimize, score,
or reveal outcome-based holdout metrics.

## Stop conditions

Stop the affected phase immediately for any of the following:

- same-game or future evidence enters a feature;
- Tier B evidence is relabeled Tier A;
- duplicate game rows or conflicting canonical identities appear;
- player/team trade chronology moves backward;
- Elo or workload incorporates an ineligible final;
- snapshot, attestation, source, or artifact checksum fails;
- canonical mapping is unresolved or ambiguous;
- feature completeness increases after evidence removal;
- query/memory/artifact growth violates the bounded plan;
- Statcast schema/mapping/duplicate/request behavior fails its current gate;
- free disk would fall below 100 GiB;
- any normal test attempts a provider call;
- any path attempts model training, promotion, historical market acquisition,
  or holdout scoring.

## Execution order

1. Implement frozen A2 schema, chronological state, compact attestations,
   resumable materialization, and tests.
2. Prove one-day and seven-day provider-free A2 reconstruction with unchanged
   labels and stronger feature coverage.
3. Promote the existing one-day Statcast archive and run its canonical/leakage
   gate; perform gated expansion only if approved conditions remain true.
4. Materialize all 2,428 strict Tier A targets, then build the immutable full
   dataset and optional separate Tier B enhanced dataset.
5. Run the 50-game reconciliation, full backend suite, audit-only Gate 3
   compatibility, storage/provider reconciliation, secret/diff audit, and write
   the final A2 report.
6. Commit code, migrations, tests, and docs only. Do not push.
