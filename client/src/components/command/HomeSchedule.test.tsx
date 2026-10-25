import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgendaEvent } from '../../services/agenda'
import { HomeSchedule, SCHEDULE_VIEW_KEY } from './HomeSchedule'
import type { ScheduleRow } from './TodaysGames'

const at = (iso: string) => Date.parse(iso)
const NOW = at('2026-09-17T15:10:00Z')
const ROWS: ScheduleRow[] = [
  { gameId: 'a', sport: 'MLB', away: 'MIL', home: 'PIT', startsAt: at('2026-09-17T16:35:00Z'), state: null, positions: [] },
  { gameId: 'b', sport: 'MLB', away: 'ATH', home: 'TB', startsAt: at('2026-09-17T17:10:00Z'), state: 'LIVE',
    positions: [{ key: 'p', selection: 'Athletics', american: 155, sportsbook: 'DraftKings', stake: 2.49, result: null }] },
]
const EVENTS: AgendaEvent[] = ROWS.map((r) => ({ key: `${r.gameId}:game`, kind: 'game', sport: r.sport, gameId: r.gameId,
  matchup: `${r.away} @ ${r.home}`, at: r.startsAt, status: 'scheduled', label: 'Game start', detail: '' }))

function renderSchedule(rows = ROWS, events = EVENTS, loading: string | null = null) {
  return render(<MemoryRouter><HomeSchedule rows={rows} events={events} now={NOW} idle={{}} decisionsHref="/decisions"
    withSport={(h) => h} asOf={<span>as of</span>} loading={loading} /></MemoryRouter>)
}

beforeEach(() => { try { window.localStorage.clear() } catch { /* ignore */ } })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Home schedule views', () => {
  it('opens on Timeline with the summary, and shows exactly one view at a time', () => {
    renderSchedule()
    const section = screen.getByRole('region', { name: 'Today’s games' })
    expect(within(section).getByText('Game times and AISP positions · Eastern Time')).toBeInTheDocument()
    expect(within(section).getByText('2 games · 1 AISP position')).toBeInTheDocument()
    const group = within(section).getByRole('group', { name: 'Schedule view' })
    expect(within(group).getByRole('button', { name: 'Timeline' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(group).getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'false')
    expect(section.querySelector('.cc-agenda')).not.toBeNull()
    expect(section.querySelector('.tg-list')).toBeNull()

    fireEvent.click(within(group).getByRole('button', { name: 'List' }))
    expect(within(group).getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'true')
    expect(section.querySelector('.cc-agenda')).toBeNull()
    expect(section.querySelector('.tg-list')).not.toBeNull()
    expect(within(section).getByText('AISP POSITION')).toBeInTheDocument()
    expect(within(section).getAllByText('2 games · 1 AISP position')).toHaveLength(1)
  })

  it('remembers the chosen view in this browser', () => {
    renderSchedule()
    fireEvent.click(screen.getByRole('button', { name: 'List' }))
    expect(window.localStorage.getItem(SCHEDULE_VIEW_KEY)).toBe('list')
    cleanup()
    renderSchedule()
    expect(screen.getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'true')
    expect(document.querySelector('.tg-list')).not.toBeNull()
  })

  it('says so when no position exists', () => {
    renderSchedule([ROWS[0]], [EVENTS[0]])
    expect(screen.getByText('1 game · 0 AISP positions')).toBeInTheDocument()
    expect(screen.getByText('No AISP positions have been opened yet.')).toBeInTheDocument()
  })

  it('uses the List only, without a toggle, on a phone-width section', () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
      { width: 343, height: 600, top: 0, left: 0, right: 343, bottom: 600, x: 0, y: 0, toJSON: () => ({}) } as DOMRect)
    window.localStorage.setItem(SCHEDULE_VIEW_KEY, 'timeline')
    renderSchedule()
    expect(screen.queryByRole('group', { name: 'Schedule view' })).toBeNull()
    expect(document.querySelector('.tg-list')).not.toBeNull()
    expect(document.querySelector('.cc-agenda')).toBeNull()
  })

  it('shows the loading message instead of either view', () => {
    renderSchedule([], [], 'Loading today’s slate…')
    expect(screen.getByText('Loading today’s slate…')).toBeInTheDocument()
    expect(document.querySelector('.cc-agenda, .tg-list')).toBeNull()
  })
})
