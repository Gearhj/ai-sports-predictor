/**
 * UFC FIGHT NIGHT (owner 10-24 night: "I'm excited to see what you develop for an MMA page"). Test mode only.
 *
 * Built on the historical archive Codex handed over (126 UFC cards, 1,504 bouts, Jan 2023 – Dec 2025), joined with the
 * retained raw source for method, round, time and fight statistics, with the winner orientation CORRECTED (the
 * handoff flipped about a third of winners; see the Codex note in asp-model-exchange/ufc-integration-v1).
 *
 *   THE OCTAGON (the HOLY SHIT feature): the chosen fight in the cage. Red and blue corners, the pre-fight line from a
 *   transparent corrected-label Elo, the round strip with the finish marked where it happened, battle bars for
 *   strikes, takedowns, control and knockdowns that fill once, where the strikes landed (head/body/leg) and where the
 *   fight was fought (distance/clinch/ground), and an UPSET stamp when the underdog won.
 *
 * Around it: the card reel, the full fight card, fighter profiles, how fights end, and the road to a live UFC model.
 * There is no current card, no odds and no wager here. Probabilities are historical reconstructions, never advice.
 */
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { AispSide } from '../flow/AispSide'
import '../slate/slate.css'
import './mma.css'

type Side = { kd: number | null; str: number | null; td: number | null; sub: number | null; ctrl: number | null; sig_pct: number | null; head: number | null; body: number | null; leg: number | null; distance: number | null; clinch: number | null; ground: number | null }
type Bout = { id: string; event: string; date: string; order: number; weight_class: string | null; split: string; a: string; b: string; elo_fixed_a: number; elo_rating_a: number; elo_rating_b: number; gen1_hgb_a_invalid: number | null; winner: 'A' | 'B' | null; handoff_winner_flipped: boolean; method: string | null; method_short: string; round: number | null; time: string | null; format: string | null; referee: string | null; stats: { a: Side; b: Side } }
type Fighter = { id: string; name: string | null; nickname: string | null; height: string | null; weight_lb: number | null; reach_in: number | null; stance: string | null; w: number | null; l: number | null; d: number | null; belt: boolean; style: string | null; ko_rate: number | null; sub_rate: number | null; dec_rate: number | null }
type Event = { id: string; name: string | null; date: string; location: string | null; bouts: string[] }
type Archive = { coverage: { bouts: number; events: number; fighters: number; first: string; last: string }; orientation_fix: { handoff_winners_flipped: number }; events: Event[]; bouts: Record<string, Bout>; fighters: Record<string, Fighter>; notes: string[] }

const RED = '#ef4444'; const BLUE = '#3b82f6'
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const mmss = (s: number | null) => (s == null ? '—' : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`)
const dateText = (d: string) => new Date(`${d}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
const initials = (n: string | null) => (n ?? '?').split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
const rec = (f: Fighter | undefined) => (f && f.w != null ? `${f.w}-${f.l ?? 0}${f.d ? `-${f.d}` : ''}` : '—')
const methodIcon: Record<string, string> = { 'KO/TKO': '💥', SUB: '🔒', DEC: '⚖', DQ: '⛔' }
// The corner: whoever was the corrected-Elo favourite sits in red, as the UFC lists the favourite/champion first.
const corners = (b: Bout) => (b.elo_fixed_a >= 0.5 ? { red: 'a' as const, blue: 'b' as const } : { red: 'b' as const, blue: 'a' as const })

export function MmaPage() {
  const [data, setData] = useState<Archive | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [eventId, setEventId] = useState<string | null>(null)
  const [boutId, setBoutId] = useState<string | null>(null)
  const [fighterId, setFighterId] = useState<string | null>(null)
  const [run, setRun] = useState(0)
  useEffect(() => {
    let alive = true
    fetch('/preview-api/ufc/archive', { headers: { accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: Archive) => { if (alive) setData(d) })
      .catch((e: Error) => { if (alive) setError(e.message) })
    return () => { alive = false }
  }, [])
  const event = useMemo(() => (data ? data.events.find((e) => e.id === eventId) ?? data.events[0] : null), [data, eventId])
  const bout = data && event ? data.bouts[boutId && event.bouts.includes(boutId) ? boutId : event.bouts[0]] : null
  const F = data?.fighters ?? {}

  return (
    <div className="hq mma">
      <AispSide />
      <div className="hq-main">
        <header className="sl-top">
          <div className="sl-title"><small>MMA · UFC</small><h1>Fight Night</h1><p>Every card, every fight, how it ended, and how the numbers saw it coming.</p></div>
          <span className="mma-flag">Historical archive · test mode · no live card yet</span>
        </header>
        {error ? <p className="sl-empty">The UFC archive could not be read ({error}).</p> : null}
        {!data && !error ? <p className="sl-empty">Loading 1,504 fights…</p> : null}
        {data && event && bout ? (
          <div className="mma-body">
            <Reel events={data.events} bouts={data.bouts} fighters={F} on={event.id} onPick={(id) => { setEventId(id); setBoutId(null); setRun((r) => r + 1) }} />
            <div className="mma-grid">
              <main className="mma-main">
                <Octagon key={`${bout.id}-${run}`} b={bout} F={F} event={event} onFighter={setFighterId} onReplay={() => setRun((r) => r + 1)} />
                <Card event={event} bouts={data.bouts} F={F} on={bout.id} onPick={(id) => { setBoutId(id); setRun((r) => r + 1) }} onFighter={setFighterId} />
              </main>
              <aside className="mma-rail">
                {fighterId && F[fighterId] ? <Profile f={F[fighterId]} bouts={data.bouts} onClose={() => setFighterId(null)} onBout={(b) => { setEventId(b.event); setBoutId(b.id); setRun((r) => r + 1) }} /> : null}
                <RoadToLive flipped={data.orientation_fix.handoff_winners_flipped} />
                <HowFightsEnd bouts={data.bouts} />
                <section className="sl-card mma-notes"><h3>About this data</h3><ul>{data.notes.map((n, i) => <li key={i}>{n}</li>)}</ul><small>{data.coverage.events} cards · {data.coverage.bouts} bouts · {data.coverage.fighters} fighters · {dateText(data.coverage.first)} – {dateText(data.coverage.last)}</small></section>
              </aside>
            </div>
          </div>
        ) : null}
        <footer className="hq-foot">Research and paper only. UFC probabilities on this page are historical reconstructions from a transparent Elo baseline, not an AISP model and never a wager signal. No odds are collected for MMA yet.</footer>
      </div>
    </div>
  )
}

function Reel({ events, bouts, fighters, on, onPick }: { events: Event[]; bouts: Record<string, Bout>; fighters: Record<string, Fighter>; on: string; onPick: (id: string) => void }) {
  return (
    <section className="mma-reel" aria-label="UFC cards">
      {events.slice(0, 40).map((e) => {
        const main = bouts[e.bouts[0]]
        const ppv = /^UFC \d+/.test(e.name ?? '')
        return (
          <button key={e.id} type="button" className={`mma-poster${e.id === on ? ' is-on' : ''}${ppv ? ' is-ppv' : ''}`} onClick={() => onPick(e.id)}>
            <small>{dateText(e.date)}</small>
            <b>{(e.name ?? 'UFC event').split(':')[0]}</b>
            <span>{main ? `${fighters[main.a]?.name?.split(' ').slice(-1)[0] ?? '?'} vs ${fighters[main.b]?.name?.split(' ').slice(-1)[0] ?? '?'}` : ''}</span>
            <em>{e.location?.split(',')[0] ?? ''}</em>
          </button>
        )
      })}
    </section>
  )
}

function Avatar({ f, color, size = 64 }: { f: Fighter | undefined; color: string; size?: number }) {
  return <span className="mma-ava" style={{ width: size, height: size, fontSize: size * 0.36, ['--c' as string]: color } as CSSProperties}>{initials(f?.name ?? null)}{f?.belt ? <i title="Champion in the dataset snapshot">🏆</i> : null}</span>
}

function Octagon({ b, F, event, onFighter, onReplay }: { b: Bout; F: Record<string, Fighter>; event: Event; onFighter: (id: string) => void; onReplay: () => void }) {
  const c = corners(b)
  const red = { id: b[c.red], s: b.stats[c.red], p: c.red === 'a' ? b.elo_fixed_a : 1 - b.elo_fixed_a, elo: c.red === 'a' ? b.elo_rating_a : b.elo_rating_b, won: b.winner === (c.red === 'a' ? 'A' : 'B') }
  const blue = { id: b[c.blue], s: b.stats[c.blue], p: 1 - red.p, elo: c.blue === 'a' ? b.elo_rating_a : b.elo_rating_b, won: b.winner === (c.blue === 'a' ? 'A' : 'B') }
  const winner = red.won ? red : blue.won ? blue : null
  const upset = Boolean(winner && winner.p < 0.45)
  const rounds = /^5/.test(b.format ?? '') ? 5 : 3
  const [mm, ss] = (b.time ?? '5:00').split(':').map(Number)
  const endFrac = b.round ? ((b.round - 1) * 300 + (mm * 60 + ss)) / (rounds * 300) : 1
  const fn = (id: string) => F[id]?.name ?? 'Unknown'
  const pts = Array.from({ length: 8 }, (_, i) => { const a = Math.PI / 8 + (i * Math.PI) / 4; return `${200 + 170 * Math.cos(a)},${200 + 170 * Math.sin(a)}` }).join(' ')
  const pts2 = Array.from({ length: 8 }, (_, i) => { const a = Math.PI / 8 + (i * Math.PI) / 4; return `${200 + 150 * Math.cos(a)},${200 + 150 * Math.sin(a)}` }).join(' ')
  const bars: Array<[string, number | null, number | null, (v: number | null) => string]> = [
    ['Significant strikes', red.s.str, blue.s.str, (v) => (v == null ? '—' : String(v))],
    ['Knockdowns', red.s.kd, blue.s.kd, (v) => (v == null ? '—' : String(v))],
    ['Takedowns', red.s.td, blue.s.td, (v) => (v == null ? '—' : String(v))],
    ['Submission attempts', red.s.sub, blue.s.sub, (v) => (v == null ? '—' : String(v))],
    ['Control time', red.s.ctrl, blue.s.ctrl, mmss],
  ]
  return (
    <section className="sl-card mma-oct" aria-label="The Octagon">
      <header className="sl-card__head">
        <h2>The Octagon <small>{event.name} · {dateText(event.date)}{event.location ? ` · ${event.location}` : ''}</small></h2>
        <button type="button" className="mma-replay" onClick={onReplay}>↻ Replay the fight</button>
      </header>
      <div className="mma-oct__stage">
        <button type="button" className={`mma-corner is-red${red.won ? ' is-won' : ''}`} onClick={() => onFighter(red.id)}>
          <Avatar f={F[red.id]} color={RED} size={86} />
          <b>{fn(red.id)}</b>
          <small>{F[red.id]?.nickname ? `"${F[red.id]!.nickname}" · ` : ''}{rec(F[red.id])}</small>
          <span className="mma-odds" style={{ ['--c' as string]: RED } as CSSProperties}><em>{pct(red.p)}</em><small>pre-fight Elo</small></span>
          {red.won ? <strong className="mma-w">WINNER</strong> : null}
        </button>
        <div className="mma-cage">
          <svg viewBox="0 0 400 400" aria-hidden="true">
            <defs>
              <radialGradient id="mma-mat" cx="50%" cy="50%" r="50%"><stop offset="0" stopColor="#1e2a3d" /><stop offset="1" stopColor="#0b1526" /></radialGradient>
              <linearGradient id="mma-tug" x1="0" x2="1"><stop offset="0" stopColor={RED} /><stop offset={red.p} stopColor={RED} /><stop offset={red.p} stopColor={BLUE} /><stop offset="1" stopColor={BLUE} /></linearGradient>
            </defs>
            <polygon points={pts} className="mma-cage__fence" />
            <polygon points={pts2} fill="url(#mma-mat)" className="mma-cage__mat" />
            <circle cx="200" cy="200" r="58" className="mma-cage__logo" />
            <text x="200" y="196" textAnchor="middle" className="mma-cage__ufc">UFC</text>
            <text x="200" y="216" textAnchor="middle" className="mma-cage__wc">{(b.weight_class ?? '').toUpperCase()}</text>
            <circle cx="88" cy="312" r="16" fill={RED} className="mma-cage__pad" />
            <circle cx="312" cy="88" r="16" fill={BLUE} className="mma-cage__pad" />
            <rect x="60" y="356" width="280" height="10" rx="5" fill="url(#mma-tug)" className="mma-cage__tug" />
            {winner ? <g className={`mma-cage__burst ${red.won ? 'is-red' : 'is-blue'}`} style={{ ['--c' as string]: red.won ? RED : BLUE } as CSSProperties}>{Array.from({ length: 12 }, (_, i) => <line key={i} x1={red.won ? 88 : 312} y1={red.won ? 312 : 88} x2={(red.won ? 88 : 312) + 46 * Math.cos((i * Math.PI) / 6)} y2={(red.won ? 312 : 88) + 46 * Math.sin((i * Math.PI) / 6)} />)}</g> : null}
          </svg>
          <div className="mma-result">
            <b>{methodIcon[b.method_short] ?? ''} {b.method ?? 'Result unknown'}</b>
            <span>{b.round ? `Round ${b.round} · ${b.time}` : ''}{b.referee ? ` · ref ${b.referee}` : ''}</span>
          </div>
          {upset ? <span className="mma-upset">UPSET!</span> : null}
        </div>
        <button type="button" className={`mma-corner is-blue${blue.won ? ' is-won' : ''}`} onClick={() => onFighter(blue.id)}>
          <Avatar f={F[blue.id]} color={BLUE} size={86} />
          <b>{fn(blue.id)}</b>
          <small>{F[blue.id]?.nickname ? `"${F[blue.id]!.nickname}" · ` : ''}{rec(F[blue.id])}</small>
          <span className="mma-odds" style={{ ['--c' as string]: BLUE } as CSSProperties}><em>{pct(blue.p)}</em><small>pre-fight Elo</small></span>
          {blue.won ? <strong className="mma-w">WINNER</strong> : null}
        </button>
      </div>

      <div className="mma-rounds" aria-label="Round strip">
        {Array.from({ length: rounds }, (_, i) => <span key={i} className={`mma-rnd${b.round && i + 1 > b.round ? ' is-unfought' : ''}`}><small>R{i + 1}</small></span>)}
        <i className="mma-rounds__fill" style={{ ['--w' as string]: `${endFrac * 100}%`, ['--c' as string]: winner ? (red.won ? RED : BLUE) : '#94a3b8' } as CSSProperties} />
        <em className="mma-rounds__end" style={{ left: `${endFrac * 100}%` }}>{methodIcon[b.method_short] ?? '■'} {b.method_short}{b.method_short === 'DEC' ? '' : ` ${b.time ?? ''}`}</em>
      </div>

      <div className="mma-tape">
        <div className="mma-bars">
          {bars.map(([label, rv, bv, f]) => {
            const tot = Math.max(1, (rv ?? 0) + (bv ?? 0))
            return (
              <div key={label} className="mma-bar">
                <b className="is-red">{f(rv)}</b>
                <span className="mma-bar__track"><i className="is-red" style={{ width: `${((rv ?? 0) / tot) * 50}%` }} /><i className="is-blue" style={{ width: `${((bv ?? 0) / tot) * 50}%` }} /><small>{label}</small></span>
                <b className="is-blue">{f(bv)}</b>
              </div>
            )
          })}
        </div>
        <div className="mma-maps">
          <TargetMap s={red.s} color={RED} label={fn(red.id).split(' ').slice(-1)[0]} />
          <PositionRing red={red.s} blue={blue.s} />
          <TargetMap s={blue.s} color={BLUE} label={fn(blue.id).split(' ').slice(-1)[0]} />
        </div>
      </div>
      <p className="mma-read">{storyOf(b, fn(red.id), fn(blue.id), red, blue, upset)}</p>
      {b.handoff_winner_flipped ? <p className="mma-fix">Corrected: the archive Codex handed over lists the other fighter as the winner of this bout. The raw source writes this row loser-first.</p> : null}
    </section>
  )
}

function storyOf(b: Bout, rn: string, bn: string, red: { p: number; won: boolean; s: Side }, blue: { p: number; won: boolean; s: Side }, upset: boolean) {
  if (!b.winner) return 'No decisive result in the archive for this bout.'
  const w = red.won ? rn : bn; const l = red.won ? bn : rn; const wp = red.won ? red.p : blue.p
  const ws = red.won ? red.s : blue.s; const ls = red.won ? blue.s : red.s
  const how = b.method_short === 'KO/TKO' ? `stopped ${l.split(' ').slice(-1)[0]} by KO/TKO in round ${b.round} at ${b.time}` : b.method_short === 'SUB' ? `submitted ${l.split(' ').slice(-1)[0]} in round ${b.round} at ${b.time}` : `won a ${b.method ?? 'decision'} over ${l.split(' ').slice(-1)[0]}`
  const edge = (ws.str ?? 0) > (ls.str ?? 0) ? ` outlanding them ${ws.str}–${ls.str}` : (ws.td ?? 0) > (ls.td ?? 0) ? ` on the strength of ${ws.td} takedowns` : (ws.ctrl ?? 0) > (ls.ctrl ?? 0) ? ` with ${mmss(ws.ctrl)} of control` : ''
  return `${w} ${how}${edge}. ${upset ? `The Elo line gave ${w.split(' ').slice(-1)[0]} only ${pct(wp)} going in: an upset.` : `The Elo line had ${w.split(' ').slice(-1)[0]} at ${pct(wp)}.`}`
}

function TargetMap({ s, color, label }: { s: Side; color: string; label: string }) {
  const a = (v: number | null) => 0.15 + 0.85 * Math.min(1, v ?? 0)
  return (
    <figure className="mma-target" style={{ ['--c' as string]: color } as CSSProperties}>
      <svg viewBox="0 0 60 120" aria-hidden="true">
        <circle cx="30" cy="16" r="12" style={{ fillOpacity: a(s.head) }} />
        <rect x="14" y="31" width="32" height="38" rx="8" style={{ fillOpacity: a(s.body) }} />
        <rect x="14" y="71" width="13" height="44" rx="6" style={{ fillOpacity: a(s.leg) }} /><rect x="33" y="71" width="13" height="44" rx="6" style={{ fillOpacity: a(s.leg) }} />
      </svg>
      <figcaption><b>{label}</b><small>head {pct(s.head)} · body {pct(s.body)} · leg {pct(s.leg)}</small></figcaption>
    </figure>
  )
}

function PositionRing({ red, blue }: { red: Side; blue: Side }) {
  const rows: Array<[string, number | null, number | null]> = [['Distance', red.distance, blue.distance], ['Clinch', red.clinch, blue.clinch], ['Ground', red.ground, blue.ground]]
  return (
    <div className="mma-pos">
      <h4>Where their strikes came from</h4>
      {rows.map(([l, r, b]) => <div key={l} className="mma-pos__row"><b className="is-red">{pct(r)}</b><span>{l}</span><b className="is-blue">{pct(b)}</b></div>)}
      <small>share of each fighter's significant strikes</small>
    </div>
  )
}

function Card({ event, bouts, F, on, onPick, onFighter }: { event: Event; bouts: Record<string, Bout>; F: Record<string, Fighter>; on: string; onPick: (id: string) => void; onFighter: (id: string) => void }) {
  return (
    <section className="sl-card mma-card" aria-label="Fight card">
      <header className="sl-card__head"><h2>The Card <small>{event.bouts.length} fights · main event first</small></h2></header>
      <ol>
        {event.bouts.map((id, i) => {
          const b = bouts[id]; const c = corners(b)
          const rid = b[c.red]; const bid = b[c.blue]; const rp = c.red === 'a' ? b.elo_fixed_a : 1 - b.elo_fixed_a
          const redWon = b.winner === (c.red === 'a' ? 'A' : 'B'); const upset = b.winner && (redWon ? rp : 1 - rp) < 0.45
          return (
            <li key={id} className={id === on ? 'is-on' : ''}>
              <button type="button" className="mma-row" onClick={() => onPick(id)}>
                <span className="mma-row__tag">{i === 0 ? 'MAIN EVENT' : i === 1 ? 'CO-MAIN' : b.weight_class ?? ''}</span>
                <span className={`mma-row__f is-red${redWon ? ' is-won' : ''}`}><Avatar f={F[rid]} color={RED} size={30} /><b onClick={(e) => { e.stopPropagation(); onFighter(rid) }}>{F[rid]?.name ?? '?'}</b><small>{rec(F[rid])}</small></span>
                <span className="mma-row__line"><i style={{ width: `${rp * 100}%` }} /><em>{pct(rp)}</em><em>{pct(1 - rp)}</em></span>
                <span className={`mma-row__f is-blue${!redWon && b.winner ? ' is-won' : ''}`}><Avatar f={F[bid]} color={BLUE} size={30} /><b onClick={(e) => { e.stopPropagation(); onFighter(bid) }}>{F[bid]?.name ?? '?'}</b><small>{rec(F[bid])}</small></span>
                <span className="mma-row__res">{methodIcon[b.method_short] ?? ''} {b.method_short}{b.round && b.method_short !== 'DEC' ? ` R${b.round} ${b.time}` : ''}{upset ? <strong>UPSET</strong> : null}{b.handoff_winner_flipped ? <u title="Winner corrected from the archive">fixed</u> : null}</span>
              </button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function Profile({ f, bouts, onClose, onBout }: { f: Fighter; bouts: Record<string, Bout>; onClose: () => void; onBout: (b: Bout) => void }) {
  const mine = Object.values(bouts).filter((b) => b.a === f.id || b.b === f.id).sort((x, y) => y.date.localeCompare(x.date))
  const rates: Array<[string, number | null, string]> = [['KO/TKO', f.ko_rate, '#ef4444'], ['Submission', f.sub_rate, '#a78bfa'], ['Decision', f.dec_rate, '#fbbf24']]
  let acc = 0; const C = 2 * Math.PI * 34
  return (
    <section className="sl-card mma-prof" aria-label={`${f.name} profile`}>
      <header className="sl-card__head"><h2>{f.name}</h2><button type="button" className="mma-x" onClick={onClose} aria-label="Close">✕</button></header>
      {f.nickname ? <small className="mma-nick">"{f.nickname}"</small> : null}
      <div className="mma-prof__tape">
        <span><small>Record</small><b>{rec(f)}</b></span><span><small>Height</small><b>{f.height ?? '—'}</b></span><span><small>Reach</small><b>{f.reach_in ? `${f.reach_in}"` : '—'}</b></span><span><small>Stance</small><b>{f.stance ?? '—'}</b></span><span><small>Style</small><b>{f.style ?? '—'}</b></span><span><small>Weight</small><b>{f.weight_lb ? `${f.weight_lb} lb` : '—'}</b></span>
      </div>
      <div className="mma-prof__how">
        <svg viewBox="0 0 90 90" aria-hidden="true"><circle cx="45" cy="45" r="34" className="mma-prof__track" />{rates.map(([k, v, c]) => { const el = <circle key={k} cx="45" cy="45" r="34" fill="none" stroke={c} strokeWidth="12" strokeDasharray={`${(v ?? 0) * C} ${C}`} strokeDashoffset={-acc * C} transform="rotate(-90 45 45)" />; acc += v ?? 0; return el })}</svg>
        <ul>{rates.map(([k, v, c]) => <li key={k}><i style={{ background: c }} />{k}<b>{pct(v)}</b></li>)}<li className="mma-prof__cap">how their wins came</li></ul>
      </div>
      <h4>In the archive</h4>
      <ol className="mma-prof__list">{mine.slice(0, 8).map((b) => { const isA = b.a === f.id; const won = b.winner === (isA ? 'A' : 'B'); return <li key={b.id}><button type="button" onClick={() => onBout(b)}><em className={won ? 'is-w' : 'is-l'}>{won ? 'W' : 'L'}</em><span>{b.method_short}{b.round && b.method_short !== 'DEC' ? ` R${b.round}` : ''}</span><small>{dateText(b.date)}</small></button></li> })}</ol>
      <small className="mma-prof__foot">Profile and record are the dataset's current snapshot.</small>
    </section>
  )
}

function RoadToLive({ flipped }: { flipped: number }) {
  const steps: Array<[string, string, 'done' | 'now' | 'james' | 'next']> = [
    ['Historical archive on AISP', 'This page: 126 cards, corrected winners', 'done'],
    ['Corrected UFC model (Codex)', `Gen1 withdrawn (${flipped} archive winners were flipped). Codex refit on corrected labels 10-25: 57% winners, log loss 0.673 vs Elo 0.687 on 2019–22. Needs a fresh test before live use.`, 'now'],
    ['Live fight-card feed', 'FighterAPI free tier: James creates the account and key', 'james'],
    ['Forecast contract + importer', 'Claude builds it once the corrected model publishes', 'next'],
    ['Odds + paper account', 'The Odds API MMA lines; MMA $1,000 paper account; shared $100', 'next'],
  ]
  return (
    <section className="sl-card mma-road" aria-label="Road to live UFC">
      <h3>Road to live UFC</h3>
      <ol>{steps.map(([t, d, s], i) => <li key={t} className={`is-${s}`}><i>{s === 'done' ? '✓' : i + 1}</i><span><b>{t}</b><small>{d}</small></span>{s === 'james' ? <em>needs James</em> : s === 'now' ? <em>in progress</em> : null}</li>)}</ol>
    </section>
  )
}

function HowFightsEnd({ bouts }: { bouts: Record<string, Bout> }) {
  const all = Object.values(bouts).filter((b) => b.winner)
  const n = all.length
  const by = (m: string) => all.filter((b) => b.method_short === m).length
  const upsets = all.filter((b) => { const p = b.winner === 'A' ? b.elo_fixed_a : 1 - b.elo_fixed_a; return p < 0.5 }).length
  const favWon = all.filter((b) => (b.elo_fixed_a >= 0.5) === (b.winner === 'A')).length
  const r1 = all.filter((b) => b.round === 1 && b.method_short !== 'DEC').length
  const rows: Array<[string, number, string]> = [['Decision', by('DEC'), '#fbbf24'], ['KO/TKO', by('KO/TKO'), '#ef4444'], ['Submission', by('SUB'), '#a78bfa']]
  return (
    <section className="sl-card mma-ends" aria-label="How fights end">
      <h3>How UFC fights end</h3>
      <div className="mma-ends__bar">{rows.map(([k, v, c]) => <i key={k} style={{ width: `${(v / n) * 100}%`, background: c }} title={`${k} ${v}`} />)}</div>
      <ul>{rows.map(([k, v, c]) => <li key={k}><i style={{ background: c }} />{k}<b>{Math.round((v / n) * 100)}%</b></li>)}</ul>
      <p><b>{Math.round((r1 / n) * 100)}%</b> end inside the first round. The Elo favourite won only <b>{Math.round((favWon / n) * 100)}%</b>; the underdog won <b>{Math.round((upsets / n) * 100)}%</b>. A plain Elo is cautious here (most lines sit between 43% and 57%), because UFC is the hardest sport on AISP to call. The corrected model Codex is rebuilding has to beat that.</p>
    </section>
  )
}

export default MmaPage
