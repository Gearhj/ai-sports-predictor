# Three reconciliations — 2026-09-09

Code commit: `8c356fc4be89fc416b2d94360cc006fa03db9ec4`
Backend: ASP Backend 8010, pid 49916, started 19:55:05 EDT, `/readyz` 200.
Nothing in this work reset, backfilled or mutated financial history, and no
threshold, policy, position or wagering authority was changed.

---

## 1. SportsGameOdds opening usage

24 objects of prior consumption are now carried by the persistent budget as ONE
explicit opening adjustment, not as four reconstructed request rows.
Reconstructed rows would have put four fabrications into an audit trail.

    provider_request_ledger, provider = SPORTSGAMEODDS
    2026-09-09 08:00  sgo_opening_adjustment  RESERVED   objects=None
    2026-09-09 08:00  sgo_opening_adjustment  SUCCEEDED  objects=24   OPENING_BALANCE_ADJUSTMENT
    2026-09-09 20:00  sgo_mlb_events          RESERVED   objects=None
    2026-09-09 20:00  sgo_mlb_events          SUCCEEDED  objects=1

Source evidence lives in `server/scripts/sgo_opening_adjustment.py`: four
authorised fetches of 2 + 2 + 10 + 10 objects, two addressed by response digest,
two annotated as not retained. The script asserts the evidence sums to its own
declared total before writing.

Idempotent by construction rather than by checking first — the fingerprint is
derived from the adjustment's content and the ledger holds a unique constraint
over `(provider, endpoint_key, request_fingerprint, status)`. A second run
returns `{"applied": false, "reason": "ALREADY_RECORDED"}`.
Ledger id `31b6d3c9-f75e-45bc-92f3-44293f8fc035`.

An adjustment records objects spent, not a call anyone made, so it is excluded
from the request count and excluded from the fetch cooldown.

### Allowance window and remaining balance

    {"window_days": 30, "requests_in_window": 1, "objects_used_in_window": 25,
     "allowance": 2500, "reserve_never_spent": 400, "objects_spendable": 2075,
     "counted_locally_because": "the provider returns no usage headers"}

The provider's own allowance window is a **calendar month**; the local guard
deliberately uses a **rolling 30 days**, which can over-count near a month
boundary and can never under-count. The 400-object reserve is never spendable,
so the guard fails closed at 2,100 objects rather than at 2,500.

Counting is local because SportsGameOdds returns no usage headers. Its billable
unit is one **event object** returned by `/events`, not one price.

---

## 2. Settlement history

### Database identity

    database  asp
    user      asp_user
    schema    public
    port      5432
    url       postgresql+psycopg://asp_user:***@localhost:5432/asp

Both the scheduled backend (8010) and the user-facing API read this same
database.

### What is actually there

`position_results` is the settlement ledger — **10 rows**, realized
**+$1.88**, newest `bankroll_after` **$1001.88** at 2026-09-08 01:21:42 EDT.

`official_asp_positions` — **11 rows**, all belonging to experiment
`43fe1b16-bd4d-4fce-ad6e-2aad7c5ebd66` (V2.1):

    OPEN    / PENDING   1   staked  2.50   payout  0.00
    SETTLED / LOSS      6   staked 15.00   payout  0.00
    SETTLED / WIN       4   staked 10.00   payout 26.88

The other two experiments — `13604fc0` (V1) and `129b6a66` (V2) — hold zero
positions. All three carry `starting_bankroll` 1000.00, which is correct and
identical by design.

`shadow_decision_settlements` holds **0 rows** and is unused.
`virtual_positions` holds **0 rows**.

### Cause of the contradiction

The two reports did not disagree about the data. My later report was wrong in
two ways:

1. I queried `shadow_decision_settlements` (0 rows, unused) instead of
   `position_results` (10 rows), and concluded there was no settlement history.
2. I read `shadow_experiments.starting_bankroll` and reported three $1,000
   figures as if they were *current* bankrolls. They are starting bankrolls.

The earlier report — 11 positions, 10 settled, +$1.88 realized — was correct.

`/bankroll?experiment=v2` returns 1001.88 / 1 open / 10 settled on both 8010 and
8018. The bare endpoint defaults to V1 (which has no positions), but the
client's `resolveExperimentKey` defaults to `'v2'`, so the UI shows the correct
figures. No data was lost, reset or mutated.

---

## 3. Cutoff coverage

Both defects were confirmed live and fixed, preserving every existing threshold
and the single scheduler owner.

**Defect A — "has any quote" was treated as sufficient coverage.** A game whose
quote would age past `MAX_QUOTE_STALENESS_MINUTES` (240) before its
`FINAL_PREGAME` cutoff was reported covered and never refreshed, so the quote
was guaranteed to fail the staleness gate at decision time. Added
`usable_through_cutoff` and `needs_refresh` to `EventCoverage`, plus
`DECISION_LATENCY_MINUTES = 5` so a quote must survive the decision itself, not
merely reach the cutoff instant. New totals `covered_but_expiring` and
`needs_refresh`.

**Defect B — stale primary rows suppressed a valid fallback pair.** Primary
preference was applied unconditionally, so a governed row too old to price
anything still displaced a usable fallback pair. `prefer_primary()` now takes a
`primary_usable` predicate; `_primary_can_price()` requires one book carrying
BOTH outcomes AND freshness within 240 minutes. A price that cannot be used no
longer costs a game the only price that can.

### Observed end to end

Immediately after the fix, CIN @ LAD — the only remaining pre-cutoff game, and
the one carrying the QUALIFIED_BET — showed age-at-cutoff 236.8 min, so with the
5-minute decision latency it would NOT have stayed usable. It was correctly
flagged `needs_refresh: true`.

The 20:00:09 scheduled checkpoint then acted on that flag. The primary lane was
latched shut (`PROVIDER_FLOOR_LATCHED` — balance 5, floor 10, never lowered), so
the governed fallback ran:

    reason        FALLBACK_COLLECTED
    objects       1
    events        1        (games_open_before_cutoff = 1)
    quote_rows    18       pairs 9
    bookmakers    betmgm bovada caesars draftkings espnbet fanduel
                  pointsbet unibet williamhill
    skew          max 0.0s, pairs_with_skew 0, simultaneity_claimed false
    filters       leagueID=MLB limit=1 finalized=false oddsPresent=true
                  startsAfter=2026-09-10T00:00:09Z startsBefore=...T03:00:09Z

The ledger row records the filters and the bounded window, distinguishes the one
request from the one billable event object, and contains **no credential
material** (verified by searching the stored metadata for the configured key).

The 20:05:10 checkpoint did **not** duplicate it:

    {"attempted": false, "reason": "FALLBACK_COOLDOWN",
     "seconds_since_last_fetch": 300.6, "cooldown_seconds": 600}

The SGO request ledger still holds exactly two rows for that one request
(RESERVED + SUCCEEDED), and the budget still reads 25 objects.

### Final coverage

    totals: {"events": 14, "covered": 10, "covered_but_expiring": 0,
             "needs_refresh": 4, "within_freshness_target": 5,
             "at_risk": 0, "missed": 4, "coverage_rate": 0.7143}

One game remains before its cutoff:

    CIN @ LAD    cutoff 21:55 EDT
      quote observed 19:52:14 EDT, Fanduel via SportsGameOdds (research)
      home -275 / away +225, 18 qualifying rows
      age at cutoff        122.8 min   (limit 240, plus 5 min latency)
      usable_through_cutoff  true
      needs_refresh          false
      next action: optional freshening toward the 60-minute target at 21:10

Every remaining game's quote stays usable through its cutoff. The four games
counted as `needs_refresh` are all already past their cutoffs and are not
requested.
