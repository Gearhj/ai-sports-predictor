/**
 * The $1,000 Experiment page with the served five-account ledger (NHL funded 2026-10-16):
 * the NHL account card, its scope button and its own scope, and a Combined view that
 * starts from all five contributions, each counted once.
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ledger as fourAccounts } from '../../test/homeFixtures'
import type { Ledger } from '../../services/commandCenter'

const clock = { now: Date.parse('2026-10-17T02:00:00Z') }

function fiveAccounts(): Ledger {
  const base = fourAccounts(clock)
  const nhl: Ledger['sports'][number] = {
    sport: 'NHL', label: 'NHL', account: { funded: true, label: '$1,003.20 virtual bankroll', effective_on: '2026-10-16' },
    starting_capital: 1000, realized_bankroll: 1003.2, profit_loss: 3.2, open_exposure: 0, available_capital: 1003.2,
    open_count: 0, settled_count: 1, record: { WIN: 1, LOSS: 0, PUSH: 0 },
    positions: [{
      position_id: 'nhl-1', game_id: '7a242ab1-ee67-481b-9156-c369cd806687', matchup: 'PIT @ COL', starts_at: '2026-10-17T01:00:00Z',
      market: 'MONEYLINE', selection: 'COL ML',
      entry: { american: -150, decimal: 1.667, sportsbook: 'DraftKings', sportsbook_key: 'draftkings', provider: 'The Odds API', classification: 'GOVERNED', quote_observed_at: '2026-10-17T00:10:00Z' },
      stake: 4.8, placed_at: '2026-10-17T00:40:00Z', status: 'SETTLED', result: 'WIN', profit_loss: 3.2, settled_at: '2026-10-17T03:40:00Z',
      experiment: { id: '658e9bb2', name: 'ASP NHL RESEARCH PAPER BANKROLL — 2026-27 V1' },
    }],
    series: [{ at: '2026-10-16T04:00:00Z', bankroll: 1000, kind: 'FUNDING', position_id: null },
      { at: '2026-10-17T03:40:00Z', bankroll: 1003.2, kind: 'SETTLEMENT', position_id: 'nhl-1', change: 3.2 }],
    reconciled: true,
  }
  return {
    ...base,
    sports: [...base.sports, nhl],
    totals: { ...base.totals, starting_capital: 5000, realized_bankroll: base.totals.realized_bankroll + 1003.2,
      profit_loss: base.totals.profit_loss + 3.2, available_capital: base.totals.available_capital + 1003.2,
      settled_count: base.totals.settled_count + 1, record: { ...base.totals.record, WIN: base.totals.record.WIN + 1 } },
  }
}

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string) => {
      if (path.startsWith('/command-center/ledger')) return Promise.resolve(fiveAccounts())
      if (path.startsWith('/sports/readiness')) {
        return Promise.resolve({ schema_version: 'AISP_SPORT_READINESS_V1', generated_at: '', authority: '', sports: [{
          sport: 'NHL', label: 'NHL', next_game: { matchup: 'NYR @ BOS', starts_label: 'Sat Oct 17, 7:00 PM ET' },
          decision: { state: 'WINDOW_NOT_OPEN', label: 'Decision window Sat Oct 17, 6:30 PM ET – 6:45 PM ET' },
          pipeline: { ready: true, blocker: null, could_qualify: null } }] })
      }
      return Promise.reject(new Error(`unexpected ${path}`))
    }),
  }
})

import { CommandExperiment } from './CommandExperiment'

afterEach(() => cleanup())

describe('Experiment with the NHL account', () => {
  it('lists the NHL account beside the other four and scopes to it', async () => {
    render(<MemoryRouter><CommandExperiment /></MemoryRouter>)
    const card = await screen.findByRole('article', { name: 'NHL account' })
    expect(within(card).getByText('NHL')).toBeInTheDocument()
    expect(card).toHaveTextContent('Starting$1,000')
    expect(card).toHaveTextContent('$1,003.20')
    expect(card).toHaveTextContent('Next: NYR @ BOS · Decision window Sat Oct 17, 6:30 PM ET – 6:45 PM ET')
    for (const name of ['MLB account', 'NFL account', 'NBA account', 'College Football account']) {
      expect(screen.getByRole('article', { name })).toBeInTheDocument()
    }
    expect(screen.getByText(/combined · every virtual sport account/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'NHL' }))
    expect(await screen.findByRole('heading', { name: 'NHL account', level: 1 })).toBeInTheDocument()
    expect(screen.getAllByRole('article').map((a) => a.getAttribute('aria-label')).filter((l) => l?.endsWith(' account')))
      .toEqual(['NHL account'])
  })
})
