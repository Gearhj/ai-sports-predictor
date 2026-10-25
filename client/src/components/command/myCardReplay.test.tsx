/**
 * Demo resilience and player-prop quotes: the historical card is separate,
 * honest about its time and cannot reach the live card or anything official;
 * player props are quotes only and are never presented as live from a fixture.
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const requests: { path: string; method: string }[] = []
const AS_OF = '2026-09-16T14:51:33-04:00'
const OBSERVED = '2026-09-16T14:49:06-04:00'
const replay = {
  schema_version: 'AISP_HISTORICAL_MARKET_REPLAY_V1', mode: 'HISTORICAL_REPLAY', available: true,
  as_of: AS_OF, as_of_label: 'Sep 16, 2026 2:51 PM ET',
  banner: 'Historical market replay — prices shown as they appeared at Sep 16, 2026 2:51 PM ET.',
  pick_label: 'Historical virtual pick — not an AISP decision.',
  creates_decisions: false, creates_positions: false, provider_calls_made: 0,
  games: [{
    game_id: 'g-nym', league: 'MLB', matchup: 'BAL @ NYM', starts_at: '2026-09-16T19:10:00-04:00',
    home: { abbreviation: 'NYM', name: 'New York Mets' }, away: { abbreviation: 'BAL', name: 'Baltimore Orioles' },
    quotes: [
      { market: 'MONEYLINE', line: null, sportsbook: 'DraftKings', sportsbook_key: 'sgo:draftkings', provider: 'SportsGameOdds',
        classification: 'RESEARCH', observed_at: OBSERVED, age_minutes: 2.5, fresh: true,
        sides: { HOME: { american: -150, decimal: 1.666667 }, AWAY: { american: 130, decimal: 2.3 } } },
      { market: 'TOTAL', line: 8.5, sportsbook: 'FanDuel', sportsbook_key: 'sgo:fanduel', provider: 'SportsGameOdds',
        classification: 'RESEARCH', observed_at: '2026-09-16T09:00:00-04:00', age_minutes: 351, fresh: false,
        sides: { OVER: { american: -110, decimal: 1.909 }, UNDER: { american: -110, decimal: 1.909 } } },
    ],
  }],
}

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string, options: RequestInit = {}) => {
      requests.push({ path, method: options.method ?? 'GET' })
      if (path === '/command-center/replay') return Promise.resolve(replay)
      return Promise.reject(new Error(`unexpected ${path}`))
    }),
  }
})

import { AllMarkets, FIXTURE_BANNER, MyCard, NO_ASSESSMENT, PlayerPropsSection, pickFrom, propPick } from './MarketsAndCard'
import { MY_CARD_KEY, REPLAY_CARD_KEY, REPLAY_PICK_LABEL, myCard, replayCard } from '../../services/myCard'
import { PLAYER_PROPS_FIXTURE } from '../../services/playerPropsFixture'
import type { DailyAssessments, PlayerPropQuote } from '../../services/probability'

const NOW = Date.parse('2026-09-16T21:30:00-04:00')  // nothing on the live card is current at this time

beforeEach(() => {
  try { window.localStorage.clear() } catch { /* ignore */ }
  myCard.reload(); myCard.clear(); replayCard.reload(); replayCard.clear()
})
afterEach(() => { cleanup(); requests.length = 0 })

function Harness() {
  const [on, setOn] = useState(false)
  return <MyCard now={NOW} replay={on} onReplay={setOn} />
}

describe('historical replay card', () => {
  it('rehearses: empty live card → replay → stored moneyline → stake → leave → live card still empty', async () => {
    render(<MemoryRouter><Harness /></MemoryRouter>)
    const live = screen.getByRole('region', { name: 'My Card' })
    expect(live).toHaveTextContent('No picks yet')

    fireEvent.click(screen.getByRole('button', { name: 'Try a historical card' }))
    const banner = await screen.findByTestId('replay-banner')
    await waitFor(() => expect(banner).toHaveTextContent(
      'Historical market replay — prices shown as they appeared at Sep 16, 2026 2:51 PM ET.'))
    const card = screen.getByRole('region', { name: 'Historical card' })
    expect(card).toHaveTextContent(REPLAY_PICK_LABEL)

    // The stored moneyline was current at the replay moment, so it can be picked.
    const add = await screen.findByRole('button', { name: /Add New York Mets to win -150 at DraftKings to the historical card/ })
    // The total was already stale at the replay moment, so it cannot.
    expect(screen.getByRole('button', { name: 'Over 8.5: not selectable' })).toBeDisabled()
    // No later information: no model, edge, EV, result or Game Center link.
    expect(screen.getByRole('region', { name: 'Historical markets' }).textContent)
      .not.toMatch(/\d+(\.\d+)?%|edge|expected return|final|won|lost|Game Center|no-vig|fair/i)
    expect(card.textContent).not.toMatch(/\d+(\.\d+)?%|Game Center/)
    fireEvent.click(add)
    const pick = within(card).getAllByRole('listitem')[0]
    expect(pick).toHaveTextContent('Historical virtual pick')
    expect(pick).toHaveTextContent('New York Mets to win -150')
    expect(pick).toHaveTextContent('DraftKings via SportsGameOdds')
    expect(pick).toHaveTextContent('Illustrative total if it wins $16.67')

    fireEvent.change(within(pick).getByRole('textbox', { name: /Virtual stake/ }), { target: { value: '30' } })
    fireEvent.blur(within(pick).getByRole('textbox', { name: /Virtual stake/ }))
    await waitFor(() => expect(within(card).getAllByRole('listitem')[0]).toHaveTextContent('Illustrative total if it wins $50.00'))

    const stored = JSON.parse(window.localStorage.getItem(REPLAY_CARD_KEY) ?? '[]')
    expect(stored).toHaveLength(1)
    expect(stored[0]).toMatchObject({
      quote_observed_at: OBSERVED, replay_as_of: AS_OF, freshness_at_add: 'CURRENT_AT_REPLAY_MOMENT',
      american: -150, sportsbook: 'DraftKings', stake: 30,
    })

    fireEvent.click(screen.getByRole('button', { name: 'Leave historical replay' }))
    const back = screen.getByRole('region', { name: 'My Card' })
    expect(back).toHaveTextContent('No picks yet')
    expect(screen.queryByTestId('replay-banner')).toBeNull()
    expect(window.localStorage.getItem(MY_CARD_KEY) ?? '[]').toBe('[]')
    expect(myCard.list()).toEqual([])
    // Only the read-only replay was requested; nothing was written anywhere.
    expect(requests).toEqual([{ path: '/command-center/replay', method: 'GET' }])
  })

  it('keeps the two stores apart in both directions', () => {
    const game = replay.games[0]
    const pick = pickFrom(game, game.quotes[0], 'HOME')!
    // A historical quote is stale now: the live card refuses it, with or without a replay moment.
    expect(myCard.add(pick, { now: NOW, current: true })).toBeNull()
    expect(myCard.add(pick, { now: Date.parse(AS_OF), current: true, asOf: AS_OF })).toBeNull()
    // The replay card refuses anything without a replay moment, and judges freshness at that moment.
    expect(replayCard.add(pick, { now: NOW, current: true })).toBeNull()
    expect(replayCard.add(pick, { asOf: '2026-09-16T23:00:00-04:00' })).toBeNull()
    const created = replayCard.add(pick, { asOf: AS_OF })!
    expect(created.quote_observed_at).toBe(OBSERVED)  // never restamped
    // A replay pick cannot be smuggled into the live card.
    expect(myCard.add(created as never, { now: Date.parse(OBSERVED), current: true })).toBeNull()
    expect(myCard.list()).toEqual([])
    expect(replayCard.list()).toHaveLength(1)
  })
})

const propQuote = (over: Partial<PlayerPropQuote> = {}): PlayerPropQuote => ({
  ...PLAYER_PROPS_FIXTURE.quotes[0], game_id: 'g-nym', quote_id: 'live-1', fresh: true, state: 'CURRENT',
  selectable: true, observed_at: new Date(NOW - 10 * 60_000).toISOString(), ...over,
})

function assessments(quotes: PlayerPropQuote[]): DailyAssessments {
  return {
    schema_version: 'AISP_DAILY_ASSESSMENTS_V1', date: '', generated_at: '', moneyline_tie_settlement: 'PUSH',
    games: [], research: [],
    parlay_policy: { sportsbook_parlay_quotes_stored: 0, unavailable_message: '', creates_decisions: false, creates_positions: false },
    cross_game: { status: 'UNSUPPORTED', code: 'CROSS_GAME_CALIBRATION_NOT_VALIDATED', message: '' },
    player_props: { ...PLAYER_PROPS_FIXTURE, message: '', quotes },
    provider_calls_made: 0,
  }
}

describe('player props', () => {
  const game = { game_id: 'g-nym', league: 'MLB', matchup: 'BAL @ NYM', starts_at: null }

  it('shows quotes only, with the provider terms underneath, and adds a current quote as a user pick', () => {
    const stale = propQuote({ quote_id: 'live-2', outcome: 'UNDER', provider_outcome: 'Under', description: 'No hit',
      american: 145, decimal: 2.45, observed_at: new Date(NOW - 300 * 60_000).toISOString(), state: 'STORED_REFERENCE_ONLY', selectable: false })
    render(<MemoryRouter><PlayerPropsSection game={game} quotes={[propQuote(), stale]} now={NOW} /><MyCard now={NOW} /></MemoryRouter>)
    const section = screen.getByRole('region', { name: 'Player props' })
    expect(section).toHaveTextContent(`${NO_ASSESSMENT}.`)
    expect(section).toHaveTextContent('Aaron Judge')
    expect(section).toHaveTextContent('1+ hit')
    expect(section).toHaveTextContent('Provider market: batter_hits · Over 0.5')
    // No model vocabulary or numbers beyond prices.
    expect(section.textContent).not.toMatch(/\d+(\.\d+)?%|fair price:|confidence|qualified|pass(ed)?\b|\bEV\b/i)
    expect(screen.getByRole('button', { name: /Aaron Judge No hit: not selectable \(price not current\)/ })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: /Add Aaron Judge 1\+ hit -185 at DraftKings to My Card/ }))
    const pick = within(screen.getByRole('region', { name: 'My Card' })).getAllByRole('listitem')[0]
    expect(pick).toHaveTextContent('Your virtual pick')
    expect(pick).toHaveTextContent('Aaron Judge 1+ hit -185')
    expect(pick).toHaveTextContent('hits (player prop)')
    expect(pick).toHaveTextContent(`Provider market: batter_hits · Over 0.5 · ${NO_ASSESSMENT}`)
    const stored = JSON.parse(window.localStorage.getItem(MY_CARD_KEY) ?? '[]')[0]
    expect(stored).toMatchObject({ market: 'PLAYER_PROP', outcome: 'OVER', line: 0.5,
      prop: { market_key: 'batter_hits', provider_outcome: 'Over', provider_line: 0.5, player_name: 'Aaron Judge' } })
    for (const key of Object.keys(stored).concat(Object.keys(stored.prop))) {
      expect(key).not.toMatch(/prob|fair|edge|expected|decision|confidence|model/i)
    }
    expect(requests).toEqual([])
  })

  it('renders a section only when stored quotes exist for the game', () => {
    const coverage = { game_id: 'g-nym', league: 'MLB', matchup: 'BAL @ NYM', starts_at: new Date(NOW + 3600e3).toISOString(),
      status: 'SCHEDULED', price_state: 'CURRENT_PRICE', books: 1, fresh_books: 1, freshest_observed_at: null,
      stalest_usable_observed_at: null, consensus_no_vig_home: null, consensus_is_current: false,
      best: { HOME: null, AWAY: null }, model: null, quotes: [replay.games[0].quotes[0]],
      checkpoints: { collection_opens_at: '', decision_window_opens_at: '', decision_window_closes_at: '' } }
    const entry = { gameId: 'g-nym', game: coverage as never, assessment: null }
    const { unmount } = render(<MemoryRouter><AllMarkets games={[entry]} data={assessments([])} now={NOW} onOpenLab={() => {}} /></MemoryRouter>)
    expect(screen.queryByRole('region', { name: 'Player props' })).toBeNull()
    unmount()
    render(<MemoryRouter><AllMarkets games={[entry]} data={assessments([propQuote()])} now={NOW} onOpenLab={() => {}} /></MemoryRouter>)
    expect(screen.getByRole('region', { name: 'Player props' })).toHaveTextContent('1+ hit')
  })

  it('the fixture preview is labelled and never selectable', () => {
    render(<MemoryRouter><AllMarkets games={[]} data={assessments([])} now={NOW} onOpenLab={() => {}} preview /></MemoryRouter>)
    const preview = screen.getByTestId('player-props-preview')
    expect(preview).toHaveTextContent(FIXTURE_BANNER)
    const buttons = within(preview).getAllByRole('button')
    expect(buttons.length).toBe(PLAYER_PROPS_FIXTURE.quotes.length)
    buttons.forEach((b) => expect(b).toBeDisabled())
    expect(preview).not.toHaveTextContent(/\bCurrent\b/)
    expect(preview).toHaveTextContent('7+ strikeouts')
    act(() => { buttons.forEach((b) => fireEvent.click(b)) })
    expect(myCard.list()).toEqual([])
    expect(propPick(game, PLAYER_PROPS_FIXTURE.quotes[0])?.prop?.description).toBe('1+ hit')
  })
})
