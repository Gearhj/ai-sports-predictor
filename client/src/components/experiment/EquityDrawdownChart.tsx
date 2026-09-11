/**
 * The bankroll's actual path, and how far it fell from its best.
 *
 * THE ONE SHAPE RULE
 * ------------------
 * A step, never a curve. The bankroll does not drift between settlements --
 * it sits perfectly still for hours or days and then jumps once, when a game
 * finishes. A smoothed line would draw a continuous change that never
 * happened and would let a reader put a cursor between two settlements and
 * read a balance the ledger never held. `curveStepAfter` is the honest shape:
 * flat, then a vertical move at the exact moment money changed hands.
 *
 * WHAT IS DRAWN, AND WHY EACH PIECE EARNS ITS INK
 * -----------------------------------------------
 *   baseline      the opening $1,000, always in frame and always labelled, so
 *                 a truncated y-axis cannot pretend the bankroll started
 *                 wherever the chart happens to begin
 *   high-water    the running peak, as a step of its own
 *   drawdown      the area between the peak and the balance. This is a risk
 *                 measure, not decoration: its height is money that had been
 *                 won and was given back
 *   markers       one per settlement, shaped AND coloured by result, so the
 *                 chart is readable without colour vision
 *   open marker   hollow and set apart, because an open position has not
 *                 moved the bankroll and must not look like it has
 *
 * Hover or keyboard-focus any settlement to read the full position behind it.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import * as d3 from 'd3'
import { useMeasuredWidth, useReducedMotion } from '../charts/d3Charts'
import { equityDomain, money, signedMoney, type EquityPoint } from './bankrollMath'
import type { Position } from '../../types/domain'

export interface EquityChartProps {
  points: EquityPoint[]
  starting: number
  positions: Position[]
  /** Compact drops the axes down to two ticks for the home preview. */
  compact?: boolean
  height?: number
  /** Open positions, drawn as pending markers beyond the last settlement. */
  openCount?: number
  openExposure?: number
}

interface Hovered {
  point: EquityPoint
  position: Position | null
  x: number
  y: number
}

/**
 * SVG path length, when the platform can measure it.
 *
 * `getTotalLength` is part of the SVG geometry API, which jsdom does not
 * implement and which some embedded webviews omit. It was being called
 * unguarded to set up the draw-on reveal, so on any such platform the chart
 * did not merely skip its animation -- it threw during the effect and took the
 * page down with it. A missing measurement should cost the animation and
 * nothing else.
 */
function pathLength(node: SVGPathElement | null): number | null {
  if (!node || typeof node.getTotalLength !== 'function') return null
  try {
    const length = node.getTotalLength()
    return Number.isFinite(length) && length > 0 ? length : null
  } catch {
    return null
  }
}

export function EquityDrawdownChart({
  points, starting, positions, compact = false, height = 380,
  openCount = 0, openExposure = 0,
}: EquityChartProps) {
  const reduced = useReducedMotion()
  const [ref, width] = useMeasuredWidth<HTMLDivElement>(compact ? 420 : 900)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const [hovered, setHovered] = useState<Hovered | null>(null)

  const byId = useMemo(() => {
    const map = new Map<string, Position>()
    for (const position of positions ?? []) map.set(position.id, position)
    return map
  }, [positions])

  // The open-position annotation sits past the last settlement and needs a
  // right gutter to live in. Reserving 190px for it is correct at desktop and
  // ruinous on a phone, where it left the plot about 90px wide -- so below
  // 560px the annotation is dropped (the hero and the narrative both state the
  // open exposure anyway) and the gutter goes back to normal.
  const narrow = width < 560
  const annotateOpen = openCount > 0 && !compact && !narrow
  const margin = compact
    ? { top: 16, right: 16, bottom: 26, left: 58 }
    : { top: 26, right: annotateOpen ? 190 : 26, bottom: 46, left: narrow ? 62 : 82 }
  // A 420px-tall chart over a 290px-wide plot is a column, not a line chart.
  const drawHeight = compact ? height : Math.min(height, Math.max(240, width * 0.62))

  useEffect(() => {
    if (!svgRef.current || points.length === 0) return
    const svg = d3.select(svgRef.current)
    const innerWidth = Math.max(80, width - margin.left - margin.right)
    const innerHeight = Math.max(60, drawHeight - margin.top - margin.bottom)
    const domain = equityDomain(points, starting)

    const x = d3.scaleLinear()
      .domain([0, Math.max(1, points[points.length - 1].sequence)])
      .range([0, innerWidth])
    const y = d3.scaleLinear().domain(domain).range([innerHeight, 0])

    svg.attr('viewBox', `0 0 ${width} ${drawHeight}`).attr('height', drawHeight)
    svg.selectAll('*').remove()
    const root = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

    // -- axes ------------------------------------------------------------
    root.append('g')
      .call(d3.axisLeft(y).ticks(compact ? 3 : 6).tickSize(-innerWidth)
        .tickFormat((value) => money(value as number, 0)))
      .call((group) => group.select('.domain').remove())
      .call((group) => group.selectAll('text')
        .attr('fill', 'var(--bx-dim)').attr('font-size', compact ? 11 : 13).attr('font-weight', 600))
      .call((group) => group.selectAll('line')
        .attr('stroke', 'var(--bx-line)').attr('stroke-dasharray', '2 5'))

    if (!compact) {
      // Tick density follows the space, not the data. One tick per settlement
      // is right at 1,200px and an unreadable smear at 375px, where twelve
      // labels overlapped into a single blot -- so ask for as many as fit at
      // ~52px each and let d3 choose round positions.
      const tickCount = Math.max(2, Math.min(points.length, Math.floor(innerWidth / 52)))
      root.append('g').attr('transform', `translate(0,${innerHeight})`)
        .call(d3.axisBottom(x).ticks(tickCount).tickFormat((value) => {
          const index = Math.round(value as number)
          // d3 may pick a fractional position when few ticks fit; only label
          // the ones that name a real settlement.
          if (Math.abs((value as number) - index) > 0.001) return ''
          return index === 0 ? 'open' : `#${index}`
        }).tickSizeOuter(0))
        .call((group) => group.selectAll('text').attr('fill', 'var(--bx-dim)').attr('font-size', 12))
        .call((group) => group.selectAll('line,path').attr('stroke', 'var(--bx-line-strong)'))
      root.append('text')
        .attr('x', innerWidth / 2).attr('y', innerHeight + 40).attr('text-anchor', 'middle')
        .attr('fill', 'var(--bx-muted)').attr('font-size', 13).attr('font-weight', 600)
        .text(innerWidth < 420
          ? 'settlement order'
          : 'settlement order — the bankroll moves only when a position settles')
    }

    // -- drawdown: peak down to balance ----------------------------------
    const drawdownArea = d3.area<EquityPoint>()
      .x((point) => x(point.sequence))
      .y0((point) => y(point.highWater))
      .y1((point) => y(point.bankroll))
      .curve(d3.curveStepAfter)
    root.append('path').datum(points)
      .attr('fill', 'var(--exp-loss)').attr('opacity', .14)
      .attr('d', drawdownArea)

    // -- the opening bankroll, always in frame ---------------------------
    root.append('line')
      .attr('x1', 0).attr('x2', innerWidth).attr('y1', y(starting)).attr('y2', y(starting))
      .attr('stroke', 'var(--bx-text)').attr('stroke-width', 1.5).attr('stroke-dasharray', '6 5')
      .attr('opacity', .55)
    // Below the line, not above it: the space above the opening balance is
    // where the winning settlements sit, and the label was landing on them.
    root.append('text')
      .attr('x', 2).attr('y', y(starting) + 17)
      .attr('fill', 'var(--bx-muted)').attr('font-size', compact ? 11 : 13).attr('font-weight', 700)
      .text(`opened ${money(starting, 0)}`)

    // -- high-water mark --------------------------------------------------
    if (!compact) {
      root.append('path').datum(points)
        .attr('fill', 'none').attr('stroke', 'var(--bx-muted)').attr('stroke-width', 1.5)
        .attr('stroke-dasharray', '3 4').attr('opacity', .7)
        .attr('d', d3.line<EquityPoint>()
          .x((point) => x(point.sequence)).y((point) => y(point.highWater))
          .curve(d3.curveStepAfter))
    }

    // -- the bankroll itself ----------------------------------------------
    const line = d3.line<EquityPoint>()
      .x((point) => x(point.sequence)).y((point) => y(point.bankroll))
      .curve(d3.curveStepAfter)
    const path = root.append('path').datum(points)
      .attr('fill', 'none').attr('stroke', 'var(--exp-win)')
      .attr('stroke-width', compact ? 2.5 : 3.5)
      .attr('stroke-linejoin', 'round').attr('stroke-linecap', 'round')
      .attr('d', line)
    const length = reduced ? null : pathLength(path.node())
    if (length != null) {
      path.attr('stroke-dasharray', `${length} ${length}`).attr('stroke-dashoffset', length)
        .transition().duration(1100).ease(d3.easeCubicInOut).attr('stroke-dashoffset', 0)
        .on('end', function () { d3.select(this).attr('stroke-dasharray', null) })
    }

    // -- settlement markers: shape carries the result, not colour alone ---
    const settlements = points.filter((point) => point.result != null)
    const markerSize = compact ? 34 : 90
    const symbol = d3.symbol<EquityPoint>()
      .type((point) => (point.result === 'WIN' ? d3.symbolTriangle
        : point.result === 'LOSS' ? d3.symbolSquare : d3.symbolDiamond))
      .size(markerSize)
    const markers = root.selectAll('path.marker').data(settlements).enter().append('path')
      .attr('class', 'marker')
      .attr('d', symbol)
      .attr('transform', (point) => `translate(${x(point.sequence)},${y(point.bankroll)})`
        + (point.result === 'LOSS' ? ' rotate(45)' : ''))
      .attr('fill', (point) => (point.result === 'WIN' ? 'var(--exp-win)'
        : point.result === 'LOSS' ? 'var(--exp-loss)' : 'var(--bx-muted)'))
      .attr('stroke', 'var(--bx-s1)').attr('stroke-width', 1.5)
      .attr('tabindex', compact ? null : 0)
      .attr('role', compact ? null : 'button')
      .attr('aria-label', (point) => `Settlement ${point.sequence}: ${point.selection ?? 'position'}, `
        + `${point.result}, ${signedMoney(point.profitLoss)}, bankroll ${money(point.bankroll)}`)

    if (!compact) {
      const show = (event: Event, point: EquityPoint) => {
        void event
        setHovered({
          point,
          position: point.positionId ? byId.get(point.positionId) ?? null : null,
          x: margin.left + x(point.sequence),
          y: margin.top + y(point.bankroll),
        })
      }
      markers
        .on('mouseenter', show).on('focus', show)
        .on('mouseleave', () => setHovered(null)).on('blur', () => setHovered(null))
        .style('cursor', 'pointer')
    }

    // -- open positions sit past the last settlement, deliberately apart --
    if (annotateOpen) {
      const last = points[points.length - 1]
      const openX = x(last.sequence) + Math.min(46, innerWidth * 0.05)
      root.append('line')
        .attr('x1', x(last.sequence)).attr('x2', openX)
        .attr('y1', y(last.bankroll)).attr('y2', y(last.bankroll))
        .attr('stroke', 'var(--bx-muted)').attr('stroke-width', 2).attr('stroke-dasharray', '3 4')
      root.append('circle')
        .attr('cx', openX).attr('cy', y(last.bankroll)).attr('r', 6)
        .attr('fill', 'var(--bx-s1)').attr('stroke', 'var(--bx-muted)').attr('stroke-width', 2.5)
      root.append('text')
        .attr('x', openX + 10).attr('y', y(last.bankroll) + 4)
        .attr('fill', 'var(--bx-muted)').attr('font-size', 12.5).attr('font-weight', 600)
        .text(`${openCount} open · ${money(openExposure)} at risk`)
    }
  }, [points, starting, width, drawHeight, compact, reduced, byId, annotateOpen, openCount,
      openExposure, margin.left, margin.right, margin.top, margin.bottom])

  return (
    <div ref={ref} className={`exp-chart${compact ? ' exp-chart--compact' : ''}`}>
      <svg ref={svgRef} role="img" width="100%"
        aria-label={`Virtual bankroll after each settlement, from ${money(starting, 0)} to `
          + `${money(points[points.length - 1]?.bankroll ?? starting)}`} />
      {hovered && <ChartTooltip hovered={hovered} />}
      {!compact && (
        <div className="exp-chart__legend">
          <span><i className="exp-key exp-key--win" />Win</span>
          <span><i className="exp-key exp-key--loss" />Loss</span>
          <span><i className="exp-key exp-key--push" />Push</span>
          <span><i className="exp-key exp-key--open" />Open — not counted</span>
          <span><i className="exp-key exp-key--dd" />Drawdown from peak</span>
          <span className="exp-chart__note">
            Stepped, because the bankroll only moves when a position settles.
          </span>
        </div>
      )}
    </div>
  )
}

/** The full position behind one step, so a marker is never just a dot. */
function ChartTooltip({ hovered }: { hovered: Hovered }) {
  const { point, position } = hovered
  const won = point.result === 'WIN'
  return (
    <div className="exp-tip" style={{ left: hovered.x, top: hovered.y }} role="status">
      <div className="exp-tip__head">
        <b>#{point.sequence}</b>
        <span className={`exp-tip__result exp-tip__result--${(point.result ?? 'open').toLowerCase()}`}>
          {point.result}
        </span>
        <em>{point.settledAt ? new Date(point.settledAt).toLocaleString('en-US', {
          month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        }) : point.date}</em>
      </div>
      {position && (
        <dl className="exp-tip__body">
          <div><dt>Matchup</dt><dd>{position.matchup ?? '—'}</dd></div>
          <div><dt>Selection</dt><dd>{position.selection ?? '—'}</dd></div>
          <div><dt>Book</dt><dd>{position.sportsbook ?? 'not recorded'}</dd></div>
          <div><dt>Price</dt><dd>{position.price == null ? '—' : position.price > 0 ? `+${position.price}` : position.price}</dd></div>
          <div><dt>Stake</dt><dd>{money(position.stake)}</dd></div>
        </dl>
      )}
      <div className="exp-tip__foot">
        <span className={won ? 'is-win' : point.result === 'LOSS' ? 'is-loss' : ''}>
          {signedMoney(point.profitLoss)}
        </span>
        <span>{money(point.bankroll - point.profitLoss)} → <b>{money(point.bankroll)}</b></span>
      </div>
    </div>
  )
}
