/** Endpoint-only playback of verified, persisted drives. No play-level simulation. */
import { Maximize2, Minimize2, Pause, Play, RotateCcw, SkipBack, SkipForward } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { FootballField } from './FootballField'
import { pct } from './OutcomeExplorer'
import type { NflDrive, NflDriveSupplement, NflRecordedDrive, NflRepresentativeGame } from '../../types/nfl'
import './nfltheater.css'

const SPEEDS = [0.5, 1, 2, 4]
const STEP_MS = 2600
export const RESULT_LABEL: Record<string, string> = {
  TD: 'Touchdown', TOUCHDOWN: 'Touchdown', FG: 'Field goal', FIELD_GOAL: 'Field goal',
  MISSED_FG: 'Missed field goal', MISSED_FIELD_GOAL: 'Missed field goal', PUNT: 'Punt',
  TURNOVER: 'Turnover', TURNOVER_ON_DOWNS: 'Turnover on downs', SAFETY: 'Safety',
  CLOCK_EXPIRED: 'Clock expired', END_OF_HALF: 'End of half', END_OF_GAME: 'End of game', KNEEL: 'Kneel-down',
}

/** Legacy normalized drives use distance from the offence's own goal line. */
export function driveGeometry(drive: NflDrive) {
  return drive.offense === 'AWAY' ? { fromYard: drive.startYardLine, toYard: drive.endYardLine }
    : { fromYard: 100 - drive.startYardLine, toYard: 100 - drive.endYardLine }
}

/** V1 raw yardline100 is distance TO the opponent goal, not distance travelled. */
export function recordedDriveGeometry(drive: NflRecordedDrive) {
  return drive.possession === 'HOME'
    ? { fromYard: drive.startYardline100, toYard: drive.endYardline100 }
    : { fromYard: 100 - drive.startYardline100, toYard: drive.endYardline100 === null ? null : 100 - drive.endYardline100 }
}

/** Quarter and display clock are derived only from the recorded half/OT clock. */
export function driveClock(drive: NflRecordedDrive, progress = 1) {
  const seconds = Math.max(0, drive.periodSecondsBefore + (drive.periodSecondsAfter - drive.periodSecondsBefore) * progress)
  const overtime = drive.period === 'OVERTIME'
  const secondHalf = drive.period === 'REGULATION_HALF_2'
  const quarter = overtime ? 'OT' : `Q${(secondHalf ? 2 : 0) + (seconds > 900 ? 1 : 2)}`
  const within = overtime ? seconds : seconds > 900 ? seconds - 900 : seconds
  const whole = Math.ceil(within)
  return { period: quarter, clock: `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}` }
}

export function scenarioLabel(supplement: NflDriveSupplement) {
  return supplement.scenario?.scenarioId === 'BASE' ? 'BASE · fitted drive engine'
    : supplement.scenario ? `Pace sensitivity · ${supplement.scenario.paceDurationMultiplier ?? '—'}× duration` : supplement.engine_id ?? 'Drive engine'
}

export function representativePaths(supplement: NflDriveSupplement | null): NflRepresentativeGame[] {
  return supplement?.paths ?? []
}

function yardDescription(distance: number | null) {
  if (distance === null) return 'endpoint not recorded'
  if (distance === 0) return 'opponent goal line'
  if (distance === 100) return 'own goal line'
  return distance > 50 ? `own ${100 - distance}` : distance === 50 ? 'midfield' : `opponent ${distance}`
}

function isScoring(drive: NflRecordedDrive) { return drive.homePointsAdded > 0 || drive.awayPointsAdded > 0 }

export function DriveTheater({ supplements, requirement, homeTeam, awayTeam, m1Label, initialRepresentative, initialSupplement }: {
  supplements: NflDriveSupplement[]; requirement: string | null; homeTeam: string; awayTeam: string; m1Label: string
  initialRepresentative?: number | null; initialSupplement?: string | null
}) {
  const [engineKey, setEngineKey] = useState(initialSupplement ?? '')
  const ordered = useMemo(() => [...supplements].sort((a, b) => Number(b.scenario?.scenarioId === 'BASE') - Number(a.scenario?.scenarioId === 'BASE')), [supplements])
  const supplement = ordered.find((s) => s.supplement_sha256 === engineKey) ?? ordered[0] ?? null
  const paths = useMemo(() => representativePaths(supplement), [supplement])
  const [representative, setRepresentative] = useState(initialRepresentative ?? -1)
  const game = paths.find((g) => g.simulationIndex === representative) ?? paths[0]
  const drives = game?.drives ?? []
  const [index, setIndex] = useState(0)
  const [progress, setProgress] = useState(1)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [timeline, setTimeline] = useState<'all' | 'scoring' | 'current'>('all')
  const [trails, setTrails] = useState(true)
  const [theater, setTheater] = useState(false)
  const [reduced, setReduced] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)
  const theaterRef = useRef<HTMLElement>(null)
  const progressRef = useRef(progress)
  progressRef.current = progress
  const last = drives.length - 1

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    const update = () => setReduced(media?.matches ?? false)
    media?.addEventListener?.('change', update)
    return () => media?.removeEventListener?.('change', update)
  }, [])
  useEffect(() => { setPlaying(false); setIndex(0); setProgress(1) }, [supplement?.supplement_sha256, game?.simulationIndex])
  useEffect(() => {
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') setTheater(false) }
    const fullscreen = () => { if (!document.fullscreenElement) setTheater(false) }
    document.addEventListener('keydown', escape)
    document.addEventListener('fullscreenchange', fullscreen)
    return () => { document.removeEventListener('keydown', escape); document.removeEventListener('fullscreenchange', fullscreen) }
  }, [])
  useEffect(() => {
    if (!playing || last < 0) return
    const startProgress = progressRef.current
    const started = performance.now()
    // Display time is never a simulated play or physical ball-flight time.
    const timer = window.setInterval(() => {
      const fraction = startProgress + (performance.now() - started) / (STEP_MS / speed)
      setProgress(reduced ? 1 : Math.min(1, fraction))
      if (fraction >= 1.28) {
        if (index >= last) setPlaying(false)
        else { setIndex((v) => v + 1); setProgress(0) }
      }
    }, 32)
    return () => window.clearInterval(timer)
  }, [playing, speed, index, last, reduced])

  const selectDrive = (next: number) => { setPlaying(false); setIndex(Math.max(0, Math.min(next, last))); setProgress(1) }
  const play = () => {
    if (playing) { setPlaying(false); return }
    if (index === last && progress >= 1) setIndex(0)
    if (progress >= 1) setProgress(0)
    setPlaying(true)
  }
  const toggleTheater = async () => {
    if (theater) { if (document.fullscreenElement) await document.exitFullscreen?.(); setTheater(false); return }
    setTheater(true)
    try { await theaterRef.current?.requestFullscreen?.() } catch { /* Expanded theater remains if native fullscreen is denied. */ }
  }

  if (!supplement || !game || !drives.length) return <section className="theater-empty" aria-label="Drive Theater"><h3>Drive Theater — awaiting recorded paths</h3><p>{requirement ?? 'No compatible recorded representative drives have been imported for this game.'}</p><p>{m1Label} supplies final-score probabilities, not a drive replay. A separate drive engine is required.</p></section>

  const current = drives[Math.min(index, last)]
  const geometry = recordedDriveGeometry(current)
  const clock = driveClock(current, progress)
  const complete = progress >= 1
  const result = RESULT_LABEL[current.outcome] ?? current.outcome.replaceAll('_', ' ')
  const homeScore = current.homeScoreAfter - (complete ? 0 : current.homePointsAdded)
  const awayScore = current.awayScoreAfter - (complete ? 0 : current.awayPointsAdded)
  const celebration = complete ? ['TD', 'TOUCHDOWN'].includes(current.outcome) ? 'td' : ['FG', 'FIELD_GOAL'].includes(current.outcome) ? 'fg' : null : null
  const expected = supplement.explorer?.expected
  const outcomes = supplement.explorer?.outcomes
  const pace = supplement.scenario?.scenarioId !== 'BASE'
  const persistedTrails = trails ? drives.slice(0, index).filter((d) => timeline !== 'scoring' || isScoring(d)).map((d) => ({ ...recordedDriveGeometry(d), tone: d.possession === 'HOME' ? 'home' as const : 'away' as const })) : []

  return <section ref={theaterRef} className={`nfl-theater${theater ? ' nfl-theater--expanded' : ''}`} aria-label="Drive Theater">
    <div className="nfl-theater__header">
      <div><span className="nfl-kicker">INDEPENDENT DRIVE ENGINE · RESEARCH ONLY</span><h2>Every possession. One possible game.</h2><p>{supplement.simulation_count?.toLocaleString() ?? '—'} stored simulations · {game.label} · {drives.length} recorded drives{game.reachedOvertime ? ' · Overtime' : ''}</p></div>
      <button type="button" className="secondary-button" onClick={toggleTheater} aria-pressed={theater}>{theater ? <Minimize2 size={16} /> : <Maximize2 size={16} />}{theater ? 'Exit theater' : 'Theater mode'}</button>
    </div>
    <div className="nfl-theater__selectors">
      <label>Scenario<select aria-label="Drive scenario" value={supplement.supplement_sha256} onChange={(e) => setEngineKey(e.target.value)}>{ordered.map((s) => <option key={s.supplement_sha256} value={s.supplement_sha256}>{scenarioLabel(s)}</option>)}</select></label>
      <label>Representative game<select aria-label="Representative simulation" value={game.simulationIndex} onChange={(e) => setRepresentative(Number(e.target.value))}>{paths.map((g) => <option key={g.simulationIndex} value={g.simulationIndex}>{g.label} · {awayTeam} {g.awayScore} – {homeTeam} {g.homeScore}{g.reachedOvertime ? ' · OT' : ''}</option>)}</select></label>
      <div className="nfl-engine-probabilities" aria-label="Selected drive engine probabilities"><span>{homeTeam} <strong>{pct(outcomes?.home)}</strong></span><span>{awayTeam} <strong>{pct(outcomes?.away)}</strong></span><span>Tie <strong>{pct(outcomes?.tie)}</strong></span><span>Expected score <strong>{awayTeam} {expected?.away.toFixed(1) ?? '—'} – {homeTeam} {expected?.home.toFixed(1) ?? '—'}</strong></span></div>
    </div>
    <p className="nfl-theater__assumption">{pace ? `Pace sensitivity: recorded drive durations scaled by ${supplement.scenario?.paceDurationMultiplier ?? '—'}. An assumed sensitivity, not a known coaching decision.` : 'BASE uses the fitted conditional drive engine.'} These are not {m1Label} possessions or probabilities.</p>
    <FootballField awayTeam={awayTeam} homeTeam={homeTeam} awayScore={awayScore} homeScore={homeScore} caption={`${game.label} · Drive ${current.driveNumber} / ${drives.length}`} {...clock} possession={current.possession} celebration={celebration} drive={{ ...geometry, tone: current.possession === 'HOME' ? 'home' : 'away', progress, result }} trails={persistedTrails}>
      <span aria-live="polite">{current.offenseTeam} possession · {yardDescription(current.startYardline100)} → {yardDescription(current.endYardline100)} · {complete ? result : 'Recorded drive unfolding'}{index === last && complete ? ` · Final ${awayTeam} ${game.awayScore}–${homeTeam} ${game.homeScore}` : ''}</span>
      <span>Movement connects recorded endpoints; no individual plays or physical ball trajectory are implied.</span>
    </FootballField>
    <div className="nfl-transport" role="group" aria-label="Replay controls">
      <button className="icon-button" type="button" aria-label="Restart" onClick={() => selectDrive(0)}><RotateCcw size={18} /></button>
      <button className="icon-button" type="button" aria-label="Previous drive" disabled={index === 0} onClick={() => selectDrive(index - 1)}><SkipBack size={18} /></button>
      <button className="primary-button" type="button" onClick={play} aria-pressed={playing}>{playing ? <Pause size={17} /> : <Play size={17} />}{playing ? 'Pause' : 'Play'}</button>
      <button className="icon-button" type="button" aria-label="Next drive" disabled={index === last} onClick={() => selectDrive(index + 1)}><SkipForward size={18} /></button>
      <input type="range" min="0" max={last} value={index} aria-label="Drive position" onChange={(e) => selectDrive(Number(e.target.value))} />
      <label>Speed<select aria-label="Replay speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}</select></label>
      <label>Timeline<select aria-label="Timeline view" value={timeline} onChange={(e) => setTimeline(e.target.value as typeof timeline)}><option value="all">Full game</option><option value="scoring">Scoring drives</option><option value="current">Current drive</option></select></label>
      <label className="nfl-toggle"><input type="checkbox" checked={trails} onChange={(e) => setTrails(e.target.checked)} />Keep trails</label>
    </div>
    {reduced && <p className="nfl-motion-note">Reduced motion is on: playback uses clear drive-state changes without ball movement.</p>}
    <ol className="nfl-drive-timeline" aria-label="Drive timeline">{drives.map((d, i) => ({ d, i })).filter(({ d, i }) => timeline === 'all' || (timeline === 'current' ? i === index : isScoring(d))).map(({ d, i }) => <li key={d.driveNumber} className={`${i === index ? 'is-current' : ''} ${isScoring(d) ? 'is-scoring' : ''}`}><button type="button" aria-current={i === index ? 'step' : undefined} onClick={() => selectDrive(i)}><span className={`nfl-timeline-team nfl-timeline-team--${d.possession.toLowerCase()}`}>{d.offenseTeam}</span><span><small>DRIVE {d.driveNumber} · {driveClock(d).period} {driveClock(d).clock}</small><strong>{RESULT_LABEL[d.outcome] ?? d.outcome}</strong></span><span className="nfl-timeline-score">{d.awayScoreAfter}–{d.homeScoreAfter}</span></button></li>)}</ol>
    <details className="nfl-lab-evidence"><summary>Engine & recorded evidence</summary><p>{supplement.engine_id} · {scenarioLabel(supplement)} · simulation {game.simulationIndex} · seed {game.seed}. Historical evidence through {supplement.historical_evidence_through_date ?? supplement.source_cutoff_utc ?? 'not stated'}; publication is not a source refresh.</p><p>Artifact {supplement.supplement_sha256}</p><ul>{supplement.limitations?.map((s) => <li key={s}>{s}</li>)}</ul></details>
  </section>
}
