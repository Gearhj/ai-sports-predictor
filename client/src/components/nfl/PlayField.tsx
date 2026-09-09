/**
 * The field, and the ball moving on it.
 *
 * This is the dominant surface of the play theater, so it is drawn large and
 * given the whole content width. Everything on it comes from one play's own
 * recorded state and geometry: the line of scrimmage, the line to gain, the
 * ball's path, the markers, the score, the clock and the down. Nothing is
 * interpolated between plays and nothing is invented inside one.
 *
 * The paths are modeled schematic geometry. They show what KIND of play
 * happened -- a throw curves through the air, a carry runs along the ground, a
 * sack goes backwards, a return reverses direction and changes colour -- and
 * they are not a claim about how any real ball moved.
 */
import { useId, type CSSProperties, type ReactNode } from 'react'
import { teamColor } from './FootballField'
import {
  BIG_MARKERS, FIELD_X0, FIELD_X1, MARKER_LABEL, clockLabel, fieldX, fieldY,
  lineToGainX, scrimmageX, segmentPath, segmentStyle, situationLabel, traverse,
  type Play, type PlaySegment,
} from './playGeometry'
import './nfltheater.css'

export interface Trail {
  segments: PlaySegment[]
  possession: 'HOME' | 'AWAY'
  scoring: boolean
}

const TOP = 40
const BOTTOM = 520

function Markers({ play, colors, progress }: {
  play: Play; colors: Record<'home' | 'away', string>; progress: number
}) {
  // A marker belongs to the end of the action, so it appears as the ball arrives.
  const shown = progress > 0.55 || play.path.segments.length === 0
  if (!shown) return null
  return (
    <g>
      {play.path.markers.map((marker, index) => {
        const x = fieldX(marker.point[0])
        const y = fieldY(marker.point[1])
        const big = BIG_MARKERS.has(marker.kind)
        const tone = play.before.possession === 'HOME' ? colors.home : colors.away
        return (
          <g key={`${marker.kind}-${index}`} data-testid="play-marker" data-kind={marker.kind}>
            {big && <circle cx={x} cy={y} r="30" fill={tone} opacity=".25" className="nfl-play-pulse" />}
            <circle cx={x} cy={y} r={big ? 13 : 8} fill={big ? '#fff8d8' : '#ffffff'}
              stroke={big ? '#f2c14e' : 'rgba(255,255,255,.75)'} strokeWidth={big ? 4 : 2} />
            <text x={x} y={y - (big ? 26 : 18)} textAnchor="middle" fontSize={big ? 24 : 17}
              fontWeight={big ? 800 : 600} fill="#fffdf2" stroke="#0b2a18" strokeWidth="4"
              paintOrder="stroke" letterSpacing=".04em">
              {MARKER_LABEL[marker.kind] ?? marker.kind.replaceAll('_', ' ').toLowerCase()}
            </text>
          </g>
        )
      })}
    </g>
  )
}

export function PlayField({
  play, homeTeam, awayTeam, progress, trails = [], showTrails = true, caption,
  celebration, children, height = 'tall', overlay,
}: {
  play: Play | null
  homeTeam: string
  awayTeam: string
  /** 0..1 through the current play. */
  progress: number
  trails?: Trail[]
  showTrails?: boolean
  caption: string
  celebration?: 'td' | 'fg' | 'turnover' | null
  children?: ReactNode
  height?: 'tall' | 'short'
  overlay?: ReactNode
}) {
  const turfId = useId().replace(/:/g, '')
  const colors = { home: teamColor(homeTeam, 'home'), away: teamColor(awayTeam, 'away') }
  const state = play ? play.before : null
  const shown = play && progress >= 1 ? play.after : state
  const offenseColor = shown?.possession === 'HOME' ? colors.home : colors.away
  const los = state ? fieldX(scrimmageX(state)) : null
  const gain = state ? lineToGainX(state) : null
  const walk = play && play.path.segments.length ? traverse(play.path.segments) : null
  // A penalty, a spike and a period boundary have no ball motion by
  // construction. The ball still sits at the spot: dropping it entirely would
  // make one play in twenty look like a rendering failure.
  const ball = walk ? walk.at(progress)
    : state && state.phase !== 'FINAL' ? { x: fieldX(scrimmageX(state)), y: fieldY(0.5) }
    : null
  const { period, clock } = shown ? clockLabel(shown) : { period: '', clock: '' }

  const toneColor = (tone: string, possession: 'HOME' | 'AWAY') => {
    if (tone === 'loss') return '#ff7d6b'
    if (tone === 'incomplete') return '#cfe2ef'
    if (tone === 'kick') return '#ffe08a'
    if (tone === 'defense') return possession === 'HOME' ? colors.away : colors.home
    return possession === 'HOME' ? colors.home : colors.away
  }

  return (
    <figure
      className={`gridiron nfl-broadcast nfl-playfield nfl-playfield--${height}${celebration ? ` nfl-broadcast--${celebration}` : ''}`}
      style={{ '--team-home': colors.home, '--team-away': colors.away } as CSSProperties}
      aria-label={`Football field: ${awayTeam} at ${homeTeam}`}
    >
      <div className="gridiron__scoreboard" role="group" aria-label="Scoreboard">
        <div className="gridiron__team gridiron__team--away">
          <span className="gridiron__chip" style={{ background: colors.away }} aria-hidden="true" />
          <strong>{awayTeam}</strong>
          <output data-testid="away-score">{shown?.awayScore ?? '—'}</output>
          {shown?.possession === 'AWAY' && shown.phase !== 'FINAL' && (
            <span className="nfl-possession">● BALL</span>
          )}
        </div>
        <div className="gridiron__center">
          <span className="gridiron__caption">{caption}</span>
          <span className="gridiron__clock" data-testid="game-clock">
            {shown?.phase === 'FINAL' ? 'FINAL' : [period, clock].filter(Boolean).join(' · ')}
          </span>
          {shown && (
            <span className="nfl-situation" data-testid="situation">
              {situationLabel(shown, homeTeam, awayTeam)}
            </span>
          )}
        </div>
        <div className="gridiron__team gridiron__team--home">
          <output data-testid="home-score">{shown?.homeScore ?? '—'}</output>
          <strong>{homeTeam}</strong>
          <span className="gridiron__chip" style={{ background: colors.home }} aria-hidden="true" />
          {shown?.possession === 'HOME' && shown.phase !== 'FINAL' && (
            <span className="nfl-possession">● BALL</span>
          )}
        </div>
      </div>

      <svg className="gridiron__svg" viewBox="0 0 1200 560" role="img" preserveAspectRatio="xMidYMid meet"
        aria-label={play ? play.description : caption}>
        <defs>
          <pattern id={turfId} width="100" height="560" patternUnits="userSpaceOnUse">
            <rect width="50" height="560" fill="#1f6b3a" />
            <rect x="50" width="50" height="560" fill="#227540" />
          </pattern>
          <filter id={`glow${turfId}`} x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="7" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>
        <rect x="0" y="0" width="1200" height="560" fill="#0b2a18" />
        <rect x={FIELD_X0} y={TOP} width={FIELD_X1 - FIELD_X0} height={BOTTOM - TOP} fill={`url(#${turfId})`} />
        {/* HOME attacks left, so the left end zone is the one HOME scores in. */}
        <rect className={celebration === 'td' && shown?.possession === 'HOME' ? 'nfl-endzone-flash' : ''}
          x="0" y={TOP} width={FIELD_X0} height={BOTTOM - TOP} fill={colors.home} fillOpacity=".5" />
        <rect className={celebration === 'td' && shown?.possession === 'AWAY' ? 'nfl-endzone-flash' : ''}
          x={FIELD_X1} y={TOP} width={FIELD_X0} height={BOTTOM - TOP} fill={colors.away} fillOpacity=".5" />
        <text x="50" y="280" fill="#f7e5d1" fontSize="30" fontWeight="700" textAnchor="middle"
          transform="rotate(-90 50 280)" letterSpacing="4">{homeTeam}</text>
        <text x="1150" y="280" fill="#dfe9f5" fontSize="30" fontWeight="700" textAnchor="middle"
          transform="rotate(90 1150 280)" letterSpacing="4">{awayTeam}</text>

        {Array.from({ length: 21 }, (_, i) => i * 5).map((yard) => (
          <g key={yard}>
            <line x1={fieldX(yard)} y1={TOP} x2={fieldX(yard)} y2={BOTTOM}
              stroke="rgba(255,255,255,.5)" strokeWidth={yard % 10 === 0 ? 2 : 1} />
            {yard % 10 === 0 && yard > 0 && yard < 100 && (
              <>
                <text x={fieldX(yard)} y="110" fill="rgba(255,255,255,.7)" fontSize="26" textAnchor="middle" fontWeight="600">
                  {yard <= 50 ? yard : 100 - yard}
                </text>
                <text x={fieldX(yard)} y="470" fill="rgba(255,255,255,.7)" fontSize="26" textAnchor="middle" fontWeight="600">
                  {yard <= 50 ? yard : 100 - yard}
                </text>
              </>
            )}
          </g>
        ))}
        {Array.from({ length: 100 }, (_, i) => i + 1).map((yard) => (
          <g key={`h${yard}`}>
            <line x1={fieldX(yard)} y1="205" x2={fieldX(yard)} y2="217" stroke="rgba(255,255,255,.35)" />
            <line x1={fieldX(yard)} y1="343" x2={fieldX(yard)} y2="355" stroke="rgba(255,255,255,.35)" />
          </g>
        ))}

        {/* Persistent wake of earlier plays in the drive. */}
        {showTrails && (
          <g data-testid="play-trails" opacity=".4">
            {trails.map((trail, index) => (
              <g key={index}>
                {trail.segments.map((segment, s) => {
                  const style = segmentStyle(segment.kind)
                  return (
                    <path key={s} d={segmentPath(segment)} fill="none" strokeLinecap="round"
                      stroke={trail.scoring ? '#ffe9a8' : toneColor(style.tone, trail.possession)}
                      strokeWidth={Math.max(3, style.width - 4)} opacity={trail.scoring ? 0.75 : 0.5} />
                  )
                })}
              </g>
            ))}
          </g>
        )}

        {/* Line of scrimmage and the line to gain, drawn like a broadcast. */}
        {los !== null && shown?.phase !== 'FINAL' && (
          <line x1={los} y1={TOP} x2={los} y2={BOTTOM} stroke="#e8f2ff" strokeWidth="4" opacity=".85"
            data-testid="line-of-scrimmage" />
        )}
        {gain !== null && (
          <line x1={fieldX(gain)} y1={TOP} x2={fieldX(gain)} y2={BOTTOM} stroke="#ffd54a"
            strokeWidth="5" opacity=".9" data-testid="line-to-gain" />
        )}

        {/* The play itself: the full route faint, the travelled part bright. */}
        {play && play.path.segments.map((segment, index) => {
          const style = segmentStyle(segment.kind)
          const color = toneColor(style.tone, segment.possession)
          return (
            <g key={index} data-testid="play-segment" data-kind={segment.kind}>
              <path d={segmentPath(segment)} fill="none" stroke={color} strokeWidth={style.width}
                strokeDasharray={style.dash} strokeLinecap="round" opacity=".28" />
            </g>
          )
        })}
        {walk && (
          <path d={walk.partial(progress)} fill="none" strokeLinecap="round" strokeLinejoin="round"
            stroke={offenseColor} strokeWidth="10" opacity=".95" filter={`url(#glow${turfId})`}
            data-testid="play-progress" />
        )}
        {play && <Markers play={play} colors={colors} progress={progress} />}
        {ball && (
          <g data-testid="football-marker" data-x={ball.x.toFixed(1)} transform={`translate(${ball.x} ${ball.y})`}>
            <circle r="26" fill={offenseColor} opacity=".28" />
            <ellipse rx="19" ry="11" fill="#9c5933" stroke="white" strokeWidth="3" transform="rotate(-22)" />
            <path d="M-9 0 H9 M-4 -4 V4 M0 -4 V4 M4 -4 V4" stroke="white" strokeWidth="2" transform="rotate(-22)" />
          </g>
        )}
        {overlay}
      </svg>
      {children && <figcaption className="gridiron__foot">{children}</figcaption>}
    </figure>
  )
}
