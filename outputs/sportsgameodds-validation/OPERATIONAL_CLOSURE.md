# SportsGameOdds — operational closure

2026-09-09. Everything below is read from the running system and the live
database.

---

## 1. Commit discrepancy — there was none

`7ecdcb1` is the fallback code; `8b92788` is its immediate documentation child.
`git merge-base --is-ancestor 7ecdcb1 8b92788` confirms containment, so the two
figures were a parent and its child, not a fork.

That was the state before this task. Three commits were then required to make
closure complete, so **neither of those was the right restart target**:

| commit | what it added |
|---|---|
| `6e862c0` | scheduler wiring, provider ledger accounting, object budget guard |
| `44cb081` | supervisor reclaims a stale foreign owner of its own port |

**Final branch and commit: `claude/aisp-nfl-wow-and-odds-recovery-v1` @
`44cb081315e28a8786dce73099fb68444a8d1499`.** Working tree clean.

## 2. Restart — done, and it needed a fix first

The first restart attempt failed silently in the way that has bitten this
machine repeatedly: `Stop-ScheduledTask` killed the powershell and cmd wrappers
but left the uvicorn grandchild (pid 62204) running and answering `/readyz`, so
the supervisor found a **healthy** holder, refused to start a duplicate, exited,
and the task reported success while superseded code kept serving.

The previous fix only reclaimed an *unhealthy* holder. The port belongs to this
task, so the question is not whether the holder is alive but whether it is
*ours*: `Test-OwnDescendant` walks the holder's ancestry for the supervisor's
own PID — a walk, not a single comparison, because the child is a grandchild via
cmd.exe and the venv launcher. Its own child is left alone; anything else is a
stale owner and is reclaimed.

**Result: pid 62204 gone, pid 64752 serving since 18:57:17 EDT, task `Running`,
`/readyz` 200.** Its Python code is HEAD's Python code — `44cb081` touched only
the `.ps1`.

Confirmed on the running process: `fallback_provider: SPORTSGAMEODDS, enabled
True`, coverage 10/15, `collection_mode: PROVIDER_FLOOR_LATCHED`.

## 3. Scheduler wiring

The fallback lives inside the existing `collect-odds-checkpoint` job — same
five-minute tick, same single owner process, same configured checkpoint
schedule. It is reached only at the branch where the primary decision already
says it cannot request, so it can never pre-empt a checkpoint The Odds API can
serve.

- **Bounded to games that can still be helped**: uncovered *and* before their
  decision cutoff, with `startsAfter` pinned to now and `startsBefore` to the
  last open cutoff. Past-cutoff games are never requested; nothing is backfilled.
- **No retry storms**: a 600-second cooldown counted from the last *attempt*,
  not the last success, so a failing provider is not hammered by a tick that
  fires twice as often. Ten minutes is also the free tier's own refresh
  interval, so a tighter cadence could only re-request unchanged prices.
- **September 30 expiry** unchanged, enforced in `provider_registry`, not
  configuration.

## 4. Provider ledger accounting

Implemented and unit-tested against real `ProviderRequestLedger` rows. Each
fetch writes a reservation before the request and a terminal row after it:

| field | carries |
|---|---|
| one row per request | the request count |
| `records_received` | the **billable event objects** returned |
| `quota_remaining` | locally computed remaining allowance |
| `quota_cost` | `NULL` — this provider bills objects, not credits |
| `response_metadata.request_shape` | filters and the bounded window |
| credential | **nowhere** — the key travels in a header and is not in the recorded filters |

Requests and objects are therefore countable separately: rows versus the sum of
`records_received`.

**Live evidence: zero SportsGameOdds ledger rows.** That is honest rather than
missing — the accounting was wired *after* the only fetch, and no fetch has been
warranted since, because every pre-cutoff game is already covered. The budget
reads `requests_in_window 0, objects_used_in_window 0, spendable 2100`, computed
from the live ledger.

## 5. Object budget guard

400 of the 2,500 objects are held back and never spendable, counted over a
rolling 30 days rather than a calendar month, because the local count is the
only count there is — the provider returns no usage headers — and a rolling
window cannot under-count by sitting just after a reset. The guard runs **before**
the request.

A transport failure now records a known zero rather than `NULL`. `NULL` is
charged the worst case, and charging an outage twenty objects would let a bad
afternoon eat the allowance.

## 6. One real scheduled checkpoint, observed end to end

`collect-odds-checkpoint` at **19:02:20 EDT**, `SUCCEEDED`, on the supervised
process:

```
primary : PROVIDER_FLOOR_LATCHED   should_request=false  provider_called=false
fallback: attempted=false  reason=NO_UNCOVERED_GAME_BEFORE_CUTOFF  objects=0
```

The full chain is proven: **scheduler → fallback evaluated and refused by name →
normalized snapshots (180 rows) → coverage monitor (10/15) → governed decision
evaluation.** Seconds later, at 19:02:21–19:02:23, the scheduled decision
lifecycle wrote **10 decisions priced from fallback quotes**.

The fallback declined rather than fetched because there was nothing left to
help — which is the correct outcome, not a gap in the demonstration. The
positive fetch path was exercised against this same database an hour earlier
(10 objects → 90 pairs → 180 rows → coverage 0/15 → 10/15). I did not
manufacture an uncovered game to force a fetch.

## 7. No duplication, no past-cutoff requests

Since the restart: **SGO snapshots still 180, zero added**; 9 distinct
`observed_at` values, i.e. one batch per bookmaker, not repeated writes. One
checkpoint ran and requested nothing. The five past-cutoff games were never
included in any request.

## 8. Report

**Coverage, now**

| | |
|---|---|
| covered | **10 / 15** |
| at risk | **0** |
| inside the 60-minute target | 5 |
| cutoff missed | 5 |

Quote age at cutoff: median **61.8 min**, min 21.8, max 236.8 — all inside the
240-minute window, unchanged.

**Every uncovered game, with its exact reason** — all five passed their cutoff
before any fallback existed, which nothing can repair after the fact:

| game | cutoff | state | cause |
|---|---|---|---|
| MIN @ DET | 12:55 EDT | CUTOFF_MISSED | QUOTA_GUARD_BLOCKED |
| TOR @ ATH | 14:50 EDT | CUTOFF_MISSED | QUOTA_GUARD_BLOCKED |
| STL @ SF | 15:30 EDT | CUTOFF_MISSED | QUOTA_GUARD_BLOCKED |
| WSH @ SD | 15:55 EDT | CUTOFF_MISSED | QUOTA_GUARD_BLOCKED |
| TEX @ SEA | 15:55 EDT | CUTOFF_MISSED | QUOTA_GUARD_BLOCKED |

**Decisions evaluated with fallback prices: 10**, all carrying full provenance
(`Bovada / Fanduel / Pointsbet (SportsGameOdds, research)`), each bound to its
provider quote timestamp. Recommendations: 5 NO_BET, 3 PROVISIONAL_EDGE, 1
WATCH, **1 QUALIFIED_BET** — CIN ML at +240 from Pointsbet, edge 0.0779, MORNING
phase. Read-model states on the ten covered games: 3 PROVISIONAL_EDGE, 4 WATCH,
3 NO_BET.

**Scheduled job identity and next checkpoint.** `asp-odds-checkpoint`, five-minute
interval, owner `ASP Backend 8010` → `asp-backend-supervisor.ps1` → pid 64752.
MLB has **no outstanding checkpoint** for the rest of today (6 open games, all
covered); NFL's next is 19:30 EDT at 3 credits, which the floor latch will
refuse.

**Zero Odds API credits.** Ledger 654 rows, unchanged since 2026-09-08 20:48.
Credits spent since 15:00 today: **0**. Lifetime 957, unchanged.

**Nothing else changed**

| | |
|---|---|
| `MIN_ADJUSTED_EDGE` | 0.02 |
| `FINAL_PREGAME_CUTOFF_MINUTES` | 15 |
| `MAX_QUOTE_STALENESS_MINUTES` | 240 |
| `FRESH_TARGET_MINUTES` | 60 |
| positions | **11**, none created today |
| positions from a fallback-priced decision | **0** |
| experiment bankrolls | 1000.00, 1000.00, 1000.00 |

**Settlements, stated separately as asked**

- Total historical settlements in `shadow_decision_settlements`: **0**
- Settlements created by this task: **0**

The historical total is zero because no position has ever been settled — the
eleven existing positions predate any settlement run.

---

## The one thing to know before tonight

A **QUALIFIED_BET now exists priced from a SportsGameOdds quote** (CIN ML +240,
Pointsbet). No position was created for it, and I created none for testing.

But positions arise from the canonical FINAL_PREGAME decision, and **CIN @ LAD's
cutoff is 21:55 EDT tonight** — roughly three hours from this writing. If that
final evaluation still qualifies, the normal scheduled lifecycle will create a
real virtual position priced from a fallback quote, with no further action from
anyone. That is exactly the prospective behaviour you authorized, and it is now
armed rather than hypothetical.

To disarm it without touching anything else, the kill switch is one setting:

```
SPORTSGAMEODDS_ENABLED=false
```

then restart the task. The fallback also expires on its own after 2026-09-30.

## Follow-ups, none blocking

1. The operator view reports `governed_sources` as the permanent set
   (`['THE_ODDS_API']`). While the fallback window is open the *effective* set
   also contains `SPORTSGAMEODDS`, and an operator cannot see that from this
   field alone — the fallback's `enabled: true` is shown separately. Worth
   surfacing the effective set; deliberately not changed here so the running
   process matches the reported commit exactly.
2. The first live SGO ledger row will not exist until the next warranted fetch.
