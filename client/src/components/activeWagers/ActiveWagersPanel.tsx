/**
 * Active Wagers: a persistent view of the funded paper positions still in play,
 * mounted once in the app shell so it follows the reader across every page.
 *
 * Desktop (>= 1280 px): a sticky right-hand panel with its own reserved column,
 * collapsible to a compact rail that still shows the open count and the stake
 * at risk. Smaller screens: a floating pill that opens an accessible bottom
 * sheet (focus trapped, Esc closes, focus returns to the pill).
 *
 * Display only. Figures come from the experiment ledger (see
 * services/activeWagers); nothing here places, funds, stakes or settles.
 */
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ChevronDown, ChevronRight, ChevronsLeft, ChevronsRight, Ticket, X } from 'lucide-react'
import {
  SPORT_COLORS, SPORT_NAMES, etTime, gameHref, money,
} from '../../services/commandCenter'
import { ptsText } from '../../services/decisionEdges'
import { shortBook } from '../../services/topPlay'
import {
  WAGER_GROUP_LABEL, WAGER_GROUP_ORDER, countdown, readCollapsed, writeCollapsed,
  type ActiveWager, type WatchItem,
} from '../../services/activeWagers'
import { AsOf } from '../command/RegionState'
import { useActiveWagers } from './useActiveWagers'
import './activeWagers.css'

export const DESKTOP_QUERY = '(min-width: 1280px)'

export function useMediaQuery(query: string, fallback = true): boolean {
  const get = () => (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : fallback)
  const [matches, setMatches] = useState(get)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    onChange()
    mql.addEventListener?.('change', onChange)
    return () => mql.removeEventListener?.('change', onChange)
  }, [query])
  return matches
}

const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const pct = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(1)}%`)
const MARKET_LABEL: Record<string, string> = { MONEYLINE: 'Moneyline', SPREAD: 'Spread', RUN_LINE: 'Run line', TOTAL: 'Total' }
const marketText = (m: string) => MARKET_LABEL[m] ?? m.replace(/_/g, ' ').toLowerCase()
const HISTORY_HREF = '/experiment'

type Data = ReturnType<typeof useActiveWagers>

/**
 * Mounted by the app shell. Owns the collapse state, so it survives every
 * route change, and mirrors it to localStorage so it survives a reload.
 */
export function ActiveWagersDock({ onLayout }: { onLayout?: (mode: 'expanded' | 'collapsed' | 'mobile') => void }) {
  const data = useActiveWagers()
  const desktop = useMediaQuery(DESKTOP_QUERY)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const toggled = useRef(false)
  const toggle = useCallback(() => { toggled.current = true; setCollapsed((c) => { writeCollapsed(!c); return !c }) }, [])
  // The toggle unmounts the button that was pressed; keep keyboard focus on its counterpart.
  useEffect(() => {
    if (!toggled.current) return
    toggled.current = false
    document.querySelector<HTMLElement>(collapsed ? '.aw-rail__button' : '.aw-panel [data-collapse]')?.focus()
  }, [collapsed])
  const mode = desktop ? (collapsed ? 'collapsed' : 'expanded') : 'mobile'
  useEffect(() => { onLayout?.(mode) }, [mode, onLayout])

  if (!desktop) return <WagersSheet data={data} />
  if (collapsed) {
    return (
      <aside className="aw-rail" aria-label="Active wagers (collapsed)">
        <button type="button" className="aw-rail__button" onClick={toggle} aria-expanded={false}
          aria-label={`Show active wagers: ${countLabel(data)}`}>
          <ChevronsLeft size={16} aria-hidden="true" />
          <Ticket size={18} aria-hidden="true" />
          <b className="aw-rail__count">{data.ledger.data ? data.summary.openCount : '—'}</b>
          <small>open</small>
          <b className="aw-rail__money">{data.ledger.data ? money(data.summary.stakeAtRisk) : '—'}</b>
          <small>at risk</small>
          {data.ledger.error && <span className="aw-rail__warn" title="Unable to refresh">!</span>}
        </button>
      </aside>
    )
  }
  return (
    <aside className="aw-panel cc-tokens" id="active-wagers-panel" aria-labelledby="active-wagers-title">
      <header className="aw-panel__head">
        <div>
          <span className="aw-kicker">Paper · $1,000 Experiment</span>
          <h2 id="active-wagers-title">Active wagers</h2>
        </div>
        <button type="button" className="aw-icon" onClick={toggle} aria-expanded={true} aria-controls="active-wagers-panel" aria-label="Collapse active wagers" data-collapse>
          <ChevronsRight size={18} aria-hidden="true" />
        </button>
      </header>
      <div className="aw-panel__body"><WagersBody data={data} /></div>
    </aside>
  )
}

function countLabel(data: Data) {
  if (!data.ledger.data) return data.ledger.error ? 'ledger unavailable' : 'loading'
  return `${data.summary.openCount} open, ${money(data.summary.stakeAtRisk)} at risk`
}

// ------------------------------------------------------------ mobile sheet --

function WagersSheet({ data }: { data: Data }) {
  const [open, setOpen] = useState(false)
  const pill = useRef<HTMLButtonElement>(null)
  const sheet = useRef<HTMLDivElement>(null)
  const location = useLocation()
  const titleId = useId()
  const close = useCallback(() => {
    setOpen(false)
    // Return focus to what opened the sheet.
    window.setTimeout(() => pill.current?.focus(), 0)
  }, [])
  // Following a link inside the sheet navigates; the sheet closes behind it.
  useEffect(() => { setOpen(false) }, [location.pathname, location.search])
  useEffect(() => {
    if (!open) return
    const node = sheet.current
    node?.querySelector<HTMLElement>('[data-autofocus]')?.focus()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); close(); return }
      if (e.key !== 'Tab' || !node) return
      const focusables = [...node.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')]
        .filter((el) => el.offsetParent !== null || el === document.activeElement)
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
      else if (!node.contains(document.activeElement)) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = previousOverflow }
  }, [open, close])

  const ready = Boolean(data.ledger.data)
  return (
    <>
      <button ref={pill} type="button" className={`aw-pill${data.ledger.error ? ' aw-pill--warn' : ''}`} onClick={() => setOpen(true)}
        aria-haspopup="dialog" aria-expanded={open} aria-label={`Active wagers: ${countLabel(data)}`}>
        <Ticket size={16} aria-hidden="true" />
        <span>Active wagers</span>
        <b>{ready ? data.summary.openCount : '—'}</b>
        {ready && data.summary.openCount > 0 && <small>{money(data.summary.stakeAtRisk)}</small>}
      </button>
      {open && (
        <div className="aw-sheet-layer">
          <div className="aw-sheet-scrim" aria-hidden="true" onClick={close} />
          <div ref={sheet} className="aw-sheet cc-tokens" role="dialog" aria-modal="true" aria-labelledby={titleId}>
            <header className="aw-panel__head">
              <div>
                <span className="aw-kicker">Paper · $1,000 Experiment</span>
                <h2 id={titleId}>Active wagers</h2>
              </div>
              <button type="button" className="aw-icon" onClick={close} aria-label="Close active wagers" data-autofocus>
                <X size={18} aria-hidden="true" />
              </button>
            </header>
            <div className="aw-panel__body"><WagersBody data={data} /></div>
          </div>
        </div>
      )}
    </>
  )
}

// -------------------------------------------------------------------- body --

export function WagersBody({ data }: { data: Pick<Data, 'ledger' | 'groups' | 'summary' | 'watching' | 'now' | 'board'> }) {
  const { ledger, groups, summary, watching, now } = data
  const has = ledger.data != null
  const next = countdown(summary.nextStart, now)
  return (
    <div className="aw-body">
      <p className="aw-scope"><span className="cc-chip">All sports</span> <AsOf resource={ledger} label="ledger" /></p>

      {!has && ledger.error && (
        <div className="aw-alert" role="alert">
          <b>Active wagers could not be loaded.</b>
          <span>This is a read failure, not an empty account. Open positions, if any, are still on the <Link to={HISTORY_HREF}>experiment ledger</Link>.</span>
        </div>
      )}
      {!has && !ledger.error && <p className="aw-muted" role="status">Reading the ledger…</p>}

      {has && (
        <>
          <dl className="aw-summary" aria-label="Active wager summary">
            <div><dt>Open wagers</dt><dd>{summary.openCount}</dd></div>
            <div><dt>Stake at risk</dt><dd>{money(summary.stakeAtRisk)}</dd></div>
            <div><dt>Paper bankroll</dt><dd>{money(summary.bankroll)}</dd></div>
            <div><dt>Next start</dt><dd>{next ?? '—'}</dd></div>
          </dl>
          {!summary.reconciled && (
            <p className="aw-note aw-note--warn" role="note">
              Positions total {money(summary.stakeAtRisk)} across {summary.openCount}; the ledger reports {money(summary.ledgerOpenExposure)} across {summary.ledgerOpenCount ?? '—'}. Treat the ledger as authoritative.
            </p>
          )}

          {summary.openCount === 0
            ? (
              <div className="aw-empty">
                <p><b>No open wagers{ledger.error ? ' in the last successful view' : ''}.</b></p>
                <p className="aw-muted">A position is funded only when a forecast, an eligible policy and a fresh price qualify inside a decision window. Settled wagers are in the <Link to={HISTORY_HREF}>wager history</Link>.</p>
              </div>
            )
            : WAGER_GROUP_ORDER.filter((g) => groups[g].length > 0).map((g) => (
              <section key={g} className="aw-group" aria-label={`${WAGER_GROUP_LABEL[g]} wagers`}>
                <h3 className={`aw-group__title aw-group__title--${g.toLowerCase()}`}>{WAGER_GROUP_LABEL[g]} <span>{groups[g].length}</span></h3>
                {groups[g].map((w) => <WagerCard key={w.position_id} wager={w} now={now} />)}
              </section>
            ))}
        </>
      )}

      <WatchingSection items={watching} board={data.board} />

      <p className="aw-foot"><Link to={HISTORY_HREF}>Recent results &amp; wager history →</Link></p>
    </div>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>
}

export function WagerCard({ wager: w, now }: { wager: ActiveWager; now: number }) {
  const [open, setOpen] = useState(false)
  const detailsId = useId()
  const cd = countdown(w.starts_at, now)
  const tone = w.group === 'LIVE' ? 'live' : w.group === 'AWAITING' ? 'warn' : ''
  const d = w.decision
  return (
    <article className="aw-card" style={{ ['--sport' as string]: SPORT_COLORS[w.sport] }} aria-label={`${w.matchup ?? 'Game'}: ${w.selection ?? w.market}`}>
      <header className="aw-card__head">
        <span className="aw-card__sport"><i className="cc-dot" aria-hidden="true" />{SPORT_NAMES[w.sport]}</span>
        <span className={`aw-status${tone ? ` aw-status--${tone}` : ''}`}>{w.statusText}</span>
      </header>
      <Link className="aw-card__matchup" to={gameHref(w.sport, w.game_id)}>{w.matchup ?? 'Game'}</Link>
      <p className="aw-card__pick"><b>{w.selection ?? '—'}</b> · {marketText(w.market)} · <span className="aw-odds">{am(w.entry.american)}</span></p>
      <dl className="aw-card__grid">
        <Row label="Stake">{money(w.stake)}</Row>
        <Row label="Payout if it wins">{money(w.potentialPayout)}<small> incl. stake</small></Row>
        <Row label="Sportsbook">{shortBook(w.entry.sportsbook) || '—'}</Row>
        <Row label="Starts">{etTime(w.starts_at, true)}{cd ? <small> · in {cd}</small> : null}</Row>
      </dl>
      <button type="button" className="aw-more" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />} Details
      </button>
      {open && (
        <dl className="aw-card__details" id={detailsId}>
          <Row label="Placed">{etTime(w.placed_at, true)}</Row>
          <Row label="Quote observed">{w.entry.quote_observed_at ? `${etTime(w.entry.quote_observed_at, true)}${w.quoteAgeAtPlacementMinutes != null ? ` · ${w.quoteAgeAtPlacementMinutes} min before placement` : ''}` : 'not recorded'}</Row>
          <Row label="Price source">{w.entry.provider ?? '—'} · {w.entry.classification.toLowerCase()}</Row>
          <Row label="Model probability">{d ? pct(d.model_probability) : 'not available'}</Row>
          <Row label="Market probability (no-vig)">{d ? pct(d.market_probability) : 'not available'}</Row>
          <Row label="Edge vs offered price">{d ? ptsText(d.edge) : 'not available'}</Row>
          {d?.adjusted?.edge != null && <Row label="Policy-adjusted edge">{ptsText(d.adjusted.edge)}</Row>}
          <Row label="Decision">{d ? `${d.recommendation.replace(/_/g, ' ').toLowerCase()} · ${d.phase.replace(/_/g, ' ').toLowerCase()}${d.recommended_at ? ` · ${etTime(d.recommended_at)}` : ''}` : 'not in the loaded decision boards'}</Row>
          {d?.plain_reasons && d.plain_reasons.length > 0 && <Row label="Why">{d.plain_reasons[0]}</Row>}
          <div className="aw-card__links"><Link to={`/experiment?position=${encodeURIComponent(w.position_id)}`}>Open in experiment ledger</Link></div>
        </dl>
      )}
    </article>
  )
}

function WatchingSection({ items, board }: { items: WatchItem[]; board: Data['board'] }) {
  const shown = items.slice(0, 5)
  return (
    <section className="aw-watch" aria-label="Watching, not funded">
      <h3 className="aw-group__title aw-group__title--watch">Watching — not funded <span>{board.data ? items.length : '—'}</span></h3>
      <p className="aw-muted aw-watch__note">Today's upcoming decisions without a position. Not money: excluded from every total above.</p>
      {!board.data && board.error && <p className="aw-note aw-note--warn" role="status">Today's decisions could not be loaded.</p>}
      {!board.data && !board.error && <p className="aw-muted" role="status">Reading today's decisions…</p>}
      {board.data && items.length === 0 && <p className="aw-muted">Nothing upcoming on today's board.</p>}
      {shown.length > 0 && (
        <ul className="aw-watch__list">
          {shown.map((i) => (
            <li key={i.decision_id} style={{ ['--sport' as string]: SPORT_COLORS[i.sport] }}>
              <Link to={gameHref(i.sport, i.game_id)}><i className="cc-dot" aria-hidden="true" />{i.matchup ?? 'Game'}</Link>
              <span>{i.american != null && i.selection ? `${i.selection} ${am(i.american)}` : 'No price yet'} · {etTime(i.starts_at)}</span>
              <span className={`cc-chip${i.tone ? ` cc-chip--${i.tone}` : ''}`}>{i.label}</span>
            </li>
          ))}
        </ul>
      )}
      {items.length > shown.length && <p className="aw-foot"><Link to="/decisions">{items.length - shown.length} more on Daily Decisions →</Link></p>}
    </section>
  )
}
