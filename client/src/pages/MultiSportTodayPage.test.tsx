import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SportRegistryProvider } from '../hooks/useSportRegistry'
import { endpoints } from '../services/api'
import { sportRegistryFixture } from '../test/sportRegistryFixture'
import type { NormalizedSelection, SportSection, TodayAllSportsReadModel } from '../types/multisport'
import { hasNoCrossSportRanking, MultiSportTodayPage } from './MultiSportTodayPage'

const baseSelection: NormalizedSelection = {
  sport_code: 'BASEBALL',
  league_code: 'MLB',
  event_id: 'mlb-1',
  event_label: 'BOS @ NYY',
  starts_at: '2026-08-31T23:00:00Z',
  decision_state: 'AWAITING_FINAL_EVIDENCE',
  funding_state: 'ELIGIBLE_NOT_FUNDED',
  market_label: 'Moneyline',
  selection_label: 'NYY',
  line: null,
  price_american: -120,
  sportsbook: 'governed-book',
  model_probability: 56,
  no_vig_probability: 53,
  adjusted_edge: 3,
  expected_value: 4,
  evidence_tier: 'TIER_1',
  virtual_stake: null,
  experiment_id: null,
  experiment_name: null,
  policy_version: 'POLICY_V1',
  official_position_id: null,
  decision_timestamp: '2026-08-31T15:00:00Z',
  final_decision_cutoff: '2026-08-31T22:45:00Z',
  source_label: 'AISP_BASELINE',
  model_status: 'DEVELOPMENT',
  outstanding_gates: ['LINEUP_NOT_CONFIRMED'],
  status_note: 'Awaiting final evidence.',
  can_ever_fund_official_position: true,
  sport_rank: 1,
  detail_href: '/games/mlb-1',
}

const nflSelection: NormalizedSelection = {
  ...baseSelection,
  sport_code: 'FOOTBALL',
  league_code: 'NFL',
  event_id: 'nfl-1',
  event_label: 'DAL @ PHI',
  decision_state: 'PROVISIONAL_EDGE',
  funding_state: 'RESEARCH_ONLY',
  can_ever_fund_official_position: false,
  source_label: 'NFL_M1_XGBOOST',
  status_note: 'NFL research shadow — no official bankroll',
  detail_href: '/shadow-board?date=2026-08-31&sport=NFL#event-nfl-1',
}

function mlbSection(selections: NormalizedSelection[]): SportSection {
  return {
    sport_code: 'BASEBALL', league_code: 'MLB', display_name: 'Major League Baseball', availability_state: 'LIVE',
    scheduled_event_count: selections.length, valid_forecast_count: selections.length,
    final_qualified_decision_count: 0, official_position_count: 0, final_qualified_count: 0,
    awaiting_final_evidence_count: selections.length, provisional_count: 0, watch_count: 0, pass_count: 0,
    open_position_count: 0, settled_today_count: 0, virtual_stake: 0, data_health: 91,
    latest_forecast_at: '2026-08-31T15:00:00Z', latest_market_at: '2026-08-31T15:00:00Z',
    engine_label: 'AISP_BASELINE', can_create_official_positions: true, official_positions_blocked_reason: null,
    degraded: false, degraded_reason: null, selections,
  }
}

function nflSection(selections: NormalizedSelection[]): SportSection {
  return {
    sport_code: 'FOOTBALL', league_code: 'NFL', display_name: 'National Football League', availability_state: 'RESEARCH_SHADOW',
    scheduled_event_count: selections.length, valid_forecast_count: selections.length,
    final_qualified_decision_count: 0, official_position_count: 0, final_qualified_count: 0,
    awaiting_final_evidence_count: 0, provisional_count: selections.length, watch_count: 0, pass_count: 0,
    open_position_count: 0, settled_today_count: 0, virtual_stake: 0, data_health: null,
    latest_forecast_at: '2026-08-31T14:00:00Z', latest_market_at: null,
    engine_label: 'NFL_M1_XGBOOST', can_create_official_positions: false,
    official_positions_blocked_reason: 'Research shadow only.',
    degraded: true, degraded_reason: 'No governed NFL market observation is persisted for this slate.',
    selections,
  }
}

function model(sections: SportSection[]): TodayAllSportsReadModel {
  const selections = sections.flatMap((section) => section.selections)
  return {
    data_mode: 'live',
    schema_version: 'TODAY_ALL_SPORTS_READ_MODEL_V1',
    registry_version: 'SPORT_REGISTRY_V1',
    operational_date: '2026-08-31',
    display_timezone: 'America/New_York',
    generated_at: '2026-08-31T16:00:00Z',
    overall_summary: {
      final_qualified_decision_count: 0, official_position_count: 0, final_qualified_position_count: 0,
      total_virtual_stake: 0, open_position_count: 0, settled_today_count: 0,
      sports_with_official_positions: [], sports_with_events: ['MLB', 'NFL'],
      latest_complete_refresh: '2026-08-31T15:00:00Z', stale_or_degraded_sport_count: 1,
    },
    qualified_positions: selections.filter((item) => item.decision_state === 'FINAL_QUALIFIED'),
    awaiting_final_evidence: selections.filter((item) => item.decision_state === 'AWAITING_FINAL_EVIDENCE'),
    provisional_opportunities: selections.filter((item) => item.decision_state === 'PROVISIONAL_EDGE'),
    sport_sections: sections,
    data_freshness: {
      latest_forecast_at: '2026-08-31T15:00:00Z', latest_market_at: '2026-08-31T15:00:00Z',
      scheduler_running: false, provider_calls_made: 0,
    },
    availability: [
      { sport_code: 'BASEBALL', league_code: 'MLB', display_name: 'Major League Baseball', availability: 'LIVE', navigable: true, note: null },
      { sport_code: 'FOOTBALL', league_code: 'NFL', display_name: 'National Football League', availability: 'RESEARCH_SHADOW', navigable: true, note: null },
      { sport_code: 'BASKETBALL', league_code: 'NBA', display_name: 'National Basketball Association', availability: 'PLANNED', navigable: false, note: null },
    ],
    cross_sport_ranking: 'NOT_APPLICABLE_PHASE_1',
    notes: {},
  }
}

const payload = model([mlbSection([baseSelection]), nflSection([nflSelection])])

// The page groups against the current clock, so pin it. Without this the
// fixture's cutoffs drift into the past and every "watching" case silently
// reclassifies as a closed decision window.
beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-08-31T16:00:00Z'))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function mount(data: TodayAllSportsReadModel, registryOk = true) {
  vi.spyOn(endpoints, 'today').mockResolvedValue(data)
  if (registryOk) vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
  else vi.spyOn(endpoints, 'sportsRegistry').mockRejectedValue(new Error('registry offline'))
  render(
    <MemoryRouter>
      <SportRegistryProvider>
        <MultiSportTodayPage />
      </SportRegistryProvider>
    </MemoryRouter>,
  )
}

function group(name: string): HTMLElement {
  return screen.getByRole('heading', { name: new RegExp(`^${name}`) }).closest('section')!
}

describe('Daily Command Center — grouping', () => {
  it('scenario 1: no qualified positions, multiple games watching', async () => {
    const watching = [
      baseSelection,
      { ...baseSelection, event_id: 'mlb-2', event_label: 'TB @ TOR' },
      { ...baseSelection, event_id: 'mlb-3', event_label: 'SEA @ LAA' },
    ]
    mount(model([mlbSection(watching)]))

    expect(await screen.findByText(/No qualified decisions yet\. 3 decisions are still under review\./)).toBeInTheDocument()
    expect(within(group('Ready')).getByText(/A zero-position day is valid/i)).toBeInTheDocument()
    expect(within(group('Watching')).getByText('TB @ TOR')).toBeInTheDocument()
  })

  it('scenario 2: a ready virtual position is separated from watching games', async () => {
    const ready: NormalizedSelection = {
      ...baseSelection,
      event_id: 'mlb-ready', event_label: 'TOR @ BAL',
      decision_state: 'FINAL_QUALIFIED', funding_state: 'OFFICIAL_VIRTUAL_POSITION',
      official_position_id: 'position-1', virtual_stake: 12, outstanding_gates: [],
    }
    mount(model([mlbSection([ready, baseSelection])]))

    expect(await screen.findByText(/1 virtual position is ready\. 1 decision remains under review\./)).toBeInTheDocument()
    const readyGroup = group('Ready')
    expect(within(readyGroup).getByText('TOR @ BAL')).toBeInTheDocument()
    expect(within(readyGroup).getByText(/Qualified virtual position recorded before the cutoff/)).toBeInTheDocument()
    expect(within(readyGroup).getByText('$12.00')).toBeInTheDocument()
    expect(within(group('Watching')).getByText('BOS @ NYY')).toBeInTheDocument()
  })

  it('scenario 3: an evaluated pass is distinguished from a game never evaluated', async () => {
    const passed: NormalizedSelection = {
      ...baseSelection,
      event_id: 'mlb-pass', event_label: 'CHC @ STL',
      decision_state: 'FINAL_NO_BET', outstanding_gates: ['EDGE_BELOW_THRESHOLD'],
    }
    const unevaluated: NormalizedSelection = {
      ...baseSelection,
      event_id: 'mlb-none', event_label: 'SD @ SF',
      decision_state: 'NOT_EVALUATED', outstanding_gates: [],
    }
    mount(model([mlbSection([passed, unevaluated])]))

    const passedGroup = await screen.findByRole('heading', { name: /^Passed/ })
      .then((heading) => heading.closest('section')!)
    expect(within(passedGroup).getByText('CHC @ STL')).toBeInTheDocument()
    // The same sentence is both the status and the humanised gate, so scope to the status line.
    const passedCard = within(passedGroup).getByText('CHC @ STL').closest('article')!
    expect(passedCard.querySelector('.sport-event-card__status')).toHaveTextContent(/did not meet the policy threshold/i)
    expect(within(passedGroup).queryByText('SD @ SF')).not.toBeInTheDocument()

    const watchingGroup = group('Watching')
    expect(within(watchingGroup).getByText('SD @ SF')).toBeInTheDocument()
    expect(within(watchingGroup).getByText(/Not yet evaluated/i)).toBeInTheDocument()
  })

  it('scenario 4: missing sportsbook evidence never renders as zero', async () => {
    const noMarket: NormalizedSelection = {
      ...baseSelection,
      event_id: 'mlb-nomarket', event_label: 'MIN @ CLE',
      price_american: null, sportsbook: null, no_vig_probability: null, adjusted_edge: null,
      outstanding_gates: ['NO_TWO_SIDED_QUOTE'],
    }
    mount(model([mlbSection([noMarket])]))

    const card = (await screen.findByText('MIN @ CLE')).closest('article')!
    expect(within(card).getByText(/Waiting for a complete two-sided sportsbook quote/i)).toBeInTheDocument()
    expect(within(card).getAllByText('Not available').length).toBeGreaterThanOrEqual(2)
    expect(within(card).queryByText('0%')).not.toBeInTheDocument()
    expect(within(card).queryByText('+0.0%')).not.toBeInTheDocument()
  })

  it('scenario 5: a settled position is reported as final, not ready', async () => {
    const settled: NormalizedSelection = {
      ...baseSelection,
      event_id: 'mlb-settled', event_label: 'HOU @ TEX',
      decision_state: 'FINAL_QUALIFIED', funding_state: 'OFFICIAL_VIRTUAL_POSITION',
      official_position_id: 'position-9', virtual_stake: 20,
      position_status: 'SETTLED', position_result: 'WIN', event_status: 'FINAL', outstanding_gates: [],
    }
    mount(model([mlbSection([settled])]))

    const closedGroup = await screen.findByRole('heading', { name: /^Closed for decisions/ })
      .then((heading) => heading.closest('section')!)
    expect(within(closedGroup).getByText('HOU @ TEX')).toBeInTheDocument()
    expect(within(closedGroup).getByText('Final — virtual position settled, won')).toBeInTheDocument()
    expect(within(group('Ready')).queryByText('HOU @ TEX')).not.toBeInTheDocument()
  })

  it('scenario 6: an NFL research-shadow entry is never presented as fundable', async () => {
    mount(payload)

    const watching = await screen.findByRole('heading', { name: /^Watching/ })
      .then((heading) => heading.closest('section')!)
    const card = within(watching).getByText('DAL @ PHI').closest('article')!
    expect(within(card).getByText(/Research shadow — no funded decision is possible/i)).toBeInTheDocument()
    // No stake: research shadow can never fund a position.
    expect(stake(card)).toHaveTextContent('No position')
    expect(within(group('Ready')).queryByText('DAL @ PHI')).not.toBeInTheDocument()
    expect(screen.getByText(/No governed NFL market observation/i)).toBeInTheDocument()
  })

  it('scenario 7: a completely empty slate is stated honestly', async () => {
    mount(model([mlbSection([]), nflSection([])]))

    expect(await screen.findByText(/No governed slate is persisted for this date\./)).toBeInTheDocument()
    expect(screen.getByText(/A zero-position day is valid/i)).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: /@/ })).not.toBeInTheDocument()
  })

  it('scenario 8: an unavailable backend substitutes nothing', async () => {
    vi.spyOn(endpoints, 'today').mockRejectedValue(new Error('backend offline'))
    vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
    render(
      <MemoryRouter>
        <SportRegistryProvider>
          <MultiSportTodayPage />
        </SportRegistryProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/The daily command center is unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/No historical or illustrative result was substituted/i)).toBeInTheDocument()
  })
})

describe('Daily Command Center — preserved governance guarantees', () => {
  it('keeps ranking within a sport and shows no cross-sport score', async () => {
    mount(payload)
    expect(await screen.findByText(/ranked only within MLB/i)).toBeInTheDocument()
    expect(screen.getByText(/ranked only within NFL/i)).toBeInTheDocument()
    expect(screen.queryByText(/opportunity score/i)).not.toBeInTheDocument()
    expect(hasNoCrossSportRanking(payload)).toBe(true)
  })

  it('states that the read made no provider calls', async () => {
    mount(payload)
    expect(await screen.findByText(/Provider calls made by this read: 0/i)).toBeInTheDocument()
  })

  it('summarises the slate once instead of repeating a warning on every card', async () => {
    const watching = Array.from({ length: 4 }, (_, index) => ({
      ...baseSelection, event_id: `mlb-${index}`, event_label: `Game ${index}`,
    }))
    mount(model([mlbSection(watching)]))
    await screen.findByText('Game 0')
    expect(screen.queryAllByText(/AWAITING FINAL EVIDENCE/)).toHaveLength(0)
  })

  it('keeps official-position and final-qualified counts semantically distinct', async () => {
    const sections = [
      { ...mlbSection([baseSelection]), official_position_count: 1, final_qualified_decision_count: 2, final_qualified_count: 99 },
      nflSection([nflSelection]),
    ]
    mount(model(sections))

    const mlbCard = (await screen.findByRole('heading', { name: 'Major League Baseball' })).closest('article')!
    expect(metric(mlbCard, 'Official positions')).toHaveTextContent('1')
    expect(metric(mlbCard, 'Final qualified decisions')).toHaveTextContent('2')
    const nflCard = screen.getByRole('heading', { name: 'National Football League' }).closest('article')!
    expect(metric(nflCard, 'Official positions')).toHaveTextContent('0')
  })

  it('fails the command center closed when registry authority is unavailable', async () => {
    mount(payload, false)

    expect(await screen.findByText(/authoritative sport registry is unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/closed rather than inventing active sports/i)).toBeInTheDocument()
    expect(screen.queryByText('BOS @ NYY')).not.toBeInTheDocument()
    expect(screen.queryByText('DAL @ PHI')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Major League Baseball' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Planned sports' })).not.toBeInTheDocument()
  })

  it('renders one card per governed policy without duplicate React keys', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const v1 = { ...baseSelection, experiment_id: 'experiment-v1', experiment_name: 'V1 strict control', policy_version: 'AISP_V1' }
    const v21 = { ...baseSelection, experiment_id: 'experiment-v2-1', experiment_name: 'V2.1 challenger', policy_version: 'AISP_V2_1' }
    mount(model([mlbSection([v1, v21])]))

    await screen.findAllByText('BOS @ NYY')
    const leagueHeading = document.querySelector('.decision-group__league h3 span')!
    expect(leagueHeading).toHaveTextContent('2 decisions · 1 game · ranked only within MLB')
    expect(screen.getAllByText('BOS @ NYY')).toHaveLength(2)
    expect(screen.getByText(/break down 2 governed decisions, not 1 game/i)).toBeInTheDocument()
    expect(consoleError.mock.calls.flat().join(' ')).not.toMatch(/same key|encountered two children/i)
  })

  it('shows a within-sport rank only for an evaluated, qualified game', async () => {
    const ready: NormalizedSelection = {
      ...baseSelection,
      event_id: 'mlb-ready', event_label: 'TOR @ BAL', sport_rank: 1,
      decision_state: 'FINAL_QUALIFIED', funding_state: 'OFFICIAL_VIRTUAL_POSITION',
      official_position_id: 'position-1', virtual_stake: 12, outstanding_gates: [],
    }
    const watching = { ...baseSelection, sport_rank: 2 }
    mount(model([mlbSection([ready, watching])]))

    const readyCard = (await screen.findByText('TOR @ BAL')).closest('article')!
    expect(within(readyCard).getByText(/#1 in MLB/)).toBeInTheDocument()
    const watchingCard = screen.getByText('BOS @ NYY').closest('article')!
    expect(within(watchingCard).queryByText(/#2 in MLB/)).not.toBeInTheDocument()
  })
})

describe('Daily Command Center — count units and in-progress games', () => {
  it('never presents four counts that look like a partition of the games', async () => {
    const twoPolicies = [
      { ...baseSelection, event_id: 'g1', policy_version: 'V1' },
      { ...baseSelection, event_id: 'g1', policy_version: 'V2' },
      { ...baseSelection, event_id: 'g2', policy_version: 'V1' },
    ]
    mount(model([mlbSection(twoPolicies)]))
    await screen.findAllByText('BOS @ NYY')

    // The tile row is labelled as a breakdown of decisions, and the scope line
    // states games and decisions separately.
    const tiles = screen.getByRole('group', { name: /Breakdown of 3 governed decisions/i })
    const tileTotal = [...tiles.querySelectorAll('.summary-count__value')]
      .reduce((sum, node) => sum + Number(node.textContent), 0)
    expect(tileTotal).toBe(3)
    expect(within(tiles).queryByText('Games')).not.toBeInTheDocument()
    // The <strong> counts split the sentence into several text nodes, so assert
    // on the element itself rather than on a single text node.
    expect(document.querySelector('.daily-summary__scope'))
      .toHaveTextContent('2 games today · 3 governed decisions')
  })

  it('states decisions and distinct games per group', async () => {
    const twoPolicies = [
      { ...baseSelection, event_id: 'g1', policy_version: 'V1' },
      { ...baseSelection, event_id: 'g1', policy_version: 'V2' },
    ]
    mount(model([mlbSection(twoPolicies)]))
    await screen.findAllByText('BOS @ NYY')
    expect(screen.getByText(/2 decisions across 1 event\./)).toBeInTheDocument()
  })

  it('moves a live game out of Ready and Watching into Closed for decisions', async () => {
    const liveReady: NormalizedSelection = {
      ...baseSelection,
      event_id: 'live-1', event_label: 'BOS @ NYY',
      decision_state: 'FINAL_QUALIFIED', funding_state: 'OFFICIAL_VIRTUAL_POSITION',
      official_position_id: 'pos-live', virtual_stake: 15, event_status: 'LIVE',
      outstanding_gates: [],
    }
    const liveWatching: NormalizedSelection = {
      ...baseSelection,
      event_id: 'live-2', event_label: 'TB @ TOR', event_status: 'LIVE',
    }
    mount(model([mlbSection([liveReady, liveWatching])]))

    const closed = await screen.findByRole('heading', { name: /^Closed for decisions/ })
      .then((heading) => heading.closest('section')!)
    expect(within(closed).getByText('BOS @ NYY')).toBeInTheDocument()
    expect(within(closed).getByText('TB @ TOR')).toBeInTheDocument()
    expect(within(closed).getAllByText('In progress — pregame decision window closed')).toHaveLength(2)

    expect(within(group('Ready')).queryByText('BOS @ NYY')).not.toBeInTheDocument()
    expect(within(group('Watching')).queryByText('TB @ TOR')).not.toBeInTheDocument()
    expect(within(group('Watching')).queryByText(/Starting lineup/i)).not.toBeInTheDocument()
  })

  it('keeps postponed and canceled games out of "final" and "settled" language', async () => {
    const postponed: NormalizedSelection = {
      ...baseSelection, event_id: 'pp', event_label: 'MIA @ NYM',
      event_status: 'POSTPONED', official_position_id: 'pos-pp', outstanding_gates: [],
    }
    const canceled: NormalizedSelection = {
      ...baseSelection, event_id: 'cc', event_label: 'SD @ SF',
      event_status: 'CANCELED', outstanding_gates: [],
    }
    mount(model([mlbSection([postponed, canceled])]))

    const closed = await screen.findByRole('heading', { name: /^Closed for decisions/ })
      .then((heading) => heading.closest('section')!)
    const postponedCard = within(closed).getByText('MIA @ NYM').closest('article')!
    expect(postponedCard.querySelector('.sport-event-card__status-line')).toHaveTextContent('Postponed')
    expect(postponedCard.querySelector('.sport-event-card__status-line')).not.toHaveTextContent(/final|settled/i)

    const canceledCard = within(closed).getByText('SD @ SF').closest('article')!
    expect(canceledCard.querySelector('.sport-event-card__status-line')).toHaveTextContent('Canceled')
  })

  it('distinguishes a final game with a position from one without', async () => {
    const withPosition: NormalizedSelection = {
      ...baseSelection, event_id: 'f1', event_label: 'HOU @ TEX',
      event_status: 'FINAL', official_position_id: 'pos-f1', virtual_stake: 18, outstanding_gates: [],
    }
    const withoutPosition: NormalizedSelection = {
      ...baseSelection, event_id: 'f2', event_label: 'CHC @ STL',
      event_status: 'FINAL', official_position_id: null, outstanding_gates: [],
    }
    mount(model([mlbSection([withPosition, withoutPosition])]))

    await screen.findByText('HOU @ TEX')
    expect(screen.getByText('Final — settlement pending')).toBeInTheDocument()
    expect(screen.getByText('Final — no position was taken')).toBeInTheDocument()
  })
})

describe('All Sports homepage — the redesigned public experience', () => {
  it('leads with the product, its value and its research-only boundary', async () => {
    mount(payload)

    const heading = await screen.findByRole('heading', { level: 1, name: 'AI Sports Predictor' })
    expect(heading).toBeInTheDocument()
    expect(screen.getByText(/Model forecasts, market prices and governed decisions/)).toBeInTheDocument()
    expect(screen.getByText('Research and simulation only · No real-money wagering')).toBeInTheDocument()
    // Date and freshness are both stated, so the reader knows how current this is.
    expect(document.querySelector('.command-header__date')).toHaveTextContent('America/New_York')
    expect(document.querySelector('.command-header__fresh')).toHaveTextContent(/Forecasts refreshed/i)
    // Internal governance vocabulary is not the headline.
    expect(heading.textContent).not.toMatch(/governed|command center|persisted/i)
  })

  it('answers the day in one plain sentence above the counts', async () => {
    mount(payload)
    const answer = await screen.findByText(/No qualified decisions yet/i)
    expect(answer).toHaveClass('today-answer__message')
    const counts = screen.getByRole('group', { name: /Breakdown of 2 governed decisions/i })
    // The answer precedes the tiles in document order.
    expect(answer.compareDocumentPosition(counts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('keeps the four decision groups summing to the governed-decision total', async () => {
    mount(payload)
    await screen.findAllByText('BOS @ NYY')

    const tiles = screen.getByRole('group', { name: /Breakdown of (\d+) governed decisions/i })
    const total = [...tiles.querySelectorAll('.summary-count__value')]
      .reduce((sum, node) => sum + Number(node.textContent), 0)
    const stated = Number(/Breakdown of (\d+)/.exec(tiles.getAttribute('aria-label')!)![1])
    expect(total).toBe(stated)
    // And the group headings account for the same decisions.
    const headingTotal = ['Ready', 'Watching', 'Passed', 'Closed for decisions']
      .map((title) => screen.getByRole('heading', { name: new RegExp(`^${title}`) }))
      .reduce((sum, heading) => sum + Number(heading.querySelector('.decision-group__count')!.textContent), 0)
    expect(headingTotal).toBe(stated)
  })

  it('shows every league with its parent sport and truthful status', async () => {
    mount(payload)
    await screen.findByRole('heading', { name: 'Sports and leagues' })

    const strip = screen.getByRole('heading', { name: 'Sports and leagues' }).closest('section')!
    const tiles = [...strip.querySelectorAll('.league-tile')]
    expect(tiles.map((tile) => tile.querySelector('.league-tile__code')!.textContent!.trim()))
      .toEqual(['⚾ MLB', '🏈 NFL', '🏀 NBA', '🏀 WNBA', '🏒 NHL', '🥊 UFC'])

    const byCode = (code: string) => tiles.find((tile) => tile.textContent!.includes(code))!
    expect(byCode('MLB')).toHaveTextContent('Active forecasting')
    expect(byCode('MLB')).toHaveTextContent('Baseball')
    expect(byCode('NFL')).toHaveTextContent('Research foundation')
    expect(byCode('WNBA')).toHaveTextContent('Basketball')
    for (const planned of ['NBA', 'WNBA', 'NHL', 'UFC']) {
      expect(byCode(planned)).toHaveTextContent('Planned')
      expect(byCode(planned).querySelector('a')).toBeNull()
      expect(within(byCode(planned) as HTMLElement).getByText('Not available yet')).toBeInTheDocument()
    }
  })

  it('states the real slate size on a supported league and no counts on an unsupported one', async () => {
    mount(payload)
    await screen.findByRole('heading', { name: 'Sports and leagues' })

    const mlb = screen.getByRole('heading', { name: 'Major League Baseball' }).closest('article')!
    expect(mlb.querySelector('.league-tile__counts')).toHaveTextContent('1 scheduled game · 1 governed decision')
    // NHL is not in the backend registry at all, so it claims nothing.
    const nhl = screen.getByRole('heading', { name: 'National Hockey League' }).closest('article')!
    expect(nhl.querySelector('.league-tile__counts')).toHaveTextContent(/not in the sport registry/i)
    expect(nhl.querySelector('.league-tile__metrics')).toBeNull()
  })

  it('never lets a planned league become a link in the header nav', async () => {
    mount(payload)
    await screen.findByRole('navigation', { name: 'Sports and leagues' })
    const nav = screen.getByRole('navigation', { name: 'Sports and leagues' })
    expect(within(nav).getAllByRole('link').map((link) => link.textContent!.trim()))
      .toEqual(['⚾ MLB', '🏈 NFL'])
    expect(nav.querySelectorAll('[aria-disabled="true"]')).toHaveLength(4)
  })

  it('offers the rest of the platform without implying a lab that does not exist', async () => {
    mount(payload)
    await screen.findByRole('heading', { name: /Beyond today’s slate/i })

    for (const name of ['Market board', 'Crowd & market pulse', 'Model performance', '$1,000 Experiment']) {
      expect(screen.getByRole('link', { name: new RegExp(name.replace('$', '\\$'), 'i') })).toBeInTheDocument()
    }
    const labs = document.querySelector('.explore__labs')!
    expect(within(labs as HTMLElement).getByText(/MLB Diamond Simulator/)).toBeInTheDocument()
    expect(labs.querySelectorAll('.lab--available')).toHaveLength(1)
    // Planned labs are named honestly and carry no route.
    expect(labs.querySelectorAll('.lab--planned')).toHaveLength(5)
    expect(labs.querySelectorAll('a')).toHaveLength(0)
  })

  it('states how the record is kept honest without becoming a governance report', async () => {
    mount(payload)
    await screen.findByRole('heading', { name: /How this record is kept honest/i })
    expect(screen.getByText(/Decisions are recorded before the event/i)).toBeInTheDocument()
    expect(screen.getByText(/Kalshi and Polymarket are comparisons only/i)).toBeInTheDocument()
    expect(screen.getByText(/uses virtual research positions/i)).toBeInTheDocument()
    expect(document.querySelectorAll('.trust__grid li')).toHaveLength(6)
  })

  it('offers the MLB Diamond Simulator from an MLB decision card', async () => {
    mount(model([mlbSection([baseSelection])]))
    const card = (await screen.findByText('BOS @ NYY')).closest('article')!
    expect(within(card).getByRole('link', { name: /Diamond Simulator/i }))
      .toHaveAttribute('href', '/games/mlb-1/simulation')
    expect(within(card).getByRole('link', { name: /Full analysis/i }))
      .toHaveAttribute('href', '/games/mlb-1')
  })

  it('uses each sport’s own vocabulary on its cards rather than baseball’s', async () => {
    mount(payload)
    await screen.findByText('DAL @ PHI')
    const mlbCard = screen.getByText('BOS @ NYY').closest('article')!
    const nflCard = screen.getByText('DAL @ PHI').closest('article')!
    expect(within(mlbCard).getByText(/First pitch:/)).toBeInTheDocument()
    expect(within(nflCard).getByText(/Kickoff:/)).toBeInTheDocument()
    expect(within(nflCard).queryByText(/First pitch/)).not.toBeInTheDocument()
    // Market vocabulary follows the sport too.
    expect(within(mlbCard).getAllByText('Moneyline').length).toBeGreaterThanOrEqual(1)
  })

  it('keeps technical evidence behind a disclosure instead of in the summary', async () => {
    mount(model([mlbSection([baseSelection])]))
    const card = (await screen.findByText('BOS @ NYY')).closest('article')!
    const evidence = card.querySelector('.sport-event-card__evidence')!
    expect(evidence.tagName).toBe('DETAILS')
    expect(evidence).not.toHaveAttribute('open')
    expect(within(evidence as HTMLElement).getByText('AWAITING_FINAL_EVIDENCE')).toBeInTheDocument()
    // The raw state token is not part of the visible summary.
    expect(card.querySelector('.sport-event-card__status-line'))
      .not.toHaveTextContent('AWAITING_FINAL_EVIDENCE')
  })
})

describe('a model forecast survives a missing sportsbook quote', () => {
  const withCurrentForecast = {
    ...baseSelection,
    // The decision never got a priceable quote, so nothing was frozen into it.
    model_probability: null,
    no_vig_probability: null,
    price_american: null,
    adjusted_edge: null,
    sportsbook: null,
    // ...but the model's own current forecast is persisted and published.
    current_forecast_probability: 61.0,
    current_forecast_generated_at: '2026-08-31T15:30:00Z',
    current_forecast_model: 'asp_mlb_ensemble 2.0.0',
  }

  it('shows the current forecast with its own time and model identity', async () => {
    mount(model([mlbSection([withCurrentForecast])]))
    const card = (await screen.findByText('BOS @ NYY')).closest('article')!

    expect(within(card).getByText('61.0%')).toBeInTheDocument()
    expect(within(card).getByText(/asp_mlb_ensemble 2\.0\.0/)).toBeInTheDocument()
    // The decision-frozen value stays honestly absent, and the two are labelled
    // as different facts rather than one being substituted for the other.
    const decision = Array.from(card.querySelectorAll('dt'))
      .find((node) => node.textContent === 'AISP forecast')!.parentElement!
    expect(decision).toHaveTextContent('Not available')
    expect(within(card).getByText(/not the forecast frozen into this decision/i))
      .toBeInTheDocument()
  })

  it('never renders a missing market as a zero price or a zero edge', async () => {
    mount(model([mlbSection([withCurrentForecast])]))
    const card = (await screen.findByText('BOS @ NYY')).closest('article')!
    expect(within(card).queryByText('0.0%')).not.toBeInTheDocument()
    expect(within(card).queryByText('+0.0%')).not.toBeInTheDocument()
    const edge = Array.from(card.querySelectorAll('dt'))
      .find((node) => node.textContent === 'Edge')!.parentElement!
    expect(edge).toHaveTextContent('Not available')
  })

  it('shows no current-forecast row when the decision already froze one', async () => {
    mount(model([mlbSection([{ ...withCurrentForecast, model_probability: 56 }])]))
    const card = (await screen.findByText('BOS @ NYY')).closest('article')!
    expect(within(card).getByText('56.0%')).toBeInTheDocument()
    expect(within(card).queryByText(/Current model forecast/i)).not.toBeInTheDocument()
  })
})


function metric(container: HTMLElement, label: string): HTMLElement {
  const node = Array.from(container.querySelectorAll('dt')).find((item) => item.textContent === label)
  if (!node?.parentElement) throw new Error(`Metric not found: ${label}`)
  return node.parentElement
}

function stake(card: HTMLElement): HTMLElement {
  const node = Array.from(card.querySelectorAll('dt')).find((item) => item.textContent === 'Virtual stake')
  if (!node?.parentElement) throw new Error('Virtual stake not found')
  return node.parentElement
}
