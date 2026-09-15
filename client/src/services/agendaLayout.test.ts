import { describe, expect, it } from 'vitest'
import type { AgendaEvent } from './agenda'
import { clusterEvents, clusterLabel, layoutLane, MAX_ROWS } from './agendaLayout'

const T0 = Date.parse('2026-09-15T22:40:00Z') // 6:40 PM ET
const MIN = 60_000
const ev = (id: string, kind: AgendaEvent['kind'], at: number, opts: Partial<AgendaEvent> = {}): AgendaEvent => ({
  key: `${id}:${kind}`, kind, sport: 'MLB', gameId: id, matchup: `A${id} @ H${id}`, at, status: 'scheduled', label: kind, detail: '', ...opts,
})
// 1 px per minute
const x = (ms: number) => 100 + (ms - T0) / MIN

describe('timeline clustering', () => {
  it('groups games starting together and keeps every exact time', () => {
    const events = [ev('1', 'game', T0), ev('2', 'game', T0), ev('3', 'game', T0 + 5 * MIN), ev('4', 'game', T0), ev('5', 'game', T0 + 25 * MIN)]
    const clusters = clusterEvents(events)
    expect(clusters).toHaveLength(2)
    expect(clusters[0].events.map((e) => e.at)).toEqual([T0, T0, T0, T0 + 5 * MIN])
    expect(clusterLabel(clusters[0])).toBe('4 games · 6:40 PM')
    expect(clusterLabel(clusters[0], true)).toBe('4 · 6:40 PM')
    expect(clusterLabel(clusters[1])).toBe('A5 @ H5')
  })

  it('never merges different kinds, statuses or sports', () => {
    const clusters = clusterEvents([
      ev('1', 'game', T0), ev('2', 'forecast', T0), ev('3', 'game', T0, { status: 'done' }), ev('4', 'game', T0, { sport: 'NFL' }),
    ])
    expect(clusters).toHaveLength(4)
  })

  it('merges decision windows only when both edges coincide', () => {
    const clusters = clusterEvents([
      ev('1', 'decision', T0, { until: T0 + 15 * MIN }),
      ev('2', 'decision', T0 + 2 * MIN, { until: T0 + 17 * MIN }),
      ev('3', 'decision', T0 + 3 * MIN, { until: T0 + 40 * MIN }),
    ])
    expect(clusters.map((c) => c.events.length)).toEqual([2, 1])
  })
})

describe('lane packing', () => {
  it('stacks overlapping marks into sub-rows up to the band limit, and a quiet lane stays one row', () => {
    const windows = [0, 8, 16, 24, 32].map((m, i) => ev(String(i), 'decision', T0 + m * MIN, { until: T0 + (m + 15) * MIN }))
    const busy = layoutLane('MLB', clusterEvents(windows), x)
    expect(busy.rows.win).toBe(MAX_ROWS.win)
    // No two marks in the same row overlap until the band is full.
    const rows = busy.clusters.slice(0, 3).map((c) => c.row)
    expect(new Set(rows).size).toBe(3)
    const quiet = layoutLane('MLB', clusterEvents([ev('1', 'game', T0), ev('2', 'game', T0 + 120 * MIN)]), x)
    expect(quiet.rows.game).toBe(1)
    expect(busy.height).toBeGreaterThan(quiet.height)
  })

  it('keeps a labelled pill inside the chart by shifting it left, not by moving its time', () => {
    const lane = layoutLane('MLB', clusterEvents([ev('1', 'game', T0), ev('2', 'game', T0)]), x, { right: 120 })
    const pill = lane.clusters[0]
    expect(pill.x1).toBe(120)
    expect(pill.at).toBe(T0)
  })

  it('gives an empty lane the compact idle height', () => {
    expect(layoutLane('NBA', [], x).height).toBe(26)
  })
})
