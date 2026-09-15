import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { RequireAdmin } from './components/RequireAdmin'
import { AuthProvider } from './hooks/useAuth'
import { PublicPolicyProvider } from './hooks/usePublicPolicy'
import { SportRegistryProvider } from './hooks/useSportRegistry'
import { SportSelectionProvider, legacySportHref, sportHref } from './hooks/useSportSelection'
import { DEMO_ROUTES_ENABLED } from './config/demoAccess'

const TodayPage = lazy(() => import('./pages/TodayPage').then((module) => ({ default: module.TodayPage })))
const LabsPage = lazy(() => import('./pages/LabsPage').then((module) => ({ default: module.LabsPage })))
const MultiSportTodayPage = lazy(() => import('./pages/MultiSportTodayPage').then((module) => ({ default: module.MultiSportTodayPage })))
const HomePage = lazy(() => import('./pages/HomePage').then((module) => ({ default: module.HomePage })))
const GameAnalysisPage = lazy(() => import('./pages/GameAnalysisPage').then((module) => ({ default: module.GameAnalysisPage })))
const GameSimulationPage = lazy(() => import('./pages/GameSimulationPage').then((module) => ({ default: module.GameSimulationPage })))
const MarketBoardPage = lazy(() => import('./pages/MarketBoardPage').then((module) => ({ default: module.MarketBoardPage })))
const PerformancePage = lazy(() => import('./pages/PerformancePage').then((module) => ({ default: module.PerformancePage })))
const BankrollPage = lazy(() => import('./pages/BankrollPage').then((module) => ({ default: module.BankrollPage })))
const ExperimentRoute = lazy(() => import('./pages/ExperimentRoute').then((module) => ({ default: module.ExperimentRoute })))
const MethodologyPage = lazy(() => import('./pages/MethodologyPage').then((module) => ({ default: module.MethodologyPage })))
const HealthPage = lazy(() => import('./pages/HealthPage').then((module) => ({ default: module.HealthPage })))
const ShadowBoardPage = lazy(() => import('./pages/ShadowBoardPage').then((module) => ({ default: module.ShadowBoardPage })))
const MarketPulsePage = lazy(() => import('./pages/MarketPulsePage').then((module) => ({ default: module.MarketPulsePage })))
const MarketPulseGamePage = lazy(() => import('./pages/MarketPulseGamePage').then((module) => ({ default: module.MarketPulseGamePage })))
const UnifiedGamesPage = lazy(() => import('./pages/UnifiedGamesPage').then((module) => ({ default: module.UnifiedGamesPage })))
const UnifiedMarketsPage = lazy(() => import('./pages/UnifiedMarketsPage').then((module) => ({ default: module.UnifiedMarketsPage })))
const UnifiedResearchPage = lazy(() => import('./pages/UnifiedResearchPage').then((module) => ({ default: module.UnifiedResearchPage })))
const NflGamePage = lazy(() => import('./pages/NflGamePage').then((module) => ({ default: module.NflGamePage })))
const NflLabPage = lazy(() => import('./pages/NflLabPage').then((module) => ({ default: module.NflLabPage })))
const AuthPage = lazy(() => import('./pages/AuthPage').then((module) => ({ default: module.AuthPage })))
const NbaResearchPage = lazy(() => import('./pages/NbaResearchPage').then((module) => ({ default: module.NbaResearchPage })))
const NbaResearchGamePage = lazy(() => import('./pages/NbaResearchGamePage').then((module) => ({ default: module.NbaResearchGamePage })))
const CfbResearchPage = lazy(() => import('./pages/CfbResearchPage').then((module) => ({ default: module.CfbResearchPage })))
const CfbResearchGamePage = lazy(() => import('./pages/CfbResearchPage').then((module) => ({ default: module.CfbResearchGamePage })))

function LegacySportRoute() {
  const { pathname, search, hash } = useLocation()
  return <Navigate to={legacySportHref(pathname, search, hash)} replace />
}
function LegacyMarketsRoute() {
  const { search, hash } = useLocation()
  return <Navigate to={`/markets${search}${hash}`} replace />
}
function GameRoute({ simulation = false }: { simulation?: boolean }) {
  const { gameId = '' } = useParams()
  const location = useLocation()
  const requested = new URLSearchParams(location.search).get('sport')
  if (!requested || requested === 'ALL') {
    const sport = /^\d{4}_\d{2}_[A-Z]+_[A-Z]+$/.test(gameId) ? 'NFL' : 'MLB'
    return <Navigate to={sportHref(`${location.pathname}${location.search}${location.hash}`, sport)} replace />
  }
  return requested === 'NFL'
    ? simulation ? <NflLabPage /> : <NflGamePage />
    : simulation ? <GameSimulationPage /> : <GameAnalysisPage />
}

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
          <SportRegistryProvider>
            <SportSelectionProvider>
            <ScrollToTop />
            <Suspense fallback={<div className="route-loading" role="status">Loading research desk…</div>}>
              <Routes>
                <Route element={<AppShell />}>
                  <Route index element={<HomePage />} />
                  {/* The decision board keeps its own page. It is operator
                      content, not a front door, so it is no longer what a
                      first-time visitor lands on. */}
                  <Route path="decisions" element={<MultiSportTodayPage />} />
                  <Route path="mlb" element={<LegacySportRoute />} />
                  <Route path="nfl" element={<LegacySportRoute />} />
                  <Route path="nfl/games/:gameId" element={<LegacySportRoute />} />
                  <Route path="nfl/games/:gameId/lab" element={<LegacySportRoute />} />
                  {/* NBA is research-only: a read of imported forecasts, deliberately
                      not routed through the governed game analysis view. */}
                  <Route path="nba/research" element={<NbaResearchPage />} />
                  <Route path="nba/games/:gameId" element={<NbaResearchGamePage />} />
                  {/* College Football is research-only and has no admitted schedule
                      source, so it uses the same read-only research route NBA does
                      rather than a degraded section of the governed game surface. */}
                  <Route path="college-football/research" element={<CfbResearchPage />} />
                  <Route path="college-football/games/:gameId" element={<CfbResearchGamePage />} />
                  <Route path="research/history" element={<UnifiedResearchPage historical />} />
                  <Route path="games" element={<UnifiedGamesPage />} />
                  <Route path="games/:gameId" element={<GameRoute />} />
                  <Route path="games/:gameId/simulation" element={<GameRoute simulation />} />
                  <Route path="markets" element={<UnifiedMarketsPage />} />
                  <Route path="performance" element={<UnifiedResearchPage />} />
                  <Route path="labs" element={<LabsPage />} />
                  <Route path="shadow-board" element={<ShadowBoardPage />} />
                  <Route path="market-pulse" element={<LegacyMarketsRoute />} />
                  <Route path="market-pulse/:gameId" element={<MarketPulseGamePage />} />
                  <Route path="experiment" element={<ExperimentRoute />} />
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
            </SportSelectionProvider>
          </SportRegistryProvider>
        </PublicPolicyProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
