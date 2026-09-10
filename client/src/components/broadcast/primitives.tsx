/**
 * The vocabulary the four consumer pages are built from.
 *
 * Every piece here renders a number that came from the API and nothing else.
 * There is deliberately no placeholder, no sample team, no "—" that could be
 * mistaken for zero: an absent value renders as an explicit absence, because
 * a price that does not exist and a price of even money look identical once
 * you round them into a box.
 */
import { useEffect } from 'react'
import { Link } from 'react-router-dom'

export type LeagueCode = 'MLB' | 'NFL' | 'NBA' | 'WNBA' | 'NHL' | 'UFC' | string

/** Dark chrome while a broadcast page is mounted; light chrome everywhere else. */
export function useBroadcastTheme(): void {
  useEffect(() => {
    document.body.classList.add('bx-dark')
    return () => document.body.classList.remove('bx-dark')
  }, [])
}

export const pct = (value: number | null | undefined, digits = 0): string =>
  value == null || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(digits)}%`

/** Percentages arrive on both scales in this API; say which one you have. */
export const pctPoints = (value: number | null | undefined, digits = 0): string =>
  value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(digits)}%`

export const signedPoints = (value: number | null | undefined, digits = 1): string =>
  value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(digits)}`

export const american = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? '—' : value > 0 ? `+${Math.round(value)}` : `${Math.round(value)}`

export const decimals = (value: number | null | undefined, digits = 1): string =>
  value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits)

export function clockOf(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', timeZone }).format(date)
}

export function dayOf(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone }).format(date)
}

/** "4h ago" reads as freshness; a raw timestamp reads as a fact to check. */
export function agoOf(iso: string | null | undefined, now = Date.now()): string | null {
  if (!iso) return null
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return null
  const minutes = Math.round((now - then) / 60_000)
  if (minutes < 0) return 'just now'
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 36) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function LeagueTag({ league }: { league: LeagueCode }) {
  return <span className={`bx-league bx-league--${league}`}><span className="bx-league__tag">{league}</span></span>
}

/**
 * A win-probability arc. The ring is the probability; the number in the middle
 * is the same probability. It is one measurement drawn twice, never two.
 */
export function ProbabilityArc({
  value, label, sublabel, size = 116, color = 'var(--bx-accent)',
}: { value: number | null; label: string; sublabel?: string; size?: number; color?: string }) {
  const radius = size / 2 - 9
  const circumference = 2 * Math.PI * radius
  const safe = value == null || !Number.isFinite(value) ? null : Math.min(1, Math.max(0, value))
  return (
    <div className="bx-arc" style={{ color: 'var(--bx-text)' }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img"
        aria-label={safe == null ? `${label}: no forecast` : `${label} ${pct(safe, 1)}`}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgba(255,255,255,.09)" strokeWidth={8} />
        {safe != null && (
          <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={color} strokeWidth={8}
            strokeLinecap="round" strokeDasharray={`${circumference * safe} ${circumference}`}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}>
            <animate attributeName="stroke-dasharray" dur="0.7s"
              from={`0 ${circumference}`} to={`${circumference * safe} ${circumference}`} fill="freeze" />
          </circle>
        )}
        <text className="bx-arc__value" x="50%" y="47%" textAnchor="middle" dominantBaseline="middle">
          {safe == null ? '—' : pct(safe, 0)}
        </text>
        <text className="bx-arc__label" x="50%" y="64%" textAnchor="middle" fill="var(--bx-dim)">{label}</text>
      </svg>
      {sublabel && <small className="bx-dim">{sublabel}</small>}
    </div>
  )
}

/** Two probabilities that must sum with the remainder shown, not hidden. */
export function SplitBar({
  away, home, awayLabel, homeLabel, awayColor = 'var(--bx-nfl)', homeColor = 'var(--bx-accent)', tie,
}: {
  away: number | null; home: number | null; awayLabel: string; homeLabel: string
  awayColor?: string; homeColor?: string; tie?: number | null
}) {
  if (away == null || home == null) {
    return <p className="bx-dim">No forecast probabilities are published for this game.</p>
  }
  const tieShare = tie ?? Math.max(0, 1 - away - home)
  return (
    <div className="bx-split">
      <div className="bx-split__bar" role="img"
        aria-label={`${awayLabel} ${pct(away, 1)}, ${homeLabel} ${pct(home, 1)}${tieShare > 0.001 ? `, tie ${pct(tieShare, 1)}` : ''}`}>
        <span style={{ width: `${away * 100}%`, background: awayColor }} />
        {tieShare > 0.001 && <span style={{ width: `${tieShare * 100}%`, background: 'rgba(255,255,255,.3)' }} />}
        <span style={{ width: `${home * 100}%`, background: homeColor }} />
      </div>
      <div className="bx-split__legend">
        <span>{awayLabel} <b>{pct(away, 0)}</b></span>
        {tieShare > 0.001 && <span className="bx-dim">Tie {pct(tieShare, 1)}</span>}
        <span><b>{pct(home, 0)}</b> {homeLabel}</span>
      </div>
    </div>
  )
}

export function TeamMark({ abbreviation, color }: { abbreviation: string; color?: string | null }) {
  return <span className="bx-slate__abbr" style={color ? { ['--bx-team' as string]: color } : undefined}>{abbreviation}</span>
}

export function Stat({
  label, value, tone,
}: { label: string; value: string; tone?: 'good' | 'bad' | 'warn' }) {
  return <div className={`bx-stat${tone ? ` bx-stat--${tone}` : ''}`}><small>{label}</small><strong className="bx-num">{value}</strong></div>
}

export function Panel({
  title, eyebrow, action, children, className = '',
}: {
  title?: string; eyebrow?: string; action?: React.ReactNode
  children: React.ReactNode; className?: string
}) {
  return (
    <section className={`bx-panel ${className}`} aria-label={title}>
      {(title || eyebrow || action) && (
        <div className="bx-panel__head">
          <div>
            {eyebrow && <span className="bx-eyebrow">{eyebrow}</span>}
            {title && <h2>{title}</h2>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

export function ActionLink({ to, children, primary = false }: { to: string; children: React.ReactNode; primary?: boolean }) {
  return <Link className={`bx-btn${primary ? ' bx-btn--primary' : ''}`} to={to}>{children}</Link>
}

/** A source label that never merges a fallback provider into the primary one. */
export function SourceBadge({ source }: { source: string | null | undefined }) {
  const text = (source ?? '').toUpperCase()
  if (!text) return null
  const kind = text.includes('SPORTSGAMEODDS') || text.includes('SGO') ? 'sgo'
    : text.includes('KALSHI') ? 'kalshi'
    : text.includes('POLYMARKET') ? 'polymarket'
    : 'odds-api'
  const label = kind === 'sgo' ? 'SGO fallback'
    : kind === 'kalshi' ? 'Kalshi'
    : kind === 'polymarket' ? 'Polymarket'
    : 'Odds API'
  const full = kind === 'sgo' ? 'SportsGameOdds — temporary research fallback'
    : kind === 'kalshi' ? 'Kalshi prediction market'
    : kind === 'polymarket' ? 'Polymarket prediction market'
    : 'The Odds API'
  return <span className={`bx-source bx-source--${kind}`} title={full}>{label}</span>
}
