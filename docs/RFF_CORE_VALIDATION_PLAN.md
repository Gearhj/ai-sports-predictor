# RFF Core — prospective validation plan

Version 1 · 2026-09-17 · branch `claude/rff-core-index-v1`

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
* **No retrofitting.** Every hypothesis below is registered before its
  observation window opens. A hypothesis that is not written down before the
  data arrives is not tested.
* **No new evidence classes.** Validation reads what the collectors already
  persist. It does not license a new provider call, a new scheduler or a
  scraped source.

---

## 1. What is being claimed

RFF Core claims to describe **current, observable market unrest** from five
components. The prospective question is whether a higher Core reading at a
stated moment is followed by more of the market behaviour it purports to be
about:

| # | Hypothesis | Outcome measured | Window |
| --- | --- | --- | --- |
| H1 | Higher Core precedes more later repricing | absolute change in consensus no-vig home probability from the reading to the last pregame frame | reading → cutoff |
| H2 | Higher Core precedes more cross-book dispersion | max-minus-min per-book no-vig probability at the last pregame frame | reading → cutoff |
| H3 | Higher Core precedes more late instability | count of direction changes in the consensus series after the reading | reading → cutoff |
| H4 | Higher Core precedes more information events | count of newly persisted lineup/starter resolutions and scratches after the reading | reading → first pitch |
| H5 | Core is not just its own components restated | H1–H4 hold for the composite after conditioning on each single component | as above |

H5 is the one that decides whether Core is worth having. If every association
is carried by one component, the honest conclusion is to display that component
and retire the composite.

## 2. Reading points

Each game contributes readings at fixed clock offsets, never at a moment chosen
after the fact:

* **T-6h** (as the information window opens),
* **T-3h**,
* **T-90m**,
* **T-30m**.

A reading is recorded only when it is publishable at that moment, or is
recorded with its tier when it is not. Provisional and insufficient readings are
analysed **separately** and never pooled with published ones: their intervals do
not identify a value, so a pooled correlation would be measuring coverage rather
than unrest.

## 3. Design

* **Prospective only.** The analysis set is games whose readings are captured
  going forward from the registration date. No back-fitting over the existing
  odds history; that history is used once, for the power estimate in §5, and
  never re-used for a result.
* **Unit of analysis:** one (game, reading point) pair. Games contribute up to
  four readings, so standard errors are clustered by game.
* **Stratification:** by league, by market liquidity (number of usable books at
  the reading), and by evidence coverage decile. Coverage is a confounder, not
  a covariate to be averaged over: a thin market both raises dispersion and
  lowers coverage.
* **Primary statistic:** Spearman rank correlation between the Core score and
  each outcome, within stratum. Rank, not Pearson, because the bands are
  ordinal and the outcomes are heavy-tailed.
* **Secondary:** the same correlation for each component alone (the H5 control),
  and for `observedComponentAverage` (the renormalised diagnostic), to confirm
  the diagnostic is *worse* than Core rather than quietly better.
* **Interval handling:** a published reading enters as its point value. A
  provisional reading enters only in a separate, clearly labelled analysis that
  uses interval methods; it is never collapsed to its midpoint, which would
  invent precision.

## 4. Success and failure, declared in advance

| Result | Reading |
| --- | --- |
| Core correlates with H1–H3 at ρ ≥ 0.2 within stratum, and survives H5 | the measure describes something; keep it, still research-only |
| Correlation present but fully explained by one component | retire the composite, display that component |
| No correlation at ρ ≥ 0.1 across two full seasons of readings | Core does not describe later market behaviour; say so publicly on the page |
| Correlation only in the lowest-coverage stratum | an artefact of thin markets; tighten the publication floor rather than claim a finding |

A negative result is a publishable result and will be written into the page
copy. The index is not defended by re-specifying it.

## 5. Power and horizon

At four readings per game, a normal MLB slate contributes roughly 60 readings a
day, of which only the published tier is usable for the primary statistic.
Detecting ρ = 0.2 with 80% power at α = 0.05 needs about 190 published
readings; at the current publication rate that is weeks, not days, and the first
scheduled look is **no earlier than 300 published readings**. No interim look
changes the index, and interim results are not published as findings.

## 6. What could make this wrong

* **Coverage confounding** — thin markets are both less covered and more
  volatile. Handled by stratifying on coverage and on book count, and by
  refusing to pool tiers.
* **Component autocorrelation** — late movement and RLM read the same series,
  so a single large move inflates both. Already declared in
  `correlated_components`; the H5 control conditions on each.
* **Survivorship in the published tier** — only well-covered games publish, so
  the primary analysis is conditioned on coverage by construction. This is
  stated as a limitation rather than corrected away.
* **Information-event counting** — H4 counts what AISP persisted, which is a
  function of AISP Evidence Health as much as of the game. H4 is therefore
  reported with the health block beside it and is treated as the weakest of the
  four hypotheses.

## 7. What this plan does not do

It does not test profitability, closing-line value, bet selection or any
settlement outcome, and it produces no threshold that a decision could consume.
If a future assignment wants an outcome-linked study, it needs its own
authorisation, its own registration, and a different document.
