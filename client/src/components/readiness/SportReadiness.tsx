/**
 * Where each sport stands: next game, forecast, price, decision window,
 * account, position, and the one thing it is waiting for.
 *
 * WHY EVERY SPORT ALWAYS RENDERS
 * ------------------------------
 * A sport with no game today used to be a blank section, which a reader cannot
 * tell apart from a broken page. Each sport now shows what would happen next
 * and exactly what prerequisite is missing, so "nothing is happening" is an
 * explained state rather than an absence.
 *
 * WHAT IT NEVER SAYS
 * ------------------
 * "No bet" for a game nobody has evaluated. A window that has not opened has no
 * disposition yet, and the panel says when it opens.
 */
import { Link } from 'react-router-dom'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { cacheKey, STALE_TIME } from '../../services/queryCache'

interface ReadinessState { state: string; label: string; [key: string]: unknown }

export interface SportReadinessEntry {
  sport: string
  label: string
  policy_version?: string
  controlling_engine?: string
  read_state?: string
  error_type?: string
  next_game?: { matchup: string; starts_label: string; game_id?: string; source_game_id?: string; research_href?: string } | null
  forecast?: ReadinessState
  price?: ReadinessState
  decision?: ReadinessState
  /** College Football adds the producer's discovery lease (CURRENT / EXPIRED_LEASE / ...), a separate gate. */
  eligibility?: { eligible: boolean; reason: string; contract?: string; discovery_lease?: string | null }
  position?: ReadinessState & { open?: number; settled?: number }
  account?: { funded: boolean; label: string; funding_key?: string; realized_bankroll?: number; available_capital?: number; season_id?: string; effective_on?: string }
  pipeline: { ready: boolean; blocker: string | null; could_qualify: string | null }
  slate?: { games: number; controlling_forecasts: number; with_fresh_price: number; positions: number }
  /** NBA Gen1: per-component truth -- what is present and what is due, each on its own evidence. */
  components?: Record<string, unknown>
}

const COMPONENT_LABELS: Record<string, string> = {
  handoff: 'Model handoff', forecast: 'Production package', eligibility: 'Decision eligibility',
  price: 'Price lane', decision: 'Paper checkpoint', outcomes: 'Outcomes & scoring',
}

function ComponentTruth({ components }: { components: Record<string, unknown> }) {
  if (components.read_state === 'READ_FAILED') return null
  return (
    <details className="readiness__components" data-testid="readiness-components">
      <summary>Component truth</summary>
      <dl className="readiness__rows">
        {Object.entries(COMPONENT_LABELS).map(([key, label]) => {
          const part = components[key] as { state?: string; detail?: string; prospective?: string } | undefined
          if (!part) return null
          const state = (part.state ?? '—').replace(/_/g, ' ').toLowerCase()
          const extra = key === 'outcomes' && part.prospective ? ` · prospective: ${part.prospective}` : ''
          return <Row key={key} term={label} value={`${state}${part.detail ? ` — ${part.detail}` : ''}${extra}`} />
        })}
      </dl>
    </details>
  )
}

interface ReadinessPayload {
  schema_version: string
  generated_at: string
  authority: string
  sports: SportReadinessEntry[]
}

export const readinessKey = () => cacheKey('sport-readiness', {})
export const loadReadiness = (signal: AbortSignal) => apiRequest<ReadinessPayload>('/sports/readiness', {}, signal)

export function useSportReadiness() {
  return useApiResource<ReadinessPayload>(loadReadiness, { key: readinessKey(), staleTimeMs: STALE_TIME.slate })
}

const RESEARCH_ROUTES: Record<string, string> = {
  NBA: '/nba/research',
  CFB: '/college-football/research',
}

function gameHref(entry: SportReadinessEntry): string | null {
  if (entry.next_game?.research_href) return entry.next_game.research_href
  if (entry.sport === 'NBA' && entry.next_game?.game_id) return `/nba/games/${entry.next_game.game_id}`
  if (entry.next_game?.game_id) return `/games/${entry.next_game.game_id}?sport=${entry.sport}`
  return RESEARCH_ROUTES[entry.sport] ?? null
}

function Row({ term, value, tone }: { term: string; value: string | undefined; tone?: string }) {
  return (
    <div className="readiness__row">
      <dt>{term}</dt>
      <dd className={tone}>{value ?? '—'}</dd>
    </div>
  )
}

function SportCard({ entry }: { entry: SportReadinessEntry }) {
  if (entry.read_state === 'READ_FAILED') {
    return (
      <article className="readiness__card readiness__card--failed" data-testid={`readiness-${entry.sport}`}>
        <header><h3>{entry.label}</h3></header>
        <p role="alert">{entry.pipeline.blocker}</p>
      </article>
    )
  }
  const href = gameHref(entry)
  const positions = entry.position?.open || entry.position?.settled
    ? `${entry.position?.label} · ${entry.position?.open ?? 0} open, ${entry.position?.settled ?? 0} settled`
    : entry.position?.label
  return (
    <article className="readiness__card" data-testid={`readiness-${entry.sport}`}>
      <header className="readiness__head">
        <h3>{entry.label}</h3>
        <span className={entry.pipeline.ready ? 'readiness__chip readiness__chip--ready' : 'readiness__chip readiness__chip--blocked'}
              data-testid={`readiness-pipeline-${entry.sport}`}>
          {entry.pipeline.ready ? 'Pipeline ready' : 'Pipeline blocked'}
        </span>
      </header>
      {entry.next_game
        ? <p className="readiness__game">
            Next: {href ? <Link to={href}>{entry.next_game.matchup}</Link> : entry.next_game.matchup}
            <span> · {entry.next_game.starts_label}</span>
          </p>
        : <p className="readiness__game">No game scheduled</p>}
      <dl className="readiness__rows">
        <Row term="Forecast" value={entry.forecast?.label} />
        <Row term="Price" value={entry.price?.label} />
        <Row term="Decision" value={entry.decision?.label} />
        {entry.eligibility && (() => {
          // The eligibility record and the discovery lease are separate gates; a
          // present record with a lapsed lease is not eligible for the checkpoint.
          const lease = entry.eligibility.discovery_lease
          const lapsed = entry.eligibility.eligible && lease != null && lease !== 'CURRENT'
          return (
            <Row term="Eligibility" value={lapsed
              ? `Producer eligibility record present; discovery lease ${lease === 'EXPIRED_LEASE' ? 'expired' : lease.replace(/_/g, ' ').toLowerCase()}`
              : entry.eligibility.eligible ? 'Producer eligibility present'
                : `Awaiting producer evidence (${entry.eligibility.reason})`}
                 tone={entry.eligibility.eligible && !lapsed ? 'readiness__ok' : 'readiness__warn'} />
          )
        })()}
        <Row term="Account" value={entry.account?.funded
          ? `${entry.account.label}${entry.account.effective_on ? ` · effective ${entry.account.effective_on}` : ''}`
          : entry.account?.label} />
        <Row term="Position" value={positions} />
      </dl>
      {entry.components && <ComponentTruth components={entry.components} />}
      {entry.pipeline.blocker && (
        <p className="readiness__blocker" data-testid={`readiness-blocker-${entry.sport}`}>
          <b>Waiting for:</b> {entry.pipeline.blocker}
        </p>
      )}
      {entry.pipeline.could_qualify && (
        <p className="readiness__could bx-dim">
          <b>Could a position be created if this persists?</b> {entry.pipeline.could_qualify}
        </p>
      )}
      <p className="readiness__policy bx-dim">
        {entry.policy_version}{entry.controlling_engine ? ` · ${entry.controlling_engine}` : ''}
      </p>
    </article>
  )
}

export function SportReadiness({ sport, title = 'All four sports, right now' }: { sport?: string | null; title?: string }) {
  const { data, loading, error } = useSportReadiness()
  const wanted = sport && sport !== 'ALL' ? sport.toUpperCase() : null
  const sports = (data?.sports ?? []).filter((entry) => !wanted || entry.sport === wanted)
  return (
    <section className="readiness" data-testid="sport-readiness" aria-label="Sport readiness">
      <header className="readiness__intro">
        <h2>{title}</h2>
        <p className="bx-dim">
          {data?.authority ?? 'Virtual research only. No real-money wagering.'}
        </p>
      </header>
      {loading && !data && <p role="status">Reading each sport's state…</p>}
      {error && !data && <p role="alert">Sport readiness is unavailable right now.</p>}
      {data && sports.length === 0 && (
        <p className="bx-dim">No readiness state is published for {wanted}.</p>
      )}
      <div className="readiness__grid">
        {sports.map((entry) => <SportCard key={entry.sport} entry={entry} />)}
      </div>
    </section>
  )
}
