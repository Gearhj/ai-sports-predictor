import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { DecisionLead, ExpandableDetails } from './DecisionLead'
import type { DecisionLead as Lead } from '../../services/decisionLead'

const NOW = Date.parse('2026-09-24T20:40:00Z')

const provisional: Lead = {
  kind: 'PROVISIONAL', authority: 'PROVISIONAL', tone: 'gold', label: 'Morning read · not final',
  sentence: 'The morning read does not clear the policy so far; the official decision is made in the 6:15–6:30 PM ET decision window.',
  official: { selection: 'CLE ML', market: 'Moneyline', sportsbook: 'Caesars', american: 112, quoteObservedAt: '2026-09-24T18:37:00Z', quoteAgeAtDecisionMinutes: 128, recordedAt: '2026-09-24T20:45:00Z' },
  edge: { label: 'Recorded policy-adjusted edge', value: 0.016, text: '+1.6 pts', qualifier: 'as recorded · policy requires ≥ 2.0 pts' },
  funded: null,
}

describe('DecisionLead', () => {
  it('leads with the authority, status, recorded selection, one sentence, edge, latest seen and countdown', () => {
    render(<DecisionLead lead={provisional} latest={{ american: 115, sportsbook: 'FanDuel', observedAt: '2026-09-24T20:36:00Z' }}
      countdown={{ label: 'Decision window opens in', value: '1 h 35 min' }} now={NOW} />)
    const lead = screen.getByRole('region', { name: 'Official AISP decision · early read, not final: Morning read · not final' })
    expect(lead).toHaveTextContent('Early-read selection · Moneyline')
    expect(lead).toHaveTextContent('CLE ML +112 at Caesars')
    expect(lead).toHaveTextContent('Recorded at decision')
    expect(lead).toHaveTextContent(provisional.sentence)
    expect(lead).toHaveTextContent('Recorded policy-adjusted edge+1.6 pts')
    // The best cross-book quote is named neutrally, with its book and clock, and never replaces the recorded +112.
    // 4:36 PM ET is after the recorded quote's 2:37 PM ET, so (and only so) it may be called later.
    expect(lead).toHaveTextContent('Best current quote across books+115FanDuel · observed 4:36 PM ET · later than the decision price')
    expect(lead).not.toHaveTextContent('later market')
    expect(lead).toHaveTextContent('Decision window opens in1 h 35 min')
    expect(within(lead).getByRole('list', { name: 'Freshness' })).toHaveTextContent('Price 128 min old at decision')
  })

  it('a funded wager says it is backed by a ledger position; unavailable values are explicit', () => {
    render(<DecisionLead lead={{ ...provisional, kind: 'FUNDED_OPEN', authority: 'FUNDED', tone: 'green', label: 'Funded paper wager',
      edge: { ...provisional.edge, value: null, text: 'not available' },
      funded: { positionId: 'p1', stake: 5, american: 110, sportsbook: 'Pointsbet', ledgerConfirmed: true, result: null, profitLoss: null } }}
      latest={null} countdown={null} now={NOW} />)
    const lead = screen.getByRole('region', { name: /^Funded paper wager: Funded paper wager$/ })
    expect(lead).toHaveTextContent('Paper stake $5.00 · entry +110 at Pointsbet · backed by a ledger position')
    expect(lead).toHaveTextContent('Recorded policy-adjusted edgenot available')
    expect(lead).toHaveTextContent('Best current quote across booksno current price')
  })

  it('a shadow-only (funding-paused) decision says so and never shows a stake', () => {
    render(<DecisionLead lead={{ ...provisional, kind: 'QUALIFIED_SHADOW_ONLY', authority: 'FINAL', tone: 'gray',
      label: 'Qualified · shadow only (funding paused)',
      sentence: 'It met the policy at the final check, but MLB V18 funding is paused pending prospective validation, so no paper position was created.',
      fundingPause: { recordId: 'rec-1', contentSha256: '0123456789abcdef'.repeat(4), pausedAt: '2026-09-24T20:31:00Z',
        reason: 'FUNDING_EDGE_NOT_ESTABLISHED', status: 'PENDING_PROSPECTIVE_VALIDATION', verified: true } }}
      latest={null} countdown={null} now={NOW} />)
    const lead = screen.getByRole('region', { name: 'Official AISP decision · final: Qualified · shadow only (funding paused)' })
    expect(lead).toHaveTextContent('Official selection · Moneyline')
    expect(lead).toHaveTextContent('MLB V18 funding is paused pending prospective validation, so no paper position was created.')
    expect(lead).toHaveTextContent('Shadow only · no paper position · funding pause recorded 4:31 PM ET · record 0123456789ab')
    expect(lead).not.toHaveTextContent('Paper stake')
    expect(lead).not.toHaveTextContent('not funded yet')
    expect(lead).not.toHaveTextContent('Funded')
  })

  it('no decision yet: no selection is invented', () => {
    render(<DecisionLead lead={{ ...provisional, kind: 'NOT_EVALUATED', authority: 'NONE', official: null, tone: 'orange', label: 'Waiting on sportsbook prices', waitingOn: 'PRICE' }} latest={null} countdown={null} now={NOW} />)
    const lead = screen.getByRole('region', { name: /not made yet: Waiting on sportsbook prices$/ })
    expect(lead).toHaveClass('dl--tone-orange')
    expect(screen.getByRole('note')).toHaveTextContent('No position can be taken until a sportsbook posts a current price on both sides.')
    expect(lead).toHaveTextContent('Official selectionNone recorded yet')
    expect(lead).not.toHaveTextContent('Best current quote')
  })
})

describe('ExpandableDetails', () => {
  it('is keyboard operable and reports aria-expanded', async () => {
    const user = userEvent.setup()
    render(<ExpandableDetails summary="Details · clocks, policy tests and evidence"><p>import transaction began 3:56 PM</p></ExpandableDetails>)
    const toggle = screen.getByRole('button', { name: /Details · clocks, policy tests and evidence/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('import transaction began 3:56 PM')).not.toBeVisible()
    await user.tab()
    expect(toggle).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('import transaction began 3:56 PM')).toBeVisible()
    await user.keyboard(' ')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(document.getElementById(toggle.getAttribute('aria-controls')!)).not.toBeNull()
  })
})
