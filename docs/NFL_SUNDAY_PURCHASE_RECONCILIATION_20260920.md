# NFL Sunday purchase reconciliation — 2026-09-20

Reconciles the claim *"15 of 15 priced with 15 objects, while tied kickoffs are
deferred"* against the actual Sunday inventory.

**Method.** The live NFL schedule and the live moneyline history were read from
Postgres inside `SET TRANSACTION READ ONLY`, rolled back, and mirrored into an
in-memory SQLite with the same game ids and kickoffs. The **real**
`collect_nfl_pregame` was then driven tick by tick against that mirror with the
provider call stubbed. No provider request was made, nothing was written to
Postgres, no switch was moved, and the primary checkout was not touched.

Branch `claude/nfl-lane-protections` (`742a73f`), base `d8ce357`.

---

## 0. The short answer

| question | answer |
|---|---|
| Sunday coverage | **15 of 15** (14 Sunday decision quotes + 1 Monday preview quote) |
| Objects spent | **15 of 15** allowed, none left, no game bought twice |
| Does it depend on an exact-ID request? | **No.** Not one request carries `eventIDs`; the mode is never read |
| Is the owner's one discriminating test spent? | **No.** Still unspent, still MLB-only |
| Is "deferral" consistent with "full coverage"? | **Yes** — and the reason is specific and fragile enough to state plainly (§2) |

The 15-of-15 is **proven** under the projected allowance of 15. It is **not**
unconditional: it holds because the grant at the first tick covers the whole
eight-game tied cluster. §3 shows what happens when it does not, and the answer
there is bad — not "fewer objects", but *the entire cluster goes unpriced*.

---

## 1. The live inventory

15 NFL games, read read-only. All 14 Sunday games already carry a moneyline
from Saturday's Show Prep preview pass at **15:02 ET on 2026-09-19** — which is
the previous service day, so it consumes none of Sunday's allowance.

| game | kickoff (ET) | matchup | newest moneyline before Sunday |
|---|---|---|---|
| `f8c8e9d0` | Sun 13:00 | NO @ BAL | Sat 15:02 |
| `29f66878` | Sun 13:00 | GB @ NYJ | Sat 15:02 |
| `81005264` | Sun 13:00 | PHI @ TEN | Sat 15:02 |
| `8ba3e58b` | Sun 13:00 | CAR @ ATL | Sat 15:02 |
| `8e3b33b1` | Sun 13:00 | PIT @ NE | Sat 15:02 |
| `9d26fc86` | Sun 13:00 | MIN @ CHI | Sat 15:02 |
| `c6a8dbad` | Sun 13:00 | CIN @ HOU | Sat 15:02 |
| `e6704e33` | Sun 13:00 | CLE @ TB | Sat 15:02 |
| `c8b96fca` | Sun 16:05 | JAX @ DEN | Sat 15:02 |
| `b019eb12` | Sun 16:05 | LV @ LAC | Sat 15:02 |
| `1193932a` | Sun 16:25 | MIA @ SF | Sat 15:02 |
| `26177ebd` | Sun 16:25 | WAS @ DAL | Sat 15:02 |
| `ab584fa0` | Sun 16:25 | SEA @ ARI | Sat 15:02 |
| `18562ce4` | Sun 20:20 | IND @ KC | Sat 15:02 |
| `313d2a19` | **Mon** 20:15 | NYG @ LAR | Sat 22:08 |

**The tied cluster is the eight 13:00 ET kickoffs.** Its decision window is
11:45–12:45 ET, and the scheduler ticks every 15 minutes at `:02/:17/:32/:47`,
so four ticks fall inside it.

### Sunday's allowance, from the app's own arithmetic

Read read-only via `_provider_balance`, `_shaped_daily_total` and
`_demand_aware_split`:

```
monthly cap      2500      used 2015      reserve 300      spendable 185
days remaining   11        replenishes 2026-10-01
Sunday need      mlb 17 + nfl 15 = 32
month projected  181  (fits inside 185)
basis            NEED_PLUS_EVEN_SHARE_OF_SLACK
daily_total      32
lane split       sgo_mlb_events 17 | sgo_nfl_events 15 | nfl_live 0 | nfl_periods 0
```

**`sgo_nfl_events` = 15 on Sunday.** The NFL need of 15 is 14 decision-window
games plus one preview slot: NYG @ LAR's Show Prep slot (15:00–16:30 ET Sunday)
falls on the Sunday service day even though the game is Monday.

---

## 2. Scenario A — the projected allowance (15). The per-tick table.

Ticks with nothing due are omitted — they return
`NO_NFL_GAME_IN_THE_COLLECTION_WINDOW` and spend nothing — **except** the three
inside the tied cluster's window, which are shown because they are the exact
ticks that used to re-buy.

| tick (ET) | due (phase @ kickoff) | grant / wanted | from | prefix keeps | prefix defers | `startsAfter` → `startsBefore`, limit | obj | allowance |
|---|---|---|---|---|---|---|---|---|
| Sun 11:47 | **8 DECISION @ 13:00** | **8 / 8** | 15 | **all 8** | — | `09-20 12:59:59` → `09-20 14:47:00`, limit 8 | 8 | 15 → 7 |
| Sun 12:02 | — (all 8 priced since 11:45) | — | 7 | — | — | no request | 0 | 7 |
| Sun 12:17 | — | — | 7 | — | — | no request | 0 | 7 |
| Sun 12:32 | — | — | 7 | — | — | no request | 0 | 7 |
| Sun 15:02 | 2 DECISION @ 16:05; 1 PREVIEW @ Mon 20:15 | 3 / 3 | 7 | 2 (`c8b96fca`, `b019eb12`) | `313d2a19` — `DEFERRED_AMBIGUOUS_UNSELECTED_EARLIER_GAME_IN_WINDOW` | `09-20 16:04:59` → `09-21 22:02:00`, limit 2 | 2 | 7 → 5 |
| Sun 15:17 | 3 DECISION @ 16:25; 1 PREVIEW @ Mon 20:15 | 4 / 4 | 5 | 3 (`1193932a`, `26177ebd`, `ab584fa0`) | `313d2a19` — same reason | `09-20 16:24:59` → `09-21 21:17:00`, limit 3 | 3 | 5 → 2 |
| Sun 15:32 | 1 PREVIEW @ Mon 20:15 | 1 / 1 | 2 | 1 (`313d2a19`) | — | `09-21 20:14:59` → `09-21 21:32:00`, limit 1 | 1 | 2 → 1 |
| Sun 19:17 | 1 DECISION @ 20:20 | 1 / 1 | 1 | 1 (`18562ce4`) | — | `09-20 20:19:59` → `09-20 22:17:00`, limit 1 | 1 | 1 → 0 |

**Totals: 15 objects, 15 games, allowance 0 left, nothing bought twice.**

Per-game cumulative count: every one of the 15 games ends on **1**.

### Why deferral and full coverage are not contradictory

They are answers to different questions.

* `313d2a19` **is** deferred, twice — at 15:02 and 15:17. Each time, its
  request window would have contained an unselected earlier cluster (16:25 at
  15:02, 20:20 at 15:17) that the provider could have returned instead. So it is
  not requested.
* It is **not** dropped. Its Show Prep slot runs 15:00–16:30 ET, which is six
  ticks wide. By 15:32 the earlier clusters have been priced and are no longer
  due, its request window contains only itself, and it is bought.

Deferral costs a *tick*, not a *game*, whenever the deferred game's window is
wider than the obstruction. That is the case for all three deferrals on Sunday.

### The tied cluster: exactly one intended purchase each — and what it depends on

**Stated plainly: yes, this depends on the whole eight-game cluster being
selected in a single request.** `unambiguous_prefix` keeps a start-time group
only when the group is *wholly* selected — a strict subset of a tied group is
never sent. The arithmetic that makes it true on Sunday:

```
tick 11:47 ET
  due                = 8   (the cluster; nothing else is in a window yet)
  lane spent so far  = 0   (Sunday's preview buys happened SATURDAY)
  room               = 15 - 0 - 0 = 15
  wanted             = 8
  granted            = min(8, 15) = 8        <- covers the whole cluster
  selection          = all 8 -> not a strict subset -> prefix keeps all 8
  request            = limit 8, startsAfter 12:59:59, startsBefore 14:47:00
  window contains    = exactly those 8 games -> no substitution is possible
```

The three later ticks in the window then find `due = 0`, because all eight were
observed at 11:47 which is after their window opened at 11:45. One purchase
each, four ticks, eight objects.

---

## 3. Scenario B — grant smaller than the cluster (7 of 8). The honest answer.

Same driver, `sgo_nfl_events` allowance set to 7.

| tick (ET) | due | grant / wanted | prefix | result | obj | allowance |
|---|---|---|---|---|---|---|
| Sun 11:47 | 8 DECISION @ 13:00 | 7 / 8 | **keeps 0, defers 7** (`DEFERRED_AMBIGUOUS_TIED_START`) | `DEFERRED_AMBIGUOUS_SELECTION` | 0 | 7 → 7 |
| Sun 12:02 | 8 DECISION @ 13:00 | 7 / 8 | keeps 0, defers 7 | `DEFERRED_AMBIGUOUS_SELECTION` | 0 | 7 → 7 |
| Sun 12:17 | 8 DECISION @ 13:00 | 7 / 8 | keeps 0, defers 7 | `DEFERRED_AMBIGUOUS_SELECTION` | 0 | 7 → 7 |
| Sun 12:32 | 8 DECISION @ 13:00 | 7 / 8 | keeps 0, defers 7 | `DEFERRED_AMBIGUOUS_SELECTION` | 0 | 7 → 7 |
| Sun 15:02 | 2 @ 16:05 + preview | 3 / 3 | keeps 2 | COLLECTED | 2 | 7 → 5 |
| Sun 15:17 | 3 @ 16:25 + preview | 4 / 4 | keeps 3 | COLLECTED | 3 | 5 → 2 |
| Sun 15:32 | preview | 1 / 1 | keeps 1 | COLLECTED | 1 | 2 → 1 |
| Sun 19:17 | 1 @ 20:20 | 1 / 1 | keeps 1 | COLLECTED | 1 | 1 → 0 |

**Result: 7 objects spent, 7 of 15 games priced, and all eight 13:00 games
unpriced.**

To answer the question asked directly: **the cluster is NOT picked up on a later
tick.** Nothing changes across the four ticks in its window — the grant stays 7,
the cluster stays 8, the prefix stays empty — so it defers identically every
time and then the window closes. The eight games with the day's earliest
decisions get no price at all.

The objects are not wasted: all 7 are spent on the later, untied games. But they
are spent on the *wrong* games relative to the lane's own priority rule, which
puts decision-window games first. This is a real weakness of deferral, and it is
the honest cost of refusing to let the provider choose.

### Where the cliff is

Coverage as a function of the Sunday allowance, from the same driver:

| allowance | objects spent | games priced | 13:00 cluster |
|---|---|---|---|
| 0–7 | = allowance | = allowance | **0 of 8** |
| 8–15 | = allowance | = allowance | 8 of 8 |
| 16 | 15 | 15 | 8 of 8 (1 object unspent) |

Coverage is `min(allowance, 15)` at every point — deferral never wastes an
object, it redirects it. But the cluster is **all-or-nothing at 8**.

How much balance headroom is there before the allowance drops below 8? From the
app's own shaping, holding the schedule fixed:

| month spendable | daily_total | basis | `sgo_nfl_events` | cluster |
|---|---|---|---|---|
| **185 (today)** | 32 | NEED_PLUS_SLACK | **15** | 8 of 8 |
| 120 | 21 | PRO_RATA | 9 | 8 of 8 |
| 110 | 19 | PRO_RATA | 8 | 8 of 8 |
| **102** | 18 | PRO_RATA | **8** | 8 of 8 ← threshold |
| 101 | 17 | PRO_RATA | 7 | **0 of 8** |
| 80 | 14 | PRO_RATA | 6 | 0 of 8 |

**The cluster is funded as long as the month's spendable balance is ≥ 102 when
Sunday's snapshot is taken.** It is 185 now — 83 objects of headroom. Sunday's
snapshot is taken once, at the first read of the service day, and held.

---

## 4. Does anything depend on the exact-ID request? No.

Verified three ways.

1. **No request carries `eventIDs`.** Every `startsAfter`/`startsBefore`/`limit`
   triple in §2 was captured from the actual call the collector made. The NFL
   lane's request builder (`fetch_nfl_pregame`) has no event-id parameter at all.
2. **The mode is never read.** `collect_nfl_pregame` does not call
   `exact_selection_mode`, and `sgo_exact_event_selection_mode` (deployed:
   `VALIDATE`) is documented in `config.py` as scoped to the MLB fallback. A
   test pins this: with the switch forced to `ON`, the NFL lane still defers and
   still sends no ids.
3. **The one discriminating test is unspent.** Read read-only from the ledger:
   `discriminating_test_state` returns
   `{attempted: False, passed: False, test_ledger_id: None, transition: None}`,
   and no `sgo_nfl_events` row carries `exact_event_selection` metadata.

AISP *does* hold a verified SportsGameOdds event id for all 14 Sunday games
(checked read-only: one id each, none shared, `external_id_mappings` agreeing
with the quote rows 14 of 14). Using them would turn Scenario B's 7-of-15 into
14-of-15. **That remains owner authority and is not assumed anywhere in this
reconciliation.**

---

## 5. Preview and decision are distinct phases — from the code path

`due_games` (`server/app/services/sgo_nfl_pregame.py`) chooses the phase first,
then applies the same already-priced test against **that phase's own window**:

```python
in_checkpoint = to_kickoff <= WINDOW_OPENS_BEFORE_KICKOFF
if in_checkpoint:
    opens, _closes = checkpoint_window(starts)        # kickoff-75m .. kickoff-15m
else:
    opens, closes = preview_slot(settings, starts)    # freeze-90m .. freeze
    if not (opens <= moment <= closes):
        continue
newest = _newest_moneyline(session, game_id)
if newest is not None and newest >= opens:
    continue                                          # one buy per WINDOW
```

The receipt labels each entry `CHECKPOINT` or `PREVIEW` (`entry["pass"]`), and
the two windows never overlap: the preview slot ends at the previous day's
16:30 ET freeze, the decision window opens 75 minutes before kickoff.

Both facts are demonstrated by the live data rather than asserted:

* Every Sunday game carried a **Saturday 15:02 ET** preview moneyline, and all
  eight 13:00 games were still `DECISION`-due at Sunday 11:47. A preview
  purchase does not satisfy a decision window.
* `313d2a19` was bought in its **preview** slot at Sun 15:32 ET, and was still
  `DECISION`-due at **Mon 19:02 ET** — 27.5 hours later, far outside the
  240-minute rule. Simulated against Monday's own allowance (`_day_need`
  2026-09-21 = `{mlb: 4, nfl: 1}`), it is bought once, for 1 object.

**One caveat, stated rather than hidden.** The implemented test is *"observed
since this window opened"*, not *"older than 240 minutes"*. These are not
identical. A quote observed 80 minutes before kickoff — just before the window
opens — is only 65 minutes old at the checkpoint and would pass a pure
240-minute test, yet the game is still bought. The rule is therefore slightly
**stricter** than the freshness rule, never laxer, and it is strictly fewer
objects than the deployed behaviour in every case (which had no test at all on
this pass and bought on every tick). The corner is narrow: the only writers in
the 75–80 minute band are this lane itself and the governed provider.

---

## 6. Allowance arithmetic, end to end

```
Sunday 2026-09-20, lane sgo_nfl_events, allowance 15

  11:47   8 objects   the eight 13:00 ET kickoffs     15 -> 7
  15:02   2 objects   JAX@DEN, LV@LAC (16:05)          7 -> 5
  15:17   3 objects   MIA@SF, WAS@DAL, SEA@ARI (16:25) 5 -> 2
  15:32   1 object    NYG@LAR preview (Mon slate)      2 -> 1
  19:17   1 object    IND@KC (20:20)                   1 -> 0
          --------
          15 objects spent, 0 remaining, 15 of 15 allowed

  games bought twice in the same phase : none
  maximum per-game count               : 1
  total ever exceeding the allowance   : no (monotone decreasing, floor 0)

Monday 2026-09-21, same lane, its own allowance 1
  19:02   1 object    NYG@LAR decision window          1 -> 0
```

Sunday's Saturday-side preview purchases (14 objects at Sat 15:02 ET) belong to
the **2026-09-19** service day and are not counted above. The Eastern service
day is `AISP_ALLOCATION_DAY_V2_EASTERN`, Eastern midnight to Eastern midnight.

---

## 7. What still needs owner authority

1. **Exact-ID selection for NFL.** Would raise Scenario B's floor from 7-of-15
   to 14-of-15 and remove the all-or-nothing cliff at 8. Requires widening a
   switch documented as MLB-only, and lane-scoping
   `discriminating_test_state` / `check_exact_response` / `exact_id_blockers`
   first. Not assumed here.
2. **Accepting the cliff.** If the exact-ID route stays closed, an NFL Sunday
   whose lane falls below the largest tied cluster loses that cluster entirely.
   The threshold today is a spendable balance of 102 against an actual 185, so
   Sunday is safe — but the shape of the failure is worth an explicit decision.

## 8. Reproducing this

Read-only drivers used (scratchpad, not committed): the live-schedule tick
driver, the Sunday allocation projection, the balance-threshold sweep and the
event-id provenance check. Each opens `SET TRANSACTION READ ONLY`, rolls back,
and stubs the provider call. The same behaviour is pinned in-repo by
`server/tests/test_nfl_lane_protections.py`, including a synthetic walk of this
Sunday's shape.
