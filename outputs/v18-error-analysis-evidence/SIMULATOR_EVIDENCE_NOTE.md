# V18 simulator evidence — field and coverage note

**For** Model Studio (Codex) · **From** AISP · **As of** 2026-09-12
**Exchange** `C:\Users\james\asp-model-exchange\mlb-v18-simulator-evidence-v1\current.json`
**Extends** `AISP_V18_ERROR_ANALYSIS_EVIDENCE_V2` — same directory, no new framework.

Read this before choosing an implementation. The point of the note is the
**omissions**: what AISP does not hold is what will decide which simulator
improvement is buildable without an acquisition project.

---

## What you can build on today

### 1. Pregame pitcher context — 2026, usable as covariates

`pregame_pitcher_context.json`, one record per V18 forecast in the population.

`starter_observations` turns out to carry the workload fields directly:

| Field | Meaning |
|---|---|
| `probablePitcherId` / `confirmedPitcherId` | starter identity, before and after club confirmation |
| `confirmationState` | how firm that identity was at the cutoff |
| `throws` | handedness |
| `daysRest` | rest before this start |
| `pitchesPreviousDay` | prior-day pitch count |
| `pitchesLastStart` | pitch count in the previous start |
| `workload` | the provider's workload block |

**The gate**: only observations whose own `available_as_of` (falling back to
`observed_at`) precedes **that forecast's own decision cutoff** are counted or
exported. A later observation of the same starter is not in the record, because
it was not knowable when the forecast was made.

### 2. Pitcher appearance history — 2025, **labels only**

`pitcher_appearance_history.jsonl` — **20,870 appearances, 873 pitchers**,
2025-03-18 to 2025-09-28, from `player_game_history_observations` (Development
scope). 4,860 starts and 16,010 relief appearances.

Per appearance: `battersFaced`, `outs`, `numberOfPitches`, `inningsPitched`,
`strikeOuts`, `baseOnBalls`, `hits`, `homeRuns`, `earnedRuns`, `gamesStarted`,
plus `eventAtUtc`, `evidenceEndAtUtc`, `eligibleAtUtc`, `retrievedAtUtc`,
`availabilityBasis`, `evidenceClassification`, `qualityTier`, `revision` and
`payloadChecksum`.

> **These are labels, not covariates.** Every record carries
> `use: LABEL_ONLY_AUTHORIZED_HISTORICAL_EVALUATION` and
> `notAPregameCovariate: true`. A row whose `evidenceEndAtUtc` falls after a
> forecast's cutoff cannot be that forecast's pregame input. Actual reliever
> usage and PA outcomes are available for authorized historical evaluation and
> for nothing else.

### 3. Same-cutoff market comparison

`market_comparison.json`. See below.

---

## Omissions — the part that matters

**Five relations hold zero rows.** Not thin, not stale: empty.

| Relation | Consequence |
|---|---|
| `pitching_features` | no aggregated pitcher features exist in AISP |
| `player_game_stats` | no per-game player stat rows in the current path |
| `player_talent_estimates` | no talent priors |
| `pitch_events` | **no pitch-level data at all** — a pitch-level transition model cannot be built from AISP |
| `bullpen_availability_observations` | reliever workload is unavailable as a pregame covariate |
| `statcast_player_observations` | batted-ball quality unavailable; also team/player-scoped with no `game_id` |

**And the scope gap that constrains everything:**
`player_game_history_observations` covers **2025 only**. There is no 2026
per-appearance history, so **within-season pitcher usage for the current slate
cannot be derived from AISP**. Current-season usage is limited to what
`starter_observations` carries at the cutoff — days rest, previous-day pitches,
last-start pitches.

The practical reading: a **starter-duration / transition** improvement is
supportable now, fitting on 2025 appearances and applying 2026 starter workload
fields at the cutoff. A **bullpen-composition** or **pitch-level** improvement
is not supportable from AISP data as it stands.

---

## Market comparison — coverage first

| Measure | Count |
|---|---:|
| Forecasts in the population | 33 |
| With a complete two-sided quote at their own cutoff | **20** |
| Also carrying a scored outcome (paired) | **18** |
| No quote known by cutoff inside the freshness window | 13 |

**Rules, frozen before any performance number was computed:**
decision cutoff = scheduled start − 15 min; quote must have been *knowable* by
that cutoff and still inside the 240-minute freshness window at that instant;
one book, complete two-sided, chosen by closest-in-time to the cutoff, ties by
lower overround then by name; margin removed with the repository's documented
proportional method (`forecast_exchange.pricing.no_vig_two_sided`). Sides are
never combined across books. **Closing prices are excluded** — they are later
information and belong to a separate comparison.

**Paired game-level differences, model minus market (negative favours the model):**

| Population | Games | Mean Δ log loss | Model better | Mean Δ Brier |
|---|---:|---:|---:|---:|
| Operationally eligible | 14 | **+0.0122** | 6 / 14 | +0.0060 |
| Scientific only (late import) | 4 | −0.0093 | 2 / 4 | −0.0064 |
| Verified prospective | 17 | +0.0214 | 7 / 17 | +0.0099 |
| Qualified diagnostic | 1 | −0.2317 | 1 / 1 | −0.1096 |

On the operationally eligible set the no-vig market is very slightly ahead of
V18. **This is descriptive on 14 games. It is not a significance test, not a
returns measure, and nothing here was used to tune selection or the unchanged
2% threshold.** The qualified-diagnostic row is a single game and should not be
read as a result at all.

---

## Protected populations — untouched

- gamePks **823343**, **824235**, **822693** are excluded as SQL predicates on
  the population, before any outcome is read.
- The **2026-08-14 .. 2026-08-27** Final OOT window stays sealed by a separate
  predicate.
- The **COL–DET duration pilot** keeps its own outcomes and authority with its
  existing Studio owner. Nothing here scores or re-derives them.
- `HISTORICAL_REPLAY_RESEARCH_SHADOW` rows stay refused by the research scorer;
  the scheduled scorer is unchanged.

## Verified versus declared

For the imported ensemble candidate `665b8d70…545ef91`, AISP verified the
exchange documents it can hash and the baseline values it already stores. The
transition artifact, treatment manifest, frozen checksums and the duration
pilot's fitted hazard are **producer-declared and recorded, not independently
verified** — AISP holds no counterpart for them. Please do not read the import
receipt as "all hashes verified".
