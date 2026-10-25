/**
 * Game Brief -> My Picks: a personal PREVIEW pick.
 *
 * The reader can track a side for themselves. It reuses the brief's model lean
 * for context (the market and the actual decision are shown separately in the
 * cards above), places NO price, records NO decision and is clearly personal
 * play money — never an official AISP paper position.
 */
import { Link } from 'react-router-dom'
import { gameHref } from '../../services/commandCenter'
import { addPick, removePick, usePreviewPick } from '../../services/previewPicks'
import type { GameStory as Story } from '../../services/gameStory'

const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)

export function MyPickCard({ story }: { story: Story }) {
  const g = story.game
  const away = g.away?.abbreviation ?? 'Away'
  const home = g.home?.abbreviation ?? 'Home'
  const modelHome = story.model_vs_market.model_home_two_way
  const current = usePreviewPick(g.game_id)
  const href = gameHref(g.sport, g.game_id)

  const choose = (side: 'HOME' | 'AWAY') => {
    if (current && current.side === side) { removePick(g.game_id); return }
    addPick({ game_id: g.game_id, sport: g.sport, matchup: `${away} @ ${home}`, away, home, side,
      selection: side === 'HOME' ? home : away, model_home_two_way: modelHome, href })
  }

  return (
    <section className="gs-card gs-mypick" aria-label="Add this game to My Picks (personal preview)">
      <div className="gs-mypick__head">
        <h3>My Picks</h3>
        <span className="gs-mypick__tag">PERSONAL PREVIEW · PLAY MONEY</span>
      </div>
      <p className="gs-sub">
        {modelHome == null
          ? 'No AISP forecast is bound to this game.'
          : <>Model lean (two-way): <b>{home} {pct(modelHome)}</b> · {away} {pct(1 - modelHome)} — a lean, not a recommendation.</>}
        {' '}The observed market and the actual decision are shown in the cards above; this preview places no price and records no decision.
      </p>
      <div className="gs-mypick__btns" role="group" aria-label="Choose a side to track">
        {(['AWAY', 'HOME'] as const).map((side) => {
          const on = current?.side === side
          const label = side === 'HOME' ? home : away
          return (
            <button key={side} type="button" aria-pressed={on} className={`gs-mypick__btn${on ? ' is-on' : ''}`} onClick={() => choose(side)}>
              {on ? `✓ Tracking ${label}` : `Preview pick: ${label}`}
            </button>
          )
        })}
      </div>
      <p className="gs-mypick__note">
        Play money · no cash value · stored only in this browser. <b>Not</b> an official AISP paper position
        (those are under <Link to="/experiment">Record</Link>).
        {current ? <> · <Link to="/my-picks">View My Picks →</Link></> : null}
      </p>
    </section>
  )
}
