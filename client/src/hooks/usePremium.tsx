/**
 * Who may see a premium feature (scale plan Phase 1, item 3). During the beta the server reports
 * premium_gating_enabled=false, so everything is open and a premium feature is only labelled. Once gating is on,
 * a member needs the PREMIUM entitlement (granted by the owner today, by the billing provider later).
 */
import { useCallback } from 'react'
import { useApiResource } from './useApiResource'
import { useOptionalUser } from './useAuth'
import { endpoints } from '../services/api'
import { STALE_TIME } from '../services/queryCache'
import type { PublicConfig } from '../types/domain'

export interface PremiumState {
  /** True when the feature may be shown to this visitor. */
  allowed: boolean
  /** The server locks premium features (false during the beta). */
  gating: boolean
  /** This account holds the entitlement. */
  premium: boolean
  signedIn: boolean
  loading: boolean
}

export function usePremium(): PremiumState {
  const user = useOptionalUser()
  const config = useApiResource<PublicConfig>(
    useCallback((signal: AbortSignal) => endpoints.publicConfig(signal), []),
    { key: 'public/config', staleTimeMs: STALE_TIME.reference },
  )
  const gating = Boolean(config.data?.premium_gating_enabled)
  const premium = Boolean(user?.entitlements?.includes('premium')) || user?.role === 'ADMIN'
  return { allowed: !gating || premium, gating, premium, signedIn: Boolean(user), loading: config.loading && !config.data }
}
