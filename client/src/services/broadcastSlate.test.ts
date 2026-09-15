/**
 * The slate puts two sports in one list, so the ways it can lie are the ways
 * worth testing: silently comparing a percentage against a fraction, reusing
 * a signed edge that was published against the other side, and inventing a
 * market comparison out of half a quote.
 */
import { describe, expect, it } from 'vitest'
import { conditionalHomeWin, isUpcoming, mlbSlate, nflSlate, priceStatusOf } from './broadcastSlate'
import type { DashboardData, ForecastGame } from '../types/domain'
import type { NflEngine, NflEvent } from '../types/nfl'

function mlbGame(over: Partial<ForecastGame> = {}): ForecastGame {
  return {
    id: 'game-1', league_code: 'MLB', start_time: '2026-09-10T16:15:00Z', status: 'SCHEDULED',
    venue: 'Park',
    away_team: { id: 'a', abbreviation: 'TB', name: 'Tampa Bay Rays', city: 'Tampa Bay', record: '87-58', color: '#092C5C' },
    home_team: { id: 'h', abbreviation: 'ATL', name: 'Atlanta Braves', city: 'Atlanta', record: '85-61', color: '#CE1141' },
    projected_away_runs: 4.4, projected_home_runs: 4.6, projected_total: 9.0,
    projected_run_difference: 0.14, uncertainty: 0.02,
    away_win_probability: 48.15, home_win_probability: 51.85,
    market_home_probability: 53.1, market_available: true,
    best_away_moneyline: 110, best_home_moneyline: -118,
    fair_away_moneyline: 108, fair_home_moneyline: -108,
    model_edge: 1.25, edge_side: 'away', opportunity: 'NO BET',
    confidence: 58.1, data_readiness: 67.5, data_as_of: '2026-09-10T13:29:51Z',
    ...over,
  } as unknown as ForecastGame
}

const dashboard = (games: ForecastGame[]): DashboardData =>
  ({ data_mode: 'live', date: '2026-09-10', games } as unknown as DashboardData)

function nflEngine(over: Partial<NflEngine> = {}): NflEngine {
  return {
    engine_id: 'NFL_M1_XGBOOST', engine_version: 'v1', model_label: 'M1',
    forecast_id: 'f1', snapshot_type: 'CURRENT', snapshot_version: 1, status: 'RESEARCH_SHADOW',
    home_win_probability: 0.5736, away_win_probability: 0.3966, tie_probability: 0.0298,
    tie_note: '', expected_home_score: 22.7, expected_away_score: 19.9,
    expected_margin_home: 2.8, expected_total_score: 42.6,
    evidence_readiness: { score: 78, threshold: 50, label: '', missing_input_count: 44, explanation: '' },
    evidence_basis: { model_inputs: '', not_consumed: [], note: '' },
    timestamps: { source_evidence_utc: '2026-08-29T14:07:00Z', published_utc: '2026-08-29T14:07:00Z' },
    ...over,
  } as unknown as NflEngine
}

function nflEvent(engine: NflEngine): NflEvent {
  return {
    event_id: '2026_01_NE_SEA', canonical_game_id: 'nfl-game-1', season: 2026, week: 1,
    kickoff_utc: '2026-09-10T00:20:00Z', state: 'UPCOMING', final_score: null,
    home: { team_id: 'h', abbreviation: 'SEA', name: 'Seattle Seahawks' },
    away: { team_id: 'a', abbreviation: 'NE', name: 'New England Patriots' },
    engines: [engine], snapshot_history_count: 1, drive_previews: [],
    supplements: { joint_engines: [], drive_engines: [], scenario_engines: [] },
    lab_available: true, analysis_href: '', lab_href: '',
  } as unknown as NflEvent
}

describe('mlbSlate', () => {
  it('converts percentages to fractions exactly once', () => {
    const [row] = mlbSlate(dashboard([mlbGame()]))
    expect(row.homeWin).toBeCloseTo(0.5185, 6)
    expect(row.awayWin).toBeCloseTo(0.4815, 6)
    expect(row.marketHome).toBeCloseTo(0.531, 6)
  })

  it('computes the edge on the home side rather than reusing model_edge', () => {
    // `model_edge` is published against `edge_side` -- here the AWAY side --
    // so rendering it in a column headed "model minus market" for the home
    // team would state the wrong sign on the wrong team.
    const [row] = mlbSlate(dashboard([mlbGame()]))
    expect(row.edgePoints).toBeCloseTo(51.85 - 53.1, 6)
    expect(row.edgePoints!).toBeLessThan(0)
  })

  it('reports no edge at all when there is no market probability', () => {
    const [row] = mlbSlate(dashboard([mlbGame({ market_home_probability: null } as Partial<ForecastGame>)]))
    expect(row.edgePoints).toBeNull()
  })

  it('never shows a price when the market is unavailable', () => {
    const [row] = mlbSlate(dashboard([mlbGame({ market_available: false } as Partial<ForecastGame>)]))
    expect(row.bestHomePrice).toBeNull()
    expect(row.bestAwayPrice).toBeNull()
  })

  it('judges freshness from the persisted price time, not the forecast data time', () => {
    const now = Date.parse('2026-09-15T18:00:00Z')
    expect(priceStatusOf('2026-09-15T17:00:00Z', true, now)).toEqual({ status: 'CURRENT', ageMinutes: 60 })
    expect(priceStatusOf('2026-08-29T22:08:45Z', true, now).status).toBe('STORED_REFERENCE')
    expect(priceStatusOf(null, true, now).status).toBe('STORED_REFERENCE')
    expect(priceStatusOf(null, false, now).status).toBe('NOT_COLLECTED')
    // No quote time on the payload means the row cannot call the price current.
    const [row] = mlbSlate(dashboard([mlbGame()]))
    expect(row.priceStatus).toBe('STORED_REFERENCE')
    expect(row.priceObservedAt).toBeNull()
  })

  it('carries no tie for baseball', () => {
    const [row] = mlbSlate(dashboard([mlbGame()]))
    expect(row.tie).toBeNull()
    expect(row.scoreUnit).toBe('runs')
  })
})

describe('nflSlate', () => {
  it('keeps NFL probabilities as the fractions they arrive as', () => {
    const [row] = nflSlate([nflEvent(nflEngine())])
    expect(row.homeWin).toBeCloseTo(0.5736, 6)
    expect(row.tie).toBeCloseTo(0.0298, 6)
    expect(row.scoreUnit).toBe('points')
  })

  it('refuses a market comparison when the quote is not comparable', () => {
    // A quote that exists is not a quote that can be compared. Without
    // comparison_available there is no no-vig probability, and half-deriving
    // one would put an invented number in an edge column.
    const engine = nflEngine({
      market_comparison: {
        available: true, reason: 'Quote timestamp is missing', markets: [{
          market_type: 'MONEYLINE', book: 'DraftKings', line: null, fresh: false,
          comparison_available: false, reason: 'stale',
          quote: {
            a: { market_type: 'MONEYLINE', book: 'DraftKings', book_key: 'dk', side: 'HOME', outcome_key: 'HOME', point: null, american: -185, decimal: 1.54, observed_at: '', snapshot_id: '' },
            b: { market_type: 'MONEYLINE', book: 'DraftKings', book_key: 'dk', side: 'AWAY', outcome_key: 'AWAY', point: null, american: 154, decimal: 2.54, observed_at: '', snapshot_id: '' },
          },
          market_no_vig: { a: 0.63, b: 0.37 },
        }],
      },
    } as Partial<NflEngine>)
    const [row] = nflSlate([nflEvent(engine)])
    expect(row.marketHome).toBeNull()
    expect(row.edgePoints).toBeNull()
    // The stored quote is retained as evidence, but it is never a current price.
    expect(row.bestHomePrice).toBe(-185)
    expect(row.priceStatus).toBe('STORED_REFERENCE')
  })

  it('reports no sportsbook price when no quote was persisted', () => {
    const [row] = nflSlate([nflEvent(nflEngine())])
    expect(row.priceStatus).toBe('NOT_COLLECTED')
    expect(row.bestHomePrice).toBeNull()
  })

  it('never reports a decision for a research-shadow league', () => {
    const [row] = nflSlate([nflEvent(nflEngine())])
    expect(row.researchOnly).toBe(true)
    expect(row.decisionState).toBeNull()
  })

  it('conditions the home probability on no tie before comparing it to a two-way market', () => {
    const engine = nflEngine()
    const conditional = conditionalHomeWin(engine)!
    expect(conditional).toBeCloseTo(0.5736 / (0.5736 + 0.3966), 6)
    // The raw figure and the conditional figure are different numbers; using
    // the raw one against a two-way no-vig market understates the model.
    expect(conditional).toBeGreaterThan(engine.home_win_probability)
  })
})

describe('isUpcoming', () => {
  it('treats a final game as finished and a started game as still on the board', () => {
    const [scheduled] = mlbSlate(dashboard([mlbGame()]))
    expect(isUpcoming(scheduled)).toBe(true)
    expect(isUpcoming({ ...scheduled, status: 'FINAL' })).toBe(false)
    expect(isUpcoming({ ...scheduled, status: 'STARTED' })).toBe(true)
  })
})
