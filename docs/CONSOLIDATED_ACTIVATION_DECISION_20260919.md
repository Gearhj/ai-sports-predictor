# One consolidated activation decision: CFB sportsbook coverage and NBA launch

> **SUPERSEDED 2026-09-19 18:30 ET.** The approved September CFB scope is **two** objects (one validation, one named target), not four. Only the delta is now requested, in `ADDITIONAL_PERMISSIONS_20260919.md`. The budget and cost figures below still hold.

2026-09-19. Everything below is **implemented, reversible and shipped OFF**. Nothing spends, and no behaviour changes, until the settings in this document are set. Details: `CFB_SPORTSBOOK_COVERAGE_20260919.md`, `NBA_LAUNCH_PLAN_20260919.md`, `NBA_INTEGRATION_EVIDENCE_20260919.md`.

## The budget these numbers come from

| | Objects |
|---|---|
| Provider entitlement | 2,500 per billing month, free **Amateur** plan, CFB and NBA included |
| September: used / spendable above the 300 reserve (both guards, 15:55 ET) | 2,014 / **186** |
| September: MLB and NFL committed need (today's remainder plus 09-20..30) | 182 |
| September: margin against projection error (`CFB_SGO_PROTECTED_HEADROOM`) | 10 |
| October: spendable above the 400 reserve | **2,100** |
| October: estimated MLB and NFL need | about 165 |

- **Monetary cost of everything below: $0.** It fits inside the free plan.
- There is no overage; exceeding the allowance returns HTTP 429.
- The next plan up (Rookie, 100,000 objects) is $149 monthly, or $99 a month billed annually. It is not needed here.
- Current acquisition authority expires **2026-10-01T00:00Z**, so every October line needs your new authority.
- **One thing to verify, free:** the provider's pricing page says the monthly limit resets 30 days after first use, while AISP assumes the 1st. October's first usage reading settles it, and no object is spent to find out.

## The decision

> ### A. CFB — one validation object, then bounded coverage
>
> 1. **September (existing authority): 4 objects.**
>    - 1 live validation object to pin the SGO college-football league id and the first team-map entries, plus up to 3 priced games.
>    - **Source:** `CFB_SGO_PROTECTED_HEADROOM` 10 → 0, which releases exactly the 4 objects September has above committed MLB/NFL need (186 − 182). The 300 reserve does not change, and MLB/NFL keep their full projected need but lose their error margin.
>    - **Settings:** `CFB_SGO_LANE_ENABLED=true`, `CFB_SGO_VALIDATION_ENABLED=true`, `CFB_SGO_LANE_OBJECT_CAP=4`, `CFB_SGO_LANE_DAILY_OBJECT_CAP=2`, `CFB_SGO_LANE_AUTHORIZED_UNTIL=2026-10-01T00:00:00Z`.
>    - **Dates:** 2026-09-24 → 2026-10-01T00:00Z.
>    - **Games:** validation on CFBD 401869941 (09-24 19:30 ET) if the producer re-lists it, else 401862779. Then up to 3 of 401858461, 401858234 and 401864576, one object each at about T−60.
>    - **Also name the reviewer** who approves the team-map proposals before pinning.
> 2. **October (new authority): a 300-object CFB lane.**
>    - `CFB_SGO_LANE_OBJECT_CAP=300`, `CFB_SGO_LANE_DAILY_OBJECT_CAP=60`, `CFB_SGO_LANE_AUTHORIZED_UNTIL=2026-11-01T00:00:00Z`.
>    - 1 object per admitted FBS–FBS game at about T−60, with no refreshes, covering up to 285 games as the reviewed map grows.
>
> ### B. NBA — consumer on now, prices and positions later
>
> 3. **Activate the consumer (no spending).** Deploy this branch, then set `NBA_PRODUCTION_HANDOFF_PATH`/`_SHA256` to Codex's selected handoff `7113b71f…`, plus `NBA_PRODUCTION_CONSUMER_ENABLED=true` and `NBA_FINAL_SOURCE_OBJECTS`. **(NBA plan O2.)**
> 4. **Import the 2025-26 history once,** for model performance: pin the two assessment receipts and run the import job once. It admits 1,225 FINAL games as RESEARCH_SHADOW. **(O3.)**
> 5. **Ask Codex to install its NBA publisher and approve its source refresh,** so real 2026-27 packages and eligibility leases arrive from 2026-10-13. It costs $0, as it uses a public CC BY 4.0 schedule asset. **(O1.)**
> 6. **October SGO for NBA: 100 objects** (`NBA_SGO_LANE_ENABLED=true`, `NBA_SGO_LANE_OBJECT_CAP=100`, `NBA_SGO_LANE_AUTHORIZED_UNTIL=2026-11-01T00:00:00Z`), covering 84 games plus at most 16 for setup, with 1 validation object on the first game (2026-10-20). **(O6.)**
> 7. **Paper positions:** only a release-bound `NBA_RECURRING_PAPER_AUTHORIZATION_V1` can carry paper authority, and today's release carries none (`paperEligibilityAuthorization: null`), so the checkpoint reports `NO_PAPER_AUTHORIZATION` and evidence imports as usual. If Codex issues one, pin it and set `NBA_PAPER_DECISIONS_ENABLED=true`. Its season-wide scope is wider than the 2026-09-15 single-game approval and is a separate decision. **(O4, O4b.)**
> 8. **Which channel decides an NBA moneyline: settled, not a choice.** The approved policy names the joint 10,000-draw probability, and Codex's completed contract says the paper lane uses that exact joint baseline, never Elo. The checkpoint does exactly that and records Elo beside it.
> 9. **2026-27 finals: settled.** Codex's source-confirmed finals lane (`finals-discovery/current.json`) is the production final path and AISP reads it; retained season exports stay a separate historical lane. AISP requests nothing either way. **(O7.)**
> 10. **The Odds API for NBA: recommended decline.** No NBA sport key exists in retained evidence, and its NBA budget lane is 0. **(O8.)**
>
> ### October totals, if A2, B6 and the MLB/NFL estimate all hold
>
> | Lane | Objects |
> |---|---|
> | MLB and NFL | about 165 |
> | CFB | 300 |
> | NBA | 100 |
> | **Total** | **about 565 of 2,100 spendable** |

## What happens if you decide nothing

- CFB keeps showing "no sportsbook coverage: collection is not active (no allocation)", and its checkpoints keep recording DECLINED `NO_TWO_SIDED_QUOTE` while the producer's lease is current.
- NBA keeps showing Codex's held package and any imported history, with every surface stating why it is idle.
- Nothing is bought, and no position is taken.
