/**
 * Contract guard: render payloads captured from the real builder
 * (`app.services.mission_control.mission_control`, run read-only against
 * production data) and check the page consumes exactly what the API returns.
 *
 * Every JSON file in `src/test/mission-captured/` is one captured
 * `GET /mission-control?date=` body. They were captured out of process, so the
 * scheduler reads NOT_RUNNING and every job NOT_REGISTERED; the page must still
 * render all of it honestly. Clocks are real, so countdowns read as overdue
 * once the capture is old; that is expected and must not break rendering.
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { STAGE_KEYS, STAGE_NAMES, stageTone, usd, type MissionControl as MissionPayload } from './missionData'

let payload: unknown = null

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    apiRequest: vi.fn((path: string) => path.startsWith('/mission-control')
      ? Promise.resolve(payload)
      : Promise.reject(new Error(`unexpected ${path}`))),
  }
})

import { MissionControl } from './MissionControl'

const captured = import.meta.glob('../../test/mission-captured/*.json', { eager: true, import: 'default' }) as Record<string, MissionPayload>
const cases = Object.entries(captured).map(([file, body]) => [file.split('/').pop() as string, body] as const)

/** Text that only appears when a field the page expected was missing or mistyped. */
const BROKEN = /undefined|NaN|Invalid Date|\[object Object\]/

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Mission Control renders captured production payloads', () => {
  it('has at least one captured payload', () => {
    expect(cases.length).toBeGreaterThan(0)
  })

  it.each(cases)('%s: every game, stage, bankroll and open bet renders without a broken value', async (_file, body) => {
    payload = body
    const errors = vi.spyOn(console, 'error')
    render(
      <MemoryRouter initialEntries={[`/mission-control?date=${body.date}`]}>
        <Routes><Route path="/mission-control" element={<MissionControl />} /></Routes>
      </MemoryRouter>,
    )
    expect(body.schema_version).toBe('AISP_MISSION_CONTROL_V1')

    if (body.games.length) {
      await screen.findAllByRole('article', {}, { timeout: 5000 })
    } else {
      await screen.findByText(/No games on the board/)
    }
    const rows = screen.queryAllByRole('article')
    expect(rows).toHaveLength(body.games.length)

    // Each row: seven chips, each coloured exactly as the server's state says.
    for (const game of body.games) {
      const row = document.getElementById(`mc-game-${game.game_id}`) as HTMLElement
      expect(row, game.matchup).toBeTruthy()
      const chips = within(row.querySelector('.mc-pipe') as HTMLElement).getAllByRole('listitem')
      expect(chips).toHaveLength(7)
      STAGE_KEYS.forEach((key, i) => {
        expect(chips[i], `${game.matchup} ${key}`).toHaveClass(`mc-tone--${stageTone(game.stages[key]?.state)}`)
        expect(chips[i].getAttribute('aria-label')).toMatch(new RegExp(`^${STAGE_NAMES[key]}: `))
      })
      expect(within(row).getByLabelText(/^Next step: /).getAttribute('aria-label')).not.toBe('Next step: Next step not reported')
    }

    // Every row's stage details open and read cleanly.
    for (const game of body.games) {
      const row = document.getElementById(`mc-game-${game.game_id}`) as HTMLElement
      fireEvent.click(within(row).getByRole('button', { name: `Show stage details for ${game.matchup}` }))
      const detail = document.getElementById(`mc-detail-${game.game_id}`) as HTMLElement
      expect(detail, game.matchup).toBeTruthy()
      expect(detail.textContent ?? '', game.matchup).not.toMatch(BROKEN)
    }

    // Five bankrolls, NHL included; funded ones show the server's number.
    for (const bank of body.bankrolls) {
      const card = screen.getByLabelText(`${bank.label} bankroll`)
      if (bank.funded) expect(card).toHaveTextContent(usd(bank.bankroll_usd))
    }
    expect(document.querySelectorAll('.mc-bank')).toHaveLength(5)

    // The shared meter shows the server's figures, not a fallback.
    if (body.allowance.state === 'ROOM' || body.allowance.state === 'FULL') {
      const meter = screen.getByRole('group', { name: /^Shared daily limit/ })
      expect(meter).toHaveTextContent(`${usd(body.allowance.entered_usd)} entered`)
      expect(meter).toHaveTextContent(`${usd(body.allowance.room_usd)} room`)
    }

    // Every open bet is on the sweating rail.
    const rail = screen.getByRole('heading', { name: 'Sweating now' }).closest('.mc-sweat') as HTMLElement
    // A captured payload whose open-bets read failed is null: then the rail must say so instead.
    if (body.sweating == null) expect(rail).toHaveTextContent('Open bets could not be read')
    expect(rail.querySelectorAll('.mc-sweat__item')).toHaveLength(body.sweating?.length ?? 0)
    for (const bet of body.sweating ?? []) expect(rail).toHaveTextContent(bet.selection)

    // Producers: one dot per sport the server reported.
    const producers = screen.getByRole('group', { name: 'Forecast producers' })
    for (const p of body.health.producers) expect(within(producers).getByLabelText(new RegExp(`^${p.sport} producer: `))).toBeInTheDocument()

    expect(document.body.textContent ?? '').not.toMatch(BROKEN)
    expect(errors).not.toHaveBeenCalled()
  }, 60_000)
})
