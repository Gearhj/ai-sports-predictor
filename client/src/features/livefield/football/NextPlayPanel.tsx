/** "What will the offense call?" The model's list of possible plays, right next to the field. */
import { CLASSES, LABELS, categoryProbs, type Category, type Forecast, type Grid, type PlayClass } from './playcall'

interface Props {
  forecast: Forecast | null
  next: { forecast: Forecast; team: string } | null
  grid: Grid | null
  team: string
  actualClass: PlayClass | null
  actualCategory: Category | null
  wpa: number | null
  league: 'nfl' | 'college-football'
  live: boolean
}

const pct = (p: number) => `${p >= 0.995 ? 100 : Math.max(0, Math.round(p * 100))}%`

export function NextPlayPanel({ forecast, next, grid, team, actualClass, actualCategory, wpa, league, live }: Props) {
  if (league === 'college-football') {
    return (
      <section className="lf-card fb-next" aria-label="Next play probabilities">
        <h3>What will the offense call?</h3>
        <p className="lf-muted lf-small">The play-calling model is trained on NFL snaps. College offenses call plays differently, so no numbers are shown for college games until a college model exists.</p>
      </section>
    )
  }
  if (!grid || !forecast) {
    return (
      <section className="lf-card fb-next" aria-label="Next play probabilities">
        <h3>What will the offense call?</h3>
        <p className="lf-muted lf-small">{grid ? 'Waiting for a scrimmage play with a down and distance.' : 'Loading the play-calling model…'}</p>
      </section>
    )
  }
  const order = CLASSES.map((c, i) => ({ c, p: forecast.probs[i] })).filter((r) => r.p >= 0.004).sort((a, b) => b.p - a.p)
  const cat = categoryProbs(forecast.probs)
  const kick = cat.PUNT + cat.FG + cat.KNEEL
  const called = actualClass ? forecast.probs[CLASSES.indexOf(actualClass)] : actualCategory ? cat[actualCategory] : null
  const oneIn = called && called > 0 ? Math.round(1 / called) : null
  const tiltPts = Math.round(forecast.tilt.delta * 100)
  const v = grid.meta.validation
  return (
    <section className="lf-card fb-next" aria-label="Next play probabilities">
      <header className="fb-next__head">
        <div>
          <h3>What will {team} call?</h3>
          <p className="lf-muted lf-small">{actualCategory ? 'The situation before this snap' : 'The next snap'} · {forecast.state.down}{['', 'st', 'nd', 'rd', 'th'][forecast.state.down]} &amp; {forecast.state.distance}, {forecast.state.yardsToEndzone} yds from the end zone</p>
        </div>
        {live ? <span className="lf-pill lf-pill--test">LIVE MODEL</span> : null}
      </header>
      <div className="fb-next__mix" role="img" aria-label={`Pass ${pct(cat.PASS)}, run ${pct(cat.RUN)}, kick or kneel ${pct(kick)}`}>
        <i className="is-pass" style={{ width: `${cat.PASS * 100}%` }}><b>PASS {pct(cat.PASS)}</b></i>
        <i className="is-run" style={{ width: `${cat.RUN * 100}%` }}><b>RUN {pct(cat.RUN)}</b></i>
        {kick >= 0.02 ? <i className="is-kick" style={{ width: `${kick * 100}%` }}><b>{pct(kick)}</b></i> : null}
      </div>
      <ol className="fb-next__list">
        {order.map(({ c, p }, rank) => {
          const hit = actualClass === c
          return (
            <li key={c} className={`fb-next__row fb-next__row--${LABELS[c].group}${hit ? ' is-called' : actualClass || actualCategory ? ' is-dim' : ''}${rank === 0 && !actualClass ? ' is-top' : ''}`}>
              <span className="fb-next__name">{hit ? '✔ ' : ''}{LABELS[c].name}</span>
              <span className="fb-next__bar"><i style={{ width: `${Math.max(2, (p / order[0].p) * 100)}%` }} /></span>
              <span className="fb-next__pct">{pct(p)}</span>
            </li>
          )
        })}
      </ol>
      {actualCategory ? (
        <p className="fb-next__reveal">
          {actualClass ? <>They called <b>{LABELS[actualClass].name.toLowerCase()}</b>. </> : <>They called a <b>{actualCategory.toLowerCase()}</b>. </>}
          {called != null && oneIn != null ? <>The model gave that <b>{pct(called)}</b>{called < 0.15 ? `, a 1-in-${oneIn} call` : ''}. </> : null}
          {wpa != null ? <>Win probability <b className={wpa >= 0 ? 'is-up' : 'is-down'}>{wpa >= 0 ? '+' : ''}{wpa.toFixed(1)} pts</b> for the offense.</> : null}
        </p>
      ) : null}
      {next && actualCategory ? (
        <div className="fb-next__up">
          <b>UP NEXT · {next.team}</b>
          <span>{next.forecast.state.down}{['', 'st', 'nd', 'rd', 'th'][next.forecast.state.down]} &amp; {next.forecast.state.distance}: {CLASSES.map((c, i) => ({ c, p: next.forecast.probs[i] })).sort((a, b) => b.p - a.p).slice(0, 3).map((r) => `${LABELS[r.c].name} ${pct(r.p)}`).join(' · ')}</span>
        </div>
      ) : null}
      <p className="lf-muted lf-small fb-next__foot">
        {tiltPts !== 0 && forecast.tilt.n >= 6 ? `Adjusted for this offense: it has passed ${Math.abs(tiltPts)} pts ${tiltPts > 0 ? 'more' : 'less'} than the model expected over its last ${forecast.tilt.n} snaps. ` : ''}
        Model: gradient boosting on {grid.meta.trainedOn.replace('nflverse play-by-play ', 'NFL snaps ')}. On 2025-26 plays it calls pass vs run right {Math.round(v.pass_vs_run_accuracy_grid * 100)}% of the time (always guessing the common one: {Math.round(v.pass_vs_run_always_majority * 100)}%). Direction and depth are far less predictable than run vs pass. A forecast of the call, not the result, and never a wager signal.
      </p>
    </section>
  )
}
