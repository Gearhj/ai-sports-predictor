import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { RequireAdmin } from './components/RequireAdmin'
import { AuthProvider } from './hooks/useAuth'
import { PublicPolicyProvider } from './hooks/usePublicPolicy'
import { DEMO_ROUTES_ENABLED } from './config/demoAccess'

const TodayPage = lazy(() => import('./pages/TodayPage').then((module) => ({ default: module.TodayPage })))
const MultiSportTodayPage = lazy(() => import('./pages/MultiSportTodayPage').then((module) => ({ default: module.MultiSportTodayPage })))
const HistoricalResearchPage = lazy(() => import('./pages/HistoricalResearchPage').then((module) => ({ default: module.HistoricalResearchPage })))
const GameAnalysisPage = lazy(() => import('./pages/GameAnalysisPage').then((module) => ({ default: module.GameAnalysisPage })))
const MarketBoardPage = lazy(() => import('./pages/MarketBoardPage').then((module) => ({ default: module.MarketBoardPage })))
const PerformancePage = lazy(() => import('./pages/PerformancePage').then((module) => ({ default: module.PerformancePage })))
const BankrollPage = lazy(() => import('./pages/BankrollPage').then((module) => ({ default: module.BankrollPage })))
const MethodologyPage = lazy(() => import('./pages/MethodologyPage').then((module) => ({ default: module.MethodologyPage })))
const HealthPage = lazy(() => import('./pages/HealthPage').then((module) => ({ default: module.HealthPage })))
const ShadowBoardPage = lazy(() => import('./pages/ShadowBoardPage').then((module) => ({ default: module.ShadowBoardPage })))
const MarketPulsePage = lazy(() => import('./pages/MarketPulsePage').then((module) => ({ default: module.MarketPulsePage })))
const MarketPulseGamePage = lazy(() => import('./pages/MarketPulseGamePage').then((module) => ({ default: module.MarketPulseGamePage })))
const AuthPage = lazy(() => import('./pages/AuthPage').then((module) => ({ default: module.AuthPage })))

function ScrollToTop() {
  // Start each navigated page at the top so opening a game from a scrolled
  // Today list never lands mid-page and looks blank.
  const { pathname } = useLocation()
  useEffect(() => {
    if (typeof window.scrollTo === 'function') {
      try { window.scrollTo(0, 0) } catch { /* jsdom / restricted contexts */ }
    }
  }, [pathname])
  return null
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <PublicPolicyProvider>
          <ScrollToTop />
          <Suspense fallback={<div className="route-loading" role="status">Loading research desk…</div>}>
            <Routes>
            <Route element={<AppShell />}>
              <Route index element={<MultiSportTodayPage />} />
              <Route path="mlb" element={<TodayPage />} />
              <Route path="research/history" element={<HistoricalResearchPage />} />
              <Route path="games/:gameId" element={<GameAnalysisPage />} />
              <Route path="markets" element={<MarketBoardPage />} />
              <Route path="performance" element={<PerformancePage />} />
              <Route path="shadow-board" element={<ShadowBoardPage />} />
              <Route path="market-pulse" element={<MarketPulsePage />} />
              <Route path="market-pulse/:gameId" element={<MarketPulseGamePage />} />
              <Route path="experiment" element={<BankrollPage />} />
              <Route path="methodology" element={<MethodologyPage />} />
              <Route path="admin" element={<RequireAdmin><HealthPage /></RequireAdmin>} />
              <Route path="account" element={<AuthPage />} />
              <Route path="login" element={<AuthPage />} />
              {DEMO_ROUTES_ENABLED && <Route path="demo" element={<TodayPage demo />} />}
              {DEMO_ROUTES_ENABLED && <Route path="demo/games/:gameId" element={<GameAnalysisPage demo />} />}
              {DEMO_ROUTES_ENABLED && <Route path="demo/markets" element={<MarketBoardPage demo />} />}
              {DEMO_ROUTES_ENABLED && <Route path="demo/market-pulse" element={<MarketPulsePage demo />} />}
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
