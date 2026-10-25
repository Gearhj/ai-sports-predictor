/**
 * The Monday Morning QB view: labelled, keyboard-accessible Season and Week controls,
 * same-origin report URLs that carry the page theme, and game links left to the report.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_BASE_URL } from '../../services/api'
import { MmqbWeekPicker, mmqbHref, mmqbReportUrl, type MmqbWeek } from './MmqbReport'

const weeks: MmqbWeek[] = [
  { season: 2025, week: 17, label: 'NFL 2025 Week 17', positions: 4, first_kickoff_utc: null },
  { season: 2026, week: 1, label: 'NFL 2026 Week 1', positions: 1, first_kickoff_utc: null },
  { season: 2026, week: 2, label: 'NFL 2026 Week 2', positions: 6, first_kickoff_utc: null },
  { season: 2026, week: 3, label: 'NFL 2026 Week 3', positions: 14, first_kickoff_utc: null },
]

afterEach(() => cleanup())

describe('Monday Morning QB controls', () => {
  it('labels a Season and a Week select and lists only that season’s weeks', () => {
    render(<MmqbWeekPicker weeks={weeks} season={2026} week={3} onChange={() => {}} />)
    const season = screen.getByLabelText('Season') as HTMLSelectElement
    const week = screen.getByLabelText('Week') as HTMLSelectElement
    expect(season.tagName).toBe('SELECT')
    expect(season.value).toBe('2026')
    expect([...season.options].map((o) => o.value)).toEqual(['2026', '2025'])
    expect(week.value).toBe('3')
    expect([...week.options].map((o) => o.textContent)).toEqual(
      ['Week 1 · 1 position', 'Week 2 · 6 positions', 'Week 3 · 14 positions'])
  })

  it('changing the week or the season reports the chosen week (the latest week of a new season)', () => {
    const onChange = vi.fn()
    render(<MmqbWeekPicker weeks={weeks} season={2026} week={3} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('Week'), { target: { value: '2' } })
    expect(onChange).toHaveBeenLastCalledWith(2026, 2)
    fireEvent.change(screen.getByLabelText('Season'), { target: { value: '2025' } })
    expect(onChange).toHaveBeenLastCalledWith(2025, 17)
  })

  it('builds same-origin URLs: the page link and the themed report', () => {
    expect(mmqbHref({ season: 2026, week: 3 })).toBe('/performance?panel=mmqb&league=NFL&season=2026&week=3')
    const url = mmqbReportUrl({ league: 'NFL', season: 2026, week: 3, theme: 'dark' })
    expect(url.endsWith('/research/mmqb?league=NFL&format=html&season=2026&week=3&theme=dark')).toBe(true)
    // The only origin is the build's API base ("/api/v1" in the production build): no preview port is hard-coded.
    expect(url.startsWith(API_BASE_URL)).toBe(true)
  })
})
