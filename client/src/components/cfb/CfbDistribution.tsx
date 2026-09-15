/**
 * The four retained distributions, drawn from the complete PMFs.
 *
 * WHAT IS NOT DONE HERE
 * ---------------------
 * No convolution. The total and margin PMFs are retained by the producer from
 * the same paired draws as the team scores; combining the two team marginals
 * here would assume independence the simulation never had, and would quietly
 * produce a narrower total than the model actually predicts.
 *
 * No trimming. The margin PMF runs from −63 to +75 and the tails are drawn.
 * Clipping to a tidy range would make the distribution look more confident than
 * it is, which is the same error as hiding the 80% interval.
 *
 * And no play sequence. These are ten thousand final scores; there is no
 * drive-by-drive path inside them to animate.
 */
import { useId } from 'react'
import type { CfbPmf, CfbResearchForecast } from '../../types/cfbResearch'

interface Series {
  key: string
  label: string
  pmf: CfbPmf
  band: { lower: number; upper: number } | null
}

function Chart({ series }: { series: Series }) {
  const gradientId = useId()
  const { pmf, band } = series
  const peak = Math.max(...pmf.counts, 1)
  const width = 320
  const height = 92
  const span = Math.max(1, pmf.values.length - 1)

  const point = (index: number, count: number) => {
    const x = (index / span) * width
    const y = height - (count / peak) * height
    return `${x.toFixed(2)},${y.toFixed(2)}`
  }
  const line = pmf.values.map((_, i) => point(i, pmf.counts[i])).join(' ')
  const area = `0,${height} ${line} ${width},${height}`

  // The 80% interval as a shaded span, on the same axis as the curve.
  const inBand = band
    ? pmf.values.map((value, index) => ({ value, index }))
        .filter(({ value }) => value >= band.lower && value <= band.upper)
    : []
  const bandStart = inBand.length ? (inBand[0].index / span) * width : 0
  const bandEnd = inBand.length ? (inBand[inBand.length - 1].index / span) * width : 0

  return (
    <figure className="cfbdist__figure" data-testid={`cfb-dist-${series.key}`}>
      <figcaption>
        <span>{series.label}</span>
        {band && (
          <small className="bx-dim">80%: {band.lower} to {band.upper}</small>
        )}
      </figcaption>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={
          `${series.label} distribution over ${pmf.draw_count.toLocaleString()} simulated games`
          + (band ? `, middle 80% from ${band.lower} to ${band.upper}` : '')
        }
      >
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--cfb-accent)" stopOpacity="0.35" />
            <stop offset="100%" stopColor="var(--cfb-accent)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {band && bandEnd > bandStart && (
          <rect
            x={bandStart} y={0} width={bandEnd - bandStart} height={height}
            fill="var(--cfb-accent)" opacity="0.10"
          />
        )}
        <polygon points={area} fill={`url(#${gradientId})`} />
        <polyline
          points={line} fill="none" stroke="var(--cfb-accent)"
          strokeWidth="1.5" vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="cfbdist__axis">
        <span className="bx-num">{pmf.values[0]}</span>
        <span className="bx-num">{pmf.values[pmf.values.length - 1]}</span>
      </div>
    </figure>
  )
}

export function CfbDistribution({ forecast }: { forecast: CfbResearchForecast }) {
  const { game, pmfs, intervals } = forecast
  const definitions: Array<[string, string]> = [
    ['away_score', `${game.away_team} points`],
    ['home_score', `${game.home_team} points`],
    ['total', 'Total points'],
    ['home_margin', `${game.home_team} margin (home minus away)`],
  ]
  const series: Series[] = definitions
    .filter(([key]) => pmfs?.[key])
    .map(([key, label]) => ({
      key, label, pmf: pmfs[key], band: intervals?.[key]?.['80'] ?? null,
    }))

  if (!series.length) return null

  return (
    <section className="cfbdist" data-testid="cfb-distributions">
      <h4>Score distributions</h4>
      <p className="cfbdist__basis">
        {forecast.explanation}
      </p>
      <div className="cfbdist__grid">
        {series.map((entry) => <Chart key={entry.key} series={entry} />)}
      </div>
    </section>
  )
}
