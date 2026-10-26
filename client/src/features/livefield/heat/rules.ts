/** Who is hot, per sport: plain thresholds on tonight's line, so a fan can see why a card lit up. */
import type { ArenaFeed } from '../arena/arenaData'
import type { RinkEvent } from '../arena/nhlModel'
import type { FbLive } from '../football/footballData'
import { findPlayer } from '../football/playParser'
import type { MlbLive } from '../liveFieldData'
import type { HeatItem } from './Heat'

const n = (v: string | number | null | undefined) => { const x = Number(v); return Number.isFinite(x) ? x : 0 }

// ------------------------------------------------------------------ NHL --
export function nhlHeat(feed: ArenaFeed, events: RinkEvent[], col: Record<string, string>): HeatItem[] {
  const out: HeatItem[] = []
  const team = (id: string | null) => (id === feed.teams.home.id ? feed.teams.home : feed.teams.away)
  for (const a of feed.athletes) {
    const s = a.stats; const c = col[a.team ?? ''] ?? '#94a3b8'; const logo = team(a.team).logo
    if (a.group === 'goalies') continue
    const g = n(s.goals); const as = n(s.assists); const pts = g + as; const sh = n(s.shotsTotal)
    const line = `${g} G · ${as} A · ${sh} shots`
    if (g >= 3) out.push({ id: a.id, name: a.name ?? '', face: a.headshot, color: c, level: 'fire', title: 'HAT TRICK!', sub: line, logo })
    else if (g === 2) out.push({ id: a.id, name: a.name ?? '', face: a.headshot, color: c, level: 'fire', title: 'HAT TRICK WATCH', sub: line, logo })
    else if (pts >= 3) out.push({ id: a.id, name: a.name ?? '', face: a.headshot, color: c, level: 'fire', title: "HE'S ON FIRE!", sub: line, logo })
    else if (pts === 2) out.push({ id: a.id, name: a.name ?? '', face: a.headshot, color: c, level: 'warm', title: 'HEATING UP', sub: line, logo })
    else if (sh >= 6) out.push({ id: a.id, name: a.name ?? '', face: a.headshot, color: c, level: 'warm', title: 'FIRING AWAY', sub: line, logo })
  }
  // goalies: saves in a row since the last goal against
  for (const t of [feed.teams.home, feed.teams.away]) {
    let run = 0
    for (const e of events) { if (e.shooter === t.id || !e.shooter) continue; if (e.kind === 'goal') run = 0; else if (e.kind === 'shot') run++ }
    const gk = feed.athletes.filter((a) => a.team === t.id && a.group === 'goalies' && a.stats.saves != null).sort((a, b) => n(b.stats.shotsAgainst) - n(a.stats.shotsAgainst))[0]
    if (!gk) continue
    const sv = n(gk.stats.savePct); const sa = n(gk.stats.shotsAgainst)
    const line = `${gk.stats.saves} saves on ${gk.stats.shotsAgainst} · ${run} straight`
    if (run >= 12) out.push({ id: gk.id, name: gk.name ?? '', face: gk.headshot, color: col[t.id] ?? '#94a3b8', level: 'ice', title: 'THE WALL', sub: line, logo: t.logo })
    else if (sa >= 20 && sv >= 0.95) out.push({ id: gk.id, name: gk.name ?? '', face: gk.headshot, color: col[t.id] ?? '#94a3b8', level: 'ice', title: 'STANDING ON HIS HEAD', sub: line, logo: t.logo })
  }
  return out
}
/** Skater ids on fire (for the flame ring on the rink). */
export const hotIds = (items: HeatItem[]) => new Set(items.filter((i) => i.level === 'fire').map((i) => i.id))

// ------------------------------------------------------------ football --
/** Completions in a row per passer (short name as the play text writes it, e.g. "S.Darnold"). */
export function completionStreaks(plays: FbLive['plays']): Map<string, number> {
  const m = new Map<string, number>()
  for (const p of plays ?? []) {
    const hit = /([A-Z][a-zA-Z'-]*\.\s?[A-Z][\w'.-]+) pass/.exec(p.text ?? '')
    if (!hit) continue
    const done = !/incomplete|INTERCEPTED/i.test(p.text ?? '') && / to /.test(p.text ?? '')
    m.set(hit[1], done ? (m.get(hit[1]) ?? 0) + 1 : 0)
  }
  return m
}

export function footballHeat(fb: FbLive, color: (teamId: string | null) => string): HeatItem[] {
  const out: HeatItem[] = []
  const teams = fb.teams; if (!teams) return out
  const logo = (id: string | null) => (id === teams.home.id ? teams.home.logo : teams.away.logo)
  const streak = completionStreaks(fb.plays)
  const push = (p: NonNullable<FbLive['players']>[number], level: HeatItem['level'], title: string, sub: string) => out.push({ id: p.id, name: `${p.first} ${p.last}`, face: p.headshot, color: color(p.team), level, title, sub, logo: logo(p.team) })
  for (const p of fb.players ?? []) {
    const s = p.stats ?? {}
    const pa = s.passing; const ru = s.rushing; const re = s.receiving; const de = s.defensive; const it = s.interceptions
    if (pa) {
      const yds = n(pa.YDS); const td = n(pa.TD)
      const short = Array.from(streak.keys()).find((k) => findPlayer(k, [p]) === p)
      const run = short ? streak.get(short) ?? 0 : 0
      const line = `${pa['C/ATT'] ?? ''} · ${yds} YDS · ${td} TD${run >= 3 ? ` · ${run} straight` : ''}`
      if (yds >= 300 || td >= 3) push(p, 'fire', "HE'S ON FIRE!", line)
      else if (run >= 6) push(p, 'fire', 'LOCKED IN', line)
      else if (yds >= 200 && td >= 1) push(p, 'warm', 'HEATING UP', line)
    }
    if (ru) { const yds = n(ru.YDS); const td = n(ru.TD); const line = `${ru.CAR} carries · ${yds} YDS · ${td} TD`; if (yds >= 100 || td >= 2) push(p, 'fire', 'BEAST MODE', line); else if (yds >= 70) push(p, 'warm', 'HEATING UP', line) }
    if (re) { const yds = n(re.YDS); const td = n(re.TD); const line = `${re.REC} catches · ${yds} YDS · ${td} TD`; if (yds >= 100 || td >= 2) push(p, 'fire', "HE'S ON FIRE!", line); else if (yds >= 70) push(p, 'warm', 'HEATING UP', line) }
    if (de || it) { const sacks = n(de?.SACKS); const ints = n(it?.INT); if (sacks >= 2 || ints >= 1 || (sacks >= 1 && n(de?.TFL) >= 2)) push(p, 'ice', 'LOCKDOWN', `${de?.TOT ?? 0} tackles · ${sacks} sacks · ${ints} INT`) }
  }
  return out
}

// ----------------------------------------------------------------- MLB --
export function mlbHeat(live: MlbLive, color: (side: 'away' | 'home') => string): HeatItem[] {
  const out: HeatItem[] = []
  const offSide: 'away' | 'home' = live.status?.is_top ? 'away' : 'home'
  const defSide: 'away' | 'home' = offSide === 'away' ? 'home' : 'away'
  const b = live.offense?.batter; const g = live.batter_stats?.game
  if (b && g) {
    const h = n(g.hits); const hr = n(g.homeRuns); const line = `${g.summary ?? `${h}-for-${n(g.atBats)}`} tonight`
    if (hr >= 1 || h >= 3) out.push({ id: `b${b.id}`, name: b.name ?? '', face: b.headshot, color: color(offSide), level: 'fire', title: hr >= 2 ? 'GOING DEEP AGAIN' : hr ? 'GOING DEEP' : "HE'S ON FIRE!", sub: line })
    else if (h === 2) out.push({ id: `b${b.id}`, name: b.name ?? '', face: b.headshot, color: color(offSide), level: 'warm', title: 'HEATING UP', sub: line })
  }
  const pi = live.defense?.pitcher; const pg = live.pitcher_stats?.game
  if (pi && pg) {
    const k = n(pg.strikeOuts); const hits = n(pg.hits); const ip = n(pg.inningsPitched)
    const line = `${pg.inningsPitched ?? '0'} IP · ${k} K · ${hits} H · ${n(pg.earnedRuns)} ER`
    if (hits === 0 && ip >= 4) out.push({ id: `p${pi.id}`, name: pi.name ?? '', face: pi.headshot, color: color(defSide), level: 'ice', title: 'NO-HIT WATCH', sub: line })
    else if (k >= 8) out.push({ id: `p${pi.id}`, name: pi.name ?? '', face: pi.headshot, color: color(defSide), level: 'ice', title: 'DEALING', sub: line })
    else if (k >= 5) out.push({ id: `p${pi.id}`, name: pi.name ?? '', face: pi.headshot, color: color(defSide), level: 'warm', title: 'CARVING', sub: line })
  }
  // tonight's home runs from the play log (names only)
  const hrs = new Map<string, number>()
  for (const p of live.recent_plays ?? []) { if (!/home run/i.test(p.event ?? '')) continue; const who = /^(.+?) homers/i.exec(p.description)?.[1]; if (who) hrs.set(who, (hrs.get(who) ?? 0) + 1) }
  for (const [who, c] of hrs) if (!out.some((o) => o.name === who)) out.push({ id: `hr-${who}`, name: who, face: null, color: '#f97316', level: 'fire', title: c >= 2 ? 'MULTI-HOMER NIGHT' : 'WENT DEEP', sub: `${c} home run${c > 1 ? 's' : ''} tonight` })
  return out
}
