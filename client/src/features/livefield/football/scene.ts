/**
 * The football scene: a camera behind and above the quarterback, the field
 * projected in perspective, and a timeline that moves 22 player icons and the
 * ball through one play.
 *
 * Coordinates: x = yards to the offence's right of centre, d = yards downfield
 * of the line of scrimmage (negative = behind it), z = height in yards.
 * Every play is a RECONSTRUCTION from the play-by-play text (formation, the
 * target's side and depth, yards, result). Individual player positions are
 * illustrative: there is no player-tracking data behind them.
 */
import type { ParsedPlay } from './playParser'

export const VB_W = 1000
export const VB_H = 640
/** The broadcast (All-22) frame: the same 1000 across, 280 tall (the live frame is always this shape; the QB view scrolls inside it). */
export const VB_H_SIDE = 340
const F = 430
const FV = 255
const HORIZON = 96

/** Camera behind and above the quarterback. A deep play pulls it back and up so the whole route stays in frame. */
export interface Camera { back: number; h: number; kind?: 'qb' | 'side'; d0?: number }
export const DEFAULT_CAM: Camera = { back: 21, h: 24 }
/** The broadcast camera: high on the near sideline at midfield, the whole field in frame, the offense driving left to right. `los` is the offense's own yard line. */
export function sideCamera(los: number, maxD = 10): Camera {
  // pan with the action like a real broadcast camera: centre a little downfield of the line, but never show past the end lines by much
  const centre = Math.max(40, Math.min(60, los + Math.max(4, Math.min(16, maxD / 2))))
  return { kind: 'side', back: 187, h: 45, d0: centre - los }
}
export function cameraFor(maxD: number): Camera {
  const extra = Math.max(0, maxD - 30)
  return { back: DEFAULT_CAM.back + extra * 0.55, h: DEFAULT_CAM.h + extra * 1.0 }
}

export function project(x: number, d: number, z = 0, cam: Camera = DEFAULT_CAM): { px: number; py: number; s: number } {
  if (cam.kind === 'side') {
    // broadcast camera: high on the near sideline, offense driving left to right
    // near sideline 160 yards from the lens, far sideline 213; 1.25x zoom about the field's centre line keeps nearly the whole field
    const Z = 1.25
    const depthS = Math.max(40, x + cam.back)
    const py0 = -655 + (150528 - 1600 * z) / depthS
    return { px: 500 + (1450 * Z * (d - (cam.d0 ?? 0))) / depthS, py: 192 + Z * (py0 - 150), s: (1450 * Z) / depthS }
  }
  const depth = Math.max(4, d + cam.back)
  return { px: 500 + (F * x) / depth, py: HORIZON + (FV * (cam.h - z)) / depth, s: F / depth }
}

export interface Frame { t: number; x: number; d: number; z?: number }
export interface Actor { id: string; side: 'off' | 'def'; role: string; label: string; key?: boolean; name?: string | null; frames: Frame[] }
export interface Marker { t: number; x: number; d: number; text: string; tone: 'good' | 'bad' | 'neutral' | 'flag'; ring?: boolean }
export interface Scene { actors: Actor[]; ball: Frame[]; markers: Marker[]; duration: number; kind: ParsedPlay['kind']; formationAt: number; snapAt: number; cam: Camera; maxD: number }

const T_SET = 1.0
const T_SNAP = 1.8
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
const dist = (a: { x: number; d: number }, b: { x: number; d: number }) => Math.hypot(a.x - b.x, a.d - b.d)

const smooth = (u: number) => u * u * (3 - 2 * u)

export function sample(frames: readonly Frame[], t: number): { x: number; d: number; z: number } {
  if (!frames.length) return { x: 0, d: 0, z: 0 }
  if (t <= frames[0].t) return { x: frames[0].x, d: frames[0].d, z: frames[0].z ?? 0 }
  const last = frames[frames.length - 1]
  if (t >= last.t) return { x: last.x, d: last.d, z: last.z ?? 0 }
  let i = 0
  while (i < frames.length - 1 && frames[i + 1].t < t) i += 1
  const a = frames[i]
  const b = frames[i + 1]
  const u = b.t === a.t ? 1 : smooth((t - a.t) / (b.t - a.t))
  return { x: a.x + (b.x - a.x) * u, d: a.d + (b.d - a.d) * u, z: (a.z ?? 0) + ((b.z ?? 0) - (a.z ?? 0)) * u }
}

/** Linear motion along points at a speed (yards per second); returns the finish time. */
function runPath(frames: Frame[], t0: number, pts: ReadonlyArray<readonly [number, number]>, speed: number): number {
  let t = t0
  let prev = frames.length ? frames[frames.length - 1] : { x: pts[0][0], d: pts[0][1], t: t0 }
  if (frames.length === 0 || frames[frames.length - 1].t < t0) frames.push({ t: t0, x: prev.x, d: prev.d })
  for (const [x, d] of pts) {
    const len = Math.hypot(x - prev.x, d - prev.d)
    if (len < 0.01) continue
    t += len / speed
    frames.push({ t, x, d })
    prev = { t, x, d }
  }
  return t
}

const hold = (frames: Frame[], t: number) => {
  const last = frames[frames.length - 1]
  if (last && last.t < t) frames.push({ t, x: last.x, d: last.d, z: last.z })
}

interface Spot { role: string; label: string; side: 'off' | 'def'; x: number; d: number }

function formation(shotgun: boolean, gapPlay: boolean): Spot[] {
  const off: Spot[] = [
    // spacing is schematic and wider than real splits, so 22 players stay readable from the broadcast camera
    ...[-6, -3, 0, 3, 6].map((x, i): Spot => ({ role: `OL${i + 1}`, label: 'OL', side: 'off', x, d: -0.7 })),
    { role: 'TE', label: 'TE', side: 'off', x: 9.5, d: -0.7 },
    { role: 'WR1', label: 'WR', side: 'off', x: -19, d: 0 },
    { role: 'WR2', label: 'WR', side: 'off', x: 19, d: 0 },
    { role: 'WR3', label: 'WR', side: 'off', x: -13, d: -1.3 },
    { role: 'QB', label: 'QB', side: 'off', x: 0, d: shotgun ? -5.2 : -1.9 },
    { role: 'RB', label: 'RB', side: 'off', x: shotgun ? 3.6 : 0, d: shotgun ? -5.4 : gapPlay ? -6.8 : -6.4 },
  ]
  const def: Spot[] = [
    ...[-7.2, -2.4, 2.4, 7.2].map((x, i): Spot => ({ role: `DL${i + 1}`, label: 'DL', side: 'def', x, d: 1.4 })),
    ...[-10, 0, 10].map((x, i): Spot => ({ role: `LB${i + 1}`, label: 'LB', side: 'def', x, d: 5.2 })),
    { role: 'CB1', label: 'CB', side: 'def', x: -19, d: 6.5 },
    { role: 'CB2', label: 'CB', side: 'def', x: 19, d: 6.5 },
    { role: 'S1', label: 'S', side: 'def', x: -10, d: 14 },
    { role: 'S2', label: 'S', side: 'def', x: 10, d: 14 },
  ]
  return [...off, ...def]
}

const GAPS: Record<string, number> = { 'left end': -11, 'left tackle': -6.8, 'left guard': -3, 'up the middle': 0, middle: 0, 'right guard': 3, 'right tackle': 6.8, 'right end': 11, left: -8, right: 8 }

export interface SceneInput {
  parsed: ParsedPlay
  yac: number | null
  /** Names for the key actors (display only). */
  names?: { passer?: string | null; target?: string | null; rusher?: string | null }
}

export function buildScene(input: SceneInput): Scene {
  const { parsed: p } = input
  const shotgun = p.formation === 'shotgun' || p.formation === 'pistol' || p.formation === 'no-huddle' || p.kind === 'pass' || p.kind === 'incomplete' || p.kind === 'interception' || p.kind === 'sack'
    ? p.formation !== 'under-center'
    : false
  const gapPlay = p.kind === 'rush'
  const spots = formation(shotgun, gapPlay)
  const actors: Actor[] = spots.map((s, i) => {
    const sx = s.side === 'off' ? s.x * 0.5 + (i % 2 ? 22 : -22) : s.x + (i % 2 ? 16 : -16)
    const sd = s.side === 'off' ? -9 : s.d + 9
    return { id: s.role + s.side, side: s.side, role: s.role, label: s.label, frames: [{ t: 0, x: sx, d: sd }, { t: T_SET, x: s.x, d: s.d }] }
  })
  const by = (role: string) => actors.find((a) => a.role === role)!
  const at = (role: string) => { const s = spots.find((q) => q.role === role)!; return { x: s.x, d: s.d } }
  for (const a of actors) hold(a.frames, T_SNAP)

  const markers: Marker[] = []
  const ball: Frame[] = [{ t: 0, x: 0, d: -0.2, z: 0.4 }, { t: T_SNAP, x: 0, d: -0.2, z: 0.4 }]
  const qb = by('QB')
  const qb0 = at('QB')
  const qbDrop = shotgun ? { x: 0, d: -7 } : { x: 0, d: -6.5 }
  let end = T_SNAP + 2.2

  // Snap: ball to the quarterback; linemen fire out.
  const snapDone = T_SNAP + 0.3
  ball.push({ t: snapDone, x: qb0.x, d: qb0.d, z: 1.1 })
  const olSet = (adv: number) => { for (let i = 1; i <= 5; i += 1) { const a = by(`OL${i}`); runPath(a.frames, T_SNAP, [[at(`OL${i}`).x, at(`OL${i}`).d + adv]], 3) } }
  const dlRush = (to: { x: number; d: number }, dur: number) => {
    for (let i = 1; i <= 4; i += 1) {
      const a = by(`DL${i}`)
      const s = at(`DL${i}`)
      runPath(a.frames, T_SNAP, [[s.x + (to.x - s.x) * 0.55, s.d + (to.d - s.d) * 0.5]], Math.hypot((to.x - s.x) * 0.55, (to.d - s.d) * 0.5) / dur)
    }
  }
  const converge = (tStart: number, spot: { x: number; d: number }, tEnd: number, roles: string[]) => {
    for (const r of roles) {
      const a = by(r)
      const last = a.frames[a.frames.length - 1]
      const tt = Math.max(tStart, last.t)
      runPath(a.frames, tt, [[spot.x + (r.charCodeAt(r.length - 1) % 3 - 1) * 1.1, spot.d + 0.8]], Math.max(3, Math.hypot(spot.x - last.x, spot.d - last.d) / Math.max(0.4, tEnd - tt)))
    }
  }
  const ALL_DEF = ['DL1', 'DL2', 'DL3', 'DL4', 'LB1', 'LB2', 'LB3', 'CB1', 'CB2', 'S1', 'S2']

  if (p.kind === 'pass' || p.kind === 'incomplete' || p.kind === 'interception') {
    const yac = p.kind === 'pass' ? Math.max(0, input.yac ?? Math.round(Math.max(0, p.yards) * 0.25)) : 0
    const air = p.kind === 'pass' ? clamp(p.yards - yac, 2, 45) : p.depth === 'deep' ? 22 : p.depth === 'short' ? 8 : 12
    const side = p.side ?? (p.depth === 'short' ? 'middle' : 'right')
    const tgtRole = side === 'left' ? 'WR1' : side === 'right' ? 'WR2' : p.depth === 'deep' ? 'WR2' : air < 9 ? 'TE' : 'WR3'
    const tgt = by(tgtRole)
    tgt.key = true
    tgt.name = input.names?.target ?? null
    qb.key = true
    qb.name = input.names?.passer ?? null
    const t0 = at(tgtRole)
    const xc = side === 'left' ? -12 : side === 'right' ? 12 : 1.5
    const catchSpot = { x: xc, d: air }
    const pts: Array<[number, number]> = p.depth === 'deep' || air > 16
      ? [[t0.x + (xc - t0.x) * 0.25, air * 0.55], [xc, air]]
      : [[t0.x, air * 0.7], [xc, air]]
    const tCatch = runPath(tgt.frames, T_SNAP + 0.15, pts, 8.2)
    olSet(-0.8)
    dlRush({ x: 0, d: -3.8 }, 1.5)
    // Other receivers run, the back checks.
    for (const r of ['WR1', 'WR2', 'WR3', 'TE'].filter((r) => r !== tgtRole)) {
      const s = at(r)
      runPath(by(r).frames, T_SNAP + 0.15, [[s.x * 0.9, r === 'TE' ? 6 : 13]], 7.5)
    }
    runPath(by('RB').frames, T_SNAP + 0.2, [[at('RB').x + 3.5, -2.5]], 4)
    // Quarterback drops back, then throws.
    runPath(qb.frames, T_SNAP + 0.3, [[qbDrop.x, qbDrop.d]], 5)
    const flight = 0.55 + dist(qbDrop, catchSpot) * 0.028
    const tRel = Math.max(snapDone + 0.9, tCatch - flight)
    for (let u = 0; u <= 1.0001; u += 0.25) {
      const tt = snapDone + (tRel - snapDone) * u
      const q = sample(qb.frames, tt)
      ball.push({ t: tt, x: q.x, d: q.d, z: 1.4 })
    }
    const arcH = 2.2 + dist(qbDrop, catchSpot) * 0.1
    const tArrive = tRel + flight
    for (let i = 1; i <= 8; i += 1) {
      const u = i / 8
      ball.push({ t: tRel + flight * u, x: qbDrop.x + (catchSpot.x - qbDrop.x) * u, d: qbDrop.d + (catchSpot.d - qbDrop.d) * u, z: 1.4 + arcH * 4 * u * (1 - u) + (1 - 1.4) * u })
    }
    const db = side === 'middle' ? by('S1') : by(side === 'left' ? 'CB1' : 'CB2')
    if (p.kind === 'pass') {
      const dEnd = p.yards
      const xEnd = xc * (air > 10 ? 0.85 : 1) + (xc < 0 ? -1 : 1) * yac * 0.15
      const tEnd = runPath(tgt.frames, tArrive, [[xEnd, dEnd]], Math.max(2.5, 7.5 - yac * 0.08))
      const tEndFinal = Math.max(tEnd, tArrive + 0.5)
      for (let tt = tArrive; tt <= tEndFinal + 0.001; tt += 0.2) { const q = sample(tgt.frames, tt); ball.push({ t: tt, x: q.x, d: q.d, z: 1.0 }) }
      runPath(db.frames, T_SNAP + 0.2, [[catchSpot.x + (xc < 0 ? -1.5 : 1.5), catchSpot.d + 1.2]], 8)
      converge(tArrive, { x: xEnd, d: dEnd }, tEndFinal, ['LB1', 'LB2', 'LB3', 'S1', 'S2', 'CB1', 'CB2'])
      markers.push({ t: tEndFinal, x: xEnd, d: dEnd, text: p.touchdown ? 'TOUCHDOWN' : `+${p.yards}`, tone: 'good', ring: true })
      end = tEndFinal + 1.6
    } else if (p.kind === 'incomplete') {
      const tFall = tArrive + 0.6
      ball.push({ t: tFall, x: xc + 1.6, d: air + 2, z: 0 })
      ball.push({ t: tFall + 0.5, x: xc + 2.2, d: air + 3, z: 0 })
      runPath(db.frames, T_SNAP + 0.2, [[catchSpot.x + (xc < 0 ? -1.2 : 1.2), catchSpot.d + 0.5]], 8)
      converge(tArrive, catchSpot, tArrive + 0.8, ['LB1', 'LB2', 'LB3', 'S1', 'S2'])
      markers.push({ t: tArrive + 0.5, x: xc, d: air, text: 'INCOMPLETE', tone: 'neutral' })
      end = tFall + 1.8
    } else {
      // Interception: the defender arrives at the catch point, then returns it.
      runPath(db.frames, T_SNAP + 0.2, [[catchSpot.x, catchSpot.d]], 9)
      const back = runPath(db.frames, tArrive, [[catchSpot.x * 0.7, catchSpot.d - 7]], 7)
      for (let tt = tArrive; tt <= back + 0.001; tt += 0.2) { const q = sample(db.frames, tt); ball.push({ t: tt, x: q.x, d: q.d, z: 1.0 }) }
      markers.push({ t: tArrive, x: catchSpot.x, d: catchSpot.d, text: 'INTERCEPTED', tone: 'bad', ring: true })
      end = back + 1.6
    }
    return finish(actors, ball, markers, end, p.kind)
  }

  if (p.kind === 'sack') {
    qb.key = true
    qb.name = input.names?.passer ?? null
    const dSack = Math.min(-2, p.yards || -6)
    olSet(-0.8)
    runPath(qb.frames, T_SNAP + 0.3, [[qbDrop.x, qbDrop.d], [0.6, dSack - 1]], 4.5)
    const rusher = by('DL2')
    const s = at('DL2')
    const tHit = runPath(rusher.frames, T_SNAP, [[s.x * 0.5, s.d - 3], [0.6, dSack - 0.6]], 5)
    dlRush({ x: 0, d: -3 }, 1.3)
    runPath(by('DL2').frames, tHit, [[0.6, dSack - 0.6]], 3)
    for (let tt = snapDone; tt <= tHit + 0.3; tt += 0.2) { const q = sample(qb.frames, tt); ball.push({ t: tt, x: q.x, d: q.d, z: 1.2 }) }
    markers.push({ t: tHit + 0.2, x: 0.6, d: dSack, text: `SACK ${p.yards}`, tone: 'bad', ring: true })
    end = tHit + 1.8
    return finish(actors, ball, markers, end, p.kind)
  }

  if (p.kind === 'rush') {
    const gx = p.gap && GAPS[p.gap] !== undefined ? GAPS[p.gap] : 0
    const isQb = /\bQB\b/.test(p.rusher ?? '') && false
    const back = isQb ? qb : by('RB')
    back.key = true
    back.name = input.names?.rusher ?? null
    const s0 = isQb ? qb0 : at('RB')
    const yds = p.yards
    const dEnd = Math.max(yds, -3)
    const handoffT = T_SNAP + 0.65
    olSet(1.2)
    dlRush({ x: gx * 0.5, d: 1.5 }, 1.0)
    const pts: Array<[number, number]> = yds <= 1
      ? [[gx * 0.6, -1.5], [gx, Math.max(dEnd, 0.2)]]
      : [[gx * 0.6, -1.5], [gx, 1.2], [gx * 1.15, dEnd]]
    const tEnd = runPath(back.frames, handoffT, pts, 7)
    hold(back.frames, tEnd)
    runPath(qb.frames, T_SNAP + 0.3, [[qb0.x - 0.6, qb0.d - 0.4]], 3)
    ball.push({ t: handoffT, x: s0.x, d: s0.d, z: 1.0 })
    for (let tt = handoffT; tt <= tEnd + 0.001; tt += 0.2) { const q = sample(back.frames, tt); ball.push({ t: tt, x: q.x, d: q.d, z: 0.9 }) }
    const xEnd = gx * 1.15
    converge(handoffT + 0.2, { x: xEnd, d: dEnd }, tEnd + 0.3, ['LB1', 'LB2', 'LB3', 'S1', 'S2', 'CB1', 'CB2'])
    markers.push({ t: tEnd + 0.1, x: xEnd, d: dEnd, text: p.touchdown ? 'TOUCHDOWN' : yds >= 0 ? `+${yds}` : `${yds}`, tone: yds >= 0 ? 'good' : 'bad', ring: true })
    end = tEnd + 1.8
    return finish(actors, ball, markers, end, p.kind)
  }

  if (p.kind === 'punt' || p.kind === 'kickoff' || p.kind === 'fieldgoal') {
    const len = p.kind === 'fieldgoal' ? 28 : clamp(Math.abs(p.yards) || 45, 20, 70)
    const flight = p.kind === 'fieldgoal' ? 1.5 : 2.4
    const hang = p.kind === 'fieldgoal' ? 7 : 22
    const start = { x: 0, d: -8 }
    for (let i = 1; i <= 12; i += 1) {
      const u = i / 12
      ball.push({ t: snapDone + 0.5 + flight * u, x: start.x + (p.kind === 'fieldgoal' ? (p.good === false ? 3 : 0) * u : 0), d: start.d + (len + 8) * u, z: 1 + hang * 4 * u * (1 - u) })
    }
    for (const r of ['WR1', 'WR2']) runPath(by(r).frames, T_SNAP + 0.4, [[at(r).x, 22]], 7)
    olSet(0.6)
    markers.push({ t: snapDone + 0.5 + flight, x: 0, d: Math.min(40, len), text: p.kind === 'fieldgoal' ? (p.good ? 'FIELD GOAL IS GOOD' : 'NO GOOD') : p.kind === 'punt' ? `PUNT ${Math.round(len)}` : 'KICKOFF', tone: p.kind === 'fieldgoal' ? (p.good ? 'good' : 'bad') : 'neutral', ring: true })
    end = snapDone + 0.5 + flight + 1.6
    return finish(actors, ball, markers, end, p.kind)
  }

  // Penalty and anything else: line up, then show the flag.
  markers.push({ t: T_SNAP + 0.4, x: 0, d: 2, text: p.kind === 'penalty' ? 'FLAG' : p.label.toUpperCase(), tone: p.kind === 'penalty' ? 'flag' : 'neutral', ring: p.kind === 'penalty' })
  end = T_SNAP + 2.4
  return finish(actors, ball, markers, end, p.kind)
}

function finish(actors: Actor[], ball: Frame[], markers: Marker[], end: number, kind: ParsedPlay['kind']): Scene {
  for (const a of actors) hold(a.frames, end)
  ball.sort((x, y) => x.t - y.t)
  hold(ball, end)
  let maxD = 12
  for (const a of actors) for (const f of a.frames) maxD = Math.max(maxD, f.d)
  for (const f of ball) maxD = Math.max(maxD, f.d)
  for (const m of markers) maxD = Math.max(maxD, m.d)
  return { actors, ball, markers, duration: end, kind, formationAt: T_SET, snapAt: T_SNAP, cam: cameraFor(maxD), maxD }
}
