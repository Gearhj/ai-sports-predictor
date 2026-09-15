/**
 * A recorded position and the latest market are two different facts.
 *
 * On 2026-09-14 Daily Decisions showed DEN at KC "Position recorded" beside
 * "+114 · sgo:fanduel". The position was taken at +115 on sgo:espnbet. For four
 * of that day's six positions the adjacent price was on the other team. These
 * render the real payload shape and assert the two never merge.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SlateBoard } from './SlateBoard'
import type { PositionEntryTerms, SlateGame, SlateStatus } from '../../types/slate'

afterEach(cleanup)

const DEN_ENTRY: PositionEntryTerms = {
  position_id: 'd85909ab-f8ae-488b-be5c-dd0b0cc98117',
  decision_id: '94f5efe0-e006-4e8a-bfdd-7c4246961dd9',
  side: 'DEN', market: 'MONEYLINE',
  american_odds: 115, american_label: '+115', decimal_odds: 2.15,
  sportsbook_key: 'sgo:espnbet', sportsbook_name: 'Espnbet (SportsGameOdds, research)',
  provider: { source: 'SPORTSGAMEODDS', name: 'SportsGameOdds',
              classification: 'RESEARCH', label: 'SportsGameOdds · research provider' },
  entry_quote_observed_at: '2026-09-14T23:42:38.389000+00:00',
  stake: 5, placed_at: '2026-09-14T23:49:35.655349+00:00',
  recorded_at: '2026-09-14T23:49:35.656442+00:00',
  status: 'settled', result: 'loss',
  tie_settlement: 'STAKE_REFUNDED_PUSH',
  tie_settlement_label: 'Tie settlement: stake refunded',
  headline: 'DEN +115',
}

function denKc(): SlateGame {
  return {
    game_id: 'g-den-kc', league_code: 'NFL',
    teams: { away: 'DEN', home: 'KC' },
    starts_at: '2026-09-15T00:15:00+00:00', game_status: 'FINAL',
    forecast: null,
    // The latest stored market -- a different book and a different price.
    quote: {
      observed_at: '2026-09-14T23:48:00+00:00', age_minutes: 138.4,
      sportsbook: 'sgo:fanduel', american_odds: 114, source: 'SPORTSGAMEODDS',
      side: 'DEN', role: 'LATEST_MARKET_PRICE',
      satisfies_policy_freshness: true, freshness_limit_minutes: 240,
    },
    policy: { active_for_sport: true, note: null },
    latest_evaluation: { recommendation: 'QUALIFIED_BET', reason: null,
                         phase: 'FINAL_PREGAME', recommended_at: null },
    next_checkpoint: null,
    positions: 1,
    recorded_positions: [DEN_ENTRY],
    state: 'POSITION_RECORDED',
    explanation: '1 recorded position(s) for this game.',
  } as SlateGame
}

function slate(games: SlateGame[]): SlateStatus {
  return { schema_version: 'AISP_SLATE_STATUS_V1',
           window: { date: '2026-09-14', timezone: 'America/New_York', description: '' },
           games } as unknown as SlateStatus
}

describe('recorded position versus latest market', () => {
  it('shows the persisted entry terms, not the latest quote, as the position', () => {
    render(<SlateBoard slate={slate([denKc()])} filter="ALL" onFilter={() => undefined} />)
    const entry = screen.getByTestId(`recorded-entry-${DEN_ENTRY.position_id}`)

    expect(within(entry).getByTestId('recorded-entry-terms').textContent).toBe('DEN +115')
    expect(within(entry).getByTestId('recorded-entry-book').textContent).toContain('sgo:espnbet')
    expect(within(entry).getByTestId('recorded-entry-stake').textContent).toBe('$5.00')
    expect(within(entry).getByTestId('recorded-entry-provider').textContent)
      .toBe('SportsGameOdds · research provider')
    expect(within(entry).getByTestId('recorded-entry-tie').textContent)
      .toBe('Tie settlement: stake refunded')
  })

  it('never lets the latest quote into the recorded-position block', () => {
    render(<SlateBoard slate={slate([denKc()])} filter="ALL" onFilter={() => undefined} />)
    const entry = screen.getByTestId(`recorded-entry-${DEN_ENTRY.position_id}`)

    expect(entry.textContent).not.toContain('114')
    expect(entry.textContent).not.toContain('fanduel')
  })

  it('labels the latest quote as the latest market price, with its own side and book', () => {
    render(<SlateBoard slate={slate([denKc()])} filter="ALL" onFilter={() => undefined} />)
    const row = screen.getByTestId('slate-g-den-kc')

    expect(row.textContent).toContain('Latest market price')
    expect(screen.getByTestId('slate-latest-g-den-kc').textContent).toContain('DEN +114')
    expect(row.textContent).toContain('sgo:fanduel')
  })

  it('omits a tie rule the decision never persisted', () => {
    const mlb = { ...DEN_ENTRY, tie_settlement: null, tie_settlement_label: null }
    render(<SlateBoard slate={slate([{ ...denKc(), recorded_positions: [mlb] }])}
      filter="ALL" onFilter={() => undefined} />)
    expect(screen.queryByTestId('recorded-entry-tie')).toBeNull()
  })
})
