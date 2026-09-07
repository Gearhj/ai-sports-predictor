import { describe, expect, it } from 'vitest'
import {
  AVAILABILITY_LABEL,
  SPORT_EXPERIENCES,
  SPORT_ORDER,
  canShowForecasts,
  labIsAvailable,
  marketLabel,
  navigableSports,
  resolveAllSports,
  resolveSport,
} from './sportExperience'
import type { SportRegistryEntry } from '../types/multisport'

/** Mirrors the real backend SPORT_REGISTRY_V1 payload. NHL is absent, as it is today. */
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
    official_positions_note: 'Research shadow only.', engine_label: 'MODEL_STUDIO_NFL_M1',
    stored_sport_aliases: [], stored_league_aliases: [], navigable: true, navigation_href: '/shadow-board',
  },
  {
    sport_code: 'BASKETBALL', league_code: 'NBA', participant_type: 'TEAM', availability: 'PLANNED',
    display_name: 'NBA', short_label: 'NBA', official_positions_supported: false,
    official_positions_note: '', engine_label: null,
    stored_sport_aliases: [], stored_league_aliases: [], navigable: false, navigation_href: null,
  },
  {
    sport_code: 'BASKETBALL', league_code: 'WNBA', participant_type: 'TEAM', availability: 'PLANNED',
    display_name: 'WNBA', short_label: 'WNBA', official_positions_supported: false,
    official_positions_note: '', engine_label: null,
    stored_sport_aliases: [], stored_league_aliases: [], navigable: false, navigation_href: null,
  },
  {
    sport_code: 'MMA', league_code: 'UFC', participant_type: 'INDIVIDUAL', availability: 'PLANNED',
    display_name: 'UFC', short_label: 'UFC', official_positions_supported: false,
    official_positions_note: '', engine_label: null,
    stored_sport_aliases: [], stored_league_aliases: [], navigable: false, navigation_href: null,
  },
]

describe('backend authority always wins', () => {
  it('marks MLB active only because the backend says LIVE', () => {
    const mlb = resolveSport('MLB', BACKEND)
    expect(mlb.availability).toBe('LIVE')
    expect(mlb.availabilityLabel).toBe('Active')
    expect(mlb.navigable).toBe(true)
    expect(mlb.officialPositionsSupported).toBe(true)
  })

  it('downgrades MLB when the backend no longer says it is live', () => {
    const downgraded = resolveSport('MLB', [{ ...BACKEND[0], availability: 'PLANNED', navigable: false }])
    expect(downgraded.availability).toBe('PLANNED')
    expect(downgraded.navigable).toBe(false)
    expect(canShowForecasts(downgraded)).toBe(false)
  })

  it('never promotes a sport the backend does not list', () => {
    // NHL has no backend entry at all today.
    const nhl = resolveSport('NHL', BACKEND)
    expect(nhl.backedByRegistry).toBe(false)
    expect(nhl.availability).toBe('PLANNED')
    expect(nhl.navigable).toBe(false)
    expect(canShowForecasts(nhl)).toBe(false)
    expect(nhl.statusExplanation).toMatch(/not yet registered/i)
  })

  it('cannot show a sport as active from presentation data alone', () => {
    // Even MLB, whose fallback is LIVE, is planned when the backend is silent.
    const withoutBackend = resolveSport('MLB', [])
    expect(withoutBackend.backedByRegistry).toBe(false)
    expect(withoutBackend.availability).toBe('PLANNED')
    expect(canShowForecasts(withoutBackend)).toBe(false)
  })

  it('treats a missing registry response as no authority', () => {
    for (const sport of resolveAllSports(null)) {
      expect(sport.navigable).toBe(false)
      expect(canShowForecasts(sport)).toBe(false)
    }
  })
})

describe('availability states are honest', () => {
  it('describes NFL as a research foundation that cannot fund a position', () => {
    const nfl = resolveSport('NFL', BACKEND)
    expect(nfl.availability).toBe('RESEARCH_SHADOW')
    expect(nfl.availabilityLabel).toBe('Research foundation')
    expect(nfl.officialPositionsSupported).toBe(false)
    expect(nfl.statusExplanation).toMatch(/never create a funded position/i)
    expect(canShowForecasts(nfl)).toBe(true)
  })

  it('keeps every planned sport free of forecast authority', () => {
    for (const league of ['NBA', 'WNBA', 'NHL', 'UFC'] as const) {
      const sport = resolveSport(league, BACKEND)
      expect(sport.availability).toBe('PLANNED')
      expect(canShowForecasts(sport)).toBe(false)
      expect(sport.forecastOutputs).toEqual([])
      expect(sport.engineLabel).toBeNull()
    }
  })

  it('uses public wording rather than internal governance vocabulary', () => {
    expect(AVAILABILITY_LABEL.LIVE).toBe('Active')
    expect(AVAILABILITY_LABEL.RESEARCH_SHADOW).toBe('Research foundation')
    expect(AVAILABILITY_LABEL.PLANNED).toBe('Planned')
  })

  it('lists only navigable sports for navigation', () => {
    expect(navigableSports(BACKEND).map((s) => s.leagueCode)).toEqual(['MLB', 'NFL'])
  })
})

describe('sport terminology is not baseball', () => {
  it('gives each sport its own event, score and start vocabulary', () => {
    expect(SPORT_EXPERIENCES.MLB.terminology.startLabel).toBe('First pitch')
    expect(SPORT_EXPERIENCES.NFL.terminology.startLabel).toBe('Kickoff')
    expect(SPORT_EXPERIENCES.NBA.terminology.startLabel).toBe('Tip-off')
    expect(SPORT_EXPERIENCES.NHL.terminology.startLabel).toBe('Puck drop')
    expect(SPORT_EXPERIENCES.UFC.terminology.startLabel).toBe('Walkout')
    expect(SPORT_EXPERIENCES.NHL.terminology.scoreUnit).toBe('goal')
    expect(SPORT_EXPERIENCES.NFL.terminology.scoreUnit).toBe('point')
  })

  it('does not model an MMA bout as a home/away team game', () => {
    const ufc = SPORT_EXPERIENCES.UFC
    expect(ufc.participantModel).toBe('INDIVIDUAL_BOUT')
    expect(ufc.terminology.event).toBe('bout')
    expect(ufc.terminology.participant).toBe('fighter')
    expect(ufc.terminology.participant).not.toBe('team')
  })

  it('keeps every team sport on the team participant model', () => {
    for (const league of ['MLB', 'NFL', 'NBA', 'WNBA', 'NHL'] as const) {
      expect(SPORT_EXPERIENCES[league].participantModel).toBe('TEAM_HOME_AWAY')
    }
  })
})

describe('market vocabulary', () => {
  it('maps one normalised family onto each sport’s own label', () => {
    const labelFor = (league: Parameters<typeof resolveSport>[0], family: 'HANDICAP' | 'TOTAL') =>
      marketLabel(resolveSport(league, BACKEND), family)
    expect(labelFor('MLB', 'HANDICAP')).toBe('Run line')
    expect(labelFor('NFL', 'HANDICAP')).toBe('Point spread')
    expect(labelFor('NHL', 'HANDICAP')).toBe('Puck line')
    expect(labelFor('MLB', 'TOTAL')).toBe('Total runs')
    expect(labelFor('NBA', 'TOTAL')).toBe('Total points')
    expect(labelFor('NHL', 'TOTAL')).toBe('Total goals')
  })

  it('gives MMA fight-specific families and no handicap', () => {
    const ufc = resolveSport('UFC', BACKEND)
    expect(ufc.marketFamilies).toContain('METHOD')
    expect(ufc.marketFamilies).toContain('ROUND')
    expect(ufc.marketFamilies).not.toContain('HANDICAP')
    expect(marketLabel(ufc, 'WINNER')).toBe('Fight winner')
    expect(marketLabel(ufc, 'HANDICAP')).toBeNull()
  })
})

describe('interactive labs', () => {
  it('keeps the Diamond Simulator MLB-specific and not AISP-universal', () => {
    const mlb = resolveSport('MLB', BACKEND)
    expect(mlb.lab?.name).toBe('MLB Interactive Lab — Diamond Simulator')
    expect(mlb.lab?.status).toBe('AVAILABLE')
    expect(labIsAvailable(mlb)).toBe(true)
    // No other sport claims a diamond.
    for (const league of ['NFL', 'NBA', 'WNBA', 'NHL', 'UFC'] as const) {
      expect(SPORT_EXPERIENCES[league].lab?.name).not.toMatch(/diamond/i)
    }
  })

  it('marks every other lab planned with no route', () => {
    for (const league of ['NFL', 'NBA', 'WNBA', 'NHL', 'UFC'] as const) {
      const lab = SPORT_EXPERIENCES[league].lab!
      expect(lab.status).toBe('PLANNED')
      expect(lab.routeTemplate).toBeNull()
      expect(lab.description).toMatch(/planned/i)
      expect(labIsAvailable(resolveSport(league, BACKEND))).toBe(false)
    }
  })

  it('names every lab after its league', () => {
    for (const league of SPORT_ORDER) {
      expect(SPORT_EXPERIENCES[league].lab?.name.startsWith(league)).toBe(true)
    }
  })
})

describe('extension boundary', () => {
  it('covers every ordered sport with a complete entry', () => {
    expect(SPORT_ORDER).toHaveLength(6)
    for (const league of SPORT_ORDER) {
      const entry = SPORT_EXPERIENCES[league]
      expect(entry.leagueCode).toBe(league)
      expect(entry.displayName.length).toBeGreaterThan(2)
      expect(entry.marketFamilies.length).toBeGreaterThan(0)
      expect(entry.emptyStateGuidance.length).toBeGreaterThan(10)
      expect(entry.methodologyRoute).toMatch(/^\//)
    }
  })

  it('adds a sport through the registry alone', () => {
    // Every consumer reads from SPORT_ORDER + SPORT_EXPERIENCES, so a new entry
    // flows into navigation and resolution without touching page components.
    const resolved = resolveAllSports(BACKEND)
    expect(resolved.map((s) => s.leagueCode)).toEqual(SPORT_ORDER)
  })
})
