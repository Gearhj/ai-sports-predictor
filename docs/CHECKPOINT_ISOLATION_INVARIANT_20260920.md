# Checkpoint isolation: the missing-input recovery invariant

Verified 2026-09-20, before the deployment of the `decision-checkpoints` lane.

## The invariant the owner asked to confirm

> A transient first tick with no eligible forecast or no usable price must not
> permanently prevent a later in-window evaluation after that evidence arrives.

Stated as the six steps it has to survive: a checkpoint fires early in the
window; the forecast or the two-sided quote is not there yet; the checkpoint
records why and stakes nothing; the evidence arrives; a later tick inside the
same window reconsiders the game; and the decision that stands is the one made
on the complete evidence — with no backdating and no second position.

## Result: three of four sports already satisfied it. NFL did not.

| Sport | Before this release | Basis |
|---|---|---|
| MLB | **Satisfied** | The freeze owner re-decides on each tick until the cutoff; the position is taken from the latest decision at or before the cutoff (`shadow_experiment_service.py:861-868`), and a second position is refused (`:884`, UNIQUE at `entities.py:3301`) |
| CFB | **Satisfied** | `PREREQUISITE_REASONS` (`app/cfb/paper_decision.py:61`) |
| NBA | **Satisfied** | `PREREQUISITE_REASONS` (`app/nba/gen1_paper.py:67`) |
| NFL | **NOT satisfied** | `_superseded_permission_block` (`nfl_paper_decision.py:246`) admitted a replacement for two producer-permission reasons only |

The NFL consequence was specific: a first tick that recorded `NO_FORECAST`
(:444), `NO_TWO_SIDED_QUOTE` (:464), `NO_PERMITTED_PROVIDER` (:481) or
`QUOTE_TOO_STALE` (:496) **pinned that answer for the rest of the window**. The
forecast or the price could arrive two minutes later and be ignored until
kickoff.

This defect is **pre-existing and independent of the lane** — it has been in the
NFL owner since the policy was written. But the lane makes an early tick more
likely, because a checkpoint that used to wait behind a long core job now fires
on time, which is precisely when the evidence is least likely to have landed.
Shipping the lane without the repair would convert a latent defect into a live
one on an NFL Sunday.

## The repair

`_supersedes_transient_state` (renamed from `_superseded_permission_block`)
admits a replacement only when all three hold:

1. the stored reason names a **missing prerequisite** (`PREREQUISITE_REASONS`),
2. **nothing was staked** on the stored decision, and
3. the new evaluation reaches a **different** answer.

Deliberately excluded, so that a real evaluation is never re-opened:
`NO_QUALIFYING_VALUE`, `ALL_GATES_PASSED`, `STAKING_OR_EXPOSURE_CAP` and
`ELIGIBILITY_RECORD_GRANTS_EXECUTION`. A game that was genuinely evaluated and
declined on the merits stays declined. A game that already carries a position is
never reconsidered.

Nothing else moves: the T−30..T−15 window, the price and eligibility gates, the
thresholds, the staking rule and the exposure caps are untouched, and no
timestamp is backdated.

## Evidence

`tests/test_checkpoint_missing_input_recovery.py` — 14 tests, parametrised over
both `NO_VALID_CURRENT_FORECAST` and `NO_TWO_SIDED_QUOTE`, walking the full six
steps including the negative cases. **4 fail before the repair; all pass after.**

Combined focused run on the integrated release: **92 passed, 0 failed**
(`test_checkpoint_missing_input_recovery`, `test_decision_checkpoint_lane`,
`test_mlb_owner_day_exception`, `test_nfl_paper_decision`, `test_job_lanes`,
`test_sgo_nfl_pregame`, `test_provider_allocation`). Wider sweeps: 233 passed
across the MLB shadow-experiment, V18 pilot and forecast-selection suites; 122
across the checkpoint and paper suites.

## Honest limits

- MLB's satisfaction of the invariant is established **by reading the code and
  its UNIQUE constraint**, not by an end-to-end test written for this purpose.
- A recovered NFL game now writes **more than one `FINAL_PREGAME` row**. Every
  reader was checked and none uses `.one()`, but any surface that *counts*
  checkpoint decisions should be eyeballed after the first real recovery — a
  count is the kind of thing a passing test suite can still miss.
