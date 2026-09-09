/**
 * Scenario Comparison — PRECOMPUTED alternate assumptions from the producer.
 *
 * A control appears only for an input the engine declares it consumes. M1
 * consumes no quarterback, injury or weather input, so those controls are
 * disabled rather than mapped to an arbitrary adjustment. No model runs here.
 */
import type { NflScenarioSupplement } from '../../types/nfl'
import { pct } from './OutcomeExplorer'

const UNSUPPORTED_CONTROLS = ['Quarterback change', 'Injury report', 'Weather']

export function ScenarioComparison({ supplements, requirement, homeTeam, awayTeam, m1Label }: {
  supplements: NflScenarioSupplement[]
  requirement: string | null
  homeTeam: string
  awayTeam: string
  m1Label: string
}) {
  const supplement = supplements[0] ?? null
  const summaries = supplement?.summaries ?? null

  return (
    <section className="scenarios" aria-label="Scenario Comparison">
      <div className="scenarios__controls" aria-label="Scenario controls">
        {UNSUPPORTED_CONTROLS.map((label) => {
          const supported = summaries?.inputsUsed.some((input) => input.toLowerCase().includes(label.split(' ')[0].toLowerCase())) ?? false
          return (
            <button key={label} type="button" className="secondary-button" disabled={!supported} title={supported ? label : `${m1Label} does not consume this input`}>
              {label}{supported ? '' : ' — not a model input'}
            </button>
          )
        })}
      </div>
      {!summaries ? (
        <div className="theater-empty">
          <h3>Scenario Comparison — awaiting precomputed scenarios</h3>
          <p>{requirement ?? 'No scenario supplement has been imported for this game.'}</p>
        </div>
      ) : (
        <>
          <p className="muted">
            Inputs this engine uses: {summaries.inputsUsed.join(', ')}. Baseline {homeTeam} {pct(summaries.baseline.homeWinProbability)} · {awayTeam} {pct(summaries.baseline.awayWinProbability)} · tie {pct(summaries.baseline.tieProbability)}.
          </p>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr><th>Scenario</th><th>Changed assumptions</th><th>{homeTeam} win</th><th>{awayTeam} win</th><th>Tie</th><th>Δ {homeTeam}</th></tr>
              </thead>
              <tbody>
                {summaries.scenarios.map((s) => (
                  <tr key={s.scenarioId}>
                    <td>{s.label ?? s.scenarioId}</td>
                    <td>{s.changedAssumptions.map((c) => `${c.input}: ${String(c.from)} → ${String(c.to)}`).join('; ')}</td>
                    <td>{pct(s.result.homeWinProbability)}</td>
                    <td>{pct(s.result.awayWinProbability)}</td>
                    <td>{pct(s.result.tieProbability)}</td>
                    <td>{pct(s.result.homeWinProbability - summaries.baseline.homeWinProbability, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}
