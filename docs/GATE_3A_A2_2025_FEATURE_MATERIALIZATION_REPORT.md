# Gate 3A Stage A2 2025 Feature Materialization Report

## Verdict

`A2_MATERIALIZATION_COMPLETE / NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT / NO_MODEL_FIT / NO_HOLDOUT_METRICS / NO_PROMOTION / A2_PROVIDER_CALLS=0`

Stage A2 successfully reconstructed and persisted the governed 2025 MLB
feature populations:

- strict Tier A: 2,428 rows, 2,428 distinct games, zero exclusions;
- enhanced Tier B shadow: 2,430 rows, 2,430 distinct games, zero exclusions;
- immutable artifacts: 70,629,875 bytes strict and 70,988,195 bytes enhanced;
- chronological, schema, identity, source-mode, target-exclusion, and market
  leakage gates: all passed;
- model fitting, calibration, holdout scoring, evaluation, forecast generation,
  promotion, and bankroll positions: all zero.

The strict dataset is not ready for Gate 3B because starting-pitcher, lineup,
Statcast, park/weather, defense/baserunning, and opponent-adjustment coverage
do not meet the declared 70% per-family gate. This is an evidence/provenance
gap, not a sample-size gap. Acquiring 2023-2024 alone would not prove the
missing pregame availability and is not authorized by this report.

## Authorization and boundary

The work used only the local A1 canonical warehouse and the existing local
one-day Statcast archive. It made no MLB, Baseball Savant, Odds API, weather,
or other provider request. Raw data and generated artifacts remain local and
Git-ignored. Historical market data remains unavailable on the current plan.

| Boundary | Result |
| --- | --- |
| Baseline commit | `58631df40729131afa9b6761ec8714d33bdbc54f` |
| Source mode | `LIVE` |
| Temporal semantics | `RETROSPECTIVE_RECONSTRUCTION` |
| Cutoff | `mlb_t_minus_6h_v1` |
| Strict population | `TIER_A_STRICT` |
| Enhanced population | `TIER_B_ENHANCED`, research-only |
| Historical market / CLV | unavailable; not imputed |
| A2 provider calls | 0 |
| Model fits / evaluations / promotions | 0 / 0 / 0 |

## Input evidence

The governed A1 population contained 2,430 unique official 2025 finals from
2025-03-18 through 2025-09-28, with 4,860 team-game rows and 127,872
player-game rows. The label inventory is 2,428 Tier A outcomes and two Tier B
rain-ended outcomes (`777474`, `777188`). All labels retain the limitation
`DERIVED_LAST_EVENT_COMPLETION_PROXY`; none is presented as an authoritative
MLB final-publication timestamp.

The canonical warehouse remained unchanged during A2: 184 historical
checkpoints, 2,435 recorded attempts, 188 ingestion runs, and no new provider
ledger entries.

## Frozen implementation identity

| Contract | Identity |
| --- | --- |
| Schema | `mlb_historical_pregame_a2_v2` |
| Schema SHA-256 | `e29cf4f37487590ff09266f189323f9c42f9c8e99f0d64d8d61bdf41d7b9b9ff` |
| Raw values / paired outputs | 272 / 544 |
| Builder | `gate3a_a2_chronological_v6` |
| Strict feature version | `a2-tier-a-strict-v6` |
| Strict feature-version ID | `7cdfa80f-725d-4061-8ca5-5967d9ea5701` |
| Strict definition SHA-256 | `a107f2404130b9c1b086c6d52e65a95b8ec888902e7deb9b69a8cc3f41f04925` |
| Enhanced feature version | `a2-tier-b-enhanced-v6` |
| Enhanced feature-version ID | `b14debd6-b72a-4f16-aa34-73c771a733a6` |
| Enhanced definition SHA-256 | `562fbd2849f3dbf07100220e84257389a62c253808044fb7cf79d779ff06b199` |
| Compact lineage | `gate3a_a2_compact_lineage_v1` |
| Source revision digest | `95f20a566667e53ac24200677665ffc18e1ee79334a8350ff7e41b0a048eaf88` |

The engine performs one chronological season replay. Evidence enters state
only after its governed event and eligibility boundary. Target-game facts are
excluded by identity. It supports season, 30-day, and 14-day team and player
rates; Bayesian shrinkage; chronological Elo; bullpen workload and quality;
starter workload for the enhanced population; and rest/travel when the
required venue evidence exists. Missing evidence remains missing.

## Materialization result

| Result | Strict Tier A | Enhanced Tier B |
| --- | ---: | ---: |
| Selected targets | 2,428 | 2,430 |
| Persisted attestations | 2,428 | 2,430 |
| Persisted snapshots | 2,428 | 2,430 |
| Persisted materializations | 2,428 | 2,430 |
| Post-selection exclusions | 0 | 0 |
| Duplicate game identities | 0 | 0 |
| Committed batches | 25 | 25 |
| Training-eligible rows | 2,428, subject to readiness | 0 |

The strict materialization initially inserted 2,428 identities across the
one-day, seven-day, and full-season runs. A complete replay then inserted zero
and reused all 2,428 attestations, snapshots, and materializations. The
enhanced population is a separate all-2,430 shadow; every row is Tier B,
research-only, and training/evaluation-ineligible.

No Tier B evidence entered strict lineage. Strict starter/lineup assumptions,
same-game player leakage, same-game/future outcome leakage, mapping failures,
timing failures, lineage failures, and quality failures were all zero.

## Compact lineage

Strict season replay referenced 8,731,965 canonical identity occurrences but
only 78,306 unique identities. Mean references per row were 3,596.3612 and the
maximum was 7,412. Each persisted row stores one compact, content-addressed
attestation reference rather than repeating the full evidence dictionaries.
No full canonical reference dictionary was serialized into the training-row
artifacts.

The attestation binds the target, cutoff, feature version, population,
outcome/team/player counts, time bounds, family checksums, source bundle,
canonical set, feature values, and target/temporal gates. The full idempotent
replay found zero checksum or identity failures.

## Strict feature coverage

Mean raw-value coverage was 85.4483%; median and maximum were 85.6618%, and
the minimum was 51.1029%. All 2,428 rows exceeded 45%; 2,414 exceeded both
55% and 65%. Mean admission quality was also 85.4483% because the A2 quality
contract uses the exact frozen feature denominator.

| Frozen A2 family | Strict coverage |
| --- | ---: |
| Team | 99.5881% |
| Recent | 99.5881% |
| Talent | 99.6470% |
| Rating | 100.0000% |
| Hitting | 99.7352% |
| Pitching | 99.7426% |
| Bullpen | 100.0000% |
| Schedule | 88.3397% |
| Target context | 0.0000% (intentionally absent in strict rows) |
| Lifecycle | 100.0000% |

The separate Gate 3 compatibility mapping reported: team strength 99.7035%,
recent form 99.5881%, bullpen 100%, and rest/travel 88.3397%; starting pitcher,
lineup, Statcast, park/weather, defense/baserunning, and opponent adjustment
were 0%. Therefore the required-family coverage check failed honestly.

## Immutable datasets

| Artifact | Strict Tier A | Enhanced Tier B |
| --- | --- | --- |
| Dataset ID | `mlb-gate3-a2-3059f81db1d0e278` | `mlb-gate3-a2-a371eaeecdbe9a36` |
| Database manifest ID | `d4087299-8a04-4176-bad2-a9ec19128b62` | `7fec0a45-3bb6-409b-b8be-e63e5d423a1d` |
| Rows | 2,428 | 2,430 |
| Exclusions | 0 | 0 |
| Date range | 2025-03-18 to 2025-09-28 | 2025-03-18 to 2025-09-28 |
| Rows SHA-256 | `bc627c16f85ac3372cb2cb3fb1c9dab6767291704dac790fb5ffe07b8af406bb` | `5261459fa99e1f6b809d8503035261dd285cee7e9c059b5de8a509f691d88e68` |
| Manifest identity SHA-256 | `3059f81db1d0e2783d0357cf765fbc027958a1d0786c8da83611bae82dca1748` | `a371eaeecdbe9a363c879c6b64b960d2e44e62c242970cf3e2e463bcf6bfd996` |
| Exclusions SHA-256 | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` | same |
| Rows / manifest / exclusions bytes | 70,571,103 / 58,770 / 2 | 70,928,035 / 60,158 / 2 |
| Total bytes | 70,629,875 | 70,988,195 |
| Quality tiers | `["A"]` | `["B"]` |

Both artifacts are below the 128 MiB limit and were written with a 100 GiB
post-write reserve. The strict artifact uses 29,089.73 bytes per row.

## Audit-only readiness

Strict status: `NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT`.

The audit found 2,428 rows and games across 184 event-date groups. A
chronological 20% holdout is structurally formable with 37 date groups and
505 rows; the development partition has 147 date groups, both outcome classes,
and 16 rolling-origin folds. Manifest, schema, compact lineage, T-minus-six
cutoffs, LIVE-only source mode, label availability, one-row-per-game, target
exclusion, and no-market/no-CLV checks all passed. Holdout labels were not
dereferenced and no metric was computed.

The only failed strict audit check was required-family coverage. The enhanced
artifact is also `NOT_READY`: it is Tier B by design and additionally lacks
Statcast, park/weather, defense/baserunning, and opponent-adjustment evidence.

## Existing Statcast archive promotion

The 2025-06-17 raw archive was promoted locally without a provider call into:

`gate3/statcast_a2/statcast-a2-d0c7d1b78871d2f7`

It contains 4,375 events and 1,311 research aggregates, with zero semantic
duplicates, mapping failures, or team mismatches. Total artifact size is
11,664,275 bytes. Checksums are:

- manifest semantic: `d0c7d1b78871d2f7882923fd1d39c8aa90b635fdb3f493229ae0490be14c790b`;
- events: `4711acedf5d6a4ff1665ae557468169942b76fc8b421eb82a7e20edbfe3ec162`;
- aggregates: `ca830e4a9514c3fecc3b943bfa5dfdbe1b83d55f545c4df0f1bd936850dd6cba`.

Idempotent reuse passed. Historical publication timing is unproven, so this
artifact remains Tier B, research-only, training/evaluation-ineligible, and
contributes zero strict Tier A features. The expansion decision is
`NO_STATCAST_EXPANSION`: more requests would not repair strict provenance.

## Real-row reconciliation

A deterministic 50-game sample covered March through September, 28 clubs,
48 strict rows, both Tier B rain games, three same-day multi-game proxies, six
extra-inning-prior cases, 14 travel/venue-change cases, 19 minimal-history
cases, 19 bullpen-heavy cases, and 50 games containing multi-team player
history. The audit recomputed cutoff, feature-count, completeness, tier,
training eligibility, attestation bounds, checksum shape, and market/Statcast
isolation. Formula/source mismatches and leakage findings were zero.

Sample identity SHA-256:
`a73dd80db2e1256589dcbba76562afebe7e2903fb2a569bedd4415d31cf01545`.

## Performance and storage

| Operation | Wall time |
| --- | ---: |
| One-day golden replay, 15 rows | 61.188 s |
| Seven-day golden replay, 90 rows | 69.341 s |
| Full strict materialization | 374.617 s |
| Strict dataset build | 344.409 s |
| Full enhanced materialization plus dataset | 736.868 s |
| Strict lineage audit plus full idempotent replay | 592.561 s |

The full strict loader took about 57-67 seconds. A query-count regression
caps the set-based loader at 12 statements for the 12-game fixture, preventing
the former per-game Game/GameTeam N+1 path. Python allocation tracing was not
used for the final season runs because it materially distorted runtime; the
first traced golden attempt hit its 240-second stop before any write. All
untraced bounded runs completed without an OS memory or storage stop.

| Storage | Before A2 | After A2 | Delta |
| --- | ---: | ---: | ---: |
| PostgreSQL | 515,038,231 | 772,414,487 | +257,376,256 |
| Historical raw archive | 319,590,230 | 319,590,230 | 0 |
| Configured raw root | 323,342,002 | 323,342,002 | 0 |
| Gate 3 runtime | 479,245,075 | 632,527,420 | +153,282,345 |
| Entire runtime | 479,250,936 | 632,533,281 | +153,282,345 |
| Free disk | 281,622,319,104 | 291,682,959,360 | +10,060,640,256 system-wide fluctuation |

The raw archive did not grow. Free space remained well above 100 GiB.

## Provider and model reconciliation

A2 made zero provider attempts and zero provider successes. The existing
provider ledger remains exactly two Odds API rows (`RESERVED`, `SUCCEEDED`)
with total recorded quota cost 3. No MLB, Statcast, Odds, or weather ledger
entry was added by A2.

The database still contains one pre-existing DEVELOPMENT model version and
zero model evaluations. A2 did not import or invoke the training/modeling
pipeline, inspect holdout metrics, generate forecasts, or promote anything.

## Verification

- focused A2 suite: 55 passed;
- migration-family suite: 17 passed using the established Windows 0777 pytest
  temporary-directory workaround;
- full provider-disabled backend suite: 392 passed in 92.8 seconds;
- Gate 3A regression sweep during integration: 201 passed;
- Python compile/import checks: passed;
- fresh SQLite upgrade to `20260814_0008`, `alembic check`, downgrade to
  `20260814_0007`, re-upgrade, and second check: passed;
- PostgreSQL offline `0007 -> 0008` SQL generation: passed;
- local PostgreSQL upgraded to `20260814_0008 (head)` with no metadata drift;
- one-day, seven-day, full strict, full enhanced, and strict idempotent replay:
  passed;
- frontend contract: unchanged; no frontend rebuild was required.

## 2025 sufficiency and next decision

Final decision: `A2_BLOCKED_FOR_NON_SEASONAL_EVIDENCE`.

The 2025 strict population is large enough to form a bounded chronological
development/holdout structure: 2,428 games, 184 dates, 505 holdout rows, both
development-period classes, and 16 rolling folds. It is not yet authorized
for Gate 3B because the declared evidence-family gate fails. More seasons can
improve preseason priors and regime diversity, but cannot retroactively prove
pregame starter/lineup publication, make the current-ingestion Statcast file
Tier A, or manufacture park/weather, defense/baserunning, opponent adjustment,
or historical market evidence.

Therefore Stage A2 does not authorize 2023-2024 acquisition. The next plan
should address the named provenance and feature-family gaps first. Any model
development, additional acquisition, Statcast expansion, or deployment needs
separate authorization.
