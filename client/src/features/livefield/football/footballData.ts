/**
 * Football live data: ESPN's public play-by-play, trimmed and served by the
 * test-mode preview server (/preview-api/football/live). Free, never stored,
 * never used for settlement. NFL and college football share one shape.
 */
import { useCallback } from 'react'
import { useApiResource } from '../../../hooks/useApiResource'
import { cacheKey } from '../../../services/queryCache'
import { PREVIEW_ONLY } from '../liveFieldData'
import { parsePlay, type ParsedPlay, type RosterPlayer } from './playParser'

export interface FbTeam { id: string; abbr: string; name: string; short: string; color: string | null; alt: string | null; logo: string | null; score: number | null; record: string | null }
export interface FbPoint { down: number | null; distance: number | null; yardLine: number | null; yardsToEndzone: number | null; team: string | null; text: string | null; possessionText: string | null }
export interface FbPlay {
  id: string; seq: string; type: string | null; text: string | null; period: number | null; clock: string | null
  start: FbPoint; end: FbPoint; yards: number | null; yac: number | null
  scoring: boolean; turnover: boolean; penalty: boolean; awayScore: number | null; homeScore: number | null; wallclock: string | null; offense: string | null
}
export interface FbLeader { team: string; cat: string; label: string; name: string; id: string | null; line: string; pos: string | null; jersey: string | null }
export interface FbSituation { down: number | null; distance: number | null; yardLine: number | null; possession: string | null; isRedZone: boolean | null; text: string | null; possessionText: string | null; homeTimeouts: number | null; awayTimeouts: number | null; lastPlay: string | null }
export interface FbLive {
  source: 'ESPN'; found: boolean; reason?: string; league?: string; event_id?: string; retrieved_at?: string
  status?: { state: 'pre' | 'in' | 'post' | null; detail: string | null; short: string | null; period: number | null; clock: string | null }
  venue?: string | null
  date?: string | null
  teams?: { home: FbTeam; away: FbTeam }
  situation?: FbSituation | null
  plays?: FbPlay[]
  win_probability?: { playId: string; home: number }[]
  stats?: Record<string, Record<string, string>>
  leaders?: FbLeader[]
  players?: RosterPlayer[]
  depth?: Record<string, { off: Record<string, { id: string | null; name: string | null; jersey: string | null }>; def: Record<string, { id: string | null; name: string | null; jersey: string | null }>; offFormation: string | null; defFormation: string | null }>
  week?: number | null
  season?: number | null
  broadcast?: string | null
  venue_city?: string | null
  weather?: { text: string | null; temp: number | null } | null
  attendance?: number | null
  drives?: Array<{ id: string; team: string | null; team_id: string | null; desc: string | null; start: string | null; start_yl: number | null; end_yl: number | null; period: number | null; clock: string | null; yards: number | null; plays: number | null; result: string | null; score: boolean; elapsed: string | null; play_ids: string[] }>
  scoring?: Array<{ id: string; team: string | null; team_id: string | null; type: string | null; text: string | null; period: number | null; clock: string | null; away: number | null; home: number | null }>
}

export function useFootballLive(args: { league: 'nfl' | 'college-football'; date: string; home: string; away: string; homeName: string; awayName: string; enabled: boolean; live: boolean }) {
  const { league, date, home, away, homeName, awayName, enabled, live } = args
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!enabled) return null
    const q = new URLSearchParams({ league, date, home, away, home_name: homeName, away_name: awayName })
    const res = await fetch(`/preview-api/football/live?${q.toString()}`, { signal, headers: { accept: 'application/json' } })
    const type = res.headers.get('content-type') ?? ''
    if (res.status === 404 || !type.includes('application/json')) throw new Error(PREVIEW_ONLY)
    const body = (await res.json()) as FbLive & { error?: string }
    if (!res.ok) throw new Error(body.reason ?? body.error ?? `HTTP ${res.status}`)
    return body
  }, [enabled, league, date, home, away, homeName, awayName])
  return useApiResource<FbLive | null>(loader, {
    key: cacheKey('lf-football', { league, date, home, away, homeName, awayName, enabled }),
    staleTimeMs: live ? 5_000 : 60_000,
    refreshIntervalMs: enabled ? (live ? 8_000 : 60_000) : undefined,
  })
}

export const ANIMATABLE = new Set<ParsedPlay['kind']>(['pass', 'incomplete', 'interception', 'sack', 'rush', 'punt', 'fieldgoal', 'kickoff', 'penalty'])

export function parseFbPlay(p: FbPlay): ParsedPlay { return parsePlay(p.type, p.text, p.yards) }

/** The most recent play that is a real snap or kick (not a timeout or period marker). */
export function latestAnimatable(plays: readonly FbPlay[] | undefined): FbPlay | null {
  if (!plays) return null
  for (let i = plays.length - 1; i >= 0; i -= 1) if (ANIMATABLE.has(parseFbPlay(plays[i]).kind)) return plays[i]
  return null
}

export const hex = (c: string | null | undefined, fallback: string) => (c && /^[0-9a-fA-F]{6}$/.test(c) ? `#${c}` : fallback)

export function ordinalDown(d: number | null | undefined): string {
  return d == null || d < 1 ? '' : ['', '1st', '2nd', '3rd', '4th'][d] ?? `${d}th`
}

/** Pass/run mix and yards per play for one team in this game, from the plays seen so far. */
export function tendencies(plays: readonly FbPlay[] | undefined, teamId: string): { pass: number; run: number; passPct: number | null; ypp: number | null; byDown: Record<number, { pass: number; run: number }> } {
  const out = { pass: 0, run: 0, passPct: null as number | null, ypp: null as number | null, byDown: { 1: { pass: 0, run: 0 }, 2: { pass: 0, run: 0 }, 3: { pass: 0, run: 0 }, 4: { pass: 0, run: 0 } } as Record<number, { pass: number; run: number }> }
  let yards = 0
  let n = 0
  for (const p of plays ?? []) {
    if ((p.offense ?? p.start.team) !== teamId) continue
    const k = parseFbPlay(p).kind
    const isPass = k === 'pass' || k === 'incomplete' || k === 'interception' || k === 'sack'
    if (!isPass && k !== 'rush') continue
    if (isPass) out.pass += 1; else out.run += 1
    const dn = p.start.down
    if (dn && out.byDown[dn]) { if (isPass) out.byDown[dn].pass += 1; else out.byDown[dn].run += 1 }
    yards += p.yards ?? 0
    n += 1
  }
  out.passPct = out.pass + out.run ? out.pass / (out.pass + out.run) : null
  out.ypp = n ? yards / n : null
  return out
}


// ----------------------------------------------------------- starters ----

export type Starters = Record<string, RosterPlayer | null>

const asPlayer = (fb: FbLive, id: string | null, name: string | null, teamId: string): RosterPlayer | null => {
  if (!id) return null
  const known = fb.players?.find((p) => p.id === id)
  if (known) return known
  const parts = (name ?? '').split(' ')
  const slug = fb.league === 'college-football' ? 'college-football' : 'nfl'
  return { id, team: teamId, first: parts[0] ?? '', last: parts.slice(1).join(' '), headshot: `https://a.espncdn.com/i/headshots/${slug}/players/full/${id}.png` }
}

/**
 * The likely starters for one team, keyed by the scene's roles (QB, RB, TE, WR1-3,
 * OL1-5, DL1-4, LB1-3, CB1-2, S1-2). NFL: ESPN's depth chart. College: the best
 * guess from the box score and roster (no depth chart exists), so the offensive line
 * stays without photos. Who is on the field for any one snap can differ.
 */
export function buildStarters(fb: FbLive, teamId: string): { starters: Starters; offFormation: string | null; defFormation: string | null; source: 'depth chart' | 'box score' } {
  const out: Starters = {}
  const d = fb.depth?.[teamId]
  if (d && Object.keys(d.off).length > 5) {
    const o = d.off
    const f = d.def
    const pick = (grp: typeof o, key: string) => asPlayer(fb, grp[key]?.id ?? null, grp[key]?.name ?? null, teamId)
    out.QB = pick(o, 'qb'); out.RB = pick(o, 'rb'); out.TE = pick(o, 'te')
    out.WR1 = pick(o, 'wr1'); out.WR2 = pick(o, 'wr2'); out.WR3 = pick(o, 'wr3')
    ;['lt', 'lg', 'c', 'rg', 'rt'].forEach((k, i) => { out[`OL${i + 1}`] = pick(o, k) })
    ;['lde', 'ldt', 'rdt', 'rde'].forEach((k, i) => { out[`DL${i + 1}`] = pick(f, k) })
    ;['wlb', 'mlb', 'slb'].forEach((k, i) => { out[`LB${i + 1}`] = pick(f, k) })
    out.CB1 = pick(f, 'lcb'); out.CB2 = pick(f, 'rcb'); out.S1 = pick(f, 'ss'); out.S2 = pick(f, 'fs')
    return { starters: out, offFormation: d.offFormation, defFormation: d.defFormation, source: 'depth chart' }
  }
  const mine = (fb.players ?? []).filter((p) => p.team === teamId)
  const by = (cat: string, groups: string[]) => mine.filter((p) => groups.includes(p.pos ?? '') && p.ranks && cat in p.ranks).sort((a, b) => (a.ranks![cat] ?? 99) - (b.ranks![cat] ?? 99))
  out.QB = by('passing', ['QB'])[0] ?? null
  out.RB = by('rushing', ['RB', 'FB'])[0] ?? null
  const rec = by('receiving', ['WR'])
  out.WR1 = rec[0] ?? null; out.WR2 = rec[1] ?? null; out.WR3 = rec[2] ?? null
  out.TE = by('receiving', ['TE'])[0] ?? null
  const dl = by('defensive', ['DL', 'DE', 'DT', 'NT'])
  const lb = by('defensive', ['LB', 'ILB', 'OLB', 'MLB'])
  const cb = by('defensive', ['CB'])
  const sf = by('defensive', ['S', 'FS', 'SS', 'DB'])
  dl.slice(0, 4).forEach((p, i) => { out[`DL${i + 1}`] = p })
  lb.slice(0, 3).forEach((p, i) => { out[`LB${i + 1}`] = p })
  cb.slice(0, 2).forEach((p, i) => { out[`CB${i + 1}`] = p })
  sf.slice(0, 2).forEach((p, i) => { out[`S${i + 1}`] = p })
  return { starters: out, offFormation: null, defFormation: null, source: 'box score' }
}

/** Short signs about how a team plays, counted from this game's plays. Nothing here predicts. */
export interface Sign { label: string; detail: string; tone: 'hot' | 'cool' | 'neutral' }

export function playbookSigns(plays: readonly FbPlay[] | undefined, offenseId: string, down: number | null, distance: number | null, offFormation: string | null, defFormation: string | null): Sign[] {
  const signs: Sign[] = []
  const mine = (plays ?? []).filter((p) => (p.offense ?? p.start.team) === offenseId).map((p) => ({ p, k: parseFbPlay(p) })).filter((x) => ['pass', 'incomplete', 'interception', 'sack', 'rush'].includes(x.k.kind))
  if (!mine.length) return offFormation || defFormation ? [{ label: 'BASE', detail: [offFormation, defFormation].filter(Boolean).join(' vs '), tone: 'neutral' }] : []
  const gun = mine.filter((x) => x.k.formation === 'shotgun' || x.k.formation === 'pistol').length
  signs.push({ label: gun / mine.length >= 0.5 ? 'SHOTGUN TEAM' : 'UNDER CENTER', detail: `${Math.round((gun / mine.length) * 100)}% of snaps from the gun`, tone: 'neutral' })
  const bucket = (dn: number | null, ds: number | null) => dn == null || ds == null ? null : `${dn}-${ds <= 3 ? 's' : ds <= 7 ? 'm' : 'l'}`
  const here = bucket(down, distance)
  if (here) {
    const same = mine.filter((x) => bucket(x.p.start.down, x.p.start.distance) === here)
    const passes = same.filter((x) => x.k.kind !== 'rush').length
    if (same.length >= 2) signs.push({ label: passes / same.length >= 0.6 ? 'PASS SPOT' : passes / same.length <= 0.4 ? 'RUN SPOT' : 'MIXED SPOT', detail: `${passes} pass / ${same.length - passes} run in this down and distance this game`, tone: passes / same.length >= 0.6 ? 'hot' : 'cool' })
  }
  const runs = mine.filter((x) => x.k.kind === 'rush' && x.k.gap)
  if (runs.length >= 3) {
    const left = runs.filter((x) => /left/.test(x.k.gap ?? '')).length
    const right = runs.filter((x) => /right/.test(x.k.gap ?? '')).length
    const mid = runs.length - left - right
    const best = Math.max(left, right, mid)
    signs.push({ label: best === left ? 'RUNS LEFT' : best === right ? 'RUNS RIGHT' : 'RUNS MIDDLE', detail: `${left} left · ${mid} middle · ${right} right of ${runs.length} runs`, tone: 'neutral' })
  }
  const air = mine.filter((x) => x.k.kind === 'pass' || x.k.kind === 'incomplete').filter((x) => x.k.depth)
  if (air.length >= 3) {
    const deep = air.filter((x) => x.k.depth === 'deep').length
    signs.push({ label: deep / air.length >= 0.3 ? 'DEEP SHOTS' : 'SHORT GAME', detail: `${deep} of ${air.length} charted passes were deep`, tone: deep / air.length >= 0.3 ? 'hot' : 'cool' })
  }
  if (offFormation || defFormation) signs.push({ label: 'BASE', detail: [offFormation && `offense ${offFormation}`, defFormation && `defense ${defFormation}`].filter(Boolean).join(' · '), tone: 'neutral' })
  return signs.slice(0, 4)
}

/** The current drive: plays and yards since the offence last changed, counted up to and including `upTo`. */
export function driveSummary(plays: readonly FbPlay[] | undefined, upToId: string | null): { plays: number; yards: number } | null {
  if (!plays?.length) return null
  const idx = upToId ? plays.findIndex((p) => p.id === upToId) : plays.length - 1
  if (idx < 0) return null
  const team = plays[idx].offense ?? plays[idx].start.team
  let n = 0
  let yards = 0
  for (let i = idx; i >= 0; i -= 1) {
    const p = plays[i]
    if ((p.offense ?? p.start.team) !== team) break
    const k = parseFbPlay(p).kind
    if (['pass', 'incomplete', 'interception', 'sack', 'rush'].includes(k)) { n += 1; yards += p.yards ?? 0 }
  }
  return { plays: n, yards }
}


/** A team colour that can be seen on the dark UI: the primary unless it is nearly black, then the alternate. */
export function visibleColor(team: { color: string | null; alt: string | null }, fallback: string): string {
  const lum = (c: string | null) => { if (!c || !/^[0-9a-fA-F]{6}$/.test(c)) return 0; const n = parseInt(c, 16); return (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255 }
  if (lum(team.color) >= 0.22) return `#${team.color}`
  if (lum(team.alt) >= 0.22) return `#${team.alt}`
  return fallback
}
