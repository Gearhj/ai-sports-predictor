/** Side cards for the football view: team stats, leaders, tendencies, the play list. ESPN data, display only. */
import { ordinalDown, parseFbPlay, tendencies, type FbLive, type FbPlay } from './footballData'
import { Headshot } from '../Diamond'

const num = (v: string | undefined) => { const n = Number(String(v ?? '').replace(/[^0-9.\-]/g, '')); return Number.isFinite(n) ? n : null }

const ROWS: Array<{ key: string; label: string; numeric: boolean; lowerBetter?: boolean }> = [
  { key: 'totalYards', label: 'Total yards', numeric: true },
  { key: 'netPassingYards', label: 'Passing yards', numeric: true },
  { key: 'rushingYards', label: 'Rushing yards', numeric: true },
  { key: 'firstDowns', label: 'First downs', numeric: true },
  { key: 'thirdDownEff', label: '3rd down', numeric: false },
  { key: 'turnovers', label: 'Turnovers', numeric: true, lowerBetter: true },
  { key: 'possessionTime', label: 'Time of possession', numeric: false },
]

export function StatsCard({ fb }: { fb: FbLive }) {
  const { away, home } = fb.teams!
  const a = fb.stats?.[away.id] ?? {}
  const h = fb.stats?.[home.id] ?? {}
  if (!Object.keys(a).length && !Object.keys(h).length) return <section className="lf-card"><h3>Team stats</h3><p className="lf-muted lf-small">Box score appears once the game is under way.</p></section>
  return (
    <section className="lf-card fb-stats" aria-label="Team stats">
      <header className="fb-stats__head"><b>{away.abbr}</b><h3>Team stats</h3><b>{home.abbr}</b></header>
      {ROWS.filter((r) => a[r.key] != null || h[r.key] != null).map((r) => {
        const av = num(a[r.key]); const hv = num(h[r.key])
        const total = (av ?? 0) + (hv ?? 0)
        const ap = r.numeric && total > 0 ? ((av ?? 0) / total) * 100 : 50
        const lead = r.numeric && av != null && hv != null && av !== hv ? ((av > hv) !== Boolean(r.lowerBetter) ? 'a' : 'h') : ''
        return (
          <div key={r.key} className="fb-stat">
            <span className={lead === 'a' ? 'is-lead' : ''}>{a[r.key] ?? '—'}</span>
            <span className="fb-stat__mid"><small>{r.label}</small>{r.numeric ? <i className="fb-stat__bar"><em style={{ width: `${ap}%` }} /></i> : null}</span>
            <span className={lead === 'h' ? 'is-lead' : ''}>{h[r.key] ?? '—'}</span>
          </div>
        )
      })}
      <p className="lf-muted lf-small">ESPN box score, as of the last refresh.</p>
    </section>
  )
}

export function LeadersCard({ fb }: { fb: FbLive }) {
  const leaders = fb.leaders ?? []
  const shown = leaders.filter((l) => ['passingYards', 'rushingYards', 'receivingYards'].includes(l.cat))
  if (!shown.length) return null
  const slug = fb.league === 'college-football' ? 'college-football' : 'nfl'
  const team = (id: string) => (id === fb.teams!.home.id ? fb.teams!.home : fb.teams!.away)
  return (
    <section className="lf-card fb-leaders" aria-label="Game leaders">
      <h3>Game leaders</h3>
      {shown.map((l) => (
        <div key={`${l.team}-${l.cat}`} className="fb-leader">
          <Headshot person={l.id ? { id: Number(l.id), name: l.name, headshot: `https://a.espncdn.com/i/headshots/${slug}/players/full/${l.id}.png` } : null} size={40} />
          <span><small>{team(l.team).abbr} · {l.label}</small><br /><b>{l.name}</b> <span className="lf-muted">{l.line}</span></span>
        </div>
      ))}
    </section>
  )
}

export function TendenciesCard({ fb, offenseId }: { fb: FbLive; offenseId: string | null }) {
  const { away, home } = fb.teams!
  const rows = [away, home].map((t) => ({ t, x: tendencies(fb.plays, t.id) }))
  if (!rows.some((r) => r.x.pass + r.x.run > 0)) return null
  return (
    <section className="lf-card fb-tend" aria-label="Play-calling this game">
      <h3>What each offense is doing <small className="lf-muted">this game</small></h3>
      {rows.map(({ t, x }) => (
        <div key={t.id} className={`fb-tend__row${offenseId === t.id ? ' is-on' : ''}`}>
          <b>{t.abbr}</b>
          <span className="fb-tend__bar" title={`${x.pass} pass plays, ${x.run} runs`}><i style={{ width: `${(x.passPct ?? 0) * 100}%` }} /></span>
          <span>{x.passPct != null ? `${Math.round(x.passPct * 100)}% pass` : '—'} · {x.ypp != null ? `${x.ypp.toFixed(1)} yds/play` : ''}</span>
          <small className="lf-muted">pass share by down: {[1, 2, 3, 4].map((d) => { const b = x.byDown[d]; const n = b.pass + b.run; return `${d}: ${n ? Math.round((b.pass / n) * 100) + '%' : '–'}` }).join(' · ')}</small>
        </div>
      ))}
      <p className="lf-muted lf-small">Counted from ESPN's plays so far (passes, sacks and runs). A tendency, not a prediction.</p>
    </section>
  )
}

export function PlayList({ plays, selectedId, latestId, onSelect, highlight = null }: { plays: readonly FbPlay[]; selectedId: string | null; latestId: string | null; onSelect: (id: string) => void; highlight?: Set<string> | null }) {
  const rows = [...plays].reverse().filter((p) => parseFbPlay(p).kind !== 'nonplay' && (!highlight || highlight.has(p.id))).slice(0, highlight ? 40 : 14)
  if (!rows.length) return null
  return (
    <section className="lf-plays" aria-label="Last plays">
      <h3>{highlight ? 'Evidence' : 'Last plays'} <small className="lf-muted">ESPN · click one to replay it on the field</small></h3>
      <ol>
        {rows.map((p) => {
          const k = parseFbPlay(p)
          return (
            <li key={p.id} className={`${p.scoring ? 'is-score' : ''}${(selectedId ?? latestId) === p.id ? ' is-picked' : ''}`}>
              <button type="button" className="fb-play" onClick={() => onSelect(p.id)}>
                <span className="lf-plays__when">Q{p.period} {p.clock}</span>
                <span className="lf-plays__text"><b>{ordinalDown(p.start.down) ? `${ordinalDown(p.start.down)} & ${p.start.distance} · ` : ''}{k.label}</b> {p.text}</span>
                <span className="lf-plays__score">{p.awayScore ?? ''}–{p.homeScore ?? ''}</span>
              </button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
