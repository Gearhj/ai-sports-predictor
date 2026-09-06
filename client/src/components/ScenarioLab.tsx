/**
 * What-If Scenario Lab.
 *
 * Two modes are kept visibly apart: the immutable Official Forecast, and a
 * clearly-labelled HYPOTHETICAL scenario recomputed by the backend through the
 * same feature mapping, ensemble and Monte Carlo the official forecast used.
 *
 * Every control here is enabled only because the server reported it SUPPORTED.
 * Unsupported controls are rendered disabled with the exact missing capability,
 * never as a cosmetic slider that nudges a probability.
 */
import { AlertTriangle, FlaskConical, RotateCcw, Undo2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { endpoints } from '../services/api'
import type {
  ScenarioCapabilities,
  ScenarioControl,
  ScenarioResult,
} from '../types/simulation'

const HYPOTHETICAL_NOTICE =
  'Hypothetical research scenario. This does not change ASP’s official forecast or recorded decision.'

function points(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(1)} pts`
}

function runs(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}`
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

export function ScenarioLab({
  gameId, homeTeam, awayTeam,
}: {
  gameId: string
  homeTeam: string
  awayTeam: string
}) {
  const [capabilities, setCapabilities] = useState<ScenarioCapabilities | null>(null)
  const [capabilityError, setCapabilityError] = useState<string | null>(null)
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  const [history, setHistory] = useState<Array<Record<string, number>>>([])
  const [result, setResult] = useState<ScenarioResult | null>(null)
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const requestId = useRef(0)

  useEffect(() => {
    let cancelled = false
    endpoints.scenarioCapabilities(gameId)
      .then((data) => { if (!cancelled) setCapabilities(data) })
      .catch((error: Error) => { if (!cancelled) setCapabilityError(error.message) })
    return () => { cancelled = true }
  }, [gameId])

  const supported = useMemo(
    () => (capabilities?.controls ?? []).filter((c) => c.status === 'SUPPORTED'),
    [capabilities],
  )
  const unsupported = useMemo(
    () => (capabilities?.controls ?? []).filter((c) => c.status !== 'SUPPORTED'),
    [capabilities],
  )

  const runScenario = useCallback(async (next: Record<string, number>) => {
    const id = ++requestId.current
    setRunning(true)
    setRunError(null)
    try {
      const data = await endpoints.runScenario(gameId, next)
      if (id === requestId.current) setResult(data)
    } catch (error) {
      if (id === requestId.current) setRunError((error as Error).message)
    } finally {
      if (id === requestId.current) setRunning(false)
    }
  }, [gameId])

  const apply = useCallback((key: string, value: number) => {
    setOverrides((current) => {
      setHistory((past) => [...past, current])
      const next = { ...current, [key]: value }
      void runScenario(next)
      return next
    })
  }, [runScenario])

  const reset = useCallback(() => {
    setHistory([])
    setOverrides({})
    setResult(null)
    setRunError(null)
  }, [])

  const undo = useCallback(() => {
    setHistory((past) => {
      if (past.length === 0) return past
      const previous = past[past.length - 1]
      setOverrides(previous)
      if (Object.keys(previous).length === 0) setResult(null)
      else void runScenario(previous)
      return past.slice(0, -1)
    })
  }, [runScenario])

  if (capabilityError) {
    return (
      <section className="scenario-lab" aria-labelledby="scenario-heading">
        <h2 id="scenario-heading">What-If Scenario Lab</h2>
        <p className="evidence-unavailable" role="status">
          Scenario capabilities could not be loaded, so no assumption can be changed.
        </p>
      </section>
    )
  }

  if (!capabilities) {
    return (
      <section className="scenario-lab" aria-labelledby="scenario-heading">
        <h2 id="scenario-heading">What-If Scenario Lab</h2>
        <p className="evidence-unavailable" role="status">Checking which assumptions this forecast can support…</p>
      </section>
    )
  }

  const active = Object.keys(overrides).length > 0

  return (
    <section className="scenario-lab" aria-labelledby="scenario-heading">
      <div className="scenario-lab__head">
        <div>
          <h2 id="scenario-heading">
            <FlaskConical size={17} aria-hidden="true" /> What-If Scenario Lab
          </h2>
          <p>
            The official forecast is immutable. A scenario recomputes it through the same
            model pipeline under assumptions you choose.
          </p>
        </div>
        <span className={`scenario-badge ${active ? 'scenario-badge--active' : ''}`}>
          {active ? 'HYPOTHETICAL' : 'OFFICIAL FORECAST'}
        </span>
      </div>

      <p className="scenario-notice" role="note">
        <AlertTriangle size={15} aria-hidden="true" /> {HYPOTHETICAL_NOTICE}
      </p>

      <div className="scenario-controls">
        {supported.length === 0 && (
          <p className="evidence-unavailable" role="status">
            No assumption on this forecast can be recomputed honestly today.
          </p>
        )}
        {supported.map((control) => (
          <SupportedControl
            key={control.key}
            control={control}
            value={overrides[control.key] ?? Number(control.current_value)}
            changed={control.key in overrides}
            onCommit={(value) => apply(control.key, value)}
          />
        ))}
      </div>

      {unsupported.length > 0 && (
        <details className="scenario-unsupported">
          <summary>{unsupported.length} assumptions this model cannot recompute</summary>
          <ul>
            {unsupported.map((control) => (
              <li key={control.key}>
                <strong>{control.label}</strong>
                <span className="scenario-status">{control.status}</span>
                <p>{control.reason}</p>
                {control.required_capability && (
                  <p className="scenario-required">Needs: {control.required_capability}</p>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="scenario-actions">
        <button type="button" onClick={reset} disabled={!active && !result}>
          <RotateCcw size={14} aria-hidden="true" /> Reset to official
        </button>
        <button type="button" onClick={undo} disabled={history.length === 0}>
          <Undo2 size={14} aria-hidden="true" /> Undo last change
        </button>
        {running && <span className="scenario-running" role="status">Recomputing…</span>}
        {runError && <span className="scenario-error" role="alert">Scenario failed: {runError}</span>}
      </div>

      {active && (
        <p className="scenario-assumptions">
          <strong>Active assumptions:</strong>{' '}
          {Object.entries(overrides)
            .map(([key, value]) => {
              const control = capabilities.controls.find((c) => c.key === key)
              return `${control?.label ?? key} = ${value}${control?.unit ?? ''}`
            })
            .join(' · ')}
        </p>
      )}

      {result && active && (
        <ScenarioComparison result={result} homeTeam={homeTeam} awayTeam={awayTeam} />
      )}
    </section>
  )
}

function SupportedControl({
  control, value, changed, onCommit,
}: {
  control: ScenarioControl
  value: number
  changed: boolean
  onCommit: (value: number) => void
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => { setDraft(value) }, [value])
  const outOfDomain = control.domain_min != null && control.domain_max != null
    && (draft < control.domain_min || draft > control.domain_max)

  return (
    <div className={`scenario-control ${changed ? 'is-changed' : ''}`}>
      <label htmlFor={`control-${control.key}`}>
        <span className="scenario-control__label">{control.label}</span>
        <span className="scenario-control__values">
          official <strong>{control.current_value}{control.unit}</strong>
          {changed && <> → scenario <strong>{draft}{control.unit}</strong></>}
        </span>
      </label>
      <input
        id={`control-${control.key}`}
        type="range"
        min={control.min}
        max={control.max}
        step={control.step}
        value={draft}
        onChange={(event) => setDraft(Number(event.target.value))}
        onMouseUp={() => onCommit(draft)}
        onKeyUp={() => onCommit(draft)}
        onTouchEnd={() => onCommit(draft)}
        aria-describedby={outOfDomain ? `domain-${control.key}` : undefined}
      />
      <p className="scenario-control__path">{control.model_path}</p>
      {outOfDomain && (
        <p className="scenario-control__domain" id={`domain-${control.key}`} role="status">
          Outside the range this model was validated on
          ({control.domain_min}–{control.domain_max}{control.unit}).
        </p>
      )}
    </div>
  )
}

function ScenarioComparison({
  result, homeTeam, awayTeam,
}: { result: ScenarioResult; homeTeam: string; awayTeam: string }) {
  const { official, scenario, deltas } = result
  const line = Object.keys(official.home_cover_probabilities)[0]
  const totalLine = Object.keys(official.total_probabilities)[0]

  const rows = [
    { label: `${homeTeam} win probability`, o: pct(official.home_win_probability), s: pct(scenario.home_win_probability), d: points(deltas.home_win_probability_points) },
    { label: `${awayTeam} win probability`, o: pct(official.away_win_probability), s: pct(scenario.away_win_probability), d: points(deltas.away_win_probability_points) },
    { label: `Expected ${homeTeam} runs`, o: official.expected_home_runs.toFixed(2), s: scenario.expected_home_runs.toFixed(2), d: runs(deltas.expected_home_runs) },
    { label: `Expected ${awayTeam} runs`, o: official.expected_away_runs.toFixed(2), s: scenario.expected_away_runs.toFixed(2), d: runs(deltas.expected_away_runs) },
    { label: 'Expected total runs', o: official.expected_total_runs.toFixed(2), s: scenario.expected_total_runs.toFixed(2), d: runs(deltas.expected_total_runs) },
    { label: `Run line ${line}`, o: pct(official.home_cover_probabilities[line]), s: pct(scenario.home_cover_probabilities[line]), d: points(deltas.home_cover_points[line]) },
    { label: `Over ${totalLine}`, o: pct(official.total_probabilities[totalLine].over), s: pct(scenario.total_probabilities[totalLine].over), d: points(deltas.total_over_points[totalLine]) },
  ]

  return (
    <div className="scenario-comparison">
      <p className="scenario-interpretation">{result.interpretation}</p>

      {result.baseline_reproduction && !result.baseline_reproduction.exact && (
        <p className="scenario-error" role="alert">
          The scenario pipeline did not reproduce the official forecast exactly
          (max difference {result.baseline_reproduction.max_absolute_delta}). Treat this
          comparison with caution.
        </p>
      )}

      <table className="scenario-table">
        <caption className="sr-only">Official forecast compared with the hypothetical scenario</caption>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col">Official</th>
            <th scope="col">Scenario <span className="scenario-badge scenario-badge--inline">HYPOTHETICAL</span></th>
            <th scope="col">Change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td>{row.o}</td>
              <td className="scenario-table__scenario">{row.s}</td>
              <td className={row.d.startsWith('+') ? 'delta-up' : row.d.startsWith('-') ? 'delta-down' : ''}>{row.d}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {result.sensitivity.length > 0 && (
        <div className="scenario-sensitivity">
          <h3>Which assumption mattered most</h3>
          <ul>
            {result.sensitivity.map((row) => (
              <li key={row.key}>
                <span>{row.key.replace(/_/g, ' ')}</span>
                <strong>{points(row.home_win_probability_points)}</strong>
                <small>{runs(row.expected_total_runs)} runs</small>
              </li>
            ))}
          </ul>
          <p className="scenario-sensitivity__note">
            Each effect is recomputed on its own against the official forecast. Combined
            effects need not equal the sum of the isolated effects.
          </p>
        </div>
      )}

      <dl className="scenario-identity">
        <div><dt>Status</dt><dd>{result.status}</dd></div>
        <div><dt>Baseline forecast</dt><dd className="mono">{result.identity.baseline_forecast_id}</dd></div>
        <div><dt>Baseline model</dt><dd>{result.identity.baseline_model_name} {result.identity.baseline_model_version}</dd></div>
        <div><dt>Evidence cutoff</dt><dd>{String(result.identity.evidence_cutoff)}</dd></div>
        <div><dt>Simulations / seed</dt><dd>{result.identity.simulations.toLocaleString()} · {result.identity.seed}</dd></div>
        <div><dt>Scenario hash</dt><dd className="mono">{result.identity.scenario_hash.slice(0, 24)}</dd></div>
      </dl>
    </div>
  )
}
