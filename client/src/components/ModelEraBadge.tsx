/**
 * Which engine controls prospective MLB decisions, and from exactly when.
 *
 * WHY ARMED AND ACTIVE LOOK DIFFERENT
 * -----------------------------------
 * Before the boundary nothing has changed: the funded experiment is still
 * deciding on Gen1 forecasts, and a badge that already said "V18" would be a
 * lie with a countdown attached. So ARMED reads as a scheduled change with the
 * time remaining, and only ACTIVE claims the engine is in control.
 *
 * WHY THE REVIEW NOTICE IS NOT AN ASTERISK
 * ----------------------------------------
 * V18's historical validation is under review, and activation does not repair
 * that. The notice therefore sits in the same block as the promotion rather
 * than in a footnote, because a reader who sees "champion" and misses the
 * condition has been misled by the layout.
 */
import { useState } from 'react'
import { ChevronDown, CircleAlert, Clock } from 'lucide-react'

export interface ModelEraData {
  data_mode?: 'live'
  present: boolean
  state: 'ARMED' | 'ACTIVE' | 'NOT_IMPORTED'
  sport?: string
  engine_id?: string
  model?: {
    name: string
    version: string
    artifact_sha256: string
    champion_manifest_sha256: string
    parent_bundle_id: string | null
    training_run_id: string | null
  }
  handoff_sha256?: string
  model_era_id?: string
  effective_at_utc?: string
  starts_in_seconds?: number
  historical_validation_under_review?: boolean
  historical_integrity_note?: string | null
  executes_in_aisp?: boolean
  real_money?: boolean
  gen1_fallback_allowed?: boolean
  minimum_adjusted_edge?: number
  coverage?: { event_date: string; forecasts: number; games: number; ready: number }
  research_only_note?: string
}

const countdown = (seconds: number) => {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.round((seconds % 3600) / 60)
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`
}

const short = (digest?: string) => (digest ? `${digest.slice(0, 12)}…` : '—')

export function ModelEraBadge({ data }: { data: ModelEraData }) {
  const [open, setOpen] = useState(false)
  if (!data.present) {
    return (
      <p className="bx-muted">
        No model-era authority is imported. MLB decisions run on the Gen1 engine.
      </p>
    )
  }
  const active = data.state === 'ACTIVE'
  const when = data.effective_at_utc
    ? new Date(data.effective_at_utc).toLocaleString('en-US', {
        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
      })
    : '—'

  return (
    <div className="mera">
      <div className="mera__head">
        <span className={`mera__state is-${active ? 'active' : 'armed'}`}>
          {active ? 'Active' : <><Clock size={14} aria-hidden="true" /> Armed</>}
        </span>
        <b className="mera__engine">{data.model?.name ?? data.engine_id}</b>
        <span className="mera__era">{data.model?.version}</span>
      </div>

      <p className="mera__line">
        {active
          ? <>Controlling prospective MLB decisions since <b>{when}</b>.</>
          : <>Takes control at <b>{when}</b>
              {data.starts_in_seconds != null && <> — in {countdown(data.starts_in_seconds)}</>}.
              Until then the funded experiment keeps deciding on Gen1 forecasts.</>}
      </p>

      {data.historical_validation_under_review && (
        <p className="mera__review">
          <CircleAlert size={15} aria-hidden="true" />
          <span>
            Conditional champion: its historical validation is under review, and
            activating it prospectively does not repair that history.
          </span>
        </p>
      )}

      <dl className="mera__facts">
        <div><dt>Today&rsquo;s V18 forecasts</dt>
          <dd>{data.coverage ? `${data.coverage.ready} ready of ${data.coverage.forecasts}` : '—'}</dd></div>
        <div><dt>Minimum adjusted edge</dt>
          <dd>{data.minimum_adjusted_edge != null
            ? `${(data.minimum_adjusted_edge * 100).toFixed(2)}% (unchanged)` : '—'}</dd></div>
        <div><dt>Gen1 fallback</dt>
          <dd>{data.gen1_fallback_allowed ? 'allowed' : 'never — a game without a V18 forecast skips'}</dd></div>
        <div><dt>Runs the model in AISP</dt>
          <dd>{data.executes_in_aisp ? 'yes' : 'no — saved forecasts only'}</dd></div>
      </dl>

      <button type="button" className="mera__more" aria-expanded={open}
        onClick={() => setOpen(!open)}>
        Exact identity
        <ChevronDown size={16} aria-hidden="true"
          style={{ transform: open ? 'rotate(180deg)' : undefined }} />
      </button>
      {open && (
        <dl className="mera__identity">
          <div><dt>Model era</dt><dd><code>{data.model_era_id}</code></dd></div>
          <div><dt>Estimator</dt><dd><code>{short(data.model?.artifact_sha256)}</code></dd></div>
          <div><dt>Champion manifest</dt><dd><code>{short(data.model?.champion_manifest_sha256)}</code></dd></div>
          <div><dt>Authority handoff</dt><dd><code>{short(data.handoff_sha256)}</code></dd></div>
          <div><dt>Parent bundle</dt><dd><code>{data.model?.parent_bundle_id ?? '—'}</code></dd></div>
          {data.historical_integrity_note && (
            <div className="mera__note"><dt>Disclosed limitation</dt>
              <dd>{data.historical_integrity_note}</dd></div>
          )}
        </dl>
      )}
    </div>
  )
}
