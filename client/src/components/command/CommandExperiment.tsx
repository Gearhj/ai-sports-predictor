/**
 * The $1,000 Experiment, in the Model-era visual language, for one scope.
 *
 * A coordinated filter (Combined, MLB, NFL, NBA, College Football) drives the
 * whole summary: the bankroll hero, "Where the experiment stands", progress to
 * the first ~50-position review, the settlement-order step chart, The Tape,
 * the sport accounts, open positions, settlements and the transaction ledger.
 * Every one of them is computed from the same scoped ledger rows
 * (`services/experimentScope`). Combined is the complete four-account
 * experiment; a single model era's subset never stands in for it. The
 * per-era Model-era detail stays below the page, unchanged.
 *
 * Every figure is from `/command-center/ledger` (account contribution plus
 * settled P&L; open stakes are reserved capital, never counted as won or lost)
 * and `/sports/readiness` (status and next opportunity). A recorded entry price
 * is the price the position was taken at; it is never replaced by a later
 * market quote. Research parlays never enter this ledger.
 */
import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useSportReadiness } from '../readiness/SportReadiness'
import {
  SPORT_CODES, SPORT_COLORS, SPORT_NAMES, etTime, gameHref, money, signedMoney, useLedger,
  type LedgerPosition, type SportCode,
} from '../../services/commandCenter'
import {
  REVIEW_CHECKPOINT, SCOPES, SCOPE_LABEL, scopeExperiment, scopedTransactions, type ExperimentScope,
} from '../../services/experimentScope'
import { BankrollHero, ExperimentNarrative, SettlementTape } from '../experiment/BankrollPanels'
import { EquityDrawdownChart } from '../experiment/EquityDrawdownChart'
import { bankrollFacts, equitySeries, narrative } from '../experiment/bankrollMath'
import { sportStatus } from './CommandHome'
import { AsOf } from './RegionState'
import './command.css'
import './assessments.css'

const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const shortBook = (name: string | null | undefined) => (name ?? '—').replace(/\s*\(.*\)\s*$/, '')
const SETTLEMENT_RULE: Record<SportCode, string> = {
  MLB: 'Moneyline · no tie',
  NFL: 'Moneyline · tie refunds stake',
  NBA: 'Moneyline · tie refunds stake',
  CFB: 'Moneyline · tie refunds stake',
}

type Row = LedgerPosition & { sport: SportCode }

const readScope = (value: string | null): ExperimentScope =>
  (SCOPES as string[]).includes(value ?? '') ? value as ExperimentScope : 'COMBINED'

export function ReviewProgress({ settled, open }: { settled: number; open: number }) {
  const share = Math.min(1, settled / REVIEW_CHECKPOINT)
  return (
    <div className="exp-review" aria-label="Progress to the first review">
      <div className="exp-review__bar" role="progressbar" aria-valuemin={0} aria-valuemax={REVIEW_CHECKPOINT} aria-valuenow={settled}
        aria-label={`${settled} of about ${REVIEW_CHECKPOINT} settled positions`}>
        <span style={{ width: `${share * 100}%` }} />
      </div>
      <p>
        <b>{settled}</b> of about {REVIEW_CHECKPOINT} settled positions{open ? ` · ${open} open` : ''}.
        {settled >= REVIEW_CHECKPOINT
          ? ' The first review sample is reached; it still describes this ledger, not proven skill.'
          : ` ${REVIEW_CHECKPOINT - settled} more before the first honest review of the policy.`}
      </p>
    </div>
  )
}

export function CommandExperiment({ highlight }: { highlight?: string | null }) {
  const ledger = useLedger()
  const readiness = useSportReadiness()
  const [params, setParams] = useSearchParams()
  const scope = readScope(params.get('scope'))
  const setScope = (next: ExperimentScope) => setParams((old) => {
    const copy = new URLSearchParams(old)
    if (next === 'COMBINED') copy.delete('scope')
    else copy.set('scope', next)
    return copy
  }, { replace: true })
  const data = ledger.data
  const view = useMemo(() => scopeExperiment(data ?? null, scope), [data, scope])
  const facts = useMemo(() => (view ? bankrollFacts(view.data) : null), [view])
  const points = useMemo(() => (view && facts ? equitySeries(view.data.equity_curve, facts.startingBankroll) : []), [view, facts])
  const rows: Row[] = view?.rows ?? []
  const open = view?.open ?? []
  const settled = useMemo(() => [...(view?.settled ?? [])].reverse(), [view])
  const transactions = useMemo(() => scopedTransactions(data ?? null, scope), [data, scope])
  const scopeSports = scope === 'COMBINED' ? SPORT_CODES : [scope]

  return (
    <section className="cc-top cc-tokens cc-experiment" aria-label="$1,000 Experiment summary">
      <header className="exp-scope-head">
        <div>
          <span className="cc-kicker">$1,000 Experiment · {scope === 'COMBINED' ? 'combined · all four virtual sport accounts' : `${SPORT_NAMES[scope]} account`}</span>
          <h1 className="cc-title">{scope === 'COMBINED' ? 'The whole experiment' : `${SPORT_NAMES[scope]} account`}</h1>
        </div>
        <div className="cc-xhero__side">
          <span className="cc-chip">Virtual research · no real money</span>
          <AsOf resource={ledger} label="ledger" />
        </div>
      </header>
      <div className="ex-scope" role="group" aria-label="Experiment scope">
        {SCOPES.map((code) => (
          <button key={code} type="button" aria-pressed={scope === code} onClick={() => setScope(code)}>{SCOPE_LABEL[code]}</button>
        ))}
      </div>
      {ledger.error && !data && <p className="cc-empty" role="alert">The ledger is unavailable: {ledger.error}</p>}
      {!data && !ledger.error && <p className="cc-empty" role="status">Reading the ledger…</p>}

      {view && facts && (
        <div className="exp" data-scope={scope}>
          <BankrollHero facts={facts} />
          <p className="experiment-sample-note" role="note">
            <strong>{facts.settledCount} settled position{facts.settledCount === 1 ? '' : 's'} {facts.settledCount === 1 ? 'is' : 'are'} too small a sample to establish skill or profitability.</strong>
            {' '}Every figure is a bookkeeping fact about this virtual ledger{scope === 'COMBINED' ? ' across all four accounts' : ` for the ${SPORT_NAMES[scope]} account`}.
          </p>

          <section className="exp-page__block" aria-label="Where the experiment stands">
            <h2 className="exp-page__title">Where the experiment stands</h2>
            <ExperimentNarrative lines={narrative(facts, REVIEW_CHECKPOINT)} />
          </section>

          <section className="exp-page__block" aria-label="Progress to the first review">
            <h2 className="exp-page__title">Toward the first review</h2>
            <ReviewProgress settled={facts.settledCount} open={facts.openCount} />
          </section>

          <section className="exp-page__block" aria-label="Equity and drawdown">
            <h2 className="exp-page__title">Every dollar, in settlement order</h2>
            <p className="exp-muted">Opening capital plus each settlement, in the order positions resolved. Open stakes are marked separately and are not in the line.</p>
            {facts.hasSettled
              ? <EquityDrawdownChart points={points} starting={facts.startingBankroll} positions={view.data.positions ?? []}
                height={380} openCount={facts.openCount} openExposure={facts.openExposure} />
              : <p className="exp-empty">No position in this scope has settled, so there is no path to draw. It still holds {money(facts.startingBankroll, 0)}.</p>}
          </section>

          <section className="exp-page__block" aria-label="Settlement tape">
            <h2 className="exp-page__title">The Tape</h2>
            <p className="exp-muted">Every position in this scope, newest first. Losses are never removed and open positions are shown as open.</p>
            <SettlementTape positions={view.data.positions ?? []} league={scope === 'COMBINED' ? 'MLB' : scope} />
          </section>
        </div>
      )}

      <h2 className="cc-section-title">Sport accounts</h2>
      <div className="cc-sports">
        {scopeSports.map((code) => {
          const s = data?.sports.find((x) => x.sport === code)
          const entry = readiness.data?.sports.find((x) => x.sport === code)
          const status = sportStatus(entry)
          return (
            <article key={code} className="cc-sport" style={{ ['--sport' as string]: SPORT_COLORS[code] }} aria-label={`${SPORT_NAMES[code]} account`}>
              <header><h3>{SPORT_NAMES[code]}</h3><b>{money(s?.realized_bankroll)}</b></header>
              <dl className="cc-acct">
                <div><dt>Starting</dt><dd>{money(s?.starting_capital, 0)}</dd></div>
                <div><dt>Available</dt><dd>{money(s?.available_capital)}</dd></div>
                <div><dt>Reserved</dt><dd>{money(s?.open_exposure)}</dd></div>
                <div><dt>Realized P&amp;L</dt><dd className={(s?.profit_loss ?? 0) < 0 ? 'is-down' : (s?.profit_loss ?? 0) > 0 ? 'is-up' : ''}>{signedMoney(s?.profit_loss)}</dd></div>
                <div><dt>Open · settled</dt><dd>{s ? `${s.open_count} · ${s.settled_count}` : '—'}</dd></div>
                <div><dt>Effective</dt><dd>{s?.account.effective_on ?? '—'}</dd></div>
              </dl>
              <p><span className={`cc-chip ${status.tone ? `cc-chip--${status.tone}` : ''}`}>{status.text}</span></p>
              <p>{entry?.next_game ? `Next: ${entry.next_game.matchup} · ${entry.decision?.label ?? entry.next_game.starts_label}` : 'No game scheduled'}</p>
            </article>
          )
        })}
      </div>

      <div className="cc-panel">
        <div className="cc-panel__head"><h2>Open positions</h2><span className="cc-note" style={{ margin: 0 }}>Recorded entry terms, not the latest market price</span></div>
        {open.length === 0
          ? <p className="cc-empty">No position is open{scope === 'COMBINED' ? ' in any account' : ` in the ${SPORT_NAMES[scope]} account`}. Positions are only recorded when a forecast, eligible policy and fresh price qualify inside a decision window.</p>
          : <PositionTable rows={open} highlight={highlight} />}
      </div>

      <div className="cc-panel">
        <div className="cc-panel__head"><h2>Settlements</h2><span className="cc-note" style={{ margin: 0 }}>{settled.length} in {SCOPE_LABEL[scope]}</span></div>
        {settled.length === 0 ? <p className="cc-empty">Nothing has settled yet.</p> : <PositionTable rows={settled.slice(0, 12)} highlight={highlight} />}
        {settled.length > 12 && (
          <details className="cc-table">
            <summary>All {settled.length} settlements</summary>
            <PositionTable rows={settled} highlight={highlight} />
          </details>
        )}
      </div>

      {data && (
        <details className="cc-panel cc-ledger">
          <summary><h2 style={{ display: 'inline' }}>Transaction ledger</h2> <span className="cc-note">fundings and settlements, in order, with the running bankroll</span></summary>
          <div className="cc-table__scroll">
            <table className="cc-board">
              <thead><tr><th>When</th><th>Account</th><th>Entry</th><th>Change</th><th>Account bankroll</th></tr></thead>
              <tbody>
                {transactions.map((p, i) => {
                    const pos = p.position_id ? rows.find((r) => r.position_id === p.position_id) : null
                    return (
                      <tr key={`${p.sport}-${p.position_id ?? 'fund'}-${i}`}>
                        <td>{etTime(p.at, true)}</td>
                        <td>{SPORT_NAMES[p.sport]}</td>
                        <td>{p.kind === 'FUNDING' ? 'Season opening funding' : `${pos?.selection ?? ''} ${am(pos?.entry.american)} · ${pos?.matchup ?? ''} · ${pos?.result ?? ''}`}</td>
                        <td className={(p.change ?? 0) < 0 ? 'is-down' : (p.change ?? 0) > 0 ? 'is-up' : ''}>{p.kind === 'FUNDING' ? money(p.bankroll) : signedMoney(p.change)}</td>
                        <td>{money(p.bankroll)}</td>
                      </tr>
                    )
                  })}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  )
}

function PositionTable({ rows, highlight }: { rows: Row[]; highlight?: string | null }) {
  return (
    <div className="cc-table__scroll">
      <table className="cc-board">
        <thead>
          <tr><th>Game</th><th>Selection</th><th>Entry price</th><th>Sportsbook · provider</th><th>Stake</th><th>Settlement rule</th><th>Result</th><th>P&amp;L</th></tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.position_id} id={`position-${p.position_id}`} className={highlight === p.position_id ? 'is-selected' : ''}>
              <td><Link className="cc-linkish" to={gameHref(p.sport, p.game_id)}>
                <i className="cc-row__sport" style={{ ['--sport' as string]: SPORT_COLORS[p.sport] }} />{p.matchup}</Link>
                <small>{SPORT_NAMES[p.sport]} · {etTime(p.starts_at, true)}</small></td>
              <td>{p.selection}</td>
              <td><b>{am(p.entry.american)}</b><small>{p.entry.decimal ? `${p.entry.decimal.toFixed(2)} decimal` : ''}{p.entry.quote_observed_at ? ` · quote ${etTime(p.entry.quote_observed_at)}` : ''}</small></td>
              <td>{shortBook(p.entry.sportsbook)}<small>{p.entry.provider} · {p.entry.classification.toLowerCase()}</small></td>
              <td>{money(p.stake)}</td>
              <td>{SETTLEMENT_RULE[p.sport]}</td>
              <td>{p.status === 'SETTLED' ? <span className={`cc-chip ${(p.profit_loss ?? 0) > 0 ? 'cc-chip--good' : (p.profit_loss ?? 0) < 0 ? 'cc-chip--bad' : ''}`}>{p.result}</span> : <span className="cc-chip cc-chip--warn">Open</span>}</td>
              <td className={(p.profit_loss ?? 0) < 0 ? 'is-down' : (p.profit_loss ?? 0) > 0 ? 'is-up' : ''}>{p.profit_loss == null ? '—' : signedMoney(p.profit_loss)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
