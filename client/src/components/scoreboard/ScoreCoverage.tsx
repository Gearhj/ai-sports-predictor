/**
 * What each sport's score feed supplies, and what it does not.
 *
 * The limits belong in the product, not only in a report. "No clock shown"
 * could mean the game is at 0:00, that AISP is broken, or that the source has
 * never published one — and only the third is true for both current feeds. A
 * reader who wonders has somewhere to look, and the answer comes from the same
 * constants the collectors use, so it cannot drift away from the behaviour.
 *
 * The measured column matters more than the declared one. A cadence is a claim;
 * the observation count and the age of the last check are what actually
 * happened.
 */
import { useScoreboardCoverage } from '../../hooks/useScoreboard'

function cadence(seconds: number | null): string {
  if (seconds == null) return 'not collected'
  if (seconds < 120) return `every ${seconds} seconds`
  if (seconds < 3600) return `every ${Math.round(seconds / 60)} minutes`
  return `every ${(seconds / 3600).toFixed(0)} hour${seconds >= 7200 ? 's' : ''}`
}

function Yes({ value }: { value: boolean }) {
  return value
    ? <span className="cov__yes">available</span>
    : <span className="cov__no">not available</span>
}

export function ScoreCoverage() {
  const { data, loading } = useScoreboardCoverage()
  if (loading && !data) {
    return <p className="bx-dim" role="status">Loading score coverage…</p>
  }
  if (!data) return null

  return (
    <div className="cov" data-testid="score-coverage">
      <p className="cov__note">{data.note}</p>
      {Object.entries(data.sports).map(([league, sport]) => (
        <section key={league} className="cov__sport" data-testid={`coverage-${league}`}>
          <header className="cov__head">
            <h3>{league}</h3>
            <span className="bx-dim">{sport.source ?? 'no source collected'}</span>
          </header>

          <dl className="cov__grid">
            <div>
              <dt>Live score</dt>
              <dd><Yes value={sport.live_score} /></dd>
            </div>
            <div>
              <dt>Period breakdown</dt>
              <dd>
                <Yes value={sport.period_breakdown} />
                {sport.period_breakdown && (
                  <small className="bx-dim"> · {sport.period_label}</small>
                )}
              </dd>
            </div>
            <div>
              <dt>Game clock</dt>
              <dd><Yes value={sport.clock} /></dd>
            </div>
            <div>
              <dt>Score timestamp</dt>
              <dd><Yes value={sport.source_score_timestamp} /></dd>
            </div>
            <div>
              <dt>Refresh</dt>
              <dd>{cadence(sport.target_cadence_seconds)}</dd>
            </div>
            <div>
              <dt>Observed in the last 24h</dt>
              <dd className="bx-num">{sport.measured.observations_last_24h}</dd>
            </div>
          </dl>

          <p className="cov__cadence">{sport.cadence_note}</p>
          {sport.cost && <p className="cov__cost">{sport.cost}</p>}

          {Object.keys(sport.unavailable_fields).length > 0 && (
            <details className="cov__gaps">
              <summary>
                What is not available ({Object.keys(sport.unavailable_fields).length})
              </summary>
              <dl>
                {Object.entries(sport.unavailable_fields).map(([field, reason]) => (
                  <div key={field}>
                    <dt>{field}</dt>
                    <dd>{reason}</dd>
                  </div>
                ))}
              </dl>
            </details>
          )}
        </section>
      ))}
    </div>
  )
}
