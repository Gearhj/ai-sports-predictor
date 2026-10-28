/**
 * THE UPSET ATLAS's maths (History, plan page list). An upset = AISP's model made a side a favourite at `min` or
 * more and that side lost (two-way, ties left out). Each upset records whether the market had the same favourite
 * (EVERYONE missed it) or not (AISP alone was fooled). Teams are tallied two ways: giant killers (won as AISP's
 * underdog) and kryptonite (lost as AISP's favourite).
 */
import type { GalaxyGame } from './galaxy'

export interface Upset { sport: string; gameId: string | null; at: string | null; winner: string; loser: string; said: number; marketSaid: number | null; alone: boolean; label: string }
export interface TeamTally { team: string; sport: string; n: number }

export function upsetsOf(games: GalaxyGame[], min = 0.6, sport = 'ALL'): { upsets: Upset[]; eligible: number } {
  const upsets: Upset[] = []
  let eligible = 0
  for (const g of games) {
    if (sport !== 'ALL' && g.sport !== sport) continue
    const p = g.p_home_two_way
    if (p == null || (g.actual_outcome !== 'HOME' && g.actual_outcome !== 'AWAY')) continue
    const favHome = p >= 0.5, said = favHome ? p : 1 - p
    if (said < min) continue
    eligible++
    if ((g.actual_outcome === 'HOME') === favHome) continue
    const m = g.decision?.market_p_home ?? null
    const marketSaid = m == null ? null : favHome ? m : 1 - m
    const home = g.home ?? '?', away = g.away ?? '?'
    upsets.push({ sport: g.sport, gameId: g.game_id ?? null, at: g.kickoff_utc ?? g.slate_date ?? null, winner: favHome ? away : home, loser: favHome ? home : away,
      said, marketSaid, alone: marketSaid != null && marketSaid < 0.5, label: `${away} @ ${home}` })
  }
  return { upsets, eligible }
}

export function tally(upsets: Upset[], who: 'winner' | 'loser', top = 6): TeamTally[] {
  const m = new Map<string, TeamTally>()
  for (const u of upsets) {
    const key = `${u.sport}|${u[who]}`
    const t = m.get(key) ?? { team: u[who], sport: u.sport, n: 0 }
    t.n++; m.set(key, t)
  }
  return [...m.values()].sort((a, b) => b.n - a.n || a.team.localeCompare(b.team)).slice(0, top)
}

/** Upset rate inside confidence bands (60–70, 70–80, 80+). */
export function upsetRates(games: GalaxyGame[], sport = 'ALL'): { lo: number; hi: number; n: number; upsets: number }[] {
  return [[0.6, 0.7], [0.7, 0.8], [0.8, 1.0001]].map(([lo, hi]) => {
    const all = upsetsOf(games, lo, sport), above = upsetsOf(games, hi, sport)
    return { lo, hi, n: all.eligible - above.eligible, upsets: all.upsets.length - above.upsets.length }
  })
}
