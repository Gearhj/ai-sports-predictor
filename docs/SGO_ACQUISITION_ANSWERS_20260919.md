# SportsGameOdds acquisition: two answers (2026-09-19)

Branch `claude/sgo-mlb-exact-event-selection`. Nothing here is deployed or switched on, and no provider was called.

All figures come from read-only database sessions (`SET TRANSACTION READ ONLY`, then rollback) taken 2026-09-19 12:27–12:45 ET, unless stated otherwise.

## The ledger position these answers use

| Item | Value |
|---|---|
| **Both guards** (guard B `object_budget`, guard A `build_plan`) | 1,989 used this billing month, reserve **300**, **211 spendable**. They agree. |
| **Reserve schedule** (`provider_allocation.reserve_at`) | 300 through the September billing month; **400 from 2026-10-01T00:00Z**. |
| **Acquisition authority** | Ends at the same instant. The SGO fallback window uses a UTC date, `provider_registry.GOVERNED_FALLBACK_UNTIL = 2026-09-30`. The CFB grant and `CFB_SGO_LANE_AUTHORIZED_UNTIL` are 2026-10-01T00:00Z. |
| **September spend by lane** | MLB `sgo_mlb_events` 1,950 objects in 345 requests. NFL `sgo_nfl_events` 15. Opening adjustment 24. **CFB 0.** Usage reads 0 (they are free). |
| **Today's allocation** (09-19 snapshot, 31 objects) | MLB **17**: 5 spent, room 12. NFL **14**: the Show Prep preview slot, 15:00–16:30 ET today, for Sunday's 14 games; 0 spent. Show Preview lane 0 (unfunded). NFL live and periods 0. CFB not a lane in the allocator. |
| **Committed MLB/NFL need** | Today's remaining room (12 + 14 = 26) plus projected need 09-20..09-30 of **181** (MLB 132, NFL 49), totalling **207**. |
| **Today's completed MLB purchases (spent, sunk)** | 10:16 ET, 1 object (14:10 game). 11:54 ET, 1 (16:05). 12:06 ET, 3 (16:05 refresh plus the 16:10 pair). |

---

## (A) Can future, unspent MLB purchases be retimed to support the frozen market pilot?

**Answer.** Retiming is **feasible and budget-neutral**, but it yields **0 qualifying pairs** until three things are established:
- the timestamp semantics;
- the market conventions;
- per-leg provenance.

Raw retention (`b56ed64`) is necessary but not sufficient. This matches the conclusion Model Studio accepted on 09-19 (MODEL_STUDIO_INTEGRATION_STATUS.md, "staged export corrections"): "the current schedule/evidence cannot supply qualifying pairs".

**The schedule.**
- The full proposal is on branch `worktree-agent-aaf3834eada733674`, commit `bd9550f`, in `docs/SGO_MLB_PURCHASE_TIMING_PROPOSAL.md`. It is flag-off and undeployed, and deliberately **not** on this branch.
- Each future, unspent game is bought **once at T−23**. A game due within the provider's 600-second spacing rides along with the current request, and games sharing a first pitch are always bought together. The window closes one second after the last selected first pitch. There are no refreshes.
- Anything already bought, including today's five objects, is sunk and never bought again.

**Refreshed numbers** (simulation from 12:45 ET to 09-27; 117 games still to buy; same allowances, guards and reserve):

| | Current schedule | T−23 |
|---|---|---|
| Objects (requests) | 134 (68.1) | **115.9 (55.1)**. At or below the current plan every day; drawn from the same issued MLB allowance. |
| Usable pair (≤ 240 min) at the FINAL_PREGAME decision | 73.3 (62.7%) | **115.5 (98.7%)** |
| Quote age at the decision, median / p90 | 228 / 234 min | **10.8 / 16.9 min** |
| Timing-only pilot rule (both legs 0 ≤ a−u ≤ 900 s, u ≤ r ≤ a) at the T−15 lock | 0 | **82.8 (70.8%)** |
| The same rule at the actual decision anchor | 0 | **98.0 (83.8%)** |
| Using only the 09-12/13 lags | — | 74.0% (T−15), 85.8% (decision) |
| Adding the worst per-leg skew (207.9 s) to every quote | — | 41.5% (T−15), 62.4% (decision) |
| Mismatch requests that would halt the lane | 2.39 | **0** |

**Timestamp evidence.**
- **Provider lag at acquisition** (older leg): median 6.2 min, maximum **15.55 min** (on 09-18). The 09-12/13 pairs peak at 13.5 min.
- **What a near-anchor receipt can do.** A receipt placed exactly at the anchor would pass 100% of the 09-12/13 pairs; 5 min before, 97.1%; 10 min before, 31.9%.
- **Pregame per-leg skew** is 0 across all 9,253 pairs in 235 collector fetches.
- **The 207.9 s skew** appears only in the 09-09 in-game body.
- **Stored clocks.** The MLB DTO stores the older leg's time on both legs (`sgo_collection.py:189`, `:192` at `1cdccb6`).
- **Raw bodies.** None were retained for 09-12..09-19 (0/180). `b56ed64` keeps them for future purchases only.

**Why 0 pairs qualify regardless of timing:**
1. What SGO's `lastUpdatedAt` means is not established: bookmaker update, provider observation or record refresh. No retained document defines it.
2. No retained, authoritative convention exists for extra innings, ties/pushes or pitcher/action.
3. There are no per-leg clocks or response identity for anything bought before `b56ed64` is deployed.

A later fetch never refreshes an old bookmaker quote.

**Cost of retiming.** MLB prices appear only at about T−21. They disappear from:
- Home, Decisions and the markets pages;
- RFF Core readings (all 11 that carried a price lose it);
- A House Divided, where the human pick window falls from a median of 171 min to about 6;
- 610 of 662 priced V18 MORNING rows and 6 of 8 LINEUP_CONFIRMED rows (V1: 92 of 95, 1 of 2).

Show Prep is unaffected: 0 of 64 MLB games had a price at the day-before 16:30 freeze under either schedule.

**Owner decisions for (A).** Adopt, or not, the T−23 schedule (flag `SGO_MLB_PURCHASE_LEAD_MINUTES=23`), and choose when to switch it on. Adopting it does not qualify a single pilot pair by itself.

**Related, separate: the halt repair.** Commit `4a7c2c5` on this branch makes the deployed schedule defer any request the provider could answer with a different game, instead of sending it. That stops the 09-18 halt recurring on today's tied 18:40 and 20:10 ET pairs. It is independent of retiming.

---

## (B) What exactly is needed to activate a bounded amount of CFB sportsbook coverage?

### What exists

- **Code, built and tested offline.**
  - **`app/cfb/sgo_lane.py`**: the capacity gate.
  - **`app/cfb/sgo_price.py`**:
    - `plan`: prerequisites, a buy window of T−75..T−35, and one-event targeting;
    - `collect`: reserves in the ledger, makes one request of `limit=1`, and parses both sides from one book;
    - binding: exact identity only;
    - persistence: one snapshot per side, each stamped with **that side's own** `lastUpdatedAt`.
  - **Job `collect-cfb-pregame-odds`**: every 10 minutes, registered only when the lane flag is on (`scheduler.py`, "CFB SportsGameOdds prices").
- **Flags (deployed `.env`):**

  | Flag | Value |
  |---|---|
  | `CFB_SGO_LANE_ENABLED` | **false** |
  | `CFB_SGO_LEAGUE_ID` | **empty** (unresolved) |
  | `CFB_SGO_TEAM_MAP_PATH` | **empty** (unresolved) |
  | `CFB_SGO_LANE_OBJECT_CAP` | 10 (a total, from the 2026-09-18 assignment) |
  | `CFB_SGO_PROTECTED_HEADROOM` | 10 |
  | `CFB_SGO_LANE_AUTHORIZED_UNTIL` | 2026-10-01T00:00Z |

  Objects spent on the lane: 0.

### Why it is inactive: three independent reasons

1. **The flag is off.**
2. **No capacity.** "0 uncommitted capacity" is the gate's arithmetic under both guards:
   - min(guard A, guard B spendable) = **211**
   - minus committed MLB/NFL need of **207** (26 of today's room plus 181 projected)
   - minus protected headroom of **10**
   - = **−6**, reported as **0**.

   Without the headroom there would be **4** objects. On 2026-09-18 the owner directed that those stay with MLB/NFL as margin against projection error.
3. **The binding is unresolved.** AISP has never observed an SGO college-football event, so `plan` reports `SGO_BINDING_UNRESOLVED` and requests nothing. Pinning the league ID and the first team-map entries needs **one live validation object** (`sgo_price.LIVE_VALIDATION`) and a human review of the team IDs.

### Per-game cost and what a bounded allocation would buy

**Cost.**
- **1 object per game.** One request with `limit=1` and a ±5-minute window around the kickoff, bought once in T−75..T−35. Games that already hold a price are never re-bought.
- **One-time setup:** 1 validation object.

**Games under existing authority** (AISP-admitted CFB games from 12:36 ET to 2026-10-01T00:00Z):
- **48 in total**: 43 today (09-19), 1 on 09-24, 4 on 09-25.
- The 09-26 slate has not been admitted yet. Admission comes from the producer's eligibility records.

**Targetable now.** Targeting needs one of:
- a kickoff that no other CFBD game shares within 5 minutes (checked against all 3,299 CFBD kickoffs); or
- a reviewed team map.

| | Today | 09-24 | 09-25 | Total |
|---|---|---|---|---|
| Uniquely timed, buy window still ahead | 3 | 1 | 3 | **7** |
| With a reviewed team map (window ahead) | up to about 39 more | | | up to 46 |

**Bounded allocations:**
- **N = 3 per day**, total cap 8: 1 validation object plus all 7 targetable games (3 today, 1 on 09-24, 3 on 09-25).
- Today is realistic only if validation and pinning finish before the uniquely timed kickoffs' T−35.
- **N = 1–2 per day** would cover part of the 7.
- Beyond 7 games, a reviewed team map is needed, and the objects needed grow to one per targeted game.

**Where the objects could come from.** None exist without touching the reserve or MLB/NFL commitments. The options, each an owner decision:

| Source | Objects |
|---|---|
| **S1.** Release the 4 objects of headroom above committed need (lower `CFB_SGO_PROTECTED_HEADROOM` from 10 to 6 or below). This consumes the owner's 09-18 margin for MLB/NFL projection error. | ≤ 4 in September |
| **S2.** The MLB T−23 retiming saves about 18 objects over 09-19..27 against the current plan (134 → 116). Only after (A) is adopted and the MLB projection (`MLB_OBJECTS_PER_GAME`, 17/15) is updated to 1.0 per game, which lowers "committed". | about 18 |
| **S3.** Cut an MLB or NFL commitment, for example an NFL preview slot. | owner-sized |
| **S4.** Lower the reserve. **Not recommended**, and out of scope here. | — |
| **S5.** October. A new month has 2,500 − 400 = 2,100 spendable, but CFB needs renewed authority after 2026-10-01T00:00Z. See `docs/CFB_OCTOBER_ACTIVATION_PROPOSAL.md`. | up to 300 as a demand lane, per that proposal |

### What must be implemented

- **Already exists behind a flag:** planning, capacity gate, request, parse, bind, persist, the job and the decision feed.
- **Staged today, commit `af82484`, lane still OFF:**
  - `CFB_SGO_LANE_DAILY_OBJECT_CAP` (unset by default): a per-day bound that can only lower what the gate allows. The existing cap is a total only.
  - Raw-response retention on the CFB fetch path (`fetch_events_archived`), matching `b56ed64` for MLB, NFL and Show Preview.
  - Tests: `test_cfb_sgo_bounded_allocation.py`, 4 pass. The existing CFB price and archive suites still pass.
- **Still needed before any spend:**
  1. Deploy the staged code.
  2. Get owner authority for the one validation object and a reviewer for the team IDs.
  3. Name a capacity source.

### Would CFB quotes carry per-leg clocks and conventions?

- **Clocks.** Better than MLB: the CFB persistence path stamps each side with its own `lastUpdatedAt`, and with `af82484` the bytes are kept.
- **The same gaps apply.**
  - What `lastUpdatedAt` means is unproven.
  - The settlement convention is only **asserted** in code: `"full game including overtime (SGO periodID 'game')"` (`sgo_price.py`). No retained provider document establishes it. Treat it as `NOT_STATED_BY_PROVIDER` for any research claim.
- **Coverage-only.** CFB quotes would let the CFB paper checkpoint price games. So far there are 15 CFB paper decisions: 13 BLOCKED, 2 DECLINED. They would not feed the frozen MLB pilot.

### The exact owner decision for (B)

> Enable the CFB SGO lane from **‹start›** to **‹stop ≤ 2026-10-01T00:00Z›**:
> - `CFB_SGO_LANE_OBJECT_CAP = ‹N_total›` and `CFB_SGO_LANE_DAILY_OBJECT_CAP = ‹N_day›`;
> - funded from **‹S1: `CFB_SGO_PROTECTED_HEADROOM` 10 → ‹h›, releasing ≤ 4 objects› / ‹S2› / ‹S3›**;
> - authorizing **one** live validation object to pin `CFB_SGO_LEAGUE_ID` and the first team-map entries, reviewed by **‹reviewer›**.
>
> Deploy `af82484` first so every CFB response keeps its bytes. No reserve change.

Smallest useful version: S1 with h = 6, N_total = 4 (1 validation + 3), N_day = 3, today only for the 3 uniquely timed games, stop at 2026-09-19 23:59 ET. The residual risk is on MLB/NFL projection error: 4 objects of margin are consumed.

> **Correction (2026-09-19, `docs/CFB_SPORTSBOOK_COVERAGE_20260919.md`):** h = 6 releases nothing (211 − 207 − 6 = −2; at 15:55 ET 186 − 182 − 6 = −2). Only h = 0 releases the 4 objects. The current decision, with the binder, validation path and October option, is in that document.
