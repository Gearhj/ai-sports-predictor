/**
 * Tonight in Sports — the AI Sports Predictor front door.
 *
 * WHAT THIS PAGE USED TO BE, AND WHY IT IS NOT THAT ANY MORE
 * ----------------------------------------------------------
 * The fold was a full-width animated football field with a thousand simulated
 * play paths drawn over it. Three things were wrong with it and none were
 * cosmetic. It made an all-sports product look like an NFL product. A thousand
 * overlapping paths is not a picture of a distribution, it is a grey smear.
 * And some paths appeared to start behind the end line, which tells a visitor
 * -- correctly -- that nobody checked the geometry. The play visualisation is
 * real work and it still exists, in the NFL Simulation Lab, where a reader has
 * asked for exactly that and one path at a time can be told apart.
 *
 * The fold is now the thing the product actually knows: tonight's games across
 * every active sport, each with its forecast, the price it can be compared
 * against, and what the policy decided.
 *
 * HOW THE TWO FEATURED MATCHUPS ARE CHOSEN
 * ----------------------------------------
 * One per league, soonest upcoming start first, among games that have a
 * published forecast. Never by richest artifact -- that is what made an NFL
 * game the front door before -- and never by result, which is not knowable at
 * selection time and would make the page a highlight reel of its own hindsight.
 */
import { SportReadiness } from '../components/readiness/SportReadiness'
import { useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Freshness, SkeletonCards, SkeletonTable } from '../components/ui'
import { AlertTriangle, ArrowRight, Beaker, CalendarClock, Flame, LineChart, Radio } from 'lucide-react'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { useSportSelection } from '../hooks/useSportSelection'
import { apiRequest, endpoints } from '../services/api'
import {
  byDisagreement, byStartTime, isUpcoming, mlbSlate, nflSlate, type SlateGame,
} from '../services/broadcastSlate'
import {
  ActionLink, LeagueTag, Panel, ProbabilityArc, SplitBar, Stat,
  agoOf, american, clockOf, dayOf, decimals, pct, signedPoints, useBroadcastTheme,
} from '../components/broadcast/primitives'
import type { BankrollData, DashboardData } from '../types/domain'
import type { NflEvent, NflWeeks } from '../types/nfl'
import type { TodayAllSportsReadModel } from '../types/multisport'
import type { RffBoard } from '../types/marketIntelligence'
import { TeamLogo } from '../components/TeamLogo'
import { onDarkColor, teamIdentity } from '../services/teamIdentity'
import { RffGauge, bandTone } from '../components/broadcast/RffLab'
import { ModelVsMarketChart } from '../components/charts/d3Charts'
import { BankrollHero } from '../components/experiment/BankrollPanels'
import { EquityDrawdownChart } from '../components/experiment/EquityDrawdownChart'
import {
  bankrollFacts, equitySeries, money, openPositions, settledPositions, signedMoney,
} from '../components/experiment/bankrollMath'
import '../broadcast.css'

interface HomeBoard {
  data_mode: 'live'
  today: TodayAllSportsReadModel | null
  mlb: DashboardData | null
  nfl: { season: number; week: number; events: NflEvent[] } | null
  bankroll: BankrollData | null
  rff: RffBoard | null
  warnings: string[]
}

type NflMarketsBoard = { season: number; week: number; events: NflEvent[] }

export function HomePage() {
  useBroadcastTheme()
  const { sport, withSport } = useSportSelection()

  const loader = useCallback(async (signal: AbortSignal): Promise<HomeBoard> => {
    const warnings: string[] = []
    const safe = async <T,>(name: string, run: () => Promise<T>): Promise<T | null> => {
      try { return await run() } catch (error) {
        if (signal.aborted) throw error
        warnings.push(`${name} unavailable`)
        return null
      }
    }
    const wantsMlb = sport === 'ALL' || sport === 'MLB'
    const wantsNfl = sport === 'ALL' || sport === 'NFL'
    const [today, mlb, weeks, bankroll, rff] = await Promise.all([
      safe('Slate summary', () => endpoints.today(signal)),
      wantsMlb ? safe('MLB slate', () => apiRequest<DashboardData>('/games/slate', {}, signal)) : null,
      wantsNfl ? safe('NFL schedule', () => endpoints.nflWeeks(signal)) : null,
      safe('Experiment bankroll', () => endpoints.bankroll(signal, 'v2')),
      // One card's worth. RFF is a research index and the home page is not its
      // home; the board it comes from lives inside Markets & Crowd.
      safe('RFF index', () => apiRequest<RffBoard>('/markets/rff?limit=1', {}, signal)),
    ])
    const current = (weeks as NflWeeks | null)?.current
    const nfl = current
      ? await safe('NFL markets', () => apiRequest<NflMarketsBoard>(`/nfl/markets/${current.season}/${current.week}`, {}, signal))
      : null
    return { data_mode: 'live', today, mlb, nfl, bankroll, rff, warnings }
  }, [sport])

  const resource = useApiResource(loader, {
    key: cacheKey('home', { sport }),
    staleTimeMs: STALE_TIME.slate,
    refreshIntervalMs: STALE_TIME.slate,
  })
  const board = resource.data

  const games = useMemo(() => {
    const rows = [...mlbSlate(board?.mlb ?? null), ...nflSlate(board?.nfl?.events ?? [])]
    return rows.sort(byStartTime)
  }, [board])

  const upcoming = useMemo(() => games.filter(isUpcoming), [games])
  const featured = useMemo(() => {
    // A game already under way is not "tonight in sports" -- it is a game you
    // missed. Prefer one that has not started; fall back to a started one only
    // when a league has nothing ahead of it, so a league never disappears.
    const pick = (league: 'MLB' | 'NFL') => {
      const forLeague = upcoming.filter((game) => game.league === league && game.homeWin != null)
      const notStarted = forLeague.filter((game) => !['STARTED', 'IN_PROGRESS', 'LIVE'].includes(game.status.toUpperCase()))
      return notStarted[0] ?? forLeague[0] ?? null
    }
    return [pick('MLB'), pick('NFL')].filter((game): game is SlateGame => game != null)
  }, [upcoming])

  const disagreements = useMemo(
    () => games.filter((game) => game.edgePoints != null).sort(byDisagreement).slice(0, 5),
    [games],
  )

  const sections = board?.today?.sport_sections ?? []
  const activeSports = sections.filter((s) => s.availability_state === 'LIVE' || s.scheduled_event_count > 0)
  const freshness = board?.today?.data_freshness
  const zone = board?.today?.display_timezone

  return (
    <div className="bx bx-home">
      <div className="bx__inner">
        <header className="bx-head">
          <div>
            <span className="bx-eyebrow"><Radio size={13} aria-hidden="true" /> AI Sports Predictor</span>
            <h1>Tonight <em>in Sports</em></h1>
            <p className="bx-muted" style={{ marginTop: 8, maxWidth: '52ch' }}>
              Every game the models cover, with the forecast, the price it can be measured
              against, and what the policy decided. Research and simulation only — no wagering.
            </p>
          </div>
          <div className="bx-head__meta">
            <span className="bx-chip"><CalendarClock size={12} aria-hidden="true" />
              {board?.today?.operational_date ? dayOf(`${board.today.operational_date}T12:00:00Z`) : 'Loading slate'}</span>
            <span className="bx-chip">{upcoming.length} upcoming</span>
            <span className="bx-chip">{activeSports.length || '—'} active {activeSports.length === 1 ? 'sport' : 'sports'}</span>
            <Freshness label="Slate" updatedAt={resource.updatedAt} refreshing={resource.refreshing}
              stale={resource.stale} error={board ? resource.error : null} onRetry={resource.refresh} />
          </div>
        </header>
        <SportReadiness sport={sport} />

        {resource.loading && !board && (
          <>
            <SkeletonCards count={3} />
            <SkeletonTable rows={6} columns={5} ariaLabel="Loading tonight’s slate" />
          </>
        )}
        {resource.error && !board && <p className="bx-empty" role="alert">{resource.error}</p>}

        {featured.length > 0 && (
          <div className="bx-home__features">
            {featured.map((game) => <FeatureCard key={game.key} game={game} zone={zone} withSport={withSport} />)}
          </div>
        )}

        {upcoming.length > 0 && (
          <Panel eyebrow="Tonight's slate" title="Next up"
            action={<Link className="bx-btn bx-btn--ghost" to={withSport('/games')}>See full schedule <ArrowRight size={14} /></Link>}>
            <div className="bx-rail-wrap"><div className="bx-rail bx-scroll">
              {upcoming.slice(0, 10).map((game) => (
                <Link key={game.key} className="bx-rail__item" to={withSport(game.analysisHref, game.league)}>
                  <header>
                    <LeagueTag league={game.league} />
                    <time>{clockOf(game.startsAt, zone)}</time>
                  </header>
                  <b>{game.away.abbreviation} <span className="bx-dim">@</span> {game.home.abbreviation}</b>
                  <SplitBar away={game.awayWin} home={game.homeWin} tie={game.tie}
                    awayLabel={game.away.abbreviation} homeLabel={game.home.abbreviation}
                    awayColor={game.away.color ?? 'var(--bx-nfl)'} homeColor={game.home.color ?? 'var(--bx-accent)'} />
                </Link>
              ))}
            </div></div>
          </Panel>
        )}

        <div className="bx-home__grid">
          <Panel eyebrow="Model vs market" title="Where we disagree with the price"
            action={<Link className="bx-btn bx-btn--ghost" to={withSport('/markets')}>Open markets <ArrowRight size={16} /></Link>}>
            <ModelVsMarketChart
              rows={disagreements.map((game) => ({
                key: game.key,
                label: `${game.away.abbreviation} @ ${game.home.abbreviation}`,
                model: game.homeWin,
                market: game.marketHome,
                color: game.home.color ?? undefined,
                href: withSport(game.analysisHref, game.league),
              }))} />
            <p className="bx-muted">
              Each row is one game&apos;s home-win probability: the hollow dot is the market with
              the vig removed, the solid dot is the model, and the bar between them is the
              disagreement. A wide bar is a question worth opening, not a selection.
            </p>
          </Panel>

          <Panel eyebrow="Decisions" title="What the policy says">
            {sections.length === 0
              ? <p className="bx-empty">No sport is reporting a slate right now.</p>
              : <div style={{ display: 'grid', gap: 12 }}>
                {sections.map((section) => (
                  <div key={section.league_code} style={{ display: 'grid', gap: 6 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                      <LeagueTag league={section.league_code} />
                      <span className="bx-muted">{section.scheduled_event_count} scheduled</span>
                      <span style={{ marginLeft: 'auto' }} className={`bx-chip ${(section.final_qualified_count ?? 0) > 0 ? 'bx-chip--good' : ''}`}>
                        {section.can_create_official_positions
                          ? `${section.final_qualified_count ?? 0} qualified`
                          : 'Research only'}
                      </span>
                    </div>
                    <p className="bx-dim">
                      {section.valid_forecast_count} forecast{section.valid_forecast_count === 1 ? '' : 's'} ·{' '}
                      {section.awaiting_final_evidence_count} awaiting final evidence ·{' '}
                      {section.open_position_count} open position{section.open_position_count === 1 ? '' : 's'}
                    </p>
                  </div>
                ))}
                <Link className="bx-btn" to={withSport('/decisions')}>Open Daily Decisions <ArrowRight size={14} /></Link>
              </div>}
          </Panel>

        </div>

        {board?.bankroll
          ? <BankrollSnapshot bankroll={board.bankroll} withSport={withSport} />
          : <Panel eyebrow="$1,000 experiment" title="Virtual bankroll">
            <p className="bx-empty">The experiment ledger is unavailable.</p>
          </Panel>}

        {/* A class, not an inline style: the inline two-column template beat the
            stylesheet's single-column rule at every width, and at 375px the Labs
            panel ran 29px past the screen. */}
        <div className="bx-home__grid bx-home__grid--pair">
          <Panel eyebrow="Spiciest market tonight" title="RFF Index — Ripe For Fuckery"
            action={<Link className="bx-btn bx-btn--ghost" to={withSport('/markets?view=RFF')}>
              Open the RFF Lab <ArrowRight size={14} /></Link>}>
            <SpiciestMarket board={board?.rff ?? null} withSport={withSport} />
          </Panel>

          <Panel eyebrow="Explore" title="Interactive Labs">
            <div style={{ display: 'grid', gap: 10 }}>
              <Link className="bx-lab-link" to={withSport('/labs', 'MLB')}>
                <strong><span><Beaker size={15} aria-hidden="true" /> MLB Diamond Simulator</span><ArrowRight size={15} /></strong>
                <span className="bx-dim">Replay a simulated game pitch by pitch and change the assumptions behind it.</span>
              </Link>
              <Link className="bx-lab-link" to={withSport('/labs', 'NFL')}>
                <strong><span><Beaker size={15} aria-hidden="true" /> NFL Simulation Lab</span><ArrowRight size={15} /></strong>
                <span className="bx-dim">Play-level simulations, drive outcomes and the whole-population density behind them.</span>
              </Link>
              <Link className="bx-lab-link" to={withSport('/performance')}>
                <strong><span><LineChart size={15} aria-hidden="true" /> Model performance</span><ArrowRight size={15} /></strong>
                <span className="bx-dim">Which model is retained, what challenged it, and whether the difference was real.</span>
              </Link>
            </div>
          </Panel>

          <Panel eyebrow="System" title="Data freshness">
            {!freshness
              ? <p className="bx-empty">Freshness is unavailable.</p>
              : <div className="bx-feature__stats">
                <Stat label="Forecasts" value={agoOf(freshness.latest_forecast_at) ?? 'not recorded'} />
                <Stat label="Market evidence" value={agoOf(freshness.latest_market_at) ?? 'not recorded'} />
                <Stat label="Scheduler" value={freshness.scheduler_running ? 'running' : 'stopped'}
                  tone={freshness.scheduler_running ? 'good' : 'bad'} />
                <Stat label="Odds refresh" value={freshness.automatic_odds_refresh_running ? 'running' : 'paused'}
                  tone={freshness.automatic_odds_refresh_running ? 'good' : 'warn'} />
              </div>}
            {freshness?.odds_refresh_detail && (
              <details className="bx-details" style={{ marginTop: 14 }}>
                <summary>Why odds refresh is paused</summary>
                <div className="bx-details__body"><p>{freshness.odds_refresh_detail}</p></div>
              </details>
            )}
            {board?.warnings?.length ? (
              <p className="bx-dim" style={{ marginTop: 10 }} role="status">{board.warnings.join(' · ')}</p>
            ) : null}
          </Panel>
        </div>
      </div>
    </div>
  )
}

/**
 * The featured matchup — the one dark panel on a light page.
 *
 * Everything else on Home is a white card, so this is where the eye lands, and
 * it earns that by being the only place showing a game at full size: crests at
 * 76px, club names at 28px, the projected score as the headline figure, and
 * the win split as a bar you can read across a room. It is the shot that has
 * to survive being screen-shared at 50% on somebody's second monitor.
 *
 * Team names come from the canonical identity table, not from `teams.name` --
 * that column still says "St. Louis Rams" for a 2026 fixture, and one wrong
 * name costs more trust than every correct number on the page earns.
 */
function FeatureCard({
  game, zone, withSport,
}: { game: SlateGame; zone?: string; withSport: (href: string, sport?: 'MLB' | 'NFL') => string }) {
  const leadHome = (game.homeWin ?? 0) >= (game.awayWin ?? 0)
  const home = teamIdentity(game.league, game.home.abbreviation)
  const away = teamIdentity(game.league, game.away.abbreviation)
  const leader = leadHome ? home : away
  const leadProbability = leadHome ? game.homeWin : game.awayWin
  const unit = game.scoreUnit === 'runs' ? 'Projected runs' : 'Projected points'
  return (
    <article className="bx-feature bx-dark-panel" aria-label={`${away.name} at ${home.name}`}>
      <div className="bx-feature__top">
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>
          <LeagueTag league={game.league} />
          <span className="bx-muted" style={{ fontSize: 15 }}>
            {dayOf(game.startsAt, zone)} · {clockOf(game.startsAt, zone)}
          </span>
        </span>
        <Link className="bx-btn" to={withSport(game.analysisHref, game.league)}>
          View game <ArrowRight size={16} />
        </Link>
      </div>

      <div className="bx-feature__teams">
        <div className="bx-feature__team bx-feature__team--away">
          <TeamLogo league={game.league} abbreviation={game.away.abbreviation} size={76} />
          <strong>{away.name}</strong>
          <span className="bx-feature__score bx-num">{decimals(game.expAway, 1)}</span>
        </div>
        <span className="bx-feature__vs">AT</span>
        <div className="bx-feature__team bx-feature__team--home">
          <TeamLogo league={game.league} abbreviation={game.home.abbreviation} size={76} />
          <strong>{home.name}</strong>
          <span className="bx-feature__score bx-num">{decimals(game.expHome, 1)}</span>
        </div>
      </div>

      <div className="bx-feature__body">
        <SplitBar away={game.awayWin} home={game.homeWin} tie={game.tie}
          awayLabel={away.abbreviation} homeLabel={home.abbreviation}
          awayColor={onDarkColor(away)} homeColor={onDarkColor(home)} />
        <ProbabilityArc value={leadProbability} label={leader.abbreviation}
          sublabel="model win probability" size={132} color={onDarkColor(leader)} />
      </div>

      <div className="bx-feature__stats">
        <Stat label={unit} value={`${decimals(game.expAway, 1)} – ${decimals(game.expHome, 1)}`} />
        <Stat label="Market home" value={pct(game.marketHome, 1)} />
        <Stat label="Model − market"
          value={game.edgePoints == null ? 'no market' : `${signedPoints(game.edgePoints, 1)} pp`}
          tone={game.edgePoints == null ? undefined : Math.abs(game.edgePoints) >= 2 ? 'good' : 'warn'} />
        <Stat label="Best price"
          value={game.bestHomePrice == null && game.bestAwayPrice == null
            ? 'none' : `${american(game.bestAwayPrice)} / ${american(game.bestHomePrice)}`} />
      </div>

      <div className="bx-feature__foot">
        {game.researchOnly
          ? <span className="bx-chip">Research shadow · no position</span>
          : <span className={`bx-chip ${game.decisionState === 'QUALIFIED BET' ? 'bx-chip--good' : ''}`}>
            {game.decisionState ?? 'No decision recorded'}
          </span>}
        {game.marketHome == null && <span className="bx-chip bx-chip--warn">No comparable price</span>}
        <span className="bx-muted" style={{ marginLeft: 'auto' }}>
          Evidence {agoOf(game.evidenceAt) ?? 'not recorded'}
        </span>
        {game.simulationHref && (
          <ActionLink to={withSport(game.simulationHref, game.league)} primary>
            <Beaker size={16} aria-hidden="true" /> Simulation
          </ActionLink>
        )}
      </div>
    </article>
  )
}

/**
 * The $1,000 Experiment, previewed at the size it is worth.
 *
 * It used to be a third-width utility card with a $30px balance, an
 * unexplained green zigzag and a stat labelled "ROI +10.62%" -- a number that
 * divided realized profit by every dollar staked INCLUDING an open position,
 * sitting next to a bankroll that had moved 0.35%. A reader could only
 * conclude the bankroll was up eleven percent. It was not.
 *
 * Now it is the full content width: the balance as the hero, both returns
 * named by their denominator, and a real miniature equity chart that starts
 * at the labelled $1,000 line and steps once per settlement. The most recent
 * settlement and the open position are shown as themselves rather than
 * averaged into anything.
 */
function BankrollSnapshot({
  bankroll, withSport,
}: { bankroll: BankrollData; withSport: (href: string, sport?: 'MLB' | 'NFL') => string }) {
  const facts = bankrollFacts(bankroll)
  const points = equitySeries(bankroll.equity_curve, facts.startingBankroll)
  const settled = settledPositions(bankroll.positions)
  const latest = settled[settled.length - 1] ?? null
  const open = openPositions(bankroll.positions)[0] ?? null

  return (
    <section className="exp exp-preview" aria-label="$1,000 Experiment">
      <div className="exp-preview__head">
        <div>
          <span className="bx-eyebrow">$1,000 Experiment</span>
          <h2>What happened when the model risked a hypothetical $1,000</h2>
          <p className="bx-muted">
            {bankroll.experiment_name ?? 'Practical pregame policy'} · every position recorded
            before first pitch, settled once, never edited. Virtual research — no real wagers.
          </p>
        </div>
        <Link className="bx-btn bx-btn--primary" to={withSport('/experiment')}>
          Open the full experiment <ArrowRight size={17} />
        </Link>
      </div>

      <BankrollHero facts={facts} compact />

      <div className="exp-preview__body">
        <div className="exp-preview__chart">
          <EquityDrawdownChart points={points} starting={facts.startingBankroll}
            positions={bankroll.positions ?? []} compact height={210} />
          <p className="exp-muted">
            Each step is one settlement. The dashed line is the {money(facts.startingBankroll, 0)} it
            opened with.
          </p>
        </div>

        <div className="exp-preview__latest">
          {latest && (
            <article className={`exp-mini exp-mini--${latest.result.toLowerCase()}`}>
              <small>Most recent settlement</small>
              <b>{latest.matchup ?? latest.selection}</b>
              <span>
                {latest.selection}
                {latest.price != null ? ` · ${latest.price > 0 ? '+' : ''}${latest.price}` : ''}
                {latest.sportsbook ? ` · ${latest.sportsbook}` : ''}
              </span>
              <strong className={latest.profit_loss >= 0 ? 'is-up' : 'is-down'}>
                {signedMoney(latest.profit_loss)}
              </strong>
            </article>
          )}
          {open
            ? <article className="exp-mini exp-mini--open">
              <small>Open position</small>
              <b>{open.matchup ?? open.selection}</b>
              <span>
                {open.selection}
                {open.price != null ? ` · ${open.price > 0 ? '+' : ''}${open.price}` : ''}
              </span>
              <strong>{money(open.stake)} at risk</strong>
            </article>
            : <article className="exp-mini exp-mini--open">
              <small>Open positions</small>
              <b>None</b>
              <span>Every position has settled.</span>
            </article>}
        </div>
      </div>

      <p className="exp-preview__caution">
        <AlertTriangle size={17} aria-hidden="true" />
        <span>
          {facts.settledCount} settled positions is far too few to show a betting edge. These are
          bookkeeping facts about a virtual ledger, not evidence that the model beats the market.
        </span>
      </p>
    </section>
  )
}

function SpiciestMarket({
  board, withSport,
}: { board: RffBoard | null; withSport: (href: string, sport?: 'MLB' | 'NFL') => string }) {
  const game = board?.games?.[0] ?? null
  if (!game) {
    return (
      <p className="bx-dim">
        {board
          ? 'No game on the board has enough persisted evidence for any RFF component to score.'
          : 'The RFF index is unavailable.'}
      </p>
    )
  }
  const away = game.event.away?.abbreviation
  const home = game.event.home?.abbreviation
  return (
    <div className="bx-home__rff">
      <RffGauge game={game} size={124} />
      <div style={{ display: 'grid', gap: 6, minWidth: 0 }}>
        <Link to={withSport(`/markets?view=RFF&rff=${game.game_id}`)}
          style={{ color: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <Flame size={14} aria-hidden="true" style={{ color: bandTone(game.band.key) }} />
          <strong style={{ fontSize: 16 }}>{away} @ {home}</strong>
        </Link>
        <p className="bx-muted">
          Provisional RFF {game.score?.toFixed(0)} · {pct(game.evidence_coverage, 0)} evidence
          coverage. {game.share_card.triggers.length > 0
            ? `Driven by ${game.share_card.triggers.map((t) => t.label.toLowerCase()).join(', ')}.`
            : 'No component is scoring high.'}
        </p>
        <p className="bx-dim">{game.share_card.disclaimer}</p>
      </div>
    </div>
  )
}
