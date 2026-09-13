/**
 * MLB Over/Under opportunities on the decision desk.
 *
 * RESEARCH, NOT WAGERS
 * --------------------
 * Totals are research under the current funded policy, which is moneyline-only.
 * Every card says so, none carries a stake, and opening one creates nothing.
 * That is why the disclosure reads "Why this opportunity?" — a listing labelled
 * "Why this wager?" would imply a decision nobody made.
 *
 * ONE RECORD, ONE LINE
 * --------------------
 * Each card renders a single selection object straight from the totals board:
 * its probability, its price and its explanation all came from the same row, so
 * a probability for Over 8.0 can never appear beside a price for Over 8.5.
 * Books genuinely disagree about the number — FanDuel has posted 8.5 while six
 * books sat at 8.0 — and the only safe guarantee is never to recombine them.
 *
 * TWO DIFFERENT QUESTIONS
 * -----------------------
 * Sorting by model probability ranks "most likely". Sorting by expected return
 * ranks "best estimated value". Those are different orders and often different
 * selections, so the control names which one is active rather than letting the
 * reader assume they coincide.
 */
import { useMemo } from 'react'
import { AlertTriangle, ArrowRight, Layers } from 'lucide-react'
import { Link } from 'react-router-dom'
import { WagerExplanation } from '../explain/WagerExplanation'
import type { DecisionExplanation } from '../../types/explain'
import type { TotalsBoard, TotalsRow, TotalsSelection } from '../../types/totals'

export type TotalsSort = 'GAME_TIME' | 'EXPECTED_RETURN' | 'WIN_PROBABILITY'

export interface Opportunity {
  row: TotalsRow
  selection: TotalsSelection
}

function american(value: number | null | undefined): string {
  if (value == null) return '—'
  return value > 0 ? `+${value}` : `${value}`
}

function ageLabel(minutes: number | null | undefined): string {
  if (minutes == null) return 'age unknown'
  if (minutes < 60) return `${Math.round(minutes)}m old`
  return `${(minutes / 60).toFixed(1)}h old`
}

function clock(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleTimeString([], {
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  })
}

/** Priced, recorded selections only. A scenario line has no offer behind it. */
export function collectOpportunities(
  board: TotalsBoard | null, oversOnly: boolean,
): Opportunity[] {
  // Shape-checked, not just null-checked. This renders inside the decision
  // desk, and a totals payload that arrives malformed must cost the reader the
  // totals listing, never the moneyline rows beside it.
  if (!board || !Array.isArray(board.rows)) return []
  const out: Opportunity[] = []
  for (const row of board.rows) {
    for (const selection of row.selections ?? []) {
      if (!selection.price) continue
      if (selection.line_source !== 'RECORDED_SPORTSBOOK_LINE') continue
      if (oversOnly && selection.side !== 'OVER') continue
      out.push({ row, selection })
    }
  }
  return out
}

export function sortOpportunities(
  items: Opportunity[], sort: TotalsSort,
): Opportunity[] {
  const copy = [...items]
  if (sort === 'EXPECTED_RETURN') {
    copy.sort((a, b) =>
      (b.selection.price?.model_implied_net_return_per_unit ?? -Infinity)
      - (a.selection.price?.model_implied_net_return_per_unit ?? -Infinity))
  } else if (sort === 'WIN_PROBABILITY') {
    copy.sort((a, b) =>
      b.selection.win_probability_pct - a.selection.win_probability_pct)
  } else {
    copy.sort((a, b) => (a.row.starts_at ?? '').localeCompare(b.row.starts_at ?? ''))
  }
  return copy
}

function OpportunityCard({ row, selection }: Opportunity) {
  const price = selection.price
  if (!price) return null
  const ev = price.model_implied_net_return_per_unit
  return (
    <article
      className="totals-op"
      data-testid={`totals-op-${row.game_id}-${selection.side}-${selection.line}`}
    >
      <div className="totals-op__game">
        <strong>{row.matchup}</strong>
        <small>{clock(row.starts_at)}</small>
        <small>
          V18 expected total{' '}
          <b className="bx-num">
            {row.forecast.expected_total_runs?.toFixed(2) ?? '—'}
          </b>
        </small>
      </div>

      <div className="totals-op__pick">
        <b>{selection.side} {selection.line.toFixed(1)}</b>
        <small>
          {american(price.american_odds)} · {price.sportsbook.replace(/\s*\(.*\)$/, '')}
          {' · '}{ageLabel(price.quote_age_minutes)}
        </small>
        {row.market.other_lines.length > 0 && (
          <small className="bx-dim">
            other recorded lines: {row.market.other_lines.join(', ')}
          </small>
        )}
      </div>

      <dl className="totals-op__numbers">
        <div>
          <dt>Win</dt>
          <dd>{selection.win_probability_pct.toFixed(2)}%</dd>
        </div>
        <div>
          <dt>Push</dt>
          <dd>
            {selection.push_possible
              ? `${selection.push_probability_pct.toFixed(2)}%`
              : <span className="bx-dim" title="A half-run line cannot land on the number">n/a</span>}
          </dd>
        </div>
        <div>
          <dt>Model fair</dt>
          <dd>{american(selection.model_fair_american_odds)}</dd>
        </div>
        <div>
          <dt>Est. return / $1</dt>
          <dd className={ev >= 0 ? 'positive' : 'negative'}>
            {ev >= 0 ? '+' : ''}{ev.toFixed(3)}
          </dd>
        </div>
      </dl>

      <div className="totals-op__actions">
        <span className="bx-chip">Research opportunity · no stake</span>
        <Link className="bx-btn" to={`/games/${row.game_id}`}>
          Game analysis <ArrowRight size={13} />
        </Link>
        <Link className="bx-btn" to="/markets?sport=MLB&view=TOTALS">
          Totals board <ArrowRight size={13} />
        </Link>
        <Link className="bx-btn" to="/markets?sport=MLB&view=PARLAY&combo=TOTALS_ONLY">
          <Layers size={13} aria-hidden="true" /> Parlay Watch
        </Link>
      </div>

      <div className="totals-op__explain">
        <WagerExplanation
          whyLabel="Why this opportunity?"
          explanation={{
            why: selection.explanation,
            what_happened: null,
            recorded_wager: false,
          } as unknown as DecisionExplanation}
        />
      </div>
    </article>
  )
}

export function TotalsOpportunities({
  board, oversOnly, sort, onSort, unavailableReason,
}: {
  board: TotalsBoard | null
  oversOnly: boolean
  sort: TotalsSort
  onSort: (next: TotalsSort) => void
  unavailableReason?: string | null
}) {
  const items = useMemo(
    () => sortOpportunities(collectOpportunities(board, oversOnly), sort),
    [board, oversOnly, sort],
  )

  return (
    <div data-testid="totals-opportunities">
      <div className="totals-op__controls">
        <span className="bx-dim">Sort</span>
        <div className="bx-segment" role="group" aria-label="Sort totals opportunities">
          <button type="button" aria-pressed={sort === 'GAME_TIME'}
            onClick={() => onSort('GAME_TIME')}>Game time</button>
          <button type="button" aria-pressed={sort === 'EXPECTED_RETURN'}
            onClick={() => onSort('EXPECTED_RETURN')}>Best estimated value</button>
          {oversOnly && (
            <button type="button" aria-pressed={sort === 'WIN_PROBABILITY'}
              onClick={() => onSort('WIN_PROBABILITY')}>Most likely Over</button>
          )}
        </div>
        <span className="bx-dim" data-testid="totals-op-count">
          {items.length} priced {oversOnly ? 'Over' : 'totals'} selection
          {items.length === 1 ? '' : 's'}
        </span>
      </div>

      <p className="totals-op__policy" role="note">
        <AlertTriangle size={13} aria-hidden="true" /> Totals are research under the
        current funded policy, which is moneyline-only. Nothing here is recorded or
        funded, and no moneyline threshold, probability penalty or staking rule is
        applied — none is established for totals.
      </p>

      {items.length === 0 ? (
        <p className="bx-empty" data-testid="totals-op-empty">
          {unavailableReason
            ?? 'No MLB game has both a current V18 forecast and a recorded sportsbook total priced on this slate.'}
        </p>
      ) : (
        <div className="totals-op__list">
          {items.map((item) => (
            <OpportunityCard
              key={`${item.row.game_id}:${item.selection.side}:${item.selection.line}`}
              {...item}
            />
          ))}
        </div>
      )}
    </div>
  )
}
