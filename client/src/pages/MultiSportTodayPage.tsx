/**
 * The public home of AI Sports Predictor — the All Sports Daily Command Center.
 *
 * The page answers six questions in order, and its layout is that order:
 *
 *   1. What is this product, and what is it not?      -> command header
 *   2. What is the answer today, in one sentence?     -> today's answer
 *   3. Which leagues does it actually cover?          -> league strip
 *   4. What are the individual decisions?             -> decision board
 *   5. Where else can I go?                           -> explore
 *   6. Why should I believe any of it?                -> verified performance
 *
 * Every number is derived from persisted authority: the read model for the
 * slate, `services/dailyDecision` for grouping, and the backend sport registry
 * for what a league is allowed to claim. This page renders that derivation; it
 * never decides, and it never fabricates a league, forecast, price or result.
 *
 * A day with no qualified position is a valid, honestly-stated outcome.
 */
import {
  Activity, BarChart3, CalendarDays, Clock3, FlaskConical, LineChart,
  RefreshCw, ShieldCheck, Sparkles, Store, Users, Beaker,
} from 'lucide-react'
import { useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { DecisionEventCard } from '../components/DecisionEventCard'
import { DataModeBanner, DataStatePanel } from '../components/ui'
import { useApiResource } from '../hooks/useApiResource'
import {
  navigableRegistrySports,
  sportNavigationHref,
  useSportRegistry,
} from '../hooks/useSportRegistry'
import { endpoints } from '../services/api'
import {
  distinctGameCount,
  GROUP_ORDER,
  groupSelections,
  slateMessage,
  slateNextCheckpoint,
  type DecisionGroup,
  type GroupedGame,
} from '../services/dailyDecision'
import { formatDate, formatTimestamp } from '../services/format'
import {
  labIsAvailable,
  resolveAllSports,
  SPORT_ORDER,
  type ResolvedSport,
} from '../services/sportExperience'
import type {
  LeagueCode,
  MultiSportDataFreshness,
  SportAvailabilityState,
  SportSection,
  TodayAllSportsReadModel,
} from '../types/multisport'

const VALUE_STATEMENT =
  'Model forecasts, market prices and governed decisions—updated as game evidence develops.'
const RESEARCH_NOTE = 'Research and simulation only · No real-money wagering'

const GROUP_META: Record<DecisionGroup, { title: string; blurb: string; empty: string; collapsed: string }> = {
  READY: {
    title: 'Ready',
    collapsed: 'ready decisions',
    blurb: 'A final qualified decision or a recorded virtual position exists.',
    empty: 'No game has produced a final qualified decision today. A zero-position day is valid.',
  },
  WATCHING: {
    title: 'Watching',
    collapsed: 'watching decisions',
    blurb: 'Forecast or decision window still open, or required evidence is incomplete.',
    empty: 'No game is currently under review.',
  },
  PASSED: {
    title: 'Passed',
    collapsed: 'passed decisions',
    blurb: 'Evaluated with sufficient evidence, but did not qualify under the policy.',
    empty: 'No game has been evaluated and passed today.',
  },
  // Deliberately not "Final and settled": this group also holds in-progress,
  // postponed and canceled games, which are neither final games nor settled
  // wagers. The umbrella states only what every member shares — no pregame
  // decision is possible any more — and each card names its own situation.
  CLOSED: {
    title: 'Closed for decisions',
    collapsed: 'closed decisions',
    blurb: 'The pregame window has closed: in progress, final, postponed, canceled, or settled.',
    empty: 'No game has closed for decisions yet today.',
  },
}

export function MultiSportTodayPage() {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.today)
  const { registry, state: registryState } = useSportRegistry()
  const navigable = navigableRegistrySports(registry)
  const mlbDeskHref = sportNavigationHref(navigable.find((sport) => sport.league_code === 'MLB') ?? null)

  // One clock for the whole render keeps grouping and countdowns consistent.
  const now = useMemo(() => Date.now(), [data])

  const selections = useMemo(
    () => (data ? data.sport_sections.flatMap((section) => section.selections) : []),
    [data],
  )
  const grouped = useMemo(() => groupSelections(selections, now), [selections, now])

  if (!data || state === 'unavailable' || state === 'loading') {
    return (
      <div className="page command-center">
        <CommandHeader />
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        <DataStatePanel
          state={state}
          emptyTitle="No governed sports evidence is available"
          emptyDetail="No current sport has a persisted slate or decision. Nothing has been fabricated."
          unavailableTitle="The daily command center is unavailable"
          unavailableDetail="The persisted-read API could not be reached. No historical or illustrative result was substituted."
          action={mlbDeskHref ? <Link className="secondary-button" to={mlbDeskHref}>Open the MLB desk</Link> : undefined}
        />
      </div>
    )
  }

  if (registryState !== 'ready' || registry === null) {
    return (
      <div className="page command-center">
        <CommandHeader />
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        <section className="multisport-roadmap" aria-label="Sport registry status">
          <p className="multisport-empty" role="status">
            {registryState === 'loading'
              ? 'Loading the authoritative sport registry. No sport is presented as active until it is verified.'
              : 'The authoritative sport registry is unavailable. The all-sports command center is closed rather than inventing active sports from an unverified payload.'}
          </p>
        </section>
      </div>
    )
  }

  const sports = resolveAllSports(registry.sports)
  const sportByLeague = new Map<string, ResolvedSport>(sports.map((sport) => [sport.leagueCode, sport]))
  const leagueOrder = registry.sports.map((sport) => sport.league_code)
  const navigationByLeague = new Map(navigable.map((sport) => [sport.league_code, sportNavigationHref(sport)]))

  // A game with two active policies produces two governed decisions that may
  // legitimately disagree, so the groups count DECISIONS. The event total is
  // shown separately and never inside the same row, because four tiles that
  // look like a partition of 11 games but sum to 19 is simply wrong.
  const gameCount = new Set(selections.map((item) => item.event_id)).size
  const counts = {
    READY: grouped.READY.length,
    WATCHING: grouped.WATCHING.length,
    PASSED: grouped.PASSED.length,
    CLOSED: grouped.CLOSED.length,
  }
  const decisionCount = counts.READY + counts.WATCHING + counts.PASSED + counts.CLOSED
  const readyFunded = grouped.READY.filter((item) => item.decision.reasonCode === 'READY_FUNDED').length
  // Watching decisions that hold no usable sportsbook price. They are open,
  // but nothing is reviewing them: they cannot be priced at all.
  const marketBlocked = grouped.WATCHING.filter((item) =>
    item.decision.reasonCode === 'WATCH_NO_MARKET_PRICE'
    || item.decision.reasonCode === 'WATCH_MARKET_PRICE_STALE',
  ).length
  const checkpoint = slateNextCheckpoint(grouped.WATCHING)
  const scopes = experimentScopes(selections)

  return (
    <div className="page command-center">
      <CommandHeader
        operationalDate={data.operational_date}
        timezone={data.display_timezone}
        freshness={relativeAge(data.data_freshness.latest_forecast_at, now)}
        sports={sports}
        navigationByLeague={navigationByLeague}
        onRefresh={refresh}
        refreshing={loading}
      />

      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <section className="today-answer" aria-labelledby="today-answer-heading">
        <h2 id="today-answer-heading" className="sr-only">Today’s decision summary</h2>
        <p className="today-answer__message">{slateMessage(counts, readyFunded, marketBlocked)}</p>
        <p className="daily-summary__scope today-answer__scope">
          <strong>{gameCount}</strong> {gameCount === 1 ? 'game' : 'games'} today
          {' · '}
          <strong>{decisionCount}</strong> governed {decisionCount === 1 ? 'decision' : 'decisions'}
        </p>
        <div
          className="daily-summary__counts today-answer__counts"
          role="group"
          aria-label={`Breakdown of ${decisionCount} governed decisions`}
        >
          {GROUP_ORDER.map((group) => (
            <SummaryCount key={group} label={GROUP_META[group].title} value={counts[group]} tone={group} />
          ))}
        </div>
        <dl className="today-answer__meta">
          <div>
            <dt>Next checkpoint</dt>
            <dd>
              {checkpoint
                ? <>{formatTimestamp(checkpoint.at)} <small>· {checkpoint.label.toLowerCase()} · {checkpoint.eventLabel}</small></>
                : 'None scheduled'}
            </dd>
          </div>
          <div>
            <dt>Last complete refresh</dt>
            <dd>{relativeAge(data.overall_summary.latest_complete_refresh, now)}</dd>
          </div>
          <div>
            <dt>Funded experiment</dt>
            <dd>
              {scopes.funded ?? (
                <>None active <small>· official decisions still governed by {GOVERNING_POLICY}</small></>
              )}
              {scopes.comparisons.length > 0 && (
                <small>· strict comparison: {scopes.comparisons.join(' · ')}</small>
              )}
            </dd>
          </div>
          <div>
            <dt>Automatic odds refresh</dt>
            <dd>
              {data.data_freshness.automatic_odds_refresh_running
                ? 'Running'
                : <>Not running <small>· {oddsRefreshLabel(data.data_freshness.odds_refresh_state)}</small></>}
            </dd>
          </div>
          <div>
            <dt>Virtual exposure</dt>
            <dd>${data.overall_summary.total_virtual_stake.toFixed(2)} <small>· virtual only</small></dd>
          </div>
        </dl>
        {decisionCount > gameCount && (
          <p className="daily-summary__footnote">
            These four counts break down {decisionCount} governed decisions, not{' '}
            {gameCount} {gameCount === 1 ? 'game' : 'games'}: some games carry more than one
            active policy.
          </p>
        )}
      </section>

      <LeagueStrip
        sports={sports}
        sections={data.sport_sections}
        grouped={grouped}
        navigationByLeague={navigationByLeague}
      />

      <section className="decision-board" aria-labelledby="decision-board-heading">
        <div className="section-heading">
          <div>
            <span className="eyebrow">TODAY’S DECISIONS</span>
            <h2 id="decision-board-heading">Decision board</h2>
          </div>
          <p className="decision-board__note">
            One card per governed decision. Open groups first; resolved groups collapse.
          </p>
        </div>
        {GROUP_ORDER.map((group) => (
          <DecisionGroupSection
            key={group}
            group={group}
            items={grouped[group]}
            leagueOrder={leagueOrder}
            sections={data.sport_sections}
            sportByLeague={sportByLeague}
            now={now}
          />
        ))}
      </section>

      <ExploreSection sports={sports} navigationByLeague={navigationByLeague} />

      <TrustSection />

      <footer className="multisport-evidence-footer">
        <span><CalendarDays size={15} /> Generated {formatTimestamp(data.generated_at)}</span>
        <span><Clock3 size={15} /> Latest forecast {data.data_freshness.latest_forecast_at ? formatTimestamp(data.data_freshness.latest_forecast_at) : 'not recorded'}</span>
        <span><Clock3 size={15} /> Latest market {data.data_freshness.latest_market_at ? formatTimestamp(data.data_freshness.latest_market_at) : 'not recorded'}</span>
        <span><ShieldCheck size={15} /> Provider calls made by this read: {data.data_freshness.provider_calls_made}</span>
      </footer>
    </div>
  )
}

/**
 * The product's front door.
 *
 * It states what AISP is in plain language before any governance vocabulary,
 * and carries the one disclosure that must never be missed: this is research
 * and simulation, with no real-money wagering.
 */
function CommandHeader({
  operationalDate, timezone, freshness, sports, navigationByLeague, onRefresh, refreshing,
}: {
  operationalDate?: string
  timezone?: string
  freshness?: string
  sports?: ResolvedSport[]
  navigationByLeague?: Map<LeagueCode, string | null>
  onRefresh?: () => void
  refreshing?: boolean
} = {}) {
  return (
    <header className="command-header">
      <div className="command-header__main">
        <p className="command-header__eyebrow">ALL SPORTS · DAILY COMMAND CENTER</p>
        <h1 className="command-header__title">AI Sports Predictor</h1>
        <p className="command-header__value">{VALUE_STATEMENT}</p>
        <div className="command-header__meta">
          {operationalDate && (
            <span className="command-header__date">
              <CalendarDays size={14} aria-hidden="true" />
              {formatDate(operationalDate)}{timezone ? ` · ${timezone}` : ''}
            </span>
          )}
          {freshness && (
            <span className="command-header__fresh">
              <Activity size={14} aria-hidden="true" /> Forecasts refreshed {freshness.toLowerCase()}
            </span>
          )}
          <span className="command-header__disclosure">{RESEARCH_NOTE}</span>
        </div>
      </div>

      <div className="command-header__side">
        <div className="command-header__buttons">
          {/* The simulators are a destination in their own right, so the way in
              sits in the header rather than inside one decision card. */}
          <Link className="command-header__labs" to="/labs">
            <Beaker size={14} aria-hidden="true" /> Open Interactive Labs
          </Link>
          {onRefresh && (
            <button className="command-header__refresh" type="button" onClick={onRefresh} disabled={refreshing}>
              <RefreshCw size={14} className={refreshing ? 'spin' : ''} aria-hidden="true" /> Refresh persisted view
            </button>
          )}
        </div>
        {sports && (
          <nav className="league-nav" aria-label="Sports and leagues">
            {sports.map((sport) => {
              const href = navigationByLeague?.get(sport.leagueCode as LeagueCode) ?? null
              const label = `${sport.shortName} · ${sport.sportName}`
              return href && sport.navigable
                ? (
                  <Link key={sport.leagueCode} className="league-nav__chip league-nav__chip--live" to={href}>
                    <span aria-hidden="true">{sport.icon}</span> {sport.shortName}
                  </Link>
                )
                : (
                  <span
                    key={sport.leagueCode}
                    className="league-nav__chip league-nav__chip--planned"
                    aria-disabled="true"
                    title={`${label} — ${sport.availabilityLabel}`}
                  >
                    <span aria-hidden="true">{sport.icon}</span> {sport.shortName}
                    <em>{sport.availabilityLabel}</em>
                  </span>
                )
            })}
          </nav>
        )}
      </div>
    </header>
  )
}

function SummaryCount({ label, value, tone }: { label: string; value: number; tone?: DecisionGroup }) {
  return (
    <div className={`summary-count${tone ? ` summary-count--${tone.toLowerCase()}` : ''}`}>
      <span className="summary-count__value">{value}</span>
      <span className="summary-count__label">{label}</span>
    </div>
  )
}

/**
 * Every league AISP recognises, with its parent sport and its truthful status.
 *
 * Planned leagues are shown deliberately rather than hidden or rendered as a
 * broken empty state, and they stay non-interactive: a league can only become a
 * link when the backend registry says it is navigable.
 */
function LeagueStrip({ sports, sections, grouped, navigationByLeague }: {
  sports: ResolvedSport[]
  sections: SportSection[]
  grouped: Record<DecisionGroup, GroupedGame[]>
  navigationByLeague: Map<LeagueCode, string | null>
}) {
  const allDecisions = GROUP_ORDER.flatMap((group) => grouped[group])
  return (
    <section className="league-strip" aria-labelledby="league-strip-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">COVERAGE</span>
          <h2 id="league-strip-heading">Sports and leagues</h2>
        </div>
        <p className="league-strip__note">
          A league is active only when the backend sport registry says so.
        </p>
      </div>
      <div className="league-strip__grid">
        {sports.map((sport) => (
          <LeagueTile
            key={sport.leagueCode}
            sport={sport}
            section={sections.find((item) => item.league_code === sport.leagueCode) ?? null}
            decisionCount={allDecisions.filter((item) => item.selection.league_code === sport.leagueCode).length}
            href={navigationByLeague.get(sport.leagueCode as LeagueCode) ?? null}
          />
        ))}
      </div>
    </section>
  )
}

function LeagueTile({ sport, section, decisionCount, href }: {
  sport: ResolvedSport
  section: SportSection | null
  decisionCount: number
  href: string | null
}) {
  const tone = sport.availability.toLowerCase().replace('_', '-')
  const eventTerm = section?.scheduled_event_count === 1
    ? sport.terminology.event
    : sport.terminology.eventPlural
  return (
    <article
      className={`league-tile league-tile--${tone}`}
      style={{ ['--sport-accent' as string]: sport.accent.base }}
      aria-disabled={sport.navigable ? undefined : 'true'}
    >
      <header className="league-tile__head">
        <span className="league-tile__code">
          <span aria-hidden="true">{sport.icon}</span> {sport.shortName}
        </span>
        <span className={`league-tile__status league-tile__status--${tone}`}>{sport.availabilityLabel}</span>
      </header>
      <h3 className="league-tile__name">{section?.display_name ?? sport.displayName}</h3>
      <p className="league-tile__sport">{sport.sportName}</p>

      {section ? (
        <p className="league-tile__counts">
          <strong>{section.scheduled_event_count}</strong> scheduled {eventTerm}
          {' · '}
          <strong>{decisionCount}</strong> governed {decisionCount === 1 ? 'decision' : 'decisions'}
        </p>
      ) : (
        <p className="league-tile__counts league-tile__counts--none">
          No slate, forecast or decision is recorded — this league is not in the sport registry.
        </p>
      )}

      {/* One short line here; the full explanation stays available on hover
          and in the card's own status, so the strip does not become an essay. */}
      <p className="league-tile__blurb" title={sport.statusExplanation}>{sport.availabilityBlurb}</p>

      {section && (
        <dl className="league-tile__metrics">
          <div><dt>Official positions</dt><dd>{section.official_position_count ?? 0}</dd></div>
          <div><dt>Final qualified decisions</dt><dd>{section.final_qualified_decision_count ?? section.final_qualified_count ?? 0}</dd></div>
        </dl>
      )}

      {section?.degraded && (
        <p className="league-tile__warning">{section.degraded_reason ?? 'Persisted evidence is degraded.'}</p>
      )}

      <div className="league-tile__actions">
        {href && sport.navigable
          ? <Link className="league-tile__link" to={href}>Open {sport.shortName}</Link>
          : <span className="league-tile__link league-tile__link--disabled" aria-disabled="true">Not available yet</span>}
        {/* Named for what it opens, and it lands on the picker rather than on
            some arbitrary game. */}
        {labIsAvailable(sport) && sport.lab && (
          <Link className="league-tile__lab" to={`/labs?league=${sport.leagueCode}`}>
            Open {sport.shortName} {sport.lab.name.split('— ')[1] ?? 'Interactive Lab'}
          </Link>
        )}
      </div>
    </article>
  )
}

function DecisionGroupSection({ group, items, leagueOrder, sections, sportByLeague, now }: {
  group: DecisionGroup
  items: GroupedGame[]
  leagueOrder: LeagueCode[]
  sections: SportSection[]
  sportByLeague: Map<string, ResolvedSport>
  now: number
}) {
  const meta = GROUP_META[group]
  const events = distinctGameCount(items)
  const headingId = `group-${group.toLowerCase()}`
  const orderedLeagues = [...leagueOrder]
  items.forEach(({ selection }) => {
    if (!orderedLeagues.includes(selection.league_code)) orderedLeagues.push(selection.league_code)
  })
  const byLeague = orderedLeagues
    .map((league) => ({ league, entries: items.filter((item) => item.selection.league_code === league) }))
    .filter((entry) => entry.entries.length > 0)

  // Resolved groups can be long and are rarely the reason someone opened the
  // page. Collapse them past a handful so they still answer "how many passed?"
  // at a glance without pushing the open decisions off the screen. <details>
  // keeps the content in the DOM and keyboard reachable.
  const collapsible = (group === 'PASSED' || group === 'CLOSED') && items.length > 6

  const body = byLeague.length === 0
    ? <p className="multisport-empty" role="status">{meta.empty}</p>
    : byLeague.map(({ league, entries }) => {
        const definition = sections.find((section) => section.league_code === league)
        const sport = sportByLeague.get(league)
        const leagueEvents = distinctGameCount(entries)
        const eventWord = sport
          ? (leagueEvents === 1 ? sport.terminology.event : sport.terminology.eventPlural)
          : (leagueEvents === 1 ? 'game' : 'games')
        return (
          <div className="decision-group__league" key={league}>
            <h3>
              {definition?.display_name ?? league}
              <span>
                {entries.length} {entries.length === 1 ? 'decision' : 'decisions'} ·{' '}
                {leagueEvents} {eventWord} ·{' '}
                ranked only within {league}
              </span>
            </h3>
            <div className="decision-card-grid">
              {entries.map((entry) => (
                sport
                  ? (
                    <DecisionEventCard
                      key={`${entry.selection.event_id}:${entry.selection.experiment_id ?? 'none'}:${entry.selection.policy_version ?? 'none'}`}
                      item={entry}
                      sport={sport}
                      now={now}
                    />
                  )
                  : null
              ))}
            </div>
          </div>
        )
      })

  return (
    <section className={`decision-group decision-group--${group.toLowerCase()}`} aria-labelledby={headingId}>
      <div className="decision-group__heading">
        <h2 id={headingId}>
          {meta.title} <span className="decision-group__count">{items.length}</span>
        </h2>
        <p>
          {meta.blurb}
          {items.length > 0 && (
            <span className="decision-group__unit">
              {' '}{items.length} {items.length === 1 ? 'decision' : 'decisions'} across{' '}
              {events} {events === 1 ? 'event' : 'events'}.
            </span>
          )}
        </p>
      </div>
      {collapsible
        ? (
          <details className="decision-group__collapse">
            <summary>Show all {items.length} {meta.collapsed}</summary>
            {body}
          </details>
        )
        : body}
    </section>
  )
}

interface Destination {
  key: string
  name: string
  blurb: string
  href: string | null
  icon: ReactNode
}

/**
 * The rest of the platform, stated at its real availability.
 *
 * A destination that does not work yet is never given a route, a control or an
 * output — it is listed as planned and left inert.
 */
function ExploreSection({ sports, navigationByLeague }: {
  sports: ResolvedSport[]
  navigationByLeague: Map<LeagueCode, string | null>
}) {
  const mlbHref = navigationByLeague.get('MLB') ?? null
  const destinations: Destination[] = [
    {
      key: 'forecasts',
      name: 'Game & event forecasts',
      blurb: 'Every scheduled event with a governed model forecast and its supporting evidence.',
      href: mlbHref,
      icon: <LineChart size={17} aria-hidden="true" />,
    },
    {
      key: 'markets',
      name: 'Market board',
      blurb: 'Observed sportsbook prices beside the model, with no price implied when none was recorded.',
      href: '/markets',
      icon: <Store size={17} aria-hidden="true" />,
    },
    {
      key: 'pulse',
      name: 'Crowd & market pulse',
      blurb: 'Kalshi and Polymarket quotes as observational comparisons — never an AISP position.',
      href: '/market-pulse',
      icon: <Users size={17} aria-hidden="true" />,
    },
    {
      key: 'performance',
      name: 'Model performance',
      blurb: 'Calibration and accuracy of the forecasts themselves, tracked separately from policy.',
      href: '/performance',
      icon: <BarChart3 size={17} aria-hidden="true" />,
    },
    {
      key: 'experiment',
      name: '$1,000 Experiment',
      blurb: 'A virtual research bankroll: every position recorded before the event, then settled.',
      href: '/experiment',
      icon: <Sparkles size={17} aria-hidden="true" />,
    },
    {
      key: 'labs',
      name: 'Interactive labs',
      blurb: 'Open the simulations behind a forecast: Gen 1 outcome distributions and V18 sequential replays.',
      href: '/labs',
      icon: <Beaker size={17} aria-hidden="true" />,
    },
    {
      key: 'methodology',
      name: 'Methodology',
      blurb: 'How forecasts are produced, how decisions qualify, and what the platform will not claim.',
      href: '/methodology',
      icon: <ShieldCheck size={17} aria-hidden="true" />,
    },
  ]

  return (
    <section className="explore" aria-labelledby="explore-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">EXPLORE THE PLATFORM</span>
          <h2 id="explore-heading">Beyond today’s slate</h2>
        </div>
      </div>

      <div className="explore__grid">
        {destinations.map((destination) => (
          destination.href
            ? (
              <Link key={destination.key} className="explore__card" to={destination.href}>
                <span className="explore__icon">{destination.icon}</span>
                <strong>{destination.name}</strong>
                <span className="explore__blurb">{destination.blurb}</span>
              </Link>
            )
            : (
              <span key={destination.key} className="explore__card explore__card--disabled" aria-disabled="true">
                <span className="explore__icon">{destination.icon}</span>
                <strong>{destination.name}</strong>
                <span className="explore__blurb">{destination.blurb}</span>
                <em>Not available yet</em>
              </span>
            )
        ))}
      </div>

      <div className="explore__labs">
        <h3><FlaskConical size={16} aria-hidden="true" /> Interactive labs</h3>
        <p className="explore__labs-note">
          A lab is listed only at its real status. A planned lab has no controls, no route and no output.
        </p>
        <ul>
          {SPORT_ORDER.map((league) => {
            const sport = sports.find((item) => item.leagueCode === league)
            if (!sport?.lab) return null
            const available = sport.lab.status === 'AVAILABLE' && sport.navigable
            const labName = sport.lab.name.split('— ')[1] ?? sport.lab.name
            return (
              <li key={league} className={available ? 'lab lab--available' : 'lab lab--planned'}>
                <span className="lab__name">
                  <span aria-hidden="true">{sport.icon}</span> {sport.shortName} {labName}
                </span>
                <span className="lab__status">{available ? 'Available' : 'Planned'}</span>
                <span className="lab__blurb">
                  {available
                    ? 'Open any MLB game and choose the simulator to explore its simulated score distribution.'
                    : sport.lab.description}
                </span>
              </li>
            )
          })}
        </ul>
      </div>
    </section>
  )
}

/** Why the numbers above can be trusted. Deliberately short. */
function TrustSection() {
  const claims: Array<{ title: string; detail: string }> = [
    { title: 'Forecasts are timestamped', detail: 'Every forecast records when it was produced and from which engine.' },
    { title: 'Decisions are recorded before the event', detail: 'A decision is written with its cutoff, then never edited.' },
    { title: 'Settled results are immutable', detail: 'Once an event settles, its recorded outcome is preserved as it was.' },
    { title: 'Model and policy are measured separately', detail: 'Forecast accuracy and wagering-policy performance are tracked apart.' },
    { title: 'Prediction markets are observational', detail: 'Kalshi and Polymarket are comparisons only. AISP takes no position in them.' },
    { title: 'The experiment is virtual', detail: 'The $1,000 Experiment uses virtual research positions. No real money is staked.' },
  ]
  return (
    <section className="trust" aria-labelledby="trust-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">VERIFIED PERFORMANCE</span>
          <h2 id="trust-heading">How this record is kept honest</h2>
        </div>
        <Link className="trust__link" to="/performance">See the performance record</Link>
      </div>
      <ul className="trust__grid">
        {claims.map((claim) => (
          <li key={claim.title}>
            <strong>{claim.title}</strong>
            <span>{claim.detail}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** Human age of the last complete refresh; never guesses when unrecorded. */
export function relativeAge(at: string | null, now: number): string {
  if (!at) return 'Not recorded'
  const value = Date.parse(at)
  if (Number.isNaN(value)) return 'Not recorded'
  const minutes = Math.round((now - value) / 60000)
  if (minutes < 0) return formatTimestamp(at)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m ago`
  return `${Math.floor(hours / 24)}d ago`
}

/**
 * The policy that governs official virtual decisions regardless of whether a
 * funded experiment is active. "None active" alone reads as "no policy is in
 * force", which is false: V2.1 still decides.
 */
const GOVERNING_POLICY = 'AISP_PRACTICAL_PREGAME_POLICY_V2_1'

function oddsRefreshLabel(state: MultiSportDataFreshness['odds_refresh_state']): string {
  switch (state) {
    case 'POLLING_DISABLED':
      return 'scheduled odds polling is disabled'
    case 'QUOTA_BELOW_FLOOR':
      return 'provider quota is at or below the configured floor'
    case 'QUOTA_MAY_HAVE_RENEWED_PROBE_REQUIRED':
      return 'quota reading is stale; an authorized probe is required'
    case 'NO_QUOTA_READING':
      return 'no provider quota reading is recorded'
    default:
      return 'refresh state unavailable'
  }
}

/**
 * The active experiment scopes, kept apart.
 *
 * More than one experiment runs at once and they are legitimately different
 * scopes: V2.1 is what funds official virtual positions, and V1 is retained as
 * its strict comparison. Picking whichever produced more rows today presented
 * the comparison as if it were the funded experiment, which is wrong. This
 * labels each scope from the persisted identities and invents neither.
 */
export function experimentScopes(
  selections: { experiment_name: string | null; policy_version: string | null }[],
): { funded: string | null; comparisons: string[] } {
  const labels = new Map<string, string>()
  selections.forEach((item) => {
    if (!item.policy_version) return
    const label = item.experiment_name
      ? `${item.experiment_name} · ${item.policy_version}`
      : item.policy_version
    labels.set(item.policy_version, label)
  })
  const funded = labels.get(GOVERNING_POLICY) ?? null
  const comparisons = [...labels.entries()]
    .filter(([policy]) => policy !== GOVERNING_POLICY)
    .map(([, label]) => label)
    .sort()
  return { funded, comparisons }
}

export function hasNoCrossSportRanking(data: TodayAllSportsReadModel): boolean {
  return data.cross_sport_ranking === 'NOT_APPLICABLE_PHASE_1'
}

/** Kept for callers that still translate a raw availability state. */
export function availabilityLabel(state: SportAvailabilityState): string {
  const labels: Record<SportAvailabilityState, string> = {
    LIVE: 'Active forecasting',
    RESEARCH_SHADOW: 'Research foundation',
    DEVELOPMENT: 'In development',
    PLANNED: 'Planned',
    UNAVAILABLE: 'Unavailable',
  }
  return labels[state] ?? state
}
