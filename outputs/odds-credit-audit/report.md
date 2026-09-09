# Sportsbook data at zero cost — credit audit and collection strategy

**Provider** The Odds API, 500 credits per billing month · **Branch** `claude/zero-cost-odds-ingestion-v1`
**Evidence** `provider_request_ledger`, `job_runs`, `odds_snapshots`, `games` — read-only.
**No provider request was made, no account registered, no credential configured, and the quota floor of 10 was not lowered.**

Machine-readable companion: `audit.json` in this directory.

---

## 1. Exactly how the 500 credits were consumed

### The billing month is the UTC calendar month — measured, not assumed

At **2026-08-31 20:02 EDT = 2026-09-01 00:02 UTC** the provider's own headers moved
`x-requests-used` 477 → 3 and `x-requests-remaining` 23 → 497 in consecutive ledger rows.
The counter resets at 00:00 UTC on the 1st.

### The two windows

| Billing month | Successful calls | Credits | End state |
|---|---:|---:|---|
| 2026-08 | 154 MLB + 1 NFL | **465** | 23 remaining on Aug 31 |
| 2026-09 (to Sep 7) | 164 MLB | **492** | **5 remaining**, used 495 |

September's entire 500-credit allowance was gone by **Sep 5** (8 remaining) — **4.2 days into the month.**

### The arithmetic that did it

Every request cost **3 credits**, because The Odds API charges one credit per market
per region and the configuration asks for three markets in one region:

```
ODDS_MARKETS=h2h,spreads,totals   ODDS_REGIONS=us    →  3 × 1 = 3 credits/request
```

All 326 reservations carry the identical request shape `markets=["h2h","spreads","totals"], regions=us`.

The cadence then fired that request ~40 times a day:

```
ODDS_DAILY_REQUEST_BUDGET=40      ODDS_PREGAME_REFRESH_MINUTES=10
odds_pregame_window_hours=4       → the gate is open whenever ANY live game
                                    starts within 4h, which on a 7–10h MLB slate
                                    is most of the afternoon and evening
40 requests/day × 3 credits = 120 credits/day        500 ÷ 120 = 4.2 days
```

Observed daily spend, Sep 1–4: **117, 120, 120, 117** credits. The model predicts the data exactly.

### By date, sport, market, region, job and outcome

* **By sport:** MLB 954 credits (318 calls); NFL 3 credits (1 call). Regions: `us` only, throughout.
* **By job:** `refresh-odds` (the scheduled cadence) drove **315 of 318 successes and 945 of 954 credits — 99%**.
  `controlled-live-validation` 2 calls / 6 credits; one manual call / 3 credits; `refresh-nfl-shadow-odds` 1 call / 3 credits.
* **Failures:** 7 (4 in August, 3 in September). Every one has `quota_cost` null and
  `response_received: false`, and `quota_remaining` does not move across them — **failures consumed no credits.**
* **Orphan reservations:** 2, both bookkeeping only (a reservation with no terminal row); no credit implication.
* **Zero-cost endpoint:** the 2 `quota_recovery_probe` reservations are recorded at cost 0 and never completed.

### What the credits actually bought

| Market | MLB snapshots stored | Usable by the governing policy? |
|---|---:|---|
| MONEYLINE | 72,840 | **Yes** |
| SPREAD | 73,158 | No |
| TOTAL | 73,432 | No |

`AISP_PRACTICAL_PREGAME_POLICY_V2_1` declares `supported_markets: ["MONEYLINE"]`.
So **636 of 957 credits (66.5%)** — 328 of September's 492 — bought markets that the
governing policy is structurally incapable of acting on.

The remainder was not wasted on stale data: only **4 of 318** calls produced zero new
snapshot rows (12 credits). The books genuinely re-quoted. The waste was **market scope
and polling granularity**, not repetition of identical prices.

Nine bookmakers produce complete same-book two-sided MLB moneylines
(DraftKings 4,482 paired observations, BetMGM 4,273, FanDuel 4,266, …), so quote
availability was never the constraint — the budget was.

### Root cause, in one sentence

> A 10-minute blanket poll of the whole sport, gated only by "some game starts within
> four hours", multiplied by a three-market request that the MONEYLINE-only policy could
> use one third of, spent 120 credits a day against a 500-credit month.

---

## 2. What the decision contract actually requires

Measured from the code, not assumed:

| Constant | Value | Where |
|---|---|---|
| `FINAL_PREGAME_CUTOFF_MINUTES` | 15 | `shadow_experiment_service.py:58` |
| `FINAL_FREEZE_WINDOW_MINUTES` | 15 | `shadow_experiment_service.py:63` |
| `MAX_QUOTE_STALENESS_MINUTES` | 240 | `opportunity_service.py:38` |
| staleness test | `observed_at < now − 240min` | `opportunity_service._is_stale` |

The final evaluation happens at some instant in `[cutoff − 15min, cutoff]`. Taking the
worst case `now = cutoff`, a game is priceable if and only if at least one observation
falls in:

```
observed_at ∈ [cutoff − 240min, cutoff]        where cutoff = first pitch − 15min
```

**The decisive fact:** one `GET /v4/sports/{sport}/odds` returns *every* upcoming event
for that sport at a cost that does not scale with event count — our own ledger shows
`records_received` of 11–28 against 7–17 games on the slate. Collection is therefore not
a per-game problem; it is a **minimum point cover** over the slate's cutoffs.

A request at time `t` covers exactly the cutoffs in `[t + 25min, t + 240min]`
(25 minutes of headroom so the response is fetched, normalised and committed before the
freeze window opens). Greedily placing each request at
`earliest_uncovered_cutoff − 25min` is the optimal cover.

---

## 3. Before and after

| | Before | After |
|---|---|---|
| MLB request cost | 3 credits (h2h+spreads+totals) | **1 credit** (h2h — the only market V2.1 can price) |
| MLB requests/day | ~40 | **1–3** (2.06 mean over 16 real slates, 3 worst) |
| MLB credits/day | ~120 | **1–3** |
| NFL request cost | 3 credits | 3 credits (research: spreads/totals kept, lower priority) |
| NFL requests/week | n/a (1 call ever) | **4–7** (4.56 mean over the real 18-week schedule) |
| Trigger | "any game within 4h", every 10 min | a due checkpoint from the persisted schedule |
| Stops at cutoff | no | **yes** — a passed cutoff plans nothing |
| Inactive sport | still polled | **never** |
| Budget guard | daily *request* count only | daily **credit** cap + monthly cap + untouchable reserve |

### Verified against every real slate

The planner was run over all 16 MLB slates and all 57 NFL game-days in the database.
**Zero games uncovered**, in every case.

```
MLB   16 days, 33 checkpoints, mean 2.06/day, worst 3/day   → all games covered
NFL   18 weeks, mean 4.56 checkpoints/week, worst 7/week    → all games covered
```

---

## 4. Projected monthly budget

Worst observed rates, not averages:

| Sport | Requests | Cost each | Credits/month |
|---|---|---:|---:|
| MLB | 3/day × 31 days | 1 | **93** |
| NFL | 7/week × 4.43 weeks | 3 | **93** |
| | | **Total** | **186** |

Against a 500-credit month:

* **Spendable** 350 (cap 500 − reserve 150)
* **Projected use** 186
* **Headroom** 164 credits on top of the reserve
* **Reserve** 150, never spent — enforced in `BudgetState.may_spend`, not just asserted here
* **Daily cap** 12 credits (worst single day: MLB 3 + NFL Sunday 6 = 9)

**Both sports fit inside 500 credits with 63% of the month's allowance untouched.**
If NFL were reduced to h2h only, the total would fall to 124.

---

## 5. Is $0/month operationally viable?

**Yes, for MLB moneyline — the only thing the governing policy can act on — with a wide margin.**
93 of 500 credits covers every MLB slate observed, and 186 covers MLB plus full NFL
research markets.

Two honest qualifications:

1. **September is already spent.** 5 credits remain against a floor of 10, so the existing
   guard correctly refuses every request until the counter resets at **2026-10-01T00:00Z**.
   The new strategy cannot demonstrate a live saving this month; it prevents the recurrence.
2. The projection depends only on things this repository can prove: the observed cost
   formula (markets × regions), the observed all-events-per-request behaviour, the real
   slate shapes, and AISP's own canonical schedule for discovery. **It does not depend on
   any unverified provider endpoint.** In particular I did not assume a free
   `/v4/sports/{sport}/events` endpoint exists — the repo has no evidence for it and no
   part of the budget relies on it.

---

## 6. SportsGameOdds as a September fallback

**Nothing about SportsGameOdds is verified.** No account was registered, no credential
configured, no documentation fetched, no request made — as instructed. The repository
contains no reference to the provider at all.

The following remain **UNVERIFIABLE WITHOUT NETWORK ACCESS**, and each independently
blocks enablement:

| Question | Status |
|---|---|
| MLB and NFL coverage on the free tier | unverified |
| Which bookmakers are included | unverified |
| Whether each price carries an observation timestamp | unverified |
| Whether both sides of a moneyline come from one book | unverified |
| Free-tier request vs object accounting | unverified |
| Licence permitting this research use | unverified |
| Event identities mappable to AISP canonical games | unverified |

**What was built anyway, safely:** `app/data_sources/sportsgameodds.py`, behind
`SPORTSGAMEODDS_ENABLED=false`. It is deliberately incapable of guessing:

* It has **no default base URL and no default field mapping.**
* It requires a **field-mapping manifest bound to a real observed response**
  (`verifiedAgainstResponseSha256`). A manifest that has never been checked against a
  payload is refused with "never been checked".
* Turning the flag on is **not** sufficient to make a request: `get_market_snapshot`
  refuses at the transport boundary because authorization has not been given.
* Its `readiness()` reports `ready_to_enable: false` and lists every unverified assumption.

So if the hypothesised schema is wrong, the failure mode is **no data** — never wrong
data attributed to the wrong market.

---

## 7. Provider-neutral ingestion: two independent barriers

The masquerade risk was real and specific. `ReadService._latest_market_snapshots`
reduced snapshots per `(market, sportsbook)` **with no provider filter**, and
`_market_inputs` then paired the two sides of a market by `books[book.id][outcome.id]`.
`Normalizer.ingest_odds` resolves a sportsbook by `Sportsbook.key` alone. A second
provider writing `draftkings` would therefore have shared one `Sportsbook` row and could
have supplied **one side** of a "same-book two-sided quote".

Two barriers now close it, neither depending on the other:

1. **Namespace separation** — a non-governed provider's bookmaker keys are prefixed
   (`sgo:draftkings`), so it gets its own `Sportsbook` rows. Because pairing keys on
   `sportsbook_id`, a mixed-provider pair is *structurally impossible*, not merely filtered.
2. **Explicit source filtering** — the governed read path restricts
   `odds_snapshots.source` to `GOVERNED_SOURCES = {THE_ODDS_API}`, in both
   `ReadService._latest_market_snapshots` and the set-based `funnel_read_context`.

Provenance is preserved throughout: `source`, `source_mode`, `source_event_id`,
`observed_at`, `available_as_of`, `payload_checksum` and the bookmaker/market identity all
survive. The existing ingestion dedup key already includes `source`, so providers cannot
overwrite each other; they coexist as separate, attributable rows.

**Verified by test:** a *newer* `SPORTSGAMEODDS` row on the same market outcome does not
appear in the governed read path and does not change what the policy can see.

---

## 8. Operator status (admin-gated, not public clutter)

`GET /api/v1/admin/odds-collection` (AdminUser) and a new **Odds credit budget** panel on
the admin health page report:

* credits used and remaining this billing month, with the reserve shown as untouchable
* **projected exhaustion date** at both the observed and the planned burn rate
* the active **collection mode** (`DISABLED` / `EVENT_CHECKPOINT` / `BUDGET_EXHAUSTED` / `PROVIDER_FLOOR_LATCHED`)
* the **root cause** of a missing quote, in words
* the per-sport plan: open games, checkpoints left, markets, credits per request
* provider governance: who is governed, who is research-only, and what blocks the fallback

The public read model is unchanged: it still says only whether the market feed is blocked
and why, with no credit balance, cost structure or key attached.

**A stale reading proves nothing.** If the newest provider reading predates the current
billing window, the state is reported as `PROVIDER_BALANCE_UNKNOWN` and the budget falls
back to AISP's own ledger arithmetic, explicitly labelled a lower bound. The August
reading of "497 remaining" can never imply September credits are available.

---

## 9. What remains blocked

| Blocked on | Detail |
|---|---|
| **Time** | September's 500 credits are spent (5 left, floor 10). The guard holds until the reset at 2026-10-01T00:00Z. |
| **Your decision** | Setting `ODDS_CHECKPOINT_COLLECTION_ENABLED=true`. It is off by default and this session did not enable it. |
| **Authorization** | Any SportsGameOdds contact: account creation, credential, documentation fetch, or a single request. All refused as instructed. |
| **Nothing else** | The strategy, budget guards, provider neutrality and operator surface are implemented and tested. |

### The exact next actions

1. **Now, free:** nothing. The floor guard is correct; leave it.
2. **After 2026-10-01T00:00Z** (or after the probe cooldown, using the documented
   zero-credit balance endpoint the repo already implements):

   ```
   ODDS_CHECKPOINT_COLLECTION_ENABLED=true
   ODDS_MARKETS=h2h                 # or leave as-is; per-sport policy overrides it
   ODDS_MONTHLY_CREDIT_CAP=500
   ODDS_MONTHLY_CREDIT_RESERVE=150
   ODDS_DAILY_CREDIT_BUDGET=12
   ODDS_POLLING_ENABLED=false       # unchanged — blanket polling stays off
   ODDS_QUOTA_FLOOR=10              # unchanged
   ```
3. **Only if you want the fallback:** authorise one SportsGameOdds request, record the
   response, and write the field-mapping manifest bound to its SHA-256. Until then the
   adapter stays inert.

No paid data request, subscription or credential change is required for MLB and NFL to
operate inside the free 500-credit plan.
