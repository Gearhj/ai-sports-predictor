import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react'
import { endpoints } from '../services/api'
import type { PublicConfig } from '../types/domain'
import { useApiResource } from './useApiResource'
import { STALE_TIME } from '../services/queryCache'

export interface PublicPolicy {
  modelStatus: string
  componentWeights: {
    baseline: number | null
    run: number | null
    boosting: number | null
  }
  bankroll: {
    label: string
    kellyFraction: number | null
    maximumStakePercentage: number | null
    standardEdgeThreshold?: number
  }
}

interface PublicPolicyContextValue {
  policy: PublicPolicy
  source: 'api' | 'development-default'
  loading: boolean
}

const developmentDefault: PublicPolicy = {
  modelStatus: 'DEVELOPMENT',
  componentWeights: { baseline: null, run: null, boosting: null },
  bankroll: {
    label: 'Configuration unavailable',
    kellyFraction: null,
    maximumStakePercentage: null,
  },
}

const PublicPolicyContext = createContext<PublicPolicyContextValue>({
  policy: developmentDefault,
  source: 'development-default',
  loading: true,
})

const percentagePoints = (value: number) => value <= 1 ? value * 100 : value

export function PublicPolicyProvider({ children }: { children: ReactNode }) {
  // Through the shared cache: the published policy is reference data, read once
  // for the whole app rather than once per provider mount. Without dedup this
  // was fetched twice on every cold load — StrictMode runs the effect twice —
  // and on a single-worker backend a duplicate read is not free: it queues
  // ahead of the slate the reader is waiting for.
  const resource = useApiResource<PublicConfig>(
    useCallback((signal: AbortSignal) => endpoints.publicConfig(signal), []),
    { key: 'public/config', staleTimeMs: STALE_TIME.reference },
  )

  const value = useMemo<PublicPolicyContextValue>(() => {
    const config = resource.data
    // A failed, pending or incomplete read keeps the development default and
    // says so. A response missing the weights or the bankroll policy is not a
    // published policy with zeros in it, and must never be rendered as one.
    if (!config?.component_weights || !config.bankroll_policy) {
      return { policy: developmentDefault, source: 'development-default', loading: resource.loading }
    }
    return {
      policy: {
        modelStatus: config.model_status || developmentDefault.modelStatus,
        componentWeights: {
          baseline: percentagePoints(config.component_weights.baseline),
          run: percentagePoints(config.component_weights.run),
          boosting: percentagePoints(config.component_weights.boosting),
        },
        bankroll: {
          label: config.bankroll_policy.label || 'Configuration unavailable',
          kellyFraction: config.bankroll_policy.kelly_fraction,
          maximumStakePercentage: percentagePoints(config.bankroll_policy.maximum_stake_percentage),
          standardEdgeThreshold: config.bankroll_policy.standard_edge_threshold == null
            ? undefined
            : percentagePoints(config.bankroll_policy.standard_edge_threshold),
        },
      },
      source: 'api',
      loading: false,
    }
  }, [resource.data, resource.loading])

  return <PublicPolicyContext.Provider value={value}>{children}</PublicPolicyContext.Provider>
}

export function usePublicPolicy() {
  return useContext(PublicPolicyContext)
}
