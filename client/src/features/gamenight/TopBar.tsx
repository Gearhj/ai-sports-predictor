/**
 * The broadcast bar: the ON AIR / STANDBY bug, the ET clock, where every
 * number on the wall came from and how old it is, the scheduler's next
 * settlement and price runs, and the wall's own controls.
 *
 * Every per-second clock lives in its own tiny component, so a 1 Hz tick
 * re-renders a few characters and never the wall.
 */
import { memo } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Maximize, Minimize, Pause, Play, RotateCcw, Sun, Tv } from 'lucide-react'
import { freshnessClock, useTick, type HealthJob } from '../mission/missionData'

/**
 * Mission Control is served from a cache that is fresh for 30 s and may be up
 * to 90 s old, and the wall polls it every 30 s: a healthy reading can be
 * 90 s + one poll + a slow rebuild old. Stale means past all of that.
 */
export const MISSION_STALE_AFTER_MS = 135_000
export const LEDGER_STALE_AFTER_MS = 15 * 60_000
/** The decision board polls every 120 s. */
export const DECISIONS_STALE_AFTER_MS = 5 * 60_000

/** Whether the board every "none" and "not scheduled" depends on has been read. */
export type BoardState = 'loading' | 'ready' | 'failed'

const ET_TIME = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', second: '2-digit' })
const ET_SHORT = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' })

export const etSeconds = (ms: number) => ET_TIME.format(ms)
export const etShort = (ms: number) => ET_SHORT.format(ms)

/** "0:42", "21:10", "1:05:09": a countdown that reads at a glance on a TV. */
export function clockText(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`
}

/** "72 s", "4 min", "2 h". */
export function agoText(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 120) return `${s} s`
  if (s < 2 * 3600) return `${Math.round(s / 60)} min`
  return `${Math.round(s / 3600)} h`
}

export interface FreshResource {
  data: unknown
  error: string | null
  asOf: number | null
  restored: boolean
  loading: boolean
}

/** One as-of chip: amber with the word "stale" past its window; "unknown", never zero, on a failed read. */
export const AsOfChip = memo(function AsOfChip({ label, resource, generatedAt, staleAfterMs }: {
  label: string; resource: FreshResource; generatedAt?: string | null; staleAfterMs: number
}) {
  const now = useTick(5000)
  if (resource.data == null) {
    return (
      <span className={`gn-chip ${resource.error ? 'gn-chip--stale' : ''}`} role="status">
        {label} {resource.error ? 'unknown' : 'loading…'}
      </span>
    )
  }
  const asOf = freshnessClock(resource.asOf, generatedAt ?? null)
  const stale = Boolean(resource.error) || asOf == null || now - asOf > staleAfterMs
  const at = asOf != null ? etSeconds(asOf) : 'an unrecorded time'
  return (
    <span className={`gn-chip ${stale ? 'gn-chip--stale' : ''}`} title={`${label}: as of ${at} ET${resource.error ? ` · last refresh failed (${resource.error})` : ''}`}>
      {label} {resource.restored ? <>last saved view, as of {at}</> : at}
      {stale && <b className="gn-chip__flag">stale</b>}
    </span>
  )
})

export interface ScoreFreshness {
  /** Newest scoreboard check among the games read, epoch ms. */
  checkedAt: number | null
  /** A live game whose reading is not CURRENT. */
  stale: boolean
  failed: boolean
  reading: boolean
}

export const ScoresChip = memo(function ScoresChip({ fresh, board }: { fresh: ScoreFreshness; board: BoardState }) {
  const now = useTick(1000)
  // Which games to read comes from the board: until it is read, "none in play" would be a guess.
  if (board === 'loading') return <span className="gn-chip">Scores …</span>
  if (board === 'failed' && !fresh.reading) return <span className="gn-chip gn-chip--stale" role="status">Scores unknown</span>
  if (!fresh.reading) return <span className="gn-chip" title="No held game has started; no score is read.">Scores: none in play</span>
  if (fresh.failed && fresh.checkedAt == null) return <span className="gn-chip gn-chip--stale" role="status">Scores unknown</span>
  if (fresh.checkedAt == null) return <span className="gn-chip">Scores loading…</span>
  return (
    <span className={`gn-chip ${fresh.stale || fresh.failed ? 'gn-chip--stale' : ''}`}
      title="When AISP last checked the source. The source publishes no score timestamp, so its own latency is unknown.">
      Scores checked {agoText(now - fresh.checkedAt)} ago{(fresh.stale || fresh.failed) && <b className="gn-chip__flag">stale</b>}
    </span>
  )
})

/** The scheduler's next run of a job role, as a countdown pill. */
export const JobPill = memo(function JobPill({ label, at, board }: { label: string; at: string | null; board: BoardState }) {
  const now = useTick(1000)
  // The schedule is read from Mission Control: before that read, "not scheduled" would be a false claim.
  if (board !== 'ready') {
    return <span className="gn-pill" title="Read from Mission Control's job health.">{label} <b>{board === 'loading' ? '…' : 'unknown'}</b></span>
  }
  const ms = at ? Date.parse(at) - now : NaN
  return (
    <span className="gn-pill" title="Scheduler next run, not a promise.">
      {label} {Number.isFinite(ms) ? (ms > 0 ? <>in <b>{clockText(ms)}</b></> : <b>due now</b>) : <b>not scheduled</b>}
    </span>
  )
})

export function nextJobRun(jobs: readonly HealthJob[] | null | undefined, pick: (job: HealthJob) => boolean, now: number): string | null {
  const times = (jobs ?? []).filter(pick).map((j) => j.next_run_at).filter((t): t is string => Boolean(t))
    .map((t) => ({ t, ms: Date.parse(t) })).filter((x) => Number.isFinite(x.ms))
  if (!times.length) return null
  const future = times.filter((x) => x.ms >= now - 5_000)
  const pool = future.length ? future : times
  return pool.reduce((a, b) => (b.ms < a.ms ? b : a)).t
}

const EtClock = memo(function EtClock() {
  const now = useTick(1000)
  const text = etSeconds(now)
  return <time className="gn-clock" aria-label={`Eastern time ${text}`}>{text}<small>ET</small></time>
})

const Standby = memo(function Standby({ next, board }: { next: { label: string; at: string } | null; board: BoardState }) {
  const now = useTick(1000)
  if (board === 'loading') return <span className="gn-standby" role="status">READING TONIGHT&apos;S BOARD…</span>
  if (board === 'failed') return <span className="gn-standby gn-standby--unknown" role="status">BOARD UNKNOWN</span>
  if (!next) return <span className="gn-standby">STANDBY</span>
  const ms = Date.parse(next.at) - now
  return <span className="gn-standby">STANDBY · next: {next.label} {ms > 0 ? `in ${clockText(ms)}` : 'starting'}</span>
})

const DirectorPill = memo(function DirectorPill({ paused, nextCutAt }: { paused: boolean; nextCutAt: number | null }) {
  const now = useTick(1000)
  if (paused) return <span className="gn-pill gn-pill--director is-paused">DIRECTOR PAUSED</span>
  return (
    <span className="gn-pill gn-pill--director">DIRECTOR ON{nextCutAt != null ? <> · next cut <b>{clockText(Math.max(0, nextCutAt - now))}</b></> : ''}</span>
  )
})

export interface TopBarProps {
  onAir: boolean
  board: BoardState
  standby: { label: string; at: string } | null
  mission: FreshResource & { data: { generated_at?: string } | null }
  ledger: FreshResource & { data: { generated_at?: string } | null }
  scores: ScoreFreshness
  settlementAt: string | null
  pricesAt: string | null
  tv: boolean
  directorPaused: boolean
  nextCutAt: number | null
  canFullscreen: boolean
  isFullscreen: boolean
  canWake: boolean
  wakeOn: boolean
  onTv: () => void
  onFullscreen: () => void
  onPause: () => void
  onReplay: () => void
  onWake: () => void
}

export function TopBar(props: TopBarProps) {
  const { onAir, tv } = props
  return (
    <header className="gn-bar">
      <div className="gn-bar__lead">
        <span className="gn-bug" aria-label={onAir ? 'AISP Game Night, on air: a held game is live' : props.board === 'ready' ? 'AISP Game Night, standby' : 'AISP Game Night'}>
          <span className="gn-bug__name">AISP <b>GAME NIGHT</b></span>
          {onAir ? <span className="gn-onair"><i aria-hidden="true" />ON AIR</span> : <Standby next={props.standby} board={props.board} />}
        </span>
        <EtClock />
      </div>
      <div className="gn-bar__meta">
        <span className="gn-chip gn-chip--paper">PAPER RESEARCH</span>
        <AsOfChip label="Games" resource={props.mission} generatedAt={props.mission.data?.generated_at} staleAfterMs={MISSION_STALE_AFTER_MS} />
        <AsOfChip label="Ledger" resource={props.ledger} generatedAt={props.ledger.data?.generated_at} staleAfterMs={LEDGER_STALE_AFTER_MS} />
        <ScoresChip fresh={props.scores} board={props.board} />
        <JobPill label="Settlement check" at={props.settlementAt} board={props.board} />
        <JobPill label="Prices" at={props.pricesAt} board={props.board} />
        {tv && <DirectorPill paused={props.directorPaused} nextCutAt={props.nextCutAt} />}
      </div>
      <nav className="gn-bar__controls" aria-label="Game Night controls">
        <button type="button" className={`gn-btn ${tv ? 'is-on' : ''}`} aria-pressed={tv} aria-label={tv ? 'Leave TV mode' : 'TV mode'} title="TV mode (T)" onClick={props.onTv}>
          <Tv size={16} aria-hidden="true" /><span>TV mode</span>
        </button>
        {props.canFullscreen && (
          <button type="button" className="gn-btn" aria-label={props.isFullscreen ? 'Exit fullscreen' : 'Fullscreen'} title="Fullscreen (F)" onClick={props.onFullscreen}>
            {props.isFullscreen ? <Minimize size={16} aria-hidden="true" /> : <Maximize size={16} aria-hidden="true" />}<span>Fullscreen</span>
          </button>
        )}
        {tv && (
          <button type="button" className="gn-btn" aria-label={props.directorPaused ? 'Resume director' : 'Pause director'} title="Pause or resume the director (Space)" onClick={props.onPause}>
            {props.directorPaused ? <Play size={16} aria-hidden="true" /> : <Pause size={16} aria-hidden="true" />}<span>{props.directorPaused ? 'Resume director' : 'Pause director'}</span>
          </button>
        )}
        {tv && props.canWake && (
          <button type="button" className={`gn-btn ${props.wakeOn ? 'is-on' : ''}`} aria-pressed={props.wakeOn} aria-label="Keep screen on" title="Keep the screen on while the wall runs" onClick={props.onWake}>
            <Sun size={16} aria-hidden="true" /><span>Keep screen on</span>
          </button>
        )}
        <button type="button" className="gn-btn" aria-label="Replay tonight" title="Replay tonight's settlements (R)" onClick={props.onReplay}>
          <RotateCcw size={16} aria-hidden="true" /><span>Replay tonight</span>
        </button>
        <Link to="/mission-control" className="gn-btn gn-btn--link" aria-label="Back to Mission Control">
          <ArrowLeft size={16} aria-hidden="true" /><span>Mission Control</span>
        </Link>
      </nav>
    </header>
  )
}
