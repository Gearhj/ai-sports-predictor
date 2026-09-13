/**
 * The published formal assessment, shown under the comparison it belongs to.
 *
 * The numbers are attractive and the sample is two games, so the tests here are
 * mostly about what must appear beside them:
 *
 *  - that one realized game is not evidence of recurring improvement;
 *  - that nothing is promoted;
 *  - that the score-limited arm's 4,722 draws are not silently compared against
 *    5,000 as if the denominators matched;
 *  - that the published binary Brier is displayed as published, never halved
 *    into a better-looking number to match AISP's summed convention.
 *
 * And one structural guarantee: an assessment bound to a different governed
 * final revision is flagged, not quietly rendered beside AISP's own final.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { EnsembleCandidatePanel } from './EnsembleCandidatePanel'
import type {
  CandidateComparison, CandidateSide, FormalAssessmentBlock, FormalAssessmentSide,
} from '../types/shadow'

const CONCLUSIONS = [
  'Survival and transition produced mixed results on COL-DET.',
  'Score limits improved the reported scores on CIN-MIL, while leaving the '
  + 'winning selection unchanged.',
  'These individual games establish no recurring improvement.',
  'Pitcher-allocation assessment remains unavailable.',
  'No research configuration is promoted.',
]

/** The real CIN-MIL score-limited arm: every metric favours it, on one game. */
const SIDE: FormalAssessmentSide = {
  schema_version: 'AISP_MLB_FORMAL_ASSESSMENT_V1',
  assessment_schema_version: 'MLB_EXISTING_PROSPECTIVE_FORMAL_CLOSEOUT_V1',
  index_sha256: '67ea56a6620b13d80dd2020973993fe81abd0258faa9f1f67a502cb4d05181d1',
  game: 'CIN–MIL',
  source_game_id: '823734',
  arm: 'SCORE_LIMITED',
  forecast_id: 'mlb-v18-score-limited-8ac962b9',
  is_baseline: false,
  baseline_forecast_id: 'mlb-v18-daily-05f86403',
  correct_pick: true,
  winner_pick: 'AWAY',
  actual_away_home: [4, 3],
  draw_count: 4722,
  baseline_draw_count: 5000,
  equal_denominator: false,
  home_win_probability: 0.4938585345192715,
  brier_convention: 'BINARY_HOME_WIN_(pHome-yHome)^2',
  brier_note:
    "This Brier is binary, (pHome - yHome)^2. AISP's own research scorer stores "
    + 'the summed two-class form, which is exactly twice this value. Neither '
    + 'number is converted into the other.',
  metrics: [
    {
      key: 'winner_log_loss', label: 'Log loss, winner',
      value: 0.6809390727199017, baseline_value: 0.6855759150636271,
      delta: -0.004636842343725411, favours: 'CANDIDATE',
    },
    {
      key: 'winner_brier', label: 'Binary Brier, winner',
      value: 0.24389625211752244, baseline_value: 0.24621443999999998,
      delta: -0.002318187882477535, favours: 'CANDIDATE',
    },
    {
      key: 'joint_energy', label: 'Joint energy score',
      value: 1.0007158611727625, baseline_value: 1.0588695223119893,
      delta: -0.05815366113922682, favours: 'CANDIDATE',
    },
  ],
  final_identity: {
    canonical_game_id: '3c7a6820-7c95-4e91-87fa-6acb89c013d7',
    source_outcome_id: '105dc37c-8fbe-4dbf-abf4-a81bfddb2194',
    source_revision: 1,
    status: 'FINAL',
    away_score: 4,
    home_score: 3,
    final_at_utc: '2026-09-13T21:40:00Z',
    lineage_basis: 'LATEST_REVISIONED_GAME_FINAL_OUTCOME',
  },
  decision:
    'Realized winner and run-distribution scores improve on this game; same '
    + 'correct CIN pick. One observation cannot establish recurring improvement; '
    + 'do not advance.',
  conclusions: CONCLUSIONS,
  score_limited: {
    original_draw_count: 5000, retained_draw_count: 4722,
    excluded_draw_count: 278, exclusion_rate: 0.0556, picks_changed: 0,
  },
  pitcher_allocation: {
    status: 'UNAVAILABLE',
    reason: 'Original baseline starter/relief counters and final-bound actual '
      + 'pitcher evidence unavailable.',
  },
  promoted: false,
  authority_note:
    'Research evidence only. This assessment activates no model, changes no '
    + 'policy, promotes no configuration and carries no wagering authority.',
  sample_note:
    'One game, one arm. A difference on a single realized result is not evidence '
    + 'of recurring improvement and says nothing about profitability at an '
    + 'offered price.',
}

const BLOCK: FormalAssessmentBlock = {
  available: true,
  package: {
    index_sha256: SIDE.index_sha256,
    index_bytes: 5128,
    files_verified: ['formal-assessment.json', 'RESULTS.md', 'governed-finals.json'],
    created_at_utc: '2026-09-13T22:11:36.879721+00:00',
    artifact_kind: 'ADDITIVE_FORMAL_ASSESSMENT_NOT_FORECAST',
    governed_study_games_scored: 2,
    research_interventions: 3,
    original_baseline_draws_per_game: 5000,
    score_limited_retained_draws: 4722,
    previous_qualified_package_sha256:
      'dc279581869935ce817fda246c3920001faf20114046b160c717944a74a8fb4b',
    qualification_resolved:
      'Only missing governed September 13 revisions; earlier qualified consumer '
      + 'observations remain unchanged.',
    decisions: null,
    conclusions: CONCLUSIONS,
    pitcher_allocation: SIDE.pitcher_allocation,
    promoted: false,
  },
  final_identity_agreement: {
    status: 'SAME_GOVERNED_FINAL',
    detail: 'The assessment scored the governed final revision AISP holds.',
    assessment_outcome_id: '105dc37c-8fbe-4dbf-abf4-a81bfddb2194',
    assessment_revision: 1,
    aisp_outcome_id: '105dc37c-8fbe-4dbf-abf4-a81bfddb2194',
    aisp_revision: 1,
  },
  brier_conventions: {
    published: 'BINARY_HOME_WIN_(pHome-yHome)^2',
    aisp_research_scorer: 'AISP_RESEARCH_SCORER_SUMMED_TWO_CLASS',
    relationship:
      "AISP's stored Brier is the summed two-class form and is exactly twice the "
      + 'published binary Brier. Both are shown as published; neither is halved '
      + 'or doubled to match the other.',
  },
  conclusions: CONCLUSIONS,
  promoted: false,
}

const SIDE_BASE: CandidateSide = {
  track: 'RESEARCH_SHADOW', label: 'V18 whole-game baseline (frozen)',
  engine_id: 'MLB_V18_WHOLE_GAME', engine_version: 'mlb-v18-whole-game-a6e7e991',
  forecast_id: 'mlb-v18-daily-05f86403', model_artifact_sha256: 'a'.repeat(64),
  artifact_type: 'PROSPECTIVE', is_replay: false,
  home_win_probability: 49.62, away_win_probability: 50.38,
  expected_home_runs: 3.85, expected_away_runs: 4.11,
  evidence_cutoff_utc: '2026-09-13T17:00:00Z',
  published_utc: '2026-09-13T17:20:00Z', imported_utc: '2026-09-13T17:30:00Z',
  scheduled_start_utc: '2026-09-13T22:40:00Z', simulation_count: 5000,
  wagering_authority: false, execution_allowed_in_aisp: false, score: null,
}

function comparison(block: FormalAssessmentBlock | null = BLOCK): CandidateComparison {
  return {
    schema_version: 'AISP_MLB_ENSEMBLE_CANDIDATE_COMPARISON_V1',
    game_id: '3c7a6820-7c95-4e91-87fa-6acb89c013d7',
    artifact_type: 'PROSPECTIVE', is_replay: false, prospective_eligible: true,
    baseline: SIDE_BASE,
    candidate: {
      ...SIDE_BASE,
      track: 'RESEARCH_CANDIDATE', label: 'Score-limited research',
      engine_id: 'MLB_ENSEMBLE_CANDIDATE', engine_version: 'mlb-v18-score-limited-v1',
      forecast_id: 'mlb-v18-score-limited-8ac962b9', simulation_count: 4722,
      formal_assessment: SIDE,
    },
    difference: null,
    components: [],
    component_attribution: null,
    component_attribution_note: null,
    formal_assessment: block,
    candidate_identity: {
      engine_id: 'MLB_ENSEMBLE_CANDIDATE', engine_version: 'mlb-v18-score-limited-v1',
      model_artifact_sha256: 'b'.repeat(64), handoff_sha256: 'f'.repeat(64),
      object_sha256: '0'.repeat(64), baseline_forecast_id: 'mlb-v18-daily-05f86403',
    },
    authority: {
      candidate_wagering_authority: false,
      candidate_execution_allowed_in_aisp: false,
      candidate_activates_model: false,
      controlling_engine_for_official_decisions: 'AISP_BASELINE',
      controlling_engine_source: 'no ACTIVE champion authority',
    },
    note: 'This candidate forecast was published before first pitch.',
  } as unknown as CandidateComparison
}

describe('the published formal assessment', () => {
  afterEach(cleanup)

  it('names the governed outcome and revision it was scored against', () => {
    render(<EnsembleCandidatePanel comparison={comparison()} homeTeam="MIL" />)
    const panel = screen.getByTestId('formal-assessment').textContent ?? ''
    expect(panel).toMatch(/105dc37c-8fbe-4dbf-abf4-a81bfddb2194/)
    expect(panel).toMatch(/revision 1/)
    expect(panel).toMatch(/4–3 final/)
  })

  it('shows the published binary Brier unchanged, and says so', () => {
    render(<EnsembleCandidatePanel comparison={comparison()} homeTeam="MIL" />)
    const panel = within(screen.getByTestId('formal-assessment'))
    // 0.243896, as published. Halving it would read 0.121948.
    expect(panel.getByText('0.243896')).toBeInTheDocument()
    expect(panel.queryByText('0.121948')).toBeNull()
    expect(screen.getByTestId('formal-brier-note').textContent)
      .toMatch(/neither is halved or doubled/)
  })

  it('states the unequal denominators rather than pairing them as equal', () => {
    render(<EnsembleCandidatePanel comparison={comparison()} homeTeam="MIL" />)
    const note = screen.getByTestId('formal-denominator').textContent ?? ''
    expect(note).toMatch(/4,722/)
    expect(note).toMatch(/5,000/)
    expect(note).toMatch(/278 excluded/)
    expect(note).toMatch(/no\s+equal-N pairing was invented/)
    expect(note).toMatch(/0 picks changed/)
  })

  it('carries the sample limit beside a clean sweep of favourable metrics', () => {
    render(<EnsembleCandidatePanel comparison={comparison()} homeTeam="MIL" />)
    const panel = screen.getByTestId('formal-assessment').textContent ?? ''
    expect(panel).toMatch(/not evidence of recurring improvement/)
    expect(panel).toMatch(/establish no recurring improvement/)
    expect(panel).toMatch(/No research configuration is promoted/)
    expect(panel).toMatch(/do not advance/)
    // Nothing may read as a promotion or an authority grant.
    expect(panel).toMatch(/no wagering authority/)
    expect(panel).not.toMatch(/promoted to|advanced to production|now the controlling/)
  })

  it('reports pitcher allocation as unavailable rather than omitting it', () => {
    render(<EnsembleCandidatePanel comparison={comparison()} homeTeam="MIL" />)
    expect(screen.getByTestId('formal-pitcher').textContent)
      .toMatch(/Pitcher allocation: unavailable/)
  })

  it('warns when the assessment scored a different final revision', () => {
    const divergent: FormalAssessmentBlock = {
      ...BLOCK,
      final_identity_agreement: {
        status: 'DIFFERENT_GOVERNED_FINAL',
        detail: 'The assessment scored a different final revision from the one '
          + 'AISP holds; its scores belong to that revision, not this one.',
        assessment_revision: 1,
        aisp_revision: 2,
      },
    }
    render(<EnsembleCandidatePanel comparison={comparison(divergent)} homeTeam="MIL" />)
    const agreement = screen.getByTestId('formal-identity-agreement')
    expect(agreement.getAttribute('role')).toBe('note')
    expect(agreement.textContent).toMatch(/different final revision/)
  })

  it('renders nothing at all when no package is attached', () => {
    render(<EnsembleCandidatePanel comparison={comparison(null)} homeTeam="MIL" />)
    expect(screen.queryByTestId('formal-assessment')).toBeNull()
  })
})
