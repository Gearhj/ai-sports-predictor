import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import historicalStyles from '../styles.css?inline'
import type { HistoricalGameExplorerPage, HistoricalPopulationSummary, HistoricalResearchData } from '../types/domain'
import { HistoricalResearchPage } from './HistoricalResearchPage'

function response(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response
}

function population(populationName: 'TIER_A_STRICT' | 'TIER_B_ENHANCED'): HistoricalPopulationSummary {
  const strict = populationName === 'TIER_A_STRICT'
  return {
    population: populationName,
    role: strict ? 'PRIMARY_STRICT' : 'RESEARCH_ONLY_SHADOW',
    source_mode: 'LIVE',
    quality_tiers: [strict ? 'A' : 'B'],
    row_count: strict ? 2428 : 2430,
    game_count: strict ? 2428 : 2430,
    excluded_row_count: 0,
    event_start_date: '2025-03-18',
    event_end_date: '2025-09-28',
    schema_version: 'mlb_historical_pregame_a2_v2',
    schema_hash: 'e'.repeat(64),
    feature_version: strict ? 'mlb_historical_a2_tier_a_strict/a2-tier-a-strict-v6' : 'mlb_historical_a2_tier_b_enhanced/a2-tier-b-enhanced-v6',
    raw_value_count: 29,
    ordered_output_count: 58,
    mean_quality_score: 85.4,
    manifest_version: strict ? 'strict-public' : 'enhanced-public',
    manifest_identity_checksum: 'c'.repeat(64),
    rows_checksum: 'a'.repeat(64),
    built_at: '2026-08-14T12:00:00Z',
    integrity_status: 'PASS',
    integrity_issues: [],
    required_family_coverage: [
      { key: 'team_strength', label: 'Team strength', coverage: 0.997, required: 0.7, passed: true },
      { key: 'starting_pitcher', label: 'Starting pitcher', coverage: 0, required: 0.7, passed: false },
    ],
    raw_family_coverage: [],
    readiness: {
      status: 'NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT',
      policy_version: 'gate3a_a2_audit_only_readiness_v1',
      row_count: strict ? 2428 : 2430,
      distinct_game_count: strict ? 2428 : 2430,
      distinct_event_dates: 184,
      development_date_groups: 147,
      holdout_date_groups: 37,
      holdout_rows: 505,
      rolling_origin_folds: 16,
      failed_checks: ['required_feature_family_coverage'],
      model_training_invoked: false,
      model_fitting_invoked: false,
      holdout_metrics_inspected: false,
      holdout_outcomes_inspected: false,
    },
  }
}

function history(mode: 'live' | 'empty' = 'live'): HistoricalResearchData {
  const populated = mode === 'live'
  return {
    data_mode: mode,
    availability: populated ? 'AVAILABLE' : 'UNAVAILABLE',
    temporal_scope: 'HISTORICAL',
    temporal_semantics: 'RETROSPECTIVE_RECONSTRUCTION',
    stage: 'GATE_3A_A2',
    season: 2025,
    cutoff_policy_version: populated ? 'mlb_t_minus_6h_v1' : null,
    built_at: populated ? '2026-08-14T12:01:00Z' : null,
    verdict: populated ? 'NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT' : 'UNAVAILABLE',
    historical_market_status: 'UNAVAILABLE',
    model_training_invoked: populated ? false : null,
    model_fitting_invoked: populated ? false : null,
    holdout_metrics_inspected: populated ? false : null,
    promotion_invoked: null,
    strict_population: populated ? population('TIER_A_STRICT') : null,
    enhanced_population: populated ? population('TIER_B_ENHANCED') : null,
    canonical_summary: populated ? {
      availability: 'AVAILABLE',
      integrity_status: 'PASS',
      official_games: 2430,
      team_game_rows: 4860,
      player_game_rows: 127872,
      strict_feature_coverage: 0.854483,
      leakage_status: 'PASS',
      statcast_status: 'UNAVAILABLE',
      historical_market_status: 'UNAVAILABLE',
      source_keys: ['MLB_STATS_API'],
      integrity_issues: [],
    } : {
      availability: 'UNAVAILABLE',
      integrity_status: 'UNAVAILABLE',
      official_games: null,
      team_game_rows: null,
      player_game_rows: null,
      strict_feature_coverage: null,
      leakage_status: 'UNAVAILABLE',
      statcast_status: 'UNAVAILABLE',
      historical_market_status: 'UNAVAILABLE',
      source_keys: [],
      integrity_issues: ['Canonical facts are unavailable.'],
    },
    limitations: [
      'Historical market and closing-line evidence is unavailable and was not imputed.',
      'This A2 manifest summary publishes no model fit, backtest metric, promotion decision, or live forecast. Model evaluation is reported separately on Performance when available.',
      'The Tier B enhanced population is a research-only shadow and is not evaluation evidence.',
    ],
  }
}

function explorer(): HistoricalGameExplorerPage {
  return {
    data_mode: 'live',
    availability: 'AVAILABLE',
    status_reason: 'Governed pregame feature summaries are shown.',
    season: 2025,
    page: 1,
    page_size: 25,
    total_pages: 1,
    total_rows: 1,
    team: null,
    month: null,
    tier: 'A',
    source: 'MLB_STATS_API',
    rows: [{
      game_id: 'game-1',
      event_date: '2025-04-05',
      scheduled_at: '2025-04-05T23:10:00Z',
      matchup: 'BOS at NYY',
      cutoff_at: '2025-04-05T17:10:00Z',
      cutoff_policy_version: 'mlb_t_minus_6h_v1',
      quality_tier: 'A',
      source_mode: 'LIVE',
      source_keys: ['MLB_STATS_API'],
      feature_completeness: 0.854483,
      admission_quality_score: 85.4483,
      present_feature_count: 232,
      total_feature_count: 272,
      starter_state: 'MISSING',
      lineup_state: 'MISSING',
      home_team_prior: { team_id: 'home', abbreviation: 'NYY', name: 'New York', elo_rating: 1512, runs_per_game: 4.7, runs_allowed_per_game: 4.1, offense_true_talent: 4.6, run_prevention_true_talent: 4.2, reliability: 0.8 },
      away_team_prior: { team_id: 'away', abbreviation: 'BOS', name: 'Boston', elo_rating: 1498, runs_per_game: 4.5, runs_allowed_per_game: 4.4, offense_true_talent: 4.5, run_prevention_true_talent: 4.4, reliability: 0.79 },
      run_strength: { home_offense: 4.6, home_run_prevention: 4.2, away_offense: 4.5, away_run_prevention: 4.4, expected_home_win_probability: 0.55 },
      market_available: null,
      market_status: 'UNAVAILABLE',
      target_excluded: true,
    }],
  }
}

describe('HistoricalResearchPage', () => {
  beforeEach(() => window.sessionStorage.clear())

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('separates strict and enhanced retrospective evidence without performance claims', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: string | URL | Request) => Promise.resolve(response(String(input).includes('/research/history/games') ? explorer() : history()))))
    render(<HistoricalResearchPage />)

    expect(await screen.findByText('GOVERNED HISTORICAL EVIDENCE - NOT LIVE FORECASTS')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Historical MLB Research Warehouse' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Tier A strict' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Tier B enhanced' })).toBeInTheDocument()
    expect(screen.getByText('RESEARCH-ONLY SHADOW')).toBeInTheDocument()
    expect(screen.getAllByText('2,428').length).toBeGreaterThan(0)
    expect(screen.getByText('2,430')).toBeInTheDocument()
    expect(screen.getByText('4,860')).toBeInTheDocument()
    expect(screen.getByText('127,872')).toBeInTheDocument()
    expect(screen.getAllByText('85.4%').length).toBeGreaterThan(0)
    expect(screen.getByText('Starting pitcher')).toBeInTheDocument()
    expect(screen.getByText('Below threshold')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByRole('table')).toHaveLength(2))
    expect(screen.getByRole('region', { name: 'Historical game explorer results' })).toHaveAttribute('tabindex', '0')
    expect(screen.getByText('BOS at NYY')).toBeInTheDocument()
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    expect(screen.getByText(/This A2 manifest audit publishes no model metrics/i)).toBeInTheDocument()
    expect(screen.queryByText(/^LIVE DATA$/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Brier score/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/home score/i)).not.toBeInTheDocument()
  })

  it('keeps population cards and wide evidence tables responsive', () => {
    expect(historicalStyles).toMatch(/@media \(max-width: 1000px\)[\s\S]*?\.history-population-grid \{ grid-template-columns: 1fr; \}/)
    expect(historicalStyles).toMatch(/@media \(max-width: 470px\)[\s\S]*?\.historical-research-page \.metric-grid--six/)
    expect(historicalStyles).toContain('.history-coverage-table { min-width: 610px; }')
    expect(historicalStyles).toContain('.history-explorer-table { min-width: 1500px; }')
  })

  it('reports an empty warehouse without inventing results', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(history('empty'))))
    render(<HistoricalResearchPage />)

    expect(await screen.findByRole('heading', { name: 'No governed historical warehouse is published' })).toBeInTheDocument()
    expect(screen.getByText(/no historical results have been inferred/i)).toBeInTheDocument()
    expect(screen.queryByText('2,428')).not.toBeInTheDocument()
  })

  it('fails closed when the historical summary API is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    render(<HistoricalResearchPage />)

    expect(await screen.findByText('HISTORICAL EVIDENCE UNAVAILABLE')).toBeInTheDocument()
    expect(screen.getByText(/no evidence was substituted/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
