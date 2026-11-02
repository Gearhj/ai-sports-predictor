# Load reality check, 2 November 2026 (scale plan Phase 1, item 6)

**Question.** Can today's backend serve 200 people reading the three heaviest pages
(Live View, Game Night, the Underdog Report) at once, and if not, what breaks first?

**Answer.** No. It serves about **20 readers** comfortably and tops out near
**14 requests a second**; past that every request queues (p50 20 s, p95 70 s at 200
readers). The first thing that breaks is **CPU in the Python web tier**, not the
database and not the number of processes. Adding web workers on this machine changed
nothing because the machine was already at 100 % CPU.

## How it was measured

- `scripts/load_check.py`: N simulated visitors, each opens a page (its API calls fire
  together, as the browser's do), reads for 15–30 s, opens another. GET only, no sign-in.
  The page definitions are the calls the production site made on 2026-11-02, read from the
  browser's network log.
- Against a **read-only side stack** of the Phase 1 branch on the same PC and the same
  PostgreSQL (scheduler and polling off), never against the live backend: once as one
  process (port 8011), once as three web-only workers (port 8012).
- **Contaminated, on purpose stated:** the run was at 18:20–18:50 ET on a game night, so
  the live backend's evening jobs, PostgreSQL, both side stacks and the load generator
  all shared one 4-core i7-4790 (8 threads, 32 GB). The numbers are a floor for this PC
  under real conditions, not the backend's best. Re-run on a quiet morning to get the clean
  figure; the shape of the result will not change.

## Numbers

| Stack | Visitors | Requests/s | p50 | p95 | Errors |
|---|---|---|---|---|---|
| 1 process, cold cache | 20 | 3.5 | 4.2 s | 8.7 s | 503 "page busy" on cold builds |
| 1 process, warm cache | 20 | 4.7 | **1.3 s** | 2.7 s | none from load |
| 1 process | 200 | **14.6** | 20.3 s | 68.2 s | 429 (one IP), timeouts |
| 3 web-only workers | 20 | 3.6 | 1.7 s | 23.9 s | each worker builds its own cold cache |
| 3 web-only workers | 200 | 13.6 | 21.8 s | 70.0 s | same |

Total CPU during the 200-visitor run: 84 %, 100 %, 99 %, 100 %, 92 % (5 s samples). The
one-process stack burned 332 CPU-seconds; the three workers about 120 each; PostgreSQL did
not appear in the top consumers. The 500s in the table are `users.email_verified_at does
not exist`: the Phase 1 code against a database that has not had the Phase 1 migrations
yet, expected on a side stack and not a load defect.

Per-request CPU cost works out near **150–200 ms** on average across these pages. Two
hundred readers opening a page every ~22 s with ~10 calls each is ~90 requests/s at
the peaks; at today's cost that is 15 cores, which no hosting plan in the scale plan buys.

## What this means for the plan

1. **The web tier must get cheaper per request before it gets bigger.** Most of these
   endpoints return the same bytes to everyone for 15–60 s (public config, the sports
   registry, mission control, the markets and decisions boards, scores, logos). The
   server's read cache stores Python objects and re-serializes them for every caller;
   serving the cached **bytes** (ETag + Cache-Control, no pydantic pass) would cut the
   CPU per request by an order of magnitude on the hot paths. One engineering item,
   no spend. Do this first; re-measure.
2. **Let Cloudflare answer the repeats.** Cache rules for the public GETs above with a
   15–60 s edge TTL cost nothing and work through the tunnel. With (1) and (2) the origin
   sees one request per endpoint per TTL instead of one per visitor.
3. **Hosting size (Phase 2, James's yes).** After (1) and (2), a 4-vCPU VPS is the right
   first box; without them, no sensible box is. The database was not the limit here.
4. **Per-IP rate limits are tuned for one household behind one IP.** The 429s in the
   200-visitor run came from every visitor sharing 127.0.0.1; real visitors have their own
   addresses. Nothing to change until Cloudflare's CF-Connecting-IP is trusted in
   production (`RATE_LIMIT_TRUST_CF_CONNECTING_IP`, already wired, off).
5. **Three workers are still the right shape** once the box has the cores: one job
   process (the scheduler owner) plus N web-only workers, which is exactly how the 11-01
   split backend ran. Each worker warms its own cache; a shared byte cache (item 1 in
   Redis or on disk) fixes that too.

## Re-running

```bash
python scripts/load_check.py --base http://127.0.0.1:8011 --users 200 --seconds 90 --game-id <a game id>
```

Start a side stack first (worktree, `.env` loaded, `SCHEDULER_ENABLED=false`); never point
it at port 8010 during a slate.
