import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EncorePage } from './EncorePage'
import { guitarists, rankingSources, styles } from './data/guitarists'
import { MethodologyPage } from '../../pages/MethodologyPage'

vi.mock('../../hooks/usePublicPolicy', () => ({
  usePublicPolicy: () => ({
    policy: { modelStatus: 'DEVELOPMENT', componentWeights: { baseline: 35, run: 40, boosting: 25 },
      bankroll: { kellyFraction: 0.25, maximumStakePercentage: 2 } },
    source: 'api', loading: false,
  }),
}))

function at(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/encore" element={<EncorePage />} />
        <Route path="/methodology" element={<MethodologyPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const listButtons = () => [...document.querySelectorAll('.g-list button')] as HTMLButtonElement[]

describe('Encore', { timeout: 60_000 }, () => {
  afterEach(cleanup)

  it('is reached from a single, labelled icon at the very end of Methodology', () => {
    at('/methodology')
    const link = screen.getByRole('link', { name: 'Encore' })
    expect(link.getAttribute('href')).toBe('/encore')
    expect(link.textContent).toBe('')
    const page = document.querySelector('.methodology-page')!
    expect(page.lastElementChild?.contains(link)).toBe(true)
    fireEvent.click(link)
    expect(screen.getByRole('heading', { level: 1, name: 'Choose the method. Watch the ranking change.' })).toBeTruthy()
  })

  it('renders every category, preset, source count and the full ranking', () => {
    at('/encore')
    const presets = [...document.querySelectorAll('.g-presets button strong')].map((n) => n.textContent)
    expect(presets).toEqual(["Critics' Consensus", 'Technical Mastery', 'Innovation', 'Commercial Legacy', 'Balanced'])
    for (const label of ['Poll consensus', 'Influence', 'Technique', 'Innovation', 'Commercial impact', 'Career length']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    }
    const select = document.querySelector('.g-toolbar select')!
    expect([...select.querySelectorAll('option')].map((o) => o.textContent)).toEqual(['All styles', ...styles])
    expect(listButtons()).toHaveLength(guitarists.length)
    const summary = document.querySelector('.g-summary')!
    expect(within(summary as HTMLElement).getByText(String(rankingSources.length))).toBeTruthy()
    expect(document.querySelector('.g-hero img')?.getAttribute('src')).toBeTruthy()
  })

  it('re-ranks with a preset or a weight, filters, searches and shows a player', () => {
    at('/encore')
    const leaderBefore = listButtons()[0].querySelector('strong')!.textContent
    fireEvent.click([...document.querySelectorAll('.g-presets button')].find((b) => b.textContent?.includes('Technical Mastery'))!)
    fireEvent.change(document.querySelectorAll('.g-weights input')[0], { target: { value: '0' } })
    expect(screen.getByText('Weight total').nextElementSibling?.textContent).not.toBe('100')
    const leaderAfter = listButtons()[0].querySelector('strong')!.textContent
    expect(typeof leaderAfter).toBe('string')
    expect(leaderBefore).toBeTruthy()

    fireEvent.change(document.querySelector('.g-toolbar select')!, { target: { value: 'Jazz' } })
    const jazz = guitarists.filter((g) => g.styles.includes('Jazz')).length
    expect(listButtons()).toHaveLength(jazz)

    fireEvent.change(screen.getByPlaceholderText(/Search by guitarist/), { target: { value: 'zzzz-no-such-player' } })
    expect(listButtons()).toHaveLength(0)
    expect(screen.getByText('No match')).toBeTruthy()
  })

  it('selects a player and shows the detail panel', () => {
    at('/encore')
    const second = listButtons()[1]
    const name = second.querySelector('.identity strong')!.textContent!
    fireEvent.click(second)
    expect(document.querySelector('.g-detail h2')?.textContent).toBe(name)
    expect(screen.getByText('Poll Evidence')).toBeTruthy()
  })

  it('keeps everything inside its namespace and offers one way back', () => {
    at('/encore')
    expect(document.querySelectorAll('.gg-encore')).toHaveLength(1)
    const back = screen.getByRole('link', { name: /Return to Methodology/ })
    expect(back.getAttribute('href')).toBe('/methodology')
    expect(document.body.textContent).not.toMatch(/sportsbook|wager|odds|bankroll/i)
  })
})
