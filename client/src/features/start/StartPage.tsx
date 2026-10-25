/**
 * START HERE (2026-10-25): the answer to "how would a first-time user know where to go and what to do?"
 * The whole platform drawn as one live map: every page is a station in the column of what you want to do
 * (Tonight, Your play, Can I trust it?, Dig deeper), each with tonight's number on it, and three trails that
 * light the route for the three things people come to AISP for. The left menu reuses the same questions,
 * so the map and the menu always agree.
 */
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AispSide, AISP_NAV, NAV_ITEMS, type NavItem } from '../flow/AispSide'
import { pickOf } from '../flow/slip'
import { useMeasuredWidth, useReducedMotion } from '../../components/charts/d3Charts'
import { operationalToday } from '../../services/operationalDate'
import { usePreviewPicks } from '../../services/previewPicks'
import { useMissionControl } from '../mission/missionData'
import { allPositions, resultOf, usePulse } from '../pulse/pulseData'
import '../slate/slate.css'
import './start.css'

export const START_SEEN_KEY = 'aisp.start.seen'

type Trail = { key: string; icon: string; title: string; who: string; steps: Array<{ to: string; why: string }> }

const TRAILS: Trail[] = [
  { key: 'play', icon: '✦', title: "Find tonight's best plays", who: 'You want to know who to back tonight.', steps: [
    { to: '/', why: "See every game on today's slate and the plays AISP likes most." },
    { to: '/games', why: "Sort by edge: where the model's price beats the sportsbooks'. Open any game for the full story." },
    { to: '/markets', why: 'Shop the line: which book has the best price, and has the money moved?' },
    { to: '/my-picks', why: 'Add your picks to the slip and lock them before the start.' },
    { to: '/live', why: 'Watch them play out, play by play.' },
  ] },
  { key: 'watch', icon: '◉', title: 'Watch tonight live', who: 'Games are on and you want to follow them.', steps: [
    { to: '/mission-control', why: 'The whole night as one flow: what is ready, what AISP bet, what has finished.' },
    { to: '/game-night', why: 'Every game at once on one wall, TV-style, with the bets riding on them.' },
    { to: '/live', why: 'Zoom into one game: the field, the plays and the win-probability river.' },
    { to: '/experiment', why: 'When the games end, see the night land on the bankroll.' },
  ] },
  { key: 'trust', icon: '◎', title: 'Is AISP any good?', who: 'You want proof before you trust a number.', steps: [
    { to: '/experiment', why: 'The paper bankroll: every bet AISP made, won or lost, and whether it beat the closing line.' },
    { to: '/performance', why: 'Model against market: accuracy, calibration, and where it is better or worse.' },
    { to: '/decisions', why: 'Why AISP bet or passed on each game, gate by gate.' },
    { to: '/methodology', why: 'How the numbers are built, in plain English.' },
  ] },
]

const WORDS: Array<[string, string]> = [
  ['Edge', "How many points AISP's win chance is above the chance the sportsbook's price implies. 3+ points is worth a look."],
  ['No-vig price', "The sportsbook's odds with its built-in margin removed: the market's honest guess."],
  ['Beat the close', 'Getting a better price than the final one before the start. The best long-run sign of real skill.'],
  ['Paper bet', 'A tracked bet with no real money. Everything on AISP is research and paper only.'],
  ['Readiness', 'Whether a game has a fresh forecast and fresh prices, so AISP is allowed to decide.'],
  ['Calibration', 'When AISP says 60%, does it happen about 60% of the time?'],
]

const COLS = AISP_NAV.map((g) => g.title)
const colOf = (to: string) => AISP_NAV.findIndex((g) => g.items.some((it) => it.to === to))
const rowOf = (to: string) => AISP_NAV[colOf(to)]?.items.findIndex((it) => it.to === to) ?? 0
const byTo = new Map(NAV_ITEMS.map((it) => [it.to, it]))

function useStats(): Map<string, string> {
  const today = operationalToday()
  const pulse = usePulse(today)
  const mission = useMissionControl(today)
  const picks = usePreviewPicks()
  return useMemo(() => {
    const games = pulse.games.filter((g) => g.day === today)
    const live = games.filter((g) => g.phase === 'LIVE' || g.phase === 'STARTED').length
    const edges = games.filter((g) => g.phase === 'UPCOMING' && (pickOf(g)?.edge ?? -1) >= 3).length
    const mg = mission.data?.games ?? []
    const upcoming = mg.filter((g) => g.phase === 'PREGAME' || g.phase === 'DECISION_WINDOW')
    const ready = upcoming.filter((g) => g.stages.forecast?.state === 'DONE' && g.stages.prices?.state === 'DONE').length
    const pos = allPositions(pulse.ledger)
    const w = pos.filter((p) => resultOf(p) === 'WIN').length
    const l = pos.filter((p) => resultOf(p) === 'LOSS').length
    const open = pos.filter((p) => resultOf(p) === 'OPEN').length
    const m = new Map<string, string>()
    if (games.length) m.set('/', `${games.length} games today`)
    m.set('/games', edges ? `${edges} edge${edges > 1 ? 's' : ''} of 3+ pts` : 'no 3-pt edges yet')
    if (mission.data) m.set('/mission-control', upcoming.length ? `${ready}/${upcoming.length} ready` : `${mg.length} games tracked`)
    if (live) { m.set('/game-night', `${live} live now`); m.set('/live', `${live} live now`) }
    const slip = picks.filter((p) => p.status !== 'locked').length
    m.set('/my-picks', slip ? `${slip} on your slip` : 'slip is empty')
    if (w + l) m.set('/experiment', `${w}–${l}${open ? ` · ${open} open` : ''}`)
    return m
  }, [pulse.games, pulse.ledger, mission.data, picks, today])
}

export function StartPage() {
  const [trail, setTrail] = useState<string>('play')
  const t = TRAILS.find((x) => x.key === trail) ?? TRAILS[0]
  useEffect(() => { try { localStorage.setItem(START_SEEN_KEY, '1') } catch { /* private window: the nudge just shows again */ } }, [])
  return (
    <div className="hq st">
      <AispSide />
      <div className="hq-main">
        <header className="sl-top">
          <div className="sl-title"><small>START HERE</small><h1>The map of AISP</h1><p>{NAV_ITEMS.length} pages, three reasons to be here. Pick yours and follow the light.</p></div>
        </header>
        <main className="st-body">
          <section className="st-trails" aria-label="Choose what you want to do">
            {TRAILS.map((x) => (
              <button key={x.key} type="button" className={`st-trail${x.key === trail ? ' is-on' : ''}`} aria-pressed={x.key === trail} onClick={() => setTrail(x.key)}>
                <i aria-hidden="true">{x.icon}</i><span><b>{x.title}</b><small>{x.who}</small></span>
              </button>
            ))}
          </section>
          <section className="st-mapcard" aria-label="Map of every page">
            <PlatformMap trail={t} />
          </section>
          <section className="st-steps" aria-label={`Steps: ${t.title}`}>
            <h2>{t.icon} {t.title}</h2>
            <ol key={t.key}>
              {t.steps.map((s, i) => {
                const it = byTo.get(s.to)
                return (
                  <li key={s.to + i} style={{ ['--i' as string]: i } as CSSProperties}>
                    <em>{i + 1}</em>
                    <span><b>{it?.label ?? s.to}</b><small>{s.why}</small></span>
                    <Link to={s.to} className="st-go">Go →</Link>
                  </li>
                )
              })}
            </ol>
          </section>
          <section className="st-lower">
            <div className="st-card">
              <h2>Six words you will see everywhere</h2>
              <dl className="st-words">{WORDS.map(([w, d]) => <div key={w}><dt>{w}</dt><dd>{d}</dd></div>)}</dl>
            </div>
            <div className="st-card st-what">
              <h2>What AISP is, and is not</h2>
              <ul>
                <li><b>It is</b> a research desk: an AI model priced against the sportsbooks on NFL, college football, MLB, NBA and NHL, every game, every night.</li>
                <li><b>It is</b> honest about its record. Every paper bet is kept, and the model is graded against the market, not against a coin.</li>
                <li><b>It is not</b> a sportsbook. Nothing here takes or places real money.</li>
                <li><b>Lost?</b> The left menu always shows <i>You are here</i>: the question the page answers and the next stop.</li>
              </ul>
            </div>
          </section>
        </main>
        <footer className="hq-foot">Research and paper only. No real-money wagering.</footer>
      </div>
    </div>
  )
}

/** The map: four columns of stations, every page's usual next stop as a faint route, the chosen trail lit. */
function PlatformMap({ trail }: { trail: Trail }) {
  const [ref, width] = useMeasuredWidth<HTMLDivElement>(1000)
  const reduced = useReducedMotion()
  const stats = useStats()
  const navigate = useNavigate()
  const [hover, setHover] = useState<NavItem | null>(null)
  const narrow = width < 720
  const nodeW = narrow ? Math.min(150, (width - 60) / 2) : Math.min(180, (width - 40) / 4 - 40)
  const nodeH = 44
  const rowGap = narrow ? 54 : 62
  const head = 34

  // stations: desktop = four columns; narrow = two columns (Tonight + Your play | Trust + Dig deeper)
  const pos = useMemo(() => {
    const m = new Map<string, { x: number; y: number; col: number }>()
    NAV_ITEMS.forEach((it) => {
      const c = colOf(it.to), r = rowOf(it.to)
      if (!narrow) {
        const colW = (width - 40) / 4
        m.set(it.to, { x: 20 + c * colW + (colW - nodeW) / 2 - 10, y: head + 14 + r * rowGap, col: c })
      } else {
        const side = c < 2 ? 0 : 1
        const offset = c === 1 ? AISP_NAV[0].items.length + 1 : c === 3 ? AISP_NAV[2].items.length + 1 : 0
        const colW = (width - 20) / 2
        m.set(it.to, { x: 10 + side * colW + (colW - nodeW) / 2 - 8, y: head + 14 + (offset + r) * rowGap, col: side * 10 + c })
      }
    })
    return m
  }, [width, narrow, nodeW, rowGap])
  const height = Math.max(...[...pos.values()].map((p) => p.y)) + nodeH + 24

  const route = (a: string, b: string): string | null => {
    const p = pos.get(a), q = pos.get(b)
    if (!p || !q) return null
    const ay = p.y + nodeH / 2, by = q.y + nodeH / 2
    if (Math.abs(p.x - q.x) < 2) {
      // same column: loop out to the right of the stations
      const x = p.x + nodeW, bulge = 22 + Math.min(40, Math.abs(by - ay) * 0.12)
      return `M${x},${ay} C${x + bulge},${ay} ${x + bulge},${by} ${x},${by}`
    }
    const fwd = q.x > p.x
    const x0 = fwd ? p.x + nodeW : p.x, x1 = fwd ? q.x : q.x + nodeW
    const s = fwd ? 1 : -1
    const span = Math.abs(x1 - x0)
    if (span > nodeW * 1.6) {
      // skipping a column: run along the empty channel under the higher of the two rows, so no station is crossed
      const gap = Math.min(p.y, q.y) + nodeH + (rowGap - nodeH) / 2
      const r = 16
      return `M${x0},${ay} C${x0 + s * r},${ay} ${x0 + s * r},${gap} ${x0 + s * 2 * r},${gap} L${x1 - s * 2 * r},${gap} C${x1 - s * r},${gap} ${x1 - s * r},${by} ${x1},${by}`
    }
    const mx = (x0 + x1) / 2
    return `M${x0},${ay} C${mx},${ay} ${mx},${by} ${x1},${by}`
  }

  const lit = new Set(trail.steps.map((s) => s.to))
  const legs = trail.steps.slice(1).map((s, i) => [trail.steps[i].to, s.to] as const)
  const usual = NAV_ITEMS.filter((it) => it.next && byTo.has(it.next)).map((it) => [it.to, it.next as string] as const)
  const stepNo = (to: string) => trail.steps.findIndex((s) => s.to === to)

  return (
    <div ref={ref} className="st-map">
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label={`Map of AISP's pages with the "${trail.title}" trail highlighted`}>
        <defs>
          <filter id="st-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        {!narrow ? COLS.map((title, c) => {
          const colW = (width - 40) / 4
          return (
            <g key={title}>
              <rect x={20 + c * colW + 6} y={4} width={colW - 12} height={height - 8} rx={14} className={`st-lane st-lane--${c}`} />
              <text x={20 + c * colW + colW / 2} y={24} textAnchor="middle" className="st-lane__t">{title}</text>
            </g>
          )
        }) : (
          <>
            <text x={width / 4} y={22} textAnchor="middle" className="st-lane__t">TONIGHT · YOUR PLAY</text>
            <text x={(3 * width) / 4} y={22} textAnchor="middle" className="st-lane__t">TRUST IT · DIG DEEPER</text>
          </>
        )}
        <g className="st-usual">{usual.map(([a, b]) => { const d = route(a, b); return d ? <path key={`${a}>${b}`} d={d} /> : null })}</g>
        <g className="st-legs" key={trail.key}>
          {legs.map(([a, b], i) => {
            const d = route(a, b)
            return d ? (
              <g key={`${a}>${b}`}>
                <path d={d} className="st-leg__glow" filter="url(#st-glow)" />
                <path d={d} className={`st-leg${reduced ? '' : ' is-flow'}`} style={{ animationDelay: `${i * 0.35}s` }} />
              </g>
            ) : null
          })}
        </g>
        {NAV_ITEMS.map((it) => {
          const p = pos.get(it.to)
          if (!p) return null
          const n = stepNo(it.to)
          const on = lit.has(it.to)
          const stat = stats.get(it.to)
          return (
            <g key={it.to} transform={`translate(${p.x},${p.y})`} className={`st-node${on ? ' is-lit' : ''}`} tabIndex={0} role="link" aria-label={`${it.label}: ${it.ask}`}
              onClick={() => navigate(it.to)} onKeyDown={(e) => { if (e.key === 'Enter') navigate(it.to) }}
              onMouseEnter={() => setHover(it)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(it)} onBlur={() => setHover(null)}>
              <rect width={nodeW} height={nodeH} rx={12} />
              <text x={12} y={stat ? 19 : 27} className="st-node__t">{it.icon} {it.label}</text>
              {stat ? <text x={12} y={35} className="st-node__s">{stat}</text> : null}
              {n >= 0 ? <g transform={`translate(${nodeW},0)`}><circle r={11} className="st-node__n" /><text textAnchor="middle" y={4} className="st-node__nt">{n + 1}</text></g> : null}
            </g>
          )
        })}
      </svg>
      {hover ? (() => {
        const p = pos.get(hover.to)
        if (!p) return null
        const left = Math.min(Math.max(8, p.x + nodeW / 2 - 130), width - 268)
        return <div className="st-tip" style={{ left, top: p.y + nodeH + 10 }}><b>{hover.label}</b><span>{hover.ask}</span><small>Click to open</small></div>
      })() : null}
    </div>
  )
}
