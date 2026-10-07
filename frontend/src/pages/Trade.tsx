import { useEffect, useState, useMemo } from 'react';
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
import TradingChart from '@/components/terminal/TradingChart';
import FundingComparison from '@/components/terminal/FundingComparison';
import OrderBook from '@/components/terminal/OrderBook';
import OrderForm from '@/components/terminal/OrderForm';
import PositionCalculator from '@/components/terminal/PositionCalculator';
import PositionsPanel from '@/components/terminal/PositionsPanel';
import { TradeSkeleton } from '@/components/common/Skeleton';
import EnableOneClickModal from '@/components/settings/EnableOneClickModal';
import MarketSelector from '@/components/terminal/MarketSelector';

// Market accent dot (visual only; falls back to the brand accent for unknown symbols).

function Stat({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'up' | 'down' | 'neutral' }) {
  return (
    <div className="pl-4 ml-1 border-l" style={{ borderColor: 'var(--border)' }}>
      <div className="text-[9px] font-bold uppercase tracking-[0.8px] rd-sans" style={{ color: 'var(--faint)' }}>{label}</div>
      <div className="rd-mono font-semibold text-[13px] mt-0.5 tabular-nums whitespace-nowrap"
        style={{ color: tone === 'up' ? 'var(--green)' : tone === 'down' ? 'var(--red)' : 'var(--text)' }}>{value}</div>
    </div>
  );
}

export default function TradePage() {
  const { markets, isLoading } = useMarketData();
  // Dynamic market tabs from the live registry; fall back to static ids only while
  // loading. A delisted market drops out; a new one appears automatically.
  const marketIds = useMemo(() => {
    const ids = Object.keys(markets || {}).map(Number);
    return ids.length ? ids.sort((a, b) => a - b) : MARKET_IDS;
  }, [markets]);
  const selectedMarketId = useTradingStore((s) => s.selectedMarketId);
  const setSelectedMarket = useTradingStore((s) => s.setSelectedMarket);
  const setStagedOrder = useTradingStore((s) => s.setStagedOrder);
  const [rightTab, setRightTab] = useState<'order' | 'calc'>('order');
  const [showAlerts, setShowAlerts] = useState(false);
  // Collapsible right-hand panels (persisted) — hiding either widens the chart.
  const [showOrderBook, setShowOrderBook] = useState(() => {
    try { return localStorage.getItem('trade-show-orderbook') !== '0'; } catch { return true; }
  });
  const [showOrderPanel, setShowOrderPanel] = useState(() => {
    try { return localStorage.getItem('trade-show-orderpanel') !== '0'; } catch { return true; }
  });
  const { address } = useAuth();

  useEffect(() => { try { localStorage.setItem('trade-show-orderbook', showOrderBook ? '1' : '0'); } catch {} }, [showOrderBook]);
  useEffect(() => { try { localStorage.setItem('trade-show-orderpanel', showOrderPanel ? '1' : '0'); } catch {} }, [showOrderPanel]);

  useEffect(() => { loadMarketConfigs(); }, []);

  // Live L2 order book for the selected market (feeds tradingStore.orderBook,
  // rendered by the OrderBook panel). Mounted once here — the panel renders
  // twice (desktop + mobile layouts) and must share one WS.
  useMarketDataWs(selectedMarketId);

  // Pre-connect the trading WS in the background (API key / stored nonce, no
  // popup) so the first order click doesn't pay connection latency.
  useEffect(() => {
    if (address) warmupTradingConnection(address);
  }, [address]);

  // Parse ?stage=<envelope> from MCP stage_order tool.
  //
  // SECURITY: the envelope is HMAC-signed by the backend and bound to the
  // user_id of the MCP token that produced it. We CANNOT trust the client-side
  // decode — an attacker could craft any payload. Instead we POST the raw
  // envelope to /api/mcp-stage/verify, which validates signature + expiry +
  // uid==current user, and returns the validated payload. Only then do we
  // dispatch into the order form.
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
        if (
          payload &&
          typeof payload.marketId === 'number' &&
          (payload.side === 'long' || payload.side === 'short') &&
          (payload.mode === 'market' || payload.mode === 'limit') &&
          typeof payload.amount === 'number' &&
          typeof payload.leverage === 'number'
        ) {
          setSelectedMarket(payload.marketId);
          setStagedOrder({
            marketId: payload.marketId,
            symbol: payload.symbol ?? '',
            side: payload.side,
            mode: payload.mode,
            amount: payload.amount,
            leverage: payload.leverage,
            price: payload.price ?? null,
            sl: payload.sl ?? null,
            tp: payload.tp ?? null,
            rationale: payload.rationale ?? null,
          });
        }
      } catch {
        // verification failed (expired, wrong user, bad signature) — ignore
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const config = MARKETS[selectedMarketId];
  const market = markets[selectedMarketId];

  if (isLoading) return <TradeSkeleton />;

  const up = (market?.price_change_24h ?? 0) >= 0;

  // ---------- Market header card (tabs + live price + real stats) ----------
  const marketHeader = (
    <div className="rd-card-lg rd-sans flex items-center gap-3 px-4 py-2.5 overflow-x-auto scrollbar-hide shrink-0">
      <div className="shrink-0 pr-3 border-r" style={{ borderColor: 'var(--border)' }}>
        <MarketSelector marketIds={marketIds} selectedMarketId={selectedMarketId} onSelect={setSelectedMarket} />
      </div>
      {market && (
        <>
          <div className="rd-mono font-extrabold text-[20px] tabular-nums shrink-0" style={{ color: up ? 'var(--green)' : 'var(--red)' }}>
            ${formatPrice(market.mark_price, config?.decimals ?? 2)}
          </div>
          <Stat label="24h Change" value={formatPercent(market.price_change_24h)} tone={up ? 'up' : 'down'} />
          <Stat label="Mark" value={`$${formatPrice(market.mark_price, config?.decimals ?? 2)}`} />
          <Stat label="Oracle" value={`$${formatPrice(market.oracle_price ?? market.mark_price, config?.decimals ?? 2)}`} />
          <Stat label="24h Vol" value={`$${(market.daily_volume_usd ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
          <Stat label="Open Interest" value={`$${(market.open_interest_usd ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
          {market.funding_rate != null && (
            <Stat label="Funding" value={`${(market.funding_rate * 100).toFixed(4)}%`} tone={market.funding_rate >= 0 ? 'down' : 'up'} />
          )}
        </>
      )}
      <div className="ml-auto shrink-0 flex items-center gap-1">
        {/* Panel toggles (desktop) — hide/show the order book & order ticket to widen the chart */}
        <button
          onClick={() => setShowOrderBook((v) => !v)}
          className="hidden md:flex shrink-0 w-8 h-8 rounded-lg items-center justify-center transition-colors"
          style={showOrderBook ? { background: 'var(--accent-soft)', color: 'var(--accent-2)' } : { color: 'var(--dim)' }}
          title={showOrderBook ? 'Hide Order Book' : 'Show Order Book'}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 6h16M4 10h10M4 14h16M4 18h10" />
          </svg>
        </button>
        <button
          onClick={() => setShowOrderPanel((v) => !v)}
          className="hidden md:flex shrink-0 w-8 h-8 rounded-lg items-center justify-center transition-colors"
          style={showOrderPanel ? { background: 'var(--accent-soft)', color: 'var(--accent-2)' } : { color: 'var(--dim)' }}
          title={showOrderPanel ? 'Hide Place Order' : 'Show Place Order'}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
          </svg>
        </button>
        <button
          onClick={() => setShowAlerts(true)}
          className="shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-colors"
          style={{ color: 'var(--dim)' }}
          title="Price Alerts"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
        </button>
      </div>
    </div>
  );

  const orderPanel = (
    <div className="flex flex-col h-full">
      <div className="flex shrink-0 border-b" style={{ borderColor: 'var(--border)' }}>
        <button onClick={() => setRightTab('order')} className={clsx('flex-1 py-2.5 text-xs font-semibold rd-sans transition-colors', rightTab === 'order' ? 'text-accent border-b-2 border-accent' : 'text-text-secondary')}>Place Order</button>
        <button onClick={() => setRightTab('calc')} className={clsx('flex-1 py-2.5 text-xs font-semibold rd-sans transition-colors', rightTab === 'calc' ? 'text-accent border-b-2 border-accent' : 'text-text-secondary')}>Calculator</button>
      </div>
      <div className="flex-1 overflow-y-auto rd-scroll">
        {rightTab === 'order' ? <OrderForm marketId={selectedMarketId} /> : <div className="p-3"><PositionCalculator marketId={selectedMarketId} /></div>}
      </div>
    </div>
  );

  return (
    <>
      {showAlerts && <PriceAlertModal isOpen={showAlerts} onClose={() => setShowAlerts(false)} defaultMarketId={selectedMarketId} />}
      <EnableOneClickModal />

      {/* ---------- Desktop: floating-card terminal ---------- */}
      {/* min-h (not fixed h) + no overflow-hidden -> the page scrolls on short viewports
          instead of crushing the chart. The workspace gets a viewport-based height with a
          generous 560px floor so the candle chart always dominates; the bottom row is
          compact so it can't steal the chart's height. */}
      <div className="hidden md:flex flex-col min-h-[calc(100vh-56px)]" style={{ padding: '14px 18px 18px', gap: 14 }}>
        {marketHeader}
        {/* Workspace row — the chart card is the primary surface. 680px floor keeps the
            candle dominant (even with RSI+MACD panes); on taller screens it grows with the
            viewport, on shorter ones the page scrolls rather than crushing the chart. */}
        <div className="flex shrink-0" style={{ gap: 14, height: 'max(680px, calc(100vh - 320px))' }}>
          <div className="rd-card-lg flex-1 min-w-0 flex flex-col">
            <TradingChart marketId={selectedMarketId} symbol={config?.symbol ?? ''} />
          </div>
          {showOrderBook && (
            <div className="rd-card-lg w-[248px] shrink-0">
              <OrderBook marketId={selectedMarketId} />
            </div>
          )}
          {showOrderPanel && (
            <div className="rd-card-lg w-[336px] shrink-0">
              {orderPanel}
            </div>
          )}
        </div>
        {/* Bottom row: positions + funding — compact, secondary */}
        <div className="flex shrink-0 h-[150px]" style={{ gap: 14 }}>
          <div className="rd-card-lg flex-[2] min-w-0 overflow-hidden">
            <PositionsPanel />
          </div>
          <div className="rd-card-lg flex-1 min-w-0 overflow-y-auto rd-scroll">
            <FundingComparison marketSymbol={config?.symbol ?? 'BTC'} />
          </div>
        </div>
      </div>

      {/* ---------- Mobile: stacked cards ---------- */}
      <div className="flex md:hidden flex-col gap-3 p-3">
        {marketHeader}
        <div className="rd-card-lg h-[420px]"><TradingChart marketId={selectedMarketId} symbol={config?.symbol ?? ''} /></div>
        <div className="rd-card-lg">{orderPanel}</div>
        <div className="rd-card-lg"><PositionsPanel /></div>
        <div className="rd-card-lg"><OrderBook marketId={selectedMarketId} /></div>
        <div className="rd-card-lg"><FundingComparison marketSymbol={config?.symbol ?? 'BTC'} /></div>
      </div>
    </>
  );
}
