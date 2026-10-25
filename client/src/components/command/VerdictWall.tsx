/**
 * THE VERDICT WALL (owner 10-24: a HOLY SHIT feature for Daily Decisions). Every decision AISP made today is one
 * tile, in the order it was made, coloured by its verdict: a bet, a bet it wanted but the shared $100 was already
 * spoken for, an edge too small to clear the bar, a game held back by the 10-point warning rule, or a game the
 * pipeline blocked. The headline says the day in one sentence; click a verdict to light up only its tiles; hover a
 * tile for the game and the exact reason in AISP's own words.
 *
 * Reads the decision board the page already loads. Paper experiment only, never advice or a wager signal.
 */
import { useMemo, useState, type CSSProperties } from 'react'
import { Crest } from '../../features/flow/Crest'
import './verdictWall.css'

type Item = { decision_id: string; sport: string; matchup: string | null; selection?: string | null; american?: number | null; recommendation: string; reason?: string | null; recommended_at?: string | null; starts_at?: string | null }
type Kind = 'bet' | 'room' | 'edge' | 'gap' | 'blocked' | 'other'
const KINDS: Array<{ key: Kind; label: string; color: string; blurb: string }> = [
  { key: 'bet', label: 'Bet', color: '#fbbf24', blurb: 'passed every gate and went in' },
  { key: 'room', label: 'Wanted it, $100 was full', color: '#a78bfa', blurb: 'qualified, but the shared $100 was entered or held for hockey and baseball' },
  { key: 'edge', label: 'Edge too small', color: '#64748b', blurb: 'the price did not leave enough edge' },
  { key: 'gap', label: 'Held back: 10-point rule', color: '#fb923c', blurb: 'model and market more than 10 points apart: a warning sign, recorded only' },
  { key: 'blocked', label: 'Blocked', color: '#f87171', blurb: 'a pipeline gate (lease, forecast, price) was not ready' },
  { key: 'other', label: 'Other', color: '#94a3b8', blurb: 'any other outcome' },
]
function kindOf(i: Item): Kind {
  const r = (i.reason ?? '').toLowerCase()
  if (i.recommendation === 'QUALIFIED_BET') return 'bet'
  if (i.recommendation === 'RECORD_ONLY' || r.includes('gap_gt_10')) return 'gap'
  if (i.recommendation === 'BLOCKED') return 'blocked'
  if (r.includes('shared $100') || r.includes('no room')) return 'room'
  if (i.recommendation === 'DECLINED' || i.recommendation === 'NO_BET') return r.includes('edge') || r.includes('expected return') || r.includes('threshold') ? 'edge' : 'other'
  return 'other'
}
const ICON: Record<string, string> = { MLB: '⚾', NFL: '🏈', CFB: '🏈', NHL: '🏒', NBA: '🏀' }
const clean = (s: string | null | undefined) => (s ?? '').replace(/Â·/g, '·').replace(/â€”/g, '—').trim()
const am = (n: number | null | undefined) => (n == null ? '' : n > 0 ? `+${n}` : `${n}`)

export function VerdictWall({ items }: { items: Item[] }) {
  const [only, setOnly] = useState<Kind | null>(null)
  const [hover, setHover] = useState<Item | null>(null)
  const rows = useMemo(() => [...items].sort((a, b) => Date.parse(a.recommended_at ?? a.starts_at ?? '') - Date.parse(b.recommended_at ?? b.starts_at ?? '')).map((i) => ({ i, k: kindOf(i) })), [items])
  if (!rows.length) return null
  const count = (k: Kind) => rows.filter((r) => r.k === k).length
  const n = rows.length
  const parts = [
    count('room') ? `wanted ${count('room')} more but the $100 was already spoken for` : '',
    count('edge') ? `passed on ${count('edge')} where the edge was too thin` : '',
    count('gap') ? `held back ${count('gap')} under the 10-point warning rule` : '',
    count('blocked') ? `could not decide ${count('blocked')} because a gate was blocked` : '',
  ].filter(Boolean)
  const sentence = `AISP looked at ${n} games today and bet ${count('bet')}.${parts.length ? ` It ${parts.slice(0, -1).join(', ')}${parts.length > 1 ? ', and ' : ''}${parts[parts.length - 1]}.` : ''}`
  const teamOf = (i: Item) => { const sel = clean(i.selection).replace(/\s+ML$/i, ''); return (sel && !/^(none|null)$/i.test(sel) ? sel : null) || (i.matchup ?? '').split(' @ ')[1] || '' }

  return (
    <section className="vw" aria-label="The Verdict Wall">
      <header className="vw__head">
        <div><h2>The Verdict Wall</h2><p>{sentence}</p></div>
        <span className="vw__legend" role="group" aria-label="Show one verdict">
          {KINDS.filter((k) => count(k.key)).map((k) => <button key={k.key} type="button" className={only === k.key ? 'is-on' : ''} style={{ ['--c' as string]: k.color } as CSSProperties} onClick={() => setOnly(only === k.key ? null : k.key)} title={k.blurb}><i />{k.label}<b>{count(k.key)}</b></button>)}
        </span>
      </header>
      <div className="vw__body">
        <ol className="vw__wall">
          {rows.map(({ i, k }, idx) => {
            const meta = KINDS.find((x) => x.key === k)!
            return (
              <li key={i.decision_id} className={`vw__tile is-${k}${only && only !== k ? ' is-dim' : ''}`} style={{ ['--c' as string]: meta.color, animationDelay: `${idx * 22}ms` } as CSSProperties} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} tabIndex={0} onFocus={() => setHover(i)} aria-label={`${i.matchup ?? ""}: ${meta.label}`}>
                <span className="vw__crest"><Crest sport={i.sport} abbr={teamOf(i)} size={26} /></span>
                <b>{teamOf(i).length > 11 ? `${teamOf(i).slice(0, 10)}…` : teamOf(i)}</b>
                <small>{ICON[i.sport] ?? ''} {k === 'bet' ? am(i.american) : meta.label.split(':')[0].replace('Wanted it, ', '')}</small>
              </li>
            )
          })}
        </ol>
        <aside className="vw__why" aria-live="polite">
          {hover ? (
            <>
              <small>{ICON[hover.sport] ?? ''} {hover.sport} · decided {hover.recommended_at ? new Date(hover.recommended_at).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }) : '—'} ET</small>
              <b>{clean(hover.matchup)}</b>
              <span className={`vw__verdict is-${kindOf(hover)}`}>{KINDS.find((x) => x.key === kindOf(hover))!.label}{hover.selection && !/^none$/i.test(hover.selection) ? ` · ${clean(hover.selection)} ${am(hover.american)}` : ''}</span>
              <p>{clean(hover.reason).replace(/^EXPIRED_LEASE:\s*/, 'Lease expired: ')}</p>
            </>
          ) : (
            <>
              <small>Why did AISP say no?</small>
              <b>Hover any tile</b>
              <p>Every tile is one game, in the order AISP decided it. The verdict is AISP's own recorded reason, word for word.</p>
              {count('room') > count('bet') / 2 ? <p className="vw__insight">The $100 cap, not the model, was the limit today: {count('room')} qualified games found no room.</p> : null}
            </>
          )}
        </aside>
      </div>
    </section>
  )
}
