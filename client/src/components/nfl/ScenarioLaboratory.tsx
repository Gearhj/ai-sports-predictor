/**
 * A guided sensitivity laboratory for the play-level engine.
 *
 * WHAT A SCENARIO IS, AND IS NOT
 * Each scenario changes exactly one declared input and re-runs the SAME 1,000
 * seeds. That makes the aggregate differences a common-random-numbers
 * comparison: the same draws under two settings, so the difference is the
 * setting and not the luck. It is sensitivity analysis. It is not an estimated
 * causal effect and not a prediction about what a coach will do.
 *
 * THE PART THAT IS EASY TO GET WRONG
 * The population is seed-paired; the SIX PUBLISHED GAMES ARE NOT. Each scenario
 * picks its own Typical, its own High scoring and so on, so putting BASE's
 * "Typical" next to PASS_HEAVY's "Typical" and calling it the same game under
 * two settings would be a lie. Sometimes a draw does appear in both scenarios'
 * representative sets, and then the pairing is real. This page distinguishes
 * the two cases explicitly and defaults to the honest one.
 */
import { ChevronLeft, ChevronRight, Pause, Play as PlayIcon, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { PlayField } from './PlayField'
import { PLAY_TYPE_LABEL, clockLabel, isScoring, isTurnover, type Play } from './playGeometry'
import type { NflPlayRepresentative, NflPlayTheaterIndex } from '../../types/nfl'
import './nfltheater.css'

const STEP_MS = 1100

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}
function num(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? '—' : value.toFixed(digits)
}
function signed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—'
  const text = value.toFixed(digits)
  return value > 0 ? `+${text}` : text
}
function signedPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const text = `${(value * 100).toFixed(1)} pts`
  return value > 0 ? `+${text}` : text
}

/** The first play at which two seed-paired games stop agreeing. */
export function firstDivergence(a: Play[], b: Play[]): number | null {
  const limit = Math.min(a.length, b.length)
  for (let i = 0; i < limit; i += 1) {
    if (a[i].play_type !== b[i].play_type
      || a[i].after.yardline100 !== b[i].after.yardline100
      || a[i].after.homeScore !== b[i].after.homeScore
      || a[i].after.awayScore !== b[i].after.awayScore) return i
  }
  return a.length === b.length ? null : limit
}

function Metric({ label, base, challenger, delta, hint }: {
  label: string; base: string; challenger: string; delta: string; hint?: string
}) {
  const moved = delta !== '—' && !/^[+-]?0\.00 ?/.test(delta) && delta !== '0.0 pts'
  return (
    <tr className={moved ? 'is-moved' : ''}>
      <th scope="row">{label}{hint && <small>{hint}</small>}</th>
      <td>{base}</td>
      <td>{challenger}</td>
      <td className="nfl-delta">{delta}</td>
    </tr>
  )
}

export function ScenarioLaboratory({
  index, homeTeam, awayTeam, load,
}: {
  index: NflPlayTheaterIndex
  gameId: string
  homeTeam: string
  awayTeam: string
  load: (scenarioId: string, position: number) => Promise<NflPlayRepresentative | null>
}) {
  const scenarios = index.scenarios
  const base = scenarios.find((s) => s.scenario_id === 'BASE') ?? scenarios[0]
  const [challengerId, setChallengerId] = useState(
    scenarios.find((s) => s.scenario_id !== base?.scenario_id)?.scenario_id ?? '')
  const challenger = scenarios.find((s) => s.scenario_id === challengerId) ?? null

  const pairs = index.pairing.seed_paired_games[challengerId] ?? []
  const [paired, setPaired] = useState(true)
  const [slot, setSlot] = useState(0)

  // Which two games are on screen. Paired mode uses one draw published by both
  // scenarios; unpaired mode compares two same-labelled but different draws.
  const chosen = useMemo(() => {
    if (paired && pairs.length) {
      const pair = pairs[Math.min(slot, pairs.length - 1)]
      return {
        basePosition: pair.base.position,
        challengerPosition: pair.challenger.position,
        seed: pair.seed, simulationIndex: pair.simulation_index, real: true,
      }
    }
    return {
      basePosition: Math.min(slot, (base?.representatives.length ?? 1) - 1),
      challengerPosition: Math.min(slot, (challenger?.representatives.length ?? 1) - 1),
      seed: null as number | null, simulationIndex: null as number | null, real: false,
    }
  }, [paired, pairs, slot, base, challenger])

  const [left, setLeft] = useState<NflPlayRepresentative | null>(null)
  const [right, setRight] = useState<NflPlayRepresentative | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!base || !challenger) return
    let cancelled = false
    setLoading(true)
    Promise.all([
      load(base.scenario_id, chosen.basePosition),
      load(challenger.scenario_id, chosen.challengerPosition),
    ]).then(([a, b]) => {
      if (cancelled) return
      setLeft(a); setRight(b); setLoading(false)
    })
    return () => { cancelled = true }
  }, [base, challenger, chosen.basePosition, chosen.challengerPosition, load])

  const leftPlays = left?.plays ?? []
  const rightPlays = right?.plays ?? []
  const longest = Math.max(leftPlays.length, rightPlays.length)
  const [cursor, setCursor] = useState(0)
  const [playing, setPlaying] = useState(false)
  useEffect(() => { setCursor(0); setPlaying(false) }, [left, right])

  useEffect(() => {
    if (!playing || longest === 0) return
    const timer = window.setInterval(() => {
      setCursor((c) => {
        if (c >= longest - 1) { setPlaying(false); return c }
        return c + 1
      })
    }, STEP_MS)
    return () => window.clearInterval(timer)
  }, [playing, longest])

  const divergence = useMemo(
    () => (chosen.real && leftPlays.length && rightPlays.length
      ? firstDivergence(leftPlays, rightPlays) : null),
    [chosen.real, leftPlays, rightPlays])

  if (!base || !challenger) {
    return (
      <section className="nfl-scenarios">
        <h2>Scenario comparison</h2>
        <p>Only one scenario is registered for this event, so there is nothing to compare it against.</p>
      </section>
    )
  }

  const bs = base.score_summary
  const cs = challenger.score_summary
  const bp = base.population_drive_stats
  const cp = challenger.population_drive_stats
  const leftPlay = leftPlays[Math.min(cursor, leftPlays.length - 1)] ?? null
  const rightPlay = rightPlays[Math.min(cursor, rightPlays.length - 1)] ?? null
  const diverged = divergence !== null && cursor >= divergence

  return (
    <section className="nfl-scenarios" aria-label="Scenario comparison laboratory">
      <header className="nfl-theater__header">
        <div>
          <span className="nfl-kicker">SINGLE-FACTOR SENSITIVITY</span>
          <h2>What changes if one assumption changes?</h2>
          <p className="nfl-scenarios__intro">
            Every scenario re-runs the same {base.draw_count?.toLocaleString() ?? '1,000'} seeds with
            exactly one input moved. Because the draws are the same, the differences below are the
            setting and not the luck. This is sensitivity analysis, not a prediction about what a team
            will choose to do.
          </p>
        </div>
      </header>

      <div className="nfl-theater__selectors">
        <label>
          Baseline
          <select value={base.scenario_id} disabled>
            <option value={base.scenario_id}>{base.label}</option>
          </select>
        </label>
        <label>
          Compare against
          <select value={challengerId} onChange={(e) => { setChallengerId(e.target.value); setSlot(0) }}>
            {scenarios.filter((s) => s.scenario_id !== base.scenario_id).map((s) => (
              <option key={s.scenario_id} value={s.scenario_id}>{s.label}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="nfl-scenario-grid">
        <article className="nfl-scenario-card">
          <h3>{base.label}</h3>
          <p>{base.explanation}</p>
        </article>
        <article className="nfl-scenario-card nfl-scenario-card--pace">
          <h3>{challenger.label}</h3>
          <p>{challenger.explanation}</p>
          {challenger.turnover_sensitivity_meaning && challenger.scenario_id === 'ELEVATED_TURNOVER' && (
            <p className="nfl-theater__assumption">{challenger.turnover_sensitivity_meaning}</p>
          )}
        </article>
      </div>

      <h3>What changed</h3>
      <table className="nfl-delta-table">
        <caption>
          Over the same {base.draw_count?.toLocaleString() ?? '1,000'} seeds. Rows that moved are highlighted.
        </caption>
        <thead>
          <tr><th scope="col">Measure</th><th scope="col">{base.label}</th>
            <th scope="col">{challenger.label}</th><th scope="col">Change</th></tr>
        </thead>
        <tbody>
          <Metric label={`${homeTeam} win`} base={pct(bs.homeWinProbability)} challenger={pct(cs.homeWinProbability)}
            delta={signedPct(cs.homeWinProbability - bs.homeWinProbability)} />
          <Metric label={`${awayTeam} win`} base={pct(bs.awayWinProbability)} challenger={pct(cs.awayWinProbability)}
            delta={signedPct(cs.awayWinProbability - bs.awayWinProbability)} />
          <Metric label="Tie" hint="final-score tie, not overtime" base={pct(bs.tieProbability)}
            challenger={pct(cs.tieProbability)} delta={signedPct(cs.tieProbability - bs.tieProbability)} />
          <Metric label="Reaches overtime" base={pct(base.reaching_overtime_probability)}
            challenger={pct(challenger.reaching_overtime_probability)}
            delta={signedPct((challenger.reaching_overtime_probability ?? 0) - (base.reaching_overtime_probability ?? 0))} />
          <Metric label={`Expected ${homeTeam} score`} base={num(bs.expectedHomeScore)} challenger={num(cs.expectedHomeScore)}
            delta={signed(cs.expectedHomeScore - bs.expectedHomeScore)} />
          <Metric label={`Expected ${awayTeam} score`} base={num(bs.expectedAwayScore)} challenger={num(cs.expectedAwayScore)}
            delta={signed(cs.expectedAwayScore - bs.expectedAwayScore)} />
          <Metric label="Expected total" base={num(bs.expectedTotal)} challenger={num(cs.expectedTotal)}
            delta={signed(cs.expectedTotal - bs.expectedTotal)} />
          <Metric label="Expected margin" hint={`${homeTeam} minus ${awayTeam}`}
            base={num(bs.expectedMarginHomeMinusAway)} challenger={num(cs.expectedMarginHomeMinusAway)}
            delta={signed(cs.expectedMarginHomeMinusAway - bs.expectedMarginHomeMinusAway)} />
          <Metric label="Drives per game" base={num(bp?.mean_drives, 1)} challenger={num(cp?.mean_drives, 1)}
            delta={signed((cp?.mean_drives ?? 0) - (bp?.mean_drives ?? 0), 1)} />
          <Metric label="Plays per game" base={num(bp?.mean_plays, 1)} challenger={num(cp?.mean_plays, 1)}
            delta={signed((cp?.mean_plays ?? 0) - (bp?.mean_plays ?? 0), 1)} />
          <Metric label="Plays per drive" base={num(bp?.plays_per_drive)} challenger={num(cp?.plays_per_drive)}
            delta={signed((cp?.plays_per_drive ?? 0) - (bp?.plays_per_drive ?? 0))} />
          <Metric label="Points per drive" base={num(bp?.points_per_drive)} challenger={num(cp?.points_per_drive)}
            delta={signed((cp?.points_per_drive ?? 0) - (bp?.points_per_drive ?? 0))} />
        </tbody>
      </table>

      <div className="nfl-scenario-deltas">
        <div>
          <span>What changed</span>
          <strong>{signed(cs.expectedTotal - bs.expectedTotal, 1)} points</strong>
          on the expected total, and {signed((cp?.mean_drives ?? 0) - (bp?.mean_drives ?? 0), 1)} drives per game.
        </div>
        <div>
          <span>Why it matters</span>
          <strong>{Math.abs(cs.homeWinProbability - bs.homeWinProbability) < 0.02 ? 'Who wins barely moves' : 'The winner moves too'}</strong>
          {Math.abs(cs.homeWinProbability - bs.homeWinProbability) < 0.02
            ? ' — this assumption mostly changes how much scoring happens, not who comes out ahead.'
            : ` — ${homeTeam}'s win probability shifts by ${signedPct(cs.homeWinProbability - bs.homeWinProbability)}.`}
        </div>
      </div>

      <h3>Watch them side by side</h3>
      <div className={`nfl-pairing-banner ${chosen.real ? 'is-paired' : 'is-illustrative'}`}>
        {chosen.real
          ? <>These two games are <strong>the same draw</strong> (simulation #{chosen.simulationIndex}, seed{' '}
            {chosen.seed}) run under both settings. Differences you see are caused by the changed assumption.</>
          : <>These are <strong>two different draws</strong>, selected separately by each scenario. They are
            illustrative — do not read the difference between them as the effect of the assumption. The table
            above is the like-for-like comparison.</>}
      </div>

      <div className="nfl-theater__selectors">
        {pairs.length > 0 && (
          <label className="nfl-toggle">
            <input type="checkbox" checked={paired} onChange={(e) => { setPaired(e.target.checked); setSlot(0) }} />
            Use the seed-paired game
          </label>
        )}
        <label>
          {chosen.real ? 'Paired draw' : 'Representative'}
          <select value={slot} onChange={(e) => setSlot(Number(e.target.value))}>
            {(chosen.real ? pairs.map((pair, i) => ({
              value: i, text: `Draw #${pair.simulation_index} · ${pair.base.label} vs ${pair.challenger.label}`,
            })) : (base.representatives.map((rep, i) => ({ value: i, text: rep.label })))).map((option) => (
              <option key={option.value} value={option.value}>{option.text}</option>
            ))}
          </select>
        </label>
      </div>

      {divergence !== null && (
        <p className="nfl-theater__assumption" data-testid="divergence">
          {divergence === 0
            ? 'These two runs differ from the very first play.'
            : `The two runs are identical for ${divergence} plays, then diverge at play ${divergence + 1}.`}
        </p>
      )}

      {loading && <p className="nfl-theater__assumption">Loading both games…</p>}

      <div className="nfl-sync-fields">
        {[{ label: base.label, game: left, play: leftPlay, plays: leftPlays },
          { label: challenger.label, game: right, play: rightPlay, plays: rightPlays }].map((side) => (
          <div key={side.label} className={`nfl-sync-field${diverged ? ' is-diverged' : ''}`}>
            <h4>{side.label}</h4>
            {side.play ? (
              <PlayField
                play={side.play} homeTeam={homeTeam} awayTeam={awayTeam} progress={1}
                showTrails={false} caption={`${side.label} · ${side.game?.label ?? ''}`}
                height="short"
              >
                <span><strong>Play {side.play.play_number} of {side.plays.length}</strong> ·{' '}
                  {PLAY_TYPE_LABEL[side.play.play_type] ?? side.play.play_type}</span>
                <span>{side.play.description}</span>
              </PlayField>
            ) : <p className="nfl-theater__assumption">No play at this position.</p>}
          </div>
        ))}
      </div>

      <div className="nfl-transport" role="group" aria-label="Synchronized playback">
        <button type="button" className="secondary-button" onClick={() => { setCursor(0); setPlaying(false) }}
          aria-label="Restart both"><RotateCcw size={15} /></button>
        <button type="button" className="secondary-button" onClick={() => { setPlaying(false); setCursor((c) => Math.max(0, c - 1)) }}
          disabled={cursor === 0} aria-label="Previous play"><ChevronLeft size={15} /></button>
        <button type="button" className="primary-button" onClick={() => setPlaying((p) => !p)}
          aria-label={playing ? 'Pause both' : 'Play both'}>
          {playing ? <Pause size={15} /> : <PlayIcon size={15} />}{playing ? 'Pause' : 'Play both'}
        </button>
        <button type="button" className="secondary-button" onClick={() => { setPlaying(false); setCursor((c) => Math.min(longest - 1, c + 1)) }}
          disabled={cursor >= longest - 1} aria-label="Next play"><ChevronRight size={15} /></button>
        <input type="range" min={0} max={Math.max(0, longest - 1)} value={cursor}
          onChange={(e) => { setPlaying(false); setCursor(Number(e.target.value)) }}
          aria-label="Scrub both games" data-testid="sync-scrubber" />
        <output className="nfl-transport__count">{cursor + 1} / {longest}</output>
      </div>

      <ScoreRibbon leftPlays={leftPlays} rightPlays={rightPlays} cursor={cursor}
        leftLabel={base.label} rightLabel={challenger.label} />

      <details className="nfl-lab-evidence">
        <summary>Technical evidence and limitations</summary>
        <p>{index.pairing.population_note}</p>
        <p>{index.pairing.representative_note}</p>
        <p>{challenger.interpretation}</p>
        <p>{index.geometry_notice}</p>
        <ul>{index.limitations.map((limit) => <li key={limit}>{limit}</li>)}</ul>
      </details>
    </section>
  )
}

/** A compact running-score strip so divergence is visible without watching. */
function ScoreRibbon({ leftPlays, rightPlays, cursor, leftLabel, rightLabel }: {
  leftPlays: Play[]; rightPlays: Play[]; cursor: number; leftLabel: string; rightLabel: string
}) {
  const rows = [{ label: leftLabel, plays: leftPlays }, { label: rightLabel, plays: rightPlays }]
  const longest = Math.max(leftPlays.length, rightPlays.length, 1)
  return (
    <div className="nfl-score-ribbon" aria-label="Scoring and turnovers through both games">
      {rows.map((row) => (
        <div key={row.label} className="nfl-score-ribbon__row">
          <span>{row.label}</span>
          <div className="nfl-score-ribbon__track">
            {row.plays.map((play, i) => {
              if (!isScoring(play) && !isTurnover(play)) return null
              return (
                <i key={i} className={isScoring(play) ? 'is-score' : 'is-turnover'}
                  style={{ left: `${(i / longest) * 100}%` }}
                  title={`${clockLabel(play.before).period} ${clockLabel(play.before).clock} — ${play.description}`} />
              )
            })}
            <b style={{ left: `${(cursor / longest) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  )
}
