"""UFC historical archive for the test-mode MMA page (read-only, local files only, no network).

Joins Codex's hash-verified historical handoff (MMA_HISTORICAL_RESEARCH_HANDOFF_V1: 1,504 released bouts,
2023-01-14..2025-12-06, HGB + Elo probabilities, explicit historical outcomes lane) with the exact-ID display
overlay and the retained raw source table (method, round, time, fight statistics, venue, fighter profiles).
Every probability is a RECONSTRUCTED historical research estimate: the Gen1 model status is NO_MODEL_SELECTED.

ORIENTATION FIX (Claude, 2026-10-25): in raw Fights.csv, Fighter_Id_1 is always the winner while the name, result and
per-side statistic columns are loser-first in about 35% of rows. The handoff's outcomes (and Gen1's labels) paired
Result_k with Fighter_Id_k, so 660 of its 1,504 winners are wrong. Here the winner of a decisive bout is Fighter_Id_1 and
each side's statistics are found by matching Fighter_k names to the fighter table. Gen1 probabilities are carried only
as INVALID (trained on flipped labels); `elo_fixed_a` is a transparent Elo on the corrected labels (K 24, scale 400,
1500 start, every fight rated only from strictly earlier dates).
"""
import csv
import importlib.util
import json
import pathlib

HANDOFF = pathlib.Path(r"C:\Users\james\asp-model-studio\outputs\mma-historical-product-handoff-20261009-v1")
OVERLAY = HANDOFF / "display-identities-v1" / "DISPLAY_IDENTITIES.json"
RAW = pathlib.Path(r"C:\Users\james\asp-data-staging\mma\ufc-gen1-cc0-v1\raw-v87\data")
_cache = None


def _f(v):
    try:
        return None if v in (None, "", "nan") else float(v)
    except ValueError:
        return None


def _i(v):
    x = _f(v)
    return None if x is None else int(x)


def _loader():
    spec = importlib.util.spec_from_file_location("mma_historical_handoff", HANDOFF / "read_handoff.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_handoff(HANDOFF, include_outcomes=True)  # explicit historical-results view


def _short_method(m: str) -> str:
    m = (m or "").upper()
    if m.startswith("KO") or "TKO" in m:
        return "KO/TKO"
    if m.startswith("SUB"):
        return "SUB"
    if "DEC" in m:
        return "DEC"
    if "DQ" in m:
        return "DQ"
    return m or "—"


def build():
    global _cache
    if _cache is not None:
        return _cache
    data = _loader()
    overlay = json.loads(OVERLAY.read_text(encoding="utf-8"))
    allowed = set(overlay["allowed_released_bout_ids"])
    fname = {r["source_fighter_id"]: r["display_name"] for r in overlay["fighters"] if r.get("join_status") == "EXACT_ID_MATCH"}
    ename = {r["source_event_id"]: r["display_name"] for r in overlay["events"] if r.get("join_status") == "EXACT_ID_MATCH"}
    raw_fights = list(csv.DictReader(open(RAW / "Fights.csv", encoding="utf-8")))
    order = {}
    by_fight = {}
    for idx, r in enumerate(raw_fights):
        by_fight[r["Fight_Id"]] = r
        order.setdefault(r["Event_Id"], []).append(r["Fight_Id"])
    venue = {r["Event_Id"]: r.get("Location") for r in csv.DictReader(open(RAW / "Events.csv", encoding="utf-8"))}
    prof = {r["Fighter_Id"]: r for r in csv.DictReader(open(RAW / "Fighters.csv", encoding="utf-8"))}
    style = {r["Fighter_Id"]: r for r in csv.DictReader(open(RAW / "Fighters Stats.csv", encoding="utf-8"))}
    fc = {r["source_fight_id"]: r for r in data["forecasts"]["records"]}
    oc = {r["source_fight_id"]: r for r in data["outcomes"]["records"]}

    names_by_id = {k: v.get("Full Name") for k, v in prof.items()}

    def slot_of(r, fighter_id):
        """The raw column suffix holding this fighter's name/result/stats (names follow results, not IDs)."""
        nm = names_by_id.get(fighter_id)
        if nm and r.get("Fighter_1") == nm:
            return "1"
        if nm and r.get("Fighter_2") == nm:
            return "2"
        return "1" if r.get("Fighter_Id_1") == fighter_id else "2"

    # transparent corrected-label Elo over the full raw history, rated date by date
    ev_date = {r["Event_Id"]: r.get("Date") for r in csv.DictReader(open(RAW / "Events.csv", encoding="utf-8"))}
    rating, elo_pre = {}, {}
    by_date = {}
    for r in raw_fights:
        d = ev_date.get(r["Event_Id"])
        if d:
            by_date.setdefault(d, []).append(r)
    for d in sorted(by_date):
        snap = dict(rating)
        for r in by_date[d]:
            i1, i2 = r["Fighter_Id_1"], r["Fighter_Id_2"]
            r1, r2 = snap.get(i1, 1500.0), snap.get(i2, 1500.0)
            elo_pre[r["Fight_Id"]] = {i1: r1, i2: r2}
        for r in by_date[d]:
            res = {r.get("Result_1"), r.get("Result_2")}
            if res != {"W", "L"}:
                continue  # draws / no contests leave ratings unchanged
            w, l = r["Fighter_Id_1"], r["Fighter_Id_2"]
            rw, rl = snap.get(w, 1500.0), snap.get(l, 1500.0)
            ew = 1 / (1 + 10 ** ((rl - rw) / 400))
            rating[w] = rating.get(w, 1500.0) + 24 * (1 - ew)
            rating[l] = rating.get(l, 1500.0) - 24 * (1 - ew)

    def side_stats(r, n):
        g = lambda k: r.get(f"{k}_{n}")
        return {"kd": _i(g("KD")), "str": _i(g("STR")), "td": _i(g("TD")), "sub": _i(g("SUB")), "ctrl": _i(g("Ctrl")),
                "sig_pct": _f(g("Sig. Str. %")), "head": _f(g("Head_%")), "body": _f(g("Body_%")), "leg": _f(g("Leg_%")),
                "distance": _f(g("Distance_%")), "clinch": _f(g("Clinch_%")), "ground": _f(g("Ground_%"))}

    bouts, fighters_used = {}, set()
    for f in data["fights"]["records"]:
        fid = f["source_fight_id"]
        if fid not in allowed:
            continue
        r = by_fight.get(fid, {})
        a, b = f["source_fighter_a_id"], f["source_fighter_b_id"]
        n_a = slot_of(r, a)
        n_b = slot_of(r, b) if slot_of(r, b) != n_a else ("2" if n_a == "1" else "1")
        fo, ou = fc.get(fid, {}), oc.get(fid, {})
        winner = None
        if {r.get("Result_1"), r.get("Result_2")} == {"W", "L"}:
            winner = "A" if r.get("Fighter_Id_1") == a else "B"
        handoff_winner = "A" if ou.get("winner_source_fighter_id") == a else "B" if ou.get("winner_source_fighter_id") == b else None
        pre = elo_pre.get(fid, {})
        ra, rb = pre.get(a, 1500.0), pre.get(b, 1500.0)
        ev = f["source_event_id"]
        seq = order.get(ev, [])
        bouts[fid] = {
            "id": fid, "event": ev, "date": f["event_date"], "order": seq.index(fid) if fid in seq else 99,
            "weight_class": f.get("weight_class") or r.get("Weight_Class") or None, "split": f["original_split_role"],
            "a": a, "b": b, "gen1_hgb_a_invalid": fo.get("hgb_probability_a_win"), "gen1_elo_a_invalid": fo.get("elo_probability_a_win"),
            "elo_fixed_a": 1 / (1 + 10 ** ((rb - ra) / 400)), "elo_rating_a": round(ra), "elo_rating_b": round(rb),
            "winner": winner, "handoff_winner_flipped": handoff_winner is not None and winner is not None and handoff_winner != winner, "method": r.get("Method") or None, "method_short": _short_method(r.get("Method")),
            "round": _i(r.get("Round")), "time": r.get("Fight_Time") or None, "format": r.get("Time Format") or None,
            "referee": r.get("Referee") or None, "stats": {"a": side_stats(r, n_a), "b": side_stats(r, n_b)},
        }
        fighters_used.update((a, b))
    fighters = {}
    for fid in fighters_used:
        p, s = prof.get(fid, {}), style.get(fid, {})
        nick = p.get("Nickname")
        fighters[fid] = {"id": fid, "name": fname.get(fid) or p.get("Full Name"), "nickname": None if nick in (None, "", "No Nickname") else nick,
                         "height": (lambda h: None if not h else (f"{h.split('.')[0]}'{h.split('.')[1]}\"" if "." in h else f"{h}'")) (p.get("Ht.")), "weight_lb": _f(p.get("Wt.")), "reach_in": _f(p.get("Reach")), "stance": p.get("Stance") or None,
                         "w": _i(p.get("W")), "l": _i(p.get("L")), "d": _i(p.get("D")), "belt": p.get("Belt") == "True",
                         "style": s.get("Fighting Style") or None, "ko_rate": _f(s.get("KO Rate")), "sub_rate": _f(s.get("SUB Rate")), "dec_rate": _f(s.get("DEC Rate"))}
    events = []
    for e in data["events"]["records"]:
        ids = sorted([k for k, v in bouts.items() if v["event"] == e["source_event_id"]], key=lambda k: bouts[k]["order"])
        if ids:
            events.append({"id": e["source_event_id"], "name": ename.get(e["source_event_id"]), "date": e["event_date"], "location": venue.get(e["source_event_id"]), "bouts": ids})
    events.sort(key=lambda e: e["date"], reverse=True)
    _cache = {
        "source": "MMA_HISTORICAL_RESEARCH_HANDOFF_V1 + display overlay + retained raw source (Kaggle UFC v87, internal research)",
        "model_status": data["modelStatus"], "performance": data["performance"],
        "coverage": {"bouts": len(bouts), "events": len(events), "fighters": len(fighters), "first": min(b["date"] for b in bouts.values()), "last": max(b["date"] for b in bouts.values())},
        "events": events, "bouts": bouts, "fighters": fighters,
        "orientation_fix": {"handoff_winners_flipped": sum(1 for b in bouts.values() if b["handoff_winner_flipped"]), "rule": "decisive winner = Fighter_Id_1; side stats by name match"},
        "notes": ["Winners are corrected: the handoff's outcomes and Gen1's training labels flipped about a third of bouts (see orientation_fix).",
                  "The Elo % is a transparent corrected-label baseline rated only from earlier fights; it was not published before these fights and is not an AISP model.",
                  "Career records and profiles are the dataset's current snapshot, not as known on fight night.",
                  "No current or upcoming card, no odds and no wagers: Gen1 is NO_MODEL_SELECTED."],
    }
    return _cache


if __name__ == "__main__":
    out = build()
    print(json.dumps(out["coverage"]), len(json.dumps(out)) // 1024, "KB")
    ev = out["events"][0]
    print(ev["name"], ev["date"], ev["location"], len(ev["bouts"]))
    b = out["bouts"][ev["bouts"][0]]
    print(out["orientation_fix"], {k: b[k] for k in ("a", "b", "elo_fixed_a", "winner", "handoff_winner_flipped", "method", "round", "time")}, out["fighters"][b["a"]]["name"], out["fighters"][b["b"]]["name"])
