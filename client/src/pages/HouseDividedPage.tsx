/**
 * /a-house-divided — a prospective virtual wagering competition.
 *
 * James (the models' official decisions, copied automatically), Scotty and
 * Red Lou each hold a separate virtual $1,000 per sport. Everything shown is
 * a persisted record; an empty state says so rather than inventing a pick.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Ticket } from 'lucide-react'
import { useApiResource } from '../hooks/useApiResource'
import { useAuth } from '../hooks/useAuth'
import { useBroadcastTheme } from '../components/broadcast/primitives'
import { MakePicks } from '../components/houseDivided/MakePicks'
import {
  RELATION_LABEL, americanOdds, houseDivided, lineText, money, wagerTerms,
  type AllSportsBoard, type Board, type BoardGame, type CompetitionSport, type EntryAccess,
  type SportBoard, type SportView, type Standing, type WagerView,
} from '../services/houseDivided'
import '../broadcast.css'
import './HouseDividedPage.css'

const BOARD_PREVIEW = 12
type Tab = 'picks' | 'standings' | 'history'
const TABS: { key: Tab; label: string }[] = [
  { key: 'picks', label: 'MAKE PICKS' },
  { key: 'standings', label: 'STANDINGS' },
  { key: 'history', label: 'WAGER HISTORY' },
]
const VIEWS: SportView[] = ['MLB', 'NFL', 'NBA', 'CFB', 'ALL']
const ET: Intl.DateTimeFormatOptions = { timeZone: 'America/New_York' }

const when = (iso: string | null | undefined, withDate = true): string => {
  if (!iso) return '—'
  const d = new Date(iso)
  const time = d.toLocaleTimeString('en-US', { ...ET, hour: 'numeric', minute: '2-digit' })
  if (!withDate) return `${time} ET`
  return `${d.toLocaleDateString('en-US', { ...ET, weekday: 'short', month: 'short', day: 'numeric' })} · ${time} ET`
}
const pctText = (v: number | null) => (v == null ? '—' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(1)}%`)
const recordText = (r: Standing['record']) => `${r.WIN}–${r.LOSS}${r.PUSH ? `–${r.PUSH}` : ''}`
const firstName = (name: string) => name.split(' — ')[0]

export function HouseDividedPage() {
  useBroadcastTheme()
  const [params, setParams] = useSearchParams()
  const rawSport = params.get('sport')?.toUpperCase() ?? null
  const urlSport: SportView | null = rawSport && (VIEWS as string[]).includes(rawSport) ? (rawSport as SportView) : null
  const view: SportView = urlSport ?? 'MLB'
  const setView = useCallback((next: SportView) => setParams((old) => {
    const copy = new URLSearchParams(old)
    copy.set('sport', next)
    return copy
  }, { replace: true }), [setParams])

  const { user } = useAuth()
  const [access, setAccess] = useState<EntryAccess | null>(null)
  const [accessKnown, setAccessKnown] = useState(false)
  const loadAccess = useCallback(() => {
    if (!user) { setAccess(null); setAccessKnown(true); return }
    houseDivided.me().then(setAccess).catch(() => setAccess(null)).finally(() => setAccessKnown(true))
  }, [user])
  useEffect(() => { loadAccess() }, [loadAccess])
  const canEnter = !!access && access.can_enter_for.length > 0
  const rawTab = params.get('tab') as Tab | null
  const tab: Tab | null = rawTab && TABS.some((t) => t.key === rawTab) ? rawTab
    : !accessKnown ? null : canEnter ? 'picks' : 'standings'
  const setTab = (next: Tab) => setParams((old) => {
    const copy = new URLSearchParams(old)
    copy.set('tab', next)
    return copy
  }, { replace: true })
  const selections = access?.drafts.length ?? 0
  // Make Picks never opens on All Sports: without a chosen sport, the server
  // picks the started sport whose next game is soonest.
  const pickSport: CompetitionSport | null = urlSport && urlSport !== 'ALL' ? urlSport : null

  const loader = useCallback((signal: AbortSignal) => houseDivided.board(view, signal), [view])
  const board = useApiResource<Board>(loader, { key: `house-divided:${view}`, staleTimeMs: 30_000, refreshIntervalMs: 60_000 })
  const data = board.data && board.data.sport === view ? board.data : null

  return (
    <div className="bx hd">
      <div className="bx__inner">
        <header className="bx-head hd-head">
          <div>
            <span className="hd-virtual" role="note">Virtual competition · no real money</span>
            <h1>A House <em>Divided</em></h1>
            <p>James — The Models vs. Scotty vs. Red Lou — Mom. A separate virtual $1,000 for each person in each sport, tracked from a recorded start. No backfill, no top-ups.</p>
          </div>
          {tab !== 'picks' && tab !== null && (
            <button type="button" className="hd-cta" onClick={() => setTab('picks')}>
              <Ticket size={20} aria-hidden /> Make Picks
            </button>
          )}
          {tab === 'picks' && !user && <Link className="bx-btn bx-btn--primary hd-signin-cta" to="/login">Sign in to make picks</Link>}
        </header>

        <nav className="hd-nav" role="tablist" aria-label="A House Divided">
          {TABS.map((t) => (
            <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
              className={`hd-nav__item hd-nav__item--${t.key}`} onClick={() => setTab(t.key)}>
              {t.key === 'picks' && <Ticket size={20} aria-hidden />}
              <span>{t.label}</span>
              {t.key === 'picks' && selections > 0 && <b className="hd-nav__count" aria-label={`${selections} open selections`}>{selections}</b>}
            </button>
          ))}
        </nav>

        {tab === null && <div className="bx-empty" role="status">Loading…</div>}
        {tab === 'picks' && (
          <MakePicks sport={pickSport} onSport={setView} onChanged={() => { loadAccess(); board.refresh() }} />
        )}
        {tab !== 'picks' && tab !== null && <SportTabs view={view} onView={setView} data={data} />}
        {tab !== 'picks' && tab !== null && board.error && !data && <div className="bx-empty" role="alert">The competition could not be loaded: {board.error}</div>}
        {tab !== 'picks' && tab !== null && !data && !board.error && <div className="bx-empty" role="status">Loading the competition…</div>}
        {tab === 'standings' && data && data.sport === 'ALL' && <AllSports data={data as AllSportsBoard} />}
        {tab === 'standings' && data && data.sport !== 'ALL' && <SportPage data={data as SportBoard} />}
        {tab === 'history' && data && <HistoryTab data={data} />}
      </div>
    </div>
  )
}

function SportTabs({ view, onView, data }: { view: SportView; onView: (v: SportView) => void; data: Board | null }) {
  const status = new Map((data?.sports ?? []).map((s) => [s.sport, s]))
  return (
    <div className="bx-segment bx-segment--views hd-tabs" role="group" aria-label="Sport">
      {VIEWS.map((v) => {
        const s = status.get(v as CompetitionSport)
        return (
          <button key={v} type="button" aria-pressed={view === v} onClick={() => onView(v)}>
            {v === 'ALL' ? 'All Sports' : v}
            {s && s.status !== 'STARTED' && <small className="hd-tab-flag">Not started</small>}
          </button>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
function HistoryTab({ data }: { data: Board }) {
  const colors = Object.fromEntries(data.standings.map((s) => [s.participant, s.color]))
  const names = Object.fromEntries(data.standings.map((s) => [s.participant, s.display_name]))
  const open = data.history.filter((w) => w.status === 'OPEN')
  const rest = data.history.filter((w) => w.status !== 'OPEN')
  return (
    <>
      <History title="Open wagers" eyebrow="Awaiting a result" wagers={open} colors={colors} names={names}
        empty="No open wagers. Hand picks appear once submitted; model picks after each game's cutoff." />
      <History title="Settled and not funded" eyebrow="Every record" wagers={rest} colors={colors} names={names}
        empty="Nothing has settled yet." />
    </>
  )
}

function SportPage({ data }: { data: SportBoard }) {
  const colors = useMemo(() => Object.fromEntries(data.standings.map((s) => [s.participant, s.color])), [data.standings])
  const names = useMemo(() => Object.fromEntries(data.standings.map((s) => [s.participant, s.display_name])), [data.standings])
  const order = useMemo(() => [...data.standings].sort((a, b) => sortKey(a) - sortKey(b)).map((s) => s.participant), [data.standings])

  if (data.status === 'NOT_STARTED') {
    return (
      <>
        <StartLine data={data} />
        <div className="hd-cards">{data.standings.map((s) => <CompetitorCard key={s.participant} s={s} notStarted />)}</div>
        <section className="bx-panel hd-empty">
          <h2>{data.sport} has not started</h2>
          <p>{data.not_started_reason}</p>
          <p className="bx-muted">Every account holds its full $1,000. Nothing is imported or backfilled until a start is recorded.</p>
        </section>
      </>
    )
  }
  return (
    <>
      <StartLine data={data} />
      <div className="hd-cards">{data.standings.map((s) => <CompetitorCard key={s.participant} s={s} />)}</div>
      <BankrollChart data={data} />
      <DividedBoard games={data.games} order={order} colors={colors} names={names} />
    </>
  )
}

const sortKey = (s: Standing) => (s.participant === 'JAMES' ? 0 : s.participant === 'SCOTTY' ? 1 : 2)

function StartLine({ data }: { data: SportBoard }) {
  return (
    <div className="hd-startline">
      <span className={`bx-chip ${data.status === 'STARTED' ? 'bx-chip--good' : 'bx-chip--warn'}`}>{data.status === 'STARTED' ? 'Live' : 'Not started'}</span>
      <span><strong>{data.sport}</strong> competition start: {data.started_at ? when(data.started_at) : 'not recorded'}</span>
      {data.official_pipeline && <span className="bx-muted">James's picks: official {data.sport} decisions ({data.official_pipeline.decision_policy}), sized by {data.official_pipeline.staking_policy}</span>}
      <span className="bx-muted">Picks lock {data.rules.cutoff_minutes} min before start · prices must be ≤ {data.rules.max_quote_age_minutes} min old · parlays not yet supported</span>
    </div>
  )
}

function CompetitorCard({ s, notStarted = false }: { s: Standing; notStarted?: boolean }) {
  const tone = s.settled_profit > 0 ? 'good' : s.settled_profit < 0 ? 'bad' : ''
  return (
    <article className="bx-panel hd-card" style={{ ['--hd-c' as string]: s.color }} aria-label={s.display_name}>
      <div className="hd-card__top">
        <span className="hd-rank" aria-label={`Rank ${s.rank}`}>{notStarted ? '—' : `#${s.rank}`}</span>
        <div>
          <h2>{s.display_name}</h2>
          <small>{s.is_model ? 'Official model decisions, copied automatically' : 'Picks entered by hand'}</small>
        </div>
      </div>
      <div className="hd-card__bankroll">
        <small>Bankroll</small>
        <strong className="bx-num">{money(s.bankroll)}</strong>
        <span className={`hd-pl ${tone}`}>{money(s.settled_profit, true)} settled</span>
      </div>
      <dl className="hd-card__stats">
        <div><dt>Available</dt><dd>{money(s.available)}</dd></div>
        <div><dt>Open exposure</dt><dd>{money(s.open_exposure)}{s.open_count ? ` · ${s.open_count} open` : ''}</dd></div>
        <div><dt>Record (W–L–P)</dt><dd>{recordText(s.record)}</dd></div>
        <div><dt>ROI</dt><dd>{pctText(s.roi)}</dd></div>
        <div><dt>Settled wagers</dt><dd>{s.settled_count}</dd></div>
        <div><dt>Volume staked</dt><dd>{money(s.volume)}</dd></div>
      </dl>
      {s.unfunded_count > 0 && <p className="hd-note">{s.unfunded_count} model pick{s.unfunded_count === 1 ? '' : 's'} could not be funded</p>}
    </article>
  )
}

// ---------------------------------------------------------------------------
function BankrollChart({ data }: { data: SportBoard }) {
  const series = data.chart.series
  const all = series.flatMap((s) => s.points)
  const settled = all.filter((p) => p.wager_id).length
  const width = 960
  const height = 300
  const pad = { l: 64, r: 24, t: 18, b: 34 }
  const times = all.map((p) => Date.parse(p.at))
  const t0 = Math.min(...times)
  const t1 = Math.max(...times, t0 + 3_600_000)
  const values = all.map((p) => p.bankroll).concat([data.chart.starting_balance])
  const spread = Math.max(20, ...values.map((v) => Math.abs(v - data.chart.starting_balance)))
  const lo = data.chart.starting_balance - spread * 1.15
  const hi = data.chart.starting_balance + spread * 1.15
  const x = (t: number) => pad.l + ((t - t0) / (t1 - t0)) * (width - pad.l - pad.r)
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (height - pad.t - pad.b)
  const step = (points: { at: string; bankroll: number }[]) => points.map((p, i) => {
    const px = x(Date.parse(p.at)); const py = y(p.bankroll)
    if (i === 0) return `M${px},${py}`
    return `H${px}V${py}`
  }).join('')
  const ticks = [lo, data.chart.starting_balance, hi]
  return (
    <section className="bx-panel hd-chart" aria-label="Cumulative bankroll">
      <div className="bx-panel__head">
        <div><span className="bx-eyebrow">Cumulative bankroll</span><h2>Everyone starts at $1,000</h2></div>
        <div className="hd-legend">{series.map((s) => <span key={s.participant}><i style={{ background: s.color }} />{firstName(s.display_name)}</span>)}</div>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Bankroll after each settled wager">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={width - pad.r} y1={y(v)} y2={y(v)} className={v === data.chart.starting_balance ? 'hd-axis hd-axis--base' : 'hd-axis'} />
            <text x={pad.l - 8} y={y(v) + 4} textAnchor="end" className="hd-tick">{money(v).replace('.00', '')}</text>
          </g>
        ))}
        {series.map((s, i) => (
          <path key={s.participant} d={step(s.points)} fill="none" stroke={s.color} strokeWidth={3.5}
            strokeDasharray={i === 0 ? undefined : i === 1 ? '10 5' : '3 4'} strokeLinejoin="round" />
        ))}
        <text x={pad.l} y={height - 10} className="hd-tick">{when(new Date(t0).toISOString())}</text>
        <text x={width - pad.r} y={height - 10} textAnchor="end" className="hd-tick">now</text>
      </svg>
      <p className="bx-muted hd-chart__note">
        {settled === 0
          ? 'No wager has settled yet, so every line is still flat at $1,000. Open stakes reduce available funds, not the bankroll.'
          : 'The line moves only when a wager settles. Open stakes reduce available funds, not the bankroll.'}
      </p>
    </section>
  )
}

// ---------------------------------------------------------------------------
function DividedBoard({ games, order, colors, names }: {
  games: BoardGame[]; order: string[]; colors: Record<string, string>; names: Record<string, string>
}) {
  const [all, setAll] = useState(false)
  const shown = all ? games : games.slice(0, BOARD_PREVIEW)
  return (
    <section className="bx-panel hd-board" aria-label="Where the House Is Divided">
      <div className="bx-panel__head">
        <div><span className="bx-eyebrow">Side by side</span><h2>Where the House Is Divided</h2></div>
      </div>
      {games.length === 0 && <div className="bx-empty">No games since the competition started yet.</div>}
      <div className="hd-board__head" aria-hidden>
        <span>Game</span>
        {order.map((k) => <span key={k} style={{ color: colors[k] }}>{firstName(names[k] ?? k)}</span>)}
      </div>
      <ol className="hd-games">
        {shown.map((g) => {
          const cells = new Map(g.cells.map((c) => [c.participant, c]))
          const relations = g.comparisons.filter((c) => c.relation !== 'SAME_SIDE')
          return (
            <li key={g.game_id} className={`hd-game${g.divided ? ' hd-game--divided' : ''}`}>
              <div className="hd-game__match">
                <strong>{g.away?.abbreviation ?? 'Away'} @ {g.home?.abbreviation ?? 'Home'}</strong>
                <small>{g.status === 'FINAL' ? `Final ${g.away_score}–${g.home_score}` : g.status === 'SCHEDULED' ? when(g.starts_at) : `${g.status.toLowerCase()} · ${when(g.starts_at)}`}</small>
                {!g.locked && <small className="hd-lock">Picks lock {when(g.cutoff, false)}</small>}
                {relations.map((r) => (
                  <span key={`${r.a}-${r.b}`} className={`hd-rel hd-rel--${r.relation.toLowerCase()}`}>
                    {firstName(names[r.a] ?? r.a)} vs {firstName(names[r.b] ?? r.b)}: {RELATION_LABEL[r.relation]}
                  </span>
                ))}
              </div>
              {order.map((k) => {
                const c = cells.get(k)
                return (
                  <div key={k} className="hd-cell" style={{ ['--hd-c' as string]: colors[k] }}>
                    <span className="hd-cell__who">{firstName(names[k] ?? k)}</span>
                    {c && c.wagers.length > 0 ? c.wagers.map((w) => <WagerChip key={w.wager_id} w={w} />)
                      : <span className={`hd-state hd-state--${(c?.state.code ?? 'none').toLowerCase()}`} title={c?.state.detail}>{c?.state.label ?? '—'}</span>}
                  </div>
                )
              })}
            </li>
          )
        })}
      </ol>
      {games.length > BOARD_PREVIEW && (
        <button type="button" className="bx-btn hd-more" onClick={() => setAll((v) => !v)}>
          {all ? 'Show fewer games' : `Show all ${games.length} games`}
        </button>
      )}
    </section>
  )
}

function WagerChip({ w }: { w: WagerView }) {
  if (w.hidden) {
    return (
      <div className="hd-wager hd-wager--hidden">
        <span className="hd-wager__terms">Pick locked</span>
        <span className="hd-wager__meta">Stake {money(w.stake)} · revealed when picks lock</span>
      </div>
    )
  }
  const badge = w.status === 'SETTLED' ? w.result : w.status === 'UNFUNDED' ? 'NOT FUNDED' : 'OPEN'
  return (
    <div className={`hd-wager hd-wager--${(badge ?? '').toLowerCase().replace(' ', '-')}`}>
      <span className="hd-wager__terms">{wagerTerms(w)}</span>
      <span className="hd-wager__meta">
        {w.status === 'UNFUNDED' ? w.unfunded_reason : <>Stake {money(w.stake)}{w.sportsbook ? ` · ${w.sportsbook}` : ''}</>}
      </span>
      <span className="hd-wager__badge">{badge}{w.status === 'SETTLED' && w.profit_loss != null ? ` ${money(w.profit_loss, true)}` : ''}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
function History({ wagers, colors, names, title = 'Wager history', eyebrow = 'Every record', empty }: {
  wagers: WagerView[]; colors: Record<string, string>; names: Record<string, string>
  title?: string; eyebrow?: string; empty?: string
}) {
  return (
    <section className="bx-panel hd-history" aria-label={title}>
      <div className="bx-panel__head"><div><span className="bx-eyebrow">{eyebrow}</span><h2>{title}</h2></div></div>
      {wagers.length === 0 && <div className="bx-empty">{empty ?? "No wagers yet. Model picks appear after each game's cutoff; hand picks appear once submitted."}</div>}
      <ul className="hd-history__list">
        {wagers.map((w) => (
          <li key={w.wager_id}>
            <details>
              <summary>
                <i style={{ background: colors[w.participant] }} />
                <span className="hd-history__who">{firstName(names[w.participant] ?? w.participant)}</span>
                <span className="hd-history__terms">{w.hidden ? `Pick locked · revealed ${w.reveal_at ? new Date(w.reveal_at).toLocaleTimeString('en-US', { ...ET, hour: 'numeric', minute: '2-digit' }) + ' ET' : 'at lock'}` : wagerTerms(w)}</span>
                <span className="hd-history__stake">{money(w.stake)}</span>
                <span className={`hd-history__result hd-history__result--${(w.result ?? w.status).toLowerCase()}`}>
                  {w.status === 'SETTLED' ? `${w.result} ${money(w.profit_loss, true)}` : w.status === 'UNFUNDED' ? 'Not funded' : 'Open'}
                </span>
              </summary>
              <dl className="hd-history__detail">
                <div><dt>Source</dt><dd>{w.source === 'MODEL_MIRROR' ? 'Official model decision (copied)' : 'Entered by hand'}</dd></div>
                <div><dt>Submitted</dt><dd>{when(w.submitted_at)}</dd></div>
                {w.entered_by && <div><dt>Entered by</dt><dd>{w.entered_by}{w.entered_on_behalf ? ' (on behalf)' : ''}</dd></div>}
                <div><dt>Price</dt><dd>{americanOdds(w.american_odds)} ({w.decimal_odds?.toFixed(3) ?? '—'}) at {w.sportsbook ?? '—'}</dd></div>
                {w.line != null && <div><dt>Line</dt><dd>{lineText(w.market_type, w.line)}</dd></div>}
                <div><dt>Quote</dt><dd>observed {when(w.quote_observed_at)} · id {w.quote_id?.slice(0, 8) ?? '—'}</dd></div>
                {w.model_version && <div><dt>Model version</dt><dd>{w.model_version}</dd></div>}
                {w.source_decision_id && <div><dt>Decision</dt><dd>{w.source_decision_id.slice(0, 8)} · issued {when(w.decision_recommended_at)}</dd></div>}
                {w.unfunded_reason && <div><dt>Not funded</dt><dd>{w.unfunded_reason}</dd></div>}
                {w.settled_at && <div><dt>Settled</dt><dd>{when(w.settled_at)} · payout {money(w.payout)}{w.final_score ? ` · final ${w.final_score.away}–${w.final_score.home}` : ''}</dd></div>}
                {w.settlement_note && <div><dt>Note</dt><dd>{w.settlement_note}</dd></div>}
              </dl>
            </details>
          </li>
        ))}
      </ul>
    </section>
  )
}

// ---------------------------------------------------------------------------
function AllSports({ data }: { data: AllSportsBoard }) {
  const colors = Object.fromEntries(data.standings.map((s) => [s.participant, s.color]))
  const names = Object.fromEntries(data.standings.map((s) => [s.participant, s.display_name]))
  return (
    <>
      <div className="hd-startline">
        <span className="bx-chip">All Sports</span>
        <span>Started: <strong>{data.started_sports.length ? data.started_sports.join(', ') : 'none yet'}</strong></span>
        <span>Combined starting capital: <strong>{money(data.combined_starting_capital_each)}</strong> each</span>
        <span className="bx-muted">{data.note}</span>
      </div>
      {data.started_sports.length === 0
        ? <section className="bx-panel hd-empty"><h2>No sport has started</h2><p>Every account holds its full $1,000.</p></section>
        : <div className="hd-cards">{data.standings.map((s) => <CompetitorCard key={s.participant} s={s} />)}</div>}
      {data.started_sports.length > 0 && (
        <section className="bx-panel" aria-label="By sport">
          <div className="bx-panel__head"><div><span className="bx-eyebrow">Accounts stay separate</span><h2>By sport</h2></div></div>
          <div className="hd-table-wrap">
            <table className="bx-table">
              <thead><tr><th>Account</th><th>Start</th><th>Bankroll</th><th>Settled P/L</th><th>Open exposure</th><th>Record</th><th>ROI</th></tr></thead>
              <tbody>
                {data.standings.flatMap((s) => (s.by_sport ?? []).map((a) => (
                  <tr key={`${s.participant}-${a.sport}`}>
                    <td><i className="hd-dot" style={{ background: s.color }} />{firstName(s.display_name)} · {a.sport}</td>
                    <td>{money(a.starting_balance)}</td><td>{money(a.bankroll)}</td><td>{money(a.settled_profit, true)}</td>
                    <td>{money(a.open_exposure)}</td><td>{recordText(a.record)}</td><td>{pctText(a.roi)}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <History wagers={data.history} colors={colors} names={names} />
    </>
  )
}
