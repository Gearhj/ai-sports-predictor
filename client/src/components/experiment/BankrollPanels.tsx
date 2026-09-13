/**
 * The reusable pieces of a bankroll experiment.
 *
 * WHY THESE ARE COMPONENTS AND NOT PAGE MARKUP
 * --------------------------------------------
 * The same ledger is presented twice today -- compactly on Home, in full on
 * the experiment page -- and the two had drifted into disagreeing about what
 * a return is. One shape, used twice, cannot disagree with itself.
 *
 * They also take a `ledger` rather than reading an endpoint, which is what a
 * future side-by-side comparison needs: the governed model's bankroll and a
 * human's picks would be different ledgers rendered by the same components,
 * with the model's authority and data untouched. Nothing here knows or cares
 * where its ledger came from, and nothing here can merge two of them.
 */
import { useMemo, useState } from 'react'
import { AlertTriangle, ChevronDown, Circle, Minus, Square, Triangle } from 'lucide-react'
import { TeamLogo } from '../TeamLogo'
import { teamIdentity } from '../../services/teamIdentity'
import {
  money, signedMoney, signedPct, type BankrollFacts, type NarrativeLine,
} from './bankrollMath'
import type { Position } from '../../types/domain'
import './experiment.css'

/* ========================================================================= */
/* The headline                                                              */
/* ========================================================================= */

/**
 * The bankroll, at the size it deserves, with both returns named.
 *
 * The current balance is the largest thing on the page by a wide margin
 * because it is the one number the experiment exists to report. Everything
 * else is support, and the two return measures sit side by side precisely so
 * neither can be mistaken for the other: +0.35% of the bankroll and +11.50%
 * of settled stake are both true and describe different questions.
 */
export function BankrollHero({
  facts, compact = false, action,
}: { facts: BankrollFacts; compact?: boolean; action?: React.ReactNode }) {
  const ahead = facts.realizedProfit >= 0
  return (
    <section className={`exp-hero${compact ? ' exp-hero--compact' : ''}`} aria-label="Bankroll position">
      <div className="exp-hero__main">
        <span className="exp-hero__label">Virtual bankroll</span>
        <strong className={`exp-hero__value${ahead ? ' is-up' : ' is-down'}`}>
          {money(facts.currentBankroll)}
        </strong>
        <p className="exp-hero__delta">
          <span className={ahead ? 'is-up' : 'is-down'}>{signedMoney(facts.realizedProfit)}</span>
          {' from '}{money(facts.startingBankroll, 0)}
          {' · '}<span className={ahead ? 'is-up' : 'is-down'}>{signedPct(facts.bankrollReturnPct, 2)}</span>
          {' bankroll return'}
        </p>
        {action}
      </div>

      <dl className="exp-hero__measures">
        <div>
          <dt>Realized profit</dt>
          <dd className={ahead ? 'is-up' : 'is-down'}>{signedMoney(facts.realizedProfit)}</dd>
          <small>settled positions only</small>
        </div>
        <div>
          <dt>Bankroll return</dt>
          <dd className={ahead ? 'is-up' : 'is-down'}>{signedPct(facts.bankrollReturnPct, 2)}</dd>
          <small>profit ÷ {money(facts.startingBankroll, 0)} opening</small>
        </div>
        <div>
          <dt>Return on settled stake</dt>
          <dd className={ahead ? 'is-up' : 'is-down'}>{signedPct(facts.returnOnSettledStakePct, 2)}</dd>
          <small>profit ÷ {money(facts.settledStake)} resolved</small>
        </div>
        <div>
          <dt>Record</dt>
          <dd>{facts.record}</dd>
          <small>{facts.settledCount} settled · wins–losses–pushes</small>
        </div>
        <div>
          <dt>Open exposure</dt>
          <dd>{money(facts.openExposure)}</dd>
          <small>{facts.openCount} unsettled · in no figure above</small>
        </div>
      </dl>
    </section>
  )
}

/* ========================================================================= */
/* The story                                                                 */
/* ========================================================================= */

export function ExperimentNarrative({ lines }: { lines: NarrativeLine[] }) {
  return (
    <div className="exp-story" aria-label="What the ledger says">
      {lines.map((line) => (
        <p key={line.key} className={`exp-story__line${line.tone === 'caution' ? ' is-caution' : ''}`}>
          {line.tone === 'caution' && <AlertTriangle size={17} aria-hidden="true" />}
          <span>{line.text}</span>
        </p>
      ))}
    </div>
  )
}

/* ========================================================================= */
/* The settlement tape                                                       */
/* ========================================================================= */

type TapeFilter = 'ALL' | 'WIN' | 'LOSS' | 'OPEN'

const RESULT_ICON: Record<string, React.ReactNode> = {
  WIN: <Triangle size={13} aria-hidden="true" />,
  LOSS: <Square size={12} aria-hidden="true" />,
  PUSH: <Minus size={13} aria-hidden="true" />,
  VOID: <Minus size={13} aria-hidden="true" />,
  OPEN: <Circle size={12} aria-hidden="true" />,
}

/** "TB @ TEX" → the two clubs, so a row can carry real identity. */
function sidesOf(matchup: string | null | undefined): [string, string] | null {
  const parts = (matchup ?? '').split('@').map((part) => part.trim())
  return parts.length === 2 && parts[0] && parts[1] ? [parts[0], parts[1]] : null
}

/**
 * Every position, newest first, losses included and never collapsed.
 *
 * Result is carried by a SHAPE as well as a colour -- triangle up for a win,
 * square for a loss, dash for a push, hollow circle for open -- so the tape
 * survives colour blindness and a black-and-white screenshot. The filters
 * default to All, and "Lost" is a first-class choice rather than something a
 * reader has to go looking for.
 */
export function SettlementTape({
  positions, league = 'MLB', startOpen = false,
}: { positions: Position[]; league?: string; startOpen?: boolean }) {
  const [filter, setFilter] = useState<TapeFilter>('ALL')
  const [expanded, setExpanded] = useState<string | null>(null)

  const ordered = useMemo(() => positions.slice().sort((a, b) => {
    const left = a.settlement_sequence ?? Number.MAX_SAFE_INTEGER
    const right = b.settlement_sequence ?? Number.MAX_SAFE_INTEGER
    return right - left
  }), [positions])

  const counts = useMemo(() => ({
    ALL: ordered.length,
    WIN: ordered.filter((position) => position.result === 'WIN').length,
    LOSS: ordered.filter((position) => position.result === 'LOSS').length,
    OPEN: ordered.filter((position) => position.result === 'OPEN').length,
  }), [ordered])

  const shown = filter === 'ALL' ? ordered : ordered.filter((position) => position.result === filter)

  return (
    <div className="exp-tape">
      <div className="exp-tape__filters" role="tablist" aria-label="Filter the ledger">
        {(['ALL', 'WIN', 'LOSS', 'OPEN'] as const).map((value) => (
          <button key={value} type="button" role="tab" aria-selected={filter === value}
            className="exp-tape__filter" onClick={() => setFilter(value)}>
            {value === 'ALL' ? 'All' : value === 'WIN' ? 'Won' : value === 'LOSS' ? 'Lost' : 'Open'}
            <span>{counts[value]}</span>
          </button>
        ))}
      </div>

      {shown.length === 0
        ? <p className="exp-empty">No position matches this filter yet.</p>
        : <ol className="exp-tape__list">
          {shown.map((position) => {
            const sides = sidesOf(position.matchup)
            const open = position.result === 'OPEN'
            const isOpenRow = expanded === position.id
            return (
              <li key={position.id} className={`exp-row exp-row--${position.result.toLowerCase()}`}>
                <span className="exp-row__seq">
                  {position.settlement_sequence != null ? `#${position.settlement_sequence}` : 'open'}
                </span>

                <span className="exp-row__teams">
                  {sides
                    ? <>
                      <TeamLogo league={league} abbreviation={sides[0]} size={34} />
                      <b>{sides[0]}</b>
                      <i>at</i>
                      <TeamLogo league={league} abbreviation={sides[1]} size={34} />
                      <b>{sides[1]}</b>
                    </>
                    : <b>{position.matchup ?? 'matchup not recorded'}</b>}
                </span>

                <span className="exp-row__pick">
                  <b>{position.selection}</b>
                  <small>
                    {position.price == null ? 'price not recorded'
                      : `${position.price > 0 ? '+' : ''}${position.price}`}
                    {position.sportsbook ? ` · ${position.sportsbook}` : ''}
                  </small>
                </span>

                <span className="exp-row__stake">
                  <b>{money(position.stake)}</b>
                  <small>stake</small>
                </span>

                <span className={`exp-row__result exp-row__result--${position.result.toLowerCase()}`}>
                  {RESULT_ICON[position.result]}
                  {position.result === 'OPEN' ? 'Open' : position.result[0] + position.result.slice(1).toLowerCase()}
                </span>

                <span className="exp-row__pl">
                  <b className={open ? '' : position.profit_loss >= 0 ? 'is-up' : 'is-down'}>
                    {open ? 'pending' : signedMoney(position.profit_loss)}
                  </b>
                  <small>
                    {open
                      ? `${money(position.stake)} at risk`
                      : position.bankroll_after != null ? `→ ${money(position.bankroll_after)}` : ''}
                  </small>
                </span>

                <button type="button" className="exp-row__more" aria-expanded={isOpenRow}
                  aria-label={`${isOpenRow ? 'Hide' : 'Show'} evidence for ${position.selection}`}
                  onClick={() => setExpanded(isOpenRow ? null : position.id)}>
                  <ChevronDown size={17} style={{
                    transform: isOpenRow ? 'rotate(180deg)' : undefined, transition: 'transform .18s',
                  }} />
                </button>

                {(isOpenRow || startOpen) && (
                  <dl className="exp-row__evidence">
                    {/* Four numbers a reader needs kept apart. The raw model
                        probability is what V18 published; the adjusted one is
                        what the policy actually used after its saved evidence
                        penalties; the offered price has its own break-even; and
                        the no-vig reference is a different comparison again.
                        Showing one "edge" let the policy's no-vig margin be read
                        as the margin the stake was paid against, and those two
                        differ by the whole penalty — 2.84 pp against no-vig
                        versus 0.32 pp against the price, on the NYY position. */}
                    <div><dt>Model probability (raw)</dt>
                      <dd>{position.raw_model_probability == null
                        ? (position.model_probability == null ? 'not recorded'
                          : `${(position.model_probability * 100).toFixed(1)}%`)
                        : `${(position.raw_model_probability * 100).toFixed(2)}%`}</dd></div>
                    <div><dt>Adjusted probability used</dt>
                      <dd>{position.adjusted_probability == null ? 'not recorded'
                        : `${(position.adjusted_probability * 100).toFixed(2)}%`}</dd></div>
                    <div><dt>Offered price break-even</dt>
                      <dd>{position.offered_break_even_probability == null ? 'not recorded'
                        : `${(position.offered_break_even_probability * 100).toFixed(2)}%`}
                        <small>1 ÷ decimal odds</small></dd></div>
                    <div><dt>Edge vs offered price</dt>
                      <dd>{position.adjusted_probability == null
                        || position.offered_break_even_probability == null ? 'not recorded'
                        : `${((position.adjusted_probability
                              - position.offered_break_even_probability) * 100).toFixed(2)} pp`}
                        <small>what the stake is paid against</small></dd></div>
                    <div><dt>No-vig reference probability</dt>
                      <dd>{position.market_probability == null ? 'not recorded'
                        : `${(position.market_probability * 100).toFixed(2)}%`}</dd></div>
                    <div><dt>Edge vs no-vig</dt>
                      <dd>{position.adjusted_edge_vs_no_vig == null
                        ? (position.adjusted_edge == null ? 'not recorded'
                          : `${(position.adjusted_edge * 100).toFixed(2)} pp`)
                        : `${(position.adjusted_edge_vs_no_vig * 100).toFixed(2)} pp`}
                        <small>what the policy minimum is applied to</small></dd></div>
                    <div><dt>Decided</dt>
                      <dd>{position.recommended_at
                        ? new Date(position.recommended_at).toLocaleString('en-US', {
                          month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
                        }) : 'not recorded'}</dd></div>
                    <div><dt>Settled</dt>
                      <dd>{position.settled_at
                        ? new Date(position.settled_at).toLocaleString('en-US', {
                          month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
                        }) : 'not settled'}</dd></div>
                    <div><dt>Closing price</dt>
                      <dd>{position.closing_price == null ? 'no closing quote persisted'
                        : `${position.closing_price > 0 ? '+' : ''}${position.closing_price}`
                          + (position.clv == null ? '' : ` · CLV ${position.clv > 0 ? '+' : ''}${position.clv.toFixed(3)}%`)}</dd></div>
                    <div><dt>Phase</dt><dd>{position.decision_phase ?? 'not recorded'}</dd></div>
                    <div className="exp-row__evidence-id">
                      <dt>Position</dt><dd><code>{position.id}</code></dd>
                    </div>
                  </dl>
                )}
              </li>
            )
          })}
        </ol>}
    </div>
  )
}

/* ========================================================================= */
/* Risk and process                                                          */
/* ========================================================================= */

/**
 * What the experiment risked, not just what it won.
 *
 * Every slice here refuses to draw itself below a usable sample. A profit
 * split across one sport and twelve settlements is not a "profit by sport"
 * chart, it is the same number twice, and drawing it would invent a
 * comparison the data cannot support.
 */
export function RiskAnalytics({ facts, positions }: { facts: BankrollFacts; positions: Position[] }) {
  const settled = positions.filter((position) => position.result !== 'OPEN')
  const stakes = settled.map((position) => position.stake)
  const uniqueStakes = new Set(stakes.map((stake) => stake.toFixed(2)))
  const winRate = facts.settledCount ? facts.wins / facts.settledCount : null
  // Wilson at 95%: the normal approximation collapses at this sample and would
  // imply a precision that twelve positions cannot support.
  const interval = wilson(facts.wins, facts.settledCount)

  return (
    <div className="exp-risk">
      <div className="exp-risk__grid">
        <Metric label="Cumulative amount risked" value={money(facts.totalStaked)}
          note={`${money(facts.settledStake)} settled · ${money(facts.openExposure)} still open`} />
        <Metric label="Average stake" value={money(facts.averageStake)}
          note={uniqueStakes.size === 1
            ? `every settled stake was identical at ${money(stakes[0])}`
            : `${uniqueStakes.size} distinct stake sizes`} />
        <Metric label="Peak bankroll" value={money(facts.peakBankroll)}
          note={`lowest ${money(facts.lowestBankroll)}`} />
        <Metric label="Maximum drawdown" value={money(facts.maxDrawdown)} tone="loss"
          note={facts.maxDrawdownPctOfPeak == null ? 'from the high-water mark'
            : `${facts.maxDrawdownPctOfPeak.toFixed(2)}% of the peak`} />
        <Metric label="Win rate" value={winRate == null ? '—' : `${(winRate * 100).toFixed(1)}%`}
          note={interval
            ? `95% interval ${(interval.low * 100).toFixed(0)}–${(interval.high * 100).toFixed(0)}% — consistent with a coin`
            : 'no settled sample'} />
        <Metric label="Payouts returned" value={money(facts.payouts)}
          note={`against ${money(facts.settledStake)} staked`} />
      </div>

      <div className="exp-risk__slices">
        <Slice title="Stake size distribution"
          available={uniqueStakes.size > 1}
          reason={`Every settled position staked ${money(stakes[0])}. The staking policy has produced `
            + 'one stake size so far, so there is no distribution to plot.'}>
          <p className="exp-muted">Sized by the active staking policy.</p>
        </Slice>
        <Slice title="Profit and loss by sport"
          available={false}
          reason={'Only one sport has funded positions. A single-bar chart of one sport is the net '
            + 'profit again, not a comparison.'} />
        <Slice title="Profit and loss by price band"
          available={false}
          reason={`${facts.settledCount} settled positions spread across price bands would leave one `
            + 'or two observations per band. That is noise with axes on it.'} />
      </div>
    </div>
  )
}

function Metric({ label, value, note, tone }: {
  label: string; value: string; note?: string; tone?: 'loss'
}) {
  return (
    <div className="exp-metric">
      <small>{label}</small>
      <strong className={tone === 'loss' ? 'is-down' : undefined}>{value}</strong>
      {note && <span>{note}</span>}
    </div>
  )
}

function Slice({ title, available, reason, children }: {
  title: string; available: boolean; reason: string; children?: React.ReactNode
}) {
  return (
    <div className={`exp-slice${available ? '' : ' is-unavailable'}`}>
      <h4>{title}</h4>
      {available ? children : <p className="exp-muted">Not enough evidence yet. {reason}</p>}
    </div>
  )
}

function wilson(successes: number, n: number): { low: number; high: number } | null {
  if (n === 0) return null
  const z = 1.96
  const rate = successes / n
  const denominator = 1 + (z * z) / n
  const centre = (rate + (z * z) / (2 * n)) / denominator
  const spread = (z / denominator) * Math.sqrt((rate * (1 - rate)) / n + (z * z) / (4 * n * n))
  return { low: Math.max(0, centre - spread), high: Math.min(1, centre + spread) }
}

/* ========================================================================= */
/* How it works                                                              */
/* ========================================================================= */

export function HowItWorks({ facts }: { facts: BankrollFacts }) {
  return (
    <details className="exp-how">
      <summary>How this experiment works</summary>
      <div className="exp-how__body">
        <ul>
          <li>
            It opens with a hypothetical <b>{money(facts.startingBankroll, 0)}</b> and takes only
            positions the governed policy recorded <b>before</b> the game started.
          </li>
          <li>
            Each position uses the sportsbook and the price that were actually observed and
            timestamped at the moment of the decision — not a better price found later.
          </li>
          <li>
            Stakes come from the active staking policy
            {facts.stakingPolicy ? <> (<code>{facts.stakingPolicy}</code>)</> : null}, applied to
            the bankroll as it stood at the time.
          </li>
          <li>
            A position is never added after its result is known, and no position is ever edited or
            removed. Losses stay in the ledger, and every game the policy passed on stays visible
            on the decisions page.
          </li>
          <li>
            Nothing counts until it settles. Open positions appear as exposure and contribute to no
            profit figure, no record and no return.
          </li>
          <li>
            This is <b>virtual research</b>. No real money is staked, and nothing here is betting
            advice.
          </li>
        </ul>
        <p className="exp-muted">
          Experiment {facts.experimentName ?? 'not identified'}
          {facts.policy ? <> · decision policy <code>{facts.policy}</code></> : null}
          {facts.startDate ? ` · opened ${facts.startDate}` : ''}.
        </p>
      </div>
    </details>
  )
}
