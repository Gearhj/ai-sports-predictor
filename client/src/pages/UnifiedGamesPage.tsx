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
import { useCallback, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Freshness, SkeletonCards, SkeletonTable, SignalPill } from '../components/ui'
import { PrefetchLink } from '../components/PrefetchLink'
import {
  ArrowRight, Beaker, CalendarDays, ChevronDown, ChevronLeft, ChevronRight, LineChart,
} from 'lucide-react'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { closedStyle, edgeDescription, hasStarted, opportunityStyle } from '../services/statusStyle'
import { availabilityNote, engineShortName, isCurrent, type ForecastIdentity } from '../services/forecastSelection'
import { useSportSelection } from '../hooks/useSportSelection'
import { apiRequest, endpoints } from '../services/api'
import { byStartTime, mlbSlate, nflSlate, type SlateGame } from '../services/broadcastSlate'
import {
  LeagueTag, SplitBar, TeamMark, agoOf, american, clockOf, dayOf, decimals,
  pct, signedPoints, useBroadcastTheme,
} from '../components/broadcast/primitives'
import type { DashboardData, MarketBoardData, MarketBoardRow } from '../types/domain'
import type { NflEvent, NflWeeks } from '../types/nfl'
import '../broadcast.css'

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

export function UnifiedGamesPage() {
  useBroadcastTheme()
  const { sport, withSport } = useSportSelection()
  const [params, setParams] = useSearchParams()
  const dateParam = params.get('date')
  const [stateFilter, setStateFilter] = useState<StateFilter>('ALL')

  const loader = useCallback(async (signal: AbortSignal): Promise<GamesBoard> => {
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
    const query = dateParam ? `?date=${encodeURIComponent(dateParam)}` : ''
    const [mlb, books, weeks] = await Promise.all([
      wantsMlb ? safe('MLB slate', () => apiRequest<DashboardData>(`/games/slate${query}`, {}, signal)) : null,
      wantsMlb ? safe('MLB prices', () => apiRequest<MarketBoardData>(`/markets/board${query}`, {}, signal)) : null,
      wantsNfl ? safe('NFL schedule', () => endpoints.nflWeeks(signal)) : null,
    ])
    const season = Number(params.get('season')) || weeks?.current?.season
    const week = Number(params.get('week')) || weeks?.current?.week
    const nfl = season && week
      ? await safe('NFL week', () => apiRequest<NflMarketsBoard>(`/nfl/markets/${season}/${week}`, {}, signal))
      : null
    return { data_mode: 'live', scope: sport, date: mlb?.date ?? dateParam, mlb, books, weeks, nfl, warnings }
    // `params` is read for season/week only; date drives its own dependency.
  }, [sport, dateParam, params])

  const resource = useApiResource(loader, {
    key: cacheKey('unified/games', {
      sport, date: dateParam, season: params.get('season'), week: params.get('week'),
    }),
    staleTimeMs: STALE_TIME.slate,
    refreshIntervalMs: STALE_TIME.slate,
  })
  const board = resource.data?.scope === sport ? resource.data : null

  const mlbGames = useMemo(() => mlbSlate(board?.mlb ?? null).sort(byStartTime), [board])
  const nflGames = useMemo(() => nflSlate(board?.nfl?.events ?? []).sort(byStartTime), [board])
  const all = useMemo(() => [...mlbGames, ...nflGames].sort(byStartTime), [mlbGames, nflGames])
  const visible = useCallback(
    (games: SlateGame[]) => stateFilter === 'ALL' ? games : games.filter((game) => stateOf(game) === stateFilter),
    [stateFilter],
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
            <p className="bx-muted" style={{ marginTop: 8, maxWidth: '58ch' }}>
              Every game, its projected score, the win split behind it and the price it can be
              measured against. Open the analysis or the simulation from any row.
            </p>
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
            withSport={withSport}
            emptyNote="No MLB game is recorded for this date."
          />
        )}

        {(sport === 'ALL' || sport === 'NFL') && (
          <LeagueSection
            league="NFL"
            title="Football"
            subtitle={board?.nfl ? `${board.nfl.season} · Week ${board.nfl.week}` : 'Selected week'}
            games={visible(nflGames)}
            books={[]}
            withSport={withSport}
            emptyNote="No NFL game is recorded for this week."
            researchNote="Research shadow. NFL forecasts create no position and carry no wagering authority."
          />
        )}

        {!['ALL', 'MLB', 'NFL'].includes(sport) && (
          <p className="bx-empty">{sport} is planned. No forecasts or markets are published yet.</p>
        )}
      </div>
    </div>
  )
}

function LeagueSection({
  league, title, subtitle, games, books, withSport, emptyNote, researchNote,
}: {
  league: 'MLB' | 'NFL'; title: string; subtitle: string; games: SlateGame[]
  books: MarketBoardRow[]; emptyNote: string; researchNote?: string
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
              books={books.filter((row) => row.game_id === game.gameId)} />
          ))}
        </div>}
    </section>
  )
}

function GameRow({
  game, books, withSport,
}: { game: SlateGame; books: MarketBoardRow[]; withSport: (href: string, sport?: 'MLB' | 'NFL') => string }) {
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

  return (
    <article className={`bx-slate__row bx-slate__row--${game.league}`} aria-label={`${game.away.name} at ${game.home.name}`}>
      <div className="bx-slate__when">
        <b>{clockOf(game.startsAt)}</b>
        <small>{state === 'UPCOMING' ? dayOf(game.startsAt).split(',')[0] : state.toLowerCase()}</small>
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

      <div className="bx-slate__prices">
        <span className={`bx-price${game.bestAwayPrice == null && game.bestHomePrice == null ? ' bx-price--muted' : ''}`}>
          <small>Moneyline</small>
          <b className="bx-num">{american(game.bestAwayPrice)} / {american(game.bestHomePrice)}</b>
        </span>
        <span className={`bx-price${handicap == null ? ' bx-price--muted' : ''}`}>
          <small>{handicapLabel}</small>
          <b className="bx-num">{handicap == null ? 'none' : signedPoints(handicap, 1)}</b>
        </span>
        <span className={`bx-price${totalLine == null ? ' bx-price--muted' : ''}`}>
          <small>{totalLabel}</small>
          <b className="bx-num">{totalLine == null ? 'none' : decimals(totalLine, 1)}</b>
        </span>
      </div>

      <div className="bx-slate__decision">
        <DecisionChip game={game} />
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
          Analysis <ArrowRight size={13} />
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


function DecisionChip({ game }: { game: SlateGame }) {
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
