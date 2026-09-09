import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import type { ExperienceLeagueCode } from '../services/sportExperience'

export type SelectedSport = 'ALL' | ExperienceLeagueCode
const CODES = new Set(['ALL', 'MLB', 'NFL', 'NBA', 'WNBA', 'NHL', 'UFC'])
const STORAGE_KEY = 'aisp_selected_sport_v1'

export function normalizeSport(value: string | null | undefined): SelectedSport | null {
  const code = value?.toUpperCase()
  return code && CODES.has(code) ? code as SelectedSport : null
}

/** Keep the event, view and hash; only replace the sport query value. */
export function sportHref(href: string, sport: SelectedSport): string {
  if (!href.startsWith('/') || href.startsWith('//')) return href
  const url = new URL(href, 'http://aisp.local')
  url.searchParams.set('sport', sport)
  url.searchParams.delete('league')
  return `${url.pathname}${url.search}${url.hash}`
}

export function legacySportHref(pathname: string, search: string, hash = ''): string {
  const nfl = pathname === '/nfl' || pathname.startsWith('/nfl/')
  const sport = nfl ? 'NFL' : 'MLB'
  const path = pathname === '/nfl' || pathname === '/mlb'
    ? '/games'
    : pathname.replace(/^\/nfl\/games\//, '/games/').replace(/\/lab$/, '/simulation')
  return sportHref(`${path}${search}${hash}`, sport)
}

interface Selection {
  sport: SelectedSport
  setSport: (sport: SelectedSport) => void
  sportQuery: string
  withSport: (href: string, override?: SelectedSport) => string
}
const Context = createContext<Selection | null>(null)

export function SportSelectionProvider({ children }: { children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [remembered, remember] = useState<SelectedSport>(() => {
    try { return normalizeSport(localStorage.getItem(STORAGE_KEY)) ?? 'ALL' } catch { return 'ALL' }
  })
  const params = new URLSearchParams(location.search)
  const explicit = normalizeSport(params.get('sport') ?? params.get('league'))
  const sport = explicit ?? remembered
  useEffect(() => {
    if (!explicit) return
    remember(explicit)
    try { localStorage.setItem(STORAGE_KEY, explicit) } catch { /* Private browsing still keeps URL state. */ }
  }, [explicit])
  const withSport = (href: string, override?: SelectedSport) => sportHref(href, override ?? sport)
  const setSport = (next: SelectedSport) => {
    remember(next)
    try { localStorage.setItem(STORAGE_KEY, next) } catch { /* URL remains authoritative. */ }
    // Changing sports on a detail page must not relabel that event as another sport.
    const target = /^\/games\/[^/]+/.test(location.pathname)
      ? location.pathname.endsWith('/simulation') ? '/labs' : '/games'
      : location.pathname
    navigate(sportHref(`${target}${location.search}${target === location.pathname ? location.hash : ''}`, next))
  }
  return <Context.Provider value={{ sport, setSport, sportQuery: `?sport=${sport}`, withSport }}>{children}</Context.Provider>
}

export function useSportSelection(): Selection {
  const value = useContext(Context)
  // Standalone focused component tests and old embeds remain usable.
  return value ?? { sport: 'ALL', setSport: () => undefined, sportQuery: '?sport=ALL', withSport: (href, sport = 'ALL') => sportHref(href, sport) }
}
