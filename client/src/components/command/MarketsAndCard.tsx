/**
 * Daily Decisions · All Markets and My Card.
 *
 * All Markets lists every stored, identity-validated quote AISP can display,
 * grouped by game and then by market. The first view is the price itself:
 * both outcomes, line, American price, sportsbook, observed time and freshness.
 * The model / no-vig / fair / EV working sits behind "Why?". Only a CURRENT
 * quote is selectable; stale and reference quotes are shown as such and can
 * never be added. A market that was not quoted is not shown as selectable, and
 * player props appear only when real, identity-complete quotes exist.
 *
 * My Card holds the reader's own virtual straight picks. It never calls the
 * API, never creates an AISP decision or position and never touches the
 * $1,000 Experiment.
 */
import { memo, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { SPORT_COLORS, SPORT_NAMES, etTime, gameHref, relative, type CoverageGame } from '../../services/commandCenter'
import {
  MY_CARD_LABEL, isCurrentQuote, myCard, pickKey, pickReturn, useMyCard,
  type NewPick, type VirtualPick,
} from '../../services/myCard'
import {
  PARLAY_UNAVAILABLE, american, legLabel, pct,
  type AssessedQuote, type DailyAssessments, type GameAssessment,
} from '../../services/probability'
import { MarketBlock } from './AssessmentBoard'
import './assessments.css'

const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')
const MARKETS: { key: string; label: string; sides: [string, string] }[] = [
  { key: 'MONEYLINE', label: 'Moneyline', sides: ['AWAY', 'HOME'] },
  { key: 'SPREAD', label: 'Spread', sides: ['AWAY', 'HOME'] },
  { key: 'TOTAL', label: 'Total', sides: ['OVER', 'UNDER'] },
]
const marketOf = (q: AssessedQuote) => (q.market === 'RUN_LINE' ? 'SPREAD' : q.market)

export interface MarketGame {
  gameId: string
  game: CoverageGame
  assessment: GameAssessment | null
}

export function outcomeLabel(market: string, side: string, line: number | null, game: CoverageGame): string {
  const team = side === 'HOME' ? (game.home?.name ?? game.home?.abbreviation ?? 'Home') : (game.away?.name ?? game.away?.abbreviation ?? 'Away')
  if (market === 'MONEYLINE') return `${team} to win`
  if (market === 'TOTAL') return `${side === 'OVER' ? 'Over' : 'Under'} ${line}`
  const signed = line == null ? null : side === 'HOME' ? line : -line
  return `${team} ${signed != null && signed > 0 ? '+' : ''}${signed ?? ''}`
}

/** The frozen pick for one outcome of one stored quote, or null when it cannot be picked. */
export function pickFrom(game: CoverageGame, quote: AssessedQuote, side: string): NewPick | null {
  const price = quote.sides[side]
  if (!price || price.american == null || price.decimal == null) return null
  const market = marketOf(quote) as NewPick['market']
  if (market !== 'MONEYLINE' && quote.line == null) return null
  const line = market === 'SPREAD' && quote.line != null ? (side === 'HOME' ? quote.line : -quote.line) : quote.line
  return {
    game_id: game.game_id, sport: game.league as NewPick['sport'], matchup: game.matchup ?? '',
    starts_at: game.starts_at, market, outcome: side as NewPick['outcome'],
    outcome_label: outcomeLabel(market, side, quote.line, game), line: market === 'MONEYLINE' ? null : line,
    american: price.american, decimal: price.decimal, sportsbook: shortBook(quote.sportsbook) || 'Sportsbook',
    provider: quote.provider, quote_observed_at: quote.observed_at,
  }
}

export function AllMarkets({ games, data, now, onOpenLab }: {
  games: MarketGame[]; data: DailyAssessments | null; now: number; onOpenLab: (gameId: string) => void
}) {
  const withQuotes = games.filter((g) => (g.assessment?.quotes.length ?? 0) > 0 || g.game.quotes.length > 0)
  const props = data?.player_props
  return (
    <section className="am" aria-label="All Markets">
      <p className="am__intro">Every stored, identity-validated sportsbook market AISP can show for this slate. Only current prices can be added to My Card.
        {' '}These are market listings, not AISP decisions.</p>
      {props && props.quotes.length === 0 && (
        <p className="am__props" data-testid="player-props-unavailable">
          <b>Player props:</b> {props.message}
        </p>
      )}
      {withQuotes.length === 0 && <p className="cc-empty">No sportsbook market is stored for this slate.</p>}
      {withQuotes.map((g) => <MarketGameRow key={g.gameId} entry={g} now={now} onOpenLab={onOpenLab} />)}
      {(data?.research.length ?? 0) > 0 && (
        <p className="am__research">Research sports ({data!.research.map((r) => r.matchup).join(', ')}) have no governed sportsbook market in AISP, so nothing is listed for them.</p>
      )}
    </section>
  )
}

const MarketGameRow = memo(function MarketGameRow({ entry, now, onOpenLab }: { entry: MarketGame; now: number; onOpenLab: (gameId: string) => void }) {
  const { game, assessment } = entry
  const quotes = assessment?.quotes ?? []
  const card = useMyCard()
  const chosen = useMemo(() => new Set(card.map(pickKey)), [card])
  const current = quotes.filter((q) => q.state === 'CURRENT').length
  const sameGame = assessment?.binding.status === 'SUPPORTED'
  return (
    <details className="am-game" style={{ ['--sport' as string]: SPORT_COLORS[game.league as 'MLB'] }}>
      <summary>
        <span className="am-game__sport"><i className="cc-dot" />{SPORT_NAMES[game.league as 'MLB'] ?? game.league}</span>
        <b>{game.away?.name ?? game.away?.abbreviation} at {game.home?.name ?? game.home?.abbreviation}</b>
        <span className="am-game__when">{game.starts_at ? etTime(game.starts_at, true) : 'time TBD'}</span>
        <span className={`am-state ${current ? 'is-current' : 'is-stale'}`}>{current ? `${current} current quote${current === 1 ? '' : 's'}` : 'No current price'}</span>
      </summary>
      <div className="am-game__body">
        {MARKETS.map((m) => {
          const rows = quotes.filter((q) => marketOf(q) === m.key)
            .sort((a, b) => Number(b.state === 'CURRENT') - Number(a.state === 'CURRENT') || b.observed_at.localeCompare(a.observed_at))
          return (
            <section key={m.key} className="am-market" aria-label={`${m.label} markets`}>
              <h4>{m.label}</h4>
              {rows.length === 0
                ? <p className="am-none">Not quoted — nothing to select.</p>
                : (
                  <ul className="am-quotes">
                    {rows.map((q, i) => (
                      <li key={`${q.sportsbook_key}-${q.line}-${i}`} className={q.state === 'CURRENT' ? '' : 'is-stale'}>
                        <span className="am-quote__book">
                          <b>{shortBook(q.sportsbook)}</b>
                          <small>{q.provider}{q.classification === 'RESEARCH' ? ' · research-classified' : ''} · {etTime(q.observed_at, true)} ({relative(q.observed_at, now)})</small>
                          <em className={`as-state ${q.state === 'CURRENT' ? 'is-current' : 'is-stale'}`}>{q.state === 'CURRENT' ? 'Current' : 'Stored reference only'}</em>
                        </span>
                        {m.sides.map((side) => {
                          const pick = pickFrom(game, q, side)
                          const selectable = pick != null && q.state === 'CURRENT' && isCurrentQuote(q.observed_at, now)
                          const already = pick != null && chosen.has(pickKey(pick))
                          const label = pick?.outcome_label ?? `${side.toLowerCase()} not quoted`
                          return (
                            <button key={side} type="button" className="am-outcome"
                              disabled={!selectable || already}
                              aria-label={selectable ? `Add ${label} ${american(q.sides[side]?.american)} at ${shortBook(q.sportsbook)} to My Card` : `${label}: not selectable (${pick ? 'price not current' : 'not quoted'})`}
                              onClick={() => { if (pick) myCard.add(pick, { now, current: q.state === 'CURRENT' }) }}>
                              <span>{label}</span>
                              <b>{pick ? american(q.sides[side]?.american) : '—'}</b>
                              <small>{already ? 'On your card' : selectable ? 'Add to My Card' : pick ? 'Not current' : 'Not quoted'}</small>
                            </button>
                          )
                        })}
                      </li>
                    ))}
                  </ul>
                )}
              {rows.length > 0 && (
                <details className="am-why">
                  <summary>Why? Model, no-vig, fair price and expected return</summary>
                  <MarketBlock market={m} quote={rows[0]} game={game} />
                </details>
              )}
            </section>
          )
        })}
        <section className="am-market" aria-label="Same-game markets">
          <h4>Same-Game</h4>
          {sameGame ? (
            <>
              {(assessment?.parlay_lab ?? []).map((ex, i) => (
                <p key={i} className="am-sgp">
                  {ex.legs.map((leg) => legLabel(leg, game.home?.abbreviation ?? 'Home', game.away?.abbreviation ?? 'Away')).join(' + ')}:
                  {' '}model joint win {pct(ex.probabilities.win)} ({ex.effective_draw_count.toLocaleString()} paired draws). {PARLAY_UNAVAILABLE}
                </p>
              ))}
              <button type="button" className="am-link" onClick={() => onOpenLab(game.game_id)}>Build a same-game combination in the Parlay Lab</button>
            </>
          ) : <p className="am-none">No verified simulation population for this game, so no same-game research is available.</p>}
        </section>
        <section className="am-market" aria-label="Player props">
          <h4>Player Props</h4>
          <p className="am-none">No player-prop quotes are stored for this game.</p>
        </section>
        <p className="am-foot"><Link to={gameHref(game.league, game.game_id)}>Open Game Center →</Link></p>
      </div>
    </details>
  )
})

export function MyCard({ now }: { now: number }) {
  const picks = useMyCard()
  const totals = picks.reduce((acc, p) => {
    const r = pickReturn(p)
    return { stake: acc.stake + p.stake, total: acc.total + r.total }
  }, { stake: 0, total: 0 })
  return (
    <section className="mc" aria-label="My Card">
      <header className="mc__head">
        <h2>My Card</h2>
        <p className="mc__label" role="note"><b>{MY_CARD_LABEL}</b> Virtual only — no real money, and nothing here enters AISP decisions or the $1,000 Experiment.</p>
        <p className="mc__storage">Saved in this browser only (demo card). It is not stored with your account and is lost if browser data is cleared.</p>
      </header>
      {picks.length === 0
        ? <p className="cc-empty">No picks yet. Add a current price from All Markets.</p>
        : (
          <ul className="mc__picks">
            {picks.map((p) => <PickRow key={p.id} pick={p} now={now} />)}
          </ul>
        )}
      {picks.length > 0 && (
        <footer className="mc__foot">
          <span>{picks.length} straight pick{picks.length === 1 ? '' : 's'} · virtual stake ${totals.stake.toFixed(2)} · total if every pick wins ${totals.total.toFixed(2)}</span>
          <small>Each pick is settled on its own price. Picks are not combined into a parlay, and no combined probability or payout is calculated.</small>
          <button type="button" onClick={() => myCard.clear()}>Clear card</button>
        </footer>
      )}
    </section>
  )
}

function PickRow({ pick, now }: { pick: VirtualPick; now: number }) {
  const [draft, setDraft] = useState(String(pick.stake))
  const r = pickReturn(pick)
  const still = isCurrentQuote(pick.quote_observed_at, now)
  return (
    <li className="mc-pick" style={{ ['--sport' as string]: SPORT_COLORS[pick.sport] }} aria-label={`${pick.outcome_label}, ${MY_CARD_LABEL}`}>
      <div className="mc-pick__main">
        <span className="mc-pick__tag">Your virtual pick</span>
        <b>{pick.outcome_label} {american(pick.american)}</b>
        <small>{pick.matchup} · {SPORT_NAMES[pick.sport]} · {pick.market.toLowerCase()} · {pick.sportsbook}{pick.provider ? ` via ${pick.provider}` : ''}</small>
        <small>Price observed {etTime(pick.quote_observed_at, true)} · added {etTime(pick.added_at, true)} ·{' '}
          <span className={still ? 'is-up' : 'is-down'}>{still ? 'price still current' : 'price no longer current'}</span></small>
      </div>
      <label className="mc-pick__stake">Virtual stake $
        <input inputMode="decimal" value={draft} onChange={(e) => setDraft(e.target.value)}
          onBlur={() => myCard.setStake(pick.id, Number(draft))} aria-label={`Virtual stake for ${pick.outcome_label}`} />
      </label>
      <span className="mc-pick__return">Returns ${r.total.toFixed(2)}<small>profit ${r.profit.toFixed(2)} at {pick.decimal.toFixed(2)}</small></span>
      <button type="button" onClick={() => myCard.remove(pick.id)} aria-label={`Remove ${pick.outcome_label}`}>Remove</button>
    </li>
  )
}
