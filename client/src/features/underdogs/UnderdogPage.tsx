/**
 * THE UNDERDOG REPORT (Scotty, 10-28: "how many times did the underdog win?").
 *
 * Every finished game since AISP started keeping closing prices: the underdog is the side the closing no-vig market
 * priced under 50%. Per sport, how often the underdog won next to how often the market said it would; by size of
 * underdog; what $1 on every underdog at the best closing price would have done; AISP's own paper record backing
 * underdogs; the biggest upsets. Reads /preview-api/insights/underdogs (read-only).
 */
import { useMemo, useState, type CSSProperties } from 'react'
import { AispSide } from '../flow/AispSide'
import { usePreviewJson } from '../edge/insights'
import { BANDS, aispSplit, americanOf, bandOf, tally, upsets, type DogRow } from './underdogs'
import '../slate/slate.css'
import './underdogs.css'

const SPORTS = ['ALL', 'NFL', 'CFB', 'NBA', 'NHL', 'MLB'] as const
const NAME: Record<string, string> = { ALL: 'All sports', NFL: 'NFL', CFB: 'College football', NBA: 'NBA', NHL: 'NHL', MLB: 'MLB' }
const COLOR: Record<string, string> = { ALL: '#fbbf24', NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }
const pct = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`
const money = (v: number) => `${v >= 0 ? '+' : '−'}$${Math.abs(v).toFixed(2)}`
const odds = (d: number | null) => { const a = americanOf(d); return a == null ? '—' : a > 0 ? `+${a}` : `${a}` }

export function UnderdogPage() {
  const { data, error } = usePreviewJson<{ rows: DogRow[]; method: string; days: number }>('/preview-api/insights/underdogs')
  const [sport, setSport] = useState<(typeof SPORTS)[number]>('ALL')
  const [recent, setRecent] = useState(false)
  const all = data?.rows ?? []
  const cutoff = useMemo(() => new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10), [])
  const windowed = useMemo(() => (recent ? all.filter((r) => r.date >= cutoff) : all), [all, recent, cutoff])
  const rows = useMemo(() => (sport === 'ALL' ? windowed : windowed.filter((r) => r.sport === sport)), [windowed, sport])
  const t = tally(rows)
  const since = all[0]?.date
  const perSport = SPORTS.filter((s) => s !== 'ALL').map((s) => ({ s, t: tally(windowed.filter((r) => r.sport === s)) })).filter((x) => x.t.n > 0)
  const bands = BANDS.map((b) => ({ b, t: tally(rows.filter((r) => bandOf(r.dog_p).key === b.key)) })).filter((x) => x.t.n > 0)
  const a = aispSplit(rows)
  const top = upsets(rows)
  const diff = t.rate - t.expected

  return (
    <div className="hq ud">
      <AispSide />
      <div className="hq-main">
        <header className="sl-top">
          <div className="sl-title"><small>DIG DEEPER</small><h1>The Underdog Report</h1><p>How often the underdog actually won, sport by sport, against how often the betting market said it would.</p></div>
        </header>
        <main id="main-content" className="ud-body">
          <div className="ud-ctrl">
            <span className="ud-seg" role="group" aria-label="Sport">
              {SPORTS.map((s) => <button key={s} type="button" aria-pressed={sport === s} className={sport === s ? 'is-on' : ''} style={{ ['--sc' as string]: COLOR[s] } as CSSProperties} onClick={() => setSport(s)}>{s === 'ALL' ? 'All' : s}</button>)}
            </span>
            <span className="ud-seg" role="group" aria-label="Time window">
              <button type="button" aria-pressed={!recent} className={!recent ? 'is-on' : ''} onClick={() => setRecent(false)}>Since {since ? new Date(`${since}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '…'}</button>
              <button type="button" aria-pressed={recent} className={recent ? 'is-on' : ''} onClick={() => setRecent(true)}>Last 30 days</button>
            </span>
          </div>

          {!data ? (
            <p className="ud-wait" role="status">{error ? `The report could not load (${error}).` : 'Counting every finished game…'}</p>
          ) : (
            <>
              <section className="ud-hero" style={{ ['--sc' as string]: COLOR[sport] } as CSSProperties} aria-label="Headline">
                <div className="ud-hero__big">
                  <small>{NAME[sport].toUpperCase()} · UNDERDOGS WON</small>
                  <b>{pct(t.rate)}</b>
                  <span>{t.won.toLocaleString()} of {t.n.toLocaleString()} games</span>
                </div>
                <div className="ud-hero__vs">
                  <div><small>The market expected</small><b>{pct(t.expected)}</b></div>
                  <div className={diff >= 0 ? 'is-up' : 'is-down'}><small>Underdogs {diff >= 0 ? 'beat' : 'fell short of'} the market by</small><b>{diff >= 0 ? '+' : '−'}{(Math.abs(diff) * 100).toFixed(1)} pts</b></div>
                  <div className={t.flatProfit >= 0 ? 'is-up' : 'is-down'}><small>$1 on every underdog</small><b>{money(t.flatProfit)}</b><em>{t.flatBets} bets at the best closing price · {t.flatBets ? `${((t.flatProfit / t.flatBets) * 100).toFixed(1)}% return` : ''}</em></div>
                </div>
                <p className="ud-hero__read">{readOut(t.n, diff)}</p>
              </section>

              {sport === 'ALL' && (
                <section className="ud-card" aria-label="By sport">
                  <h2>By sport <small>the bar is how often the underdog won; the white tick is what the market expected</small></h2>
                  <ul className="ud-bars">
                    {perSport.map(({ s, t: st }) => (
                      <li key={s} style={{ ['--sc' as string]: COLOR[s] } as CSSProperties}>
                        <button type="button" className="ud-bars__name" onClick={() => setSport(s)}>{NAME[s]}<small>{st.n} games</small></button>
                        <span className="ud-bars__track" aria-label={`${NAME[s]}: underdogs won ${pct(st.rate)}, the market expected ${pct(st.expected)}`}>
                          <i className="ud-bars__fill" style={{ width: `${Math.min(100, st.rate * 200)}%` }} />
                          <i className="ud-bars__tick" style={{ left: `${Math.min(100, st.expected * 200)}%` }} />
                        </span>
                        <b>{pct(st.rate)}</b>
                        <em className={st.flatProfit >= 0 ? 'is-up' : 'is-down'}>{money(st.flatProfit)}</em>
                      </li>
                    ))}
                  </ul>
                  <p className="ud-note">The scale runs to 50%: an underdog is, by definition, the side the market gave less than a 50% chance. The right-hand column is $1 on every underdog in that sport.</p>
                </section>
              )}

              <section className="ud-card" aria-label="By size of underdog">
                <h2>By size of underdog <small>{NAME[sport]} · the bigger the underdog, the rarer the win, but the bigger the payout</small></h2>
                <div className="ud-scroll"><table className="ud-table">
                  <thead><tr><th>How big</th><th>Games</th><th>Won</th><th>Market said</th><th>$1 on each</th></tr></thead>
                  <tbody>
                    {bands.map(({ b, t: bt }) => (
                      <tr key={b.key}>
                        <td className="ud-band">{b.label.split(' (')[0]}<small>{b.label.split(' (')[1]?.replace(')', '')}</small></td><td>{bt.n}</td>
                        <td><span className="ud-mini"><i style={{ width: `${bt.rate * 200}%` }} /></span>{pct(bt.rate)}</td>
                        <td>{pct(bt.expected)}</td>
                        <td className={bt.flatProfit >= 0 ? 'is-up' : 'is-down'}>{money(bt.flatProfit)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table></div>
              </section>

              <div className="ud-pair">
                <section className="ud-card ud-aisp" aria-label="AISP on underdogs">
                  <h2>AISP on underdogs <small>its paper bets in these games</small></h2>
                  {a.dog.n + a.fav.n === 0 ? <p className="ud-note">AISP placed no paper bets on these games.</p> : (
                    <div className="ud-aisp__grid">
                      <div><small>Backing the underdog</small><b>{a.dog.w}–{a.dog.l}</b><em className={a.dog.net >= 0 ? 'is-up' : 'is-down'}>{money(a.dog.net)} on ${a.dog.staked.toFixed(0)}</em></div>
                      <div><small>Backing the favourite</small><b>{a.fav.w}–{a.fav.l}</b><em className={a.fav.net >= 0 ? 'is-up' : 'is-down'}>{money(a.fav.net)} on ${a.fav.staked.toFixed(0)}</em></div>
                    </div>
                  )}
                  <p className="ud-note">AISP bets whichever side its model says is underpriced, so it often lands on underdogs. Paper money only.</p>
                </section>
                <section className="ud-card" aria-label="Biggest upsets">
                  <h2>Biggest upsets <small>the longest-priced underdogs that won</small></h2>
                  <ol className="ud-upsets">
                    {top.map((r) => (
                      <li key={`${r.date}${r.score}`} style={{ ['--sc' as string]: COLOR[r.sport] } as CSSProperties}>
                        <span><i>{r.sport}</i><b>{r.dog_team}</b> beat {r.fav_team}</span>
                        <span className="ud-upsets__score">{r.score}</span>
                        <span><b>{pct(r.dog_p, 0)}</b> · {odds(r.dog_best_decimal)}</span>
                        <small>{new Date(`${r.date}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</small>
                      </li>
                    ))}
                  </ol>
                </section>
              </div>
              <p className="ud-method">How this is counted: {data.method} Ties are left out. Research only; nothing here is betting advice.</p>
            </>
          )}
        </main>
      </div>
    </div>
  )
}

function readOut(n: number, diff: number): string {
  if (n < 30) return 'Too few games yet to say much: a handful of upsets swings these numbers a lot.'
  if (Math.abs(diff) < 0.02) return 'Underdogs won about as often as the market said they would. That is what an efficient market looks like.'
  return diff > 0
    ? 'Underdogs won more often than the market priced. Over a small sample that can be luck; watch whether it holds.'
    : 'Favourites won more often than the market priced. Over a small sample that can be luck; watch whether it holds.'
}
