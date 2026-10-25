/**
 * The NHL positions panel counts what Wager Results reads (NHL paper era open since
 * 2026-10-16) instead of asserting "no official NHL positions". Invented rows only.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../services/api', () => ({
  apiRequest: vi.fn(async () => ({ rows: [{ status: 'OPEN' }, { status: 'SETTLED' }, { status: 'SETTLED' }], research_only_sports: {} })),
}))

import { NhlNoPositions } from './NhlPerformance'

afterEach(() => cleanup())

describe('NHL paper positions panel', () => {
  it('counts the recorded positions and links to the NHL Wager Results', async () => {
    render(<MemoryRouter><NhlNoPositions /></MemoryRouter>)
    expect(await screen.findByText('3 NHL paper positions recorded (1 open, 2 settled).')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open NHL Wager Results' })).toHaveAttribute('href', '/performance?panel=wagers&sport=NHL')
    expect(screen.getByTestId('nhl-no-positions').textContent).not.toMatch(/No official NHL positions exist/)
  })
})
