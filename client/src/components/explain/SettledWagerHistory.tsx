/**
 * Settled wagers on the Historical Research surface.
 *
 * The Historical Game Explorer beside this panel is the 2025 Tier-A canonical
 * training population — a different thing entirely from the 2026 wagers the
 * experiment recorded. Attaching a wager's rationale to those rows would be a
 * category error, so the settled record gets its own panel and reuses the same
 * disclosure component the decision desk and the ledger already render.
 *
 * That reuse is the point. A settled wager's "Why this wager?" is built from
 * the decision's frozen evidence, so it reads here exactly as it did before the
 * result was known — which is the only thing that makes a historical rationale
 * worth reading at all.
 */
import { useCallback } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import { apiRequest } from '../../services/api'
import { WagerExplanation } from './WagerExplanation'
import { GameScoreboard } from '../scoreboard/GameScoreboard'
import { useScoreboard } from '../../hooks/useScoreboard'
import type { DecisionExplanation } from '../../types/explain'

interface SettledExplanations {
  schema_version: 'AISP_SETTLED_EXPLANATIONS_V1'
  explanations: DecisionExplanation[]
  settled: number
  with_event_evidence: number
  with_population_comparison: number
}

export function SettledWagerHistory({ limit = 25 }: { limit?: number }) {
  const loader = useCallback(
    (signal: AbortSignal) =>
      apiRequest<SettledExplanations>(`/explanations/settled?limit=${limit}`, {}, signal),
    [limit],
  )
  const resource = useApiResource(loader, {
    key: cacheKey('research/settled-explanations', { limit }),
    staleTimeMs: STALE_TIME.history,
  })
  // One read for the whole list. Called before any early return, because hooks
  // cannot be conditional; an empty id list simply asks for nothing.
  const rows = Array.isArray(resource.data?.explanations)
    ? resource.data.explanations : []
  const board = useScoreboard({
    gameIds: rows.map((row) => row.game_id).filter(Boolean),
    enabled: rows.length > 0,
  })

  if (resource.loading && !resource.data) {
    return <p className="muted" role="status">Loading settled wager explanations…</p>
  }
  // Guard on the array itself, not on the count. A payload that arrives without
  // `explanations` — an older build, a partial response — must render an empty
  // state, not throw and take the whole Historical Research page down with it.
  const explanations = rows
  if (explanations.length === 0) {
    return (
      <p className="muted" role="status">
        No settled wager has a bound decision record yet.
      </p>
    )
  }

  const settled = resource.data?.settled ?? explanations.length
  const withEvents = resource.data?.with_event_evidence ?? 0
  const withPopulation = resource.data?.with_population_comparison ?? 0
  return (
    <div data-testid="settled-wager-history">
      <p className="info-label" data-testid="settled-coverage">
        {settled} settled wagers · {withEvents} with bound event evidence ·{' '}
        {withPopulation} with a simulated population comparison
      </p>
      <div className="settled-history">
        {explanations.map((explanation) => (
          <article
            key={explanation.decision_id}
            className="settled-history__row"
            data-testid={`settled-${explanation.matchup?.label ?? explanation.game_id}`}
          >
            <header className="settled-history__head">
              <strong>{explanation.matchup?.label ?? 'Unknown matchup'}</strong>
              <span className="settled-history__selection">
                {explanation.why.selection}
              </span>
              <span
                className={`settled-history__result settled-history__result--${
                  explanation.what_happened?.result ?? 'open'}`}
              >
                {explanation.what_happened?.result_language ?? 'open'}
              </span>
              <span className="settled-history__date">{explanation.slate_date}</span>
            </header>
            {/* The observed final, beside the wager it settled. Shown for a
                completed wager because "what happened" is the question this
                surface exists to answer, and the period breakdown is the part
                a reader most often wants to check. */}
            <GameScoreboard
              game={board.games[explanation.game_id] ?? null}
              onRefresh={board.refresh}
            />
            {/* Collapsed by default, exactly as on the other two surfaces. */}
            <WagerExplanation explanation={explanation} />
          </article>
        ))}
      </div>
    </div>
  )
}
