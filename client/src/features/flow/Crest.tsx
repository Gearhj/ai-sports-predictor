/**
 * A team's mark, everywhere on the new pages. Pro leagues use AISP's TeamLogo; college teams use ESPN's logo
 * and colour (FBS and FCS, from the test-mode preview feed /preview-api/logos/cfb, loaded once and cached),
 * matched by school name with accents and punctuation ignored. An initials crest is only the fallback when no
 * logo is found or the image fails to load.
 */
import { useState, useSyncExternalStore } from 'react'
import { TeamLogo } from '../../components/TeamLogo'
import type { SportCode } from '../../services/commandCenter'

export interface CfbTeam { id: string; abbr: string | null; location: string | null; short: string | null; display: string | null; nickname: string | null; color: string | null; alt: string | null; logo: string | null; fbs?: boolean }
const norm = (s: string | null | undefined) => (s ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/&/g, 'and').replace(/\bst\b\.?/g, 'state').replace(/[^a-z0-9]/g, '')

let table: Map<string, CfbTeam> | null = null
let loading = false
const subs = new Set<() => void>()
function load() {
  if (table || loading || typeof window === 'undefined') return
  loading = true
  fetch('/preview-api/logos/cfb', { headers: { accept: 'application/json' } })
    .then((r) => (r.ok && (r.headers.get('content-type') ?? '').includes('json') ? r.json() : { teams: [] }))
    .then((b: { teams?: CfbTeam[] }) => {
      const m = new Map<string, CfbTeam>()
      const teams = [...(b.teams ?? [])].sort((a, c) => Number(Boolean(c.fbs)) - Number(Boolean(a.fbs)))
      for (const t of teams) for (const k of [t.location, t.short, t.display, t.abbr, `${t.location ?? ''} ${t.nickname ?? ''}`]) { const key = norm(k); if (key && !m.has(key)) m.set(key, t) }
      table = m
    })
    .catch(() => { table = new Map() })
    .finally(() => { loading = false; subs.forEach((f) => f()) })
}
const subscribe = (f: () => void) => { subs.add(f); load(); return () => { subs.delete(f) } }
const snapshot = () => table

/** The ESPN team for a college name, or null (before the table loads, or when nothing matches). */
export function cfbTeam(name: string | null | undefined): CfbTeam | null {
  if (!table || !name) return null
  const k = norm(name)
  return table.get(k) ?? table.get(k.replace(/university$/, '')) ?? null
}
export function useCfbTable(): Map<string, CfbTeam> | null { return useSyncExternalStore(subscribe, snapshot, () => null) }
/** A team colour for gradients and accents: ESPN's for college, the caller's fallback otherwise. */
export function cfbColor(name: string): string | null { const t = cfbTeam(name); return t?.color ? `#${t.color.replace('#', '')}` : null }

const hueOf = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)
export function Crest({ sport, abbr, size }: { sport: SportCode | string; abbr: string; size: number }) {
  useCfbTable()
  const [broken, setBroken] = useState(false)
  if (sport !== 'CFB') return <TeamLogo league={sport as SportCode} abbreviation={abbr} size={size} />
  const t = cfbTeam(abbr)
  if (t?.logo && !broken) return <img className="sl-logo" src={t.logo} alt="" width={size} height={size} style={{ width: size, height: size }} onError={() => setBroken(true)} loading="lazy" />
  const words = abbr.replace(/[()]/g, '').split(/[\s-]+/).filter(Boolean)
  const ini = (words.length >= 2 ? words.slice(0, 3).map((w) => w[0]).join('') : abbr.slice(0, 3)).toUpperCase()
  return <span className="sl-crest" style={{ width: size, height: size, fontSize: Math.max(8, Math.round(size * 0.34)), background: `radial-gradient(circle at 30% 25%, hsl(${hueOf(abbr)} 60% 52%), hsl(${hueOf(abbr)} 55% 26%))` }} aria-hidden="true">{ini}</span>
}
