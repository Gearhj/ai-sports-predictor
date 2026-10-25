/**
 * The Active Wagers panel's reads. All are existing, read-only sources:
 *
 *   /command-center/ledger     funded positions and every money figure
 *                              (same cache key as the $1,000 Experiment page)
 *   /command-center/decisions  today's board: decision detail and "watching"
 *                              (same cache key as Home and Daily Decisions)
 *   /scoreboard                observed game state for the open positions only
 *
 * Nothing here writes, places, funds or settles anything.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { useScoreboard } from '../../hooks/useScoreboard'
import { apiRequest } from '../../services/api'
import {
  etDateOf, expectSchema, ledgerKey, loadLedger, useDecisionBoard,
  type DecisionBoard, type DecisionItem, type Ledger,
} from '../../services/commandCenter'
import { operationalToday } from '../../services/operationalDate'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import {
  activeWagers, decisionsByPosition, openPositions, summarize, watchingItems,
} from '../../services/activeWagers'

/** Decision boards for the other slate dates an open position sits on (usually none). */
function useOtherDecisionBoards(dates: string[]) {
  const key = [...new Set(dates)].sort().join(',')
  const loader = useCallback(async (signal: AbortSignal) => {
    const days = key ? key.split(',') : []
    const boards = await Promise.all(days.map(async (d) =>
      expectSchema(await apiRequest<DecisionBoard>(`/command-center/decisions?date=${encodeURIComponent(d)}`, {}, signal), 'AISP_COMMAND_CENTER_DECISIONS_V1')))
    return { data_mode: 'live' as const, items: boards.flatMap((b) => b.items) }
  }, [key])
  return useApiResource(loader, { key: cacheKey('active-wagers-decisions', { key }), staleTimeMs: STALE_TIME.slate })
}

export function useNow(intervalMs = 30_000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}

export function useActiveWagers() {
  const now = useNow()
  const ledger = useApiResource<Ledger>(loadLedger, {
    key: ledgerKey(), staleTimeMs: STALE_TIME.operational, refreshIntervalMs: 120_000,
  })
  const today = operationalToday(new Date(now))
  const board = useDecisionBoard(today)

  const open = useMemo(() => openPositions(ledger.data), [ledger.data])
  const gameIds = useMemo(() => open.map((p) => p.game_id).filter(Boolean), [open])
  const scoreboard = useScoreboard({ gameIds, enabled: gameIds.length > 0 })

  const otherDates = useMemo(() => [...new Set(open.map((p) => etDateOf(p.starts_at)).filter((d): d is string => !!d && d !== today))], [open, today])
  const others = useOtherDecisionBoards(otherDates)

  const decisionItems: DecisionItem[] = useMemo(
    () => [...(board.data?.items ?? []), ...((others.data as { items?: DecisionItem[] } | null)?.items ?? [])],
    [board.data, others.data],
  )
  const decisions = useMemo(() => decisionsByPosition(decisionItems), [decisionItems])
  const groups = useMemo(() => activeWagers(ledger.data, scoreboard.games, decisions, now), [ledger.data, scoreboard.games, decisions, now])
  const summary = useMemo(() => summarize(ledger.data, groups), [ledger.data, groups])
  const watching = useMemo(
    () => watchingItems(board.data?.items ?? [], new Set(open.map((p) => p.position_id)), now),
    [board.data, open, now],
  )

  return { now, ledger, board, scoreboard, groups, summary, watching }
}
