/**
 * The umpire's view: the strike zone as the plate umpire sees it, with every
 * pitch of the at-bat where it crossed the plate (MLB tracking: pX, pZ), its
 * speed and type, the call, and the batter's box. When the at-bat ends with a
 * ball in play the final ball flies off toward where it was hit. Display only.
 */
import { useEffect, useRef, useState } from 'react'
import { hitSprayAngle, trajectoryWord, type AtBat, type HitEvent, type PitchEvent } from './liveFieldData'

const W = 420
const H = 520
const CX = 210
const GROUND = 470
const SCALE = 100
const clampX = (x: number) => Math.min(W - 16, Math.max(16, x))
const clampY = (y: number) => Math.min(H - 12, Math.max(16, y))
const px = (p: PitchEvent) => clampX(CX + (p.pX ?? 0) * SCALE)
const py = (p: PitchEvent) => clampY(GROUND - (p.pZ ?? 2.5) * SCALE)

function callClass(p: PitchEvent): string {
  const c = (p.call ?? '').toLowerCase()
  if (p.inPlay || c.includes('in play')) return 'play'
  if (c.includes('swinging') || c.includes('missed')) return 'swing'
  if (c.includes('foul')) return 'foul'
  if (p.isStrike || c.includes('strike')) return 'strike'
  if (p.isBall || c.includes('ball')) return 'ball'
  return 'other'
}

function Batter({ side }: { side: string | null }) {
  const right = side === 'L' // catcher's view: a right-handed batter stands on the left
  const x = right ? 345 : 75
  const dir = right ? -1 : 1
  return (
    <g className="lf-ump__batter" aria-hidden="true">
      <circle cx={x} cy={150} r={24} />
      <path d={`M ${x - 30} 185 Q ${x} 170 ${x + 30} 185 L ${x + 26} 300 L ${x - 26} 300 Z`} />
      <path d={`M ${x - 22} 300 L ${x - 30} 462 L ${x - 6} 462 L ${x} 330 L ${x + 6} 462 L ${x + 30} 462 L ${x + 22} 300 Z`} />
      <line x1={x + dir * 18} y1={190} x2={x + dir * 52} y2={70} className="lf-ump__bat" />
    </g>
  )
}

export function UmpireView({ atBat, hit }: { atBat: AtBat | null | undefined; hit: HitEvent | null | undefined }) {
  const pitches = atBat?.pitches ?? []
  const last = pitches[pitches.length - 1]
  const top = last?.szTop ?? 3.4
  const bot = last?.szBot ?? 1.6
  const zx0 = CX - 0.708 * SCALE
  const zx1 = CX + 0.708 * SCALE
  const zy0 = GROUND - top * SCALE
  const zy1 = GROUND - bot * SCALE
  const flies = Boolean(atBat?.complete && hit && hit.atBatIndex === atBat.atBatIndex && last?.inPlay)

  // Replay the flight only for a hit this view has not shown yet (or on demand).
  const [shown, setShown] = useState<string | null>(null)
  const seen = useRef<string | null>(null)
  useEffect(() => {
    if (!flies || !hit || seen.current === hit.key) return
    seen.current = hit.key
    setShown(hit.key)
  }, [flies, hit])
  const replay = () => {
    if (!hit) return
    setShown(null)
    window.setTimeout(() => setShown(hit.key), 60)
  }

  let flight: { d: string; ex: number; ey: number } | null = null
  if (flies && last && hit) {
    const sx = px(last)
    const sy = py(last)
    const ang = hitSprayAngle(hit) ?? 0
    const traj = hit.trajectory ?? ''
    const ex = clampX(CX + Math.sin(ang) * 230)
    const ey = /fly|popup|pop/.test(traj) ? 60 : /line/.test(traj) ? 170 : 330
    const peak = /fly|popup|pop/.test(traj) ? 150 : /line/.test(traj) ? 40 : 0
    flight = { d: `M ${sx} ${sy} Q ${(sx + ex) / 2} ${Math.min(sy, ey) - peak} ${ex} ${ey}`, ex, ey }
  }

  return (
    <section className="lf-card lf-ump" aria-label="Umpire's view">
      <header className="lf-ump__head">
        <h3>Umpire's view</h3>
        <span className="lf-muted lf-small">
          {atBat ? `${atBat.batter ?? 'Batter'} · count ${last?.balls ?? 0}-${last?.strikes ?? 0}${atBat.complete ? ' · at-bat over' : ''}` : 'waiting for a pitch'}
        </span>
        {flies ? <button type="button" className="lf-chipbtn lf-ump__replay" onClick={replay}>↻ Replay</button> : null}
      </header>
      <svg className="lf-ump__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Pitch locations: ${pitches.length} pitches this at-bat`}>
        <defs>
          <linearGradient id="lf-ump-bg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#0f2740" />
            <stop offset="0.82" stopColor="#102b46" />
            <stop offset="1" stopColor="#0a1a2a" />
          </linearGradient>
        </defs>
        <rect width={W} height={H} fill="url(#lf-ump-bg)" rx="14" />
        {/* dirt, plate and batter's boxes */}
        <ellipse cx={CX} cy={GROUND + 18} rx={250} ry={46} className="lf-ump__dirt" />
        <path d={`M ${CX - 71} ${GROUND + 6} L ${CX + 71} ${GROUND + 6} L ${CX + 71} ${GROUND + 20} L ${CX} ${GROUND + 34} L ${CX - 71} ${GROUND + 20} Z`} className="lf-ump__plate" />
        <rect x={14} y={GROUND - 8} width={CX - 71 - 40} height={42} className="lf-ump__box" />
        <rect x={CX + 71 + 40} y={GROUND - 8} width={W - 14 - (CX + 71 + 40)} height={42} className="lf-ump__box" />
        <Batter side={atBat?.bat_side ?? null} />
        {/* the zone */}
        <rect x={zx0} y={zy0} width={zx1 - zx0} height={zy1 - zy0} className="lf-ump__zone" />
        {[1, 2].map((i) => <line key={`v${i}`} x1={zx0 + ((zx1 - zx0) * i) / 3} x2={zx0 + ((zx1 - zx0) * i) / 3} y1={zy0} y2={zy1} className="lf-ump__grid" />)}
        {[1, 2].map((i) => <line key={`h${i}`} y1={zy0 + ((zy1 - zy0) * i) / 3} y2={zy0 + ((zy1 - zy0) * i) / 3} x1={zx0} x2={zx1} className="lf-ump__grid" />)}
        {/* pitches */}
        {pitches.map((p) => {
          const isLast = p === last
          const cls = callClass(p)
          return (
            <g key={`${atBat?.atBatIndex}-${p.n}`} className={`lf-pitch lf-pitch--${cls}${isLast ? ' is-last' : ''}`} style={{ transformOrigin: `${px(p)}px ${py(p)}px` }}>
              {isLast ? <circle cx={px(p)} cy={py(p)} r={20} className="lf-pitch__ring" /> : null}
              <circle cx={px(p)} cy={py(p)} r={12} className="lf-pitch__dot" />
              <text x={px(p)} y={py(p) + 4} textAnchor="middle" className="lf-pitch__n">{p.n}</text>
            </g>
          )
        })}
        {flight && shown ? (
          <g key={shown} className="lf-ump__flight">
            <path d={flight.d} className="lf-ump__trail" pathLength={1} />
            <circle r={12} className="lf-ump__ball">
              <animateMotion dur="1.5s" fill="freeze" path={flight.d} calcMode="spline" keyTimes="0;1" keySplines="0.2 0.6 0.4 1" />
              <animate attributeName="r" values="12;3" dur="1.5s" fill="freeze" />
            </circle>
            <text x={flight.ex} y={Math.max(24, flight.ey - 14)} textAnchor="middle" className="lf-ump__label">
              {trajectoryWord(hit?.trajectory)}{hit?.speed ? ` · ${hit.speed} mph` : ''}
            </text>
          </g>
        ) : null}
        {!pitches.length ? <text x={CX} y={GROUND - 200} textAnchor="middle" className="lf-ump__empty">First pitch ahead</text> : null}
        <text x={CX} y={H - 8} textAnchor="middle" className="lf-ump__cap">catcher's / umpire's view · 17-inch plate</text>
      </svg>
      <ol className="lf-ump__list">
        {pitches.slice(-6).reverse().map((p) => (
          <li key={`${atBat?.atBatIndex}-${p.n}`} className={`lf-ump__row lf-pitch--${callClass(p)}`}>
            <span className="lf-ump__n">{p.n}</span>
            <span className="lf-ump__type">{p.type ?? 'Pitch'}</span>
            <span className="lf-ump__speed">{p.speed ? `${p.speed.toFixed(1)} mph` : ''}</span>
            <span className="lf-ump__call">{p.call ?? ''}</span>
          </li>
        ))}
      </ol>
      {atBat?.complete && atBat.description ? <p className="lf-ump__result"><b>{atBat.event}.</b> {atBat.description}</p> : null}
    </section>
  )
}
