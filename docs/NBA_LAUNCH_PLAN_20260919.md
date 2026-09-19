# NBA launch plan — draft, 2026-09-19

**Nothing here is approved, activated or deployed.** The NBA Gen1 consumer is built on branch
`claude/nba-gen1-integration` with every switch off and every pin empty. No provider request was made
and nothing was spent while building it.

The authority that exists today:

- The `AISP_NBA_PAPER_POLICY_V1` terms and their $1,000 virtual account. The owner approved both on
  2026-09-15; experiment `bb76cac7…` is active and holds no position.
- Codex's selected model handoff and one production forecast package, both verified byte-for-byte.
  Neither grants AISP consumer, decision or wagering authority.

Each section below says which parts need the owner.

## 1. What Codex selected, and the dates that follow from it

| Object | Identity | State |
|---|---|---|
| Selected model handoff | `7113b71ff5819c89103ad19d5f9afb22c51dbc92980be12b56ce8826cb34ccaf`: `NBA_GEN1_ELO_WIN_RIDGE_A100_ORDINARY_REST_V1` + `NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1`, 10,000 draws, effective `2026-09-18T01:16:08.176807Z` | `READY_AWAITING_ACTIVATION` · producer not activated · AISP not activated |
| Production package | `8a725a439af9f9ad4828281ff9bcdec5511c2af292c3feac4826b975dfa0d4c2`, PHI at NYK, ESPN 401909089, 2026-10-20 23:00Z. Forecast `8c69bc25…`, population `62b48824…` (logical `6d7b75fb…`) | Published 2026-09-18 01:16:32Z in V1 discovery, which has **no eligibility lease** |
| Publisher deployment (successor) | `34b4060ff4ae4609f4fb61a2c5bd8518fa0587396e5cd1c489d78ec411d8ccf6`, manifest `07c0b9b9…`. Release `515a5e12…` | Staged, **not installed**; no NBA task exists |
| Source refresh plan | `REFRESH_PLAN` `f2210fc0…`; proposal `9f39f191…`; grant template `3a778a60…` | **Not approved**; zero requests |
| Owner handoff | `3dc687f9b27dc3d73dbc66e331ffde30471d2fa58761274b615998d19970ddf1` (2026-09-18 14:55Z) | Readiness only |

**Channel semantics** come from the forecast bytes and the producer contract, and AISP keeps them apart
everywhere:

- **Ridge** gives expected points.
- **Elo** gives winner probabilities.
- **The joint lattice** gives winner, spread and total probabilities from 10,000 paired draws.

**Correction.** The earlier staged adapter (`eea993a`) read the Elo channel as a margin association. It is
not: the forecast's `eloWinnerProbabilities` is a winner probability, and AISP now treats and scores it as
one.

**Opening night, from Codex's retained 2026-09-14 capture** (identity snapshot only, not a current
schedule status):

| Game | ESPN id | Tipoff (captured) | Tipoff shared within ±5 min? |
|---|---|---|---|
| BOS at DET | 401909088 | 2026-10-20 15:00 ET | no |
| PHI at NYK | 401909089 | 2026-10-20 19:00 ET | no |
| OKC at SAS | 401909090 | 2026-10-20 21:30 ET | no |

- **Producer timeline:** the earliest legitimate capture is **2026-10-13T18:00Z**, and the first
  publication checkpoint (T−7d) is **2026-10-13T19:00Z**. Both require the owner's approvals in §4.
- **Season volume** (captured schedule, regular-season STD games): 1,205 in total; Codex supports 1,197.
- **Games per UTC billing month:**

  | Month | Games |
  |---|---|
  | 2026-10 | 84 (88 by ET date; four Oct-31 ET tipoffs bill in November UTC) |
  | 2026-11 | 212 |
  | 2026-12 | 181 |
  | 2027-01 | 236 |
  | 2027-02 | 167 |
  | 2027-03 | 232 |
  | 2027-04 | 93 |

- **Peak and mean days:** the peak day is 15 games (Nov, Dec, Apr). The mean is about 7.5 games per
  game-day.

## 2. SportsGameOdds acquisition allocation, October onward (owner decision)

**Source of objects:** the SportsGameOdds provider entitlement, 2,500 objects per UTC billing month.
Nothing else: no paid upgrade, and nothing borrowed from another month. The Odds API is not used for
NBA (§5).

**October budget.** Read-only evaluation of guard B at 2026-10-01T16:00Z: 0 used, reserve **400**,
spendable **2,100**. Guard A holds the same 400 from 2026-10-01 (budget-guard phase 2), so both guards
give 2,100.

| October 2026 (billing month 2026-10) | Objects | Basis |
|---|---|---|
| Entitlement | 2,500 | Resets 2026-10-01T00:00Z |
| Reserve (both guards) | 400 | October default |
| **Spendable** | **2,100** | |
| MLB postseason (upper bound) | ≤ 51 | CFB October proposal §1 |
| NFL checkpoints + Show Prep previews | ≈ 133 | same |
| CFB demand lane (proposed, not approved) | ≤ 300 | same |
| **NBA (proposed here)** | **≤ 100** | 84 checkpoint purchases + ≤ 16 setup (below) |
| Remaining slack | ≈ 1,516 | NFL live/period shares, then MLB |

**Unit: one object per NBA game.** That is one targeted `limit=1` request inside the buy window
T−75..T−35, so that the same-book pair is fresh at the policy's T−30..T−15 decision (older leg ≤ 240 min).
There are no previews, no refreshes, no in-game buys and no totals-only buys. The one request returns
moneyline, spread and total quotes. The adapter persists all three, but the policy prices the moneyline
only.

- **Objects per day:** the day's decision-eligible NBA games. The mean is about 7.5 and the peak is 15.
- **Objects per month:** at most the month's scheduled games (the table in §1). A game with no current
  V2 lease at buy time is never bought.

**Setup (October only, ≤ 16 objects, all counted inside the 100):**

1. **Identity validation, 1 object.** A uniquely timed opening-night game (BOS at DET 15:00 ET; buy
   window 13:45–14:25 ET). The response must show:
   - the provider's NBA league id;
   - both `teamID`s;
   - `startsAt` within ±5 min;
   - full-game oddIDs with per-book `lastUpdatedAt`;
   - exactly one object billed.

   Only then are `NBA_SGO_LEAGUE_ID` and the first two reviewed team-map entries pinned. The same request
   **measures the NBA older-leg lag**. Stop rule, mirroring CFB: if the lag exceeds 180 minutes, no
   checkpoint purchase starts until the buy window is re-proposed.
2. **Team map: 30 franchises.** Only 31 of October's 88 games are uniquely timed, so targeting by team is
   what makes full coverage possible. Decision rule:
   - First, one `limit=1` teams-listing probe with a usage reading before and after.
   - If a 30-team listing fits in the remaining setup budget, take it. Every entry is reviewed against
     the Codex franchise id before use; the map schema is `AISP_NBA_SGO_TEAM_MAP_V1`, one-to-one.
   - Otherwise grow the map from purchases on uniquely timed games. Coverage is then below 84 until the
     map is complete.

**November onward:** at most one object per scheduled game in the UTC month, with no setup unless a
provider id changes (1 re-validation object).

**Worst-case month.** January 2027 has 236 games against 2,100 spendable. That fits beside NFL (about 130)
and a renewed CFB lane (≤ 300). The binding constraint is the **daily** shaped share, not the month. The
demand-shaped allocator has no NBA lane yet, so the NBA need must be added to its demand input before
activation (§6, E1).

**Season total:** 1,205 games plus 16 setup, so at most 1,221 objects across seven billing months.

**Current authority, and why October needs the owner.**

- Both acquisition authorities in force expire at **2026-10-01T00:00Z** (Wed 09-30, 8:00 PM ET):
  - the September reserve schedule;
  - the CFB 10-object ceiling.
- The NBA lane has **no** authority today: `NBA_SGO_LANE_ENABLED=false`, `NBA_SGO_LANE_OBJECT_CAP=0`, and
  `NBA_SGO_LANE_AUTHORIZED_UNTIL` is empty.
- An October NBA allowance is a new spending decision. It is **not** assumed here.

## 3. Owner decisions (none is taken by this branch)

| # | Decision | What it unlocks | Exact terms |
|---|---|---|---|
| **O1** | Approve Codex's NBA source-refresh proposal and the successor publisher installation | Real 2026-27 packages and V2 slate discovery with eligibility leases | Proposal `9f39f191…` / plan `f2210fc0…` / deployment `34b4060f…`. Source: `sportsdataverse` GitHub asset `nba_schedule_2027.parquet` (CC BY 4.0, $0). Interval 2026-10-13 18:00Z → 2027-04-19 04:00Z. ≤ 4 HTTP requests per invocation; ≤ 8 attempts / 32 requests per UTC day; 16 MiB per invocation, 128 MiB per day; zero retries. Administrator install via Codex's `install_nba_gen1_slate_publisher.ps1 -Mode Plan/Install/Verify`. |
| **O2** | Activate the AISP consumer | Import, display, finals, scoring | Deploy this branch (migration `20260919_0048`, backend restart, frontend build). Set `NBA_PRODUCTION_HANDOFF_PATH` = `…\handoffs\sha256\7113b71f…\index.json`, `NBA_PRODUCTION_HANDOFF_SHA256=7113b71f…`, `NBA_PRODUCTION_CONSUMER_ENABLED=true`, and `NBA_FINAL_SOURCE_OBJECTS` (the 2027 capture `d4ffe0d9…` with its receipt `70d3f0e6…`). |
| **O3** | Import the HISTORICAL 2025-26 evidence once | Model-performance history (Ridge / Elo / joint, per channel) | Pin `NBA_HISTORICAL_ASSESSMENT_RECEIPT_*` = `…\nba-gen1-long-gap-v1\assessment-2026\receipt.json` / `26125dfc…` and `NBA_HISTORICAL_JOINT_RECEIPT_*` = `…\nba-gen1-joint-residual-v1\assessment\2026\receipt.json` / `d62e3efb…`. Add the 2026 object `b3884aa4…` with receipt `e8cc1882…` to `NBA_FINAL_SOURCE_OBJECTS`. Run `POST /api/v1/admin/jobs/import-nba-historical-evidence` once. It admits 1,225 FINAL games as `RESEARCH_SHADOW`; about 60 s. |
| **O4** | Extend `AISP_NBA_PAPER_POLICY_V1` to production packages | Paper positions, after O5 and O6 | A grant in schema `NBA_VIRTUAL_POLICY_EXTENSION_AUTHORIZATION_V1`, mirroring CFB's `e533fb73`: `approvedBy: "James"`, `policyVersion`, `modelConfiguration` = `NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1`, `realMoneyAuthorized: false`, `policyAuthorization.mode: VIRTUAL_RESEARCH_ONLY`, and `validUntilUtc`. Pin it with `NBA_PAPER_POLICY_GRANT_PATH/_SHA256`, then set `NBA_PAPER_DECISIONS_ENABLED=true`. |
| **O5** | Which channel controls the moneyline decision | Paper positions | The policy as approved names the **joint** win probability. Codex's later contract calls **Elo** the "controlling winner channel". The checkpoint implements the policy exactly as written (joint) and records Elo beside it. Changing to Elo is a policy change. |
| **O6** | The October SGO allowance (§2) and its monthly successors | Prices | `NBA_SGO_LANE_ENABLED=true`, `NBA_SGO_LANE_OBJECT_CAP=100`, `NBA_SGO_LANE_AUTHORIZED_UNTIL=2026-11-01T00:00:00Z`. The 1-object validation, the team-map rule and the 180-minute lag stop rule. Priority as a demand lane after MLB/NFL checkpoint needs and CFB. |
| **O7** | 2026-27 finals source | Prospective scoring | Either Codex publishes a retained final-result evidence pointer (the CFB pattern), or the owner pins each new capture object it retains into `NBA_FINAL_SOURCE_OBJECTS`. AISP makes no request either way. |
| **O8** | The Odds API for NBA | — | **Recommended: decline.** No NBA sport key is in retained evidence, the governed provider's NBA budget lane is 0, and SGO research quotes are the policy's only permitted source. |

## 4. Order of operations (proposed)

1. **By 2026-10-12:** O1 (Codex install and refresh approval) and O2 (AISP deploy and consumer on).
   - The restart must fall outside MLB/NFL/CFB decision windows.
   - O3 can run on the same day.
2. **2026-10-13 19:00Z:** the first V2 slate discovery should appear. The consumer imports it and records
   its leases. This verifies V2 import on real bytes; V2 has only been tested on fixtures shaped from
   Codex's publisher source.
3. **By 2026-10-19:** decide O4, O5 and O6.
4. **2026-10-20, 13:45–14:25 ET:** the 1-object identity validation on BOS at DET. Then pin the league id
   and team map (O6).
5. **2026-10-20 onward:** the checkpoint evaluates each game at T−30..T−15 and records a reason for every
   game. A position forms only after O4 and O6.

**If nothing is approved:** AISP keeps showing the PHI at NYK package, and any HISTORICAL rows imported
under O3. Every NBA checkpoint surface reports why it is idle. Nothing is bought.

## 5. What the adapters do today (flag-off, synthetic tests only)

- **SportsGameOdds NBA** (`app/nba/sgo_price.py`):
  - exact event binding (pinned league, reviewed team map to Codex franchise ids, ±5 min tipoff,
    orientation both ways);
  - raw-response retention (`sgo_raw_archive`);
  - lane halt on any unselected event;
  - per-leg book clocks;
  - both budget guards, and a ledger reservation before any request.

  With shipped settings it returns `LANE_DISABLED` and makes no request.
- **The Odds API:** the adapter accepts only `baseball_mlb` and `americanfootball_nfl`, and the generic
  normalizer refuses NBA events (provider league unmapped). Nothing was added (O8).

## 6. Engineering still required before activation (flagged off, not in this branch)

- **E1:** an NBA demand lane in the shaped SGO allocator, so the day's share carries NBA need (the CFB
  proposal needs the same).
- **E2:** one-team + tipoff targeting, as the CFB proposal's §3c, if the team listing is not taken.
- **E3:** the NBA older-leg lag stop rule, once the lag is measured.
