/**
 * Game Center "Game Story" read model (`/probability/games/{id}/story`).
 *
 * The browser never simulates. Cells, distributions and line masses come from
 * the server's verified population; this module only classifies the stored
 * distribution values into win/loss/push regions for colouring, using the
 * producer contract's orientation:
 *
 *   margin value = home score − away score
 *   SPREAD, line attached to HOME:  HOME wins when margin + line > 0,
 *                                   pushes when margin + line = 0
 *   TOTAL:                          OVER wins when total > line,
 *                                   pushes when total = line
 *
 * A test reconciles the region sums against the server's masses.
 */
import { useCallback } from 'react'
import { useApiResource } from '../hooks/useApiResource'
import { apiRequest } from './api'
import { expectSchema, type SportCode } from './commandCenter'
import type { AssessedQuote, Channel, LegMasses } from './probability'
import { STALE_TIME, cacheKey } from './queryCache'

export interface StoryCell { away: number; home: number; count: number }
export interface StoryValue { value: number; count: number }

export interface StoryPopulation {
  draw_count: number
  population_sha256: string
  cells: StoryCell[]
  mode: StoryCell
  likely_region: { cells: { away: number; home: number }[]; count: number }
  margin: StoryValue[]
  total: StoryValue[]
}

export interface StoryLine {
  line: number
  /** SPREAD lines are attached to HOME; TOTAL lines have no side. */
  line_side: 'HOME' | null
  sides: Record<string, LegMasses>
}

export interface GameStory {
  schema_version: string
  game: {
    game_id: string
    sport: SportCode
    status: string
    starts_at: string | null
    home: { abbreviation: string; name: string } | null
    away: { abbreviation: string; name: string } | null
    final: { home: number; away: number } | null
    final_note: string | null
  }
  channel: Channel
  population: StoryPopulation | null
  model_vs_market: {
    model_home: number | null
    model_away: number | null
    model_tie: number | null
    model_home_two_way: number | null
    model_source: 'VERIFIED_POPULATION_FORECAST' | 'STORED_CONTROLLING_FORECAST' | null
    market_home_no_vig: number | null
    market_is_current: boolean
    market_books: number
  }
  lines: { spread: StoryLine | null; total: StoryLine | null; from: 'CURRENT_QUOTES' | 'STORED_QUOTES' | null }
  market: {
    price_state: string
    consensus_no_vig_home: number | null
    consensus_is_current: boolean
    best: Record<'HOME' | 'AWAY', { american: number | null; sportsbook: string | null; observed_at: string } | null>
    books: number
    freshest_observed_at: string | null
    quotes: AssessedQuote[]
  } | null
  facts: { kind: 'DISAGREEMENT' | 'FRESHNESS' | 'MISSING'; text: string }[]
  provider_calls_made: number
}

export function useGameStory(gameId: string | null | undefined) {
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!gameId) throw new Error('No canonical game id')
    return expectSchema(await apiRequest<GameStory>(`/probability/games/${encodeURIComponent(gameId)}/story`, {}, signal),
      'AISP_GAME_STORY_V1')
  }, [gameId])
  return useApiResource<GameStory>(loader, { key: cacheKey('game-story', { gameId }), staleTimeMs: STALE_TIME.game })
}

export type Region = 'WIN' | 'LOSS' | 'PUSH'
export type DistributionKind = 'margin' | 'total'

/**
 * The region of one stored distribution value for the selected side.
 * `line` is the quoted line: attached to HOME for a spread, the total for a total.
 */
export function regionOf(kind: DistributionKind, side: 'HOME' | 'AWAY' | 'OVER' | 'UNDER', line: number, value: number): Region {
  let adjusted: number
  if (kind === 'margin') {
    // HOME side of a HOME-attached line; AWAY holds the opposite line.
    adjusted = side === 'HOME' ? value + line : -value - line
  } else {
    adjusted = side === 'OVER' ? value - line : line - value
  }
  return adjusted > 0 ? 'WIN' : adjusted < 0 ? 'LOSS' : 'PUSH'
}

export function regionMasses(values: StoryValue[], drawCount: number, kind: DistributionKind,
  side: 'HOME' | 'AWAY' | 'OVER' | 'UNDER', line: number): Record<Region, number> {
  const out: Record<Region, number> = { WIN: 0, LOSS: 0, PUSH: 0 }
  for (const v of values) out[regionOf(kind, side, line, v.value)] += v.count
  return { WIN: out.WIN / drawCount, LOSS: out.LOSS / drawCount, PUSH: out.PUSH / drawCount }
}

/** Implied probability of an American price (includes the book's margin). */
export function impliedFromAmerican(american: number): number {
  return american > 0 ? 100 / (american + 100) : -american / (-american + 100)
}

export function americanFromProbability(p: number): number | null {
  if (!(p > 0 && p < 1)) return null
  return p >= 0.5 ? Math.round(-100 * p / (1 - p)) : Math.round(100 * (1 - p) / p)
}
