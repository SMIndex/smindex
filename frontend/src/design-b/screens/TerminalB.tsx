import { useEffect, useState, useMemo, lazy, Suspense } from 'react';
import { clsx } from 'clsx';
import { useMarketData } from '@/hooks/useMarketData';
import { useTradingStore } from '@/stores/tradingStore';
import { MARKETS, MARKET_IDS } from '@/config/constants';
import { loadMarketConfigs, warmupTradingConnection } from '@/lib/perplTrading';
import { useAuth } from '@/hooks/useAuth';
import { useMarketDataWs } from '@/hooks/useMarketDataWs';
import api from '@/lib/api';
import { formatPrice, formatPercent } from '@/lib/formatters';
import PriceAlertModal from '@/components/common/PriceAlertModal';
// Chart loads lazily (spec 4.6): the trade screen renders and takes orders before the chart bundle arrives.
const TradingChart = lazy(() => import('@/components/terminal/TradingChart'));
import FundingComparison from '@/components/terminal/FundingComparison';
import OrderBook from '@/components/terminal/OrderBook';
import OrderForm from '@/components/terminal/OrderForm';
import PositionCalculator from '@/components/terminal/PositionCalculator';
import PositionsPanel from '@/components/terminal/PositionsPanel';
import { TradeSkeleton } from '@/components/common/Skeleton';
import EnableOneClickModal from '@/components/settings/EnableOneClickModal';
import MarketSelector from '@/components/terminal/MarketSelector';
import { useDesignTheme } from '../useDesignShell';

// Design B — Terminal (spec §3.3). Full B re-skin. REUSES the exact existing
// components and hooks (TradingChart, OrderBook, OrderForm, PositionCalculator,
// PositionsPanel, FundingComparison, MarketSelector) + the same data/order flow
// (useMarketDataWs, warmupTradingConnection, HMAC-signed ?stage= verify). The
// re-skin is presentation only:
//  - DOM components (order book, order form, positions, funding, market strip,
//    chart chrome) re-theme via the `.tbwrap` A-var→B-token map in tokens.css.
//  - The chart CANVAS themes via TradingChart's B-aware chartPalette() (candles
//    = --long/--short, bg/grid/axis/crosshair/last-price = B, signal markers
//    relabelled), keyed to remount on the B theme toggle.
//  - OrderForm's `variant="b"` only changes the action-button label; a dev-only
//    log lets the submit payload be diffed A vs B (must be equal).
// The candle/indicator DATA pipeline and the order/signing/ws path are UNCHANGED
// (wrapper Rule 7): every edit is colour/label, gated so shell A is byte-identical.

export default function TerminalB() {
  const { markets, isLoading } = useMarketData();
  const marketIds = useMemo(() => {
    const ids = Object.keys(markets || {}).map(Number);
    return ids.length ? ids.sort((a, b) => a - b) : MARKET_IDS;
  }, [markets]);
  const selectedMarketId = useTradingStore((s) => s.selectedMarketId);
  const setSelectedMarket = useTradingStore((s) => s.setSelectedMarket);
  const setStagedOrder = useTradingStore((s) => s.setStagedOrder);
  const [rightTab, setRightTab] = useState<'order' | 'calc'>('order');
  const [chartOpen, setChartOpen] = useState(true);
  const [mTab, setMTab] = useState<'order' | 'book' | 'positions' | 'orders'>('order');
  const [orderSheet, setOrderSheet] = useState<'long' | 'short' | null>(null);
  const [showAlerts, setShowAlerts] = useState(false);
  const { address } = useAuth();
  // Remount the chart when the B theme toggles so the canvas re-reads the B
  // palette (lightweight-charts colours are set in JS at init, not via CSS).
  const [dsbTheme] = useDesignTheme();

  useEffect(() => { loadMarketConfigs(); }, []);
  useMarketDataWs(selectedMarketId);
  useEffect(() => { if (address) warmupTradingConnection(address); }, [address]);

  // ?stage=<envelope> from the MCP stage_order tool — HMAC-verified server-side
  // before dispatch (identical to the A page; security-critical, unchanged).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const envelope = params.get('stage');
    if (envelope) window.history.replaceState({}, '', '/trade');
    if (!envelope) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.post('/api/mcp-stage/verify', { envelope });
        if (cancelled) return;
        const payload = data?.payload;
        if (payload && typeof payload.marketId === 'number'
          && (payload.side === 'long' || payload.side === 'short')
          && (payload.mode === 'market' || payload.mode === 'limit')
          && typeof payload.amount === 'number' && typeof payload.leverage === 'number') {
          setSelectedMarket(payload.marketId);
          setStagedOrder({
            marketId: payload.marketId, symbol: payload.symbol ?? '', side: payload.side,
            mode: payload.mode, amount: payload.amount, leverage: payload.leverage,
            price: payload.price ?? null, sl: payload.sl ?? null, tp: payload.tp ?? null,
            rationale: payload.rationale ?? null,
          });
        }
      } catch { /* verification failed — ignore */ }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const config = MARKETS[selectedMarketId];
  const market = markets[selectedMarketId];
  if (isLoading) return <TradeSkeleton />;
  const up = (market?.price_change_24h ?? 0) >= 0;

  const strip = (
    <div className="mstrip">
      <div className="pair">
        <MarketSelector marketIds={marketIds} selectedMarketId={selectedMarketId} onSelect={setSelectedMarket} />
        <span className="muted" style={{ fontWeight: 500 }}>/ USD Perpetual</span>
      </div>
      {market && (
        <>
          <div className="px" style={{ color: up ? 'var(--long)' : 'var(--short)' }}>${formatPrice(market.mark_price, config?.decimals ?? 2)}</div>
          <div className="m"><b className={up ? 'long-c' : 'short-c'}>{formatPercent(market.price_change_24h)}</b><span>24h change</span></div>
          <div className="m"><b>${formatPrice(market.mark_price, config?.decimals ?? 2)}</b><span>Mark</span></div>
          <div className="m"><b>${formatPrice(market.oracle_price ?? market.mark_price, config?.decimals ?? 2)}</b><span>Oracle</span></div>
          <div className="m"><b>${(market.daily_volume_usd ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</b><span>24h volume</span></div>
          <div className="m"><b>${(market.open_interest_usd ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</b><span>Open interest</span></div>
          {market.funding_rate != null && (
            <div className="m"><b className={market.funding_rate >= 0 ? '' : 'short-c'}>{(market.funding_rate * 100).toFixed(4)}%</b><span>Funding</span></div>
          )}
        </>
      )}
      <button className="btn sm ghost" style={{ marginLeft: 'auto' }} onClick={() => setShowAlerts(true)} title="Price alerts">Alerts</button>
    </div>
  );

  const orderPanel = (
    <div className="card" style={{ padding: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div className="tabs" style={{ margin: 0, padding: '0 12px' }}>
        <button aria-selected={rightTab === 'order'} onClick={() => setRightTab('order')}>Place order</button>
        <button aria-selected={rightTab === 'calc'} onClick={() => setRightTab('calc')}>Calculator</button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {rightTab === 'order' ? <OrderForm marketId={selectedMarketId} variant="b" /> : <div style={{ padding: 12 }}><PositionCalculator marketId={selectedMarketId} /></div>}
      </div>
    </div>
  );

  return (
    <div className="screen tbwrap">
      {showAlerts && <PriceAlertModal isOpen={showAlerts} onClose={() => setShowAlerts(false)} defaultMarketId={selectedMarketId} />}
      <EnableOneClickModal />

      {strip}

      {/* Desktop 3-column grid */}
      <div className="tgrid tdesk">
        <div className="card" style={{ padding: 0, minWidth: 0, display: 'flex', flexDirection: 'column', height: 'max(560px, calc(100vh - 300px))' }}>
          <Suspense fallback={<div className="empty" style={{ height: '100%' }}>Loading chart…</div>}><TradingChart key={`chart-${dsbTheme}`} marketId={selectedMarketId} symbol={config?.symbol ?? ''} /></Suspense>
        </div>
        <div className="card obook" style={{ padding: 0, height: 'max(560px, calc(100vh - 300px))', overflow: 'hidden' }}>
          <OrderBook marketId={selectedMarketId} />
        </div>
        <div className="opanel" style={{ height: 'max(560px, calc(100vh - 300px))', minHeight: 0 }}>
          {orderPanel}
        </div>
      </div>

      {/* Bottom row */}
      <div className="row two tdesk" style={{ marginTop: 12, gridTemplateColumns: '2fr 1fr' }}>
        <div className="card" style={{ padding: 0, overflow: 'hidden', minHeight: 150 }}><PositionsPanel /></div>
        <div className="card" style={{ overflowY: 'auto', minHeight: 150 }}><FundingComparison marketSymbol={config?.symbol ?? 'BTC'} /></div>
      </div>

      {/* Mobile (spec 4.3): price strip, collapsible chart (45% of the screen),
          then tabs Order / Book / Positions / Orders. The order form opens as a
          bottom sheet from big, spaced Long / Short buttons. */}
      <div className="tmob" style={{ display: 'none', flexDirection: 'column', gap: 12 }}>
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <button className="btn ghost" style={{ width: '100%', justifyContent: 'space-between', display: 'flex', borderRadius: 0 }}
                  onClick={() => setChartOpen((o) => !o)} aria-expanded={chartOpen}>
            <span>Chart</span><span className="dim">{chartOpen ? 'Hide' : 'Show'}</span>
          </button>
          {chartOpen && (
            <div style={{ height: '45vh', minHeight: 260 }}>
              <Suspense fallback={<div className="empty" style={{ height: '100%' }}>Loading chart…</div>}><TradingChart key={`chart-m-${dsbTheme}`} marketId={selectedMarketId} symbol={config?.symbol ?? ''} /></Suspense>
            </div>
          )}
        </div>
        <div className="tabs" role="tablist" style={{ margin: 0, overflowX: 'auto' }}>
          {(['order', 'book', 'positions', 'orders'] as const).map((t) => (
            <button key={t} role="tab" aria-selected={mTab === t} onClick={() => setMTab(t)} style={{ minHeight: 44 }}>
              {t === 'order' ? 'Order' : t === 'book' ? 'Book' : t === 'positions' ? 'Positions' : 'Orders'}
            </button>
          ))}
        </div>
        {mTab === 'order' && (
          <div className="card" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, padding: 16 }}>
            <button className="btn" style={{ minHeight: 56, background: 'var(--long)', color: '#04130c', fontWeight: 700, fontSize: 16 }}
                    onClick={() => setOrderSheet('long')}>Long</button>
            <button className="btn" style={{ minHeight: 56, background: 'var(--short)', color: '#fff', fontWeight: 700, fontSize: 16 }}
                    onClick={() => setOrderSheet('short')}>Short</button>
            <div style={{ gridColumn: '1 / -1' }}><PositionCalculator marketId={selectedMarketId} /></div>
          </div>
        )}
        {mTab === 'book' && <div className="card" style={{ padding: 0, overflow: 'hidden' }}><OrderBook marketId={selectedMarketId} /></div>}
        {mTab === 'positions' && <div className="card" style={{ padding: 0, overflow: 'hidden' }}><PositionsPanel initialTab="positions" /></div>}
        {mTab === 'orders' && <div className="card" style={{ padding: 0, overflow: 'hidden' }}><PositionsPanel initialTab="orders" /></div>}
        <div className="card" style={{ overflowY: 'auto' }}><FundingComparison marketSymbol={config?.symbol ?? 'BTC'} /></div>
        {orderSheet && (
          <div style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(0,0,0,.5)', display: 'flex', alignItems: 'flex-end' }}
               onClick={() => setOrderSheet(null)}>
            <div role="dialog" aria-label={`Place ${orderSheet} order`} onClick={(e) => e.stopPropagation()}
                 style={{ width: '100%', maxHeight: '88vh', overflowY: 'auto', background: 'var(--s1)', borderRadius: '16px 16px 0 0',
                   border: '1px solid var(--line)', paddingBottom: 'calc(12px + env(safe-area-inset-bottom))' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px' }}>
                <b>{orderSheet === 'long' ? 'Long' : 'Short'} {config?.symbol}</b>
                <button className="btn ghost" onClick={() => setOrderSheet(null)} aria-label="Close order sheet">Close</button>
              </div>
              <OrderForm key={orderSheet} marketId={selectedMarketId} variant="b" defaultSide={orderSheet} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
