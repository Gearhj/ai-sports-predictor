/**
 * Where one College Football forecast stands, in the order the paper checkpoint
 * judges it: publication, discovery lease, the checkpoint's recorded decision,
 * price, then the final and its score.
 *
 * Every line is a server-stated fact. Nothing here infers a decision from a
 * probability, and a missing price is named with its reason, never left blank.
 */
import type { CfbPipeline } from '../../types/cfbResearch'

const ET = 'America/New_York'

function when(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', { timeZone: ET, weekday: 'short', hour: 'numeric', minute: '2-digit' }) + ' ET'
}

const LEASE: Record<string, string> = {
  CURRENT: 'Currently eligible (producer lease live)',
  EXPIRED_LEASE: 'Lease expired — awaiting the producer’s next tick',
  HISTORICAL: 'Historical — the producer no longer vouches for it',
  NOT_IN_CURRENT_DISCOVERY: 'Not in the newest discovery manifest',
  GAME_STARTED: 'Game started — the pregame forecast is history',
  NOT_IN_PRODUCTION_DISCOVERY: 'Earlier research package — never decision-eligible',
  NO_VERIFIED_DISCOVERY: 'No verified discovery yet',
  REJECTED: 'Not verified in the newest manifest',
}

const REASON: Record<string, string> = {
  NO_TWO_SIDED_QUOTE: 'No position: no governed two-sided price was held',
  AWAITING_PRODUCER_ELIGIBILITY: 'No position: the producer’s eligibility record was not yet imported',
  DISCOVERY_NOT_CURRENT: 'No position: the discovery lease was not current',
  QUOTE_TOO_STALE: 'No position: the price was older than 240 minutes',
  NO_QUALIFYING_EDGE: 'No position: no side cleared the 2-point edge',
  ACTIVATION_CONTROL_OFF: 'No position: the activation control was off',
  ALL_GATES_PASSED: 'Position recorded',
}

export function CfbPipelinePanel({ pipeline, home, away }: { pipeline: CfbPipeline; home: string; away: string }) {
  const lease = pipeline.discovery
  const decision = pipeline.decision
  const final = pipeline.final
  const evaluation = pipeline.evaluation
  return (
    <dl className="cfbpipe" data-testid="cfb-pipeline">
      <div>
        <dt>Published</dt>
        <dd>{when(pipeline.publication_clock.published_utc)} <small>{pipeline.publication_clock.label}</small></dd>
      </div>
      <div>
        <dt>Decision eligibility</dt>
        <dd data-testid="cfb-lease">{LEASE[lease.state] ?? lease.state}
          {lease.lease_valid_until && lease.state === 'CURRENT' && <small> until {when(lease.lease_valid_until)}</small>}</dd>
      </div>
      <div>
        <dt>Paper checkpoint</dt>
        <dd data-testid="cfb-decision">
          {decision
            ? <>{REASON[decision.reason ?? ''] ?? decision.recommendation}{' '}
              <small>recorded {when(decision.recorded_at)}{decision.position ? ` · $${decision.position.stake.toFixed(2)} ${decision.position.status}` : ''}</small></>
            : pipeline.decision_window
              ? <>Window {when(pipeline.decision_window.opens_utc)}–{when(pipeline.decision_window.closes_utc)} <small>AISP_CFB_PAPER_POLICY_V1</small></>
              : '—'}
        </dd>
      </div>
      <div>
        <dt>Price</dt>
        <dd data-testid="cfb-price">{pipeline.price ? <>{pipeline.price.detail} <small>{pipeline.price.reference_lines}</small></> : 'Unavailable'}</dd>
      </div>
      {final && (
        <div>
          <dt>Final</dt>
          <dd data-testid="cfb-final">
            {away} {final.away_score} – {home} {final.home_score}{final.overtime ? ' (OT)' : ''}{' '}
            <small>{final.source}; observed {when(final.observed_utc)}; completion time not supplied by the source</small>
          </dd>
        </div>
      )}
      {evaluation && (
        <div>
          <dt>Scored ({evaluation.channel})</dt>
          <dd data-testid="cfb-evaluation">
            {evaluation.winner_correct ? 'Winner correct' : 'Winner missed'} · log loss {evaluation.log_loss.toFixed(3)}
            {evaluation.two_way_brier != null && <> · Brier {evaluation.two_way_brier.toFixed(3)}</>}
            {evaluation.margin_error != null && <> · margin error {evaluation.margin_error > 0 ? '+' : ''}{evaluation.margin_error.toFixed(1)}</>}
          </dd>
        </div>
      )}
    </dl>
  )
}
