/**
 * The disclosure's job is to answer a question without creating a new one.
 *
 * The fixture is the real Kansas City position: +195, raw 38.46%, adjusted
 * 36.46%, break-even 33.90%. It is the case where a reader is most likely to
 * come away believing something false — that AISP expected Kansas City to win —
 * so most of these tests are about what the panel must NOT say.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { WagerExplanation } from './WagerExplanation'
import type { DecisionExplanation } from '../../types/explain'

const PRICE = {
  american_odds: 195,
  decimal_odds: 2.95,
  model_probability_pct: 36.46,
  raw_model_probability_pct: 38.46,
  break_even_probability_pct: 33.9,
  no_vig_probability_pct: 32.31,
  margin_over_break_even_pts: 2.56,
  disagreement_with_market_pts: 4.15,
  clears_break_even: true,
  model_makes_it_favoured: false,
  priced_as_underdog: true,
  probability_units: 'PERCENT' as const,
  definitions: {},
}

const DETAILS = {
  decision_id: 'd1', position_id: 'p1', phase: 'FINAL_PREGAME',
  recommended_at_utc: '2026-09-12T19:30:00Z',
  quote_observed_at_utc: '2026-09-12T19:28:00Z',
  sportsbook: 'Bovada',
  policy_version: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1',
  staking_version: 'AISP_QUARTER_KELLY_EVIDENCE_V2',
  tier: 'TIER_B_PROBABLE_STABLE',
  gates_passed: ['valid_current_forecast', 'adjusted_edge_minimum'],
  forecast_identity: 'mlb-v18-daily-910080c6',
  engine_id: 'MLB_V18_WHOLE_GAME',
  engine_version: 'v18',
  forecast_evidence_cutoff_utc: '2026-09-12T17:18:15Z',
  forecast_published_utc: '2026-09-12T17:31:23Z',
  bound_at_decision_time:
    'This explanation uses only the forecast and price this decision bound. '
    + 'Later forecasts, corrected lineups, current prices and the result do not enter it.',
}

function explanation(over: Partial<DecisionExplanation> = {}): DecisionExplanation {
  return {
    schema_version: 'AISP_DECISION_EXPLANATION_V1',
    decision_id: 'd1', position_id: 'p1', game_id: 'g1', slate_date: '2026-09-12',
    matchup: {
      home: 'BOS', away: 'KC', home_name: 'Boston Red Sox',
      away_name: 'Kansas City Royals', label: 'KC at BOS', starts_at_utc: null,
    },
    recorded_wager: true,
    why: {
      schema_version: 'AISP_WAGER_EXPLANATION_V1',
      recorded_wager: true,
      status_line: 'A virtual wager was recorded for this selection.',
      market: 'MONEYLINE',
      selection: 'KC ML',
      bullets: [
        { key: 'selection', text: 'KC ML in KC at BOS was the selection, staked at $2.49.' },
        {
          key: 'probability',
          text: 'The model gave it 38.46% before adjustments and 36.46% after: '
            + '2.00 pts for starting pitcher not yet confirmed.',
        },
        {
          key: 'price',
          text: 'The offered price was +195, which needs 33.90% to break even — '
            + 'the estimate is 2.56 points above that.',
        },
        { key: 'policy', text: 'It qualified because the edge cleared the minimum.' },
        {
          key: 'support',
          text: 'Detailed matchup contributions were not recorded for this forecast, '
            + 'so no claim is made about team strength, pitching, weather or park.',
          attribution_available: false,
        },
      ],
      price: PRICE,
      value_framing: {
        kind: 'UNDERDOG_TAKEN_ON_PRICE',
        headline:
          'The model did NOT make this the more likely winner. It was taken because '
          + "the offered price required a lower chance than the model's estimate — "
          + 'value at the price, not a prediction that it would win.',
        model_makes_it_favoured: false,
        priced_as_underdog: true,
        clears_break_even: true,
        note:
          'Being worth backing at a price and being likely to win are different things.',
      },
      details: DETAILS,
    },
    what_happened: {
      schema_version: 'AISP_OUTCOME_EXPLANATION_V1',
      result: 'loss',
      result_language: 'lost',
      bullets: [
        { key: 'needed', text: 'The selection needed KC to win the game.' },
        { key: 'outcome', text: 'Final: KC 1, BOS 5 — the wager lost (-2.49).' },
        {
          key: 'events',
          text: 'Deeper event analysis is unavailable: AISP holds no play-level record.',
          evidence_available: false,
        },
      ],
      uncertainty: {
        resolved: false,
        inside_central_range: true,
        central_range: [4, 15],
        text: "The final total sat inside the forecast's central range (4–15 runs). "
          + 'That is consistent with the forecast; it does not by itself show the '
          + 'forecast was right or wrong about this game.',
      },
      interpretation_guard:
        'This describes the result. A single game does not establish that the model '
        + 'was wrong.',
      event_evidence_available: false,
    },
    derived_only: 'Derived presentation data.',
    ...over,
  }
}

function mount(over: Partial<DecisionExplanation> = {}) {
  return render(<WagerExplanation explanation={explanation(over)} />)
}

describe('the panels start collapsed', () => {
  afterEach(cleanup)

  it('renders both toggles closed', () => {
    mount()
    expect(screen.getByTestId('explain-why-toggle')).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByTestId('explain-what-toggle')).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByTestId('explain-why')).not.toBeVisible()
  })

  it('opens on click and reports it to assistive technology', () => {
    mount()
    fireEvent.click(screen.getByTestId('explain-why-toggle'))
    expect(screen.getByTestId('explain-why-toggle')).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByTestId('explain-why')).toBeVisible()
  })

  it('keeps the evidence identities in a second, separate disclosure', () => {
    mount()
    fireEvent.click(screen.getByTestId('explain-why-toggle'))
    expect(screen.getByTestId('explain-details')).not.toBeVisible()
    fireEvent.click(screen.getByTestId('explain-details-toggle'))
    expect(screen.getByTestId('explain-details')).toBeVisible()
    expect(screen.getByTestId('explain-details')).toHaveTextContent('mlb-v18-daily-910080c6')
  })
})

describe('an underdog is never presented as the likely winner', () => {
  afterEach(cleanup)

  it('leads with the framing sentence', () => {
    mount()
    fireEvent.click(screen.getByTestId('explain-why-toggle'))
    const framing = screen.getByTestId('explain-framing')
    expect(framing).toHaveTextContent('did NOT make this the more likely winner')
    expect(framing).toHaveTextContent('value at the price')
  })

  it('shows the three probabilities under distinct labels', () => {
    mount()
    fireEvent.click(screen.getByTestId('explain-why-toggle'))
    const price = screen.getByTestId('explain-price')
    expect(price).toHaveTextContent('36.46%')  // model
    expect(price).toHaveTextContent('33.90%')  // what the price needs
    expect(price).toHaveTextContent('32.31%')  // no-vig market
    expect(price).toHaveTextContent('Price needs')
    expect(price).toHaveTextContent('Market (no-vig)')
  })

  it('carries the recorded adjustment and its reason', () => {
    mount()
    fireEvent.click(screen.getByTestId('explain-why-toggle'))
    expect(screen.getByTestId('explain-why')).toHaveTextContent(
      '38.46% before adjustments and 36.46% after')
    expect(screen.getByTestId('explain-why')).toHaveTextContent(
      'starting pitcher not yet confirmed')
  })
})

describe('a recorded wager is distinguished from a qualifying opportunity', () => {
  afterEach(cleanup)

  it('says a wager was recorded', () => {
    mount()
    fireEvent.click(screen.getByTestId('explain-why-toggle'))
    expect(screen.getByTestId('explain-status')).toHaveTextContent(
      'A virtual wager was recorded')
  })

  it('says plainly when none was', () => {
    const base = explanation()
    mount({
      recorded_wager: false,
      why: {
        ...base.why, recorded_wager: false,
        status_line:
          'This selection qualified under the policy. No virtual wager was recorded.',
      },
    })
    fireEvent.click(screen.getByTestId('explain-why-toggle'))
    expect(screen.getByTestId('explain-status')).toHaveTextContent(
      'No virtual wager was recorded')
  })
})

describe('what happened', () => {
  afterEach(cleanup)

  it('reports the result and what the selection needed', () => {
    mount()
    fireEvent.click(screen.getByTestId('explain-what-toggle'))
    const panel = screen.getByTestId('explain-what')
    expect(panel).toHaveTextContent('needed KC to win')
    expect(panel).toHaveTextContent('Final: KC 1, BOS 5')
  })

  it('says event analysis is unavailable rather than narrating the score', () => {
    mount()
    fireEvent.click(screen.getByTestId('explain-what-toggle'))
    expect(screen.getByTestId('explain-what')).toHaveTextContent(
      'Deeper event analysis is unavailable')
  })

  it('labels a loss neither a model failure nor bad luck', () => {
    mount()
    fireEvent.click(screen.getByTestId('explain-what-toggle'))
    const text = screen.getByTestId('explain-what').textContent ?? ''
    expect(text.toLowerCase()).not.toContain('model failure')
    expect(text.toLowerCase()).not.toContain('bad luck')
    expect(screen.getByTestId('explain-uncertainty')).toHaveTextContent(
      'does not by itself show the forecast was right or wrong')
  })

  it('is absent entirely while the wager is still open', () => {
    mount({ what_happened: null })
    expect(screen.queryByTestId('explain-what-toggle')).toBeNull()
    expect(screen.getByTestId('explain-why-toggle')).toBeInTheDocument()
  })
})
