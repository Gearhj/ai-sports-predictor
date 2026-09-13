/**
 * The theater as a whole: state, controls, keyboard, and the boundary between
 * what the record supports and what the screen may claim.
 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BASE_STEP_MS, DiamondTheaterShell } from './DiamondTheaterShell'
import type { ShadowForecastView, ShadowPath, ShadowPathEvent } from '../../types/shadow'

function event(overrides: Partial<ShadowPathEvent> & { pa: number }): ShadowPathEvent {
  return {
    inning: 1, half: 'TOP', battingOrderPosition: 1, outcome: 'BIP_OUT',
    outsBefore: 0, outsAfter: 1, baseMaskBefore: 0, baseMaskAfter: 0,
    runsScored: 0, homeScoreAfter: 0, awayScoreAfter: 0,
    pitcherState: 'STARTER', pitchingChange: false,
    ...overrides,
  }
}

/** A short path that still exercises every transition class. */
const EVENTS: ShadowPathEvent[] = [
  event({ pa: 1, outcome: 'STRIKEOUT', outsAfter: 1 }),
  event({ pa: 2, outcome: 'SINGLE', outsBefore: 1, outsAfter: 1, baseMaskAfter: 1 }),
  event({
    pa: 3, outcome: 'DOUBLE', outsBefore: 1, outsAfter: 1,
    baseMaskBefore: 1, baseMaskAfter: 6,
  }),
  event({
    pa: 4, outcome: 'SINGLE', outsBefore: 1, outsAfter: 1,
    baseMaskBefore: 6, baseMaskAfter: 5, runsScored: 1, awayScoreAfter: 1,
  }),
  event({
    pa: 5, inning: 5, half: 'BOTTOM', outcome: 'BIP_OUT', outsBefore: 2, outsAfter: 3,
    awayScoreAfter: 1, homeScoreAfter: 1, pitcherState: 'TEAM_BULLPEN', pitchingChange: true,
  }),
  event({
    pa: 6, inning: 10, half: 'BOTTOM', outcome: 'STRIKEOUT', outsBefore: 2, outsAfter: 3,
    awayScoreAfter: 2, homeScoreAfter: 1, pitcherState: 'TEAM_BULLPEN',
  }),
]

const PATH: ShadowPath = {
  schema_version: 'AISP_MLB_V18_SEQUENTIAL_PATH_V1',
  forecast_id: 'mlb-v18-1', game_id: 'game-1', engine_id: 'MLB_V18_WHOLE_GAME',
  bundle_sha256: 'a'.repeat(64), path_digest: 'd'.repeat(64),
  master_seed: 20260907, simulation_count: 5000, path_count: 1, paths: [EVENTS],
}

const SHADOW = {
  simulation_count: 5000,
  monte_carlo_95_half_width: 0.013736,
  sides: { AWAY: { starter: 'Away Starter' }, HOME: { starter: 'Home Starter' } },
  population: {
    simulation_count: 5000, home_win_probability: 43.34, away_win_probability: 56.66,
    expected_home_runs: 3.4694, expected_away_runs: 4.423, expected_total_runs: 7.8924,
    expected_run_differential: -0.9536, one_run_probability: 0.3094,
    extra_inning_probability: 0.1074, pitching_change_rate_home: 1,
    pitching_change_rate_away: 0.9998, monte_carlo_standard_error: 0.007008,
    monte_carlo_95_half_width: 0.013736, total_run_distribution: null,
  },
} as unknown as ShadowForecastView

afterEach(cleanup)

function mount(path = PATH) {
  return render(
    <DiamondTheaterShell path={path} shadow={SHADOW} awayTeam="TOR" homeTeam="ATH" />,
  )
}

function seek(index: number) {
  const slider = screen.getByRole('slider')
  fireEvent.change(slider, { target: { value: String(index) } })
}

describe('the scorebug reflects persisted state', () => {
  it('opens on the first plate appearance', () => {
    mount()
    const bug = screen.getByRole('group', { name: /scoreboard/i })
    expect(bug).toHaveTextContent('PA 1 of 6')
    expect(bug).toHaveTextContent('TOR')
    expect(bug).toHaveTextContent('ATH')
    expect(bug).toHaveTextContent('1 out')
    expect(bug).toHaveTextContent('In progress')
  })

  it('tracks score, outs, inning and base state through the path', () => {
    mount()
    seek(3)
    const bug = screen.getByRole('group', { name: /scoreboard/i })
    // Run scored on PA 4: away 1, and runners are on first and third.
    expect(bug).toHaveTextContent('1')
    expect(within(bug).getByRole('img', { name: /runner on first, runner on third/i }))
      .toBeInTheDocument()
  })

  it('names the pitcher only while the governed state is STARTER', () => {
    mount()
    // Top of the inning: the home club's starter is pitching.
    expect(screen.getByText('Home Starter')).toBeInTheDocument()
    seek(4)
    expect(screen.getByText('Team bullpen')).toBeInTheDocument()
    expect(screen.queryByText('Home Starter')).not.toBeInTheDocument()
  })

  it('reports extra innings and then the final from the path itself', () => {
    mount()
    seek(5)
    expect(screen.getByRole('group', { name: /scoreboard/i })).toHaveTextContent('Final')
    expect(screen.getByText(/Final in this simulated path/i)).toBeInTheDocument()
    expect(screen.getByText('TOR 2 – ATH 1')).toBeInTheDocument()
    expect(screen.getByText(/10 innings · extra innings/)).toBeInTheDocument()
  })
})

describe('the field shows the persisted base state', () => {
  it('draws a token for every occupied base and none otherwise', () => {
    const { container } = mount()
    expect(container.querySelectorAll('.bp-occupied rect')).toHaveLength(0)
    seek(1)
    expect(container.querySelectorAll('.bp-occupied rect')).toHaveLength(1)
    seek(2)
    expect(container.querySelectorAll('.bp-occupied rect')).toHaveLength(2)
  })

  it('describes the field state for assistive technology', () => {
    mount()
    seek(2)
    expect(screen.getByRole('img', { name: /Top of inning 1, 1 out\. 2 runner\(s\) on base\./ }))
      .toBeInTheDocument()
  })

  it('announces the outcome without inventing how it happened', () => {
    mount()
    seek(3)
    expect(screen.getByText('Single')).toBeInTheDocument()
    expect(screen.getByText('+1 run')).toBeInTheDocument()
    const overlay = document.querySelector('.outcome-overlay')!
    for (const forbidden of [/fly ball/i, /line drive/i, /left field/i, /fastball/i]) {
      expect(overlay.textContent).not.toMatch(forbidden)
    }
  })

  it('shows a pitching change as a broadcast transition', () => {
    mount()
    seek(4)
    expect(screen.getByText('Pitching change')).toBeInTheDocument()
    expect(screen.getByText(/Team bullpen enters · governed pitcher state/))
      .toBeInTheDocument()
  })
})

describe('the population summary is fixed and separate', () => {
  it('shows the population values from the payload, not the replay', () => {
    mount()
    const overlay = screen.getByLabelText(/Simulated population summary/i)
    expect(within(overlay).getByText('56.7%')).toBeInTheDocument()
    expect(within(overlay).getByText('43.3%')).toBeInTheDocument()
    expect(within(overlay).getByText('4.42 – 3.47')).toBeInTheDocument()
    expect(within(overlay).getByText('7.89 total runs')).toBeInTheDocument()
    expect(within(overlay).getByText('30.9%')).toBeInTheDocument()
    expect(within(overlay).getByText('10.7%')).toBeInTheDocument()
    expect(within(overlay).getByText('5,000')).toBeInTheDocument()
    expect(within(overlay).getByText(/±1.37 pts 95% sampling/)).toBeInTheDocument()
  })

  it('does not change as the replay advances', () => {
    mount()
    const before = screen.getByLabelText(/Simulated population summary/i).textContent
    seek(4)
    expect(screen.getByLabelText(/Simulated population summary/i).textContent).toBe(before)
  })

  it('never shows Gen 1 evidence', () => {
    const { container } = mount()
    expect(container.textContent).not.toMatch(/20,000/)
    expect(container.textContent).not.toMatch(/20250601/)
    expect(container.textContent).not.toMatch(/Gen 1/)
  })

  it('states that the path is one member of the population', () => {
    mount()
    expect(screen.getByText(/One retained simulated game out of 5,000/)).toBeInTheDocument()
    expect(screen.getByText(/not a prediction of the play sequence/)).toBeInTheDocument()
  })

  it('does not call the path representative or typical', () => {
    // The package retains one path and records no selection method, so neither
    // word is supported by anything stored.
    const { container } = mount()
    const text = container.textContent ?? ''
    // The word may appear in a DENIAL ("not a typical game"); what must never
    // appear is the affirmative claim, which nothing retained supports.
    expect(text).not.toMatch(/representative/i)
    expect(text).not.toMatch(/(?<!not a )typical game/i)
    expect(text).toMatch(/not a typical game/i)
    expect(screen.getByText(/records no method for selecting which one was kept/))
      .toBeInTheDocument()
  })

  it('marks the animation as one example and says what playback is doing', () => {
    mount()
    expect(screen.getByTestId('theater-example-label')).toHaveTextContent('One simulated game')
    // In words, not inferred from a play/pause glyph.
    const status = screen.getByTestId('theater-playback-status')
    expect(status.textContent).toMatch(/Simulation playing|Paused|Simulation complete/)
    expect(screen.getByText(/not the live game and not a predicted sequence/))
      .toBeInTheDocument()
  })
})

describe('the feed', () => {
  it('shows completed plays newest first and never the future', () => {
    mount()
    seek(2)
    const feed = screen.getByLabelText(/Simulated game feed/i)
    const items = within(feed).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Double — runners on second and third')
    // Nothing from PA 4 onward has happened yet.
    expect(feed.textContent).not.toMatch(/One run scores/)
  })

  it('adds inning and pitching-change entries in place', () => {
    mount()
    seek(5)
    const feed = screen.getByLabelText(/Simulated game feed/i)
    expect(feed).toHaveTextContent('Game moves to extra innings')
  })

  it('states that runner movement is inferred', () => {
    mount()
    expect(screen.getByText(/Runner movement between bases is inferred/i)).toBeInTheDocument()
  })
})

describe('controls', () => {
  it('steps, restarts and stops at the boundaries', async () => {
    const user = userEvent.setup()
    mount()
    expect(screen.getByRole('button', { name: /previous plate appearance/i })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /next plate appearance/i }))
    expect(screen.getByRole('slider')).toHaveValue('1')
    seek(5)
    expect(screen.getByRole('button', { name: /next plate appearance/i })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /restart replay/i }))
    expect(screen.getByRole('slider')).toHaveValue('0')
  })

  it('plays on a timer at the declared pace and pauses', () => {
    vi.useFakeTimers()
    try {
      mount()
      act(() => { screen.getByRole('button', { name: /play replay/i }).click() })
      act(() => { vi.advanceTimersByTime(BASE_STEP_MS) })
      expect(screen.getByRole('slider')).toHaveValue('1')
      act(() => { screen.getByRole('button', { name: /pause replay/i }).click() })
      act(() => { vi.advanceTimersByTime(BASE_STEP_MS * 4) })
      expect(screen.getByRole('slider')).toHaveValue('1')
    } finally {
      vi.useRealTimers()
    }
  })

  it('honours the speed multiplier', () => {
    vi.useFakeTimers()
    try {
      mount()
      act(() => { screen.getByRole('button', { name: '4×' }).click() })
      act(() => { screen.getByRole('button', { name: /play replay/i }).click() })
      act(() => { vi.advanceTimersByTime(BASE_STEP_MS / 4) })
      expect(screen.getByRole('slider')).toHaveValue('1')
    } finally {
      vi.useRealTimers()
    }
  })

  it('reviews a 74-plate-appearance path in roughly 20-30 seconds at 1x', () => {
    // 74 steps at the base pace: the brief's target window.
    const seconds = (74 * BASE_STEP_MS) / 1000
    expect(seconds).toBeGreaterThanOrEqual(20)
    expect(seconds).toBeLessThanOrEqual(30)
  })

  it('offers every declared speed and jumps to each pitching change', async () => {
    const user = userEvent.setup()
    mount()
    const speeds = screen.getByRole('group', { name: /replay speed/i })
    expect(within(speeds).getAllByRole('button').map((button) => button.textContent))
      .toEqual(['0.5×', '1×', '2×', '4×'])

    const changes = screen.getByRole('group', { name: /jump to a pitching change/i })
    await user.click(within(changes).getByRole('button', { name: 'PA 5' }))
    expect(screen.getByRole('slider')).toHaveValue('4')
  })

  it('marks scoring plays and pitching changes on the timeline', () => {
    const { container } = mount()
    expect(container.querySelectorAll('.transport__mark--score')).toHaveLength(1)
    expect(container.querySelectorAll('.transport__mark--change')).toHaveLength(1)
  })
})

describe('keyboard', () => {
  it('supports space, arrows and home', () => {
    vi.useFakeTimers()
    try {
      mount()
      fireEvent.keyDown(window, { key: 'ArrowRight' })
      expect(screen.getByRole('slider')).toHaveValue('1')
      fireEvent.keyDown(window, { key: 'ArrowLeft' })
      expect(screen.getByRole('slider')).toHaveValue('0')

      fireEvent.keyDown(window, { key: ' ' })
      act(() => { vi.advanceTimersByTime(BASE_STEP_MS) })
      expect(screen.getByRole('slider')).toHaveValue('1')
      fireEvent.keyDown(window, { key: ' ' })

      fireEvent.keyDown(window, { key: 'ArrowRight' })
      fireEvent.keyDown(window, { key: 'Home' })
      expect(screen.getByRole('slider')).toHaveValue('0')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('theater mode', () => {
  it('expands and exits by button or Escape', async () => {
    const user = userEvent.setup()
    const { container } = mount()
    await user.click(screen.getByRole('button', { name: /theater mode/i }))
    expect(container.querySelector('.theater--full')).toBeTruthy()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(container.querySelector('.theater--full')).toBeFalsy()

    await user.click(screen.getByRole('button', { name: /theater mode/i }))
    await user.click(screen.getByRole('button', { name: /exit theater mode/i }))
    expect(container.querySelector('.theater--full')).toBeFalsy()
  })
})

describe('reduced motion', () => {
  it('opens on the final state and keeps manual navigation', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('prefers-reduced-motion'),
      media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
      dispatchEvent: () => false,
    }))
    try {
      const { container } = mount()
      expect(screen.getByText(/Final in this simulated path/i)).toBeInTheDocument()
      // Tokens do not animate in reduced motion.
      expect(container.querySelector('.bp-runner--static, .bp-runners')).toBeTruthy()
      await user.click(screen.getByRole('button', { name: /previous plate appearance/i }))
      expect(screen.getByRole('slider')).toHaveValue('4')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('an empty path', () => {
  it('states there is nothing to replay rather than drawing a game', () => {
    mount({ ...PATH, paths: [[]] })
    expect(screen.getByText(/No sequential path is persisted/i)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: /scoreboard/i })).not.toBeInTheDocument()
  })
})

describe('the broadcast frame', () => {
  /**
   * The park's own extremes: the warning-track crown behind centre field and
   * the foul-grass corners at the front. Both must be inside the drawn frame,
   * and the frame must be fitted rather than sliced, or the outfield is cropped.
   */
  const TOP_OF_PARK = 32
  const BOTTOM_OF_PARK = 664

  it('draws the whole field and never crops it', () => {
    mount()
    const field = document.querySelector('.theater__field')!
    const [minX, minY, width, height] = (field.getAttribute('viewBox') ?? '')
      .split(' ').map(Number)

    expect(field.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet')
    expect(minY).toBeLessThanOrEqual(TOP_OF_PARK)
    expect(minY + height).toBeGreaterThanOrEqual(BOTTOM_OF_PARK)
    // Both foul poles and both outfield corners are within the horizontal span.
    expect(minX).toBeLessThanOrEqual(52)
    expect(minX + width).toBeGreaterThanOrEqual(948)
  })
})
