/**
 * A Mission Control response (AISP_MISSION_CONTROL_V1) for client tests.
 *
 * Every clock is relative to `now`, so countdown assertions hold whenever the
 * suite runs. Between them the games cover every stage state the server can
 * send (DONE, WAITING, BLOCKED, NOT_APPLICABLE, UNKNOWN), a stage the server
 * left out, and a state string the client does not recognise.
 */
import type { MissionControl, MissionGame, Stage } from '../features/mission/missionData'

export const MISSION_DAY = '2026-10-17'

const iso = (now: number, minutes: number) => new Date(now + minutes * 60_000).toISOString()

const done = (label: string, detail: string, extra: Record<string, unknown> = {}): Stage => ({ state: 'DONE', code: 'OK', label, detail, ...extra })
const na = (label: string, detail: string): Stage => ({ state: 'NOT_APPLICABLE', code: 'NOT_APPLICABLE', label, detail })

export function missionGames(now: number): MissionGame[] {
  // NHL, before its windows: forecast and approval done, prices due, decision due in 12 min.
  const nhl: MissionGame = {
    game_id: 'g-nhl', sport: 'NHL', source_game_id: '2026020123', matchup: 'BOS @ TOR',
    away: { abbr: 'BOS', name: 'Boston Bruins' }, home: { abbr: 'TOR', name: 'Toronto Maple Leafs' },
    starts_at: iso(now, 42), status: 'scheduled', phase: 'PREGAME',
    clocks: { forecast_due_at: null, buy_opens_at: iso(now, -33), buy_closes_at: iso(now, 7), decision_opens_at: iso(now, 12), decision_closes_at: iso(now, 27) },
    stages: {
      forecast: { ...done('TOR 58%', 'AISP in-process NHL Gen1 forecast made this morning.'), source: 'AISP in-process NHL Gen1', made_at: iso(now, -600), made_at_basis: 'AISP_IN_PROCESS_GENERATED', freshness: 'FRESH', age_minutes: 600, home_win_prob: 0.58, away_win_prob: 0.42, favorite: { side: 'HOME', team: 'TOR', prob: 0.58 } },
      approval: { ...done('Eligible', 'In-process eligibility holds for this game.'), route: 'NHL_IN_PROCESS', eligible: true },
      prices: { state: 'WAITING', code: 'COLLECTION_SCHEDULED', label: 'Buys 7:45 PM', detail: 'The collector buys this game between 75 and 35 minutes before puck drop.', due_at: iso(now, 3), books: 0, fresh_books: 0, collector: 'ON' },
      decision: { state: 'WAITING', code: 'BEFORE_DECISION_WINDOW', label: 'Window 7:30 PM', detail: 'The decision window opens 30 minutes before puck drop.', due_at: iso(now, 12), class: 'PENDING' },
      bet: { state: 'WAITING', code: 'AWAITING_DECISION', label: 'After decision', detail: 'A bet is placed only if the decision is BET.' },
      live: na('No live feed', 'No NHL in-game feed; ESPN finals every 30 min.'),
      result: { state: 'WAITING', code: 'NOT_STARTED', label: 'After puck drop', detail: 'The result is read from ESPN after the game.' },
    },
    next_step: { stage: 'DECISION', kind: 'WAIT', at: iso(now, 12), label: 'Decision in', text: 'Decision window opens 7:30 PM ET' },
    attention: 'NONE',
  }

  // CFB, blocked at approval: the discovery lease expired.
  const cfb: MissionGame = {
    game_id: 'g-cfb', sport: 'CFB', source_game_id: '401628374', matchup: 'OSU @ MICH',
    away: { abbr: 'OSU', name: 'Ohio State' }, home: { abbr: 'MICH', name: 'Michigan' },
    starts_at: iso(now, 90), status: 'scheduled', phase: 'PREGAME',
    clocks: { decision_opens_at: iso(now, 60), decision_closes_at: iso(now, 75) },
    stages: {
      forecast: { ...done('MICH 61%', 'CFB Gen1 joint forecast published.'), source: 'CFB Gen1 joint', freshness: 'FRESH', home_win_prob: 0.61, away_win_prob: 0.39 },
      approval: { state: 'BLOCKED', code: 'EXPIRED_LEASE', label: 'Lease expired', detail: 'The discovery lease expired at 11:00 AM ET, so no paper decision can be made until it is renewed.', route: 'CFB_LEASE_AND_RECORD', eligible: false },
      prices: { state: 'DONE', code: 'PRICE_HELD', label: 'MICH -150', detail: 'A fresh two-sided price is held.', books: 6, fresh_books: 5, quote_age_minutes: 14, best: { home: { american: -150, sportsbook: 'DraftKings' }, away: { american: 135, sportsbook: 'FanDuel' } } },
      decision: { state: 'BLOCKED', code: 'NO_PAPER_RESEARCH_ELIGIBILITY_RECORD', label: 'Not approved', detail: 'No decision can be recorded without approval.', class: 'BLOCKED' },
      bet: na('No bet', 'No bet without a decision.'),
      live: na('No live feed', 'No live CFB score feed.'),
      result: { state: 'WAITING', code: 'NOT_STARTED', label: 'After the game', detail: 'The result is read after the game.' },
    },
    next_step: { stage: 'APPROVAL', kind: 'BLOCKED', at: null, label: 'Lease expired', text: 'The discovery lease expired at 11:00 AM ET, so no paper decision can be made until it is renewed.' },
    attention: 'BLOCKED',
  }

  // MLB, live with an open bet.
  const mlb: MissionGame = {
    game_id: 'g-mlb', sport: 'MLB', source_game_id: null, matchup: 'NYY @ BOS',
    away: { abbr: 'NYY', name: 'New York Yankees' }, home: { abbr: 'BOS', name: 'Boston Red Sox' },
    starts_at: iso(now, -95), status: 'live', phase: 'STARTED',
    clocks: { decision_opens_at: iso(now, -125), decision_closes_at: iso(now, -110) },
    stages: {
      forecast: { ...done('NYY 55%', 'Model Studio V18 published before T-45.'), source: 'Model Studio V18' },
      approval: done('Active policy', 'MLB has an active paper policy.'),
      prices: done('NYY +110', 'Nine books held a fresh price.'),
      decision: { ...done('BET NYY', 'Model edge cleared the threshold.'), class: 'BET', edge: 0.043, model_probability: 0.52, market_probability: 0.477 },
      bet: { ...done('NYY ML +110', 'Placed $5.00 to win $5.50.'), side: 'NYY ML', american: 110, stake_usd: 5, to_win_usd: 5.5, status: 'OPEN' },
      live: { state: 'DONE', code: 'LIVE', label: 'Bot 7th', detail: 'Live score from the scoreboard.', available: true, is_live: true, is_final: false, away_score: 3, home_score: 2, period_label: 'Bot 7th', freshness: 'FRESH' },
      result: { state: 'WAITING', code: 'IN_PROGRESS', label: 'In progress', detail: 'Settles after the final.' },
    },
    // The server's WATCH step: label "Live", the bet and score in the text.
    next_step: { stage: 'LIVE', kind: 'WATCH', at: null, label: 'Live', text: 'Sweating NYY ML · 3–2 · Bot 7th' },
    attention: 'NONE',
  }

  // NFL, final and won.
  const nfl: MissionGame = {
    game_id: 'g-nfl', sport: 'NFL', source_game_id: null, matchup: 'KC @ BUF',
    away: { abbr: 'KC', name: 'Kansas City Chiefs' }, home: { abbr: 'BUF', name: 'Buffalo Bills' },
    starts_at: iso(now, -300), status: 'final', phase: 'FINAL',
    stages: {
      forecast: done('BUF 60%', 'NFL M1 FINAL_PREGAME forecast.'),
      approval: done('Eligible', 'Producer eligibility held.'),
      prices: done('BUF -120', 'Fresh price held.'),
      decision: { ...done('BET BUF', 'Edge cleared.'), class: 'BET' },
      bet: { ...done('BUF ML -120', 'Placed.'), side: 'BUF ML', american: -120, stake_usd: 2.88, to_win_usd: 2.4, status: 'SETTLED' },
      live: { ...done('Final', 'Game over.'), available: true, is_live: false, is_final: true, away_score: 20, home_score: 27, period_label: 'Final' },
      result: { ...done('W +$2.40', 'Settled as a win.'), outcome: 'WIN', profit_usd: 2.4, final: { away: 20, home: 27, label: 'Final' } },
    },
    // The server's DONE step: no label, the result chip's text.
    next_step: { stage: 'DONE', kind: 'DONE', at: null, label: null, text: 'W +$2.40' },
    attention: 'NONE',
  }

  // NBA: an explicit UNKNOWN (a read failed), a state the client does not
  // know, and five stages the server never sent.
  const nba: MissionGame = {
    game_id: 'g-nba', sport: 'NBA', source_game_id: null, matchup: 'LAL @ BOS',
    away: { abbr: 'LAL', name: 'Los Angeles Lakers' }, home: { abbr: 'BOS', name: 'Boston Celtics' },
    starts_at: iso(now, 240), status: 'scheduled', phase: 'PREGAME',
    stages: {
      forecast: { state: 'UNKNOWN', code: 'READ_FAILED', label: 'Read failed', detail: 'The NBA forecast could not be read.' },
      approval: { state: 'SUCCESS' as never, code: 'ODD', label: 'Looks fine', detail: 'A state this client does not know.' },
    },
    next_step: null,
    attention: 'WATCH',
  }

  return [nhl, cfb, mlb, nfl, nba]
}

export function missionFixture(now: number = Date.now(), overrides: Partial<MissionControl> = {}): MissionControl {
  return {
    schema_version: 'AISP_MISSION_CONTROL_V1',
    date: MISSION_DAY, timezone: 'America/New_York', is_today: true,
    window: { local_date: MISSION_DAY, timezone: 'America/New_York', start_utc: '2026-10-17T04:00:00Z', end_utc: '2026-10-18T04:00:00Z', description: 'Eastern day' },
    generated_at: new Date(now - 20_000).toISOString(),
    authority: 'Virtual research only. No real-money wagering and no execution outside AISP.',
    provider_calls_made: 0,
    refresh: { client_poll_seconds: 30, server_fresh_seconds: 30, server_max_stale_seconds: 90 },
    health: {
      overall: 'DEGRADED', issues: ['The CFB decision job failed on its last run.'],
      scheduler: { state: 'RUNNING', started_at: iso(now, -600) },
      database: { state: 'OK' },
      jobs: [
        { key: 'nhl-decisions', job_name: 'evaluate-nhl-paper-checkpoint', scheduler_id: 'asp-dc-nhl-paper-checkpoint', label: 'Evaluate NHL paper checkpoint', sport: 'NHL', role: 'DECISION', registered: true, expected: true, state: 'OK', last_status: 'succeeded', last_started_at: iso(now, -3), last_success_at: iso(now, -3), next_run_at: iso(now, 2), stale_after_minutes: 20 },
        { key: 'nhl-forecasts', job_name: 'generate-nhl-forecasts', scheduler_id: 'asp-im-nhl-forecast-generation', label: 'Generate NHL forecasts', sport: 'NHL', role: 'FORECAST', registered: true, expected: true, state: 'RUNNING', last_status: 'running', last_started_at: iso(now, -1), last_success_at: iso(now, -400) },
        { key: 'cfb-decisions', job_name: 'evaluate-cfb-paper-checkpoint', scheduler_id: 'asp-dc-cfb-paper-checkpoint', label: 'Evaluate CFB paper checkpoint', sport: 'CFB', role: 'DECISION', registered: true, expected: true, state: 'FAILED', last_status: 'failed', last_started_at: iso(now, -4), last_success_at: iso(now, -90) },
        { key: 'settle', job_name: 'finalize-games', scheduler_id: 'asp-finalize-games', label: 'Finalize games', sport: 'ALL', role: 'RESULTS', registered: true, expected: true, state: 'OK', last_status: 'succeeded', last_success_at: iso(now, -10) },
        // A lane settings leave off: NOT_REGISTERED with expected false is not a fault.
        { key: 'nba-decisions', job_name: 'evaluate-nba-paper-checkpoint', scheduler_id: 'asp-nba-paper-checkpoint', label: 'Evaluate NBA paper checkpoint', sport: 'NBA', role: 'DECISION', registered: false, expected: false, state: 'NOT_REGISTERED', last_status: null, last_success_at: null, next_run_at: null, stale_after_minutes: 20 },
      ],
      producers: [
        { sport: 'MLB', name: 'Model Studio V18', state: 'OK', last_published_at: iso(now, -30), age_minutes: 30, detail: 'Receipt current.' },
        { sport: 'CFB', name: 'CFB Gen1 joint', state: 'EXPIRED', valid_until: iso(now, -60), detail: 'Grant expired.' },
        { sport: 'NHL', name: 'AISP in-process NHL Gen1', state: 'OK', last_published_at: iso(now, -400) },
      ],
    },
    allowance: {
      state: 'ROOM', in_force: true, effective_from: '2026-10-16T04:00:00Z', limit_usd: 100, entered_usd: 37.5, room_usd: 62.5,
      entries: 3, ambiguous_entries: 0, slate_date: MISSION_DAY, basis: 'ALL_OFFICIAL_SPORTS_ENTERED_ON_EASTERN_SLATE_DATE_ANY_STATUS',
    },
    bankrolls: [
      { sport: 'MLB', label: 'MLB', funded: true, starting_usd: 1000, bankroll_usd: 962.4, available_usd: 957.4, open_exposure_usd: 5, profit_usd: -37.6, effective_on: '2026-09-01', read_state: 'OK', error_type: null },
      { sport: 'NFL', label: 'NFL', funded: true, starting_usd: 1000, bankroll_usd: 1012.3, available_usd: 1012.3, open_exposure_usd: 0, profit_usd: 12.3, effective_on: '2026-09-04', read_state: 'OK', error_type: null },
      { sport: 'CFB', label: 'College Football', funded: true, starting_usd: 1000, bankroll_usd: 990, available_usd: 990, open_exposure_usd: 0, profit_usd: -10, effective_on: '2026-09-18', read_state: 'OK', error_type: null },
      { sport: 'NHL', label: 'NHL', funded: true, starting_usd: 1000, bankroll_usd: 1000, available_usd: 1000, open_exposure_usd: 0, profit_usd: 0, effective_on: '2026-10-16', read_state: 'OK', error_type: null },
      // NBA is deliberately absent: the strip must say "Not reported", never $0.
    ],
    sports: [
      { sport: 'MLB', label: 'MLB', games: 1, read_state: 'OK' },
      { sport: 'NFL', label: 'NFL', games: 1, read_state: 'OK' },
      { sport: 'CFB', label: 'College Football', games: 1, read_state: 'OK' },
      { sport: 'NHL', label: 'NHL', games: 1, read_state: 'OK', pending_games: [{ source_game_id: '2026020130', starts_at: iso(now, 1440), reason: 'REST_INCOMPLETE_BACK_TO_BACK' }] },
      { sport: 'NBA', label: 'NBA', games: 1, read_state: 'OK' },
    ],
    games: missionGames(now),
    sweating: [
      { position_id: 'p-mlb', game_id: 'g-mlb', sport: 'MLB', matchup: 'NYY @ BOS', selection: 'NYY ML', american: 110, sportsbook: 'FanDuel', stake_usd: 5, to_win_usd: 5.5, starts_at: iso(now, -95), slate_date: MISSION_DAY, placed_at: iso(now, -118), state: 'LIVE', live: { available: true, is_live: true, is_final: false, away_score: 3, home_score: 2, period_label: 'Bot 7th', freshness: 'FRESH' } },
      { position_id: 'p-nhl', game_id: 'g-nhl-late', source_game_id: '2026020456', sport: 'NHL', matchup: 'PIT @ COL', selection: 'PIT ML', american: 185, sportsbook: 'DraftKings', stake_usd: 4, to_win_usd: 7.4, starts_at: iso(now, 150), slate_date: MISSION_DAY, state: 'UPCOMING', live: null },
    ],
    counts: { games: 5, live: 1, bets_open: 2, bets_settled: 1, blocked: 1, waiting: 2, staked_usd: 11.88, profit_usd: 2.4 },
    read_errors: {},
    ...overrides,
  }
}
