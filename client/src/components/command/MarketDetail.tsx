/**
 * Selected-game market detail: the moneyline / spread / total summary, the RFF
 * market-risk lens, and how the consensus moved against the model.
 *
 * Reads only persisted evidence: the coverage read model (already cached), the
 * market tape (`/markets/tape/{game}`) and the RFF index (`/markets/rff/{game}`),
 * each fetched once per game and cached. Movement is drawn only when the tape
 * holds more than one observed instant; a single observation is said to be one.
 * RFF is a research lens -- not a market, not an input to any decision.
 */
import * as d3 from 'd3'
import { memo, useCallback, useEffect, useMemo, useRef } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import { etTime, relative, type CoverageGame, type CoverageQuote } from '../../services/commandCenter'
import { Tooltip, useTooltip, useWidth } from './chartKit'

type Family = 'MONEYLINE' | 'SPREAD' | 'TOTAL'

interface TapeFrame { t: string; usable_books: number; consensus_no_vig_home: number | null; range_a?: [number, number] | null }
interface Tape {
  schema_version: string
  frames: TapeFrame[]
  observed_instants: number
  first_observed_utc: string | null
  last_observed_utc: string | null
  model_probability_series: { generated_at_utc: string; home_win_probability: number | null; lifecycle?: string }[]
}
interface Rff {
  schema_version: string
  score: number | null
  band?: { key: string; label: string; description: string } | null
  evidence_coverage?: number
  explanation?: string
  contributions?: { key: string; label: string; score: number; share_of_score: number }[]
  authority?: { statement: string }
  status?: string
}

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')

function summary(game: CoverageGame, market: string): { text: string; sub: string; current: boolean } | null {
  const quotes = game.quotes.filter((q) => q.market === market || (market === 'SPREAD' && q.market === 'RUN_LINE'))
  if (!quotes.length) return null
  const fresh = quotes.filter((q) => q.fresh)
  const use: CoverageQuote[] = fresh.length ? fresh : quotes
  const newest = [...use].sort((a, b) => b.observed_at.localeCompare(a.observed_at))[0]
  const lines = use.map((q) => q.line).filter((v): v is number => v != null)
  const home = game.home?.abbreviation ?? 'Home'
  const away = game.away?.abbreviation ?? 'Away'
  if (market === 'MONEYLINE') {
    const text = fresh.length && game.best.AWAY && game.best.HOME
      ? `${away} ${am(game.best.AWAY.american)} · ${home} ${am(game.best.HOME.american)}`
      : `${away} ${am(newest.sides.AWAY?.american)} · ${home} ${am(newest.sides.HOME?.american)}`
    return { text, sub: `${fresh.length ? 'best current' : `${shortBook(newest.sportsbook)}, not current`} · no-vig ${home} ${pct(game.consensus_no_vig_home)}`, current: fresh.length > 0 }
  }
  const lo = d3.min(lines)
  const hi = d3.max(lines)
  const median = d3.median(lines)
  const label = market === 'TOTAL' ? `Total ${median ?? '—'}` : `${home} ${median != null ? (median > 0 ? `+${median}` : median) : '—'}`
  return { text: label, sub: `${lo === hi ? 'all books' : `range ${lo} to ${hi}`} · ${use.length} books · ${fresh.length ? 'current' : 'not current'}`, current: fresh.length > 0 }
}

export const MarketDetail = memo(function MarketDetail({ game, family }: { game: CoverageGame; family: Family }) {
  const tapeLoader = useCallback((signal: AbortSignal) =>
    apiRequest<Tape>(`/markets/tape/${game.game_id}?market=MONEYLINE`, {}, signal), [game.game_id])
  const tape = useApiResource<Tape>(tapeLoader, { key: cacheKey('markets-tape', { game: game.game_id }), staleTimeMs: STALE_TIME.slate })
  const rffLoader = useCallback((signal: AbortSignal) =>
    apiRequest<Rff>(`/markets/rff/${game.game_id}`, {}, signal), [game.game_id])
  const rff = useApiResource<Rff>(rffLoader, { key: cacheKey('markets-rff-game', { game: game.game_id }), staleTimeMs: STALE_TIME.slate })

  const families: { key: Family; label: string }[] = [
    { key: 'MONEYLINE', label: 'Moneyline' },
    { key: 'SPREAD', label: game.league === 'MLB' ? 'Run line' : 'Spread' },
    { key: 'TOTAL', label: 'Total' },
  ]
  const band = rff.data?.band
  return (
    <div className="cc-mdetail">
      <dl className="cc-mdetail__summary">
        {families.map((f) => {
          const s = summary(game, f.key)
          return (
            <div key={f.key} className={f.key === family ? 'is-active' : ''}>
              <dt>{f.label}</dt>
              <dd>{s ? s.text : 'Not collected'}</dd>
              <small className={s && !s.current ? 'is-gold' : ''}>{s ? s.sub : 'No stored quote'}</small>
            </div>
          )
        })}
      </dl>

      <div className="cc-mdetail__row">
        <section className={`cc-rff${(rff.data?.score ?? 0) >= 55 ? ' cc-rff--hot' : ''}`} aria-label="RFF market-risk lens">
          <header>
            <span>RFF · market-risk lens</span>
            <b>{rff.data?.score != null ? Math.round(rff.data.score) : '—'}</b>
          </header>
          {rff.data
            ? <>
              <p className="cc-rff__band">{band ? `${band.label} — ${band.description}` : 'Not scored'}{rff.data.evidence_coverage != null ? ` · ${Math.round(rff.data.evidence_coverage * 100)}% evidence coverage` : ''}</p>
              {(rff.data.contributions ?? []).filter((c) => c.share_of_score > 0).slice(0, 3).map((c) => (
                <p key={c.key} className="cc-rff__driver"><i style={{ width: `${Math.round(c.share_of_score * 100)}%` }} />{c.label} <b>{Math.round(c.score)}</b></p>
              ))}
              <p className="cc-rff__note">Research index — not a sportsbook market, not a betting signal, and not an input to any decision.</p>
            </>
            : <p className="cc-muted">{rff.error ? 'RFF unavailable for this game.' : 'Loading RFF…'}</p>}
        </section>

        <section className="cc-move" aria-label="Market movement">
          <header>
            <span>Movement · consensus no-vig {game.home?.abbreviation} vs model</span>
            <small>{tape.data ? `${tape.data.observed_instants} observed instant${tape.data.observed_instants === 1 ? '' : 's'}` : ''}</small>
          </header>
          {tape.data
            ? <MovementLine tape={tape.data} home={game.home?.abbreviation ?? 'Home'} />
            : <p className="cc-muted">{tape.error ? 'Market tape unavailable.' : 'Loading tape…'}</p>}
        </section>
      </div>
    </div>
  )
})

const MovementLine = memo(function MovementLine({ tape, home }: { tape: Tape; home: string }) {
  const { ref, width } = useWidth<HTMLDivElement>(320)
  const { tip, show, hide, frame } = useTooltip()
  const axis = useRef<SVGGElement | null>(null)
  const frames = useMemo(() => tape.frames.filter((f) => f.consensus_no_vig_home != null)
    .map((f) => ({ t: Date.parse(f.t), v: f.consensus_no_vig_home as number, books: f.usable_books })), [tape])
  const modelAll = useMemo(() => tape.model_probability_series.filter((m) => m.home_win_probability != null)
    .map((m) => ({ t: Date.parse(m.generated_at_utc), v: m.home_win_probability as number, lifecycle: m.lifecycle })), [tape])
  const height = 118
  const m = { top: 8, right: 10, bottom: 20, left: 38 }
  // The time axis spans the observed quotes only; a forecast published hours
  // earlier is carried forward as a flat reference, not stretched into the axis.
  const tMin = d3.min(frames, (p) => p.t) ?? Date.now()
  const tMax = Math.max(d3.max(frames, (p) => p.t) ?? Date.now(), tMin + 60_000)
  const before = modelAll.filter((p) => p.t <= tMin)
  const model = [...(before.length ? [{ ...before[before.length - 1], t: tMin }] : []), ...modelAll.filter((p) => p.t > tMin && p.t <= tMax)]
  const latestModel = modelAll[modelAll.length - 1]
  const vals = [...frames, ...model].map((p) => p.v)
  const x = d3.scaleTime().domain([tMin, tMax]).range([m.left, width - m.right])
  const y = d3.scaleLinear().domain([Math.max(0, (d3.min(vals) ?? 0.4) - 0.03), Math.min(1, (d3.max(vals) ?? 0.6) + 0.03)]).range([height - m.bottom, m.top])

  useEffect(() => {
    if (!axis.current) return
    d3.select(axis.current).call(d3.axisLeft(y).ticks(3).tickFormat((v) => d3.format('.0%')(v as number)) as never)
  }, [y])

  if (frames.length < 2) {
    return <p className="cc-muted">{frames.length === 1 ? `One observation only (${etTime(tape.first_observed_utc, true)}) — no movement to show yet.` : 'No priced observation stored.'}</p>
  }
  const line = d3.line<{ t: number; v: number }>().x((p) => x(p.t)).y((p) => y(p.v)).curve(d3.curveStepAfter)
  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img" aria-label={`Consensus moved from ${pct(frames[0].v)} to ${pct(frames[frames.length - 1].v)} across ${frames.length} observations`}>
        <g ref={axis} className="cc-axis" transform={`translate(${m.left}, 0)`} />
        <path d={line(frames) ?? ''} fill="none" stroke="var(--bx-text, #10203a)" strokeWidth={2} />
        {model.length > 0 && <path d={line([...model, { t: tMax, v: model[model.length - 1].v }]) ?? ''} fill="none" stroke="var(--bx-accent, #0f8d7d)" strokeWidth={2} strokeDasharray="5 3" />}
        {frames.map((p, i) => (
          <circle key={i} cx={x(p.t)} cy={y(p.v)} r={3.5} className="cc-move__pt" tabIndex={0}
            aria-label={`${etTime(new Date(p.t).toISOString(), true)}: consensus ${pct(p.v)} across ${p.books} books`}
            onMouseMove={(e) => show(e, <><b>{etTime(new Date(p.t).toISOString(), true)}</b><dl><dt>Consensus {home}</dt><dd>{pct(p.v)}</dd><dt>Books</dt><dd>{p.books}</dd></dl></>)}
            onMouseLeave={hide} onFocus={(e) => show(e, <><b>{etTime(new Date(p.t).toISOString(), true)}</b><dl><dt>Consensus {home}</dt><dd>{pct(p.v)}</dd><dt>Books</dt><dd>{p.books}</dd></dl></>)} onBlur={hide} />
        ))}
        <text x={m.left} y={height - 5} className="cc-muted-text">{etTime(new Date(tMin).toISOString()).replace(' ET', '')}</text>
        <text x={width - m.right} y={height - 5} textAnchor="end" className="cc-muted-text">{etTime(new Date(tMax).toISOString())} · {relative(new Date(tMax).toISOString())}</text>
      </svg>
      <Tooltip tip={tip} width={width} />
      <p className="cc-note">Solid: sportsbook consensus (no-vig) {pct(frames[0].v)} → {pct(frames[frames.length - 1].v)}.{latestModel ? ` Dashed: model ${home} ${pct(latestModel.v)}${latestModel.lifecycle ? ` (${latestModel.lifecycle.toLowerCase().replace(/_/g, ' ')})` : ''}, published ${etTime(new Date(latestModel.t).toISOString(), true)}.` : ' No model probability published.'}</p>
    </div>
  )
})
