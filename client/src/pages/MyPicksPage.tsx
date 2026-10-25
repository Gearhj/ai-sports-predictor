/**
 * My Picks — one of the five primary destinations.
 *
 * The reader's PERSONAL play-money PREVIEW picks: games they chose to track from
 * a Game Brief. Play money, no cash value, stored only in this browser; places no
 * price, records no decision, settles no result. It is a different record from the
 * official AISP $1,000 paper experiment (Record / `/experiment`), never merged.
 */
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/ui'
import { removePick, usePreviewPicks } from '../services/previewPicks'
import './myPicks.css'

const pct = (v: number | null | undefined) => (v == null ? null : `${Math.round(v * 100)}%`)
const when = (iso: string) => { try { return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) } catch { return '' } }

export function MyPicksPage() {
  const picks = usePreviewPicks()
  return (
    <div className="page mypicks">
      <PageHeader
        eyebrow="MY PICKS"
        title="Your personal picks"
        description="Play-money preview picks you tracked from a Game Brief — separate from the AISP paper experiment under Record."
      />
      <p className="mypicks__banner" role="note">
        <b>Personal preview · play money.</b> No cash value; stored only in this browser. These place no price,
        record no decision and settle no result. They are <b>not</b> the official AISP $1,000 paper experiment
        (<Link to="/experiment">Record</Link>), and following another entrant never copies picks.
      </p>

      {picks.length === 0 ? (
        <section className="mypicks__empty" role="status">
          <h2>No preview picks yet</h2>
          <p>Open a <Link to="/games">game</Link>, read its Game Brief, and choose a side under <b>My Picks</b> to track it here.
            Your picks stay personal and separate from the official paper record.</p>
          <div className="mypicks__emptyactions">
            <Link className="secondary-button" to="/games">Browse games</Link>
            <Link className="secondary-button" to="/a-house-divided">See A House Divided</Link>
          </div>
        </section>
      ) : (
        <ul className="mypicks__list" aria-label="Your preview picks">
          {picks.map((p) => {
            const sideP = p.model_home_two_way == null ? null : (p.side === 'HOME' ? p.model_home_two_way : 1 - p.model_home_two_way)
            return (
              <li key={p.id} className="mypicks__item" style={{ ['--sport' as string]: 'var(--teal, #0b7f79)' }}>
                <div className="mypicks__main">
                  <Link className="mypicks__game" to={p.href}>{p.matchup}</Link>
                  <span className="mypicks__sport">{p.sport}</span>
                  <span className="mypicks__added">tracked {when(p.added_at)}</span>
                </div>
                <div className="mypicks__pick">
                  <span className="mypicks__k">Your preview pick</span>
                  <b>{p.selection}</b>
                </div>
                <div className="mypicks__model">
                  <span className="mypicks__k">Model lean</span>
                  <span>{sideP == null ? 'No forecast' : `${p.selection} ${pct(sideP)}`}</span>
                </div>
                <div className="mypicks__actions">
                  <Link className="secondary-button" to={p.href}>Open Game Brief</Link>
                  <button type="button" className="mypicks__remove" onClick={() => removePick(p.game_id)} aria-label={`Remove ${p.matchup} from My Picks`}>Remove</button>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
