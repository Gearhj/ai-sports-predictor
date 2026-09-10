/**
 * The bankroll's path, told as the three things a reader actually asks of it.
 *
 * The chart this replaces drew the bankroll, the opening line and the
 * high-water mark as three similar-looking strokes. All three were present and
 * none was emphasised, so the eye had to do the work of separating "where the
 * money is" from "where the money has been" from "where it started".
 *
 * Here the same three series carry different visual weight:
 *
 *   - **Bankroll** is the subject: a filled step line, because the balance
 *     really does hold flat between settlements and then jump. A smoothed line
 *     would draw movement on days nothing settled, which is a lie about when
 *     risk was taken.
 *   - **Drawdown** is shaded between the high-water mark and the balance, so
 *     the distance from the peak is a visible area rather than a number in a
 *     table. This is the quantity that decides whether a strategy is survivable.
 *   - **Opening bankroll** is a quiet reference line, not a competitor.
 *
 * Settlement markers are coloured by result, so wins and losses are legible
 * without reading the tooltip. Colours are taken from the governance palette
 * already in use; nothing here invents a new meaning for a colour.
 *
 * Every value drawn is passed in from the API payload. This component computes
 * no financial figure of its own -- the one derived value, the high-water mark,
 * is only a fallback for older payloads that omit it, and it is the running
 * maximum by definition rather than a re-derivation of anything.
 */
import { useMemo } from 'react'
import { AnalyticsChart, chartTheme } from './AnalyticsChart'
import type { EquityCurvePoint } from '../types/domain'

const RESULT_COLOUR: Record<string, string> = {
  WIN: '#227558',
  LOSS: '#b6454d',
  PUSH: '#a97018',
  VOID: '#6d7d91',
}

export type EquityCurveLabels = {
  bankroll: string
  benchmark: string
  highWater: string
  drawdown: string
}

const DEFAULT_LABELS: EquityCurveLabels = {
  bankroll: 'Virtual bankroll after each settlement',
  benchmark: 'Opening bankroll',
  highWater: 'High-water mark',
  drawdown: 'Drawdown from peak',
}

/** The running maximum, used only when a payload predates `high_water`. */
export function runningHighWater(curve: EquityCurvePoint[]): number[] {
  let peak = Number.NEGATIVE_INFINITY
  return curve.map((point) => {
    peak = Math.max(peak, point.bankroll)
    return point.high_water ?? peak
  })
}

export function EquityCurveChart({
  curve,
  height = 400,
  labels = DEFAULT_LABELS,
  formatCurrency,
  formatSignedCurrency,
}: {
  curve: EquityCurvePoint[]
  height?: number
  labels?: EquityCurveLabels
  formatCurrency: (value: number) => string
  formatSignedCurrency: (value: number) => string
}) {
  const option = useMemo(() => {
    const highWater = runningHighWater(curve)
    const axisLabels = curve.map((point) => (
      point.sequence == null ? point.date
        : point.sequence === 0 ? `Open · ${point.date}`
        : `#${point.sequence} · ${point.date}`
    ))

    const low = Math.min(...curve.map((point) => point.bankroll))
    const high = Math.max(...highWater)
    // A bankroll chart that starts its axis at the lowest value exaggerates
    // every wiggle into a cliff. Pad by a tenth of the range so the shape is
    // readable without being theatrical.
    const pad = Math.max(2, (high - low) * 0.12)

    return {
      grid: { top: 22, left: 68, right: 26, bottom: 74 },
      tooltip: {
        trigger: 'axis' as const,
        axisPointer: { type: 'line' as const, lineStyle: { color: 'rgba(24,59,101,.28)' } },
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params]
          const index = (list[0] as { dataIndex?: number } | undefined)?.dataIndex
          const point = index == null ? undefined : curve[index]
          if (!point || index == null) return ''
          const opening = point.sequence === 0 || point.sequence == null
          const head = opening
            ? `<strong>Opening bankroll</strong> · ${point.date}`
            : `<strong>Settlement #${point.sequence}</strong> · ${point.date}`
          const rows = [head]
          if (!opening && point.selection) {
            const colour = point.result ? RESULT_COLOUR[point.result] ?? chartTheme.muted : chartTheme.muted
            rows.push(
              `${point.selection}` +
              (point.result ? ` · <span style="color:${colour};font-weight:700">${point.result}</span>` : ''),
            )
          }
          rows.push(`Bankroll ${formatCurrency(point.bankroll)}`)
          if (!opening && point.profit_loss != null) rows.push(`P/L ${formatSignedCurrency(point.profit_loss)}`)
          const peak = highWater[index]
          const drawdown = point.drawdown ?? (peak - point.bankroll)
          rows.push(`${labels.highWater} ${formatCurrency(peak)}`)
          rows.push(
            drawdown > 0.005
              ? `${labels.drawdown} ${formatCurrency(drawdown)}`
              : 'At the high-water mark',
          )
          return rows.join('<br/>')
        },
      },
      legend: {
        bottom: 0,
        data: [labels.bankroll, labels.drawdown, labels.benchmark, labels.highWater],
        textStyle: { color: chartTheme.muted, fontSize: 11 },
        itemWidth: 14,
      },
      xAxis: {
        type: 'category' as const,
        boundaryGap: false,
        data: axisLabels,
        axisLabel: {
          color: chartTheme.muted, interval: 0,
          rotate: axisLabels.length > 12 ? 45 : 0, fontSize: 10,
        },
        axisLine: { lineStyle: { color: chartTheme.grid } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value' as const,
        min: Math.floor(low - pad),
        max: Math.ceil(high + pad),
        axisLabel: { formatter: (value: number) => formatCurrency(value), color: chartTheme.muted },
        splitLine: { lineStyle: { color: chartTheme.grid } },
      },
      series: ([
        /*
         * Drawdown is drawn as a stacked pair: an invisible band up to the
         * balance, then a shaded band of the remaining height to the peak. That
         * renders the gap itself rather than two lines the reader must subtract.
         */
        {
          name: 'drawdown-base', type: 'line', step: 'end', stack: 'drawdown',
          data: curve.map((point) => point.bankroll),
          symbol: 'none', silent: true, lineStyle: { opacity: 0 },
          areaStyle: { opacity: 0 }, tooltip: { show: false }, legendHoverLink: false, z: 1,
        },
        {
          name: labels.drawdown, type: 'line', step: 'end', stack: 'drawdown',
          data: curve.map((point, index) => Math.max(0, highWater[index] - point.bankroll)),
          symbol: 'none', silent: true, lineStyle: { opacity: 0 },
          areaStyle: { color: 'rgba(182,69,77,.13)' }, z: 1,
        },
        {
          name: labels.highWater, type: 'line', step: 'end',
          data: highWater, symbol: 'none',
          lineStyle: { type: 'dotted', width: 1.5, color: '#94a3b3' }, z: 2,
        },
        {
          name: labels.benchmark, type: 'line',
          data: curve.map((point) => point.benchmark), symbol: 'none',
          lineStyle: { type: 'dashed', width: 1.5, color: chartTheme.muted }, z: 2,
        },
        {
          name: labels.bankroll, type: 'line', step: 'end',
          data: curve.map((point, index) => ({
            value: point.bankroll,
            // The opening point is not a result, so it gets a neutral marker
            // rather than being coloured as though something had settled.
            itemStyle: {
              color: index === 0 || !point.result
                ? '#ffffff'
                : RESULT_COLOUR[point.result] ?? chartTheme.teal,
              borderColor: index === 0 || !point.result
                ? chartTheme.muted
                : RESULT_COLOUR[point.result] ?? chartTheme.teal,
              borderWidth: 2,
            },
          })),
          symbolSize: 9,
          lineStyle: { width: 3, color: chartTheme.teal },
          itemStyle: { color: chartTheme.teal },
          areaStyle: {
            color: {
              type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(8,127,122,.26)' },
                { offset: 1, color: 'rgba(8,127,122,.02)' },
              ],
            },
          },
          z: 3,
        },
      ] as Record<string, unknown>[]),
    }
  }, [curve, labels, formatCurrency, formatSignedCurrency])

  return (
    <AnalyticsChart
      option={option as never}
      height={height}
      ariaLabel={
        'Virtual bankroll after each settlement, in settlement order, with the opening bankroll, ' +
        'the high-water mark, and the drawdown from peak shaded between them. ' +
        curve
          .filter((point) => point.sequence !== 0 && point.result)
          .map((point) => `Settlement ${point.sequence}, ${point.selection ?? 'position'}, ${point.result}, bankroll ${formatCurrency(point.bankroll)}`)
          .join('. ')
      }
    />
  )
}
