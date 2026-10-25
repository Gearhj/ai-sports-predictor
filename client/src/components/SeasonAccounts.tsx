import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertTriangle, CalendarRange, CircleDollarSign, Info, Layers } from 'lucide-react'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { endpoints } from '../services/api'
import { Freshness, Panel, SkeletonCards, SkeletonTable, TableScroll, formatCurrency, formatSignedCurrency } from './ui'
import { formatTimestamp } from '../services/format'
import type {
  AccountPosition, EquityPoint, EraSummary, ExperimentAccountsData, SeasonAccount,
} from '../types/accounts'

// The server's program order (season_calendar.supported_sports); College Football was missing here.
const PROGRAM_SPORTS = ['MLB', 'NFL', 'NBA', 'CFB', 'NHL', 'MMA']

/** A percentage, or an explicit unavailability. Never a fabricated 0.0%. */
function pct(value: number | null | undefined, unavailable = 'Unavailable'): string {
  if (value == null || !Number.isFinite(value)) return unavailable
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function money(value: number | null | undefined): string {
  return value == null ? '—' : formatCurrency(value)
}

const STATUS_LABEL: Record<string, string> = {
  PLANNED: 'Not started',
  ACTIVE: 'Active',
  CLOSED_FOR_NEW_POSITIONS: 'Closed to new positions · awaiting final settlement',
  ARCHIVED: 'Season closed',
}

/**
 * Realized bankroll over the season, with the $1,000 opening drawn in.
 *
 * Hand-built SVG rather than a chart library: the shape needed here is one
 * line, one reference and a readable table, and the values must be reachable
 * without seeing the picture. The `<table>` below is not a fallback — it is the
 * accessible representation, and the chart is marked decorative so a screen
 * reader is given the numbers rather than a description of a drawing.
 *
 * Nothing is drawn before the account's real tracking start. A curve that ran
 * back to the configured season opening would be inventing months of flat
 * performance that were never traded.
 */
function EquityCurve({ points, contribution, label }: {
  points: EquityPoint[]
  contribution: number
  label: string
}) {
  if (points.length === 0) {
    return <p className="bx-dim" role="status">No settlement has moved this bankroll yet.</p>
  }
  const width = 640
  const height = 180
  const pad = { top: 14, right: 14, bottom: 22, left: 54 }
  const values = points.map((point) => point.bankroll)
  // The reference is always inside the range, so the $1,000 line is visible
  // even when every point sits on one side of it.
  const low = Math.min(...values, contribution)
  const high = Math.max(...values, contribution)
  const span = high - low || 1
  const padded = { low: low - span * 0.12, high: high + span * 0.12 }
  const range = padded.high - padded.low

  const x = (index: number) =>
    pad.left + (points.length === 1
      ? (width - pad.left - pad.right) / 2
      : (index / (points.length - 1)) * (width - pad.left - pad.right))
  const y = (value: number) =>
    pad.top + (1 - (value - padded.low) / range) * (height - pad.top - pad.bottom)

  const path = points.map((point, index) =>
    `${index === 0 ? 'M' : 'L'}${x(index).toFixed(1)},${y(point.bankroll).toFixed(1)}`,
  ).join(' ')
  const referenceY = y(contribution)
  const last = points[points.length - 1]
  const above = last.bankroll >= contribution

  return (
    <div className="equity-curve">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="equity-curve__chart"
        role="presentation"
        aria-hidden="true"
        preserveAspectRatio="none"
      >
        <line
          className="equity-curve__reference"
          x1={pad.left} x2={width - pad.right} y1={referenceY} y2={referenceY}
        />
        <text className="equity-curve__reference-label" x={pad.left - 6} y={referenceY + 3}>
          {formatCurrency(contribution, 0)}
        </text>
        <path
          className={`equity-curve__line ${above ? 'is-up' : 'is-down'}`}
          d={path}
        />
        {points.map((point, index) => (
          <circle
            key={`${point.date}-${index}`}
            className="equity-curve__point"
            cx={x(index)} cy={y(point.bankroll)} r={2.5}
          />
        ))}
      </svg>
      <TableScroll ariaLabel={`${label} realized bankroll by date`}>
        <table className="equity-curve__values">
          <caption className="visually-hidden">
            {label}: realized bankroll after each settlement date, against a
            {` ${formatCurrency(contribution, 0)} `}opening contribution.
          </caption>
          <thead>
            <tr><th scope="col">Date</th><th scope="col">Realized bankroll</th><th scope="col">vs opening</th><th scope="col">Settlements</th></tr>
          </thead>
          <tbody>
            {/* The opening point can share a date with a settlement that same
                day, so the index is part of the key: two rows, one date. */}
            {points.map((point, index) => (
              <tr key={`${point.date}-${index}`}>
                <th scope="row">{point.date}</th>
                <td>{formatCurrency(point.bankroll)}</td>
                <td className={point.bankroll >= contribution ? 'is-up' : 'is-down'}>
                  {formatSignedCurrency(point.bankroll - contribution)}
                </td>
                <td>{point.settlements === 0 ? 'Opening' : point.settlements}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </div>
  )
}

function SportCard({ account, selected, onSelect }: {
  account: SeasonAccount
  selected: boolean
  onSelect: (sport: string) => void
}) {
  const planned = account.original_contribution == null
  return (
    <button
      type="button"
      className={`season-card${selected ? ' season-card--selected' : ''}${planned ? ' season-card--planned' : ''}`}
      aria-pressed={selected}
      onClick={() => onSelect(account.sport)}
    >
      <span className="season-card__head">
        <strong>{account.sport}</strong>
        <span className="season-card__season">{account.season_id}</span>
      </span>
      <span className={`season-card__status season-card__status--${account.status.toLowerCase()}`}>
        {STATUS_LABEL[account.status] ?? account.status}
      </span>
      {planned ? (
        <>
          {/* Planned, not funded: the figure is labelled as an allocation and
              no balance, return or record is shown, because none exists. */}
          <span className="season-card__planned-amount">
            {formatCurrency(account.planned_contribution, 0)} planned
          </span>
          <span className="season-card__note">Not funded · nothing tracked</span>
        </>
      ) : (
        <>
          <span className="season-card__bankroll">{money(account.realized_bankroll)}</span>
          <span className={`season-card__return ${(account.season_return_pct ?? 0) >= 0 ? 'is-up' : 'is-down'}`}>
            {pct(account.season_return_pct)} season return
          </span>
          <span className="season-card__note">
            {money(account.available_capital)} available · {account.record ?? '—'}
          </span>
        </>
      )}
    </button>
  )
}

function EraRow({ era }: { era: EraSummary }) {
  const excluded = era.role !== 'FUNDED_ERA'
  return (
    <tr className={excluded ? 'era-row--excluded' : undefined}>
      <th scope="row">
        v{era.version} · {era.model_era_id ?? era.controlling_engine}
        {/* "Active" and "in control" differ: a predecessor stays active so its
            open positions keep settling. Only the controlling era says so. */}
        {era.is_controlling && <span className="era-row__active"> · controlling now</span>}
      </th>
      <td>{era.started_on}</td>
      <td>
        {era.role === 'FUNDED_ERA' ? 'Funded era'
          : era.role === 'UNFUNDED_CONTROL' ? 'Unfunded control'
            : 'Research candidate'}
      </td>
      <td>{excluded ? <span className="bx-dim">Excluded</span> : formatSignedCurrency(era.settled_net_pl)}</td>
      <td>{excluded ? '—' : formatCurrency(era.reserved_stake)}</td>
      <td>{era.open_positions}</td>
      <td>{era.settled_positions}</td>
    </tr>
  )
}

function PositionRow({ position }: { position: AccountPosition }) {
  // The ledger's enum values are lower-case ("open"/"settled"); comparing
  // against "OPEN" labelled every open position with its pending result.
  const open = position.status.toUpperCase() === 'OPEN'
  return (
    <tr>
      <td>{formatTimestamp(position.placed_at)}</td>
      <td>{position.era ?? '—'}</td>
      <td>{formatCurrency(position.stake)}</td>
      {/* Entry terms from the position's own snapshot and decision. A bare
          decimal told the reader the price and nothing about the side, the
          book or who supplied it. */}
      <td className="ledger-entry">
        {position.entry
          ? <>
              <b data-testid={`ledger-entry-${position.id}`}>
                {position.entry.side ?? 'Selection'} {position.entry.american_label ?? position.decimal_odds.toFixed(3)}
              </b>
              <small>
                {position.entry.sportsbook_key ?? '—'} · decimal {position.decimal_odds.toFixed(3)}
              </small>
              {position.entry.provider.label && <small>{position.entry.provider.label}</small>}
              {position.entry.tie_settlement_label && (
                <small data-testid={`ledger-tie-${position.id}`}>{position.entry.tie_settlement_label}</small>
              )}
            </>
          : position.decimal_odds.toFixed(3)}
      </td>
      <td>
        {open
          ? <span className="bx-chip">Open</span>
          : position.result.charAt(0).toUpperCase() + position.result.slice(1)}
      </td>
      <td className={position.profit_loss == null ? undefined : position.profit_loss >= 0 ? 'is-up' : 'is-down'}>
        {position.profit_loss == null ? '—' : formatSignedCurrency(position.profit_loss)}
      </td>
      <td>{position.settled_at ? formatTimestamp(position.settled_at) : '—'}</td>
    </tr>
  )
}

function AccountDetail({ account }: { account: SeasonAccount }) {
  if (account.original_contribution == null) {
    return (
      <Panel
        eyebrow={`${account.sport} · ${account.season_id}`}
        title={`${account.sport_label} — not started`}
        className="section-panel"
      >
        <p className="season-detail__planned">
          <CircleDollarSign size={15} aria-hidden="true" />
          <strong>{formatCurrency(account.planned_contribution, 0)} planned allocation.</strong>{' '}
          No capital has been contributed to this account and nothing is being
          tracked. This figure is a proposal, not funded money and not available
          capital.
        </p>
        {account.status_note && <p className="bx-dim">{account.status_note}</p>}
        <p className="bx-dim season-detail__boundary">
          <CalendarRange size={13} aria-hidden="true" />
          Season window {account.boundary.opens_on} → {account.boundary.closes_on}. {account.boundary.basis}
          {account.boundary.convention ? ` ${account.boundary.convention}` : ''}
        </p>
      </Panel>
    )
  }

  const contribution = account.original_contribution
  return (
    <>
      <Panel
        eyebrow={`${account.sport} · ${account.season_label}`}
        title="Season account"
        className="section-panel"
        action={
          <span className={`season-card__status season-card__status--${account.status.toLowerCase()}`}>
            {STATUS_LABEL[account.status] ?? account.status}
          </span>
        }
      >
        <dl className="season-metrics">
          <div><dt>Original contribution</dt><dd>{money(contribution)}</dd></div>
          <div><dt>Realized bankroll</dt><dd>{money(account.realized_bankroll)}</dd></div>
          <div><dt>Reserved stakes</dt><dd>{money(account.reserved_stake)}</dd></div>
          <div><dt>Available capital</dt><dd>{money(account.available_capital)}</dd></div>
          <div>
            <dt>Settled net P&amp;L</dt>
            <dd className={(account.settled_net_pl ?? 0) >= 0 ? 'is-up' : 'is-down'}>
              {account.settled_net_pl == null ? '—' : formatSignedCurrency(account.settled_net_pl)}
            </dd>
          </div>
          <div>
            <dt>Season return</dt>
            <dd className={(account.season_return_pct ?? 0) >= 0 ? 'is-up' : 'is-down'}>
              {pct(account.season_return_pct)}
            </dd>
            <dd className="season-metrics__denominator">settled P&amp;L ÷ {money(contribution)} contribution</dd>
          </div>
          <div>
            <dt>Settled-stake ROI</dt>
            {/* A separate question with a separate denominator, and explicitly
                unavailable rather than 0.00% when no stake has settled. */}
            <dd className={(account.settled_stake_roi_pct ?? 0) >= 0 ? 'is-up' : 'is-down'}>
              {pct(account.settled_stake_roi_pct, 'Unavailable — no settled stake')}
            </dd>
            <dd className="season-metrics__denominator">
              settled P&amp;L ÷ {money(account.settled_stake)} settled stakes
            </dd>
          </div>
          <div><dt>Record</dt><dd>{account.record ?? '—'}</dd></div>
          <div>
            <dt>Positions</dt>
            <dd>{account.open_positions ?? 0} open · {account.settled_positions ?? 0} settled</dd>
          </div>
        </dl>

        <p className="bx-dim season-detail__boundary">
          <CalendarRange size={13} aria-hidden="true" />
          Tracking started <strong>{account.tracking_started_on}</strong>
          {account.partial_season && (
            <> · <span className="season-detail__partial">partial season</span> — the season
              window opened {account.boundary.opens_on} and no result is claimed before
              the actual start</>
          )}
          . Season window {account.boundary.opens_on} → {account.boundary.closes_on}.
        </p>
        {account.boundary.convention && (
          <p className="bx-dim"><Info size={13} aria-hidden="true" /> {account.boundary.convention}</p>
        )}
        {account.controlling_engine && (
          <p className="bx-dim">
            <Layers size={13} aria-hidden="true" /> Controlling model:{' '}
            <strong>{account.controlling_era_id ?? account.controlling_engine}</strong>. A model
            change within a season does not reset this bankroll.
          </p>
        )}
      </Panel>

      <Panel eyebrow="Equity" title="Realized bankroll this season" className="section-panel">
        <EquityCurve
          points={account.equity_curve}
          contribution={contribution}
          label={`${account.sport} ${account.season_id}`}
        />
      </Panel>

      <Panel eyebrow="Model eras" title="Which model controlled, and when" className="section-panel">
        <TableScroll ariaLabel="Model eras in this season account">
          <table className="season-table">
            <thead>
              <tr>
                <th scope="col">Era</th><th scope="col">Started</th><th scope="col">Role</th>
                <th scope="col">Settled P&amp;L</th><th scope="col">Reserved</th>
                <th scope="col">Open</th><th scope="col">Settled</th>
              </tr>
            </thead>
            <tbody>
              {account.eras.map((era) => <EraRow key={era.experiment_id} era={era} />)}
            </tbody>
          </table>
        </TableScroll>
        <p className="bx-dim">
          Only funded eras contribute to this account's bankroll. An unfunded
          control or a superseded registration is listed so it is visible, and
          excluded from every figure above.
        </p>
      </Panel>

      {account.positions.length > 0 && (
        <Panel eyebrow="Ledger" title="Open and settled positions" className="section-panel">
          <TableScroll ariaLabel="Positions in this season account">
            <table className="season-table">
              <thead>
                <tr>
                  <th scope="col">Placed</th><th scope="col">Era</th><th scope="col">Stake</th>
                  <th scope="col">Entry terms</th><th scope="col">Result</th>
                  <th scope="col">P&amp;L</th><th scope="col">Settled</th>
                </tr>
              </thead>
              <tbody>
                {account.positions.map((position) => (
                  <PositionRow key={position.id} position={position} />
                ))}
              </tbody>
            </table>
          </TableScroll>
        </Panel>
      )}
    </>
  )
}

/**
 * The season-account surface: one card per sport, then one season in detail.
 *
 * The selection lives in the URL so a refresh, the back button and a shared
 * link all resolve to the same account. Both reads are cached and keyed by the
 * account they describe, so switching sports and switching back is instant and
 * cannot show one sport's figures under another's heading.
 */
export function SeasonAccounts() {
  const [params, setParams] = useSearchParams()
  const selectedSport = (params.get('sport') ?? 'MLB').toUpperCase()
  const selectedSeason = params.get('season')

  const program = useApiResource<ExperimentAccountsData>(
    useCallback((signal: AbortSignal) => endpoints.experimentAccounts(signal), []),
    { key: 'experiment/accounts', staleTimeMs: STALE_TIME.operational },
  )
  const detail = useApiResource<ExperimentAccountsData>(
    useCallback(
      (signal: AbortSignal) =>
        endpoints.experimentAccountsForSport(selectedSport, signal, selectedSeason),
      [selectedSport, selectedSeason],
    ),
    {
      key: cacheKey('experiment/accounts/sport', {
        sport: selectedSport, season: selectedSeason,
      }),
      staleTimeMs: STALE_TIME.operational,
    },
  )

  const select = (sport: string) => {
    const next = new URLSearchParams(params)
    next.set('sport', sport)
    next.delete('season')   // a sport change cannot carry another sport's season
    setParams(next, { replace: true })
  }
  const selectSeason = (season: string) => {
    const next = new URLSearchParams(params)
    next.set('sport', selectedSport)
    next.set('season', season)
    setParams(next, { replace: true })
  }

  const cards = useMemo(() => {
    const byCode = new Map((program.data?.accounts ?? []).map((row) => [row.sport, row]))
    return PROGRAM_SPORTS.map((code) => byCode.get(code)).filter(Boolean) as SeasonAccount[]
  }, [program.data])

  const summary = program.data?.summary
  // The detail payload is for the selected sport only when it says so. Until
  // then it is treated as loading, so one sport's numbers can never render
  // under another sport's heading during a switch.
  const detailAccounts = detail.data?.accounts ?? []
  const matches = detailAccounts.length > 0 && detailAccounts[0].sport === selectedSport
  const seasons = matches ? detailAccounts : []
  const shown = seasons.find((row) => !selectedSeason || row.season_id === selectedSeason)
    ?? seasons[0]

  return (
    <section className="season-accounts" aria-label="Sport and season paper bankrolls">
      <header className="season-accounts__head">
        <div>
          <p className="eyebrow">PAPER BANKROLLS BY SPORT AND SEASON</p>
          <h2>Each sport runs its own $1,000 season experiment</h2>
          <p className="season-accounts__lede">
            A season opens once with $1,000 of virtual capital and tracks through
            that season. Changing the model inside a season does not reset its
            bankroll. Virtual research only — no real-money wagering.
          </p>
        </div>
        <Freshness
          label="Accounts"
          updatedAt={program.updatedAt}
          refreshing={program.refreshing}
          stale={program.stale}
          error={program.data ? program.error : null}
          onRetry={program.refresh}
        />
      </header>

      {program.loading && <SkeletonCards count={5} />}
      {program.error && !program.data && (
        <p className="bx-empty" role="alert">
          {program.error}. No balance is substituted when the read fails.
        </p>
      )}

      {summary && (
        <div className="season-summary" role="group" aria-label="Program totals">
          <div className="season-summary__funded">
            <span className="season-summary__label">Funded capital</span>
            <strong>{formatCurrency(summary.funded_contribution, 0)}</strong>
            <span className="season-summary__detail">
              across {summary.funded_accounts} account{summary.funded_accounts === 1 ? '' : 's'}
              {summary.funded_account_keys.length > 0 && ` (${summary.funded_account_keys.join(', ')})`}
              {' · '}realized {formatCurrency(summary.funded_realized_bankroll)}
              {' · '}available {formatCurrency(summary.funded_available_capital)}
            </span>
          </div>
          {/* Kept in its own box, with its own label, so it can never read as
              money that exists. Five planned sports is $5,000 proposed, not
              $5,000 funded. */}
          <div className="season-summary__planned">
            <span className="season-summary__label">Planned allocation</span>
            <strong>{formatCurrency(summary.planned_allocation, 0)}</strong>
            <span className="season-summary__detail">
              proposed for {summary.planned_accounts} sport
              {summary.planned_accounts === 1 ? '' : 's'} that have not started —{' '}
              <strong>not funded and not available today</strong>
            </span>
          </div>
        </div>
      )}
      {summary && (
        <p className="bx-dim season-summary__note">
          <AlertTriangle size={13} aria-hidden="true" /> {summary.note}
        </p>
      )}

      {cards.length > 0 && (
        <div className="season-cards" role="group" aria-label="Select a sport">
          {cards.map((account) => (
            <SportCard
              key={account.sport}
              account={account}
              selected={account.sport === selectedSport}
              onSelect={select}
            />
          ))}
        </div>
      )}

      {seasons.length > 1 && (
        <div className="season-selector">
          <span className="season-selector__label" id="season-selector-label">Season</span>
          <div role="group" aria-labelledby="season-selector-label">
            {seasons.map((row) => (
              <button
                key={row.season_id}
                type="button"
                className={`season-selector__option${row.season_id === shown?.season_id ? ' is-selected' : ''}`}
                aria-pressed={row.season_id === shown?.season_id}
                onClick={() => selectSeason(row.season_id)}
              >
                {row.season_id}
              </button>
            ))}
          </div>
        </div>
      )}

      {detail.loading && !matches && <SkeletonTable rows={6} columns={4} ariaLabel="Loading season account" />}
      {shown && <AccountDetail account={shown} />}
    </section>
  )
}
