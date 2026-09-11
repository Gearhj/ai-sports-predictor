/**
 * Where every game on the slate stopped, and the first reason it stopped.
 *
 * WHY THIS IS SHAPED LIKE A FUNNEL AND NOT A TABLE
 * ------------------------------------------------
 * The question it answers is "fifteen games, one position — where did the
 * other fourteen go?", and that question has an order to it. A game must be
 * mappable before it can be forecast, forecast before a price means anything,
 * priced before an edge exists, and above the threshold before a position can.
 * A table of counts hides that order; a funnel is the order.
 *
 * THE ONE RULE THE READER HAS TO TRUST
 * ------------------------------------
 * Every game is counted ONCE, at the first gate it failed. So the reasons add
 * up to the games that did not finish — exactly, with nothing double-charged
 * — and that arithmetic is printed on the page rather than asserted, because
 * a funnel whose numbers do not reconcile is worse than no funnel.
 *
 * Reasons that mean "not yet" are styled as neutral rather than as failures.
 * A 7pm game whose freshness window opens at 3pm has not gone wrong at
 * lunchtime, and colouring it red would teach the reader to ignore the panel.
 */
import { useMemo, useState } from 'react'
import { AlertTriangle, ChevronDown, Clock } from 'lucide-react'

export interface FunnelStage {
  stage: string
  label: string
  games: number
  dropped: number
}

export interface FunnelReason {
  reason: string
  games: number
  explanation: string | null
}

export interface FunnelGame {
  game_id: string
  matchup: string
  starts_at: string
  cutoff: string
  reached: string
  blocked_at: string | null
  reason: string | null
  explanation?: string | null
  adjusted_edge_percentage?: number | null
  [key: string]: unknown
}

export interface CoverageFunnelData {
  /** Present so the shared resource hook can treat this like any other read. */
  data_mode?: 'live'
  schema_version: string
  league: string
  event_date: string
  minimum_edge_percentage: number
  policy: string | null
  stages: FunnelStage[]
  first_blocking_reasons: FunnelReason[]
  reasons_sum: number
  games: FunnelGame[]
}

/** Reasons that mean "not yet", which must not read as failures. */
const PENDING = new Set([
  'FRESHNESS_WINDOW_HAS_NOT_OPENED_YET',
  'QUALIFIED_AND_AWAITING_ITS_CUTOFF',
])

/** Reasons the policy produced on purpose. Also not failures. */
const BY_DESIGN = new Set([
  'BELOW_THRESHOLD',
  'RESEARCH_ONLY_NO_WAGERING_AUTHORITY',
])

function toneOf(reason: string | null): 'pending' | 'design' | 'blocked' {
  if (!reason) return 'design'
  if (PENDING.has(reason)) return 'pending'
  if (BY_DESIGN.has(reason)) return 'design'
  return 'blocked'
}

const humanise = (reason: string) =>
  reason.toLowerCase().replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

export function CoverageFunnel({ data }: { data: CoverageFunnelData }) {
  const [open, setOpen] = useState<string | null>(null)
  const widest = Math.max(1, ...data.stages.map((stage) => stage.games))
  const positioned = data.stages[data.stages.length - 1]?.games ?? 0
  const scheduled = data.stages[0]?.games ?? 0
  const balances = data.reasons_sum + positioned === scheduled

  const byReason = useMemo(() => {
    const map = new Map<string, FunnelGame[]>()
    for (const game of data.games) {
      if (!game.reason) continue
      const list = map.get(game.reason) ?? []
      list.push(game)
      map.set(game.reason, list)
    }
    return map
  }, [data.games])

  return (
    <div className="cf">
      <ol className="cf__stages">
        {data.stages.map((stage, index) => (
          <li key={stage.stage} className="cf__stage">
            <div className="cf__stage-head">
              <span>{stage.label}</span>
              <b>{stage.games}</b>
            </div>
            <div className="cf__track" role="img"
              aria-label={`${stage.label}: ${stage.games} of ${scheduled} games`}>
              <span style={{ width: `${(stage.games / widest) * 100}%` }} />
            </div>
            {index > 0 && stage.dropped > 0 && (
              <small className="cf__dropped">−{stage.dropped} here</small>
            )}
          </li>
        ))}
      </ol>

      <p className="cf__rule">
        Every game is counted once, at the first gate it failed —{' '}
        <b>{data.reasons_sum}</b> stopped + <b>{positioned}</b> positioned ={' '}
        <b>{scheduled}</b> scheduled{balances ? '.' : ' — these do not reconcile.'}
        {' '}Threshold {data.minimum_edge_percentage}%
        {data.policy ? <> from <code>{data.policy}</code></> : null}, unchanged.
      </p>

      <ul className="cf__reasons">
        {data.first_blocking_reasons.map((entry) => {
          const tone = toneOf(entry.reason)
          const expanded = open === entry.reason
          return (
            <li key={entry.reason} className={`cf__reason is-${tone}`}>
              <button type="button" aria-expanded={expanded}
                onClick={() => setOpen(expanded ? null : entry.reason)}>
                <span className="cf__reason-count">{entry.games}</span>
                <span className="cf__reason-name">
                  {tone === 'pending'
                    ? <Clock size={15} aria-hidden="true" />
                    : tone === 'blocked' ? <AlertTriangle size={15} aria-hidden="true" /> : null}
                  {humanise(entry.reason)}
                </span>
                <ChevronDown size={17} aria-hidden="true"
                  style={{ transform: expanded ? 'rotate(180deg)' : undefined }} />
              </button>
              {expanded && (
                <div className="cf__drill">
                  {entry.explanation && <p className="cf__explain">{entry.explanation}</p>}
                  <ul>
                    {(byReason.get(entry.reason) ?? []).map((game) => (
                      <li key={game.game_id}>
                        <b>{game.matchup}</b>
                        <span>
                          cutoff {new Date(game.cutoff).toLocaleTimeString('en-US', {
                            hour: 'numeric', minute: '2-digit',
                          })}
                          {game.adjusted_edge_percentage != null
                            ? ` · edge ${game.adjusted_edge_percentage.toFixed(2)}%`
                            : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
