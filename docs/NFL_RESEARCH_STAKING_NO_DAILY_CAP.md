# Prepared: every qualifying NFL research pick gets the standard position

**PREPARED, NOT DEPLOYED.** Branch `claude/nfl-research-staking-no-daily-cap`,
commit `6737358`, based on the deployed `d7cde9d`. The deployed checkout is
untouched and still at `d7cde9d`.

## What it changes

The 2% aggregate daily ceiling no longer binds a research stake. Beginning with
the first slate after deployment, every pick that qualifies receives the
standard $5 research position.

## Why

On 2026-09-20 the ceiling was $20 against a $5 position cap. An eight-game slate
produced seven qualifying picks, all evaluated in one loop at 12:41:32, and the
daily room ran 20 → 15 → 10 → 5 → 0. The last three — NO@BAL, PHI@TEN, PIT@NE —
were declined with `STAKING_OR_EXPOSURE_CAP` / `bound_by: daily_cap`.

Two facts make this a defect rather than a policy working as intended:

1. **The survivors were chosen by spelling.** The evaluator's loop order is
   alphabetical by matchup (CAR, CIN, CLE, GB, MIN, NO, PHI, PIT), so the four
   that got money were the first four qualifying games in the alphabet.
2. **The three dropped held the largest Kelly fractions of the eight**
   (PIT@NE 46.0%, PHI@TEN 28.4%). By expected value the funded four would have
   been CLE, PHI, CAR, NO; instead two of the three lowest-EV picks were funded.

This is a virtual research ledger whose purpose is an unbiased estimate of how
qualifying picks perform. A ceiling that truncates the sample in alphabetical
order buys risk management a virtual bankroll does not need, and pays for it in
the estimate, which is the entire point of the ledger.

## What does NOT change

- the per-position cap — still the **penalised** 0.5% ($5), because no NFL
  evidence penalty has been calibrated;
- the same-game ceiling (1%, $10), still computed and still binding;
- quarter-Kelly, the 2.0-point minimum adjusted edge, and **every gate that
  decides whether a pick qualifies at all**;
- idempotency: a game that already carries a position never receives a second,
  so lifting the day's ceiling cannot stack exposure on one game;
- **nothing retroactive.** No position is created for any past game, and
  Sunday's three capped games stay exactly as recorded.

## The analytical fields

Both are recorded beside each decision and neither sizes anything:

| Field | Meaning |
|---|---|
| `recommended_kelly_stake` / `recommended_kelly_fraction` | the unconstrained quarter-Kelly size, so the sizing the model actually recommended survives even though the position is flat |
| `constrained_bankroll_portfolio` | what the pick **would** have received under the daily ceiling: `daily_cap_fraction`, `daily_room_before_this_pick`, `stake_under_daily_cap`, `would_be_declined`, and `basis: ANALYTICAL_ONLY_NOT_ENFORCED` |

`MAX_DAILY_PERCENTAGE` is retained at 0.02 precisely so the constrained
portfolio remains computable and comparable with the record built before the
change. `AGGREGATE_DAILY_CAP_BINDS = False` is the whole of the switch.

## Evidence

`tests/test_nfl_research_staking_no_daily_cap.py` — 9 tests on a six-game slate
($30 against the old $20 day):

- all six qualifying picks are funded at $5, `bound_by: position_cap`, and no
  `daily_cap` key appears in the binding set;
- the recorded constrained portfolio reproduces the old behaviour exactly — the
  first four funded, the fifth and sixth `would_be_declined`, $20 in total;
- the per-position cap still holds a $69.90 Kelly recommendation to $5;
- the same-game ceiling is still computed at $10 and still binding;
- a repeated tick on a funded game still yields exactly one position;
- **flipping `AGGREGATE_DAILY_CAP_BINDS` back to True restores the old
  truncation exactly** — 4 positions, $20, three `STAKING_OR_EXPOSURE_CAP`
  declines with `bound_by: daily_cap`.

Regression: **522 passed, 2 failed** across the NFL, paper, funnel, scorecard
and experiment suites. Both failures
(`test_experiment_season_accounts::test_the_summary_keeps_planned_allocation_out_of_funded_capital`
and `test_multi_sport_today::test_registry_contains_mlb_and_nfl_and_keeps_planned_sports_inactive`)
were confirmed to fail **identically on the unmodified base commit** with the
change stashed, so neither is caused by this work.

## Known limitation before deploying

The new analytical fields live in the `staking` dict, which reaches
`job_runs.result` but **not** `shadow_experiment_decisions`. That is defect
**D2** in the frozen review: `record_outcome` flushes the decision row before
staking is computed and never updates it, which is also why Sunday's capped
games persist as `QUALIFIED_BET / ALL_GATES_PASSED` while the receipt records
`DECLINED / STAKING_OR_EXPOSURE_CAP`.

So until D2 is repaired, `recommended_kelly_stake` and
`constrained_bankroll_portfolio` are retained only in job telemetry. If the
owner wants them durable on the decision record, D2 should be repaired first or
alongside — this change does not attempt it.

## Deployment considerations

- Server-only: no migration, no client change, so restart only.
- Deploy outside any open NFL or MLB decision window.
- Effect begins with the next slate evaluated after the restart. The next NFL
  slate is Monday 2026-09-21 (NYG@LAR, 20:15 ET); a full Sunday slate is
  2026-09-27.
- Budget note, not part of this change: funding more picks does not spend more
  provider objects. Prices are already purchased per game regardless of whether
  a position follows, so object consumption is unchanged.
