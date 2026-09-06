import { describe, expect, it } from 'vitest'
import {
  FORBIDDEN_STORY_WORDS,
  centralScores,
  gameStory,
  modelMarketDifference,
  percentOrUnavailable,
  probabilityComparison,
} from './simulationStory'
import type { MarketPulseRow } from '../types/domain'
import type { GameSimulationData } from '../types/simulation'

function simulation(overrides: Partial<GameSimulationData> = {}): GameSimulationData {
  return {
    data_mode: 'live',
    simulation_available: true,
    unavailable_reason: null,
    identity: {
      forecast_id: 'f1', forecast_version: 3, forecast_track: 'CURRENT_DEVELOPMENT',
      lifecycle: 'PREGAME', generated_at: '2026-09-06T18:24:41Z',
      available_as_of: '2026-09-06T18:24:41Z', model_name: 'ASP MLB', model_version: 'gen1',
      model_status: 'DEVELOPMENT', feature_snapshot_reference: 'sha256:abc',
      development_model: true, development_note: 'Gen 1 is a development model.',
    },
    play_by_play: { available: false, reason: 'no sequence', what_is_shown_instead: 'outcomes' },
    official_win_probability: { home: 0.44, away: 0.56 },
    simulation_win_probability: { home: 0.41, away: 0.59 },
    inputs: {
      expected_home_runs: 4.26, expected_away_runs: 4.56,
      simulations: 20000, seed: 20250601, simulator: 'independent-Poisson',
    },
    means: { home_runs: 4.33, away_runs: 4.63, total_runs: 8.96, run_difference: -0.3 },
    extra_inning_probability: 0.132,
    ...overrides,
  }
}

function pulseRow(overrides: Partial<MarketPulseRow> = {}): MarketPulseRow {
  return {
    game_id: 'g1', sport: 'BASEBALL', league: 'MLB',
    home_team: 'Home', away_team: 'Away', home_abbrev: 'HOU', away_abbrev: 'TEX',
    starts_at: '2026-09-06T23:10:00Z', status: 'scheduled',
    aisp_home_probability: 0.44, aisp_forecast_fresh: true,
    sportsbook_home_probability: 0.47,
    polymarket_home_probability: 0.46,
    kalshi_home_probability: 0.45,
    research_consensus_home_probability: null, consensus_eligible: false, consensus_note: null,
    diff_aisp_sportsbook: null, diff_aisp_prediction: null, diff_polymarket_kalshi: null,
    cross_market_dispersion: null,
    providers: [
      { provider: 'KALSHI', midpoint: 0.45, quality_tier: 'A', mapping_status: 'MAPPED', semantics_status: 'OK', quote_age_seconds: 120 },
      { provider: 'POLYMARKET', midpoint: 0.46, quality_tier: 'B', mapping_status: 'MAPPED', semantics_status: 'OK', quote_age_seconds: 45 },
    ],
    ...overrides,
  } as MarketPulseRow
}

describe('probability comparison', () => {
  it('puts every source on one home-win-probability scale', () => {
    const rows = probabilityComparison(simulation(), pulseRow())
    expect(rows.map((r) => r.key)).toEqual(['asp', 'sportsbook', 'kalshi', 'polymarket'])
    expect(rows[0].probability).toBeCloseTo(44, 5)
    expect(rows[1].probability).toBeCloseTo(47, 5)
    expect(rows[2].probability).toBeCloseTo(45, 5)
    expect(rows[3].probability).toBeCloseTo(46, 5)
  })

  it('labels prediction markets as observed markets, never model predictions', () => {
    const rows = probabilityComparison(simulation(), pulseRow())
    const kalshi = rows.find((r) => r.key === 'kalshi')!
    expect(kalshi.kind).toBe('observed_market')
    expect(kalshi.basis).toMatch(/not a model prediction/i)
    expect(rows.find((r) => r.key === 'asp')!.kind).toBe('model')
  })

  it('renders a missing sportsbook price as unavailable with a reason, never zero', () => {
    const rows = probabilityComparison(simulation(), pulseRow({ sportsbook_home_probability: null }))
    const book = rows.find((r) => r.key === 'sportsbook')!
    expect(book.probability).toBeNull()
    expect(book.unavailableReason).toMatch(/no complete two-sided/i)
  })

  it('renders missing prediction markets as unavailable', () => {
    const rows = probabilityComparison(simulation(), pulseRow({
      kalshi_home_probability: null, polymarket_home_probability: null,
    }))
    expect(rows.find((r) => r.key === 'kalshi')!.probability).toBeNull()
    expect(rows.find((r) => r.key === 'polymarket')!.probability).toBeNull()
  })

  it('treats a 0.0 sportsbook probability as unobserved, not as a real 0% quote', () => {
    // The upstream read model emits 0.0 when no quote exists. Rendering that as
    // "0.0%" printed a fabricated ~39 point edge against a market that never
    // quoted, which live acceptance caught.
    const rows = probabilityComparison(simulation(), pulseRow({ sportsbook_home_probability: 0 }))
    const book = rows.find((r) => r.key === 'sportsbook')!
    expect(book.probability).toBeNull()
    expect(book.unavailableReason).toMatch(/no complete two-sided/i)
    expect(modelMarketDifference(rows, 'sportsbook')).toBeNull()
  })

  it('treats an impossible 1.0 market probability as unobserved', () => {
    const rows = probabilityComparison(simulation(), pulseRow({ kalshi_home_probability: 1 }))
    expect(rows.find((r) => r.key === 'kalshi')!.probability).toBeNull()
  })

  it('handles a completely absent market row', () => {
    const rows = probabilityComparison(simulation(), null)
    expect(rows.filter((r) => r.probability == null)).toHaveLength(3)
    expect(rows.find((r) => r.key === 'asp')!.probability).toBeCloseTo(44, 5)
  })

  it('carries observation age for prediction markets', () => {
    const rows = probabilityComparison(simulation(), pulseRow())
    expect(rows.find((r) => r.key === 'kalshi')!.observedAgeSeconds).toBe(120)
  })
})

describe('model-market difference', () => {
  it('is computed only when both probabilities exist', () => {
    const rows = probabilityComparison(simulation(), pulseRow())
    expect(modelMarketDifference(rows, 'sportsbook')).toBeCloseTo(44 - 47, 5)
  })

  it('is null when the market side is unavailable, so no zero edge appears', () => {
    const rows = probabilityComparison(simulation(), pulseRow({ sportsbook_home_probability: null }))
    expect(modelMarketDifference(rows, 'sportsbook')).toBeNull()
  })

  it('is null when the model side is unavailable', () => {
    const rows = probabilityComparison(null, pulseRow({ aisp_home_probability: null }))
    expect(modelMarketDifference(rows, 'kalshi')).toBeNull()
  })
})

describe('game story', () => {
  it('describes the simulated conclusion in plain language', () => {
    const story = gameStory({
      simulation: simulation(), homeTeam: 'CLE', awayTeam: 'DET',
      decisionStatus: null, decisionReason: null,
    })
    expect(story).toMatch(/DET wins 56% of simulations/)
    expect(story).toMatch(/close/)
    expect(story).toMatch(/about 9\.0 total runs/)
  })

  it('never uses persuasive or certainty language', () => {
    const story = gameStory({
      simulation: simulation(), homeTeam: 'CLE', awayTeam: 'DET',
      decisionStatus: 'Passed', decisionReason: 'The price did not meet the policy threshold.',
    }).toLowerCase()
    for (const word of FORBIDDEN_STORY_WORDS) {
      expect(story).not.toContain(word.toLowerCase())
    }
    expect(story).not.toMatch(/should bet|recommend|edge of/i)
  })

  it('includes the official decision when one is supplied', () => {
    const story = gameStory({
      simulation: simulation(), homeTeam: 'CLE', awayTeam: 'DET',
      decisionStatus: 'Watching', decisionReason: 'Awaiting a complete quote.',
    })
    expect(story).toMatch(/Official decision: Watching\./)
  })

  it('states honestly when no simulation is available', () => {
    const story = gameStory({
      simulation: simulation({ simulation_available: false, means: undefined }),
      homeTeam: 'CLE', awayTeam: 'DET', decisionStatus: null, decisionReason: null,
    })
    expect(story).toMatch(/not available/i)
  })

  it('reflects a one-sided, higher-scoring game differently', () => {
    const story = gameStory({
      simulation: simulation({
        official_win_probability: { home: 0.72, away: 0.28 },
        means: { home_runs: 6.4, away_runs: 4.2, total_runs: 10.6, run_difference: 2.2 },
      }),
      homeTeam: 'CLE', awayTeam: 'DET', decisionStatus: null, decisionReason: null,
    })
    expect(story).toMatch(/one-sided/)
    expect(story).toMatch(/higher-scoring/)
    expect(story).toMatch(/CLE wins 72%/)
  })
})

describe('central scores', () => {
  const joint = {
    home_labels: ['0', '1', '2', '>1'],
    away_labels: ['0', '1', '2', '>1'],
    probabilities: [
      [0, 0.10, 0.05, 0],
      [0.20, 0, 0.05, 0],
      [0.30, 0.10, 0, 0],
      [0, 0, 0, 0.20],
    ],
    counts: [], cap: 2, note: '',
  }

  it('returns the smallest set of scores covering the requested mass', () => {
    const scores = centralScores(simulation({ joint_score_distribution: joint }), 0.5)
    expect(scores[0]).toEqual({ home: 2, away: 0, probability: 0.30 })
    expect(scores.reduce((s, c) => s + c.probability, 0)).toBeGreaterThanOrEqual(0.5)
  })

  it('excludes the overflow bucket, which is not an exact score', () => {
    const scores = centralScores(simulation({ joint_score_distribution: joint }), 1)
    expect(scores.every((s) => s.home < 3 && s.away < 3)).toBe(true)
  })

  it('returns nothing when no joint distribution exists', () => {
    expect(centralScores(simulation({ joint_score_distribution: undefined }))).toEqual([])
  })
})

describe('percent formatting', () => {
  it('never renders a missing value as zero', () => {
    expect(percentOrUnavailable(null)).toBe('Not available')
    expect(percentOrUnavailable(undefined)).toBe('Not available')
    expect(percentOrUnavailable(0)).toBe('0.0%')
  })
})
