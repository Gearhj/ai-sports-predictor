/** RECENT PLAYS: time, situation, what happened, and expected points added. Click one to replay it. */
import { useState } from 'react'
import type { EpaPlay } from './ep'
import { ordinalDown, parseFbPlay, type FbPlay } from './footballData'

interface Props { plays: readonly FbPlay[]; epa: EpaPlay[]; selectedId: string | null; latestId: string | null; onSelect: (id: string) => void; highlight?: Set<string> | null }

export function RecentPlays({ plays, epa, selectedId, latestId, onSelect, highlight = null }: Props) {
  const [filter, setFilter] = useState<'all' | 'scoring' | 'big' | 'third'>('all')
  const e = new Map(epa.map((x) => [x.id, x.epa]))
  const rows = [...plays].reverse().filter((p) => {
    const k = parseFbPlay(p).kind
    if (k === 'nonplay') return false
    if (highlight && !highlight.has(p.id)) return false
    if (filter === 'scoring') return p.scoring
    if (filter === 'big') return (p.yards ?? 0) >= 15 || p.turnover
    if (filter === 'third') return p.start.down === 3 || p.start.down === 4
    return true
  }).slice(0, highlight ? 40 : 12)
  return (
    <section className="lf-card rp" aria-label="Recent plays">
      <header className="am__head">
        <h3>{highlight ? 'EVIDENCE' : 'RECENT PLAYS'}</h3>
        <select value={filter} onChange={(ev) => setFilter(ev.target.value as typeof filter)} aria-label="Filter plays"><option value="all">All Plays</option><option value="scoring">Scoring</option><option value="big">Big plays &amp; turnovers</option><option value="third">3rd &amp; 4th downs</option></select>
      </header>
      <ol className="rp__list">
        {rows.length ? rows.map((p) => {
          const k = parseFbPlay(p)
          const v = e.get(p.id)
          const picked = (selectedId ?? latestId) === p.id
          return (
            <li key={p.id} className={`${p.scoring ? 'is-score' : ''}${picked ? ' is-picked' : ''}`}>
              <button type="button" onClick={() => onSelect(p.id)}>
                <span className="rp__when">Q{p.period} {p.clock}</span>
                <span className="rp__sit"><b>{ordinalDown(p.start.down) ? `${ordinalDown(p.start.down)} & ${p.start.distance}` : k.label}</b><small>{p.start.possessionText ?? ''}</small></span>
                <span className="rp__text">{p.text}</span>
                <span className={`rp__epa ${v != null ? (v >= 0 ? 'is-up' : 'is-down') : ''}`}>{v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(1)}` : ''}</span>
              </button>
            </li>
          )
        }) : <li className="lf-muted lf-small">No plays match.</li>}
      </ol>
    </section>
  )
}
