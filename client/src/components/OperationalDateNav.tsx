import { ChevronLeft, ChevronRight } from 'lucide-react'
import { operationalToday, shiftDate } from '../services/operationalDate'

/**
 * Previous / Today / Next / picker navigation for an operational date.
 * The owning page persists the selection in the URL (?date=YYYY-MM-DD).
 */
export function OperationalDateNav({
  date,
  onChange,
  ariaLabel = 'Operating date',
}: {
  date: string
  onChange: (next: string) => void
  ariaLabel?: string
}) {
  const today = operationalToday()
  return (
    <div className="date-nav" role="group" aria-label={ariaLabel}>
      <button type="button" className="date-nav__step" aria-label="Previous day" onClick={() => onChange(shiftDate(date, -1))}>
        <ChevronLeft size={15} />
      </button>
      <input
        type="date"
        aria-label={`${ariaLabel} picker`}
        value={date}
        onChange={(event) => event.target.value && onChange(event.target.value)}
      />
      <button type="button" className="date-nav__step" aria-label="Next day" onClick={() => onChange(shiftDate(date, 1))}>
        <ChevronRight size={15} />
      </button>
      <button
        type="button"
        className="date-nav__today"
        onClick={() => onChange(today)}
        disabled={date === today}
      >
        Today
      </button>
    </div>
  )
}

export default OperationalDateNav
