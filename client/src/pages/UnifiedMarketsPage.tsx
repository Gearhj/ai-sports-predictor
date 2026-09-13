/**
 * Markets & Crowd — the price board.
 *
 * WHAT WAS UNUSABLE ABOUT THE PAGE THIS REPLACES
 * ----------------------------------------------
 * MLB rendered every bookmaker as a line of wrapped prose inside a narrow
 * card column: "Fanduel (SportsGameOdds, research) +104 · Betmgm
 * (SportsGameOdds, research) -102 · ..." repeated for both sides of every
 * game. Nine books became a two-hundred-pixel-tall column of text in which no
 * two prices were on the same line, so the one thing a price board exists for
 * -- comparing prices -- was the one thing it could not do.
 *
 * This is comparison-first. One row per game per market. The prices that
 * matter (model fair, best available, consensus) are in fixed columns, the
 * spread of the book is a plot rather than a paragraph, and the full book list
 * is a compact table inside a drawer.
 *
 * WHAT IS KEPT APART, ON PURPOSE
 * ------------------------------
 *  - Moneyline, run line / spread, and total are separate tabs. They are
 *    separate questions and the page never puts them on one axis.
 *  - A no-vig consensus probability and a single book's implied probability
 *    are different quantities; the book band is drawn hollow and labelled as
 *    carrying the margin.
 *  - Kalshi and Polymarket are home-win contracts with their own settlement
 *    rules. They appear on the moneyline tab only, carry their own review
 *    status, and are never averaged into a sportsbook consensus.
 *  - The Odds API and the temporary SportsGameOdds research fallback are
 *    labelled separately on every price. They are not one "source".
 */
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Freshness, SkeletonCards, SkeletonTable } from '../components/ui'
import {
  ArrowRight, ChevronDown, Flame, History, Layers, RefreshCw, Radio, Sigma,
} from 'lucide-react'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { quoteDescription, resolveQuotedContract } from '../services/marketOrientation'
import { availabilityNote, engineShortName } from '../services/forecastSelection'
import { hasStarted } from '../services/statusStyle'
import { useSportSelection } from '../hooks/useSportSelection'
import { apiRequest, endpoints } from '../services/api'
import {
  ComparisonPlot, PlotLegend, impliedProbability, type PlotMark,
} from '../components/broadcast/ComparisonPlot'
import { TeamLogo } from '../components/TeamLogo'
import { teamIdentity } from '../services/teamIdentity'
import {
  LeagueTag, Panel, SourceBadge, agoOf, american, clockOf, decimals, pct,
  signedPoints, useBroadcastTheme,
} from '../components/broadcast/primitives'
import { ReplayView, RffView, type ReplayChoice } from '../components/broadcast/MarketViews'
import { MlbTotalsView } from '../components/markets/MlbTotalsView'
import { ParlayWatchView } from '../components/markets/ParlayWatchView'
import {
  readMarketFilter, useMlbTotals, useParlayWatch, useRecordTicket,
} from '../hooks/useMlbTotals'
import { conditionalHomeWin } from '../services/broadcastSlate'
import type {
  MarketBoardData, MarketBoardRow, MarketPulseBoard, MarketPulseRow,
  PredictionProviderProbability,
} from '../types/domain'
import type { NflEvent, NflMarketBlock, NflWeeks } from '../types/nfl'
import '../broadcast.css'

type Family = 'MONEYLINE' | 'SPREAD' | 'TOTAL'
type View = 'PRICES' | 'TOTALS' | 'PARLAY' | 'REPLAY' | 'RFF'
const VIEW_KEYS = ['PRICES', 'TOTALS', 'PARLAY', 'REPLAY', 'RFF'] as const
function readView(value: string | null): View {
  const upper = (value ?? '').toUpperCase()
  return (VIEW_KEYS as readonly string[]).includes(upper) ? (upper as View) : 'PRICES'
}

const VIEWS: { key: View; label: string; hint: string }[] = [
  { key: 'PRICES', label: 'Prices', hint: 'One row per game: model, best price, consensus, book spread.' },
  { key: 'TOTALS', label: 'Over / Under', hint: "V18's own run distribution priced against the recorded total." },
  { key: 'PARLAY', label: 'Parlay Watch', hint: 'Two- and three-leg research combinations, priced from stored quotes.' },
  { key: 'REPLAY', label: 'Replay', hint: 'Watch one market move from its first observation to the cutoff.' },
  { key: 'RFF', label: 'RFF Lab', hint: 'Ripe For Fuckery: a provisional research index of market unrest.' },
]
const FAMILIES: Family[] = ['MONEYLINE', 'SPREAD', 'TOTAL']
const FAMILY_LABEL: Record<'MLB' | 'NFL', Record<Family, string>> = {
  MLB: { MONEYLINE: 'Moneyline', SPREAD: 'Run line', TOTAL: 'Total runs' },
  NFL: { MONEYLINE: 'Moneyline', SPREAD: 'Point spread', TOTAL: 'Total points' },
}
/** The board's own market_type strings, which are not the family codes. */
const BOARD_MARKET: Record<Family, string> = { MONEYLINE: 'Moneyline', SPREAD: 'Run line', TOTAL: 'Total' }

const FRESH_MINUTES = 240

type NflBoard = { season: number; week: number; events: NflEvent[]; now_utc?: string }

interface MarketsBoard {
  data_mode: 'live'
  scope: string
  date: string
  books: MarketBoardData | null
  crowd: MarketPulseBoard | null
  weeks: NflWeeks | null
  nfl: NflBoard | null
  warnings: string[]
}

const isFresh = (observedAt: string | null | undefined, now: number): boolean | null => {
  if (!observedAt) return null
  const age = (now - new Date(observedAt).getTime()) / 60_000
  return Number.isFinite(age) ? age <= FRESH_MINUTES : null
}

export function UnifiedMarketsPage() {
  useBroadcastTheme()
  const { sport, withSport } = useSportSelection()
  const [params, setParams] = useSearchParams()
  const [family, setFamily] = useState<Family>('MONEYLINE')
  const [query, setQuery] = useState('')
  // Replay and the RFF Lab are views of THIS page, not new destinations: the
  // sidebar already has ten entries and neither is a place you go, it is
  // something you do to the board you are already looking at.
  //
  // The view and the game it is looking at live in the URL, so a replay of one
  // market is a link someone can send rather than a set of clicks to describe.
  const view = readView(params.get('view'))
  const replayGame = params.get('game')
  const rffGame = params.get('rff')
  const [replayBook, setReplayBook] = useState<string | null>(null)
  const setParam = useCallback((key: string, value: string | null) => {
    setParams((old) => {
      const next = new URLSearchParams(old)
      if (value === null) next.delete(key)
      else next.set(key, value)
      return next
    }, { replace: true })
  }, [setParams])
  const setView = useCallback((next: View) => setParam('view', next === 'PRICES' ? null : next), [setParam])
  const day = params.get('date') ?? new Date().toISOString().slice(0, 10)

  // Totals and Parlay Watch state. Filters live in the URL for the same reason
  // the replay does: a board someone is looking at should be a link they can
  // send, not a set of clicks they have to describe.
  const oversOnly = params.get('overs') === '1'
  const legCount: 2 | 3 = params.get('legs') === '3' ? 3 : 2
  const marketFilter = readMarketFilter(params.get('combo'))
  const [scenarios, setScenarios] = useState<Record<string, string>>({})
  const setScenario = useCallback((gameId: string, line: string) => {
    setScenarios((old) => ({ ...old, [gameId]: line }))
  }, [])
  const totals = useMlbTotals({
    day, enabled: view === 'TOTALS' && sport !== 'NFL', oversOnly, scenarios,
  })
  const parlay = useParlayWatch({
    day, enabled: view === 'PARLAY' && sport !== 'NFL', legCount, marketFilter,
  })
  const {
    recordTicket, recordingKey, recordedByKey, recordError,
  } = useRecordTicket(() => parlay.refresh())

  const loader = useCallback(async (signal: AbortSignal): Promise<MarketsBoard> => {
    const warnings: string[] = []
    const safe = async <T,>(name: string, run: () => Promise<T>): Promise<T | null> => {
      try { return await run() } catch (error) {
        if (signal.aborted) throw error
        warnings.push(`${name} unavailable`)
        return null
      }
    }
    const wantsMlb = sport === 'ALL' || sport === 'MLB'
    const wantsNfl = sport === 'ALL' || sport === 'NFL'
    const [books, crowd, weeks] = await Promise.all([
      wantsMlb ? safe('Sportsbook board', () => apiRequest<MarketBoardData>(`/markets/board?date=${day}`, {}, signal)) : null,
      wantsMlb ? safe('Prediction markets', () => endpoints.marketPulse(day, signal, 'MLB')) : null,
      wantsNfl ? safe('NFL schedule', () => endpoints.nflWeeks(signal)) : null,
    ])
    const season = Number(params.get('season')) || weeks?.current?.season
    const week = Number(params.get('week')) || weeks?.current?.week
    const nfl = season && week
      ? await safe('NFL markets', () => apiRequest<NflBoard>(`/nfl/markets/${season}/${week}`, {}, signal))
      : null
    return { data_mode: 'live', scope: sport, date: day, books, crowd, weeks, nfl, warnings }
  }, [sport, day, params])

  const resource = useApiResource(loader, {
    key: cacheKey('unified/markets', {
      sport, date: day, season: params.get('season'), week: params.get('week'),
    }),
    staleTimeMs: STALE_TIME.slate,
    refreshIntervalMs: STALE_TIME.slate,
  })
  const board = resource.data?.scope === sport ? resource.data : null
  const now = board?.nfl?.now_utc ? new Date(board.nfl.now_utc).getTime() : Date.now()

  const mlbRows = useMemo(() => {
    const crowdRows = (board?.crowd?.rows ?? []).filter((row) => (row.league ?? row.sport) === 'MLB')
    return crowdRows
      .map((row) => ({
        row,
        books: (board?.books?.rows ?? []).filter((entry) => entry.game_id === row.game_id
          && entry.market_type === BOARD_MARKET[family]),
      }))
      .filter(({ row }) => matches(row.away_team, row.home_team, row.away_abbrev, row.home_abbrev, query))
      .sort((a, b) => (a.row.starts_at ?? '').localeCompare(b.row.starts_at ?? ''))
  }, [board, family, query])

  const nflRows = useMemo(
    () => (board?.nfl?.events ?? [])
      .filter((event) => matches(event.away.name, event.home.name, event.away.abbreviation, event.home.abbreviation, query))
      .sort((a, b) => (a.kickoff_utc ?? '').localeCompare(b.kickoff_utc ?? '')),
    [board, query],
  )

  const total = (sport === 'NFL' ? 0 : mlbRows.length) + (sport === 'MLB' ? 0 : nflRows.length)

  /**
   * Replay can only open a game the board is already showing, and it opens on
   * a game that has a tape: defaulting to the earliest kickoff put an NFL game
   * with no persisted price at the top, so the first thing the feature showed
   * was its own empty state.
   */
  const replayChoices = useMemo<ReplayChoice[]>(() => [
    ...(sport === 'NFL' ? [] : mlbRows.map(({ row, books }) => ({
      gameId: row.game_id, league: 'MLB' as const,
      label: `${row.away_abbrev} @ ${row.home_abbrev}`, startsAt: row.starts_at ?? '',
      hasQuote: books.some((entry) => (entry.books ?? []).length > 0),
    }))),
    ...(sport === 'MLB' ? [] : nflRows
      .filter((event) => event.canonical_game_id)
      .map((event) => {
        const engine = event.engines.find((entry) => entry.engine_id === 'NFL_M1_XGBOOST')
          ?? event.engines[0]
        return {
          gameId: event.canonical_game_id as string, league: 'NFL' as const,
          label: `${event.away.abbreviation} @ ${event.home.abbreviation}`,
          startsAt: event.kickoff_utc ?? '',
          hasQuote: (engine?.market_comparison?.markets ?? []).some((block) => Boolean(block.quote)),
        }
      })),
  ].sort((a, b) => Number(b.hasQuote) - Number(a.hasQuote) || a.startsAt.localeCompare(b.startsAt)),
  [mlbRows, nflRows, sport])

  return (
    <div className="bx bx-markets">
      <div className="bx__inner">
        <header className="bx-head">
          <div>
            <span className="bx-eyebrow"><Radio size={13} aria-hidden="true" /> Markets &amp; Crowd</span>
            <h1>The <em>price</em> of the game</h1>
            <p className="bx-muted" style={{ marginTop: 8, maxWidth: '60ch' }}>
              Model expectation, best available price, book consensus and the spread between books —
              one row per game. Sportsbooks and prediction markets stay separate.
            </p>
          </div>
          <div className="bx-head__meta">
            {sport !== 'NFL' && (
              <label className="bx-field">Date
                <input type="date" aria-label="MLB market date" value={day}
                  onChange={(event) => setParams((old) => {
                    const q = new URLSearchParams(old); q.set('date', event.target.value); return q
                  }, { replace: true })} />
              </label>
            )}
            {sport !== 'MLB' && board?.weeks && (
              <label className="bx-field">Week
                <select aria-label="NFL market week"
                  value={board.nfl ? `${board.nfl.season}-${board.nfl.week}` : ''}
                  onChange={(event) => {
                    const [season, week] = event.target.value.split('-')
                    setParams((old) => {
                      const q = new URLSearchParams(old); q.set('season', season); q.set('week', week); return q
                    }, { replace: true })
                  }}>
                  {board.weeks.weeks.map((entry) => (
                    <option key={`${entry.season}-${entry.week}`} value={`${entry.season}-${entry.week}`}>
                      {entry.season} · Week {entry.week}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button type="button" className="bx-btn" onClick={resource.refresh}>
              <RefreshCw size={14} aria-hidden="true" /> Reload stored evidence
            </button>
          </div>
        </header>

        <div className="bx-segment bx-segment--views" role="group" aria-label="Market view">
          {VIEWS.map((entry) => (
            <button key={entry.key} type="button" aria-pressed={view === entry.key}
              title={entry.hint} onClick={() => setView(entry.key)}>
              {entry.key === 'REPLAY' && <History size={13} aria-hidden="true" />}
              {entry.key === 'RFF' && <Flame size={13} aria-hidden="true" />}
              {entry.key === 'TOTALS' && <Sigma size={13} aria-hidden="true" />}
              {entry.key === 'PARLAY' && <Layers size={13} aria-hidden="true" />}
              {entry.label}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="bx-segment" role="group" aria-label="Market">
            {FAMILIES.map((value) => (
              <button key={value} type="button" aria-pressed={family === value} onClick={() => setFamily(value)}>
                {sport === 'NFL' ? FAMILY_LABEL.NFL[value]
                  : sport === 'MLB' ? FAMILY_LABEL.MLB[value]
                    : value === 'MONEYLINE' ? 'Moneyline' : value === 'SPREAD' ? 'Run line / spread' : 'Total'}
              </button>
            ))}
          </div>
          <label className="bx-field">Find
            <input value={query} onChange={(event) => setQuery(event.target.value)}
              placeholder="Team or abbreviation" aria-label="Find a team" />
          </label>
          <span className="bx-dim">{total} game{total === 1 ? '' : 's'} on the board</span>
          {board?.warnings?.length ? <span className="bx-dim" role="status">{board.warnings.join(' · ')}</span> : null}
        </div>

        <p className="bx-dim">
          Reloading this page makes no provider call. A price is persisted evidence with its own
          observation time — never a wagering-eligibility decision. A game's status and a quote's
          status are separate: a started or finished game still shows the last price recorded
          <em> before</em> first pitch, and the age below is when that price was recorded, not when
          this page last refreshed. "Within the freshness window" means observed within{' '}
          {FRESH_MINUTES / 60} hours of this read. The model probability compared here is the
          <b> forecast that governs each game</b>, chosen by the same rule the slate and the
          simulator use — so one game shows one model's number everywhere. Each row names the
          engine that supplied its figure and any condition attached to it; two engines' numbers
          are never mixed into one comparison.
        </p>

        {resource.loading && !board && (
          <>
            <SkeletonCards count={3} />
            <SkeletonTable rows={8} columns={6} ariaLabel="Loading persisted market evidence" />
          </>
        )}
        {resource.error && !board && <p className="bx-empty" role="alert">{resource.error}</p>}
        {board && (
          <Freshness label="Prices" updatedAt={resource.updatedAt} refreshing={resource.refreshing}
            stale={resource.stale} error={resource.error} onRetry={resource.refresh} />
        )}

        {view === 'PRICES' && sport !== 'NFL' && (
          <Panel eyebrow={`MLB · ${FAMILY_LABEL.MLB[family]}`} title="Baseball prices"
            action={<span className="bx-dim">{mlbRows.length} game{mlbRows.length === 1 ? '' : 's'}</span>}>
            {mlbRows.length === 0
              ? <p className="bx-empty">No MLB game with persisted evidence matches this date and filter.</p>
              : <div className="bx-mkt">
                {mlbRows.map(({ row, books }) => (
                  <MlbMarketRow key={row.game_id} row={row} books={books} family={family}
                    now={now} withSport={withSport} onReplay={(id) => { setParam('game', id); setView('REPLAY') }} />
                ))}
              </div>}
          </Panel>
        )}

        {view === 'PRICES' && sport !== 'MLB' && (
          <Panel eyebrow={`NFL · ${FAMILY_LABEL.NFL[family]}`} title="Football prices"
            action={<span className="bx-dim">{nflRows.length} game{nflRows.length === 1 ? '' : 's'} · research shadow</span>}>
            {nflRows.length === 0
              ? <p className="bx-empty">No NFL game with persisted evidence matches this week and filter.</p>
              : <div className="bx-mkt">
                {nflRows.map((event) => (
                  <NflMarketRow key={event.event_id} event={event} family={family} now={now}
                    withSport={withSport} onReplay={(id) => { setParam('game', id); setView('REPLAY') }} />
                ))}
              </div>}
          </Panel>
        )}

        {view === 'TOTALS' && (
          <Panel
            eyebrow="MLB · Total runs"
            title="Over / Under"
            action={<span className="bx-dim">V18 distribution vs recorded line</span>}
          >
            <MlbTotalsView
              board={totals.data}
              oversOnly={oversOnly}
              onOversOnly={(next) => setParam('overs', next ? '1' : null)}
              scenarios={scenarios}
              onScenario={setScenario}
            />
          </Panel>
        )}

        {view === 'PARLAY' && (
          <Panel
            eyebrow="MLB · Research combinations"
            title="Parlay Watch"
            action={<span className="bx-dim">No wagering authority</span>}
          >
            <ParlayWatchView
              watch={parlay.data}
              legCount={legCount}
              onLegCount={(next) => setParam('legs', String(next))}
              marketFilter={marketFilter}
              onMarketFilter={(next) => setParam('combo', next === 'ALL' ? null : next)}
              onRecord={recordTicket}
              recordingKey={recordingKey}
              recordedByKey={recordedByKey}
              recordError={recordError}
            />
          </Panel>
        )}

        {view === 'REPLAY' && (
          <ReplayView
            family={family}
            games={replayChoices}
            gameId={replayGame ?? replayChoices[0]?.gameId ?? null}
            onSelectGame={(id) => { setParam('game', id); setReplayBook(null) }}
            selectedBook={replayBook}
            onSelectBook={setReplayBook}
          />
        )}

        {view === 'RFF' && (
          <RffView day={day} league={sport === 'MLB' || sport === 'NFL' ? sport : null} family={family}
            selected={rffGame} onSelect={(id) => setParam('rff', id)} />
        )}
      </div>
    </div>
  )
}

/** "Betmgm (SportsGameOdds, research)" -> "Betmgm"; the provenance badge carries the rest. */
export function bookName(book: string): string {
  return book.replace(/\s*\(.*\)\s*$/, '').trim()
}

function matches(...values: (string | null | undefined)[]): boolean {
  const needle = String(values[values.length - 1] ?? '').toLowerCase().trim()
  if (!needle) return true
  return values.slice(0, -1).some((value) => (value ?? '').toLowerCase().includes(needle))
}

/* -- MLB ------------------------------------------------------------------ */

/**
 * The collapsed row's comparison, as labelled values rather than a picture.
 *
 * Every value names its team or contract. A missing value is rendered as an
 * explicit unavailable state with the reason beside it, because a blank cell
 * and a zero look the same at a glance and neither is the truth.
 */
function ComparisonSummary({
  selectionLabel, modelLabel, modelValue, consensusLabel, consensusValue,
  gap, forecastNote, bestLabel, bestValue, bestBook, bestSource, observed, comparability,
}: {
  selectionLabel: string
  modelLabel: string
  modelValue: string
  consensusLabel: string
  consensusValue: string
  gap: { text: string; tone: 'up' | 'down' | 'flat' } | null
  forecastNote: string | null
  bestLabel: string
  bestValue: string
  bestBook: string
  bestSource: ReactNode
  observed: string
  comparability: string | null
}) {
  return (
    <div className="bx-mkt__compare">
      <span className="bx-mkt__compare-selection">{selectionLabel}</span>
      <dl className="bx-mkt__facts">
        <div>
          <dt>{modelLabel}</dt>
          <dd className="bx-num">{modelValue}</dd>
        </div>
        <div>
          <dt>{consensusLabel}</dt>
          <dd className="bx-num">{consensusValue}</dd>
        </div>
        <div>
          <dt>Model &minus; consensus</dt>
          <dd className={`bx-num${gap ? ` bx-gap--${gap.tone}` : ''}`}>
            {gap ? gap.text : 'unavailable'}
          </dd>
        </div>
        <div>
          <dt>{bestLabel}</dt>
          <dd className="bx-num">{bestValue}</dd>
          <dd className="bx-mkt__facts-note">
            {bestBook}
            {bestSource && <span className="bx-mkt__facts-source">{bestSource}</span>}
          </dd>
        </div>
        <div>
          <dt>Sportsbook price age</dt>
          <dd className="bx-mkt__facts-note">{observed}</dd>
        </div>
        {forecastNote && (
          <div>
            <dt>Forecast</dt>
            <dd className="bx-mkt__facts-note">{forecastNote}</dd>
          </div>
        )}
      </dl>
      {comparability && <p className="bx-mkt__compare-note">{comparability}</p>}
    </div>
  )
}


function MlbMarketRow({
  row, books, family, now, withSport, onReplay,
}: {
  row: MarketPulseRow; books: MarketBoardRow[]; family: Family; now: number
  withSport: (href: string, sport?: 'MLB' | 'NFL') => string
  onReplay: (gameId: string) => void
}) {
  const [open, setOpen] = useState(false)
  // The home selection is the one whose price is comparable to a home-win
  // probability. For run line and total there is no such mapping, so the row
  // reports prices only and draws its plot on the price axis.
  const homeRow = books.find((entry) => entry.selection === row.home_team)
    ?? books.find((entry) => entry.selection?.toLowerCase().includes('over'))
    ?? books[0]
  const prices = (homeRow?.books ?? []).map((entry) => entry.price).filter((price) => Number.isFinite(price))
  const best = homeRow?.books?.find((entry) => entry.best) ?? null
  const observedAt = homeRow?.observed_at ?? null
  const fresh = isFresh(observedAt, now)
  const crowd = (row.providers ?? []).filter((provider) => provider.midpoint != null)
  // Game status and quote status are different facts. A started game's last
  // book price is a pregame record, and a page that refreshed a moment ago does
  // not make a four-hour-old quote live.
  const gameStarted = hasStarted(row.status)
  const priceAge = observedAt
    ? `${gameStarted ? 'Pregame price · ' : ''}last recorded ${agoOf(observedAt, now)}`
      + (!gameStarted && fresh === false ? ' · outside the freshness window' : '')
    : 'Observation time not recorded'

  const marks: PlotMark[] = []
  let range = null as null | { low: number; high: number; label: string }
  let domain: [number, number] = [0, 1]
  let format = (value: number) => pct(value, 0)
  let caption = ''

  if (family === 'MONEYLINE') {
    const implied = prices.map((price) => impliedProbability(price)).filter((value): value is number => value != null)
    marks.push({ key: 'model', label: 'Model', value: row.aisp_home_probability ?? NaN, color: 'var(--bx-accent)' })
    marks.push({
      key: 'book', label: 'Sportsbook no-vig', value: row.sportsbook_home_probability ?? NaN, color: 'var(--bx-nfl)',
    })
    for (const provider of crowd) {
      marks.push({
        key: provider.provider,
        label: provider.provider === 'KALSHI' ? 'Kalshi' : provider.provider === 'POLYMARKET' ? 'Polymarket' : provider.provider,
        value: provider.midpoint ?? NaN,
        color: provider.provider === 'KALSHI' ? 'var(--bx-good)' : '#b985ff',
      })
    }
    if (implied.length > 1) {
      range = { low: Math.min(...implied), high: Math.max(...implied), label: 'Individual books (margin included)' }
    }
    const values = [...marks.map((mark) => mark.value), ...(range ? [range.low, range.high] : [])]
      .filter((value) => Number.isFinite(value))
    const low = Math.min(...values, 0.5), high = Math.max(...values, 0.5)
    const pad = Math.max(0.04, (high - low) * 0.4)
    domain = [Math.max(0, low - pad), Math.min(1, high + pad)]
    caption = `${row.home_abbrev} home-win probability. The book band carries each book's margin; the no-vig point does not.`
  } else {
    const model = homeRow?.asp_fair ?? null
    const consensus = homeRow?.consensus ?? null
    marks.push({ key: 'model', label: 'Model fair price', value: model ?? NaN, color: 'var(--bx-accent)' })
    marks.push({ key: 'consensus', label: 'Consensus', value: consensus ?? NaN, color: 'var(--bx-nfl)' })
    if (prices.length > 1) range = { low: Math.min(...prices), high: Math.max(...prices), label: 'Book range' }
    const values = [...marks.map((mark) => mark.value), ...(range ? [range.low, range.high] : [])]
      .filter((value) => Number.isFinite(value))
    if (values.length === 0) { domain = [-200, 200] } else {
      const low = Math.min(...values), high = Math.max(...values)
      const pad = Math.max(20, (high - low) * 0.35)
      domain = [low - pad, high + pad]
    }
    format = (value: number) => american(Math.round(value))
    caption = `American price for ${homeRow?.selection ?? 'the recorded selection'}${homeRow?.line != null ? ` ${signedPoints(homeRow.line, 1)}` : ''}.`
  }

  // Which forecast this comparison uses. Resolved server-side by the shared
  // selector, so this row cannot compare a different model from the slate.
  const selected = row.forecast_selection?.primary ?? null
  // The engine that actually produced the number compared here, which is not
  // always the selector's primary: when the controlling engine's forecast is
  // unusable the comparison falls back, and the label must follow the number.
  // `displayed_engine_id` is the field the slate reads too, so both surfaces
  // name the engine the same way. The row-level field stays as a fallback for
  // a payload produced before it existed.
  // No fallback to the SELECTED engine: that is what made an unavailable row
  // still claim "V18 MODEL" beside an empty value. Null here means no engine
  // supplied a number, which is exactly what the row must say.
  const comparisonEngineId = row.forecast_selection?.displayed_engine_id
    ?? row.aisp_comparison_engine_id ?? null
  // Falls back to the SELECTED engine's name only for the column heading, which
  // has to say something; the value under it is "—" when nothing was published.
  const selectedEngine = engineShortName(comparisonEngineId ?? selected?.engine_id)
  const selectionNote = availabilityNote(selected)

  // What the row is actually comparing, named rather than implied.
  const selectionLabel = family === 'MONEYLINE'
    ? `${row.home_abbrev} win`
    : `${homeRow?.selection ?? 'recorded selection'}${homeRow?.line != null ? ` ${signedPoints(homeRow.line, 1)}` : ''}`

  // Model minus consensus, on the one basis both are expressed in. For a run
  // line or a total the two are prices, and they are only comparable when they
  // are the same selection at the same line -- which the backend guarantees by
  // construction here, because both come from the same `homeRow`.
  const gap: { text: string; tone: 'up' | 'down' | 'flat' } | null = (() => {
    if (family === 'MONEYLINE') {
      const model = row.aisp_home_probability
      const consensus = row.sportsbook_home_probability
      if (model == null || consensus == null) return null
      const points = (model - consensus) * 100
      return {
        text: `${points > 0 ? '+' : ''}${points.toFixed(1)} pp`,
        tone: points > 0.05 ? 'up' : points < -0.05 ? 'down' : 'flat',
      }
    }
    const model = homeRow?.asp_fair
    const consensus = homeRow?.consensus
    if (model == null || consensus == null) return null
    const diff = model - consensus
    return {
      text: `${diff > 0 ? '+' : ''}${Math.round(diff)}`,
      tone: diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat',
    }
  })()

  // Model, version and the two clocks, compactly. Never the refresh time.
  //
  // No engine is named when none supplied a number. The removed branch read
  // "Gen 1 baseline shown — V18 is not currently usable" above a Gen 1 figure;
  // the primary fields now stay empty instead, so this row and the slate agree
  // about a game the controlling engine said nothing on.
  const unavailable = !comparisonEngineId
  const forecastIdentityNote = selected
    ? (unavailable
      ? [
        `${engineShortName(selected.engine_id)} forecast unavailable`,
        selectionNote || null,
      ].filter(Boolean).join(' · ')
      : [
        `${selected.model_label}`,
        selected.source_cutoff_utc ? `evidence to ${agoOf(selected.source_cutoff_utc, now)}` : null,
        selected.published_utc ? `published ${agoOf(selected.published_utc, now)}` : null,
        selectionNote || null,
      ].filter(Boolean).join(' · '))
    : null

  // Why a comparison is missing, when it is.
  const comparability = family === 'MONEYLINE'
    ? (row.aisp_home_probability == null
        ? 'The controlling engine published no forecast for this game, so there '
          + 'is nothing to compare. No other model is substituted here.'
        : row.sportsbook_home_probability == null
          ? 'No complete two-sided book quote supports a no-vig consensus, so no comparison is shown.'
          : null)
    : (homeRow == null
        ? 'No recorded selection at a comparable line, so no comparison is shown.'
        : homeRow.asp_fair == null || homeRow.consensus == null
          ? `Only one side of ${selectionLabel} is recorded; a model-versus-market gap needs both at the same line.`
          : null)

  return (
    <>
      <article className={`bx-mkt__row${open ? ' bx-mkt__row--open' : ''}`}
        aria-label={`${row.away_team} at ${row.home_team}`}>
        <div className="bx-mkt__when">
          <b>{clockOf(row.starts_at)}</b>
          <small>{(row.status ?? '').toLowerCase()}</small>
        </div>
        <div className="bx-mkt__teams">
          <b className="bx-mkt__crests">
            <TeamLogo league="MLB" abbreviation={row.away_abbrev} size={32} />
            <span>{row.away_abbrev}</span>
            <i>at</i>
            <TeamLogo league="MLB" abbreviation={row.home_abbrev} size={32} />
            <span>{row.home_abbrev}</span>
          </b>
          <small>
            {teamIdentity('MLB', row.away_abbrev).name} at {teamIdentity('MLB', row.home_abbrev).name}
          </small>
        </div>
        {/* The collapsed row used to carry an unlabelled miniature plot. It is
            now the same facts as words: which selection, what the model says,
            what the books say, how far apart they are, and the best recorded
            price with its age. The plot moved into the drawer, where it has a
            title and a legend. */}
        <ComparisonSummary
          selectionLabel={selectionLabel}
          modelLabel={family === 'MONEYLINE' ? `${selectedEngine} model` : `${selectedEngine} model fair price`}
          modelValue={family === 'MONEYLINE' ? pct(row.aisp_home_probability, 1) : american(homeRow?.asp_fair)}
          consensusLabel={family === 'MONEYLINE' ? 'Sportsbook consensus' : 'Sportsbook consensus price'}
          consensusValue={family === 'MONEYLINE' ? pct(row.sportsbook_home_probability, 1) : american(homeRow?.consensus)}
          gap={gap}
          forecastNote={forecastIdentityNote}
          bestLabel={best ? `Best recorded ${selectionLabel} price` : 'Best recorded price'}
          bestValue={best ? american(best.price) : 'unavailable'}
          bestBook={best ? bookName(best.book) : 'no complete two-sided quote is persisted'}
          // The provider-implementation badge (SGO FALLBACK and friends) lives
          // in the drawer's source details. The book name and the price are
          // what a reader needs here.
          bestSource={null}
          observed={priceAge}
          comparability={comparability}
        />
        <button type="button" className="bx-mkt__toggle" aria-expanded={open}
          aria-label={`${open ? 'Hide' : 'Show'} bookmaker detail for ${row.away_abbrev} at ${row.home_abbrev}`}
          onClick={() => setOpen((value) => !value)}>
          <ChevronDown size={16} style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform .18s' }} />
        </button>
      </article>
      {open && (
        <div className="bx-mkt__drawer">
          <div className="bx-mkt__drawer-grid">
            <div>
              <h4 style={{ margin: '10px 0 8px', fontSize: 13 }}>Every persisted book price</h4>
              {books.length === 0
                ? <p className="bx-dim">No {BOARD_MARKET[family].toLowerCase()} price is persisted for this game.</p>
                : <div className="bx-scroll">
                  <table className="bx-table">
                    <thead>
                      <tr>
                        <th scope="col">Selection</th><th scope="col">Book</th><th scope="col">Price</th>
                        <th scope="col">Model fair</th><th scope="col">Consensus</th><th scope="col">Source</th>
                      </tr>
                    </thead>
                    <tbody>
                      {books.flatMap((entry) => (entry.books ?? []).map((quote) => (
                        <tr key={`${entry.id}:${quote.book}`}>
                          <th scope="row">{entry.selection}{entry.line != null ? ` ${signedPoints(entry.line, 1)}` : ''}</th>
                          <td style={{ textAlign: 'left' }}>{bookName(quote.book)}</td>
                          <td className="bx-num" style={{ fontWeight: quote.best ? 800 : 500, color: quote.best ? 'var(--bx-good)' : undefined }}>
                            {american(quote.price)}
                          </td>
                          <td className="bx-num">{american(entry.asp_fair)}</td>
                          <td className="bx-num">{american(entry.consensus)}</td>
                          <td style={{ textAlign: 'left' }}><SourceBadge source={quote.book} /></td>
                        </tr>
                      )))}
                    </tbody>
                  </table>
                </div>}
              <p className="bx-dim" style={{ marginTop: 8 }}>
                Stored book references. A consensus may include an implied price without a paired
                no-vig quote, so no new edge is inferred from it.
              </p>
            </div>
            <div>
              <h4 style={{ margin: '10px 0 8px', fontSize: 13 }}>Prediction markets</h4>
              {family !== 'MONEYLINE'
                ? <p className="bx-dim">
                  Kalshi and Polymarket publish home-win contracts. They are not run-line or total
                  prices and are not shown on this tab.
                </p>
                : <CrowdTable
                    providers={crowd} home={row.home_abbrev ?? 'Home'}
                    away={row.away_abbrev ?? 'Away'}
                    homeProbability={row.aisp_home_probability}
                    now={now} note={row.consensus_note} />}

              {/* The detailed plot lives here, with a title and its legend,
                  rather than unlabelled in the collapsed row. */}
              <h4 style={{ margin: '16px 0 6px', fontSize: 13 }}>
                {family === 'MONEYLINE'
                  ? `${row.home_abbrev} home-win probability by source`
                  : `${selectionLabel} price by source`}
              </h4>
              <ComparisonPlot marks={marks} range={range} domain={domain} format={format} />
              <PlotLegend marks={marks} range={range} />
              <p className="bx-dim" style={{ marginTop: 6 }}>{caption}</p>
              <details className="bx-mkt__sources">
                <summary>Source details and provenance</summary>
                <p className="bx-dim" style={{ marginTop: 6 }}>
                  Sportsbook observation {observedAt ? agoOf(observedAt, now) : 'not recorded'}.{' '}
                  {row.display_note ?? ''}
                </p>
                {best && (
                  <p className="bx-dim" style={{ marginTop: 6, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    Best recorded price provider: {bookName(best.book)}
                    <SourceBadge source={best.book} />
                  </p>
                )}
              </details>
              <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                <button type="button" className="bx-btn bx-btn--primary" onClick={() => onReplay(row.game_id)}>
                  <History size={13} aria-hidden="true" /> Replay this market
                </button>
                <Link className="bx-btn" to={withSport(`/games/${row.game_id}`, 'MLB')}>Game analysis <ArrowRight size={13} /></Link>
                <Link className="bx-btn" to={withSport(`/games/${row.game_id}/simulation`, 'MLB')}>Diamond Simulator <ArrowRight size={13} /></Link>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

/* -- NFL ------------------------------------------------------------------ */

function NflMarketRow({
  event, family, now, withSport, onReplay,
}: {
  event: NflEvent; family: Family; now: number
  withSport: (href: string, sport?: 'MLB' | 'NFL') => string
  onReplay: (gameId: string) => void
}) {
  const [open, setOpen] = useState(false)
  const engine = event.engines.find((entry) => entry.engine_id === 'NFL_M1_XGBOOST') ?? event.engines[0]
  const block: NflMarketBlock | undefined = engine?.market_comparison?.markets
    .find((entry) => entry.market_type === family)
  const observedAt = block?.observed_at ?? null
  const fresh = block ? Boolean(block.fresh) : null
  const modelHome = conditionalHomeWin(engine)
  const crowd = ((event as NflEvent & {
    prediction_market?: { providers: PredictionProviderProbability[] }
  }).prediction_market?.providers ?? []).filter((provider) => provider.midpoint != null)

  const marks: PlotMark[] = []
  let range = null as null | { low: number; high: number; label: string }
  let domain: [number, number] = [0, 1]
  let format = (value: number) => pct(value, 0)
  let caption = ''

  if (family === 'MONEYLINE') {
    marks.push({ key: 'model', label: 'Model (no tie)', value: modelHome ?? NaN, color: 'var(--bx-accent)' })
    marks.push({ key: 'novig', label: 'Sportsbook no-vig', value: block?.market_no_vig?.a ?? NaN, color: 'var(--bx-nfl)' })
    for (const provider of crowd) {
      marks.push({
        key: provider.provider,
        label: provider.provider === 'KALSHI' ? 'Kalshi' : 'Polymarket',
        value: provider.midpoint ?? NaN,
        color: provider.provider === 'KALSHI' ? 'var(--bx-good)' : '#b985ff',
      })
    }
    const implied = [block?.quote?.a?.american]
      .map((price) => impliedProbability(price)).filter((value): value is number => value != null)
    if (implied.length) range = { low: Math.min(...implied), high: Math.max(...implied), label: 'Quoted book (margin included)' }
    const values = [...marks.map((mark) => mark.value), ...(range ? [range.low, range.high] : [])]
      .filter((value) => Number.isFinite(value))
    const low = Math.min(...values, 0.5), high = Math.max(...values, 0.5)
    const pad = Math.max(0.05, (high - low) * 0.4)
    domain = [Math.max(0, low - pad), Math.min(1, high + pad)]
    caption = `${event.home.abbreviation} home-win probability, conditional on no tie. The model's ${pct(engine?.tie_probability, 1)} tie is its own final-score tie, not an overtime probability.`
  } else {
    const modelValue = family === 'SPREAD' ? -(engine?.expected_margin_home ?? NaN) : engine?.expected_total_score ?? NaN
    marks.push({ key: 'model', label: family === 'SPREAD' ? 'Model implied handicap' : 'Model expected total', value: modelValue, color: 'var(--bx-accent)' })
    marks.push({ key: 'book', label: 'Book line', value: block?.line ?? NaN, color: 'var(--bx-nfl)' })
    const values = marks.map((mark) => mark.value).filter((value) => Number.isFinite(value))
    if (values.length === 0) domain = family === 'SPREAD' ? [-14, 14] : [30, 60]
    else {
      const low = Math.min(...values), high = Math.max(...values)
      const pad = Math.max(3, (high - low) * 0.6)
      domain = [low - pad, high + pad]
    }
    format = (value: number) => family === 'SPREAD' ? signedPoints(value, 1) : decimals(value, 1)
    caption = family === 'SPREAD'
      ? 'Points. The model figure is its mean-implied home handicap, not a quoted line.'
      : 'Points. The model figure is its expected total, not a quoted line.'
  }

  return (
    <>
      <article className={`bx-mkt__row${open ? ' bx-mkt__row--open' : ''}`}
        aria-label={`${event.away.name} at ${event.home.name}`}>
        <div className="bx-mkt__when">
          <b>{clockOf(event.kickoff_utc)}</b>
          <small>{(event.state ?? '').toLowerCase()}</small>
        </div>
        <div className="bx-mkt__teams">
          <b className="bx-mkt__crests">
            <TeamLogo league="NFL" abbreviation={event.away.abbreviation} size={32} />
            <span>{event.away.abbreviation}</span>
            <i>at</i>
            <TeamLogo league="NFL" abbreviation={event.home.abbreviation} size={32} />
            <span>{event.home.abbreviation}</span>
          </b>
          <small>
            {teamIdentity('NFL', event.away.abbreviation).name} at{' '}
            {teamIdentity('NFL', event.home.abbreviation).name}
          </small>
        </div>
        <div className="bx-mkt__model">
          <b>{family === 'MONEYLINE' ? pct(modelHome, 1)
            : family === 'SPREAD' ? signedPoints(-(engine?.expected_margin_home ?? NaN), 1)
              : decimals(engine?.expected_total_score, 1)}</b>
          <small>model</small>
        </div>
        <div style={{ minWidth: 0 }}>
          <ComparisonPlot marks={marks} range={range} domain={domain} format={format} />
        </div>
        <div className="bx-mkt__best">
          <b>{block?.quote ? `${american(block.quote.b.american)} / ${american(block.quote.a.american)}` : 'no price'}</b>
          <small>{block?.book ? bookName(block.book) : 'no same-book two-sided quote is persisted'}</small>
          <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
            {block?.book && <SourceBadge source={block.book} />}
            {fresh != null && (
              <span className={`bx-source ${fresh ? 'bx-source--kalshi' : 'bx-source--sgo'}`}>
                {fresh ? 'fresh' : 'stale'}{observedAt ? ` ${agoOf(observedAt, now)}` : ''}
              </span>
            )}
          </span>
        </div>
        <div className="bx-mkt__model">
          <b>{family === 'MONEYLINE' ? pct(block?.market_no_vig?.a, 1)
            : block?.line != null ? (family === 'SPREAD' ? signedPoints(block.line, 1) : decimals(block.line, 1)) : '—'}</b>
          <small>{family === 'MONEYLINE' ? 'no-vig' : 'book line'}</small>
        </div>
        <button type="button" className="bx-mkt__toggle" aria-expanded={open}
          aria-label={`${open ? 'Hide' : 'Show'} bookmaker detail for ${event.away.abbreviation} at ${event.home.abbreviation}`}
          onClick={() => setOpen((value) => !value)}>
          <ChevronDown size={16} style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform .18s' }} />
        </button>
      </article>
      {open && (
        <div className="bx-mkt__drawer">
          <div className="bx-mkt__drawer-grid">
            <div>
              <h4 style={{ margin: '10px 0 8px', fontSize: 13 }}>Quoted market</h4>
              {!block?.quote
                ? <p className="bx-dim">
                  {block?.reason ?? 'No same-book two-sided quote is persisted, so no edge is calculated.'}
                </p>
                : <div className="bx-scroll">
                  <table className="bx-table">
                    <thead>
                      <tr>
                        <th scope="col">Side</th><th scope="col">Line</th><th scope="col">Price</th>
                        <th scope="col">No-vig</th><th scope="col">Model</th><th scope="col">Model − market</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(['a', 'b'] as const).map((side) => {
                        const quote = block.quote![side]
                        const label = quote.side === 'HOME' ? event.home.abbreviation
                          : quote.side === 'AWAY' ? event.away.abbreviation
                            : quote.side === 'OVER' ? 'Over' : 'Under'
                        const model = block.model_comparable?.[side] ?? null
                        const novig = block.market_no_vig?.[side] ?? null
                        const gap = block.comparison_available && model != null && novig != null ? model - novig : null
                        return (
                          <tr key={side}>
                            <th scope="row">{label}</th>
                            <td className="bx-num">{quote.point == null ? '—' : signedPoints(quote.point, 1)}</td>
                            <td className="bx-num">{american(quote.american)}</td>
                            <td className="bx-num">{pct(novig, 1)}</td>
                            <td className="bx-num">{pct(model, 1)}</td>
                            <td className="bx-num">{gap == null ? '—' : `${signedPoints(gap * 100, 1)} pp`}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>}
              <p className="bx-dim" style={{ marginTop: 8 }}>
                {block?.books_with_two_sided_quote != null
                  ? `${block.books_with_two_sided_quote} book(s) had a two-sided quote at this observation. `
                  : ''}
                {block?.comparison_available ? '' : block?.reason ?? ''}
              </p>
            </div>
            <div>
              <h4 style={{ margin: '10px 0 8px', fontSize: 13 }}>Prediction markets</h4>
              {family !== 'MONEYLINE'
                ? <p className="bx-dim">
                  Home-win contracts are not spread or total prices and are not shown on this tab.
                </p>
                : <CrowdTable providers={crowd} home={event.home.abbreviation} now={now} note={null} />}
              <PlotLegend marks={marks} range={range} />
              <p className="bx-dim" style={{ marginTop: 6 }}>{caption}</p>
              <p className="bx-dim" style={{ marginTop: 8 }}>
                {engine?.model_label ?? 'model not published'} · research shadow, no wagering authority.
                Source evidence {agoOf(engine?.timestamps?.source_evidence_utc ?? null, now) ?? 'not recorded'}.
              </p>
              {event.canonical_game_id && (
                <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                  <button type="button" className="bx-btn bx-btn--primary"
                    onClick={() => onReplay(event.canonical_game_id as string)}>
                    <History size={13} aria-hidden="true" /> Replay this market
                  </button>
                  <Link className="bx-btn" to={withSport(`/games/${event.canonical_game_id}`, 'NFL')}>Game analysis <ArrowRight size={13} /></Link>
                  {event.lab_available && (
                    <Link className="bx-btn" to={withSport(`/games/${event.canonical_game_id}/simulation`, 'NFL')}>Simulation Lab <ArrowRight size={13} /></Link>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  )
}

/**
 * Prediction-market observations, with the side each book is actually on.
 *
 * The midpoint column is oriented to the home team by the backend. The bid and
 * ask are the raw contract's own prices, and different venues list different
 * sides of the same game -- Polymarket may quote the away contract while
 * Kalshi quotes the home one. Both were previously shown under a single
 * "<home> win" heading, which made a correct away-side book look like a
 * contradiction.
 *
 * The side is resolved from the numbers and named. When it cannot be
 * established the prices are withheld with the reason, never complemented into
 * agreement.
 */
function CrowdTable({
  providers, home, away, homeProbability, now, note,
}: {
  providers: PredictionProviderProbability[]
  home: string
  away: string
  homeProbability: number | null | undefined
  now: number
  note: string | null | undefined
}) {
  if (providers.length === 0) {
    return <p className="bx-dim">No matched pregame contract observation is available for this game.</p>
  }
  return (
    <>
      <div className="bx-scroll">
        <table className="bx-table">
          <thead>
            <tr>
              <th scope="col">Venue</th>
              <th scope="col">{home} win</th>
              <th scope="col">Quoted side</th>
              <th scope="col">Bid / ask</th>
              <th scope="col">Observation</th>
              <th scope="col">Settlement review</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((provider) => {
              // Read from the contract's own identity. A price crossing 50%
              // cannot change which team a contract settles on, so nothing
              // here looks at the numbers to decide the side.
              const orientation = resolveQuotedContract(provider, homeProbability)
              const sideLabel = orientation.side === 'HOME' ? home
                : orientation.side === 'AWAY' ? away : null
              const quote = quoteDescription({
                observationKind: provider.observation_kind,
                inPlay: provider.in_play,
                ageLabel: agoOf(provider.observed_at, now),
              })
              return (
                <tr key={provider.provider}>
                  <th scope="row"><SourceBadge source={provider.provider} /></th>
                  <td className="bx-num">{pct(provider.midpoint, 1)}</td>
                  <td>
                    {sideLabel
                      ? (
                        <span
                          className="bx-chip"
                          title={[orientation.question, orientation.settlementMeaning]
                            .filter(Boolean).join(' · ') || undefined}
                        >
                          {sideLabel}
                          {orientation.nativeLabel && orientation.nativeLabel !== sideLabel
                            ? ` · ${orientation.nativeLabel}` : ''}
                        </span>
                      )
                      : (
                        // The native label and the raw quote still show; only
                        // the normalized comparison is withheld.
                        <span className="bx-mkt__unverified" title={orientation.reason ?? undefined}>
                          {orientation.nativeLabel ?? 'contract not identified'}
                          <small>Side unverified</small>
                        </span>
                      )}
                  </td>
                  <td className="bx-num">
                    {provider.best_bid == null
                      ? <span className="bx-dim">unavailable</span>
                      : `${pct(provider.best_bid, 1)} / ${pct(provider.best_ask, 1)}`}
                  </td>
                  <td>
                    <span className="bx-mkt__quotekind">{quote.kind}</span>
                    <small className="bx-dim" style={{ display: 'block' }}>{quote.recorded}</small>
                  </td>
                  <td>{provider.semantics_status === 'SEMANTICS_MATCHED'
                    ? 'reviewed'
                    : 'unreviewed · research only'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="bx-dim" style={{ marginTop: 8 }}>
        The <b>{home} win</b> column is oriented to {home}. <b>Bid / ask</b> are the venue's own
        contract prices, and each venue lists both sides as separate contracts. The side comes
        from the contract's own identity — its outcome label, market and outcome identifiers —
        not from the prices, so it does not change when a price crosses 50%.
      </p>
      {providers.some((p) => resolveQuotedContract(p, homeProbability).consistencyWarning) && (
        <p className="bx-dim" role="status" style={{ marginTop: 6 }}>
          {providers
            .map((p) => resolveQuotedContract(p, homeProbability).consistencyWarning)
            .filter(Boolean)[0]}
        </p>
      )}
      <details className="bx-mkt__sources" style={{ marginTop: 6 }}>
        <summary>Contract identifiers</summary>
        <ul className="bx-mkt__contract-ids">
          {providers.map((p) => {
            const o = resolveQuotedContract(p, homeProbability)
            return (
              <li key={p.provider}>
                <b>{p.provider}</b> · {o.question ?? 'question not recorded'}
                {o.marketId && <> · <code>{o.marketId}</code></>}
                {o.outcomeId && <> · outcome <code>{o.outcomeId}</code></>}
                {o.settlementMeaning && <> · {o.settlementMeaning}</>}
              </li>
            )
          })}
        </ul>
      </details>
      {note && <p className="bx-dim" style={{ marginTop: 6 }}>{note}</p>}
    </>
  )
}
