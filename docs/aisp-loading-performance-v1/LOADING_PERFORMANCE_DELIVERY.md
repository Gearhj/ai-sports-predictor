# AISP loading, prefetching and background refresh

**As of** 2026-09-12 · **Branch** `claude/aisp-v18-and-price-coverage-v1` (working tree)
**Status** Deployed. Backend restarted through the supervised task; frontend
changes are live on the Vite dev server and the production build succeeds.

No package was added and none was upgraded. No new service, Redis, service
worker or second scheduler. The installed router (react-router-dom 7.1.1) and
React 18.3.1 are used as they are.

---

## 1. What the user experiences differently

| Before | Now |
|---|---|
| Nothing was on screen until all data arrived. | The page's shell and skeleton are up in **~150–290 ms**. |
| Each slate read took **5.5–46 s**. | Each takes **~0.9–1.5 s**. |
| Navigating away and back re-fetched everything from an empty page. | Returning renders **immediately from cache, with zero requests**. |
| A page under load showed a spinner on a blank screen. | The page's **own structure** is on screen from the first paint, then fills in. |
| A failed refresh replaced working content with an empty page. | The **last good read stays on screen**, marked stale, with a Retry. |
| No indication of how old anything was. | A small line reads **"Slate read 20s ago"**, and says when it is updating. |
| Opening a game waited for the whole request. | Hovering the link **warms it first**; the click then paints in ~150 ms. |
| The coverage panel returned HTTP 500 for every reader. | It answers in **~0.4–0.7 s**. |

---

## 2. The measured bottleneck, and what it actually was

Endpoint timings first, measured against the live backend on 8010, three runs
each, **serially** (see §7 on concurrency):

| Endpoint | Before (ms) | After (ms) |
|---|---|---|
| `/dashboard/today` | 46052, 16645, 8263 | **1532, 1016, 1382** |
| `/today` | 6291, 5783, 7280 | **1077, 924, 1142** |
| `/games/slate` | 5587, 6114, 5853 | **845, 980, 1088** |
| `/coverage/price-funnel?league=MLB` | HTTP 500 | **645, 663, 416** |
| `/markets/board` | 140, 702, 141 | 164, 195, 149 |
| `/research/model-era` | 24, 30, 17 | 28, 18, 12 |
| `/bankroll?experiment=v2` | 21, 731, 16 | 39, 18, 19 |

Server-side profile of `/dashboard/today` before the fix:

```
TOTAL 7.740s | queries 128 | sql 1.076s
```

Only **1.08 s of that was the database**. The remaining ~6.7 s was Python. A
cProfile run named the line:

```
 15 calls  7.151s cum  read_service.py:1114(game_card)
  3 calls  7.085s       read_service.py:3037(_snapshot_readiness)
  3 calls  7.083s       game_state_service.py:90(get_game_state)
  6 calls  7.045s       game_state_service.py:173(_team_state)
  6 calls  6.255s       game_state_service.py:393(_current_roster)
```

`_current_roster` loaded **all 14,149 rows** of
`roster_availability_observations`, hydrated them into ORM objects, deduplicated
them in Python down to **1,037** current players, and then kept only the one
team it was asked about — **six times per dashboard**. 93% of the objects it
built were discarded immediately, and it rebuilt them for every team.

**The fix** (`server/app/services/game_state_service.py`): resolve the current
roster once and reuse it.

- A `_resolved_roster()` helper memoises the resolution on `Session.info`, keyed
  by `(cutoff, source_mode)` — so six calls in one request do one resolution.
- On PostgreSQL the deduplication moves into the query as
  `DISTINCT ON (player_id)` with the original ordering, so ~1,037 rows are
  hydrated instead of 14,149. The previous Python `_dedupe` is retained as the
  SQLite fallback the tests run on.
- The semantics are unchanged: the roster is still resolved **globally and then
  filtered by team**, which is what stops a traded player's old-club row
  surviving as current.

After the fix: `TOTAL 2.378s` cold / `0.952s` warm, 125 queries, SQL 0.33–0.48 s.

### A second, separate backend defect

`/coverage/price-funnel` returned **HTTP 500 to every reader**, so the coverage
panel on the decisions board could never render. The cause was one missing pair
of parentheses in `price_coverage_funnel.py:297`: `entry.age_at_cutoff` is a
method, and passing it uncalled put a bound method in the response body, which
FastAPI could not serialize. Every other caller in the codebase invokes it. It
was pre-existing — the same 500 reproduces with all of this work stashed.

### What a cold page load actually costs now

Per-endpoint numbers are not page-load numbers, and it would be misleading to
present them as if they were. Measured from `navigationStart` on a cold
`/decisions`:

| | |
|---|---|
| Shell and skeleton on screen | **141–287 ms** |
| All reads issued | by **~210 ms**, concurrently |
| Board fully populated | **~5.6 s** |

The gap is not per-request latency — each read is ~1 s on its own. It is that
the browser issues six reads at once and a single-worker backend serves them one
at a time, so cold wall-clock is closer to their *sum* than their maximum. That
is unchanged by this work and is called out in §7.

What did change is that the reader is no longer looking at nothing for those
seconds, that one of the six reads was a duplicate and is now gone, and that
coming back costs none of it.

---

## 3. Fast first render, cached return, background refresh

### One shared query cache — `client/src/services/queryCache.ts`

The frontend held every response in component state. Unmounting a page — which
is what navigation does — discarded the data, so returning always started from
`null` and re-fetched. And the old error path called `setData(null)`, so a
failed refresh actively erased content that was fine a second earlier. Those two
lines are the whole reported symptom.

The cache is ~290 lines over `fetch`. It guarantees:

- **Scoped identity.** A key carries everything that changes the answer. One
  game's numbers cannot appear under another's, because they are different keys
  — and `useApiResource.render.test.tsx` asserts that game B renders empty
  rather than briefly wearing game A's figures.
- **Account scoping, enforced centrally.** Every key is namespaced by the
  signed-in account inside the cache itself, and changing identity clears it.
  Scoping is *not* left to call sites — see §5.
- **Content survives failure.** A failed refresh keeps the last successful
  snapshot and reports the error beside it.
- **One request per key.** Concurrent readers share one in-flight promise. An
  explicit refresh is the exception: it starts a new request rather than being
  swallowed by one that is already struggling.
- **No torn state.** Every write carries its fetch's sequence number; a response
  that started earlier than the newest one is discarded.
- **Bounded background work.** At most 3 concurrent prefetches.
- **Obsolete requests are abandoned.** When the last reader of a key navigates
  away, its in-flight request is aborted after a short grace window, so a page
  the reader has left cannot hold a connection from the one they are looking at.
  The grace matters: React StrictMode mounts, unmounts and remounts in
  development, and an immediate abort would cancel a request about to be wanted
  again a tick later.

### Refresh cadences, matched to how fast the data can change

| Data | Interval |
|---|---|
| Current slate, prices, decisions | 60 s |
| Operational status, bankroll ledger | 120 s |
| One game's detail | 90 s |
| Finalized history, research exports | 15 min |
| Registry, policy, methodology | 60 min |

Polling **pauses entirely while the tab is not visible** and refreshes on return
if what is on screen has gone stale. Verified live: with the pane hidden,
`requestsAttempted` was 0.

### Skeletons instead of a spinner

`Skeleton`, `SkeletonTable` and `SkeletonCards` render the page's own structure
while the first read is outstanding, on `/`, `/decisions`, `/games`, `/markets`
and the MLB desk. Measured live on an uncached navigation to `/markets`: **57
placeholders** on screen, replaced by real content. They are deliberately blank
rather than zeroed — an empty cell cannot be misread as a real edge of 0.00%.

### Prefetch on intent

`PrefetchLink` / `PrefetchNavLink` warm a destination on hover, keyboard focus
or touch — both the route's lazy chunk and its first read. Measured live:

```
hover  → /api/v1/games/{id}                       (1 request)
click  → heading painted at 150 ms, no loading panel,
         /api/v1/games/{id} NOT re-requested
```

### Request cost

Every refresh and prefetch is a **GET against stored AISP data** — the same read
the page would issue a moment later. Nothing on this path posts, so no browser
activity can acquire a quote, spend an odds credit, record a decision or run a
model; those remain backend jobs. Re-reading a stored quote does not give it a
new evidence timestamp: `updatedAt` is a **display** clock, labelled as when the
browser last read, and it is shown separately from the payload's own forecast,
quote and cutoff times. Display caching relaxes nothing — freshness windows,
the T−15 cutoff, model authority and the 2.00% minimum are all enforced on the
server against evidence clocks and are untouched.

Measured request counts:

| Action | Requests |
|---|---|
| Cold load of `/decisions` | 6 (was 7 — `/public/config` was fetched twice) |
| Hover a game link | 1 |
| Click that link | 2 (secondary panels only) |
| **Return to `/decisions`** | **0** |

---

## 4. Defects found and fixed along the way

1. **Prefetch wrote to a namespace the page never read.** Scoping was applied in
   the hook but not in `prefetchQuery`, so every warm read landed in a slot the
   page could not see and the page refetched anyway. Fixed by moving scoping
   inside the cache so a prefetch and a read cannot disagree. Two regression
   tests cover it.
2. **Two panels duplicated every game request.** `ShadowForecastPanel` and
   `MarketPulsePanel` each did their own `useEffect` + `fetch`, outside the
   cache; `shadow-forecasts` and `prediction-markets/games/{id}` were each
   requested **twice** per game. Routed through the cache: now once.
3. **An in-flight request swallowed explicit refreshes.** Deduplication returned
   the pending promise even for a user-initiated refresh, so pressing refresh
   during a slow request did nothing. `force` now supersedes it.
4. **`useSyncExternalStore` needs immutable snapshots.** Entries were being
   mutated in place, which updates the cache without re-rendering anything
   reading it. Entries are now replaced on every write.
5. **`/public/config` was read twice on every cold load.** `PublicPolicyProvider`
   had no deduplication and StrictMode runs its effect twice. On a single-worker
   backend a duplicate read is not free: it queues ahead of the slate the reader
   is waiting for. Routed through the cache as reference data.
6. **`OddsCollectionPanel` crashed the admin page** on a payload without a
   `budget`, taking the whole route down (there is no error boundary). It now
   reports the absence instead. This was latent; the timing change surfaced it.
7. **`PublicPolicyProvider` would have rendered an incomplete config.** Its old
   `.then`/`.catch` absorbed a malformed response by accident. Made explicit: a
   response missing the component weights or the bankroll policy falls back to
   the development default and says so, rather than being rendered as a
   published policy with zeros in it.

---

## 5. Cache keys and invalidation

Keys are built with `cacheKey(name, scope)` and include sport, slate date, game
id, season/week and every active filter. Account scope is applied centrally
rather than written into each key, because "no call site forgets" is not a
property you can get from a convention — so signing in, switching account or
signing out changes the namespace and empties the cache in one place
(`useAuth.tsx` → `setCacheScope`).

Invalidation marks an entry stale **without removing it from the screen**, so a
version change refreshes in the background rather than blanking the page.

Call sites that do not pass a key keep their previous per-mount behaviour, so
nothing was changed by omission.

---

## 6. Verification

**Tests.** 28 new tests (21 cache, 7 hook-render) covering key scoping, account
scoping, stale refresh, out-of-order responses, failed-refresh retention,
in-flight sharing, prefetch/read agreement, prefetch concurrency and
obsolete-request cancellation.

**No regressions.** Full client suite before this work: **44 failures**. After:
**43**, the same set minus one load-sensitive timeout, with **zero new
failures** — verified by capturing a real baseline with the changes stashed and
diffing failing test names, not by trusting a remembered number.

The cache is module-global by design, which is what makes return navigation
work; `src/test/setup.ts` now clears it before each test so a response mocked in
one test can never be served to the next.

**Server suite.** 20 failures, and **all 20 fail identically with this task's
two backend files stashed** — they come from earlier authorized work in this
session (the NBA migration head, the added research-scoring job) or predate it.
None originates here. `test_funnel_read_model.py`'s 4 query-budget tests fail on
baseline too.

**Production build** succeeds (`vite build`, 26.3 s).

**Deployment.** The backend was restarted through the Windows Scheduled Task
"ASP Backend 8010". Stopping the task orphans the uvicorn tree, which this
session could not signal (not elevated) — but the supervisor already handles
exactly that: it detected the stale holder, reclaimed port 8010, and started
clean.

```
port 8010 is served by a healthy but FOREIGN process (pid 37524) -- a stale
owner from a previous run of this task. Reclaiming it.
reclaimed port 8010 from stale pid 37524
scheduler instance 0a33056a15e4458ab037cdceb9bdef11 claimed
uvicorn launched (cmd pid=26532) on 127.0.0.1:8010
```

**Single supervised owner verified afterwards**: one listener on 8010, one
scheduler claim, task state Running.

**In-progress NBA work preserved.** The restart also brought the NBA admin
routes live for the first time: `/api/v1/admin/nba/data-status` now answers 401
(admin-gated) rather than 404. Its access boundary is unchanged —
`NBA_REAL_DATA_PILOT_ACCESS_BLOCKED`, 0 provider requests, $0.

**Live MLB/NFL behaviour preserved.** No policy, threshold, readiness weight,
model authority or bankroll value was touched. No Gen 1 simulation is presented
as V18 anywhere in this change.

---

## 7. Remaining slow paths — not fixed

### Request concurrency is now the dominant cold-load cost

Six reads start within ~50 ms of each other and a single-worker uvicorn serves
them sequentially. Measured on one cold `/decisions`: every request started by
210 ms and the last finished at 5,614 ms, though each is ~1 s served alone.

Fixing this means either more uvicorn workers or fewer reads per page — the
first is a deployment change and the second a read-model change, and this task
excludes a new service or a broad redesign. It is now the largest remaining
item, and it is a different problem from the one that was assigned: a cold load
is slow, but no page is blank or wrong while it happens, and a return costs
nothing.

### Per-call costs inside `/dashboard/today`

It is now ~1.0–1.5 s rather than ~20 ms of Python plus 6.7 s of waste. The
remaining cost is spread thin rather than concentrated, so there is no second
single fix:

| Path | Cost |
|---|---|
| `bankroll` / `_experiment_bankroll` | 0.652 s |
| `ledger_view` | 0.360 s |
| `build_funnel_read_context` | 0.328 s |
| `_daily_rows` | 0.314 s |
| `_decision_metrics` | 0.273 s |
| `game_card` (was 7.151 s) | 0.257 s |
| `_current_roster` chain (was 6.255 s) | 0.157 s |

Each would need its own change, and none of them is what made pages look broken.
With cached return navigation and background refresh in place, a reader now pays
this once rather than on every visit.

Two other honest limits:

- **`/experiment/funnel` is ~0.8–1.4 s** and is fetched by the decisions board.
  It loads concurrently and does not block the decisions themselves.
- **The scheduler shares the backend process, and concurrency degrades
  sharply.** The 46-second outlier in the "before" column was `/dashboard/today`
  competing with a scheduler job holding the same interpreter. Driving five
  routes at once during verification pushed `/today` to 44 s while the server
  test suite also ran; the same endpoint measured serially, moments later, was
  1.0–1.4 s. The roster fix removed the 6.7 s of CPU that made these collisions
  expensive, and the cache removes most of the repeat requests that caused them,
  but a heavy job can still slow a request. Separating them would mean a second
  process, which this task explicitly excludes.

  All "after" figures in §2 are serial measurements. They are honest for one
  reader on an otherwise quiet machine, which is this deployment, and they are
  not a claim about behaviour under concurrent load.
