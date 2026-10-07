export const API_BASE = import.meta.env.VITE_API_URL || '';
export const WS_BASE = import.meta.env.VITE_WS_URL || `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;

// Copy Trading gate for LIVE copy order placement (real Perpl orders), mirrors
// backend settings.COPY_LIVE_ENABLED. Env-controlled via VITE_COPY_LIVE_ENABLED;
// default false so the copy UI never places a real order unless deliberately turned
// on. When false, the live "Copy" CTAs are shown but disabled/observe-only. This does
// NOT affect manual terminal trading (OrderForm uses useCopyTrade with source:'manual').
export const COPY_LIVE_ENABLED = import.meta.env.VITE_COPY_LIVE_ENABLED === 'true';

// Primary copy product mode (mirrors backend COPY_MODE): 'live_manual' is the main
// product (user confirms each copy -> real order). 'live_auto' is NOT implemented yet.
// 'paper' is an optional sandbox only.
export const COPY_MODE = (import.meta.env.VITE_COPY_MODE as 'live_manual' | 'live_auto' | 'paper') || 'live_manual';

// DISPLAY FALLBACK ONLY — NOT the source of truth for which markets are active.
// The live market list comes from the dynamic registry (GET /api/markets via
// useMarkets() / MARKET_CONFIGS). This map is a best-effort label/decimals fallback
// for historical rows and offline rendering; active selectors MUST use the registry.
// Kept roughly in sync with current Perpl listings (SOL re-listed at id 31; HYPE=40, ZEC=50).
export const MARKETS: Record<number, { symbol: string; name: string; decimals: number }> = {
  1: { symbol: 'BTC', name: 'Bitcoin', decimals: 1 },
  10: { symbol: 'MON', name: 'Monad', decimals: 6 },
  20: { symbol: 'ETH', name: 'Ethereum', decimals: 2 },
  31: { symbol: 'SOL', name: 'Solana', decimals: 3 },
  40: { symbol: 'HYPE', name: 'Hyperliquid', decimals: 4 },
  50: { symbol: 'ZEC', name: 'Zcash', decimals: 2 },
  90: { symbol: 'PUMP', name: 'Pump.fun', decimals: 6 },
};

export type MarketId = number;

// Fallback id list only — prefer the dynamic registry for active markets.
export const MARKET_IDS = Object.keys(MARKETS).map(Number);
