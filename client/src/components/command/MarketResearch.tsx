/**
 * Research families on Markets & Crowd, each mounted only when its tab is open:
 *
 *  - Parlays: a research watchlist of stored-price combinations. It carries no
 *    governed wagering authority: leg compatibility, same-time freshness,
 *    correlation treatment and policy support do not exist for parlays.
 *  - RFF: the market-risk lens across the slate (a research index, not a market).
 *  - Totals model: V18's own run distribution against the recorded total.
 */
import { useState } from 'react'
import { MlbTotalsView } from '../markets/MlbTotalsView'
import { ParlayWatchView } from '../markets/ParlayWatchView'
import { RffView } from '../broadcast/MarketViews'
import { readMarketFilter, useMlbTotals, useParlayWatch, useRecordTicket, useRecordedTickets } from '../../hooks/useMlbTotals'
import type { ParlayWatch } from '../../types/totals'

export function ParlaysPanel({ day }: { day: string }) {
  const [legCount, setLegCount] = useState<2 | 3>(2)
  const [marketFilter, setMarketFilter] = useState<ParlayWatch['market_filter']>(readMarketFilter(null))
  const parlay = useParlayWatch({ day, enabled: true, legCount, marketFilter })
  const recorded = useRecordedTickets(true)
  const { recordTicket, recordingKey, recordedByKey, recordError } = useRecordTicket(() => { parlay.refresh(); recorded.refresh() })
  return (
    <section className="cc-mpanel" aria-label="Parlay watch">
      <p className="cc-banner cc-banner--quiet">
        <b>Research watchlist.</b> Combinations are priced from stored quotes for study only. No parlay carries governed
        wagering authority: leg compatibility, same-time price freshness, correlation treatment and policy support do not
        yet exist. A recorded ticket is a research record, never a position.
      </p>
      <ParlayWatchView
        watch={parlay.data}
        legCount={legCount}
        onLegCount={setLegCount}
        marketFilter={marketFilter}
        onMarketFilter={setMarketFilter}
        onRecord={recordTicket}
        recordingKey={recordingKey}
        recordedByKey={recordedByKey}
        recordError={recordError}
        recorded={recorded.data?.tickets ?? []}
      />
    </section>
  )
}

export function RffPanel({ day, league }: { day: string; league: 'MLB' | 'NFL' | null }) {
  const [selected, setSelected] = useState<string | null>(null)
  return (
    <section className="cc-mpanel" aria-label="RFF market-risk lens">
      <p className="cc-banner cc-banner--quiet">
        <b>RFF is a market-risk lens, not a sportsbook market.</b> It scores market imbalance, movement and unresolved
        information from stored evidence. It never feeds a forecast, threshold, decision or position.
      </p>
      <RffView day={day} league={league} family="MONEYLINE" selected={selected} onSelect={setSelected} />
    </section>
  )
}

export function TotalsModelPanel({ day }: { day: string }) {
  const [oversOnly, setOversOnly] = useState(false)
  const [scenarios, setScenarios] = useState<Record<string, string>>({})
  const totals = useMlbTotals({ day, enabled: true, oversOnly, scenarios })
  return (
    <MlbTotalsView board={totals.data} oversOnly={oversOnly} onOversOnly={setOversOnly}
      scenarios={scenarios} onScenario={(gameId, line) => setScenarios((old) => ({ ...old, [gameId]: line }))} />
  )
}
