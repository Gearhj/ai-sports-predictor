/** Fourth down: go, punt or kick, in expected points. League averages, not a prediction of this game. */
import { useMemo } from 'react'
import { advise, type FourthTables, type Option } from './fourth'
import type { EpGrid } from './ep'

interface Props {
  ep: EpGrid
  tables: FourthTables
  /** Yards from the offense's own goal line, yards to go, and who has the ball. */
  own: number
  distance: number
  team: string
  /** What the coach did, when the play has been run. */
  did: 'go' | 'punt' | 'fg' | null
  label: string
}

const fmt = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}`

export function FourthDownCard({ ep, tables, own, distance, team, did, label }: Props) {
  const advice = useMemo(() => advise(ep, tables, own, distance), [ep, tables, own, distance])
  const lo = Math.min(0, ...advice.options.map((o) => o.ep))
  const hi = Math.max(0.5, ...advice.options.map((o) => o.ep))
  const pos = (v: number) => ((v - lo) / (hi - lo)) * 100
  const chosen: Option | undefined = did ? advice.options.find((o) => o.key === did) : undefined
  const gap = chosen ? advice.best.ep - chosen.ep : 0
  return (
    <section className="lf-card fb-fourth" aria-label="Fourth down advisor">
      <header>
        <h3>Fourth down: what pays?</h3>
        <p className="lf-muted lf-small">{label} · {team} · 4th &amp; {distance}, {advice.yardsToGoal} yds from the end zone</p>
      </header>
      <ol className="fb-fourth__list">
        {[...advice.options].sort((a, b) => b.ep - a.ep).map((o) => (
          <li key={o.key} className={`${o.key === advice.best.key ? 'is-best' : ''}${did === o.key ? ' is-did' : ''}`}>
            <span className="fb-fourth__name">{o.label}{did === o.key ? ' · what they did' : ''}</span>
            <span className="fb-fourth__bar"><i style={{ width: `${Math.max(3, pos(o.ep))}%` }} /></span>
            <b className={o.ep >= 0 ? 'is-up' : 'is-down'}>{fmt(o.ep)}</b>
            <small>{o.detail}</small>
          </li>
        ))}
      </ol>
      <p className="fb-fourth__verdict">
        {did == null
          ? <>The numbers like <b>{advice.best.label.toLowerCase()}</b> here.</>
          : gap < 0.15
            ? <>They made a sound call: <b>{chosen?.label.toLowerCase()}</b> is at or within a tenth of a point of the best option.</>
            : <>The numbers liked <b>{advice.best.label.toLowerCase()}</b> by <b>{gap.toFixed(2)}</b> expected points over what they did.</>}
      </p>
      <p className="lf-muted lf-small">Expected points from league averages: {tables.trainedOn}. Expected points, not win probability, so late in a close game the best call can differ. Never a wager signal.</p>
    </section>
  )
}
