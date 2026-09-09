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
import { useId, type CSSProperties, type ReactNode } from 'react'
import './nfltheater.css'

export interface FieldMarker {
  /** 0..100 from the left goal line. */
  yard: number
  label?: string
  tone?: 'home' | 'away' | 'neutral'
}

export interface FieldDrive {
  /** Both 0..100 from the left goal line. */
  fromYard: number
  toYard: number | null
  tone: 'home' | 'away'
  result?: string
  progress?: number
}

const ACCENT_AWAY = '#4fb3ff'
const ACCENT_HOME = '#ffb454'
const TEAM_COLORS: Record<string, string> = {
  ARI: '#b62c48', ATL: '#c93945', BAL: '#7853b8', BUF: '#2771cb', CAR: '#259dc6', CHI: '#eb732e',
  CIN: '#f47431', CLE: '#dc7633', DAL: '#4187bb', DEN: '#ef7338', DET: '#38a4e1', GB: '#e6bc41',
  HOU: '#cf4058', IND: '#4c82cf', JAX: '#24a7a0', KC: '#ef5364', LA: '#397be3', LAR: '#397be3',
  LAC: '#4bb6ef', LV: '#b0bbc6', MIA: '#28c3b5', MIN: '#9a6dd5', NE: '#df536d', NO: '#d6bb78',
  NYG: '#4c87df', NYJ: '#2dab88', PHI: '#28b7a5', PIT: '#e8bf34', SEA: '#83c842', SF: '#ec5b66',
  TB: '#f16b6c', TEN: '#67bddf', WAS: '#cc9150',
}
export function teamColor(team: string, side: 'home' | 'away') { return TEAM_COLORS[team] ?? (side === 'home' ? ACCENT_HOME : ACCENT_AWAY) }

/** Field x for a yard 0..100 from the left goal line, inside a 1200-unit viewBox. */
export function fieldX(yard: number): number {
  const clamped = Math.max(0, Math.min(100, yard))
  return 100 + clamped * 10
}

export function FootballField({
  awayTeam, homeTeam, awayScore, homeScore, caption, markers = [], drive, period, clock,
  children, trails = [], possession, celebration, compact = false,
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
  trails?: FieldDrive[]
  possession?: 'HOME' | 'AWAY'
  celebration?: string | null
  compact?: boolean
}) {
  const yardLines = Array.from({ length: 21 }, (_, index) => index * 5)
  const turfId = useId().replace(/:/g, '')
  const colors = { home: teamColor(homeTeam, 'home'), away: teamColor(awayTeam, 'away') }
  const progress = Math.min(1, Math.max(0, drive?.progress ?? 1))
  const ballYard = drive ? drive.fromYard + ((drive.toYard ?? drive.fromYard) - drive.fromYard) * progress : 0
  return (
    <figure className={`gridiron nfl-broadcast${compact ? ' nfl-broadcast--compact' : ''}${celebration ? ` nfl-broadcast--${celebration}` : ''}`} style={{ '--team-home': colors.home, '--team-away': colors.away } as CSSProperties} aria-label={`Football field: ${awayTeam} at ${homeTeam}`}>
      <div className="gridiron__scoreboard" role="group" aria-label="Scoreboard">
        <div className="gridiron__team gridiron__team--away">
          <span className="gridiron__chip" style={{ background: colors.away }} aria-hidden="true" />
          <strong>{awayTeam}</strong>
          <output>{awayScore ?? '—'}</output>
          {possession === 'AWAY' && <span className="nfl-possession" aria-label={`${awayTeam} possession`}>● BALL</span>}
        </div>
        <div className="gridiron__center">
          <span className="gridiron__caption">{caption}</span>
          {(period || clock) && (
            <span className="gridiron__clock">{[period, clock].filter(Boolean).join(' · ')}</span>
          )}
          {drive?.result && <span className="nfl-score-result" data-testid="drive-result">{progress >= 1 ? drive.result : 'DRIVE IN PROGRESS'}</span>}
        </div>
        <div className="gridiron__team gridiron__team--home">
          <output>{homeScore ?? '—'}</output>
          <strong>{homeTeam}</strong>
          <span className="gridiron__chip" style={{ background: colors.home }} aria-hidden="true" />
          {possession === 'HOME' && <span className="nfl-possession" aria-label={`${homeTeam} possession`}>● BALL</span>}
        </div>
      </div>

      <svg className="gridiron__svg" viewBox="0 0 1200 560" role="img" aria-hidden="true" preserveAspectRatio="xMidYMid meet">
        <defs>
          <pattern id={turfId} width="100" height="560" patternUnits="userSpaceOnUse">
            <rect width="50" height="560" fill="#1f6b3a" />
            <rect x="50" width="50" height="560" fill="#227540" />
          </pattern>
        </defs>
        <rect x="0" y="0" width="1200" height="560" fill="#0b2a18" />
        <rect x="100" y="40" width="1000" height="480" fill={`url(#${turfId})`} />
        {/* End zones: away on the left, home on the right. */}
        <rect className={celebration === 'td' && possession === 'HOME' ? 'nfl-endzone-flash' : ''} x="0" y="40" width="100" height="480" fill={colors.away} fillOpacity=".55" />
        <rect className={celebration === 'td' && possession === 'AWAY' ? 'nfl-endzone-flash' : ''} x="1100" y="40" width="100" height="480" fill={colors.home} fillOpacity=".55" />
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
        {trails.filter((t) => t.toYard !== null).map((trail, i) => <line key={i} x1={fieldX(trail.fromYard)} x2={fieldX(trail.toYard!)} y1={250 + (i % 5) * 14} y2={250 + (i % 5) * 14} stroke={colors[trail.tone]} strokeWidth="5" opacity=".32" strokeLinecap="round" />)}
        {drive && (
          <g>
            {drive.toYard !== null && <>
              <line x1={fieldX(drive.fromYard)} y1="280" x2={fieldX(drive.toYard)} y2="280" stroke="white" strokeWidth="2" strokeDasharray="6 9" opacity=".45" />
              <line x1={fieldX(drive.fromYard)} y1="280" x2={fieldX(ballYard)} y2="280" stroke={colors[drive.tone]} strokeWidth="12" strokeLinecap="round" />
              <line x1={fieldX(drive.toYard)} y1="255" x2={fieldX(drive.toYard)} y2="305" stroke={colors[drive.tone]} strokeWidth="4" />
            </>}
            <circle cx={fieldX(drive.fromYard)} cy="280" r="13" fill="none" stroke="white" strokeWidth="3" />
            <text x={fieldX(drive.fromYard)} y="335" fill="white" fontSize="17" textAnchor="middle">START</text>
            <g data-testid="football-marker" data-yard={ballYard.toFixed(2)} transform={`translate(${fieldX(ballYard)} 280)`}>
              <circle r="30" fill={colors[drive.tone]} opacity=".2" />
              <ellipse rx="22" ry="13" fill="#9c5933" stroke="white" strokeWidth="3" transform="rotate(-25)" />
              <path d="M-10 0 H10 M-5 -4 V4 M0 -4 V4 M5 -4 V4" stroke="white" strokeWidth="2" transform="rotate(-25)" />
            </g>
          </g>
        )}
        {markers.map((marker, index) => (
          <g key={`${marker.yard}-${index}`}>
            <line x1={fieldX(marker.yard)} y1="60" x2={fieldX(marker.yard)} y2="500" stroke={marker.tone === 'home' ? colors.home : marker.tone === 'away' ? colors.away : '#ffffff'} strokeWidth="4" strokeDasharray="10 8" />
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
