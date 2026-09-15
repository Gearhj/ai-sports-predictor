/**
 * The Daily Decisions market filter.
 *
 * The filter decides which of two very different things the desk is showing:
 * recorded moneyline decisions, and totals research that is not a decision at
 * all. Two mistakes matter here. Selecting Totals must not make the moneyline
 * rows vanish into an "empty slate" message — that would report a filter as a
 * finding. And an empty totals listing must name its own cause, because "no
 * book has posted a total" and "the model has nothing" are different facts that
 * a shared blank would merge.
 */
import { describe, expect, it, vi } from 'vitest'
import {
  readMarket, readTotalsSort, totalsUnavailableReason,
} from './MultiSportTodayPage'
import type { TotalsBoard } from '../types/totals'
// The four-sport CommandDecisions region has its own tests; these cover the page's audit view.
vi.mock('../components/command/CommandDecisions', () => ({ CommandDecisions: () => null }))


function board(coverage: Partial<TotalsBoard['coverage']>): TotalsBoard {
  return {
    schema_version: 'AISP_MLB_TOTALS_BOARD_V1', event_date: '2026-09-13',
    generated_at: '2026-09-13T19:00:00Z', probability_units: 'PERCENT',
    overs_only: false, filter_note: null, rows: [],
    coverage: {
      games: 15, games_with_v18_distribution: 15, games_index_verified: 11,
      games_with_recorded_line: 1, games_priceable: 1, selections_with_usable_price: 2,
      ...coverage,
    },
    market_availability: {
      latest_total_quote_at: '2026-09-07T12:00:00Z',
      latest_total_quote_source: 'SPORTSGAMEODDS', age_hours: 150,
      detail: 'The last MLB total AISP holds was captured on 2026-09-07.',
    },
  }
}

describe('readMarket', () => {
  it('defaults to all markets, including on an unknown value', () => {
    expect(readMarket(null)).toBe('ALL')
    expect(readMarket('')).toBe('ALL')
    expect(readMarket('SPREADS')).toBe('ALL')
  })

  it('reads the two it supports, case-insensitively', () => {
    expect(readMarket('totals')).toBe('TOTALS')
    expect(readMarket('MONEYLINE')).toBe('MONEYLINE')
  })
})

describe('readTotalsSort', () => {
  it('refuses "most likely Over" on a listing that is not Overs only', () => {
    // Applied to a mixed listing it would rank Overs and Unders of the same
    // game against each other by one side's number.
    expect(readTotalsSort('WIN_PROBABILITY', false)).toBe('GAME_TIME')
    expect(readTotalsSort('WIN_PROBABILITY', true)).toBe('WIN_PROBABILITY')
  })

  it('keeps estimated value available either way', () => {
    expect(readTotalsSort('EXPECTED_RETURN', false)).toBe('EXPECTED_RETURN')
  })

  it('falls back to game time', () => {
    expect(readTotalsSort(null, true)).toBe('GAME_TIME')
    expect(readTotalsSort('nonsense', true)).toBe('GAME_TIME')
  })
})

describe('totalsUnavailableReason', () => {
  it('says nothing before a board has loaded', () => {
    expect(totalsUnavailableReason(null)).toBeNull()
  })

  it('blames the market feed, and quotes when it last had one', () => {
    const reason = totalsUnavailableReason(board({
      games_with_recorded_line: 0, games_priceable: 0, selections_with_usable_price: 0,
    })) ?? ''
    expect(reason).toMatch(/No sportsbook total is recorded/)
    expect(reason).toMatch(/2026-09-07/)
    expect(reason).not.toMatch(/forecast/)
  })

  it('blames the forecast only when the forecast is what is missing', () => {
    const reason = totalsUnavailableReason(board({
      games_with_v18_distribution: 0, games_with_recorded_line: 0,
      games_priceable: 0, selections_with_usable_price: 0,
    })) ?? ''
    expect(reason).toMatch(/no current V18 forecast|has a current V18 forecast/)
  })

  it('distinguishes an empty slate from a covered one', () => {
    expect(totalsUnavailableReason(board({
      games: 0, games_with_v18_distribution: 0, games_with_recorded_line: 0,
      games_priceable: 0, selections_with_usable_price: 0,
    }))).toMatch(/No MLB game is scheduled/)
  })

  it('says the line exists but the price does not, when that is the case', () => {
    expect(totalsUnavailableReason(board({ selections_with_usable_price: 0 })))
      .toMatch(/no side carries a usable offered price/)
  })

  it('returns null when the board has priced selections to show', () => {
    expect(totalsUnavailableReason(board({}))).toBeNull()
  })
})
