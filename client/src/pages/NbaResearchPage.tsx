/**
 * The minimal NBA research surface: imported forecasts, and nothing implied.
 *
 * Deliberately small. NBA is research-only and unfunded, so this is not the
 * beginning of an NBA product — it is the one view that makes an imported
 * forecast discoverable and readable, with every limit the package declared
 * kept visible rather than designed away.
 *
 * WHY IT GROUPS BY THE CAPTURED DATE
 * ----------------------------------
 * The heading is the date the forecast was computed against, not AISP's current
 * schedule for the game. Those can differ, and grouping under the current one
 * would quietly move a forecast to a date it never saw. `?date=` narrows on the
 * same captured value, so a link to October 20 keeps meaning October 20.
 */
import { Link, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/ui'
import { NbaResearchCard } from '../components/nba/NbaResearchCard'
import { useNbaResearch } from '../hooks/useNbaResearch'
import type { NbaResearchForecast } from '../types/nbaResearch'

function dateHeading(isoDate: string): string {
  // The stored value is a plain calendar date. Parsing it as an instant would
  // shift it a day west of UTC, so it is split rather than passed to Date().
  const [year, month, day] = isoDate.split('-').map(Number)
  return new Date(year, (month ?? 1) - 1, day ?? 1).toLocaleDateString(undefined, {
    weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
  })
}

function byCapturedDate(forecasts: NbaResearchForecast[]) {
  const groups = new Map<string, NbaResearchForecast[]>()
  for (const forecast of forecasts) {
    const key = forecast.captured_schedule.game_date
    groups.set(key, [...(groups.get(key) ?? []), forecast])
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
}

export function NbaResearchPage() {
  const [params] = useSearchParams()
  const date = params.get('date')
  const { data, loading, error } = useNbaResearch()

  const all = data?.forecasts ?? []
  const visible = date
    ? all.filter((forecast) => forecast.captured_schedule.game_date === date)
    : all
  const groups = byCapturedDate(visible)

  return (
    <div className="page">
      <PageHeader
        eyebrow="NBA · RESEARCH"
        title="NBA research forecasts"
        description={'Imported research forecasts. AISP holds no NBA odds, evaluation, '
          + 'policy, position or bankroll — nothing here can be funded.'}
      />

      {date && (
        <p className="nbares__back" data-testid="nba-date-filter">
          Showing forecasts captured for {dateHeading(date)}.{' '}
          <Link to="/nba/research">Show every date</Link>
        </p>
      )}

      {loading && !data && <p className="muted" role="status">Loading NBA research forecasts…</p>}
      {error && !data && (
        <p className="muted" role="status">NBA research forecasts are unavailable right now.</p>
      )}

      {data && visible.length === 0 && (
        <p className="muted" data-testid="nba-research-empty">
          {date
            ? 'No research forecast has been imported for that date.'
            : 'No NBA research forecast has been imported yet.'}
        </p>
      )}

      {groups.map(([day, forecasts]) => (
        <section key={day} className="nbares__day" data-testid={`nba-day-${day}`}>
          <h2 className="nbares__dayhead">{dateHeading(day)}</h2>
          <div className="nbares__list">
            {forecasts.map((forecast) => (
              <div key={forecast.forecast_id}>
                <NbaResearchCard forecast={forecast} />
                <p className="nbares__back">
                  <Link to={`/nba/games/${forecast.game_id}`}>Open this game’s forecast</Link>
                </p>
              </div>
            ))}
          </div>
        </section>
      ))}

      {data && visible.length > 0 && <p className="muted nbares__scope">{data.note}</p>}
    </div>
  )
}
