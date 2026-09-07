/**
 * Sport and league experience registry — presentation and navigation only.
 *
 * HIERARCHY
 * ---------
 * A sport (BASEBALL, BASKETBALL, MMA…) owns terminology, participant model,
 * market families and the lab family. A league or promotion (MLB, WNBA, UFC…)
 * belongs to a sport and owns only its own name, accent, status and route.
 * UFC is a promotion within MMA, not a sport; NBA and WNBA are two leagues of
 * one sport and therefore cannot drift apart in vocabulary.
 *
 * WHAT THIS IS NOT
 * ----------------
 * It is NOT forecasting or market authority. Availability always comes from the
 * backend SPORT_REGISTRY_V1 response; `plannedStatus` here is only the fallback
 * used when the backend has no entry for a league at all. A league can never be
 * shown as active because this file says so — see `resolveSport`.
 *
 * Nothing here fabricates a forecast, price, record or model status.
 */
import type { SportAvailabilityState, SportRegistryEntry } from '../types/multisport'

export type ExperienceLeagueCode = 'MLB' | 'NFL' | 'NBA' | 'WNBA' | 'NHL' | 'UFC'

/** The sport itself. A league or promotion belongs to one of these. */
export type SportCode = 'BASEBALL' | 'FOOTBALL' | 'BASKETBALL' | 'HOCKEY' | 'MMA'

/**
 * How participants relate. MMA is not a home/away team game, and generic
 * contracts must not assume it is.
 */
export type ParticipantModel = 'TEAM_HOME_AWAY' | 'INDIVIDUAL_BOUT'

/**
 * Normalised, user-facing market vocabulary. Each sport maps these onto its own
 * label; the family is what shared code reasons about.
 */
export type MarketFamily = 'WINNER' | 'HANDICAP' | 'TOTAL' | 'METHOD' | 'ROUND' | 'PROPS'

export interface SportTerminology {
  /** "game", "bout" — never hardcode "game" in shared code. */
  event: string
  eventPlural: string
  participant: string
  participantPlural: string
  scoreUnit: string
  scoreUnitPlural: string
  /** "First pitch", "Kickoff", "Tip-off", "Puck drop", "Walkout". */
  startLabel: string
  /** Headline individual where one exists: "Starting pitcher", "Starting goalie". */
  keyRoleLabel: string | null
}

export interface InteractiveLab {
  key: string
  /** Always league-prefixed, so no lab reads as AISP's universal format. */
  name: string
  status: 'AVAILABLE' | 'PLANNED'
  routeTemplate: string | null
  description: string
}

/** Everything shared by every league of one sport. */
export interface SportDefinition {
  sportCode: SportCode
  displayName: string
  icon: string
  participantModel: ParticipantModel
  terminology: SportTerminology
  marketFamilies: MarketFamily[]
  marketLabels: Partial<Record<MarketFamily, string>>
  labFamily: { key: string; name: string; description: string }
}

export interface SportExperience {
  leagueCode: ExperienceLeagueCode
  /** The sport this league belongs to — never the league itself. */
  sportCode: SportCode
  /** "Baseball", "Basketball" — shown beside the league on the league strip. */
  sportName: string
  displayName: string
  shortName: string
  icon: string
  accent: { base: string; soft: string; ink: string }
  participantModel: ParticipantModel
  terminology: SportTerminology
  marketFamilies: MarketFamily[]
  marketLabels: Partial<Record<MarketFamily, string>>
  forecastOutputs: string[]
  lab: InteractiveLab | null
  methodologyRoute: string
  /** Fallback only; the backend decides whenever it has an entry. */
  plannedStatus: SportAvailabilityState
  emptyStateGuidance: string
}

const TEAM_TERMS = (
  event: string, scoreUnit: string, startLabel: string, keyRoleLabel: string | null,
): SportTerminology => ({
  event,
  eventPlural: `${event}s`,
  participant: 'team',
  participantPlural: 'teams',
  scoreUnit,
  scoreUnitPlural: `${scoreUnit}s`,
  startLabel,
  keyRoleLabel,
})

/** The five sports. Leagues inherit all of this. */
export const SPORTS: Record<SportCode, SportDefinition> = {
  BASEBALL: {
    sportCode: 'BASEBALL', displayName: 'Baseball', icon: '⚾',
    participantModel: 'TEAM_HOME_AWAY',
    terminology: TEAM_TERMS('game', 'run', 'First pitch', 'Starting pitcher'),
    marketFamilies: ['WINNER', 'HANDICAP', 'TOTAL'],
    marketLabels: { WINNER: 'Moneyline', HANDICAP: 'Run line', TOTAL: 'Total runs' },
    labFamily: {
      key: 'diamond', name: 'Diamond Simulator',
      description:
        'Explore the full population of simulated final scores behind a forecast, and test '
        + 'hypothetical run-environment assumptions against the official forecast.',
    },
  },
  FOOTBALL: {
    sportCode: 'FOOTBALL', displayName: 'Football', icon: '🏈',
    participantModel: 'TEAM_HOME_AWAY',
    terminology: TEAM_TERMS('game', 'point', 'Kickoff', 'Quarterback'),
    marketFamilies: ['WINNER', 'HANDICAP', 'TOTAL'],
    marketLabels: { WINNER: 'Moneyline', HANDICAP: 'Point spread', TOTAL: 'Total points' },
    labFamily: {
      key: 'gridiron', name: 'Gridiron Lab',
      description: 'Planned: drive-level and scoring-path exploration once a governed engine exists.',
    },
  },
  BASKETBALL: {
    sportCode: 'BASKETBALL', displayName: 'Basketball', icon: '🏀',
    participantModel: 'TEAM_HOME_AWAY',
    terminology: TEAM_TERMS('game', 'point', 'Tip-off', 'Expected starters'),
    marketFamilies: ['WINNER', 'HANDICAP', 'TOTAL'],
    marketLabels: { WINNER: 'Moneyline', HANDICAP: 'Point spread', TOTAL: 'Total points' },
    labFamily: {
      key: 'court', name: 'Court Lab',
      description: 'Planned: possession-level exploration once a governed engine exists.',
    },
  },
  HOCKEY: {
    sportCode: 'HOCKEY', displayName: 'Hockey', icon: '🏒',
    participantModel: 'TEAM_HOME_AWAY',
    terminology: TEAM_TERMS('game', 'goal', 'Puck drop', 'Starting goalie'),
    marketFamilies: ['WINNER', 'HANDICAP', 'TOTAL'],
    marketLabels: { WINNER: 'Moneyline', HANDICAP: 'Puck line', TOTAL: 'Total goals' },
    labFamily: {
      key: 'ice', name: 'Ice Lab',
      description: 'Planned: shot- and goal-path exploration once a governed engine exists.',
    },
  },
  MMA: {
    sportCode: 'MMA', displayName: 'Mixed Martial Arts', icon: '🥊',
    participantModel: 'INDIVIDUAL_BOUT',
    terminology: {
      event: 'bout', eventPlural: 'bouts',
      participant: 'fighter', participantPlural: 'fighters',
      scoreUnit: 'round', scoreUnitPlural: 'rounds',
      startLabel: 'Walkout', keyRoleLabel: 'Weight class',
    },
    marketFamilies: ['WINNER', 'METHOD', 'ROUND'],
    marketLabels: { WINNER: 'Fight winner', METHOD: 'Method of victory', ROUND: 'Round' },
    labFamily: {
      key: 'fight', name: 'Fight Lab',
      description:
        'Planned: round-by-round and method-of-victory exploration once a governed engine exists.',
    },
  },
}

interface LeagueSeed {
  leagueCode: ExperienceLeagueCode
  sportCode: SportCode
  displayName: string
  shortName: string
  accent: { base: string; soft: string; ink: string }
  forecastOutputs: string[]
  labAvailable: boolean
  labRoute: string | null
  plannedStatus: SportAvailabilityState
  emptyStateGuidance: string
}

const LEAGUE_SEEDS: LeagueSeed[] = [
  {
    leagueCode: 'MLB', sportCode: 'BASEBALL',
    displayName: 'Major League Baseball', shortName: 'MLB',
    accent: { base: '#087f7a', soft: '#e5f4f1', ink: '#17553f' },
    forecastOutputs: [
      'Win probability', 'Expected runs', 'Final-score distribution', 'Run line', 'Total runs',
    ],
    labAvailable: true, labRoute: '/games/:eventId/simulation',
    plannedStatus: 'LIVE',
    emptyStateGuidance: 'No MLB games are on this operational date.',
  },
  {
    leagueCode: 'NFL', sportCode: 'FOOTBALL',
    displayName: 'National Football League', shortName: 'NFL',
    accent: { base: '#286f9e', soft: '#e8f2f9', ink: '#1f5271' },
    forecastOutputs: ['Research win probability', 'Expected points', 'Point spread', 'Total points'],
    labAvailable: false, labRoute: null,
    plannedStatus: 'RESEARCH_SHADOW',
    emptyStateGuidance:
      'NFL runs as an unfunded research foundation. Forecasts are research output and never '
      + 'create a position.',
  },
  {
    leagueCode: 'NBA', sportCode: 'BASKETBALL',
    displayName: 'National Basketball Association', shortName: 'NBA',
    accent: { base: '#d36d2b', soft: '#fff1e5', ink: '#8a4415' },
    forecastOutputs: [], labAvailable: false, labRoute: null, plannedStatus: 'PLANNED',
    emptyStateGuidance: 'NBA is planned. No NBA model, market or forecast exists in AISP yet.',
  },
  {
    leagueCode: 'WNBA', sportCode: 'BASKETBALL',
    displayName: 'Women’s National Basketball Association', shortName: 'WNBA',
    accent: { base: '#a2568f', soft: '#f8ecf5', ink: '#6d2f5d' },
    forecastOutputs: [], labAvailable: false, labRoute: null, plannedStatus: 'PLANNED',
    emptyStateGuidance: 'WNBA is planned. No WNBA model, market or forecast exists in AISP yet.',
  },
  {
    leagueCode: 'NHL', sportCode: 'HOCKEY',
    displayName: 'National Hockey League', shortName: 'NHL',
    accent: { base: '#4a6fa5', soft: '#eaf0f8', ink: '#2c456b' },
    forecastOutputs: [], labAvailable: false, labRoute: null, plannedStatus: 'PLANNED',
    emptyStateGuidance:
      'NHL is planned. It is not yet in the backend sport registry, so AISP holds no NHL '
      + 'model, market or forecast authority at all.',
  },
  {
    leagueCode: 'UFC', sportCode: 'MMA',
    displayName: 'Ultimate Fighting Championship', shortName: 'UFC',
    accent: { base: '#b6454d', soft: '#fdebed', ink: '#7d2c33' },
    forecastOutputs: [], labAvailable: false, labRoute: null, plannedStatus: 'PLANNED',
    emptyStateGuidance: 'UFC is planned. No MMA model, market or forecast exists in AISP yet.',
  },
]

function composeLeague(seed: LeagueSeed): SportExperience {
  const sport = SPORTS[seed.sportCode]
  return {
    leagueCode: seed.leagueCode,
    sportCode: seed.sportCode,
    sportName: sport.displayName,
    displayName: seed.displayName,
    shortName: seed.shortName,
    icon: sport.icon,
    accent: seed.accent,
    participantModel: sport.participantModel,
    terminology: sport.terminology,
    marketFamilies: sport.marketFamilies,
    marketLabels: sport.marketLabels,
    forecastOutputs: seed.forecastOutputs,
    lab: {
      key: `${seed.leagueCode.toLowerCase()}-${sport.labFamily.key}`,
      name: `${seed.shortName} Interactive Lab — ${sport.labFamily.name}`,
      status: seed.labAvailable ? 'AVAILABLE' : 'PLANNED',
      routeTemplate: seed.labRoute,
      description: sport.labFamily.description,
    },
    methodologyRoute: '/methodology',
    plannedStatus: seed.plannedStatus,
    emptyStateGuidance: seed.emptyStateGuidance,
  }
}

export const SPORT_EXPERIENCES: Record<ExperienceLeagueCode, SportExperience> =
  Object.fromEntries(
    LEAGUE_SEEDS.map((seed) => [seed.leagueCode, composeLeague(seed)]),
  ) as Record<ExperienceLeagueCode, SportExperience>

export const SPORT_ORDER: ExperienceLeagueCode[] = ['MLB', 'NFL', 'NBA', 'WNBA', 'NHL', 'UFC']

/** Leagues grouped under their sport, for sport-first navigation. */
export function leaguesBySport(): Array<{ sport: SportDefinition; leagues: SportExperience[] }> {
  const order: SportCode[] = ['BASEBALL', 'FOOTBALL', 'BASKETBALL', 'HOCKEY', 'MMA']
  return order.map((code) => ({
    sport: SPORTS[code],
    leagues: LEAGUE_SEEDS
      .filter((seed) => seed.sportCode === code)
      .map((seed) => SPORT_EXPERIENCES[seed.leagueCode]),
  }))
}

/** Public availability wording, kept out of internal governance vocabulary. */
export const AVAILABILITY_LABEL: Record<SportAvailabilityState, string> = {
  LIVE: 'Active forecasting',
  RESEARCH_SHADOW: 'Research foundation',
  DEVELOPMENT: 'In development',
  PLANNED: 'Planned',
  UNAVAILABLE: 'Unavailable',
}

/** One-line public description of what each status means for the reader. */
export const AVAILABILITY_BLURB: Record<SportAvailabilityState, string> = {
  LIVE: 'Governed forecasts, markets and recorded decisions.',
  RESEARCH_SHADOW: 'Research forecasts only — never a funded position.',
  DEVELOPMENT: 'Being built; not published yet.',
  PLANNED: 'On the roadmap. No model or market yet.',
  UNAVAILABLE: 'Currently unavailable.',
}

export interface ResolvedSport extends SportExperience {
  availability: SportAvailabilityState
  availabilityLabel: string
  availabilityBlurb: string
  navigable: boolean
  backedByRegistry: boolean
  statusExplanation: string
  officialPositionsSupported: boolean
  engineLabel: string | null
  navigationHref: string | null
}

function explain(
  availability: SportAvailabilityState,
  experience: SportExperience,
  backed: boolean,
): string {
  if (!backed) {
    return `${experience.displayName} is not yet registered with AISP’s sport authority, `
      + 'so no forecasts, markets or results exist for it.'
  }
  switch (availability) {
    case 'LIVE':
      return `${experience.displayName} is active: governed forecasts, markets and results are available.`
    case 'RESEARCH_SHADOW':
      return `${experience.displayName} runs as a research foundation. Forecasts are research `
        + 'output only and never create a funded position.'
    case 'DEVELOPMENT':
      return `${experience.displayName} is in development and not yet published.`
    case 'PLANNED':
      return `${experience.displayName} is planned. No model, market or forecast exists yet.`
    default:
      return `${experience.displayName} is currently unavailable.`
  }
}

/**
 * Merges backend registry authority over the presentation entry.
 * Backend availability always wins; an unbacked league can never be navigable.
 */
export function resolveSport(
  league: ExperienceLeagueCode,
  entries: SportRegistryEntry[] | null | undefined,
): ResolvedSport {
  const experience = SPORT_EXPERIENCES[league]
  const entry = (entries ?? []).find((item) => item.league_code === league)
  const backed = Boolean(entry)
  const effective: SportAvailabilityState = backed
    ? entry!.availability
    : (experience.plannedStatus === 'LIVE' ? 'PLANNED' : experience.plannedStatus)
  return {
    ...experience,
    availability: effective,
    availabilityLabel: AVAILABILITY_LABEL[effective] ?? 'Unavailable',
    availabilityBlurb: AVAILABILITY_BLURB[effective] ?? '',
    navigable: Boolean(entry?.navigable),
    backedByRegistry: backed,
    statusExplanation: explain(effective, experience, backed),
    officialPositionsSupported: Boolean(entry?.official_positions_supported),
    engineLabel: entry?.engine_label ?? null,
    navigationHref: entry?.navigation_href ?? null,
  }
}

export function resolveAllSports(entries: SportRegistryEntry[] | null | undefined): ResolvedSport[] {
  return SPORT_ORDER.map((league) => resolveSport(league, entries))
}

export function navigableSports(entries: SportRegistryEntry[] | null | undefined): ResolvedSport[] {
  return resolveAllSports(entries).filter((sport) => sport.navigable)
}

/** Whether a league may display forecast content at all. */
export function canShowForecasts(sport: ResolvedSport): boolean {
  return sport.backedByRegistry
    && (sport.availability === 'LIVE' || sport.availability === 'RESEARCH_SHADOW')
}

/** Whether a league may display an interactive lab today. */
export function labIsAvailable(sport: ResolvedSport): boolean {
  return sport.lab?.status === 'AVAILABLE' && canShowForecasts(sport)
}

export function marketLabel(sport: ResolvedSport, family: MarketFamily): string | null {
  return sport.marketLabels[family] ?? null
}

export function term(sport: ResolvedSport, key: keyof SportTerminology): string {
  return String(sport.terminology[key] ?? '')
}
