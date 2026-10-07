import { useState, useMemo } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { clsx } from 'clsx';
import { useMarketStore } from '@/stores/marketStore';
import { useTradingStore } from '@/stores/tradingStore';
import { MARKETS, MARKET_IDS } from '@/config/constants';
import { formatPrice, formatCompact } from '@/lib/formatters';

const COIN_META: Record<string, { color: string; gradient: string }> = {
  BTC: { color: '#f7931a', gradient: 'from-[#f7931a]/15 to-transparent' },
  ETH: { color: '#627eea', gradient: 'from-[#627eea]/15 to-transparent' },
  MON: { color: '#a78bfa', gradient: 'from-[#a78bfa]/15 to-transparent' },
  SOL: { color: '#14f195', gradient: 'from-[#14f195]/15 to-transparent' },
};

function CoinLogo({ symbol, size = 20 }: { symbol: string; size?: number }) {
  const s = size;
  if (symbol === 'BTC') {
    return (
      <svg width={s} height={s} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="16" fill="#f7931a"/>
        <path d="M22.5 14.2c.3-2-1.2-3.1-3.3-3.8l.7-2.7-1.7-.4-.7 2.6c-.4-.1-.9-.2-1.3-.3l.7-2.7-1.7-.4-.7 2.7c-.4-.1-.7-.2-1-.2l-2.3-.6-.4 1.8s1.2.3 1.2.3c.7.2.8.6.8 1l-.8 3.2c0 .1.1.1.1.1l-.1 0-1.1 4.5c-.1.2-.3.5-.8.4 0 0-1.2-.3-1.2-.3l-.8 1.9 2.2.5c.4.1.8.2 1.2.3l-.7 2.8 1.7.4.7-2.7c.5.1.9.2 1.3.3l-.7 2.7 1.7.4.7-2.8c2.8.5 4.9.3 5.8-2.2.7-2-.1-3.2-1.5-3.9 1.1-.3 1.9-1 2.1-2.5zm-3.7 5.2c-.5 2-3.9.9-5 .7l.9-3.6c1.1.3 4.7.8 4.1 2.9zm.5-5.3c-.5 1.8-3.3.9-4.2.7l.8-3.2c.9.2 3.9.7 3.4 2.5z" fill="white"/>
      </svg>
    );
  }
  if (symbol === 'ETH') {
    return (
      <svg width={s} height={s} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="16" fill="#627eea"/>
        <path d="M16 4l-.2.5v15.9l.2.1 7.3-4.3L16 4z" fill="white" fillOpacity=".6"/>
        <path d="M16 4L8.7 16.2l7.3 4.3V4z" fill="white"/>
        <path d="M16 22l-.1.1v5.6l.1.3 7.3-10.3L16 22z" fill="white" fillOpacity=".6"/>
        <path d="M16 28v-6l-7.3-4.3L16 28z" fill="white"/>
        <path d="M16 20.5l7.3-4.3L16 12.5v8z" fill="white" fillOpacity=".2"/>
        <path d="M8.7 16.2l7.3 4.3v-8l-7.3 3.7z" fill="white" fillOpacity=".5"/>
      </svg>
    );
  }
  if (symbol === 'SOL') {
    return (
      <svg width={s} height={s} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="16" fill="#000"/>
        <linearGradient id="sol-g" x1="6" y1="24" x2="26" y2="8">
          <stop stopColor="#9945FF"/>
          <stop offset="0.5" stopColor="#14F195"/>
          <stop offset="1" stopColor="#00C2FF"/>
        </linearGradient>
        <path d="M9.2 20.8a.6.6 0 01.4-.2h15.6a.3.3 0 01.2.5l-2.6 2.6a.6.6 0 01-.4.2H6.8a.3.3 0 01-.2-.5l2.6-2.6z" fill="url(#sol-g)"/>
        <path d="M9.2 8.2a.6.6 0 01.4-.2h15.6a.3.3 0 01.2.5l-2.6 2.6a.6.6 0 01-.4.2H6.8a.3.3 0 01-.2-.5L9.2 8.2z" fill="url(#sol-g)"/>
        <path d="M22.8 14.4a.6.6 0 00-.4-.2H6.8a.3.3 0 00-.2.5l2.6 2.6a.6.6 0 00.4.2h15.6a.3.3 0 00.2-.5l-2.6-2.6z" fill="url(#sol-g)"/>
      </svg>
    );
  }
  if (symbol === 'MON') {
    return (
      <svg width={s} height={s} viewBox="0 0 480 480" fill="none">
        <rect width="480" height="480" rx="240" fill="#6E54FF"/>
        <path d="M240.135 90C196.78 90 90 196.68 90 240C90 283.318 196.78 390 240.135 390C283.491 390 390.273 283.316 390.273 240C390.273 196.682 283.493 90 240.135 90ZM216.739 325.774C198.457 320.796 149.302 234.89 154.285 216.624C159.268 198.357 245.251 149.248 263.533 154.226C281.817 159.204 330.971 245.108 325.989 263.376C321.005 281.642 235.023 330.752 216.739 325.774Z" fill="white"/>
      </svg>
    );
  }
  // Generic fallback for any new/unknown market (e.g. HYPE) — symbol initial.
  return (
    <div
      style={{ width: s, height: s }}
      className="rounded-full bg-accent/20 border border-accent/30 flex items-center justify-center text-accent font-bold"
    >
      <span style={{ fontSize: s * 0.42 }}>{(symbol || '?').slice(0, 1)}</span>
    </div>
  );
}

// Simple mini bar chart showing relative 24h volume
function VolumeBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="w-full h-[3px] bg-text-secondary/5 rounded-full overflow-hidden">
      <div className="h-full rounded-full bg-accent/30 transition-all duration-500" style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const markets = useMarketStore((s) => s.markets);
  const selectedMarketId = useTradingStore((s) => s.selectedMarketId);
  const setSelectedMarket = useTradingStore((s) => s.setSelectedMarket);

  const handleMarketClick = (id: number) => {
    setSelectedMarket(id);
    navigate('/trade');
  };

  const maxVol = Math.max(...Object.values(markets).map((m) => m.daily_volume_usd ?? 0), 1);

  // Dynamic market list: ids actually returned by the live registry (/api/markets).
  // Falls back to the static id list only while the store is still loading, so a
  // delisted market (e.g. SOL) disappears and a new one (e.g. HYPE) appears.
  const marketIds = useMemo(() => {
    const ids = Object.keys(markets).map(Number);
    return ids.length ? ids.sort((a, b) => a - b) : MARKET_IDS;
  }, [markets]);

  // Search + favorites (persisted) — favorites pinned to the top.
  const [search, setSearch] = useState('');
  const [favorites, setFavorites] = useState<number[]>(() => {
    try { return JSON.parse(localStorage.getItem('perpl-fav-markets') || '[]'); } catch { return []; }
  });
  const toggleFav = (id: number, e: { stopPropagation: () => void }) => {
    e.stopPropagation();
    setFavorites((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      try { localStorage.setItem('perpl-fav-markets', JSON.stringify(next)); } catch {}
      return next;
    });
  };
  const symbolOf = (id: number) => markets[id]?.symbol || MARKETS[id]?.symbol || `#${id}`;
  const q = search.trim().toLowerCase();
  const filteredIds = useMemo(
    () => marketIds.filter((id) => !q || symbolOf(id).toLowerCase().includes(q) || (MARKETS[id]?.name || '').toLowerCase().includes(q)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [marketIds, q, markets],
  );
  const starredIds = filteredIds.filter((id) => favorites.includes(id));
  const otherIds = filteredIds.filter((id) => !favorites.includes(id));

  return (
    <aside
      className={clsx(
        'hidden lg:flex flex-col bg-bg-secondary border-r border-text-secondary/10 transition-all duration-200 shrink-0',
        collapsed ? 'w-[56px]' : 'w-[220px]',
      )}
    >
      {/* Header */}
      <div className={clsx(
        'flex items-center border-b border-text-secondary/10 shrink-0',
        collapsed ? 'h-10 justify-center' : 'h-10 justify-between px-4',
      )}>
        {!collapsed && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-text-secondary tracking-[1.5px] uppercase rd-sans">Markets</span>
            <span className="rd-mono text-[10px] px-1.5 py-0.5 rounded-[6px]" style={{ background: 'var(--surface-2)', color: 'var(--dim)' }}>{marketIds.length}</span>
          </div>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="w-6 h-6 rounded flex items-center justify-center text-text-secondary/40 hover:text-text-primary transition-colors"
        >
          <svg className={clsx('w-3 h-3 transition-transform', collapsed && 'rotate-180')} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
      </div>

      {/* Search (expanded) */}
      {!collapsed && (
        <div className="px-2.5 pt-2.5 pb-1.5">
          <div className="relative">
            <svg className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: 'var(--faint)' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeWidth={2} d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" />
            </svg>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search markets"
              className="rd-input rd-sans w-full text-[12px] pl-8 pr-2 py-1.5"
            />
          </div>
        </div>
      )}

      {/* Market list (favorites pinned first) */}
      <div className="flex-1 overflow-y-auto py-1 rd-scroll">
        {!collapsed && filteredIds.length === 0 && (
          <div className="px-3 py-4 text-[11px]" style={{ color: 'var(--faint)' }}>No markets match "{search}".</div>
        )}
        {!collapsed && starredIds.length > 0 && (
          <div className="px-3 pt-1 pb-1 text-[9px] font-bold tracking-[1.2px] uppercase rd-sans" style={{ color: 'var(--faint)' }}>★ Starred</div>
        )}
        {[...starredIds, ...otherIds].map((id) => {
          const market = markets[id];
          // Prefer live registry symbol; fall back to the static meta map for label/decimals.
          const config = MARKETS[id] || {
            symbol: market?.symbol || `#${id}`,
            name: market?.symbol || `Market ${id}`,
            decimals: (market as any)?.price_decimals ?? 2,
          };
          const isSelected = selectedMarketId === id && location.pathname.startsWith('/trade');
          const change = market?.price_change_24h ?? 0;
          const isUp = change >= 0;
          const meta = COIN_META[config.symbol] || COIN_META.BTC;

          if (collapsed) {
            return (
              <div
                key={id}
                onClick={() => handleMarketClick(id)}
                title={`${config.symbol}/USD`}
                className={clsx(
                  'relative flex flex-col items-center py-3 cursor-pointer transition-all',
                  isSelected ? 'bg-bg-card' : 'hover:bg-bg-card/40',
                )}
              >
                {isSelected && <div className="absolute left-0 top-2 bottom-2 w-[2px] rounded-r bg-accent" />}
                <CoinLogo symbol={config.symbol} size={22} />
                <span className="text-[10px] font-semibold text-text-primary mt-0.5">{config.symbol}</span>
                {market && (
                  <span className={clsx('text-[8px] font-medium', isUp ? 'text-success' : 'text-danger')}>
                    {isUp ? '+' : ''}{change.toFixed(1)}%
                  </span>
                )}
              </div>
            );
          }

          return (
            <div
              key={id}
              onClick={() => handleMarketClick(id)}
              className={clsx(
                'relative mx-2 my-0.5 rounded-lg cursor-pointer transition-all',
                isSelected
                  ? `bg-gradient-to-r ${meta.gradient} bg-bg-card`
                  : 'hover:bg-bg-card/40',
              )}
            >
              {isSelected && <div className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-r" style={{ backgroundColor: meta.color }} />}

              <div className="px-3 py-2.5">
                {/* Row 1: Symbol + Price */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <CoinLogo symbol={config.symbol} size={24} />
                    <div>
                      <span className="text-[12px] font-bold text-text-primary">{config.symbol}</span>
                      <span className="text-[10px] text-text-secondary/40 ml-0.5">/USD</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {market && (
                      <span className={clsx(
                        'text-[10px] font-bold tabular-nums px-1.5 py-0.5 rounded',
                        isUp ? 'text-success bg-success/8' : 'text-danger bg-danger/8',
                      )}>
                        {isUp ? '+' : ''}{change.toFixed(2)}%
                      </span>
                    )}
                    <button
                      onClick={(e) => toggleFav(id, e)}
                      title={favorites.includes(id) ? 'Unstar' : 'Star'}
                      aria-label={favorites.includes(id) ? 'Unstar market' : 'Star market'}
                      className="text-[13px] leading-none transition-opacity"
                      style={{ color: favorites.includes(id) ? '#f7d774' : 'var(--faint)', opacity: favorites.includes(id) ? 1 : 0.4 }}
                    >
                      ★
                    </button>
                  </div>
                </div>

                {/* Row 2: Price + Volume */}
                {market ? (
                  <div className="mt-1.5">
                    <div className="flex items-baseline justify-between">
                      <span className="text-[13px] font-semibold text-text-primary tabular-nums">
                        ${formatPrice(market.mark_price, config.decimals)}
                      </span>
                      <span className="text-[9px] text-text-secondary/50 tabular-nums">
                        {formatCompact(market.daily_volume_usd ?? 0)}
                      </span>
                    </div>
                    <div className="mt-1.5">
                      <VolumeBar value={market.daily_volume_usd ?? 0} max={maxVol} />
                    </div>
                  </div>
                ) : (
                  <div className="mt-1.5 space-y-1">
                    <div className="h-3.5 w-20 bg-bg-card rounded animate-pulse" />
                    <div className="h-[3px] w-full bg-bg-card rounded animate-pulse" />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer */}
      {!collapsed && (
        <div className="px-4 py-2.5 border-t border-text-secondary/10">
          <div className="flex items-center justify-between text-[9px]">
            <span className="text-text-secondary/40">SMINDEX on Monad</span>
            <div className="flex items-center gap-1">
              <div className="w-1 h-1 rounded-full bg-success animate-pulse" />
              <span className="text-text-secondary/40">Live</span>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
