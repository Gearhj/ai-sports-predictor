/**
 * SHOP THE LINE (owner 10-24: a HOLY SHIT feature for Markets & Crowd). Same game, same team, different books,
 * different money. For every game with at least three books this shows how much more a $100 stake would have paid
 * at the best book than at the worst, a ladder of every book's price (gold = best, red = worst), and which books
 * had the best price most often today. AISP's own research put line shopping at about 1.4–1.6% of value: this is
 * what that looks like on tonight's board.
 *
 * Reads the market coverage the page already loads (moneyline quotes AISP collected). Research only: a price shown
 * here may be stale, and nothing here is advice or a wager signal.
 */
import { useMemo, useState, type CSSProperties } from 'react'
import { useMarketCoverage } from '../../services/commandCenter'
import { Crest } from '../../features/flow/Crest'
import './shopTheLine.css'

type Side = 'HOME' | 'AWAY'
type Quote = { market: string; sportsbook: string; observed_at?: string | null; sides: Partial<Record<Side, { american: number | null; decimal: number | null }>>; fresh?: boolean }
type Game = { game_id: string; league: string; matchup: string; home: { abbreviation: string; name?: string }; away: { abbreviation: string; name?: string }; starts_at: string; status?: string; quotes?: Quote[] }
type Row = { g: Game; side: Side; team: string; prices: Array<{ book: string; dec: number; am: number | null }>; best: { book: string; dec: number; am: number | null }; worst: { book: string; dec: number; am: number | null }; extra: number }

const am = (n: number | null) => (n == null ? '—' : n > 0 ? `+${n}` : `${n}`)

export function ShopTheLine({ day }: { day: string }) {
  const coverage = useMarketCoverage(day)
  const [hover, setHover] = useState<{ row: Row; book: string; am: number | null; x: number; y: number } | null>(null)
  const rows = useMemo(() => {
    const out: Row[] = []
    for (const g of ((coverage.data?.games ?? []) as unknown as Game[])) {
      // Compare like with like: only quotes taken within 30 minutes of the game's newest quote.
      const all = (g.quotes ?? []).filter((q) => q.market === 'MONEYLINE')
      const newest = Math.max(...all.map((q) => Date.parse(q.observed_at ?? '') || 0))
      const ml = all.filter((q) => newest - (Date.parse(q.observed_at ?? '') || 0) <= 30 * 60_000)
      for (const side of ['HOME', 'AWAY'] as Side[]) {
        // Prices between about -400 and +300: longshots make every gap look huge and say little.
        const prices = ml.map((q) => ({ book: q.sportsbook, dec: q.sides?.[side]?.decimal ?? NaN, am: q.sides?.[side]?.american ?? null })).filter((p) => Number.isFinite(p.dec) && p.dec >= 1.25 && p.dec <= 4)
        if (prices.length < 3) continue
        const sorted = [...prices].sort((a, b) => b.dec - a.dec)
        const best = sorted[0]; const worst = sorted[sorted.length - 1]
        out.push({ g, side, team: side === 'HOME' ? g.home.abbreviation : g.away.abbreviation, prices, best, worst, extra: (best.dec - worst.dec) * 100 })
      }
    }
    return out
  }, [coverage.data])
  if (!rows.length) return null
  // one row per game: the side where shopping mattered more
  const perGame = new Map<string, Row>()
  rows.forEach((r) => { const cur = perGame.get(r.g.game_id); if (!cur || r.extra > cur.extra) perGame.set(r.g.game_id, r) })
  const top = [...perGame.values()].sort((a, b) => b.extra - a.extra).slice(0, 10)
  const sortedExtra = rows.map((r) => r.extra).sort((a, b) => a - b)
  const avg = sortedExtra[Math.floor(sortedExtra.length / 2)]
  const bestCount = new Map<string, number>()
  rows.forEach((r) => r.prices.filter((p) => p.dec === r.best.dec).forEach((p) => bestCount.set(p.book, (bestCount.get(p.book) ?? 0) + 1)))
  const board = [...bestCount.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6)
  const maxBest = board[0]?.[1] ?? 1
  const biggest = top[0]

  return (
    <section className="stl" aria-label="Shop the line">
      <div className="stl__main">
        <header>
          <h2>Shop the Line</h2>
          <small>same team, same game, different books: what a $100 stake pays more at the best price than the worst</small>
        </header>
        <div className="stl__hero">
          <span className="stl__big"><small>typical side today</small><b>+${avg.toFixed(2)}</b><em>more profit per $100 at the best book (median of {rows.length} sides with 3+ books, quotes within 30 minutes, prices -400 to +300)</em></span>
          {biggest ? <span className="stl__big is-gold"><small>biggest gap tonight</small><b>+${biggest.extra.toFixed(2)}</b><em>{biggest.team} {am(biggest.best.am)} at {biggest.best.book} vs {am(biggest.worst.am)} at {biggest.worst.book}</em></span> : null}
        </div>
        <ol className="stl__ladders" onMouseLeave={() => setHover(null)}>
          {top.map((r) => {
            const lo = r.worst.dec; const hi = r.best.dec; const span = Math.max(1e-6, hi - lo)
            return (
              <li key={`${r.g.game_id}-${r.side}`} style={{ ['--w' as string]: `${Math.min(100, r.extra * 4)}%` } as CSSProperties}>
                <span className="stl__team"><Crest sport={r.g.league} abbr={r.team} size={26} /><span><b>{r.team}</b><small>{r.g.matchup}</small></span></span>
                <span className="stl__rail">
                  {r.prices.map((p, i) => {
                    const x = ((p.dec - lo) / span) * 100
                    const cls = p.dec === hi ? 'is-best' : p.dec === lo ? 'is-worst' : ''
                    return <i key={`${p.book}-${i}`} className={cls} style={{ left: `${x}%` }} onMouseEnter={(e) => { const box = (e.currentTarget.closest('.stl__ladders') as HTMLElement).getBoundingClientRect(); setHover({ row: r, book: p.book, am: p.am, x: e.clientX - box.left, y: e.clientY - box.top }) }} />
                  })}
                  <em className="stl__lo">{am(r.worst.am)}</em><em className="stl__hi">{am(r.best.am)}</em>
                </span>
                <span className="stl__gain"><b>+${r.extra.toFixed(2)}</b><small>at {r.best.book}</small></span>
              </li>
            )
          })}
          {hover ? <div className="stl__tip" style={{ left: hover.x + 12, top: hover.y - 8 }}><b>{hover.book}</b><span>{hover.row.team} {am(hover.am)}</span></div> : null}
        </ol>
      </div>
      <aside className="stl__books">
        <h3>Best price, most often</h3>
        <small>how many of today's sides each book had the top price on</small>
        <ul>{board.map(([book, n], i) => <li key={book}><span className="stl__rank">{i + 1}</span><b>{book}</b><span className="stl__bar"><i style={{ width: `${(n / maxBest) * 100}%` }} /></span><em>{n}</em></li>)}</ul>
        <p>No single book wins every night. AISP's crowd research found nine books behave like about one independent opinion on the odds, but their prices still differ enough that shopping is worth about 1.4–1.6% of value over time.</p>
      </aside>
    </section>
  )
}
