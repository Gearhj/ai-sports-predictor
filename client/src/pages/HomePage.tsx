/**
 * The AI Sports Predictor home page.
 *
 * WHAT THIS PAGE LEADS WITH, AND WHY IT CHANGED
 * ---------------------------------------------
 * It used to open with a masthead, a green "LIVE DATA · Connected to the ASP
 * API" banner, and a headline counting how many decisions were left. Every one
 * of those is a statement about the SYSTEM. A first-time visitor learned that a
 * server was reachable, which is not evidence about sports and certainly not a
 * reason to stay.
 *
 * Now the fold is a real matchup with its forecast and its saved simulation
 * already moving, chosen by a published rule (soonest kickoff among games that
 * have simulation evidence -- never by result). Everything about the system's
 * own health moved into a drawer at the foot of the page, where an operator can
 * still reach it in one click and a visitor is not asked to read it first.
 *
 * TWO CLAIMS THIS PAGE IS CAREFUL NOT TO MAKE
 * -------------------------------------------
 *  - API connectivity is not freshness. "Connected" says nothing about whether
 *    the odds or forecasts behind a number are current, so the freshness that
 *    matters is stated beside the content it affects, not as a banner.
 *  - A research-only league has not failed anything. NFL carries no execution
 *    authority, so "0 official positions" is not a result; it is the absence of
 *    a policy. It is labelled as research, not as a zero.
 */
import { useCallback } from 'react'
import { ArrowRight, Beaker, LineChart, Radio } from 'lucide-react'
import { Link } from 'react-router-dom'
import { FeaturedMatchup } from '../components/home/FeaturedMatchup'
import { EquityCurveChart } from '../charts/EquityCurveChart'
import { DataStatePanel, formatCurrency, formatSignedCurrency } from '../components/ui'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { useSportSelection } from '../hooks/useSportSelection'
import type { BankrollData } from '../types/domain'
import type { HomeCandidate, HomeFeatured } from '../types/nfl'
import '../components/home/home.css'

function kickoffShort(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: 'short', hour: 'numeric', minute: '2-digit',
  })
}

export function HomePage() {
  const featuredResource = useApiResource<HomeFeatured>(
    useCallback((signal: AbortSignal) => endpoints.homeFeatured(signal), []),
  )
  const bankrollResource = useApiResource<BankrollData>(
    useCallback((signal: AbortSignal) => endpoints.bankroll(signal, 'v2'), []),
  )
  const data = featuredResource.data
  const featured = data?.featured ?? null

  return (
    <div className="page home-page">
      {featured ? (
        <FeaturedMatchup candidate={featured} criteria={data!.selection_criteria} />
      ) : (
        <DataStatePanel
          state={featuredResource.state}
          emptyTitle="No upcoming matchup is scheduled"
          emptyDetail="Every game in the registry has already started. The next dated slate will appear here as soon as one is scheduled."
          unavailableTitle="The featured matchup could not be loaded"
          unavailableDetail={featuredResource.error ?? 'The read model could not be reached.'}
        />
      )}

      {data && data.candidates.length > 1 && (
        <section className="upnext" aria-labelledby="upnext-heading">
          <div className="section-heading">
            <div>
              <span className="eyebrow">UP NEXT</span>
              <h2 id="upnext-heading">More matchups</h2>
            </div>
            <p className="upnext__note">
              {data.counts.with_simulation_evidence} of the next {data.counts.upcoming_games} scheduled
              games carry saved simulation evidence.
            </p>
          </div>
          <div className="upnext__rail">
            {data.candidates.map((candidate) => (
              <MatchupCard
                key={candidate.game_id}
                candidate={candidate}
                current={candidate.game_id === featured?.game_id}
              />
            ))}
          </div>
        </section>
      )}

      <section className="home-split">
        <ExperimentPanel resource={bankrollResource} />
        <LabEntries />
      </section>

      <StatusDrawer />
    </div>
  )
}

/** One compact upcoming game. Never shows a result — these have not happened. */
function MatchupCard({ candidate, current }: { candidate: HomeCandidate; current: boolean }) {
  const forecast = candidate.forecast
  return (
    <Link
      to={candidate.explore_href}
      className={`matchup-card${current ? ' matchup-card--current' : ''}`}
      aria-current={current ? 'true' : undefined}
    >
      <span className="matchup-card__when">
        {candidate.league_code} · {kickoffShort(candidate.starts_at_utc)}
      </span>
      <span className="matchup-card__teams">
        <b>{candidate.away.abbreviation}</b> at <b>{candidate.home.abbreviation}</b>
      </span>
      {forecast ? (
        <span className="matchup-card__bar" role="img" aria-label={
          `${candidate.away.abbreviation} ${(forecast.away_win_probability * 100).toFixed(0)}%, ` +
          `${candidate.home.abbreviation} ${(forecast.home_win_probability * 100).toFixed(0)}%`
        }>
          <i style={{ flexGrow: forecast.away_win_probability * 100 }} />
          <em style={{ flexGrow: forecast.home_win_probability * 100 }} />
        </span>
      ) : <span className="matchup-card__bar matchup-card__bar--none" />}
      <span className="matchup-card__meta">
        {forecast
          ? `${(forecast.away_win_probability * 100).toFixed(0)}% · ${(forecast.home_win_probability * 100).toFixed(0)}%`
          : 'No forecast yet'}
        {candidate.simulation.explorable && <b> · simulation</b>}
      </span>
    </Link>
  )
}

/**
 * The $1,000 experiment, chart first.
 *
 * Sample size and research status sit BESIDE the curve rather than in front of
 * it. Ten settlements is far too few to claim anything and the panel says so —
 * but a reader who cannot see the curve cannot judge that for themselves.
 */
function ExperimentPanel({ resource }: { resource: ReturnType<typeof useApiResource<BankrollData>> }) {
  const data = resource.data
  const settled = data?.equity_curve?.filter((point) => point.sequence !== 0).length ?? 0
  return (
    <section className="home-panel" aria-labelledby="experiment-heading">
      <div className="home-panel__head">
        <div>
          <span className="eyebrow">THE $1,000 EXPERIMENT</span>
          <h2 id="experiment-heading">Every settlement, in order</h2>
        </div>
        <Link to="/experiment?sport=MLB" className="home-panel__more">
          Open <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </div>
      {data && settled > 0 ? (
        <>
          <EquityCurveChart
            curve={data.equity_curve}
            height={220}
            compact
            formatCurrency={formatCurrency}
            formatSignedCurrency={formatSignedCurrency}
          />
          <dl className="home-panel__facts">
            <div><dt>Settled positions</dt><dd>{settled}</dd></div>
            <div><dt>Bankroll</dt><dd>{formatCurrency(data.current_bankroll)}</dd></div>
            <div><dt>Realised</dt><dd>{formatSignedCurrency(data.total_return)}</dd></div>
            <div><dt>Status</dt><dd className="home-panel__status">Virtual research · no real-money wagering</dd></div>
          </dl>
          <p className="home-panel__caveat">
            {settled} settled positions is far too small a sample to separate skill from luck.
            The curve is a record of what the policy did, not evidence that it works.
          </p>
        </>
      ) : (
        <p className="home-panel__caveat">
          No positions have settled yet. The opening bankroll is not a performance result.
        </p>
      )}
    </section>
  )
}

/** Direct entry into each sport's lab. */
function LabEntries() {
  return (
    <section className="home-panel" aria-labelledby="labs-heading">
      <div className="home-panel__head">
        <div>
          <span className="eyebrow">INTERACTIVE LABS</span>
          <h2 id="labs-heading">Run the evidence yourself</h2>
        </div>
      </div>
      <div className="lab-entries">
        <Link to="/labs?sport=NFL" className="lab-entry">
          <Radio size={22} aria-hidden="true" />
          <span>
            <strong>NFL Simulation Lab</strong>
            <small>Watch a modeled play population accumulate, replay one game, compare scenarios.</small>
          </span>
          <ArrowRight size={16} aria-hidden="true" />
        </Link>
        <Link to="/labs?sport=MLB" className="lab-entry">
          <Beaker size={22} aria-hidden="true" />
          <small className="lab-entry__hidden" />
          <span>
            <strong>MLB Diamond Simulator</strong>
            <small>Explore the saved score population and the simulated game paths behind a forecast.</small>
          </span>
          <ArrowRight size={16} aria-hidden="true" />
        </Link>
        <Link to="/performance?sport=MLB" className="lab-entry">
          <LineChart size={22} aria-hidden="true" />
          <span>
            <strong>Model Performance</strong>
            <small>Calibration, Brier score and the sample each rests on.</small>
          </span>
          <ArrowRight size={16} aria-hidden="true" />
        </Link>
      </div>
    </section>
  )
}

/**
 * Scheduler, quota, import and governance diagnostics — collapsed.
 *
 * These matter, and an operator reaches them in one click. They are not what a
 * visitor should read first, and "the API answered" was never evidence that the
 * sports data behind a number was fresh.
 */
function StatusDrawer() {
  const { sport } = useSportSelection()
  return (
    <details className="status-drawer">
      <summary>
        System status, data sources and governance
        <small>Scheduler, provider quota, imports and policy authority</small>
      </summary>
      <div className="status-drawer__body">
        <p>
          Freshness is reported beside the content it affects, because a reachable API says
          nothing about whether the forecast or price behind a number is current.
        </p>
        <ul>
          <li><Link to={`/decisions?sport=${sport}`}>Daily decision board</Link> — every governed decision on the operational date, with its outstanding gates.</li>
          <li><Link to={`/markets?sport=${sport}`}>Markets and crowd</Link> — quote coverage, provider state and the collection ledger.</li>
          <li><Link to="/methodology">Methodology</Link> — policies, thresholds and what each engine is authorised to do.</li>
          <li><Link to="/shadow-board?sport=MLB">Shadow board</Link> — the point-in-time decision record.</li>
        </ul>
      </div>
    </details>
  )
}
