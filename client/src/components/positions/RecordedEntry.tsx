/**
 * The terms a position was taken at.
 *
 * One component, used by Daily Decisions and the experiment ledger, so the two
 * surfaces cannot describe the same wager differently.
 *
 * Everything shown comes from `PositionEntryTerms`, which the server reads from
 * the position's own snapshot and decision. There is deliberately no prop for a
 * market quote: the latest price for a game is a separate panel with its own
 * label, and putting it inside this component is exactly the mistake that made
 * "+114 · sgo:fanduel" read as the execution price of a +115 espnbet position.
 */
import type { PositionEntryTerms } from '../../types/slate'

function when(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  })
}

function statusLabel(entry: PositionEntryTerms): string {
  const status = entry.status.toLowerCase()
  if (status === 'open') return 'Open'
  const result = entry.result.toLowerCase()
  return `Settled · ${result.charAt(0).toUpperCase()}${result.slice(1)}`
}

export function RecordedEntry({
  entry, compact = false,
}: { entry: PositionEntryTerms; compact?: boolean }) {
  const price = entry.american_label ?? entry.decimal_odds.toFixed(3)
  return (
    <div className={`recorded-entry${compact ? ' recorded-entry--compact' : ''}`}
         data-testid={`recorded-entry-${entry.position_id}`}>
      <p className="recorded-entry__headline">
        <span className="recorded-entry__eyebrow">Recorded position</span>
        <b data-testid="recorded-entry-terms">
          {entry.side ?? 'Selection'} {price}
        </b>
        {entry.sportsbook_key && (
          <span className="recorded-entry__book" data-testid="recorded-entry-book">
            {' '}at {entry.sportsbook_key}
          </span>
        )}
      </p>
      <dl className="recorded-entry__terms">
        <div><dt>Stake</dt><dd className="bx-num" data-testid="recorded-entry-stake">${entry.stake.toFixed(2)}</dd></div>
        <div><dt>Decimal</dt><dd className="bx-num">{entry.decimal_odds.toFixed(3)}</dd></div>
        <div><dt>Entered</dt><dd>{when(entry.placed_at)}</dd></div>
        <div><dt>Status</dt><dd data-testid="recorded-entry-status">{statusLabel(entry)}</dd></div>
      </dl>
      <p className="recorded-entry__provenance bx-dim">
        {entry.provider.label && <span data-testid="recorded-entry-provider">{entry.provider.label}</span>}
        {entry.entry_quote_observed_at && (
          <span> · entry quote observed {when(entry.entry_quote_observed_at)}</span>
        )}
      </p>
      {entry.tie_settlement_label && (
        <p className="recorded-entry__tie" data-testid="recorded-entry-tie">
          {entry.tie_settlement_label}
        </p>
      )}
    </div>
  )
}
