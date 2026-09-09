# NFL WOW + odds recovery — final report

Branch `claude/aisp-nfl-wow-and-odds-recovery-v1`, 7 commits on top of the merge of
Codex's foundation `dca3abe` with `claude/zero-cost-odds-ingestion-v1`.
46 files, +9,115 / −57 against the foundation.

---

## 1. Timely sportsbook price collection — the highest priority

**The objective is coverage, not collection.** A game either has a complete, same-book,
two-sided moneyline inside its own freshness window before its 15-minute cutoff, or it does
not — and when it does not there is exactly one operational reason.

`quote_coverage.py` derives that per event from persisted rows: six states
(SCHEDULED / QUOTE_EXPECTED / COMPLETE_QUOTE_CAPTURED / FINAL_FRESH_QUOTE_CAPTURED /
RECOVERY_REQUIRED / CUTOFF_MISSED) and one exact cause from ten codes, each with a
plain-language explanation. Run against history it is immediately diagnostic:

| slate | covered | causes |
|---|---|---|
| 2026-09-02 | 9 / 15 | 6 × QUOTE_CAPTURED_BUT_STALE_AT_CUTOFF |
| 2026-09-04 | 13 / 16 | 3 × stale |
| 2026-09-06 | 0 / 15 | 15 × QUOTA_GUARD_BLOCKED |
| 2026-09-09 (today) | 0 / 15 | 15 × QUOTA_GUARD_BLOCKED |

A pair assembled from two different observation times was never quoted together and is not
counted as a quote.

**Recovery ladder.** Coverage is re-checked 90 minutes out, a priority attempt is reserved at
45 and one final controlled attempt at 25, all ahead of the cutoff, and only for a game
actually diagnosed RECOVERY_REQUIRED. Recovery waits for the scheduled cover to have had its
chance — the first replay showed the 45-minute rung opening *before* the earliest game's own
checkpoint, which would have spent the protected reserve doing the scheduled request's job.

## 2. Quota protection — and the three reasons it was not real

Five lanes: MLB_REQUIRED 150, MLB_RECOVERY 100 (protected), NFL_RESEARCH 60,
OPTIONAL_MARKETS 90, OTHER_RESEARCH 0. An optional lane may spend only what is left **after**
every protected lane's unspent allocation. That refusal *is* the reserve.

Three defects meant it protected nothing. All are fixed:

1. **Enforcement was in the wrong place.** Lanes were checked in the new collector while three
   other jobs spent freely. Measured: **495 of September's 500 credits** were consumed by one
   ten-minute three-market poller (`asp-live-odds-refresh`, gated only on the API key) that
   consults no budget at all — 486 of them in four days, leaving five slate-days with no
   prices for any decision. Enforcement now sits in `_reserve_provider_request`, the single
   point every provider call passes through.
2. **The floor probe fell through.** On reaching the quota floor the guard reserved a bounded
   recovery probe to refresh a stale balance reading, then *returned normally* — letting the
   caller it had just refused spend its full three credits. It did exactly that on 2026-09-08
   (8 remaining → 5) and again on 2026-09-09, and would have run again against the last 5
   credits. It now raises after reserving the probe.
3. **Lane attribution was blind.** `request_shape` is written on the RESERVED row and no
   terminal row carried it, so every completed request looked shapeless and fell to the
   optional lane by default — 975 of 978 credits over 60 days, with MLB_REQUIRED reporting
   zero spent whatever it had spent. Terminal rows now copy the shape, and attribution reads
   the whole reservation group so older rows are still attributed correctly.

Provider reconciliation is not resolved in our favour: counted-more locks the optional lanes;
counted-less keeps our higher figure. Currently `PROVIDER_COUNTED_LESS`, delta −6.

## 3. Acceptance demonstration — all seven criteria, on two slates

`scripts/odds_acceptance_replay.py` drives the real planner, ladder and lane arithmetic with a
simulated five-minute clock over a real slate. **No provider is contacted**; only its responses
are injected, so missing, incomplete and failed cases are demonstrated deliberately.

Run on 2026-09-06 (widest span) and on 2026-08-28 — which the survey identified as the harder
case, because its cover degenerates into two singleton checkpoints bracketing a thirteen-game
cluster, AZ@SF's cutoff falls *exactly* on the last checkpoint's left window edge (zero slack),
and it crosses a UTC date boundary.

| criterion | result (both slates) |
|---|---|
| 1 historical day replayed | 15 games, staggered starts |
| 2 checkpoints cover every window | 15/15 on 3 requests, 3 credits |
| 3 recovery for missing / incomplete / failed | 15/15 each, 4 requests; unrecoverable named `PROVIDER_RETURNED_NO_EVENT` |
| 4 optional cannot exhaust the MLB reserve | MLB 15/15 with optional and NFL lanes at 0 |
| 5 idempotent persistence | 0 duplicate snapshot groups |
| 6 month fits in 500 | 336 of 500 projected |
| 7 experiment receives the quote in time | 10-minute margin to the freeze window |

Artifacts: `outputs/odds-credit-audit/acceptance-replay-2026-09-06.json`, `-2026-08-28.json`.

**No freshness requirement, decision threshold or quota floor was lowered.**

## 4. Operator view

`/admin` now leads with coverage, not spend: covered / at risk / cutoff missed / inside the
60-minute target, the lane table with the protected reserve, the reconciliation state, and a
per-game table naming the exact cause for every uncovered game. Screenshot 07.

---

## 5. Play-level evidence — verified, not accepted

The handoff verifies completely: 226 files (112 artifacts + 112 manifests + contract + handoff),
0 checksum or byte-count failures, and the handoff digest matches
`654b4a8b3850a040d237f66309e247a89de2fc276477995306c886a49142f8c0` exactly.

The producer ships its own `validationReceipt`. That is evidence it checked, not a reason not
to, so every contract invariant is re-derived from the plays: adjacent states must join exactly
across all ten required fields, play numbers contiguous, drive numbers nondecreasing, the clock
must reconcile to `outcome.seconds` with only PERIOD_END permitted to move the quarter, both
point increments must equal the score movement, every replay must finish FINAL on the score its
population member declares, every geometry point inside its frame with anonymity flags intact.
**All 112 pass.** Independently counted totals — 672 representative games, **108,466 plays** —
match the delivery exactly.

**The plays do not go in the database.** The artifacts are 316 MB and 92% of that is
`simulatedGames`. The row stores summaries, distributions, the 1,000-draw population, governance,
limitations and a light index of the six representatives — 4.1 MB for all 112 — and plays are read
from the immutable content-addressed file on demand. The address *is* the checksum, so the file
cannot drift, be swapped or be truncated without the read failing, and it fails with an exact
cause rather than an empty animation.

## 6. Play Theater

Every frame is a persisted play. The animation interpolates the ball *along* one play's supplied
path and never between plays. Verified live on a 158-play game:

- distinct paths per declared kind — pass air curve + separate YAC ground segment, rush, scramble,
  sack backwards, return reversing direction and colour, kickoff/punt/field-goal arcs;
- persistent trails accumulating within a drive (4 → 7 → 9 as the drive progresses);
- line of scrimmage and the yellow line to gain, which correctly vanishes at first-and-goal;
- scoreboard, possession, quarter, clock, down, distance and field position, all from the play's
  own state — clock ran 14:56 → 1:03 and the score 0–0 → 6–3 across 30 steps;
- scoring and turnover emphasis: pulsing marker, flash class (`nfl-broadcast--fg` / `--td` /
  `--turnover`), 11 scoring plays listed and clickable;
- Play/Pause, Prev/Next, Restart, speed (0.5–4×), scrubber to any play, trail toggle, filters
  (every play / scoring / turnovers / this drive), full-width mode, and `?play=N` deep links;
- reduced motion advances without animating and says so.

**5,393 of the 108,466 plays carry no ball path by construction** (PERIOD_END, PENALTY, SPIKE).
The ball is drawn at the spot rather than vanishing, which would have made one play in twenty
look like a rendering failure.

## 7. Scenario Comparison

The population **is** seed-paired across all seven scenarios (identical index→seed map in 16 of
16 events), so aggregate deltas are a common-random-numbers comparison and are reported as
like-for-like. The six published representatives are **not** — each scenario re-selects its own,
so two games sharing a label are usually different draws.

But a genuine pairing exists and the page finds it: simulation indices published by both
scenarios. Every event has at least one for every scenario. The banner then says *"These two
games are the same draw (simulation #0, seed 930947) run under both settings"* — and where no
pair exists it says *"two different draws, selected separately"* instead.

Verified: BASE vs Faster pace on the paired draw runs identical for 38 plays then diverges at 39;
at index 60 both show the same field position and score while the clocks read 5:14 and 7:56 —
which is exactly what a 10%-faster pace means. Delta table covers win/tie probabilities, overtime,
expected scores, margin, total, drives, plays, plays per drive and points per drive, with moved
rows highlighted, plus "What changed" and "Why it matters".

Note: the seed root 930947 is shared across all 16 events, structurally the same pattern as the
drive engine's 930731 — but it does **not** reproduce that defect. Cross-matchup identical finals
run 0–6 per 1,000 (chance), against 163–520 for the drive engine.

## 8. Outcome Explorer

The field was capped at 115px under a six-card grid next to a 360px heatmap — the smallest thing
on a page about it. It now opens the section at up to 46vh, carries the drawn or selected score,
and the heatmap rings whichever cell is on the scoreboard, so drawing and picking are visibly the
same act. A guided paragraph says the thing most easily misread: this engine did not play a game,
it produced a distribution, and the field shows one draw from it.

All ten distributions are selectable: M1, the two drive scenarios, and each of the seven
play-level sensitivities.

## 9. Model Performance

Executive-first. The first viewport answers all four questions in words: which model is retained
(M1_RECIPE_FOLD_REFIT), the best challenger (HIST_GRADIENT_BOOSTING_B_T2), whether the difference
was meaningful (**No** — the 95% interval crosses zero), and the market comparison (not comparable,
with the reason). Then the interval visualisation, a lower-is-better metric table with
plain-language hints, a raw/calibrated toggle, the full-season-only scope note, and the 24-pipeline
table behind a disclosure.

## 10. $1,000 Experiment funnel

Measured live, uncollapsed on the page, with one earliest blocking gate per lost game:

```
114  games in the experiment
112  the model produced a forecast        −2   no forecast
 48  a usable price was available         −42  no usable price, −22 price too old
 12  the edge cleared the threshold       −36  edge below the threshold
 12  decision qualified
 11  position created                     −1   execution defect
```

Reasons sum to 103 = 114 − 11, with no residual bucket. The claim the page exists to make, with
its arithmetic attached: *restoring prices makes more games **evaluable**, not necessarily more
games **qualifying*** — only 12 of 48 priced games (25%) cleared the bar, so the 64 unpriced ones
project to about 16 more qualifiers, not 64.

Thresholds unchanged: 2.0-point minimum adjusted edge, 15-minute cutoff. Ledger untouched — all
three experiments still show a 1000.00 starting bankroll and no position was created, edited or
removed.

---

## 11. Verification

- **Backend:** 22 new tests in `test_nfl_play_level_and_funnel.py`, all passing. Existing
  `test_odds_zero_cost_collection`, `test_nfl_labs_product`, `test_nfl_labs_v1_import`,
  `test_odds_refresh_cadence`, `test_labs_read_service` — 50/50 passing.
- **Frontend:** 20 new tests in `PlayTheater.test.tsx`, all passing. Full suite 540 tests,
  **43 failing — all 43 fail identically at the pre-work commit**, all in the MLB Today-page area
  (`TodayPage.opportunities` 23, `App.*` 11, `MultiSportTodayPage` 4, `phase15.integrity` 1,
  `sportExperience` 1). Measured by diffing failing test *names* against a baseline checkout, not
  file names. Zero net new failures.
- **Production build:** clean (`tsc -b && vite build`).
- **Browser walkthrough:** on a private stack (backend 8021, dev server 5179) with every
  provider-touching scheduler disabled. `provider_request_ledger` gained **0 rows**; the running
  8010/8011/8018/5173 services were not touched.
- **Overflow:** no page-level horizontal overflow at 375px on theater, scenarios, explorer,
  experiment or admin. The 24-row pipeline table scrolls inside its own `overflow-x:auto` wrapper.

**Three defects the walkthrough found that the tests did not:**

1. End zones were labelled with the wrong team — HOME attacks left, so the left end zone is the
   one AWAY defends. The touchdown capture showed the Chargers scoring in an end zone marked
   "Chargers".
2. Model Performance compared the champion's *calibrated* figures against the challenger's *raw*
   ones, reporting the challenger 0.0126 **worse** when the study's own paired result has it
   0.0032 **better**. The sign was wrong on the one page whose job is that comparison.
3. The seven play scenarios never reached the Outcome Explorer — the edit sat after a failed
   assertion in the same script and never ran.

## 12. Screenshots

`outputs/nfl-wow-walkthrough/`, all captured from the running application against the live
database:

1. `01-play-theater-touchdown.png` — 54-yard touchdown pass: curved air trajectory, catch and
   touchdown markers, trails, LOS and line to gain, scoreboard and full transport.
2. `02-play-theater-controls.png` — Pass-heavy scenario mid-game, legend, filters, play list.
3. `03-scenario-comparison.png` — delta table, seed-paired banner, divergence at play 39, two
   synchronized fields, score ribbon.
4. `04-outcome-explorer.png` — dominant field, drawn outcome, heatmap with the selected cell ringed.
5. `05-model-performance.png` — four executive answers, interval crossing zero, metric table.
6. `06-experiment-funnel.png` — the six stages with per-stage blocking reasons.
7. `07-operator-coverage.png` — quote coverage, credit lanes, per-game causes.

---

## Verdict

**`AISP_TIMELY_MLB_QUOTES_AND_NFL_WOW_READY`**

The replay proves complete cutoff coverage — 15/15 on both the widest and the hardest slate, on
3 requests and 3 credits, recovering through every injected fault and naming the exact cause of
the one that cannot be recovered — inside a month projected at 336 of 500 credits with the
250-credit protected reserve intact.

Two facts belong next to that verdict rather than under it:

- **The collector is still disabled.** `ODDS_CHECKPOINT_COLLECTION_ENABLED` is `false`. Activation
  is one operator setting, deliberately not flipped here.
- **This month's credits are already spent.** The provider reports 5 remaining against a floor of
  10, so every lane correctly refuses and today's slate reads 0 of 15 covered,
  `QUOTA_GUARD_BLOCKED`. That is the legacy poller's four-day burn, not a failure of the new
  mechanism — and the three fixes in §2 are what stop it recurring when the counter resets on
  1 October.
