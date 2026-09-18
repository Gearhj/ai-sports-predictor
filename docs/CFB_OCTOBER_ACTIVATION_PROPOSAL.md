# CFB October activation: one consistent plan (revised)

Prepared 2026-09-18 and revised the same evening. **Nothing here is approved or active. October activation still needs its own authorization.**

Three authorizations expire at **2026-10-01T00:00:00Z**, which is **Wed 09-30 at 8:00 PM ET**:

- the producer's recurring CFBD plan `3fead29f…`;
- your virtual-policy grant `e533fb73…`;
- AISP's ten-object CFB SGO ceiling (`CFB_SGO_LANE_AUTHORIZED_UNTIL`).

Nothing here assumes renewal.

Calendar used throughout: Tue 09-29, **Wed 09-30**, **Thu 10-01**, Fri 10-02, Sat 10-03.

## 1. SportsGameOdds in October (reset entitlement)

| Item | Objects | Basis |
|---|---|---|
| Entitlement | 2,500 | Resets 2026-10-01T00:00Z (UTC billing month) |
| Reserve (both guards) | 400 | The October default, kept unless you separately authorize otherwise. September's 300 applies only to service days 09-14..09-30 **inside billing month 2026-09** (budget-guard phase 2). |
| **Spendable** | **2,100** | Guard A = guard B |
| MLB postseason (upper bound) | ≤ 51 | ≤ 45 October games × 17/15 |
| NFL | ≈ 133 | 60 checkpoints plus about 73 Show Prep previews |
| **CFB (proposed ceiling)** | **≤ 300** | Everything CFB, counted in section 2 |
| Remaining slack | ≈ 1,616 | Existing allocator: NFL live and period shares, then MLB |

The CFB lane is a **demand lane after MLB and NFL projected need**, never taken from their shares. Both guards apply on every request.

## 2. One ceiling: setup and purchases both count inside 300

Every CFB object counts against the same 300 under ledger key `sgo_cfb_events`: validation, probes, any listing, checkpoint purchases, repeats and charged failures.

| Component | Objects | Notes |
|---|---|---|
| Identity validation | 1 | Section 3a |
| Team-listing cost probe | ≤ 1 | Section 3b; `limit=1`, with a free usage reading before and after |
| Team listing (only if the probe's measured cost qualifies) | 0 to N | Section 3b decision rule |
| **Setup S** | 1 + probe + listing | |
| Checkpoint purchases P | ≤ min(283, 300 − S) | One per targetable, eligible game; no previews, no refreshes |

October has **283** FBS-vs-FBS games (CFBD schedule, weeks 5–9; 193 kickoff times still TBD).

**Coverage tradeoff.** If S ≤ 17, every targetable game can be bought. Above 17, each additional setup object removes one game: coverage ≤ 300 − S. For example, S = 140 leaves at most 160 games.

## 3. How teams are identified before a targeted purchase needs their ids

A purchase is made only when one request can address exactly one event. Otherwise the plan records `UNTARGETABLE_SHARED_KICKOFF` / `TEAM_NOT_MAPPED` and buys nothing. No SGO league, event or team id is ever invented. Each id enters the map only after it has been observed in a provider response and **reviewed** against the CFBD school id: exact school name and conference match, and anything else held for review, unused.

### 3a. Identity validation (1 object)

- **Game:** a kickoff no other CFBD game shares within ±5 minutes. Both Thu 10-01 games qualify: **Western Kentucky @ New Mexico State, 8:00 PM ET**, and **North Texas @ Tulsa, 9:00 PM ET**.
- **Request:** `limit=1`, the documented candidate league `NCAAF`, and the ±5-minute kickoff window.
- **It must show:**
  1. the returned league id;
  2. both teams' `teamID`s;
  3. `startsAt` within 5 minutes of CFBD;
  4. full-game moneyline `oddID`s with each book's `lastUpdatedAt`;
  5. exactly one object billed.
- Only then are `CFB_SGO_LEAGUE_ID` and the first two reviewed map entries pinned.

### 3b. The rest of the map: measured, not assumed

Simulated on the retained CFBD schedule for October's 283 games, with TBD kickoffs treated as non-unique:

| Map source | Games targetable | Setup S |
|---|---|---|
| Purchases only, current adapter (both teams must be mapped) | **40** | 1 |
| Purchases only, **one reviewed team + kickoff window** (adapter change, 3c) | **110** | 1 |
| Reviewed teams listing | up to **283** | 1 + probe + listing cost |

The October slate has 138 distinct FBS teams.

**Decision rule, fixed now:**

1. Run one `limit=1` teams-listing probe for the validated league, with a usage reading before and after.
   - If it bills **0**, take the full listing: coverage up to 283 at S ≤ 2.
   - If it bills per team and can be restricted to FBS (≈ 138), take it only if 300 − S exceeds 110. At S ≈ 140 that means about 160 games.
   - Otherwise, skip the listing and grow the map from purchases (about 110 games).
2. Either way, every listing entry is reviewed by name and conference before use.

### 3c. AISP adapter change (small; tests exist)

`sgo_price.targeting` accepts **one reviewed team id plus the ±5-minute kickoff window**. A team plays one game at a time, so this still addresses exactly one event. `bind_quote_event` then verifies:

- the mapped team's side;
- the kickoff;
- the opponent's name against CFBD. The opponent's id becomes a *pending-review* entry and is not used for targeting until reviewed.

## 4. Purchase timing against real quote timestamps

The policy rule, unchanged: at the decision (T−30..T−15), the **older** leg of the same-book pair must be ≤ 240 minutes old.

For a purchase made at lead L before kickoff, where lag = acquisition time − the older leg's book timestamp:

- older-leg age at T−15 = lag + (L − 15);
- the requirement is **lag ≤ 255 − L**, which is ≤ 180 min when buying at T−75 and ≤ 220 min at T−35.

Measured on AISP's own SGO acquisitions since 09-01 (older leg of each same-book moneyline pair):

| Sport | Pairs | Older-leg lag at acquisition: p50 / p90 / p99 / max | Bought inside T−75..T−35 | Older-leg age at T−15 (max) | ≤ 240 min |
|---|---|---|---|---|---|
| MLB | 16,426 | 6.3 / 8.8 / 12.2 / 15.6 min | 998 | 70.5 min | 100% |
| NFL | 171 | 5.6 / 6.7 / 8.8 / 9.0 min | 36 | 64.1 min | 100% |

With the worst observed lag (15.6 min), a T−75 purchase is still only about 76 minutes old at T−15. That leaves a margin of about 164 minutes.

CFB lag is **unmeasured**: college books may update less often. The identity validation records it. **Stop rule:** if the validated CFB older-leg lag exceeds 180 minutes, no checkpoint purchase starts until the buy window is re-proposed. Buying anyway would spend objects on quotes the checkpoint would reject as stale.

## 5. The Wed 09-30, 8:00 PM ET transition

At 2026-10-01T00:00Z the Eastern service day is still Wed 09-30, but the UTC billing month becomes 2026-10.

**Automatic, and already verified in code and tests:**

- **SGO:** the entitlement resets to 2,500 and guard B's ledger month resets. `reserve_at` returns **400** for both guards, because September's 300 entry is bound to billing month 2026-09. Tested: 2026-09-30T23:59:59Z gives 300 and 2026-10-01T00:00:00Z gives 400.
- **Grant `e533fb73` expires:** the CFB checkpoint records `POLICY_GRANT_EXPIRED` from then on.
- **Producer plan `3fead29f` expires:** no new CFB forecasts arrive until its successor is published.
- **CFB lane authorization:** reads `AUTHORIZATION_EXPIRED`. The lane is off anyway.

**Why nothing has to happen at 8 PM Wednesday:**

- No CFB game kicks off between Wed 09-30 8:00 PM ET and Thu 10-01 8:00 PM ET. The first October checkpoint window is WKU @ NMSU, **Thu 7:30–7:45 PM ET**.
- The MLB Wild Card games on Tue/Wed/Thu still have placeholder times. A Wednesday-evening MLB purchase bills to September before 8 PM ET and to October after. MLB and NFL decisions do not depend on the CFB grant.
- The successor handoff, grant and lane config can all be pinned **Thu 10-01 in the morning**, with one restart. The restart must be:
  - outside any MLB Wild Card window once those times are published;
  - before 7:00 PM ET;
  - clear of the NFL TNF window at 7:45–8:00 PM ET and the CFB window at 7:30–7:45 PM ET.

## 6. Renewals to approve (exact terms; unchanged from the first draft)

### 6a. Producer recurring plan (Codex publishes; you approve the exact SHA)

Successor to plan `3fead29fb55a23eb91a1d7051a762a3f1786b87421d2eff8c009e55e34975a60`. The model deployment is unchanged: `83e8bb75a410c23bd72029a5d341d89c5d95a7d26f3a7e329e07de24647f8a14`, or an operational successor that keeps the numerical model and population.

| Term | Proposed |
|---|---|
| Validity | 2026-10-01T00:00:00Z → 2026-11-01T00:00:00Z |
| Endpoints | Same as `3fead29f`. **No `/lines`.** |
| Per capture | 7 requests, 25 MiB |
| Per UTC day | 28 requests, 100 MiB |
| Period | 600 requests. CFBD Free tier: 1,000 a month shared, resetting Oct 1. |
| Retention | 2 GiB |
| Retries | 0 |
| Cost | $0 |
| Owner | Same single owner: S4U/Limited/IgnoreNew, 15-minute cadence, 90-minute capture spacing |

### 6b. Virtual-policy grant (Codex retains it; you reply explicitly)

Same schema and terms as grant `e533fb73`. Only the dates change.

```json
{"schema":"CFB_VIRTUAL_POLICY_EXTENSION_AUTHORIZATION_V1","approvedBy":"James",
 "policyVersion":"AISP_CFB_PAPER_POLICY_V1","modelConfiguration":"CFB_GEN1_RIDGE_A100_PAIRED_RESIDUAL_V1",
 "realMoneyAuthorized":false,"modelPromotionAuthorized":false,"consumerVirtualExperimentAuthorized":true,
 "policyAuthorization":{"mode":"VIRTUAL_RESEARCH_ONLY","marketScope":["MONEYLINE"],"kellyFraction":0.25,
   "positionCapFraction":0.005,"initialPositionCapUsd":5,"sameGameCapFraction":0.01,"dailyCapFraction":0.02,
   "minimumAdjustedEdgeFraction":0.02,"virtualInitialBankrollUsd":1000,
   "quoteAuthority":"SPORTSGAMEODDS_RESEARCH_QUOTES_ONLY_UNDER_SEPARATE_CONSUMER_POLICY",
   "realMoneyExecutionAllowed":false,"wageringOutsideAispAllowed":false,
   "experimentId":"AISP_CFB_PAPER_POLICY_V1:CFB:2026"},
 "scope":"Otherwise eligible immutable CFB_GEN1_RIDGE_A100_PAIRED_RESIDUAL_V1 forecasts from the single authorized recurring CFB owner; each supplement published before game T-30; current discovery lease, consumer quote policy, decision window and all existing caps remain required.",
 "validFromUtc":"2026-10-01T00:00:00Z","validUntilUtc":"2026-11-01T00:00:00Z"}
```

The question to answer: *"Do you authorize the unchanged virtual-only AISP_CFB_PAPER_POLICY_V1 terms for otherwise eligible forecasts from the authorized CFB owner from 2026-10-01T00:00Z to 2026-11-01T00:00Z?"*

### 6c. AISP CFB SGO lane (you approve)

- **Ceiling:** at most **300 objects** in billing month 2026-10, counting setup and purchases together (section 2), as a demand lane after MLB and NFL.
- **Timing:** buy window T−75..T−35.
- **Targeting:** by the rules in section 3.
- **Stop rule:** section 4.
- **Validity:** until 2026-11-01T00:00Z.
- **Order:** the listing decision rule in 3b runs after the 1-object validation.

## 7. Activation steps, in order

1. **By Tue 09-29:** you decide on 6a, 6b and 6c.
2. **Codex:** publishes the successor plan and grant, then a successor `CFB_RECURRING_ACTIVATION_HANDOFF_V1` binding both. It installs through its supported installer, `-Mode Plan`, then `Install`, then `Verify`.
3. **Claude, before Wed 09-30 (flagged off):**
   - add the allocator's CFB demand lane;
   - make the lane cap per billing month;
   - add one-team targeting (3c);
   - add the stop rule (4).
4. **Thu 10-01 morning (Claude):**
   - Pin `CFB_PRODUCTION_HANDOFF_PATH` / `_SHA256` to the successor.
   - Set the lane config.
   - Restart through ASP Backend 8010 inside the safe restart window in section 5.
   - Verify the new listener PID, that the grant reads as valid until 2026-11-01, and the October capacity.
5. **Thu 10-01 (before T−75 of WKU @ NMSU, i.e. by 6:45 PM ET):** the 1-object identity validation on that uniquely timed game. Then pin `CFB_SGO_LEAGUE_ID` and the first reviewed team entries, and record the CFB older-leg lag.
6. **Then:** the teams-listing probe and decision rule (3b). Normal checkpoint buying begins inside the 300 ceiling.

**If nothing is approved:** from Wed 09-30 8:00 PM ET, the CFB checkpoint records `POLICY_GRANT_EXPIRED` and no CFB object is bought. Import, display, finals and scoring continue for whatever the producer still publishes. The producer's plan expires too, so no new CFB forecasts arrive.
