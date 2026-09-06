/**
 * The Daily Command Center.
 *
 * Organised around the four questions a user actually has: what is actionable
 * now, what is still being watched, what was evaluated and passed, and what is
 * finished. Groups are derived in `services/dailyDecision` from persisted
 * authority only — this page renders that derivation, it does not decide.
 *
 * A day with no qualified position is a valid, honestly-stated outcome.
 */
import { AlertTriangle, CalendarDays, Clock3, FlaskConical, RefreshCw, ShieldCheck } from 'lucide-react'
import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { GameDecisionCard } from '../components/GameDecisionCard'
import { DataModeBanner, DataStatePanel, PageHeader } from '../components/ui'
import { useApiResource } from '../hooks/useApiResource'
import {
  navigableRegistrySports,
  sportNavigationHref,
  useSportRegistry,
  type SportRegistryState,
} from '../hooks/useSportRegistry'
import { endpoints } from '../services/api'
import {
  GROUP_ORDER,
  groupSelections,
  slateMessage,
  slateNextCheckpoint,
  type DecisionGroup,
  type GroupedGame,
} from '../services/dailyDecision'
import { formatDate, formatTimestamp } from '../services/format'
import type {
  LeagueCode,
  SportAvailabilityState,
  SportRegistryEntry,
  SportSection,
  TodayAllSportsReadModel,
} from '../types/multisport'

const GROUP_META: Record<DecisionGroup, { title: string; blurb: string; empty: string }> = {
  READY: {
    title: 'Ready',
    blurb: 'A final qualified decision or a recorded virtual position exists.',
    empty: 'No game has produced a final qualified decision today. A zero-position day is valid.',
  },
  WATCHING: {
    title: 'Watching',
    blurb: 'Forecast or decision window still open, or required evidence is incomplete.',
    empty: 'No game is currently under review.',
  },
  PASSED: {
    title: 'Passed',
    blurb: 'Evaluated with sufficient evidence, but did not qualify under the policy.',
    empty: 'No game has been evaluated and passed today.',
  },
  FINAL: {
    title: 'Final and settled',
    blurb: 'The game finished or the virtual position was settled.',
    empty: 'No game has finished yet today.',
  },
}

export function MultiSportTodayPage() {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.today)
  const { registry, state: registryState } = useSportRegistry()
  const navigableSports = navigableRegistrySports(registry)
  const mlbDeskHref = sportNavigationHref(navigableSports.find((sport) => sport.league_code === 'MLB') ?? null)

  // One clock for the whole render keeps grouping and countdowns consistent.
  const now = useMemo(() => Date.now(), [data])

  const selections = useMemo(
    () => (data ? data.sport_sections.flatMap((section) => section.selections) : []),
    [data],
  )
  const grouped = useMemo(() => groupSelections(selections, now), [selections, now])

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
          action={mlbDeskHref ? <Link className="secondary-button" to={mlbDeskHref}>Open the MLB desk</Link> : undefined}
        />
      </div>
    )
  }

  if (registryState !== 'ready' || registry === null) {
    return (
      <div className="page multisport-today-page">
        <PageHeader
          eyebrow="ALL SPORTS · REGISTRY AUTHORITY REQUIRED"
          title="Today’s Sports Command Center"
          description="Sport capability and navigation remain closed until the authoritative registry is available."
        />
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        <section className="multisport-roadmap" aria-label="Sport registry status">
          <p className="multisport-empty" role="status">
            {registryState === 'loading'
              ? 'Loading the authoritative sport registry. No sport is presented as active until it is verified.'
              : 'The authoritative sport registry is unavailable. The all-sports command center is closed rather than inventing active sports from an unverified payload.'}
          </p>
        </section>
      </div>
    )
  }

  const leagueOrder = registry.sports.map((sport) => sport.league_code)
  const navigationByLeague = new Map(navigableSports.map((sport) => [sport.league_code, sportNavigationHref(sport)]))
  const gameCount = new Set(selections.map((item) => item.event_id)).size
  const counts = {
    READY: grouped.READY.length,
    WATCHING: grouped.WATCHING.length,
    PASSED: grouped.PASSED.length,
    FINAL: grouped.FINAL.length,
  }
  const checkpoint = slateNextCheckpoint(grouped.WATCHING)
  const policy = dominantPolicy(selections)

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

      <section className="daily-summary" aria-label="Daily summary">
        <p className="daily-summary__message">{slateMessage(counts)}</p>
        <div className="daily-summary__counts">
          <SummaryCount label="Games" value={gameCount} />
          {GROUP_ORDER.map((group) => (
            <SummaryCount key={group} label={GROUP_META[group].title} value={counts[group]} tone={group} />
          ))}
        </div>
        <dl className="daily-summary__meta">
          <div>
            <dt>Next checkpoint</dt>
            <dd>
              {checkpoint
                ? <>{formatTimestamp(checkpoint.at)} <small>· {checkpoint.label.toLowerCase()} · {checkpoint.eventLabel}</small></>
                : 'None scheduled'}
            </dd>
          </div>
          <div>
            <dt>Last complete refresh</dt>
            <dd>{relativeAge(data.overall_summary.latest_complete_refresh, now)}</dd>
          </div>
          <div>
            <dt>Experiment policy</dt>
            <dd>{policy ?? 'None active'}</dd>
          </div>
          <div>
            <dt>Virtual exposure</dt>
            <dd>${data.overall_summary.total_virtual_stake.toFixed(2)} <small>· virtual only</small></dd>
          </div>
        </dl>
        {counts.READY + counts.WATCHING + counts.PASSED + counts.FINAL > gameCount && (
          <p className="daily-summary__footnote">
            Some games carry more than one governed policy, so decisions outnumber games.
          </p>
        )}
      </section>

      {GROUP_ORDER.map((group) => (
        <DecisionGroupSection
          key={group}
          group={group}
          items={grouped[group]}
          leagueOrder={leagueOrder}
          sections={data.sport_sections}
          now={now}
        />
      ))}

      <section aria-labelledby="sport-status-heading" className="sport-status-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">BY SPORT</span>
            <h2 id="sport-status-heading">Evidence by sport</h2>
          </div>
        </div>
        <div className="sport-status-grid">
          {orderedSections(data.sport_sections, leagueOrder).map((section) => (
            <SportStatusCard
              key={section.league_code}
              section={section}
              href={navigationByLeague.get(section.league_code) ?? null}
            />
          ))}
        </div>
      </section>

      <AvailabilityRoadmap sports={registry.sports} registryState={registryState} />

      <footer className="multisport-evidence-footer">
        <span><CalendarDays size={15} /> Generated {formatTimestamp(data.generated_at)}</span>
        <span><Clock3 size={15} /> Latest forecast {data.data_freshness.latest_forecast_at ? formatTimestamp(data.data_freshness.latest_forecast_at) : 'not recorded'}</span>
        <span><Clock3 size={15} /> Latest market {data.data_freshness.latest_market_at ? formatTimestamp(data.data_freshness.latest_market_at) : 'not recorded'}</span>
        <span><ShieldCheck size={15} /> Provider calls made by this read: {data.data_freshness.provider_calls_made}</span>
      </footer>
    </div>
  )
}

function SummaryCount({ label, value, tone }: { label: string; value: number; tone?: DecisionGroup }) {
  return (
    <div className={`summary-count${tone ? ` summary-count--${tone.toLowerCase()}` : ''}`}>
      <span className="summary-count__value">{value}</span>
      <span className="summary-count__label">{label}</span>
    </div>
  )
}

function DecisionGroupSection({ group, items, leagueOrder, sections, now }: {
  group: DecisionGroup
  items: GroupedGame[]
  leagueOrder: LeagueCode[]
  sections: SportSection[]
  now: number
}) {
  const meta = GROUP_META[group]
  const headingId = `group-${group.toLowerCase()}`
  const orderedLeagues = [...leagueOrder]
  items.forEach(({ selection }) => {
    if (!orderedLeagues.includes(selection.league_code)) orderedLeagues.push(selection.league_code)
  })
  const byLeague = orderedLeagues
    .map((league) => ({ league, entries: items.filter((item) => item.selection.league_code === league) }))
    .filter((entry) => entry.entries.length > 0)

  // Resolved groups can be long and are rarely the reason someone opened the
  // page. Collapse them past a handful so they still answer "how many passed?"
  // at a glance without pushing the open decisions off the screen. <details>
  // keeps the content in the DOM and keyboard reachable.
  const collapsible = (group === 'PASSED' || group === 'FINAL') && items.length > 6

  const body = byLeague.length === 0
    ? <p className="multisport-empty" role="status">{meta.empty}</p>
    : byLeague.map(({ league, entries }) => {
          const definition = sections.find((section) => section.league_code === league)
          return (
            <div className="decision-group__league" key={league}>
              <h3>
                {definition?.display_name ?? league}
                <span>{entries.length} {entries.length === 1 ? 'decision' : 'decisions'} · ranked only within {league}</span>
              </h3>
              <div className="decision-card-grid">
                {entries.map(({ selection, decision }) => (
                  <GameDecisionCard
                    key={`${selection.event_id}:${selection.experiment_id ?? 'none'}:${selection.policy_version ?? 'none'}`}
                    selection={selection}
                    decision={decision}
                    now={now}
                  />
                ))}
              </div>
            </div>
          )
      })

  return (
    <section className={`decision-group decision-group--${group.toLowerCase()}`} aria-labelledby={headingId}>
      <div className="decision-group__heading">
        <h2 id={headingId}>
          {meta.title} <span className="decision-group__count">{items.length}</span>
        </h2>
        <p>{meta.blurb}</p>
      </div>
      {collapsible
        ? (
          <details className="decision-group__collapse">
            <summary>Show all {items.length} {meta.title.toLowerCase()} decisions</summary>
            {body}
          </details>
        )
        : body}
    </section>
  )
}

function SportStatusCard({ section, href }: { section: SportSection; href: string | null }) {
  return (
    <article className={`sport-status-card ${section.degraded ? 'sport-status-card--degraded' : ''}`}>
      <header>
        <span className={`sport-code sport-code--${section.league_code.toLowerCase()}`}>{section.league_code}</span>
        <strong>{availabilityLabel(section.availability_state)}</strong>
      </header>
      <h3>{section.display_name}</h3>
      <p>
        {section.scheduled_event_count === 0
          ? 'No slate on this operational date.'
          : `${section.scheduled_event_count} scheduled · ${section.valid_forecast_count} with a valid forecast`}
      </p>
      <dl>
        <div><dt>Official positions</dt><dd>{section.official_position_count ?? 0}</dd></div>
        <div><dt>Final qualified decisions</dt><dd>{section.final_qualified_decision_count ?? section.final_qualified_count ?? 0}</dd></div>
        <div><dt>Open positions</dt><dd>{section.open_position_count}</dd></div>
        <div><dt>Settled today</dt><dd>{section.settled_today_count}</dd></div>
      </dl>
      {section.degraded && (
        <p className="sport-status-card__warning">
          <AlertTriangle size={14} aria-hidden="true" /> {section.degraded_reason ?? 'Persisted evidence is degraded.'}
        </p>
      )}
      {!section.can_create_official_positions && section.official_positions_blocked_reason && (
        <p className="sport-status-card__note">
          <FlaskConical size={14} aria-hidden="true" /> {section.official_positions_blocked_reason}
        </p>
      )}
      {href
        ? <Link className="text-link" to={href}>Open {section.league_code} view</Link>
        : <span className="text-link" aria-disabled="true">{section.league_code} navigation unavailable</span>}
    </article>
  )
}

function AvailabilityRoadmap({ sports, registryState }: {
  sports: SportRegistryEntry[] | null
  registryState: SportRegistryState
}) {
  if (registryState !== 'ready') return null
  const planned = (sports ?? []).filter((item) => item.availability === 'PLANNED')
  if (!planned.length) return null
  return (
    <section className="multisport-roadmap" aria-labelledby="planned-sports-heading">
      <div className="section-heading">
        <div><span className="eyebrow">REGISTERED · NOT LIVE</span><h2 id="planned-sports-heading">Planned sports</h2></div>
      </div>
      <div className="planned-sport-grid">
        {planned.map((item) => (
          <article key={item.league_code} aria-disabled="true">
            <span className="sport-code">{item.league_code}</span>
            <div><strong>{item.display_name}</strong><small>PLANNED · no live workflow or evidence represented</small></div>
          </article>
        ))}
      </div>
    </section>
  )
}

function availabilityLabel(state: SportAvailabilityState): string {
  const labels: Record<SportAvailabilityState, string> = {
    LIVE: 'Live',
    RESEARCH_SHADOW: 'Research shadow',
    DEVELOPMENT: 'Development',
    PLANNED: 'Planned',
    UNAVAILABLE: 'Unavailable',
  }
  return labels[state] ?? state
}

/** Human age of the last complete refresh; never guesses when unrecorded. */
export function relativeAge(at: string | null, now: number): string {
  if (!at) return 'Not recorded'
  const value = Date.parse(at)
  if (Number.isNaN(value)) return 'Not recorded'
  const minutes = Math.round((now - value) / 60000)
  if (minutes < 0) return formatTimestamp(at)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m ago`
  return `${Math.floor(hours / 24)}d ago`
}

function dominantPolicy(selections: { experiment_name: string | null; policy_version: string | null }[]): string | null {
  const tally = new Map<string, number>()
  selections.forEach((item) => {
    if (!item.policy_version) return
    const label = item.experiment_name ? `${item.experiment_name} · ${item.policy_version}` : item.policy_version
    tally.set(label, (tally.get(label) ?? 0) + 1)
  })
  if (tally.size === 0) return null
  return [...tally.entries()].sort((a, b) => b[1] - a[1])[0][0]
}

function orderedSections(sections: SportSection[], leagueOrder: LeagueCode[]): SportSection[] {
  const order = new Map(leagueOrder.map((league, index) => [league, index]))
  return sections
    .map((section, index) => ({ section, index }))
    .sort((a, b) => (
      (order.get(a.section.league_code) ?? leagueOrder.length + a.index)
      - (order.get(b.section.league_code) ?? leagueOrder.length + b.index)
    ))
    .map(({ section }) => section)
}

export function hasNoCrossSportRanking(data: TodayAllSportsReadModel): boolean {
  return data.cross_sport_ranking === 'NOT_APPLICABLE_PHASE_1'
}
