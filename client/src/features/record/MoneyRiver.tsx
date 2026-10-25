/**
 * WHERE THE MONEY WENT: every paper bet AISP has made, as a river from sport to price to odds band to result,
 * sized by dollars staked (or by count). Click Underdog and watch where that money landed. The verdict line
 * above it says, in one sentence, which side of the price has made and lost the money.
 */
import { useMemo, useState } from 'react'
import { FlowRiver, type RiverColumn } from '../../components/charts/FlowRiver'
import type { LedgerPosition, SportCode } from '../../services/commandCenter'
import { resultOf, usd } from '../pulse/pulseData'

type Pos = LedgerPosition & { sport: SportCode }

const SPORT_COLOR: Record<string, string> = { MLB: '#ff6b57', NFL: '#6ea4ff', CFB: '#b88cff', NHL: '#7cc4e4', NBA: '#ffb14a' }
const NODE_COLOR: Record<string, string> = {
  Favorite: '#60a5fa', Underdog: '#fb923c', 'Unknown price': '#64748b',
  Moneyline: '#2dd4bf', Spread: '#a78bfa', Total: '#fbbf24',
  'Heavy favorite': '#3b82f6', 'Short underdog': '#fdba74', 'Long shot': '#f472b6',
  Won: '#34d399', Lost: '#f87171', 'Push / void': '#94a3b8', Open: '#c4b5fd',
}

const priceOf = (p: Pos) => { const a = p.entry?.american; return a == null ? 'Unknown price' : a < 0 ? 'Favorite' : 'Underdog' }
const betOf = (p: Pos) => { const m = (p.market ?? '').toUpperCase(); return m.includes('SPREAD') || m.includes('RUN_LINE') || m.includes('PUCK_LINE') ? 'Spread' : m.includes('TOTAL') ? 'Total' : 'Moneyline' }
const bandOf = (p: Pos) => { const a = p.entry?.american; if (a == null) return 'Unknown price'; return a <= -200 ? 'Heavy favorite' : a < 0 ? 'Favorite' : a <= 150 ? 'Short underdog' : a <= 250 ? 'Underdog' : 'Long shot' }
const outOf = (p: Pos) => { const r = resultOf(p); return r === 'WIN' ? 'Won' : r === 'LOSS' ? 'Lost' : r === 'PUSH' ? 'Push / void' : 'Open' }

const SPORT_COL: RiverColumn<Pos> = { title: 'SPORT', of: (p) => p.sport }
const PRICE_COL: RiverColumn<Pos> = { title: 'PRICE', of: priceOf, order: ['Favorite', 'Underdog', 'Unknown price'] }
const ODDS_COL: RiverColumn<Pos> = { title: 'ODDS', of: bandOf, order: ['Heavy favorite', 'Favorite', 'Short underdog', 'Underdog', 'Long shot', 'Unknown price'] }
const BET_COL: RiverColumn<Pos> = { title: 'BET', of: betOf, order: ['Moneyline', 'Spread', 'Total'] }
const RESULT_COL: RiverColumn<Pos> = { title: 'RESULT', of: outOf, order: ['Won', 'Lost', 'Push / void', 'Open'] }

export function MoneyRiver({ positions }: { positions: Pos[] }) {
  const [by, setBy] = useState<'stake' | 'count'>('stake')
  const items = useMemo(() => positions.filter((p) => (p.stake ?? 0) > 0), [positions])
  // the bet-type column only earns its place once AISP bets more than one kind
  const columns = useMemo(() => (new Set(items.map(betOf)).size > 1 ? [SPORT_COL, PRICE_COL, ODDS_COL, BET_COL, RESULT_COL] : [SPORT_COL, PRICE_COL, ODDS_COL, RESULT_COL]), [items])
  const verdict = useMemo(() => {
    const side = (k: string) => {
      const ps = items.filter((p) => priceOf(p) === k)
      const settled = ps.filter((p) => resultOf(p) !== 'OPEN')
      return { n: ps.length, staked: ps.reduce((t, p) => t + (p.stake ?? 0), 0), pl: settled.reduce((t, p) => t + (p.profit_loss ?? 0), 0), w: settled.filter((p) => resultOf(p) === 'WIN').length, l: settled.filter((p) => resultOf(p) === 'LOSS').length }
    }
    return { fav: side('Favorite'), dog: side('Underdog') }
  }, [items])
  if (!items.length) return null
  const total = items.reduce((t, p) => t + (p.stake ?? 0), 0)
  const dogShare = total ? Math.round((verdict.dog.staked / total) * 100) : 0
  const describe = (ps: Pos[]) => {
    const settled = ps.filter((p) => resultOf(p) !== 'OPEN')
    const pl = settled.reduce((t, p) => t + (p.profit_loss ?? 0), 0)
    const w = settled.filter((p) => resultOf(p) === 'WIN').length, l = settled.filter((p) => resultOf(p) === 'LOSS').length
    return [`${ps.length} bet${ps.length === 1 ? '' : 's'} · ${w}–${l}`, `${usd(ps.reduce((t, p) => t + (p.stake ?? 0), 0))} staked · P&L ${usd(pl, true)}`]
  }
  return (
    <section className="sl-card rc-river" aria-label="Where the money went">
      <header className="sl-card__head">
        <h2>🌊 Where the money went <small className="rc-sub">every paper bet, from sport to price to result</small></h2>
        <span className="rc-river__toggle" role="group" aria-label="Size the river by">
          <button type="button" className={by === 'stake' ? 'is-on' : ''} aria-pressed={by === 'stake'} onClick={() => setBy('stake')}>Dollars</button>
          <button type="button" className={by === 'count' ? 'is-on' : ''} aria-pressed={by === 'count'} onClick={() => setBy('count')}>Bets</button>
        </span>
      </header>
      <p className="rc-river__verdict">
        <b>{dogShare}%</b> of the money has gone on <b className="is-dog">underdogs</b> ({verdict.dog.w}–{verdict.dog.l}, <b className={verdict.dog.pl >= 0 ? 'is-up' : 'is-down'}>{usd(verdict.dog.pl, true)}</b>);
        {' '}<b className="is-fav">favorites</b> are {verdict.fav.w}–{verdict.fav.l}, <b className={verdict.fav.pl >= 0 ? 'is-up' : 'is-down'}>{usd(verdict.fav.pl, true)}</b>.
      </p>
      <FlowRiver
        items={items}
        columns={columns}
        value={by === 'stake' ? (p) => p.stake ?? 0 : () => 1}
        colorOf={(col, label) => (col === 0 ? SPORT_COLOR[label] ?? '#94a3b8' : NODE_COLOR[label] ?? '#94a3b8')}
        format={by === 'stake' ? (v) => usd(v) : (v) => `${Math.round(v)}`}
        label={`${items.length} paper bets flowing from sport to price to result`}
        describe={describe}
      />
    </section>
  )
}
