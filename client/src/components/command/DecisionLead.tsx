/**
 * The lead of a Daily Decisions game card: the OFFICIAL decision status, its
 * selection at the price RECORDED at the decision, one plain sentence on what
 * happens next (or why it passed), the recorded policy-adjusted edge, the latest
 * price seen (kept apart), one countdown and short freshness chips.
 * Mapping: services/decisionLead.
 */
import { useId, useState, type ReactNode } from 'react'
import { etTime } from '../../services/commandCenter'
import {
  AUTHORITY_LABEL, BEST_QUOTE_LABEL, NOT_AVAILABLE, bestQuoteSource, freshness,
  type DecisionLead as Lead, type LatestSeen,
} from '../../services/decisionLead'

const american = (a: number | null | undefined) => (a == null ? NOT_AVAILABLE : a > 0 ? `+${a}` : `${a}`)
const money = (v: number | null | undefined) => (v == null ? NOT_AVAILABLE : `$${Math.abs(v).toFixed(2)}`)

export function DecisionLead({ lead, latest, countdown, now }: {
  lead: Lead
  latest: LatestSeen | null
  countdown: { label: string; value: string } | null
  now: number
}) {
  const o = lead.official
  const chips = freshness(lead, latest, now)
  const selectionLabel = lead.authority === 'FUNDED' ? 'Funded selection'
    : lead.authority === 'FINAL' ? 'Official selection' : 'Early-read selection'
  return (
    <section className={`dl dl--${lead.authority.toLowerCase()} dl--tone-${lead.tone}`} aria-label={`${AUTHORITY_LABEL[lead.authority]}: ${lead.label}`}>
      <div className="dl-top">
        <span className="dl-kind">{AUTHORITY_LABEL[lead.authority]}</span>
        <span className={`dl-status cc-tone--${lead.tone}`}>{lead.label}</span>
      </div>
      {o ? (
        <div className="dl-sel">
          <small>{selectionLabel} · {o.market}</small>
          <p><b>{o.selection}</b> <strong>{american(o.american)}</strong> <span>at {o.sportsbook ?? `sportsbook ${NOT_AVAILABLE}`}</span></p>
          <small className="dl-recorded">Recorded at decision{o.quoteObservedAt ? ` · quote observed ${etTime(o.quoteObservedAt)}` : ''}</small>
        </div>
      ) : (
        <div className="dl-sel dl-sel--none"><small>Official selection</small><p>None recorded yet</p></div>
      )}
      <p className="dl-sentence">{lead.sentence}</p>
      {lead.waitingOn ? (
        <p className={`dl-waiting dl-waiting--${lead.tone}`} role="note">
          <b>{lead.tone === 'red' ? '⛔' : '⚠'}</b> {lead.waitingOn === 'FORECAST' ? 'No position can be taken until the model publishes its forecast for this game.' : 'No position can be taken until a sportsbook posts a current price on both sides.'}
        </p>
      ) : null}
      {lead.fundingPause && (
        <p className="dl-paused">
          <b>Shadow only · no paper position</b> · funding pause recorded{' '}
          {lead.fundingPause.pausedAt ? etTime(lead.fundingPause.pausedAt) : `(time ${NOT_AVAILABLE})`} · record{' '}
          <code title={`record ${lead.fundingPause.recordId} · sha256 ${lead.fundingPause.contentSha256}`}>
            {lead.fundingPause.contentSha256.slice(0, 12)}
          </code>
          {lead.fundingPause.verified ? '' : ' · record hash not verified'}
        </p>
      )}
      {lead.funded && (
        <p className="dl-funded">
          <b>Paper stake {money(lead.funded.stake)}</b> · entry {american(lead.funded.american)}{lead.funded.sportsbook ? ` at ${lead.funded.sportsbook}` : ''} ·{' '}
          {lead.funded.ledgerConfirmed === true ? 'backed by a ledger position'
            : lead.funded.ledgerConfirmed === false ? 'position not found in the latest ledger read' : 'ledger read pending'}
        </p>
      )}
      <dl className="dl-facts">
        <div>
          <dt>{lead.edge.label}</dt>
          <dd className={lead.edge.value == null ? 'is-dim' : lead.edge.value >= 0.02 ? 'is-up' : ''}>{lead.edge.text}</dd>
          <small>{lead.edge.qualifier}</small>
        </div>
        {/* The current market matters only before the game; once it has started the recorded price is the story. */}
        {o && countdown?.label !== 'Game' && (
          <div>
            <dt>{BEST_QUOTE_LABEL}</dt>
            <dd className={latest ? '' : 'is-dim'}>{latest ? american(latest.american) : 'no current price'}</dd>
            <small>{latest ? bestQuoteSource(latest, o.quoteObservedAt) : 'separate from the recorded decision price'}</small>
          </div>
        )}
        {countdown && (
          <div>
            <dt>{countdown.label}</dt>
            <dd>{countdown.value}</dd>
          </div>
        )}
      </dl>
      {chips.length > 0 && <ul className="dl-fresh" aria-label="Freshness">{chips.map((c) => <li key={c}>{c}</li>)}</ul>}
    </section>
  )
}

/** A keyboard-operable disclosure (button + aria-expanded + aria-controls). */
export function ExpandableDetails({ summary, children, defaultOpen = false }: { summary: string; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const id = useId()
  return (
    <div className={`dl-details${open ? ' is-open' : ''}`}>
      <button type="button" className="dl-details__toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
        <span aria-hidden="true">{open ? '▾' : '▸'}</span> {summary}
      </button>
      <div id={id} className="dl-details__body" hidden={!open}>{children}</div>
    </div>
  )
}
