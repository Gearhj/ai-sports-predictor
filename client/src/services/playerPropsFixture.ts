/**
 * TEST FIXTURE — not live sportsbook data.
 *
 * A provider-shaped player-prop sample (mirrors
 * server/tests/fixtures/the_odds_api/event_odds_player_props.json after
 * identity resolution) used only by the `?preview=player-props` interface
 * preview and by tests. Nothing here is ever selectable or stored.
 */
import type { PlayerProps, PlayerPropQuote } from './probability'

const base = {
  game_id: 'fixture-game', mlb_game_ref: null, provider: 'The Odds API', provider_event_id: 'fixture-event',
  observed_at: '2026-09-20T18:00:00+00:00', ingested_at: '2026-09-20T18:02:00+00:00', age_minutes: 0,
  fresh: false, state: 'STORED_REFERENCE_ONLY', market_status: 'OPEN', identity_status: 'RESOLVED',
  selectable: false, sportsbook: 'DraftKings', sportsbook_key: 'draftkings',
} as const

const q = (over: Partial<PlayerPropQuote> & Pick<PlayerPropQuote, 'quote_id' | 'player_id' | 'player_name' | 'team' | 'market_key' | 'market_label' | 'outcome' | 'provider_outcome' | 'line' | 'description' | 'american' | 'decimal'>): PlayerPropQuote => ({
  ...base, provider_player_name: over.player_name, team_id: `team-${over.team}`, identity_evidence: 'ROSTER', ...over,
})

export const PLAYER_PROPS_FIXTURE: PlayerProps = {
  contract: 'AISP_PLAYER_PROP_QUOTE_V1',
  required_identity: [],
  status: 'AVAILABLE',
  assessment: 'Sportsbook market — no AISP assessment',
  message: 'Test fixture — not live sportsbook data.',
  quarantined: { PLAYER_AMBIGUOUS: 1, PLAYER_WRONG_TEAM: 1, PLAYER_UNKNOWN: 1 },
  quotes: [
    q({ quote_id: 'f1', player_id: 'p-judge', player_name: 'Aaron Judge', team: 'New York Yankees', market_key: 'batter_hits', market_label: 'Hits', outcome: 'OVER', provider_outcome: 'Over', line: 0.5, description: '1+ hit', american: -185, decimal: 1.540541 }),
    q({ quote_id: 'f2', player_id: 'p-judge', player_name: 'Aaron Judge', team: 'New York Yankees', market_key: 'batter_hits', market_label: 'Hits', outcome: 'UNDER', provider_outcome: 'Under', line: 0.5, description: 'No hit', american: 145, decimal: 2.45 }),
    q({ quote_id: 'f3', player_id: 'p-judge', player_name: 'Aaron Judge', team: 'New York Yankees', market_key: 'batter_home_runs', market_label: 'Home runs', outcome: 'YES', provider_outcome: 'Yes', line: null, description: '1+ home run', american: 240, decimal: 3.4, sportsbook: 'FanDuel', sportsbook_key: 'fanduel', observed_at: '2026-09-20T18:01:00+00:00' }),
    q({ quote_id: 'f4', player_id: 'p-devers', player_name: 'Rafael Devers', team: 'Boston Red Sox', market_key: 'batter_hits', market_label: 'Hits', outcome: 'OVER', provider_outcome: 'Over', line: 1.5, description: '2+ hits', american: 120, decimal: 2.2, identity_evidence: 'LINEUP' }),
    q({ quote_id: 'f5', player_id: 'p-cole', player_name: 'Gerrit Cole', team: 'New York Yankees', market_key: 'pitcher_strikeouts', market_label: 'Strikeouts', outcome: 'OVER', provider_outcome: 'Over', line: 6.5, description: '7+ strikeouts', american: -115, decimal: 1.869565, identity_evidence: 'PROBABLE_PITCHER' }),
    q({ quote_id: 'f6', player_id: 'p-cole', player_name: 'Gerrit Cole', team: 'New York Yankees', market_key: 'pitcher_strikeouts', market_label: 'Strikeouts', outcome: 'UNDER', provider_outcome: 'Under', line: 6.5, description: 'Under 6.5 strikeouts', american: -105, decimal: 1.952381, identity_evidence: 'PROBABLE_PITCHER' }),
  ],
}
