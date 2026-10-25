/**
 * The CFB game page's pipeline: stored quotes appear with their clocks and the
 * joint comparison; with none stored, the coverage reason stays; and the
 * checkpoint's recorded meaning (NO_BET / NO_PRICE / BLOCKED) is stated.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { CfbPipelinePanel } from './CfbPipelinePanel'
import type { CfbPipeline } from '../../types/cfbResearch'
import { fixtureMarket } from '../../test/cfbQuoteFixture'

afterEach(cleanup)

const base: CfbPipeline = {
  origin: 'PRODUCTION_DISCOVERY',
  publication_clock: { source: 'DISCOVERY_RECEIPT_COMMITTED_AT', label: 'receipt', published_utc: '2026-09-26T12:00:00+00:00' },
  discovery: { state: 'CURRENT', eligible: true, lease_valid_until: '2026-09-26T20:00:00+00:00' },
  decision: null,
  decision_window: { opens_utc: '2026-09-26T19:30:00+00:00', closes_utc: '2026-09-26T19:45:00+00:00' },
  price: { reason: 'NO_UNCOMMITTED_SGO_CAPACITY', detail: 'Guard B has 186 objects left; 0 are genuinely uncommitted.', reference_lines: 'reference only' },
}

describe('CfbPipelinePanel · market', () => {
  it('lists each stored quote with its three clocks and its joint comparison', () => {
    render(<CfbPipelinePanel pipeline={{ ...base, market: fixtureMarket }} home="Home U" away="Away U" />)
    const market = screen.getByTestId('cfb-market')
    const ml = within(market).getByTestId('cfb-quote-moneyline')
    expect(ml).toHaveTextContent('Moneyline · DraftKings (SportsGameOdds, research) (SPORTSGAMEODDS): Home U -150 / Away U +130')
    expect(ml).toHaveTextContent('quote 3:10 PM ET (older leg; home 3:10 PM, away 3:13 PM)')
    expect(ml).toHaveTextContent('fetched 3:20 PM ET · received 3:20 PM ET')
    expect(ml).toHaveTextContent('Joint model Home U 62.0%')
    expect(ml).toHaveTextContent('the policy tests this market only at its checkpoint')
    expect(within(market).getByTestId('cfb-quote-spread')).toHaveTextContent('research comparison; the policy does not test this market')
    expect(market).not.toHaveTextContent(/ridge|elo/i)
    expect(screen.queryByText(/genuinely uncommitted/)).toBeNull()
  })

  it('keeps the coverage reason when no quote is stored', () => {
    render(<CfbPipelinePanel pipeline={{ ...base, market: { ...fixtureMarket, state: 'NO_QUOTE_ROWS', quotes: [], comparisons: [] } }} home="H" away="A" />)
    expect(screen.getByTestId('cfb-price')).toHaveTextContent('0 are genuinely uncommitted')
    expect(screen.queryByTestId('cfb-market')).toBeNull()
  })
})

describe('CfbPipelinePanel · checkpoint meaning', () => {
  const recorded = (reason: string, cls: 'NO_BET' | 'NO_PRICE' | 'BLOCKED', recommendation = 'DECLINED'): CfbPipeline => ({
    ...base,
    decision: { recommendation, reason, detail: null, recorded_at: '2026-09-26T19:31:00+00:00', position: null,
      semantics: { class: cls, reason, evaluated: cls !== 'BLOCKED', priced: cls === 'NO_BET', text: '' } },
  })

  it.each([
    ['NO_QUALIFYING_EDGE', 'NO_BET', 'No bet · priced, edge below policy'],
    ['NO_TWO_SIDED_QUOTE', 'NO_PRICE', 'Declined · no same-book two-sided moneyline was stored'],
    ['DISCOVERY_NOT_CURRENT', 'BLOCKED', 'Blocked · not evaluated'],
  ] as const)('%s reads as %s', (reason, cls, label) => {
    render(<CfbPipelinePanel pipeline={recorded(reason, cls, cls === 'BLOCKED' ? 'BLOCKED' : 'DECLINED')} home="H" away="A" />)
    expect(screen.getByTestId('cfb-decision')).toHaveTextContent(label)
  })
})
