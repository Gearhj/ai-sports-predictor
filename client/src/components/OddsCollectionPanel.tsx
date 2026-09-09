/**
 * Operator view of odds credit consumption. ADMIN ONLY.
 *
 * Credit balances, burn rate and projected exhaustion are operational facts
 * about money, not product detail. The public read model already says whether
 * the market feed is blocked and why, without any number attached to cost;
 * this panel is where the numbers live, behind the admin gate.
 *
 * It answers exactly four operator questions:
 *   how much have we spent this billing month, how much is left,
 *   when does it run out at the current rate,
 *   which collection mode is running, and
 *   why does a given game have no usable quote.
 */
import { AlertTriangle, Coins, Gauge, ShieldCheck } from 'lucide-react'
import { useCallback } from 'react'
import { Panel, MetricCard, TableScroll } from './ui'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import { QuoteCoveragePanel } from './QuoteCoveragePanel'
import type { OddsCollectionStatus } from '../types/domain'

const MODE_LABEL: Record<string, string> = {
  DISABLED: 'Disabled — no provider request is made',
  EVENT_CHECKPOINT: 'Event checkpoint — one request per covered slate window',
  BUDGET_EXHAUSTED: 'Budget exhausted — collection stopped by AISP’s own cap',
  PROVIDER_FLOOR_LATCHED: 'Provider floor latched — balance is at or below the floor',
}

export function OddsCollectionPanel() {
  const loader = useCallback(
    (signal: AbortSignal) => endpoints.oddsCollectionStatus(signal),
    [],
  )
  const { data, state, error } = useApiResource<OddsCollectionStatus>(loader)

  if (!data) {
    return (
      <Panel title="Odds credit budget" eyebrow="PROVIDER OPERATIONS" className="section-panel">
        <p className="muted">
          {state === 'unavailable'
            ? error ?? 'The operator status endpoint could not be reached.'
            : 'Reading the provider request ledger…'}
        </p>
      </Panel>
    )
  }

  const budget = data.budget
  const projection = data.projection
  const measured = budget.balance_state === 'PROVIDER_BALANCE_MEASURED'

  return (
    <Panel
      title="Odds credit budget"
      eyebrow="PROVIDER OPERATIONS · ADMIN ONLY"
      className="section-panel"
      action={<span className="info-label"><Coins size={13} /> {budget.provider}</span>}
    >
      <p className={`market-comparison__status ${data.collection_mode === 'EVENT_CHECKPOINT' ? 'is-available' : 'is-unavailable'}`}>
        <Gauge size={14} aria-hidden="true" /> {MODE_LABEL[data.collection_mode] ?? data.collection_mode}. {data.collection_detail}
      </p>

      {/* Coverage before spend: the operator's question is whether tonight's
          games will be priced in time, not how many credits are left. */}
      <QuoteCoveragePanel coverage={data.quote_coverage} lanes={data.budget_lanes} />

      <div className="metric-grid metric-grid--four">
        <MetricCard
          label="Spent this month"
          value={`${budget.spent} / ${budget.monthly_cap}`}
          detail={`Billing window from ${budget.billing_window_start_utc.slice(0, 10)} (UTC month)`}
        />
        <MetricCard
          label="Budgeted remaining"
          value={`${budget.budgeted_remaining}`}
          detail={`Reserve ${budget.reserve} is never spent${budget.reserve_intact ? '' : ' — RESERVE BREACHED'}`}
        />
        <MetricCard
          label="Today"
          value={`${budget.daily_spent} / ${budget.daily_cap}`}
          detail={`${budget.daily_remaining} credits left today`}
        />
        <MetricCard
          label="Projected exhaustion"
          value={projection.exhaustion_date ?? '—'}
          detail={
            projection.survives_the_month
              ? `Survives to ${projection.window_ends} at ${projection.rate_credits_per_day}/day`
              : `Runs out before ${projection.window_ends} at ${projection.rate_credits_per_day}/day`
          }
        />
      </div>

      <dl className="today-answer__meta">
        <div>
          <dt>Provider balance</dt>
          <dd>
            {measured
              ? `${budget.provider_remaining} remaining, read ${budget.provider_reading_at_utc ? formatTimestamp(budget.provider_reading_at_utc) : 'time not recorded'}`
              : 'Unknown for this billing window'}
          </dd>
        </div>
        <div><dt>Floor</dt><dd>{budget.floor}{budget.floor_latched ? ' — latched' : ' — clear'}</dd></div>
        <div><dt>Requests this window</dt><dd>{budget.requests_this_window}</dd></div>
        <div><dt>Observed burn</dt><dd>{projection.observed_rate_credits_per_day} credits/day</dd></div>
      </dl>

      {!measured && (
        <p className="muted">
          <AlertTriangle size={13} aria-hidden="true" /> {budget.balance_note}
        </p>
      )}

      <h4>Planned collection</h4>
      <TableScroll ariaLabel="Planned odds collection checkpoints">
        <table className="data-table">
          <thead>
            <tr><th>League</th><th>Open games</th><th>Checkpoints left</th><th>Markets</th><th>Credits / request</th><th>Credits planned</th></tr>
          </thead>
          <tbody>
            {data.plans.map((plan) => (
              <tr key={plan.league_code}>
                <td><strong>{plan.league_code}</strong></td>
                <td>{plan.open_games}</td>
                <td>{plan.checkpoints.length}</td>
                <td className="mono">{plan.markets.join(', ')}</td>
                <td>{plan.credit_cost_per_request}</td>
                <td>{plan.projected_credits}</td>
              </tr>
            ))}
            {data.plans.length === 0 && <tr><td colSpan={6} className="muted">No sport has an open game.</td></tr>}
          </tbody>
        </table>
      </TableScroll>

      <h4>Why a game may have no usable quote</h4>
      <p className="muted">
        {data.provider_floor_guard.latched
          ? `Ingestion is blocked: ${data.provider_floor_guard.remaining} credits remain against a floor of ${data.provider_floor_guard.floor}. ${data.provider_floor_guard.probe_blocked_reason ?? ''}`
          : data.collection_mode === 'DISABLED'
            ? 'Checkpoint collection is switched off, so no new quote is being observed. Existing quotes age out of the 240-minute freshness window and the policy then reports no usable price.'
            : 'Collection is running. A game without a quote either had no two-sided same-book price at its checkpoint, or its cutoff passed before a checkpoint could serve it.'}
      </p>

      <h4>Providers</h4>
      <ul className="nfl-candidates">
        {data.providers.map((provider) => (
          <li key={provider.provider}>
            <strong>{provider.display_name}</strong> — {provider.governed ? 'GOVERNED' : 'research only'}
            {' · '}bookmaker namespace <span className="mono">{provider.bookmaker_namespace}</span>
            <br /><span className="muted">{provider.authority}</span>
          </li>
        ))}
      </ul>
      <p className="muted">
        <ShieldCheck size={13} aria-hidden="true" /> Only {data.governed_sources.join(', ')} may price a governed decision.
        {' '}Fallback provider {data.fallback_provider.provider}: enabled {String(data.fallback_provider.enabled)},
        {' '}mapping {data.fallback_provider.mapping_state}, ready to enable {String(data.fallback_provider.ready_to_enable)}.
        {' '}{data.fallback_provider.blocked_on}
      </p>
    </Panel>
  )
}
