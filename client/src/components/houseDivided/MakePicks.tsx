/**
 * Make Picks — a familiar sportsbook flow on top of the competition ledger.
 *
 * Tapping a price adds a straight wager to the Pick Slip as a server draft
 * that names the stored quote. Every number on the slip (odds, line, book,
 * potential profit, available funds) comes back from the server; the browser
 * sends only the quote id, the stake, and the quote it was looking at.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../../hooks/useAuth'
import { TeamMark } from '../broadcast/primitives'
import {
  CompetitionRefusal, americanOdds, houseDivided, lineText, money,
  type BoardOption, type BoardQuote, type CompetitionSport, type EntryAccess, type MarketBoard, type MarketGame,
  type ParticipantAccount, type SlipWager,
} from '../../services/houseDivided'

const QUICK_STAKES = [5, 10, 25, 50]
const ET: Intl.DateTimeFormatOptions = { timeZone: 'America/New_York' }

const clock = (iso: string | null | undefined) => (iso
  ? `${new Date(iso).toLocaleTimeString('en-US', { ...ET, hour: 'numeric', minute: '2-digit' })} ET` : '—')
const dayLabel = (day: string, today: string) => {
  if (day === today) return 'Today'
  const d = new Date(`${day}T12:00:00Z`)
  return d.toLocaleDateString('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric' })
}
const todayEt = () => new Date().toLocaleDateString('en-CA', ET)
const minutesAgo = (iso: string | null | undefined, now: number) =>
  iso ? Math.max(0, Math.round((now - Date.parse(iso)) / 60_000)) : null

export function countdown(targetIso: string, now: number): string {
  const ms = Date.parse(targetIso) - now
  if (ms <= 0) return 'Locked'
  const minutes = Math.floor(ms / 60_000)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (h >= 24) return `Locks in ${Math.floor(h / 24)}d ${h % 24}h`
  return h > 0 ? `Locks in ${h}h ${m}m` : `Locks in ${m}m`
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

export function MakePicks({ sport, sports, onSport, onChanged }: {
  sport: CompetitionSport
  sports: { sport: CompetitionSport; status: string }[]
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
  const openRef = useRef<HTMLDivElement | null>(null)

  const canEnter = !!access && access.can_enter_for.length > 0

  useEffect(() => {
    if (!user) { setAccess(null); return }
    houseDivided.me().then((a) => {
      setAccess(a)
      setWho((current) => (current && a.can_enter_for.includes(current) ? current : a.can_enter_for[0] ?? ''))
    }).catch(() => setAccess(null))
  }, [user])

  const loadBoard = useCallback(() => {
    houseDivided.marketBoard(sport, date).then((b) => { setBoard(b); setBoardError(null) })
      .catch((e: Error) => setBoardError(e.message))
  }, [sport, date])
  useEffect(() => { setBoard(null); loadBoard() }, [loadBoard])
  useEffect(() => {
    const id = window.setInterval(loadBoard, 60_000)
    return () => window.clearInterval(id)
  }, [loadBoard])
  useEffect(() => { setDate(null) }, [sport])

  const loadAccount = useCallback(() => {
    if (!canEnter || !who) { setAccount(null); return }
    houseDivided.account(who, sport).then(setAccount).catch(() => setAccount(null))
  }, [canEnter, who, sport])
  useEffect(() => { loadAccount() }, [loadAccount])

  const drafts = account?.drafts ?? []
  const draftByOutcome = useMemo(() => {
    const map = new Map<string, SlipWager>()
    for (const d of drafts) map.set(`${d.game_id}|${d.market_type}`, d)
    return map
  }, [drafts])
  const selectedQuotes = useMemo(() => new Set(drafts.map((d) => d.quote_id)), [drafts])

  const refresh = () => { loadAccount(); loadBoard(); onChanged() }

  const pick = async (game: MarketGame, quote: BoardQuote) => {
    if (!canEnter) return
    setNotice(null)
    const existing = draftByOutcome.get(`${game.game_id}|${quote.market_type}`)
    setBusy(quote.quote_id)
    try {
      if (existing && existing.quote_id === quote.quote_id) await houseDivided.slipRemove(existing.wager_id)
      else if (existing) await houseDivided.slipUpdate(existing.wager_id, who, quote.quote_id, existing.stake ? String(existing.stake) : '')
      else await houseDivided.slipAdd(who, quote.quote_id)
      loadAccount()
    } catch (e) {
      setNotice({ tone: 'bad', text: e instanceof Error ? e.message : 'Refused' })
    } finally { setBusy(null) }
  }

  const participantName = access?.participants?.find((p) => p.key === who)?.display_name ?? who
  const slip = (
    <PickSlip
      participant={participantName} sport={sport} drafts={drafts} receipts={receipts} moves={moves}
      busy={busy} now={now}
      onStake={async (d, stake) => {
        await houseDivided.slipUpdate(d.wager_id, who, d.quote_id ?? '', stake).catch(() => undefined)
        loadAccount()
      }}
      onRemove={async (d) => {
        setBusy(d.wager_id)
        await houseDivided.slipRemove(d.wager_id).catch(() => undefined)
        setMoves((m) => { const next = { ...m }; delete next[d.wager_id]; return next })
        setBusy(null); loadAccount()
      }}
      onAcceptMove={async (d, replacement) => {
        setBusy(d.wager_id)
        try {
          await houseDivided.slipUpdate(d.wager_id, who, replacement.quote_id, d.stake ? String(d.stake) : '')
          setMoves((m) => { const next = { ...m }; delete next[d.wager_id]; return next })
          setNotice({ tone: 'good', text: 'New price accepted. Review it and place the wager again.' })
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
              previous: `${americanOdds(d.american_odds)} at ${d.sportsbook ?? '—'}`,
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

  return (
    <div className="mp">
      {!user && (
        <div className="mp-signin" role="region" aria-label="Sign in to make picks">
          <div>
            <strong>Sign in to make picks</strong>
            <span>Scotty and Red Lou pick with their own AISP login. An administrator can pick on their behalf.</span>
          </div>
          <Link className="bx-btn bx-btn--primary" to="/login">Sign in to make picks</Link>
        </div>
      )}
      {user && access && !canEnter && (
        <div className="mp-signin mp-signin--muted" role="note">
          <div>
            <strong>This login isn’t linked to Scotty or Red Lou</strong>
            <span>You can browse the board. An administrator can link your login or place picks on your behalf.</span>
          </div>
        </div>
      )}

      <div className="mp-bar">
        {canEnter && (
          <label className="mp-bar__field">
            <span>Picking for</span>
            {access!.is_admin
              ? (
                <select value={who} onChange={(e) => { setWho(e.target.value); setMoves({}) }} aria-label="Picking for">
                  {(access!.participants ?? []).map((p) => <option key={p.key} value={p.key}>{p.display_name}</option>)}
                </select>
              )
              : <strong className="mp-bar__who">{participantName}</strong>}
          </label>
        )}
        <div className="mp-bar__field">
          <span>Sport</span>
          <div className="mp-chips" role="group" aria-label="Sport">
            {sports.map((s) => (
              <button key={s.sport} type="button" aria-pressed={s.sport === sport} onClick={() => onSport(s.sport)}
                disabled={s.status !== 'STARTED'} title={s.status !== 'STARTED' ? 'Not started' : undefined}>
                {s.sport}
              </button>
            ))}
          </div>
        </div>
        <div className="mp-bar__field mp-bar__dates">
          <span>Date</span>
          <div className="mp-chips" role="group" aria-label="Date">
            {dates.length === 0 && <small className="bx-muted">No upcoming games</small>}
            {dates.map((d) => (
              <button key={d.date} type="button" aria-pressed={d.date === activeDate} onClick={() => setDate(d.date)}>
                {dayLabel(d.date, today)} <small>{d.games}</small>
              </button>
            ))}
          </div>
        </div>
        {canEnter && account && (
          <div className="mp-bar__money" aria-label="Selected account">
            <div><span>Available</span><strong>{money(account.available)}</strong></div>
            <div><span>Open exposure</span><strong>{money(account.open_exposure)}</strong></div>
            <button type="button" className="bx-btn" onClick={() => {
              if (window.matchMedia('(max-width: 1099px)').matches) setDrawer(true)
              else openRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
            }}>Open picks ({account.open_picks.length})</button>
          </div>
        )}
      </div>

      {notice && <p className={`hd-msg hd-msg--${notice.tone}`} role="status">{notice.text}</p>}

      <div className="mp-layout">
        <div className="mp-main">
          {boardError && <div className="bx-empty" role="alert">The market board could not be loaded: {boardError}</div>}
          {!board && !boardError && <div className="bx-empty" role="status">Loading the market board…</div>}
          {board && board.status !== 'STARTED' && <div className="bx-empty">{sport} has not started. {board.reason}</div>}
          {board && board.status === 'STARTED' && board.games.length === 0 && <div className="bx-empty">No {sport} games on this date.</div>}
          {board?.games.map((g) => (
            <GameMarketCard key={g.game_id} game={g} sport={sport} now={now} canEnter={canEnter}
              signedIn={!!user} busy={busy} selected={selectedQuotes} onPick={(q) => pick(g, q)} />
          ))}
        </div>
        {canEnter && (
          <aside className="mp-aside" aria-label="Pick Slip">
            {slip}
            <div ref={openRef}><OpenPicks account={account} /></div>
          </aside>
        )}
      </div>

      {canEnter && (
        <>
          <button type="button" className="mp-slipbar" onClick={() => setDrawer(true)}>
            Pick Slip · {drafts.length} selection{drafts.length === 1 ? '' : 's'}
            {account && <small>{money(account.available)} available</small>}
          </button>
          {drawer && (
            <div className="mp-drawer" role="dialog" aria-modal="true" aria-label="Pick Slip">
              <button type="button" className="mp-drawer__scrim" aria-label="Close pick slip" onClick={() => setDrawer(false)} />
              <div className="mp-drawer__sheet">
                <div className="mp-drawer__head">
                  <strong>Pick Slip</strong>
                  <button type="button" className="bx-btn bx-btn--ghost" onClick={() => setDrawer(false)}>Close</button>
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
function GameMarketCard({ game, sport, now, canEnter, signedIn, busy, selected, onPick }: {
  game: MarketGame; sport: CompetitionSport; now: number; canEnter: boolean; signedIn: boolean
  busy: string | null; selected: Set<string | null>; onPick: (q: BoardQuote) => void
}) {
  const updated = minutesAgo(game.last_update, now)
  const lockText = game.locked ? 'Market locked' : countdown(game.human_lock_at, now)
  return (
    <article className={`mp-game${game.locked ? ' mp-game--locked' : ''}`} aria-label={`${game.away?.abbreviation} at ${game.home?.abbreviation}`}>
      <header className="mp-game__head">
        <div className="mp-game__teams">
          <span className="mp-team">{game.away && <TeamMark league={sport} abbreviation={game.away.abbreviation} size={30} />}<b>{game.away?.name ?? 'Away'}</b></span>
          <span className="mp-at">@</span>
          <span className="mp-team">{game.home && <TeamMark league={sport} abbreviation={game.home.abbreviation} size={30} />}<b>{game.home?.name ?? 'Home'}</b></span>
        </div>
        <div className="mp-game__meta">
          <span>{clock(game.starts_at)}</span>
          <span className={`mp-lock${game.locked ? ' mp-lock--closed' : ''}`}
            title={game.lock_basis === 'MODEL_PICK_PUBLIC' ? 'Locks when the models’ pick becomes public' : 'Locks 15 minutes before the start'}>
            {lockText}{!game.locked && game.lock_basis === 'MODEL_PICK_PUBLIC' ? ' · model pick public' : ''}
          </span>
          <span className="mp-fresh">{updated == null ? 'No current prices yet' : `Prices updated ${updated} min ago`}</span>
        </div>
      </header>
      {game.locked && game.lock_reason && <p className="mp-game__reason">{game.lock_reason}</p>}
      {!game.locked && (
        <div className="mp-markets">
          {game.markets.map((m) => (
            <section key={m.market_type} className="mp-market" aria-label={m.label}>
              <h3>{m.label}</h3>
              {m.status !== 'OPEN'
                ? <p className="mp-market__reason">{m.reason}</p>
                : (
                  <div className="mp-tiles">
                    {m.options.map((o) => (
                      <OptionTile key={`${o.side}|${o.line}|${o.best.quote_id}`} option={o} game={game}
                        canEnter={canEnter} signedIn={signedIn} busy={busy} selected={selected} onPick={onPick} />
                    ))}
                  </div>
                )}
            </section>
          ))}
        </div>
      )}
    </article>
  )
}

function optionName(o: BoardOption, game: MarketGame) {
  if (o.side === 'home') return game.home?.abbreviation ?? o.selection
  if (o.side === 'away') return game.away?.abbreviation ?? o.selection
  return o.side === 'over' ? 'Over' : o.side === 'under' ? 'Under' : o.selection
}

function OptionTile({ option, game, canEnter, signedIn, busy, selected, onPick }: {
  option: BoardOption; game: MarketGame; canEnter: boolean; signedIn: boolean
  busy: string | null; selected: Set<string | null>; onPick: (q: BoardQuote) => void
}) {
  const best = option.best
  const name = optionName(option, game)
  const line = lineText(best.market_type, option.line)
  const title = !signedIn ? 'Sign in to make picks' : !canEnter ? 'This login cannot place picks' : `Add ${name} ${line} ${americanOdds(best.american_odds)} to the Pick Slip`
  const isOn = option.quotes.some((q) => selected.has(q.quote_id))
  return (
    <div className="mp-option">
      <button type="button" className={`mp-tile${selected.has(best.quote_id) ? ' mp-tile--on' : ''}`}
        disabled={!canEnter || busy === best.quote_id} aria-pressed={selected.has(best.quote_id)}
        title={title} onClick={() => onPick(best)}>
        <span className="mp-tile__pick">{name}{line ? ` ${line}` : ''}</span>
        <strong className="mp-tile__odds">{americanOdds(best.american_odds)}</strong>
        <span className="mp-tile__book">{best.sportsbook_name}</span>
        {option.best_of > 1 && <span className="mp-tile__best">Best current price</span>}
      </button>
      {option.quotes.length > 1 && (
        <details className="mp-shop" open={isOn && !selected.has(best.quote_id) ? true : undefined}>
          <summary>Shop books ({option.quotes.length})</summary>
          <div className="mp-shop__list">
            {option.quotes.map((q) => (
              <button key={q.quote_id} type="button" disabled={!canEnter || busy === q.quote_id}
                aria-pressed={selected.has(q.quote_id)} className={selected.has(q.quote_id) ? 'mp-shop--on' : ''}
                onClick={() => onPick(q)}>
                <span>{q.sportsbook_name}</span><strong>{americanOdds(q.american_odds)}</strong><small>{q.age_minutes}m</small>
              </button>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
function PickSlip({ participant, sport, drafts, receipts, moves, busy, now, onStake, onRemove, onPlace, onAcceptMove, onDismissReceipt }: {
  participant: string; sport: CompetitionSport; drafts: SlipWager[]; receipts: SlipWager[]
  moves: Record<string, MoveNotice>; busy: string | null; now: number
  onStake: (d: SlipWager, stake: string) => Promise<void>
  onRemove: (d: SlipWager) => void
  onPlace: (d: SlipWager, stake: string) => void
  onAcceptMove: (d: SlipWager, replacement: BoardQuote) => void
  onDismissReceipt: (id: string) => void
}) {
  return (
    <section className="mp-slip" aria-label="Pick Slip items">
      <header className="mp-slip__head">
        <div>
          <span className="bx-eyebrow">Pick Slip · virtual</span>
          <h2>{participant || '—'} · {sport}</h2>
        </div>
        <span className="mp-slip__count">{drafts.length}</span>
      </header>
      {receipts.map((r) => <Receipt key={r.wager_id} r={r} onDismiss={() => onDismissReceipt(r.wager_id)} />)}
      {drafts.length === 0 && receipts.length === 0 && (
        <p className="mp-slip__empty">Tap a price on the board to add a straight wager. One wager per submission; parlays are not supported yet.</p>
      )}
      {drafts.map((d) => (
        <SlipItem key={d.wager_id} d={d} move={moves[d.wager_id]} busy={busy === d.wager_id} now={now}
          onStake={onStake} onRemove={onRemove} onPlace={onPlace} onAcceptMove={onAcceptMove} />
      ))}
    </section>
  )
}

function SlipItem({ d, move, busy, now, onStake, onRemove, onPlace, onAcceptMove }: {
  d: SlipWager; move?: MoveNotice; busy: boolean; now: number
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
    timer.current = window.setTimeout(async () => {
      await onStake(d, value)
      setSaving(false)
    }, 450)
  }
  const numeric = Number(stake)
  const valid = stake !== '' && Number.isFinite(numeric) && numeric > 0
  const locked = Date.parse(d.human_lock_at) <= now
  const pickName = d.market_type === 'TOTAL' ? `${d.selection} ${lineText(d.market_type, d.line)}` : `${d.selection}${d.line != null ? ` ${lineText(d.market_type, d.line)}` : ''}`
  return (
    <div className="mp-item">
      <div className="mp-item__top">
        <div>
          <small>{d.matchup} · {d.market_label}</small>
          <strong>{pickName}</strong>
        </div>
        <button type="button" className="mp-item__x" aria-label="Remove from slip" onClick={() => onRemove(d)} disabled={busy}>×</button>
      </div>
      <div className="mp-item__price">
        <strong>{americanOdds(d.american_odds)}</strong>
        <span>{d.sportsbook}</span>
        <small>quote {d.quote_age_minutes ?? '—'} min old</small>
      </div>
      {move && (
        <div className="mp-move" role="alert">
          <strong>{move.replacement ? 'Price moved' : 'Price unavailable'}</strong>
          <span>{move.replacement
            ? `Was ${move.previous}; now ${americanOdds(move.replacement.american_odds)} at ${move.replacement.sportsbook_name}${move.replacement.line != null ? ` (line ${lineText(move.replacement.market_type, move.replacement.line)})` : ''}.`
            : move.message}</span>
          {move.replacement && <button type="button" className="bx-btn" disabled={busy} onClick={() => onAcceptMove(d, move.replacement!)}>Accept new price</button>}
        </div>
      )}
      <label className="mp-item__stake">
        <span>Stake</span>
        <span className="mp-item__input"><b>$</b><input inputMode="decimal" value={stake} placeholder="0.00"
          onChange={(e) => commit(e.target.value.replace(/[^0-9.]/g, ''))} aria-label="Stake" /></span>
      </label>
      <div className="mp-quick" role="group" aria-label="Quick stake">
        {QUICK_STAKES.map((v) => <button key={v} type="button" onClick={() => commit(v.toFixed(2))}>${v}</button>)}
      </div>
      <dl className="mp-item__math">
        <div><dt>Available now</dt><dd>{money(d.available_before)}</dd></div>
        <div><dt>Available after</dt><dd>{saving ? '…' : money(d.available_after)}</dd></div>
        <div><dt>Potential profit</dt><dd>{saving ? '…' : money(d.potential_profit)}</dd></div>
        <div><dt>Total payout if it wins</dt><dd>{saving ? '…' : money(d.potential_payout)}</dd></div>
      </dl>
      <p className="mp-item__refund">{d.refund_rule}</p>
      <button type="button" className="bx-btn bx-btn--primary mp-place"
        disabled={busy || saving || !valid || !!move || locked}
        onClick={() => onPlace(d, Number(stake).toFixed(2))}>
        {locked ? 'Market locked' : busy ? 'Placing…' : 'Place Virtual Wager'}
      </button>
    </div>
  )
}

function Receipt({ r, onDismiss }: { r: SlipWager; onDismiss: () => void }) {
  return (
    <div className="mp-receipt" role="status" aria-label="Wager receipt">
      <div className="mp-receipt__head"><strong>Wager placed</strong><span>#{r.confirmation_id}</span></div>
      <dl>
        <div><dt>Game</dt><dd>{r.matchup}</dd></div>
        <div><dt>Market</dt><dd>{r.market_label}</dd></div>
        <div><dt>Selection</dt><dd>{r.selection}{r.line != null ? ` ${lineText(r.market_type, r.line)}` : ''}</dd></div>
        <div><dt>Odds</dt><dd>{americanOdds(r.american_odds)} · {r.sportsbook}</dd></div>
        <div><dt>Stake</dt><dd>{money(r.stake)}</dd></div>
        <div><dt>Payout if it wins</dt><dd>{money(r.potential_payout)}</dd></div>
        <div><dt>Submitted</dt><dd>{r.submitted_at ? new Date(r.submitted_at).toLocaleString('en-US', { ...ET, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' }) + ' ET' : '—'}</dd></div>
        {r.entered_on_behalf && <div><dt>Entered by</dt><dd>{r.entered_by} (on behalf)</dd></div>}
      </dl>
      <p className="mp-receipt__note">Terms are locked. Revealed to the others when picks for this game lock.</p>
      <button type="button" className="bx-btn" onClick={onDismiss}>Done</button>
    </div>
  )
}

function OpenPicks({ account }: { account: ParticipantAccount | null }) {
  if (!account) return null
  return (
    <section className="mp-open" aria-label="Open picks">
      <header><span className="bx-eyebrow">Open picks · {account.display_name}</span></header>
      {account.open_picks.length === 0 && <p className="bx-muted">No open picks in {account.sport}.</p>}
      {account.open_picks.map((w) => (
        <div key={w.wager_id} className="mp-open__row">
          <div>
            <strong>{w.selection}{w.line != null ? ` ${lineText(w.market_type, w.line)}` : ''}</strong>
            <small>{w.matchup} · {w.market_label} · {americanOdds(w.american_odds)} {w.sportsbook}</small>
          </div>
          <div className="mp-open__money">
            <strong>{money(w.stake)}</strong>
            <small>#{w.confirmation_id}</small>
          </div>
        </div>
      ))}
    </section>
  )
}
