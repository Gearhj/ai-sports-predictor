/**
 * The ballpark.
 *
 * A high broadcast angle from behind home plate, drawn as flat SVG with a
 * gentle vertical compression that reads as depth without pretending to be a
 * 3D render. Everything here is scenery: grass, dirt, mound, foul lines, wall,
 * poles, dugouts. No element encodes a simulated value except through the
 * layers composited on top of it.
 *
 * The coordinate system is shared with the runner layer, so both agree on
 * exactly where the bases are.
 */

/** Field geometry, exported so the runner layer cannot drift from the art. */
export const FIELD = {
  viewBox: '0 0 1000 720',
  /** Broadcast crop: the drawn area kept when the container is wider than tall. */
  frame: '0 96 1000 596',
  home: { x: 500, y: 618 },
  first: { x: 683, y: 470 },
  second: { x: 500, y: 322 },
  third: { x: 317, y: 470 },
  mound: { x: 500, y: 470 },
} as const

export type BaseKey = 'home' | 'first' | 'second' | 'third'

/** Where a runner token sits for a given base. 0/4 are both home plate. */
export function basePoint(base: 0 | 1 | 2 | 3 | 4): { x: number; y: number } {
  switch (base) {
    case 1: return FIELD.first
    case 2: return FIELD.second
    case 3: return FIELD.third
    default: return FIELD.home
  }
}

export function BallparkField({ accentAway, accentHome }: {
  accentAway: string
  accentHome: string
}) {
  return (
    <>
      <defs>
        <radialGradient id="bp-sky" cx="50%" cy="18%" r="85%">
          <stop offset="0%" stopColor="#1b3a5c" />
          <stop offset="55%" stopColor="#0e2138" />
          <stop offset="100%" stopColor="#07111f" />
        </radialGradient>
        <linearGradient id="bp-grass-deep" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#256f46" />
          <stop offset="100%" stopColor="#12522f" />
        </linearGradient>
        <linearGradient id="bp-dirt" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#9a6f4a" />
          <stop offset="100%" stopColor="#7c553a" />
        </linearGradient>
        <radialGradient id="bp-lights" cx="50%" cy="30%" r="70%">
          <stop offset="0%" stopColor="#ffffff" stopOpacity=".16" />
          <stop offset="55%" stopColor="#ffffff" stopOpacity=".05" />
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
        </radialGradient>
        <clipPath id="bp-fair">
          <path d={FAIR_TERRITORY} />
        </clipPath>
      </defs>

      {/* Stadium bowl and night sky */}
      <rect x="0" y="0" width="1000" height="720" fill="url(#bp-sky)" />
      <path d={STANDS} className="bp-stands" />
      <ellipse cx="500" cy="300" rx="620" ry="330" fill="url(#bp-lights)" />

      {/* Warning track sits under the grass so the grass edge cuts into it */}
      <path d={WARNING_TRACK} className="bp-warning" />
      <path d={FAIR_TERRITORY} fill="url(#bp-grass-deep)" />
      <path d={FOUL_GRASS_LEFT} className="bp-foul-grass" />
      <path d={FOUL_GRASS_RIGHT} className="bp-foul-grass" />

      {/* Restrained mowing stripes, clipped to fair territory */}
      <g clipPath="url(#bp-fair)" className="bp-mow">
        {MOW_STRIPES.map((stripe, index) => (
          <path key={stripe} d={stripe} opacity={index % 2 === 0 ? 0.075 : 0.038} />
        ))}
      </g>

      {/* Infield dirt: arc across the front, cut out for the grass wedge */}
      <path d={INFIELD_DIRT} fill="url(#bp-dirt)" />
      <path d={INFIELD_GRASS} fill="url(#bp-grass-deep)" className="bp-infield-grass" />
      <path d={HOME_CIRCLE} fill="url(#bp-dirt)" />

      {/* Base paths */}
      <path d={BASE_PATHS} className="bp-basepath" />

      {/* Mound and rubber */}
      <ellipse cx={FIELD.mound.x} cy={FIELD.mound.y} rx="46" ry="30" fill="url(#bp-dirt)" />
      <rect x={FIELD.mound.x - 13} y={FIELD.mound.y - 5} width="26" height="6" rx="2" className="bp-rubber" />

      {/* Foul lines and poles */}
      <path d="M500 618 L120 246" className="bp-foul-line" />
      <path d="M500 618 L880 246" className="bp-foul-line" />
      <g className="bp-pole">
        <rect x="116" y="196" width="5" height="54" rx="2" />
        <rect x="879" y="196" width="5" height="54" rx="2" />
      </g>

      {/* Outfield wall */}
      <path d={OUTFIELD_WALL} className="bp-wall" />
      <path d={OUTFIELD_WALL} className="bp-wall-cap" />

      {/* Restrained distance markers on the wall */}
      <g className="bp-distance">
        <text x="196" y="278" textAnchor="middle">330</text>
        <text x="500" y="182" textAnchor="middle">400</text>
        <text x="804" y="278" textAnchor="middle">330</text>
      </g>

      {/* Dugout suggestions, tinted with each club's accent */}
      <path d="M188 556 q78 -34 150 -6 l-14 34 q-64 -24 -128 4 z" className="bp-dugout"
        style={{ fill: accentAway }} />
      <path d="M812 556 q-78 -34 -150 -6 l14 34 q64 -24 128 4 z" className="bp-dugout"
        style={{ fill: accentHome }} />

      {/* Batter's boxes and plate */}
      <g className="bp-boxes">
        <rect x="452" y="596" width="26" height="42" rx="2" />
        <rect x="522" y="596" width="26" height="42" rx="2" />
      </g>
      <path d="M492 612 h16 l5 8 -13 10 -13 -10 z" className="bp-plate" />

      {/* Bases. Rendered here so occupancy highlights sit exactly on the art. */}
      <g className="bp-bases">
        {(['first', 'second', 'third'] as const).map((base) => {
          const point = FIELD[base]
          return (
            <rect
              key={base}
              data-base={base}
              x={point.x - 11}
              y={point.y - 11}
              width="22"
              height="22"
              rx="2"
              transform={`rotate(45 ${point.x} ${point.y})`}
            />
          )
        })}
      </g>
    </>
  )
}

// -- geometry -------------------------------------------------------------

/** Fair territory: the wedge from home plate out to the wall arc. */
const FAIR_TERRITORY =
  'M500 618 L120 246 A470 470 0 0 1 880 246 Z'

const WARNING_TRACK =
  'M500 618 L104 232 A492 492 0 0 1 896 232 Z'

const FOUL_GRASS_LEFT = 'M500 618 L120 246 L52 300 L392 664 Z'
const FOUL_GRASS_RIGHT = 'M500 618 L880 246 L948 300 L608 664 Z'

const OUTFIELD_WALL =
  'M120 246 A470 470 0 0 1 880 246'

/**
 * Infield dirt: the skin around the base paths, closed by the classic arc
 * behind second base. Slightly larger than the base-path diamond so the bases
 * themselves sit on dirt rather than floating on grass.
 */
const INFIELD_DIRT =
  'M500 660 L727 470 Q500 258 273 470 Z'

/** The grass wedge inside the base paths. */
const INFIELD_GRASS =
  'M500 588 L634 470 L500 352 L366 470 Z'

const HOME_CIRCLE = 'M440 618 a60 44 0 0 1 120 0 a60 44 0 0 1 -120 0'

const BASE_PATHS =
  'M500 618 L683 470 L500 322 L317 470 Z'

const MOW_STRIPES = [
  'M500 618 L120 246 L214 246 L500 618 Z',
  'M500 618 L214 246 L308 246 L500 618 Z',
  'M500 618 L308 246 L402 246 L500 618 Z',
  'M500 618 L402 246 L500 246 L500 618 Z',
  'M500 618 L500 246 L598 246 L500 618 Z',
  'M500 618 L598 246 L692 246 L500 618 Z',
  'M500 618 L692 246 L786 246 L500 618 Z',
  'M500 618 L786 246 L880 246 L500 618 Z',
]

const STANDS =
  'M0 0 H1000 V300 q-260 -190 -500 -190 T0 300 Z'
