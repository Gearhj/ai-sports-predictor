/**
 * The totals screen, tested on the states it actually has to survive.
 *
 * The live one today is "the model is ready and the market is not": V18 has a
 * distribution for every game and AISP holds no MLB total from any book since
 * 2026-09-07. That must read as a named gap in the market feed, not as a broken
 * model and not as a zero.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MlbTotalsView } from './MlbTotalsView'
import type { TotalsBoard, TotalsRow } from '../../types/totals'

const FORECAST = {
  engine_id: 'MLB_V18_WHOLE_GAME',
  engine_version: 'mlb-v18-whole-game-a6e7e99',
  model_label: 'V18 whole-game (research shadow)',
  forecast_id: 'mlb-v18-daily-abc',
  availability: 'AVAILABLE',
  availability_detail: '',
  decision_eligible: true,
  distribution_available: true,
  expected_total_runs: 9.0696,
  simulation_count: 5000,
  support: [1, 34] as [number, number],
  unavailable_reason: null,
}

function row(over: Partial<TotalsRow> = {}): TotalsRow {
  return {
    game_id: 'g1',
    matchup: 'COL @ DET',
    away_team: { id: 'a', abbreviation: 'COL', name: 'Colorado Rockies' },
    home_team: { id: 'h', abbreviation: 'DET', name: 'Detroit Tigers' },
    starts_at: '2026-09-13T16:10:00Z',
    status: 'SCHEDULED',
    forecast: FORECAST,
    market: {
      line: null, line_source: null, line_note: null, books: {},
      observed_at: null, quote_age_minutes: null, other_lines: [],
      unavailable_reason: 'NO_RECORDED_TOTAL_LINE',
    },
    selections: [],
    ...over,
  }
}

function board(over: Partial<TotalsBoard> = {}): TotalsBoard {
  return {
    schema_version: 'AISP_MLB_TOTALS_BOARD_V1',
    event_date: '2026-09-13',
    generated_at: '2026-09-13T19:00:00Z',
    probability_units: 'PERCENT',
    overs_only: false,
    filter_note: null,
    rows: [row()],
    coverage: {
      games: 15, games_with_v18_distribution: 14, games_with_recorded_line: 0,
      games_priceable: 0, selections_with_usable_price: 0,
    },
    market_availability: {
      latest_total_quote_at: '2026-09-07T20:44:23-04:00',
      latest_total_quote_source: 'THE_ODDS_API',
      age_hours: 138.5,
      detail: 'The newest MLB totals quote AISP holds was observed 2026-09-07 from THE_ODDS_API.',
    },
    ...over,
  }
}

function mount(over: Partial<Parameters<typeof MlbTotalsView>[0]> = {}) {
  return render(
    <MlbTotalsView
      board={board()} oversOnly={false} onOversOnly={vi.fn()}
      scenarios={{}} onScenario={vi.fn()} {...over}
    />,
  )
}

const PRICED_SELECTIONS = [
  {
    side: 'OVER' as const, line: 8.5, line_source: 'RECORDED_SPORTSBOOK_LINE' as const,
    win_probability_pct: 51.16, push_probability_pct: 0, loss_probability_pct: 48.84,
    probability_units: 'PERCENT' as const, push_possible: false,
    model_fair_decimal_odds: 1.9547, model_fair_american_odds: -105,
    price: {
      sportsbook: 'Bovada', decimal_odds: 1.91, american_odds: -110,
      observed_at: '2026-09-13T18:30:00Z', quote_age_minutes: 30, stale: false,
      odds_snapshot_id: 's1', market_outcome_id: 'o1',
      model_implied_net_return_per_unit: -0.0229,
      estimate_basis: 'p_win x (decimal odds - 1) - p_loss',
    },
    price_unavailable_reason: null,
  },
  {
    side: 'UNDER' as const, line: 8.5, line_source: 'RECORDED_SPORTSBOOK_LINE' as const,
    win_probability_pct: 48.84, push_probability_pct: 0, loss_probability_pct: 51.16,
    probability_units: 'PERCENT' as const, push_possible: false,
    model_fair_decimal_odds: 2.0475, model_fair_american_odds: 105,
    price: null, price_unavailable_reason: 'NO_RECORDED_PRICE_FOR_THIS_SIDE',
  },
]

describe('the model side is usable when the market side is not', () => {
  afterEach(cleanup)

  it('shows the V18 expected total with the simulation count behind it', () => {
    mount()
    const card = screen.getByTestId('totals-game-g1')
    expect(within(card).getByTestId('totals-expected')).toHaveTextContent('9.07')
    expect(within(card).getByTestId('totals-expected')).toHaveTextContent('5,000 sims')
  })

  it('says the line is not recorded rather than rendering a zero', () => {
    mount()
    const card = screen.getByTestId('totals-game-g1')
    expect(within(card).getByTestId('totals-line')).toHaveTextContent('Not recorded')
    expect(within(card).getByTestId('totals-line')).not.toHaveTextContent('0.0')
  })

  it('names the market gap and dates it, rather than blaming the model', () => {
    mount()
    const gap = screen.getByTestId('totals-market-gap')
    expect(gap).toHaveTextContent('No sportsbook total is recorded for any game')
    expect(gap).toHaveTextContent('2026-09-07')
    expect(gap).toHaveTextContent('THE_ODDS_API')
    expect(gap).toHaveTextContent(/model side below is current/i)
  })

  it('reports coverage as counts a reader can check', () => {
    mount()
    expect(screen.getByTestId('totals-coverage')).toHaveTextContent('15 games')
    expect(screen.getByTestId('totals-coverage')).toHaveTextContent(
      '0 with a recorded line')
  })

  it('offers a scenario input whenever the distribution exists', () => {
    mount()
    expect(screen.getByLabelText('Exploratory total for COL @ DET')).toBeInTheDocument()
  })
})

describe('a scenario line is never presented as a quote', () => {
  afterEach(cleanup)

  const scenarioRow = row({
    market: {
      line: 9, line_source: 'USER_SELECTED_SCENARIO',
      line_note: 'This line was chosen by you, not quoted by a sportsbook.',
      books: {}, observed_at: null, quote_age_minutes: null, other_lines: [],
      unavailable_reason: 'NO_RECORDED_TOTAL_LINE',
    },
    selections: [
      {
        side: 'OVER', line: 9, line_source: 'USER_SELECTED_SCENARIO',
        win_probability_pct: 40.98, push_probability_pct: 10.18,
        loss_probability_pct: 48.84, probability_units: 'PERCENT',
        push_possible: true, model_fair_decimal_odds: 2.19,
        model_fair_american_odds: 119, price: null,
        price_unavailable_reason: 'NO_RECORDED_TOTAL_LINE',
      },
    ],
  })

  it('labels the heading as the reader\'s own line, not the sportsbook\'s', () => {
    mount({ board: board({ rows: [scenarioRow] }) })
    const line = screen.getByTestId('totals-line')
    expect(line).toHaveTextContent('9.0')
    expect(line).toHaveTextContent('not a quote')
    expect(screen.getByText('Your scenario line')).toBeInTheDocument()
    expect(screen.queryByText('Sportsbook total')).toBeNull()
  })

  it('badges the selection and attaches no price', () => {
    mount({ board: board({ rows: [scenarioRow] }) })
    const selection = screen.getByTestId('totals-selection-OVER')
    expect(selection).toHaveTextContent('Scenario')
    expect(selection).toHaveTextContent('No price recorded')
  })

  it('shows the push on an integer line', () => {
    mount({ board: board({ rows: [scenarioRow] }) })
    expect(screen.getByTestId('totals-selection-OVER')).toHaveTextContent('10.2%')
  })
})

describe('half-run lines cannot push, and say so', () => {
  afterEach(cleanup)

  it('marks the push column not-applicable rather than zero', () => {
    mount({
      board: board({
        rows: [row({
          market: {
            line: 8.5, line_source: 'RECORDED_SPORTSBOOK_LINE', line_note: null,
            books: {}, observed_at: '2026-09-13T18:30:00Z', quote_age_minutes: 30,
            other_lines: [], unavailable_reason: null,
          },
          selections: PRICED_SELECTIONS,
        })],
      }),
    })
    // "n/a", not "0.0%": the game cannot land on 8.5, which is a different
    // statement from "it lands there 0% of the time".
    expect(screen.getByTestId('totals-selection-OVER')).toHaveTextContent('n/a')
  })

  it('labels the return estimate as model-implied and per dollar', () => {
    mount({
      board: board({
        rows: [row({
          market: {
            line: 8.5, line_source: 'RECORDED_SPORTSBOOK_LINE', line_note: null,
            books: {}, observed_at: '2026-09-13T18:30:00Z', quote_age_minutes: 30,
            other_lines: [], unavailable_reason: null,
          },
          selections: PRICED_SELECTIONS,
        })],
      }),
    })
    const over = screen.getByTestId('totals-selection-OVER')
    expect(over).toHaveTextContent('-0.023')
    expect(over).toHaveTextContent('model-implied')
    expect(over).toHaveTextContent('Bovada')
    expect(over).toHaveTextContent('30m old')
  })

  it('shows a side with no price as having none, not as even money', () => {
    mount({
      board: board({
        rows: [row({
          market: {
            line: 8.5, line_source: 'RECORDED_SPORTSBOOK_LINE', line_note: null,
            books: {}, observed_at: '2026-09-13T18:30:00Z', quote_age_minutes: 30,
            other_lines: [], unavailable_reason: null,
          },
          selections: PRICED_SELECTIONS,
        })],
      }),
    })
    expect(screen.getByTestId('totals-selection-UNDER'))
      .toHaveTextContent('No price recorded')
  })
})

describe('the Overs-only filter', () => {
  afterEach(cleanup)

  it('states that it changes nothing but the list', () => {
    mount({
      oversOnly: true,
      board: board({
        overs_only: true,
        filter_note:
          'Showing Over selections only. This changes which selections are listed; '
          + 'it does not change any probability, price or estimate, and it is not a '
          + 'claim that Overs offer better value.',
      }),
    })
    const note = screen.getByTestId('overs-only-note')
    expect(note).toHaveTextContent(/does not change any probability, price or estimate/)
    expect(note).toHaveTextContent(/not a claim that Overs offer better value/)
  })

  it('says which side was hidden and that its numbers are untouched', () => {
    mount({
      oversOnly: true,
      board: board({
        overs_only: true,
        rows: [row({
          selections: [PRICED_SELECTIONS[0]],
          hidden_by_filter: ['UNDER'],
        })],
      }),
    })
    expect(screen.getByTestId('totals-game-g1'))
      .toHaveTextContent('UNDER hidden by the Overs-only filter')
    expect(screen.getByTestId('totals-game-g1'))
      .toHaveTextContent('probabilities are unchanged')
  })

  it('reflects the toggle state to the checkbox', () => {
    mount({ oversOnly: true })
    expect(screen.getByTestId('overs-only-toggle')).toBeChecked()
  })
})

describe('an unusable forecast is explained, not hidden', () => {
  afterEach(cleanup)

  it('never substitutes Gen 1 when V18 is not current', () => {
    mount({
      board: board({
        rows: [row({
          forecast: {
            ...FORECAST, availability: 'SUPERSEDED', distribution_available: false,
            expected_total_runs: null, simulation_count: null,
            unavailable_reason: 'V18_NOT_CURRENT',
            availability_detail: 'A later V18 forecast replaced this one.',
          },
        })],
      }),
    })
    const card = screen.getByTestId('totals-game-g1')
    expect(card).toHaveTextContent(/Gen 1 is not substituted/)
    expect(within(card).getByTestId('totals-expected')).toHaveTextContent('—')
  })

  it('hides the scenario input when there is no distribution to interrogate', () => {
    mount({
      board: board({
        rows: [row({
          forecast: {
            ...FORECAST, distribution_available: false,
            unavailable_reason: 'NO_TOTAL_RUNS_DISTRIBUTION',
          },
        })],
      }),
    })
    expect(screen.queryByLabelText('Exploratory total for COL @ DET')).toBeNull()
  })
})
