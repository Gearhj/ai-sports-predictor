import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ user: null as null | { id: string; role: string; display_name: string } }))
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => auth }))

const api = vi.hoisted(() => ({
  me: vi.fn(), marketBoard: vi.fn(), account: vi.fn(), slipAdd: vi.fn(), slipUpdate: vi.fn(),
  place: vi.fn(), slipRemove: vi.fn(),
}))
vi.mock('../../services/houseDivided', async (original) => {
  const actual = await original<typeof import('../../services/houseDivided')>()
  return { ...actual, houseDivided: { ...actual.houseDivided, ...api } }
})

import { CompetitionRefusal } from '../../services/houseDivided'
import { MakePicks, countdown, nickname, odds } from './MakePicks'

// Isolated fixtures: never persisted anywhere.
const soon = new Date(Date.now() + 3 * 3600_000).toISOString()
const q = (id: string, type: 'MONEYLINE' | 'SPREAD' | 'TOTAL', american: number, book = 'DraftKings', line: number | null = null) => ({
  quote_id: id, market_type: type, market_label: type, selection: 'x', outcome_key: 'x', line,
  sportsbook: book.toLowerCase(), sportsbook_name: book, american_odds: american, decimal_odds: 2,
  observed_at: new Date().toISOString(), age_minutes: 3,
})
const opt = (side: string, quotes: ReturnType<typeof q>[], line: number | null = null) =>
  ({ side, selection: side, line, quotes, best: quotes[0], best_of: quotes.length })
const game = {
  game_id: 'g1', starts_at: soon, cutoff: soon, status: 'SCHEDULED', locked: false,
  home: { name: 'Cincinnati Reds', abbreviation: 'CIN' }, away: { name: 'Chicago Cubs', abbreviation: 'CHC' },
  home_score: null, away_score: null, human_lock_at: soon, lock_basis: 'CUTOFF', lock_reason: null,
  state: { code: 'OPEN', label: 'Open for picks', detail: null }, expected_prices_at: null,
  last_update: new Date().toISOString(), books_available: 2, best_book: 'DraftKings', spread_label: 'Run line',
  moneyline: { away: opt('away', [q('ml-a', 'MONEYLINE', 125), q('ml-a2', 'MONEYLINE', 120, 'FanDuel')]), home: opt('home', [q('ml-h', 'MONEYLINE', -140)]) },
  spreads: [{ line: 1.5, away: opt('away', [q('rl-a', 'SPREAD', -165, 'DraftKings', 1.5)], 1.5), home: opt('home', [q('rl-h', 'SPREAD', 140, 'DraftKings', -1.5)], -1.5) }],
  totals: [
    { line: 8.5, over: opt('over', [q('t-o', 'TOTAL', -110, 'DraftKings', 8.5)], 8.5), under: opt('under', [q('t-u', 'TOTAL', -110, 'DraftKings', 8.5)], 8.5) },
    { line: 9, over: opt('over', [q('t-o9', 'TOTAL', 105, 'DraftKings', 9)], 9), under: null },
  ],
  props: { status: 'UNAVAILABLE', message: 'Player props are not available yet for this game.', reason: 'r',
    groups: [{ label: 'Pitcher strikeouts', status: 'UNAVAILABLE' }, { label: 'Hits', status: 'UNAVAILABLE' }], stored_current_quotes: 0 },
}
const waiting = {
  ...game, game_id: 'g2', away: { name: 'Boston Red Sox', abbreviation: 'BOS' }, home: { name: 'Tampa Bay Rays', abbreviation: 'TB' },
  state: { code: 'EXPECTED', label: 'Current prices expected near the decision window', detail: null },
  expected_prices_at: new Date(Date.now() + 3600_000).toISOString(), books_available: 0, best_book: null,
  moneyline: { away: null, home: null }, spreads: [], totals: [],
}
const board = {
  sport: 'MLB', status: 'STARTED', date: '2026-09-18', dates: [{ date: '2026-09-18', games: 2 }], prop_groups: [],
  sports: [{ sport: 'MLB', status: 'STARTED', reason: null }, { sport: 'NFL', status: 'STARTED', reason: null },
    { sport: 'NBA', status: 'NOT_STARTED', reason: 'x' }, { sport: 'CFB', status: 'NOT_STARTED', reason: 'x' }],
  games: [game, waiting],
}
const draft = {
  wager_id: 'w1', participant: 'SCOTTY', sport: 'MLB', source: 'HUMAN', status: 'DRAFT', game_id: 'g1',
  market_type: 'MONEYLINE', market_label: 'Moneyline', selection: 'Chicago Cubs', line: null, sportsbook: 'draftkings',
  american_odds: 125, decimal_odds: 2.25, quote_id: 'ml-a', quote_observed_at: new Date().toISOString(), stake: 25,
  submitted_at: null, created_at: null, entered_by: 'admin', entered_on_behalf: true, source_decision_id: null,
  model_version: null, decision_recommended_at: null, staking_detail: null, unfunded_reason: null, result: null,
  profit_loss: null, payout: null, settled_at: null, settlement_note: null, final_score: null, matchup: 'CHC @ CIN',
  away: null, home: null, starts_at: soon, human_lock_at: soon, lock_basis: 'CUTOFF', quote_age_minutes: 3,
  potential_profit: 31.25, potential_payout: 56.25, push_possible: false,
  refund_rule: 'If the game is cancelled, the stake is refunded.', confirmation_id: null,
  available_before: 1000, available_after: 975,
}
const account = (drafts: unknown[] = [draft]) => ({
  participant: 'SCOTTY', display_name: 'Scotty', color: '#e0772f', sport: 'MLB', started_at: soon,
  bankroll: 1000, available: 1000, open_exposure: 0, drafts, open_picks: [],
})
const admin = { can_enter_for: ['SCOTTY', 'RED_LOU'], is_admin: true, drafts: [], participants: [
  { key: 'SCOTTY', display_name: 'Scotty', color: '#e0772f', linked_to_you: false },
  { key: 'RED_LOU', display_name: 'Red Lou — Mom', color: '#c9304a', linked_to_you: false },
] }

function show(sport: 'MLB' | null = 'MLB', onSport = vi.fn()) {
  render(<MemoryRouter><MakePicks sport={sport} onSport={onSport} onChanged={() => undefined} /></MemoryRouter>)
  return onSport
}

describe('Make Picks lobby', () => {
  beforeEach(() => {
    api.marketBoard.mockResolvedValue(board)
    api.me.mockResolvedValue(admin)
    api.account.mockResolvedValue(account())
    api.slipAdd.mockResolvedValue({ wager_id: 'w2', status: 'DRAFT' })
  })
  afterEach(() => { cleanup(); vi.clearAllMocks(); auth.user = null })

  it('prints odds the way a sportsbook does', async () => {
    auth.user = { id: 'a', role: 'ADMIN', display_name: 'admin' }
    show()
    expect(await screen.findByRole('button', { name: 'Cubs +125' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Reds −140' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Cubs +1.5 −165' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Over 8.5 −110' })).toBeTruthy()
    expect(nickname({ name: 'Boston Red Sox', abbreviation: 'BOS' })).toBe('Red Sox')
    expect(odds(-140)).toBe('−140')
  })

  it('asks a signed-out viewer to sign in and keeps every price unselectable', async () => {
    show()
    expect(await screen.findAllByRole('link', { name: 'Sign in to make picks' })).not.toHaveLength(0)
    expect(await screen.findByRole('button', { name: 'Cubs +125' })).toBeDisabled()
    expect(api.me).not.toHaveBeenCalled()
    expect(screen.getByText('Current prices expected near the decision window')).toBeTruthy()
    expect(screen.getByText(/Next collection for this game from about/)).toBeTruthy()
  })

  it('lets an unlinked member browse but not add selections', async () => {
    auth.user = { id: 'm', role: 'MEMBER', display_name: 'Someone' }
    api.me.mockResolvedValue({ can_enter_for: [], is_admin: false, drafts: [], participants: [] })
    show()
    expect(await screen.findByText('Browsing only')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Cubs +125' })).toBeDisabled()
    expect(screen.queryByLabelText('Pick Slip')).toBeNull()
  })

  it('shows a linked Scotty only his own slip, with no participant chooser', async () => {
    auth.user = { id: 's', role: 'MEMBER', display_name: 'Elvis' }
    api.me.mockResolvedValue({ can_enter_for: ['SCOTTY'], is_admin: false, drafts: [], participants: [admin.participants[0]] })
    show()
    expect(await screen.findByText('Picking as')).toBeTruthy()
    expect(screen.queryByLabelText('Picking for')).toBeNull()
    await waitFor(() => expect(api.account).toHaveBeenCalledWith('SCOTTY', 'MLB'))
    expect(api.account).not.toHaveBeenCalledWith('RED_LOU', expect.anything())
  })

  it('lets an administrator pick for Scotty or Red Lou and adds the best price on a tap', async () => {
    auth.user = { id: 'a', role: 'ADMIN', display_name: 'admin' }
    api.account.mockResolvedValue(account([]))
    show()
    const chooser = await screen.findByLabelText('Picking for')
    expect((chooser as HTMLSelectElement).value).toBe('SCOTTY')
    fireEvent.click(await screen.findByRole('button', { name: 'Reds −140' }))
    await waitFor(() => expect(api.slipAdd).toHaveBeenCalledWith('SCOTTY', 'ml-h'))
    fireEvent.change(chooser, { target: { value: 'RED_LOU' } })
    await waitFor(() => expect(api.account).toHaveBeenCalledWith('RED_LOU', 'MLB'))
  })

  it('shops books and offers More wagers', async () => {
    auth.user = { id: 'a', role: 'ADMIN', display_name: 'admin' }
    show()
    fireEvent.click(await screen.findByText('Shop books (2)'))
    fireEvent.click(screen.getByRole('button', { name: 'Cubs +120 at FanDuel' }))
    await waitFor(() => expect(api.slipUpdate).toHaveBeenCalledWith('w1', 'SCOTTY', 'ml-a2', '25'))
    expect(screen.getByText('More wagers (1)')).toBeTruthy()
  })

  it('fills the slip with server figures and requires a second confirmation when the price moves', async () => {
    auth.user = { id: 'a', role: 'ADMIN', display_name: 'admin' }
    api.place.mockRejectedValueOnce(new CompetitionRefusal('moved', 409, 'PRICE_CHANGED', q('ml-new', 'MONEYLINE', 110)))
    show()
    const slip = (await screen.findAllByLabelText('Pick Slip items'))[0]
    expect(within(slip).getByText('$31.25')).toBeTruthy()
    expect(within(slip).getByText('$56.25')).toBeTruthy()
    expect(within(slip).getByText('$975.00')).toBeTruthy()
    fireEvent.click(within(slip).getByRole('button', { name: /Place Virtual Wager/ }))
    expect(await within(slip).findByText('The price changed')).toBeTruthy()
    expect(api.place).toHaveBeenCalledWith('w1', '25.00', 'ml-a')
    await waitFor(() => expect(within(slip).getByRole('button', { name: /Place Virtual Wager/ })).toBeDisabled())
    fireEvent.click(within(slip).getByRole('button', { name: 'Use updated price' }))
    await waitFor(() => expect(api.slipUpdate).toHaveBeenCalledWith('w1', 'SCOTTY', 'ml-new', '25'))
  })

  it('shows the player-prop groups and says plainly that props are not available', async () => {
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Player Props' }))
    expect(screen.getAllByText('Player props are not available yet for this game.').length).toBe(2)
    expect(screen.getAllByText('Pitcher strikeouts').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'Cubs +125' })).toBeNull()
  })

  it('opens on the server-chosen sport when none is given', async () => {
    const onSport = show(null)
    await waitFor(() => expect(onSport).toHaveBeenCalledWith('MLB'))
    expect(api.marketBoard).toHaveBeenCalledWith(null, null)
  })

  it('counts down to the human lock', () => {
    const now = Date.parse('2026-09-18T12:00:00Z')
    expect(countdown('2026-09-18T14:05:00Z', now)).toBe('Locks in 2h 5m')
    expect(countdown('2026-09-18T11:00:00Z', now)).toBe('Locked')
  })
})
