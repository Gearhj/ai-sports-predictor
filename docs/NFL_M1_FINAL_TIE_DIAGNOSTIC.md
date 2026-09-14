# NFL M1 final-tie probability — bounded diagnostic

**Date:** 2026-09-14 · **Scope:** read-only. No forecast, fitted model, residual
sampler or published distribution was changed. No provider request was made. No
replacement forecast was published.

## The question

The producer handoff for DEN at KC reports **430 equal final-score pairs out of
10,000 draws** (4.30%) with **no explicit overtime-resolution stage**. Is that a
demonstrated construction defect, a calibration mismatch, or an unresolved
limitation?

## Exact identities

| Thing | Identity |
| --- | --- |
| Forecast | `asp-sports-game-forecast:sha256:2d9d0197aa7ef02c15a110cf7ce5f36ad2a87d2f1c2a92601d9989d5c487aec9` |
| Event | `2026_01_DEN_KC` · canonical `14a64986-3d92-48af-ba69-15731e936d54` |
| Engine | `NFL_M1_XGBOOST` / `NFL_M1_2026_RESEARCH_SHADOW_V1` |
| Model binary | `7bec06646fd5035380107501d07e139425ac483a47e02e0bbbd56301124fb8fd` |
| Generating law | `NFL_M1_EMPIRICAL_CORRELATED_RESIDUAL_V1`, seed `731001`, 10,000 draws |
| Simulation artifact | `nfl-labs-simulation-supplements/sha256/73b090a01a8df9c9f8f2f38f6867f53c3a5fa240410427e3f842fb6ea4e11074/artifact.json` |
| Producer handoff | `aisp-daily-forecast-bundles/handoffs/sha256/45d2d835fb9b8963f461ed03fec6cf915cada2fea6452a9d1efc03498dbbe5c9/CLAUDE_HANDOFF.md` |
| Assessment population | `outputs/nfl-m1-qb-context-residual-v1/residuals/assessment-residuals.parquet` (570 games, 2023–2024) |
| Donor population | `…/development-residuals.parquet` (838 games, 2020–2022) |
| Benchmark predictions | `asp-data-staging/nfl/m1-modeling-benchmark-v1/predictions/five-fold-oof-point-predictions.parquet` (1,408 distinct games, 2020–2024) |
| Published probabilities | DEN 0.5753 · KC 0.3817 · tie 0.0430 |

## 1. Where the tie mass originates

The published law, read back from
`asp-model-studio/server/sports_nfl_m1/distributions.py`, does three things in
order: draw a **paired** residual by row index from a frozen mean-centred bank,
add it to the XGBoost mean, **clip at 0**, then apply **stochastic rounding**
(`floor + (u < latent − floor)`) — not a plain floor.

Replaying that construction on DEN at KC:

| | draws |
| --- | ---: |
| Integer ties after discretisation | 339 of 10,000 |
| **Ties already present before discretisation** | **0** |
| — of which both scores clipped to 0 | 0 |
| **Ties created by discretisation** | **339** |

Latent margin on the tied draws: **−1.636 to +1.561**, with 88.2% inside ±1.

**Every tied draw is manufactured by the rounding step.** The latent scores are
continuous (a float mean plus a float residual), so an exact latent tie has
probability zero, and the replay confirms zero. A tie appears only where the
continuous margin was already within about a point of zero *and* the two
independent rounding draws happened to land on the same integer.

> **Replay caveat, stated rather than hidden.** The replay reproduces the
> *mechanism* at 3.39%, not the published 4.30%. It uses the 838-donor
> development bank — the only paired-residual bank retained in exchangeable
> form — while the published forecast used the producer's full frozen training
> bank (through 2026-02-08). The 0.9-point difference is a donor-bank
> difference, not a disagreement about where ties come from, and both figures
> are an order of magnitude above the observed rate. **Gap:** the published
> forecast's own latent draws are not retained; only the integer joint PMF is
> (`"populationReconstruction": "INTEGER_COUNTS_RECOVERED_EXACTLY; ORIGINAL_DRAW_ORDER_NOT_ASSERTED"`).

## 2. Do the fitted targets and residual donors include overtime?

**Yes.** The benchmark's target columns are `actual_home_points`,
`actual_away_points`, `actual_home_win` and **`actual_tie`** — nflverse *final*
scores, which are post-overtime. A game that went to overtime and was decided
appears as a decided final; one that stayed level appears under `actual_tie`.
Across 1,408 distinct games, **4** finals were level.

Residual donors are `actual − predicted` on those same targets, so the donors
carry overtime's effect too.

**Consequence:** appending an overtime-resolution stage would count overtime
twice. The model is not missing overtime; it is failing to reproduce the
*shape* overtime imposes on final margins.

## 3. Predicted versus observed ties — chronological assessment

Observations are **games**. Seasons 2020–2022 are **reused historical evidence**
(the donor bank, inspected during development); 2023–2024 are the held-out
assessment.

| Season | Phase | Games | Predicted ties | Observed ties | Pred rate | Obs rate |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 2020 | reused historical evidence | 269 | 8.3 | 1 | 0.0309 | 0.0037 |
| 2021 | reused historical evidence | 285 | 8.8 | 1 | 0.0307 | 0.0035 |
| 2022 | reused historical evidence | 284 | 8.7 | 2 | 0.0307 | 0.0070 |
| 2023 | held-out assessment | 285 | 8.7 | 0 | 0.0306 | 0.0000 |
| 2024 | held-out assessment | 285 | 8.7 | 0 | 0.0304 | 0.0000 |
| **All** | **2020–2024** | **1,408** | **43.2** | **4** | **0.0307** | **0.0028** |
| | held-out only | 570 | 17.4 | 0 | 0.0305 | 0.0000 |

The model predicts ties at roughly **11× the observed rate**, and the
overstatement is stable across every season — it is not a 2026 artefact and not
driven by one game.

## 4. Descriptive breakdown

By expected margin (home − away):

| Band | Games | Predicted | Observed | Pred rate | Obs rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| (−99, −7] | 78 | 2.0 | 0 | 0.0250 | 0.0000 |
| (−7, −3] | 199 | 5.8 | 1 | 0.0294 | 0.0050 |
| (−3, 0] | 244 | 8.6 | 1 | 0.0353 | 0.0041 |
| (0, 3] | 303 | 9.3 | 0 | 0.0308 | 0.0000 |
| (3, 7] | 339 | 11.5 | 2 | 0.0339 | 0.0059 |
| (7, 99] | 245 | 5.4 | 0 | 0.0218 | 0.0000 |

By expected total:

| Band | Games | Predicted | Observed | Pred rate | Obs rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| (0, 40] | 99 | 3.2 | 0 | 0.0319 | 0.0000 |
| (40, 44] | 376 | 11.5 | 2 | 0.0305 | 0.0053 |
| (44, 48] | 540 | 15.9 | 2 | 0.0294 | 0.0037 |
| (48, 99] | 393 | 12.0 | 0 | 0.0306 | 0.0000 |

Predicted tie rate barely moves with either — it stays near 3% everywhere,
dipping only in the blowout margin bands. The model is not concentrating tie
mass where ties are plausible; it is spreading a near-constant rate everywhere.

*No correction is chosen from these bands, and no broad model search was run.*

## 5. The shape around zero — the decisive evidence

DEN at KC margin probability from the retained PMF, against observed final
margins in the same 1,408-game population:

| Margin | Model | Observed |
| ---: | ---: | ---: |
| −4 | 0.0306 | 0.0263 |
| −3 | 0.0346 | **0.0788** |
| −2 | 0.0398 | 0.0213 |
| −1 | 0.0530 | 0.0234 |
| **0** | **0.0430** | **0.0028** |
| +1 | 0.0329 | 0.0220 |
| +2 | 0.0256 | 0.0270 |
| +3 | 0.0313 | **0.0710** |
| +4 | 0.0244 | 0.0206 |

A construction defect would show as an **isolated spike** at zero. There is no
spike: 0.0430 sits *between* its neighbours (−1 at 0.0530, +1 at 0.0329). The
model's margin distribution is smooth. The real one is spiky — 3 and 7 are
common because of field goals and touchdowns, and **zero is nearly absent
because overtime resolves it**.

## Findings

**Where the tie mass originates.** Entirely in the discretisation step. Zero of
10,000 draws were tied before rounding; every tie is a stochastic-rounding
coincidence on a continuous latent margin that was already within about a point
of zero. Neither the clip-at-zero step nor an overtime omission contributes.

**Predicted versus observed.** 3.07% predicted against 0.28% observed over 1,408
games; 3.05% against 0.00% over the 570 held-out games. Roughly 11× overstated,
stable across all five seasons.

**Defect, mismatch, or limitation?** **Not a demonstrated construction defect.**
The sampler does exactly what its published law states, the joint PMF reconciles,
and the tie mass equals the local density of a smooth margin distribution — there
is no double-count and no coding error to point at. It is a **calibration
mismatch with an identified cause, which is also an unresolved limitation the
artifact already declares**: an unconstrained final-score residual law produces a
smooth integer margin distribution and therefore cannot reproduce football's
discrete margin structure — neither the spikes at 3 and 7 nor the hole at 0.

**What the evidence does *not* support.** Appending an overtime stage (targets
are already post-overtime, so it would double-count); substituting a league tie
rate; silently removing ties; or redistributing the mass between the teams. No
allocation method has been specified or assessed, so none is applied here.

## The limitation to disclose

> This forecast gives the game a 4.3% chance of ending in a tie. That number is
> an artefact of how the model rounds scores to whole numbers, not a football
> judgement. Measured against five seasons of real results, the model predicts
> ties about eleven times more often than they happen — 3.1% predicted against
> 0.3% observed, and none at all in the two held-out seasons. Real NFL games
> almost never end level because overtime settles them, and this model has no
> way to represent that. The tie handling in the market is settled — a tie
> refunds the stake — but roughly four points of probability are sitting on an
> outcome that almost never occurs, and this diagnostic does not establish which
> team they belong to. Any edge computed from these win and loss probabilities
> carries that uncertainty.

## Effect on refund-on-tie moneyline EV

**Illustrative prices only — not quotes, not offers, not current.** AISP holds no
current NFL quote: the NFL pregame lane is unfunded and the newest stored
moneyline was captured 2026-08-29. That price is **not** a current opportunity
and is not used as one. Published probabilities are preserved exactly.

Tie as a push versus tie as a loss, at the published probabilities:

| Illustrative price | EV (tie = push) | EV (tie = loss) | Difference |
| ---: | ---: | ---: | ---: |
| +100 | 19.36% | 15.06% | 4.30% |
| +120 | 30.87% | 26.57% | 4.30% |
| +140 | 42.37% | 38.07% | 4.30% |

The difference is exactly the tie mass at every price. That part is settled: a
push returns the stake, and treating it as a loss subtracts money the market
gives back.

The open part is the 4.02 points of **excess** tie mass (4.30% published minus
0.28% observed). Its two extreme allocations **bound** the answer:

| Illustrative price | EV published | Bound: excess all to DEN | Bound: excess all to KC |
| ---: | ---: | ---: | ---: |
| +100 | 19.36% | 23.38% | 15.34% |
| +120 | 30.87% | 35.69% | 26.85% |
| +140 | 42.37% | 48.00% | 38.35% |

**These bounds are not a correction.** Neither column is proposed, adopted or
assessed, and neither replaces the published probabilities. The width between
them is the size of the open question — roughly 8 points of EV at any price.

## Evidence gaps named rather than reconstructed

1. The published forecast's **latent (pre-rounding) draws are not retained** —
   only the exact integer joint PMF. The pre- versus post-discretisation split
   above therefore comes from a replay of the published law, not from the
   original draws.
2. The **full frozen training donor bank** is not in the exchange, so the replay
   used the 838-donor development bank and reproduces 3.39% rather than 4.30%.
3. The retained assessment export carries **no overtime flag** per game. The
   target identity (`actual_home_points`/`actual_away_points` = nflverse final
   scores, plus a separate `actual_tie`) settles the overtime question without
   it, but a per-game OT rate cannot be computed from the retained export alone.
4. AISP's own `nfl_final_observations` holds **15 finals (2026 REG only)** — far
   too few for a tie-rate comparison, which is why the producer's chronological
   population was used.

None of these was reconstructed. Closing 1 or 2 would need the producer to
retain latent draws or export the training donor bank.
