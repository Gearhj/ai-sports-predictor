import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ledgerKey, type Ledger } from '../../services/commandCenter'
import { fetchQuery } from '../../services/queryCache'
import { COLLAPSE_STORAGE_KEY } from '../../services/activeWagers'
import { ledgerOf, pos } from '../../test/activeWagersFixture'

let ledger: Ledger | null = null
let ledgerError: string | null = null
const decisions = { schema_version: 'AISP_COMMAND_CENTER_DECISIONS_V1', date: '', generated_at: '', counts: {}, exposure: 0, staked: 0, realized_profit_loss: 0, next_decision_deadline: null, items: [] as unknown[] }

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string) => {
      if (path.startsWith('/command-center/ledger')) return ledgerError ? Promise.reject(new Error(ledgerError)) : Promise.resolve(ledger)
      if (path.startsWith('/command-center/decisions')) return Promise.resolve(decisions)
      if (path.startsWith('/scoreboard')) return Promise.resolve({ schema_version: 'AISP_GAME_SCOREBOARD_V1', games: {}, live: 0 })
      return Promise.reject(new Error(`unexpected ${path}`))
    }),
  }
})

import { ActiveWagersDock } from './ActiveWagersPanel'

const inHours = (h: number) => new Date(Date.now() + h * 3_600_000).toISOString()

function setViewport(desktop: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({ matches: desktop, media: query, addEventListener() {}, removeEventListener() {} }),
  })
}

function Page({ name }: { name: string }) {
  return <div><h1>{name}</h1><Link to="/games/1?sport=MLB">game</Link> <Link to="/experiment">experiment</Link></div>
}

function renderShell(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<Page name="Home" />} />
        <Route path="/games/:id" element={<Page name="Game" />} />
        <Route path="/experiment" element={<Page name="Experiment" />} />
      </Routes>
      <ActiveWagersDock />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  setViewport(true)
  ledger = ledgerOf({
    MLB: [pos('m1', { stake: 5, starts_at: inHours(3), matchup: 'NYY @ BOS' }), pos('m0', { status: 'SETTLED', result: 'WIN', profit_loss: 7.5 })],
    NFL: [pos('n1', { stake: 2.5, starts_at: inHours(1), matchup: 'NYG @ DAL', entry: { ...pos('n1').entry, american: -125, decimal: 1.8 } })],
  })
  ledgerError = null
})
afterEach(() => { cleanup(); try { window.localStorage.clear() } catch { /* ignore */ } })

describe('Active Wagers panel', () => {
  it('shows every open wager across sports with ledger totals, ordered by start', async () => {
    renderShell()
    const panel = await screen.findByRole('complementary', { name: 'Active wagers' })
    await within(panel).findByText('NYG @ DAL')
    expect(panel).toHaveTextContent('All sports')
    expect(panel).toHaveTextContent('Paper · $1,000 Experiment')
    const summary = within(panel).getByLabelText('Active wager summary')
    expect(summary).toHaveTextContent('Open wagers2')
    expect(summary).toHaveTextContent('Stake at risk$7.50')
    expect(summary).toHaveTextContent('Paper bankroll$3,969.01')
    const cards = within(panel).getAllByRole('article')
    expect(cards.map((c) => within(c).getByRole('link').textContent)).toEqual(['NYG @ DAL', 'NYY @ BOS'])
    expect(cards[0]).toHaveTextContent('Payout if it wins$4.50 incl. stake')
    expect(within(cards[0]).getByRole('link', { name: 'NYG @ DAL' })).toHaveAttribute('href', '/games/g-n1?sport=NFL')
    // The settled position is not active.
    expect(panel).not.toHaveTextContent('Am0 @ Hm0')
  })

  it('a failed first read is an error, never "no open wagers"', async () => {
    ledgerError = 'HTTP 503'
    renderShell()
    const panel = await screen.findByRole('complementary', { name: 'Active wagers' })
    expect(await within(panel).findByRole('alert')).toHaveTextContent('could not be loaded')
    expect(panel).not.toHaveTextContent('No open wagers')
    expect(panel).not.toHaveTextContent('Open wagers')
  })

  it('keeps the cards and says so when a refresh fails', async () => {
    renderShell()
    const panel = await screen.findByRole('complementary', { name: 'Active wagers' })
    await within(panel).findByText('NYY @ BOS')
    await act(async () => { await fetchQuery(ledgerKey(), () => Promise.reject(new Error('HTTP 502')), { force: true }) })
    expect(within(panel).getByText('NYY @ BOS')).toBeInTheDocument()
    expect(panel).toHaveTextContent('unable to refresh, showing the last successful view')
    expect(panel).toHaveTextContent(/As of .* ET/)
  })

  it('shows a useful empty state only when the ledger genuinely has no open position', async () => {
    ledger = ledgerOf({ MLB: [pos('s', { status: 'SETTLED', result: 'LOSS', profit_loss: -5 })] })
    renderShell()
    const panel = await screen.findByRole('complementary', { name: 'Active wagers' })
    expect(await within(panel).findByText('No open wagers.')).toBeInTheDocument()
    expect(within(panel).getAllByRole('link', { name: /wager history/i }).length).toBeGreaterThan(0)
  })

  it('collapse persists across navigation and a remount (reload)', async () => {
    const { unmount } = renderShell()
    const panel = await screen.findByRole('complementary', { name: 'Active wagers' })
    await within(panel).findByText('NYY @ BOS')
    fireEvent.click(within(panel).getByRole('button', { name: 'Collapse active wagers' }))
    const rail = screen.getByRole('button', { name: 'Show active wagers: 2 open, $7.50 at risk' })
    expect(rail).toHaveAttribute('aria-expanded', 'false')
    expect(window.localStorage.getItem(COLLAPSE_STORAGE_KEY)).toBe('1')
    fireEvent.click(screen.getByRole('link', { name: 'game' }))
    await screen.findByRole('heading', { name: 'Game' })
    expect(screen.getByRole('button', { name: /^Show active wagers/ })).toBeInTheDocument()
    unmount()
    renderShell('/experiment')
    expect(await screen.findByRole('button', { name: /^Show active wagers/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^Show active wagers/ }))
    expect(screen.getByRole('complementary', { name: 'Active wagers' })).toBeInTheDocument()
    expect(window.localStorage.getItem(COLLAPSE_STORAGE_KEY)).toBe('0')
  })

  it('small screens: pill opens a modal sheet; Esc closes it and returns focus', async () => {
    setViewport(false)
    renderShell()
    const pill = await screen.findByRole('button', { name: 'Active wagers: 2 open, $7.50 at risk' })
    expect(screen.queryByRole('complementary', { name: 'Active wagers' })).toBeNull()
    fireEvent.click(pill)
    const dialog = screen.getByRole('dialog', { name: 'Active wagers' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    await waitFor(() => expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Close active wagers' })))
    expect(dialog).toHaveTextContent('NYG @ DAL')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() => expect(document.activeElement).toBe(pill))
  })
})
