/**
 * The $1,000 Experiment. First viewport: a dark aggregate headline -- combined
 * bankroll, starting capital, net P&L and return, today's P&L, available and
 * reserved capital, open positions and the per-sport breakdown -- with the
 * interactive bankroll curve inside it. Then the four sport accounts, open
 * positions, recent settlements and the transaction ledger.
 *
 * Every figure is from `/command-center/ledger` (account contribution plus
 * settled P&L; open stakes are reserved capital, never counted as won or lost)
 * and `/sports/readiness` (status and next opportunity). A recorded entry price
 * is the price the position was taken at; it is never replaced by a later
 * market quote.
 */
import { Link } from 'react-router-dom'
import { useSportReadiness } from '../readiness/SportReadiness'
import {
  SPORT_CODES, SPORT_COLORS, SPORT_NAMES, etTime, gameHref, money, signedMoney, useLedger,
  type LedgerPosition, type SportCode,
} from '../../services/commandCenter'
import { BankrollHistory } from './BankrollHistory'
import { sportStatus, todayEt, todaysSettlements } from './CommandHome'
import { AsOf } from './RegionState'
import './command.css'

const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const shortBook = (name: string | null | undefined) => (name ?? '—').replace(/\s*\(.*\)\s*$/, '')
const SETTLEMENT_RULE: Record<SportCode, string> = {
  MLB: 'Moneyline · no tie',
  NFL: 'Moneyline · tie refunds stake',
  NBA: 'Moneyline · tie refunds stake',
  CFB: 'Moneyline · tie refunds stake',
}

type Row = LedgerPosition & { sport: SportCode }

export function CommandExperiment({ highlight }: { highlight?: string | null }) {
  const ledger = useLedger()
  const readiness = useSportReadiness()
  const data = ledger.data
  const totals = data?.totals
  const rows: Row[] = (data?.sports ?? []).flatMap((s) => s.positions.map((p) => ({ ...p, sport: s.sport })))
  const open = rows.filter((p) => p.status !== 'SETTLED')
  const settled = rows.filter((p) => p.status === 'SETTLED').sort((a, b) => (b.settled_at ?? '').localeCompare(a.settled_at ?? ''))
  const record = totals?.record
  const today = todaysSettlements(data ?? null, todayEt())
  const tone = (v: number | null | undefined) => (v == null || v === 0 ? '' : v > 0 ? 'is-up' : 'is-down')
  const ret = totals && totals.starting_capital ? (totals.profit_loss / totals.starting_capital) * 100 : null

  return (
    <section className="cc-top cc-tokens cc-experiment" aria-label="$1,000 Experiment summary">
      <section className="cc-dark cc-hero-bank cc-xhero" aria-label="Combined virtual bankroll">
        <header className="cc-hero-bank__head">
          <div>
            <span className="cc-kicker">$1,000 Experiment · combined virtual bankroll · MLB · NFL · NBA · CFB</span>
            <h1 className="cc-xhero__value cc-hero-bank__value">{money(totals?.realized_bankroll)}</h1>
            <div className={`cc-hero-bank__change ${tone(totals?.profit_loss)}`}>
              {totals && ret != null ? `${signedMoney(totals.profit_loss)} (${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%) net from ${money(totals.starting_capital, 0)} starting capital` : 'Reading the ledger…'}
            </div>
          </div>
          <div className="cc-xhero__side">
            <span className="cc-chip cc-chip--dark">Virtual research · no real money</span>
            <AsOf resource={ledger} label="ledger" />
          </div>
        </header>
        {ledger.error && !data && <p className="cc-empty" role="alert">The ledger is unavailable: {ledger.error}</p>}
        <dl className="cc-hero-bank__stats cc-xhero__stats">
          <div><dt>Starting capital</dt><dd>{money(totals?.starting_capital, 0)}</dd><small>four season accounts</small></div>
          <div><dt>Net P&amp;L · return</dt><dd className={tone(totals?.profit_loss)}>{signedMoney(totals?.profit_loss)}</dd><small>{ret == null ? '—' : `${ret >= 0 ? '+' : ''}${ret.toFixed(2)}% of starting`}</small></div>
          <div><dt>Today’s P&amp;L</dt><dd className={tone(today.pnl)}>{signedMoney(today.pnl)}</dd><small>{today.count} settled today</small></div>
          <div><dt>Available</dt><dd>{money(totals?.available_capital)}</dd><small>virtual capital free</small></div>
          <div><dt>Reserved</dt><dd>{money(totals?.open_exposure)}</dd><small>in open stakes</small></div>
          <div><dt>Open · record</dt><dd>{totals?.open_count ?? '—'}</dd><small>{record ? `${record.WIN}–${record.LOSS}${record.PUSH ? `–${record.PUSH}` : ''} from ${totals?.settled_count ?? 0} settled` : ''}</small></div>
        </dl>
        <ul className="cc-xhero__sports" aria-label="Sport breakdown">
          {SPORT_CODES.map((code) => {
            const s = data?.sports.find((x) => x.sport === code)
            return (
              <li key={code} style={{ ['--sport' as string]: SPORT_COLORS[code] }}>
                <i className="cc-dot" /><span>{SPORT_NAMES[code]}</span>
                <b>{money(s?.realized_bankroll)}</b>
                <small className={tone(s?.profit_loss)}>{s ? signedMoney(s.profit_loss) : '—'}{s?.open_count ? ` · ${s.open_count} open` : ''}</small>
              </li>
            )
          })}
        </ul>
        {data
          ? <BankrollHistory ledger={data} compactHeight={190} hideTable />
          : <div className="cc-hero-bank__placeholder" />}
        <p className="cc-hero-bank__foot">Realized bankroll steps once per settlement; open stakes are reserved, never counted as won or lost. Hover or tab to a marker.</p>
      </section>

      <div className="cc-sports">
        {SPORT_CODES.map((code) => {
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
          ? <p className="cc-empty">No position is open in any account. Positions are only recorded when a forecast, eligible policy and fresh price qualify inside a decision window.</p>
          : <PositionTable rows={open} highlight={highlight} />}
      </div>

      <div className="cc-panel">
        <div className="cc-panel__head"><h2>Recent settlements</h2><span className="cc-note" style={{ margin: 0 }}>{settled.length} total</span></div>
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
                {data.sports.flatMap((s) => s.series.map((p) => ({ ...p, sport: s.sport })))
                  .sort((a, b) => (a.at ?? '').localeCompare(b.at ?? ''))
                  .map((p, i) => {
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
