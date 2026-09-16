# MLB Show Preview: what AISP holds against the producer's input request

Status: **read-only inventory, 2026-09-15. Nothing was exported, unsealed,
created or requested. No external call was made and no provider object was
spent.**

Request inspected:
`C:\Users\james\asp-model-studio\outputs\mlb-show-preview-feasibility-v1\CURRENT_INPUT_REQUEST.json`
(schema `MLB_SHOW_PREVIEW_MINIMUM_CURRENT_INPUT_REQUEST_V1`, status
`NOT_EXECUTED_NO_SOURCE_SCOPE_EXPANSION`), with the feature contract in
`FEATURE_CONTRACT.json` (`MLB_TEAM_SHOW_PREVIEW_FEATURE_CONTRACT_V1`) and the
producer's own statement of the gap in `CLAUDE_HANDOFF.md`.

Target: **MIL at PIT**, MLBAM gamePk `823334`, 2026-09-17 16:35 UTC
(12:35 EDT), show deadline 2026-09-16 20:00 UTC.

## How each row is classified

| Class | Meaning |
|---|---|
| **AVAILABLE_ADMISSIBLE** | AISP holds it and it may be used as a model input for this model. |
| **AVAILABLE_NOT_ADMISSIBLE** | AISP holds it, but using it here would break a rule: it is outside the feature contract, it is sportsbook data, or its provenance does not meet what the request asks for. |
| **MISSING** | AISP does not hold it. Not "hard to get" — absent. |
| **PROTECTED** | A sealed or outcome-evaluation store. Not opened for this inventory and not proposed as an input. |

## The inventory

| # | Requested input | Class | What AISP actually holds |
|---|---|---|---|
| 1 | Canonical / provider / team mapping | **AVAILABLE_ADMISSIBLE** | `external_id_mappings` holds `MLB_STATS_API` team ids for all 30 clubs, including **158 = MIL** and **134 = PIT**, and 2,743 MLB game mappings. |
| 2 | Confirmed schedule capture for the target game | **AVAILABLE_ADMISSIBLE** | Canonical game `1d21e240-55c5-4649-b6f2-df88cab29f5f`, mapped to `MLB_STATS_API` external id **823334**, PIT home / MIL away, `starts_at` 2026-09-17 12:35 EDT = **16:35 UTC**, status `SCHEDULED`, row captured 2026-09-15 01:03 EDT. Every field the request names agrees. |
| 3 | Prior-season (2025) team records for MIL and PIT | **AVAILABLE_ADMISSIBLE**, with the caveat in row 4 | `team_game_history_observations` holds **162 rows for MIL and 162 for PIT** across 2025-03-27 … 2025-09-28, quality tier `A`, classification `RECONSTRUCTABLE_POINT_IN_TIME`, every row carrying `eligible_at`. 4,856 such rows cover all 30 clubs for the full 2025 season. |
| 4 | Original source / capture / revision clocks, certified | **AVAILABLE_NOT_ADMISSIBLE** | The clocks exist but say what they are: every history row carries `availability_basis = RETROSPECTIVE_DERIVED_LAST_EVENT_COMPLETION_PROXY` and `temporal_semantics = RETROSPECTIVE_RECONSTRUCTION`. Revisions are tracked (`revision` 1–3 across 198 checkpoints, one source version). This is a **derived completion proxy, not the original publication clock**, so it cannot satisfy a request for certified original source, capture and revision vintages. The producer reaches the same conclusion independently: "historical source revision/publication vintages remain uncertified." |
| 5 | Full earlier-league totals to reconstruct the frozen prior (2019–2024) | **MISSING** | AISP's earliest MLB game row of any kind is **2025-03-18**. There is no 2019–2024 MLB history in AISP at all. The producer states this history is retained on the Model Studio side; AISP is not a source for it. |
| 6 | **Complete 2026 MIL and PIT results through 2026-09-15** | **MISSING — this is the binding gap** | AISP's 2026 MLB coverage starts **2026-08-13** (earliest scheduled row) and **2026-08-27** (earliest FINAL row), against a season that opened 2026-02-28. For the two target clubs AISP holds **16 completed MIL games (2026-08-27 … 09-13)** and **15 completed PIT games (2026-08-28 … 09-13)**. Point-in-time history rows for 2026 are narrower still: **9 PIT and 5 MIL rows, 2026-09-09 … 09-13**. |
| 7 | `last60` ordering and current-season counts | **MISSING** | Derived from row 6, so it inherits row 6's gap. A last-60 window cannot be formed from ~16 games, and a current-season count computed over a partial season is not a smaller number — it is a wrong one. |
| 8 | Completion dates preserved for suspended games | **MISSING** | There is nothing to preserve: no completed 2026 game outside the August 27 – September 13 window exists in AISP to carry a suspension date. For the window AISP does hold, completion is a derived proxy (row 4). |
| 9 | History completeness certification | **MISSING** | AISP cannot certify it. Its 2026 MLB holdings are 247 FINAL, 14 LIVE and 52 SCHEDULED games concentrated in August (59 FINAL) and September (188 FINAL) — roughly one month of a season that began in February. A completeness certificate over that is a claim AISP has no evidence for. |
| 10 | 2025 outcome payload / sealed evaluation evidence | **PROTECTED** | `game_final_outcomes` holds 2,430 rows for 2025 and 95 for 2026. It is the training-label and outcome store. **No row payload was opened for this inventory** — only row counts and date bounds were read. It is not proposed as a model input, and no exception to open it is requested. |
| 11 | Starters, lineups, injuries | **AVAILABLE_NOT_ADMISSIBLE** | AISP holds 45,536 `starter_observations`, 29,392 `lineup_observations` and 16,333 `roster_availability_observations`. The feature contract explicitly excludes them ("No starters, lineups, injuries, Statcast or sportsbook inputs required"), and the model's own card records starter and lineup status as `UNKNOWN`. Feeding them in would produce a different model than the one assessed. |
| 12 | Statcast | **MISSING** (and not required) | `statcast_player_observations` is empty (0 rows). It is outside the contract in any case. |
| 13 | Sportsbook inputs | **AVAILABLE_NOT_ADMISSIBLE** | AISP holds 255,620 `odds_snapshots`. They are market evidence, not model inputs. Reinterpreting a price as a feature would turn a model-versus-market comparison into a circle, which is the one thing the Show Board exists to keep honest. |

## What this means

1. **Every identity input the producer needs, AISP already has and can supply
   admissibly.** The target game, its MLBAM id, its two clubs, their provider
   ids and its start time all agree with the request as written.

2. **The 2026 season history is the blocker, and it is a real absence rather
   than an access problem.** AISP's own MLB coverage begins in mid-August 2026.
   The producer's cached receipts cover 2026-08-14 … 09-14. Neither side holds
   the season, which is why the feature contract's `recent60` window cannot be
   formed by either of them. No permission grant fixes this; the data is not
   somewhere AISP is failing to look.

3. **Nothing here should be filled in by inference.** A 60-game window built
   from 16 games, or a prior-season figure substituted for a current-season one,
   would produce a forecast that looks complete and is not. The feature
   contract's own rule for the current-season gap says as much: "unavailable
   unless owner certifies complete source coverage."

4. **The protected store stays closed.** `game_final_outcomes` is where 2025
   outcomes live in AISP, and no part of this inventory needed to open it. If a
   future determination makes an outcome export admissible as a model input,
   that is an owner decision about source scope, recorded before it is used —
   not something this inventory grants.

5. **Consumer-side state is unchanged.** MLB rows on the Show Board still read
   "Preview forecast awaiting producer," which is the accurate statement while
   the above holds. See `docs/SHOW_PREVIEW_MLB_DISCLOSURES.md` for what a
   genuine MLB package must disclose when one does arrive.

## Provenance of this inventory

Every figure above came from one read-only pass over the production database
(`games`, `game_teams`, `teams`, `leagues`, `seasons`, `external_id_mappings`,
`team_game_history_observations`, `odds_snapshots`, `starter_observations`,
`lineup_observations`, `roster_availability_observations`,
`statcast_player_observations`) plus row counts and date bounds — and nothing
else — from `game_final_outcomes`. No table was written. No file was exported.
No external service was contacted. No SportsGameOdds object and no Odds API
credit was consumed.
