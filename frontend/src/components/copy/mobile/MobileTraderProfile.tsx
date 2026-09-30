import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { clsx } from 'clsx';
import { useTrader } from '@/hooks/useCopyV1';
import Sparkline from '@/components/copy/Sparkline';
import MarketLogo from '@/components/copy/mobile/marketLogos';
import OpenDuration, { formatOpenedDate } from '@/components/copy/OpenDuration';
import { MARKETS } from '@/config/constants';
import type { EquityTimeframe } from '@/lib/copyApi';
import { formatCompact, formatPercent, formatPrice, formatSize, shortenAddress } from '@/lib/formatters';

interface Props {
  wallet: string;
  timeframe?: EquityTimeframe;
  exchange?: string;               // 'perpl' (default) | 'hl'
  isWatched: boolean;
  onWatchToggle: (wallet: string) => void;
  onSetupCopy: (wallet: string, name: string | null) => void;
  onClose: () => void;
}

const TF_LABEL: Record<EquityTimeframe, string> = { '24h': '24h', '7d': '7d', '30d': '30d', all: 'all-time' };

function rankCls(r?: number | null): string {
  return r === 1 ? 'g1' : r === 2 ? 'g2' : r === 3 ? 'g3' : '';
}

export default function MobileTraderProfile({ wallet, timeframe = 'all', exchange = 'perpl', isWatched, onWatchToggle, onSetupCopy, onClose }: Props) {
  const { detail, positions, positionsError, equity, loading } = useTrader(wallet, timeframe, exchange);
  const [open, setOpen] = useState(false);
  const closingRef = useRef(false);
  const head = detail?.stats;
  const name = detail?.profile?.display_name || shortenAddress(wallet);

  const handleClose = useCallback(() => {
    if (closingRef.current) return;
    closingRef.current = true;
    setOpen(false);
    window.setTimeout(onClose, 300); // let the slide-out finish
  }, [onClose]);

  // Slide in on mount; lock body scroll; wire hardware-back + Esc.
  useEffect(() => {
    const raf = requestAnimationFrame(() => setOpen(true));
    document.body.style.overflow = 'hidden';
    window.history.pushState({ mcProfile: true }, '');
    const onPop = () => handleClose();
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') { window.history.back(); } };
    window.addEventListener('popstate', onPop);
    window.addEventListener('keydown', onEsc);
    return () => {
      cancelAnimationFrame(raf);
      document.body.style.overflow = '';
      window.removeEventListener('popstate', onPop);
      window.removeEventListener('keydown', onEsc);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The back button / Esc route through history so Android hardware-back also closes.
  const requestClose = () => window.history.back();

  // Portal to <body> so the fixed overlay is positioned against the viewport and not
  // trapped/clipped by the app's scrolling, transformed <main> container.
  return createPortal(
    <div className={clsx('mc-profile', open && 'open')} role="dialog" aria-modal="true">
      <div className="mc-p-bar">
        <button className="mc-p-back" onClick={requestClose} aria-label="Back">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><polyline points="15 18 9 12 15 6" /></svg>
        </button>
        <span className="ttl">Trader profile</span>
      </div>

      <div className="mc-p-scroll">
        <div className="mc-p-hero">
          <div className={clsx('mc-rank', rankCls(head?.rank))}>{head?.rank ? `#${head.rank}` : '—'}</div>
          <div className="min-w-0">
            <div className="addr">{name}</div>
            <div className="full">{wallet}</div>
          </div>
        </div>

        <div className="mc-p-stats">
          <div className="mc-p-stat"><div className="k">PnL</div><div className="v" style={head?.pnl_total != null ? { color: head.pnl_total >= 0 ? 'var(--green)' : 'var(--red)' } : undefined}>{head?.pnl_total != null ? formatCompact(head.pnl_total) : '—'}</div></div>
          <div className="mc-p-stat"><div className="k">ROI</div><div className="v" style={head?.roi != null ? { color: head.roi >= 0 ? 'var(--green)' : 'var(--red)' } : undefined}>{head?.roi != null ? formatPercent(head.roi) : '—'}</div></div>
          <div className="mc-p-stat"><div className="k">Volume</div><div className="v">{head?.volume != null ? formatCompact(head.volume) : '—'}</div></div>
          <div className="mc-p-stat"><div className="k">On leaderboard</div><div className="v">{head?.on_leaderboard ? 'Yes' : 'No'}</div></div>
        </div>

        <div className="mc-p-sec">Equity curve <span className="ro">· {TF_LABEL[timeframe]} · {positions.length} open</span></div>
        <div className="mc-p-chart">
          {equity.length >= 2
            ? <Sparkline points={equity} width={340} height={114} animate />
            : <div className="mc-nochart">{loading ? 'Loading…' : 'No equity history yet.'}</div>}
        </div>

        <div className="mc-p-sec">Live positions <span className="ro">· read-only</span></div>
        {loading ? (
          <div className="mc-nochart" style={{ padding: '22px 0' }}>Checking active trades…</div>
        ) : positions.length === 0 ? (
          <div className="mc-nochart" style={{ padding: '22px 0' }}>{positionsError ? 'Open trades unavailable.' : 'No open positions right now.'}</div>
        ) : (
          positions.map((p, i) => {
            const dec = MARKETS[p.market_id]?.decimals ?? 2;
            return (
              <div key={`${p.market_id}-${i}`} className="mc-poscard">
                <div className="r1">
                  <MarketLogo symbol={p.symbol} size={26} />
                  <span className="mc-pos-mkt">{p.symbol}</span>
                  <span className={clsx('mc-side', p.side)}>{p.side.toUpperCase()}</span>
                  <span className="mc-pos-pnl" style={{ color: p.pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{formatCompact(p.pnl)}</span>
                </div>
                <div className="mc-pos-grid">
                  <div className="mc-pos-cell"><div className="k">Size</div><div className="v">{formatSize(p.size)}</div></div>
                  <div className="mc-pos-cell"><div className="k">Entry</div><div className="v">{formatPrice(p.entry_price, dec)}</div></div>
                  <div className="mc-pos-cell"><div className="k">Mark</div><div className="v">{formatPrice(p.mark_price, dec)}</div></div>
                  <div className="mc-pos-cell"><div className="k">Lev</div><div className="v">{p.leverage}x</div></div>
                  <div className="mc-pos-cell"><div className="k">Opened</div><div className="v">{formatOpenedDate(p.opened_at)}</div></div>
                  <div className="mc-pos-cell"><div className="k">Active</div><div className="v"><OpenDuration openedAt={p.opened_at} /></div></div>
                </div>
              </div>
            );
          })
        )}
      </div>

      <div className="mc-p-foot">
        <button className="mc-btn" onClick={() => onWatchToggle(wallet)} style={isWatched ? { color: 'var(--accent-2)' } : undefined}>
          {isWatched ? 'Watching' : 'Watch'}
        </button>
        <button className="mc-btn mc-btn-copy" onClick={() => onSetupCopy(wallet, detail?.profile?.display_name ?? null)}>Set up copy</button>
      </div>
    </div>,
    document.body,
  );
}
