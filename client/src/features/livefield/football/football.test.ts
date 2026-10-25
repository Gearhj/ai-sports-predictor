// @ts-expect-error node:fs types are not part of the browser tsconfig; vitest runs in node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { bucketIndex, predict, type Grid } from './playcall'
import { parsePlay } from './playParser'
import { buildScene, sample } from './scene'

describe('football play parser', () => {
  it('reads a deep pass', () => {
    const p = parsePlay('Pass Reception', '(Shotgun) T.Lawrence pass deep left to B.Strange to HST 46 for 24 yards (C.Bullock).', 24)
    expect(p).toMatchObject({ kind: 'pass', formation: 'shotgun', passer: 'T.Lawrence', target: 'B.Strange', depth: 'deep', side: 'left', yards: 24 })
  })
  it('reads an incompletion, run, sack, interception, touchdown and kicks', () => {
    expect(parsePlay('Pass Incompletion', '(Shotgun) T.Lawrence pass incomplete short right to Bri.Thomas.', 0)).toMatchObject({ kind: 'incomplete', side: 'right', depth: 'short', target: 'Bri.Thomas' })
    expect(parsePlay('Rush', '(Shotgun) B.Tuten right guard to HST 43 for 3 yards (D.Hunter; R.Blankenship).', 3)).toMatchObject({ kind: 'rush', rusher: 'B.Tuten', gap: 'right guard', yards: 3 })
    expect(parsePlay('Sack', 'C.Stroud sacked at HST 25 for -7 yards (J.Smith).', -7)).toMatchObject({ kind: 'sack', passer: 'C.Stroud' })
    expect(parsePlay('Pass Interception Return', '(Shotgun) T.Lawrence pass short left intended for J.Meyers INTERCEPTED by C.Bullock at HST -6. Touchback.', 0)).toMatchObject({ kind: 'interception', target: 'J.Meyers' })
    expect(parsePlay('Passing Touchdown', '(Shotgun) C.Stroud pass short right to F.Moreau for 6 yards, TOUCHDOWN. K.Fairbairn extra point is GOOD.', 6)).toMatchObject({ kind: 'pass', touchdown: true })
    expect(parsePlay('Field Goal Good', 'K.Fairbairn 25 yard field goal is GOOD, Center-A.Brinkman.', 25)).toMatchObject({ kind: 'fieldgoal', good: true })
    expect(parsePlay('Punt', 'L.Cooke punts 36 yards to HST 6, fair catch by J.Noel.', 0).kind).toBe('punt')
    expect(parsePlay('Official Timeout', 'Official Timeout at 10:37.', 0).kind).toBe('nonplay')
    expect(parsePlay('Penalty', 'B.Tuten right end for 3 yards, TOUCHDOWN NULLIFIED by Penalty. PENALTY on JAX-B.Strange, Offensive Holding, 10 yards, enforced at HST 3 - No Play.', -10).kind).toBe('penalty')
  })
  it('reads college play-by-play with clocks and jersey numbers', () => {
    expect(parsePlay('Pass Reception', '(00:11) Shotgun #10 J.Hoover pass complete short right to #80 C.Becker caught at U-M37, for 15 yards to the U-M37', 15)).toMatchObject({ kind: 'pass', formation: 'shotgun', passer: 'J.Hoover', target: 'C.Becker', depth: 'short', side: 'right' })
    expect(parsePlay('Rush', '(00:01) #24 B.Kuzdzal rush left for 9 yards gain to the U-M47 (#4 A.Harris)', 9)).toMatchObject({ kind: 'rush', rusher: 'B.Kuzdzal', gap: 'left', yards: 9 })
    expect(parsePlay('Field Goal Missed', '(00:04) #35 P.McAteer field goal attempt from 56 yards NO GOOD (H: #93 Q.Warren)', 0)).toMatchObject({ kind: 'fieldgoal', good: false })
  })
  it('reads no-huddle shotgun college text', () => {
    expect(parsePlay('Rush', '(11:21) No Huddle-Shotgun #5 K.Raphael rush middle for 15 yards to the CAL41 (#30 C.Sibley)', 15)).toMatchObject({ kind: 'rush', rusher: 'K.Raphael', formation: 'shotgun', gap: 'middle' })
  })
  it('reads college football wording', () => {
    expect(parsePlay('Pass Reception', "Jaxson Dart pass complete to Ja'Tavion Sanders for 15 yds to the Duke 30", 15)).toMatchObject({ kind: 'pass', passer: 'Jaxson Dart', target: "Ja'Tavion Sanders" })
  })
})

describe('football scene', () => {
  const cases: Array<[string, string, number | null, number | null]> = [
    ['Pass Reception', '(Shotgun) T.Lawrence pass deep left to B.Strange to HST 46 for 24 yards (C.Bullock).', 24, 4],
    ['Pass Incompletion', '(Shotgun) T.Lawrence pass incomplete short right to Bri.Thomas.', 0, null],
    ['Rush', '(Shotgun) B.Tuten right guard to HST 43 for 3 yards (D.Hunter).', 3, null],
    ['Sack', 'C.Stroud sacked at HST 25 for -7 yards (J.Smith).', -7, null],
    ['Pass Interception Return', '(Shotgun) T.Lawrence pass short left intended for J.Meyers INTERCEPTED by C.Bullock at HST -6.', 0, null],
    ['Punt', 'L.Cooke punts 36 yards to HST 6.', 36, null],
    ['Field Goal Good', 'K.Fairbairn 25 yard field goal is GOOD.', 25, null],
    ['Kickoff', 'C.Little kicks 65 yards from JAX 35 to end zone, Touchback.', 0, null],
    ['Penalty', 'PENALTY on JAX, Offensive Holding, 10 yards, No Play.', -10, null],
  ]
  it.each(cases)('builds finite 22-player timelines for %s', (type, text, yards, yac) => {
    const scene = buildScene({ parsed: parsePlay(type, text, yards), yac })
    expect(scene.actors).toHaveLength(22)
    expect(scene.duration).toBeGreaterThan(2)
    for (const a of scene.actors) {
      for (let t = 0; t <= scene.duration; t += 0.25) {
        const p = sample(a.frames, t)
        expect(Number.isFinite(p.x) && Number.isFinite(p.d)).toBe(true)
      }
    }
    for (let t = 0; t <= scene.duration; t += 0.25) { const b = sample(scene.ball, t); expect(Number.isFinite(b.x) && Number.isFinite(b.d) && Number.isFinite(b.z)).toBe(true) }
  })
  it('puts the catch where the play says', () => {
    const scene = buildScene({ parsed: parsePlay('Pass Reception', '(Shotgun) T.Lawrence pass deep left to B.Strange to HST 46 for 24 yards.', 24), yac: 4 })
    const end = scene.markers[scene.markers.length - 1]
    expect(end.d).toBeCloseTo(24, 0)
    expect(end.text).toBe('+24')
  })
})

describe('next-play lookup grid', () => {
  const buf = (readFileSync as (p: string) => Uint8Array & { buffer: ArrayBuffer; byteOffset: number; byteLength: number })('public/playcall/playcall_grid.bin')
  const grid: Grid = { data: new Uint16Array(buf.buffer, buf.byteOffset, buf.byteLength / 2), meta: { trainedOn: '', validation: { stage1_logloss_prior: 0, stage1_logloss_grid: 0, pass_vs_run_accuracy_grid: 0, pass_vs_run_always_majority: 0, test_plays: 0 } } }
  // Golden values printed by train_playcall.py's own bucket() for the same situations.
  it.each([
    [{ down: 3, distance: 2, yardsToEndzone: 38, scoreDiff: -4, period: 4, secondsRemaining: 600 }, 13772, [1774, 1097, 1780, 426, 124, 499, 1463, 1410, 1427, 0, 1, 0]],
    [{ down: 1, distance: 10, yardsToEndzone: 75, scoreDiff: 0, period: 1, secondsRemaining: 3000 }, 4568, [1456, 733, 1760, 299, 168, 295, 2062, 1188, 2038, 0, 0, 0]],
    [{ down: 4, distance: 1, yardsToEndzone: 55, scoreDiff: -7, period: 4, secondsRemaining: 100 }, 19462, [2518, 1639, 2544, 573, 321, 711, 392, 824, 371, 107, 0, 0]],
    [{ down: 2, distance: 7, yardsToEndzone: 95, scoreDiff: 3, period: 2, secondsRemaining: 100 }, 10266, [1552, 962, 1578, 231, 120, 340, 1582, 1754, 1810, 0, 0, 71]],
  ])('matches the trained table at %j', (state, index, row) => {
    expect(bucketIndex(state)).toBe(index)
    expect(Array.from(grid.data.slice(index * 12, index * 12 + 12))).toEqual(row)
    const p = predict(grid, state)
    expect(p.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 6)
  })

  it('keeps a touchdown when a penalty is logged after it (on the try)', () => {
    const p = parsePlay('Passing Touchdown', '(13:51) Shotgun #5 T.Dickens pass complete deep left to #1 D.Rogers caught at HAW01, for 25 yards to the HAW00 TOUCHDOWN, clock 13:46, 1ST DOWN PENALTY NIU Delay Of Game 5 yards from HAW03 to HAW08. NO PLAY #33 J.Schlen', 25)
    expect(p.touchdown).toBe(true)
    expect(p.kind).toBe('pass')
  })
})
