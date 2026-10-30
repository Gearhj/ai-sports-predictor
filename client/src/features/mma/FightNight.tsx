/**
 * FIGHT NIGHT LIVE (owner 10-30: "the tale of the tape, the octagon, the fighter's stats, info on fighting style, info
 * on odds for each fighter, a golden card if this is a title fight"; then "There needs to be animations for knock outs,
 * take downs, submissions, clean strikes" and "The Main Event section at the top ... the Octagon is below that and it
 * feels repetitive").
 *
 * ONE hero card: the event, the chosen fight, both corners with odds, and the cage where the fight plays out. The card
 * list sits beside it. Under the cage: the fight's own stat line, the tale of the tape, career numbers and the style
 * read. A title fight turns the hero gold.
 *
 * THE CAGE ANIMATES REAL FIGHT DATA (/preview-api/ufc/bout: ESPN's official per-fight counters and timeline):
 *   live      polled every 10 s; every change in a fighter's counters becomes a moment in the cage: significant
 *             strikes (head/body/leg) fly across with an impact, a knockdown drops the other fighter, a takedown takes
 *             both to the mat, a submission attempt wraps them, a reversal flips who is on top. The finish (KO/TKO,
 *             TAP, or the judges' cards) lands when ESPN posts it.
 *   finished  "Replay the fight" rebuilds it from the official totals: takedowns at ESPN's recorded times, the finish
 *             at its real round and clock, the strikes spread through the time fought (ESPN records no strike times,
 *             so their order inside the fight is illustrative; the totals are exact, and the page says so).
 * Odds are the sportsbook's, information only: AISP has no UFC model or wager.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'

type Stats = { slpm: number | null; str_acc: number | null; td_avg: number | null; td_acc: number | null; sub_avg: number | null }
type Fighter = { id: string; name: string | null; nickname: string | null; headshot: string | null; flag: string | null; country: string | null; age: number | null; height: string | null; height_in: number | null; reach: string | null; reach_in: number | null; weight: string | null; stance: string | null; styles: string[]; gym: string | null; stats: Stats; style_read: string | null; champion: boolean; rank: number | null }
type Corner = { id: string; record: string | null; winner: boolean; odds: { american?: number | null; open?: number | null } }
type Bout = { id: string; slot: string | null; weight_class: string; rounds: number; title_fight: boolean; starts_at: string; state: 'pre' | 'in' | 'post' | string; status: string | null; period: number | null; clock: string | null; method: string | null; card_segment: string; a: Corner; b: Corner; market_p_a: number | null; odds_provider: string | null }
export type UfcCard = { found: boolean; reason?: string; retrieved_at?: string; event: { id: string; name: string; date: string; venue: string | null; city: string | null; live: boolean; complete: boolean }; bouts: Bout[]; fighters: Record<string, Fighter>; notes: string[] }
type Side = { kd: number | null; sig: number | null; sig_att: number | null; td: number | null; td_att: number | null; sub: number | null; rev: number | null; ctrl: string | null; head: number | null; body: number | null; leg: number | null; judges: number[]; has_stats: boolean }
type Detail = { found: boolean; state: string; period: number | null; clock: string | null; result: string | null; a: Side; b: Side; timeline: Array<{ type: string; round: number | null; clock: number | null }> }
type Who = 'a' | 'b'
type Moment = { t: number; kind: 'strike' | 'kd' | 'td' | 'sub' | 'rev' | 'round' | 'ko' | 'tap' | 'dec'; by: Who; target?: 'head' | 'body' | 'leg'; n?: number; round?: number }
type Fx = Moment & { key: number }
type Count = { sig: number; kd: number; td: number; sub: number }

const RED = '#ef4444'; const BLUE = '#3b82f6'; const GOLD = '#fbbf24'
const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : String(v))
const implied = (v: number | null | undefined) => (v == null ? null : v > 0 ? 100 / (v + 100) : -v / (-v + 100))
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const etTime = (iso: string, o: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit' }) => new Date(iso).toLocaleString('en-US', { timeZone: 'America/New_York', ...o })
const last = (n: string | null | undefined) => (n ?? '?').split(' ').slice(-1)[0]
const initials = (n: string | null) => (n ?? '?').split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
const other = (w: Who): Who => (w === 'a' ? 'b' : 'a')
const fightClock = (t: number) => { const r = Math.min(5, Math.floor(t / 300) + 1); const s = Math.round(t - (r - 1) * 300); return `R${r} ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` }
const ZERO: Count = { sig: 0, kd: 0, td: 0, sub: 0 }

export function useUfcCard(which: 'next' | 'last' = 'next') {
  const [card, setCard] = useState<UfcCard | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true; let timer = 0
    setCard(null)
    const load = () => fetch(`/preview-api/ufc/card${which === 'last' ? '?card=last' : ''}`, { headers: { accept: 'application/json' } })
      .then((r) => r.json())
      .then((d: UfcCard) => {
        if (!alive) return
        setCard(d); setError(null)
        // Every 30 s while a fight is on, every minute in the two hours before the first bell, else every 10 minutes.
        const first = d.found ? Math.min(...d.bouts.map((b) => Date.parse(b.starts_at))) : Infinity
        const wait = d.found && d.event.live ? 30_000 : first - Date.now() < 2 * 3600_000 && !d.event?.complete ? 60_000 : 600_000
        timer = window.setTimeout(load, wait)
      })
      .catch((e: Error) => { if (alive) { setError(e.message); timer = window.setTimeout(load, 120_000) } })
    load()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [which])
  return { card, error }
}

/** The fight's own counters, timeline and scorecards: polled every 10 s while it is live, once otherwise. */
function useBout(eventId: string, b: Bout) {
  const [d, setD] = useState<Detail | null>(null)
  useEffect(() => {
    let alive = true; let timer = 0
    setD(null)
    const load = () => fetch(`/preview-api/ufc/bout?event=${eventId}&comp=${b.id}&a=${b.a.id}&b=${b.b.id}`, { headers: { accept: 'application/json' } })
      .then((r) => r.json())
      .then((x: Detail) => { if (alive && x.found) { setD(x); if (x.state === 'in' || (x.state === 'pre' && b.state !== 'post')) timer = window.setTimeout(load, x.state === 'in' ? 10_000 : 60_000) } })
      .catch(() => { if (alive) timer = window.setTimeout(load, 30_000) })
    load()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [eventId, b.id, b.a.id, b.b.id, b.state])
  return d
}

// A seeded random source so a fight's replay is the same every time it is played.
function seeded(text: string) { let h = 2166136261; for (const c of text) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return () => { h += 0x6d2b79f5; let t = h; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296 } }

const clockSec = (c: string | null | undefined) => { const [m, s] = (c ?? '0:00').split(':').map(Number); return (m || 0) * 60 + (s || 0) }

/** Rebuild a finished fight as a list of moments in fight seconds (see the header for what is exact and what is not). */
function buildReplay(b: Bout, d: Detail): { moments: Moment[]; end: number } {
  const end = Math.max(30, (Math.max(1, d.period ?? b.rounds) - 1) * 300 + clockSec(d.clock))
  const rnd = seeded(b.id)
  const out: Moment[] = []
  for (let r = 1; r <= Math.ceil(end / 300); r += 1) out.push({ t: (r - 1) * 300 + 0.01, kind: 'round', by: 'a', round: r })
  const winner: Who | null = b.a.winner ? 'a' : b.b.winner ? 'b' : null
  const finish = /KO|TKO/i.test(d.result ?? b.method ?? '') ? 'ko' : /SUB/i.test(d.result ?? b.method ?? '') ? 'tap' : /DEC/i.test(d.result ?? b.method ?? '') ? 'dec' : null
  for (const w of ['a', 'b'] as Who[]) {
    const s = d[w]
    // Strikes: each landed significant strike gets a time, then they are grouped into 15-second bursts.
    const bursts = new Map<number, { n: number; head: number; body: number; leg: number }>()
    for (const target of ['head', 'body', 'leg'] as const) {
      for (let i = 0; i < (s[target] ?? 0); i += 1) {
        const slot = Math.floor((rnd() * (end - 4)) / 15)
        const g = bursts.get(slot) ?? { n: 0, head: 0, body: 0, leg: 0 }
        g.n += 1; g[target] += 1; bursts.set(slot, g)
      }
    }
    for (const [slot, g] of bursts) out.push({ t: slot * 15 + rnd() * 14, kind: 'strike', by: w, n: g.n, target: g.head >= g.body && g.head >= g.leg ? 'head' : g.body >= g.leg ? 'body' : 'leg' })
    const kds = s.kd ?? 0
    for (let i = 0; i < kds; i += 1) out.push({ t: finish === 'ko' && w === winner && i === kds - 1 ? end - 6 : rnd() * (end - 10), kind: 'kd', by: w })
    for (let i = 0; i < (s.sub ?? 0); i += 1) out.push({ t: finish === 'tap' && w === winner && i === (s.sub ?? 0) - 1 ? end - 8 : rnd() * (end - 10), kind: 'sub', by: w })
    for (let i = 0; i < (s.rev ?? 0); i += 1) out.push({ t: rnd() * (end - 10), kind: 'rev', by: w })
  }
  // Takedowns at ESPN's recorded times; its timeline does not name the fighter, so they are dealt out in proportion
  // to each fighter's official takedown count.
  const tdTimes = d.timeline.filter((x) => x.type === 'Takedown' && x.round).map((x) => ((x.round ?? 1) - 1) * 300 + (300 - (x.clock ?? 300)))
  const owners: Who[] = [...Array(d.a.td ?? 0).fill('a'), ...Array(d.b.td ?? 0).fill('b')]
  owners.forEach((w, i) => out.push({ t: tdTimes[i] ?? rnd() * (end - 10), kind: 'td', by: w }))
  if (winner && finish) out.push({ t: end, kind: finish, by: winner })
  return { moments: out.sort((x, y) => x.t - y.t), end }
}

/** Live: turn the change between two polls of the official counters into moments, spread over the next few seconds. */
function diffMoments(prev: Detail, next: Detail, b: Bout): Moment[] {
  const out: Moment[] = []
  for (const w of ['a', 'b'] as Who[]) {
    const p = prev[w]; const n = next[w]
    for (const target of ['head', 'body', 'leg'] as const) { const dlt = (n[target] ?? 0) - (p[target] ?? 0); if (dlt > 0) out.push({ t: 0, kind: 'strike', by: w, target, n: dlt }) }
    for (const [k, kind] of [['kd', 'kd'], ['td', 'td'], ['sub', 'sub'], ['rev', 'rev']] as const) for (let i = 0; i < (n[k] ?? 0) - (p[k] ?? 0); i += 1) out.push({ t: 0, kind, by: w })
  }
  if (prev.state !== 'post' && next.state === 'post') {
    const winner: Who | null = b.a.winner ? 'a' : b.b.winner ? 'b' : null
    const r = next.result ?? ''
    if (winner) out.push({ t: 0, kind: /KO|TKO/i.test(r) ? 'ko' : /SUB/i.test(r) ? 'tap' : 'dec', by: winner })
  }
  return out.map((m, i) => ({ ...m, t: i * 1.2 }))
}

export function FightNight({ card, which, onWhich }: { card: UfcCard; which: 'next' | 'last'; onWhich: (w: 'next' | 'last') => void }) {
  const [boutId, setBoutId] = useState<string | null>(null)
  const live = card.bouts.find((b) => b.state === 'in')
  const bout = card.bouts.find((b) => b.id === boutId) ?? live ?? card.bouts[0]
  const segments = useMemo(() => ['Main card', 'Prelims'].map((s) => ({ s, bouts: card.bouts.filter((b) => b.card_segment === s) })).filter((g) => g.bouts.length), [card])
  const first = (s: string) => card.bouts.filter((b) => b.card_segment === s).map((b) => b.starts_at).sort()[0]
  if (!bout) return null
  return (
    <section className="fn" aria-label="Fight night">
      <div className="fn-grid">
        <Hero key={bout.id} card={card} b={bout} which={which} onWhich={onWhich} times={[['Prelims', first('Prelims')], ['Main card', first('Main card')]]} />
        <aside className="fn-list" aria-label="The card">
          {segments.map(({ s, bouts }) => (
            <div key={s}>
              <h4>{s}{first(s) ? ` · ${etTime(first(s))} ET` : ''}</h4>
              <ol>
                {bouts.map((b) => {
                  const A = card.fighters[b.a.id]; const B = card.fighters[b.b.id]
                  return (
                    <li key={b.id}>
                      <button type="button" className={`fn-row${b.id === bout.id ? ' is-on' : ''}${b.title_fight ? ' is-title' : ''}`} onClick={() => setBoutId(b.id)}>
                        <span className="fn-row__tag">{b.title_fight ? '🏆 TITLE' : b.slot ?? b.weight_class}{b.state === 'in' ? <em className="fn-live">R{b.period} LIVE</em> : null}</span>
                        <span className={`fn-row__f${b.a.winner ? ' is-won' : ''}`}><b className="is-red">{last(A?.name)}</b><small>{am(b.a.odds.american)}</small></span>
                        <span className="fn-row__bar"><i style={{ width: `${(b.market_p_a ?? 0.5) * 100}%` }} /></span>
                        <span className={`fn-row__f is-r${b.b.winner ? ' is-won' : ''}`}><small>{am(b.b.odds.american)}</small><b className="is-blue">{last(B?.name)}</b></span>
                        {b.state === 'post' && b.method ? <span className="fn-row__res">{last((b.a.winner ? A : B)?.name)} · {b.method}{b.method !== 'Decision' && b.period ? ` R${b.period} ${b.clock ?? ''}` : ''}</span> : null}
                      </button>
                    </li>
                  )
                })}
              </ol>
            </div>
          ))}
          <small className="fn-src">Card, results and fight stats: ESPN. Rankings: UFC.com. Odds: {bout.odds_provider ?? 'the sportsbook ESPN lists'}, information only (AISP has no UFC model or wager).</small>
        </aside>
      </div>
    </section>
  )
}

function Hero({ card, b, which, onWhich, times }: { card: UfcCard; b: Bout; which: 'next' | 'last'; onWhich: (w: 'next' | 'last') => void; times: Array<[string, string | undefined]> }) {
  const F = card.fighters; const A = F[b.a.id]; const B = F[b.b.id]
  const d = useBout(card.event.id, b)
  const name = { a: last(A?.name), b: last(B?.name) }
  // --- the cage's moment player -------------------------------------------------------------------------------
  const [fx, setFx] = useState<Fx[]>([])
  const [pose, setPose] = useState<{ kind: 'stand' | 'ground' | 'down'; top?: Who; low?: Who }>({ kind: 'stand' })
  const [count, setCount] = useState<{ a: Count; b: Count }>({ a: ZERO, b: ZERO })
  const [feed, setFeed] = useState<string[]>([])
  const [clock, setClock] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const timers = useRef<number[]>([])
  const seq = useRef(0)
  const stop = () => { timers.current.forEach((t) => window.clearTimeout(t)); timers.current = [] }
  useEffect(() => stop, [])
  const play = useCallback((m: Moment) => {
    const key = (seq.current += 1)
    setFx((cur) => [...cur.slice(-10), { ...m, key }])
    timers.current.push(window.setTimeout(() => setFx((cur) => cur.filter((x) => x.key !== key)), m.kind === 'strike' ? 900 : 2200))
    const by = name[m.by]; const vs = name[other(m.by)]
    const line = m.kind === 'strike' ? `${by} lands ${m.n} to the ${m.target}` : m.kind === 'kd' ? `KNOCKDOWN! ${by} drops ${vs}` : m.kind === 'td' ? `${by} takes ${vs} down` : m.kind === 'sub' ? `${by} goes for the submission` : m.kind === 'rev' ? `${by} reverses position` : m.kind === 'round' ? `Round ${m.round}` : m.kind === 'ko' ? `${by} wins by KO/TKO!` : m.kind === 'tap' ? `${vs} taps! ${by} wins by submission` : `${by} wins on the judges' cards`
    setFeed((cur) => [line, ...cur].slice(0, 5))
    if (m.kind !== 'round' && m.kind !== 'dec') setCount((c) => ({ ...c, [m.by]: { ...c[m.by], sig: c[m.by].sig + (m.kind === 'strike' ? m.n ?? 1 : 0), kd: c[m.by].kd + (m.kind === 'kd' ? 1 : 0), td: c[m.by].td + (m.kind === 'td' ? 1 : 0), sub: c[m.by].sub + (m.kind === 'sub' ? 1 : 0) } }))
    if (m.kind === 'td') setPose({ kind: 'ground', top: m.by, low: other(m.by) })
    else if (m.kind === 'rev') setPose({ kind: 'ground', top: m.by, low: other(m.by) })
    else if (m.kind === 'kd' || m.kind === 'ko') setPose({ kind: 'down', low: other(m.by), top: m.by })
    else if (m.kind === 'tap') setPose({ kind: 'ground', top: m.by, low: other(m.by) })
    else if (m.kind === 'round') setPose({ kind: 'stand' })
    if (m.kind === 'kd' || m.kind === 'td' || m.kind === 'rev') timers.current.push(window.setTimeout(() => setPose((p) => (p.kind === 'down' && m.kind === 'kd' ? { kind: 'stand' } : p.kind === 'ground' && m.kind !== 'kd' ? { kind: 'stand' } : p)), m.kind === 'kd' ? 1600 : 3200))
  }, [name.a, name.b]) // eslint-disable-line react-hooks/exhaustive-deps

  const replay = useCallback(() => {
    if (!d || !d.a.has_stats) return
    stop(); setFx([]); setFeed([]); setCount({ a: ZERO, b: ZERO }); setPose({ kind: 'stand' }); setPlaying(true)
    const { moments, end } = buildReplay(b, d)
    const ms = Math.min(48_000, Math.max(9_000, (end / 300) * 9_500))
    for (const m of moments) timers.current.push(window.setTimeout(() => { setClock(fightClock(Math.min(m.t, end))); play(m) }, (m.t / end) * ms))
    timers.current.push(window.setTimeout(() => { setPlaying(false); setClock(`${fightClock(end)} · FINAL`); setCount({ a: { sig: d.a.sig ?? 0, kd: d.a.kd ?? 0, td: d.a.td ?? 0, sub: d.a.sub ?? 0 }, b: { sig: d.b.sig ?? 0, kd: d.b.kd ?? 0, td: d.b.td ?? 0, sub: d.b.sub ?? 0 } }) }, ms + 2400))
  }, [b, d, play])

  // Finished fight: replay once when it opens. Live fight: animate each poll's changes.
  const prev = useRef<Detail | null>(null)
  useEffect(() => {
    if (!d) return
    const was = prev.current; prev.current = d
    if (d.state === 'in' || (was && was.state === 'in')) {
      const fromStats = { a: { sig: d.a.sig ?? 0, kd: d.a.kd ?? 0, td: d.a.td ?? 0, sub: d.a.sub ?? 0 }, b: { sig: d.b.sig ?? 0, kd: d.b.kd ?? 0, td: d.b.td ?? 0, sub: d.b.sub ?? 0 } }
      setClock(`R${d.period ?? 1} ${d.clock ?? ''}`)
      if (!was) { setCount(fromStats); return }
      const moments = diffMoments(was, d, b)
      moments.forEach((m) => timers.current.push(window.setTimeout(() => play(m), m.t * 1000)))
      timers.current.push(window.setTimeout(() => setCount(fromStats), moments.length * 1200 + 600))
    } else if (d.state === 'post' && !was) replay()
  }, [d]) // eslint-disable-line react-hooks/exhaustive-deps

  const pA = b.market_p_a
  const winner: Who | null = b.state === 'post' ? (b.a.winner ? 'a' : b.b.winner ? 'b' : null) : null
  const elapsed = b.state === 'in' || b.state === 'post' ? ((Math.max(1, b.period ?? 1) - 1) * 300 + clockSec(b.clock)) / (b.rounds * 300) : 0
  return (
    <article className={`sl-card fn-hero${b.title_fight ? ' is-title' : ''}`}>
      {b.title_fight ? <div className="fn-ribbon">🏆 UFC {b.weight_class.replace(/^W /, "Women's ").toUpperCase()} CHAMPIONSHIP · 5 ROUNDS</div> : null}
      <header className="fn-hero__head">
        <div className="fn-hero__event">
          <span className="fn-which" role="tablist" aria-label="Which card">
            <button type="button" role="tab" aria-selected={which === 'next'} className={which === 'next' ? 'is-on' : ''} onClick={() => onWhich('next')}>This weekend</button>
            <button type="button" role="tab" aria-selected={which === 'last'} className={which === 'last' ? 'is-on' : ''} onClick={() => onWhich('last')}>Last card's results</button>
          </span>
          <h2>{card.event.live ? <i className="fn-dot" /> : null}{card.event.name}</h2>
          <p>{etTime(card.event.date, { weekday: 'long', month: 'long', day: 'numeric' })}{card.event.venue ? ` · ${card.event.venue}` : ''}{card.event.city ? `, ${card.event.city}` : ''}{times.filter(([, t]) => t).map(([l, t]) => ` · ${l} ${etTime(t!)} ET`).join('')}</p>
        </div>
        <div className="fn-hero__bout">
          <b>{b.slot ?? b.card_segment}</b>
          <span>{b.weight_class.replace(/^W /, "Women's ")} · {b.rounds} rounds{b.rounds === 5 && !b.title_fight ? ' (non-title)' : ''}</span>
          <em>{b.state === 'pre' ? `${etTime(b.starts_at, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET` : b.state === 'in' ? `LIVE · R${b.period} ${b.clock ?? ''}` : `FINAL · ${b.method ?? ''}`}</em>
        </div>
      </header>

      <div className="fn-stage">
        <CornerCard f={A} c={b.a} color={RED} side="red" won={!playing && winner === 'a'} title={b.title_fight} />
        <div className="fn-arena">
          <Arena b={b} A={A} B={B} fx={fx} pose={pose} winner={playing ? null : winner} pA={pA} clock={clock} />
          <div className="fn-count" aria-label="Fight stats so far">
            {(['sig', 'kd', 'td', 'sub'] as const).map((k) => <span key={k}><b className="is-red">{count.a[k]}</b><small>{{ sig: 'sig. strikes', kd: 'knockdowns', td: 'takedowns', sub: 'sub attempts' }[k]}</small><b className="is-blue">{count.b[k]}</b></span>)}
          </div>
          <ol className="fn-feed" aria-live="polite">{feed.map((l, i) => <li key={`${i}-${l}`} className={i === 0 ? 'is-new' : ''}>{l}</li>)}</ol>
          <div className="fn-arena__foot">
            {d?.a.has_stats && b.state === 'post' ? <button type="button" className="mma-replay" onClick={replay}>{playing ? '↻ Restart replay' : '▶ Replay the fight'}</button> : null}
            {b.state === 'pre' ? <small>The cage comes alive at the opening bell: strikes, knockdowns, takedowns and submissions play here as ESPN's official counters move (checked every 10 seconds).</small> : null}
            {b.state === 'post' && d?.a.has_stats ? <small>Replay from the official fight totals, takedowns at ESPN's recorded times and the real finish. ESPN records no strike times, so where strikes fall inside the fight is illustrative.</small> : null}
            {b.state === 'post' && d?.a.judges.length ? <small className="fn-judges">Judges: {d.a.judges.map((s, i) => `${s}-${d.b.judges[i] ?? '?'}`).join(' · ')}</small> : null}
          </div>
        </div>
        <CornerCard f={B} c={b.b} color={BLUE} side="blue" won={!playing && winner === 'b'} title={b.title_fight} />
      </div>

      <div className="mma-rounds" aria-label="Rounds">
        {Array.from({ length: b.rounds }, (_, i) => <span key={i} className={`mma-rnd${b.state === 'post' && b.period && i + 1 > b.period ? ' is-unfought' : ''}`}><small>R{i + 1}{b.rounds === 5 && i >= 3 ? ' · championship' : ''}</small></span>)}
        {elapsed > 0 ? <i className="mma-rounds__fill" style={{ ['--w' as string]: `${Math.min(1, elapsed) * 100}%`, ['--c' as string]: winner === 'a' ? RED : winner === 'b' ? BLUE : GOLD } as CSSProperties} /> : null}
      </div>

      <div className="fn-cols">
        <Tape A={A} B={B} />
        <Battle A={A} B={B} />
      </div>
      <Styles A={A} B={B} b={b} />
    </article>
  )
}

const POS = { a: { x: 150, y: 250 }, b: { x: 250, y: 150 } }

function Arena({ b, A, B, fx, pose, winner, pA, clock }: { b: Bout; A: Fighter | undefined; B: Fighter | undefined; fx: Fx[]; pose: { kind: string; top?: Who; low?: Who }; winner: Who | null; pA: number | null; clock: string | null }) {
  const pts = (r: number) => Array.from({ length: 8 }, (_, i) => { const a = Math.PI / 8 + (i * Math.PI) / 4; return `${200 + r * Math.cos(a)},${200 + r * Math.sin(a)}` }).join(' ')
  const at = (w: Who) => {
    if (pose.kind === 'ground' && pose.top) return w === pose.top ? { x: 200, y: 196, s: 1, r: 0 } : { x: 200, y: 226, s: 0.92, r: w === 'a' ? -78 : 78 }
    if (pose.kind === 'down' && pose.low === w) return { ...POS[w], y: POS[w].y + 26, s: 0.85, r: w === 'a' ? -90 : 90 }
    return { ...POS[w], s: 1, r: 0 }
  }
  const color = { a: RED, b: BLUE }
  const accent = b.title_fight ? GOLD : '#94a3b8'
  const token = (w: Who, f: Fighter | undefined) => {
    const p = at(w)
    return (
      <g key={w} className={`fn-tok${winner === w ? ' is-won' : ''}${winner && winner !== w ? ' is-lost' : ''}`} style={{ transform: `translate(${p.x}px, ${p.y}px) rotate(${p.r}deg) scale(${p.s})` }}>
        <g className={fx.some((x) => x.kind === 'strike' && x.by !== w) ? 'fn-tok__hit' : undefined}>
          <circle r="30" fill={color[w]} />
          <clipPath id={`fn-clip-${w}`}><circle r="26" /></clipPath>
          <circle r="26" fill="#0b1526" />
          {f?.headshot ? <image href={f.headshot} x="-26" y="-30" width="52" height="52" clipPath={`url(#fn-clip-${w})`} preserveAspectRatio="xMidYMin slice" /> : <text y="7" textAnchor="middle" className="fn-tok__ini">{initials(f?.name ?? null)}</text>}
        </g>
      </g>
    )
  }
  return (
    <div className="mma-cage fn-cagebox">
      <svg viewBox="0 0 400 400" aria-label={`${A?.name ?? 'Red'} vs ${B?.name ?? 'Blue'} in the Octagon`}>
        <defs>
          <radialGradient id="fn-mat" cx="50%" cy="50%" r="50%"><stop offset="0" stopColor={b.title_fight ? '#2a2412' : '#1e2a3d'} /><stop offset="1" stopColor="#0b1526" /></radialGradient>
          <linearGradient id="fn-tug" x1="0" x2="1"><stop offset="0" stopColor={RED} /><stop offset={pA ?? 0.5} stopColor={RED} /><stop offset={pA ?? 0.5} stopColor={BLUE} /><stop offset="1" stopColor={BLUE} /></linearGradient>
        </defs>
        <polygon points={pts(170)} className="mma-cage__fence" style={{ stroke: accent }} />
        <polygon points={pts(150)} fill="url(#fn-mat)" className="mma-cage__mat" />
        <circle cx="200" cy="200" r="58" className="mma-cage__logo" style={b.title_fight ? { stroke: 'rgba(251,191,36,.45)' } : undefined} />
        <text x="200" y="196" textAnchor="middle" className="mma-cage__ufc" style={b.title_fight ? { fill: 'rgba(251,191,36,.45)' } : undefined}>UFC</text>
        <text x="200" y="214" textAnchor="middle" className="mma-cage__wc">{clock ?? `${b.rounds} × 5 MIN`}</text>
        <circle cx="88" cy="312" r="12" fill={RED} className="mma-cage__pad" />
        <circle cx="312" cy="88" r="12" fill={BLUE} className="mma-cage__pad" />
        {b.state === 'in' ? <circle cx="200" cy="200" r="160" className="fn-live-ring" /> : null}
        {pose.kind === 'ground' && pose.top ? <ellipse cx="200" cy="214" rx="64" ry="22" className="fn-mat-glow" /> : null}
        {pose.top === 'b' || pose.low === 'a' ? <>{token('a', A)}{token('b', B)}</> : <>{token('b', B)}{token('a', A)}</>}
        {fx.map((m) => <Effect key={m.key} m={m} pose={pose} at={at} color={color} />)}
        {pA != null ? <rect x="60" y="370" width="280" height="8" rx="4" fill="url(#fn-tug)" className="mma-cage__tug" /> : null}
      </svg>
    </div>
  )
}

function Effect({ m, at, color }: { m: Fx; pose: { kind: string }; at: (w: Who) => { x: number; y: number }; color: Record<Who, string> }) {
  const from = at(m.by); const to = at(other(m.by)); const c = color[m.by]
  const dy = m.target === 'head' ? -16 : m.target === 'leg' ? 18 : 2
  if (m.kind === 'strike') {
    const tx = to.x - (to.x - from.x) * 0.25; const ty = to.y - (to.y - from.y) * 0.25 + dy
    return (
      <g className="fn-fx fn-fx--strike" style={{ ['--c' as string]: c } as CSSProperties}>
        <line x1={from.x} y1={from.y} x2={tx} y2={ty} className="fn-fx__trail" />
        <g transform={`translate(${tx} ${ty})`}>{Array.from({ length: 8 }, (_, i) => <line key={i} x1="0" y1="0" x2={14 * Math.cos((i * Math.PI) / 4)} y2={14 * Math.sin((i * Math.PI) / 4)} className="fn-fx__spark" />)}<circle r="9" className="fn-fx__impact" /></g>
        <text x={tx + 18} y={ty - 10} className="fn-fx__n">+{m.n} {m.target}</text>
      </g>
    )
  }
  if (m.kind === 'kd' || m.kind === 'ko') {
    return (
      <g className={`fn-fx fn-fx--${m.kind}`} style={{ ['--c' as string]: c } as CSSProperties}>
        <circle cx={to.x} cy={to.y} r="20" className="fn-fx__shock" />
        <circle cx={to.x} cy={to.y} r="20" className="fn-fx__shock is-2" />
        <text x="200" y="120" textAnchor="middle" className="fn-fx__stamp">{m.kind === 'ko' ? 'KO!' : 'KNOCKDOWN!'}</text>
      </g>
    )
  }
  if (m.kind === 'td') return <g className="fn-fx fn-fx--td" style={{ ['--c' as string]: c } as CSSProperties}><path d="M 130 150 Q 200 110 270 150" className="fn-fx__arc" /><text x="200" y="120" textAnchor="middle" className="fn-fx__stamp is-small">TAKEDOWN</text></g>
  if (m.kind === 'sub' || m.kind === 'tap') {
    return (
      <g className={`fn-fx fn-fx--sub`} style={{ ['--c' as string]: c } as CSSProperties}>
        <circle cx="200" cy="210" r="56" className="fn-fx__lock" />
        <circle cx="200" cy="210" r="44" className="fn-fx__lock is-2" />
        <text x="200" y="120" textAnchor="middle" className={`fn-fx__stamp${m.kind === 'sub' ? ' is-small' : ''}`}>{m.kind === 'tap' ? 'TAP!' : 'SUBMISSION ATTEMPT'}</text>
      </g>
    )
  }
  if (m.kind === 'rev') return <g className="fn-fx fn-fx--td" style={{ ['--c' as string]: c } as CSSProperties}><text x="200" y="120" textAnchor="middle" className="fn-fx__stamp is-small">REVERSAL</text></g>
  if (m.kind === 'round') return <g className="fn-fx fn-fx--round"><text x="200" y="120" textAnchor="middle" className="fn-fx__stamp is-round">ROUND {m.round}</text></g>
  return <g className="fn-fx fn-fx--dec" style={{ ['--c' as string]: c } as CSSProperties}><text x="200" y="120" textAnchor="middle" className="fn-fx__stamp is-small">JUDGES' DECISION</text></g>
}

function Face({ f, color, size }: { f: Fighter | undefined; color: string; size: number }) {
  const [broken, setBroken] = useState(false)
  return (
    <span className="fn-face" style={{ width: size, height: size, ['--c' as string]: color } as CSSProperties}>
      {f?.headshot && !broken ? <img src={f.headshot} alt="" onError={() => setBroken(true)} /> : <b style={{ fontSize: size * 0.34 }}>{initials(f?.name ?? null)}</b>}
      {f?.flag ? <img className="fn-flag" src={f.flag} alt={f.country ?? ''} title={f.country ?? ''} /> : null}
    </span>
  )
}

function CornerCard({ f, c, color, side, won, title }: { f: Fighter | undefined; c: Corner; color: string; side: 'red' | 'blue'; won: boolean; title: boolean }) {
  const cur = c.odds.american; const open = c.odds.open
  const moved = cur != null && open != null && cur !== open ? (implied(cur)! > implied(open)! ? 'in' : 'out') : null
  return (
    <div className={`mma-corner is-${side}${won ? ' is-won' : ''}`}>
      <Face f={f} color={color} size={96} />
      <span className="fn-badges">
        {f?.champion ? <em className="fn-badge is-champ">{title ? '🏆 CHAMPION' : 'CHAMPION'}</em> : f?.rank ? <em className="fn-badge">#{f.rank} ranked</em> : <em className="fn-badge is-un">unranked</em>}
      </span>
      <b>{f?.name ?? 'TBA'}</b>
      <small>{f?.nickname ? `"${f.nickname}" · ` : ''}{c.record ?? '—'}</small>
      <span className="fn-odds" style={{ ['--c' as string]: color } as CSSProperties}>
        <em>{am(cur)}</em>
        <small>{cur == null ? 'no line yet' : `${pct(implied(cur))} implied`}</small>
        {open != null ? <small className={`fn-move${moved ? ` is-${moved}` : ''}`}>opened {am(open)}{moved === 'in' ? ' ▲ money coming in' : moved === 'out' ? ' ▼ drifting' : ''}</small> : null}
      </span>
      {won ? <strong className="mma-w">WINNER</strong> : null}
    </div>
  )
}

function Tape({ A, B }: { A: Fighter | undefined; B: Fighter | undefined }) {
  // [label, red text, blue text, red value, blue value, bigger is the edge?]
  const rows: Array<[string, string, string, number | null, number | null, boolean | null]> = [
    ['Age', A?.age != null ? String(A.age) : '—', B?.age != null ? String(B.age) : '—', A?.age ?? null, B?.age ?? null, false],
    ['Height', A?.height ?? '—', B?.height ?? '—', A?.height_in ?? null, B?.height_in ?? null, true],
    ['Reach', A?.reach ?? '—', B?.reach ?? '—', A?.reach_in ?? null, B?.reach_in ?? null, true],
    ['Weight', A?.weight ?? '—', B?.weight ?? '—', null, null, null],
    ['Stance', A?.stance ?? '—', B?.stance ?? '—', null, null, null],
    ['Style', A?.styles.join(', ') || '—', B?.styles.join(', ') || '—', null, null, null],
    ['Team', A?.gym ?? '—', B?.gym ?? '—', null, null, null],
    ['From', A?.country ?? '—', B?.country ?? '—', null, null, null],
  ]
  return (
    <section className="fn-tape" aria-label="Tale of the tape">
      <h3>Tale of the Tape</h3>
      {rows.map(([label, rt, bt, rv, bv, big]) => {
        const edge = big == null || rv == null || bv == null || rv === bv ? null : (big ? rv > bv : rv < bv) ? 'red' : 'blue'
        return <div key={label} className="fn-tape__row"><b className={edge === 'red' ? 'is-edge is-red' : ''}>{rt}</b><span>{label}</span><b className={edge === 'blue' ? 'is-edge is-blue' : ''}>{bt}</b></div>
      })}
    </section>
  )
}

function Battle({ A, B }: { A: Fighter | undefined; B: Fighter | undefined }) {
  const rows: Array<[string, keyof Stats, (v: number) => string]> = [
    ['Sig. strikes landed / min', 'slpm', (v) => v.toFixed(2)],
    ['Striking accuracy', 'str_acc', (v) => `${Math.round(v)}%`],
    ['Takedowns / 15 min', 'td_avg', (v) => v.toFixed(2)],
    ['Takedown accuracy', 'td_acc', (v) => `${Math.round(v)}%`],
    ['Submission attempts / 15 min', 'sub_avg', (v) => v.toFixed(2)],
  ]
  return (
    <section className="fn-battle" aria-label="Career stats">
      <h3>Career numbers</h3>
      {rows.map(([label, k, f]) => {
        const r = A?.stats[k] ?? null; const bl = B?.stats[k] ?? null
        const tot = Math.max(0.0001, (r ?? 0) + (bl ?? 0))
        return (
          <div key={k} className="mma-bar">
            <b className="is-red">{r == null ? '—' : f(r)}</b>
            <span className="mma-bar__track"><i className="is-red" style={{ width: `${((r ?? 0) / tot) * 50}%` }} /><i className="is-blue" style={{ width: `${((bl ?? 0) / tot) * 50}%` }} /><small>{label}</small></span>
            <b className="is-blue">{bl == null ? '—' : f(bl)}</b>
          </div>
        )
      })}
    </section>
  )
}

function Styles({ A, B, b }: { A: Fighter | undefined; B: Fighter | undefined; b: Bout }) {
  const reach = A?.reach_in != null && B?.reach_in != null ? A.reach_in - B.reach_in : null
  const fav = b.market_p_a == null ? null : b.market_p_a >= 0.5 ? A : B
  const favP = b.market_p_a == null ? null : Math.max(b.market_p_a, 1 - b.market_p_a)
  const td = (f: Fighter | undefined) => f?.stats.td_avg ?? 0
  const grappler = td(A) >= 2 || td(B) >= 2 ? (td(A) >= td(B) ? A : B) : null
  const otherF = grappler === A ? B : A
  const lines = [
    fav && favP != null ? (favP < 0.56 ? `The books see a near coin flip: ${last(fav.name)} is only a slight favourite at ${pct(favP)}.` : `${last(fav.name)} is the betting favourite at ${pct(favP)}.`) : null,
    reach != null && Math.abs(reach) >= 2 ? `${last((reach > 0 ? A : B)?.name)} has a ${Math.abs(reach)}" reach advantage, so watch who controls the distance.` : null,
    grappler ? `${last(grappler.name)} averages ${td(grappler).toFixed(1)} takedowns per 15 minutes. The question is whether ${last(otherF?.name)} can keep it standing.` : 'Neither fighter leans on takedowns. Expect this one to be decided on the feet.',
    A?.stance && B?.stance && A.stance !== B.stance ? `${A.stance} vs ${B.stance}: an open-stance fight, where the lead-leg kick and the power straight are the big shots.` : null,
  ].filter(Boolean)
  return (
    <section className="fn-styles" aria-label="Fighting styles">
      <div className="fn-style is-red"><small>{A?.name ?? 'Red corner'}</small><b>{A?.style_read ?? 'Not enough UFC data yet'}</b></div>
      <div className="fn-style is-blue"><small>{B?.name ?? 'Blue corner'}</small><b>{B?.style_read ?? 'Not enough UFC data yet'}</b></div>
      <ul className="fn-read">{lines.map((l) => <li key={l}>{l}</li>)}</ul>
    </section>
  )
}
