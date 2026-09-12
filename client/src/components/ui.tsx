import { useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Circle, Database, RefreshCw, WifiOff } from 'lucide-react'
import { ILLUSTRATIVE_DEMO_LABEL } from '../config/publicModel'
import type { ReadinessState, ResourceState, Trend } from '../types/domain'

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  )
}

export function DataModeBanner({
  state,
  loading,
  error,
  onRetry,
}: {
  state: ResourceState
  loading: boolean
  error: string | null
  onRetry?: () => void
}) {
  if (state === 'demo') {
    return (
      <div className="data-banner data-banner--demo" role="status">
        <span><WifiOff size={14} /> {ILLUSTRATIVE_DEMO_LABEL}</span>
        <span>Static examples for product exploration. Nothing on this page is a current result, price, or provider status.</span>
      </div>
    )
  }

  if (state === 'loading') {
    return (
      <div className="data-banner data-banner--checking" role="status">
        <span><RefreshCw size={14} className="spin" /> CHECKING LIVE DATA</span>
        <span>No results are shown until the API reports provenance.</span>
      </div>
    )
  }

  if (state === 'unavailable') {
    return (
      <div className="data-banner data-banner--unavailable" role="status">
        <span><AlertTriangle size={14} /> LIVE DATA UNAVAILABLE</span>
        <span>The live API could not be reached. No illustrative results have been substituted.</span>
        {onRetry && <button type="button" className="text-button" onClick={onRetry} title={error || 'Retry live API'}>
          <RefreshCw size={13} /> Retry
        </button>}
      </div>
    )
  }

  if (state === 'empty') {
    return (
      <div className="data-banner data-banner--empty" role="status">
        <span><Database size={14} /> LIVE DATA · NO PERSISTED RESULTS</span>
        <span>The API is connected, but this view has no eligible live records yet.</span>
      </div>
    )
  }

  return (
    <div className="data-banner data-banner--live" role="status">
      <span><CheckCircle2 size={14} /> LIVE DATA</span>
      <span>{loading ? 'Refreshing live data; the last verified response remains visible.' : 'Connected to the ASP API. Timestamps show source freshness.'}</span>
    </div>
  )
}

export function DataStatePanel({
  state,
  emptyTitle,
  emptyDetail,
  unavailableTitle = 'Live data is temporarily unavailable',
  unavailableDetail = 'The application did not replace this view with illustrative figures.',
  action,
}: {
  state: ResourceState
  emptyTitle: string
  emptyDetail: string
  unavailableTitle?: string
  unavailableDetail?: string
  action?: ReactNode
}) {
  if (state === 'live' || state === 'demo') return null
  const isLoading = state === 'loading'
  const isEmpty = state === 'empty'
  return (
    <section className={`data-state-panel data-state-panel--${state}`} aria-live="polite">
      {isLoading ? <RefreshCw className="spin" size={22} /> : isEmpty ? <Database size={22} /> : <AlertTriangle size={22} />}
      <div>
        <h2>{isLoading ? 'Checking the live API' : isEmpty ? emptyTitle : unavailableTitle}</h2>
        <p>{isLoading ? 'Waiting for a provenance-aware response. No results are displayed yet.' : isEmpty ? emptyDetail : unavailableDetail}</p>
      </div>
      {action && !isLoading && <div className="data-state-panel__action">{action}</div>}
    </section>
  )
}

export function MetricCard({
  label,
  value,
  detail,
  trend = 'neutral',
  accent,
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  trend?: Trend
  accent?: string
}) {
  return (
    <article className="metric-card" style={accent ? { '--metric-accent': accent } as React.CSSProperties : undefined}>
      <span className="metric-label">{label}</span>
      <strong className={`metric-value metric-value--${trend}`}>{value}</strong>
      {detail && <span className="metric-detail">{detail}</span>}
    </article>
  )
}

export function Panel({
  title,
  eyebrow,
  action,
  children,
  className = '',
}: {
  title?: string
  eyebrow?: string
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`panel ${className}`.trim()}>
      {(title || eyebrow || action) && (
        <header className="panel-header">
          <div>
            {eyebrow && <span className="panel-eyebrow">{eyebrow}</span>}
            {title && <h2>{title}</h2>}
          </div>
          {action && <div className="panel-action">{action}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function StatusPill({ status, label }: { status: ReadinessState; label?: string }) {
  const icon = status === 'ready'
    ? <CheckCircle2 size={12} />
    : status === 'watch' || status === 'stale'
      ? <AlertTriangle size={12} />
      : <Circle size={12} />
  return <span className={`status-pill status-pill--${status}`}>{icon}{label || status}</span>
}

export function ScoreBar({
  value,
  label,
  tone = 'teal',
}: {
  value: number | null
  label?: string
  tone?: 'teal' | 'blue' | 'amber'
}) {
  return (
    <div className="score-bar" aria-label={`${label || 'Score'} ${value === null ? 'unavailable' : `${value}%`}`}>
      <div className="score-bar__meta"><span>{label}</span><strong>{value === null ? '—' : `${Math.round(value)}%`}</strong></div>
      <div className="score-bar__track"><span className={`score-bar__fill score-bar__fill--${tone}`} style={{ width: `${value === null ? 0 : Math.min(100, Math.max(0, value))}%` }} /></div>
    </div>
  )
}

export function TeamBadge({ abbreviation, color, size = 'md' }: { abbreviation: string; color: string; size?: 'sm' | 'md' | 'lg' }) {
  return (
    <span className={`team-badge team-badge--${size}`} style={{ '--team-color': color } as React.CSSProperties} aria-hidden="true">
      {abbreviation}
    </span>
  )
}

// Canonical MLB team-logo resolution. Logos are served from a cacheable CDN and
// keyed by the team's abbreviation identity; the abbreviation badge is the
// alt text and the fallback when a logo cannot be resolved or loaded.
const ESPN_TEAM_SLUG: Record<string, string> = {
  ARI: 'ari', AZ: 'ari', ATL: 'atl', BAL: 'bal', BOS: 'bos', CHC: 'chc', CHW: 'chw', CWS: 'chw',
  CIN: 'cin', CLE: 'cle', COL: 'col', DET: 'det', HOU: 'hou', KC: 'kc', KCR: 'kc', LAA: 'laa',
  LAD: 'lad', MIA: 'mia', MIL: 'mil', MIN: 'min', NYM: 'nym', NYY: 'nyy', OAK: 'oak', ATH: 'oak',
  PHI: 'phi', PIT: 'pit', SD: 'sd', SDP: 'sd', SEA: 'sea', SF: 'sf', SFG: 'sf', STL: 'stl',
  TB: 'tb', TBR: 'tb', TEX: 'tex', TOR: 'tor', WSH: 'wsh', WSN: 'wsh', WAS: 'wsh',
}

export function teamLogoUrl(abbreviation: string): string | null {
  const slug = ESPN_TEAM_SLUG[abbreviation.toUpperCase()]
  return slug ? `https://a.espncdn.com/i/teamlogos/mlb/500/${slug}.png` : null
}

export function TeamLogo({ abbreviation, color, size = 'md' }: { abbreviation: string; color: string; size?: 'sm' | 'md' | 'lg' }) {
  const [failed, setFailed] = useState(false)
  const url = teamLogoUrl(abbreviation)
  if (!url || failed) return <TeamBadge abbreviation={abbreviation} color={color} size={size} />
  return (
    <img
      className={`team-logo team-logo--${size}`}
      src={url}
      alt={abbreviation}
      title={abbreviation}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  )
}

export const formatMoneyline = (value: number | null | undefined) => value == null ? '—' : value > 0 ? `+${Math.round(value)}` : `${Math.round(value)}`
export const formatCurrency = (value: number, digits = 2) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)
export const formatPct = (value: number | null | undefined, digits = 1) => value == null ? '—' : `${value.toFixed(digits)}%`
export const formatSignedCurrency = (value: number, digits = 2) => `${value > 0 ? '+' : ''}${formatCurrency(value, digits)}`
export const formatSignedPct = (value: number | null | undefined, digits = 1) => value == null ? '—' : `${value > 0 ? '+' : ''}${formatPct(value, digits)}`
export const formatNumber = (value: number | null | undefined, digits = 1) => value == null ? '—' : value.toFixed(digits)

export function TableScroll({ children, ariaLabel }: { children: ReactNode; ariaLabel?: string }) {
  return <div className="table-scroll" role={ariaLabel ? 'region' : undefined} aria-label={ariaLabel} tabIndex={ariaLabel ? 0 : undefined}>{children}</div>
}

/**
 * A placeholder shaped like the content that will replace it.
 *
 * Skeletons exist so the page has its final structure from the first paint:
 * headings, panels and table rows occupy the space they will keep, and the
 * arriving data fills them in without the layout jumping. A spinner in the
 * middle of an empty page is what made a loading page look like a broken one.
 */
export function Skeleton({ width = '100%', height = 14, radius = 6 }: {
  width?: string | number
  height?: string | number
  radius?: number
}) {
  return (
    <span
      className="skeleton"
      aria-hidden="true"
      style={{ width, height, borderRadius: radius }}
    />
  )
}

export function SkeletonTable({ rows = 6, columns = 5, ariaLabel = 'Loading rows' }: {
  rows?: number
  columns?: number
  ariaLabel?: string
}) {
  return (
    <div className="skeleton-table" role="status" aria-busy="true" aria-label={ariaLabel}>
      {Array.from({ length: rows }, (_, row) => (
        <div className="skeleton-table__row" key={row}>
          {Array.from({ length: columns }, (_, column) => (
            <Skeleton key={column} height={12} width={column === 0 ? '38%' : '100%'} />
          ))}
        </div>
      ))}
    </div>
  )
}

export function SkeletonCards({ count = 4 }: { count?: number }) {
  return (
    <div className="skeleton-cards" role="status" aria-busy="true" aria-label="Loading summary">
      {Array.from({ length: count }, (_, index) => (
        <div className="skeleton-cards__card" key={index}>
          <Skeleton width="52%" height={10} />
          <Skeleton width="72%" height={22} />
          <Skeleton width="40%" height={10} />
        </div>
      ))}
    </div>
  )
}

function relativeAge(timestamp: number, now: number): string {
  const seconds = Math.max(0, Math.round((now - timestamp) / 1000))
  if (seconds < 10) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`
}

/**
 * When this page last re-read stored data, and whether that read is current.
 *
 * `updatedAt` is a **display** clock: when the browser last fetched what is on
 * screen. It is not the evidence clock. A quote's freshness, a forecast's
 * publication time and a decision cutoff all come from the payload and are
 * shown by the page itself; re-reading a stored quote here does not give it a
 * newer evidence timestamp, and nothing on this control relaxes those rules.
 */
export function Freshness({ updatedAt, refreshing, stale, error, onRetry, label = 'Data' }: {
  updatedAt: number | null
  refreshing: boolean
  stale?: boolean
  error?: string | null
  onRetry?: () => void
  label?: string
}) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15_000)
    return () => window.clearInterval(timer)
  }, [])

  const failed = Boolean(error)
  return (
    <span
      className={`freshness${failed ? ' freshness--error' : stale ? ' freshness--stale' : ''}`}
      aria-live="polite"
    >
      {refreshing
        ? <RefreshCw className="spin" size={12} aria-hidden="true" />
        : failed
          ? <WifiOff size={12} aria-hidden="true" />
          : <Circle size={8} aria-hidden="true" />}
      <span className="freshness__text">
        {refreshing
          ? `Updating ${label.toLowerCase()}…`
          : failed
            ? `Showing the last good read — refresh failed (${error})`
            : updatedAt === null
              ? `${label} not read yet`
              : `${label} read ${relativeAge(updatedAt, now)}`}
      </span>
      {failed && onRetry && (
        <button type="button" className="freshness__retry" onClick={onRetry}>Retry</button>
      )}
    </span>
  )
}

/**
 * One signal pill, styled from the shared status table.
 *
 * Named `SignalPill` because `StatusPill` above is a different thing — a
 * readiness indicator — and the two must not share a name or a class prefix.
 *
 * Sizing is fixed here rather than at each call site so a longer label like
 * STANDARD EDGE cannot change a row's geometry, which is how it came to
 * collide with the Analysis button. The pill wraps its text instead of pushing
 * its neighbours.
 */
export function SignalPill({ label, className, title, sub }: {
  label: ReactNode
  className: string
  title?: string
  /** A quieter second line, e.g. "last pregame reading". */
  sub?: ReactNode
}) {
  return (
    <span className="signal-pill-wrap">
      <span className={className} title={title}>{label}</span>
      {sub && <small className="signal-pill__sub">{sub}</small>}
    </span>
  )
}
