import { act, cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearCache, currentScope } from '../../services/queryCache'
import type { ShowBoardResponse } from '../../services/showBoard'
import { draftPartial, frozenFull } from '../../test/showBoardFixtures'
import css from './show.css?raw'

let current: ShowBoardResponse | null = null
let hang = false
const requests: string[] = []

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string) => {
      requests.push(path)
      if (hang || !current) return new Promise(() => {})
      if (path.startsWith('/show-board')) return Promise.resolve(current)
      return Promise.reject(new Error(`unexpected ${path}`))
    }),
  }
})

import { ShowBoardPage } from '../../pages/ShowBoardPage'

afterEach(() => { cleanup(); clearCache(); current = null; hang = false; requests.length = 0 })

async function renderBoard(fixture: ShowBoardResponse, url = '/show-board?date=2026-09-17') {
  current = fixture
  render(<MemoryRouter initialEntries={[url]}><Routes><Route path="/show-board" element={<ShowBoardPage />} /></Routes></MemoryRouter>)
  return screen.findByRole('region', { name: 'Show Board' })
}

describe('Show Board', () => {
  it('draft: readiness counts, honest pending states, never a final disposition', async () => {
    const board = await renderBoard(draftPartial(Date.now()))
    expect(within(board).getByRole('heading', { level: 1 })).toHaveTextContent('Show board · Thursday, September 17')
    expect(within(board).getAllByText('Draft show board').length).toBeGreaterThan(0)
    expect(within(board).getAllByText('Not a final wagering decision').length).toBeGreaterThan(0)
    expect(within(board).getAllByText('Preview forecast awaiting producer')).toHaveLength(4)
    expect(within(board).getByText(/Stale only · captured/)).toBeInTheDocument()
    expect(within(board).getByText('One side only')).toBeInTheDocument()
    expect(board.textContent).not.toMatch(/NO BET|adjusted edge/i)
    expect(requests[0]).toBe('/show-board?date=2026-09-17')
  })

  it('top preview opportunities show only valid comparisons, ranked, never padded', async () => {
    const draft = await renderBoard(draftPartial(Date.now()))
    const draftCards = within(within(draft).getByRole('region', { name: /Top preview opportunities/ })).queryAllByRole('listitem')
    expect(draftCards).toHaveLength(1)
    cleanup()
    clearCache()
    const frozen = await renderBoard(frozenFull(Date.now()))
    const cards = within(within(frozen).getByRole('region', { name: /Top preview opportunities/ })).getAllByRole('listitem')
    expect(cards).toHaveLength(4)
    expect(cards.map((c) => c.getAttribute('aria-label')!.split('.')[0])).toEqual(['1', '2', '3', '4'])
    expect(within(cards[0]).getByText('SHOW PREVIEW — NOT FINAL')).toBeInTheDocument()
    expect(within(cards[0]).getByTitle('Preview model-market difference')).toBeInTheDocument()
    const rail = within(cards[0]).getAllByRole('img').find((el) => /win probability \(preview\)/.test(el.getAttribute('aria-label') ?? ''))
    expect(rail?.querySelector('[tabindex="0"]')).not.toBeNull()
  })

  it('frozen: snapshot identity, all four sports in the rundown, preview versus final stated neutrally', async () => {
    const board = await renderBoard(frozenFull(Date.now()))
    expect(within(board).getAllByText('Frozen show snapshot').length).toBeGreaterThan(0)
    expect(within(board).getByTitle(/^c0ffee/)).toBeInTheDocument()
    for (const sport of ['MLB rundown', 'NFL rundown', 'NBA rundown', 'CFB rundown']) {
      expect(within(board).getByRole('table', { name: sport })).toBeInTheDocument()
    }
    const pvf = within(board).getByRole('region', { name: /Preview versus final/ })
    expect(within(pvf).getByText('Selection changed')).toBeInTheDocument()
    expect(within(pvf).getByText(/settled · \$12\.50 · WIN/)).toBeInTheDocument()
    expect(within(pvf).getByText('none recorded')).toBeInTheDocument()
    expect(within(board).getByText('not in AISP schedule')).toBeInTheDocument()
  })

  it('evidence is lazy: a game detail is requested only when opened', async () => {
    const board = await renderBoard(frozenFull(Date.now()))
    expect(requests.some((p) => p.startsWith('/show-board/game'))).toBe(false)
    const summary = within(board).getAllByText(/^Evidence · /)[0]
    expect(summary.tagName).toBe('SUMMARY')
  })

  it('renders the cached board immediately while the refresh is still pending', async () => {
    const cached = frozenFull(Date.now())
    const scope = currentScope().split(':').slice(1).join(':')
    window.localStorage.setItem(`aisp-query:v1:${scope}|show-board?date=2026-09-17`,
      JSON.stringify({ data: cached, updatedAt: Date.now() - 60_000, asOf: Date.now() - 60_000, staleTimeMs: 30_000 }))
    hang = true
    await act(async () => {
      render(<MemoryRouter initialEntries={['/show-board?date=2026-09-17']}><Routes><Route path="/show-board" element={<ShowBoardPage />} /></Routes></MemoryRouter>)
    })
    expect(screen.getAllByText('Frozen show snapshot').length).toBeGreaterThan(0)
    expect(requests).toContain('/show-board?date=2026-09-17')
  })

  it('print stylesheet hides navigation and keeps the rundown on white', () => {
    const print = css.slice(css.indexOf('@media print'))
    expect(print).toMatch(/\.sidebar/)
    expect(print).toMatch(/\.workspace-toolbar/)
    expect(print).toMatch(/\.sb-evidence/)
    expect(print).toMatch(/background: #fff/)
  })
})
