/**
 * Population statistics, positioned around the ballpark.
 *
 * These are the FIXED conclusions across every simulated game, so they are
 * styled deliberately unlike the changing replay state: flat translucent
 * plates on the outfield, never the bright accent treatment the live scorebug
 * uses. Every value comes from the payload; none is computed here.
 */
import type { ShadowPopulation } from '../../types/shadow'

function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? '—' : `${value.toFixed(digits)}%`
}

function ratio(value: number | null | undefined): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

function runs(value: number | null | undefined): string {
  return value == null ? '—' : value.toFixed(2)
}

export function PopulationStatOverlay({
  population, awayTeam, homeTeam, accentAway, accentHome,
}: {
  population: ShadowPopulation | null
  awayTeam: string
  homeTeam: string
  accentAway: string
  accentHome: string
}) {
  if (!population) return null
  const half = population.monte_carlo_95_half_width

  return (
    <div className="pop-overlay" aria-label="Simulated population summary">
      <div className="pop-overlay__corner pop-overlay__corner--left" style={{ ['--accent' as string]: accentAway }}>
        <span className="pop-overlay__team">{awayTeam}</span>
        <strong>{pct(population.away_win_probability)}</strong>
        <small>win · {runs(population.expected_away_runs)} expected runs</small>
      </div>

      <div className="pop-overlay__center">
        <span className="pop-overlay__label">Expected final</span>
        <strong>
          {runs(population.expected_away_runs)} – {runs(population.expected_home_runs)}
        </strong>
        <small>{runs(population.expected_total_runs)} total runs</small>
      </div>

      <div className="pop-overlay__corner pop-overlay__corner--right" style={{ ['--accent' as string]: accentHome }}>
        <span className="pop-overlay__team">{homeTeam}</span>
        <strong>{pct(population.home_win_probability)}</strong>
        <small>win · {runs(population.expected_home_runs)} expected runs</small>
      </div>

      <div className="pop-overlay__plate">
        <span><em>{ratio(population.one_run_probability)}</em> one-run games</span>
        <span><em>{ratio(population.extra_inning_probability)}</em> reach extra innings</span>
      </div>

      <div className="pop-overlay__badge">
        <strong>{population.simulation_count?.toLocaleString() ?? '—'}</strong>
        <span>completed simulations</span>
        <small>
          {half != null ? `±${(half * 100).toFixed(2)} pts 95% sampling` : 'sampling interval not recorded'}
        </small>
      </div>
    </div>
  )
}
