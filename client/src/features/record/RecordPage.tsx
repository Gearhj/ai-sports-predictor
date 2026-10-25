/**
 * THE RECORD (owner's design, 2026-10-24): AISP's immutable paper betting history, read so it can be acted on.
 * Headline tiles, the bankroll line (drawdowns shaded, every settled bet marked), performance by sport, the
 * AISP COACH (rule-based observations from this record, plus an on-demand plain-English read from the Booth
 * model), the immutable ledger (search, filters, CSV export) and the active wagers rail.
 *
 * Source: the command-center ledger (AISP_COMMAND_CENTER_LEDGER_V1) and the day's games for live state. This
 * is the $5,000 paper experiment: nothing here places, suggests or sizes a real-money wager.
 */
import { BeatTheClose } from '../edge/BeatTheClose'
import { MoneyRiver } from './MoneyRiver'
import { AispSide } from '../flow/AispSide'
import { Crest, cfbColor } from '../flow/Crest'
import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { TeamLogo } from '../../components/TeamLogo'
import { gameHref, type BankrollPoint, type LedgerPosition, type SportCode } from '../../services/commandCenter'
import { operationalToday } from '../../services/operationalDate'
import { allPositions, american, etClock, resultOf, usd, usePulse, type PulseGame } from '../pulse/pulseData'
import { GamePlanBar } from '../flow/GamePlanBar'
import '../slate/slate.css'
import './record.css'

type Sport = 'ALL' | SportCode
type Range = '7' | '30' | '90' | 'season' | 'all'
type Res = 'ALL' | 'WIN' | 'LOSS' | 'PUSH' | 'OPEN'
type Pos = LedgerPosition & { sport: SportCode }
const SPORTS: Array<{ key: SportCode; label: string; color: string; icon: string }> = [
  { key: 'NFL', label: 'NFL', color: '#6ea4ff', icon: '🏈' }, { key: 'CFB', label: 'CFB', color: '#b88cff', icon: '🏈' }, { key: 'NBA', label: 'NBA', color: '#ffb14a', icon: '🏀' },
  { key: 'NHL', label: 'NHL', color: '#7cc4e4', icon: '🏒' }, { key: 'MLB', label: 'MLB', color: '#ff6b57', icon: '⚾' },
]
const COLOR = Object.fromEntries(SPORTS.map((s) => [s.key, s.color])) as Record<SportCode, string>
const NAV_WORK = [{ to: '/', label: 'Today', icon: '⌂' }, { to: '/games', label: 'Games', icon: '▦' }, { to: '/my-picks', label: 'My Picks', icon: '✓' }, { to: '/experiment', label: 'Record', icon: '◎', on: true }, { to: '/a-house-divided', label: 'Compete', icon: '♛' }]
const NAV_TOOLS = [{ to: '/mission-control', label: 'Mission Control', icon: '◈' }, { to: '/game-night', label: 'Game Night', icon: '▣' }, { to: '/markets', label: 'Markets & Crowd', icon: '◇' }, { to: '/performance', label: 'Model Performance', icon: '▥' }, { to: '/decisions', label: 'Daily Decisions', icon: '☑' }, { to: '/labs', label: 'Game Simulator', icon: '⚗' }, { to: '/research/history', label: 'Historical Research', icon: '⌛' }, { to: '/shadow-board', label: 'Shadow Board', icon: '◐' }, { to: '/methodology', label: 'Methodology', icon: '?' }]
const hueOf = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)
const pctS = (v: number | null, d = 1) => (v == null || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(d)}%`)
const sgn = (v: number | null, d = 1, suf = '') => (v == null || !Number.isFinite(v) ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}${suf}`)
const usdS = (v: number) => `${v >= 0 ? '+' : '-'}${usd(Math.abs(v))}`
const dayOf = (iso: string | null | undefined) => (iso ? operationalToday(new Date(iso)) : '')
const teamsOf = (m: string | null) => { const [a, h] = (m ?? '').split('@').map((x) => x.trim()); return { away: a || '—', home: h || '—' } }

function stats(list: Pos[]) {
  const done = list.filter((p) => resultOf(p) !== 'OPEN')
  const w = done.filter((p) => resultOf(p) === 'WIN').length
  const l = done.filter((p) => resultOf(p) === 'LOSS').length
  const pu = done.filter((p) => resultOf(p) === 'PUSH').length
  const pl = done.reduce((t, p) => t + (p.profit_loss ?? 0), 0)
  const staked = done.reduce((t, p) => t + (p.stake ?? 0), 0)
  return { n: done.length, w, l, pu, pl, staked, roi: staked ? pl / staked : null, win: w + l ? w / (w + l) : null }
}

export function RecordPage() {
  const today = operationalToday()
  const pulse = usePulse(today)
  const navigate = useNavigate()
  const [sport, setSport] = useState<Sport>('ALL')
  const [range, setRange] = useState<Range>('90')
  const [res, setRes] = useState<Res>('ALL')
  const [q, setQ] = useState('')
  const [chart, setChart] = useState<'all' | 'sports'>('all')
  const [shown, setShown] = useState(25)
  const ledgerRef = useRef<HTMLElement | null>(null)
  const ledger = pulse.ledger
  const all = useMemo(() => allPositions(ledger) as Pos[], [ledger])
  const since = useMemo(() => {
    if (range === 'all') return ''
    if (range === 'season') return `${Number(today.slice(5, 7)) >= 8 ? today.slice(0, 4) : String(Number(today.slice(0, 4)) - 1)}-08-01`
    const d = new Date(`${today}T12:00:00Z`); d.setUTCDate(d.getUTCDate() - Number(range)); return d.toISOString().slice(0, 10)
  }, [range, today])
  const inRange = (p: Pos) => !since || (dayOf(p.settled_at ?? p.placed_at ?? p.starts_at) >= since) || resultOf(p) === 'OPEN'
  const scoped = useMemo(() => all.filter((p) => (sport === 'ALL' || p.sport === sport) && inRange(p)), [all, sport, since]) // eslint-disable-line react-hooks/exhaustive-deps
  const st = useMemo(() => stats(scoped), [scoped])
  const open = useMemo(() => all.filter((p) => resultOf(p) === 'OPEN').sort((a, b) => Date.parse(a.starts_at ?? '') - Date.parse(b.starts_at ?? '')), [all])
  const settledAll = useMemo(() => all.filter((p) => resultOf(p) !== 'OPEN' && p.settled_at).sort((a, b) => Date.parse(a.settled_at!) - Date.parse(b.settled_at!)), [all])
  const starting = ledger?.totals.starting_capital ?? 5000
  const bankroll = ledger?.totals.realized_bankroll ?? null
  const gameById = useMemo(() => new Map(pulse.games.map((g) => [g.id, g])), [pulse.games])
  // streak over the scope, newest first
  const scopeSettled = scoped.filter((p) => resultOf(p) !== 'OPEN' && p.settled_at).sort((a, b) => Date.parse(a.settled_at!) - Date.parse(b.settled_at!))
  let streak = 0
  for (let i = scopeSettled.length - 1; i >= 0; i -= 1) { const r = resultOf(scopeSettled[i]); if (r === 'PUSH') continue; const win = r === 'WIN'; if (!streak) streak = win ? 1 : -1; else if (win === streak > 0) streak += Math.sign(streak); else break }
  const last5 = scopeSettled.slice(-5).map((p) => resultOf(p))
  const stakes = scoped.map((p) => p.stake ?? 0).filter((v) => v > 0).sort((a, b) => a - b)
  const avgStake = stakes.length ? stakes.reduce((t, v) => t + v, 0) / stakes.length : null
  const medStake = stakes.length ? stakes[Math.floor(stakes.length / 2)] : null
  const exposure = open.reduce((t, p) => t + (p.stake ?? 0), 0)
  const potential = open.reduce((t, p) => t + (p.stake ?? 0) * (p.entry.decimal ?? 1), 0)
  const periodPl = st.pl
  const bySport = SPORTS.map((s) => ({ s, ...stats(all.filter((p) => p.sport === s.key && inRange(p))) })).filter((x) => x.n > 0)

  // the bankroll line for the chosen scope
  const series: BankrollPoint[] = useMemo(() => {
    const src = sport === 'ALL' ? ledger?.combined_series ?? [] : ledger?.sports.find((s) => s.sport === sport)?.series ?? []
    return src.filter((p) => p.at && (!since || dayOf(p.at) >= since))
  }, [ledger, sport, since])
  const posById = useMemo(() => new Map(all.map((p) => [p.position_id, p])), [all])
  const bankAfter = useMemo(() => { const m = new Map<string, number>(); for (const p of ledger?.combined_series ?? []) if (p.position_id) m.set(p.position_id, p.bankroll); return m }, [ledger])

  // the ledger table
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase()
    return [...all].filter((p) => (sport === 'ALL' || p.sport === sport) && inRange(p) && (res === 'ALL' || resultOf(p) === res) && (!t || `${p.matchup ?? ''} ${p.selection ?? ''} ${p.entry.sportsbook ?? ''} ${p.sport}`.toLowerCase().includes(t)))
      .sort((a, b) => Date.parse(b.placed_at ?? b.starts_at ?? '') - Date.parse(a.placed_at ?? a.starts_at ?? ''))
  }, [all, sport, since, res, q]) // eslint-disable-line react-hooks/exhaustive-deps
  const seq = useMemo(() => { const m = new Map<string, number>(); [...all].sort((a, b) => Date.parse(a.placed_at ?? a.starts_at ?? '') - Date.parse(b.placed_at ?? b.starts_at ?? '')).forEach((p, i) => m.set(p.position_id, i + 1)); return m }, [all])
  const exportCsv = () => {
    const head = ['#', 'placed_at', 'sport', 'matchup', 'market', 'selection', 'american', 'stake', 'sportsbook', 'result', 'profit_loss', 'bankroll_after']
    const esc = (v: unknown) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s }
    const body = rows.map((p) => [seq.get(p.position_id), p.placed_at, p.sport, p.matchup, p.market, p.selection, p.entry.american, p.stake, p.entry.sportsbook, resultOf(p), p.profit_loss, bankAfter.get(p.position_id)].map(esc).join(','))
    const blob = new Blob([[head.join(','), ...body].join('\n')], { type: 'text/csv' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `aisp-record-${today}.csv`; a.click(); window.setTimeout(() => URL.revokeObjectURL(a.href), 2000)
  }
  const showSport = (s: SportCode) => { setSport(s); ledgerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }

  return (
    <div className="hq rc">
      <AispSide />
      <div className="hq-main">
        <GamePlanBar current="track" />
        <div className="rc-layout">
          <main className="rc-content">
            <header className="rc-head">
              <i className="rc-head__icon" aria-hidden="true">▥</i>
              <div><h1>The Record</h1><p>Your immutable betting history. Analyze performance. Find edges. Make better decisions.</p></div>
              <CoachMe stats={{ st, bySport, streak, avgStake, medStake, exposure, bankroll, starting, range }} />
            </header>
            <section className="rc-filters" aria-label="Filters">
              <span className="sl-tabs">
                <button type="button" className={sport === 'ALL' ? 'is-on' : ''} onClick={() => setSport('ALL')}>All Sports</button>
                {SPORTS.map((s) => <button key={s.key} type="button" className={sport === s.key ? 'is-on' : ''} onClick={() => setSport(s.key)}>{s.label}</button>)}
                <button type="button" disabled title="Not covered by AISP yet">MMA</button><button type="button" disabled title="Not covered by AISP yet">Boxing</button>
              </span>
              <span className="rc-selects">
                <select value={range} onChange={(e) => setRange(e.target.value as Range)} aria-label="Date range"><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option><option value="season">This season</option><option value="all">All time</option></select>
                <select value="ML" disabled aria-label="Market" title="The paper policy bets moneylines only"><option value="ML">All Markets (moneyline)</option></select>
                <select value={res} onChange={(e) => setRes(e.target.value as Res)} aria-label="Result"><option value="ALL">All Results</option><option value="WIN">Wins</option><option value="LOSS">Losses</option><option value="PUSH">Pushes</option><option value="OPEN">Open</option></select>
                <button type="button" onClick={() => { setSport('ALL'); setRange('90'); setRes('ALL'); setQ('') }}>Reset</button>
              </span>
            </section>

            <section className="rc-kpis" aria-label="Headline numbers">
              <Tile tone="teal" icon="▤" label="Paper Bankroll" big={bankroll == null ? '—' : usd(bankroll)} sub={<><span className={periodPl >= 0 ? 'is-up' : 'is-down'}>{usdS(periodPl)}</span> · starting {usd(starting)}</>} />
              <Tile tone="green" icon="↗" label="Net Profit / Loss" big={usdS(periodPl)} bigCls={periodPl >= 0 ? 'is-up' : 'is-down'} sub={<>ROI {sgn(st.roi == null ? null : st.roi * 100, 2, '%')} · wagered {usd(st.staked)}</>} />
              <Tile tone="blue" icon="◎" label="Win Rate" big={pctS(st.win)} sub={<>{st.w} / {st.w + st.l} wins / decided</>} />
              <Tile tone="gold" icon="♛" label="Record" big={`${st.w}-${st.l}-${st.pu}`} sub="wins · losses · pushes" />
              <Tile tone="violet" icon="≡" label="Avg Stake" big={avgStake == null ? '—' : usd(avgStake)} sub={`median ${medStake == null ? '—' : usd(medStake)}`} />
              <Tile tone="orange" icon="◔" label="Open Exposure" big={usd(exposure)} sub={`${open.length} open bets`} />
              <Tile tone={streak >= 0 ? 'green' : 'red'} icon="🔥" label="Current Streak" big={streak ? `${Math.abs(streak)} ${streak > 0 ? 'Win' : 'Loss'}${Math.abs(streak) > 1 ? (streak > 0 ? 's' : 'es') : ''}` : '—'} sub={<span className="rc-dots">{last5.map((r, i) => <i key={i} className={`is-${r.toLowerCase()}`}>{r[0]}</i>)}</span>} />
            </section>

            <div className="rc-row">
              <section className="sl-card rc-chart" aria-label="Bankroll performance">
                <header className="sl-card__head"><h2>Bankroll Performance</h2>
                  <span className="sl-wtabs rc-ctabs"><button type="button" className={chart === 'all' ? 'is-on' : ''} onClick={() => setChart('all')}>{sport === 'ALL' ? 'All Bets' : `${sport} bets`}</button><button type="button" className={chart === 'sports' ? 'is-on' : ''} onClick={() => setChart('sports')}>Compare Sports</button></span>
                  <span className={`rc-chart__delta ${periodPl >= 0 ? 'is-up' : 'is-down'}`}><b>{sgn(st.roi == null ? null : (periodPl / Math.max(1, starting)) * 100, 2, '%')}</b><small>{usdS(periodPl)}</small></span>
                </header>
                {chart === 'all' ? <BankChart series={series} base={sport === 'ALL' ? starting : (series[0]?.bankroll ?? 0) - (series[0]?.change ?? 0)} posById={posById} /> : <CompareChart ledger={ledger} since={since} />}
              </section>
              <section className="sl-card rc-sports" aria-label="Performance by sport">
                <header className="sl-card__head"><h2>Performance by Sport</h2></header>
                <table className="rc-table rc-table--sports">
                  <thead><tr><th>Sport</th><th>Record</th><th>Win rate</th><th>P/L</th><th>ROI</th></tr></thead>
                  <tbody>
                    {bySport.map((x) => (
                      <tr key={x.s.key} onClick={() => setSport(x.s.key)} className={sport === x.s.key ? 'is-on' : ''}>
                        <td><span className="rc-sport"><i style={{ background: x.s.color }}>{x.s.icon}</i>{x.s.label}</span></td>
                        <td>{x.w}-{x.l}-{x.pu}</td>
                        <td><span className="rc-wr"><b>{pctS(x.win)}</b><i><u style={{ width: `${(x.win ?? 0) * 100}%`, background: x.s.color }} /></i></span></td>
                        <td className={x.pl >= 0 ? 'is-up' : 'is-down'}>{usdS(x.pl)}</td>
                        <td className={(x.roi ?? 0) >= 0 ? 'is-up' : 'is-down'}><b>{sgn(x.roi == null ? null : x.roi * 100, 1, '%')}</b></td>
                      </tr>
                    ))}
                    {!bySport.length ? <tr><td colSpan={5} className="sl-empty">No settled bets in this range.</td></tr> : null}
                  </tbody>
                </table>
                <p className="rc-tiny">Click a sport to filter the whole page.</p>
              </section>
            </div>

            <Coach all={all} bySport={bySport} settled={settledAll} onSport={showSport} />

            <MoneyRiver positions={all} />
            <BeatTheClose />
            <section className="sl-card rc-ledger" aria-label="The immutable ledger" ref={ledgerRef}>
              <header className="sl-card__head"><h2>🔒 The Immutable Ledger</h2><small className="rc-sub">Every bet. Every result. Immutable and transparent.</small>
                <label className="rc-search"><span className="lf-sr">Search the ledger</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search teams, sportsbooks, or bet type…" /></label>
                <button type="button" className="rc-btn" onClick={exportCsv}>⇩ Export CSV</button>
              </header>
              <div className="rc-tablewrap">
                <table className="rc-table">
                  <thead><tr><th>#</th><th>Date (ET)</th><th>Sport</th><th>Event</th><th>Bet</th><th>Odds</th><th>Stake</th><th>Sportsbook</th><th>Result</th><th>P&amp;L</th><th>Bankroll after</th><th /></tr></thead>
                  <tbody>
                    {rows.slice(0, shown).map((p) => { const t = teamsOf(p.matchup); const r = resultOf(p); const g = gameById.get(p.game_id); return (
                      <tr key={p.position_id} onClick={() => navigate(gameHref(p.sport, p.game_id))}>
                        <td className="rc-dim">#{seq.get(p.position_id)}</td>
                        <td>{etClock(p.placed_at ?? p.starts_at, { month: 'short', day: 'numeric' })}<small>{etClock(p.starts_at)}</small></td>
                        <td><span className="rc-sport"><i style={{ background: COLOR[p.sport] }}>{SPORTS.find((s) => s.key === p.sport)?.icon}</i>{p.sport}</span></td>
                        <td><span className="rc-event"><Crest sport={p.sport} abbr={t.away} size={20} /><span>{t.away}</span><em>@</em><Crest sport={p.sport} abbr={t.home} size={20} /><span>{t.home}</span></span></td>
                        <td>{p.selection ?? '—'}<small>{p.market}</small></td>
                        <td>{p.entry.american != null ? american(p.entry.american) : '—'}</td>
                        <td>{usd(p.stake)}</td>
                        <td>{p.entry.sportsbook ?? '—'}</td>
                        <td><span className={`rc-res rc-res--${r.toLowerCase()}`}>{r === 'OPEN' ? (g && (g.phase === 'LIVE' || g.phase === 'STARTED') ? 'Live' : 'Open') : r === 'WIN' ? 'Win' : r === 'LOSS' ? 'Loss' : 'Push'}</span></td>
                        <td className={(p.profit_loss ?? 0) > 0 ? 'is-up' : (p.profit_loss ?? 0) < 0 ? 'is-down' : ''}>{p.profit_loss == null ? '—' : usdS(p.profit_loss)}</td>
                        <td>{bankAfter.has(p.position_id) ? usd(bankAfter.get(p.position_id)!) : '—'}</td>
                        <td className="rc-dim">›</td>
                      </tr>) })}
                    {!rows.length ? <tr><td colSpan={12} className="sl-empty">No bets match.</td></tr> : null}
                  </tbody>
                </table>
              </div>
              {rows.length > shown ? <button type="button" className="sl-more" onClick={() => setShown((n) => n + 50)}>Show {Math.min(50, rows.length - shown)} more of {rows.length}</button> : null}
            </section>
            <footer className="hq-foot">The $5,000 paper experiment: positions are entered and settled by AISP&apos;s policy and recorded once, never edited. Research and paper only; nothing here is a recommendation to wager real money.</footer>
          </main>

          <aside className="rc-rail" aria-label="Active wagers">
            <section className="sl-card">
              <header className="sl-card__head"><h2>Active Wagers</h2><span className="rc-count">{open.length}</span></header>
              <ul className="rc-wagers">
                {open.slice(0, 8).map((p) => <ActiveWager key={p.position_id} p={p} g={gameById.get(p.game_id) ?? null} onOpen={() => navigate(gameHref(p.sport, p.game_id))} />)}
                {!open.length ? <li className="sl-empty">No open paper wagers.</li> : null}
              </ul>
              <div className="rc-totals"><span><i className="tone-green">●</i>Open Wagers<b>{open.length}</b></span><span><i className="tone-gold">$</i>Total Risk<b>{usd(exposure)}</b></span><span><i className="tone-teal">↗</i>Potential Return<b>{usd(potential)}</b></span></div>
              <button type="button" className="rc-cta" onClick={() => { setRes('OPEN'); setSport('ALL'); ledgerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}>View All Active Wagers →</button>
            </section>
          </aside>
        </div>
      </div>
    </div>
  )
}

function Tile({ tone, icon, label, big, sub, bigCls = '' }: { tone: string; icon: string; label: string; big: string; sub: ReactNode; bigCls?: string }) {
  return <div className={`sl-kpi rc-kpi tone-${tone}`}><i className="sl-kpi__icon" aria-hidden="true">{icon}</i><span className="sl-kpi__text"><small>{label}</small><b className={bigCls}>{big}</b><em>{sub}</em></span></div>
}

function BankChart({ series, base, posById }: { series: BankrollPoint[]; base: number; posById: Map<string, Pos> }) {
  const [hv, setHv] = useState<number | null>(null)
  const W = 900; const H = 300; const L = 58; const R = 14; const T = 14; const B = 30
  if (series.length < 2) return <p className="sl-empty">Not enough settled bets in this range to draw the line.</p>
  const t0 = Date.parse(series[0].at!); const t1 = Date.parse(series[series.length - 1].at!)
  const vals = series.map((p) => p.bankroll)
  const lo = Math.min(base, ...vals); const hi = Math.max(base, ...vals); const pad = Math.max(5, (hi - lo) * 0.08)
  const X = (t: number) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - R)
  const Y = (v: number) => T + (1 - (v - (lo - pad)) / (hi - lo + 2 * pad)) * (H - T - B)
  const pts = series.map((p) => ({ x: X(Date.parse(p.at!)), y: Y(p.bankroll), p }))
  const line = pts.map((q, i) => `${i ? 'L' : 'M'}${q.x.toFixed(1)},${q.y.toFixed(1)}`).join(' ')
  const area = `${line} L${pts[pts.length - 1].x.toFixed(1)},${Y(base).toFixed(1)} L${pts[0].x.toFixed(1)},${Y(base).toFixed(1)} Z`
  // drawdown: the gap between the running peak and the bankroll
  let peak = -Infinity
  const peaks = pts.map((q) => { peak = Math.max(peak, q.p.bankroll); return Y(peak) })
  const dd = `M${pts.map((q, i) => `${q.x.toFixed(1)},${peaks[i].toFixed(1)}`).join(' L')} L${[...pts].reverse().map((q) => `${q.x.toFixed(1)},${q.y.toFixed(1)}`).join(' L')} Z`
  const ticks = Array.from({ length: 5 }, (_, i) => lo - pad + ((hi - lo + 2 * pad) * i) / 4)
  const days = Math.max(1, Math.round((t1 - t0) / 86_400_000))
  const step = days > 60 ? 14 : days > 21 ? 7 : days > 7 ? 2 : 1
  const xt: number[] = []
  for (let t = t0; t <= t1; t += step * 86_400_000) xt.push(t)
  const onMove = (e: MouseEvent<SVGSVGElement>) => { const r = e.currentTarget.getBoundingClientRect(); const x = ((e.clientX - r.left) * W) / r.width; let best = 0; let bd = Infinity; pts.forEach((q, i) => { const d = Math.abs(q.x - x); if (d < bd) { bd = d; best = i } }); setHv(best) }
  const h = hv != null ? pts[hv] : null
  const hp = h?.p.position_id ? posById.get(h.p.position_id) : null
  return (
    <div className="rc-chartwrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="rc-svg" onMouseMove={onMove} onMouseLeave={() => setHv(null)} role="img" aria-label="Bankroll over time">
        <defs>
          <clipPath id="rc-above"><rect x="0" y="0" width={W} height={Y(base)} /></clipPath>
          <clipPath id="rc-below"><rect x="0" y={Y(base)} width={W} height={H} /></clipPath>
          <linearGradient id="rc-g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#34d399" stopOpacity=".45" /><stop offset="1" stopColor="#34d399" stopOpacity="0" /></linearGradient>
          <linearGradient id="rc-r" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stopColor="#f87171" stopOpacity=".45" /><stop offset="1" stopColor="#f87171" stopOpacity="0" /></linearGradient>
        </defs>
        {ticks.map((v) => <g key={v}><line x1={L} x2={W - R} y1={Y(v)} y2={Y(v)} stroke="rgba(255,255,255,.07)" /><text x={L - 8} y={Y(v) + 4} textAnchor="end" className="rc-ax">{usd(Math.round(v))}</text></g>)}
        {xt.map((t) => <text key={t} x={X(t)} y={H - 8} textAnchor="middle" className="rc-ax">{new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'America/New_York' }).format(new Date(t))}</text>)}
        <path d={area} fill="url(#rc-g)" clipPath="url(#rc-above)" />
        <path d={area} fill="url(#rc-r)" clipPath="url(#rc-below)" />
        <path d={dd} fill="rgba(248,113,113,.14)" />
        <line x1={L} x2={W - R} y1={Y(base)} y2={Y(base)} stroke="rgba(255,255,255,.55)" strokeDasharray="5 5" />
        <path d={line} fill="none" stroke="#34d399" strokeWidth="2.2" clipPath="url(#rc-above)" />
        <path d={line} fill="none" stroke="#f87171" strokeWidth="2.2" clipPath="url(#rc-below)" />
        {pts.map((q, i) => (q.p.kind === 'SETTLEMENT' && pts.length < 400 ? <circle key={i} cx={q.x} cy={q.y} r="2.6" fill={(q.p.change ?? 0) > 0 ? '#34d399' : (q.p.change ?? 0) < 0 ? '#f87171' : '#cbd5e1'} /> : null))}
        {h ? <g><line x1={h.x} x2={h.x} y1={T} y2={H - B} stroke="rgba(255,255,255,.4)" /><circle cx={h.x} cy={h.y} r="6" fill="#fff" stroke="#0b1526" strokeWidth="2" /></g> : null}
        <text x={pts[pts.length - 1].x - 4} y={pts[pts.length - 1].y - 10} textAnchor="end" className="rc-last">{usd(pts[pts.length - 1].p.bankroll)}</text>
      </svg>
      {h ? <div className="rc-tip" style={{ left: `${(h.x / W) * 100}%`, top: `${(h.y / H) * 100}%` }}><b>{usd(h.p.bankroll)}</b><span className={(h.p.change ?? 0) >= 0 ? 'is-up' : 'is-down'}>{h.p.change != null ? usdS(h.p.change) : h.p.kind}</span><small>{hp ? `${hp.selection} · ${hp.sport}` : h.p.kind === 'FUNDING' ? 'Funding' : ''}</small><small>{etClock(h.p.at, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</small></div> : null}
      <div className="rc-legend"><span><i style={{ background: '#34d399' }} />Bankroll above start</span><span><i style={{ background: '#f87171' }} />Below start</span><span><i style={{ background: 'rgba(248,113,113,.4)' }} />Drawdown from peak</span><span><b className="is-up">●</b> Win <b className="is-down">●</b> Loss <b>●</b> Push</span><span>- - - Starting bankroll</span></div>
    </div>
  )
}

function CompareChart({ ledger, since }: { ledger: ReturnType<typeof usePulse>['ledger']; since: string }) {
  const W = 900; const H = 300; const L = 58; const R = 60; const T = 14; const B = 30
  const lines = (ledger?.sports ?? []).map((s) => {
    let acc = 0
    const pts = s.series.filter((p) => p.at && p.kind === 'SETTLEMENT' && (!since || dayOf(p.at) >= since)).map((p) => { acc += p.change ?? 0; return { t: Date.parse(p.at!), v: acc } })
    return { sport: s.sport, pts }
  }).filter((x) => x.pts.length > 1)
  if (!lines.length) return <p className="sl-empty">Not enough settled bets in this range.</p>
  const t0 = Math.min(...lines.map((l) => l.pts[0].t)); const t1 = Math.max(...lines.map((l) => l.pts[l.pts.length - 1].t))
  const lo = Math.min(0, ...lines.flatMap((l) => l.pts.map((p) => p.v))); const hi = Math.max(0, ...lines.flatMap((l) => l.pts.map((p) => p.v)))
  const X = (t: number) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - R)
  const Y = (v: number) => T + (1 - (v - lo) / Math.max(1, hi - lo)) * (H - T - B)
  return (
    <div className="rc-chartwrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="rc-svg" role="img" aria-label="Profit and loss by sport over time">
        {[lo, (lo + hi) / 2, hi].map((v) => <g key={v}><line x1={L} x2={W - R} y1={Y(v)} y2={Y(v)} stroke="rgba(255,255,255,.07)" /><text x={L - 8} y={Y(v) + 4} textAnchor="end" className="rc-ax">{usdS(v)}</text></g>)}
        <line x1={L} x2={W - R} y1={Y(0)} y2={Y(0)} stroke="rgba(255,255,255,.5)" strokeDasharray="5 5" />
        {lines.map((l) => <g key={l.sport}><path d={l.pts.map((p, i) => `${i ? 'L' : 'M'}${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ')} fill="none" stroke={COLOR[l.sport]} strokeWidth="2.4" /><text x={X(l.pts[l.pts.length - 1].t) + 6} y={Y(l.pts[l.pts.length - 1].v) + 4} className="rc-ax" fill={COLOR[l.sport]} style={{ fill: COLOR[l.sport], fontWeight: 900 }}>{l.sport} {usdS(l.pts[l.pts.length - 1].v)}</text></g>)}
      </svg>
      <div className="rc-legend">{lines.map((l) => <span key={l.sport}><i style={{ background: COLOR[l.sport] }} />{l.sport}</span>)}<span>cumulative profit since the start of the range</span></div>
    </div>
  )
}

type SportStat = ReturnType<typeof stats> & { s: (typeof SPORTS)[number] }
function Coach({ all, bySport, settled, onSport }: { all: Pos[]; bySport: SportStat[]; settled: Pos[]; onSport: (s: SportCode) => void }) {
  const cards: Array<{ tone: string; icon: string; title: string; text: string; action?: { label: string; run: () => void } }> = []
  const ranked = bySport.filter((x) => x.n >= 8 && x.roi != null).sort((a, b) => (b.roi ?? 0) - (a.roi ?? 0))
  const best = ranked[0]
  if (best && (best.roi ?? 0) > 0) cards.push({ tone: 'green', icon: '↗', title: `${best.s.label} is the strongest edge`, text: `${pctS(best.win)} win rate and ${sgn((best.roi ?? 0) * 100, 1, '%')} ROI over ${best.n} bets. Keep the sizing steady while the sample grows: ${best.n} bets is still noisy.`, action: { label: `View ${best.s.label} bets`, run: () => onSport(best.s.key) } })
  const worst = ranked[ranked.length - 1]
  if (worst && worst !== best && (worst.roi ?? 0) < 0) cards.push({ tone: 'red', icon: '⚠', title: `${worst.s.label} is costing money`, text: `${pctS(worst.win)} win rate and ${sgn((worst.roi ?? 0) * 100, 1, '%')} ROI over ${worst.n} bets (${usdS(worst.pl)}). Worth checking where the model is behind the market before adding more ${worst.s.label} volume.`, action: { label: `View ${worst.s.label} bets`, run: () => onSport(worst.s.key) } })
  // losing streaks and what happened to stake size after a loss
  let run = 0; let maxRun = 0
  const afterLoss: number[] = []; const afterWin: number[] = []
  settled.forEach((p, i) => { const r = resultOf(p); if (r === 'LOSS') { run += 1; maxRun = Math.max(maxRun, run) } else if (r === 'WIN') run = 0; const nxt = settled[i + 1]; if (nxt?.stake) (r === 'LOSS' ? afterLoss : r === 'WIN' ? afterWin : []).push(nxt.stake) })
  const mean = (a: number[]) => (a.length ? a.reduce((t, v) => t + v, 0) / a.length : null)
  const aL = mean(afterLoss); const aW = mean(afterWin)
  if (maxRun >= 3 && aL != null && aW != null) cards.push({ tone: 'gold', icon: '≡', title: 'Stake discipline after losses', text: `Longest losing run: ${maxRun}. Average stake after a loss ${usd(aL)} vs ${usd(aW)} after a win${aL > aW * 1.05 ? ' — stakes rise after losses, the classic chase pattern.' : aL < aW * 0.95 ? ' — stakes shrink after losses, as a fractional policy should.' : ' — sizing stays flat, so streaks are not being chased.'}` })
  // same-slate clusters (correlated exposure)
  const slots = new Map<string, Pos[]>()
  for (const p of all) { if (!p.starts_at) continue; const k = `${p.sport}|${p.starts_at}`; slots.set(k, [...(slots.get(k) ?? []), p]) }
  const big = [...slots.values()].filter((l) => l.length >= 3)
  if (big.length) { const ps = big.flat(); const s = stats(ps); cards.push({ tone: 'blue', icon: '⛓', title: 'Clustered kickoffs', text: `${big.length} time slots carried 3+ bets in the same sport at once (${ps.length} bets, ${s.w}-${s.l}, ${usdS(s.pl)}). Bets that kick off together tend to move together, which widens the swings.` }) }
  if (!cards.length) cards.push({ tone: 'blue', icon: '✦', title: 'Coach is warming up', text: 'Observations appear once there are enough settled bets in the range.' })
  return (
    <section className="sl-card rc-coach" aria-label="AISP Coach">
      <header className="sl-card__head"><h2>✦ AISP Coach</h2><small className="rc-sub">Observations drawn from this record</small></header>
      <div className="rc-coach__cards">
        {cards.slice(0, 4).map((c) => (
          <article key={c.title} className={`rc-cc tone-${c.tone}`}>
            <i aria-hidden="true">{c.icon}</i>
            <h3>{c.title}</h3>
            <p>{c.text}</p>
            {c.action ? <button type="button" onClick={c.action.run}>{c.action.label} →</button> : null}
          </article>
        ))}
      </div>
    </section>
  )
}

function CoachMe({ stats: s }: { stats: Record<string, unknown> }) {
  const [state, setState] = useState<{ busy: boolean; answer: string | null; error: string | null }>({ busy: false, answer: null, error: null })
  const ask = async () => {
    setState({ busy: true, answer: null, error: null })
    try {
      const res = await fetch('/preview-api/booth', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ question: 'Read this paper betting record back to me like a coach: where is it strongest, where is it struggling, and what does the data say about stake sizing? Do not tell me to place any bet.', context: { page: 'AISP Record (paper experiment, no real money)', ...s } }) })
      const type = res.headers.get('content-type') ?? ''
      if (!type.includes('json')) throw new Error('The coach runs in test mode only.')
      const body = (await res.json()) as { answer?: string; error?: string }
      if (!res.ok || !body.answer) throw new Error(body.error ?? `HTTP ${res.status}`)
      setState({ busy: false, answer: body.answer, error: null })
    } catch (e) { setState({ busy: false, answer: null, error: e instanceof Error ? e.message : 'The coach did not answer.' }) }
  }
  useEffect(() => undefined, [])
  return (
    <div className="rc-coachme">
      <button type="button" onClick={ask} disabled={state.busy}><i aria-hidden="true">✦</i>{state.busy ? 'Thinking…' : 'Coach Me'}</button>
      <small>{state.answer ?? state.error ?? 'AI read of your betting approach'}</small>
    </div>
  )
}

function ActiveWager({ p, g, onOpen }: { p: Pos; g: PulseGame | null; onOpen: () => void }) {
  const live = g ? g.phase === 'LIVE' || g.phase === 'STARTED' : false
  const t = teamsOf(p.matchup)
  const toWin = (p.stake ?? 0) * ((p.entry.decimal ?? 1) - 1)
  return (
    <li style={{ ['--sc' as string]: COLOR[p.sport] } as CSSProperties}>
      <button type="button" onClick={onOpen}>
        <span className="rc-w__top"><em>{p.sport}</em><b className={live ? 'is-live' : ''}>{live ? (g?.score ? `Live ${g.score.away}-${g.score.home}` : 'Live') : 'Upcoming'}</b></span>
        <span className="rc-w__match">{g ? `${g.away.abbr} @ ${g.home.abbr}` : `${t.away} @ ${t.home}`}</span>
        <span className="rc-w__sel">{p.selection} <b className="is-up">{p.entry.american != null ? american(p.entry.american) : ''}</b></span>
        <span className="rc-w__money"><b>{usd(p.stake)}</b><em>To win {usd(toWin)}</em></span>
        <span className="rc-w__foot"><small>{p.entry.sportsbook ?? ''}</small><small>{etClock(p.starts_at, { weekday: 'short', month: 'short', day: 'numeric' })} · {etClock(p.starts_at)} ET</small></span>
      </button>
    </li>
  )
}
