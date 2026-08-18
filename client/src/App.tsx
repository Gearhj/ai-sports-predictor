import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { RequireAdmin } from './components/RequireAdmin'
import { AuthProvider } from './hooks/useAuth'
import { PublicPolicyProvider } from './hooks/usePublicPolicy'
import { DEMO_ROUTES_ENABLED } from './config/demoAccess'

const TodayPage = lazy(() => import('./pages/TodayPage').then((module) => ({ default: module.TodayPage })))
const HistoricalResearchPage = lazy(() => import('./pages/HistoricalResearchPage').then((module) => ({ default: module.HistoricalResearchPage })))
const GameAnalysisPage = lazy(() => import('./pages/GameAnalysisPage').then((module) => ({ default: module.GameAnalysisPage })))
const MarketBoardPage = lazy(() => import('./pages/MarketBoardPage').then((module) => ({ default: module.MarketBoardPage })))
const PerformancePage = lazy(() => import('./pages/PerformancePage').then((module) => ({ default: module.PerformancePage })))
const BankrollPage = lazy(() => import('./pages/BankrollPage').then((module) => ({ default: module.BankrollPage })))
const MethodologyPage = lazy(() => import('./pages/MethodologyPage').then((module) => ({ default: module.MethodologyPage })))
const HealthPage = lazy(() => import('./pages/HealthPage').then((module) => ({ default: module.HealthPage })))
const AuthPage = lazy(() => import('./pages/AuthPage').then((module) => ({ default: module.AuthPage })))

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <PublicPolicyProvider>
          <Suspense fallback={<div className="route-loading" role="status">Loading research desk…</div>}>
            <Routes>
            <Route element={<AppShell />}>
              <Route index element={<TodayPage />} />
              <Route path="research/history" element={<HistoricalResearchPage />} />
              <Route path="games/:gameId" element={<GameAnalysisPage />} />
              <Route path="markets" element={<MarketBoardPage />} />
              <Route path="performance" element={<PerformancePage />} />
              <Route path="experiment" element={<BankrollPage />} />
              <Route path="methodology" element={<MethodologyPage />} />
              <Route path="admin" element={<RequireAdmin><HealthPage /></RequireAdmin>} />
              <Route path="account" element={<AuthPage />} />
              <Route path="login" element={<AuthPage />} />
              {DEMO_ROUTES_ENABLED && <Route path="demo" element={<TodayPage demo />} />}
              {DEMO_ROUTES_ENABLED && <Route path="demo/games/:gameId" element={<GameAnalysisPage demo />} />}
              {DEMO_ROUTES_ENABLED && <Route path="demo/markets" element={<MarketBoardPage demo />} />}
              {DEMO_ROUTES_ENABLED && <Route path="demo/performance" element={<PerformancePage demo />} />}
              {DEMO_ROUTES_ENABLED && <Route path="demo/experiment" element={<BankrollPage demo />} />}
              {DEMO_ROUTES_ENABLED && <Route path="demo/methodology" element={<MethodologyPage />} />}
              {DEMO_ROUTES_ENABLED && <Route path="demo/admin" element={<HealthPage demo />} />}
              {DEMO_ROUTES_ENABLED && <Route path="demo/account" element={<Navigate to="/account" replace />} />}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
            </Routes>
          </Suspense>
        </PublicPolicyProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
