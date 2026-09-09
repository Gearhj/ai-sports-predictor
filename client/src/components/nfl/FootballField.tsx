/**
 * A football field with a scoreboard, drawn with football concepts only.
 *
 * Coordinates: the field is 120 yards long (two 10-yard end zones), drawn
 * left-to-right with the away end zone on the left and the home end zone on
 * the right. A yard line is expressed 0..100 from the LEFT goal line, so a
 * drive by the offence moving right advances the ball toward 100.
 *
 * It renders only what it is given: a final score (labelled a sampled final
 * outcome when there is no sequential path) or one persisted drive's start
 * and end. It never invents a route, a throw or a tackle.
 */
import type { ReactNode } from 'react'

export interface FieldMarker {
  /** 0..100 from the left goal line. */
  yard: number
  label?: string
  tone?: 'home' | 'away' | 'neutral'
}

export interface FieldDrive {
  /** Both 0..100 from the left goal line. */
  fromYard: number
  toYard: number
  tone: 'home' | 'away'
  result?: string
}

const ACCENT_AWAY = '#4fb3ff'
const ACCENT_HOME = '#ffb454'

/** Field x for a yard 0..100 from the left goal line, inside a 1200-unit viewBox. */
export function fieldX(yard: number): number {
  const clamped = Math.max(0, Math.min(100, yard))
  return 100 + clamped * 10
}

export function FootballField({
  awayTeam, homeTeam, awayScore, homeScore, caption, markers = [], drive, period, clock,
  children,
}: {
  awayTeam: string
  homeTeam: string
  awayScore: number | null
  homeScore: number | null
  caption: string
  markers?: FieldMarker[]
  drive?: FieldDrive | null
  period?: string | null
  clock?: string | null
  children?: ReactNode
}) {
  const yardLines = Array.from({ length: 21 }, (_, index) => index * 5)
  return (
    <figure className="gridiron" aria-label={`Football field: ${awayTeam} at ${homeTeam}`}>
      <div className="gridiron__scoreboard" role="group" aria-label="Scoreboard">
        <div className="gridiron__team gridiron__team--away">
          <span className="gridiron__chip" style={{ background: ACCENT_AWAY }} aria-hidden="true" />
          <strong>{awayTeam}</strong>
          <output>{awayScore ?? '—'}</output>
        </div>
        <div className="gridiron__center">
          <span className="gridiron__caption">{caption}</span>
          {(period || clock) && (
            <span className="gridiron__clock">{[period, clock].filter(Boolean).join(' · ')}</span>
          )}
        </div>
        <div className="gridiron__team gridiron__team--home">
          <output>{homeScore ?? '—'}</output>
          <strong>{homeTeam}</strong>
          <span className="gridiron__chip" style={{ background: ACCENT_HOME }} aria-hidden="true" />
        </div>
      </div>

      <svg className="gridiron__svg" viewBox="0 0 1200 560" role="img" aria-hidden="true" preserveAspectRatio="xMidYMid meet">
        <defs>
          <pattern id="gridiron-turf" width="100" height="560" patternUnits="userSpaceOnUse">
            <rect width="50" height="560" fill="#1f6b3a" />
            <rect x="50" width="50" height="560" fill="#227540" />
          </pattern>
        </defs>
        <rect x="0" y="0" width="1200" height="560" fill="#0b2a18" />
        <rect x="100" y="40" width="1000" height="480" fill="url(#gridiron-turf)" />
        {/* End zones: away on the left, home on the right. */}
        <rect x="0" y="40" width="100" height="480" fill="#1b4f8a" />
        <rect x="1100" y="40" width="100" height="480" fill="#8a4a1b" />
        <text x="50" y="290" fill="#dfe9f5" fontSize="30" fontWeight="700" textAnchor="middle" transform="rotate(-90 50 290)" letterSpacing="4">{awayTeam}</text>
        <text x="1150" y="290" fill="#f7e5d1" fontSize="30" fontWeight="700" textAnchor="middle" transform="rotate(90 1150 290)" letterSpacing="4">{homeTeam}</text>
        {yardLines.map((yard) => (
          <g key={yard}>
            <line x1={fieldX(yard)} y1="40" x2={fieldX(yard)} y2="520" stroke="rgba(255,255,255,.55)" strokeWidth={yard % 10 === 0 ? 2 : 1} />
            {yard % 10 === 0 && yard > 0 && yard < 100 && (
              <>
                <text x={fieldX(yard)} y="120" fill="rgba(255,255,255,.75)" fontSize="26" textAnchor="middle" fontWeight="600">{yard <= 50 ? yard : 100 - yard}</text>
                <text x={fieldX(yard)} y="450" fill="rgba(255,255,255,.75)" fontSize="26" textAnchor="middle" fontWeight="600" transform={`rotate(180 ${fieldX(yard)} 441)`}>{yard <= 50 ? yard : 100 - yard}</text>
              </>
            )}
          </g>
        ))}
        {/* Hash marks. */}
        {Array.from({ length: 100 }, (_, index) => index + 1).map((yard) => (
          <g key={`hash-${yard}`}>
            <line x1={fieldX(yard)} y1="200" x2={fieldX(yard)} y2="212" stroke="rgba(255,255,255,.4)" />
            <line x1={fieldX(yard)} y1="348" x2={fieldX(yard)} y2="360" stroke="rgba(255,255,255,.4)" />
          </g>
        ))}
        {drive && (
          <g>
            <line
              x1={fieldX(drive.fromYard)} y1="280" x2={fieldX(drive.toYard)} y2="280"
              stroke={drive.tone === 'home' ? ACCENT_HOME : ACCENT_AWAY} strokeWidth="10" strokeLinecap="round" opacity=".9"
            />
            <circle cx={fieldX(drive.fromYard)} cy="280" r="10" fill="white" opacity=".85" />
            <polygon
              points={drive.toYard >= drive.fromYard
                ? `${fieldX(drive.toYard) + 16},280 ${fieldX(drive.toYard) - 4},266 ${fieldX(drive.toYard) - 4},294`
                : `${fieldX(drive.toYard) - 16},280 ${fieldX(drive.toYard) + 4},266 ${fieldX(drive.toYard) + 4},294`}
              fill={drive.tone === 'home' ? ACCENT_HOME : ACCENT_AWAY}
            />
            {drive.result && (
              <text x={fieldX(drive.toYard)} y="250" fill="white" fontSize="22" fontWeight="700" textAnchor="middle">{drive.result}</text>
            )}
          </g>
        )}
        {markers.map((marker, index) => (
          <g key={`${marker.yard}-${index}`}>
            <line x1={fieldX(marker.yard)} y1="60" x2={fieldX(marker.yard)} y2="500" stroke={marker.tone === 'home' ? ACCENT_HOME : marker.tone === 'away' ? ACCENT_AWAY : '#ffffff'} strokeWidth="4" strokeDasharray="10 8" />
            {marker.label && (
              <text x={fieldX(marker.yard)} y="30" fill="white" fontSize="20" textAnchor="middle" fontWeight="600">{marker.label}</text>
            )}
          </g>
        ))}
      </svg>
      {children && <figcaption className="gridiron__foot">{children}</figcaption>}
    </figure>
  )
}
