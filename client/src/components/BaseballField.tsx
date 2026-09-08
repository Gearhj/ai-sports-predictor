/**
 * The baseball field: SVG geometry beneath a Canvas trace layer.
 *
 * The SVG draws the park (grass, mowing bands, warning track, infield dirt,
 * base paths, bases, mound, foul lines, batter's boxes). The Canvas above it
 * draws the outcome traces, because 20,000 curves must never become 20,000 DOM
 * nodes. Statistics are HTML overlays so they stay selectable and readable by
 * a screen reader.
 */
import { FIELD } from '../services/diamondTraces'

const { width: W, height: H, home, first, second, third, mound, fenceRadius, infieldRadius, foulAngle } = FIELD

function polar(radius: number, angle: number) {
  return { x: home.x + Math.sin(angle) * radius, y: home.y - Math.cos(angle) * radius }
}

/** Arc across fair territory at a fixed distance from home plate. */
function fairArc(radius: number): string {
  const a = polar(radius, -foulAngle)
  const b = polar(radius, foulAngle)
  return `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} A ${radius} ${radius} 0 0 1 ${b.x.toFixed(1)} ${b.y.toFixed(1)}`
}

/** Wedge of fair territory between two radii — used for the mowing bands. */
function fairBand(inner: number, outer: number): string {
  const oa = polar(outer, -foulAngle)
  const ob = polar(outer, foulAngle)
  const ia = polar(inner, foulAngle)
  const ib = polar(inner, -foulAngle)
  return [
    `M ${oa.x.toFixed(1)} ${oa.y.toFixed(1)}`,
    `A ${outer} ${outer} 0 0 1 ${ob.x.toFixed(1)} ${ob.y.toFixed(1)}`,
    `L ${ia.x.toFixed(1)} ${ia.y.toFixed(1)}`,
    `A ${inner} ${inner} 0 0 0 ${ib.x.toFixed(1)} ${ib.y.toFixed(1)}`,
    'Z',
  ].join(' ')
}

function base(x: number, y: number, size = 15) {
  return (
    <rect
      x={x - size / 2} y={y - size / 2} width={size} height={size}
      transform={`rotate(45 ${x} ${y})`}
      fill="#f2f6f8" stroke="#c3d3dd" strokeWidth="1"
    />
  )
}

const MOW_BANDS = [
  [infieldRadius, 262], [262, 328], [328, 394], [394, 460], [460, fenceRadius],
]

export function BaseballFieldSvg({ homeAccent, awayAccent }: { homeAccent: string; awayAccent: string }) {
  const foulLeft = polar(fenceRadius + 18, -foulAngle)
  const foulRight = polar(fenceRadius + 18, foulAngle)

  return (
    <svg
      className="field-svg"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Baseball field diagram. Simulated outcomes are drawn as traces from home plate into the outfield."
    >
      <defs>
        <linearGradient id="bf-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#08131f" />
          <stop offset="100%" stopColor="#0b1729" />
        </linearGradient>
        <radialGradient id="bf-grass" cx="50%" cy="88%" r="78%">
          <stop offset="0%" stopColor="#16412f" />
          <stop offset="60%" stopColor="#12361f" />
          <stop offset="100%" stopColor="#0d2a19" />
        </radialGradient>
        <linearGradient id="bf-dirt" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#7a5334" />
          <stop offset="100%" stopColor="#63412a" />
        </linearGradient>
        {/* Fair territory only — keeps grass, bands and traces off foul ground. */}
        <clipPath id="bf-fair">
          <path d={`M ${home.x} ${home.y} L ${polar(fenceRadius + 40, -foulAngle).x} ${polar(fenceRadius + 40, -foulAngle).y} A ${fenceRadius + 40} ${fenceRadius + 40} 0 0 1 ${polar(fenceRadius + 40, foulAngle).x} ${polar(fenceRadius + 40, foulAngle).y} Z`} />
        </clipPath>
      </defs>

      <rect width={W} height={H} fill="url(#bf-sky)" />

      {/* Outfield grass, bounded by the fence arc. */}
      <path
        d={`M ${home.x} ${home.y} L ${polar(fenceRadius, -foulAngle).x} ${polar(fenceRadius, -foulAngle).y} A ${fenceRadius} ${fenceRadius} 0 0 1 ${polar(fenceRadius, foulAngle).x} ${polar(fenceRadius, foulAngle).y} Z`}
        fill="url(#bf-grass)"
      />

      {/* Restrained mowing bands. */}
      <g clipPath="url(#bf-fair)" opacity="0.5">
        {MOW_BANDS.map(([inner, outer], index) => (
          index % 2 === 0
            ? <path key={inner} d={fairBand(inner, outer)} fill="#ffffff" opacity="0.028" />
            : null
        ))}
      </g>

      {/* Warning track just inside the fence. */}
      <g clipPath="url(#bf-fair)">
        <path d={fairBand(fenceRadius - 26, fenceRadius)} fill="#6d4a30" opacity="0.55" />
      </g>

      {/* Fence. */}
      <path d={fairArc(fenceRadius)} fill="none" stroke="#2f5470" strokeWidth="3" />

      {/* Distance markers — orientation aid, not a claim about the simulation. */}
      {[
        { angle: -foulAngle * 0.62, label: '330' },
        { angle: 0, label: '400' },
        { angle: foulAngle * 0.62, label: '330' },
      ].map(({ angle, label }) => {
        const p = polar(fenceRadius - 40, angle)
        return (
          <text key={label + angle} x={p.x} y={p.y} textAnchor="middle"
                fill="#7e98ad" fontSize="13" opacity="0.55">{label}</text>
        )
      })}

      {/* Infield dirt. */}
      <path
        d={`M ${home.x} ${home.y} L ${polar(infieldRadius, -foulAngle).x} ${polar(infieldRadius, -foulAngle).y} A ${infieldRadius} ${infieldRadius} 0 0 1 ${polar(infieldRadius, foulAngle).x} ${polar(infieldRadius, foulAngle).y} Z`}
        fill="url(#bf-dirt)" opacity="0.9"
      />
      {/* Infield grass inside the base paths. */}
      <polygon
        points={`${home.x},${home.y - 26} ${first.x - 20},${first.y} ${second.x},${second.y + 26} ${third.x + 20},${third.y}`}
        fill="#143a22" opacity="0.92"
      />

      {/* Base paths. */}
      <polygon
        points={`${home.x},${home.y} ${first.x},${first.y} ${second.x},${second.y} ${third.x},${third.y}`}
        fill="none" stroke="#d8c3a8" strokeWidth="3.5" opacity="0.55"
      />

      {/* Foul lines and poles. */}
      <line x1={home.x} y1={home.y} x2={foulLeft.x} y2={foulLeft.y} stroke="#e8eef2" strokeWidth="2" opacity="0.45" />
      <line x1={home.x} y1={home.y} x2={foulRight.x} y2={foulRight.y} stroke="#e8eef2" strokeWidth="2" opacity="0.45" />

      {/* Pitcher's mound and rubber. */}
      <circle cx={mound.x} cy={mound.y} r="26" fill="#7a5334" opacity="0.95" />
      <rect x={mound.x - 8} y={mound.y - 2.5} width="16" height="5" fill="#f2f6f8" opacity="0.85" />

      {/* Bases. */}
      {base(first.x, first.y)}
      {base(second.x, second.y)}
      {base(third.x, third.y)}

      {/* Batter's boxes and home plate. */}
      <rect x={home.x - 38} y={home.y - 16} width="22" height="32" fill="none" stroke="#e8eef2" strokeWidth="1.4" opacity="0.4" />
      <rect x={home.x + 16} y={home.y - 16} width="22" height="32" fill="none" stroke="#e8eef2" strokeWidth="1.4" opacity="0.4" />
      <polygon
        points={`${home.x - 8},${home.y - 6} ${home.x + 8},${home.y - 6} ${home.x + 8},${home.y + 2} ${home.x},${home.y + 10} ${home.x - 8},${home.y + 2}`}
        fill="#f2f6f8"
      />

      {/* Team-colour accents on the correct sides: away left, home right. */}
      <path d={fairBand(fenceRadius - 8, fenceRadius)} fill="none" />
      <line
        x1={polar(fenceRadius, -foulAngle).x} y1={polar(fenceRadius, -foulAngle).y}
        x2={polar(fenceRadius, -foulAngle * 0.28).x} y2={polar(fenceRadius, -foulAngle * 0.28).y}
        stroke={awayAccent} strokeWidth="4" opacity="0.5" strokeLinecap="round"
      />
      <line
        x1={polar(fenceRadius, foulAngle * 0.28).x} y1={polar(fenceRadius, foulAngle * 0.28).y}
        x2={polar(fenceRadius, foulAngle).x} y2={polar(fenceRadius, foulAngle).y}
        stroke={homeAccent} strokeWidth="4" opacity="0.5" strokeLinecap="round"
      />
    </svg>
  )
}
