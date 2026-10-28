/**
 * THE DROP (Home): AISP's strongest call of the day, revealed once a day at noon ET like a card pack.
 * Before noon: a sealed pack and a countdown. After: tap to tear it open; the card rises and flips in with the
 * pick, model vs market, the edge, the price, and a 100-dot field ("in 100 games like this..."). Opened state
 * is remembered per day in this browser. Which game is chosen, and why, is in drop.ts.
 */
import { GAMES_ON } from '../release/flags'
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { Crest } from '../flow/Crest'
import { american, etClock, pct, type PulseGame } from '../pulse/pulseData'
import { teamIdentity } from '../../services/teamIdentity'
import type { SportCode } from '../../services/commandCenter'
import { KIND_LABEL, chooseDrop, dropTime } from './drop'
import './drop.css'

// Owner rule (10-25): prices shown for a pick come from US-regulated books only (offshore books feed consensus).
const US_BOOK = /draftkings|fanduel|betmgm|caesars|william ?hill|betrivers|espn ?bet|fanatics|hard ?rock|bally|betparx|bet365/i

const KEY = (day: string) => `aisp-drop-opened-${day}`
/** The game id the pack revealed today (pinned, so the card never hops to another game as kickoffs pass). */
const readOpened = (day: string): string | null => { try { return localStorage.getItem(KEY(day)) } catch { return null } }
/** Collapsed after opening: remembered per day; a page visit after the reveal starts collapsed. */
const FOLD = (day: string) => `aisp-drop-collapsed-${day}`
const readFold = (day: string): boolean | null => { try { const v = localStorage.getItem(FOLD(day)); return v == null ? null : v === '1' } catch { return null } }
const color = (sport: string, abbr: string) => { const id = teamIdentity(sport as SportCode, abbr); return id.known ? id.primary : '#34d399' }
function countdown(ms: number) { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}` }

export function TheDrop({ games, day }: { games: PulseGame[]; day: string }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t) }, [])
  const opensAt = dropTime(day)
  const [opened, setOpened] = useState<string | null>(() => readOpened(day))
  const [tearing, setTearing] = useState(false)
  // Already opened on an earlier visit: start folded unless the visitor chose otherwise.
  const [folded, setFolded] = useState<boolean>(() => readFold(day) ?? Boolean(readOpened(day)))
  const fold = (v: boolean) => { setFolded(v); try { localStorage.setItem(FOLD(day), v ? '1' : '0') } catch { /* private window */ } }
  // Before opening: the best call among games not yet started. After: the pinned game, whatever its state now.
  const pinned = opened ? games.find((g) => g.id === opened) ?? null : null
  const drop = useMemo(() => (pinned ? chooseDrop([pinned], 0) : chooseDrop(games, Math.max(now, opensAt))), [pinned, games, opensAt, Math.floor(now / 60_000)]) // eslint-disable-line react-hooks/exhaustive-deps
  const sealed = now < opensAt

  const open = () => {
    if (sealed || tearing) return
    setTearing(true)
    const id = drop?.game.id ?? null
    window.setTimeout(() => { setOpened(id); setTearing(false); setFolded(false); try { if (id) localStorage.setItem(KEY(day), id) } catch { /* private window */ } }, 1100)
  }

  return (
    <section className={`dp${opened && folded ? ' is-folded' : ''}`} aria-label="The Drop: AISP's call of the day">
      <header className="dp__head">
        <h2>THE DROP</h2>
        {opened && folded && drop ? <FoldedLine d={drop} /> : <span>{sealed ? `AISP's call of the day drops at 12:00 PM ET` : opened ? `Today's call · dropped 12:00 PM ET` : 'Today’s call is in. Open it.'}</span>}
        {opened && drop ? (
          <button type="button" className="dp__fold" aria-expanded={!folded} onClick={() => fold(!folded)}>{folded ? 'Show card ▾' : 'Collapse ▴'}</button>
        ) : null}
      </header>
      {!drop ? (
        <p className="dp__empty">{games.length ? 'Every game today has started, so today’s drop is spent. Next drop tomorrow at noon.' : 'No games today. Next drop tomorrow at noon.'}</p>
      ) : !opened ? (
        <div className="dp__stage">
          <button type="button" className={`dp-pack${sealed ? ' is-sealed' : ' is-ready'}${tearing ? ' is-tearing' : ''}`} onClick={open} disabled={sealed} aria-label={sealed ? 'Sealed until noon' : 'Open today’s drop'}>
            <span className="dp-pack__half dp-pack__half--top"><b>AISP</b><small>CALL OF THE DAY</small></span>
            <span className="dp-pack__half dp-pack__half--bot"><b>{sealed ? countdown(opensAt - now) : 'TAP TO OPEN'}</b><small>{sealed ? 'until the drop' : `${games.length} games scanned`}</small></span>
            <i className="dp-pack__foil" aria-hidden="true" />
          </button>
        </div>
      ) : folded ? null : (
        <DropCard d={drop} />
      )}
      {opened && folded ? null : <p className="dp__fine">AISP&apos;s paper call, chosen by a fixed rule (its own bet first, then its biggest edge inside the 10-point gap rule, then its model&apos;s surest favourite). Model estimate and sportsbook price, not a wager signal.</p>}
    </section>
  )
}

/** One line for the collapsed bar: the pick and its numbers. */
function FoldedLine({ d }: { d: NonNullable<ReturnType<typeof chooseDrop>> }) {
  const g = d.game
  const team = d.side === 'HOME' ? g.home : g.away
  const opp = d.side === 'HOME' ? g.away : g.home
  return (
    <span className="dp__line">
      <em>{KIND_LABEL[d.kind]}</em>
      <Crest sport={g.sport} abbr={team.abbr} size={22} />
      <b>{team.abbr}</b> over {opp.abbr} · {g.sport} {etClock(g.startsAt)} ET · model <b>{pct(d.model)}</b>
      {d.market != null ? <> vs market <b>{pct(d.market)}</b></> : null}
      {d.edge != null ? <> · edge <b className={d.edge > 0 ? 'is-up' : ''}>{d.edge >= 0 ? '+' : ''}{d.edge.toFixed(1)}</b></> : null}
    </span>
  )
}

function DropCard({ d }: { d: NonNullable<ReturnType<typeof chooseDrop>> }) {
  const g = d.game
  const team = d.side === 'HOME' ? g.home : g.away
  const opp = d.side === 'HOME' ? g.away : g.home
  const model = Math.round(d.model * 100)
  const market = d.market == null ? null : Math.round(d.market * 100)
  return (
    <div className="dp-card" style={{ '--tc': color(g.sport, team.abbr) } as CSSProperties}>
      <div className="dp-card__holo" aria-hidden="true" />
      <div className="dp-card__left">
        <span className="dp-card__kind">{KIND_LABEL[d.kind]}</span>
        <div className="dp-card__team"><Crest sport={g.sport} abbr={team.abbr} size={86} /><span><b>{team.name || team.abbr}</b><small>to beat {opp.name || opp.abbr} · {g.sport} · {etClock(g.startsAt)} ET</small></span></div>
        <div className="dp-card__nums">
          <div><small>MODEL</small><b>{model}%</b></div>
          <div><small>MARKET</small><b>{market == null ? '—' : `${market}%`}</b></div>
          <div><small>EDGE</small><b className={d.edge != null && d.edge > 0 ? 'is-up' : ''}>{d.edge == null ? '—' : `${d.edge >= 0 ? '+' : ''}${d.edge.toFixed(1)}`}</b></div>
          {d.book && US_BOOK.test(d.book) && d.price != null
            ? <div><small>PRICE</small><b>{american(d.price)}</b><em>{d.book}</em></div>
            : <div><small>US PRICE</small><b>—</b><em>{d.book ? 'top price is offshore · US prices in Shop the Line' : 'AISP buys lines at T-75'}</em></div>}
        </div>
        <div className="dp-card__acts">
          {GAMES_ON && <Link to="/arena" className="dp-btn dp-btn--go">Call it in The Arena →</Link>}
          <Link to={`/games/${g.id}`} className="dp-btn">Game page</Link>
        </div>
      </div>
      <figure className="dp-dots" aria-label={`In 100 games like this the model sees ${team.abbr} winning ${model}${market == null ? '' : `; the market prices ${market}`}.`}>
        <div className="dp-dots__grid">
          {Array.from({ length: 100 }, (_, i) => (
            <i key={i} className={`${i < model ? 'is-win' : ''}${market != null && i === market - 1 ? ' is-mkt' : ''}`} style={{ animationDelay: `${0.4 + i * 0.014}s` }} />
          ))}
        </div>
        <figcaption>In <b>100 games like this</b>, the model sees {team.abbr} winning <b>{model}</b>{market != null ? <>; the market prices <b>{market}</b> (ringed dot)</> : null}. {pct(d.model)} is a model estimate.</figcaption>
      </figure>
    </div>
  )
}
