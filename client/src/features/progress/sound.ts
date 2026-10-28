/**
 * MOMENTS + SOUND (plan page list): an optional sound pack for the site's big moments, synthesized in the browser
 * with Web Audio (no files, no cost). OFF by default; the speaker on the player bar turns it on, remembered in this
 * browser. Browsers only allow sound after a click, which that toggle provides.
 */
import { useEffect, useState } from 'react'

const KEY = 'aisp-sound-on'
const EVENT = 'aisp-sound'
let ctx: AudioContext | null = null

export function soundOn(): boolean { try { return localStorage.getItem(KEY) === '1' } catch { return false } }
export function setSound(on: boolean): void {
  try { localStorage.setItem(KEY, on ? '1' : '0') } catch { /* private window */ }
  if (on) { audio()?.resume().catch(() => undefined); chime() }
  window.dispatchEvent(new CustomEvent(EVENT))
}
export function useSoundOn(): boolean {
  const [on, set] = useState(soundOn)
  useEffect(() => { const f = () => set(soundOn()); window.addEventListener(EVENT, f); window.addEventListener('storage', f); return () => { window.removeEventListener(EVENT, f); window.removeEventListener('storage', f) } }, [])
  return on
}

function audio(): AudioContext | null {
  if (typeof window === 'undefined') return null
  try { ctx = ctx ?? new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)() } catch { return null }
  return ctx
}
/** One enveloped oscillator note. */
function note(freq: number, at: number, dur: number, type: OscillatorType = 'sine', gain = 0.12, glideTo?: number): void {
  const a = audio(); if (!a) return
  const o = a.createOscillator(), g = a.createGain()
  o.type = type; o.frequency.setValueAtTime(freq, a.currentTime + at)
  if (glideTo) o.frequency.exponentialRampToValueAtTime(glideTo, a.currentTime + at + dur)
  g.gain.setValueAtTime(0.0001, a.currentTime + at)
  g.gain.exponentialRampToValueAtTime(gain, a.currentTime + at + 0.02)
  g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + at + dur)
  o.connect(g).connect(a.destination); o.start(a.currentTime + at); o.stop(a.currentTime + at + dur + 0.05)
}
const play = (fn: () => void) => { if (soundOn() && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) fn() }

/** Trophy unlocked: a bright two-note chime. */
export const chime = () => play(() => { note(1318.5, 0, 0.35, 'triangle', 0.1); note(1975.5, 0.09, 0.5, 'triangle', 0.08) })
/** Level up: a rising major arpeggio. */
export const levelUp = () => play(() => [523.3, 659.3, 784, 1046.5].forEach((f, i) => note(f, i * 0.09, 0.32, 'square', 0.05)))
/** A hit lands (right call): a low thump and a shimmer. */
export const hit = () => play(() => { note(140, 0, 0.25, 'sine', 0.25, 60); note(880, 0.05, 0.4, 'triangle', 0.06, 1760) })
/** A miss: a short falling buzz. */
export const miss = () => play(() => note(220, 0, 0.35, 'sawtooth', 0.05, 110))
/** A score in a game you have skin in: hockey gets the goal horn, everything else a crowd swell. */
export const horn = (sport: string) => play(() => {
  if (sport === 'nhl' || sport === 'NHL') [196, 246.9, 293.7].forEach((f) => note(f, 0, 1.1, 'sawtooth', 0.045))
  else { note(392, 0, 0.5, 'triangle', 0.06, 523.3); note(523.3, 0.12, 0.5, 'triangle', 0.05, 659.3) }
})
