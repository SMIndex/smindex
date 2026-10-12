import { useState } from 'react';
import { clsx } from 'clsx';
import { useMarketStore } from '@/stores/marketStore';
import type { LivePosition } from '@/lib/copyApi';
import { MARKETS } from '@/config/constants';
import { formatUSD, formatPrice, formatSize, shortenAddress } from '@/lib/formatters';
import LiveCloseModal from '@/components/copy/LiveCloseModal';

import { useAuth } from '@/hooks/useAuth';
import { usePerplAccount } from '@/hooks/usePerplAccount';
import { useEnsurePerpl } from '@/hooks/useEnsurePerpl';
import { triggerPrice, triggersFor } from '@/lib/accountStore';
import SlTpEditor from '@/components/common/SlTpEditor';

interface Props {
  positions: LivePosition[];
  loading?: boolean;
  onRefresh?: () => void;
}

const STATUS_STYLE: Record<string, string> = {
  open: 'text-success bg-success/10',
  partially_closed: 'text-accent bg-accent/10',
  closed: 'text-text-secondary bg-text-secondary/10',
  failed: 'text-danger bg-danger/10',
  orphaned: 'text-warning bg-warning/10',
};

export default function LivePositionsTable({ positions, loading, onRefresh }: Props) {
  const markets = useMarketStore((s) => s.markets);
  const [closeTarget, setCloseTarget] = useState<LivePosition | null>(null);
  // Item 4 (2026-10-08): the SAME SL/TP editor the Terminal uses, on the copy
  // dashboard, against the same native Perpl trigger path. The armed legs come
  // from the venue's own order frames through the shared account store.
  const { address } = useAuth();
  const account = usePerplAccount(address);
  const ensureConnected = useEnsurePerpl();
  const [slTpFor, setSlTpFor] = useState<number | null>(null);

  if (loading) return <div className="text-center text-xs text-text-secondary/60 py-10">Loading live positions…</div>;
  if (!positions.length) {
    return (
      <div className="text-center text-xs text-text-secondary/60 py-10">
        No live copied positions yet. They appear here after a live copy order fills.
      </div>
    );
  }

  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-text-secondary/60 text-left border-b border-text-secondary/10">
              <th className="py-2 px-2 font-medium">Market</th>
              <th className="py-2 px-2 font-medium">Side</th>
              <th className="py-2 px-2 font-medium">Status</th>
              <th className="py-2 px-2 font-medium text-right">Entry</th>
              <th className="py-2 px-2 font-medium text-right">Size</th>
              <th className="py-2 px-2 font-medium text-right">Margin</th>
              <th className="py-2 px-2 font-medium text-right">PnL</th>
              <th className="py-2 px-2 font-medium">SL / TP on Perpl</th>
              <th className="py-2 px-2 font-medium">Trader</th>
              <th className="py-2 px-2 font-medium text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const decimals = MARKETS[p.follower_market_id]?.decimals ?? 2;
              const isOpen = p.status === 'open' || p.status === 'partially_closed';
              const mark = markets[p.follower_market_id]?.mark_price ?? null;
              const size = p.current_size ?? p.entry_size ?? 0;
              const unreal = isOpen && mark && p.entry_price != null
                ? (p.side === 'long' ? (mark - p.entry_price) : (p.entry_price - mark)) * size
                : null;
              const pnl = isOpen ? unreal : p.realized_pnl;
              const pnlUp = (pnl ?? 0) >= 0;
              const { sl, tp } = triggersFor(account, p.follower_market_id, p.side as 'long' | 'short');
              const slPx = sl ? triggerPrice(sl) : null;
              const tpPx = tp ? triggerPrice(tp) : null;
              return (
                <>
                <tr key={p.id} className="border-b border-text-secondary/5 hover:bg-bg-secondary/30">
                  <td className="py-2.5 px-2 font-semibold text-text-primary">{p.symbol || MARKETS[p.follower_market_id]?.symbol || p.follower_market_id}</td>
                  <td className="py-2.5 px-2"><span className={clsx('font-medium', p.side === 'long' ? 'text-success' : 'text-danger')}>{p.side?.toUpperCase()}</span></td>
                  <td className="py-2.5 px-2">
                    <span className={clsx('text-[10px] font-medium px-1.5 py-0.5 rounded uppercase', STATUS_STYLE[p.status] || STATUS_STYLE.closed)}>{p.status.replace(/_/g, ' ')}</span>
                    {/* audit A1: TP/SL placement failed after the fill, loud, danger-tinted */}
                    {p.triggers_failed && isOpen && (
                      <span className="ml-1.5 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-danger/15 text-danger border border-danger/30"
                        title="TP/SL trigger placement failed after this position opened, it has no armed stop. Set one from the terminal.">
                        No stop armed
                      </span>
                    )}
                    {p.close_suggested && (
                      <span className="ml-1 text-[9px] font-medium px-1.5 py-0.5 rounded bg-warning/15 text-warning whitespace-nowrap">
                        leader {p.close_suggestion_type === 'reduce' ? 'reduced' : 'closed'}
                      </span>
                    )}
                  </td>
                  <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.entry_price != null ? formatPrice(p.entry_price, decimals) : '-'}</td>
                  <td className="py-2.5 px-2 text-right tabular-nums text-text-primary">{formatSize(size)}</td>
                  <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.entry_margin != null ? formatUSD(p.entry_margin) : '-'}</td>
                  <td className={clsx('py-2.5 px-2 text-right tabular-nums font-semibold', pnl == null ? 'text-text-secondary/50' : pnlUp ? 'text-success' : 'text-danger')}>
                    {pnl == null ? '-' : formatUSD(pnl)}{isOpen && unreal != null ? ' *' : ''}
                  </td>
                  <td className="py-2.5 px-2">
                    {!isOpen ? <span className="text-text-secondary/40">-</span>
                      : !account.venueSession
                        ? <span className="text-[10px] text-text-secondary/60">connect to Perpl</span>
                        : (
                          <span className="flex items-center gap-1.5 whitespace-nowrap">
                            <span className={clsx('text-[10px] px-1 rounded', sl ? 'bg-danger/10 text-danger' : 'bg-warning/10 text-warning')}>
                              {sl ? `SL ${slPx != null ? formatPrice(slPx, decimals) : 'set'}` : 'no SL'}
                            </span>
                            <span className={clsx('text-[10px] px-1 rounded', tp ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning')}>
                              {tp ? `TP ${tpPx != null ? formatPrice(tpPx, decimals) : 'set'}` : 'no TP'}
                            </span>
                            <button
                              onClick={() => setSlTpFor(slTpFor === p.id ? null : p.id)}
                              className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-warning/15 text-warning hover:bg-warning/25"
                            >
                              {sl || tp ? 'Edit' : 'Add'}
                            </button>
                          </span>
                        )}
                  </td>
                  <td className="py-2.5 px-2 font-mono text-text-secondary/70">{shortenAddress(p.trader_wallet)}</td>
                  <td className="py-2.5 px-2 text-right">
                    {isOpen ? (
                      <button
                        onClick={() => setCloseTarget(p)}
                        className={clsx('text-[11px] font-semibold px-2.5 py-1 rounded-md transition-colors',
                          p.close_suggested ? 'bg-warning/20 text-warning hover:bg-warning/30' : 'bg-danger/15 text-danger hover:bg-danger/25')}
                      >
                        {p.close_suggested ? (p.close_suggestion_type === 'reduce' ? 'Confirm Reduce' : 'Confirm Close') : 'Close'}
                      </button>
                    ) : <span className="text-text-secondary/40">-</span>}
                  </td>
                </tr>
                {slTpFor === p.id && isOpen && (
                  <tr key={`${p.id}-sltp`} className="border-b border-text-secondary/5 bg-bg-secondary/20">
                    <td colSpan={10} className="px-3 py-3">
                      <SlTpEditor
                        account={account}
                        ensureConnected={ensureConnected}
                        position={{
                          marketId: p.follower_market_id,
                          symbol: p.symbol || MARKETS[p.follower_market_id]?.symbol || `M${p.follower_market_id}`,
                          side: p.side as 'long' | 'short',
                          size,
                          markPrice: mark ?? p.entry_price ?? 0,
                          leverage: p.entry_leverage ?? undefined,
                        }}
                        onChanged={() => onRefresh?.()}
                      />
                      <div className="mt-2 text-[10px] text-text-secondary/60">
                        These are native Perpl trigger orders on your account, sized on this position. Replacing a leg
                        cancels the armed one first.
                      </div>
                    </td>
                  </tr>
                )}
                </>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="text-[10px] text-text-secondary/40 mt-2">* unrealized (from live mark)</div>

      {closeTarget && (
        <LiveCloseModal
          isOpen={!!closeTarget}
          onClose={() => setCloseTarget(null)}
          position={closeTarget}
          onClosed={() => { setCloseTarget(null); onRefresh?.(); }}
        />
      )}
    </>
  );
}
