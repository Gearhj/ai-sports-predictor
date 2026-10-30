/**
 * FIGHT NIGHT LIVE (owner 10-30: "I'd like to see the tale of the tape, the octagon, the fighter's stats, info on
 * fighting style, info on odds for each fighter, a golden card if this is a title fight").
 *
 * This weekend's real UFC card from /preview-api/ufc/card (ESPN public feeds + UFC.com rankings): every bout in a
 * picker, and the chosen bout in the cage with both corners walking out, the market split filling the tug bar, the
 * tale of the tape with the edge on each line lit, career striking and grappling battle bars, a plain-language style
 * read and matchup read, and the opening and current moneyline with which way the money moved. A title fight turns
 * the whole card gold. While a bout is live the round strip fills with the clock; a finished bout shows the method
 * and bursts the winner's corner. The odds are the sportsbook's, information only: AISP has no UFC model or wager.
 */
import { useEffect, useMemo, useState, type CSSProperties } from 'react'

type Stats = { slpm: number | null; str_acc: number | null; td_avg: number | null; td_acc: number | null; sub_avg: number | null }
type Fighter = { id: string; name: string | null; nickname: string | null; headshot: string | null; flag: string | null; country: string | null; age: number | null; height: string | null; height_in: number | null; reach: string | null; reach_in: number | null; weight: string | null; stance: string | null; styles: string[]; gym: string | null; stats: Stats; style_read: string | null; champion: boolean; rank: number | null }
type Corner = { id: string; record: string | null; winner: boolean; odds: { american?: number | null; open?: number | null } }
type Bout = { id: string; slot: string | null; weight_class: string; rounds: number; title_fight: boolean; starts_at: string; state: 'pre' | 'in' | 'post' | string; status: string | null; period: number | null; clock: string | null; method: string | null; card_segment: string; a: Corner; b: Corner; market_p_a: number | null; odds_provider: string | null }
export type UfcCard = { found: boolean; reason?: string; retrieved_at?: string; event: { id: string; name: string; date: string; venue: string | null; city: string | null; live: boolean; complete: boolean }; bouts: Bout[]; fighters: Record<string, Fighter>; notes: string[] }

const RED = '#ef4444'; const BLUE = '#3b82f6'; const GOLD = '#fbbf24'
const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : String(v))
const implied = (v: number | null | undefined) => (v == null ? null : v > 0 ? 100 / (v + 100) : -v / (-v + 100))
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const etTime = (iso: string, o: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit' }) => new Date(iso).toLocaleString('en-US', { timeZone: 'America/New_York', ...o })
const last = (n: string | null | undefined) => (n ?? '?').split(' ').slice(-1)[0]
const initials = (n: string | null) => (n ?? '?').split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase()

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

export function FightNight({ card, which, onWhich }: { card: UfcCard; which: 'next' | 'last'; onWhich: (w: 'next' | 'last') => void }) {
  const [boutId, setBoutId] = useState<string | null>(null)
  const [run, setRun] = useState(0)
  const live = card.bouts.find((b) => b.state === 'in')
  const bout = card.bouts.find((b) => b.id === boutId) ?? live ?? card.bouts[0]
  const segments = useMemo(() => ['Main card', 'Prelims'].map((s) => ({ s, bouts: card.bouts.filter((b) => b.card_segment === s) })).filter((g) => g.bouts.length), [card])
  const first = (s: string) => card.bouts.filter((b) => b.card_segment === s).map((b) => b.starts_at).sort()[0]
  if (!bout) return null
  return (
    <section className="fn" aria-label="Fight night">
      <header className="fn-head">
        <div>
          <span className="fn-which" role="tablist" aria-label="Which card">
            <button type="button" role="tab" aria-selected={which === 'next'} className={which === 'next' ? 'is-on' : ''} onClick={() => onWhich('next')}>This weekend</button>
            <button type="button" role="tab" aria-selected={which === 'last'} className={which === 'last' ? 'is-on' : ''} onClick={() => onWhich('last')}>Last card's results</button>
          </span>
          <small>{card.event.live ? <><i className="fn-dot" /> LIVE NOW</> : card.event.complete ? 'FINAL RESULTS' : 'THIS WEEKEND'}</small>
          <h2>{card.event.name}</h2>
          <p>{etTime(card.event.date, { weekday: 'long', month: 'long', day: 'numeric' })}{card.event.venue ? ` · ${card.event.venue}` : ''}{card.event.city ? `, ${card.event.city}` : ''}</p>
        </div>
        <ul className="fn-times">
          {first('Prelims') ? <li><small>Prelims</small><b>{etTime(first('Prelims'))} ET</b></li> : null}
          {first('Main card') ? <li><small>Main card</small><b>{etTime(first('Main card'))} ET</b></li> : null}
          <li><small>Fights</small><b>{card.bouts.length}</b></li>
        </ul>
      </header>
      <div className="fn-grid">
        <Cage key={`${bout.id}-${run}`} b={bout} F={card.fighters} onReplay={() => setRun((r) => r + 1)} />
        <aside className="fn-list" aria-label="The card">
          {segments.map(({ s, bouts }) => (
            <div key={s}>
              <h4>{s}</h4>
              <ol>
                {bouts.map((b) => {
                  const A = card.fighters[b.a.id]; const B = card.fighters[b.b.id]
                  return (
                    <li key={b.id}>
                      <button type="button" className={`fn-row${b.id === bout.id ? ' is-on' : ''}${b.title_fight ? ' is-title' : ''}`} onClick={() => { setBoutId(b.id); setRun((r) => r + 1) }}>
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
          <small className="fn-src">Card, results and fighter data: ESPN. Rankings: UFC.com. Odds: {bout.odds_provider ?? 'the sportsbook ESPN lists'}, information only (AISP has no UFC model or wager).</small>
        </aside>
      </div>
    </section>
  )
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

function Cage({ b, F, onReplay }: { b: Bout; F: Record<string, Fighter>; onReplay: () => void }) {
  const A = F[b.a.id]; const B = F[b.b.id]
  const pA = b.market_p_a
  const winner = b.state === 'post' ? (b.a.winner ? 'a' : b.b.winner ? 'b' : null) : null
  const [mm, ss] = (b.clock ?? '0:00').split(':').map(Number)
  const elapsed = b.state === 'in' || b.state === 'post' ? ((Math.max(1, b.period ?? 1) - 1) * 300 + (mm || 0) * 60 + (ss || 0)) / (b.rounds * 300) : 0
  const pts = (r: number) => Array.from({ length: 8 }, (_, i) => { const a = Math.PI / 8 + (i * Math.PI) / 4; return `${200 + r * Math.cos(a)},${200 + r * Math.sin(a)}` }).join(' ')
  const accent = b.title_fight ? GOLD : '#94a3b8'
  return (
    <article className={`sl-card fn-cage${b.title_fight ? ' is-title' : ''}`}>
      {b.title_fight ? <div className="fn-ribbon">🏆 UFC {b.weight_class.replace(/^W /, "Women's ").toUpperCase()} CHAMPIONSHIP · 5 ROUNDS</div> : null}
      <header className="sl-card__head">
        <h2>{b.slot ? `${b.slot} · ` : ''}{b.weight_class.replace(/^W /, "Women's ")} <small>{b.rounds} rounds{b.rounds === 5 && !b.title_fight ? ' (main event, non-title)' : ''} · {b.state === 'pre' ? `${etTime(b.starts_at, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET` : b.status}</small></h2>
        <button type="button" className="mma-replay" onClick={onReplay}>↻ Walk them out again</button>
      </header>
      <div className="fn-stage">
        <CornerCard f={A} c={b.a} color={RED} side="red" won={winner === 'a'} title={b.title_fight} />
        <div className="mma-cage">
          <svg viewBox="0 0 400 400" aria-hidden="true">
            <defs>
              <radialGradient id="fn-mat" cx="50%" cy="50%" r="50%"><stop offset="0" stopColor={b.title_fight ? '#2a2412' : '#1e2a3d'} /><stop offset="1" stopColor="#0b1526" /></radialGradient>
              <linearGradient id="fn-tug" x1="0" x2="1"><stop offset="0" stopColor={RED} /><stop offset={pA ?? 0.5} stopColor={RED} /><stop offset={pA ?? 0.5} stopColor={BLUE} /><stop offset="1" stopColor={BLUE} /></linearGradient>
            </defs>
            <polygon points={pts(170)} className="mma-cage__fence" style={{ stroke: accent }} />
            <polygon points={pts(150)} fill="url(#fn-mat)" className="mma-cage__mat" />
            <circle cx="200" cy="200" r="58" className="mma-cage__logo" style={b.title_fight ? { stroke: 'rgba(251,191,36,.45)' } : undefined} />
            <text x="200" y="192" textAnchor="middle" className="mma-cage__ufc" style={b.title_fight ? { fill: 'rgba(251,191,36,.5)' } : undefined}>UFC</text>
            <text x="200" y="212" textAnchor="middle" className="mma-cage__wc">{b.weight_class.replace(/^W /, "WOMEN'S ").toUpperCase()}</text>
            <text x="200" y="230" textAnchor="middle" className="mma-cage__wc">{b.state === 'in' ? `ROUND ${b.period} · ${b.clock}` : b.state === 'post' ? (b.method ?? 'FINAL').toUpperCase() : `${b.rounds} × 5 MIN`}</text>
            <circle cx="88" cy="312" r="16" fill={RED} className="mma-cage__pad fn-pad" />
            <circle cx="312" cy="88" r="16" fill={BLUE} className="mma-cage__pad fn-pad" />
            {b.state === 'in' ? <circle cx="200" cy="200" r="70" className="fn-live-ring" /> : null}
            {pA != null ? <rect x="60" y="356" width="280" height="10" rx="5" fill="url(#fn-tug)" className="mma-cage__tug" /> : null}
            {winner ? <g className="mma-cage__burst" style={{ ['--c' as string]: winner === 'a' ? RED : BLUE } as CSSProperties}>{Array.from({ length: 12 }, (_, i) => { const x = winner === 'a' ? 88 : 312; const y = winner === 'a' ? 312 : 88; return <line key={i} x1={x} y1={y} x2={x + 46 * Math.cos((i * Math.PI) / 6)} y2={y + 46 * Math.sin((i * Math.PI) / 6)} /> })}</g> : null}
          </svg>
          <div className="fn-split">{pA != null ? <><b className="is-red">{pct(pA)}</b><small>market win chance (no-vig)</small><b className="is-blue">{pct(1 - pA)}</b></> : <small>No sportsbook line posted yet</small>}</div>
        </div>
        <CornerCard f={B} c={b.b} color={BLUE} side="blue" won={winner === 'b'} title={b.title_fight} />
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

function CornerCard({ f, c, color, side, won, title }: { f: Fighter | undefined; c: Corner; color: string; side: 'red' | 'blue'; won: boolean; title: boolean }) {
  const cur = c.odds.american; const open = c.odds.open
  const moved = cur != null && open != null && cur !== open ? (implied(cur)! > implied(open)! ? 'in' : 'out') : null
  return (
    <div className={`mma-corner is-${side}${won ? ' is-won' : ''}`}>
      <Face f={f} color={color} size={104} />
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
  const other = grappler === A ? B : A
  const lines = [
    fav && favP != null ? (favP < 0.56 ? `The books see a near coin flip: ${last(fav.name)} is only a slight favourite at ${pct(favP)}.` : `${last(fav.name)} is the betting favourite at ${pct(favP)}.`) : null,
    reach != null && Math.abs(reach) >= 2 ? `${last((reach > 0 ? A : B)?.name)} has a ${Math.abs(reach)}" reach advantage, so watch who controls the distance.` : null,
    grappler ? `${last(grappler.name)} averages ${td(grappler).toFixed(1)} takedowns per 15 minutes. The question is whether ${last(other?.name)} can keep it standing.` : 'Neither fighter leans on takedowns. Expect this one to be decided on the feet.',
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
