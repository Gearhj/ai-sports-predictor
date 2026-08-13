import { useCallback, useEffect, useState } from 'react'
import type { DataMode, DataProvenance, ResourceState } from '../types/domain'

interface ApiResource<T> {
  data: T | null
  state: ResourceState
  loading: boolean
  error: string | null
  refresh: () => void
}

interface ApiResourceOptions<T> {
  demoData?: T
}

export function resolveDataMode(payload: DataProvenance): DataMode {
  if (payload.data_mode) return payload.data_mode
  if (typeof payload.is_demo === 'boolean') return payload.is_demo ? 'demo' : 'live'
  // An unprovenanced response must not be promoted to either live or demo results.
  return 'empty'
}

export function useApiResource<T extends DataProvenance>(
  loader: (signal: AbortSignal) => Promise<T>,
  options: ApiResourceOptions<T> = {},
): ApiResource<T> {
  const { demoData } = options
  const [data, setData] = useState<T | null>(demoData ?? null)
  const [state, setState] = useState<ResourceState>(demoData ? 'demo' : 'loading')
  const [loading, setLoading] = useState(!demoData)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)

  const refresh = useCallback(() => {
    if (!demoData) setRevision((value) => value + 1)
  }, [demoData])

  useEffect(() => {
    if (demoData) {
      setData(demoData)
      setState('demo')
      setLoading(false)
      setError(null)
      return
    }

    const controller = new AbortController()
    setLoading(true)
    setState((current) => current === 'live' || current === 'empty' ? current : 'loading')
    loader(controller.signal)
      .then((result) => {
        setData(result)
        setState(resolveDataMode(result))
        setError(null)
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setData(null)
        setState('unavailable')
        setError(reason instanceof Error ? reason.message : 'Live API unavailable')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [demoData, loader, revision])

  return { data, state, loading, error, refresh }
}
