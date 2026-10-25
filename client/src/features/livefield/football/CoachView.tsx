/**
 * COACH'S VIEW: "what does this coach call in this spot?" in three steps a fan can follow.
 *  1. SITUATIONS: every down-and-distance spot this game as a tile (pass share vs a typical coach), the
 *     spot of the snap on the field marked NOW.
 *  2. THE CLIPBOARD: the latest play from the chosen spot drawn as X's and O's in marker (routes, blocks,
 *     drops, the ball's flight); step through the others.
 *  3. THE ANSWER: what he called there and how it worked, the concepts he leans on in that spot, and the
 *     game-wide read (pass rate vs a typical coach, tempo, script, fourth downs).
 * Positions are drawn from the play text, not tracked. Descriptive, never a wager signal.
 */
import { useEffect, useMemo, useState } from 'react'
import { buildScene, sample, type Scene } from './scene'
import { parseFbPlay, visibleColor, type FbPlay, type FbTeam } from './footballData'
import type { EpaPlay, EpGrid } from './ep'
import type { FourthTables } from './fourth'
import type { Grid } from './playcall'
import { coachProfile, coachRead, situationOf, type SituationRow } from './coach'
import { categoryProbs, classOfPlay, predict, stateForPlay } from './playcall'

const PW = 560
const PH = 640
const LOS_Y = 468
const K = 8.9
const PX = (x: number) => PW / 2 + x * K
const PY = (d: number) => LOS_Y - d * K
const pctf = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? '—' : `${Math.round(v * 100)}%`)
const signed = (v: number | null | undefined, d = 2) => (v == null || !Number.isFinite(v) ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}`)
const pts = (arr: Array<{ x: number; d: number }>) => arr.map((q) => `${PX(q.x).toFixed(1)},${PY(Math.max(-12, Math.min(40, q.d))).toFixed(1)}`).join(' ')
const SCRIM = ['pass', 'incomplete', 'interception', 'sack', 'rush']
const isScrim = (pl: FbPlay | null | undefined) => Boolean(pl && SCRIM.includes(parseFbPlay(pl).kind))

function Clipboard({ play, scene, offense, caption, sub }: { play: FbPlay | null; scene: Scene | null; offense: string; caption: string; sub: string }) {
  const lines: JSX.Element[] = []
  const marks: JSX.Element[] = []
  if (scene) {
    const t0 = scene.snapAt
    const tEnd = scene.duration
    for (const a of scene.actors) {
      const s = sample(a.frames, t0)
      const path: Array<{ x: number; d: number }> = []
      for (let t = t0; t <= tEnd + 1e-6; t += 0.08) { const q = sample(a.frames, t); path.push({ x: q.x, d: q.d }) }
      const end = path[path.length - 1]
      const moved = Math.hypot(end.x - s.x, end.d - s.d)
      if (a.side === 'off') {
        if (a.role.startsWith('OL') || (a.role === 'TE' && moved < 3)) {
          const bx = s.x + (end.x - s.x) * 0.6
          const bd = s.d + Math.max(0.9, (end.d - s.d) * 0.6)
          const ang = Math.atan2(-(bd - s.d), bx - s.x)
          const nx = Math.sin(ang) * 0.9; const nd = Math.cos(ang) * 0.9
          lines.push(<g key={`b${a.id}`} className="cv-ink cv-ink--block"><polyline points={pts([{ x: s.x, d: s.d + 0.4 }, { x: bx, d: bd }])} /><polyline points={pts([{ x: bx - nx, d: bd - nd }, { x: bx + nx, d: bd + nd }])} /></g>)
        } else if (moved > 0.8) {
          lines.push(<polyline key={`r${a.id}`} className={`cv-ink ${a.key ? 'cv-ink--key' : 'cv-ink--route'}`} points={pts(path)} markerEnd={`url(#cv-arr-${a.key ? 'key' : 'route'})`} />)
        }
        marks.push(<g key={`o${a.id}`} className={`cv-o${a.key ? ' is-key' : ''}`} transform={`translate(${PX(s.x)} ${PY(s.d)})`}><circle r="8.5" />{a.role === 'QB' ? <text y="3.5">Q</text> : null}</g>)
      } else {
        if (moved > 2.5) lines.push(<polyline key={`d${a.id}`} className="cv-ink cv-ink--def" points={pts(path)} />)
        const x = PX(s.x); const y = PY(s.d)
        marks.push(<g key={`x${a.id}`} className="cv-x"><line x1={x - 7} y1={y - 7} x2={x + 7} y2={y + 7} /><line x1={x - 7} y1={y + 7} x2={x + 7} y2={y - 7} /></g>)
      }
    }
    const ball: Array<{ x: number; d: number }> = []
    for (let t = t0; t <= tEnd + 1e-6; t += 0.06) { const b = sample(scene.ball, t); ball.push({ x: b.x, d: b.d }) }
    if (scene.kind === 'pass' || scene.kind === 'incomplete' || scene.kind === 'interception') lines.push(<polyline key="ball" className="cv-ink cv-ink--ball" points={pts(ball)} />)
  }
  return (
    <svg className="cv-board" viewBox={`0 0 ${PW} ${PH + 60}`} role="img" aria-label={`Clipboard diagram: ${caption}`}>
      <defs>
        <linearGradient id="cv-wood" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#a8723f" /><stop offset=".5" stopColor="#8a5a2e" /><stop offset="1" stopColor="#6e4523" /></linearGradient>
        <filter id="cv-grain" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="0.012 0.35" numOctaves="3" seed="4" /><feColorMatrix type="matrix" values="0 0 0 0 0.3  0 0 0 0 0.18  0 0 0 0 0.08  0 0 0 0.45 0" /><feComposite in2="SourceGraphic" operator="in" /></filter>
        <filter id="cv-hand" x="-5%" y="-5%" width="110%" height="110%"><feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="9" result="n" /><feDisplacementMap in="SourceGraphic" in2="n" scale="3.2" /></filter>
        <linearGradient id="cv-metal" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#f1f4f8" /><stop offset=".45" stopColor="#9aa6b5" /><stop offset="1" stopColor="#d6dde6" /></linearGradient>
        {(['route', 'key'] as const).map((k) => <marker key={k} id={`cv-arr-${k}`} viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill={k === 'key' ? '#d22d2d' : '#1f3c8f'} /></marker>)}
      </defs>
      <rect x="4" y="34" width={PW - 8} height={PH + 20} rx="22" fill="url(#cv-wood)" />
      <rect x="4" y="34" width={PW - 8} height={PH + 20} rx="22" fill="#000" filter="url(#cv-grain)" opacity=".6" />
      <rect x="30" y="66" width={PW - 60} height={PH - 40} rx="4" fill="#f3efe4" stroke="#d8d2c2" />
      {Array.from({ length: 21 }, (_, i) => <line key={i} x1="34" x2={PW - 34} y1={98 + i * 26} y2={98 + i * 26} stroke="#c9dcef" strokeWidth=".8" />)}
      <line x1="64" x2="64" y1="68" y2={PH + 24} stroke="#f2a7a7" strokeWidth="1.2" />
      <g className="cv-sketch" filter="url(#cv-hand)">
        <line x1="44" x2={PW - 44} y1={LOS_Y} y2={LOS_Y} strokeDasharray="10 7" />
        {[-10, 10, 20, 30].map((d) => <g key={d}>{[-3.1, 3.1].map((h) => <line key={h} x1={PX(h) - 5} x2={PX(h) + 5} y1={PY(d)} y2={PY(d)} />)}<text x="48" y={PY(d) + 4}>{d > 0 ? `+${d}` : d}</text></g>)}
        {lines}
        {marks}
      </g>
      <text x="76" y="92" className="cv-hand cv-hand--title">{caption}</text>
      <text x="76" y={PH + 8} className="cv-hand cv-hand--sub">{sub}</text>
      <text x={PW - 44} y="92" textAnchor="end" className="cv-hand cv-hand--team">{offense} ↑</text>
      <rect x={PW / 2 - 92} y="14" width="184" height="58" rx="12" fill="url(#cv-metal)" stroke="#6b7684" />
      <rect x={PW / 2 - 60} y="26" width="120" height="16" rx="8" fill="#5b6573" opacity=".55" />
      <circle cx={PW / 2 - 70} cy="56" r="5" fill="#7c8796" /><circle cx={PW / 2 + 70} cy="56" r="5" fill="#7c8796" />
      {!play ? <text x={PW / 2} y={PH / 2} textAnchor="middle" className="cv-hand cv-hand--sub">No snaps in this spot yet.</text> : null}
    </svg>
  )
}

interface Props { plays: readonly FbPlay[]; epa: EpaPlay[]; away: FbTeam; home: FbTeam; shownPlay: FbPlay | null; defaultTeamId: string | null; grid: Grid | null; ep: EpGrid | null; tables: FourthTables | null; isNfl: boolean; onPick: (id: string) => void; onShow: (ids: string[], label: string, teamId: string) => void }

export function CoachView({ plays, epa, away, home, shownPlay, defaultTeamId, grid, ep, tables, isNfl, onPick, onShow }: Props) {
  const [pickTeam, setPickTeam] = useState<string | null>(null)
  const teamId = pickTeam ?? defaultTeamId ?? away.id
  const team = teamId === home.id ? home : away
  const color = visibleColor(team, '#3fd8c4')
  const profile = useMemo(() => coachProfile(plays, teamId, home.id, grid, epa, ep, tables), [plays, teamId, home.id, grid, epa, ep, tables])
  const read = useMemo(() => coachRead(profile, team.abbr, !isNfl), [profile, team.abbr, isNfl])
  const byId = useMemo(() => new Map(plays.map((p) => [p.id, p])), [plays])
  const epaOf = useMemo(() => new Map(epa.map((e) => [e.id, e.epa])), [epa])
  // the spot of the snap on the field, when it is this team's
  const nowKeys = useMemo(() => (shownPlay && (shownPlay.offense ?? shownPlay.start.team) === teamId ? situationOf(shownPlay, teamId, home.id) : []), [shownPlay, teamId, home.id])
  const downRows = profile.rows.filter((r) => !['rz', 'two'].includes(r.key))
  const extraRows = profile.rows.filter((r) => ['rz', 'two'].includes(r.key))
  const [sel, setSel] = useState<string | null>(null)
  const [concept, setConcept] = useState<string | null>(null)
  const [step, setStep] = useState(0)
  const [pin, setPin] = useState<string | null>(null)
  useEffect(() => { setSel(null); setConcept(null); setStep(0); setPin(null) }, [teamId])
  const fallback = nowKeys.find((k) => profile.rows.some((r) => r.key === k)) ?? [...profile.rows].sort((a, b) => b.n - a.n)[0]?.key ?? null
  const key = sel ?? fallback
  const row: SituationRow | null = profile.rows.find((r) => r.key === key) ?? null
  const rowIds = new Set(row?.ids ?? [])
  // what he calls in this spot: the concepts, restricted to this spot's snaps
  const inSpot = row ? profile.concepts.map((c) => { const ids = c.ids.filter((id) => rowIds.has(id)); const e = ids.map((id) => epaOf.get(id)).filter((v): v is number => v != null); return { ...c, ids, n: ids.length, success: e.length ? e.filter((v) => v > 0).length / e.length : null, epa: e.reduce((t, v) => t + v, 0) } }).filter((c) => c.n > 0).sort((a, b) => b.n - a.n).slice(0, 5) : []
  const pool = (concept ? inSpot.find((c) => c.key === concept)?.ids : row?.ids)?.filter((id) => isScrim(byId.get(id))) ?? []
  const boardPlay = pin && byId.get(pin) ? byId.get(pin)! : pool.length ? byId.get(pool[Math.max(0, pool.length - 1 - (step % pool.length))]) ?? null : null
  // every snap in this spot, newest first, for the log beside the clipboard
  const spotSnaps = (row?.ids ?? []).map((id) => byId.get(id)).filter((p): p is FbPlay => Boolean(p)).reverse()
  // pass share by down x distance for this coach vs a typical coach (the play-call model on the same snaps)
  const grid2 = useMemo(() => {
    const cells = new Map<string, { n: number; pass: number; exp: number; expN: number }>()
    plays.forEach((p, i) => {
      if ((p.offense ?? p.start.team) !== teamId) return
      const d = p.start.down ?? 0; const dist = p.start.distance ?? 0
      if (d < 1 || d > 4 || dist < 1) return
      const { category } = classOfPlay(parseFbPlay(p), p.text)
      if (category !== 'PASS' && category !== 'RUN') return
      const b = dist <= 3 ? 's' : dist <= 6 ? 'm' : 'l'
      const k = `${d}${b}`
      const c = cells.get(k) ?? { n: 0, pass: 0, exp: 0, expN: 0 }
      c.n += 1; c.pass += category === 'PASS' ? 1 : 0
      if (grid) { const st = stateForPlay(plays, i, home.id); if (st) { const cp = categoryProbs(predict(grid, st)); c.exp += cp.PASS / Math.max(1e-6, cp.PASS + cp.RUN); c.expN += 1 } }
      cells.set(k, c)
    })
    return cells
  }, [plays, teamId, grid, home.id])
  const cellKey = (d: number, b: string) => (d === 1 ? 'd1' : d === 4 ? 'd4' : `d${d}${b}`)
  const scene = useMemo(() => { if (!boardPlay) return null; const parsed = parseFbPlay(boardPlay); if (!SCRIM.includes(parsed.kind)) return null; return buildScene({ parsed, yac: boardPlay.yac, names: { passer: parsed.passer, target: parsed.target, rusher: parsed.rusher } }) }, [boardPlay])
  const parsed = boardPlay ? parseFbPlay(boardPlay) : null
  const dd = boardPlay?.start.down ? `${['', '1st', '2nd', '3rd', '4th'][boardPlay.start.down]} & ${boardPlay.start.distance}` : ''
  const caption = boardPlay ? `${dd}${parsed?.formation ? ` · ${parsed.formation.replace('-', ' ')}` : ''} · ${parsed?.label ?? ''}` : row?.label ?? ''
  const sub = boardPlay ? `Q${boardPlay.period} ${boardPlay.clock} · ${boardPlay.yards != null ? `${boardPlay.yards >= 0 ? '+' : ''}${boardPlay.yards} yds` : ''}${epaOf.has(boardPlay.id) ? ` · ${signed(epaOf.get(boardPlay.id) ?? null)} exp. pts` : ''} · ${pool.length - (step % Math.max(1, pool.length))} of ${pool.length}` : ''
  const lean = row && Number.isFinite(row.proe) ? row.proe : null
  const tile = (r: SituationRow) => {
    const now = nowKeys.includes(r.key)
    const d = Number.isFinite(r.proe) ? r.proe : null
    return (
      <button key={r.key} type="button" className={`cv-sit${r.key === key ? ' is-on' : ''}${now ? ' is-now' : ''}`} onClick={() => { setSel(r.key); setConcept(null); setStep(0); setPin(null) }} aria-pressed={r.key === key}>
        <span className="cv-sit__top"><b>{r.label}</b>{now ? <i>NOW</i> : null}</span>
        <span className="cv-sit__mix"><u style={{ width: `${r.pass * 100}%` }} /><em style={{ left: `${Number.isFinite(r.expPass) ? r.expPass * 100 : 50}%` }} /></span>
        <span className="cv-sit__nums"><strong>{pctf(r.pass)} pass</strong><small>{r.n} snaps{d != null ? ` · ${d >= 0 ? '+' : ''}${Math.round(d * 100)} vs typical` : ''}</small></span>
      </button>
    )
  }
  return (
    <section className="lf-card cv" aria-label="Coach's view">
      <header className="am__head">
        <h3>COACH&apos;S VIEW <small className="lf-muted">what does this coach call in this spot?</small></h3>
        <span className="fbd-tabs">{[away, home].map((t) => <button key={t.id} type="button" className={t.id === teamId ? 'is-on' : ''} style={{ ['--tc' as string]: visibleColor(t, '#3fd8c4') }} onClick={() => setPickTeam(t.id)}>{t.abbr} coach</button>)}</span>
      </header>
      <ol className="cv__steps"><li><b>1</b> Pick a spot</li><li><b>2</b> See the play he ran there</li><li><b>3</b> Read what he does and how it works</li></ol>
      <div className="cv__grid">
        <div className="cv__sits" role="group" aria-label="Situations">
          <h4>{team.abbr} BY DOWN &amp; DISTANCE</h4>
          {downRows.map(tile)}
          {extraRows.length ? <h4>SPECIAL SPOTS</h4> : null}
          {extraRows.map(tile)}
          {!profile.rows.length ? <p className="lf-muted lf-small">No scrimmage snaps yet.</p> : null}
          <p className="cv__key"><span className="cv__keybar" /> pass share · <span className="cv__keytick" /> a typical coach</p>
        </div>
        <div className="cv__boardcol">
          <Clipboard play={scene ? boardPlay : null} scene={scene} offense={team.abbr} caption={caption} sub={sub} />
          <span className="cv__nav">
            <button type="button" onClick={() => setStep((s) => s + 1)} disabled={pool.length < 2}>‹ earlier</button>
            <button type="button" onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={pool.length < 2 || step === 0}>later ›</button>
            {boardPlay ? <button type="button" className="is-go" onClick={() => onPick(boardPlay.id)}>▶ Replay on the field</button> : null}
          </span>
        </div>
        <div className="cv__answer">
          {row ? (
            <>
              <p className="cv__q">On <b>{row.label.replace(/ \(.*\)$/, '').toLowerCase()}</b>, {team.abbr}…</p>
              <p className="cv__big" style={{ ['--tc' as string]: color }}><b>{row.pass >= 0.5 ? 'throws' : 'runs'} {Math.round((row.pass >= 0.5 ? row.pass : 1 - row.pass) * 100)}%</b> <span>of the time</span></p>
              <p className="cv__vs">{Number.isFinite(row.expPass) ? <>A typical coach {row.pass >= 0.5 ? 'throws' : 'runs'} <b>{Math.round((row.pass >= 0.5 ? row.expPass : 1 - row.expPass) * 100)}%</b> here{lean != null && Math.abs(lean) >= 0.08 ? <> — {team.abbr} is <b className={lean > 0 ? 'cv-pass' : 'cv-run'}>{lean > 0 ? 'more pass-happy' : 'more run-heavy'}</b> than most.</> : ' — about the same.'}</> : null}</p>
              <div className="cv__stats"><span><small>Snaps</small><b>{row.n}</b></span><span><small>Worked</small><b>{pctf(row.success)}</b></span><span><small>Game avg</small><b>{pctf(profile.rows.reduce((t, r) => t + (r.success ?? 0) * r.n, 0) / Math.max(1, profile.rows.reduce((t, r) => t + r.n, 0)))}</b></span></div>
              <h4>HIS CALLS HERE <small>tap one to draw it</small></h4>
              <ul className="cv__calls">
                {inSpot.map((c) => (
                  <li key={c.key}><button type="button" className={concept === c.key ? 'is-on' : ''} onClick={() => { setConcept(concept === c.key ? null : c.key); setStep(0); setPin(null) }}>
                    <span>{c.label}</span><i><u style={{ width: `${(c.n / Math.max(1, row.n)) * 100}%` }} /></i><b>{c.n}×</b><small className={(c.success ?? 0) >= 0.5 ? 'cv-ok' : 'cv-bad'}>{pctf(c.success)} worked</small>
                  </button></li>
                ))}
              </ul>
              <button type="button" className="cv__showall" onClick={() => onShow(row.ids, `${team.abbr}: ${row.label}`, teamId)}>Highlight these {row.n} plays on the attack map and play list</button>
              <div className="cv__more">
                <div className="cv__log">
                  <h4>EVERY SNAP IN THIS SPOT <small>tap one to draw it</small></h4>
                  <ol>{spotSnaps.slice(0, 9).map((p) => { const k = parseFbPlay(p); const e = epaOf.get(p.id); return <li key={p.id}><button type="button" className={boardPlay?.id === p.id ? 'is-on' : ''} onClick={() => setPin(p.id)}><span className="cv__logq">Q{p.period} {p.clock}</span><span className="cv__logp"><b>{p.start.down ? `${['', '1st', '2nd', '3rd', '4th'][p.start.down]} & ${p.start.distance}` : ''} · {k.label}</b><small>{(p.text ?? '').replace(/^\(\d+:\d+\)\s*/, '').slice(0, 90)}</small></span><span className={`cv__logy ${(p.yards ?? 0) >= (p.start.distance ?? 99) ? 'cv-ok' : ''}`}>{p.yards != null ? `${p.yards >= 0 ? '+' : ''}${p.yards}` : ''}</span><span className={`cv__loge ${(e ?? 0) > 0 ? 'cv-ok' : 'cv-bad'}`}>{e == null ? '' : `${e >= 0 ? '+' : ''}${e.toFixed(1)}`}</span></button></li> })}</ol>
                </div>
                <div className="cv__dd">
                  <h4>PASS RATE BY DOWN &amp; DISTANCE <small>{team.abbr} vs a typical coach</small></h4>
                  <table><thead><tr><th /><th>1-3 to go</th><th>4-6</th><th>7+</th></tr></thead><tbody>
                    {[1, 2, 3, 4].map((d) => <tr key={d}><th>{['', '1st', '2nd', '3rd', '4th'][d]}</th>{['s', 'm', 'l'].map((b) => { const c = grid2.get(`${d}${b}`); const p = c && c.n ? c.pass / c.n : null; const ex = c && c.expN ? c.exp / c.expN : null; const k2 = cellKey(d, b); return <td key={b}><button type="button" disabled={!c} className={k2 === key ? 'is-on' : ''} onClick={() => { setSel(k2); setConcept(null); setStep(0); setPin(null) }} style={{ ['--p' as string]: p == null ? 0.5 : p }}>{c ? <><b>{Math.round((p ?? 0) * 100)}%</b><small>{c.n} snaps{ex != null ? ` · typ ${Math.round(ex * 100)}%` : ''}</small></> : <small>—</small>}</button></td> })}</tr>)}
                  </tbody></table>
                  <p className="cv__ddkey"><i className="run" /> run-heavy <i className="pass" /> pass-heavy · tap a cell to open that spot</p>
                </div>
              </div>
            </>
          ) : <p className="lf-muted">Pick a spot on the left.</p>}
        </div>
      </div>
      <div className="cv__foot">
        <div className="cv__tiles">
          <div style={{ ['--tc' as string]: color }}><small>Pass rate vs typical coach</small><b className={profile.proe == null ? '' : profile.proe >= 0 ? 'is-up' : 'is-down'}>{profile.proe == null ? '—' : `${profile.proe >= 0 ? '+' : ''}${Math.round(profile.proe * 100)} pts`}</b><em>{pctf(profile.pass)} pass · typical {pctf(profile.expPass)}</em></div>
          <div><small>Shotgun · no huddle</small><b>{pctf(profile.shotgun)}</b><em>no huddle {pctf(profile.noHuddle)} of snaps</em></div>
          <div><small>Opening script</small><b>{profile.script ? `${profile.script.pass}P · ${profile.script.n - profile.script.pass}R` : '—'}</b><em>first {profile.script?.n ?? 0} snaps · {signed(profile.script?.epa ?? null, 1)} exp. pts</em></div>
          <div><small>4th-down calls</small><b>{profile.fourth.faced ? `${profile.fourth.matched}/${profile.fourth.faced}` : '—'}</b><em>{profile.fourth.faced ? `matched the numbers · went ${profile.fourth.went}×` : 'none yet'}</em></div>
        </div>
        <div className="cv__read">
          <h4>THE GAME-WIDE READ</h4>
          <ul>{read.map((s) => <li key={s}>{s}</li>)}</ul>
          {profile.fourth.calls.length ? <div className="cv__fourth">{profile.fourth.calls.slice(-4).map((c) => <button key={c.id} type="button" onClick={() => onPick(c.id)} className={c.did === c.best || c.gap < 0.15 ? 'is-ok' : 'is-off'}>{c.label}: {c.did === 'go' ? 'went' : c.did === 'punt' ? 'punted' : 'kicked'}{c.did === c.best || c.gap < 0.15 ? ' ✓' : ` · numbers: ${c.best} (+${c.gap.toFixed(1)})`}</button>)}</div> : null}
        </div>
      </div>
      <p className="lf-muted lf-tiny">Drawn from ESPN&apos;s play text: direction, depth, yards and result are real; where each player lines up and runs is a schematic. &quot;Typical coach&quot; = AISP&apos;s play-call model on NFL snaps 2015-2026{isNfl ? '' : ', applied to college as a rough guide'}. &quot;Worked&quot; = the snap added expected points. Descriptive, never a wager signal.</p>
    </section>
  )
}
