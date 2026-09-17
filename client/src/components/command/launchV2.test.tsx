/**
 * Launch experience v2 acceptance: Home order, Experiment trim, Game Center
 * charts and reconciliation, All Markets selection, My Card isolation, slate
 * sorting, and the no-fabrication rules for props and cross-game products.
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { namedScenario, game as fixtureGame, priced, type Scenario } from '../../test/homeFixtures'

const requests: { path: string; method: string }[] = []
let scenario: Scenario | null = null
let story: unknown = null

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string, options: RequestInit = {}) => {
      requests.push({ path, method: options.method ?? 'GET' })
      if (path.startsWith('/probability/games/') && path.endsWith('/story')) {
        return story ? Promise.resolve(story) : Promise.reject(new Error('Game not found'))
      }
      if (!scenario) return new Promise(() => {})
      if (path.startsWith('/command-center/decisions')) return Promise.resolve(scenario.decisions)
      if (path.startsWith('/command-center/markets')) return Promise.resolve(scenario.markets)
      if (path.startsWith('/command-center/ledger')) return Promise.resolve(scenario.ledger)
      if (path.startsWith('/sports/readiness')) return Promise.resolve({ schema_version: 'x', generated_at: '', authority: '', sports: [] })
      return Promise.reject(new Error(`unexpected ${path}`))
    }),
  }
})

import { CommandHome } from './CommandHome'
import { CommandExperiment } from './CommandExperiment'
import { AllMarkets, MyCard, pickFrom } from './MarketsAndCard'
import { GameStory } from '../story/GameStory'
import { MY_CARD_KEY, MY_CARD_LABEL, myCard, pickReturn } from '../../services/myCard'
import { regionMasses, regionOf, type GameStory as Story } from '../../services/gameStory'
import { currentGap, sortSlate } from '../../services/slateSort'
import type { AssessedQuote, DailyAssessments } from '../../services/probability'
import type { SlateGame } from '../../services/broadcastSlate'

beforeEach(() => { try { window.localStorage.clear() } catch { /* ignore */ } myCard.reload(); myCard.clear() })
afterEach(() => { cleanup(); scenario = null; story = null; requests.length = 0 })

/* --------------------------------------------------------------- Home --- */

describe('Home order', () => {
  it('leads with the dark sections and puts AISP Today last', async () => {
    scenario = namedScenario('four-plus', { now: Date.now() })
    render(<MemoryRouter><CommandHome withSport={(href) => href} /></MemoryRouter>)
    const today = await screen.findByRole('region', { name: 'AISP Today' })
    const edge = screen.getByRole('region', { name: /Today’s|Closest/ })
    const bank = screen.getByRole('region', { name: 'Combined virtual bankroll' })
    const timeline = screen.getByRole('region', { name: "Today's timeline" })
    const order = [edge, bank, timeline, today]
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
    // Nothing on Home follows AISP Today.
    const home = screen.getByRole('region', { name: 'Command center' })
    for (const section of home.querySelectorAll('section')) {
      if (section === today || today.contains(section)) continue
      expect(today.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING).toBeFalsy()
    }
  })
})

/* --------------------------------------------------------- Experiment --- */

describe('Experiment trim', () => {
  it('drops the prose block and keeps one concise warning in the hero', async () => {
    scenario = namedScenario('settled-position', { now: Date.now() })
    render(<MemoryRouter><CommandExperiment /></MemoryRouter>)
    const hero = await screen.findByRole('region', { name: 'Bankroll position' })
    expect(screen.queryByText('Where the experiment stands')).toBeNull()
    expect(screen.queryByRole('region', { name: 'Where the experiment stands' })).toBeNull()
    const warnings = screen.getAllByText(/cannot establish skill or profitability|too small a sample|far too few/i)
    expect(warnings).toHaveLength(1)
    expect(hero).toContainElement(warnings[0])
    const progress = screen.getByRole('region', { name: 'Progress to the first review' })
    const chart = screen.getByRole('region', { name: 'Equity and drawdown' })
    const tape = screen.getByRole('region', { name: 'Settlement tape' })
    for (const [a, b] of [[hero, progress], [progress, chart], [chart, tape]]) {
      expect(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
  })
})

/* -------------------------------------------------------- Game Center --- */

// The producer's golden cells, scaled to 10,000 draws.
const CELLS = [[0, 0], [1, 0], [0, 1], [2, 1], [1, 2], [3, 0], [0, 3], [2, 2]].map(([away, home]) => ({ away, home, count: 1250 }))
const margin = (() => { const m = new Map<number, number>(); for (const c of CELLS) m.set(c.home - c.away, (m.get(c.home - c.away) ?? 0) + c.count); return [...m].sort((a, b) => a[0] - b[0]).map(([value, count]) => ({ value, count })) })()
const total = (() => { const m = new Map<number, number>(); for (const c of CELLS) m.set(c.home + c.away, (m.get(c.home + c.away) ?? 0) + c.count); return [...m].sort((a, b) => a[0] - b[0]).map(([value, count]) => ({ value, count })) })()
const masses = (win: number, loss: number, push: number) => ({ win, loss, push, void: 0, conditional: null, mcse: 0.004, fair_decimal: null, fair_american: null })

function storyPayload(overrides: Partial<Story> = {}): Story {
  return {
    schema_version: 'AISP_GAME_STORY_V1',
    game: { game_id: 'g1', sport: 'NFL', status: 'FINAL', starts_at: '2026-09-20T17:00:00Z',
      home: { abbreviation: 'KC', name: 'Kansas City Chiefs' }, away: { abbreviation: 'IND', name: 'Indianapolis Colts' },
      final: { home: 2, away: 1 }, final_note: 'Actual final score, shown after the game; it was not known when the forecast was made.' },
    channel: { channel: 'PAIRED_POPULATION', status: 'SUPPORTED', authority: 'PAPER_RESEARCH_EVIDENCE',
      binding: { sport: 'NFL', gameId: 'g1', forecastId: 'f', modelId: 'NFL_M1_XGBOOST', configurationId: 'c', packageSha256: 'a'.repeat(64), populationSha256: 'b'.repeat(64), drawCount: 10000 } },
    population: { draw_count: 10000, population_sha256: 'b'.repeat(64), cells: CELLS, mode: CELLS[0],
      likely_region: { cells: CELLS.slice(0, 2).map(({ away, home }) => ({ away, home })), count: 2500 }, margin, total },
    model_vs_market: { model_home: 0.375, model_away: 0.375, model_tie: 0.25, model_home_two_way: 0.5, model_source: 'VERIFIED_POPULATION_FORECAST',
      market_home_no_vig: 0.55, market_is_current: true, market_books: 2 },
    lines: {
      // Server masses for HOME −1 and total 3 on the golden cells.
      spread: { line: -1, line_side: 'HOME', sides: { HOME: masses(0.125, 0.625, 0.25), AWAY: masses(0.625, 0.125, 0.25) } },
      total: { line: 3, line_side: null, sides: { OVER: masses(0.125, 0.375, 0.5), UNDER: masses(0.375, 0.125, 0.5) } },
      from: 'CURRENT_QUOTES',
    },
    market: { price_state: 'CURRENT_PRICE', consensus_no_vig_home: 0.55, consensus_is_current: true,
      best: { HOME: null, AWAY: null }, books: 2, freshest_observed_at: new Date().toISOString(),
      quotes: [quote('MONEYLINE', { HOME: -120, AWAY: 100 }, true), quote('MONEYLINE', { HOME: -130, AWAY: 110 }, false, 'Bovada')] },
    facts: [{ kind: 'DISAGREEMENT', text: 'Largest current model/market difference: home moneyline at DraftKings.' },
      { kind: 'MISSING', text: 'Not quoted: spread.' }],
    provider_calls_made: 0,
    ...overrides,
  }
}

function quote(market: string, prices: Record<string, number>, current: boolean, book = 'DraftKings', line: number | null = null): AssessedQuote {
  const sides = Object.fromEntries(Object.entries(prices).map(([k, a]) => [k, {
    american: a, decimal: a > 0 ? 1 + a / 100 : 1 - 100 / a, no_vig: 0.5, model: null, edge: null, expected_return: null,
    edge_reason: current ? null : 'QUOTE_NOT_CURRENT',
  }]))
  return {
    market, line, sportsbook: `${book} (SportsGameOdds, research)`, sportsbook_key: book.toLowerCase(), provider: 'SportsGameOdds',
    classification: 'RESEARCH', observed_at: new Date(Date.now() - (current ? 20 : 900) * 60_000).toISOString(),
    age_minutes: current ? 20 : 900, fresh: current, state: current ? 'CURRENT' : 'STORED_REFERENCE_ONLY', sides,
  }
}

describe('Game Center', () => {
  it('reconciles chart regions with the server masses and keeps the contract orientation', () => {
    const s = storyPayload()
    // Spread attached to HOME: HOME −1 wins when margin − 1 > 0, pushes at margin 1.
    expect(regionOf('margin', 'HOME', -1, 2)).toBe('WIN')
    expect(regionOf('margin', 'HOME', -1, 1)).toBe('PUSH')
    expect(regionOf('margin', 'AWAY', -1, 1)).toBe('PUSH')
    expect(regionOf('margin', 'AWAY', -1, 0)).toBe('WIN')
    expect(regionOf('total', 'OVER', 2.5, 3)).toBe('WIN')
    expect(regionOf('total', 'UNDER', 2.5, 2)).toBe('WIN')
    for (const side of ['HOME', 'AWAY'] as const) {
      const m = regionMasses(s.population!.margin, 10000, 'margin', side, -1)
      const server = s.lines.spread!.sides[side]
      expect([m.WIN, m.LOSS, m.PUSH]).toEqual([server.win, server.loss, server.push])
    }
    for (const side of ['OVER', 'UNDER'] as const) {
      const m = regionMasses(s.population!.total, 10000, 'total', side, 3)
      const server = s.lines.total!.sides[side]
      expect([m.WIN, m.LOSS, m.PUSH]).toEqual([server.win, server.loss, server.push])
    }
    // Half-point lines never push.
    expect(regionMasses(s.population!.total, 10000, 'total', 'OVER', 2.5).PUSH).toBe(0)
  })

  it('the distribution chart is one tab stop and arrow keys announce each value', async () => {
    story = storyPayload()
    render(<MemoryRouter><GameStory gameId="g1" /></MemoryRouter>)
    const dist = await screen.findByRole('region', { name: 'Margin and total distribution' })
    const chart = within(dist).getByRole('application')
    expect(chart).toHaveAttribute('tabindex', '0')
    // No bar is a tab stop of its own.
    expect(dist.querySelectorAll('rect[tabindex]')).toHaveLength(0)
    const live = dist.querySelector('[aria-live="polite"]') as HTMLElement
    fireEvent.focus(chart)
    const first = live.textContent
    expect(first).toMatch(/^Total \d+: \d+ draws/)
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(live.textContent).not.toBe(first)
    fireEvent.keyDown(chart, { key: 'Home' })
    expect(live.textContent).toMatch(/^Total 0: /)
    expect(dist.querySelectorAll('rect.is-focused')).toHaveLength(1)
    fireEvent.blur(chart)
    expect(live.textContent).toBe('')
  })

  it('draws the heatmap, distribution, ladder and facts from the payload', async () => {
    story = storyPayload()
    render(<MemoryRouter><GameStory gameId="g1" /></MemoryRouter>)
    const heat = await screen.findByRole('region', { name: 'Score population heatmap' })
    expect(heat.querySelectorAll('svg rect[fill]')).toHaveLength(CELLS.length)
    expect(heat).toHaveTextContent('Most likely: IND 0 – KC 0 (12.50%)')
    expect(heat).toHaveTextContent('Final: IND 1 – KC 2')
    expect(heat).toHaveTextContent('not known when the forecast was made')
    const app = within(heat).getByRole('application')
    fireEvent.keyDown(app, { key: 'ArrowRight' })
    expect(heat).toHaveTextContent('Selected: IND 1 – KC 0: 1,250 of 10,000 draws (12.50%)')

    const dist = screen.getByRole('region', { name: 'Margin and total distribution' })
    expect(dist).toHaveTextContent('Win 12.5% · Loss 37.5% · Push 50.0%')
    fireEvent.click(within(dist).getByRole('button', { name: 'Margin' }))
    expect(dist).toHaveTextContent('Win 12.5% · Loss 62.5% · Push 25.0%')
    expect(within(dist).getAllByRole('button', { name: 'Margin' })[0]).toHaveAttribute('aria-pressed', 'true')
    const pushBars = dist.querySelectorAll('rect.gs-dist--push')
    expect(pushBars).toHaveLength(1)
    // The same fact through the accessible table view (bars are no longer individual tab stops).
    const pushRow = within(dist).getAllByRole('row').find((r) => /^1\s/.test(r.textContent ?? '') || r.textContent?.startsWith('1'))
    expect(pushRow).toHaveTextContent('25.00%')
    expect(pushRow).toHaveTextContent('Push')

    const mvm = screen.getByRole('region', { name: 'Model versus market' })
    expect(mvm).toHaveTextContent('KC model 50.0% · market 55.0% · difference −5.0 pts')
    expect(mvm).toHaveTextContent('model tie mass 25.0%')

    const ladder = screen.getByRole('region', { name: 'Sportsbook comparison' })
    expect(within(ladder).getAllByRole('img')[0]).toBeInTheDocument()
    expect(ladder).toHaveTextContent('KC best current -120 (DraftKings)')
    expect(ladder.querySelectorAll('circle.is-stale')).toHaveLength(2)

    const facts = screen.getByRole('region', { name: 'What matters' })
    expect(facts).toHaveTextContent('Not quoted: spread.')
  })

  it('shows precise unavailable states instead of empty charts', async () => {
    story = storyPayload({
      channel: { channel: 'PAIRED_POPULATION', status: 'UNSUPPORTED', error: { code: 'POPULATION_SOURCE_UNREADABLE', message: 'retained population file is unreadable' } },
      population: null, market: null,
      model_vs_market: { model_home: null, model_away: null, model_tie: null, model_home_two_way: null, model_source: null, market_home_no_vig: null, market_is_current: false, market_books: 0 },
      lines: { spread: null, total: null, from: null },
    })
    render(<MemoryRouter><GameStory gameId="g1" /></MemoryRouter>)
    expect(await screen.findByRole('region', { name: 'Score population unavailable' })).toHaveTextContent('retained population file is unreadable')
    expect(screen.getByRole('region', { name: 'Margin and total distribution unavailable' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Model vs market unavailable' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Sportsbook comparison unavailable' })).toHaveTextContent('No moneyline quote is stored')
    expect(document.querySelectorAll('svg')).toHaveLength(0)
  })

  it('research sports never show a sportsbook comparison', async () => {
    const s = storyPayload()
    story = { ...s, game: { ...s.game, sport: 'CFB', final: null }, market: null, lines: { spread: null, total: null, from: null } }
    render(<MemoryRouter><GameStory gameId="g1" /></MemoryRouter>)
    expect(await screen.findByRole('region', { name: 'Sportsbook comparison unavailable' })).toHaveTextContent('Research sport')
  })
})

/* ------------------------------------------------ All Markets / Card --- */

function marketsData(): { games: Parameters<typeof AllMarkets>[0]['games']; data: DailyAssessments } {
  const clock = { now: Date.now() }
  const g = { ...priced(clock, fixtureGame(clock, '1', { startIn: 120 })), game_id: 'g-guardians' }
  g.home = { abbreviation: 'CLE', name: 'Cleveland Guardians' }
  g.away = { abbreviation: 'DET', name: 'Detroit Tigers' }
  const assessment = {
    game_id: 'g-guardians', sport: 'MLB' as const,
    binding: { channel: 'PAIRED_POPULATION' as const, status: 'SUPPORTED' as const },
    quotes: [quote('MONEYLINE', { HOME: -140, AWAY: 120 }, true), quote('MONEYLINE', { HOME: -160, AWAY: 135 }, false, 'Bovada'),
      quote('TOTAL', { OVER: -105 }, true, 'DraftKings', 8.5)],
    parlay_lab: [],
  }
  const data = {
    schema_version: 'AISP_DAILY_ASSESSMENTS_V1', date: '', generated_at: '', moneyline_tie_settlement: 'PUSH',
    games: [assessment], research: [],
    parlay_policy: { sportsbook_parlay_quotes_stored: 0, unavailable_message: '', creates_decisions: false, creates_positions: false },
    cross_game: { status: 'UNSUPPORTED', code: 'CROSS_GAME_CALIBRATION_NOT_VALIDATED', message: '' },
    player_props: { contract: 'AISP_PLAYER_PROP_QUOTE_V1', required_identity: [], status: 'NOT_STORED', quotes: [],
      assessment: 'Sportsbook market — no AISP assessment', message: 'No player-prop quotes are stored. AISP collects only moneyline, spread and total markets today.' },
    provider_calls_made: 0,
  } as DailyAssessments
  return { games: [{ gameId: 'g-guardians', game: g, assessment: assessment as never }], data }
}

describe('All Markets and My Card', () => {
  it('adds a current moneyline to My Card without any API call, and never a stale one', () => {
    const { games, data } = marketsData()
    render(<MemoryRouter><AllMarkets games={games} data={data} now={Date.now()} onOpenLab={() => {}} /><MyCard now={Date.now()} /></MemoryRouter>)
    const add = screen.getByRole('button', { name: /Add Cleveland Guardians to win -140 at DraftKings to My Card/ })
    const stale = screen.getByRole('button', { name: /Cleveland Guardians to win: not selectable \(price not current\)/ })
    expect(stale).toBeDisabled()
    // A one-sided total: the missing Under is shown as not quoted, not invented.
    expect(screen.getByRole('button', { name: /under not quoted: not selectable \(not quoted\)/ })).toBeDisabled()
    expect(screen.getByRole('region', { name: 'Spread markets' })).toHaveTextContent('Not quoted — nothing to select.')
    expect(screen.getByTestId('player-props-unavailable')).toHaveTextContent('No player-prop quotes are stored')
    // A Player Props section exists only when real stored quotes exist.
    expect(screen.queryByRole('region', { name: 'Player props' })).toBeNull()

    const before = requests.length
    fireEvent.click(add)
    fireEvent.click(stale)
    expect(requests.length).toBe(before)
    const card = screen.getByRole('region', { name: 'My Card' })
    expect(card).toHaveTextContent(MY_CARD_LABEL)
    expect(card).toHaveTextContent('Saved in this browser only')
    const picks = within(card).getAllByRole('listitem')
    expect(picks).toHaveLength(1)
    expect(picks[0]).toHaveTextContent('Cleveland Guardians to win -140')
    expect(picks[0]).toHaveTextContent('DraftKings via SportsGameOdds')
    expect(picks[0]).toHaveTextContent('Returns $17.14')
    // Stored only in this browser, under the card's own key.
    const stored = JSON.parse(window.localStorage.getItem(MY_CARD_KEY) ?? '[]')
    expect(stored).toHaveLength(1)
    expect(stored[0]).toMatchObject({ game_id: 'g-guardians', market: 'MONEYLINE', outcome: 'HOME', american: -140, sportsbook: 'DraftKings', freshness_at_add: 'CURRENT' })
    // The chosen outcome is now marked and cannot be added twice.
    expect(add).toBeDisabled()
    expect(add).toHaveTextContent('On your card')
    expect(requests.every((r) => r.method === 'GET')).toBe(true)
  })

  it('the store refuses stale quotes and never combines picks', () => {
    const { games } = marketsData()
    const [current, stale] = games[0].assessment!.quotes
    const game = games[0].game
    expect(myCard.add(pickFrom(game, stale, 'HOME')!, { current: false })).toBeNull()
    const old = { ...pickFrom(game, current, 'HOME')!, quote_observed_at: new Date(Date.now() - 300 * 60_000).toISOString() }
    expect(myCard.add(old, { current: true })).toBeNull()
    myCard.add(pickFrom(game, current, 'HOME')!, { current: true })
    myCard.add(pickFrom(game, current, 'AWAY')!, { current: true })
    expect(myCard.add(pickFrom(game, current, 'AWAY')!, { current: true })).toBeNull()  // no duplicate
    expect(myCard.list()).toHaveLength(2)
    expect(pickReturn({ stake: 10, decimal: 2.2 })).toEqual({ total: 22, profit: 12 })
    render(<MemoryRouter><MyCard now={Date.now()} /></MemoryRouter>)
    const card = screen.getByRole('region', { name: 'My Card' })
    expect(card).toHaveTextContent('Picks are not combined into a parlay')
    // No probability or combined price anywhere on the card.
    expect(card.textContent).not.toMatch(/\d+(\.\d+)?%/)
    expect(card.textContent).not.toMatch(/parlay price|combined odds|joint/i)
    act(() => { myCard.setStake(myCard.list()[0].id, 25) })
    expect(within(card).getAllByRole('listitem')[0]).toHaveTextContent(/Returns \$42\.86/)
    act(() => { myCard.clear() })
    expect(card).toHaveTextContent('No picks yet')
  })

  it('My Card cannot reach the official experiment', async () => {
    const { games } = marketsData()
    const before = requests.length
    myCard.add(pickFrom(games[0].game, games[0].assessment!.quotes[0], 'HOME')!, { current: true })
    render(<MemoryRouter><MyCard now={Date.now()} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: /Remove/ }))
    await waitFor(() => expect(screen.getByRole('region', { name: 'My Card' })).toHaveTextContent('No picks yet'))
    expect(requests.slice(before)).toEqual([])
  })
})

/* --------------------------------------------------------- Games sort --- */

const slate = (key: string, start: number, home: number | null, status: SlateGame['priceStatus'], edge: number | null) => ({
  key, gameId: key, startsAt: new Date(Date.UTC(2026, 8, 16, start)).toISOString(), homeWin: home, priceStatus: status, edgePoints: edge,
}) as unknown as SlateGame

describe('Games & Forecasts sort', () => {
  const games = [slate('a', 20, null, 'NOT_COLLECTED', null), slate('b', 18, 0.6, 'STORED_REFERENCE', 9), slate('c', 22, 0.55, 'CURRENT', -3), slate('d', 19, 0.5, 'CURRENT', 1)]
  it('sorts by kickoff, forecast status and largest current gap', () => {
    const none = () => null
    expect(sortSlate(games, 'KICKOFF', none).map((g) => g.key)).toEqual(['b', 'd', 'a', 'c'])
    expect(sortSlate(games, 'FORECAST', none).map((g) => g.key)).toEqual(['d', 'c', 'b', 'a'])
    // A stale price never ranks as a gap, however large.
    expect(currentGap(games[1], null)).toBeNull()
    expect(sortSlate(games, 'GAP', none).map((g) => g.key)).toEqual(['c', 'd', 'b', 'a'])
  })
})
