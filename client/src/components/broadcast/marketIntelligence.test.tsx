/**
 * Replay — where this surface can mislead.
 *
 * The replay carries a price forward, which is what the market really looked
 * like, and a carried price four hours old is not a usable quote. The stored
 * flag belongs to the instant the book last MOVED, so a book that has not
 * moved all day would carry this morning's "usable" into tonight and print
 * "12h ago · yes" on one row.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MarketReplay, usableAt } from './MarketReplay'
import type { MarketTape } from '../../types/marketIntelligence'

afterEach(cleanup)

const T = (minutesAgo: number) =>
  new Date(Date.UTC(2026, 8, 10, 16, 0) - minutesAgo * 60_000).toISOString()

function tape(over: Partial<MarketTape> = {}): MarketTape {
  const frames: MarketTape['frames'] = [
    {
      t: T(300), before_cutoff: true, usable_books: 2, consensus_no_vig_a: 0.52,
      consensus_no_vig_home: 0.52, dispersion: 0.004, range_a: [0.515, 0.525],
      best_a: { american: -120, sportsbook_id: 'b1' }, best_b: { american: 105, sportsbook_id: 'b2' },
    },
    {
      t: T(10), before_cutoff: true, usable_books: 1, consensus_no_vig_a: 0.55,
      consensus_no_vig_home: 0.55, dispersion: null, range_a: [0.55, 0.55],
      best_a: { american: -140, sportsbook_id: 'b2' }, best_b: { american: 118, sportsbook_id: 'b2' },
    },
  ]
  return {
    schema_version: 'AISP_MARKET_TAPE_V1', data_mode: 'live', generated_at_utc: T(0),
    game_id: 'g1',
    market: {
      market_type: 'MONEYLINE', period: 'full_game',
      side_a: { market_outcome_id: 'oa', outcome_key: 'a', label: 'Atlanta Braves' },
      side_b: { market_outcome_id: 'ob', outcome_key: 'b', label: 'Tampa Bay Rays' },
      home_side: 'a', probability_meaning: 'Two-sided no-vig probability of the home selection at one book',
    },
    event: {
      home: { team_id: 'h', abbreviation: 'ATL', name: 'Atlanta Braves' },
      away: { team_id: 'a', abbreviation: 'TB', name: 'Tampa Bay Rays' },
      starts_at_utc: T(-60), decision_cutoff_utc: T(-45), status: 'SCHEDULED',
    },
    providers: {
      primary: 'THE_ODDS_API', fallback: 'SPORTSGAMEODDS', fallback_is_temporary: true,
      present: [{ provider: 'SPORTSGAMEODDS', books: 2 }],
      note: 'A pair is always one book at one provider.',
    },
    books: [
      {
        sportsbook_id: 'b1', name: 'Williamhill', key: 'sgo:williamhill', provider: 'SPORTSGAMEODDS',
        observations: 2,
        // Quoted once, five hours ago, and never moved since.
        series: [{
          t: T(300), a: -120, b: 105, point_a: null, point_b: null,
          observed_at_utc: T(300), usable: true, skew_seconds: 0, no_vig_a: 0.52,
        }],
      },
      {
        sportsbook_id: 'b2', name: 'Bovada', key: 'sgo:bovada', provider: 'SPORTSGAMEODDS',
        observations: 4,
        series: [
          { t: T(300), a: -122, b: 106, point_a: null, point_b: null, observed_at_utc: T(300), usable: true, skew_seconds: 0, no_vig_a: 0.523 },
          { t: T(10), a: -140, b: 118, point_a: null, point_b: null, observed_at_utc: T(10), usable: true, skew_seconds: 0, no_vig_a: 0.55 },
        ],
      },
    ],
    contract: {}, frames, frame_count: frames.length, observed_instants: 2,
    frames_sampled: false, first_observed_utc: T(300), last_observed_utc: T(10),
    model_probability_series: [{
      version: 1, generated_at_utc: T(200), available_as_of_utc: T(200),
      home_win_probability: 0.5, status: 'published', lifecycle: 'morning',
    }],
    milestones: {
      opening: { t: T(300), consensus_no_vig_a: 0.52, usable_books: 2 },
      latest: { t: T(10), consensus_no_vig_a: 0.55, usable_books: 1 },
      at_cutoff: null,
      movement: { points: 3, velocity_points_per_hour: 0.62, hours_observed: 4.83 },
    },
    requirement: null,
    ...over,
  }
}

describe('MarketReplay', () => {
  it('re-derives usability at the frame being viewed, not at the last move', () => {
    // Williamhill last quoted five hours ago and never moved; its stored flag
    // says usable because it WAS, at the instant it published.
    render(<MarketReplay tape={tape()} family="MONEYLINE" selectedBook={null} onSelectBook={() => undefined} />)
    const table = screen.getByRole('table')
    const row = within(table).getByRole('row', { name: /Williamhill/ })
    expect(row).toHaveTextContent(/stale at this instant/)
    const fresh = within(table).getByRole('row', { name: /Bovada/ })
    expect(fresh).toHaveTextContent(/yes/)
  })

  it('marks a carried price with the time it was really observed', () => {
    render(<MarketReplay tape={tape()} family="MONEYLINE" selectedBook={null} onSelectBook={() => undefined} />)
    const row = within(screen.getByRole('table')).getByRole('row', { name: /Williamhill/ })
    expect(row).toHaveTextContent(/5h ago/)
    // The price itself is still shown: that is what the market looked like.
    expect(row).toHaveTextContent('-120')
  })

  it('states the absence when nothing has a complete two-sided quote', () => {
    const empty = tape({
      frames: [{
        t: T(10), before_cutoff: true, usable_books: 0, consensus_no_vig_a: null,
        consensus_no_vig_home: null, dispersion: null, range_a: null, best_a: null, best_b: null,
      }],
    })
    render(<MarketReplay tape={empty} family="MONEYLINE" selectedBook={null} onSelectBook={() => undefined} />)
    expect(screen.getByText(/none has a complete two-sided quote at one book/i)).toBeInTheDocument()
  })

  it('passes the market requirement through rather than drawing an empty chart', () => {
    const none = tape({ requirement: 'No spread price has been persisted for this game.' })
    render(<MarketReplay tape={none} family="SPREAD" selectedBook={null} onSelectBook={() => undefined} />)
    expect(screen.getByText(/No spread price has been persisted/i)).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('labels the model line as something other than a market observation', () => {
    render(<MarketReplay tape={tape()} family="MONEYLINE" selectedBook={null} onSelectBook={() => undefined} />)
    expect(screen.getByText(/Model forecast — not a market observation/i)).toBeInTheDocument()
  })

  it('says a provider is never combined with another into one quote', () => {
    render(<MarketReplay tape={tape()} family="MONEYLINE" selectedBook={null} onSelectBook={() => undefined} />)
    expect(screen.getByText(/A pair is always one book at one provider/i)).toBeInTheDocument()
  })
})

describe('usableAt', () => {
  const entry = {
    t: T(300), a: -120, b: 105, point_a: null, point_b: null,
    observed_at_utc: T(300), usable: true, skew_seconds: 0, no_vig_a: 0.52,
  }
  it('expires a carried quote past the freshness window', () => {
    expect(usableAt(entry, T(300))).toBe(true)
    expect(usableAt(entry, T(120))).toBe(true)   // three hours old
    expect(usableAt(entry, T(10))).toBe(false)   // nearly five hours old
  })
  it('never revives a quote the service already refused', () => {
    expect(usableAt({ ...entry, usable: false }, T(300))).toBe(false)
  })
})

/* -- RFF ------------------------------------------------------------------- */
