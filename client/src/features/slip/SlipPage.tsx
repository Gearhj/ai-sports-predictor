/**
 * MY PICKS: the play-money bet slip (step 5 of Tonight's Game Plan).
 *  - each pick: the price you saw vs the best price now, the model's chance, the market's chance, the
 *    break-even chance at your price, the edge, expected value, a quarter-Kelly play stake, and lock / remove;
 *  - 10,000 NIGHTS: your whole slip played out ten thousand times, animated, under the model's numbers and
 *    under the market's numbers, so you see how much of the upside depends on the model being right;
 *  - the parlay check, and your play record once games go final.
 * PLAY MONEY ONLY: no cash value, stored in this browser, never sent anywhere, separate from the AISP paper
 * record. Prices are sportsbook quotes AISP observed; nothing here places or suggests a real-money wager.
 */
import { AispSide } from '../flow/AispSide'
import { Crest, cfbColor } from '../flow/Crest'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { TeamLogo } from '../../components/TeamLogo'
import { type SportCode } from '../../services/commandCenter'
import { operationalToday } from '../../services/operationalDate'
import { playBankroll, removePick, setPlayBankroll, updatePick, usePreviewPicks, type PreviewPick } from '../../services/previewPicks'
import { american, etClock, pct, usd, usePulse, type PulseGame } from '../pulse/pulseData'
import { GamePlanBar } from '../flow/GamePlanBar'
import { addToSlip, breakEven, decimalOf, evPerDollar, pickOf, simulate, suggestStake, type SimResult } from '../flow/slip'
import '../slate/slate.css'
import './slip.css'

const NAV = [{ to: '/', label: 'Home', icon: '⌂' }, { to: '/live', label: 'Live View', icon: '◉' }, { to: '/games', label: 'Games', icon: '▦' }, { to: '/mission-control', label: 'Mission Control', icon: '◈' }, { to: '/game-night', label: 'Game Night', icon: '▣' }, { to: '/my-picks', label: 'My Picks', icon: '✓', on: true }, { to: '/experiment', label: 'Record', icon: '◎' }]
const hueOf = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)
const sgn = (v: number | null, d = 1, suf = '') => (v == null || !Number.isFinite(v) ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}${suf}`)
const money = (v: number) => `${v >= 0 ? '+' : '-'}$${Math.abs(v).toFixed(2)}`

type Leg = { pick: PreviewPick; g: PulseGame | null; p: number | null; market: number | null; price: number | null; nowPrice: number | null; stake: number; suggested: number; result: 'WIN' | 'LOSS' | null; live: boolean }

export function SlipPage() {
  const today = operationalToday()
  const pulse = usePulse(today)
  const picks = usePreviewPicks()
  const navigate = useNavigate()
  const [bank, setBank] = useState(playBankroll())
  useEffect(() => setBank(playBankroll()), [picks])
  const gameById = useMemo(() => new Map(pulse.games.map((g) => [g.id, g])), [pulse.games])
  const legs: Leg[] = useMemo(() => picks.map((pk) => {
    const g = gameById.get(pk.game_id) ?? null
    const p = pk.model_home_two_way == null ? null : pk.side === 'HOME' ? pk.model_home_two_way : 1 - pk.model_home_two_way
    const best = g ? (pk.side === 'HOME' ? g.bestHome : g.bestAway) : null
    const price = pk.price_american ?? best?.american ?? null
    const suggested = p != null && price != null ? suggestStake(p, price, bank) : 0
    const fin = g?.phase === 'FINAL' && g.score ? g.score : null
    const result = fin ? ((pk.side === 'HOME' ? fin.home > fin.away : fin.away > fin.home) ? 'WIN' : 'LOSS') : null
    return { pick: pk, g, p, market: pk.market_prob ?? null, price, nowPrice: best?.american ?? null, stake: pk.stake ?? 0, suggested, result, live: Boolean(g && (g.phase === 'LIVE' || g.phase === 'STARTED')) }
  }), [picks, gameById, bank])
  const pending = legs.filter((l) => !l.result)
  const settled = legs.filter((l) => l.result && l.pick.status === 'locked')
  const simLegs = pending.filter((l) => l.p != null && l.price != null).map((l) => ({ p: l.p!, stake: l.stake || l.suggested || 10, american: l.price! }))
  const mktLegs = pending.filter((l) => l.p != null && l.price != null).map((l) => ({ p: l.market ?? breakEven(l.price!), stake: l.stake || l.suggested || 10, american: l.price! }))
  const simModel = useMemo(() => (simLegs.length ? simulate(simLegs) : null), [JSON.stringify(simLegs)]) // eslint-disable-line react-hooks/exhaustive-deps
  const simMarket = useMemo(() => (mktLegs.length ? simulate(mktLegs) : null), [JSON.stringify(mktLegs)]) // eslint-disable-line react-hooks/exhaustive-deps
  const totalStake = simLegs.reduce((t, l) => t + l.stake, 0)
  const parlay = simLegs.length >= 2 ? { dec: simLegs.reduce((t, l) => t * decimalOf(l.american), 1), p: simLegs.reduce((t, l) => t * l.p, 1) } : null
  const sameGame = new Set(pending.map((l) => l.pick.game_id)).size < pending.length
  const record = { w: settled.filter((l) => l.result === 'WIN').length, l: settled.filter((l) => l.result === 'LOSS').length, pl: settled.reduce((t, l) => t + (l.result === 'WIN' ? l.stake * (decimalOf(l.price ?? 100) - 1) : -l.stake), 0) }
  const suggestions = useMemo(() => pulse.games.filter((g) => g.day === today && g.phase === 'UPCOMING' && !picks.some((p) => p.game_id === g.id)).map((g) => ({ g, p: pickOf(g) })).filter((x) => (x.p?.edge ?? -1) >= 3 && x.p?.price != null).sort((a, b) => (b.p!.edge ?? 0) - (a.p!.edge ?? 0)).slice(0, 4), [pulse.games, picks, today])

  return (
    <div className="hq sp">
      <AispSide />
      <div className="hq-main">
        <GamePlanBar current="picks" />
        <div className="sp-layout">
          <main className="sp-content">
            <header className="sp-head">
              <i aria-hidden="true">✓</i>
              <div><h1>My Picks</h1><p>Your bet slip. See the price, the edge and the risk before you lock it in. <b>Play money</b>, separate from the AISP paper record.</p></div>
              <label className="sp-bank">Play bankroll<span>$<input type="number" min={10} step={50} value={bank} onChange={(e) => { const v = Number(e.target.value); if (Number.isFinite(v) && v >= 10) { setBank(v); setPlayBankroll(v) } }} /></span></label>
            </header>

            {!picks.length ? (
              <section className="sl-card sp-empty">
                <h2>Your slip is empty</h2>
                <p>Add picks from the <Link to="/games">Games</Link> page, or start with tonight&apos;s strongest model edges:</p>
                <ul className="sp-suggest">{suggestions.map(({ g, p }) => <li key={g.id}><button type="button" onClick={() => addToSlip(g)}><Crest sport={g.sport} abbr={p!.side === 'HOME' ? g.home.abbr : g.away.abbr} size={26} /><span><b>{p!.side === 'HOME' ? g.home.abbr : g.away.abbr} {american(p!.price)}</b><small>{g.away.abbr} @ {g.home.abbr} · {etClock(g.startsAt)} ET · edge {sgn(p!.edge, 1, ' pts')}</small></span><em>+ Add</em></button></li>)}{!suggestions.length ? <li className="sl-empty">No upcoming game has a 3-point edge with a fresh price right now.</li> : null}</ul>
              </section>
            ) : (
              <section className="sp-legs" aria-label="Your slip">
                {legs.map((l) => <LegCard key={l.pick.id} l={l} bank={bank} onOpen={() => navigate(l.pick.href)} />)}
              </section>
            )}

            {simModel && simMarket ? <Nights model={simModel} market={simMarket} stake={totalStake} legs={simLegs.length} /> : picks.length ? <section className="sl-card"><p className="sl-empty">The simulator needs at least one pick with a model forecast and a price.</p></section> : null}

            {parlay ? (
              <section className="sl-card sp-parlay" aria-label="Parlay check">
                <header className="sl-card__head"><h2>🔗 Parlay check</h2><small className="rc-sub">the same picks as one ticket</small></header>
                <div className="sp-parlay__grid">
                  <span><small>Parlay price</small><b>{american(Math.round(parlay.dec >= 2 ? (parlay.dec - 1) * 100 : -100 / (parlay.dec - 1)))}</b><em>{parlay.dec.toFixed(2)}× decimal</em></span>
                  <span><small>$10 pays</small><b>${(10 * parlay.dec).toFixed(2)}</b><em>if every leg wins</em></span>
                  <span><small>Model: all win</small><b>{pct(parlay.p, 1)}</b><em>break-even {pct(1 / parlay.dec, 1)}</em></span>
                  <span><small>Verdict</small><b className={parlay.p > 1 / parlay.dec ? 'is-up' : 'is-down'}>{parlay.p > 1 / parlay.dec ? 'Model edge' : 'No edge'}</b><em>{sgn((parlay.p * parlay.dec - 1) * 100, 1, '% EV')}</em></span>
                </div>
                {sameGame ? <p className="sp-warn">Two legs are from the same game: their results move together, so this math overstates the parlay.</p> : <p className="rc-tiny">Assumes the legs are independent. Picks kicking off together in the same sport and conference tend to move together, which makes real swings bigger.</p>}
              </section>
            ) : null}

            {settled.length ? (
              <section className="sl-card sp-record"><header className="sl-card__head"><h2>Your play record</h2></header><p><b>{record.w}-{record.l}</b> · <span className={record.pl >= 0 ? 'is-up' : 'is-down'}>{money(record.pl)}</span> play money on locked picks that have gone final.</p></section>
            ) : null}
            <footer className="hq-foot">Play money only: no cash value, stored in this browser, never sent anywhere, never placed with a sportsbook. Prices are quotes AISP observed; the model and market numbers are estimates. Separate from the AISP $5,000 paper record.</footer>
          </main>
          <aside className="sp-rail">
            <section className="sl-card">
              <header className="sl-card__head"><h2>Slip summary</h2></header>
              <div className="sl-mini">
                <span className="tone-violet"><i>✓</i><b>{pending.length}</b><small>Open picks</small></span>
                <span className="tone-gold"><i>$</i><b>{usd(totalStake)}</b><small>Play stake</small></span>
                <span className="tone-green"><i>↗</i><b>{simModel ? money(simModel.mean) : '—'}</b><small>Expected (model)</small></span>
                <span className="tone-teal"><i>%</i><b>{simModel ? pct(simModel.pProfit) : '—'}</b><small>Nights in profit</small></span>
              </div>
              <p className="rc-tiny">Blank stakes count as the suggested play stake (quarter Kelly on {usd(bank)}, capped at 5%), or $10 when the model sees no edge.</p>
            </section>
            {suggestions.length && picks.length ? (
              <section className="sl-card"><header className="sl-card__head"><h2>Strongest edges not on your slip</h2></header>
                <ul className="sp-suggest">{suggestions.map(({ g, p }) => <li key={g.id}><button type="button" onClick={() => addToSlip(g)}><Crest sport={g.sport} abbr={p!.side === 'HOME' ? g.home.abbr : g.away.abbr} size={22} /><span><b>{p!.side === 'HOME' ? g.home.abbr : g.away.abbr} {american(p!.price)}</b><small>edge {sgn(p!.edge, 1, ' pts')} · {etClock(g.startsAt)} ET</small></span><em>+</em></button></li>)}</ul>
              </section>
            ) : null}
          </aside>
        </div>
      </div>
    </div>
  )
}

function LegCard({ l, bank, onOpen }: { l: Leg; bank: number; onOpen: () => void }) {
  const { pick } = l
  const locked = pick.status === 'locked'
  const be = l.price != null ? breakEven(l.price) : null
  const edge = l.p != null && be != null ? (l.p - be) * 100 : null
  const ev = l.p != null && l.price != null ? evPerDollar(l.p, l.price) : null
  const stake = l.stake
  const eff = stake || l.suggested || 10
  const toWin = l.price != null ? eff * (decimalOf(l.price) - 1) : 0
  const moved = l.nowPrice != null && l.price != null && l.nowPrice !== l.price ? l.nowPrice - l.price : 0
  const state = l.result ? l.result : l.live ? 'LIVE' : 'PREGAME'
  return (
    <article className={`sl-card sp-leg${locked ? ' is-locked' : ''} is-${state.toLowerCase()}`} style={{ ['--edge' as string]: edge != null && edge > 0 ? '#34d399' : '#f87171' } as CSSProperties}>
      <header className="sp-leg__head">
        <Crest sport={pick.sport} abbr={pick.selection} size={34} />
        <span className="sp-leg__who"><b>{pick.selection} ML {l.price != null ? american(l.price) : ''}</b><small>{pick.sport} · {pick.matchup} · {l.g ? (l.live ? `LIVE ${l.g.score ? `${l.g.score.away}-${l.g.score.home}` : ''}` : l.g.phase === 'FINAL' ? `Final ${l.g.score ? `${l.g.score.away}-${l.g.score.home}` : ''}` : `${etClock(l.g.startsAt, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET`) : 'game not on today’s board'}</small></span>
        <span className={`sp-leg__state is-${state.toLowerCase()}`}>{l.result === 'WIN' ? `WON ${money(toWin)}` : l.result === 'LOSS' ? `LOST ${money(-stake)}` : locked ? '🔒 LOCKED' : 'ON SLIP'}</span>
      </header>
      <div className="sp-leg__nums">
        <span><small>Model chance</small><b>{l.p == null ? '—' : pct(l.p)}</b></span>
        <span><small>Market chance</small><b>{l.market == null ? '—' : pct(l.market)}</b></span>
        <span><small>Break-even at {l.price != null ? american(l.price) : '—'}</small><b>{be == null ? '—' : pct(be)}</b></span>
        <span><small>Edge</small><b className={(edge ?? 0) > 0 ? 'is-up' : 'is-down'}>{sgn(edge, 1, ' pts')}</b></span>
        <span><small>Expected value</small><b className={(ev ?? 0) > 0 ? 'is-up' : 'is-down'}>{ev == null ? '—' : `${money(ev * 10)} / $10`}</b></span>
        <span><small>Price now</small><b>{l.nowPrice != null ? american(l.nowPrice) : '—'}</b><em className={moved > 0 ? 'is-up' : moved < 0 ? 'is-down' : ''}>{moved ? `${moved > 0 ? '▲' : '▼'} moved ${Math.abs(moved)}` : l.price != null ? 'unchanged' : ''}</em></span>
      </div>
      <div className="sp-leg__bar" aria-hidden="true"><i style={{ left: `${(be ?? 0) * 100}%` }} className="be" /><i style={{ left: `${(l.market ?? 0) * 100}%` }} className="mk" /><i style={{ left: `${(l.p ?? 0) * 100}%` }} className="md" /><u style={{ left: `${Math.min(be ?? 0, l.p ?? 0) * 100}%`, width: `${Math.abs((l.p ?? 0) - (be ?? 0)) * 100}%` }} /></div>
      <p className="sp-leg__key"><span className="md">● model</span><span className="mk">● market</span><span className="be">| break-even</span>{edge != null ? <em>{edge > 0 ? `The model needs ${pick.selection} to win ${pct(be)} of the time to profit; it says ${pct(l.p)}.` : `At ${american(l.price)} the model sees no edge: it gives ${pick.selection} ${pct(l.p)}, below the ${pct(be)} break-even.`}</em> : null}</p>
      <footer className="sp-leg__foot">
        <label className="sp-stake">Play stake $<input type="number" min={0} step={1} value={stake || ''} placeholder={String(l.suggested || 10)} disabled={locked} onChange={(e) => updatePick(pick.game_id, { stake: Math.max(0, Number(e.target.value) || 0) })} /></label>
        {!locked ? <button type="button" className="sp-chipbtn" onClick={() => updatePick(pick.game_id, { stake: l.suggested || 10 })} title={`Quarter Kelly on a ${usd(bank)} play bankroll, capped at 5%`}>¼ Kelly {usd(l.suggested || 0)}</button> : null}
        <span className="sp-towin">To win <b>{money(toWin)}</b>{!stake ? <small> on the suggested ${eff}</small> : null}</span>
        <span className="sp-acts">
          <button type="button" onClick={onOpen}>Game →</button>
          {!locked && !l.result ? <button type="button" className="is-go" disabled={!stake} onClick={() => updatePick(pick.game_id, { status: 'locked', locked_at: new Date().toISOString(), price_american: l.nowPrice ?? l.price })}>🔒 Lock pick</button> : null}
          {locked && !l.result && !l.live ? <button type="button" onClick={() => updatePick(pick.game_id, { status: 'slip', locked_at: null })}>Unlock</button> : null}
          <button type="button" className="is-x" onClick={() => removePick(pick.game_id)} aria-label="Remove">✕</button>
        </span>
      </footer>
    </article>
  )
}

/** 10,000 NIGHTS: the slip played out ten thousand times, the histogram filling in as the nights run. */
function Nights({ model, market, stake, legs }: { model: SimResult; market: SimResult; stake: number; legs: number }) {
  const [mode, setMode] = useState<'model' | 'market'>('model')
  const sim = mode === 'model' ? model : market
  const [shown, setShown] = useState(0)
  const raf = useRef(0)
  useEffect(() => {
    setShown(0)
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce) { setShown(sim.n); return undefined }
    let k = 0
    const step = () => { k = Math.min(sim.n, k + 90); setShown(k); if (k < sim.n) raf.current = requestAnimationFrame(step) }
    raf.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf.current)
  }, [sim])
  const lo = Math.min(model.worst, market.worst); const hi = Math.max(model.best, market.best)
  const BINS = 44
  const w = (hi - lo) / BINS || 1
  const bins = useMemo(() => { const b = new Array(BINS).fill(0); for (let i = 0; i < shown; i += 1) b[Math.min(BINS - 1, Math.floor((sim.pl[i] - lo) / w))] += 1; return b }, [shown, sim, lo, w])
  const full = useMemo(() => { const b = new Array(BINS).fill(0); for (let i = 0; i < sim.n; i += 1) b[Math.min(BINS - 1, Math.floor((sim.pl[i] - lo) / w))] += 1; return b }, [sim, lo, w])
  const maxB = Math.max(1, ...full)
  const W = 900; const H = 260; const L = 40; const B = 30; const T = 14
  const X = (v: number) => L + ((v - lo) / (hi - lo || 1)) * (W - L - 10)
  const bw = (W - L - 10) / BINS
  const live = shown < sim.n
  // few legs -> few possible results: draw one bar per result (an outcome ladder) instead of a histogram
  const ladder = useMemo(() => { const set = new Set<number>(); for (const r of [model, market]) { for (let i = 0; i < r.n; i += 1) { set.add(Math.round(r.pl[i] * 100) / 100); if (set.size > 24) return null } } return [...set].sort((a, b) => a - b) }, [model, market])
  const counts = useMemo(() => { if (!ladder) return []; const idx = new Map(ladder.map((v, i) => [v, i])); const c = new Array(ladder.length).fill(0); for (let i = 0; i < shown; i += 1) c[idx.get(Math.round(sim.pl[i] * 100) / 100) ?? 0] += 1; return c }, [ladder, shown, sim])
  const maxC = useMemo(() => { if (!ladder) return 1; const idx = new Map(ladder.map((v, i) => [v, i])); let m = 1; for (const r of [model, market]) { const c = new Array(ladder.length).fill(0); for (let i = 0; i < r.n; i += 1) c[idx.get(Math.round(r.pl[i] * 100) / 100) ?? 0] += 1; m = Math.max(m, ...c) } return m }, [ladder, model, market])
  const cw = ladder ? (W - L - 10) / ladder.length : 0
  const medV = ladder ? ladder.reduce((best, v) => (Math.abs(v - sim.p50) < Math.abs(best - sim.p50) ? v : best), ladder[0]) : null
  const zi = ladder ? ladder.findIndex((v) => v >= 0) : -1
  const zeroAt = ladder && zi > 0 ? L + zi * cw : null
  const drop = model.pProfit - market.pProfit
  return (
    <section className="sl-card sp-nights" id="nights" aria-label="Ten thousand nights">
      <header className="sl-card__head">
        <h2>🌌 10,000 Nights</h2><small className="rc-sub">your slip, played out ten thousand times</small>
        <span className="sl-wtabs sp-mode"><button type="button" className={mode === 'model' ? 'is-on' : ''} onClick={() => setMode('model')}>If AISP is right</button><button type="button" className={mode === 'market' ? 'is-on' : ''} onClick={() => setMode('market')}>If the market is right</button></span>
      </header>
      <div className="sp-nights__body">
        <div className="sp-nights__chart">
          {ladder ? (
            <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Every possible result of the slip and how often it happened over ten thousand simulated nights">
              {ladder.map((v, i) => {
                const c = counts[i]; const h = (c / maxC) * (H - T - B - 18); const x = L + i * cw + cw * 0.12; const bwid = cw * 0.76
                const isMed = v === medV
                return (
                  <g key={v}>
                    <rect x={x} y={H - B - h} width={bwid} height={Math.max(0, h)} rx="5" fill={v >= 0 ? '#34d399' : '#f87171'} opacity={0.88} stroke={isMed && !live ? '#fbbf24' : 'none'} strokeWidth="3" />
                    {c ? <text x={x + bwid / 2} y={H - B - h - 6} textAnchor="middle" className="sp-ax-pct">{((c / Math.max(1, shown)) * 100).toFixed(c / Math.max(1, shown) < 0.1 ? 1 : 0)}%</text> : null}
                    <text x={x + bwid / 2} y={H - 10} textAnchor="middle" className={`rc-ax${isMed ? ' sp-ax-goldtxt' : ''}`}>{money(v)}</text>
                  </g>
                )
              })}
              {zeroAt != null ? <line x1={zeroAt} x2={zeroAt} y1={T} y2={H - B} stroke="rgba(255,255,255,.5)" strokeDasharray="4 4" /> : null}
              {zeroAt != null ? <text x={zeroAt} y={T - 2} textAnchor="middle" className="rc-ax">break even</text> : null}
            </svg>
          ) : (
            <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Distribution of the slip's profit over ten thousand simulated nights">
              <line x1={X(0)} x2={X(0)} y1={T} y2={H - B} stroke="rgba(255,255,255,.55)" strokeDasharray="4 4" />
              <text x={X(0)} y={T - 2} textAnchor="middle" className="rc-ax">break even</text>
              {bins.map((c, i) => { const x0 = lo + i * w; const h = (c / maxB) * (H - T - B); return <rect key={i} x={L + i * bw + 1} y={H - B - h} width={Math.max(1, bw - 2)} height={h} rx="2" fill={x0 + w / 2 >= 0 ? '#34d399' : '#f87171'} opacity={0.85} /> })}
              {!live ? <line x1={X(sim.p50)} x2={X(sim.p50)} y1={T + 10} y2={H - B} stroke="#fbbf24" strokeWidth="2" /> : null}
              {[lo, (lo + hi) / 2, hi].map((v) => <text key={v} x={X(v)} y={H - 10} textAnchor="middle" className="rc-ax">{money(v)}</text>)}
            </svg>
          )}
          <p className="sp-nights__counter">{live ? <>Simulating night <b>{shown.toLocaleString()}</b> of 10,000…</> : <>10,000 nights · {legs} pick{legs > 1 ? 's' : ''} · {usd(stake)} play stake · numbers {mode === 'model' ? "from AISP's model" : "from the market's no-vig prices"}</>}</p>
        </div>
        <div className="sp-nights__stats">
          <span className="big"><small>Nights you finish ahead</small><b className={sim.pProfit >= 0.5 ? 'is-up' : 'is-down'}>{pct(sim.pProfit)}</b></span>
          <span><small>Average night</small><b className={sim.mean >= 0 ? 'is-up' : 'is-down'}>{money(sim.mean)}</b></span>
          <span><small>9 nights in 10</small><b>{money(sim.p05)} to {money(sim.p95)}</b></span>
          <span><small>Best · worst night</small><b><span className="is-up">{money(sim.best)}</span> · <span className="is-down">{money(sim.worst)}</span></b></span>
          <span><small>Every pick loses</small><b>{pct(sim.allLose, 1)}</b></span>
        </div>
      </div>
      <p className="sp-nights__verdict">{drop > 0.005
        ? <>If AISP&apos;s numbers are right you finish ahead on <b>{pct(model.pProfit)}</b> of nights (average {money(model.mean)}). If the market is right, only <b>{pct(market.pProfit)}</b> (average {money(market.mean)}). That gap of <b>{Math.round(drop * 100)} points</b> is the model&apos;s edge: it pays only if the model is right and the market is wrong, which is exactly the bet this slip makes.</>
        : <>The model and the market roughly agree on this slip ({pct(model.pProfit)} vs {pct(market.pProfit)} winning nights): there is little model edge here, so the outcome is mostly the price.</>}</p>
    </section>
  )
}
