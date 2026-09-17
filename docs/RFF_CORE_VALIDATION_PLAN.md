# RFF Core — prospective validation plan

Version 2 · 2026-09-17 · branch `claude/rff-core-validation-plan-v2`

> **Version 2 corrects the methodology of version 1.** v1 treated all four
> reading points as independent observations, powered the study on *readings*
> rather than games, and stated H5 ("Core is not just its own components
> restated") as an aspiration without an operational test. Those three faults
> would have overstated precision and left the incremental question open to a
> choice made after seeing results. Nothing about the index itself changed:
> weights, calculation, publication gates, interface, authority and production
> behaviour are exactly as deployed.

RFF Core is a **research** measure. This plan tests whether it describes
anything real about markets. It deliberately does **not** test whether it makes
money, and nothing in it may be used to tune the index against bet outcomes.

---

## 0. Standing prohibitions

* **No outcome optimisation.** RFF Core weights, thresholds, bands and
  saturation points are frozen design choices. They are never fitted,
  re-weighted or reselected against bet results, closing-line value, profit or
  any settled position — in this plan or in any follow-up.
* **No decision coupling.** RFF Core and RFF Extended are not forecast
  features, decision thresholds, bet recommendations or position inputs. A
  validation result never changes that; a *successful* result does not either.
* **No retrofitting.** Every hypothesis, covariate, transformation, estimator
  and threshold below is fixed before its observation window opens. Anything
  not written down before the data arrives is not tested.
* **No new evidence classes.** Validation reads what the collectors already
  persist. It does not license a new provider call, a new scheduler or a
  scraped source.

---

## 1. Reading points: one primary, three secondary

**The primary reading for a game is T-90m** — 90 minutes before scheduled
start. It is predeclared here, it is the same offset for every game and every
league, and it is chosen because it is the latest offset at which all five Core
components are routinely observable while the market is still live.

**T-6h, T-3h and T-30m are secondary repeated measurements** of the same game.
They exist to describe how Core evolves and to check that a primary finding is
not an artefact of one clock offset. They are never pooled with the primary as
if they were independent games.

| Role | Offsets | Unit of analysis | Used for |
| --- | --- | --- | --- |
| **Primary** | T-90m | **one game** | every confirmatory test in §2 |
| Secondary | T-6h, T-3h, T-30m | (game, offset), clustered by game | repeated-measures description and sensitivity only |

A game contributes to the primary analysis only when its T-90m reading is
**publishable** (`RFF_PUBLISHED`). Provisional and insufficient readings do not
enter the primary: their intervals do not identify a value, so including them
would measure coverage rather than unrest. They are counted and reported, and
analysed separately with interval methods, never collapsed to a midpoint.

---

## 2. Hypotheses — every one is incremental (H5, operationalised)

Version 1 listed H5 as a separate hypothesis: "Core is not just its own
components restated." That is not a test. In version 2 **there is no marginal
test at all** — each hypothesis asks whether Core adds anything *beyond a
predeclared baseline that is already available at the moment of the reading*.
A raw association that disappears once the baseline is in the model is not a
finding about Core.

| # | Question | Outcome (after the reading) | Predeclared baseline (known at the reading) |
| --- | --- | --- | --- |
| **H1** | Does Core predict **future** repricing beyond movement already seen? | \|Δ consensus no-vig home\| from the reading to the last pregame frame | (a) \|Δ\| over the 90 minutes before the reading, (b) \|Δ\| from opening to the reading, (c) frames observed before the reading |
| **H2** | Does Core predict **later** dispersion beyond dispersion already present? | cross-book no-vig spread at the last pregame frame | (a) cross-book spread at the reading, (b) usable book count at the reading |
| **H3** | Does Core predict **future** instability beyond prior instability? | count of consensus direction changes after the reading | (a) direction changes in the 90 minutes before the reading, (b) frames observed before the reading |
| **H4** | Does Core add anything beyond unresolved participants and our own evidence health? | count of newly persisted lineup/starter resolutions and scratches from the reading to first pitch | (a) the information-uncertainty component score itself, (b) `lineup_sides_observed`, (c) `starter_sides_observed`, (d) `forecast_readiness_score`, (e) `coverage_lost_to_collection_gaps` |

Each baseline is measured **strictly at or before the reading instant**, from
the same knowledge-time-constrained observations the index itself used. No
covariate may use information that arrives after T-90m.

H4 deserves its own warning: its outcome is partly a function of AISP Evidence
Health, which is also one of its baselines. It is therefore the weakest of the
four and is reported with the health block beside it.

### Secondary component check

For each hypothesis, the identical incremental test is run with each **single
Core component** substituted for the composite. If a component alone matches or
beats the composite on every hypothesis, the honest conclusion is to display
that component and retire the composite. The renormalised diagnostic
`observedComponentAverage` is run through the same test, to confirm it performs
**worse** than Core rather than quietly better.

---

## 3. Predeclared incremental comparison method

Fixed here, before any data is observed. It is not chosen, tuned or swapped
after viewing results.

**Estimator — partial Spearman rank correlation, residual-on-residual:**

1. Rank-transform the outcome, the Core score and every baseline covariate
   within the analysis set. Counts (H3, H4) are rank-transformed rather than
   log-transformed; no transformation is selected later.
2. Regress ranked outcome on ranked baselines (OLS, main effects only, no
   interactions, no variable selection). Keep the residuals.
3. Regress ranked Core on the same ranked baselines. Keep the residuals.
4. **Partial ρ** is the Pearson correlation of the two residual vectors.
5. Confidence intervals come from a **game-level bootstrap**, 2,000 resamples,
   resampling whole games. For the secondary repeated-measures analysis the
   bootstrap resamples games, not readings, which is what carries the
   clustering.

**Specification lock.** The analysis script is written, committed and its SHA
recorded in this document **before the 300th qualifying game is reached**. The
recorded hash is what runs. A change to the script after that point invalidates
the look and starts a new, separately registered one.

**Fixed model choices:** main effects only; no covariate added, dropped or
transformed after the lock; a game missing any baseline for a hypothesis is
excluded from that hypothesis and the exclusion count is reported; stratum
adjustment enters as covariates in steps 2 and 3 (league, usable book count at
the reading), not as separate per-stratum tests.

**Why a pooled adjusted model rather than per-stratum correlations.** v1 said
the primary statistic was a within-stratum correlation. With games as the unit,
300 games split across leagues and liquidity strata leaves no stratum with
usable power. Stratum-specific estimates are therefore **descriptive only** and
are labelled as such; they are not powered and no conclusion rests on them.

A held-forward variant (fit the baseline model on the first half of the
qualifying games, apply it to the second) is an acceptable alternative form and
is likewise fixed at lock time; it is not run in addition and then chosen
between.

---

## 4. Power, with games as the unit

All figures use the Fisher-z approximation with the standard Spearman variance
inflation of 1.06, α = 0.05 two-sided:

| Quantity | v1 (readings, wrong) | **v2 (games)** |
| --- | --- | --- |
| Unit | reading | **game (T-90m)** |
| n for ρ = 0.20 at 80% power | ~190 readings | **~206 games** |
| MDE at 80% power with 300 | ρ ≈ 0.10 (implied) | **ρ ≈ 0.166** |
| Power at ρ = 0.20 with 300 | overstated | **≈ 0.92** |
| Power at ρ = 0.15 with 300 | overstated | **≈ 0.72** |

Adjusting for three baseline covariates costs about 0.3% of precision at this
n (df 297 → 294) and does not change any figure above.

**First formal look: 300 games with a publishable T-90m reading** — not 300
readings. That is roughly a 50% margin over the 206 needed for ρ = 0.20, and it
is deliberately generous because the confirmatory quantity is a *partial* rank
correlation, which is bounded below the marginal one.

**Within-game correlation in the secondary analysis.** The three secondary
offsets are repeated measurements of the same game and are strongly correlated:
consecutive Core readings share components, and the tape-driven ones share the
same price series. Their effective sample size is therefore far below the
nominal count:

```
DEFF = 1 + (m - 1) · ICC        m = 4 readings per game
ICC = 0.5  ->  DEFF = 2.5  ->  1,200 readings behave like ~480 games
ICC = 0.7  ->  DEFF = 3.1  ->  1,200 readings behave like ~387 games
```

So the three extra offsets add roughly 30–60% more effective information than
the primary alone — not four times as much, which is what treating readings as
independent would have claimed. The secondary analysis is run as a mixed model
with a random intercept per game (equivalently, cluster-robust standard errors
clustered by game), **the observed ICC is reported**, and the effective sample
size is reported alongside the nominal one. No secondary result is reported
without both.

---

## 5. Success and failure, declared in advance

Applied to the **primary, incremental** statistic at the first formal look:

| Result | Reading |
| --- | --- |
| Partial ρ ≥ 0.15 with a 95% CI excluding 0, on H1–H3 | Core describes something beyond what the market already showed; keep it, still research-only |
| Incremental effect present but matched or beaten by a single component | retire the composite, display that component |
| Partial ρ < 0.15, or a CI including 0, at 300 games | **not yet informative** — 300 games gives only ~72% power at 0.15. Continue to the two-season horizon; do not declare a null here |
| No incremental effect at ρ ≥ 0.10 across two full seasons of qualifying games | Core does not add to what is already observable; say so on the page |
| Effect only in the lowest-coverage or thinnest-book stratum | an artefact of thin markets; tighten the publication floor rather than claim a finding |

A negative result is a publishable result and will be written into the page
copy. The index is not defended by re-specifying it.

---

## 6. What could make this wrong

* **Publication survivorship.** Only well-covered games have a publishable
  T-90m reading, so the primary analysis is conditioned on coverage by
  construction. This is stated as a limitation, not corrected away, and the
  qualifying rate is reported with every look.
* **Component autocorrelation.** Late movement and RLM read the same series, so
  one large move raises both. Declared in `correlated_components`; the
  per-component secondary check quantifies it.
* **Baseline leakage.** The whole design depends on every covariate being
  knowable at T-90m. Any covariate found to use later information voids that
  hypothesis for that look.
* **H4 circularity.** Its outcome depends on AISP's own collection, which is
  also its baseline. Treated as the weakest hypothesis and never reported alone.
* **Clock-offset sensitivity.** If a primary finding at T-90m is absent at every
  secondary offset, it is reported as offset-specific and treated as fragile.

---

## 7. What this plan does not do

It does not test profitability, closing-line value, bet selection or any
settlement outcome, and it produces no threshold that a decision could consume.
If a future assignment wants an outcome-linked study, it needs its own
authorisation, its own registration, and a different document.

---

## 8. Change log

| Version | Date | Change |
| --- | --- | --- |
| 1 | 2026-09-17 | First registration, alongside the RFF Core deployment |
| 2 | 2026-09-17 | Methodology correction: single primary reading at T-90m; T-6h/T-3h/T-30m demoted to secondary repeated measures; first look restated as 300 **games** with a publishable T-90m reading; power recomputed with games as the unit and an explicit design effect for the repeated measures; H5 replaced by making every hypothesis incremental against a predeclared baseline; the incremental estimator, its bootstrap and a specification lock predeclared. No change to the index, weights, gates, interface, authority or production behaviour. |
