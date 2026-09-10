/**
 * Where the model and the market disagree, on the home page.
 *
 * WHEN THIS RENDERS
 * -----------------
 * Only when a row carries BOTH the model's fair price and a market consensus
 * for the same selection. A comparison needs two comparable numbers; a row with
 * one of them is not a small disagreement, it is no comparison at all, and it
 * is dropped rather than shown as a zero. If nothing qualifies, the panel
 * removes itself instead of rendering an empty frame.
 *
 * WHAT A BAR MEANS, AND WHAT IT DOES NOT
 * --------------------------------------
 * The bar is the gap between the model's implied probability and the market's,
 * in percentage points. It is a disagreement, not an edge and not a
 * recommendation: whether any of it survives the policy's thresholds, staleness
 * gates and vig treatment is a separate question answered on the decision
 * board, and nothing here is a position.
 */
import { useMemo, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { MarketBoardRow } from '../../types/domain'

/** American odds to implied probability, vig included. */
export function impliedProbability(american: number): number {
  return american > 0 ? 100 / (american + 100) : -american / (-american + 100)
}

export interface Disagreement {
  id: string
  matchup: string
  selection: string
  modelProbability: number
  marketProbability: number
  gap: number
  modelPrice: number
  marketPrice: number
}

/**
 * One row per SELECTION, keeping only rows with both prices.
 *
 * A moneyline market publishes both sides, and each side's disagreement is the
 * mirror of the other's, so showing both would double-count the same
 * disagreement and fill the panel with pairs. Only the side the model prefers
 * is kept.
 */
export function disagreements(rows: MarketBoardRow[]): Disagreement[] {
  const out: Disagreement[] = []
  for (const row of rows) {
    if (row.asp_fair == null || row.consensus == null) continue
    const modelProbability = impliedProbability(row.asp_fair)
    const marketProbability = impliedProbability(row.consensus)
    const gap = modelProbability - marketProbability
    if (gap <= 0) continue
    out.push({
      id: row.id,
      matchup: row.matchup,
      selection: row.selection,
      modelProbability,
      marketProbability,
      gap,
      modelPrice: row.asp_fair,
      marketPrice: row.consensus,
    })
  }
  return out.sort((a, b) => b.gap - a.gap)
}

function price(value: number): string {
  return value > 0 ? `+${value.toFixed(0)}` : value.toFixed(0)
}

export function ModelVersusMarket({ rows, observedAt }: {
  rows: MarketBoardRow[]
  observedAt: string | null
}) {
  const all = useMemo(() => disagreements(rows), [rows])
  const [selected, setSelected] = useState<string | null>(null)

  if (all.length === 0) return null
  const top = all.slice(0, 6)
  const widest = Math.max(...top.map((row) => row.gap))
  const active = top.find((row) => row.id === selected) ?? null

  return (
    <section className="mvm" aria-labelledby="mvm-heading">
      <div className="home-panel__head">
        <div>
          <span className="eyebrow">MODEL VERSUS MARKET</span>
          <h2 id="mvm-heading">Where they disagree today</h2>
        </div>
        <Link to="/markets?sport=MLB" className="home-panel__more">
          Open <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </div>

      <ul className="mvm__list">
        {top.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              className={`mvm__row${active?.id === row.id ? ' mvm__row--on' : ''}`}
              aria-pressed={active?.id === row.id}
              onClick={() => setSelected(active?.id === row.id ? null : row.id)}
            >
              <span className="mvm__who">
                <b>{row.selection}</b>
                <small>{row.matchup}</small>
              </span>
              <span className="mvm__scale" aria-hidden="true">
                <i style={{ width: `${(row.gap / widest) * 100}%` }} />
              </span>
              <span className="mvm__gap">
                +{(row.gap * 100).toFixed(1)}<small>pts</small>
              </span>
            </button>
          </li>
        ))}
      </ul>

      {active && (
        <dl className="mvm__detail" role="status">
          <div>
            <dt>Model fair price</dt>
            <dd>{price(active.modelPrice)}<small>{(active.modelProbability * 100).toFixed(1)}% implied</small></dd>
          </div>
          <div>
            <dt>Market consensus</dt>
            <dd>{price(active.marketPrice)}<small>{(active.marketProbability * 100).toFixed(1)}% implied</small></dd>
          </div>
          <div>
            <dt>Disagreement</dt>
            <dd>+{(active.gap * 100).toFixed(1)} pts<small>not an edge or a recommendation</small></dd>
          </div>
        </dl>
      )}

      <p className="mvm__note">
        Implied probabilities include the bookmaker&rsquo;s margin, so a disagreement is not by
        itself a betting edge. Whether any of it survives the policy&rsquo;s thresholds and
        freshness gates is decided on the decision board; nothing here is a position.
        {observedAt && <> Prices observed {new Date(observedAt).toLocaleString()}.</>}
      </p>
    </section>
  )
}
