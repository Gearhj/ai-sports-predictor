import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { demoPerformance } from '../data/demo'
import type { OfflineEvaluation, PerformanceData } from '../types/domain'
import { PerformancePage } from './PerformancePage'

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-placeholder" />,
}))

function jsonResponse(payload: PerformanceData) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  } as Response
}

function livePerformance(offlineEvaluation?: OfflineEvaluation | null): PerformanceData {
  return {
    ...demoPerformance,
    data_mode: 'live',
    offline_evaluation: offlineEvaluation,
  }
}

describe('PerformancePage offline chronological evaluation', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('preserves the legacy API contract and labels missing offline evidence as not run', async () => {
    const { offline_evaluation: _offlineEvaluation, ...legacyPayload } = livePerformance()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(legacyPayload)))

    render(<PerformancePage />)

    expect(await screen.findByRole('heading', { name: 'Offline chronological evaluation' })).toBeInTheDocument()
    expect(screen.getAllByText('NOT RUN').length).toBeGreaterThan(0)
    expect(screen.getByText(/No governed offline evaluation artifact has been published/i)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Published forecast record' })).toBeInTheDocument()
  })

  it('publishes a collecting state instead of zero ROI, yield, or CLV before settlement', async () => {
    const payload = livePerformance()
    payload.evaluated_games = 0
    payload.evaluation_available = false
    payload.brier_score = null
    payload.log_loss = null
    payload.accuracy = null
    payload.run_mae = null
    payload.total_mae = null
    payload.versus_market_brier = null
    payload.calibration = []
    payload.rolling = []
    payload.model_components = []
    payload.by_confidence = []
    payload.betting = {
      starting_bankroll: 1000, current_bankroll: 1000, net_profit_loss: 0, total_risked: 0,
      roi: null, yield_pct: null, positions: 0, settled: 0, open: 0,
      wins: 0, losses: 0, pushes: 0, voids: 0, record: '0–0–0',
      max_drawdown: 0, longest_losing_streak: 0, closing_line_value: null,
      beat_close_percentage: null, has_settled: false, breakdowns: {},
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)))

    render(<PerformancePage />)

    expect(await screen.findByText('COLLECTING DATA')).toBeInTheDocument()
    expect(screen.getByText('COLLECTING SETTLEMENT EVIDENCE')).toBeInTheDocument()
    for (const label of ['ROI', 'Yield', 'Closing line value']) {
      const card = screen.getByText(label).closest('article') as HTMLElement
      expect(within(card).getByText('—')).toBeInTheDocument()
      expect(card).not.toHaveTextContent('0.00%')
    }
    expect(screen.getByText('No legitimate closing sample')).toBeInTheDocument()
  })

  it('fails closed when the historical sample is insufficient', async () => {
    const evaluation: OfflineEvaluation = {
      status: 'INSUFFICIENT_DATA',
      status_reason: 'Only nine eligible games were available; the policy requires at least 1,000.',
      model_version: 'asp-gate3-candidate-v1',
      manifest_version: 'controlled-run-2026-08-13',
      feature_version: 'mlb-pit-v1',
      evaluation_policy_version: 'gate3-v1',
      rolling_origin_folds: 0,
      train_window: null,
      validation_window: null,
      holdout_window: null,
      validation_metrics: null,
      holdout_metrics: null,
      baselines: [],
      promotion: {
        decision: 'BLOCKED',
        reason: 'Minimum sample and temporal coverage gates were not met.',
        criteria_version: 'promotion-v1',
      },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(livePerformance(evaluation))))

    render(<PerformancePage />)

    expect((await screen.findAllByText('INSUFFICIENT DATA')).length).toBeGreaterThan(0)
    expect(screen.getByText(/Only nine eligible games were available/i)).toBeInTheDocument()
    expect(screen.getByText('controlled-run-2026-08-13')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'BLOCKED' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Evaluation results' })).not.toBeInTheDocument()
  })

  it('shows historical dataset readiness separately from model evaluation', async () => {
    const payload = livePerformance({
      status: 'NOT_RUN',
      status_reason: 'No model evaluation was run.',
      model_version: null,
      manifest_version: null,
      feature_version: null,
      rolling_origin_folds: 0,
      train_window: null,
      validation_window: null,
      holdout_window: null,
      validation_metrics: null,
      holdout_metrics: null,
      baselines: [],
      promotion: { decision: 'BLOCKED', reason: 'Dataset readiness checks failed.' },
    })
    payload.historical_dataset = {
      availability: 'AVAILABLE',
      stage: 'GATE_3A_A2',
      season: 2025,
      population: 'TIER_A_STRICT',
      row_count: 2428,
      distinct_event_dates: 184,
      holdout_rows: 505,
      rolling_origin_folds: 16,
      readiness_status: 'NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT',
      failed_checks: ['required_feature_family_coverage'],
      status_reason: 'The strict historical dataset is not ready. No model evaluation was run.',
      model_training_invoked: false,
      model_fitting_invoked: false,
      holdout_metrics_inspected: false,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)))

    render(<PerformancePage />)

    expect(await screen.findByRole('heading', { name: 'Historical dataset readiness' })).toBeInTheDocument()
    expect(screen.getByText('DATASET EVIDENCE - NOT MODEL PERFORMANCE')).toBeInTheDocument()
    expect(screen.getByText('2,428')).toBeInTheDocument()
    expect(screen.getByText(/A2 dataset-readiness panel publishes no model metrics/i)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Offline chronological evaluation' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'BLOCKED' })).toBeInTheDocument()
  })

  it('renders provenance, temporal splits, metrics, baselines, and the promotion decision', async () => {
    const evaluation = demoPerformance.offline_evaluation
    if (!evaluation) throw new Error('Demo evaluation fixture is required')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(livePerformance(evaluation))))

    render(<PerformancePage />)

    expect(await screen.findByText('example-manifest-v1')).toBeInTheDocument()
    expect(screen.getByText('example-features-v1')).toBeInTheDocument()
    expect(screen.getByLabelText(`Dataset checksum: ${'a'.repeat(64)}`)).toHaveTextContent('aaaaaaaaaaaa...aaaaaaaa')
    expect(screen.getByLabelText(`Feature-set checksum: ${'b'.repeat(64)}`)).toHaveTextContent('bbbbbbbbbbbb...bbbbbbbb')
    expect(screen.getAllByText('RESEARCH CHALLENGER').length).toBeGreaterThan(0)
    expect(screen.getByText(/separate from production promotion/i)).toBeInTheDocument()
    expect(screen.getByText('3 folds')).toBeInTheDocument()
    expect(screen.getByText('2022-04-07 to 2023-09-30')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Evaluation results' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Baselines' })).toBeInTheDocument()
    expect(screen.getByText('Market consensus example')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'PENDING' })).toBeInTheDocument()
    expect(screen.getByText(/demo results can never promote a model/i)).toBeInTheDocument()
  })
})
