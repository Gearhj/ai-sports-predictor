# SportsGameOdds budget reconciliation: September 2026

As of 2026-09-18 16:00 ET. Read-only, from the provider's own usage reading, AISP's request ledger, both budget guards, and the allocator's demand projection. The projection was built inside a rolled-back SAVEPOINT, so no snapshot was written.

## Provider, guards and commitments

| Item | Value | Source |
|---|---|---|
| Provider entitlement | 2,500 objects per UTC calendar month; the September bill ends 2026-10-01T00:00Z | SGO `/account/usage` (tier "amateur") |
| Provider used | **1,984** (reading taken 15:46 ET) | same |
| AISP ledger used | **1,984** from 482 SUCCEEDED rows, with 0 null counts | `provider_request_ledger` |
| Open reservations | **0**: every RESERVED row has a terminal row | ledger |
| Guard A (plan) | Reserve 300, the owner's schedule for 09-14 to 09-30. **216 spendable** | `provider_allocation.build_plan` |
| Guard B (ledger) | Reserve max(400, schedule) = 400. **116 spendable**. This guard binds. | `sgo_collection.object_budget` |
| Today (09-18) | MLB 3 of 3 spent and NFL 14 of 14 spent; 0 room left | today's snapshot |

September spend by lane:

| Lane | Objects |
|---|---|
| MLB pregame | 1,945 |
| NFL pregame | 15 |
| Opening adjustment | 24 |
| Account-usage reads | 0 |
| CFB | 0 |

## Remaining MLB and NFL demand (Sat 09-19 to Wed 09-30)

The horizon is the same as both guards': from now until the end of the billing month.

| Date | MLB | NFL checkpoint | NFL preview | Total | Cumulative | Guard B left | Guard A left |
|---|---|---|---|---|---|---|---|
| 09-19 Sat | 17 | 0 | 14 | 31 | 31 | 85 | 185 |
| 09-20 Sun | 17 | 14 | 1 | 32 | 63 | 53 | 153 |
| 09-21 Mon | 4 | 1 | 0 | 5 | 68 | 48 | 148 |
| 09-22 Tue | 19 | 0 | 0 | 19 | 87 | 29 | 129 |
| 09-23 Wed | 17 | 0 | 1 | 18 | 105 | 11 | 111 |
| 09-24 Thu | 14 | 1 | 0 | 15 | 120 | **−4** | 96 |
| 09-25 Fri | 17 | 0 | 0 | 17 | 137 | −21 | 79 |
| 09-26 Sat | 17 | 0 | 14 | 31 | 168 | −52 | 48 |
| 09-27 Sun | 17 | 14 | 1 | 32 | 200 | −84 | 16 |
| 09-28 Mon | 0 | 1 | 0 | 1 | 201 | −85 | 15 |
| 09-29 Tue | 5 | 0 | 0 | 5 | 206 | −90 | 10 |
| 09-30 Wed | 5 | 0 | 1 | 6 | 212 | **−96** | 4 |

- **MLB** needs 17 objects per 15 games: one checkpoint buy per game, plus refreshes where a pair's older leg would be stale by the decision. Recent fully funded days spent 16–19 objects.
- **NFL** needs one checkpoint object per game, plus one Show Prep preview per game on the day before (owner decision 2026-09-18).
- **Postseason:** 09-29 and 09-30 are the MLB Wild Card games, 4 per day.
- **CFB:** zero.

## Double-counting checks

None found.

- Provider usage equals the AISP ledger (1,984 = 1,984). No null counts are charged at 20.
- No reservation is open, and completed reservations are not counted twice: guard B counts only terminal rows, and in-flight is counted only for a RESERVED row with no terminal row.
- Each NFL game is counted once as a checkpoint on its own service day, and once as a preview on the day before. Those are two different purchases: the preview cannot serve the checkpoint, because the checkpoint's freshness limit is 240 minutes and the preview's is 18 hours.
- The 14 NFL objects bought overnight on 09-18 cannot stand in for Saturday's previews either. They will be about 40 hours old at the 16:30 freeze on Saturday.
- Today's remaining room (0) and the future days do not overlap.

## Result

- Under guard A (300 reserve), the month fits: 212 needed against 216 spendable.
- Under guard B (400 reserve), which enforces on every pregame grant, spending stops during **Thursday 09-24**.
- **96 planned objects cannot be funded.** Thursday is short 4, which falls on MLB and NFL in the same proportion.
  - MLB, about 61–64 objects: all of 09-25, 09-26 and 09-27 (the final regular-season weekend), plus the Wild Card games on 09-29 and 09-30.
  - NFL week 4, about 31–32 objects: the Saturday 09-26 previews, the Sunday 09-27 checkpoints, the Monday 09-28 checkpoint and the 09-30 preview for Thursday Oct 1.
- The gap is exactly the 100-object difference between the two guards' reserves, less guard A's 4 spare.

## Smallest practical adjustment (not applied; needs your decision)

**A. Budget-guard repair, phase 2.** Guard B would honour the owner's scheduled 300 reserve for 09-14 to 09-30, as guard A already does.
- Guard B then has 216 spendable and covers all 212, leaving 4 spare.
- It does not raise spending above the schedule you already approved: the month closes with at least 300 objects in reserve.
- It does release 100 objects of guard B's standing reserve, so it is your decision to make.

**B. Keep the 400 reserve and cut 96 objects of coverage.** This necessarily reduces MLB/NFL decision coverage. In least-damaging order:
- NFL Show Prep previews on 09-19 and 09-26: 28 objects. These are editorial, not decisions.
- MLB refresh buys at 1 object per game instead of 17 per 15: about 20 objects.
- MLB Wild Card games on 09-29 and 09-30: about 10 objects.
- That totals 58. The remaining 38 would have to come from NFL week-4 checkpoints or the last MLB regular-season days.

Recommendation: **A**, if you accept releasing guard B's extra 100 for the rest of September. Otherwise choose the cuts in B explicitly. Without a decision, the guards stop MLB/NFL pregame collection from 09-24.
