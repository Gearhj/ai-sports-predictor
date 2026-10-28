/**
 * The tracker and the scorecard for MY MODEL. `useModelTracker` runs wherever it is mounted (Game Night, Focus):
 * it writes the saved model's prediction for every game of a modelled sport before it starts (with the value side at
 * the best US book when a price exists) and settles finished games from the final score. `ModelRecordCard` shows the
 * head-to-head: your model against AISP and the market on the same games.
 */
import { useEffect, useMemo, useState } from 'react'
import { useMarketCoverage } from '../../services/commandCenter'
import { operationalToday, shiftDate } from '../../services/operationalDate'
import { usd, usePulse } from '../pulse/pulseData'
import { isUsBook } from '../edge/MarketEdge'
import { availabilityFor, useAvailability } from '../focus/FocusExtras'
import { LOG_EVENT, logPredictions, readLog, scorecard, settle, valueSide, type LogEntry } from './modelLog'
import { MODEL_EVENT, applyModel, loadModel, loadReads, type MyModel } from './myModel'
import './lab.css'

type CovQuote = { market?: string; sportsbook: string; fresh?: boolean; sides?: Record<string, { american?: number | null; decimal?: number | null }> }
type CovRow = { game_id: string; quotes?: CovQuote[] }
function bestUs(row: CovRow | undefined, side: 'HOME' | 'AWAY'): { american: number; book: string } | null {
  let best: { american: number; book: string; dec: number } | null = null
  for (const q of row?.quotes ?? []) {
    const s = q.sides?.[side]
    if (q.market !== 'MONEYLINE' || q.fresh === false || !isUsBook(q.sportsbook) || s?.american == null || !s.decimal) continue
    if (!best || s.decimal > best.dec) best = { american: s.american, book: q.sportsbook, dec: s.decimal }
  }
  return best ? { american: best.american, book: best.book } : null
}

export function useModelTracker(): void {
  const today = operationalToday()
  const pulse = usePulse(today)
  const cov = useMarketCoverage(today)
  const avail = useAvailability(today)
  const availNext = useAvailability(shiftDate(today, 1))
  const [m, setM] = useState<MyModel | null>(loadModel)
  useEffect(() => { const on = () => setM(loadModel()); window.addEventListener(MODEL_EVENT, on); return () => window.removeEventListener(MODEL_EVENT, on) }, [])
  useEffect(() => {
    if (!m || !pulse.games.length) return
    const rows = (cov.data?.games ?? []) as unknown as CovRow[]
    const board = [...(avail.data?.games ?? []), ...(availNext.data?.games ?? [])]
    const entries: Omit<LogEntry, 'logged_at' | 'result'>[] = []
    for (const g of pulse.games) {
      const f = m.sports[g.sport]
      if (!f || g.modelHome == null || g.phase !== 'UPCOMING') continue
      const a = availabilityFor(g, board)
      const out = a ? Object.entries(a.teams).flatMap(([side, t]) => t.key_players.filter((k) => ['OUT', 'DOUBTFUL', 'QUESTIONABLE'].includes(k.status))
        .map((k) => ({ side: (side === 'home' ? 'HOME' : 'AWAY') as 'HOME' | 'AWAY', status: k.status, name: k.name }))) : []
      const market = g.marketFresh ? g.marketHome : null
      const you = applyModel(g.sport, f, g.modelHome, market, out, loadReads()[g.id] ?? null).p
      const row = rows.find((r) => r.game_id === g.id)
      const usOr = (side: 'HOME' | 'AWAY') => bestUs(row, side) ?? ((b) => (b?.american != null && b.book && isUsBook(b.book) ? { american: b.american, book: b.book } : null))(side === 'HOME' ? g.bestHome : g.bestAway)
      entries.push({ game_id: g.id, sport: g.sport, matchup: `${g.away.abbr} @ ${g.home.abbr}`, home: g.home.abbr, away: g.away.abbr, starts_at: g.startsAt,
        version: m.version, you, aisp: g.modelHome, market, value: valueSide(you, { HOME: usOr('HOME'), AWAY: usOr('AWAY') }) })
    }
    logPredictions(entries)
    const finals = new Map<string, boolean>()
    for (const g of pulse.games) if (g.phase === 'FINAL' && g.score && g.score.home !== g.score.away) finals.set(g.id, g.score.home > g.score.away)
    settle(finals)
  }, [m, pulse.games, cov.data, avail.data, availNext.data])
}

export function ModelRecordCard({ compact = false }: { compact?: boolean }) {
  const [rows, setRows] = useState<LogEntry[]>(readLog)
  useEffect(() => { const on = () => setRows(readLog()); window.addEventListener(LOG_EVENT, on); return () => window.removeEventListener(LOG_EVENT, on) }, [])
  const s = useMemo(() => scorecard(rows), [rows])
  if (!rows.length) return null
  const best = Math.min(s.you.logLoss || 9, s.aisp.logLoss || 9, s.market.n ? s.market.logLoss : 9)
  const cell = (label: string, hits: number, n: number, logLoss: number, tone: string) => (
    <span className={`mr-cell mr-cell--${tone}${n && Math.abs(logLoss - best) < 1e-9 ? ' is-best' : ''}`}>
      <small>{label}</small><b>{n ? `${hits}–${n - hits}` : '—'}</b><em>{n ? `log loss ${logLoss.toFixed(3)}` : 'no results yet'}</em>
    </span>
  )
  return (
    <section className={`mr${compact ? ' mr--compact' : ''}`} aria-label="My model's track record">
      <header><b>📈 My model’s track record</b><small>{s.n} settled · {s.pending} waiting on a final · logged before each start, never changed after</small></header>
      {s.n === 0 ? <p className="rp__muted">Your model has called {s.pending} game{s.pending === 1 ? '' : 's'}. Results appear here as they go final.</p> : (
        <>
          <div className="mr-row">
            {cell('YOU', s.you.hits, s.n, s.you.logLoss, 'you')}
            {cell('AISP', s.aisp.hits, s.n, s.aisp.logLoss, 'aisp')}
            {cell('MARKET', s.market.hits, s.market.n, s.market.logLoss, 'mkt')}
          </div>
          <p className="mr-value">Value plays (flat $10 where your number beat the best US price): <b>{s.value.wins}–{s.value.bets - s.value.wins}</b>
            {' '}<b className={s.value.pl >= 0 ? 'is-up' : 'is-down'}>{usd(s.value.pl, true)}</b> on {usd(s.value.staked)}. Lowest log loss wins; small samples are mostly luck.</p>
        </>
      )}
    </section>
  )
}
