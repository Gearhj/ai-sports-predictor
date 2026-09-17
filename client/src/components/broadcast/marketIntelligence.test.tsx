/**
 * Replay and the RFF Lab — the two places these surfaces can mislead.
 *
 * The replay carries a price forward, which is what the market really looked
 * like, and a carried price four hours old is not a usable quote. The stored
 * flag belongs to the instant the book last MOVED, so a book that has not
 * moved all day would carry this morning's "usable" into tonight and print
 * "12h ago · yes" on one row.
 *
 * The RFF Lab has one job it must never get wrong: a component with no data
 * reads Unavailable, never 0. A zero says we looked and found nothing.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MarketReplay, usableAt } from './MarketReplay'
import { RffComponents, RffGauge, RffShareCard, bandTone } from './RffLab'
import type { MarketTape, RffComponent, RffGame } from '../../types/marketIntelligence'

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

function component(over: Partial<RffComponent> = {}): RffComponent {
  return {
    key: 'CROSS_BOOK_DISAGREEMENT', label: 'Cross-book disagreement', weight: 0.14,
    available: true, score: 67, display: '67', basis: 'spread between books',
    detail: { spread_points: 3.4 }, requires: null, limitation: null,
    ...over,
  }
}

function rff(over: Partial<RffGame> = {}): RffGame {
  const components = [
    component({ key: 'PUBLIC_CONCENTRATION', label: 'Public concentration', weight: 0.2,
      available: false, score: null, display: 'Unavailable',
      requires: 'A public betting-percentage source.' }),
    component(),
  ]
  return {
    schema_version: 'AISP_RFF_INDEX_V1', generated_at_utc: T(0), game_id: 'g1',
    status: 'PROVISIONAL_RFF', label: 'RFF Lens — Market reality check',
    description: 'A research measure of market imbalance.',
    market_type: 'MONEYLINE',
    event: tape().event,
    components,
    score: 60, score_range: [30, 80],
    score_basis: 'Weighted mean of the components that have data.',
    band: { key: 'SPICY', label: 'Spicy', description: 'Real disagreement.' },
    evidence_coverage: 0.5, available_components: 1, unavailable_components: 1,
    contributions: [{ key: 'CROSS_BOOK_DISAGREEMENT', label: 'Cross-book disagreement', score: 67, weight: 0.14, share_of_score: 1 }],
    earlier: null,
    explanation: 'Provisional RFF 60 · 50% evidence coverage.',
    share_card: {
      matchup: 'TB @ ATL', headline: 'Provisional RFF 60', band: 'Spicy', coverage: 0.5,
      triggers: [{ label: 'Cross-book disagreement', score: 67, why: 'spread between books' }],
      disclaimer: 'Research index. Not evidence that a game is manipulated, and not a betting signal.',
    },
    authority: { affects_forecast: false, statement: 'RFF is a research index.' },
    sources: {}, requirement: null,
    ...over,
  }
}

describe('RFF components', () => {
  it('renders an unavailable component as the word, never as a zero', () => {
    render(<RffComponents game={rff()} />)
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    const row = screen.getByText('Public concentration').closest('.bx-rff__component')!
    expect(row).not.toHaveTextContent(/\b0\b/)
    expect(row.className).toContain('is-unavailable')
  })

  it('shows an available component as its score with its share of the number', () => {
    render(<RffComponents game={rff()} />)
    expect(screen.getByText('67')).toBeInTheDocument()
    expect(screen.getByText('100% of score')).toBeInTheDocument()
  })

  it('shows the weight of a component that has no data, so its absence is sized', () => {
    render(<RffComponents game={rff()} />)
    expect(screen.getByText('weight 20%')).toBeInTheDocument()
  })
})

describe('RFF gauge', () => {
  it('publishes the plausible range beside the point estimate', () => {
    render(<RffGauge game={rff()} />)
    expect(screen.getByText(/plausible 30–80 at 50% coverage/i)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /plausible range 30 to 80, 50 percent evidence coverage/i }))
      .toBeInTheDocument()
  })

  it('says a game with no scored component is unscored rather than calm', () => {
    render(<RffGauge game={rff({ score: null, score_range: null, band: { key: 'UNSCORED', label: 'Unscored', description: 'No component has data.' } })} />)
    expect(screen.getByRole('img', { name: /No RFF score: no component has data/i })).toBeInTheDocument()
    expect(screen.queryByText(/plausible/i)).toBeNull()
  })
})

describe('RFF share card', () => {
  it('carries its own disclaimer, because it is the piece that travels', () => {
    render(<RffShareCard game={rff()} />)
    expect(screen.getByText(/Not evidence that a game is manipulated/i)).toBeInTheDocument()
    expect(screen.getByText(/50% evidence coverage/i)).toBeInTheDocument()
  })

  it('never accuses anyone of anything', () => {
    const { container } = render(<RffShareCard game={rff()} />)
    const text = container.textContent!.toLowerCase()
    for (const forbidden of ['rigged', 'fixed', 'manipulating', 'cheating', 'corrupt', 'scandal']) {
      expect(text).not.toContain(forbidden)
    }
  })
})

describe('band tone', () => {
  it('gives every band its own colour and never guesses one', () => {
    const tones = ['CALM', 'CURIOUS', 'SPICY', 'MAXIMUM_FUCKERY'].map(bandTone)
    expect(new Set(tones).size).toBe(4)
    expect(bandTone('SOMETHING_ELSE')).toBe('var(--bx-dim)')
  })
})
