# RFF Core — the operational market-reality index

Adopted 2026-09-17 · branch `claude/rff-core-index-v1` ·
supersedes the eight-component index in `RFF_EVIDENCE_COVERAGE_REPAIR.md`

RFF Core is a **research display**. It is not a forecast feature, a decision
threshold, a bet recommendation or a position input, and it never becomes one.

---

## 1. The five components and their fixed design weights

| Component | Weight | Evidence family | Source |
| --- | ---: | --- | --- |
| Reverse line movement | **28.125%** | Price movement | multi-book tape, opening → latest consensus |
| Late movement | **21.875%** | Price movement | multi-book tape, final-window frames |
| Cross-book disagreement | **21.875%** | Cross-book structure | per-book no-vig range at one instant |
| Model–market disagreement | **15.625%** | Model versus market | published AISP forecast vs consensus no-vig |
| Information uncertainty | **12.500%** | Game information | lineup and starter observations, both sides |
| **Total** | **100.000%** | 4 families | |

These are **fixed design weights**. They were chosen once, over a fixed and
fully collectable set, and are **never renormalised for an individual game**
because of what that game happens to have. A game missing a component gets a
wider interval, never a rescaled score — that per-game renormalisation is the
defect this index was rebuilt to remove.

## 2. The calculation is unchanged

```
knownContribution = Σ wᵢ·sᵢ  over available components      (0..100)
coverage          = Σ wᵢ      over available components      (0..1)
unknownWeight     = 1 - coverage
lowerBound        = knownContribution
upperBound        = knownContribution + unknownWeight · 100
```

An unavailable component contributes nothing. It is **not** imputed to zero and
the remaining components are **not** rescaled: the missing weight becomes the
width of the interval.

## 3. Publication gates (unchanged)

| Coverage | Status | Shown | Ranked |
| --- | --- | --- | --- |
| `< 50%` | `INSUFFICIENT_EVIDENCE` | known contribution, interval, what is missing. Labelled **Unknown** | no |
| `50–69%` | `PROVISIONAL_WIDE_RANGE` | known contribution and interval only | no |
| `≥ 70%` **and** ≥ 4 components **and** ≥ 2 families | `RFF_PUBLISHED` | a score | yes, against published games only |

A Calm / Curious / Spicy / Maximum unrest band is assigned **only** when the
whole interval lies inside one band (and is no wider than 25 points). Otherwise
the category is `CATEGORY_UNCERTAIN` when published, or `CATEGORY_UNKNOWN`
below the floor.

Because every four-of-five combination clears 70% coverage, a published score is
now genuinely reachable — unlike the eight-component index, whose 0.64 source
ceiling made publication impossible.

## 4. Unknown is not calm

Below the publication floor the band label is **Unknown**, never Calm. Calm is a
finding about the market; Unknown is a statement about our evidence. The board
section is titled "Unknown — not enough evidence", the gauge caption reads
`UNKNOWN — NOT CALM`, and the empty published section says in as many words that
an empty board is an absence of evidence rather than a quiet slate.

## 5. The point estimate does not always lead

`emphasize_point` is true only when the game is published **and** the interval
identifies a single band. When it does not, the card and the gauge lead with the
range and caption it `RANGE — NO SINGLE BAND`. The range is the finding in that
case, and a large number in a large font would misrepresent it.

## 6. AISP Evidence Health is separate

Internal forecast readiness, ingestion gaps, provider outages and missing
internal records are reported in their own `evidence_health` block
(`AISP_EVIDENCE_HEALTH_V1`) and **never** raise game-information uncertainty:

* `lineup_sides_observed`, `starter_sides_observed`, `forecast_readiness_score`
* `components_blocked_by_collection` — components unavailable because of a gap
  on our side, with the classified reason
* `components_awaiting_their_window` — the clock, not a gap
* `coverage_lost_to_collection_gaps` — how much of the measure our own gaps cost

Information uncertainty itself scores only the evidence AISP *holds*: it needs
both sides observed inside the 6-hour window, and a missing side makes the
component unavailable rather than maximal.

## 7. RFF Extended is a concept, not a number

Public concentration, sportsbook liability/relief and social narrative have no
legitimate source. They carry **no weight**, are **never calculated**, and are
displayed only as a collapsed list of what each would need. Price movement is
never substituted for public money; public concentration and social sentiment
are never manufactured. Extended becomes a number only when all three have real
sources — and that would be a new decision, not an automatic promotion.

## 8. The diagnostic

`diagnostics.observedComponentAverage` — the old renormalised figure — is kept
as an internal diagnostic only. In the interface it lives in a collapsed
disclosure explicitly labelled **"not RFF"**. It never sets a category and never
ranks games.

## 9. Authority

Every payload carries the authority block: RFF affects no forecast, no adjusted
edge, no threshold, no decision, no position, no settlement and no bankroll.
Prospective validation is specified in `RFF_CORE_VALIDATION_PLAN.md` and is
explicitly barred from optimising the index against bet outcomes.

## 10. Tests that hold this in place

`server/tests/test_rff_evidence_policy.py` is the gate. Alongside the
fixed-weight arithmetic it pins the requirement that **missing data can never
create an extreme score or band**: a parametrised test drives every partial
shape of Core with each observed component pinned at 100 and asserts that the
result publishes no number, names no band and never reaches the top band. A
companion test shows the top band is reachable only when the observed evidence
earns it — four or five components at maximum, with the whole interval above 75.
