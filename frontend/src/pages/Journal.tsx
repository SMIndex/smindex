import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/hooks/useAuth';
import { clsx } from 'clsx';
import api, { getTradeHistory } from '@/lib/api';
import { MARKETS, MARKET_IDS } from '@/config/constants';
import { formatUSD, formatPrice } from '@/lib/formatters';
import Modal from '@/components/common/Modal';
import LoadingSpinner from '@/components/common/LoadingSpinner';

type ViewMode = 'list' | 'calendar' | 'stats';

interface JournalEntry {
  id: number;
  market_id: number;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number | null;
  pnl: number | null;
  notes: string | null;
  tags: string[];
  rating: number | null;
  created_at: string;
}

const PRESET_TAGS = ['scalp', 'swing', 'breakout', 'reversal', 'trend', 'fomo', 'revenge', 'planned'];

function StarRating({ value, onChange }: { value: number; onChange?: (v: number) => void }) {
  return (
    <div className="flex gap-0.5">
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          type="button"
          onClick={() => onChange?.(star)}
          className={clsx(
            'text-sm transition-colors',
            star <= value ? 'text-yellow-400' : 'text-text-secondary/30',
            onChange && 'hover:text-yellow-400 cursor-pointer',
          )}
        >
          ★
        </button>
      ))}
    </div>
  );
}

// ========== CALENDAR HEATMAP ==========
function TradeCalendar({ entries, trades }: { entries: JournalEntry[]; trades: any[] }) {
  const [monthOffset, setMonthOffset] = useState(0);

  const now = new Date();
  const viewDate = new Date(now.getFullYear(), now.getMonth() + monthOffset, 1);
  const year = viewDate.getFullYear();
  const month = viewDate.getMonth();
  const monthName = viewDate.toLocaleString('default', { month: 'long', year: 'numeric' });

  // Build daily PnL map from both journal entries and trade_history
  // Deduplicate: skip trade_history entries that match a journal entry (same market, price, same day)
  const dailyPnl = useMemo(() => {
    const map: Record<string, { pnl: number; count: number }> = {};
    const toLocalDay = (dateStr: string) => {
      const d = new Date(dateStr);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    };
    const addToDay = (day: string, pnl: number) => {
      if (!map[day]) map[day] = { pnl: 0, count: 0 };
      map[day].pnl += pnl;
      map[day].count += 1;
    };

    // Track journal entry fingerprints to avoid double-counting
    const journalKeys = new Set<string>();
    entries.forEach((e) => {
      if (e.pnl != null && e.created_at) {
        const day = toLocalDay(e.created_at);
        addToDay(day, e.pnl);
        journalKeys.add(`${e.market_id}-${e.entry_price}-${day}`);
      }
    });
    trades.forEach((t: any) => {
      if (t.pnl != null && t.action === 'close' && t.time) {
        const day = toLocalDay(t.time);
        const key = `${t.market_id}-${t.price}-${day}`;
        if (!journalKeys.has(key)) addToDay(day, t.pnl);
      }
    });
    return map;
  }, [entries, trades]);

  // Calendar grid
  const firstDay = new Date(year, month, 1).getDay(); // 0=Sun
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: (number | null)[] = [];
  for (let i = 0; i < firstDay; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);

  // Monthly totals
  const monthTotal = Object.entries(dailyPnl)
    .filter(([d]) => d.startsWith(`${year}-${String(month + 1).padStart(2, '0')}`))
    .reduce((s, [, v]) => s + v.pnl, 0);
  const monthTrades = Object.entries(dailyPnl)
    .filter(([d]) => d.startsWith(`${year}-${String(month + 1).padStart(2, '0')}`))
    .reduce((s, [, v]) => s + v.count, 0);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <button onClick={() => setMonthOffset((p) => p - 1)} className="text-text-secondary hover:text-text-primary p-1">&lt;</button>
        <div className="text-center">
          <div className="text-sm font-semibold text-text-primary">{monthName}</div>
          <div className="text-[10px] text-text-secondary">
            {monthTrades} trades | <span className={monthTotal >= 0 ? 'text-success' : 'text-danger'}>{monthTotal >= 0 ? '+' : ''}{formatUSD(monthTotal)}</span>
          </div>
        </div>
        <button onClick={() => setMonthOffset((p) => Math.min(p + 1, 0))} disabled={monthOffset >= 0} className="text-text-secondary hover:text-text-primary p-1 disabled:opacity-30">&gt;</button>
      </div>
      <div className="grid grid-cols-7 gap-1">
        {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((d, i) => (
          <div key={i} className="text-center text-[9px] text-text-secondary/60 py-0.5">{d}</div>
        ))}
        {cells.map((day, i) => {
          if (day === null) return <div key={`e-${i}`} />;
          const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
          const dayData = dailyPnl[dateStr];
          const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
          const isToday = dateStr === todayStr;
          let bg = 'bg-bg-secondary/30';
          if (dayData) {
            if (dayData.pnl > 0) bg = dayData.pnl > 50 ? 'bg-success/40' : 'bg-success/20';
            else if (dayData.pnl < 0) bg = dayData.pnl < -50 ? 'bg-danger/40' : 'bg-danger/20';
            else bg = 'bg-text-secondary/10';
          }
          return (
            <div
              key={dateStr}
              className={clsx('text-center py-1.5 rounded text-[10px] relative', bg, isToday && 'ring-1 ring-accent')}
              title={dayData ? `${dateStr}: ${dayData.count} trade${dayData.count > 1 ? 's' : ''}, PnL: ${dayData.pnl >= 0 ? '+' : ''}$${dayData.pnl.toFixed(2)}` : dateStr}
            >
              <span className={clsx('font-medium', dayData ? 'text-text-primary' : 'text-text-secondary/50')}>{day}</span>
              {dayData && (
                <div className={clsx('text-[7px] font-bold', dayData.pnl >= 0 ? 'text-success' : 'text-danger')}>
                  {dayData.pnl >= 0 ? '+' : ''}{dayData.pnl.toFixed(0)}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ========== STATS SUMMARY ==========
function JournalStats({ entries, trades }: { entries: JournalEntry[]; trades: any[] }) {
  const stats = useMemo(() => {
    // Combine journal PnL entries + trade_history close trades, deduplicated
    const allPnls: { pnl: number; tags: string[]; symbol: string; source: string; time: string; market_id: number; price: number }[] = [];
    const journalKeys = new Set<string>();
    entries.forEach((e) => {
      if (e.pnl != null) {
        const day = e.created_at ? e.created_at.slice(0, 10) : '';
        journalKeys.add(`${e.market_id}-${e.entry_price}-${day}`);
        allPnls.push({ pnl: e.pnl, tags: e.tags || [], symbol: e.symbol, source: 'journal', time: e.created_at || '', market_id: e.market_id, price: e.entry_price });
      }
    });
    trades.forEach((t: any) => {
      if (t.pnl != null && t.action === 'close') {
        const day = t.time ? t.time.slice(0, 10) : '';
        const key = `${t.market_id}-${t.price}-${day}`;
        if (!journalKeys.has(key)) {
          allPnls.push({ pnl: t.pnl, tags: [], symbol: t.symbol, source: 'trade', time: t.time || '', market_id: t.market_id, price: t.price });
        }
      }
    });

    if (allPnls.length === 0) return null;

    // Sort by date for correct streak calculation
    allPnls.sort((a, b) => a.time.localeCompare(b.time));

    const totalPnl = allPnls.reduce((s, t) => s + t.pnl, 0);
    const wins = allPnls.filter((t) => t.pnl > 0);
    const losses = allPnls.filter((t) => t.pnl < 0);
    const best = allPnls.reduce((b, t) => (t.pnl > b.pnl ? t : b), allPnls[0]);
    const worst = allPnls.reduce((w, t) => (t.pnl < w.pnl ? t : w), allPnls[0]);
    const avgWin = wins.length > 0 ? wins.reduce((s, t) => s + t.pnl, 0) / wins.length : 0;
    const avgLoss = losses.length > 0 ? losses.reduce((s, t) => s + t.pnl, 0) / losses.length : 0;

    // Streaks
    let currentStreak = 0;
    let bestWinStreak = 0;
    let worstLoseStreak = 0;
    let tempStreak = 0;
    allPnls.forEach((t) => {
      if (t.pnl > 0) { tempStreak = tempStreak > 0 ? tempStreak + 1 : 1; }
      else if (t.pnl < 0) { tempStreak = tempStreak < 0 ? tempStreak - 1 : -1; }
      else { tempStreak = 0; }
      if (tempStreak > bestWinStreak) bestWinStreak = tempStreak;
      if (tempStreak < worstLoseStreak) worstLoseStreak = tempStreak;
    });
    currentStreak = tempStreak;

    // Tag performance (journal entries only)
    const tagMap: Record<string, { pnl: number; count: number; wins: number }> = {};
    entries.forEach((e) => {
      if (e.pnl == null) return;
      (e.tags || []).forEach((tag) => {
        if (!tagMap[tag]) tagMap[tag] = { pnl: 0, count: 0, wins: 0 };
        tagMap[tag].pnl += e.pnl!;
        tagMap[tag].count += 1;
        if (e.pnl! > 0) tagMap[tag].wins += 1;
      });
    });
    const tagPerf = Object.entries(tagMap)
      .map(([tag, s]) => ({ tag, ...s, winRate: s.count > 0 ? Math.round(s.wins / s.count * 100) : 0 }))
      .sort((a, b) => b.pnl - a.pnl);

    // Market performance
    const mktMap: Record<string, { pnl: number; count: number; wins: number }> = {};
    allPnls.forEach((t) => {
      if (!mktMap[t.symbol]) mktMap[t.symbol] = { pnl: 0, count: 0, wins: 0 };
      mktMap[t.symbol].pnl += t.pnl;
      mktMap[t.symbol].count += 1;
      if (t.pnl > 0) mktMap[t.symbol].wins += 1;
    });
    const mktPerf = Object.entries(mktMap)
      .map(([sym, s]) => ({ symbol: sym, ...s, winRate: s.count > 0 ? Math.round(s.wins / s.count * 100) : 0 }))
      .sort((a, b) => b.pnl - a.pnl);

    // Average rating
    const rated = entries.filter((e) => e.rating);
    const avgRating = rated.length > 0 ? rated.reduce((s, e) => s + (e.rating || 0), 0) / rated.length : 0;

    return { totalPnl, total: allPnls.length, wins: wins.length, losses: losses.length, winRate: Math.round(wins.length / allPnls.length * 100), best, worst, avgWin, avgLoss, currentStreak, bestWinStreak, worstLoseStreak, tagPerf, mktPerf, avgRating };
  }, [entries, trades]);

  if (!stats) return <div className="card text-center py-8 text-sm text-text-secondary">No trade data for stats</div>;

  return (
    <div className="space-y-4">
      {/* Overview */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="card p-3">
          <div className="text-[9px] text-text-secondary uppercase">Total PnL</div>
          <div className={clsx('text-lg font-bold', stats.totalPnl >= 0 ? 'text-success' : 'text-danger')}>
            {stats.totalPnl >= 0 ? '+' : ''}{formatUSD(stats.totalPnl)}
          </div>
        </div>
        <div className="card p-3">
          <div className="text-[9px] text-text-secondary uppercase">Win Rate</div>
          <div className={clsx('text-lg font-bold', stats.winRate >= 50 ? 'text-success' : 'text-danger')}>{stats.winRate}%</div>
          <div className="text-[9px] text-text-secondary">{stats.wins}W / {stats.losses}L of {stats.total}</div>
        </div>
        <div className="card p-3">
          <div className="text-[9px] text-text-secondary uppercase">Avg Win / Loss</div>
          <div className="text-xs font-bold text-success">+{formatUSD(stats.avgWin)}</div>
          <div className="text-xs font-bold text-danger">{formatUSD(stats.avgLoss)}</div>
        </div>
        <div className="card p-3">
          <div className="text-[9px] text-text-secondary uppercase">Streaks</div>
          <div className="text-xs">
            <span className="text-text-secondary">Current: </span>
            <span className={clsx('font-bold', stats.currentStreak > 0 ? 'text-success' : stats.currentStreak < 0 ? 'text-danger' : 'text-text-primary')}>
              {stats.currentStreak > 0 ? `${stats.currentStreak}W` : stats.currentStreak < 0 ? `${Math.abs(stats.currentStreak)}L` : '0'}
            </span>
          </div>
          <div className="text-[9px] text-text-secondary">Best: {stats.bestWinStreak}W | Worst: {Math.abs(stats.worstLoseStreak)}L</div>
        </div>
      </div>

      {/* Best / Worst */}
      <div className="grid grid-cols-2 gap-3">
        <div className="card p-3 border-l-2 border-success">
          <div className="text-[9px] text-text-secondary uppercase">Best Trade</div>
          <div className="text-sm font-bold text-success">{stats.best.pnl >= 0 ? '+' : ''}{formatUSD(stats.best.pnl)}</div>
          <div className="text-[10px] text-text-secondary">{stats.best.symbol}</div>
        </div>
        <div className="card p-3 border-l-2 border-danger">
          <div className="text-[9px] text-text-secondary uppercase">Worst Trade</div>
          <div className="text-sm font-bold text-danger">{formatUSD(stats.worst.pnl)}</div>
          <div className="text-[10px] text-text-secondary">{stats.worst.symbol}</div>
        </div>
      </div>

      {/* Market Performance */}
      {stats.mktPerf.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-text-secondary/10 text-xs font-semibold text-text-primary">Performance by Market</div>
          {stats.mktPerf.map((m) => (
            <div key={m.symbol} className="flex items-center justify-between px-4 py-2 border-t border-text-secondary/5 text-[11px]">
              <div className="flex items-center gap-2">
                <span className="font-bold text-text-primary w-10">{m.symbol}</span>
                <span className="text-text-secondary">{m.count} trades</span>
                <span className={clsx('font-medium', m.winRate >= 50 ? 'text-success' : 'text-danger')}>{m.winRate}% win</span>
              </div>
              <span className={clsx('font-bold', m.pnl >= 0 ? 'text-success' : 'text-danger')}>
                {m.pnl >= 0 ? '+' : ''}{formatUSD(m.pnl)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Tag Performance */}
      {stats.tagPerf.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-text-secondary/10 text-xs font-semibold text-text-primary">Performance by Tag</div>
          {stats.tagPerf.map((t) => (
            <div key={t.tag} className="flex items-center justify-between px-4 py-2 border-t border-text-secondary/5 text-[11px]">
              <div className="flex items-center gap-2">
                <span className="px-1.5 py-0.5 rounded bg-accent/10 text-accent text-[10px] font-medium">{t.tag}</span>
                <span className="text-text-secondary">{t.count} trades</span>
                <span className={clsx('font-medium', t.winRate >= 50 ? 'text-success' : 'text-danger')}>{t.winRate}% win</span>
              </div>
              <span className={clsx('font-bold', t.pnl >= 0 ? 'text-success' : 'text-danger')}>
                {t.pnl >= 0 ? '+' : ''}{formatUSD(t.pnl)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Avg Rating */}
      {stats.avgRating > 0 && (
        <div className="card p-3 text-center">
          <div className="text-[9px] text-text-secondary uppercase mb-1">Average Trade Rating</div>
          <StarRating value={Math.round(stats.avgRating)} />
          <div className="text-xs text-text-secondary mt-0.5">{stats.avgRating.toFixed(1)} / 5</div>
        </div>
      )}
    </div>
  );
}

export default function JournalPage() {
  const { address, hydrated } = useAuth();
  const queryClient = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [filterMarket, setFilterMarket] = useState<number | null>(null);
  const [filterTag, setFilterTag] = useState<string>('');

  // Form state
  const [formMarketId, setFormMarketId] = useState(1);
  const [formSide, setFormSide] = useState('long');
  const [formEntry, setFormEntry] = useState<number>(0);
  const [formExit, setFormExit] = useState<number>(0);
  const [formPnl, setFormPnl] = useState<number>(0);
  const [formNotes, setFormNotes] = useState('');
  const [formTags, setFormTags] = useState<string[]>([]);
  const [formRating, setFormRating] = useState(3);

  // All trades for calendar/stats
  const { data: allTrades = [] } = useQuery({
    queryKey: ['trades-for-journal'],
    queryFn: () => getTradeHistory({ limit: 200 }),
    enabled: !!address && (viewMode === 'calendar' || viewMode === 'stats'),
  });

  // Recent trades for auto-populate
  const { data: recentTrades } = useQuery({
    queryKey: ['recent-trades-for-journal'],
    queryFn: () => getTradeHistory({ limit: 20 }),
    enabled: !!address && showAdd,
  });

  function importFromTrade(t: any) {
    setFormMarketId(t.market_id);
    setFormSide(t.side);
    setFormEntry(t.price || 0);
    setFormPnl(t.pnl || 0);
    if (t.action === 'close') {
      setFormExit(t.price || 0);
    }
  }

  const { data: entries = [], isLoading } = useQuery({
    queryKey: ['journal', filterMarket, filterTag],
    queryFn: () => {
      const params: Record<string, string> = {};
      if (filterMarket) params.market_id = String(filterMarket);
      if (filterTag) params.tag = filterTag;
      return api.get('/api/journal', { params }).then((r) => r.data as JournalEntry[]);
    },
    enabled: !!address,
  });

  const createMutation = useMutation({
    mutationFn: (data: any) => api.post('/api/journal', data).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['journal'] });
      setShowAdd(false);
      resetForm();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/journal/${id}`).then((r) => r.data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['journal'] }),
  });

  function resetForm() {
    setFormMarketId(1);
    setFormSide('long');
    setFormEntry(0);
    setFormExit(0);
    setFormPnl(0);
    setFormNotes('');
    setFormTags([]);
    setFormRating(3);
  }

  function handleSubmit() {
    createMutation.mutate({
      market_id: formMarketId,
      symbol: MARKETS[formMarketId]?.symbol ?? 'UNK',
      side: formSide,
      entry_price: formEntry,
      exit_price: formExit || null,
      pnl: formPnl || null,
      notes: formNotes || null,
      tags: formTags.length > 0 ? formTags : null,
      rating: formRating,
    });
  }

  function toggleTag(tag: string) {
    setFormTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
  }

  // Collect all unique tags from entries
  const allTags = Array.from(new Set(entries.flatMap((e) => e.tags || [])));

  if (!hydrated) {
    return <div className="flex justify-center py-12"><div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" /></div>;
  }

  if (!address) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-bold text-text-primary">Trade Journal</h1>
        <div className="card text-center py-12 text-sm text-text-secondary">Connect your wallet to use the journal</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-text-primary">Trade Journal</h1>
        <div className="flex items-center gap-2">
          <div className="flex bg-bg-secondary rounded-lg p-0.5">
            {(['list', 'calendar', 'stats'] as ViewMode[]).map((mode) => (
              <button
                key={mode}
                onClick={() => setViewMode(mode)}
                className={clsx('text-xs px-3 py-1 rounded-md font-medium transition-colors capitalize',
                  viewMode === mode ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary')}
              >
                {mode}
              </button>
            ))}
          </div>
          <button onClick={() => setShowAdd(true)} className="btn-primary text-xs px-4 py-2">
            + New Entry
          </button>
        </div>
      </div>

      {/* Calendar View */}
      {viewMode === 'calendar' && <TradeCalendar entries={entries} trades={allTrades} />}

      {/* Stats View */}
      {viewMode === 'stats' && <JournalStats entries={entries} trades={allTrades} />}

      {/* List View — Filters */}
      {viewMode === 'list' && <div className="flex items-center gap-3 flex-wrap">
        <select
          value={filterMarket ?? ''}
          onChange={(e) => setFilterMarket(e.target.value ? Number(e.target.value) : null)}
          className="input-field text-xs w-32"
        >
          <option value="">All Markets</option>
          {MARKET_IDS.map((id) => (
            <option key={id} value={id}>{MARKETS[id].symbol}</option>
          ))}
        </select>
        {allTags.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            <button
              onClick={() => setFilterTag('')}
              className={clsx('px-2 py-0.5 rounded text-[10px] font-medium transition-colors', !filterTag ? 'bg-accent text-white' : 'bg-bg-secondary text-text-secondary hover:text-text-primary')}
            >
              All
            </button>
            {allTags.map((tag) => (
              <button
                key={tag}
                onClick={() => setFilterTag(filterTag === tag ? '' : tag)}
                className={clsx('px-2 py-0.5 rounded text-[10px] font-medium transition-colors', filterTag === tag ? 'bg-accent text-white' : 'bg-bg-secondary text-text-secondary hover:text-text-primary')}
              >
                {tag}
              </button>
            ))}
          </div>
        )}
      </div>}

      {/* Entries list */}
      {viewMode === 'list' && (isLoading ? (
        <div className="flex justify-center py-12"><LoadingSpinner /></div>
      ) : entries.length === 0 ? (
        <div className="card text-center py-12 text-sm text-text-secondary">
          No journal entries yet. Click "New Entry" to log your first trade.
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map((entry) => {
            const cfg = MARKETS[entry.market_id];
            const dec = cfg?.decimals ?? 2;
            return (
              <div key={entry.id} className="card p-0 overflow-hidden">
                <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 px-4 py-3">
                  {/* Header row: market + side + date + delete */}
                  <div className="flex items-center justify-between sm:contents">
                    <div className="shrink-0">
                      <span className="font-bold text-text-primary text-sm">{entry.symbol}</span>
                      <span className={clsx('ml-1.5 text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded', entry.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10')}>
                        {entry.side}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 sm:hidden">
                      {entry.rating && <StarRating value={entry.rating} />}
                      <span className="text-[10px] text-text-secondary">
                        {new Date(entry.created_at).toLocaleDateString()}
                      </span>
                      <button
                        onClick={() => deleteMutation.mutate(entry.id)}
                        className="text-text-secondary hover:text-danger text-xs p-1"
                        title="Delete"
                      >
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    </div>
                  </div>
                  {/* Prices */}
                  <div className="flex flex-wrap gap-3 sm:gap-4 text-xs">
                    <div>
                      <span className="text-text-secondary">Entry </span>
                      <span className="text-text-primary font-medium">${formatPrice(entry.entry_price, dec)}</span>
                    </div>
                    {entry.exit_price != null && (
                      <div>
                        <span className="text-text-secondary">Exit </span>
                        <span className="text-text-primary font-medium">${formatPrice(entry.exit_price, dec)}</span>
                      </div>
                    )}
                    {entry.pnl != null && (
                      <div>
                        <span className="text-text-secondary">PnL </span>
                        <span className={clsx('font-bold', entry.pnl >= 0 ? 'text-success' : 'text-danger')}>
                          {entry.pnl >= 0 ? '+' : ''}{formatUSD(entry.pnl)}
                        </span>
                      </div>
                    )}
                  </div>
                  {/* Rating - desktop only (mobile shown above) */}
                  <div className="ml-auto hidden sm:flex items-center gap-3">
                    {entry.rating && <StarRating value={entry.rating} />}
                    <span className="text-[10px] text-text-secondary">
                      {new Date(entry.created_at).toLocaleDateString()}
                    </span>
                    <button
                      onClick={() => deleteMutation.mutate(entry.id)}
                      className="text-text-secondary hover:text-danger text-xs p-1"
                      title="Delete"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                </div>
                {/* Tags + Notes */}
                {(entry.tags?.length > 0 || entry.notes) && (
                  <div className="px-4 pb-3 flex items-start gap-2 sm:gap-3 flex-wrap">
                    {entry.tags?.length > 0 && (
                      <div className="flex gap-1 flex-wrap">
                        {entry.tags.map((tag) => (
                          <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent font-medium">{tag}</span>
                        ))}
                      </div>
                    )}
                    {entry.notes && (
                      <p className="text-xs text-text-secondary flex-1">{entry.notes}</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}

      {/* Add Entry Modal */}
      {showAdd && (
        <Modal isOpen={showAdd} onClose={() => { setShowAdd(false); resetForm(); }} title="New Journal Entry">
          <div className="space-y-3">
            {/* Import from recent trades */}
            {recentTrades && recentTrades.length > 0 && (
              <div>
                <label className="text-[10px] text-text-secondary uppercase tracking-wider">Import from recent trade</label>
                <select
                  className="input-field text-sm w-full mt-1"
                  defaultValue=""
                  onChange={(e) => {
                    const idx = parseInt(e.target.value);
                    if (!isNaN(idx)) importFromTrade(recentTrades[idx]);
                  }}
                >
                  <option value="">Select a trade to auto-fill...</option>
                  {recentTrades.map((t: any, i: number) => (
                    <option key={i} value={i}>
                      {t.symbol} {t.action === 'close' ? 'Close' : 'Open'} {t.side} @ ${t.price?.toFixed(6)} {t.pnl != null ? `(PnL: $${t.pnl.toFixed(2)})` : ''} — {t.time ? new Date(t.time).toLocaleDateString() : ''}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="flex gap-2">
              <select value={formMarketId} onChange={(e) => setFormMarketId(Number(e.target.value))} className="input-field text-sm flex-1">
                {MARKET_IDS.map((id) => (
                  <option key={id} value={id}>{MARKETS[id].symbol}</option>
                ))}
              </select>
              <select value={formSide} onChange={(e) => setFormSide(e.target.value)} className="input-field text-sm w-24">
                <option value="long">Long</option>
                <option value="short">Short</option>
              </select>
            </div>
            <div className="flex gap-2">
              <div className="flex-1">
                <label className="text-[10px] text-text-secondary uppercase tracking-wider">Entry Price</label>
                <input type="number" value={formEntry || ''} onChange={(e) => setFormEntry(Number(e.target.value))} className="input-field text-sm w-full mt-1" placeholder="0.00" />
              </div>
              <div className="flex-1">
                <label className="text-[10px] text-text-secondary uppercase tracking-wider">Exit Price</label>
                <input type="number" value={formExit || ''} onChange={(e) => setFormExit(Number(e.target.value))} className="input-field text-sm w-full mt-1" placeholder="0.00" />
              </div>
            </div>
            <div>
              <label className="text-[10px] text-text-secondary uppercase tracking-wider">PnL ($)</label>
              <input type="number" value={formPnl || ''} onChange={(e) => setFormPnl(Number(e.target.value))} className="input-field text-sm w-full mt-1" placeholder="0.00" />
            </div>
            <div>
              <label className="text-[10px] text-text-secondary uppercase tracking-wider">Notes</label>
              <textarea value={formNotes} onChange={(e) => setFormNotes(e.target.value)} className="input-field text-sm w-full mt-1 h-20 resize-none" placeholder="What went well? What would you do differently?" />
            </div>
            <div>
              <label className="text-[10px] text-text-secondary uppercase tracking-wider mb-1 block">Tags</label>
              <div className="flex gap-1 flex-wrap">
                {PRESET_TAGS.map((tag) => (
                  <button
                    key={tag}
                    type="button"
                    onClick={() => toggleTag(tag)}
                    className={clsx('px-2 py-0.5 rounded text-[10px] font-medium transition-colors', formTags.includes(tag) ? 'bg-accent text-white' : 'bg-bg-secondary text-text-secondary hover:text-text-primary')}
                  >
                    {tag}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-[10px] text-text-secondary uppercase tracking-wider mb-1 block">Rating</label>
              <StarRating value={formRating} onChange={setFormRating} />
            </div>
            <button
              onClick={handleSubmit}
              disabled={formEntry <= 0 || createMutation.isPending}
              className="btn-primary w-full py-2 text-sm"
            >
              {createMutation.isPending ? 'Saving...' : 'Save Entry'}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
