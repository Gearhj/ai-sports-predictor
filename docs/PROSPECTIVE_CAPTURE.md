# Prospective research capture

**Status: `DESIGN_BUILT_NOT_ENABLED`**

Built, tested, committed, and **off**. `PROSPECTIVE_CAPTURE_ENABLED` defaults to
`false`; while it is off the exporter reads no row, opens no file and calls no
provider. There is **no scheduled job** for it, by design.

| | |
|---|---|
| Module | `server/app/research_exports/prospective_capture.py` |
| Tests | `server/tests/test_prospective_capture.py` |
| Flag | `Settings.prospective_capture_enabled` (default `False`) |
| Lane | `Settings.prospective_capture_dir` (default `~/asp-model-exchange/prospective-research-capture-v1`) |
| Callable | `run_prospective_capture(session, settings, *, now, window_start, window_end, leagues, directory)` |
| Manual entry point | `python -m app.research_exports.prospective_capture` |
| Scheduler entry | **none** — `app/jobs/scheduler.py` does not reference this module, and a test asserts it |

## What it is for

Model Studio (Codex) owns a forthcoming frozen contract for a **prospective**
comparison of three things across **every eligible priced game**:

1. the **unchanged** model,
2. the **market**, and
3. a fixed **50/50 blend**.

AISP owns exactly two jobs in that comparison: supplying the **inputs that were
genuinely knowable at the checkpoint**, and supplying **authentic outcomes**.
AISP does not own the comparison. Nothing in this module computes a blend,
chooses a threshold, scores a forecast or optimises anything — and a guard
refuses a record that contains a field shaped like any of those.

The contract is **not frozen yet**. The record shape below is therefore
provisional; `RECORD_FIELDS` / `EXCLUSION_FIELDS` are single ordered tuples so
the shape can be adapted in one place, and every record carries
`contractStatus: "DESIGN_BUILT_NOT_ENABLED_AWAITING_MODEL_STUDIO_FREEZE"`.

## Coverage

The universe is the **slate**, not the games AISP bet and not even the games it
priced: one record per eligible game per checkpoint evaluation, including games
that were **DECLINED** and games that were **UNFUNDED**. A comparison that only
sees funded games is a comparison of AISP's selection, not of the model.

A game the exporter cannot capture is **never silently omitted**. It produces an
`AISP_PROSPECTIVE_CAPTURE_EXCLUSION_V1` record naming the reason, so the
coverage denominator is always recoverable. Current exclusion reason codes:

```
NO_FINAL_PREGAME_DECISION_AT_CHECKPOINT
ALL_FINAL_PREGAME_DECISIONS_AFTER_CHECKPOINT
NO_PROBABILITY_BEARING_DECISION_CHANNEL
AMBIGUOUS_PROBABILITY_BEARING_DECISION_CHANNELS
CONTROLLING_FORECAST_NOT_AVAILABLE_AT_CHECKPOINT
DECISION_NAMES_NO_ODDS_SNAPSHOT
NAMED_ODDS_SNAPSHOT_MISSING
NAMED_ODDS_SNAPSHOT_NOT_VISIBLE_AT_CHECKPOINT
NAMED_MARKET_IS_NOT_A_MONEYLINE
NAMED_MARKET_BELONGS_TO_ANOTHER_GAME
SELECTED_SIDE_NOT_RESOLVABLE
OPPOSING_MARKET_OUTCOME_MISSING
NO_SAME_BOOK_OPPOSING_LEG_VISIBLE_AT_CHECKPOINT
OPPOSING_LEG_DISAGREES_WITH_RETAINED_OPPOSITE_PRICE
LEG_PRICE_IS_NOT_VALID_AMERICAN_ODDS
DECISION_LACKS_DISTINCT_QUALIFICATION_AND_EXECUTION_OUTCOMES
```

An **unresolved outcome is not an exclusion**. The checkpoint inputs are still
valid; the record is written with `outcome.resolved: false` and a stated basis,
and when the final arrives a *new* record is written beside it.

## Record schema

`recordType: AISP_PROSPECTIVE_CAPTURE_V1`, `schemaVersion: "1"`. Top-level keys:

| Key | Contents |
|---|---|
| `identity` | `gameId`, `decisionId`, `decisionChannelExperimentId`, `positionId`, `forecastRowId`, `forecastRowTable`, `forecastIdentity` (content-addressed), `forecastIdentityBasis`, `forecastIdentityAgreesWithRow` |
| `context` | `sport`, `league`, `engineId`, `engineVersion`, `modelArtifactSha256`, `snapshotType`, `snapshotVersion`, `policyVersion` (+ basis), `stakingVersion` (+ basis), `scheduledStartUtc` |
| `checkpoint` | `checkpointAtUtc` (+ basis), `forecastSnapshotPhase`, `forecastAvailableAsOfUtc`, `forecastCutoffUtc`, `forecastPublishedAtUtc`, `forecastImportedAtUtc`, `createdAtNotUsed` |
| `orientation` | `orientationBasis`, `homeTeamId`/`homeAbbreviation`, `awayTeamId`/`awayAbbreviation`, `selectedSide`, `selectedSideBasis`, `selectedTeamId`, `probabilityOrientation` |
| `probabilities` | `forSide`, and four `{value, basis, definition}` blocks: `rawModelProbability`, `policyAdjustedProbability`, `marketNoVigProbability`, `retainedMarketProbability` |
| `market` | `marketType`, `period`, `sportsbookKey`/`sportsbookName`, `sameBook` (+ basis), `opposingLegBasis`, `selectedLeg`, `opposingLeg`, `deVig` |
| `status` | `qualificationStatus`/`qualificationReason`, `fundingStatus`/`fundingReason`, `positionStatus`/`positionResult`, `postStakingRecommendation`(+reason), and the definition of each |
| `ledgerSettlement` | the **only** place money may appear |
| `outcome` | `resolved`, `source`, `sourceMode`, `sourceConfirmedTerminalStatus`, `terminalStatusBasis`, `homeScore`, `awayScore`, `outcomeRevisionIdentity` (+ basis), `completionTimeUtc`, `completionTimeBasis`, `sourceLastUpdatedRaw`, `retrievedAtUtc`, `observationId` |
| `clockMeanings` | one sentence per clock, in the record itself |

Each leg (`selectedLeg` / `opposingLeg`) carries: `side`, `oddsSnapshotId`,
`marketOutcomeId`, `americanOdds`, `decimalOdds`, `observedAtUtc` (+ basis),
`captureClockUtc` (+ basis), `source`, `sourceMode`.

`deVig` shows the arithmetic in full: `selectedDecimalOdds`,
`opposingDecimalOdds`, `selectedImpliedProbability`, `opposingImpliedProbability`,
`overround`, `selectedNoVigProbability`, `opposingNoVigProbability`, plus
`formula` and `basis`.

Exclusion records carry `identity`, `context`, `checkpoint`, `exclusion`
(`excluded`, `reasonCode`, `reasonDetail`, `meaning`) and `clockMeanings`.

### Three distinct status fields

`qualificationStatus`, `fundingStatus` and `positionStatus` answer three
different questions and are never collapsed. A **value-qualified game with no
position** reads:

```
qualificationStatus = "QUALIFIED"       fundingStatus = "NOT_EXECUTED"
qualificationReason = "ALL_GATES_PASSED"  fundingReason = "STAKING_OR_EXPOSURE_CAP"
positionStatus      = "NO_POSITION"       positionId    = "NOT_APPLICABLE"
```

That combination is a real, capturable row, not an error. Conflating the first
two is what let three exposure-capped games persist as `QUALIFIED_BET` on
2026-09-20 while the job receipt said `DECLINED`.

### Clocks

Every clock is converted with `.astimezone(UTC)` *before* formatting. The
session timezone is `America/New_York` and psycopg returns ET-aware
timestamptz; formatting one with a literal `Z` without converting is the bug
that cost six provider objects on 2026-09-20.

`created_at` is **never** used as a clock — in PostgreSQL it is the
transaction-**start** stamp. The record states this in `checkpoint.createdAtNotUsed`.

### Outcomes

- **NFL** reads `nfl_final_observations`, which is the NFL source of record.
  `game_final_outcomes` is MLB-only (2609 rows, 0 NFL) and is never touched here.
- Everything else reads the sport-neutral `game_state_observations`.

**Source-confirmed FINAL STATUS and genuine COMPLETION TIME are separate
fields.** The NFL source has no completion-time column, so NFL records carry
`completionTimeUtc: null` with
`completionTimeBasis: "SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_UNAVAILABLE"` —
the same accepted basis string the V18 V2 outcome lane uses (pinned equal by
test). `retrieved_at` and the feed's `source_last_updated` string are carried as
evidence and are never promoted into a completion time.

## Fields that could NOT be populated from retained data

Stated explicitly rather than invented:

- **`policyAdjustedProbability` is null for the NFL research channel.** That
  channel retains no adjusted probability, and the code deliberately does **not**
  fall back to the raw probability — doing so would assert an adjustment that
  was never recorded. Basis: `NOT_RETAINED_BY_THIS_DECISION_CHANNEL`. The MLB V2
  channel does retain `adjusted_probability` and populates it.
- **`retainedMarketProbability` is null for NFL** (basis `NOT_RETAINED`). The
  derived `marketNoVigProbability` is always present, computed from both
  captured legs.
- **`outcome.completionTimeUtc` is always null for NFL** — see above.
- **`policyVersion` / `stakingVersion` are null for an unfunded game whose
  channel retains no `policy_version` in evidence** (basis `NOT_RETAINED`). They
  come from the position row when one exists, from decision evidence otherwise.
- **The opposing leg's row ids are not retained by any decision channel.** The
  NFL channel retains the opposing leg's *price* (`evidence.selection
  .opposite_american`) but not its `market_outcome_id` / `odds_snapshot_id`; MLB
  retains neither. The exporter therefore **re-derives** the opposing leg as the
  latest same-book, same-source quote visible at the checkpoint, and where the
  price was retained it cross-checks — a mismatch is the exclusion
  `OPPOSING_LEG_DISAGREES_WITH_RETAINED_OPPOSITE_PRICE`, never a silent
  substitution.
- **`overtime`, `week` and `game_type`** are present in
  `nfl_final_observations` but are not currently emitted; say the word and they
  are a one-line addition.

## What Codex must confirm for compatibility

1. **Record type and version.** Is `AISP_PROSPECTIVE_CAPTURE_V1` /
   `schemaVersion "1"` acceptable, or does the frozen contract name its own?
2. **The exclusion record.** Does the reader accept a second record type in the
   same lane, or must exclusions go to a sibling directory? AISP will not drop
   them either way.
3. **`positionId: "NOT_APPLICABLE"`.** A literal, not a null, so "no position"
   cannot be read as "field missing". Confirm the reader does not parse it as a
   uuid.
4. **Null `completionTimeUtc` with basis
   `SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_UNAVAILABLE`.** This is the
   already-accepted amendment shape. Confirm the frozen prospective reader
   permits it too — the V1 outcome lane's reader did **not**, which is why the
   V2 lane exists.
5. **Probability orientation.** Every probability is stated **for the selected
   side**, not for home. Confirm, or ask for home-oriented and AISP will emit
   both.
6. **Two-way no-vig is conditional on no tie.** The moneyline pair spans "this
   side wins given somebody wins". Comparing it against an **unconditional**
   three-way model probability compares two different quantities. Confirm which
   basis the comparison uses; AISP will emit the conditional model probability
   alongside if needed.
7. **Which checkpoint.** `checkpointAtUtc` is the deciding row's
   `recommended_at`. If the contract wants a fixed T-minus target instead, say
   which — AISP will emit both rather than relabel one.
8. **Decision channel.** The probability-bearing FINAL_PREGAME channel is
   selected explicitly by `experiment_id`, never by recency; two probability-
   bearing channels is an exclusion, not a tie-break. Confirm that refusing is
   preferred to guessing.
9. **Filenames.** `records/<gameId>/<sha256-of-canonical-bytes>.json`, plus the
   hash-chained `capture-ledger.jsonl`. Confirm the reader can enumerate a
   directory rather than expecting one file per game.

## The six hard rules, and where each is enforced

| Rule | Enforcement |
|---|---|
| 1. Append-only | `write_once` (exclusive create, then read-only); filename **is** the content hash; `_write_once_same` adopts identical bytes and raises `RECORD_EXISTS_WITH_DIFFERENT_BYTES` otherwise; a later evaluation hashes differently and lands *beside* the earlier record |
| 2. Frozen pregame view | `_visible()` bounds every pregame read by the checkpoint; the forecast and selected leg are the exact rows the decision **named**, then bound-checked; the opposing leg is the latest same-book quote **visible at the checkpoint**; `build_record` takes the checkpoint as an argument and reads no clock |
| 3. No betting effect | `v18_market_pilot_outcomes.read_only` (confirmed `SET TRANSACTION READ ONLY`, always rolled back, refuses a dirty session) **plus** `_no_write_guard`, which counts `shadow_experiment_decisions` and `official_asp_positions` rows either side and refuses on any change — dialect-independent, so it bites on SQLite too, where the read-only statement is a no-op |
| 4. Scoring separate from money | money may appear only under `ledgerSettlement` (`assert_money_is_quarantined`); blend/score/threshold/optimisation-shaped keys are refused **anywhere** (`assert_no_derived_comparison`); nothing computes a blend or a score |
| 5. No new scheduler | no scheduler id, no `add_job`, not imported by `app/jobs/scheduler.py`; a test asserts the flag-on scheduler registers nothing and that the module contains neither `SCHEDULER_ID` nor `add_job` |
| 6. Canonical JSON | `v18_market_pilot.canonical_bytes` (sorted keys, no whitespace); **no export clock** in the record — clocks live in the ledger, which is not the record |

## Running it manually (once the contract is frozen and the flag is turned on)

```
python -m app.research_exports.prospective_capture \
    --window-start 2026-09-21T00:00:00Z \
    --window-end   2026-09-22T00:00:00Z \
    --leagues NFL,MLB
```

It prints a JSON summary: universe size, captures, exclusions, how many files
this run created, and each record's sha256. With the flag off it prints the
inert summary and touches nothing.
