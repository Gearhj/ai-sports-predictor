/**
 * A club's crest, at whatever size the surface needs.
 *
 * The image is treated as an enhancement, never as the layout. The coloured
 * mark underneath is drawn first and always occupies the exact same box, so
 * a slow CDN, an offline laptop, a blocked request or a team we have no
 * artwork for all degrade to something that still reads as that team --
 * their colours, their abbreviation, the same size hole in the page. Nothing
 * shifts when the image arrives or fails.
 */
import { useState } from 'react'
import { contrastInk, logoUrl, teamIdentity, type LeagueCode, type TeamIdentity } from '../services/teamIdentity'

export interface TeamLogoProps {
  league: LeagueCode
  abbreviation: string | null | undefined
  /** Season of the EVENT, so a historical page keeps its historical crest. */
  season?: number | null
  size?: number
  /** Suppress the image and draw the colour mark. Used in dense rows. */
  markOnly?: boolean
  className?: string
}

export function TeamLogo({
  league, abbreviation, season, size = 44, markOnly = false, className = '',
}: TeamLogoProps) {
  const identity = teamIdentity(league, abbreviation, season)
  const [failed, setFailed] = useState(false)
  const src = markOnly ? null : logoUrl(identity)
  const showImage = src != null && !failed

  return (
    <span
      className={`team-logo${showImage ? ' is-image' : ''} ${className}`}
      style={{
        width: size, height: size,
        // The mark is the ground; the image sits on it. Both fill the box.
        background: showImage ? 'transparent' : identity.primary,
        color: contrastInk(identity.primary),
        borderColor: showImage ? 'transparent' : identity.secondary,
        fontSize: Math.max(10, Math.round(size * (identity.abbreviation.length > 2 ? 0.3 : 0.36))),
      }}
      title={identity.name}
    >
      {showImage
        ? <img src={src} alt="" width={size} height={size} loading="lazy" decoding="async"
            onError={() => setFailed(true)} />
        : <span aria-hidden="true">{identity.abbreviation}</span>}
    </span>
  )
}

/** Logo, name and optional line of context, as one identity block. */
export function TeamBadge({
  league, abbreviation, season, name, size = 44, detail, align = 'left', nameSize,
}: TeamLogoProps & {
  /** Overrides the canonical name only when the caller has a better one. */
  name?: string | null
  detail?: React.ReactNode
  align?: 'left' | 'right'
  nameSize?: number
}) {
  const identity = teamIdentity(league, abbreviation, season)
  return (
    <span className={`team-badge team-badge--${align}`}>
      <TeamLogo league={league} abbreviation={abbreviation} season={season} size={size} />
      <span className="team-badge__text">
        <b style={nameSize ? { fontSize: nameSize } : undefined}>{name || identity.name}</b>
        {detail != null && <small>{detail}</small>}
      </span>
    </span>
  )
}

export { teamIdentity }
export type { TeamIdentity }
