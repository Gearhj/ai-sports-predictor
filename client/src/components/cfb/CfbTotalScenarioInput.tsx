/**
 * Illustrative total scenarios.
 *
 * WHAT THIS IS NOT
 * ----------------
 * A betting surface. No sportsbook offered AISP a College Football market, so
 * there is no price, no expected return and no Over/Under recommendation here.
 * Every row carries the label rather than relying on one disclaimer at the
 * bottom that a scrolling reader will miss.
 *
 * PUSH ON A HALF-POINT LINE
 * -------------------------
 * Exactly zero, and it stays a number. The UI writes "No push" because "0.00%"
 * invites the reader to wonder whether it was rounded down from something; the
 * underlying value is a real zero and the sum of the three still reaches 1.
 */
import { useState } from 'react'
import type { CfbResearchForecast, CfbTotalScenario } from '../../types/cfbResearch'
import { fetchTotalScenario } from '../../hooks/useCfbResearch'

function pct(fraction: number): string {
  return `${(fraction * 100).toFixed(2)}%`
}

function pushCell(scenario: { push: number; push_possible: boolean }): string {
  return scenario.push_possible ? pct(scenario.push) : 'No push'
}

export function CfbTotalScenarioInput({ forecast }: { forecast: CfbResearchForecast }) {
  const [line, setLine] = useState('53.0')
  const [result, setResult] = useState<CfbTotalScenario | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function run(event: React.FormEvent) {
    event.preventDefault()
    const value = Number(line)
    if (!Number.isFinite(value)) {
      setError('Enter an integer or half-point total.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      setResult(await fetchTotalScenario(forecast.game.source_game_id, value))
    } catch {
      setError('That scenario could not be computed from the retained distribution.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="cfbtotals" data-testid="cfb-total-scenarios">
      <h4>Illustrative total scenarios</h4>
      <p className="cfbtotals__label" data-testid="cfb-totals-label">
        Illustrative line · no sportsbook quote recorded
      </p>

      <table className="cfbtotals__table">
        <thead>
          <tr><th>Line</th><th>Over</th><th>Push</th><th>Under</th></tr>
        </thead>
        <tbody>
          {forecast.illustrative_totals.map((entry) => (
            <tr key={String(entry.line)} data-testid={`cfb-total-${entry.line}`}>
              <td className="bx-num">{entry.line.toFixed(1)}</td>
              <td className="bx-num">{pct(entry.over)}</td>
              <td className="bx-num">
                {Number.isInteger(entry.line) ? pct(entry.push) : 'No push'}
              </td>
              <td className="bx-num">{pct(entry.under)}</td>
            </tr>
          ))}
          {result && (
            <tr className="cfbtotals__custom" data-testid="cfb-total-custom">
              <td className="bx-num">{result.line}</td>
              <td className="bx-num">{pct(result.over)}</td>
              <td className="bx-num">{pushCell(result)}</td>
              <td className="bx-num">{pct(result.under)}</td>
            </tr>
          )}
        </tbody>
      </table>

      <form className="cfbtotals__form" onSubmit={run}>
        <label htmlFor={`cfb-line-${forecast.game.source_game_id}`}>
          Try a line
        </label>
        <input
          id={`cfb-line-${forecast.game.source_game_id}`}
          type="number" step="0.5" inputMode="decimal"
          value={line} onChange={(event) => setLine(event.target.value)}
          data-testid="cfb-line-input"
        />
        <button type="submit" disabled={busy} data-testid="cfb-line-submit">
          {busy ? 'Reading…' : 'Compute'}
        </button>
      </form>

      {error && <p className="cfbtotals__error" role="alert">{error}</p>}
      {result && <p className="cfbtotals__note bx-dim">{result.note}</p>}
      <p className="cfbtotals__note bx-dim">
        The {forecast.pick.team} winner forecast is the visible research pick. The
        total scenario is analytical context, not an Over or Under pick.
      </p>
    </section>
  )
}
