import { useState } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { clsx } from 'clsx';
import { useAuth } from '@/hooks/useAuth';
import { useTrader, useWatchlist, useSubscriptions } from '@/hooks/useCopyV1';
import CopyLayout from '@/components/copy/CopyLayout';
import StartPaperCopyModal from '@/components/copy/StartPaperCopyModal';
import LiveCopyModal, { type LiveCopyPosition } from '@/components/copy/LiveCopyModal';
import Sparkline from '@/components/copy/Sparkline';
import OpenDuration, { formatOpenedDate } from '@/components/copy/OpenDuration';
import { MARKETS } from '@/config/constants';
import { formatCompact, formatPercent, formatPrice, formatSize, shortenAddress } from '@/lib/formatters';

export default function TraderProfilePage() {
  const { wallet } = useParams<{ wallet: string }>();
  const [searchParams] = useSearchParams();
  // telegram deep links carry ?exchange=hl — resolve the profile per-exchange
  const exchange = searchParams.get('exchange') === 'hl' ? 'hl' : 'perpl';
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const { detail, stats, positions, positionsError, equity, loading, error } = useTrader(wallet, '7d', exchange);
  const watchlist = useWatchlist(isAuthenticated);
  const subs = useSubscriptions(isAuthenticated);
  const [showCopy, setShowCopy] = useState(false);
  const [liveCopyTarget, setLiveCopyTarget] = useState<LiveCopyPosition | null>(null);

  if (!wallet) return null;
  const watched = watchlist.isWatched(wallet);
  const name = detail?.profile?.display_name || shortenAddress(wallet);

  const headline = detail?.stats;
  const latestStat = stats.length ? stats[stats.length - 1] : null;

  return (
    <CopyLayout>
      <button onClick={() => navigate('/copy/discover')} className="text-xs text-text-secondary hover:text-text-primary mb-3 flex items-center gap-1">
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        Back to Discover
      </button>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-danger/10 border border-danger/25 text-xs text-danger mb-4">{error}</div>
      )}

      {/* Profile header */}
      <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-5 mb-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-full bg-accent/10 border border-accent/20 flex items-center justify-center text-accent text-sm font-bold">
              {headline?.rank ? `#${headline.rank}` : '—'}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-bold text-text-primary">{name}</h2>
                {detail?.profile?.is_verified && (
                  <svg className="w-4 h-4 text-accent" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                  </svg>
                )}
              </div>
              <span className="text-xs text-text-secondary/60 font-mono">{wallet}</span>
              {detail?.profile?.bio && <p className="text-xs text-text-secondary mt-1">{detail.profile.bio}</p>}
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={async () => {
                if (!isAuthenticated) return;
                if (watched) await watchlist.unwatch(wallet);
                else await watchlist.watch(wallet);
              }}
              className={clsx(
                'text-xs font-medium py-2 px-4 rounded-lg transition-colors',
                watched ? 'bg-accent/15 text-accent border border-accent/30' : 'bg-bg-secondary text-text-secondary hover:text-text-primary',
              )}
            >
              {watched ? 'Watching' : 'Watch'}
            </button>
            <button
              onClick={() => setShowCopy(true)}
              className="text-xs font-semibold py-2 px-4 rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors"
            >
              Set up Copy
            </button>
          </div>
        </div>

        {/* Headline stats */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-5">
          <Stat label="PnL" value={headline?.pnl_total != null ? formatCompact(headline.pnl_total) : '—'} tone={headline?.pnl_total != null ? (headline.pnl_total >= 0 ? 'up' : 'down') : 'neutral'} />
          <Stat label="ROI" value={headline?.roi != null ? formatPercent(headline.roi) : '—'} tone={headline?.roi != null ? (headline.roi >= 0 ? 'up' : 'down') : 'neutral'} />
          <Stat label="Volume" value={headline?.volume != null ? formatCompact(headline.volume) : '—'} />
          <Stat label="On Leaderboard" value={headline?.on_leaderboard ? 'Yes' : 'No'} />
        </div>
        {latestStat && (latestStat.win_rate != null || latestStat.trades != null) && (
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-text-secondary/60 mt-3">
            {latestStat.win_rate != null && <span>Win rate: {formatPercent(latestStat.win_rate)}</span>}
            {latestStat.trades != null && <span>Trades: {latestStat.trades}</span>}
            {latestStat.max_drawdown != null && <span>Max DD: {formatPercent(latestStat.max_drawdown)}</span>}
          </div>
        )}
      </div>

      {/* Equity curve (real cumulative-PnL from leaderboard snapshots) */}
      <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4 mb-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-text-primary">Equity curve</h3>
          <span className="text-[11px]" style={{ color: 'var(--faint)' }}>Cumulative PnL · last 7d · {positions.length} open</span>
        </div>
        <div className="rounded-lg px-3 py-2" style={{ background: 'var(--surface-2)' }}>
          {equity.length >= 2 ? (
            <Sparkline points={equity} width={880} height={120} />
          ) : (
            <div className="h-[120px] flex items-center justify-center text-[11px]" style={{ color: 'var(--faint)' }}>
              {loading ? 'Loading…' : 'No equity history yet for this trader.'}
            </div>
          )}
        </div>
      </div>

      {/* On-chain positions (read-only) */}
      <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4">
        <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
          Live On-Chain Positions <span className="text-[11px] font-normal text-text-secondary/50">(read-only)</span>
          {!loading && (
            <span className={clsx('text-[10px] font-semibold px-2 py-0.5 rounded-full',
              positions.length > 0 ? 'bg-success/12 text-success border border-success/25' : 'bg-text-secondary/8 text-text-secondary/60')}>
              {positions.length > 0
                ? `${positions.length} active trade${positions.length > 1 ? 's' : ''}`
                : positionsError ? 'Open trades unavailable' : 'No active trades'}
            </span>
          )}
        </h3>
        {loading ? (
          <div className="text-center text-xs text-text-secondary/60 py-8">Checking active trades…</div>
        ) : positions.length === 0 ? (
          <div className="text-center text-xs text-text-secondary/60 py-8">
            {positionsError ? 'Open trades unavailable — try again shortly.' : 'No active trades.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-secondary/60 text-left border-b border-text-secondary/10">
                  <th className="py-2 px-2 font-medium">Market</th>
                  <th className="py-2 px-2 font-medium">Side</th>
                  <th className="py-2 px-2 font-medium text-right">Size</th>
                  <th className="py-2 px-2 font-medium text-right">Entry</th>
                  <th className="py-2 px-2 font-medium text-right">Mark</th>
                  <th className="py-2 px-2 font-medium text-right">Lev</th>
                  <th className="py-2 px-2 font-medium text-right">Opened</th>
                  <th className="py-2 px-2 font-medium text-right">Active</th>
                  <th className="py-2 px-2 font-medium text-right">PnL</th>
                  <th className="py-2 px-2 font-medium text-right">Copy</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => {
                  const decimals = MARKETS[p.market_id]?.decimals ?? 2;
                  return (
                    <tr key={`${p.market_id}-${i}`} className="border-b border-text-secondary/5">
                      <td className="py-2.5 px-2 font-semibold text-text-primary">{p.symbol || MARKETS[p.market_id]?.symbol}</td>
                      <td className="py-2.5 px-2"><span className={clsx('font-medium', p.side === 'long' ? 'text-success' : 'text-danger')}>{p.side?.toUpperCase()}</span></td>
                      <td className="py-2.5 px-2 text-right tabular-nums">{formatSize(p.size)}</td>
                      <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{formatPrice(p.entry_price, decimals)}</td>
                      <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{formatPrice(p.mark_price, decimals)}</td>
                      <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.leverage}x</td>
                      <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary whitespace-nowrap">{formatOpenedDate(p.opened_at)}</td>
                      <td className="py-2.5 px-2 text-right text-text-secondary"><OpenDuration openedAt={p.opened_at} /></td>
                      <td className={clsx('py-2.5 px-2 text-right tabular-nums font-semibold', p.pnl >= 0 ? 'text-success' : 'text-danger')}>{formatCompact(p.pnl)}</td>
                      <td className="py-2.5 px-2 text-right">
                        <button
                          onClick={() => setLiveCopyTarget({
                            market_id: p.market_id, symbol: p.symbol, side: p.side,
                            entry_price: p.entry_price, mark_price: p.mark_price,
                            leverage: p.leverage, size: p.size,
                            venue: 'perpl', opened_at: p.opened_at,
                          })}
                          className="text-[11px] font-semibold px-2.5 py-1 rounded-md bg-accent/15 text-accent hover:bg-accent/25 transition-colors"
                        >
                          Copy
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCopy && (
        <StartPaperCopyModal
          isOpen={showCopy}
          onClose={() => setShowCopy(false)}
          traderWallet={wallet}
          traderName={detail?.profile?.display_name}
          onSubmit={subs.create}
        />
      )}

      {liveCopyTarget && (
        <LiveCopyModal
          isOpen={!!liveCopyTarget}
          onClose={() => setLiveCopyTarget(null)}
          traderWallet={wallet}
          traderName={detail?.profile?.display_name}
          position={liveCopyTarget}
        />
      )}
    </CopyLayout>
  );
}

function Stat({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'up' | 'down' | 'neutral' }) {
  return (
    <div className="rounded-lg bg-bg-secondary/50 py-2.5 px-3">
      <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">{label}</div>
      <div className={clsx('text-sm font-bold tabular-nums mt-0.5', tone === 'up' ? 'text-success' : tone === 'down' ? 'text-danger' : 'text-text-primary')}>
        {value}
      </div>
    </div>
  );
}
