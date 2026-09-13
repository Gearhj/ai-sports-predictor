/**
 * Where the experiment's games actually go.
 *
 * The experiment has staked money on very few games, and there are two wrong
 * stories a reader will reach for on their own: that the model almost never
 * finds an edge, or that fixing the missing prices would multiply the bets.
 * The first is wrong because most games were never priced at all. The second
 * is wrong because only a quarter of the games that WERE priced cleared the
 * threshold.
 *
 * So this shows every stage, and for every game that dropped out, the single
 * earliest gate it failed. The reasons at each stage sum exactly to that
 * stage's loss -- there is no "other" bucket to hide the awkward ones in.
 */
import { useCallback } from 'react'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { endpoints } from '../services/api'
import type { ExperimentFunnelData } from '../types/domain'

function Bar({ count, of }: { count: number; of: number }) {
  const width = of > 0 ? Math.max(1.5, (count / of) * 100) : 0
  return (
    <div className="funnel-bar" aria-hidden="true">
      <span style={{ width: `${width}%` }} />
    </div>
  )
}

export function ExperimentFunnel() {
  const resource = useApiResource<ExperimentFunnelData>(
    useCallback((signal: AbortSignal) => endpoints.experimentFunnel(signal), []),
    { key: 'experiment/funnel', staleTimeMs: STALE_TIME.operational },
  )
  const data = resource.data
  if (!data || !data.available) return null

  const first = data.stages[0]?.count ?? 0
  const priced = data.price_restoration

  return (
    <section className="panel experiment-lifetime-funnel" aria-labelledby="funnel-heading">
      <header>
        <h2 id="funnel-heading">Why so few positions?</h2>
        <p>
          Every game the experiment reached a decision on, and the one thing that stopped each
          game that did not become a position. A game is counted once, against the earliest gate
          it failed.
        </p>
      </header>

      <ol className="funnel-stages">
        {data.stages.map((stage) => (
          <li key={stage.key}>
            <div className="funnel-stage__head">
              <strong>{stage.count}</strong>
              <span>{stage.label}</span>
            </div>
            <Bar count={stage.count} of={first} />
            <p className="funnel-stage__explain">{stage.explanation}</p>
            {stage.reasons.length > 0 && (
              <ul className="funnel-reasons">
                {stage.reasons.map((reason) => (
                  <li key={reason.reason}>
                    <b>−{reason.count}</b>
                    <span>
                      <strong>{reason.label}</strong>
                      {reason.explanation}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>

      <div className="funnel-claim">
        <h3>More prices would mean more games judged, not more bets placed</h3>
        <p data-testid="funnel-claim">{priced.statement}</p>
        <p className="funnel-claim__method">{priced.method}</p>
      </div>

      <p className="funnel-thresholds" data-testid="funnel-thresholds">{data.thresholds_unchanged}</p>
    </section>
  )
}
