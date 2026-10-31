/**
 * The site's two views (owner 10-27): FULL (everything, for the data-geek sports nerd) and FOCUS (a stripped-down,
 * phone-first view of the reader's games and wagers). Kept in this browser; every page reads it through useMode().
 */
import { useEffect, useState } from 'react'

export type ViewMode = 'full' | 'focus'
const KEY = 'aisp-view-mode'
const EVENT = 'aisp-view-mode'

/** A stored choice wins; with none, a phone opens in Focus (the mobile version) and a tablet or desktop in Full. */
export function readMode(): ViewMode {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'focus' || v === 'full') return v
  } catch { /* storage unavailable: fall through to the device default */ }
  return typeof window !== 'undefined' && window.matchMedia?.('(max-width: 700px)').matches ? 'focus' : 'full'
}

export function setMode(mode: ViewMode): void {
  try { localStorage.setItem(KEY, mode) } catch { /* private window: the toggle still works for this page */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT, { detail: mode }))
}

export function useMode(): ViewMode {
  const [mode, set] = useState<ViewMode>(readMode)
  useEffect(() => {
    const on = (e: Event) => set(((e as CustomEvent).detail as ViewMode) ?? readMode())
    const storage = (e: StorageEvent) => { if (e.key === KEY) set(readMode()) }
    window.addEventListener(EVENT, on)
    window.addEventListener('storage', storage)
    return () => { window.removeEventListener(EVENT, on); window.removeEventListener('storage', storage) }
  }, [])
  return mode
}

/** Paths that stay as they are in Focus mode (sign-in, account, admin, the TV wall). UFC Fight Night (10-30):
 *  phones default to Focus, and the live fight card has no Focus screen. */
export const FOCUS_PASSTHROUGH = /^\/(login|account|admin|encore|welcome|underdogs|mma)(\/|$)/
