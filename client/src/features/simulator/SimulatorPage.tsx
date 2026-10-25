import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Dices, Info, Pause, Play, RotateCcw, Target } from 'lucide-react'
import { ScoreBar } from '../shared/ScoreBug'
import { teamIdentity } from '../../services/teamIdentity'
import { distribution, fairAmerican, lineOutcome, makeSampler, useSimModel, type Cell, type SimModel } from './simData'
import { FORECAST_TIMING_SUMMARY, forecastRefreshText } from '../../services/forecastCadence'
import './simulator.css'

const pct = (p: number | null | undefined, d = 0) => (p == null ? '—' : `${(p * 100).toFixed(d)}%`)
const signed = (n: number) => (n > 0 ? `+${n}` : `${n}`)
const odds = (n: number | null) => (n == null ? '—' : n > 0 ? `+${n}` : `${n}`)
const half = (x: number) => Math.round(x * 2) / 2

export function SimulatorPage() {
  const { gameId = '' } = useParams()
  const [params] = useSearchParams()
  const sport = params.get('sport') ?? 'NFL'
  const res = useSimModel(gameId)
  const m = res.data?.model ?? null

  return (
    <div className="sim">
      <nav className="sim__crumbs">
        <Link to={`/games/${gameId}?sport=${sport}`}><ArrowLeft size={14} /> Game brief</Link>
        <span>Game Simulator</span>
      </nav>
      {res.loading && <div className="sim-skel" style={{ height: 420 }} />}
      {!res.loading && !m && (
        <div className="sim-empty">
          <Dices size={28} />
          <h1>No simulation for this game yet</h1>
          <p>{res.data?.reason ?? res.error ?? 'The model has not published a score distribution for this game.'}</p>
          <p>Forecast timing ({sport}): {forecastRefreshText(sport)}. {FORECAST_TIMING_SUMMARY}. <Link to="/labs">Browse games that have one</Link>.</p>
        </div>
      )}
      {m && <Simulator m={m} gameId={gameId} />}
    </div>
  )
}

function Simulator({ m, gameId }: { m: SimModel; gameId: string }) {
  const awayColor = teamIdentity(m.sport, m.away.abbr).primary
  const homeColor = teamIdentity(m.sport, m.home.abbr).primary
  const colors = { away: awayColor, home: homeColor }
  const stale = m.hoursBeforeStart != null && m.hoursBeforeStart > 48
  return (
    <>
      <ScoreBar gameId={gameId} sport={m.sport} away={m.away} home={m.home} startsAt={m.startsAt} />
      <p className="sim__prov">
        <strong>{m.modelLabel}</strong> · {m.draws.toLocaleString()} simulated games
        {m.forecastAt && <> · forecast {m.hoursBeforeStart != null ? `${formatLead(m.hoursBeforeStart)} before start` : ''}</>}
        {m.development && <span className="sim__flag">Development model · no proven edge</span>}
        {stale && <span className="sim__flag">Early forecast · {forecastRefreshText(m.sport)}</span>}
      </p>

      <div className="sim__row sim__row--top">
        <WhoWins m={m} colors={colors} />
        {m.final ? <HowItPlayed m={m} /> : <LikelyScores m={m} />}
      </div>

      <LineLab m={m} colors={colors} />

      <div className="sim__row">
        <PlayItOut m={m} colors={colors} />
        <ScoreMap m={m} />
      </div>
    </>
  )
}

function formatLead(h: number) {
  if (h < 0) return 'after the start'
  if (h < 1) return `${Math.round(h * 60)} min`
  if (h < 48) return `${Math.round(h)} h`
  return `${Math.round(h / 24)} days`
}

// ---------------------------------------------------------------- who wins --

function WhoWins({ m, colors }: { m: SimModel; colors: { away: string; home: string } }) {
  const mk = m.market
  const fav = m.win.home >= m.win.away ? m.home : m.away
  const favP = Math.max(m.win.home, m.win.away)
  const marketOld = mk?.observedAt ? (Date.now() - Date.parse(mk.observedAt)) / 3600_000 > 36 : true
  return (
    <section className="sim-card sim-who">
      <p className="sim-eyebrow">Who wins</p>
      <h2><span style={{ color: fav === m.home ? colors.home : colors.away }}>{fav.abbr}</span> wins {pct(favP)} of simulations</h2>
      <div className="winbar" aria-label="Model win probability">
        <span style={{ width: pct(m.win.away, 2), background: colors.away }}><b>{m.win.away >= 0.25 ? `${m.away.abbr} ` : ''}{pct(m.win.away)}</b></span>
        {m.win.tie > 0.001 && <span className="winbar__tie" style={{ width: pct(m.win.tie, 2) }} title={`Tie ${pct(m.win.tie, 1)}`} />}
        <span style={{ width: pct(m.win.home, 2), background: colors.home }} className="winbar__home"><b>{pct(m.win.home)}{m.win.home >= 0.25 ? ` ${m.home.abbr}` : ''}</b></span>
      </div>
      {mk?.homeNoVig != null && (
        <div className={`winbar winbar--market ${marketOld ? 'is-old' : ''}`} aria-label="Sportsbook implied probability">
          <span style={{ width: pct(1 - mk.homeNoVig, 2) }}><b>{pct(1 - mk.homeNoVig)}</b></span>
          <span style={{ width: pct(mk.homeNoVig, 2) }} className="winbar__home"><b>{pct(mk.homeNoVig)}</b></span>
        </div>
      )}
      <p className="sim-who__legend">
        <span>Top bar: our model{m.win.tie > 0.001 ? ` (gray = tie, ${pct(m.win.tie, 1)})` : ''}.</span>
        {mk?.homeNoVig != null && <span> Bottom bar: sportsbooks ({mk.books} books, {marketOld ? 'old price, not comparable' : mk.current ? 'current' : 'recent'}).</span>}
      </p>
      <dl className="sim-stats">
        <div><dt>Expected score</dt><dd>{m.away.abbr} {m.expected.away.toFixed(1)} – {m.expected.home.toFixed(1)} {m.home.abbr}</dd></div>
        <div><dt>Expected margin</dt><dd>{m.home.abbr} {signed(Number((m.expected.home - m.expected.away).toFixed(1)))}</dd></div>
        <div><dt>Expected total</dt><dd>{(m.expected.home + m.expected.away).toFixed(1)}</dd></div>
      </dl>
    </section>
  )
}

function LikelyScores({ m }: { m: SimModel }) {
  const top = [...m.cells].sort((a, b) => b.p - a.p).slice(0, 6)
  const max = top[0]?.p ?? 1
  return (
    <section className="sim-card">
      <p className="sim-eyebrow">Most likely final scores</p>
      <ol className="likely">
        {top.map((c, i) => (
          <li key={`${c.away}-${c.home}`}>
            <span className="likely__rank">{i + 1}</span>
            <span className="likely__score">{m.away.abbr} {c.away} – {c.home} {m.home.abbr}</span>
            <span className="likely__bar"><span style={{ width: `${(c.p / max) * 100}%` }} /></span>
            <span className="likely__p">{pct(c.p, 1)}</span>
          </li>
        ))}
      </ol>
      <p className="sim-note">Even the single most likely score happens in only {pct(top[0]?.p, 1)} of games. That is why the model thinks in ranges, not exact scores.</p>
    </section>
  )
}

function HowItPlayed({ m }: { m: SimModel }) {
  const f = m.final!
  const margin = f.home - f.away
  const winner = margin > 0 ? m.home : margin < 0 ? m.away : null
  const winnerP = margin > 0 ? m.win.home : margin < 0 ? m.win.away : m.win.tie
  // Share of simulations at least this lopsided in the winner's direction.
  const asExtreme = m.cells.filter((c) => (margin > 0 ? c.home - c.away >= margin : margin < 0 ? c.away - c.home >= -margin : c.home === c.away)).reduce((s, c) => s + c.p, 0)
  const exact = m.cells.find((c) => c.home === Math.min(f.home, (m.cap ?? 999) + 1) && c.away === Math.min(f.away, (m.cap ?? 999) + 1))?.p ?? 0
  const verdict = winnerP >= 0.6 ? 'Called it' : winnerP >= 0.4 ? 'Coin flip' : winnerP >= 0.2 ? 'Upset' : 'Big upset'
  return (
    <section className={`sim-card played played--${verdict.replace(' ', '-').toLowerCase()}`}>
      <p className="sim-eyebrow">How it played out</p>
      <div className="played__stamp">{verdict}</div>
      <h2>{winner ? `${winner.abbr} won by ${Math.abs(margin)}` : 'It ended tied'}</h2>
      <ul className="played__facts">
        <li><b>{pct(winnerP)}</b><span>of simulations had {winner ? `${winner.abbr} winning` : 'a tie'}</span></li>
        <li><b>{pct(asExtreme)}</b><span>{winner ? `had ${winner.abbr} winning by ${Math.abs(margin)} or more` : 'ended level'}</span></li>
        <li><b>{exact > 0 ? pct(exact, 2) : '<0.01%'}</b><span>landed on exactly {m.away.abbr} {f.away} – {f.home} {m.home.abbr}</span></li>
      </ul>
      <p className="sim-note">The actual final is ringed on the score map below. The model's job is to get the odds right over many games, not to call each one.</p>
    </section>
  )
}

// ---------------------------------------------------------------- line lab --

function LineLab({ m, colors }: { m: SimModel; colors: { away: string; home: string } }) {
  const [kind, setKind] = useState<'spread' | 'total'>('spread')
  const expMargin = m.expected.home - m.expected.away
  const expTotal = m.expected.home + m.expected.away
  // Without a book line: MLB uses the standard run line, NFL the nearest half point to the model margin.
  const defaultSpread = m.bookLines.spreadHome
    ?? (m.sport === 'MLB' ? (expMargin >= 0 ? -1.5 : 1.5) : Math.floor(-expMargin) + 0.5)
  const defaultTotal = m.bookLines.total ?? half(expTotal) + 0.5
  const [spreadHome, setSpreadHome] = useState(defaultSpread)
  const [total, setTotal] = useState(defaultTotal)
  const [side, setSide] = useState<'A' | 'B'>('B') // A = away/over, B = home/under

  const dist = useMemo(() => distribution(m.cells, kind === 'spread' ? 'margin' : 'total'), [m.cells, kind])
  // Visible range: the middle 98% of outcomes.
  const range = useMemo(() => {
    let acc = 0, lo = dist[0]?.value ?? 0, hi = dist[dist.length - 1]?.value ?? 1
    for (const d of dist) { acc += d.p; if (acc >= 0.01) { lo = d.value; break } }
    acc = 0
    for (const d of [...dist].reverse()) { acc += d.p; if (acc >= 0.01) { hi = d.value; break } }
    return { lo: Math.floor(lo), hi: Math.ceil(hi) }
  }, [dist])

  const line = kind === 'spread' ? spreadHome : total
  const threshold = kind === 'spread' ? -spreadHome : total // the margin / total value the line sits on
  const out = lineOutcome(m.cells, kind, line)
  // Side A: away (spread) or over (total)... normalise to the selected side's view.
  const sideIsHomeOrUnder = side === 'B'
  const win = kind === 'spread' ? (sideIsHomeOrUnder ? out.win : out.loss) : (sideIsHomeOrUnder ? out.loss : out.win)
  const loss = kind === 'spread' ? (sideIsHomeOrUnder ? out.loss : out.win) : (sideIsHomeOrUnder ? out.win : out.loss)
  const fair = fairAmerican(win, loss)
  const sideName = kind === 'spread'
    ? (sideIsHomeOrUnder ? `${m.home.abbr} ${signed(spreadHome)}` : `${m.away.abbr} ${signed(-spreadHome)}`)
    : (sideIsHomeOrUnder ? `Under ${total}` : `Over ${total}`)
  const bookLine = kind === 'spread' ? m.bookLines.spreadHome : m.bookLines.total

  const setThreshold = (v: number) => {
    const h = half(v)
    if (kind === 'spread') setSpreadHome(-h); else setTotal(h)
  }

  return (
    <section className="sim-card linelab">
      <header className="linelab__head">
        <div>
          <p className="sim-eyebrow"><Target size={13} /> Check a line</p>
          <h2>Would {sideName} cash?</h2>
        </div>
        <div className="seg" role="group" aria-label="Market">
          <button aria-pressed={kind === 'spread'} onClick={() => setKind('spread')}>Spread</button>
          <button aria-pressed={kind === 'total'} onClick={() => setKind('total')}>Total</button>
        </div>
      </header>
      <div className="linelab__body">
        <div className="linelab__chart">
          <Histogram dist={dist} range={range} threshold={threshold} kind={kind} sideB={sideIsHomeOrUnder} colors={colors}
            onPick={setThreshold} labels={kind === 'spread' ? [`← ${m.away.abbr} wins big`, `${m.home.abbr} wins big →`] : [`← fewer ${m.sport === 'MLB' ? 'runs' : 'points'}`, `more ${m.sport === 'MLB' ? 'runs' : 'points'} →`]} />
          <label className="linelab__slider">
            <span>{kind === 'spread' ? `${m.home.abbr} line` : 'Total line'}</span>
            <button aria-label="Lower line" onClick={() => kind === 'spread' ? setSpreadHome(spreadHome - 0.5) : setTotal(total - 0.5)}>−</button>
            <input type="range" min={kind === 'spread' ? -range.hi : range.lo} max={kind === 'spread' ? -range.lo : range.hi} step={0.5}
              value={line} onChange={(e) => kind === 'spread' ? setSpreadHome(Number(e.target.value)) : setTotal(Number(e.target.value))} />
            <button aria-label="Raise line" onClick={() => kind === 'spread' ? setSpreadHome(spreadHome + 0.5) : setTotal(total + 0.5)}>+</button>
            <output>{kind === 'spread' ? signed(spreadHome) : total}</output>
          </label>
          <p className="sim-note">Drag across the chart or use the slider. Green bars are simulated games where your pick cashes; gray bars lose; the bar exactly on the line is a push.</p>
        </div>
        <div className="linelab__side">
          <div className="seg seg--wide" role="group" aria-label="Side">
            <button aria-pressed={side === 'A'} onClick={() => setSide('A')}>{kind === 'spread' ? `${m.away.abbr} ${signed(-spreadHome)}` : `Over ${total}`}</button>
            <button aria-pressed={side === 'B'} onClick={() => setSide('B')}>{kind === 'spread' ? `${m.home.abbr} ${signed(spreadHome)}` : `Under ${total}`}</button>
          </div>
          <div className="verdict">
            <div className="verdict__big">{pct(win)}</div>
            <p>of simulated games, <strong>{sideName}</strong> cashes</p>
            <div className="verdict__bar"><span className="w" style={{ width: pct(win, 2) }} /><span className="p" style={{ width: pct(1 - win - loss, 2) }} /><span className="l" style={{ width: pct(loss, 2) }} /></div>
            <dl>
              <div><dt>Cash</dt><dd>{pct(win, 1)}</dd></div>
              <div><dt>Push</dt><dd>{pct(1 - win - loss, 1)}</dd></div>
              <div><dt>Lose</dt><dd>{pct(loss, 1)}</dd></div>
              <div><dt>Fair odds</dt><dd>{odds(fair)}</dd></div>
            </dl>
            <p className="sim-note">Fair odds are the price at which this bet breaks even if the model is right. A book paying more than that is offering value; less, and it is not.</p>
          </div>
          {bookLine != null && (
            <div className="bookline">
              <span>Sportsbook line: <strong>{kind === 'spread' ? `${m.home.abbr} ${signed(bookLine)}` : bookLine}</strong>{!m.bookLines.fresh && <em> · stored, not current</em>}</span>
              {bookLine !== line && <button onClick={() => kind === 'spread' ? setSpreadHome(bookLine) : setTotal(bookLine)}>Use it</button>}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

function Histogram({ dist, range, threshold, kind, sideB, colors, onPick, labels }: {
  dist: { value: number; p: number }[]; range: { lo: number; hi: number }; threshold: number; kind: 'spread' | 'total'; sideB: boolean
  colors: { away: string; home: string }; onPick: (v: number) => void; labels: [string, string]
}) {
  const W = 520, H = 180, pad = 16
  const vis = dist.filter((d) => d.value >= range.lo && d.value <= range.hi)
  const max = Math.max(...vis.map((d) => d.p), 0.0001)
  const n = range.hi - range.lo + 1
  const bw = (W - pad * 2) / n
  const x = (v: number) => pad + (v - range.lo) * bw
  const ref = useRef<SVGSVGElement>(null)
  const dragging = useRef(false)
  const pick = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect()
    const vx = ((clientX - r.left) / r.width) * W
    onPick(range.lo + (vx - pad) / bw - 0.5)
  }
  // For spread, the side B (home) cashes when margin > threshold; for total, side B (under) cashes when total < threshold.
  const cashes = (v: number) => kind === 'spread' ? (sideB ? v > threshold : v < threshold) : (sideB ? v < threshold : v > threshold)
  const ticks = Array.from({ length: n }, (_, i) => range.lo + i).filter((v) => v % (n > 40 ? 10 : n > 16 ? 5 : n > 10 ? 2 : 1) === 0)
  return (
    <svg ref={ref} className="hist" viewBox={`0 -6 ${W} ${H + 44}`} role="img" aria-label="Distribution with the selected line"
      onPointerDown={(e) => { dragging.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); pick(e.clientX) }}
      onPointerMove={(e) => { if (dragging.current) pick(e.clientX) }}
      onPointerUp={() => { dragging.current = false }}>
      {vis.map((d) => {
        const h = (d.p / max) * (H - 10)
        const onLine = d.value === threshold
        const cls = onLine ? 'hist__bar hist__bar--push' : cashes(d.value) ? 'hist__bar hist__bar--win' : 'hist__bar'
        return <rect key={d.value} className={cls} x={x(d.value) + 1} y={H - h} width={Math.max(1, bw - 2)} height={h} rx={Math.min(3, bw / 4)} />
      })}
      {kind === 'spread' && <line x1={x(0) + bw / 2} x2={x(0) + bw / 2} y1={0} y2={H} className="hist__zero" />}
      <line x1={x(threshold) + bw / 2} x2={x(threshold) + bw / 2} y1={-4} y2={H + 4} className="hist__line" />
      <circle cx={x(threshold) + bw / 2} cy={H + 4} r={6} className="hist__handle" />
      {ticks.map((v) => <text key={v} x={x(v) + bw / 2} y={H + 20} className="hist__tick">{kind === 'spread' && v > 0 ? `+${v}` : v}</text>)}
      <text x={pad} y={H + 37} className="hist__lab" style={{ fill: kind === 'spread' ? colors.away : undefined }}>{labels[0]}</text>
      <text x={W - pad} y={H + 37} className="hist__lab" textAnchor="end" style={{ fill: kind === 'spread' ? colors.home : undefined }}>{labels[1]}</text>
    </svg>
  )
}

// ------------------------------------------------------------- play it out --

function PlayItOut({ m, colors }: { m: SimModel; colors: { away: string; home: string } }) {
  const N = 100
  const sampler = useMemo(() => makeSampler(m.cells), [m.cells])
  const [games, setGames] = useState<Cell[]>([])
  const [running, setRunning] = useState(false)
  const [batches, setBatches] = useState<number[]>([])
  useEffect(() => {
    if (!running) return
    if (games.length >= N) {
      setRunning(false)
      setBatches((b) => [...b.slice(-7), games.filter((g) => g.home > g.away).length])
      return
    }
    const t = window.setTimeout(() => setGames((g) => [...g, sampler()]), 28)
    return () => window.clearTimeout(t)
  }, [running, games, sampler])
  const start = () => { setGames([]); setRunning(true) }
  const home = games.filter((g) => g.home > g.away).length
  const away = games.filter((g) => g.away > g.home).length
  const tie = games.length - home - away
  const last = games[games.length - 1]
  const blowout = games.reduce<Cell | null>((b, g) => (!b || Math.abs(g.home - g.away) > Math.abs(b.home - b.away) ? g : b), null)
  return (
    <section className="sim-card playit">
      <p className="sim-eyebrow"><Dices size={13} /> Play it out</p>
      <h2>Watch 100 games</h2>
      <p className="sim-note">Each dot is one game drawn from the model's {m.draws.toLocaleString()} simulations. Run it a few times: the count wobbles even when the model doesn't change. That wobble is luck.</p>
      <div className="playit__grid" aria-live="polite">
        {Array.from({ length: N }, (_, i) => {
          const g = games[i]
          const c = !g ? undefined : g.home > g.away ? colors.home : g.away > g.home ? colors.away : '#9aa8b8'
          return <i key={i} className={g ? 'is-on' : ''} style={c ? { background: c } : undefined} title={g ? `${m.away.abbr} ${g.away} – ${g.home} ${m.home.abbr}` : undefined} />
        })}
      </div>
      <div className="playit__tally">
        <span style={{ color: colors.away }}><b>{away}</b> {m.away.abbr}</span>
        {tie > 0 && <span><b>{tie}</b> tie</span>}
        <span style={{ color: colors.home }}><b>{home}</b> {m.home.abbr}</span>
        <span className="playit__last">{last ? `Game ${games.length}: ${m.away.abbr} ${last.away} – ${last.home} ${m.home.abbr}` : 'Press play'}</span>
      </div>
      <div className="playit__actions">
        <button className="btn-primary" onClick={running ? () => setRunning(false) : start}>{running ? <><Pause size={15} /> Pause</> : games.length ? <><RotateCcw size={15} /> Run 100 more</> : <><Play size={15} /> Play 100 games</>}</button>
        {!running && games.length >= N && blowout && <span className="sim-note">Biggest blowout this run: {m.away.abbr} {blowout.away} – {blowout.home} {m.home.abbr}</span>}
      </div>
      {batches.length > 1 && (
        <p className="playit__batches">{m.home.abbr} wins per run: {batches.map((b, i) => <b key={i}>{b}</b>)} <span>· model says {pct(m.win.home)}</span></p>
      )}
    </section>
  )
}

// ---------------------------------------------------------------- score map --

function ScoreMap({ m }: { m: SimModel }) {
  const [hover, setHover] = useState<Cell | null>(null)
  // Zoom to where 99.5% of each team's scores fall (and the actual final).
  const q = (key: 'home' | 'away') => {
    const by = new Map<number, number>()
    for (const c of m.cells) by.set(c[key], (by.get(c[key]) ?? 0) + c.p)
    let acc = 0
    for (const [v, p] of [...by.entries()].sort((a, b) => a[0] - b[0])) { acc += p; if (acc >= 0.995) return v }
    return Math.max(...by.keys())
  }
  const capVal = (v: number) => (m.cap != null && v > m.cap ? m.cap + 1 : v)
  const span = Math.max(q('away'), q('home'), m.final ? capVal(m.final.away) : 0, m.final ? capVal(m.final.home) : 0)
  const maxA = span, maxH = span
  const size = 300, cs = size / (span + 1)
  const tickStep = span > 30 ? 7 : span > 12 ? 3 : 1
  const maxP = Math.max(...m.cells.map((c) => c.p))
  const final = m.final
  return (
    <section className="sim-card scoremap">
      <p className="sim-eyebrow">Every possible score</p>
      <h2>The score map</h2>
      <div className="scoremap__body">
        <svg viewBox={`-32 -6 ${size + 40} ${size + 38}`} className="scoremap__svg" role="img" aria-label="Probability of each final score" onPointerLeave={() => setHover(null)}>
          <line x1={0} y1={size} x2={size} y2={0} className="scoremap__diag" />
          {m.cells.filter((c) => c.away <= maxA && c.home <= maxH).map((c) => (
            <rect key={`${c.away}-${c.home}`} x={c.away * cs} y={size - (c.home + 1) * cs} width={cs} height={cs}
              style={{ fillOpacity: 0.12 + 0.88 * Math.sqrt(c.p / maxP) }}
              className={`scoremap__cell ${c.home > c.away ? 'is-home' : c.away > c.home ? 'is-away' : 'is-tie'}`}
              onPointerEnter={() => setHover(c)} />
          ))}
          {final && <circle cx={(capVal(final.away) + 0.5) * cs} cy={size - (capVal(final.home) + 0.5) * cs} r={Math.max(5, cs * 0.9)} className="scoremap__final" />}
          {hover && <rect x={hover.away * cs} y={size - (hover.home + 1) * cs} width={cs} height={cs} className="scoremap__hover" />}
          {Array.from({ length: Math.floor(span / tickStep) + 1 }, (_, i) => i * tickStep).map((v) => (
            <g key={v}>
              <text x={(v + 0.5) * cs} y={size + 12} className="scoremap__tick" textAnchor="middle">{v}</text>
              <text x={-4} y={size - (v + 0.5) * cs + 3} className="scoremap__tick" textAnchor="end">{v}</text>
            </g>
          ))}
          <text x={size / 2} y={size + 26} className="scoremap__axis" textAnchor="middle">{m.away.abbr} score →</text>
          <text x={-20} y={size / 2} className="scoremap__axis" textAnchor="middle" transform={`rotate(-90 -20 ${size / 2})`}>{m.home.abbr} score →</text>
        </svg>
        <div className="scoremap__readout">
          {hover ? (
            <>
              <strong>{m.away.abbr} {hover.away}{m.cap != null && hover.away > m.cap ? '+' : ''} – {hover.home}{m.cap != null && hover.home > m.cap ? '+' : ''} {m.home.abbr}</strong>
              <span>{pct(hover.p, 2)} of simulations</span>
              <small>about 1 in {Math.max(1, Math.round(1 / hover.p)).toLocaleString()} games</small>
            </>
          ) : (
            <>
              <strong>Hover a square</strong>
              <span>Brighter = more likely.</span>
              <small><i className="sw sw--home" /> {m.home.abbr} wins <i className="sw sw--away" /> {m.away.abbr} wins{final ? <> <i className="sw sw--final" /> actual final</> : null}</small>
            </>
          )}
        </div>
      </div>
      <p className="sim-note"><Info size={12} /> Squares above the diagonal are {m.home.abbr} wins, below are {m.away.abbr} wins.</p>
    </section>
  )
}
