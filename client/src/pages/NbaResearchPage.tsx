/**
 * The minimal NBA research surface: imported forecasts, and nothing implied.
 *
 * Deliberately small. NBA has a virtual paper account but no price yet, so this is not the
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
import { PaperStatusStrip } from '../components/readiness/PaperStatusStrip'
import { Link, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/ui'
import { NbaResearchCard } from '../components/nba/NbaResearchCard'
import { useNbaResearch } from '../hooks/useNbaResearch'
import { useNbaJoint } from '../hooks/useNbaJoint'
import { NbaJointComparison } from '../components/nba/NbaJointComparison'
import { EvidenceBadge, NbaChannelPanel } from '../components/nba/NbaChannels'
import { useNbaGen1Board } from '../hooks/useNbaGen1'
import type { NbaResearchForecast } from '../types/nbaResearch'
import type { NbaGen1Board } from '../types/nbaGen1'

function Gen1Section({ board, date }: { board: NbaGen1Board; date: string | null }) {
  const games = date ? board.production.filter((g) => g.game_date === date) : board.production
  return (
    <section className="nbares__day" data-testid="nba-gen1-section">
      <h2 className="nbares__dayhead">Gen1 production packages</h2>
      <p className="bx-dim" data-testid="nba-gen1-handoff">
        Model handoff: {board.handoff.status.replace(/_/g, ' ').toLowerCase()}
        {board.handoff.handoff_sha256 ? ` · ${board.handoff.handoff_sha256.slice(0, 12)}…` : ''}
        {board.handoff.production_label_allowed ? '' : ' · not yet an active production label'}
      </p>
      {games.length === 0 && (
        <p className="muted" data-testid="nba-gen1-empty">
          No verified NBA production package is imported{date ? ' for that date' : ''}.
        </p>
      )}
      <div className="nbares__list">
        {games.map((g) => {
          const [away, home] = g.matchup.split(' at ')
          return (
            <article key={g.forecast_id} className="nbares" data-testid={`nba-gen1-${g.espn_game_id}`}>
              <header className="nbares__head">
                <EvidenceBadge evidenceClass={g.evidence_class} />
                <span className="nbares__label">ESPN {g.espn_game_id} · {g.revisions} revision{g.revisions === 1 ? '' : 's'}</span>
                <h3>{g.matchup}</h3>
                <small className="bx-dim">
                  Decision eligibility: {g.eligibility?.now?.state?.replace(/_/g, ' ').toLowerCase() ?? 'no discovery'}
                </small>
              </header>
              <NbaChannelPanel channels={g.channels} home={home ?? 'Home'} away={away ?? 'Away'} />
              {g.game_id && (
                <p className="nbares__back"><Link to={`/nba/games/${g.game_id}`}>Open this game</Link></p>
              )}
            </article>
          )
        })}
      </div>
      <p className="bx-dim" data-testid="nba-gen1-historical">
        {board.historical.rows > 0
          ? `${board.historical.label}: ${board.historical.rows.toLocaleString()} games held, `
            + `${board.historical.games_scored.toLocaleString()} scored per channel. `
          : 'No HISTORICAL 2025–26 rows are imported. '}
        <Link to="/performance?sport=NBA">Model performance</Link>
      </p>
    </section>
  )
}

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
  // The joint comparison sits beside the point/Elo forecast, not inside it:
  // one declares it has no distribution, the other has ten thousand draws.
  const joint = useNbaJoint()
  const gen1 = useNbaGen1Board()

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
        description={'Imported research forecasts. The 2026–27 regular season has not started, so these '
          + 'are early research examples, not a live slate. AISP holds no NBA sportsbook price, so no '
          + 'assessment or paper position can occur yet.'}
      />
      <PaperStatusStrip
        sport="NBA"
        seasonNote="The regular season has not started; nothing here is a current or final game."
      />

      {date && (
        <p className="nbares__back" data-testid="nba-date-filter">
          Showing forecasts captured for {dateHeading(date)}.{' '}
          <Link to="/nba/research">Show every date</Link>
        </p>
      )}

      {gen1.data && <Gen1Section board={gen1.data} date={date} />}

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
                <NbaJointComparison comparison={joint.data} />
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
