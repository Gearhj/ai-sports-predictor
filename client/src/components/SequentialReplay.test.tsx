import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SequentialReplay, basesFromMask } from './SequentialReplay'
import type { ShadowForecastView, ShadowPath, ShadowPathEvent } from '../types/shadow'

function event(overrides: Partial<ShadowPathEvent> & { pa: number }): ShadowPathEvent {
  return {
    inning: 1, half: 'TOP', battingOrderPosition: 1, outcome: 'BIP_OUT',
    outsBefore: 0, outsAfter: 1, baseMaskBefore: 0, baseMaskAfter: 0,
    runsScored: 0, homeScoreAfter: 0, awayScoreAfter: 0,
    pitcherState: 'STARTER', pitchingChange: false,
    ...overrides,
  }
}

/** A short but structurally real path: a run scores, a pitcher changes, extras. */
const EVENTS: ShadowPathEvent[] = [
  event({ pa: 1, outcome: 'STRIKEOUT' }),
  event({ pa: 2, outcome: 'SINGLE', outsBefore: 1, outsAfter: 1, baseMaskAfter: 1 }),
  event({ pa: 3, outcome: 'DOUBLE', outsBefore: 1, outsAfter: 1, baseMaskBefore: 1, baseMaskAfter: 6 }),
  event({
    pa: 4, outcome: 'SINGLE', outsBefore: 1, outsAfter: 1,
    baseMaskBefore: 6, baseMaskAfter: 3, runsScored: 1, awayScoreAfter: 1,
  }),
  event({
    pa: 5, inning: 9, half: 'BOTTOM', outcome: 'BIP_OUT', outsBefore: 2, outsAfter: 3,
    awayScoreAfter: 1, homeScoreAfter: 1, pitcherState: 'TEAM_BULLPEN', pitchingChange: true,
  }),
  event({
    pa: 6, inning: 10, half: 'BOTTOM', outcome: 'STRIKEOUT', outsBefore: 2, outsAfter: 3,
    awayScoreAfter: 2, homeScoreAfter: 1, pitcherState: 'TEAM_BULLPEN',
  }),
]

const PATH: ShadowPath = {
  schema_version: 'AISP_MLB_V18_SEQUENTIAL_PATH_V1',
  forecast_id: 'mlb-v18-test', game_id: 'game-1', engine_id: 'MLB_V18_WHOLE_GAME',
  bundle_sha256: 'a'.repeat(64), path_digest: 'd'.repeat(64),
  master_seed: 20260907, simulation_count: 5000, path_count: 1, paths: [EVENTS],
}

const SHADOW = {
  simulation_count: 5000,
  sides: { AWAY: { starter: 'Test Starter' }, HOME: { starter: 'Home Starter' } },
} as unknown as ShadowForecastView

afterEach(cleanup)

function mount() {
  return render(
    <SequentialReplay path={PATH} shadow={SHADOW} awayTeam="TOR" homeTeam="ATH" />,
  )
}

describe('base occupancy comes from the stored bitmask', () => {
  it.each([
    [0, { first: false, second: false, third: false }],
    [1, { first: true, second: false, third: false }],
    [2, { first: false, second: true, third: false }],
    [4, { first: false, second: false, third: true }],
    [3, { first: true, second: true, third: false }],
    [7, { first: true, second: true, third: true }],
  ])('mask %i', (mask, expected) => {
    expect(basesFromMask(mask)).toEqual(expected)
  })
})

describe('the replay renders the stored state, not a derived one', () => {
  it('opens on the first plate appearance with its exact recorded state', () => {
    mount()
    expect(screen.getByText('1 of 6')).toBeInTheDocument()
    expect(screen.getByText('Strikeout')).toBeInTheDocument()
    // Outs, score and bases are the values recorded AFTER this plate appearance.
    expect(screen.getByRole('img', { name: /Top of the inning, 1 out\. Bases empty\./ }))
      .toBeInTheDocument()
  })

  it('advances through legal base, out and score transitions', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: /next plate appearance/i }))
    expect(screen.getByRole('img', { name: /Runners on first\./ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /next plate appearance/i }))
    expect(screen.getByRole('img', { name: /Runners on second, third\./ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /next plate appearance/i }))
    // A run scored on this PA and the away score moved with it.
    const facts = screen.getByText('Runs on this PA').parentElement!
    expect(facts).toHaveTextContent('1')
    expect(screen.getByRole('img', { name: /Runners on first, second\./ })).toBeInTheDocument()
  })

  it('names the pitcher by identity only while the state is STARTER', async () => {
    const user = userEvent.setup()
    mount()
    // Top of the inning: the home team's starter is on the mound.
    expect(screen.getByText('Home Starter')).toBeInTheDocument()
    for (let step = 0; step < 4; step += 1) {
      await user.click(screen.getByRole('button', { name: /next plate appearance/i }))
    }
    expect(screen.getByText(/team bullpen/i)).toBeInTheDocument()
    expect(screen.queryByText('Home Starter')).not.toBeInTheDocument()
  })

  it('shows the batting-order position and says identity is not persisted', () => {
    mount()
    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText(/batter identity is not persisted/i)).toBeInTheDocument()
  })

  it('reports extra innings and the final result from the path itself', async () => {
    const user = userEvent.setup()
    mount()
    for (let step = 0; step < 5; step += 1) {
      await user.click(screen.getByRole('button', { name: /next plate appearance/i }))
    }
    expect(screen.getByText(/Extra innings — this path reached inning 10/)).toBeInTheDocument()
    expect(screen.getByText(/Final in this path: TOR 2 – ATH 1 \(10 innings\)/)).toBeInTheDocument()
  })
})

describe('controls', () => {
  it('jumps to a pitching change and to an inning', async () => {
    const user = userEvent.setup()
    mount()
    const changes = screen.getByRole('group', { name: /jump to a pitching change/i })
    await user.click(within(changes).getByRole('button', { name: 'PA 5' }))
    expect(screen.getByText('5 of 6')).toBeInTheDocument()
    expect(screen.getByText('Yes — new pitcher this PA')).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText(/jump to inning/i), '1')
    expect(screen.getByText('1 of 6')).toBeInTheDocument()
  })

  it('restarts and disables navigation at the boundaries', async () => {
    const user = userEvent.setup()
    mount()
    expect(screen.getByRole('button', { name: /previous plate appearance/i })).toBeDisabled()
    for (let step = 0; step < 5; step += 1) {
      await user.click(screen.getByRole('button', { name: /next plate appearance/i }))
    }
    expect(screen.getByRole('button', { name: /next plate appearance/i })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /restart replay/i }))
    expect(screen.getByText('1 of 6')).toBeInTheDocument()
  })

  it('plays forward on a timer and pauses on request', async () => {
    vi.useFakeTimers()
    try {
      render(<SequentialReplay path={PATH} shadow={SHADOW} awayTeam="TOR" homeTeam="ATH" />)
      act(() => { screen.getByRole('button', { name: /play replay/i }).click() })
      // 340ms per plate appearance at 1x, so a 74-PA game reviews in ~25s.
      act(() => { vi.advanceTimersByTime(340) })
      expect(screen.getByText('2 of 6')).toBeInTheDocument()
      act(() => { vi.advanceTimersByTime(340) })
      expect(screen.getByText('3 of 6')).toBeInTheDocument()
      act(() => { screen.getByRole('button', { name: /pause replay/i }).click() })
      act(() => { vi.advanceTimersByTime(2000) })
      expect(screen.getByText('3 of 6')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('offers every declared speed', () => {
    mount()
    const speeds = screen.getByRole('group', { name: /replay speed/i })
    expect(within(speeds).getAllByRole('button').map((b) => b.textContent))
      .toEqual(['0.5×', '1×', '2×', '4×'])
  })
})

describe('provenance', () => {
  it('states that the path is one member of the simulated population', () => {
    mount()
    expect(screen.getByText(/Representative path selected from 5,000 completed/))
      .toBeInTheDocument()
    expect(screen.getByText(/not a prediction of the exact play sequence/))
      .toBeInTheDocument()
    expect(screen.getByText(/No simulation is run in the browser/)).toBeInTheDocument()
  })

  it('renders nothing to replay when the path is empty', () => {
    render(
      <SequentialReplay
        path={{ ...PATH, paths: [[]] }}
        shadow={SHADOW}
        awayTeam="TOR"
        homeTeam="ATH"
      />,
    )
    expect(screen.getByText(/No sequential path is persisted/)).toBeInTheDocument()
  })
})

describe('reduced motion', () => {
  it('opens on the final state and still allows manual navigation', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('prefers-reduced-motion'),
      media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
      dispatchEvent: () => false,
    }))
    try {
      mount()
      // No animation is required to see the outcome.
      expect(screen.getByText('6 of 6')).toBeInTheDocument()
      expect(screen.getByText(/Final in this path: TOR 2 – ATH 1/)).toBeInTheDocument()
      // Manual navigation is preserved.
      await user.click(screen.getByRole('button', { name: /previous plate appearance/i }))
      expect(screen.getByText('5 of 6')).toBeInTheDocument()
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
