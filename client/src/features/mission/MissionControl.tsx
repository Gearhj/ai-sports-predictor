/**
 * Mission Control: every game of the day as a pipeline it travels through,
 * live. One read (`/mission-control?date=`), refreshed every 30 s while the
 * tab is visible. Read-only: nothing here posts, places, or changes a bet.
 *
 * Honest by construction: a stage the server did not report renders as
 * UNKNOWN (striped, "?"), never as done; a failed read keeps the last good
 * view on screen and says so; a blocked stage shows its reason in words.
 */
import { GamePlanBar } from '../flow/GamePlanBar'
import { memo, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  Activity, Ban, BrainCircuit, Check, ChevronDown, CircleHelp, Clock3, Flame, Gauge, Minus, Radar, Radio,
  RefreshCw, Scale, ShieldCheck, Tag, Ticket, Trophy, Wallet,
} from 'lucide-react'
import { TeamLogo } from '../../components/TeamLogo'
import { RegionBoundary } from '../../components/command/RegionBoundary'
import { AsOf } from '../../components/command/RegionState'
import { useNow } from '../../components/activeWagers/useActiveWagers'
import { ScoreInline } from '../shared/ScoreBug'
import { gameHref } from '../../services/commandCenter'
import { operationalToday, resolveOperationalDate, shiftDate } from '../../services/operationalDate'
import {
  KICK_WORD, MISSION_SPORTS, NEXT_STAGE_KEY, SPORT_LABEL, STAGE_ABBR, STAGE_KEYS, STAGE_NAMES, STATE_WORD, SWEAT_WORDS,
  american, asSportFilter, bankrollStrip, buyWindowText, chipAria, forecastAgeSuffix, freshnessClock, chipLabel, dayLabel, etClock, filterBySport, isLive, isNetworkFailure, isSwitchedOff, jobSummary, liveScoreState,
  minutesText, nextStepLine, normalizeState, pct, progressIndex, showsTeamMark, sinceText, sortRows, sportCounts, stageOf, stageTone, tickPeriod, usd, useTick, whenText,
  useMissionControl,
  type ForecastStage, type HealthJob, type MissionAllowance, type MissionControl as MissionPayload, type MissionGame, type MissionHealth,
  type MissionSport, type SportFilter, type Stage, type StageKey, type SweatItem,
} from './missionData'
import '../pulse/pulse.css'
import './mission.css'

const STAGE_ICON: Record<StageKey, typeof Check> = {
  forecast: BrainCircuit, approval: ShieldCheck, prices: Tag, decision: Scale, bet: Ticket, live: Radio, result: Trophy,
}
const TONE_ICON = { done: Check, waiting: Clock3, blocked: Ban, na: Minus, unknown: CircleHelp } as const

const longDate = (day: string) =>
  new Intl.DateTimeFormat('en-US', { weekday: 'long', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${day}T12:00:00Z`))

/** "REST_INCOMPLETE_BACK_TO_BACK" -> "Rest incomplete back to back". */
export function humanCode(code: string | null | undefined): string {
  if (!code) return ''
  const words = code.replace(/_/g, ' ').toLowerCase().trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function MissionControl() {
  const [params, setParams] = useSearchParams()
  const now = useNow(30_000)
  const today = operationalToday(new Date(now))
  const day = resolveOperationalDate(params.get('date'))
  const resource = useMissionControl(day)
  const data = resource.data
  // The sport filter lives in the URL (`?sport=NHL`), so a reload or a shared link keeps it.
  const sport: SportFilter = asSportFilter(params.get('sport'))
  const [open, setOpen] = useState<string | null>(null)

  const setDay = (next: string) => {
    setParams((prev) => {
      const out = new URLSearchParams(prev)
      if (next === today) out.delete('date'); else out.set('date', next)
      return out
    }, { replace: true })
    setOpen(null)
  }
  const setSport = (next: SportFilter) => {
    setParams((prev) => {
      const out = new URLSearchParams(prev)
      if (next === 'ALL') out.delete('sport'); else out.set('sport', next)
      return out
    }, { replace: true })
  }
  // Stable, so memo(GameRow) skips rows whose game did not change.
  const onToggle = useCallback((id: string) => setOpen((current) => (current === id ? null : id)), [])

  const chips = useMemo(() => sportCounts(data), [data])
  // A sport chosen on another day that has no games here falls back to All.
  const activeSport: SportFilter = sport !== 'ALL' && !chips.some((c) => c.sport === sport) ? 'ALL' : sport
  const rows = useMemo(() => sortRows(filterBySport(data?.games ?? [], activeSport)), [data, activeSport])
  // null = the open-bets read failed (never shown as "no open bets").
  const sweatFailed = Boolean(data && (data.sweating == null || data.read_errors?.sweating))
  const sweating = useMemo(() => filterBySport(data?.sweating ?? [], activeSport), [data, activeSport])

  return (
    <div className="pulse mc">
      <GamePlanBar current="ready" />
      <RegionBoundary label="mission header">
        <Deck day={day} today={today} onDay={setDay} resource={resource} data={data} />
      </RegionBoundary>

      <div className="mc__body">
        <RegionBoundary label="bankroll strip">
          <BankrollStrip data={data} loading={resource.loading} />
        </RegionBoundary>

        <div className="mc__grid">
          <section className="mc__board" aria-labelledby="mc-board-title">
            <header className="mc__board-head">
              <div>
                <p className="pulse__eyebrow"><Radar size={13} /> The pipeline</p>
                <h2 id="mc-board-title">{dayLabel(day, today)} · {longDate(day)}</h2>
              </div>
              {chips.length > 0 && (
                <div className="pulse__chips mc__sports" role="group" aria-label="Sport filter">
                  <button type="button" aria-pressed={activeSport === 'ALL'} className={`pulse-chip ${activeSport === 'ALL' ? 'is-on' : ''}`} onClick={() => setSport('ALL')}>
                    All <b>{data?.games.length ?? 0}</b>
                  </button>
                  {chips.map((c) => (
                    <button key={c.sport} type="button" aria-pressed={activeSport === c.sport}
                      className={`pulse-chip ${activeSport === c.sport ? 'is-on' : ''} ${c.failed ? 'mc-chip--failed' : ''}`}
                      onClick={() => setSport(c.sport)} title={c.failed ? `${c.sport} could not be read` : undefined}>
                      {c.sport} <b>{c.failed && !c.games ? '!' : c.games}</b>
                    </button>
                  ))}
                </div>
              )}
            </header>
            <Legend />
            <RegionBoundary label="sport notices">
              <SportNotices data={data} sport={activeSport} />
            </RegionBoundary>
            <RegionBoundary label="game pipeline">
              <Board resource={resource} rows={rows} day={day} today={today} sport={activeSport} open={open} onToggle={onToggle} />
            </RegionBoundary>
          </section>

          {/* On a phone the rail sits above the board only when there is a bet to sweat. */}
          <aside className={`mc__rail ${data && !sweatFailed && sweating.length === 0 ? 'mc__rail--empty' : ''}`} aria-labelledby="mc-sweat-title">
            <RegionBoundary label="sweating rail">
              <SweatRail items={sweating} games={data?.games ?? []} loading={resource.loading && !data} failed={sweatFailed} hasData={Boolean(data)} />
            </RegionBoundary>
          </aside>
        </div>

        {data && (
          <p className="mc__authority">
            <ShieldCheck size={14} aria-hidden="true" /> {data.authority || 'Virtual research only.'}
            {' '}This page reads stored AISP records only; provider calls made to build it: {data.provider_calls_made ?? 'not reported'}.
          </p>
        )}
      </div>
    </div>
  )
}

// -------------------------------------------------------------------- deck --

type Resource = ReturnType<typeof useMissionControl>

function Deck({ day, today, onDay, resource, data }: {
  day: string; today: string; onDay: (d: string) => void; resource: Resource; data: MissionPayload | null
}) {
  // Yesterday (to read last night's results), today, tomorrow and the next day.
  const days = [shiftDate(today, -1), today, shiftDate(today, 1), shiftDate(today, 2)]
  const extra = days.includes(day) ? null : day
  return (
    <header className="mc-deck">
      <div className="mc-deck__top">
        <div className="mc-deck__title">
          <p className="mc-deck__kicker"><Radar size={14} /> Mission Control</p>
          <h1>Every game, every gate</h1>
          <p className="mc-deck__sub">Forecast → approval → prices → decision → bet → live → result, for every sport, as it happens.</p>
        </div>
        <Link to="/game-night" className="mc-deck__gn">Game Night ▶</Link>
        <LiveClock resource={resource} />
      </div>

      <div className="mc-deck__row">
        <nav className="mc-days" aria-label="Day">
          {days.map((d) => (
            <button key={d} type="button" aria-pressed={d === day} className={`mc-day ${d === day ? 'is-on' : ''}`} onClick={() => onDay(d)}>
              <span>{dayLabel(d, today)}</span><small>{new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${d}T12:00:00Z`))}</small>
            </button>
          ))}
          {extra && (
            <button type="button" aria-pressed className="mc-day is-on" onClick={() => onDay(extra)}>
              <span>{dayLabel(extra, today)}</span><small>{extra.slice(5)}</small>
            </button>
          )}
        </nav>
        <RegionBoundary label="system health">
          <HealthStrip health={data?.health ?? null} error={resource.error} hasData={Boolean(data)} loading={resource.loading} />
        </RegionBoundary>
      </div>

      <div className="mc-deck__row mc-deck__row--meters">
        <RegionBoundary label="shared daily limit">
          <AllowanceMeter allowance={data?.allowance ?? null} loading={resource.loading} today={today} />
        </RegionBoundary>
        <Counts data={data} />
      </div>
    </header>
  )
}

function LiveClock({ resource }: { resource: Resource }) {
  const now = useNow(1000)
  const asOf = freshnessClock(resource.asOf, resource.data?.generated_at)
  const since = asOf != null ? sinceText(asOf, now) : null
  const time = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(now)
  return (
    <div className="mc-clock">
      <span className="mc-clock__time" aria-label={`Eastern time ${time}`}>{time}<small>ET</small></span>
      <span className={`mc-clock__fresh ${resource.error ? 'is-error' : ''}`} role="status" aria-live="off">
        {resource.refreshing ? <RefreshCw size={12} className="mc-spin" aria-hidden="true" /> : <i className={`mc-heartbeat ${resource.error ? 'is-error' : ''}`} aria-hidden="true" />}
        {resource.data
          ? since != null ? `Updated ${since} ago` : 'Update time not reported'
          : resource.error ? 'Not loaded' : 'Connecting…'}
      </span>
      {/* With no view at all the board carries the one error and its Retry; here only a kept view is qualified. */}
      {resource.data != null && (resource.error || resource.restored) && <AsOf resource={resource} label="Mission Control" />}
    </div>
  )
}

const DOT_TONE: Record<string, string> = {
  OK: 'done', RUNNING: 'done', DEGRADED: 'waiting', STALE: 'waiting', NEVER_RUN: 'na', NOT_APPLICABLE: 'na',
  FAILED: 'blocked', DOWN: 'blocked', NOT_RUNNING: 'blocked', NOT_REGISTERED: 'blocked', MISSING: 'blocked', EXPIRED: 'blocked', DISABLED: 'waiting',
}

function HealthStrip({ health, error, hasData, loading }: { health: MissionHealth | null; error: string | null; hasData: boolean; loading: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const fetchFailed = Boolean(error)
  // Only a request that never reached the server means the backend is unreachable; an answer that was an
  // error (a 4xx/5xx, or a body that is not this read model) means the backend is up and the read failed.
  const unreachable = isNetworkFailure(error)
  if (!health) {
    return (
      <div className="mc-health" aria-label="System health">
        <span className={`mc-pill mc-pill--${fetchFailed ? 'blocked' : 'na'}`}>
          <i className="mc-dot" />{fetchFailed ? (unreachable ? 'Backend unreachable' : 'Health not read') : loading ? 'Checking backend…' : 'Health not reported'}
        </span>
      </div>
    )
  }
  const jobs = jobSummary(health.jobs)
  const troubled = jobs.failed + jobs.stale + jobs.unregistered
  const overall = fetchFailed && hasData ? 'DEGRADED' : health.overall
  const scheduler = health.scheduler?.state ?? 'NOT_RUNNING'
  return (
    <div className="mc-health" aria-label="System health">
      <span className={`mc-pill mc-pill--${DOT_TONE[overall] ?? 'unknown'}`} title={health.issues?.join(' ') || undefined}>
        <i className="mc-dot" />{overall === 'OK' ? 'All systems go' : overall === 'DEGRADED' ? 'Degraded' : 'Down'}
      </span>
      <span className={`mc-pill mc-pill--${fetchFailed ? 'blocked' : 'done'}`}>
        <i className="mc-dot" />{fetchFailed ? (unreachable ? 'Backend not answering · last view shown' : 'Refresh failed · last view shown') : 'Backend ready'}
      </span>
      <span className={`mc-pill mc-pill--${DOT_TONE[scheduler] ?? 'unknown'}`}>
        <i className="mc-dot" />{scheduler === 'RUNNING' ? 'Scheduler running' : scheduler === 'DISABLED' ? 'Scheduler disabled' : 'Scheduler not running'}
      </span>
      <ProducerDots producers={health.producers ?? []} />
      <button type="button" className={`mc-pill mc-pill--btn mc-pill--${troubled ? 'blocked' : jobs.total ? 'done' : 'na'}`} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <Activity size={12} aria-hidden="true" />
        {jobs.total ? `${jobs.ok}/${jobs.total} jobs OK` : 'No jobs reported'}
        {jobs.failed ? ` · ${jobs.failed} failed` : ''}{jobs.stale ? ` · ${jobs.stale} stale` : ''}{jobs.unregistered ? ` · ${jobs.unregistered} not registered` : ''}
        <ChevronDown size={12} className={expanded ? 'mc-rot' : ''} aria-hidden="true" />
      </button>
      {expanded && <HealthDetail health={health} />}
    </div>
  )
}

/** The word shown beside a producer that is not OK (colour alone never carries the state). */
const PRODUCER_WORD: Record<string, string> = {
  STALE: 'stale', MISSING: 'missing', FAILED: 'failed', EXPIRED: 'expired', NOT_APPLICABLE: 'n/a',
}

/** One dot per sport's forecast producer: the glanceable "did tonight's forecasts publish". */
function ProducerDots({ producers }: { producers: MissionHealth['producers'] }) {
  if (!producers.length) return <span className="mc-pill mc-pill--na"><i className="mc-dot" />Producers not reported</span>
  return (
    <span className="mc-pill mc-producers" role="group" aria-label="Forecast producers">
      <span className="mc-producers__label" aria-hidden="true">Producers</span>
      {producers.map((p) => {
        const word = PRODUCER_WORD[p.state] ?? (p.state === 'OK' ? null : humanCode(p.state).toLowerCase())
        return (
          <span key={`${p.sport}-${p.name}`} role="img" className={`mc-producer mc-pill--${DOT_TONE[p.state] ?? 'unknown'}`}
            aria-label={`${p.sport} producer: ${humanCode(p.state).toLowerCase()}${p.detail ? ` — ${p.detail}` : ''}`}
            title={`${p.name}: ${humanCode(p.state)}${p.detail ? ` · ${p.detail}` : ''}`}>
            <i className="mc-dot" aria-hidden="true" />{p.sport}{word && <b className="mc-producer__state">{word}</b>}
          </span>
        )
      })}
    </span>
  )
}

function HealthDetail({ health }: { health: MissionHealth }) {
  const now = useNow(30_000)
  const groups = [...MISSION_SPORTS, 'ALL' as const]
    .map((s) => ({ sport: s, jobs: (health.jobs ?? []).filter((j) => j.sport === s) }))
    .filter((g) => g.jobs.length)
  return (
    <div className="mc-health__detail">
      {health.issues?.length > 0 && (
        <ul className="mc-issues">{health.issues.map((i) => <li key={i}>{i}</li>)}</ul>
      )}
      <div className="mc-jobs">
        {groups.map((g) => (
          <div key={g.sport} className="mc-jobs__group">
            <h4>{g.sport === 'ALL' ? 'Shared' : g.sport}</h4>
            <ul>{g.jobs.map((j) => <JobLine key={j.key} job={j} now={now} />)}</ul>
          </div>
        ))}
        {(health.producers ?? []).length > 0 && (
          <div className="mc-jobs__group">
            <h4>Forecast producers</h4>
            <ul>
              {health.producers.map((p) => (
                <li key={`${p.sport}-${p.name}`} className="mc-job">
                  <i className={`mc-dot mc-dot--${DOT_TONE[p.state] ?? 'unknown'}`} aria-hidden="true" />
                  <span><b>{p.sport}</b> {p.name}<small>{humanCode(p.state)}{p.last_published_at ? ` · published ${sinceText(p.last_published_at, now)} ago` : ''}{p.valid_until ? ` · valid until ${etClock(p.valid_until, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}` : ''}{p.detail ? ` · ${p.detail}` : ''}</small></span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}

function JobLine({ job, now }: { job: HealthJob; now: number }) {
  const ok = job.last_success_at ? `last OK ${sinceText(job.last_success_at, now)} ago` : 'no success in 3 days'
  // A lane settings leave off is not a fault; say so instead of painting it red.
  const off = isSwitchedOff(job)
  return (
    <li className="mc-job">
      <i className={`mc-dot mc-dot--${off ? 'na' : DOT_TONE[job.state] ?? 'unknown'}`} aria-hidden="true" />
      <span>{job.label || job.job_name}<small>{off ? 'Switched off in settings' : humanCode(job.state)} · {ok}{job.next_run_at ? ` · next ${etClock(job.next_run_at)}` : ''}</small></span>
    </li>
  )
}

/** "Today's", "Yesterday's", "Tomorrow's", else "Sun Oct 18" -- the meter is for the selected slate, not always today. */
function slateWords(slate: string | null | undefined, today: string): string {
  if (!slate) return "This slate's"
  const label = dayLabel(slate, today)
  if (['Today', 'Yesterday', 'Tomorrow'].includes(label)) return `${label}'s`
  return new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${slate}T12:00:00Z`))
}

function AllowanceMeter({ allowance, loading, today }: { allowance: MissionAllowance | null; loading: boolean; today: string }) {
  if (!allowance) {
    return <div className="mc-meter mc-meter--missing"><p className="mc-meter__title"><Gauge size={13} /> Shared daily limit</p><p className="mc-meter__note">{loading ? 'Loading…' : 'Not reported'}</p></div>
  }
  // A failed read is unknown: never "not in force", never $0 entered.
  if (allowance.state === 'UNKNOWN' || allowance.read_state === 'READ_FAILED' || allowance.in_force == null) {
    return (
      <div className="mc-meter mc-meter--unknown" role="group" aria-label="Shared daily limit">
        <p className="mc-meter__title"><Gauge size={13} /> Shared daily limit</p>
        <p className="mc-meter__note">Could not be read{allowance.error_type ? ` (${allowance.error_type})` : ''}; entered and room are unknown.</p>
      </div>
    )
  }
  const limit = allowance.limit_usd || 100
  const entered = Math.max(0, allowance.entered_usd ?? 0)
  const share = Math.min(1, entered / limit)
  const tone = !allowance.in_force || allowance.state === 'NOT_IN_FORCE' ? 'na' : allowance.state === 'FULL' ? 'blocked' : share >= 0.8 ? 'waiting' : 'done'
  return (
    <div className={`mc-meter mc-meter--${tone}`} role="group" aria-label={`Shared daily limit ${usd(limit)}`}>
      <p className="mc-meter__title"><Gauge size={13} /> {slateWords(allowance.slate_date, today)} shared {usd(limit).replace('.00', '')}</p>
      {allowance.in_force && allowance.state !== 'NOT_IN_FORCE' ? (
        <>
          <div className="mc-meter__bar" role="meter" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={entered} aria-label={`Entered ${usd(entered)} of ${usd(limit)}`}>
            <span style={{ width: `${share * 100}%` }} />
          </div>
          <p className="mc-meter__note">
            <b>{usd(entered)}</b> entered · <b>{usd(Math.max(0, allowance.room_usd ?? limit - entered))}</b> room · {allowance.entries ?? '?'} {allowance.entries === 1 ? 'bet' : 'bets'}
            {allowance.state === 'FULL' && <span className="mc-meter__full"> · Full for this slate</span>}
          </p>
          {(allowance.ambiguous_entries ?? 0) > 0 && <p className="mc-meter__warn">{allowance.ambiguous_entries} {allowance.ambiguous_entries === 1 ? 'entry' : 'entries'} could not be placed on a slate date cleanly.</p>}
        </>
      ) : (
        <p className="mc-meter__note">Not in force for this slate{allowance.effective_from ? ` · starts ${etClock(allowance.effective_from, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} ET` : ''}.</p>
      )}
    </div>
  )
}

function Counts({ data }: { data: MissionPayload | null }) {
  const c = data?.counts
  const cell = (label: string, value: ReactNode, tone = '') => <div className={tone}><dt>{label}</dt><dd>{value}</dd></div>
  return (
    <dl className="mc-counts" aria-label="Slate counts">
      {cell('Games', c ? c.games : '—')}
      {cell('Live', c ? <>{c.live > 0 && <i className="live-dot" />}{c.live}</> : '—')}
      {cell('Open bets', c ? c.bets_open : '—')}
      {cell('Blocked', c ? c.blocked : '—', c && c.blocked > 0 ? 'is-blocked' : '')}
      {cell('Waiting', c ? c.waiting : '—')}
      {cell('Staked', c ? usd(c.staked_usd) : '—')}
      {cell('P/L', c ? usd(c.profit_usd, true) : '—', c ? (c.profit_usd > 0 ? 'is-up' : c.profit_usd < 0 ? 'is-down' : '') : '')}
    </dl>
  )
}

// --------------------------------------------------------------- bankrolls --

function BankrollStrip({ data, loading }: { data: MissionPayload | null; loading: boolean }) {
  const cards = bankrollStrip(data)
  return (
    <section className="mc-banks" aria-label="Bankrolls by sport">
      {cards.map((b) => {
        if ('missing' in b) {
          return (
            <div key={b.sport} className="mc-bank mc-bank--missing" aria-label={`${b.sport} bankroll`}>
              <span className="mc-bank__sport"><SportTag sport={b.sport} /></span>
              <strong>{loading ? '…' : '—'}</strong>
              <small>{loading ? 'Loading' : 'Not reported'}</small>
            </div>
          )
        }
        if (b.read_state === 'READ_FAILED' || b.funded == null) {
          return (
            <div key={b.sport} className="mc-bank mc-bank--missing" aria-label={`${b.label} bankroll`}>
              <span className="mc-bank__sport"><SportTag sport={b.sport} /><Wallet size={12} aria-hidden="true" /></span>
              <strong>Unknown</strong>
              <small>Could not be read{b.error_type ? ` (${b.error_type})` : ''}</small>
            </div>
          )
        }
        const profit = b.profit_usd ?? null
        return (
          <div key={b.sport} className={`mc-bank ${b.funded ? '' : 'mc-bank--unfunded'}`} aria-label={`${b.label} bankroll`}>
            <span className="mc-bank__sport"><SportTag sport={b.sport} /><Wallet size={12} aria-hidden="true" /></span>
            <strong>{b.funded ? usd(b.bankroll_usd) : 'Not funded'}</strong>
            {b.funded
              ? <small><span className={profit != null && profit > 0 ? 'is-up' : profit != null && profit < 0 ? 'is-down' : ''}>{usd(profit, true)}</span><span className="mc-bank__base"> on {usd(b.starting_usd).replace('.00', '')}</span> · {usd(b.open_exposure_usd)} at risk</small>
              : <small>{b.effective_on ? `Opens ${b.effective_on}` : 'No account for this sport'}</small>}
          </div>
        )
      })}
    </section>
  )
}

function SportTag({ sport }: { sport: MissionSport | string }) {
  return <span className={`mc-sport mc-sport--${String(sport).toLowerCase()}`}>{sport}</span>
}

// ------------------------------------------------------------------ legend --

function Legend() {
  const items: [string, string][] = [['done', 'Done'], ['waiting', 'Waiting'], ['blocked', 'Blocked'], ['na', 'Not applicable'], ['unknown', 'Unknown']]
  return (
    <ul className="mc-legend" aria-label="Stage colours">
      {items.map(([tone, word]) => {
        const Icon = TONE_ICON[tone as keyof typeof TONE_ICON]
        return <li key={tone} className={`mc-legend__item mc-tone--${tone}`}><Icon size={11} aria-hidden="true" />{word}</li>
      })}
    </ul>
  )
}

// ----------------------------------------------------------- sport notices --

/** "7:30 PM", or "Sat 7:30 PM" when the start falls on another ET day than the board's. */
function startClock(iso: string, day: string) {
  const sameDay = Boolean(iso) && operationalToday(new Date(iso)) === day
  return etClock(iso, sameDay ? undefined : { weekday: 'short', hour: 'numeric', minute: '2-digit' })
}

function SportNotices({ data, sport }: { data: MissionPayload | null; sport: SportFilter }) {
  if (!data) return null
  const list = data.sports.filter((s) => sport === 'ALL' || s.sport === sport)
  const notes = list.flatMap((s) => {
    const out: { key: string; tone: string; text: ReactNode }[] = []
    if (s.read_state === 'READ_FAILED') {
      out.push({ key: `${s.sport}-failed`, tone: 'blocked', text: <><b>{s.label || s.sport}</b> could not be read{s.error_type ? ` (${s.error_type})` : ''}. Its games are missing from this view; the other sports are unaffected.</> })
    }
    const pending = s.pending_games ?? []
    if (pending.length) {
      const reasons = [...new Set(pending.map((p) => p.reason))]
      const rest = reasons.some((r) => r.startsWith('REST_INCOMPLETE'))
      out.push({
        key: `${s.sport}-pending`, tone: 'waiting',
        text: <><b>{pending.length} more {s.label || s.sport} {pending.length === 1 ? 'game' : 'games'}</b> not forecast yet ({pending.map((p) => startClock(p.starts_at, data.date)).join(', ')} ET).{' '}
          {rest ? 'A back-to-back team’s rest is only complete on game day, so the game-day forecast run adds these.' : reasons.map(humanCode).join('; ')}</>,
      })
    }
    // How a sport's day works, only for sports on this board (or the one picked): five notes on a one-sport day is noise.
    if (s.schedule_note && (s.games > 0 || sport === s.sport)) out.push({ key: `${s.sport}-note`, tone: 'note', text: <><b>{s.label || s.sport}:</b> {s.schedule_note}</> })
    return out
  })
  const alerts = notes.filter((n) => n.tone !== 'note')
  const howto = notes.filter((n) => n.tone === 'note')
  if (!notes.length) return null
  return (
    <>
      {alerts.length > 0 && (
        <ul className="mc-notices">
          {alerts.map((n) => <li key={n.key} className={`mc-notice mc-tone--${n.tone}`}>{n.text}</li>)}
        </ul>
      )}
      {howto.length > 0 && <HowToday notes={howto} />}
    </>
  )
}

const PHONE = '(max-width: 760px)'
const isPhone = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(PHONE).matches

/** How each sport's day works: open on a wide screen, one tap away on a phone so the games come first. */
function HowToday({ notes }: { notes: { key: string; text: ReactNode }[] }) {
  const [open, setOpen] = useState(() => !isPhone())
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia(PHONE)
    const follow = () => setOpen(!mql.matches)
    mql.addEventListener?.('change', follow)
    return () => mql.removeEventListener?.('change', follow)
  }, [])
  return (
    <details className="mc-howto" open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>How today works <small>({notes.length} {notes.length === 1 ? 'note' : 'notes'})</small></summary>
      <ul className="mc-notices">
        {notes.map((n) => <li key={n.key} className="mc-notice mc-notice--note">{n.text}</li>)}
      </ul>
    </details>
  )
}

// ------------------------------------------------------------------- board --

function Board({ resource, rows, day, today, sport, open, onToggle }: {
  resource: Resource; rows: MissionGame[]; day: string; today: string; sport: SportFilter; open: string | null; onToggle: (id: string) => void
}) {
  const data = resource.data
  if (!data) {
    if (resource.error) {
      // The one place the error is spelled out, with the one Retry.
      const unreachable = isNetworkFailure(resource.error)
      return (
        <div className="mc-empty mc-empty--error" role="alert">
          <Ban size={18} aria-hidden="true" />
          <div>
            <strong>{unreachable ? 'The backend could not be reached.' : 'Mission Control could not be read.'}</strong>
            <p>{resource.error}</p>
            <button type="button" className="mc-retry" onClick={resource.refresh}><RefreshCw size={13} aria-hidden="true" /> Retry</button>
          </div>
        </div>
      )
    }
    return (
      <div className="mc-rows" aria-busy="true" aria-label="Loading games">
        {[0, 1, 2, 3].map((i) => <div key={i} className="pulse-skeleton mc-skel" />)}
      </div>
    )
  }
  if (!data.games.length) {
    const next = data.sports.filter((s) => s.next_event_at).sort((a, b) => Date.parse(a.next_event_at!) - Date.parse(b.next_event_at!))[0]
    return (
      <div className="mc-empty">
        <Radar size={18} aria-hidden="true" />
        <div>
          <strong>No games on the board for {dayLabel(day, today).toLowerCase() === 'today' ? 'today' : longDate(day)}.</strong>
          <p>{next ? `Next up: ${next.label || next.sport} on ${etClock(next.next_event_at, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} ET.` : 'No upcoming game is scheduled in AISP for this window.'}</p>
        </div>
      </div>
    )
  }
  if (!rows.length) {
    return <div className="mc-empty"><Radar size={18} aria-hidden="true" /><div><strong>No {sport === 'ALL' ? '' : `${SPORT_LABEL[sport]} `}games on this day.</strong></div></div>
  }
  return (
    <div className="mc-rows">
      {rows.map((g) => <GameRow key={g.game_id} game={g} open={open === g.game_id} onToggle={onToggle} />)}
    </div>
  )
}

const GameRow = memo(function GameRow({ game, open, onToggle }: { game: MissionGame; open: boolean; onToggle: (id: string) => void }) {
  const live = isLive(game)
  const final = game.phase === 'FINAL' || game.status === 'final'
  const voided = game.phase === 'VOID' || game.status === 'postponed' || game.status === 'canceled'
  const progress = progressIndex(game)
  const current = game.next_step ? NEXT_STAGE_KEY[game.next_step.stage] ?? null : null
  const attention = game.attention ?? 'NONE'
  const score = liveScoreState(game.stages?.live)
  const result = game.stages?.result
  const href = gameHref(game.sport, game.source_game_id ?? game.game_id)
  const detailId = `mc-detail-${game.game_id}`
  return (
    <article id={`mc-game-${game.game_id}`} className={`mc-row mc-row--${attention.toLowerCase()} ${live ? 'is-live' : ''} ${final || voided ? 'is-done' : ''}`}
      aria-label={`${game.matchup}, ${SPORT_LABEL[game.sport] ?? game.sport}`}>
      <div className="mc-row__head">
        <div className="mc-row__game">
          <SportTag sport={game.sport} />
          <span className="mc-row__time">{live ? <><i className="live-dot" />LIVE</> : voided ? humanCode(game.status) : etClock(game.starts_at)}</span>
          <span className="mc-row__teams">
            {/* A school name (College Football) does not fit a 22 px mark: the name alone reads better. */}
            {showsTeamMark(game.away.abbr) && <TeamLogo league={game.sport} abbreviation={game.away.abbr} size={22} markOnly />}
            <Link to={href} className="mc-row__matchup">{game.away.abbr} <em>@</em> {game.home.abbr}</Link>
            {showsTeamMark(game.home.abbr) && <TeamLogo league={game.sport} abbreviation={game.home.abbr} size={22} markOnly />}
          </span>
          {(score.phase === 'LIVE' || score.phase === 'FINAL') && (
            <ScoreInline state={score} away={game.away.abbr} home={game.home.abbr} startsAt={game.starts_at} />
          )}
          {final && result?.final && score.phase !== 'FINAL' && (
            <span className="sb-inline sb-inline--final"><span>{game.away.abbr} {result.final.away}</span><span className="sb-inline__sep">·</span><span>{game.home.abbr} {result.final.home}</span><em>{result.final.label || 'Final'}</em></span>
          )}
        </div>
        <NextStepView game={game} />
        <button type="button" className="mc-row__toggle" aria-expanded={open} aria-controls={detailId} onClick={() => onToggle(game.game_id)}
          aria-label={`${open ? 'Hide' : 'Show'} stage details for ${game.matchup}`}>
          <ChevronDown size={16} className={open ? 'mc-rot' : ''} aria-hidden="true" />
        </button>
      </div>
      {/* A tap anywhere on the pipeline opens the stage details; keyboard users have the toggle button. */}
      <ol className="mc-pipe" aria-label={`Pipeline for ${game.matchup}`} onClick={() => onToggle(game.game_id)}>
        {STAGE_KEYS.map((key, i) => (
          <StageChip key={key} stageKey={key} stage={stageOf(game, key)} current={key === current} passed={i <= progress} />
        ))}
      </ol>
      {open && <RowDetail id={detailId} game={game} />}
    </article>
  )
})

function StageChip({ stageKey, stage, current, passed }: { stageKey: StageKey; stage: Stage; current: boolean; passed: boolean }) {
  const tone = stageTone(stage.state)
  const Icon = STAGE_ICON[stageKey]
  const Mark = TONE_ICON[tone]
  const label = chipLabel(stageKey, stage)
  // A forecast that is not fresh says how old it is on the chip itself, not only in the details.
  const age = stageKey === 'forecast' && tone === 'done' ? forecastAgeSuffix(stage as ForecastStage) : null
  return (
    <li className={`mc-chip mc-tone--${tone} ${current ? 'is-current' : ''} ${passed ? 'is-passed' : ''} ${age ? 'is-aged' : ''}`} aria-label={chipAria(stageKey, stage)} title={stage.detail || label}>
      <span className="mc-chip__stage"><Icon size={12} aria-hidden="true" /><span className="mc-chip__name">{STAGE_NAMES[stageKey]}</span><span className="mc-chip__abbr">{STAGE_ABBR[stageKey]}</span></span>
      <span className="mc-chip__label"><Mark size={11} aria-hidden="true" className="mc-chip__mark" /><span>{label}{age && <em className="mc-chip__age"> · {age} old</em>}</span></span>
    </li>
  )
}

function NextStepView({ game }: { game: MissionGame }) {
  const step = game.next_step
  const at = step?.kind === 'WAIT' && step.at ? Date.parse(step.at) : NaN
  // One shared clock: every second only while the step is under eleven minutes away; 15 s further out;
  // 30 s once overdue; hourly for a step without a clock.
  const now = useTick(tickPeriod(at, Date.now()))
  const line = nextStepLine(step, now)
  const kind = step?.kind ?? 'UNKNOWN'
  const KindIcon = kind === 'BLOCKED' ? Ban : kind === 'WATCH' ? Radio : kind === 'DONE' ? Check : kind === 'WAIT' ? Clock3 : CircleHelp
  return (
    <div className={`mc-next mc-next--${kind.toLowerCase()} ${line.overdue ? 'is-overdue' : ''}`} aria-label={`Next step: ${line.headline}${line.sub ? `. ${line.sub}` : ''}`}>
      <span className="mc-next__headline"><KindIcon size={13} aria-hidden="true" />{line.headline}</span>
      {line.sub && <small>{line.sub}</small>}
    </div>
  )
}

// -------------------------------------------------------------- row detail --

function when(iso: string | null | undefined) {
  return whenText(iso)
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  if (value == null || value === '' || value === false) return null
  return <div><dt>{label}</dt><dd>{value}</dd></div>
}

function RowDetail({ id, game }: { id: string; game: MissionGame }) {
  const f = stageOf(game, 'forecast')
  const a = stageOf(game, 'approval')
  const p = stageOf(game, 'prices')
  const d = stageOf(game, 'decision')
  const b = stageOf(game, 'bet')
  const l = stageOf(game, 'live')
  const r = stageOf(game, 'result')
  const c = game.clocks ?? {}
  const facts: Record<StageKey, ReactNode> = {
    forecast: <>
      <Fact label="Source" value={f.source} />
      <Fact label="Made" value={f.made_at ? `${when(f.made_at)}${f.made_at_basis ? ` · ${humanCode(f.made_at_basis)}` : ''}` : null} />
      <Fact label="Freshness" value={f.freshness ? `${humanCode(f.freshness)}${f.age_minutes != null ? ` · ${minutesText(f.age_minutes)} old` : ''}` : null} />
      <Fact label="Win prob" value={f.home_win_prob != null || f.away_win_prob != null ? `${game.away.abbr} ${pct(f.away_win_prob)} · ${game.home.abbr} ${pct(f.home_win_prob)}` : null} />
      <Fact label="Snapshot" value={f.snapshot ? humanCode(f.snapshot) : null} />
    </>,
    approval: <>
      <Fact label="Route" value={a.route ? humanCode(a.route) : null} />
      <Fact label="Eligible" value={a.eligible == null ? null : a.eligible ? 'Yes' : 'No'} />
      <Fact label="Valid until" value={when(a.valid_until)} />
    </>,
    prices: <>
      <Fact label={`${game.away.abbr} best`} value={p.best?.away ? `${american(p.best.away.american)}${p.best.away.sportsbook ? ` · ${p.best.away.sportsbook}` : ''}` : null} />
      <Fact label={`${game.home.abbr} best`} value={p.best?.home ? `${american(p.best.home.american)}${p.best.home.sportsbook ? ` · ${p.best.home.sportsbook}` : ''}` : null} />
      <Fact label="Books" value={p.books != null ? `${p.fresh_books ?? 0} fresh of ${p.books}` : null} />
      <Fact label="Quote age" value={minutesText(p.quote_age_minutes)} />
      <Fact label="Consensus" value={p.consensus_home != null ? `${game.home.abbr} ${pct(p.consensus_home, 1)} (no-vig)` : null} />
      <Fact label="Buy window" value={buyWindowText(c.buy_opens_at, c.buy_closes_at)} />
      <Fact label="Collector" value={p.collector ? humanCode(p.collector) : null} />
    </>,
    decision: <>
      <Fact label="Call" value={d.class ? humanCode(d.class) : null} />
      <Fact label="Recommendation" value={d.recommendation} />
      <Fact label="Reason" value={d.reason_code ? humanCode(d.reason_code) : null} />
      <Fact label="Window" value={c.decision_opens_at ? `${etClock(c.decision_opens_at)}–${etClock(c.decision_closes_at)} ET` : null} />
      <Fact label="Model vs market" value={d.model_probability != null && d.market_probability != null ? `${pct(d.model_probability, 1)} vs ${pct(d.market_probability, 1)}` : null} />
      <Fact label="Edge" value={d.edge != null ? `${(d.edge * 100).toFixed(1)} pts` : null} />
      <Fact label="Recorded" value={when(d.recorded_at)} />
    </>,
    bet: <>
      <Fact label="Side" value={b.side ? `${b.side} ${american(b.american)}` : null} />
      <Fact label="Stake" value={b.stake_usd != null ? `${usd(b.stake_usd)} to win ${usd(b.to_win_usd)}` : null} />
      <Fact label="Book" value={b.sportsbook} />
      <Fact label="Placed" value={when(b.placed_at)} />
      <Fact label="Status" value={b.display_status || (b.status ? humanCode(b.status) : null)} />
    </>,
    live: <>
      <Fact label="Score" value={l.available && l.away_score != null ? `${game.away.abbr} ${l.away_score} · ${game.home.abbr} ${l.home_score}` : null} />
      <Fact label="Period" value={l.period_label} />
      <Fact label="Feed" value={l.freshness ? humanCode(l.freshness) : null} />
      <Fact label="Checked" value={when(l.last_checked_at)} />
    </>,
    result: <>
      <Fact label="Outcome" value={r.outcome ? humanCode(r.outcome) : null} />
      <Fact label="Profit" value={r.profit_usd != null ? usd(r.profit_usd, true) : null} />
      <Fact label="Final" value={r.final ? `${game.away.abbr} ${r.final.away} · ${game.home.abbr} ${r.final.home}${r.final.label ? ` (${r.final.label})` : ''}` : null} />
      <Fact label="Settled" value={when(r.settled_at)} />
      <Fact label="Settlement" value={r.awaiting_settlement ? 'Awaiting settlement' : null} />
    </>,
  }
  const stages = { forecast: f, approval: a, prices: p, decision: d, bet: b, live: l, result: r } as Record<StageKey, Stage>
  return (
    <div id={id} className="mc-detail">
      <ol className="mc-detail__list">
        {STAGE_KEYS.map((key) => {
          const s = stages[key]
          const state = normalizeState(s.state)
          const tone = stageTone(state)
          const Mark = TONE_ICON[tone]
          return (
            <li key={key} className={`mc-detail__stage mc-tone--${tone}`}>
              <span className="mc-detail__mark"><Mark size={12} aria-hidden="true" /></span>
              <div>
                <p className="mc-detail__head"><b>{STAGE_NAMES[key]}</b> <span>{STATE_WORD[state]}</span>{s.code ? <code>{s.code}</code> : null}</p>
                <p className="mc-detail__text">{s.detail || chipLabel(key, s)}</p>
                {state === 'WAITING' && s.due_at && <p className="mc-detail__clock">Due {when(s.due_at)}</p>}
                {state === 'DONE' && s.at && <p className="mc-detail__clock">At {when(s.at)}</p>}
                <dl className="mc-facts">{facts[key]}</dl>
              </div>
            </li>
          )
        })}
      </ol>
      <p className="mc-detail__foot">
        {KICK_WORD[game.sport] ?? 'Start'} {when(game.starts_at)} · <Link to={gameHref(game.sport, game.source_game_id ?? game.game_id)}>Open game page</Link>
      </p>
    </div>
  )
}

// ------------------------------------------------------------- sweat rail --

const SWEAT_ORDER: Record<string, number> = { LIVE: 0, STARTED_NO_LIVE_FEED: 1, FINAL_AWAITING_SETTLEMENT: 2, VOID_AWAITING_SETTLEMENT: 3, UPCOMING: 4 }

function SweatRail({ items, games, loading, failed, hasData }: { items: SweatItem[]; games: MissionGame[]; loading: boolean; failed: boolean; hasData: boolean }) {
  const sorted = [...items].sort((a, b) => (SWEAT_ORDER[a.state] ?? 9) - (SWEAT_ORDER[b.state] ?? 9) || Date.parse(a.starts_at) - Date.parse(b.starts_at))
  const live = items.filter((i) => i.state === 'LIVE').length
  const atRisk = items.reduce((sum, i) => sum + (i.stake_usd ?? 0), 0)
  const byId = new Map(games.map((g) => [g.game_id, g]))
  return (
    <div className="mc-sweat">
      <header className="mc-sweat__head">
        <h2 id="mc-sweat-title"><Flame size={15} aria-hidden="true" /> Sweating now</h2>
        {items.length > 0 && <span>{live > 0 && <><i className="live-dot" />{live} live · </>}{usd(atRisk)} at risk</span>}
      </header>
      {loading && <div className="pulse-skeleton" style={{ height: 120 }} />}
      {/* A failed read is never "no open bets": that sentence must be a fact. */}
      {!loading && failed && <p className="mc-sweat__empty mc-sweat__empty--failed" role="status">Open bets could not be read. Any open bet is still riding; this list is unknown, not empty.</p>}
      {!loading && !failed && !hasData && <p className="mc-sweat__empty">Open bets appear here once Mission Control loads.</p>}
      {!loading && !failed && hasData && sorted.length === 0 && <p className="mc-sweat__empty">No open bets right now. When a decision window places one, it shows up here with its live score.</p>}
      <ul className="mc-sweat__list">
        {sorted.map((i) => {
          const game = byId.get(i.game_id)
          const [away, home] = game ? [game.away.abbr, game.home.abbr] : (i.matchup ?? '').split('@').map((s) => s.trim())
          const score = liveScoreState(i.live)
          return (
            <li key={i.position_id} className={`mc-sweat__item ${i.state === 'LIVE' ? 'is-live' : ''}`}>
              <Link to={gameHref(i.sport, game?.source_game_id ?? i.source_game_id ?? i.game_id)}>
                <span className="mc-sweat__top"><SportTag sport={i.sport} /><b>{i.selection}</b><em title={i.sportsbook ? `at ${i.sportsbook}` : undefined}>{american(i.american)}</em></span>
                <span className="mc-sweat__game">
                  {score.phase === 'LIVE' || score.phase === 'FINAL'
                    ? <ScoreInline state={score} away={away || 'Away'} home={home || 'Home'} startsAt={i.starts_at} />
                    : <span className="mc-sweat__matchup">{i.matchup}</span>}
                </span>
                <span className="mc-sweat__foot">
                  <span className={`mc-sweat__state mc-sweat__state--${i.state.toLowerCase()}`}>
                    {i.state === 'UPCOMING' ? `${KICK_WORD[i.sport] ?? 'Start'} ${startClock(i.starts_at, operationalToday())}` : SWEAT_WORDS[i.state] ?? humanCode(i.state)}
                  </span>
                  <span>{usd(i.stake_usd)} → <b>{usd(i.to_win_usd)}</b></span>
                </span>
              </Link>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export default MissionControl
