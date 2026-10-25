/**
 * DECISION INTELLIGENCE: what the offense will call (the next-play model, five rows) and how the defense
 * has been defending (computed from this game's plays), plus the AISP INSIGHT box that puts the two in
 * one plain-English paragraph. The model is an estimate of the CALL; nothing here is a wager signal.
 */
import { useState } from 'react'
import type { DefenseFacts, CallRow } from './situation'
import type { Forecast, Grid } from './playcall'
import { LABELS, CLASSES, type Category, type PlayClass } from './playcall'
import type { PulseCard } from './GamePulse'

const pct = (p: number) => `${Math.round(p * 100)}%`
const ICON: Record<CallRow['icon'], string> = { pass: '➶', deep: '⤒', inside: '⇧', outside: '⇗', kick: '⚑' }

interface Props {
  rows: CallRow[] | null
  forecast: Forecast | null
  grid: Grid | null
  offense: string
  defense: string
  defenseId: string | null
  /** The offense whose plays the defensive facts are evidence of (the attack map focuses on it). */
  offenseId: string | null
  facts: DefenseFacts | null
  actual: { cls: PlayClass | null; category: Category | null }
  live: boolean
  isNfl: boolean
  insight: string
  reviewing: boolean
  onShow: (c: PulseCard | null) => void
  active: string | null
  /** Hovering a call row: its key (short, deep, inside, outside, kick), so the attack map can outline where it goes. */
  onFocusRow?: (key: string | null) => void
}

export function DecisionIntel({ rows, forecast, grid, offense, defense, defenseId, offenseId, facts, actual, live, isNfl, insight, reviewing, onShow, active, onFocusRow }: Props) {
  const [tab, setTab] = useState<'call' | 'defend'>('call')
  const [open, setOpen] = useState<string | null>(null)
  const actualRow = actual.cls ? (actual.cls.startsWith('PASS_S') ? 'short' : actual.cls.startsWith('PASS_D') ? 'deep' : actual.cls === 'RUN_M' ? 'inside' : actual.cls.startsWith('RUN_') ? 'outside' : 'kick') : actual.category === 'PASS' ? null : actual.category === 'RUN' ? null : actual.category ? 'kick' : null
  const fmt = (v: number | null, d = 2) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}`)
  const show = (key: string, headline: string, detail: string, ids: string[]) => onShow(active === key ? null : { key, headline, detail, stat: '', ids, teamId: offenseId ?? defenseId, tone: 'neutral', score: 1 })
  return (
    <section className="lf-card di" aria-label="Decision intelligence">
      <header className="di__head">
        <h3>DECISION INTELLIGENCE</h3>
        <span className="di__badge" title={forecast && grid ? `Gradient boosting on ${grid.meta.trainedOn.replace('nflverse play-by-play ', 'NFL snaps ')}; pass vs run right ${Math.round(grid.meta.validation.pass_vs_run_accuracy_grid * 100)}% on 2025-26 (always-the-common-one: ${Math.round(grid.meta.validation.pass_vs_run_always_majority * 100)}%). Tilted toward ${offense}'s own calls this game. A forecast of the call, not the result.${isNfl ? '' : ' Applied to college as a rough guide.'}` : undefined}>AISP MODEL · playcall v1{isNfl ? '' : ' · NFL-trained'}</span>
      </header>
      <div className="di__tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'call'} className={tab === 'call' ? 'is-on' : ''} onClick={() => setTab('call')}>What will {offense} call?</button>
        <button type="button" role="tab" aria-selected={tab === 'defend'} className={tab === 'defend' ? 'is-on' : ''} onClick={() => setTab('defend')}>How is {defense} defending?</button>
        {live && !reviewing ? <span className="lf-pill lf-pill--live">LIVE</span> : reviewing ? <span className="lf-pill lf-pill--test">REVIEW</span> : null}
      </div>
      {tab === 'call' ? (
        rows && forecast ? (
          <ol className="di__rows">
            {rows.map((r) => (
              <li key={r.key} className={`di__row di__row--${r.icon}${actualRow === r.key ? ' is-called' : actual.category ? ' is-dim' : ''}`}>
                <button type="button" className="di__rowbtn" onClick={() => setOpen(open === r.key ? null : r.key)} aria-expanded={open === r.key} onMouseEnter={() => onFocusRow?.(r.key)} onMouseLeave={() => onFocusRow?.(null)} onFocus={() => onFocusRow?.(r.key)} onBlur={() => onFocusRow?.(null)}>
                  <i className="di__icon" aria-hidden="true">{ICON[r.icon]}</i>
                  <span className="di__label">{actualRow === r.key ? '✔ ' : ''}{r.label}</span>
                  <span className="di__bar"><i style={{ width: `${Math.max(2, r.p * 100)}%` }} /></span>
                  <b>{pct(r.p)}</b>
                </button>
                {open === r.key && r.parts.length ? <div className="di__parts">{r.parts.map((p) => <span key={p.label}>{p.label} <b>{pct(p.p)}</b></span>)}</div> : null}
              </li>
            ))}
          </ol>
        ) : <p className="lf-muted lf-small">{grid ? 'Waiting for a scrimmage play with a down and distance.' : 'Loading the play-calling model…'}</p>
      ) : (
        facts && (facts.dropbacks + facts.rushes) ? (
          <ul className="di__facts">
            <li><button type="button" className={active === 'def-pass' ? 'is-active' : ''} onClick={() => show('def-pass', `${defense} against the pass`, `${facts.dropbacks} dropbacks faced`, facts.ids.dropbacks)}><span>vs the pass</span><b className={(facts.epaPerDropback ?? 0) <= 0 ? 'is-up' : 'is-down'}>{fmt(facts.epaPerDropback)}</b><small>EPA allowed per dropback · {facts.dropbacks} dropbacks · {facts.sacks} sacks · {facts.ints} INT</small></button></li>
            <li><button type="button" className={active === 'def-run' ? 'is-active' : ''} onClick={() => show('def-run', `${defense} against the run`, `${facts.rushes} carries faced`, facts.ids.rushes)}><span>vs the run</span><b className={(facts.epaPerRush ?? 0) <= 0 ? 'is-up' : 'is-down'}>{fmt(facts.epaPerRush)}</b><small>EPA allowed per carry · {facts.rushes} carries</small></button></li>
            <li><button type="button" className={active === 'def-third' ? 'is-active' : ''} onClick={() => show('def-third', `${defense} on third down`, `${facts.thirdStops} of ${facts.thirdFaced} stopped`, facts.ids.third)}><span>3rd-down stops</span><b>{facts.thirdFaced ? `${Math.round((facts.thirdStops / facts.thirdFaced) * 100)}%` : '—'}</b><small>{facts.thirdStops} of {facts.thirdFaced} third downs stopped</small></button></li>
            <li><button type="button" className={active === 'def-boom' ? 'is-active' : ''} onClick={() => show('def-boom', `${defense} explosive plays allowed`, `${facts.explosiveAllowed} plays of 15+`, facts.ids.explosive)}><span>explosives allowed</span><b className={facts.explosiveAllowed <= 2 ? 'is-up' : 'is-down'}>{facts.explosiveAllowed}</b><small>plays of 15+ yards · short passes {fmt(facts.shortPassEpa)} EPA ({facts.shortPassN}) · deep {fmt(facts.deepPassEpa)} EPA ({facts.deepPassN})</small></button></li>
          </ul>
        ) : <p className="lf-muted lf-small">Defensive numbers appear once {defense} has faced a few snaps.</p>
      )}
      {insight ? <div className="di__insight"><b>💡 AISP INSIGHT</b><p>{insight}</p></div> : null}
      {actual.category && actual.cls == null && forecast ? <p className="lf-muted lf-tiny di__note">They called a {actual.category.toLowerCase()} ({CLASSES.filter((c) => LABELS[c].group === (actual.category === 'PASS' ? 'pass' : actual.category === 'RUN' ? 'run' : 'kick')).length} classes); the text gave no direction.</p> : null}
    </section>
  )
}
