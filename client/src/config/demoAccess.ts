const explicitDemoFlag = import.meta.env.VITE_ENABLE_DEMO_MODE

export function resolveDemoRoutesEnabled(explicit: string | undefined, dev: boolean, mode: string) {
  return explicit == null ? dev || mode === 'test' : explicit === 'true'
}

export const DEMO_ROUTES_ENABLED = resolveDemoRoutesEnabled(explicitDemoFlag, import.meta.env.DEV, import.meta.env.MODE)
