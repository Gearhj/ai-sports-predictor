/** NBA / NHL live feed from the test-mode preview server (ESPN), plus the geometry both views share. Read-only. */
import { useEffect, useState } from 'react'

export interface ArenaTeam { id: string; abbr: string; name: string; short: string; color: string | null; alt: string | null; logo: string | null; score: number; record: string | null; linescores: Array<number | null> }
export interface ArenaPlayer { id: string; name: string | null; short: string | null; headshot: string | null; role?: string | null }
export interface ArenaPlay {
  id: string; seq: string; type: string | null; abbr: string | null; text: string | null; short: string | null; period: number | null; clock: string | null; team: string | null
  scoring: boolean; value: number | null; shooting: boolean; attempted: number | null; coord: { x: number; y: number } | null; away: number | null; home: number | null
  wallclock: string | null; players: ArenaPlayer[]; strength: string | null
}
export interface ArenaAthlete { id: string; name: string | null; short: string | null; jersey: string | null; pos: string | null; team: string | null; headshot: string | null; starter: boolean | null; stats: Record<string, string>; group: string | null }
export interface ArenaFeed {
  source: string; found: boolean; reason?: string; league: 'nba' | 'nhl'; event_id: string
  status: { state: 'pre' | 'in' | 'post'; detail: string | null; short: string | null; period: number | null; clock: string | null }
  teams: { home: ArenaTeam; away: ArenaTeam }
  plays: ArenaPlay[]; win_probability: Array<{ play: string; home: number }>
  on_ice: Array<{ team: string; id: string; name: string | null; short: string | null; pos: string | null; jersey: string | null; headshot: string | null }>
  leaders: Array<{ team: string; category: string; value: string; id: string; name: string; headshot: string | null }>
  athletes: ArenaAthlete[]; venue: string | null; retrieved_at: string
}

export function useArenaLive(league: 'nba' | 'nhl', date: string, home: string, away: string, live: boolean) {
  const [data, setData] = useState<ArenaFeed | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setData(null); setError(null)
    if (!home || !away) return undefined
    let alive = true
    const q = new URLSearchParams({ league, date, home, away })
    const load = () => fetch(`/preview-api/arena/live?${q.toString()}`, { headers: { accept: 'application/json' } })
      .then((r) => (r.ok && (r.headers.get('content-type') ?? '').includes('json') ? r.json() : Promise.reject(new Error(r.status === 404 ? 'preview-only feed' : `HTTP ${r.status}`))))
      .then((d: ArenaFeed) => { if (alive) { setData(d); setError(null) } })
      .catch((e: Error) => { if (alive) setError(e.message) })
    load()
    const t = window.setInterval(load, live ? 10_000 : 60_000)
    return () => { alive = false; window.clearInterval(t) }
  }, [league, date, home, away, live])
  return { data, error }
}

export const hex = (c: string | null | undefined, fb = '#94a3b8') => (c ? `#${c.replace('#', '')}` : fb)
/** A team colour that reads on navy: if the primary is too dark, use the alternate (or lift it). */
export function onNavy(primary: string | null | undefined, alt: string | null | undefined): string {
  const lum = (h: string) => { const v = h.replace('#', ''); if (v.length !== 6) return 0.5; const c = [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16) / 255); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] }
  const p = hex(primary); const a = hex(alt, '#cbd5e1')
  if (lum(p) >= 0.18) return p
  if (lum(a) >= 0.18 && lum(a) < 0.92) return a
  return `color-mix(in srgb, ${p} 55%, #ffffff)`
}

/** Seconds elapsed in the game at a play (NBA 12-min quarters / 5-min OT; NHL 20-min periods / 5-min OT). */
export function elapsedAt(league: 'nba' | 'nhl', period: number | null, clock: string | null): number {
  const per = period ?? 1
  const len = league === 'nba' ? (per <= 4 ? 720 : 300) : per <= 3 ? 1200 : 300
  const before = league === 'nba' ? Math.min(per - 1, 4) * 720 + Math.max(0, per - 5) * 300 : Math.min(per - 1, 3) * 1200 + Math.max(0, per - 4) * 300
  const [m, s] = (clock ?? '0:00').split(':').map((v) => Number(v))
  const left = Number.isFinite(m) ? m * 60 + (Number.isFinite(s) ? s : 0) : 0
  return before + Math.max(0, len - left)
}
export const periodName = (league: 'nba' | 'nhl', p: number | null) => (p == null ? '' : league === 'nba' ? (p <= 4 ? `Q${p}` : `OT${p - 4 > 1 ? p - 4 : ''}`) : p <= 3 ? `P${p}` : p === 4 ? 'OT' : 'SO')

/** The two teams' colours on navy, kept apart: when they are too alike, the home side takes its alternate, else white. */
export function teamPair(home: { color: string | null; alt: string | null }, away: { color: string | null; alt: string | null }): { home: string; away: string } {
  const rgb = (h: string) => { const v = h.replace('#', ''); return v.length === 6 ? [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)) : null }
  const solid = (p: string | null, a: string | null) => { const c = onNavy(p, a); return c.startsWith('#') ? c : hex(p) }
  const a = solid(away.color, away.alt); let h = solid(home.color, home.alt)
  const dist = (x: string, y: string) => { const p = rgb(x); const q = rgb(y); return p && q ? Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) : 999 }
  if (dist(a, h) < 120) {
    const alt = hex(home.alt, '#f8fafc')
    h = dist(a, alt) >= 120 && rgb(alt) && (rgb(alt)!.reduce((s, v) => s + v, 0) > 150) ? alt : '#f8fafc'
  }
  return { home: h, away: a }
}
