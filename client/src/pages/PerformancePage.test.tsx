import { cleanup, render, screen } from '@testing-library/react'
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

  it('renders provenance, temporal splits, metrics, baselines, and the promotion decision', async () => {
    const evaluation = demoPerformance.offline_evaluation
    if (!evaluation) throw new Error('Demo evaluation fixture is required')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(livePerformance(evaluation))))

    render(<PerformancePage />)

    expect(await screen.findByText('example-manifest-v1')).toBeInTheDocument()
    expect(screen.getByText('example-features-v1')).toBeInTheDocument()
    expect(screen.getByText('3 folds')).toBeInTheDocument()
    expect(screen.getByText('2022-04-07 to 2023-09-30')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Evaluation results' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Baselines' })).toBeInTheDocument()
    expect(screen.getByText('Market consensus example')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'PENDING' })).toBeInTheDocument()
    expect(screen.getByText(/demo results can never promote a model/i)).toBeInTheDocument()
  })
})
