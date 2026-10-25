"""Read-only market insights for the test-mode preview: Beat the Close, Market Movers and the Model Lab.

Every query runs inside SET TRANSACTION READ ONLY and is rolled back. Nothing is written; no provider is called.
Prices come from AISP's own odds_snapshots (moneyline), decisions from shadow_experiment_decisions, positions from
official_asp_positions, finals from games. Results are cached in memory for a short time.
"""
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

SERVER = r"C:\Users\james\ai-sports-predictor\server"
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

_cache: dict = {}
SPORT = {"BASEBALL_MLB": "MLB", "MLB": "MLB", "NFL": "NFL", "CFB": "CFB", "NBA": "NBA", "NHL": "NHL"}


def _rows(sql: str, params: dict | None = None):
    from sqlalchemy import text
    from app.db.session import SessionLocal
    s = SessionLocal()
    try:
        s.execute(text("SET TRANSACTION READ ONLY"))
        return [dict(r._mapping) for r in s.execute(text(sql), params or {})]
    finally:
        s.rollback()
        s.close()


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def _iso(v):
    return v.astimezone(timezone.utc).isoformat() if isinstance(v, datetime) else v


def _league_of_game_sql():
    return "(select l.code from leagues l where l.id = g.league_id)"


def _closing_lines(days: int):
    """Last moneyline price per (game, book, side) at or before first pitch / kickoff, games in the last `days`."""
    return _rows(f"""
      with ml as (
        select m.game_id, mo.id as mo_id, split_part(mo.outcome_key, ':', 1) as side
        from markets m join market_outcomes mo on mo.market_id = m.id
        where m.market_type = 'MONEYLINE' and split_part(mo.outcome_key, ':', 1) in ('HOME', 'AWAY'))
      select distinct on (s.sportsbook_id, s.market_outcome_id)
             ml.game_id, ml.side, s.sportsbook_id, b.name as book, s.decimal_odds, s.american_odds, s.observed_at
      from odds_snapshots s
      join ml on ml.mo_id = s.market_outcome_id
      join games g on g.id = ml.game_id
      join sportsbooks b on b.id = s.sportsbook_id
      where g.starts_at > now() - make_interval(days => :days) and s.observed_at <= g.starts_at and s.decimal_odds > 1
      order by s.sportsbook_id, s.market_outcome_id, s.observed_at desc
    """, {"days": days})


def _consensus(lines):
    """Per game: median no-vig home probability across books that quoted both sides at the close, plus per-book prices."""
    by = {}
    for r in lines:
        g = by.setdefault(r["game_id"], {}).setdefault(r["book"], {})
        g[r["side"]] = r
    out = {}
    for gid, books in by.items():
        nv = []
        for book, sides in books.items():
            if "HOME" in sides and "AWAY" in sides:
                ih, ia = 1 / float(sides["HOME"]["decimal_odds"]), 1 / float(sides["AWAY"]["decimal_odds"])
                nv.append(ih / (ih + ia))
        last = max((r["observed_at"] for sides in books.values() for r in sides.values()), default=None)
        out[gid] = {"home": statistics.median(nv) if nv else None, "books": len(nv), "prices": books, "last_seen": last}
    return out


def beat_the_close(days: int = 75):
    def run():
        lines = _closing_lines(days)
        cons = _consensus(lines)
        pos = _rows(f"""
          select p.id, p.placed_at, p.stake, p.decimal_odds, p.result, p.status, p.closing_decimal_odds,
                 g.id as game_id, g.starts_at, {_league_of_game_sql()} as league,
                 split_part(mo.outcome_key, ':', 1) as side, b.name as book, d.selection, d.sport
          from official_asp_positions p
          join market_outcomes mo on mo.id = p.market_outcome_id
          join markets m on m.id = mo.market_id
          join games g on g.id = m.game_id
          left join odds_snapshots s on s.id = p.odds_snapshot_id
          left join sportsbooks b on b.id = s.sportsbook_id
          left join shadow_experiment_decisions d on d.id = p.shadow_decision_id
          where m.market_type = 'MONEYLINE' and g.starts_at > now() - make_interval(days => :days)
          order by g.starts_at
        """, {"days": days})
        out = []
        for p in pos:
            c = cons.get(p["game_id"])
            if not c or c["home"] is None or p["side"] not in ("HOME", "AWAY"):
                continue
            fair = c["home"] if p["side"] == "HOME" else 1 - c["home"]
            entry = float(p["decimal_odds"])
            same = (c["prices"].get(p["book"] or "", {}) or {}).get(p["side"])
            started = p["starts_at"] <= datetime.now(timezone.utc)
            out.append({
                "id": str(p["id"]), "sport": SPORT.get(p["sport"] or "", p["league"] or p["sport"]), "selection": p["selection"], "book": p["book"],
                "starts_at": _iso(p["starts_at"]), "placed_at": _iso(p["placed_at"]), "stake": float(p["stake"] or 0), "result": p["result"], "status": p["status"],
                "entry_decimal": entry, "close_fair_prob": fair, "close_fair_decimal": 1 / fair if fair else None,
                "clv_pct": (entry * fair - 1) * 100, "same_book_close_decimal": float(same["decimal_odds"]) if same else None,
                "beat_same_book": (entry > float(same["decimal_odds"])) if same and same["observed_at"] > p["placed_at"] else None, "close_books": c["books"], "final_close": started,
                "close_after_bet": bool(c["last_seen"] and c["last_seen"] > p["placed_at"]), "close_minutes_before_start": round((p["starts_at"] - c["last_seen"]).total_seconds() / 60, 1) if c["last_seen"] else None,
            })
        return {"positions": out, "days": days, "method": "CLV = entry decimal x closing no-vig consensus probability - 1 (median across books' last moneyline captured at or before the start); only bets with a price captured after the bet count as graded"}
    return _cached(f"btc{days}", 300, run)


def movers(hours_back: int = 12, hours_ahead: int = 30):
    """Consensus no-vig home probability through time for upcoming/in-progress games, and the moves that look like steam."""
    def run():
        snaps = _rows(f"""
          with ml as (
            select m.game_id, mo.id as mo_id, split_part(mo.outcome_key, ':', 1) as side
            from markets m join market_outcomes mo on mo.market_id = m.id
            where m.market_type = 'MONEYLINE' and split_part(mo.outcome_key, ':', 1) in ('HOME', 'AWAY'))
          select ml.game_id, ml.side, s.sportsbook_id, s.decimal_odds, s.observed_at, g.starts_at, {_league_of_game_sql()} as league
          from odds_snapshots s join ml on ml.mo_id = s.market_outcome_id join games g on g.id = ml.game_id
          where g.starts_at between now() - make_interval(hours => 22) and now() + make_interval(hours => :ahead)
            and s.observed_at > g.starts_at - make_interval(hours => :back) and s.observed_at <= least(now(), g.starts_at) and s.decimal_odds > 1
          order by s.observed_at
        """, {"back": hours_back, "ahead": hours_ahead})
        games = {}
        for r in snaps:
            g = games.setdefault(r["game_id"], {"starts_at": r["starts_at"], "league": r["league"], "events": []})
            g["events"].append(r)
        out = []
        for gid, g in games.items():
            state = {}  # book -> {HOME: dec, AWAY: dec}
            series = []
            for r in g["events"]:
                state.setdefault(r["sportsbook_id"], {})[r["side"]] = float(r["decimal_odds"])
                nv = [(1 / v["HOME"]) / (1 / v["HOME"] + 1 / v["AWAY"]) for v in state.values() if "HOME" in v and "AWAY" in v]
                if nv:
                    t = r["observed_at"]
                    if series and (t - series[-1]["t"]).total_seconds() < 300:
                        series[-1] = {"t": t, "home": statistics.median(nv), "books": len(nv), "per_book": {k: (1 / v["HOME"]) / (1 / v["HOME"] + 1 / v["AWAY"]) for k, v in state.items() if "HOME" in v and "AWAY" in v}}
                    else:
                        series.append({"t": t, "home": statistics.median(nv), "books": len(nv), "per_book": {k: (1 / v["HOME"]) / (1 / v["HOME"] + 1 / v["AWAY"]) for k, v in state.items() if "HOME" in v and "AWAY" in v}})
            if len(series) < 2:
                continue
            # steam: the consensus moves >= 2 points within 2 hours and most books that quoted both times moved the same way
            steam = []
            for i, b in enumerate(series):
                for a in series[:i]:
                    if (b["t"] - a["t"]).total_seconds() > 7200 or abs(b["home"] - a["home"]) < 0.02:
                        continue
                    common = [k for k in b["per_book"] if k in a["per_book"]]
                    if len(common) < 3:
                        continue
                    sign = 1 if b["home"] > a["home"] else -1
                    agree = sum(1 for k in common if (b["per_book"][k] - a["per_book"][k]) * sign > 0.004)
                    if agree / len(common) >= 0.6:
                        steam.append({"from": _iso(a["t"]), "to": _iso(b["t"]), "move": b["home"] - a["home"], "books_agree": agree, "books": len(common)})
                        break
            # keep the biggest non-overlapping steam moves
            steam.sort(key=lambda s: -abs(s["move"]))
            kept = []
            for s in steam:
                if all(s["to"] < k["from"] or s["from"] > k["to"] for k in kept):
                    kept.append(s)
            first, last = series[0], series[-1]
            out.append({"game_id": str(gid), "league": g["league"], "starts_at": _iso(g["starts_at"]), "open_home": first["home"], "now_home": last["home"],
                        "move": last["home"] - first["home"], "books": last["books"], "steam": kept[:3],
                        "series": [{"t": _iso(p["t"]), "home": round(p["home"], 4), "books": p["books"]} for p in series]})
        out.sort(key=lambda m: -abs(m["move"]))
        return {"games": out, "window": {"hours_back": hours_back, "hours_ahead": hours_ahead}, "generated_at": datetime.now(timezone.utc).isoformat()}
    return _cached(f"mov{hours_back}-{hours_ahead}", 120, run)


def lab(days: int = 75):
    """One row per decided game: AISP model home probability, closing no-vig market home probability, and who won."""
    def run():
        lines = _closing_lines(days)
        cons = _consensus(lines)
        dec = _rows("""
          select distinct on (d.game_id) d.game_id, d.sport, d.phase, d.model_probability, d.recommended_at,
                 split_part(mo.outcome_key, ':', 1) as side, g.home_score, g.away_score, g.starts_at, g.status
          from shadow_experiment_decisions d
          join market_outcomes mo on mo.id = d.market_outcome_id
          join games g on g.id = d.game_id
          where d.market = 'MONEYLINE' and d.model_probability is not null and g.starts_at > now() - make_interval(days => :days)
            and g.home_score is not null and g.away_score is not null and g.home_score <> g.away_score and g.starts_at < now() - interval '3 hours'
          order by d.game_id, (d.phase = 'FINAL_PREGAME') desc, d.recommended_at desc
        """, {"days": days})
        rows = []
        for d in dec:
            c = cons.get(d["game_id"])
            if not c or c["home"] is None or d["side"] not in ("HOME", "AWAY"):
                continue
            mp = float(d["model_probability"])
            rows.append({"sport": SPORT.get(d["sport"], d["sport"]), "date": d["starts_at"].astimezone(timezone(timedelta(hours=-4))).date().isoformat(),
                         "model_home": mp if d["side"] == "HOME" else 1 - mp, "market_home": c["home"], "home_won": d["home_score"] > d["away_score"],
                         "close_home_dec": max((float(v["HOME"]["decimal_odds"]) for v in c["prices"].values() if "HOME" in v), default=None),
                         "close_away_dec": max((float(v["AWAY"]["decimal_odds"]) for v in c["prices"].values() if "AWAY" in v), default=None)})
        rows.sort(key=lambda r: r["date"])
        return {"rows": rows, "days": days, "method": "AISP model probability from the latest FINAL_PREGAME (else latest) moneyline decision per game; market = closing no-vig consensus; best closing price across books"}
    return _cached(f"lab{days}", 600, run)


def closes(days: int = 21):
    """Closing (or, before the start, latest) no-vig consensus per game, for grading any price against the close."""
    def run():
        cons = _consensus(_closing_lines(days))
        return {"games": {str(k): {"home": v["home"], "books": v["books"]} for k, v in cons.items() if v["home"] is not None}}
    return _cached(f"cl{days}", 180, run)


if __name__ == "__main__":
    t = time.time(); b = beat_the_close(); print("btc", len(b["positions"]), round(time.time() - t, 1), "s", b["positions"][:2])
    t = time.time(); m = movers(); print("movers", len(m["games"]), round(time.time() - t, 1), "s", [(g["league"], round(g["move"], 3), len(g["steam"])) for g in m["games"][:5]])
    t = time.time(); l = lab(); print("lab", len(l["rows"]), round(time.time() - t, 1), "s", l["rows"][:2])
