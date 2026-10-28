/**
 * YOUR READ, beside the Monte Carlo grid (owner 10-27: "a selection mechanism right beside the MC visualization";
 * then "once the user selects their setting, there is no button or instructions on implementing their selections").
 * The game's sport-specific matchup reads (QB edge, goaltending, starting pitchers, rest, pace...) are a DRAFT while
 * you move them; RUN MY 10,000 commits them to the same per-game reads Build Your Model uses (aisp-my-reads-v1),
 * and the lab re-runs the 10,000 on the grid. Back to AISP clears them.
 */
import { useEffect, useState } from 'react'
import { operationalToday } from '../../services/operationalDate'
import { pct, usePulse } from '../pulse/pulseData'
import { READS, READS_EVENT, applyModel, defaultsFor, loadModel, loadReads, saveReads, type Reads } from '../lab/myModel'
import type { FocusTarget } from './gameNightData'

const same = (a: Reads, b: Reads) => {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)])
  return [...keys].every((k) => (a[k] ?? 0) === (b[k] ?? 0))
}

export function QuickRead({ target }: { target: FocusTarget }) {
  const pulse = usePulse(operationalToday())
  const g = pulse.games.find((x) => x.id === target.gameId) ?? null
  const [saved, setSaved] = useState<Reads>(() => loadReads()[target.gameId] ?? {})
  const [draft, setDraft] = useState<Reads>(saved)
  const [ran, setRan] = useState(false)
  useEffect(() => {
    const now = loadReads()[target.gameId] ?? {}
    setSaved(now); setDraft(now); setRan(false)
    // Build Your Model writes the same reads; follow what is committed there
    const on = () => { const v = loadReads()[target.gameId] ?? {}; setSaved(v); setDraft(v) }
    window.addEventListener(READS_EVENT, on)
    return () => window.removeEventListener(READS_EVENT, on)
  }, [target.gameId])
  const defs = READS[target.sport] ?? []
  const f = loadModel()?.sports[target.sport] ?? defaultsFor(target.sport)
  const model = g?.modelHome ?? null
  const market = g?.marketFresh ? g.marketHome : null
  const draftP = model == null ? null : applyModel(target.sport, f, model, market, [], draft).p
  const changes = defs.filter((d) => (draft[d.key] ?? 0) !== (saved[d.key] ?? 0)).length
  const dirty = !same(draft, saved)
  const onGrid = Object.values(saved).some((v) => v)
  const run = () => { saveReads(target.gameId, draft); setSaved(draft); setRan(true); window.setTimeout(() => setRan(false), 4000) }
  const reset = () => { setDraft({}); saveReads(target.gameId, {}); setSaved({}) }
  const { home, away } = target
  return (
    <section className={`qr${dirty ? ' is-dirty' : ''}`} aria-label="Your read on this game">
      <header className="qr__head">
        <b>YOUR READ</b>
        <small><i>1</i> Move what you believe about this game <i>2</i> Press <em>Run my 10,000</em> and the grid replays with your numbers</small>
      </header>
      {defs.map((d) => {
        const v = draft[d.key] ?? 0
        const dp = d.step < 0.1 ? 2 : d.step < 1 ? 1 : 0
        const changed = v !== (saved[d.key] ?? 0)
        return (
          <label key={d.key} className={`qr__row${v ? ' is-set' : ''}${changed ? ' is-changed' : ''}`} title={d.help}>
            <span><b>{d.label}</b><em>{v ? `${d.kind === 'total' && v > 0 ? '+' : ''}${Math.abs(v).toFixed(dp)} ${d.kind === 'margin' ? (v > 0 ? home.abbr : away.abbr) : d.unit.replace(/ of total$/, '')}` : 'even'}</em></span>
            <input type="range" min={d.min} max={d.max} step={d.step} value={v} onChange={(e) => setDraft({ ...draft, [d.key]: Number(e.target.value) })} onDoubleClick={() => setDraft({ ...draft, [d.key]: 0 })}
              aria-label={`${d.label}: ${d.kind === 'margin' ? `left favours ${away.abbr}, right favours ${home.abbr}` : 'left lowers the total, right raises it'}`} />
            <small><i>{d.kind === 'margin' ? `◀ ${away.abbr}` : '◀ lower'}</i><i>{d.kind === 'margin' ? `${home.abbr} ▶` : 'higher ▶'}</i></small>
          </label>
        )
      })}
      <div className="qr__out">
        <span><small>AISP</small><b>{model == null ? '—' : pct(model, 1)}</b></span>
        <span className="qr__arrow" aria-hidden="true">→</span>
        <span className={dirty || onGrid ? 'is-you' : ''}><small>{dirty ? 'YOUR DRAFT' : 'YOU'}</small><b>{draftP == null ? '—' : pct(draftP, 1)}</b></span>
        <em>{home.abbr} wins</em>
      </div>
      <button type="button" className="qr__run" disabled={!dirty} onClick={run}>
        {dirty ? `▶ Run my 10,000 · ${changes} change${changes === 1 ? '' : 's'}` : onGrid ? '✓ Your 10,000 are on the grid' : '▶ Run my 10,000'}
      </button>
      <p className={`qr__status${ran ? ' is-ran' : ''}`} role="status">
        {ran ? 'Running your 10,000 on the grid now…'
          : dirty ? 'Not on the grid yet: press Run to replay the 10,000 with your read.'
            : onGrid ? 'The grid shows YOUR 10,000. Move a slider and run again, or go back to AISP.'
              : 'The grid shows AISP’s own 10,000.'}
      </p>
      <div className="qr__btns">
        <button type="button" disabled={!onGrid && !dirty} onClick={reset}>↺ Back to AISP</button>
        <button type="button" onClick={() => document.querySelector('.lb')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Full builder ↓</button>
      </div>
    </section>
  )
}
