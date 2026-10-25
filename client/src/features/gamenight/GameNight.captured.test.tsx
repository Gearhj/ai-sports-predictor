/**
 * Contract guard: render the wall from real GET bodies captured read-only from
 * production at 00:25Z on 2026-10-19 (trimmed where large) and check that no
 * text shows a value the page expected and did not get, or mis-decoded UTF-8.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const captured = import.meta.glob('../../test/gamenight-captured/*.json', { eager: true, import: 'default' }) as Record<string, unknown>
const files = Object.fromEntries(Object.entries(captured).map(([path, body]) => [path.split('/').pop() as string, body]))
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T
const storyById = new Map<string, unknown>()
for (const [name, body] of Object.entries(files)) {
  if (name.startsWith('story-')) storyById.set((body as { game: { game_id: string } }).game.game_id, body)
}

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string) => {
      if (path.startsWith('/mission-control')) return Promise.resolve(clone(files['mission-control-2026-10-18.json']))
      if (path === '/command-center/ledger') return Promise.resolve(clone(files['ledger.json']))
      if (path.startsWith('/command-center/decisions')) return Promise.resolve(clone(files['decisions-2026-10-18.json']))
      if (path.startsWith('/scoreboard')) return Promise.resolve(clone(files['scoreboard-snf.json']))
      const story = /^\/probability\/games\/([^/]+)\/story$/.exec(path)
      if (story && storyById.has(story[1])) return Promise.resolve(clone(storyById.get(story[1])))
      if (path.startsWith('/nhl/games/')) return Promise.resolve(clone(files['nhl-cbj-min.json']))
      if (path.startsWith('/research/wager-results')) return Promise.resolve(clone(files['wager-results-trimmed.json']))
      return Promise.reject(new Error(`unexpected ${path}`))
    }),
  }
})

import { GameNight } from './GameNight'

/** Text that only appears when a field was missing or mistyped, or UTF-8 was decoded as Latin-1. */
const BROKEN = /undefined|NaN|Invalid Date|\[object Object\]|Â|â€/

const FOCUS = [
  ['default (the director’s pick)', ''],
  ['NFL ARI @ LAR', 'a7b18ede-f71c-499b-b1b3-07c6fe35cf54'],
  ['MLB TB @ CLE', '95638a9f-5342-46e2-8332-017c050ee35a'],
  ['NHL CBJ @ MIN', 'e3cfbae6-febc-474e-824e-dba3a08c35ca'],
] as const

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-19T00:26:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('Game Night renders captured production payloads', { timeout: 30_000 }, () => {
  it('has the captured set', () => {
    for (const name of ['mission-control-2026-10-18.json', 'ledger.json', 'decisions-2026-10-18.json', 'scoreboard-snf.json', 'nhl-cbj-min.json', 'wager-results-trimmed.json']) {
      expect(files[name]).toBeTruthy()
    }
    expect(storyById.size).toBeGreaterThanOrEqual(3)
  })

  it.each(FOCUS)('%s: every region renders without a broken value', async (_label, focus) => {
    const errors = vi.spyOn(console, 'error')
    render(
      <MemoryRouter initialEntries={[`/game-night${focus ? `?focus=${focus}` : ''}`]}>
        <Routes><Route path="/game-night" element={<GameNight />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findAllByText(/not yet settled/)
    await screen.findByText(/Forecast made/)
    fireEvent.click(screen.getByRole('button', { name: /Load the season/ }))
    await screen.findByText(/Season gap galaxy/)
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(BROKEN)
    const labels = [...document.querySelectorAll('[aria-label]')].map((e) => e.getAttribute('aria-label') ?? '').join(' | ')
    expect(labels).not.toMatch(BROKEN)
    expect(screen.queryByText(/could not be displayed/)).not.toBeInTheDocument()
    expect(errors.mock.calls.filter((c) => /failed to render/.test(String(c[0])))).toEqual([])
    errors.mockRestore()
  })
})
