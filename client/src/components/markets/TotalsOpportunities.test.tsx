/**
 * Totals opportunities on the decision desk — the claims they may not make.
 *
 * Three failures would each be worse than showing nothing:
 *
 *  1. A probability for one line shown beside a price for another. Books
 *     genuinely post different numbers on the same game, so the test builds a
 *     board where they do and checks that nothing recombines them.
 *  2. A research listing that reads like a position. There is no funded totals
 *     policy; a card that implied a stake would be describing money nobody
 *     committed, so the wording is asserted, not assumed.
 *  3. "Most likely" silently standing in for "best value". They are different
 *     orders, and the test pins that they actually produce different ones.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  TotalsOpportunities, collectOpportunities, sortOpportunities,
} from './TotalsOpportunities'
import type {
  TotalsBoard, TotalsPrice, TotalsRow, TotalsSelection,
} from '../../types/totals'

function explanation(text: string, modelPct = 49.42) {
  return {
    schema_version: 'AISP_TOTALS_OPPORTUNITY_EXPLANATION_V1',
    recorded_wager: false,
    status_line:
      'Research opportunity. No virtual wager, decision or stake exists for this selection.',
    market: 'TOTAL',
    selection: text,
    bullets: [{ key: 'selection', text: `${text} is a research opportunity, not a recorded wager.` }],
    price: {
      american_odds: 105, decimal_odds: 2.05, model_probability_pct: modelPct,
      raw_model_probability_pct: null, break_even_probability_pct: 48.78,
      no_vig_probability_pct: null, model_edge_vs_break_even_pct: 0.64,
      expected_value_per_unit: 0.0131, priced_as_underdog: true, clears_break_even: true,
    },
    value_framing: {
      kind: 'LESS_LIKELY_BUT_PRICED_WELL',
      headline: 'The model does NOT make this the more likely side.',
      model_makes_it_favoured: false, priced_as_underdog: true, clears_break_even: true,
      note: 'The most likely outcome and the best estimated value can be different selections.',
    },
    details: {
      gates_passed: [], gates_passed_count: 0, forecast_identity: 'fc-1',
      bound_at_decision_time: 'Every figure here is bound to this exact line.',
    },
  } as unknown as TotalsSelection['explanation']
}

function price(over: Partial<TotalsPrice> = {}): TotalsPrice {
  return {
    sportsbook: 'Betmgm', decimal_odds: 2.05, american_odds: 105,
    observed_at: '2026-09-13T17:39:00Z', quote_age_minutes: 101, stale: false,
    odds_snapshot_id: 'snap-1', market_outcome_id: 'mo-1',
    model_implied_net_return_per_unit: 0.0131, estimate_basis: 'V18_TOTAL_RUN_DISTRIBUTION',
    ...over,
  }
}

function selection(over: Partial<TotalsSelection> = {}): TotalsSelection {
  return {
    side: 'OVER', line: 7.5, line_source: 'RECORDED_SPORTSBOOK_LINE',
    win_probability_pct: 49.42, push_probability_pct: 0, loss_probability_pct: 50.58,
    probability_units: 'PERCENT', push_possible: false,
    model_fair_decimal_odds: 2.023, model_fair_american_odds: 102,
    price: price(), price_unavailable_reason: null,
    explanation: explanation('Over 7.5'),
    ...over,
  }
}

function row(over: Partial<TotalsRow> = {}): TotalsRow {
  return {
    game_id: 'g1', matchup: 'SD at SF',
    away_team: { id: 'a', abbreviation: 'SD', name: 'San Diego Padres' },
    home_team: { id: 'h', abbreviation: 'SF', name: 'San Francisco Giants' },
    starts_at: '2026-09-13T20:15:00Z', status: 'SCHEDULED',
    forecast: {
      engine_id: 'MLB_V18_WHOLE_GAME', engine_version: 'mlb-v18-whole-game-a6e7e99',
      model_label: 'V18 whole-game', forecast_id: 'fc-1', availability: 'AVAILABLE',
      availability_detail: '', decision_eligible: true, distribution_available: true,
      expected_total_runs: 8.0296, simulation_count: 5000, support: [1, 30],
      unavailable_reason: null,
      totals_verification: {
        status: 'VERIFIED_AGAINST_PUBLISHED_INDEX',
        detail: 'verified', index_sha256: '73d9', attached_at_utc: '2026-09-13T19:30:24Z',
        source_field: 'distributions.totalRunDistribution',
      },
    },
    market: {
      line: 7.5, line_source: 'RECORDED_SPORTSBOOK_LINE', line_note: null, books: {},
      observed_at: '2026-09-13T17:39:00Z', quote_age_minutes: 101, other_lines: [],
      unavailable_reason: null,
    },
    selections: [selection()],
    ...over,
  }
}

function board(over: Partial<TotalsBoard> = {}): TotalsBoard {
  return {
    schema_version: 'AISP_MLB_TOTALS_BOARD_V1', event_date: '2026-09-13',
    generated_at: '2026-09-13T19:00:00Z', probability_units: 'PERCENT',
    overs_only: false, filter_note: null, rows: [row()],
    coverage: {
      games: 15, games_with_v18_distribution: 15, games_index_verified: 11,
      games_with_recorded_line: 1, games_priceable: 1, selections_with_usable_price: 2,
    },
    market_availability: {
      latest_total_quote_at: '2026-09-13T17:39:00Z',
      latest_total_quote_source: 'SPORTSGAMEODDS', age_hours: 1.7,
      detail: 'Totals were last captured at 17:39Z from SportsGameOdds.',
    },
    ...over,
  }
}

function paint(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

afterEach(cleanup)

describe('collectOpportunities', () => {
  it('keeps only priced, recorded lines', () => {
    const mixed = board({
      rows: [row({
        selections: [
          selection(),
          selection({ side: 'UNDER', price: null, price_unavailable_reason: 'NO_QUOTE' }),
          // A scenario line is a question the reader typed, not an offer.
          selection({ line: 9.5, line_source: 'USER_SELECTED_SCENARIO' }),
        ],
      })],
    })
    const items = collectOpportunities(mixed, false)
    expect(items).toHaveLength(1)
    expect(items[0].selection.line_source).toBe('RECORDED_SPORTSBOOK_LINE')
    expect(items[0].selection.price).not.toBeNull()
  })

  it('drops Unders when Overs only is on, and nothing else', () => {
    const both = board({
      rows: [row({
        selections: [selection(), selection({ side: 'UNDER', win_probability_pct: 50.58 })],
      })],
    })
    expect(collectOpportunities(both, false)).toHaveLength(2)
    const overs = collectOpportunities(both, true)
    expect(overs).toHaveLength(1)
    expect(overs[0].selection.side).toBe('OVER')
  })

  it('returns nothing rather than throwing when no board has loaded', () => {
    expect(collectOpportunities(null, false)).toEqual([])
  })
})

describe('sortOpportunities', () => {
  // The two orders are not the same question, and the fixture makes them
  // disagree so a test could not pass by accident if one were used for both.
  const likelyButShortPriced = { row: row({ game_id: 'g2', starts_at: '2026-09-13T23:00:00Z' }),
    selection: selection({
      win_probability_pct: 61, price: price({ model_implied_net_return_per_unit: -0.02 }),
    }) }
  const unlikelyButGoodValue = { row: row({ game_id: 'g1', starts_at: '2026-09-14T02:00:00Z' }),
    selection: selection({
      win_probability_pct: 44, price: price({ model_implied_net_return_per_unit: 0.08 }),
    }) }
  const items = [likelyButShortPriced, unlikelyButGoodValue]

  it('ranks by estimated value, which is not the likeliest side', () => {
    const sorted = sortOpportunities(items, 'EXPECTED_RETURN')
    expect(sorted[0].selection.win_probability_pct).toBe(44)
  })

  it('ranks by model probability, which is not the best value', () => {
    const sorted = sortOpportunities(items, 'WIN_PROBABILITY')
    expect(sorted[0].selection.win_probability_pct).toBe(61)
  })

  it('defaults to game time', () => {
    const sorted = sortOpportunities(items, 'GAME_TIME')
    expect(sorted.map((item) => item.row.starts_at)).toEqual([
      '2026-09-13T23:00:00Z', '2026-09-14T02:00:00Z',
    ])
  })
})

describe('the card', () => {
  it('shows the probability and the price that came from the same record', () => {
    // FanDuel at 8.5 and BetMGM at 7.5 on one game: the exact shape that would
    // produce a plausible-looking, wrong pairing if the card recombined them.
    const disagreeing = board({
      rows: [row({
        market: {
          line: 7.5, line_source: 'RECORDED_SPORTSBOOK_LINE', line_note: null, books: {},
          observed_at: '2026-09-13T17:39:00Z', quote_age_minutes: 101,
          other_lines: [8.5], unavailable_reason: null,
        },
        selections: [
          selection(),
          selection({
            line: 8.5, win_probability_pct: 38.1,
            explanation: explanation('Over 8.5', 38.1),
            price: price({
              sportsbook: 'Fanduel', american_odds: 130, decimal_odds: 2.3,
              model_implied_net_return_per_unit: -0.124,
            }),
          }),
        ],
      })],
    })
    paint(<TotalsOpportunities board={disagreeing} oversOnly sort="GAME_TIME" onSort={vi.fn()} />)

    // Read the whole card as text: the guarantee is about what one card
    // contains and what it must never contain, not about where it sits.
    const first = screen.getByTestId('totals-op-g1-OVER-7.5').textContent ?? ''
    expect(first).toMatch(/OVER 7\.5/)
    expect(first).toMatch(/49\.42%/)
    expect(first).toMatch(/\+105/)
    expect(first).toMatch(/Betmgm/)
    // The 8.5 card's own numbers may not leak into the 7.5 card.
    expect(first).not.toMatch(/38\.1/)
    expect(first).not.toMatch(/\+130/)
    expect(first).not.toMatch(/Fanduel/)
    expect(first).toMatch(/other recorded lines: 8\.5/)

    const second = screen.getByTestId('totals-op-g1-OVER-8.5').textContent ?? ''
    expect(second).toMatch(/38\.10%/)
    expect(second).toMatch(/\+130/)
    expect(second).not.toMatch(/49\.42%/)
  })

  it('says n/a for a push that cannot happen rather than 0%', () => {
    paint(<TotalsOpportunities board={board()} oversOnly={false} sort="GAME_TIME" onSort={vi.fn()} />)
    const card = within(screen.getByTestId('totals-op-g1-OVER-7.5'))
    expect(card.getByText('n/a')).toBeTruthy()
  })

  it('asks "Why this opportunity?", never "Why this wager?"', () => {
    paint(<TotalsOpportunities board={board()} oversOnly={false} sort="GAME_TIME" onSort={vi.fn()} />)
    expect(screen.getByText('Why this opportunity?')).toBeTruthy()
    expect(screen.queryByText('Why this wager?')).toBeNull()
  })

  it('states that nothing is recorded, funded or staked', () => {
    paint(<TotalsOpportunities board={board()} oversOnly={false} sort="GAME_TIME" onSort={vi.fn()} />)
    expect(screen.getByText(/Research opportunity · no stake/)).toBeTruthy()
    const policy = screen.getByRole('note').textContent ?? ''
    expect(policy).toMatch(/moneyline-only/)
    expect(policy).toMatch(/no moneyline threshold, probability penalty or staking rule/)
  })

  it('offers "Most likely Over" only while the listing is Overs', () => {
    const { rerender } = paint(
      <TotalsOpportunities board={board()} oversOnly={false} sort="GAME_TIME" onSort={vi.fn()} />)
    expect(screen.queryByText('Most likely Over')).toBeNull()
    rerender(
      <MemoryRouter>
        <TotalsOpportunities board={board()} oversOnly sort="GAME_TIME" onSort={vi.fn()} />
      </MemoryRouter>)
    expect(screen.getByText('Most likely Over')).toBeTruthy()
  })

  it('gives the caller\'s reason for an empty listing, not a generic blank', () => {
    paint(
      <TotalsOpportunities
        board={board({ rows: [] })} oversOnly={false} sort="GAME_TIME" onSort={vi.fn()}
        unavailableReason="No sportsbook total is recorded for any game on this slate." />)
    expect(screen.getByTestId('totals-op-empty').textContent)
      .toMatch(/No sportsbook total is recorded/)
  })
})
