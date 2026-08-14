# Gate 3A Stage A1 Real MLB Data Report

> **Final evidence boundary:** The one-day v3 and seven-day v4 artifacts, full
> 2025 MLB core acquisition, and one-day raw-only Statcast trial are reconciled
> below. The v1 artifacts remain immutable superseded audit evidence, not final
> training artifacts.

## Verdict

Gate 3A Stage A1 passed its one-day v3 and seven-day v4 artifact gates for the
strict Tier A historical-reconstruction population. The 2025 MLB regular-season
core is complete at 2,430 unique official finals. The separately controlled
one-day Statcast trial succeeded as raw-only partial evidence and was not made
canonical or training-eligible. No model was fitted, evaluated, or promoted.

The v1 materialization emitted valid lineage and leakage evidence but
misrepresented its 100-point temporal-integrity score as an overall quality
score. Its actual schema-level feature completeness was 16 of 43 fields, or
37.2093%, and its repeated evidence references produced an approximately
271.6 MB 90-row JSONL artifact. The one-day v3 and seven-day v4 rebuilds
corrected those quality semantics and compacted row-level lineage. The v4
90-row artifact is 375,715 projected bytes rather than hundreds of megabytes.

The controlling result is:

`ONE_DAY_V3_PASS / SEVEN_DAY_V4_PASS / 2025_CORE_COMPLETE / STATCAST_RAW_ONLY_PARTIAL / NO_TRAINING / NO_PROMOTION`

This is a local/private research result, not authorization for redistribution
or commercial deployment. The full 2025 core acquisition is complete, but no
full-season training artifact or model was built. Historical market evidence
remains unavailable on the current plan.

## Authorization and use boundary

The project owner recorded the following engineering-governance decision on
2026-08-13:

| Field | Recorded decision |
| --- | --- |
| Approval | `APPROVED` |
| Reviewer | Project owner |
| Scope | `LOCAL_PRIVATE_RESEARCH` |
| Purpose | Local ASP research/model development |
| Population | `HISTORICAL_RECONSTRUCTION` |
| Public redistribution | `PROHIBITED_PENDING_REVIEW` |
| Commercial deployment | `NOT_APPROVED` |
| Raw-data resale | `PROHIBITED` |
| Technical access restrictions | `RESPECT_REQUIRED` |
| Attribution/licensing | `FINAL_REVIEW_REQUIRED` |

This is owner authorization for a bounded local research run. It is not legal
advice or a legal determination, does not approve public redistribution or
commercial deployment, and does not authorize bypassing technical controls.
Raw payloads and derived artifacts remain local and Git-ignored.

## Acquired population

The pilot target was the normal 15-game regular-season slate on MLB schedule
date 2025-06-17. A 60-day prior-evidence window was acquired for the first
artifact gates, followed by the remaining 2025 core. The final canonical
regular-season window is 2025-03-18 through 2025-09-28 by official MLB game
date.

| Evidence | Persisted result |
| --- | ---: |
| Unique canonical official finals | 2,430 |
| Finals with explicit official game date | 2,430 / 2,430 |
| Team-game history rows | 4,860 |
| Player-game history rows | 127,872 |
| Canonical SQL checkpoints | 184 |
| Tier A / Tier B outcomes | 2,428 / 2 |
| Unresolved game/team/venue/player mappings | 0 |
| Corrections/revisions | 0 |
| Rejected top-level records | 0 |
| Duplicate top-level records | 0 |

The two Tier B finals are rain-ended games `777474` and `777188`. They remain
official finals but are not forced into Tier A completion evidence. All other
2025 core outcomes are Tier A.

## Request and archive accounting

Physical attempts are reported independently from the smaller set of archives
referenced by successful canonical checkpoints:

| Activity | Attempts | Successes | Failed attempts | Retries/rate limits |
| --- | ---: | ---: | ---: | ---: |
| MLB through pilot/lookback boundary | 818 | 817 | 1 legacy-v1 `404` | 0 |
| Full-2025 discovery calls | 2 | 2 | 0 | 0 |
| Early-range core acquisition | 285 | 285 | 0 | 0 |
| Late-range fail-closed rain-schema chunk | 7 | 7 | 0 | 0 |
| Partial resumed core acquisition | 278 | 278 | 0 | 0 |
| Final resumed core acquisition | 1,070 | 1,070 | 0 | 0 |
| Baseball Savant one-day trial | 1 | 1 | 0 | 0 |
| **All A1 providers** | **2,461** | **2,460** | **1** | **0** |

MLB accounts for 2,460 attempts, 2,459 successes, and the one old preflight
`404`. Statcast accounts for one successful attempt. Odds and weather account
for zero. There were no retries, `429` responses, or rate-limit events.

The final canonical checkpoint set references 2,434 archives totaling
315,853,213 bytes. This is intentionally lower than 2,459 successful MLB
responses. The 25-response difference consists of seven earlier non-ledger
preflight/discovery successes, 14 duplicate successful game-feed calls from two
chunks that later failed closed on rain-ended schema handling, and four extra
discovery/restart schedule successes. The failed chunks committed no partial
canonical facts; their repeated feeds remain counted in physical accounting.
No raw payload or dataset artifact is tracked by Git.

Provider accounting outside MLB was exactly:

| Source | Requests | Result |
| --- | ---: | --- |
| The Odds API historical endpoints | 0 | `HISTORICAL_MARKET_DATA = UNAVAILABLE_ON_CURRENT_PLAN` |
| Baseball Savant / Statcast | 1 | Successful raw-only trial; not canonical or training-eligible |
| Historical weather | 0 | Not acquired |

No historical Odds credits were consumed. Historical closing lines and CLV
remain unavailable rather than imputed.

## Completion-time evidence

The MLB feed did not provide an explicit authoritative game-final publication
timestamp for these games. All 2,430 outcomes use
`DERIVED_LAST_EVENT_COMPLETION_PROXY`. For 2,428 Tier A outcomes, the field is
the trailing completed play at
`liveData.plays.allPlays[-1].about.endTime`. The two Tier B rain-ended games use
indexed prior complete plays instead: `allPlays[49].about.endTime` for `777474`
and `allPlays[56].about.endTime` for `777188`. In every case, the value is a
proxy for recorded-play completion, not an explicit MLB game-final publication
timestamp.

No first-pitch time, retrieval time, midnight, end-of-day value, or estimated
duration was substituted. The historical completion proxy orders completed
prior-game evidence; `retrieved_at` and label availability remain the actual
2026 acquisition time and are never backdated. Each outcome is stored as
`DERIVED_LAST_EVENT_COMPLETION_PROXY`, with the proxy limitation preserved
alongside it. Of those outcomes, 2,428 are Tier A
`RECONSTRUCTABLE_POINT_IN_TIME`; rain-ended games `777474` and `777188` are
honestly downgraded to Tier B `PARTIALLY_RECONSTRUCTABLE`.

## One-day gate

The 2025-06-17 pilot passed end to end under the corrected v3 contract:

| Check | Result |
| --- | --- |
| Schedule population | 15 regular-season games |
| MLB physical attempts | 16/16 successful; 0 retries |
| Top-level records | 30 received, 30 accepted, 0 rejected, 0 duplicates |
| Canonical facts | 15 games, 15 finals, 30 team rows, 776 player rows |
| v3 Tier A materializations | 15 |
| v3 dataset rows / exclusions | 15 / 0 |
| Snapshot type | `HISTORICAL_RECONSTRUCTED_PREGAME` |
| Cutoff | `mlb_t_minus_6h_v1` |
| Frozen schema | 47 inputs / 94 outputs including missing indicators |
| Observed row inputs | 20 total; 16 non-derived of 43 coverage inputs |
| Feature completeness / admission quality | 16/43 (37.2093%) |
| Temporal-integrity checks | All true; evaluated independently from coverage |
| Artifact rows / projected total | 61,496 bytes / 68,929 bytes |
| Provider calls / model training during rebuild | 0 / 0 |
| Training readiness | Not established; A1 artifact admission is not model sufficiency |

The immutable v3 dataset is `mlb-gate3-99680a8ab2887281`. Its manifest
checksum is
`99680a8ab28872818d9e51086867d277bdd4b1156f9022b7812658d5207d1f8c`
and its rows checksum is
`9312c0e58c8b0ad9ff18fd6ece08875a8a9940515a48e59f7a98584f1a836cf8`.
The frozen artifact exposes all 47 inputs through 94 value/missing-indicator
outputs while storing only compact row-level lineage attestations. Its Tier A
classification describes reconstructability and temporal provenance; its
37.2093 admission-quality score separately reflects the minimum of temporal
integrity and frozen-schema feature coverage.

### Pilot reconciliation

Every source game reconciled to one canonical game and one official final. The
time shown is the recorded last-play completion proxy described above.

| MLB gamePk | Matchup | Final (away-home) | Completion proxy (UTC) |
| ---: | --- | ---: | --- |
| 777481 | Phillies at Marlins | 3-8 | 2025-06-18T00:54:46.905Z |
| 777475 | Pirates at Tigers | 3-7 | 2025-06-18T00:59:58.293Z |
| 777480 | Rockies at Nationals | 10-6 | 2025-06-18T01:41:58.584Z |
| 777476 | Angels at Yankees | 4-0 | 2025-06-18T01:38:03.701Z |
| 777479 | Diamondbacks at Blue Jays | 4-5 | 2025-06-18T02:06:22.235Z |
| 777478 | Twins at Reds | 5-6 | 2025-06-18T02:16:16.560Z |
| 777489 | Mets at Braves | 4-5 | 2025-06-18T02:39:11.411Z |
| 777477 | Orioles at Rays | 5-1 | 2025-06-18T02:18:08.805Z |
| 777467 | Cardinals at White Sox | 12-2 | 2025-06-18T02:13:47.838Z |
| 777468 | Royals at Rangers | 6-1 | 2025-06-18T02:32:59.258Z |
| 777482 | Brewers at Cubs | 3-5 | 2025-06-18T02:31:13.956Z |
| 777473 | Red Sox at Mariners | 0-8 | 2025-06-18T04:01:20.318Z |
| 777472 | Guardians at Giants | 3-2 | 2025-06-18T04:21:13.766Z |
| 777469 | Astros at Athletics | 13-3 | 2025-06-18T04:54:03.822Z |
| 777471 | Padres at Dodgers | 6-8 | 2025-06-18T05:19:33.869Z |

## Seven-day v4 gate

The 2025-06-11 through 2025-06-17 schedule-date acquisition and temporal audit
covered 90 distinct games. The optimized provider-free v4 rebuild succeeded in
437.3 seconds with 90 rows, zero exclusions, and no duplicate game identities.

| MLB schedule date | Games / rows | Rejected | Duplicates |
| --- | ---: | ---: | ---: |
| 2025-06-11 | 15 | 0 | 0 |
| 2025-06-12 | 8 | 0 | 0 |
| 2025-06-13 | 15 | 0 | 0 |
| 2025-06-14 | 15 | 0 | 0 |
| 2025-06-15 | 15 | 0 | 0 |
| 2025-06-16 | 7 | 0 | 0 |
| 2025-06-17 | 15 | 0 | 0 |
| **Total** | **90** | **0** | **0** |

| v4 evidence | Result |
| --- | --- |
| Dataset / database manifest | `mlb-gate3-84ea623d0b3c8c41` / `62d75e29-a9fd-497a-a5cd-b4bf560ac8f3` |
| Manifest checksum | `84ea623d0b3c8c417eaef85fe28bad1e9d92058dd5c3acd834b86b63b8319520` |
| Rows checksum | `4fd3b70e8877fd065d0c6fa6b0ed3dddf417b009e0c18e939bcd62c115003349` |
| Rows / manifest / exclusions bytes | 368,281 / 7,432 / 2 |
| Projected artifact total | 375,715 bytes |
| Canonical reference occurrences | 174,649, compactly attested |
| Frozen schema / observed inputs | 47 inputs, 94 outputs / 20 observed |
| Feature coverage / admission quality | 16/43 (37.2093%) |
| Temporal integrity | 100; all checks true |
| Leakage contract | All checks true |
| Market evidence | Null; `no_market_or_clv=true` |
| Provider calls / model training | 0 / 0 |

The v4 manifest's UTC date coverage ends on 2025-06-18 because late US games
on the 2025-06-17 MLB schedule date began after midnight UTC. It revalidated
canonical lineage from the database and records compact attestations rather
than serializing 174,649 repeated reference objects into the rows file.

V4 family coverage is 100% for rating, team, and recent form; 80% for schedule
and rest/travel inputs; 33.3333% for bullpen; and 0% for starter, lineup,
Statcast, context, and lifecycle. Actual starters and actual batting orders are
preserved as canonical postgame participation facts, but their historical
pregame publication time is unknown, so they are not promoted into Tier A
pregame features. A later Tier B enhanced population may use them only with an
explicit timing-risk label and separate reporting.

Every v4 manifest leakage assertion passed, including one row per game, pregame
cutoff, source-evidence cutoff, target-only final scores, absence of target
features, historical identity, tier consistency, and no market/CLV evidence.
Operational `source_mode=LIVE` identifies real non-demo source evidence; the
snapshot type and temporal semantics explicitly identify retrospective
reconstruction and must not be described as prospective LIVE capture.

The superseded v1 90-row artifact remains audit evidence for the defect that v4
corrected; it is not the closeout artifact.

## Full 2025 MLB core

The canonical core covers all 2,430 unique official 2025 regular-season finals
from 2025-03-18 through 2025-09-28. Every final has an explicit official MLB
game date, one home and one away team row, exact source identity, and a retained
completion-evidence basis. No record was rejected, duplicated, or revised.

| Core reconciliation | Result |
| --- | ---: |
| Official finals / official dates | 2,430 / 2,430 |
| Team-game rows | 4,860 |
| Player-game rows | 127,872 |
| Canonical SQL checkpoints | 184 |
| Tier A reconstructable outcomes | 2,428 |
| Tier B rain-ended outcomes | 2 |
| Revisions / duplicates / rejections | 0 / 0 / 0 |

This completes core acquisition, not full-season feature materialization or
model training. The two Tier B outcomes are kept separate, and all 2,430
completion timestamps remain labeled as derived last-event proxies rather than
authoritative final-publication times.

## One-day Statcast trial

One Baseball Savant request for 2025-06-17 succeeded and was archived raw-only:

| Trial evidence | Result |
| --- | --- |
| Rows / games | 4,375 / 15 |
| Batters / pitchers / unique players | 304 / 133 / 436 |
| Fields / schema drift | 119 / none detected |
| Archive bytes | 2,523,608 |
| Payload checksum | `bcaa171d4adfb8666ab3ee85eb0490710103825ecf973f8b07a80e55c53a4251` |
| CSV checksum | `932d42bec7a1536374648ae6bc99827f31267bdd56f02db1955638e24ae3b055` |
| Archive checksum | `601af8d47af4dfe66102efd4df38ecd324e8ebf38449ddc8eb0c708337e79f2d` |

This trial is explicitly `PARTIAL / RAW_ONLY`. Publication time and historical
eligibility are unproven. The rows were not canonicalized, aggregated into
pregame features, admitted to a dataset, or used for training. Successful
technical acquisition does not establish redistribution or commercial-use
rights.

## Final local storage snapshot

| Storage family | Closeout measurement |
| --- | ---: |
| PostgreSQL | 515,038,231 bytes |
| Canonical referenced archives | 2,434 files / 315,853,213 bytes |
| Full configured raw root | 2,512 files / 323,342,002 bytes |
| Ignored runtime root | 35 files / 479,245,075 bytes |
| Historical Parquet | 0 files / 0 bytes |
| One-day v3 projected artifact | 68,929 bytes |
| Seven-day v4 projected artifact | 375,715 bytes |

The runtime total retains superseded immutable v1 audit artifacts, including
the deliberately documented large repeated-reference artifact. The compact v3
and v4 artifacts correct that layout without deleting prior audit evidence.

## What Stage A1 does not establish

- No 2023-2024 or multi-season expansion has begun.
- No full-season reconstructed feature/dataset artifact has been built.
- Statcast exists only as one raw, partial trial; it is not canonical or
  training-eligible.
- No historical weather has been acquired.
- No historical Odds endpoint was called and no closing line or CLV was
  fabricated.
- No model, calibrator, holdout evaluation, promotion, public pick, or virtual
  position was produced.
- The valid 90-row v4 artifact remains below Gate 3's unchanged 200-row minimum
  and does not support a performance or superiority claim.
- Owner authorization does not resolve final attribution, licensing,
  retention, redistribution, or commercial-deployment review.

## Next decision

Stage A1 has produced corrected governed one-day and seven-day artifacts,
completed the 2025 MLB core, and quarantined one successful Statcast day as
raw-only partial evidence. The next action is a human review of storage growth,
the completion-proxy limitation, two Tier B rain-ended games, missing feature
families, Statcast publication/eligibility uncertainty, and source-governance
conditions. This report does not authorize multi-season acquisition, Statcast
expansion, model training, or deployment.
