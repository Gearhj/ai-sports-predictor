/**
 * College Football on Games & Forecasts: one row per game with a verified
 * forecast on this date, linking to its game page.
 *
 * Each row says what AISP holds now -- the joint-channel pick, the Ridge
 * expected score, whether the forecast is currently decision-eligible, and the
 * checkpoint's recorded result or its window -- and never implies a price.
 */
import { Link } from 'react-router-dom'
import { useCfbResearch } from '../../hooks/useCfbResearch'
import type { CfbResearchForecast } from '../../types/cfbResearch'

const ET = 'America/New_York'

function etToday(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: ET })
}

function kick(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', { timeZone: ET, hour: 'numeric', minute: '2-digit' }) + ' ET'
}

function state(f: CfbResearchForecast): string {
  const p = f.pipeline
  if (!p) return 'Forecast held'
  if (p.final) return `Final ${p.final.away_score}–${p.final.home_score}`
  if (p.decision) {
    return p.decision.position ? 'Position recorded' : `Checkpoint: ${(p.decision.reason ?? p.decision.recommendation).replace(/_/g, ' ').toLowerCase()}`
  }
  if (p.discovery.state === 'CURRENT') return 'Eligible · no price held'
  return p.discovery.state.replace(/_/g, ' ').toLowerCase()
}

export function CfbSlateSection({ date }: { date?: string | null }) {
  const day = date || etToday()
  const { data, loading, error } = useCfbResearch(day)
  const rows = data?.forecasts ?? []
  return (
    <section aria-label="College Football games" style={{ display: 'grid', gap: 8 }} data-testid="cfb-slate">
      <header>
        <h2 className="bx-h2" style={{ margin: 0 }}>College Football</h2>
        <p className="bx-dim" style={{ margin: '2px 0 0' }}>
          Verified prospective forecasts · joint-simulation pick, Ridge expected score · virtual paper policy
          checkpoint 30–15 minutes before kickoff · no College Football price is being collected this month
        </p>
      </header>
      {loading && !data && <p className="bx-empty" role="status">Loading College Football…</p>}
      {error && !data && <p className="bx-empty" role="alert">College Football forecasts are unavailable.</p>}
      {data && rows.length === 0 && <p className="bx-empty">{data.empty_state}</p>}
      {rows.length > 0 && (
        <div role="table" className="cfbslate">
          {rows.map((f) => {
            const home = f.game.home_team
            const away = f.game.away_team
            const joint = f.win_probabilities.find((w) => w.method === 'joint')
            const homeLead = (joint?.home ?? 0) >= 0.5
            return (
              <div role="row" key={f.forecast_id} className="cfbslate__row">
                <span role="cell" className="bx-num">{kick(f.game.kickoff_utc)}</span>
                <span role="cell"><Link to={`/college-football/games/${f.game.source_game_id}`}>{away} @ {home}</Link></span>
                <span role="cell">
                  <b>{homeLead ? home : away} {(((homeLead ? joint?.home : joint?.away) ?? 0) * 100).toFixed(0)}%</b>
                  <small> joint · {f.expected.away_points.toFixed(1)}–{f.expected.home_points.toFixed(1)} Ridge</small>
                </span>
                <span role="cell" className="bx-dim">{state(f)}</span>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
