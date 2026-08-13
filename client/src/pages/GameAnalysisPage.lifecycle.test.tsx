import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { GameAnalysisPage } from './GameAnalysisPage'

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-placeholder" />,
}))

describe('GameAnalysis lifecycle evidence', () => {
  it('renders lifecycle, refresh, lineup, final-state, and readiness detail', async () => {
    render(
      <MemoryRouter initialEntries={['/demo/games/demo-nym-atl']}>
        <Routes>
          <Route path="/demo/games/:gameId" element={<GameAnalysisPage demo />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('region', { name: 'Forecast lifecycle details' })).toBeInTheDocument()
    expect(screen.getByText('T−60m')).toBeInTheDocument()
    expect(screen.getByText('33m to first pitch')).toBeInTheDocument()
    expect(screen.getByText('Illustrative source refresh · 6:40 PM')).toBeInTheDocument()
    expect(screen.getByText('Illustrative scheduled refresh · 6:45 PM')).toBeInTheDocument()
    expect(screen.getByText('Projected · complete')).toBeInTheDocument()
    expect(screen.getByText('Confirmed · complete')).toBeInTheDocument()
    expect(screen.getByText('Atlanta confirmed; New York remains projected')).toBeInTheDocument()
    expect(screen.getAllByText('Not started').length).toBeGreaterThan(0)
  })
})
