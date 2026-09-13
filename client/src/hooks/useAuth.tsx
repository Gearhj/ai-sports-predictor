import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { clearToken, endpoints, getStoredToken, storeToken } from '../services/api'
import { setCacheScope } from '../services/queryCache'
import type { AuthUser } from '../types/domain'

interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, displayName: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)
const normalizeUser = (user: AuthUser): AuthUser => ({ ...user, role: user.role.toUpperCase() as AuthUser['role'] })

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(Boolean(getStoredToken()))

  useEffect(() => {
    if (!getStoredToken()) return
    const controller = new AbortController()
    endpoints.me(controller.signal)
      .then((response) => {
        if (!controller.signal.aborted) setUser(normalizeUser(response))
      })
      .catch(() => {
        if (controller.signal.aborted) return
        clearToken()
        setUser(null)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const response = await endpoints.login(email, password)
    storeToken(response.access_token)
    setUser(normalizeUser(response.user))
  }, [])

  const register = useCallback(async (email: string, password: string, displayName: string) => {
    const response = await endpoints.register(email, password, displayName)
    storeToken(response.access_token)
    setUser(normalizeUser(response.user))
  }, [])

  const logout = useCallback(() => {
    // Best-effort server-side revocation (bumps token_version). Local cleanup
    // proceeds regardless so an already-expired/revoked token never strands the
    // user in a signed-in UI state.
    void endpoints.logout().catch(() => undefined)
    clearToken()
    setUser(null)
  }, [])

  // Cached responses belong to whoever was signed in when they were fetched.
  // Changing identity — signing in, switching account, signing out — empties
  // the shared cache so nothing from the previous scope can be rendered.
  useEffect(() => {
    setCacheScope(user ? `user:${user.id}` : 'anon')
  }, [user])

  const value = useMemo(() => ({ user, loading, login, register, logout }), [user, loading, login, register, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}
