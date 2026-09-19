/**
 * Home · AISP Today: one compact read of the day, above the edge board.
 *
 * Every figure is counted from the same cached read models the rest of Home
 * uses -- market coverage (today's MLB/NFL slate), the decision board, the
 * ledger and sport readiness -- so the numbers reconcile with the cards below
 * and with Daily Decisions. The strip underneath covers all four sports,
 * including the research-only ones, without adding a panel per sport.
 */
import { Link } from 'react-router-dom'
import type { SportReadinessEntry } from '../readiness/SportReadiness'
import {
  SPORT_CODES, SPORT_COLORS, SPORT_NAMES, asSport, etTime,
  type CoverageGame, type DecisionItem, type Ledger, type SportCode,
} from '../../services/commandCenter'
import './assessments.css'
import { controllingMargin, controllingTest, ptsText, testThresholdText, valueText } from '../../services/decisionEdges'

export interface TodaySummary {
  games: number
  forecasts: number
  currentPrices: number
  qualified: number
  openPositions: number
  nextWindow: { at: string; open: boolean; matchup: string | null } | null
  /** Ranked by the policy's first test (margin to its minimum), not by the break-even figure. */
  closest: { matchup: string; edge: number; sport: SportCode; text: string } | null
  story: string
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

export function buildTodaySummary({ games, items, ledger, now }: {
  games: CoverageGame[]; items: DecisionItem[]; ledger: Ledger | null; now: number
}): TodaySummary {
  const forecasts = games.filter((g) => g.model).length
  const currentPrices = games.filter((g) => g.price_state === 'CURRENT_PRICE').length
  const qualifiedGames = new Set(items.filter((i) => i.recommendation === 'QUALIFIED_BET' && i.phase === 'FINAL_PREGAME').map((i) => i.game_id))
  const openPositions = (ledger?.sports ?? []).reduce((n, s) => n + s.positions.filter((p) => p.status !== 'SETTLED').length, 0)

  let nextWindow: TodaySummary['nextWindow'] = null
  for (const g of games) {
    const opens = Date.parse(g.checkpoints.decision_window_opens_at)
    const closes = Date.parse(g.checkpoints.decision_window_closes_at)
    const open = opens <= now && now < closes
    if (!open && opens <= now) continue
    const at = open ? g.checkpoints.decision_window_closes_at : g.checkpoints.decision_window_opens_at
    if (!nextWindow || (open && !nextWindow.open) || (open === nextWindow.open && at < nextWindow.at)) {
      nextWindow = { at, open, matchup: g.matchup }
    }
  }

  const startedById = new Map(games.map((g) => [g.game_id, g.starts_at ? Date.parse(g.starts_at) <= now : false]))
  let closest: TodaySummary['closest'] = null
  for (const i of items) {
    const test = controllingTest(i)
    // "Closest to qualifying" ranks on what the policy tests, as recorded. A
    // payload without edge_metrics falls back to the recorded policy-adjusted
    // edge (item.adjusted.edge), never to the stored unadjusted break-even
    // figure, which is not a policy test.
    const recorded = i.adjusted?.edge ?? null
    const score = test ? controllingMargin(i) : recorded != null ? recorded - 0.02 : null
    if (score == null || qualifiedGames.has(i.game_id) || i.position || startedById.get(i.game_id)) continue
    const sport = asSport(i.sport)
    if (!sport) continue
    if (!closest || score > closest.edge) {
      closest = {
        matchup: i.matchup ?? 'Game', edge: score, sport,
        text: test ? `${test.label.toLowerCase()} ${valueText(test)}, ${testThresholdText(test)}`
          : `recorded policy-adjusted edge ${ptsText(recorded)}, policy requires ≥ 2.0 pts`,
      }
    }
  }

  const parts: string[] = []
  if (games.length === 0) parts.push('No MLB or NFL game is on today’s slate')
  else parts.push(`${plural(games.length, 'game')} on the slate, ${forecasts} with a forecast and ${currentPrices} with a current sportsbook price`)
  if (qualifiedGames.size) parts.push(`${plural(qualifiedGames.size, 'decision')} ${qualifiedGames.size === 1 ? 'qualifies' : 'qualify'}`)
  else if (closest) parts.push(`nothing qualifies yet; ${closest.matchup} is closest (${closest.text})`)
  else if (games.length) parts.push('nothing qualifies yet')
  if (openPositions) parts.push(`${plural(openPositions, 'virtual position')} open`)
  if (nextWindow) parts.push(nextWindow.open ? `a decision window is open until ${etTime(nextWindow.at)}` : `the next decision window opens at ${etTime(nextWindow.at)}`)
  const story = `${parts.join('; ')}.`.replace(/^./, (c) => c.toUpperCase())

  return { games: games.length, forecasts, currentPrices, qualified: qualifiedGames.size, openPositions, nextWindow, closest, story }
}

const FORECAST_SHORT: Record<string, string> = {
  CONTROLLING_FORECAST_AVAILABLE: 'held',
  RESEARCH_FORECAST_AVAILABLE: 'research held',
  // NBA Gen1: a verified production package, which is not the same as a research example.
  PRODUCTION_PACKAGE_AVAILABLE: 'production package held',
  PROSPECTIVE_FORECAST_AVAILABLE: 'prospective held',
  FORECAST_AWAITING_PRODUCER: 'awaiting',
  FORECAST_UNAVAILABLE: 'none',
}
const PRICE_SHORT: Record<string, string> = {
  FRESH_PRICE: 'current',
  STORED_REFERENCE_ONLY: 'stale reference only',
  COLLECTION_NOT_OPEN: 'collection not open',
  COLLECTION_BLOCKED: 'intake blocked',
}
const RESEARCH_SPORTS = new Set<SportCode>(['NBA', 'CFB'])

export function readinessLine(entry: SportReadinessEntry | undefined, code: SportCode) {
  const research = RESEARCH_SPORTS.has(code)
  return {
    authority: entry?.controlling_engine
      ? `${entry.controlling_engine.replace(/_/g, ' ')}${research ? ' · research only' : ''}`
      : research ? 'Research only' : '—',
    forecast: entry?.forecast ? FORECAST_SHORT[entry.forecast.state] ?? entry.forecast.state.replace(/_/g, ' ').toLowerCase() : '—',
    price: entry?.price ? PRICE_SHORT[entry.price.state] ?? entry.price.state.replace(/_/g, ' ').toLowerCase() : '—',
    eligibility: entry?.eligibility
      ? (!entry.eligibility.eligible ? 'Not eligible'
        // College Football: a present eligibility record with a lapsed discovery lease is not eligible.
        : entry.eligibility.discovery_lease && entry.eligibility.discovery_lease !== 'CURRENT'
          ? (entry.eligibility.discovery_lease === 'EXPIRED_LEASE' ? 'Eligibility lease expired' : 'Not currently eligible')
          : research ? 'Paper research eligible' : 'Eligible')
      : research ? 'Not eligible' : 'Decided by policy at the window',
    next: entry?.next_game ? `${entry.next_game.matchup} · ${entry.next_game.starts_label}` : 'No game scheduled',
  }
}

export function AispToday({ summary, readiness, loading, decisionsHref }: {
  summary: TodaySummary; readiness: SportReadinessEntry[]; loading: boolean; decisionsHref: string
}) {
  return (
    <section className="at-today" aria-label="AISP Today">
      <header className="at-today__head">
        <h2>AISP Today</h2>
        <Link to={decisionsHref}>Daily Decisions →</Link>
      </header>
      <p className="at-today__story">{loading ? 'Reading today’s slate…' : summary.story}</p>
      <dl className="at-stats">
        <div><dt>Games today</dt><dd>{summary.games}</dd></div>
        <div><dt>With forecast</dt><dd>{summary.forecasts}</dd></div>
        <div><dt>Current prices</dt><dd>{summary.currentPrices}</dd></div>
        <div><dt>Qualified</dt><dd className={summary.qualified ? 'is-up' : ''}>{summary.qualified}</dd></div>
        <div><dt>Open positions</dt><dd>{summary.openPositions}</dd></div>
        <div><dt>{summary.nextWindow?.open ? 'Window closes' : 'Next window'}</dt>
          <dd>{summary.nextWindow ? etTime(summary.nextWindow.at).replace(' ET', '') : '—'}<small>{summary.nextWindow?.matchup ? ` ${summary.nextWindow.matchup}` : ''}</small></dd></div>
        <div><dt>Closest to qualifying</dt>
          <dd>{summary.closest ? <>{summary.closest.matchup}<small> {summary.closest.text}</small></> : '—'}</dd></div>
      </dl>
      <div className="at-strip" aria-label="Sport readiness">
        {SPORT_CODES.map((code) => {
          const entry = readiness.find((r) => asSport(r.sport) === code)
          const line = readinessLine(entry, code)
          return (
            <div key={code} className="at-sport" style={{ ['--sport' as string]: SPORT_COLORS[code] }} data-testid={`today-${code}`}>
              <b>{SPORT_NAMES[code]}</b>
              <span><i>Model</i> {line.authority}</span>
              <span><i>Forecast</i> {line.forecast} · <i>Price</i> {line.price}</span>
              <span><i>Eligibility</i> {line.eligibility}</span>
              <span><i>Next</i> {line.next}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}
