/**
 * Deterministic Show Board responses for tests and browser verification via
 * request interception. Never written to any database.
 */
import type { PreviewFinalRow, ShowBoardResponse, ShowGame, ShowSport } from '../services/showBoard'

const iso = (now: number, minutes: number) => new Date(now + minutes * 60_000).toISOString()

interface GameSpec {
  key: string; sport: ShowSport; away: string; home: string; startIn: number
  forecast?: { home: number; away: number; tie?: number; score: [number, number] }
  market?: 'complete' | 'stale' | 'partial' | 'none'
  consensusHome?: number
}

function build(now: number, spec: GameSpec): ShowGame {
  const start = iso(now, spec.startIn)
  const f = spec.forecast
  const market = spec.market ?? 'none'
  const consensus = spec.consensusHome ?? 0.5
  const captured = market === 'stale' ? iso(now, -60 * 30) : iso(now, -95)
  const pairs = market === 'complete'
    ? ['Fanduel', 'Betmgm', 'Draftkings'].map((b, i) => ({
      sportsbook: `${b} (SportsGameOdds, research)`, provider: 'SportsGameOdds', classification: 'RESEARCH', line: null,
      older_leg_observed_utc: captured, no_vig_home: consensus + (i - 1) * 0.012, settlement: spec.sport === 'NFL' || spec.sport === 'CFB' ? 'TIE_REFUNDS_STAKE' : 'TWO_WAY_NO_TIE',
      legs: [{ side: 'HOME', american: -130 + i * 5, point: null }, { side: 'AWAY', american: 110 + i * 4, point: null }],
    }))
    : []
  const missing: ShowGame['missing'] = []
  if (!f) missing.push({ code: 'PREVIEW_FORECAST_AWAITING_PRODUCER', message: 'Preview forecast awaiting producer' })
  if (f && spec.sport === 'MLB') missing.push({ code: 'PROBABLE_STARTERS', message: 'Starters are probable, not confirmed' })
  if (market === 'none') missing.push({ code: 'MARKET_NOT_POSTED', message: 'Market not posted: no sportsbook moneyline captured before the evidence cut' })
  if (market === 'stale') missing.push({ code: 'STALE_PREVIEW_PRICE', message: 'Only stale preview moneyline prices were captured' })
  if (market === 'partial') missing.push({ code: 'PARTIAL_MARKET', message: 'Only one side of the moneyline was captured' })
  const complete = market === 'complete'
  const readiness = f && complete ? 'READY' : f ? 'FORECAST_ONLY' : complete ? 'MARKET_ONLY' : 'NEITHER'
  const twoWayHome = f ? f.home / (f.home + f.away) : null
  const side = twoWayHome != null && twoWayHome - consensus >= (1 - twoWayHome) - (1 - consensus) ? 'HOME' : 'AWAY'
  const model = twoWayHome == null ? null : side === 'HOME' ? twoWayHome : 1 - twoWayHome
  const noVig = side === 'HOME' ? consensus + 0.012 : 1 - (consensus - 0.012)
  const american = side === 'HOME' ? -120 : 118
  const decimal = american > 0 ? 1 + american / 100 : 1 + 100 / -american
  const comparison = f && complete && model != null ? {
    label: 'Preview model-market difference', selection: side, team: side === 'HOME' ? spec.home : spec.away,
    model_probability: model, no_vig_probability: noVig, difference: model - noVig, expected_return: model * (decimal - 1) - (1 - model),
    american, opposite_american: side === 'HOME' ? 110 : -130, sportsbook: 'Fanduel (SportsGameOdds, research)', provider: 'SportsGameOdds',
    classification: 'RESEARCH', settlement: pairs[0].settlement, price_observed_utc: captured, forecast_created_utc: iso(now, -140),
  } as ShowGame['comparison'] : null
  return {
    game_key: spec.key, canonical_game_id: spec.sport === 'CFB' ? null : `00000000-0000-4000-8000-${spec.key.padStart(12, '0')}`,
    sport: spec.sport, matchup: `${spec.away} @ ${spec.home}`,
    away: { abbreviation: spec.away, name: spec.away }, home: { abbreviation: spec.home, name: spec.home },
    kickoff_utc: start, status: 'SCHEDULED',
    final_decision_window: { opens_utc: iso(now, spec.startIn - 30), closes_utc: iso(now, spec.startIn - 15) },
    readiness, readiness_label: { READY: 'Forecast and market ready', FORECAST_ONLY: 'Market not posted', MARKET_ONLY: 'Preview forecast pending', NEITHER: 'Preview forecast pending · Market not posted' }[readiness],
    forecast_status: f ? 'AVAILABLE' : 'AWAITING_PRODUCER',
    forecast: f ? {
      forecast_id: `preview-${spec.key}`, content_sha256: 'b'.repeat(64), model_id: `${spec.sport}_PREVIEW_FIXTURE`, model_version: 'fixture-v1',
      created_utc: iso(now, -140), source_cutoff_utc: iso(now, -170),
      expected_score: { away: f.score[0], home: f.score[1] }, projected_margin_home: f.score[1] - f.score[0], projected_total: f.score[0] + f.score[1],
      probabilities: { away_win: f.away, home_win: f.home, tie: f.tie ?? 0 },
      lean: { side: f.home >= f.away ? 'HOME' : 'AWAY', team: f.home >= f.away ? spec.home : spec.away, probability_two_way: Math.max(f.home, f.away) / (f.home + f.away) },
      starters: spec.sport === 'MLB' ? 'PROBABLE' : 'NOT_APPLICABLE', lineups: spec.sport === 'MLB' ? 'UNKNOWN' : 'NOT_APPLICABLE',
    } : null,
    markets: {
      MONEYLINE: { pairs, complete: pairs.length, stale: market === 'stale' ? 2 : 0, partial: market === 'partial' ? 1 : 0, latest_capture_utc: market === 'none' ? null : captured },
      SPREAD: { pairs: [], complete: complete ? 2 : 0, stale: 0, partial: 0, latest_capture_utc: complete ? captured : null },
      TOTAL: { pairs: [], complete: complete ? 2 : 0, stale: 0, partial: 0, latest_capture_utc: complete ? captured : null },
    },
    market_summary: {
      moneyline: market === 'none' ? null : {
        books: market === 'partial' ? 1 : 3, complete_books: complete ? 3 : 0, stale_books: market === 'stale' ? 2 : 0, partial_books: market === 'partial' ? 1 : 0,
        consensus_no_vig_home: complete ? consensus : null, range_no_vig_home: complete ? [consensus - 0.012, consensus + 0.012] : null,
        best: complete ? { HOME: { american: -120, sportsbook: 'Fanduel (SportsGameOdds, research)', provider: 'SportsGameOdds', observed_utc: captured }, AWAY: { american: 118, sportsbook: 'Draftkings (SportsGameOdds, research)', provider: 'SportsGameOdds', observed_utc: captured } } : {},
        captured_utc: complete ? captured : null,
      },
      spread: complete ? { median_line: spec.sport === 'MLB' ? -1.5 : -3.5, range: [-4, -3], books: 2, captured_utc: captured } : null,
      total: complete ? { median_line: spec.sport === 'MLB' ? 8.5 : 47.5, range: [8, 9], books: 2, captured_utc: captured } : null,
    },
    comparison,
    rff: { supported: false, note: 'RFF is not computed at the preview horizon: its inputs have no evidence-time cut.' },
    // MLB preview forecasts carry the model's own limitations; the other
    // sports' producers state theirs inside missing_evidence.
    disclosures: f && spec.sport === 'MLB' ? [
      { code: 'TEAM_HISTORY_MODEL_ONLY', message: 'Team score history only. No pitchers, lineups, injuries, park, rest or market inputs.' },
      { code: 'NO_JOINT_SCORE_DISTRIBUTION', message: 'Win probability and the two expected scores are estimated separately. They are not one joint distribution and must not be read as a simulated population.' },
      { code: 'NO_TOTAL_OR_RUN_LINE_PROBABILITY', message: 'No total or run-line probability is produced. Expected runs are not an Over/Under or run-line probability.' },
      { code: 'RESEARCH_ONLY', message: 'Research and editorial only. It carries no decision, stake, position or wagering authority.' },
      { code: 'NOT_FINAL_V18', message: 'Not the Final V18 forecast, and never a substitute for one.' },
    ] : [],
    missing,
  }
}

function response(now: number, slate: string, mode: ShowBoardResponse['mode'], games: ShowGame[], pvf: PreviewFinalRow[] = []): ShowBoardResponse {
  const ranked = games.filter((g) => g.comparison && g.comparison.difference > 0)
    .sort((a, b) => b.comparison!.difference - a.comparison!.difference)
  const frozenAt = iso(now, -30)
  const tp = []
  if (ranked[0]) tp.push({ code: 'STRONGEST_DISAGREEMENT', game_key: ranked[0].game_key, text: `Largest preview model-market difference: ${ranked[0].comparison!.team} moneyline in ${ranked[0].matchup}, +${(ranked[0].comparison!.difference * 100).toFixed(1)} pts.` })
  const withF = games.filter((g) => g.forecast)
  if (withF.length) {
    const closest = [...withF].sort((a, b) => Math.abs(a.forecast!.projected_margin_home) - Math.abs(b.forecast!.projected_margin_home))[0]
    tp.push({ code: 'CLOSEST_PROJECTED', game_key: closest.game_key, text: `Closest projected game: ${closest.matchup}.` })
  }
  const unposted = games.filter((g) => !g.market_summary.moneyline?.complete_books).length
  if (unposted) tp.push({ code: 'MISSING_MARKETS', game_key: null, text: `${unposted} of ${games.length} games have no complete preview moneyline yet.` })
  const clockBase = Date.parse(`${slate}T00:00:00Z`) - 24 * 3_600_000 + 4 * 3_600_000
  const step = (key: string, label: string, t: string, calls = false, implemented = true) => ({
    key, label, local_time: t, at_utc: new Date(clockBase + (Number(t.slice(0, 2)) * 60 + Number(t.slice(3))) * 60_000).toISOString(),
    provider_calls: calls, implemented, note: '',
  })
  return {
    schema_version: 'AISP_SHOW_BOARD_RESPONSE_V1', generated_at_utc: iso(now, -1), slate_date: slate, default_slate_date: slate,
    mode, mode_label: { FROZEN: 'Frozen show snapshot', DRAFT: 'Draft show board', RECONSTRUCTED: 'Reconstructed from retained evidence — not a frozen snapshot' }[mode],
    planned_freeze_utc: mode === 'FROZEN' ? frozenAt : iso(now, 55),
    flags: { show_preview_enabled: mode === 'FROZEN', provider_collection_enabled: false, auto_freeze_enabled: false },
    snapshot: mode === 'FROZEN' ? { snapshot_sha256: 'c0ffee'.repeat(10) + 'abcd', frozen_utc: frozenAt, trigger: 'ADMIN_FREEZE', verified: true, predecessor_sha256: null, supersede_reason: null, payload_bytes: 48213 } : null,
    history: [],
    board: {
      schema_version: 'AISP_SHOW_BOARD_V1', horizon: 'SHOW_PREVIEW', status_label: 'SHOW PREVIEW — NOT FINAL', slate_date: slate, timezone: 'America/New_York',
      planned_freeze_utc: mode === 'FROZEN' ? frozenAt : iso(now, 55), evidence_as_of_utc: mode === 'FROZEN' ? frozenAt : iso(now, -1),
      authority: { statement: 'Show Preview research for the day-before recording. Not a final wagering decision, not a governed position and not final-pregame evidence.', grants_wagering_authority: false, creates_positions: false },
      readiness: {
        games: games.length, preview_forecasts: withF.length, complete_markets: games.filter((g) => g.market_summary.moneyline?.complete_books).length,
        both: games.filter((g) => g.readiness === 'READY').length, missing_evidence_games: games.filter((g) => g.readiness !== 'READY').length,
        valid_comparisons: ranked.length, by_sport: Object.fromEntries((['MLB', 'NFL', 'NBA', 'CFB'] as const).map((s) => [s, games.filter((g) => g.sport === s).length])),
      },
      games,
      ranking: ranked.map((g, i) => ({ rank: i + 1, game_key: g.game_key, difference: g.comparison!.difference, expected_return: g.comparison!.expected_return })),
      talking_points: tp,
      disclosures: [{ game_key: null, code: 'PREVIEW_COLLECTION_DISABLED', message: 'Day-before sportsbook collection is not enabled; markets shown are those already persisted' }],
      clock: [step('SCHEDULE_SYNC', "Synchronize tomorrow's schedule", '15:00', false, false), step('FORECAST_IMPORT', "Import or reuse tomorrow's preview forecasts", '15:15'),
        step('MARKET_COLLECTION', 'Collect available next-day sportsbook markets', '16:00', true, false), step('BUILD', 'Build preliminary comparisons and rankings', '16:15'),
        step('FREEZE', 'Freeze the immutable Show Board', '16:30'), step('RECORDING', 'James and Scott record', '17:00', false, false)],
    },
    preview_vs_final: pvf,
    provider_calls_made: 0,
  }
}

export function draftPartial(now: number): ShowBoardResponse {
  const games = [
    build(now, { key: '1', sport: 'MLB', away: 'BOS', home: 'NYY', startIn: 1500, market: 'complete', consensusHome: 0.56 }),
    build(now, { key: '2', sport: 'MLB', away: 'LAD', home: 'SF', startIn: 1560, market: 'stale' }),
    build(now, { key: '3', sport: 'MLB', away: 'CHC', home: 'STL', startIn: 1590, market: 'partial' }),
    build(now, { key: '4', sport: 'MLB', away: 'HOU', home: 'SEA', startIn: 1680 }),
    build(now, { key: '11', sport: 'NFL', away: 'DET', home: 'BUF', startIn: 1650, market: 'complete', consensusHome: 0.55, forecast: { home: 0.6, away: 0.37, tie: 0.03, score: [21.4, 25.9] } }),
    build(now, { key: '12', sport: 'NFL', away: 'KC', home: 'GB', startIn: 1700, market: 'complete', consensusHome: 0.48, forecast: { home: 0.46, away: 0.515, tie: 0.025, score: [24.1, 23.0] } }),
  ]
  return response(now, '2026-09-17', 'DRAFT', games)
}

export function frozenFull(now: number): ShowBoardResponse {
  const games = [
    build(now, { key: '1', sport: 'MLB', away: 'BOS', home: 'NYY', startIn: 1500, market: 'complete', consensusHome: 0.52, forecast: { home: 0.6, away: 0.4, score: [3.9, 5.1] } }),
    build(now, { key: '2', sport: 'MLB', away: 'LAD', home: 'SF', startIn: 1560, market: 'complete', consensusHome: 0.44, forecast: { home: 0.47, away: 0.53, score: [4.6, 4.2] } }),
    build(now, { key: '11', sport: 'NFL', away: 'DET', home: 'BUF', startIn: 1650, market: 'complete', consensusHome: 0.55, forecast: { home: 0.63, away: 0.34, tie: 0.03, score: [20.8, 26.4] } }),
    build(now, { key: '21', sport: 'NBA', away: 'BOS', home: 'NYK', startIn: 1620, market: 'complete', consensusHome: 0.5, forecast: { home: 0.56, away: 0.44, score: [108.2, 111.5] } }),
    build(now, { key: '31', sport: 'CFB', away: 'UGA', home: 'BAMA', startIn: 1400, market: 'none', forecast: { home: 0.58, away: 0.40, tie: 0.02, score: [24.3, 27.8] } }),
    build(now, { key: '3', sport: 'MLB', away: 'CHC', home: 'STL', startIn: 1590, market: 'complete', consensusHome: 0.5, forecast: { home: 0.53, away: 0.47, score: [4.4, 4.7] } }),
  ]
  const pvf: PreviewFinalRow[] = [
    { game_key: '1', matchup: 'BOS @ NYY', sport: 'MLB', preview: { selection: 'NYY', model_probability: 0.6, no_vig_probability: 0.532, difference: 0.068, american: -120, sportsbook: 'Fanduel', price_observed_utc: iso(now, -95), forecast_status: 'AVAILABLE', consensus_no_vig_home: 0.52 },
      final: { selection: 'NYY', model_probability: 0.61, no_vig_probability: 0.55, american: -128, sportsbook: 'Fanduel', price_observed_utc: iso(now, 1480), disposition: 'QUALIFIED_BET', decided_utc: iso(now, 1478) },
      changes: { selection_changed: false, model_probability_change: 0.01, price_change_american: -8 }, position: { stake: 12.5, status: 'SETTLED', result: 'WIN', profit_loss: 9.77 }, outcome: { away_score: 3, home_score: 6, winner: 'NYY' } },
    { game_key: '2', matchup: 'LAD @ SF', sport: 'MLB', preview: { selection: 'LAD', model_probability: 0.53, no_vig_probability: 0.548, difference: -0.018, american: 118, sportsbook: 'Fanduel', price_observed_utc: iso(now, -95), forecast_status: 'AVAILABLE', consensus_no_vig_home: 0.44 },
      final: { selection: 'SF', model_probability: 0.49, no_vig_probability: 0.45, american: 124, sportsbook: 'Betmgm', price_observed_utc: iso(now, 1540), disposition: 'NO_BET', decided_utc: iso(now, 1538) },
      changes: { selection_changed: true, model_probability_change: null, price_change_american: null }, position: null, outcome: null },
  ]
  return response(now, '2026-09-17', 'FROZEN', games, pvf)
}
