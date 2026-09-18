# CFB October activation: allocation and renewal proposal

Prepared 2026-09-18. **Nothing here is approved or active.** Three authorizations expire at **2026-10-01T00:00:00Z**, which is Tue 09-30 at 8:00 PM ET:

- the producer's recurring CFBD plan `3fead29f…`;
- your virtual-policy grant `e533fb73…`;
- AISP's ten-object CFB SGO ceiling (`CFB_SGO_LANE_AUTHORIZED_UNTIL`).

After that moment AISP's checkpoint records `POLICY_GRANT_EXPIRED` for every CFB game until the grant is renewed. Nothing here assumes renewal.

## 1. SportsGameOdds in October (reset entitlement)

| Item | Objects | Basis |
|---|---|---|
| Entitlement | 2,500 | Resets 2026-10-01T00:00Z (UTC calendar month) |
| Reserve (both guards) | 400 | September's 300 schedule ends 09-30; the code restores 400 automatically |
| **Spendable** | **2,100** | Guard A = guard B in October |
| MLB postseason (upper bound) | ≤ 51 | ≤ 45 October games × 17/15. Wild Card game 3 on Oct 1 (4 games, if necessary), Division Series ≤ 20, LCS ≤ 14, World Series ≤ 7. Typical is about 41. |
| NFL | ≈ 133 | 60 October games, from the schedule in AISP: 60 checkpoints and about 73 Show Prep previews (the Oct 1 preview falls in September; the Nov 1 previews fall in October) |
| **CFB (proposed)** | **≤ 300** | 283 FBS-vs-FBS regular-season games in weeks 5–9 (CFBD schedule; 193 kickoff times still TBD). One checkpoint object per eligible game, plus a margin of up to 17 for identity validation and team-map bootstrap. |
| Remaining slack | ≈ 1,616 | Distributed by the existing allocator: NFL live and period shares, then MLB |

Everything fits with a large margin. There is no need to touch the reserve or buy an upgrade.

### Proposed CFB lane terms

- **Budget:** at most **300 objects** in October's billing month, funded as a **demand lane** after MLB and NFL projected need, never from their shares.
- **When it buys:** one object per game, inside T−75 to T−35. It buys only for games whose forecast is currently eligible and whose per-forecast eligibility record exists. No previews, and no refresh purchases.
- **Targeting:** one event per request, by reviewed SGO team id. Kickoffs are rarely unique: on Oct 10 only 2 of 12 timed FBS games have a unique kickoff across all divisions. That is why the reviewed team map is required, and why a game that can't be targeted is skipped, not bought and filtered afterwards.
- **Ledger key:** `sgo_cfb_events`. Both guards apply on every request.

### AISP code needed before Oct 1 (small; tests exist for the adapter)

1. `provider_allocation._day_need` / `_demand_aware_split`: add a CFB demand lane, so NFL live and period shares cannot starve it. Keep `LANES` share 0 for CFB.
2. `sgo_lane.capacity`: switch the cap from a lifetime cap to a per-billing-month cap, with a new authorization window.
3. Config (only once approved):
   - `CFB_SGO_LANE_ENABLED=true`
   - `CFB_SGO_LANE_OBJECT_CAP=300`
   - `CFB_SGO_LANE_AUTHORIZED_UNTIL=2026-11-01T00:00:00+00:00`
   - `CFB_SGO_LEAGUE_ID` and `CFB_SGO_TEAM_MAP_PATH`, set from live validation (below)

## 2. Live validation (the one observation the adapter still needs)

**First CFB object in October: 1 object.**
- **Game:** a CFB game whose kickoff no other CFBD game shares within five minutes. Thu 10-01 and Fri 10-02 have few games.
- **Request:** `limit=1`, `leagueID` set to the documented candidate `NCAAF`.

It must show:
1. the league id the provider actually returns;
2. both schools' `teamID` values;
3. `startsAt` within five minutes of the CFBD kickoff;
4. full-game moneyline `oddID`s, with each book's `lastUpdatedAt`;
5. exactly one object billed.

Only then are `CFB_SGO_LEAGUE_ID` and the first two team-map entries pinned.

**Team-map bootstrap (decision needed; cost unknown until measured):**
- **(a)** One provider teams listing for the league, if a free usage reading before and after shows it bills 0 or a known small number. Each entry is reviewed against CFBD school ids by name and conference, and marked `reviewed: true`.
- **(b)** Otherwise the map grows from each purchased event, mapped by exact kickoff slot plus reviewed names.
- **Cap:** no more than 150 objects outside ordinary checkpoint buys.

## 3. Renewals to approve (exact terms)

### 3a. Producer recurring plan (Codex publishes; you approve the exact SHA)

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

### 3b. Virtual-policy grant (Codex retains it; you reply explicitly)

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

### 3c. AISP CFB SGO lane (you approve)

Replace the September ten-object ceiling with the October terms in section 1: at most 300 objects per billing month, as a demand lane, until 2026-11-01T00:00Z, starting with the 1-object identity validation.

## 4. Activation steps, in order

1. **By Tue 09-29:** you decide on 3a, 3b and 3c, and on the team-map bootstrap choice (2a or 2b).
2. **Codex:** publishes the successor plan and grant, then a successor `CFB_RECURRING_ACTIVATION_HANDOFF_V1` binding both. It installs through its supported installer, `-Mode Plan`, then `Install`, then `Verify`.
3. **Claude, before 10-01:** implements and deploys the allocator CFB lane and the per-month lane cap, both flagged off.
4. **Claude, after Codex's handoff:**
   - Pin `CFB_PRODUCTION_HANDOFF_PATH` / `_SHA256` to the successor.
   - Set the lane config.
   - Restart through ASP Backend 8010, outside MLB Wild Card and NFL windows (for example Wed 10-01 at 10:00 ET).
   - Verify the new listener PID and that the grant reads as valid until 2026-11-01.
5. **First eligible uniquely timed CFB game (Oct 1–2):** the 1-object identity validation, then pin `CFB_SGO_LEAGUE_ID` and the first reviewed team-map entries.
6. **Team-map bootstrap** as approved. Then normal checkpoint buying begins.

**If nothing is approved:** from 2026-10-01T00:00Z the CFB checkpoint keeps recording `POLICY_GRANT_EXPIRED` and no CFB object is bought. Forecast import, display, finals and scoring continue for whatever the producer still publishes. The producer's plan expires too, so no new CFB forecasts arrive.
