import type { GameSimulationData, ScenarioCapabilities, ScenarioResult } from '../types/simulation'
import type {
  AuthResponse,
  AuthUser,
  BankrollData,
  ExperimentComparison,
  DashboardData,
  GameAnalysisData,
  HealthData,
  HistoricalGameExplorerPage,
  HistoricalResearchData,
  LiveShadowStatus,
  MarketBoardData,
  MarketPulseBoard,
  MarketPulseGameDetail,
  PregameCaptureCoverage,
  PerformanceData,
  QualificationFunnel,
  ProspectiveRescoreBoard,
  PublicConfig,
  ShadowBoardData,
} from '../types/domain'
import type { SportRegistryResponse, TodayAllSportsReadModel } from '../types/multisport'
import type { LabsGames } from '../types/labs'
import type { OddsCollectionStatus } from '../types/domain'
import type { NflConsumerStatus, NflGameDetail, NflResearchResults, NflWeekOverview, NflWeeks } from '../types/nfl'
import type {
  PlayGeometryPayload,
  PopulationLayersPayload,
  PopulationPayload,
  PopulationRepresentativesPayload,
  ShadowComparison,
  ShadowPath,
} from '../types/shadow'

export const API_BASE_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1').replace(/\/$/, '')
const TOKEN_KEY = 'asp_access_token'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getStoredToken() {
  return window.sessionStorage.getItem(TOKEN_KEY)
}

export function storeToken(token: string) {
  window.sessionStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  window.sessionStorage.removeItem(TOKEN_KEY)
}

function unwrap<T>(payload: unknown): T {
  if (payload && typeof payload === 'object' && 'data' in payload) {
    return (payload as { data: T }).data
  }
  return payload as T
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const token = getStoredToken()
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (options.body) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
    signal,
  })

  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: string; message?: string }
      message = body.detail || body.message || message
    } catch {
      // Retain the status-based error when the server does not return JSON.
    }
    throw new ApiError(message, response.status)
  }

  if (response.status === 204) return undefined as T
  return unwrap<T>(await response.json())
}

export const endpoints = {
  today: (signal?: AbortSignal, sport?: 'MLB' | 'NFL' | null) => {
    const query = new URLSearchParams()
    if (sport) query.set('sport', sport)
    const suffix = query.toString()
    return apiRequest<TodayAllSportsReadModel>(`/today${suffix ? `?${suffix}` : ''}`, {}, signal)
  },
  sportsRegistry: (signal?: AbortSignal) => apiRequest<SportRegistryResponse>('/sports/registry', {}, signal),
  dashboard: (signal?: AbortSignal) => apiRequest<DashboardData>('/dashboard/today', {}, signal),
  game: (id: string, signal?: AbortSignal) => apiRequest<GameAnalysisData>(`/games/${encodeURIComponent(id)}`, {}, signal),
  // Interactive Labs: which games a simulator can actually open.
  labsGames: (league: string, signal?: AbortSignal) =>
    apiRequest<LabsGames>(`/labs/games?league=${encodeURIComponent(league)}`, {}, signal),
  // Research-shadow tracks. Read-only; they carry no wagering authority.
  gameShadowForecasts: (id: string, signal?: AbortSignal) =>
    apiRequest<ShadowComparison>(`/games/${encodeURIComponent(id)}/shadow-forecasts`, {}, signal),
  shadowForecastGeometry: (forecastId: string, signal?: AbortSignal) =>
    apiRequest<PlayGeometryPayload>(
      `/shadow-forecasts/${encodeURIComponent(forecastId)}/geometry`, {}, signal,
    ),
  // Population geometry loads progressively: frames first, then the density
  // layers, then the representative games only when one is asked for.
  shadowForecastPopulation: (forecastId: string, signal?: AbortSignal) =>
    apiRequest<PopulationPayload>(
      `/shadow-forecasts/${encodeURIComponent(forecastId)}/population`, {}, signal,
    ),
  shadowForecastPopulationLayers: (forecastId: string, signal?: AbortSignal) =>
    apiRequest<PopulationLayersPayload>(
      `/shadow-forecasts/${encodeURIComponent(forecastId)}/population/layers`, {}, signal,
    ),
  shadowForecastPopulationRepresentatives: (forecastId: string, signal?: AbortSignal) =>
    apiRequest<PopulationRepresentativesPayload>(
      `/shadow-forecasts/${encodeURIComponent(forecastId)}/population/representatives`, {}, signal,
    ),
  shadowForecastPath: (forecastId: string, signal?: AbortSignal) =>
    apiRequest<ShadowPath>(`/shadow-forecasts/${encodeURIComponent(forecastId)}/path`, {}, signal),
  gameSimulation: (id: string, signal?: AbortSignal) =>
    apiRequest<GameSimulationData>(`/games/${encodeURIComponent(id)}/simulation`, {}, signal),
  scenarioCapabilities: (id: string, signal?: AbortSignal) =>
    apiRequest<ScenarioCapabilities>(`/games/${encodeURIComponent(id)}/simulation/capabilities`, {}, signal),
  // POST because the assumptions are structured input. The endpoint is still
  // strictly read-only: it writes nothing and calls no provider.
  runScenario: (id: string, overrides: Record<string, number>, signal?: AbortSignal) =>
    apiRequest<ScenarioResult>(
      `/games/${encodeURIComponent(id)}/simulation/scenario`,
      { method: 'POST', body: JSON.stringify({ overrides }) },
      signal,
    ),
  markets: (signal?: AbortSignal) => apiRequest<MarketBoardData>('/markets/board', {}, signal),
  performance: (signal?: AbortSignal) => apiRequest<PerformanceData>('/performance', {}, signal),
  history: (signal?: AbortSignal) => apiRequest<HistoricalResearchData>('/research/history', {}, signal),
  historyGames: (
    filters: {
      page: number
      pageSize: number
      team?: string
      month?: number
      tier: 'A' | 'B'
      source?: string
    },
    signal?: AbortSignal,
  ) => {
    const query = new URLSearchParams({
      page: String(filters.page),
      page_size: String(filters.pageSize),
      tier: filters.tier,
    })
    if (filters.team) query.set('team', filters.team)
    if (filters.month) query.set('month', String(filters.month))
    if (filters.source) query.set('source', filters.source)
    return apiRequest<HistoricalGameExplorerPage>(`/research/history/games?${query.toString()}`, {}, signal)
  },
  bankroll: (signal?: AbortSignal, experiment?: 'v1' | 'v2' | null) => {
    const query = new URLSearchParams()
    if (experiment) query.set('experiment', experiment)
    const qs = query.toString()
    return apiRequest<BankrollData>(`/bankroll${qs ? `?${qs}` : ''}`, {}, signal)
  },
  experimentComparison: (signal?: AbortSignal) =>
    apiRequest<ExperimentComparison>('/experiment/comparison', {}, signal),
  nflWeeks: (signal?: AbortSignal) => apiRequest<NflWeeks>('/nfl/weeks', {}, signal),
  nflWeek: (season: number, week: number, signal?: AbortSignal) =>
    apiRequest<NflWeekOverview>(`/nfl/weeks/${season}/${week}`, {}, signal),
  nflGame: (gameId: string, signal?: AbortSignal) =>
    apiRequest<NflGameDetail>(`/nfl/games/${encodeURIComponent(gameId)}`, {}, signal),
  nflResearch: (signal?: AbortSignal) => apiRequest<NflResearchResults>('/nfl/research', {}, signal),
  nflConsumer: (signal?: AbortSignal) => apiRequest<NflConsumerStatus>('/nfl/consumer', {}, signal),
  /** Admin-gated: credit budget, collection plan and provider governance. */
  oddsCollectionStatus: (signal?: AbortSignal) =>
    apiRequest<OddsCollectionStatus>('/admin/odds-collection', {}, signal),
  shadowBoard: (slateDate: string, signal?: AbortSignal, sport?: string | null) => {
    const query = new URLSearchParams({ date: slateDate })
    if (sport) query.set('sport', sport)
    return apiRequest<ShadowBoardData>(`/shadow-board?${query.toString()}`, {}, signal)
  },
  rescoreBoard: (slateDate: string, signal?: AbortSignal) =>
    apiRequest<ProspectiveRescoreBoard>(`/shadow-board/rescores?date=${encodeURIComponent(slateDate)}`, {}, signal),
  marketPulse: (slateDate: string, signal?: AbortSignal, sport?: string | null) => {
    const query = new URLSearchParams({ date: slateDate })
    if (sport) query.set('sport', sport)
    return apiRequest<MarketPulseBoard>(`/prediction-markets/board?${query.toString()}`, {}, signal)
  },
  marketPulseCaptureCoverage: (slateDate: string, signal?: AbortSignal) =>
    apiRequest<PregameCaptureCoverage>(`/prediction-markets/capture-coverage?date=${encodeURIComponent(slateDate)}`, {}, signal),
  marketPulseGame: (gameId: string, signal?: AbortSignal) =>
    apiRequest<MarketPulseGameDetail>(`/prediction-markets/games/${encodeURIComponent(gameId)}`, {}, signal),
  qualificationFunnel: (date: string, signal?: AbortSignal, policy?: 'v1' | 'v2' | null) => {
    const query = new URLSearchParams({ date })
    if (policy) query.set('policy', policy)
    return apiRequest<QualificationFunnel>(`/experiment/qualification-funnel?${query.toString()}`, {}, signal)
  },
  health: (signal?: AbortSignal) => apiRequest<HealthData>('/health/sources', {}, signal),
  publicConfig: (signal?: AbortSignal) => apiRequest<PublicConfig>('/public/config', {}, signal),
  me: (signal?: AbortSignal) => apiRequest<AuthUser>('/auth/me', {}, signal),
  login: (email: string, password: string) =>
    apiRequest<AuthResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  register: (email: string, password: string, display_name: string) =>
    apiRequest<AuthResponse>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password, display_name }),
    }),
  logout: () => apiRequest<{ status: string }>('/auth/logout', { method: 'POST' }),
  runJob: (job: string) => apiRequest<{ id?: string; job_id?: string; status: string }>(`/admin/jobs/${encodeURIComponent(job)}`, { method: 'POST' }),
  liveShadowStatus: (signal?: AbortSignal) => apiRequest<LiveShadowStatus>('/admin/live-shadow/status', {}, signal),
}
