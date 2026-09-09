/**
 * Research results — the producer's own machine-readable study, as imported.
 *
 * Rows come from a byte-verified handoff, never a hand-typed table. Cohort
 * size, window, raw-versus-calibrated, the benchmark timing class and the
 * paired uncertainty are shown as the study states them. A HISTORICAL_UNTIMED
 * benchmark is a historical reference, never an executable backtest.
 */
import { formatTimestamp } from '../../services/format'
import type { NflResearchResults } from '../../types/nfl'

const TIMING_LABEL: Record<string, string> = {
  HISTORICAL_UNTIMED: 'Historical reference (untimed lines) — not an executable backtest',
  TIMESTAMP_MATCHED: 'Timestamp-matched market comparison',
  NONE: 'No market benchmark (quote timing could not be proven)',
}

function num(value: unknown, digits = 4): string {
  return typeof value === 'number' ? value.toFixed(digits) : '—'
}

export function ResearchResultsPanel({ results }: { results: NflResearchResults }) {
  if (!results.available) {
    return (
      <section className="research-results theater-empty" aria-label="Research results">
        <h3>Research results — no study imported</h3>
        <p>{results.requirement}</p>
      </section>
    )
  }
  return (
    <section className="research-results" aria-label="Research results">
      {results.studies.map((study) => {
        const s = study.summaries
        if (!s) return null
        const paired = s.pairedUncertaintyVersusM1
        const rows = [...s.leaderboard].sort((x, y) => Number(x.multiclassLogLoss ?? 1) - Number(y.multiclassLogLoss ?? 1)).slice(0, 8)
        return (
          <article key={study.supplement_sha256} className="research-study">
            <header>
              <h3>Overnight model laboratory · run {s.runId ?? study.artifact_version}</h3>
              <span className="muted">
                Imported {study.imported_at_utc ? formatTimestamp(study.imported_at_utc) : '—'} · {study.contract_version} · handoff {study.supplement_sha256.slice(0, 12)}…
              </span>
            </header>
            <dl className="today-answer__meta">
              <div><dt>Common cohort</dt><dd>{s.cohort.games.toLocaleString()} games{s.cohort.common_cohort ? ' (shared by every pipeline)' : ''}</dd></div>
              <div><dt>Window</dt><dd>{s.cohort.window ?? '—'}</dd></div>
              <div><dt>Benchmark timing class</dt><dd>{TIMING_LABEL[s.benchmarkTimingClass] ?? s.benchmarkTimingClass}</dd></div>
              <div><dt>Selected pipeline</dt><dd>{s.developmentSelectedPipeline ?? '—'} (unpromoted)</dd></div>
            </dl>
            {paired && (
              <p>
                Calibrated finalist minus calibrated M1 log loss: <strong>{num(paired.candidateMinusM1LogLoss, 6)}</strong>
                {paired.clusterBootstrap95Ci ? ` · 95% ${(paired.cluster ?? 'block').toLowerCase().replaceAll('_', '-')} bootstrap [${num(paired.clusterBootstrap95Ci[0], 6)}, ${num(paired.clusterBootstrap95Ci[1], 6)}] over ${paired.temporalBlocks ?? '—'} blocks, ${paired.resamples ?? '—'} resamples` : ''}.
                {' '}{s.conclusion}
              </p>
            )}
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr><th>Pipeline</th><th>Family</th><th>Games</th><th>Raw log loss</th><th>Calibrated log loss</th><th>Brier</th><th>Margin MAE</th><th>Total MAE</th></tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.pipelineId} className={row.pipelineId === s.developmentSelectedPipeline ? 'is-selected' : ''}>
                      <td>{row.pipelineId}</td>
                      <td>{String(row.family ?? '—')}</td>
                      <td>{typeof row.games === 'number' ? row.games.toLocaleString() : '—'}</td>
                      <td>{num(row.multiclassLogLoss)}</td>
                      <td>{num(row.calibratedLogLoss)}</td>
                      <td>{num(row.multiclassBrier)}</td>
                      <td>{num(row.marginMae, 2)}</td>
                      <td>{num(row.totalMae, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted">
              Showing {rows.length} of {s.leaderboard.length} pipelines by raw log loss. Raw and calibrated columns are distinct producer figures.
              {s.marketBenchmark && !s.marketBenchmark.available ? ` Market benchmark: ${s.marketBenchmark.reason}` : ''}
              {s.earlySeason ? ` Early season: ${s.earlySeason.note}` : ''}
            </p>
          </article>
        )
      })}
    </section>
  )
}
