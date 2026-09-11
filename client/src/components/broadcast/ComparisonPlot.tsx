/**
 * One axis, several observations of the same quantity.
 *
 * THE RULE THIS COMPONENT EXISTS TO ENFORCE
 * -----------------------------------------
 * Everything drawn on one of these axes must be the same measurement. A
 * moneyline plot carries home-win probabilities and nothing else; a run-line
 * plot carries prices for one selection and nothing else. A total is never
 * drawn beside a win probability, because a number of runs and a probability
 * are not two opinions about one thing.
 *
 * The one place two genuinely different quantities appear together is the
 * band of individual sportsbook prices on a probability axis: those are
 * implied probabilities WITH the bookmaker's margin still in them, and a
 * no-vig consensus does not have it. They are drawn as a hollow band rather
 * than as points, and the caller is required to label them, so the difference
 * is visible rather than merely documented.
 */

export interface PlotMark {
  key: string
  label: string
  value: number
  color: string
  /** A hollow mark is a different quantity from the solid ones. See above. */
  hollow?: boolean
}

export interface PlotRange {
  low: number
  high: number
  label: string
}

export function ComparisonPlot({
  marks, range, domain, format, height = 64, caption,
}: {
  marks: PlotMark[]
  range?: PlotRange | null
  /** Explicit, so two rows of the same market share one scale. */
  domain: [number, number]
  format: (value: number) => string
  height?: number
  /** Only supplied inside a drawer; a row keeps its plot to one line. */
  caption?: string
}) {
  const [low, high] = domain
  const span = high - low || 1
  /* Percentage positions with pixel radii. A viewBox scaled with
   * preserveAspectRatio="none" stretches a circle into an ellipse, which
   * reads as a different kind of mark rather than the same mark elsewhere. */
  const at = (value: number) => `${(((value - low) / span) * 96 + 2).toFixed(2)}%`
  const axisY = height - 20
  const usable = marks.filter((mark) => Number.isFinite(mark.value))
  if (usable.length === 0 && !range) {
    return <p className="bx-dim">No comparable observation is published for this market.</p>
  }
  const ticks = [low, low + span / 2, high]

  return (
    <div className="bx-dumbbell" style={{ color: 'var(--bx-muted)' }}>
      <svg width="100%" height={height} role="img"
        aria-label={usable.map((mark) => `${mark.label} ${format(mark.value)}`).join(', ')}>
        {ticks.map((tick) => (
          <line key={tick} className="bx-dumbbell__grid" x1={at(tick)} x2={at(tick)} y1={4} y2={axisY} />
        ))}
        {range && Number.isFinite(range.low) && Number.isFinite(range.high) && (
          <>
            <line className="bx-dumbbell__range" x1={at(range.low)} x2={at(range.high)} y1={axisY / 2} y2={axisY / 2} />
            <line className="bx-dumbbell__range" x1={at(range.low)} x2={at(range.low)} y1={axisY / 2 - 5} y2={axisY / 2 + 5} />
            <line className="bx-dumbbell__range" x1={at(range.high)} x2={at(range.high)} y1={axisY / 2 - 5} y2={axisY / 2 + 5} />
          </>
        )}
        {usable.map((mark) => (
          <circle key={mark.key} cx={at(mark.value)} cy={axisY / 2} r={4.2}
            fill={mark.hollow ? 'none' : mark.color} stroke={mark.color} strokeWidth={mark.hollow ? 1.6 : 0}>
            <title>{`${mark.label} ${format(mark.value)}`}</title>
          </circle>
        ))}
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', font: '600 9.5px var(--bx-mono)', color: 'var(--bx-dim)' }}>
        {ticks.map((tick) => <span key={tick}>{format(tick)}</span>)}
      </div>
      {caption && <p className="bx-dim" style={{ marginTop: 4 }}>{caption}</p>}
    </div>
  )
}

export function PlotLegend({ marks, range }: { marks: PlotMark[]; range?: PlotRange | null }) {
  return (
    <div className="bx-dumbbell__legend">
      {marks.filter((mark) => Number.isFinite(mark.value)).map((mark) => (
        <span key={mark.key}>
          <i style={{
            background: mark.hollow ? 'transparent' : mark.color,
            boxShadow: mark.hollow ? `inset 0 0 0 1.6px ${mark.color}` : undefined,
          }} />
          {mark.label}
        </span>
      ))}
      {range && <span><i style={{ background: 'var(--bx-line-strong)' }} />{range.label}</span>}
    </div>
  )
}

/** Implied probability of an American price, margin included. */
export function impliedProbability(american: number | null | undefined): number | null {
  if (american == null || !Number.isFinite(american) || american === 0) return null
  return american > 0 ? 100 / (american + 100) : -american / (-american + 100)
}
