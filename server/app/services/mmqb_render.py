"""Render one MMQB payload (``mmqb_report.assemble``) as HTML, BRIEF.md, CSV/JSON and a show script.

Every renderer reads the same payload; none queries the database or recomputes a number. The data
cutoff and snapshot identity lead every output.
"""
from __future__ import annotations

import csv
import html
import io
import json
from typing import Any

from app.services.mmqb_report import et_label, zulu

E = html.escape


# ----------------------------------------------------------------- format ---

def money(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "—"
    return f"{'+' if signed and v > 0 else '-' if v < 0 else ''}${abs(v):,.2f}"


def pct(v: float | None, d: int = 1, signed: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.{d}f}%" if signed else f"{v * 100:.{d}f}%"


def num(v: float | None, d: int = 1, signed: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v:+.{d}f}" if signed else f"{v:.{d}f}"


def odds(v: Any) -> str:
    if v is None:
        return "—"
    v = int(v)
    return f"+{v}" if v > 0 else str(v)


def ci(v: list[float] | None, d: int = 3, as_pct: bool = False) -> str:
    if not v:
        return ""
    if as_pct:
        return f" [{v[0] * 100:+.1f}%, {v[1] * 100:+.1f}%]"
    return f" [{v[0]:+.{d}f}, {v[1]:+.{d}f}]"


def record(w: int, l_: int, p: int) -> str:
    return f"{w}-{l_}-{p}"


def final_text(s: dict[str, Any]) -> str:
    f = s.get("final")
    if not f:
        return f"{s.get('game_status') or 'PENDING'} (not final)"
    ot = " OT" if s.get("overtime") else ""
    return f"{s['away']} {f['away']} – {s['home']} {f['home']}{ot}"


def _yes(v: Any) -> str:
    return "—" if v is None else ("yes" if v else "no")


def _exact(v: float | None) -> str:
    if v is None:
        return "—"
    return "not among the stored scores (below 0.01%)" if v == 0 else pct(v, 2)


def short(uuid: str | None) -> str:
    return (uuid or "—")[:8]


def headline(p: dict[str, Any]) -> dict[str, Any]:
    s = p["financial"]["summary"]
    e = p["financial"]["expectation_settled"]
    return {
        "record": record(s["wins"], s["losses"], s["pushes"]),
        "settled": s["settled"], "n": s["n"], "stake": s["settled_stake"], "net": s["net_profit"], "roi": s["roi"],
        "roi_ci": s["roi_ci95"], "open_count": s["open_count"], "open_exposure": s["open_exposure"],
        "expected_wins": e.get("expected_wins"), "expected_pushes": e.get("expected_pushes"),
        "expected_profit": e.get("expected_profit"), "sd_profit": e.get("sd_profit"),
        "p_profit": e.get("p_profit_at_least_actual"), "z": e.get("z_profit"),
    }


def scorecard_line(p: dict[str, Any]) -> str:
    h = headline(p)
    line = (f"{h['record']} (W-L-P) on {h['settled']} settled of {h['n']} positions · net {money(h['net'], True)} on "
            f"{money(h['stake'])} staked · ROI {pct(h['roi'], 1, True)}{ci(h['roi_ci'], as_pct=True)}")
    if h["open_count"]:
        line += f" · {h['open_count']} open ({money(h['open_exposure'])} exposure)"
    groups = p.get("slate_groups") or []
    if len(groups) > 1:
        line += " · week total by slate: " + ", ".join(f"{g['day']} {g['n']}" for g in groups)
    return line


def expectation_line(p: dict[str, Any]) -> str:
    h = headline(p)
    if h["expected_wins"] is None:
        return "Expectation not computable (missing recorded probabilities)."
    return (f"Expected at entry on the same settled wagers: {h['expected_wins']:.1f} wins, {h['expected_pushes']:.2f} "
            f"pushes, profit {money(h['expected_profit'], True)} (SD {money(h['sd_profit'])} under the model's own "
            f"probabilities). Actual: {h['record'].split('-')[0]} wins, profit {money(h['net'], True)}"
            + (f" — {h['z']:+.2f} SD from expectation; P(profit ≥ actual) = {h['p_profit']:.2f}." if h["z"] is not None else ".")
            + f" SD, z and P: {p['uncertainty_label']}")


def identity(p: dict[str, Any]) -> dict[str, Any]:
    """The one identity every output carries: data cutoff, snapshot id, content hash, generator version."""

    snap = p["snapshot"]
    return {"data_cutoff_utc": p["data_cutoff"]["db_clock_utc"], "pg_snapshot": snap.get("pg_snapshot"),
            "content_sha256": snap.get("content_sha256"), "generator": snap.get("generator_commit") or "uncommitted"}


def identity_line(p: dict[str, Any]) -> str:
    i = identity(p)
    return (f"Data cutoff {zulu(i['data_cutoff_utc'])} ({i['data_cutoff_utc']}) · snapshot {i['pg_snapshot']} · "
            f"content sha256 {i['content_sha256']} · generator {i['generator']}")


def cutoff_block(p: dict[str, Any]) -> list[str]:
    snap = p["snapshot"]
    dc = p["data_cutoff"]
    return [
        f"Data cutoff: {zulu(dc['db_clock_utc'])} (database clock at the read-only snapshot)",
        identity_line(p) + f" · {snap.get('isolation') or '—'}, read_only={snap.get('read_only')} · generated "
        f"{zulu(snap.get('generated_at_utc'))}",
        f"Latest settlement {zulu(dc.get('latest_settlement_utc'))} · latest final observation "
        f"{zulu(dc.get('latest_final_observation_utc'))}",
    ]


# ------------------------------------------------------------------- HTML ---

CSS = """
:root{--bg:#f7f7f4;--panel:#ffffff;--ink:#1b1d21;--muted:#5d6470;--line:#dcdfe4;--accent:#1f5fbf;--win:#177245;
--loss:#b3261e;--push:#7a5b00;--warn-bg:#fff4d6;--warn-ink:#5c4400;--chip:#eef1f5;--flag-bg:#fde8e6;--pass:#1d6b3a}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#111317;--panel:#1a1d23;--ink:#e8eaee;
--muted:#a0a7b3;--line:#2e333c;--accent:#7fb0ff;--win:#5fd08e;--loss:#ff8a80;--push:#e6c35c;--warn-bg:#3a3014;
--warn-ink:#f3dc9a;--chip:#252a32;--flag-bg:#3d1f1c;--pass:#6fd39a}}
:root[data-theme="dark"]{--bg:#111317;--panel:#1a1d23;--ink:#e8eaee;--muted:#a0a7b3;--line:#2e333c;--accent:#7fb0ff;
--win:#5fd08e;--loss:#ff8a80;--push:#e6c35c;--warn-bg:#3a3014;--warn-ink:#f3dc9a;--chip:#252a32;--flag-bg:#3d1f1c;--pass:#6fd39a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.7rem;margin:.2rem 0}h2{font-size:1.25rem;margin:2rem 0 .6rem;border-bottom:2px solid var(--line);padding-bottom:.3rem}
h3{font-size:1.05rem;margin:1.2rem 0 .4rem}
p{margin:.4rem 0}.muted{color:var(--muted)}.small{font-size:.85rem}
p,li,dd,dt,h1,h2,h3,h4,summary,.box{overflow-wrap:anywhere}code{word-break:break-all}
a{color:var(--accent)}
.cutoff{background:var(--warn-bg);color:var(--warn-ink);border-radius:8px;padding:10px 14px;margin:12px 0;font-size:.9rem;overflow-wrap:anywhere}
.cutoff strong{font-size:1rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.tile b{display:block;font-size:1.35rem}.tile span{color:var(--muted);font-size:.8rem}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:.84rem}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}
th{background:var(--chip);font-weight:600;position:sticky;top:0}
td.wrap,th.wrap{white-space:normal;min-width:180px}
.num{text-align:right;font-variant-numeric:tabular-nums}
.WIN{color:var(--win);font-weight:600}.LOSS{color:var(--loss);font-weight:600}.PUSH{color:var(--push);font-weight:600}
.OPEN,.PENDING{color:var(--muted);font-weight:600}
.chip{display:inline-block;background:var(--chip);border-radius:10px;padding:0 7px;font-size:.75rem;margin-right:4px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:12px 0;padding:0}
.card>summary{cursor:pointer;padding:10px 14px;font-weight:600;list-style:none}
.card>summary::-webkit-details-marker{display:none}
.card .body{padding:0 14px 12px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.box{border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.box h4{margin:0 0 .3rem;font-size:.8rem;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}
ul.checks{list-style:none;padding:0;margin:0}ul.checks li{padding:3px 0;border-bottom:1px dashed var(--line);font-size:.85rem}
.st{font-weight:700;font-size:.72rem;border-radius:4px;padding:0 5px;margin-right:6px}
.st-PASS{color:var(--pass)}.st-FLAG{background:var(--flag-bg);color:var(--loss)}.st-INFO{color:var(--muted)}
.st-PENDING{color:var(--push)}.st-NOT_CHECKABLE{color:var(--muted)}
.finding{background:var(--panel);border-left:4px solid var(--accent);border-radius:6px;padding:10px 14px;margin:10px 0}
.caveat{border:1px dashed var(--line);border-radius:8px;padding:10px 14px}
footer{margin-top:2.5rem;font-size:.82rem;color:var(--muted)}
.pos-list{border:1px solid var(--line);border-radius:8px;background:var(--panel)}
.pos-head,.pos>summary{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,.9fr) minmax(0,1fr) minmax(0,.9fr) minmax(0,.55fr) minmax(0,.9fr);gap:6px;align-items:start;padding:7px 10px}
.pos-head{font-size:.75rem;font-weight:600;color:var(--muted);background:var(--chip);border-radius:8px 8px 0 0}
.pos{border-top:1px solid var(--line)}
.pos>summary{cursor:pointer;list-style:none;font-size:.88rem;font-variant-numeric:tabular-nums}
.pos>summary::-webkit-details-marker{display:none}
.pos>summary span{overflow-wrap:anywhere;min-width:0}
.pos>summary small{display:block;color:var(--muted);font-size:.72rem}
.pos .r{text-align:right}
.pos[open]>summary{background:var(--chip)}
dl.prov{margin:0;padding:6px 10px 2px;display:grid;gap:4px;font-size:.8rem}
dl.prov div{display:grid;grid-template-columns:minmax(0,9rem) minmax(0,1fr);gap:8px}
dl.prov dt{color:var(--muted)}dl.prov dd{margin:0;overflow-wrap:anywhere}
.pos p.small{padding:0 10px 8px}
.banner{background:var(--flag-bg);color:var(--loss);border:1px solid var(--loss);border-radius:8px;padding:10px 14px;margin:10px 0}
.dive{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:14px 0}
.dive h3{margin-top:0}
.tag{display:inline-block;font-size:.7rem;font-weight:700;letter-spacing:.05em;border-radius:4px;padding:1px 6px;margin-right:6px}
.tag-before{background:var(--chip)}.tag-obs{background:#dff3e6;color:#14532d}.tag-hyp{background:#fdf0d5;color:#6b4a00}
ol.seq{margin:.2rem 0;padding-left:1.2rem;font-size:.82rem;columns:2 16rem}
@media (max-width:600px){dl.prov div{grid-template-columns:1fr;gap:0}.pos-head,.pos>summary{gap:4px;padding:7px 8px;font-size:.8rem}}
@media (max-width:600px){body{font-size:14px}h1{font-size:1.35rem}.tile b{font-size:1.15rem}}
"""


def _table(headers: list[tuple[str, str]], rows: list[list[str]]) -> str:
    head = "".join(f'<th class="{cls}">{E(h)}</th>' for h, cls in headers)
    body = "".join("<tr>" + "".join(f'<td class="{headers[i][1]}">{c}</td>' for i, c in enumerate(r)) + "</tr>"
                   for r in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _game_link(s: dict[str, Any], base: str) -> str:
    return f"{base}/games/{s['game_id']}?sport=NFL"


def _provenance(s: dict[str, Any], base: str, target: str) -> str:
    lq = s.get("last_observed_quote") or {}
    q = lq.get("quote")
    items = [
        ("Model's predicted winner", f"{s['model_favourite_team'] or '—'} {pct(s['model_favourite_p_two_way'], 0)} (two-way)"
         + ("" if s["selected_is_model_favourite"] in (None, True) else " — the selection is the value side, not the pick")),
        ("Probabilities (decision)", f"pWin {pct(s['p_win'])} · pTie {pct(s['p_tie'])} · pLoss {pct(s['p_loss'])} · "
                                     f"two-way {pct(s['p_win_two_way'])}"),
        ("Forecast", f"{s['forecast_snapshot'] or '—'} ({s['forecast_type'] or '—'}), available {zulu(s['forecast_available_utc'])}, "
                     f"{num(s['forecast_lead_hours'], 2)} h before kickoff, {num(s['forecast_age_at_decision_hours'], 2)} h "
                     f"old at the decision"),
        ("Decision", f"{zulu(s['decision_at_utc'])}, {num(s['decision_minutes_before_kickoff'], 1)} min before kickoff · "
                     f"id {s['decision_id'] or '—'} · binding {s['binding_status']}"),
        ("Entry", f"{s['sportsbook'] or '—'} {odds(s['entry_american'])} (other side {odds(s['opposite_american'])}), "
                  f"stake {money(s['stake'])}"),
        ("Quote clocks", f"book observed {zulu(s['quote_observed_at_utc'])} · received {zulu(s['quote_available_utc'])} · "
                         f"{num(s['quote_book_age_minutes'], 1)} min old on the book's clock at the decision"),
        ("Market", f"entry-pair no-vig {pct(s['no_vig_selected'])} · median of {s['market_median_books'] or 0} books "
                   f"{pct(s['market_median_selected'])} · model − market {num(s['model_minus_market_pts'], 1, True)} pts"),
        ("Recorded EV", f"{pct(s['recorded_ev'], 1, True)} per $1 (tie refunded) · expected profit {money(s['expected_profit'], True)}"),
        ("Later price", (f"LAST OBSERVED QUOTE {odds(q['american'])} at {zulu(q['observed_at_utc'])} — not a closing line"
                         if q else "LAST OBSERVED QUOTE: the entry quote (no later same-book quote retained); no closing line")),
        ("Position", f"id {s['position_id']} · settled {zulu(s['settled_at_utc'])}"),
    ]
    rows = "".join(f"<div><dt>{E(k)}</dt><dd>{E(v)}</dd></div>" for k, v in items)
    return (f'<dl class="prov">{rows}</dl><p class="small"><a href="{E(_game_link(s, base))}" target="{target}">'
            f"Game page</a></p>")


def slate_table(slate: list[dict[str, Any]], base: str, target: str) -> str:
    """Six columns that fit at 375 px; everything else in a native <details> row per position."""

    head = ('<div class="pos-head" aria-hidden="true"><span>Game</span><span>Our selection</span>'
            '<span>Odds · model</span><span>Final</span><span>W–L–P</span><span class="r">Net</span></div>')
    rows = []
    for s in slate:
        res = s["result"] or s["status"] or "—"
        short_res = {"WIN": "W", "LOSS": "L", "PUSH": "P"}.get(res, res.title())
        f = s.get("final")
        final = f"{f['away']}–{f['home']}" if f else "pending"
        rows.append(
            f'<details class="pos" data-position="{E(s["position_id"])}"><summary>'
            f'<span class="g">{E(s["matchup"])}<small>{E(s["kickoff_z"][11:])}</small></span>'
            f'<span><b>{E(s["selected_team"] or "—")}</b></span>'
            f'<span>{E(odds(s["entry_american"]))}<small>{pct(s["p_win"])}</small></span>'
            f'<span>{E(final)}</span>'
            f'<span class="{E(res)}">{E(short_res)}</span>'
            f'<span class="r">{E(money(s["net_profit"], True) if s["settled"] else "open")}</span>'
            f"</summary>{_provenance(s, base, target)}</details>")
    return f'<div class="pos-list">{head}{"".join(rows)}</div>'


def summary_table(groups: list[dict[str, Any]], label: str) -> str:
    headers = [(label, "wrap"), ("n", "num"), ("Settled W-L-P", ""), ("Stake", "num"), ("Net", "num"), ("ROI", "num"),
               ("Exp. wins (settled)", "num"), ("Exp. profit (settled)", "num"), ("Open", "num")]
    rows = []
    for g in groups:
        if not g.get("n"):
            continue
        e = g.get("expectation_settled") or {}
        rows.append([E(g["label"]), str(g["n"]), record(g["wins"], g["losses"], g["pushes"]), money(g["settled_stake"]),
                     money(g["net_profit"], True), pct(g["roi"], 1, True),
                     num(e.get("expected_wins"), 2), money(e.get("expected_profit"), True), str(g["open_count"])])
    return _table(headers, rows)


def band_table(bands: list[dict[str, Any]], label: str) -> str:
    headers = [(label, ""), ("n", "num"), ("Settled W-L-P", ""), ("Stake", "num"), ("Net", "num"), ("ROI", "num"),
               ("Open", "num")]
    rows = [[E(b["label"]), str(b["n"]), record(b["wins"], b["losses"], b["pushes"]), money(b["settled_stake"]),
             money(b["net_profit"], True), pct(b["roi"], 1, True), str(b["open_count"])] for b in bands if b["n"]]
    return _table(headers, rows)


def mvm_table(p: dict[str, Any]) -> str:
    mv = p["model_vs_market"]
    rows = []

    def add(label: str, block: dict[str, Any] | None, scope: str) -> None:
        if not block:
            rows.append([E(label), E(scope), "—", "—", "—", "—", "—", "—", "0"])
            return
        rows.append([E(label), E(scope), num(block.get("model_log_loss", block.get("model_log_loss_two_way")), 3),
                     num(block.get("market_log_loss", block.get("market_log_loss_two_way")), 3),
                     num(block["delta_log_loss"], 3, True) + E(ci(block.get("delta_log_loss_ci95"))),
                     num(block.get("model_brier", block.get("model_brier_two_way")), 3),
                     num(block.get("market_brier", block.get("market_brier_two_way")), 3),
                     num(block["delta_brier"], 3, True) + E(ci(block.get("delta_brier_ci95"))),
                     f"{block['n']} ({block['model_better_games']} model better)"])

    for label, key in (("Prior weeks", "prior_weeks_overview"), ("Season to date", "season_overview")):
        block = mv.get(key) or {}
        add(f"{label} · vs median of books (Overview)", block.get("market_decision_time"),
            "governing decisions, decision time")
        add(f"{label} · comparable time only (Overview)", block.get("market_comparable"),
            "forecast ≤ 6 h old at decision")
    headers = [("Comparison", "wrap"), ("Population", "wrap"), ("Model LL", "num"), ("Market LL", "num"),
               ("Δ LL [95% CI]", "num"), ("Model Brier", "num"), ("Market Brier", "num"), ("Δ Brier [95% CI]", "num"),
               ("n", "num")]
    return _table(headers, rows)


def week_mvm_html(p: dict[str, Any]) -> str:
    """The two week comparisons, visibly separate, each with its own n, interval and verdict."""

    mv = p["model_vs_market"]
    wk = mv["week"]
    out = []
    for title, block, verdict_key in (
            ("A · Entry book's own no-vig pair", wk["vs_decision_pair"], "verdict_pair"),
            ("B · Median of books (Overview's strict clock)", wk["vs_median_of_books"], "verdict_median")):
        if not block:
            out.append(f'<div class="box"><h4>{E(title)}</h4><p>Not computable.</p></div>')
            continue
        out.append(
            f'<div class="box"><h4>{E(title)}</h4>'
            f"<p><b>n = {block['n']}</b> decided games · two-way log loss model {block['model_log_loss']:.3f} vs market "
            f"{block['market_log_loss']:.3f} · Δ {block['delta_log_loss']:+.3f}{E(ci(block['delta_log_loss_ci95']))} · "
            f"Brier Δ {block['delta_brier']:+.3f}{E(ci(block['delta_brier_ci95']))} · model better in "
            f"{block['model_better_games']} of {block['n']}</p><p><b>{E(wk[verdict_key])}</b></p></div>")
    return (f'<div class="grid2">{"".join(out)}</div><p class="small"><b>Why the two n differ:</b> '
            f"{E(mv['explanations']['pair_vs_median'])}</p>")


def _seq_line(x: dict[str, Any]) -> str:
    who = x.get("player_as_recorded")
    yards = x.get("kick_distance_yards")
    detail = (f" {who}" if who else "") + (f" {yards:.0f} yd" if yards else "")
    return f"{x['clock']} {x['team']} {x['kind']}{detail} ({x['away']}–{x['home']})"


def dive_card(d: dict[str, Any], s: dict[str, Any]) -> str:
    b, o = d["before_kickoff"], d["observed"]
    res = d["result"] or "—"
    qb_rows = "".join(
        f"<li>{E(q['team'])}: depth-rank-one proxy {E(q['depth_rank_one_gsis_id'] or '—')} "
        f"({E(str(q.get('depth_proxy_prior_pass_attempts')))} prior pass attempts, {E(str(q.get('roster_years_exp')))} roster years); "
        f"{E(q.get('change_signal') or '—')}; confirmed starter {E(q.get('confirmed_starter_status') or '—')}; reported "
        f"availability {E(q.get('reported_availability_status') or '—')}</li>" for q in b["qb_sides"])
    cap = b.get("qb_capture") or {}
    before = f"""
<p><span class="tag tag-before">BEFORE KICKOFF</span>Forecast home {pct(b['p_home'])}, away {pct(b['p_away'])}, tie {pct(b['p_tie'])};
expected {E(s['away'] or '')} {num(b['expected_away'])} – {E(s['home'] or '')} {num(b['expected_home'])} (home margin
{num(b['expected_margin_home'], 1, True)}, total {num(b['expected_total'])}). Selected <b>{E(b['selected'] or '—')}</b>
{E(odds(b['entry_american']))} at {E(b['book'] or '—')}: pWin {pct(b['p_win'])} (two-way {pct(b['p_win_two_way'])}) vs
entry-pair no-vig {pct(b['no_vig_selected'])} ({num(b['model_minus_market_pts'], 1, True)} pts); recorded EV
{pct(b['recorded_ev'], 1, True)}.</p>
<p class="small">{E(b['forecast_inputs'])}</p>
{('<p class="small">QB research capture (cutoff ' + E(str(cap.get('forecast_cutoff_utc'))) + ', '
  + E(('admission granted ' + str(cap.get('admission_granted'))) if cap.get('admission_granted') is not None
      else ('funding-eligible ' + str(cap.get('funding_eligible')) + ', no admission recorded')) + ', sha256 ' + E((cap.get('sha256') or '')[:16]) + '…):</p><ul class="small">'
  + qb_rows + '</ul>') if qb_rows else '<p class="small">No QB capture retained for this game.</p>'}"""
    if o.get("play_by_play_status") == "FINAL_PLAY_BY_PLAY_RETAINED":
        src = o["source"]
        scoring = "".join(f"<li>{E(_seq_line(x))}</li>" for x in o["scoring_sequence"] if x["kind"] in ("TD", "FG", "SAFETY"))
        tos = ", ".join(f"{x['clock']} {x['lost_by']} {x['kind'].replace('_', ' ').lower()}" for x in o["turnovers"]) or "none"
        passers = "; ".join(f"{x['team']} {x['name_as_recorded']} ({x['gsis_id']}) {x['pass_attempts']} att"
                            for x in o["passers"])
        observed = f"""
<p><span class="tag tag-obs">OBSERVED</span>Final {E(s['away'] or '')} {o['final']['away']} – {E(s['home'] or '')} {o['final']['home']}:
home margin {o['margin_home']:+d} vs expected {num(o['expected_margin_home'], 1, True)} ({pct(o['margin_percentile'], 0)} percentile of
the forecast's margin distribution); total {o['total']} vs expected {num(o['expected_total'])} ({pct(o['total_percentile'], 0)}
percentile). Result {E(res)} {E(money(d['net_profit'], True))}.</p>
<p class="small">From the retained nflverse play-by-play ({src['rows_for_game']} rows for this game of {src['rows_total']};
retrieved {E(zulu(src['retrieved_at_utc']))}, archive-observed {E(zulu(src['archive_observed_at_utc']))}; sha256
{E((src['sha256'] or '')[:16])}… {'matches' if src['sha256_matches_vintage'] else 'DOES NOT match'} its vintage; {E(src['final_check'])})</p>
<p class="small"><b>Scoring plays (touchdowns, field goals, safeties):</b></p><ol class="seq">{scoring}</ol>
<p class="small"><b>Turnovers recorded:</b> {E(tos)} · by team {E(', '.join(f'{k} {v}' for k, v in o['turnover_counts'].items()))}
(recorded facts; no cause or forecast judgement is attached to them)</p>
<p class="small"><b>Pass attempts by passer:</b> {E(passers)}</p>"""
    elif o.get("play_by_play_status") == "GAME_EVIDENCE_UNAVAILABLE":
        observed = f"""
<div class="banner" role="alert"><b>{E(o['evidence_unavailable_reason'])}</b></div>
<p><span class="tag tag-obs">OBSERVED</span>Final {E(s['away'] or '')} {o['final'].get('away')} – {E(s['home'] or '')}
{o['final'].get('home')}; home margin {o.get('margin_home')} vs expected {num(o.get('expected_margin_home'), 1, True)}.
No in-game event is stated.</p>"""
    else:
        observed = f"""
<p><span class="tag tag-obs">OBSERVED</span>Final {E(s['away'] or '')} {o['final'].get('away')} – {E(s['home'] or '')}
{o['final'].get('home')}; home margin {o.get('margin_home')} vs expected {num(o.get('expected_margin_home'), 1, True)}.
Play-by-play: {E(o['play_by_play_status'])}; no in-game event is stated.</p>"""
    hyp = "".join(f"<li><b>{E(h['topic'])}.</b> {E(h['text'])} <span class=\"muted\">→ {E(h['link'])}</span></li>"
                  for h in d["hypotheses"])
    return (f'<section class="dive" id="dive-{E(d["key"].lower())}"><h3>{E(d["matchup"])} · selected '
            f'{E(d["selected_team"] or "—")} {E(odds(s["entry_american"]))} · <span class="{E(res)}">{E(res)}</span></h3>'
            f'<p class="small muted">Why this game: {E(d["reason"])}.</p>{before}{observed}'
            f'<p><span class="tag tag-hyp">HYPOTHESIS</span>to investigate, not conclusions:</p><ul class="small">{hyp}</ul></section>')


def evidence_banner(p: dict[str, Any]) -> str:
    st = p.get("game_evidence_status") or {}
    if st.get("status") == "VERIFIED":
        return (f'<p class="small">Game evidence {E(st.get("source", ""))}: {E(st.get("file", ""))} · file sha256 '
                f'{E(st.get("file_sha256", ""))} · source vintage {E(str(st.get("source_vintage_sha256")))} '
                f'({E(str(st.get("source_row_count")))} rows, retrieved {E(zulu(st.get("source_retrieved_at_utc")))}) · '
                f'VERIFIED.</p>')
    return (f'<div class="banner" role="alert" data-testid="evidence-banner"><b>Game evidence unavailable: '
            f'{E(st.get("reason") or "unknown")}</b> (status {E(st.get("status") or "MISSING")}). The play-by-play '
            f'sections below are withheld, not omitted.</div>')


def sources_html(p: dict[str, Any]) -> str:
    src = p.get("game_evidence_sources")
    if not src:
        return '<p class="small">No retained game-evidence file was supplied; the deep dives use database records only.</p>'
    rows = "".join(f"<li><b>{E(x.get('source', ''))}</b>: {E(x.get('finding', ''))}</li>" for x in src.get("searched") or [])
    return (f'<details class="card"><summary>Sources checked for game evidence (read-only; nothing downloaded; extracted '
            f'{E(zulu(src.get("extracted_at_utc")))})</summary><div class="body"><ul class="small">{rows}</ul></div></details>')


def cumulative_table(p: dict[str, Any]) -> str:
    rows = []
    for label, key in (("This week", "week"), ("Prior NFL weeks", "prior_weeks"), ("Season to date", "season_to_date")):
        c = p["cumulative_wagers"][key]
        rows.append([E(label), str(c["n"]), record(c["wins"], c["losses"], c["pushes"]), money(c["settled_stake"]),
                     money(c["net_profit"], True), pct(c["roi"], 1, True) + E(ci(c.get("roi_ci95"), as_pct=True)),
                     num(c.get("expected_wins"), 2), str(c.get("actual_wins_with_probability") or 0),
                     f"{c['open_count']} ({money(c['open_exposure'])})"])
    headers = [("Wagers", ""), ("n", "num"), ("Settled W-L-P", ""), ("Stake", "num"), ("Net", "num"),
               ("ROI [95% CI]", "num"), ("Exp. wins (non-tie basis)", "num"), ("Actual wins", "num"), ("Open", "num")]
    return _table(headers, rows)


def accuracy_table(p: dict[str, Any]) -> str:
    rows = []
    for label, key in (("This week", "week_forecast_accuracy"), ("Season to date", "season_forecast_accuracy")):
        a = p["model_vs_market"].get(key)
        if not a:
            continue
        m, t = a.get("margin") or {}, a.get("total") or {}
        cov = a.get("interval_coverage") or {}
        m50 = (cov.get("margin") or {}).get("50") or {}
        t50 = (cov.get("total") or {}).get("50") or {}
        rows.append([E(label), str(a["n"]), f"{a['favourite_correct']}/{a['favourite_n']}",
                     num(a.get("log_loss_two_way"), 3) + E(ci(a.get("log_loss_two_way_ci95"))),
                     num(m.get("bias"), 1, True), num(m.get("mae"), 1), num(t.get("bias"), 1, True), num(t.get("mae"), 1),
                     f"{m50.get('contained', '—')}/{m50.get('n', '—')}", f"{t50.get('contained', '—')}/{t50.get('n', '—')}"])
    headers = [("Scored forecasts", ""), ("n", "num"), ("Favourite won", "num"), ("Two-way LL [95% CI]", "num"),
               ("Margin bias", "num"), ("Margin MAE", "num"), ("Total bias", "num"), ("Total MAE", "num"),
               ("50% margin interval", "num"), ("50% total interval", "num")]
    return _table(headers, rows)


def story_card(g: dict[str, Any], s: dict[str, Any], base: str, target: str) -> str:
    pre, post = g["pregame"], g["postgame"]
    fc, mk, sel = pre["forecast"], pre["market"], pre["selected"]
    res = s["result"] or s["status"]
    head = (f"{E(s['kickoff_z'])} · {E(s['matchup'])} · selected <b>{E(s['selected_team'] or '—')}</b> "
            f"{E(odds(s['entry_american']))} · <span class=\"{E(res or '')}\">{E(res or '—')}</span> "
            f"{E(money(s['net_profit'], True)) if s['settled'] else ''}")
    pi = fc.get("prediction_interval") or {}
    pregame = f"""
<div class="box"><h4>(a) Pregame rationale — retained evidence</h4>
<p>Forecast {E(fc.get('snapshot') or '—')} (row <code>{E(short(fc.get('row_id')))}</code>, {E(fc.get('event_id') or '')}),
available {E(zulu(fc.get('available_utc')))}: home {pct(fc.get('p_home'))}, away {pct(fc.get('p_away'))}, tie {pct(fc.get('p_tie'))}.
Expected score {E(s['away'] or '')} {num(fc.get('expected_away'))} – {E(s['home'] or '')} {num(fc.get('expected_home'))}
(margin home {num(fc.get('expected_margin_home'), 1, True)}, total {num(fc.get('expected_total'))};
90% interval margin {num(pi.get('margin_lower'), 0)} to {num(pi.get('margin_upper'), 0)}, total {num(pi.get('total_lower'), 0)}–{num(pi.get('total_upper'), 0)}).</p>
<p>Selected <b>{E(s['selected_team'] or '—')}</b>: pWin {pct(sel['p_win'])}, pTie {pct(sel['p_tie'])}, pLoss {pct(sel['p_loss'])}
(two-way {pct(sel['p_win_two_way'])}). Model's predicted winner: {E(pre['model_favourite']['team'] or '—')} {pct(pre['model_favourite']['p_two_way'])}.</p>
<p>Price {E(mk['book'] or '—')} {E(odds(mk['american']))} (other side {E(odds(mk['opposite_american']))}), no-vig {pct(mk['no_vig_selected'])};
median of {mk.get('median_books') or 0} books at the decision {pct(mk.get('median_selected'))}. Recorded EV {pct(pre['ev'], 1, True)} per $1
(ties refunded); stake {money(pre['stake'])} ({E(str(pre['staking'].get('bound_by') or '—'))}).</p>
<p class="small muted">Gates: {E(str(pre['gates'].get('outcome')))} / {E(str(pre['gates'].get('reason')))} · readiness verified
{E(str(pre['gates'].get('readiness_verified')))} ({E(str(pre['gates'].get('exposure_status')))}) · eligibility
{E(str(pre['gates'].get('eligibility')))} · pair age {E(str(pre['gates'].get('pair_age_minutes')))} min.</p></div>"""
    if post.get("final"):
        f = post["final"]
        postgame = f"""
<div class="box"><h4>(b) What happened — POSTGAME</h4>
<p>Final {E(s['away'] or '')} {f['away']} – {E(s['home'] or '')} {f['home']}{' (OT)' if post.get('overtime') else ''}.
Margin (home) {post['margin_home']:+d} vs expected {num(post['expected_margin_home'], 1, True)}; total {post['total']} vs expected
{num(post['expected_total'])} ({E(str(post.get('total_direction') or '').lower())} by {num(abs(post['total_error'] or 0), 1)}).</p>
<p>In the forecast's own distribution: margin at the {pct(post.get('margin_percentile'), 0)} percentile, total at the
{pct(post.get('total_percentile'), 0)} percentile; exact score probability {_exact(post.get('exact_score_probability'))};
inside the 90% interval: margin {_yes(post.get('margin_in_90'))}, total {_yes(post.get('total_in_90'))}.</p>
<p class="small muted">{E(post['play_by_play'])}</p></div>"""
    else:
        postgame = f"""
<div class="box"><h4>(b) What happened — POSTGAME</h4><p><b>Pending.</b> Game status {E(str(post.get('status')))} at the data
cutoff; no result is shown.</p></div>"""
    checks = "".join(f'<li><span class="st st-{E(c["status"])}">{E(c["status"])}</span>{E(c["area"])}: {E(c["detail"])}</li>'
                     for c in g["checks"])
    hyp = "".join(f"<li>{E(h)}</li>" for h in g["hypotheses"])
    return f"""
<details class="card" id="g-{E(s['position_id'][:8])}"><summary>{head}</summary><div class="body">
<div class="grid2">{pregame}{postgame}</div>
<div class="grid2" style="margin-top:10px">
<div class="box"><h4>(c) Can a problem be established?</h4><p><b>{E(g['established_summary'])}</b></p><ul class="checks">{checks}</ul></div>
<div class="box"><h4>(d) Still a hypothesis</h4><ul>{hyp}</ul>
<p class="small muted">Decision <code>{E(s['decision_id'] or '—')}</code> · position <code>{E(s['position_id'])}</code> ·
<a href="{E(_game_link(s, base))}" target="{target}">game page</a></p></div></div></div></details>"""


def render_html(p: dict[str, Any], *, link_base: str = "", link_target: str = "_top", theme: str | None = None) -> str:
    h = headline(p)
    slate = {s["position_id"]: s for s in p["slate"]}
    settled = [slate[i] for i in p["settled"]]
    open_ = [slate[i] for i in p["open"]]
    fin = p["financial"]
    e = fin["expectation_settled"]
    ea = fin["expectation_all_positions"]
    cut = cutoff_block(p)
    tiles = [
        (h["record"], "Settled W-L-P"), (money(h["net"], True), "Net profit (settled)"),
        (pct(h["roi"], 1, True), "ROI" + ci(h["roi_ci"], as_pct=True)), (money(h["stake"]), "Settled stake"),
        (f"{h['open_count']} · {money(h['open_exposure'])}", "Open positions · exposure"),
        (num(h["expected_wins"], 1), "Expected wins (Σ pWin)"),
        (money(h["expected_profit"], True), f"Expected profit (SD {money(h['sd_profit'])})"),
    ]
    tiles_html = "".join(f"<div class=\"tile\"><b>{E(v)}</b><span>{E(k)}</span></div>" for v, k in tiles)
    open_rows = "".join(
        f"<li><b>{E(s['matchup'])}</b> {E(s['kickoff_z'])} — {E(s['selected_team'] or '')} {E(odds(s['entry_american']))}, "
        f"stake {money(s['stake'])}, recorded EV {pct(s['recorded_ev'], 1, True)}, expected profit "
        f"{money(s['expected_profit'], True)}. <span class=\"OPEN\">OPEN</span> — game {E(str(s['game_status']))} at the cutoff.</li>"
        for s in open_)
    nopos = "".join(f"<li><b>{E(g['matchup'])}</b> {E(zulu(g['kickoff_utc']))} — {E(g['note'])}"
                    + (f" Final {g['final']['away']}–{g['final']['home']}." if g.get("final") else "") + "</li>"
                    for g in p["games_without_position"])
    findings = "".join(
        f'<div class="finding"><h3>{i}. {E(f["title"])}</h3><p>{E(f["finding"])}</p>'
        f'<p><b>Next test:</b> {E(f["next_test"])}</p><p class="small muted">Belongs to: {E(f["experiment"])}</p></div>'
        for i, f in enumerate(p["findings"], 1))
    stories = "".join(story_card(g, slate[g["position_id"]], link_base, link_target) for g in p["games"])
    links = "".join(f"<li>{E(x['label'])}: <code>{E(x['path'])}</code></li>" for x in p["research_links"])
    exps = "".join(f"<li>{E(v)}</li>" for v in p["experiments"].values())
    notes = "".join(f"<li>{E(n)}</li>" for n in p["notes"])
    pending_note = (f"<p><b>{len(open_)} position(s) still open</b> at the cutoff; they are excluded from every settled "
                    f"number above and listed below.</p>" if open_ else "")
    return f"""<!doctype html>
<html lang="en"{(" data-theme=\"" + theme + "\"") if theme in ("dark", "light") else ""}><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(p['title'])}</title><style>{CSS}</style></head>
<body><main>
<p class="muted small">AISP · Model Performance · weekly report</p>
<h1>{E(p['title'])}</h1>
<p class="muted">{E(p['window']['label'])} · {E(p['population']['label'])}</p>
<div class="cutoff"><strong>{E(cut[0])}</strong><br>{E(cut[1])}<br>{E(cut[2])}</div>
<div class="tiles">{tiles_html}</div>
<p>{E(scorecard_line(p))}</p>
<p>{E(expectation_line(p))}</p>
{pending_note}

<h2>1 · Every official position this week, by slate</h2>
<p class="small muted">Selected = the funded selection, not the model's predicted winner. pWin/pTie are the decision-time
numbers recorded on each position's own funding decision. Lead = hours from the forecast's availability to kickoff. Later
prices are "last observed quotes", never closing lines: {E(p['closing_line_rule'])}</p>
{''.join(f"<h3>{E(g['label'])} — {g['n']} position{'s' if g['n'] != 1 else ''} ({g['settled']} settled, {g['open']} open)</h3>" + slate_table([slate[i] for i in g['position_ids']], link_base, link_target) for g in p['slate_groups'])}
<h3>Unfinished games and open exposure</h3>
<ul>{open_rows or '<li>None.</li>'}</ul>
<h3>Week games with no position</h3>
<ul>{nopos or '<li>None.</li>'}</ul>

<h2>2 · Financial and predictive results</h2>
<h3>Realised versus the entry expectation (settled wagers only)</h3>
{_table([("Measure", "wrap"), ("Expected at entry", "num"), ("Actual", "num")], [
    ["Wins (Σ pWin)", num(e.get('expected_wins'), 2), str(e.get('actual_wins', '—'))],
    ["Pushes (Σ pTie)", num(e.get('expected_pushes'), 2), str(e.get('actual_pushes', '—'))],
    ["Losses (Σ pLoss)", num(e.get('expected_losses'), 2), str(e.get('actual_losses', '—'))],
    ["Profit (Σ stake × recorded EV)", money(e.get('expected_profit'), True), money(e.get('actual_profit'), True)],
    ["SD of profit †", money(e.get('sd_profit')), "—"],
    ["z-score of actual profit †", "—", num(e.get('z_profit'), 2, True)],
    ["P(wins ≥ actual) · P(profit ≥ actual) †", "—", f"{num(e.get('p_wins_at_least_actual'), 2)} · {num(e.get('p_profit_at_least_actual'), 2)}"],
])}
<p class="small muted">Recorded EV recomputes as pWin(d−1) − pLoss on every wager (max difference
{num(e.get('max_abs_recorded_vs_recomputed_ev'), 12)}). † {E(p['uncertainty_label'])} Same-day NFL games are
correlated, so the true spread is at least this wide. Including the open position(s) the entry expectation is
{num(ea.get('expected_wins'), 2)} wins and {money(ea.get('expected_profit'), True)} on {money(ea.get('stake'))}.</p>
<h3>By favourite / underdog, disagreement and model pick</h3>
{summary_table(fin['groups']['price'], 'Price group')}
<br>{summary_table(fin['groups']['disagreement'], 'Model two-way vs decision no-vig')}
<br>{summary_table(fin['groups']['model_pick'], 'Selection vs model pick')}
<h3>By recorded probability band and recorded-return band (Wager Results bands)</h3>
<div class="grid2"><div>{band_table(fin['bands']['probability'], 'pWin band')}</div><div>{band_table(fin['bands']['return'], 'Recorded EV band')}</div></div>
<p class="small muted">Band results are exploratory slices of a small sample, unadjusted for multiple comparisons. Higher recorded
return is not higher confidence: a longshot can carry a high return with a low win probability.</p>
<h3>Model versus market on identically qualified games</h3>
{week_mvm_html(p)}
<h4>Season context (Overview calculation)</h4>
{mvm_table(p)}
<p class="small muted">{E(p['model_vs_market']['week']['note'])} Negative Δ favours the model.</p>
<h3>This week alongside the season (NFL)</h3>
{cumulative_table(p)}
<br>{accuracy_table(p)}
<p class="small muted">Forecast accuracy uses the Overview's scored evaluations (one per game, latest snapshot before kickoff),
including games with no position. Wager expectation here uses the Wager Results non-tie basis pWin/(1−pTie) on decided wagers.</p>
<p class="small"><b>Why {p['model_vs_market']['week_forecast_accuracy']['n'] if p['model_vs_market'].get('week_forecast_accuracy') else 0} forecasts but {len(p['slate'])} positions:</b> {E(p['model_vs_market']['explanations']['evaluations_vs_wagers'])}</p>

<h2>3 · Three games, deconstructed</h2>
<p class="small muted">Chosen by rule, not by story: {E('; '.join(d['reason'] + ' (' + d['matchup'] + ')' for d in p['deep_dives']))}.
Each card keeps three things apart: BEFORE KICKOFF (retained evidence only), OBSERVED (what the retained final records say,
with source) and HYPOTHESIS (what deserves investigation). No injury, turnover cause or feature attribution is invented.</p>
{evidence_banner(p)}
{''.join(dive_card(d, slate[d['position_id']]) for d in p['deep_dives'])}
{sources_html(p)}

<h2>4 · Record checks for every position</h2>
<p class="small muted">(a) from retained pregame evidence only; (b) labelled POSTGAME; (c) what the records establish; (d) what stays a
hypothesis. A win does not validate an edge; a loss does not invalidate one.</p>
{stories}

<h2>5 · Actionable learning</h2>
{findings}
<div class="caveat"><b>{E(p['caveat'])}</b></div>

<footer>
<h3>How to read this</h3><ul>{notes}</ul>
<p>{E(p['play_by_play'])}</p>
<h3>Active and proposed experiments</h3><ul>{exps}</ul>
<h3>Related research</h3><ul>{links}</ul>
<p>{E(p['authority']['statement'])} It creates no positions: the official wagers are in <a href="{E(link_base)}/performance?panel=wagers&amp;sport={E(p['league'])}" target="{link_target}">Wager Results</a>. Schema {E(p['schema_version'])}.</p>
<p>{E(' · '.join(cut))}</p>
</footer>
</main></body></html>"""


# ----------------------------------------------------------------- export ---

CSV_FIELDS = [
    "position_id", "decision_id", "game_id", "kickoff_utc", "matchup", "selected_team", "selected_side",
    "model_favourite_team", "selected_is_model_favourite", "p_win", "p_tie", "p_loss", "p_win_two_way",
    "forecast_row_id", "forecast_snapshot", "forecast_available_utc", "forecast_lead_hours",
    "forecast_age_at_decision_hours", "decision_at_utc", "decision_minutes_before_kickoff", "sportsbook",
    "entry_american", "entry_decimal", "opposite_american", "quote_observed_at_utc", "quote_available_utc",
    "quote_book_age_minutes", "no_vig_selected", "market_median_selected", "model_minus_market_pts", "price_group",
    "stake", "recorded_ev", "expected_profit", "status", "result", "net_profit", "settled_at_utc", "game_status",
    "final_away", "final_home", "overtime", "last_observed_quote_label", "last_observed_quote_american",
    "closing_line", "binding_status", "data_cutoff_utc", "content_sha256",
]


def wager_records(p: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in p["slate"]:
        lq = s.get("last_observed_quote") or {}
        rec = {k: s.get(k) for k in CSV_FIELDS if k in s}
        rec |= {"final_away": (s.get("final") or {}).get("away"), "final_home": (s.get("final") or {}).get("home"),
                "last_observed_quote_label": lq.get("label"),
                "last_observed_quote_american": (lq.get("quote") or {}).get("american"),
                "data_cutoff_utc": p["data_cutoff"]["db_clock_utc"], "content_sha256": p["snapshot"]["content_sha256"]}
        out.append(rec)
    return out


def render_csv(p: dict[str, Any]) -> str:
    buf = io.StringIO()
    buf.write(f"# {p['title']} | {identity_line(p)}\n")
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in wager_records(p):
        writer.writerow(r)
    return buf.getvalue()


def render_wagers_json(p: dict[str, Any]) -> str:
    return json.dumps({"title": p["title"], "identity": identity(p), "schema_version": p["schema_version"],
                       "window": p["window"],
                       "data_cutoff": p["data_cutoff"], "snapshot": p["snapshot"], "population": p["population"],
                       "closing_line_rule": p["closing_line_rule"], "summary": p["financial"]["summary"],
                       "expectation_settled": p["financial"]["expectation_settled"],
                       "wagers": wager_records(p), "slate_detail": p["slate"],
                       "games_without_position": p["games_without_position"]}, indent=1, default=str)


# ------------------------------------------------------------------ brief ---

def render_brief(p: dict[str, Any], *, report_dir: str | None = None) -> str:
    h = headline(p)
    fin = p["financial"]
    e = fin["expectation_settled"]
    mv = p["model_vs_market"]
    pair, med = mv["week"]["vs_decision_pair"] or {}, mv["week"]["vs_median_of_books"] or {}
    season = (mv.get("season_overview") or {}).get("market_decision_time") or {}
    comp = (mv.get("season_overview") or {}).get("market_comparable") or {}
    slate = {s["position_id"]: s for s in p["slate"]}
    open_ = [slate[i] for i in p["open"]]
    lines = [f"# {p['title']} — brief", ""]
    lines += [f"> **{c}**" if i == 0 else f"> {c}" for i, c in enumerate(cutoff_block(p))]
    lines += ["", f"Window: {p['window']['label']}. Population: official funded positions, the same rows and settlement as "
                  f"Wager Results; model versus market uses the Model Performance Overview calculation.", "",
              "## Scorecard", "",
              f"- **{scorecard_line(p)}**",
              f"- {expectation_line(p)}"]
    if open_:
        for s in open_:
            lines.append(f"- **Pending:** {s['matchup']} ({s['kickoff_z']}) — {s['selected_team']} "
                         f"{odds(s['entry_american'])}, {money(s['stake'])} open, expected {money(s['expected_profit'], True)}.")
    for g in p["games_without_position"]:
        lines.append(f"- {g['matchup']} ({zulu(g['kickoff_utc'])}): {g['note']}")
    big = next((g for g in fin["groups"]["disagreement"] if g["key"] == "LARGE"), None)
    fav = {g["key"]: g for g in fin["groups"]["price"]}
    lines += ["", "## Where it came from", ""]
    for key, label in (("FAVOURITE", "Favourites"), ("UNDERDOG", "Underdogs")):
        g = fav.get(key)
        if g and g["n"]:
            lines.append(f"- {label}: {g['n']} positions, {record(g['wins'], g['losses'], g['pushes'])}, net "
                         f"{money(g['net_profit'], True)} (expected {money((g['expectation_settled'] or {}).get('expected_profit'), True)}).")
    if big and big["n"]:
        lines.append(f"- Model − market ≥ 10 pts: {big['n']} positions, {record(big['wins'], big['losses'], big['pushes'])}, "
                     f"net {money(big['net_profit'], True)} (expected wins {num((big['expectation_settled'] or {}).get('expected_wins'), 1)}).")
    lines += ["", "## Model vs market (two-way, same games, same decision time)", ""]
    for label, block, key in (("A. Entry book's own no-vig pair", pair, "verdict_pair"),
                              ("B. Median of books (Overview's strict clock)", med, "verdict_median")):
        if block:
            lines.append(f"- **{label}:** n={block['n']}; model log loss {block['model_log_loss']:.3f} vs market "
                         f"{block['market_log_loss']:.3f}, Δ {block['delta_log_loss']:+.3f}{ci(block['delta_log_loss_ci95'])}; "
                         f"model better in {block['model_better_games']} of {block['n']}. {mv['week'][key]}")
    lines.append(f"- Why the two n differ: {mv['explanations']['pair_vs_median']}")
    if season:
        lines.append(f"- Season to date (Overview): Δ {season['delta_log_loss']:+.3f}{ci(season['delta_log_loss_ci95'])} "
                     f"(n={season['n']}); comparable-time only Δ {comp.get('delta_log_loss', 0):+.3f}"
                     f"{ci(comp.get('delta_log_loss_ci95'))} (n={comp.get('n', 0)}).")
    lines += ["- Negative Δ favours the model.",
              f"- Forecast diagnostics vs scorecard: {mv['explanations']['evaluations_vs_wagers']}", "",
              "## Three games, deconstructed", "",
              (lambda st: f"Game evidence: **{st.get('status')}** — {st.get('reason')}"
                          + (f" ({st.get('file')}, file sha256 {st.get('file_sha256')}, source vintage "
                             f"{st.get('source_vintage_sha256')})" if st.get('status') == 'VERIFIED' else
                             ". Play-by-play sections are withheld."))(p.get('game_evidence_status') or {}), ""]
    for d in p["deep_dives"]:
        o = d["observed"]
        lines.append(f"- **{d['matchup']}** ({d['reason']}): selected {d['selected_team']}, {d['result']} "
                     f"{money(d['net_profit'], True)}. OBSERVED: final {o['final'].get('away')}–{o['final'].get('home')} "
                     f"(away–home); {o['play_by_play_status']}"
                     + (f"; turnovers {', '.join(f'{k} {v}' for k, v in o['turnover_counts'].items())}" if o.get('turnover_counts') else "")
                     + ". HYPOTHESES: " + "; ".join(dict.fromkeys(h["topic"] for h in d["hypotheses"])) + ".")
    src = p.get("game_evidence_sources") or {}
    if src:
        lines += ["", "Sources checked for game evidence (read-only, nothing downloaded):"]
        lines += [f"- {x.get('source')}: {x.get('finding')}" for x in src.get("searched") or []]
    lines += ["",
              "## Three findings", ""]
    for i, f in enumerate(p["findings"], 1):
        lines += [f"{i}. **{f['title']}.** {f['finding']}", f"   - Next test: {f['next_test']}",
                  f"   - Belongs to: {f['experiment']}"]
    lines += ["", f"**{p['caveat']}**", "", "## What the records could not support", "",
              f"- {p['play_by_play']}",
              f"- Closing-line value: {p['closing_line_rule']}",
              "- The expectation's spread treats wagers as independent; same-day games are correlated.",
              "- No result is claimed for games that had not finished at the data cutoff.", ""]
    if report_dir:
        lines += ["## Files", "", f"- `{report_dir}\\report.html` (full report)", f"- `{report_dir}\\wagers.csv`, "
                  f"`wagers.json` (wager-level export)", f"- `{report_dir}\\show_segment.md` (5–7 minute script)",
                  f"- `{report_dir}\\payload.json` (the generator's full payload)", ""]
    lines += ["## Related research", ""] + [f"- {x['label']}: `{x['path']}`" for x in p["research_links"]]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- show ---

def _max_lead(seq: list[dict[str, Any]], side: str) -> tuple[int, str | None]:
    best, when = 0, None
    for x in seq:
        lead = (x["home"] - x["away"]) if side == "HOME" else (x["away"] - x["home"])
        if lead > best:
            best, when = lead, x["clock"]
    return best, when


def _observed_words(d: dict[str, Any], s: dict[str, Any]) -> str:
    o = d["observed"]
    if o.get("play_by_play_status") != "FINAL_PLAY_BY_PLAY_RETAINED":
        return ((o.get("evidence_unavailable_reason") or "No finalized play-by-play is available") +
                ", so the final score is all we'll say about the game itself.")
    seq = [x for x in o["scoring_sequence"] if x["kind"] in ("TD", "FG", "SAFETY", "2PT", "XP", "OTHER")]
    lead, when = _max_lead(seq, s["selected_side"])
    last = next((x for x in reversed(o["scoring_sequence"]) if x["kind"] in ("TD", "FG", "SAFETY")), None)
    tos = ", ".join(f"{k} {v}" for k, v in o["turnover_counts"].items())
    top = {}
    for x in o["passers"]:
        if x["team"] not in top or x["pass_attempts"] > top[x["team"]]["pass_attempts"]:
            top[x["team"]] = x
    passers = " and ".join(f"{x['name_as_recorded']} threw {x['pass_attempts']} passes for {x['team']}" for x in top.values())
    words = (f"The recorded play-by-play says: {s['selected_team']}'s biggest lead was {lead} points"
             + (f", at {when}" if when else "") + ". ")
    if last:
        what = ('touchdown' if last['kind'] == 'TD' else 'field goal' if last['kind'] == 'FG' else 'safety')
        who = last.get("player_as_recorded")
        yards = last.get("kick_distance_yards")
        words += (f"The last score was a {last['team']} {what}"
                  + (f" by {who}" if who else "") + (f" from {yards:.0f} yards" if yards else "")
                  + f" at {last['clock']}. ")
    return words + f"Turnovers recorded: {tos}. {passers}."


def _show_story(i: int, d: dict[str, Any], s: dict[str, Any], widest: bool) -> str:
    b = d["before_kickoff"]
    sel = s["selected_team"]
    other = s["home"] if sel == s["away"] else s["away"]
    f = s["final"] or {}
    gap = s["model_minus_market_pts"] or 0
    lead_in = {"LONGSHOT_WIN": "the long shot that came in", "DISAGREEMENT_LOSS": "our biggest argument with the market",
               "CONFIDENT_LOSS": "the one that stung"}.get(d["key"], d["reason"])
    fav = ("" if s["selected_is_model_favourite"] in (None, True) else
           f" The model actually expected {s['model_favourite_team']} to win; we bought {sel} because the price beat the model's number.")
    qbs = [h for h in d["hypotheses"] if h["topic"] == "QB input"]
    qb = next((h for h in qbs if "did not attempt a pass" in h["text"]), qbs[0] if qbs else None)
    qb_line = ""
    if qb:
        qb_line = (" One research note, not a finding: our production forecast has no explicit quarterback input and "
                   "picks no starter. A separate research capture, which was never admitted and never fed the forecast, "
                   "had listed a different depth-chart quarterback from the one who threw every pass. That's an input gap "
                   "for the QB research to test, not evidence that production picked the wrong starter."
                   if "did not attempt a pass" in qb["text"] else
                   " One research note, not a finding: our production forecast has no explicit quarterback input, and an "
                   "unadmitted research capture showed a depth-chart change for one side. That goes to the QB research.")
    return (f"Story {i}: {lead_in}. {sel} at {odds(s['entry_american'])} against {other}. Leaving the small tie chance "
            f"aside, the model had {sel} at {pct(s['p_win_two_way'], 0)}; the market's fair price was "
            f"{pct(s['no_vig_selected'], 0)}. That's {gap:+.0f} points{', the widest on our card' if widest else ''}, and "
            f"{pct(s['recorded_ev'], 0, True)} expected per dollar.{fav} Final: {s['away']} {f.get('away')}, {s['home']} "
            f"{f.get('home')}. {s['result'].title()}, {money(s['net_profit'], True)}. {_observed_words(d, s)}{qb_line} "
            f"What we don't do is turn that into a cause. The report lists what's recorded and, separately, what we "
            f"want to investigate.")


def _odds_words(p_loss: float | None) -> str:
    if p_loss is None:
        return "more often than not"
    return f"{p_loss * 100:.0f}% of the time"


def _stake_words(stake: float | None) -> str:
    if stake is None:
        return "the stake"
    return f"{stake:g} dollars" if float(stake).is_integer() else money(stake)


def _closeness_words(z: float | None) -> str:
    if z is None:
        return "We can't size that gap yet."
    if abs(z) < 0.5:
        return "That's basically right on the number."
    if abs(z) < 1.5:
        return f"That's {'a bit better' if z > 0 else 'a bit worse'} than expected, well inside the normal range."
    return f"That's {'well above' if z > 0 else 'well below'} expectation, about {abs(z):.1f} standard deviations."


def _check_status(g: dict[str, Any], key: str) -> str | None:
    return next((c["status"] for c in g["checks"] if c["key"] == key), None)


def _checks_words(g: dict[str, Any]) -> str:
    said = ["the forecast was a kickoff-window FINAL forecast" if _check_status(g, "forecast_type_and_age") == "PASS"
            else "the forecast was older than we'd like"]
    if _check_status(g, "odds_match_snapshot") == "PASS":
        said.append("the price matched the sportsbook record")
    if _check_status(g, "settlement_matches_score") == "PASS":
        said.append("the settlement matched the score")
    text_ = (", ".join(said[:-1]) + ", and " + said[-1] if len(said) > 1 else said[0]) + "."
    flags = [c for c in g["checks"] if c["status"] == "FLAG"]
    if flags:
        text_ += f" One record issue did show up, and it's in the report: {flags[0]['area']}."
    return text_


def _records_words(p: dict[str, Any]) -> str:
    checks = [c for g in p["games"] for c in g["checks"]]

    def tally(key: str) -> tuple[int, int]:
        rel = [c for c in checks if c["key"] == key and c["status"] not in ("PENDING", "NOT_CHECKABLE")]
        return sum(1 for c in rel if c["status"] == "PASS"), len(rel)

    (s_ok, s_n), (o_ok, o_n), (e_ok, e_n) = tally("settlement_matches_score"), tally("odds_match_snapshot"), tally("ev_arithmetic")
    if s_ok == s_n and o_ok == o_n and e_ok == e_n:
        return ("The records check out, too. Every settled bet matched the final score, every price matched the "
                "sportsbook snapshot it came from, and every expected-value number recomputes exactly from the recorded "
                "probabilities.")
    return (f"The record checks: settlement matched the score on {s_ok} of {s_n}, prices matched their snapshot on "
            f"{o_ok} of {o_n}, and expected value recomputed on {e_ok} of {e_n}. The exceptions are in the report.")


def _plumbing_words(p: dict[str, Any]) -> str:
    if not any(x["key"] == "ORDERING" for x in p["findings"]):
        return "The timing records were clean this week: every decision saw its prices before it acted."
    delayed_ids = {g["position_id"] for g in p["games"] if any(c["key"] == "first_in_window_decision" for c in g["checks"])}
    clusters = {s["kickoff_utc"] for s in p["slate"]}
    delayed = {s["kickoff_utc"] for s in p["slate"] if s["position_id"] in delayed_ids}
    clock = sum(1 for g in p["games"] for c in g["checks"]
                if c["key"] == "quote_available_before_decision" and c["status"] == "FLAG")
    text_ = "One more thing we caught in our own plumbing. "
    if delayed:
        text_ += (f"In {len(delayed)} of the {len(clusters)} kickoff windows, the system checked for prices a moment "
                  f"before the fresh prices landed, said 'too stale', and placed the bets five minutes later instead. ")
    if clock:
        text_ += ("In another, the two jobs started in the same tick, and the price request finished about 1.7 seconds "
                  "after the bet's recorded decision time. Codex reviewed it: under our strict research rule the receipt "
                  "order is unproven, because the moment the decision actually read the price isn't recorded. That alone "
                  "doesn't show the decision saw anything early, and the bet and its settlement stand. Recording the real "
                  "read time is a separate fix. ")
    return text_ + ("The prices used were the ones collection delivered. The race between the two scheduled jobs is "
                    "known, and a fix is waiting for review.")


def render_show(p: dict[str, Any], *, report_path: str | None = None) -> str:
    h = headline(p)
    slate = {s["position_id"]: s for s in p["slate"]}
    open_ = [slate[i] for i in p["open"]]
    e = p["financial"]["expectation_settled"]
    wk = p["model_vs_market"]["week"]
    pair = wk["vs_decision_pair"] or {}
    big = next((g for g in p["financial"]["groups"]["disagreement"] if g["key"] == "LARGE"), None)
    fav = {g["key"]: g for g in p["financial"]["groups"]["price"]}
    w, l_, pu = h["record"].split("-")
    stakes = {x["stake"] for x in p["slate"]}
    stake_each = f", {_stake_words(next(iter(stakes)))} each" if len(stakes) == 1 else ""
    gaps = [x for x in p["slate"] if x["model_minus_market_pts"] is not None]
    widest_id = max(gaps, key=lambda x: abs(x["model_minus_market_pts"]))["position_id"] if gaps else None
    tab = f"/performance?panel=mmqb&league={p['league']}&season={p['window'].get('season')}&week={p['window'].get('week')}"
    parts = [
        f"# Monday Morning QB — show segment ({p['window']['short_label']})", "",
        f"*{identity_line(p)}*", "",
        f"*Full {h['n']}-position scorecard: {report_path or 'report.html'} (section 1), and in the app at "
        f"Model Performance → Monday Morning QB (`{tab}`). Every number below is in it. Target 5–7 minutes.*", "",
        "## Cold open: the scorecard", "",
        (f"Good morning, and welcome to Monday Morning QB, where we grade our own homework in public. The AI Sports "
         f"Predictor made {h['n']} paper bets on this NFL week{stake_each}. "
         f"{'All ' + str(h['n']) + ' are settled' if h['settled'] == h['n'] else str(h['settled']) + ' are settled'}. "
         f"The record: {w} wins, {l_} losses{', ' + pu + ' pushes' if pu != '0' else ''}. Net: {money(h['net'], True)} on "
         f"{money(h['stake'])} staked, a return of {pct(h['roi'], 0, True)}."
         + (f" {len(open_)} still open; we grade those when they're final." if open_ else "")),
        "",
        (f"Going in, the model's own probabilities said about {e.get('expected_wins', 0):.1f} wins and "
         f"{money(e.get('expected_profit'), True)}. We got {w} wins and {money(h['net'], True)}. "
         f"{_closeness_words(e.get('z_profit'))} The typical swing for a card like this is about "
         f"{money(e.get('sd_profit'))} either way, but that figure takes the model's probabilities at face value and "
         f"treats every bet as independent, so it can't tell you whether the model is calibrated. One week is a data "
         f"point, not a verdict."),
        "",
    ]
    for i, d in enumerate(p["deep_dives"], 1):
        s = slate[d["position_id"]]
        parts += [f"## {d['matchup']} — {d['reason']}", "", _show_story(i, d, s, s["position_id"] == widest_id), ""]
    right = []
    for key, label in (("FAVOURITE", "favourites"), ("UNDERDOG", "underdogs")):
        g = fav.get(key)
        if g and g["n"]:
            right.append(f"The {label} went {record(g['wins'], g['losses'], g['pushes'])}, net {money(g['net_profit'], True)}.")
    missed = ""
    if big and big["n"]:
        missed = (f"The bets where the model disagreed with the market by ten points or more went "
                  f"{record(big['wins'], big['losses'], big['pushes'])} for {money(big['net_profit'], True)}. ")
    if pair:
        missed += (f"And on the same {pair['n']} games, at the moment we bet, the model's probabilities scored "
                   f"{'worse' if pair['delta_log_loss'] > 0 else 'better'} than the sportsbook's own prices in this "
                   f"sample, but the uncertainty does not establish a difference.")
    parts += ["## What the model got right, what it missed", "",
              "What went right: " + " ".join(right) + " " + _records_words(p), "",
              "What it missed: " + missed, "", _plumbing_words(p), "",
              "## What we're testing next", ""]
    for i, f in enumerate(p["findings"], 1):
        parts.append(f"{i}. {f.get('spoken') or f['next_test']}")
    parts += ["",
              ("And the rule we live by: one Sunday can flag things worth checking. It can't justify re-tuning the model or "
               "moving the thresholds. A win doesn't prove an edge, and a loss doesn't disprove one. See you next Monday."),
              ""]
    text_ = "\n".join(parts)
    spoken = [ln for ln in parts if ln and not ln.startswith(("#", "*"))]
    words = len([w_ for w_ in " ".join(spoken).split() if any(ch.isalpha() for ch in w_)])
    return text_ + f"\n---\n*Spoken word count ≈ {words} (about {words / 150:.1f} minutes at 150 words a minute).*\n"


__all__ = ["render_html", "render_brief", "render_csv", "render_wagers_json", "render_show", "headline",
           "scorecard_line", "wager_records"]
