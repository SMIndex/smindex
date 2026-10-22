// React hooks for Copy Trading v1 (PAPER-ONLY).
//
// Thin data-fetching wrappers around lib/copyApi. These hooks NEVER call
// useCopyTrade or perplTrading.placeOrder: copy v1 is simulated only.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as copyApi from '@/lib/copyApi';
import * as copyPortfolio from '@/lib/copyPortfolio';
import { useToastStore } from '@/components/common/Toast';
import { useMarketStore } from '@/stores/marketStore';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import type {
  TraderListItem,
  TraderDetail,
  TraderDailyStat,
  TraderOnChainPosition,
  WatchlistItem,
  CopySubscription,
  CopyOrder,
  PaperPosition,
  RiskEvent,
  AuditLog,
  CreateSubscriptionPayload,
} from '@/lib/copyApi';

function errMsg(e: any): string {
  if (e?.response?.status === 429) {
    const retryAfter = Number(e.response.headers?.['retry-after']) || 60;
    return `Too many requests: please slow down. Try again in ~${retryAfter}s.`;
  }
  // FastAPI 422 detail is an ARRAY, normalize to a string or the toast/error
  // render crashes React ("Objects are not valid as a React child").
  return copyApi.apiErrorMessage(e);
}

// Show at most one rate-limit toast per window to avoid a toast storm on rapid clicks.
let _last429ToastAt = 0;
function toastActionError(e: any, fallbackPrefix: string) {
  const msg = errMsg(e);
  if (e?.response?.status === 429) {
    const now = Date.now();
    if (now - _last429ToastAt > 5000) {
      _last429ToastAt = now;
      useToastStore.getState().addToast('warning', msg);
    }
  } else {
    useToastStore.getState().addToast('error', `${fallbackPrefix}: ${msg}`);
  }
}

// --------------------------- discovery ---------------------------

// Cold-load paint (beta speed pass 2026-10-08): the Discover list was the
// slowest first paint on the site (time-to-data 23.1 s measured on 10-07),
// and the page sat empty until the API answered. The last successful list is
// kept per view and painted immediately on the next visit, then replaced by
// the live fetch. Live on-chain fields are NOT restored from the snapshot,
// they come back as 'pending' so a stale open-position claim can never render.
const DISCOVER_SNAP_PREFIX = 'smindex_discover_v1:';
const DISCOVER_SNAP_MAX_AGE_MS = 10 * 60 * 1000;
const DISCOVER_SNAP_ROWS = 60;

function discoverSnapKey(p: { exchange: string; sort: string; period: string; limit: number; wallets?: string }) {
  return `${DISCOVER_SNAP_PREFIX}${p.exchange}:${p.sort}:${p.period}:${p.limit}:${p.wallets ?? ''}`;
}

function readDiscoverSnap(key: string): TraderListItem[] | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const snap = JSON.parse(raw);
    if (!snap || !Array.isArray(snap.rows) || !snap.at) return null;
    if (Date.now() - snap.at > DISCOVER_SNAP_MAX_AGE_MS) return null;
    return snap.rows.map((t: TraderListItem) => ({
      ...t,
      active_positions_count: null,
      active_markets: [],
      has_active_positions: false,
      active_positions_pending: true,
      active_positions_error: undefined,
    }));
  } catch {
    return null;
  }
}

function writeDiscoverSnap(key: string, rows: TraderListItem[]) {
  try {
    localStorage.setItem(key, JSON.stringify({ at: Date.now(), rows: rows.slice(0, DISCOVER_SNAP_ROWS) }));
  } catch {}
}

export function useTraders(params?: { sort?: string; period?: string; limit?: number; exchange?: string; wallets?: string }) {
  const [traders, setTraders] = useState<TraderListItem[]>([]);
  const [loading, setLoading] = useState(true);
  // 2026-10-09: `loading` is for the FIRST load only. These hooks are
  // refreshed after every action and on every venue event, and announcing each
  // refresh with a spinner is what made the copy dashboard look like it
  // reloaded every few seconds. Rows that are already on screen stay there and
  // are updated in place.
  const loadedOnce = useRef(false);   // initial load only
  const [fetching, setFetching] = useState(false); // background refetch indicator
  const [error, setError] = useState<string | null>(null);

  const sort = params?.sort ?? 'pnl';
  const period = params?.period ?? 'all';
  const limit = params?.limit ?? 50;
  const exchange = params?.exchange ?? 'perpl';
  const wallets = params?.wallets;

  // NOTE: do NOT flip `loading` here: that would swap the whole grid to skeletons on
  // every background poll (flicker). Keep previous cards mounted; update values in place.
  // replace=true -> use the server order as-is (initial load / user changed sort/filter).
  // replace=false (background poll) -> merge, preserving previous order + object identity
  // for unchanged traders, so only changed cards re-render and nothing reorders/flickers.
  const load = useCallback(async (replace = false) => {
    setFetching(true);
    setError(null);
    try {
      const data = await copyApi.getTraders({ sort, period, limit, exchange,
        ...(wallets ? { wallets } : {}) });
      // Render rows IMMEDIATELY: open-position summaries attach afterwards
      // (non-blocking; same progressive contract as Perpl's fields).
      // HL rows come back without open-position fields (they arrive from the batch
      // below); keep each row's last known ones meanwhile, or every refresh shows
      // 0 open for seconds and the "Has open positions" filter empties.
      setTraders((prev) => {
        const rows = exchange === 'hl' ? keepHlPositions(prev, data) : data;
        return replace ? rows : mergeTraders(prev, rows);
      });
      if (data.length) writeDiscoverSnap(discoverSnapKey({ exchange, sort, period, limit, wallets }), data);
      if (exchange === 'hl' && data.length) {
        copyApi.getHlActiveBatch(data.map((t) => t.wallet_address)).then((batch) => {
          setTraders((prev) => prev.map((t) => {
            const s = batch[t.wallet_address.toLowerCase()];
            if (!s) return t;
            return {
              ...t,
              active_positions_count: s.count,
              active_markets: s.markets ?? [],
              has_active_positions: (s.count ?? 0) > 0,
              active_positions_pending: s.pending,
              active_positions_error: s.count === null && !s.pending ? true : t.active_positions_error,
            };
          }));
        }).catch(() => {});
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
      setFetching(false);
    }
  }, [sort, period, limit, exchange, wallets]);

  useEffect(() => {
    // Fresh key (exchange/sort/period/limit/wallets): NEVER show the previous key's
    // rows: clear, then paint THIS key's last snapshot if we have a recent one
    // (cold load paints real rows instead of skeletons), and fetch in the
    // background. No snapshot -> skeletons exactly as before.
    const snap = readDiscoverSnap(discoverSnapKey({ exchange, sort, period, limit, wallets }));
    setTraders(snap ?? []);
    setLoading(!snap);
    load(true);
  }, [load, exchange, sort, period, limit, wallets]);

  // While any trader's active-position count is still 'pending', poll on a fixed interval
  // (capped). The interval lives across renders and does NOT depend on the traders array
  // identity, so the identity-preserving merge can't stall it.
  const anyPending = traders.some((t) => t.active_positions_pending || t.active_positions_stale);
  useEffect(() => {
    if (!anyPending) return;
    let n = 0;
    const id = setInterval(() => {
      n += 1;
      load();
      if (n >= 12) clearInterval(id);
    }, 5000);
    return () => clearInterval(id);
  }, [anyPending, load]);

  // Regular refresh (2026-10-22): every 60 s while the tab is visible, the same
  // as the server's 60 s list cache, plus once on return to the tab when the
  // last refresh is older than that. Background merge: no flicker, rows keep
  // their order and the page keeps its scroll, sort and filters.
  const lastLoadAt = useRef(0);
  useEffect(() => { if (!fetching) lastLoadAt.current = Date.now(); }, [fetching]);
  useEffect(() => {
    const tick = () => {
      if (document.hidden || Date.now() - lastLoadAt.current < LIST_REFRESH_MS - 1000) return;
      load();
    };
    const id = setInterval(tick, LIST_REFRESH_MS);
    const onVis = () => { if (!document.hidden) tick(); };
    document.addEventListener('visibilitychange', onVis);
    return () => { clearInterval(id); document.removeEventListener('visibilitychange', onVis); };
  }, [load]);

  // HL "last active" every 15 s while visible: only that column is patched.
  const walletsRef = useRef<string[]>([]);
  walletsRef.current = traders.map((t) => t.wallet_address);
  useEffect(() => {
    if (exchange !== 'hl') return;
    const tick = async () => {
      if (document.hidden || !walletsRef.current.length) return;
      try {
        const m = await copyApi.getHlLastActive(walletsRef.current);
        setTraders((prev) => {
          let changed = false;
          const next = prev.map((t) => {
            const v = m[t.wallet_address.toLowerCase()];
            if (!v || !v.last_trade_at || v.last_trade_at === t.last_trade_at) return t;
            changed = true;
            return { ...t, last_trade_at: v.last_trade_at, last_trade_src: v.src ?? undefined };
          });
          return changed ? next : prev;
        });
      } catch { /* keep the values on screen */ }
    };
    const id = setInterval(tick, LAST_ACTIVE_REFRESH_MS);
    return () => clearInterval(id);
  }, [exchange]);

  return { traders, loading, fetching, error, refresh: load };
}

const LIST_REFRESH_MS = 60_000;
const LAST_ACTIVE_REFRESH_MS = 15_000;

function keepHlPositions(prev: TraderListItem[], next: TraderListItem[]): TraderListItem[] {
  if (!prev.length) return next;
  const byWallet = new Map(prev.map((t) => [t.wallet_address.toLowerCase(), t]));
  return next.map((t) => {
    const p = byWallet.get(t.wallet_address.toLowerCase());
    if (!p || p.active_positions_count === undefined || t.active_positions_count !== undefined) return t;
    return {
      ...t,
      active_positions_count: p.active_positions_count,
      active_markets: p.active_markets,
      has_active_positions: p.has_active_positions,
      active_positions_pending: p.active_positions_pending,
      active_positions_error: p.active_positions_error,
    };
  });
}

/** Merge fresh trader data into the previous array, keeping previous order and reusing
 *  unchanged trader objects (and the array itself) so memoized cards don't re-render. */
function mergeTraders(prev: TraderListItem[], next: TraderListItem[]): TraderListItem[] {
  if (prev.length === 0) return next;
  const nextByWallet = new Map(next.map((t) => [t.wallet_address.toLowerCase(), t]));
  let changed = false;
  const merged: TraderListItem[] = [];
  for (const p of prev) {
    const key = p.wallet_address.toLowerCase();
    const fresh = nextByWallet.get(key);
    if (fresh) {
      nextByWallet.delete(key);
      if (copyApi.traderSignature(p) === copyApi.traderSignature(fresh)) {
        merged.push(p);            // unchanged -> keep same reference (no re-render)
      } else {
        merged.push(fresh);
        changed = true;
      }
    } else {
      changed = true;              // trader dropped out of the list
    }
  }
  for (const t of next) {          // append genuinely-new traders, preserving their order
    if (nextByWallet.has(t.wallet_address.toLowerCase())) {
      merged.push(t);
      changed = true;
    }
  }
  return changed || merged.length !== prev.length ? merged : prev;
}

export function useTrader(wallet: string | undefined, timeframe: import('@/lib/copyApi').EquityTimeframe = '7d', exchange = 'perpl') {
  const [detail, setDetail] = useState<TraderDetail | null>(null);
  const [stats, setStats] = useState<TraderDailyStat[]>([]);
  const [positions, setPositions] = useState<TraderOnChainPosition[]>([]);
  const [positionsError, setPositionsError] = useState(false);
  const [equity, setEquity] = useState<number[]>([]);
  const [equityTimes, setEquityTimes] = useState<number[]>([]);
  const [hlState, setHlState] = useState<import('@/lib/copyApi').HlProfileState | null>(null);
  const [hlError, setHlError] = useState(false);
  // Perpl resting trigger orders (TP/SL), decoded on-chain via the existing
  // /api/leaders/positions internals (label-fix, Wallet Explorer Part 3).
  const [perplOrders, setPerplOrders] = useState<PerplRestingOrder[]>([]);
  const [loading, setLoading] = useState(true);
  // 2026-10-09: `loading` is for the FIRST load only. These hooks are
  // refreshed after every action and on every venue event, and announcing each
  // refresh with a spinner is what made the copy dashboard look like it
  // reloaded every few seconds. Rows that are already on screen stay there and
  // are updated in place.
  const loadedOnce = useRef(false);
  const [equityPending, setEquityPending] = useState(true);
  const [hlPending, setHlPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Monotonic token: late responses from a previous wallet/timeframe are dropped.
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    if (!wallet) return;
    const seq = ++loadSeq.current;
    const fresh = () => loadSeq.current === seq;
    if (!loadedOnce.current) setLoading(true);
    setError(null);
    const isHl = exchange === 'hl';
    // Slow panels (equity curve, HL depth) fire in parallel but do NOT gate
    // the modal paint: the header/stats render as soon as the detail lands
    // (cold HL equity+state were adding 2-3s to first paint).
    setEquityPending(true);
    setHlPending(isHl);
    const eqPromise = copyApi.getTraderEquity(wallet, timeframe, 160, exchange).catch(() => ({ points: [] as number[] }));
    const hsPromise = isHl ? copyApi.getTraderHlState(wallet).catch(() => null) : Promise.resolve(null);
    // Perpl: resting orders (incl. on-chain TP/SL triggers) from the existing
    // leaders/positions endpoint: non-gating, absence renders honestly.
    if (!isHl) {
      import('@/lib/api').then(({ getTraderPositions: getLeaderDetail }) =>
        getLeaderDetail(wallet)
          .then((d: { orders?: PerplRestingOrder[] }) => { if (fresh()) setPerplOrders(d?.orders || []); })
          .catch(() => { if (fresh()) setPerplOrders([]); }));
    } else {
      setPerplOrders([]);
    }
    eqPromise.then((eq) => {
      if (!fresh()) return;
      // HL equity comes venue-timestamped ([{t, v}]); Perpl stays number[].
      const rawPts: any[] = (eq as any).points || [];
      const timed = rawPts.length > 0 && typeof rawPts[0] === 'object';
      setEquity(timed ? rawPts.map((p: any) => p.v) : rawPts);
      setEquityTimes(timed ? rawPts.map((p: any) => p.t) : []);
      setEquityPending(false);
    });
    hsPromise.then((hs) => {
      if (!fresh()) return;
      setHlState(hs);
      setHlError(isHl && hs === null);
      setHlPending(false);
    });
    try {
      const [d, s, p] = await Promise.all([
        copyApi.getTrader(wallet, exchange),
        // daily stats + on-chain positions are Perpl-only surfaces
        isHl ? Promise.resolve([] as TraderDailyStat[]) : copyApi.getTraderStats(wallet).catch(() => [] as TraderDailyStat[]),
        isHl
          ? Promise.resolve({ wallet, positions: [] as TraderOnChainPosition[] })
          : copyApi.getTraderPositions(wallet).catch(() => ({ wallet, positions: [] as TraderOnChainPosition[], active_positions_error: true })),
      ]);
      if (!fresh()) return;
      setDetail(d);
      setStats(s);
      setPositions(p.positions || []);
      setPositionsError(!!(p as any).active_positions_error);
    } catch (e) {
      if (fresh()) setError(errMsg(e));
    } finally {
      if (fresh()) setLoading(false);
    }
  }, [wallet, timeframe, exchange]);

  useEffect(() => {
    load();
  }, [load]);

  // While HL open-time dating runs server-side, re-poll just hl-state until
  // the funding-ledger dates land (resolver is rate-limit paced, ~1-2 min).
  const datingPending = !!hlState?.positions?.some((p) => p.opened_at_pending);
  useEffect(() => {
    if (!datingPending || !wallet) return;
    let n = 0;
    const id = setInterval(() => {
      n += 1;
      copyApi.getTraderHlState(wallet).then(setHlState).catch(() => {});
      if (n >= 24) clearInterval(id);
    }, 5000);
    return () => clearInterval(id);
  }, [datingPending, wallet]);

  return { detail, stats, positions, positionsError, perplOrders, equity, equityTimes, hlState, hlError, loading, equityPending, hlPending, error, refresh: load };
}

// Shape of /api/leaders/positions/{wallet} order rows (chain_reader decode)
export interface PerplRestingOrder {
  market_id: number;
  symbol: string;
  order_type: string;
  is_trigger: boolean;
  side: string;
  price: number | null;
  size: number | null;
  leverage: number | null;
  notional: number | null;
  margin_locked: number;
}

// --------------------------- watchlist ---------------------------

export function useWatchlist(enabled = true) {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  // See the note above: first load only.
  const loadedOnce = useRef(false);

  const load = useCallback(async () => {
    if (!enabled) {
      setItems([]);
      setLoading(false);
      return;
    }
    if (!loadedOnce.current) setLoading(true);
    setError(null);
    try {
      setItems(await copyApi.getWatchlist());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      loadedOnce.current = true;
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    load();
  }, [load]);

  // Per-wallet in-flight guard: rapid re-clicks while a request is pending are
  // ignored instead of firing duplicate POST/DELETE storms at the API.
  const pendingRef = useRef<Set<string>>(new Set());

  const watch = useCallback(async (wallet: string, exchange = 'perpl') => {
    const key = `${exchange}:${wallet.toLowerCase()}`;
    if (pendingRef.current.has(key)) return;
    pendingRef.current.add(key);
    try {
      await copyApi.addWatch(wallet, exchange);
      await load();
    } catch (e: any) {
      toastActionError(e, "Couldn't add to watchlist");
    } finally {
      pendingRef.current.delete(key);
    }
  }, [load]);

  const unwatch = useCallback(async (wallet: string, exchange = 'perpl') => {
    const key = `${exchange}:${wallet.toLowerCase()}`;
    if (pendingRef.current.has(key)) return;
    pendingRef.current.add(key);
    try {
      await copyApi.removeWatch(wallet, exchange);
      await load();
    } catch (e: any) {
      toastActionError(e, "Couldn't remove from watchlist");
    } finally {
      pendingRef.current.delete(key);
    }
  }, [load]);

  const isWatched = useCallback(
    (wallet: string, exchange = 'perpl') => items.some(
      (i) => i.trader_wallet.toLowerCase() === wallet.toLowerCase()
        && (i.exchange ?? 'perpl') === exchange,
    ),
    [items],
  );

  return { items, loading, error, refresh: load, watch, unwatch, isWatched };
}

// --------------------------- subscriptions ---------------------------

export function useSubscriptions(enabled = true) {
  const [subscriptions, setSubscriptions] = useState<CopySubscription[]>([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  // See the note above: first load only.
  const loadedOnce = useRef(false);

  const load = useCallback(async () => {
    if (!enabled) {
      setSubscriptions([]);
      setLoading(false);
      return;
    }
    if (!loadedOnce.current) setLoading(true);
    setError(null);
    try {
      setSubscriptions(await copyApi.getSubscriptions());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      loadedOnce.current = true;
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    load();
  }, [load]);

  const create = useCallback(async (payload: CreateSubscriptionPayload) => {
    const sub = await copyApi.createSubscription(payload);
    await load();
    return sub;
  }, [load]);

  const pause = useCallback(async (id: number) => {
    await copyApi.pauseSubscription(id);
    await load();
  }, [load]);

  const resume = useCallback(async (id: number) => {
    await copyApi.resumeSubscription(id);
    await load();
  }, [load]);

  const stop = useCallback(async (id: number) => {
    await copyApi.stopSubscription(id);
    await load();
  }, [load]);

  return { subscriptions, loading, error, refresh: load, create, pause, resume, stop };
}

// --------------------------- dashboard reads ---------------------------

export function useCopyOrders(params?: { subscription_id?: number; status?: string; limit?: number; mode?: string }) {
  const [orders, setOrders] = useState<CopyOrder[]>([]);
  const [loading, setLoading] = useState(true);
  // 2026-10-09: `loading` is for the FIRST load only. These hooks are
  // refreshed after every action and on every venue event, and announcing each
  // refresh with a spinner is what made the copy dashboard look like it
  // reloaded every few seconds. Rows that are already on screen stay there and
  // are updated in place.
  const loadedOnce = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const key = JSON.stringify(params || {});
  const load = useCallback(async () => {
    if (!loadedOnce.current) setLoading(true);
    setError(null);
    try {
      setOrders(await copyApi.getCopyOrders(params));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      loadedOnce.current = true;
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    load();
  }, [load]);

  return { orders, loading, error, refresh: load };
}

export function useCopyPositions(params?: { subscription_id?: number; status?: string }) {
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [loading, setLoading] = useState(true);
  // 2026-10-09: `loading` is for the FIRST load only. These hooks are
  // refreshed after every action and on every venue event, and announcing each
  // refresh with a spinner is what made the copy dashboard look like it
  // reloaded every few seconds. Rows that are already on screen stay there and
  // are updated in place.
  const loadedOnce = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const key = JSON.stringify(params || {});
  const load = useCallback(async () => {
    if (!loadedOnce.current) setLoading(true);
    setError(null);
    try {
      setPositions(await copyApi.getCopyPositions(params));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      loadedOnce.current = true;
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    load();
  }, [load]);

  return { positions, loading, error, refresh: load };
}

export function useLivePositions(status = 'all') {
  const [positions, setPositions] = useState<import('@/lib/copyApi').LivePosition[]>([]);
  const [loading, setLoading] = useState(true);
  // 2026-10-09: `loading` is for the FIRST load only. These hooks are
  // refreshed after every action and on every venue event, and announcing each
  // refresh with a spinner is what made the copy dashboard look like it
  // reloaded every few seconds. Rows that are already on screen stay there and
  // are updated in place.
  const loadedOnce = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!loadedOnce.current) setLoading(true);
    setError(null);
    try {
      setPositions(await copyApi.getLivePositions({ status }));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      loadedOnce.current = true;
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  return { positions, loading, error, refresh: load };
}

export function useRiskEvents(limit = 50) {
  const [events, setEvents] = useState<RiskEvent[]>([]);
  const [loading, setLoading] = useState(true);
  // 2026-10-09: `loading` is for the FIRST load only. These hooks are
  // refreshed after every action and on every venue event, and announcing each
  // refresh with a spinner is what made the copy dashboard look like it
  // reloaded every few seconds. Rows that are already on screen stay there and
  // are updated in place.
  const loadedOnce = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!loadedOnce.current) setLoading(true);
    setError(null);
    try {
      setEvents(await copyApi.getRiskEvents({ limit }));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      loadedOnce.current = true;
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    load();
  }, [load]);

  return { events, loading, error, refresh: load };
}

export function useAuditLogs(limit = 50) {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  // 2026-10-09: `loading` is for the FIRST load only. These hooks are
  // refreshed after every action and on every venue event, and announcing each
  // refresh with a spinner is what made the copy dashboard look like it
  // reloaded every few seconds. Rows that are already on screen stay there and
  // are updated in place.
  const loadedOnce = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!loadedOnce.current) setLoading(true);
    setError(null);
    try {
      setLogs(await copyApi.getAuditLogs({ limit }));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      loadedOnce.current = true;
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    load();
  }, [load]);

  return { logs, loading, error, refresh: load };
}

// --------------------------- portfolio + history (Phase 1, read-only) ---------------------------

// Resolvers wired to the live market store + loaded market configs. Live mark => live
// unrealized PnL (null when not cached). Taker bps => estimated open fee (never exact).
function useCopyResolvers(): import('@/lib/copyPortfolio').Resolvers {
  const markets = useMarketStore((s) => s.markets);
  return useMemo(() => ({
    resolveMark: (marketId: number) => {
      const m = markets[marketId];
      return m && Number.isFinite(m.mark_price) ? m.mark_price : null;
    },
    resolveTakerBps: (marketId: number) => {
      const c = MARKET_CONFIGS[marketId];
      return c && Number.isFinite(c.takerFeeBps) ? c.takerFeeBps : null;
    },
  }), [markets]);
}

export function useCopyPortfolio() {
  const paper = useCopyPositions({ status: 'all' });
  const live = useLivePositions('all');
  const subs = useSubscriptions(true);
  const resolvers = useCopyResolvers();

  const positions = useMemo(() => [
    ...paper.positions.map((p) => copyPortfolio.normalizePaperPosition(p, resolvers)),
    ...live.positions.map((p) => copyPortfolio.normalizeLivePosition(p, resolvers)),
  ], [paper.positions, live.positions, resolvers]);

  const summary = useMemo(() => copyPortfolio.buildSummary(positions), [positions]);
  const traderBreakdown = useMemo(() => copyPortfolio.buildTraderBreakdown(positions), [positions]);
  const marketBreakdown = useMemo(() => copyPortfolio.buildMarketBreakdown(positions), [positions]);
  const cumulativeRealized = useMemo(() => copyPortfolio.buildCumulativeRealized(positions), [positions]);

  const open = useMemo(() => positions.filter((p) => p.is_open), [positions]);
  const closed = useMemo(() => positions.filter((p) => !p.is_open), [positions]);

  // Total allocated capital across active subscriptions (context for "Allocation" card).
  const subscribedAllocation = useMemo(
    () => subs.subscriptions.filter((s) => s.status === 'active').reduce((a, s) => a + (Number(s.allocation_usd) || 0), 0),
    [subs.subscriptions],
  );

  return {
    positions, open, closed, summary, traderBreakdown, marketBreakdown, cumulativeRealized,
    subscribedAllocation,
    loading: paper.loading || live.loading,
    error: paper.error || live.error,
    refresh: () => { paper.refresh(); live.refresh(); subs.refresh(); },
  };
}

export function useCopyHistory(limit = 100) {
  // /api/copy/orders and /api/copy/audit-logs cap limit at 100 (Query le=100) -> 422 above.
  const safeLimit = Math.min(Math.max(1, limit), 100);
  const ordersHook = useCopyOrders({ limit: safeLimit });
  const paper = useCopyPositions({ status: 'all' });
  const live = useLivePositions('all');
  const audit = useAuditLogs(safeLimit);
  const resolvers = useCopyResolvers();

  const rows = useMemo(
    () => copyPortfolio.buildHistory(ordersHook.orders, paper.positions, live.positions, resolvers),
    [ordersHook.orders, paper.positions, live.positions, resolvers],
  );

  return {
    rows,
    auditLogs: audit.logs,
    loading: ordersHook.loading || paper.loading || live.loading,
    error: ordersHook.error || paper.error || live.error,
    refresh: () => { ordersHook.refresh(); paper.refresh(); live.refresh(); audit.refresh(); },
  };
}
