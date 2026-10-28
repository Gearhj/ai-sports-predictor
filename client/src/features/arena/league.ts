/**
 * BOSS FIGHT and RIVAL MODE scoring (plan #5). Pure functions over Arena calls.
 *
 * THE HOUSE (weekly boss, Monday to Sunday ET): the market's favourite at the moment you locked. Only games
 * where you went against The House count; each one you win knocks 20 HP off the boss, each one The House wins
 * knocks 20 off you. Agreeing with the market is safe and does nothing.
 *
 * RIVAL: games you and your rival both called (LIVE), once settled. The week's belt goes to whoever was right
 * more often on shared games this week; ties keep it unclaimed.
 */
import type { ArenaCall, LeagueCall, Side } from './arenaStore'

export const BOSS_HIT = 20

/** Monday 00:00 America/New_York of the week containing `now`, as epoch ms. */
export function weekStart(now = Date.now()): number {
  const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(now))
  const dow = new Date(`${day}T12:00:00Z`).getUTCDay() // 0 Sun .. 6 Sat
  const back = (dow + 6) % 7
  const monday = new Date(Date.parse(`${day}T12:00:00Z`) - back * 86_400_000).toISOString().slice(0, 10)
  const etHour = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', hourCycle: 'h23' })
  for (const h of [4, 5]) { // midnight ET is 04:00Z in daylight time, 05:00Z in standard time
    const t = Date.parse(`${monday}T0${h}:00:00Z`)
    if (etHour.format(new Date(t)) === '00') return t
  }
  return Date.parse(`${monday}T04:00:00Z`)
}

const settled = (c: Partial<ArenaCall>) => c.result && c.result.winner !== 'TIE'

export interface Boss { fights: number; wins: number; losses: number; bossHp: number; youHp: number; broken: boolean; pending: number }
export function bossOf(calls: readonly ArenaCall[], from: number, to = from + 7 * 86_400_000): Boss {
  const week = calls.filter((c) => c.mode === 'LIVE' && c.house && c.house !== c.side && Date.parse(c.starts_at) >= from && Date.parse(c.starts_at) < to)
  const done = week.filter(settled)
  const wins = done.filter((c) => c.result!.winner === c.side).length
  const losses = done.length - wins
  const bossHp = Math.max(0, 100 - wins * BOSS_HIT)
  return { fights: done.length, wins, losses, bossHp, youHp: Math.max(0, 100 - losses * BOSS_HIT), broken: bossHp === 0, pending: week.length - done.length }
}

export interface SharedGame { game_id: string; label: string; starts_at: string; mine: Side; theirs: Side; winner: Side | 'TIE' | null }
export interface Rivalry { shared: SharedGame[]; me: number; rival: number; settled: number; weekMe: number; weekRival: number; belt: 'ME' | 'RIVAL' | null; clashes: SharedGame[] }
export function rivalryOf(mine: readonly ArenaCall[], theirs: readonly LeagueCall[], from: number): Rivalry {
  const byId = new Map(theirs.filter((c) => !c.hidden && c.side).map((c) => [c.game_id, c]))
  const shared: SharedGame[] = []
  for (const c of mine) {
    if (c.mode !== 'LIVE') continue
    const t = byId.get(c.game_id)
    if (!t?.side) continue
    const winner = c.result?.winner ?? t.result?.winner ?? null
    shared.push({ game_id: c.game_id, label: `${c.away} @ ${c.home}`, starts_at: c.starts_at, mine: c.side, theirs: t.side as Side, winner })
  }
  shared.sort((a, b) => Date.parse(b.starts_at) - Date.parse(a.starts_at))
  const done = shared.filter((s) => s.winner && s.winner !== 'TIE')
  const week = done.filter((s) => Date.parse(s.starts_at) >= from)
  const weekMe = week.filter((s) => s.mine === s.winner).length
  const weekRival = week.filter((s) => s.theirs === s.winner).length
  return {
    shared, settled: done.length,
    me: done.filter((s) => s.mine === s.winner).length, rival: done.filter((s) => s.theirs === s.winner).length,
    weekMe, weekRival, belt: weekMe > weekRival ? 'ME' : weekRival > weekMe ? 'RIVAL' : null,
    clashes: shared.filter((s) => s.mine !== s.theirs),
  }
}
