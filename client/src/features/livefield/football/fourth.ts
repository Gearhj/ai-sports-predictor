/**
 * The fourth-down advisor: go for it, punt or kick, in expected points.
 *
 * Every number is a league average from nflverse snaps 2015-2026 (conversion odds by distance, field-goal make
 * rate by kick distance, where punts leave the other team) run through the same expected-points model as the
 * momentum chart. It is a rule-of-thumb in expected POINTS, not win probability: late in a close game the right
 * call can differ, and the page says so. It never recommends a wager.
 */
import { useEffect, useState } from 'react'
import { epAt, type EpGrid } from './ep'

export interface FourthTables { conv: number[][]; fg: Record<string, number>; puntOpp: number[]; kickoffTouchbackOwn: number; trainedOn: string }

let cached: Promise<FourthTables> | null = null
export function loadFourth(): Promise<FourthTables> {
  if (!cached) {
    cached = fetch('/playcall/fourth_tables.json').then((r) => { if (!r.ok) throw new Error('fourth'); return r.json() as Promise<FourthTables> })
    cached.catch(() => { cached = null })
  }
  return cached
}

export function useFourth(): FourthTables | null {
  const [t, setT] = useState<FourthTables | null>(null)
  useEffect(() => {
    let live = true
    loadFourth().then((x) => { if (live) setT(x) }).catch(() => undefined)
    return () => { live = false }
  }, [])
  return t
}

const ydBucket = (yd: number) => (yd <= 1 ? 0 : yd <= 2 ? 1 : yd <= 3 ? 2 : yd <= 5 ? 3 : yd <= 7 ? 4 : yd <= 10 ? 5 : yd <= 14 ? 6 : 7)

export interface Option { key: 'go' | 'punt' | 'fg'; label: string; ep: number; detail: string }
export interface Advice { options: Option[]; best: Option; yardsToGoal: number; distance: number }

/** `own` = yards from the offense's own goal line (0-100); `distance` = yards to go. */
export function advise(ep: EpGrid, t: FourthTables, own: number, distance: number): Advice {
  const toGoal = Math.max(1, 100 - own)
  const zone = toGoal <= 20 ? 1 : 0
  const pConv = t.conv[ydBucket(distance)]?.[zone] ?? 0.4
  const kickoffOpp = epAt(ep, 1, 10, t.kickoffTouchbackOwn)
  const options: Option[] = []

  // Go for it: convert and keep the ball at the new spot, or fail and hand over the ball where it stands.
  const converted = own + distance >= 100 ? 7 - kickoffOpp : epAt(ep, 1, 10, own + distance + 3)
  const failed = -epAt(ep, 1, 10, 100 - own)
  options.push({ key: 'go', label: 'Go for it', ep: pConv * converted + (1 - pConv) * failed, detail: `${Math.round(pConv * 100)}% to get ${distance}` })

  // Punt: the other team starts, on average, where the league's punts from this spot leave it.
  if (toGoal > 35) {
    const bucket = Math.max(0, Math.min(9, Math.floor(toGoal / 10)))
    const oppOwn = t.puntOpp[bucket] ?? 20
    options.push({ key: 'punt', label: 'Punt', ep: -epAt(ep, 1, 10, oppOwn), detail: `they start about their own ${Math.round(oppOwn)}` })
  }

  // Field goal: kick distance is the line of scrimmage plus 17 yards.
  const kick = toGoal + 17
  if (kick <= 63) {
    const key = String(18 + 3 * Math.max(0, Math.min(15, Math.floor((kick - 18) / 3))))
    const pMake = t.fg[key] ?? 0.6
    // a miss gives the ball to the other team at the spot of the kick (7 yards back), never worse than its own 20
    const missOpp = epAt(ep, 1, 10, Math.min(80, Math.max(20, toGoal + 7)))
    options.push({ key: 'fg', label: `${kick}-yard field goal`, ep: pMake * (3 - kickoffOpp) + (1 - pMake) * -missOpp, detail: `${Math.round(pMake * 100)}% to make it` })
  }
  const best = [...options].sort((a, b) => b.ep - a.ep)[0]
  return { options, best, yardsToGoal: toGoal, distance }
}
