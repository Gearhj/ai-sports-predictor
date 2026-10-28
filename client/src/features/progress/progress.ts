/**
 * SITE-WIDE PROGRESSION (owner 10-26: "the player card, XP bar and trophies on every page"). One level for the
 * whole platform, fed by everything a visitor does:
 *   - The Arena (calls against AISP: its own XP rules, see arenaStore.xpOf)
 *   - exploring: +25 XP the first time each page is opened
 *   - showing up: +50 XP per day visited, and a day streak
 *   - My Picks: +50 XP per pick locked before the start
 *   - You Call It (Live View): calling the next snap against the play-call model (youCallStore.xpOfCall)
 * Stored ONLY in this browser (localStorage), like the Arena and preview picks. Play only: XP has no cash value.
 * Everything is DERIVED from stored facts (visited pages, visit days, calls, locked picks), so the total can
 * always be recomputed and never double-counts.
 */
import { useEffect, useMemo, useState } from 'react'
import { usePreviewPicks, type PreviewPick } from '../../services/previewPicks'
import { useYouCalls, xpOfCall, type YouCall } from '../livefield/football/youCallStore'
import { badgesOf as arenaBadges, levelOf, titleOf, useArena, xpOf, type ArenaCall, type Badge } from '../arena/arenaStore'

export { levelOf, titleOf, type Badge }

const KEY = 'aisp-progress-v1'
const EVENT = 'aisp-progress'
export const XP_PAGE = 25
export const XP_DAY = 50
export const XP_PICK = 50
export const XP_QUEST = 50

/** ROOKIE QUESTS (Start page): four first steps that teach the site, +50 XP each, a trophy for all four. */
export interface Quest { id: string; title: string; how: string; to: string; done: boolean }
export function questsOf(pages: readonly string[], calls: readonly ArenaCall[]): Quest[] {
  return [
    { id: 'blind', title: 'Call a game blind', how: 'In The Arena, lock a winner before you flip AISP’s card.', to: '/arena', done: calls.some((c) => c.blind) },
    { id: 'live', title: 'Watch a game play by play', how: 'Open Live View and follow the field.', to: '/live', done: pages.includes('/live') },
    { id: 'why', title: 'See why AISP bet or passed', how: 'Daily Decisions runs every game through the Decision Machine.', to: '/decisions', done: pages.includes('/decisions') },
    { id: 'card', title: 'Open your Player Card', how: 'Your level, streak and trophies in one card.', to: '/player', done: pages.includes('/player') },
  ]
}

interface Stored { pages: Record<string, string>; days: string[]; seenBadges: string[]; seenLevel: number }
const EMPTY: Stored = { pages: {}, days: [], seenBadges: [], seenLevel: 0 }

function read(): Stored {
  try { const raw = localStorage.getItem(KEY); return raw ? { ...EMPTY, ...(JSON.parse(raw) as Stored) } : EMPTY } catch { return EMPTY }
}
function write(s: Stored): void {
  try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* storage unavailable */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT))
}

/** Normalise a path to the page it belongs to: /games/abc -> /games/:id, so each game is not a new page. */
export function pageKey(pathname: string): string {
  const p = pathname.replace(/\/+$/, '') || '/'
  return p.replace(/\/(games|nfl\/games|nba\/games|nhl\/games|college-football\/games|market-pulse)\/[^/]+/, '/$1/:id')
}
const etDay = (d = new Date()) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(d)

/** Record a visit: the page (first time only) and today's date (once per day). */
export function trackVisit(pathname: string, now = new Date()): void {
  const s = read()
  const key = pageKey(pathname)
  const day = etDay(now)
  if (s.pages[key] && s.days.includes(day)) return
  write({ ...s, pages: s.pages[key] ? s.pages : { ...s.pages, [key]: now.toISOString() }, days: s.days.includes(day) ? s.days : [...s.days, day].sort() })
}
export function markProgressSeen(badges: string[], level: number): void { const s = read(); write({ ...s, seenBadges: badges, seenLevel: level }) }
export function resetProgress(): void { write(EMPTY) }

/** Longest and current run of consecutive visit days (current only counts if it reaches today or yesterday). */
export function streakOf(days: string[], today = etDay()): { current: number; best: number } {
  const sorted = [...new Set(days)].sort()
  let best = 0, run = 0, prev: number | null = null
  const toN = (d: string) => Math.round(Date.parse(`${d}T12:00:00Z`) / 86_400_000)
  for (const d of sorted) { const n = toN(d); run = prev != null && n === prev + 1 ? run + 1 : 1; best = Math.max(best, run); prev = n }
  const last = sorted.length ? toN(sorted[sorted.length - 1]) : null
  const current = last != null && toN(today) - last <= 1 ? run : 0
  return { current, best }
}

export interface Progress {
  xp: number
  level: number
  into: number
  need: number
  title: string
  sources: Array<{ label: string; xp: number }>
  badges: Badge[]
  streak: { current: number; best: number }
  pages: number
  arena: { calls: number; settled: number; right: number; beatAisp: number; aispRight: number; bestCombo: number; blindRight: number }
  picksLocked: number
  quests: Quest[]
}

const GLASS_BOX = ['/mission-control', '/decisions', '/methodology']

export function computeProgress(stored: Stored, calls: ArenaCall[], picks: PreviewPick[], today = etDay(), allSnaps: YouCall[] = []): Progress {
  const snaps = allSnaps.filter((c) => !c.event.startsWith('rehearsal:')) // rehearsal snaps never count
  const arenaXp = calls.reduce((t, c) => t + xpOf(c).xp, 0)
  const pages = Object.keys(stored.pages)
  const locked = picks.filter((p) => p.status === 'locked')
  const sources = [
    { label: 'The Arena', xp: arenaXp },
    { label: `Exploring (${pages.length} pages)`, xp: pages.length * XP_PAGE },
    { label: `Showing up (${stored.days.length} days)`, xp: stored.days.length * XP_DAY },
    { label: `Picks locked (${locked.length})`, xp: locked.length * XP_PICK },
    { label: `You Call It (${snaps.filter((c) => c.result).length} snaps)`, xp: snaps.reduce((t, c) => t + xpOfCall(c), 0) },
  ]
  const quests = questsOf(pages, calls)
  const questsDone = quests.filter((q) => q.done).length
  sources.push({ label: `Rookie quests (${questsDone}/${quests.length})`, xp: questsDone * XP_QUEST })
  const xp = sources.reduce((t, s) => t + s.xp, 0)
  const lv = levelOf(xp)
  const streak = streakOf(stored.days, today)
  const settled = calls.filter((c) => c.result && c.result.winner !== 'TIE')
  const right = settled.filter((c) => c.result!.winner === c.side)
  let bestCombo = 0, run = 0
  for (const c of [...settled].sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at))) { run = c.result!.winner === c.side ? run + 1 : 0; bestCombo = Math.max(bestCombo, run) }
  const b = (id: string, icon: string, name: string, how: string, got: boolean): Badge => ({ id, icon, name, how, got })
  const site: Badge[] = [
    b('explorer', '🧭', 'Explorer', 'Open 8 different pages', pages.length >= 8),
    b('cartographer', '🗺️', 'Cartographer', 'Open 15 different pages', pages.length >= 15),
    b('regular', '📅', 'Regular', 'Visit 3 days in a row', streak.best >= 3),
    b('ironman', '🛡️', 'Iron Man', 'Visit 7 days in a row', streak.best >= 7),
    b('locked', '🔒', 'Locked In', 'Lock your first My Picks pick', locked.length >= 1),
    b('glassbox', '🔍', 'Glass Box', 'Open Mission Control, Daily Decisions and Methodology', GLASS_BOX.every((p) => pages.includes(p))),
    b('graduate', '🎓', 'Rookie Graduate', 'Finish all four Rookie Quests', questsDone === quests.length),
    b('film', '🎬', 'Film Study', 'Open a Live View', pages.includes('/live')),
    b('caller', '📋', 'Play Caller', 'Call 10 snaps right in You Call It', snaps.filter((c) => c.result?.right).length >= 10),
    b('coordinator', '🧠', 'Coordinator', 'Beat the play-call model 5 times', snaps.filter((c) => c.result?.right && !c.result.modelRight).length >= 5),
  ]
  return {
    quests, xp, level: lv.level, into: lv.into, need: lv.need, title: titleOf(lv.level), sources, streak, pages: pages.length, picksLocked: locked.length,
    badges: [...arenaBadges(calls), ...site],
    arena: { calls: calls.filter((c) => c.mode === 'LIVE').length, settled: settled.length, right: right.length, beatAisp: right.filter((c) => c.result!.aisp && c.result!.aisp !== c.side).length,
      aispRight: settled.filter((c) => c.result!.aisp === c.result!.winner).length, bestCombo, blindRight: right.filter((c) => c.blind).length },
  }
}

export function useProgressStore(): Stored {
  const [s, set] = useState<Stored>(read)
  useEffect(() => {
    const on = () => set(read())
    window.addEventListener(EVENT, on); window.addEventListener('storage', on)
    // The menu's HUD (a child) records the visit in its own effect, which runs BEFORE this parent subscribes;
    // re-read once so that first update is never missed.
    on()
    return () => { window.removeEventListener(EVENT, on); window.removeEventListener('storage', on) }
  }, [])
  return s
}

export function useProgress(): Progress & { seen: { badges: string[]; level: number } } {
  const stored = useProgressStore()
  const arena = useArena(true) // the real record, never a rehearsal's
  const picks = usePreviewPicks()
  const snaps = useYouCalls()
  const p = useMemo(() => computeProgress(stored, arena.calls, picks, undefined, snaps), [stored, arena.calls, picks, snaps])
  return { ...p, seen: { badges: stored.seenBadges, level: stored.seenLevel } }
}
