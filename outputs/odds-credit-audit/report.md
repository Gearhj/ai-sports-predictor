# Sportsbook data at zero cost — credit audit and collection strategy

**Provider** The Odds API, 500 credits per billing month · **Branch** `claude/zero-cost-odds-ingestion-v1`
**Evidence** `provider_request_ledger`, `job_runs`, `odds_snapshots`, `games` — read-only.
**No provider request was made, no account registered, no credential configured, and the quota floor of 10 was not lowered.**

Machine-readable companion: `audit.json`. Every figure below was reproduced directly against the database.

---

## 1. Exactly how the 500 credits were consumed

### The billing month is the UTC calendar month — measured, not assumed

At **2026-08-31 20:02 EDT = 2026-09-01 00:02 UTC** the provider's own headers moved
`x-requests-used` 477 → 3 and `x-requests-remaining` 23 → 497 in consecutive ledger rows.
The counter resets at 00:00 UTC on the 1st.

### The two windows

| Billing month | Ledgered calls | Ledgered credits | End state |
|---|---:|---:|---|
| 2026-08 | 154 MLB + 1 NFL | 465 | 23 remaining |
| 2026-09 (to Sep 7) | 164 MLB | 492 | **5 remaining**, used 495 |

September's entire allowance was gone by **Sep 5** (8 remaining) — **4.2 days into the month**.

### The ledger under-reports by 15 credits

Reconciling our ledger against the provider's own `x-requests-used` counter, stepping
between consecutive readings, three steps are not the expected 3 credits:

| Interval | used → used | Δ | Explanation |
|---|---|---:|---|
| 2026-08-14 12:48 → 2026-08-27 23:52 ET | 6 → 21 | **+15** | ledger records 1 call (3 credits) → **12 credits / 4 calls made entirely off-ledger** |
| 2026-08-31 19:52 → 20:02 ET | 477 → 3 | −474 | the monthly reset |
| 2026-09-04 12:54 → 13:14 ET | 420 → 426 | **+6** | ledger records 1 success (3) and 1 FAILED → **the failed call cost 3 credits** |

**Provider-metered total: 972 credits. Ledgered: 957.** Four calls between 2026-08-14 and
2026-08-27 have no ledger row and no `ingestion_runs` row — made outside the application.
Six of the seven recorded failures cost nothing; **one did** (the 2026-09-04 ReadTimeout).

### The arithmetic that did it

Every request cost **3 credits** — one per market per region:

```
ODDS_MARKETS=h2h,spreads,totals   ODDS_REGIONS=us    →  3 × 1 = 3 credits/request
```

All 326 reservations carry the identical shape `markets=["h2h","spreads","totals"], regions=us`.
The cadence then fired it ~40 times a day:

```
ODDS_DAILY_REQUEST_BUDGET=40      ODDS_PREGAME_REFRESH_MINUTES=10
odds_pregame_window_hours=4       → open whenever ANY live game starts within 4h,
                                    which on a 7–10h MLB slate is most of the day
40 × 3 = 120 credits/day          500 ÷ 120 = 4.2 days
```

Observed daily spend Sep 1–4: **117, 120, 120, 117**. The model predicts the data exactly.

### By sport, market, region, job and outcome

* **Sport:** MLB 954 ledgered credits (318 calls); NFL 3 (1 call). Region `us` throughout.
* **Job:** `refresh-odds` drove **315 of 319 successes and 945 of 957 ledgered credits — 98.7%**.
  `controlled-live-validation` 2 calls / 6 credits; 1 unattributed call / 3; `refresh-nfl-shadow-odds` 1 / 3.
* **The daily pipeline spent nothing** — its odds step is skipped in all 13 runs with
  `ODDS_POLLING_ENABLED=false`. The scheduled pregame cadence is the only lever that matters.
* **Failures:** 7, all transport-level (5 ConnectTimeout, 1 ConnectError, 1 ReadTimeout).
  No 401s, no 429s. Six free, one charged.
* **Orphan reservations:** 2, bookkeeping only.

### What the credits actually bought

| Market | MLB snapshots stored | Usable by the governing policy? |
|---|---:|---|
| MONEYLINE | 72,840 | **Yes** |
| SPREAD | 73,158 | No |
| TOTAL | 73,432 | No |

`AISP_PRACTICAL_PREGAME_POLICY_V2_1` declares `supported_markets: ["MONEYLINE"]`.
**638 of 957 ledgered credits (67%)** bought markets the policy cannot act on.

And the 10-minute cadence bought little that was new: **76.0% of the 219,430 MLB snapshot
rows carry a price identical to the previous row for the same outcome and book.**

Nine bookmakers produce complete same-book two-sided MLB moneylines (DraftKings 4,482
paired observations, BetMGM 4,273, FanDuel 4,266, …). Quote availability was never the
constraint — the budget was.

### What that spending actually achieved — the honest before-state

Per game, did any moneyline observation land inside `[cutoff − 240min, cutoff]`?

| Window | Games | Uncovered |
|---|---:|---:|
| Funded polling era, Aug 28 – Sep 4 (~120 credits/day) | 113 | **13 (11.5%)** |
| After exhaustion, Sep 5 – 9 | 71 | 67 (94.4%) |
| Before routine polling, Aug 13 – 27 | 30 | 29 (96.7%) |

The like-for-like figure is **11.5%**: even fully funded at 120 credits a day, one game in
nine still had no usable quote at its own decision cutoff. The other misses are simply the
absence of money, not its misallocation.

### Root cause, in one sentence

> A 10-minute blanket poll of the whole sport, gated only by "some game starts within four
> hours", asking for three markets when the policy can act on one, spent 120 credits a day
> against a 500-credit month — and still missed 11.5% of games.

---

## 2. What the decision contract actually requires

| Constant | Value | Where |
|---|---|---|
| `FINAL_PREGAME_CUTOFF_MINUTES` | 15 | `shadow_experiment_service.py:58` |
| `FINAL_FREEZE_WINDOW_MINUTES` | 15 | `shadow_experiment_service.py:63` |
| `MAX_QUOTE_STALENESS_MINUTES` | 240 | `opportunity_service.py:38` |
| staleness test | `observed_at < now − 240min` | `opportunity_service._is_stale` |

The final evaluation happens in `[cutoff − 15min, cutoff]`. Worst case `now = cutoff`:

```
observed_at ∈ [cutoff − 240min, cutoff]        cutoff = first pitch − 15min
```

**The decisive fact:** one `GET /v4/sports/{sport}/odds` returns *every* upcoming event for
that sport at a cost independent of event count — our ledger shows `records_received` of
11–28 against 7–17 games. Collection is a **minimum point cover** over the slate's cutoffs,
not a per-game poll.

A request observed at `r` serves cutoffs in `[r + 25min, r + 240min]` — 25 minutes of
headroom so it is fetched, normalised and committed before the freeze window opens.

**A scheduler cannot fire at an instant.** A 5-minute tick lands *after* its checkpoint, so
the plan reserves 10 minutes of tick slack: a checkpoint is placed at
`cutoff − 35min` and credited only with `[cp + 35min, cp + 230min]`. A request that fires
anywhere in `[cp, cp + 10min]` therefore still genuinely covers everything it was credited
with. An earlier draft omitted this and covered **zero** games at four of five tick phases.

---

## 3. Before and after

| | Before | After |
|---|---|---|
| MLB request cost | 3 credits | **1 credit** (h2h — the only market V2.1 can price) |
| MLB requests/day | ~40 | **1–4** (2.31 mean, 4 worst, over 200 real slates) |
| MLB credits/day | ~120 | **1–4** |
| NFL request cost | 3 credits | 3 (research: spreads/totals retained, lower priority) |
| NFL requests/week | n/a | **2–9** (5.7 mean, 9 worst, over the real 18-week season) |
| Trigger | "any game within 4h", every 10 min | a due checkpoint from the persisted schedule |
| Stops at cutoff | no | **yes** |
| Inactive sport | still polled | **never** |
| Budget guard | daily *request* count | daily **credit** cap + monthly cap + untouchable reserve |
| Coverage (measured) | 11.5% of games missed | **0% missed** |

### Verified by replaying a real scheduler

The planner was driven by a simulated **5-minute tick at five different phase offsets**
(0s, 37s, 123s, 271s, 299s) across **every game in the database** — 2,644 MLB and 272 NFL —
with each request timestamped at the tick that actually fired it, then checked against the
true contract:

```
MLB  461 requests over 200 game-days   0 of 2,644 games uncovered   worst 4/day
NFL  102 requests over  57 game-days   0 of   272 games uncovered   worst 4/day
```

Identical at every offset. This is the test that caught the phase-lock, and it ships as a
regression test.

---

## 4. Projected monthly budget

Worst observed rates, not averages:

| Sport | Requests | Cost each | Credits/month |
|---|---|---:|---:|
| MLB | 4/day × 31 days | 1 | **124** |
| NFL | 9/week × 4.43 weeks | 3 | **120** |
| | | **Total** | **244** |

Against a 500-credit month:

* **Spendable** 350 (cap 500 − reserve 150)
* **Projected worst case** 244 → **106 credits of headroom** on top of the reserve
* **Reserve** 150, enforced in `BudgetState.may_spend`, not merely asserted
* **Daily cap 18**, sized from the replay: worst MLB day 4 credits + worst NFL day 12 = 16 peak

### The one number that is inferred rather than measured

Cost `= markets × regions` is consistent with every row we have, but every row we have used
**three** markets. **We have never measured the cost of a one-market request.** If `h2h`
turns out to cost 3 rather than 1:

| Branch | MLB | NFL | Total | Fits in 350? |
|---|---:|---:|---:|---|
| h2h = 1 (expected) | 124 | 120 | **244** | yes, 106 spare |
| h2h = 3, NFL unchanged | 372 | 120 | 492 | **no** — guard bites ~day 22 |
| h2h = 3, NFL reduced to h2h | 372 | 40 | 412 | no |
| h2h = 3, NFL h2h, MLB capped at 3/day | 279 | 40 | **319** | yes, 31 spare |

**The first request after the reset measures this**: `_policy_cost` reads the real
`x-requests-last` header, so the budget self-corrects from that call onward and degrades by
refusing rather than by overspending. If the measured cost is 3, apply the fourth row.

---

## 5. Is $0/month operationally viable?

**Yes — with 106 credits of headroom in the expected branch, and a stated fallback if the
one inferred number is wrong.** MLB moneyline alone, the only thing the governing policy
can act on, needs 124 of 500.

Two honest qualifications:

1. **Nothing collects before 2026-10-01T00:00Z.** 5 credits remain against a floor of 10,
   and September's ledgered spend already exceeds the 350 spendable ceiling. Both guards
   refuse every request for the rest of the month. This work prevents the recurrence; it
   cannot recover September.
2. The projection depends only on what this repository can prove: the cost formula, the
   all-events-per-request behaviour, the real slate shapes, and AISP's own canonical
   schedule for discovery. **It does not depend on any unverified provider endpoint** — in
   particular I did not assume a free `/v4/sports/{sport}/events` endpoint exists.

### About the "zero-cost" balance endpoint

`GET /v4/sports/` is **documented by the provider** as returning quota headers without
consuming a credit, and the adapter records `credit_cost: 0`. **It has never actually been
called from this system** — both `quota_recovery_probe` rows are reservations that never
completed — so the cost is documented, not measured. Nothing in the budget above depends on
it. It matters in one place: it is the mechanism that can release the floor latch, and the
floor is therefore *not* absolutely inviolable — the existing recovery path may release it
once per `odds_quota_probe_cooldown_hours`. That path is unchanged by this work.

---

## 6. SportsGameOdds as a September fallback

**Nothing about SportsGameOdds is verified,** and it delivers **zero September relief.** No
account was registered, no credential configured, no documentation fetched, no request made.
The repository contained no reference to the provider at all.

These remain **UNVERIFIABLE WITHOUT NETWORK ACCESS**, each independently blocking enablement:
MLB/NFL coverage · which bookmakers · whether prices carry an observation timestamp ·
whether both moneyline sides come from one book · free-tier request vs object accounting ·
licence terms · event-identity mapping.

**What was built, safely:** `app/data_sources/sportsgameodds.py`, behind
`SPORTSGAMEODDS_ENABLED=false`, deliberately incapable of guessing:

* no default base URL and no default field mapping;
* requires a field-mapping manifest **bound to a real observed response**
  (`verifiedAgainstResponseSha256`) — an unchecked manifest is refused;
* enabling the flag is **not** sufficient to make a request: it refuses at the transport
  boundary because authorization has not been given;
* `readiness()` reports `ready_to_enable: false` and lists every unverified assumption.

If the hypothesised schema is wrong, the failure mode is **no data** — never wrong data.

---

## 7. Provider-neutral ingestion

The masquerade risk was real, specific, and wider than first assessed. Adversarial review
found **five** governed paths reading `odds_snapshots` with no provider predicate. All are
now closed, with two independent barriers:

1. **Namespace separation, enforced at the write boundary.** `Normalizer.ingest_odds` — the
   only place that creates `Sportsbook` rows — now resolves the book through
   `provider_registry.identity(source).bookmaker_key(...)`, so a non-governed provider gets
   its own `Sportsbook` rows (`sgo:draftkings`) and its own display name
   ("DraftKings (SportsGameOdds, research)"). Because governed two-sided pairing keys on
   `sportsbook_id`, a mixed-provider pair is structurally impossible. Putting this in the
   normalizer rather than in the adapter means a caller cannot bypass it.
2. **Explicit source filtering on every governed read**, applied to LIVE rows (the only ones
   that can reach a decision):
   * `ReadService._latest_market_snapshots` — the V2.1 pricing feed
   * `funnel_read_context` — the set-based slate read model
   * `bankroll/positions._moneyline_candidates` — **official position creation**, which
     selects the best price *across books* and so had no structural protection at all;
     it now filters and additionally calls `require_governed` at the point of use
   * `ProspectiveShadowService._best_snapshot` — the governed rescore gate
   * `ReadService._market_feed_status` — an unqualified `MAX(observed_at)` over the whole
     table, which a single fallback row would have made look fresh while the governed
     provider was dark

Provenance is preserved throughout: `source`, `source_mode`, `source_event_id`,
`observed_at`, `available_as_of`, `payload_checksum`, bookmaker and market identity. The
existing dedup key already includes `source`, so providers coexist as separate attributable
rows rather than overwriting each other.

**Verified by test:** a *newer* `SPORTSGAMEODDS` row on the same market outcome does not
appear in the governed read path and does not change what the policy can see.

**Known residual:** `odds_snapshots` still has no unique constraint beyond its primary key,
so two providers writing the same `(market_outcome_id, sportsbook_id, observed_at)` would
duplicate rather than collide. Namespacing makes that combination unreachable in practice;
a unique index is the belt-and-braces follow-up and is **not** applied here because adding
one to a 222,318-row table is a migration that deserves its own review.

---

## 8. Operator status (admin-gated)

`GET /api/v1/admin/odds-collection` (AdminUser) and an **Odds credit budget** panel on the
admin health page report: credits used and remaining this billing month with the reserve
shown as untouchable · **projected exhaustion date** at both observed and planned burn ·
the active **collection mode** · the **root cause** of a missing quote in words · the
per-sport plan · provider governance and what blocks the fallback.

The public read model is unchanged: it still says only whether the market feed is blocked
and why, with no balance, cost structure or key.

**A stale reading proves nothing.** If the newest reading predates the current billing
window the state is `PROVIDER_BALANCE_UNKNOWN`, and the budget falls back to AISP's own
ledger arithmetic, explicitly labelled a lower bound. August's "497 remaining" can never
imply September credits are available.

---

## 9. What remains blocked

| Blocked on | Detail |
|---|---|
| **Time** | September is spent (5 credits left, floor 10). Nothing collects before 2026-10-01T00:00Z. |
| **Your decision** | `ODDS_CHECKPOINT_COLLECTION_ENABLED=true`. Off by default; this session did not enable it. |
| **Authorization** | Any SportsGameOdds contact — account, credential, documentation fetch, or a single request. All refused. |
| **Measurement** | The true credit cost of a one-market request. The first post-reset call measures it. |

### The exact next actions

1. **Now:** nothing. The floor guard is correct; leave it.
2. **At/after 2026-10-01T00:00Z:**

   ```
   ODDS_CHECKPOINT_COLLECTION_ENABLED=true
   ODDS_MONTHLY_CREDIT_CAP=500
   ODDS_MONTHLY_CREDIT_RESERVE=150
   ODDS_DAILY_CREDIT_BUDGET=18
   ODDS_POLLING_ENABLED=false       # unchanged — blanket polling stays off
   ODDS_QUOTA_FLOOR=10              # unchanged
   ```

   Then read the operator panel after the first request: if `credit_cost` came back as 3
   rather than 1, apply the fourth branch of the §4 table (NFL to `h2h`, MLB capped at 3/day).
3. **Only if you want the fallback:** authorise one SportsGameOdds request, record the
   response, and write the field-mapping manifest bound to its SHA-256. Until then the
   adapter stays inert.

No paid data request, subscription or credential change is required for MLB and NFL to
operate inside the free 500-credit plan.
