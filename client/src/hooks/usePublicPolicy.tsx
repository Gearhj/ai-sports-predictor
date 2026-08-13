import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { endpoints } from '../services/api'

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
  const [policy, setPolicy] = useState(developmentDefault)
  const [source, setSource] = useState<PublicPolicyContextValue['source']>('development-default')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    endpoints.publicConfig(controller.signal)
      .then((config) => {
        setPolicy({
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
        })
        setSource('api')
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setPolicy(developmentDefault)
          setSource('development-default')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  const value = useMemo(() => ({ policy, source, loading }), [loading, policy, source])
  return <PublicPolicyContext.Provider value={value}>{children}</PublicPolicyContext.Provider>
}

export function usePublicPolicy() {
  return useContext(PublicPolicyContext)
}
