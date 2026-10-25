/**
 * VALUE FINDER and MARKET MOVERS (borrowed from OddsJam / Unabated and Action Network's "Sharp Action", built only on
 * odds AISP already collects; no new provider calls).
 *
 * Value Finder: every book price, right now, that pays more than the market's own fair price (the no-vig consensus
 * across books). Expected value = price x fair probability - 1. AISP's model is shown beside it, and a model/market
 * gap over 10 points carries the warning label (more often model error than edge).
 * Market Movers: the no-vig consensus through the day for each game, with STEAM flagged when most books moved the
 * same way, 2+ points, within two hours: the closest free signal to "sharp money" without buying ticket data.
 * Research only: never advice or a wager signal.
 */
import { useMemo, useState, type CSSProperties } from 'react'
import { useMarketCoverage } from '../../services/commandCenter'
import { shiftDate } from '../../services/operationalDate'
import { Crest } from '../flow/Crest'
import { SPORT_COLOR, am, pct, signed, usePreviewJson, type MoverGame } from './insights'
import './edge.css'

type Side = 'HOME' | 'AWAY'
type Quote = { market: string; sportsbook: string; observed_at?: string | null; fresh?: boolean; sides: Partial<Record<Side, { american: number | null; decimal: number | null }>> }
type CovGame = { game_id: string; league: string; matchup: string; home: { abbreviation: string }; away: { abbreviation: string }; starts_at: string; status?: string; consensus_no_vig_home?: number | null; consensus_is_current?: boolean; model?: { home_two_way?: number | null; home?: number | null } | null; quotes?: Quote[] }

export function ValueFinder({ day }: { day: string }) {
  const cov = useMarketCoverage(day)
  const [minEv, setMinEv] = useState(1)
  const now = Date.now()
  const rows = useMemo(() => {
    const out: Array<{ g: CovGame; side: Side; book: string; american: number | null; dec: number; fair: number; ev: number; model: number | null; gap: number | null; fresh: boolean }> = []
    for (const g of ((cov.data?.games ?? []) as unknown as CovGame[])) {
      if (g.status === 'FINAL' || Date.parse(g.starts_at) < now || g.consensus_no_vig_home == null) continue
      const mh = g.model?.home_two_way ?? g.model?.home ?? null
      for (const q of g.quotes ?? []) {
        if (q.market !== 'MONEYLINE') continue
        for (const side of ['HOME', 'AWAY'] as Side[]) {
          const s = q.sides?.[side]; if (!s?.decimal) continue
          const fair = side === 'HOME' ? g.consensus_no_vig_home : 1 - g.consensus_no_vig_home
          const ev = (s.decimal * fair - 1) * 100
          const model = mh == null ? null : side === 'HOME' ? mh : 1 - mh
          out.push({ g, side, book: q.sportsbook, american: s.american, dec: s.decimal, fair, ev, model, gap: model == null ? null : (model - fair) * 100, fresh: q.fresh !== false })
        }
      }
    }
    return out.sort((a, b) => b.ev - a.ev)
  }, [cov.data, now])
  const shown = rows.filter((r) => r.ev >= minEv).slice(0, 14)
  return (
    <section className="ed-card ed-vf" aria-label="Value Finder">
      <header className="ed-head">
        <div><h2>Value Finder</h2><p className="ed-muted">Book prices right now that pay more than the market's own fair price (the no-vig consensus across books).</p></div>
        <span className="ed-seg">{[0.5, 1, 2, 3].map((v) => <button key={v} type="button" className={minEv === v ? 'is-on' : ''} onClick={() => setMinEv(v)}>EV ≥ {v}%</button>)}</span>
      </header>
      {!cov.data ? <p className="ed-muted">Reading today's prices…</p> : !shown.length ? <p className="ed-muted">No upcoming price beats the consensus by {minEv}% or more right now. That is normal: books copy each other fast.</p> : (
        <div className="ed-scroll"><table className="ed-table ed-vf__t">
          <thead><tr><th>Game</th><th>Side</th><th>Book</th><th>Price</th><th>Fair</th><th>Expected value</th><th>AISP model</th></tr></thead>
          <tbody>{shown.map((r, i) => {
            const team = r.side === 'HOME' ? r.g.home.abbreviation : r.g.away.abbreviation
            return (
              <tr key={`${r.g.game_id}-${r.side}-${r.book}-${i}`}>
                <td><span className="ed-game"><Crest sport={r.g.league} abbr={r.g.away.abbreviation} size={20} /><Crest sport={r.g.league} abbr={r.g.home.abbreviation} size={20} /><span><b>{r.g.matchup}</b><small style={{ color: SPORT_COLOR[r.g.league] }}>{r.g.league} · {new Date(r.g.starts_at).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' })}</small></span></span></td>
                <td><b>{team}</b></td>
                <td>{r.book}{r.fresh ? '' : <small className="ed-stale"> stale</small>}</td>
                <td className="ed-num">{am(r.american)}</td>
                <td className="ed-num">{pct(r.fair, 1)}</td>
                <td><span className="ed-ev" style={{ ['--w' as string]: `${Math.min(100, r.ev * 12)}%` } as CSSProperties}><i /><b>{signed(r.ev, 1, '%')}</b></span></td>
                <td>{r.model == null ? '—' : <span className={r.gap != null && Math.abs(r.gap) > 10 ? 'ed-warn' : r.gap != null && r.gap > 0 ? 'is-good' : ''}>{pct(r.model)}{r.gap != null && Math.abs(r.gap) > 10 ? ' · >10-pt gap, warning' : r.gap != null && r.gap > 0 ? ' · agrees' : ' · disagrees'}</span>}</td>
              </tr>
            )
          })}</tbody>
        </table></div>
      )}
      <p className="ed-foot">Fair = consensus with the bookmaker margin removed. Value here is against the market itself, not a prediction; AISP's own model is shown for context. Research only, never a wager signal.</p>
    </section>
  )
}

export function MarketMovers({ day }: { day: string }) {
  const { data, error } = usePreviewJson<{ games: MoverGame[] }>('/preview-api/insights/movers', 120_000)
  const cov = useMarketCoverage(day)
  const prev = useMarketCoverage(shiftDate(day, -1))
  const names = useMemo(() => new Map([...((prev.data?.games ?? []) as unknown as CovGame[]), ...((cov.data?.games ?? []) as unknown as CovGame[])].map((g) => [g.game_id, g])), [cov.data, prev.data])
  const [steamOnly, setSteamOnly] = useState(false)
  const list = (data?.games ?? []).filter((g) => names.has(g.game_id) && (!steamOnly || g.steam.length)).slice(0, 12)
  const steamCount = (data?.games ?? []).filter((g) => g.steam.length).length
  return (
    <section className="ed-card ed-mm" aria-label="Market Movers">
      <header className="ed-head">
        <div><h2>Market Movers</h2><p className="ed-muted">How the market's fair price moved through the day. <b className="ed-steam-word">STEAM</b> = most books moved the same way, 2+ points, inside two hours: the free signal of sharp money.</p></div>
        <span className="ed-seg"><button type="button" className={!steamOnly ? 'is-on' : ''} onClick={() => setSteamOnly(false)}>Biggest moves</button><button type="button" className={steamOnly ? 'is-on' : ''} onClick={() => setSteamOnly(true)}>Steam only ({steamCount})</button></span>
      </header>
      {error ? <p className="ed-muted">Market history is only on the test-mode preview ({error}).</p> : !data ? <p className="ed-muted">Reading the price history…</p> : !list.length ? <p className="ed-muted">No market moves to show for today's board yet.</p> : (
        <div className="ed-mm__grid">
          {list.map((m) => {
            const g = names.get(m.game_id)!
            const towardHome = m.move >= 0
            const mover = towardHome ? g.home.abbreviation : g.away.abbreviation
            return (
              <article key={m.game_id} className={`ed-mover${m.steam.length ? ' has-steam' : ''}`}>
                <header><span className="ed-game"><Crest sport={g.league} abbr={g.away.abbreviation} size={22} /><Crest sport={g.league} abbr={g.home.abbreviation} size={22} /><span><b>{g.matchup}</b><small style={{ color: SPORT_COLOR[g.league] }}>{g.league} · {new Date(m.starts_at).toLocaleString('en-US', { timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: '2-digit' })}</small></span></span>{m.steam.length ? <span className="ed-steam">STEAM</span> : null}</header>
                <Spark series={m.series} steam={m.steam} />
                <p><b className={Math.abs(m.move) >= 0.02 ? 'is-good' : ''}>{mover} {signed(Math.abs(m.move) * 100, 1, ' pts')}</b> · {g.home.abbreviation} {pct(m.open_home)} → {pct(m.now_home)} · {m.books} books{m.steam[0] ? ` · steam ${signed(m.steam[0].move * 100, 1, ' pts')} with ${m.steam[0].books_agree}/${m.steam[0].books} books` : ''}</p>
              </article>
            )
          })}
        </div>
      )}
      <p className="ed-foot">Built from AISP's own price captures, which are a few snapshots per game, so a short-lived move between captures can be missed. No ticket or handle data is used.</p>
    </section>
  )
}

function Spark({ series, steam }: { series: MoverGame['series']; steam: MoverGame['steam'] }) {
  const W = 300; const H = 70
  const t0 = Date.parse(series[0].t); const t1 = Math.max(t0 + 1, Date.parse(series[series.length - 1].t))
  const lo = Math.min(...series.map((p) => p.home)) - 0.01; const hi = Math.max(...series.map((p) => p.home)) + 0.01
  const X = (t: string) => 4 + ((Date.parse(t) - t0) / (t1 - t0)) * (W - 8)
  const Y = (v: number) => H - 6 - ((v - lo) / Math.max(0.01, hi - lo)) * (H - 12)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="ed-spark" aria-hidden="true">
      {steam.map((s, i) => <rect key={i} x={X(s.from)} y={2} width={Math.max(4, X(s.to) - X(s.from))} height={H - 4} className="ed-spark__steam" />)}
      <polyline points={series.map((p) => `${X(p.t).toFixed(1)},${Y(p.home).toFixed(1)}`).join(' ')} className="ed-spark__line" />
      <circle cx={X(series[series.length - 1].t)} cy={Y(series[series.length - 1].home)} r="3.5" className="ed-spark__end" />
    </svg>
  )
}
