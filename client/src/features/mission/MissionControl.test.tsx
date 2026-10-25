import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MISSION_DAY, missionFixture } from '../../test/missionFixture'
import type { MissionControl as MissionPayload } from './missionData'

let payload: unknown = null
let failure: Error | null = null
const requests: string[] = []

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string) => {
      requests.push(path)
      if (path.startsWith('/mission-control')) return failure ? Promise.reject(failure) : Promise.resolve(payload)
      return Promise.reject(new Error(`unexpected ${path}`))
    }),
  }
})

import { MissionControl } from './MissionControl'
import { prefetchRoute } from '../../services/routePrefetch'
import { operationalToday, shiftDate } from '../../services/operationalDate'

function Where() {
  const location = useLocation()
  return <output aria-label="Location">{location.pathname}{location.search}</output>
}

function renderPage(path = `/mission-control?date=${MISSION_DAY}`) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/mission-control" element={<><MissionControl /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

const row = (matchup: string) => screen.getByRole('article', { name: new RegExp(`^${matchup.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')},`) })

beforeEach(() => {
  payload = missionFixture(Date.now())
  failure = null
  requests.length = 0
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('Mission Control page', () => {
  it('asks for the day in the URL and renders every game as a pipeline', async () => {
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    expect(requests).toContain(`/mission-control?date=${MISSION_DAY}`)
    expect(screen.getByRole('heading', { level: 1, name: 'Every game, every gate' })).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(5)
    // Live first, then by the next clock, then finished.
    expect(screen.getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual([
      'NYY @ BOS, MLB', 'BOS @ TOR, NHL', 'OSU @ MICH, College Football', 'LAL @ BOS, NBA', 'KC @ BUF, NFL',
    ])
    // Each row carries all seven stage chips.
    expect(within(row('BOS @ TOR')).getAllByRole('listitem')).toHaveLength(7)
  })

  it('labels every chip state for assistive technology', async () => {
    renderPage()
    const nhl = await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    // DONE
    expect(within(nhl).getByLabelText('Forecast: done — AISP in-process NHL Gen1 forecast made this morning.')).toHaveClass('mc-tone--done')
    // WAITING, with its chip text
    const prices = within(nhl).getByLabelText('Prices: waiting — The collector buys this game between 75 and 35 minutes before puck drop.')
    expect(prices).toHaveClass('mc-tone--waiting')
    expect(prices).toHaveTextContent('Buys 7:45 PM')
    // NOT_APPLICABLE
    expect(within(nhl).getByLabelText('Live: not applicable — No NHL in-game feed; ESPN finals every 30 min.')).toHaveClass('mc-tone--na')
    // The next step's stage is highlighted.
    expect(within(nhl).getByLabelText(/^Decision: waiting/)).toHaveClass('is-current')

    // BLOCKED, with the reason in words.
    const cfb = row('OSU @ MICH')
    const approval = within(cfb).getByLabelText(/^Approval: blocked — The discovery lease expired at 11:00 AM ET/)
    expect(approval).toHaveClass('mc-tone--blocked')
    expect(approval).toHaveTextContent('Lease expired')
    expect(cfb).toHaveClass('mc-row--blocked')
    expect(within(cfb).getByLabelText(/^Next step: Blocked · Lease expired/)).toHaveTextContent('so no paper decision can be made until it is renewed')

    // UNKNOWN: an explicit read failure, an unrecognised state, and stages never sent.
    const nba = row('LAL @ BOS')
    expect(within(nba).getByLabelText('Forecast: unknown — The NBA forecast could not be read.')).toHaveClass('mc-tone--unknown')
    const odd = within(nba).getByLabelText('Approval: unknown — A state this client does not know.')
    expect(odd).toHaveClass('mc-tone--unknown')
    expect(odd).not.toHaveClass('mc-tone--done')
    for (const stage of ['Prices', 'Decision', 'Bet', 'Live', 'Result']) {
      const chip = within(nba).getByLabelText(`${stage}: unknown — AISP did not report the ${stage.toLowerCase()} stage for this game.`)
      expect(chip).toHaveTextContent('Not reported')
    }
    expect(nba.querySelectorAll('.mc-tone--done')).toHaveLength(0)
    expect(within(nba).getByLabelText('Next step: Next step not reported')).toBeInTheDocument()
  })

  it('counts down to the next step', async () => {
    renderPage()
    const nhl = await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    expect(within(nhl).getByLabelText(/^Next step: Decision in 1[12] min/)).toHaveTextContent('Decision window opens 7:30 PM ET')
    expect(within(row('NYY @ BOS')).getByLabelText(/^Next step: Live\. Sweating NYY ML/)).toBeInTheDocument()
    expect(within(row('KC @ BUF')).getByLabelText(/^Next step: W \+\$2\.40/)).toBeInTheDocument()
  })

  it('ticks the countdown as the clock moves', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const now = Date.now()
    payload = missionFixture(now)
    const game = (payload as MissionPayload).games[0]
    game.next_step = { stage: 'DECISION', kind: 'WAIT', at: new Date(now + 5 * 60_000).toISOString(), label: 'Decision in', text: null }
    renderPage()
    const nhl = await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    expect(within(nhl).getByLabelText(/^Next step: Decision in [45]:\d\d$/)).toBeInTheDocument()
    await act(async () => { vi.advanceTimersByTime(2 * 60_000) })
    expect(within(nhl).getByLabelText(/^Next step: Decision in [23]:\d\d$/)).toBeInTheDocument()
    await act(async () => { vi.advanceTimersByTime(4 * 60_000) })
    expect(within(nhl).getByLabelText(/^Next step: Decision · due (\d+ s|\d+ min) ago$/)).toBeInTheDocument()
  })

  it('shows the shared $100 meter, the five bankrolls with NHL, and system health', async () => {
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    const meter = screen.getByRole('group', { name: 'Shared daily limit $100.00' })
    expect(meter).toHaveTextContent('$37.50 entered')
    expect(meter).toHaveTextContent('$62.50 room')
    expect(meter).toHaveTextContent('3 bets')
    expect(within(meter).getByRole('meter')).toHaveAttribute('aria-valuenow', '37.5')

    const nhl = screen.getByLabelText('NHL bankroll')
    expect(nhl).toHaveTextContent('$1,000.00')
    expect(nhl).toHaveTextContent('on $1,000')
    // NBA was not reported: it reads as missing, never $0.
    const nba = screen.getByLabelText('NBA bankroll')
    expect(nba).toHaveTextContent('Not reported')
    expect(nba).not.toHaveTextContent('$0')

    const health = screen.getByLabelText('System health')
    expect(health).toHaveTextContent('Scheduler running')
    expect(health).toHaveTextContent('Backend ready')
    expect(health).toHaveTextContent('Degraded')
    expect(health).toHaveTextContent('3/4 jobs OK · 1 failed')
    expect(within(health).getByLabelText('CFB producer: expired — Grant expired.')).toBeInTheDocument()
    fireEvent.click(within(health).getByRole('button', { name: /jobs OK/ }))
    expect(health).toHaveTextContent('Evaluate CFB paper checkpoint')
    expect(health).toHaveTextContent('The CFB decision job failed on its last run.')
    // A lane settings leave off is listed as off, and is not counted as a fault above.
    expect(health).toHaveTextContent('Evaluate NBA paper checkpointSwitched off in settings')
    expect(health).not.toHaveTextContent('not registered')

    expect(screen.getByText(/Updated \d+ s ago/)).toBeInTheDocument()
  })

  it("reads the server's degraded shapes honestly: unknown allowance, unreadable bankroll, failed producer, voiding bet", async () => {
    const data = missionFixture(Date.now())
    payload = {
      ...data,
      allowance: {
        state: 'UNKNOWN', in_force: null, effective_from: null, limit_usd: null, entered_usd: null, room_usd: null,
        entries: null, ambiguous_entries: null, slate_date: MISSION_DAY, basis: null, read_state: 'READ_FAILED', error_type: 'OperationalError',
      },
      bankrolls: data.bankrolls.map((b) => b.sport === 'CFB'
        ? { ...b, funded: null, starting_usd: null, bankroll_usd: null, available_usd: null, open_exposure_usd: null, profit_usd: null, effective_on: null, read_state: 'READ_FAILED', error_type: 'ProgrammingError' }
        : b),
      health: {
        ...data.health,
        producers: [...data.health.producers, { sport: 'NFL', name: 'NFL M1 generator', state: 'FAILED', last_published_at: null, age_minutes: null, valid_until: null, detail: "The NFL M1 generator's last run failed." }],
        jobs: data.health.jobs.map((j) => j.key === 'nba-decisions' ? { ...j, expected: true } : j),
      },
      sweating: [
        ...(data.sweating ?? []),
        { position_id: 'p-void', game_id: 'g-void', sport: 'MLB', matchup: 'TB @ CLE', selection: 'CLE ML', american: 101, sportsbook: 'LowVig.ag', stake_usd: 2.42, to_win_usd: 2.44, starts_at: new Date(Date.now() - 3_600_000).toISOString(), slate_date: MISSION_DAY, placed_at: null, state: 'VOID_AWAITING_SETTLEMENT', live: { available: false, is_live: false, is_final: false, away_score: null, home_score: null, period_label: null, freshness: null, last_checked_at: null } },
      ],
      read_errors: { allowance: 'OperationalError' },
    } satisfies MissionPayload
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })

    const meter = screen.getByRole('group', { name: 'Shared daily limit' })
    expect(meter).toHaveTextContent('Could not be read (OperationalError)')
    expect(meter).not.toHaveTextContent('Not in force')
    expect(meter).not.toHaveTextContent('$0')

    const cfb = screen.getByLabelText('College Football bankroll')
    expect(cfb).toHaveTextContent('Could not be read (ProgrammingError)')
    expect(cfb).not.toHaveTextContent('Not funded')

    const health = screen.getByLabelText('System health')
    expect(within(health).getByLabelText(/^NFL producer: failed/)).toHaveClass('mc-pill--blocked')
    // The same NBA job, now expected by settings, IS a fault.
    expect(health).toHaveTextContent('3/5 jobs OK · 1 failed · 1 not registered')

    const rail = screen.getByRole('heading', { name: 'Sweating now' }).closest('.mc-sweat') as HTMLElement
    const voiding = within(rail).getAllByRole('listitem').find((li) => li.textContent?.includes('CLE ML')) as HTMLElement
    expect(voiding).toHaveTextContent('Not played · voiding')
    expect(within(voiding).getByTitle('at LowVig.ag')).toHaveTextContent('+101')
  })

  it('says the shared limit is not in force when the server reports no room figure', async () => {
    const data = missionFixture(Date.now())
    payload = { ...data, allowance: { ...data.allowance, state: 'NOT_IN_FORCE', in_force: false, room_usd: null, effective_from: '2026-10-20T04:00:00Z' } } satisfies MissionPayload
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    expect(screen.getByRole('group', { name: 'Shared daily limit $100.00' })).toHaveTextContent('Not in force for this slate · starts Oct 20')
  })

  it('lists open bets in the sweating rail with live scores', async () => {
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    const rail = screen.getByRole('heading', { name: 'Sweating now' }).closest('.mc-sweat') as HTMLElement
    const items = within(rail).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('NYY ML')
    expect(items[0]).toHaveTextContent('NYY 3')
    expect(items[0]).toHaveTextContent('Bot 7th')
    expect(items[1]).toHaveTextContent('PIT ML')
    expect(items[1]).toHaveTextContent('Puck drop')
    expect(rail).toHaveTextContent('1 live · $9.00 at risk')
  })

  it('tells why games are missing: pending NHL games and a sport that failed to read', async () => {
    const data = missionFixture(Date.now())
    payload = {
      ...data,
      games: data.games.filter((g) => g.sport !== 'CFB'),
      sports: data.sports.map((s) => s.sport === 'CFB' ? { ...s, games: 0, read_state: 'READ_FAILED', error_type: 'OperationalError' } : s),
    }
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    expect(screen.getByText(/could not be read \(OperationalError\)/)).toBeInTheDocument()
    expect(screen.getByText(/1 more NHL game/)).toBeInTheDocument()
    expect(screen.getByText(/back-to-back team’s rest is only complete on game day/)).toBeInTheDocument()
    // The failed sport keeps a flagged chip; the rest of the page is intact.
    expect(screen.getByRole('button', { name: /CFB !/ })).toHaveClass('mc-chip--failed')
    expect(screen.getAllByRole('article')).toHaveLength(4)
  })

  it('explains how the day works only for sports on the board', async () => {
    const data = missionFixture(Date.now())
    payload = {
      ...data,
      sports: data.sports.map((s) => ({ ...s, schedule_note: `${s.sport} note.`, ...(s.sport === 'NBA' ? { games: 0 } : {}) })),
      games: data.games.filter((g) => g.sport !== 'NBA'),
    } satisfies MissionPayload
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    expect(screen.getByText('NHL note.')).toBeInTheDocument()
    expect(screen.queryByText('NBA note.')).not.toBeInTheDocument()
  })

  it('filters by sport, starting from ?sport=, and keeps the choice in the URL', async () => {
    renderPage(`/mission-control?date=${MISSION_DAY}&sport=NHL`)
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    expect(screen.getAllByRole('article')).toHaveLength(1)
    const filter = screen.getByRole('group', { name: 'Sport filter' })
    fireEvent.click(within(filter).getByRole('button', { name: /^All/ }))
    expect(screen.getAllByRole('article')).toHaveLength(5)
    // "All" removes the filter from the URL, so a reload or a shared link shows every sport.
    expect(screen.getByLabelText('Location').textContent).toBe(`/mission-control?date=${MISSION_DAY}`)
    fireEvent.click(within(filter).getByRole('button', { name: /^NFL/ }))
    expect(screen.getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual(['KC @ BUF, NFL'])
    expect(screen.getByLabelText('Location').textContent).toBe(`/mission-control?date=${MISSION_DAY}&sport=NFL`)
  })

  it('switches day through the URL and asks for that day', async () => {
    renderPage('/mission-control')
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    const days = screen.getByRole('navigation', { name: 'Day' })
    expect(within(days).getAllByRole('button').map((b) => b.querySelector('span')?.textContent)).toEqual(['Yesterday', 'Today', 'Tomorrow', expect.any(String)])
    expect(within(days).getByRole('button', { name: /^Today/ })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(days).getByRole('button', { name: /^Tomorrow/ }))
    const search = screen.getByLabelText('Location').textContent ?? ''
    const tomorrow = /date=(\d{4}-\d{2}-\d{2})/.exec(search)?.[1]
    expect(tomorrow).toBeTruthy()
    await waitFor(() => expect(requests).toContain(`/mission-control?date=${tomorrow}`))
  })

  it('expands a row into plain-English stage details', async () => {
    renderPage()
    const cfb = await screen.findByRole('article', { name: /^OSU @ MICH,/ })
    fireEvent.click(within(cfb).getByRole('button', { name: 'Show stage details for OSU @ MICH' }))
    expect(cfb).toHaveTextContent('EXPIRED_LEASE')
    expect(cfb).toHaveTextContent('MICH best-150 · DraftKings')
    expect(cfb).toHaveTextContent('5 fresh of 6')
  })

  it('says there are no games, with the next one, on an empty day', async () => {
    const next = new Date(Date.now() + 26 * 3_600_000).toISOString()
    payload = missionFixture(Date.now(), {
      games: [], sweating: [],
      sports: [{ sport: 'NHL', label: 'NHL', games: 0, read_state: 'OK', next_event_at: next }],
      counts: { games: 0, live: 0, bets_open: 0, bets_settled: 0, blocked: 0, waiting: 0, staked_usd: 0, profit_usd: 0 },
    })
    renderPage()
    expect(await screen.findByText(/No games on the board/)).toBeInTheDocument()
    expect(screen.getByText(/Next up: NHL on/)).toBeInTheDocument()
    expect(screen.getByText(/No open bets right now/)).toBeInTheDocument()
  })

  it('shows a failed read once, with one Retry, without blanking the page', async () => {
    failure = new Error('Request failed (503)')
    renderPage()
    // The server answered with an error: the read failed, the backend is not "unreachable".
    expect(await screen.findByText('Mission Control could not be read.')).toBeInTheDocument()
    expect(screen.getAllByText(/Request failed \(503\)/)).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: /Retry/ })).toHaveLength(1)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    // The deck, the bankroll strip and the rail still render, honestly empty.
    expect(screen.getByRole('heading', { level: 1, name: 'Every game, every gate' })).toBeInTheDocument()
    expect(screen.getByLabelText('System health')).toHaveTextContent('Health not read')
    expect(screen.getByLabelText('System health')).not.toHaveTextContent('unreachable')
    expect(screen.getByText('Not loaded')).toBeInTheDocument()
    expect(screen.getByLabelText('NHL bankroll')).toHaveTextContent('Not reported')
    expect(screen.getByText('Open bets appear here once Mission Control loads.')).toBeInTheDocument()
    expect(screen.queryByText(/No open bets right now/)).not.toBeInTheDocument()
    expect(screen.queryByText('Scheduler running')).not.toBeInTheDocument()
  })

  it('says the backend is unreachable only when the request never reached it', async () => {
    failure = new TypeError('Failed to fetch')
    renderPage()
    expect(await screen.findByText('The backend could not be reached.')).toBeInTheDocument()
    expect(screen.getByLabelText('System health')).toHaveTextContent('Backend unreachable')
  })

  it('refuses a response that is not the Mission Control read model', async () => {
    payload = { schema_version: 'AISP_COMMAND_CENTER_DECISIONS_V1', items: [] }
    renderPage()
    expect(await screen.findByText('Mission Control could not be read.')).toBeInTheDocument()
    expect(screen.getAllByText(/Unexpected response \(expected AISP_MISSION_CONTROL_V1\)/)).toHaveLength(1)
    expect(screen.queryAllByRole('article')).toHaveLength(0)
  })

  it('never shows a failed open-bets read as "no open bets"', async () => {
    payload = { ...missionFixture(Date.now()), sweating: null, read_errors: { sweating: 'DataError', scores: 'DataError' } } satisfies MissionPayload
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    const rail = screen.getByRole('heading', { name: 'Sweating now' }).closest('.mc-sweat') as HTMLElement
    expect(rail).toHaveTextContent('Open bets could not be read')
    expect(rail).not.toHaveTextContent('No open bets right now')
    // (An empty but successful read says "No open bets right now": see the empty-day case.)
  })

  it("labels the shared-limit meter with the selected slate, not always today's", async () => {
    const yesterday = shiftDate(operationalToday(), -1)
    const data = missionFixture(Date.now())
    payload = { ...data, date: yesterday, allowance: { ...data.allowance, slate_date: yesterday } } satisfies MissionPayload
    renderPage(`/mission-control?date=${yesterday}`)
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    const meter = screen.getByRole('group', { name: 'Shared daily limit $100.00' })
    expect(meter).toHaveTextContent("Yesterday's shared $100")
    expect(meter).not.toHaveTextContent("Today's")
  })

  it('names a producer that is not OK in words, not by dot colour alone', async () => {
    const data = missionFixture(Date.now())
    payload = {
      ...data,
      health: { ...data.health, producers: [...data.health.producers, { sport: 'NFL', name: 'NFL M1 generator', state: 'MISSING', last_published_at: null, age_minutes: null, valid_until: null, detail: 'No successful run.' }] },
    } satisfies MissionPayload
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    const nfl = within(screen.getByLabelText('System health')).getByRole('img', { name: /^NFL producer: missing/ })
    expect(nfl).toHaveTextContent('NFLmissing')
  })

  it('shows a stale forecast as aged on its chip, and an MLB buy window with no close as "from"', async () => {
    const now = Date.now()
    const data = missionFixture(now)
    const cfb = data.games.find((g) => g.sport === 'CFB')!
    cfb.stages.forecast = { ...cfb.stages.forecast!, freshness: 'STALE', age_minutes: 8600 }
    const mlb = data.games.find((g) => g.sport === 'MLB')!
    mlb.clocks = { ...mlb.clocks, buy_opens_at: '2026-10-17T20:10:00Z', buy_closes_at: null }
    payload = data
    renderPage()
    const row = await screen.findByRole('article', { name: /^OSU @ MICH,/ })
    const chip = within(row).getByLabelText(/^Forecast: done/)
    expect(chip).toHaveTextContent('MICH 61% · 6 d old')
    expect(chip).toHaveClass('is-aged')
    const nyy = screen.getByRole('article', { name: /^NYY @ BOS,/ })
    fireEvent.click(within(nyy).getByRole('button', { name: /Show stage details/ }))
    expect(nyy).toHaveTextContent('Buy windowfrom 4:10 PM ET')
    expect(nyy).not.toHaveTextContent('–— ET')
  })

  it('gives a long College Football school name no crest-sized mark', async () => {
    const data = missionFixture(Date.now())
    const cfb = data.games.find((g) => g.sport === 'CFB')!
    Object.assign(cfb, { matchup: 'Western Michigan @ Central Michigan', away: { abbr: 'Western Michigan' }, home: { abbr: 'Central Michigan' } })
    payload = data
    renderPage()
    const row = await screen.findByRole('article', { name: /^Western Michigan @ Central Michigan,/ })
    expect(row.querySelectorAll('.mc-row__teams .team-logo')).toHaveLength(0)
    expect(within(row).getByRole('link', { name: 'Western Michigan @ Central Michigan' })).toBeInTheDocument()
    // Short codes keep their marks.
    expect(screen.getByRole('article', { name: /^BOS @ TOR,/ }).querySelectorAll('.mc-row__teams .team-logo')).toHaveLength(2)
  })

  it('links an open bet whose game is not on this board through its own game id', async () => {
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    const rail = screen.getByRole('heading', { name: 'Sweating now' }).closest('.mc-sweat') as HTMLElement
    const pit = within(rail).getAllByRole('link').find((a) => a.textContent?.includes('PIT ML'))!
    expect(pit).toHaveAttribute('href', '/nhl/games/2026020456')
  })

  it('runs one shared countdown clock, not one timer per row', async () => {
    const spy = vi.spyOn(window, 'setInterval')
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    const delays = spy.mock.calls.map((c) => c[1])
    spy.mockRestore()
    // Rows used to start one 15 s (or 1 s) interval each.
    expect(delays.filter((d) => d === 15_000)).toHaveLength(0)
    expect(delays.filter((d) => d === 1000).length).toBeLessThanOrEqual(2)   // the deck clock + the shared row clock
  })

  it("warms today's read when the nav link is hovered", async () => {
    prefetchRoute('/mission-control?sport=ALL')
    await waitFor(() => expect(requests).toContain(`/mission-control?date=${operationalToday()}`))
  })

  it('polls every 30 seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderPage()
    await screen.findByRole('article', { name: /^BOS @ TOR,/ })
    const before = requests.filter((r) => r.startsWith('/mission-control')).length
    await act(async () => { vi.advanceTimersByTime(30_000) })
    await waitFor(() => expect(requests.filter((r) => r.startsWith('/mission-control')).length).toBeGreaterThan(before))
  })
})
