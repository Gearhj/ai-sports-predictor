import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import fixture from '../../test/topPlayFixture.json'
import type { CoverageGame, DecisionItem } from '../../services/commandCenter'
import { myCard } from '../../services/myCard'
import type { GameAssessment } from '../../services/probability'
import { topPlay } from '../../services/topPlay'
import { DIFFERS_NOTE, MarketSections, RESEARCH_NOTE, TopOpportunity } from './TopOpportunity'
import { MyCard } from './MarketsAndCard'

type Fx = { assessment: GameAssessment; game: CoverageGame; item: DecisionItem }
const real = (matchup: string) => (fixture.games as unknown as Fx[]).find((g) => g.game.matchup === matchup)!
// The capture's prices are current relative to its own clock.
const CAPTURED = Date.parse(fixture.captured_at)

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'], now: CAPTURED })
  try { window.localStorage.clear() } catch { /* ignore */ }
  myCard.reload(); myCard.clear()
})
afterEach(() => { cleanup(); vi.useRealTimers() })

function renderTop(fx: Fx, item: DecisionItem | null = fx.item, status = 'Morning read qualifies · awaiting final check') {
  const play = topPlay(fx.assessment, fx.game, item)
  render(<MemoryRouter>
    <TopOpportunity play={play} game={fx.game} item={item} sport="MLB" status={status} channel={fx.assessment.binding} />
    <MarketSections play={play} assessment={fx.assessment} game={fx.game} sport="MLB" />
  </MemoryRouter>)
  return play
}

describe('Top opportunity panel', () => {
  it('ATH at TB: #1 AISP PICK with the plain $1 figures and one primary action', () => {
    renderTop(real('ATH @ TB'))
    const panel = screen.getByRole('region', { name: 'Top opportunity: #1 AISP PICK' })
    expect(within(panel).getByRole('heading', { name: 'Athletics to win' })).toBeInTheDocument()
    expect(panel).toHaveTextContent('Moneyline · +230 at')
    expect(panel).toHaveTextContent('Bet $1 → $3.30 total if it wins')
    expect(panel).toHaveTextContent('Profit if it wins: $2.30')
    expect(panel).toHaveTextContent('Expected profit: +$0.13 per $1')
    expect(panel).toHaveTextContent('AISP expected return: $1.13 per $1')
    expect(panel).toHaveTextContent('Morning read qualifies · awaiting final check')
    expect(within(panel).getAllByRole('button', { name: /^Add .* to My Card$/ })).toHaveLength(1)
    expect(within(panel).getByText('Why this?')).toBeInTheDocument()
    expect(panel).not.toHaveTextContent(DIFFERS_NOTE)
    // The calculation is behind the disclosure, not in the headline.
    const why = within(panel).getByText('Why this?').closest('details')!
    expect(why).toHaveTextContent('34.2% × (3.300 − 1) − 65.8% = +$0.13 per $1')
  })

  it('LAD at CIN: official moneyline pick and the stronger cross-market value, both labelled', () => {
    const play = renderTop(real('LAD @ CIN'))
    const panel = screen.getByRole('region', { name: 'Top opportunity: #1 AISP PICK' })
    expect(panel).toHaveTextContent('Official AISP pick: Dodgers to win -168')
    expect(panel).toHaveTextContent(`Best cross-market model value: ${play.bestValue!.label}`)
    expect(panel).toHaveTextContent(DIFFERS_NOTE)
  })

  it('without an official decision the best value is labelled research', () => {
    const fx = real('ATH @ TB')
    renderTop(fx, { ...fx.item, recommendation: 'WATCH' })
    const panel = screen.getByRole('region', { name: 'Top opportunity: BEST CURRENT MODEL VALUE' })
    expect(panel).toHaveTextContent(RESEARCH_NOTE)
  })

  it('same-game combinations say the parlay price is missing and are not ranked', () => {
    renderTop(real('LAD @ CIN'))
    const combos = screen.getByRole('region', { name: 'Same-game combinations' })
    expect(within(combos).getAllByText(/Model probability available · sportsbook parlay price unavailable/).length).toBeGreaterThan(0)
    expect(within(combos).queryByText(/#\d/)).toBeNull()
  })

  it('market sections list explicit, ranked rows for each side', () => {
    renderTop(real('ATH @ TB'))
    const ml = screen.getByRole('region', { name: 'Moneyline' })
    const rows = within(ml).getAllByRole('listitem').filter((li) => li.classList.contains('tp-row'))
    expect(rows.map((r) => r.querySelector('.tp-row__sel b')!.textContent)).toEqual(['Athletics to win', 'Rays to win'])
    expect(rows[0]).toHaveTextContent('#1')
    expect(rows[0]).toHaveTextContent('$1 → $3.30 if it wins')
    const total = screen.getByRole('region', { name: 'Game total' })
    expect(within(total).getByText('Over 7.5')).toBeInTheDocument()
    expect(within(total).getByText('Under 7.5')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Run line' })).toHaveTextContent('Athletics +1.5')
  })
})

describe('My Card from the top opportunity', () => {
  it('freezes the price, payout and AISP expectation at selection time', () => {
    const fx = real('ATH @ TB')
    renderTop(fx)
    const panel = screen.getByRole('region', { name: 'Top opportunity: #1 AISP PICK' })
    fireEvent.click(within(panel).getByRole('button', { name: /^Add Athletics to win \+230 at .* to My Card$/ }))
    const [pick] = myCard.list()
    expect(pick).toMatchObject({ market: 'MONEYLINE', outcome: 'AWAY', american: 230, origin: 'OFFICIAL_PICK' })
    expect(pick.expectation!.expected_profit).toBeCloseTo(0.12926, 4)
    expect(pick.expectation!.forecast_id).toBe(fx.assessment.binding.binding!.forecastId)
    expect(within(panel).getByRole('button', { name: /is on My Card$/ })).toBeDisabled()
    cleanup()

    render(<MemoryRouter><MyCard now={CAPTURED} replay={false} onReplay={() => {}} /></MemoryRouter>)
    const row = screen.getAllByRole('listitem').find((li) => li.classList.contains('mc-pick'))!
    expect(row).toHaveTextContent('Your virtual copy of the #1 AISP pick')
    expect(row).toHaveTextContent('Total if it wins $33.00')
    expect(row).toHaveTextContent('profit if it wins $23.00')
    expect(row).toHaveTextContent('AISP expected profit when added: +$0.13 per $1')
    expect(row).toHaveTextContent('frozen at this price')
  })

  it('a stale quote cannot be added', () => {
    const fx = real('ATH @ TB')
    const stale = { ...fx.assessment, quotes: fx.assessment.quotes.map((q) => ({ ...q, state: 'STORED_REFERENCE_ONLY' as const, fresh: false })) }
    renderTop({ ...fx, assessment: stale }, null)
    expect(screen.getByRole('region', { name: 'Top opportunity: CURRENT PRICE NEEDED' }))
      .toHaveTextContent('Stored reference price — current expected return unavailable')
    expect(screen.queryAllByRole('button', { name: /^Add / })).toHaveLength(0)
  })
})
