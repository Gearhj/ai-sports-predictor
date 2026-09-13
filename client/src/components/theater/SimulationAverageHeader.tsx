/**
 * The model's forecast, above the animation, in the reader's first glance.
 *
 * The Diamond Theater puts a moving scoreboard on screen. A moving scoreboard
 * is the most familiar object in sport, and readers were reading it as the
 * forecast -- or as the live game. One simulated game ending 12-7 sat above a
 * model whose actual forecast was 4.08 to 4.81, and nothing on the first screen
 * said which number was the model's answer.
 *
 * So the aggregate comes first, stays visible while the animation plays, and is
 * bound to the same forecast identity as the replay. Every value here is a mean
 * or a probability over ALL simulated games; nothing here changes during
 * playback, and that is the point.
 *
 * The two clocks stay apart: the forecast's evidence cutoff is when the model
 * last saw the world, and it is not the time this page was read.
 */
import type { ShadowForecastView } from '../../types/shadow'

/**
 * Win probabilities arrive from the population payload ALREADY in percent
 * units (60.24 means 60.24%), which is how the population panel renders them.
 * Multiplying by 100 here produced "6024.0%" on the real forecast.
 */
function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${value.toFixed(1)}%`
}

function runs(value: number | null | undefined): string {
  return value == null ? '—' : value.toFixed(2)
}

/**
 * A central range for total runs, read off the retained mass function.
 *
 * This is the distribution the forecast actually published, not a reconstruction
 * from the mean: the same mass function the CRPS fields are scored against. When
 * no distribution was retained the range is simply absent -- never invented from
 * a standard error the package did not supply.
 */
export function centralTotalRunRange(
  distribution: Array<{ value: number; probability: number }> | null | undefined,
  lower = 0.1,
  upper = 0.9,
): { low: number; high: number; coverage: number } | null {
  if (!distribution || distribution.length === 0) return null
  const sorted = [...distribution].sort((a, b) => a.value - b.value)
  const mass = sorted.reduce((total, bin) => total + bin.probability, 0)
  if (mass <= 0) return null
  let cumulative = 0
  let low: number | null = null
  let high: number | null = null
  for (const bin of sorted) {
    cumulative += bin.probability / mass
    if (low === null && cumulative >= lower) low = bin.value
    if (high === null && cumulative >= upper) { high = bin.value; break }
  }
  if (low === null || high === null) return null
  return { low, high, coverage: upper - lower }
}

export function SimulationAverageHeader({
  shadow, awayTeam, homeTeam, readAt, playing,
}: {
  shadow: ShadowForecastView
  awayTeam: string
  homeTeam: string
  /** When this page read the stored forecast. Never the forecast's own clock. */
  readAt?: string | null
  /** Playback state, so the header can say it is deliberately holding still. */
  playing?: boolean
}) {
  const population = shadow.population
  const simulations = population?.simulation_count ?? shadow.simulation_count ?? null
  const range = centralTotalRunRange(population?.total_run_distribution)
  const homeWin = population?.home_win_probability ?? shadow.home_win_probability ?? null
  const awayWin = population?.away_win_probability
    ?? (shadow.home_win_probability == null ? null : 100 - shadow.home_win_probability)
  const expectedAway = population?.expected_away_runs ?? shadow.expected_away_runs ?? null
  const expectedHome = population?.expected_home_runs ?? shadow.expected_home_runs ?? null

  return (
    <section
      className="sim-average"
      data-testid="simulation-average-header"
      aria-labelledby="simulation-average-heading"
    >
      <div className="sim-average__head">
        <span className="eyebrow">MODEL FORECAST · THE ANSWER</span>
        <h2 id="simulation-average-heading">
          Simulation average
          {simulations != null && (
            <small> · mean of {simulations.toLocaleString()} simulated games</small>
          )}
        </h2>
      </div>

      <div className="sim-average__body">
      <div className="sim-average__scores">
        <div className="sim-average__team">
          <span className="sim-average__abbr">{awayTeam}</span>
          <b className="sim-average__runs">{runs(expectedAway)}</b>
          <small>Simulation average runs</small>
        </div>
        <span className="sim-average__vs" aria-hidden="true">–</span>
        <div className="sim-average__team">
          <span className="sim-average__abbr">{homeTeam}</span>
          <b className="sim-average__runs">{runs(expectedHome)}</b>
          <small>Simulation average runs</small>
        </div>
      </div>

      <dl className="sim-average__facts">
        <div>
          <dt>{awayTeam} win</dt>
          <dd>{pct(awayWin)}</dd>
        </div>
        <div>
          <dt>{homeTeam} win</dt>
          <dd>{pct(homeWin)}</dd>
        </div>
        <div>
          <dt>Simulations</dt>
          <dd>{simulations == null ? '—' : simulations.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Total runs, middle {range ? Math.round(range.coverage * 100) : 80}%</dt>
          <dd>
            {range
              ? `${range.low}–${range.high}`
              : <span className="sim-average__absent">not published</span>}
          </dd>
        </div>
      </dl>
      </div>

      <p className="sim-average__note">
        {playing
          ? 'These are averages over every simulated game. They do not change while the animation below plays.'
          : 'These are averages over every simulated game, not the single game animated below.'}
      </p>

      <p className="sim-average__identity">
        {/* Same forecast identity as the replay, and the two clocks kept apart:
            the evidence cutoff is the model's, the read time is the page's. */}
        <code>{shadow.forecast_id}</code>
        {shadow.evidence_cutoff && (
          <> · evidence to {new Date(shadow.evidence_cutoff).toLocaleString('en-US', {
            month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
          })}</>
        )}
        {readAt && (
          <> · page read {new Date(readAt).toLocaleString('en-US', {
            month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
          })}</>
        )}
      </p>
    </section>
  )
}
