/**
 * V18 sequential game replay.
 *
 * This renders a path the V18 bundle already simulated and AISP already
 * persisted. It runs no simulation of its own, derives nothing from Gen 1, and
 * invents no player, pitch, hit location or event that is not in the stored
 * record. Every field on screen maps to a persisted key:
 *
 *   inning / half / outsAfter / baseMaskAfter / homeScoreAfter /
 *   awayScoreAfter / outcome / runsScored / pitchingChange /
 *   pitcherState / battingOrderPosition / pa
 *
 * Base occupancy is the stored bitmask: 1 = first, 2 = second, 4 = third.
 *
 * The batter is shown by batting-order position because that is what the path
 * persists; the pitcher is shown by name only while the governed state is
 * STARTER (the starter is recorded in the forecast's own evidence) and by its
 * governed state otherwise. Neither is guessed.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Pause, Play, RotateCcw } from 'lucide-react'
import type { ShadowForecastView, ShadowPath, ShadowPathEvent } from '../types/shadow'

/**
 * Milliseconds per plate appearance at 1x.
 *
 * Chosen so a 74-PA game reviews in ~25s rather than crawling at one PA per
 * second; the other speeds scale from it.
 */
const BASE_STEP_MS = 340

const SPEEDS = [0.5, 1, 2, 4] as const
type Speed = (typeof SPEEDS)[number]

const OUTCOME_LABEL: Record<string, string> = {
  STRIKEOUT: 'Strikeout',
  BIP_OUT: 'Ball in play — out',
  SINGLE: 'Single',
  DOUBLE: 'Double',
  TRIPLE: 'Triple',
  HOME_RUN: 'Home run',
  WALK_HBP_CI: 'Walk / HBP / catcher interference',
  WALK: 'Walk',
  OTHER_REACH: 'Reached base — other',
}

function outcomeLabel(outcome: string): string {
  return OUTCOME_LABEL[outcome] ?? outcome.replaceAll('_', ' ').toLowerCase()
}

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** Bases occupied after this plate appearance, from the stored bitmask. */
export function basesFromMask(mask: number): { first: boolean; second: boolean; third: boolean } {
  return {
    first: (mask & 1) === 1,
    second: (mask & 2) === 2,
    third: (mask & 4) === 4,
  }
}

export function SequentialReplay({ path, shadow, awayTeam, homeTeam }: {
  path: ShadowPath
  shadow: ShadowForecastView
  awayTeam: string
  homeTeam: string
}) {
  const events = useMemo<ShadowPathEvent[]>(() => path.paths[0] ?? [], [path])
  const reduced = useMemo(prefersReducedMotion, [])
  // Reduced motion opens on the final state; manual navigation still works.
  const [index, setIndex] = useState(() => (reduced ? Math.max(0, events.length - 1) : 0))
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<Speed>(1)
  const timer = useRef<number | null>(null)

  const last = events.length - 1
  const current = events[Math.min(index, last)]

  useEffect(() => {
    if (!playing || events.length === 0) return
    if (index >= last) { setPlaying(false); return }
    const delay = BASE_STEP_MS / speed
    timer.current = window.setTimeout(() => setIndex((value) => Math.min(value + 1, last)), delay)
    return () => { if (timer.current) window.clearTimeout(timer.current) }
  }, [playing, index, last, speed, events.length])

  const step = useCallback((delta: number) => {
    setPlaying(false)
    setIndex((value) => Math.min(Math.max(value + delta, 0), last))
  }, [last])

  const innings = useMemo(() => {
    const seen = new Map<number, number>()
    events.forEach((event, position) => {
      if (!seen.has(event.inning)) seen.set(event.inning, position)
    })
    return [...seen.entries()].sort((a, b) => a[0] - b[0])
  }, [events])

  const pitchingChanges = useMemo(
    () => events.map((event, position) => ({ event, position }))
      .filter((item) => item.event.pitchingChange),
    [events],
  )

  if (events.length === 0) {
    return (
      <p className="replay__empty" role="status">
        No sequential path is persisted for this forecast.
      </p>
    )
  }

  const bases = basesFromMask(current.baseMaskAfter)
  const complete = index >= last
  const starterName = shadow.sides?.[current.half === 'TOP' ? 'HOME' : 'AWAY']?.starter ?? null

  return (
    <section className="replay" aria-labelledby="replay-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">V18 RESEARCH SHADOW · SEQUENTIAL GAME REPLAY</span>
          <h3 id="replay-heading">One simulated game, plate appearance by plate appearance</h3>
        </div>
      </div>

      <p className="replay__provenance">
        Representative path selected from {shadow.simulation_count?.toLocaleString() ?? '—'} completed
        V18 simulations. This is one simulated game path, not a prediction of the exact play sequence.
      </p>

      <div className="replay__stage">
        <Scoreboard
          current={current}
          awayTeam={awayTeam}
          homeTeam={homeTeam}
          index={index}
          total={events.length}
          complete={complete}
        />
        <Field bases={bases} outs={current.outsAfter} half={current.half} />
      </div>

      <dl className="replay__now">
        <div>
          <dt>Plate appearance</dt>
          <dd>{current.pa} of {events.length}</dd>
        </div>
        <div>
          <dt>Batting order</dt>
          <dd>#{current.battingOrderPosition} <small>batter identity is not persisted in the path</small></dd>
        </div>
        <div>
          <dt>Pitcher</dt>
          <dd>
            {current.pitcherState === 'STARTER' && starterName
              ? <>{starterName} <small>starter</small></>
              : <>{current.pitcherState.replaceAll('_', ' ').toLowerCase()} <small>governed pitcher state</small></>}
          </dd>
        </div>
        <div>
          <dt>Outcome</dt>
          <dd>{outcomeLabel(current.outcome)}</dd>
        </div>
        <div>
          <dt>Runs on this PA</dt>
          <dd>{current.runsScored}</dd>
        </div>
        <div>
          <dt>Pitching change</dt>
          <dd>{current.pitchingChange ? 'Yes — new pitcher this PA' : 'No'}</dd>
        </div>
      </dl>

      {current.inning > 9 && (
        <p className="replay__extra" role="status">
          Extra innings — this path reached inning {current.inning}.
        </p>
      )}

      {complete && (
        <p className="replay__final">
          Final in this path: {awayTeam} {current.awayScoreAfter} – {homeTeam} {current.homeScoreAfter}
          {current.inning > 9 ? ` (${current.inning} innings)` : ''}.
        </p>
      )}

      <div className="replay__controls" role="group" aria-label="Replay controls">
        <button type="button" onClick={() => step(-1)} disabled={index === 0} aria-label="Previous plate appearance">
          <ChevronLeft size={15} aria-hidden="true" /> Prev
        </button>
        <button
          type="button"
          className="replay__play"
          onClick={() => setPlaying((value) => !value)}
          disabled={complete}
          aria-label={playing ? 'Pause replay' : 'Play replay'}
        >
          {playing ? <Pause size={15} aria-hidden="true" /> : <Play size={15} aria-hidden="true" />}
          {playing ? 'Pause' : 'Play'}
        </button>
        <button type="button" onClick={() => step(1)} disabled={complete} aria-label="Next plate appearance">
          Next <ChevronRight size={15} aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => { setPlaying(false); setIndex(0) }}
          aria-label="Restart replay"
        >
          <RotateCcw size={15} aria-hidden="true" /> Restart
        </button>

        <span className="replay__speed" role="group" aria-label="Replay speed">
          {SPEEDS.map((value) => (
            <button
              key={value}
              type="button"
              className={value === speed ? 'is-active' : ''}
              onClick={() => setSpeed(value)}
              aria-pressed={value === speed}
            >
              {value}×
            </button>
          ))}
        </span>

        <label className="replay__jump">
          <span>Jump to inning</span>
          <select
            value={current.inning}
            onChange={(event) => {
              const target = innings.find(([inning]) => inning === Number(event.target.value))
              if (target) { setPlaying(false); setIndex(target[1]) }
            }}
          >
            {innings.map(([inning]) => (
              <option key={inning} value={inning}>{inning}</option>
            ))}
          </select>
        </label>

        {pitchingChanges.length > 0 && (
          <span className="replay__jump-changes" role="group" aria-label="Jump to a pitching change">
            <span>Pitching changes</span>
            {pitchingChanges.map((item) => (
              <button
                key={item.position}
                type="button"
                onClick={() => { setPlaying(false); setIndex(item.position) }}
              >
                PA {item.event.pa}
              </button>
            ))}
          </span>
        )}
      </div>

      <p className="replay__lineage">
        Path digest {path.path_digest?.slice(0, 12) ?? 'not recorded'}… · seed {path.master_seed} ·
        {' '}engine {path.engine_id}. No simulation is run in the browser.
      </p>
    </section>
  )
}

function Scoreboard({ current, awayTeam, homeTeam, index, total, complete }: {
  current: ShadowPathEvent
  awayTeam: string
  homeTeam: string
  index: number
  total: number
  complete: boolean
}) {
  return (
    <div className="replay__scoreboard">
      <div className="replay__inning">
        <span aria-hidden="true">{current.half === 'TOP' ? '▲' : '▼'}</span>
        <strong>{current.inning}</strong>
        <small>{current.half === 'TOP' ? 'Top' : 'Bottom'}</small>
      </div>
      <div className="replay__scores">
        <div className={current.half === 'TOP' ? 'is-batting' : ''}>
          <span>{awayTeam}</span><strong>{current.awayScoreAfter}</strong>
        </div>
        <div className={current.half === 'BOTTOM' ? 'is-batting' : ''}>
          <span>{homeTeam}</span><strong>{current.homeScoreAfter}</strong>
        </div>
      </div>
      <div className="replay__outs">
        <span>Outs</span>
        <span className="replay__out-dots" aria-hidden="true">
          {[0, 1, 2].map((slot) => (
            <i key={slot} className={slot < current.outsAfter ? 'is-out' : ''} />
          ))}
        </span>
        <small>{current.outsAfter}</small>
      </div>
      <div className="replay__progress">
        <span>PA</span>
        <strong>{index + 1}</strong>
        <small>of {total}{complete ? ' · complete' : ''}</small>
      </div>
    </div>
  )
}

function Field({ bases, outs, half }: {
  bases: { first: boolean; second: boolean; third: boolean }
  outs: number
  half: 'TOP' | 'BOTTOM'
}) {
  const label = [
    bases.first && 'first',
    bases.second && 'second',
    bases.third && 'third',
  ].filter(Boolean).join(', ')

  return (
    <svg
      className="replay__field"
      viewBox="0 0 260 220"
      role="img"
      aria-label={
        `${half === 'TOP' ? 'Top' : 'Bottom'} of the inning, ${outs} out. `
        + (label ? `Runners on ${label}.` : 'Bases empty.')
      }
    >
      {/* Outfield and infield are scene setting only; nothing here is data. */}
      <path d="M130 200 L20 90 A155 155 0 0 1 240 90 Z" className="replay__grass" />
      <path d="M130 190 L62 122 L130 54 L198 122 Z" className="replay__dirt" />
      <circle cx="130" cy="122" r="13" className="replay__mound" />
      <path d="M124 194 h12 l4 6 h-20 z" className="replay__plate" />

      <g className="replay__bases">
        <rect x="190" y="114" width="16" height="16" transform="rotate(45 198 122)"
          className={bases.first ? 'is-occupied' : ''} />
        <rect x="122" y="46" width="16" height="16" transform="rotate(45 130 54)"
          className={bases.second ? 'is-occupied' : ''} />
        <rect x="54" y="114" width="16" height="16" transform="rotate(45 62 122)"
          className={bases.third ? 'is-occupied' : ''} />
      </g>
    </svg>
  )
}
