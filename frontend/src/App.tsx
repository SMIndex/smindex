import { lazy, Suspense, Component, type ReactNode, useEffect } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import Layout from '@/components/layout/Layout';
import { ToastContainer } from '@/components/common/Toast';
import { usePriceAlerts } from '@/hooks/usePriceAlerts';
import AccessGate from '@/components/auth/AccessGate';

// Eagerly loaded — main nav pages (no spinner on tab switch)
import DashboardPage from '@/pages/Dashboard';
import PortfolioPage from '@/pages/Portfolio';

// Copy Trading v1 (paper-only social copy) — lazy-loaded
const CopyDiscoverPage = lazy(() => import('@/pages/copy/Discover'));
const CopyTraderProfilePage = lazy(() => import('@/pages/copy/TraderProfile'));
const CopyWatchlistPage = lazy(() => import('@/pages/copy/Watchlist'));
const CopyDashboardPage = lazy(() => import('@/pages/copy/CopyDashboard'));
const CopyPortfolioPage = lazy(() => import('@/pages/copy/Portfolio'));
const CopyHistoryPage = lazy(() => import('@/pages/copy/History'));

// Lazy-loaded pages
const TradePage = lazy(() => import('@/pages/Trade'));
const HeatmapPage = lazy(() => import('@/pages/Heatmap'));
const LeaderDetailPage = lazy(() => import('@/pages/LeaderDetail'));
const PaperTradingPage = lazy(() => import('@/components/paper/PaperTradingPage'));
const OrderHistoryPage = lazy(() => import('@/pages/OrderHistory'));
const MultiChartPage = lazy(() => import('@/pages/MultiChart'));
const MarketComparisonPage = lazy(() => import('@/pages/MarketComparison'));
const SocialPage = lazy(() => import('@/pages/Social'));
const JournalPage = lazy(() => import('@/pages/Journal'));
const MarketsPage = lazy(() => import('@/pages/Markets'));
const SettingsPage = lazy(() => import('@/pages/Settings'));
const PerplReportPage = lazy(() => import('@/pages/PerplReport'));
const TerminalStatsPage = lazy(() => import('@/pages/TerminalStats'));
const WalletInsightsPage = lazy(() => import('@/pages/WalletInsights'));
const AnalyticsPage = lazy(() => import('@/pages/Analytics'));
const AnalyticsPulsePage = lazy(() => import('@/pages/AnalyticsPulse'));
const AnalyticsMoversPage = lazy(() => import('@/pages/AnalyticsMovers'));

// Design B (wrapper Rules 1-2): per-screen migration behind ?design=b. A screen
// renders its B version ONLY when the flag is 'b' AND it has been migrated; else
// the exact current (A) component. Nothing is deleted/renamed.
import { useDesignShell } from '@/design-b/useDesignShell';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
const SettingsB = lazy(() => import('@/design-b/screens/SettingsB'));
const AnalyticsPulseB = lazy(() => import('@/design-b/screens/AnalyticsPulseB'));
const AnalyticsAssetB = lazy(() => import('@/design-b/screens/AnalyticsAssetB'));
const AnalyticsMoversB = lazy(() => import('@/design-b/screens/AnalyticsMoversB'));
const DiscoverB = lazy(() => import('@/design-b/screens/DiscoverB'));
const TraderProfileB = lazy(() => import('@/design-b/screens/TraderProfileB'));
const PortfolioB = lazy(() => import('@/design-b/screens/PortfolioB'));
const TerminalB = lazy(() => import('@/design-b/screens/TerminalB'));
const StrategiesOverviewB = lazy(() => import('@/design-b/screens/StrategiesOverviewB'));
const StrategyDetailB = lazy(() => import('@/design-b/screens/StrategyDetailB'));
const WalletExplorerB = lazy(() => import('@/design-b/screens/WalletExplorerB'));

// Strategies is a shell-B-only surface (spec §2). Under shell A the route
// redirects home so shell A gains no new page.
function StrategiesRoute({ detail }: { detail?: boolean }) {
  const design = useDesignShell();
  if (design !== 'b') return <Navigate to="/" replace />;
  return detail ? <StrategyDetailB /> : <StrategiesOverviewB />;
}

// Wallet Explorer (Part 2) — shell-B surface like Strategies; shell A redirects
// home so A gains no new page until the owner flips the default.
function ExplorerRoute() {
  const design = useDesignShell();
  if (design !== 'b') return <Navigate to="/" replace />;
  return <WalletExplorerB />;
}

function DesignScreen({ a: A, b: B }: { a: React.ComponentType; b: React.ComponentType }) {
  const design = useDesignShell();
  const C = design === 'b' ? B : A;
  return <C />;
}

// Shell-B default landing is Copy trade (spec §2). In shell A, '/' stays Dashboard.
function DashboardOrCopy() {
  const design = useDesignShell();
  return design === 'b' ? <Navigate to="/copy/discover" replace /> : <DashboardPage />;
}

function PageLoader() {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
        <span className="text-xs text-text-secondary">Loading...</span>
      </div>
    </div>
  );
}

class ChunkErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center py-20">
          <div className="text-center space-y-3">
            <div className="text-sm text-text-secondary">Page failed to load</div>
            <button onClick={() => window.location.reload()} className="btn-primary text-xs">Reload</button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// Scroll to top on route change
// SMINDEX launch: sets document.title per route (presentation only).
function DocumentTitle() {
  useDocumentTitle();
  return null;
}

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => { window.scrollTo(0, 0); }, [pathname]);
  return null;
}

function App() {
  usePriceAlerts();
  return (
    <AccessGate>
      <ScrollToTop />
      <DocumentTitle />
      <ChunkErrorBoundary>
        <Suspense fallback={<PageLoader />}>
          <Routes>
            {/* Standalone pages — no header/sidebar */}
            <Route path="/perpl-report" element={<PerplReportPage />} />
            <Route path="/stats" element={<TerminalStatsPage />} />

            {/* Main app pages — with header/sidebar */}
            <Route path="/*" element={
              <Layout>
                <Routes>
                  <Route path="/" element={<DashboardOrCopy />} />
                  <Route path="/trade" element={<DesignScreen a={TradePage} b={TerminalB} />} />
                  {/* Copy Trading v1 — paper-only social copy */}
                  <Route path="/copy" element={<Navigate to="/copy/discover" replace />} />
                  <Route path="/copy/discover" element={<DesignScreen a={CopyDiscoverPage} b={DiscoverB} />} />
                  <Route path="/copy/watchlist" element={<CopyWatchlistPage />} />
                  <Route path="/copy/dashboard" element={<CopyDashboardPage />} />
                  <Route path="/copy/trader/:wallet" element={<DesignScreen a={CopyTraderProfilePage} b={TraderProfileB} />} />
                  <Route path="/copy/portfolio" element={<CopyPortfolioPage />} />
                  <Route path="/copy/history" element={<CopyHistoryPage />} />
                  <Route path="/insights" element={<WalletInsightsPage />} />
                  <Route path="/analytics" element={<DesignScreen a={AnalyticsPulsePage} b={AnalyticsPulseB} />} />
                  {/* movers + wallet alias must register BEFORE :assetParam or they resolve as an asset */}
                  <Route path="/analytics/movers" element={<DesignScreen a={AnalyticsMoversPage} b={AnalyticsMoversB} />} />
                  <Route path="/analytics/wallet/:address" element={<ExplorerRoute />} />
                  <Route path="/analytics/:assetParam" element={<DesignScreen a={AnalyticsPage} b={AnalyticsAssetB} />} />
                  {/* Wallet Explorer (Part 2) — shell-B surface */}
                  <Route path="/wallet" element={<ExplorerRoute />} />
                  <Route path="/wallet/:address" element={<ExplorerRoute />} />
                  <Route path="/portfolio" element={<DesignScreen a={PortfolioPage} b={PortfolioB} />} />
                  <Route path="/strategies" element={<StrategiesRoute />} />
                  <Route path="/strategies/:id" element={<StrategiesRoute detail />} />
                  <Route path="/heatmap" element={<HeatmapPage />} />
                  <Route path="/heatmap/:marketId" element={<HeatmapPage />} />
                  {/* Legacy leader-detail route kept for backwards compat (not used by v1 UI) */}
                  <Route path="/copy/:leaderId" element={<LeaderDetailPage />} />
                  <Route path="/orders" element={<OrderHistoryPage />} />
                  <Route path="/multi-chart" element={<MultiChartPage />} />
                  <Route path="/compare" element={<MarketComparisonPage />} />
                  <Route path="/social" element={<SocialPage />} />
                  <Route path="/journal" element={<JournalPage />} />
                  <Route path="/markets" element={<MarketsPage />} />
                  <Route path="/paper" element={<PaperTradingPage />} />
                  <Route path="/health" element={<Navigate to="/portfolio" replace />} />
                  <Route path="/settings" element={<DesignScreen a={SettingsPage} b={SettingsB} />} />
                </Routes>
              </Layout>
            } />
          </Routes>
        </Suspense>
      </ChunkErrorBoundary>
      <ToastContainer />
    </AccessGate>
  );
}

export default App;
