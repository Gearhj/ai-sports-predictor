/**
 * Games & Forecasts — the slate.
 *
 * WHAT WAS WRONG WITH THE PAGE THIS REPLACES
 * ------------------------------------------
 * Every game was a tall portrait card carrying its own model label, its own
 * evidence timestamp, its own readiness paragraph and its own pair of buttons.
 * Three of them filled a 1440-wide screen. A sixteen-game NFL week was five
 * screens of scrolling through what were, visually, sixteen copies of the same
 * form -- and the two numbers a reader actually came for, the projected score
 * and the price, were the smallest things on it.
 *
 * A slate is a list. So this is a list: one landscape row per game, the score
 * and the win split at the size they deserve, the prices in fixed columns so
 * the eye can run down them, and everything that identifies the *model* rather
 * than the *game* folded into a per-row drawer. Eight games fit above the fold
 * and the sixteenth is a scroll away, not a pilgrimage.
 *
 * WHAT IS DELIBERATELY NOT SHARED BETWEEN THE SPORTS
 * -------------------------------------------------
 * The rows look alike; the numbers in them do not mean alike. MLB projects
 * runs and has no tie; NFL projects points and publishes a final-score tie
 * that is not an overtime probability. The run line and the point spread are
 * different markets and sit in a column labelled by the sport, never in one
 * column called "spread". MLB can create an official position and NFL cannot,
 * so an NFL row reports research status rather than a decision it was never
 * allowed to make.
 */
import { SportReadiness } from '../components/readiness/SportReadiness'
import { useCallback, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Freshness, SkeletonCards, SkeletonTable, SignalPill } from '../components/ui'
import { PrefetchLink } from '../components/PrefetchLink'
import {
  ArrowRight, Beaker, CalendarDays, ChevronDown, ChevronLeft, ChevronRight, LineChart,
} from 'lucide-react'
import { useApiResource } from '../hooks/useApiResource'
import { SLATE_SORT_LABEL, currentGap, hasCurrentPrice, hasForecast, sortSlate, type SlateSort } from '../services/slateSort'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { closedStyle, edgeDescription, hasStarted, marketWait, opportunityStyle } from '../services/statusStyle'
import { availabilityNote, engineShortName, isCurrent, type ForecastIdentity } from '../services/forecastSelection'
import { useSportSelection } from '../hooks/useSportSelection'
import { useSportRegistry } from '../hooks/useSportRegistry'
import { resolveAllSports } from '../services/sportExperience'
import { apiRequest, endpoints } from '../services/api'
import { byStartTime, mlbSlate, nflSlate, type SlateGame } from '../services/broadcastSlate'
import {
  etDateOf, useCoverageForDates, useLedger, type CoverageGame, type CoverageQuote, type LedgerPosition,
} from '../services/commandCenter'
import {
  LeagueTag, SplitBar, TeamMark, agoOf, american, clockOf, dayOf, decimals,
  pct, signedPoints, useBroadcastTheme,
} from '../components/broadcast/primitives'
import type { DashboardData, MarketBoardData, MarketBoardRow } from '../types/domain'
import type { NflEvent, NflWeeks } from '../types/nfl'
import '../broadcast.css'
import '../components/command/command.css'

type NflMarketsBoard = { season: number; week: number; events: NflEvent[] }
type StateFilter = 'ALL' | 'UPCOMING' | 'STARTED' | 'FINAL'

interface GamesBoard {
  data_mode: 'live'
  scope: string
  date: string | null
  mlb: DashboardData | null
  books: MarketBoardData | null
  weeks: NflWeeks | null
  nfl: NflMarketsBoard | null
  warnings: string[]
}

const stateOf = (game: SlateGame): StateFilter => {
  const status = game.status.toUpperCase()
  if (['FINAL', 'COMPLETED', 'CLOSED'].includes(status)) return 'FINAL'
  if (['STARTED', 'IN_PROGRESS', 'LIVE'].includes(status)) return 'STARTED'
  return 'UPCOMING'
}

/** Quote age as a reader says it. Null age means the price time was not usable. */
export function ageText(minutes: number | null): string {
  if (minutes == null) return 'price time not recorded'
  if (minutes < 1) return 'observed just now'
  if (minutes < 90) return `observed ${Math.round(minutes)} min ago`
  if (minutes < 48 * 60) return `observed ${Math.round(minutes / 60)} h ago`
  return `observed ${Math.round(minutes / 1440)} days ago`
}

/** The next checkpoint a missing price is waiting on: the T-30 decision window. */
export function nextCheckpoint(startsAt: string | null, now: number = Date.now()): string {
  const start = startsAt ? Date.parse(startsAt) : Number.NaN
  if (!Number.isFinite(start)) return 'Next price checkpoint not scheduled'
  const opens = start - 30 * 60_000
  if (now >= start) return 'Game started · no pregame price was collected'
  if (now >= opens) return 'Decision window open now · awaiting a collected price'
  return `Next checkpoint: decision window opens ${new Date(opens).toLocaleString([], {
    weekday: 'short', hour: 'numeric', minute: '2-digit' })}`
}

export function UnifiedGamesPage() {
  useBroadcastTheme()
  const { sport, withSport } = useSportSelection()
  const { registry } = useSportRegistry()
  // Backend authority, not a route guessed from a league code.
  const researchHref = resolveAllSports(registry?.sports)
    .find((item) => item.leagueCode === sport)?.researchHref ?? null
  const [params, setParams] = useSearchParams()
  const dateParam = params.get('date')
  const [stateFilter, setStateFilter] = useState<StateFilter>('ALL')
  const sortMode: SlateSort = (['KICKOFF', 'FORECAST', 'GAP'] as const).find((m) => m === params.get('sort')) ?? 'KICKOFF'
  const setSortMode = (mode: SlateSort) => setParams((old) => {
    const q = new URLSearchParams(old)
    if (mode === 'KICKOFF') q.delete('sort')
    else q.set('sort', mode)
    return q
  }, { replace: true })

  // Two independent regions: the MLB slate never waits for the slower NFL week
  // board, and either one failing leaves the other usable.
  const wantsMlb = sport === 'ALL' || sport === 'MLB'
  const wantsNfl = sport === 'ALL' || sport === 'NFL'
  const seasonParam = params.get('season')
  const weekParam = params.get('week')
  const mlbRes = useApiResource(useCallback(async (signal: AbortSignal) => {
    if (!wantsMlb) return { data_mode: 'live' as const, mlb: null, books: null, warnings: [] as string[] }
    const query = dateParam ? `?date=${encodeURIComponent(dateParam)}` : ''
    const warnings: string[] = []
    const [mlb, books] = await Promise.all([
      apiRequest<DashboardData>(`/games/slate${query}`, {}, signal),
      apiRequest<MarketBoardData>(`/markets/board${query}`, {}, signal).catch((error) => {
        if (signal.aborted) throw error
        warnings.push('MLB prices unavailable')
        return null
      }),
    ])
    return { data_mode: 'live' as const, mlb, books, warnings }
  }, [wantsMlb, dateParam]), { key: cacheKey('games-mlb', { wantsMlb, date: dateParam }), staleTimeMs: STALE_TIME.slate, refreshIntervalMs: STALE_TIME.slate })
  const nflRes = useApiResource(useCallback(async (signal: AbortSignal) => {
    if (!wantsNfl) return { data_mode: 'live' as const, weeks: null, nfl: null }
    const weeks = await endpoints.nflWeeks(signal)
    const season = Number(seasonParam) || weeks?.current?.season
    const week = Number(weekParam) || weeks?.current?.week
    const nfl = season && week ? await apiRequest<NflMarketsBoard>(`/nfl/markets/${season}/${week}`, {}, signal) : null
    return { data_mode: 'live' as const, weeks, nfl }
  }, [wantsNfl, seasonParam, weekParam]), { key: cacheKey('games-nfl', { wantsNfl, season: seasonParam, week: weekParam }), staleTimeMs: STALE_TIME.slate, refreshIntervalMs: STALE_TIME.slate })
  const board: GamesBoard = {
    data_mode: 'live', scope: sport, date: mlbRes.data?.mlb?.date ?? dateParam,
    mlb: mlbRes.data?.mlb ?? null, books: mlbRes.data?.books ?? null,
    weeks: nflRes.data?.weeks ?? null, nfl: nflRes.data?.nfl ?? null,
    warnings: [
      ...(mlbRes.data?.warnings ?? []),
      ...(mlbRes.error && !mlbRes.data ? ['MLB slate unavailable'] : []),
      ...(nflRes.error && !nflRes.data ? ['NFL week unavailable'] : []),
    ],
  }
  const resource = {
    loading: (wantsMlb ? mlbRes.loading : true) && (wantsNfl ? nflRes.loading : true),
    error: mlbRes.data || nflRes.data ? null : (mlbRes.error ?? nflRes.error),
    updatedAt: mlbRes.updatedAt ?? nflRes.updatedAt,
    refreshing: mlbRes.refreshing || nflRes.refreshing,
    stale: mlbRes.stale || nflRes.stale,
    refresh: () => { mlbRes.refresh(); nflRes.refresh() },
  }
  const mlbPending = wantsMlb && mlbRes.loading
  const nflPending = wantsNfl && nflRes.loading

  const mlbGames = useMemo(() => mlbSlate(board?.mlb ?? null).sort(byStartTime), [mlbRes.data])
  const nflGames = useMemo(() => nflSlate(board?.nfl?.events ?? []).sort(byStartTime), [nflRes.data])
  // Stored sportsbook evidence for every date on screen, from the coverage read
  // model (all persisted LIVE quotes, with provider classification), and the
  // ledger for recorded entry terms. Both are cached, provider-free reads.
  const evidenceDates = useMemo(() => [...new Set([...mlbGames, ...nflGames]
    .map((g) => etDateOf(g.startsAt)).filter((d): d is string => Boolean(d)))], [mlbGames, nflGames])
  const evidence = useCoverageForDates(evidenceDates)
  const ledger = useLedger()
  const entriesByGame = useMemo(() => {
    const out: Record<string, LedgerPosition[]> = {}
    for (const s of ledger.data?.sports ?? []) for (const pos of s.positions) (out[pos.game_id] ??= []).push(pos)
    return out
  }, [ledger.data])
  const all = useMemo(() => [...mlbGames, ...nflGames].sort(byStartTime), [mlbGames, nflGames])
  const byGame = evidence.data?.byGame ?? null
  const visible = useCallback(
    (games: SlateGame[]) => sortSlate(
      stateFilter === 'ALL' ? games : games.filter((game) => stateOf(game) === stateFilter),
      sortMode, (game) => (game.gameId ? byGame?.[game.gameId] : null),
    ),
    [stateFilter, sortMode, byGame],
  )

  const displayedDate = board?.date ?? dateParam ?? ''
  const stepDate = (days: number) => {
    if (!displayedDate) return
    const next = new Date(`${displayedDate}T12:00:00Z`)
    next.setUTCDate(next.getUTCDate() + days)
    setParams((old) => {
      const q = new URLSearchParams(old)
      q.set('date', next.toISOString().slice(0, 10))
      return q
    }, { replace: true })
  }
  const setDate = (value: string) => setParams((old) => {
    const q = new URLSearchParams(old); q.set('date', value); return q
  }, { replace: true })

  const nextUp = useMemo(
    () => all.filter((game) => stateOf(game) === 'UPCOMING').slice(0, 8),
    [all],
  )

  return (
    <div className="bx bx-games">
      <div className="bx__inner">
        <header className="bx-head">
          <div>
            <span className="bx-eyebrow"><LineChart size={13} aria-hidden="true" /> Games &amp; Forecasts</span>
            <h1>The <em>slate</em></h1>
          </div>
          <div className="bx-head__meta">
            {(sport === 'ALL' || sport === 'MLB') && (
              <span className="bx-field">
                <button type="button" className="bx-btn bx-btn--ghost" style={{ minHeight: 26, padding: '0 6px' }}
                  aria-label="Previous MLB date" disabled={!displayedDate} onClick={() => stepDate(-1)}>
                  <ChevronLeft size={15} />
                </button>
                <CalendarDays size={14} aria-hidden="true" />
                <input type="date" aria-label="MLB slate date" value={displayedDate}
                  onChange={(event) => setDate(event.target.value)} />
                <button type="button" className="bx-btn bx-btn--ghost" style={{ minHeight: 26, padding: '0 6px' }}
                  aria-label="Next MLB date" disabled={!displayedDate} onClick={() => stepDate(1)}>
                  <ChevronRight size={15} />
                </button>
              </span>
            )}
            {(sport === 'ALL' || sport === 'NFL') && board?.weeks && (
              <label className="bx-field">Week
                <select aria-label="NFL week"
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
          </div>
        </header>

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="bx-segment" role="group" aria-label="Game state filter">
            {(['ALL', 'UPCOMING', 'STARTED', 'FINAL'] as const).map((value) => (
              <button key={value} type="button" aria-pressed={stateFilter === value}
                onClick={() => setStateFilter(value)}>
                {value === 'ALL' ? 'All' : value === 'UPCOMING' ? 'Upcoming' : value === 'STARTED' ? 'Started' : 'Final'}
              </button>
            ))}
          </div>
          <label className="bx-field">Sort
            <select value={sortMode} onChange={(e) => setSortMode(e.target.value as SlateSort)} aria-label="Sort games">
              {(Object.keys(SLATE_SORT_LABEL) as SlateSort[]).map((mode) => (
                <option key={mode} value={mode}>{SLATE_SORT_LABEL[mode]}</option>
              ))}
            </select>
          </label>
          <span className="bx-dim">
            {all.length} game{all.length === 1 ? '' : 's'} on the board
            {board?.warnings?.length ? ` · ${board.warnings.join(' · ')}` : ''}
          </span>
        </div>

        {nextUp.length > 0 && (
          <nav className="bx-ribbon" aria-label="Next games">
            <span className="bx-ribbon__label">Next up</span>
            <div className="bx-ribbon__scroll bx-scroll">
              {nextUp.map((game) => (
                <PrefetchLink key={game.key} className="bx-ribbon__game" to={withSport(game.analysisHref, game.league)}>
                  <b><span style={{ color: `var(--bx-${game.league.toLowerCase()})` }}>{game.league}</span>{' '}
                    {game.away.abbreviation} @ {game.home.abbreviation}</b>
                  <small>{clockOf(game.startsAt)}</small>
                </PrefetchLink>
              ))}
            </div>
          </nav>
        )}

        {resource.loading && !board && (
          <>
            <SkeletonCards count={4} />
            <SkeletonTable rows={8} columns={6} ariaLabel="Loading the slate" />
          </>
        )}
        {resource.error && !board && <p className="bx-empty" role="alert">{resource.error}</p>}
        {board && (
          <Freshness label="Slate" updatedAt={resource.updatedAt} refreshing={resource.refreshing}
            stale={resource.stale} error={resource.error} onRetry={resource.refresh} />
        )}

        {(sport === 'ALL' || sport === 'MLB') && (
          <LeagueSection
            league="MLB"
            title="Baseball"
            subtitle={displayedDate ? dayOf(`${displayedDate}T12:00:00Z`) : 'Daily slate'}
            games={visible(mlbGames)}
            books={board?.books?.rows ?? []}
            evidence={evidence.data?.byGame ?? null}
            entries={entriesByGame}
            withSport={withSport}
            emptyNote={mlbPending ? 'Loading the MLB slate…' : 'No MLB game is recorded for this date.'}
          />
        )}

        {(sport === 'ALL' || sport === 'NFL') && (
          <LeagueSection
            league="NFL"
            title="Football"
            subtitle={board?.nfl ? `${board.nfl.season} · Week ${board.nfl.week}` : 'Selected week'}
            games={visible(nflGames)}
            books={[]}
            evidence={evidence.data?.byGame ?? null}
            entries={entriesByGame}
            withSport={withSport}
            emptyNote={nflPending ? 'Loading the NFL week…' : 'No NFL game is recorded for this week.'}
            researchNote="NFL M1 research forecasts. A virtual paper position is recorded only under the NFL paper policy, with producer eligibility and a fresh price inside the decision window."
          />
        )}

        <details className="cc-below">
          <summary>Forecast and price readiness for each sport</summary>
          <SportReadiness sport={sport} title="Forecast and price readiness by sport" />
        </details>

        {/* A league off the governed board is not automatically a league with
            nothing in it. NBA holds imported research forecasts and no market;
            saying "no forecasts" here would contradict the surface that shows
            them. The registry's own research href decides which sentence runs. */}
        {!['ALL', 'MLB', 'NFL'].includes(sport) && (
          researchHref ? (
            <p className="bx-empty">
              {sport} has no slate, market or position in AISP. Its imported research
              forecasts are on the{' '}
              <Link to={researchHref}>{sport} research page</Link>.
            </p>
          ) : (
            <p className="bx-empty">{sport} is planned. No forecasts or markets are published yet.</p>
          )
        )}
      </div>
    </div>
  )
}

function LeagueSection({
  league, title, subtitle, games, books, withSport, emptyNote, researchNote, evidence, entries,
}: {
  league: 'MLB' | 'NFL'; title: string; subtitle: string; games: SlateGame[]
  books: MarketBoardRow[]; emptyNote: string; researchNote?: string
  evidence: Record<string, CoverageGame> | null
  entries: Record<string, LedgerPosition[]>
  withSport: (href: string, sport?: 'MLB' | 'NFL') => string
}) {
  return (
    <section aria-label={`${league} games`} style={{ display: 'grid', gap: 12 }}>
      <div className={`bx-league-head bx-league-head--${league}`}>
        <LeagueTag league={league} />
        <h2>{title}</h2>
        <span className="bx-dim">{subtitle}</span>
        <div className="bx-league-head__meta">
          {researchNote && <span className="bx-chip">Research shadow</span>}
          <span className="bx-dim">{games.length} shown</span>
        </div>
      </div>
      {researchNote && <p className="bx-dim">{researchNote}</p>}
      {games.length === 0
        ? <p className="bx-empty">{emptyNote}</p>
        : <div className="bx-slate">
          {games.map((game) => (
            <GameRow key={game.key} game={game} withSport={withSport}
              books={books.filter((row) => row.game_id === game.gameId)}
              evidence={game.gameId ? evidence?.[game.gameId] ?? null : null}
              entries={game.gameId ? entries[game.gameId] ?? [] : []} />
          ))}
        </div>}
    </section>
  )
}

function GameRow({
  game, books, withSport, evidence, entries,
}: {
  game: SlateGame; books: MarketBoardRow[]; withSport: (href: string, sport?: 'MLB' | 'NFL') => string
  evidence: CoverageGame | null; entries: LedgerPosition[]
}) {
  const [open, setOpen] = useState(false)
  const state = stateOf(game)
  const homeLead = (game.homeWin ?? 0) >= (game.awayWin ?? 0)
  // The handicap column is labelled by the sport because a run line and a
  // point spread are not the same market and must never share one heading.
  const handicapLabel = game.league === 'MLB' ? 'Run line' : 'Spread'
  const totalLabel = game.league === 'MLB' ? 'Total runs' : 'Total pts'
  const runLine = books.find((row) => row.market_type === 'Run line')
  const totalRow = books.find((row) => row.market_type === 'Total')
  const handicap = game.handicap ?? runLine?.line ?? null
  const totalLine = game.totalLine ?? totalRow?.line ?? null
  // Emphasis only for what is genuinely current: a held forecast AND a price
  // inside the freshness window. Stale or missing prices never get it.
  const ready = hasForecast(game) && hasCurrentPrice(game, evidence)
  const gap = currentGap(game, evidence)

  return (
    <article className={`bx-slate__row bx-slate__row--${game.league}${ready ? ' bx-slate__row--ready' : ''}`}
      aria-label={`${game.away.name} at ${game.home.name}${ready ? ', forecast and current price' : ''}`}>
      <div className="bx-slate__when">
        <b>{clockOf(game.startsAt)}</b>
        <small>{state === 'UPCOMING' ? dayOf(game.startsAt).split(',')[0] : state.toLowerCase()}</small>
        {ready && <span className="bx-ready-tag">Forecast · current price{gap != null ? ` · gap ${(gap * 100).toFixed(1)} pts` : ''}</span>}
      </div>

      <div className="bx-slate__match">
        <div className={`bx-slate__side${homeLead ? '' : ' bx-slate__side--lead'}`}>
          <TeamMark league={game.league} abbreviation={game.away.abbreviation} />
          <em>{game.away.name}</em>
          <i className="bx-num">{decimals(game.expAway, 1)}</i>
        </div>
        <div className={`bx-slate__side${homeLead ? ' bx-slate__side--lead' : ''}`}>
          <TeamMark league={game.league} abbreviation={game.home.abbreviation} />
          <em>{game.home.name}</em>
          <i className="bx-num">{decimals(game.expHome, 1)}</i>
        </div>
      </div>

      <div style={{ minWidth: 0 }}>
        <SplitBar away={game.awayWin} home={game.homeWin} tie={game.tie}
          awayLabel={game.away.abbreviation} homeLabel={game.home.abbreviation}
          awayColor={game.away.color ?? 'var(--bx-nfl)'} homeColor={game.home.color ?? 'var(--bx-accent)'} />
        <p className="bx-dim" style={{ marginTop: 5 }}>
          {game.scoreUnit === 'runs' ? 'Projected runs' : 'Projected points'} ·{' '}
          total {decimals(game.expTotal, 1)}
        </p>
      </div>

      <PriceCell game={game} evidence={evidence} entries={entries} handicapLabel={handicapLabel}
        totalLabel={totalLabel} fallbackHandicap={handicap} fallbackTotal={totalLine} />

      <div className="bx-slate__decision">
        <DecisionChip game={game} evidence={evidence} />
        {/* The band is a comparison against the market, made by one specific
            forecast. Naming that forecast is what stops a Gen 1 judgement
            sitting beside a V18 number. */}
        {game.selection?.primary && (
          <ForecastChip
            identity={game.selection.primary}
            displayedEngineId={game.selection.displayed_engine_id}
          />
        )}
        {/* The band describes one side; the old bare signed number was always
            the home side, so the two disagreed in sign on every away edge. */}
        <span className="bx-dim">
          {edgeDescription({
            edgeSide: game.edgeSide,
            modelEdgePoints: game.modelEdgePoints,
            homeEdgePoints: game.edgePoints,
            homeAbbrev: game.home.abbreviation,
            awayAbbrev: game.away.abbreviation,
          }).text}
        </span>
      </div>

      <div className="bx-slate__actions">
        <PrefetchLink className="bx-btn" to={withSport(game.analysisHref, game.league)}>
          Open Game Center <ArrowRight size={13} />
        </PrefetchLink>
        {game.simulationHref && (
          <PrefetchLink className="bx-btn bx-btn--primary" to={withSport(game.simulationHref, game.league)}>
            <Beaker size={13} aria-hidden="true" /> Simulate
          </PrefetchLink>
        )}
        {/* The evidence drawer is an affordance on the row, not a strip under
            it: as a strip it added a line to all twenty-one games so that a
            reader could open one. */}
        <button type="button" className="bx-mkt__toggle" aria-expanded={open}
          aria-label={`${open ? 'Hide' : 'Show'} model and evidence for ${game.away.abbreviation} at ${game.home.abbreviation}`}
          onClick={() => setOpen((value) => !value)}>
          <ChevronDown size={15} style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform .18s' }} />
        </button>
      </div>

      {open && (
        <div className="bx-details__body" style={{ gridColumn: '1 / -1', paddingTop: 10, borderTop: '1px solid var(--bx-line)' }}>
          <p><strong>Model</strong> {game.modelLabel ?? 'not published'}</p>
          <p><strong>Source evidence</strong> {agoOf(game.evidenceAt) ?? 'not recorded'}
            {game.priceObservedAt ? ` · price observed ${agoOf(game.priceObservedAt)}` : ''}
            {game.priceBook ? ` at ${game.priceBook}` : ''}</p>
          <p><strong>Win probabilities</strong> {game.away.abbreviation} {pct(game.awayWin, 1)} ·{' '}
            {game.home.abbreviation} {pct(game.homeWin, 1)}
            {game.tie != null && game.tie > 0
              ? ` · tie ${pct(game.tie, 1)} (the model's own final-score tie, not an overtime probability)`
              : ''}</p>
          {game.marketHome != null && (
            <p><strong>Market home-win</strong> {pct(game.marketHome, 1)} no-vig ·{' '}
              model minus market {signedPoints(game.edgePoints, 2)} pp</p>
          )}
          {books.length > 0 && (
            <p><strong>Persisted prices</strong>{' '}
              {books.map((row) => `${row.market_type} ${row.selection}${row.line != null ? ` ${row.line}` : ''}`).join(' · ')}</p>
          )}
          <p>{game.researchOnly
            ? 'Research shadow. This forecast creates no decision and no position.'
            : 'A displayed price is persisted evidence, not a wagering eligibility decision.'}</p>
        </div>
      )}
    </article>
  )
}

/**
 * The market-disagreement band for one game.
 *
 * Two things are kept apart that used to be one chip. The band says how far
 * the model is from the market; it is not a decision, and a game that has
 * already started carries only the reading it had before first pitch. Showing
 * a started game's old band with the same weight as a live one is what made
 * finished games look like open opportunities.
 */
/**
 * Which forecast produced the numbers in this row, and whether it governs.
 *
 * A forecast that exists but is not current still shows, with its condition:
 * removing it would hide evidence, and showing it unmarked would present a
 * superseded or invalidated reading as the live one.
 */
function ForecastChip(
  { identity, displayedEngineId }:
  { identity: ForecastIdentity; displayedEngineId?: string | null },
) {
  // `displayed_engine_id` is null when the controlling engine published nothing
  // for this game. The row then carries no model number at all, and the chip
  // says so rather than naming an engine whose figure is not on screen. No
  // other engine is substituted into the primary fields; Gen 1 stays reachable
  // as an explicit comparison.
  const unavailable = !displayedEngineId
  const note = availabilityNote(identity)
  if (unavailable) {
    return (
      <span className="bx-forecast-chip bx-forecast-chip--absent" title={note || undefined}>
        <b>{engineShortName(identity.engine_id)} forecast unavailable</b>
      </span>
    )
  }
  return (
    <span className="bx-forecast-chip" title={note || undefined}>
      <b>{engineShortName(displayedEngineId)}</b>
      {!isCurrent(identity) && <small className="bx-forecast-chip__flag">not current</small>}
    </span>
  )
}


function DecisionChip({ game, evidence }: { game: SlateGame; evidence?: CoverageGame | null }) {
  // A band computed without a price is not a decision. Before a sportsbook
  // price is stored the game is awaiting its market, never "NO BET".
  const hasPrice = evidence ? evidence.quotes.length > 0 : game.priceStatus !== 'NOT_COLLECTED'
  const wait = hasStarted(game.status) && hasPrice ? null : marketWait({
    hasPrice, startsAt: game.startsAt, collectionOpensAt: evidence?.checkpoints.collection_opens_at ?? null,
  })
  if (wait) {
    return (
      <SignalPill label={wait.label} className={`signal-pill ${wait.state === 'NO_PREGAME_PRICE' ? 'signal-pill--closed' : 'signal-pill--watch'}`}
        title={wait.title} sub={wait.detail} />
    )
  }
  if (game.researchOnly) {
    return (
      <SignalPill
        label="Research only" className="signal-pill signal-pill--neutral"
        title="This sport publishes research forecasts and no governed decision band."
      />
    )
  }
  if (!game.decisionState) {
    return (
      <SignalPill
        label="No band published" className="signal-pill signal-pill--neutral"
        title="No market-disagreement band is published for this game."
      />
    )
  }
  const style = opportunityStyle(game.decisionState)
  const started = hasStarted(game.status)
  return (
    <SignalPill
      label={game.decisionState}
      className={started ? closedStyle().className : style.className}
      title={started ? closedStyle().title : style.title}
      sub={started ? 'last pregame reading' : undefined}
    />
  )
}

const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')
const median = (values: number[]) => {
  if (!values.length) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

/**
 * The price column, in exactly one of five honest states:
 *  - CURRENT: persisted quotes inside the freshness window (best price per side
 *    across books, never mixing sides from different books into one quote);
 *  - STALE REFERENCE: the latest persisted quote from ONE book, labelled as not
 *    current, with its age;
 *  - NOT COLLECTED: no persisted quote, with the next checkpoint;
 *  - plus, separately, any RECORDED ENTRY -- the price a position was taken at,
 *    which is never replaced by a later market price.
 * Provider, sportsbook, observation time, age and research/governed class are
 * always named. Model output never fills a price box.
 */
function PriceCell({ game, evidence, entries, handicapLabel, totalLabel, fallbackHandicap, fallbackTotal }: {
  game: SlateGame; evidence: CoverageGame | null; entries: LedgerPosition[]
  handicapLabel: string; totalLabel: string; fallbackHandicap: number | null; fallbackTotal: number | null
}) {
  const quotes = evidence?.quotes ?? []
  const fresh = quotes.filter((q) => q.fresh)
  const current = evidence ? evidence.price_state === 'CURRENT_PRICE' : game.priceStatus === 'CURRENT'
  const ml = (list: CoverageQuote[]) => list.filter((q) => q.market === 'MONEYLINE')
  const latestMl = [...ml(quotes)].sort((a, b) => b.observed_at.localeCompare(a.observed_at))[0] ?? null
  const refBook = latestMl?.sportsbook_key ?? null
  const fromBook = (market: string) => quotes.filter((q) => q.market === market && q.sportsbook_key === refBook)
    .sort((a, b) => b.observed_at.localeCompare(a.observed_at))[0] ?? null
  const source = (q: CoverageQuote | null, books?: number) => q
    ? `${books && books > 1 ? `${books} books` : shortBook(q.sportsbook)} · ${q.provider} (${q.classification.toLowerCase()}) · observed ${etTimeShort(q.observed_at)} · ${ageText(q.age_minutes)}`
    : null

  let away: number | null = null
  let home: number | null = null
  let spread: number | null = null
  let total: number | null = null
  let line: string | null = null
  let state: 'CURRENT' | 'STALE' | 'NONE' = 'NONE'

  if (evidence && current && fresh.length) {
    state = 'CURRENT'
    away = evidence.best.AWAY?.american ?? null
    home = evidence.best.HOME?.american ?? null
    spread = median(fresh.filter((q) => q.market === 'SPREAD' || q.market === 'RUN_LINE').map((q) => q.line as number).filter((v) => v != null))
    total = median(fresh.filter((q) => q.market === 'TOTAL').map((q) => q.line as number).filter((v) => v != null))
    const newest = [...ml(fresh)].sort((a, b) => a.observed_at.localeCompare(b.observed_at))[0] ?? fresh[0]
    line = `Best current price · ${source(newest, evidence.fresh_books)}`
  } else if (evidence && latestMl) {
    state = 'STALE'
    away = latestMl.sides.AWAY?.american ?? null
    home = latestMl.sides.HOME?.american ?? null
    spread = fromBook('SPREAD')?.line ?? fromBook('RUN_LINE')?.line ?? null
    total = fromBook('TOTAL')?.line ?? null
    line = `Last stored price, not current · ${source(latestMl)}`
  } else if (!evidence && game.priceStatus !== 'NOT_COLLECTED') {
    state = game.priceStatus === 'CURRENT' ? 'CURRENT' : 'STALE'
    away = game.bestAwayPrice
    home = game.bestHomePrice
    spread = fallbackHandicap
    total = fallbackTotal
    line = `${state === 'CURRENT' ? 'Current' : 'Last stored price, not current'} · ${[game.priceBook, game.priceProvider].filter(Boolean).join(' via ')} · ${ageText(game.priceAgeMinutes)}`
  }

  return (
    <div className={`bx-slate__prices bx-slate__prices--${state.toLowerCase()}`}>
      {state === 'NONE' ? (
        <span className="bx-price bx-price--muted bx-price--wide">
          <small>Sportsbook price</small>
          <b>Not collected</b>
        </span>
      ) : (
        <>
          <span className="bx-price">
            <small>Moneyline{state === 'STALE' ? ' · stale' : ''}</small>
            <b className="bx-num">{american(away)} / {american(home)}</b>
          </span>
          <span className={`bx-price${spread == null ? ' bx-price--muted' : ''}`}>
            <small>{handicapLabel}</small>
            <b className="bx-num">{spread == null ? 'none' : signedPoints(spread, 1)}</b>
          </span>
          <span className={`bx-price${total == null ? ' bx-price--muted' : ''}`}>
            <small>{totalLabel}</small>
            <b className="bx-num">{total == null ? 'none' : decimals(total, 1)}</b>
          </span>
        </>
      )}
      <small className="bx-price__source">{state === 'NONE' ? nextCheckpoint(game.startsAt) : line}</small>
      {entries.map((e) => (
        <small key={e.position_id} className="bx-price__entry">
          Recorded entry {e.selection} {american(e.entry.american)} · {shortBook(e.entry.sportsbook)} ({e.entry.classification.toLowerCase()}) · stake ${(e.stake ?? 0).toFixed(2)}
          {e.status === 'SETTLED' ? ` · ${e.result} ${(e.profit_loss ?? 0) >= 0 ? '+' : '−'}$${Math.abs(e.profit_loss ?? 0).toFixed(2)}` : ' · open'}
        </small>
      ))}
    </div>
  )
}

function etTimeShort(iso: string): string {
  return new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso)) + ' ET'
}
