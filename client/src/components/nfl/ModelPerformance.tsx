/**
 * Model performance, answered before it is evidenced.
 *
 * The previous surface was a 24-row table of pipeline identifiers and six-decimal
 * log losses. Everything in it was true and none of it answered the four
 * questions a reader actually arrives with: which model are we using, did
 * anything beat it, was that difference real, and how does any of it compare to
 * the market. Those four answers are now the first thing on the page, in
 * words, and the table is still there underneath for anyone who wants it.
 *
 * The most important answer here is a negative one. The best challenger did
 * score better -- and its confidence interval crosses zero, which means the
 * result is consistent with no improvement at all. A page that showed only the
 * point estimate would be reporting a win that the evidence does not support.
 */
import { ChevronDown, Minus, TrendingDown } from 'lucide-react'
import { useState } from 'react'
import type { NflResearchResults, NflResearchStudy } from '../../types/nfl'

const TIMING_LABEL: Record<string, string> = {
  HISTORICAL_UNTIMED: 'Historical reference using untimed lines — not an executable backtest',
  TIMESTAMP_MATCHED: 'Timestamp-matched market comparison',
  NONE: 'No market benchmark',
}

/** Every metric here is an error or a loss, so smaller is better. Say so. */
const METRICS: Array<{ key: string; label: string; hint: string; digits: number }> = [
  { key: 'multiclassLogLoss', label: 'Log loss', digits: 4,
    hint: 'How surprised the model was by what actually happened, across win/loss/tie. Lower is better.' },
  { key: 'multiclassBrier', label: 'Brier score', digits: 4,
    hint: 'Average squared error of the predicted probabilities. Lower is better.' },
  { key: 'homeWinEce', label: 'Calibration error', digits: 4,
    hint: 'When the model says 70%, how often does it actually happen? This is the average gap. Lower is better.' },
  { key: 'marginMae', label: 'Margin error', digits: 2,
    hint: 'Average miss on the final margin, in points. Lower is better.' },
  { key: 'totalMae', label: 'Total error', digits: 2,
    hint: 'Average miss on the combined score, in points. Lower is better.' },
]

function num(value: unknown, digits = 4): string {
  return typeof value === 'number' ? value.toFixed(digits) : '—'
}

type Row = Record<string, unknown>

/** Where an interval sits relative to zero, as a 0..100 track position. */
function intervalTrack(low: number, high: number, point: number) {
  const span = Math.max(Math.abs(low), Math.abs(high), Math.abs(point)) * 1.15 || 1
  const at = (value: number) => ((value + span) / (2 * span)) * 100
  return { left: at(low), right: at(high), point: at(point), zero: at(0) }
}

function Study({ study }: { study: NflResearchStudy }) {
  const s = study.summaries
  const [showAll, setShowAll] = useState(false)
  const [calibrated, setCalibrated] = useState(true)
  if (!s) return null

  const leaderboard = (s.leaderboard ?? []) as Row[]
  const challengerId = s.developmentSelectedPipeline
  const challenger = leaderboard.find((row) => row.pipelineId === challengerId) ?? null
  const champion = leaderboard.find((row) => String(row.pipelineId).startsWith('M1')) ?? null
  const paired = s.pairedUncertaintyVersusM1
  const ci = paired?.clusterBootstrap95Ci
  const crossesZero = Boolean(ci && ci[0] < 0 && ci[1] > 0)
  const market = s.marketBenchmark as { available?: boolean; reason?: string } | undefined
  const calibration = s.calibration as { calibrated?: Row; method?: string } | undefined
  const early = s.earlySeason as { applies?: boolean; note?: string } | undefined

  const championRow = calibrated && calibration?.calibrated ? calibration.calibrated : champion
  const track = ci && paired ? intervalTrack(ci[0], ci[1], paired.candidateMinusM1LogLoss ?? 0) : null

  return (
    <article className="model-performance">
      <header className="model-performance__verdict">
        <span className="nfl-kicker">MODEL DECISION</span>
        <h2>{s.conclusion}</h2>
        <p>
          Measured on {s.cohort?.games?.toLocaleString() ?? '—'} games that every pipeline saw,
          from {s.cohort?.window ?? 'the development window'}.{' '}
          {s.cohort?.population_note}
        </p>
      </header>

      <div className="model-answers">
        <div className="model-answer">
          <h3>Which model is in use?</h3>
          <strong>{String(champion?.pipelineId ?? 'M1')}</strong>
          <p>The retained forecast model. Nothing on this page has changed it.</p>
        </div>
        <div className="model-answer">
          <h3>What was the best challenger?</h3>
          <strong>{challengerId ?? '—'}</strong>
          <p>Best of {leaderboard.length} pipelines by calibrated log loss. Not promoted.</p>
        </div>
        <div className={`model-answer ${crossesZero ? 'is-inconclusive' : 'is-decisive'}`}>
          <h3>Was the difference meaningful?</h3>
          <strong>{crossesZero ? 'No' : 'Yes'}</strong>
          <p>
            {crossesZero
              ? 'The challenger scored better, but its 95% interval includes zero — the result is consistent with no improvement at all.'
              : 'The interval excludes zero.'}
          </p>
        </div>
        <div className="model-answer is-unavailable">
          <h3>How does it compare to the market?</h3>
          <strong>{market?.available ? 'Compared' : 'Not comparable'}</strong>
          <p>{market?.reason ?? TIMING_LABEL[s.benchmarkTimingClass] ?? s.benchmarkTimingClass}</p>
        </div>
      </div>

      {track && paired && (
        <section className="model-interval" aria-label="Challenger advantage interval">
          <h3>How much better, and how sure?</h3>
          <p>
            Calibrated challenger minus calibrated {String(champion?.pipelineId ?? 'M1')} log loss.
            Negative is better for the challenger. The bar is the 95% interval over{' '}
            {paired.temporalBlocks ?? '—'} season-week blocks and {paired.resamples ?? '—'} resamples.
          </p>
          <div className="interval-track">
            <span className="interval-track__zero" style={{ left: `${track.zero}%` }} />
            <span className="interval-track__bar"
              style={{ left: `${track.left}%`, width: `${Math.max(1, track.right - track.left)}%` }} />
            <span className="interval-track__point" style={{ left: `${track.point}%` }} />
          </div>
          <div className="interval-legend">
            <span>{num(ci?.[0], 5)}</span>
            <span><strong>{num(paired.candidateMinusM1LogLoss, 5)}</strong> best estimate</span>
            <span>{num(ci?.[1], 5)}</span>
          </div>
          {crossesZero && (
            <p className="interval-note">
              <Minus size={13} aria-hidden="true" /> The interval crosses zero, so this is not
              evidence that the challenger is better.
            </p>
          )}
        </section>
      )}

      <section className="model-metrics" aria-label="Metric comparison">
        <div className="model-metrics__head">
          <h3>Where the two differ</h3>
          <label className="nfl-toggle">
            <input type="checkbox" checked={calibrated} onChange={(e) => setCalibrated(e.target.checked)} />
            Calibrated probabilities
          </label>
        </div>
        <p className="muted">
          {calibrated
            ? `Probabilities recalibrated by ${calibration?.method ?? 'the study’s stated method'}. Score errors are unaffected by calibration.`
            : 'Raw model output, before recalibration.'}
        </p>
        <table className="model-metric-table">
          <thead>
            <tr>
              <th scope="col">Measure <TrendingDown size={12} aria-hidden="true" /> lower is better</th>
              <th scope="col">{String(champion?.pipelineId ?? 'M1')}</th>
              <th scope="col">{challengerId ?? 'Challenger'}</th>
              <th scope="col">Difference</th>
            </tr>
          </thead>
          <tbody>
            {METRICS.map((metric) => {
              const a = championRow?.[metric.key]
              const b = challenger?.[metric.key]
              const delta = typeof a === 'number' && typeof b === 'number' ? b - a : null
              return (
                <tr key={metric.key}>
                  <th scope="row">
                    {metric.label}
                    <small>{metric.hint}</small>
                  </th>
                  <td>{num(a, metric.digits)}</td>
                  <td>{num(b, metric.digits)}</td>
                  <td className={delta === null ? '' : delta < 0 ? 'is-better' : delta > 0 ? 'is-worse' : ''}>
                    {delta === null ? '—' : `${delta > 0 ? '+' : ''}${delta.toFixed(metric.digits)}`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>

      {early && (
        <p className="model-scope">
          <strong>Full season only.</strong> {early.note}
        </p>
      )}

      <details className="nfl-lab-evidence" open={showAll}
        onToggle={(e) => setShowAll((e.target as HTMLDetailsElement).open)}>
        <summary>
          All {leaderboard.length} pipelines <ChevronDown size={13} aria-hidden="true" />
        </summary>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Pipeline</th><th scope="col">Family</th><th scope="col">Log loss</th>
                <th scope="col">Calibrated</th><th scope="col">Brier</th><th scope="col">ECE</th>
                <th scope="col">Margin MAE</th><th scope="col">Total MAE</th><th scope="col">Games</th>
              </tr>
            </thead>
            <tbody>
              {leaderboard.map((row) => (
                <tr key={String(row.pipelineId)}
                  className={row.pipelineId === challengerId ? 'is-selected' : ''}>
                  <th scope="row">{String(row.pipelineId)}</th>
                  <td>{String(row.family)}</td>
                  <td>{num(row.multiclassLogLoss, 6)}</td>
                  <td>{num(row.calibratedLogLoss, 6)}</td>
                  <td>{num(row.multiclassBrier, 5)}</td>
                  <td>{num(row.homeWinEce, 5)}</td>
                  <td>{num(row.marginMae, 3)}</td>
                  <td>{num(row.totalMae, 3)}</td>
                  <td>{typeof row.games === 'number' ? row.games.toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted">
          Study {s.runId} · {study.contract_version} · handoff {study.supplement_sha256.slice(0, 16)}…
          Imported from a byte-verified producer artifact, never hand-entered.
        </p>
      </details>
    </article>
  )
}

export function ModelPerformance({ results }: { results: NflResearchResults }) {
  if (!results.available) {
    return (
      <section className="research-results theater-empty" aria-label="Model performance">
        <h3>Model performance — no study imported</h3>
        <p>{results.requirement}</p>
      </section>
    )
  }
  return (
    <section className="research-results" aria-label="Model performance">
      {results.studies.map((study) => <Study key={study.supplement_sha256} study={study} />)}
    </section>
  )
}
