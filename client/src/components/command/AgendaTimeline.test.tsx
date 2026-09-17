import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import type { AgendaEvent } from '../../services/agenda'
import { AgendaTimeline, PX_PER_HOUR, clampEvents, timelineDomain } from './AgendaTimeline'

afterEach(cleanup)

const at = (iso: string) => Date.parse(iso)
const HOUR = 3_600_000
const ev = (key: string, kind: AgendaEvent['kind'], when: string, over: Partial<AgendaEvent> = {}): AgendaEvent => ({
  key, kind, sport: 'MLB', gameId: key, matchup: `${key.toUpperCase()} @ HOME`, at: at(when), status: 'scheduled', label: kind, detail: '', ...over,
})

const NOW = at('2026-09-17T15:10:00Z') // 11:10 AM ET
const DAY: AgendaEvent[] = [
  ev('mil', 'game', '2026-09-17T16:35:00Z'),
  ev('lad', 'game', '2026-09-17T16:40:00Z'),
  ev('min', 'game', '2026-09-18T01:38:00Z'),
  ev('nfl', 'game', '2026-09-18T00:15:00Z', { sport: 'NFL' }),
  // A forecast published a week earlier: the reason the old axis spanned days.
  ev('nfl-f', 'forecast', '2026-09-10T14:00:00Z', { sport: 'NFL', status: 'done', gameId: 'nfl' }),
  ev('mil-c', 'collection', '2026-09-17T14:55:00Z', { until: at('2026-09-17T16:05:00Z'), status: 'open', gameId: 'mil' }),
]

describe('timeline window', () => {
  it("covers today's slate and now, not an earlier day's forecast", () => {
    const [lo, hi] = timelineDomain(DAY, NOW)
    expect(lo).toBeGreaterThanOrEqual(at('2026-09-17T14:00:00Z') - HOUR)
    expect(lo).toBeLessThanOrEqual(at('2026-09-17T14:55:00Z'))
    expect(hi).toBeGreaterThanOrEqual(at('2026-09-18T01:38:00Z'))
    expect((hi - lo) / HOUR).toBeLessThan(16)
    expect(lo % HOUR).toBe(0)
  })

  it('holds out-of-window events at the edge and keeps their real time', () => {
    const domain = timelineDomain(DAY, NOW)
    const shown = clampEvents(DAY, domain)
    const old = shown.find((e) => e.key === 'nfl-f')!
    expect(old.at).toBe(domain[0])
    expect(old.actualAt).toBe(at('2026-09-10T14:00:00Z'))
    expect(shown.find((e) => e.key === 'mil')!.at).toBe(at('2026-09-17T16:35:00Z'))
  })

  it('without games, centres on now', () => {
    const [lo, hi] = timelineDomain([], NOW)
    expect(lo).toBeLessThanOrEqual(NOW)
    expect(hi).toBeGreaterThan(NOW)
  })
})

describe('AgendaTimeline', () => {
  it('draws one hourly tick per hour on a fixed, scrollable scale with pinned labels', () => {
    render(<MemoryRouter><AgendaTimeline events={DAY} now={NOW} /></MemoryRouter>)
    const [lo, hi] = timelineDomain(DAY, NOW)
    const svg = screen.getByRole('img', { name: /Today's agenda/ })
    expect(Number(svg.getAttribute('width'))).toBeGreaterThanOrEqual(((hi - lo) / HOUR) * PX_PER_HOUR)
    expect(document.querySelectorAll('.cc-agenda__hour')).toHaveLength((hi - lo) / HOUR + 1)
    expect(screen.getByRole('region', { name: 'Timeline, scrolls horizontally' })).toContainElement(svg)
    expect(screen.getByRole('button', { name: 'Now' })).toBeInTheDocument()
    expect(screen.getByText('← Earlier')).toBeInTheDocument()
    const labels = [...document.querySelectorAll('.cc-agenda__label b')].map((b) => b.textContent)
    expect(labels).toEqual(['MLB', 'NFL'])
    expect(screen.getByText(/1 earlier milestone shown at the left edge/)).toBeInTheDocument()
    expect(document.querySelector('.cc-now')).not.toBeNull()
  })

  it('names the real date of an earlier milestone in the table', () => {
    render(<MemoryRouter><AgendaTimeline events={DAY} now={NOW} /></MemoryRouter>)
    expect(document.body.textContent).toMatch(/Thu, Sep 10, 10:00 AM/)
  })

  it('keeps a sport with no game today as a labelled row with its next game', () => {
    render(<MemoryRouter><AgendaTimeline events={DAY} now={NOW} idle={{ NBA: 'Next: BOS @ DET · Tue Oct 20' }} /></MemoryRouter>)
    const idle = document.querySelector('.cc-agenda__label.is-idle')!
    expect(idle).toHaveTextContent('NBA')
    expect(idle).toHaveTextContent('Next: BOS @ DET · Tue Oct 20')
  })
})
