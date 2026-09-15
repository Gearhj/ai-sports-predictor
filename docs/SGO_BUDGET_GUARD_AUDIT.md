# SportsGameOdds budget-guard audit and repair recommendation

Status: **read-only audit, 2026-09-15. Nothing changed. The repair below is a
recommendation that needs James's approval before it is implemented.**

AISP enforces SportsGameOdds (SGO) spend with two independent guards that
answer the same question — "may this request spend objects?" — using
different arithmetic.

| | Guard A: allocation plan | Guard B: local object budget |
|---|---|---|
| Code | `app/services/provider_allocation.py` `request_objects()` → `build_plan()` → `daily_snapshot()` | `app/services/sgo_collection.py` `object_budget()` → `ObjectBudget.may_spend()` |
| Balance source | Provider's own usage reading (`sgo_account_usage` ledger row, ~hourly) plus AISP spend since that reading | AISP's ledger only |
| Window | **UTC calendar month**; replenishes on the 1st | **Rolling 30 days** |
| Reserve | **Scheduled**: `reserve_for(service_date)` → 300 for 2026-09-14..30, 400 otherwise | **Fixed** `OBJECT_RESERVE = 400` |
| Output | A daily allowance per lane, fixed once per Eastern service day | Total objects still spendable in the window |
| Values on 2026-09-15 | cap 2,500 · used 1,926 · reserve 300 · spendable 274 · 17/day · MLB lane 17 | used 1,933 · reserve 400 · **spendable 167–174** |

## 1. Which guard authorizes or blocks each collection path

| Lane / path | Guard A | Guard B | Effective limit today |
|---|---|---|---|
| `sgo_mlb_events` — MLB pregame (`sgo_collection.fetch_fallback`) | enforced (grant after B) | **enforced first** (`budget.may_spend(wanted)` → `OBJECT_BUDGET_EXHAUSTED`) | the tighter of both; **B binds** (167 left vs A's 274) |
| `sgo_nfl_events` — NFL pregame (`sgo_nfl_pregame`) | enforced first | enforced (`OBJECT_RESERVE_GUARD`) | both; B binds |
| `sgo_nfl_live` — NFL in-game (`scoreboard/nfl_live`) | enforced | **not enforced** — `object_budget()` is passed to `record_request` only for accounting | A only |
| `sgo_nfl_periods` — final periods (`scoreboard/collect`) | enforced | **not enforced** (accounting only) | A only |
| `sgo_account_usage` — balance read | not a spend (0 objects) | not a spend | none; zero-object row |
| The Odds API lanes (`odds_collection_service`) | separate credit budget, not SGO | — | out of scope |
| Show Preview day-before collection | **does not exist** — `app/show_preview/collection.py` refuses by name | — | zero |

So the two lanes that feed funded decisions (MLB and NFL pregame) are governed
by the stricter, misaligned guard, while the in-game lanes are governed only by
the plan.

## 2. Why Guard B does not align with the provider reset

1. **Window.** SGO bills a UTC calendar month and refills on the 1st. Guard B
   counts the trailing 30 days, so objects spent in September keep counting
   against October until each ages out 30 days later.
2. **Reserve.** The owner-approved September reserve reduction (400 → 300) was
   applied to Guard A's schedule only. Guard B still holds 400 back, so the
   100 objects James released for September price coverage are unreachable by
   the two pregame lanes.

Today the two windows happen to cover the same rows (the ledger begins
2026-09-09), so the entire current difference is the reserve: 274 vs 167–174.

## 3. What happens on October 1

Assume September ends with Guard B's ceiling reached (2,100 objects in the
window; it will be reached before Guard A's, around Sept 25 at ~17/day).

* **Provider:** the balance refills to 2,500 at 2026-10-01 00:00 UTC
  (2026-09-30 8:00 PM ET).
* **Guard A:** the reserve returns to 400 on the Oct 1 Eastern service day. But
  the day's allocation is fixed at the first read after 00:00 ET, and
  `_provider_balance` takes the **latest** usage reading without checking its
  month. If that reading was taken before 00:00 UTC it still reports
  September's usage, so `daily_total` computes to 0 and every lane pauses for
  all of Oct 1, even though the provider has refilled. This self-corrects on
  Oct 2 at the earliest, and only if a post-reset reading exists before that
  day's snapshot.
* **Guard B:** still sees about 2,100 objects in its rolling window, so it
  refuses MLB and NFL pregame until September rows age out. Using the retained
  ledger, the first releases are +25 objects on Oct 9, +330 on Oct 10 and
  +1,245 on Oct 11. **MLB postseason and NFL pregame collection would be
  blocked from roughly Oct 1 through Oct 9–10** while the provider balance
  sits at ~2,500 unused.

## 4. Smallest safe repair

Keep two guards: Guard B is a useful independent check against a planner bug.
Make them measure the same thing.

1. **Guard B window → the provider's UTC calendar month.** In
   `object_budget()`, replace `since = moment - 30 days` with the start of the
   current UTC month. Keep counting from AISP's own ledger (still independent of
   the provider reading), keep charging unknown counts at
   `MAX_EVENTS_PER_FETCH`, and keep the opening adjustment.
2. **Guard B reserve → `max(DEFAULT_OBJECT_RESERVE, reserve_for(service_date))`
   in phase 1**, so September is not loosened (see §6). A later phase 2, with
   James's explicit approval, may use `reserve_for()` exactly and so honor the
   September 300.
3. **Guard A month check.** In `_provider_balance`, ignore a usage reading whose
   `requested_at` falls in an earlier UTC month than `moment`; fall back to
   `LOCAL_LEDGER_COUNT` for the new month, which already exists and already adds
   a warning. Optionally trigger one usage refresh when the first grant of a
   month finds no current-month reading. That is a zero-object account read.

No schema change, no provider plan change, no reserve reduction, no new job.

## 5. Migration and rollback

* **Migration:** code-only. No table or column changes. `ProviderAllocationSnapshot`
  rows already written for a day are kept; the change applies from the next
  service day's snapshot. Deploy by restarting the single supervised backend.
* **Rollback:** revert the commit and restart. Both guards are recomputed on
  every read, so there is no persisted state to unwind. A snapshot written
  under the new rule for the current day stays valid under the old rule
  because it only records a fixed allowance.
* **Pre-deploy check:** run both guards read-only against production and
  confirm Guard B's September spendable is unchanged (phase 1).

## 6. Could the repair increase September spending?

* **Phase 1 (window + `max(400, scheduled)` reserve + month check): no.** For
  September the calendar-month window and the rolling window contain the same
  rows (ledger starts Sept 9), and the reserve stays 400 for Guard B. Guard A is
  untouched in September because the month check only rejects readings from a
  prior month. September spendable stays 167–174.
* **Phase 2 (Guard B uses the scheduled reserve):** yes, by at most **100
  objects** through Sept 30, which is exactly the reduction James already
  approved for Guard A. Guard A's 17/day allowance still caps actual daily
  spend, so the realized increase is bounded by the remaining days × 17 and by
  the 100-object reserve difference.

## 7. Tests the repair needs

1. `object_budget` counts only rows from the current UTC month: a row on the
   last day of the prior month is excluded one second after 00:00 UTC.
2. Unknown record counts are still charged `MAX_EVENTS_PER_FETCH`; the opening
   adjustment is still counted in its month.
3. Phase 1 reserve is `max(400, reserve_for(day))`: 400 on 2026-09-20, 400 on
   2026-10-05; phase 2 is `reserve_for(day)`: 300 on 2026-09-20.
4. September equivalence: with the retained ledger, Guard B's spendable on
   2026-09-15 is identical before and after phase 1.
5. October 1: a September usage reading is ignored by `_provider_balance` on
   2026-10-01; the balance source is `LOCAL_LEDGER_COUNT`; `daily_total` is
   non-zero when the local October spend is below cap minus reserve.
6. The Sept 30 service day, which spans the 00:00 UTC reset, keeps its
   September snapshot (conservative) and does not double-count.
7. MLB pregame and NFL pregame on 2026-10-02 are granted when both guards agree,
   and refused by name when either refuses.
8. NFL live and periods lanes remain governed only by Guard A. This is a
   regression pin, not a behavior change.
9. No path writes a reservation when either guard refuses.

## 8. Recommendation

Approve **phase 1** before 2026-09-30 so the pregame lanes are not dark for
the first ten days of October. Decide **phase 2** separately; it is the only
part that changes September spending. Neither phase enables Show Preview
day-before collection, which remains unimplemented and switched off.
