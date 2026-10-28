/**
 * THE STEAM RADAR (Markets, plan page list): every game whose fair price moved, on a radar. Angle = start time on a
 * 24-hour ET dial; distance from the centre = how far the no-vig price moved (rings at 1, 2 and 3 points); colour =
 * sport. STEAM (most books moving the same way, 2+ points, inside two hours) pulses red. The beam sweeps every
 * 6 seconds and each blip flashes as it passes. Built from AISP's own price captures; no ticket or handle data.
 */
import type { CSSProperties } from 'react'
import { SPORT_COLOR, type MoverGame } from './insights'
import './radar.css'

const R = 140, C = 160, PERIOD = 6
export interface RadarItem { m: MoverGame; label: string; league: string; mover: string }

export function radarPoint(startsAt: string, move: number): { x: number; y: number; angle: number; r: number } {
  const h = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23' }).format(new Date(startsAt)))
  const min = new Date(startsAt).getUTCMinutes()
  const angle = ((h + min / 60) / 24) * 360 // 0° = midnight at the top, clockwise
  const r = Math.min(1, Math.abs(move) * 100 / 3.5) * R
  const rad = ((angle - 90) * Math.PI) / 180
  return { x: C + Math.cos(rad) * Math.max(10, r), y: C + Math.sin(rad) * Math.max(10, r), angle, r }
}

export function SteamRadar({ items }: { items: RadarItem[] }) {
  return (
    <figure className="sr" aria-label={`Steam radar: ${items.length} games moved, ${items.filter((i) => i.m.steam.length).length} with steam`}>
      <svg viewBox="0 0 320 320" className="sr__svg" role="img">
        <defs>
          <radialGradient id="sr-bg"><stop offset="0" stopColor="#0f2a22" /><stop offset="1" stopColor="#050b14" /></radialGradient>
          <linearGradient id="sr-beam" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stopColor="rgba(52,211,153,0)" /><stop offset="1" stopColor="rgba(52,211,153,.55)" /></linearGradient>
        </defs>
        <circle cx={C} cy={C} r={R + 8} fill="url(#sr-bg)" stroke="rgba(52,211,153,.35)" />
        {[1, 2, 3].map((pts) => <g key={pts}><circle cx={C} cy={C} r={(pts / 3.5) * R} fill="none" stroke="rgba(52,211,153,.18)" strokeDasharray="3 4" /><text x={C + 3} y={C - (pts / 3.5) * R - 3} className="sr__ring">{pts} pt</text></g>)}
        <line x1={C} y1={C - R} x2={C} y2={C + R} stroke="rgba(52,211,153,.12)" /><line x1={C - R} y1={C} x2={C + R} y2={C} stroke="rgba(52,211,153,.12)" />
        {[['12 AM', C, C - R + 14], ['6 AM', C + R - 18, C - 4], ['NOON', C, C + R - 6], ['6 PM', C - R + 18, C - 4]].map(([t, x, y]) => <text key={String(t)} x={Number(x)} y={Number(y)} textAnchor="middle" className="sr__hour">{t}</text>)}
        <g className="sr__sweep" style={{ animationDuration: `${PERIOD}s`, transformOrigin: `${C}px ${C}px` }}>
          <path d={`M ${C} ${C} L ${C} ${C - R - 6} A ${R + 6} ${R + 6} 0 0 1 ${C + (R + 6) * Math.sin(Math.PI / 5)} ${C - (R + 6) * Math.cos(Math.PI / 5)} Z`} fill="url(#sr-beam)" opacity=".55" />
          <line x1={C} y1={C} x2={C} y2={C - R - 6} stroke="#34d399" strokeWidth="1.5" />
        </g>
        {items.map(({ m, label, league, mover }) => {
          const p = radarPoint(m.starts_at, m.move)
          const steam = m.steam.length > 0
          const color = SPORT_COLOR[league] ?? '#94a3b8'
          return (
            <g key={m.game_id} className={`sr__blip${steam ? ' is-steam' : ''}`} style={{ '--d': `${(p.angle / 360) * PERIOD}s`, '--p': `${PERIOD}s` } as CSSProperties}>
              <title>{`${label} · ${league} · ${mover} ${(Math.abs(m.move) * 100).toFixed(1)} pts${steam ? ' · STEAM' : ''}`}</title>
              {steam ? <circle cx={p.x} cy={p.y} r="9" fill="none" stroke="#f43f5e" className="sr__ring-steam" /> : null}
              <circle cx={p.x} cy={p.y} r={steam ? 5.5 : 4} fill={steam ? '#f43f5e' : color} className="sr__dot" />
            </g>
          )
        })}
      </svg>
      <figcaption className="sr__cap">
        <b>The Steam Radar</b> · angle = start time (24-hour ET dial) · distance = how far the market&apos;s fair price moved · <span className="sr__steamword">red pulse = STEAM</span>. {items.length ? '' : 'No moves on the board yet; prices are bought near each game, so blips appear through the evening.'}
      </figcaption>
    </figure>
  )
}
