/**
 * LIVE PLAY VIEW: the broadcast camera on the near sideline (All-22) or the 3D view behind the quarterback.
 * 22 player cards with photos run into formation, the snap, the route, the ball, the result. Overlays: the
 * quarterback's line, the projected play result (league average for this situation), and the live win
 * probability. Every play is a reconstruction from the play-by-play text and the field says so.
 */
import { useEffect, useMemo, useRef } from 'react'
import { buildStarters, driveSummary, hex, parseFbPlay, type FbLive, type FbPlay, type FbTeam } from './footballData'
import { findPlayer, type RosterPlayer } from './playParser'
import { buildScene, DEFAULT_CAM, project, sample, sideCamera, VB_H, VB_H_SIDE, VB_W, type Scene } from './scene'
import { LABELS, type PlayClass } from './playcall'
import { roar, thud, whistle } from './fx'
import { FieldArt } from './FieldArt'
import type { Projection } from './situation'

export interface Impact { label: string; before: number | null; after: number | null; wpa: number | null; offHome: boolean; intensity: number; kind?: 'td' | 'pick' | 'fumble' | 'turnover' | 'fg' | 'sack' | 'big'; side?: 'off' | 'def' }
export interface Ghost { cls: PlayClass; p: number }
export interface WpCard { team: string; pct: number; delta: number | null }

const GHOST: Record<string, Array<[number, number]>> = {
  PASS_SL: [[-19, 0], [-19, 4], [-12, 8]], PASS_SM: [[9.5, -0.7], [5, 4], [1.5, 9]], PASS_SR: [[19, 0], [19, 4], [12, 8]],
  PASS_DL: [[-19, 0], [-20, 14], [-24, 27]], PASS_DM: [[-13, -1.3], [-5, 14], [1, 29]], PASS_DR: [[19, 0], [20, 14], [24, 27]],
  RUN_L: [[3.6, -5.4], [-5, -1.5], [-10, 7]], RUN_M: [[3.6, -5.4], [0.5, -1], [0, 8]], RUN_R: [[3.6, -5.4], [8, -1.5], [12, 7]],
  PUNT: [[0, -9], [0, 14], [0, 40]], FG: [[0, -8], [0, 14], [0, 36]], KNEEL: [[0, -2], [0, -4.5]],
}
const G = (p: RosterPlayer | null, cat: string) => (p?.stats?.[cat] ?? null)
export function statLine(p: RosterPlayer | null, role: string): string | null {
  if (!p) return null
  if (role === 'QB') { const s = G(p, 'passing'); return s ? `${s['C/ATT']} · ${s.YDS} yds · ${s.TD} TD · ${s.INT} INT` : null }
  if (role === 'RB') { const s = G(p, 'rushing'); return s ? `${s.CAR} car · ${s.YDS} yds · ${s.AVG} avg · ${s.TD} TD` : null }
  if (role.startsWith('WR') || role === 'TE') { const s = G(p, 'receiving'); return s ? `${s.REC} rec · ${s.YDS} yds · ${s.TGTS ?? '–'} tgts · ${s.TD} TD` : null }
  const s = G(p, 'defensive')
  return s ? `${s.TOT} tackles · ${s.SACKS} sacks · ${s.TFL ?? '0'} TFL · ${s.PD ?? '0'} PD` : null
}

function Football() {
  return (
    <svg viewBox="0 0 60 36" width="100%" height="100%" aria-hidden="true">
      <defs><radialGradient id="fb-leather" cx="38%" cy="32%" r="75%"><stop offset="0" stopColor="#b8651f" /><stop offset="1" stopColor="#6e3510" /></radialGradient></defs>
      <path d="M2 18 C 10 2, 50 2, 58 18 C 50 34, 10 34, 2 18 Z" fill="url(#fb-leather)" stroke="#3b1c08" strokeWidth="1.4" />
      <path d="M14 7 C 12 12, 12 24, 14 29 M46 7 C 48 12, 48 24, 46 29" fill="none" stroke="#f4efe6" strokeWidth="2" />
      <line x1="19" y1="18" x2="41" y2="18" stroke="#f4efe6" strokeWidth="1.6" />
      {[22, 26, 30, 34, 38].map((x) => <line key={x} x1={x} y1="14.5" x2={x} y2="21.5" stroke="#f4efe6" strokeWidth="1.5" />)}
    </svg>
  )
}

interface Props {
  view: 'qb' | 'side'
  fb: FbLive
  play: FbPlay | null
  nonce: number
  slow: boolean
  ghosts?: Ghost[]
  actualCls?: PlayClass | null
  burst?: boolean
  impact?: Impact | null
  sound?: boolean
  projected?: Projection | null
  wpCard?: WpCard | null
}

export function FootballField({ view, fb, play, nonce, slow, ghosts = [], actualCls = null, burst = false, impact = null, sound = false, projected = null, wpCard = null }: Props) {
  const teams = fb.teams!
  const FH = view === 'side' ? VB_H_SIDE : VB_H
  // The live feed hands back a fresh copy of the same play on every refresh: key everything on the play's
  // content, not the object, so the animation runs once per play instead of restarting every 8 seconds.
  const playKey = play ? `${play.id}|${play.type ?? ''}|${play.text ?? ''}|${play.yards ?? ''}|${play.yac ?? ''}` : null
  const parsed = useMemo(() => (play ? parseFbPlay(play) : null), [playKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const offId = play?.offense ?? play?.start.team ?? fb.situation?.possession ?? null
  const off: FbTeam = offId && offId === teams.away.id ? teams.away : offId && offId === teams.home.id ? teams.home : teams.away
  const def: FbTeam = off.id === teams.home.id ? teams.away : teams.home
  const away = teams.away
  const home = teams.home
  const offColor = hex(off.color, '#c0392b')
  const defColor = hex(def.color, '#34495e')
  const losRaw = play?.start.yardLine ?? fb.situation?.yardLine ?? 25
  const los = off.id === teams.home.id ? losRaw : 100 - losRaw
  const toGo = play?.start.distance ?? fb.situation?.distance ?? 10
  const roster = fb.players
  const offS = useMemo(() => buildStarters(fb, off.id), [fb, off.id])
  const defS = useMemo(() => buildStarters(fb, def.id), [fb, def.id])
  const picks = useMemo(() => (parsed ? { pass: findPlayer(parsed.passer, roster, off.id), tgt: findPlayer(parsed.target, roster, off.id), rsh: findPlayer(parsed.rusher, roster, off.id) } : null), [parsed, roster, off.id])
  const scene: Scene | null = useMemo(() => (parsed && play ? buildScene({ parsed, yac: play.yac, names: { passer: parsed.passer, target: parsed.target, rusher: parsed.rusher } }) : null), [parsed]) // eslint-disable-line react-hooks/exhaustive-deps
  const cam = useMemo(() => (view === 'side' ? sideCamera(los, scene?.maxD ?? 10) : (scene?.cam ?? DEFAULT_CAM)), [view, los, scene])
  const impactRef = useRef(impact)
  impactRef.current = impact
  const impactKey = impact ? `${impact.label}|${impact.before ?? ''}|${impact.after ?? ''}` : null
  const drive = useMemo(() => driveSummary(fb.plays, play?.id ?? null), [fb.plays, play])
  const qbLeader = fb.leaders?.find((l) => l.team === off.id && l.cat === 'passingYards')
  const qbPlayer: RosterPlayer | null = picks?.pass ?? offS.starters.QB ?? null
  const uid = `fa${off.id}`

  const frameRef = useRef<HTMLDivElement | null>(null)
  const actorEls = useRef<Map<string, HTMLDivElement>>(new Map())
  const ballEl = useRef<HTMLDivElement | null>(null)
  const shadowEl = useRef<HTMLDivElement | null>(null)
  const markerEls = useRef<Map<number, HTMLDivElement>>(new Map())
  const stateEl = useRef<HTMLSpanElement | null>(null)
  const ghostEls = useRef<Map<string, SVGGElement>>(new Map())
  const ballTrail = useRef<SVGPolylineElement | null>(null)
  const routeTrail = useRef<SVGPolylineElement | null>(null)
  const awayNum = useRef<HTMLElement | null>(null)
  const homeNum = useRef<HTMLElement | null>(null)
  const wpaNum = useRef<HTMLElement | null>(null)
  const gaugeBar = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!scene) return undefined
    const frame = frameRef.current
    if (!frame) return undefined
    const c = cam
    let raf = 0
    const t0 = performance.now()
    const rate = slow ? 0.45 : 1
    const paint = (t: number) => {
      const u = frame.clientWidth / VB_W
      for (const a of scene.actors) {
        const el = actorEls.current.get(a.id)
        if (!el) continue
        const p = sample(a.frames, t)
        const q = project(p.x, p.d, 0, c)
        // broadcast view: linemen are small team dots (like All-22 film), skill players photos, the key players bigger
        const line = /^(OL|DL)/.test(a.role)
        const size = view === 'side' ? (line ? Math.max(11, q.s * u * 1.35) : a.key || a.role === 'QB' ? Math.max(26, q.s * u * 3.3) : Math.max(18, q.s * u * 2.35)) : Math.max(22, q.s * u * (a.key ? 3.1 : 2.4))
        el.style.left = `${(q.px / VB_W) * 100}%`
        el.style.top = `${(q.py / FH) * 100}%`
        el.style.setProperty('--fb-size', `${size}px`)
        el.style.zIndex = String(Math.round(q.py))
      }
      const b = sample(scene.ball, t)
      const b2 = sample(scene.ball, t + 0.06)
      const bq = project(b.x, b.d, b.z, c)
      const bq2 = project(b2.x, b2.d, b2.z, c)
      const gq = project(b.x, b.d, 0, c)
      if (ballEl.current) {
        const bs = Math.max(22, bq.s * u * (view === 'side' ? 2.8 : 1.8))
        const dx = bq2.px - bq.px
        const dy = bq2.py - bq.py
        const ang = Math.hypot(dx, dy) > 0.15 ? (Math.atan2(dy, dx) * 180) / Math.PI : -28
        ballEl.current.style.left = `${(bq.px / VB_W) * 100}%`
        ballEl.current.style.top = `${(bq.py / FH) * 100}%`
        ballEl.current.style.width = `${bs}px`
        ballEl.current.style.height = `${bs * 0.6}px`
        ballEl.current.style.transform = `translate(-50%, -50%) rotate(${ang}deg)`
      }
      if (shadowEl.current) {
        shadowEl.current.style.left = `${(gq.px / VB_W) * 100}%`
        shadowEl.current.style.top = `${(gq.py / FH) * 100}%`
        shadowEl.current.style.width = `${Math.max(8, gq.s * u * (1.5 - Math.min(0.8, b.z * 0.08)))}px`
        shadowEl.current.style.opacity = String(Math.max(0.15, 0.55 - b.z * 0.05))
      }
      if (ballTrail.current || routeTrail.current) {
        const pts: string[] = []
        for (let tt = scene.snapAt; tt <= t + 1e-6; tt += 0.05) { const bb = sample(scene.ball, tt); const qq = project(bb.x, bb.d, bb.z, c); pts.push(`${qq.px.toFixed(1)},${qq.py.toFixed(1)}`) }
        ballTrail.current?.setAttribute('points', pts.join(' '))
        const tgt = scene.actors.find((a) => a.key && a.role !== 'QB')
        if (tgt && routeTrail.current) {
          const rp: string[] = []
          for (let tt = scene.snapAt; tt <= t + 1e-6; tt += 0.08) { const pp = sample(tgt.frames, tt); const qq = project(pp.x, pp.d, 0, c); rp.push(`${qq.px.toFixed(1)},${qq.py.toFixed(1)}`) }
          routeTrail.current.setAttribute('points', rp.join(' '))
        }
      }
      ghostEls.current.forEach((el, cls) => {
        const called = actualCls != null && cls === actualCls
        const fade = actualCls == null ? 1 : t < scene.snapAt ? 1 : Math.max(0, 1 - (t - scene.snapAt) / 0.7)
        el.style.opacity = String(called ? 1 : fade)
        el.dataset.called = called && t >= scene.snapAt ? '1' : '0'
      })
      scene.markers.forEach((m, i) => { const el = markerEls.current.get(i); if (el) el.style.opacity = t >= m.t ? '1' : '0' })
      if (stateEl.current) stateEl.current.textContent = t < scene.snapAt ? 'LINING UP' : t < scene.duration ? 'LIVE PLAY' : 'PLAY OVER'
    }
    const tick = (now: number) => {
      const t = Math.min(scene.duration, ((now - t0) / 1000) * rate)
      paint(t)
      if (t < scene.duration) raf = requestAnimationFrame(tick)
    }
    const freeze = Number(new URLSearchParams(window.location.search).get('freeze'))
    if (Number.isFinite(freeze) && freeze > 0) { paint(Math.min(freeze, scene.duration)); return undefined }
    paint(0)
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [scene, nonce, slow, actualCls, cam])

  useEffect(() => {
    if (!scene) return undefined
    if (Number(new URLSearchParams(window.location.search).get('freeze')) > 0) return undefined
    const rate = slow ? 0.45 : 1
    const timers: number[] = []
    const intervals: number[] = []
    const at = (sec: number, fn: () => void) => timers.push(window.setTimeout(fn, Math.max(0, (sec / rate) * 1000)))
    const impact = impactRef.current
    if (sound) { at(scene.snapAt, thud); if (impact) at(scene.duration - 1.2, () => roar(impact.intensity)); if (scene.kind !== 'nonplay') at(scene.duration, whistle) }
    if (impact && impact.before != null && impact.after != null) {
      const { before, after } = impact
      at(scene.duration - 1.2, () => {
        const start = performance.now()
        const id = window.setInterval(() => {
          const u = Math.min(1, (performance.now() - start) / 1100)
          const e = 1 - (1 - u) * (1 - u) * (1 - u)
          const h = before + (after - before) * e
          if (homeNum.current) homeNum.current.textContent = `${Math.round(h)}%`
          if (awayNum.current) awayNum.current.textContent = `${Math.round(100 - h)}%`
          if (gaugeBar.current) gaugeBar.current.style.width = `${100 - h}%`
          if (wpaNum.current && impact.wpa != null) wpaNum.current.textContent = `${impact.wpa >= 0 ? '+' : ''}${(impact.wpa * e).toFixed(1)}`
          if (u >= 1) window.clearInterval(id)
        }, 30)
        intervals.push(id)
      })
    }
    return () => { timers.forEach((id) => window.clearTimeout(id)); intervals.forEach((id) => window.clearInterval(id)) }
  }, [scene, nonce, slow, sound, impactKey, view]) // eslint-disable-line react-hooks/exhaustive-deps

  const photoFor = (role: string, side: 'off' | 'def', key: boolean | undefined): RosterPlayer | null => {
    if (side === 'off') {
      if (role === 'QB') return picks?.pass ?? offS.starters.QB ?? null
      if (key && role !== 'RB') return picks?.tgt ?? offS.starters[role] ?? null
      if (role === 'RB' && parsed?.kind === 'rush') return picks?.rsh ?? offS.starters.RB ?? null
      return offS.starters[role] ?? null
    }
    return defS.starters[role] ?? null
  }
  const delayS = scene ? Math.max(0, (scene.duration - 1.3) / (slow ? 0.45 : 1)) : 0
  const endQ = scene?.markers.length ? (() => { const m = scene.markers[scene.markers.length - 1]; return project(m.x, m.d, 0, cam) })() : { px: 500, py: 340 }

  return (
    <div className="fb-wrap">
    <div className={`fb-frame fb-frame--${view}`} ref={frameRef} style={{ aspectRatio: `${VB_W} / ${VB_H_SIDE}` }}>
      <div key={`s${nonce}`} className={`fb-stage${impact ? ' is-punch' : ''}`} style={impact && scene ? { transformOrigin: `${(endQ.px / VB_W) * 100}% ${(endQ.py / FH) * 100}%`, animationDelay: `${delayS}s` } : undefined}>
        <div key={`f${nonce}`} className={`fb-fieldwrap${burst ? ' is-shaking' : ''}`} style={burst && scene ? { animationDelay: `${delayS}s` } : undefined}>
          <FieldArt los={los} toGo={toGo} offColor={offColor} defColor={defColor} offName={off.short || off.name} defName={def.short || def.name} cam={cam} uid={uid} />
        </div>
        {ghosts.length ? (
          <svg className="fb-ghosts" viewBox={`0 0 ${VB_W} ${FH}`} preserveAspectRatio="xMidYMid slice" aria-hidden="true">
            {/* prediction lanes: slim dashed paths with a small fixed arrowhead and a % tag; kicks and kneels have no lane (see the chip) */}
            {ghosts.filter((g) => GHOST[g.cls] && LABELS[g.cls].group !== 'kick').slice(0, view === 'side' ? 3 : 5).map((g, rank) => {
              const pts = GHOST[g.cls].map(([x, d]) => project(x, d, 0, cam))
              const end = pts[pts.length - 1]
              const prev = pts[pts.length - 2]
              const len = Math.hypot(end.px - prev.px, end.py - prev.py) || 1
              const ux = (end.px - prev.px) / len; const uy = (end.py - prev.py) / len
              const w = Math.max(2, Math.min(5.5, 1.5 + g.p * 8))
              const hs = Math.min(11, 6 + w)
              const bx = end.px - ux * hs; const by = end.py - uy * hs
              const head = `${end.px.toFixed(1)},${end.py.toFixed(1)} ${(bx - uy * hs * 0.6).toFixed(1)},${(by + ux * hs * 0.6).toFixed(1)} ${(bx + uy * hs * 0.6).toFixed(1)},${(by - ux * hs * 0.6).toFixed(1)}`
              const label = `${Math.round(g.p * 100)}%`
              const lw = 12 + label.length * 7.5
              const ly = end.py - hs - 14
              return (
                <g key={g.cls} className={`fb-ghost fb-ghost--${LABELS[g.cls].group}${rank === 0 ? ' is-top' : ''}`} data-called="0" ref={(el) => { if (el) ghostEls.current.set(g.cls, el); else ghostEls.current.delete(g.cls) }}>
                  <polyline points={pts.slice(0, -1).concat([{ px: bx, py: by, s: 0 }]).map((q) => `${q.px.toFixed(1)},${q.py.toFixed(1)}`).join(' ')} fill="none" strokeWidth={w} strokeLinecap="round" strokeLinejoin="round" />
                  <polygon points={head} />
                  <rect className="fb-ghost__pill" x={end.px - lw / 2} y={ly - 9} width={lw} height="18" rx="9" />
                  <text x={end.px} y={ly + 4} textAnchor="middle">{label}</text>
                  <text className="fb-ghost__tag" x={end.px} y={ly - 14} textAnchor="middle">CALLED</text>
                </g>
              )
            })}
          </svg>
        ) : null}
        {scene ? (
          <>
            <div className="fb-shadow" ref={shadowEl} />
            {scene.actors.map((a) => {
              const p = photoFor(a.role, a.side, a.key)
              const color = a.side === 'off' ? offColor : defColor
              return (
                <div key={`${nonce}-${a.id}`} ref={(el) => { if (el) actorEls.current.set(a.id, el); else actorEls.current.delete(a.id) }} className={`fb-actor fb-actor--${a.side}${a.key ? ' is-key' : ''}${/^(OL|DL)/.test(a.role) ? ' is-line' : ''}${a.role === 'QB' ? ' is-qb' : ''}`} style={{ ['--fb-c' as string]: color }} title={p ? `${p.first} ${p.last}` : a.role}>
                  <span className="fb-actor__face">{p ? <img src={p.headshot} alt="" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} /> : null}<i>{a.label}</i></span>
                  <b className="fb-actor__pos">{a.label}{a.key && p ? ` · ${p.last}` : ''}</b>
                  <div className="fb-actor__card" role="tooltip">
                    <b>{p ? `${p.first} ${p.last}` : a.role}</b>
                    <span>{a.label}{p?.jersey ? ` · #${p.jersey}` : ''}{p?.pos && p.pos !== a.label ? ` · ${p.pos}` : ''} · {a.side === 'off' ? off.abbr : def.abbr}</span>
                    <em>{statLine(p, a.role) ?? (p ? 'No stats yet this game' : 'Starter not identified')}</em>
                  </div>
                </div>
              )
            })}
            <div className="fb-ball" ref={ballEl} aria-hidden="true"><Football /></div>
            {burst && scene.markers.length ? (
              <div className="fb-burst" aria-hidden="true" style={{ left: `${(endQ.px / VB_W) * 100}%`, top: `${(endQ.py / FH) * 100}%`, animationDelay: `${delayS}s` }}>
                {Array.from({ length: 28 }, (_, i) => <i key={i} style={{ ['--a' as string]: `${(i / 28) * 360}deg`, ['--r' as string]: `${70 + (i % 5) * 26}px`, ['--c' as string]: ['#ffd76a', '#3fd8c4', '#ff7a83', '#fff', '#4bd996'][i % 5], animationDelay: `${delayS}s` }} />)}
              </div>
            ) : null}
            {scene.markers.map((m, i) => {
              const q = project(m.x, m.d, 0, cam)
              return <div key={`${nonce}-m${i}`} ref={(el) => { if (el) markerEls.current.set(i, el); else markerEls.current.delete(i) }} className={`fb-marker fb-marker--${m.tone}${m.ring ? ' has-ring' : ''}`} style={{ left: `${(q.px / VB_W) * 100}%`, top: `${(q.py / FH) * 100}%`, opacity: 0 }}><span>{m.text}</span></div>
            })}
          </>
        ) : null}
        <svg className="fb-trails" viewBox={`0 0 ${VB_W} ${FH}`} preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <polyline ref={routeTrail} className="fb-trail fb-trail--route" fill="none" />
          <polyline ref={ballTrail} className="fb-trail fb-trail--ball" fill="none" />
        </svg>
      </div>
      {impact && scene ? (
        <div className={`fb-impact fb-impact--${impact.kind ?? 'big'}`} key={`i${nonce}`} style={{ animationDelay: `${delayS}s` }}>
          <div className="fb-impact__banner" style={{ background: `linear-gradient(100deg, ${impact.side === 'def' ? defColor : offColor}, ${impact.side === 'def' ? defColor : offColor}cc 60%, transparent)` }}><b>{impact.label}</b><span>{impact.side === 'def' ? `${def.name} take it away` : off.name}</span></div>
          {impact.kind === 'pick' || impact.kind === 'fumble' ? <div className="fb-steal" aria-hidden="true" style={{ ['--dc' as string]: defColor }}><i className="fb-steal__flash" /><b className="fb-steal__stamp">{impact.kind === 'pick' ? 'PICKED OFF!' : 'BALL OUT!'}</b><span className="fb-steal__ball"><Football /></span>{Array.from({ length: 14 }, (_, k) => <u key={k} style={{ ['--a' as string]: `${k * 26}deg` }} />)}</div> : null}
          {impact.before != null && impact.after != null ? (
            <div className="fb-impact__gauge" aria-label="Win probability">
              <b className="a">{away.abbr} <em ref={awayNum}>{Math.round(100 - impact.before)}%</em></b>
              <i><u ref={gaugeBar} style={{ width: `${100 - impact.before}%`, background: hex(away.color, '#3fd8c4') }} /></i>
              <b className="h"><em ref={homeNum}>{Math.round(impact.before)}%</em> {home.abbr}</b>
              {impact.wpa != null ? <strong className={impact.wpa >= 0 ? 'is-up' : 'is-down'}><em ref={wpaNum}>0.0</em><small>win prob. for {off.abbr}</small></strong> : null}
            </div>
          ) : null}
        </div>
      ) : null}
      <span className="fb-badge" title="ESPN publishes plays, not player tracking. Direction, depth, yards and result are real; where each player stands is drawn.">RECONSTRUCTED · positions schematic</span>
      <span className="fb-state"><span ref={stateEl} /></span>
      {ghosts.some((g) => LABELS[g.cls].group === 'kick' && g.p >= 0.15) ? (
        <span className="fb-callchips">{ghosts.filter((g) => LABELS[g.cls].group === 'kick' && g.p >= 0.15).map((g) => <b key={g.cls} className={actualCls === g.cls ? 'is-called' : ''}>Model: {LABELS[g.cls].name.toLowerCase()} <i>{Math.round(g.p * 100)}%</i>{actualCls === g.cls ? ' ✓' : ''}</b>)}</span>
      ) : null}
      {!play ? <div className="fb-ov fb-ov--empty">{fb.status?.state === 'pre' ? 'Kickoff has not happened yet. Plays appear as ESPN logs them.' : 'Waiting for the first scrimmage play.'}</div> : null}
    </div>
      {play ? (
        <div className="fb-ovs">
          <div className="fb-ov fb-ov--qb">
            <h4>{off.abbr} QUARTERBACK</h4>
            <div className="fb-ov__who">
              {qbPlayer ? <img src={qbPlayer.headshot} alt="" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} /> : null}
              <span><b>{qbPlayer ? `${qbPlayer.first} ${qbPlayer.last}` : 'QB'}</b><small>QB{qbPlayer?.jersey ? ` #${qbPlayer.jersey}` : ''}</small></span>
            </div>
            <div className="fb-ov__line">{(qbLeader?.line ?? statLine(qbPlayer, 'QB') ?? 'no throws yet').split(/\s*[,·]\s*/).map((s) => <span key={s}>{s}</span>)}</div>
            {drive ? <small>This drive: {drive.plays} plays · {drive.yards} yds</small> : null}
          </div>
          <div className="fb-ov fb-ov--proj">
            <h4>PROJECTED PLAY RESULT</h4>
            {projected ? (
              <>
                <b className={projected.yards >= 0 ? 'is-up' : 'is-down'}>{projected.yards >= 0 ? '+' : ''}{projected.yards.toFixed(1)} <small>expected yards</small></b>
                <span>{Math.round(projected.explosive * 100)}% chance of explosive (15+) · {Math.round(projected.success * 100)}% success</span>
                {play.yards != null && parsed && ['pass', 'incomplete', 'interception', 'sack', 'rush'].includes(parsed.kind) ? <em>Actual: {play.yards >= 0 ? '+' : ''}{play.yards} yds{play.yards >= 15 ? ' · explosive' : ''}</em> : null}
                <small>League average for this down, distance and spot ({projected.n.toLocaleString()} snaps)</small>
              </>
            ) : <span className="lf-muted">Needs a down and distance.</span>}
          </div>
          {wpCard ? (
            <div className="fb-ov fb-ov--wp">
              <h4>{wpCard.team} WIN PROBABILITY</h4>
              <b>{Math.round(wpCard.pct * 100)}% {wpCard.delta != null ? <small className={wpCard.delta >= 0 ? 'is-up' : 'is-down'}>{wpCard.delta >= 0 ? '+' : ''}{Math.round(wpCard.delta)}%</small> : null}</b>
              <i><u style={{ width: `${wpCard.pct * 100}%`, background: offColor }} /></i>
              <small>ESPN live model, after this play</small>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
