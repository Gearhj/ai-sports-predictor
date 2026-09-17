import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import type { CoverageGame, DecisionItem, Ledger, LedgerPosition } from '../../services/commandCenter'
import { TodaysGames, buildSchedule, etDate, gameState, scheduleSlots, type ScheduleRow } from './TodaysGames'

afterEach(cleanup)

const game = (id: string, startsAt: string, away: string, home: string, league = 'MLB', status = 'SCHEDULED'): CoverageGame => ({
  game_id: id, league, matchup: `${away} @ ${home}`, starts_at: startsAt, status,
  away: { abbreviation: away, name: away }, home: { abbreviation: home, name: home },
  price_state: 'NOT_COLLECTED', books: 0, fresh_books: 0, freshest_observed_at: null, stalest_usable_observed_at: null,
  consensus_no_vig_home: null, consensus_is_current: false, best: { HOME: null, AWAY: null }, model: null, quotes: [],
  checkpoints: { collection_opens_at: startsAt, decision_window_opens_at: startsAt, decision_window_closes_at: startsAt },
})

const position = (gameId: string, over: Partial<LedgerPosition> = {}): LedgerPosition => ({
  position_id: `p-${gameId}`, game_id: gameId, matchup: null, starts_at: null, market: 'MONEYLINE', selection: 'Athletics',
  entry: { american: 155, decimal: 2.55, sportsbook: 'DraftKings', sportsbook_key: 'draftkings', provider: null, classification: 'GOVERNED', quote_observed_at: null },
  stake: 2.49, placed_at: null, status: 'OPEN', result: null, profit_loss: null, settled_at: null, experiment: { id: 'e', name: 'MLB' },
  ...over,
})

const ledger = (positions: LedgerPosition[]): Ledger => ({
  schema_version: 'x', generated_at: '', authority: '', combined_series: [],
  totals: { starting_capital: 0, realized_bankroll: 0, profit_loss: 0, open_exposure: 0, available_capital: 0, open_count: 0, settled_count: 0, record: { WIN: 0, LOSS: 0, PUSH: 0 } },
  sports: [{ sport: 'MLB', label: 'MLB', account: { funded: true, label: '' }, starting_capital: 0, realized_bankroll: 0, profit_loss: 0,
    open_exposure: 0, available_capital: 0, open_count: 0, settled_count: 0, record: { WIN: 0, LOSS: 0, PUSH: 0 }, positions, series: [], reconciled: true }],
})

const DAY = '2026-09-17'
const SLATE = [
  game('late', '2026-09-18T01:38:00Z', 'MIN', 'LAA'),          // 9:38 PM ET on the 17th (UTC date is the 18th)
  game('nfl', '2026-09-18T00:15:00Z', 'DET', 'BUF', 'NFL'),     // 8:15 PM ET
  game('a', '2026-09-17T16:35:00Z', 'MIL', 'PIT', 'MLB', 'FINAL'),
  game('b', '2026-09-17T16:40:00Z', 'LAD', 'CIN', 'MLB', 'LIVE'),
  game('tb', '2026-09-17T17:10:00Z', 'ATH', 'TB'),
  game('nym', '2026-09-17T23:15:00Z', 'PHI', 'NYM'),
  game('hou', '2026-09-17T23:15:00Z', 'KC', 'HOU'),
  game('tomorrow', '2026-09-18T16:35:00Z', 'SEA', 'OAK'),       // 12:35 PM ET on the 18th
  game('nba', '2026-10-21T23:30:00Z', 'BOS', 'DET', 'NBA'),     // October NBA example
  game('overnight', '2026-09-17T03:40:00Z', 'SF', 'SD'),        // 11:40 PM ET on the 16th
]

describe('buildSchedule', () => {
  it("keeps only today's Eastern games, in start order, including started and finished games", () => {
    const rows = buildSchedule({ games: SLATE, ledger: ledger([]), items: [], day: DAY })
    expect(rows.map((r) => r.gameId)).toEqual(['a', 'b', 'tb', 'hou', 'nym', 'nfl', 'late'])
    expect(rows.find((r) => r.gameId === 'a')!.state).toBe('FINAL')
    expect(rows.find((r) => r.gameId === 'b')!.state).toBe('LIVE')
    expect(rows.some((r) => r.sport === 'NBA')).toBe(false)
  })

  it('uses the Eastern date around midnight UTC and across the November DST change', () => {
    expect(etDate(Date.parse('2026-09-18T01:38:00Z'))).toBe('2026-09-17')
    expect(etDate(Date.parse('2026-09-17T03:59:00Z'))).toBe('2026-09-16')
    expect(etDate(Date.parse('2026-09-17T04:00:00Z'))).toBe('2026-09-17')
    // 1 Nov 2026 is the fall-back Sunday: 11:30 PM EST on 1 Nov is 04:30Z on 2 Nov.
    expect(etDate(Date.parse('2026-11-02T04:30:00Z'))).toBe('2026-11-01')
    expect(etDate(Date.parse('2026-11-02T05:00:00Z'))).toBe('2026-11-02')
    // The day before, 11:30 PM EDT is 03:30Z.
    expect(etDate(Date.parse('2026-11-01T03:30:00Z'))).toBe('2026-10-31')
    const sunday = [game('snf', '2026-11-02T01:20:00Z', 'KC', 'BUF', 'NFL')]
    expect(buildSchedule({ games: sunday, ledger: null, items: [], day: '2026-11-01' })).toHaveLength(1)
    expect(buildSchedule({ games: sunday, ledger: null, items: [], day: '2026-11-02' })).toHaveLength(0)
  })

  it('attaches official positions, and a result only once settled', () => {
    const rows = buildSchedule({ games: SLATE, ledger: ledger([
      position('tb'),
      position('a', { position_id: 'p2', selection: 'Pittsburgh Pirates', status: 'SETTLED', result: 'WIN' }),
      position('b', { position_id: 'p3', status: 'OPEN', result: 'WIN' }),
    ]), items: [], day: DAY })
    expect(rows.find((r) => r.gameId === 'tb')!.positions[0]).toMatchObject({ selection: 'Athletics', american: 155, stake: 2.49, result: null })
    expect(rows.find((r) => r.gameId === 'a')!.positions[0].result).toBe('Win')
    expect(rows.find((r) => r.gameId === 'b')!.positions[0].result).toBeNull()
  })

  it("falls back to a decision's own position before the ledger is read", () => {
    const item = { game_id: 'tb', selection: 'Athletics', american: 155, sportsbook: 'FanDuel',
      position: { position_id: 'x', stake: 3, status: 'OPEN', result: null, profit_loss: null } } as unknown as DecisionItem
    const rows = buildSchedule({ games: SLATE, ledger: null, items: [item], day: DAY })
    expect(rows.find((r) => r.gameId === 'tb')!.positions[0]).toMatchObject({ sportsbook: 'FanDuel', stake: 3 })
  })

  it('maps provider statuses to plain game states', () => {
    expect(['SCHEDULED', 'LIVE', 'in_progress', 'FINAL', 'DELAYED', 'POSTPONED', 'canceled'].map(gameState))
      .toEqual([null, 'LIVE', 'LIVE', 'FINAL', 'DELAYED', 'POSTPONED', 'CANCELED'])
  })
})

describe('scheduleSlots', () => {
  const row = (id: string, at: number): ScheduleRow => ({ gameId: id, sport: 'MLB', away: id, home: 'X', startsAt: at, state: null, positions: [] })
  it('keeps up to three identical starts as separate cards and groups four or more', () => {
    expect(scheduleSlots([row('a', 1), row('b', 1), row('c', 1)]).map((s) => s.rows.length)).toEqual([1, 1, 1])
    expect(scheduleSlots([row('a', 1), row('b', 1), row('c', 1), row('d', 1), row('e', 2)]).map((s) => s.rows.length)).toEqual([4, 1])
  })
})

describe('TodaysGames', () => {
  const renderRows = (rows: ScheduleRow[]) => render(<MemoryRouter><TodaysGames rows={rows} /></MemoryRouter>)

  it('shows counts, no-position text and no time axis or legend', () => {
    renderRows(buildSchedule({ games: SLATE, ledger: ledger([]), items: [], day: DAY }))
    expect(screen.getByText('7 games · 0 AISP positions')).toBeInTheDocument()
    expect(screen.getByText('No AISP positions have been opened yet.')).toBeInTheDocument()
    const items = within(screen.getByRole('list')).getAllByRole('listitem')
    expect(items).toHaveLength(7)
    expect(items[0]).toHaveTextContent('12:35 PM ET')
    expect(items[0]).toHaveTextContent('MLB · MIL at PIT')
    expect(items[0]).toHaveTextContent('FINAL')
    expect(items[1]).toHaveTextContent('LIVE')
    expect(items[5]).toHaveTextContent('8:15 PM ETNFL · DET at BUF')
    expect(screen.getAllByText('No AISP position')).toHaveLength(7)
    expect(document.querySelector('svg')).toBeNull()
    expect(document.body.textContent).not.toMatch(/Collection|Decision window|Forecast|Settlement|Next:/)
    expect(screen.getAllByRole('link')[0]).toHaveAttribute('href', '/games/a?sport=MLB')
  })

  it('renders a position as text with selection, price, book and virtual stake', () => {
    renderRows(buildSchedule({ games: SLATE, ledger: ledger([position('tb')]), items: [], day: DAY }))
    expect(screen.getByText('7 games · 1 AISP position')).toBeInTheDocument()
    expect(screen.queryByText('No AISP positions have been opened yet.')).toBeNull()
    const card = screen.getByText('AISP POSITION').closest('li')!
    expect(card).toHaveTextContent('1:10 PM ET')
    expect(card).toHaveTextContent('Athletics +155 at DraftKings')
    expect(card).toHaveTextContent('$2.49 virtual stake')
    // The decision link sits beside the Game Center link, not inside it.
    const links = within(card).getAllByRole('link')
    expect(links.map((l) => l.textContent)).toContain('View decision')
    expect(links[0].querySelector('a')).toBeNull()
  })

  it('shows a settled result on the position badge', () => {
    renderRows(buildSchedule({ games: SLATE, ledger: ledger([position('a', { status: 'SETTLED', result: 'LOSS' })]), items: [], day: DAY }))
    expect(screen.getByText('AISP POSITION · Loss')).toBeInTheDocument()
  })

  it('lists four games at the same start under one time label, all visible', () => {
    const same = ['A', 'B', 'C', 'D'].map((t) => game(t, '2026-09-17T23:05:00Z', t, `${t}H`))
    renderRows(buildSchedule({ games: same, ledger: null, items: [], day: DAY }))
    const heading = screen.getByRole('heading', { level: 3 })
    expect(heading).toHaveTextContent('7:05 PM ET · 4 games')
    expect(screen.getAllByText(/^MLB$/)).toHaveLength(4)
    expect(screen.getAllByRole('link')).toHaveLength(4)
  })

  it('says so when no game is scheduled today', () => {
    renderRows(buildSchedule({ games: [SLATE[7], SLATE[8]], ledger: null, items: [], day: DAY }))
    expect(screen.getByText('No games are scheduled for today.')).toBeInTheDocument()
    expect(screen.getByText('0 games · 0 AISP positions')).toBeInTheDocument()
    expect(screen.queryByRole('list')).toBeNull()
  })
})
