/**
 * AISP as a phone app (owner 10-27: Focus is the mobile version). The manifest and service worker live in public/;
 * this registers the worker, honours ?view=focus|full on launch (the home-screen icon opens Focus), and exposes the
 * browser's install prompt. iPhones have no prompt: the hook says so and the button shows Share -> Add to Home Screen.
 */
import { useEffect, useState } from 'react'
import { setMode } from './mode'

interface InstallEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }> }
let deferred: InstallEvent | null = null
const subs = new Set<() => void>()

export function bootApp(): void {
  if (typeof window === 'undefined') return
  const view = new URLSearchParams(window.location.search).get('view')
  if (view === 'focus' || view === 'full') setMode(view)
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferred = e as InstallEvent; subs.forEach((f) => f()) })
  window.addEventListener('appinstalled', () => { deferred = null; subs.forEach((f) => f()) })
  if (import.meta.env.PROD && 'serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => undefined) })
  }
}

export const isInstalled = () => typeof window !== 'undefined' && (window.matchMedia?.('(display-mode: standalone)').matches || (navigator as Navigator & { standalone?: boolean }).standalone === true)
const isIos = () => typeof navigator !== 'undefined' && /iPhone|iPad|iPod/.test(navigator.userAgent)

export function useInstall(): { state: 'installed' | 'prompt' | 'ios' | 'none'; install: () => Promise<void> } {
  const [, bump] = useState(0)
  useEffect(() => { const f = () => bump((n) => n + 1); subs.add(f); return () => { subs.delete(f) } }, [])
  const state = isInstalled() ? 'installed' : deferred ? 'prompt' : isIos() ? 'ios' : 'none'
  const install = async () => {
    if (!deferred) return
    await deferred.prompt()
    await deferred.userChoice.catch(() => undefined)
    deferred = null
    subs.forEach((f) => f())
  }
  return { state, install }
}
