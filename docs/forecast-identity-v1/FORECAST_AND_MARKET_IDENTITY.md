# Which forecast, and which contract

Two questions the product used to answer differently on different pages, and
one of them it answered by guessing.

1. **Which model's number is this?** Four surfaces resolved that independently.
   The same COL @ DET game read 58.9% on the market board and 60.2% in the
   simulator, with nothing on either page saying they were different models.
2. **Which side is this price on?** The quoted side was inferred by comparing
   the midpoint against the home probability. That worked on the data in front
   of it and would have silently relabelled a contract the moment its price
   crossed 50%.

Both are now read from stored identity rather than derived at display time.

---

## 1. One selector, four surfaces

`server/app/services/forecast_selection.py` is the only place that decides
which forecast a game has. `/games`, `/markets`, the game analysis page and
`/games/:id/simulation` all call it.

The rules, and why each exists:

| Situation | Primary | What the reader sees |
|---|---|---|
| Game governed by the V18 era | V18 | the number, no caption |
| V18 published nothing | V18, no probability, `MISSING` | the condition, and any figure shown is labelled with the engine that produced it |
| An input changed after publication | V18 **with** the recorded reason, `INVALIDATED` | the forecast, plus why it is not decision-eligible |
| Published after first pitch | V18, `NOT_PREGAME` | "replay", never a pregame forecast |
| Game started before the era boundary | **Gen 1**, with V18 offered as `OUTSIDE_ERA` comparison | history is not restated |

Gen 1 is never substituted silently. Where it supplies a number (only when the
controlling engine published none), the payload carries
`forecast_selection.displayed_engine_id` naming it, and the chip reads
**"Gen 1 · substituted"**, not "V18".

An invalidated forecast keeps its own number rather than being swapped: a
limitation is a caption, not a reason to show a different model. That rule is
applied identically by the slate and the market board — an earlier version of
the board required `AVAILABLE` and so displayed Gen 1's 58.9% beside the
slate's V18 60.2% for the same game.

### Default model per surface, verified live 2026-09-12

| Surface | MLB (2026-09-12) | MLB (2026-09-11, pre-era) | NFL |
|---|---|---|---|
| `/games` | V18 on 14/15; Gen 1 named as substituted on SEA @ ATH | Gen 1, V18 as comparison | M1, unchanged — no selection payload |
| `/markets` | same 14/15; `aisp_comparison_engine_id` = `AISP_BASELINE` on SEA @ ATH | Gen 1 | M1, unchanged |
| Game analysis | V18 headline (COL 39.8% / 60.2% DET) | Gen 1 | M1 |
| `/games/:id/simulation` | V18 (60.2%) | Gen 1 track | M1 |

### One forecast identity, traced end to end

Game `b163682f-fbad-4ee0-86e9-771bc0449208` (COL @ DET, source event `824224`):

```
engine_id          MLB_V18_WHOLE_GAME
engine_version     mlb-v18-whole-game-a6e7e99122f1b60701a4728b
forecast_id        mlb-v18-daily-21239787e7773ffa160e98251cd999d427f1b9d99875f7f29b07d68f425c81f4
snapshot           PROSPECTIVE v1
source cutoff      2026-09-12 10:49 EDT   (newest evidence consumed)
published          2026-09-12 10:57 EDT   (producer publication)
recorded           2026-09-12 11:00 EDT   (AISP import)
home_win_prob      0.6024
availability       INVALIDATED
detail             "An input changed after this forecast was published: Material
                    lineup change after the current forecast: LINEUP_CONFIRMED.
                    It remains visible for the record and is not currently
                    decision-eligible."
```

Three clocks stay distinct and none of them is the page's refresh time. The
`INVALIDATED` reason is **read** from the recorded
`ShadowExperimentDecision.recommendation_reason`, not recomputed at display
time — a second opinion computed here could contradict the record.

### Cache keys

`forecastCacheScope({gameId, identity, horizon})` keys on game, engine,
forecast id, snapshot revision and horizon, so switching model or moving to a
different snapshot cannot serve the previous one's numbers. The simulation and
analysis pages pass `as_of` as the horizon, separating a current read from an
explicitly historical one for the same game.

Selection is a pure read. `test_selecting_a_forecast_writes_nothing` asserts
that decision and position counts are unchanged across a `select()` call; no
decision, position, NO_BET record or funded authority is created or overwritten
by choosing what to display.

---

## 2. Contract identity, not price inference

`client/src/services/marketOrientation.ts` no longer derives the side from
numbers. `PredictionMarketContract` already stored the venue's own outcome
label, market id, outcome id and question; the DTO now carries them
(`quoted_selection`, `quoted_outcome_label`, `quoted_market_id`,
`quoted_outcome_id`, `quoted_market_question`, `quoted_settlement_meaning`,
`quoted_side_source`).

`quoted_side_source` is `CONTRACT_METADATA` or `UNVERIFIED`. Without metadata
the UI shows **"Side unverified"** with the venue's native label — it does not
guess, and it never complements a price to manufacture the other side.

### Exactly which contract supplied each raw bid/ask in the screenshot

The DET-oriented column read 59.5% while the bid/ask beside it read 40.0/41.0.
Both were correct and described **different contracts**. Each venue lists both
sides separately:

```
POLYMARKET  market  0x923c5ee08d25025640b5acb89cfd94a8b179b6eed86574440642fbaff60c303d
            outcome 32400354811020468444989410749511250097085898452538330185839317606120110088418
            label   "Colorado Rockies"      YES settles if Colorado Rockies wins   0.40 / 0.41
KALSHI      market  KXMLBGAME-26SEP121310COLDET-COL
            outcome KXMLBGAME-26SEP121310COLDET-COL:YES
            label   "Colorado wins"         YES settles if Colorado wins           0.40 / 0.41
```

The DET-side contracts (`…-DET`, Polymarket token `527481423…`) quote
0.59 / 0.60. The drawer now states this in place of the earlier ambiguity: the
win column is oriented to DET, the bid/ask are the venue's own contract prices,
and the side comes from the contract's identity, so **it does not change when a
price crosses 50%**. Prices are still compared against the model probability,
but only as a consistency *warning* (0.15 tolerance) that reports a
contradiction rather than overriding identity.

NFL tie outcomes are untouched — no two-outcome reinterpretation was
introduced.

---

## 3. Simulator view availability

The disabled mode controls now say one plain thing:

> This forecast does not include this view. Nothing is reconstructed in its place.

The field names moved one disclosure below the controls
("Technical detail: unavailable views"), naming the exact lineage source:

```
available_views.geometry   = false, derived from lineage.geometry.events
available_views.population = false, derived from lineage.populationGeometry.summary
```

Availability is still derived from the artifacts themselves, not from a flag
asserted upstream.

### Remaining Codex dependency — re-verified 2026-09-12, unchanged

```
forecast    mlb-v18-daily-21239787e7773ffa160e98251cd999d427f1b9d99875f7f29b07d68f425c81f4
lineage     availableViews = {"broadcast": true, "geometry": false, "population": false}
supplements bound to this forecast: 0
research_supplements: 266 rows, all NFL
            (PLAY_LEVEL_SIMULATION 217, DRIVE_SIMULATION 32,
             JOINT_SCORE_SIMULATION 16, MODEL_BENCHMARK_STUDY 1)
exporter    still emits V18_EXCHANGE_VIEW_SUPPLEMENT_EXPORT_NOT_SUPPORTED
```

Needed from the producer: a `PLAY_LEVEL_SIMULATION`-shaped geometry supplement
and a population-geometry supplement, bound to that forecast id through the
existing `ResearchSupplement` contract. AISP needs no further change to render
them — the modes enable themselves when the artifacts arrive. Nothing was
synthesised from score marginals, no estimator runs in AISP, and no Studio code
was modified.

---

## 4. Verification

**Backend** — `server/tests/test_forecast_selection.py`, 10 tests: the
controlling engine leads; a pre-era game keeps its own forecast; a
post-first-pitch publication is a replay; a changed input invalidates without
replacing; a missing forecast is reported, not silently swapped; the newest
snapshot wins; both clocks are stored values and never the read time; the
blocking reason reads as a sentence (the stored reason ends `).`, and stripping
bracket and stop in a fixed order always left one behind); a reason with no
parenthetical is kept whole; and selection writes nothing.

**Frontend** — 12 `forecastSelection` tests (cache separation across model,
revision, horizon and game; attribution to the engine that supplied the
number) and 13 `marketOrientation` tests (side from metadata for home/away;
stability across a price crossing 50%; "Side unverified" with no metadata;
no complementing; consistency warning on a genuine contradiction).
`GameSimulationPage.tracks` 16 tests pass, including the reworded mode
controls and the technical disclosure.

Full client suite: 728 tests, **43 failures — the documented pre-existing
baseline, zero regressions.** All of them are in files this task did not touch
(`App.*` 18, `TodayPage.opportunities` 23, `sportExperience` 1, and the
`phase15` typography guard's 5 long-standing 10.5px declarations). One backend
failure in
`test_market_pulse_canonical_coverage.py` is the known local-midnight bucketing
flake — confirmed by re-running it with this task's changes stashed, where it
fails identically.

**Deployment** — restarted through `scripts/asp-backend-supervisor.ps1` under
the "ASP Backend 8010" scheduled task. A readiness probe fired immediately
after `Start-ScheduledTask` is answered by the *old* instance until the new
supervisor reclaims port 8010, so each restart was confirmed against the
supervisor log's own "reclaimed port" and "scheduler instance … claimed" lines
and the listener pid, not against `/readyz` alone. After the restart: `/readyz`
ready,
database reachable, scheduler running, **one listener on 8010, one supervisor
process, one scheduler owner** (`.runtime/scheduler-owner`, claimed in the
supervisor log). MLB jobs continued through the restart — a V18 forecast for
TEX @ AZ published four minutes after redeploy, moving that row from `MISSING`
to `AVAILABLE` on its own. NFL M1 is unchanged: 16 games on the Week 1 board,
60 labs games, and no selection payload on the NFL path.

---

## 5. Known, unchanged, reported

The game analysis page's research-shadow panel still describes V18 as having
"no wagering authority" while the simulator reports the V18 era controls funded
decisions. That is an authority description, not a forecast selection, and this
task was scoped not to change model authority — left as found and flagged here.

The simulator's "Simulation Population" *mode* is unavailable while an
"ALL SIMULATIONS" statistics panel renders on the same page. These are two
different artifacts with similar names: the mode needs
`lineage.populationGeometry.summary`, the panel comes from the forecast itself.
The technical disclosure now says so explicitly; renaming the mode was out of
scope.
