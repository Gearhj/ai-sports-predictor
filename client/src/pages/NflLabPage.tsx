/**
 * NFL Simulation Lab — Outcome Explorer, Drive Theater, Scenario Comparison.
 *
 * The Outcome Explorer runs on the imported forecast's own joint score
 * distribution. Drive Theater and Scenario Comparison render only persisted
 * producer supplements and say plainly what they are waiting for otherwise.
 * No model is executed here.
 */
import { ChevronRight } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { DriveTheater } from '../components/nfl/DriveTheater'
import { OutcomeExplorer } from '../components/nfl/OutcomeExplorer'
import { ScenarioComparison } from '../components/nfl/ScenarioComparison'
import { stateLabel } from '../components/nfl/NflEventCard'
import { DataStatePanel, PageHeader } from '../components/ui'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import type { NflExplorer, NflGameDetail } from '../types/nfl'

type View = 'explorer' | 'drives' | 'scenarios'

interface EngineChoice {
  key: string
  label: string
  explorer: NflExplorer | null
  kind: 'M1' | 'CANDIDATE'
}

export function NflLabPage() {
  const { gameId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const view: View = params.get('view') === 'drives' ? 'drives' : params.get('view') === 'scenarios' ? 'scenarios' : 'explorer'
  const resource = useApiResource<NflGameDetail>(
    useCallback((signal: AbortSignal) => endpoints.nflGame(gameId, signal), [gameId]),
  )
  const data = resource.data
  const choices = useMemo<EngineChoice[]>(() => {
    if (!data) return []
    return [
      ...data.engines.map((e) => ({ key: e.engine_id, label: e.model_label, explorer: e.explorer ?? null, kind: 'M1' as const })),
      ...data.research_candidates.map((c) => ({ key: c.supplement_sha256, label: `${c.model_label} (research candidate)`, explorer: c.explorer, kind: 'CANDIDATE' as const })),
    ]
  }, [data])
  const [engineKey, setEngineKey] = useState<string | null>(null)
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
  const setView = (next: View) => setParams({ view: next }, { replace: true })

  return (
    <div className="page nfl-page nfl-lab">
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to="/labs?league=NFL">Interactive labs</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <Link to={`/nfl?season=${event.season}&week=${event.week}`}>NFL · Week {event.week}</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <Link to={`/nfl/games/${event.canonical_game_id}`}>{away} @ {home}</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <span aria-current="page">Simulation Lab</span>
      </nav>
      <PageHeader
        eyebrow={`NFL SIMULATION LAB · WEEK ${event.week} · ${stateLabel(event.state).toUpperCase()}`}
        title={`${event.away?.name ?? away} at ${event.home?.name ?? home}`}
        description={`Kickoff ${event.kickoff_utc ? formatTimestamp(event.kickoff_utc) : 'not scheduled'}. Explore the imported joint final-score evidence, replay persisted drive simulations, and compare precomputed scenarios. Research only — no model runs here and nothing creates a position.`}
      />

      <div className="nfl-lab__bar">
        <div className="analysis-tabs nfl-lab__tabs" role="tablist" aria-label="Lab views">
          <button type="button" role="tab" aria-selected={view === 'explorer'} className={view === 'explorer' ? 'is-active' : ''} onClick={() => setView('explorer')}>Outcome Explorer</button>
          <button type="button" role="tab" aria-selected={view === 'drives'} className={view === 'drives' ? 'is-active' : ''} onClick={() => setView('drives')}>
            Drive Theater{data.drive_theater.available ? '' : ' · awaiting evidence'}
          </button>
          <button type="button" role="tab" aria-selected={view === 'scenarios'} className={view === 'scenarios' ? 'is-active' : ''} onClick={() => setView('scenarios')}>
            Scenario Comparison{data.scenario_comparison.available ? '' : ' · awaiting evidence'}
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

      {view === 'explorer' && (
        choice?.explorer
          ? <OutcomeExplorer explorer={choice.explorer} homeTeam={home} awayTeam={away} engineLabel={choice.label} />
          : (
            <section className="theater-empty">
              <h3>Outcome Explorer — no joint score evidence</h3>
              <p>This engine published win probabilities and expected scores but no joint final-score distribution, so only those summaries are shown on the game page. No joint distribution is reconstructed from marginal figures.</p>
            </section>
          )
      )}
      {view === 'drives' && (
        <DriveTheater
          supplements={data.drive_theater.supplements}
          requirement={data.drive_theater.requirement}
          homeTeam={home} awayTeam={away}
          m1Label={m1?.model_label ?? 'M1'}
        />
      )}
      {view === 'scenarios' && (
        <ScenarioComparison
          supplements={data.scenario_comparison.supplements}
          requirement={data.scenario_comparison.requirement}
          homeTeam={home} awayTeam={away}
          m1Label={m1?.model_label ?? 'M1'}
        />
      )}

      <p className="labs-return">
        <Link to={`/nfl/games/${event.canonical_game_id}`}>Back to game analysis</Link> · <Link to={`/nfl?season=${event.season}&week=${event.week}`}>Back to Week {event.week}</Link>
      </p>
    </div>
  )
}
