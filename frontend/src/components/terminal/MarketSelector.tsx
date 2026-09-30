import { useState, useMemo, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useMarketStore } from '@/stores/marketStore';
import { MARKETS } from '@/config/constants';
import { formatPrice, formatCompact } from '@/lib/formatters';

const DOT: Record<string, string> = { BTC: '#f7931a', ETH: '#627eea', MON: '#836EF9', SOL: '#14f195', HYPE: '#3ddc84', ZEC: '#ecb244' };
const dotColor = (sym: string) => DOT[sym] || 'var(--accent)';

// Compact market switcher for the Trade top bar — replaces the pill row with a
// searchable dropdown that also carries the starred favorites from the old
// sidebar (shared localStorage key 'perpl-fav-markets').
//
// The menu is PORTALED to <body>: the market header uses overflow-x-auto (which
// forces overflow-y:auto too), so an in-flow absolute dropdown gets clipped to
// the header height. A fixed-position portal escapes that.
export default function MarketSelector({
  marketIds,
  selectedMarketId,
  onSelect,
}: {
  marketIds: number[];
  selectedMarketId: number;
  onSelect: (id: number) => void;
}) {
  const markets = useMarketStore((s) => s.markets);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const [favorites, setFavorites] = useState<number[]>(() => {
    try { return JSON.parse(localStorage.getItem('perpl-fav-markets') || '[]'); } catch { return []; }
  });
  const toggleFav = (id: number, e: React.MouseEvent) => {
    e.stopPropagation();
    setFavorites((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      try { localStorage.setItem('perpl-fav-markets', JSON.stringify(next)); } catch {}
      return next;
    });
  };

  const symbolOf = (id: number) => markets[id]?.symbol || MARKETS[id]?.symbol || `#${id}`;
  const decimalsOf = (id: number) => MARKETS[id]?.decimals ?? (markets[id] as any)?.price_decimals ?? 2;

  const q = search.trim().toLowerCase();
  const filtered = useMemo(
    () => marketIds.filter((id) => !q || symbolOf(id).toLowerCase().includes(q)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [marketIds, q, markets],
  );
  const ordered = useMemo(() => {
    const star = filtered.filter((id) => favorites.includes(id));
    const rest = filtered.filter((id) => !favorites.includes(id));
    return [...star, ...rest];
  }, [filtered, favorites]);

  const place = () => {
    const r = btnRef.current?.getBoundingClientRect();
    if (r) setCoords({ top: r.bottom + 6, left: r.left });
  };
  const openMenu = () => { place(); setOpen(true); };

  // Close / reposition on outside-click, Esc, scroll, resize.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!btnRef.current?.contains(t) && !menuRef.current?.contains(t)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    const onReposition = () => place();
    const t = setTimeout(() => {
      document.addEventListener('click', onDoc);
      document.addEventListener('keydown', onKey);
      window.addEventListener('scroll', onReposition, true);
      window.addEventListener('resize', onReposition);
    }, 0);
    return () => {
      clearTimeout(t);
      document.removeEventListener('click', onDoc);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onReposition, true);
      window.removeEventListener('resize', onReposition);
    };
  }, [open]);

  const selSym = symbolOf(selectedMarketId);

  return (
    <>
      <button
        ref={btnRef}
        onClick={() => (open ? setOpen(false) : openMenu())}
        className="rd-seg flex items-center gap-2 px-3 py-1.5 rounded-[9px] font-bold rd-mono text-[14px]"
        style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)' }}
      >
        <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: dotColor(selSym) }} />
        {selSym}
        <span className="text-[11px] font-medium" style={{ color: 'var(--dim)' }}>/USD</span>
        <svg className="w-3 h-3 opacity-60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && coords && createPortal(
        <div
          ref={menuRef}
          className="fixed z-[200] w-[300px] rounded-[12px] p-2 shadow-2xl"
          style={{ top: coords.top, left: coords.left, background: 'var(--surface)', border: '1px solid var(--border-strong)' }}
        >
          <div className="relative mb-1.5">
            <svg className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: 'var(--faint)' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeWidth={2} d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" />
            </svg>
            <input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search markets"
              className="rd-input rd-sans w-full text-[12px] pl-8 pr-2 py-1.5"
            />
          </div>

          <div className="max-h-[340px] overflow-y-auto rd-scroll">
            {ordered.length === 0 && (
              <div className="px-2 py-4 text-[11px]" style={{ color: 'var(--faint)' }}>No markets match "{search}".</div>
            )}
            {ordered.map((id) => {
              const sym = symbolOf(id);
              const m = markets[id];
              const dec = decimalsOf(id);
              const change = m?.price_change_24h ?? 0;
              const up = change >= 0;
              const active = id === selectedMarketId;
              const starred = favorites.includes(id);
              return (
                <button
                  key={id}
                  onClick={() => { onSelect(id); setOpen(false); }}
                  className="w-full flex items-center gap-2.5 px-2 py-2 rounded-[8px] transition-colors hover:bg-[var(--surface-2)]"
                  style={active ? { background: 'var(--accent-soft)' } : {}}
                >
                  <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: dotColor(sym) }} />
                  <div className="flex flex-col items-start min-w-0">
                    <span className="text-[12.5px] font-bold rd-mono" style={{ color: active ? 'var(--accent-2)' : 'var(--text)' }}>{sym}</span>
                    <span className="text-[9px]" style={{ color: 'var(--faint)' }}>{formatCompact(m?.daily_volume_usd ?? 0)} vol</span>
                  </div>
                  <div className="ml-auto flex flex-col items-end">
                    <span className="text-[12px] font-semibold tabular-nums rd-mono" style={{ color: 'var(--text)' }}>
                      {m ? `$${formatPrice(m.mark_price, dec)}` : '—'}
                    </span>
                    <span className="text-[10px] font-semibold tabular-nums" style={{ color: up ? 'var(--green)' : 'var(--red)' }}>
                      {up ? '+' : ''}{change.toFixed(2)}%
                    </span>
                  </div>
                  <span
                    onClick={(e) => toggleFav(id, e)}
                    title={starred ? 'Unstar' : 'Star'}
                    className="text-[13px] leading-none pl-1"
                    style={{ color: starred ? '#f7d774' : 'var(--faint)', opacity: starred ? 1 : 0.45 }}
                  >
                    ★
                  </span>
                </button>
              );
            })}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
