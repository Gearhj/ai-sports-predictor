/**
 * Analysis below the theater.
 *
 * The line score and the path summary describe the ONE representative game,
 * and say so. The population panel describes all simulated games. They are
 * never mixed, and the distribution chart is drawn only from the distribution
 * the bundle actually publishes — never reconstructed from a single path.
 */
import { buildLineScore, summarizePath } from '../../services/replayModel'
import type { ShadowPathEvent, ShadowPopulation } from '../../types/shadow'

export function InningLineScore({ events, awayTeam, homeTeam }: {
  events: ShadowPathEvent[]
  awayTeam: string
  homeTeam: string
}) {
  const lines = buildLineScore(events)
  const summary = summarizePath(events)
  if (!summary) return null

  return (
    <section className="analysis-section line-score" aria-labelledby="line-score-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">REPRESENTATIVE PATH</span>
          <h2 id="line-score-heading">Inning line score</h2>
        </div>
        <p className="line-score__note">
          This one simulated game, not a summary of the 5,000.
        </p>
      </div>

      <div className="line-score__scroll">
        <table className="line-score__table">
          <caption className="sr-only">
            Runs by inning for the one simulated game animated above
          </caption>
          <thead>
            <tr>
              <th scope="col">Team</th>
              {lines.map((line) => (
                <th key={line.inning} scope="col" className={line.extra ? 'is-extra' : ''}>
                  {line.inning}
                </th>
              ))}
              <th scope="col" className="is-total">R</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">{awayTeam}</th>
              {lines.map((line) => (
                <td key={line.inning} className={line.awayRuns ? 'is-scored' : ''}>
                  {line.awayRuns ?? '–'}
                </td>
              ))}
              <td className="is-total">{summary.awayFinal}</td>
            </tr>
            <tr>
              <th scope="row">{homeTeam}</th>
              {lines.map((line) => (
                <td key={line.inning} className={line.homeRuns ? 'is-scored' : ''}>
                  {line.homeRuns ?? '–'}
                </td>
              ))}
              <td className="is-total">{summary.homeFinal}</td>
            </tr>
            <tr className="line-score__markers">
              <th scope="row">Changes</th>
              {lines.map((line) => (
                <td key={line.inning}>{line.pitchingChanges > 0 ? '◆' : ''}</td>
              ))}
              <td />
            </tr>
          </tbody>
        </table>
      </div>
      <p className="line-score__legend">
        ◆ pitching change · a dash means that half-inning was not batted in this path.
      </p>

      <dl className="path-summary">
        <div><dt>Plate appearances</dt><dd>{summary.plateAppearances}</dd></div>
        <div><dt>Innings</dt><dd>{summary.innings}{summary.extraInnings ? ' (extra)' : ''}</dd></div>
        <div><dt>Pitching changes</dt><dd>{summary.pitchingChanges.length}</dd></div>
        <div><dt>Scoring plate appearances</dt><dd>{summary.scoringPlays.length}</dd></div>
        <div>
          <dt>Final</dt>
          <dd>{awayTeam} {summary.awayFinal} – {homeTeam} {summary.homeFinal}</dd>
        </div>
      </dl>

      {summary.turningPoints.length > 0 && (
        <div className="turning-points">
          {/* These are LEAD changes plus the final scoring play, which is what
              summarizePath derives -- not every score change. Under the old
              heading the list looked like it skipped runs: an 8-run inning
              appeared as a single row jumping 5-4 to 12-7, while "Scoring plate
              appearances" right above it said 16. The data was right and the
              heading was wrong. */}
          <h3>Lead changes in this path</h3>
          <p className="turning-points__scope">
            {summary.turningPoints.length} of {summary.scoringPlays.length} scoring
            plate appearances — the ones that changed who was ahead, plus the last
            one. Every run is in the inning line above.
          </p>
          <ul>
            {summary.turningPoints.map((event) => (
              <li key={event.pa}>
                <strong>PA {event.pa}</strong> ·{' '}
                {event.half === 'TOP' ? 'Top' : 'Bottom'} {event.inning} ·{' '}
                {event.runsScored} {event.runsScored === 1 ? 'run' : 'runs'} →{' '}
                {event.awayScoreAfter}–{event.homeScoreAfter}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

export function PopulationPanel({ population }: { population: ShadowPopulation | null }) {
  if (!population) return null
  const distribution = population.total_run_distribution ?? []
  const peak = distribution.reduce((max, bin) => Math.max(max, bin.probability), 0)

  return (
    <section className="analysis-section population-panel" aria-labelledby="population-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">ALL SIMULATIONS</span>
          <h2 id="population-heading">
            Simulation population · {population.simulation_count?.toLocaleString() ?? '—'} games
          </h2>
        </div>
      </div>

      <dl className="population-grid">
        <div><dt>Home win</dt><dd>{fmtPct(population.home_win_probability)}</dd></div>
        <div><dt>Away win</dt><dd>{fmtPct(population.away_win_probability)}</dd></div>
        <div><dt>Expected home runs</dt><dd>{fmtNum(population.expected_home_runs)}</dd></div>
        <div><dt>Expected away runs</dt><dd>{fmtNum(population.expected_away_runs)}</dd></div>
        <div><dt>Expected total</dt><dd>{fmtNum(population.expected_total_runs)}</dd></div>
        <div><dt>Run differential</dt><dd>{fmtNum(population.expected_run_differential)}</dd></div>
        <div><dt>One-run games</dt><dd>{fmtRatio(population.one_run_probability)}</dd></div>
        <div><dt>Extra innings</dt><dd>{fmtRatio(population.extra_inning_probability)}</dd></div>
        <div>
          <dt>Monte Carlo uncertainty</dt>
          <dd>
            {population.monte_carlo_standard_error != null
              ? `±${(population.monte_carlo_standard_error * 100).toFixed(2)} pts SE`
              : 'Not recorded'}
            {population.monte_carlo_95_half_width != null && (
              <small>±{(population.monte_carlo_95_half_width * 100).toFixed(2)} pts 95% sampling · excludes model uncertainty</small>
            )}
          </dd>
        </div>
      </dl>

      {/* Drawn only because the bundle publishes this distribution. It is not
          reconstructed from the representative path. */}
      {distribution.length > 0 && peak > 0 && (
        <figure className="total-runs">
          <figcaption>Total runs across all simulated games</figcaption>
          <div className="total-runs__bars" role="img" aria-label={
            `Distribution of total runs across ${population.simulation_count ?? 0} simulations, `
            + `from ${distribution[0].value} to ${distribution[distribution.length - 1].value} runs.`
          }>
            {distribution.map((bin) => (
              <span
                key={bin.value}
                className="total-runs__bar"
                style={{ height: `${(bin.probability / peak) * 100}%` }}
                title={`${bin.value} runs · ${(bin.probability * 100).toFixed(1)}%`}
              />
            ))}
          </div>
          <div className="total-runs__axis" aria-hidden="true">
            <span>{distribution[0].value}</span>
            <span>total runs</span>
            <span>{distribution[distribution.length - 1].value}</span>
          </div>
        </figure>
      )}
    </section>
  )
}

function fmtPct(value: number | null | undefined): string {
  return value == null ? 'Not available' : `${value.toFixed(1)}%`
}
function fmtNum(value: number | null | undefined): string {
  return value == null ? 'Not available' : value.toFixed(2)
}
function fmtRatio(value: number | null | undefined): string {
  return value == null ? 'Not available' : `${(value * 100).toFixed(1)}%`
}
