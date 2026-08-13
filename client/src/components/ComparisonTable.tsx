import type { ComparisonMetric, TeamSummary } from '../types/domain'

export function ComparisonTable({
  away,
  home,
  rows,
}: {
  away: TeamSummary
  home: TeamSummary
  rows: ComparisonMetric[]
}) {
  return (
    <div className="comparison-table">
      <div className="comparison-table__header">
        <strong>{away.abbreviation}</strong><span>Metric</span><strong>{home.abbreviation}</strong>
      </div>
      {rows.map((row) => (
        <div className="comparison-table__row" key={row.metric} title={row.context}>
          <strong className={row.preferred === 'away' ? 'is-preferred' : ''}>{row.away}</strong>
          <span>{row.metric}{row.context && <small>{row.context}</small>}</span>
          <strong className={row.preferred === 'home' ? 'is-preferred' : ''}>{row.home}</strong>
        </div>
      ))}
    </div>
  )
}
