import type { MouseEvent } from 'react'
import { AlertTriangle, ArrowUpRight, ChevronRight, Clock3, MapPin, ScrollText } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import type { GameOpportunity, ForecastGame, OpportunitySelection, RecommendationState } from '../types/domain'
import { formatTime } from '../services/format'
import { formatCurrency, formatMoneyline, formatNumber, formatPct, TeamBadge } from './ui'

const STATE_LABEL: Record<RecommendationState, string> = {
  QUALIFIED_BET: 'QUALIFIED BET',
  WATCH: 'WATCH',
  NO_BET: 'NO BET',
}

function signedPts(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(1)} pts`
}

function signedPct(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
}

function freshness(seconds: number | null | undefined): string {
  if (seconds == null) return 'not recorded'
  if (seconds < 90) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return `${hours}h ago`
}

function warnings(game: ForecastGame, selection: OpportunitySelection | null): string[] {
  const items: string[] = []
  if (game.forecast_available === false) items.push('Forecast unavailable')
  if (game.away_lineup?.status === 'MISSING' || game.home_lineup?.status === 'MISSING') items.push('Lineups unconfirmed')
  if (selection?.is_stale) items.push('Latest quote is stale')
  return items
}

export function OpportunityCard({
  game,
  opportunity,
  selection,
  rank,
  variant = 'compact',
  demo = false,
}: {
  game: ForecastGame
  opportunity: GameOpportunity | undefined
  selection?: OpportunitySelection | null
  rank?: number
  variant?: 'featured' | 'compact'
  demo?: boolean
}) {
  const navigate = useNavigate()
  const analysisHref = `${demo ? '/demo' : ''}/games/${game.id}`
  const marketsHref = `${demo ? '/demo' : ''}/markets`
  const bet = selection ?? opportunity?.best ?? null
  const state: RecommendationState = bet?.recommendation ?? opportunity?.recommendation_state ?? 'NO_BET'
  const reason = opportunity?.recommendation_reason ?? 'No governed quote is available.'
  const missing = warnings(game, bet)

  const openAnalysis = (event: MouseEvent<HTMLElement>) => {
    // The footer link is the primary, keyboard-accessible affordance; a plain
    // click anywhere else on the card is a mouse convenience to the same page.
    if (event.defaultPrevented || event.button !== 0) return
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    if ((event.target as HTMLElement).closest('a, button, select, input')) return
    navigate(analysisHref)
  }

  const showBet = bet != null && state !== 'NO_BET'
  const hasPosition = (bet?.virtual_stake ?? 0) > 0

  return (
    <article
      className={`forecast-card opportunity-card opportunity-card--${variant} opportunity-card--${state.toLowerCase()} forecast-card--interactive`}
      onClick={openAnalysis}
    >
      <header className="opportunity-card__top">
        <div className="opportunity-card__meta">
          {variant === 'featured' && rank != null && <span className="opportunity-rank">#{rank}</span>}
          <span className="opportunity-card__time"><Clock3 size={13} /> {formatTime(game.start_time)}</span>
          <span className="opportunity-card__venue"><MapPin size={13} /> {game.venue.split(' · ')[0]}</span>
        </div>
        <span className={`rec-badge rec-badge--${state.toLowerCase()}`}>{STATE_LABEL[state]}</span>
      </header>

      <div className="opportunity-card__matchup">
        <div className="opportunity-team">
          <TeamBadge abbreviation={game.away_team.abbreviation} color={game.away_team.color} size="sm" />
          <div>
            <strong>{game.away_team.name}</strong>
            <small>{game.away_team.record} · {formatPct(game.away_win_probability)} win</small>
          </div>
        </div>
        <div className="opportunity-score">
          <span>ASP score</span>
          <strong>{formatNumber(game.projected_away_runs)}<i>–</i>{formatNumber(game.projected_home_runs)}</strong>
        </div>
        <div className="opportunity-team opportunity-team--home">
          <div>
            <strong>{game.home_team.name}</strong>
            <small>{game.home_team.record} · {formatPct(game.home_win_probability)} win</small>
          </div>
          <TeamBadge abbreviation={game.home_team.abbreviation} color={game.home_team.color} size="sm" />
        </div>
      </div>

      {showBet ? (
        <div className="opportunity-bet">
          <div className="opportunity-bet__headline">
            <span className="opportunity-bet__market">{bet!.market_type} · {bet!.period}</span>
            <strong className="opportunity-bet__selection">{bet!.selection}</strong>
          </div>
          <div className="opportunity-bet__price">
            <span className="opportunity-bet__odds">{formatMoneyline(bet!.american_odds)}</span>
            <span className="opportunity-bet__book">{bet!.sportsbook ?? 'book unavailable'}</span>
          </div>
        </div>
      ) : (
        <div className="opportunity-bet opportunity-bet--none">
          <strong>No qualified bet</strong>
          <span>{reason}</span>
        </div>
      )}

      {bet != null && (
        <dl className="opportunity-analytics">
          <div><dt>ASP probability</dt><dd>{formatPct(bet.model_probability)}</dd></div>
          <div><dt>No-vig market</dt><dd>{formatPct(bet.no_vig_probability ?? bet.consensus_probability)}</dd></div>
          <div><dt>Fair odds</dt><dd>{formatMoneyline(bet.fair_odds)}</dd></div>
          <div><dt>Edge (unc-adj)</dt><dd className="opportunity-analytics__edge">{signedPts(bet.uncertainty_adjusted_edge)}</dd></div>
          <div><dt>Expected value</dt><dd>{signedPct(bet.expected_value)}</dd></div>
        </dl>
      )}

      <div className="opportunity-payout">
        {hasPosition ? (
          <>
            <div><span>Virtual stake</span><strong>{formatCurrency(bet!.virtual_stake)}</strong></div>
            <div><span>Potential profit</span><strong className="opportunity-payout__profit">{formatCurrency(bet!.potential_profit)}</strong></div>
            <div><span>Total return</span><strong>{formatCurrency(bet!.total_return)}</strong></div>
          </>
        ) : (
          <div className="opportunity-payout__none">
            <span>No virtual position</span>
            <small>{state === 'NO_BET' ? reason : 'Exposure caps or a zero Kelly stake leave no virtual position.'}</small>
          </div>
        )}
      </div>

      <div className="opportunity-card__signals">
        <span>Readiness {opportunity?.readiness != null ? `${Math.round(opportunity.readiness)}/100` : '—'}</span>
        <span>Uncertainty {opportunity?.uncertainty != null ? `${opportunity.uncertainty.toFixed(1)}%` : '—'}</span>
        <span>Disagreement {opportunity?.model_disagreement != null ? `${opportunity.model_disagreement.toFixed(1)} pts` : '—'}</span>
        <span>Quote {freshness(bet?.quote_age_seconds)}</span>
      </div>

      {missing.length > 0 && (
        <p className="opportunity-card__warning"><AlertTriangle size={13} /> {missing.join(' · ')}</p>
      )}

      <footer className="opportunity-card__footer">
        <Link to={analysisHref} className="forecast-card__cta">Open full analysis <ChevronRight size={15} /></Link>
        <Link to={marketsHref} className="opportunity-card__compare">Compare all sportsbooks <ArrowUpRight size={13} /></Link>
        <span className="opportunity-card__research"><ScrollText size={12} /> Research · simulation only</span>
      </footer>
    </article>
  )
}
