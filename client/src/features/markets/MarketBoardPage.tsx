/**
 * Markets & Crowd, rebuilt as one board.
 *
 * Every game is a row: score or start time, model, sportsbooks (no-vig),
 * prediction-market crowd, the gap, and the best price on each side. The
 * selected game opens a detail panel: the three opinions on one axis, line
 * shopping across books, and how the line moved. All reads are stored rows.
 */
import { useCallback, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, ChevronLeft, ChevronRight, Dices } from 'lucide-react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import { expectSchema, gameHref, type CoverageGame, type CoverageQuote, type MarketCoverage } from '../../services/commandCenter'
import { operationalToday, resolveOperationalDate, shiftDate } from '../../services/operationalDate'
import { TeamLogo } from '../../components/TeamLogo'
import { ScoreInline, useScores } from '../shared/ScoreBug'
import './markets.css'

const FRESH_H = 36
const pct = (p: number | null | undefined, d = 0) => (p == null ? '—' : `${(p * 100).toFixed(d)}%`)
const odds = (n: number | null | undefined) => (n == null ? '—' : n > 0 ? `+${n}` : `${n}`)
const implied = (a: number) => (a > 0 ? 100 / (a + 100) : -a / (-a + 100))
const ageH = (iso: string | null | undefined) => (iso ? (Date.now() - Date.parse(iso)) / 3600_000 : Infinity)
const ageText = (iso: string | null | undefined) => {
  const h = ageH(iso)
  if (!Number.isFinite(h)) return 'no price'
  if (h < 1) return `${Math.round(h * 60)} min ago`
  if (h < 48) return `${Math.round(h)} h ago`
  return `${Math.round(h / 24)} days ago`
}
const etTime = (iso: string | null) => (iso ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).format(new Date(iso)) : '—')
const dayTitle = (day: string) => new Intl.DateTimeFormat('en-US', { weekday: 'long', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${day}T12:00:00Z`))

interface CrowdRow { game_id: string; polymarket_home_probability: number | null; kalshi_home_probability: number | null; semantics_status?: string }

function useBoard(day: string) {
  const loader = useCallback(async (signal: AbortSignal) => {
    const [cov, crowd] = await Promise.all([
      apiRequest<MarketCoverage>(`/command-center/markets?date=${day}`, {}, signal).then((c) => expectSchema(c, 'AISP_COMMAND_CENTER_MARKET_COVERAGE_V1')),
      apiRequest<{ rows?: CrowdRow[] }>(`/prediction-markets/board?date=${day}`, {}, signal).catch(() => ({ rows: [] as CrowdRow[] })),
    ])
    const crowdBy: Record<string, CrowdRow> = {}
    for (const r of crowd.rows ?? []) crowdBy[r.game_id] = r
    return { cov, crowdBy }
  }, [day])
  return useApiResource(loader, { key: cacheKey('market-board', { day }), staleTimeMs: STALE_TIME.slate, refreshIntervalMs: 120_000 })
}

interface Row {
  g: CoverageGame
  away: string; home: string; sport: string
  model: number | null
  vegas: number | null
  vegasFresh: boolean
  crowd: number | null
  gap: number | null
}

export function MarketBoardPage() {
  const [params, setParams] = useSearchParams()
  const day = resolveOperationalDate(params.get('date'))
  const today = operationalToday()
  const board = useBoard(day)
  const [sport, setSport] = useState<string>('ALL')
  const [sort, setSort] = useState<'time' | 'gap'>('time')
  const selected = params.get('g')
  const setDay = (d: string) => setParams((p) => { const n = new URLSearchParams(p); n.set('date', d); n.delete('g'); return n })
  const select = (id: string) => {
    setParams((p) => { const n = new URLSearchParams(p); n.set('g', id); return n }, { replace: true })
    // Bring the detail into view only when it is off screen.
    window.setTimeout(() => {
      const el = document.getElementById('mkt-detail')
      if (el && el.getBoundingClientRect().top > window.innerHeight - 120) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 50)
  }

  const rows: Row[] = useMemo(() => (board.data?.cov.games ?? []).map((g) => {
    const away = g.away?.abbreviation ?? (g.matchup ?? '').split('@')[0]?.trim() ?? '—'
    const home = g.home?.abbreviation ?? (g.matchup ?? '').split('@')[1]?.trim() ?? '—'
    const vegasFresh = g.consensus_is_current || ageH(g.freshest_observed_at) < FRESH_H
    const crowdRow = board.data?.crowdBy[g.game_id]
    const crowd = crowdRow?.polymarket_home_probability ?? crowdRow?.kalshi_home_probability ?? null
    const model = g.model?.home_two_way ?? null
    const vegas = g.consensus_no_vig_home
    return { g, away, home, sport: g.league, model, vegas, vegasFresh, crowd, gap: model != null && vegas != null && vegasFresh ? model - vegas : null }
  }), [board.data])
  const sports = [...new Set(rows.map((r) => r.sport))]
  const shown = rows.filter((r) => sport === 'ALL' || r.sport === sport)
    .sort((a, b) => sort === 'gap' ? Math.abs(b.gap ?? -1) - Math.abs(a.gap ?? -1) : Date.parse(a.g.starts_at ?? '') - Date.parse(b.g.starts_at ?? ''))
  const scores = useScores(rows.map((r) => r.g.game_id))
  const active = rows.find((r) => r.g.game_id === selected) ?? shown[0] ?? null
  const summary = board.data?.cov.summary
  const fresh = rows.filter((r) => r.vegasFresh).length

  return (
    <div className="mkt">
      <header className="mkt__head">
        <div>
          <p className="mkt-eyebrow">Markets &amp; Crowd</p>
          <h1>{dayTitle(day)}</h1>
          <p className="mkt__sub">
            {rows.length} game{rows.length === 1 ? '' : 's'} · {fresh} with a fresh sportsbook price · {summary?.sportsbooks.length ?? 0} books
            {summary?.freshest_observed_at && <> · newest quote {ageText(summary.freshest_observed_at)}</>}
          </p>
        </div>
        <div className="mkt__nav">
          <button aria-label="Previous day" onClick={() => setDay(shiftDate(day, -1))}><ChevronLeft size={16} /></button>
          <button className={day === today ? 'is-on' : ''} onClick={() => setDay(today)}>Today</button>
          <button aria-label="Next day" onClick={() => setDay(shiftDate(day, 1))}><ChevronRight size={16} /></button>
        </div>
      </header>

      <div className="mkt__layout">
        <section className="mkt-board" aria-label="Game markets">
          <div className="mkt-board__bar">
            <div className="mkt-chips">
              {['ALL', ...sports].map((s) => <button key={s} aria-pressed={sport === s} onClick={() => setSport(s)}>{s === 'ALL' ? 'All' : s}</button>)}
            </div>
            <div className="mkt-chips mkt-chips--quiet">
              <button aria-pressed={sort === 'time'} onClick={() => setSort('time')}>By time</button>
              <button aria-pressed={sort === 'gap'} onClick={() => setSort('gap')}>Biggest gap</button>
            </div>
          </div>
          <div className="mkt-table" role="table">
            <div className="mkt-tr mkt-tr--head" role="row">
              <span role="columnheader">Game</span>
              <span role="columnheader">Model</span>
              <span role="columnheader">Vegas</span>
              <span role="columnheader" title="Prediction markets (Polymarket / Kalshi)">Crowd</span>
              <span role="columnheader">Gap</span>
              <span role="columnheader">Best price</span>
            </div>
            {board.loading && Array.from({ length: 4 }, (_, i) => <div key={i} className="mkt-tr mkt-skel" />)}
            {!board.loading && shown.length === 0 && <p className="mkt-empty">No games on this date.</p>}
            {shown.map((r) => {
              const s = scores[r.g.game_id]
              const homeFav = (r.model ?? r.vegas ?? 0.5) >= 0.5
              return (
                <button key={r.g.game_id} role="row" className={`mkt-tr ${active?.g.game_id === r.g.game_id ? 'is-on' : ''}`} onClick={() => select(r.g.game_id)}>
                  <span className="mkt-game" role="cell">
                    <span className="mkt-game__teams">
                      <TeamLogo league={r.sport} abbreviation={r.away} size={22} /><b className={!homeFav ? 'is-fav' : ''}>{r.away}</b>
                      <em>@</em>
                      <TeamLogo league={r.sport} abbreviation={r.home} size={22} /><b className={homeFav ? 'is-fav' : ''}>{r.home}</b>
                    </span>
                    <ScoreInline state={s} away={r.away} home={r.home} startsAt={r.g.starts_at} />
                  </span>
                  <span role="cell" className="mkt-num mkt-num--model">{pct(r.model)}<small>{r.model != null ? r.home : ''}</small></span>
                  <span role="cell" className={`mkt-num ${r.vegasFresh ? '' : 'is-stale'}`} title={r.vegasFresh ? '' : `Last price ${ageText(r.g.freshest_observed_at)}`}>{pct(r.vegas)}<small>{r.vegas != null ? (r.vegasFresh ? r.home : ageText(r.g.freshest_observed_at)) : ''}</small></span>
                  <span role="cell" className="mkt-num mkt-num--crowd">{pct(r.crowd)}<small>{r.crowd != null ? r.home : ''}</small></span>
                  <span role="cell">{r.gap != null ? <span className={`gap ${Math.abs(r.gap) >= 0.06 ? 'gap--big' : ''}`}>{r.gap > 0 ? '+' : ''}{(r.gap * 100).toFixed(0)}</span> : <span className="gap gap--none">—</span>}</span>
                  <BestCell g={r.g} away={r.away} home={r.home} />
                </button>
              )
            })}
          </div>
          <p className="mkt-foot">Model, Vegas and Crowd are each the chance the <b>home</b> team wins. Vegas is the no-vig consensus of the books; a gray number is an old price that is not compared. Gap = model minus Vegas, in percentage points, only against a fresh price.</p>
        </section>

        {active && <Detail key={active.g.game_id} row={active} />}
      </div>
    </div>
  )
}

const decOf = (x: number | null | undefined) => (x == null ? 0 : x > 0 ? 1 + x / 100 : 1 + 100 / -x)

/** Best moneyline per side: the server's current best, else the best stored quote (grayed). */
function BestCell({ g, away, home }: { g: CoverageGame; away: string; home: string }) {
  const ml = g.quotes.filter((q) => q.market === 'MONEYLINE')
  const stored = (side: 'AWAY' | 'HOME') => {
    let best: CoverageQuote | null = null
    for (const q of ml) if (q.sides[side]?.american != null && (!best || decOf(q.sides[side]!.american) > decOf(best.sides[side]!.american))) best = q
    return best ? { american: best.sides[side]!.american, book: best.sportsbook } : null
  }
  const a = g.best?.AWAY ? { american: g.best.AWAY.american, book: g.best.AWAY.sportsbook, current: true } : { ...stored('AWAY'), current: false }
  const h = g.best?.HOME ? { american: g.best.HOME.american, book: g.best.HOME.sportsbook, current: true } : { ...stored('HOME'), current: false }
  return (
    <span role="cell" className="mkt-best">
      <span className={a.current ? '' : 'is-stale'} title={a.book ?? ''}>{away} <b>{odds(a.american)}</b> <small>{a.book ?? ''}</small></span>
      <span className={h.current ? '' : 'is-stale'} title={h.book ?? ''}>{home} <b>{odds(h.american)}</b> <small>{h.book ?? ''}</small></span>
    </span>
  )
}

// ---------------------------------------------------------------- detail --

function Detail({ row }: { row: Row }) {
  const g = row.g
  const [market, setMarket] = useState<'MONEYLINE' | 'SPREAD' | 'TOTAL'>('MONEYLINE')
  const quotes = g.quotes.filter((q) => q.market === market)
  return (
    <section className="mkt-detail" aria-label="Selected game" id="mkt-detail">
      <div className="mkt-detail__head">
        <span className="mkt-detail__teams">
          <TeamLogo league={row.sport} abbreviation={row.away} size={34} /> {row.away} <em>@</em> {row.home} <TeamLogo league={row.sport} abbreviation={row.home} size={34} />
          <span className="mkt-detail__when">{row.sport} · {etTime(g.starts_at)} ET</span>
        </span>
        <div className="mkt-detail__links">
          <Link to={gameHref(row.sport, g.game_id)}>Game brief <ArrowRight size={14} /></Link>
          <Link to={`/games/${g.game_id}/simulation?sport=${row.sport}`}><Dices size={14} /> Simulate it</Link>
        </div>
      </div>
      <div className="mkt-detail__grid">
        <div className="mkt-detail__col">
          <h3>Three opinions on {row.home}</h3>
          <Opinions row={row} />
          <h3>How the line moved</h3>
          <Movement gameId={g.game_id} home={row.home} model={row.model} />
        </div>
        <div className="mkt-detail__col">
          <div className="mkt-detail__tabs">
            <h3>Line shopping</h3>
            <div className="mkt-chips mkt-chips--quiet">
              {(['MONEYLINE', 'SPREAD', 'TOTAL'] as const).map((m) => <button key={m} aria-pressed={market === m} onClick={() => setMarket(m)}>{m === 'MONEYLINE' ? 'Moneyline' : m === 'SPREAD' ? 'Spread' : 'Total'}</button>)}
            </div>
          </div>
          <Shop quotes={quotes} market={market} away={row.away} home={row.home} />
        </div>
      </div>
    </section>
  )
}

function Opinions({ row }: { row: Row }) {
  const items = [
    { key: 'Model', p: row.model, cls: 'op--model', note: row.model == null ? 'no forecast yet' : 'our forecast' },
    { key: 'Vegas', p: row.vegas, cls: `op--vegas ${row.vegasFresh ? '' : 'is-stale'}`, note: row.vegas == null ? 'no price' : row.vegasFresh ? `${row.g.books} books, no-vig` : `old price · ${ageText(row.g.freshest_observed_at)}` },
    { key: 'Crowd', p: row.crowd, cls: 'op--crowd', note: row.crowd == null ? 'no prediction market' : 'prediction market' },
  ]
  return (
    <div className="ops">
      <div className="ops__axis">
        <span className="ops__half" />
        {items.filter((i) => i.p != null).map((i) => (
          <span key={i.key} className={`ops__dot ${i.cls}`} style={{ left: pct(i.p, 2) }} title={`${i.key} ${pct(i.p, 1)}`}><b>{i.key[0]}</b></span>
        ))}
        <span className="ops__lab ops__lab--l">{row.away} favored</span>
        <span className="ops__lab ops__lab--r">{row.home} favored</span>
      </div>
      <dl className="ops__list">
        {items.map((i) => (
          <div key={i.key} className={i.cls}><dt><i />{i.key}</dt><dd>{pct(i.p, 1)}</dd><small>{i.note}</small></div>
        ))}
      </dl>
    </div>
  )
}

function Shop({ quotes, market, away, home }: { quotes: CoverageQuote[]; market: string; away: string; home: string }) {
  if (!quotes.length) return <p className="mkt-empty">No {market.toLowerCase()} prices stored for this game.</p>
  const [a, b] = market === 'TOTAL' ? ['OVER', 'UNDER'] : ['AWAY', 'HOME']
  const labA = market === 'TOTAL' ? 'Over' : away, labB = market === 'TOTAL' ? 'Under' : home
  // Best = highest payout: compare by decimal odds.
  const dec = (x: number | null | undefined) => (x == null ? 0 : x > 0 ? 1 + x / 100 : 1 + 100 / -x)
  const bestA = Math.max(...quotes.map((q) => dec(q.sides[a]?.american)))
  const bestB = Math.max(...quotes.map((q) => dec(q.sides[b]?.american)))
  const sorted = [...quotes].sort((x, y) => dec(y.sides[b]?.american) - dec(x.sides[b]?.american))
  return (
    <div className="shop" role="table">
      <div className="shop__tr shop__tr--head" role="row"><span>Book</span>{market !== 'MONEYLINE' && <span>Line</span>}<span>{labA}</span><span>{labB}</span><span>Updated</span></div>
      {sorted.map((q) => {
        const pa = q.sides[a]?.american, pb = q.sides[b]?.american
        return (
          <div key={`${q.sportsbook_key}-${q.line}`} className={`shop__tr ${q.fresh ? '' : 'is-stale'}`} role="row">
            <span>{q.sportsbook ?? q.sportsbook_key}</span>
            {market !== 'MONEYLINE' && <span>{q.line == null ? '—' : market === 'SPREAD' ? `${home} ${q.line > 0 ? '+' : ''}${q.line}` : q.line}</span>}
            <span className={dec(pa) === bestA && pa != null ? 'is-best' : ''}>{odds(pa)}{pa != null && <small>{pct(implied(pa))}</small>}</span>
            <span className={dec(pb) === bestB && pb != null ? 'is-best' : ''}>{odds(pb)}{pb != null && <small>{pct(implied(pb))}</small>}</span>
            <span className="shop__age">{ageText(q.observed_at)}</span>
          </div>
        )
      })}
      <p className="mkt-note"><span className="is-best-key">Green</span> = best payout on that side. Small % = the book's implied chance, including its margin.</p>
    </div>
  )
}

interface TapeFrame { t: string; consensus_no_vig_home: number | null; range_a?: [number, number] | null; usable_books: number }
function Movement({ gameId, home, model }: { gameId: string; home: string; model: number | null }) {
  const loader = useCallback((signal: AbortSignal) => apiRequest<{ frames?: TapeFrame[] }>(`/markets/tape/${gameId}?market=MONEYLINE`, {}, signal), [gameId])
  const res = useApiResource(loader, { key: cacheKey('mkt-tape', { gameId }), staleTimeMs: STALE_TIME.slate })
  const frames = (res.data?.frames ?? []).filter((f) => f.consensus_no_vig_home != null)
  if (res.loading) return <div className="mkt-skel" style={{ height: 150, borderRadius: 10 }} />
  if (frames.length < 2) return <p className="mkt-empty">Not enough price history yet{frames.length ? ' (one observation)' : ''}.</p>
  const W = 420, H = 140, padL = 34, padB = 22
  const ts = frames.map((f) => Date.parse(f.t))
  const t1 = Math.max(...ts)
  const vals = frames.flatMap((f) => [f.consensus_no_vig_home!, ...(f.range_a ? [1 - f.range_a[0], 1 - f.range_a[1]] : [])])
  if (model != null) vals.push(model)
  const lo = Math.max(0, Math.min(...vals) - 0.04), hi = Math.min(1, Math.max(...vals) + 0.04)
  // Evenly spaced by price change: weeks of quiet would otherwise crush the
  // final hours, where nearly all movement happens, into one pixel.
  const n = frames.length
  const xi = (i: number) => padL + (i / (n - 1)) * (W - padL - 8)
  const y = (v: number) => 6 + (1 - (v - lo) / (hi - lo || 1)) * (H - padB - 6)
  let d = ''
  frames.forEach((f, i) => { const X = xi(i), Y = y(f.consensus_no_vig_home!); d += i ? ` H${X.toFixed(1)} V${Y.toFixed(1)}` : `M${X.toFixed(1)},${Y.toFixed(1)}` })
  const fmt = (t: number) => {
    const sameDay = t1 - t < 20 * 3600_000
    return new Intl.DateTimeFormat('en-US', sameDay ? { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' } : { month: 'short', day: 'numeric', timeZone: 'America/New_York' }).format(new Date(t))
  }
  const tickIdx = [0, Math.floor((n - 1) / 2), n - 1]
  const yTicks = [lo + 0.02, (lo + hi) / 2, hi - 0.02].map((v) => Math.round(v * 100) / 100)
  const first = frames[0].consensus_no_vig_home!, last = frames[frames.length - 1].consensus_no_vig_home!
  return (
    <div className="move">
      <p className="move__sum">{home} went from <b>{pct(first)}</b> to <b>{pct(last)}</b> <span className={last > first ? 'up' : 'down'}>({last > first ? '+' : ''}{((last - first) * 100).toFixed(1)} pts)</span> across {frames.length} price changes.</p>
      <svg viewBox={`0 0 ${W} ${H}`} className="move__svg" role="img" aria-label="Consensus win probability over time">
        {yTicks.map((v) => <g key={v}><line x1={padL} x2={W - 8} y1={y(v)} y2={y(v)} className="move__grid" /><text x={padL - 6} y={y(v) + 4} className="move__tick" textAnchor="end">{pct(v)}</text></g>)}
        {model != null && <><line x1={padL} x2={W - 8} y1={y(model)} y2={y(model)} className="move__model" /><text x={W - 10} y={y(model) - 5} className="move__mlab" textAnchor="end">model {pct(model)}</text></>}
        <path d={d} className="move__line" />
        {frames.map((f, i) => <circle key={i} cx={xi(i)} cy={y(f.consensus_no_vig_home!)} r={i === n - 1 ? 4 : 2.2} className="move__end" />)}
        {tickIdx.map((ti, i) => <text key={i} x={xi(ti)} y={H - 6} className="move__tick" textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}>{fmt(ts[ti])}</text>)}
      </svg>
      <p className="mkt-note">Each step is one change in the books' consensus, evenly spaced; dates mark when they happened.</p>
    </div>
  )
}

