/**
 * Model versus market for one NFL engine.
 *
 * Three distinct numbers are kept apart: the market's implied probability
 * (with and without the vig), the model's independent probability read off
 * its own joint distribution, and — for a two-way moneyline — the conditional
 * non-tie probability that the book's tie-void rule makes comparable. A stale
 * quote is shown as a stale quote; missing data is never turned into a zero
 * edge, and an edge shown here is research, not wagering authority.
 */
import { formatTimestamp } from '../../services/format'
import type { NflMarketBlock, NflMarketComparison as Comparison, NflQuote } from '../../types/nfl'
import { pct } from './OutcomeExplorer'

function american(value: number | null | undefined): string {
  return value == null ? '—' : value > 0 ? `+${value}` : `${value}`
}

function quoteLabel(q: NflQuote, homeTeam: string, awayTeam: string): string {
  const side = q.side === 'HOME' ? homeTeam : q.side === 'AWAY' ? awayTeam : q.side === 'OVER' ? 'Over' : q.side === 'UNDER' ? 'Under' : q.side
  if (q.point == null || q.market_type === 'MONEYLINE') return side
  // A spread carries a sign; a total is a plain number.
  return q.market_type === 'SPREAD' ? `${side} ${q.point > 0 ? `+${q.point}` : q.point}` : `${side} ${q.point}`
}

function Block({ block, homeTeam, awayTeam }: { block: NflMarketBlock; homeTeam: string; awayTeam: string }) {
  const title = { MONEYLINE: 'Moneyline', SPREAD: 'Point spread', TOTAL: 'Total points' }[block.market_type]
  if (!block.quote) {
    return (
      <article className="market-block market-block--unavailable">
        <h4>{title}</h4>
        <p className="muted">{block.reason ?? 'Comparison unavailable'}</p>
      </article>
    )
  }
  const { a, b } = block.quote
  return (
    <article className={`market-block ${block.comparison_available ? '' : 'market-block--reference'}`}>
      <header>
        <h4>{title}</h4>
        <span className="muted">
          {block.book} · observed {block.observed_at ? formatTimestamp(block.observed_at) : 'time not recorded'} · {block.fresh ? 'fresh' : 'stale — reference only'}
          {block.books_with_two_sided_quote ? ` · ${block.books_with_two_sided_quote} book(s) two-sided` : ''}
        </span>
      </header>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr><th>Side</th><th>Price</th><th>Market implied</th><th>Market no-vig</th><th>Model (no push)</th><th>Edge</th><th>Unit EV</th></tr>
          </thead>
          <tbody>
            {[a, b].map((q, i) => {
              const key = i === 0 ? 'a' : 'b'
              return (
                <tr key={q.snapshot_id}>
                  <td>{quoteLabel(q, homeTeam, awayTeam)}</td>
                  <td>{american(q.american)}</td>
                  <td>{block.market_implied ? pct(block.market_implied[key]) : '—'}</td>
                  <td>{block.market_no_vig ? pct(block.market_no_vig[key]) : '—'}</td>
                  <td>{block.model_comparable ? pct(block.model_comparable[key]) : block.model ? pct(block.model[key]) : '—'}</td>
                  <td>{block.edge && block.edge[key] != null ? `${(block.edge[key]! * 100) >= 0 ? '+' : ''}${(block.edge[key]! * 100).toFixed(1)} pts` : '—'}</td>
                  <td>{block.expected_value && block.expected_value[key] != null ? block.expected_value[key]!.toFixed(3) : '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {block.model && block.model.push > 0 && <p className="muted">Push {pct(block.model.push)} — refunded, counted for neither side. No-vig comparison conditions on no push; EV retains the raw win/loss/push mass.</p>}
      {block.three_way && (
        <p className="muted">
          Three-way forecast unchanged: {homeTeam} {pct(block.three_way.home)} · {awayTeam} {pct(block.three_way.away)} · tie {pct(block.three_way.tie)}. {block.settlement_note}
        </p>
      )}
      {!block.three_way && block.settlement_note && <p className="muted">{block.settlement_note}</p>}
      {!block.comparison_available && block.reason && <p className="muted">{block.reason}</p>}
    </article>
  )
}

export function NflMarketComparison({ comparison, homeTeam, awayTeam }: {
  comparison: Comparison | undefined
  homeTeam: string
  awayTeam: string
}) {
  if (!comparison) return null
  return (
    <section className="market-comparison" aria-label="Model versus market">
      <p className={`market-comparison__status ${comparison.available ? 'is-available' : 'is-unavailable'}`}>
        {comparison.available
          ? `Comparison available on a fresh two-sided quote (freshness window ${comparison.freshness_window_minutes} minutes).`
          : `Comparison unavailable — ${comparison.reason ?? 'no valid quote'}.`}
        {comparison.quotes_observed ? ` ${comparison.quotes_observed} quote(s) observed; last ${comparison.last_observed_utc ? formatTimestamp(comparison.last_observed_utc) : '—'}.` : ''}
      </p>
      {comparison.markets.map((block) => <Block key={block.market_type} block={block} homeTeam={homeTeam} awayTeam={awayTeam} />)}
      <p className="muted">Research shadow: a displayed edge grants no wagering authority and creates no decision or position.</p>
    </section>
  )
}
