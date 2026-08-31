import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { endpoints } from '../services/api'
import type { SportRegistryEntry, SportRegistryResponse } from '../types/multisport'

export type SportRegistryState = 'loading' | 'ready' | 'unavailable'

interface SportRegistryContextValue {
  registry: SportRegistryResponse | null
  state: SportRegistryState
}

const SportRegistryContext = createContext<SportRegistryContextValue>({
  registry: null,
  state: 'unavailable',
})

const ACTIVE_AVAILABILITY = new Set(['LIVE', 'RESEARCH_SHADOW'])

interface RegistryReadCache {
  loader: typeof endpoints.sportsRegistry
  fetchImplementation: typeof globalThis.fetch
  promise: Promise<SportRegistryResponse>
}

let registryReadCache: RegistryReadCache | null = null

function isRegistryResponse(value: unknown): value is SportRegistryResponse {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<SportRegistryResponse>
  return candidate.version === 'SPORT_REGISTRY_V1'
    && Array.isArray(candidate.sports)
    && Array.isArray(candidate.supported_filters)
    && candidate.supported_filters.every((filter) => typeof filter === 'string')
    && candidate.sports.every((sport) => (
      Boolean(sport)
      && typeof sport.league_code === 'string'
      && typeof sport.sport_code === 'string'
      && typeof sport.display_name === 'string'
      && typeof sport.short_label === 'string'
      && typeof sport.availability === 'string'
      && typeof sport.navigable === 'boolean'
      && (typeof sport.navigation_href === 'string' || sport.navigation_href === null)
    ))
}

function readSportRegistryOnce(): Promise<SportRegistryResponse> {
  const loader = endpoints.sportsRegistry
  const fetchImplementation = globalThis.fetch
  if (registryReadCache?.loader === loader && registryReadCache.fetchImplementation === fetchImplementation) {
    return registryReadCache.promise
  }
  const promise = loader().then((response) => {
    if (!isRegistryResponse(response)) throw new Error('Invalid sport registry response')
    return response
  })
  registryReadCache = { loader, fetchImplementation, promise }
  return promise
}

export function SportRegistryProvider({ children }: { children: ReactNode }) {
  const [registry, setRegistry] = useState<SportRegistryResponse | null>(null)
  const [state, setState] = useState<SportRegistryState>('loading')

  useEffect(() => {
    let mounted = true
    readSportRegistryOnce()
      .then((response) => {
        if (mounted) {
          setRegistry(response)
          setState('ready')
        }
      })
      .catch(() => {
        if (mounted) {
          setRegistry(null)
          setState('unavailable')
        }
      })
    return () => { mounted = false }
  }, [])

  // This provider is the application-lifetime query cache. AppShell and every
  // routed page consume the same registry result and never issue parallel reads.
  const value = useMemo(() => ({ registry, state }), [registry, state])
  return <SportRegistryContext.Provider value={value}>{children}</SportRegistryContext.Provider>
}

export function useSportRegistry() {
  return useContext(SportRegistryContext)
}

export function navigableRegistrySports(registry: SportRegistryResponse | null): SportRegistryEntry[] {
  if (!registry) return []
  const supported = new Set(registry.supported_filters.filter((league) => league !== 'ALL'))
  return registry.sports.filter((sport) => (
    sport.navigable
    && ACTIVE_AVAILABILITY.has(sport.availability)
    && supported.has(sport.league_code)
    && typeof sport.navigation_href === 'string'
    && sport.navigation_href.startsWith('/')
    && !sport.navigation_href.startsWith('//')
  ))
}

export function sportNavigationHref(sport: SportRegistryEntry | null): string | null {
  if (!sport) return null
  if (!sport.navigable || !ACTIVE_AVAILABILITY.has(sport.availability)) return null
  return sport.navigation_href?.startsWith('/') && !sport.navigation_href.startsWith('//')
    ? sport.navigation_href
    : null
}

export function registryAvailabilityLabel(sport: SportRegistryEntry): string {
  return sport.availability === 'RESEARCH_SHADOW'
    ? 'Research shadow'
    : sport.availability.toLowerCase().replaceAll('_', ' ').replace(/^./, (value) => value.toUpperCase())
}
