# AISP Scale and Capacity Report — Fable 5.1 Audit

Audited commit `932f556` · repairs on `claude/fable51-production-readiness-audit` · 2026-09-03

Labels: **OBSERVED / REPRODUCED / INFERRED / RECOMMENDED / NOT TESTED / BLOCKED**.

---

## 1. Traffic model (planning scenarios, not claims about the current host)

| Tier | Registered | Concurrent (game-day) | Workload shape |
|---|---|---|---|
| A — initial public launch | 5,000 | 250 | ≥95% reads: Today hub, MLB Today, game detail, market board, performance, bankroll; periodic auth (login/me) |
| B — growth | 25,000 | 1,000 | same mix, heavier Today/performance reads |
| C — event burst | — | short spikes ≫ B | schedule publication (06:00), final-lineup window, final-pregame cutoff (T-15 min per game), game start, settlement |

Assumed per-user behaviour during a game-day session: 1 Today load + 2–3 game-detail loads + 1 market-board + occasional performance/bankroll, with page refreshes every few minutes ⇒ roughly **0.05 req/s per concurrent user** on heavy endpoints. Tier A ⇒ ~12 heavy req/s; Tier B ⇒ ~50 heavy req/s; Tier C bursts ⇒ 100+ req/s for a minute.

## 2. Test environment

| Item | Value |
|---|---|
| Host | single Windows 10 workstation (the operational machine) |
| Backend under test (repaired) | isolated uvicorn on `127.0.0.1:8020` from the audit worktree, **1 worker**, `SCHEDULER_ENABLED=false`, every provider/observer flag off, no odds key |
| Backend (baseline) | operational uvicorn on `:8010` (commit 932f556, scheduler + observers armed) |
| Database | live `asp` on `localhost:5432`, **read-only access only** (GET endpoints; zero writes; zero provider calls — verified by code path and tests) |
| Isolated clone for write/load tests | **BLOCKED** — `asp_user` lacks `CREATEDB` (`permission denied to create database`); the audit did not use a superuser credential. Command for the owner: `CREATE DATABASE asp_loadtest TEMPLATE asp;` run as postgres, then restore-based clone script in the scratch notes. |
| Load tool | asyncio + httpx client (`bench.py`, `bench2.py`), bounded to ≤ 320 requests per case |
| Data | real operational slate for 2026-09-02 (15 MLB games, ~2,160 odds snapshots per game, 9 sportsbooks); 2026-09-03 (9 games, 0 forecasts) |

Why the full Tier A/B load test was **not** executed: the mandate forbids load-testing a database that holds irreplaceable operational evidence. The characterization below is bounded (tens of requests per case), read-only, and sufficient to identify the first two bottlenecks and the throughput ceiling. Tier-scale numbers are **INFERRED** from the measured per-request cost and the single-process serialisation observed.

## 3. Endpoint cost — baseline (operational `:8010`, warm, concurrency 1)

| Endpoint | t1 / t2 / t3 (ms) | bytes |
|---|---|---|
| `/sports/registry` | 3 / 3 / 3 | 2,398 |
| `/public/config` | 101 / 188 / 98 | 335 |
| `/today` (today: empty slate) | 640 / 574 / 835 | 3,706 |
| **`/today?date=2026-09-02` (15 games)** | **9,085 / 9,294 / 8,177** | 44,554 |
| **`/markets/board?date=2026-09-02`** | **7,244 / 8,125 / 8,358** | 80,487 |
| `/performance` | 4,425 / 4,873 / 4,667 | 5,506 |
| `/research/history` | 3,098 / 3,290 / 3,615 | 7,918 |
| `/experiment/comparison` | 1,301 / 1,549 / 1,431 | 1,373 |
| `/bankroll?experiment=v2` | 733 / 937 / 1,001 | 4,070 |
| `/prediction-markets/board` | 716 / 707 / 684 | 5,776 |
| `/games/{id}` | 677 / 799 / 660 | — |
| `/shadow-board?sport=NFL` | 18 / 11 / 12 | 153 |

Instrumented (SQLAlchemy cursor events + cProfile, same code, read-only session):

| Read path | SQL queries | SQL time | wall | Python |
|---|---|---|---|---|
| `ReadService.dashboard(2026-09-02)` | 509 | 785 ms | 7,320 ms | **6,536 ms** |
| `TodayAllSportsReadService.build(2026-09-02)` | 547 | 778 ms | 7,043 ms | 6,265 ms |
| `market_board(2026-09-02)` | 63 | 477 ms | 5,958 ms | 5,481 ms |
| `performance()` | 114 | 1,211 ms | 4,224 ms | 3,013 ms |

cProfile: `ReadService._latest_market_snapshots` — 30 calls, **8.0 s cumulative**, 258,974 ORM instances, 71,596 JSON loads. **REPRODUCED.**

## 4. Concurrency characterization — baseline (`:8010`, scheduler running)

| Endpoint | c | n | rps | p50 | p95 | p99/max | err |
|---|---|---|---|---|---|---|---|
| `/sports/registry` | 1 | 30 | 1.05 | 5 ms | 9 ms | **28,439 ms** | 0 |
| `/sports/registry` | 16 | 160 | 2.54 | 468 ms | **53,216 ms** | 53,268 ms | 0 |
| `/sports/registry` | 64 | 256 | 114.5 | 517 ms | 769 ms | 874 ms | 0 |
| `/public/config` | 8 | 48 | 16.5 | 451 ms | 835 ms | 851 ms | 0 |
| `/prediction-markets/board` | 8 | 32 | 1.60 | 4,916 ms | 6,133 ms | 6,429 ms | 0 |
| `/bankroll?experiment=v2` | 8 | 24 | 1.04 | 7,542 ms | 8,840 ms | 8,850 ms | 0 |
| `/today` (empty) | 8 | 24 | 1.14 | 7,101 ms | 7,619 ms | 7,626 ms | 0 |
| `/today?date=2026-09-02` | 1 | 2 | 0.10 | 10,185 ms | — | — | 0 |
| `/today?date=2026-09-02` | 4 | 8 | 0.12 | 34,343 ms | 36,688 ms | — | 0 |
| `/today?date=2026-09-02` | 8 | 8 | **0.13** | **61,917 ms** | 61,922 ms | — | 0 |

Interpretation (**REPRODUCED**): the 28–53 s stalls on a 3 ms endpoint coincide with `freeze-shadow-experiment`, `finalize-games` and `poll-availability-events` runs in the backend log (16:16–16:21Z). The scheduler's coroutine jobs execute sync DB/CPU work on the API event loop, so *every* request waits for the job. Heavy pages serialise linearly with concurrency (single process, GIL-bound Python).

## 5. After repair — isolated `:8020` (repaired code, no scheduler, idle machine)

Instrumented:

| Read path | queries | SQL | wall | Python | Δ wall |
|---|---|---|---|---|---|
| `dashboard(2026-09-02)` | 494 | 562 ms | **2,354 ms** | 1,792 ms | **3.1× faster** |
| `today_all_sports(2026-09-02)` | 532 | 458 ms | **1,710 ms** | 1,252 ms | **4.1×** |
| `market_board(2026-09-02)` | 48 | 188 ms | **320 ms** | 131 ms | **18.6×** |
| `performance()` | 135 | 1,328 ms | 4,099 ms | 2,772 ms | unchanged (different hotspot) |
| `bankroll("v2")` | 5 | 130 ms | 916 ms | 786 ms | ≈ |

ORM instances for the dashboard path: 258,974 → 11,701. No single function dominates the remaining 1.8 s of Python; it is spread across ORM hydration and per-game feature/policy assembly — the next step is caching, not another micro-fix.

HTTP:

| Endpoint | c | n | rps | p50 | p95 | max |
|---|---|---|---|---|---|---|
| `/sports/registry` | 1 | 20 | 183 | 5 ms | 20 ms | 20 ms |
| `/sports/registry` | 32 | 320 | 96.5 | 183 ms | 900 ms | 1,840 ms |
| `/public/config` | 8 | 48 | 28.6 | 185 ms | 485 ms | 812 ms |
| `/today` (today, 9 games, no forecasts) | 1 | 3 | 1.12 | 921 ms | 934 ms | 934 ms |
| **`/today?date=2026-09-02`** | 1 | 3 | 0.63 | **1,597 ms** | 1,672 ms | 1,672 ms |
| **`/markets/board?date=2026-09-02`** | 1 | 3 | 2.87 | **307 ms** | 438 ms | 438 ms |
| `/performance` | 1 | 3 | 0.24 | 4,244 ms | 4,402 ms | 4,402 ms |
| `/bankroll?experiment=v2` | 1 | 3 | 0.81 | 1,025 ms | 1,705 ms | 1,705 ms |
| `/games/{id}` | 1 | 3 | 3.35 | 308 ms | 338 ms | 338 ms |
| `/prediction-markets/board` | 1 | 3 | 1.43 | 668 ms | 878 ms | 878 ms |
| `/today?date=2026-09-02` | 4 | 8 | 0.62 | 6,636 ms | 7,068 ms | 7,068 ms |
| `/today?date=2026-09-02` | 8 | 16 | **0.69** | 11,398 ms | 11,892 ms | 11,892 ms |
| `/markets/board?date=2026-09-02` | 8 | 16 | 4.42 | 1,646 ms | 2,053 ms | 2,053 ms |
| `/today` (today) | 8 | 16 | 1.20 | 5,644 ms | 7,637 ms | 7,637 ms |

## 6. Bottlenecks

1. **First bottleneck — CPU-bound per-request board reconstruction in one Python process.** After the query repair a 15-game Today costs ~1.6 s CPU; throughput is ~0.6–0.7 rps *regardless of concurrency* (GIL serialisation). `/performance` costs ~4.2 s. There is no cache and no read model. **REPRODUCED.**
2. **Second bottleneck — scheduler work on the API event loop.** Adds multi-second to minute-long stalls to *all* endpoints every 2–5 minutes. **REPRODUCED.**
3. Third — DB pool (5+10, 30 s wait) versus a 40-slot threadpool; repaired to 10/20/10 s (OBSERVED, NOT load-verified against PostgreSQL contention because the clone was BLOCKED).
4. Fourth — `odds_snapshots` latest-per-book scan: after the rewrite it is 11 ms/game, 7,487 buffers, all index scans (EXPLAIN ANALYZE, read-only). A composite `(market_outcome_id, observed_at DESC)` index would shave the 727-buffer inner scan; benefit is milliseconds — **RECOMMENDED, not required**.
5. Database CPU/locks: not the limiter at these volumes (SQL time 0.5–1.3 s per heavy request is dominated by row count, not waits; `pg_stat_activity` active = 9). `pg_stat_statements` is not installed, so slow-query attribution is **NOT TESTED** beyond the instrumented paths.

## 7. Capacity verdict

**Current maximum sustainable throughput (single process, repaired code, no scheduler interference):**

* Static/cheap reads: ~100–180 rps.
* Game detail / market board: ~3–4 rps.
* Today (15-game slate): **~0.6 rps** (≈ 36 page loads per minute across all users).
* Performance page: ~0.25 rps.

**Safe concurrency estimate today:** roughly **5–10 concurrent active users** before p95 of the landing page exceeds 10 s; with the operational scheduler co-located, periodic freezes make even that unreliable.

**Against the SLO targets** (public/cached reads p95 < 300 ms; authenticated p95 < 500 ms): only the static endpoints meet them. The heavy pages miss by 5–15× at concurrency 1 and by 20–40× at c=8.

**Tier A (250 concurrent) is not supportable by the current topology.** With the read-model cache from the architecture plan (§2.2), Today/market-board/performance become ~5–20 ms cache hits; the per-second cost then falls to the cache-miss rate (one recomputation per evidence bump, i.e. per scheduler job), and Tier A is comfortably within 2 API replicas. **INFERRED** from measured per-request cost; must be re-measured after the cache lands on an isolated clone.

Resource profile observed during characterization: single Python process pegged one core during heavy reads; memory not a limiter; DB connections 9–15.

Scale-up limit: one process ≈ one core of Python; `uvicorn --workers N` multiplies heavy-read throughput ~N× but also multiplies the embedded scheduler (**must** be separated first). Scale-out blockers: in-process scheduler, in-memory rate limiter, module-global scheduler flags (see findings F51-002, F51-009).

Recommended capacity margin: provision for 3× the modelled Tier A heavy-read rate on cache misses (i.e. tolerate a fully cold cache at ~36 rps for one minute) ⇒ 2 API replicas × 2 workers plus the cache.

## 8. Test matrix items 57–65 — status

| # | Test | Status |
|---|---|---|
| 57 | Public Today load | REPRODUCED (bounded, read-only, live DB) — numbers above |
| 58 | Game-detail load | REPRODUCED (c=1) |
| 59 | Authenticated bankroll read | REPRODUCED for the public `/bankroll` (no per-user bankroll endpoint exists) |
| 60 | Open-position list | N/A — no per-user positions endpoint; platform ledger covered by `/bankroll` |
| 61 | DB pool saturation | NOT TESTED (clone BLOCKED); pool resized and unit-tested |
| 62 | Cold vs warm cache | N/A — no cache exists; first-request costs above are effectively "cold" every time |
| 63 | Short burst | REPRODUCED at c=8/32/64 (bounded) |
| 64 | Sustained read-heavy | NOT TESTED (would require the isolated clone) |
| 65 | Graceful degradation under dependency failure | PARTIAL: `/readyz` returns 503 on DB failure (unit-tested); provider failures degrade to `job_failed` + honest UNAVAILABLE (OBSERVED in live logs: `refresh-odds` ConnectError ×36, surfaced, no fabrication) |
