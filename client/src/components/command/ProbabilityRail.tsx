/**
 * One game's moneyline on a probability rail -- the Markets & Crowd consensus
 * board's row, drawn for a single selection.
 *
 *   translucent teal band  the individual books' no-vig range   (cc-range)
 *   dark tick              the no-vig consensus                  (cc-consensus)
 *   sport-coloured point   the model probability for the side    (cc-dot__mark)
 *   stem between them      the disagreement, green only when the model is above
 *
 * Every value comes from the caller, already oriented to the selected side
 * with the shared definitions in `services/marketMath`. A missing range is not
 * drawn; nothing is inferred. The model point eases in from the consensus on
 * entry unless the reader prefers reduced motion. Hover or focus shows exact
 * values, and the SVG carries a text equivalent.
 */
import * as d3 from 'd3'
import { memo, useEffect, useRef } from 'react'
import { Tooltip, useReducedMotion, useTooltip, useWidth } from './chartKit'

const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

export interface RailProps {
  label: string
  range: [number, number] | null
  consensus: number | null
  consensusCurrent: boolean
  model: number | null
  /** The evaluated book's no-vig probability, when a decision priced against one. */
  bookProbability?: number | null
  bookLabel?: string | null
  color: string
  height?: number
}

export const ProbabilityRail = memo(function ProbabilityRail({
  label, range, consensus, consensusCurrent, model, bookProbability = null, bookLabel = null, color, height = 30,
}: RailProps) {
  const reduced = useReducedMotion()
  const { ref, width } = useWidth<HTMLDivElement>(240)
  const { tip, show, hide, frame } = useTooltip()
  const modelRef = useRef<SVGCircleElement | null>(null)
  const stemRef = useRef<SVGLineElement | null>(null)

  const values = [range?.[0], range?.[1], consensus, model, bookProbability].filter((v): v is number => v != null)
  const lo = Math.max(0, Math.floor(((d3.min(values) ?? 0.4) - 0.06) * 20) / 20)
  const hi = Math.min(1, Math.ceil(((d3.max(values) ?? 0.6) + 0.06) * 20) / 20)
  const pad = 30
  const x = d3.scaleLinear().domain([lo, hi]).range([pad, Math.max(pad + 40, width - pad)])
  const cy = height / 2 + 2
  const gap = model != null && consensus != null ? model - consensus : null
  const positive = gap != null && gap > 0

  useEffect(() => {
    if (!modelRef.current || model == null) return
    const target = x(model)
    const from = consensus != null ? x(consensus) : target
    const dot = d3.select(modelRef.current)
    const stem = stemRef.current ? d3.select(stemRef.current) : null
    if (reduced) {
      dot.attr('cx', target)
      stem?.attr('x2', target)
      return
    }
    dot.attr('cx', from).transition().duration(650).ease(d3.easeCubicOut).attr('cx', target)
    stem?.attr('x2', from).transition().duration(650).ease(d3.easeCubicOut).attr('x2', target)
  }, [model, consensus, width, reduced]) // eslint-disable-line react-hooks/exhaustive-deps

  const summary = `${label}: model ${pct(model)}${consensus != null ? `, no-vig consensus ${pct(consensus)}${consensusCurrent ? '' : ' (not all books current)'}` : ''}${range ? `, books ${pct(range[0])} to ${pct(range[1])}` : ''}${bookProbability != null ? `, ${bookLabel ?? 'evaluated book'} ${pct(bookProbability)}` : ''}${gap != null ? `, gap ${(gap * 100 >= 0 ? '+' : '')}${(gap * 100).toFixed(1)} pts` : ''}`
  const tipContent = (
    <>
      <b>{label}</b>
      <dl>
        <dt>Model</dt><dd>{pct(model)}</dd>
        {consensus != null && <><dt>No-vig consensus</dt><dd>{pct(consensus)}{consensusCurrent ? '' : ' · some books not current'}</dd></>}
        {range && <><dt>Book range</dt><dd>{pct(range[0])} – {pct(range[1])}</dd></>}
        {bookProbability != null && <><dt>{bookLabel ?? 'Evaluated book'}</dt><dd>{pct(bookProbability)}</dd></>}
        {gap != null && <><dt>Model − consensus</dt><dd>{gap >= 0 ? '+' : ''}{(gap * 100).toFixed(1)} pts</dd></>}
      </dl>
    </>
  )

  return (
    <div className="cc-rail" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img" aria-label={summary}>
        <g tabIndex={0} className="cc-rail__focus" aria-label={summary} onMouseMove={(e) => show(e, tipContent)} onMouseLeave={hide}
          onFocus={(e) => show(e, tipContent)} onBlur={hide}>
          <rect x={0} y={0} width={width} height={height} fill="transparent" />
          <line x1={pad} x2={width - pad} y1={cy} y2={cy} className="cc-rail__track" />
          {range && <rect x={x(range[0]) - 2} y={cy - 6} width={Math.max(4, x(range[1]) - x(range[0]) + 4)} height={12} rx={6} className="cc-range cc-rail__range" />}
          {gap != null && (
            <line ref={stemRef} x1={x(consensus!)} x2={x(model!)} y1={cy} y2={cy}
              className={`cc-rail__stem ${positive ? 'cc-rail__stem--up' : 'cc-rail__stem--down'}`} />
          )}
          {bookProbability != null && <path d={d3.symbol(d3.symbolTriangle, 34)() ?? ''} transform={`translate(${x(bookProbability)}, ${cy - 11}) rotate(180)`} className="cc-rail__book" />}
          {consensus != null && <line x1={x(consensus)} x2={x(consensus)} y1={cy - 9} y2={cy + 9} className="cc-consensus cc-rail__consensus" />}
          {model != null && <circle ref={modelRef} cx={x(model)} cy={cy} r={6} fill={color} className="cc-dot__mark cc-rail__model" />}
          <text x={pad - 6} y={cy + 3.5} textAnchor="end" className="cc-rail__tick">{pct(lo).replace('.0', '')}</text>
          <text x={width - pad + 6} y={cy + 3.5} className="cc-rail__tick">{pct(hi).replace('.0', '')}</text>
        </g>
      </svg>
      <Tooltip tip={tip} width={width} />
    </div>
  )
})

/** Forecast-only graphic: the model's two-way win split, with a projected score when published. */
export const WinSplit = memo(function WinSplit({ awayLabel, homeLabel, homeProbability, awayColor, homeColor, projected, emphasize }: {
  awayLabel: string
  homeLabel: string
  homeProbability: number
  awayColor: string
  homeColor: string
  projected: { home: number; away: number } | null
  emphasize: 'HOME' | 'AWAY'
}) {
  const reduced = useReducedMotion()
  const barRef = useRef<HTMLDivElement | null>(null)
  const away = 1 - homeProbability
  useEffect(() => {
    const node = barRef.current
    if (!node || reduced) return
    node.animate?.([{ transform: 'scaleX(0.4)', opacity: 0.4 }, { transform: 'scaleX(1)', opacity: 1 }], { duration: 500, easing: 'cubic-bezier(.2,.7,.2,1)' })
  }, [homeProbability, reduced])
  const text = `Model win split: ${awayLabel} ${(away * 100).toFixed(1)}%, ${homeLabel} ${(homeProbability * 100).toFixed(1)}%${projected ? `; projected score ${awayLabel} ${projected.away.toFixed(1)}, ${homeLabel} ${projected.home.toFixed(1)}` : ''}`
  return (
    <div className="cc-split" role="img" aria-label={text} tabIndex={0} title={text}>
      <div className="cc-split__labels">
        <b className={emphasize === 'AWAY' ? 'is-lean' : ''}>{awayLabel} {(away * 100).toFixed(0)}%</b>
        <b className={emphasize === 'HOME' ? 'is-lean' : ''}>{(homeProbability * 100).toFixed(0)}% {homeLabel}</b>
      </div>
      <div className="cc-split__bar" ref={barRef}>
        <span className={emphasize === 'AWAY' ? 'is-lean' : ''} style={{ width: `${away * 100}%`, ['--team' as string]: awayColor }} />
        <span className={emphasize === 'HOME' ? 'is-lean' : ''} style={{ width: `${homeProbability * 100}%`, ['--team' as string]: homeColor }} />
      </div>
      {projected && <small className="cc-split__score">Projected {awayLabel} {projected.away.toFixed(1)} – {projected.home.toFixed(1)} {homeLabel}</small>}
    </div>
  )
})
