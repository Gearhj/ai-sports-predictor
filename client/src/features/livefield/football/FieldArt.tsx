/**
 * The stadium: a broadcast-camera field drawn in SVG. In the All-22 (side) view the whole field is in frame,
 * end zone to end zone, from a high camera on the near sideline: crowd and light banks behind the far
 * sideline, LED boards along it, striped grass with texture, yard lines, numbers and hashes, painted end
 * zones with the team names running across them, glowing line-of-scrimmage and first-down lines, and a
 * vignette. Everything is projected through the same camera as the players.
 */
import { project, VB_H, VB_H_SIDE, VB_W, type Camera } from './scene'

export const HALF_W = 26.67
const poly = (pts: Array<{ px: number; py: number }>) => pts.map((p) => `${p.px.toFixed(1)},${p.py.toFixed(1)}`).join(' ')
/** Deterministic pseudo-random in [0, 1) so the crowd lights never flicker between renders. */
const rnd = (i: number, k: number) => { const v = Math.sin(i * 12.9898 + k * 78.233) * 43758.5453; return v - Math.floor(v) }

interface Props { los: number; toGo: number | null; offColor: string; defColor: string; offName: string; defName: string; cam: Camera; uid: string }

export function FieldArt({ los, toGo, offColor, defColor, offName, defName, cam, uid }: Props) {
  const side = cam.kind === 'side'
  const H = side ? VB_H_SIDE : VB_H
  const dMin = side ? -los - 10 : (255 * cam.h) / (VB_H - 96) - cam.back + 0.2
  const dMax = side ? 110 - los : 54
  const P = (x: number, d: number, z = 0) => project(x, d, z, cam)
  const bands: JSX.Element[] = []
  const lines: JSX.Element[] = []
  const nums: JSX.Element[] = []
  const hashes: JSX.Element[] = []
  const first = Math.floor((los + dMin) / 5) * 5
  const last = Math.ceil((los + dMax) / 5) * 5
  for (let a = Math.max(-10, first); a < Math.min(110, last); a += 5) {
    const d0 = Math.max(dMin, a - los)
    const d1 = Math.min(dMax, a + 5 - los)
    if (d1 <= d0) continue
    const endzone = a < 0 || a >= 100
    const fill = endzone ? (a < 0 ? offColor : defColor) : Math.floor(a / 5) % 2 === 0 ? `url(#${uid}-g1)` : `url(#${uid}-g2)`
    bands.push(<polygon key={`b${a}`} points={poly([P(-HALF_W, d0), P(HALF_W, d0), P(HALF_W, d1), P(-HALF_W, d1)])} fill={fill} opacity={endzone ? 0.9 : 1} />)
  }
  for (let a = Math.max(0, first); a <= Math.min(100, last); a += 5) {
    const d = a - los
    if (d < dMin || d > dMax) continue
    const l = P(-HALF_W, d)
    const r = P(HALF_W, d)
    const goal = a === 0 || a === 100
    lines.push(<line key={`l${a}`} x1={l.px} y1={l.py} x2={r.px} y2={r.py} stroke="#f6f8f6" strokeOpacity={goal ? 0.95 : a % 10 === 0 ? 0.8 : 0.5} strokeWidth={Math.max(1, l.s * (goal ? 0.3 : 0.13))} />)
    for (const hx of [-3.1, 3.1, -HALF_W + 0.6, HALF_W - 0.6]) {
      const h0 = P(hx, d - 0.35)
      const h1 = P(hx, d + 0.35)
      hashes.push(<line key={`h${a}${hx}`} x1={h0.px} y1={h0.py} x2={h1.px} y2={h1.py} stroke="#f6f8f6" strokeOpacity="0.55" strokeWidth={Math.max(1, h0.s * 0.16)} />)
    }
    if (a % 10 === 0 && a >= 10 && a <= 90) {
      const n = Math.min(a, 100 - a)
      for (const sx of [-17, 17]) {
        const q = P(sx, d)
        nums.push(<text key={`n${a}${sx}`} x={q.px} y={q.py} textAnchor="middle" dominantBaseline="middle" fontSize={Math.max(9, q.s * (side ? 2.6 : 3.4))} fill="#f6f8f6" fillOpacity={0.62} fontWeight={800} fontFamily="Oswald, Impact, 'Arial Narrow', sans-serif">{n}</text>)
        if (!side) continue
        const arrow = sx < 0 ? P(sx - 2.2, d - (a < 50 ? 1.4 : -1.4)) : P(sx + 2.2, d - (a < 50 ? 1.4 : -1.4))
        if (a !== 50) nums.push(<circle key={`a${a}${sx}`} cx={arrow.px} cy={arrow.py} r={Math.max(0.8, q.s * 0.14)} fill="#f6f8f6" fillOpacity="0.5" />)
      }
    }
    // single-yard hashes between the fives
    if (side) for (let y = 1; y < 5; y += 1) { const dd = d + y; if (dd > dMax) break; for (const hx of [-3.1, 3.1]) { const h0 = P(hx, dd - 0.25); const h1 = P(hx, dd + 0.25); hashes.push(<line key={`hh${a}${y}${hx}`} x1={h0.px} y1={h0.py} x2={h1.px} y2={h1.py} stroke="#f6f8f6" strokeOpacity="0.4" strokeWidth={Math.max(0.8, h0.s * 0.12)} />) } }
  }
  // end zone names: across the end zone (parallel to the goal line), like real paint
  const zoneText = (a0: number, color: string, name: string, key: string) => {
    const dc = a0 - los + 5
    if (dc < dMin || dc > dMax) return null
    const c = P(side ? 3 : 0, dc)
    const text = name.toUpperCase()
    if (side) {
      const n0 = P(-HALF_W + 9, dc)
      const n1 = P(HALF_W - 3, dc)
      const span = Math.hypot(n1.px - n0.px, n1.py - n0.py)
      const base = (Math.atan2(n1.py - n0.py, n1.px - n0.px) * 180) / Math.PI
      const angle = a0 < 0 ? base : base + 180
      const size = Math.max(14, Math.min(c.s * 7.5, (0.9 * span) / Math.max(4, text.length * 0.64)))
      return <text key={key} x={c.px} y={c.py} textAnchor="middle" dominantBaseline="middle" fontSize={size} fill="#fff" fillOpacity="0.92" fontWeight={900} letterSpacing="0.1em" fontFamily="Oswald, Impact, 'Arial Narrow', sans-serif" transform={`rotate(${angle.toFixed(1)} ${c.px.toFixed(1)} ${c.py.toFixed(1)})`} style={{ paintOrder: 'stroke', stroke: color, strokeWidth: 2 }}>{text}</text>
    }
    const angle = a0 < 0 ? 0 : 180
    const size = Math.max(12, Math.min(c.s * 4.6, (9.2 * c.s) / Math.max(4, text.length * 0.58)))
    return <text key={key} x={c.px} y={c.py} textAnchor="middle" dominantBaseline="middle" fontSize={size} fill="#fff" fillOpacity="0.9" fontWeight={900} letterSpacing="0.08em" fontFamily="Oswald, Impact, 'Arial Narrow', sans-serif" transform={`rotate(${angle} ${c.px} ${c.py})`} style={{ paintOrder: 'stroke', stroke: color, strokeWidth: 2 }}>{text}</text>
  }
  const sideline = (sx: number) => poly([P(sx, dMin), P(sx, dMax)])
  const l0 = P(-HALF_W, 0)
  const l1 = P(HALF_W, 0)
  const fd = toGo != null && toGo > 0 ? Math.min(toGo, 100 - los) : null
  const f0 = fd != null ? P(-HALF_W, fd) : null
  const f1 = fd != null ? P(HALF_W, fd) : null
  const far = P(HALF_W, cam.d0 ?? 0).py
  const crowd: JSX.Element[] = []
  if (side) {
    for (let i = 0; i < 160; i += 1) {
      const x = rnd(i, 1) * VB_W
      const y = 4 + rnd(i, 2) * Math.max(10, far - 16)
      const r = 0.7 + rnd(i, 3) * 1.9
      const warm = rnd(i, 4)
      crowd.push(<circle key={`c${i}`} cx={x.toFixed(1)} cy={y.toFixed(1)} r={r.toFixed(1)} fill={warm > 0.82 ? offColor : warm > 0.64 ? defColor : warm > 0.3 ? '#fff4d6' : '#9fb4d8'} opacity={(0.22 + rnd(i, 5) * 0.5).toFixed(2)} />)
    }
  }
  return (
    <svg className="fb-svg" viewBox={`0 0 ${VB_W} ${H}`} preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs>
        <linearGradient id={`${uid}-sky`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#04070f" /><stop offset="1" stopColor="#0c1a2e" /></linearGradient>
        <linearGradient id={`${uid}-g1`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#226f38" /><stop offset="1" stopColor="#2f9450" /></linearGradient>
        <linearGradient id={`${uid}-g2`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#1c6130" /><stop offset="1" stopColor="#288347" /></linearGradient>
        <linearGradient id={`${uid}-stand`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#0b0f1a" /><stop offset="0.55" stopColor="#1a2033" /><stop offset="1" stopColor="#2a3146" /></linearGradient>
        <linearGradient id={`${uid}-led`} x1="0" y1="0" x2="1" y2="0"><stop offset="0" stopColor="#1ec8ff" /><stop offset="0.25" stopColor="#ffffff" /><stop offset="0.5" stopColor={offColor} /><stop offset="0.75" stopColor="#ffffff" /><stop offset="1" stopColor={defColor} /></linearGradient>
        <radialGradient id={`${uid}-vig`} cx="50%" cy="60%" r="78%"><stop offset="55%" stopColor="#000" stopOpacity="0" /><stop offset="100%" stopColor="#000" stopOpacity="0.5" /></radialGradient>
        <radialGradient id={`${uid}-flare`} cx="50%" cy="50%" r="50%"><stop offset="0" stopColor="#fff8e0" stopOpacity="0.95" /><stop offset="30%" stopColor="#ffe9a8" stopOpacity="0.35" /><stop offset="100%" stopColor="#ffe9a8" stopOpacity="0" /></radialGradient>
        <filter id={`${uid}-grain`} x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="3" /><feColorMatrix type="saturate" values="0" /><feComponentTransfer><feFuncA type="linear" slope="0.16" /></feComponentTransfer></filter>
        <filter id={`${uid}-crowd`} x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="0.35 0.9" numOctaves="3" seed="7" /><feColorMatrix type="matrix" values="0 0 0 0 0.42  0 0 0 0 0.36  0 0 0 0 0.4  0 0 0 0.85 0" /></filter>
        <filter id={`${uid}-glow`}><feGaussianBlur stdDeviation="2.2" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        <filter id={`${uid}-soft`}><feGaussianBlur stdDeviation="3" /></filter>
      </defs>
      <rect width={VB_W} height={H} fill={`url(#${uid}-sky)`} />
      {side ? (
        <>
          {/* the bowl: crowd behind the far sideline, light banks, LED boards */}
          <rect x="0" y="0" width={VB_W} height={Math.max(1, far - 5)} fill={`url(#${uid}-stand)`} />
          <rect x="0" y="0" width={VB_W} height={Math.max(1, far - 5)} filter={`url(#${uid}-crowd)`} opacity="0.55" />
          {crowd}
          {[70, 250, 430, 610, 790, 950].map((x) => <g key={x}><circle cx={x} cy={2} r="46" fill={`url(#${uid}-flare)`} /><rect x={x - 30} y={0} width="60" height="5" rx="2" fill="#dfe8ff" opacity="0.9" /></g>)}
          <rect x="0" y={far - 9} width={VB_W} height="8" fill={`url(#${uid}-led)`} opacity="0.95" />
          <rect x="0" y={far - 9} width={VB_W} height="8" fill={`url(#${uid}-led)`} opacity="0.6" filter={`url(#${uid}-soft)`} />
          <rect x="0" y={far - 1.5} width={VB_W} height="3" fill="#0a1220" />
        </>
      ) : null}
      {side ? <polygon points={poly([P(-HALF_W - 3, dMin - 9), P(HALF_W + 1.5, dMin - 9), P(HALF_W + 1.5, dMax + 9), P(-HALF_W - 3, dMax + 9)])} fill="#17301d" /> : null}
      {bands}
      <rect x="0" y="0" width={VB_W} height={H} filter={`url(#${uid}-grain)`} opacity="0.5" style={{ mixBlendMode: 'multiply' }} />
      {lines}
      {hashes}
      <polyline points={sideline(-HALF_W)} fill="none" stroke="#fff" strokeWidth={side ? 3 : 4} strokeOpacity="0.95" />
      <polyline points={sideline(HALF_W)} fill="none" stroke="#fff" strokeWidth={side ? 2 : 3} strokeOpacity="0.9" />
      {nums}
      {zoneText(-10, offColor, offName, 'ez-off')}
      {zoneText(100, defColor, defName, 'ez-def')}
      {f0 && f1 ? <line x1={f0.px} y1={f0.py} x2={f1.px} y2={f1.py} stroke="#ffd23f" strokeWidth={Math.max(2.5, f0.s * 0.3)} strokeOpacity="0.95" filter={`url(#${uid}-glow)`} /> : null}
      <line x1={l0.px} y1={l0.py} x2={l1.px} y2={l1.py} stroke="#4aa8ff" strokeWidth={Math.max(2.5, l0.s * 0.3)} strokeOpacity="0.95" filter={`url(#${uid}-glow)`} />
      <rect width={VB_W} height={H} fill={`url(#${uid}-vig)`} />
    </svg>
  )
}
