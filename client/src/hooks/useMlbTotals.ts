/**
 * Data access for the totals board, Parlay Watch, and recording a ticket.
 *
 * Two things are deliberate here.
 *
 * **Nothing fetches until its tab is open.** `enabled` gates each loader, so a
 * reader on the Prices tab never triggers a totals or parlay read. The server
 * side touches no provider either way; this just keeps the work off the wire.
 *
 * **The cache key carries every input that changes the answer** -- date, filter,
 * leg count, and the scenario lines -- because a key that forgets one renders
 * one board's numbers under another board's heading, which is the single worst
 * failure this page could have.
 */
import { useCallback, useMemo, useState } from 'react'
import { useApiResource } from './useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { apiRequest } from '../services/api'
import type {
  ParlayTicketBoard, ParlayTicketCandidate, ParlayWatch, TotalsBoard,
} from '../types/totals'

export function readMarketFilter(value: string | null): ParlayWatch['market_filter'] {
  const upper = (value ?? '').toUpperCase()
  return upper === 'OVERS_ONLY' || upper === 'TOTALS_ONLY' || upper === 'MIXED'
    ? upper
    : 'ALL'
}

export function useMlbTotals({
  day, enabled, oversOnly, scenarios,
}: {
  day: string
  enabled: boolean
  oversOnly: boolean
  scenarios: Record<string, string>
}) {
  // Only lines that parse. A half-typed "8." must not be sent as a scenario,
  // and an unparseable entry is simply not a scenario yet.
  const lines = useMemo(
    () => Object.entries(scenarios)
      .filter(([, value]) => value !== '' && Number.isFinite(Number(value)))
      .map(([gameId, value]) => `${gameId}:${Number(value)}`)
      .sort(),
    [scenarios],
  )

  const loader = useCallback(async (signal: AbortSignal): Promise<TotalsBoard | null> => {
    if (!enabled) return null
    const query = new URLSearchParams({ date: day })
    if (oversOnly) query.set('overs_only', 'true')
    for (const entry of lines) query.append('scenario_line', entry)
    return apiRequest<TotalsBoard>(`/mlb/totals?${query.toString()}`, {}, signal)
  }, [day, enabled, oversOnly, lines])

  return useApiResource(loader, {
    key: cacheKey('mlb/totals', { day, oversOnly, lines: lines.join(','), enabled }),
    staleTimeMs: STALE_TIME.slate,
  })
}

export function useParlayWatch({
  day, enabled, legCount, marketFilter,
}: {
  day: string
  enabled: boolean
  legCount: 2 | 3
  marketFilter: ParlayWatch['market_filter']
}) {
  const loader = useCallback(async (signal: AbortSignal): Promise<ParlayWatch | null> => {
    if (!enabled) return null
    const query = new URLSearchParams({
      date: day, legs: String(legCount), market_filter: marketFilter,
    })
    return apiRequest<ParlayWatch>(`/mlb/parlays?${query.toString()}`, {}, signal)
  }, [day, enabled, legCount, marketFilter])

  return useApiResource(loader, {
    key: cacheKey('mlb/parlays', { day, legCount, marketFilter, enabled }),
    staleTimeMs: STALE_TIME.slate,
  })
}

/**
 * Recording one ticket.
 *
 * The request sends identity only -- game, market, selection, book. Every price
 * and probability is re-derived server-side, so a ticket records what AISP held
 * rather than what this browser had on screen. The response says whether a row
 * was created or whether the identical ticket already existed; both are success,
 * and the difference is shown rather than hidden.
 */
/** Every recorded ticket, with its legs and their settlement status. */
export function useRecordedTickets(enabled: boolean) {
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!enabled) return null
    return apiRequest<ParlayTicketBoard>('/mlb/parlays/tickets', {}, signal)
  }, [enabled])
  return useApiResource<ParlayTicketBoard | null>(loader, {
    key: cacheKey('mlb/parlays/tickets', { enabled }),
    staleTimeMs: STALE_TIME.operational,
  })
}

export function useRecordTicket(onRecorded: () => void) {
  const [recordingKey, setRecordingKey] = useState<string | null>(null)
  const [recordedByKey, setRecorded] = useState<Record<string, string>>({})
  const [recordError, setError] = useState<string | null>(null)

  const recordTicket = useCallback(async (ticket: ParlayTicketCandidate) => {
    setRecordingKey(ticket.ticket_key)
    setError(null)
    try {
      const response = await apiRequest<{ created: boolean; detail: string }>(
        '/mlb/parlays/tickets',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sportsbook: ticket.sportsbook,
            stake: 1.0,
            funded: false,
            legs: ticket.legs.map((leg) => ({
              game_id: leg.game_id,
              market: leg.market,
              selection: leg.selection,
            })),
          }),
        },
      )
      setRecorded((old) => ({
        ...old,
        [ticket.ticket_key]: response.created
          ? 'Recorded. $1 research ticket, unfunded.'
          : 'Already recorded — nothing was written a second time.',
      }))
      onRecorded()
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Could not record the ticket.')
    } finally {
      setRecordingKey(null)
    }
  }, [onRecorded])

  return { recordTicket, recordingKey, recordedByKey, recordError }
}
