# MLB acceptance-policy study — outcome-linked

**Study** `AISP_MLB_ACCEPTANCE_POLICY_STUDY_V2` · **control** `AISP_PRACTICAL_PREGAME_POLICY_V2_1`
**Cohort frozen** 2026-09-09T02:00:43Z · **branch** `claude/mlb-overnight-acceptance-policy-study-v2`

Read-only throughout. No provider call, no odds refresh, no policy change, and no forecast,
decision, position or settlement was written. The earlier `MLB_STRICT_POLICY_RETAINED`
receipt is untouched; this is recorded as a separate study.

---

## 1. The headline

**The acceptance policy is not what is blocking MLB opportunities. Price availability is.**

Of 111 distinct cohort events, 63 never reached the economic stage because no usable
moneyline price existed at the decision cutoff. Of the 48 that did reach it, 36 were
rejected by the 2.0-point adjusted-edge floor and 12 were selected. **Readiness, the
feature-completeness composite, starter evidence, lineup evidence, scratches and the
extreme-disagreement stop blocked exactly zero events at the cutoff.**

Two structural claims in the brief do not hold against the persisted contract, and this
changes what the matrix could possibly show:

- **V2.1 applies no composite total-readiness threshold.** 609 of its 748 qualified
  evaluations carry readiness below 70 (minimum 52.5, median 67.5). A threshold that is
  not applied cannot be lowered.
- **There is no feature-completeness critical veto.** `feature_completeness` is classified
  `DUPLICATE_DERIVED_GATE` and is deliberately excluded from the atomic hard-block
  whitelist. Across all 13,753 persisted control evaluations, **zero** were blocked by a
  model-required-input veto. A veto that does not exist cannot be excepted.

So R0, R1, R2, R3 and R4 apply identical rules to identical evidence. The declared 20-cell
matrix collapses to **4 distinct cells**, differing only in the adjusted-edge floor. No new
candidates were invented to fill the gap.

## 2. Cohort, coverage and the repaired outcome join

| Fact | Count |
|---|---|
| Distinct events (control operating window 2026-09-01 → 2026-09-08) | **111** |
| Unique selection opportunities (distinct event × market outcome at final pregame) | **73** |
| Repeated evaluation rows — final pregame | 2,596 |
| Repeated evaluation rows — all phases | 13,753 |
| Events with a valid forecast at the cutoff | 109 |
| Events with a moneyline price ever observed | 73 |
| Events with a **usable** (fresh, complete, two-sided) quote at the cutoff | **48** |
| Settled events (final score available) | **89** |
| Unsettled events (live or scheduled) | 22 |
| Events with an `is_closing`-flagged snapshot | **0** |

**The outcome join is repaired.** The previous study found no outcomes because it read
`game_final_outcomes`, the sealed certified table, which holds **nothing** for this window.
The operational score of record is the `games` row, and the selected-team join runs
`market_outcome.participant_team_id → game_teams → final score`. It is verified, not
assumed: for all 10 settled official positions the reconstructed score and the reconstructed
win/loss both match `position_results` exactly — **0 score mismatches, 0 result mismatches**.

Repeated evaluations are never treated as independent observations anywhere below.

## 3. Why games are rejected

### Sequential attrition (distinct events, at the decision of record)

| Step | Surviving | Lost |
|---|---:|---:|
| Distinct cohort events | 111 | — |
| Reached a final-pregame decision of record | 111 | 0 |
| Valid current forecast at the cutoff | 109 | 2 |
| Model-required inputs valid / governed fallback | 109 | **0** |
| Complete same-book two-sided moneyline quote | 70 | **39** |
| Quote fresh at the cutoff | 48 | **22** |
| Model and market probabilities available | 48 | 0 |
| Not extreme model–market disagreement | 48 | **0** |
| No unresolved scratch | 48 | **0** |
| Starters CONFIRMED or PROBABLE_STABLE | 48 | **0** |
| Lineup confirmed / adequate / allowed pending | 48 | **0** |
| Adjusted edge ≥ 2.0 pts (control floor) | 12 | **36** |
| Final QUALIFIED_BET at the cutoff | 12 | — |
| Official position created | **11** | 1 |

### Overlapping causes (measured independently of gate order)

The evaluator returns at its *first* failed gate, so `gates_passed` alone cannot show
overlap. Starter and lineup evidence are reduced unconditionally before the gate loop, and
price availability comes from the odds tables, so these are real overlaps:

- No moneyline price ever observed: **38**
- Priced, but no fresh complete two-sided quote at the cutoff: **25**
- No valid forecast at the cutoff: **2**
- Adjusted edge below the 2.0 pt floor: **36**
- Starter evidence ineligible: **0** — all 109 events with a forecast were
  `PROBABLE_STABLE` / `PROBABLE_STABLE` at the cutoff
- Lineup evidence ineligible: **0** — all 109 were `CONFIRMED_COMPLETE`

The 942 starter-blocked evaluation rows visible in the raw data are all *earlier* intraday
evaluations. By the cutoff, starters had stabilised in every case. That distinction is
exactly why the principal unit is the event, not the row.

### Today, kept separate

The 2026-09-08 slate (15 events) has **zero** usable quotes at any cutoff and zero
selections. Odds collection stopped at 2026-09-07 20:44. That is an ingestion state, not a
policy outcome. Historically priceable: 96 events, 48 with a usable quote, 12 selected.

### Feature completeness — corrected arithmetic and its actual effect

13/36 = 0.361111 coverage; 23 measured slots missing; four excluded structural placeholders
make the diagnostic list 27 entries. Under the scoring contract, feature completeness earns
**5 of 10** points (the stale factor gives half credit, not zero), bullpen 0 of 5, Statcast
0 of 5 — a **15-point** deficit against full readiness, **not 20**.

- **Score-only effect: 0 selections changed.** The control applies no readiness threshold,
  so a 15-point readiness deficit cannot change an acceptance decision.
- **Veto effect: none exists.** Quantified separately and measured at zero across every
  persisted evaluation.

## 4. The frozen matrix

Frozen before any candidate return was read. Every cell replays the **same persisted
decision of record** — the last final-pregame row at or before the cutoff, which is exactly
what the live position contract consumes. Side, bookmaker, price and timestamp are the ones
the live contract froze; only the edge floor moves. One position per market outcome, as
live.

| Cell | Selections | Settled | +/− vs control | Hit rate | Unit P/L | Unit return | Brier | Log loss | Staked P/L | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| R0–R4 @ 0.0175 | 13 | 12 | +1 / −0 | 0.417 | **+1.20** | +10.0% | 0.2741 | 0.7415 | −7.11 | 16.19 |
| R0–R4 @ 0.0150 | 14 | 13 | +2 / −0 | 0.385 | +0.20 | +1.5% | **0.2669** | **0.7271** | −8.64 | 17.71 |
| **R0–R4 @ 0.0200 (control)** | **12** | **11** | — | 0.364 | −0.25 | −2.3% | 0.2725 | 0.7384 | −7.14 | 16.19 |
| R0–R4 @ 0.0250 | 11 | 10 | +0 / −1 | 0.300 | −2.13 | −21.3% | 0.2686 | 0.7304 | −12.23 | 21.23 |

**Control replay reconciliation.** The replay reproduces every one of the 11 live positions
(0 missing). It selects one event the live ledger does not — see §6.

**Live ledger, kept separate from all hypotheticals:** 11 positions, $27.50 staked,
10 settled (4 W / 6 L), realised **+$1.88**. No hypothetical result was ever added to it.

### Marginal selections, at their actual decision-time prices

| Cell | Change | Event | Selection | Book | Odds | Adj. edge | Result |
|---|---|---|---|---|---:|---:|---|
| @0.0175 | added | 2026-09-05 SF @ NYM | SF ML | BetRivers | +145 | 0.0185 | **won** |
| @0.0150 | added | 2026-09-05 SF @ NYM | SF ML | BetRivers | +145 | 0.0185 | won |
| @0.0150 | added | 2026-09-01 ATH @ TEX | ATH ML | BetOnline.ag | +194 | 0.0158 | lost |
| @0.0250 | removed | 2026-09-03 ATH @ SEA | ATH ML | BetOnline.ag | +188 | 0.0221 | won |

**That is the whole matrix.** Three settled events separate four cells. A single game —
SF @ NYM on 2026-09-05 — decides whether 0.0175 outranks the control. Flip that one result
and the ranking reverses.

### Closing line value

No snapshot in the cohort carries the `is_closing` flag, so CLV uses the settlement
engine's own definition — the last same-book price strictly after the recommendation and at
or before first pitch — applied unchanged to live and hypothetical selections alike, and
never imputed. Coverage is about half: 6 of 12 control selections, 5 of 11 live positions.
Live mean CLV −0.15%; control cell mean −0.33%, 1 of 6 beat the close. Coverage this thin
supports no conclusion.

### Strict historical comparison (V1) — not pooled

`AISP_OPPORTUNITY_POLICY_V1`, 2026-08-28 → 2026-09-08, 169 distinct events, 3,654 final-
pregame rows: 1,529 PROVISIONAL_EDGE, 1,158 WATCH, 931 NO_BET, 36 EXTREME_DISAGREEMENT,
**0 QUALIFIED_BET, 0 positions**. Its readiness ≥ 70 gate and CONFIRMED-starter requirement
are both unsatisfiable in this configuration. V2.1 owns all 11 positions. Gen 1 results are
kept apart from V18, which holds no official decision authority.

## 5. Answers

**How many non-selections come from missing prices versus policy restrictions?**
Of 99 non-selections: **63 are availability** (38 never priced, 25 priced but no fresh
complete two-sided quote, plus 2 with no valid forecast — 63 events in total lack a usable
forecast+quote pair) and **36 are the edge floor**. Every other gate contributed zero.

**Does lowering the total readiness threshold matter, or does the separate veto dominate?**
Neither. There is no total-readiness threshold in the control and no feature-completeness
veto. Both were measured, not assumed. The binding constraint above the edge floor is
whether a price exists at all.

**Which extra selections do the lower edge thresholds create, and what happened?**
Exactly two, listed above: SF ML +145 (won) at 0.0175 and below, and ATH ML +194 (lost) at
0.0150. Raising to 0.0250 removes one winner, ATH ML +188.

**Which gates should remain hard?**
All of them. The forecast-validity, exact-identity, complete two-sided quote, quote
freshness, pregame cutoff and positive-adjusted-EV gates are integrity gates — each prevents
a decision from being priced against evidence that does not exist, and none of them is
responsible for the shortfall. The starter and lineup gates cost nothing in this cohort
(zero blocks at the cutoff) and should stay. The 0.15 extreme-disagreement stop blocked
nothing at the cutoff and remains cheap insurance. The **only** gate with a measurable
selection effect is the adjusted-edge floor, and the evidence cannot say what it should be.

**Is there enough evidence to nominate one prospective research candidate?**
Not for a policy change. One candidate — the 0.0175 floor, single change, everything else
identical — is registered as an **inert** specification for prospective observation only
(`candidate_spec.json`), explicitly pending an explicit later decision. It is deliberately
not proposed for activation: its entire retrospective support is one baseball game.

## 6. One concrete defect found

`_create_canonical_position` re-binds the frozen decision to its odds snapshot and refuses
unless the two decimal prices are equal within 1e-9. The decision column stores the price
rounded to four places, so a book price whose decimal expansion needs more digits fails the
identity check and a fully qualifying selection is silently dropped.

Exactly one cohort event is affected: **BOS ML, 2026-09-01, BetOnline.ag −115**, decision
1.8696 vs snapshot 1.8695652173913042, difference 3.48e-05. It cleared every policy gate at
the cutoff and never became a position. (It would have lost, so the ledger is unaffected in
realised terms.) 24 of 168 qualified final-pregame rows carry the mismatch, all on that one
outcome.

This is an arithmetic binding defect, not a policy rejection. **It was not fixed here** —
changing it creates positions, which this assignment does not authorise. It is reported for
an explicit decision. `app/services/shadow_experiment_service.py::_create_canonical_position`

## 7. Evidence limitations — stated plainly

- The control's whole operating window is 8 slate days, already examined by the earlier
  retained-policy study. **No genuinely unexamined later period exists**, so every result
  here is **exploratory**. No selection/evaluation split is claimed.
- One week cannot support a weekly-block inference at any number of bootstrap repetitions.
  None was run. The matrix separates on 3 settled events; the sensitivity is reported as the
  exact count of results that would have to flip (one).
- No cohort event has a certified `game_final_outcomes` row.
- No `is_closing` snapshot exists; CLV coverage is ~50%.
- `final_at` is null for every cohort game, so intraday settlement sequencing is unrecorded;
  the staking replay advances bankroll at slate-date boundaries, which the data does support.
- The 2026-09-08 slate contributes to availability counts only. No missing-price game
  produced an invented quote, return, opportunity or retrospective profit claim.

## 8. Prospective observation protocol (frozen)

Full detail in `study.json → prospective_protocol`.

- **Unit:** one distinct event, one final-pregame decision of record.
- **Candidate:** 0.0175 floor. **Control:** 0.0200, unchanged and still governing.
- **Primary metric:** unit-stake profit per settled selection. **Guardrail:** must not worsen
  Brier or log loss.
- **Minimum sample:** 30 settled *marginal* selections. Current: 1. At the observed rate
  (~1 marginal selection per 3 slate days) that is ~90 slate days.
- **Binding prerequisite:** restore odds ingestion. Also capture a genuine flagged closing
  price, persist `final_at`, and backfill certified outcomes.
- **Stop rules:** stop and report if the two still differ on fewer than 10 settled selections
  after 45 slate days; never re-run this window with more configurations.

## 9. Files

All under `outputs/mlb-acceptance-policy-study-v2/`:

| File | Contents |
|---|---|
| `cohort.json` (123 MB, local) / `cohort.json.gz` (6.6 MB, committed) | the immutable frozen cohort |
| `cohort_manifest.json` | extraction watermark, counts, SHA-256 `40b6e7a2…4e7fb667` |
| `policy_contract.json` | measured control contract + corrected completeness arithmetic |
| `waterfall.json` | attrition, first cause, overlapping causes, per-event detail |
| `matrix.json` | the frozen matrix, the collapse and its basis, the leaderboard |
| `replay.json` | control reconciliation, per-cell results, CLV |
| `marginal_ledger.json` | every event-linked added/removed selection |
| `study.json` | strict comparison, sensitivity, evidence limits, protocol, the defect |
| `candidate_spec.json` | the inert candidate, pending activation |
| `study_receipt.json` | this study's identity and boundary attestations |

Analysis code: `server/scripts/mlb_acceptance_policy_study.py` (export) and
`server/scripts/mlb_acceptance_policy_analysis.py` (analysis).
Regression test: `server/tests/test_mlb_acceptance_policy_study.py`.

## 10. One next decision

**Restore MLB odds ingestion, then re-run this study unchanged.** 63 of 99 non-selections
are price availability; no threshold in the frozen matrix moves more than two events. Until
a fresh two-sided quote exists at the cutoff for most of the slate, no acceptance threshold
can be evaluated, and more configurations will not create more evidence.

---

**MLB_ACCEPTANCE_CONTROL_RETAINED_AFTER_OUTCOME_STUDY**
