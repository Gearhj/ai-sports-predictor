# RFF Core prospective capture — operations and rollback

Deployed 2026-09-17 · migration `20260917_0045` · branch
`claude/rff-prospective-capture-v1`

## What is running

A passive hook inside the existing `refresh-scoreboard` job, before its
`should_run_fast` gate. It makes no provider request, adds no scheduler, and
calls the deployed `RffService` unchanged. Each due `(game, offset)` produces at
most one row in `rff_reading_snapshots`, and that row is the authoritative
reading. A later recomputation is a diagnostic comparison, never a correction.

## Rollback — disable the hook, keep the snapshots

The snapshots already captured are authentic prospective evidence. Rollback
**never deletes them**; it stops new ones being taken.

**Step 1 — disable capture** (one line, `app/jobs/orchestrator.py`, in
`refresh_scoreboard`):

```python
# rff = capture_due_rff_snapshots(self.session, now=datetime.now(UTC))
rff = {"accepted": 0, "missed": 0, "failed": 0, "due": 0, "reason": "CAPTURE_DISABLED"}
```

**Step 2 — restart** the `ASP Backend 8010` scheduled task, outside an open
decision window. Nothing else is required: the hook is the only caller.

**Step 3 — do nothing to the table.** Leave `rff_reading_snapshots` in place and
leave migration `20260917_0045` applied. Nothing else in the system reads it, so
an idle table costs nothing, and dropping it would destroy readings that cannot
be recreated — the whole reason capture exists.

Full code rollback, if the commit itself must go:

```bash
git -C C:/Users/james/ai-sports-predictor revert --no-commit bff27a4 && git commit -m "revert: disable RFF capture"
```

Then restart the task. **Do not run `alembic downgrade`**: the downgrade drops
the table and with it every authentic snapshot. If the schema must truly be
removed, export the rows first.

Frontend rollback, if needed, is unrelated to capture: the deployed builds are
kept in `.runtime/frontend-rollback/` (`dist-c18abc8`, `dist-5be82f1`).

## Integrity properties

* No `UPDATE` or `DELETE` path exists for accepted snapshots, in the service or
  anywhere else.
* `UNIQUE(game_id, target_offset)` enforces one row per reading in the database,
  not only in the service.
* A capture failure is swallowed and reported in the job result; it cannot fail
  the scoreboard refresh or poison its transaction (each capture runs in its own
  `SAVEPOINT`).
* Offsets whose target instant precedes `ACTIVATED_AT_UTC`
  (2026-09-17 20:40:00Z) produce nothing at all — not a reading, not a receipt.
* A capture later than `MAX_CAPTURE_LAG_SECONDS` runs no calculation and leaves a
  `MISSED_CAPTURE_WINDOW` receipt that is never backfilled.

## Known limitation — the host cadence is slower than the limit

`MAX_CAPTURE_LAG_SECONDS` is 180, chosen for a job documented as running every
two minutes. Measured over three hours of live operation on 2026-09-17, the
actual spacing of `refresh-scoreboard` runs was:

| Statistic | Value |
| --- | ---: |
| Runs observed | 39 |
| Median gap | **240 s** |
| Maximum gap | **835 s** |
| Gaps exceeding 180 s | **24 of 38** |

The job is scheduled every two minutes but shares a single job thread with
every other owner and coalesces missed ticks, so the effective cadence is about
four minutes. At a 180-second limit most targets will therefore be refused with
a `MISSED_CAPTURE_WINDOW` receipt rather than captured.

That is fail-closed and safe — no false reading is ever recorded — but it will
starve the validation study, which needs 300 games with a publishable T-90m
reading. Two options, neither applied here because both change the admissibility
contract and belong to whoever owns the study:

1. **Raise the limit** to roughly 300–360 seconds, which covers the observed
   median and most of the tail while staying far inside the 30-minute spacing
   between registered offsets.
2. **Host the hook in a job with a reliable cadence**, or give the scoreboard job
   its own worker so its two-minute schedule is real.

Option 1 is one constant and a test; option 2 is a scheduler change. Until one
is chosen, expect a low accept rate and watch the ratio of `ACCEPTED` to
`MISSED_CAPTURE_WINDOW` rows.
