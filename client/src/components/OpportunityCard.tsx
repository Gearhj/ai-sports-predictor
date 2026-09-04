import type { MouseEvent } from 'react'
import { AlertTriangle, ArrowUpRight, ChevronRight, Clock3, MapPin, ScrollText } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import type { GameOpportunity, ForecastGame, OpportunitySelection, RecommendationState } from '../types/domain'
import { formatTime, formatTimestamp } from '../services/format'
import { formatCurrency, formatMoneyline, formatNumber, formatPct, TeamLogo } from './ui'
import { PregameEvidenceBadges } from './PregameEvidenceBadges'

const LIFECYCLE_LABEL: Record<string, string> = {
  NOT_YET_EVALUATED: 'Not yet evaluated',
  PENDING_FINAL_EVIDENCE: 'Pending final evidence',
  PROVISIONALLY_ELIGIBLE: 'Provisionally eligible — awaiting final evaluation',
  FINAL_QUALIFIED: 'FINAL QUALIFIED',
  FINAL_NO_BET: 'Final no-bet',
}

const STATE_LABEL: Record<RecommendationState, string> = {
  QUALIFIED_BET: 'QUALIFIED BET',
  PROVISIONAL_EDGE: 'PROVISIONAL EDGE',
  EXTREME_DISAGREEMENT: 'REVIEW REQUIRED',
  WATCH: 'WATCH',
  NO_BET: 'NO BET',
}

function lineupLabel(status: string): string {
  if (status === 'DETAILS_MISSING') return 'DETAILS MISSING'
  return status
}

type VisibleExperimentStatus = Exclude<NonNullable<GameOpportunity['experiment_status']>, 'NOT_IN_EXPERIMENT'>

const EXPERIMENT_LABEL: Record<VisibleExperimentStatus, string> = {
  ELIGIBLE: '$1K EXPERIMENT · ELIGIBLE',
  PROVISIONAL: '$1K EXPERIMENT · PROVISIONAL',
  NO_POSITION: '$1K EXPERIMENT · NO POSITION',
  FROZEN: '$1K EXPERIMENT · FROZEN',
  SETTLED: '$1K EXPERIMENT · SETTLED',
}

function signedPts(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(1)} pts`
}

function signedPct(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
}

function pct0(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${value.toFixed(0)}%`
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
  rankBadge,
  variant = 'compact',
  demo = false,
}: {
  game: ForecastGame
  opportunity: GameOpportunity | undefined
  selection?: OpportunitySelection | null
  rank?: number
  rankBadge?: number
  variant?: 'featured' | 'compact'
  demo?: boolean
}) {
  const navigate = useNavigate()
  const analysisHref = `${demo ? '/demo' : ''}/games/${game.id}`
  const marketsHref = `${demo ? '/demo' : ''}/markets`
  const bet = selection ?? opportunity?.best ?? null
  const state: RecommendationState = bet?.recommendation ?? opportunity?.recommendation_state ?? 'NO_BET'
  const experimentStatus = opportunity?.experiment_status
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
          {rankBadge != null && <span className="opportunity-rank-marker">#{rankBadge} OPPORTUNITY</span>}
          <span className="opportunity-card__time"><Clock3 size={13} /> {formatTime(game.start_time)}</span>
          <span className="opportunity-card__venue"><MapPin size={13} /> {game.venue.split(' · ')[0]}</span>
        </div>
        <span className={`rec-badge rec-badge--${state.toLowerCase()}`}>{STATE_LABEL[state]}</span>
        {experimentStatus && experimentStatus !== 'NOT_IN_EXPERIMENT' && (
          <span className={`rec-badge rec-badge--experiment rec-badge--experiment-${experimentStatus.toLowerCase()}`}>
            {EXPERIMENT_LABEL[experimentStatus]}
            {opportunity.experiment_stake ? ` · ${formatCurrency(opportunity.experiment_stake)}` : ''}
          </span>
        )}
      </header>

      <div className="opportunity-card__matchup">
        <div className="opportunity-team">
          <TeamLogo abbreviation={game.away_team.abbreviation} color={game.away_team.color} size={variant === 'featured' ? 'md' : 'sm'} />
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
          <TeamLogo abbreviation={game.home_team.abbreviation} color={game.home_team.color} size={variant === 'featured' ? 'md' : 'sm'} />
        </div>
      </div>

      {state === 'EXTREME_DISAGREEMENT' && (
        <p className="opportunity-review-banner"><AlertTriangle size={14} /> EXTREME DISAGREEMENT — REVIEW REQUIRED</p>
      )}

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
          {(() => {
            const isMoneyline = /moneyline|h2h|money line/i.test(bet!.market_type)
            const v2 = opportunity?.v2
            return (
              <span className="experiment-eligibility-stack">
                {isMoneyline ? (
                  <span className="experiment-eligibility experiment-eligibility--eligible">V1 strict control: moneyline market</span>
                ) : (
                  <span className="experiment-eligibility experiment-eligibility--board-only">Not funded by the current moneyline experiments.</span>
                )}
                {isMoneyline && v2 && (
                  v2.recommendation === 'QUALIFIED_BET' ? (
                    <span className="experiment-eligibility experiment-eligibility--eligible">
                      V2 challenger: {LIFECYCLE_LABEL[v2.lifecycle] ?? 'Qualified'} · {v2.tier}
                      {v2.adjusted_edge != null ? ` · adj ${v2.adjusted_edge > 0 ? '+' : ''}${v2.adjusted_edge.toFixed(1)} pts` : ''}
                      {v2.lifecycle === 'FINAL_QUALIFIED' && v2.theoretical_stake != null ? ` · ${formatCurrency(v2.theoretical_stake)}` : ''}
                    </span>
                  ) : (
                    <span className="experiment-eligibility experiment-eligibility--board-only">
                      V2 challenger: {LIFECYCLE_LABEL[v2.lifecycle] ?? v2.tier}
                      {v2.reasons.length > 0 ? ` — ${v2.reasons[0]}` : ''}
                    </span>
                  )
                )}
              </span>
            )
          })()}
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
          <div><dt>Raw edge</dt><dd>{signedPts(bet.raw_edge)}</dd></div>
          <div><dt>Edge (unc-adj)</dt><dd className="opportunity-analytics__edge">{signedPts(bet.uncertainty_adjusted_edge)}</dd></div>
          {opportunity?.v2 && (opportunity.v2.starter_penalty > 0 || opportunity.v2.lineup_penalty > 0) && (
            <div><dt>V2 penalties</dt><dd>-{(opportunity.v2.starter_penalty + opportunity.v2.lineup_penalty).toFixed(1)} pts</dd></div>
          )}
          {opportunity?.v2?.adjusted_edge != null && (
            <div><dt>V2 adjusted edge</dt><dd className="opportunity-analytics__edge">{signedPts(opportunity.v2.adjusted_edge)}</dd></div>
          )}
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
            <small>{state === 'NO_BET'
              ? reason
              : state === 'EXTREME_DISAGREEMENT'
                ? 'Flagged for review — confirmed inputs, a fresh quote, and a rescore are required to qualify.'
                : state === 'PROVISIONAL_EDGE'
                  ? 'Critical inputs incomplete — provisional edge, no virtual position.'
                  : 'Exposure caps or a zero Kelly stake leave no virtual position.'}</small>
          </div>
        )}
      </div>

      {opportunity?.availability && (
        <div className="opportunity-availability">
          <span>Lineup <b>{lineupLabel(opportunity.availability.lineup_status)}</b></span>
          <span>Starter <b>{opportunity.availability.starting_pitcher_status}</b></span>
          {(opportunity.availability.unavailable_players ?? 0) > 0 && (
            <span className="opportunity-availability__flag">Unavailable <b>{opportunity.availability.unavailable_players}</b></span>
          )}
          {opportunity.availability.last_availability_update && (
            <span>Availability {formatTimestamp(opportunity.availability.last_availability_update)}</span>
          )}
        </div>
      )}

      {opportunity?.v2 && (
        <div className="opportunity-availability opportunity-availability--v2">
          <span>V2 starter evidence <b>{opportunity.v2.starter_state_home ?? '—'}{opportunity.v2.starter_state_away && opportunity.v2.starter_state_away !== opportunity.v2.starter_state_home ? ` / ${opportunity.v2.starter_state_away}` : ''}</b></span>
          {opportunity.v2.starter_stable_since_home && (
            <span>Stable since <b>{formatTimestamp(opportunity.v2.starter_stable_since_home)}</b></span>
          )}
          <span>V2 lineup <b>{opportunity.v2.lineup_state ?? '—'}</b></span>
          {opportunity.v2.pregame_evidence && <PregameEvidenceBadges evidence={opportunity.v2.pregame_evidence} compact />}
          <span>V2 state <b>{LIFECYCLE_LABEL[opportunity.v2.lifecycle] ?? opportunity.v2.lifecycle}</b></span>
          {(opportunity.v2.starter_penalty > 0 || opportunity.v2.lineup_penalty > 0) && (
            <span>Evidence penalties <b>
              {opportunity.v2.starter_penalty > 0 ? `starter -${opportunity.v2.starter_penalty.toFixed(1)}` : ''}
              {opportunity.v2.starter_penalty > 0 && opportunity.v2.lineup_penalty > 0 ? ' · ' : ''}
              {opportunity.v2.lineup_penalty > 0 ? `lineup -${opportunity.v2.lineup_penalty.toFixed(1)}` : ''}
              {' pts'}
            </b></span>
          )}
          {(opportunity.v2.feature_decomposition?.model_required_missing?.length ?? 0) > 0 && (
            <span className="opportunity-availability__flag">Model-required missing <b>{opportunity.v2.feature_decomposition!.model_required_missing.join(', ')}</b></span>
          )}
          {opportunity.v2.recommendation !== 'QUALIFIED_BET' && opportunity.v2.reasons.length > 0 && (
            <span>Current blocker <b>{opportunity.v2.reasons[0]}</b></span>
          )}
        </div>
      )}

      {opportunity?.qualification_reasons && opportunity.qualification_reasons.length > 0 && state !== 'QUALIFIED_BET' && (() => {
        const reasons = opportunity.qualification_reasons
        const critical = reasons.find((r) => /stale|lineup|forecast unavailable|review required/i.test(r)) ?? reasons[0]
        return (
          <details className="opportunity-blockers">
            <summary>
              <span className="opportunity-blockers__label">Why no position</span>
              <span className="opportunity-blockers__count">{reasons.length} unresolved requirement{reasons.length === 1 ? '' : 's'}</span>
              {critical && <span className="opportunity-blockers__critical">{critical}</span>}
            </summary>
            <ol className="opportunity-blockers__detail">
              {reasons.map((item) => <li key={item}>{item}</li>)}
            </ol>
          </details>
        )
      })()}

      {opportunity?.availability?.rescored && (() => {
        const before = opportunity.availability.forecast_before
        const after = opportunity.availability.forecast_after
        const material = before != null && after != null && Math.abs(after - before) >= 0.005
        const when = opportunity.availability.last_availability_update
        return material ? (
          <div className="opportunity-beforeafter">
            <span className="opportunity-beforeafter__tag">Forecast updated</span>
            <span>Home win {pct0(before)} → <b>{pct0(after)}</b></span>
            <span>Edge {signedPts(opportunity.availability.edge_before)} → <b>{signedPts(opportunity.availability.edge_after)}</b></span>
            <span>Book moved <b>{opportunity.availability.book_moved == null ? '—' : opportunity.availability.book_moved ? 'yes' : 'no'}</b></span>
          </div>
        ) : (
          <div className="opportunity-beforeafter opportunity-beforeafter--quiet">
            <span className="opportunity-beforeafter__tag">Forecast refreshed{when ? ` · ${formatTime(when)}` : ''}</span>
            <span>No material prediction change</span>
          </div>
        )
      })()}

      <div className="opportunity-card__signals">
        {bet != null && <span className="opportunity-card__score">Score {(bet.opportunity_score ?? opportunity?.opportunity_score ?? 0).toFixed(0)}</span>}
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
