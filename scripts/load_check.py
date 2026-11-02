"""Load reality check (scale plan 2026-11-01, Phase 1 item 6).

Simulates N visitors on the three heaviest pages (Live View, Game Night, the
Underdog Report): each visitor opens a page (its API calls fire together, as the
browser's do), reads for a while, then opens another. Reports throughput,
latency percentiles per endpoint and the first thing that broke. GET only, no
sign-in, no writes; point it at a side stack, never at the live backend during a
slate.

    python scripts/load_check.py --base http://127.0.0.1:8011 --users 200 --seconds 90

The page definitions below were captured from the production site on 2026-11-02
(the browser's own network log); pass --pages some.json to use another set.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

import httpx

DEFAULT_PAGES: dict[str, list[str]] = {
    "live-view": [
        "/api/v1/public/config",
        "/api/v1/sports/registry",
        "/api/v1/mission-control?date={today}",
        "/api/v1/scoreboard?game_ids={game_id}",
        "/api/v1/probability/games/{game_id}/story",
        "/preview-api/arena/live?league=nhl&date={yyyymmdd}&home=FLA&away=DET",
        "/api/v1/command-center/ledger",
        "/api/v1/house-divided?sport=ALL",
    ],
    "game-night": [
        "/api/v1/sports/registry",
        "/api/v1/public/config",
        "/api/v1/mission-control?date={today}",
        "/preview-api/vibe?league=nfl",
        "/api/v1/availability?date={today}",
        "/api/v1/availability?date={tomorrow}",
        "/api/v1/command-center/markets?date={yesterday}",
        "/api/v1/command-center/markets?date={today}",
        "/api/v1/command-center/markets?date={tomorrow}",
        "/api/v1/command-center/decisions?date={yesterday}",
        "/api/v1/command-center/decisions?date={today}",
        "/api/v1/command-center/decisions?date={tomorrow}",
        "/api/v1/command-center/ledger",
        "/api/v1/house-divided?sport=ALL",
        "/preview-api/logos/cfb",
        "/api/v1/scoreboard?game_ids={game_id}",
        "/preview-api/scores?league=nba&date={yyyymmdd}",
        "/preview-api/scores?league=nfl&date={yyyymmdd}",
        "/preview-api/scores?league=nhl&date={yyyymmdd}",
    ],
    "underdogs": [
        "/api/v1/sports/registry",
        "/api/v1/public/config",
        "/api/v1/mission-control?date={today}",
        "/preview-api/insights/underdogs",
    ],
}


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


async def visitor(client: httpx.AsyncClient, pages: dict[str, list[str]], subs: dict[str, str], stop: float, stats: dict, think: tuple[float, float]) -> None:
    names = list(pages)
    while time.monotonic() < stop:
        page = random.choice(names)
        urls = [u.format(**subs) for u in pages[page]]

        async def one(url: str) -> None:
            key = url.split("?")[0]
            t0 = time.monotonic()
            try:
                r = await client.get(url)
                dt = time.monotonic() - t0
                stats["lat"][key].append(dt)
                stats["status"][r.status_code] += 1
                if r.status_code >= 500 and stats["first_error"] is None:
                    stats["first_error"] = (key, r.status_code, r.text[:200])
            except Exception as exc:  # noqa: BLE001 - this is what we came to find
                stats["lat"][key].append(time.monotonic() - t0)
                stats["status"]["EXC"] += 1
                if stats["first_error"] is None:
                    stats["first_error"] = (key, "EXC", repr(exc)[:200])

        await asyncio.gather(*(one(u) for u in urls))
        stats["pages"][page] += 1
        await asyncio.sleep(random.uniform(*think))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--users", type=int, default=200)
    ap.add_argument("--seconds", type=int, default=90)
    ap.add_argument("--ramp", type=float, default=15.0, help="seconds over which the visitors arrive")
    ap.add_argument("--think", default="15,30", help="min,max seconds a visitor reads a page")
    ap.add_argument("--pages", type=Path, default=None)
    ap.add_argument("--game-id", default="")
    ap.add_argument("--today", default="")
    args = ap.parse_args()
    pages = json.loads(args.pages.read_text()) if args.pages else DEFAULT_PAGES
    today = args.today or time.strftime("%Y-%m-%d")
    from datetime import date, timedelta

    d = date.fromisoformat(today)
    subs = {
        "today": today, "yyyymmdd": today.replace("-", ""),
        "yesterday": (d - timedelta(days=1)).isoformat(), "tomorrow": (d + timedelta(days=1)).isoformat(),
        "game_id": args.game_id or "00000000-0000-0000-0000-000000000000",
    }
    think = tuple(float(x) for x in args.think.split(","))
    stats = {"lat": defaultdict(list), "status": defaultdict(int), "pages": defaultdict(int), "first_error": None}
    limits = httpx.Limits(max_connections=args.users + 10, max_keepalive_connections=args.users)
    t_start = time.monotonic()
    stop = t_start + args.seconds
    async with httpx.AsyncClient(base_url=args.base, timeout=30.0, limits=limits) as client:
        tasks = []
        for i in range(args.users):
            await asyncio.sleep(args.ramp / args.users)
            tasks.append(asyncio.create_task(visitor(client, pages, subs, stop, stats, think)))
        await asyncio.gather(*tasks)
    elapsed = time.monotonic() - t_start
    total = sum(len(v) for v in stats["lat"].values())
    print(f"base={args.base} users={args.users} seconds={args.seconds} think={think}")
    print(f"requests={total} rps={total / elapsed:.1f} pages={dict(stats['pages'])} status={dict(stats['status'])}")
    all_lat = [x for v in stats["lat"].values() for x in v]
    print(f"ALL p50={percentile(all_lat, 50) * 1000:.0f}ms p95={percentile(all_lat, 95) * 1000:.0f}ms p99={percentile(all_lat, 99) * 1000:.0f}ms max={max(all_lat or [0]) * 1000:.0f}ms")
    for key, v in sorted(stats["lat"].items(), key=lambda kv: -percentile(kv[1], 95)):
        print(f"  {key:58s} n={len(v):5d} p50={statistics.median(v) * 1000:6.0f}ms p95={percentile(v, 95) * 1000:6.0f}ms max={max(v) * 1000:6.0f}ms")
    if stats["first_error"]:
        print("FIRST ERROR:", stats["first_error"])
    bad = stats["status"].get("EXC", 0) + sum(n for code, n in stats["status"].items() if isinstance(code, int) and code >= 500)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
