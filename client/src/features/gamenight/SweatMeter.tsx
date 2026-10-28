/**
 * THE SWEAT METER (Game Night): one heart-monitor lane per live game on AISP's slate. The beat speeds up and
 * the spikes grow as the game gets closer and later (see sweat.ts); a score change fires a big spike and a
 * flash. Lanes with skin in the game come first: YOUR Arena call or locked pick (emerald) and AISP's open paper
 * bet (gold), each saying whether that side is ahead right now.
 *
 * Scores: ESPN's public scoreboard through the test-mode preview server (/preview-api/scores), read every 20 s.
 * A feel-meter for the broadcast, not a probability and never a wager signal.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { matches, type EspnGame } from '../flow/Alerts'
import { useArena } from '../arena/arenaStore'
import { usePreviewPicks } from '../../services/previewPicks'
import { operationalToday } from '../../services/operationalDate'
import { resultOf, usePulse, type PulseGame } from '../pulse/pulseData'
import { bpmOf, ecg, progressOf, sweatOf, tierOf, type League } from './sweat'
import { simulateNight, useRehearsal } from '../rehearsal/rehearsal'
import { horn } from '../progress/sound'
import './sweat.css'

const LEAGUE: Record<string, League> = { NFL: 'nfl', CFB: 'cfb', NBA: 'nba', NHL: 'nhl', MLB: 'mlb' }
const COLOR: Record<League, string> = { nfl: '#34d399', cfb: '#c084fc', nba: '#ff9f43', nhl: '#67e8f9', mlb: '#ff6b6b' }
type Side = 'HOME' | 'AWAY'

interface Lane {
  id: string; lg: League; away: string; home: string; awayScore: number; homeScore: number; detail: string
  sweat: number; you: Side | null; aisp: Side | null; simulated?: boolean
}

/** ESPN's public scoreboard per league, read every 20 s (also used by Focus mode). */
export function useEspn(leagues: League[], day: string): Record<League, EspnGame[]> {
  const [data, setData] = useState<Record<string, EspnGame[]>>({})
  const key = leagues.join(',')
  useEffect(() => {
    if (!leagues.length) return
    let alive = true
    const ymd = day.replace(/-/g, '')
    const load = async () => {
      const out: Record<string, EspnGame[]> = {}
      await Promise.all(leagues.map(async (lg) => {
        const body = await fetch(`/preview-api/scores?league=${lg}&date=${ymd}`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
        out[lg] = (body?.games ?? []) as EspnGame[]
      }))
      if (alive) setData(out)
    }
    void load()
    const t = window.setInterval(load, 20_000)
    return () => { alive = false; window.clearInterval(t) }
  }, [key, day]) // eslint-disable-line react-hooks/exhaustive-deps
  return data as Record<League, EspnGame[]>
}

const sideOfSel = (g: PulseGame, sel: string | null | undefined): Side | null => {
  const s = (sel ?? '').toUpperCase()
  if (!s) return null
  if (s.includes(g.home.abbr.toUpperCase()) || s.includes('HOME')) return 'HOME'
  if (s.includes(g.away.abbr.toUpperCase()) || s.includes('AWAY')) return 'AWAY'
  return null
}

export function SweatMeter() {
  const day = operationalToday()
  const pulse = usePulse(day)
  const arena = useArena()
  const picks = usePreviewPicks()
  const [sim, setSim] = useState(false)
  const reh = useRehearsal()
  const todays = useMemo(() => pulse.games.filter((g) => g.day === day), [pulse.games, day])
  const leagues = useMemo(() => [...new Set(todays.map((g) => LEAGUE[g.sport]).filter(Boolean))].sort() as League[], [todays])
  const espn = useEspn(leagues, day)

  const lanes = useMemo<Lane[]>(() => {
    const out: Lane[] = []
    if (reh.active) {
      // REHEARSAL: last night on the simulated clock (scores climb toward the real finals).
      for (const { game: g, clock } of simulateNight(pulse.games, reh.day, reh.now)) {
        const lg = LEAGUE[g.sport]
        if (!lg || g.phase !== 'LIVE' || !g.score || !clock) continue
        const call = arena.calls.find((c) => c.game_id === g.id)
        const pos = g.position ? sideOfSel(g, g.position.selection) : null
        out.push({ id: g.id, lg, away: g.away.abbr, home: g.home.abbr, awayScore: g.score.away, homeScore: g.score.home, detail: clock.detail,
          sweat: sweatOf(lg, g.score.home - g.score.away, progressOf(lg, clock.period, clock.clock, clock.detail)), you: call?.side ?? null, aisp: pos, simulated: true })
      }
      const skin = (l: Lane) => (l.you || l.aisp ? 1 : 0)
      return out.sort((a, b) => skin(b) - skin(a) || b.sweat - a.sweat)
    }
    for (const g of todays) {
      const lg = LEAGUE[g.sport]
      const e = (espn[lg] ?? []).find((x) => x.state === 'in' && matches(x.home, g.home.abbr, lg) && matches(x.away, g.away.abbr, lg))
      if (!e) continue
      const call = arena.calls.find((c) => c.game_id === g.id && c.mode === 'LIVE')
      const pick = picks.find((p) => p.game_id === g.id && p.status === 'locked')
      const pos = g.position && resultOf(g.position) === 'OPEN' ? sideOfSel(g, g.position.selection) : null
      const margin = e.home.score - e.away.score
      out.push({ id: g.id, lg, away: g.away.abbr, home: g.home.abbr, awayScore: e.away.score, homeScore: e.home.score, detail: e.detail,
        sweat: sweatOf(lg, margin, progressOf(lg, e.period, e.clock, e.detail)), you: call?.side ?? pick?.side ?? null, aisp: pos })
    }
    const skin = (l: Lane) => (l.you || l.aisp ? 1 : 0)
    return out.sort((a, b) => skin(b) - skin(a) || b.sweat - a.sweat)
  }, [todays, espn, arena.calls, picks, reh.active, reh.day, Math.floor(reh.now / 5_000), pulse.games]) // eslint-disable-line react-hooks/exhaustive-deps

  const shown = lanes.length ? lanes : sim ? SIMULATED : []
  const peak = shown.reduce((m, l) => Math.max(m, l.sweat), 0)
  return (
    <section className={`sw${peak >= 0.75 ? ' is-cardiac' : ''}${shown.length ? '' : ' sw--idle'}`} aria-labelledby="sw-title">
      <header className="sw__head">
        <h2 id="sw-title">THE SWEAT METER</h2>
        <span className="sw__sub">{reh.active ? `REHEARSAL · last night replayed · ${lanes.length} live` : lanes.length ? `${lanes.length} live · closest and latest beat fastest` : sim ? 'SIMULATED PREVIEW · not real games' : 'Lights up when tonight’s games go live'}</span>
        {!lanes.length ? <button type="button" className="sw__sim" onClick={() => setSim((v) => !v)}>{sim ? 'Stop preview' : 'Preview it'}</button> : null}
      </header>
      {shown.length ? (
        <div className="sw__lanes">{shown.map((l) => <SweatLane key={l.id} lane={l} />)}</div>
      ) : (
        <p className="sw__empty">{pulse.loading ? 'Reading tonight’s slate…' : (() => {
          const first = todays.filter((g) => Date.parse(g.startsAt) > Date.now()).sort((a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt))[0]
          return first ? `No game is live yet · it lights up at ${new Date(first.startsAt).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' })} ET when ${first.away.abbr} @ ${first.home.abbr} starts.` : 'No game is live right now.'
        })()}</p>
      )}
      <p className="sw__fine">Heart rate = how close the score is × how late it is (60 to 170 bpm). A feel-meter for the broadcast, not a probability or a wager signal. Scores: ESPN&apos;s public scoreboard, every 20 s.</p>
    </section>
  )
}

function SweatLane({ lane }: { lane: Lane }) {
  const ref = useRef<HTMLCanvasElement | null>(null)
  const [flash, setFlash] = useState(false)
  const scoreKey = `${lane.awayScore}-${lane.homeScore}`
  const prev = useRef(scoreKey)
  const kick = useRef(0)
  const live = useRef(lane)
  live.current = lane
  useEffect(() => {
    if (prev.current !== scoreKey) { kick.current = performance.now(); setFlash(true); if (lane.you || lane.aisp) horn(lane.lg); const t = window.setTimeout(() => setFlash(false), 1800); prev.current = scoreKey; return () => window.clearTimeout(t) }
    return undefined
  }, [scoreKey])
  useEffect(() => {
    const cv = ref.current
    if (!cv) return
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    let raf = 0
    let phase = 0
    let last = performance.now()
    const trail: number[] = []
    const draw = (now: number) => {
      const dpr = window.devicePixelRatio || 1
      const w = cv.clientWidth, h = cv.clientHeight
      if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr) }
      const g = cv.getContext('2d')
      if (!g) return
      const l = live.current
      const dt = Math.min(0.1, (now - last) / 1000); last = now
      phase = (phase + dt * (bpmOf(l.sweat) / 60)) % 1
      const boost = now - kick.current < 3000 ? 1.7 : 1
      const amp = (0.3 + 0.7 * l.sweat) * boost
      const step = Math.max(1, Math.round(w / 260))
      const steps = reduced ? 0 : Math.max(1, Math.round((dt * 120) / step))
      for (let i = 0; i < steps; i++) trail.push(ecg((phase + (i / steps) * dt * (bpmOf(l.sweat) / 60)) % 1, amp))
      while (trail.length > w / step) trail.shift()
      g.setTransform(dpr, 0, 0, dpr, 0, 0)
      g.clearRect(0, 0, w, h)
      g.strokeStyle = 'rgba(148,163,184,.12)'; g.lineWidth = 1
      for (let x = 0; x < w; x += 18) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke() }
      const color = COLOR[l.lg]
      g.strokeStyle = color; g.lineWidth = 2; g.shadowColor = color; g.shadowBlur = 8 + 14 * l.sweat
      g.beginPath()
      trail.forEach((v, i) => { const x = w - (trail.length - i) * step, y = h * 0.62 - v * h * 0.5; if (i) g.lineTo(x, y); else g.moveTo(x, y) })
      g.stroke()
      const lastV = trail[trail.length - 1] ?? 0
      g.shadowBlur = 18; g.fillStyle = '#fff'; g.beginPath(); g.arc(w - 2, h * 0.62 - lastV * h * 0.5, 3, 0, Math.PI * 2); g.fill()
      g.shadowBlur = 0
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [])
  const t = tierOf(lane.sweat)
  const lead = lane.homeScore === lane.awayScore ? null : lane.homeScore > lane.awayScore ? 'HOME' : 'AWAY'
  const tag = (who: 'YOU' | 'AISP', side: Side | null) => side ? <em className={`sw__tag sw__tag--${who.toLowerCase()}`}>{who}: {side === 'HOME' ? lane.home : lane.away} {lead == null ? 'level' : lead === side ? 'ahead' : 'behind'}</em> : null
  return (
    <article className={`sw__lane sw__lane--${t.tone}${flash ? ' is-flash' : ''}`} style={{ ['--sc' as string]: COLOR[lane.lg] }}>
      <div className="sw__info">
        <b className="sw__score">{lane.away} {lane.awayScore} – {lane.homeScore} {lane.home}</b>
        <small>{lane.lg.toUpperCase()} · {lane.detail}{lane.simulated ? ' · SIMULATED' : ''}</small>
        <span className="sw__tags">{tag('YOU', lane.you)}{tag('AISP', lane.aisp)}</span>
      </div>
      <canvas ref={ref} className="sw__ecg" aria-hidden="true" />
      <div className="sw__bpm" aria-label={`${t.label}, ${bpmOf(lane.sweat)} beats per minute`}>
        <b><i className="sw__heart" style={{ animationDuration: `${60 / bpmOf(lane.sweat)}s` }}>♥</i>{bpmOf(lane.sweat)}</b>
        <small>{t.label}</small>
      </div>
    </article>
  )
}

// Clearly labelled demo lanes so the meter can be seen before tonight's games start. Never mixed with live lanes.
const SIMULATED: Lane[] = [
  { id: 'sim1', lg: 'nba', away: 'MIN', home: 'CLE', awayScore: 104, homeScore: 105, detail: '1:12 - 4th', sweat: sweatOf('nba', 1, progressOf('nba', 4, '1:12', '')), you: 'AWAY', aisp: 'HOME', simulated: true },
  { id: 'sim2', lg: 'nhl', away: 'LAK', home: 'NYR', awayScore: 2, homeScore: 2, detail: '6:40 - 3rd', sweat: sweatOf('nhl', 0, progressOf('nhl', 3, '6:40', '')), you: 'HOME', aisp: null, simulated: true },
  { id: 'sim3', lg: 'nfl', away: 'DAL', home: 'PHI', awayScore: 17, homeScore: 10, detail: '8:02 - 3rd', sweat: sweatOf('nfl', 7, progressOf('nfl', 3, '8:02', '')), you: null, aisp: 'AWAY', simulated: true },
  { id: 'sim4', lg: 'nba', away: 'GSW', home: 'DEN', awayScore: 41, homeScore: 58, detail: '4:30 - 2nd', sweat: sweatOf('nba', 17, progressOf('nba', 2, '4:30', '')), you: null, aisp: null, simulated: true },
]
