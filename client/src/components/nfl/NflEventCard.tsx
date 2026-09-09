/**
 * One NFL event, one card. The latest valid snapshot per engine, with a model
 * selector when more than one engine has published. Home, away and tie are
 * shown as three separate outcomes.
 */
import { ArrowRight, FlaskConical } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useSportSelection } from '../../hooks/useSportSelection'
import { formatTimestamp } from '../../services/format'
import type { NflEvent } from '../../types/nfl'
import { pct } from './OutcomeExplorer'

export function stateLabel(state: NflEvent['state']): string {
  return { UPCOMING: 'Upcoming', STARTED: 'Started', FINAL: 'Final', POSTPONED: 'Postponed', CANCELED: 'Canceled', UNSCHEDULED: 'Unscheduled' }[state]
}

export function NflEventCard({ event }: { event: NflEvent }) {
  const { withSport } = useSportSelection()
  const [engineIndex, setEngineIndex] = useState(0)
  const engine = event.engines[engineIndex] ?? event.engines[0]
  const home = event.home
  const away = event.away
  const moneyline = engine?.market_comparison?.markets.find(item => item.market_type === 'MONEYLINE')
  return (
    <article className={`nfl-card nfl-card--${event.state.toLowerCase()}`} aria-label={`${away.name} at ${home.name}`}>
      <header className="nfl-card__head">
        <div>
          <h3>{away.abbreviation} @ {home.abbreviation}</h3>
          <p className="nfl-card__names">{away.name} at {home.name}</p>
        </div>
        <span className={`nfl-card__state nfl-card__state--${event.state.toLowerCase()}`}>{stateLabel(event.state)}</span>
      </header>
      <p className="nfl-card__kickoff">
        Kickoff {event.kickoff_utc ? formatTimestamp(event.kickoff_utc) : 'not scheduled'}
        {event.final_score ? ` · final ${home.abbreviation} ${event.final_score.home ?? '—'}–${away.abbreviation} ${event.final_score.away ?? '—'}` : ''}
      </p>

      {event.engines.length > 1 && (
        <label className="nfl-card__engine">
          Model
          <select value={engineIndex} onChange={(e) => setEngineIndex(Number(e.target.value))}>
            {event.engines.map((item, i) => <option key={item.engine_id} value={i}>{item.model_label}</option>)}
          </select>
        </label>
      )}

      {engine && (
        <>
          <div className="nfl-card__scoreboard"><div><b>{away.abbreviation}</b><strong>{engine.expected_away_score.toFixed(1)}</strong></div><small>EXPECTED<br />POINTS</small><div><b>{home.abbreviation}</b><strong>{engine.expected_home_score.toFixed(1)}</strong></div></div>
          <div className="nfl-card__probs">
            <div className="nfl-card__prob nfl-card__prob--away">
              <span>{away.abbreviation} win</span>
              <strong>{pct(engine.away_win_probability)}</strong>
            </div>
            <div className="nfl-card__prob nfl-card__prob--tie">
              <span>Tie</span>
              <strong>{pct(engine.tie_probability)}</strong>
            </div>
            <div className="nfl-card__prob nfl-card__prob--home">
              <span>{home.abbreviation} win</span>
              <strong>{pct(engine.home_win_probability)}</strong>
            </div>
          </div>
          <p className="nfl-card__market">Home margin {engine.expected_margin_home >= 0 ? '+' : ''}{engine.expected_margin_home.toFixed(1)} · Total {engine.expected_total_score.toFixed(1)}</p>
          <p className="nfl-card__market">{moneyline?.comparison_available && moneyline.fresh
            ? `${moneyline.book}: ${home.abbreviation} ${moneyline.quote?.a.american ?? '—'} / ${away.abbreviation} ${moneyline.quote?.b.american ?? '—'} · Home edge ${moneyline.edge?.a == null ? '—' : (100*moneyline.edge.a).toFixed(1)+' pts'}`
            : moneyline?.quote ? 'Sportsbook quote stale · no current priced edge' : 'Sportsbook price unavailable · model forecast still available'}</p>
          <p className="nfl-card__market">Source evidence {engine.timestamps.source_evidence_utc ? formatTimestamp(engine.timestamps.source_evidence_utc) : 'time not recorded'}</p>
          <details><summary>Model & evidence</summary><dl className="nfl-card__meta">
            <div><dt>Model</dt><dd>{engine.model_label} · {engine.snapshot_type.replaceAll('_', ' ').toLowerCase()} v{engine.snapshot_version}</dd></div>
            <div><dt>Margin / total</dt><dd>{engine.expected_margin_home >= 0 ? '+' : ''}{engine.expected_margin_home.toFixed(1)} {home.abbreviation} · {engine.expected_total_score.toFixed(1)} points</dd></div>
            <div><dt>Evidence</dt><dd title={engine.evidence_readiness.explanation}>{engine.evidence_readiness.label}</dd></div>
            <div><dt>Freshness</dt><dd>Source {engine.timestamps.source_evidence_utc ? formatTimestamp(engine.timestamps.source_evidence_utc) : '—'} · published {engine.timestamps.published_utc ? formatTimestamp(engine.timestamps.published_utc) : '—'}</dd></div>
          </dl></details>
        </>
      )}

      <div className="nfl-card__actions">
        {event.analysis_href && (
          <Link className="lab-game__action lab-game__action--analysis" to={withSport(`/games/${event.canonical_game_id}`, 'NFL')}>
            Game Analysis <ArrowRight size={13} aria-hidden="true" />
          </Link>
        )}
        {event.lab_href && event.lab_available && (
          <Link className="lab-game__action lab-game__action--v18" to={withSport(`/games/${event.canonical_game_id}/simulation`, 'NFL')}>
            <FlaskConical size={13} aria-hidden="true" /> Open NFL Simulation Lab
          </Link>
        )}
      </div>
    </article>
  )
}
