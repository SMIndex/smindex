import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import api from '@/lib/api';
import { shortenAddress } from '@/lib/formatters';
import LoadingSpinner from '@/components/common/LoadingSpinner';

interface InsightWallet {
  wallet: string;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  est_pnl_total: number;
  avg_hold_sec: number | null;
  markets: string[];
  first_close: string | null;
  last_close: string | null;
  unrated_trades: number;
}

interface InsightTrade {
  symbol: string;
  side: string;
  open_time: string | null;
  close_time: string;
  hold_sec: number | null;
  size: number | null;
  avg_entry: number | null;
  notional_usd: number | null;
  est_pnl: number | null;
  leverage: number | null;
  legs: number;
}

function fmtHold(sec: number | null): string {
  if (sec == null) return '—';
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86400).toFixed(1)}d`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return 'before tracking';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function fmtPnl(v: number | null): string {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}$${Math.abs(v) >= 1000 ? v.toFixed(0) : v.toFixed(2)}`;
}

function TradesTable({ wallet }: { wallet: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['insight-trades', wallet],
    queryFn: () => api.get(`/api/insights/wallets/${wallet}/trades`).then((r) => r.data),
    staleTime: 60000,
  });

  if (isLoading) return <div className="py-6 flex justify-center"><LoadingSpinner /></div>;
  const trades: InsightTrade[] = data?.trades || [];
  if (!trades.length) return <div className="py-4 text-sm text-text-secondary">No reconstructed trades for this wallet.</div>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-text-secondary uppercase tracking-wider text-[9px] border-b border-text-secondary/10">
            <th className="text-left py-1.5 px-2">Market</th>
            <th className="text-left px-2">Side</th>
            <th className="text-left px-2">Opened</th>
            <th className="text-left px-2">Closed</th>
            <th className="text-right px-2">Held</th>
            <th className="text-right px-2">Size</th>
            <th className="text-right px-2">Avg Entry</th>
            <th className="text-right px-2">Amount (USD)</th>
            <th className="text-right px-2">Lev</th>
            <th className="text-right px-2">Est PnL</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i} className="border-b border-text-secondary/5 hover:bg-bg-secondary/40">
              <td className="py-1.5 px-2 font-semibold">{t.symbol}</td>
              <td className={clsx('px-2 font-medium', t.side === 'long' ? 'text-success' : 'text-danger')}>{t.side}</td>
              <td className="px-2 text-text-secondary tabular-nums">{fmtTime(t.open_time)}</td>
              <td className="px-2 text-text-secondary tabular-nums">{fmtTime(t.close_time)}</td>
              <td className="px-2 text-right tabular-nums">{fmtHold(t.hold_sec)}</td>
              <td className="px-2 text-right tabular-nums">{t.size != null ? t.size.toLocaleString(undefined, { maximumFractionDigits: 4 }) : '—'}</td>
              <td className="px-2 text-right tabular-nums">{t.avg_entry != null ? t.avg_entry.toLocaleString(undefined, { maximumFractionDigits: 4 }) : '—'}</td>
              <td className="px-2 text-right tabular-nums">{t.notional_usd != null ? `$${t.notional_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}</td>
              <td className="px-2 text-right tabular-nums" title={t.leverage == null ? 'Leverage recording started 2026-07-31 — older trades have none' : undefined}>
                {t.leverage != null ? `${t.leverage}x` : '—'}
              </td>
              <td className={clsx('px-2 text-right font-semibold tabular-nums', (t.est_pnl ?? 0) > 0 ? 'text-success' : t.est_pnl == null ? 'text-text-secondary' : 'text-danger')}>
                {fmtPnl(t.est_pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type SortKey = 'trades' | 'wins' | 'win_rate' | 'est_pnl_total' | 'avg_hold_sec' | 'last_close';

export default function WalletInsights() {
  const [minTrades, setMinTrades] = useState(10);
  const [greenOnly, setGreenOnly] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>('win_rate');
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc');
  const [expanded, setExpanded] = useState<string | null>(null);
  const qc = useQueryClient();

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(key); setSortDir('desc'); }
  };

  const { data, isLoading } = useQuery({
    queryKey: ['insight-wallets'],
    queryFn: () => api.get('/api/insights/wallets').then((r) => r.data),
    refetchInterval: 120000,
  });

  const [refreshing, setRefreshing] = useState(false);
  const triggerRefresh = async () => {
    setRefreshing(true);
    try {
      await api.post('/api/insights/refresh');
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['insight-wallets'] });
        qc.invalidateQueries({ queryKey: ['insight-trades'] });
        setRefreshing(false);
      }, 90000); // reconstruction takes ~1-2 min (candle fetches)
    } catch {
      setRefreshing(false);
    }
  };

  const wallets: InsightWallet[] = (data?.wallets || [])
    .filter((w: InsightWallet) => w.trades >= minTrades)
    .filter((w: InsightWallet) => !greenOnly || w.est_pnl_total > 0)
    .sort((a: InsightWallet, b: InsightWallet) => {
      const va = sortKey === 'last_close' ? (a.last_close || '') : (a[sortKey] ?? -Infinity);
      const vb = sortKey === 'last_close' ? (b.last_close || '') : (b[sortKey] ?? -Infinity);
      const cmp = va < vb ? -1 : va > vb ? 1 : b.trades - a.trades;
      return sortDir === 'desc' ? -cmp : cmp;
    });
  const status = data?.status;

  const SortTh = ({ k, label, align = 'right' }: { k: SortKey; label: string; align?: 'left' | 'right' }) => (
    <th
      onClick={() => toggleSort(k)}
      className={clsx('px-3 cursor-pointer select-none hover:text-text-primary transition-colors', align === 'left' ? 'text-left' : 'text-right')}
      title={`Sort by ${label}`}
    >
      {label}
      <span className={clsx('ml-0.5', sortKey === k ? 'text-accent' : 'opacity-25')}>
        {sortKey === k ? (sortDir === 'desc' ? '▼' : '▲') : '▼'}
      </span>
    </th>
  );

  return (
    <div className="space-y-4 max-w-6xl mx-auto p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Wallet Insights</h1>
          <p className="text-sm text-text-secondary mt-1">
            Tracked wallets ranked by win rate — every closed position reconstructed from on-chain tracking.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setGreenOnly((v) => !v)}
            className={clsx(
              'px-3 py-1.5 rounded-lg text-[11px] font-medium border transition-colors',
              greenOnly
                ? 'bg-success/15 border-success/40 text-success'
                : 'bg-bg-card border-text-secondary/15 text-text-secondary hover:text-text-primary',
            )}
            title="Show only wallets with positive estimated PnL"
          >
            {greenOnly ? '✓ ' : ''}Green PnL only
          </button>
          <div className="flex rounded-lg overflow-hidden border border-text-secondary/15 text-[11px]">
            {[1, 10, 20].map((n) => (
              <button
                key={n}
                onClick={() => setMinTrades(n)}
                className={clsx('px-3 py-1.5 font-medium transition-colors', minTrades === n ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary')}
              >
                {n === 1 ? 'All' : `≥${n} trades`}
              </button>
            ))}
          </div>
          <button
            onClick={triggerRefresh}
            disabled={refreshing || status?.running}
            className="px-3 py-1.5 rounded-lg text-[11px] font-medium bg-bg-card border border-text-secondary/15 text-text-secondary hover:text-text-primary disabled:opacity-50 transition-colors"
          >
            {refreshing || status?.running ? 'Recomputing…' : 'Refresh data'}
          </button>
        </div>
      </div>

      <div className="card p-3 text-[11px] text-text-secondary leading-relaxed">
        <span className="font-semibold text-text-primary">How this is computed:</span>{' '}
        only wallets our tracker has watched appear here (top leaderboard + watched wallets, polled on-chain every 30s).
        A trade = one full open→close round-trip. Est. PnL prices each exit at the 5-minute candle close — it is an
        estimate, gross of fees/rebates. Win = positive est. PnL. Leverage is not shown for historical trades because
        the tracker never recorded it. Trades opened before tracking began show "before tracking" as open time and are
        excluded from hold-time averages.
        {status?.last_refresh && <span> Last recomputed: {new Date(status.last_refresh + 'Z').toLocaleString()}.</span>}
      </div>

      {isLoading ? (
        <div className="flex justify-center py-20"><LoadingSpinner /></div>
      ) : !wallets.length ? (
        <div className="card p-8 text-center text-sm text-text-secondary">
          {data?.wallets?.length
            ? 'No wallets match the filters — lower the minimum trades or turn off "Green PnL only".'
            : 'No data yet — hit "Refresh data" to run the first reconstruction (takes ~2 minutes).'}
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-text-secondary uppercase tracking-wider text-[9px] border-b border-text-secondary/10">
                <th className="text-left py-2 px-3">#</th>
                <th className="text-left px-3">Wallet</th>
                <SortTh k="trades" label="Trades" />
                <SortTh k="wins" label="W / L" />
                <SortTh k="win_rate" label="Win rate (W/R)" align="left" />
                <SortTh k="est_pnl_total" label="Est PnL (gross)" />
                <SortTh k="avg_hold_sec" label="Avg hold" />
                <th className="text-left px-3">Markets</th>
                <SortTh k="last_close" label="Last trade" />
                <th className="px-3" />
              </tr>
            </thead>
            <tbody>
              {wallets.map((w, i) => (
                <>
                  <tr key={w.wallet} className="border-b border-text-secondary/5 hover:bg-bg-secondary/40">
                    <td className="py-2 px-3 text-text-secondary">{i + 1}</td>
                    <td className="px-3">
                      <span className="font-mono font-medium">{shortenAddress(w.wallet)}</span>
                      <button
                        onClick={() => navigator.clipboard?.writeText(w.wallet)}
                        title="Copy address"
                        className="ml-1.5 text-text-secondary/50 hover:text-text-primary text-[10px]"
                      >⧉</button>
                      {w.trades < 10 && <span className="ml-1.5 text-[9px] text-warning" title="Small sample — win rate not statistically meaningful">⚠ small sample</span>}
                    </td>
                    <td className="px-3 text-right tabular-nums">{w.trades}</td>
                    <td className="px-3 text-right tabular-nums">
                      <span className="text-success">{w.wins}</span>
                      <span className="text-text-secondary/50"> / </span>
                      <span className="text-danger">{w.losses}</span>
                    </td>
                    <td className="px-3">
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-bg-secondary rounded-full overflow-hidden">
                          <div
                            className={clsx('h-full rounded-full', (w.win_rate ?? 0) >= 55 ? 'bg-success' : (w.win_rate ?? 0) >= 45 ? 'bg-warning' : 'bg-danger')}
                            style={{ width: `${w.win_rate ?? 0}%` }}
                          />
                        </div>
                        <span className="font-bold tabular-nums w-12 text-right">{w.win_rate?.toFixed(1)}%</span>
                      </div>
                    </td>
                    <td className={clsx('px-3 text-right font-semibold tabular-nums', w.est_pnl_total > 0 ? 'text-success' : 'text-danger')}>
                      {fmtPnl(w.est_pnl_total)}
                    </td>
                    <td className="px-3 text-right tabular-nums">{fmtHold(w.avg_hold_sec)}</td>
                    <td className="px-3">
                      <div className="flex gap-1 flex-wrap">
                        {w.markets.slice(0, 4).map((m) => (
                          <span key={m} className="px-1.5 py-0.5 rounded bg-bg-secondary text-[9px] font-medium">{m}</span>
                        ))}
                      </div>
                    </td>
                    <td className="px-3 text-right text-text-secondary tabular-nums text-[10px]">{fmtTime(w.last_close)}</td>
                    <td className="px-3 text-right">
                      <button
                        onClick={() => setExpanded(expanded === w.wallet ? null : w.wallet)}
                        className={clsx(
                          'px-2.5 py-1 rounded-md text-[10px] font-semibold transition-colors',
                          expanded === w.wallet ? 'bg-accent text-white' : 'bg-accent/10 text-accent hover:bg-accent/20',
                        )}
                      >
                        {expanded === w.wallet ? 'Hide' : 'View'}
                      </button>
                    </td>
                  </tr>
                  {expanded === w.wallet && (
                    <tr key={`${w.wallet}-detail`}>
                      <td colSpan={10} className="bg-bg-secondary/30 px-3 py-2">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-[10px] uppercase tracking-wider text-text-secondary font-semibold">
                            All reconstructed trades — <span className="font-mono normal-case">{w.wallet}</span>
                          </span>
                          {w.unrated_trades > 0 && (
                            <span className="text-[10px] text-text-secondary">{w.unrated_trades} additional trade(s) had no candle data and are unrated</span>
                          )}
                        </div>
                        <TradesTable wallet={w.wallet} />
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
