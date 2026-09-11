/**
 * The charts that carry the argument.
 *
 * WHY D3 AND NOT MORE CARDS
 * -------------------------
 * Every number on this product is a comparison -- model against market, model
 * against what happened, one distribution against another -- and a comparison
 * rendered as two numbers in two boxes makes the reader do the subtraction.
 * These draw the subtraction.
 *
 * D3 is used for the pieces that need a real scale, an axis, a transition
 * between two states, or a brush. Where a static bar already worked, it stays
 * a static bar; nothing here was rewritten in D3 for its own sake.
 *
 * TWO RULES EVERY CHART HERE FOLLOWS
 * ----------------------------------
 * 1. Animation interpolates between values that both exist. A bar grows from
 *    zero to its measured height, or morphs from last week's value to this
 *    week's. No chart invents an intermediate observation to make a line look
 *    smooth, and none extends past its last real point.
 * 2. Reduced motion is honoured by jumping to the final state, never by
 *    dropping the chart. `prefers-reduced-motion` means "do not move", not
 *    "show me less".
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import * as d3 from 'd3'
import './d3Charts.css'

/** True when the viewer has asked the OS for less motion. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true,
  )
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!query) return
    const listener = (event: MediaQueryListEvent) => setReduced(event.matches)
    query.addEventListener('change', listener)
    return () => query.removeEventListener('change', listener)
  }, [])
  return reduced
}

/** Width of the element, so a chart can be drawn to the space it actually has. */
export function useMeasuredWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T | null>(null)
  const [width, setWidth] = useState(fallback)
  // Measure synchronously BEFORE paint, then keep watching.
  //
  // Waiting for ResizeObserver alone left every chart drawn at the fallback
  // width for at least one frame, and an SVG with a 720-wide viewBox inside a
  // 360-wide panel is scaled to half size -- so 15px labels rendered at 7px
  // and the chart looked like a thumbnail of itself. Anywhere the observer
  // never fires at all (a headless capture, a hidden tab that is then
  // photographed) it stayed that way permanently.
  useLayoutEffect(() => {
    const node = ref.current
    if (!node) return
    const measure = () => {
      const measured = node.getBoundingClientRect().width
      if (measured > 0) setWidth((current) => (Math.abs(current - measured) > 1 ? measured : current))
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}

const duration = (reduced: boolean, ms: number) => (reduced ? 0 : ms)

/* ========================================================================= */
/* Model against market                                                      */
/* ========================================================================= */

export interface ProbabilityRow {
  key: string
  label: string
  /** 0-1. The model's probability for the side named by `label`. */
  model: number | null
  /** 0-1. The market's no-vig probability for the SAME side. */
  market: number | null
  color?: string
  href?: string
}

/**
 * Two dots on one scale, joined by the gap between them.
 *
 * A dumbbell rather than paired bars because the quantity the reader wants is
 * the DISTANCE, and paired bars make distance the one thing you cannot see.
 * A row with only one of the two values draws that one dot and says the other
 * is missing -- it never drops to zero, which would read as "the market says
 * 0%" rather than "we have no market".
 */
export function ModelVsMarketChart({
  rows, height = 34, onHover, highlight,
}: {
  rows: ProbabilityRow[]
  height?: number
  onHover?: (key: string | null) => void
  highlight?: string | null
}) {
  const reduced = useReducedMotion()
  const [ref, width] = useMeasuredWidth<HTMLDivElement>(720)
  const svgRef = useRef<SVGSVGElement | null>(null)
  // The label gutter and the gap readout both shrink on a narrow panel: a
  // fixed 150px gutter left barely any axis on a third-width column, which is
  // exactly where this chart lives on the home page.
  const compact = width < 520
  const margin = {
    top: 28, right: compact ? 62 : 74, bottom: 10, left: compact ? 108 : 156,
  }
  const rowHeight = compact ? Math.max(height, 38) : height
  const innerWidth = Math.max(90, width - margin.left - margin.right)
  const chartHeight = rows.length * rowHeight + margin.top + margin.bottom

  useEffect(() => {
    const svg = d3.select(svgRef.current)
    if (!svgRef.current || rows.length === 0) return
    const x = d3.scaleLinear().domain([0, 1]).range([0, innerWidth])

    svg.attr('viewBox', `0 0 ${width} ${chartHeight}`).attr('height', chartHeight)
    let root = svg.select<SVGGElement>('g.root')
    if (root.empty()) root = svg.append('g').attr('class', 'root')
    root.attr('transform', `translate(${margin.left},${margin.top})`)

    // Axis: percentage gridlines, drawn once and reused.
    let axis = root.select<SVGGElement>('g.axis')
    if (axis.empty()) axis = root.append('g').attr('class', 'axis')
    const ticks = axis.selectAll<SVGGElement, number>('g.tick')
      .data(x.ticks(5), (d) => String(d))
    const tickEnter = ticks.enter().append('g').attr('class', 'tick')
    tickEnter.append('line').attr('y1', -12).attr('stroke-dasharray', '2 5')
    tickEnter.append('text').attr('y', -18).attr('text-anchor', 'middle')
    ticks.exit().remove()
    const allTicks = tickEnter.merge(ticks)
    allTicks.attr('transform', (d) => `translate(${x(d)},0)`)
    allTicks.select('line')
      .attr('y2', rows.length * rowHeight - 8)
      .attr('stroke', 'var(--bx-line)')
    allTicks.select('text')
      .attr('fill', 'var(--bx-dim)').attr('font-size', 12).attr('font-weight', 600)
      .text((d) => `${Math.round(d * 100)}%`)

    const groups = root.selectAll<SVGGElement, ProbabilityRow>('g.row')
      .data(rows, (d) => d.key)
    groups.exit().remove()
    const enter = groups.enter().append('g').attr('class', 'row')
    enter.append('rect').attr('class', 'hit').attr('x', -margin.left).attr('height', rowHeight)
      .attr('fill', 'transparent')
    enter.append('text').attr('class', 'label').attr('x', -14).attr('text-anchor', 'end')
    enter.append('line').attr('class', 'connector')
    enter.append('circle').attr('class', 'market').attr('r', 0)
    enter.append('circle').attr('class', 'model').attr('r', 0)
    enter.append('text').attr('class', 'gap').attr('text-anchor', 'start')

    const merged = enter.merge(groups)
    merged.attr('transform', (_, index) => `translate(0,${index * rowHeight})`)
    merged.select<SVGRectElement>('rect.hit')
      .attr('width', width)
      .style('cursor', (d) => (d.href ? 'pointer' : 'default'))
      .on('mouseenter', (_, d) => onHover?.(d.key))
      .on('mouseleave', () => onHover?.(null))
      .on('click', (_, d) => { if (d.href) window.location.assign(d.href) })

    merged.select<SVGTextElement>('text.label')
      .attr('y', rowHeight / 2 + 5)
      .attr('font-size', compact ? 13.5 : 15)
      .attr('font-weight', (d) => (d.key === highlight ? 700 : 500))
      .attr('fill', 'var(--bx-text)')
      .text((d) => d.label)

    const mid = rowHeight / 2
    merged.select<SVGLineElement>('line.connector')
      .attr('y1', mid).attr('y2', mid)
      .attr('stroke-width', (d) => (d.key === highlight ? 7 : 5))
      .attr('stroke-linecap', 'round')
      .attr('stroke', (d) => (d.model != null && d.market != null
        ? (d.model >= d.market ? 'var(--bx-good)' : 'var(--bx-bad)') : 'transparent'))
      .attr('opacity', 0.35)
      .transition().duration(duration(reduced, 620))
      .attr('x1', (d) => x(Math.min(d.model ?? 0, d.market ?? 0)))
      .attr('x2', (d) => x(Math.max(d.model ?? 0, d.market ?? 0)))

    merged.select<SVGCircleElement>('circle.market')
      .attr('cy', mid).attr('fill', 'var(--bx-s1)')
      .attr('stroke', 'var(--bx-muted)').attr('stroke-width', 3)
      .attr('opacity', (d) => (d.market == null ? 0 : 1))
      .transition().duration(duration(reduced, 620))
      .attr('cx', (d) => x(d.market ?? 0))
      .attr('r', (d) => (d.market == null ? 0 : 7))

    merged.select<SVGCircleElement>('circle.model')
      .attr('cy', mid)
      .attr('fill', (d) => d.color ?? 'var(--bx-accent)')
      .attr('stroke', 'var(--bx-s1)').attr('stroke-width', 2)
      .attr('opacity', (d) => (d.model == null ? 0 : 1))
      .transition().duration(duration(reduced, 620)).delay(duration(reduced, 90))
      .attr('cx', (d) => x(d.model ?? 0))
      .attr('r', (d) => (d.model == null ? 0 : 9))

    merged.select<SVGTextElement>('text.gap')
      .attr('y', mid + 5).attr('font-size', compact ? 12.5 : 14).attr('font-weight', 700)
      .attr('fill', (d) => (d.model != null && d.market != null
        ? (d.model >= d.market ? 'var(--bx-good)' : 'var(--bx-bad)') : 'var(--bx-dim)'))
      .attr('x', innerWidth + 14)
      .text((d) => {
        if (d.model == null) return 'no forecast'
        if (d.market == null) return 'no market'
        const gap = (d.model - d.market) * 100
        return `${gap > 0 ? '+' : ''}${gap.toFixed(1)} pp`
      })
  }, [rows, width, innerWidth, chartHeight, rowHeight, reduced, highlight, onHover, margin.left, margin.right, margin.top])

  if (rows.length === 0) {
    return <p className="bx-muted">No game currently has both a forecast and a comparable market price.</p>
  }
  return (
    <div ref={ref} className="d3-chart">
      <svg ref={svgRef} role="img" width="100%"
        aria-label={`Model probability against market probability for ${rows.length} games`} />
      <div className="d3-legend">
        <span><i style={{ background: 'var(--bx-accent)' }} /> Model</span>
        <span><i className="is-hollow" /> Market (no-vig)</span>
        <span className="bx-muted">The bar is the disagreement, not a recommendation.</span>
      </div>
    </div>
  )
}

/* ========================================================================= */
/* Predicted against actual                                                  */
/* ========================================================================= */

export interface ActualVsPredicted {
  key: string
  label: string
  predicted: number
  actual: number
  unit?: string
}

/**
 * Predicted and actual on one axis, with the error drawn as the span.
 *
 * The whole NE-SEA story is here in one picture: the margin span is a hair
 * wide and the total span is enormous. Two numbers in two boxes would have
 * made that a subtraction exercise.
 */
export function PredictedVsActualChart({ rows, domain }: {
  rows: ActualVsPredicted[]
  /** Force a shared scale so margin and total errors stay comparable. */
  domain?: [number, number]
}) {
  const reduced = useReducedMotion()
  const [ref, width] = useMeasuredWidth<HTMLDivElement>(600)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const rowHeight = 58
  const margin = { top: 28, right: 24, bottom: 10, left: 118 }

  const scaleDomain = useMemo<[number, number]>(() => {
    if (domain) return domain
    const values = rows.flatMap((row) => [row.predicted, row.actual])
    const min = Math.min(...values, 0)
    const max = Math.max(...values, 0)
    const pad = Math.max(2, (max - min) * 0.12)
    return [min - pad, max + pad]
  }, [rows, domain])

  useEffect(() => {
    if (!svgRef.current || rows.length === 0) return
    const svg = d3.select(svgRef.current)
    const innerWidth = Math.max(120, width - margin.left - margin.right)
    const height = rows.length * rowHeight + margin.top + margin.bottom
    const x = d3.scaleLinear().domain(scaleDomain).range([0, innerWidth])

    svg.attr('viewBox', `0 0 ${width} ${height}`).attr('height', height)
    let root = svg.select<SVGGElement>('g.root')
    if (root.empty()) root = svg.append('g').attr('class', 'root')
    root.attr('transform', `translate(${margin.left},${margin.top})`)

    let axis = root.select<SVGGElement>('g.axis')
    if (axis.empty()) axis = root.append('g').attr('class', 'axis')
    const ticks = axis.selectAll<SVGGElement, number>('g.tick').data(x.ticks(6), (d) => String(d))
    const enterTick = ticks.enter().append('g').attr('class', 'tick')
    enterTick.append('line').attr('y1', -14).attr('stroke-dasharray', '2 5')
    enterTick.append('text').attr('y', -20).attr('text-anchor', 'middle')
    ticks.exit().remove()
    const allTicks = enterTick.merge(ticks)
    allTicks.attr('transform', (d) => `translate(${x(d)},0)`)
    allTicks.select('line').attr('y2', rows.length * rowHeight - 14).attr('stroke', 'var(--bx-line)')
    allTicks.select('text').attr('fill', 'var(--bx-dim)').attr('font-size', 12).attr('font-weight', 600).text(String)

    const groups = root.selectAll<SVGGElement, ActualVsPredicted>('g.row').data(rows, (d) => d.key)
    groups.exit().remove()
    const enter = groups.enter().append('g').attr('class', 'row')
    enter.append('text').attr('class', 'label').attr('x', -14).attr('text-anchor', 'end')
    enter.append('line').attr('class', 'span')
    enter.append('circle').attr('class', 'pred').attr('r', 0)
    enter.append('circle').attr('class', 'act').attr('r', 0)
    enter.append('text').attr('class', 'err')

    const merged = enter.merge(groups)
    merged.attr('transform', (_, index) => `translate(0,${index * rowHeight})`)
    const mid = rowHeight / 2 - 6

    merged.select<SVGTextElement>('text.label')
      .attr('y', mid + 5).attr('font-size', 15).attr('font-weight', 600).attr('fill', 'var(--bx-text)')
      .text((d) => d.label)

    merged.select<SVGLineElement>('line.span')
      .attr('y1', mid).attr('y2', mid).attr('stroke-width', 8).attr('stroke-linecap', 'round')
      .attr('stroke', (d) => (Math.abs(d.predicted - d.actual) > 5 ? 'var(--bx-bad)' : 'var(--bx-good)'))
      .attr('opacity', .3)
      .transition().duration(duration(reduced, 700))
      .attr('x1', (d) => x(Math.min(d.predicted, d.actual)))
      .attr('x2', (d) => x(Math.max(d.predicted, d.actual)))

    merged.select<SVGCircleElement>('circle.act')
      .attr('cy', mid).attr('fill', 'var(--bx-text)')
      .transition().duration(duration(reduced, 700))
      .attr('cx', (d) => x(d.actual)).attr('r', 9)

    merged.select<SVGCircleElement>('circle.pred')
      .attr('cy', mid).attr('fill', 'var(--bx-s1)').attr('stroke', 'var(--bx-accent)').attr('stroke-width', 4)
      .transition().duration(duration(reduced, 700)).delay(duration(reduced, 80))
      .attr('cx', (d) => x(d.predicted)).attr('r', 8)

    merged.select<SVGTextElement>('text.err')
      .attr('y', mid + 26).attr('font-size', 13.5).attr('font-weight', 700)
      .attr('text-anchor', 'middle')
      .attr('fill', (d) => (Math.abs(d.predicted - d.actual) > 5 ? 'var(--bx-bad)' : 'var(--bx-good)'))
      .attr('x', (d) => x((d.predicted + d.actual) / 2))
      .text((d) => {
        const error = d.predicted - d.actual
        return `${error > 0 ? '+' : ''}${error.toFixed(2)}${d.unit ? ` ${d.unit}` : ''}`
      })
  }, [rows, width, scaleDomain, reduced, margin.left, margin.right, margin.top, margin.bottom])

  if (rows.length === 0) return null
  return (
    <div ref={ref} className="d3-chart">
      <svg ref={svgRef} role="img" width="100%" aria-label="Predicted values against actual values" />
      <div className="d3-legend">
        <span><i className="is-hollow" style={{ borderColor: 'var(--bx-accent)' }} /> Predicted</span>
        <span><i style={{ background: 'var(--bx-text)' }} /> Actual</span>
        <span className="bx-muted">One shared scale, so a small miss looks small.</span>
      </div>
    </div>
  )
}

/* ========================================================================= */
/* Market movement                                                           */
/* ========================================================================= */

export interface MovementPoint { t: string; value: number | null }

/**
 * A price over time, with a hover readout.
 *
 * Gaps are gaps. `d3.line().defined()` breaks the path where no quote was
 * recorded rather than interpolating across the hole, because a straight line
 * through six unobserved hours is a claim about six unobserved hours.
 */
export function MovementChart({
  series, height = 190, label, formatValue = (value) => value.toFixed(1),
}: {
  series: { key: string; label: string; color: string; points: MovementPoint[] }[]
  height?: number
  label?: string
  formatValue?: (value: number) => string
}) {
  const reduced = useReducedMotion()
  const [ref, width] = useMeasuredWidth<HTMLDivElement>(680)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const [readout, setReadout] = useState<{ t: string; values: { label: string; color: string; value: number | null }[] } | null>(null)
  const margin = { top: 14, right: 16, bottom: 30, left: 48 }

  const usable = series.filter((entry) => entry.points.some((point) => point.value != null))

  useEffect(() => {
    if (!svgRef.current || usable.length === 0) return
    const svg = d3.select(svgRef.current)
    const innerWidth = Math.max(80, width - margin.left - margin.right)
    const innerHeight = height - margin.top - margin.bottom
    const times = usable[0].points.map((point) => new Date(point.t))
    const allValues = usable.flatMap((entry) => entry.points.map((point) => point.value)).filter((value): value is number => value != null)
    const x = d3.scaleTime().domain(d3.extent(times) as [Date, Date]).range([0, innerWidth])
    const pad = Math.max(0.5, (d3.max(allValues)! - d3.min(allValues)!) * 0.18)
    const y = d3.scaleLinear().domain([d3.min(allValues)! - pad, d3.max(allValues)! + pad]).nice().range([innerHeight, 0])

    svg.attr('viewBox', `0 0 ${width} ${height}`).attr('height', height)
    svg.selectAll('*').remove()
    const root = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

    root.append('g').attr('transform', `translate(0,${innerHeight})`)
      .call(d3.axisBottom(x).ticks(Math.max(2, Math.floor(innerWidth / 110))).tickSizeOuter(0))
      .call((group) => group.selectAll('text').attr('fill', 'var(--bx-dim)').attr('font-size', 12))
      .call((group) => group.selectAll('line,path').attr('stroke', 'var(--bx-line-strong)'))
    root.append('g')
      .call(d3.axisLeft(y).ticks(4).tickSize(-innerWidth).tickFormat((value) => formatValue(value as number)))
      .call((group) => group.select('.domain').remove())
      .call((group) => group.selectAll('text').attr('fill', 'var(--bx-dim)').attr('font-size', 12))
      .call((group) => group.selectAll('line').attr('stroke', 'var(--bx-line)').attr('stroke-dasharray', '2 5'))

    const line = d3.line<MovementPoint>()
      .defined((point) => point.value != null)
      .x((point) => x(new Date(point.t)))
      .y((point) => y(point.value as number))
      .curve(d3.curveMonotoneX)

    usable.forEach((entry) => {
      const path = root.append('path')
        .datum(entry.points)
        .attr('fill', 'none').attr('stroke', entry.color).attr('stroke-width', 2.5)
        .attr('stroke-linejoin', 'round').attr('stroke-linecap', 'round')
        .attr('d', line)
      if (!reduced) {
        const length = (path.node() as SVGPathElement).getTotalLength()
        path.attr('stroke-dasharray', `${length} ${length}`).attr('stroke-dashoffset', length)
          .transition().duration(900).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0)
          .on('end', function () { d3.select(this).attr('stroke-dasharray', null) })
      }
    })

    const marker = root.append('line').attr('y1', 0).attr('y2', innerHeight)
      .attr('stroke', 'var(--bx-text)').attr('stroke-width', 1).attr('opacity', 0)
    root.append('rect')
      .attr('width', innerWidth).attr('height', innerHeight).attr('fill', 'transparent')
      .on('mousemove', (event) => {
        const [px] = d3.pointer(event)
        const moment = x.invert(px)
        let closest = 0
        let best = Infinity
        times.forEach((time, index) => {
          const distance = Math.abs(time.getTime() - moment.getTime())
          if (distance < best) { best = distance; closest = index }
        })
        marker.attr('opacity', .45).attr('x1', x(times[closest])).attr('x2', x(times[closest]))
        setReadout({
          t: usable[0].points[closest].t,
          values: usable.map((entry) => ({
            label: entry.label, color: entry.color, value: entry.points[closest]?.value ?? null,
          })),
        })
      })
      .on('mouseleave', () => { marker.attr('opacity', 0); setReadout(null) })
  }, [usable, width, height, reduced, formatValue, margin.left, margin.right, margin.top, margin.bottom])

  if (usable.length === 0) {
    return <p className="bx-muted">No recorded movement for this market yet.</p>
  }
  return (
    <div ref={ref} className="d3-chart">
      {label && <p className="d3-chart__title">{label}</p>}
      <svg ref={svgRef} role="img" width="100%" aria-label={label ?? 'Market movement over time'} />
      <div className="d3-readout" aria-live="polite">
        {readout
          ? <>
            <b>{new Date(readout.t).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</b>
            {readout.values.map((entry) => (
              <span key={entry.label}>
                <i style={{ background: entry.color }} />{entry.label}{' '}
                <b>{entry.value == null ? 'no quote' : formatValue(entry.value)}</b>
              </span>
            ))}
          </>
          : <span className="bx-muted">Hover the chart to read any moment. Breaks in a line are hours with no recorded quote.</span>}
      </div>
    </div>
  )
}

/* ========================================================================= */
/* Distributions                                                             */
/* ========================================================================= */

/**
 * A saved outcome distribution, with the actual result marked on it.
 *
 * Bars are the saved population; the marker is what happened. Both are
 * measured, so the picture says how far into the tail the real game fell
 * without anyone computing a z-score.
 */
export function DistributionChart({
  bins, actual, expected, height = 160, label, unit = '',
}: {
  bins: { value: number; probability: number }[]
  actual?: number | null
  expected?: number | null
  height?: number
  label?: string
  unit?: string
}) {
  const reduced = useReducedMotion()
  const [ref, width] = useMeasuredWidth<HTMLDivElement>(520)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const margin = { top: 22, right: 14, bottom: 30, left: 14 }

  useEffect(() => {
    if (!svgRef.current || bins.length === 0) return
    const svg = d3.select(svgRef.current)
    const innerWidth = Math.max(80, width - margin.left - margin.right)
    const innerHeight = height - margin.top - margin.bottom
    const x = d3.scaleLinear()
      .domain([d3.min(bins, (bin) => bin.value)!, d3.max(bins, (bin) => bin.value)!])
      .range([0, innerWidth])
    const y = d3.scaleLinear().domain([0, d3.max(bins, (bin) => bin.probability)!]).range([innerHeight, 0])
    const barWidth = Math.max(1.5, innerWidth / bins.length - 1.5)

    svg.attr('viewBox', `0 0 ${width} ${height}`).attr('height', height)
    svg.selectAll('*').remove()
    const root = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

    root.append('g').attr('transform', `translate(0,${innerHeight})`)
      .call(d3.axisBottom(x).ticks(Math.max(3, Math.floor(innerWidth / 90))).tickSizeOuter(0))
      .call((group) => group.selectAll('text').attr('fill', 'var(--bx-dim)').attr('font-size', 12))
      .call((group) => group.selectAll('line,path').attr('stroke', 'var(--bx-line-strong)'))

    const bars = root.selectAll('rect.bin').data(bins).enter().append('rect')
      .attr('class', 'bin')
      .attr('x', (bin) => x(bin.value) - barWidth / 2)
      .attr('width', barWidth)
      .attr('rx', 1.5)
      .attr('fill', (bin) => (actual != null && Math.abs(bin.value - actual) < 0.5
        ? 'var(--bx-text)' : 'var(--bx-accent)'))
      .attr('opacity', .75)
      .attr('y', innerHeight).attr('height', 0)
    if (reduced) {
      bars.attr('y', (bin) => y(bin.probability)).attr('height', (bin) => innerHeight - y(bin.probability))
    } else {
      bars.transition().duration(700).delay((_, index) => index * 3)
        .attr('y', (bin) => y(bin.probability))
        .attr('height', (bin) => innerHeight - y(bin.probability))
    }

    const mark = (value: number, color: string, text: string, dash: string | null) => {
      root.append('line')
        .attr('x1', x(value)).attr('x2', x(value)).attr('y1', -6).attr('y2', innerHeight)
        .attr('stroke', color).attr('stroke-width', 2.5)
        .attr('stroke-dasharray', dash)
      root.append('text')
        .attr('x', x(value)).attr('y', -10).attr('text-anchor', 'middle')
        .attr('fill', color).attr('font-size', 12.5).attr('font-weight', 700)
        .text(text)
    }
    if (expected != null) mark(expected, 'var(--bx-warn)', `expected ${expected.toFixed(1)}${unit}`, '4 3')
    if (actual != null) mark(actual, 'var(--bx-bad)', `actual ${actual}${unit}`, null)
  }, [bins, actual, expected, width, height, reduced, unit, margin.left, margin.right, margin.top, margin.bottom])

  if (bins.length === 0) return null
  return (
    <div ref={ref} className="d3-chart">
      {label && <p className="d3-chart__title">{label}</p>}
      <svg ref={svgRef} role="img" width="100%"
        aria-label={label ?? 'Saved outcome distribution with the actual result marked'} />
    </div>
  )
}

/* ========================================================================= */
/* Calibration                                                               */
/* ========================================================================= */

export interface CalibrationBin {
  label: string
  forecast: number
  observed: number | null
  n: number
  low?: number | null
  high?: number | null
}

/**
 * Forecast probability against observed frequency, with the sample on every point.
 *
 * At small n this diagram is mostly noise, and the honest way to say so is to
 * draw the interval and print the count -- not to hide the chart until the
 * sample is respectable. A bin with n=1 gets a dot the size of n=1 and an
 * interval that spans most of the panel, which reads correctly as "we do not
 * know yet".
 */
export function CalibrationChart({ bins, size = 320 }: { bins: CalibrationBin[]; size?: number }) {
  const reduced = useReducedMotion()
  const svgRef = useRef<SVGSVGElement | null>(null)
  const margin = { top: 16, right: 18, bottom: 42, left: 50 }

  useEffect(() => {
    if (!svgRef.current) return
    const svg = d3.select(svgRef.current)
    const inner = size - margin.left - margin.right
    const x = d3.scaleLinear().domain([0, 1]).range([0, inner])
    const y = d3.scaleLinear().domain([0, 1]).range([inner, 0])

    svg.attr('viewBox', `0 0 ${size} ${size - margin.right + margin.bottom - 6}`)
    svg.selectAll('*').remove()
    const root = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

    root.append('rect').attr('width', inner).attr('height', inner)
      .attr('fill', 'var(--bx-sunk)').attr('rx', 8)
    root.append('line')
      .attr('x1', x(0)).attr('y1', y(0)).attr('x2', x(1)).attr('y2', y(1))
      .attr('stroke', 'var(--bx-line-strong)').attr('stroke-width', 2).attr('stroke-dasharray', '5 5')
    root.append('text').attr('x', inner - 6).attr('y', y(1) + 18)
      .attr('text-anchor', 'end').attr('fill', 'var(--bx-dim)').attr('font-size', 12)
      .text('perfect calibration')

    root.append('g').attr('transform', `translate(0,${inner})`)
      .call(d3.axisBottom(x).ticks(5).tickFormat((value) => `${Math.round((value as number) * 100)}%`))
      .call((group) => group.selectAll('text').attr('fill', 'var(--bx-dim)').attr('font-size', 12))
      .call((group) => group.selectAll('line,path').attr('stroke', 'var(--bx-line-strong)'))
    root.append('g')
      .call(d3.axisLeft(y).ticks(5).tickFormat((value) => `${Math.round((value as number) * 100)}%`))
      .call((group) => group.selectAll('text').attr('fill', 'var(--bx-dim)').attr('font-size', 12))
      .call((group) => group.selectAll('line,path').attr('stroke', 'var(--bx-line-strong)'))
    root.append('text').attr('x', inner / 2).attr('y', inner + 38).attr('text-anchor', 'middle')
      .attr('fill', 'var(--bx-muted)').attr('font-size', 13).attr('font-weight', 600)
      .text('forecast probability')
    root.append('text').attr('transform', `rotate(-90)`).attr('x', -inner / 2).attr('y', -34)
      .attr('text-anchor', 'middle').attr('fill', 'var(--bx-muted)').attr('font-size', 13).attr('font-weight', 600)
      .text('observed frequency')

    const usable = bins.filter((bin) => bin.observed != null)
    const radius = d3.scaleSqrt().domain([1, Math.max(2, d3.max(bins, (bin) => bin.n) ?? 1)]).range([5, 15])

    usable.forEach((bin) => {
      if (bin.low != null && bin.high != null) {
        root.append('line')
          .attr('x1', x(bin.forecast)).attr('x2', x(bin.forecast))
          .attr('y1', y(bin.low)).attr('y2', y(bin.high))
          .attr('stroke', 'var(--bx-accent)').attr('stroke-width', 3)
          .attr('stroke-linecap', 'round').attr('opacity', .35)
      }
    })
    const dots = root.selectAll('circle.bin').data(usable).enter().append('circle')
      .attr('class', 'bin')
      .attr('cx', (bin) => x(bin.forecast))
      .attr('cy', (bin) => y(bin.observed as number))
      .attr('fill', 'var(--bx-accent)').attr('stroke', 'var(--bx-s1)').attr('stroke-width', 2)
      .attr('r', 0)
    if (reduced) dots.attr('r', (bin) => radius(bin.n))
    else dots.transition().duration(600).delay((_, index) => index * 70).attr('r', (bin) => radius(bin.n))

    usable.forEach((bin) => {
      root.append('text')
        .attr('x', x(bin.forecast)).attr('y', y(bin.observed as number) - radius(bin.n) - 7)
        .attr('text-anchor', 'middle').attr('fill', 'var(--bx-muted)')
        .attr('font-size', 12).attr('font-weight', 700)
        .text(`n=${bin.n}`)
    })
  }, [bins, size, reduced, margin.bottom, margin.left, margin.right, margin.top])

  return (
    <div className="d3-chart d3-chart--square">
      <svg ref={svgRef} role="img" width="100%"
        aria-label="Calibration: forecast probability against observed frequency, sized by sample" />
    </div>
  )
}

/* ========================================================================= */
/* Accumulation                                                              */
/* ========================================================================= */

/**
 * A running count as evidence is revealed, for the simulation workspace.
 *
 * Deliberately a step line: each point is one more play that was actually
 * drawn from the saved population. A smooth curve would imply we know what
 * happened between two plays, and there is no between.
 */
export function AccumulationChart({
  points, height = 120, color = 'var(--bx-accent)', label, valueLabel,
}: {
  points: { x: number; y: number }[]
  height?: number
  color?: string
  label?: string
  valueLabel?: string
}) {
  const [ref, width] = useMeasuredWidth<HTMLDivElement>(420)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const margin = { top: 12, right: 12, bottom: 24, left: 42 }

  useEffect(() => {
    if (!svgRef.current || points.length < 2) return
    const svg = d3.select(svgRef.current)
    const innerWidth = Math.max(60, width - margin.left - margin.right)
    const innerHeight = height - margin.top - margin.bottom
    const x = d3.scaleLinear().domain(d3.extent(points, (point) => point.x) as [number, number]).range([0, innerWidth])
    const y = d3.scaleLinear().domain([0, d3.max(points, (point) => point.y) as number]).nice().range([innerHeight, 0])

    svg.attr('viewBox', `0 0 ${width} ${height}`).attr('height', height)
    svg.selectAll('*').remove()
    const root = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

    root.append('g').attr('transform', `translate(0,${innerHeight})`)
      .call(d3.axisBottom(x).ticks(4).tickSizeOuter(0))
      .call((group) => group.selectAll('text').attr('fill', 'var(--bx-dim)').attr('font-size', 11))
      .call((group) => group.selectAll('line,path').attr('stroke', 'var(--bx-line-strong)'))
    root.append('g')
      .call(d3.axisLeft(y).ticks(3).tickSize(-innerWidth))
      .call((group) => group.select('.domain').remove())
      .call((group) => group.selectAll('text').attr('fill', 'var(--bx-dim)').attr('font-size', 11))
      .call((group) => group.selectAll('line').attr('stroke', 'var(--bx-line)').attr('stroke-dasharray', '2 4'))

    const area = d3.area<{ x: number; y: number }>()
      .x((point) => x(point.x)).y0(innerHeight).y1((point) => y(point.y))
      .curve(d3.curveStepAfter)
    root.append('path').datum(points).attr('fill', color).attr('opacity', .16).attr('d', area)
    root.append('path').datum(points)
      .attr('fill', 'none').attr('stroke', color).attr('stroke-width', 2.5)
      .attr('d', d3.line<{ x: number; y: number }>()
        .x((point) => x(point.x)).y((point) => y(point.y)).curve(d3.curveStepAfter))
  }, [points, width, height, color, margin.bottom, margin.left, margin.right, margin.top])

  if (points.length < 2) {
    return <p className="bx-muted" style={{ fontSize: 13.5 }}>Not enough revealed evidence to plot yet.</p>
  }
  return (
    <div ref={ref} className="d3-chart">
      {label && <p className="d3-chart__title">{label} {valueLabel && <b>{valueLabel}</b>}</p>}
      <svg ref={svgRef} role="img" width="100%" aria-label={label ?? 'Accumulated evidence'} />
    </div>
  )
}
