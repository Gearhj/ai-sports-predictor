# Additional permissions requested — only the delta

2026-09-19, 18:30 ET. This replaces the broader draft in `CONSOLIDATED_ACTIVATION_DECISION_20260919.md`, which proposed four September CFB objects. **The approved September CFB scope is two objects, and that is respected.** Everything already authorized continues without further review.

## Already authorized, no new permission needed

| Item | Status |
|---|---|
| Today's 2 CFB objects (1 identity validation + 1 Kennesaw State–Tennessee, CFBD 401856692), window 22:30–23:10Z, lane end 23:45Z | **Expired unused. 0 objects spent.** The lane and the binding can only be switched on with a backend restart, and MLB and CFB windows run continuously across that window. No substitute game was bought, and no restart was rushed. |
| Software implementation and deployment (CFB path, NBA consumer, pilot exporter, V2 outcome export) | Authorized. Tonight's deploy is at 22:50 ET, outside all windows. |
| Outcome amendment `7508ae72…` and the V2 exporter | Accepted by James; no repeat approval. It is in tonight's release. |
| NBA consumer activation, history import, joint controlling channel | Authorized; the joint channel is the existing approved term, so there is no channel choice to make. |

## Budget position now (18:25 ET, both guards agree)

| | Objects |
|---|---|
| September used / allowance | 2,015 / 2,500 |
| Reserve, never spent | 300 |
| **Spendable above reserve** | **185** |
| MLB and NFL still to buy in September (10 games left today, 114 MLB and 31 NFL on 09-20..30) | about 182 |
| **Left after MLB/NFL commitments** | **about 3**, plus whatever the 2 protected-headroom objects hold back |

October: 2,500 less the 400 reserve leaves **2,100 spendable**; estimated MLB and NFL need is about 165.

## The delta: what needs your approval

> ### 1. CFB — repeat today's expired scope on a later date (2 objects)
>
> - **Objects:** 2, unchanged in shape: 1 identity validation + 1 named target.
> - **Cost:** $0, inside the free Amateur plan.
> - **Target and date:** CFBD **401869941, 2026-09-24 19:30 ET**, window T−75..T−35 (18:15–18:55 ET), if the producer lists it with a current lease. If it is not listed, the fallback target is **401862779, 2026-09-25 16:00 ET**. One target only; no substitution beyond that named fallback.
> - **Source:** the 2 protected-headroom objects, so **MLB and NFL keep their full committed 182**.
> - **Why a later date works:** tonight's deploy carries the CFB lane and the reviewed binder, so the settings can be in place before the window opens, with no restart inside it.
> - **Also needed:** the name of the reviewer who approves the team-map proposal before any id is pinned.
>
> ### 2. October monthly scope (new authority; current authority ends 2026-10-01T00:00Z)
>
> | Lane | Objects (month) | Basis |
> |---|---|---|
> | CFB | **up to 300** | 1 per admitted FBS–FBS game at about T−60, no refreshes |
> | NBA | **up to 100** | 84 games plus at most 16 for setup, from 2026-10-20 |
> | MLB and NFL | about 165 | existing pattern |
> | **Total** | **about 565 of 2,100** | leaves about 1,535 unused |
>
> - **Cost: $0.**
> - Approving a smaller CFB number is fine; the lane caps at whatever you set.
>
> ### 3. NBA paper positions beyond the original single-game authority (separate)
>
> - The existing grant covers one game. Season-wide or recurring paper positions need a **new** grant; AISP will not widen the original implicitly.
> - Until you grant it, NBA imports, displays and scores evidence with no paper authority, and takes no positions.
> - The controlling channel stays **joint**, 10,000 draws, with Elo labelled separately.

## What proceeds with no decision from you

Tonight's deploy, the NBA consumer and history import, the pilot exporter's readable-originals repair, the V2 outcome export, and all existing MLB and NFL buying under its current commitments.
