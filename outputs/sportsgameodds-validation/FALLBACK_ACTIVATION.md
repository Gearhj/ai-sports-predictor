# SportsGameOdds temporary governed MLB fallback — validated and activated

2026-09-09. Branch `claude/aisp-nfl-wow-and-odds-recovery-v1`, commit `7ecdcb1`.

---

## 1. Requests and object usage

| request | filters | objects |
|---|---|---|
| earlier session, live events | `leagueID=MLB&limit=2&oddsAvailable=true` | 2 |
| **pregame validation** | `leagueID=MLB&limit=2&startsAfter=…&finalized=false&oddsPresent=true` | **2** |
| bounded slate fetch (ingest failed on a wrong class name) | `limit=10&startsAfter=…&startsBefore=+12h` | 10 |
| **bounded current-slate collection** | same | **10** |

**24 objects total** against the 2,500/month Amateur allowance — under 1%. An
object is one event, per the provider's published definition. The provider
returns **no usage headers at all**, so consumption is counted locally. The
first request used `oddsAvailable`, which is not a documented parameter name and
was silently ignored; the harness now passes only documented filters.

The credential was never printed, logged or written. It is sent in an
`X-Api-Key` header, and anything key/auth/token-shaped is redacted before any
output. Confirmed loaded only as a boolean and a length.

## 2. Pregame validation — PASSED

`startsAfter` proves pregame filtering. Both returned events were explicitly
unstarted: `started=false, live=false, completed=false, ended=false,
cancelled=false`, kickoffs 22:35Z and 22:40Z against a 21:47Z fetch.

**18 pairs admitted from 2 events. Zero rejections.** Nine bookmakers on both,
both sides, all available. Realistic pregame prices — CLE @ BAL at −102/−118,
−110/−110, +100/−120; HOU @ PHI at −154/+130.

## 3. Games and bookmakers covered

The bounded collection returned **10 objects → 10 pregame events → 90 pairs,
zero rejections**, and those 10 events were exactly the 10 MLB games still
before their decision cutoff.

Bookmakers, all nine on every event: betmgm, bovada, caesars, **draftkings**,
espnbet, **fanduel**, pointsbet, unibet, williamhill. 180 snapshot rows written
(10 × 9 × 2).

## 4. Coverage

| | before | after |
|---|---|---|
| covered | **0 / 15** | **10 / 15** |
| at risk | 10 | **0** |
| cutoff missed | 5 | 5 |

Every game still before its cutoff is now covered. The 5 that remain uncovered
had already passed their cutoff when the fallback ran, and nothing can fix that
after the fact — they keep their exact cause, `QUOTA_GUARD_BLOCKED`.

## 5. Mapping and timestamp results

A provider-specific parser reads the documented dictionary keys rather than
forcing the payload through the generic dotted-path model, which cannot express
identity held in keys. A pair is admitted only when both sides appear in the
same response, `oddID`/`opposingOddID` reconcile **both ways**, event, bookmaker,
market and period identity agree, the event is pregame by the provider's flags
**and** the clock, both prices are valid American odds, both timestamps exist and
both sides are marked available. Nine named refusal reasons; never a bare
"unavailable".

**Skew: 0.0 seconds on all 90 pairs** in this collection. The first validation
had found up to 208s (draftkings 63.8s, espnbet 133.8s, bovada 207.9s), so the
handling is real rather than theoretical: both stamps are kept, the skew is
recorded, `simultaneity_claimed` is `false`, and the pair is dated by the
**older** stamp — a pair is only as fresh as its stalest half.

As stored: `observed_at` 17:57:38 → 17:58:10 EDT (the provider pair times),
`available_as_of` 18:01:18 EDT (one receipt time).

> **One deliberate, stated deviation.** The instruction put receipt time in
> `observed_at`. `observed_at` is the field the 240-minute staleness gate reads,
> so receipt time there would have made every fallback quote permanently fresh
> at ingestion and silently disabled the gate the same instruction requires
> retaining. Receipt time is therefore in `available_as_of`, which is AISP's
> existing field for exactly that, and the pair-freshness timestamp is in
> `observed_at`. Both facts are preserved; only the column assignment differs.

American conversion is the specified formula, tested at +100, +145, +2500,
+9000, −110, −150, −10000, −100000. The original American price is preserved
alongside the derived decimal. Anything between −100 and +100, or unparseable,
is refused rather than coerced.

## 6. Fallback state and expiry

```
SPORTSGAMEODDS_ENABLED=true
SPORTSGAMEODDS_FALLBACK_UNTIL=2026-09-30
```

Governance is dated **in code**, not configuration: `GOVERNED_SOURCES` remains
`{THE_ODDS_API}` permanently, and the fallback is a separate exception requiring
**both** the flag and a window ending 2026-09-30. Verified: included on Sep 15
and Sep 30 23:59, excluded on Oct 1 00:01, and excluded at any date with the
flag off. The Odds API returns to sole primary status with nobody remembering.

Scope is MLB moneyline only, and it is **never preferred**: `prefer_primary`
drops fallback rows for any market the primary already priced, applied in
position selection and per market in the read path — which is where a newer
fallback row would otherwise win the latest-per-book reduction. The pre-existing
guard test that a research row never displaces a governed one passes unchanged.

## 7. Provenance is visible

Bookmakers are stored namespaced and labelled: `sgo:draftkings` →
"Draftkings (SportsGameOdds, research)". Nothing is relabelled as
`THE_ODDS_API`, and because the two providers never share a `Sportsbook` row, a
two-sided pair can never mix providers.

The `/today` decision payload carries **20 attributions**, and the UI renders
them: *Book — Bovada (SportsGameOdds, research)*, alongside *Selection BAL ML*,
*+102*, *Policy-adjusted edge +1.9%*, *Final decision cutoff Sep 9, 6:20 PM EDT*,
*Virtual stake — No position*. Screenshot:
`outputs/sportsgameodds-validation/fallback-attribution.png`.

The MLB board moved from 1 game / 1 governed decision to **15 games / 20
governed decisions, 10 watching**.

Note: the per-game analysis page still shows no book rows, correctly — it is a
point-in-time view as of the 3:34 PM forecast cutoff, and these quotes arrived
at 6:01 PM. That is the point-in-time discipline working, not a gap.

## 8. Nothing else changed

| | |
|---|---|
| Positions | **11**, unchanged; 0 created |
| Experiment bankrolls | 1000.00, 1000.00, 1000.00 — unchanged |
| Settlements | **0**, unchanged |
| Decisions referencing an SGO book | **0** |
| Decisions written in the window | 84, all `NO_BET` with no sportsbook, from the supervised scheduler's own MORNING/LINEUP_CONFIRMED lifecycle |
| `MIN_ADJUSTED_EDGE` | 0.02 — unchanged |
| Cutoff / staleness / freshness gates | unchanged |
| THE_ODDS_API ledger | **654 rows**, unchanged — 0 credits spent |
| THE_ODDS_API snapshots | 222,318, untouched |

Decision states on the 10 covered games: **3 PROVISIONAL_EDGE, 4 WATCH, 3
NO_BET — no QUALIFIED_BET.** The best observed policy-adjusted edge was +1.9%
against a 2.0% minimum. The thresholds did their job untouched; no position was
created for testing, and none was backfilled.

## 9. Licensing

Redistribution and standalone public display remain prohibited by their terms,
so this stays a private research/development source and internally derived
decision output. The captured payloads are git-ignored; only derived records are
committed. Commercial/public redistribution remains disabled pending a separate
terms review.

## 10. Remaining blocker

**One, and it is operational rather than technical: the supervised process is
not yet running this code.** `ASP Backend 8010` restarted at 16:11 onto the
commit *before* the fallback existed, so its scheduler still sees primary-only
governance and will not collect from the fallback on its own. Everything above
was proven in-process against the live database.

To close it:

```bash
Stop-ScheduledTask -TaskName 'ASP Backend 8010'; Start-ScheduledTask -TaskName 'ASP Backend 8010'
```

The supervisor now reclaims its own wedged port, so this restarts cleanly.

Two smaller follow-ups, neither blocking: the fallback fetch is not yet wired
into the event-aware checkpoint scheduler (it was run directly here), and the
provider ledger does not yet record SGO requests, so object accounting is
currently counted from the payload rather than from the ledger.
