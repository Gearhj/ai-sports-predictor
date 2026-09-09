# MLB operational selection audit

Read-only. Commit `eae5c197`. Machine-readable detail: `audit.json`.

Period: **2026-08-28 → 2026-09-08** (12 slate dates, 169 games, 20,679 decision rows in
`shadow_experiment_decisions`). Current slate reported separately.

## 1. Which engine decides

**Gen 1**, exclusively. All 20,245 decision rows with a forecast resolve to the Gen 1 ensemble
`asp_mlb_ensemble 2.0.0-gate2-08cca634c81b` through `shadow_experiment_decisions.forecast_id →
forecasts`. All 11 official positions carry `AISP_PRACTICAL_PREGAME_POLICY_V2_1`.

## 2. Is V18 evaluated

**No — display only.** 14 MLB V18 shadow forecasts exist (13 imported for 2026-09-08). Zero produce
a `shadow_board_decisions` row and zero are linked to a position. The only shadow-board rows in the
database belong to `NFL_M1_XGBOOST`. V18 cannot currently affect an MLB selection.

## 3. Where games stop (first binding gate, one row per game)

| Stage | Historical 08-28→09-07 (154 games) | Current slate 09-08 (15 games) |
|---|---|---|
| No valid forecast | 2 | 0 |
| Forecast after cutoff | 0 | 0 |
| No complete two-sided quote | 24 | **15** |
| Stale quote | 25 | 0 |
| Readiness below 70 | 46 | 0 (not reached) |
| Starter not confirmed | 9 | 0 (not reached) |
| Lineup not confirmed | 0 (never first) | 0 (not reached) |
| Edge / EV below threshold | 36 | 0 (not reached) |
| Exposure / risk restriction | 0 | 0 |
| Duplicate or closed decision | 0 | 0 |
| Reached QUALIFIED_BET on the final row | 12 | 0 |

Counting *every* gate a game failed at its final phase (historical): quote 24, stale 25,
readiness 49, starter 58, lineup 48, edge/EV 70, other operational 58. Starter and lineup fail
often but are rarely the *first* stop — the market and readiness gates fire earlier.

## 4. Readiness components (newest forecast per game)

Threshold **70.0**; publish floor **55.0**. A component is a hard stop only where that game's
forecast marks it `critical`.

| Component | Weight | Hist. ready % (152) | 09-08 ready % (15) | Gate type |
|---|---|---|---|---|
| team_stats | 15 | 100.0 | 100.0 | hard stop (critical) |
| schedule | 10 | 98.7 | 100.0 | hard stop (critical) |
| probable_pitchers | 10 | 99.3 | 93.3 | hard stop (critical) |
| roster | 5 | 100.0 | 100.0 | score only |
| sportsbook_count | 5 | 81.6 | **0.0** | score only |
| odds | 10 | 58.6 | **0.0** | score only |
| lineups | 10 | 19.1 | 0.0 | critical for 7/152 hist, none on 09-08 |
| weather | 10 | 3.3 | 26.7 | critical for 7/152 hist, none on 09-08 |
| pitcher_confirmation | 5 | 0.0 | 0.0 | critical for 7/152 hist, none on 09-08 |
| feature_completeness | 10 | 0.0 | 0.0 | hard stop (critical) — permanently `stale` |
| bullpen / statcast | 5 / 5 | 0.0 | 0.0 | score only — never ingested |

Three components (`feature_completeness`, `bullpen`, `statcast`) are **never ready in any game**,
costing 20 weight points before any live evidence is considered. That is why current forecasts top
out at 57.5 against a 70 threshold: even a perfect market day cannot clear readiness today.

## 5. Quotes

* Current slate: **missing, not stale**. 0 of 15 games have a MONEYLINE market row at all; 0 of
  1,991 decision rows carry an `odds_snapshot_id`.
* Most recent quote anywhere: **2026-09-07 20:44:23 ET**. Latest MLB market row created the same
  minute. 2026-09-06 and 2026-09-08 have zero snapshots; 2026-09-07 covered 3 of 11 games.
* Books ingested (9): DraftKings, FanDuel, Bovada, BetRivers, BetMGM, MyBookie.ag, BetUS,
  BetOnline.ag, LowVig.ag. Books actually priced into decisions are dominated by LowVig.ag (3,161
  rows) and BetOnline.ag (2,222).
* Origin: **disabled polling + exhausted quota + stopped ingestion**, not normalization and not
  policy logic. `ODDS_POLLING_ENABLED=false`; the last successful call returned
  `quota_remaining=5` against `ODDS_QUOTA_FLOOR=10`, so the quota guard refuses further requests.
  Rows that do have quotes normalize and price correctly.

## 6. UI vs persisted evidence

1. **"15 decisions still under review" / WATCHING** — persisted evidence records all 15 as
   `NO_BET` with no two-sided quote; `opportunity_summary` returns `no_bet=15, watch=0`. The client
   groups them as WATCHING until the final-pregame cutoff. Wording only, but it implies a pending
   evaluation that cannot resolve while ingestion is stopped.
2. **Replayed funnel marks all history stale** — re-running the funnel for a past date shows
   `fresh_quote=0` even for dates that produced positions, because freshness is re-anchored to the
   replay time. Historical stage counts here therefore come from persisted rows, not a replay.
3. **`complete_forecasts=15` / data health 53** — all 15 forecasts exist but score 40.0–57.5, and
   10 of 15 are DRAFT (below the 55.0 publish floor). "Complete" does not mean decision-ready.

## 7. Why there are no selections

**Operational configuration first, market data second.** The proximate cause is that no MLB odds
have been ingested since 2026-09-07 20:44 ET, so no 2026-09-08 game has a market to price; the
upstream cause is disabled polling plus a quota balance below its own floor. Nothing reaches the
readiness, edge or exposure gates today.

If quotes were restored unchanged, the **readiness gate would immediately become the binding
constraint** (all 15 games at 40.0–57.5 vs 70.0, held down by three components that are never
ingested), followed by edge/EV, which was the first blocker for 36 of 154 historical games.
Forecast availability is not a material constraint (2 of 169 games).

**For Codex:** tuning edge or Kelly parameters changes nothing on the current slate — those gates
are never reached. The ordered leverage is (1) odds ingestion/quota configuration, (2) the readiness
composite and its three permanently-missing components, (3) edge/EV thresholds.

Verdict: `AISP_MLB_OPERATIONAL_SELECTION_AUDIT_READY_FOR_CODEX`
