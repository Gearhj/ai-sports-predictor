import { act, cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { currentScope } from '../../services/queryCache'
import { namedScenario, type Scenario } from '../../test/homeFixtures'

let current: Scenario | null = null
let hang = false
const requests: string[] = []

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string) => {
      requests.push(path)
      if (hang || !current) return new Promise(() => {})
      if (path.startsWith('/command-center/decisions')) return Promise.resolve(current.decisions)
      if (path.startsWith('/command-center/markets')) return Promise.resolve(current.markets)
      if (path.startsWith('/command-center/ledger')) return Promise.resolve(current.ledger)
      if (path.startsWith('/sports/readiness')) return Promise.resolve({ schema_version: 'x', generated_at: '', authority: '', sports: [] })
      return Promise.reject(new Error(`unexpected ${path}`))
    }),
  }
})

import { CommandHome } from './CommandHome'

afterEach(() => { cleanup(); current = null; hang = false; requests.length = 0 })

async function renderScenario(name: string) {
  current = namedScenario(name, { now: Date.now() })
  render(<MemoryRouter><CommandHome withSport={(href) => href} /></MemoryRouter>)
  const hero = await screen.findByRole('region', { name: /^(Today’s (Best|Model|Games on)|Closest)/ })
  await within(hero).findAllByRole('listitem')
  return hero
}

describe('Home Edge Board', () => {
  it('headlines up to four evaluated games, ranked, with decision links and accessible rails', async () => {
    const hero = await renderScenario('four-plus')
    expect(within(hero).getByRole('heading', { level: 1 })).toHaveTextContent('Today’s Best Edges')
    const cards = within(hero).getAllByRole('listitem')
    expect(cards).toHaveLength(4)
    // Qualified first (largest adjusted edge), declined after every qualified game.
    expect(cards[0]).toHaveAccessibleName(/^1\. .*Qualified$/)
    expect(cards.map((c) => c.getAttribute('aria-label')!.split(', ').pop())).toEqual(['Qualified', 'Qualified', 'Qualified', 'Declined'])
    expect(within(cards[0]).getByTitle('+7.1 pts')).toBeInTheDocument()
    expect(within(hero).getAllByRole('link', { name: /View decision/ })).toHaveLength(4)
    expect(within(hero).getByText('+2 more on Daily Decisions')).toBeInTheDocument()
    const rail = within(cards[0]).getAllByRole('img').find((el) => /win probability: model/.test(el.getAttribute('aria-label') ?? ''))!
    expect(rail.getAttribute('aria-label')).toMatch(/no-vig consensus .*books .* to .*gap/)
    expect(rail.querySelector('[tabindex="0"]')).not.toBeNull()
  })

  it('before prices: model leans with win split and milestones, no edge, no EV, never declined', async () => {
    const hero = await renderScenario('forecasts-only')
    expect(within(hero).getByRole('heading', { level: 1 })).toHaveTextContent('Today’s Model Leans')
    expect(within(hero).getByText('Sportsbook markets pending')).toBeInTheDocument()
    expect(within(hero).queryByText(/Adj\. edge|Exp\. return|Declined|NO BET/i)).toBeNull()
    expect(within(hero).getAllByText('Market pending')).toHaveLength(4)
    expect(within(hero).getAllByRole('link', { name: /Analyze game/ })).toHaveLength(4)
    expect(within(hero).getAllByRole('img', { name: /Model win split/ })).toHaveLength(4)
    expect(within(hero).getAllByText('Market collection').length).toBeGreaterThan(0)
  })

  it('before forecasts and prices: games on deck with the milestones still required', async () => {
    const hero = await renderScenario('pending-all')
    expect(within(hero).getByRole('heading', { level: 1 })).toHaveTextContent('Today’s Games on Deck')
    expect(within(hero).getAllByText(/expected by/)).toHaveLength(4)
    expect(within(hero).queryByText(/pts|Exp\. return|Declined/)).toBeNull()
  })

  it('a recorded position leads, with its entry terms', async () => {
    const hero = await renderScenario('open-position')
    const cards = within(hero).getAllByRole('listitem')
    expect(cards[0]).toHaveAccessibleName(/Position recorded$/)
    expect(within(cards[0]).getByText(/Entered \+125 · Betmgm · stake \$12\.50/)).toBeInTheDocument()
  })

  it('closest to a play shows plain reasons without machine codes', async () => {
    const hero = await renderScenario('no-qualifiers')
    expect(within(hero).getByRole('heading', { level: 1 })).toHaveTextContent('Closest to a Play')
    expect(within(hero).getAllByText('Adjusted edge below the 2.0 pt minimum')).toHaveLength(3)
  })
})

describe('Home layout and timeline', () => {
  it('puts the bankroll with its sport chips below the hero and removes the separate sport-card row', async () => {
    await renderScenario('four-plus')
    const bank = screen.getByRole('region', { name: 'Combined virtual bankroll' })
    expect(within(bank).getByRole('list', { name: 'Sport accounts' }).children).toHaveLength(4)
    expect(screen.queryByRole('list', { name: 'Sport bankrolls' })).toBeNull()
    const order = [...document.querySelectorAll('.cc-edge, .cc-bank, .cc-agenda-panel')].map((el) => el.className.split(' ').find((c) => ['cc-edge', 'cc-bank', 'cc-agenda-panel'].includes(c)))
    expect(order).toEqual(['cc-edge', 'cc-bank', 'cc-agenda-panel', 'cc-agenda-panel'])
  })

  it("lists today's games in start order, four identical starts under one time label", async () => {
    // A morning clock, so the fixture's games (2–3 hours ahead) stay on today's Eastern date.
    vi.useFakeTimers({ toFake: ['Date'], now: Date.parse('2026-09-17T14:00:00Z') })
    try {
      await renderScenario('shared-start')
      const schedule = screen.getByRole('region', { name: 'Today’s games' })
      expect(within(schedule).getByText('Game times and AISP positions · Eastern Time')).toBeInTheDocument()
      expect(within(schedule).getByText('8 games · 0 AISP positions')).toBeInTheDocument()
      expect(within(schedule).getByText('No AISP positions have been opened yet.')).toBeInTheDocument()
      expect(within(schedule).getByRole('heading', { level: 3 })).toHaveTextContent(/ET · 4 games$/)
      expect(within(schedule).getAllByRole('link')).toHaveLength(8)
      // Only game starts and positions: no axis, legend or operational milestones.
      expect(schedule.querySelector('svg')).toBeNull()
      expect(schedule.textContent).not.toMatch(/Collection|Decision window|Settlement|Forecast|Next:/)
    } finally {
      vi.useRealTimers()
    }
  })

  it('renders the cached ledger immediately while the refresh is still pending', async () => {
    const cached = namedScenario('four-plus', { now: Date.now() })
    const scope = currentScope().split(':').slice(1).join(':')
    window.localStorage.setItem(`aisp-query:v1:${scope}|command-ledger`, JSON.stringify({ data: cached.ledger, updatedAt: Date.now() - 60_000, asOf: Date.now() - 60_000, staleTimeMs: 30_000 }))
    hang = true
    await act(async () => {
      render(<MemoryRouter><CommandHome withSport={(href) => href} /></MemoryRouter>)
    })
    expect(screen.getByText('$4,007.40')).toBeInTheDocument()
    expect(requests.some((p) => p.startsWith('/command-center/ledger'))).toBe(true)
  })
})
