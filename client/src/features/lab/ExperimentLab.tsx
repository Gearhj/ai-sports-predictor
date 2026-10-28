/**
 * BUILD YOUR MODEL (Game Night's Monte Carlo lab; owner 10-26 / 10-27: "this is where users can develop their own
 * model ... put some power into the user's hands ... give them a button to implement their newly developed model").
 *
 * Owner 10-27 ("the selections should be sport specific and reflect aspects of the game"): THE MATCHUP comes first --
 * the reads a handicapper makes about this one game in its sport's units (QB edge, goaltending, starting pitchers,
 * rest, weather, pace...), next to tonight's real players, with your projected final score. Then YOUR RULES, five
 * decisions per sport (myModel.ts): how much to trust AISP over the market, how confident favourites deserve to
 * be, home edge, what a key player out is worth (the game's real key players are filled in from AISP's every-sport
 * availability board), and how chaotic results are. The game on the stage above previews it: your number against
 * AISP and the market, your 10,000 games replayed on the score grid, a margin histogram paired with box plots, your
 * fair price and value at the best US book. SAVE AS MY MODEL keeps it per sport; APPLY TO TONIGHT scores every game
 * of that sport with it and lists your model's plays with + My Picks. A personal tool, never AISP's production model.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { Crest } from '../flow/Crest'
import { addPick, usePreviewPicks } from '../../services/previewPicks'
import { useMarketCoverage } from '../../services/commandCenter'
import { operationalToday } from '../../services/operationalDate'
import { american, etClock, pct, usePulse, type PulseGame } from '../pulse/pulseData'
import { stormFromUser, type StormSource } from '../gamenight/gameNightData'
import { availabilityFor, useAvailability, type AvailGame } from '../focus/FocusExtras'
import { KIND, LEAGUE, defaults, evOf, fairAmerican, projectedScore, quantiles, simulate, splitTotal, tallyOf, type Inputs } from './lab'
import { MODEL_EVENT, READS, READS_EVENT, UNIT, applyModel, defaultsFor, loadModel, loadReads, ranges, saveModel, saveReads, type Absence, type Factors, type MyModel, type ReadDef, type Reads } from './myModel'
import './lab.css'

const COLOR: Record<string, string> = { NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }
const US = /draftkings|fanduel|betmgm|caesars|william ?hill|betrivers|espn ?bet|fanatics|hard ?rock|bally|betparx|bet365/i
type CovQuote = { market?: string; sportsbook: string; fresh?: boolean; sides?: Record<string, { american?: number | null; decimal?: number | null }> }
type CovRow = { game_id: string; quotes?: CovQuote[] }
/** Best fresh US-regulated moneyline price per side (owner rule: picks only at US-regulated books). */
function bestUs(row: CovRow | undefined, side: 'HOME' | 'AWAY'): { american: number; book: string } | null {
  let best: { american: number; book: string; dec: number } | null = null
  for (const q of row?.quotes ?? []) {
    const s = q.sides?.[side]
    if (q.market !== 'MONEYLINE' || q.fresh === false || !US.test(q.sportsbook) || s?.american == null || !s.decimal) continue
    if (!best || s.decimal > best.dec) best = { american: s.american, book: q.sportsbook, dec: s.decimal }
  }
  return best ? { american: best.american, book: best.book } : null
}
/** The key players listed out / doubtful / questionable for a game, by side. */
function absencesOf(a: AvailGame | null): Absence[] {
  if (!a) return []
  const out: Absence[] = []
  for (const [side, t] of Object.entries(a.teams)) for (const k of t.key_players) if (['OUT', 'DOUBTFUL', 'QUESTIONABLE'].includes(k.status)) out.push({ side: side === 'home' ? 'HOME' : 'AWAY', status: k.status, name: `${t.abbreviation} ${k.name}` })
  return out
}
/** The score-shape inputs that reproduce a chance (for the 10,000 games), with chaos widening the spread and your
 * total reads (weather, pace, park...) moving the expected total. */
function shapeFor(sport: string, p: number, chaos: number, totalShift = 0): Inputs {
  const d = defaults(sport, p)
  if (d.kind === 'GOALS') { const s = splitTotal(Math.max(1, (LEAGUE[sport]?.total ?? 6) * chaos + totalShift), p); return { ...d, home: s.home, away: s.away } }
  return { ...d, total: d.total + totalShift, sigma: d.sigma * chaos, mu: d.sigma * chaos * (d.mu / d.sigma) }
}
const projected = (sport: string, p: number, chaos: number, totalShift: number) => projectedScore(sport, p, chaos, totalShift)
/** The players a matchup read is about, from AISP's availability board (QB1, starting goalie, probable starters...). */
function factOf(def: ReadDef, a: AvailGame | null): { team: string; names: string }[] | null {
  if (!def.fact || !a) return null
  const pickRole = (role: string, i: number) => def.fact === 'qb' ? /QB/i.test(role) : def.fact === 'sp' ? /SP|pitcher/i.test(role)
    : def.fact === 'goalie' ? /G1|goal|start/i.test(role) : def.fact === 'scorers' ? !/G1|goal/i.test(role) : i < 2
  const rows = (['away', 'home'] as const).map((k) => {
    const t = a.teams[k]
    const names = (t?.key_players ?? []).filter((x, i) => pickRole(x.role ?? '', i)).map((x) => `${x.name}${x.status && x.status !== 'AVAILABLE' ? ` (${x.status})` : ''}`).join(', ')
    return { team: t?.abbreviation ?? k, names }
  })
  return rows.some((r) => r.names) ? rows : null
}

interface LabProps {
  /** The game on the stage above (the reel picks it); null = the first eligible game. */
  focusId?: string | null
  /** Ask the stage to focus another game (the chips shown when the focused game can't be modelled). */
  onFocus?: (id: string) => void
  /** Your 10,000 as a storm source once you change an input; null when the inputs are AISP's. */
  onUser?: (source: StormSource | null, key: string) => void
}

export function ExperimentLab({ focusId = null, onFocus, onUser }: LabProps = {}) {
  const today = operationalToday()
  const pulse = usePulse(today)
  const slip = usePreviewPicks()
  const cov = useMarketCoverage(today)
  const avail = useAvailability(today)
  const board = avail.data?.games
  const games = useMemo(() => pulse.games.filter((g) => g.day === today && g.phase !== 'FINAL' && g.modelHome != null && KIND[g.sport]), [pulse.games, today])
  const [gid, setGid] = useState<string | null>(null)
  const focused = focusId ? games.find((x) => x.id === focusId) ?? null : null
  const g = focusId ? focused : games.find((x) => x.id === gid) ?? games[0] ?? null
  const sport = g?.sport ?? 'NBA'
  const [saved, setSaved] = useState<MyModel | null>(loadModel)
  useEffect(() => { const on = () => setSaved(loadModel()); window.addEventListener(MODEL_EVENT, on); return () => window.removeEventListener(MODEL_EVENT, on) }, [])
  const base = useMemo(() => defaultsFor(sport), [sport])
  const [f, setF] = useState<Factors>(base)
  useEffect(() => { setF(saved?.sports[sport] ?? defaultsFor(sport)) }, [sport]) // eslint-disable-line react-hooks/exhaustive-deps
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [reads, setReadsState] = useState<Reads>({})
  useEffect(() => {
    if (!g) { setReadsState({}); return undefined }
    setReadsState(loadReads()[g.id] ?? {})
    // the picker beside the grid writes the same reads; follow it
    const on = (e: Event) => { if ((e as CustomEvent).detail === g.id) setReadsState(loadReads()[g.id] ?? {}) }
    window.addEventListener(READS_EVENT, on)
    return () => window.removeEventListener(READS_EVENT, on)
  }, [g?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const [applied, setApplied] = useState(false)
  const a = g ? availabilityFor(g, board) : null
  const absences = absencesOf(a)
  const used = absences.filter((x) => !excluded.has(x.name))
  const market = g?.marketFresh ? g.marketHome : null
  const mine = useMemo(() => (g ? applyModel(sport, f, g.modelHome!, market, used, reads) : null), [g?.id, f, market, used.map((x) => x.name + x.status).join('|'), reads]) // eslint-disable-line react-hooks/exhaustive-deps
  const sim = useMemo(() => (mine ? simulate(shapeFor(sport, mine.p, f.chaos, mine.totalShift)) : null), [mine, sport, f.chaos])
  const aispSim = useMemo(() => (g ? simulate(defaults(sport, g.modelHome!)) : null), [g?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const mktSim = useMemo(() => (g && market != null ? simulate(defaults(sport, market)) : null), [g?.id, market]) // eslint-disable-line react-hooks/exhaustive-deps
  const changed = JSON.stringify(f) !== JSON.stringify(base) || used.length > 0 || Object.values(reads).some((v) => v)
  // Your 10,000 go to the stage (debounced while a slider is dragged); AISP's inputs hand the stage back.
  useEffect(() => {
    if (!onUser) return undefined
    if (!g || !sim || !changed) { onUser(null, ''); return undefined }
    const t = window.setTimeout(() => onUser(stormFromUser(g.id, g.sport, { abbr: g.away.abbr, name: g.away.name ?? g.away.abbr }, { abbr: g.home.abbr, name: g.home.name ?? g.home.abbr },
      tallyOf(sim), sim.pHome), JSON.stringify([f, used.map((x) => x.name), reads])), 250)
    return () => window.clearTimeout(t)
  }, [sim, changed]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { onUser?.(null, '') }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (!g || !mine || !sim) return (
    <section className="lb lb--empty" aria-label="Build your model"><header className="lb__head"><h2>BUILD YOUR MODEL</h2>
      <span>{pulse.loading ? 'Loading tonight’s games…' : focusId ? 'The game on the stage has finished or has no AISP forecast yet. Pick a game still to play:' : 'No game left tonight with a model forecast to experiment on.'}</span></header>
      {focusId && onFocus && games.length > 0 && <div className="lb__games">{games.map((v) => <button key={v.id} type="button" style={{ '--sc': COLOR[v.sport] } as CSSProperties} onClick={() => onFocus(v.id)}><b>{v.away.abbr}@{v.home.abbr}</b><small>{v.sport} {etClock(v.startsAt)}</small></button>)}</div>}
    </section>)
  const R = ranges(sport)
  const u = UNIT[sport] ?? 'pts'
  const yours = mine.p, model = g.modelHome!
  const kind = KIND[sport]
  const rows = ((cov.data?.games ?? []) as unknown as CovRow[])
  const sides = (['HOME', 'AWAY'] as const).map((s) => {
    const q = bestUs(rows.find((r) => r.game_id === g.id), s)
    const p = s === 'HOME' ? yours : 1 - yours
    return { s, team: s === 'HOME' ? g.home.abbr : g.away.abbr, p, price: q?.american ?? null, book: q?.book ?? null, ev: q ? evOf(p, q.american) : null }
  })
  const bestSide = sides.filter((v) => v.ev != null).sort((x, y) => (y.ev ?? 0) - (x.ev ?? 0))[0]
  const onSlip = slip.some((p) => p.game_id === g.id)
  const set = (patch: Partial<Factors>) => setF({ ...f, ...patch })
  const isSaved = JSON.stringify(saved?.sports[sport]) === JSON.stringify(f)

  return (
    <section className="lb" aria-label="Build your model">
      <header className="lb__head">
        <h2>BUILD YOUR MODEL</h2>
        <span>{g.away.abbr} @ {g.home.abbr} · read the {sport} matchup the way a handicapper does, then set your rules for every {sport} game. Start from AISP, change what you believe, save it and run it on tonight’s board.</span>
        {changed && <b className="lb__live">● YOUR MODEL IS ON THE GRID</b>}
      </header>
      {!focusId && <div className="lb__games" role="tablist" aria-label="Game">
        {games.map((v) => (
          <button key={v.id} type="button" role="tab" aria-selected={v.id === g.id} className={v.id === g.id ? 'is-on' : ''} style={{ '--sc': COLOR[v.sport] } as CSSProperties} onClick={() => setGid(v.id)}>
            <Crest sport={v.sport} abbr={v.away.abbr} size={18} /><Crest sport={v.sport} abbr={v.home.abbr} size={18} /><b>{v.away.abbr}@{v.home.abbr}</b><small>{v.sport} {etClock(v.startsAt)}</small>
          </button>
        ))}
      </div>}
      <div className="lb__body">
        <div className="lb__inputs">
          {/* Owner 10-27: the matchup sliders live once, in YOUR READ beside the grid. Here: what they are worth. */}
          <h3>WHAT MOVES THIS GAME <small>{g.away.abbr} @ {g.home.abbr} · how far each read can swing the {g.home.abbr} win chance, from the full {g.away.abbr} end to the full {g.home.abbr} end, with your other reads held</small></h3>
          <Tornado sport={sport} f={f} model={g.modelHome!} market={market} out={used} reads={reads} away={g.away.abbr} home={g.home.abbr} facts={(READS[sport] ?? []).map((d) => factOf(d, a))} />
          <button type="button" className="lb__toread" onClick={() => document.querySelector('.qr')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>Set your read beside the grid ↑</button>
          <GameShape sim={sim} sport={sport} away={g.away.abbr} home={g.home.abbr} homeFav={model >= 0.5} />
          {(() => {
            const you = projected(sport, yours, f.chaos, mine.totalShift)
            const ai = projected(sport, model, 1, 0)
            const dp = KIND[sport] === 'GOALS' ? 1 : 0
            return (
              <div className="lb__proj" aria-label="Projected final score">
                <small>YOUR PROJECTED FINAL</small>
                <b><span><small>{g.away.abbr}</small>{you.away.toFixed(dp)}</span><em>–</em><span><small>{g.home.abbr}</small>{you.home.toFixed(dp)}</span></b>
                <small>total {(you.home + you.away).toFixed(1)} · AISP {g.away.abbr} {ai.away.toFixed(dp)}–{ai.home.toFixed(dp)} {g.home.abbr} ({(ai.home + ai.away).toFixed(1)})</small>
              </div>
            )
          })()}
          <div className="lb__inj">
            <b>Injury report · {g.away.abbr} @ {g.home.abbr}</b>
            {sport === 'CFB' && !absences.length ? <small>ESPN publishes no college injury list. Put what you know about the quarterback into Quarterback edge above.</small>
              : !a ? <small>Waiting on the injury report for this game (AISP refreshes every 10 minutes).</small>
              : !absences.length ? <small>✓ Every key player is available on the latest report.</small>
                : absences.map((x) => (
                  <label key={x.name} className="lb__out"><input type="checkbox" checked={!excluded.has(x.name)} onChange={() => setExcluded((s) => { const n = new Set(s); if (n.has(x.name)) n.delete(x.name); else n.add(x.name); return n })} />
                    {x.name} <small>{x.status} · {x.side === 'HOME' ? 'home' : 'away'}</small></label>
                ))}
          </div>
          <details className="lb__rules">
            <summary><b>YOUR RULES · every {sport} game</b><small>{saved?.sports[sport] ? `saved v${saved.version}` : 'not saved yet'} · trust {f.trust.toFixed(2)} · favourites ×{f.confidence.toFixed(2)} · home {f.homeEdge >= 0 ? '+' : ''}{f.homeEdge} · key player {f.keyOut} {u} · chaos {f.chaos.toFixed(2)}</small></summary>
          <Slider label="1 · Trust AISP over the market" value={f.trust} {...R.trust} def={1} onChange={(v) => set({ trust: v })}
            help="0 = take the market's price as the truth, 1 = AISP's number, above 1 = lean even harder into AISP's disagreements." />
          <Slider label="2 · Favourite confidence" value={f.confidence} {...R.confidence} def={1} onChange={(v) => set({ confidence: v })}
            help="Below 1 = favourites win less often than the number says (AISP's own audit found it overconfident); above 1 = more often." />
          <Slider label={`3 · Extra home edge (${u})`} value={f.homeEdge} {...R.homeEdge} def={0} signed onChange={(v) => set({ homeEdge: v })}
            help={`Crowd, travel, altitude: + helps every home team by this many ${u}.`} />
          <Slider label={`4 · A key player out is worth (${u})`} value={f.keyOut} {...R.keyOut} def={base.keyOut} onChange={(v) => set({ keyOut: v })}
            help={`Applied to the real injury report below: OUT counts in full, DOUBTFUL 75%, QUESTIONABLE ${Math.round(f.questionable * 100)}%.`} />
          <Slider label="   Questionable counts as" value={f.questionable} {...R.questionable} def={0.5} onChange={(v) => set({ questionable: v })} help="How likely you think a questionable star sits." />
          <Slider label="5 · Chaos (how wide results spread)" value={f.chaos} {...R.chaos} def={1} onChange={(v) => set({ chaos: v })}
            help="Shapes the 10,000 games and the margins; it never moves the win chance." />
          </details>
          <div className="lb__actions">
            <button type="button" className="lb__save" disabled={isSaved} onClick={() => setSaved(saveModel(sport, f))}>{isSaved ? `✓ Saved as My ${sport} Model` : `💾 Save as My ${sport} Model`}</button>
            <button type="button" className="lb__apply" onClick={() => { if (!isSaved) setSaved(saveModel(sport, f)); setApplied(true); window.setTimeout(() => document.querySelector('.lb-board')?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50) }}>▶ Apply to tonight’s {sport} games</button>
            <button type="button" className="lb__reset" disabled={!changed} onClick={() => { setF(base); setExcluded(new Set()); setReadsState({}); saveReads(g.id, {}) }}>↺ Back to AISP</button>
          </div>
        </div>
        <div className="lb__out-col">
          <div className="lb__three">
            <Bar label="YOUR MODEL" p={yours} team={g.home.abbr} tone="you" />
            <Bar label="AISP MODEL" p={model} team={g.home.abbr} tone="aisp" />
            <Bar label="MARKET" p={market} team={g.home.abbr} tone="mkt" />
          </div>
          <div className="lb__steps">
            {mine.steps.map((s, i) => <span key={i}><small>{s.label}</small><b>{pct(s.p, 1)}</b></span>)}
            <span className="is-final"><small>Your number</small><b>{pct(yours, 1)} {g.home.abbr}</b></span>
          </div>
          <div className="lb__price">
            <span>Your fair price: <b>{g.home.abbr} {american(fairAmerican(yours))}</b> · <b>{g.away.abbr} {american(fairAmerican(1 - yours))}</b></span>
            {sides.map((v) => v.price != null ? <span key={v.s} className={(v.ev ?? 0) > 0 ? 'is-up' : ''}>Best US book · {v.team} {american(v.price)} at {v.book}: {v.ev != null ? `${v.ev >= 0 ? '+' : ''}${(v.ev * 100).toFixed(1)}% by your numbers` : ''}</span> : null)}
            {sides.every((v) => v.price == null) ? <span className="lb__wait">⏳ Waiting on sportsbook lines: AISP buys US-book prices about 75 minutes before the start ({etClock(new Date(Date.parse(g.startsAt) - 75 * 60_000).toISOString())} ET for this game).</span> : null}
            {bestSide && (bestSide.ev ?? 0) > 0 ? (onSlip ? <Link to="/my-picks" className="lb__added">✓ on your slip</Link> : (
              <button type="button" className="lb__add" onClick={() => addPick({ game_id: g.id, sport: g.sport, matchup: `${g.away.abbr} @ ${g.home.abbr}`, away: g.away.abbr, home: g.home.abbr, side: bestSide.s, selection: bestSide.team,
                model_home_two_way: yours, href: `/games/${g.id}`, price_american: bestSide.price, price_book: bestSide.book, price_seen_at: new Date().toISOString(), market_prob: market == null ? null : bestSide.s === 'HOME' ? market : 1 - market, status: 'slip', starts_at: g.startsAt })}>
                + My Picks: {bestSide.team} {american(bestSide.price)} at {bestSide.book}</button>)) : null}
          </div>
          {/* the charts fill the results column beside the inputs instead of sitting under both */}
          <div className="lb__charts lb__charts--stack">
            <Hist margins={sim.margins} home={g.home.abbr} away={g.away.abbr} kind={kind} />
            <BoxPlots rows={[{ key: 'you', label: 'YOU', m: sim.margins }, { key: 'aisp', label: 'AISP', m: aispSim?.margins ?? null }, { key: 'mkt', label: 'MARKET', m: mktSim?.margins ?? null }]} home={g.home.abbr} kind={kind} />
          </div>
          <Waterfall steps={mine.steps} model={model} market={market} team={g.home.abbr} />
          <ModelBoard sport={sport} f={f} games={games.filter((x) => x.sport === sport)} board={board} rows={rows} live={!applied} />
        </div>
      </div>
    </section>
  )
}

/** Your saved model run over every game of one sport tonight: your number, the edge, the best US price, + My Picks. */
export function ModelBoard({ sport, f, games, board, rows, onClose, live = false }: { sport: string; f: Factors; games: PulseGame[]; board: AvailGame[] | undefined; rows: CovRow[]; onClose?: () => void; live?: boolean }) {
  const slip = usePreviewPicks()
  const list = games.filter((g) => g.modelHome != null).map((g) => {
    const market = g.marketFresh ? g.marketHome : null
    const r = applyModel(sport, f, g.modelHome!, market, absencesOf(availabilityFor(g, board)), loadReads()[g.id] ?? null)
    const sides = (['HOME', 'AWAY'] as const).map((s) => { const q = bestUs(rows.find((x) => x.game_id === g.id), s); const p = s === 'HOME' ? r.p : 1 - r.p; return { s, p, q, ev: q ? evOf(p, q.american) : null } })
    const best = sides.filter((v) => v.ev != null).sort((x, y) => (y.ev ?? 0) - (x.ev ?? 0))[0] ?? null
    const lean = r.p >= 0.5 ? 'HOME' : 'AWAY'
    return { g, r, market, best, lean, edge: market == null ? null : ((lean === 'HOME' ? r.p - market : market - r.p) * 100) }
  }).sort((x, y) => (y.best?.ev ?? -1) - (x.best?.ev ?? -1))
  return (
    <div className="lb-board">
      <header><b>{live ? 'Your model across tonight’s' : '▶ Your'} {sport} {live ? 'board' : 'model on tonight’s board'}</b><small>{list.length} game{list.length === 1 ? '' : 's'} · {live ? 'updates as you move a slider (unsaved) · ' : ''}sorted by value at the best US book · your picks stay separate from AISP’s record</small>{onClose && <button type="button" className="lb__reset" onClick={onClose}>Close</button>}</header>
      <table>
        <thead><tr><th>Game</th><th>Your pick</th><th>You</th><th>AISP</th><th>Market</th><th>Best US price</th><th>Value</th><th /></tr></thead>
        <tbody>{list.map(({ g, r, market, best, lean }) => {
          const t = lean === 'HOME' ? g.home.abbr : g.away.abbr
          const yp = lean === 'HOME' ? r.p : 1 - r.p
          const on = slip.some((p) => p.game_id === g.id)
          return (
            <tr key={g.id}>
              <td>{g.away.abbr} @ {g.home.abbr}<small>{etClock(g.startsAt)}</small></td>
              <td><b>{t}</b>{r.injuryUnits ? <small>injuries {r.injuryUnits > 0 ? '+' : ''}{r.injuryUnits.toFixed(1)}</small> : null}{r.readUnits ? <small>your read {r.readUnits > 0 ? '+' : ''}{r.readUnits.toFixed(1)}</small> : null}</td>
              <td className="is-you">{pct(yp, 1)}</td>
              <td>{pct(lean === 'HOME' ? g.modelHome! : 1 - g.modelHome!, 1)}</td>
              <td>{market == null ? '—' : pct(lean === 'HOME' ? market : 1 - market, 1)}</td>
              <td>{best?.q ? `${best.s === 'HOME' ? g.home.abbr : g.away.abbr} ${american(best.q.american)}` : <small>⏳ T-75</small>}<small>{best?.q?.book ?? ''}</small></td>
              <td className={(best?.ev ?? 0) > 0 ? 'is-up' : ''}>{best?.ev == null ? '—' : `${best.ev >= 0 ? '+' : ''}${(best.ev * 100).toFixed(1)}%`}</td>
              <td>{on ? <span className="lb__added">✓</span> : best?.q && (best.ev ?? 0) > 0 ? (
                <button type="button" className="lb__add" onClick={() => addPick({ game_id: g.id, sport: g.sport, matchup: `${g.away.abbr} @ ${g.home.abbr}`, away: g.away.abbr, home: g.home.abbr, side: best.s, selection: best.s === 'HOME' ? g.home.abbr : g.away.abbr,
                  model_home_two_way: r.p, href: `/games/${g.id}`, price_american: best.q!.american, price_book: best.q!.book, price_seen_at: new Date().toISOString(), market_prob: market == null ? null : best.s === 'HOME' ? market : 1 - market, status: 'slip', starts_at: g.startsAt })}>+ Pick</button>) : null}</td>
            </tr>
          )
        })}</tbody>
      </table>
    </div>
  )
}

function Slider({ label, value, min, max, step, def, onChange, signed, help }: { label: string; value: number; min: number; max: number; step: number; def: number; onChange: (v: number) => void; signed?: boolean; help?: string }) {
  const fmt = (v: number) => `${signed && v > 0 ? '+' : ''}${v.toFixed(step < 0.1 ? 2 : 1)}`
  return (
    <label className="lb__slider">
      <span><b>{label}</b><em>{fmt(value)}</em></span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <small>{help ? `${help} ` : ''}Range {fmt(min)} to {fmt(max)} · AISP {fmt(def)}</small>
    </label>
  )
}

/** How your number is built: each step of your model from AISP's number to yours, on one 0-100% axis. */
function Waterfall({ steps, model, market, team }: { steps: { label: string; p: number }[]; model: number; market: number | null; team: string }) {
  const rows = [{ label: 'AISP model', p: model, from: model }, ...steps.map((s, i) => ({ label: s.label, p: s.p, from: i === 0 ? model : steps[i - 1].p }))]
    .filter((r, i) => i === 0 || !r.label.startsWith('AISP (no market'))
  const lo = Math.max(0, Math.min(...rows.map((r) => Math.min(r.p, r.from)), market ?? 1) - 0.08)
  const hi = Math.min(1, Math.max(...rows.map((r) => Math.max(r.p, r.from)), market ?? 0) + 0.08)
  const x = (p: number) => `${((p - lo) / (hi - lo)) * 100}%`
  return (
    <section className="lb-wf" aria-label="How your number is built">
      <header><b>How your number is built</b><small>{team} win chance, step by step{market != null ? ' · dashed line = the market' : ''}</small></header>
      <ol>
        {rows.map((r, i) => {
          const d = r.p - r.from
          return (
            <li key={i}>
              <span>{r.label}</span>
              <span className="lb-wf__track">
                {market != null && <i className="lb-wf__mkt" style={{ left: x(market) }} />}
                {i === 0 ? <i className="lb-wf__dot" style={{ left: x(r.p) }} /> : <i className={`lb-wf__bar ${d > 0 ? 'is-up' : d < 0 ? 'is-down' : ''}`} style={{ left: x(Math.min(r.p, r.from)), width: `max(3px, ${(Math.abs(d) / (hi - lo)) * 100}%)` }} />}
              </span>
              <b>{pct(r.p, 1)}</b><small>{i === 0 ? '' : `${d >= 0 ? '+' : ''}${(d * 100).toFixed(1)}`}</small>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

/** Sensitivity of the home win chance to each matchup read: bar from its full-away to its full-home value. */
function Tornado({ sport, f, model, market, out, reads, away, home, facts }: { sport: string; f: Factors; model: number; market: number | null; out: Absence[]; reads: Reads; away: string; home: string; facts: ({ team: string; names: string }[] | null)[] }) {
  const defs = READS[sport] ?? []
  const now = applyModel(sport, f, model, market, out, reads).p
  const rows = defs.map((d, i) => {
    const at = (v: number) => applyModel(sport, f, model, market, out, { ...reads, [d.key]: v }).p
    const lo = d.kind === 'margin' ? at(d.min) : now, hi = d.kind === 'margin' ? at(d.max) : now
    return { d, lo: Math.min(lo, hi), hi: Math.max(lo, hi), swing: Math.abs(hi - lo), set: reads[d.key] ?? 0, fact: facts[i] }
  }).sort((a, b) => b.swing - a.swing)
  const min = Math.min(now, ...rows.map((r) => r.lo)) - 0.02, max = Math.max(now, ...rows.map((r) => r.hi)) + 0.02
  const x = (p: number) => `${((p - min) / (max - min)) * 100}%`
  return (
    <ol className="lb-tor">
      {rows.map(({ d, lo, hi, swing, set, fact }) => (
        <li key={d.key} className={set ? 'is-set' : ''}>
          <span className="lb-tor__lab"><b>{d.label}</b>{d.kind === 'total' ? <small>moves the score, not the winner</small> : fact ? <small>{fact.map((r) => `${r.team} ${r.names || '—'}`).join(' · ')}</small> : null}</span>
          <span className="lb-tor__track">
            {d.kind === 'margin' ? <i className="lb-tor__bar" style={{ left: x(lo), width: `calc(${x(hi)} - ${x(lo)})` }}><em>{away}</em><em>{home}</em></i> : null}
            <i className="lb-tor__now" style={{ left: x(now) }} />
          </span>
          <span className="lb-tor__val">{d.kind === 'margin' ? <>±{(swing * 50).toFixed(1)}<small> pts</small></> : <small>total only</small>}</span>
        </li>
      ))}
      <li className="lb-tor__key"><span /><span><i className="lb-tor__now lb-tor__now--key" /> your number now: <b>{pct(now, 1)} {home}</b></span><span /></li>
    </ol>
  )
}

/** The shape of your 10,000: how often it is close, a blowout, level, or an upset. */
function GameShape({ sim, sport, away, home, homeFav }: { sim: { margins: Int16Array } | null; sport: string; away: string; home: string; homeFav: boolean }) {
  if (!sim) return null
  const n = sim.margins.length
  const close = sport === 'NHL' || sport === 'MLB' ? 1 : sport === 'NBA' ? 5 : 8
  const blow = sport === 'NHL' ? 3 : sport === 'MLB' ? 5 : sport === 'NBA' ? 15 : 17
  let c = 0, b = 0, t = 0, u = 0
  for (const m of sim.margins) { const a = Math.abs(m); if (m !== 0 && a <= close) c++; if (a >= blow) b++; if (m === 0) t++; if (homeFav ? m < 0 : m > 0) u++ }
  const word = sport === 'NHL' ? 'one-goal game' : sport === 'MLB' ? 'one-run game' : sport === 'NBA' ? 'within 5' : 'one-score game'
  const tie = sport === 'NHL' ? 'level after 60 (OT/SO)' : sport === 'MLB' ? 'level after 9 (extras)' : 'tie after regulation'
  const tiles: [string, number, string][] = [
    [word, c / n, `|margin| ≤ ${close}`],
    ['blowout', b / n, `by ${blow}+`],
    [tie, t / n, 'margin 0'],
    ['upset', u / n, `${homeFav ? away : home} wins`],
  ]
  return (
    <section className="lb-shape" aria-label="Game shape of your 10,000">
      <header><b>GAME SHAPE</b><small>your 10,000 · {away} @ {home}</small></header>
      <div>{tiles.map(([k, v, h]) => <span key={k}><b>{(v * 100).toFixed(1)}%</b><small>{k}</small><em>{h}</em></span>)}</div>
    </section>
  )
}

function Bar({ label, p, team, tone }: { label: string; p: number | null; team: string; tone: 'you' | 'aisp' | 'mkt' }) {
  return (
    <div className={`lb__bar lb__bar--${tone}`}>
      <small>{label}</small>
      <b>{p == null ? '—' : pct(p, 1)}</b>
      <i><em style={{ width: `${(p ?? 0) * 100}%` }} /></i>
      <small>{team} wins</small>
    </div>
  )
}

/** D3 histogram of the simulated home margin (green = home wins, gold = away wins). */
function Hist({ margins, home, away, kind }: { margins: Int16Array; home: string; away: string; kind: string }) {
  const ref = useRef<SVGSVGElement | null>(null)
  useEffect(() => {
    if (!ref.current) return
    const W = 640, H = 170, M = { l: 34, r: 10, t: 18, b: 26 }
    const span = kind === 'GOALS' ? 8 : 40
    const counts = new Map<number, number>()
    margins.forEach((m) => { const v = Math.max(-span, Math.min(span, m)); counts.set(v, (counts.get(v) ?? 0) + 1) })
    const data = d3.range(-span, span + 1).map((v) => ({ v, n: counts.get(v) ?? 0 }))
    const x = d3.scaleBand<number>().domain(data.map((d) => d.v)).range([M.l, W - M.r]).padding(0.12)
    const y = d3.scaleLinear().domain([0, d3.max(data, (d) => d.n) ?? 1]).nice().range([H - M.b, M.t])
    const s = d3.select(ref.current)
    if (s.select('g.bars').empty()) { s.append('g').attr('class', 'bars'); s.append('g').attr('class', 'lb-ax lb-ax--x'); s.append('text').attr('class', 'lb-cap') }
    s.select<SVGGElement>('g.bars').selectAll<SVGRectElement, (typeof data)[number]>('rect').data(data, (d) => String(d.v)).join('rect')
      .attr('x', (d) => x(d.v)!).attr('width', x.bandwidth()).attr('fill', (d) => (d.v > 0 ? '#34d399' : d.v < 0 ? '#fbbf24' : '#94a3b8'))
      .transition().duration(450).attr('y', (d) => y(d.n)).attr('height', (d) => H - M.b - y(d.n))
    const every = kind === 'GOALS' ? 1 : 5
    s.select<SVGGElement>('g.lb-ax--x').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).tickValues(data.map((d) => d.v).filter((v) => v % every === 0)).tickFormat((v) => (Number(v) > 0 ? `+${v}` : String(v))))
    s.select('text.lb-cap').attr('x', M.l).attr('y', M.t + 8).text(`${home} margin · right = ${home} wins, left = ${away} wins`)
  }, [margins, home, away, kind])
  return <svg ref={ref} viewBox="0 0 640 170" className="lb__hist" role="img" aria-label="Simulated margins" />
}

/**
 * Box plots of the home margin under three models on the same axis: YOU (your inputs), AISP (its model), MARKET
 * (the no-vig price turned into the same kind of model). Box = middle half of games, whiskers = 90%, line =
 * median. Click a chip to hide or show a row; hover a row for its numbers.
 */
function BoxPlots({ rows, home, kind }: { rows: { key: string; label: string; m: Int16Array | null }[]; home: string; kind: string }) {
  const ref = useRef<SVGSVGElement | null>(null)
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [hot, setHot] = useState<string | null>(null)
  const shown = rows.filter((r) => r.m && !hidden.has(r.key)).map((r) => ({ ...r, q: quantiles(r.m!) }))
  const sig = shown.map((r) => r.key + r.q.join(',')).join('|')
  useEffect(() => {
    if (!ref.current) return
    const TONE: Record<string, string> = { you: '#34d399', aisp: '#fbbf24', mkt: '#94a3b8' }
    const W = 640, H = 170, M = { l: 70, r: 14, t: 18, b: 26 }
    const span = kind === 'GOALS' ? 6 : 30
    const x = d3.scaleLinear().domain([-span, span]).range([M.l, W - M.r])
    const y = d3.scaleBand<string>().domain(shown.map((r) => r.key)).range([M.t, H - M.b]).padding(0.35)
    const s = d3.select(ref.current); s.selectAll('*').remove()
    s.append('line').attr('class', 'lb-zero').attr('x1', x(0)).attr('x2', x(0)).attr('y1', M.t - 4).attr('y2', H - M.b)
    s.append('g').attr('class', 'lb-ax').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(7).tickFormat((v) => (Number(v) > 0 ? `+${v}` : String(v))))
    const cx = (v: number) => x(Math.max(-span, Math.min(span, v)))
    for (const r of shown) {
      const c = TONE[r.key], yy = y(r.key)!, h = y.bandwidth(), mid = yy + h / 2
      const g = s.append('g').attr('class', `lb-box${hot && hot !== r.key ? ' is-dim' : ''}`).on('mouseenter', () => setHot(r.key)).on('mouseleave', () => setHot(null))
      g.append('rect').attr('x', M.l).attr('width', W - M.r - M.l).attr('y', yy - 3).attr('height', h + 6).attr('fill', 'transparent')
      g.append('line').attr('x1', cx(r.q[0])).attr('x2', cx(r.q[4])).attr('y1', mid).attr('y2', mid).attr('stroke', c).attr('stroke-width', 1.5)
      for (const v of [r.q[0], r.q[4]]) g.append('line').attr('x1', cx(v)).attr('x2', cx(v)).attr('y1', mid - h / 4).attr('y2', mid + h / 4).attr('stroke', c).attr('stroke-width', 1.5)
      g.append('rect').attr('x', cx(r.q[1])).attr('width', Math.max(2, cx(r.q[3]) - cx(r.q[1]))).attr('y', yy).attr('height', h).attr('rx', 4)
        .attr('fill', c).attr('fill-opacity', 0.25).attr('stroke', c).attr('stroke-width', 1.5)
      g.append('line').attr('x1', cx(r.q[2])).attr('x2', cx(r.q[2])).attr('y1', yy).attr('y2', yy + h).attr('stroke', '#fff').attr('stroke-width', 2.5)
      g.append('text').attr('class', 'lb-box__label').attr('x', M.l - 8).attr('y', mid + 4).attr('text-anchor', 'end').attr('fill', c).text(r.label)
    }
    s.append('text').attr('class', 'lb-cap').attr('x', M.l).attr('y', 11).text(`${home} margin · box = middle half of games · whiskers = 90%`)
  }, [sig, hot, home, kind]) // eslint-disable-line react-hooks/exhaustive-deps
  const hotRow = shown.find((r) => r.key === hot)
  const sign = (v: number) => (v > 0 ? `+${v}` : String(v))
  return (
    <div className="lb__boxwrap">
      <svg ref={ref} viewBox="0 0 640 170" className="lb__hist" role="img" aria-label="Box plots of the margin: you, AISP and the market" />
      <div className="lb__boxbar">
        {rows.filter((r) => r.m).map((r) => <button key={r.key} type="button" aria-pressed={!hidden.has(r.key)} className={`lb__chip lb__chip--${r.key}`}
          onClick={() => setHidden((h) => { const n = new Set(h); if (n.has(r.key)) n.delete(r.key); else n.add(r.key); return n })}>{r.label}</button>)}
        <small>{hotRow ? `${hotRow.label}: median ${sign(hotRow.q[2])}, middle half ${sign(hotRow.q[1])} to ${sign(hotRow.q[3])}, 90% of games ${sign(hotRow.q[0])} to ${sign(hotRow.q[4])}` : 'Hover a box for its numbers; click a chip to hide it.'}</small>
      </div>
    </div>
  )
}
