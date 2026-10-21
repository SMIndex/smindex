/**
 * ONE live store for the signed-in wallet's own Perpl account (2026-10-08).
 *
 * Before this, every screen kept its own copy: the Terminal polled the chain
 * every 5s, Portfolio called /api/account-health once on mount, the Copy
 * dashboard had its own query, Dashboard another, so the same account showed
 * "Positions (1), $35.02" on one page and "0 positions, $43.78" on another at
 * the same moment. Every screen now reads from here.
 *
 * Two feeds, each used for what it is actually authoritative for:
 *
 *  1. CHAIN READ (`/api/leaders/positions/{w}?fresh=1` -> chain_reader
 *     .get_trader_detail, uncached): account id, balance, margin in use,
 *     positions, and the ON-CHAIN resting orders. Run on load, on focus, on
 *     reconnect, after any own action, and on a 5s poll.
 *  2. PERPL TRADING WS (through our proxy): the venue's own frames,
 *     mt:23/24 orders, which are the ONLY source for armed SL/TP trigger
 *     orders (Perpl holds those on its servers, not on-chain, so no chain read
 *     and no backend endpoint can ever see them), mt:21 account (`b`, `lb`),
 *     and mt:25/26/27 fills/positions, which we treat as "something changed,
 *     re-read the chain" rather than decoding undocumented fields.
 *
 * Account value definitions (sources in the report, section 2):
 *   marginInUse  = chain getAccountByAddr[2]            (Perpl: locked balance `lb`)
 *   available    = chain getAccountByAddr[1]            (Perpl: `b` - `lb`)
 *   balance      = available + marginInUse              (Perpl: `b`)
 *   unrealizedPnl= sum of position pnl (incl. funding)  (chain getPosition[0][8])
 *   accountValue = balance + unrealizedPnl
 */
import { MARKETS } from '@/config/constants';
import {
  MARKET_CONFIGS, getOpenOrders, getPositionsSeenAt, getSession, getVenueAccount,
  getVenueFills, getVenuePositions, getVenueStats, subscribeOpenOrders,
  type OpenOrder, type VenueFill, type VenuePosition, type VenueStats,
} from '@/lib/perplTrading';

export interface AccountPosition {
  /** Venue position id (mt:26/27 `pid`), when the venue has told us, this is
   *  what a TP/SL trigger's `lp` is set to. */
  pid?: number;
  /** Where this row came from. 'venue' means the venue stream has reported it
   *  but no chain read has confirmed it yet, so the UI says "confirming". */
  source: 'chain' | 'venue';
  marketId: number;
  symbol: string;
  side: 'long' | 'short';
  size: number;
  entryPrice: number;
  markPrice: number;
  pnl: number;
  deltaPnl: number;
  fundingPnl: number;
  deposit: number;        // collateral locked in this position
  leverage: number;
  notional: number;
  roePct: number;
  liqPrice: number | null;
  liqDistancePct: number | null;
}

export interface AccountState {
  wallet: string | null;
  accountId: number;
  available: number;
  marginInUse: number;
  balance: number;
  unrealizedPnl: number;
  accountValue: number;
  positions: AccountPosition[];
  chainOrders: any[];        // resting on-chain orders (limit orders)
  triggers: OpenOrder[];     // armed/activated SL-TP from the venue (st 8/9)
  venueSession: boolean;     // false => triggers cannot be known, say so in the UI
  venueAccountMismatch: string | null;
  at: number;                // last successful chain read (ms)
  /** Last time ANY feed updated this state, what the "live" dot shows. */
  updatedAt: number;
  /** Last venue frame (mt:19/21/25/26/27) we decoded, 0 = none this session. */
  venueFrameAt: number;
  /** Lifetime totals from the venue's own mt:19 `sts` (null without a session). */
  lifetime: VenueStats | null;
  /** Most recent fills from the venue stream, newest first. */
  recentFills: VenueFill[];
  loading: boolean;
  error: string | null;
  /** The server says this wallet has never opened a Perpl account: not an error. */
  noPerplAccount: boolean;
}

const EMPTY: AccountState = {
  wallet: null, accountId: 0, available: 0, marginInUse: 0, balance: 0,
  unrealizedPnl: 0, accountValue: 0, positions: [], chainOrders: [], triggers: [],
  venueSession: false, venueAccountMismatch: null, at: 0, updatedAt: 0,
  venueFrameAt: 0, lifetime: null, recentFills: [], loading: false, error: null,
  noPerplAccount: false,
};

let state: AccountState = { ...EMPTY };
const subs = new Set<(s: AccountState) => void>();

function emit(): void {
  state = { ...state, updatedAt: Date.now() };
  subs.forEach((fn) => { try { fn(state); } catch {} });
}

export function getAccountState(): AccountState { return state; }

export function subscribeAccount(fn: (s: AccountState) => void): () => void {
  subs.add(fn);
  return () => { subs.delete(fn); };
}

function liqFor(p: any): { liqPrice: number | null; liqDistancePct: number | null } {
  const mcfg = MARKET_CONFIGS[p.market_id];
  // No guessed maintenance margin: without the market's settings we show no
  // liquidation price rather than a wrong one.
  if (!mcfg || mcfg.maintenanceMarginHdths == null || !p.size || !p.deposit) {
    return { liqPrice: null, liqDistancePct: null };
  }
  const mmr = p.notional * (100 / mcfg.maintenanceMarginHdths);
  const liq = p.side === 'long'
    ? p.entry_price - (p.deposit - mmr) / p.size
    : p.entry_price + (p.deposit - mmr) / p.size;
  const liqPrice = Math.max(0, liq);
  const mark = p.mark_price || p.entry_price;
  return {
    liqPrice,
    liqDistancePct: mark > 0 ? Math.abs(liqPrice - mark) / mark * 100 : null,
  };
}

function mapPosition(p: any): AccountPosition {
  const { liqPrice, liqDistancePct } = liqFor(p);
  return {
    source: 'chain',
    marketId: p.market_id, symbol: p.symbol, side: p.side, size: p.size,
    entryPrice: p.entry_price, markPrice: p.mark_price, pnl: p.pnl,
    deltaPnl: p.delta_pnl ?? 0, fundingPnl: p.funding_pnl ?? 0,
    deposit: p.deposit, leverage: p.leverage, notional: p.notional,
    roePct: p.deposit > 0 ? (p.pnl / p.deposit) * 100 : 0,
    liqPrice, liqDistancePct,
  };
}

/** Armed or activated trigger orders (SL/TP) for one position. */
export function triggersFor(s: AccountState, marketId: number, side: 'long' | 'short'): {
  sl: OpenOrder | null; tp: OpenOrder | null; all: OpenOrder[];
} {
  // t:3 closes a long, t:4 closes a short.
  const closeT = side === 'long' ? 3 : 4;
  const all = s.triggers.filter((o) => o.mkt === marketId && o.t === closeT);
  const isSl = (o: OpenOrder) => (o.t === 3 ? (o.tpc === 4 || o.tpc === 2) : (o.tpc === 3 || o.tpc === 1));
  return {
    sl: all.find(isSl) ?? null,
    tp: all.find((o) => !isSl(o)) ?? null,
    all,
  };
}

/** Trigger price in human units, or null when the market settings are missing. */
export function triggerPrice(o: OpenOrder): number | null {
  const mcfg = MARKET_CONFIGS[o.mkt];
  if (!mcfg) return null;
  const raw = o.tp ?? o.p;
  return raw == null ? null : raw / 10 ** mcfg.priceDecimals;
}

/** Trigger size in human units, or null when it is genuinely unknown. */
export function triggerSize(o: OpenOrder): number | null {
  const mcfg = MARKET_CONFIGS[o.mkt];
  if (!mcfg || o.s == null || !Number.isFinite(o.s)) return null;
  return o.s / 10 ** mcfg.sizeDecimals;
}

// ---------------------------------------------------------------------------
// Venue-fed positions (2026-10-09)
//
// The venue PAINTS, the chain ARBITRATES.
//
// A position frame (mt:26/27) or a fill (mt:25) updates the screen the instant
// it lands, marked source:'venue' so the UI can show "confirming". The next
// chain read replaces that row with the chain's own numbers (mark price,
// unrealized P/L, liquidation price: none of which a position frame carries)
// and keeps the venue's `pid` so a TP/SL can be linked with `lp`.
//
// This is why the old behaviour felt slow: every venue frame only meant "go
// and poll the chain", so a fill took up to a whole poll interval to show.
// ---------------------------------------------------------------------------

/** Rows the venue has reported that no chain read has confirmed yet. */
const pending = new Map<number, AccountPosition>();   // keyed by pid

function symbolFor(marketId: number): string {
  return MARKETS[marketId]?.symbol ?? `M${marketId}`;
}

function fromVenuePosition(v: VenuePosition): AccountPosition {
  const notional = v.size * v.entryPrice;
  const { liqPrice, liqDistancePct } = liqFor({
    market_id: v.mkt, side: v.side, size: v.size, entry_price: v.entryPrice,
    deposit: v.collateral, notional, mark_price: v.entryPrice,
  });
  return {
    pid: v.pid,
    source: 'venue',
    marketId: v.mkt,
    symbol: symbolFor(v.mkt),
    side: v.side,
    size: v.size,
    entryPrice: v.entryPrice,
    markPrice: v.entryPrice,      // the frame carries no mark; the chain read fills it in
    pnl: 0,
    deltaPnl: 0,
    fundingPnl: v.fundingPnl,
    deposit: v.collateral,
    leverage: v.leverage,
    notional,
    roePct: 0,
    liqPrice,
    liqDistancePct,
  };
}

/**
 * A fill on a market we hold no position in means a position just opened.
 * Synthesised from the fill itself (`t` 1/2 = open long/short) so the Terminal
 * can show it immediately; `pid` is unknown until the position frame arrives.
 */
function fromVenueFill(f: VenueFill): AccountPosition | null {
  if (f.t !== 1 && f.t !== 2) return null;        // 3/4 are closes
  const side: 'long' | 'short' = f.t === 1 ? 'long' : 'short';
  return {
    source: 'venue',
    marketId: f.mkt,
    symbol: symbolFor(f.mkt),
    side,
    size: f.size,
    entryPrice: f.price,
    markPrice: f.price,
    pnl: 0, deltaPnl: 0, fundingPnl: 0,
    deposit: 0, leverage: 0,
    notional: f.size * f.price,
    roePct: 0, liqPrice: null, liqDistancePct: null,
  };
}

/** Chain rows win; venue rows the chain has not caught up with are appended. */
function mergePositions(chain: AccountPosition[]): AccountPosition[] {
  if (!pending.size) return chain;
  const held = new Set(chain.map((p) => `${p.marketId}:${p.side}`));
  const extra = Array.from(pending.values()).filter((p) => !held.has(`${p.marketId}:${p.side}`));
  // Attach the venue's position id to the chain rows that do match, so `lp`
  // works even once the chain read has taken over the numbers.
  const withPid = chain.map((p) => {
    const v = Array.from(pending.values()).find((q) => q.marketId === p.marketId && q.side === p.side);
    return v?.pid != null && p.pid == null ? { ...p, pid: v.pid } : p;
  });
  return [...withPid, ...extra];
}

/** Apply a decoded venue frame straight to the screen. */
function applyVenuePositions(rows: VenuePosition[], frameAt: number): void {
  pending.clear();
  for (const v of rows) pending.set(v.pid, fromVenuePosition(v));
  // The venue's set is authoritative about WHICH positions exist, so a chain
  // row the venue no longer reports is dropped at once (a 100% close vanishes
  // immediately instead of lingering for a poll). If the next chain read still
  // shows it, it comes straight back, that is the cross-check working.
  const live = new Set(rows.map((v) => `${v.mkt}:${v.side}`));
  const keptChain = state.positions.filter((p) => p.source === 'chain' && live.has(`${p.marketId}:${p.side}`));
  const positions = mergePositions(keptChain);
  state = {
    ...state,
    positions,
    unrealizedPnl: positions.reduce((a, p) => a + (p.pnl || 0), 0),
    accountValue: state.balance + positions.reduce((a, p) => a + (p.pnl || 0), 0),
    venueFrameAt: frameAt || Date.now(),
  };
  emit();
  console.log('[venue-sync] paint', Date.now(), `frame:${frameAt}`,
    `delay:${frameAt ? Date.now() - frameAt : '?'}ms`, `positions:${positions.length}`);
}

/** Apply fills: show a brand-new position before any read confirms it. */
function applyVenueFills(fills: VenueFill[], frameAt: number): void {
  let added = 0;
  for (const f of fills) {
    const already = state.positions.some((p) => p.marketId === f.mkt)
      || Array.from(pending.values()).some((p) => p.marketId === f.mkt);
    if (already) continue;
    const p = fromVenueFill(f);
    if (!p) continue;
    pending.set(-f.oid, p);     // negative key: not a real pid, replaced by mt:26/27
    added += 1;
  }
  state = {
    ...state,
    recentFills: getVenueFills(),
    positions: added ? mergePositions(state.positions.filter((p) => p.source === 'chain')) : state.positions,
    venueFrameAt: frameAt || Date.now(),
  };
  emit();
  if (added) {
    console.log('[venue-sync] paint-from-fill', Date.now(), `frame:${frameAt}`,
      `delay:${frameAt ? Date.now() - frameAt : '?'}ms`);
  }
}

// ---------------------------------------------------------------------------
// Chain read: single-flight, with the venue cross-check
// ---------------------------------------------------------------------------
let inFlight: Promise<void> | null = null;
let pollTimer: number | null = null;
let confirmTimer: number | null = null;
let burstTimers: number[] = [];

async function readChain(): Promise<void> {
  const wallet = state.wallet;
  if (!wallet) return;
  const resp = await fetch(`/api/leaders/positions/${wallet}?fresh=1`);
  if (resp.status === 404) {
    // 404 here means only "no Perpl account for this wallet" (leaders.py): an
    // empty account to show calmly, not a failed read.
    state = { ...state, noPerplAccount: true, positions: [], chainOrders: [], at: Date.now(), loading: false, error: null };
    return;
  }
  if (!resp.ok) throw new Error(`account read failed (${resp.status})`);
  const d = await resp.json();
  const positions = (d.positions ?? []).map(mapPosition);
  const available = Number(d.balance ?? 0);
  const marginInUse = Number(d.margin_used ?? 0);
  const unrealizedPnl = positions.reduce((a: number, p: AccountPosition) => a + (p.pnl || 0), 0);

  // Cross-check against Perpl's own account frame when we have one. Never
  // silently prefer one over the other, a disagreement is reported.
  let mismatch: string | null = null;
  const va = getVenueAccount();
  if (va && va.b != null && va.lb != null) {
    const venueAvailable = va.b - va.lb;
    if (Math.abs(venueAvailable - available) > 0.01 || Math.abs(va.lb - marginInUse) > 0.01) {
      mismatch = `chain available ${available.toFixed(2)} / margin ${marginInUse.toFixed(2)} vs venue b-lb ${venueAvailable.toFixed(2)} / lb ${va.lb.toFixed(2)}`;
      console.warn('[account] chain and venue disagree:', mismatch);
    }
  }

  // The chain has now confirmed these; drop the matching pending venue rows so
  // the "confirming" marker clears and nothing is counted twice.
  const confirmed = new Set(positions.map((p: AccountPosition) => `${p.marketId}:${p.side}`));
  for (const [pid, p] of Array.from(pending.entries())) {
    if (confirmed.has(`${p.marketId}:${p.side}`)) {
      const keepPid = pid > 0 ? pid : undefined;
      if (keepPid != null) pending.set(pid, { ...p, pid: keepPid });
      else pending.delete(pid);
    }
  }
  const merged = mergePositions(positions);
  const mergedUnrealized = merged.reduce((a, p) => a + (p.pnl || 0), 0);

  state = {
    ...state,
    accountId: d.account_id ?? state.accountId,
    available,
    marginInUse,
    balance: available + marginInUse,
    unrealizedPnl: merged.length === positions.length ? unrealizedPnl : mergedUnrealized,
    accountValue: available + marginInUse + (merged.length === positions.length ? unrealizedPnl : mergedUnrealized),
    positions: merged,
    chainOrders: d.orders ?? [],
    venueAccountMismatch: mismatch,
    at: Date.now(),
    loading: false,
    error: null,
    noPerplAccount: false,
  };
}

/** Read the account now. Concurrent callers share one request. */
export function refreshAccount(reason = 'manual'): Promise<void> {
  if (!state.wallet) return Promise.resolve();
  if (inFlight) return inFlight;
  if (!state.at) { state = { ...state, loading: true }; emit(); }
  inFlight = readChain()
    .then(() => { emit(); })
    .catch((e: any) => {
      // Keep the last good numbers on screen and say the read failed, rather
      // than flashing zeros (a failed RPC is not an empty account).
      state = { ...state, loading: false, error: e?.message || 'account read failed' };
      console.warn(`[account] refresh(${reason}) failed:`, e?.message);
      emit();
    })
    .finally(() => { inFlight = null; });
  return inFlight;
}

/**
 * The read pattern after our own action: read now, then again as the chain
 * catches up. The venue frame has normally already painted by the time the
 * first read lands: these reads exist to replace the venue's partial row with
 * the chain's full one (mark price, unrealized P/L, liquidation price).
 */
export function refreshBurst(reason: string): void {
  refreshAccount(reason);
  burstTimers.forEach((t) => window.clearTimeout(t));
  burstTimers = [800, 2500, 6000].map((ms) =>
    window.setTimeout(() => refreshAccount(`${reason}+${ms}ms`), ms));
}

function syncVenueParts(): void {
  const sess = getSession();
  const triggers = getOpenOrders().filter((o) => o.st === 8 || o.st === 9);
  const lifetime = getVenueStats();
  const same = triggers.length === state.triggers.length
    && triggers.every((o, i) => state.triggers[i]?.oid === o.oid && state.triggers[i]?.st === o.st
      && state.triggers[i]?.s === o.s && state.triggers[i]?.tp === o.tp);
  const venueSession = sess.authenticated && sess.hasWs;
  if (same && state.venueSession === venueSession && state.lifetime === lifetime) return;
  state = { ...state, triggers, venueSession, lifetime };
  emit();
}

/** Seconds since the newest of our two feeds said anything. */
export function accountAgeMs(s: AccountState): number {
  const newest = Math.max(s.at, s.venueFrameAt);
  return newest ? Date.now() - newest : 0;
}

/** True once we have ever had data, the only time a screen may show a skeleton. */
export function hasAccountData(s: AccountState): boolean {
  return s.at > 0 || s.venueFrameAt > 0;
}

let started = false;
let detach: Array<() => void> = [];

/** Point the store at a wallet (idempotent). Call it from any screen. */
export function initAccountStore(wallet?: string | null): void {
  const w = (wallet || '').toLowerCase() || null;
  // A null wallet means "not known yet", NOT "signed out".
  //
  // Screens call this hook before auth has hydrated, so a component mounting
  // with `undefined` used to wipe a populated store back to EMPTY, every
  // other screen then flashed "Reading your account from Perpl..." and the
  // whole app looked like it was reloading. Only a DIFFERENT, real wallet
  // resets the store.
  if (w && w !== state.wallet) {
    state = { ...EMPTY, wallet: w };
    emit();
    refreshAccount('wallet-change');
  } else if (w && !state.at && !inFlight) {
    refreshAccount('init');
  }
  if (started) return;
  started = true;

  detach.push(subscribeOpenOrders(syncVenueParts));

  // A venue frame is applied straight away. The chain read that follows is a
  // CHECK, not the source: one read shortly after, not a poll storm.
  const onVenue = (e: Event) => {
    syncVenueParts();
    const d: any = (e as CustomEvent)?.detail?.detail;
    const frameAt: number = d?.frameAt ?? (e as CustomEvent)?.detail?.at ?? Date.now();
    if (Array.isArray(d?.positions)) applyVenuePositions(d.positions, frameAt);
    else if (Array.isArray(d?.fills)) applyVenueFills(d.fills, frameAt);
    else if (d?.account) {
      const va = d.account;
      if (va.b != null && va.lb != null) {
        const positions = state.positions;
        const unrealized = positions.reduce((a, p) => a + (p.pnl || 0), 0);
        state = {
          ...state,
          available: va.b - va.lb,
          marginInUse: va.lb,
          balance: va.b,
          accountValue: va.b + unrealized,
          venueFrameAt: frameAt,
        };
        emit();
      }
    }
    // One confirming read per burst, not one per frame.
    if (confirmTimer) window.clearTimeout(confirmTimer);
    confirmTimer = window.setTimeout(() => refreshAccount('venue-confirm'), 1200);
  };
  const onOwnAction = () => refreshBurst('own-action');
  const onVisible = () => { if (document.visibilityState === 'visible') refreshAccount('focus'); };

  // Liquidation prices need the market's maintenance margin, so a read that
  // beat the config fetch must be redone once the configs land.
  const onConfigs = () => { if (state.at) refreshAccount('market-configs-loaded'); };
  window.addEventListener('market_configs_loaded', onConfigs);
  window.addEventListener('venue_account_event', onVenue);
  window.addEventListener('trigger_leg_filled', onVenue);
  window.addEventListener('order_placed', onOwnAction);
  window.addEventListener('position_closed', onOwnAction);
  document.addEventListener('visibilitychange', onVisible);
  detach.push(() => {
    window.removeEventListener('market_configs_loaded', onConfigs);
    window.removeEventListener('venue_account_event', onVenue);
    window.removeEventListener('trigger_leg_filled', onVenue);
    window.removeEventListener('order_placed', onOwnAction);
    window.removeEventListener('position_closed', onOwnAction);
    document.removeEventListener('visibilitychange', onVisible);
  });

  // 30s background check, paused while the tab is hidden.
  //
  // It used to be 5s, because the chain read was the only way to learn that
  // anything had happened. Now the venue stream reports every fill, position
  // change and balance change as it happens, so this poll is only a safety net
  // for the case where no venue session exists (or a frame was missed), and a
  // read every 5s was itself part of what made the app feel like it was
  // constantly reloading.
  pollTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') refreshAccount('poll');
  }, 30000);
  syncVenueParts();
  if (getPositionsSeenAt()) applyVenuePositions(getVenuePositions(), getPositionsSeenAt());
}

export function stopAccountStore(): void {
  if (pollTimer) window.clearInterval(pollTimer);
  pollTimer = null;
  if (confirmTimer) window.clearTimeout(confirmTimer);
  confirmTimer = null;
  burstTimers.forEach((t) => window.clearTimeout(t));
  burstTimers = [];
  detach.forEach((fn) => fn());
  detach = [];
  started = false;
}
