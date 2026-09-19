# NBA integration evidence, 2026-09-19

This traces one real NBA game end to end — **package → mapping → channels → outcome → score** — using only
bytes that Model Studio (Codex) had already retained. It then sets out the checks that are not yet due.

## How this was run

- **Code:** branch `claude/nba-gen1-integration`, run on 2026-09-19.
- **Database:** a **scratch SQLite database** in the session scratchpad, never the live database. The AISP
  game UUIDs quoted below are therefore scratch identities; a real import assigns its own.
- **Live database:** read-only throughout; nothing was written to it.
- **Network:** no provider request, no spend.

The game is **HISTORICAL**. The 2025-26 rows are a Category A reconstruction from finalized records: the
models were frozen before any 2025-26 score was read, and the forecasts were never published before the
games. They are scored in their own evidence class and are never counted as prospective forecasts.

## 1. Package: the pinned, hash-bound inputs

| Input | Identity |
|---|---|
| 2025-26 fixed assessment receipt (`NBA_GEN1_REST_FIXED_2026_ASSESSMENT_V1_ASSESSMENT_RECEIPT`, created 2026-09-14T13:04:28Z) | `26125dfcba854896bb4cc83b4af72e4f6a1cfb9a03a3122b31429ac45bb9a662` |
| → `predictions.parquet` (bound by the receipt; SHA-256 and bytes re-verified) | `8663690cffb6e9b01532d60c6de74bb8b975cc38ee0cdd5e7db9b491a76a4353` |
| Joint-residual season receipt 2026 (`NBA_GEN1_JOINT_RESIDUAL_SEASON_ASSESSMENT_V1`) | `d62e3efb7e42d76a52b4f26ae10e9f4e5203b2a0e86a6113b731e661b829cb57` |
| → `paired-joint-counts.parquet` | `3ef6eb603024d3e725e6b51f3dd19caf34ca1112d986b485c9c1b90478231e8a` |
| → joint method freeze (the **same** method the production handoff binds for its joint channel) | `c4960a87cf4b0318ff844a3deaadacd5cdd03c0db21b9944bee178533663ccfc` |
| Retained ESPN 2025-26 schedule object `nba_schedule_2026.parquet` (content-addressed; SportsDataverse, CC BY 4.0) | `b3884aa4aa276d1fcafc71b69394675904a968902068bff634932e5d7afacae4` |
| → acquisition receipt, which names that object (`acquiredAtUtc` 2026-09-14T12:52:46.784132Z) | `e8cc18825af4fe953056e6728a1c8e4b0f59645b044ccb7106d6998011b843b5` |

The whole-season import verified:

- 1,225 of 1,225 games imported;
- 30 franchise↔ESPN-team pairs, **0 conflicts**;
- **0 skipped**;
- 0 provider requests.

## 2. The game: HOU at OKC, 2025-10-21 (ESPN `401809243`)

### Mapping

Exact identifiers, unique in both directions:

| Codex identity | AISP |
|---|---|
| `nba-espn:game:401809243` | game `21f64105-f514-4360-a982-b65bdd590939` (scratch), via `external_id_mappings(game, NBA_ESPN, 401809243)`. Tipoff 2025-10-21T23:30Z, from the retained row. Source mode `RESEARCH_SHADOW` |
| `nba-neil:franchise:OKC` (home) | team "Oklahoma City Thunder": `NBA_NEIL_FRANCHISE` plus `NBA_ESPN_TEAM` `25` |
| `nba-neil:franchise:HOU` (away) | team "Houston Rockets": `NBA_NEIL_FRANCHISE` plus `NBA_ESPN_TEAM` `10` |

- The ESPN team ids were learned from the season-wide pairing, not guessed. Every OKC game carried
  ESPN team 25 and no other franchise did.
- The source's Eastern calendar date (2025-10-21) equals the assessment's `game_date`.
- `nba_games` / `nba_teams` hold SportsDataIO **FIXTURE** rows only, and none was mapped.

### Channels

Each channel is stored in its own columns and served under its own name.

| Channel | Home OKC | Away HOU | Units | Provenance |
|---|---|---|---|---|
| **Ridge expected points** (`ridge_a100_rest`, model `7bd9a0a2…`) | 116.969 | 108.453 | points, overtime included | assessment predictions |
| **Elo win probability** (model `b27a1a39…`) | 0.735343 | 0.264657 | fraction 0–1 | assessment predictions |
| **Joint-distribution probabilities** (lattice, 10,000 draws, cell digest `92a2ab66d7db007e…`, matching Model Studio's own) | 0.7294 | 0.2706 | fraction 0–1 | joint counts |

- Joint means: 116.8141 / 108.3384 points.
- Joint 80% intervals: total 201–249, home margin −9 to +26.
- Cross-check: the joint receipt's per-game Ridge means and Elo probability are **identical** to the
  assessment's.

### Outcome

The final is re-read from the retained object `b3884aa4…`, row ESPN 401809243:

- `STATUS_FINAL`, `completed` = true;
- **OKC 125, HOU 124**;
- 6 periods, so **overtime (2OT)**;
- ESPN team ids 25 / 10, equal to the admitted sides;
- tipoff equal to the admitted tipoff.

What AISP recorded:

- `game_state_observations` FINAL, source `ESPN_SPORTSDATAVERSE` (tier 2).
- `nba_final_observations`, checksum `f53b9410350ee64270f349890dd7e5596c0e0a95801a989fa2a0291558f1533c`.
- The observation time is the **acquisition** clock, 2026-09-14T12:52:46Z. The completion clock is
  `ABSENT_FROM_RETAINED_SOURCE_SCHEMA`, and `games.final_at` stays empty.

### Score (per channel; never averaged together)

| Channel | Log loss | Brier (2-way) | Winner | Home / away abs error | Margin / total error | CRPS total / margin | 80% total interval |
|---|---|---|---|---|---|---|---|
| Ridge points | n/a (not a probability) | n/a | n/a | 8.031 / 15.547 | +7.516 / −23.578 | n/a | n/a |
| Elo winner | **0.307419** | **0.070044** | correct | n/a (no points) | n/a | n/a | n/a |
| Joint | **0.315533** | **0.073224** | correct | 8.186 / 15.662 | +7.476 / −23.848 | **15.1703 / 4.8180** | 201–249 contains 249 |

**Reproduction.** Elo log loss and Brier equal Model Studio's `predictions.parquet` exactly. Joint log
loss, Brier and both CRPS values equal Model Studio's `scores.parquet` (paired) exactly. The database
refuses a probability on a Ridge row (`ck_nba_channel_eval_ridge_is_not_a_probability`) and points on an
Elo row.

### Whole season (1,225 games, HISTORICAL)

AISP's own scoring reproduces Model Studio's published figures:

| Channel | AISP | Model Studio's report |
|---|---|---|
| Elo | log loss **0.606258**, Brier **0.208971**, **832/1225** correct, AUC 0.7285 | 0.606258 / 0.208971 / 832/1225 |
| Ridge (ordinary-rest) | margin MAE **11.393**, total MAE **15.178** | 11.393 / 15.178 |
| Joint | total CRPS **10.8207**, margin CRPS **8.1643**; log loss 0.598537, Brier 0.205900, AUC 0.7348, 80% coverage total 81.6% / margin 78.5% | 10.8207 / 8.1643 (2025-26 row) |

AUC is AISP's own addition (the producer's report does not quote one); it is computed only for the two
probability channels and is null for Ridge, where it has no meaning.

54 of the 1,225 games went to overtime.

## 3. The selected production package (prospective, not yet scored)

- **Handoff:** `7113b71ff5819c89103ad19d5f9afb22c51dbc92980be12b56ce8826cb34ccaf`. Every member verifies
  and there are no identity problems.
- **Package:** `8a725a439af9f9ad4828281ff9bcdec5511c2af292c3feac4826b975dfa0d4c2`, PHI at NYK, ESPN
  `401909089`.
- **Byte-for-byte verification:**
  - `forecast.json` is `8c69bc25e64c969b0dfd2f0cfe3b3ec7af0f8699358368a44ab4289ba3f46282`;
  - the population artifact is `62b48824…`, logical HOME/AWAY `6d7b75fb…`;
  - **every** PMF, interval and illustrative total is recomputed from the 10,000 draws and matches.
- **Channels:**

  | Channel | NYK | PHI |
  |---|---|---|
  | Ridge (points) | 117.061 | 111.118 |
  | Elo | 0.677257 | 0.322743 |
  | Joint | 0.6819 | 0.3181 |

  Joint means are 117.2234 / 111.1279, and the joint total's 80% interval is 204–253.
- **Mapping:** admitted as a `RESEARCH_SHADOW` game at 2026-10-20T23:00Z. Orientation is **CONFIRMED**
  by the retained 2026-27 schedule object that the forecast itself names:
  - object `d4ffe0d9…`, whose row gives NYK = ESPN 18 and PHI = ESPN 20;
  - these equal the bindings learned from 2025-26.
- **Discovery:** V1 (`NBA_GEN1_PRODUCTION_DISCOVERY_V1`) carries no lease, so the package is
  `NO_LEASE_IN_SCHEMA`: displayed and scoreable, **never decision-eligible**.

## 4. Future checks (NOT YET DUE)

| Due | Check |
|---|---|
| 2026-10-01T00:00Z (Wed 09-30, 8 PM ET) | SGO billing month resets and the reserve becomes 400. The current acquisition authority expires; an NBA October allowance needs the owner (plan §2, O6). |
| 2026-10-13T18:00Z | Earliest legitimate Codex NBA capture, if the refresh proposal is approved and the publisher installed (O1). |
| 2026-10-13T19:00Z | First producer publication checkpoint (T−7d). First V2 slate discovery on **real** bytes; confirm the AISP V2 import and lease recording (so far tested on fixtures only). |
| 2026-10-20, 13:45–14:25 ET | 1-object SGO identity validation on BOS at DET (15:00 ET), if O6 is approved. Records the NBA league id, team ids and the older-leg lag. |
| 2026-10-20T18:30–18:45Z | First possible NBA paper checkpoint (BOS at DET, T−30..T−15). With shipped settings it records `DISCOVERY_NOT_CURRENT` or `NO_PAPER_AUTHORIZATION`, and no position. |
| 2026-10-20T23:00Z | PHI at NYK tipoff. A V1 package is never eligible; its checkpoint records `DISCOVERY_NOT_CURRENT`. |
| After ~2026-10-21T03:00Z | First 2026-27 final (PHI at NYK), delivered on the producer's source-confirmed finals lane (`finals-discovery/current.json`). AISP verifies the manifest and the per-game record, joins the exact game/franchises/tipoff, records `completedAtUtc` as the explicit unknown it is, and scores each channel. |
| ~2026-10-26 | Codex's first monitoring review point: 50 completed games (cumulative 52 by 10-26 ET). |
| ~2026-11-01/02 | Second review point: 100 completed games. |

## 5. Reproduce

1. Build a scratch SQLite database from the models.
2. Point the settings at the files above.
3. Run, in order:
   - `gen1_historical.import_historical`
   - `gen1_finals.ingest` (both lanes: `ingest_exchange` = the production
     source-confirmed finals lane, `ingest_retained` = historical reconstruction)
   - `gen1_production.consume` (consumer on, pin `7113b71f…`)
   - `gen1_evaluation.evaluate`

The whole season takes about 60 s. The one test that reads the real exchange,
`tests/test_nba_gen1_consumer.py::test_codex_selected_phi_at_nyk_package_verifies_byte_for_byte`, skips
when the files are absent.

## 6. The three contract corrections (2026-09-19 evening)

Model Studio's conformance review named three places where this consumer would not have matched its
completed contract. Each was changed in code and proved against **Codex's own bytes**, not against a
fixture of our shape.

### 6.1 A V2 package's effective clock is its own release's

`app/nba/gen1_package.py` now, for `NBA_GEN1_PRODUCTION_FORECAST_PACKAGE_INDEX_V2` only, verifies the
package's `forecast.bindings.slateRelease` physically (`path`, `sha256`, `bytes`), requires that release to
be `NBA_GEN1_SLATE_RELEASE_V1`, to name the pinned model handoff, to carry the same configuration identities
and draw count, and to be the same object the package index names — and then compares the package's
`prospectiveEffectiveAtUtc` to **that release's** clock. The pinned model handoff is still checked
independently, from the forecast's own `bindings.productionHandoff`. V1 packages are unchanged: they are
still compared with the handoff's clock.

| Object | Identity | Clock |
|---|---|---|
| Selected model handoff (unchanged) | `7113b71ff5819c89103ad19d5f9afb22c51dbc92980be12b56ce8826cb34ccaf` | `2026-09-18T01:16:08.176807Z` |
| Slate release staged 2026-09-19 | `9c5de22ce0cf9d366d7297202cccd4b18ba8738c2a8fccf1449cd8fdc17a4a0b` | `2026-09-19T20:26:29.236061Z` |
| Deployment / manifest it names | `05729a535e02409b1fca4b68c54e8ddde9b5a9006732d4d1d817f745061a8c88` / `eb636f3eb7c4414d5f91e777ce553205de5dd611feaad42136d13604416c46e5` | staged, not installed |

The release's `paperEligibilityAuthorization` is `null`, which is exactly why the paper lane reports
`NO_PAPER_AUTHORIZATION` (§6.3). **A newer release never re-dates an older preserved package**: the package
carries its own release binding, that binding is what is compared, and the stored row keeps both
(`nba_gen1_forecasts.slate_release_sha256`, `prospective_effective_at_utc`).

### 6.2 The source-confirmed finals lane is the production final path

`app/nba/gen1_final_exchange.py` + `app/nba/gen1_finals.py::ingest_exchange`. Verified end to end against
Model Studio's own shipped fixture lane
(`outputs/nba-finals-fixtures/0300382f6d834473a3faae3933e414df/fixture-exchange`):

| Object | SHA-256 |
|---|---|
| Discovery manifest (`NBA_GEN1_FINAL_DISCOVERY_V1`) | `4acfb73e378b4a606db4c8979cf4efbb56350803150b94e5fbbe925f5996256c` |
| Current record (`NBA_GEN1_SOURCE_CONFIRMED_FINAL_V1`, DET 112 – BOS 109) | `a38c637a70ee7f322b19081daefa3fec060fff4878deed2d15f95dad0e696e62` |
| Preserved earlier revision, membership false | `4e16756e4ded62d7c962e2736aedc5ee3cfcd5829bd61a670775d4c005958464` |
| Finals-exchange contract | `e5e070165dcd164faddf323bb30370ed6d465983d12884a23a76c1c6c5224ec1` |

Reading it admitted one final and retained one withdrawn revision as ineligible evidence
(`tests/test_nba_gen1_consumer.py::test_the_producers_own_finals_lane_verifies_and_admits`). What the lane
does and refuses:

- `completedAtUtc` is null in the record and stays null in AISP — `nba_final_observations.completed_at_utc`
  carries a `CHECK (completed_at_utc IS NULL)` so no code can promote a capture, validation or admission
  time into it. `games.final_at` also stays empty.
- Only `currentlySourceConfirmed` revisions are current membership. A correction keeps the old record
  (`SUPERSEDED_BY_CORRECTION`); a withdrawal keeps it as `WITHDRAWN_FROM_SOURCE_MEMBERSHIP`; a record the
  current manifest no longer lists becomes `ABSENT_FROM_CURRENT_DISCOVERY`. None of these is deleted.
- A changed tipoff or changed participants is **never settled automatically**: the record is retained with
  `reconciliation_required` and the reason, and the game is left alone.
- A record that claims a completion clock, a different completion basis, a tie, a non-final source status,
  out-of-order clocks, consumer settlement authority, or a franchise id that does not name its own code is
  refused individually — the rest of the manifest still imports.
- A game AISP does not hold by exact ESPN id is listed as unbound. It is never guessed into existence.
- Retained ESPN season exports remain a **separate** historical lane (`ingest_retained`), reported
  separately and never summed with the production lane.

### 6.3 The optional recurring paper grant

`app/nba/gen1_paper.py::policy_grant` now validates `NBA_RECURRING_PAPER_AUTHORIZATION_V1` and nothing else.
Proved against Codex's real grant document
(`outputs/nba-paper-fixtures/05437214250c4efba616e8b6a2e464db/grants/sha256/6c30bcf2…/receipt.json`, bound by
release `b1d1327c…`):

- inside its interval it is `GRANT_VALID` (`2026-10-13T18:00Z → 2027-04-19T04:00Z`, scope
  `ALL_SUPPORTED_2026_27_REGULAR_SEASON_FORECASTS`);
- **today it is `GRANT_NOT_CURRENT`** — a September clock never becomes fresh eligibility;
- without the release that binds it, it is `GRANT_NOT_BOUND_TO_A_VERIFIED_RELEASE`.

No grant is pinned in this branch, so the checkpoint blocks with `NO_PAPER_AUTHORIZATION`, evidence still
imports, and every gate before it still runs. The season-wide scope is **listed as a separate proposal**
(plan O4b) and is not applied: each position still needs the per-forecast
`ASP_NBA_PAPER_RESEARCH_ELIGIBILITY_V1` companion and a current discovery lease, so the 2026-09-15
single-game approval is not widened by anything here.

## 7. What a live import receipt will contain

Everything above was produced against synthetic fixtures and Codex's shipped fixture lanes on a scratch
SQLite database. **That is not a live import receipt**, and this document does not present it as one. When
the owner activates the consumer (plan O1/O2) the first real import will record, and AISP can then publish:

| Field | Source |
|---|---|
| Package index / forecast / population SHA-256 | `nba_gen1_forecasts.package_index_sha256`, `.forecast_sha256`, `.population_artifact_sha256` and `.population_logical_sha256` — each recomputed from the bytes at import |
| Slate release and model handoff | `.slate_release_sha256`, `.handoff_sha256`, with `prospective_effective_at_utc` from the bound release |
| Publication clock and its authority | `.exchange_published_utc` + `.publication_clock_source` (V2 = the bound receipt's `committedAtUtc`), `.discovery_receipt_sha256`, `.first_manifest_sha256` |
| Import job start and completion | the `ingestion_runs` row for `ingest-nba-source-confirmed-finals` / the exchange-consumer job: `started_at`, `completed_at`, `records_processed`, `schema_version` |
| Consumer deployment identity | the deployed commit and migration head (`20260919_0049`) recorded by the release runbook |
| Eligibility disposition, honestly | `nba_discovery_observations.lease_at_check` (`CURRENT` / `EXPIRED_LEASE` / `NO_LEASE_IN_SCHEMA`) and the checkpoint's recorded reason — today `NO_PAPER_AUTHORIZATION`, with no position |
| Finals lane | `nba_final_discovery_manifests.manifest_sha256` + per-record `final_record_sha256`, `revision_key`, `currently_source_confirmed`, and `completed_at_utc` null with basis `NOT_RETAINED_BY_ADMITTED_SOURCE` |

Until that import happens, the honest statement is the one this branch makes everywhere: the evidence
verifies, nothing is eligible, and no paper authority exists.

## 8. The acknowledgement AISP sends Model Studio once this is deployed

Send this verbatim, filling in only the deployed commit and the deploy time. It
claims nothing that has not happened.

> **AISP consumer conformance acknowledgement — NBA Gen1, 2026-09-19**
>
> AISP has implemented the three corrections from your conformance review and
> read your own bytes to prove them.
>
> 1. **V2 package effective clock.** A `…FORECAST_PACKAGE_INDEX_V2` package is now
>    dated by the slate release it binds in `forecast.bindings.slateRelease`,
>    verified by path, SHA-256 and byte count, and required to be the same object
>    the package index names. The pinned model handoff
>    `7113b71ff5819c89103ad19d5f9afb22c51dbc92980be12b56ce8826cb34ccaf` is checked
>    independently and is unchanged. V1 packages keep their handoff comparison. We
>    read release `9c5de22ce0cf9d366d7297202cccd4b18ba8738c2a8fccf1449cd8fdc17a4a0b`
>    and record its effective clock `2026-09-19T20:26:29.236061Z` as the clock a V2
>    package published under it must carry. No preserved older package is re-dated
>    by a newer release; each row keeps its own release SHA-256 and its own clock.
>
> 2. **Source-confirmed finals lane.** `finals-discovery/current.json` →
>    `NBA_GEN1_FINAL_DISCOVERY_V1` → `NBA_GEN1_SOURCE_CONFIRMED_FINAL_V1` is
>    implemented as the production final path, with the retained season exports
>    kept as a separate historical-reconstruction lane. We verified your shipped
>    fixture lane end to end in the AISP process: manifest
>    `4acfb73e378b4a606db4c8979cf4efbb56350803150b94e5fbbe925f5996256c`, current
>    record `a38c637a70ee7f322b19081daefa3fec060fff4878deed2d15f95dad0e696e62`
>    (DET 112 – BOS 109), preserved revision
>    `4e16756e4ded62d7c962e2736aedc5ee3cfcd5829bd61a670775d4c005958464` retained
>    with membership false, contract
>    `e5e070165dcd164faddf323bb30370ed6d465983d12884a23a76c1c6c5224ec1`.
>    `completedAtUtc` is null in our store and a database CHECK keeps it null;
>    `capturedAtUtc`, `sourceValidatedAtUtc` and `admittedAtUtc` are stored as
>    themselves and never substituted for completion. Withdrawn, corrected-away,
>    absent, identity-changed and rejected finals are retained as explicitly
>    ineligible evidence and reported for explicit reconciliation; none is settled
>    automatically. We join only exact game, franchise and tipoff identities, and a
>    game we do not hold is listed, never guessed.
>
> 3. **Recurring paper authorisation.** We accept only a release-bound
>    `NBA_RECURRING_PAPER_AUTHORIZATION_V1`, checked against your own required
>    fields, the unchanged policy terms and their prior supplement, the approval
>    interval, and the release that binds it. Your grant
>    `6c30bcf2f761545ce3cc3916deae60424f0d7e8c2a908be24739529860377aa5` validates
>    inside `2026-10-13T18:00Z → 2027-04-19T04:00Z` and reports `GRANT_NOT_CURRENT`
>    today. Today's release carries `paperEligibilityAuthorization: null`, so our
>    checkpoint reports `NO_PAPER_AUTHORIZATION`, writes no companions and imports
>    evidence as usual. We have not widened the 2026-09-15 single-game
>    authorisation: every position still requires the per-forecast
>    `ASP_NBA_PAPER_RESEARCH_ELIGIBILITY_V1` companion and a current lease, and a
>    season-scoped grant is recorded as a separate owner proposal.
>
> Unchanged and still true: `NO_LEASE_IN_SCHEMA` for V1 discovery; the joint
> 10,000-draw distribution is the controlling paper channel with Elo recorded
> separately and never blended; no September clock is treated as fresh
> eligibility.
>
> **Still not done, and we are not claiming it:** no real V2 forecast package
> exists yet, so V2 package import has been exercised only on fixtures shaped from
> your publisher source and on your release document. The first real verification
> is due at your first publication checkpoint, 2026-10-13T19:00Z. Nothing NBA is
> switched on in AISP: the consumer, the paper decisions and the price lane are
> all off, and no provider request has been made.
>
> Deployed: `<commit>` (migrations `20260919_0048`, `20260919_0049`) at `<time>`.
