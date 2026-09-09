import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LabsPage } from './LabsPage'
import { SportRegistryProvider } from '../hooks/useSportRegistry'
import { SportSelectionProvider } from '../hooks/useSportSelection'
import { endpoints } from '../services/api'
import { sportRegistryFixture } from '../test/sportRegistryFixture'
import type { LabsGames } from '../types/labs'
import type { NflWeekOverview, NflWeeks } from '../types/nfl'

const MLB = {
  schema_version: 'AISP_INTERACTIVE_LABS_V1', league: 'MLB', featured_slate_date: '2026-09-08',
  games: [
    { game_id: 'cle-bal', label: 'CLE @ BAL', slate_date: '2026-09-08', starts_at: '2026-09-08T23:00:00Z', event_status: 'FINAL', is_final: true, is_pregame: false, home_score: 4, away_score: 2,
      gen1_available: true, gen1_href: '/games/cle-bal/simulation?track=gen1', v18_available: true, v18_href: '/games/cle-bal/simulation?track=v18',
      v18_path_events: 76, v18_views: { broadcast: true, geometry: true, population: true }, analysis_href: '/games/cle-bal',
      v18_expected_home: 4.5, v18_expected_away: 3.9, v18_simulation_count: 5000 },
    { game_id: 'old', label: 'TOR @ ATH', slate_date: '2026-09-07', event_status: 'FINAL', is_final: true, gen1_available: true, gen1_href: '/games/old/simulation?track=gen1', v18_available: false,
      v18_views: { broadcast: false, geometry: false, population: false }, analysis_href: '/games/old', v18_skip_reason: 'No saved starter evidence' },
  ],
} as unknown as LabsGames
const WEEKS = { current: { season: 2026, week: 1 }, weeks: [{ season: 2026, week: 1 }] } as NflWeeks
const NFL = {
  season: 2026, week: 1, events: [{
    event_id: '2026_01_NE_SEA', canonical_game_id: 'ne-sea', week: 1, state: 'UPCOMING', lab_available: true,
    home: { name: 'Seattle Seahawks', abbreviation: 'SEA' }, away: { name: 'New England Patriots', abbreviation: 'NE' },
    kickoff_utc: '2026-09-10T00:20:00Z', supplements: { joint_engines: [], drive_engines: ['NFL_DEVELOPMENT_CONDITIONAL_DRIVE_V2'], scenario_engines: ['NFL_DEVELOPMENT_CONDITIONAL_DRIVE_V2'] },
    engines: [{ engine_id: 'NFL_M1_XGBOOST', model_label: 'M1 research shadow', expected_home_score: 22.7, expected_away_score: 19.9, simulation: { joint_available: true, count: 10000 }, timestamps: {} }],
    drive_previews: [{ engine_id: 'NFL_DEVELOPMENT_CONDITIONAL_DRIVE_V2', scenario_id: 'BASE', expected_home_score: 23, expected_away_score: 20, simulation_count: 1000 }],
  }],
} as unknown as NflWeekOverview

function mount(search = '?sport=ALL') {
  vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
  vi.spyOn(endpoints, 'labsGames').mockResolvedValue(MLB)
  vi.spyOn(endpoints, 'nflWeeks').mockResolvedValue(WEEKS)
  vi.spyOn(endpoints, 'nflWeek').mockResolvedValue(NFL)
  render(<MemoryRouter initialEntries={['/labs' + search]}><SportSelectionProvider><SportRegistryProvider><LabsPage /></SportRegistryProvider></SportSelectionProvider></MemoryRouter>)
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear() })

describe('Game-first unified Labs', () => {
  it('puts real MLB and NFL games together immediately, on explicitly different windows', async () => {
    mount()
    const cards = await screen.findAllByRole('article')
    expect(cards).toHaveLength(2)
    expect(cards[0]).toHaveAttribute('aria-label', 'MLB CLE @ BAL')
    expect(cards[1]).toHaveAttribute('aria-label', 'NFL NE @ SEA')
    expect(screen.queryByRole('heading', { name: 'TOR @ ATH' })).not.toBeInTheDocument()
    expect(screen.getByText(/latest saved V18 slate/)).toBeInTheDocument()
    expect(within(cards[0]).getByText(/5,000 simulations/)).toBeInTheDocument()
    expect(within(cards[0]).getByRole('link', { name: /Enter V18 Diamond Simulator/ })).toHaveAttribute('href', '/games/cle-bal/simulation?track=v18&sport=MLB')
    expect(within(cards[1]).getByRole('link', { name: /Enter NFL Outcome Explorer/ })).toHaveAttribute('href', '/games/ne-sea/simulation?view=explorer&engine=NFL_M1_XGBOOST&sport=NFL')
  })
  it('global NFL selection excludes MLB and selecting drive changes the real engine and destination', async () => {
    const user = userEvent.setup()
    mount('?sport=NFL')
    const card = await screen.findByRole('article', { name: 'NFL NE @ SEA' })
    expect(screen.queryByRole('article', { name: /MLB/ })).not.toBeInTheDocument()
    expect(endpoints.labsGames).not.toHaveBeenCalled()
    await user.selectOptions(screen.getByLabelText('Engine for NE @ SEA'), 'DRIVE')
    expect(within(card).getByText('20.0 — 23.0')).toBeInTheDocument()
    expect(within(card).getByText(/1,000 simulations/)).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: /Enter Drive Theater/ })).toHaveAttribute('href', '/games/ne-sea/simulation?view=drives&engine=NFL_DEVELOPMENT_CONDITIONAL_DRIVE_V2&sport=NFL')
  })
  it('preserves an explicit older MLB date and does not offer absent V18 evidence', async () => {
    mount('?sport=MLB&date=2026-09-07')
    const card = await screen.findByRole('article', { name: 'MLB TOR @ ATH' })
    expect(screen.queryByRole('article', { name: /NFL/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'MLB CLE @ BAL' })).not.toBeInTheDocument()
    expect(within(card).queryByRole('option', { name: /V18/ })).not.toBeInTheDocument()
    expect(within(card).getByRole('link', { name: /Enter Gen 1 Outcome Distribution/ })).toHaveAttribute('href', '/games/old/simulation?track=gen1&sport=MLB')
    expect(within(card).getByText(/V18 forecast skipped: No saved starter evidence/)).toBeInTheDocument()
  })
})
