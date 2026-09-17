# RFF evidence coverage and comparability repair

Branch `claude/rff-evidence-coverage-repair-v1` · commit `c18abc8` · 2026-09-17

RFF is and remains a **research display**. It does not touch a forecast, a
threshold, a decision, a stake, a position, a settlement or a bankroll. Nothing
in this repair changed a raw observation: the collectors, the tape and the
stored evidence are untouched. Only the arithmetic over that evidence, the
policy for publishing it, and the way it is presented changed.

---

## 1. The defect

On the 2026-09-17 board, four games displayed **RFF 100 · Maximum unrest** while
only **8%** of the measure had any data behind it, and the same board showed
SD @ COL at **RFF 41** with **50%** coverage. A reader comparing those two rows
would conclude the 8%-coverage games were the disturbed ones. The opposite was
true: they were the games AISP knew almost nothing about.

### Root cause

`RffService._score` renormalised the composite by the weight that happened to be
available:

```python
# before
weighted = sum(w_i * s_i for available components)
covered  = sum(w_i       for available components)
point    = weighted / covered          # <- the defect
```

Dividing by `covered` rescales whatever was observed up to the full 0–100 range.
One component — information uncertainty, nominal weight **0.08** — scoring 100
became `8.0 / 0.08 = 100`, i.e. the entire index, and then earned the top
category from a single input. Absence of evidence was being rendered as an
extreme observation.

### Second-order cause

The one component that *was* available was itself measuring the wrong thing.
`_information_uncertainty` scored AISP's own collection gaps as game
uncertainty. For PHI @ NYM it summed:

* 30 points because no lineup rows existed for either side,
* 30 points because forecast readiness was below target,
* 40 points for genuinely unconfirmed starters,

= 100. Two of those three terms are facts about the AISP pipeline, not about the
game. So the pipeline's own silence was amplified by renormalisation into
"Maximum unrest".

---

## 2. The repair

### 2.1 Fixed-weight evidence accounting

Weights are fixed and always sum to 1. An unavailable component contributes
**nothing** — it is never zero, never imputed, and never renormalised away. What
it does is widen the interval:

```
knownContribution = Σ w_i · s_i   over available components      (0..100)
coverage          = Σ w_i         over available components      (0..1)
unknownWeight     = 1 - coverage
lowerBound        = knownContribution                  (unobserved weight at 0)
upperBound        = knownContribution + unknownWeight · 100   (unobserved at 100)
intervalWidth     = unknownWeight · 100
```

Consequences that are now pinned by tests:

* one 0.08 component at 100 ⇒ known **8.0**, range **8–100**, no score;
* one 0.08 component at 0 ⇒ known **0.0**, range **0–92** — a low reading on a
  sliver of evidence is not proof of calm either;
* the gap between the bounds is *exactly* the unobserved weight;
* contribution shares are **share of the whole measure** (`share_of_measure`),
  so they cannot total 1 on partial evidence. The old `share_of_score` field,
  which always totalled 1, is gone.

The old renormalised figure survives only as
`diagnostics.observedComponentAverage`, explicitly labelled "not RFF", so the
two numbers can never be confused in an API consumer.

### 2.2 Fail-closed publication policy

| Coverage | Status | What is shown | Ranked? |
| --- | --- | --- | --- |
| `< 50%` | `INSUFFICIENT_EVIDENCE` | known contribution, interval, what was observed, what is missing | no |
| `50–69%` | `PROVISIONAL_WIDE_RANGE` | known contribution and interval, no number, no category | no |
| `≥ 70%` | `RFF_PUBLISHED` | a score — **only** with ≥ 4 components from ≥ 2 evidence families | yes, against other published games only |

Blocker codes returned per game: `COVERAGE_BELOW_PUBLICATION_THRESHOLD`,
`TOO_FEW_COMPONENTS`, `TOO_FEW_EVIDENCE_FAMILIES`.

A published score **is** the known contribution — not a rescaled mean. At 100%
coverage the two coincide; below it the composite counts the unobserved weight
as unknown rather than as agreement.

### 2.3 Categories require an identified band

A band is assigned only when all three hold: the game is published, the interval
is ≤ 25 points wide, and **both ends of the interval fall in the same band**.
Otherwise the label is `CATEGORY_UNCERTAIN` ("Category uncertain") with a reason
string, e.g. *"the evidence spans Curious to Spicy"*. "Maximum unrest" is
therefore unreachable from one component or from thin coverage; it now requires
the whole interval to sit above 75.

### 2.4 Evidence families and correlation

Coverage is not diversity. Two components in one family measure nearly the same
phenomenon, so publication requires at least two families:

| Family | Components |
| --- | --- |
| Public positioning | Public concentration, Sportsbook-relief proxy |
| Price movement | Reverse line movement, Late movement |
| Cross-book structure | Cross-book disagreement |
| Model versus market | Model–market disagreement |
| Game-information uncertainty | Information uncertainty |
| Narrative / social | Social / narrative signal |

Declared overlaps, exposed in the payload as `correlated_components` so a reader
never reads one phenomenon as two independent confirmations:

* **RLM ↔ Late movement** — both read the same consensus price series over
  different windows; one large move can raise both.
* **Sportsbook relief → Public concentration** — relief is a function of public
  concentration; without handle share there is nothing to relieve, and it is
  never derived from price alone.

### 2.5 Information uncertainty now measures the game, not the pipeline

Rewritten so it scores **only the evidence AISP actually holds**:

* before `start − 6h` → unavailable, `NOT_YET_OBSERVABLE`;
* fewer than two sides with any observation → unavailable,
  `INCOMPLETE_EVIDENCE` (a missing side is a collection gap, not uncertainty);
* newest observation older than 360 minutes → unavailable, `STALE_EVIDENCE`;
* otherwise the score is `unresolved kinds ÷ observed kinds` per side, averaged
  across the two sides.

`forecast_readiness_score`, `lineup_sides_observed` and `starter_sides_observed`
are reported **beside** the component as pipeline completeness and are never
added to it.

### 2.6 Freshness

Market components require a frame within **240 minutes**; information evidence
within **360 minutes**. A stale component is unavailable with reason
`STALE_EVIDENCE` and **buys no coverage** — it cannot help a game over the
publication threshold.

---

## 3. Component / source / availability matrix

Weights are nominal and fixed; they do not change with availability.

| Component | Weight | Family | Source and owner | Grain | Required observation time | Freshness rule | Availability today | Reason |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- |
| Public concentration | 0.20 | Public positioning | **None.** Would need a public betting-percentage feed: ticket count **and** handle share, by side, per book | per book × side × instant | continuous pregame; must be paired with the price at the same instant | n/a | **Unavailable, structurally** | `NO_SOURCE` |
| Sportsbook-relief proxy | 0.12 | Public positioning | Derived from public concentration | per book × side | same as above | n/a | **Unavailable, structurally** | `DEPENDS_ON_UNAVAILABLE` |
| Reverse line movement | 0.18 | Price movement | `MarketTapeService` frames (Odds API multi-book quotes already collected) | consensus no-vig per frame | ≥ 2 priced frames with complete two-sided quotes, opening → latest | latest frame ≤ 240 min | **Available when the tape is fresh** | `INSUFFICIENT_OBSERVATIONS` / `STALE_EVIDENCE` otherwise |
| Late movement | 0.14 | Price movement | Same tape, final-window frames | consensus no-vig per frame | ≥ 2 frames inside the late window | latest frame ≤ 240 min | **Available late pregame only** | `INSUFFICIENT_OBSERVATIONS` before the window fills |
| Cross-book disagreement | 0.14 | Cross-book structure | Same tape, per-book no-vig range | ≥ 2 usable books at one instant | any frame with `usable_books ≥ 2` | latest frame ≤ 240 min | **Available when ≥ 2 books quote** | `INSUFFICIENT_OBSERVATIONS` / `STALE_EVIDENCE` |
| Model–market disagreement | 0.10 | Model versus market | Published AISP forecast + consensus no-vig home | forecast version × frame | a published forecast and a home-identified consensus | frame ≤ 240 min | **Available where a forecast exists** | `INSUFFICIENT_OBSERVATIONS` where no forecast |
| Information uncertainty | 0.08 | Game information | `LineupObservation` / `StarterObservation` (MLB Stats API), read at knowledge time | per side × evidence kind | window opens 6h before start | newest observation ≤ 360 min | **Available only with both sides observed** | `NOT_YET_OBSERVABLE` / `INCOMPLETE_EVIDENCE` / `STALE_EVIDENCE` |
| Social / narrative signal | 0.04 | Narrative | **None.** Would need a social-volume feed plus a baseline to call a spike abnormal | per side × time bucket | continuous pregame | n/a | **Unavailable, structurally** | `NO_SOURCE` |

**Post-cutoff usage:** none of these components is used after a decision cutoff
for any governed purpose. RFF is computed for display at read time from
knowledge-time-constrained observations, so a board recomputed later reflects
what was knowable then, and the index is never an input to a decision, a
settlement or an evaluation.

**Reproducibility:** every available component carries `observed_utc` and a
`detail` block with the exact frames, books, spreads and saturation points used,
so any displayed number can be recomputed from stored observations.

**Structural ceiling:** with public concentration (0.20), sportsbook relief
(0.12) and social narrative (0.04) having no source, the maximum achievable
coverage today is **0.64** — below the 0.70 publication floor. No game can
currently publish an RFF score, and that is the honest state of the evidence
rather than a bug. See §6.

---

## 4. Before and after, every displayed game

Both columns are computed from **one** component snapshot, so they differ only
in the arithmetic. "OLD" replays the renormalising formula; "NEW" is the shipped
one.

Live board, generated 2026-09-17T19:14:45.026628+00:00 (slate 2026-09-17). Counts: **0 published, 2 provisional, 8 insufficient**.

| Game | Coverage | Components | Observed | OLD score · band | NEW known | NEW range | NEW status |
| --- | ---: | ---: | --- | --- | ---: | --- | --- |
| DET @ CWS | 8% | 1/8 | information 100 | **100** · Maximum unrest | 8 | 8–100 | Insufficient |
| BOS @ TEX | 8% | 1/8 | information 100 | **100** · Maximum unrest | 8 | 8–100 | Insufficient |
| MIL @ PIT | 8% | 1/8 | information 50 | **50** · Curious | 4 | 4–96 | Insufficient |
| LAD @ CIN | 8% | 1/8 | information 50 | **50** · Curious | 4 | 4–96 | Insufficient |
| ATH @ TB | 8% | 1/8 | information 50 | **50** · Curious | 4 | 4–96 | Insufficient |
| SD @ COL | 8% | 1/8 | information 50 | **50** · Curious | 4 | 4–96 | Insufficient |
| PHI @ NYM | 50% | 4/8 | model-market 93, information 100, cross-book 24, RLM 13 | **46** · Curious | 23 | 23–73 | Provisional |
| KC @ HOU | 50% | 4/8 | information 100, cross-book 41, model-market 31, RLM 0 | **34** · Curious | 17 | 17–67 | Provisional |
| DET @ BUF | 0% | 0/8 | none | — | 0 | 0–100 | Insufficient |
| MIN @ LAA | 0% | 0/8 | none | — | 0 | 0–100 | Insufficient |

The largest correction is **DET @ CWS**: the old formula rescaled a single 8%-weight observation to **100 · Maximum unrest**; the same evidence now reads **known 8, possible 8–100, no score, no category, not ranked**.

Note the direction of the fix: the games that looked most alarming were the ones AISP knew least about, and the two games with real coverage (50%) were the ones the old board ranked *below* them.

---

## 5. What the user sees

The RFF page is now three sections, never one descending list:

1. **RFF scores with sufficient evidence** — published, ranked, comparable with
   each other and with nothing else.
2. **Provisional reads** — known contribution and interval only, explicitly not
   ranked.
3. **Games needing more data** — what was observed and what is still needed.

Each card shows coverage, "N of M components", the possible range and the top
observed component; non-published cards carry a **Not ranked** marker. The lab
gauge shows `known+` rather than a score below the publication floor, captioned
`PROVISIONAL` or `INSUFFICIENT EVIDENCE`, with the line
"known N · plausible A–B at C% coverage". Component rows read
"9 of 100 points · weight 14%" when counted and "weight 20% · not counted" when
not. An empty published section says so explicitly: *that is an absence of
evidence, not a calm market.*

---

## 6. RFF Core versus RFF Extended

Three of the eight components have **no sustainable source** and, on the
evidence rule above, will never contribute: public concentration, sportsbook
relief and social narrative. Holding 36% of the measure permanently unobservable
means the published tier is permanently empty.

**Recommendation — split the index:**

* **RFF Core (five components, renormalised weights summing to 1 within Core):**
  reverse line movement, late movement, cross-book disagreement, model–market
  disagreement, information uncertainty. Every one has a live, owned source.
  Core would be publishable on a normal slate and honestly comparable across
  games, because its complete set is actually collectable.
* **RFF Extended (eight components):** Core plus public concentration,
  sportsbook relief and social narrative. Extended stays defined and visible as
  a specification, permanently reporting `INSUFFICIENT_EVIDENCE` until a real
  source exists. It is the measure we *want*; Core is the measure we *have*.

This is a proposal, not a shipped change: adopting Core changes what the index
means, so it should be an explicit decision rather than a side effect of a bug
fix. Note that Core renormalises **within a fixed, fully collectable set** —
which is legitimate — whereas the defect renormalised over whatever happened to
arrive, which is not.

---

## 7. Data-availability plan

**Obtainable from evidence AISP already collects** (no new provider, no new
spend):

* Reverse line movement, late movement, cross-book disagreement — all three come
  from the existing multi-book tape. They need **≥ 2 usable books** with
  complete two-sided quotes and **≥ 2 frames**, with the late component needing
  two frames inside the final window. The binding constraint is *capture
  horizon*, not access: frames must be captured from opening through to close,
  and the current cadence leaves gaps that show up as
  `INSUFFICIENT_OBSERVATIONS` and, later in the day, `STALE_EVIDENCE`.
* Model–market disagreement — needs a published forecast for the game. Games
  without a V18 forecast simply cannot have it.
* Information uncertainty — needs lineup **and** starter observations for
  **both** sides inside the 6-hour window. Today, one-sided collection is the
  most common cause of unavailability.

**Requires a genuinely new source** (and therefore a decision, not engineering):

* Public concentration — a betting-percentage feed with ticket **and** handle
  share by side per book. There is no substitute. **Price movement is not
  public money**: a price says where a book will trade, not who is on it, and
  this repair does not and must not synthesise one from the other.
* Sportsbook relief — unlocked by the above and nothing else. Price
  concentration is not liability.
* Social narrative — a social-volume feed plus a baseline for abnormality.

Nothing here manufactures public concentration or social sentiment, and no
threshold in this document was chosen from outcomes or postgame evidence.

---

## 8. Validation

* **Backend:** `server/tests/test_rff_evidence_policy.py` (10 new tests) +
  `server/tests/test_rff_index.py` (16, updated) = **26 passing**. The new file
  pins the exact live failure: one 8%-weight component at 100 yields coverage
  0.08, known 8.0, range [8, 100], `score is None`, `INSUFFICIENT_EVIDENCE`,
  `CATEGORY_UNCERTAIN`, `rankable False`, and the old figure only as
  `observedComponentAverage == 100`.
* **Client:** `marketIntelligence.test.tsx` 16 passing; full client comparison
  against the base branch showed **0 new failures**.
* **Viewports:** 375, 768 and 1440 px — see §9.

---

## 9. Deployment

**Deployed** 2026-09-17 15:12 ET, commit `c18abc8` (previous `533c89b`).

* Frontend built from a clean tree — `/frontendz` reports
  `commit c18abc86db1a32b2a9350e986615b87e4796db28`,
  `client_tree_dirty_at_build: false`, 108 assets.
* Backend restarted through the **ASP Backend 8010** scheduled task only, at
  15:12 ET — **outside every decision window**. The 14:55 ET final-pregame
  cutoff for SD @ COL (15:10 first pitch) had closed; the next cutoff is
  19:00 ET.
* `/readyz` 200 on the first poll after restart.

**Responsive verification** (headless Chrome over CDP, emulated widths):

| Width | Sections | Cards (published / provisional / insufficient) | Horizontal overflow |
| ---: | ---: | --- | --- |
| 375 | 3 | 0 / 2 / 8 | none |
| 768 | 3 | 0 / 2 / 8 | none |
| 1440 | 3 | 0 / 2 / 8 | none |

No element overflowed its section at any width; `scrollWidth == clientWidth` in
all three cases.

**Protected state** — fingerprint (18 keys: positions, results, experiment
accounts and fundings, shadow decisions and settlements, bankrolls, virtual
positions, shadow forecasts, parlay tickets, champion authorities, provider
ledger, odds snapshots) taken before the change and again after deployment.
14 of 18 identical. The four that moved are all ordinary scheduler activity,
none of it from RFF, which writes nothing:

| Key | Before → after | Attribution |
| --- | --- | --- |
| `official_asp_positions` | 46 → 47 | one position opened by the scheduler at **15:07:38 ET**, five minutes *before* the restart, in the 14:55 decision window |
| `shadow_experiment_decisions` | 33 646 → 33 651 | five scheduled MORNING/FINAL_PREGAME checkpoints, 14:25–15:07 ET |
| `odds_snapshots` | 256 844 → 257 044 | 200 rows from scheduled odds collection |
| `provider_request_ledger` | 1 530 → 1 536 | see below |

**Provider calls, credits and cost for this assignment: none.** The ledger rows
in the window are:

| Time (ET) | Endpoint | Quota cost | Cause |
| --- | --- | ---: | --- |
| 15:01:02 | `sgo_mlb_events` | 2 | scheduled collection, before the restart |
| 15:11:23 | `sgo_mlb_events` | 2 | scheduled collection, before the restart |
| 15:17:50 | `sgo_account_usage` | **0** | the free, non-billable account read a backend start always performs |

SportsGameOdds after the restart: 1 962 of 2 500 used this month, 538
remaining — unchanged by this work. The Odds API reserve floor (10) was not
touched, prop collection stays disabled, and no threshold, budget or account
setting was modified.

**Rollback.** The previously deployed build was rebuilt over during this
deployment, so there is no pre-change `dist` copy in
`.runtime/frontend-rollback/`. To roll back:

```bash
git -C C:/Users/james/ai-sports-predictor checkout 533c89b -- server client && powershell -File scripts/build-frontend.ps1
```

then restart the **ASP Backend 8010** scheduled task. Nothing else has to be
undone: there is no migration (alembic head remains `20260917_0044`), no
scheduler change, and no written state to reverse.
