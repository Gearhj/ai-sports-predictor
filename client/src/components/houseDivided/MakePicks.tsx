/**
 * Make Picks — a sportsbook-style lobby on top of the competition ledger.
 *
 * Tapping a price adds a straight wager to the Pick Slip as a server draft
 * that names the stored quote. Every number on the slip (odds, line, book,
 * potential profit, available funds) comes back from the server; the browser
 * sends only the quote id, the stake, and the quote it was looking at.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Search, Ticket, X } from 'lucide-react'
import { useAuth } from '../../hooks/useAuth'
import { TeamMark } from '../broadcast/primitives'
import {
  CompetitionRefusal, houseDivided, money,
  type BoardOption, type BoardQuote, type CompetitionSport, type EntryAccess, type MarketBoard, type MarketGame,
  type ParticipantAccount, type SlipWager, type TeamRef,
} from '../../services/houseDivided'

const QUICK_STAKES = [5, 10, 25, 50]
const ET: Intl.DateTimeFormatOptions = { timeZone: 'America/New_York' }
const SPORTS: CompetitionSport[] = ['MLB', 'NFL', 'NBA', 'CFB']
export type LobbyFilter = 'POPULAR' | 'MONEYLINE' | 'SPREAD' | 'TOTAL' | 'PROPS' | 'ALL'

const clock = (iso: string | null | undefined) => (iso
  ? `${new Date(iso).toLocaleTimeString('en-US', { ...ET, hour: 'numeric', minute: '2-digit' })} ET` : '—')
const dayLabel = (day: string, today: string) => {
  if (day === today) return 'Today'
  return new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric' })
}
const todayEt = () => new Date().toLocaleDateString('en-CA', ET)
const minutesAgo = (iso: string | null | undefined, now: number) =>
  iso ? Math.max(0, Math.round((now - Date.parse(iso)) / 60_000)) : null

/** −140 / +125 with a true minus sign, as a sportsbook prints it. */
export const odds = (value: number | null | undefined) =>
  value == null ? '—' : value > 0 ? `+${value}` : `−${Math.abs(value)}`
export const signed = (value: number | null | undefined) =>
  value == null ? '' : value > 0 ? `+${value}` : value < 0 ? `−${Math.abs(value)}` : 'PK'

const TWO_WORD = ['Red Sox', 'White Sox', 'Blue Jays']
/** "Chicago Cubs" -> "Cubs"; "Boston Red Sox" -> "Red Sox". */
export function nickname(team: TeamRef | null | undefined): string {
  if (!team) return ''
  const two = TWO_WORD.find((n) => team.name.endsWith(n))
  if (two) return two
  const words = team.name.split(' ')
  return words[words.length - 1] || team.abbreviation
}

export function countdown(targetIso: string, now: number): string {
  const ms = Date.parse(targetIso) - now
  if (ms <= 0) return 'Locked'
  const minutes = Math.floor(ms / 60_000)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (h >= 24) return `Locks in ${Math.floor(h / 24)}d ${h % 24}h`
  return h > 0 ? `Locks in ${h}h ${m}m` : `Locks in ${m}m`
}

/** A tile's words: "Cubs +125", "Cubs +1.5 −165", "Over 8.5 −110". */
export function tileLabel(option: BoardOption, game: MarketGame, quote: BoardQuote = option.best) {
  const team = option.side === 'home' ? nickname(game.home) : option.side === 'away' ? nickname(game.away) : ''
  if (quote.market_type === 'MONEYLINE') return { pick: team, price: odds(quote.american_odds) }
  if (quote.market_type === 'SPREAD') return { pick: `${team} ${signed(option.line)}`, price: odds(quote.american_odds) }
  return { pick: `${option.side === 'over' ? 'Over' : 'Under'} ${option.line}`, price: odds(quote.american_odds) }
}

function useNow(periodMs = 30_000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), periodMs)
    return () => window.clearInterval(id)
  }, [periodMs])
  return now
}

interface MoveNotice { previous: string; replacement: BoardQuote | null; message: string }

export function MakePicks({ sport, onSport, onChanged }: {
  sport: CompetitionSport | null
  onSport: (sport: CompetitionSport) => void
  onChanged: () => void
}) {
  const { user } = useAuth()
  const now = useNow()
  const [access, setAccess] = useState<EntryAccess | null>(null)
  const [who, setWho] = useState<string>('')
  const [date, setDate] = useState<string | null>(null)
  const [board, setBoard] = useState<MarketBoard | null>(null)
  const [boardError, setBoardError] = useState<string | null>(null)
  const [account, setAccount] = useState<ParticipantAccount | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null)
  const [moves, setMoves] = useState<Record<string, MoveNotice>>({})
  const [receipts, setReceipts] = useState<SlipWager[]>([])
  const [drawer, setDrawer] = useState(false)
  const [filter, setFilter] = useState<LobbyFilter>('POPULAR')
  const [query, setQuery] = useState('')
  const openRef = useRef<HTMLDivElement | null>(null)

  const canEnter = !!access && access.can_enter_for.length > 0
  const activeSport = board?.sport ?? sport

  useEffect(() => {
    if (!user) { setAccess(null); return }
    houseDivided.me().then((a) => {
      setAccess(a)
      setWho((current) => (current && a.can_enter_for.includes(current) ? current : a.can_enter_for[0] ?? ''))
    }).catch(() => setAccess(null))
  }, [user])

  const loadBoard = useCallback(() => {
    houseDivided.marketBoard(sport, date).then((b) => {
      setBoard(b); setBoardError(null)
      if (!sport) onSport(b.sport)
    }).catch((e: Error) => setBoardError(e.message))
  }, [sport, date, onSport])
  useEffect(() => { loadBoard() }, [loadBoard])
  useEffect(() => {
    const id = window.setInterval(loadBoard, 60_000)
    return () => window.clearInterval(id)
  }, [loadBoard])
  useEffect(() => { setDate(null) }, [sport])

  const loadAccount = useCallback(() => {
    if (!canEnter || !who || !activeSport) { setAccount(null); return }
    houseDivided.account(who, activeSport).then(setAccount).catch(() => setAccount(null))
  }, [canEnter, who, activeSport])
  useEffect(() => { loadAccount() }, [loadAccount])

  const drafts = useMemo(() => account?.drafts ?? [], [account])
  const selected = useMemo(() => new Set(drafts.map((d) => d.quote_id)), [drafts])
  const refresh = () => { loadAccount(); loadBoard(); onChanged() }

  const pick = async (game: MarketGame, quote: BoardQuote) => {
    if (!canEnter) return
    setNotice(null)
    const existing = drafts.find((d) => d.game_id === game.game_id && d.market_type === quote.market_type)
    setBusy(quote.quote_id)
    try {
      if (existing && existing.quote_id === quote.quote_id) await houseDivided.slipRemove(existing.wager_id)
      else if (existing) await houseDivided.slipUpdate(existing.wager_id, who, quote.quote_id, existing.stake ? String(existing.stake) : '')
      else await houseDivided.slipAdd(who, quote.quote_id)
      onChanged()
      loadAccount()
    } catch (e) {
      setNotice({ tone: 'bad', text: e instanceof Error ? e.message : 'Refused' })
    } finally { setBusy(null) }
  }

  const participant = access?.participants?.find((p) => p.key === who)
  const participantName = participant?.display_name ?? who
  const removeDraft = async (d: SlipWager) => {
    await houseDivided.slipRemove(d.wager_id).catch(() => undefined)
    setMoves((m) => { const next = { ...m }; delete next[d.wager_id]; return next })
  }
  const slip = (
    <PickSlip
      participant={participantName} sport={activeSport ?? '—'} drafts={drafts} receipts={receipts} moves={moves}
      busy={busy} now={now}
      onStake={async (d, stake) => {
        await houseDivided.slipUpdate(d.wager_id, who, d.quote_id ?? '', stake).catch(() => undefined)
        loadAccount()
      }}
      onRemove={async (d) => { setBusy(d.wager_id); await removeDraft(d); setBusy(null); onChanged(); loadAccount() }}
      onClear={async () => {
        setBusy('clear')
        for (const d of drafts) await removeDraft(d)
        setBusy(null); onChanged(); loadAccount()
      }}
      onAcceptMove={async (d, replacement) => {
        setBusy(d.wager_id)
        try {
          await houseDivided.slipUpdate(d.wager_id, who, replacement.quote_id, d.stake ? String(d.stake) : '')
          setMoves((m) => { const next = { ...m }; delete next[d.wager_id]; return next })
          setNotice({ tone: 'good', text: 'Updated price accepted. Check it, then place the wager again.' })
        } catch (e) {
          setNotice({ tone: 'bad', text: e instanceof Error ? e.message : 'Refused' })
        } finally { setBusy(null); loadAccount() }
      }}
      onPlace={async (d, stake) => {
        setBusy(d.wager_id); setNotice(null)
        try {
          const result = await houseDivided.place(d.wager_id, stake, d.quote_id ?? '')
          setReceipts((r) => [result.receipt, ...r])
          refresh()
        } catch (e) {
          if (e instanceof CompetitionRefusal && (e.code === 'PRICE_CHANGED' || e.code === 'PRICE_UNAVAILABLE')) {
            setMoves((m) => ({ ...m, [d.wager_id]: {
              previous: `${odds(d.american_odds)} at ${d.sportsbook_name ?? d.sportsbook ?? '—'}`,
              replacement: e.replacement, message: e.message } }))
            loadBoard()
          } else {
            setNotice({ tone: 'bad', text: e instanceof Error ? e.message : 'Refused' })
          }
          loadAccount()
        } finally { setBusy(null) }
      }}
      onDismissReceipt={(id) => setReceipts((r) => r.filter((x) => x.wager_id !== id))}
    />
  )

  const today = todayEt()
  const dates = board?.dates ?? []
  const activeDate = board?.date ?? date
  const q = query.trim().toLowerCase()
  const games = (board?.games ?? []).filter((g) => !q || [g.home?.name, g.away?.name, g.home?.abbreviation, g.away?.abbreviation]
    .some((t) => t?.toLowerCase().includes(q)))
  const sportStatus = new Map((board?.sports ?? []).map((s) => [s.sport, s]))

  return (
    <div className="lb">
      {!user && (
        <div className="lb-banner" role="region" aria-label="Sign in to make picks">
          <div>
            <strong>Sign in to make picks</strong>
            <span>Browse every game now. Scotty and Red Lou pick with their own login; an administrator can pick for them.</span>
          </div>
          <Link className="bx-btn bx-btn--primary" to="/login">Sign in to make picks</Link>
        </div>
      )}
      {user && access && !canEnter && (
        <div className="lb-banner lb-banner--muted" role="note">
          <div>
            <strong>Browsing only</strong>
            <span>This login isn’t linked to Scotty or Red Lou, so it can’t add picks. An administrator can link it.</span>
          </div>
        </div>
      )}

      <section className="lb-top" aria-label="Lobby controls">
        <div className="lb-top__row">
          {canEnter && (
            <div className="lb-who">
              {access!.is_admin
                ? (
                  <label>
                    <span>Picking for</span>
                    <select value={who} onChange={(e) => { setWho(e.target.value); setMoves({}); setReceipts([]) }} aria-label="Picking for">
                      {(access!.participants ?? []).map((p) => <option key={p.key} value={p.key}>{p.display_name}</option>)}
                    </select>
                  </label>
                )
                : <div><span>Picking as</span><strong style={{ color: participant?.color }}>{participantName}</strong></div>}
              {account && <div><span>Available bankroll</span><strong className="lb-money">{money(account.available)}</strong></div>}
            </div>
          )}
          <label className="lb-select">
            <span>Sport</span>
            <select value={activeSport ?? ''} onChange={(e) => onSport(e.target.value as CompetitionSport)} aria-label="Sport">
              {SPORTS.map((s) => {
                const st = sportStatus.get(s)
                return <option key={s} value={s} disabled={st?.status === 'NOT_STARTED'}>{s}{st?.status === 'NOT_STARTED' ? ' — not started' : ''}</option>
              })}
            </select>
          </label>
          <label className="lb-select">
            <span>Date</span>
            <select value={activeDate ?? ''} onChange={(e) => setDate(e.target.value)} aria-label="Date" disabled={!dates.length}>
              {!dates.length && <option value="">No upcoming games</option>}
              {dates.map((d) => <option key={d.date} value={d.date}>{dayLabel(d.date, today)} · {d.games} games</option>)}
            </select>
          </label>
          <label className="lb-search">
            <Search size={16} aria-hidden />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search teams" aria-label="Search teams or games" />
          </label>
          {canEnter && account && (
            <button type="button" className="lb-openpicks" onClick={() => {
              if (window.matchMedia('(max-width: 1099px)').matches) setDrawer(true)
              else openRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
            }}>Open Picks <b>{account.open_picks.length}</b></button>
          )}
        </div>
        <div className="lb-filters" role="group" aria-label="Markets">
          {([['POPULAR', 'Popular'], ['MONEYLINE', 'Moneyline'], ['SPREAD', 'Spread / Run Line'], ['TOTAL', 'Total'],
            ['PROPS', 'Player Props'], ['ALL', 'All Markets']] as [LobbyFilter, string][]).map(([key, label]) => (
            <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>
          ))}
        </div>
      </section>

      {notice && <p className={`hd-msg hd-msg--${notice.tone}`} role="status">{notice.text}</p>}

      <div className={`lb-layout${canEnter ? '' : ' lb-layout--solo'}`}>
        <div className="lb-main">
          {boardError && <div className="bx-empty" role="alert">The lobby could not be loaded: {boardError}</div>}
          {!board && !boardError && <div className="bx-empty" role="status">Loading games…</div>}
          {board && board.status !== 'STARTED' && <div className="lb-empty"><strong>{board.sport} has not started.</strong> {board.reason}</div>}
          {board && board.status === 'STARTED' && games.length === 0 && (
            <div className="lb-empty">{q ? `No ${board.sport} game matches “${query}”.` : `No ${board.sport} games on this date.`}</div>
          )}
          {games.map((g) => (
            <GameCard key={g.game_id} game={g} sport={board!.sport} now={now} filter={filter} canEnter={canEnter}
              signedIn={!!user} busy={busy} selected={selected} onPick={(quote) => pick(g, quote)} />
          ))}
        </div>
        {canEnter && (
          <aside className="lb-aside" aria-label="Pick Slip">
            {slip}
            <div ref={openRef}><OpenPicks account={account} /></div>
          </aside>
        )}
      </div>

      {canEnter && (
        <>
          <button type="button" className="lb-slipbar" onClick={() => setDrawer(true)}>
            <Ticket size={20} aria-hidden /> Pick Slip · {drafts.length}
            {account && <small>{money(account.available)} available</small>}
          </button>
          {drawer && (
            <div className="lb-drawer" role="dialog" aria-modal="true" aria-label="Pick Slip">
              <button type="button" className="lb-drawer__scrim" aria-label="Close pick slip" onClick={() => setDrawer(false)} />
              <div className="lb-drawer__sheet">
                <div className="lb-drawer__head">
                  <strong><Ticket size={18} aria-hidden /> Pick Slip · {drafts.length}</strong>
                  <button type="button" className="lb-icon" aria-label="Close" onClick={() => setDrawer(false)}><X size={20} /></button>
                </div>
                {slip}
                <OpenPicks account={account} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
function GameCard({ game, sport, now, filter, canEnter, signedIn, busy, selected, onPick }: {
  game: MarketGame; sport: CompetitionSport; now: number; filter: LobbyFilter; canEnter: boolean; signedIn: boolean
  busy: string | null; selected: Set<string | null>; onPick: (q: BoardQuote) => void
}) {
  const updated = minutesAgo(game.last_update, now)
  const spread = game.spreads[0] ?? null
  const total = game.totals[0] ?? null
  const more = { spreads: game.spreads.slice(1), totals: game.totals.slice(1) }
  const showMl = ['POPULAR', 'MONEYLINE', 'ALL'].includes(filter)
  const showSpread = ['POPULAR', 'SPREAD', 'ALL'].includes(filter)
  const showTotal = ['POPULAR', 'TOTAL', 'ALL'].includes(filter)
  const showProps = filter === 'PROPS' || filter === 'ALL'
  const open = game.state.code === 'OPEN'
  const cols = [showSpread && game.spread_label, showTotal && 'Total', showMl && 'Moneyline'].filter(Boolean) as string[]
  const tile = (option: BoardOption | null | undefined) => (
    <OddsTile option={option ?? null} game={game} canEnter={canEnter} signedIn={signedIn} busy={busy} selected={selected} onPick={onPick} />
  )
  const allOptions = [game.moneyline.away, game.moneyline.home, ...game.spreads.flatMap((r) => [r.away, r.home]),
    ...game.totals.flatMap((r) => [r.over, r.under])].filter((o): o is BoardOption => !!o)
  const shoppable = allOptions.filter((o) => o.quotes.length > 1)
  return (
    <article className={`lb-game lb-game--${game.state.code.toLowerCase()}`} aria-label={`${game.away?.name} at ${game.home?.name}`}>
      <header className="lb-game__head">
        <span className="lb-game__time">{clock(game.starts_at)}</span>
        <span className={`lb-lock${game.locked ? ' lb-lock--closed' : ''}`}
          title={game.lock_basis === 'MODEL_PICK_PUBLIC' ? 'Picks close when the models’ pick becomes public' : 'Picks close 15 minutes before the start'}>
          {game.locked ? 'Locked' : countdown(game.human_lock_at, now)}
        </span>
        <span className={`lb-state lb-state--${game.state.code.toLowerCase()}`}>{game.state.label}</span>
        {open && <span className="lb-fresh">Updated {updated} min ago · {game.books_available} book{game.books_available === 1 ? '' : 's'}{game.best_book ? ` · best: ${game.best_book}` : ''}</span>}
      </header>

      {(showMl || showSpread || showTotal) && (
        <div className="lb-table" role="table" aria-label="Odds">
          <div className="lb-row lb-row--head" role="row" style={{ ['--cols' as string]: cols.length }}>
            <span role="columnheader">Team</span>
            {cols.map((c) => <span key={c} role="columnheader">{c}</span>)}
          </div>
          {(['away', 'home'] as const).map((side) => (
            <div key={side} className="lb-row" role="row" style={{ ['--cols' as string]: cols.length }}>
              <span className="lb-team" role="cell">
                {game[side] && <TeamMark league={sport} abbreviation={game[side]!.abbreviation} size={30} />}
                <span><b>{game[side]?.name ?? (side === 'home' ? 'Home' : 'Away')}</b><small>{side === 'home' ? 'Home' : 'Away'}</small></span>
              </span>
              {showSpread && <span role="cell">{tile(side === 'away' ? spread?.away : spread?.home)}</span>}
              {showTotal && <span role="cell">{tile(side === 'away' ? total?.over : total?.under)}</span>}
              {showMl && <span role="cell">{tile(game.moneyline[side])}</span>}
            </div>
          ))}
        </div>
      )}

      {!open && (showMl || showSpread || showTotal) && (
        <p className="lb-game__why">
          {game.state.code === 'EXPECTED' && game.expected_prices_at
            ? `Prices are collected close to each game. Next collection for this game from about ${clock(game.expected_prices_at)}.`
            : game.state.detail ?? game.state.label}
        </p>
      )}

      {open && (more.spreads.length > 0 || more.totals.length > 0) && (showSpread || showTotal) && (
        <details className="lb-more" open={filter === 'ALL' || undefined}>
          <summary>More wagers ({more.spreads.length + more.totals.length})</summary>
          <div className="lb-more__grid">
            {showSpread && more.spreads.map((r, i) => (
              <div key={`s${i}`} className="lb-more__row"><small>{game.spread_label}</small>{tile(r.away)}{tile(r.home)}</div>
            ))}
            {showTotal && more.totals.map((r, i) => (
              <div key={`t${i}`} className="lb-more__row"><small>Total</small>{tile(r.over)}{tile(r.under)}</div>
            ))}
          </div>
        </details>
      )}

      {open && shoppable.length > 0 && !(filter === 'PROPS') && (
        <details className="lb-shop">
          <summary>Shop books ({game.books_available})</summary>
          <div className="lb-shop__list">
            {shoppable.map((o) => (
              <div key={`${o.side}|${o.line}|${o.best.market_type}`} className="lb-shop__option">
                <strong>{tileLabel(o, game).pick || o.selection}</strong>
                <div className="lb-shop__books">
                  {o.quotes.map((quote) => (
                    <button key={quote.quote_id} type="button" disabled={!canEnter || busy === quote.quote_id}
                      aria-pressed={selected.has(quote.quote_id)} onClick={() => onPick(quote)}
                      aria-label={`${tileLabel(o, game, quote).pick} ${odds(quote.american_odds)} at ${quote.sportsbook_name}`}>
                      <span>{quote.sportsbook_name}</span><b>{odds(quote.american_odds)}</b>
                      {quote.quote_id === o.best.quote_id && <em>Best</em>}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </details>
      )}

      {showProps && (
        <section className="lb-props" aria-label="Player props">
          <h3>Player props</h3>
          {game.props.groups.length > 0 && (
            <div className="lb-props__groups">
              {game.props.groups.map((g) => <span key={g.label}>{g.label}</span>)}
            </div>
          )}
          <p className="lb-props__msg">{game.props.message}</p>
        </section>
      )}
    </article>
  )
}

function OddsTile({ option, game, canEnter, signedIn, busy, selected, onPick }: {
  option: BoardOption | null; game: MarketGame; canEnter: boolean; signedIn: boolean
  busy: string | null; selected: Set<string | null>; onPick: (q: BoardQuote) => void
}) {
  if (!option) return <span className="lb-tile lb-tile--empty" aria-label="Price unavailable">—</span>
  const label = tileLabel(option, game)
  const on = option.quotes.some((q) => selected.has(q.quote_id))
  const title = !signedIn ? 'Sign in to make picks' : !canEnter ? 'This login can browse but not pick' : `${label.pick} ${label.price} at ${option.best.sportsbook_name}`
  return (
    <button type="button" className={`lb-tile${on ? ' lb-tile--on' : ''}`} disabled={!canEnter || busy === option.best.quote_id}
      aria-pressed={on} title={title} aria-label={`${label.pick} ${label.price}`} onClick={() => onPick(option.best)}>
      <span className="lb-tile__pick">{label.pick}</span>
      <b className="lb-tile__price">{label.price}</b>
      <small className="lb-tile__book">{option.best.sportsbook_name}{option.best_of > 1 ? ` · best of ${option.best_of}` : ''}</small>
    </button>
  )
}

// ---------------------------------------------------------------------------
function PickSlip({ participant, sport, drafts, receipts, moves, busy, now, onStake, onRemove, onClear, onPlace, onAcceptMove, onDismissReceipt }: {
  participant: string; sport: string; drafts: SlipWager[]; receipts: SlipWager[]
  moves: Record<string, MoveNotice>; busy: string | null; now: number
  onStake: (d: SlipWager, stake: string) => Promise<void>
  onRemove: (d: SlipWager) => void
  onClear: () => void
  onPlace: (d: SlipWager, stake: string) => void
  onAcceptMove: (d: SlipWager, replacement: BoardQuote) => void
  onDismissReceipt: (id: string) => void
}) {
  return (
    <section className="lb-slip" aria-label="Pick Slip items">
      <header className="lb-slip__head">
        <div>
          <span className="lb-slip__eyebrow"><Ticket size={15} aria-hidden /> Pick Slip · virtual</span>
          <h2>{participant || '—'}</h2>
        </div>
        <span className="lb-slip__count">{drafts.length}</span>
      </header>
      {receipts.map((r) => <Receipt key={r.wager_id} r={r} onDismiss={() => onDismissReceipt(r.wager_id)} />)}
      {drafts.length === 0 && receipts.length === 0 && (
        <p className="lb-slip__empty">Tap any price to add a straight wager. Each wager is placed on its own; parlays are not available yet.</p>
      )}
      {drafts.map((d) => (
        <SlipItem key={d.wager_id} d={d} participant={participant} sport={sport} move={moves[d.wager_id]}
          busy={busy === d.wager_id} now={now} onStake={onStake} onRemove={onRemove} onPlace={onPlace} onAcceptMove={onAcceptMove} />
      ))}
      {drafts.length > 1 && <button type="button" className="lb-clear" disabled={busy === 'clear'} onClick={onClear}>Clear slip</button>}
      {drafts.length === 1 && <button type="button" className="lb-clear" disabled={busy === 'clear'} onClick={onClear}>Clear</button>}
    </section>
  )
}

function pickText(d: SlipWager) {
  if (d.market_type === 'TOTAL') return `${d.selection} ${d.line}`
  if (d.market_type === 'SPREAD') return `${d.selection} ${signed(d.line)}`
  return d.selection ?? ''
}

function SlipItem({ d, participant, sport, move, busy, now, onStake, onRemove, onPlace, onAcceptMove }: {
  d: SlipWager; participant: string; sport: string; move?: MoveNotice; busy: boolean; now: number
  onStake: (d: SlipWager, stake: string) => Promise<void>
  onRemove: (d: SlipWager) => void
  onPlace: (d: SlipWager, stake: string) => void
  onAcceptMove: (d: SlipWager, replacement: BoardQuote) => void
}) {
  const [stake, setStake] = useState(d.stake ? d.stake.toFixed(2) : '')
  const [saving, setSaving] = useState(false)
  const timer = useRef<number | null>(null)
  useEffect(() => { setStake(d.stake ? d.stake.toFixed(2) : '') }, [d.wager_id, d.stake])
  const commit = (value: string) => {
    setStake(value)
    if (timer.current) window.clearTimeout(timer.current)
    setSaving(true)
    timer.current = window.setTimeout(async () => { await onStake(d, value); setSaving(false) }, 450)
  }
  const numeric = Number(stake)
  const valid = stake !== '' && Number.isFinite(numeric) && numeric > 0
  const locked = Date.parse(d.human_lock_at) <= now
  return (
    <div className="lb-item">
      <div className="lb-item__top">
        <div>
          <small>{participant} · {sport} · {d.matchup}</small>
          <strong>{pickText(d)}</strong>
          <span className="lb-item__market">{d.market_label}{d.line != null ? ` · line ${d.market_type === 'TOTAL' ? d.line : signed(d.line)}` : ''}</span>
        </div>
        <button type="button" className="lb-icon" aria-label="Remove from slip" onClick={() => onRemove(d)} disabled={busy}><X size={18} /></button>
      </div>
      <div className="lb-item__price">
        <b>{odds(d.american_odds)}</b>
        <span>{d.sportsbook_name ?? d.sportsbook}</span>
        <small>price as of {d.quote_observed_at ? clock(d.quote_observed_at) : '—'}</small>
      </div>
      {move && (
        <div className="lb-move" role="alert">
          <strong>{move.replacement ? 'The price changed' : 'Price no longer available'}</strong>
          <span>{move.replacement
            ? `Was ${move.previous}. Now ${odds(move.replacement.american_odds)} at ${move.replacement.sportsbook_name}${move.replacement.line != null ? `, line ${move.replacement.market_type === 'TOTAL' ? move.replacement.line : signed(move.replacement.line)}` : ''}.`
            : 'Remove this pick and choose a current price.'}</span>
          {move.replacement && <button type="button" className="bx-btn" disabled={busy} onClick={() => onAcceptMove(d, move.replacement!)}>Use updated price</button>}
        </div>
      )}
      <label className="lb-item__stake">
        <span>Stake</span>
        <span className="lb-item__input"><b>$</b><input inputMode="decimal" value={stake} placeholder="0.00"
          onChange={(e) => commit(e.target.value.replace(/[^0-9.]/g, ''))} aria-label="Stake" /></span>
      </label>
      <div className="lb-quick" role="group" aria-label="Quick stake">
        {QUICK_STAKES.map((v) => <button key={v} type="button" aria-pressed={numeric === v} onClick={() => commit(v.toFixed(2))}>${v}</button>)}
      </div>
      <dl className="lb-item__math">
        <div><dt>Potential profit</dt><dd>{saving ? '…' : money(d.potential_profit)}</dd></div>
        <div><dt>Total payout</dt><dd>{saving ? '…' : money(d.potential_payout)}</dd></div>
        <div><dt>Available after</dt><dd>{saving ? '…' : money(d.available_after)}</dd></div>
      </dl>
      <p className="lb-item__refund">{d.refund_rule}</p>
      <button type="button" className="lb-place" disabled={busy || saving || !valid || !!move || locked}
        onClick={() => onPlace(d, Number(stake).toFixed(2))}>
        {locked ? 'Game locked' : busy ? 'Placing…' : `Place Virtual Wager${valid ? ` · ${money(numeric)}` : ''}`}
      </button>
    </div>
  )
}

function Receipt({ r, onDismiss }: { r: SlipWager; onDismiss: () => void }) {
  return (
    <div className="lb-receipt" role="status" aria-label="Wager receipt">
      <div className="lb-receipt__head"><strong>Wager placed · locked</strong><span>#{r.confirmation_id}</span></div>
      <dl>
        <div><dt>Game</dt><dd>{r.matchup}</dd></div>
        <div><dt>Pick</dt><dd>{pickText(r)}</dd></div>
        <div><dt>Market</dt><dd>{r.market_label}</dd></div>
        <div><dt>Odds</dt><dd>{odds(r.american_odds)} · {r.sportsbook_name ?? r.sportsbook}</dd></div>
        <div><dt>Stake</dt><dd>{money(r.stake)}</dd></div>
        <div><dt>Payout if it wins</dt><dd>{money(r.potential_payout)}</dd></div>
        <div><dt>Submitted</dt><dd>{r.submitted_at ? new Date(r.submitted_at).toLocaleString('en-US', { ...ET, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' }) + ' ET' : '—'}</dd></div>
        {r.entered_on_behalf && <div><dt>Entered by</dt><dd>{r.entered_by} (on behalf)</dd></div>}
      </dl>
      <p className="lb-receipt__note">These terms can’t be changed. Others see this pick once picks for the game close.</p>
      <button type="button" className="bx-btn" onClick={onDismiss}>Done</button>
    </div>
  )
}

function OpenPicks({ account }: { account: ParticipantAccount | null }) {
  if (!account) return null
  return (
    <section className="lb-open" aria-label="Open picks">
      <header><strong>Open Picks</strong><span>{account.display_name} · {account.sport}</span></header>
      {account.open_picks.length === 0 && <p className="bx-muted">No open picks in {account.sport}.</p>}
      {account.open_picks.map((w) => (
        <div key={w.wager_id} className="lb-open__row">
          <div>
            <strong>{pickText(w)} {odds(w.american_odds)}</strong>
            <small>{w.matchup} · {w.market_label} · {w.sportsbook_name ?? w.sportsbook}</small>
          </div>
          <div className="lb-open__money"><strong>{money(w.stake)}</strong><small>#{w.confirmation_id}</small></div>
        </div>
      ))}
    </section>
  )
}
