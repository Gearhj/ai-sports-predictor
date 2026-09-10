/**
 * The prospective results section — the three ways it could lie.
 *
 *  1. Reading like a track record. One game with a correct winner is "100%"
 *     unless n travels with it, so n must be on screen, not in a tooltip.
 *  2. Pooling models. Two engines averaged into one accuracy is a number
 *     about nothing; each engine's board must stay its own.
 *  3. Dressing up what we do not have. An operator-entered final is not
 *     provider-verified, and a game with no persisted quote has no market
 *     comparison — both must say so in words.
 */
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { NflProspectiveSection } from './NflProspectiveResults'
import type {
  NflProspectiveResults, ProspectiveGame, ProspectiveModel,
} from '../../types/nflProspective'

afterEach(cleanup)

/** One scoreboard tile's text, found by its label, so "1 of 1" is unambiguous. */
function stat(container: HTMLElement, label: string): string {
  const tile = [...container.querySelectorAll('.np-stat')]
    .find((node) => node.querySelector('small')?.textContent === label)
  if (!tile) throw new Error(`no scoreboard tile labelled "${label}"`)
  return tile.textContent ?? ''
}

function game(over: Partial<ProspectiveGame> = {}): ProspectiveGame {
  return {
    evaluation_id: 'eval-1',
    event_id: '2026_01_NE_SEA',
    canonical_game_id: 'game-1',
    home: { abbreviation: 'SEA', name: 'Seattle Seahawks' },
    away: { abbreviation: 'NE', name: 'New England Patriots' },
    kickoff_utc: '2026-09-10T00:20:00+00:00',
    final: { home: 13, away: 10 },
    forecast: {
      identity: 'forecast:NFL_M1:CURRENT:9',
      snapshot_type: 'CURRENT', snapshot_version: 9,
      frozen_published_utc: '2026-09-08T22:53:00+00:00',
      evidence_utc: '2026-09-08T12:00:00+00:00',
      imported_utc: '2026-09-09T02:00:00+00:00',
      hours_before_kickoff: 25.45,
      engine_id: 'NFL_M1_XGBOOST', engine_version: 'NFL_M1_2026_RESEARCH_SHADOW_V1',
    },
    result: {
      identity: 'OPERATOR_ENTERED:6e454379', source: 'OPERATOR_ENTERED',
      observed_utc: '2026-09-10T03:30:00+00:00', provider_verified: false,
    },
    outcome: {
      actual: 'HOME', predicted: 'HOME', winner_correct: true,
      probability_of_actual: 0.5736, log_loss: 0.5558229896967265, brier_score: 0.33999656,
      published_probabilities: { home: 0.5736, away: 0.3966, tie: 0.0298, mass_before_normalising: 1 },
    },
    scores: {
      predicted_home: 22.7482, predicted_away: 19.9418,
      home_error: 9.7482, away_error: 9.9418, team_score_mae: 9.845,
    },
    margin: { predicted: 2.8064, actual: 3, error: -0.1936 },
    total: { predicted: 42.69, actual: 23, error: 19.69 },
    distribution: {
      available: true, reason: null, draws: 10000,
      crps_home: 5.886, crps_away: 6.009, crps_margin: 2.771, crps_total: 12.645,
      energy_score: 9.006,
      interval_coverage: {
        margin: {
          50: { level: 0.5, lower: -4, upper: 9, contains: true, width: 13 },
          80: { level: 0.8, lower: -10, upper: 15, contains: true, width: 25 },
          90: { level: 0.9, lower: -14, upper: 19, contains: true, width: 33 },
        },
        total: {
          50: { level: 0.5, lower: 36, upper: 49, contains: false, width: 13 },
          80: { level: 0.8, lower: 30, upper: 56, contains: false, width: 26 },
          90: { level: 0.9, lower: 26, upper: 61, contains: false, width: 35 },
        },
        note: 'For one game these are hits and misses.',
      },
    },
    market: {
      available: false,
      reason: 'No sportsbook quote was ever persisted for this game before kickoff, so there is '
        + 'no market to compare against. None is reconstructed.',
      evidence: null,
    },
    excluded_snapshots: [{
      forecast_identity: 'forecast:NFL_M1:CURRENT:8', snapshot_type: 'CURRENT',
      snapshot_version: 8, published_utc: '2026-09-07T22:00:00+00:00',
      reason: 'SUPERSEDED_BY_A_LATER_PRE_KICKOFF_SNAPSHOT',
      detail: 'A later pre-kickoff snapshot (CURRENT v9) is what an observer would have seen.',
    }],
    snapshots_considered: 10,
    ...over,
  }
}

function model(over: Partial<ProspectiveModel> = {}): ProspectiveModel {
  const games = over.games ?? [game()]
  return {
    engine_id: 'NFL_M1_XGBOOST',
    engine_version: 'NFL_M1_2026_RESEARCH_SHADOW_V1',
    model_label: 'NFL_M1_XGBOOST',
    scored_games: games.length,
    scoreboard: {
      scored_games: games.length, correct_winners: 1, winner_accuracy: 1,
      average_log_loss: 0.5558, average_brier_score: 0.34,
      margin_mae: 0.1936, total_mae: 19.69, team_score_mae: 9.845,
      average_probability_of_actual: 0.5736,
      distribution_sample: 1, market_comparison_sample: 0,
      average_crps_margin: 2.771, average_crps_total: 12.645, average_energy_score: 9.006,
      interval_coverage: {
        sample: 1,
        margin: { 50: { contained: 1, of: 1, rate: 1 }, 80: { contained: 1, of: 1, rate: 1 }, 90: { contained: 1, of: 1, rate: 1 } },
        total: { 50: { contained: 0, of: 1, rate: 0 }, 80: { contained: 0, of: 1, rate: 0 }, 90: { contained: 1, of: 1, rate: 1 } },
        note: 'For one game these are hits and misses.',
      },
    },
    ...over,
    games,
  }
}

function results(over: Partial<NflProspectiveResults> = {}): NflProspectiveResults {
  return {
    schema_version: 'AISP_NFL_PROSPECTIVE_RESULTS_V1',
    data_mode: 'live',
    generated_at_utc: '2026-09-10T12:00:00+00:00',
    league: 'NFL',
    scoring_version: 'AISP_NFL_PROSPECTIVE_SCORING_V1',
    models: [model()],
    total_scored_games: 1,
    review_checkpoint_games: 50,
    sample_note: 'One game is a description of one game and cannot establish that a model works.',
    inventory: {
      numerical_forecast_models: [{
        engine_id: 'NFL_M1_XGBOOST', engine_version: 'NFL_M1_2026_RESEARCH_SHADOW_V1',
        snapshots: 10, events: 1,
        first_published_utc: '2026-08-29T00:00:00+00:00',
        last_published_utc: '2026-09-08T22:53:00+00:00',
        scoreable: true,
      }],
      supporting_evidence: [{
        kind: 'PLAY_LEVEL_SIMULATION', engine_id: 'NFL_M1_XGBOOST', records: 4, events: 1,
        scoreable_as_a_forecast: false,
        why: 'A play-level visualisation publishes no outcome probability, so no scoring rule applies.',
      }],
      nfl_games_with_a_final: 1,
    },
    authority: { wagering_enabled: false },
    requirement: null,
    ...over,
  }
}

describe('sample size', () => {
  it('puts n beside the accuracy rather than leaving 100% to stand alone', () => {
    const { container } = render(<NflProspectiveSection data={results()} />)
    const banner = container.querySelector('.np__sample')!
    expect(banner.textContent).toContain('n = 1')
    expect(banner.textContent).toContain('scored game for NFL_M1_XGBOOST')
    // The accuracy is never shown without its denominator beside it.
    const winners = stat(container, 'Correct winners')
    expect(winners).toContain('1 of 1')
    expect(winners).toContain('100%')
  })

  it('names the review checkpoint without withholding the result until then', () => {
    const section = render(<NflProspectiveSection data={results()} />).container
    expect(section.textContent).toContain('50 settled selections')
    expect(section.textContent).toContain('hiding evidence until then would be its own distortion')
    // The game is on screen NOW, at n = 1.
    expect(screen.getByLabelText('NE at SEA result')).toBeTruthy()
  })

  it('makes no superiority or promotion claim', () => {
    const { container } = render(<NflProspectiveSection data={results()} />)
    const text = container.textContent ?? ''
    for (const forbidden of ['beats the market', 'outperform', 'promote', 'ready for production']) {
      expect(text.toLowerCase()).not.toContain(forbidden)
    }
    expect(text).toContain('creates no position and holds no wagering authority')
  })
})

describe('model separation', () => {
  it('shows one model at a time and never a pooled number', async () => {
    const second = model({
      engine_id: 'NFL_CURRENT_STATE_RESIDUAL_V2_REPAIRED',
      engine_version: 'NFL_UNCERTAINTY_MEAN_SEPARATION_REPAIR_V1',
      model_label: 'NFL_CURRENT_STATE_RESIDUAL_V2_REPAIRED',
      games: [game({ evaluation_id: 'eval-2' })],
    })
    second.scoreboard = { ...second.scoreboard, correct_winners: 0, winner_accuracy: 0, average_log_loss: 1.2 }
    const { container } = render(
      <NflProspectiveSection data={results({ models: [model(), second], total_scored_games: 2 })} />)

    // M1's board is on screen; V2's log loss is not mixed into it.
    expect(stat(container, 'Avg log loss')).toContain('0.5558')
    expect(stat(container, 'Correct winners')).toContain('1 of 1')
    // And it is not a combined two-game aggregate either.
    expect(stat(container, 'Scored games')).toContain('1')

    await userEvent.selectOptions(screen.getByRole('combobox'),
      'NFL_CURRENT_STATE_RESIDUAL_V2_REPAIRED')
    expect(stat(container, 'Avg log loss')).toContain('1.2000')
    expect(stat(container, 'Avg log loss')).not.toContain('0.5558')
    expect(stat(container, 'Correct winners')).toContain('0 of 1')
  })

  it('hides the selector when only one model has a scored forecast', () => {
    const { container } = render(<NflProspectiveSection data={results()} />)
    expect(screen.queryByRole('combobox')).toBeNull()
    // The single model is still named, so no reader mistakes it for "the NFL model".
    expect(container.querySelector('.np__sample')!.textContent).toContain('NFL_M1_XGBOOST')
  })
})

describe('honest unavailability', () => {
  it('says a market comparison does not exist instead of showing a zero', () => {
    render(<NflProspectiveSection data={results()} />)
    const card = screen.getByLabelText('NE at SEA result')
    expect(card.textContent).toContain('Market comparison unavailable')
    expect(card.textContent).toContain('None is reconstructed')
    // The scoreboard counts the comparison as absent, with the reason.
    expect(screen.getByText('no comparable quote')).toBeTruthy()
  })

  it('labels an operator-entered final as not provider-verified', () => {
    render(<NflProspectiveSection data={results()} />)
    const card = screen.getByLabelText('NE at SEA result')
    expect(card.textContent).toContain('not provider-verified')
    expect(card.textContent).toContain('operator entered')
  })

  it('drops the provenance warning when a provider supplied the final', () => {
    const verified = game({
      result: { identity: 'THE_ODDS_API:abc', source: 'THE_ODDS_API',
        observed_utc: '2026-09-10T03:30:00+00:00', provider_verified: true },
    })
    render(<NflProspectiveSection data={results({ models: [model({ games: [verified] })] })} />)
    expect(screen.getByLabelText('NE at SEA result').textContent).not.toContain('not provider-verified')
  })

  it('reports distribution scoring as unavailable with its reason', () => {
    const bare = game({
      distribution: {
        available: false,
        reason: 'This forecast publishes no joint score distribution.',
        draws: null, crps_home: null, crps_away: null, crps_margin: null, crps_total: null,
        energy_score: null, interval_coverage: null,
      },
    })
    render(<NflProspectiveSection data={results({ models: [model({ games: [bare] })] })} />)
    const card = screen.getByLabelText('NE at SEA result')
    expect(card.textContent).toContain('Distribution scoring unavailable')
    expect(card.textContent).toContain('no joint score distribution')
  })

  it('shows the requirement rather than an empty board when nothing is scored', () => {
    render(<NflProspectiveSection data={results({
      models: [], total_scored_games: 0,
      requirement: 'No NFL game has both a final result and a forecast published before kickoff.',
    })} />)
    expect(screen.getByText(/No NFL game has both a final result/)).toBeTruthy()
    expect(screen.queryByLabelText('NE at SEA result')).toBeNull()
  })
})

describe('the error picture', () => {
  it('draws a 0.19 margin miss and a 19.69 total miss on the same scale', () => {
    const { container } = render(<NflProspectiveSection data={results()} />)
    const bars = container.querySelectorAll<HTMLElement>('.np-error__bar')
    const widths = [...bars].map((bar) => parseFloat(bar.style.width))
    const [, , marginWidth, totalWidth] = widths
    expect(marginWidth).toBeLessThan(1)
    expect(totalWidth).toBeGreaterThan(marginWidth * 20)
  })

  it('says which side of the truth each error falls on', () => {
    render(<NflProspectiveSection data={results()} />)
    const card = screen.getByLabelText('NE at SEA result')
    expect(card.textContent).toContain('+19.69 pts (over)')
    expect(card.textContent).toContain('-0.19 pts (under)')
  })

  it('reports interval containment per game as hits and misses', () => {
    const { container } = render(<NflProspectiveSection data={results()} />)
    const legends = container.querySelectorAll('.np-interval__legend')
    expect(legends[0].textContent).toBe('50% hit80% hit90% hit')
    expect(legends[1].textContent).toBe('50% miss80% miss90% miss')
    expect(container.textContent).toContain('For one game these are hits and misses')
  })
})

describe('the evidence drawer', () => {
  it('records that ten snapshots were considered and nine excluded with reasons', () => {
    render(<NflProspectiveSection data={results()} />)
    const card = screen.getByLabelText('NE at SEA result')
    expect(card.textContent).toContain('10 snapshot(s) considered')
    expect(card.textContent).toContain('Ten stored snapshots of one forecast are one game, not ten')
    expect(card.textContent).toContain('A later pre-kickoff snapshot')
    expect(card.textContent).toContain('OPERATOR_ENTERED:6e454379')
  })

  it('separates numerical forecasts from evidence that cannot be scored as one', () => {
    const { container } = render(<NflProspectiveSection data={results()} />)
    const inventory = container.querySelector('.np__inventory')!
    expect(inventory.textContent).toContain('NFL_M1_XGBOOST')
    expect(inventory.textContent).toContain('play level simulation')
    expect(inventory.textContent)
      .toContain('describes those forecasts rather than competing with them')
  })
})
