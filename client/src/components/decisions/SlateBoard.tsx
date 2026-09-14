/**
 * Every scheduled game on the slate, and why each one stands where it does.
 *
 * WHY THE DEFAULT IS ALL GAMES
 * ----------------------------
 * A board that showed only opportunities answered "what should I look at" and
 * silently refused to answer "what is happening today" — so a slate with no
 * qualifying edge rendered as an empty page, which reads as a broken product
 * rather than as a quiet day. The default now lists every game, and the filters
 * narrow it.
 *
 * FIVE STATES THAT ARE NOT THE SAME THING
 * ---------------------------------------
 * "No position" covers five very different situations, and rendering them
 * identically teaches a reader that the model declined five wagers it never
 * saw. Only NO_QUALIFYING_VALUE is a rejected bet. FORECAST_PENDING and
 * PRICE_UNAVAILABLE are missing inputs, NOT_YET_EVALUATED is a schedule, and
 * POLICY_INACTIVE means nothing evaluates this sport at all.
 *
 * Each card says which, in a sentence, and never implies a sportsbook withheld
 * a line AISP simply has not captured.
 */
import { useMemo } from 'react'
import { AlertTriangle, Clock, CheckCircle2 } from 'lucide-react'
import { GameScoreboard } from '../scoreboard/GameScoreboard'
import type { GameScoreboard as Scoreboard } from '../../types/scoreboard'
import type { SlateGame, SlateStatus } from '../../types/slate'

export type SlateFilter = 'ALL' | 'OPPORTUNITIES' | 'POSITIONS'

/** Which states count as an opportunity worth a second look. */
const OPPORTUNITY_STATES = new Set(['NO_QUALIFYING_VALUE', 'POSITION_RECORDED'])

const TONE: Record<string, string> = {
  POSITION_RECORDED: 'slate__state--good',
  NO_QUALIFYING_VALUE: 'slate__state--neutral',
  PRICE_UNAVAILABLE: 'slate__state--warn',
  FORECAST_PENDING: 'slate__state--wait',
  NOT_YET_EVALUATED: 'slate__state--wait',
  POLICY_INACTIVE: 'slate__state--inactive',
}

const LABEL: Record<string, string> = {
  POSITION_RECORDED: 'Position recorded',
  NO_QUALIFYING_VALUE: 'No qualifying value',
  PRICE_UNAVAILABLE: 'Price unavailable',
  FORECAST_PENDING: 'Forecast pending',
  NOT_YET_EVALUATED: 'Not yet evaluated',
  POLICY_INACTIVE: 'Policy not active',
}

/**
 * The global sport selector applies here too.
 *
 * Without it the board contradicts the rest of the page: with NBA selected the
 * funnel above correctly reports zero games while the board below listed
 * eleven MLB and NFL ones, which reads as two different answers to the same
 * question.
 */
export function filterBySport(games: SlateGame[], sport: string): SlateGame[] {
  if (!sport || sport === 'ALL') return games
  return games.filter((game) => game.league_code === sport)
}

export function filterGames(games: SlateGame[], filter: SlateFilter): SlateGame[] {
  if (filter === 'POSITIONS') return games.filter((game) => game.positions > 0)
  if (filter === 'OPPORTUNITIES') {
    return games.filter((game) => OPPORTUNITY_STATES.has(game.state))
  }
  return games
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  })
}

function age(minutes: number): string {
  if (minutes < 90) return `${Math.round(minutes)}m old`
  const hours = minutes / 60
  if (hours < 48) return `${hours.toFixed(1)}h old`
  return `${(hours / 24).toFixed(1)} days old`
}

function GameRow({ game, score, onRefreshScore }: {
  game: SlateGame
  score?: Scoreboard | null
  onRefreshScore?: () => void
}) {
  const away = game.teams.away ?? '—'
  const home = game.teams.home ?? '—'
  const notStarted = new Date(game.starts_at).getTime() > Date.now()
  return (
    <article className="slate__row" data-testid={`slate-${game.game_id}`}>
      <div className="slate__game">
        <strong>{away} @ {home}</strong>
        <small>{clock(game.starts_at)} · {game.league_code}</small>
      </div>

      {/* THE SCORE, ON EVERY ROW, ABOVE THE FOLD.
          It hangs off nothing: not a forecast, not a quote, not a qualifying
          edge and not a recorded wager. Previously the only scoreboard on this
          page sat inside a recorded selection row, so a day with no selections
          -- which is most days under a moneyline-only funded policy -- showed
          no scores at all, and a finished game was invisible on the page whose
          whole job is "what is happening today". The period breakdown and the
          source provenance stay in the component's own disclosure; the score
          itself never requires expanding anything. */}
      <div className="slate__score">
        {/* `score` may exist and still be unavailable -- the service returns a
            row per requested game and marks it. So the test is availability,
            not presence. */}
        {score?.available
          ? <GameScoreboard game={score} compact onRefresh={onRefreshScore} />
          : notStarted
            ? (
              // No observation exists because the game has not been played.
              // That is "Scheduled", not "unavailable" -- and it is certainly
              // not 0-0. The collector only opens a watch window near first
              // pitch, so most of the day this is the honest state.
              <p className="scorebd scorebd--absent" data-testid="slate-scheduled">
                <b>Scheduled</b>
                <span className="bx-dim"> · starts {clock(game.starts_at)}</span>
              </p>
            )
            : <GameScoreboard game={score} compact onRefresh={onRefreshScore} />}
      </div>

      {/* Forecast before price: a game with a model and no quote is a very
          different state from one with neither, and the reader can see it
          without opening anything. */}
      <div className="slate__cell">
        <dt>Forecast</dt>
        <dd>
          {game.forecast
            ? <>
                <b className="slate__yes">available</b>
                <small>{game.forecast.engine_id}</small>
                <small>{new Date(game.forecast.created_at ?? '').toLocaleString()}</small>
              </>
            : <span className="slate__no">not published yet</span>}
        </dd>
      </div>

      <div className="slate__cell">
        <dt>Price</dt>
        <dd>
          {game.quote
            ? <>
                <b className="bx-num">
                  {game.quote.american_odds != null && game.quote.american_odds > 0 ? '+' : ''}
                  {game.quote.american_odds ?? '—'}
                </b>
                <small>{game.quote.sportsbook} · {age(game.quote.age_minutes)}</small>
                <small className={game.quote.satisfies_policy_freshness
                  ? 'slate__yes' : 'slate__warn'}>
                  {game.quote.satisfies_policy_freshness
                    ? 'within the policy freshness window'
                    : `past the ${game.quote.freshness_limit_minutes / 60}h freshness limit`}
                </small>
              </>
            : <span className="slate__no">none captured</span>}
        </dd>
      </div>

      <div className="slate__cell">
        <dt>Policy</dt>
        <dd>
          {game.policy.active_for_sport
            ? <b className="slate__yes">active</b>
            : <span className="slate__no">not active</span>}
          {game.latest_evaluation && (
            <small>
              {game.latest_evaluation.recommendation}
              {game.latest_evaluation.phase ? ` · ${game.latest_evaluation.phase}` : ''}
            </small>
          )}
        </dd>
      </div>

      <div className="slate__cell">
        <dt>Next checkpoint</dt>
        <dd>
          {game.next_checkpoint
            ? <>
                <b>{clock(game.next_checkpoint.at_utc)}</b>
                <small>
                  {game.next_checkpoint.window_open
                    ? 'collection window open'
                    : `opens ${clock(game.next_checkpoint.collection_window_opens_utc)}`}
                </small>
              </>
            : <span className="slate__no">none ahead</span>}
        </dd>
      </div>

      <div className="slate__verdict">
        <span className={`slate__state ${TONE[game.state] ?? ''}`}
              data-testid={`slate-state-${game.game_id}`}>
          {game.state === 'POSITION_RECORDED' && <CheckCircle2 size={12} aria-hidden="true" />}
          {game.state === 'POLICY_INACTIVE' && <AlertTriangle size={12} aria-hidden="true" />}
          {(game.state === 'NOT_YET_EVALUATED' || game.state === 'FORECAST_PENDING')
            && <Clock size={12} aria-hidden="true" />}
          {LABEL[game.state] ?? game.state}
        </span>
        <p className="slate__why">{game.explanation}</p>
      </div>
    </article>
  )
}

/** Local calendar day, offset by whole days. Never a UTC date. */
function shiftDay(isoDay: string, days: number): string {
  const [year, month, day] = isoDay.split('-').map(Number)
  const moved = new Date(year, (month ?? 1) - 1, (day ?? 1) + days)
  return [moved.getFullYear(),
          String(moved.getMonth() + 1).padStart(2, '0'),
          String(moved.getDate()).padStart(2, '0')].join('-')
}

function today(): string {
  const now = new Date()
  return [now.getFullYear(),
          String(now.getMonth() + 1).padStart(2, '0'),
          String(now.getDate()).padStart(2, '0')].join('-')
}

/**
 * Today / Yesterday / any day.
 *
 * Without it the page could only ever show the operational date, so a finished
 * game became unreachable the moment the day rolled over -- which is precisely
 * when someone wants to look at it. The sport selection rides in the URL and is
 * untouched here.
 */
function DateControl({ day, onDay }: { day: string; onDay: (next: string) => void }) {
  const now = today()
  const yesterday = shiftDay(now, -1)
  return (
    <div className="slate__dates" data-testid="slate-dates">
      <button type="button" className="bx-btn bx-btn--ghost"
        aria-label="Previous day" data-testid="slate-day-prev"
        onClick={() => onDay(shiftDay(day, -1))}>‹</button>
      <button type="button" aria-pressed={day === now}
        className={day === now ? 'bx-btn' : 'bx-btn bx-btn--ghost'}
        data-testid="slate-day-today" onClick={() => onDay(now)}>Today</button>
      <button type="button" aria-pressed={day === yesterday}
        className={day === yesterday ? 'bx-btn' : 'bx-btn bx-btn--ghost'}
        data-testid="slate-day-yesterday"
        onClick={() => onDay(yesterday)}>Yesterday</button>
      <input type="date" value={day} aria-label="Slate date"
        data-testid="slate-day-input" max={now}
        onChange={(event) => event.target.value && onDay(event.target.value)} />
      <button type="button" className="bx-btn bx-btn--ghost"
        aria-label="Next day" data-testid="slate-day-next"
        disabled={day >= now}
        onClick={() => onDay(shiftDay(day, 1))}>›</button>
    </div>
  )
}

export function SlateBoard({
  slate, filter, onFilter, sport = 'ALL', scores = {}, onRefreshScore,
  day, onDay,
}: {
  slate: SlateStatus | null
  filter: SlateFilter
  onFilter: (next: SlateFilter) => void
  /** The global sport selection. 'ALL' shows every league on the slate. */
  sport?: string
  /** Observed scores keyed by game id, from the page's single read. */
  scores?: Record<string, Scoreboard>
  onRefreshScore?: () => void
  /** The day on screen, and how to change it. Omit both to hide the control. */
  day?: string
  onDay?: (next: string) => void
}) {
  // `?? []` on every read: the board sits above the rest of Daily Decisions,
  // so a thrown render here blanks the whole route rather than one panel.
  const all = useMemo(
    () => filterBySport(Array.isArray(slate?.games) ? slate.games : [], sport),
    [slate, sport],
  )
  const games = useMemo(() => filterGames(all, filter), [all, filter])
  const counts = {
    ALL: all.length,
    OPPORTUNITIES: filterGames(all, 'OPPORTUNITIES').length,
    POSITIONS: filterGames(all, 'POSITIONS').length,
  }

  return (
    <div className="slate" data-testid="slate-board">
      <div className="slate__controls">
        {day && onDay && <DateControl day={day} onDay={onDay} />}
        <div className="bx-segment" role="group" aria-label="Slate filter">
          {(['ALL', 'OPPORTUNITIES', 'POSITIONS'] as SlateFilter[]).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => onFilter(value)}
              data-testid={`slate-filter-${value}`}
            >
              {value === 'ALL' ? 'All games'
                : value === 'OPPORTUNITIES' ? 'Opportunities' : 'Positions'}{' '}
              <span className="bx-dim">{counts[value]}</span>
            </button>
          ))}
        </div>
        {/* The window, stated. "Today" is a local calendar day, and a reader
            comparing this count against a two-day figure needs to know which. */}
        {slate?.window?.description && (
          <span className="bx-dim" data-testid="slate-window">
            {slate.window.description}
          </span>
        )}
      </div>

      {games.length === 0 ? (
        <p className="bx-empty" data-testid="slate-empty">
          {all.length === 0
            ? sport !== 'ALL'
              ? `No ${sport} game is scheduled on this slate.`
              : 'No game is scheduled on this slate.'
            : filter === 'POSITIONS'
              ? 'No position has been recorded on this slate. Every scheduled game '
                + 'is still listed under All games with the reason it has none.'
              : 'No game on this slate has reached a priced evaluation yet. '
                + 'Every scheduled game is still listed under All games.'}
        </p>
      ) : (
        <div className="slate__list">
          {games.map((game) => (
            <GameRow
              key={game.game_id}
              game={game}
              score={scores[game.game_id]}
              onRefreshScore={onRefreshScore}
            />
          ))}
        </div>
      )}
    </div>
  )
}
