import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { RequireAdmin } from './components/RequireAdmin'
import { AuthProvider } from './hooks/useAuth'
import { PublicPolicyProvider } from './hooks/usePublicPolicy'
import { SportRegistryProvider } from './hooks/useSportRegistry'
import { SportSelectionProvider, legacySportHref, sportHref } from './hooks/useSportSelection'
import { DEMO_ROUTES_ENABLED } from './config/demoAccess'
import { BuildRefresh } from './components/BuildRefresh'

const TodayPage = lazy(() => import('./pages/TodayPage').then((module) => ({ default: module.TodayPage })))
const LabsPage = lazy(() => import('./pages/LabsPage').then((module) => ({ default: module.LabsPage })))
const MultiSportTodayPage = lazy(() => import('./pages/MultiSportTodayPage').then((module) => ({ default: module.MultiSportTodayPage })))
const HomePage = lazy(() => import('./pages/HomePage').then((module) => ({ default: module.HomePage })))
const GameAnalysisPage = lazy(() => import('./pages/GameAnalysisPage').then((module) => ({ default: module.GameAnalysisPage })))
const GameSimulationPage = lazy(() => import('./pages/GameSimulationPage').then((module) => ({ default: module.GameSimulationPage })))
const MarketBoardPage = lazy(() => import('./pages/MarketBoardPage').then((module) => ({ default: module.MarketBoardPage })))
const PerformancePage = lazy(() => import('./pages/PerformancePage').then((module) => ({ default: module.PerformancePage })))
const BankrollPage = lazy(() => import('./pages/BankrollPage').then((module) => ({ default: module.BankrollPage })))
const ShowBoardPage = lazy(() => import('./pages/ShowBoardPage').then((module) => ({ default: module.ShowBoardPage })))
const DecisionsRoute = lazy(() => import('./pages/DecisionsRoute').then((module) => ({ default: module.DecisionsRoute })))
const ExperimentRoute = lazy(() => import('./pages/ExperimentRoute').then((module) => ({ default: module.ExperimentRoute })))
const MethodologyPage = lazy(() => import('./pages/MethodologyPage').then((module) => ({ default: module.MethodologyPage })))
const HealthPage = lazy(() => import('./pages/HealthPage').then((module) => ({ default: module.HealthPage })))
const ShadowBoardPage = lazy(() => import('./pages/ShadowBoardPage').then((module) => ({ default: module.ShadowBoardPage })))
const MarketPulsePage = lazy(() => import('./pages/MarketPulsePage').then((module) => ({ default: module.MarketPulsePage })))
const MarketPulseGamePage = lazy(() => import('./pages/MarketPulseGamePage').then((module) => ({ default: module.MarketPulseGamePage })))
const UnifiedGamesPage = lazy(() => import('./pages/UnifiedGamesPage').then((module) => ({ default: module.UnifiedGamesPage })))
const PerformanceRoute = lazy(() => import('./pages/PerformanceRoute').then((module) => ({ default: module.PerformanceRoute })))
const MarketsRoute = lazy(() => import('./pages/MarketsRoute').then((module) => ({ default: module.MarketsRoute })))
const UnifiedResearchPage = lazy(() => import('./pages/UnifiedResearchPage').then((module) => ({ default: module.UnifiedResearchPage })))
const NflGamePage = lazy(() => import('./pages/NflGamePage').then((module) => ({ default: module.NflGamePage })))
const NflLabPage = lazy(() => import('./pages/NflLabPage').then((module) => ({ default: module.NflLabPage })))
const AuthPage = lazy(() => import('./pages/AuthPage').then((module) => ({ default: module.AuthPage })))
const NbaResearchPage = lazy(() => import('./pages/NbaResearchPage').then((module) => ({ default: module.NbaResearchPage })))
const NhlResearchPage = lazy(() => import('./pages/NhlResearchPage').then((module) => ({ default: module.NhlResearchPage })))
const NhlResearchGamePage = lazy(() => import('./pages/NhlResearchPage').then((module) => ({ default: module.NhlResearchGamePage })))
const NbaResearchGamePage = lazy(() => import('./pages/NbaResearchGamePage').then((module) => ({ default: module.NbaResearchGamePage })))
const CfbResearchPage = lazy(() => import('./pages/CfbResearchPage').then((module) => ({ default: module.CfbResearchPage })))
const CfbResearchGamePage = lazy(() => import('./pages/CfbResearchPage').then((module) => ({ default: module.CfbResearchGamePage })))
const HouseDividedPage = lazy(() => import('./pages/HouseDividedPage').then((module) => ({ default: module.HouseDividedPage })))
const MarketsBoard = lazy(() => import('./features/markets/MarketBoardPage').then((module) => ({ default: module.MarketBoardPage })))
const SimulatorHub = lazy(() => import('./features/simulator/SimulatorHub').then((module) => ({ default: module.SimulatorHub })))
const SimulatorPage = lazy(() => import('./features/simulator/SimulatorPage').then((module) => ({ default: module.SimulatorPage })))
const PulseHome = lazy(() => import('./features/pulse/PulseHome').then((module) => ({ default: module.PulseHome })))
const MissionControl = lazy(() => import('./features/mission/MissionControl').then((module) => ({ default: module.MissionControl })))
const MmaPage = lazy(() => import('./features/mma/MmaPage').then((module) => ({ default: module.MmaPage })))
const MmqbPage = lazy(() => import('./features/mmqb/MmqbPage').then((module) => ({ default: module.MmqbPage })))
const StartPage = lazy(() => import('./features/start/StartPage').then((module) => ({ default: module.StartPage })))
const ControlPage = lazy(() => import('./features/control/ControlPage').then((module) => ({ default: module.ControlPage })))
const MyPicksPage = lazy(() => import('./pages/MyPicksPage').then((module) => ({ default: module.MyPicksPage })))
// Encore: an unlisted page, loaded only when visited. Outside the app shell on purpose.
const EncorePage = lazy(() => import('./features/guitarists/EncorePage').then((module) => ({ default: module.EncorePage })))
// Game Night: a full-screen broadcast wall (TV mode at ?tv=1). Outside the app shell on purpose, like Encore.
const LiveField = lazy(() => import('./features/livefield/LiveField').then((module) => ({ default: module.LiveField })))
const HqHome = lazy(() => import('./features/hq/HqHome').then((module) => ({ default: module.HqHome })))
const AispLayout = lazy(() => import('./features/flow/AispLayout').then((module) => ({ default: module.AispLayout })))
const SlipPage = lazy(() => import('./features/slip/SlipPage').then((module) => ({ default: module.SlipPage })))
const SlatePage = lazy(() => import('./features/slate/SlatePage').then((module) => ({ default: module.SlatePage })))
const RecordPage = lazy(() => import('./features/record/RecordPage').then((module) => ({ default: module.RecordPage })))
const GameNight = lazy(() => import('./features/gamenight/GameNight').then((module) => ({ default: module.GameNight })))

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
            <BuildRefresh />
            <Suspense fallback={<div className="route-loading" role="status">Loading research desk…</div>}>
              <Routes>
                <Route path="encore" element={<EncorePage />} />
                <Route path="game-night" element={<GameNight />} />
                <Route path="live" element={<LiveField />} />
                {/* The home page is the owner's command center, full width and outside the shell (2026-10-24). */}
                <Route index element={<HqHome />} />
                {/* The Record: the owner's design, full width outside the shell (2026-10-24). The old page is /experiment/classic. */}
                <Route path="experiment" element={<RecordPage />} />
                {/* The Slate (owner's Games design, 2026-10-24); the old games page is /games/classic. */}
                <Route path="games" element={<SlatePage />} />
                {/* My Picks: the play-money bet slip with 10,000 Nights (2026-10-24); the old list is /my-picks/classic. */}
                <Route path="my-picks" element={<SlipPage />} />
                {/* Mission Control redesign (2026-10-24): Slate Flow, The Night, gates board; the old page is /mission-control/classic. */}
                <Route path="mission-control" element={<ControlPage />} />
                {/* UFC Fight Night (2026-10-25): historical archive, orientation-corrected, test mode. */}
                <Route path="mma" element={<MmaPage />} />
                <Route path="start" element={<StartPage />} />
                <Route path="mmqb" element={<MmqbPage />} />
                {/* Model Lab withdrawn 2026-10-25 (owner: real model-building tools or none); the code stays for the rebuild. */}
                <Route path="model-lab" element={<Navigate to="/performance" replace />} />
                {/* Every page not yet rebuilt runs inside the shared AISP shell (one menu, navy + steel). */}
                <Route element={<AispLayout />}>
                  <Route path="pulse" element={<PulseHome />} />
                  <Route path="classic" element={<HomePage />} />
                  {/* The decision board keeps its own page. It is operator
                      content, not a front door, so it is no longer what a
                      first-time visitor lands on. */}
                  <Route path="decisions" element={<DecisionsRoute />} />
                  <Route path="show-board" element={<ShowBoardPage />} />
                  <Route path="mlb" element={<LegacySportRoute />} />
                  <Route path="nfl" element={<LegacySportRoute />} />
                  <Route path="nfl/games/:gameId" element={<LegacySportRoute />} />
                  <Route path="nfl/games/:gameId/lab" element={<LegacySportRoute />} />
                  {/* NBA is research-only: a read of imported forecasts, deliberately
                      not routed through the governed game analysis view. */}
                  <Route path="nba/research" element={<NbaResearchPage />} />
                  <Route path="nba/games/:gameId" element={<NbaResearchGamePage />} />
                  {/* NHL is research-only: imported Model Studio forecasts, no prices,
                      no wagering. Same read-only research route pattern as NBA/CFB. */}
                  <Route path="nhl/research" element={<NhlResearchPage />} />
                  <Route path="nhl/games/:gameId" element={<NhlResearchGamePage />} />
                  {/* College Football is research-only and has no admitted schedule
                      source, so it uses the same read-only research route NBA does
                      rather than a degraded section of the governed game surface. */}
                  <Route path="college-football/research" element={<CfbResearchPage />} />
                  <Route path="college-football/games/:gameId" element={<CfbResearchGamePage />} />
                  <Route path="research/history" element={<UnifiedResearchPage historical />} />
                  <Route path="games/classic" element={<UnifiedGamesPage />} />
                  <Route path="games/:gameId" element={<GameRoute />} />
                  <Route path="games/:gameId/simulation" element={<SimulatorPage />} />
                  <Route path="games/:gameId/simulation/classic" element={<GameRoute simulation />} />
                  <Route path="markets" element={<MarketsRoute />} />
                  <Route path="markets/board" element={<MarketsBoard />} />
                  <Route path="markets/classic" element={<Navigate to="/markets" replace />} />
                  <Route path="performance" element={<PerformanceRoute />} />
                  <Route path="labs" element={<SimulatorHub />} />
                  <Route path="labs/classic" element={<LabsPage />} />
                  <Route path="shadow-board" element={<ShadowBoardPage />} />
                  <Route path="market-pulse" element={<LegacyMarketsRoute />} />
                  <Route path="market-pulse/:gameId" element={<MarketPulseGamePage />} />
                  <Route path="experiment/classic" element={<ExperimentRoute />} />
                  <Route path="a-house-divided" element={<HouseDividedPage />} />
                  <Route path="my-picks/classic" element={<MyPicksPage />} />
                  {/* Every game of the day as a live pipeline: forecast to result. Read-only. */}
                  <Route path="mission-control/classic" element={<MissionControl />} />
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
