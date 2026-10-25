/**
 * NFL Simulation Lab — Outcome Explorer, Drive Theater, Scenario Comparison.
 *
 * The Outcome Explorer runs on the imported forecast's own joint score
 * distribution. Drive Theater and Scenario Comparison render only persisted
 * producer supplements and say plainly what they are waiting for otherwise.
 * No model is executed here.
 */
import { BarChart3, ChevronRight, Clapperboard, GitCompareArrows, Orbit } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { DriveTheater } from '../components/nfl/DriveTheater'
import { OutcomeExplorer } from '../components/nfl/OutcomeExplorer'
import { PlayTheater } from '../components/nfl/PlayTheater'
import { SimulationPopulation } from '../components/nfl/SimulationPopulation'
import { ScenarioComparison } from '../components/nfl/ScenarioComparison'
import { ScenarioLaboratory } from '../components/nfl/ScenarioLaboratory'
import { ScenarioPopulationCompare } from '../components/nfl/ScenarioPopulationCompare'
import { stateLabel } from '../components/nfl/NflEventCard'
import { DataStatePanel, PageHeader } from '../components/ui'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { useNflPlayTheater } from '../hooks/useNflPlayTheater'
import { useNflPlayPopulation } from '../hooks/useNflPlayPopulation'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import type { NflExplorer, NflGameDetail, NflRepresentativeGame } from '../types/nfl'
import '../components/nfl/nfltheater.css'
import { teamIdentity } from '../services/teamIdentity'

type View = 'population' | 'watch' | 'explorer' | 'scenarios' | 'drives'

/**
 * `theater` is the old name for watching a single representative game. It stays
 * accepted so existing deep links keep working; it is not offered as a tab.
 */
const VIEWS: View[] = ['population', 'watch', 'explorer', 'scenarios', 'drives']
const LEGACY_VIEWS: Record<string, View> = { theater: 'watch' }

interface EngineChoice {
  key: string
  label: string
  explorer: NflExplorer | null
  kind: 'M1' | 'CANDIDATE' | 'DRIVE' | 'PLAY'
  representatives?: Array<{ homeScore: number; awayScore: number; label?: string; simulationIndex?: number }>
}

export function NflLabPage() {
  const { gameId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const requestedRaw = params.get('view')
  const requested = (LEGACY_VIEWS[requestedRaw ?? ''] ?? requestedRaw) as View | null
  const scenarioId = params.get('scenario') ?? 'BASE'
  const position = Number(params.get('rep') ?? 0) || 0
  const initialPlay = Math.max(0, Number(params.get('play') ?? 0) || 0)
  const theater = useNflPlayTheater(gameId, scenarioId, position)
  // The play population is the play engine's primary experience, so the lab
  // opens on it -- when the game has play-level evidence. Without it that view
  // is an empty panel, so the lab opens on the Outcome Explorer, which every
  // imported joint score distribution supports.
  const defaultView: View = theater.index || theater.indexLoading ? 'population' : 'explorer'
  const view: View = requested && VIEWS.includes(requested) ? requested : defaultView
  const populationResource = useNflPlayPopulation(gameId, scenarioId)
  const resource = useApiResource<NflGameDetail>(
    useCallback((signal: AbortSignal) => endpoints.nflGame(gameId, signal), [gameId]),
    { key: cacheKey('nfl/game', { gameId }), staleTimeMs: STALE_TIME.game },
  )
  const data = resource.data
  const choices = useMemo<EngineChoice[]>(() => {
    if (!data) return []
    return [
      ...data.engines.map((e) => ({ key: e.engine_id, label: e.model_label, explorer: e.explorer ?? null, kind: 'M1' as const, representatives: data.research_candidates.find(c => c.engine_id === e.engine_id && c.forecast_id === e.forecast_id)?.representative_finals })),
      ...data.research_candidates.filter(c => !data.engines.some(e => e.engine_id === c.engine_id && e.forecast_id === c.forecast_id)).map((c) => ({ key: c.supplement_sha256, label: `${c.model_label} · saved score population`, explorer: c.explorer, kind: 'CANDIDATE' as const, representatives: c.representative_finals })),
      ...data.drive_theater.supplements.filter((s) => s.explorer).map((s) => ({ key: s.supplement_sha256, label: `Independent drive V2 · ${s.scenario?.scenarioId === 'BASE' ? 'BASE' : 'Pace sensitivity'}`, explorer: s.explorer ?? null, kind: 'DRIVE' as const, representatives: s.paths as NflRepresentativeGame[] | undefined })),
      // Each play-level scenario is its own distribution, so the same explorer
      // tools answer the same questions of a sensitivity as of the base run.
      ...(data.play_scenarios ?? []).map((s) => ({
        key: s.supplement_sha256, label: `Play engine · ${s.label}`,
        explorer: s.explorer, kind: 'PLAY' as const, representatives: undefined,
      })),
    ]
  }, [data])
  const [engineKey, setEngineKey] = useState<string | null>(params.get('engine'))
  const choice = choices.find((c) => c.key === engineKey) ?? choices[0] ?? null

  if (!data) {
    return (
      <div className="page nfl-page">
        <DataStatePanel
          state={resource.state}
          emptyTitle="No NFL simulation evidence"
          emptyDetail="No imported forecast carries a joint score distribution for this game."
          unavailableTitle="Simulation Lab unavailable"
          unavailableDetail={resource.error ?? 'The NFL read model could not be reached.'}
        />
      </div>
    )
  }

  const { event } = data
  const home = event.home?.abbreviation ?? 'HOME'
  const away = event.away?.abbreviation ?? 'AWAY'
  const m1 = data.engines[0]
  const setParam = (name: string, value: string) => setParams((previous) => {
    const next = new URLSearchParams(previous)
    next.set('sport', 'NFL')
    next.set(name, value)
    if (name === 'scenario') next.set('rep', '0')
    return next
  }, { replace: true })
  const setView = (next: View) => setParams((previous) => { const nextParams = new URLSearchParams(previous); nextParams.set('sport', 'NFL'); nextParams.set('view', next); return nextParams }, { replace: true })
  const initialDrive = data.drive_theater.supplements.find((s) => (s.supplement_sha256 === params.get('engine') || s.engine_id === params.get('engine') || params.get('engine') === 'NFL_DEVELOPMENT_CONDITIONAL_DRIVE_V2') && (!params.get('scenario') || s.scenario?.scenarioId === params.get('scenario')))
  const market = m1?.market_comparison?.markets
  const marketSpread = market?.find((m) => m.market_type === 'SPREAD' && m.fresh)?.line
  const marketTotal = market?.find((m) => m.market_type === 'TOTAL' && m.fresh)?.line

  return (
    <div className="page nfl-page nfl-lab">
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to="/labs?sport=NFL">Interactive labs</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <Link to={`/games?sport=NFL&season=${event.season}&week=${event.week}`}>NFL · Week {event.week}</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <Link to={`/games/${event.canonical_game_id}?sport=NFL`}>{away} @ {home}</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <span aria-current="page">Simulation Lab</span>
      </nav>
      <PageHeader
        eyebrow={`NFL SIMULATION LAB · WEEK ${event.week} · ${stateLabel(event.state).toUpperCase()}`}
        title={`${teamIdentity('NFL', away).name} at ${teamIdentity('NFL', home).name}`}
        description={`Kickoff ${event.kickoff_utc ? formatTimestamp(event.kickoff_utc) : 'not scheduled'}. Explore the imported joint final-score evidence, replay persisted drive simulations, and compare precomputed scenarios. Research only — no model runs here and nothing creates a position.`}
      />

      <div className="nfl-lab__bar">
        <div className="nfl-mode-cards" role="tablist" aria-label="Lab views">
          <button type="button" role="tab" aria-selected={view === 'population'} className={view === 'population' ? 'is-active' : ''} onClick={() => setView('population')}>
            <Orbit size={26} /><span><strong>Simulation Population</strong><small>{theater.index ? 'Watch the modeled plays accumulate.' : 'Awaiting play-level evidence.'}</small></span>
          </button>
          <button type="button" role="tab" aria-selected={view === 'watch'} className={view === 'watch' ? 'is-active' : ''} onClick={() => setView('watch')}>
            <Clapperboard size={26} /><span><strong>Watch one game</strong><small>{theater.index ? 'Play by play, with the scoreboard.' : 'Awaiting play-level evidence.'}</small></span>
          </button>
          <button type="button" role="tab" aria-selected={view === 'explorer'} className={view === 'explorer' ? 'is-active' : ''} onClick={() => setView('explorer')}><BarChart3 size={26} /><span><strong>Outcome Explorer</strong><small>Every simulated final score, margin and total.</small></span></button>
          <button type="button" role="tab" aria-selected={view === 'scenarios'} className={view === 'scenarios' ? 'is-active' : ''} onClick={() => setView('scenarios')}>
            <GitCompareArrows size={26} /><span><strong>Scenario Comparison</strong><small>{theater.index ? `Compare ${theater.index.scenarios.length - 1} sensitivities against base.` : 'Awaiting a verified scenario pair.'}</small></span>
          </button>
        </div>
        {view === 'explorer' && choices.length > 0 && (
          <label className="nfl-card__engine">Engine
            <select value={choice?.key ?? ''} onChange={(e) => setEngineKey(e.target.value)}>
              {choices.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
          </label>
        )}
      </div>

      {view === 'population' && (
        theater.index
          ? (
            <SimulationPopulation
              population={populationResource.population}
              scenarios={theater.index.scenarios}
              scenarioId={scenarioId}
              onScenarioChange={(next) => setParam('scenario', next)}
              loading={populationResource.loading}
              error={populationResource.error}
            />
          )
          : (
            <section className="theater-empty">
              <h3>Simulation Population — {theater.indexLoading ? 'loading' : 'no play-level evidence'}</h3>
              <p>
                {theater.indexLoading
                  ? 'Reading the imported play-level simulations for this game.'
                  : theater.indexError
                    ?? 'No PLAY_LEVEL_SIMULATION supplement is registered for this event, so there are no modeled plays to draw.'}
              </p>
            </section>
          )
      )}
      {view === 'explorer' && (
        choice?.explorer
          ? <OutcomeExplorer key={choice.key} explorer={choice.explorer} homeTeam={home} awayTeam={away} engineLabel={choice.label} representativeFinals={choice.representatives} marketSpread={marketSpread} marketTotal={marketTotal} onReplayRepresentative={choice.kind === 'DRIVE' ? (simulationIndex) => { setParams((previous) => { const next = new URLSearchParams(previous); next.set('sport', 'NFL'); next.set('view', 'drives'); next.set('engine', choice.key); next.set('simulation', String(simulationIndex)); return next }, { replace: true }) } : undefined} />
          : (
            <section className="theater-empty">
              <h3>Outcome Explorer — no joint score evidence</h3>
              <p>This engine published win probabilities and expected scores but no joint final-score distribution, so only those summaries are shown on the game page. No joint distribution is reconstructed from marginal figures.</p>
            </section>
          )
      )}
      {view === 'watch' && (
        theater.index
          ? (
            <PlayTheater
              index={theater.index}
              game={theater.game}
              scenarioId={scenarioId}
              position={position}
              loading={theater.gameLoading}
              error={theater.gameError}
              homeTeam={home} awayTeam={away}
              onScenario={(next) => setParam('scenario', next)}
              onRepresentative={(next) => setParam('rep', String(next))}
              initialPlay={initialPlay}
            />
          )
          : (
            <section className="theater-empty">
              <h3>Watch one game — {theater.indexLoading ? 'loading' : 'no play-level evidence'}</h3>
              <p>
                {theater.indexLoading
                  ? 'Reading the imported play-level simulations for this game.'
                  : theater.indexError
                    ?? 'No PLAY_LEVEL_SIMULATION supplement is registered for this event, so there are no plays to animate. Drive-level evidence records where possessions started and ended, not what happened inside them, and this page will not invent the difference.'}
              </p>
            </section>
          )
      )}
      {view === 'scenarios' && theater.index && (
        <ScenarioPopulationCompare
          gameId={gameId}
          scenarios={theater.index.scenarios}
          scenarioId={scenarioId}
          onScenarioChange={(next) => setParam('scenario', next)}
          homeAbbr={home}
          awayAbbr={away}
        />
      )}
      {view === 'scenarios' && theater.index && (
        <ScenarioLaboratory
          index={theater.index} gameId={gameId} homeTeam={home} awayTeam={away}
          load={theater.load}
        />
      )}
      {view === 'drives' && (
        <DriveTheater
          supplements={data.drive_theater.supplements}
          requirement={data.drive_theater.requirement}
          homeTeam={home} awayTeam={away}
          m1Label={m1?.model_label ?? 'M1'}
          initialSupplement={initialDrive?.supplement_sha256}
          initialRepresentative={params.has('simulation') ? Number(params.get('simulation')) : undefined}
        />
      )}
      {view === 'scenarios' && !theater.index && (
        <ScenarioComparison
          supplements={data.scenario_comparison.supplements}
          requirement={data.scenario_comparison.requirement}
          homeTeam={home} awayTeam={away}
          m1Label={m1?.model_label ?? 'M1'}
        />
      )}

      {view !== 'drives' && data.drive_theater.available && (
        <details className="advanced-analysis">
          <summary>Advanced analysis</summary>
          <div>
            <p>
              <strong>Independent drive engine.</strong> A separate research artifact with its own
              identity and its own recorded evidence: it records where possessions started and
              ended, not what happened inside them. It is not the play engine and is not a live feed.
            </p>
            <button type="button" className="pop-btn" onClick={() => setView('drives')}>
              Open the drive engine
            </button>
          </div>
        </details>
      )}

      <p className="labs-return">
        <Link to={`/games/${event.canonical_game_id}?sport=NFL`}>Back to game analysis</Link> · <Link to={`/games?sport=NFL&season=${event.season}&week=${event.week}`}>Back to Week {event.week}</Link>
      </p>
    </div>
  )
}
