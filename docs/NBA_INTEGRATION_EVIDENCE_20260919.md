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
| Elo | log loss **0.606258**, Brier **0.208971**, **832/1225** correct | 0.606258 / 0.208971 / 832/1225 |
| Ridge (ordinary-rest) | margin MAE **11.393**, total MAE **15.178** | 11.393 / 15.178 |
| Joint | total CRPS **10.8207**, margin CRPS **8.1643**; log loss 0.598537, Brier 0.205900, 80% coverage total 81.6% / margin 78.5% | 10.8207 / 8.1643 (2025-26 row) |

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
| 2026-10-20T18:30–18:45Z | First possible NBA paper checkpoint (BOS at DET, T−30..T−15). With shipped settings it records `DISCOVERY_NOT_CURRENT` or `NO_OWNER_POLICY_EXTENSION`, and no position. |
| 2026-10-20T23:00Z | PHI at NYK tipoff. A V1 package is never eligible; its checkpoint records `DISCOVERY_NOT_CURRENT`. |
| After ~2026-10-21T03:00Z | First 2026-27 final (PHI at NYK), **once a capture containing it is retained and pinned** (O7). Prospective per-channel scoring of package `8a725a43…` follows. |
| ~2026-10-26 | Codex's first monitoring review point: 50 completed games (cumulative 52 by 10-26 ET). |
| ~2026-11-01/02 | Second review point: 100 completed games. |

## 5. Reproduce

1. Build a scratch SQLite database from the models.
2. Point the settings at the files above.
3. Run, in order:
   - `gen1_historical.import_historical`
   - `gen1_finals.ingest`
   - `gen1_production.consume` (consumer on, pin `7113b71f…`)
   - `gen1_evaluation.evaluate`

The whole season takes about 60 s. The one test that reads the real exchange,
`tests/test_nba_gen1_consumer.py::test_codex_selected_phi_at_nyk_package_verifies_byte_for_byte`, skips
when the files are absent.
