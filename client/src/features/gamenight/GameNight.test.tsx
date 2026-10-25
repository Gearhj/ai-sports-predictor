import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Ledger } from '../../services/commandCenter'
import mission from '../../test/gamenight-captured/mission-control-2026-10-18.json'
import ledgerJson from '../../test/gamenight-captured/ledger.json'
import decisions from '../../test/gamenight-captured/decisions-2026-10-18.json'
import scoreboard from '../../test/gamenight-captured/scoreboard-snf.json'
import storyAri from '../../test/gamenight-captured/story-ari-lar.json'
import storyGb from '../../test/gamenight-captured/story-dal-gb.json'
import nhl from '../../test/gamenight-captured/nhl-cbj-min.json'
import wagers from '../../test/gamenight-captured/wager-results-trimmed.json'

const calls: { path: string; init: RequestInit | undefined }[] = []
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T
/** Which Mission Control body a request gets (per test). */
let missionFor: (path: string) => unknown = () => clone(mission)

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string, init?: RequestInit) => {
      calls.push({ path, init })
      if (path.startsWith('/mission-control')) return Promise.resolve(missionFor(path))
      if (path === '/command-center/ledger') return Promise.resolve(clone(ledgerJson))
      if (path === '/command-center/decisions?date=2026-10-18') return Promise.resolve(clone(decisions))
      if (path.startsWith('/command-center/decisions')) {
        return Promise.resolve({ schema_version: 'AISP_COMMAND_CENTER_DECISIONS_V1', date: '', generated_at: '2026-10-19T00:17:13Z', counts: {}, exposure: 0, staked: 0, realized_profit_loss: 0, next_decision_deadline: null, items: [] })
      }
      if (path.startsWith('/scoreboard')) return Promise.resolve(clone(scoreboard))
      if (path === `/probability/games/${storyAri.game.game_id}/story`) return Promise.resolve(clone(storyAri))
      if (path === `/probability/games/${storyGb.game.game_id}/story`) return Promise.resolve(clone(storyGb))
      if (path === '/nhl/games/2026020135') return Promise.resolve(clone(nhl))
      if (path.startsWith('/research/wager-results')) return Promise.resolve(clone(wagers))
      return Promise.reject(new Error(`unexpected ${path}`))
    }),
  }
})

import { GameNight } from './GameNight'
import { clearCache, fetchQuery } from '../../services/queryCache'
import { ledgerKey } from '../../services/commandCenter'
import { ledgerPositions } from './gameNightData'

const ARI = storyAri.game.game_id
const CBJ = 'e3cfbae6-febc-474e-824e-dba3a08c35ca'
const CAPTURE = new Date('2026-10-19T00:26:00Z')

function renderAt(path = '/game-night') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/game-night" element={<GameNight />} /></Routes>
    </MemoryRouter>,
  )
}

function mockMotion(reduce: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: reduce && query.includes('prefers-reduced-motion'), media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  })
}

/** Mission Control for a day with nothing on it (the day after a slate, just past midnight). */
function emptyMission(day: string) {
  const m = clone(mission) as unknown as Record<string, unknown>
  return { ...m, date: day, games: [], sweating: [], counts: { games: 0, live: 0, bets_open: 0, bets_settled: 0, blocked: 0, waiting: 0, staked_usd: 0, profit_usd: 0 } }
}

/** Start from nothing cached, in memory or in browser storage. */
function freshCache() {
  try { window.localStorage.clear() } catch { /* jsdom */ }
  clearCache()
}

/** The captured ledger with one more settlement recorded at 00:25 UTC. */
function ledgerWithSettlement(selection: string, change: number) {
  const next = clone(ledgerJson) as unknown as Ledger
  const pos = next.sports.flatMap((s) => s.positions).find((p) => p.selection === selection)!
  pos.status = 'SETTLED'; pos.result = change > 0 ? 'WIN' : 'LOSS'; pos.profit_loss = change; pos.settled_at = '2026-10-19T00:25:00Z'
  const last = next.combined_series[next.combined_series.length - 1]
  next.combined_series.push({ at: '2026-10-19T00:25:00Z', bankroll: Math.round((last.bankroll + change) * 100) / 100, kind: 'SETTLEMENT', position_id: pos.position_id, change, sport: 'NFL' })
  return next
}

beforeEach(() => {
  calls.length = 0
  missionFor = () => clone(mission)
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(CAPTURE)
  mockMotion(false)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  // @ts-expect-error -- jsdom has no matchMedia; remove the test double
  delete window.matchMedia
})

// Each test renders the whole wall from captured payloads.
describe('Game Night', { timeout: 20_000 }, () => {
  it('is labelled paper research and never fires a settlement moment on first load', async () => {
    renderAt()
    expect(await screen.findByText('PAPER RESEARCH')).toBeInTheDocument()
    expect(screen.getByText(/Nothing on this page can place a wager, trigger a job or change data/)).toBeInTheDocument()
    await screen.findAllByText(/not yet settled/)
    expect(screen.queryByText(/JUST SETTLED/)).not.toBeInTheDocument()
  })

  it('says an observed final with an open position is not yet settled, and uses the server\'s no-feed label for the NHL', async () => {
    renderAt()
    const rail = await screen.findByRole('complementary')
    expect(await within(rail).findByText(/Observed final 33–30/)).toBeInTheDocument()
    expect(within(rail).getAllByText(/not yet settled/).length).toBeGreaterThan(0)
    expect(within(rail).getAllByText('No live feed').length).toBe(2)
    expect(within(rail).getAllByText(/There is no NHL in-game feed/).length).toBe(2)
    // An NHL game in play never shows a score.
    expect(within(rail).queryByText(/0 – 0/)).not.toBeInTheDocument()
  })

  it('replays the exact stored tally and says the order is ours', async () => {
    renderAt(`/game-night?focus=${ARI}`)
    expect(await screen.findByText(/stored pregame draws in a shuffled order/)).toBeInTheDocument()
    expect(screen.getByText(/The order is ours; the finished grid is exactly the model's/)).toBeInTheDocument()
    // The real final against the model, once the (instant, canvas-less) replay finishes.
    expect(await screen.findByText(/579th most likely of 701 scores/)).toBeInTheDocument()
    expect(screen.getByText('2,122')).toBeInTheDocument()
    expect(screen.getAllByText(/W \+\$30\.65/).length).toBeGreaterThan(0)
    // The market is the decision's same-book pair.
    expect(screen.getAllByText(/two prices when AISP decided/).length).toBeGreaterThan(0)
  })

  it('labels the NHL replay as sampled from a regulation-only derived grid', async () => {
    renderAt(`/game-night?focus=${CBJ}`)
    expect(await screen.findByText(/sampled from the model's stored distribution: a regulation-only grid/)).toBeInTheDocument()
    expect(screen.getByText(/which it does not model as scores/)).toBeInTheDocument()
    expect(calls.some((c) => c.path.includes(`/probability/games/${CBJ}`))).toBe(false)
  })

  it('flags a gap over ten points as a model warning and labels Gap Watch exploratory', async () => {
    renderAt()
    expect((await screen.findAllByText('MODEL WARNING')).length).toBeGreaterThan(0)
    expect(screen.getByText('EXPLORATORY')).toBeInTheDocument()
    const gap = screen.getByRole('region', { name: /model-warning scoreboard/i })
    expect(within(gap).getByText('GAP > 10 PTS')).toBeInTheDocument()
    expect(within(gap).getByText('0–5')).toBeInTheDocument()
    expect(within(gap).getByText('4–0')).toBeInTheDocument()
    // The gap is never called an edge or a value; "not an edge" is the only use of the word.
    expect((gap.textContent ?? '').replace(/not an edge/gi, '')).not.toMatch(/\bedge\b|\bvalue\b/i)
  })

  it('under reduced motion draws the tally in one step with its table and no animation frames', async () => {
    mockMotion(true)
    const original = window.requestAnimationFrame
    const raf = vi.fn((cb: FrameRequestCallback) => window.setTimeout(() => cb(performance.now()), 16))
    window.requestAnimationFrame = raf as unknown as typeof window.requestAnimationFrame
    try {
      renderAt(`/game-night?focus=${ARI}`)
      expect((await screen.findAllByText(/the 20 most-drawn scores of 10,000 stored draws/)).length).toBeGreaterThan(0)
      expect(await screen.findByText(/579th most likely/)).toBeInTheDocument()
      expect(raf).not.toHaveBeenCalled()
    } finally {
      window.requestAnimationFrame = original
    }
  })

  it('only reads: GETs, no admin path, no form, no wager control', async () => {
    renderAt()
    await screen.findAllByText(/not yet settled/)
    fireEvent.click(screen.getByRole('button', { name: /Load the season/ }))
    await screen.findByText(/Season gap galaxy/)
    expect(calls.length).toBeGreaterThan(0)
    for (const c of calls) {
      expect(c.path.startsWith('/admin')).toBe(false)
      expect(c.init?.method == null || c.init.method === 'GET').toBe(true)
    }
    expect(document.querySelector('form')).toBeNull()
    for (const b of screen.getAllByRole('button')) {
      expect(b.textContent ?? '').not.toMatch(/place|wager|bet now|submit/i)
      expect(b.getAttribute('aria-label') ?? '').not.toMatch(/place|wager|bet now|submit/i)
    }
  })

  it('plays a lower-third when the ledger records a new settlement while the page is open', async () => {
    renderAt()
    await screen.findAllByText(/not yet settled/)
    const next = clone(ledgerJson) as unknown as Ledger
    const gb = next.sports.find((s) => s.sport === 'NFL')!.positions.find((p) => p.selection === 'GB ML')!
    gb.status = 'SETTLED'; gb.result = 'WIN'; gb.profit_loss = 5; gb.settled_at = '2026-10-19T00:25:00Z'
    next.combined_series.push({ at: '2026-10-19T00:25:00Z', bankroll: 5009.29, kind: 'SETTLEMENT', position_id: gb.position_id, change: 5, sport: 'NFL' })
    await act(async () => { await fetchQuery(ledgerKey(), () => Promise.resolve(next), { force: true }) })
    expect((await screen.findAllByText(/JUST SETTLED/)).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/NEW ALL-TIME HIGH \(previous \$5,006\.71/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Paper research bet; no real money/)).toBeInTheDocument()
    // Announced once, from a live region that holds only the words; the counting panel is hidden from it.
    const overlay = document.querySelector('.gn-moments')!
    expect(overlay.getAttribute('aria-live')).toBeNull()
    expect(document.querySelector('.gn-moment')!.getAttribute('aria-hidden')).toBe('true')
    expect(overlay.querySelector('[aria-live="polite"]')!.textContent).toMatch(/JUST SETTLED: WIN/)
    // The stage cuts to the game that settled.
    expect(screen.getByRole('heading', { level: 2, name: /DAL @ GB/ })).toBeInTheDocument()
  })

  it('never makes a real settlement wait behind a replay reel', async () => {
    renderAt()
    await screen.findAllByText(/not yet settled/)
    fireEvent.click(screen.getByRole('button', { name: 'Replay tonight' }))
    expect((await screen.findAllByText(/REPLAY · real settlement/)).length).toBeGreaterThan(0)
    await act(async () => { await fetchQuery(ledgerKey(), () => Promise.resolve(ledgerWithSettlement('LV ML', 4.5)), { force: true }) })
    expect((await screen.findAllByText(/JUST SETTLED/)).length).toBeGreaterThan(0)
    expect(screen.queryByText(/REPLAY · real settlement/)).not.toBeInTheDocument()
  })

  it('fullscreens the whole document, so the lower-thirds portalled to <body> stay visible', async () => {
    const request = vi.fn(() => Promise.resolve())
    const root = document.documentElement as HTMLElement & { requestFullscreen?: unknown }
    const original = root.requestFullscreen
    Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: true })
    root.requestFullscreen = request
    try {
      renderAt()
      fireEvent.click(await screen.findByRole('button', { name: 'Fullscreen' }))
      expect(request).toHaveBeenCalledTimes(1)
      expect(request.mock.contexts[0]).toBe(document.documentElement)
    } finally {
      root.requestFullscreen = original
      delete (document as { fullscreenEnabled?: boolean }).fullscreenEnabled
    }
  })

  it('after midnight ET keeps last night: the headline and Gap Watch still count the slate that just finished', async () => {
    freshCache()
    vi.setSystemTime(new Date('2026-10-19T04:30:00Z')) // 12:30 AM ET, when a Monday-night game settles
    missionFor = (path) => (path.includes('date=2026-10-19') ? emptyMission('2026-10-19') : clone(mission))
    renderAt()
    const since = Date.parse('2026-10-18T10:00:00Z') // 6 AM ET on the 18th
    const tonight = [...ledgerPositions(ledgerJson as unknown as Ledger).values()]
      .filter((p) => p.status === 'SETTLED' && p.settled_at && Date.parse(p.settled_at) >= since && ['WIN', 'LOSS', 'PUSH'].includes(p.result ?? ''))
    expect(tonight.length).toBeGreaterThan(0)
    const label = await screen.findByText('Tonight:')
    expect(label.parentElement!.textContent).toContain(`${tonight.length} settled`)
    const gap = screen.getByRole('region', { name: /model-warning scoreboard/i })
    expect(await within(gap).findByText('4–0')).toBeInTheDocument()
    expect(within(gap).getByText('0–5')).toBeInTheDocument()
    expect(within(gap).getByText(/10-18 and 10-19 slates/)).toBeInTheDocument()
    expect(within(gap).queryByText(/No funded bet/)).not.toBeInTheDocument()
  })

  it('does not throw with a canvas that cannot draw (the jsdom stub)', async () => {
    const canvas = document.createElement('canvas')
    expect(typeof (canvas.getContext('2d') as unknown as { fillRect?: unknown })?.fillRect).toBe('undefined')
    renderAt(`/game-night?focus=${ARI}`)
    expect(await screen.findByText(/stored pregame draws in a shuffled order/)).toBeInTheDocument()
  })

  it('makes no status claim it cannot back: no typed-in study note, and every empty state waits for the board', async () => {
    freshCache()
    missionFor = () => new Promise(() => {}) // Mission Control has not answered yet
    try {
      renderAt()
      expect(await screen.findByText(/READING TONIGHT.S BOARD/)).toBeInTheDocument()
      expect(screen.getByText('Reading open bets…')).toBeInTheDocument()
      expect(screen.getAllByText(/Reading tonight.s board…/).length).toBeGreaterThan(0)
      const text = document.body.textContent ?? ''
      expect(text).not.toMatch(/STANDBY|not scheduled|none in play|No bet in play|No games on tonight/)
      expect(text).not.toMatch(/gap study running/i)
    } finally {
      freshCache()
    }
  })
})
