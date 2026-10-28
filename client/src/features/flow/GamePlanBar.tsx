/**
 * Tonight's Game Plan bar: RETIRED 2026-10-26 (owner: "we can get rid of the Tonight's Game Plan section"). The
 * numbered left menu (1 Home ... 10 MMQB) now carries the recommended order. Kept as a no-op so every page that
 * still renders <GamePlanBar /> keeps compiling; it no longer loads the slate and Mission Control on every page.
 */
export type PlanStep = 'scan' | 'edges' | 'ready' | 'outcomes' | 'picks' | 'track'

export function GamePlanBar(_props: { current: PlanStep }) {
  return null
}
