/**
 * Interactive Labs discoverability and honesty.
 *
 * The guarantees under test: every league's lab is listed at its real status,
 * a planned lab carries no route, the game picker comes from the read model
 * rather than a hardcoded game, and a V18 action appears only for a game that
 * actually has a registered path.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LabsPage } from './LabsPage'
import { SportRegistryProvider } from '../hooks/useSportRegistry'
import { endpoints } from '../services/api'
import { sportRegistryFixture } from '../test/sportRegistryFixture'
import type { LabsGame, LabsGames } from '../types/labs'

function game(overrides: Partial<LabsGame> & { game_id: string }): LabsGame {
  return {
    label: 'TOR @ ATH', home_team: 'ATH', away_team: 'TOR',
    starts_at: '2026-09-08T02:05:00Z', slate_date: '2026-09-07',
    event_status: 'SCHEDULED', is_final: false, is_pregame: true,
    home_score: null, away_score: null,
    gen1_available: true, gen1_href: `/games/${overrides.game_id}/simulation?track=gen1`,
    v18_available: false, v18_href: null, v18_forecast_id: null, v18_path_events: 0,
    v18_forecast_at: null, v18_object_sha256: null,
    v18_views: { broadcast: false, geometry: false, population: false },
    v18_skip_category: null, v18_skip_reason: null,
    analysis_href: `/games/${overrides.game_id}`,
    ...overrides,
  }
}

const WITH_V18 = game({
  game_id: 'b8c8632f-7ae1-4ad0-9404-bd51aafa78fe',
  v18_available: true,
  v18_href: '/games/b8c8632f-7ae1-4ad0-9404-bd51aafa78fe/simulation?track=v18',
  v18_forecast_id: 'mlb-v18-1', v18_path_events: 74,
  v18_forecast_at: '2026-09-07T18:00:00Z', v18_object_sha256: 'a'.repeat(64),
  v18_views: { broadcast: true, geometry: false, population: false },
})
const GEN1_ONLY = game({
  game_id: 'game-2', label: 'CIN @ LAD',
  v18_skip_category: 'MISSING_REQUIRED_EVIDENCE',
  v18_skip_reason: 'starter identity and timestamp are required',
})
const FINISHED = game({
  game_id: 'game-3', label: 'ATL @ PHI', slate_date: '2026-09-06',
  event_status: 'FINAL', is_final: true, is_pregame: false,
  home_score: 4, away_score: 2,
})

const PAYLOAD: LabsGames = {
  schema_version: 'AISP_INTERACTIVE_LABS_V1',
  league: 'MLB', generated_at: '2026-09-07T22:00:00Z',
  eligible_count: 3, v18_count: 1, v18_skip_count: 1,
  featured_slate_date: '2026-09-07', featured_v18_count: 1,
  featured_v18_skip_count: 1,
  games: [WITH_V18, GEN1_ONLY, FINISHED],
  note: 'A game is listed only when its own persisted simulation can be replayed.',
}

function mount(search = '', payload: LabsGames | null = PAYLOAD) {
  vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
  vi.spyOn(endpoints, 'labsGames').mockImplementation(
    payload ? () => Promise.resolve(payload) : () => Promise.reject(new Error('offline')),
  )
  render(
    <MemoryRouter initialEntries={[`/labs${search}`]}>
      <SportRegistryProvider>
        <Routes><Route path="/labs" element={<LabsPage />} /></Routes>
      </SportRegistryProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-09-07T22:00:00Z'))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('the lab list', () => {
  it('lists every league lab at its real status', async () => {
    mount()
    await screen.findByRole('heading', { name: 'MLB Diamond Simulator' })

    const expected: Array<[string, string]> = [
      ['MLB Diamond Simulator', 'Available'],
      ['NFL Gridiron Lab', 'Planned'],
      ['NBA Court Lab', 'Planned'],
      ['WNBA Court Lab', 'Planned'],
      ['NHL Ice Lab', 'Planned'],
      ['UFC Fight Lab', 'Planned'],
    ]
    for (const [name, status] of expected) {
      const card = screen.getByRole('heading', { name }).closest('article')!
      expect(within(card).getByText(status)).toBeInTheDocument()
    }
    expect(document.querySelectorAll('.lab-card')).toHaveLength(6)
  })

  it('gives a planned lab no route, no controls and no outputs', async () => {
    mount()
    await screen.findByRole('heading', { name: 'NHL Ice Lab' })
    for (const name of ['NFL Gridiron Lab', 'NBA Court Lab', 'WNBA Court Lab', 'NHL Ice Lab', 'UFC Fight Lab']) {
      const card = screen.getByRole('heading', { name }).closest('article')!
      expect(card).toHaveAttribute('aria-disabled', 'true')
      expect(card.querySelector('a')).toBeNull()
      expect(card.querySelector('button')).toBeNull()
      expect(within(card as HTMLElement).getByText('Not available yet')).toBeInTheDocument()
    }
  })

  it('names each lab after its own sport, never after baseball', async () => {
    mount()
    await screen.findByRole('heading', { name: 'NHL Ice Lab' })
    const nhl = screen.getByRole('heading', { name: 'NHL Ice Lab' }).closest('article')!
    expect(nhl).toHaveTextContent('Hockey')
    expect(nhl).not.toHaveTextContent(/diamond/i)
    const ufc = screen.getByRole('heading', { name: 'UFC Fight Lab' }).closest('article')!
    expect(ufc).toHaveTextContent('Mixed Martial Arts')
  })
})

describe('the game picker', () => {
  it('lists eligible games from the read model, grouped by their own date', async () => {
    mount('?league=MLB')
    expect(await screen.findByText('TOR @ ATH')).toBeInTheDocument()
    expect(screen.getByText('CIN @ LAD')).toBeInTheDocument()
    expect(screen.getByText('ATL @ PHI')).toBeInTheDocument()
    expect(screen.getByText(/1 V18-enabled game/))
      .toBeInTheDocument()
    expect(screen.getByText(/1 forecast skip/)).toBeInTheDocument()
  })

  it('offers both tracks only where a V18 path exists', async () => {
    mount('?league=MLB')
    await screen.findByText('TOR @ ATH')

    const withV18 = screen.getByText('TOR @ ATH').closest('article')!
    expect(within(withV18).getByText('V18 AVAILABLE')).toBeInTheDocument()
    expect(within(withV18).getByRole('link', { name: /Open Gen 1 Outcome Distribution/ }))
      .toHaveAttribute('href', '/games/b8c8632f-7ae1-4ad0-9404-bd51aafa78fe/simulation?track=gen1')
    expect(within(withV18).getByRole('link', { name: /Open V18 Simulator/ }))
      .toHaveAttribute('href', '/games/b8c8632f-7ae1-4ad0-9404-bd51aafa78fe/simulation?track=v18')
    expect(withV18).toHaveTextContent(/Broadcast Replay only/)
    expect(within(withV18).getByRole('link', { name: /Open game analysis/ }))
      .toHaveAttribute('href', '/games/b8c8632f-7ae1-4ad0-9404-bd51aafa78fe')

    // A Gen 1-only game must not imply a replay it does not have.
    const gen1Only = screen.getByText('CIN @ LAD').closest('article')!
    expect(within(gen1Only).getByRole('link', { name: /Open Gen 1 Outcome Distribution/ }))
      .toBeInTheDocument()
    expect(within(gen1Only).queryByRole('link', { name: /Open V18/ })).not.toBeInTheDocument()
    expect(within(gen1Only).getByText(/V18 forecast skipped: starter identity/i))
      .toBeInTheDocument()
  })

  it('labels a preserved historical replay with its date and final score', async () => {
    mount('?league=MLB')
    await screen.findByText('ATL @ PHI')
    const finished = screen.getByText('ATL @ PHI').closest('article')!
    expect(within(finished).getByText('Final 2–4')).toBeInTheDocument()
    expect(finished).toHaveTextContent(/September 6, 2026|2026-09-06|Sep 6/)
    // It is still explorable, just not presented as live.
    expect(within(finished).getByRole('link', { name: /Open Gen 1 Outcome Distribution/ }))
      .toBeInTheDocument()
  })

  it('does not hardcode a destination game', async () => {
    const other = game({ game_id: 'only-game', label: 'SEA @ HOU' })
    mount('?league=MLB', {
      ...PAYLOAD, games: [other], eligible_count: 1, v18_count: 0, v18_skip_count: 0,
      featured_v18_count: 0, featured_v18_skip_count: 0,
    })
    await screen.findByText('SEA @ HOU')
    expect(screen.queryByText('TOR @ ATH')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open Gen 1 Outcome Distribution/ }))
      .toHaveAttribute('href', '/games/only-game/simulation?track=gen1')
  })

  it('states an empty or unreachable list rather than substituting one', async () => {
    mount('?league=MLB', {
      ...PAYLOAD, games: [], eligible_count: 0, v18_count: 0, v18_skip_count: 0,
      featured_slate_date: null, featured_v18_count: 0, featured_v18_skip_count: 0,
    })
    expect(await screen.findByText(/No MLB game currently persists a simulation/i))
      .toBeInTheDocument()

    cleanup()
    mount('?league=MLB', null)
    expect(await screen.findByText(/No illustrative game was substituted/i)).toBeInTheDocument()
  })

  it('selects the available lab from the league query parameter', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    mount()
    await screen.findByText('TOR @ ATH')
    // MLB is the only available lab, so it is preselected and choosing it again
    // is a no-op rather than a broken state.
    const mlb = screen.getByRole('heading', { name: 'MLB Diamond Simulator' }).closest('article')!
    await user.click(within(mlb as HTMLElement).getByRole('button', { name: /Choose a game/i }))
    expect(screen.getByText('TOR @ ATH')).toBeInTheDocument()
  })
})

describe('navigation', () => {
  it('offers a breadcrumb back to the command center', async () => {
    mount()
    const crumbs = await screen.findByRole('navigation', { name: /breadcrumb/i })
    expect(within(crumbs).getByRole('link', { name: /Daily command center/i }))
      .toHaveAttribute('href', '/')
    expect(within(crumbs).getByText('Interactive labs')).toHaveAttribute('aria-current', 'page')
  })
})
