/**
 * Simulation Population — the primary play-engine experience.
 *
 * WHAT IT CLAIMS, AND WHAT IT DOES NOT
 * ------------------------------------
 * A scenario simulates 1,000 games. SIX of them were published with play paths.
 * This view draws every play of those six and says so, in the header, in
 * numbers, permanently. The 1,000-game aggregates are shown too -- they are
 * genuinely the whole population -- but under their own heading, with their own
 * denominators, never merged with the drawn sample.
 *
 * The distinction is load-bearing. A field with eight hundred moving lines
 * looks like a lot of evidence, and it would be very easy to let a reader
 * believe those lines were the thousand games. They are 0.6% of them.
 *
 * Nothing is pooled across scenarios or matchups to make the count larger.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Eraser, Expand, Maximize2, Pause, Play, RotateCcw,
} from 'lucide-react'
import { AccumulationChart } from '../charts/d3Charts'
import { PopulationField } from './PopulationField'
import { buildDensity } from './populationDensity'
import {
  DEFAULT_FILTERS, drawnStats, matches, outOfFieldPoints, POPULATION_PALETTE,
  type DownGroup, type FamilyFilter, type FieldZone, type PopulationFilters,
  type PopulationPlay, type TeamFilter,
} from './populationGeometry'
import type { NflPlayPopulation, NflPlayScenario } from '../../types/nfl'

const SPEEDS = [0.5, 1, 2, 4] as const
/**
 * Plays revealed per second at 1x. Time-based rather than per-tick: a timer
 * budget makes the reveal rate depend on the frame rate and on whether the tab
 * is in the background, so the same "2x" meant different things on different
 * machines. Plays per second means plays per second.
 */
const PLAYS_PER_SECOND = 90

const DOWN_OPTIONS: { value: DownGroup; label: string }[] = [
  { value: 'ALL', label: 'Any down' },
  { value: 'FIRST', label: '1st down' },
  { value: 'SECOND', label: '2nd down' },
  { value: 'THIRD', label: '3rd down' },
  { value: 'FOURTH', label: '4th down' },
]
const ZONE_OPTIONS: { value: FieldZone; label: string }[] = [
  { value: 'ALL', label: 'Anywhere' },
  { value: 'OWN', label: 'Backed up (60+ to go)' },
  { value: 'MIDDLE', label: 'Midfield (21–60)' },
  { value: 'RED', label: 'Red zone (≤20)' },
]

function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? '—' : `${(value * 100).toFixed(digits)}%`
}
function num(value: number | null | undefined, digits = 1): string {
  return value == null ? '—' : value.toFixed(digits)
}

export function SimulationPopulation({
  population, scenarios, scenarioId, onScenarioChange, loading, error,
}: {
  population: NflPlayPopulation | null
  scenarios: NflPlayScenario[]
  scenarioId: string
  onScenarioChange: (next: string) => void
  loading: boolean
  error: string | null
}) {
  const [filters, setFilters] = useState<PopulationFilters>(DEFAULT_FILTERS)
  const [limit, setLimit] = useState(400)
  const [speed, setSpeed] = useState<number>(1)
  const [running, setRunning] = useState(true)
  const [persistTrails, setPersistTrails] = useState(true)
  const [revealed, setRevealed] = useState(0)
  const [repaintKey, setRepaintKey] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const [showDensity, setShowDensity] = useState(true)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  const allPlays = useMemo<PopulationPlay[]>(() => population?.plays ?? [], [population])

  /** The selected population: filtered, then capped at the display limit. */
  const selected = useMemo(() => {
    const kept = allPlays.filter((play) => matches(play, filters))
    return kept.slice(0, limit)
  }, [allPlays, filters, limit])

  const matchedBeforeLimit = useMemo(
    () => allPlays.filter((play) => matches(play, filters)).length,
    [allPlays, filters],
  )

  // Any change to what is on screen restarts the accumulation, so the animation
  // always shows the CURRENT selection building rather than a stale pile.
  useEffect(() => {
    setRevealed(0)
    setRepaintKey((key) => key + 1)
  }, [selected])

  /*
   * Batched accumulation on a real clock.
   *
   * A timer rather than requestAnimationFrame, deliberately. rAF is suspended
   * outright whenever the document is not visible -- which includes embedded
   * and unfocused webviews, not just background tabs -- and a field frozen at
   * zero plays is indistinguishable from a broken one. A timer keeps running;
   * because the reveal is driven by ELAPSED TIME rather than by tick count, a
   * throttled timer produces a coarser animation, never a wrong one.
   *
   * The elapsed clamp matters for the same reason: returning to a long-hidden
   * tab must resume the accumulation, not dump nine hundred plays in one frame.
   * The fractional remainder carries between ticks so slow speeds still advance
   * instead of stalling whenever a tick's worth of plays rounds to zero.
   */
  const carry = useRef(0)
  useEffect(() => {
    if (!running || selected.length === 0) return
    let last = performance.now()
    let done = false
    const timer = window.setInterval(() => {
      if (done) return
      const now = performance.now()
      const elapsed = Math.min(0.25, (now - last) / 1000)
      last = now
      carry.current += elapsed * PLAYS_PER_SECOND * speed
      const whole = Math.floor(carry.current)
      if (whole <= 0) return
      carry.current -= whole
      setRevealed((current) => {
        const next = Math.min(selected.length, current + whole)
        // Stop the timer the moment the population is fully drawn, rather than
        // spinning on a no-op sixty times a second for as long as the page is
        // open -- and so nothing is still scheduled when the view unmounts.
        if (next >= selected.length) {
          done = true
          window.clearInterval(timer)
          carry.current = 0
        }
        return next
      })
    }, 16)
    return () => { done = true; window.clearInterval(timer) }
    // `repaintKey` belongs in these dependencies even though the timer never
    // reads it. The timer clears ITSELF once the population is fully drawn, so
    // without a dependency that changes on Restart, pressing Restart set the
    // counter back to zero and then nothing ever advanced it again -- the
    // button looked broken while the state behind it was correct.
  }, [running, selected, speed, repaintKey])

  const stats = useMemo(() => drawnStats(selected.slice(0, revealed)), [selected, revealed])
  // The field clips to the goal lines. If the artifact ever places a point
  // outside the field of play, the reader is told rather than shown a picture
  // quietly moved to fit.
  const offField = useMemo(() => outOfFieldPoints(selected), [selected])

  /*
   * The whole population's shape, filtered by the SAME play-type choice as the
   * paths so the two layers always describe the same selection. Team, down,
   * zone and game filters are deliberately NOT applied: the producer's cells
   * are binned by play type and field position only, so narrowing on anything
   * else would silently leave the density describing a wider population than
   * the paths drawn over it.
   */
  const density = useMemo(() => {
    if (!showDensity) return null
    const families = filters.family === 'BOTH' ? null : new Set([filters.family])
    return buildDensity(population?.population_spatial ?? null, families)
  }, [population, filters.family, showDensity])
  const population_ = population?.population_accounting ?? null
  const coverage = population?.coverage ?? null

  const patch = useCallback(
    (next: Partial<PopulationFilters>) => setFilters((current) => ({ ...current, ...next })),
    [],
  )

  const restart = useCallback(() => {
    setRevealed(0)
    setRepaintKey((key) => key + 1)
    setRunning(true)
  }, [])

  const clear = useCallback(() => {
    setRevealed(0)
    setRepaintKey((key) => key + 1)
    setRunning(false)
  }, [])

  const toggleFullscreen = useCallback(() => {
    const node = wrapRef.current
    if (!node) return
    if (document.fullscreenElement) {
      void document.exitFullscreen?.()
    } else {
      void node.requestFullscreen?.()
    }
  }, [])

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  // Every hook runs before the early returns below: a first, uncached visit
  // renders once while loading and again with data, and a hook declared after
  // those returns changed the hook count between the two (React error #310).
  // One point per ~2.5% of the reveal, so the curve fills smoothly without the
  // chart re-rendering on every single play.
  const accumulation = useMemo(() => {
    const step = Math.max(1, Math.round(Math.max(1, revealed) / 40))
    const points: { x: number; y: number }[] = []
    for (let index = 0; index <= revealed; index += step) points.push({ x: index, y: index })
    if (points.length === 0 || points[points.length - 1].x !== revealed) {
      points.push({ x: revealed, y: revealed })
    }
    return points
  }, [revealed])

  if (error) {
    return <p className="pop-empty">Play-level population unavailable: {error}</p>
  }
  if (loading && !population) {
    return <p className="pop-empty">Loading the simulated population…</p>
  }
  if (!population || !population.available) {
    return (
      <p className="pop-empty">
        No play-level population is published for this scenario.
        {population?.detail ? ` ${population.detail}` : ''}
      </p>
    )
  }

  const home = population.home_team?.abbreviation ?? 'HOME'
  const away = population.away_team?.abbreviation ?? 'AWAY'

  return (
    <div className={`pop${fullscreen ? ' pop--fullscreen' : ''}`} ref={wrapRef}>
      <header className="pop__head">
        <div>
          <p className="eyebrow">SIMULATION POPULATION</p>
          <h3>
            {away} at {home} · {scenarios.find((s) => s.scenario_id === scenarioId)?.label ?? scenarioId}
          </h3>
        </div>
        {/* The count that must never be ambiguous. */}
        <p className="pop__coverage">
          <strong>{stats.plays.toLocaleString()}</strong> drawn ·{' '}
          <strong>{matchedBeforeLimit.toLocaleString()}</strong> match the filters ·{' '}
          <strong>{(coverage?.plays_drawable ?? 0).toLocaleString()}</strong> have paths, from{' '}
          <strong>{coverage?.games_with_play_paths ?? 0}</strong> of{' '}
          <strong>{(coverage?.simulated_games ?? 0).toLocaleString()}</strong> simulated games
        </p>
      </header>

      <div className="pop__stage">
        <div className="pop__fieldwrap">
          <PopulationField
            plays={selected}
            revealed={revealed}
            persistTrails={persistTrails}
            repaintKey={repaintKey}
            homeAbbr={home}
            awayAbbr={away}
            height={fullscreen ? 560 : 380}
            density={density}
          />
          <ul className="pop-legend" aria-label="What the colours on the field mean">
            <li><i style={{ background: POPULATION_PALETTE.PASS }} /> Pass <small>dashed while airborne</small></li>
            <li><i style={{ background: POPULATION_PALETTE.RUN }} /> Run <small>solid ground trail</small></li>
            <li><i style={{ background: POPULATION_PALETTE.KICK }} /> Kick, punt, try</li>
            <li><i style={{ background: POPULATION_PALETTE.TURNOVER }} /> Turnover <small>at the modeled spot</small></li>
            <li><i className="pop-legend__ring" style={{ borderColor: POPULATION_PALETTE.SCORE }} /> Score</li>
            {density && (
              <li><i className="pop-legend__band" /> Shaded band <small>all {density.plays.toLocaleString()} simulated plays</small></li>
            )}
          </ul>
          {density ? (
            <p className="pop-note pop-note--density">
              The shaded band is every play in the population — all{' '}
              <b>{density.plays.toLocaleString()}</b> of them, binned by play type and field
              position by the producer. The lines are the{' '}
              <b>{(coverage?.plays_drawable ?? 0).toLocaleString()}</b> plays that have paths.
              {density.unplaced > 0 && (
                <> {density.unplaced.toLocaleString()} plays carry no placeable field position and
                are not in the band.</>
              )}
            </p>
          ) : population.population_spatial ? null : (
            <p className="pop-note pop-note--density">
              This scenario has no whole-population summary: its artifact predates the addition and
              cannot be regenerated, because the game has already kicked off.
            </p>
          )}
          <p className="pop-note">
            {population.geometry_notice}{' '}
            The rectangle is 120 yards: an end zone, the 100-yard field of play the producer's
            coordinates describe, and an end zone. A path may reach a goal line and never crosses
            it — a touchdown terminates there rather than being extended with coordinates nothing
            measured. Plays are spread across lanes for legibility: the producer re-centres every
            play laterally, so vertical position carries no cross-play meaning.
            {offField.plays > 0 && (
              <> <b>{offField.plays.toLocaleString()}</b> of the drawn plays carry{' '}
              {offField.points.toLocaleString()} point(s) outside the field of play; those points
              are clipped to the nearest goal line.</>
            )}
          </p>
        </div>

        {/* The live panel sits BESIDE the field, not under it. Watching plays
            accumulate and then scrolling to find out what they added up to are
            two different activities; this makes them one. */}
        <section className="pop-stats-block pop-stats-block--live">
          <h4>On this field <small>{stats.plays.toLocaleString()} plays drawn</small></h4>
          <dl>
            <div><dt>Pass / run mix</dt><dd>{pct(stats.passShare)} pass<small>{stats.passAttempts} attempts · {stats.rushes} rushes</small></dd></div>
            <div><dt>Completion rate</dt><dd>{pct(stats.completionRate)}<small>{stats.completions} of {stats.passAttempts} attempts</small></dd></div>
            <div><dt>Yards per play</dt><dd>{num(stats.yards.mean, 2)}<small>median {num(stats.yards.median, 0)} · over {stats.scrimmage} scrimmage plays</small></dd></div>
            <div><dt>Turnover rate</dt><dd>{pct(stats.turnoverRate, 2)}<small>{stats.turnovers} in {stats.scrimmage} scrimmage plays</small></dd></div>
            <div><dt>Scoring plays</dt><dd>{pct(stats.scoringRate, 2)}<small>{stats.scoring} in {stats.scrimmage} scrimmage plays</small></dd></div>
          </dl>
          <YardHistogram histogram={stats.histogram} />
          {/* Each point is one more saved play drawn onto the field. The step
              shape is deliberate: there is no "between" two plays to smooth
              through, and a smooth curve would imply there is. */}
          <AccumulationChart points={accumulation} height={100}
            label="Plays revealed" valueLabel={`${revealed.toLocaleString()} of ${selected.length.toLocaleString()}`} />
          <p className="pop-denominator">
            Every number here counts only the plays currently on the field — what the filters and
            the playback position leave selected. These are outcome-selected representative games,
            so this is not an unbiased sample of the population and no win probability is computed
            from it.
          </p>
        </section>

        <div className="pop__controls" aria-label="Population controls">
          <div className="pop__transport">
            <button type="button" onClick={() => setRunning((r) => !r)} className="pop-btn pop-btn--primary">
              {running ? <Pause size={15} /> : <Play size={15} />}
              {running ? 'Pause' : 'Play'}
            </button>
            <button type="button" onClick={restart} className="pop-btn"><RotateCcw size={14} /> Restart</button>
            <button type="button" onClick={clear} className="pop-btn"><Eraser size={14} /> Clear</button>
            <button type="button" onClick={toggleFullscreen} className="pop-btn">
              {fullscreen ? <Maximize2 size={14} /> : <Expand size={14} />} Fullscreen
            </button>
          </div>

          <label className="pop-ctl">
            <span>Speed</span>
            <div className="pop-seg" role="group" aria-label="Speed">
              {SPEEDS.map((value) => (
                <button
                  key={value} type="button"
                  className={`pop-seg__btn${speed === value ? ' pop-seg__btn--on' : ''}`}
                  onClick={() => setSpeed(value)}
                >{value}×</button>
              ))}
            </div>
          </label>

          <label className="pop-ctl">
            <span>Plays displayed <b>{limit}</b></span>
            <input
              type="range" min={25} max={1200} step={25} value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}
            />
          </label>

          <label className="pop-ctl">
            <span>Play type</span>
            <div className="pop-seg" role="group" aria-label="Play type">
              {(['BOTH', 'PASS', 'RUN'] as FamilyFilter[]).map((value) => (
                <button
                  key={value} type="button"
                  className={`pop-seg__btn${filters.family === value ? ' pop-seg__btn--on' : ''}`}
                  onClick={() => patch({ family: value })}
                >{value === 'BOTH' ? 'Both' : value === 'PASS' ? 'Pass' : 'Run'}</button>
              ))}
            </div>
          </label>

          <label className="pop-ctl">
            <span>Offense</span>
            <div className="pop-seg" role="group" aria-label="Offense">
              {(['BOTH', 'AWAY', 'HOME'] as TeamFilter[]).map((value) => (
                <button
                  key={value} type="button"
                  className={`pop-seg__btn${filters.team === value ? ' pop-seg__btn--on' : ''}`}
                  onClick={() => patch({ team: value })}
                >{value === 'BOTH' ? 'Both' : value === 'HOME' ? home : away}</button>
              ))}
            </div>
          </label>

          <label className="pop-ctl">
            <span>Scenario</span>
            <select aria-label="Scenario" value={scenarioId} onChange={(event) => onScenarioChange(event.target.value)}>
              {scenarios.map((scenario) => (
                <option key={scenario.scenario_id} value={scenario.scenario_id}>{scenario.label}</option>
              ))}
            </select>
          </label>

          <label className="pop-ctl">
            <span>Down</span>
            <select aria-label="Down" value={filters.down} onChange={(event) => patch({ down: event.target.value as DownGroup })}>
              {DOWN_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>

          <label className="pop-ctl">
            <span>Field position</span>
            <select aria-label="Field position" value={filters.zone} onChange={(event) => patch({ zone: event.target.value as FieldZone })}>
              {ZONE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>

          <div className="pop-ctl pop-ctl--checks">
            <label>
              <input
                type="checkbox" checked={filters.scoringOnly}
                onChange={(event) => patch({ scoringOnly: event.target.checked })}
              /> Scoring plays only
            </label>
            <label>
              <input
                type="checkbox" checked={filters.turnoversOnly}
                onChange={(event) => patch({ turnoversOnly: event.target.checked })}
              /> Turnovers only
            </label>
            <label>
              <input
                type="checkbox" checked={persistTrails}
                onChange={(event) => setPersistTrails(event.target.checked)}
              /> Keep trails
            </label>
            {population.population_spatial && (
              <label>
                <input
                  type="checkbox" checked={showDensity}
                  onChange={(event) => setShowDensity(event.target.checked)}
                /> Whole-population density
              </label>
            )}
          </div>

          <fieldset className="pop-ctl pop-ctl--games">
            <legend>Representative games</legend>
            {population.games.map((game) => {
              const on = !filters.games || filters.games.has(game.position)
              return (
                <label key={game.position}>
                  <input
                    type="checkbox" checked={on}
                    onChange={(event) => {
                      const next = new Set(filters.games ?? population.games.map((g) => g.position))
                      if (event.target.checked) next.add(game.position)
                      else next.delete(game.position)
                      patch({ games: next.size === population.games.length ? null : next })
                    }}
                  />
                  {game.label} <small>{game.away_score ?? ''}–{game.home_score ?? ''}</small>
                </label>
              )
            })}
          </fieldset>
        </div>
      </div>

      <div className="pop__stats">
        <section className="pop-stats-block pop-stats-block--population">
          <h4>
            Full simulated population
            <small>{(coverage?.simulated_games ?? 0).toLocaleString()} games · {(population_?.plays ?? 0).toLocaleString()} plays</small>
          </h4>
          {/* These arrive precomputed over all 1,000 draws. They do NOT move
              with the filters above, and they are kept here so a six-game
              sample can never borrow a thousand-game denominator. */}
          <dl>
            <div><dt>Completion rate</dt><dd>{pct(population_?.completionRate)}<small>over {(population_?.completionDenominator ?? 0).toLocaleString()} attempts</small></dd></div>
            <div><dt>Turnovers per game</dt><dd>{num(population_?.turnoversPerGame, 3)}<small>game-level, over {(coverage?.simulated_games ?? 0).toLocaleString()} games</small></dd></div>
            <div><dt>Yards per scrimmage play</dt><dd>{num(population_?.yardsPerScrimmagePlay?.mean, 3)}<small>over {(population_?.scrimmageDenominator ?? 0).toLocaleString()} plays</small></dd></div>
            <div><dt>Points per drive</dt><dd>{num(population_?.pointsPerDrive, 3)}<small>{(population_?.drives ?? 0).toLocaleString()} drives</small></dd></div>
            <div><dt>Plays per game</dt><dd>{num(population_?.playsPerGame?.mean, 1)}<small>game-level, {(population_?.playsPerGame?.count ?? 0).toLocaleString()} games</small></dd></div>
            <div><dt>Third-down conversion</dt><dd>{pct(population_?.thirdDownConversionRate)}<small>{(population_?.thirdDownAttempts ?? 0).toLocaleString()} attempts</small></dd></div>
          </dl>
          <p className="pop-denominator">{population_?.denominatorNotes}</p>
        </section>
      </div>
    </div>
  )
}

/** Yards gained, bucketed. Redrawn as the selection changes. */
function YardHistogram({ histogram }: { histogram: { bucket: number; count: number }[] }) {
  const max = Math.max(1, ...histogram.map((bin) => bin.count))
  if (histogram.length === 0) return <p className="pop-denominator">No scrimmage plays in this selection.</p>
  return (
    <div className="pop-hist" role="img" aria-label={
      `Yards gained per scrimmage play: ${histogram.map((b) => `${b.bucket} to ${b.bucket + 5} yards, ${b.count} plays`).join('; ')}`
    }>
      {histogram.map((bin) => (
        <div key={bin.bucket} className="pop-hist__bar" title={`${bin.bucket} to ${bin.bucket + 5} yards: ${bin.count}`}>
          <span style={{ height: `${(bin.count / max) * 100}%` }} />
          <small>{bin.bucket}</small>
        </div>
      ))}
    </div>
  )
}
