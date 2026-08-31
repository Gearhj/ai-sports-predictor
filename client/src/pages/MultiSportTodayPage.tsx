import { AlertTriangle, CalendarDays, Clock3, FlaskConical, RefreshCw, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { DataModeBanner, DataStatePanel, MetricCard, PageHeader } from '../components/ui'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatDate, formatTimestamp } from '../services/format'
import type {
  LeagueCode,
  NormalizedSelection,
  SportAvailabilityEntry,
  SportSection,
  TodayAllSportsReadModel,
} from '../types/multisport'

const LEAGUE_ORDER: LeagueCode[] = ['MLB', 'NFL', 'NBA', 'WNBA', 'UFC']

const STATE_LABEL: Record<NormalizedSelection['decision_state'], string> = {
  AWAITING_FINAL_EVIDENCE: 'Awaiting final evidence',
  PROVISIONAL_EDGE: 'Provisional edge',
  WATCH: 'Watch',
  PASS: 'Pass',
  FINAL_QUALIFIED: 'Final qualified',
  FINAL_NO_BET: 'Final no bet',
  NOT_EVALUATED: 'Not evaluated',
}

const FUNDING_LABEL: Record<NormalizedSelection['funding_state'], string> = {
  OFFICIAL_VIRTUAL_POSITION: 'Official virtual position',
  ELIGIBLE_NOT_FUNDED: 'Eligible · not funded',
  RESEARCH_ONLY: 'Research only',
  MARKET_NOT_SUPPORTED: 'Market not supported',
  NOT_EVALUATED: 'Not evaluated',
}

export function MultiSportTodayPage() {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.today)

  if (!data || state === 'unavailable' || state === 'loading') {
    return (
      <div className="page multisport-today-page">
        <PageHeader
          eyebrow="ALL SPORTS · PERSISTED EVIDENCE"
          title="Today’s Sports Command Center"
          description="One operational view of governed daily decisions. Public reads never call a provider."
        />
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        <DataStatePanel
          state={state}
          emptyTitle="No governed sports evidence is available"
          emptyDetail="No current sport has a persisted slate or decision. Nothing has been fabricated."
          unavailableTitle="The daily command center is unavailable"
          unavailableDetail="The persisted-read API could not be reached. No historical or illustrative result was substituted."
          action={<Link className="secondary-button" to="/mlb">Open the MLB desk</Link>}
        />
      </div>
    )
  }

  const totalAwaiting = data.awaiting_final_evidence.length
  const totalProvisional = data.provisional_opportunities.length
  const noActiveSlate = data.sport_sections.every((section) => section.scheduled_event_count === 0)

  return (
    <div className="page multisport-today-page">
      <PageHeader
        eyebrow="ALL SPORTS · DAILY COMMAND CENTER"
        title="Today’s Sports Command Center"
        description={`${formatDate(data.operational_date)} · ${data.display_timezone}`}
        actions={(
          <div className="multisport-header-actions">
            <span className="model-chip">{data.registry_version}</span>
            <button className="secondary-button" type="button" onClick={refresh} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'spin' : ''} /> Refresh persisted view
            </button>
          </div>
        )}
      />

      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <div className="multisport-safety-note" role="note">
        <ShieldCheck size={18} />
        <p><strong>No pooled bankroll and no universal sports score.</strong> Decisions remain governed within their own league, model, policy, and experiment. Display order never treats raw edges from different sports as equivalent.</p>
      </div>

      <section className="metric-grid metric-grid--three multisport-summary" aria-label="All-sports daily summary">
        <MetricCard label="Final qualified" value={data.overall_summary.final_qualified_position_count} detail="Governed and actionable only" trend={data.overall_summary.final_qualified_position_count ? 'positive' : 'neutral'} />
        <MetricCard label="Open virtual positions" value={data.overall_summary.open_position_count} detail="Separate experiment ledgers" />
        <MetricCard label="Awaiting final evidence" value={totalAwaiting} detail="Approaching governed cutoff" />
        <MetricCard label="Provisional / watch" value={totalProvisional} detail="Never an official position" />
        <MetricCard label="Settled today" value={data.overall_summary.settled_today_count} detail="Across isolated experiments" />
        <MetricCard label="Virtual exposure" value={`$${data.overall_summary.total_virtual_stake.toFixed(2)}`} detail="Official positions only" />
      </section>

      <SelectionLane
        eyebrow="1 · ACTIONABLE AUTHORITY"
        title="Qualified today"
        empty="No final governed opportunity qualifies for an official virtual position today."
        items={data.qualified_positions}
        sections={data.sport_sections}
      />

      <OpenPositionLane sections={data.sport_sections} />

      <SelectionLane
        eyebrow="3 · APPROACHING START"
        title="Awaiting final evidence"
        empty="No event is currently waiting on a final governed evidence gate."
        items={data.awaiting_final_evidence}
        sections={data.sport_sections}
      />

      <SelectionLane
        eyebrow="4 · NOT ACTIONABLE"
        title="Provisional candidates and watch items"
        empty="No provisional or watch candidate is persisted for today."
        items={data.provisional_opportunities}
        sections={data.sport_sections}
      />

      <section aria-labelledby="sport-status-heading">
        <div className="section-heading">
          <div><span className="eyebrow">5 · SPORT-SPECIFIC STATUS</span><h2 id="sport-status-heading">Daily evidence by sport</h2></div>
        </div>
        {noActiveSlate && <p className="multisport-empty" role="status">No registered live or research-shadow league has a slate on this operational date.</p>}
        <div className="sport-status-grid">
          {orderedSections(data.sport_sections).map((section) => <SportStatusCard key={section.league_code} section={section} />)}
        </div>
      </section>

      <AvailabilityRoadmap availability={data.availability} />

      <footer className="multisport-evidence-footer">
        <span><CalendarDays size={15} /> Generated {formatTimestamp(data.generated_at)}</span>
        <span><Clock3 size={15} /> Latest forecast {data.data_freshness.latest_forecast_at ? formatTimestamp(data.data_freshness.latest_forecast_at) : 'not recorded'}</span>
        <span><Clock3 size={15} /> Latest market {data.data_freshness.latest_market_at ? formatTimestamp(data.data_freshness.latest_market_at) : 'not recorded'}</span>
        <span><ShieldCheck size={15} /> Provider calls made by this read: {data.data_freshness.provider_calls_made}</span>
      </footer>
    </div>
  )
}

function SelectionLane({ eyebrow, title, empty, items, sections }: {
  eyebrow: string
  title: string
  empty: string
  items: NormalizedSelection[]
  sections: SportSection[]
}) {
  const grouped = LEAGUE_ORDER
    .map((league) => ({ league, items: items.filter((item) => item.league_code === league) }))
    .filter((group) => group.items.length > 0)

  return (
    <section className="multisport-lane" aria-label={title}>
      <div className="section-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div></div>
      {grouped.length === 0 ? <p className="multisport-empty" role="status">{empty}</p> : grouped.map((group) => {
        const definition = sections.find((section) => section.league_code === group.league)
        return (
          <div className="sport-selection-group" key={group.league}>
            <h3>{definition?.display_name ?? group.league}<span>{group.items.length} {group.items.length === 1 ? 'event' : 'events'} · ranked only within {group.league}</span></h3>
            <div className="multisport-selection-grid">
              {group.items.map((item) => <SelectionCard key={`${item.league_code}-${item.event_id}-${item.market_label ?? 'event'}`} item={item} />)}
            </div>
          </div>
        )
      })}
    </section>
  )
}

function SelectionCard({ item }: { item: NormalizedSelection }) {
  const body = (
    <>
      <div className="multisport-selection__topline">
        <span className={`sport-code sport-code--${item.league_code.toLowerCase()}`}>{item.league_code}</span>
        <span className={`decision-state decision-state--${item.decision_state.toLowerCase()}`}>{STATE_LABEL[item.decision_state]}</span>
      </div>
      <h4>{item.event_label}</h4>
      <p className="multisport-selection__time">{item.starts_at ? formatTimestamp(item.starts_at) : 'Start time unavailable'}</p>
      <dl className="multisport-selection__facts">
        <div><dt>Market</dt><dd>{item.market_label ?? 'Not evaluated'}</dd></div>
        <div><dt>Selection</dt><dd>{item.selection_label ?? '—'}{item.line == null ? '' : ` ${item.line > 0 ? '+' : ''}${item.line}`}</dd></div>
        <div><dt>Price</dt><dd>{item.price_american == null ? '—' : `${item.price_american > 0 ? '+' : ''}${item.price_american}`}</dd></div>
        <div><dt>Funding</dt><dd>{FUNDING_LABEL[item.funding_state]}</dd></div>
      </dl>
      {item.status_note && <p className="multisport-selection__note">{item.status_note}</p>}
      {item.outstanding_gates.length > 0 && <p className="multisport-selection__gates"><strong>Outstanding:</strong> {item.outstanding_gates.join(' · ')}</p>}
      <div className="multisport-selection__footer">
        <span>{item.source_label ?? 'Engine unavailable'}</span>
        {item.sport_rank != null && <span>Within-{item.league_code} rank #{item.sport_rank}</span>}
      </div>
    </>
  )
  return item.detail_href
    ? <Link className="multisport-selection" to={item.detail_href} aria-label={`Open ${item.league_code} analysis for ${item.event_label}`}>{body}</Link>
    : <article className="multisport-selection">{body}</article>
}

function OpenPositionLane({ sections }: { sections: SportSection[] }) {
  const withPositions = orderedSections(sections).filter((section) => section.open_position_count > 0)
  return (
    <section className="multisport-lane" aria-label="Open virtual positions">
      <div className="section-heading"><div><span className="eyebrow">2 · ISOLATED LEDGERS</span><h2>Open virtual positions</h2></div></div>
      {withPositions.length === 0
        ? <p className="multisport-empty" role="status">No official virtual position is open. A zero-position day is valid.</p>
        : <div className="open-position-grid">{withPositions.map((section) => (
          <article key={section.league_code}><strong>{section.league_code}</strong><span>{section.open_position_count} open</span><small>${section.virtual_stake.toFixed(2)} current slate exposure · ledger remains separate</small></article>
        ))}</div>}
    </section>
  )
}

function SportStatusCard({ section }: { section: SportSection }) {
  const href = section.league_code === 'MLB' ? '/mlb' : '/shadow-board?sport=NFL'
  return (
    <article className={`sport-status-card ${section.degraded ? 'sport-status-card--degraded' : ''}`}>
      <header><span className={`sport-code sport-code--${section.league_code.toLowerCase()}`}>{section.league_code}</span><strong>{section.availability_state.replaceAll('_', ' ')}</strong></header>
      <h3>{section.display_name}</h3>
      <p>{section.scheduled_event_count === 0 ? 'No slate on this operational date.' : `${section.scheduled_event_count} scheduled · ${section.valid_forecast_count} with a valid forecast`}</p>
      <dl>
        <div><dt>Final qualified</dt><dd>{section.final_qualified_count}</dd></div>
        <div><dt>Awaiting</dt><dd>{section.awaiting_final_evidence_count}</dd></div>
        <div><dt>Provisional / watch</dt><dd>{section.provisional_count + section.watch_count}</dd></div>
        <div><dt>Open positions</dt><dd>{section.open_position_count}</dd></div>
      </dl>
      {section.degraded && <p className="sport-status-card__warning"><AlertTriangle size={14} /> {section.degraded_reason ?? 'Persisted evidence is degraded.'}</p>}
      {!section.can_create_official_positions && section.official_positions_blocked_reason && <p className="sport-status-card__note"><FlaskConical size={14} /> {section.official_positions_blocked_reason}</p>}
      <Link className="text-link" to={href}>Open {section.league_code} view</Link>
    </article>
  )
}

function AvailabilityRoadmap({ availability }: { availability: SportAvailabilityEntry[] }) {
  const planned = availability.filter((item) => !item.navigable)
  if (!planned.length) return null
  return (
    <section className="multisport-roadmap" aria-labelledby="planned-sports-heading">
      <div className="section-heading"><div><span className="eyebrow">REGISTERED · NOT LIVE</span><h2 id="planned-sports-heading">Planned sports</h2></div></div>
      <div className="planned-sport-grid">
        {planned.map((item) => (
          <article key={item.league_code} aria-disabled="true">
            <span className="sport-code">{item.league_code}</span>
            <div><strong>{item.display_name}</strong><small>{item.availability.replaceAll('_', ' ')} · no live workflow or evidence represented</small></div>
          </article>
        ))}
      </div>
    </section>
  )
}

function orderedSections(sections: SportSection[]): SportSection[] {
  return [...sections].sort((a, b) => LEAGUE_ORDER.indexOf(a.league_code) - LEAGUE_ORDER.indexOf(b.league_code))
}

export function hasNoCrossSportRanking(data: TodayAllSportsReadModel): boolean {
  return data.cross_sport_ranking === 'NOT_APPLICABLE_PHASE_1'
}
