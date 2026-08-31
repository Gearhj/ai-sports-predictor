import type { SportRegistryEntry, SportRegistryResponse } from '../types/multisport'

function entry(overrides: Partial<SportRegistryEntry> & Pick<SportRegistryEntry, 'sport_code' | 'league_code'>): SportRegistryEntry {
  return {
    participant_type: 'TEAM',
    availability: 'PLANNED',
    display_name: overrides.league_code,
    short_label: overrides.league_code,
    official_positions_supported: false,
    official_positions_note: 'Test registry entry.',
    engine_label: null,
    stored_sport_aliases: [overrides.sport_code],
    stored_league_aliases: [overrides.league_code],
    navigable: false,
    navigation_href: null,
    ...overrides,
  }
}

export const sportRegistryFixture: SportRegistryResponse = {
  version: 'SPORT_REGISTRY_V1',
  sports: [
    entry({
      sport_code: 'BASEBALL',
      league_code: 'MLB',
      availability: 'LIVE',
      official_positions_supported: true,
      engine_label: 'AISP_BASELINE',
      navigable: true,
      navigation_href: '/mlb',
    }),
    entry({
      sport_code: 'FOOTBALL',
      league_code: 'NFL',
      availability: 'RESEARCH_SHADOW',
      engine_label: 'MODEL_STUDIO_NFL_M1',
      navigable: true,
      navigation_href: '/shadow-board?sport=NFL',
    }),
    entry({ sport_code: 'BASKETBALL', league_code: 'NBA' }),
    entry({ sport_code: 'BASKETBALL', league_code: 'WNBA' }),
    entry({ sport_code: 'MMA', league_code: 'UFC', participant_type: 'INDIVIDUAL' }),
  ],
  supported_filters: ['ALL', 'MLB', 'NFL'],
}
