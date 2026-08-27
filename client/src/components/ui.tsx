import { useState, type ReactNode } from 'react'
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
