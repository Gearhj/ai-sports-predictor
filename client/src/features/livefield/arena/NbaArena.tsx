/**
 * NBA LIVE VIEW (owner 10-25: "the court view is cartoonish ... no player profiles, hot zones, fire plays, 3-point
 * animation, free throw graphics, changing stats ... where is the HOLY SHIT moment?").
 *
 * THE ARENA: a full hardwood court, each team attacking its own basket, every shot at its real spot (made = filled,
 * missed = x). The ball flies to the rim in a D3 arc for every new shot and replays the last run of shots on a loop:
 * SPLASH on a three, SLAM on a dunk, the net ripples on a make, CLANK on a miss, and free throws leave the line.
 * HE'S ON FIRE: three straight makes and the player's face goes up in flames (NBA Jam), their shots burn on the court.
 * HEX HEAT: hexagons sized by attempts and coloured by how far the shooting runs above or below the league norm.
 * BIG MOMENTS: dunks, threes, and-ones, lead changes and clutch buckets, each replayable on the court.
 * WHO WINS?: ESPN's live win probability as a gauge and a river with the faces of the plays that swung it most.
 * FREE THROW LINE: every attempt as a ball, made or missed. Read-only: ESPN's public feed via /preview-api.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { elapsedAt, hex, periodName, teamPair, type ArenaAthlete, type ArenaFeed, type ArenaPlay, type ArenaTeam } from './arenaData'
import { NbaPlayers, NbaStory } from './NbaExtras'
import './nba.css'

const W = 940; const H = 500
const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const isFT = (p: ArenaPlay) => /free throw/i.test(p.type ?? '') || p.attempted === 1
const isDunk = (p: ArenaPlay) => /dunk|alley oop/i.test(p.type ?? '')
const pct = (v: number) => `${Math.round(v * 100)}%`

/** The basket a team attacks (home right, away left), in court pixels (10 px per foot). */
const basket = (home: boolean) => ({ x: home ? W - 52.5 : 52.5, y: H / 2 })
/** ESPN half-court feet (x 0-50 across, y = feet out from the rim) to full-court pixels. */
function spot(p: ArenaPlay, home: boolean): { x: number; y: number } {
  const b = basket(home)
  if (!p.coord) { const ft = 13.75 * 10; return { x: home ? b.x - ft : b.x + ft, y: b.y } }      // free throw line
  const along = (p.coord.y) * 10; const across = (p.coord.x - 25) * 10
  return home ? { x: b.x - along, y: b.y + across } : { x: b.x + along, y: b.y - across }
}
const expected = (p: ArenaPlay) => { if (p.attempted === 3) return 0.36; const d = p.coord ? Math.hypot(p.coord.x - 25, p.coord.y) : 0; return d < 4 ? 0.64 : d < 14 ? 0.43 : 0.41 }

interface Shot { p: ArenaPlay; home: boolean; x: number; y: number; ft: boolean; three: boolean; made: boolean; shooter: string | null }
interface Ctx { feed: ArenaFeed; col: Record<string, string>; shots: Shot[]; fire: Map<string, number>; teamOf: (id: string | null | undefined) => ArenaTeam }

export function NbaArena({ feed, bet, side, children }: { feed: ArenaFeed; bet: string | null; side?: ReactNode; children?: ReactNode }) {
  const { home, away } = feed.teams
  const pair = teamPair(home, away)
  const col = useMemo<Record<string, string>>(() => ({ [home.id]: pair.home, [away.id]: pair.away }), [home.id, away.id, pair.home, pair.away])
  const shots = useMemo<Shot[]>(() => feed.plays.filter((p) => p.shooting).map((p) => {
    const h = p.team === home.id
    const s = spot(p, h)
    return { p, home: h, x: s.x, y: s.y, ft: isFT(p), three: p.attempted === 3, made: !!p.scoring, shooter: p.players[0]?.id ?? null }
  }), [feed.plays, home.id])
  // current made-in-a-row streak per player (field goals only; a miss resets it)
  const fire = useMemo(() => {
    const m = new Map<string, number>()
    for (const s of shots) { if (s.ft || !s.shooter) continue; m.set(s.shooter, s.made ? (m.get(s.shooter) ?? 0) + 1 : 0) }
    return m
  }, [shots])
  const [replay, setReplay] = useState<{ shot: Shot; n: number } | null>(null)
  const ctx: Ctx = { feed, col, shots, fire, teamOf: (id) => (id === home.id ? home : away) }
  return (
    <div className="nb">
      <NbaStory feed={feed} bet={bet} />
      <OnFire ctx={ctx} />
      <div className="nb-top">
        <TheArena ctx={ctx} replay={replay} />
        <div className="nb-side">
          <WhoWins ctx={ctx} />
          <FreeThrowLine ctx={ctx} />
          {side}
        </div>
      </div>
      <BigMoments ctx={ctx} onReplay={(shot) => { setReplay((r) => ({ shot, n: (r?.n ?? 0) + 1 })); document.querySelector('.nb-arena')?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }} />
      <NbaPlayers feed={feed} />
      {children}
    </div>
  )
}

// ------------------------------------------------------------ the court --

function CourtEnd() {
  // the right-hand end, drawn in court pixels; the left end is its mirror
  return (
    <g className="nb-lines">
      <rect x={W - 190} y={170} width={190} height={160} className="nb-key" />
      <circle cx={W - 190} cy={250} r={60} />
      <path d={`M${W - 190} 190 A60 60 0 0 0 ${W - 190} 310`} strokeDasharray="10 8" transform={`translate(0 0)`} />
      <path d={`M${W} 30 L${W - 140} 30 A237.5 237.5 0 0 0 ${W - 140} 470 L${W} 470`} />
      <path d={`M${W - 52.5} 210 A40 40 0 0 0 ${W - 52.5} 290`} />
      <line x1={W - 40} x2={W - 40} y1={220} y2={280} className="nb-board" />
      <circle cx={W - 52.5} cy={250} r={7.5} className="nb-rim" />
    </g>
  )
}

function Court({ home, away }: { home: ArenaTeam; away: ArenaTeam }) {
  return (
    <>
      <defs>
        <pattern id="nb-planks" width="940" height="28" patternUnits="userSpaceOnUse">
          {Array.from({ length: 4 }, (_, k) => <rect key={k} x={0} y={k * 7} width={940} height={7} fill={['#c98d4e', '#c2864a', '#cd9455', '#bf8346'][k]} />)}
          {Array.from({ length: 12 }, (_, k) => <line key={`j${k}`} x1={(k * 173) % 940} x2={(k * 173) % 940} y1={(k % 4) * 7} y2={(k % 4) * 7 + 7} stroke="#a86d36" strokeWidth="0.8" />)}
        </pattern>
        <radialGradient id="nb-spot" cx="50%" cy="45%" r="70%"><stop offset="0" stopColor="#fff" stopOpacity="0.16" /><stop offset="0.7" stopColor="#000" stopOpacity="0.08" /><stop offset="1" stopColor="#000" stopOpacity="0.45" /></radialGradient>
        <filter id="nb-fire" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>
      <rect x="0" y="0" width={W} height={H} rx="10" fill="url(#nb-planks)" />
      <rect x="0" y="0" width={W} height={H} rx="10" fill="url(#nb-spot)" />
      <g className="nb-lines">
        <rect x="2" y="2" width={W - 4} height={H - 4} />
        <line x1={W / 2} x2={W / 2} y1="2" y2={H - 2} />
        <circle cx={W / 2} cy={H / 2} r="60" /><circle cx={W / 2} cy={H / 2} r="20" />
      </g>
      {home.logo ? <image href={home.logo} x={W / 2 - 52} y={H / 2 - 52} width="104" height="104" opacity="0.55" /> : null}
      <CourtEnd />
      <g transform={`translate(${W} 0) scale(-1 1)`}><CourtEnd /></g>
      <text x={W - 16} y={H / 2} className="nb-baseline" transform={`rotate(90 ${W - 16} ${H / 2})`} textAnchor="middle">{home.short.toUpperCase()}</text>
      <text x={16} y={H / 2} className="nb-baseline" transform={`rotate(-90 16 ${H / 2})`} textAnchor="middle">{away.short.toUpperCase()}</text>
    </>
  )
}

// ------------------------------------------------------------ who is on the floor --

type Lineups = Record<string, string[]>
/** The ten on the floor after each play: the starters, then every "A enters the game for B"; anyone who shows up in a play is put on. */
function lineupsByPlay(feed: ArenaFeed): Lineups[] {
  const { home, away } = feed.teams
  const roster = new Map(feed.athletes.map((a) => [a.id, a.team]))
  const starters = (id: string) => feed.athletes.filter((a) => a.team === id && a.starter).map((a) => a.id).slice(0, 5)
  let cur: Lineups = { [home.id]: starters(home.id), [away.id]: starters(away.id) }
  const last = new Map<string, number>()
  return feed.plays.map((p, i) => {
    const t = p.team
    if (t && cur[t]) {
      if (/substitution/i.test(p.type ?? '') && p.players.length >= 2) {
        const L = cur[t].filter((x) => x !== p.players[1].id)
        if (!L.includes(p.players[0].id)) L.push(p.players[0].id)
        cur = { ...cur, [t]: L.slice(-5) }; last.set(p.players[0].id, i)
      } else {
        for (const pl of p.players) {
          const team = roster.get(pl.id); if (!team || !cur[team]) continue
          last.set(pl.id, i)
          if (!cur[team].includes(pl.id)) {
            const L = [...cur[team]]
            if (L.length >= 5) { L.sort((a, b) => (last.get(a) ?? -1) - (last.get(b) ?? -1)); L.shift() }
            L.push(pl.id); cur = { ...cur, [team]: L }
          }
        }
      }
    }
    return cur
  })
}

/** Feet from the attacking basket (u out, v across) to court pixels. */
const mapUV = (homeEnd: boolean, u: number, v: number) => { const b = basket(homeEnd); return homeEnd ? { x: b.x - u * 10, y: b.y + v * 10 } : { x: b.x + u * 10, y: b.y - v * 10 } }
const OFFENSE: Array<[number, number]> = [[24, 0], [19, -16], [19, 16], [3, -21], [6, 7]]
const posRank = (pos: string | null) => (pos?.startsWith('G') ? 0 : pos?.startsWith('F') ? 1 : 2)

// ------------------------------------------------------------ hot zones --

type ZoneKey = 'ra' | 'paint' | 'midL' | 'midC' | 'midR' | 'c3L' | 'c3R' | 'abL' | 'abC' | 'abR'
const ZONE_FG: Record<ZoneKey, number> = { ra: 0.64, paint: 0.43, midL: 0.41, midC: 0.41, midR: 0.41, c3L: 0.385, c3R: 0.385, abL: 0.355, abC: 0.355, abR: 0.355 }
const ZONE_AT: Record<ZoneKey, [number, number]> = { ra: [1.2, 0], paint: [9, 0], midL: [7, -15], midC: [18, 0], midR: [7, 15], c3L: [2, -23.5], c3R: [2, 23.5], abL: [20, -20], abC: [26, 0], abR: [20, 20] }
function zoneOf(p: ArenaPlay): ZoneKey | null {
  if (!p.coord) return null
  const u = p.coord.y; const v = p.coord.x - 25
  if (p.attempted === 3) return u < 8.75 ? (v < 0 ? 'c3L' : 'c3R') : v < -8 ? 'abL' : v > 8 ? 'abR' : 'abC'
  if (Math.hypot(u, v) < 4) return 'ra'
  if (Math.abs(v) <= 8 && u < 13.75) return 'paint'
  return v < -8 ? 'midL' : v > 8 ? 'midR' : 'midC'
}
const IN3 = 'M-5.25 -22 L8.75 -22 A23.75 23.75 0 0 1 8.75 22 L-5.25 22 Z'

function HotZones({ shots, homeEnd, id }: { shots: Shot[]; homeEnd: boolean; id: string }) {
  const agg = new Map<ZoneKey, { m: number; a: number }>()
  for (const s of shots) { const z = zoneOf(s.p); if (!z) continue; const r = agg.get(z) ?? { m: 0, a: 0 }; r.a += 1; r.m += s.made ? 1 : 0; agg.set(z, r) }
  const color = d3.scaleDiverging<string>().domain([-0.2, 0, 0.2]).interpolator((t) => d3.interpolateRdBu(1 - t)).clamp(true)
  const fill = (z: ZoneKey) => { const r = agg.get(z); return r?.a ? color(r.m / r.a - ZONE_FG[z]) : 'rgba(148,163,184,.08)' }
  const op = (z: ZoneKey) => (agg.get(z)?.a ? 0.62 : 0.15)
  const b = basket(homeEnd)
  const m = homeEnd ? `matrix(-10 0 0 10 ${b.x} ${b.y})` : `matrix(10 0 0 -10 ${b.x} ${b.y})`
  const z = (k: ZoneKey, el: ReactNode) => <g key={k} className="nb-zone" style={{ animationDelay: `${Object.keys(ZONE_AT).indexOf(k) * 50}ms` }} fill={fill(k)} fillOpacity={op(k)}>{el}</g>
  return (
    <g>
      <g transform={m}>
        <defs>
          <clipPath id={`${id}-in`} clipPathUnits="userSpaceOnUse"><path d={IN3} /></clipPath>
          <mask id={`${id}-out`} maskUnits="userSpaceOnUse" x="-6" y="-26" width="50" height="52"><rect x="-6" y="-26" width="50" height="52" fill="#fff" /><path d={IN3} fill="#000" /></mask>
        </defs>
        {z('midL', <rect x={-5.25} y={-25} width={47} height={17} clipPath={`url(#${id}-in)`} />)}
        {z('midC', <rect x={13.75} y={-8} width={28} height={16} clipPath={`url(#${id}-in)`} />)}
        {z('midR', <rect x={-5.25} y={8} width={47} height={17} clipPath={`url(#${id}-in)`} />)}
        {z('paint', <rect x={-5.25} y={-8} width={19} height={16} />)}
        {z('ra', <circle r={4} />)}
        {z('c3L', <rect x={-5.25} y={-25} width={14} height={3} />)}
        {z('c3R', <rect x={-5.25} y={22} width={14} height={3} />)}
        {z('abL', <rect x={8.75} y={-25} width={21.25} height={17} mask={`url(#${id}-out)`} />)}
        {z('abC', <rect x={8.75} y={-8} width={21.25} height={16} mask={`url(#${id}-out)`} />)}
        {z('abR', <rect x={8.75} y={8} width={21.25} height={17} mask={`url(#${id}-out)`} />)}
        <path d={IN3} fill="none" stroke="rgba(255,250,240,.9)" strokeWidth="2.4" vectorEffect="non-scaling-stroke" />
      </g>
      {(Object.keys(ZONE_AT) as ZoneKey[]).map((k) => { const r = agg.get(k); if (!r?.a) return null; const p = mapUV(homeEnd, ...ZONE_AT[k]); return (
        <g key={`l${k}`} className="nb-zone__lab" transform={`translate(${p.x} ${p.y})`}>
          <text y={k.startsWith('c3') ? 4 : 0} textAnchor="middle" className="nb-zone__pct">{Math.round((r.m / r.a) * 100)}%</text>
          {k.startsWith('c3') ? null : <text y={15} textAnchor="middle" className="nb-zone__n">{r.m}/{r.a}</text>}
        </g>) })}
    </g>
  )
}

// ------------------------------------------------------------ the arena --

type Mode = 'recent' | 'new' | 'replay'
const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms))
const MEANING = /substitution|rebound|turnover|steal/i

function TheArena({ ctx, replay }: { ctx: Ctx; replay: { shot: Shot; n: number } | null }) {
  const { feed, col, shots, fire, teamOf } = ctx
  const { home, away } = feed.teams
  const [lens, setLens] = useState<'shots' | 'zones'>('shots')
  const [team, setTeam] = useState<'all' | string>('all')
  const [period, setPeriod] = useState(0)
  const [caption, setCaption] = useState<ArenaPlay | null>(null)
  const [take, setTake] = useState<{ p: ArenaPlay; word: string } | null>(null)
  const [card, setCard] = useState<string | null>(null)
  const [shake, setShake] = useState(false)
  const [run, setRun] = useState<{ mode: Mode; n: number }>({ mode: 'recent', n: 0 })
  const svgRef = useRef<SVGSVGElement | null>(null); const tokRef = useRef<SVGGElement | null>(null); const fxRef = useRef<SVGGElement | null>(null)
  const lineups = useMemo(() => lineupsByPlay(feed), [feed])
  const live = useRef({ feed, col, fire, lineups, shots, replay }); live.current = { feed, col, fire, lineups, shots, replay }
  const seen = useRef<string | null | undefined>(undefined)
  const lastPlay = feed.plays[feed.plays.length - 1] ?? null
  useEffect(() => { if (replay) setRun((r) => ({ mode: 'replay', n: r.n + 1 })) }, [replay])
  useEffect(() => {
    if (!lastPlay) return
    if (seen.current === undefined) { seen.current = lastPlay.id; return }
    if (lastPlay.id !== seen.current) setRun((r) => (r.mode === 'replay' ? r : { mode: 'new', n: r.n + 1 }))
  }, [lastPlay])
  useEffect(() => { const t = window.setInterval(() => setRun((r) => (r.mode === 'recent' ? { mode: 'recent', n: r.n + 1 } : r)), 26_000); return () => window.clearInterval(t) }, [])

  useEffect(() => {
    const svgEl = svgRef.current; const tokEl = tokRef.current; const fxEl = fxRef.current
    if (!svgEl || !tokEl || !fxEl) return undefined
    const L = live.current; const plays = L.feed.plays
    const idx = (id: string) => plays.findIndex((p) => p.id === id)
    const isEvent = (p: ArenaPlay) => p.shooting || MEANING.test(p.type ?? '')
    let from: number; let to = plays.length - 1
    if (run.mode === 'replay' && L.replay) { to = idx(L.replay.shot.p.id); from = to; let k = 0; while (from > 0 && k < 2) { from--; if (isEvent(plays[from])) k++ } }
    else if (run.mode === 'new') { const s = seen.current ? idx(seen.current) : to - 1; from = Math.max(s + 1, to - 12); seen.current = plays[to]?.id ?? null }
    else { from = to; let k = 0; while (from > 0 && k < 11) { from--; if (isEvent(plays[from])) k++ } }
    const events = plays.slice(Math.max(0, from), to + 1).map((p, i) => ({ p, i: Math.max(0, from) + i })).filter((e) => isEvent(e.p))
    if (!events.length) return undefined
    let dead = false
    const svg = d3.select(svgEl); const tok = d3.select(tokEl); const fx = d3.select(fxEl)
    fx.selectAll('*').interrupt().remove()
    const big = run.mode !== 'recent'
    const athlete = (id: string) => L.feed.athletes.find((a) => a.id === id)
    const teamOfPlayer = (id: string) => athlete(id)?.team ?? null
    const posOf = new Map<string, { x: number; y: number }>()
    tok.selectAll<SVGGElement, string>('g.nb-tok').each(function (id) { const t = d3.select(this).attr('transform'); const m = /translate\(([-\d.]+)[ ,]([-\d.]+)\)/.exec(t ?? ''); if (m) posOf.set(id, { x: +m[1], y: +m[2] }) })
    const draw = (lu: Lineups) => {
      const ids = [...(lu[home.id] ?? []), ...(lu[away.id] ?? [])]
      const sel = tok.selectAll<SVGGElement, string>('g.nb-tok').data(ids, (d) => d)
      sel.exit().transition().duration(400).style('opacity', 0).remove()
      const en = sel.enter().append('g').attr('class', 'nb-tok').style('cursor', 'pointer')
        .attr('transform', (d) => { const p = posOf.get(d) ?? { x: W / 2, y: H + 30 }; posOf.set(d, p); return `translate(${p.x},${p.y})` })
        .on('click', (_e, d) => setCard(d))
      en.append('circle').attr('r', 24).attr('class', 'nb-tok__ring')
      en.append('clipPath').attr('id', (d) => `nb-tc-${d}`).append('circle').attr('r', 20)
      en.append('circle').attr('r', 20).attr('class', 'nb-tok__bg')
      en.append('image').attr('x', -20).attr('y', -20).attr('width', 40).attr('height', 40).attr('clip-path', (d) => `url(#nb-tc-${d})`).attr('preserveAspectRatio', 'xMidYMin slice')
      en.append('rect').attr('class', 'nb-tok__tag').attr('x', -30).attr('y', 24).attr('width', 60).attr('height', 15).attr('rx', 7.5)
      en.append('text').attr('class', 'nb-tok__name').attr('y', 35).attr('text-anchor', 'middle')
      en.append('circle').attr('class', 'nb-tok__chip').attr('cx', 19).attr('cy', -19).attr('r', 10)
      en.append('text').attr('class', 'nb-tok__pts').attr('x', 19).attr('y', -15.5).attr('text-anchor', 'middle')
      const all = en.merge(sel)
      all.each(function (d) {
        const a = athlete(d); const c = L.col[a?.team ?? ''] ?? '#94a3b8'; const g = d3.select(this)
        g.classed('is-fire', (L.fire.get(d) ?? 0) >= 3)
        g.select('.nb-tok__ring').attr('fill', c)
        g.select('.nb-tok__chip').attr('fill', c)
        g.select('image').attr('href', a?.headshot ?? '')
        g.select('.nb-tok__name').text((a?.short ?? a?.name ?? '').replace(/^\w\. /, '').slice(0, 11))
        const light = (() => { const v = c.replace('#', ''); if (v.length !== 6) return false; const [r, gg, bb] = [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)); return 0.299 * r + 0.587 * gg + 0.114 * bb > 170 })()
        g.select('.nb-tok__pts').text(a?.stats.points ?? '0').style('fill', light ? '#0b1526' : '#fff').style('stroke', light ? 'none' : 'rgba(0,0,0,.5)')
        g.select('title').remove(); g.append('title').text(`${a?.name ?? ''} · ${a?.stats.points ?? 0} PTS · ${a?.stats.rebounds ?? 0} REB · ${a?.stats.assists ?? 0} AST`)
      })
    }
    const moveTo = (id: string, p: { x: number; y: number }, ms: number) => {
      posOf.set(id, p)
      return tok.selectAll<SVGGElement, string>('g.nb-tok').filter((d) => d === id).transition().duration(ms).ease(d3.easeCubicInOut).attr('transform', `translate(${p.x},${p.y})`).end().catch(() => undefined)
    }
    let possession: string | null = null
    const formation = async (lu: Lineups, offense: string, ms: number) => {
      possession = offense
      const homeEnd = offense === home.id
      const defense = offense === home.id ? away.id : home.id
      const order = (ids: string[]) => [...ids].sort((a, b) => posRank(athlete(a)?.pos ?? null) - posRank(athlete(b)?.pos ?? null))
      const off = order(lu[offense] ?? []); const def = order(lu[defense] ?? [])
      const jobs: Array<Promise<unknown>> = []
      off.forEach((id, k) => { const [u, v] = OFFENSE[k % 5]; jobs.push(moveTo(id, mapUV(homeEnd, u, v), ms)) })
      def.forEach((id, k) => { const [u, v] = OFFENSE[k % 5]; jobs.push(moveTo(id, mapUV(homeEnd, u * 0.68 + 1, v * 0.78 + (v >= 0 ? -1.5 : 1.5)), ms)) })
      await Promise.all(jobs)
    }
    const ball = fx.append('g').attr('class', 'nb-ball').style('opacity', 0)
    ball.append('circle').attr('r', 8).attr('class', 'nb-ball__body'); ball.append('path').attr('d', 'M-8 0 H8 M0 -8 V8').attr('class', 'nb-ball__seam')
    let bxy = { x: W / 2, y: H / 2 }
    const ballAt = (p: { x: number; y: number }) => { bxy = p; ball.style('opacity', 1).attr('transform', `translate(${p.x},${p.y - 18})`) }
    const pass = async (p: { x: number; y: number }, ms: number) => {
      const a = bxy; fx.append('line').attr('class', 'nb-pass').attr('x1', a.x).attr('y1', a.y - 18).attr('x2', p.x).attr('y2', p.y - 18).transition().duration(900).style('opacity', 0).remove()
      await ball.transition().duration(ms).ease(d3.easeQuadOut).attr('transform', `translate(${p.x},${p.y - 18})`).end().catch(() => undefined); bxy = p
    }
    const label = (p: { x: number; y: number }, text: string, cls: string, c = '#fff') => fx.append('text').attr('x', p.x).attr('y', p.y - 40).attr('text-anchor', 'middle').attr('class', cls).style('fill', c).text(text)
      .transition().duration(1300).ease(d3.easeCubicOut).attr('y', p.y - 80).style('opacity', 0).remove()
    const vb = (x: number, y: number, w: number, h: number) => `${x.toFixed(1)} ${y.toFixed(1)} ${w.toFixed(1)} ${h.toFixed(1)}`
    let view = [0, 0, W, H]
    const camera = (cx: number, cy: number, w: number, ms: number) => {
      const h = (w * H) / W; const target = [Math.max(0, Math.min(W - w, cx - w / 2)), Math.max(0, Math.min(H - h, cy - h / 2)), w, h]
      const start = view; view = target
      return svg.transition('cam').duration(ms).ease(d3.easeCubicInOut).attrTween('viewBox', () => (t: number) => vb(...(start.map((s, i) => s + (target[i] - s) * t) as [number, number, number, number]))).end().catch(() => undefined)
    }
    const confetti = (p: { x: number; y: number }, c: string) => {
      const parts = d3.range(46).map(() => ({ x: p.x, y: p.y, vx: (Math.random() - 0.5) * 9, vy: -Math.random() * 9 - 3, r: Math.random() * 360, c: Math.random() < 0.5 ? c : Math.random() < 0.5 ? '#fde047' : '#fff' }))
      const g = fx.append('g'); const els = g.selectAll('rect').data(parts).enter().append('rect').attr('width', 6).attr('height', 3).attr('fill', (d) => d.c)
      const timer = d3.timer((el) => {
        parts.forEach((d) => { d.x += d.vx; d.y += d.vy; d.vy += 0.32; d.r += 12 })
        els.attr('transform', (d) => `translate(${d.x},${d.y}) rotate(${d.r})`).style('opacity', Math.max(0, 1 - el / 1700))
        if (el > 1700 || dead) { timer.stop(); g.remove() }
      })
    }
    const shoot = async (e: { p: ArenaPlay; i: number }) => {
      const p = e.p; const shooter = p.players[0]?.id; const homeEnd = p.team === home.id; const c = L.col[p.team ?? ''] ?? '#f97316'
      const ft = isFT(p); const three = p.attempted === 3; const made = !!p.scoring
      const target = ft ? mapUV(homeEnd, 13.75, 0) : spot(p, homeEnd)
      if (possession !== p.team) await formation(L.lineups[e.i], p.team!, big ? 800 : 600)
      if (dead) return
      const assister = made && /assist/i.test(p.text ?? '') ? p.players[1]?.id : null
      ballAt(posOf.get(assister ?? shooter ?? '') ?? target)
      if (shooter) await moveTo(shooter, target, 420)
      if (assister) await pass(target, 320)
      if (dead) return
      ballAt(target)
      const b = basket(homeEnd)
      if (big && !ft) await camera((target.x + b.x) / 2, (target.y + b.y) / 2, Math.max(420, Math.min(760, Math.hypot(b.x - target.x, b.y - target.y) * 2.2)), 500)
      const dist = Math.hypot(b.x - target.x, b.y - target.y); const apex = Math.min(150, 40 + dist * 0.36)
      const shadow = fx.append('ellipse').attr('rx', 7).attr('ry', 4).attr('class', 'nb-shadow')
      const trail = fx.append('path').attr('class', 'nb-arc').attr('stroke', (L.fire.get(shooter ?? '') ?? 0) >= 3 ? '#f97316' : c); const pts: Array<[number, number]> = []
      await ball.transition().duration(ft ? 900 : 950 + dist * 0.8).ease(d3.easeLinear).tween('fly', () => (t: number) => {
        const x = target.x + (b.x - target.x) * t; const y = target.y - 18 + (b.y - target.y + 18) * t; const hh = 4 * apex * t * (1 - t)
        ball.attr('transform', `translate(${x},${y - hh * 0.55}) scale(${1 + hh / 150}) rotate(${t * 600})`)
        shadow.attr('cx', x).attr('cy', y + 4).attr('rx', 7 - hh / 40).style('opacity', 0.4 - hh / 500)
        pts.push([x, y - hh * 0.55]); trail.attr('d', d3.line()(pts) ?? '')
      }).end().catch(() => undefined)
      shadow.remove(); trail.transition().duration(1200).style('opacity', 0).remove()
      if (dead) return
      setCaption(p)
      if (made) {
        bxy = b; ball.transition().duration(240).attr('transform', `translate(${b.x},${b.y + 8}) scale(.7)`)
        for (let r = 0; r < 3; r++) fx.append('circle').attr('cx', b.x).attr('cy', b.y).attr('r', 6).attr('fill', 'none').attr('stroke', r === 1 ? '#fff' : c).attr('stroke-width', 3 - r * 0.6)
          .transition().delay(r * 110).duration(900).ease(d3.easeCubicOut).attr('r', 46 + r * 18).style('opacity', 0).remove()
        label({ x: b.x + (homeEnd ? -46 : 46), y: b.y + 20 }, ft ? '+1' : three ? '+3' : '+2', 'nb-pts', c)
        const streak = shooter ? L.fire.get(shooter) ?? 0 : 0
        const hype = !ft && (three || isDunk(p) || streak >= 3)
        if (hype && big) {
          confetti(b, c)
          setTake({ p, word: streak >= 3 && e.i === L.feed.plays.length - 1 ? "HE'S ON FIRE!" : isDunk(p) ? 'SLAM!' : 'SPLASH!' })
          if (isDunk(p)) { setShake(true); window.setTimeout(() => setShake(false), 520) }
          await sleep(2600); setTake(null)
        } else if (hype) { confetti(b, c); await sleep(500) }
      } else {
        const off = (homeEnd ? -1 : 1) * (20 + Math.random() * 18)
        const carom = { x: b.x + off, y: b.y + (Math.random() - 0.5) * 80 }
        await ball.transition().duration(360).ease(d3.easeQuadOut).attr('transform', `translate(${carom.x},${carom.y - 10})`).end().catch(() => undefined)
        bxy = carom; label(b, ft ? 'MISS' : 'CLANK', 'nb-clank')
      }
      if (big && !ft) await camera(W / 2, H / 2, W, 600)
      if (made && !(ft && /1 of 2|2 of 3/i.test(p.type ?? ''))) { const other = p.team === home.id ? away.id : home.id; await formation(L.lineups[e.i], other, big ? 900 : 700) }
    }
    const step = async (e: { p: ArenaPlay; i: number }) => {
      const p = e.p
      if (p.shooting) return shoot(e)
      if (/substitution/i.test(p.type ?? '')) {
        draw(L.lineups[e.i]); const inn = p.players[0]?.id
        if (inn && possession) { await formation(L.lineups[e.i], possession, 500); const pp = posOf.get(inn); if (pp) label(pp, 'CHECKS IN', 'nb-tag', L.col[p.team ?? ''] ?? '#fff') }
        return sleep(250)
      }
      const who = p.players[0]?.id; const wt = who ? teamOfPlayer(who) : p.team
      if (/rebound/i.test(p.type ?? '')) {
        const pp = who ? posOf.get(who) : null
        if (pp) { await pass(pp, 420); label(pp, /offensive/i.test(p.type ?? '') ? 'OFF. BOARD' : 'REBOUND', 'nb-tag', L.col[wt ?? ''] ?? '#fff') }
        if (wt && wt !== possession) await formation(L.lineups[e.i], wt, big ? 800 : 650)
        return
      }
      if (/turnover|steal/i.test(p.type ?? '')) {
        const thief = p.players[1]?.id; const tp = thief ? posOf.get(thief) : null
        if (tp) { await pass(tp, 380); label(tp, 'STEAL!', 'nb-tag', L.col[teamOfPlayer(thief!) ?? ''] ?? '#fff') } else label(bxy, 'TURNOVER', 'nb-tag')
        const other = p.team === home.id ? away.id : home.id
        await formation(L.lineups[e.i], other, big ? 800 : 650)
      }
    }
    ;(async () => {
      const first = events[0]
      draw(L.lineups[Math.max(0, first.i - 1)])
      setCaption(first.p)
      const off = first.p.shooting ? first.p.team : (first.p.players[0] && teamOfPlayer(first.p.players[0].id)) || first.p.team
      if (off) await formation(L.lineups[Math.max(0, first.i - 1)], off, 700)
      for (const e of events) { if (dead) return; draw(L.lineups[e.i]); if (!e.p.shooting) setCaption(e.p); await step(e); await sleep(big ? 350 : 220) }
      if (!dead && run.mode !== 'recent') window.setTimeout(() => !dead && setRun((r) => ({ mode: 'recent', n: r.n })), 2500)
    })()
    return () => { dead = true; svg.interrupt('cam'); svg.attr('viewBox', `0 0 ${W} ${H}`); fx.selectAll('*').interrupt().remove(); tok.selectAll('*').interrupt() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run])

  const periods = Array.from(new Set(shots.map((s) => s.p.period ?? 0))).filter(Boolean).sort((a, b) => a - b)
  const shown = shots.filter((s) => !s.ft && (team === 'all' || s.p.team === team) && (!period || s.p.period === period))
  // the run going right now: points by one team since the other last scored
  const runNow = useMemo(() => {
    let tid: string | null = null; let pts = 0; let start: ArenaPlay | null = null
    for (const p of feed.plays) { if (!p.scoring || !p.team) continue; if (p.team === tid) pts += p.value ?? 0; else { tid = p.team; pts = p.value ?? 0; start = p } }
    return tid ? { tid, pts, start } : null
  }, [feed.plays])
  const cardA = card ? feed.athletes.find((a) => a.id === card) ?? null : null
  return (
    <section className="nb-card nb-arena" aria-label="The arena">
      <header>
        <h2>The Arena <small>{run.mode === 'replay' ? 'replaying the moment' : run.mode === 'new' ? 'live: the newest plays' : 'the last run of plays, on a loop'} · tap a player</small></h2>
        <span className="ar-seg">{([['shots', 'Every shot'], ['zones', '🔥 Hot zones']] as const).map(([k, l]) => <button key={k} type="button" className={lens === k ? 'is-on' : ''} onClick={() => setLens(k)}>{l}</button>)}</span>
        <span className="ar-seg">{[['all', 'Both'], [away.id, away.abbr], [home.id, home.abbr]].map(([k, l]) => <button key={k} type="button" className={team === k ? 'is-on' : ''} onClick={() => setTeam(k)}>{l}</button>)}</span>
        <span className="ar-seg">{[0, ...periods].map((k) => <button key={k} type="button" className={period === k ? 'is-on' : ''} onClick={() => setPeriod(k)}>{k ? periodName('nba', k) : 'All'}</button>)}</span>
      </header>
      <div className={`nb-arena__wrap${shake ? ' is-shake' : ''}`} style={{ ['--nb-paint' as string]: hex(home.color, '#1d428a') } as CSSProperties}>
        <div className="nb-jumbo" aria-label="Scoreboard">
          <span style={{ color: col[away.id] }}>{away.logo ? <img src={away.logo} alt="" /> : null}{away.abbr} <Tween value={away.score} /></span>
          <em>{feed.status.state === 'in' ? `${periodName('nba', feed.status.period)} ${feed.status.clock ?? ''}` : feed.status.short}</em>
          <span style={{ color: col[home.id] }}><Tween value={home.score} /> {home.abbr}{home.logo ? <img src={home.logo} alt="" /> : null}</span>
          {runNow && runNow.pts >= 6 ? <i className="nb-jumbo__run" style={{ ['--c' as string]: col[runNow.tid] } as CSSProperties}>{runNow.pts >= 10 ? '🔥 ' : ''}{teamOf(runNow.tid).abbr} {runNow.pts}–0 RUN</i> : null}
        </div>
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Full court with the players, every shot and the ball">
          <Court home={home} away={away} />
          {lens === 'zones' ? <>
            {team !== home.id ? <HotZones shots={shown.filter((s) => s.p.team === away.id)} homeEnd={false} id="nbz-a" /> : null}
            {team !== away.id ? <HotZones shots={shown.filter((s) => s.p.team === home.id)} homeEnd id="nbz-h" /> : null}
          </> : shown.map((s) => {
            const c = col[s.p.team ?? ''] ?? '#e2e8f0'
            const hot = s.made && s.shooter && (fire.get(s.shooter) ?? 0) >= 3
            return s.made
              ? <circle key={s.p.id} cx={s.x} cy={s.y} r={s.three ? 6 : 4.5} fill={hot ? '#f97316' : c} stroke={hot ? '#fde047' : '#0b1526'} strokeWidth={s.three ? 1.8 : 1.2} opacity="0.85" className={`ar-shot${hot ? ' nb-hot' : ''}`}><title>{s.p.text}</title></circle>
              : <g key={s.p.id} transform={`translate(${s.x} ${s.y})`} className="ar-shot" opacity="0.6"><path d="M-3.5 -3.5 L3.5 3.5 M3.5 -3.5 L-3.5 3.5" stroke={c} strokeWidth="2" strokeLinecap="round" /><title>{s.p.text}</title></g>
          })}
          <g ref={tokRef} />
          <g ref={fxRef} />
        </svg>
        {take ? <Takeover p={take.p} word={take.word} ctx={ctx} /> : null}
        {cardA ? <CourtCard a={cardA} ctx={ctx} onClose={() => setCard(null)} /> : null}
      </div>
      <div className="nb-caption" style={{ ['--c' as string]: caption ? col[caption.team ?? ''] ?? '#94a3b8' : '#94a3b8' } as CSSProperties} aria-live="polite">
        {caption ? (<>
          {caption.players[0]?.headshot ? <img src={caption.players[0].headshot} alt="" /> : <i />}
          <span><b>{caption.shooting ? (caption.scoring ? (isFT(caption) ? 'FREE THROW' : caption.attempted === 3 ? 'THREE' : isDunk(caption) ? 'DUNK' : 'BUCKET') : 'MISS') : (caption.type ?? '').split('\n')[0].toUpperCase()}</b> <small>{periodName('nba', caption.period)} {caption.clock} · {away.abbr} {caption.away ?? ''}–{caption.home ?? ''} {home.abbr}</small><br />{caption.text}</span>
        </>) : <span>Waiting for tip-off…</span>}
      </div>
      <div className="ar-rink__key"><span>faces = the ten on the floor (tap one) · number = points tonight</span><span>● made · × missed</span><span className="is-hot">🔥 = 3+ straight makes</span>{lens === 'zones' ? <span>zones: red = shooting above the NBA norm, blue = below</span> : null}</div>
    </section>
  )
}

function Tween({ value }: { value: number }) {
  const ref = useRef<HTMLSpanElement | null>(null); const prev = useRef(value)
  useEffect(() => {
    const el = ref.current; if (!el) return undefined
    const from = prev.current; prev.current = value
    if (from === value || reduced()) { el.textContent = String(value); return undefined }
    el.classList.remove('is-pop'); void el.offsetWidth; el.classList.add('is-pop')
    const t = d3.timer((ms) => { const k = Math.min(1, ms / 700); el.textContent = String(Math.round(from + (value - from) * d3.easeCubicOut(k))); if (k >= 1) t.stop() })
    return () => t.stop()
  }, [value])
  return <b ref={ref} className="nb-tween">{value}</b>
}

function Takeover({ p, word, ctx }: { p: ArenaPlay; word: string; ctx: Ctx }) {
  const id = p.players[0]?.id; const a = ctx.feed.athletes.find((x) => x.id === id); const t = ctx.teamOf(p.team)
  const c = ctx.col[p.team ?? ''] ?? '#f97316'
  const fire = word.includes('FIRE')
  return (
    <div className={`nb-take${fire ? ' is-fire' : ''}`} style={{ ['--c' as string]: c } as CSSProperties}>
      <div className="nb-take__slab">
        {t.logo ? <img src={t.logo} alt="" className="nb-take__logo" /> : null}
        {p.players[0]?.headshot ? <img src={p.players[0].headshot} alt="" className="nb-take__face" /> : null}
        {fire ? <i className="nb-flames nb-take__flames" aria-hidden="true"><i /><i /><i /></i> : null}
      </div>
      <div className="nb-take__txt">
        <b>{word}</b>
        <em>{p.players[0]?.name ?? t.short}</em>
        <span><i>{a?.stats.points ?? '—'}<small>PTS</small></i><i>{a?.stats['fieldGoalsMade-fieldGoalsAttempted'] ?? '—'}<small>FG</small></i><i>{a?.stats['threePointFieldGoalsMade-threePointFieldGoalsAttempted'] ?? '—'}<small>3PT</small></i><i>{a?.stats.assists ?? '—'}<small>AST</small></i></span>
      </div>
    </div>
  )
}

function CourtCard({ a, ctx, onClose }: { a: ArenaAthlete; ctx: Ctx; onClose: () => void }) {
  const t = ctx.teamOf(a.team); const c = ctx.col[t.id] ?? '#94a3b8'
  const mine = ctx.shots.filter((s) => s.shooter === a.id && !s.ft)
  const made = mine.filter((s) => s.made).length
  const streak = ctx.fire.get(a.id) ?? 0
  const s = a.stats
  return (
    <div className="nb-ccard" style={{ ['--c' as string]: c } as CSSProperties} role="dialog" aria-label={`${a.name} on the court`}>
      <button type="button" onClick={onClose} aria-label="Close">✕</button>
      <div className="nb-ccard__top">{a.headshot ? <img src={a.headshot} alt="" /> : null}<span><b>{a.name}</b><small>{a.pos}{a.jersey ? ` · #${a.jersey}` : ''} · {t.short}</small>{streak >= 2 ? <em>{streak >= 3 ? '🔥 ON FIRE' : 'HEATING UP'} · {streak} straight</em> : null}</span></div>
      <div className="nb-ccard__stats">{([['PTS', s.points], ['REB', s.rebounds], ['AST', s.assists], ['FG', s['fieldGoalsMade-fieldGoalsAttempted']], ['3PT', s['threePointFieldGoalsMade-threePointFieldGoalsAttempted']], ['FT', s['freeThrowsMade-freeThrowsAttempted']], ['STL', s.steals], ['BLK', s.blocks], ['+/−', s.plusMinus], ['MIN', s.minutes]] as Array<[string, string | undefined]>).map(([l, v]) => <span key={l}><b>{v ?? '—'}</b><small>{l}</small></span>)}</div>
      <p>{mine.length ? `${made} of ${mine.length} from the floor tonight (${Math.round((made / mine.length) * 100)}%).` : 'No shots from the floor yet.'}</p>
    </div>
  )
}

// ------------------------------------------------------------ on fire --

function OnFire({ ctx }: { ctx: Ctx }) {
  const { feed, fire, col } = ctx
  const hot = Array.from(fire.entries()).filter(([, n]) => n >= 2).sort((a, b) => b[1] - a[1])
  const seen = useRef<Set<string> | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  useEffect(() => {
    const now = new Set(hot.filter(([, n]) => n >= 3).map(([id]) => id))
    if (seen.current) for (const id of now) if (!seen.current.has(id)) { setFlash(id); window.setTimeout(() => setFlash(null), 5000) }
    seen.current = now
  }, [hot])
  if (!hot.length) return null
  const person = (id: string) => feed.athletes.find((a) => a.id === id) ?? null
  return (
    <section className="nb-fire" aria-label="Who is hot">
      {hot.slice(0, 3).map(([id, n]) => {
        const a = person(id); const c = col[a?.team ?? ''] ?? '#f97316'
        return (
          <div key={id} className={`nb-fire__card${n >= 3 ? ' is-fire' : ''}${flash === id ? ' is-flash' : ''}`} style={{ ['--c' as string]: c } as CSSProperties}>
            <span className="nb-fire__face">{a?.headshot ? <img src={a.headshot} alt="" /> : null}{n >= 3 ? <i className="nb-flames" aria-hidden="true"><i /><i /><i /></i> : null}</span>
            <span><b>{n >= 3 ? "HE'S ON FIRE!" : 'HEATING UP'}</b><em>{a?.name ?? 'Player'}</em><small>{n} straight makes · {a?.stats.points ?? '—'} pts ({a?.stats['fieldGoalsMade-fieldGoalsAttempted'] ?? '—'} FG)</small></span>
          </div>
        )
      })}
    </section>
  )
}

// ------------------------------------------------------------ big moments --

function BigMoments({ ctx, onReplay }: { ctx: Ctx; onReplay: (s: Shot) => void }) {
  const { feed, shots, col } = ctx
  const list = useMemo(() => {
    const out: Array<{ s: Shot; tag: string; score: number }> = []
    let lastSign = 0
    shots.forEach((s, i) => {
      if (!s.made) return
      const margin = (s.p.home ?? 0) - (s.p.away ?? 0); const sign = Math.sign(margin)
      const late = (s.p.period ?? 1) >= 4 && elapsedAt('nba', s.p.period, s.p.clock) >= 2580 && Math.abs(margin) <= 6
      const andOne = !s.ft && shots[i + 1]?.ft && shots[i + 1].shooter === s.shooter && shots[i + 1].p.clock === s.p.clock && /1 of 1/i.test(shots[i + 1].p.type ?? '')
      const change = sign !== 0 && lastSign !== 0 && sign !== lastSign
      if (sign !== 0) lastSign = sign
      if (s.ft) return
      const tag = andOne ? 'AND-ONE' : isDunk(s.p) ? 'SLAM' : change ? 'LEAD CHANGE' : late ? 'CLUTCH' : s.three ? 'SPLASH' : ''
      if (tag) out.push({ s, tag, score: (late ? 3 : 0) + (change ? 2 : 0) + (andOne ? 2 : 0) + (isDunk(s.p) ? 1.5 : 0) + (s.three ? 1 : 0) })
    })
    return out.slice(-14).reverse()
  }, [shots])
  if (!list.length) return null
  return (
    <section className="nb-card nb-moments" aria-label="Big moments">
      <header><h2>Big Moments <small>tap ▶ to watch it on the court</small></h2></header>
      <ol>
        {list.map(({ s, tag }, k) => {
          const who = s.p.players[0]
          return (
            <li key={s.p.id} style={{ ['--c' as string]: col[s.p.team ?? ''] ?? '#94a3b8', animationDelay: `${k * 70}ms` } as CSSProperties}>
              <span className="nb-moments__face">{who?.headshot ? <img src={who.headshot} alt="" /> : <i />}</span>
              <span className="nb-moments__txt"><b className={`t-${tag.replace(/\W/g, '').toLowerCase()}`}>{tag}</b><em>{who?.name ?? ''}</em><small>{periodName('nba', s.p.period)} {s.p.clock} · {feed.teams.away.abbr} {s.p.away}–{s.p.home} {feed.teams.home.abbr}</small></span>
              <button type="button" className="nb-moments__play" onClick={() => onReplay(s)} aria-label={`Replay ${tag} by ${who?.name ?? 'player'}`}>▶</button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

// ------------------------------------------------------------ who wins --

function WhoWins({ ctx }: { ctx: Ctx }) {
  const { feed, col } = ctx
  const { home, away } = feed.teams
  const byId = useMemo(() => new Map(feed.plays.map((p) => [p.id, p])), [feed.plays])
  const pts = useMemo(() => {
    const raw = feed.win_probability.map((w, i) => { const p = byId.get(w.play); return p ? { t: elapsedAt('nba', p.period, p.clock), p: w.home, play: p, i } : null })
      .filter((x): x is { t: number; p: number; play: ArenaPlay; i: number } => !!x).sort((x, y) => x.t - y.t || x.i - y.i)
    if (feed.status.state !== 'post' || !raw.length) return raw
    // ESPN appends readings after the buzzer; a finished game ends at its result
    const end = Math.max(...raw.map((r) => r.t))
    const last = raw[raw.length - 1].play
    return [...raw.filter((r) => r.t < end), { t: end, p: home.score > away.score ? 1 : 0, play: last, i: raw.length }]
  }, [feed.win_probability, feed.status.state, byId, home.score, away.score])
  const now = pts.length ? pts[pts.length - 1].p : null
  const needle = useRef<SVGGElement | null>(null); const label = useRef<SVGTextElement | null>(null); const prev = useRef(0.5)
  useEffect(() => {
    if (now == null) return
    const from = prev.current; prev.current = now
    const ang = (p: number) => -90 + p * 180; const dur = reduced() ? 0 : 1600
    if (needle.current) d3.select(needle.current).transition().duration(dur).ease(d3.easeElasticOut.amplitude(1).period(0.5)).attrTween('transform', () => (t: number) => `rotate(${ang(from + (now - from) * t)} 150 132)`)
    const el = label.current
    if (el) d3.select(el).transition().duration(dur).tween('text', () => { const i = d3.interpolate(from, now); return (t: number) => { const v = i(t); el.textContent = pct(Math.max(v, 1 - v)) } })
  }, [now])
  if (now == null) return null
  const RW = 520; const RH = 150; const pad = 16
  const end = Math.max(2880, pts[pts.length - 1].t)
  const X = (t: number) => pad + (t / end) * (RW - pad * 2); const Y = (p: number) => 8 + (1 - p) * (RH - 28)
  const smooth = pts.filter((_, i) => i % Math.max(1, Math.floor(pts.length / 160)) === 0 || i === pts.length - 1)
  const line = d3.line<{ t: number; p: number }>().x((d) => X(d.t)).y((d) => Y(d.p)).curve(d3.curveMonotoneX)(smooth) ?? ''
  const area = d3.area<{ t: number; p: number }>().x((d) => X(d.t)).y0(Y(0.5)).y1((d) => Y(d.p)).curve(d3.curveMonotoneX)(smooth) ?? ''
  const swings = pts.map((d, i) => ({ ...d, dp: i ? d.p - pts[i - 1].p : 0 })).filter((d) => d.play.players[0]?.headshot && Math.abs(d.dp) <= 0.3 && d.t > 60 && d.i < pts.length - 1).sort((a, b) => Math.abs(b.dp) - Math.abs(a.dp)).slice(0, 5)
  const fav = now >= 0.5 ? home : away
  const arc = (a0: number, a1: number) => d3.arc()({ innerRadius: 92, outerRadius: 118, startAngle: a0, endAngle: a1 }) ?? ''
  return (
    <section className="nb-card nb-wp" aria-label="Win probability">
      <header><h2>Who Wins? <small>ESPN live win probability</small></h2></header>
      <svg viewBox="0 0 300 196" className="nh-wp__gauge" role="img" aria-label={`${fav.short} ${pct(Math.max(now, 1 - now))}`}>
        <g transform="translate(150 132)">
          <path d={arc(-Math.PI / 2, -0.012)} fill={col[away.id]} opacity={now < 0.5 ? 0.95 : 0.4} />
          <path d={arc(0.012, Math.PI / 2)} fill={col[home.id]} opacity={now >= 0.5 ? 0.95 : 0.4} />
        </g>
        <g ref={needle} transform="rotate(0 150 132)"><path d="M146 132 L150 30 L154 132 Z" fill="#fff" /><circle cx="150" cy="132" r="10" fill="#fff" /><circle cx="150" cy="132" r="4" fill="#0b1526" /></g>
        {away.logo ? <image href={away.logo} x="6" y="136" width="36" height="36" /> : null}
        {home.logo ? <image href={home.logo} x="258" y="136" width="36" height="36" /> : null}
        <text x="150" y="170" textAnchor="middle" className="nh-wp__big" ref={label}>{pct(Math.max(now, 1 - now))}</text>
        <text x="150" y="190" textAnchor="middle" className="nh-wp__who" fill={col[fav.id]}>{feed.status.state === 'post' ? `${fav.short} won` : `${fav.short} to win`}</text>
      </svg>
      <svg viewBox={`0 0 ${RW} ${RH}`} className="nh-wp__river" role="img" aria-label="Win probability over the game">
        <defs><clipPath id="nb-up"><rect x="0" y="0" width={RW} height={Y(0.5)} /></clipPath><clipPath id="nb-dn"><rect x="0" y={Y(0.5)} width={RW} height={RH} /></clipPath></defs>
        {[1, 2, 3].map((k) => <line key={k} x1={X(k * 720)} x2={X(k * 720)} y1={6} y2={RH - 18} className="ar-flow__grid" />)}
        {[0, 1, 2, 3].map((k) => <text key={k} x={X(k * 720 + 360)} y={RH - 4} textAnchor="middle" className="ar-flow__tick">{periodName('nba', k + 1)}</text>)}
        <line x1={pad} x2={RW - pad} y1={Y(0.5)} y2={Y(0.5)} className="ar-flow__zero" />
        <path d={area} fill={col[home.id]} opacity="0.35" clipPath="url(#nb-up)" className="nh-fadein" />
        <path d={area} fill={col[away.id]} opacity="0.35" clipPath="url(#nb-dn)" className="nh-fadein" />
        <path d={line} className="nh-wp__line" pathLength={1} />
        <text x={pad + 2} y={16} className="ar-flow__lab" fill={col[home.id]}>{home.abbr} ↑</text><text x={pad + 2} y={RH - 22} className="ar-flow__lab" fill={col[away.id]}>{away.abbr} ↓</text>
        {swings.map((s) => { const x = X(s.t); const y = Y(s.p); const id = `nb-wf-${s.play.id}`; return (
          <g key={s.play.id} className="nh-wp__goal"><clipPath id={id}><circle cx={x} cy={y} r="11" /></clipPath>
            <circle cx={x} cy={y} r="13" fill="#0b1526" stroke={col[s.play.team ?? ''] ?? '#fff'} strokeWidth="2.5" />
            <image href={s.play.players[0]!.headshot!} x={x - 11} y={y - 11} width="22" height="22" clipPath={`url(#${id})`} />
            <title>{`${s.play.text} (${home.abbr} ${s.dp >= 0 ? '+' : '−'}${Math.abs(Math.round(s.dp * 100))} pts)`}</title></g>) })}
      </svg>
      {swings[0] ? <p className="nh-wp__swing">Biggest swing: <b>{swings[0].play.players[0]?.name}</b>, {Math.abs(Math.round(swings[0].dp * 100))} points ({periodName('nba', swings[0].play.period)} {swings[0].play.clock}).</p> : null}
    </section>
  )
}

// ------------------------------------------------------------ free throws --

function FreeThrowLine({ ctx }: { ctx: Ctx }) {
  const { feed, shots, col } = ctx
  const fts = shots.filter((s) => s.ft)
  if (!fts.length) return null
  return (
    <section className="nb-card nb-ft" aria-label="Free throw line">
      <header><h2>The Free Throw Line <small>every attempt, in order</small></h2></header>
      {[feed.teams.away, feed.teams.home].map((t) => {
        const mine = fts.filter((s) => s.p.team === t.id); const made = mine.filter((s) => s.made).length
        return (
          <div key={t.id} className="nb-ft__row" style={{ ['--c' as string]: col[t.id] } as CSSProperties}>
            <b>{t.logo ? <img src={t.logo} alt="" /> : null}{t.abbr}</b>
            <span className="nb-ft__balls">{mine.map((s, k) => <i key={s.p.id} className={s.made ? 'is-made' : 'is-miss'} style={{ animationDelay: `${k * 35}ms` }} title={s.p.text ?? ''} />)}</span>
            <strong>{made}/{mine.length}<small>{mine.length ? pct(made / mine.length) : '—'}</small></strong>
          </div>
        )
      })}
      <p className="ar-small">A typical NBA team makes about 78% of its free throws.</p>
    </section>
  )
}
