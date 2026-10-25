/**
 * MONDAY MORNING QB (2026-10-25): its own page, built for James's ask: "really dissect the results and provide
 * clear, easy to understand analysis of what went right and what went wrong, lessons learned, and whether
 * there is an associated update to our model or selection process because of it."
 *
 * Everything comes from the backend's read-only weekly report (one database snapshot, AISP_MMQB_REPORT_V1). The
 * "What changed because of it" timeline is the only hand-kept part; each entry that is a live rule is checked
 * against the rules Mission Control reports as in force right now, so the page never claims a change that is off.
 */
import { useMemo, useState, type CSSProperties } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import * as d3 from 'd3'
import { AispSide } from '../flow/AispSide'
import { FlowRiver, type RiverColumn } from '../../components/charts/FlowRiver'
import { useMeasuredWidth } from '../../components/charts/d3Charts'
import { operationalToday } from '../../services/operationalDate'
import { useMissionControl } from '../mission/missionData'
import { american, usd } from '../pulse/pulseData'
import { fixText, gapBand, useMmqbIndex, useMmqbReport, type MmqbBet, type MmqbReport } from './mmqbData'
import '../slate/slate.css'
import './mmqb.css'

const pct = (p: number | null | undefined) => (p == null ? '—' : `${Math.round(p * 100)}%`)
const pts = (x: number | null | undefined) => (x == null ? '—' : `${x > 0 ? '+' : ''}${x.toFixed(1)} pts`)
const won = (b: MmqbBet) => b.result === 'WIN'
const lost = (b: MmqbBet) => b.result === 'LOSS'

type OwnerRules = { rule?: string; active_at_observation?: boolean; nfl_qb_out_gate?: { active_at_observation?: boolean; week?: number } }

/** What AISP changed because of what these reports found. Kept by hand; live rules are verified below. */
const CHANGES: Array<{ date: string; kind: 'Selection rule' | 'Process fix' | 'Proposal'; title: string; what: string; why: string; check?: (r: OwnerRules | null) => boolean | null; status?: string }> = [
  {
    date: 'Oct 20', kind: 'Selection rule', title: 'Big model–market gaps are now record-only',
    what: 'When the model sits more than 10 points above the sportsbook’s own no-vig price on the side it would take, the pick is recorded but no paper money goes on it.',
    why: 'Week 6’s six bets with a 10+ point gap went 1–5 (−$23.59), and season to date the model’s biggest disagreements have scored worse than the market. A huge gap is more often the model missing something than the market being wrong.',
    check: (r) => (r ? Boolean(r.active_at_observation && /GAP_GT_10/.test(r.rule ?? '')) : null),
  },
  {
    date: 'Oct 23', kind: 'Selection rule', title: 'Week 7: an expected-out starting QB makes the game record-only',
    what: 'If the expected starting quarterback is out, the NFL pick is recorded but not funded.',
    why: 'The NFL model’s final forecast carries no quarterback, injury or availability input (Week 6 deep dive), so it cannot see a backup starting. The market can.',
    check: (r) => (r ? Boolean(r.nfl_qb_out_gate?.active_at_observation) : null),
  },
  {
    date: 'Oct 24', kind: 'Process fix', title: 'The NFL price fallback re-checks on every tick from 30 to 21 minutes out',
    what: 'If the first check finds a stale price, the paid Odds API fallback is now allowed on every 5-minute tick in the window, not once.',
    why: 'In Week 6, 11 of 13 bets were taken one tick after an earlier check had skipped for a stale quote: collection and decision were racing each other.',
    status: 'Live since Oct 24. Still not perfect: PIT@NO this morning skipped once for a stale quote before deciding 5 minutes later.',
  },
  {
    date: 'Pending', kind: 'Proposal', title: 'Decision repairs: wait for fresh prices instead of skipping',
    what: 'A post-collection re-check and an alert whenever a bet’s recorded price is newer than its decision.',
    why: 'The fallback fix reduces the race; this would remove it. It is written and waiting for review, off by default.',
    status: 'Pending review',
  },
  {
    date: 'Pending', kind: 'Proposal', title: 'Score-range recalibration',
    what: 'Widen or narrow the predicted score ranges if the season’s coverage says they are off.',
    why: 'One week’s misses do not justify it; the season-long interval check decides. Not active.',
    status: 'Proposal, not active',
  },
]

export function MmqbPage() {
  const [params, setParams] = useSearchParams()
  const index = useMmqbIndex('NFL')
  const weeks = index.data?.weeks ?? []
  const latest = index.data?.latest ?? null
  const season = Number(params.get('season')) || latest?.season || null
  const week = Number(params.get('week')) || latest?.week || null
  const report = useMmqbReport('NFL', season, week)
  const r = report.data ?? null
  const mission = useMissionControl(operationalToday())
  const rules = ((mission.data as unknown as { owner_funding_restrictions?: OwnerRules } | null)?.owner_funding_restrictions) ?? null

  return (
    <div className="hq mq">
      <AispSide />
      <div className="hq-main">
        <header className="sl-top">
          <div className="sl-title"><small>THE WEEKLY REVIEW</small><h1>Monday Morning QB</h1><p>Every NFL bet from the week, what went right, what went wrong, and what we changed because of it.</p></div>
          <label className="mq-pick">Week{' '}
            <select value={week ?? ''} onChange={(e) => { const w = weeks.find((x) => x.week === Number(e.target.value)); if (w) setParams({ season: String(w.season), week: String(w.week) }) }}>
              {weeks.map((w) => <option key={`${w.season}-${w.week}`} value={w.week}>Week {w.week} · {w.positions} bet{w.positions === 1 ? '' : 's'}</option>)}
            </select>
          </label>
        </header>
        <main className="mq-body">
          {!r ? <p className="sl-empty">{report.error ? `The report could not be read: ${report.error}` : 'Building the report from one read-only snapshot…'}</p> : <Report r={r} rules={rules} rulesKnown={Boolean(mission.data)} />}
        </main>
        <footer className="hq-foot">Research and paper only. Built from a read-only database snapshot; it changes no record, rule or stake. {r ? fixText(r.data_cutoff.statement) : ''}</footer>
      </div>
    </div>
  )
}

function Report({ r, rules, rulesKnown }: { r: MmqbReport; rules: OwnerRules | null; rulesKnown: boolean }) {
  const s = r.financial.summary
  const bets = r.slate
  const settled = bets.filter((b) => won(b) || lost(b))
  const marketWins = settled.reduce((t, b) => t + (b.no_vig_selected ?? 0), 0)
  const modelWins = settled.reduce((t, b) => t + (b.p_win_two_way ?? 0), 0)
  const big = bets.filter((b) => (b.model_minus_market_pts ?? 0) > 10)
  const bigW = big.filter(won).length, bigL = big.filter(lost).length
  const rest = bets.filter((b) => (b.model_minus_market_pts ?? 0) <= 10)
  const restPl = rest.reduce((t, b) => t + (b.net_profit ?? 0), 0)
  const bigPl = big.reduce((t, b) => t + (b.net_profit ?? 0), 0)
  // the verdict says only what the numbers say: the result against the market's expectation, and where the money came from
  const best = [...settled].sort((a, b) => (b.net_profit ?? 0) - (a.net_profit ?? 0))[0]
  const bestShare = best && s.net_profit > 0 ? (best.net_profit ?? 0) / s.net_profit : 0
  const vsMarket = s.wins > marketWins + 0.5 ? 'more wins than the market’s prices expected' : s.wins < marketWins - 0.5 ? 'fewer wins than the market’s prices expected' : 'about the wins the market’s prices expected'
  const verdict = s.net_profit >= 0
    ? `A winning week, with ${vsMarket}.${best && bestShare > 0.6 ? ` Most of the profit came from one price: ${best.selected_team} ${american(best.entry_american)} (${usd(best.net_profit ?? 0, true)}).` : ''}`
    : `A losing week, with ${vsMarket}. Below: whether the prices were wrong or the dice were.`
  return (
    <>
      <section className="mq-hero">
        <div className="mq-hero__score">
          <small>{fixText(r.window.label)}</small>
          <h2><b>{s.wins}–{s.losses}{s.pushes ? `–${s.pushes}` : ''}</b> <span className={s.net_profit >= 0 ? 'is-up' : 'is-down'}>{usd(s.net_profit, true)}</span></h2>
          <p>{usd(s.settled_stake)} staked · ROI {s.roi == null ? '—' : `${Math.round(s.roi * 100)}%`}{s.roi_ci95 ? ` (95% range ${Math.round(s.roi_ci95[0] * 100)}% to ${Math.round(s.roi_ci95[1] * 100)}%)` : ''}</p>
          <p className="mq-hero__verdict">{verdict}</p>
        </div>
        <WinsBar actual={s.wins} market={marketWins} model={modelWins} n={settled.length} />
      </section>

      <section className="mq-card mq-scatter-card">
        <header><h2>Every bet on one chart</h2><small>Across: how far the model sat from the sportsbook’s own price on the side we took. Up and down: what the bet made or lost. Hover a dot.</small></header>
        <GapScatter bets={bets} />
        <p className="mq-call">
          Bets with a gap over 10 points went <b>{bigW}–{bigL}</b> ({usd(bigPl, true)}). Everything else went <b>{rest.filter(won).length}–{rest.filter(lost).length}</b> ({usd(restPl, true)}).
          {big.length ? ' That shaded zone is now record-only (since Oct 20).' : ''}
        </p>
      </section>

      <section className="mq-two">
        <RightWrong bets={bets} />
      </section>

      <section className="mq-card">
        <header><h2>Where the week’s money went</h2><small>Click any block to follow it to the result.</small></header>
        <FlowRiver
          items={bets}
          columns={RIVER}
          value={(b) => b.stake}
          colorOf={(col, label) => RIVER_COLOR[label] ?? (col === 0 ? '#60a5fa' : '#94a3b8')}
          format={(v) => usd(v)}
          height={300}
          label="This week's bets from price to model-market gap to result"
          describe={(bs) => [`${bs.length} bet${bs.length === 1 ? '' : 's'} · ${bs.filter(won).length}–${bs.filter(lost).length}`, `P&L ${usd(bs.reduce((t, b) => t + (b.net_profit ?? 0), 0), true)}`]}
        />
      </section>

      <section className="mq-card">
        <header><h2>Lessons learned</h2><small>What the week says, in plain English, and the test that decides whether it is real.</small></header>
        <ol className="mq-lessons">
          {r.findings.map((f, i) => (
            <li key={f.key} style={{ ['--i' as string]: i } as CSSProperties}>
              <h3>{fixText(f.title)}</h3>
              {f.spoken ? <p className="mq-plain">{fixText(f.spoken)}</p> : null}
              <details><summary>The numbers</summary><p>{fixText(f.finding)}</p></details>
              <p className="mq-next"><b>Next test</b> {fixText(f.next_test)}</p>
              {f.experiment ? <span className="mq-chip">{fixText(f.experiment)}</span> : null}
            </li>
          ))}
        </ol>
      </section>

      <section className="mq-card">
        <header><h2>What changed because of it</h2><small>Changes to how AISP picks and places bets. Live rules are checked against what is in force right now.</small></header>
        <ol className="mq-changes">
          {CHANGES.map((c) => {
            const live = c.check ? c.check(rules) : null
            const state = c.check ? (live === null ? (rulesKnown ? 'Not found in force' : 'Checking…') : live ? 'In force now' : 'Not in force') : c.status ?? ''
            const tone = c.check ? (live ? 'on' : live === null && !rulesKnown ? 'wait' : 'off') : c.kind === 'Proposal' ? 'wait' : 'on'
            return (
              <li key={c.title} className={`mq-change mq-change--${tone}`}>
                <em>{c.date}</em>
                <div>
                  <span className="mq-change__kind">{c.kind}</span>
                  <h3>{c.title}</h3>
                  <p><b>What:</b> {c.what}</p>
                  <p><b>Why:</b> {c.why}</p>
                  <span className={`mq-state mq-state--${tone}`}>{state}</span>
                </div>
              </li>
            )
          })}
        </ol>
        <p className="mq-caveat">{fixText(r.caveat)}</p>
      </section>

      {r.deep_dives.length ? (
        <section className="mq-card">
          <header><h2>Film room</h2><small>The games worth replaying, and what each one can and cannot tell us.</small></header>
          <div className="mq-dives">
            {r.deep_dives.map((d) => (
              <article key={d.key} className={`mq-dive ${d.result === 'WIN' ? 'is-win' : 'is-loss'}`}>
                <small>{fixText(d.reason)}</small>
                <h3>{d.matchup} · took {d.selected_team} <span>{d.result === 'WIN' ? 'WON' : 'LOST'} {usd(d.net_profit, true)}</span></h3>
                <dl>
                  <div><dt>Model</dt><dd>{pct(d.before_kickoff.p_win_two_way)}</dd></div>
                  <div><dt>Market</dt><dd>{pct(d.before_kickoff.no_vig_selected)}</dd></div>
                  <div><dt>Price</dt><dd>{american(d.before_kickoff.entry_american)}</dd></div>
                  <div><dt>Final</dt><dd>{d.observed.final ? `${d.observed.final.away}–${d.observed.final.home}` : '—'}</dd></div>
                </dl>
                <ul>{d.hypotheses.map((h) => <li key={h.topic}><b>{fixText(h.topic)}.</b> {fixText(h.text)}</li>)}</ul>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      <section className="mq-card">
        <header><h2>Game by game</h2><small>{bets.length} bets{r.games_without_position.length ? ` · ${r.games_without_position.length} game${r.games_without_position.length === 1 ? '' : 's'} with no bet` : ''}</small></header>
        <div className="mq-tablewrap">
          <table className="mq-table">
            <thead><tr><th>Game</th><th>Took</th><th>Price</th><th>Model</th><th>Market</th><th>Gap</th><th>Final</th><th>Result</th></tr></thead>
            <tbody>
              {bets.map((b) => (
                <tr key={b.position_id} className={won(b) ? 'is-win' : lost(b) ? 'is-loss' : ''}>
                  <td><b>{b.matchup}</b><small>{b.kickoff_et}</small></td>
                  <td>{b.selected_team}{b.selected_is_model_favourite ? '' : <small>model underdog</small>}</td>
                  <td>{american(b.entry_american)}<small>{b.sportsbook ?? ''}</small></td>
                  <td>{pct(b.p_win_two_way)}</td>
                  <td>{pct(b.no_vig_selected)}</td>
                  <td className={(b.model_minus_market_pts ?? 0) > 10 ? 'is-big' : ''}>{pts(b.model_minus_market_pts)}</td>
                  <td>{b.final ? `${b.away} ${b.final.away} – ${b.home} ${b.final.home}${b.overtime ? ' OT' : ''}` : '—'}</td>
                  <td className="mq-res">{b.result ?? 'Open'} <b>{b.net_profit == null ? '' : usd(b.net_profit, true)}</b></td>
                </tr>
              ))}
              {r.games_without_position.map((g) => (
                <tr key={g.matchup} className="is-none"><td><b>{g.matchup}</b><small>{g.kickoff_et}</small></td><td colSpan={7}>{fixText(g.note)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mq-card mq-season">
        <header><h2>Season to date</h2><small>This week next to everything before it.</small></header>
        <div className="mq-season__row">
          {([['This week', r.cumulative_wagers.week], ['Earlier weeks', r.cumulative_wagers.prior_weeks], ['Season', r.cumulative_wagers.season_to_date]] as const).map(([label, t]) => (
            <div key={label} className="mq-tile">
              <small>{label}</small>
              <b>{t.wins}–{t.losses}{t.pushes ? `–${t.pushes}` : ''}</b>
              <span className={t.net_profit >= 0 ? 'is-up' : 'is-down'}>{usd(t.net_profit, true)}</span>
              <em>ROI {t.roi == null ? '—' : `${Math.round(t.roi * 100)}%`}{t.roi_ci95 ? ` · range ${Math.round(t.roi_ci95[0] * 100)}% to ${Math.round(t.roi_ci95[1] * 100)}%` : ''}</em>
            </div>
          ))}
        </div>
        <ul className="mq-notes">{r.notes.map((n) => <li key={n}>{fixText(n)}</li>)}</ul>
        <p className="mq-links"><Link to="/performance?panel=wagers&sport=NFL">Every official wager →</Link> <Link to="/experiment">The paper bankroll →</Link></p>
      </section>
    </>
  )
}

/** Three numbers that separate skill from luck: what the market's prices expected, what happened, what the model expected. */
function WinsBar({ actual, market, model, n }: { actual: number; market: number; model: number; n: number }) {
  const max = Math.max(n, 1)
  const rows: Array<[string, number, string, string]> = [
    ['The market expected', market, 'mq-w--market', 'If the sportsbooks’ own prices were exactly right.'],
    ['We actually won', actual, 'mq-w--actual', 'What happened.'],
    ['Our model expected', model, 'mq-w--model', 'If the model’s probabilities were exactly right.'],
  ]
  const read = actual >= model - 0.5 ? 'We won about what the model expected.' : actual >= market ? 'We beat the market’s expectation but fell short of the model’s: the model was too confident.' : 'We fell short of even the market’s expectation.'
  return (
    <div className="mq-wins" aria-label="Wins: market expected, actual, model expected">
      <h3>Skill or luck? Wins out of {n}</h3>
      {rows.map(([label, v, cls, help]) => (
        <div key={label} className={`mq-w ${cls}`} title={help}>
          <span>{label}</span>
          <i><b style={{ width: `${(v / max) * 100}%` }} /></i>
          <em>{v.toFixed(1).replace(/\.0$/, '')}</em>
        </div>
      ))}
      <p>{read}</p>
    </div>
  )
}

function GapScatter({ bets }: { bets: MmqbBet[] }) {
  const [ref, width] = useMeasuredWidth<HTMLDivElement>(900)
  const [hover, setHover] = useState<MmqbBet | null>(null)
  const W = Math.max(320, width - 2), H = 300, m = { l: 54, r: 18, t: 18, b: 40 }
  const xs = bets.map((b) => b.model_minus_market_pts ?? 0)
  const ys = bets.map((b) => b.net_profit ?? 0)
  const x = d3.scaleLinear().domain([Math.min(-2, d3.min(xs) ?? 0) - 1, Math.max(14, d3.max(xs) ?? 0) + 2]).range([m.l, W - m.r]).nice()
  const yMax = Math.max(8, d3.max(ys.map(Math.abs)) ?? 8)
  const y = d3.scaleLinear().domain([-yMax * 1.1, yMax * 1.1]).range([H - m.b, m.t]).nice()
  const r = d3.scaleSqrt().domain([0, d3.max(bets, (b) => b.stake) ?? 5]).range([0, 11])
  const zone = x(10)
  return (
    <div ref={ref} className="mq-scatter" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="Each bet's model-market gap against its profit">
        <rect x={zone} y={m.t} width={Math.max(0, W - m.r - zone)} height={H - m.t - m.b} className="mq-zone" />
        <text x={zone + 8} y={m.t + 16} className="mq-zone__t">Gap over 10 pts · record-only since Oct 20</text>
        <line x1={m.l} x2={W - m.r} y1={y(0)} y2={y(0)} className="mq-axis0" />
        {x.ticks(8).map((t) => <g key={t}><line x1={x(t)} x2={x(t)} y1={m.t} y2={H - m.b} className="mq-grid" /><text x={x(t)} y={H - m.b + 16} textAnchor="middle" className="mq-tick">{t > 0 ? `+${t}` : t}</text></g>)}
        {y.ticks(5).map((t) => <text key={t} x={m.l - 8} y={y(t)} dy="0.35em" textAnchor="end" className="mq-tick">{t > 0 ? `+$${t}` : t < 0 ? `−$${-t}` : '$0'}</text>)}
        <text x={(m.l + W - m.r) / 2} y={H - 4} textAnchor="middle" className="mq-axis-t">model minus market, points (positive = model liked our side more than the book did)</text>
        {bets.map((b) => (
          <circle key={b.position_id} cx={x(b.model_minus_market_pts ?? 0)} cy={y(b.net_profit ?? 0)} r={Math.max(5, r(b.stake))}
            className={`mq-dot ${won(b) ? 'is-win' : lost(b) ? 'is-loss' : 'is-open'}${hover === b ? ' is-on' : ''}`}
            onMouseEnter={() => setHover(b)} onFocus={() => setHover(b)} tabIndex={0} aria-label={`${b.matchup}: took ${b.selected_team}, ${b.result ?? 'open'} ${usd(b.net_profit ?? 0, true)}, gap ${pts(b.model_minus_market_pts)}`} />
        ))}
      </svg>
      {hover ? (
        <div className="mq-tip" style={{ left: Math.min(x(hover.model_minus_market_pts ?? 0) + 12, W - 240), top: Math.max(8, y(hover.net_profit ?? 0) - 10) }}>
          <b>{hover.matchup} · took {hover.selected_team}</b>
          <span>{american(hover.entry_american)} at {hover.sportsbook ?? '—'}</span>
          <span>Model {pct(hover.p_win_two_way)} · market {pct(hover.no_vig_selected)} · gap {pts(hover.model_minus_market_pts)}</span>
          <span className={won(hover) ? 'is-up' : 'is-down'}>{hover.result ?? 'Open'} {usd(hover.net_profit ?? 0, true)}</span>
        </div>
      ) : null}
    </div>
  )
}

function RightWrong({ bets }: { bets: MmqbBet[] }) {
  const right = useMemo(() => bets.filter(won).sort((a, b) => (b.net_profit ?? 0) - (a.net_profit ?? 0)).slice(0, 4), [bets])
  const wrong = useMemo(() => bets.filter(lost).sort((a, b) => (b.p_win_two_way ?? 0) - (a.p_win_two_way ?? 0)).slice(0, 4), [bets])
  const dogs = bets.filter((b) => b.price_group === 'UNDERDOG'), favs = bets.filter((b) => b.price_group !== 'UNDERDOG')
  const sum = (bs: MmqbBet[]) => bs.reduce((t, b) => t + (b.net_profit ?? 0), 0)
  return (
    <>
      <div className="mq-card mq-right">
        <header><h2>✔ What went right</h2></header>
        <p className="mq-sum">Underdogs went <b>{dogs.filter(won).length}–{dogs.filter(lost).length}</b> ({usd(sum(dogs), true)}). The biggest paydays:</p>
        <ul>{right.map((b) => <li key={b.position_id}><b>{b.selected_team}</b> {american(b.entry_american)} <span>{b.matchup}</span><em className="is-up">{usd(b.net_profit ?? 0, true)}</em><small>model {pct(b.p_win_two_way)} vs market {pct(b.no_vig_selected)}</small></li>)}</ul>
      </div>
      <div className="mq-card mq-wrong">
        <header><h2>✘ What went wrong</h2></header>
        <p className="mq-sum">Favourites went <b>{favs.filter(won).length}–{favs.filter(lost).length}</b> ({usd(sum(favs), true)}). The losses the model was surest about:</p>
        <ul>{wrong.map((b) => <li key={b.position_id}><b>{b.selected_team}</b> {american(b.entry_american)} <span>{b.matchup}</span><em className="is-down">{usd(b.net_profit ?? 0, true)}</em><small>model {pct(b.p_win_two_way)} vs market {pct(b.no_vig_selected)} · gap {pts(b.model_minus_market_pts)}</small></li>)}</ul>
      </div>
    </>
  )
}

const RIVER: RiverColumn<MmqbBet>[] = [
  { title: 'PRICE', of: (b) => (b.price_group === 'UNDERDOG' ? 'Underdog' : 'Favourite'), order: ['Favourite', 'Underdog'] },
  { title: 'MODEL vs MARKET', of: (b) => gapBand(b.model_minus_market_pts), order: ['Gap under 5 pts', 'Gap 5–10 pts', 'Gap over 10 pts', 'Unknown gap'] },
  { title: 'RESULT', of: (b) => (won(b) ? 'Won' : lost(b) ? 'Lost' : b.result === 'PUSH' ? 'Push' : 'Open'), order: ['Won', 'Lost', 'Push', 'Open'] },
]
const RIVER_COLOR: Record<string, string> = {
  Favourite: '#60a5fa', Underdog: '#fb923c', 'Gap under 5 pts': '#2dd4bf', 'Gap 5–10 pts': '#fbbf24', 'Gap over 10 pts': '#f472b6', 'Unknown gap': '#64748b',
  Won: '#34d399', Lost: '#f87171', Push: '#94a3b8', Open: '#c4b5fd',
}
