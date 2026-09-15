/**
 * A tiny D3 step line of one account's realized bankroll, with its starting
 * capital as a hairline. Decorative only in the sense that the same numbers are
 * printed beside it; it is hidden from assistive technology for that reason.
 */
import * as d3 from 'd3'
import { memo, useMemo } from 'react'
import type { BankrollPoint } from '../../services/commandCenter'

export const Sparkline = memo(function Sparkline({ series, color, width = 120, height = 34 }: {
  series: BankrollPoint[]
  color: string
  width?: number
  height?: number
}) {
  const path = useMemo(() => {
    const points = series.filter((p) => p.at).map((p) => ({ t: Date.parse(p.at as string), v: p.bankroll }))
    if (points.length < 2) return null
    points.push({ t: Date.now(), v: points[points.length - 1].v })
    const x = d3.scaleTime().domain(d3.extent(points, (p) => p.t) as [number, number]).range([1, width - 1])
    const [lo, hi] = d3.extent(points, (p) => p.v) as [number, number]
    const pad = Math.max(1, (hi - lo) * 0.2)
    const y = d3.scaleLinear().domain([lo - pad, hi + pad]).range([height - 2, 2])
    return {
      line: d3.line<{ t: number; v: number }>().x((p) => x(p.t)).y((p) => y(p.v)).curve(d3.curveStepAfter)(points),
      start: y(points[0].v),
    }
  }, [series, width, height])
  if (!path) {
    return <svg width={width} height={height} aria-hidden="true"><line x1={0} x2={width} y1={height / 2} y2={height / 2} className="cc-spark__flat" /></svg>
  }
  return (
    <svg width={width} height={height} aria-hidden="true" className="cc-spark">
      <line x1={0} x2={width} y1={path.start} y2={path.start} className="cc-spark__start" />
      <path d={path.line ?? ''} fill="none" stroke={color} strokeWidth={2} />
    </svg>
  )
})
