import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { SportEventCard, SportSlot, type SportEvent } from './SportEventCard'
import { resolveSport } from '../services/sportExperience'
import type { SportRegistryEntry } from '../types/multisport'

const BACKEND: SportRegistryEntry[] = [
  {
    sport_code: 'BASEBALL', league_code: 'MLB', participant_type: 'TEAM', availability: 'LIVE',
    display_name: 'MLB', short_label: 'MLB', official_positions_supported: true,
    official_positions_note: '', engine_label: 'AISP_BASELINE',
    stored_sport_aliases: [], stored_league_aliases: [], navigable: true, navigation_href: '/mlb',
  },
  {
    sport_code: 'FOOTBALL', league_code: 'NFL', participant_type: 'TEAM', availability: 'RESEARCH_SHADOW',
    display_name: 'NFL', short_label: 'NFL', official_positions_supported: false,
    official_positions_note: '', engine_label: 'MODEL_STUDIO_NFL_M1',
    stored_sport_aliases: [], stored_league_aliases: [], navigable: true, navigation_href: '/shadow-board',
  },
  {
    sport_code: 'MMA', league_code: 'UFC', participant_type: 'INDIVIDUAL', availability: 'PLANNED',
    display_name: 'UFC', short_label: 'UFC', official_positions_supported: false,
    official_positions_note: '', engine_label: null,
    stored_sport_aliases: [], stored_league_aliases: [], navigable: false, navigation_href: null,
  },
]

function event(overrides: Partial<SportEvent> = {}): SportEvent {
  return {
    eventId: 'e1', leagueCode: 'MLB',
    participants: [{ name: 'BOS' }, { name: 'BAL' }],
    startsAt: '2026-09-07T23:05:00Z', statusLabel: 'Scheduled',
    forecastProbability: 52.1, marketProbability: 49.4,
    marketFamily: 'WINNER', readinessLabel: 'Watching', readinessTone: 'watching',
    evidenceFreshness: 'Updated 12m ago', detailHref: '/games/e1',
    ...overrides,
  }
}

function mount(node: React.ReactNode) {
  return render(<MemoryRouter>{node}</MemoryRouter>)
}

afterEach(cleanup)

describe('MLB event', () => {
  const mlb = resolveSport('MLB', BACKEND)

  it('renders MLB fields with baseball terminology', () => {
    mount(
      <SportEventCard sport={mlb} event={event()}>
        <SportSlot sport={mlb} details={{ keyPlayers: ['B. Bello', 'K. Bradish'] }} />
      </SportEventCard>,
    )
    expect(screen.getByText('BOS @ BAL')).toBeInTheDocument()
    expect(screen.getByText(/First pitch:/)).toBeInTheDocument()
    expect(screen.getAllByText('Starting pitcher')).toHaveLength(2)
    expect(screen.getByText('B. Bello')).toBeInTheDocument()
    expect(screen.getByText('Moneyline')).toBeInTheDocument()
  })

  it('links to full analysis', () => {
    mount(<SportEventCard sport={mlb} event={event()} />)
    expect(screen.getByRole('link', { name: /full analysis/i })).toHaveAttribute('href', '/games/e1')
  })

  it('names the handicap market Run line for MLB', () => {
    mount(<SportEventCard sport={mlb} event={event({ marketFamily: 'HANDICAP' })} />)
    expect(screen.getByText('Run line')).toBeInTheDocument()
  })
})

describe('NFL research-shadow event', () => {
  const nfl = resolveSport('NFL', BACKEND)

  it('uses football terminology, not baseball', () => {
    mount(
      <SportEventCard sport={nfl} event={event({ leagueCode: 'NFL', participants: [{ name: 'DAL' }, { name: 'PHI' }] })}>
        <SportSlot sport={nfl} details={{ keyPlayers: ['D. Prescott', 'J. Hurts'] }} />
      </SportEventCard>,
    )
    expect(screen.getByText(/Kickoff:/)).toBeInTheDocument()
    expect(screen.getAllByText('Quarterback')).toHaveLength(2)
    expect(screen.queryByText(/First pitch/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Starting pitcher/)).not.toBeInTheDocument()
  })

  it('still shows research forecasts because the backend allows them', () => {
    mount(<SportEventCard sport={nfl} event={event({ leagueCode: 'NFL' })} />)
    expect(screen.getByText('52.1%')).toBeInTheDocument()
    expect(screen.getByText('AISP forecast')).toBeInTheDocument()
    expect(screen.queryByText(/is planned/i)).not.toBeInTheDocument()
  })

  it('names the handicap market Point spread for NFL', () => {
    mount(<SportEventCard sport={nfl} event={event({ marketFamily: 'HANDICAP' })} />)
    expect(screen.getByText('Point spread')).toBeInTheDocument()
  })
})

describe('MMA bout', () => {
  const ufc = resolveSport('UFC', BACKEND)

  it('does not require home/away semantics', () => {
    mount(
      <SportEventCard
        sport={ufc}
        event={event({
          leagueCode: 'UFC',
          participants: [{ name: 'A. Volkanovski' }, { name: 'I. Topuria' }],
          forecastProbability: null, marketProbability: null,
        })}
      >
        <SportSlot sport={ufc} details={{ weightClass: 'Featherweight', boutOrder: 'Main event' }} />
      </SportEventCard>,
    )
    expect(screen.getByText('A. Volkanovski vs I. Topuria')).toBeInTheDocument()
    expect(screen.queryByText(/@/)).not.toBeInTheDocument()
    expect(screen.getByText(/Walkout:/)).toBeInTheDocument()
    expect(screen.getByText('Featherweight')).toBeInTheDocument()
    expect(screen.getByText('Main event')).toBeInTheDocument()
  })

  it('never shows a forecast for a planned sport', () => {
    mount(
      <SportEventCard
        sport={ufc}
        event={event({ leagueCode: 'UFC', forecastProbability: 71.4, marketProbability: 66.0 })}
      />,
    )
    // Even when a caller passes numbers, a planned sport cannot display them.
    expect(screen.queryByText('71.4%')).not.toBeInTheDocument()
    expect(screen.queryByText('66.0%')).not.toBeInTheDocument()
    expect(screen.getByText(/Ultimate Fighting Championship is planned/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /full analysis/i })).not.toBeInTheDocument()
  })
})

describe('missing evidence', () => {
  const mlb = resolveSport('MLB', BACKEND)

  it('renders a missing market probability as unavailable, never zero', () => {
    mount(<SportEventCard sport={mlb} event={event({ marketProbability: null })} />)
    expect(screen.getByText('Not available')).toBeInTheDocument()
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument()
  })

  it('renders a missing forecast as unavailable, never zero', () => {
    mount(<SportEventCard sport={mlb} event={event({ forecastProbability: null, marketProbability: null })} />)
    expect(screen.getAllByText('Not available').length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument()
  })

  it('states when a key player has not been announced', () => {
    mount(
      <SportEventCard sport={mlb} event={event()}>
        <SportSlot sport={mlb} details={{ keyPlayers: [null, 'K. Bradish'] }} />
      </SportEventCard>,
    )
    expect(screen.getByText('Not announced')).toBeInTheDocument()
  })

  it('states when evidence freshness is unrecorded', () => {
    mount(<SportEventCard sport={mlb} event={event({ evidenceFreshness: null })} />)
    expect(screen.getByText('Not recorded')).toBeInTheDocument()
  })
})

describe('readiness semantics are preserved', () => {
  const mlb = resolveSport('MLB', BACKEND)

  it.each(['ready', 'watching', 'passed', 'closed'] as const)('renders the %s decision group', (tone) => {
    const labels = { ready: 'Ready', watching: 'Watching', passed: 'Passed', closed: 'Closed for decisions' }
    const { container } = mount(
      <SportEventCard sport={mlb} event={event({ readinessTone: tone, readinessLabel: labels[tone] })} />,
    )
    expect(screen.getByText(labels[tone])).toBeInTheDocument()
    expect(container.querySelector(`.sport-event-card--${tone}`)).toBeTruthy()
  })
})
