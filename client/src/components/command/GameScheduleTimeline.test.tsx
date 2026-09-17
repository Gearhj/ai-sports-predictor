import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { GameScheduleTimeline, groupGames, hourTicks, type ScheduleGame } from './GameScheduleTimeline'

afterEach(cleanup)

const at = (hhmm: string) => Date.parse(`2026-09-17T${hhmm}:00-04:00`)
const game = (id: string, hhmm: string, matchup: string, stake: number | null | undefined = undefined): ScheduleGame => ({
  gameId: id, sport: 'MLB', matchup, startsAt: at(hhmm), position: stake === undefined ? null : { stake },
})
const SLATE: ScheduleGame[] = [
  game('a', '12:35', 'MIL @ PIT', 2.49),
  game('b', '12:40', 'LAD @ CIN'),
  game('c', '13:10', 'ATH @ TB'),
  game('d', '19:10', 'KC @ HOU'),
  game('e', '19:15', 'PHI @ NYM'),
  game('f', '19:15', 'SD @ COL'),
  game('g', '21:38', 'MIN @ LAA'),
  { gameId: 'n', sport: 'NFL', matchup: 'DET @ BUF', startsAt: at('20:15'), position: null },
]

describe('home schedule grouping and axis', () => {
  it('groups games starting within ten minutes and counts positions', () => {
    const groups = groupGames(SLATE)
    const mlb = groups.filter((g) => g.sport === 'MLB')
    expect(mlb.map((g) => g.games.length)).toEqual([2, 1, 3, 1])
    expect(mlb[0].positions).toBe(1)
    expect(groups.filter((g) => g.sport === 'NFL')).toHaveLength(1)
  })

  it('never labels more than six hours, on sensible whole-hour steps', () => {
    const ticks = hourTicks(at('11:50'), at('22:53'))
    expect(ticks.length).toBeLessThanOrEqual(6)
    const hours = ticks.map((t) => Number(new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23' }).format(t)))
    const steps = hours.slice(1).map((h, i) => h - hours[i])
    expect(new Set(steps).size).toBe(1)
    expect(steps[0]).toBeGreaterThanOrEqual(2)
    expect(hourTicks(at('18:00'), at('21:00')).length).toBeLessThanOrEqual(6)
  })
})

describe('home schedule chart', () => {
  it('shows only game starts and positions, with two visual states', () => {
    render(<MemoryRouter><GameScheduleTimeline games={SLATE} now={at('09:40')} /></MemoryRouter>)
    const chart = screen.getByRole('group', { name: /Today's games: 8 across 2 sports; 1 with an AISP position/ })
    // No operational glyphs or legend entries.
    expect(document.body.textContent).not.toMatch(/Collection|Decision window|Forecast|Settlement|[◆▲▮★]/)
    expect(within(chart).getAllByRole('button').length).toBe(5)
    expect(document.querySelectorAll('.hs-marker.is-position')).toHaveLength(1)
    const legend = screen.getByRole('list', { name: 'Legend' })
    expect(within(legend).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Scheduled game', 'AISP position'])
    // A grouped marker says how many games and when, and lists them on Enter.
    const group = within(chart).getByRole('button', { name: /^3 games · 7:10 PM/ })
    fireEvent.keyDown(group, { key: 'Enter' })
    const list = screen.getByRole('region', { name: '3 games at 7:10 PM' })
    expect(list).toHaveTextContent('KC @ HOU')
    expect(list).toHaveTextContent('SD @ COL')
    // The position group names the stake.
    expect(within(chart).getByRole('button', { name: /^2 games · 12:35 PM, 1 POSITION/ })).toBeInTheDocument()
    // Now is before the game window, so it is stated rather than stretching the axis.
    expect(screen.getByText('Now 9:40 AM · first game at 12:35 PM')).toBeInTheDocument()
    const ticks = [...document.querySelectorAll('.hs-timeline__tick')].map((t) => t.textContent)
    expect(ticks.length).toBeLessThanOrEqual(6)
  })

  it('a single game marker carries matchup, time and position status', () => {
    render(<MemoryRouter><GameScheduleTimeline games={[game('a', '19:10', 'BAL @ NYM', 2.49), game('b', '21:40', 'MIA @ AZ')]} now={at('20:00')} /></MemoryRouter>)
    expect(screen.getByRole('button', { name: 'BAL @ NYM, 7:10 PM, POSITION · $2.49' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'MIA @ AZ, 9:40 PM, No position' })).toBeInTheDocument()
    expect(document.querySelector('.cc-now')).not.toBeNull()
  })
})
