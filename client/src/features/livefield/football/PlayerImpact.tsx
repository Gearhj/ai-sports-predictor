/**
 * PLAYER IMPACT: who is involved and what happened on his plays. "EPA" is the expected points added by
 * every snap he was the passer, target or runner on: shared credit with blockers and teammates, labelled
 * as such. Click a row to light up his plays across the page.
 */
import { useMemo, useState } from 'react'
import type { EpaPlay } from './ep'
import { parseFbPlay, visibleColor, type FbLive, type FbPlay, type FbTeam } from './footballData'
import { findPlayer } from './playParser'
import type { PulseCard } from './GamePulse'
import { Headshot } from '../Diamond'

interface Row { key: string; name: string; pos: string; jersey: string | null; headshot: string | null; id: string | null; snaps: number; yards: number; epa: number; ids: string[]; line: string }

interface Props { fb: FbLive; epa: EpaPlay[]; away: FbTeam; home: FbTeam; defaultTeamId: string | null; active: string | null; onShow: (c: PulseCard | null) => void }

export function PlayerImpact({ fb, epa, away, home, defaultTeamId, active, onShow }: Props) {
  const [pick, setPick] = useState<string | null>(null)
  const [sort, setSort] = useState<'epa' | 'snaps'>('epa')
  const teamId = pick ?? defaultTeamId ?? away.id
  const team = teamId === home.id ? home : away
  const epaById = useMemo(() => new Map(epa.map((e) => [e.id, e.epa])), [epa])
  const rows = useMemo(() => {
    const map = new Map<string, Row & { drop: number; tgt: number; rec: number; car: number }>()
    const touch = (name: string | null, role: 'QB' | 'tgt' | 'car', p: FbPlay, kind: string) => {
      if (!name) return
      const rp = findPlayer(name, fb.players, teamId)
      const key = rp?.id ?? name
      const r = map.get(key) ?? { key, name: rp ? `${rp.first} ${rp.last}` : name, pos: rp?.pos ?? (role === 'QB' ? 'QB' : role === 'car' ? 'RB' : 'WR'), jersey: rp?.jersey ?? null, headshot: rp?.headshot ?? null, id: rp?.id ?? null, snaps: 0, yards: 0, epa: 0, ids: [], line: '', drop: 0, tgt: 0, rec: 0, car: 0 }
      if (role === 'QB') { r.drop += 1; if (kind === 'pass') r.yards += p.yards ?? 0 }
      if (role === 'tgt') { r.tgt += 1; if (kind === 'pass') { r.rec += 1; r.yards += p.yards ?? 0 } }
      if (role === 'car') { r.car += 1; r.yards += p.yards ?? 0 }
      if (!r.ids.includes(p.id)) { r.ids.push(p.id); r.snaps += 1; r.epa += epaById.get(p.id) ?? 0 }
      map.set(key, r)
    }
    for (const p of fb.plays ?? []) {
      if ((p.offense ?? p.start.team) !== teamId) continue
      const k = parseFbPlay(p)
      if (k.kind === 'pass' || k.kind === 'incomplete' || k.kind === 'interception') { touch(k.passer, 'QB', p, k.kind); touch(k.target, 'tgt', p, k.kind) }
      else if (k.kind === 'sack') touch(k.passer, 'QB', p, 'sack')
      else if (k.kind === 'rush') touch(k.rusher, 'car', p, 'rush')
    }
    const list = [...map.values()].map((r) => ({ ...r, line: r.drop ? `${r.drop} dropbacks · ${r.yards} yds` : r.car && !r.tgt ? `${r.car} car · ${r.yards} yds` : r.car ? `${r.car} car · ${r.rec}/${r.tgt} rec · ${r.yards} yds` : `${r.rec}/${r.tgt} targets · ${r.yards} yds` }))
    list.sort((a, b) => (sort === 'epa' ? b.epa - a.epa : b.snaps - a.snaps || b.epa - a.epa))
    return list.slice(0, 7)
  }, [fb.plays, fb.players, teamId, epaById, sort])
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.epa)))
  return (
    <section className="lf-card pi" aria-label="Player impact">
      <header className="am__head">
        <h3>PLAYER IMPACT</h3>
        <span className="fbd-tabs">
          <button type="button" className={teamId === away.id ? 'is-on' : ''} style={{ ['--tc' as string]: visibleColor(away, '#3fd8c4') }} onClick={() => setPick(away.id)}>{away.abbr}</button>
          <button type="button" className={teamId === home.id ? 'is-on' : ''} style={{ ['--tc' as string]: visibleColor(home, '#ffb547') }} onClick={() => setPick(home.id)}>{home.abbr}</button>
          <select value={sort} onChange={(e) => setSort(e.target.value as 'epa' | 'snaps')} aria-label="Sort"><option value="epa">Sort by EPA</option><option value="snaps">Most involved</option></select>
        </span>
      </header>
      {rows.length ? (
        <table className="pi__table">
          <thead><tr><th>#</th><th>Player</th><th>Pos</th><th>Snaps</th><th>EPA</th><th>Impact</th></tr></thead>
          <tbody>
            {rows.map((r) => {
              const id = `player-${r.key}`
              return (
                <tr key={r.key} className={active === id ? 'is-active' : ''} onClick={() => onShow(active === id ? null : { key: id, headline: r.name, detail: r.line, stat: '', ids: r.ids, teamId, tone: 'neutral', score: 1 })}>
                  <td>{r.jersey ?? '–'}</td>
                  <td className="pi__who"><Headshot person={r.id ? { id: Number(r.id), name: r.name, headshot: r.headshot ?? '' } : null} size={26} /><span><b>{r.name}</b><small>{r.line}</small></span></td>
                  <td>{r.pos}</td>
                  <td>{r.snaps}</td>
                  <td className={r.epa >= 0 ? 'is-up' : 'is-down'}>{r.epa >= 0 ? '+' : ''}{r.epa.toFixed(1)}</td>
                  <td><i className="pi__bar"><u className={r.epa >= 0 ? 'is-up' : 'is-down'} style={{ width: `${Math.max(4, (Math.abs(r.epa) / maxAbs) * 100)}%` }} /></i></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      ) : <p className="lf-muted lf-small">{team.abbr}: named players appear as plays are logged.</p>}
      <p className="lf-muted lf-tiny">EPA on plays he was passer, target or runner on (shared credit). Snaps = plays he is named on. Click a row to light up his plays.</p>
    </section>
  )
}
