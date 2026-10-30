/**
 * Day-before opening moneylines (owner 10-30: "collect a day-before opening price for every game ... and show it
 * next to the current price"). One US-book request per sport per day, stored apart from the betting prices on the
 * server (app/services/opening_lines.py); display only, never a decision input. Fetched once per page load and
 * shared by every row.
 */
import { useEffect, useState } from 'react'

export interface Opener {
  best_away: { american: number; book: string }
  best_home: { american: number; book: string }
  no_vig_home: number
  books: number
  received_at_utc: string
}

let pending: Promise<Record<string, Opener>> | null = null

function load(): Promise<Record<string, Opener>> {
  pending ??= fetch('/preview-api/opening-lines')
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => (d?.openers ?? {}) as Record<string, Opener>)
    .catch(() => ({}))
  return pending
}

export function useOpeners(): Record<string, Opener> {
  const [openers, setOpeners] = useState<Record<string, Opener>>({})
  useEffect(() => {
    let live = true
    load().then((o) => { if (live) setOpeners(o) })
    return () => { live = false }
  }, [])
  return openers
}
