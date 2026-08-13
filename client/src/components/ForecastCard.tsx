import { ChevronRight, Clock3, CloudSun, Database, MapPin, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { ForecastGame } from '../types/domain'
import { formatTime, formatTimestamp } from '../services/format'
import { formatMoneyline, formatNumber, formatPct, ScoreBar, TeamBadge } from './ui'

function OpportunityBadge({ label }: { label: ForecastGame['opportunity'] }) {
  const className = label === 'STRONG EDGE'
    ? 'opportunity opportunity--strong'
    : label === 'STANDARD EDGE'
      ? 'opportunity opportunity--standard'
      : label === 'WATCH'
        ? 'opportunity opportunity--watch'
        : 'opportunity'
  return <span className={className}>{label}</span>
}

function TeamRow({
  side,
  game,
}: {
  side: 'away' | 'home'
  game: ForecastGame
}) {
  const team = side === 'away' ? game.away_team : game.home_team
  const pitcher = side === 'away' ? game.away_pitcher : game.home_pitcher
  const projectedRuns = side === 'away' ? game.projected_away_runs : game.projected_home_runs
  const winProbability = side === 'away' ? game.away_win_probability : game.home_win_probability
  const isEdge = game.edge_side === side
  const hasForecast = game.forecast_available ?? (!game.status.toUpperCase().includes('FORECAST UNAVAILABLE') && winProbability != null)

  return (
    <div className={`forecast-team ${isEdge ? 'forecast-team--edge' : ''}`}>
      <TeamBadge abbreviation={team.abbreviation} color={team.color} />
      <div className="forecast-team__identity">
        <span className="forecast-team__city">{team.city}</span>
        <strong>{team.name}</strong>
        <small>{team.record} · {pitcher.name} {pitcher.hand && <span>{pitcher.hand}</span>}{pitcher.confirmed && <ShieldCheck size={11} aria-label="Pitcher confirmed" />}</small>
      </div>
      <div className="forecast-team__projection">
        <span>ASP runs</span><strong>{hasForecast ? formatNumber(projectedRuns) : '—'}</strong>
      </div>
      <div className="forecast-team__probability">
        <span>Win probability</span><strong>{hasForecast ? formatPct(winProbability) : '—'}</strong>
      </div>
    </div>
  )
}

export function ForecastCard({ game, demo = false }: { game: ForecastGame; demo?: boolean }) {
  const hasForecast = game.forecast_available ?? !game.status.toUpperCase().includes('FORECAST UNAVAILABLE')
  const hasMarket = game.market_available ?? game.books_reporting > 0
  const hasMarketEdge = hasForecast && hasMarket && game.edge_side !== 'none' && game.model_edge != null
  const edgeTeam = game.edge_side === 'home' ? game.home_team : game.edge_side === 'away' ? game.away_team : null
  const fair = game.edge_side === 'home' ? game.fair_home_moneyline : game.fair_away_moneyline
  const best = game.edge_side === 'home' ? game.best_home_moneyline : game.best_away_moneyline

  return (
    <article className="forecast-card">
      <header className="forecast-card__header">
        <div className="forecast-card__timing">
          <span><Clock3 size={13} /> {formatTime(game.start_time)}</span>
          <span><MapPin size={13} /> {game.venue.split(' · ')[0]}</span>
          {game.status.toLowerCase().includes('weather') && <span className="forecast-card__weather"><CloudSun size={13} /> Weather watch</span>}
        </div>
        <OpportunityBadge label={game.opportunity} />
      </header>

      <div className="forecast-card__teams">
        <TeamRow side="away" game={game} />
        <div className="forecast-card__at">AT</div>
        <TeamRow side="home" game={game} />
      </div>

      <div className="forecast-card__analysis">
        <div className="forecast-edge">
          <span className="forecast-edge__label">MODEL — MARKET</span>
          {hasMarketEdge && edgeTeam ? <><div><strong>{edgeTeam.abbreviation}</strong><b>+{game.model_edge!.toFixed(1)} pts</b></div><small>{formatMoneyline(fair)} fair · {formatMoneyline(best)} best current</small></> : <><div><strong>Unavailable</strong></div><small>{hasForecast ? 'No complete two-sided market comparison' : 'Forecast awaiting required inputs'}</small></>}
        </div>
        <div className="forecast-market">
          <div><span>Projected total</span><strong>{hasForecast ? formatNumber(game.projected_total) : '—'}</strong></div>
          <div><span>Market total</span><strong>{hasMarket ? formatNumber(game.market_total) : '—'}</strong></div>
          <div><span>Books</span><strong>{game.books_reporting}</strong></div>
        </div>
        <div className="forecast-quality">
          <ScoreBar value={game.confidence} label="Confidence" tone="blue" />
          <ScoreBar value={game.data_readiness} label="Data readiness" tone={(game.data_readiness ?? 0) < 85 ? 'amber' : 'teal'} />
        </div>
      </div>

      <footer className="forecast-card__footer">
        <span><Database size={13} /> Updated {formatTimestamp(game.last_update)}</span>
        <Link to={`${demo ? '/demo' : ''}/games/${game.id}`}>Open full analysis <ChevronRight size={15} /></Link>
      </footer>
    </article>
  )
}
