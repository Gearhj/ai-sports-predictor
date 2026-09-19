# CFB coverage presentation: Akron @ Minnesota, 2026-09-19

Game `018cbc48-7799-4c3e-869c-7e7e6823cb2e`, CFBD `401858451`. Kickoff 12:00 PM ET.
Decision window 11:30 to 11:45 AM ET.

The evidence was read on 2026-09-19 between 12:24 and 12:30 PM ET:

- the live API on 127.0.0.1:8010 (`/command-center/markets`, `/assessments`, `/decisions`, `/sports/readiness`, `/sports/registry`);
- PostgreSQL, inside `SET TRANSACTION READ ONLY` transactions that were rolled back.

The live frontend is build `dd77aeb`. Its code for the files cited below is identical to the base of this branch, so the base line numbers apply to what was on screen.

## What the stores held

| Evidence | Value |
|---|---|
| Quote rows for the game | `markets` 0, `odds_snapshots` 0. College Football had 0 odds rows in total. |
| SGO CFB lane | `provider_request_ledger` has 0 rows for `sgo_cfb_events` (0 objects spent). `job_runs` has 0 runs of `collect-cfb-pregame-odds`, a job the scheduler registers only when `cfb_sgo_lane_enabled` is set (`server/app/jobs/scheduler.py:758`). The capacity gate reads `NO_UNCOMMITTED_SGO_CAPACITY`: "... 0 are genuinely uncommitted. CFB takes the smaller of 10 and that: 0." |
| Coverage row (`/command-center/markets`) | `price_state NOT_COLLECTED`, `quotes []`, `collection_opens_at null`, `price_unavailable.reason NO_UNCOMMITTED_SGO_CAPACITY`. The row had no `market_status` or `model_status`, because they were built only for `league in CONTROLLING_ENGINES` (MLB and NFL), `server/app/services/command_center.py:768`. |
| Forecast (`cfb_research_forecasts` 6035534b…) | `cfb-gen1:ea85d35a…`, origin `PRODUCTION_DISCOVERY`, `research_only = true`, producer status `STAGED_PROSPECTIVE_BASELINE_NO_CONSUMER_AUTHORITY`, configuration `CFB_GEN1_RIDGE_A100_PAIRED_RESIDUAL_V1`, published at the receipt's commit time 2026-09-18 11:57:55 ET. Joint probability: home 0.7831. |
| Probability binding (`/assessments`) | `PAIRED_POPULATION`, `SUPPORTED`, 10,000 draws, `authority RESEARCH_ONLY`, `quotes []`. |
| Discovery lease | 52 verified manifests. The producer renewed the lease every 15 minutes until its last manifest `03ba7f58…`, created 08:05:46 ET and verified 08:06:16 ET. That manifest lists the forecast `CURRENT_ELIGIBLE`, lease valid until **08:20:44 ET**. No manifest has arrived since. |
| Eligibility record | `research_supplements` 2f02a49b…: `PAPER_RESEARCH_ELIGIBILITY`, `cfbd:401858451`, status `RESEARCH_SHADOW`, `research_shadow_only = true`, published 2026-09-18 14:36 ET, before T-30. |
| Checkpoint (`job_runs`, `evaluate-cfb-paper-checkpoint`) | Run 8fe7049f at 11:30:55 ET: outcome `BLOCKED`, reason `DISCOVERY_NOT_CURRENT`, detail "EXPIRED_LEASE: …", persisted `RECORDED`, decision 29d99c43. The refresh reported `MANIFEST_ALREADY_RECORDED 03ba7f58, currently_eligible 0`. Runs 30ad4512 (11:36:48 ET) and 3d46c10d (11:40:55 ET) returned `REUSED_EXISTING_DECISION`. The recorded evidence carries `discovery.state EXPIRED_LEASE, lease_valid_until 08:20:44.067 ET`. |

## 1. "Waiting for a sportsbook price"

**Where it came from:** `client/src/components/command/CommandDecisions.tsx:130` (`describeDecision`), called from `buildFeed` at `:153`. It was shown as:

- the card status chip, `AssessmentBoard.tsx:272`;
- the Decision row, `AssessmentBoard.tsx:280`;
- the audit rows.

**What it read:** until the checkpoint row was written at 11:30:55 ET, the game had no decision item. The fall-through then ran:

1. `rec` defaulted to `NOT_YET_EVALUATED`, and the game had not started.
2. `game.model` was present, so the "Waiting for the forecast" branch did not apply.
3. `game.price_state` was `NOT_COLLECTED`, which is not `CURRENT_PRICE`, so the card said "Waiting for a sportsbook price".

**Why it was wrong:** no collector exists for this game:

- the lane has no allocation and has spent 0 objects;
- the collector job is not even registered;
- there is no quote row.

"Waiting" promised a price nothing would fetch. College Football had no server-side market state, so the client could only fall through to the generic MLB/NFL wording.

**The same fall-through, two more labels:**

- **"Collection scheduled":** the audit progress label (`CommandDecisions.tsx:238`) named a collection that does not exist.
- **"Awaiting producer eligibility":** after 11:30:55 ET the chip changed to this label (`CommandDecisions.tsx:124`), because `/eligib/` matched "The producer's eligibility lease has expired". That was a finished, recorded block, not something pending.

## 2. "Stored reference price"

**Where it came from:** `client/src/components/command/TopOpportunity.tsx:182`. It renders `PRICE_BLOCKED_TEXT.STALE`, "Stored reference price — current expected return unavailable", from `client/src/services/topPlay.ts:58`, whenever `topPlay` returns `PRICE_NEEDED` (`topPlay.ts:248`, header "CURRENT PRICE NEEDED").

**What it read:**

1. The binding was `SUPPORTED` and `quotes` was `[]`, so there were no outcomes, no official pick and no best value.
2. `anyCurrent` was false, so the state became `PRICE_NEEDED`.

**Why it was wrong:** `PRICE_NEEDED` was written for a game whose quotes are all stale, and the text describes that case. Here no quote row exists at all, so there is no "stored reference".

## 3. "Research only": verified, correct, and a sport-level label

**Where it came from:**

1. `AssessmentBoard.tsx:188`/`:193` (the Forecast cell) renders `AUTHORITY_TEXT[channel.authority]`, defined at `client/src/services/probability.ts:249`.
2. `channel.authority` is set from the admission table at `server/app/probability/adapter.py:96`: `ADMITTED["CFB"]["authority"] = RESEARCH_ONLY`.
3. `_cfb_channel` passes it on at `:556`, and it is emitted at `:146`.

The value is a static per-sport constant. It is not derived from this forecast's lease, from the checkpoint or from the missing price.

**Is it right for this forecast?** Yes, and every binding agrees:

- the forecast row has `research_only = true` and producer status `…NO_CONSUMER_AUTHORITY`;
- the eligibility record is `RESEARCH_SHADOW` with `research_shadow_only`;
- `games.source_mode` is `RESEARCH_SHADOW`;
- the sport registry lists College Football as `PLANNED`: "Virtual paper only";
- the only account is "ASP CFB RESEARCH PAPER BANKROLL — 2026 V1", a $1,000 virtual paper account.

It is therefore a correct sport-level label, not a wrong inference. It says nothing about paper eligibility. Until the lease lapsed at 08:20:44 ET, the forecast was paper-eligible. The checkpoint was blocked by the lapsed lease, not by "research only" status, and that is now stated on its own line.

## 4. Recovery goes through the normal path, and checkpoints are never rewritten

The current code already behaves this way:

- **One intake path.** `consume_discovery` (`production_import.py:190`) is the only way a manifest gets in. It is called by the exchange consumer job (`model_exchange_consumer.py:309`) and by the checkpoint's own refresh (`paper_decision.py:321`). A recovered successor manifest is accepted only through its hash-bound predecessor. There is no special case.
- **Evaluation only inside the window.** `games_at_checkpoint` (`paper_decision.py:107-114`) selects a game only while it is in T-30..T-15. A manifest that arrives after 11:45 ET cannot cause a new Akron evaluation.
- **No rewrite, no backdating.** `record` (`paper_decision.py:255-266`) reuses the prior row unless it ended on a missing prerequisite. Even then it adds a new row stamped `recommended_at=moment` (`:277`), the evaluation's own time. Nothing in `server/app` updates a recorded `shadow_experiment_decisions` row.

**New tests** in `server/tests/test_cfb_consumer_activation.py`:

- `test_a_recovered_manifest_after_the_window_is_accepted_normally_and_the_blocked_checkpoint_stands`: the lease lapses, the checkpoint blocks, and a successor manifest after T-15 is recorded (`MANIFEST_RECORDED`, `present_eligibility` `CURRENT`). The later tick evaluates nothing. Every decision row keeps its recommendation, reason, qualification and time, still stamped at the checkpoint.
- `test_a_recovery_inside_the_window_adds_a_new_evaluation_at_its_own_time`: the earlier block is kept unchanged, and the new row carries the recovery time.

## The fix

**Server** (`pregame_status.py`, `command_center.py`). College Football games now carry their own `market_status` and `model_status`. The MLB and NFL code paths are untouched.

`market_status` states:

| State | Meaning |
|---|---|
| `COLLECTION_INACTIVE_NO_ALLOCATION` | No quote row, and no active collector: the lane is switched off or the gate grants 0 objects. |
| `NO_QUOTES_YET` | No quote row, but a collector is active. |
| `COLLECTION_STATE_UNKNOWN` | The lane is on, but the gate could not be read. |

- Each of these states carries `collection` with `active`, `lane_enabled`, `allocation_state`, `objects_allowed` and `spent_on_lane`.
- Once quotes exist, the ordinary freshness states apply, against the College Football policy's permitted source (SportsGameOdds).

`model_status` states:

| State | Meaning |
|---|---|
| `ELIGIBILITY_CURRENT` | The producer's discovery lease is current. |
| `ELIGIBILITY_EXPIRED` | The lease has lapsed. The status carries `lease_valid_until`. |
| `ELIGIBILITY_NOT_CURRENT` | The forecast is not currently eligible for another reason, such as not being listed or being historical. |
| `NOT_PUBLISHED` | No forecast is bound. |

- `model_status` also carries `eligibility`, `eligibility_now`, `checkpoint` and `renewed_since_checkpoint`.
- Once a checkpoint is recorded, the state is read from the lease that checkpoint recorded. A later manifest appears only as "renewed since".

**Client.** `pregameStatus.ts` renders two independent lines. For Akron:

- Market: **No sportsbook coverage**: "CFB price collection is not active (no allocation): no uncommitted SportsGameOdds capacity is left after MLB/NFL; the CFB lane is not switched on; 0 objects spent on it · no quote is stored for this game".
- Model: **Forecast eligibility expired**: "Forecast eligibility expired at 8:20 AM ET; the publisher has not renewed it (newest manifest 8:05 AM ET); the 11:30 AM ET checkpoint was blocked (DISCOVERY_NOT_CURRENT)".

Other client changes:

- **Status chip.** The chip now reads "Blocked · forecast eligibility expired", keyed to the recorded reason's `EXPIRED_LEASE:` prefix.
- **Before a checkpoint.** A College Football card states the lapsed lease, or the missing coverage, as a fact. Neither says "waiting".
- **Top panel.**
  - With no quote row and no collector, the state is `NO_COVERAGE` ("NO SPORTSBOOK COVERAGE").
  - "Stored reference price" appears only when a quote row exists.
  - A game with no quote row reads "No sportsbook price is stored for this game (yet)".
- **Audit progress.** "No collection (no allocation)" replaces "Collection scheduled".
- **Readiness strip and panel.** An eligibility record with a lapsed discovery lease no longer reads "Paper research eligible" or "Producer eligibility present".

**MLB and NFL:** their market and model states, labels and tests are unchanged. The only wording they can see differently is in the top panel, and only for a game that has a forecast but no quote row at all (rare, since collection opens at T-255). That panel now reads "No sportsbook price is stored for this game yet" instead of "Stored reference price". A game with stale stored quotes still reads "Stored reference price".

**Unchanged:** no policy, threshold, model, spending or scheduler change. `price_unavailable` gained fields but lost none.
