/**
 * Markets & Crowd: the market command center.
 *
 *   * A dark Market Pulse: games with current prices, sportsbooks represented,
 *     freshest quote, the largest model-market disagreement, next collection,
 *     and whether the view is CURRENT or the LATEST AVAILABLE snapshot.
 *   * When today has no stored price, the page shows the most recent legitimate
 *     stored market evidence instead of an empty table, with its real date and
 *     observation time stated up front and today's collection countdown kept
 *     visible. Old evidence is never relabelled as current.
 *   * An interactive model-vs-consensus plot, one row per game, with the
 *     individual-book range and the largest disagreements highlighted.
 *   * A by-book comparison for the selected game, with any recorded position
 *     price shown separately from the market.
 *   * Uncollected games grouped into a compact "Scheduled for collection" list.
 *
 * Nothing here fabricates crowd percentages, intervals or closing lines.
 */
import * as d3 from 'd3'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  SPORT_CODES, SPORT_COLORS, SPORT_NAMES, asSport, etTime, gameHref, money, relative, signedMoney,
  useLedger, useMarketCoverage, type CoverageGame, type SportCode,
} from '../../services/commandCenter'
import { BookSpread } from './BookSpread'
import { ChartTable, Tooltip, activate, useTooltip, useWidth } from './chartKit'
import { AsOf } from './RegionState'
import { todayEt } from './CommandHome'
import { countdown } from './CommandDecisions'
import './command.css'

type Market = 'MONEYLINE' | 'SPREAD' | 'TOTAL'
const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')

function bookRange(game: CoverageGame): [number, number] | null {
  const values = game.quotes.filter((q) => q.market === 'MONEYLINE' && q.no_vig_home != null).map((q) => q.no_vig_home as number)
  return values.length ? [d3.min(values)!, d3.max(values)!] : null
}
const gapOf = (g: CoverageGame) => (g.model?.home_two_way != null && g.consensus_no_vig_home != null
  ? g.model.home_two_way - g.consensus_no_vig_home : null)

export function CommandMarkets({ day, onDay }: { day: string; onDay: (next: string) => void }) {
  const today = useMarketCoverage(day)
  const summary = today.data?.summary
  const todayHasQuotes = (today.data?.games ?? []).some((g) => g.quotes.length > 0)
  const snapshotDay = summary?.latest_snapshot?.date ?? null
  const useSnapshot = Boolean(today.data) && !todayHasQuotes && Boolean(snapshotDay) && snapshotDay !== day
  const snapshot = useMarketCoverage(useSnapshot ? (snapshotDay as string) : day)
  const evidence = useSnapshot ? snapshot : today
  const ledger = useLedger()
  const [sport, setSport] = useState<SportCode | 'ALL'>('ALL')
  const [market, setMarket] = useState<Market>('MONEYLINE')
  const [selected, setSelected] = useState<string | null>(null)
  const [showAllScheduled, setShowAllScheduled] = useState(false)
  const now = Date.now()

  const evidenceGames = useMemo(() => (evidence.data?.games ?? []).filter((g) => g.quotes.length > 0 || g.model), [evidence.data])
  const games = useMemo(() => evidenceGames.filter((g) => sport === 'ALL' || asSport(g.league) === sport), [evidenceGames, sport])
  const withQuotes = games.filter((g) => g.quotes.length > 0)
  const chosen = games.find((g) => g.game_id === selected)
    ?? [...withQuotes].sort((a, b) => Math.abs(gapOf(b) ?? 0) - Math.abs(gapOf(a) ?? 0))[0] ?? null
  const largest = [...withQuotes].filter((g) => gapOf(g) != null).sort((a, b) => Math.abs(gapOf(b)!) - Math.abs(gapOf(a)!))[0] ?? null
  const current = (today.data?.games ?? []).filter((g) => g.price_state === 'CURRENT_PRICE').length
  const scheduled = (today.data?.games ?? [])
    .filter((g) => g.price_state === 'NOT_COLLECTED' && g.starts_at && Date.parse(g.starts_at) > now)
    .sort((a, b) => a.checkpoints.collection_opens_at.localeCompare(b.checkpoints.collection_opens_at))
  const evSummary = evidence.data?.summary
  const positions = useMemo(() => (ledger.data?.sports ?? []).flatMap((s) => s.positions.map((p) => ({ ...p, sport: s.sport }))), [ledger.data])
  const recorded = chosen ? positions.filter((p) => p.game_id === chosen.game_id) : []
  const sports = SPORT_CODES.filter((c) => evidenceGames.some((g) => asSport(g.league) === c))

  return (
    <section className="cc-markets" aria-label="Markets and crowd">
      <section className="cc-dark cc-mpulse" aria-label="Market pulse">
        <header className="cc-mpulse__head">
          <div>
            <span className="cc-kicker">Market pulse · sportsbook evidence</span>
            <h1>{useSnapshot ? 'Latest available market snapshot' : 'Today’s market'}</h1>
            <span className={`cc-snap ${useSnapshot ? 'cc-snap--old' : todayHasQuotes ? 'cc-snap--live' : ''}`}>
              {useSnapshot
                ? `Snapshot from ${new Date(`${snapshotDay}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })} · last quote ${etTime(summary?.latest_snapshot?.observed_at, true)} · not current`
                : todayHasQuotes ? `Stored quotes for ${new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : 'No stored market evidence yet'}
            </span>
          </div>
          <div className="cc-mpulse__day">
            <button type="button" onClick={() => onDay(shiftDay(day, -1))} aria-label="Previous day">‹</button>
            <span>{new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}</span>
            <button type="button" onClick={() => onDay(shiftDay(day, 1))} aria-label="Next day">›</button>
          </div>
        </header>
        <dl className="cc-mpulse__stats">
          <div><dt>Games with a current price</dt><dd className={current ? 'is-up' : ''}>{today.data ? `${current} / ${today.data.games.length}` : '—'}</dd><small>{day === todayEt() ? 'today' : day}</small></div>
          <div><dt>Sportsbooks</dt><dd>{evSummary ? evSummary.sportsbooks.length : '—'}</dd><small>{evSummary?.providers.join(' · ') || 'none stored'}</small></div>
          <div><dt>Freshest quote</dt><dd>{evSummary?.freshest_observed_at ? relative(evSummary.freshest_observed_at) : '—'}</dd><small>{evSummary?.freshest_observed_at ? etTime(evSummary.freshest_observed_at, true) : ''}</small></div>
          <div><dt>Largest disagreement</dt>
            <dd className={largest ? ((gapOf(largest) ?? 0) >= 0 ? 'is-up' : 'is-down') : ''}>{largest ? `${(gapOf(largest)! * 100 >= 0 ? '+' : '')}${(gapOf(largest)! * 100).toFixed(1)} pts` : '—'}</dd>
            <small>{largest ? `${largest.matchup}${largest.consensus_is_current ? '' : ' · stale'}` : 'needs model + price'}</small></div>
          <div><dt>Next collection</dt><dd className="is-gold">{summary?.next_collection_at ? etTime(summary.next_collection_at).replace(' ET', '') : '—'}</dd><small>{summary?.next_collection_at ? `in ${countdown(summary.next_collection_at, now)}` : 'none pending'}</small></div>
        </dl>
        <div className="cc-mpulse__asof"><AsOf resource={evidence} label="quotes" /></div>
      </section>

      {useSnapshot && (
        <p className="cc-banner" role="status">
          <b>Today’s market collection has not opened.</b> Showing the latest available market snapshot from{' '}
          {etTime(summary?.latest_snapshot?.observed_at, true)}.
          {summary?.next_collection_at ? ` Today’s collection opens ${etTime(summary.next_collection_at)} (in ${countdown(summary.next_collection_at, now)}).` : ''}
        </p>
      )}

      <div className="cc-feedbar">
        <div className="cc-tabs" role="group" aria-label="Market">
          {(['MONEYLINE', 'SPREAD', 'TOTAL'] as const).map((m) => (
            <button key={m} type="button" aria-selected={market === m} onClick={() => setMarket(m)}>
              {m === 'MONEYLINE' ? 'Moneyline' : m === 'SPREAD' ? 'Handicap' : 'Total'}
            </button>
          ))}
        </div>
        {sports.length > 1 && (
          <div className="cc-filters" role="group" aria-label="Sport" style={{ margin: 0 }}>
            {(['ALL', ...sports] as const).map((code) => (
              <button key={code} type="button" aria-pressed={sport === code} onClick={() => setSport(code)}>
                {code === 'ALL' ? 'All sports' : SPORT_NAMES[code]}
              </button>
            ))}
          </div>
        )}
      </div>

      {!evidence.data
        ? <p className="cc-empty" role="status">{evidence.error ? `Market evidence is unavailable: ${evidence.error}` : 'Loading stored quotes…'}</p>
        : withQuotes.length === 0
          ? <p className="cc-empty">No stored sportsbook quote exists for {SPORT_NAMES[sport as SportCode] ?? 'any sport'} in AISP yet.</p>
          : (
            <div className="cc-mgrid">
              <section className="cc-mpanel" aria-label="Model versus consensus">
                <h3>Model vs no-vig consensus · {withQuotes.length} priced games</h3>
                <ConsensusBoard games={games} selected={chosen?.game_id ?? null} onSelect={setSelected} />
              </section>
              <section className="cc-mpanel" aria-label="By book">
                <h3>{chosen ? <>By book · <Link to={gameHref(asSport(chosen.league) ?? 'MLB', chosen.game_id)}>{chosen.matchup}</Link></> : 'By book'}</h3>
                {chosen && <BookSpread game={chosen} market={market} />}
                {recorded.length > 0 && (
                  <div className="cc-recorded">
                    <b>Recorded position{recorded.length > 1 ? 's' : ''}</b> <span className="cc-dim">entry terms, not a market price</span>
                    {recorded.map((p) => (
                      <p key={p.position_id}>
                        {p.selection} <b>{am(p.entry.american)}</b> · {shortBook(p.entry.sportsbook)} ({p.entry.classification.toLowerCase()}) · stake {money(p.stake)}
                        {p.status === 'SETTLED' ? <> · <span className={(p.profit_loss ?? 0) >= 0 ? 'is-up' : 'is-down'}>{p.result} {signedMoney(p.profit_loss)}</span></> : ' · open'}
                      </p>
                    ))}
                  </div>
                )}
              </section>
            </div>
          )}

      {scheduled.length > 0 && (
        <section className="cc-mpanel" aria-label="Scheduled for collection">
          <h3>Scheduled for collection · {scheduled.length} games today</h3>
          <ul className="cc-sched">
            {(showAllScheduled ? scheduled : scheduled.slice(0, 4)).map((g) => {
              const code = asSport(g.league) ?? 'MLB'
              return (
                <li key={g.game_id} style={{ ['--sport' as string]: SPORT_COLORS[code] }}>
                  <Link to={gameHref(code, g.game_id)}><i className="cc-dot" /><b>{g.matchup}</b></Link>
                  <span>{etTime(g.starts_at).replace(' ET', '')} start</span>
                  <span className="is-gold">collect in {countdown(g.checkpoints.collection_opens_at, now)}</span>
                </li>
              )
            })}
          </ul>
          {scheduled.length > 4 && (
            <button type="button" className="cc-more" onClick={() => setShowAllScheduled((v) => !v)}>
              {showAllScheduled ? 'Show fewer' : `Show all ${scheduled.length}`}
            </button>
          )}
        </section>
      )}

      <p className="cc-note">Prediction-market (crowd) evidence is shown only where it was captured; see the detailed boards below. Model output is never shown as a price.</p>
    </section>
  )
}

function shiftDay(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

/** One row per game: book range, consensus tick, model dot, gap stem; largest gaps highlighted. */
const ConsensusBoard = memo(function ConsensusBoard({ games, selected, onSelect }: { games: CoverageGame[]; selected: string | null; onSelect: (id: string) => void }) {
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const axis = useRef<SVGGElement | null>(null)
  const rows = useMemo(() => games.filter((g) => g.consensus_no_vig_home != null)
    .sort((a, b) => Math.abs(gapOf(b) ?? -1) - Math.abs(gapOf(a) ?? -1)), [games])
  const top = new Set(rows.filter((g) => gapOf(g) != null).slice(0, 3).map((g) => g.game_id))
  const rowH = 26
  const m = { top: 26, right: 16, bottom: 6, left: width < 560 ? 86 : 108 }
  const height = m.top + Math.max(1, rows.length) * rowH + m.bottom
  const values = rows.flatMap((g) => [g.consensus_no_vig_home, g.model?.home_two_way, ...(bookRange(g) ?? [])]).filter((v): v is number => v != null)
  const lo = Math.max(0, (d3.min(values) ?? 0.3) - 0.05)
  const hi = Math.min(1, (d3.max(values) ?? 0.7) + 0.05)
  const x = d3.scaleLinear().domain([lo, hi]).range([m.left, width - m.right])

  useEffect(() => {
    if (axis.current) d3.select(axis.current).call(d3.axisTop(x).ticks(width < 560 ? 4 : 7).tickFormat((v) => d3.format('.0%')(v as number)) as never)
  }, [x, width])

  if (rows.length === 0) return <p className="cc-muted">No game here has a two-sided moneyline to compare.</p>
  const tipFor = (g: CoverageGame) => {
    const range = bookRange(g)
    const best = g.quotes.filter((q) => q.market === 'MONEYLINE').sort((a, b) => b.observed_at.localeCompare(a.observed_at))[0]
    return (
      <>
        <b>{g.matchup}</b> · {g.league}
        <dl>
          <dt>Model {g.home?.abbreviation}</dt><dd>{g.model ? pct(g.model.home_two_way) : 'not published'}</dd>
          <dt>Consensus</dt><dd>{pct(g.consensus_no_vig_home)}{g.consensus_is_current ? '' : ' · stale'}</dd>
          <dt>Gap</dt><dd>{gapOf(g) != null ? `${(gapOf(g)! * 100).toFixed(1)} pts` : '—'}</dd>
          <dt>Book range</dt><dd>{range ? `${pct(range[0])}–${pct(range[1])}` : '—'} ({g.books} books)</dd>
          {best && <><dt>Latest</dt><dd>{shortBook(best.sportsbook)} {am(best.sides.AWAY?.american)}/{am(best.sides.HOME?.american)}</dd>
            <dt>Provider</dt><dd>{best.provider} · {best.classification.toLowerCase()}</dd>
            <dt>Observed</dt><dd>{etTime(best.observed_at, true)} ({relative(best.observed_at)})</dd></>}
        </dl>
      </>
    )
  }
  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img" aria-label={`Model and consensus for ${rows.length} games`}>
        <g ref={axis} className="cc-axis" transform={`translate(0, ${m.top - 6})`} />
        {rows.map((g, i) => {
          const cy = m.top + i * rowH + rowH / 2
          const range = bookRange(g)
          const code = asSport(g.league) ?? 'MLB'
          const gap = gapOf(g)
          const hot = top.has(g.game_id)
          return (
            <g key={g.game_id} className={`cc-lane${g.game_id === selected ? ' is-selected' : ''}`} tabIndex={0} role="button"
              aria-label={`${g.matchup}: model ${pct(g.model?.home_two_way)}, consensus ${pct(g.consensus_no_vig_home)}`}
              onMouseMove={(e) => show(e, tipFor(g))} onMouseLeave={hide} onFocus={(e) => show(e, tipFor(g))} onBlur={hide}
              {...activate(() => onSelect(g.game_id))}>
              <rect x={0} y={cy - rowH / 2} width={width} height={rowH} className="cc-lane__hit" />
              <text x={m.left - 8} y={cy + 4} textAnchor="end" className={`cc-lane__label${hot ? ' cc-hot' : ''}`}>{g.matchup}</text>
              {range && <rect x={x(range[0])} y={cy - 5} width={Math.max(3, x(range[1]) - x(range[0]))} height={10} rx={5} className="cc-range" />}
              {gap != null && <line x1={x(g.consensus_no_vig_home!)} x2={x(g.model!.home_two_way!)} y1={cy} y2={cy}
                className={gap >= 0 ? 'cc-stem cc-stem--up' : 'cc-stem cc-stem--down'} strokeWidth={hot ? 3 : 1.5} />}
              <line x1={x(g.consensus_no_vig_home!)} x2={x(g.consensus_no_vig_home!)} y1={cy - 8} y2={cy + 8} className="cc-consensus" />
              {g.model?.home_two_way != null && <circle cx={x(g.model.home_two_way)} cy={cy} r={hot ? 7 : 5.5} fill={SPORT_COLORS[code]} className="cc-dot__mark" />}
            </g>
          )
        })}
      </svg>
      <Tooltip tip={tip} width={width} />
      <ul className="cc-legend">
        <li><i className="cc-key cc-key--book" style={{ borderRadius: 4, width: 16 }} />Book range</li>
        <li><i className="cc-key cc-key--consensus" />No-vig consensus</li>
        <li><i className="cc-key cc-key--forecast" />Model (home, two-way)</li>
        <li>Largest gaps in bold · select a row for its books</li>
      </ul>
      <ChartTable caption="Model and consensus by game">
        <thead><tr><th>Game</th><th>Model</th><th>Consensus</th><th>Gap</th><th>Range</th></tr></thead>
        <tbody>{rows.map((g) => { const r = bookRange(g); return <tr key={g.game_id}><td>{g.matchup}</td><td>{pct(g.model?.home_two_way)}</td><td>{pct(g.consensus_no_vig_home)}</td><td>{gapOf(g) != null ? (gapOf(g)! * 100).toFixed(1) : '—'}</td><td>{r ? `${pct(r[0])}–${pct(r[1])}` : '—'}</td></tr> })}</tbody>
      </ChartTable>
    </div>
  )
})
