# Making eligible V18 forecasts scorable — what was repaired

**Branch** `claude/aisp-v18-and-price-coverage-v1`
**HEAD at start** `a357435` (no commit made; changes are in the working tree)
**Repaired and put into service** 2026-09-11, 22:42–22:49 ET

---

## The defect, and why the fix is not a new final store

The V18 settlement path gated on `game_final_outcomes`. That table holds 2430
rows spanning 2025-03-18 to 2025-09-28, every one
`RETROSPECTIVE_RECONSTRUCTION`, written only by
`app/historical/persistence.py`. It is the **training-label** store. Nothing in
the 2026 live path writes it, so the gate could never open.

The obvious repair — materialise live finals into that table — is the **largest**
option available, not the smallest, and the brief's own constraints rule it out:

- The table carries `CheckConstraint("temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'")`.
  Writing a live-observed final there would require either a migration relaxing
  that constraint or labelling live evidence as a retrospective reconstruction —
  exactly the conflation the brief forbids.
- `app/training/dataset.py` reads `game_final_outcomes` **without** filtering
  `temporal_semantics`. New rows there are precisely the "existing historical
  consumers misinterpret new records" hazard.
- `source_version_id` and `checkpoint_id` are non-null foreign keys to
  `historical_source_versions` and `history_backfill_checkpoints`. A live final
  has no backfill checkpoint; inventing one would be fabricated provenance.

Meanwhile the required evidence **already existed**, with better provenance
semantics than that table offers. `game_state_observations` carries, per game:
status, both scores, the source, the observation time, the knowledge time
(`available_as_of`), a source record id and a payload checksum. And
`app/services/forecast_evaluation.py` — the NFL research scorer — already reads
exactly that shape, already refuses anything weaker, and already handles
correction and supersession.

So the repair **adapted the existing scorer** rather than building a second
final store. One scorer, two leagues.

---

## What changed

Four files, 354 insertions.

| File | Change |
|---|---|
| `server/app/services/result_provenance.py` | Registered `MLB_STATS_API` at tier 2 with its own note, and let a source override the generic tier note. |
| `server/app/services/forecast_evaluation.py` | Added `LeagueProfile` + `LEAGUE_PROFILES`; resolve sport/league/scoring-version from the game instead of module constants; score published **marginals** when no joint distribution exists; record a publication-clock classification on every score; bound `evaluate_league` by start floor, protected ids and forecast presence. |
| `server/app/jobs/orchestrator.py` | New job `evaluate-mlb-v18-research-forecasts` with the V18 bounds as module constants. |
| `server/app/jobs/scheduler.py` | Registered `asp-mlb-v18-research-evaluate` on the existing supervised scheduler, 30-minute interval. |
| `server/tests/test_mlb_v18_prospective_scoring.py` | New — 15 focused tests. |
| `server/scripts/v18_error_analysis_export.py` | Gate moved to the observation path; `researchScore` and `cohort` added; schema `V2`. |
| `server/scripts/v18_export_reconcile.py` | Five new checks covering scores, duplicates and cohort partitioning. |

### The provenance decision worth flagging

`MLB_STATS_API` is tier **2**, not tier 1. It is the league's own wire —
stronger than the tier-2 note's "mirror, not the league's own wire" description —
but tier 1 is documented as the governed odds provider, the only source that may
reach a priced decision. Promoting MLB's API there would let something claim
"provider-verified" for a source with no wagering governance behind it. It keeps
tier 2 and carries its own accurate note instead.

### The energy score is deliberately absent

V18 publishes marginal run distributions, not a joint score distribution. CRPS on
each marginal is exact and is computed. The energy score is a joint statistic;
producing one by multiplying the marginals would assume the two sides are
independent, which is the assumption a whole-game simulator exists to avoid. It
stays null with that reason on the row.

---

## Before and after

| Measure | Before | After |
|---|---:|---:|
| Eligible finals for the V18 population (gated) | **0** | **25** |
| Distinct V18 games with a persisted research score | **0** | **25** |
| Research evaluations in the database (all leagues) | 2 | 27 |
| Scheduled job invoking the scoring path | none | `asp-mlb-v18-research-evaluate`, every 30 min |

"Before" for eligible finals is 0 against `game_final_outcomes`, which was the
only gate that existed. Against the observation gate the evidence was already
there — it had simply never been read.

### Cohorts

| Cohort | Games |
|---|---:|
| `VERIFIED_PROSPECTIVE` | **11** |
| `QUALIFIED_DIAGNOSTIC` | **14** |
| `NOT_YET_SCORED` | 6 |

**The seventeen finished games and the verified-clock forecasts were not the same
set, and assuming they were would have been wrong.** At the point the repair
began, 17 games had finished — and 14 of those 17 had collapsed publication
clocks. Only **3** were both finished and independently clock-verified. The
verified count rose to 11 only because tonight's slate, which publishes with
distinct clocks, finished during the work.

### The fourteen unresolved clocks

Inspected through their stored immutable receipts only; no publication history
was reconstructed. What the receipts do and do not establish:

- `inputReceipts.captureMode` is
  `PERSISTED_AISP_EVIDENCE_SELECT_ONLY_NO_PROVIDER_CALLS`, with
  `outcomesRead: false`, `marketDataRead: false`, `providerCalls: 0`,
  `aispRowsModified: 0`, plus a content-addressed `pregameSnapshotSha256`. That
  is real evidence the snapshot was pregame and outcome-free.
- But `pregameSnapshotCapturedAt`, `evidenceCutoff` and `available_as_of_utc` are
  **the same value**. Nothing independently records when the forecast was
  *published*, as distinct from when its evidence was cut.

So: outcome-leakage is well evidenced against; publication timing is not
evidenced either way. They are scored — the scorer's rule (published before first
pitch) is unchanged and was **not** loosened — and they are held in the qualified
diagnostic cohort rather than counted as verified prospective evidence.

### Withheld

No game was withheld for missing provenance. Six of the 31 are `NOT_YET_SCORED`
because they had no eligible final observation at export time: games still in
progress or not yet started on the 2026-09-11 slate. They score themselves on the
next scheduled run after they finish.

---

## A concrete forecast → final → score

**gamePk 823817** — LAD at MIA, 2026-09-11 19:10 ET.

```
forecast   mlb-v18-daily-0f60c4030bc8ab611102669b4e9ce3b4c28b34c4344b7ffe1febdf4969b1c99f
           shadow_forecast_id a6a6d877-d689-41ca-9072-a3457c454f49
           engine MLB_V18_WHOLE_GAME, artifact 44ec4f45…88d71c8b, 5000 simulated games
           published 2026-09-11 18:52:48 ET, evidence cutoff 18:44:00 ET  -> clocks distinct
           publication clock: VERIFIED_INDEPENDENT_PUBLICATION

final      observation MLB_STATS_API, tier 2 (automated, not provider-verified)
           result_identity MLB_STATS_API:b7b821c9da59d5ba24aa4f10251a5937ab6c21eb668814d9f918f5ec052ba221
           MIA 2, LAD 6

score      evaluation is_current, scoring_version AISP_MLB_V18_PROSPECTIVE_SCORING_V1
           predicted AWAY  actual AWAY   winner_correct true
           probability of actual 0.644   log loss 0.4401   Brier 0.2535
           predicted margin -1.881   actual margin -4   margin error +2.119
           CRPS home 0.804  away 0.909  margin 1.465  total 1.037
           energy score null (marginals published, not a joint)
```

The score names the forecast, and it names the exact observation whose checksum
produced the label. If that observation is ever corrected, the checksum changes,
a new evaluation is written, and this one is retained and marked superseded.

---

## Evidence the scheduler is really doing this

The supervised backend was restarted through the supported path at 22:45 —
`Stop-ScheduledTask` then `Start-ScheduledTask` on `ASP Backend 8010`. The
supervisor's own ownership claim reclaimed port 8010 from the orphaned uvicorn
child (old PID 49176 retired, new listener PID 50024 at 22:45:27), so job
ownership was preserved and no second scheduler was created. The restart was
78 minutes clear of the 00:00 ET model-era boundary and its capital capture, and
the V18 publisher runs under its own separate task and was untouched.

First real scheduled invocation, from `job_runs`:

```
evaluate-mlb-v18-research-forecasts
  2026-09-11 22:48:31.508 -> 22:48:33.467   SUCCEEDED
  written 8   reused 17   superseded 0   final_games 25   skipped 0
  verified_publication_clock 11   qualified_publication_clock 14
  provider_calls 0   models_executed 0
  positions_created 0   official_decisions_created 0
  model_era_activated false   bankroll_mutated false
```

**`written 8, reused 17` is the duplicate-free retry, demonstrated by the real
scheduler rather than asserted.** The 17 games scored manually minutes earlier
were recognised and left alone; only the 8 that had become final since were
written.

Second scheduled invocation, on its normal 30-minute cadence — the legitimate
no-new-work result:

```
evaluate-mlb-v18-research-forecasts
  2026-09-11 23:18:31 -> 23:18:33   SUCCEEDED
  written 0   reused 25   superseded 0   final_games 25   skipped 0
  verified_publication_clock 11   qualified_publication_clock 14
  provider_calls 0   models_executed 0
  positions_created 0   official_decisions_created 0
  model_era_activated false   bankroll_mutated false
```

Nothing new had finished in that half hour, so the job wrote nothing and said so.
Confirmed against the database immediately afterwards:

```
research_forecast_evaluations, league MLB
  total rows 25   current rows 25   distinct games 25
  forecast identities carrying two current rows: 0
```

Twenty-five rows, twenty-five current, twenty-five distinct games — the counts
coincide because no correction or provenance upgrade has landed yet. The
duplicate check is the one that matters: no forecast identity has two current
evaluations, across two scheduled runs and one manual run over the same
population.

---

## Focused verification

`server/tests/test_mlb_v18_prospective_scoring.py` — **15 tests, all passing**.
Scoped to the risks the repair actually changed:

- *Insufficient evidence is refused.* A `live` observation with scores, a bare
  `games.status = FINAL` with scores and no observation behind it, and a FINAL
  observation with null scores are each refused with
  `NO_AUTHORITATIVE_FINAL` — the bare flag does not bypass the rule.
- *Idempotent retry.* Two runs, one row. Two stored snapshots of one game, one
  score.
- *Correction preserves earlier evidence.* A corrected final writes a second
  evaluation, keeps the first, links it via `superseded_by_id` and labels it
  `RESULT_CORRECTED`. A better-provenanced source confirming the same score
  labels the old one `PROVENANCE_UPGRADE` and does **not** add a scored game.
- *Publication clock.* Distinct clocks score as verified; collapsed clocks score
  and are marked unresolved — the eligibility rule is asserted unchanged.
- *Bounds before outcomes.* A protected gamePk and a game before the floor are
  both absent from the population, not filtered afterwards.
- *No side effects.* Scoring creates no decision and no position.

Regression check on the paths the change touches:
`test_nfl_prospective_scoring.py` and `test_mlb_v18_research_shadow.py` pass
unchanged. Unrelated suites were not run.

---

## Remaining operational notes

1. **`test_fable51_scheduler_reliability.py::test_scheduler_registration_unchanged_when_no_catch_up`
   fails, and it was already failing before this work.** Its expected job-id set
   is stale by four jobs that predate this change
   (`asp-model-era-boundary`, `asp-model-exchange-consumer`,
   `asp-mlb-event-bridge`, `asp-shadow-threshold-ladder`); verified by stashing
   this change and re-running. The new `asp-mlb-v18-research-evaluate` makes it a
   fifth. Fixing the assertion is someone's cleanup, not part of this repair.

2. **`app/shadow_import/mlb_v18_settlement.py` is untouched and still blocked.**
   It writes a settlement stamp into `shadow_forecasts.lineage_detail` and still
   gates on `game_final_outcomes`, so it still finds nothing. It was deliberately
   not used and not changed: it is a settlement stamp, not a research score, and
   repairing it would have meant writing to the training-label store. Anyone
   reaching for it should use the scoring path instead.

3. **Nothing was committed or pushed.** The changes sit in the working tree on
   `claude/aisp-v18-and-price-coverage-v1`. The pre-existing untracked files were
   preserved.

4. **No model activation, no settlement, no bankroll movement, no policy change.**
   The V18 model era is still `ARMED` with `execution_allowed_in_aisp` false.
   Positions, decisions and active experiments are at the same counts as before
   the work, and each scheduled run records that on its own result row.
