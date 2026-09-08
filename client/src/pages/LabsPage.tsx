/**
 * Interactive Labs — the permanent home of AISP's simulators.
 *
 * The lab list is generated from the sport/league experience registry, so a
 * lab's name, sport and status come from the same authority the rest of the
 * product uses; a planned lab is visible but inert, with no route, no controls
 * and no outputs.
 *
 * The game picker for an available lab is driven entirely by a read model that
 * only lists games whose own persisted simulation can actually be replayed.
 * Nothing here hardcodes a game, and a V18 action appears only where that
 * specific game has a registered research-shadow path.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowRight, Beaker, CalendarDays, ChevronRight } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/ui'
import { endpoints } from '../services/api'
import { useSportRegistry } from '../hooks/useSportRegistry'
import { formatDate, formatTime } from '../services/format'
import {
  labIsAvailable,
  resolveAllSports,
  SPORT_ORDER,
  type ExperienceLeagueCode,
  type ResolvedSport,
} from '../services/sportExperience'
import type { LabsGame, LabsGames } from '../types/labs'

/** "MLB Interactive Lab — Diamond Simulator" -> "Diamond Simulator". */
function labName(sport: ResolvedSport): string {
  return sport.lab?.name.split('— ')[1] ?? sport.lab?.name ?? 'Interactive Lab'
}

export function LabsPage() {
  const [params, setParams] = useSearchParams()
  const { registry } = useSportRegistry()
  const sports = useMemo(() => resolveAllSports(registry?.sports), [registry])

  const available = useMemo(() => sports.filter(labIsAvailable), [sports])
  const requested = (params.get('league') ?? '').toUpperCase() as ExperienceLeagueCode
  const selected = available.find((sport) => sport.leagueCode === requested)
    ?? available[0]
    ?? null

  return (
    <div className="page labs-page">
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to="/">Daily command center</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <span aria-current="page">Interactive labs</span>
      </nav>

      <PageHeader
        eyebrow="INTERACTIVE LABS"
        title="Interactive labs"
        description="Explore the simulations behind a forecast. Research and simulation only — nothing here changes a forecast or creates a position."
      />

      <section className="labs-grid-section" aria-labelledby="labs-heading">
        <h2 id="labs-heading" className="sr-only">Available and planned labs</h2>
        <div className="labs-grid">
          {SPORT_ORDER.map((league) => {
            const sport = sports.find((item) => item.leagueCode === league)
            if (!sport?.lab) return null
            const ready = labIsAvailable(sport)
            return (
              <article
                key={league}
                className={`lab-card ${ready ? 'lab-card--available' : 'lab-card--planned'}`}
                style={{ ['--sport-accent' as string]: sport.accent.base }}
                aria-disabled={ready ? undefined : 'true'}
              >
                <header>
                  <span className="lab-card__league">
                    <span aria-hidden="true">{sport.icon}</span> {sport.shortName}
                  </span>
                  <span className={`lab-card__status lab-card__status--${ready ? 'available' : 'planned'}`}>
                    {ready ? 'Available' : 'Planned'}
                  </span>
                </header>
                <h3>{sport.shortName} {labName(sport)}</h3>
                <p className="lab-card__sport">{sport.sportName}</p>
                <p className="lab-card__blurb">
                  {ready
                    ? 'Open any eligible game to explore its simulated outcomes.'
                    : sport.lab.description}
                </p>
                {ready
                  ? (
                    <button
                      type="button"
                      className="lab-card__open"
                      aria-pressed={selected?.leagueCode === sport.leagueCode}
                      onClick={() => setParams({ league: sport.leagueCode }, { replace: true })}
                    >
                      Choose a game <ArrowRight size={13} aria-hidden="true" />
                    </button>
                  )
                  : (
                    <span className="lab-card__open lab-card__open--disabled" aria-disabled="true">
                      Not available yet
                    </span>
                  )}
              </article>
            )
          })}
        </div>
      </section>

      {selected
        ? <LabGamePicker sport={selected} />
        : (
          <section className="labs-empty" role="status">
            <h2>No lab is available yet</h2>
            <p>
              No league currently has a governed interactive lab. Planned labs are
              listed above and carry no simulator route.
            </p>
          </section>
        )}
    </div>
  )
}

function LabGamePicker({ sport }: { sport: ResolvedSport }) {
  const [data, setData] = useState<LabsGames | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'unavailable'>('loading')

  const load = useCallback((signal: AbortSignal) => endpoints.labsGames(sport.leagueCode, signal), [sport.leagueCode])

  useEffect(() => {
    const controller = new AbortController()
    let live = true
    setState('loading')
    load(controller.signal)
      .then((payload) => {
        if (!live) return
        const usable = payload?.schema_version === 'AISP_INTERACTIVE_LABS_V1'
        setData(usable ? payload : null)
        setState(usable ? 'ready' : 'unavailable')
      })
      .catch(() => { if (live) { setData(null); setState('unavailable') } })
    return () => { live = false; controller.abort() }
  }, [load])

  const games = data?.games ?? []
  const bySlate = useMemo(() => {
    const grouped = new Map<string, LabsGame[]>()
    for (const game of games) {
      const key = game.slate_date ?? 'Date not recorded'
      grouped.set(key, [...(grouped.get(key) ?? []), game])
    }
    return [...grouped.entries()]
  }, [games])

  return (
    <section className="lab-games" aria-labelledby="lab-games-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">{sport.shortName} · {labName(sport)}</span>
          <h2 id="lab-games-heading">Choose a game</h2>
        </div>
        {data && (
          <p className="lab-games__count">
            {data.eligible_count} {data.eligible_count === 1 ? 'game' : 'games'} with a
            replayable simulation · {data.v18_count} with a V18 path
          </p>
        )}
      </div>

      {state === 'loading' && <p className="muted" role="status">Loading eligible games…</p>}
      {state === 'unavailable' && (
        <p className="labs-empty" role="status">
          The eligible-game list could not be loaded. No illustrative game was substituted.
        </p>
      )}
      {state === 'ready' && games.length === 0 && (
        <p className="labs-empty" role="status">
          No {sport.shortName} game currently persists a simulation that can be replayed.
        </p>
      )}

      {state === 'ready' && data && games.length > 0 && (
        <>
          <p className="lab-games__note">{data.note}</p>
          {bySlate.map(([slate, entries]) => (
            <div className="lab-games__day" key={slate}>
              <h3>
                <CalendarDays size={14} aria-hidden="true" />
                {slate === 'Date not recorded' ? slate : formatDate(slate)}
                <span>{entries.length} {entries.length === 1 ? 'game' : 'games'}</span>
              </h3>
              <div className="lab-games__grid">
                {entries.map((game) => <LabGameCard key={game.game_id} game={game} />)}
              </div>
            </div>
          ))}
        </>
      )}
    </section>
  )
}

function LabGameCard({ game }: { game: LabsGame }) {
  // The event's own state, so a preserved replay of a finished game is never
  // read as a live one.
  const status = game.is_final
    ? `Final${game.away_score != null && game.home_score != null ? ` ${game.away_score}–${game.home_score}` : ''}`
    : game.is_pregame ? 'Pregame' : game.event_status.charAt(0) + game.event_status.slice(1).toLowerCase()

  return (
    <article className={`lab-game ${game.is_final ? 'lab-game--final' : ''}`}>
      <header>
        <h4>{game.label}</h4>
        <span className={`lab-game__status lab-game__status--${game.is_final ? 'final' : game.is_pregame ? 'pregame' : 'live'}`}>
          {status}
        </span>
      </header>
      <p className="lab-game__when">
        {game.starts_at ? formatTime(game.starts_at) : 'Start time not recorded'}
        {game.slate_date && <> · {formatDate(game.slate_date)}</>}
      </p>

      <div className="lab-game__actions">
        <Link className="lab-game__action lab-game__action--gen1" to={game.gen1_href}>
          Explore Gen 1 outcome distribution <ArrowRight size={13} aria-hidden="true" />
        </Link>
        {/* Only where this specific game has a registered path. */}
        {game.v18_available && game.v18_href && (
          <Link className="lab-game__action lab-game__action--v18" to={game.v18_href}>
            Replay V18 simulated game <ArrowRight size={13} aria-hidden="true" />
            <small>{game.v18_path_events} plate appearances</small>
          </Link>
        )}
        <Link className="lab-game__action lab-game__action--analysis" to={game.analysis_href}>
          Open game analysis <ArrowRight size={13} aria-hidden="true" />
        </Link>
      </div>

      {!game.v18_available && (
        <p className="lab-game__no-v18">
          <Beaker size={12} aria-hidden="true" /> No V18 research-shadow path is registered
          for this game.
        </p>
      )}
    </article>
  )
}
