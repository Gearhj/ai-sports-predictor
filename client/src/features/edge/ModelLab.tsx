/**
 * MODEL LAB (borrowed from Rithmm's build-your-own-model, done the AISP way: on real, settled history).
 *
 * Three dials build a model out of AISP's ingredients: how much to trust AISP's model versus the market's closing
 * price, how much caution to add (pull every probability toward 50/50), and how big an edge you need before you bet.
 * The lab replays every decided game at once and shows what that model would have done: forecast quality against the
 * market, bets, win rate, profit and a bankroll line. THE SWEET SPOT (the HOLY SHIT moment) draws forecast quality for
 * every blend from 0% to 100% AISP, so the best mix is visible at a glance.
 *
 * Reads /preview-api/insights/lab (test mode, read-only). The market side is the CLOSING no-vig consensus and bets are
 * settled at the best closing price across books, both of which flatter any backtest; the page says so. Play money,
 * research only, never advice or a wager signal.
 */
import { useMemo, useState, type CSSProperties } from 'react'
import { AispSide } from '../flow/AispSide'
import { signed, usePreviewJson, type LabRow } from './insights'
import '../slate/slate.css'
import './edge.css'

const clamp = (p: number) => Math.min(1 - 1e-6, Math.max(1e-6, p))
const blend = (r: LabRow, w: number, caution: number) => { const p = w * r.model_home + (1 - w) * r.market_home; return 0.5 + (p - 0.5) * (1 - caution) }
function score(rows: LabRow[], w: number, caution: number) {
  let ll = 0; let br = 0
  rows.forEach((r) => { const p = clamp(blend(r, w, caution)); const y = r.home_won ? 1 : 0; ll -= y * Math.log(p) + (1 - y) * Math.log(1 - p); br += (p - y) ** 2 })
  return { ll: ll / Math.max(1, rows.length), brier: br / Math.max(1, rows.length) }
}

export function ModelLab() {
  const { data, error } = usePreviewJson<{ rows: LabRow[]; method: string }>('/preview-api/insights/lab?days=120')
  const [sport, setSport] = useState('ALL')
  const [w, setW] = useState(0.5)
  const [caution, setCaution] = useState(0)
  const [minEdge, setMinEdge] = useState(3)
  const [stake, setStake] = useState<'flat' | 'kelly'>('flat')
  const all = data?.rows ?? []
  const sports = Array.from(new Set(all.map((r) => r.sport))).sort()
  const rows = useMemo(() => all.filter((r) => sport === 'ALL' || r.sport === sport), [all, sport])
  const curve = useMemo(() => Array.from({ length: 21 }, (_, i) => ({ w: i / 20, ...score(rows, i / 20, caution) })), [rows, caution])
  const best = curve.reduce((a, b) => (b.ll < a.ll ? b : a), curve[0] ?? { w: 0, ll: 0, brier: 0 })
  const mine = score(rows, w, caution)
  const market = score(rows, 0, 0)
  const aisp = score(rows, 1, 0)
  const sim = useMemo(() => {
    let bank = 1000; let bets = 0; let wins = 0; let staked = 0
    const line: Array<{ date: string; bank: number }> = [{ date: rows[0]?.date ?? '', bank }]
    rows.forEach((r) => {
      const p = blend(r, w, caution)
      const opts = ([['HOME', p, r.close_home_dec], ['AWAY', 1 - p, r.close_away_dec]] as Array<[string, number, number | null]>).filter((o) => o[2] && o[2] > 1)
      const pick = opts.map(([side, prob, dec]) => ({ side, prob, dec: dec!, edge: (prob - 1 / dec!) * 100 })).sort((a, b) => b.edge - a.edge)[0]
      if (!pick || pick.edge < minEdge) return
      const k = (pick.prob * pick.dec - 1) / (pick.dec - 1)
      const amt = stake === 'flat' ? 10 : Math.max(0, Math.min(bank * 0.05, bank * 0.25 * k))
      if (amt <= 0) return
      const won = (pick.side === 'HOME') === r.home_won
      bets += 1; staked += amt; if (won) wins += 1
      bank += won ? amt * (pick.dec - 1) : -amt
      line.push({ date: r.date, bank })
    })
    return { bank, bets, wins, staked, line }
  }, [rows, w, caution, minEdge, stake])
  const profit = sim.bank - 1000

  return (
    <div className="hq ed-lab">
      <AispSide />
      <div className="hq-main">
        <header className="sl-top"><div className="sl-title"><small>Research · Model Lab</small><h1>Build Your Own Model</h1><p>Blend AISP's model with the market, add caution, choose when to bet, and replay every decided game.</p></div></header>
        {error ? <p className="sl-empty">The lab's history is only on the test-mode preview ({error}).</p> : !data ? <p className="sl-empty">Loading every decided game…</p> : (
          <div className="ed-lab__body">
            <section className="ed-card ed-dials" aria-label="Your model">
              <h2>Your model</h2>
              <span className="ed-seg">{['ALL', ...sports].map((s) => <button key={s} type="button" className={sport === s ? 'is-on' : ''} onClick={() => setSport(s)}>{s === 'ALL' ? `All (${all.length})` : `${s} (${all.filter((r) => r.sport === s).length})`}</button>)}</span>
              <label className="ed-dial"><span><b>Trust in AISP's model</b><em>{Math.round(w * 100)}% AISP · {Math.round((1 - w) * 100)}% market</em></span><input id="lab-w" type="range" min="0" max="1" step="0.05" value={w} onChange={(e) => setW(Number(e.target.value))} /></label>
              <label className="ed-dial"><span><b>Caution</b><em>pull every probability {Math.round(caution * 100)}% toward 50/50</em></span><input id="lab-c" type="range" min="0" max="0.5" step="0.05" value={caution} onChange={(e) => setCaution(Number(e.target.value))} /></label>
              <label className="ed-dial"><span><b>Only bet when the edge is at least</b><em>{minEdge} points over the price</em></span><input id="lab-e" type="range" min="0" max="12" step="0.5" value={minEdge} onChange={(e) => setMinEdge(Number(e.target.value))} /></label>
              <span className="ed-seg"><button type="button" className={stake === 'flat' ? 'is-on' : ''} onClick={() => setStake('flat')}>Flat $10</button><button type="button" className={stake === 'kelly' ? 'is-on' : ''} onClick={() => setStake('kelly')}>¼ Kelly, max 5%</button></span>
              <button type="button" className="ed-best" onClick={() => setW(best.w)}>Jump to the sweet spot ({Math.round(best.w * 100)}% AISP)</button>
            </section>

            <section className="ed-card ed-sweet" aria-label="The Sweet Spot">
              <h2>The Sweet Spot <small>forecast error for every blend, lower is better</small></h2>
              <Sweet curve={curve} w={w} best={best} onPick={setW} />
              <p className="ed-read">{best.w === 0 ? <>On these {rows.length} games the <b>market alone</b> forecast best; adding AISP's model only added error.</> : best.w === 1 ? <>On these {rows.length} games <b>AISP's model alone</b> forecast best.</> : <>On these {rows.length} games the best forecast was <b>{Math.round(best.w * 100)}% AISP, {Math.round((1 - best.w) * 100)}% market</b>, better than either alone.</>} Log loss: market {market.ll.toFixed(4)}, AISP {aisp.ll.toFixed(4)}, yours {mine.ll.toFixed(4)}.</p>
            </section>

            <section className="ed-card ed-result" aria-label="How your model did">
              <h2>How your model would have done</h2>
              <div className="ed-kpis">
                <span className={mine.ll <= market.ll ? 'is-good' : 'is-bad'}><small>Forecast vs the market</small><b>{signed((market.ll - mine.ll) * 1000, 1)}</b><em>log loss × 1000 better than market</em></span>
                <span><small>Bets</small><b>{sim.bets}</b><em>{sim.bets ? `${Math.round((sim.wins / sim.bets) * 100)}% won` : 'raise or lower the edge dial'}</em></span>
                <span className={profit >= 0 ? 'is-good' : 'is-bad'}><small>Play-money profit</small><b>{signed(profit, 0, '')}</b><em>{sim.staked ? `${signed((profit / sim.staked) * 100, 1, '%')} ROI on $${Math.round(sim.staked)}` : 'no bets'}</em></span>
              </div>
              <Bank line={sim.line} />
              <p className="ed-foot">Honest limits: the market side is the last no-vig consensus AISP captured before the start (usually around decision time), and bets settle at the best of those prices across books, so real results would be somewhat worse. AISP's model probability is from its last pregame decision. {data.method}.</p>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}

function Sweet({ curve, w, best, onPick }: { curve: Array<{ w: number; ll: number }>; w: number; best: { w: number; ll: number }; onPick: (w: number) => void }) {
  const W = 640; const H = 220; const pad = 40
  const lo = Math.min(...curve.map((c) => c.ll)); const hi = Math.max(...curve.map((c) => c.ll))
  const X = (v: number) => pad + v * (W - pad * 2)
  const Y = (v: number) => H - 30 - ((v - lo) / Math.max(1e-6, hi - lo)) * (H - 60)
  const near = curve.reduce((a, b) => (Math.abs(b.w - w) < Math.abs(a.w - w) ? b : a), curve[0])
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="ed-sweet__svg" role="img" aria-label="Forecast error by blend">
      <defs><linearGradient id="ed-sweet-g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="rgba(45,212,191,.35)" /><stop offset="1" stopColor="rgba(45,212,191,0)" /></linearGradient></defs>
      {[0, 0.25, 0.5, 0.75, 1].map((v) => <g key={v}><line x1={X(v)} x2={X(v)} y1={18} y2={H - 30} className="ed-grid" /><text x={X(v)} y={H - 12} textAnchor="middle" className="ed-axis">{v === 0 ? 'market only' : v === 1 ? 'AISP only' : `${v * 100}% AISP`}</text></g>)}
      <polygon points={`${X(0)},${H - 30} ${curve.map((c) => `${X(c.w)},${Y(c.ll)}`).join(' ')} ${X(1)},${H - 30}`} fill="url(#ed-sweet-g)" />
      <polyline points={curve.map((c) => `${X(c.w)},${Y(c.ll)}`).join(' ')} className="ed-sweet__line" />
      {curve.map((c) => <circle key={c.w} cx={X(c.w)} cy={Y(c.ll)} r="9" className="ed-sweet__hit" onClick={() => onPick(c.w)}><title>{Math.round(c.w * 100)}% AISP · log loss {c.ll.toFixed(4)}</title></circle>)}
      <g className="ed-sweet__best"><circle cx={X(best.w)} cy={Y(best.ll)} r="8" /><text x={X(best.w)} y={Y(best.ll) - 14} textAnchor="middle">SWEET SPOT</text></g>
      <g className="ed-sweet__you" style={{ ['--x' as string]: `${X(near.w)}px` } as CSSProperties}><line x1={X(near.w)} x2={X(near.w)} y1={18} y2={H - 30} /><circle cx={X(near.w)} cy={Y(near.ll)} r="6" /><text x={X(near.w) + 8} y={30}>you</text></g>
    </svg>
  )
}

function Bank({ line }: { line: Array<{ date: string; bank: number }> }) {
  if (line.length < 2) return <p className="ed-muted">No bets at these settings.</p>
  const W = 640; const H = 150; const pad = 30
  const lo = Math.min(1000, ...line.map((p) => p.bank)); const hi = Math.max(1000, ...line.map((p) => p.bank))
  const X = (i: number) => pad + (i / (line.length - 1)) * (W - pad * 2)
  const Y = (v: number) => H - 16 - ((v - lo) / Math.max(1, hi - lo)) * (H - 32)
  const end = line[line.length - 1].bank
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="ed-bank" role="img" aria-label="Play-money bankroll">
      <line x1={pad} x2={W - pad} y1={Y(1000)} y2={Y(1000)} className="ed-zero" /><text x={pad} y={Y(1000) - 4} className="ed-axis">$1,000 start</text>
      <polyline points={line.map((p, i) => `${X(i).toFixed(1)},${Y(p.bank).toFixed(1)}`).join(' ')} className={end >= 1000 ? 'ed-bank__line is-up' : 'ed-bank__line is-down'} />
      <text x={W - pad} y={Y(end) - 6} textAnchor="end" className="ed-axis">${Math.round(end).toLocaleString()}</text>
      <text x={pad} y={H - 2} className="ed-axis">{line[1].date}</text><text x={W - pad} y={H - 2} textAnchor="end" className="ed-axis">{line[line.length - 1].date}</text>
    </svg>
  )
}

export default ModelLab
