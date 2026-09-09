/**
 * NFL week overview — the normal way into the NFL product.
 *
 * Week navigation, one card per event, the research-import status in plain
 * words, and a way into every game's analysis and Simulation Lab. Everything
 * is persisted research-shadow evidence; nothing here is executable.
 */
import { Beaker, ChevronLeft, ChevronRight, FlaskConical } from 'lucide-react'
import { useCallback, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { NflEventCard } from '../components/nfl/NflEventCard'
import { DataStatePanel, PageHeader } from '../components/ui'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatDate, formatTimestamp } from '../services/format'
import type { NflConsumerStatus, NflWeekOverview, NflWeeks } from '../types/nfl'

export function ConsumerStatusLine({ consumer }: { consumer: NflConsumerStatus }) {
  const lastImport = [consumer.last_bundle_import_utc, consumer.last_supplement_import_utc].filter(Boolean).sort().at(-1) ?? null
  return (
    <p className="nfl-consumer" aria-label="Research import status">
      <FlaskConical size={13} aria-hidden="true" />
      {consumer.auto_import_enabled
        ? `Automatic research import on (${consumer.job_name}) · last successful run ${consumer.last_success_utc ? formatTimestamp(consumer.last_success_utc) : 'not yet'}`
        : `Automatic research import is off · manual imports only`}
      {' · '}{consumer.registered_bundles.toLocaleString()} forecast bundles, {consumer.registered_supplements} supplements registered
      {lastImport ? ` · newest artifact imported ${formatTimestamp(lastImport)}` : ''}
      {consumer.last_failure_utc && (!consumer.last_success_utc || consumer.last_failure_utc > consumer.last_success_utc)
        ? ` · last run failed ${formatTimestamp(consumer.last_failure_utc)}: ${consumer.last_failure_message ?? 'see job log'}`
        : ''}
    </p>
  )
}

export function NflWeekPage() {
  const [params, setParams] = useSearchParams()
  const weeksResource = useApiResource<NflWeeks>(useCallback((signal: AbortSignal) => endpoints.nflWeeks(signal), []))
  const weeks = weeksResource.data
  const requestedSeason = Number(params.get('season'))
  const requestedWeek = Number(params.get('week'))
  const selected = useMemo(() => {
    if (requestedSeason && requestedWeek) return { season: requestedSeason, week: requestedWeek }
    return weeks?.current ?? null
  }, [requestedSeason, requestedWeek, weeks])

  const loader = useCallback(
    (signal: AbortSignal) => selected
      ? endpoints.nflWeek(selected.season, selected.week, signal)
      : Promise.reject(new Error('No NFL week is available')),
    [selected],
  )
  const overview = useApiResource<NflWeekOverview>(loader)
  const data = overview.data

  const index = weeks?.weeks.findIndex((w) => selected && w.season === selected.season && w.week === selected.week) ?? -1
  const previous = index > 0 ? weeks?.weeks[index - 1] : null
  const next = index >= 0 && weeks && index < weeks.weeks.length - 1 ? weeks.weeks[index + 1] : null
  const go = (season: number, week: number) => setParams({ season: String(season), week: String(week) }, { replace: true })

  const grouped = useMemo(() => {
    const map = new Map<string, NflWeekOverview['events']>()
    for (const event of data?.events ?? []) {
      const key = event.kickoff_utc ? event.kickoff_utc.slice(0, 10) : 'Unscheduled'
      map.set(key, [...(map.get(key) ?? []), event])
    }
    return [...map.entries()]
  }, [data])

  return (
    <div className="page nfl-page">
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to="/">Daily command center</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <span aria-current="page">NFL research</span>
      </nav>
      <PageHeader
        eyebrow="NFL · RESEARCH SHADOW"
        title={selected ? `${selected.season} season · Week ${selected.week}` : 'NFL research forecasts'}
        description="Imported Model Studio research forecasts for every scheduled game: win, tie and score expectations, evidence freshness, and a Simulation Lab per game. Research only — nothing here is executable or creates a position."
      />

      <div className="nfl-weeknav" role="group" aria-label="Week navigation">
        <button type="button" className="secondary-button" disabled={!previous} onClick={() => previous && go(previous.season, previous.week)}>
          <ChevronLeft size={14} aria-hidden="true" /> Week {previous?.week ?? '—'}
        </button>
        <label>
          Week
          <select value={selected ? `${selected.season}-${selected.week}` : ''} onChange={(e) => { const [s, w] = e.target.value.split('-').map(Number); go(s, w) }}>
            {(weeks?.weeks ?? []).map((w) => (
              <option key={`${w.season}-${w.week}`} value={`${w.season}-${w.week}`}>
                {w.season} · Week {w.week} · {w.events} games{w.first_kickoff_utc ? ` · from ${formatDate(w.first_kickoff_utc.slice(0, 10))}` : ''}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="secondary-button" disabled={!next} onClick={() => next && go(next.season, next.week)}>
          Week {next?.week ?? '—'} <ChevronRight size={14} aria-hidden="true" />
        </button>
        <Link className="command-header__labs" to="/labs?league=NFL"><Beaker size={14} aria-hidden="true" /> NFL Simulation Lab</Link>
      </div>

      {data?.consumer && <ConsumerStatusLine consumer={data.consumer} />}

      {!data && (
        <DataStatePanel
          state={overview.state}
          emptyTitle="No NFL week is available"
          emptyDetail="No research forecast has been imported yet."
          unavailableTitle="NFL week unavailable"
          unavailableDetail={overview.error ?? 'The NFL read model could not be reached.'}
          action={<button type="button" className="secondary-button" onClick={overview.refresh}>Retry</button>}
        />
      )}

      {data && (
        <>
          <p className="nfl-counts">
            {data.counts.events} games · {data.counts.upcoming} upcoming · {data.counts.started} started · {data.counts.final} final · {data.counts.with_joint_evidence} with joint score evidence · runtime clock {formatTimestamp(data.now_utc)}
          </p>
          {grouped.map(([day, events]) => (
            <section key={day} className="nfl-day" aria-label={day}>
              <h2>{day === 'Unscheduled' ? day : formatDate(day)}</h2>
              <div className="nfl-grid">
                {events.map((event) => <NflEventCard key={event.event_id} event={event} />)}
              </div>
            </section>
          ))}
          {data.events.length === 0 && <p className="labs-empty">No forecast has been imported for this week yet.</p>}
        </>
      )}
    </div>
  )
}
