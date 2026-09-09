/**
 * Play-by-play theater for the independent play-level engine.
 *
 * WHAT MAKES THIS HONEST
 * Every frame is a persisted play: its own recorded state before and after,
 * its own outcome, and its own modeled geometry. The animation interpolates
 * the ball ALONG one play's supplied path and never between plays, so nothing
 * on screen is a guess about what happened in between. The paths are schematic
 * -- they show the category of the play, not a measured trajectory -- and the
 * page says so where a viewer will actually read it.
 *
 * WHAT MAKES IT WATCHABLE
 * The field is the page. Controls sit under it, the drive list beside it, and
 * the ball moves at a constant speed regardless of how far the play travelled,
 * because a two-yard plunge that takes as long as a fifty-yard bomb reads as
 * broken. Scores, turnovers and sacks get a longer beat and a visible flash;
 * everything else keeps moving.
 */
import {
  ChevronLeft, ChevronRight, Maximize2, Minimize2, Pause, Play as PlayIcon, RotateCcw,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PlayField, type Trail } from './PlayField'
import {
  PLAY_TYPE_LABEL, SEGMENT_LABEL, celebrationFor, clockLabel, isBigPlay, isScoring, isTurnover,
  segmentStyle, type Play,
} from './playGeometry'
import type {
  NflPlayRepresentative, NflPlayScenario, NflPlayTheaterIndex,
} from '../../types/nfl'
import './nfltheater.css'

const SPEEDS = [0.5, 1, 1.5, 2, 4]
/** A plain play at 1x. Long enough to read the ball, short enough to keep moving. */
const PLAY_MS = 1500
/** Scores, turnovers and sacks hold the frame after the ball stops. */
const BIG_PLAY_HOLD_MS = 900
const PLAIN_HOLD_MS = 260

type Filter = 'all' | 'scoring' | 'turnovers' | 'drive'

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!query) return
    const listen = (event: MediaQueryListEvent) => setReduced(event.matches)
    query.addEventListener('change', listen)
    return () => query.removeEventListener('change', listen)
  }, [])
  return reduced
}

export function playDuration(play: Play, speed: number, reduced: boolean): number {
  if (reduced) return 0
  const travel = play.path.segments.length ? PLAY_MS : PLAY_MS * 0.4
  return travel / speed
}

export function holdAfter(play: Play, speed: number): number {
  return (isBigPlay(play) ? BIG_PLAY_HOLD_MS : PLAIN_HOLD_MS) / speed
}

/** Plays that ran earlier in the same drive, for the persistent wake. */
export function trailsFor(plays: Play[], index: number): Trail[] {
  const current = plays[index]
  if (!current) return []
  const out: Trail[] = []
  for (let i = 0; i < index; i += 1) {
    const play = plays[i]
    if (play.drive_number !== current.drive_number) continue
    if (!play.path.segments.length) continue
    out.push({
      segments: play.path.segments,
      possession: play.before.possession,
      scoring: isScoring(play),
    })
  }
  return out
}

export function PlayTheater({
  index, game, scenarioId, position, loading, error,
  onScenario, onRepresentative, homeTeam, awayTeam, initialPlay = 0,
}: {
  index: NflPlayTheaterIndex
  game: NflPlayRepresentative | null
  scenarioId: string
  position: number
  loading: boolean
  error: string | null
  onScenario: (scenarioId: string) => void
  onRepresentative: (position: number) => void
  homeTeam: string
  awayTeam: string
  /** Deep-link straight to one play, so a moment can be shared or re-opened. */
  initialPlay?: number
}) {
  const reduced = useReducedMotion()
  const plays = useMemo(() => game?.plays ?? [], [game])
  const scenario = index.scenarios.find((s) => s.scenario_id === scenarioId) ?? index.scenarios[0]

  const [cursor, setCursor] = useState(initialPlay)
  const [progress, setProgress] = useState(1)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [showTrails, setShowTrails] = useState(true)
  const [filter, setFilter] = useState<Filter>('all')
  const [expanded, setExpanded] = useState(false)
  const frame = useRef<number | null>(null)

  // A new game restarts the theater rather than stranding the cursor in a
  // scenario whose play count is different.
  useEffect(() => { setCursor(initialPlay); setProgress(1); setPlaying(false) },
    [game?.supplement_sha256, position, initialPlay])

  const play: Play | null = plays[cursor] ?? null
  const total = plays.length

  const step = useCallback((delta: number) => {
    setPlaying(false)
    setCursor((c) => Math.max(0, Math.min(total - 1, c + delta)))
    setProgress(1)
  }, [total])

  const goTo = useCallback((next: number) => {
    setCursor(Math.max(0, Math.min(total - 1, next)))
    setProgress(1)
  }, [total])

  /* The animation loop: advance one play, hold, then move to the next. */
  useEffect(() => {
    if (!playing || !play) return
    let cancelled = false
    let hold: number | null = null
    const duration = playDuration(play, speed, reduced)
    const start = performance.now()
    setProgress(duration === 0 ? 1 : 0)

    const tick = (now: number) => {
      if (cancelled) return
      const elapsed = now - start
      if (duration > 0 && elapsed < duration) {
        setProgress(elapsed / duration)
        frame.current = requestAnimationFrame(tick)
        return
      }
      frame.current = null
      setProgress(1)
      hold = window.setTimeout(() => {
        if (cancelled) return
        if (cursor >= total - 1) setPlaying(false)
        else setCursor((c) => c + 1)
      }, holdAfter(play, speed))
    }
    frame.current = requestAnimationFrame(tick)
    return () => {
      cancelled = true
      if (frame.current !== null) cancelAnimationFrame(frame.current)
      if (hold !== null) window.clearTimeout(hold)
    }
  }, [playing, cursor, play, speed, reduced, total])

  const visible = useMemo(() => plays.map((p, i) => ({ play: p, i })).filter(({ play: p }) => {
    if (filter === 'scoring') return isScoring(p)
    if (filter === 'turnovers') return isTurnover(p)
    if (filter === 'drive') return p.drive_number === (play?.drive_number ?? -1)
    return true
  }), [plays, filter, play])

  const trails = useMemo(() => trailsFor(plays, cursor), [plays, cursor])
  const celebration = play && progress >= 1 ? celebrationFor(play) : null
  const legend = useMemo(() => {
    const kinds = new Set<string>()
    plays.forEach((p) => p.path.segments.forEach((s) => kinds.add(s.kind)))
    return [...kinds].sort()
  }, [plays])

  if (error) {
    return (
      <section className="nfl-theater">
        <h2>Play theater</h2>
        <p className="nfl-evidence-missing" role="alert">{error}</p>
      </section>
    )
  }

  return (
    <section
      className={`nfl-theater nfl-theater--play${expanded ? ' nfl-theater--expanded' : ''}`}
      aria-label="Play-by-play theater"
    >
      <header className="nfl-theater__header">
        <div>
          <span className="nfl-kicker">PLAY-LEVEL RESEARCH ENGINE</span>
          <h2>Watch a simulated game, play by play</h2>
          <p>
            One of {scenario?.draw_count?.toLocaleString() ?? '1,000'} simulated games, replayed from
            its own recorded plays. Every score, clock and field position below is the play's own.
          </p>
        </div>
        <button type="button" className="secondary-button" onClick={() => setExpanded((e) => !e)}>
          {expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          {expanded ? 'Exit full width' : 'Full width'}
        </button>
      </header>

      <div className="nfl-theater__selectors">
        <label>
          Scenario
          <select value={scenarioId} onChange={(e) => onScenario(e.target.value)}>
            {index.scenarios.map((s) => (
              <option key={s.scenario_id} value={s.scenario_id}>{s.label}</option>
            ))}
          </select>
        </label>
        <label>
          Simulated game
          <select value={position} onChange={(e) => onRepresentative(Number(e.target.value))}>
            {(scenario?.representatives ?? []).map((rep) => (
              <option key={rep.position} value={rep.position}>
                {rep.label} · {rep.awayScore}–{rep.homeScore} · {rep.playCount} plays
              </option>
            ))}
          </select>
        </label>
        {game?.seed !== undefined && (
          <p className="nfl-theater__assumption">
            Draw #{game.simulation_index} · seed {game.seed}
          </p>
        )}
      </div>

      {scenario?.explanation && (
        <p className="nfl-theater__assumption">{scenario.explanation}</p>
      )}

      {loading && <p className="nfl-theater__assumption">Loading this game's plays…</p>}

      {game && game.available === false && (
        <p className="nfl-evidence-missing" role="alert">
          This game's plays could not be read: <strong>{game.cause}</strong>. {game.detail}
        </p>
      )}

      {play && (
        <>
          <PlayField
            play={play} homeTeam={homeTeam} awayTeam={awayTeam} progress={progress}
            trails={trails} showTrails={showTrails} celebration={celebration}
            caption={`${scenario?.label ?? ''} · ${game?.label ?? ''}`}
          >
            <span data-testid="play-description">
              <strong>Play {play.play_number} of {total}</strong> · Drive {play.drive_number} ·{' '}
              {PLAY_TYPE_LABEL[play.play_type] ?? play.play_type}
            </span>
            <span>{play.description}</span>
          </PlayField>

          <div className="nfl-transport" role="group" aria-label="Playback controls">
            <button type="button" className="secondary-button" onClick={() => { setCursor(0); setProgress(1); setPlaying(false) }}
              aria-label="Restart"><RotateCcw size={15} /></button>
            <button type="button" className="secondary-button" onClick={() => step(-1)}
              disabled={cursor === 0} aria-label="Previous play"><ChevronLeft size={15} /></button>
            <button type="button" className="primary-button" onClick={() => setPlaying((p) => !p)}
              aria-label={playing ? 'Pause' : 'Play'}>
              {playing ? <Pause size={15} /> : <PlayIcon size={15} />}
              {playing ? 'Pause' : 'Play'}
            </button>
            <button type="button" className="secondary-button" onClick={() => step(1)}
              disabled={cursor >= total - 1} aria-label="Next play"><ChevronRight size={15} /></button>

            <input
              type="range" min={0} max={Math.max(0, total - 1)} value={cursor}
              onChange={(e) => { setPlaying(false); goTo(Number(e.target.value)) }}
              aria-label="Scrub to any play" data-testid="play-scrubber"
            />
            <output className="nfl-transport__count">{cursor + 1} / {total}</output>

            <label>
              Speed
              <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
              </select>
            </label>
            <label className="nfl-toggle">
              <input type="checkbox" checked={showTrails} onChange={(e) => setShowTrails(e.target.checked)} />
              Trails
            </label>
            <label>
              Show
              <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
                <option value="all">Every play</option>
                <option value="scoring">Scoring plays</option>
                <option value="turnovers">Turnovers</option>
                <option value="drive">This drive</option>
              </select>
            </label>
          </div>

          {reduced && (
            <p className="nfl-motion-note">
              Reduced motion is on, so plays advance without animating. Every control still works.
            </p>
          )}

          <div className="nfl-play-legend" aria-label="What the paths mean">
            {legend.map((kind) => {
              const style = segmentStyle(kind)
              return (
                <span key={kind} className={`nfl-legend-chip nfl-legend-chip--${style.tone}`}>
                  <svg width="34" height="10" aria-hidden="true">
                    <line x1="1" y1="5" x2="33" y2="5" strokeWidth="4" strokeLinecap="round"
                      strokeDasharray={style.dash} />
                  </svg>
                  {SEGMENT_LABEL[kind] ?? kind}
                </span>
              )
            })}
          </div>

          <ol className="nfl-play-list" aria-label="Plays">
            {visible.map(({ play: p, i }) => {
              const { period, clock } = clockLabel(p.before)
              return (
                <li key={p.play_number} className={`${i === cursor ? 'is-current' : ''} ${isScoring(p) ? 'is-scoring' : ''} ${isTurnover(p) ? 'is-turnover' : ''}`}>
                  <button type="button" onClick={() => { setPlaying(false); goTo(i) }}>
                    <small>{period} {clock} · Drive {p.drive_number}</small>
                    <strong>{PLAY_TYPE_LABEL[p.play_type] ?? p.play_type}</strong>
                    <span>{p.description}</span>
                    <span className="nfl-play-list__score">{p.after.awayScore}–{p.after.homeScore}</span>
                  </button>
                </li>
              )
            })}
          </ol>
        </>
      )}

      <details className="nfl-lab-evidence">
        <summary>Technical evidence and limitations</summary>
        <p><strong>{index.engine.engine_id}</strong> v{index.engine.version} · trained through{' '}
          {index.engine.training_cutoff} · {index.governance.promotion_status} ·{' '}
          research shadow, no wagering authority.</p>
        <p>{index.geometry_notice}</p>
        <ul>{index.limitations.map((limit) => <li key={limit}>{limit}</li>)}</ul>
        {game?.supplement_sha256 && <p>Artifact sha256 {game.supplement_sha256}</p>}
      </details>
    </section>
  )
}
