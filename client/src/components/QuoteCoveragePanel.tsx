/**
 * Will tonight's games have a price when the decision is made?
 *
 * A credit balance and a burn rate answer a different question. They say how
 * much is left; they do not say whether the games that matter will be priced
 * in time. This panel answers the operational question directly: how many of
 * today's games are covered, how many are still at risk, when the next
 * checkpoint fires -- and for every game that is not covered, the single exact
 * reason, never the word "unavailable".
 */
import { AlertTriangle, CheckCircle2, Clock, ShieldCheck } from 'lucide-react'
import type { BudgetLanes, QuoteCoverageReport } from '../types/domain'

function time(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—'
    : parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function QuoteCoveragePanel({ coverage, lanes }: {
  coverage?: QuoteCoverageReport
  lanes?: BudgetLanes
}) {
  if (!coverage) return null
  const t = coverage.totals
  const uncovered = coverage.events.filter((event) => !event.covered)

  return (
    <section className="panel quote-coverage" aria-labelledby="coverage-heading">
      <header>
        <h3 id="coverage-heading">Quote coverage · {coverage.league}</h3>
        <p>
          A game is covered when one sportsbook has priced <strong>both</strong> sides of its
          moneyline, at the same moment, within {coverage.contract.freshness_window_minutes} minutes
          of its decision cutoff — {coverage.contract.cutoff_minutes_before_start} minutes before
          first pitch. Anything else is not a quote.
        </p>
      </header>

      <div className="coverage-totals">
        <div className={t.coverage_rate >= 1 ? 'is-good' : t.covered > 0 ? 'is-partial' : 'is-bad'}>
          <CheckCircle2 size={17} aria-hidden="true" />
          <strong data-testid="coverage-covered">{t.covered} / {t.events}</strong>
          <span>covered</span>
        </div>
        <div className={t.at_risk ? 'is-warn' : ''}>
          <Clock size={17} aria-hidden="true" />
          <strong>{t.at_risk}</strong><span>still at risk</span>
        </div>
        <div className={t.missed ? 'is-bad' : ''}>
          <AlertTriangle size={17} aria-hidden="true" />
          <strong>{t.missed}</strong><span>cutoff missed</span>
        </div>
        <div>
          <ShieldCheck size={17} aria-hidden="true" />
          <strong>{t.within_freshness_target}</strong>
          <span>inside the {coverage.contract.freshness_target_minutes}-minute target</span>
        </div>
      </div>

      {lanes && (
        <table className="lane-table">
          <caption>
            Credit lanes. A lane may spend only what is left after every protected lane's
            unspent allocation, which is what makes the reserve real rather than a number in a
            report. {lanes.protected_unspent} credits are currently protected.
          </caption>
          <thead>
            <tr><th scope="col">Lane</th><th scope="col">Spent</th><th scope="col">Allocated</th>
              <th scope="col">Left</th><th scope="col">Today</th></tr>
          </thead>
          <tbody>
            {lanes.lanes.map((lane) => (
              <tr key={lane.lane} className={lane.protected ? 'is-protected' : ''}>
                <th scope="row">
                  {lane.label}
                  {lane.protected && <span className="lane-badge">protected</span>}
                  <small>{lane.purpose}</small>
                </th>
                <td>{lane.spent}</td>
                <td>{lane.monthly_allocation}</td>
                <td>{lane.remaining}</td>
                <td>{lane.spent_today} / {lane.daily_cap}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {lanes && lanes.reconciliation.state !== 'RECONCILED' && (
        <p className="coverage-reconciliation" role="status">
          <strong>{lanes.reconciliation.state}</strong> — {lanes.reconciliation.detail}
        </p>
      )}

      {uncovered.length > 0 && (
        <>
          <h4>Games without a usable quote, and exactly why</h4>
          <table className="coverage-table">
            <thead>
              <tr><th scope="col">Game</th><th scope="col">Cutoff</th><th scope="col">State</th>
                <th scope="col">Reason</th></tr>
            </thead>
            <tbody>
              {uncovered.map((event) => (
                <tr key={event.game_id} className={event.at_risk ? 'is-at-risk' : ''}>
                  <th scope="row">{event.matchup}</th>
                  <td>{time(event.cutoff_utc)}</td>
                  <td><code>{event.state}</code></td>
                  <td>
                    <strong>{event.cause ?? 'UNCLASSIFIED'}</strong>
                    {event.cause_detail && <small>{event.cause_detail}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {uncovered.length === 0 && (
        <p className="coverage-clear">Every game on this slate has a complete, fresh quote.</p>
      )}
    </section>
  )
}
