/**
 * The Play Geometry field.
 *
 * A separate, deliberately quieter diamond from the Broadcast Replay ballpark:
 * here the field is a spatial reference and the PATH LAYERS are the subject, so
 * the scenery is drawn low-contrast and the paths carry the brightness.
 *
 * The geometry uses the supplement's own declared coordinate system,
 * ASP_NORMALIZED_DIAMOND_V1: origin at home plate, +y along the
 * home-plate-to-centre-field axis, +x toward first base and right field,
 * dimensionless units with 1.0 the nominal centre-field reference, foul lines
 * at +/-45 degrees. `project` is the single place that maps it to SVG.
 */

export const GEOMETRY_VIEWBOX = '0 0 1000 780'

/** Home plate in SVG space. */
const HOME = { x: 500, y: 700 }
/** SVG length of one normalized unit along the centre-field axis. */
const UNIT = 620

/**
 * Normalized supplement coordinates to SVG coordinates.
 *
 * y is negated because the supplement's +y runs toward centre field while SVG
 * y grows downward. Nothing else is transformed: no scaling of x relative to y,
 * so a declared direction in degrees stays that direction on screen.
 */
export function project(point: { x: number; y: number }): { x: number; y: number } {
  return { x: HOME.x + point.x * UNIT, y: HOME.y - point.y * UNIT }
}

/** Where a runner token sits for each base state, in SVG space. */
export const BASE_POINTS: Record<string, { x: number; y: number }> = {
  BATTER_BOX: project({ x: 0, y: 0 }),
  HOME: project({ x: 0, y: 0 }),
  FIRST: project({ x: 0.2121, y: 0.2121 }),
  SECOND: project({ x: 0, y: 0.4243 }),
  THIRD: project({ x: -0.2121, y: 0.2121 }),
  // A retired runner leaves the field: drawn as a short fade toward the dugout
  // side rather than a position claim.
  OUT: project({ x: 0, y: 0 }),
}

export function GeometryDiamond({ accentAway, accentHome }: {
  accentAway: string
  accentHome: string
}) {
  const first = BASE_POINTS.FIRST
  const second = BASE_POINTS.SECOND
  const third = BASE_POINTS.THIRD
  const foulLeft = project({ x: -0.7071, y: 0.7071 })
  const foulRight = project({ x: 0.7071, y: 0.7071 })

  return (
    <>
      <defs>
        <radialGradient id="pg-ground" cx="50%" cy="88%" r="105%">
          <stop offset="0%" stopColor="#0d2136" />
          <stop offset="60%" stopColor="#081726" />
          <stop offset="100%" stopColor="#040c15" />
        </radialGradient>
        <pattern id="pg-grid" width="62" height="62" patternUnits="userSpaceOnUse">
          <path d="M62 0 L0 0 0 62" fill="none" stroke="#ffffff" strokeWidth="1" opacity="0.035" />
        </pattern>
      </defs>

      <rect x="0" y="0" width="1000" height="780" fill="url(#pg-ground)" />

      {/* Fair territory, then a restrained grid for spatial reference. */}
      <path d={`M${HOME.x} ${HOME.y} L${foulLeft.x} ${foulLeft.y} A${UNIT} ${UNIT} 0 0 1 ${foulRight.x} ${foulRight.y} Z`}
        className="pg-fair" />
      <path d={`M${HOME.x} ${HOME.y} L${foulLeft.x} ${foulLeft.y} A${UNIT} ${UNIT} 0 0 1 ${foulRight.x} ${foulRight.y} Z`}
        fill="url(#pg-grid)" />

      {/* Outfield boundary arc. */}
      <path d={`M${foulLeft.x} ${foulLeft.y} A${UNIT} ${UNIT} 0 0 1 ${foulRight.x} ${foulRight.y}`}
        className="pg-boundary" />

      {/* Infield dirt: the skin around the base paths, closed behind second. */}
      <path
        d={`M${HOME.x} ${HOME.y + 22} L${first.x + 26} ${first.y} Q${second.x} ${second.y - 46} ${third.x - 26} ${third.y} Z`}
        className="pg-dirt"
      />

      {/* Base paths and foul lines. */}
      <path d={`M${HOME.x} ${HOME.y} L${first.x} ${first.y} L${second.x} ${second.y} L${third.x} ${third.y} Z`}
        className="pg-basepath" />
      <path d={`M${HOME.x} ${HOME.y} L${foulLeft.x} ${foulLeft.y}`} className="pg-foul" />
      <path d={`M${HOME.x} ${HOME.y} L${foulRight.x} ${foulRight.y}`} className="pg-foul" />

      {/* Team accents on the dugout sides, used sparingly. */}
      <path d={`M${HOME.x - 250} ${HOME.y - 40} q60 -26 116 -4`} className="pg-dugout"
        style={{ stroke: accentAway }} />
      <path d={`M${HOME.x + 250} ${HOME.y - 40} q-60 -26 -116 -4`} className="pg-dugout"
        style={{ stroke: accentHome }} />

      {/* Bases and plate. */}
      <g className="pg-bases">
        {[first, second, third].map((point, index) => (
          <rect key={index} x={point.x - 9} y={point.y - 9} width="18" height="18" rx="2"
            transform={`rotate(45 ${point.x} ${point.y})`} />
        ))}
      </g>
      <path d={`M${HOME.x - 9} ${HOME.y - 6} h18 l0 6 l-9 8 l-9 -8 z`} className="pg-plate" />
    </>
  )
}
