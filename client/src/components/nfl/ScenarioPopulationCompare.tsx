/**
 * BASE beside one sensitivity, both populations accumulating on the same clock.
 *
 * WHAT THE PAIRING ACTUALLY SUPPORTS
 * ----------------------------------
 * The 1,000-draw POPULATION is seed-paired: every scenario re-runs the same
 * seeds, so an aggregate difference between them is a common-random-numbers
 * comparison and may be read as the effect of the setting rather than of luck.
 * That is why the numbers under these fields are stated as differences.
 *
 * The SIX PUBLISHED GAMES are not paired. Each scenario re-selects its own
 * Typical / Close / High scoring draws, so the plays on the left and the plays
 * on the right are generally different draws. The two fields are therefore an
 * illustration of two distributions, not the same game twice, and this
 * component says so rather than letting synchronised motion imply otherwise.
 *
 * A scenario is a single-factor sensitivity. It is not an estimated causal
 * effect and not a prediction about what a coach will do.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Pause, Play, RotateCcw } from 'lucide-react'
import { PopulationField } from './PopulationField'
import { drawnStats, type PopulationPlay } from './populationGeometry'
import { useNflPlayPopulation } from '../../hooks/useNflPlayPopulation'
import type { NflPlayScenario } from '../../types/nfl'

const PLAYS_PER_SECOND = 110
const LIMIT = 320

function pct(value: number | null | undefined, digits = 1) {
  return value == null ? '—' : `${(value * 100).toFixed(digits)}%`
}
function signed(value: number | null | undefined, digits = 2) {
  if (value == null) return '—'
  const text = value.toFixed(digits)
  return value > 0 ? `+${text}` : text
}
function signedPts(value: number | null | undefined) {
  if (value == null) return '—'
  const text = `${(value * 100).toFixed(1)} pts`
  return value > 0 ? `+${text}` : text
}

export function ScenarioPopulationCompare({
  gameId, scenarios, scenarioId, onScenarioChange, homeAbbr, awayAbbr,
}: {
  gameId: string
  scenarios: NflPlayScenario[]
  scenarioId: string
  onScenarioChange: (next: string) => void
  homeAbbr: string
  awayAbbr: string
}) {
  const base = useNflPlayPopulation(gameId, 'BASE')
  const other = useNflPlayPopulation(gameId, scenarioId === 'BASE' ? '' : scenarioId)
  const [running, setRunning] = useState(true)
  const [revealed, setRevealed] = useState(0)
  const [repaintKey, setRepaintKey] = useState(0)
  const carry = useRef(0)

  const scenario = scenarios.find((entry) => entry.scenario_id === scenarioId) ?? null
  const isBase = scenarioId === 'BASE'

  const basePlays = useMemo<PopulationPlay[]>(
    () => (base.population?.plays ?? []).slice(0, LIMIT), [base.population],
  )
  const otherPlays = useMemo<PopulationPlay[]>(
    () => (other.population?.plays ?? []).slice(0, LIMIT), [other.population],
  )

  // One counter drives both fields, so the two populations build in step and a
  // difference in shape is a difference in the runs, never in the animation.
  const longest = Math.max(basePlays.length, otherPlays.length)

  useEffect(() => { setRevealed(0); setRepaintKey((key) => key + 1) }, [basePlays, otherPlays])

  useEffect(() => {
    if (!running || longest === 0) return
    let last = performance.now()
    let done = false
    const timer = window.setInterval(() => {
      if (done) return
      const now = performance.now()
      carry.current += Math.min(0.25, (now - last) / 1000) * PLAYS_PER_SECOND
      last = now
      const whole = Math.floor(carry.current)
      if (whole <= 0) return
      carry.current -= whole
      setRevealed((current) => {
        const next = Math.min(longest, current + whole)
        if (next >= longest) { done = true; window.clearInterval(timer); carry.current = 0 }
        return next
      })
    }, 16)
    return () => { done = true; window.clearInterval(timer) }
  }, [running, longest])

  const baseStats = useMemo(() => drawnStats(basePlays.slice(0, revealed)), [basePlays, revealed])
  const otherStats = useMemo(() => drawnStats(otherPlays.slice(0, revealed)), [otherPlays, revealed])

  const baseAcc = base.population?.population_accounting ?? null
  const otherAcc = other.population?.population_accounting ?? null
  const delta = scenario?.scenario_delta ?? null
  const turnoverDelta = baseAcc?.turnoversPerGame != null && otherAcc?.turnoversPerGame != null
    ? otherAcc.turnoversPerGame - baseAcc.turnoversPerGame
    : null

  const restart = () => { setRevealed(0); setRepaintKey((key) => key + 1); setRunning(true) }

  return (
    <section className="scencmp">
      <header className="scencmp__head">
        <div>
          <p className="eyebrow">SCENARIO COMPARISON</p>
          {/* The one sentence that explains the interaction. */}
          <h3>Choose a scenario to see how the simulated game changes.</h3>
        </div>
        <div className="scencmp__controls">
          <select
            aria-label="Scenario"
            value={scenarioId}
            onChange={(event) => onScenarioChange(event.target.value)}
          >
            {scenarios.map((entry) => (
              <option key={entry.scenario_id} value={entry.scenario_id}>{entry.label}</option>
            ))}
          </select>
          <button type="button" className="pop-btn pop-btn--primary" onClick={() => setRunning((r) => !r)}>
            {running ? <Pause size={14} /> : <Play size={14} />}{running ? 'Pause' : 'Play'}
          </button>
          <button type="button" className="pop-btn" onClick={restart}><RotateCcw size={14} /> Restart</button>
          <button
            type="button"
            className="pop-btn"
            onClick={() => onScenarioChange('BASE')}
            disabled={isBase}
          >Reset to BASE</button>
        </div>
      </header>

      {scenario && !isBase && (
        <p className="scencmp__param">
          <b>What changed:</b> {scenario.explanation}
          {scenario.parameters && (
            <code>
              {Object.entries(scenario.parameters)
                .filter(([key]) => key !== 'parameter' || true)
                .map(([key, value]) => `${key}=${String(value)}`)
                .join(' · ')}
            </code>
          )}
        </p>
      )}

      <div className="scencmp__fields">
        <figure>
          <figcaption>Base <small>{baseStats.plays} of {basePlays.length} plays drawn</small></figcaption>
          <PopulationField
            plays={basePlays} revealed={revealed} persistTrails
            repaintKey={repaintKey} homeAbbr={homeAbbr} awayAbbr={awayAbbr} height={250}
          />
        </figure>
        <figure>
          <figcaption>
            {isBase ? 'Pick a scenario to compare' : scenario?.label}
            <small>{isBase ? '—' : `${otherStats.plays} of ${otherPlays.length} plays drawn`}</small>
          </figcaption>
          {isBase
            ? <p className="scencmp__empty">Choose a sensitivity above to run it beside base.</p>
            : (
              <PopulationField
                plays={otherPlays} revealed={revealed} persistTrails
                repaintKey={repaintKey} homeAbbr={homeAbbr} awayAbbr={awayAbbr} height={250}
              />
            )}
        </figure>
      </div>

      {!isBase && (
        <>
          <table className="scencmp__table">
            <caption>
              Differences over the full 1,000-draw populations, which ARE seed-paired:
              every scenario re-runs the same seeds, so these are the same draws under two settings.
            </caption>
            <thead>
              <tr><th scope="col">Measure</th><th scope="col">Base</th><th scope="col">{scenario?.label}</th><th scope="col">Difference</th></tr>
            </thead>
            <tbody>
              <Row
                label="Expected total"
                base={scenarios.find((s) => s.scenario_id === 'BASE')?.score_summary?.expectedTotal}
                other={scenario?.score_summary?.expectedTotal}
                diff={delta?.expectedTotal}
              />
              <Row
                label="Expected margin (home)"
                base={scenarios.find((s) => s.scenario_id === 'BASE')?.score_summary?.expectedMarginHomeMinusAway}
                other={scenario?.score_summary?.expectedMarginHomeMinusAway}
                diff={delta?.expectedMarginHomeMinusAway}
              />
              <tr>
                <th scope="row">Home win probability</th>
                <td>{pct(scenarios.find((s) => s.scenario_id === 'BASE')?.score_summary?.homeWinProbability)}</td>
                <td>{pct(scenario?.score_summary?.homeWinProbability)}</td>
                <td className={cellClass(delta?.homeWinProbability)}>{signedPts(delta?.homeWinProbability)}</td>
              </tr>
              <Row
                label="Turnovers per game"
                base={baseAcc?.turnoversPerGame}
                other={otherAcc?.turnoversPerGame}
                diff={turnoverDelta}
                digits={3}
              />
            </tbody>
          </table>

          <p className="scencmp__note">
            A single-factor sensitivity: one declared input changes and the same seeds run again.
            It is not an estimated causal effect and not a prediction about what a coach will do.
            The six games drawn on each field are each scenario&rsquo;s own representatives, so the
            two fields are two distributions — not the same game twice.
          </p>
        </>
      )}
    </section>
  )
}

function cellClass(value: number | null | undefined): string {
  if (value == null || Math.abs(value) < 1e-9) return 'scencmp__flat'
  return value > 0 ? 'scencmp__up' : 'scencmp__down'
}

function Row({ label, base, other, diff, digits = 2 }: {
  label: string; base?: number | null; other?: number | null; diff?: number | null; digits?: number
}) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td>{base == null ? '—' : base.toFixed(digits)}</td>
      <td>{other == null ? '—' : other.toFixed(digits)}</td>
      <td className={cellClass(diff)}>{signed(diff, digits)}</td>
    </tr>
  )
}
