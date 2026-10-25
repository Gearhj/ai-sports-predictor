/**
 * BEAT THE CLOSE (borrowed from Pikkit / Leans / Unabated, built on AISP's own data). Every paper bet AISP placed is
 * graded against the market's final word: the closing no-vig consensus across books (median of each book's last
 * moneyline at or before the start). A bet "beats the close" when the price AISP took pays more than the closing fair
 * price; over many bets that is the most trusted sign of real skill, ahead of the win-loss record.
 * Reads /preview-api/insights/closing (test mode, read-only).
 */
import { useMemo, useState, type CSSProperties } from 'react'
import { SPORT_COLOR, americanFromDec, am, signed, usePreviewJson, type ClvRow } from './insights'
import './edge.css'

export function BeatTheClose() {
  const { data, error } = usePreviewJson<{ positions: ClvRow[]; method: string }>('/preview-api/insights/closing')
  const [sport, setSport] = useState<string>('ALL')
  const [hover, setHover] = useState<ClvRow | null>(null)
  const settled = useMemo(() => (data?.positions ?? []).filter((r) => r.final_close), [data])
  // Only bets with a price captured AFTER the bet can be graded against the close; the rest have no closing capture.
  const rows = useMemo(() => settled.filter((r) => r.close_after_bet).sort((a, b) => a.starts_at.localeCompare(b.starts_at)), [settled])
  const sports = Array.from(new Set(rows.map((r) => r.sport))).sort()
  const shown = rows.filter((r) => sport === 'ALL' || r.sport === sport)
  if (error) return <section className="ed-card"><h2>Beat the Close</h2><p className="ed-muted">The closing-line feed is only on the test-mode preview ({error}).</p></section>
  if (!data) return <section className="ed-card"><h2>Beat the Close</h2><p className="ed-muted">Grading every AISP bet against the closing line…</p></section>
  if (!shown.length) return null
  const beat = shown.filter((r) => r.clv_pct > 0).length
  const avg = shown.reduce((s, r) => s + r.clv_pct, 0) / shown.length
  const same = shown.filter((r) => r.beat_same_book != null)
  const sameBeat = same.filter((r) => r.beat_same_book).length
  const W = 760; const H = 200; const pad = 30
  const maxAbs = Math.max(6, ...shown.map((r) => Math.min(25, Math.abs(r.clv_pct))))
  const bw = (W - pad * 2) / shown.length
  const Y = (v: number) => H / 2 - (Math.max(-25, Math.min(25, v)) / maxAbs) * (H / 2 - 12)
  const roll = shown.map((_, i) => { const w = shown.slice(Math.max(0, i - 19), i + 1); return w.reduce((s, r) => s + r.clv_pct, 0) / w.length })
  const by = sports.map((s) => { const r = rows.filter((x) => x.sport === s); return { s, n: r.length, beat: r.filter((x) => x.clv_pct > 0).length, avg: r.reduce((a, x) => a + x.clv_pct, 0) / Math.max(1, r.length) } })
  const verdict = beat / shown.length >= 0.55 && avg > 0 ? 'AISP is beating the market\'s final price more often than not.' : beat / shown.length >= 0.45 ? 'AISP is landing close to the market\'s final price: no real edge either way yet.' : 'The market\'s final price has usually been better than the one AISP took.'
  return (
    <section className="ed-card ed-btc" aria-label="Beat the Close">
      <header className="ed-head">
        <div><h2>Beat the Close</h2><p className="ed-muted">Did AISP get a better price than the market's final word? The most trusted test of real skill, graded on every paper bet.</p></div>
        <span className="ed-seg">{['ALL', ...sports].map((s) => <button key={s} type="button" className={sport === s ? 'is-on' : ''} onClick={() => setSport(s)}>{s === 'ALL' ? 'All' : s}</button>)}</span>
      </header>
      <div className="ed-kpis">
        <span className={beat / shown.length >= 0.5 ? 'is-good' : 'is-bad'}><small>Beat the close</small><b>{Math.round((beat / shown.length) * 100)}%</b><em>{beat} of {shown.length} bets</em></span>
        <span className={avg >= 0 ? 'is-good' : 'is-bad'}><small>Average closing-line value</small><b>{signed(avg, 2, '%')}</b><em>price taken vs closing fair price</em></span>
        <span><small>Beat the same book's closing price</small><b>{same.length ? `${Math.round((sameBeat / same.length) * 100)}%` : '—'}</b><em>{sameBeat} of {same.length} where the same book was captured later</em></span>
      </div>
      <div className="ed-chart">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Closing-line value of each bet">
          <line x1={pad} x2={W - pad} y1={H / 2} y2={H / 2} className="ed-zero" />
          <text x={pad} y={14} className="ed-axis">beat the close ↑</text><text x={pad} y={H - 4} className="ed-axis">worse than the close ↓</text>
          {shown.map((r, i) => {
            const x = pad + i * bw; const y = Y(r.clv_pct)
            return <rect key={r.id} x={x + bw * 0.12} width={Math.max(1, bw * 0.76)} y={Math.min(y, H / 2)} height={Math.max(1, Math.abs(y - H / 2))} className={r.clv_pct >= 0 ? 'ed-bar is-up' : 'ed-bar is-down'} style={{ ['--sc' as string]: SPORT_COLOR[r.sport] ?? '#94a3b8' } as CSSProperties} onMouseEnter={() => setHover(r)} onMouseLeave={() => setHover(null)} />
          })}
          <polyline points={roll.map((v, i) => `${(pad + (i + 0.5) * bw).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')} className="ed-roll" />
        </svg>
        <p className="ed-hover">{hover ? <>{hover.sport} · {new Date(hover.starts_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} · {hover.selection ?? ''} at {hover.book ?? 'book'}: took <b>{am(americanFromDec(hover.entry_decimal))}</b>, closing fair <b>{hover.close_fair_decimal ? am(americanFromDec(hover.close_fair_decimal)) : '—'}</b> → <b className={hover.clv_pct >= 0 ? 'is-good' : 'is-bad'}>{signed(hover.clv_pct, 1, '%')}</b>{hover.result ? ` · ${hover.result}` : ''}</> : 'Each bar is one bet, oldest to newest; the gold line is the 20-bet average. Hover a bar.'}</p>
      </div>
      <div className="ed-split">
        <table className="ed-table"><thead><tr><th>Sport</th><th>Bets</th><th>Beat the close</th><th>Avg CLV</th></tr></thead>
          <tbody>{by.map((b) => <tr key={b.s}><th style={{ color: SPORT_COLOR[b.s] }}>{b.s}</th><td>{b.n}</td><td className={b.beat / b.n >= 0.5 ? 'is-good' : 'is-bad'}>{Math.round((b.beat / b.n) * 100)}%</td><td className={b.avg >= 0 ? 'is-good' : 'is-bad'}>{signed(b.avg, 2, '%')}</td></tr>)}</tbody></table>
        <p className="ed-read"><b>{verdict}</b> Graded {rows.length} of {settled.length} bets: the other {settled.length - rows.length} have no price captured after the bet (AISP stops collecting once it decides), so their close is unknown. Capturing one more price a few minutes before each start would grade every bet; that uses Odds API credit, so it waits on James's yes. Closing-line value cares about the price, not the result: a bet can lose and still beat the close. Over hundreds of bets, beating the close is what separates skill from luck. Method: {data.method}.</p>
      </div>
    </section>
  )
}
