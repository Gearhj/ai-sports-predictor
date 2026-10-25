/**
 * MISSION CONTROL, redesigned (owner 10-24: "put the user first", navy + steel, a HOLY SHIT feature on every page).
 *
 * Step 3 of the night: is everything ready, and what happens next? One read model,
 * GET /mission-control (AISP_MISSION_CONTROL_V1), drawn four ways:
 *   1. six headline tiles: games, the next decision, bets entered, the shared $100, today's result, system health;
 *   2. SLATE FLOW (the HOLY SHIT feature): every game of the day travels the pipeline as a light, sport by sport,
 *      through forecast, approval, prices and the decision to its outcome, so the whole night reads as one picture;
 *      click any band to see exactly those games;
 *   3. THE NIGHT: a timeline of every game with its decision window and a now line;
 *   4. the board: each game's seven gates as a track, the next step in words, and every gate's sentence on a click.
 * The rail holds the departures board (what happens in the next hours), system health, the $100 gauge, bankrolls
 * and the bets being sweated.
 *
 * Read-only: nothing here posts, calls a provider or changes a decision. A stage the server did not send is UNKNOWN,
 * never promoted to done. Paper experiment only, never advice or a wager signal.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { AispSide } from '../flow/AispSide'
import { Crest, cfbColor } from '../flow/Crest'
import { GamePlanBar } from '../flow/GamePlanBar'
import { gameHref } from '../../services/commandCenter'
import { operationalToday, resolveOperationalDate, shiftDate } from '../../services/operationalDate'
import { teamIdentity } from '../../services/teamIdentity'
import { american, etClock, usd } from '../pulse/pulseData'
import {
  MISSION_SPORTS, STAGE_KEYS, STAGE_NAMES, SWEAT_WORDS, chipLabel, countdownText, isLive, jobSummary, nextStepLine, sinceText, sortRows,
  stageOf, stageTone, useMissionControl, useTick, type MissionControl, type MissionGame, type MissionSport, type StageKey,
} from '../mission/missionData'
import '../slate/slate.css'
import './control.css'

const SPORT: Record<MissionSport, { long: string; color: string; icon: string; hours: number }> = {
  MLB: { long: 'MLB', color: '#ff6b57', icon: '⚾', hours: 3 },
  NFL: { long: 'NFL', color: '#6ea4ff', icon: '🏈', hours: 3.25 },
  CFB: { long: 'College Football', color: '#b88cff', icon: '🏈', hours: 3.5 },
  NHL: { long: 'NHL', color: '#7cc4e4', icon: '🏒', hours: 2.6 },
  NBA: { long: 'NBA', color: '#ffb14a', icon: '🏀', hours: 2.4 },
}
const hueOf = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)
function teamColor(sport: MissionSport, abbr: string): string {
  if (sport !== 'CFB') { const id = teamIdentity(sport, abbr); if (id.known) return id.primary }
  return cfbColor(abbr) ?? `hsl(${hueOf(abbr)} 60% 55%)`
}
const shortName = (abbr: string, n = 14) => (abbr.length <= n ? abbr : abbr.split(/\s+/).map((w, i, a) => (i === a.length - 1 ? w : `${w[0]}.`)).join(' '))
const clean = (s: string | null | undefined) => (s ?? '').replace(/Â·/g, '·').trim()
const ms = (iso: string | null | undefined) => { const t = iso ? Date.parse(iso) : NaN; return Number.isFinite(t) ? t : null }
/** In play: AISP's live flag, or a game that has started and is not final (college games carry no live feed). */
const liveNow = (g: MissionGame) => !isFinalG(g) && (isLive(g) || g.phase === 'STARTED')
const isFinalG = (g: MissionGame) => g.phase === 'FINAL' || g.status === 'final'
const isFinal = (g: MissionGame) => g.phase === 'FINAL' || g.status === 'final'
const hasBet = (g: MissionGame) => stageOf(g, 'bet').state === 'DONE'
const outcomeOf = (g: MissionGame) => stageOf(g, 'result').outcome ?? null

type Tab = 'all' | 'attention' | 'next' | 'live' | 'bets' | 'final'
type Focus = { label: string; ids: Set<string> } | null

export function ControlPage() {
  const [params, setParams] = useSearchParams()
  const now = useTick(1000)
  const today = operationalToday(new Date(now))
  const day = resolveOperationalDate(params.get('date'))
  const resource = useMissionControl(day)
  const data = resource.data as MissionControl | null
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('all')
  const [sport, setSport] = useState<MissionSport | 'ALL'>('ALL')
  const [focus, setFocus] = useState<Focus>(null)
  const [open, setOpen] = useState<string | null>(null)
  const boardRef = useRef<HTMLElement | null>(null)

  const setDay = (next: string) => { setParams((prev) => { const o = new URLSearchParams(prev); if (next === today) o.delete('date'); else o.set('date', next); return o }, { replace: true }); setFocus(null); setOpen(null) }

  const games = useMemo(() => data?.games ?? [], [data])
  const live = games.filter(liveNow)
  const finals = games.filter(isFinal)
  const pre = games.filter((g) => !liveNow(g) && !isFinal(g) && g.phase !== 'VOID')
  const attention = games.filter((g) => g.attention === 'BLOCKED' || g.attention === 'WATCH')
  const bets = games.filter(hasBet)
  const nextDecision = useMemo(() => {
    const ups = games.map((g) => ({ g, at: ms(g.clocks?.decision_opens_at) })).filter((x) => x.at != null && x.at > now) as Array<{ g: MissionGame; at: number }>
    if (!ups.length) return null
    const first = Math.min(...ups.map((x) => x.at))
    return { at: first, games: ups.filter((x) => x.at === first).map((x) => x.g) }
  }, [games, now])
  const counts = data?.counts
  const allowance = data?.allowance
  const health = data?.health
  const jobs = jobSummary(health?.jobs)

  const shown = useMemo(() => {
    let list = games
    if (focus) list = list.filter((g) => focus.ids.has(g.game_id))
    if (sport !== 'ALL') list = list.filter((g) => g.sport === sport)
    if (tab === 'attention') list = list.filter((g) => g.attention === 'BLOCKED' || g.attention === 'WATCH')
    if (tab === 'next') list = list.filter((g) => !liveNow(g) && !isFinal(g))
    if (tab === 'live') list = list.filter(liveNow)
    if (tab === 'bets') list = list.filter(hasBet)
    if (tab === 'final') list = list.filter(isFinal)
    const sorted = sortRows(list)
    return [...sorted.filter(liveNow), ...sorted.filter((g) => !liveNow(g))]
  }, [games, focus, sport, tab])

  const showGames = (label: string, ids: string[]) => {
    setFocus({ label, ids: new Set(ids) }); setTab('all'); setSport('ALL')
    window.setTimeout(() => boardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 30)
  }
  const sportsToday = MISSION_SPORTS.filter((s) => games.some((g) => g.sport === s))
  const dayText = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(`${day}T12:00:00Z`))

  return (
    <div className="hq mx">
      <AispSide>
        <small className="hq-side__label">SPORTS TODAY</small>
        <nav className="hq-nav hq-nav--sports">
          {sportsToday.map((s) => <button key={s} type="button" className={sport === s ? 'is-on' : ''} onClick={() => setSport(sport === s ? 'ALL' : s)}><i aria-hidden="true">{SPORT[s].icon}</i>{s}<em>{games.filter((g) => g.sport === s).length}</em></button>)}
        </nav>
      </AispSide>
      <div className="hq-main">
        <header className="sl-top">
          <div className="sl-title"><small>Step 3 · Ready?</small><h1>Mission Control</h1><p>Every game, every gate, and exactly what happens next.</p></div>
          <span className="hq-date">
            <button type="button" aria-label="Previous day" onClick={() => setDay(shiftDate(day, -1))}>‹</button>
            <b>📅 {dayText}</b>
            <button type="button" aria-label="Next day" onClick={() => setDay(shiftDate(day, 1))}>›</button>
          </span>
          {day !== today ? <button type="button" className="mx-today" onClick={() => setDay(today)}>Back to today</button> : null}
          <span className={`mx-beacon is-${(health?.overall ?? 'unknown').toLowerCase()}`} title={(health?.issues ?? []).join('\n') || 'All systems normal'}><i />{health ? (health.overall === 'OK' ? 'All systems go' : health.overall === 'DEGRADED' ? 'Running, with issues' : 'Down') : resource.error ? 'Cannot reach AISP' : 'Checking…'}</span>
          <span className="mx-asof">{data ? `Updated ${sinceText(data.generated_at, now) ?? 'now'} ago` : ''}</span>
        </header>
        <GamePlanBar current="ready" />

        <div className="sl-layout">
          <main className="sl-content mx-content">
            <section className="sl-kpis" aria-label="The day at a glance">
              <Kpi tone="green" icon="▦" big={String(games.length)} label="Games Today" sub={`${live.length} live · ${pre.length} to go · ${finals.length} final`} onClick={() => { setFocus(null); setTab('all') }} />
              <Kpi tone="gold" icon="◷" big={nextDecision ? countdownText(new Date(nextDecision.at).toISOString(), now) ?? 'now' : '—'} label="Next Decision" sub={nextDecision ? `${etClock(new Date(nextDecision.at).toISOString())} ET · ${nextDecision.games.length} game${nextDecision.games.length === 1 ? '' : 's'}` : 'No more windows today'} onClick={nextDecision ? () => showGames(`Deciding ${etClock(new Date(nextDecision.at).toISOString())}`, nextDecision.games.map((g) => g.game_id)) : undefined} />
              <Kpi tone="violet" icon="▤" big={String(bets.length)} label="Bets Entered" sub={`${counts?.bets_open ?? 0} open · ${counts?.bets_settled ?? 0} settled`} onClick={() => { setFocus(null); setTab('bets') }} />
              <Kpi tone="teal" icon="$" big={allowance?.entered_usd != null ? usd(allowance.entered_usd) : '—'} label={`Of the $${allowance?.limit_usd ?? 100} today`} sub={allowance?.room_usd != null ? `${usd(allowance.room_usd)} room · ${allowance.entries ?? 0} entries` : allowance?.state === 'NOT_IN_FORCE' ? 'Allowance not in force' : 'Allowance unknown'} />
              <Kpi tone={(counts?.profit_usd ?? 0) >= 0 ? 'green' : 'red'} icon="±" big={counts ? usd(counts.profit_usd, true) : '—'} label="Today's Result" sub={counts ? `on ${usd(counts.staked_usd)} staked` : ''} />
              <Kpi tone={health?.overall === 'OK' ? 'green' : health?.overall === 'DEGRADED' ? 'orange' : 'red'} icon="⚙" big={health ? (health.overall === 'OK' ? 'All good' : `${health.issues.length} issue${health.issues.length === 1 ? '' : 's'}`) : '—'} label="System Health" sub={`${jobs.ok}/${jobs.total} jobs healthy${jobs.failed ? ` · ${jobs.failed} failed` : ''}`} onClick={() => document.getElementById('mx-health')?.scrollIntoView({ behavior: 'smooth' })} />
            </section>

            {games.length ? <SlateFlow games={games} onShow={showGames} /> : null}
            {games.length ? <NightTimeline games={games} now={now} isToday={day === today} onOpen={(g) => { setFocus(null); setTab('all'); setSport('ALL'); setOpen(g.game_id); window.setTimeout(() => document.getElementById(`mx-g-${g.game_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 40) }} /> : null}

            <section className="mx-board" ref={boardRef} aria-label="Every game, every gate">
              <header className="mx-board__head">
                <h2>Every game, every gate</h2>
                <span className="sl-tabs" role="tablist">
                  {([['all', 'All', games.length], ['attention', 'Needs a look', attention.length], ['next', 'Still to come', pre.length], ['live', 'Live', live.length], ['bets', 'Bets', bets.length], ['final', 'Final', finals.length]] as Array<[Tab, string, number]>).map(([k, l, n]) => <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? 'is-on' : ''} onClick={() => setTab(k)}>{l} <em>({n})</em></button>)}
                </span>
                {focus ? <button type="button" className="mx-focus" onClick={() => setFocus(null)}>{focus.label} · {focus.ids.size} <b aria-label="Clear">✕</b></button> : null}
              </header>
              <StageKey />
              <div className="mx-cards">
                {shown.map((g) => <GameCard key={g.game_id} g={g} now={now} open={open === g.game_id} onToggle={() => setOpen(open === g.game_id ? null : g.game_id)} onGo={() => navigate(gameHref(g.sport, g.source_game_id ?? g.game_id))} onLive={() => navigate(`/live?game=${g.game_id}`)} />)}
              </div>
              {!shown.length && data ? <p className="sl-empty">No games match.</p> : null}
              {!data && resource.loading ? <p className="sl-empty">Loading the board…</p> : null}
              {!data && resource.error ? <p className="sl-empty">Mission Control could not be read ({resource.error}). Nothing is assumed ready.</p> : null}
            </section>
            <footer className="hq-foot">Read-only. Every gate comes from AISP's Mission Control read model on one clock; a gate the server did not report is unknown, never done. Paper experiment only, never advice or a wager signal.</footer>
          </main>

          <aside className="sl-rail mx-rail" aria-label="Departures, health and money">
            <Departures games={games} now={now} onOpen={(g) => { setOpen(g.game_id); setFocus(null); setTab('all'); window.setTimeout(() => document.getElementById(`mx-g-${g.game_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 40) }} />
            <HealthCard data={data} now={now} />
            <AllowanceCard data={data} />
            <BankrollCard data={data} />
            <SweatCard data={data} onGo={(gid, sp) => navigate(gameHref(sp, gid))} />
          </aside>
        </div>
      </div>
    </div>
  )
}

function Kpi({ tone, icon, big, label, sub, onClick }: { tone: string; icon: string; big: string; label: string; sub: ReactNode; onClick?: () => void }) {
  const body = <><i className="sl-kpi__icon" aria-hidden="true">{icon}</i><span className="sl-kpi__text"><small>{label}</small><b>{big}</b><em>{sub}</em></span></>
  return onClick ? <button type="button" className={`sl-kpi tone-${tone}`} onClick={onClick}>{body}</button> : <div className={`sl-kpi tone-${tone}`}>{body}</div>
}

// ------------------------------------------------------------- SLATE FLOW --

type Col = { key: string; title: string; order: string[]; of: (g: MissionGame) => string }
const decisionNode = (g: MissionGame) => {
  const d = stageOf(g, 'decision')
  const c = String(d.class ?? '')
  if (c === 'BET') return 'Bet'
  if (c === 'RECORD_ONLY') return 'Record only'
  if (c === 'PASS') return 'Passed'
  if (c === 'NO_PRICE') return 'No price'
  if (c === 'BLOCKED') return 'Blocked'
  if (c === 'PENDING' || d.state === 'WAITING') return 'Still to decide'
  return 'Not recorded'
}
const COLS: Col[] = [
  { key: 'sport', title: 'THE SLATE', order: MISSION_SPORTS.map((s) => s), of: (g) => g.sport },
  { key: 'forecast', title: 'FORECAST', order: ['Forecast in', 'Waiting', 'Missing'], of: (g) => { const s = stageOf(g, 'forecast').state; return s === 'DONE' ? 'Forecast in' : s === 'WAITING' ? 'Waiting' : 'Missing' } },
  { key: 'approval', title: 'APPROVAL', order: ['Approved', 'Waiting', 'Not needed', 'Blocked', 'Unknown'], of: (g) => { const s = stageOf(g, 'approval').state; return s === 'DONE' ? 'Approved' : s === 'WAITING' ? 'Waiting' : s === 'BLOCKED' ? 'Blocked' : s === 'NOT_APPLICABLE' ? 'Not needed' : 'Unknown' } },
  { key: 'prices', title: 'PRICES', order: ['Priced', 'Waiting', 'Not needed', 'No price'], of: (g) => { const s = stageOf(g, 'prices').state; return s === 'DONE' ? 'Priced' : s === 'WAITING' ? 'Waiting' : s === 'NOT_APPLICABLE' ? 'Not needed' : 'No price' } },
  { key: 'decision', title: 'DECISION', order: ['Bet', 'Record only', 'Passed', 'Still to decide', 'No price', 'Blocked', 'Not recorded'], of: decisionNode },
  { key: 'result', title: 'OUTCOME', order: ['Won', 'Lost', 'Push / void', 'Open bet', 'No bet'], of: (g) => { if (!hasBet(g)) return 'No bet'; const o = outcomeOf(g); return o === 'WIN' ? 'Won' : o === 'LOSS' ? 'Lost' : o === 'PUSH' || o === 'VOID' ? 'Push / void' : 'Open bet' } },
]
const NODE_COLOR: Record<string, string> = {
  'Forecast in': '#2dd4bf', Approved: '#2dd4bf', Priced: '#2dd4bf', Waiting: '#fbbf24', 'Still to decide': '#fbbf24', Missing: '#f87171', Blocked: '#f87171', 'No price': '#fb923c', Unknown: '#94a3b8', 'Not needed': '#64748b', 'Not recorded': '#64748b',
  Bet: '#fbbf24', 'Record only': '#a78bfa', Passed: '#7c8aa0', Won: '#34d399', Lost: '#f87171', 'Push / void': '#94a3b8', 'Open bet': '#a78bfa', 'No bet': '#4b5563',
}
const colorOf = (col: number, label: string) => (col === 0 ? SPORT[label as MissionSport]?.color ?? '#94a3b8' : NODE_COLOR[label] ?? '#94a3b8')

type FNode = { col: number; label: string; ids: string[]; x: number; y: number; h: number }
type FLink = { col: number; from: FNode; to: FNode; ids: string[]; sy: number; ty: number; w: number }

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null)
  const [w, setW] = useState(1100)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const ro = new ResizeObserver(() => setW(Math.max(560, Math.round(el.clientWidth))))
    ro.observe(el); setW(Math.max(560, Math.round(el.clientWidth)))
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

function SlateFlow({ games, onShow }: { games: MissionGame[]; onShow: (label: string, ids: string[]) => void }) {
  const [wrapRef, W] = useWidth<HTMLDivElement>()
  const H = 360; const top = 34; const bottom = 10; const nodeW = 14; const gap = 10; const padL = 8; const padR = 150
  const [run, setRun] = useState(0)
  const [hover, setHover] = useState<{ x: number; y: number; title: string; lines: string[] } | null>(null)
  const gById = useMemo(() => new Map(games.map((g) => [g.game_id, g])), [games])
  // A stable signature: the flow only re-lays out (and replays) when a game actually changes lane.
  const sig = games.map((g) => `${g.game_id}:${COLS.map((c) => c.of(g)).join('|')}`).join(';')
  const layout = useMemo(() => {
    const ordered = [...games].sort((a, b) => MISSION_SPORTS.indexOf(a.sport) - MISSION_SPORTS.indexOf(b.sport) || Date.parse(a.starts_at) - Date.parse(b.starts_at))
    const N = ordered.length
    const colX = (i: number) => padL + (i * (W - padL - padR - nodeW)) / (COLS.length - 1)
    const groups = COLS.map((c) => { const m = new Map<string, string[]>(); ordered.forEach((g) => { const k = c.of(g); m.set(k, [...(m.get(k) ?? []), g.game_id]) }); return c.order.filter((k) => m.has(k)).map((k) => ({ label: k, ids: m.get(k)! })) })
    const k = Math.min(...groups.map((gs) => (H - top - bottom - gap * (gs.length - 1)) / Math.max(1, N)))
    const nodes: FNode[][] = groups.map((gs, i) => {
      const total = N * k + gap * (gs.length - 1)
      let y = top + (H - top - bottom - total) / 2
      return gs.map((gr) => { const n = { col: i, label: gr.label, ids: gr.ids, x: colX(i), y, h: Math.max(2, gr.ids.length * k) }; y += gr.ids.length * k + gap; return n })
    })
    const nodeOf = (col: number, g: MissionGame) => nodes[col].find((n) => n.label === COLS[col].of(g))!
    const links: FLink[] = []
    for (let i = 0; i < COLS.length - 1; i += 1) {
      const m = new Map<string, FLink>()
      ordered.forEach((g) => {
        const from = nodeOf(i, g); const to = nodeOf(i + 1, g); const key = `${from.label}>${to.label}`
        const l = m.get(key) ?? { col: i, from, to, ids: [], sy: 0, ty: 0, w: 0 }
        l.ids.push(g.game_id); m.set(key, l)
      })
      const ls = [...m.values()]
      ls.forEach((l) => { l.w = l.ids.length * k })
      nodes[i].forEach((n) => { let y = n.y; ls.filter((l) => l.from === n).sort((a, b) => a.to.y - b.to.y).forEach((l) => { l.sy = y; y += l.w }) })
      nodes[i + 1].forEach((n) => { let y = n.y; ls.filter((l) => l.to === n).sort((a, b) => a.from.y - b.from.y).forEach((l) => { l.ty = y; y += l.w }) })
      links.push(...ls)
    }
    // each game's light: its y inside every band it rides
    const trail = new Map<string, Array<{ x0: number; y0: number; x1: number; y1: number }>>()
    links.forEach((l) => l.ids.forEach((id, j) => {
      const off = ((j + 0.5) / l.ids.length) * l.w
      const t = trail.get(id) ?? []
      t[l.col] = { x0: l.from.x + nodeW, y0: l.sy + off, x1: l.to.x, y1: l.ty + off }
      trail.set(id, t)
    }))
    return { nodes, links, trail, order: ordered.map((g) => g.game_id) }
  }, [sig, W]) // eslint-disable-line react-hooks/exhaustive-deps

  // The lights run once per load (and on Replay), then rest: the picture stays still while you read it.
  const dotsRef = useRef<SVGGElement | null>(null)
  useEffect(() => {
    const g = dotsRef.current
    if (!g) return undefined
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const circles = Array.from(g.querySelectorAll('circle'))
    if (reduce) { circles.forEach((c) => c.setAttribute('opacity', '0')); return undefined }
    const LEG = 650; const STAGGER = Math.min(28, 1800 / Math.max(1, layout.order.length))
    const start = performance.now()
    let raf = 0
    const tick = (t: number) => {
      let busy = false
      circles.forEach((c, idx) => {
        const legs = layout.trail.get(layout.order[idx]) ?? []
        const local = t - start - idx * STAGGER
        const total = LEG * legs.length
        if (local < 0) { c.setAttribute('opacity', '0'); busy = true; return }
        if (local >= total) { c.setAttribute('opacity', '0'); return }
        busy = true
        const li = Math.min(legs.length - 1, Math.floor(local / LEG))
        const L = legs[li]; if (!L) return
        const u = (local - li * LEG) / LEG
        const e = u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2
        const xm = (L.x0 + L.x1) / 2
        const x = (1 - e) ** 3 * L.x0 + 3 * (1 - e) ** 2 * e * xm + 3 * (1 - e) * e * e * xm + e ** 3 * L.x1
        const y = (1 - e) ** 3 * L.y0 + 3 * (1 - e) ** 2 * e * L.y0 + 3 * (1 - e) * e * e * L.y1 + e ** 3 * L.y1
        c.setAttribute('cx', x.toFixed(1)); c.setAttribute('cy', y.toFixed(1))
        c.setAttribute('opacity', local > total - 240 ? String(Math.max(0, (total - local) / 240)) : '1')
      })
      if (busy) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [layout, run])

  const reasons = (ids: string[], key: StageKey | null) => {
    const m = new Map<string, number>()
    ids.forEach((id) => { const g = gById.get(id); if (!g) return; const lab = key ? clean(chipLabel(key, stageOf(g, key))) : g.matchup; m.set(lab, (m.get(lab) ?? 0) + 1) })
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([l, n]) => `${l}${n > 1 ? ` × ${n}` : ''}`)
  }
  const KEY_FOR: Array<StageKey | null> = [null, 'forecast', 'approval', 'prices', 'decision', 'result']
  const tip = (e: React.MouseEvent, title: string, lines: string[]) => {
    const r = wrapRef.current?.getBoundingClientRect()
    if (r) setHover({ x: e.clientX - r.left, y: e.clientY - r.top, title, lines })
  }
  const bets = games.filter(hasBet).length
  const won = games.filter((g) => outcomeOf(g) === 'WIN').length
  const lost = games.filter((g) => outcomeOf(g) === 'LOSS').length

  return (
    <section className="sl-card mx-flow" aria-label="Slate flow">
      <header className="sl-card__head">
        <h2>Slate Flow <small>where every game of the day went</small></h2>
        <span className="mx-flow__sum">{games.length} games → {bets} bets → <b className="is-up">{won} won</b> · <b className="is-down">{lost} lost</b> · {bets - won - lost} open or void</span>
        <button type="button" className="mx-replay" onClick={() => setRun((r) => r + 1)}>↻ Replay the day</button>
      </header>
      <div className="mx-flow__wrap" ref={wrapRef} onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={`${games.length} games flowing from forecast to outcome`}>
          <defs>
            <filter id="mx-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.4" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          </defs>
          {COLS.map((c, i) => <text key={c.key} x={layout.nodes[i]?.[0]?.x ?? 0} y={16} className="mx-flow__col">{c.title}</text>)}
          <g key={`l${run}`} className="mx-flow__links">
            {layout.links.map((l, i) => {
              const x0 = l.from.x + nodeW; const x1 = l.to.x; const xm = (x0 + x1) / 2
              const d = `M${x0},${l.sy} C${xm},${l.sy} ${xm},${l.ty} ${x1},${l.ty} L${x1},${l.ty + l.w} C${xm},${l.ty + l.w} ${xm},${l.sy + l.w} ${x0},${l.sy + l.w} Z`
              return <path key={i} d={d} fill={colorOf(l.col + 1, l.to.label)} style={{ animationDelay: `${l.col * 0.45}s` }} onMouseMove={(e) => tip(e, `${l.from.label} → ${l.to.label}`, [`${l.ids.length} game${l.ids.length === 1 ? '' : 's'}`, ...reasons(l.ids, null).slice(0, 4)])} onClick={() => onShow(`${l.from.label} → ${l.to.label}`, l.ids)} />
            })}
          </g>
          <g className="mx-flow__nodes">
            {layout.nodes.flat().map((n) => (
              <g key={`${n.col}-${n.label}`} onMouseMove={(e) => tip(e, `${n.label} · ${n.ids.length}`, n.col === 0 ? [`${n.ids.length} games on the ${SPORT[n.label as MissionSport]?.long ?? n.label} slate`] : reasons(n.ids, KEY_FOR[n.col]))} onClick={() => onShow(n.col === 0 ? n.label : `${COLS[n.col].title.toLowerCase()}: ${n.label}`, n.ids)}>
                <rect x={n.x} y={n.y} width={nodeW} height={n.h} rx={3} fill={colorOf(n.col, n.label)} filter="url(#mx-glow)" />
                <text x={n.x + nodeW + 6} y={n.y + n.h / 2} dy="0.35em" className="mx-flow__label"><tspan className="mx-flow__n">{n.ids.length}</tspan> {n.col === 0 ? (SPORT[n.label as MissionSport]?.long ?? n.label) : n.label}</text>
              </g>
            ))}
          </g>
          <g ref={dotsRef} key={`d${run}-${sig.length}`} className="mx-flow__dots" pointerEvents="none">
            {layout.order.map((id) => { const g = gById.get(id); return <circle key={id} r={3.4} cx={-10} cy={-10} opacity={0} fill={g ? SPORT[g.sport].color : '#fff'} filter="url(#mx-glow)" /> })}
          </g>
        </svg>
        {hover ? <div className="mx-tip" style={{ left: Math.min(hover.x + 14, W - 250), top: hover.y + 12 }}><b>{hover.title}</b>{hover.lines.map((l, i) => <span key={i}>{l}</span>)}<small>Click to see these games</small></div> : null}
      </div>
    </section>
  )
}

// ------------------------------------------------------------ THE NIGHT --

function NightTimeline({ games, now, isToday, onOpen }: { games: MissionGame[]; now: number; isToday: boolean; onOpen: (g: MissionGame) => void }) {
  const [wrapRef, W] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<{ x: number; y: number; g: MissionGame } | null>(null)
  const padL = 120; const padR = 16; const laneGap = 2; const axisH = 22
  const rows = useMemo(() => {
    const items = games.map((g) => {
      const start = Date.parse(g.starts_at)
      const dOpen = ms(g.clocks?.decision_opens_at) ?? start - 30 * 60_000
      const end = start + SPORT[g.sport].hours * 3600_000
      return { g, start, dOpen, dClose: ms(g.clocks?.decision_closes_at) ?? dOpen + 15 * 60_000, end }
    }).filter((x) => Number.isFinite(x.start))
    const t0 = Math.floor(Math.min(...items.map((x) => x.dOpen), isToday ? now : Infinity) / 3600_000) * 3600_000
    const t1 = Math.ceil(Math.max(...items.map((x) => x.end), isToday ? now + 1800_000 : 0) / 3600_000) * 3600_000
    const bands = MISSION_SPORTS.filter((s) => items.some((x) => x.g.sport === s)).map((s) => {
      const lanes: number[] = []
      const placed = items.filter((x) => x.g.sport === s).sort((a, b) => a.dOpen - b.dOpen).map((x) => {
        let li = lanes.findIndex((endAt) => endAt <= x.dOpen - 5 * 60_000)
        if (li < 0) { lanes.push(0); li = lanes.length - 1 }
        lanes[li] = x.end
        return { ...x, lane: li }
      })
      return { sport: s, lanes: lanes.length, placed }
    })
    // a big slate (44 college games) packs its lanes tighter so the whole night fits on one screen
    const most = Math.max(1, ...bands.map((x) => x.lanes))
    const totalLanes = bands.reduce((n, x) => n + x.lanes, 0)
    const lane = Math.max(12, Math.min(15, Math.floor(420 / Math.max(totalLanes, most))))
    return { t0, t1, bands, lane }
  }, [games, isToday, Math.floor(now / 60_000)]) // eslint-disable-line react-hooks/exhaustive-deps
  const lane = rows.lane
  const X = (t: number) => padL + ((t - rows.t0) / Math.max(1, rows.t1 - rows.t0)) * (W - padL - padR)
  let y = axisH
  const bandY = rows.bands.map((b) => { const at = y; y += b.lanes * (lane + laneGap) + 14; return at })
  const H = y + 4
  const hours: number[] = []
  for (let t = rows.t0; t <= rows.t1; t += 3600_000) hours.push(t)
  const fillOf = (g: MissionGame) => { const o = outcomeOf(g); if (o === 'WIN') return '#34d399'; if (o === 'LOSS') return '#f87171'; if (hasBet(g)) return '#a78bfa'; return isFinal(g) ? '#3a4352' : '#4a5568' }

  return (
    <section className="sl-card mx-night" aria-label="The night, hour by hour">
      <header className="sl-card__head"><h2>The Night <small>every game, its decision window, and where we are now</small></h2>
        <span className="mx-legend"><i style={{ background: '#fbbf24' }} />decision window <i style={{ background: '#a78bfa' }} />bet open <i style={{ background: '#34d399' }} />won <i style={{ background: '#f87171' }} />lost <i style={{ background: '#4a5568' }} />no bet</span>
      </header>
      <div className="mx-night__wrap" ref={wrapRef} onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="Timeline of today's games">
          <defs><pattern id="mx-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="rgba(52,211,153,.18)" /><line x1="0" y1="0" x2="0" y2="6" stroke="rgba(52,211,153,.7)" strokeWidth="2" /></pattern></defs>
          {hours.map((t) => <g key={t}><line x1={X(t)} x2={X(t)} y1={axisH - 4} y2={H} className="mx-night__grid" /><text x={X(t) + 3} y={13} className="mx-night__hour">{etClock(new Date(t).toISOString(), { hour: 'numeric' })}</text></g>)}
          {rows.bands.map((b, bi) => (
            <g key={b.sport}>
              <rect x={0} y={bandY[bi] - 3} width={W} height={b.lanes * (lane + laneGap) + 8} className="mx-night__band" />
              <text x={10} y={bandY[bi] + 12} className="mx-night__sport" fill={SPORT[b.sport].color}>{SPORT[b.sport].icon} {b.sport}</text>
              <text x={10} y={bandY[bi] + 26} className="mx-night__cnt">{b.placed.length} games</text>
              {b.placed.map((p) => {
                const yy = bandY[bi] + p.lane * (lane + laneGap)
                const x0 = X(p.start); const x1 = X(p.end); const live = liveNow(p.g)
                const label = `${shortName(p.g.away.abbr, 10)} @ ${shortName(p.g.home.abbr, 10)}`
                return (
                  <g key={p.g.game_id} className="mx-night__game" onMouseMove={(e) => { const r = wrapRef.current?.getBoundingClientRect(); if (r) setHover({ x: e.clientX - r.left, y: e.clientY - r.top, g: p.g }) }} onClick={() => onOpen(p.g)}>
                    <rect x={X(p.dOpen)} y={yy + 4} width={Math.max(2, X(p.dClose) - X(p.dOpen))} height={lane - 8} rx={2} fill="#fbbf24" opacity={0.9} />
                    <rect x={x0} y={yy} width={Math.max(3, x1 - x0)} height={lane} rx={4} fill={live ? 'url(#mx-hatch)' : fillOf(p.g)} stroke={hasBet(p.g) ? '#fbbf24' : live ? '#34d399' : 'rgba(255,255,255,.08)'} strokeWidth={hasBet(p.g) ? 1.4 : 1} opacity={isFinal(p.g) && !hasBet(p.g) ? 0.55 : 1} />
                    {x1 - x0 > label.length * 5.4 + 8 ? <text x={x0 + 5} y={yy + lane / 2} dy="0.35em" className="mx-night__lab">{label}</text> : null}
                  </g>
                )
              })}
            </g>
          ))}
          {isToday && now >= rows.t0 && now <= rows.t1 ? <g className="mx-night__now"><line x1={X(now)} x2={X(now)} y1={axisH - 6} y2={H} /><rect x={X(now) - 30} y={0} width={60} height={16} rx={8} /><text x={X(now)} y={11.5} textAnchor="middle">NOW {etClock(new Date(now).toISOString())}</text></g> : null}
        </svg>
        {hover ? <TimelineTip g={hover.g} x={Math.min(hover.x + 14, W - 270)} y={hover.y + 12} /> : null}
      </div>
    </section>
  )
}

function TimelineTip({ g, x, y }: { g: MissionGame; x: number; y: number }) {
  const d = stageOf(g, 'decision'); const b = stageOf(g, 'bet'); const r = stageOf(g, 'result'); const lv = stageOf(g, 'live')
  const score = r.final ? `${r.final.away}–${r.final.home}` : lv.available && lv.away_score != null ? `${lv.away_score}–${lv.home_score} · ${lv.period_label ?? ''}` : null
  return (
    <div className="mx-tip" style={{ left: x, top: y }}>
      <b>{g.matchup}</b>
      <span>{etClock(g.starts_at)} ET{score ? ` · ${score}` : ''}</span>
      <span>Decision: {clean(chipLabel('decision', d))}</span>
      {b.state === 'DONE' ? <span>Bet: {clean(b.label) || `${b.side ?? ''} ${american(b.american ?? null)}`}{r.outcome ? ` · ${r.outcome}${r.profit_usd != null ? ` ${usd(r.profit_usd, true)}` : ''}` : ''}</span> : null}
      <small>Click to open its gates</small>
    </div>
  )
}

// ---------------------------------------------------------------- board --

const STAGE_ICON: Record<StageKey, string> = { forecast: '◔', approval: '✓', prices: '$', decision: '⚖', bet: '▤', live: '◉', result: '★' }

function StageKey() {
  return (
    <div className="mx-key" aria-hidden="true">
      {STAGE_KEYS.map((k, i) => <span key={k}><i>{STAGE_ICON[k]}</i>{STAGE_NAMES[k]}{i < STAGE_KEYS.length - 1 ? <em>→</em> : null}</span>)}
      <span className="mx-key__tones"><b className="t-done" />done <b className="t-waiting" />waiting <b className="t-blocked" />blocked <b className="t-na" />not needed <b className="t-unknown" />unknown</span>
    </div>
  )
}


/** Live football score + win probability from the test-mode ESPN feed (Mission Control carries no college scores). */
type FbLive = { away: number; home: number; status: string; wp: number[] }
function useFootballLive(g: MissionGame, enabled: boolean): FbLive | null {
  const [data, setData] = useState<FbLive | null>(null)
  useEffect(() => {
    if (!enabled) return undefined
    let alive = true
    const league = g.sport === 'CFB' ? 'college-football' : 'nfl'
    const q = new URLSearchParams({ league, date: operationalToday(new Date(g.starts_at)).replace(/-/g, ''), home: g.home.abbr, away: g.away.abbr, home_name: g.home.name ?? g.home.abbr, away_name: g.away.name ?? g.away.abbr, lite: '1' })
    const load = () => fetch(`/preview-api/football/live?${q.toString()}`, { headers: { accept: 'application/json' } })
      .then((r) => (r.ok && (r.headers.get('content-type') ?? '').includes('json') ? r.json() : null))
      .then((b) => { if (alive && b?.found && b.teams) setData({ away: Number(b.teams.away?.score ?? 0), home: Number(b.teams.home?.score ?? 0), status: String(b.status?.short ?? b.status?.detail ?? ''), wp: (b.win_probability ?? []).map((w: { home: number }) => w.home).filter((v: unknown) => typeof v === 'number') }) })
      .catch(() => undefined)
    load()
    const t = window.setInterval(load, 30_000)
    return () => { alive = false; window.clearInterval(t) }
  }, [enabled, g.sport, g.starts_at, g.home.abbr, g.away.abbr, g.home.name, g.away.name])
  return enabled ? data : null
}

function WpLine({ wp, home, away }: { wp: number[]; home: string; away: string }) {
  const W = 300; const H = 34
  const pts = wp.map((v, i) => `${((i / Math.max(1, wp.length - 1)) * W).toFixed(1)},${(H - v * H).toFixed(1)}`).join(' ')
  const last = wp[wp.length - 1] ?? 0.5
  return (
    <span className="mx-wp" title={`Live win probability (ESPN): ${home} ${Math.round(last * 100)}%`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true"><line x1={0} x2={W} y1={H / 2} y2={H / 2} className="mx-wp__mid" /><polyline points={pts} style={{ stroke: home }} /><circle cx={W} cy={H - last * H} r={3} style={{ fill: last >= 0.5 ? home : away }} /></svg>
      <small>live win prob · {Math.round(last * 100)}% home</small>
    </span>
  )
}

function GameCard({ g, now, open, onToggle, onGo, onLive }: { g: MissionGame; now: number; open: boolean; onToggle: () => void; onGo: () => void; onLive: () => void }) {
  const f = stageOf(g, 'forecast'); const b = stageOf(g, 'bet'); const r = stageOf(g, 'result'); const lv = stageOf(g, 'live')
  const live = liveNow(g); const final = isFinal(g)
  const step = nextStepLine(g.next_step, now)
  const current = g.next_step?.stage && g.next_step.stage !== 'DONE' ? (g.next_step.stage.toLowerCase() as StageKey) : null
  const pHome = f.home_win_prob ?? null
  const fb = useFootballLive(g, live && (g.sport === 'CFB' || g.sport === 'NFL'))
  const score = r.final ? { a: r.final.away, h: r.final.home, t: clean(r.final.label) || 'Final' } : lv.available && lv.away_score != null && lv.home_score != null ? { a: lv.away_score, h: lv.home_score, t: clean(lv.period_label) || (live ? 'Live' : '') } : fb ? { a: fb.away, h: fb.home, t: fb.status } : null
  const outcome = r.outcome ?? null
  const team = (side: 'away' | 'home') => {
    const t = g[side]; const p = pHome == null ? null : side === 'home' ? pHome : 1 - pHome
    return (
      <div className="mx-team" style={{ ['--tc' as string]: teamColor(g.sport, t.abbr) } as CSSProperties}>
        <Crest sport={g.sport} abbr={t.abbr} size={30} />
        <b title={t.name ?? t.abbr}>{shortName(t.name || t.abbr, 20)}</b>
        {p != null ? <span className="mx-team__p"><i style={{ width: `${Math.round(p * 100)}%` }} /><em>{Math.round(p * 100)}%</em></span> : <span className="mx-team__p is-none"><em>—</em></span>}
        {score ? <strong className={score && ((side === 'home' && score.h > score.a) || (side === 'away' && score.a > score.h)) ? 'is-lead' : ''}>{side === 'home' ? score.h : score.a}</strong> : null}
      </div>
    )
  }
  return (
    <article id={`mx-g-${g.game_id}`} className={`mx-card${open ? ' is-open' : ''}${live ? ' is-live' : ''}${g.attention === 'BLOCKED' ? ' is-blocked' : g.attention === 'WATCH' ? ' is-watch' : ''}`} style={{ ['--sc' as string]: SPORT[g.sport].color } as CSSProperties}>
      <button type="button" className="mx-card__main" onClick={onToggle} aria-expanded={open}>
        <span className="mx-card__top">
          <em className="mx-sport">{SPORT[g.sport].icon} {g.sport}</em>
          <span className="mx-card__when">{live ? <b className="mx-live">● LIVE{score?.t ? ` · ${score.t}` : ''}</b> : final ? <b>{score?.t ?? 'Final'}</b> : <b>{etClock(g.starts_at)} ET</b>}</span>
          {outcome ? <span className={`mx-out is-${outcome.toLowerCase()}`}>{outcome}{r.profit_usd != null ? ` ${usd(r.profit_usd, true)}` : ''}</span> : b.state === 'DONE' ? <span className="mx-out is-open">BET</span> : null}
        </span>
        {team('away')}
        {team('home')}
        {fb && fb.wp.length > 2 ? <WpLine wp={fb.wp} home={teamColor(g.sport, g.home.abbr)} away={teamColor(g.sport, g.away.abbr)} /> : null}
        <span className="mx-track" aria-label="Pipeline">
          {STAGE_KEYS.map((k) => { const s = stageOf(g, k); const tone = stageTone(s.state); return <span key={k} className={`mx-stop t-${tone}${current === k ? ' is-current' : ''}`} title={`${STAGE_NAMES[k]}: ${clean(chipLabel(k, s))}`}><i>{STAGE_ICON[k]}</i><small>{STAGE_NAMES[k]}</small></span> })}
        </span>
        <span className={`mx-next${step.overdue ? ' is-late' : ''}${g.next_step?.kind === 'BLOCKED' ? ' is-blocked' : ''}`}><b>{clean(step.headline)}</b>{step.sub ? <small>{clean(step.sub)}</small> : null}</span>
        {b.state === 'DONE' ? <span className="mx-bet">▤ {clean(b.label) || `${b.side ?? ''} ${american(b.american ?? null)}`}{b.to_win_usd != null ? ` · to win ${usd(b.to_win_usd)}` : ''}</span> : null}
      </button>
      {open ? (
        <div className="mx-gates">
          <ol>
            {STAGE_KEYS.map((k) => { const s = stageOf(g, k); const tone = stageTone(s.state); return (
              <li key={k} className={`t-${tone}`}>
                <i>{STAGE_ICON[k]}</i>
                <span><b>{STAGE_NAMES[k]} · {clean(chipLabel(k, s))}</b>{s.detail ? <small>{clean(s.detail)}</small> : null}{s.at ? <em>{etClock(s.at)} ET</em> : s.due_at ? <em>due {etClock(s.due_at)} ET</em> : null}</span>
              </li>) })}
          </ol>
          <span className="mx-gates__go">
            <button type="button" onClick={onGo}>Open the game page ›</button>
            {(g.sport === 'CFB' || g.sport === 'NFL') ? <button type="button" onClick={onLive}>Watch in Live View ›</button> : null}
          </span>
        </div>
      ) : null}
    </article>
  )
}

// ----------------------------------------------------------------- rail --

function Departures({ games, now, onOpen }: { games: MissionGame[]; now: number; onOpen: (g: MissionGame) => void }) {
  const events = useMemo(() => {
    const out: Array<{ at: number; what: string; tone: string; g: MissionGame }> = []
    games.forEach((g) => {
      if (isFinal(g)) return
      const push = (iso: string | null | undefined, what: string, tone: string) => { const t = ms(iso); if (t != null && t > now - 60_000) out.push({ at: t, what, tone, g }) }
      push(g.clocks?.buy_closes_at, 'PRICES LOCK', 'teal')
      push(g.clocks?.decision_opens_at, 'DECIDES', 'gold')
      push(g.starts_at, g.sport === 'NHL' ? 'PUCK DROP' : g.sport === 'NBA' ? 'TIP-OFF' : g.sport === 'MLB' ? 'FIRST PITCH' : 'KICKOFF', 'green')
    })
    return out.sort((a, b) => a.at - b.at).slice(0, 9)
  }, [games, Math.floor(now / 15_000)]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section className="sl-card mx-dep" aria-label="Departures">
      <header className="sl-card__head"><h2>🛫 Departures</h2><small className="mx-dep__sub">what happens next</small></header>
      {events.length ? (
        <ol className="mx-dep__list">
          {events.map((e, i) => (
            <li key={`${e.g.game_id}-${e.what}`} style={{ animationDelay: `${i * 70}ms` }}>
              <button type="button" onClick={() => onOpen(e.g)}>
                <span className="mx-flap">{etClock(new Date(e.at).toISOString()).replace(/\s?[AP]M/, '')}</span>
                <span className={`mx-dep__what tone-${e.tone}`}>{e.what}</span>
                <span className="mx-dep__game"><Crest sport={e.g.sport} abbr={e.g.away.abbr} size={18} /><Crest sport={e.g.sport} abbr={e.g.home.abbr} size={18} /><small>{shortName(e.g.away.abbr, 9)} @ {shortName(e.g.home.abbr, 9)}</small></span>
                <span className="mx-dep__in">{countdownText(new Date(e.at).toISOString(), now) ? `in ${countdownText(new Date(e.at).toISOString(), now)}` : 'now'}</span>
              </button>
            </li>
          ))}
        </ol>
      ) : <p className="sl-empty">Nothing else scheduled today.</p>}
    </section>
  )
}

function HealthCard({ data, now }: { data: MissionControl | null; now: number }) {
  const h = data?.health
  const j = jobSummary(h?.jobs)
  const bad = (h?.jobs ?? []).filter((x) => x.state === 'FAILED' || x.state === 'STALE')
  return (
    <section className="sl-card mx-health" id="mx-health" aria-label="System health">
      <header className="sl-card__head"><h2>System Health</h2><span className={`mx-beacon is-${(h?.overall ?? 'unknown').toLowerCase()}`}><i />{h?.overall ?? '—'}</span></header>
      <div className="mx-health__grid">
        <span className={h?.scheduler.state === 'RUNNING' ? 'is-ok' : 'is-bad'}><b>Scheduler</b><small>{h?.scheduler.state === 'RUNNING' ? `running ${sinceText(h.scheduler.started_at, now) ?? ''}` : h?.scheduler.state ?? '—'}</small></span>
        <span className={h?.database.state === 'OK' ? 'is-ok' : 'is-bad'}><b>Database</b><small>{h?.database.state ?? '—'}</small></span>
        <span className={j.failed || j.stale ? 'is-warn' : 'is-ok'}><b>Jobs</b><small>{j.ok}/{j.total} healthy{j.off ? ` · ${j.off} off` : ''}</small></span>
      </div>
      {(h?.producers ?? []).length ? <div className="mx-prod">{h!.producers.map((p) => <span key={`${p.sport}-${p.name}`} className={`is-${p.state === 'OK' ? 'ok' : p.state === 'NOT_APPLICABLE' ? 'na' : 'bad'}`} title={clean(p.detail) || p.state}>{p.sport} producer · {p.state === 'OK' ? 'fresh' : p.state.toLowerCase().replace('_', ' ')}</span>)}</div> : null}
      {h?.issues.length ? <ul className="mx-issues">{h.issues.map((t, i) => <li key={i}>{clean(t)}</li>)}</ul> : h ? <p className="mx-allgood">Everything that should be running is running.</p> : null}
      {bad.length ? <ul className="mx-jobs">{bad.slice(0, 5).map((x) => <li key={x.key}><b>{x.label ?? x.job_name}</b><small>{x.state.toLowerCase()} · last ok {x.last_success_at ? `${sinceText(x.last_success_at, now)} ago` : 'never'}</small></li>)}</ul> : null}
    </section>
  )
}

function AllowanceCard({ data }: { data: MissionControl | null }) {
  const a = data?.allowance
  const limit = a?.limit_usd ?? 100; const used = a?.entered_usd ?? 0
  const frac = Math.max(0, Math.min(1, used / Math.max(1, limit)))
  const R = 54; const C = 2 * Math.PI * R
  const bySport = useMemo(() => {
    const m = new Map<MissionSport, number>()
    ;(data?.games ?? []).forEach((g) => { const b = stageOf(g, 'bet'); if (b.state === 'DONE' && b.stake_usd) m.set(g.sport, (m.get(g.sport) ?? 0) + b.stake_usd) })
    return MISSION_SPORTS.filter((s) => m.has(s)).map((s) => ({ s, v: m.get(s)! }))
  }, [data])
  let acc = 0
  return (
    <section className="sl-card mx-allow" aria-label="Today's shared allowance">
      <header className="sl-card__head"><h2>Today's ${limit}</h2><small>{a?.in_force === false ? 'not in force' : a?.state === 'FULL' ? 'full' : 'shared by every sport'}</small></header>
      <div className="mx-allow__body">
        <svg viewBox="0 0 140 140" width={140} height={140} aria-hidden="true">
          <circle cx={70} cy={70} r={R} className="mx-allow__track" />
          {bySport.map(({ s, v }) => { const f = v / Math.max(1, limit); const el = <circle key={s} cx={70} cy={70} r={R} fill="none" stroke={SPORT[s].color} strokeWidth={12} strokeDasharray={`${f * C} ${C}`} strokeDashoffset={-acc * C} transform="rotate(-90 70 70)" />; acc += f; return el })}
          <text x={70} y={66} textAnchor="middle" className="mx-allow__big">{usd(used)}</text>
          <text x={70} y={86} textAnchor="middle" className="mx-allow__small">{Math.round(frac * 100)}% entered</text>
        </svg>
        <ul>
          {bySport.map(({ s, v }) => <li key={s}><i style={{ background: SPORT[s].color }} />{SPORT[s].long}<b>{usd(v)}</b></li>)}
          <li className="mx-allow__room"><i />Room left<b>{a?.room_usd != null ? usd(a.room_usd) : '—'}</b></li>
        </ul>
      </div>
    </section>
  )
}

function BankrollCard({ data }: { data: MissionControl | null }) {
  const list = (data?.bankrolls ?? []).filter((b) => b.read_state !== 'READ_FAILED')
  const max = Math.max(60, ...list.map((b) => Math.abs(b.profit_usd ?? 0)))
  return (
    <section className="sl-card mx-bank" aria-label="Bankrolls by sport">
      <header className="sl-card__head"><h2>Bankrolls</h2><Link to="/experiment">The Record ›</Link></header>
      <ul>
        {list.map((b) => { const p = b.profit_usd ?? 0; return (
          <li key={b.sport} style={{ ['--sc' as string]: SPORT[b.sport].color } as CSSProperties}>
            <span className="mx-bank__name">{SPORT[b.sport].icon} {b.sport}</span>
            <span className="mx-bank__bar"><i className={p >= 0 ? 'is-up' : 'is-down'} style={{ width: `${(Math.abs(p) / max) * 50}%`, [p >= 0 ? 'left' : 'right']: '50%' } as CSSProperties} /></span>
            <span className="mx-bank__v"><b>{usd(b.bankroll_usd)}</b><small className={p >= 0 ? 'is-up' : 'is-down'}>{usd(p, true)}</small></span>
          </li>) })}
      </ul>
      <small className="mx-bank__foot">Each sport started at $1,000 of paper money. Bars show profit or loss.</small>
    </section>
  )
}

function SweatCard({ data, onGo }: { data: MissionControl | null; onGo: (gid: string, sport: MissionSport) => void }) {
  const items = data?.sweating ?? []
  return (
    <section className="sl-card mx-sweat" aria-label="Bets being sweated">
      <header className="sl-card__head"><h2>😅 Sweating</h2><small>{items.length} open bet{items.length === 1 ? '' : 's'}</small></header>
      {data && data.sweating == null ? <p className="sl-empty">Open bets could not be read.</p> : null}
      <ul>
        {items.slice(0, 8).map((s) => (
          <li key={s.position_id}>
            <button type="button" onClick={() => onGo(s.source_game_id ?? s.game_id, s.sport)}>
              <span className="mx-sweat__top"><em>{s.sport}</em><b>{clean(s.selection)} {american(s.american)}</b><small className={`is-${s.state.toLowerCase()}`}>{SWEAT_WORDS[s.state]}</small></span>
              <span className="mx-sweat__sub"><small>{clean(s.matchup)}</small><small>{usd(s.stake_usd)} to win {usd(s.to_win_usd)}</small></span>
              {s.live?.available && s.live.away_score != null ? <span className="mx-sweat__score">{s.live.away_score}–{s.live.home_score} · {clean(s.live.period_label)}</span> : null}
            </button>
          </li>
        ))}
        {!items.length && data?.sweating ? <li className="sl-empty">No open bets right now.</li> : null}
      </ul>
    </section>
  )
}
