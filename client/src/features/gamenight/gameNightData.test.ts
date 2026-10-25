import { describe, expect, it } from 'vitest'
import type { DecisionBoard, Ledger } from '../../services/commandCenter'
import type { MissionControl } from '../mission/missionData'
import type { ScoreboardPayload } from '../../types/scoreboard'
import missionJson from '../../test/gamenight-captured/mission-control-2026-10-18.json'
import ledgerJson from '../../test/gamenight-captured/ledger.json'
import decisionsJson from '../../test/gamenight-captured/decisions-2026-10-18.json'
import scoreboardJson from '../../test/gamenight-captured/scoreboard-snf.json'
import {
  bugState, directorQueue, etHour, focusTargets, heldBets, heldFromLedger, isEarlyHours, ledgerPositions, mergeBoards, observedScore,
  pickSideOf, pooledCell, scoreLabel, stormFromSim,
} from './gameNightData'
import type { SimModel } from '../simulator/simData'

const mission = missionJson as unknown as MissionControl
const ledger = ledgerJson as unknown as Ledger
const decisions = decisionsJson as unknown as DecisionBoard
const scoreboard = scoreboardJson as unknown as ScoreboardPayload
const CAPTURE = Date.parse('2026-10-19T00:26:00Z')

const held = heldBets({ missions: [mission], ledger, decisions: [decisions], scores: scoreboard.games })
const by = (selection: string) => held.find((h) => h.selection === selection)!

describe('held bets joined across Mission Control, the ledger and the decision board', () => {
  it('lists every bet on the board, settled or open, once', () => {
    const positions = new Set(mission.games.map((g) => g.stages?.bet?.position_id).filter(Boolean))
    for (const s of mission.sweating ?? []) positions.add(s.position_id)
    expect(held).toHaveLength(positions.size)
    expect(new Set(held.map((h) => h.positionId)).size).toBe(held.length)
  })

  it('takes settlement from the ledger', () => {
    const ari = by('ARI ML')
    expect(ari.open).toBe(false)
    expect(ari.result).toBe('WIN')
    expect(ari.profit).toBe(30.65)
    expect(ari.side).toBe('AWAY')
  })

  it('reads an observed final with the position open as awaiting settlement, never settled', () => {
    const lv = by('LV ML')
    expect(lv.open).toBe(true)
    expect(bugState(lv, CAPTURE)).toBe('FINAL_AWAITING_SETTLEMENT')
    expect(observedScore(lv)).toMatchObject({ away: 33, home: 30, final: true })
  })

  it('prefers the scoreboard for live state', () => {
    expect(bugState(by('GB ML'), CAPTURE)).toBe('LIVE')
    expect(bugState(by('CBJ ML'), CAPTURE)).toBe('STARTED_NO_LIVE_FEED')
  })

  it('joins the decision-time gap by position id', () => {
    expect(by('CGY ML').gap?.warning).toBe(true)
    expect(by('GB ML').gap?.warning).toBe(false)
  })

  it('tells which side a selection backs', () => {
    expect(pickSideOf('ARI ML', { abbr: 'ARI' }, { abbr: 'LAR' })).toBe('AWAY')
    expect(pickSideOf('Cleveland Guardians', { abbr: 'TB', name: 'Tampa Bay Rays' }, { abbr: 'CLE', name: 'Cleveland Guardians' })).toBe('HOME')
    expect(pickSideOf('AWAY', { abbr: 'A' }, { abbr: 'B' })).toBe('AWAY')
    expect(pickSideOf('', { abbr: 'A' }, { abbr: 'B' })).toBeNull()
  })
})

describe('the director', () => {
  const targets = focusTargets([mission], held)

  it('puts held games first, then the other games with a forecast', () => {
    const firstUnheld = targets.findIndex((t) => !t.held)
    expect(targets.slice(0, firstUnheld).every((t) => t.held)).toBe(true)
    expect(targets.slice(firstUnheld).every((t) => !t.held)).toBe(true)
  })

  it('opens on the live held game, then in-play bets, at most six', () => {
    const q = directorQueue(targets, { now: CAPTURE, lastScoreChange: new Map() })
    expect(q[0]).toBe(by('GB ML').gameId)
    expect(q.length).toBeLessThanOrEqual(6)
    expect(q.every((id) => targets.find((t) => t.gameId === id)?.held)).toBe(true)
  })

  it('falls back to the first game with a forecast when nothing is held', () => {
    const unheld = focusTargets([mission], [])
    const q = directorQueue(unheld, { now: CAPTURE, lastScoreChange: new Map() })
    expect(q).toHaveLength(1)
  })

  it('knows the small hours in ET', () => {
    expect(etHour(Date.parse('2026-10-19T05:30:00Z'))).toBe(1)
    expect(isEarlyHours(Date.parse('2026-10-19T05:30:00Z'))).toBe(true)
    expect(isEarlyHours(CAPTURE)).toBe(false)
  })
})

describe('helpers the stage and the moments lean on', () => {
  it('puts a score above an MLB Gen 1 cap in the pooled cell and labels it "13+"', () => {
    expect(pooledCell({ away: 14, home: 3 }, 12)).toEqual({ away: 13, home: 3 })
    expect(pooledCell({ away: 14, home: 3 }, null)).toEqual({ away: 14, home: 3 })
    expect(scoreLabel(13, 12)).toBe('13+')
    expect(scoreLabel(12, 12)).toBe('12')
    expect(scoreLabel(13, null)).toBe('13')
  })

  it('lands an MLB Gen 1 final above the cap in the pooled cell, and keeps the exact tally', () => {
    const model = {
      sport: 'MLB', home: { abbr: 'CLE', name: 'Cleveland' }, away: { abbr: 'TB', name: 'Tampa Bay' }, startsAt: null,
      final: { home: 3, away: 14 }, cells: [{ home: 3, away: 13, p: 0.01 }, { home: 4, away: 2, p: 0.99 }], draws: 1000, cap: 12,
      win: { home: 0.5, away: 0.5, tie: 0 }, expected: { home: 4, away: 4 }, modelLabel: 'MLB Gen 1', development: true,
      forecastAt: null, hoursBeforeStart: null, market: null, bookLines: { spreadHome: null, total: null, fresh: false }, story: null,
    } as unknown as SimModel
    const storm = stormFromSim('g', model)
    expect(storm.cap).toBe(12)
    expect(storm.strikeCell).toEqual({ away: 13, home: 3 })
    expect(storm.final).toMatchObject({ away: 14, home: 3 })
    expect(storm.population?.drawCount).toBe(1000)
  })

  it('merges two slates into one board without double-counting a decision', () => {
    const merged = mergeBoards([decisions, decisions, null], '2026-10-19')!
    expect(merged.items).toHaveLength(decisions.items.length)
    expect(merged.date).toBe('2026-10-19')
    expect(mergeBoards([null, undefined], 'x')).toBeNull()
  })

  it('builds a held bet from a ledger position alone, with the ledger as settlement truth', () => {
    const pos = [...ledgerPositions(ledger).values()].find((p) => p.selection === 'ARI ML')!
    const h = heldFromLedger(pos, null)
    expect(h).toMatchObject({ positionId: pos.position_id, gameId: pos.game_id, result: 'WIN', open: false, side: 'AWAY', sport: 'NFL' })
    expect(h.gap).toBeNull()
  })
})
