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
 * Player props are sportsbook quotes only ("Sportsbook market — no AISP
 * assessment"): no model probability, fair price, edge, expected return,
 * decision status or confidence is ever shown for them.
 *
 * My Card holds the reader's own virtual straight picks. It never calls the
 * API, never creates an AISP decision or position and never touches the
 * $1,000 Experiment. "Try a historical card" opens the historical replay:
 * stored prices exactly as they appeared at a past moment, picked onto a
 * separate replay card that can never reach the live card.
 */
import { memo, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  SPORT_COLORS, SPORT_NAMES, etTime, gameHref, relative, type CoverageGame, type CoverageQuote,
} from '../../services/commandCenter'
import {
  MY_CARD_LABEL, REPLAY_PICK_LABEL, isCurrentQuote, myCard, pickKey, pickReturn, replayCard, useCard, useMyCard,
  type CardStore, type NewPick, type VirtualPick,
} from '../../services/myCard'
import {
  PARLAY_UNAVAILABLE, american, legLabel, pct,
  type AssessedQuote, type DailyAssessments, type GameAssessment, type PlayerPropQuote,
} from '../../services/probability'
import { PLAYER_PROPS_FIXTURE } from '../../services/playerPropsFixture'
import { useReplay, type ReplayGame } from '../../services/replay'
import { MarketBlock } from './AssessmentBoard'
import './assessments.css'

const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')
const MARKETS: { key: string; label: string; sides: [string, string] }[] = [
  { key: 'MONEYLINE', label: 'Moneyline', sides: ['AWAY', 'HOME'] },
  { key: 'SPREAD', label: 'Spread', sides: ['AWAY', 'HOME'] },
  { key: 'TOTAL', label: 'Total', sides: ['OVER', 'UNDER'] },
]
type QuoteLike = Pick<AssessedQuote, 'market' | 'line' | 'sportsbook' | 'provider' | 'observed_at'> & {
  sides: Record<string, { american: number | null; decimal: number | null } | undefined>
}
type GameLike = Pick<CoverageGame, 'game_id' | 'league' | 'matchup' | 'starts_at' | 'home' | 'away'>
const marketOf = (q: Pick<QuoteLike, 'market'>) => (q.market === 'RUN_LINE' ? 'SPREAD' : q.market)
export const NO_ASSESSMENT = 'Sportsbook market — no AISP assessment'
const ORIGIN_TAG: Record<string, string> = {
  OFFICIAL_PICK: 'Your virtual copy of the #1 AISP pick',
  MODEL_VALUE: 'Your virtual pick · research model value',
  PERSONAL: 'Your virtual pick',
}
export const FIXTURE_BANNER = 'Test fixture — not live sportsbook data. Interface preview only; nothing here can be selected.'

export interface MarketGame {
  gameId: string
  game: CoverageGame
  assessment: GameAssessment | null
}

export function outcomeLabel(market: string, side: string, line: number | null, game: GameLike): string {
  const team = side === 'HOME' ? (game.home?.name ?? game.home?.abbreviation ?? 'Home') : (game.away?.name ?? game.away?.abbreviation ?? 'Away')
  if (market === 'MONEYLINE') return `${team} to win`
  if (market === 'TOTAL') return `${side === 'OVER' ? 'Over' : 'Under'} ${line}`
  const signed = line == null ? null : side === 'HOME' ? line : -line
  return `${team} ${signed != null && signed > 0 ? '+' : ''}${signed ?? ''}`
}

/** The frozen pick for one outcome of one stored quote, or null when it cannot be picked. */
export function pickFrom(game: GameLike, quote: QuoteLike, side: string): NewPick | null {
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

/** A straight player-prop pick frozen from one stored quote. */
export function propPick(game: GameLike, q: PlayerPropQuote): NewPick | null {
  if (!(q.decimal > 1) || !Number.isFinite(q.american)) return null
  return {
    game_id: game.game_id, sport: game.league as NewPick['sport'], matchup: game.matchup ?? '',
    starts_at: game.starts_at, market: 'PLAYER_PROP', outcome: q.outcome,
    outcome_label: `${q.player_name} ${q.description}`, line: q.line,
    american: q.american, decimal: q.decimal, sportsbook: shortBook(q.sportsbook) || 'Sportsbook',
    provider: q.provider, quote_observed_at: q.observed_at,
    prop: {
      quote_id: q.quote_id, market_key: q.market_key, market_label: q.market_label, player_id: q.player_id,
      player_name: q.player_name, team: q.team, description: q.description,
      provider_outcome: q.provider_outcome, provider_line: q.line,
    },
  }
}

const providerTerms = (q: Pick<PlayerPropQuote, 'market_key' | 'provider_outcome' | 'line'>) =>
  `${q.market_key} · ${q.provider_outcome}${q.line != null ? ` ${q.line}` : ''}`

/** Player-prop quotes for one game, grouped by player and then by prop. */
export function PlayerPropsSection({ game, quotes, now, preview = false }: {
  game: GameLike; quotes: PlayerPropQuote[]; now: number; preview?: boolean
}) {
  const card = useMyCard()
  const chosen = useMemo(() => new Set(card.map(pickKey)), [card])
  const players = useMemo(() => {
    const out = new Map<string, { name: string; team: string; props: Map<string, PlayerPropQuote[]> }>()
    for (const q of quotes) {
      const player = out.get(q.player_id) ?? { name: q.player_name, team: q.team, props: new Map<string, PlayerPropQuote[]>() }
      player.props.set(q.market_key, [...(player.props.get(q.market_key) ?? []), q])
      out.set(q.player_id, player)
    }
    return [...out.entries()].sort((a, b) => a[1].name.localeCompare(b[1].name))
  }, [quotes])
  return (
    <section className="am-market am-props" aria-label="Player props">
      <h4>Player Props</h4>
      <p className="am-props__label" role="note"><b>{NO_ASSESSMENT}.</b> Prices only — no AISP probability, fair price, edge or recommendation.</p>
      {players.map(([pid, player]) => (
        <div key={pid} className="am-player">
          <p className="am-player__name"><b>{player.name}</b> <small>{player.team}</small></p>
          {[...player.props.entries()].map(([marketKey, rows]) => (
            <ul key={marketKey} className="am-props__rows" aria-label={`${player.name} ${rows[0].market_label}`}>
              {rows.map((q) => {
                const pick = propPick(game, q)
                const live = !preview && q.selectable && q.state === 'CURRENT' && isCurrentQuote(q.observed_at, now)
                const already = pick != null && chosen.has(pickKey(pick))
                return (
                  <li key={q.quote_id} className={live ? '' : 'is-stale'}>
                    <span className="am-prop__what">
                      <b>{q.description}</b>
                      <small>Provider market: {providerTerms(q)}</small>
                    </span>
                    <span className="am-quote__book">
                      <b>{shortBook(q.sportsbook)}</b>
                      <small>{q.provider} · {etTime(q.observed_at, true)}{preview ? '' : ` (${relative(q.observed_at, now)})`}</small>
                      <em className={`as-state ${live ? 'is-current' : 'is-stale'}`}>{preview ? 'Test fixture' : live ? 'Current' : 'Stored reference only'}</em>
                    </span>
                    <button type="button" className="am-outcome" disabled={!live || already || pick == null}
                      aria-label={live
                        ? `Add ${player.name} ${q.description} ${american(q.american)} at ${shortBook(q.sportsbook)} to My Card`
                        : `${player.name} ${q.description}: not selectable (${preview ? 'test fixture' : 'price not current'})`}
                      onClick={() => { if (pick && live) myCard.add(pick, { now, current: true }) }}>
                      <span>{q.description}</span>
                      <b>{american(q.american)}</b>
                      <small>{already ? 'On your card' : live ? 'Add to My Card' : preview ? 'Preview only' : 'Not current'}</small>
                    </button>
                  </li>
                )
              })}
            </ul>
          ))}
        </div>
      ))}
    </section>
  )
}

/** The fixture-backed interface preview (?preview=player-props). Never live, never selectable. */
export function PlayerPropsPreview({ now }: { now: number }) {
  const game: GameLike = { game_id: 'fixture-game', league: 'MLB', matchup: 'Boston Red Sox at New York Yankees', starts_at: null }
  return (
    <section className="am-preview" aria-label="Player props interface preview" data-testid="player-props-preview">
      <p className="am-preview__banner" role="alert"><b>{FIXTURE_BANNER}</b></p>
      <PlayerPropsSection game={game} quotes={PLAYER_PROPS_FIXTURE.quotes} now={now} preview />
    </section>
  )
}

export function AllMarkets({ games, data, now, onOpenLab, preview = false }: {
  games: MarketGame[]; data: DailyAssessments | null; now: number; onOpenLab: (gameId: string) => void; preview?: boolean
}) {
  const withQuotes = games.filter((g) => (g.assessment?.quotes.length ?? 0) > 0 || g.game.quotes.length > 0)
  const props = data?.player_props
  const propsByGame = useMemo(() => {
    const out = new Map<string, PlayerPropQuote[]>()
    for (const q of props?.quotes ?? []) out.set(q.game_id, [...(out.get(q.game_id) ?? []), q])
    return out
  }, [props])
  return (
    <section className="am" aria-label="All Markets">
      <p className="am__intro">Every stored, identity-validated sportsbook market AISP can show for this slate. Only current prices can be added to My Card.
        {' '}These are market listings, not AISP decisions.</p>
      {preview && <PlayerPropsPreview now={now} />}
      {props && props.quotes.length === 0 && (
        <p className="am__props" data-testid="player-props-unavailable">
          <b>Player props:</b> {props.message}
        </p>
      )}
      {withQuotes.length === 0 && <p className="cc-empty">No sportsbook market is stored for this slate.</p>}
      {withQuotes.map((g) => <MarketGameRow key={g.gameId} entry={g} now={now} onOpenLab={onOpenLab} props={propsByGame.get(g.gameId)} />)}
      {(data?.research.length ?? 0) > 0 && (
        <p className="am__research">Research sports ({data!.research.map((r) => r.matchup).join(', ')}) have no governed sportsbook market in AISP, so nothing is listed for them.</p>
      )}
    </section>
  )
}

const MarketGameRow = memo(function MarketGameRow({ entry, now, onOpenLab, props }: {
  entry: MarketGame; now: number; onOpenLab: (gameId: string) => void; props?: PlayerPropQuote[]
}) {
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
        {props && props.length > 0 && <PlayerPropsSection game={game} quotes={props} now={now} />}
        <p className="am-foot"><Link to={gameHref(game.league, game.game_id)}>Open Game Center →</Link></p>
      </div>
    </details>
  )
})

export function MyCard({ now, replay = false, onReplay }: { now: number; replay?: boolean; onReplay?: (on: boolean) => void }) {
  if (replay) return <ReplayCard now={now} onLeave={() => onReplay?.(false)} />
  return <LiveCard now={now} onReplay={onReplay} />
}

function LiveCard({ now, onReplay }: { now: number; onReplay?: (on: boolean) => void }) {
  const picks = useMyCard()
  return (
    <section className="mc" aria-label="My Card">
      <header className="mc__head">
        <h2>My Card</h2>
        <p className="mc__label" role="note"><b>{MY_CARD_LABEL}</b> Virtual only — no real money, and nothing here enters AISP decisions or the $1,000 Experiment.</p>
        <p className="mc__storage">Saved in this browser only (demo card). It is not stored with your account and is lost if browser data is cleared.</p>
      </header>
      {picks.length === 0
        ? <p className="cc-empty">No picks yet. Add a current price from All Markets.</p>
        : <PickList picks={picks} store={myCard} now={now} />}
      {onReplay && (
        <aside className="mc-replay-offer" aria-label="Historical card">
          <p><b>No current price to pick from?</b> Replay stored sportsbook prices exactly as they appeared at an earlier moment.
            {' '}Historical picks go on a separate card and never reach this one.</p>
          <button type="button" onClick={() => onReplay(true)}>Try a historical card</button>
        </aside>
      )}
    </section>
  )
}

function PickList({ picks, store, now }: { picks: VirtualPick[]; store: CardStore; now: number }) {
  const replay = store.mode === 'replay'
  const totals = picks.reduce((acc, p) => {
    const r = pickReturn(p)
    return { stake: acc.stake + p.stake, total: acc.total + r.total }
  }, { stake: 0, total: 0 })
  return (
    <>
      <ul className="mc__picks">
        {picks.map((p) => <PickRow key={p.id} pick={p} store={store} now={now} />)}
      </ul>
      <footer className="mc__foot">
        <span>{picks.length} straight pick{picks.length === 1 ? '' : 's'} · virtual stake ${totals.stake.toFixed(2)} · {replay ? 'illustrative total' : 'total'} if every pick wins ${totals.total.toFixed(2)}</span>
        <small>Each pick is settled on its own price. Picks are not combined into a parlay, and no combined probability or payout is calculated.
          {replay ? ' Historical picks are never settled and show no result.' : ''}</small>
        <button type="button" onClick={() => store.clear()}>{replay ? 'Clear historical card' : 'Clear card'}</button>
      </footer>
    </>
  )
}

function ReplayCard({ now, onLeave }: { now: number; onLeave: () => void }) {
  const replay = useReplay()
  const picks = useCard(replayCard)
  const data = replay.data
  const label = data?.as_of_label ?? ''
  return (
    <section className="mc mc--replay" aria-label="Historical card">
      <div className="mc-replay__banner" role="status" data-testid="replay-banner">
        <b>{data?.banner ?? 'Historical market replay — loading the stored moment…'}</b>
        <button type="button" onClick={onLeave}>Leave historical replay</button>
      </div>
      <header className="mc__head">
        <h2>Historical card</h2>
        <p className="mc__label" role="note"><b>{REPLAY_PICK_LABEL}</b> A demonstration with stored prices. It is separate from My Card and never enters AISP decisions, positions, settlements, performance or the $1,000 Experiment.</p>
        <p className="mc__storage">Prices are shown as they stood at that moment. No later price, result, model or edge is used.</p>
      </header>
      {picks.length === 0
        ? <p className="cc-empty">No historical picks yet. Choose a stored price below.</p>
        : <PickList picks={picks} store={replayCard} now={now} />}
      {replay.error && <p className="cc-empty" role="status">Historical replay is unavailable: {replay.error}</p>}
      {data && !data.available && <p className="cc-empty">{data.message ?? 'No stored moment is available for a replay.'}</p>}
      {data?.available && data.as_of && <ReplayMarkets games={data.games} asOf={data.as_of} asOfLabel={label} />}
    </section>
  )
}

function ReplayMarkets({ games, asOf, asOfLabel }: { games: ReplayGame[]; asOf: string; asOfLabel: string }) {
  const card = useCard(replayCard)
  const chosen = useMemo(() => new Set(card.filter((p) => p.replay_as_of === asOf).map(pickKey)), [card, asOf])
  const at = Date.parse(asOf)
  return (
    <section className="am am--replay" aria-label="Historical markets">
      <h3 className="mc-replay__title">Stored prices at {asOfLabel}</h3>
      {games.map((game) => (
        <details key={game.game_id} className="am-game" open={games.length <= 3}
          style={{ ['--sport' as string]: SPORT_COLORS[game.league as 'MLB'] }}>
          <summary>
            <span className="am-game__sport"><i className="cc-dot" />{SPORT_NAMES[game.league as 'MLB'] ?? game.league}</span>
            <b>{game.away?.name ?? game.away?.abbreviation} at {game.home?.name ?? game.home?.abbreviation}</b>
            <span className="am-game__when">scheduled {etTime(game.starts_at, true)}</span>
          </summary>
          <div className="am-game__body">
            {MARKETS.map((m) => {
              const rows = game.quotes.filter((q) => marketOf(q) === m.key)
              return (
                <section key={m.key} className="am-market" aria-label={`Historical ${m.label} markets`}>
                  <h4>{m.label}</h4>
                  {rows.length === 0 ? <p className="am-none">Not quoted at that moment.</p> : (
                    <ul className="am-quotes">
                      {rows.map((q: CoverageQuote, i) => (
                        <li key={`${q.sportsbook_key}-${q.line}-${i}`} className={q.fresh ? '' : 'is-stale'}>
                          <span className="am-quote__book">
                            <b>{shortBook(q.sportsbook)}</b>
                            <small>{q.provider} · observed {etTime(q.observed_at, true)}</small>
                            <em className={`as-state ${q.fresh ? 'is-current' : 'is-stale'}`}>{q.fresh ? 'Current at that moment' : 'Already stale then'}</em>
                          </span>
                          {m.sides.map((side) => {
                            const pick = pickFrom(game, q, side)
                            const selectable = pick != null && q.fresh && isCurrentQuote(q.observed_at, at)
                            const already = pick != null && chosen.has(pickKey(pick))
                            const text = pick?.outcome_label ?? `${side.toLowerCase()} not quoted`
                            return (
                              <button key={side} type="button" className="am-outcome" disabled={!selectable || already}
                                aria-label={selectable ? `Add ${text} ${american(q.sides[side]?.american)} at ${shortBook(q.sportsbook)} to the historical card` : `${text}: not selectable`}
                                onClick={() => { if (pick) replayCard.add(pick, { asOf }) }}>
                                <span>{text}</span>
                                <b>{pick ? american(q.sides[side]?.american) : '—'}</b>
                                <small>{already ? 'On historical card' : selectable ? 'Add to historical card' : pick ? 'Not current then' : 'Not quoted'}</small>
                              </button>
                            )
                          })}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              )
            })}
          </div>
        </details>
      ))}
    </section>
  )
}

function PickRow({ pick, store, now }: { pick: VirtualPick; store: CardStore; now: number }) {
  const [draft, setDraft] = useState(String(pick.stake))
  const r = pickReturn(pick)
  const replay = store.mode === 'replay'
  const still = isCurrentQuote(pick.quote_observed_at, now)
  const kind = pick.prop ? `${pick.prop.market_label.toLowerCase()} (player prop)` : pick.market.toLowerCase()
  return (
    <li className="mc-pick" style={{ ['--sport' as string]: SPORT_COLORS[pick.sport] }} aria-label={`${pick.outcome_label}, ${replay ? REPLAY_PICK_LABEL : MY_CARD_LABEL}`}>
      <div className="mc-pick__main">
        <span className={`mc-pick__tag${replay ? ' is-replay' : ''}`}>{replay ? 'Historical virtual pick' : ORIGIN_TAG[pick.origin ?? 'PERSONAL']}</span>
        <b>{pick.outcome_label} {american(pick.american)}</b>
        <small>{pick.matchup} · {SPORT_NAMES[pick.sport]} · {kind} · {pick.sportsbook}{pick.provider ? ` via ${pick.provider}` : ''}</small>
        {pick.prop && (
          <small>Provider market: {pick.prop.market_key} · {pick.prop.provider_outcome}{pick.prop.provider_line != null ? ` ${pick.prop.provider_line}` : ''} · {NO_ASSESSMENT}</small>
        )}
        {replay
          ? <small>Price as it appeared {etTime(pick.quote_observed_at, true)}{pick.replay_as_of ? ` · replay moment ${etTime(pick.replay_as_of, true)}` : ''}</small>
          : <small>Price observed {etTime(pick.quote_observed_at, true)} · added {etTime(pick.added_at, true)} ·{' '}
            <span className={still ? 'is-up' : 'is-down'}>{still ? 'price still current' : 'price no longer current'}</span></small>}
        {pick.expectation && (
          <small className="mc-pick__ev">AISP expected profit when added: {pick.expectation.expected_profit >= 0 ? '+' : '−'}${Math.abs(pick.expectation.expected_profit).toFixed(2)} per $1
            {' '}(expected return ${pick.expectation.expected_return.toFixed(2)}; win {pct(pick.expectation.win)}{pick.expectation.push ? `, push ${pct(pick.expectation.push)}` : ''}) · frozen at this price</small>
        )}
      </div>
      <label className="mc-pick__stake">Virtual stake $
        <input inputMode="decimal" value={draft} onChange={(e) => setDraft(e.target.value)}
          onBlur={() => store.setStake(pick.id, Number(draft))} aria-label={`Virtual stake for ${pick.outcome_label}`} />
      </label>
      <span className="mc-pick__return">{replay ? 'Illustrative total if it wins' : 'Total if it wins'} ${r.total.toFixed(2)}<small>profit if it wins ${r.profit.toFixed(2)} · $1 → ${pick.decimal.toFixed(2)}</small></span>
      <button type="button" onClick={() => store.remove(pick.id)} aria-label={`Remove ${pick.outcome_label}`}>Remove</button>
    </li>
  )
}
