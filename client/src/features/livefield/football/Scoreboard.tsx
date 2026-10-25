/** The scoreboard strip: logos, scores, the situation, and the section tabs. */
import { visibleColor, type FbLive } from './footballData'

export type Tab = 'center' | 'prob' | 'props' | 'tend' | 'drives' | 'scoring' | 'box'
export const TABS: Array<[Tab, string]> = [['center', 'Game Center'], ['prob', 'Live Probabilities'], ['props', 'Player Props'], ['tend', 'Team Tendencies'], ['drives', 'Drive Chart'], ['scoring', 'Scoring'], ['box', 'Box Score']]

interface Props { fb: FbLive; situation: string; spot: string; offenseId: string | null; tab: Tab; onTab: (t: Tab) => void; reviewing: boolean }

export function Scoreboard({ fb, situation, spot, offenseId, tab, onTab, reviewing }: Props) {
  const { away, home } = fb.teams!
  const st = fb.status
  const live = st?.state === 'in'
  return (
    <section className="sb" aria-label="Scoreboard" style={{ ['--ca' as string]: visibleColor(away, '#3fd8c4'), ['--ch' as string]: visibleColor(home, '#ffb547') }}>
      <div className="sb__row">
        {[away, home].map((t, i) => (
          <div key={t.id} className={`sb__team${i ? ' is-home' : ''}`} style={{ ['--tc' as string]: visibleColor(t, '#3fd8c4') }}>
            {t.logo ? <img src={t.logo} alt="" className="sb__logo" /> : null}
            <div className="sb__who"><b>{t.abbr}</b><span>{t.name}</span><small>{t.record ?? ''}{t.record ? ' · ' : ''}{i ? 'home' : 'away'}</small></div>
            <b className="sb__num">{t.score ?? '–'}</b>
            {offenseId === t.id ? <span className="sb__ball" title="Has the ball">🏈</span> : null}
          </div>
        ))}
        <div className="sb__mid">
          <b>{live ? `Q${st?.period} ${st?.clock}` : st?.short ?? ''}</b>
          <span>{situation}</span>
          <small>{spot}</small>
          {reviewing ? <em className="sb__review">REVIEW</em> : null}
        </div>
      </div>
      <nav className="sb__tabs" role="tablist" aria-label="Sections">
        {TABS.map(([k, label]) => <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? 'is-on' : ''} onClick={() => onTab(k)}>{label}</button>)}
      </nav>
    </section>
  )
}
