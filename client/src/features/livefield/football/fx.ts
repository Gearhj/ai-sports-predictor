/**
 * Stadium sound, synthesised in the browser with the Web Audio API (no audio files, no network):
 * a crowd roar that swells with the size of the play, the snap thud and the whistle.
 * Off by default; the Sound button is the user gesture browsers require before audio may play.
 */
let ctx: AudioContext | null = null

type AudioCtor = typeof AudioContext

export function unlockAudio(): boolean {
  try {
    const Ctor: AudioCtor | undefined = window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioCtor }).webkitAudioContext
    if (!Ctor) return false
    ctx = ctx ?? new Ctor()
    void ctx.resume()
    return true
  } catch {
    return false
  }
}

function noiseBuffer(c: AudioContext, seconds: number): AudioBuffer {
  const buf = c.createBuffer(1, Math.floor(c.sampleRate * seconds), c.sampleRate)
  const d = buf.getChannelData(0)
  let last = 0
  for (let i = 0; i < d.length; i += 1) {
    last = last * 0.96 + (Math.random() * 2 - 1) * 0.5 // brown-ish noise: the rumble of a crowd
    d[i] = last
  }
  return buf
}

/** A crowd roar: filtered noise that swells, with a second band for the cheer. `intensity` is 0 to 1. */
export function roar(intensity: number): void {
  if (!ctx) return
  const c = ctx
  const t = c.currentTime
  const vol = 0.12 + 0.5 * Math.min(1, Math.max(0, intensity))
  const src = c.createBufferSource()
  src.buffer = noiseBuffer(c, 3.4)
  const band = c.createBiquadFilter()
  band.type = 'bandpass'
  band.Q.value = 0.7
  band.frequency.setValueAtTime(350, t)
  band.frequency.exponentialRampToValueAtTime(950, t + 0.9)
  band.frequency.exponentialRampToValueAtTime(520, t + 3.2)
  const gain = c.createGain()
  gain.gain.setValueAtTime(0.0001, t)
  gain.gain.exponentialRampToValueAtTime(vol, t + 0.55)
  gain.gain.exponentialRampToValueAtTime(vol * 0.55, t + 1.6)
  gain.gain.exponentialRampToValueAtTime(0.0001, t + 3.3)
  src.connect(band).connect(gain).connect(c.destination)
  src.start(t)
  src.stop(t + 3.4)
  // The cheer: a brighter band that rides on top for big plays.
  if (intensity > 0.55) {
    const s2 = c.createBufferSource()
    s2.buffer = noiseBuffer(c, 2.6)
    const hp = c.createBiquadFilter()
    hp.type = 'bandpass'
    hp.frequency.value = 2100
    hp.Q.value = 0.9
    const g2 = c.createGain()
    g2.gain.setValueAtTime(0.0001, t + 0.15)
    g2.gain.exponentialRampToValueAtTime(vol * 0.35, t + 0.7)
    g2.gain.exponentialRampToValueAtTime(0.0001, t + 2.4)
    s2.connect(hp).connect(g2).connect(c.destination)
    s2.start(t + 0.15)
    s2.stop(t + 2.7)
  }
}

export function thud(): void {
  if (!ctx) return
  const c = ctx
  const t = c.currentTime
  const o = c.createOscillator()
  const g = c.createGain()
  o.type = 'sine'
  o.frequency.setValueAtTime(130, t)
  o.frequency.exponentialRampToValueAtTime(42, t + 0.18)
  g.gain.setValueAtTime(0.5, t)
  g.gain.exponentialRampToValueAtTime(0.0001, t + 0.3)
  o.connect(g).connect(c.destination)
  o.start(t)
  o.stop(t + 0.32)
}

export function whistle(): void {
  if (!ctx) return
  const c = ctx
  const t = c.currentTime
  const o = c.createOscillator()
  const lfo = c.createOscillator()
  const lg = c.createGain()
  const g = c.createGain()
  o.type = 'triangle'
  o.frequency.value = 2950
  lfo.frequency.value = 38
  lg.gain.value = 90
  lfo.connect(lg).connect(o.frequency)
  g.gain.setValueAtTime(0.0001, t)
  g.gain.exponentialRampToValueAtTime(0.1, t + 0.04)
  g.gain.exponentialRampToValueAtTime(0.0001, t + 0.42)
  o.connect(g).connect(c.destination)
  o.start(t)
  lfo.start(t)
  o.stop(t + 0.45)
  lfo.stop(t + 0.45)
}
