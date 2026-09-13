# V18 error-analysis evidence — AISP extraction and availability diagnosis

> **Superseded on the scoring question, 2026-09-11 22:49 ET.** The "zero scorable
> games" finding below was correct about `game_final_outcomes` and correct that a
> repair was needed. That repair has since landed: eligible V18 forecasts are now
> scored against the provenance-ranked FINAL state observations that already
> existed, and a scheduled job keeps scoring them. See `REPAIR_REPORT.md`. The
> inventory, the clock analysis and the readiness diagnosis in this document
> remain accurate.

**Study id** `AISP_MLB_V18_ERROR_ANALYSIS_EVIDENCE_V1`
**As of** 2026-09-12T01:39:53Z (2026-09-11 21:39 ET)
**Branch / HEAD** `claude/aisp-v18-and-price-coverage-v1` @ `a357435`
**Scope** AISP extraction, forecast/final provenance, evidence availability.
Statistical diagnosis, specialist selection and ensemble methodology belong to
Codex in Model Studio and are not attempted here.

---

## Lead

**Distinct V18 games that can be error-analyzed against an outcome right now: zero.**

31 V18 forecasts exist, covering 31 distinct MLB games, all identity-bound, all
published before their pregame deadline, all carrying full run distributions and
uncertainty. The forecast side of the evidence is in good shape and is exported.
The outcome side is empty, and not because the games have not been played — 17 of
the 31 have completed. It is empty because **the only finals store the V18
settlement gate reads has never received a 2026 row.**

Two independent gaps produce that, and neither is a data-quality problem:

1. **The label store is the wrong store.** `game_final_outcomes` — the table
   `app/shadow_import/mlb_v18_settlement.py` queries — holds 2430 rows spanning
   2025-03-18 to 2025-09-28, every one of them
   `RETROSPECTIVE_RECONSTRUCTION`. It is the *training-label* store, written only
   by `app/historical/persistence.py`. Nothing in the live 2026 path constructs a
   `GameFinalOutcome`. 2026 finals land instead on `games.home_score` /
   `games.away_score` and in `game_state_observations`, which the gate does not read.
2. **Nothing runs the settlement.** `settle_shadow_forecasts` is called from
   exactly one place, the manual CLI `server/scripts/mlb_v18_shadow.py`. No
   scheduler job invokes it. Even with labels present, no V18 forecast would be
   settled on its own.

**The readiness concerns are confirmed in part and corrected in part.** Two of the
three audited components have genuinely never ingested a value; the third has
ingested a value on every single forecast and is being misread. The measured cost
is 15 points per forecast, not 20 — and neither number is the largest gap in the
readiness formula.

Nothing was changed. No weight, threshold, policy, model authority, forecast,
decision, position or settlement was touched; no provider request was made; no
model was executed.

---

## 1. Inventory of real V18 evidence

### Populations, kept separate

| Population | Store | Rows | Distinct games | Numerical artifacts |
|---|---|---:|---:|---|
| **V18 MLB research shadow** | `shadow_forecasts` via MLB bundles | **31** | **31** | 1 (`44ec4f45…88d71c8b`), engine `mlb-v18-whole-game-a6e7e99122f1b60701a4728b` |
| **Gen 1 (funded)** | `forecasts` | 512 total, **470** since 2026-08-28 | 223 total | `asp_mlb_ensemble 2.0.0-gate2-08cca634c81b` |
| **NFL research shadow** | `shadow_forecasts` via NFL bundles | 2178 | 272 | 1 (`NFL_M1_XGBOOST` / `NFL_M1_2026_RESEARCH_SHADOW_V1`) |
| **NFL labs supplements** | `research_supplements` | 266 | 16 events | 4 study kinds across 3 engines |

These four never mix. The V18 export in this directory contains only the first row
of that table.

**On the "duration-research" population:** no MLB or V18 duration study exists in
this database. No table or column carries a duration concept, and no
`research_supplements` row is MLB. The only duration-related research artifact is
the `PACE_DURATION_0.95` scenario inside the NFL labs contract
(`app/forecast_exchange/nfl_labs_v1.py:204`), which lives in the NFL supplements
population above. Reported as not-found rather than mapped onto something adjacent.

### V18 imports and revisions

| Measure | Value |
|---|---:|
| MLB bundles imported | **29** (2026-09-07 19:55 ET → 2026-09-11 19:06 ET) |
| Forecast rows created | **31** |
| Distinct canonical games | **31** |
| Forecast revisions | **0** — every row is `snapshot_version = 1` |
| Alternate versions in the export | **0** |
| Bundles that contributed zero new forecasts | **18 of 29** |

Eighteen of twenty-nine imports added nothing. The daily-slate handoff is
cumulative — a bundle declaring `forecast_count: 11` re-presents games already
held — and the importer is correctly idempotent. **Repeated imports are not extra
games, and there is not a single forecast revision to reconcile.** One game, one
forecast, throughout.

### Games with eligible persisted revisioned finals

**0 of 31.**

`game_final_outcomes` contains zero rows with `official_date >= 2026-08-25`. Its
entire content is the 2025 reconstruction described above.

### Games currently unscorable, with exact reasons

All 31, under the single reason code `NO_ELIGIBLE_PERSISTED_REVISIONED_FINAL`.
Broken down by what the game itself was doing at export time:

| `games.status` at export | Games | What this means |
|---|---:|---|
| `FINAL` | **17** | Played to completion. The outcome exists in the world and on `games`, but not in the gated store. These unlock the moment a 2026 final is persisted with a revision. |
| `LIVE` | 10 | In progress at 21:39 ET. |
| `SCHEDULED` | 4 | Not yet started, or status lagging behind play. |

The 17 `FINAL` games are the immediately recoverable population: nothing about
them is uncertain except where their label is written.

One integrity note observed in passing, not acted on: game 824227 carried
`status = SCHEDULED` alongside a non-zero inline score at export time, so
`games.status` lags actual play for at least one game. This is one more reason the
inline score is not a safe substitute for a gated final.

### V18 experiment and authority state — read once

| Field | Value |
|---|---|
| Authority state | **ARMED** (not `ACTIVE`) |
| `effective_at` | 2026-09-12T00:00:00-04:00 (= 2026-09-12T04:00Z) |
| `execution_allowed_in_aisp` | `false` |
| `real_money_execution_allowed` | `false` |
| `gen1_fallback_allowed` | `false` |
| `historical_validation_under_review` | `true` |
| Experiment `is_active` | `false` |
| `boundary_capital` | `null` — no capital receipt taken |
| V18 decisions | **0** |
| Positions referencing a V18 forecast | **0** |

The boundary had not arrived at the as-of time (21:39 ET versus a 00:00 ET
opening), so there is no activation or capital receipt to report and no
discrepancy. Read once; no monitor installed; no wait.

---

## 2. The research export

### Declared rules, fixed before any outcome was read

| Rule | Value |
|---|---|
| Population | `shadow_forecasts` where bundle sport = MLB and `engine_id = MLB_V18_WHOLE_GAME` |
| Date floor | `event_date >= 2026-08-28`, applied in SQL |
| Sealed OOT window | `2026-08-14 .. 2026-08-27` excluded by a separate explicit predicate |
| Permanent exclusions | `823343`, `824235`, `822693`, excluded in the same SQL predicate |
| Primary forecast | max(`snapshot_version`, `available_as_of_utc`, `created_at`) per canonical game; all other versions go to `alternates` |
| Forecast horizon | as published; all four clocks retained separately |
| Policy cutoff | `scheduled_start_utc − 15 min` (`AISP_PRACTICAL_PREGAME_POLICY_V2_1`) |
| Final revision | highest `revision` in `game_final_outcomes` for the canonical game — the same gate the V18 settlement module uses |

Identities and eligibility are filtered **before** outcomes are touched. No
unrestricted outcomes query was run and then cleaned up afterwards: the exclusions
and the date floor are predicates in the population query itself, and the outcome
query only ever receives game ids that already survived them. The sealed
2026-08-14..27 window was never queried — and in the event it is moot, because no
V18 forecast exists before 2026-09-07.

### Two eligibility flags, deliberately not merged

| Flag | Games | Meaning |
|---|---:|---|
| **Scientific prospective** | **31 of 31** | Publication time at or before the policy cutoff, independent of when AISP imported it. |
| **Operational decision** | **0 of 31** | Import *and* an active, execution-allowed era, both at or before the cutoff. |

Every one of the 31 forecasts was published before its deadline. None was
operationally decision-eligible, for reasons the export records per game:

| Blocking reason | Games |
|---|---:|
| `MODEL_ERA_ARMED_NOT_ACTIVE` | 31 |
| `EXECUTION_NOT_ALLOWED_IN_AISP` | 31 |
| `NO_DECISION_ROW_EXISTS` | 31 |
| `IMPORTED_AFTER_POLICY_CUTOFF` | **4** |

The four late imports matter independently of the era, because they would still
have been ineligible after activation:

| gamePk | Date | Published before cutoff by | Imported *after* cutoff by |
|---|---|---:|---:|
| 823499 | 2026-09-10 | 80 min | 1044 min |
| 824550 | 2026-09-10 | 125 min | 1009 min |
| 823817 | 2026-09-11 | 2 min | 11 min |
| 824631 | 2026-09-11 | 82 min | 10 min |

All four were published in time and imported late — the 09-10 pair by roughly
seventeen hours, in a single catch-up import the next afternoon. **A late import
cannot create an eligible past decision, and it does not erase the earlier
publication.** Both facts are kept on the record; neither overwrites the other.
For the rest of the 2026-09-11 slate, import landed a median of about 2.5 hours
before cutoff, which is comfortable.

### Timing uncertainty, exported rather than smoothed

14 of 31 forecasts have `source_cutoff_utc == available_as_of_utc` — the evidence
cutoff and the publication time are the same stored value, so publication time is
**not independently evidenced** for them. All 14 are on 2026-09-07, 09-08 and
09-09; every forecast from 09-10 onward carries distinct clocks. Each record
states this in `clocks.timingUncertainty`, and the publisher's own caveat travels
with it:

> "Current archive reconstruction is not historical publication-vintage evidence."

Codex should treat the 14 collapsed-clock forecasts as weaker prospective evidence
than the 17 with separated clocks, and the export makes that partition mechanical.

### What each record carries

Identity (canonical game id, MLB gamePk, date, teams, explicit home/away
orientation, scheduled start) · forecast and artifact identity (forecast id, engine,
model artifact / manifest / feature-vector hashes, simulator id, version and seed,
bundle key and bundle sha256, contract version, model era) · all four clocks plus
the derived policy cutoff · both eligibility flags with blocking reasons ·
full-game and first-five win probabilities and expected runs · saved run,
total-run and run-differential distributions · uncertainty (simulation count,
Monte Carlo standard error, deterministic-replay verification, illegal-state and
non-termination counts) · the publisher's own readiness detail including lineup and
starter observation times and sources · AISP pregame evidence counts · the outcome
block (null with an explicit reason where the gate did not pass) · decisions and
positions as annotations · alternates · lineage.

Nulls are retained with reasons. Nothing was back-filled from current observations,
V18 was not re-run, and no historical distribution was generated.

**COL–DET paired measurement** was left entirely alone: no new paired score, no
alternative path to protected paired outcomes, no starter BF acquisition. It stays
with its existing Studio owner and scorer.

### Artifacts

| File | Bytes | sha256 (first 16) |
|---|---:|---|
| `v18_forecast_evidence.jsonl` | 305,024 | `d94eccf39c4e6bc6` |
| `v18_lineage_blobs.jsonl` | 16,428,560 | `c9701f935cd39372` |
| `omissions.json` | 9,226 | `c9c23f0f02c26c02` |
| `evidence_availability_audit.json` | — | see file |
| `manifest.json` | 3,432 | — |
| `reconciliation.json` | — | — |

The per-game `populationGeometry`, `playGeometry` and `sequentialPath` payloads are
94% of the raw volume, so they live in the sidecar keyed by `shadowForecastId`;
each primary record keeps a sha256 and byte count for its blobs under
`lineage.blobReferences`. The primary export therefore streams in 305 KB.

### Reconciliation

`v18_export_reconcile.py` re-derives the population independently and asserts the
invariants. **14 checks, 14 passed, 0 failed** — artifact hashes, game-set equality
against the database, primary-plus-alternates accounting for every stored version,
absence of every excluded gamePk, absence of any sealed-window date, the date floor,
schema completeness, both eligibility flags present, and — the one that matters
most here — **no score payload present on any game that did not pass the
eligible-final gate.**

---

## 3. Evidence availability — can missing inputs explain this?

Measured across **470** Gen 1 forecasts since 2026-08-28 carrying stored readiness
components, under the implemented formula
`score = 100 × Σ(weight × status_factor) / Σ(weight)` with factors
ready 1.0, stale 0.5, missing 0.0, not-applicable 1.0, and the `GATE2_WEIGHTS` map
whose weights sum to 100.

### The carried-forward claim: partly confirmed, partly wrong

> *"feature_completeness, bullpen and statcast had never ingested any value,
> costing 20 weight points."* (audit of 2026-09-08)

| Component | Weight | ready | stale | missing | Avg points lost | Verdict |
|---|---:|---:|---:|---:|---:|---|
| `statcast` | 5 | 0 | 0 | **470** | **5.00** | **Confirmed.** Table has never held a row. |
| `bullpen` | 5 | 0 | 0 | **470** | **5.00** | **Confirmed.** Table has never held a row. |
| `feature_completeness` | 10 | 0 | **470** | **0** | **5.00** | **Corrected.** Populated on every forecast; `STALE`, never `MISSING`. |

**Measured cost of the three: 15.0 points per forecast, not 20.**
`feature_completeness` is not an un-ingested field. It is computed on every
forecast and reports a real measured value — typically around 36% coverage against
a 60% threshold — which makes it `STALE` and scores it **half** its weight. Calling
it missing overstates the cost by 5 points and, more importantly, points remediation
at the wrong thing: the field works, its inputs are thin.

Note also that the weight-equals-points arithmetic holds only because this weight
map happens to sum to 100. The formula normalizes by the sum of active weights, so
that identity is a coincidence of the current map, not a property to rely on.

### The gap nobody was counting

| Component | Weight | ready | stale | missing | Avg points lost |
|---|---:|---:|---:|---:|---:|
| **`lineups`** | 10 | 46 | 0 | **424** | **9.02** |
| `odds` | 10 | 172 | 56 | 242 | 5.74 |
| `statcast` | 5 | 0 | 0 | 470 | 5.00 |
| `bullpen` | 5 | 0 | 0 | 470 | 5.00 |
| `feature_completeness` | 10 | 0 | 470 | 0 | 5.00 |
| `weather` | 10 | 193 | 253 | 24 | 3.20 |
| `pitcher_confirmation` | 5 | 0 | 438 | 32 | 2.67 |
| `sportsbook_count` | 5 | 228 | 0 | 242 | 2.57 |
| `probable_pitchers` | 10 | 438 | 0 | 32 | 0.68 |
| `schedule` | 10 | 465 | 5 | 0 | 0.05 |
| `roster` | 5 | 470 | 0 | 0 | 0.00 |
| `team_stats` | 15 | 470 | 0 | 0 | 0.00 |

`lineups` is missing on **424 of 470** forecasts and costs **9.02** points on
average — more than any of the three audited components and nearly two-thirds of
all three combined. `lineup_observations` holds 23,698 rows and is actively
written, and the V18 games all have both clubs' lineups, so this is a timing or
resolution question within the Gen 1 path rather than an absent source. It is the
single largest lever in the readiness score and it was not on the list.

### Is readiness a structural cap?

**Yes, and a severe one.** Only **71 of 470 forecasts (15.1%)** reach the
threshold of 70. Scores run from 35.0 to a maximum of **82.5** — no forecast in the
window has ever exceeded 82.5, because `statcast` (5) + `bullpen` (5) +
`feature_completeness` at half weight (5) remove 15 points from every forecast
before any live evidence arrives, and `lineups` usually removes 10 more.

This is stated as measurement, not as an argument for moving the threshold. The
2% edge threshold and the 70 readiness floor were not changed, and no reduction is
recommended here; creating more decisions is not a goal of this audit.

### Field-by-field

| Field | Producer | Storage | Missingness kind | Role |
|---|---|---|---|---|
| `feature_completeness` | `services/feature_snapshot_service.py` | `game_feature_snapshots.feature_completeness`, mirrored to `forecasts.readiness_components` | **Proxy below threshold** — populated, measured, below 60% | AISP readiness input **and** Gen 1 model input |
| `bullpen` | no live 2026 ingester | `bullpen_availability_observations` (has `game_id`), `bullpen_features` — both **0 rows, ever** | **Absent observations** | AISP readiness input and Gen 1 model input |
| `statcast` | no live 2026 ingester | `statcast_player_observations` — **0 rows, ever**, and **team/player-scoped with no `game_id` column** | **Absent observations**, plus a shape mismatch | AISP readiness input and Gen 1 model input |

Two applicability facts that matter before anyone proposes a fix. First, both
`statcast` and `bullpen` require **≥70% coverage on both clubs** to reach `READY`
(`services/forecast_service.py`), and `bullpen` additionally requires non-null
`home_/away_bullpen_availability` feature values — so partial ingestion would score
nothing at all. Second, `statcast_player_observations` has no `game_id`; it can
only ever be joined to a game through its clubs, which is a mapping-shape question
that has to be answered before ingestion volume means anything.

### Confirmed model-input gaps versus operational readiness gaps

These are not the same thing, and for V18 the distinction is decisive.

All 31 V18 games have **both clubs' lineups (31/31)**, **both clubs' starters
(31/31)** and a **feature snapshot (31/31)** stored in AISP. Zero have a bullpen
observation. But V18's own `readiness_detail` cites exactly what it used — CONFIRMED
lineups and PROBABLE starters from `MLB_STATS_API`, with observation timestamps —
and reports `is_ready = true`, `readiness_score = 1.0` against its own threshold of
1.0 on every forecast. **It does not cite bullpen or Statcast features at all.**

So the empty `bullpen` and `statcast` tables are a confirmed **AISP operational
readiness gap** affecting the Gen 1 path. They are **not** evidence that V18 lacked
those inputs. Whether V18's estimator consumes bullpen or batted-ball features is a
Model Studio question that AISP's tables cannot answer, and this report does not
guess at it.

### Historical availability: unknown

No timestamped archive of readiness inputs *as they stood before* a past forecast
was located. Stored `readiness_components` describe the state at generation time
only. What was knowable at an earlier decision time therefore cannot be
reconstructed from this environment, and is marked unknown rather than inferred
from current observations.

One measurement caveat: `unavailable_reason` was added to the component structure
after this window opened, so reason codes are present on only **64 of 470**
forecasts. Absence of a code is not absence of a gap — the statuses, which are
present throughout, carry the finding.

---

## 4. What this means for the ensemble-versus-specialist decision

The honest answer to "where does V18 repeatedly miss" is that **AISP cannot yet
support that question with a single scored game**, and the reason is an
infrastructure gap, not a modelling one. The forecast side is complete and clean:
31 games, full distributions, verified identity, publication before deadline in
every case. The label side is not connected.

What would change that, in order of leverage:

1. **Persist 2026 finals into the gated store.** 17 games become analyzable
   immediately, and the population then grows by roughly a slate a day. This is the
   whole difference between zero and a working sample. It is a production change and
   is deliberately **not** made here.
2. **Schedule the settlement.** `settle_shadow_forecasts` runs only by hand today.
3. **Then reassess the readiness question.** Until there are labels, no amount of
   readiness work tells anyone whether V18 misses on bullpen-heavy games or anywhere
   else.

Note that the readiness cap and the V18 error question are largely **independent**.
Readiness gates Gen 1 qualification and therefore price coverage and decision
volume; V18 publishes on its own gate and is already at full readiness by its own
measure. Fixing readiness would not have produced a single additional V18 scored
game.

Statistical significance, specialist selection and ensemble methodology are Codex's
to determine. With zero scored games, none of those questions is answerable yet
regardless of method — which is itself the most useful thing this extraction
establishes.

---

## 5. Reproduce

```bash
python server/scripts/v18_error_analysis_export.py
```
```bash
python server/scripts/v18_evidence_availability_audit.py
```
```bash
python server/scripts/v18_export_reconcile.py
```

All three are read-only, take no provider request and execute no model. The first
two accept `--out DIR`, the third `--dir DIR`. Re-running after a 2026 final is
persisted will populate the `outcome` block with **no schema change** — the fields
are already present and null with a reason.
