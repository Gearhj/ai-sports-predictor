/**
 * The front door: one real matchup, its forecast, and its simulation moving.
 *
 * WHAT REPLACED WHAT
 * ------------------
 * This page used to open with a masthead, a connectivity banner and a sentence
 * about how many decisions were left. All three are things ABOUT the product.
 * None of them is the product. A visitor who has never seen AISP before learned
 * that an API was reachable.
 *
 * So the fold now carries a game: who is playing, when, what the model thinks,
 * and a field with the saved simulation already accumulating on it. The
 * interaction is immediate and it is real evidence -- the same artifacts the
 * lab draws, not a decorative loop.
 *
 * WHAT THE NUMBERS ARE ALLOWED TO SAY
 * -----------------------------------
 * A research-only league still HAS a forecast. It simply has no execution
 * authority, which is a different fact and is stated as its own chip rather
 * than being rendered as a failed bet. The old page showed "Official positions
 * 0 / Final qualified decisions 0" under NFL, which reads as a model that tried
 * and failed a funded policy. NFL never entered one.
 */
import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, CircleDot, FlaskConical, Pause, Play } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PopulationField } from '../nfl/PopulationField'
import {
  DEFAULT_FILTERS, matches, POPULATION_PALETTE, type FamilyFilter,
} from '../nfl/populationGeometry'
import { useNflPlayPopulation } from '../../hooks/useNflPlayPopulation'
import type { HomeCandidate } from '../../types/nfl'

const PLAYS_PER_SECOND = 70
const PREVIEW_LIMIT = 260

function kickoffLabel(iso: string): string {
  const at = new Date(iso)
  return at.toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  })
}

export function FeaturedMatchup({
  candidate, criteria,
}: {
  candidate: HomeCandidate
  criteria: { ordering: string[]; excluded: string }
}) {
  const [scenario, setScenario] = useState('BASE')
  const [family, setFamily] = useState<FamilyFilter>('BOTH')
  const [running, setRunning] = useState(true)
  const [revealed, setRevealed] = useState(0)
  const [repaintKey, setRepaintKey] = useState(0)

  const scenarios = candidate.simulation.scenarios
  const usable = scenarios.includes(scenario) ? scenario : (scenarios[0] ?? 'BASE')
  const { population, loading } = useNflPlayPopulation(
    candidate.simulation.explorable ? candidate.game_id : '', usable,
  )

  const plays = useMemo(() => {
    const all = population?.plays ?? []
    const filtered = all.filter((play) => matches(play, { ...DEFAULT_FILTERS, family }))
    return filtered.slice(0, PREVIEW_LIMIT)
  }, [population, family])

  useEffect(() => { setRevealed(0); setRepaintKey((key) => key + 1) }, [plays])

  useEffect(() => {
    if (!running || plays.length === 0) return
    let last = performance.now()
    let carry = 0
    const timer = window.setInterval(() => {
      const now = performance.now()
      carry += Math.min(0.25, (now - last) / 1000) * PLAYS_PER_SECOND
      last = now
      const whole = Math.floor(carry)
      if (whole > 0) {
        carry -= whole
        setRevealed((current) => {
          // Loop rather than stop: the fold should always be alive when a
          // visitor arrives, whatever moment they arrive at.
          if (current + whole >= plays.length) { setRepaintKey((key) => key + 1); return 0 }
          return current + whole
        })
      }
    }, 16)
    return () => window.clearInterval(timer)
  }, [running, plays])

  const forecast = candidate.forecast
  const homePct = forecast ? forecast.home_win_probability * 100 : null
  const awayPct = forecast ? forecast.away_win_probability * 100 : null

  return (
    <section className="feature" aria-labelledby="feature-heading">
      <div className="feature__intro">
        <p className="feature__eyebrow">
          <CircleDot size={12} aria-hidden="true" />
          NEXT UP · {candidate.league_code} · {kickoffLabel(candidate.starts_at_utc)}
        </p>
        <h1 id="feature-heading" className="feature__title">
          <span>{candidate.away.name}</span>
          <em>at</em>
          <span>{candidate.home.name}</span>
        </h1>
        <div className="feature__chips">
          {candidate.research_only && (
            <span className="feature__chip feature__chip--research">
              <FlaskConical size={13} aria-hidden="true" />
              Research forecast · this league has no funded position
            </span>
          )}
          {forecast && (
            <span className="feature__chip">{forecast.engine_id.replace(/_/g, ' ')}</span>
          )}
        </div>
      </div>

      {forecast && (
        <div className="feature__numbers">
          <div className="feature__winprob">
            <div className="feature__winbar" role="img" aria-label={
              `Model win probability: ${candidate.away.abbreviation} ${awayPct?.toFixed(1)}%, ` +
              `${candidate.home.abbreviation} ${homePct?.toFixed(1)}%`
            }>
              <span className="feature__winbar--away" style={{ flexGrow: awayPct ?? 50 }} />
              <span className="feature__winbar--home" style={{ flexGrow: homePct ?? 50 }} />
            </div>
            <p>
              <b>{candidate.away.abbreviation} {awayPct?.toFixed(1)}%</b>
              <span>model win probability</span>
              <b>{candidate.home.abbreviation} {homePct?.toFixed(1)}%</b>
            </p>
          </div>
          <dl className="feature__stats">
            <div>
              <dt>Projected score</dt>
              <dd>{forecast.expected_away_score.toFixed(1)} – {forecast.expected_home_score.toFixed(1)}</dd>
            </div>
            <div><dt>Total</dt><dd>{forecast.expected_total.toFixed(1)}</dd></div>
            <div>
              <dt>Margin</dt>
              <dd>{forecast.expected_margin_home > 0 ? '+' : ''}{forecast.expected_margin_home.toFixed(1)} {candidate.home.abbreviation}</dd>
            </div>
          </dl>
        </div>
      )}

      <div className="feature__stage">
        {candidate.simulation.explorable ? (
          <>
            <PopulationField
              plays={plays}
              revealed={revealed}
              persistTrails
              repaintKey={repaintKey}
              homeAbbr={candidate.home.abbreviation}
              awayAbbr={candidate.away.abbreviation}
              height={300}
            />
            <div className="feature__previewbar">
              <button type="button" className="pop-btn pop-btn--primary" onClick={() => setRunning((r) => !r)}>
                {running ? <Pause size={14} /> : <Play size={14} />}{running ? 'Pause' : 'Play'}
              </button>
              <div className="pop-seg">
                {(['BOTH', 'PASS', 'RUN'] as FamilyFilter[]).map((value) => (
                  <button
                    key={value} type="button"
                    className={`pop-seg__btn${family === value ? ' pop-seg__btn--on' : ''}`}
                    onClick={() => setFamily(value)}
                  >{value === 'BOTH' ? 'All plays' : value === 'PASS' ? 'Passes' : 'Runs'}</button>
                ))}
              </div>
              <select value={usable} onChange={(event) => setScenario(event.target.value)} aria-label="Scenario">
                {scenarios.map((id) => (
                  <option key={id} value={id}>{id.replace(/_/g, ' ').toLowerCase()}</option>
                ))}
              </select>
              <ul className="feature__legend">
                <li><i style={{ background: POPULATION_PALETTE.PASS }} />Pass</li>
                <li><i style={{ background: POPULATION_PALETTE.RUN }} />Run</li>
                <li><i style={{ background: POPULATION_PALETTE.TURNOVER }} />Turnover</li>
              </ul>
            </div>
            <p className="feature__evidence">
              {loading && !population ? 'Loading the saved simulation…' : (
                <>
                  Drawing <b>{Math.min(revealed, plays.length).toLocaleString()}</b> of{' '}
                  <b>{plays.length.toLocaleString()}</b> modeled plays from{' '}
                  <b>{population?.coverage.games_with_play_paths ?? 0}</b> representative games.
                  The scenario simulated{' '}
                  <b>{(population?.coverage.simulated_games ?? 0).toLocaleString()}</b>; the rest were
                  saved as final scores only. Modeled schematic geometry, not tracking.
                </>
              )}
            </p>
          </>
        ) : (
          <p className="feature__evidence">
            No saved play-level simulation is published for this matchup yet.
          </p>
        )}
      </div>

      <div className="feature__actions">
        <Link className="feature__cta" to={candidate.explore_href}>
          Explore this game <ArrowRight size={16} aria-hidden="true" />
        </Link>
        <details className="feature__why">
          <summary>Why this game?</summary>
          <ol>{criteria.ordering.map((line) => <li key={line}>{line}</li>)}</ol>
          <p>{criteria.excluded}</p>
        </details>
      </div>
    </section>
  )
}
