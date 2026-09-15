/**
 * A disclosure whose content is not mounted -- and so makes no request and
 * loads no chunk -- until it is first opened. Once opened it stays mounted, so
 * closing and reopening does not refetch.
 */
import { Suspense, useState, type ReactNode } from 'react'

export function LazyDetails({ summary, children, defaultOpen = false, className = 'cc-below' }: {
  summary: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  className?: string
}) {
  const [opened, setOpened] = useState(defaultOpen)
  return (
    <details className={className} open={defaultOpen || undefined}
      onToggle={(event) => { if ((event.currentTarget as HTMLDetailsElement).open) setOpened(true) }}>
      <summary>{summary}</summary>
      {opened && <Suspense fallback={<p className="cc-empty" role="status">Loading…</p>}>{children}</Suspense>}
    </details>
  )
}
