# CFB sportsbook coverage: mapping, ingestion, display, checkpoint meaning, and the one decision (2026-09-19)

Branch `claude/cfb-sportsbook-path`, cut from `claude/pilot-conformance-outcomes-v1` at `30d69db` (the release deploying at 16:05 ET today). Its commits:

| Commit | What it adds |
|---|---|
| `3288e58` | `af82484` cherry-picked: the daily cap and raw retention |
| `7d3caf0` | The binder, the review artifact and the validation request |
| `43d1531` | Display and checkpoint meaning |

This document is the next commit. **Nothing here is deployed or switched on.** No SportsGameOdds (SGO) or other provider API request was made and no object was spent. Database reads were `SET TRANSACTION READ ONLY` and rolled back, 2026-09-19 15:53–16:45 ET.

Every new behaviour is behind flags that stay off: `CFB_SGO_LANE_ENABLED=false` (deployed value), `CFB_SGO_VALIDATION_ENABLED=false` (new, default), `CFB_SGO_LEAGUE_ID` and `CFB_SGO_TEAM_MAP_PATH` empty. With those values the lane plans nothing, registers no collector and spends nothing. The display changes only take effect when a CFB quote row exists, and none does.

---

## 1. Event mapping: not pinned; the binder and the review artifact are built

**What retained evidence establishes about SGO College Football: nothing observable.**

| Evidence | What it holds |
|---|---|
| `provider_request_ledger` | 0 rows on `sgo_cfb_events`. No SGO request has ever returned a CFB event. |
| Retained SGO raw bodies (`data/raw/sportsgameodds/`) | One file, `2026/09/14/020044_nfl_coverage_probe.json`: NFL events only (`leagueID` `NFL`). No `NCAAF` string. |
| `cfb-activation-20260918/LINES_PROBE_*` | A **CFBD** `/lines` probe (DraftKings via CollegeFootballData, 14 of 71 games with markets, no quote timestamps). It says nothing about SGO league or team ids. |
| SGO OpenAPI (retained 2026-09-19, `openapi-20260919T182608Z.json`) | `NCAAF` appears only as a league key in example bookmaker-support data. Team-id examples follow `<NAME>_<LEAGUE>` (for example `KANSAS_CITY_CHIEFS_NFL`), but no College Football team id appears. |
| SGO pricing page (retained 2026-09-19, below) | "College Football" is one of the Amateur plan's 8 leagues. |

So `NCAAF` is a **documented candidate**, used only as a request filter, never as a pin. No team id can be pinned from retained evidence. **One live validation object is required to observe the first real league and team identifiers.**

**What is built (flag-off):**

- **`app/cfb/sgo_binding.py`: the exact binder.** `review_events` reads a retained response and, per event, proposes a binding only when:
  - the event's `leagueID` is the pinned league, or the filter it was requested with;
  - `status.startsAt` is within 5 minutes of exactly one CFBD kickoff (all 3,299 timed CFBD kickoffs, every classification);
  - that CFBD game is within 5 minutes of exactly one event in the response (unique both ways);
  - orientation carries over as listed: SGO home to CFBD home, SGO away to CFBD away;
  - the resulting map stays one-to-one, against the reviewed map and within the artifact.

  Names never bind. They can only **withhold** a proposal when they read as the reverse orientation (`ORIENTATION_SUSPECT`). Neutral-site games are flagged for the reviewer. Every other event is receipted with its reason: `AMBIGUOUS_START`, `AMBIGUOUS_IN_RESPONSE`, `NO_CFBD_CANDIDATE`, `LEAGUE_UNEXPECTED`, `CONFLICTS_WITH_REVIEWED_MAP`, `CONFLICT_WITHIN_ARTIFACT` or `INCOMPLETE_EVENT`.
- **The review artifact** (`AISP_CFB_SGO_BINDING_REVIEW_V1`) is content-addressed and written beside the raw archive, which is git-ignored (`<raw_data_root>/sportsgameodds-cfb-binding-review/`). `league.pinnable` is set only when the response returned exactly the league it was filtered by.
- **`apply_review`** writes the reviewed team map (`AISP_CFB_SGO_TEAM_MAP_V1`) from proposal ids that a named reviewer approves, with the evidence recorded on each entry: artifact sha256, response-body sha256, event id, CFBD game, and the names on both sides. It approves nothing by itself.
- **`load_binding` now refuses a map that is not one-to-one** (`TEAM_MAP_NOT_ONE_TO_ONE`), so nothing binds from such a map.
- **The validation request.** While the league is unpinned and `CFB_SGO_VALIDATION_ENABLED=true`, `plan` marks one uniquely timed, lease-current, producer-eligible game in its T−75..T−35 window as `VALIDATION_READY`. The request is `leagueID=<candidate>`, `limit=1`, ±5 minutes, with `oddsPresent=true`, so an event without odds returns nothing and bills nothing.
  - It still spends only through the capacity gate (lane switched on, both guards, reserve, MLB/NFL commitments, caps).
  - It is limited to `CFB_SGO_VALIDATION_MAX_REQUESTS` (default 1), counted from the ledger.
  - It prices nothing: every event is receipted `LEAGUE_NOT_PINNED_VALIDATION_ONLY`.
  - It writes the review artifact, and the response bytes are retained (`af82484`).
- **Every purchase is receipted.** The ledger's terminal row now carries `event_receipts`, one per event: `BOUND`, or the refusal reason with both team ids. A response with unmapped teams also writes a review artifact, so each purchase adds reviewable team ids. Ambiguity (more than one event binding one game) binds nothing.
- **`server/scripts/cfb_sgo_binding_review.py`** makes no request:
  - `review` reads retained bytes into an artifact;
  - `apply` writes a new reviewed map;
  - `rebind` persists a retained response after pinning. Its `available_as_of` is the binding moment, so it is never backdated, and `fetched_at` keeps the request time.

After review, the owner pins `CFB_SGO_LEAGUE_ID` to `league.pinnable` and `CFB_SGO_TEAM_MAP_PATH` to the new map, then restarts the backend (settings are read at start).

**Limits of per-game targeting without a map.** A request can only address one event by a unique kickoff or by a reviewed `teamID`, so until the map grows, only uniquely timed games can be bought. Each purchase reveals two team ids for review.

## 2. Ingestion: moneyline, spread and total from the same bytes

`collect` already parsed all three main markets from one response (`parse_main_markets`, exact oddIDs, both sides from one book). This branch adds:

- **Clocks.** Three clocks are recorded on every row, and none is used for another:

  | Clock | Stored as | Meaning |
  |---|---|---|
  | Quote | `observed_at` | That side's own book `lastUpdatedAt`. |
  | Fetch | `available_as_of` and `raw_payload.fetched_at` | The request time, which is the ledger reservation's `requested_at`. |
  | Receipt | `raw_payload.response_received_at` | When the response arrived, taken after the HTTP call. The ledger's terminal row also carries its own `completed_at` and the same `response_received_at`. |

  `raw_payload.clock_semantics` names all three on every row.
- **Provenance on each row.** Each row carries `request_fingerprint` and `response_body_sha256`. The settlement convention is labelled `ASSERTED_BY_AISP_NOT_STATED_BY_PROVIDER`: no retained provider document states the overtime convention.
- **Raw retention.** Bytes are kept via `af82484`, and exactly one request is made.

A live-shaped fixture test proves the whole path: one response gives 3 markets, and 6 rows, each side carrying its own stamp.

## 3. Display: genuine quotes, three clocks, joint-only comparisons

| Surface | With stored CFB quotes | With none |
|---|---|---|
| **CFB game page** (`pipeline.market`, `CfbPipelinePanel`) | Every stored quote as offered, its three clocks and 240-minute usability, and the comparison for its market (below). | The existing coverage reason, unchanged (`COLLECTION_INACTIVE_NO_ALLOCATION`, …). |
| **Daily Decisions** (`/assessments` → `TopOpportunity` “Why?”) | The quote row now shows **Clocks**: quote (older leg, each side), fetched, received. Masses come from the verified joint population (`adapter._cfb_channel`), as before. | Unchanged `NO_COVERAGE` / no-price text. |
| **Home cards** (`EdgeBoard`) | A “Quote clocks” line for the card's book. The lean card's projected score is now the **joint draws' mean** (`JOINT_SCORE_DISTRIBUTION_MEAN`), not Ridge's expected points. | Unchanged. |

**Comparisons** (`app/cfb/market_read.py`) use only the joint channel:

- **Moneyline:** the joint win share, ties excluded, against the **same book's** no-vig pair.
- **Spread and total:** the joint simulation's own `home_margin` and `total` distributions (the 10,000-draw PMFs, verified equal to the joint probability, for example P(margin>0) = `joint_home` exactly), counted at the quoted line, with push mass kept separate. They are shown only when the forecast carries those distributions; otherwise the reading is `FORECAST_PROVIDES_NO_SCORE_DISTRIBUTION`.

Ridge and Elo are never used, and each comparison lists them as excluded. The moneyline is labelled as the market the policy tests at its checkpoint; spread and total are labelled research comparisons.

**Statements that would read false once a quote exists were corrected:**

- the CFB authority note ("no price is being collected this month because …");
- the illustrative-total label and `total_scenario.market_recorded`, which is now true only for a stored line;
- the research-sport row on `/assessments` (`STORED_QUOTES` when quotes exist);
- the Game Center CFB fact, which made a static capacity claim.

MLB and NFL payloads gain nothing, and their quotes carry no `clocks`, because MLB rows store one clock on both legs.

## 4. Checkpoint semantics

Confirmed: the CFB checkpoint reads stored pairs through `nfl_paper_decision.paired_quotes`. It prices real SGO quotes when present; the older leg must be ≤ 240 minutes, from the same provider, book and market. It records `NO_TWO_SIDED_QUOTE` otherwise. The fixture test ends in `NO_QUALIFYING_EDGE` or `ALL_GATES_PASSED` from the collected pair.

The server derives the meaning from the recorded row (`paper_decision.checkpoint_semantics`). The row is never rewritten and no threshold moves.

| Class | From | Evaluated | Priced | UI label |
|---|---|---|---|---|
| **NO_BET** | `DECLINED` / `NO_QUALIFYING_EDGE` | yes | yes | "No bet · priced, edge below policy" |
| **NO_PRICE** | `DECLINED` / `NO_TWO_SIDED_QUOTE`, `QUOTE_TOO_STALE`, `NO_PERMITTED_PROVIDER` | yes | no | "Declined · no same-book two-sided moneyline was stored" (per reason) |
| **BLOCKED** | `BLOCKED` / lease, eligibility record, grant, forecast | no | no | "Blocked · not evaluated" (the lease-specific wording is kept) |
| QUALIFIED / QUALIFIED_NOT_ACTIVATED | `ALL_GATES_PASSED` / `ACTIVATION_CONTROL_OFF` | yes | yes | "Qualified · …" |

The class is exposed in three places:

- `/command-center/decisions` items: `checkpoint_semantics`, CFB only;
- `/command-center/markets`: `model_status.checkpoint.semantics`;
- the CFB game page: `pipeline.decision.semantics`.

The client labels them in the Daily Decisions status, the model line and the CFB pipeline panel.

**Recorded so far:**
- 2026-09-18: 2 × `DECLINED NO_TWO_SIDED_QUOTE` (CFBD 401858226, 401856811), class **NO_PRICE**.
- 2026-09-19: **26 × `BLOCKED DISCOVERY_NOT_CURRENT`**, recorded 11:10–15:45 ET, class **BLOCKED**.
- 0 positions.

Every checkpoint today up to 15:45 ET was blocked by lapsed producer leases, so prices alone would not have produced a single evaluation today. At 15:56 ET the leases of all 28 remaining 09-19 games read `CURRENT`.

## 5. Allocation analysis (read-only, 2026-09-19 15:53–16:45 ET)

### The ledger position

| Item | Value | Source |
|---|---|---|
| Provider usage | tier **amateur**, cap 2,500, used 1,997, remaining 503 (reading at 15:06 ET; it costs no objects) | `provider_request_ledger` `sgo_account_usage` receipt |
| Guard A (`build_plan`) | used 2,014, reserve 300, **186 spendable** | read-only call, 15:55 ET |
| Guard B (`object_budget`) | used 2,014, reserve 300, **186 spendable**. The two guards agree. | read-only call |
| Reserve | **300** through `2026-10-01T00:00Z` (`reserve_at(2026-09-30T23:59Z)=300`); **400 from `2026-10-01T00:00Z`**, when the current acquisition authority and `CFB_SGO_LANE_AUTHORIZED_UNTIL` also end | `provider_allocation.RESERVE_SCHEDULE` |
| September spend by lane | MLB `sgo_mlb_events` 1,962 (352 requests), NFL 29 (3), opening adjustment 24, **CFB 0** | ledger |
| MLB/NFL committed | today's remaining room: MLB 1 (16 of 17 spent), NFL 0 (14 of 14). Future need 09-20..09-30: MLB 132, NFL 49 = 181. **Committed 182.** | `_day_need` per day (read-only), `build_plan` lanes |
| CFB gate | min(A, B) 186 − committed 182 − protected headroom 10 = **−6, reported as 0**. With headroom 0 it is **4**. | `sgo_lane.capacity` arithmetic |

Correction to `SGO_ACQUISITION_ANSWERS_20260919.md` (B): lowering the headroom to 6 releases **nothing**. At 12:36 ET the gate read 211 − 207 − 6 = −2, and now it reads 186 − 182 − 6 = −2. Only headroom **0** releases the 4 objects.

### Remaining CFB games through 2026-09-30 (as of 15:56 ET)

"Admitted" means AISP has the forecast and the game (producer scope: FBS vs FBS). "Unique kickoff" means no other CFBD game starts within 5 minutes, so the game is targetable without a team map.

| ET date | CFBD FBS-FBS still ahead | Admitted | Lease current | Unique kickoff (admitted) |
|---|---|---|---|---|
| Sat 09-19 | 28 | 28 | 28 | 2: 401871047 18:30, 401856692 19:45 |
| Thu 09-24 | 1 | 1 | 0 (producer lists it `HISTORICAL`) | 1: 401869941 19:30 |
| Fri 09-25 | 4 | 4 | 3 | 3: 401862779 16:00, 401858461 20:00, 401858234 22:30 (`HISTORICAL`) |
| Sat 09-26 | 51 (+2 TBD) | 17 so far | 17 | 1: 401864576 12:45 (4 of the 51 CFBD games are uniquely timed) |
| 09-27..09-30 | 0 | 0 | — | — |
| **Total** | **84 (+2 TBD)** | **50** | **48** | **7** |

**October**, if a new allowance were granted (retained CFBD schedule):

- 285 FBS-FBS regular-season games.
- 92 have a kickoff time; **193 are still TBD**.
- 33 of the 92 are uniquely timed.
- Saturdays carry 44–51 games each.

With a reviewed team map, `teamID` targeting makes shared kickoffs buyable, one object each.

### Cost per game

| Item | Objects |
|---|---|
| One price for the checkpoint: one request, `limit=1`, one event, bought once at T−75..T−35 (≈T−60). The older leg stays within the 240-minute rule through T−15, and a game that already holds a pair is never re-bought. | **1 per game** |
| Refresh policy | **None required by the policy.** A second object near T−20 would only freshen what is displayed. Amateur's documented update frequency is 10 minutes. |
| Validation, once | **1** |
| Money inside the Amateur allowance | **$0**. The only cost is objects taken from MLB/NFL margin in September. |

### Minimum objects for useful bounded coverage

- **September (existing authority, until `2026-10-01T00:00Z`).**
  - Fundable only from the 4-object margin, by setting headroom from 10 to 0.
  - **4 objects**: 1 validation plus up to **3** uniquely timed games on 09-25 and 09-26, one price each at T−60. Covering 09-24 as well depends on the producer re-listing its lease.
  - Two prices per game (4 + 3 = 7 objects) cannot be funded without cutting an MLB/NFL commitment (S3) or retiming MLB (S2, about 18 objects, not on this branch).
  - Duration: 3 slate days.
- **October (new authority).**
  - One object per admitted, targetable game at T−60.
  - Admitted games reach at most 285, and targetable games start at the 33 uniquely timed and grow as the map grows. **300 objects** cover every FBS-FBS game once.
  - October has 2,500 − 400 = 2,100 spendable under both guards. MLB/NFL October need is about 165: 60 scheduled NFL games × (checkpoint + preview) = 120, plus about 45 MLB postseason. That is **an estimate from the schedule, not the allocator's projection**, which covers only the current month. So 300 CFB objects fit with **no reserve change and $0**.

### Monetary cost

The price was established from an authoritative source. No retained AISP document or ledger receipt carried a price: the usage receipts give the tier (`amateur`) and not a price. The provider's public pricing page was therefore read **once** and retained, git-ignored:

- `data/raw/provider-docs/sportsgameodds/pricing-20260919T195412Z.html`: sha256 `1d6754ebc5717b0cd6982696d85c409d9b467593b8ba5cfc9e0b052e84bfbbb9`, 243,211 bytes, HTTP 200.
- Headers: `pricing-20260919T195412Z.headers.txt`, sha256 `24b17ac25ff2131020b5552003be94f854578925c94007a2f79c328449754e7a`.

| Plan | Price (plan data `monthlyPricePerMonth` / `yearlyPricePerMonth`) | Objects / month | Notes |
|---|---|---|---|
| **Amateur (current)** | **$0** | 2,500 | 10 req/min, 10-minute update frequency, 8 leagues including **College Football**, 9 bookmakers |
| Rookie | **$149 / month** billed monthly; $99 / month billed annually ($1,188 / year) | 100,000 | 50 req/min, 3-minute updates, 17 leagues, 77 bookmakers |
| Pro | $499 / month monthly; $299 / month annually | unlimited | 300 req/min |

**There is no overage product.** Exceeding a limit returns HTTP 429, so more objects than 2,500 a month means a plan change. The coverage above needs none.

**One discrepancy to verify.** The same page says the monthly object limit resets "on a rolling 30-day period counted from your first use — not on the first of the calendar month". AISP's guards assume the UTC calendar month (`sgo_usage.py`), and the provider's own `used_this_month` matched AISP's calendar-month ledger on 09-18 (1,984 = 1,984). That is consistent with a period that began on the 1st, but it is not proof. The first October usage reading, which is free, confirms or refutes the `2026-10-01T00:00Z` refill.

## 6. Finals and scoring (read-only)

Source-confirmed CFB finals are admitted and scored today, from retained producer evidence, with no AISP provider request.

- **`ingest-cfb-finals`:** 53 runs since 2026-09-18 15:16 ET, **all SUCCEEDED**, `provider_requests: 0`. The latest run, 15:39 ET, read export `3d7062be…` (103 rows, `RAW_VERIFIED`), matched 3 held games and reused 3 observations.
- **`cfb_final_observations`:** 3 rows. Each has `source_completed=true` and `raw_record_verified=true`, with the completion clock `ABSENT_FROM_RETAINED_SOURCE`: CFBD supplies no completion time, and none is invented.

  | CFBD | Kickoff (ET) | Final (home–away) | Captured (ET) |
  |---|---|---|---|
  | 401858225 | 09-17 19:30 | 27–13 | 09-18 14:15 |
  | 401858226 | 09-18 19:30 | 20–33 | 09-19 02:50 |
  | 401856811 | 09-18 20:00 | 28–26 | 09-19 02:50 |

  In each case the game row is `FINAL` with the same score.
- **Scoring** (`research_forecast_evaluations`, `CFB_GEN1_JOINT_BASELINE`, `AISP_CFB_PROSPECTIVE_SCORING_V1`, all current): winner correct 3 of 3.

  | CFBD | P(actual) | Log loss | Two-way Brier | Margin error | Total error |
  |---|---|---|---|---|---|
  | 401858225 | 0.769 | 0.262 | 0.053 | −2.44 | +12.58 |
  | 401858226 | 0.772 | 0.258 | 0.052 | +0.47 | +8.65 |
  | 401856811 | 0.559 | 0.582 | 0.195 | +0.33 | −0.77 |
- **Today's games:** 26 of the 54 admitted 09-19 games have kicked off, and **none is final in AISP yet**. The producer's finals export at 15:39 ET still lists 103 rows with 3 held games. Today's finals arrive with the producer's next capture, as designed. They are not missing.

---

## Owner decision

> **CFB sportsbook coverage: one validation object, then bounded coverage. All within the free Amateur allowance ($0).**
>
> 1. **Deploy** `claude/cfb-sportsbook-path`: backend restart plus frontend build. Every CFB flag keeps its current value, so nothing spends.
> 2. **September:**
>    - **Objects:** **4** in total, made up of **1 live validation object** and up to **3** priced games.
>    - **Source:** S1, `CFB_SGO_PROTECTED_HEADROOM` 10 → **0**. This releases the 4 objects the September projection leaves above MLB/NFL committed need (186 − 182). MLB/NFL keep their full projected need but lose their projection-error margin. No reserve change.
>    - **Flags:** `CFB_SGO_LANE_ENABLED=true`, `CFB_SGO_VALIDATION_ENABLED=true` (filter `NCAAF`, at most 1 request), `CFB_SGO_LANE_OBJECT_CAP=4`, `CFB_SGO_LANE_DAILY_OBJECT_CAP=2`. `CFB_SGO_LANE_AUTHORIZED_UNTIL` stays `2026-10-01T00:00:00+00:00`.
>    - **Dates:** from 2026-09-24 00:00 ET to 2026-10-01T00:00Z.
>    - **Games:**
>      - The validation object goes on the first uniquely timed, lease-current, producer-eligible game in its T−75..T−35 window: CFBD 401869941 (09-24 19:30 ET) if the producer re-lists it, otherwise 401862779 (09-25 16:00 ET).
>      - Then, after review and pinning, 1 object each at about T−60 for up to 3 of: 401858461 (09-25 20:00), 401858234 (09-25 22:30, if re-listed) and 401864576 (09-26 12:45).
>    - **Review:** **‹reviewer›** approves the artifact's proposals. The owner then pins `CFB_SGO_LEAGUE_ID` to `league.pinnable` and `CFB_SGO_TEAM_MAP_PATH` to the new map, and restarts the backend.
> 3. **October (new authority, yes or no):**
>    - **Objects:** a CFB lane of **300 objects** (`CFB_SGO_LANE_OBJECT_CAP=300` for the month, `CFB_SGO_LANE_DAILY_OBJECT_CAP=60`).
>    - **Price:** 1 object per admitted, targetable FBS-FBS game at about T−60, and no refreshes.
>    - **Dates:** `CFB_SGO_LANE_AUTHORIZED_UNTIL=2026-11-01T00:00:00+00:00`.
>    - **Source:** October's 2,100 spendable objects above the 400 reserve. MLB/NFL October need is estimated at about 165.
>    - **Games:** up to 285 games, 33 targetable today, more as the reviewed map grows. `CFB_SGO_PROTECTED_HEADROOM` returns to 10.
>
> **$ cost: $0.** More coverage than this, or refreshes, would require Rookie at $149 for a month billed monthly.
