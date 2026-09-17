import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { SportSelectionProvider, legacySportHref, sportHref, useSportSelection } from './useSportSelection'

afterEach(() => { cleanup(); localStorage.clear() })
function Workspace() {
  const {sport,setSport,withSport}=useSportSelection()
  const location=useLocation()
  return <><output aria-label="Location">{location.pathname}{location.search}{location.hash}</output><output aria-label="Selected sport">{sport}</output><button onClick={()=>setSport('MLB')}>MLB</button><Link to={withSport('/markets')}>Markets</Link><Link to={withSport('/labs')}>Labs</Link></>
}
it('retains URL sport while navigating and changes only the selected sport',async()=>{
  const user=userEvent.setup()
  render(<MemoryRouter initialEntries={['/games?sport=NFL&week=1']}><SportSelectionProvider><Workspace/></SportSelectionProvider></MemoryRouter>)
  await user.click(screen.getByRole('link',{name:'Markets'}))
  expect(screen.getByLabelText('Location')).toHaveTextContent('/markets?sport=NFL')
  await user.click(screen.getByRole('link',{name:'Labs'}))
  expect(screen.getByLabelText('Location')).toHaveTextContent('/labs?sport=NFL')
  await user.click(screen.getByRole('button',{name:'MLB'}))
  expect(screen.getByLabelText('Location')).toHaveTextContent('/labs?sport=MLB')
  expect(localStorage.getItem('aisp_selected_sport_v1')).toBe('MLB')
})
it('explicit All overrides a saved sport and a detail sport change returns to the game picker',async()=>{
  localStorage.setItem('aisp_selected_sport_v1','NFL')
  const user=userEvent.setup()
  render(<MemoryRouter initialEntries={['/games/event/simulation?sport=ALL&view=drives']}><SportSelectionProvider><Workspace/></SportSelectionProvider></MemoryRouter>)
  expect(screen.getByLabelText('Selected sport')).toHaveTextContent('ALL')
  await user.click(screen.getByRole('button',{name:'MLB'}))
  expect(screen.getByLabelText('Location')).toHaveTextContent('/labs?sport=MLB&view=drives')
})
it('redirects old bookmarks without losing event, simulation mode, week or hash',()=>{
  expect(legacySportHref('/nfl','?season=2026&week=1')).toBe('/games?season=2026&week=1&sport=NFL')
  expect(legacySportHref('/nfl/games/abc/lab','?view=drives&scenario=BASE','#drive-4')).toBe('/games/abc/simulation?view=drives&scenario=BASE&sport=NFL#drive-4')
  expect(legacySportHref('/mlb','?date=2026-09-08')).toBe('/games?date=2026-09-08&sport=MLB')
  expect(sportHref('/games/abc/simulation?track=v18&view=population#frame-5','MLB')).toBe('/games/abc/simulation?track=v18&view=population&sport=MLB#frame-5')
})

describe('impliedSport', () => {
  it('ties research pages to their own sport', async () => {
    const { impliedSport, normalizeSport } = await import('./useSportSelection')
    expect(impliedSport('/nba/research')).toBe('NBA')
    expect(impliedSport('/nba/games/abc')).toBe('NBA')
    expect(impliedSport('/college-football/research')).toBe('CFB')
    expect(impliedSport('/games')).toBeNull()
    expect(normalizeSport('cfb')).toBe('CFB')
  })
})
