import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
import { MakePicks, countdown } from './MakePicks'

const soon = new Date(Date.now() + 3 * 3600_000).toISOString()
const quote = (id: string, american: number, book = 'DraftKings') => ({
  quote_id: id, market_type: 'MONEYLINE' as const, market_label: 'Moneyline', selection: 'Cubs', outcome_key: 'away',
  line: null, sportsbook: book.toLowerCase(), sportsbook_name: book, american_odds: american,
  decimal_odds: 2, observed_at: new Date().toISOString(), age_minutes: 3,
})
const board = {
  sport: 'MLB', status: 'STARTED', date: '2026-09-18', dates: [{ date: '2026-09-18', games: 1 }],
  games: [{
    game_id: 'g1', starts_at: soon, cutoff: soon, status: 'SCHEDULED', locked: false,
    home: { name: 'Cincinnati Reds', abbreviation: 'CIN' }, away: { name: 'Chicago Cubs', abbreviation: 'CHC' },
    home_score: null, away_score: null, human_lock_at: soon, lock_basis: 'CUTOFF', lock_reason: null,
    last_update: new Date().toISOString(),
    markets: [
      { market_type: 'MONEYLINE', label: 'Moneyline', status: 'OPEN', reason: null, options: [
        { side: 'away', selection: 'Chicago Cubs', line: null, quotes: [quote('q1', 105), quote('q2', 100, 'FanDuel')], best: quote('q1', 105), best_of: 2 },
      ] },
      { market_type: 'SPREAD', label: 'Run line', status: 'AWAITING', reason: 'Awaiting current markets.', options: [] },
      { market_type: 'TOTAL', label: 'Total', status: 'UNAVAILABLE', reason: 'Price unavailable: the latest prices are older than 240 minutes.', options: [] },
    ],
  }],
}
const draft = {
  wager_id: 'w1', participant: 'SCOTTY', sport: 'MLB', source: 'HUMAN', status: 'DRAFT', game_id: 'g1',
  market_type: 'MONEYLINE', market_label: 'Moneyline', selection: 'Chicago Cubs', line: null, sportsbook: 'draftkings',
  american_odds: 105, decimal_odds: 2.05, quote_id: 'q1', quote_observed_at: null, stake: 25, submitted_at: null,
  created_at: null, entered_by: 'admin', entered_on_behalf: true, source_decision_id: null, model_version: null,
  decision_recommended_at: null, staking_detail: null, unfunded_reason: null, result: null, profit_loss: null,
  payout: null, settled_at: null, settlement_note: null, final_score: null, matchup: 'CHC @ CIN', away: null,
  home: null, starts_at: soon, human_lock_at: soon, lock_basis: 'CUTOFF', quote_age_minutes: 3,
  potential_profit: 26.25, potential_payout: 51.25, push_possible: false,
  refund_rule: 'If the game is cancelled, the stake is refunded.', confirmation_id: null,
  available_before: 1000, available_after: 975,
}
const account = (drafts: unknown[] = [draft]) => ({
  participant: 'SCOTTY', display_name: 'Scotty', color: '#e0772f', sport: 'MLB', started_at: soon,
  bankroll: 1000, available: 1000, open_exposure: 0, drafts, open_picks: [],
})
const sports = [{ sport: 'MLB' as const, status: 'STARTED' }, { sport: 'NFL' as const, status: 'STARTED' }]

function show() {
  return render(<MemoryRouter><MakePicks sport="MLB" sports={sports} onSport={() => undefined} onChanged={() => undefined} /></MemoryRouter>)
}

describe('Make Picks', () => {
  beforeEach(() => {
    api.marketBoard.mockResolvedValue(board)
    api.me.mockResolvedValue({ can_enter_for: ['SCOTTY', 'RED_LOU'], is_admin: true, drafts: [], participants: [
      { key: 'SCOTTY', display_name: 'Scotty', color: '#e0772f', linked_to_you: false },
      { key: 'RED_LOU', display_name: 'Red Lou — Mom', color: '#c9304a', linked_to_you: false },
    ] })
    api.account.mockResolvedValue(account())
  })
  afterEach(() => { cleanup(); vi.clearAllMocks(); auth.user = null })

  it('asks a signed-out viewer to sign in instead of hiding the workflow', async () => {
    show()
    expect(await screen.findAllByRole('link', { name: 'Sign in to make picks' })).not.toHaveLength(0)
    const tile = await screen.findByRole('button', { name: /CHC/ })
    expect(tile).toBeDisabled()
    expect(screen.getByText('Awaiting current markets.')).toBeTruthy()
    expect(screen.getByText(/older than 240 minutes/)).toBeTruthy()
    expect(api.me).not.toHaveBeenCalled()
  })

  it('lets an administrator choose whom they are picking for, and shows best price and shop books', async () => {
    auth.user = { id: 'a', role: 'ADMIN', display_name: 'admin' }
    show()
    const select = await screen.findByLabelText('Picking for')
    expect((select as HTMLSelectElement).value).toBe('SCOTTY')
    expect(screen.getByText('Best current price')).toBeTruthy()
    expect(screen.getByText('Shop books (2)')).toBeTruthy()
    fireEvent.change(select, { target: { value: 'RED_LOU' } })
    await waitFor(() => expect(api.account).toHaveBeenCalledWith('RED_LOU', 'MLB'))
  })

  it('shows a moved price and requires a fresh confirmation before placing', async () => {
    auth.user = { id: 'a', role: 'ADMIN', display_name: 'admin' }
    api.place.mockRejectedValueOnce(new CompetitionRefusal('The price moved', 409, 'PRICE_CHANGED', quote('q9', -102)))
    show()
    const place = (await screen.findAllByRole('button', { name: 'Place Virtual Wager' }))[0]
    expect(screen.getAllByText('$51.25').length).toBeGreaterThan(0)
    fireEvent.click(place)
    expect((await screen.findAllByText('Price moved')).length).toBeGreaterThan(0)
    expect(api.place).toHaveBeenCalledWith('w1', '25.00', 'q1')
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Place Virtual Wager' })[0]).toBeDisabled())
    fireEvent.click(screen.getAllByRole('button', { name: 'Accept new price' })[0])
    await waitFor(() => expect(api.slipUpdate).toHaveBeenCalledWith('w1', 'SCOTTY', 'q9', '25'))
  })

  it('counts down to the human lock', () => {
    const now = Date.parse('2026-09-18T12:00:00Z')
    expect(countdown('2026-09-18T14:05:00Z', now)).toBe('Locks in 2h 5m')
    expect(countdown('2026-09-18T11:00:00Z', now)).toBe('Locked')
  })
})
