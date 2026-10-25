/**
 * Expected points (EP) and expected points added (EPA), the analytics behind the momentum chart.
 *
 * EP = the net points the team with the ball is expected to score next from this down, distance and spot,
 * from a model fit to 461,646 NFL snaps (it matches nflfastR's EP with a 0.975 correlation on 2025-26 plays
 * it never saw). EPA of a play = the points scored on it, plus the new situation's EP, minus the old one.
 * A 7-yard gain on 3rd and 5 is worth far more than a 7-yard gain on 3rd and 15; EPA says so.
 */
import { useEffect, useState } from 'react'
import { parseFbPlay, type FbPlay } from './footballData'

export interface EpGrid { data: Int16Array; trainedOn: string; corr: number }

let cached: Promise<EpGrid> | null = null
export function loadEp(): Promise<EpGrid> {
  if (!cached) {
    cached = Promise.all([fetch('/playcall/ep_grid.bin').then((r) => { if (!r.ok) throw new Error('ep'); return r.arrayBuffer() }), fetch('/playcall/ep_meta.json').then((r) => r.json() as Promise<{ trainedOn: string; validation: { corr_with_nflfastR_ep: number } }>)])
      .then(([buf, meta]) => ({ data: new Int16Array(buf), trainedOn: meta.trainedOn, corr: meta.validation.corr_with_nflfastR_ep }))
    cached.catch(() => { cached = null })
  }
  return cached
}

export function useEp(): EpGrid | null {
  const [grid, setGrid] = useState<EpGrid | null>(null)
  useEffect(() => {
    let live = true
    loadEp().then((g) => { if (live) setGrid(g) }).catch(() => undefined)
    return () => { live = false }
  }, [])
  return grid
}

const ydBucket = (yd: number) => (yd <= 1 ? 0 : yd <= 2 ? 1 : yd <= 3 ? 2 : yd <= 5 ? 3 : yd <= 7 ? 4 : yd <= 10 ? 5 : yd <= 14 ? 6 : 7)

/** Expected points for the offense at this snap. yardLine = yards from the offense's own goal line (0-100). */
export function epAt(grid: EpGrid, down: number, distance: number, yardLine: number): number {
  const toGo = Math.max(1, Math.min(99, Math.round(100 - yardLine)))
  const d = Math.max(1, Math.min(4, down))
  return grid.data[((d - 1) * 8 + ydBucket(distance)) * 99 + (toGo - 1)] / 1000
}

export interface EpaPlay {
  idx: number
  id: string
  team: string
  epa: number
  kind: string
  period: number | null
  clock: string | null
  yards: number
  text: string
  scoring: boolean
  turnover: boolean
}

// College feeds give kickoffs a first-down start; a kickoff is not a scrimmage snap.
const valid = (p: FbPlay) => p.start.down != null && p.start.down >= 1 && p.start.down <= 4 && p.start.distance != null && p.start.yardLine != null && !/kickoff|end (of )?(period|quarter|half|game)/i.test(p.type ?? '')

export function computeEpa(grid: EpGrid, plays: readonly FbPlay[], homeId: string): EpaPlay[] {
  const out: EpaPlay[] = []
  // ESPN's yardLine counts from the HOME team's goal line; EP needs yards from the offense's own goal line.
  const own = (yl: number, offenseId: string | null) => (offenseId === homeId ? yl : 100 - yl)
  for (let i = 0; i < plays.length; i += 1) {
    const p = plays[i]
    if (!valid(p)) continue
    const kind = parseFbPlay(p).kind
    if (kind === 'nonplay') continue
    const offense = p.offense ?? p.start.team
    if (!offense) continue
    const before = epAt(grid, p.start.down!, p.start.distance!, own(p.start.yardLine!, offense))
    let j = i + 1
    while (j < plays.length && !valid(plays[j])) j += 1
    let after: number | null = null
    let scoreEndIdx = i
    if (j < plays.length) {
      const nxt = plays[j]
      const nOff = nxt.offense ?? nxt.start.team
      const e = epAt(grid, nxt.start.down!, nxt.start.distance!, own(nxt.start.yardLine!, nOff))
      after = nOff === offense ? e : -e
      scoreEndIdx = j - 1
    } else if (p.end.down != null && p.end.down >= 1 && p.end.down <= 4 && p.end.team === offense && p.end.yardLine != null && p.end.distance != null) {
      after = epAt(grid, p.end.down, p.end.distance, own(p.end.yardLine, offense))
    }
    if (after == null) continue
    const prev = i > 0 ? plays[i - 1] : null
    const sa = plays[scoreEndIdx]
    const dAway = (sa.awayScore ?? 0) - (prev?.awayScore ?? 0)
    const dHome = (sa.homeScore ?? 0) - (prev?.homeScore ?? 0)
    const points = offense === homeId ? dHome - dAway : dAway - dHome
    const epa = Math.max(-9, Math.min(9, points + after - before))
    out.push({ idx: i, id: p.id, team: offense, epa, kind, period: p.period, clock: p.clock, yards: p.yards ?? 0, text: p.text ?? '', scoring: p.scoring, turnover: p.turnover })
  }
  return out
}
