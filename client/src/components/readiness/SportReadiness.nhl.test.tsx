/**
 * NHL on the readiness surfaces (live since 2026-10-16): the card renders from the
 * served /sports/readiness entry with its own eligibility route stated, links to the
 * NHL research page by the ten-digit NHL game id, and never borrows the producer
 * wording CFB/NBA use. A canonical AISP game id never becomes a broken NHL link.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../../services/api'
import { gameHref, SPORT_CODES, SPORT_COLORS, SPORT_NAMES, asSport } from '../../services/commandCenter'
import { SportReadiness, type SportReadinessEntry } from './SportReadiness'

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return { ...actual, apiRequest: vi.fn() }
})

afterEach(() => { cleanup(); vi.mocked(apiRequest).mockReset() })

const NHL: SportReadinessEntry = {
  sport: 'NHL', label: 'NHL', policy_version: 'AISP_NHL_PAPER_POLICY_V1', controlling_engine: 'NHL_GEN1_FINAL_TWO_WAY',
  next_game: { matchup: 'PIT @ COL', starts_label: 'Fri Oct 16, 9:00 PM ET', game_id: '7a242ab1-ee67-481b-9156-c369cd806687',
    source_game_id: '2026020119', research_href: '/nhl/games/2026020119' },
  forecast: { state: 'IN_PROCESS_FORECAST_AVAILABLE', label: 'AISP in-process Gen1 forecast held: final two-way (OT/SO included) home 63.04% / away 36.96%' },
  price: { state: 'COLLECTION_SCHEDULED', label: 'The Odds API collection Fri Oct 16, 7:45 PM ET – 8:25 PM ET' },
  decision: { state: 'WINDOW_NOT_OPEN', label: 'Decision window Fri Oct 16, 8:30 PM ET – 8:45 PM ET' },
  eligibility: { eligible: true, reason: 'AISP_IN_PROCESS_ELIGIBLE', contract: 'AISP_NHL_IN_PROCESS_ELIGIBILITY_V1',
    route: 'AISP_IN_PROCESS', label: 'AISP in-process eligibility' },
  account: { funded: true, label: '$1,000.00 virtual bankroll', effective_on: '2026-10-16' },
  position: { state: 'NONE_YET', label: 'No NHL position yet', open: 0, settled: 0 },
  pipeline: { ready: true, blocker: null, could_qualify: 'Yes, if the final two-way edge clears 2 points.' },
}

describe('NHL readiness', () => {
  it('renders the served NHL card with its in-process route, account and research link', async () => {
    vi.mocked(apiRequest).mockResolvedValue({ schema_version: 'AISP_SPORT_READINESS_V1', generated_at: '', authority: 'Virtual research only.',
      sports: [NHL] } as never)
    render(<MemoryRouter><SportReadiness /></MemoryRouter>)
    const card = await screen.findByTestId('readiness-NHL')
    expect(screen.getByRole('heading', { name: 'Every sport, right now' })).toBeInTheDocument()
    expect(within(card).getByText('NHL')).toBeInTheDocument()
    expect(within(card).getByTestId('readiness-pipeline-NHL')).toHaveTextContent('Pipeline ready')
    expect(within(card).getByRole('link', { name: 'PIT @ COL' })).toHaveAttribute('href', '/nhl/games/2026020119')
    expect(card).toHaveTextContent('AISP in-process eligibility')
    expect(card).not.toHaveTextContent('Producer eligibility present')
    expect(card).toHaveTextContent('$1,000.00 virtual bankroll · effective 2026-10-16')
    expect(card).toHaveTextContent('AISP_NHL_PAPER_POLICY_V1 · NHL_GEN1_FINAL_TWO_WAY')
  })

  it('names a refused in-process eligibility instead of asking for producer evidence', async () => {
    const refused = { ...NHL, eligibility: { eligible: false, reason: 'ELIGIBILITY_RECORD_INVALID', route: 'AISP_IN_PROCESS',
      label: 'Not eligible (ELIGIBILITY_RECORD_INVALID)' }, pipeline: { ready: false, blocker: 'Eligibility refused (ELIGIBILITY_RECORD_INVALID).', could_qualify: null } }
    vi.mocked(apiRequest).mockResolvedValue({ schema_version: 'AISP_SPORT_READINESS_V1', generated_at: '', authority: '', sports: [refused] } as never)
    render(<MemoryRouter><SportReadiness sport="nhl" /></MemoryRouter>)
    const card = await screen.findByTestId('readiness-NHL')
    expect(card).toHaveTextContent('Not eligible (ELIGIBILITY_RECORD_INVALID)')
    expect(card).not.toHaveTextContent('Awaiting producer evidence')
    expect(within(card).getByTestId('readiness-blocker-NHL')).toHaveTextContent('Eligibility refused')
  })
})

describe('NHL as a sport account', () => {
  it('is a sport code with its existing colour token and name', () => {
    expect(SPORT_CODES).toEqual(['MLB', 'NFL', 'NBA', 'CFB', 'NHL'])
    expect(SPORT_NAMES.NHL).toBe('NHL')
    expect(SPORT_COLORS.NHL).toBe('var(--cc-nhl)')
    expect(asSport('nhl')).toBe('NHL')
  })

  it('links a ten-digit NHL game id to its page and a canonical AISP id to the research board', () => {
    expect(gameHref('NHL', '2026020119')).toBe('/nhl/games/2026020119')
    // An AISP game id opens the same game page; the server resolves it (the reported 404).
    expect(gameHref('NHL', '7a242ab1-ee67-481b-9156-c369cd806687')).toBe('/nhl/games/7a242ab1-ee67-481b-9156-c369cd806687')
    expect(gameHref('NHL', null)).toBe('/nhl/research')
  })
})
