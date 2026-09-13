import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { EnsembleCandidatePanel } from './EnsembleCandidatePanel'
import type {
  CandidateCohort, CandidateComparison, CandidateConfiguration, CandidateScore,
  CandidateSide,
} from '../types/shadow'

const BASELINE: CandidateSide = {
  track: 'RESEARCH_SHADOW',
  label: 'V18 whole-game baseline (frozen)',
  engine_id: 'MLB_V18_WHOLE_GAME',
  engine_version: 'mlb-v18-whole-game-a6e7e991',
  forecast_id: 'mlb-v18-daily-abc',
  model_artifact_sha256: '44ec4f457741f1aaeff42b81af1be5fd2712e494709ff1a0a8f0742b88d71c8b',
  artifact_type: 'PROSPECTIVE',
  is_replay: false,
  home_win_probability: 55.12,
  away_win_probability: 44.88,
  expected_home_runs: 4.71,
  expected_away_runs: 4.18,
  evidence_cutoff_utc: '2026-09-12T20:02:00Z',
  published_utc: '2026-09-12T20:10:00Z',
  imported_utc: '2026-09-12T20:21:00Z',
  scheduled_start_utc: '2026-09-12T23:10:00Z',
  simulation_count: 5000,
  wagering_authority: false,
  execution_allowed_in_aisp: false,
  score: null,
}

const CANDIDATE: CandidateSide = {
  ...BASELINE,
  track: 'RESEARCH_CANDIDATE',
  label: 'Ensemble release candidate',
  engine_id: 'MLB_ENSEMBLE_CANDIDATE',
  engine_version: 'mlb-ensemble-rc1-2026091201',
  forecast_id: 'mlb-ensemble-prospective-825101',
  model_artifact_sha256: 'b'.repeat(64),
  home_win_probability: 58.12,
  away_win_probability: 41.88,
  expected_home_runs: 4.91,
  expected_away_runs: 4.02,
  score: null,
}

function comparison(overrides: Partial<CandidateComparison> = {}): CandidateComparison {
  return {
    schema_version: 'AISP_MLB_ENSEMBLE_CANDIDATE_COMPARISON_V1',
    game_id: 'game-1',
    artifact_type: 'PROSPECTIVE',
    is_replay: false,
    prospective_eligible: true,
    baseline: BASELINE,
    candidate: CANDIDATE,
    difference: {
      home_win_probability_points: 3.0,
      expected_home_runs: 0.2,
      expected_away_runs: -0.16,
      convention: 'candidate minus baseline',
    },
    components: [
      { component_id: 'V18_WHOLE_GAME', role: 'BASE', status: 'FROZEN', sha256: 'a'.repeat(64), ran: true, fallback_reason: null, detail: null },
      { component_id: 'CALIBRATION_ISOTONIC_V3', role: 'CALIBRATION', status: 'TESTED', sha256: 'c'.repeat(64), ran: true, fallback_reason: null, detail: null },
      { component_id: 'STARTER_DURATION_ADAPTER', role: 'DURATION', status: 'SUPPORTED', sha256: 'd'.repeat(64), ran: false, fallback_reason: 'No fitted starter-duration curve for either probable starter', detail: null },
    ],
    component_attribution: null,
    component_attribution_note:
      'Model Studio supplied no per-component attribution, so the difference is shown as a total only.',
    candidate_identity: {
      engine_id: 'MLB_ENSEMBLE_CANDIDATE',
      engine_version: 'mlb-ensemble-rc1-2026091201',
      model_artifact_sha256: 'b'.repeat(64),
      handoff_sha256: 'f'.repeat(64),
      object_sha256: '0'.repeat(64),
      baseline_forecast_id: 'mlb-v18-daily-abc',
    },
    authority: {
      candidate_wagering_authority: false,
      candidate_execution_allowed_in_aisp: false,
      candidate_activates_model: false,
      controlling_engine_for_official_decisions: 'AISP_BASELINE',
      controlling_engine_source: 'no ACTIVE champion authority; Gen 1 remains the controlling engine',
    },
    note: 'This candidate forecast was published before first pitch.',
    ...overrides,
  }
}

describe('EnsembleCandidatePanel', () => {
  afterEach(cleanup)

  it('shows both sides, their predicted scores and the difference', () => {
    render(<EnsembleCandidatePanel comparison={comparison()} homeTeam="MIA" />)

    expect(screen.getByText('55.1%')).toBeInTheDocument()   // baseline
    expect(screen.getByText('58.1%')).toBeInTheDocument()   // candidate
    expect(screen.getByText('4.18 – 4.71 (away – home)')).toBeInTheDocument()
    expect(screen.getByText('4.02 – 4.91 (away – home)')).toBeInTheDocument()

    const difference = screen.getByTestId('candidate-difference')
    expect(within(difference).getByText('+3.0 pts')).toBeInTheDocument()
    expect(within(difference).getByText('+0.20')).toBeInTheDocument()
    expect(within(difference).getByText('-0.16')).toBeInTheDocument()
  })

  it('names each component and the fallback reason for one that did not run', () => {
    render(<EnsembleCandidatePanel comparison={comparison()} homeTeam="MIA" />)

    const components = screen.getByTestId('candidate-components')
    expect(within(components).getByText('CALIBRATION_ISOTONIC_V3')).toBeInTheDocument()
    expect(within(components).getByText('STARTER_DURATION_ADAPTER')).toBeInTheDocument()
    expect(
      within(components).getByText(/Fallback: No fitted starter-duration curve/i),
    ).toBeInTheDocument()
    expect(within(components).getAllByText('ran')).toHaveLength(2)
    expect(within(components).getByText('did not run')).toBeInTheDocument()
  })

  it('refuses to split the difference into per-component points without an attribution', () => {
    render(<EnsembleCandidatePanel comparison={comparison()} homeTeam="MIA" />)

    expect(
      screen.getByText(/supplied no per-component attribution/i),
    ).toBeInTheDocument()
    // The total is shown once; no component carries a points figure of its own.
    const components = screen.getByTestId('candidate-components')
    expect(within(components).queryByText(/pts/)).toBeNull()
  })

  it('marks a labelled historical replay and says it is excluded from prospective work', () => {
    render(
      <EnsembleCandidatePanel
        homeTeam="MIA"
        comparison={comparison({
          artifact_type: 'HISTORICAL_REPLAY',
          is_replay: true,
          prospective_eligible: false,
          candidate: { ...CANDIDATE, artifact_type: 'HISTORICAL_REPLAY', is_replay: true },
          note: 'This candidate forecast is a labelled historical replay. It is excluded from prospective performance, and it can reach no decision and no position.',
        })}
      />,
    )

    expect(screen.getByTestId('candidate-artifact-type')).toHaveTextContent(/Historical replay/i)
    expect(screen.getByText(/excluded from prospective performance/i)).toBeInTheDocument()
  })

  it('names the controlling model and claims no authority for the candidate', () => {
    render(<EnsembleCandidatePanel comparison={comparison()} homeTeam="MIA" />)

    expect(screen.getByTestId('controlling-engine')).toHaveTextContent('AISP_BASELINE')
    expect(screen.getAllByText('No wagering authority').length).toBeGreaterThan(0)
    expect(screen.getByText('Never')).toBeInTheDocument()
  })

  it('shows the scored result when eligible final evidence exists', () => {
    render(
      <EnsembleCandidatePanel
        homeTeam="MIA"
        comparison={comparison({
          candidate: {
            ...CANDIDATE,
            score: {
              evaluation_id: 'eval-1',
              scoring_version: 'AISP_MLB_V18_PROSPECTIVE_SCORING_V1',
              evaluated_at: '2026-09-13T04:10:00Z',
              home_score: 2, away_score: 6,
              actual_outcome: 'AWAY', predicted_outcome: 'HOME',
              winner_correct: false,
              probability_of_actual: 0.4188,
              log_loss: 0.8702, brier_score: 0.3378,
              margin_error: 4.89, total_error: -0.07,
              crps_margin: 1.9, crps_total: 1.1,
              result_source: 'MLB_STATS_API', result_tier: 2,
              publication_clock: 'VERIFIED_INDEPENDENT_PUBLICATION',
              publication_clock_verified: true,
            },
          },
        })}
      />,
    )

    expect(screen.getByText(/Final 6–2 · missed the winner/)).toBeInTheDocument()
    expect(screen.getByText('0.8702')).toBeInTheDocument()
  })

  it('reports an absent baseline rather than inventing a difference', () => {
    render(
      <EnsembleCandidatePanel
        homeTeam="MIA"
        comparison={comparison({ baseline: null, difference: null })}
      />,
    )

    expect(screen.getByText(/No frozen V18 forecast is registered/i)).toBeInTheDocument()
    expect(screen.queryByTestId('candidate-difference')).toBeNull()
  })
})

// -- the real COL-DET replay's four configurations ---------------------------

const REAL_CONFIGS: CandidateConfiguration[] = [
  {
    key: 'A_FROZEN_V18_BASELINE_HOOK', letter: 'A', title: 'Enabled release default',
    status: 'ENABLED_RELEASE_DEFAULT', note: 'Unchanged frozen V18.',
    enabled_in_release: true, provenance: 'SAVED_REUSED', simulations: 5000,
    home_win_probability: 0.6138, home_win_monte_carlo_standard_error: 0.0068861742556499276,
    expected_home_runs: 5.0294, expected_away_runs: 4.0254, expected_total_runs: 9.0548,
    extra_inning_rate: 0.0928,
    paired_delta_vs_a: {
      home_win_probability: 0, home_win_paired_standard_error: 0,
      expected_home_runs: 0, expected_away_runs: 0, expected_total_runs: 0,
    },
    illegal_state_count: 0, nontermination_count: 0,
  },
  {
    key: 'B_V18_PLUS_CALIBRATION', letter: 'B', title: 'Comparison-only calibration',
    status: 'DISABLED_COMPARISON_ONLY', note: 'Disabled by its own selection gate.',
    enabled_in_release: false, provenance: 'NEW_RETROSPECTIVE', simulations: 5000,
    home_win_probability: 0.6312, home_win_monte_carlo_standard_error: 0.006823974305715475,
    expected_home_runs: 5.215, expected_away_runs: 4.0404, expected_total_runs: 9.2554,
    extra_inning_rate: 0.0898,
    paired_delta_vs_a: {
      home_win_probability: 0.0174, home_win_paired_standard_error: 0.007013507386263412,
      expected_home_runs: 0.1856, expected_away_runs: 0.015, expected_total_runs: 0.2006,
    },
    illegal_state_count: 0, nontermination_count: 0,
  },
  {
    key: 'C_V18_PLUS_DURATION', letter: 'C', title: 'Duration shadow / unassessed',
    status: 'SHADOW_ONLY_UNASSESSED', note: 'Shadow-only and unassessed at game level.',
    enabled_in_release: false, provenance: 'SAVED_REUSED', simulations: 5000,
    home_win_probability: 0.6188, home_win_monte_carlo_standard_error: 0.0055,
    expected_home_runs: 5.041, expected_away_runs: 4.0854, expected_total_runs: 9.1264,
    extra_inning_rate: 0.09,
    paired_delta_vs_a: {
      home_win_probability: 0.005, home_win_paired_standard_error: 0.0055,
      expected_home_runs: 0.0116, expected_away_runs: 0.06, expected_total_runs: 0.0716,
    },
    illegal_state_count: 0, nontermination_count: 0,
  },
  {
    key: 'D_V18_PLUS_CALIBRATION_PLUS_DURATION', letter: 'D',
    title: 'Combined replay / unassessed', status: 'DISABLED_AND_UNASSESSED',
    note: 'Neither component is enabled.',
    enabled_in_release: false, provenance: 'NEW_RETROSPECTIVE', simulations: 5000,
    home_win_probability: 0.6274, home_win_monte_carlo_standard_error: 0.0075,
    expected_home_runs: 5.2228, expected_away_runs: 4.0742, expected_total_runs: 9.297,
    extra_inning_rate: 0.089,
    paired_delta_vs_a: {
      home_win_probability: 0.0136, home_win_paired_standard_error: 0.0075,
      expected_home_runs: 0.1934, expected_away_runs: 0.0488, expected_total_runs: 0.2422,
    },
    illegal_state_count: 0, nontermination_count: 0,
  },
]

describe('EnsembleCandidatePanel — the real COL–DET replay', () => {
  afterEach(cleanup)

  function realReplay(): CandidateComparison {
    return comparison({
      artifact_type: 'HISTORICAL_REPLAY',
      is_replay: true,
      prospective_eligible: false,
      configurations: REAL_CONFIGS,
      default_configuration: 'A_FROZEN_V18_BASELINE_HOOK',
      comparison_status: 'COMPLETED_ONE_GAME_HISTORICAL_REPLAY_TOO_SPARSE_FOR_ORDERING',
      limitations: ['One game cannot establish configuration ordering or superiority'],
      difference: {
        home_win_probability_points: 0,
        expected_home_runs: 0,
        expected_away_runs: 0,
        convention: 'candidate minus baseline',
      },
      candidate: { ...CANDIDATE, artifact_type: 'HISTORICAL_REPLAY', is_replay: true,
        home_win_probability: 61.38, expected_home_runs: 5.0294, expected_away_runs: 4.0254 },
      baseline: { ...BASELINE, home_win_probability: 61.38,
        expected_home_runs: 5.0294, expected_away_runs: 4.0254 },
    })
  }

  it('shows all four configurations with their real status', () => {
    render(<EnsembleCandidatePanel comparison={realReplay()} homeTeam="DET" />)

    const table = screen.getByTestId('candidate-configurations')
    expect(within(table).getByText('Enabled release default')).toBeInTheDocument()
    expect(within(table).getByText('Comparison-only calibration')).toBeInTheDocument()
    expect(within(table).getByText('Duration shadow / unassessed')).toBeInTheDocument()
    expect(within(table).getByText('Combined replay / unassessed')).toBeInTheDocument()
    expect(within(table).getByText('61.38%')).toBeInTheDocument()
    expect(within(table).getByText('63.12%')).toBeInTheDocument()
  })

  it('marks only A as enabled in the release', () => {
    render(<EnsembleCandidatePanel comparison={realReplay()} homeTeam="DET" />)

    expect(screen.getByTestId('candidate-config-A')).toHaveClass('is-enabled')
    for (const letter of ['B', 'C', 'D']) {
      expect(screen.getByTestId(`candidate-config-${letter}`)).toHaveClass('is-not-enabled')
    }
    expect(screen.getAllByText('Enabled in release')).toHaveLength(1)
  })

  it('states that one replay provides no prospective ordering', () => {
    render(<EnsembleCandidatePanel comparison={realReplay()} homeTeam="DET" />)

    const claim = screen.getByTestId('candidate-ordering-claim')
    expect(claim).toHaveTextContent(/ONE historical replay/i)
    expect(claim).toHaveTextContent(/no prospective ordering/i)
    expect(screen.getByText(/One game cannot establish configuration ordering/i)).toBeInTheDocument()
  })

  it('shows the candidate matching the baseline, because the default is unchanged V18', () => {
    render(<EnsembleCandidatePanel comparison={realReplay()} homeTeam="DET" />)

    const difference = screen.getByTestId('candidate-difference')
    expect(within(difference).getByText('0.0 pts')).toBeInTheDocument()
    expect(screen.getByTestId('candidate-artifact-type')).toHaveTextContent(/Historical replay/i)
  })
})

// -- two arms, both inspectable --------------------------------------------

describe('candidate configurations', () => {
  afterEach(cleanup)

  const arm = (key: string, supported: boolean) => ({
    key,
    configuration_id: key,
    configuration_identity_source: 'CANDIDATE_ENGINE_VERSION' as const,
    actual_configuration: supported ? key : 'V18_BASE',
    engine_version: key,
    forecast_id: `f-${key}`,
    contract_version: supported
      ? 'AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2'
      : 'AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1',
    component_trial_supported: supported,
    fell_back_to_baseline_draws: !supported,
    published_utc: '2026-09-12T21:19:47Z',
  })

  it('lists every published arm so one cannot stand in for the other', () => {
    render(
      <EnsembleCandidatePanel
        comparison={{
          ...comparison(),
          configuration: arm('mlb-v18-three-arm-transition-v1', true),
          available_configurations: [
            arm('mlb-v18-three-arm-transition-v1', true),
            arm('mlb-v18-three-arm-duration-v1', false),
          ],
          baseline_binding: 'BOUND_BY_CANDIDATE_BASELINE_FORECAST_ID',
        }}
        homeTeam="Tampa Bay Rays"
      />,
    )
    const bar = screen.getByTestId('candidate-configurations')
    expect(bar).toHaveTextContent('mlb-v18-three-arm-transition-v1')
    expect(bar).toHaveTextContent('mlb-v18-three-arm-duration-v1')
    expect(bar).toHaveTextContent('component ran')
    expect(bar).toHaveTextContent('fell back to baseline draws')
    expect(bar).toHaveTextContent('BOUND_BY_CANDIDATE_BASELINE_FORECAST_ID')
  })

  it('says a fallback arm equals the baseline by construction', () => {
    render(
      <EnsembleCandidatePanel
        comparison={{
          ...comparison(),
          configuration: arm('mlb-v18-three-arm-duration-v1', false),
          available_configurations: [arm('mlb-v18-three-arm-duration-v1', false)],
          fallback_note:
            "This configuration's component did not run for this game, so it reused the "
            + "baseline's own draws. Its numbers equal the baseline's by construction. That "
            + 'is operational coverage, not a measured component effect, and it is not '
            + 'evidence about the component.',
        }}
        homeTeam="Tampa Bay Rays"
      />,
    )
    expect(screen.getByTestId('candidate-configurations'))
      .toHaveTextContent('not a measured component effect')
  })
})

// -- the result, and the cohort it belongs to ------------------------------

describe('scored comparison', () => {
  afterEach(cleanup)

  const scored = (over: Partial<CandidateScore> = {}): CandidateScore => ({
    evaluation_id: 'eval-1',
    scoring_version: 'AISP_MLB_V18_PROSPECTIVE_SCORING_V1',
    evaluated_at: '2026-09-13T03:10:00Z',
    home_score: 6, away_score: 2,
    actual_outcome: 'HOME', predicted_outcome: 'HOME', winner_correct: true,
    probability_of_actual: 0.6312,
    log_loss: 0.4601, brier_score: 0.1359,
    margin_error: 1.2, total_error: 0.8,
    crps_home: null, crps_away: null, crps_margin: null, crps_total: null,
    energy_score: null,
    distribution_available: false,
    distribution_unavailable_reason: 'The forecast retained no simulation draws.',
    distribution_draws: null,
    result_identity: 'MLB_STATS_API:obs-a',
    result_source: 'MLB_STATS_API', result_tier: 1,
    result_observed_utc: '2026-09-13T02:40:00Z',
    publication_clock: 'VERIFIED_INDEPENDENT_PUBLICATION',
    publication_clock_verified: true,
    ...over,
  })

  const difference = {
    convention: 'candidate minus baseline',
    direction:
      'Every metric here is a loss, so a NEGATIVE difference means the candidate '
      + 'lost less than the baseline on this game. A positive difference means it lost more.',
    metrics: [
      { key: 'log_loss', label: 'Log loss', candidate: 0.4601, baseline: 0.4879,
        delta: -0.0278, favours: 'CANDIDATE' as const },
      { key: 'brier_score', label: 'Brier score', candidate: 0.1359, baseline: 0.1322,
        delta: 0.0037, favours: 'BASELINE' as const },
    ],
  }

  const withResult = (over: Partial<CandidateComparison> = {}) => comparison({
    baseline: { ...BASELINE, score: scored({ log_loss: 0.4879, brier_score: 0.1322 }) },
    candidate: { ...CANDIDATE, score: scored() },
    configuration: {
      key: 'mlb-v18-three-arm-transition-v1',
      configuration_id: 'mlb-v18-three-arm-transition-v1',
      configuration_identity_source: 'CANDIDATE_ENGINE_VERSION' as const,
      actual_configuration: 'V18_TRANSITION_ONLY',
      engine_version: 'mlb-v18-three-arm-transition-v1',
      forecast_id: 'cand-1',
      contract_version: 'AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2',
      component_trial_supported: true,
      fell_back_to_baseline_draws: false,
      published_utc: '2026-09-12T21:19:47Z',
    },
    score_difference: difference,
    shared_final: {
      status: 'SHARED' as const,
      candidate_result_identity: 'MLB_STATS_API:obs-a',
      baseline_result_identity: 'MLB_STATS_API:obs-a',
      detail: 'Both arms were scored against the same recorded final.',
    },
    governed_final: {
      revision: 1, outcome_status: 'FINAL', home_score: 6, away_score: 2,
      final_at_utc: '2026-09-13T02:22:30Z', payload_checksum: 'c'.repeat(64),
      source_record_id: '822927',
    },
    ...over,
  })

  it('shows the final, both losses and a difference whose direction is stated', () => {
    render(<EnsembleCandidatePanel comparison={withResult()} homeTeam="Tampa Bay Rays" />)
    const block = screen.getByTestId('candidate-result')
    expect(block).toHaveTextContent('2–6')
    expect(block).toHaveTextContent('revision 1')
    expect(block).toHaveTextContent('0.4879')   // baseline log loss
    expect(block).toHaveTextContent('0.4601')   // candidate log loss
    expect(block).toHaveTextContent('-0.0278')
    expect(block).toHaveTextContent('candidate lost less')
    expect(block).toHaveTextContent('baseline lost less')
    expect(block).toHaveTextContent(/NEGATIVE difference/)
  })

  it('says a missing distribution is unavailable rather than showing a zero', () => {
    render(<EnsembleCandidatePanel comparison={withResult()} homeTeam="Tampa Bay Rays" />)
    expect(screen.getAllByText('unavailable').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/retained no simulation draws/).length).toBeGreaterThan(0)
    // A CRPS row is absent from the difference table, not present as 0.0000.
    const block = screen.getByTestId('candidate-result')
    expect(block).not.toHaveTextContent('CRPS')
  })

  it('warns when the two arms were scored against different finals', () => {
    render(
      <EnsembleCandidatePanel
        comparison={withResult({
          shared_final: {
            status: 'DIVERGENT',
            candidate_result_identity: 'MLB_STATS_API:obs-a',
            baseline_result_identity: 'MLB_STATS_API:obs-b',
            detail: 'The two arms were scored against DIFFERENT recorded finals, so the '
              + 'difference between them is not a like-for-like comparison.',
          },
        })}
        homeTeam="Tampa Bay Rays"
      />,
    )
    expect(screen.getByTestId('candidate-result'))
      .toHaveTextContent(/DIFFERENT recorded finals/)
  })

  const cohort = (over: Partial<CandidateCohort> = {}): CandidateCohort => ({
    schema_version: 'AISP_MLB_ENSEMBLE_CANDIDATE_COHORT_V1',
    configuration: 'mlb-v18-three-arm-transition-v1',
    games: 2,
    metrics: [
      { key: 'log_loss', label: 'Log loss', games: 2, candidate_mean: 0.51,
        baseline_mean: 0.53, delta: -0.02, favours: 'CANDIDATE' },
      { key: 'crps_total', label: 'CRPS, total runs', games: 1, candidate_mean: 2.1,
        baseline_mean: 2.05, delta: 0.05, favours: 'BASELINE' },
    ],
    included: [],
    excluded: [
      { forecast_identity: 'cand-3', source_game_id: '825035',
        reason: 'PENDING_NOT_YET_SCORED', detail: 'No eligible recorded final yet.' },
      { forecast_identity: 'surv-1', source_game_id: '822927',
        reason: 'FALLBACK_NOT_A_COMPONENT_TRIAL',
        detail: "The component did not run, so the numbers are the baseline's own draws." },
    ],
    convention: 'candidate minus baseline',
    claim_limit: 'This is a mean over a handful of observed games. It is not a significance test.',
    ...over,
  })

  it('reports the cohort game count per metric and keeps pending games visible', () => {
    render(
      <EnsembleCandidatePanel
        comparison={withResult()} homeTeam="Tampa Bay Rays" cohort={cohort()}
      />,
    )
    const block = screen.getByTestId('candidate-cohort')
    expect(block).toHaveTextContent('Across 2 scored games')
    // Metrics do not share a denominator: CRPS was computed on one game only,
    // so its row must carry its own count rather than the cohort's.
    const rowFor = (label: RegExp) => within(block).getAllByRole('row')
      .find((row) => label.test(row.textContent ?? ''))
    expect(within(rowFor(/CRPS, total runs/)!).getAllByRole('cell')[0])
      .toHaveTextContent('1')
    expect(within(rowFor(/Log loss/)!).getAllByRole('cell')[0]).toHaveTextContent('2')
    expect(screen.getByTestId('candidate-cohort-pending')).toHaveTextContent('825035')
    expect(block).toHaveTextContent('not a significance test')
  })

  it('names the fallback exclusion rather than hiding it', () => {
    render(
      <EnsembleCandidatePanel
        comparison={withResult()} homeTeam="Tampa Bay Rays" cohort={cohort()}
      />,
    )
    expect(screen.getByTestId('candidate-cohort'))
      .toHaveTextContent('FALLBACK_NOT_A_COMPONENT_TRIAL')
  })

  it('does not show a cohort belonging to a different configuration', () => {
    render(
      <EnsembleCandidatePanel
        comparison={withResult()} homeTeam="Tampa Bay Rays"
        cohort={cohort({ configuration: 'mlb-v18-three-arm-duration-v1' })}
      />,
    )
    expect(screen.queryByTestId('candidate-cohort')).toBeNull()
  })
})
