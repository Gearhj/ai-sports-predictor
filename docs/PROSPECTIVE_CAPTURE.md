# Prospective model–market capture — mapped to the FROZEN contract

**Status: `MAPPED_TO_FROZEN_CONTRACT_BUILT_NOT_ENABLED`.**
`PROSPECTIVE_CAPTURE_ENABLED` defaults to `false` and is `false` in the deployed
checkout. Nothing is registered with the scheduler. No provider is called, no
object is spent, no production record, policy, threshold, staking rule or
selection gate is touched by anything described here.

| | |
| --- | --- |
| Module | `server/app/research_exports/prospective_capture.py` |
| Tests | `server/tests/test_prospective_capture.py` |
| Record type | `MODEL_MARKET_OBSERVATION_V1` |
| Contract bound | `CONSUMER_EXPORT_CONTRACT.md` sha256 `e0ee599e6c6efeea3ab7699f4c6682003b9ac23e6acc458641f719bfb2ac9fc5` |
| Protocol bound | `FROZEN_PROTOCOL.json` sha256 `fcb530fc1d12d20f688ff1859fba1b4c5bb63115719cd3e629d8fbf05f10a9e3` |
| Freeze instant | `2026-09-21T18:32:03.7468106Z` |
| Invocation | `PREPARED_NOT_ACTIVATED`, on the existing `asp-rx-v18-market-pilot-outcomes-v2` owner |

Both hashes are **re-computed from the files on disk** by
`verify_contract_files()` before anything relies on them. A record binds a hash it
has checked, never one it has merely been told.

## The first genuine record uses only post-freeze evidence

This is enforced, not asserted. `evaluate_game` compares the game's fixed T-15
checkpoint against `FREEZE_INSTANT` and refuses anything earlier with the
immutable code `PRE_FREEZE_EVIDENCE_NEVER_ENROLLED`. The pre-freeze
`mlb_market_comparison_167.csv` work, and everything else built before
`2026-09-21T18:32:03.7468106Z`, is development evidence and cannot enrol through
this lane even if someone widens the window by hand.

Enrollment also closes at `2026-10-01T00:00:00Z`
(`CHECKPOINT_AFTER_ENROLLMENT_END`).

## Codex's nine dispositions, and where each is enforced

1. **Record type and hash binding** — `CONTRACT`, `CONTRACT_SHA256` and
   `PROTOCOL_SHA256` are in every record; `verify_contract_files()` re-hashes the
   two files and refuses a mismatch.
2. **An exclusion is not a second file** — there is ONE builder,
   `build_observation`, and one record shape. A game that cannot be priced or
   admitted carries `admission.state = UNPRICED` or `EXCLUDED` in that same
   record, with the same identity and clock envelope. The old
   `AISP_PROSPECTIVE_CAPTURE_EXCLUSION_V1` type is gone.
3. **`positionId` is JSON `null`** — `decision.positionId` is `str` or `None`.
   The `NOT_APPLICABLE` sentinel is refused anywhere in the record by
   `assert_no_not_applicable_sentinel`.
4. **Null genuine completion time** — only under
   `SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_UNAVAILABLE`, and only beside the
   exact source, revision identity and source-confirmed status. NFL's source of
   record has no completion column, so no retrieval, admission, availability or
   feed clock is ever promoted into `genuineCompletionAtUtc`.
5. **Home, away AND tie, as published** — `forecast.homeWinProbabilityRaw`,
   `awayWinProbabilityRaw` and `tieProbabilityRaw`, in HOME orientation, from the
   controlling forecast row. MLB's tie is a stated `0.0`. Selected-side values
   live only under `forecast.selectedSideAdditional`.
6. **NFL decisive-conditioned channel** — `decisiveConditionedHomeProbability` =
   `pHome / (pHome + pAway)`, separately named, alongside the published three-way
   vector. Official ties refund; `outcome.tieDisposition` and `outcome.isTie` say
   so, and `market.conventions.tieTreatment` records the market rule.
7. **Fixed T-15** — `checkpoint_for(starts_at) = starts_at - 15 minutes`, and it
   takes no decision clock. `decision.recommendedAtUtc` is a separate field.
8. **Ambiguity is an exclusion** — two probability-bearing decision CHANNELS
   (distinct `experiment_id`) at the same tier produce
   `AMBIGUOUS_PROBABILITY_BEARING_DECISION_CHANNELS`. Recency never breaks the
   tie.
9. **Layout** — `records/<gameId>/<sha256>.json` plus the hash-chained append-only
   `observation-ledger.jsonl`. Each file's name IS the sha256 of its bytes, the
   ledger enumerates every write deterministically, and exactly one canonical
   record per `observationId` is admitted.

## One canonical record per eligible game AND checkpoint

`observationId = "obs-" + sha256("MODEL_MARKET_OBSERVATION_V1|<sport>|<gameId>|<checkpointAtUtc>")`.

It carries no export clock, so repeated ticks compute the same id and the same
bytes and adopt the existing file. `_admitted_observation_ids()` reads the ledger,
so a crashed run recovers idempotently. If a later tick recomputes DIFFERENT bytes
for an already-admitted id — almost always because an outcome landed after the
pregame observation was sealed — the pregame observation is **not** rewritten and
no competing record is created: the ledger records
`OBSERVATION_ALREADY_ADMITTED_LATER_EVIDENCE_NOT_WRITTEN` and the new evidence
goes to the append-only revision lane instead.

## Deterministic, model-independent book selection

`eligible_book_candidates()` enumerates **every** sportsbook with a complete
same-book home/away moneyline pair where each leg is:

* visible at the checkpoint (`available_as_of <= checkpoint`);
* observed at or before the checkpoint (`observed_at <= checkpoint`);
* at most 900 seconds old at the checkpoint, measured from the BOOK's own
  `observed_at`;
* valid American odds;
* from the same provider AND the same exact fetch as its partner.

Eligible candidates are then ordered by **`(older_leg_age_seconds, sportsbook_key)`
ascending** and the first is selected.

Nothing in that function reads `shadow_experiment_decisions.sportsbook`,
`odds_snapshot_id`, `selection` or any probability. `market.eligibleBookCandidates`
publishes the COMPLETE ordered list with each book's legs, ages and
`orderingKey`, and `market.ineligibleBookCandidates` publishes every rejected book
with its reason code, so the ordering can be re-derived from the record alone
without the database. The record also carries
`decision.namedSportsbookNotUsedForSelection`, so a reader can see the decision's
own book and confirm it was ignored. The synthetic example demonstrates exactly
that: the decision named `synthetic:book-b` and the exporter selected
`synthetic:book-a`.

## Provenance: what is retained, and what is not

`market.exactFetchIdentity` carries provider, provider event id, provider mode and
the consumer receipt clock, and BOTH legs must match on all four or the book is
rejected with `BOOK_LEGS_FROM_DIFFERENT_FETCH_IDENTITY`.

`market.commonResponseSha256` is **null with a stated basis**. `odds_snapshots`
retains no digest of the provider RESPONSE a pair was read from;
`payload_checksum` is a per-row checksum, not a response identity. That is a
contract field this lane cannot populate from retained data, and it says so rather
than inventing one.

## Conventions: missing semantics yield `UNPRICED`

`BOUND_CONVENTIONS` is the only source of settlement semantics and it is **empty**.
No retained provider response states extra-innings inclusion, tie or postponement
settlement, overtime inclusion, or any listed-pitcher/action rule. So today every
otherwise-complete pair is `UNPRICED` with the immutable codes
`MARKET_CONVENTION_SETTLEMENT_NOT_ESTABLISHED`,
`MARKET_CONVENTION_EXTRA_INNINGS_NOT_ESTABLISHED` /
`MARKET_CONVENTION_OVERTIME_NOT_ESTABLISHED` and
`MARKET_CONVENTION_PITCHER_ACTION_NOT_ESTABLISHED`. A `(sport, provider)` pair may
be added to the registry only with the retained document that states it.

## Raw, adjusted and controlling — without substitution

| Channel | Source | When it is null |
| --- | --- | --- |
| `homeWinProbabilityRaw` | `shadow_forecasts.full_home_win_probability` | never, for an admitted record |
| `awayWinProbabilityRaw` | `shadow_forecasts.full_away_win_probability` | never, for an admitted record |
| `tieProbabilityRaw` | MLB: stated `0.0`. NFL: published residual `1 - pHome - pAway` | never, for an admitted record |
| `homeWinProbabilityAdjusted` | `decision.evidence.adjusted_probability` | when not retained, or when the decision selected AWAY |
| `homeWinProbabilityControlling` | `shadow_experiment_decisions.model_probability` | same rule |

The adjusted and controlling channels are retained **for the selected side**.
`1 - adjusted(away)` is a quantity the decision never recorded — a policy
adjustment is not a complement — so it is never derived. The field is null with a
basis and the selected-side value is carried additionally. An MLB pair whose
published home and away do not sum to one is `EXCLUDED`
(`PUBLISHED_PROBABILITIES_DO_NOT_SUM_TO_ONE`), never normalised.

## Conditional two-way vs unconditional three-way

`market.noVig.homeNoVigProbability` is `qHome = rHome/(rHome+rAway)` on the
selected same-book pair. A refund-on-tie market has no tie outcome to price, so
those two prices span "home wins given somebody wins": it is a **conditional**
two-way probability. It is comparable with `decisiveConditionedHomeProbability`
and NOT with the unconditional three-way vector. Both the record and the module
state this in `CHANNEL_MEANINGS`.

## Clocks

Every clock goes through `v18_market_pilot.iso()`, which converts to UTC BEFORE
formatting — the session timezone is `America/New_York`. `created_at` is never
used as a clock: in PostgreSQL it is the TRANSACTION-START stamp. Each record
carries `clockMeanings` naming the column behind every field, including
`createdAtNotUsed`.

| Field | Column |
| --- | --- |
| `checkpointAtUtc` | `games.starts_at - 15m` |
| `decision.recommendedAtUtc` | `shadow_experiment_decisions.recommended_at` |
| `forecast.generatedAtUtc` | `shadow_forecasts.forecast_cutoff_utc` |
| `forecast.exposedAtUtc` | `shadow_forecasts.available_as_of_utc` |
| `forecast.publishedAtUtc` | `shadow_experiment_decisions.forecast_published_at` |
| `forecast.importedAtOrBeforeUtc` | `shadow_experiment_decisions.forecast_imported_at` |
| `forecast.consumerObservedAtUtc` | `shadow_experiment_decisions.recommended_at` |
| leg `quoteObservedAtUtc` | `odds_snapshots.observed_at` (the book's own clock) |
| leg `consumerReceiptAtUtc` | `odds_snapshots.available_as_of` |
| `outcome.sourceCaptureAtUtc` | `nfl_final_observations.retrieved_at` / `game_state_observations.available_as_of` |
| `outcome.sourceAdmissionAtUtc` | that row's `created_at`, as an admission bound only |
| `outcome.genuineCompletionAtUtc` | `game_state_observations.final_at`, or null with the accepted basis |

Forecast exposure AND import must both be at or before the checkpoint
(`FORECAST_EXPOSED_AFTER_CHECKPOINT`, `FORECAST_IMPORTED_AFTER_CHECKPOINT`); a
missing clock is `FORECAST_EXPOSURE_CLOCK_MISSING` /
`FORECAST_IMPORT_CLOCK_MISSING`, never assumed.

## Dispositions kept apart

`decision.qualificationOutcome`, `decision.executionOutcome`,
`decision.funded` and `decision.positionId` answer four different questions and
are never collapsed. Declines and unfunded decisions REMAIN in the population, as
the protocol requires: position status is metadata, never an inclusion rule.

## Outcomes and corrections

`outcome` is the latest supported terminal revision at the moment the canonical
record was written, with its revision identity, status, scores, three source
clocks and nullable genuine completion clock. NFL additionally carries `overtime`,
`week`, `gameType` and `season`, plus `sourceLastUpdatedRaw` carried verbatim as
an unparsed feed string.

Every supported revision — the first and every correction — is ALSO written to
`outcome-revisions/<gameId>/<sha256>.json`, naming the `observationId` and its
ordinal. A correction appends the next ordinal. The pregame observation is never
rewritten.

MLB reads `game_state_observations`; `game_final_outcomes` is the MLB 2025
training-label store and is not touched. NFL reads `nfl_final_observations`,
which is the NFL source of record.

## The read-only guarantee — five properties, none of them a row count

A row-count diff cannot see an UPDATE that preserves counts, and a legitimate
concurrent production write would make it accuse this exporter of a write it did
not perform. It is **not** the barrier.

| | Property | Enforced by | Test |
| --- | --- | --- | --- |
| A | Explicit, CONFIRMED PostgreSQL read-only transaction, always rolled back | `read_only_session` → `v18_market_pilot_outcomes.read_only` issues `SET TRANSACTION READ ONLY` and checks `SHOW transaction_read_only` | `test_property_a_postgres_gets_an_explicit_and_confirmed_read_only_transaction`, and `..._a_server_that_does_not_confirm_read_only_is_refused` |
| B | No INSERT/UPDATE/DELETE/merge/flush/ORM-mutation path in the module | `assert_module_has_no_mutation_path()` AST-walks the module's own source **at import time** | `test_property_b_the_module_contains_no_mutation_path` and five parametrised cases proving the guard actually catches `.add`, `.flush`, `.commit`, `from sqlalchemy import update` and a `delete from` literal |
| C | File output is content-addressed, append-only, and OUTSIDE the transaction | `write_once` (exclusive create, then read-only) plus `_refuse_write_inside_read_transaction`, which reads a marker set in `session.info` for the life of the transaction | `test_property_c_a_file_write_inside_the_read_transaction_is_refused` |
| D | A mutation attempted through the exporter's session FAILS | `read_only_session` attaches `before_flush` and `do_orm_execute` listeners, so it raises on SQLite too | `test_property_d_a_mutation_through_the_exporters_session_fails`, `..._an_orm_delete_through_the_session_fails` |
| E | A concurrent legitimate production write is never blamed on the exporter | nothing in the production path compares counts | `test_property_e_the_guarantee_is_not_a_row_count_diff` greps `run_prospective_capture`'s body for a count and then lands a real concurrent write between ticks |

`sqlite_row_count_probe()` survives **only as a test aid** and its docstring says
so in those words. It is not called anywhere in `run_prospective_capture`.
`test_the_exporter_writes_nothing_to_decisions_or_positions` additionally compares
odds-snapshot **values**, not counts, so an UPDATE would be visible.

## The prepared — and inactive — invocation path

`research_export_tick_hook()` is called as a trailing step of the EXISTING
`export-v18-market-pilot-outcomes-v2` orchestrator handler
(`asp-rx-v18-market-pilot-outcomes-v2`). It returns inert while the flag is off,
which is the default and the deployed value.

**No new scheduler id, no new job, no new owner is registered.** The chosen owner
satisfies every requirement:

| Requirement | Why this owner |
| --- | --- |
| runs only AFTER the canonical decision-time evidence is fixed | it is the outcome lane; it runs strictly after the checkpoints it reads |
| never delays the decision | `research-export` is a separate single-worker lane from `decision-checkpoints` |
| zero provider requests | the module imports no client and no data source (pinned by test) |
| zero DB mutations | properties A–E above |
| does not compete with checkpoint execution | `asp-rx-*`, never `asp-dc-*` |
| at most one canonical record per eligible game | `observationId` + `_admitted_observation_ids` |
| recovers idempotently after interruption | content-addressed records; the ledger enumerates what was admitted |
| preserves exclusions | an unpriced or excluded game keeps its record and reason codes |

**Why not `producer-import` (`asp-im-*`)**: the import must complete BEFORE the
decision; this export must run AFTER the decision-time evidence is fixed. Adding
work to the one lane whose lateness silently costs a decision — 703.56 measured
seconds of queue wait on 2026-09-20 — would be the wrong trade.

**Why not `decision-checkpoints` (`asp-dc-*`)**: a checkpoint window is fifteen
minutes wide and cannot be moved; this export has no deadline at all.

A hook failure can never fail the owner: the call is wrapped, and the V2 result is
returned unchanged.

**Deployment prerequisite:** the owner is registered only when
`V18_MARKET_PILOT_OUTCOMES_V2_ENABLED` is true. Activating the capture means
turning `PROSPECTIVE_CAPTURE_ENABLED` on while that owner is registered.

## The synthetic example and its validator

| | |
| --- | --- |
| Example | `C:\Users\james\aisp-analysis\wagering-review-20260921\contract\synthetic-model-market-observation-v1.json` |
| sha256 | `6aea958212fa167615e0e3678c69ac92f94b9a438c9babd828de649da9387b5b` |
| Generator | `contract\build_synthetic_example.py` |
| Validator | `contract\validate_model_market_observation.py` |

The example is built by the REAL exporter (`build_observation`) against a
fabricated in-memory slate, so it cannot drift from the code that will write
genuine records, and it is marked
`status: "SYNTHETIC_CONTRACT_EXAMPLE_NOT_EVIDENCE"` exactly as the contract's own
example is. It cannot enrol: the slate is fabricated, nothing in it came from
AISP's database, and the status string is the contract's non-enrolling marker.

The validator is **self-contained** — it imports nothing from AISP — so Model
Studio can run it against any record file. `capture.validate_record()` implements
the same checks inside the codebase, and the test suite runs both.

## Running it by hand

```
python -m app.research_exports.prospective_capture \
    --verify-contract "C:\Users\james\asp-model-studio\outputs\model-market-prospective-comparison-20260921-v1"
```

It still does nothing unless `PROSPECTIVE_CAPTURE_ENABLED` is true.

## Fields this lane cannot populate from retained data

Stated rather than invented:

* `market.commonResponseSha256` — no provider-response digest is retained;
  `payload_checksum` is per row. The exact-fetch identity is carried instead.
* every `market.conventions` rule — no retained provider response states
  settlement, extra innings, overtime or pitcher/action semantics, so every
  record is `UNPRICED` until a convention is bound with the document that states
  it.
* `outcome.genuineCompletionAtUtc` for NFL — the NFL source of record has no
  completion column, so it is null under the accepted basis.
* MLB `outcome` week / game type / overtime — `game_state_observations` is
  sport-neutral and holds none of them; they are null, and only NFL is required to
  carry them.
