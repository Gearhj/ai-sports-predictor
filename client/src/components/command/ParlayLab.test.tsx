import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CoverageGame } from '../../services/commandCenter'
import type { AssessedQuote, DailyAssessments, GameAssessment, PopulationBinding } from '../../services/probability'
import { game, priced } from '../../test/homeFixtures'

const calls: { path: string; body: unknown }[] = []

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string, options: RequestInit = {}) => {
      calls.push({ path, body: options.body ? JSON.parse(String(options.body)) : null })
      if (path === '/probability/evaluate') {
        return Promise.resolve({
          schema_version: 'AISP_PROBABILITY_RESULT_V1', kind: 'SAME_GAME_PARLAY', display_method: 'PAIRED_DRAW_INTERSECTION',
          authority: 'PAPER_RESEARCH_EVIDENCE', authority_label: 'NFL M1', fair_american: 300, creates_decision: false, creates_position: false,
          contract: {
            method: 'PER_DRAW_SAME_GAME_INTERSECTION', legs: (JSON.parse(String(options.body)) as { legs: unknown[] }).legs,
            counts: { win: 2500, loss: 6250, push: 1250, void: 0 }, probabilities: { win: 0.25, loss: 0.625, push: 0.125, void: 0 },
            effectiveDrawCount: 10000, winProbabilityMonteCarloStandardError: 0.0043, population: BINDING,
          },
          parlay: { quote_status: 'SPORTSBOOK_PARLAY_PRICE_UNAVAILABLE', message: 'x', quote: null, implied_probability: null, edge: null, expected_return: null },
        })
      }
      return Promise.reject(new Error(`unexpected ${path}`))
    }),
  }
})

import { AssessmentBoard, classifyAssessments, primaryQuote, type AssessmentSource } from './AssessmentBoard'
import { ParlayLab } from './ParlayLab'
import parlaySource from './ParlayLab.tsx?raw'
import boardSource from './AssessmentBoard.tsx?raw'
import probabilitySource from '../../services/probability.ts?raw'

afterEach(() => { cleanup(); calls.length = 0 })

const BINDING: PopulationBinding = {
  sport: 'NFL', gameId: 'g1', forecastId: 'asp-sports-game-forecast:sha256:abc', modelId: 'NFL_M1_XGBOOST',
  configurationId: 'NFL_M1_2026_RESEARCH_SHADOW_V1', packageSha256: 'a'.repeat(64), populationSha256: 'b'.repeat(64), drawCount: 10000,
}
const clock = { now: Date.parse('2026-09-20T15:00:00Z') }

function coverage(): CoverageGame[] {
  return [priced(clock, game(clock, '1', { sport: 'NFL', startIn: 120 })), priced(clock, game(clock, '2', { sport: 'NFL', startIn: 150, pair: 1 }))]
    .map((g, i) => ({ ...g, game_id: `g${i + 1}` }))
}

const side = (american: number, extra: Partial<AssessedQuote['sides'][string]> = {}) => ({
  american, decimal: american > 0 ? 1 + american / 100 : 1 - 100 / american, no_vig: 0.5,
  model: { win: 0.4, loss: 0.55, push: 0.05, void: 0, conditional: 0.4211, mcse: 0.005, fair_decimal: 2.37, fair_american: 137 },
  edge: null, expected_return: null, edge_reason: 'QUOTE_NOT_CURRENT', ...extra,
})

function quote(market: string, sides: AssessedQuote['sides'], opts: Partial<AssessedQuote> = {}): AssessedQuote {
  return {
    market, line: null, sportsbook: 'DraftKings', sportsbook_key: 'draftkings', provider: 'The Odds API', classification: 'GOVERNED',
    observed_at: '2026-08-30T02:08:45Z', age_minutes: 30000, fresh: false, state: 'STORED_REFERENCE_ONLY', sides, ...opts,
  }
}

function assessment(gameId: string, quotes: AssessedQuote[], withExample = true): GameAssessment {
  return {
    game_id: gameId, sport: 'NFL', quotes,
    binding: { channel: 'PAIRED_POPULATION', status: 'SUPPORTED', binding: { ...BINDING, gameId }, authority: 'PAPER_RESEARCH_EVIDENCE',
      authority_label: 'NFL M1 research/paper probability evidence', moneyline_tie_settlement: 'PUSH',
      winner: { home: 0.6, away: 0.37, tie: 0.03 }, expected_score: { home: 24.1, away: 20.3 }, forecast_created_at: '2026-09-15T09:37:00Z' },
    parlay_lab: withExample ? [{
      legs: [{ market: 'MONEYLINE', selection: 'HOME', tieSettlement: 'PUSH' }, { market: 'TOTAL', selection: 'OVER', line: 44.5 }],
      probabilities: { win: 0.31, loss: 0.66, push: 0.03, void: 0 }, counts: { win: 3100, loss: 6600, push: 300, void: 0 },
      effective_draw_count: 10000, mcse: 0.0046, method: 'PER_DRAW_SAME_GAME_INTERSECTION', display_method: 'PAIRED_DRAW_INTERSECTION',
      fair_american: 223, population: { ...BINDING, gameId }, authority: 'PAPER_RESEARCH_EVIDENCE', authority_label: 'NFL M1',
      parlay: { quote_status: 'SPORTSBOOK_PARLAY_PRICE_UNAVAILABLE', message: 'x', quote: null, implied_probability: null, edge: null, expected_return: null },
    }] : [],
  }
}

function payload(): DailyAssessments {
  return {
    schema_version: 'AISP_DAILY_ASSESSMENTS_V1', date: '2026-09-20', generated_at: '', moneyline_tie_settlement: 'PUSH',
    games: [
      assessment('g1', [
        quote('MONEYLINE', { HOME: side(-150), AWAY: side(130) }),
        quote('TOTAL', { OVER: side(-110), UNDER: side(-110) }, { line: 44.5 }),
      ]),
      assessment('g2', [quote('MONEYLINE', { HOME: side(-120, { edge: 0.031, expected_return: 0.042, edge_reason: null }), AWAY: side(100) },
        { fresh: true, state: 'CURRENT' })], false),
    ],
    research: [{
      game_id: 'cfb1', sport: 'CFB', matchup: 'Coastal Carolina @ Delaware', starts_at: '2026-09-19T15:30:00Z',
      home: { abbreviation: 'CFBD48', name: 'Delaware' }, away: { abbreviation: 'CFBD324', name: 'Coastal Carolina' },
      channels: [{ channel: 'PAIRED_POPULATION', status: 'SUPPORTED', binding: { ...BINDING, sport: 'CFB', gameId: 'cfb1' }, authority: 'RESEARCH_ONLY',
        authority_label: 'College Football paired-residual baseline — research only', winner: { home: 0.7315, away: 0.2685, tie: 0 },
        expected_score: { home: 32.9, away: 22.8 } }],
      price_state: 'NO_GOVERNED_PRICE', price_note: 'Research sport: AISP collects no governed sportsbook price, so no edge is assessed.',
    }],
    parlay_policy: { sportsbook_parlay_quotes_stored: 0, unavailable_message: 'x', creates_decisions: false, creates_positions: false },
    cross_game: { status: 'UNSUPPORTED', code: 'CROSS_GAME_CALIBRATION_NOT_VALIDATED', message: 'x' },
    provider_calls_made: 0,
  }
}

const PERCENT = /\d+(\.\d+)?%/

describe('Parlay Lab', () => {
  it('shows same-game results from the server and keeps cross-game disabled with nothing listed', () => {
    render(<MemoryRouter><ParlayLab data={payload()} coverage={coverage()} /></MemoryRouter>)
    const lab = screen.getByRole('region', { name: 'Parlay Lab' })
    const cross = within(lab).getByLabelText('Cross-game combinations')
    expect(cross).toHaveAttribute('aria-disabled', 'true')
    expect(cross).toHaveTextContent('Cross-game combinations are not yet validated. AISP is collecting prospective calibration and dependency evidence.')
    expect(cross).toHaveTextContent('CROSS_GAME_CALIBRATION_NOT_VALIDATED')
    // No probability, price, edge, return or candidate list under the disabled state.
    expect(cross.textContent).not.toMatch(PERCENT)
    expect(within(cross).queryByRole('list')).toBeNull()
    expect(within(cross).queryAllByRole('button')).toHaveLength(0)
    expect(cross.textContent).not.toMatch(/fair|edge|expected return|[+-]\d{3}\b/i)

    const example = within(lab).getAllByLabelText('Same-game result')[0]
    expect(example).toHaveTextContent('31.0%')
    expect(example).toHaveTextContent('PAIRED_DRAW_INTERSECTION')
    expect(example).toHaveTextContent('10,000')
    expect(example).toHaveTextContent('Model probability available. Sportsbook parlay price unavailable, so edge and expected return cannot be assessed.')
    expect(example.textContent).not.toMatch(/implied|edge [+-]|EV /i)
  })

  it('evaluates a built combination against the issued binding, for one game only', async () => {
    render(<MemoryRouter><ParlayLab data={payload()} coverage={coverage()} /></MemoryRouter>)
    const lab = screen.getByRole('region', { name: 'Parlay Lab' })
    // One game selector: legs can never come from two games.
    expect(within(lab).getAllByRole('combobox', { name: /Game/ })).toHaveLength(1)
    fireEvent.click(within(lab).getByRole('button', { name: 'Add leg' }))
    fireEvent.change(within(lab).getByRole('combobox', { name: 'Market' }), { target: { value: 'TOTAL' } })
    expect(within(lab).getByRole('button', { name: 'Add leg' })).toBeDisabled()
    fireEvent.change(within(lab).getByRole('textbox', { name: 'Total line' }), { target: { value: '44.5' } })
    fireEvent.click(within(lab).getByRole('button', { name: 'Add leg' }))
    fireEvent.click(within(lab).getByRole('button', { name: 'Evaluate same-game combination' }))
    await waitFor(() => expect(calls.some((c) => c.path === '/probability/evaluate')).toBe(true))
    const request = calls.find((c) => c.path === '/probability/evaluate')!.body as Record<string, unknown>
    expect(request).toMatchObject({ crossGameMode: 'REJECT', populationBinding: { ...BINDING, gameId: 'g1' } })
    expect(request.legs).toEqual([
      { market: 'MONEYLINE', selection: 'HOME', tieSettlement: 'PUSH' },
      { market: 'TOTAL', selection: 'OVER', line: 44.5 },
    ])
    const results = await within(lab).findAllByLabelText('Same-game result')
    expect(results.at(-1)).toHaveTextContent('25.0%')
    expect(calls.every((c) => !c.path.includes('cross-game'))).toBe(true)
  })

  it('never multiplies probabilities or exposes the independence calculation in client code', () => {
    for (const source of [parlaySource, boardSource, probabilitySource]) {
      expect(source.length).toBeGreaterThan(1000)
      expect(source).not.toMatch(/INDEPENDENCE_ASSUMPTION|evaluate_cross_game|cross-game['"]\s*,/)
      // No product of two probability values anywhere in these modules.
      expect(source).not.toMatch(/(probabilit\w*|\.win|conditional|no_vig)\s*(\)|\])?\s*\*\s*[\w.(]*(probabilit|\.win|conditional|no_vig)/)
      expect(source).not.toMatch(/reduce\(\s*\(\s*\w+\s*,\s*\w+\s*\)\s*=>\s*\w+\s*\*\s*\w+/)
    }
  })
})

describe('assessment cards', () => {
  const now = clock.now
  const feed = (): AssessmentSource[] => coverage().map((g, i) => ({
    key: g.game_id, sport: 'NFL', matchup: g.matchup ?? '', startsAt: g.starts_at,
    tab: i === 0 ? 'WATCHING' : 'ACTIONABLE', status: i === 0 ? { label: 'Near the threshold', tone: 'gold', detail: 'Edge 1.9 pts below policy' } : { label: 'Qualifies now', tone: 'green', detail: null },
    item: null, game: g,
  }))

  it('groups cards and labels stale, missing and research states without inferring anything', () => {
    const data = payload()
    const groups = classifyAssessments(feed(), data, now)
    expect(groups.QUALIFIED.map((c) => c.key)).toEqual(['g2'])
    expect(groups.CLOSEST.map((c) => c.key)).toEqual(['g1'])
    expect(groups.CLOSEST[0].bestEdge).toBeNull()
    expect(groups.QUALIFIED[0].bestEdge).toBe(0.031)
    render(<MemoryRouter><AssessmentBoard groups={groups} research={data.research} day="2026-09-20" loading={false} /></MemoryRouter>)

    const closest = screen.getByRole('region', { name: 'Closest to qualifying' })
    const moneyline = within(closest).getByLabelText('Moneyline at DraftKings')
    expect(moneyline).toHaveTextContent('Stored reference only')
    expect(moneyline).toHaveTextContent('Price is not current — no edge assessed')
    expect(moneyline).toHaveTextContent('Push')
    expect(moneyline).toHaveTextContent('5.0%')
    expect(moneyline.textContent).not.toMatch(/EV /)
    // Both sides of the total, with the line on each.
    const total = within(closest).getByLabelText('Total at DraftKings')
    expect(total).toHaveTextContent('Over 44.5')
    expect(total).toHaveTextContent('Under 44.5')
    // No spread was quoted: shown as missing, never invented.
    expect(within(closest).getByLabelText('Spread: not quoted')).toHaveTextContent('No spread quote stored')
    for (const name of ['Analyze', 'Simulate', 'Shop Books']) expect(within(closest).getByRole('link', { name })).toBeInTheDocument()
    expect(within(closest).getByRole('link', { name: 'Simulate' })).toHaveAttribute('href', '/games/g1/simulation?sport=NFL')

    const qualified = screen.getByRole('region', { name: 'Qualified opportunities' })
    const current = within(qualified).getByLabelText('Moneyline at DraftKings')
    expect(current).toHaveTextContent('Current')
    expect(current).toHaveTextContent('+3.1 pts')
    expect(current).toHaveTextContent('EV 4.2%')

    const research = screen.getByRole('region', { name: 'Research-only comparisons' })
    expect(research).toHaveTextContent('Research only')
    expect(research).toHaveTextContent('Coastal Carolina at Delaware')
    expect(research).toHaveTextContent('AISP collects no governed sportsbook price')
    expect(research.textContent).not.toMatch(/Edge ·|EV /)
  })

  it('a one-sided quote shows the missing side as not quoted', () => {
    const q = quote('TOTAL', { OVER: side(-105, { no_vig: null, edge_reason: 'NO_VIG_UNAVAILABLE' }) }, { line: 8.5 })
    expect(primaryQuote([q], 'TOTAL')).toBe(q)
    const data = payload()
    data.games[0].quotes = [q]
    const groups = classifyAssessments(feed(), data, now)
    render(<MemoryRouter><AssessmentBoard groups={groups} research={[]} day="2026-09-20" loading={false} /></MemoryRouter>)
    const total = within(screen.getByRole('region', { name: 'Closest to qualifying' })).getByLabelText('Total at DraftKings')
    expect(total).toHaveTextContent('not quoted')
    expect(total).toHaveTextContent('No two-sided price — no edge assessed')
    expect(total).toHaveTextContent('Over 8.5')
  })
})
