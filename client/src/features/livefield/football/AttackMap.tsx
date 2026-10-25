/**
 * ATTACK MAP: where an offense is going and where it is getting results, on a field drawn the way the
 * broadcast shows it (offense driving right). Discrete zones (short/deep × left/middle/right, plus the
 * run lanes) glow by usage or by expected points added; every snap is a dot that flies in from the
 * quarterback with D3 physics and settles in its zone. Pass / Run / Both, two teams, and a "most targeted"
 * list. Click a dot to replay the play. Sample sizes are shown so a lucky zone never looks like a trend.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { forceCollide, forceSimulation, forceX, forceY, interpolateRgb, scaleLinear, select, symbol, symbolCircle, symbolCross, symbolDiamond, symbolStar, type Simulation, type SimulationNodeDatum } from 'd3'

/** Volume heat: cold blue where the offense rarely goes, hot red where it lives (the owner's heat-map look). */
export const HEAT = scaleLinear<string>().domain([0, 0.3, 0.6, 1]).range(['#1e3a8a', '#06b6d4', '#facc15', '#ef4444']).interpolate(interpolateRgb)
import { parseFbPlay, visibleColor, type FbPlay, type FbTeam } from './footballData'
import type { EpaPlay } from './ep'
import { CLASSES, categoryProbs, classOfPlay, forecastFor, predict, stateForPlay, type Forecast, type Grid, type PlayClass } from './playcall'

/** Which attack-map zone each play-call class lands in. */
export const CLASS_ZONE: Partial<Record<PlayClass, string>> = { PASS_SL: 'short-left', PASS_SM: 'short-middle', PASS_SR: 'short-right', PASS_DL: 'deep-left', PASS_DM: 'deep-middle', PASS_DR: 'deep-right', RUN_L: 'run-left', RUN_M: 'run-middle', RUN_R: 'run-right' }
/** The next-play list's rows (DecisionIntel) as attack-map zones, so hovering a call lights up where it goes. */
export const ROW_ZONES: Record<string, string[]> = { short: ['short-left', 'short-middle', 'short-right'], deep: ['deep-left', 'deep-middle', 'deep-right'], inside: ['run-middle'], outside: ['run-left', 'run-right'], kick: [] }

const W = 760
const H = 380
const X = scaleLinear().domain([-12, 48]).range([44, 736])
const Y = scaleLinear().domain([-27, 27]).range([52, 346])
const QB = { x: X(-5), y: Y(0) }

type Kind = 'pass' | 'incomplete' | 'interception' | 'sack' | 'run' | 'td'
interface Pt extends SimulationNodeDatum { id: string; kind: Kind; tx: number; ty: number; zone: string; yards: number; epa: number | null; text: string; clock: string; label: string }

const hash = (s: string) => { let h = 2166136261; for (let i = 0; i < s.length; i += 1) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) } return (h >>> 0) / 4294967295 }
const GAP_Y: Record<string, number> = { 'left end': -9, 'left tackle': -5, 'left guard': -2.5, 'up the middle': 0, middle: 0, 'right guard': 2.5, 'right tackle': 5, 'right end': 9, left: -6, right: 6 }
export const ZONES = [
  { id: 'short-left', name: 'Short left', x0: 0, x1: 12, y0: -27, y1: -8 }, { id: 'short-middle', name: 'Short middle', x0: 0, x1: 12, y0: -8, y1: 8 }, { id: 'short-right', name: 'Short right', x0: 0, x1: 12, y0: 8, y1: 27 },
  { id: 'deep-left', name: 'Deep left', x0: 12, x1: 48, y0: -27, y1: -8 }, { id: 'deep-middle', name: 'Deep middle', x0: 12, x1: 48, y0: -8, y1: 8 }, { id: 'deep-right', name: 'Deep right', x0: 12, x1: 48, y0: 8, y1: 27 },
  { id: 'run-left', name: 'Runs left', x0: -12, x1: 0, y0: -27, y1: -3.5 }, { id: 'run-middle', name: 'Runs inside', x0: -12, x1: 0, y0: -3.5, y1: 3.5 }, { id: 'run-right', name: 'Runs right', x0: -12, x1: 0, y0: 3.5, y1: 27 },
]
const zoneOf = (dx: number, y: number, run: boolean) => (run ? `run-${y < -3.5 ? 'left' : y > 3.5 ? 'right' : 'middle'}` : `${dx >= 12 ? 'deep' : 'short'}-${y < -8 ? 'left' : y > 8 ? 'right' : 'middle'}`)

export function chartPoints(plays: readonly FbPlay[], teamId: string, epaById: Map<string, number>): Pt[] {
  const out: Pt[] = []
  for (const p of plays) {
    if ((p.offense ?? p.start.team) !== teamId) continue
    const k = parseFbPlay(p)
    const r = hash(p.id)
    const r2 = hash(p.id + 'y')
    const base = { id: p.id, yards: p.yards ?? 0, epa: epaById.get(p.id) ?? null, text: p.text ?? '', clock: `Q${p.period} ${p.clock}`, label: k.label }
    if (k.kind === 'pass' || k.kind === 'incomplete' || k.kind === 'interception') {
      const cy = k.side === 'left' ? -16 : k.side === 'right' ? 16 : k.side === 'middle' ? 0 : (r - 0.5) * 40
      const y = Math.max(-25, Math.min(25, cy + (r - 0.5) * (k.side ? 12 : 4)))
      const air = k.kind === 'pass' ? Math.max(0, Math.min(46, (p.yards ?? 0) - (p.yac ?? Math.round(Math.max(0, p.yards ?? 0) * 0.25)))) : (k.depth === 'deep' ? 24 : k.depth === 'short' ? 6 : 12) + (r2 - 0.5) * 8
      const dx = Math.max(0.5, Math.min(46, air))
      out.push({ ...base, kind: k.kind === 'pass' ? (k.touchdown ? 'td' : 'pass') : k.kind, tx: X(dx), ty: Y(y), zone: zoneOf(dx, y, false) })
    } else if (k.kind === 'sack') {
      out.push({ ...base, kind: 'sack', tx: X(-6 + (r - 0.5) * 2), ty: Y((r2 - 0.5) * 6), zone: 'sack' })
    } else if (k.kind === 'rush') {
      const gy = k.gap && GAP_Y[k.gap] !== undefined ? GAP_Y[k.gap] : (r - 0.5) * 10
      const y = gy + (r2 - 0.5) * 2
      out.push({ ...base, kind: k.touchdown ? 'td' : 'run', tx: X(Math.max(-11, Math.min(-1, -6 + (p.yards ?? 0) * 0.15))), ty: Y(y), zone: zoneOf(0, y, true) })
    }
  }
  return out
}

const COLORS: Record<Kind, string> = { pass: '#3fd8c4', incomplete: '#ff7a83', interception: '#ff4d5e', sack: '#ff4d5e', run: '#ffb547', td: '#ffd76a' }
const symbolFor = (k: Kind) => (k === 'run' ? symbolDiamond : k === 'td' ? symbolStar : k === 'sack' || k === 'interception' ? symbolCross : symbolCircle)

interface Props { plays: readonly FbPlay[]; epa: EpaPlay[]; away: FbTeam; home: FbTeam; shownId: string | null; defaultTeamId: string | null; onPick: (id: string) => void; highlight?: Set<string> | null; focusTeam?: string | null; /** Narrow column beside the field: list under the map, fewer controls. */ compact?: boolean; title?: string
  /** Play-call model + home id: unlocks the "vs Model" lens (what the model expected vs where the offense went). */
  grid?: Grid | null; homeId?: string
  /** The forecast for the snap on the field, drawn as each zone's chance when it is this team's snap. */
  forecast?: Forecast | null; forecastTeamId?: string | null
  /** Zones to outline (a hovered row of the next-play list). */
  focusZones?: string[] | null; live?: boolean }

export function AttackMap({ plays, epa, away, home, shownId, defaultTeamId, onPick, highlight = null, focusTeam = null, compact = false, title = 'ATTACK MAP', grid = null, homeId, forecast = null, forecastTeamId = null, focusZones = null, live = false }: Props) {
  const [teamId, setTeamId] = useState<string>(defaultTeamId ?? away.id)
  const [follow, setFollow] = useState(true)
  const [mode, setMode] = useState<'pass' | 'run' | 'both'>('both')
  const [lens, setLens] = useState<'usage' | 'epa' | 'model'>('usage')
  useEffect(() => { if (follow && defaultTeamId) setTeamId(defaultTeamId) }, [defaultTeamId, follow])
  useEffect(() => { if (focusTeam) { setTeamId(focusTeam); setFollow(false) } }, [focusTeam])
  const team = teamId === home.id ? home : away
  const other = teamId === home.id ? away : home
  const color = visibleColor(team, '#3fd8c4')
  const epaById = useMemo(() => new Map(epa.map((e) => [e.id, e.epa])), [epa])
  const all = useMemo(() => chartPoints(plays, teamId, epaById), [plays, teamId, epaById])
  const pts = useMemo(() => all.filter((p) => (mode === 'both' ? true : mode === 'run' ? p.zone.startsWith('run') : !p.zone.startsWith('run'))), [all, mode])
  const zones = useMemo(() => {
    const m = new Map<string, { n: number; comp: number; yards: number; epa: number; ids: string[] }>()
    for (const p of all) {
      if (p.zone === 'sack') continue
      const e = m.get(p.zone) ?? { n: 0, comp: 0, yards: 0, epa: 0, ids: [] }
      e.n += 1; e.yards += p.kind === 'incomplete' || p.kind === 'interception' ? 0 : p.yards; e.epa += p.epa ?? 0; e.ids.push(p.id)
      if (p.kind === 'pass' || p.kind === 'td' || p.kind === 'run') e.comp += 1
      m.set(p.zone, e)
    }
    return m
  }, [all])
  const passN = all.filter((p) => !p.zone.startsWith('run') && p.zone !== 'sack').length
  const runN = all.filter((p) => p.zone.startsWith('run')).length
  const top = [...zones.entries()].filter(([id]) => (mode === 'run' ? id.startsWith('run') : mode === 'pass' ? !id.startsWith('run') : true)).sort((a, b) => b[1].n - a[1].n).slice(0, 3)
  const denom = mode === 'run' ? runN : mode === 'pass' ? passN : passN + runN
  const maxN = Math.max(1, ...[...zones.values()].map((z) => z.n))
  const heat = scaleLinear<string>().domain([-0.7, 0, 0.7]).range(['#ff5d6c', '#8fa6c8', '#3fd8c4']).interpolate(interpolateRgb)
  // heat blobs: each snap warms its neighbourhood; colour = local density (usage) or local EPA (results)
  const blobs = useMemo(() => {
    const R = 70
    return pts.filter((p) => p.zone !== 'sack').map((p) => {
      let n = 0; let e = 0
      for (const q of pts) { if (q.zone === 'sack') continue; const d = Math.hypot(q.tx - p.tx, q.ty - p.ty); if (d < R) { n += 1; e += q.epa ?? 0 } }
      return { id: p.id, x: p.tx, y: p.ty, n, epa: n ? e / n : 0 }
    })
  }, [pts])
  const maxBlob = Math.max(3, ...blobs.map((b) => b.n))
  // validation: where the model expected this offense to go (zone shares) vs where it went, plus the run/pass hit rate
  const check = useMemo(() => {
    if (!grid || !homeId) return null
    const exp = new Map<string, number>(); const act = new Map<string, number>()
    let n = 0; let calls = 0; let hits = 0
    plays.forEach((p, i) => {
      if ((p.offense ?? p.start.team) !== teamId) return
      const { cls, category } = classOfPlay(parseFbPlay(p), p.text)
      if (category !== 'PASS' && category !== 'RUN') return
      const f = forecastFor(grid, plays, i, homeId)
      if (f) { const cp = categoryProbs(f.probs); calls += 1; if ((cp.PASS >= cp.RUN ? 'PASS' : 'RUN') === category) hits += 1 }
      const z = cls ? CLASS_ZONE[cls] : undefined
      const st = stateForPlay(plays, i, homeId)
      if (!z || !st) return
      const pr = predict(grid, st)
      const w = pr.slice(0, 9).reduce((t, v) => t + v, 0) || 1
      CLASSES.slice(0, 9).forEach((c, k) => { const zz = CLASS_ZONE[c]!; exp.set(zz, (exp.get(zz) ?? 0) + pr[k] / w) })
      act.set(z, (act.get(z) ?? 0) + 1); n += 1
    })
    const rows = ZONES.map((zz) => ({ id: zz.id, name: zz.name, act: n ? (act.get(zz.id) ?? 0) / n : 0, exp: n ? (exp.get(zz.id) ?? 0) / n : 0 })).map((r) => ({ ...r, diff: r.act - r.exp }))
    return { n, calls, hits, rows, byId: new Map(rows.map((r) => [r.id, r])) }
  }, [grid, homeId, plays, teamId])
  const snapProbs = useMemo(() => {
    if (!forecast || forecastTeamId !== teamId) return null
    const m = new Map<string, number>()
    CLASSES.slice(0, 9).forEach((c, k) => m.set(CLASS_ZONE[c]!, forecast.probs[k]))
    const topId = [...m.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null
    return { m, topId }
  }, [forecast, forecastTeamId, teamId])
  const diverge = scaleLinear<string>().domain([-0.15, 0, 0.15]).range(['#4aa8ff', '#5d6f8c', '#ff9f43']).interpolate(interpolateRgb)

  const svgRef = useRef<SVGSVGElement | null>(null)
  const simRef = useRef<Simulation<Pt, undefined> | null>(null)
  const nodesRef = useRef<Map<string, Pt>>(new Map())
  const trailsRef = useRef<Map<string, { attr: (k: string, v: number) => unknown }>>(new Map())
  const [hover, setHover] = useState<{ p: Pt; x: number; y: number } | null>(null)
  const pickRef = useRef(onPick)
  pickRef.current = onPick

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return undefined
    const reduce = Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) || new URLSearchParams(window.location.search).has('freeze')
    if (!simRef.current) simRef.current = forceSimulation<Pt>([]).force('x', forceX<Pt>((d) => d.tx).strength(0.24)).force('y', forceY<Pt>((d) => d.ty).strength(0.24)).force('collide', forceCollide<Pt>((d) => (d.kind === 'run' ? 5.5 : 7.5)).strength(0.9).iterations(2)).alphaDecay(0.035).velocityDecay(0.32)
    const sim = simRef.current
    const known = nodesRef.current
    const fresh = new Map<string, Pt>()
    for (const p of pts) {
      const old = known.get(p.id)
      if (old) { old.tx = p.tx; old.ty = p.ty; old.epa = p.epa; fresh.set(p.id, old) } else fresh.set(p.id, { ...p, x: QB.x, y: QB.y + (hash(p.id + 'qy') - 0.5) * 10 })
    }
    nodesRef.current = fresh
    const list = [...fresh.values()]
    sim.nodes(list)
    const root = select(svg).select<SVGGElement>('g.dots')
    const sel = root.selectAll<SVGGElement, Pt>('g.dot').data(list, (d) => d.id)
    sel.exit().transition().duration(250).style('opacity', 0).remove()
    const enter = sel.enter().append('g').attr('class', 'dot').style('cursor', 'pointer')
    enter.append('path').attr('class', 'shape')
    enter.append('circle').attr('class', 'halo').attr('r', 11).attr('fill', 'none')
    enter.each(function (d) { const g = select(this).attr('transform', `translate(${d.x},${d.y})`); if (reduce) g.attr('opacity', 1); else g.attr('opacity', 0).transition().duration(350).attr('opacity', 1) })
    const allSel = enter.merge(sel)
    allSel.select<SVGPathElement>('path.shape').attr('d', (d) => symbol().type(symbolFor(d.kind)).size(d.kind === 'run' ? 100 : d.kind === 'td' ? 300 : 160)()).attr('fill', (d) => (d.kind === 'incomplete' ? 'rgba(255,122,131,.12)' : COLORS[d.kind])).attr('stroke', (d) => (d.kind === 'incomplete' ? COLORS.incomplete : '#fff')).attr('stroke-width', (d) => (d.kind === 'incomplete' ? 2 : 1.2))
    allSel.select('circle.halo').attr('stroke', color)
    allSel.on('mouseenter', (_e, d) => { const r = svg.getBoundingClientRect(); setHover({ p: d, x: (d.x ?? 0) * (r.width / W), y: (d.y ?? 0) * (r.height / H) }) }).on('mouseleave', () => setHover(null)).on('click', (_e, d) => pickRef.current(d.id))
    const trails = select(svg).select<SVGGElement>('g.trails')
    enter.each((d) => { const line = trails.append('line').attr('x1', QB.x).attr('y1', QB.y).attr('stroke', COLORS[d.kind]).attr('stroke-width', 3).attr('stroke-linecap', 'round').attr('opacity', 0.85); trailsRef.current.set(d.id, line); line.transition().delay(700).duration(450).attr('opacity', 0).remove(); window.setTimeout(() => trailsRef.current.delete(d.id), 1300) })
    sim.on('tick', () => { allSel.attr('transform', (d) => `translate(${d.x},${d.y})`); trailsRef.current.forEach((line, id) => { const n = fresh.get(id); if (n) { line.attr('x2', n.x ?? 0); line.attr('y2', n.y ?? 0) } }) })
    if (reduce) { sim.alpha(0.1); for (let i = 0; i < 160; i += 1) sim.tick(); allSel.attr('transform', (d) => `translate(${d.x},${d.y})`) } else sim.alpha(0.95).restart()
    return undefined
  }, [pts, color])
  useEffect(() => { const svg = svgRef.current; if (!svg) return; select(svg).selectAll<SVGGElement, Pt>('g.dot').classed('is-shown', (d) => d.id === shownId).classed('is-dim', (d) => Boolean(highlight) && !highlight!.has(d.id)).classed('is-evidence', (d) => Boolean(highlight) && highlight!.has(d.id)) }, [shownId, pts, highlight])
  useEffect(() => () => { simRef.current?.stop(); simRef.current = null; nodesRef.current = new Map() }, [])

  const fmt = (v: number, d = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(d)}`
  return (
    <section className="lf-card am" aria-label="Attack map">
      <header className="am__head">
        <h3>{title}{compact || live ? <small className="am__live">LIVE</small> : null}</h3>
        <span className="fbd-tabs">
          <button type="button" className={teamId === away.id ? 'is-on' : ''} style={{ ['--tc' as string]: visibleColor(away, '#3fd8c4') }} onClick={() => { setTeamId(away.id); setFollow(false) }}>{away.abbr} offense</button>
          <button type="button" className={teamId === home.id ? 'is-on' : ''} style={{ ['--tc' as string]: visibleColor(home, '#ffb547') }} onClick={() => { setTeamId(home.id); setFollow(false) }}>{home.abbr} offense</button>
        </span>
        <span className="fb-seg am__seg" role="group" aria-label="Play type">{(['pass', 'run', 'both'] as const).map((m) => <button key={m} type="button" className={mode === m ? 'is-on' : ''} onClick={() => setMode(m)}>{m[0].toUpperCase() + m.slice(1)}</button>)}</span>
        {compact ? null : <span className="fb-seg am__seg" role="group" aria-label="Colour by"><button type="button" className={lens === 'usage' ? 'is-on' : ''} onClick={() => setLens('usage')}>Usage</button><button type="button" className={lens === 'epa' ? 'is-on' : ''} onClick={() => setLens('epa')}>Results</button>{check ? <button type="button" className={lens === 'model' ? 'is-on' : ''} onClick={() => { setLens('model'); setMode('both') }} title="Where the model expected this offense to go, against where it went">vs Model</button> : null}</span>}
      </header>
      <div className={`am__body${compact ? ' am__body--compact' : ''}`}>
        <div className="am__wrap">
          <svg ref={svgRef} className="am__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${team.abbr} passes and runs by landing spot against ${other.abbr}`}>
            <defs><filter id="am-blur" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="22" /></filter></defs>
            <rect x="0" y="0" width={W} height={H} rx="12" fill="#0b2a1c" />
            <g className="am__heat" filter="url(#am-blur)" opacity={lens === 'model' ? 0.22 : 0.62}>
              {blobs.map((b) => <circle key={b.id} cx={b.x} cy={b.y} r={34 + Math.min(26, b.n * 4)} fill={lens === 'usage' ? HEAT(Math.min(1, b.n / maxBlob)) : heat(Math.max(-0.7, Math.min(0.7, b.epa)))} />)}
            </g>
            {ZONES.filter((z) => (mode === 'run' ? z.id.startsWith('run') : mode === 'pass' ? !z.id.startsWith('run') : true)).map((z) => {
              const s = zones.get(z.id)
              const v = check?.byId.get(z.id)
              const fill = lens === 'model' ? (v && check!.n ? diverge(Math.max(-0.15, Math.min(0.15, v.diff))) : 'transparent') : !s || s.n < 2 ? 'transparent' : lens === 'usage' ? color : heat(Math.max(-0.7, Math.min(0.7, s.epa / s.n)))
              const op = lens === 'model' ? (v && check!.n ? 0.18 + Math.min(0.5, Math.abs(v.diff) * 3) : 0) : !s || s.n < 2 ? 0 : lens === 'usage' ? 0.08 + 0.42 * (s.n / maxN) : 0.3
              const sp = snapProbs?.m.get(z.id)
              const focus = focusZones?.includes(z.id)
              return (
                <g key={z.id}>
                  <rect x={X(z.x0)} y={Y(z.y0)} width={X(z.x1) - X(z.x0)} height={Y(z.y1) - Y(z.y0)} fill={fill} opacity={op} style={{ transition: 'fill .6s, opacity .6s' }} stroke="rgba(255,255,255,.1)" />
                  {lens === 'model' && v && check!.n ? <text x={X(z.x0) + 6} y={Y(z.y0) + 15} className="am__zone">{z.name} · {Math.round(v.act * 100)}% <tspan className="am__exp">vs {Math.round(v.exp * 100)}% expected</tspan></text>
                    : s ? <text x={X(z.x0) + 6} y={Y(z.y0) + 15} className="am__zone">{z.name} · {s.n < 3 ? `${s.n} (thin)` : `${s.comp}/${s.n} · ${s.yards} yds · ${fmt(s.epa / s.n, 2)}`}</text> : <text x={X(z.x0) + 6} y={Y(z.y0) + 15} className="am__zone am__zone--empty">{z.name}</text>}
                  {sp != null && sp >= 0.005 ? <text x={X(z.x1) - 6} y={Y(z.y1) - 8} textAnchor="end" className={`am__snap${snapProbs?.topId === z.id ? ' is-top' : ''}`}>{snapProbs?.topId === z.id ? 'MODEL ' : ''}{Math.round(sp * 100)}%</text> : null}
                  {snapProbs?.topId === z.id ? <rect x={X(z.x0) + 2} y={Y(z.y0) + 2} width={X(z.x1) - X(z.x0) - 4} height={Y(z.y1) - Y(z.y0) - 4} rx="6" className="am__top" /> : null}
                  {focus ? <rect x={X(z.x0) + 1} y={Y(z.y0) + 1} width={X(z.x1) - X(z.x0) - 2} height={Y(z.y1) - Y(z.y0) - 2} rx="6" className="am__focus" /> : null}
                </g>
              )
            })}
            {[-10, 0, 10, 20, 30, 40].map((yd) => <g key={yd}><line x1={X(yd)} x2={X(yd)} y1={Y(-27)} y2={Y(27)} stroke={yd === 0 ? '#4aa8ff' : 'rgba(255,255,255,.18)'} strokeWidth={yd === 0 ? 3 : 1} strokeDasharray={yd === 0 ? undefined : '2 8'} /><text x={X(yd)} y={Y(-27) - 6} textAnchor="middle" className="am__tick">{yd === 0 ? 'LINE' : yd > 0 ? `+${yd}` : yd}</text></g>)}
            <text x={X(-11.5)} y={Y(-24)} className="am__tick">{team.abbr} left</text>
            <text x={X(-11.5)} y={Y(26)} className="am__tick">{team.abbr} right</text>
            <g className="trails" />
            <g className="dots" />
            <circle cx={QB.x} cy={QB.y} r="7" fill={color} stroke="#fff" strokeWidth="2" />
            <text x={QB.x} y={QB.y + 20} textAnchor="middle" className="am__tick">QB</text>
          </svg>
          {lens === 'model' ? <div className="am__legend am__legend--model" aria-hidden="true"><span>Less than the model expected</span><i /><span>More than expected</span></div> : <div className="am__legend" aria-hidden="true"><span>Low volume</span><i /><span>High volume</span></div>}
          {snapProbs ? <p className="am__key"><span className="am__key-top" /> model&apos;s top call for the snap on the field · <span className="am__key-ring" /> what actually happened</p> : null}
          {hover ? <div className="fb-attack__tip" style={{ left: `${Math.min(68, Math.max(2, (hover.x / (svgRef.current?.getBoundingClientRect().width || W)) * 100 - 12))}%`, top: `${Math.max(2, (hover.y / (svgRef.current?.getBoundingClientRect().height || H)) * 100 + 5)}%` }}><b>{hover.p.label} · {hover.p.clock}</b><span>{hover.p.text}</span><em>{hover.p.yards >= 0 ? '+' : ''}{hover.p.yards} yds{hover.p.epa != null ? ` · ${fmt(hover.p.epa, 2)} expected points` : ''}</em></div> : null}
        </div>
        <aside className="am__side">
          {check && check.calls ? <div className="am__hit" title="Before each snap the model leaned run or pass; this is how often the offense did what it leaned."><b>{check.hits}/{check.calls}</b><span>run-or-pass calls the model got right this game ({Math.round((check.hits / check.calls) * 100)}%)</span></div> : null}
          {lens === 'model' && check ? (
            <>
              <h4>BIGGEST SURPRISES</h4>
              <ol>{[...check.rows].sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff)).slice(0, 3).map((r, i) => <li key={r.id}><span className="am__rank">{i + 1}</span><span className="am__name">{r.name}<small>{Math.round(r.act * 100)}% of snaps · model {Math.round(r.exp * 100)}%</small></span><b className={r.diff >= 0 ? 'is-up' : 'is-down'}>{r.diff >= 0 ? '+' : ''}{Math.round(r.diff * 100)}</b></li>)}</ol>
            </>
          ) : null}
          {lens === 'model' ? null : <h4>MOST TARGETED AREAS</h4>}
          {lens === 'model' ? null : <ol>
            {top.length ? top.map(([id, s], i) => { const z = ZONES.find((x) => x.id === id)!; return <li key={id}><span className="am__rank">{i + 1}</span><span className="am__name">{z.name}<small>{s.comp}/{s.n} · {s.yards} yds · {fmt(s.epa / Math.max(1, s.n), 2)} EPA/snap</small></span><b>{denom ? Math.round((s.n / denom) * 100) : 0}%</b></li> }) : <li className="lf-muted">No {mode === 'both' ? 'plays' : mode === 'pass' ? 'passes' : 'runs'} charted yet.</li>}
          </ol>}
          <p className="lf-muted lf-tiny" title="Depth is air yards; sides come from ESPN's text. Zones under 3 snaps are marked thin.">{passN} passes · {runN} runs · {lens === 'usage' ? 'glow = share of snaps' : 'glow = expected points per snap'} · click a dot to replay it</p>
        </aside>
      </div>
    </section>
  )
}
