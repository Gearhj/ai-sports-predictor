/**
 * ALERTS (borrowed from Action Network's game alerts; free). One watcher for the whole site, started by the bell in the
 * left menu. It follows the games on your My Picks slip, AISP's own open paper bets and your Live View favourite teams,
 * and tells you when a game gets underway, when someone scores (every score in football, hockey and baseball; lead
 * changes in basketball), when it goes final (with whether your pick won), and when the market's fair price on a slip
 * pick moves 2+ points after you added it. Alerts show as toasts on any page and, if you allow it, as desktop
 * notifications. Settings live in this browser only.
 *
 * Feeds: ESPN scoreboards and AISP's closing-line consensus via the test-mode preview, AISP Mission Control for open
 * bets. Read-only; nothing is sent anywhere.
 */
import { useEffect, useState, useSyncExternalStore } from 'react'
import { listPicks, type PreviewPick } from '../../services/previewPicks'
import './alerts.css'

export interface AispAlert { id: string; at: number; kind: 'start' | 'score' | 'lead' | 'final' | 'price'; title: string; body: string; good?: boolean | null }
interface Settings { desktop: boolean; slip: boolean; bets: boolean; favs: boolean }
const SKEY = 'aisp.alerts.settings.v1'; const AKEY = 'aisp.alerts.log.v1'
const LEAGUE: Record<string, string> = { NFL: 'nfl', CFB: 'cfb', NBA: 'nba', NHL: 'nhl', MLB: 'mlb' }
const ALIAS: Record<string, Record<string, string>> = { nba: { CHO: 'CHA', PHO: 'PHX', GSW: 'GS', NOP: 'NO', BRK: 'BKN', NYK: 'NY', SAS: 'SA', UTA: 'UTAH', WAS: 'WSH' }, nhl: { TBL: 'TB', UTA: 'UTAH', LAK: 'LA', NJD: 'NJ', SJS: 'SJ' } }

const read = <T,>(k: string, fb: T): T => { try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : fb } catch { return fb } }
const write = (k: string, v: unknown) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* private window */ } }

// ------------------------------------------------------------------ store --
let settings: Settings = read(SKEY, { desktop: false, slip: true, bets: true, favs: true })
let log: AispAlert[] = read(AKEY, [])
let unread = 0
let toasts: AispAlert[] = []
const subs = new Set<() => void>()
let version = 0
const emit = () => { version += 1; subs.forEach((f) => f()) }
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f) } }
function push(a: AispAlert) {
  if (log.some((x) => x.id === a.id)) return
  log = [a, ...log].slice(0, 40); write(AKEY, log); unread += 1
  toasts = [...toasts, a].slice(-4); emit()
  window.setTimeout(() => { toasts = toasts.filter((t) => t.id !== a.id); emit() }, 7000)
  if (settings.desktop && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
    try { new Notification(a.title, { body: a.body, tag: a.id }) } catch { /* not allowed here */ }
  }
}
export function setSettings(p: Partial<Settings>) { settings = { ...settings, ...p }; write(SKEY, settings); emit() }

// ---------------------------------------------------------------- watcher --
type EspnTeam = { abbr: string; name: string; short: string; location: string; score: number }
type EspnGame = { id: string; state: 'pre' | 'in' | 'post'; detail: string; period: number; clock: string; home: EspnTeam; away: EspnTeam }
type Follow = { key: string; sport: string; home: string; away: string; why: string; side?: 'HOME' | 'AWAY' | null; pick?: PreviewPick }
const norm = (v: string | null | undefined) => (v ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '')
const etDay = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()).replace(/-/g, '')
function matches(t: EspnTeam, want: string, lg: string): boolean {
  const w = norm(ALIAS[lg]?.[want.toUpperCase()] ?? want)
  return [t.abbr, t.name, t.short, t.location].some((k) => { const n = norm(k); return n === w || (w.length > 4 && n.length > 4 && (n.includes(w) || w.includes(n))) })
}
const prev = new Map<string, { state: string; home: number; away: number }>()
let started = false
let bets: Follow[] = []
let lastBets = 0
let lastPrice = 0

async function tick() {
  try {
    const follows: Follow[] = []
    if (settings.slip) listPicks().forEach((p) => follows.push({ key: `slip:${p.game_id}`, sport: p.sport, home: p.home, away: p.away, why: 'your slip', side: p.side, pick: p }))
    if (settings.bets) {
      if (Date.now() - lastBets > 180_000) {
        lastBets = Date.now()
        const d = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date())
        const mc = await fetch(`/api/v1/mission-control?date=${d}`, { headers: { accept: 'application/json' } }).then((r) => (r.ok ? r.json() : null)).catch(() => null)
        bets = ((mc?.sweating ?? []) as Array<{ sport: string; matchup: string; selection: string }>).map((s) => { const [away, home] = s.matchup.split(' @ '); return { key: `bet:${s.matchup}`, sport: s.sport, home, away, why: `AISP bet: ${s.selection}`, side: s.selection.startsWith(home) ? 'HOME' : s.selection.startsWith(away) ? 'AWAY' : null } })
      }
      follows.push(...bets)
    }
    const favs = settings.favs ? read<string[]>('aisp.live.favorites', []) : []
    const leagues = new Set(follows.map((f) => LEAGUE[f.sport]).filter(Boolean))
    if (favs.length) ['nfl', 'nba', 'nhl', 'mlb'].forEach((l) => leagues.add(l))
    const day = etDay()
    for (const lg of leagues) {
      const sb = await fetch(`/preview-api/scores?league=${lg}&date=${day}`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
      for (const g of ((sb?.games ?? []) as EspnGame[])) {
        const f = follows.find((x) => LEAGUE[x.sport] === lg && matches(g.home, x.home, lg) && matches(g.away, x.away, lg))
        const fav = !f && favs.some((a) => matches(g.home, a, lg) || matches(g.away, a, lg))
        if (!f && !fav) continue
        const why = f?.why ?? 'favourite team'
        const was = prev.get(g.id)
        prev.set(g.id, { state: g.state, home: g.home.score, away: g.away.score })
        if (!was) continue // first look sets the baseline: no alert for what already happened
        const line = `${g.away.abbr} ${g.away.score} – ${g.home.score} ${g.home.abbr}`
        if (was.state === 'pre' && g.state === 'in') push({ id: `${g.id}:start`, at: Date.now(), kind: 'start', title: `Underway: ${g.away.short} @ ${g.home.short}`, body: `${why} · ${g.detail}` })
        if (g.state === 'in' && (g.home.score !== was.home || g.away.score !== was.away)) {
          const scorer = g.home.score - was.home > g.away.score - was.away ? g.home : g.away
          if (lg === 'nba') {
            const lead = (h: number, a: number) => Math.sign(h - a)
            if (lead(g.home.score, g.away.score) !== lead(was.home, was.away) && lead(g.home.score, g.away.score) !== 0) push({ id: `${g.id}:lead:${g.home.score}-${g.away.score}`, at: Date.now(), kind: 'lead', title: `Lead change: ${scorer.short} in front`, body: `${line} · ${g.detail} · ${why}` })
          } else push({ id: `${g.id}:score:${g.home.score}-${g.away.score}`, at: Date.now(), kind: 'score', title: `${scorer.short} score${lg === 'nhl' ? ' 🚨' : ''}`, body: `${line} · ${g.detail} · ${why}` })
        }
        if (was.state !== 'post' && g.state === 'post') {
          const homeWon = g.home.score > g.away.score
          const won = f?.side ? (f.side === 'HOME') === homeWon : null
          push({ id: `${g.id}:final`, at: Date.now(), kind: 'final', title: `Final: ${line}`, body: won == null ? why : `${why} · ${won ? 'your side WON' : 'your side lost'}`, good: won })
        }
      }
    }
    // the market's fair price on a slip pick moved 2+ points since it was added
    if (settings.slip && Date.now() - lastPrice > 180_000) {
      lastPrice = Date.now()
      const picks = listPicks().filter((p) => p.market_prob != null)
      if (picks.length) {
        const cl = await fetch('/preview-api/insights/closes?days=3').then((r) => (r.ok ? r.json() : null)).catch(() => null)
        for (const p of picks) {
          const c = cl?.games?.[p.game_id]; if (!c?.home) continue
          const nowP = p.side === 'HOME' ? c.home : 1 - c.home
          const move = (nowP - (p.market_prob ?? nowP)) * 100
          if (Math.abs(move) >= 2) push({ id: `${p.game_id}:price:${move > 0 ? 'up' : 'down'}`, at: Date.now(), kind: 'price', title: `Price moved ${move > 0 ? 'your way' : 'against you'}: ${p.selection}`, body: `Market fair price for your side ${(p.market_prob! * 100).toFixed(0)}% → ${(nowP * 100).toFixed(0)}% since you added it`, good: move > 0 })
        }
      }
    }
  } catch { /* a feed hiccup never breaks the page */ }
}
function start() { if (started || typeof window === 'undefined') return; started = true; void tick(); window.setInterval(() => { void tick() }, 45_000) }

// --------------------------------------------------------------------- UI --
export function AlertsBell({ compact = false }: { compact?: boolean }) {
  useSyncExternalStore(subscribe, () => version, () => version)
  const [open, setOpen] = useState(false)
  useEffect(() => { start() }, [])
  const perm = typeof Notification === 'undefined' ? 'unsupported' : Notification.permission
  const enableDesktop = async () => {
    if (typeof Notification === 'undefined') return
    const p = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission().catch(() => 'denied' as NotificationPermission)
    setSettings({ desktop: p === 'granted' })
  }
  return (
    <>
      <button type="button" className={`al-bell${compact ? ' is-compact' : ''}${unread ? ' has-new' : ''}`} onClick={() => { setOpen((v) => !v); unread = 0; emit() }} aria-expanded={open} aria-label={`Alerts${unread ? `, ${unread} new` : ''}`}>
        <i aria-hidden="true">🔔</i>{compact ? null : <span>Alerts</span>}{unread ? <em>{unread}</em> : null}
      </button>
      {open ? (
        <div className="al-panel" role="dialog" aria-label="Alerts">
          <header><b>Alerts</b><button type="button" onClick={() => setOpen(false)} aria-label="Close">✕</button></header>
          <div className="al-set">
            {([['slip', 'Games on my slip'], ['bets', "AISP's open bets"], ['favs', 'My favourite teams']] as Array<[keyof Settings, string]>).map(([k, l]) => <label key={k}><input id={`al-${k}`} type="checkbox" checked={settings[k]} onChange={(e) => setSettings({ [k]: e.target.checked })} />{l}</label>)}
            {perm === 'granted' ? <label><input id="al-desktop" type="checkbox" checked={settings.desktop} onChange={(e) => setSettings({ desktop: e.target.checked })} />Desktop notifications</label>
              : perm === 'unsupported' ? <small>This browser has no desktop notifications; alerts show on the page.</small>
              : <button type="button" className="al-enable" onClick={() => { void enableDesktop() }}>Turn on desktop notifications</button>}
          </div>
          <ol className="al-list">{log.length ? log.map((a) => <li key={a.id} className={`k-${a.kind}${a.good === true ? ' is-good' : a.good === false ? ' is-bad' : ''}`}><b>{a.title}</b><span>{a.body}</span><small>{new Date(a.at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}</small></li>) : <li className="al-empty">Nothing yet. Alerts start when a followed game gets underway, someone scores, it goes final, or a slip price moves 2+ points.</li>}</ol>
        </div>
      ) : null}
      <div className="al-toasts" aria-live="polite">{toasts.map((t) => <div key={t.id} className={`al-toast k-${t.kind}${t.good === true ? ' is-good' : t.good === false ? ' is-bad' : ''}`}><b>{t.title}</b><span>{t.body}</span></div>)}</div>
    </>
  )
}
