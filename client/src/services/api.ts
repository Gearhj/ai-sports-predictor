import type {
  AuthResponse,
  AuthUser,
  BankrollData,
  DashboardData,
  GameAnalysisData,
  HealthData,
  HistoricalGameExplorerPage,
  HistoricalResearchData,
  LiveShadowStatus,
  MarketBoardData,
  PerformanceData,
  ProspectiveRescoreBoard,
  PublicConfig,
  ShadowBoardData,
} from '../types/domain'

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
  dashboard: (signal?: AbortSignal) => apiRequest<DashboardData>('/dashboard/today', {}, signal),
  game: (id: string, signal?: AbortSignal) => apiRequest<GameAnalysisData>(`/games/${encodeURIComponent(id)}`, {}, signal),
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
  bankroll: (signal?: AbortSignal) => apiRequest<BankrollData>('/bankroll', {}, signal),
  shadowBoard: (slateDate: string, signal?: AbortSignal) =>
    apiRequest<ShadowBoardData>(`/shadow-board?date=${encodeURIComponent(slateDate)}`, {}, signal),
  rescoreBoard: (slateDate: string, signal?: AbortSignal) =>
    apiRequest<ProspectiveRescoreBoard>(`/shadow-board/rescores?date=${encodeURIComponent(slateDate)}`, {}, signal),
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
