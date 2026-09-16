/**
 * Markets & Crowd · stored-observation visuals.
 *
 *   PriceMovement     the selected game's no-vig consensus over time, with the
 *                     book range and the model's published versions
 *                     (`/markets/tape/{game}` — persisted snapshots only)
 *   BookDispersion    each book's no-vig home probability per priced game,
 *                     with the consensus tick
 *   CoverageStrip     current / stored-only / missing markets across the slate
 *   LargestGaps       games with a valid CURRENT model-vs-consensus comparison
 *
 * Each visual hides itself when its source data is insufficient; nothing is
 * requested from a provider.
 */
import * as d3 from 'd3'
import { useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { SPORT_COLORS, asSport, etTime, gameHref, type CoverageGame } from '../../services/commandCenter'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import { ChartTable, Tooltip, useTooltip, useWidth } from './chartKit'
import './assessments.css'

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)

interface TapeFrame { t: string; usable_books: number; consensus_no_vig_home: number | null; range_a: [number, number] | null }
interface Tape {
  schema_version: string
  market: { side_a: { label: string } } | null
  event: { home: { name: string; abbreviation: string }; away: { name: string; abbreviation: string } } | null
  frames: TapeFrame[]
  model_probability_series: { version: number; available_as_of_utc: string; home_win_probability: number; status: string }[]
  requirement: string | null
}

/** Frames usable for a movement line: a consensus at a real observation time. */
export function movementPoints(tape: Tape | null) {
  if (!tape) return []
  const homeIsA = tape.market && tape.event ? tape.market.side_a.label === tape.event.home.name : null
  return tape.frames
    .filter((f) => f.consensus_no_vig_home != null && f.usable_books > 0)
    .map((f) => ({
      t: new Date(f.t),
      consensus: f.consensus_no_vig_home as number,
      range: f.range_a && homeIsA != null
        ? (homeIsA ? f.range_a : [1 - f.range_a[1], 1 - f.range_a[0]]) as [number, number]
        : null,
      books: f.usable_books,
    }))
}

export function PriceMovement({ game }: { game: CoverageGame }) {
  const tape = useApiResource<Tape>(useCallback((signal: AbortSignal) =>
    apiRequest<Tape>(`/markets/tape/${encodeURIComponent(game.game_id)}?market=MONEYLINE`, {}, signal), [game.game_id]),
    { key: cacheKey('markets/tape', { game: game.game_id, market: 'MONEYLINE' }), staleTimeMs: STALE_TIME.slate })
  const points = useMemo(() => movementPoints(tape.data ?? null), [tape.data])
  const { ref, width } = useWidth<HTMLDivElement>(560)
  const { tip, show, hide, frame } = useTooltip()
  if (points.length < 2) return null
  const models = (tape.data?.model_probability_series ?? []).map((m) => ({ ...m, t: new Date(m.available_as_of_utc) }))
  const pad = { left: 44, right: 12, top: 10, bottom: 28 }
  const h = 180
  const all = [...points.map((p) => p.t), ...models.map((m) => m.t)]
  const x = d3.scaleTime().domain(d3.extent(all) as [Date, Date]).range([pad.left, Math.max(pad.left + 80, width - pad.right)])
  const values = [...points.flatMap((p) => [p.consensus, ...(p.range ?? [])]), ...models.map((m) => m.home_win_probability)]
  const y = d3.scaleLinear().domain([(d3.min(values) ?? 0.4) - 0.02, (d3.max(values) ?? 0.6) + 0.02]).nice().range([pad.top + h, pad.top])
  const line = d3.line<typeof points[number]>().x((p) => x(p.t)).y((p) => y(p.consensus)).curve(d3.curveStepAfter)
  const withRange = points.filter((p) => p.range)
  const area = d3.area<typeof points[number]>().x((p) => x(p.t)).y0((p) => y(p.range![0])).y1((p) => y(p.range![1])).curve(d3.curveStepAfter)
  const home = tape.data?.event?.home.abbreviation ?? game.home?.abbreviation ?? 'Home'
  return (
    <section className="mv-card" aria-label="Price movement">
      <h3>Price movement · {game.matchup} · {home} win (no-vig)</h3>
      <div ref={ref} className="gs-svgwrap">
        <div ref={frame} style={{ position: 'relative' }}>
          <svg width={width} height={pad.top + h + pad.bottom} role="img"
            aria-label={`${home} no-vig consensus moved from ${pct(points[0].consensus)} to ${pct(points.at(-1)!.consensus)} across ${points.length} stored observations`}>
            {y.ticks(4).map((t) => (
              <g key={t} transform={`translate(0,${y(t)})`}>
                <line x1={pad.left} x2={width - pad.right} className="gs-grid" />
                <text x={pad.left - 6} y={4} textAnchor="end" className="gs-tick">{(t * 100).toFixed(0)}%</text>
              </g>
            ))}
            {x.ticks(4).map((t) => (
              <text key={t.toISOString()} x={x(t)} y={pad.top + h + 16} textAnchor="middle" className="gs-tick">{etTime(t.toISOString()).replace(' ET', '')}</text>
            ))}
            {withRange.length > 1 && <path d={area(withRange) ?? ''} className="mv-band" />}
            <path d={line(points) ?? ''} className="mv-line" />
            {points.map((p, i) => (
              <circle key={i} cx={x(p.t)} cy={y(p.consensus)} r={3.5} className="mv-pt" tabIndex={0}
                aria-label={`${etTime(p.t.toISOString())}: consensus ${pct(p.consensus)} from ${p.books} books`}
                onMouseMove={(e) => show(e, `${etTime(p.t.toISOString(), true)} · consensus ${pct(p.consensus)} · ${p.books} book${p.books === 1 ? '' : 's'}${p.range ? ` · range ${pct(p.range[0])}–${pct(p.range[1])}` : ''}`)}
                onFocus={(e) => show(e, `${etTime(p.t.toISOString(), true)} · consensus ${pct(p.consensus)} · ${p.books} books`)}
                onMouseLeave={hide} onBlur={hide} />
            ))}
            {models.map((m) => (
              <path key={m.version} d={d3.symbol(d3.symbolTriangle, 60)() ?? ''} transform={`translate(${x(m.t)},${y(m.home_win_probability)})`}
                className="gs-fair" tabIndex={0} aria-label={`Model version ${m.version} (${m.status}): ${pct(m.home_win_probability)}`}
                onMouseMove={(e) => show(e, `Model v${m.version} (${m.status}) · ${pct(m.home_win_probability)} · ${etTime(m.available_as_of_utc, true)}`)}
                onMouseLeave={hide} />
            ))}
          </svg>
          <Tooltip tip={tip} width={width} />
        </div>
      </div>
      <p className="gs-legend">Line: no-vig consensus (steps when a book changes) · band: book range · triangles: model versions. Observation times are the books&rsquo; own.</p>
      <ChartTable caption="Stored consensus over time">
        <thead><tr><th>Observed</th><th>Consensus {home}</th><th>Books</th></tr></thead>
        <tbody>{points.map((p, i) => <tr key={i}><td>{etTime(p.t.toISOString(), true)}</td><td>{pct(p.consensus)}</td><td>{p.books}</td></tr>)}</tbody>
      </ChartTable>
    </section>
  )
}

/** One point per book: its own no-vig home probability from a two-sided moneyline. */
export function bookPoints(game: CoverageGame) {
  return game.quotes
    .filter((q) => q.market === 'MONEYLINE' && q.no_vig_home != null)
    .map((q) => ({ book: q.sportsbook ?? 'book', p: q.no_vig_home as number, current: q.fresh }))
}

export function BookDispersion({ games, onSelect }: { games: CoverageGame[]; onSelect: (id: string) => void }) {
  const rows = games.map((g) => ({ game: g, points: bookPoints(g) })).filter((r) => r.points.length >= 2)
  const { ref, width } = useWidth<HTMLDivElement>(560)
  const { tip, show, hide, frame } = useTooltip()
  if (rows.length === 0) return null
  const all = rows.flatMap((r) => r.points.map((p) => p.p))
  const x = d3.scaleLinear().domain([Math.max(0, (d3.min(all) ?? 0) - 0.03), Math.min(1, (d3.max(all) ?? 1) + 0.03)]).range([120, Math.max(200, width - 12)])
  const rowH = 26
  return (
    <section className="mv-card" aria-label="Book dispersion">
      <h3>Book dispersion · home no-vig probability by book</h3>
      <div ref={ref} className="gs-svgwrap">
        <div ref={frame} style={{ position: 'relative' }}>
          <svg width={width} height={rows.length * rowH + 24} role="img" aria-label="No-vig home probability by sportsbook for each priced game">
            {x.ticks(5).map((t) => (
              <g key={t} transform={`translate(${x(t)},0)`}>
                <line y1={0} y2={rows.length * rowH} className="gs-grid" />
                <text y={rows.length * rowH + 14} textAnchor="middle" className="gs-tick">{(t * 100).toFixed(0)}%</text>
              </g>
            ))}
            {rows.map((r, i) => {
              const cy = i * rowH + rowH / 2
              const ext = d3.extent(r.points, (p) => p.p) as [number, number]
              const code = asSport(r.game.league) ?? 'MLB'
              return (
                <g key={r.game.game_id}>
                  <text x={0} y={cy + 4} className="gs-label mv-rowlabel" onClick={() => onSelect(r.game.game_id)}>{r.game.matchup}</text>
                  <line x1={x(ext[0])} x2={x(ext[1])} y1={cy} y2={cy} className="mv-range" />
                  {r.game.consensus_no_vig_home != null && (
                    <line x1={x(r.game.consensus_no_vig_home)} x2={x(r.game.consensus_no_vig_home)} y1={cy - 8} y2={cy + 8} className="gs-consensus" />
                  )}
                  {r.points.map((p, j) => (
                    <circle key={j} cx={x(p.p)} cy={cy} r={4} tabIndex={0} style={{ ['--sport' as string]: SPORT_COLORS[code] }}
                      className={`mv-book ${p.current ? 'is-current' : 'is-stale'}`}
                      aria-label={`${r.game.matchup}, ${p.book}: ${pct(p.p)}${p.current ? '' : ', not current'}`}
                      onMouseMove={(e) => show(e, `${r.game.matchup} · ${p.book}: ${pct(p.p)}${p.current ? '' : ' (not current)'}`)}
                      onFocus={(e) => show(e, `${r.game.matchup} · ${p.book}: ${pct(p.p)}`)}
                      onMouseLeave={hide} onBlur={hide} />
                  ))}
                </g>
              )
            })}
          </svg>
          <Tooltip tip={tip} width={width} />
        </div>
      </div>
      <p className="gs-legend">Dots: individual books (hollow = not current) · line: their range · tick: consensus. Click a game name to select it.</p>
    </section>
  )
}

export interface CoverageCounts { market: string; current: number; stale: number; missing: number }

export function coverageCounts(games: CoverageGame[]): CoverageCounts[] {
  const markets: [string, string[]][] = [['Moneyline', ['MONEYLINE']], ['Spread / run line', ['SPREAD', 'RUN_LINE']], ['Total', ['TOTAL']]]
  return markets.map(([label, keys]) => {
    let current = 0, stale = 0, missing = 0
    for (const g of games) {
      const quotes = g.quotes.filter((q) => keys.includes(q.market))
      if (quotes.some((q) => q.fresh)) current += 1
      else if (quotes.length) stale += 1
      else missing += 1
    }
    return { market: label, current, stale, missing }
  })
}

export function CoverageStrip({ games }: { games: CoverageGame[] }) {
  if (games.length === 0) return null
  const counts = coverageCounts(games)
  return (
    <section className="mv-card" aria-label="Market coverage">
      <h3>Coverage · {games.length} games on this date</h3>
      <ul className="mv-cov">
        {counts.map((c) => (
          <li key={c.market}>
            <b>{c.market}</b>
            <span className="mv-cov__bar" role="img" aria-label={`${c.market}: ${c.current} current, ${c.stale} stored only, ${c.missing} missing`}>
              {c.current > 0 && <i className="is-current" style={{ flexGrow: c.current }}>{c.current}</i>}
              {c.stale > 0 && <i className="is-stale" style={{ flexGrow: c.stale }}>{c.stale}</i>}
              {c.missing > 0 && <i className="is-missing" style={{ flexGrow: c.missing }}>{c.missing}</i>}
            </span>
          </li>
        ))}
      </ul>
      <p className="gs-legend"><i className="mv-key is-current" /> current <i className="mv-key is-stale" /> stored, not current <i className="mv-key is-missing" /> not collected</p>
    </section>
  )
}

/** Only games whose model and consensus are both valid and the consensus is CURRENT. */
export function largestGaps(games: CoverageGame[], limit = 5) {
  return games
    .filter((g) => g.consensus_is_current && g.consensus_no_vig_home != null && g.model?.home_two_way != null)
    .map((g) => ({ game: g, gap: (g.model!.home_two_way as number) - (g.consensus_no_vig_home as number) }))
    .sort((a, b) => Math.abs(b.gap) - Math.abs(a.gap))
    .slice(0, limit)
}

export function LargestGaps({ games, onSelect }: { games: CoverageGame[]; onSelect: (id: string) => void }) {
  const rows = largestGaps(games)
  if (rows.length === 0) return null
  return (
    <section className="mv-card" aria-label="Largest model and market gaps">
      <h3>Largest model/market gaps · current prices only</h3>
      <ol className="mv-gaps">
        {rows.map(({ game, gap }) => {
          const code = asSport(game.league) ?? 'MLB'
          const side = gap >= 0 ? game.home?.abbreviation : game.away?.abbreviation
          return (
            <li key={game.game_id} style={{ ['--sport' as string]: SPORT_COLORS[code] }}>
              <button type="button" onClick={() => onSelect(game.game_id)}>{game.matchup}</button>
              <span>model {pct(game.model?.home_two_way)} vs consensus {pct(game.consensus_no_vig_home)} ({game.home?.abbreviation})</span>
              <b className={gap >= 0 ? 'is-up' : 'is-down'}>{side} {(Math.abs(gap) * 100).toFixed(1)} pts</b>
              <Link to={gameHref(code, game.game_id)}>Game Center</Link>
            </li>
          )
        })}
      </ol>
      <p className="gs-legend">A research comparison of stored numbers; the governed decision status is on Daily Decisions.</p>
    </section>
  )
}
