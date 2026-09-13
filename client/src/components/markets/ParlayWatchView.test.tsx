/**
 * Parlay Watch, tested on the claims it must not make.
 *
 * The fixture is the live COL/SEA-era shape: two eligible games, moneyline legs
 * only, no recorded combined quote anywhere. The screen has to be useful in that
 * state without letting the estimate pass for an offer or letting "every leg
 * wins" pass for "the ticket pays".
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ParlayWatchView } from './ParlayWatchView'
import type { ParlayTicketCandidate, ParlayWatch } from '../../types/totals'

const INDEPENDENCE =
  'Legs are combined as if the games were independent. AISP has not measured '
  + 'cross-game correlation, and shared conditions (weather, umpires, league-wide '
  + 'scoring) would move same-side legs together. Treat the combined figures as an '
  + 'approximation.'

const ESTIMATED =
  'No sportsbook parlay quote is recorded for this combination. The combined price '
  + "is the product of that book's individual leg prices. Books apply their own "
  + 'parlay rules and may not offer this price, so this is a research estimate, not '
  + 'an available offer.'

function leg(over: Partial<ParlayTicketCandidate['legs'][0]> = {}) {
  return {
    game_id: 'g1', market: 'MONEYLINE' as const, selection: 'SEA ML', line: null,
    win_probability_pct: 57.58, push_probability_pct: 0, loss_probability_pct: 42.42,
    probability_units: 'PERCENT' as const, decimal_odds: 1.769, american_odds: -130,
    sportsbook: 'Espnbet', forecast_id: 'mlb-v18-daily-abc',
    engine_id: 'MLB_V18_WHOLE_GAME',
    quote_observed_at: '2026-09-13T18:30:00Z', starts_at: '2026-09-13T20:05:00Z',
    odds_snapshot_id: 's1', market_outcome_id: 'o1', push_possible: false,
    ...over,
  }
}

function ticket(over: Partial<ParlayTicketCandidate> = {}): ParlayTicketCandidate {
  return {
    ticket_key: 'k1',
    sportsbook: 'Espnbet',
    legs: [leg(), leg({ game_id: 'g2', selection: 'AZ ML', win_probability_pct: 58.44,
                        loss_probability_pct: 41.56 })],
    price: {
      leg_count: 2,
      all_legs_win_probability_pct: 33.65,
      ticket_pays_probability_pct: 33.65,
      stake_returned_probability_pct: 0,
      ticket_loses_probability_pct: 66.35,
      probability_units: 'PERCENT',
      combined_decimal_odds: 3.1302,
      price_provenance: 'ESTIMATED_FROM_SAME_BOOK_LEG_ODDS',
      model_fair_decimal_odds: 2.9718,
      estimated_net_return_per_unit: 0.053297,
      payout_multiple_if_all_win: 3.1302,
      push_possible: false,
      independence_note: INDEPENDENCE,
      probability_definitions: {},
    },
    earliest_cutoff_utc: '2026-09-13T19:50:00Z',
    games: ['g1', 'g2'],
    ...over,
  }
}

function watch(over: Partial<ParlayWatch> = {}): ParlayWatch {
  const featured = ticket({
    feature_labels: ['HIGHEST_ESTIMATED_CHANCE', 'BEST_ESTIMATED_VALUE'],
    feature_note: 'The same combination leads on both measures. It is shown once, not twice.',
  })
  return {
    schema_version: 'AISP_MLB_PARLAY_WATCH_V1',
    event_date: '2026-09-13',
    generated_at: '2026-09-13T19:00:00Z',
    leg_count: 2,
    market_filter: 'ALL',
    probability_units: 'PERCENT',
    independence_note: INDEPENDENCE,
    estimated_price_note: ESTIMATED,
    pregame_cutoff_minutes: 15,
    legs_available: 36,
    candidate_count: 36,
    tickets: [featured],
    truncated: false,
    featured: [featured],
    excluded_games: [{
      game_id: 'g9', matchup: 'COL @ DET', reason: 'PAST_PREGAME_CUTOFF',
      detail: 'This game is inside the 15-minute pregame cutoff, so no leg from it can still be recorded.',
    }],
    leg_inventory: { by_sportsbook: { Espnbet: 4 }, by_market: { MONEYLINE: 36 }, games: 2 },
    ...over,
  }
}

function mount(over: Partial<Parameters<typeof ParlayWatchView>[0]> = {}) {
  return render(
    <ParlayWatchView
      watch={watch()} legCount={2} onLegCount={vi.fn()}
      marketFilter="ALL" onMarketFilter={vi.fn()} onRecord={vi.fn()}
      recordingKey={null} recordedByKey={{}} recordError={null}
      {...over}
    />,
  )
}

describe('the assumptions are on the screen', () => {
  afterEach(cleanup)

  it('states cross-game independence as an approximation', () => {
    mount()
    const note = screen.getByTestId('parlay-independence')
    expect(note).toHaveTextContent(/as if the games were independent/)
    expect(note).toHaveTextContent(/has not measured cross-game correlation/)
  })

  it('says an estimated combined price may not be offered', () => {
    mount()
    const note = screen.getByTestId('parlay-estimated-note')
    expect(note).toHaveTextContent(/research estimate, not an available offer/)
  })

  it('badges the combined price as estimated on the ticket itself', () => {
    mount()
    expect(within(screen.getByTestId('parlay-ticket'))
      .getByText('Estimated')).toBeInTheDocument()
  })

  it('labels a real recorded quote differently', () => {
    const recorded = ticket({
      ticket_key: 'k2',
      price: {
        ...ticket().price,
        price_provenance: 'RECORDED_SPORTSBOOK_COMBINED_QUOTE',
        combined_decimal_odds: 3.0,
      },
    })
    mount({ watch: watch({ tickets: [recorded], featured: [recorded] }) })
    expect(within(screen.getByTestId('parlay-ticket'))
      .getByText('Recorded quote')).toBeInTheDocument()
  })
})

describe('"every leg wins" and "the ticket pays" stay apart', () => {
  afterEach(cleanup)

  it('shows both, each under its own label', () => {
    mount()
    const card = screen.getByTestId('parlay-ticket')
    expect(card).toHaveTextContent('Ticket pays')
    expect(card).toHaveTextContent('Every leg wins')
  })

  it('adds a stake-returned figure only when a leg can push', () => {
    mount()
    expect(screen.getByTestId('parlay-ticket')).not.toHaveTextContent('Stake returned')

    cleanup()
    const pushable = ticket({
      legs: [leg({ market: 'TOTAL', selection: 'OVER 8', line: 8,
                   push_probability_pct: 10.18, push_possible: true }),
             leg({ game_id: 'g2', selection: 'AZ ML' })],
      price: {
        ...ticket().price,
        push_possible: true,
        all_legs_win_probability_pct: 22.5,
        ticket_pays_probability_pct: 27.5,
        stake_returned_probability_pct: 1.2,
      },
    })
    mount({ watch: watch({ tickets: [pushable], featured: [pushable] }) })
    const card = screen.getByTestId('parlay-ticket')
    expect(card).toHaveTextContent('Stake returned')
    expect(card).toHaveTextContent('27.50%')
    expect(card).toHaveTextContent('22.50%')
    expect(card).toHaveTextContent(/pushed leg is removed and the ticket reprices/)
  })
})

describe('featuring', () => {
  afterEach(cleanup)

  it('shows one card carrying both labels when they coincide', () => {
    mount()
    const cards = screen.getAllByTestId('parlay-ticket')
    expect(cards).toHaveLength(1)
    expect(cards[0]).toHaveTextContent('Highest estimated chance')
    expect(cards[0]).toHaveTextContent('Best estimated value')
    expect(cards[0]).toHaveTextContent('shown once, not twice')
  })

  it('shows two distinct featured tickets when they differ', () => {
    const chance = ticket({ ticket_key: 'chance',
                            feature_labels: ['HIGHEST_ESTIMATED_CHANCE'] })
    const value = ticket({ ticket_key: 'value',
                           feature_labels: ['BEST_ESTIMATED_VALUE'] })
    mount({ watch: watch({ tickets: [chance, value], featured: [chance, value] }) })
    expect(screen.getAllByTestId('parlay-ticket')).toHaveLength(2)
  })

  it('says nothing is eligible rather than showing an empty grid', () => {
    mount({
      watch: watch({
        tickets: [], featured: [], candidate_count: 0, legs_available: 18,
        leg_inventory: { by_sportsbook: {}, by_market: { MONEYLINE: 18 }, games: 1 },
      }),
    })
    const empty = screen.getByTestId('parlay-empty')
    expect(empty).toHaveTextContent('No eligible combination exists')
    // And it explains the actual reason rather than leaving it a mystery.
    expect(empty).toHaveTextContent('Only 1 game still has a usable leg')
    expect(empty).toHaveTextContent('needs 2 distinct games')
  })
})

describe('filters and recording', () => {
  afterEach(cleanup)

  it('offers every documented market filter', () => {
    mount()
    for (const key of ['ALL', 'OVERS_ONLY', 'TOTALS_ONLY', 'MIXED']) {
      expect(screen.getByTestId(`parlay-filter-${key}`)).toBeInTheDocument()
    }
  })

  it('reports the active filter through aria-pressed', () => {
    mount({ marketFilter: 'OVERS_ONLY' })
    expect(screen.getByTestId('parlay-filter-OVERS_ONLY'))
      .toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('parlay-filter-ALL'))
      .toHaveAttribute('aria-pressed', 'false')
  })

  it('records a ticket only when the button is pressed', () => {
    const onRecord = vi.fn()
    mount({ onRecord })
    expect(onRecord).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('parlay-record'))
    expect(onRecord).toHaveBeenCalledTimes(1)
    expect(onRecord.mock.calls[0][0].ticket_key).toBe('k1')
  })

  it('replaces the button once the ticket is recorded', () => {
    mount({ recordedByKey: { k1: 'Recorded. $1 research ticket, unfunded.' } })
    expect(screen.queryByTestId('parlay-record')).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent('Recorded')
  })

  it('surfaces a recording failure instead of silently doing nothing', () => {
    mount({ recordError: 'no eligible leg matches OVER 8.5' })
    expect(screen.getByRole('alert')).toHaveTextContent('no eligible leg matches')
  })

  it('lists the excluded games with their reasons', () => {
    mount()
    expect(screen.getByText(/1 games excluded, and why/)).toBeInTheDocument()
    expect(screen.getByText(/15-minute pregame cutoff/)).toBeInTheDocument()
  })
})
