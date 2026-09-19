/**
 * Today's Edge Board: Home's hero. Up to four game cards chosen and ordered by
 * `services/edgeBoard` -- see that module for exactly when a game may be
 * called an edge. This file only presents them.
 *
 * Before prices arrive the same hero shows the model's strongest leans (win
 * split, projected score, collection and decision milestones) or the next
 * games on deck. Edge and expected return are shown only on evaluated cards.
 */
import { ArrowRight } from 'lucide-react'
import { memo, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { TeamLogo } from '../TeamLogo'
import { SPORT_COLORS, SPORT_NAMES, etTime, type SportCode } from '../../services/commandCenter'
import { STATUS_LABEL, type EdgeBoard as Board, type EdgeCard } from '../../services/edgeBoard'
import { teamIdentity } from '../../services/teamIdentity'
import { ProbabilityRail, WinSplit } from './ProbabilityRail'
import { countdown } from './CommandDecisions'

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const pts = (v: number | null | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)} pts`)
const signedPct = (v: number | null | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}%`)
const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')
const clock = (iso: string | null | undefined) => (iso ? etTime(iso).replace(' ET', '') : '—')
const money = (v: number | null | undefined) => (v == null ? '—' : `$${Math.abs(v).toFixed(2)}`)

function timing(card: EdgeCard, now: number): { text: string; tone: '' | 'is-up' | 'is-gold' } {
  const start = card.startsAt ? Date.parse(card.startsAt) : null
  const status = (card.game?.status ?? '').toUpperCase()
  if (status === 'FINAL' || status === 'COMPLETED') return { text: 'Final', tone: '' }
  if (start != null && now >= start) return { text: 'In progress', tone: '' }
  if (!card.window) return { text: '', tone: '' }
  const opens = Date.parse(card.window.opensAt)
  const closes = Date.parse(card.window.closesAt)
  if (now < opens) return { text: `Decision in ${countdown(card.window.opensAt, now)}`, tone: 'is-gold' }
  if (now < closes) return { text: `Window open · closes in ${countdown(card.window.closesAt, now)}`, tone: 'is-up' }
  return { text: card.evaluatedAt ? `Decided ${clock(card.evaluatedAt)}` : 'Window closed', tone: '' }
}

export const EdgeBoard = memo(function EdgeBoard({ board, now, loading, asOf, allHref, dateLabel }: {
  board: Board
  now: number
  loading: boolean
  asOf: React.ReactNode
  allHref: string
  dateLabel: string
}) {
  const scroller = useRef<HTMLDivElement | null>(null)
  const [active, setActive] = useState(0)
  const count = board.cards.length

  useEffect(() => {
    const node = scroller.current
    if (!node) return
    const onScroll = () => {
      const first = node.firstElementChild as HTMLElement | null
      const step = first ? first.getBoundingClientRect().width + 12 : node.clientWidth
      setActive(Math.max(0, Math.min(count - 1, Math.round(node.scrollLeft / Math.max(1, step)))))
    }
    node.addEventListener('scroll', onScroll, { passive: true })
    return () => node.removeEventListener('scroll', onScroll)
  }, [count])

  const goTo = (index: number) => {
    const node = scroller.current
    const child = node?.children[index] as HTMLElement | undefined
    if (node && child) node.scrollTo({ left: child.offsetLeft - node.offsetLeft, behavior: 'smooth' })
  }

  return (
    <section className={`cc-dark cc-edge cc-edge--${board.state}`} aria-labelledby="cc-edge-title">
      <header className="cc-edge__head">
        <div>
          <span className="cc-kicker">{dateLabel} · virtual research</span>
          <h1 id="cc-edge-title">{board.title}</h1>
          <p className="cc-edge__sub">{board.subtitle}</p>
        </div>
        <div className="cc-edge__side">
          {asOf}
          <Link to={allHref} className="cc-edge__all">All decisions <ArrowRight size={13} /></Link>
        </div>
      </header>

      {loading && count === 0
        ? <div className="cc-edge__cards cc-edge__cards--n4" aria-busy="true">{[0, 1, 2, 3].map((i) => <div key={i} className="cc-ecard cc-ecard--skeleton" />)}</div>
        : count === 0
          ? <p className="cc-edge__empty">{board.subtitle}. The next slate appears here as soon as it is registered.</p>
          : (
            <>
              <div className={`cc-edge__cards cc-edge__cards--n${count}`} ref={scroller} role="list" aria-label={board.title}>
                {board.cards.map((card, index) => <GameCard key={card.key} card={card} rank={index + 1} now={now} showReason={board.state === 'CLOSEST'} />)}
              </div>
              {count > 1 && (
                <div className="cc-edge__pager">
                  <span className="cc-edge__count">{active + 1} of {count} · swipe for more</span>
                  <span className="cc-edge__dots">
                    {board.cards.map((card, i) => (
                      <button key={card.key} type="button" aria-label={`Show card ${i + 1}: ${card.matchup}`} aria-current={i === active}
                        className={i === active ? 'is-active' : ''} onClick={() => goTo(i)} />
                    ))}
                  </span>
                </div>
              )}
              {board.available > count && (
                <p className="cc-edge__more"><Link to={allHref}>+{board.available - count} more {board.state === 'BEST_EDGES' || board.state === 'CLOSEST' ? '' : 'games '}on Daily Decisions</Link></p>
              )}
            </>
          )}
    </section>
  )
})

function Team({ sport, abbreviation, selected }: { sport: SportCode; abbreviation: string | null | undefined; selected: boolean }) {
  return (
    <span className={`cc-ecard__team${selected ? ' is-selected' : ''}`}>
      <TeamLogo league={sport === 'CFB' ? 'CFB' : sport} abbreviation={abbreviation} size={34} />
      <b>{abbreviation ?? '—'}</b>
    </span>
  )
}

const GameCard = memo(function GameCard({ card, rank, now, showReason }: { card: EdgeCard; rank: number; now: number; showReason: boolean }) {
  const when = timing(card, now)
  const color = SPORT_COLORS[card.sport]
  const away = card.game?.away?.abbreviation ?? card.matchup.split(' @ ')[0]
  const home = card.game?.home?.abbreviation ?? card.matchup.split(' @ ')[1]
  const nonQualifying = card.status === 'WATCHING' || card.status === 'DECLINED'
  return (
    <article role="listitem" className={`cc-ecard cc-ecard--${card.status}`} style={{ ['--sport' as string]: color }}
      aria-label={`${rank}. ${card.matchup}, ${SPORT_NAMES[card.sport]}, ${STATUS_LABEL[card.status]}`}>
      <header className="cc-ecard__top">
        <span className="cc-ecard__rank">{rank}</span>
        <span className="cc-ecard__sport"><i className="cc-dot" />{card.sport}</span>
        <span className={`cc-ebadge cc-ebadge--${card.status}`}>{STATUS_LABEL[card.status]}</span>
      </header>

      <div className="cc-ecard__teams">
        <Team sport={card.sport} abbreviation={away} selected={card.side === 'AWAY'} />
        <span className="cc-ecard__at">@</span>
        <Team sport={card.sport} abbreviation={home} selected={card.side === 'HOME'} />
      </div>
      <p className="cc-ecard__when"><span>{clock(card.startsAt)} ET</span>{when.text && <span className={when.tone}>{when.text}</span>}</p>

      {card.kind === 'evaluated' && <EvaluatedBody card={card} color={color} showReason={showReason || nonQualifying} />}
      {card.kind === 'lean' && <LeanBody card={card} color={color} now={now} away={away} home={home} />}
      {card.kind === 'deck' && <DeckBody card={card} now={now} />}

      <footer className="cc-ecard__foot">
        <Link to={card.action.href} className="cc-ecard__action">{card.action.label} <ArrowRight size={13} /></Link>
      </footer>
    </article>
  )
})

function EvaluatedBody({ card, color, showReason }: { card: EdgeCard; color: string; showReason: boolean }) {
  const price = card.price
  const declined = card.status === 'DECLINED'
  return (
    <>
      <div className="cc-ecard__pick">
        <span>{declined ? 'Evaluated' : card.status === 'WATCHING' ? 'Model side' : 'Model pick'}</span>
        <b>{card.selectionLabel}</b>
        <strong className="cc-ecard__price">{am(price?.american)}</strong>
      </div>
      {price && (
        <p className="cc-ecard__book">
          {price.basis === 'BEST_CURRENT' ? 'Best current' : `At evaluation · ${clock(price.observedAt)}`} · {shortBook(price.sportsbook)}
          {price.provider ? ` · ${price.provider}` : ''}{price.classification ? ` ${price.classification.toLowerCase()}` : ''}
        </p>
      )}
      <dl className="cc-ecard__nums">
        <div><dt>Model</dt><dd>{pct(card.modelProbability)}</dd></div>
        <div><dt>No-vig</dt><dd>{pct(card.marketProbability)}</dd></div>
        {/* The recorded policy value only; the stored unadjusted break-even figure is never shown under this name. */}
        <div><dt>Recorded policy-adjusted edge</dt>
          {card.recordedPolicyEdge == null
            ? <dd className="is-dim">unavailable</dd>
            : <dd className={!declined && card.recordedPolicyEdge > 0 ? 'is-up' : ''} title={pts(card.recordedPolicyEdge)}>{pts(card.recordedPolicyEdge).replace(' pts', '')}<small> pts</small></dd>}</div>
        <div><dt>{card.expectedValueBasis === 'UNADJUSTED_MODEL' ? 'Unadjusted model return' : 'Recorded policy-adjusted return'}</dt><dd className={!declined && (card.expectedValue ?? 0) > 0 ? 'is-up' : ''}>{signedPct(card.expectedValue)}</dd></div>
      </dl>
      <ProbabilityRail label={`${card.selectionLabel ?? 'Selection'} win probability`} range={card.range} consensus={card.consensus}
        consensusCurrent={card.consensusCurrent} model={card.modelProbability} bookProbability={card.marketProbability}
        bookLabel={`${shortBook(card.price?.sportsbook) || 'Evaluated book'} no-vig`} color={color} />
      {card.position && (
        <p className="cc-ecard__entry">
          Entered {am(card.position.american)} · {shortBook(card.position.sportsbook)} · stake {money(card.position.stake)}
          {card.position.status === 'SETTLED'
            ? <> · <span className={(card.position.profitLoss ?? 0) >= 0 ? 'is-up' : 'is-down'}>{card.position.result} {(card.position.profitLoss ?? 0) >= 0 ? '+' : '−'}{money(card.position.profitLoss)}</span></>
            : ' · open'}
        </p>
      )}
      {card.preFinal && !card.position && <p className="cc-ecard__note">Early read · final evaluation in the decision window</p>}
      {showReason && !card.position && card.reason && <p className="cc-ecard__reason">{card.reason}</p>}
    </>
  )
}

function LeanBody({ card, color, now, away, home }: { card: EdgeCard; color: string; now: number; away: string; home: string }) {
  const homeP = card.side === 'HOME' ? card.modelProbability! : 1 - (card.modelProbability ?? 0.5)
  const awayColor = teamIdentity(card.sport, away).primary
  const homeColor = teamIdentity(card.sport, home).primary
  return (
    <>
      <div className="cc-ecard__pick">
        <span>Model lean</span>
        <b>{card.selectionLabel}</b>
        <strong className="cc-ecard__price cc-ecard__price--prob">{pct(card.modelProbability, 0)}</strong>
      </div>
      <WinSplit awayLabel={away} homeLabel={home} homeProbability={homeP} awayColor={awayColor} homeColor={homeColor}
        projected={card.projected} emphasize={card.side ?? 'HOME'} />
      {card.range && (
        <ProbabilityRail label={`${card.selectionLabel ?? 'Selection'} win probability`} range={card.range} consensus={card.consensus}
          consensusCurrent={card.consensusCurrent} model={card.modelProbability} color={color} height={40} />
      )}
      <Milestones card={card} now={now} />
    </>
  )
}

function DeckBody({ card, now }: { card: EdgeCard; now: number }) {
  return <Milestones card={card} now={now} forecast />
}

function Milestones({ card, now, forecast = false }: { card: EdgeCard; now: number; forecast?: boolean }) {
  const collection = card.collectionOpensAt
  const priceCurrent = card.status === 'EVALUATION_PENDING'
  const collected = card.game?.price_state === 'CURRENT_PRICE'
  return (
    <ul className="cc-ecard__miles">
      {forecast && card.startsAt && (
        <li><span>Forecast</span><b className="is-gold">expected by {clock(new Date(Date.parse(card.startsAt) - 60 * 60_000).toISOString())}</b></li>
      )}
      {priceCurrent && card.price?.american != null
        ? <li><span>Market</span><b className="is-up">{am(card.price.american)} {shortBook(card.price.sportsbook)} · current</b></li>
        : collected
          ? <li><span>Market</span><b className="is-up">collected {clock(card.game?.freshest_observed_at)} · {card.game?.fresh_books} books</b></li>
          : collection && (
          <li><span>Market collection</span>
            <b className="is-gold">{clock(collection)} · {Date.parse(collection) <= now ? 'open' : `in ${countdown(collection, now)}`}</b></li>
        )}
      {card.price?.basis === 'REFERENCE' && <li className="cc-ecard__ref"><span>Stored reference</span><b>{clock(card.price.observedAt)} · not current</b></li>}
      {card.window && <li><span>Decision window</span><b>{clock(card.window.opensAt)}–{clock(card.window.closesAt)}</b></li>}
    </ul>
  )
}
