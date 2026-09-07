/**
 * Sport experience registry — presentation and navigation authority only.
 *
 * WHAT THIS IS
 * ------------
 * One typed place describing how each sport is *presented*: naming, iconography,
 * accent, terminology, which market families are meaningful, which pages exist,
 * and which interactive lab belongs to it. Adding a sport should mean adding an
 * entry here plus a presentation adapter — not editing the shell or every page.
 *
 * WHAT THIS IS NOT
 * ----------------
 * It is NOT forecasting or market authority. Availability always comes from the
 * backend SPORT_REGISTRY_V1 response; this file's `plannedStatus` is only the
 * fallback used when the backend has no entry for a league at all. A sport can
 * never be shown as active because this file says so. See `resolveSport`.
 *
 * Nothing here fabricates a forecast, price, record or model status.
 */
import type { SportAvailabilityState, SportRegistryEntry } from '../types/multisport'

export type ExperienceLeagueCode = 'MLB' | 'NFL' | 'NBA' | 'WNBA' | 'NHL' | 'UFC'

/**
 * How participants relate in a sport. MMA is not a home/away team game, and
 * generic contracts must not assume it is.
 */
export type ParticipantModel = 'TEAM_HOME_AWAY' | 'INDIVIDUAL_BOUT'

/**
 * Normalised, user-facing market vocabulary. Each sport maps these to its own
 * label; the family is what shared code reasons about.
 */
export type MarketFamily =
  | 'WINNER'          // moneyline / fight winner
  | 'HANDICAP'        // run line / point spread / puck line
  | 'TOTAL'           // total runs / points / goals
  | 'METHOD'          // MMA method of victory
  | 'ROUND'           // MMA round
  | 'PROPS'

export interface SportTerminology {
  /** "game", "match", "bout" — never hardcode "game" in shared code. */
  event: string
  eventPlural: string
  /** "team", "fighter". */
  participant: string
  participantPlural: string
  /** "run", "point", "goal". */
  scoreUnit: string
  scoreUnitPlural: string
  /** "First pitch", "Kickoff", "Tip-off", "Puck drop", "Walkout". */
  startLabel: string
  /** The headline individual, where one exists: "Starting pitcher", "Goalie". */
  keyRoleLabel: string | null
}

export interface InteractiveLab {
  key: string
  /** Always prefixed by league so no lab reads as AISP's universal format. */
  name: string
  status: 'AVAILABLE' | 'PLANNED'
  /** Route when AVAILABLE; null while planned. */
  routeTemplate: string | null
  description: string
}

export interface SportExperience {
  leagueCode: ExperienceLeagueCode
  sportCode: string
  displayName: string
  shortName: string
  /** Emoji marker; the shell renders it beside the short name. */
  icon: string
  accent: { base: string; soft: string; ink: string }
  participantModel: ParticipantModel
  terminology: SportTerminology
  marketFamilies: MarketFamily[]
  /** Market family → the label this sport actually uses for it. */
  marketLabels: Partial<Record<MarketFamily, string>>
  /** Forecast outputs this sport's model would publish, when it has one. */
  forecastOutputs: string[]
  lab: InteractiveLab | null
  methodologyRoute: string
  /**
   * Fallback only. The backend registry decides availability whenever it has an
   * entry; this is used when it has none (today: NHL).
   */
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

export const SPORT_EXPERIENCES: Record<ExperienceLeagueCode, SportExperience> = {
  MLB: {
    leagueCode: 'MLB', sportCode: 'BASEBALL',
    displayName: 'Major League Baseball', shortName: 'MLB', icon: '⚾',
    accent: { base: '#087f7a', soft: '#e5f4f1', ink: '#17553f' },
    participantModel: 'TEAM_HOME_AWAY',
    terminology: TEAM_TERMS('game', 'run', 'First pitch', 'Starting pitcher'),
    marketFamilies: ['WINNER', 'HANDICAP', 'TOTAL'],
    marketLabels: { WINNER: 'Moneyline', HANDICAP: 'Run line', TOTAL: 'Total runs' },
    forecastOutputs: [
      'Win probability', 'Expected runs', 'Final-score distribution',
      'Run line', 'Total runs',
    ],
    lab: {
      key: 'diamond', name: 'MLB Interactive Lab — Diamond Simulator',
      status: 'AVAILABLE', routeTemplate: '/games/:eventId/simulation',
      description:
        'Explore the full population of simulated final scores behind an MLB forecast, '
        + 'and test hypothetical run-environment assumptions against the official forecast.',
    },
    methodologyRoute: '/methodology',
    plannedStatus: 'LIVE',
    emptyStateGuidance: 'No MLB games are on this operational date.',
  },
  NFL: {
    leagueCode: 'NFL', sportCode: 'FOOTBALL',
    displayName: 'National Football League', shortName: 'NFL', icon: '🏈',
    accent: { base: '#286f9e', soft: '#e8f2f9', ink: '#1f5271' },
    participantModel: 'TEAM_HOME_AWAY',
    terminology: TEAM_TERMS('game', 'point', 'Kickoff', 'Quarterback'),
    marketFamilies: ['WINNER', 'HANDICAP', 'TOTAL'],
    marketLabels: { WINNER: 'Moneyline', HANDICAP: 'Point spread', TOTAL: 'Total points' },
    forecastOutputs: ['Research win probability', 'Expected points', 'Point spread', 'Total points'],
    lab: {
      key: 'gridiron', name: 'NFL Interactive Lab — Gridiron Simulator',
      status: 'PLANNED', routeTemplate: null,
      description:
        'Planned: drive-level and scoring-path exploration once a governed NFL '
        + 'simulation engine exists. No NFL simulator is available today.',
    },
    methodologyRoute: '/methodology',
    plannedStatus: 'RESEARCH_SHADOW',
    emptyStateGuidance:
      'NFL runs as an unfunded research shadow. Forecasts are research output and never '
      + 'create a position.',
  },
  NBA: {
    leagueCode: 'NBA', sportCode: 'BASKETBALL',
    displayName: 'National Basketball Association', shortName: 'NBA', icon: '🏀',
    accent: { base: '#d36d2b', soft: '#fff1e5', ink: '#8a4415' },
    participantModel: 'TEAM_HOME_AWAY',
    terminology: TEAM_TERMS('game', 'point', 'Tip-off', 'Expected starters'),
    marketFamilies: ['WINNER', 'HANDICAP', 'TOTAL'],
    marketLabels: { WINNER: 'Moneyline', HANDICAP: 'Point spread', TOTAL: 'Total points' },
    forecastOutputs: [],
    lab: {
      key: 'court', name: 'NBA Interactive Lab — Court Simulator',
      status: 'PLANNED', routeTemplate: null,
      description: 'Planned: possession-level exploration once a governed NBA engine exists.',
    },
    methodologyRoute: '/methodology',
    plannedStatus: 'PLANNED',
    emptyStateGuidance:
      'NBA is planned. No NBA model, market or forecast exists in AISP yet.',
  },
  WNBA: {
    leagueCode: 'WNBA', sportCode: 'BASKETBALL',
    displayName: "Women's National Basketball Association", shortName: 'WNBA', icon: '🏀',
    accent: { base: '#a2568f', soft: '#f8ecf5', ink: '#6d2f5d' },
    participantModel: 'TEAM_HOME_AWAY',
    terminology: TEAM_TERMS('game', 'point', 'Tip-off', 'Expected starters'),
    marketFamilies: ['WINNER', 'HANDICAP', 'TOTAL'],
    marketLabels: { WINNER: 'Moneyline', HANDICAP: 'Point spread', TOTAL: 'Total points' },
    forecastOutputs: [],
    lab: {
      key: 'court-wnba', name: 'WNBA Interactive Lab — Court Simulator',
      status: 'PLANNED', routeTemplate: null,
      description: 'Planned: possession-level exploration once a governed WNBA engine exists.',
    },
    methodologyRoute: '/methodology',
    plannedStatus: 'PLANNED',
    emptyStateGuidance:
      'WNBA is planned. No WNBA model, market or forecast exists in AISP yet.',
  },
  NHL: {
    leagueCode: 'NHL', sportCode: 'HOCKEY',
    displayName: 'National Hockey League', shortName: 'NHL', icon: '🏒',
    accent: { base: '#4a6fa5', soft: '#eaf0f8', ink: '#2c456b' },
    participantModel: 'TEAM_HOME_AWAY',
    terminology: TEAM_TERMS('game', 'goal', 'Puck drop', 'Starting goalie'),
    marketFamilies: ['WINNER', 'HANDICAP', 'TOTAL'],
    marketLabels: { WINNER: 'Moneyline', HANDICAP: 'Puck line', TOTAL: 'Total goals' },
    forecastOutputs: [],
    lab: {
      key: 'ice', name: 'NHL Interactive Lab — Ice Simulator',
      status: 'PLANNED', routeTemplate: null,
      description: 'Planned: shot- and goal-path exploration once a governed NHL engine exists.',
    },
    methodologyRoute: '/methodology',
    plannedStatus: 'PLANNED',
    emptyStateGuidance:
      'NHL is planned. It is not yet in the backend sport registry, so AISP holds no NHL '
      + 'model, market or forecast authority at all.',
  },
  UFC: {
    leagueCode: 'UFC', sportCode: 'MMA',
    displayName: 'Ultimate Fighting Championship', shortName: 'UFC', icon: '🥊',
    accent: { base: '#b6454d', soft: '#fdebed', ink: '#7d2c33' },
    // A bout has two fighters and no home/away side. Shared code must branch on
    // this, never assume a home team exists.
    participantModel: 'INDIVIDUAL_BOUT',
    terminology: {
      event: 'bout', eventPlural: 'bouts',
      participant: 'fighter', participantPlural: 'fighters',
      scoreUnit: 'round', scoreUnitPlural: 'rounds',
      startLabel: 'Walkout', keyRoleLabel: 'Weight class',
    },
    marketFamilies: ['WINNER', 'METHOD', 'ROUND'],
    marketLabels: { WINNER: 'Fight winner', METHOD: 'Method of victory', ROUND: 'Round' },
    forecastOutputs: [],
    lab: {
      key: 'fight', name: 'UFC Interactive Lab — Fight Simulator',
      status: 'PLANNED', routeTemplate: null,
      description:
        'Planned: round-by-round and method-of-victory exploration once a governed MMA '
        + 'engine exists.',
    },
    methodologyRoute: '/methodology',
    plannedStatus: 'PLANNED',
    emptyStateGuidance:
      'UFC is planned. No MMA model, market or forecast exists in AISP yet.',
  },
}

export const SPORT_ORDER: ExperienceLeagueCode[] = ['MLB', 'NFL', 'NBA', 'WNBA', 'NHL', 'UFC']

/** Public availability wording, kept out of internal governance vocabulary. */
export const AVAILABILITY_LABEL: Record<SportAvailabilityState, string> = {
  LIVE: 'Active',
  RESEARCH_SHADOW: 'Research foundation',
  DEVELOPMENT: 'In development',
  PLANNED: 'Planned',
  UNAVAILABLE: 'Unavailable',
}

export interface ResolvedSport extends SportExperience {
  /** Availability actually in force — backend wins whenever it has an entry. */
  availability: SportAvailabilityState
  availabilityLabel: string
  /** True only when the backend says this sport is navigable. */
  navigable: boolean
  /** True when the backend registry knows this league at all. */
  backedByRegistry: boolean
  statusExplanation: string
  /** Whether governed positions can ever exist for this sport. */
  officialPositionsSupported: boolean
  engineLabel: string | null
}

function explain(
  availability: SportAvailabilityState,
  experience: SportExperience,
  backed: boolean,
): string {
  if (!backed) {
    return `${experience.displayName} is not yet registered with AISP's sport authority, `
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
 *
 * Backend availability always wins. When the backend has no entry the sport is
 * reported with `backedByRegistry: false` and can never be navigable, so a
 * presentation-only sport cannot masquerade as supported.
 */
export function resolveSport(
  league: ExperienceLeagueCode,
  entries: SportRegistryEntry[] | null | undefined,
): ResolvedSport {
  const experience = SPORT_EXPERIENCES[league]
  const entry = (entries ?? []).find((item) => item.league_code === league)
  const backed = Boolean(entry)
  const availability = entry?.availability ?? experience.plannedStatus
  return {
    ...experience,
    availability: backed ? availability : (experience.plannedStatus === 'LIVE' ? 'PLANNED' : experience.plannedStatus),
    availabilityLabel: AVAILABILITY_LABEL[backed ? availability : experience.plannedStatus] ?? 'Unavailable',
    navigable: Boolean(entry?.navigable),
    backedByRegistry: backed,
    statusExplanation: explain(backed ? availability : experience.plannedStatus, experience, backed),
    officialPositionsSupported: Boolean(entry?.official_positions_supported),
    engineLabel: entry?.engine_label ?? null,
  }
}

/** Every registered sport in display order, resolved against backend authority. */
export function resolveAllSports(entries: SportRegistryEntry[] | null | undefined): ResolvedSport[] {
  return SPORT_ORDER.map((league) => resolveSport(league, entries))
}

/** Sports a user can actually open. Never includes a planned sport. */
export function navigableSports(entries: SportRegistryEntry[] | null | undefined): ResolvedSport[] {
  return resolveAllSports(entries).filter((sport) => sport.navigable)
}

/** Whether a sport may display forecast content at all. */
export function canShowForecasts(sport: ResolvedSport): boolean {
  return sport.backedByRegistry
    && (sport.availability === 'LIVE' || sport.availability === 'RESEARCH_SHADOW')
}

/** Whether a sport may display an interactive lab today. */
export function labIsAvailable(sport: ResolvedSport): boolean {
  return sport.lab?.status === 'AVAILABLE' && canShowForecasts(sport)
}

/** The label a sport uses for one normalised market family. */
export function marketLabel(sport: ResolvedSport, family: MarketFamily): string | null {
  return sport.marketLabels[family] ?? null
}

/** Terminology helper so shared code never hardcodes "game" or "team". */
export function term(sport: ResolvedSport, key: keyof SportTerminology): string {
  return String(sport.terminology[key] ?? '')
}
