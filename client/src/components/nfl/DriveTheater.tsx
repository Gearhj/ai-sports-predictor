/**
 * NFL Drive Theater — replays PERSISTED possessions from a drive-level engine.
 *
 * Every drive shown is a row the producer simulated and AISP verified: period,
 * clock, offence, start and end yard line, result and running score. The
 * replay reconciles to the supplement's own final score because the importer
 * refused it otherwise. Nothing is drawn that is not in the persisted drive:
 * no routes, no throws, no tackles. When no drive engine has delivered
 * evidence for this game, the theater says so and lists what it needs.
 */
import { Pause, Play, RotateCcw, SkipBack, SkipForward } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FootballField } from './FootballField'
import type { NflDrive, NflDriveSupplement } from '../../types/nfl'

const SPEEDS = [0.5, 1, 2, 4] as const
type Speed = (typeof SPEEDS)[number]
const BASE_STEP_MS = 1400

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches } catch { return false }
}

/** Away drives move right (toward the home end zone at 100); home drives move left. */
export function driveGeometry(drive: NflDrive): { fromYard: number; toYard: number } {
  return drive.offense === 'AWAY'
    ? { fromYard: drive.startYardLine, toYard: drive.endYardLine }
    : { fromYard: 100 - drive.startYardLine, toYard: 100 - drive.endYardLine }
}

const RESULT_LABEL: Record<string, string> = {
  TOUCHDOWN: 'Touchdown', FIELD_GOAL: 'Field goal', MISSED_FIELD_GOAL: 'Missed FG', PUNT: 'Punt',
  TURNOVER: 'Turnover', TURNOVER_ON_DOWNS: 'Turnover on downs', SAFETY: 'Safety',
  END_OF_HALF: 'End of half', END_OF_GAME: 'End of game', KNEEL: 'Kneel-down',
}

export function DriveTheater({ supplements, requirement, homeTeam, awayTeam, m1Label }: {
  supplements: NflDriveSupplement[]
  requirement: string | null
  homeTeam: string
  awayTeam: string
  m1Label: string
}) {
  const [engineIndex, setEngineIndex] = useState(0)
  const supplement = supplements[engineIndex] ?? null
  const drives = useMemo(() => supplement?.summaries?.drives ?? [], [supplement])
  const reduced = useMemo(prefersReducedMotion, [])
  const [index, setIndex] = useState(() => (reduced ? Math.max(0, drives.length - 1) : 0))
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<Speed>(1)
  const [overlay, setOverlay] = useState<'none' | 'scoring' | 'all'>('none')
  const timer = useRef<number | null>(null)
  const last = drives.length - 1

  useEffect(() => { setIndex(reduced ? Math.max(0, drives.length - 1) : 0); setPlaying(false) }, [drives.length, reduced])

  useEffect(() => {
    if (!playing || drives.length === 0) return
    if (index >= last) { setPlaying(false); return }
    timer.current = window.setTimeout(() => setIndex((v) => Math.min(v + 1, last)), BASE_STEP_MS / speed)
    return () => { if (timer.current) window.clearTimeout(timer.current) }
  }, [playing, index, last, speed, drives.length])

  const step = useCallback((delta: number) => { setPlaying(false); setIndex((v) => Math.min(Math.max(v + delta, 0), last)) }, [last])

  if (!supplement || drives.length === 0) {
    return (
      <section className="theater-empty" aria-label="Drive Theater">
        <h3>Drive Theater — awaiting drive-level evidence</h3>
        <p>{requirement ?? 'No drive-level simulation has been imported for this game.'}</p>
        <p className="muted">
          {m1Label} publishes a joint final-score distribution, not possessions, so it cannot be replayed drive by drive.
          A separate drive engine&rsquo;s replay will be shown under its own name and will never be presented as an {m1Label} game.
        </p>
      </section>
    )
  }

  const current = drives[Math.min(index, last)]
  const geometry = driveGeometry(current)
  const engineLabel = supplement.summaries?.modelLabel ?? supplement.engine_id ?? 'Drive engine'
  const final = supplement.summaries?.finalScore
  const scoringMarkers = drives
    .filter((d) => overlay === 'all' || (overlay === 'scoring' && ['TOUCHDOWN', 'FIELD_GOAL', 'SAFETY'].includes(d.result)))
    .filter((d) => d.driveIndex <= current.driveIndex)
    .map((d) => ({ yard: driveGeometry(d).toYard, tone: d.offense === 'HOME' ? 'home' as const : 'away' as const, label: RESULT_LABEL[d.result] ?? d.result }))

  return (
    <section className="theater" aria-label="Drive Theater">
      <div className="theater__engine">
        <label htmlFor="drive-engine">Drive engine</label>
        <select id="drive-engine" value={engineIndex} onChange={(e) => setEngineIndex(Number(e.target.value))}>
          {supplements.map((s, i) => <option key={s.supplement_sha256} value={i}>{s.summaries?.modelLabel ?? s.engine_id ?? s.supplement_id}</option>)}
        </select>
        <span className="muted">
          {supplement.simulation_kind === 'MONTE_CARLO' && supplement.simulation_count
            ? `One of ${supplement.simulation_count.toLocaleString()} simulated games · seed ${supplement.simulation_seed ?? '—'}`
            : 'One simulated game'} · {drives.length} drives · drive-level labels{current.plays?.length ? '' : ' (no individual plays simulated)'}
        </span>
      </div>

      <FootballField
        awayTeam={awayTeam} homeTeam={homeTeam}
        awayScore={current.awayScoreAfter} homeScore={current.homeScoreAfter}
        caption={`${engineLabel} · drive ${current.driveIndex + 1} of ${drives.length}`}
        period={`Q${current.period}`} clock={current.clockEnd ?? current.clockStart ?? null}
        drive={{ ...geometry, tone: current.offense === 'HOME' ? 'home' : 'away', result: RESULT_LABEL[current.result] ?? current.result }}
        markers={scoringMarkers}
      >
        <span>
          {current.offense === 'HOME' ? homeTeam : awayTeam} ball from their {current.startYardLine <= 50 ? `own ${current.startYardLine}` : `opponent's ${100 - current.startYardLine}`}
          {' '}to the {current.endYardLine <= 50 ? `own ${current.endYardLine}` : `opponent's ${100 - current.endYardLine}`} · {RESULT_LABEL[current.result] ?? current.result}
          {final && index === last ? ` · final ${homeTeam} ${final.home}–${awayTeam} ${final.away}` : ''}
        </span>
      </FootballField>

      <div className="theater__transport" role="group" aria-label="Replay controls">
        <button type="button" className="icon-button" onClick={() => { setPlaying(false); setIndex(0) }} aria-label="Restart"><RotateCcw size={16} /></button>
        <button type="button" className="icon-button" onClick={() => step(-1)} aria-label="Previous drive"><SkipBack size={16} /></button>
        <button type="button" className="primary-button" onClick={() => setPlaying((v) => !v)} aria-pressed={playing}>
          {playing ? <><Pause size={15} /> Pause</> : <><Play size={15} /> Play</>}
        </button>
        <button type="button" className="icon-button" onClick={() => step(1)} aria-label="Next drive"><SkipForward size={16} /></button>
        <input type="range" min={0} max={last} value={index} onChange={(e) => { setPlaying(false); setIndex(Number(e.target.value)) }} aria-label="Drive position" />
        <label>Speed
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value) as Speed)}>
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
          </select>
        </label>
        <label>Overlay
          <select value={overlay} onChange={(e) => setOverlay(e.target.value as typeof overlay)}>
            <option value="none">None</option>
            <option value="scoring">Scoring drives</option>
            <option value="all">All drives so far</option>
          </select>
        </label>
        {reduced && <span className="muted">Reduced motion: showing the final state; step manually.</span>}
      </div>

      <ol className="theater__timeline" aria-label="Drive timeline">
        {drives.map((d) => (
          <li key={d.driveIndex} className={d.driveIndex === current.driveIndex ? 'is-current' : ''}>
            <button type="button" onClick={() => { setPlaying(false); setIndex(d.driveIndex) }}>
              <span>Q{d.period}</span> {d.offense === 'HOME' ? homeTeam : awayTeam} · {RESULT_LABEL[d.result] ?? d.result} · {homeTeam} {d.homeScoreAfter}–{awayTeam} {d.awayScoreAfter}
            </button>
          </li>
        ))}
      </ol>
      <p className="muted">
        {supplement.summaries?.overtime === true ? 'The producer flagged overtime for this simulated game.' : 'Overtime is stated only when the producer flags it; it is never inferred from a tie probability.'}
      </p>
    </section>
  )
}
