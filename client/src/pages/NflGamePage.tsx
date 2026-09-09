/**
 * NFL game analysis — what our model predicts, what the market says, how the
 * model has performed. No MLB pitcher, bullpen or Statcast scaffolding.
 */
import { ArrowRight, ChevronRight, FlaskConical } from 'lucide-react'
import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { NflMarketComparison } from '../components/nfl/NflMarketComparison'
import { pct } from '../components/nfl/OutcomeExplorer'
import { ResearchResultsPanel } from '../components/nfl/ResearchResultsPanel'
import { stateLabel } from '../components/nfl/NflEventCard'
import { DataStatePanel, MetricCard, PageHeader, Panel } from '../components/ui'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import type { NflGameDetail } from '../types/nfl'

export function NflGamePage() {
  const { gameId = '' } = useParams()
  const resource = useApiResource<NflGameDetail>(
    useCallback((signal: AbortSignal) => endpoints.nflGame(gameId, signal), [gameId]),
  )
  const data = resource.data
  const [engineIndex, setEngineIndex] = useState(0)

  if (!data) {
    return (
      <div className="page nfl-page">
        <DataStatePanel
          state={resource.state}
          emptyTitle="No NFL research forecast"
          emptyDetail="No imported forecast is registered for this game."
          unavailableTitle="Game analysis unavailable"
          unavailableDetail={resource.error ?? 'The NFL read model could not be reached.'}
        />
      </div>
    )
  }

  const { event } = data
  const home = event.home?.abbreviation ?? 'HOME'
  const away = event.away?.abbreviation ?? 'AWAY'
  const engine = data.engines[engineIndex] ?? data.engines[0]

  return (
    <div className="page nfl-page">
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to="/">Daily command center</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <Link to={`/games?sport=NFL&season=${event.season}&week=${event.week}`}>NFL · Week {event.week}</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <span aria-current="page">{away} @ {home}</span>
      </nav>
      <PageHeader
        eyebrow={`NFL · WEEK ${event.week} · ${stateLabel(event.state).toUpperCase()} · RESEARCH SHADOW`}
        title={`${event.away?.name ?? away} at ${event.home?.name ?? home}`}
        description={`Kickoff ${event.kickoff_utc ? formatTimestamp(event.kickoff_utc) : 'not scheduled'}${event.final_score ? ` · final ${home} ${event.final_score.home}–${away} ${event.final_score.away}` : ''} · research shadow — not executable, no wagering authority.`}
        actions={<Link className="primary-button" to={`/games/${event.canonical_game_id}/simulation?sport=NFL`}><FlaskConical size={14} aria-hidden="true" /> Open Simulation Lab</Link>}
      />

      <Panel title="What does our model predict?" eyebrow="FORECAST" className="section-panel"
        action={data.engines.length > 1 ? (
          <label className="nfl-card__engine">Model
            <select value={engineIndex} onChange={(e) => setEngineIndex(Number(e.target.value))}>
              {data.engines.map((item, i) => <option key={item.engine_id} value={i}>{item.model_label}</option>)}
            </select>
          </label>
        ) : <span className="admin-chip">{engine?.model_label}</span>}
      >
        {engine && (
          <>
            <div className="metric-grid metric-grid--three">
              <MetricCard label={`${home} win`} value={pct(engine.home_win_probability)} detail={`Expected ${engine.expected_home_score.toFixed(2)} points`} />
              <MetricCard label={`${away} win`} value={pct(engine.away_win_probability)} detail={`Expected ${engine.expected_away_score.toFixed(2)} points`} />
              <MetricCard label="Tie" value={pct(engine.tie_probability)} detail="Own outcome — not redistributed" />
            </div>
            <div className="metric-grid metric-grid--three">
              <MetricCard label="Expected margin" value={`${engine.expected_margin_home >= 0 ? '+' : ''}${engine.expected_margin_home.toFixed(2)}`} detail={`${home} minus ${away}`} />
              <MetricCard label="Expected total" value={engine.expected_total_score.toFixed(2)} detail="Combined points" />
              <MetricCard label="Snapshot" value={`${engine.snapshot_type.replaceAll('_', ' ')} v${engine.snapshot_version}`} detail={`${engine.history?.length ?? 1} immutable snapshot(s) kept`} />
            </div>
            <dl className="today-answer__meta">
              <div><dt>Model</dt><dd>{engine.model_label} · {engine.engine_version}</dd></div>
              <div><dt>Evidence readiness</dt><dd>{engine.evidence_readiness.label}</dd></div>
              <div><dt>Source evidence</dt><dd>{engine.timestamps.source_evidence_utc ? formatTimestamp(engine.timestamps.source_evidence_utc) : 'Not recorded'}</dd></div>
              <div><dt>Forecast published</dt><dd>{engine.timestamps.published_utc ? formatTimestamp(engine.timestamps.published_utc) : 'Not recorded'}</dd></div>
              <div><dt>Imported by AISP</dt><dd>{engine.timestamps.imported_at_utc ? formatTimestamp(engine.timestamps.imported_at_utc) : 'Not recorded'}</dd></div>
              <div><dt>Simulation</dt><dd>{engine.simulation.count ? `${engine.simulation.count.toLocaleString()} Monte Carlo draws` : 'Count not stated'} · {engine.simulation.simulator_id}</dd></div>
            </dl>
            <p className="muted"><strong>Evidence basis.</strong> {engine.evidence_readiness.explanation} {engine.evidence_basis.model_inputs}. Not consumed: {engine.evidence_basis.not_consumed.join(', ')}. {engine.evidence_basis.note}</p>
            <p className="muted"><strong>Tie.</strong> {engine.tie_note}</p>
          </>
        )}
      </Panel>

      <Panel title="What is the available market price?" eyebrow="MODEL VERSUS MARKET" className="section-panel">
        <NflMarketComparison comparison={engine?.market_comparison} homeTeam={home} awayTeam={away} />
      </Panel>

      {data.research_candidates.length > 0 && (
        <Panel title="Saved score-distribution evidence" eyebrow="VERIFIED SUPPLEMENTS" className="section-panel">
          <ul className="nfl-candidates">
            {data.research_candidates.map((candidate) => (
              <li key={candidate.supplement_sha256}>
                <strong>{candidate.model_label}</strong> · {home} {pct(candidate.explorer.outcomes.home)} · {away} {pct(candidate.explorer.outcomes.away)} · tie {pct(candidate.explorer.outcomes.tie)}
                {candidate.separately_calibrated_win_probabilities ? ` · separately calibrated ${home} ${pct(candidate.separately_calibrated_win_probabilities.home)} (shown apart from the raw distribution)` : ''}
                {' '}· imported {candidate.imported_at_utc ? formatTimestamp(candidate.imported_at_utc) : '—'}
              </li>
            ))}
          </ul>
          <p className="muted">An M1 supplement describes its saved score population, not a new model. Independent engines retain their own identity and never overwrite or promote M1.</p>
        </Panel>
      )}

      <Panel title="How has it performed?" eyebrow="RESEARCH RESULTS" className="section-panel">
        <ResearchResultsPanel results={data.research_results} />
      </Panel>

      <p className="labs-return">
        <Link className="lab-game__action" to={`/games/${event.canonical_game_id}/simulation?sport=NFL`}>Open the NFL Simulation Lab for this game <ArrowRight size={13} aria-hidden="true" /></Link>
      </p>
    </div>
  )
}
