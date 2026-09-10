/**
 * A reliability diagram: predicted probability against what actually happened.
 *
 * The chart it replaces plotted the same numbers on a category axis and drew a
 * straight "perfect calibration" line beside them. That reads as a calibration
 * plot but it is not one, and it hides the thing a reader most needs:
 *
 *   - **Where the diagonal actually is.** On a category axis, x is a label, not
 *     a position. A bucket at 39% predicted sat the same distance from its
 *     neighbour whether that neighbour was 41% or 80%, so the visual distance
 *     from the diagonal -- the entire point of the plot -- meant nothing.
 *   - **How much evidence each point carries.** One live bucket here is three
 *     games: two won, so it reads 66.7% observed against 39.6% predicted and
 *     looks like a catastrophic miss. It is three games. Drawn at the same
 *     weight as a fifty-game bucket, that is a misleading picture of the model,
 *     and it is misleading in the flattering direction as often as not.
 *
 * So: a true square plot with the identity line where it belongs, marker area
 * proportional to sample count, and a 95% Wilson interval on every bucket. The
 * Wilson interval is used rather than the normal approximation because at n=3
 * the normal interval is nonsense (it runs past 0 and 1); Wilson stays inside
 * the unit interval and stays honest at small n, which is the regime this
 * experiment is actually in.
 *
 * A point whose interval crosses the diagonal is not evidence of miscalibration.
 * The chart says so rather than leaving the reader to assume the worst.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { AnalyticsChart, chartTheme } from './AnalyticsChart'

export type CalibrationBucket = { predicted: number; observed: number; count: number }

/**
 * 95% Wilson score interval for a binomial proportion, in percent.
 *
 * `observed` arrives as a percentage and `count` as the bucket's sample size,
 * which is all Wilson needs: the interval is a function of the proportion and
 * n, so the raw success count never has to be reconstructed.
 */
export function wilsonInterval(observedPct: number, count: number, z = 1.959964): [number, number] {
  if (!Number.isFinite(count) || count <= 0) return [observedPct, observedPct]
  const p = Math.min(1, Math.max(0, observedPct / 100))
  const denominator = 1 + (z * z) / count
  const centre = p + (z * z) / (2 * count)
  const spread = z * Math.sqrt((p * (1 - p)) / count + (z * z) / (4 * count * count))
  const low = (centre - spread) / denominator
  const high = (centre + spread) / denominator
  return [Math.max(0, low) * 100, Math.min(1, high) * 100]
}

/** True when the bucket's interval covers its own predicted probability. */
export function coversDiagonal(bucket: CalibrationBucket): boolean {
  const [low, high] = wilsonInterval(bucket.observed, bucket.count)
  return bucket.predicted >= low && bucket.predicted <= high
}

/** Axis bounds shared by both axes, so one unit of x is one unit of y. */
export function squareBounds(buckets: CalibrationBucket[], pad = 8): [number, number] {
  const values = buckets.flatMap((b) => {
    const [low, high] = wilsonInterval(b.observed, b.count)
    return [b.predicted, b.observed, low, high]
  })
  if (values.length === 0) return [0, 100]
  const lo = Math.max(0, Math.floor((Math.min(...values) - pad) / 10) * 10)
  const hi = Math.min(100, Math.ceil((Math.max(...values) + pad) / 10) * 10)
  // A degenerate window would put every point on top of the diagonal and tell
  // the reader nothing; keep at least 30 points of context.
  if (hi - lo < 30) {
    const centre = (hi + lo) / 2
    return [Math.max(0, centre - 15), Math.min(100, centre + 15)]
  }
  return [lo, hi]
}

const PLOT_TOP = 18
const PLOT_BOTTOM = 62
const AXIS_GUTTER = 62
const PLOT_RIGHT = 24

export function ReliabilityDiagram({ buckets, height = 380 }: { buckets: CalibrationBucket[]; height?: number }) {
  // The diagonal only means "perfect calibration" if the drawing box is square.
  // In a responsive two-column grid it never is by default, so the box is sized
  // in pixels from the observed container width instead of being left to the
  // layout. Without this the identity line lies at whatever angle the column
  // happens to produce, and distance from it -- the entire read of the chart --
  // is no longer comparable between the two axes.
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const node = containerRef.current
    if (!node || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width ?? 0
      setWidth(next)
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  const option = useMemo(() => {
    const counts = buckets.map((b) => b.count)
    const maxCount = Math.max(1, ...counts)
    // Area, not radius, carries the sample size -- a bucket with four times the
    // evidence should look four times as substantial, and radius would make it
    // look sixteen times.
    const radius = (count: number) => 7 + 22 * Math.sqrt(Math.max(0, count) / maxCount)

    const intervals = buckets.map((b) => {
      const [low, high] = wilsonInterval(b.observed, b.count)
      return { ...b, low, high }
    })

    const [lo, hi] = squareBounds(buckets)
    // Square means square in both directions: the box is bounded by the height
    // it was given AND by the width actually available, or it overflows a
    // narrow column and the plot is clipped rather than merely non-square.
    const widthBudget = width > 0 ? width - AXIS_GUTTER - PLOT_RIGHT : Number.POSITIVE_INFINITY
    const box = Math.max(120, Math.min(height - PLOT_TOP - PLOT_BOTTOM, widthBudget))
    // Centre it in whatever width the column gives us, never letting it overlap
    // the y-axis labels.
    const left = width > 0 ? Math.max(AXIS_GUTTER, (width - box) / 2) : AXIS_GUTTER

    return {
      grid: { top: PLOT_TOP, left, width: box, height: box },
      tooltip: {
        trigger: 'item' as const,
        formatter: (params: unknown) => {
          const entry = (Array.isArray(params) ? params[0] : params) as { dataIndex?: number } | undefined
          const bucket = entry?.dataIndex == null ? undefined : intervals[entry.dataIndex]
          if (!bucket) return ''
          const verdict = bucket.predicted >= bucket.low && bucket.predicted <= bucket.high
            ? 'Consistent with perfect calibration at this sample size.'
            : 'Predicted probability falls outside the 95% interval.'
          return [
            `<strong>Predicted ${bucket.predicted.toFixed(1)}%</strong>`,
            `Observed ${bucket.observed.toFixed(1)}%`,
            `95% interval ${bucket.low.toFixed(1)}% – ${bucket.high.toFixed(1)}%`,
            `Sample ${bucket.count} ${bucket.count === 1 ? 'game' : 'games'}`,
            `<em>${verdict}</em>`,
          ].join('<br/>')
        },
      },
      legend: {
        bottom: 4,
        data: ['Observed frequency', 'Perfect calibration', '95% interval'],
        textStyle: { color: chartTheme.muted, fontSize: 11 },
        itemWidth: 14,
      },
      xAxis: {
        type: 'value' as const, min: lo, max: hi,
        name: 'Predicted win probability', nameLocation: 'middle' as const, nameGap: 28,
        nameTextStyle: { color: chartTheme.muted },
        axisLabel: { formatter: '{value}%', color: chartTheme.muted },
        splitLine: { lineStyle: { color: chartTheme.grid } },
        axisLine: { lineStyle: { color: chartTheme.grid } },
      },
      yAxis: {
        type: 'value' as const, min: lo, max: hi,
        name: 'Observed frequency', nameLocation: 'middle' as const, nameGap: 44,
        nameTextStyle: { color: chartTheme.muted },
        axisLabel: { formatter: '{value}%', color: chartTheme.muted },
        splitLine: { lineStyle: { color: chartTheme.grid } },
        axisLine: { lineStyle: { color: chartTheme.grid } },
      },
      series: ([
        {
          // Drawn first so the markers sit on top of it.
          name: 'Perfect calibration',
          type: 'line' as const,
          data: [[lo, lo], [hi, hi]],
          symbol: 'none',
          silent: true,
          lineStyle: { type: 'dashed' as const, width: 1.5, color: chartTheme.muted },
          z: 1,
        },
        {
          name: '95% interval',
          type: 'custom' as const,
          silent: true,
          z: 2,
          renderItem: (_params: unknown, api: {
            value: (index: number) => number
            coord: (point: [number, number]) => [number, number]
          }) => {
            const x = api.value(0)
            const [px, lowY] = api.coord([x, api.value(1)])
            const [, highY] = api.coord([x, api.value(2)])
            const cap = 5
            const stroke = { stroke: 'rgba(8,127,122,.55)', lineWidth: 1.5, fill: 'none' }
            return {
              type: 'group',
              children: [
                { type: 'line', shape: { x1: px, y1: lowY, x2: px, y2: highY }, style: stroke },
                { type: 'line', shape: { x1: px - cap, y1: lowY, x2: px + cap, y2: lowY }, style: stroke },
                { type: 'line', shape: { x1: px - cap, y1: highY, x2: px + cap, y2: highY }, style: stroke },
              ],
            }
          },
          data: intervals.map((b) => [b.predicted, b.low, b.high]),
        },
        {
          name: 'Observed frequency',
          type: 'scatter' as const,
          z: 3,
          data: intervals.map((b) => [b.predicted, b.observed]),
          symbolSize: (_value: unknown, params: { dataIndex: number }) =>
            radius(intervals[params.dataIndex]?.count ?? 0) * 2,
          itemStyle: {
            color: chartTheme.teal,
            opacity: 0.82,
            borderColor: '#ffffff',
            borderWidth: 1.5,
          },
        },
      ] as Record<string, unknown>[]),
    }
  }, [buckets, height, width])

  return (
    <div ref={containerRef}>
      <AnalyticsChart
        option={option as never}
        height={height}
      ariaLabel={
        'Reliability diagram. Predicted win probability on the horizontal axis against observed ' +
        'frequency on the vertical, with the identity line marking perfect calibration. Marker ' +
        'area is proportional to the number of games in the bucket and each point carries a ' +
        '95% Wilson interval. ' +
        buckets
          .map((b) => `${b.predicted.toFixed(0)}% predicted, ${b.observed.toFixed(0)}% observed, ${b.count} games`)
          .join('. ')
        }
      />
    </div>
  )
}
