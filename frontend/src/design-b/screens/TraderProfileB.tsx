import { useState } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useTrader, useWatchlist, useSubscriptions } from '@/hooks/useCopyV1';
import StartPaperCopyModal from '@/components/copy/StartPaperCopyModal';
import LiveCopyModal, { type LiveCopyPosition } from '@/components/copy/LiveCopyModal';
import Sparkline from '@/components/copy/Sparkline';
import OpenDuration, { formatOpenedDate } from '@/components/copy/OpenDuration';
import { MARKETS } from '@/config/constants';
import { formatCompact, formatPercent, formatPrice, formatSize, shortenAddress } from '@/lib/formatters';
import type { EquityTimeframe } from '@/lib/copyApi';

// Design B — Trader profile (spec §3.2). Presentation only: the EXACT existing
// data layer from pages/copy/TraderProfile.tsx (useTrader, watchlist, subs).
// "Set up copy" opens StartPaperCopyModal UNCHANGED; each position's Copy opens
// LiveCopyModal UNCHANGED (real-money surfaces — not restyled this pass,
// wrapper Rule 7). Reached only from Discover's Profile buttons.

const HL_WINDOWS: { label: string; tf: EquityTimeframe }[] = [
  { label: 'All', tf: 'all' }, { label: '24h', tf: '24h' }, { label: '1W', tf: '7d' }, { label: '1M', tf: '30d' },
];

function relAge(iso: string | null | undefined): string {
  if (!iso) return 'Not enough data';
  const s = Math.max(0, (Date.now() - Date.parse(iso.endsWith('Z') ? iso : iso + 'Z')) / 1000);
  if (s < 90) return 'Just now';
  if (s < 3600) return `${Math.round(s / 60)} minutes ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hours ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export default function TraderProfileB() {
  const { wallet } = useParams<{ wallet: string }>();
  const [searchParams] = useSearchParams();
  const exchange = searchParams.get('exchange') === 'hl' ? 'hl' : 'perpl';
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const [tf, setTf] = useState<EquityTimeframe>('all');
  const { detail, stats, positions, positionsError, equity, loading, error } = useTrader(wallet, tf, exchange);
  const watchlist = useWatchlist(isAuthenticated);
  const subs = useSubscriptions(isAuthenticated);
  const [showCopy, setShowCopy] = useState(false);
  const [liveCopyTarget, setLiveCopyTarget] = useState<LiveCopyPosition | null>(null);

  if (!wallet) return null;
  const watched = watchlist.isWatched(wallet, exchange);
  const name = detail?.profile?.display_name || shortenAddress(wallet);
  const headline = detail?.stats;
  const activity = detail?.activity;
  const fill = detail?.fill_stats;
  const latestStat = stats.length ? stats[stats.length - 1] : null;
  const venue = exchange === 'hl' ? 'Hyperliquid' : 'Perpl';
  const isHl = exchange === 'hl';
  const days = activity?.active_days_7d;
  const tradesPerDay = fill?.trades_7d != null && fill.trades_7d > 0 ? `About ${Math.max(1, Math.round(fill.trades_7d / 7))}` : null;

  const toggleWatch = async () => {
    if (!isAuthenticated) return;
    if (watched) await watchlist.unwatch(wallet, exchange);
    else await watchlist.watch(wallet, exchange);
  };

  return (
    <div className="screen">
      <div className="head">
        <div>
          <button className="btn sm ghost" style={{ paddingLeft: 0, marginBottom: 4 }} onClick={() => navigate('/copy/discover')}>← Back to copy trade</button>
          <h1>Trader profile</h1>
          <p>{headline?.rank != null ? `Rank ${headline.rank} on ${venue}` : `On ${venue}`}{isHl ? '. Signals come from Hyperliquid, orders are placed on Perpl.' : ''}</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {isHl && (
            <div className="seg">
              {HL_WINDOWS.map((w) => <button key={w.tf} aria-pressed={tf === w.tf} onClick={() => setTf(w.tf)}>{w.label}</button>)}
            </div>
          )}
          <button className="btn" aria-pressed={watched} onClick={toggleWatch}>{watched ? 'Watching' : 'Watch'}</button>
          <button className="btn primary" onClick={() => setShowCopy(true)}>Set up copy</button>
        </div>
      </div>

      {error && <div className="empty" style={{ borderColor: 'var(--short)', color: 'var(--short)', marginBottom: 12 }}>{error}</div>}

      <div className="prof">
        {/* Left column */}
        <div className="id">
          <div className="card idcard">
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <span className={headline?.rank === 1 ? 'medal m1' : headline?.rank === 2 ? 'medal m2' : headline?.rank === 3 ? 'medal m3' : 'medal'}>{headline?.rank ?? '—'}</span>
              <span className="addr">{name}</span>
              {isHl && <span className="tag long">HL</span>}
            </div>
            <div className="mono muted" style={{ wordBreak: 'break-all' }}>{wallet}</div>
            <div style={{ display: 'flex', gap: 6, marginTop: 12, flexWrap: 'wrap' }}>
              {headline?.on_leaderboard && <span className="tag accent">On leaderboard</span>}
              {days != null && <span className="tag flat">Active {days} of 7 days</span>}
              {isHl && tradesPerDay && <span className="tag flat">{tradesPerDay} trades a day</span>}
            </div>
          </div>

          <div className="card stat-list">
            <h2>Track record</h2>
            <div className="sub">All time on {venue}</div>
            <div className="kv"><span>Total profit</span><b className={headline?.pnl_total != null ? (headline.pnl_total >= 0 ? 'long-c' : 'short-c') : 'dim'}>{headline?.pnl_total != null ? formatCompact(headline.pnl_total) : 'Not enough data'}</b></div>
            <div className="kv"><span>Return</span><b className={headline?.roi != null ? (headline.roi >= 0 ? 'long-c' : 'short-c') : 'dim'}>{headline?.roi != null ? formatPercent(headline.roi) : 'Not enough data'}</b></div>
            <div className="kv"><span>Volume traded</span><b>{headline?.volume != null ? formatCompact(headline.volume) : 'Not enough data'}</b></div>
            {isHl
              ? <div className="kv"><span>Open positions</span><b>{loading ? '…' : positions.length}</b></div>
              : <div className="kv"><span>Volume last 24h</span><b className={activity?.vol_velocity_24h != null ? '' : 'dim'}>{activity?.vol_velocity_24h != null ? formatCompact(activity.vol_velocity_24h) : 'Not enough data'}</b></div>}
            <div className="kv"><span>Active this week</span><b className={days != null ? '' : 'dim'}>{days != null ? `${days} of 7 days` : 'Not enough data'}</b></div>
            {isHl && <div className="kv"><span>Trades per day</span><b className={tradesPerDay ? '' : 'dim'}>{tradesPerDay ?? 'Loads from Hyperliquid'}</b></div>}
            <div className="kv"><span>Last active</span><b className={activity?.last_active_at ? '' : 'dim'}>{relAge(activity?.last_active_at)}</b></div>
            <div className="kv"><span>Win rate</span><b className={latestStat?.win_rate != null ? '' : 'dim'}>{latestStat?.win_rate != null ? formatPercent(latestStat.win_rate) : (isHl ? 'Loads from Hyperliquid' : 'Not enough data')}</b></div>
          </div>

          {isHl && (
            <div className="card">
              <h2>Copy settings preview</h2>
              <div className="sub">What copying this trader means for you</div>
              <div className="kv"><span>Executes on</span><b>Perpl</b></div>
              <div className="kv"><span>Markets mirrored</span><b>Only those listed on Perpl</b></div>
              <div className="kv"><span>Confirmation</span><b>Every order</b></div>
            </div>
          )}
        </div>

        {/* Right column */}
        <div>
          <div className="card chart">
            <h2>Cumulative profit</h2>
            <div className="sub">{isHl ? 'Recent equity from the Hyperliquid leaderboard feed' : 'From leaderboard snapshots'}, {positions.length} position{positions.length === 1 ? '' : 's'} open</div>
            {equity.length >= 2 ? (
              <Sparkline points={equity} width={880} height={200} />
            ) : (
              <div className="empty" style={{ marginTop: 12 }}>{loading ? 'Loading equity…' : 'No equity history yet for this trader.'}</div>
            )}
          </div>

          <div className="card" style={{ marginTop: 16 }}>
            <h2>Open positions</h2>
            <div className="sub">Read from chain. Take profit and stop loss are not public on Perpl.</div>
            {loading ? (
              <div className="empty" style={{ marginTop: 12 }}>Checking active trades…</div>
            ) : positions.length === 0 ? (
              <div className="empty" style={{ marginTop: 12 }}>{positionsError ? 'Open trades unavailable — try again shortly.' : 'No active trades.'}</div>
            ) : (
              <div className="postable-wrap">
                <table className="postable">
                  <thead>
                    <tr>
                      <th>Market</th><th>Side</th><th className="r">Size</th><th className="r">Entry</th><th className="r">Mark</th>
                      <th className="r">Leverage</th><th>Opened</th><th className="r">Profit</th><th className="r" />
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((p, i) => {
                      const decimals = MARKETS[p.market_id]?.decimals ?? 2;
                      return (
                        <tr key={`${p.market_id}-${i}`}>
                          <td className="amt">{p.symbol || MARKETS[p.market_id]?.symbol}</td>
                          <td><span className={`tag ${p.side === 'long' ? 'long' : 'short'}`}>{p.side === 'long' ? 'Long' : 'Short'}</span></td>
                          <td className="r">{formatSize(p.size)}</td>
                          <td className="r">{formatPrice(p.entry_price, decimals)}</td>
                          <td className="r">{formatPrice(p.mark_price, decimals)}</td>
                          <td className="r">{p.leverage}x</td>
                          <td style={{ whiteSpace: 'nowrap' }}>{p.opened_at ? formatOpenedDate(p.opened_at) : '—'}{p.opened_at ? <>, <OpenDuration openedAt={p.opened_at} /></> : null}</td>
                          <td className={`r amt ${p.pnl >= 0 ? 'long-c' : 'short-c'}`}>{formatCompact(p.pnl)}</td>
                          <td className="r">
                            <button className="btn sm primary" onClick={() => setLiveCopyTarget({
                              market_id: p.market_id, symbol: p.symbol, side: p.side,
                              entry_price: p.entry_price, mark_price: p.mark_price,
                              leverage: p.leverage, size: p.size, venue: 'perpl', opened_at: p.opened_at,
                            })}>Copy</button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
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
    </div>
  );
}
