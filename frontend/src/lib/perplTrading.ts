import api from '@/lib/api';
import { getStoredApiKey, buildWsSignInFrame } from '@/lib/perplApiKey';

// Trading WS goes through our backend proxy to avoid CORS
const PERPL_WS = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/trading`;
const CHAIN_ID = 143;

// Our app JWT (same source the axios interceptor uses). Sent to the trading proxy so
// the backend loads Perpl cookies for OUR authenticated wallet only.
function getStoredAuthToken(): string {
  try {
    return JSON.parse(localStorage.getItem('perpl-auth') || '{}')?.state?.token || '';
  } catch {
    return '';
  }
}

interface PerplSession {
  nonce: string;
  ws: WebSocket | null;
  accountId: number;
  authenticated: boolean;
  lastBlock: number;
}

let session: PerplSession = {
  nonce: '',
  ws: null,
  accountId: 0,
  authenticated: false,
  lastBlock: 0,
};

// Timestamp of the last mt:100 heartbeat, lets getFreshBlock skip the
// up-to-one-block wait when we saw a heartbeat moments ago.
let lastBlockAt = 0;

/**
 * Get a fresh block number. Uses the last heartbeat block if it arrived
 * within the last 1.2s (blocks stale in <3s, order lb adds ~6 blocks TTL);
 * otherwise waits for the next heartbeat.
 */
function waitForBlock(timeoutMs: number): Promise<number> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      session.ws?.removeEventListener('message', handler);
      reject(new Error('Timeout waiting for block'));
    }, timeoutMs);

    const handler = (e: MessageEvent) => {
      try {
        const m = JSON.parse(e.data);
        if (m.mt === 100 && m.sn) {
          clearTimeout(timeout);
          session.ws?.removeEventListener('message', handler);
          session.lastBlock = m.sn;
          lastBlockAt = Date.now();
          resolve(m.sn);
        }
      } catch {}
    };
    session.ws!.addEventListener('message', handler);
  });
}

async function getFreshBlock(): Promise<number> {
  if (!session.ws || session.ws.readyState !== WebSocket.OPEN) {
    throw new Error('Trading WS not connected');
  }
  if (session.lastBlock && Date.now() - lastBlockAt < 1200) {
    return session.lastBlock;
  }
  try {
    // Heartbeats arrive about 3 a second; 2 s without one is a dead socket
    // (was 5 s of silent waiting).
    return await waitForBlock(2000);
  } catch {
    // No heartbeat means the socket is half-dead (Perpl went idle server-side
    // without closing TCP). Tear it down, re-auth once, wait for the first
    // heartbeat of the new session, instead of failing the order click.
    try { window.dispatchEvent(new CustomEvent('perpl_reconnecting')); } catch {}
    try { session.ws?.close(); } catch {}
    session.ws = null;
    session.authenticated = false;
    const ok = await tryAutoReconnect();
    if (!ok || !session.ws) throw new Error('Timeout waiting for block');
    return await waitForBlock(5000);
  }
};

let requestId = Date.now();

// Per Perpl docs, rq must be strictly increasing per account and > the
// server's last-forwarded request (lfr): otherwise error 32. Bump our
// counter whenever the server tells us its lfr.
function bumpRequestIdFrom(msg: any): void {
  const lfr = msg?.lfr ?? msg?.d?.lfr ?? msg?.d?.acc?.lfr;
  if (typeof lfr === 'number' && lfr >= requestId) requestId = lfr;
  // The account snapshot (mt:19) carries it at as[].lfr and nowhere else.
  // Auto-copy numbers its orders in microseconds, so on an account it has
  // traded the venue's lfr is ~1000x Date.now() and every order here failed
  // sr:32 until this was read (2026-10-12).
  if (Array.isArray(msg?.as)) {
    for (const a of msg.as) {
      const v = Number(a?.lfr);
      if (Number.isSafeInteger(v) && v >= requestId) requestId = v;
    }
  }
}

// ---------------------------------------------------------------------------
// Open-orders store: fed by mt:23 (snapshot) / mt:24 (updates) on the
// trading WS. Terminal statuses drop the row; resting orders stay.
// ---------------------------------------------------------------------------

export interface OpenOrder {
  oid: number;
  mkt: number;
  t: number;       // 1 OpenLong / 2 OpenShort / 3 CloseLong / 4 CloseShort
  p: number;       // scaled price
  /**
   * Scaled order size.
   *
   * CORRECTION (2026-10-09): the venue's Order object calls this **`os`**, not
   * `s` - `s` is the Fill object's size field. Captured from Perpl's own
   * order-history for account 161: every row carries `os` and no row carries
   * `s`, e.g. the ETH trigger {"oid":288788852637722,"st":8,"os":7,"tp":254200}
   * (os 7 / 10^3 = 0.007 ETH). Reading `s` is why the Orders tab printed
   * "size-": the field was always absent, so the number was NaN from the very
   * first frame, not only after a merge. `os` is normalised into `s` on ingest
   * so every existing caller keeps working.
   */
  s: number;
  os?: number;     // the venue's own name for the size above
  fs?: number;     // filled size
  st?: number;     // status (8 = Untriggered: armed SL/TP trigger)
  lv?: number;
  rq?: number;
  tp?: number;     // trigger price (scaled) for trigger orders
  tpc?: number;    // trigger condition (1 GTELast/2 LTELast/3 GTEMark/4 LTEMark)
  lp?: number;     // linked position id: venue cancels the leg when it closes
  // true while the only thing we know about this order is what WE submitted
  // (the venue acked the oid but has not sent a frame carrying its fields yet)
  _local?: boolean;
}

const ST_TERMINAL = new Set([4, 5, 6, 7, 10]); // Filled/Canceled/Expired/Failed/Executed

const _openOrders = new Map<number, OpenOrder>();
const _orderSubs = new Set<() => void>();
let _ordersSnapshot: OpenOrder[] = [];

function notifyOrderSubs() {
  _ordersSnapshot = Array.from(_openOrders.values());
  console.log('[order-timing] notify', Date.now());
  _orderSubs.forEach((fn) => { try { fn(); } catch {} });
}

function ingestOrderRows(rows: any[]): void {
  let changed = false;
  for (const o of rows) {
    if (o?.oid == null) continue;
    // The venue's Order rows carry the size as `os`; normalise it to `s` so
    // every existing reader (triggerSize, the Orders tab, the oversized-leg
    // check) sees a number instead of undefined. See OpenOrder.s above.
    if (o.os != null && o.s == null) o.s = o.os;
    // Diagnostic capture (2026-09-28): armed triggers and fills logged raw so
    // the exact venue field names are on record (size-NaN investigation).
    if (o.st === 8 || o.st === 4) {
      try { console.log('[order-row-capture]', JSON.stringify(o)); } catch {}
    }
    const prev = _openOrders.get(o.oid);
    if (o.st != null && ST_TERMINAL.has(o.st)) {
      _openOrders.delete(o.oid);
      // A fill/cancel/execute is order activity even when the order never
      // rested in the map: a market order fills without ever being stored,
      // so the old delete-miss silently swallowed the venue's fill frame and
      // the e9fe4b4 push-refetch never fired for market orders.
      changed = true;
      // OCO: an armed (8) or activated (9) TP/SL leg that fills (4) or
      // executes (10) may leave a sibling leg behind, announce it so the
      // panel can cancel the sibling once the position reads zero.
      if (prev && (prev.st === 8 || prev.st === 9) && (o.st === 4 || o.st === 10)) {
        try {
          console.log('[order-timing] trigger-leg-filled', Date.now(), `oid:${o.oid} mkt:${o.mkt ?? prev.mkt}`);
          window.dispatchEvent(new CustomEvent('trigger_leg_filled', {
            detail: { oid: o.oid, mkt: o.mkt ?? prev.mkt },
          }));
        } catch {}
      }
    } else if (o.r) {
      _openOrders.delete(o.oid);
      changed = true;
    } else {
      // MERGE, never replace: the venue's later frames for the same oid carry
      // only what changed (an st:8 "armed" update has no `s`), so overwriting
      // the row dropped the size we already knew and the Orders tab rendered
      // "size-" (NaN). Keep every field we have ever been told.
      _openOrders.set(o.oid, { ...(prev ?? {}), ...o, _local: false } as OpenOrder);
      changed = true;
    }
  }
  if (changed) notifyOrderSubs();
}

// ---------------------------------------------------------------------------
// Venue account frame (mt:21 AccountUpdate / mt:19 WalletSnapshot).
// Perpl's own numbers, with Perpl's own names: `b` balance, `lb` locked
// balance, `ft` fee tier, `lfr` last forwarded request id. We keep them raw,
// the account screens derive available = b - lb from these (docs: api/websocket).
// ---------------------------------------------------------------------------
export interface VenueAccount {
  id?: number; b?: number; lb?: number; ft?: number; fr?: boolean; fw?: boolean;
  lfr?: number;
  at: number;              // when we received it
}

/**
 * Lifetime account stats, straight from the venue (mt:19 `sts[]`).
 *
 * Captured 2026-10-09 from Perpl for account 161 and cross-checked three ways
 * (audits/SYNC_UI_REPORT.md section 1):
 *   b   58832054        /1e6 = 58.83          == the chain read's balance, to the cent
 *   tv  3585346172933   /1e6 = 3,585,346.17   == the leaderboard's "$3.58M volume"
 *   tf  414473571       /1e6 = 414.47
 * so every money field on this socket is micro-USD (1e6), the same scale the
 * REST fee field uses (a MON fill's f/1e6 is exactly notional * 3.45 bps, the
 * market's taker fee).
 */
export interface VenueStats {
  accountId: number;
  deposited: number;       // td
  withdrawn: number;       // tw
  volume: number;          // tv
  fees: number;            // tf
  builderFees: number;     // tbf
  realizedPnl: number;     // trp
  winRatePct: number;      // wr, hundredths of a percent (1635 = 16.35%)
  trades: number;          // tt
  at: number;
}

/** An open position as the venue itself reports it (mt:26 snapshot / mt:27 update). */
export interface VenuePosition {
  pid: number;             // position id: what a trigger order's `lp` points at
  mkt: number;
  side: 'long' | 'short';  // sd: 1 Long, 2 Short  (NOTE: the CHAIN uses 0/1)
  st?: number;             // position status (2 = closed in every observed row)
  size: number;            // human units
  entryPrice: number;      // human units
  collateral: number;      // USD
  leverage: number;        // lv hundredths -> x
  deltaPnl: number;        // dpnl, USD
  fundingPnl: number;      // fnd, USD
  fee: number;             // fee + cfee, USD
  at: number;              // venue block time (ms)
  seen: number;            // when we received it
}

/** One executed fill (mt:25, the same object REST /v1/trading/fills returns). */
export interface VenueFill {
  at: number;              // block time (ms)
  mkt: number;
  oid: number;
  t: number;               // 1 OpenLong 2 OpenShort 3 CloseLong 4 CloseShort
  maker: boolean;          // l: 1 maker, 2 taker
  price: number;           // human units
  size: number;            // human units
  fee: number;             // USD (negative = rebate)
  builderFee: number;      // USD
  seen: number;
}

const MONEY = 1e6;          // micro-USD, proven above
const num = (v: any): number => (typeof v === 'string' ? Number(v) : v) || 0;
const money = (v: any): number => num(v) / MONEY;

let _venueAccount: VenueAccount | null = null;
let _venueStats: VenueStats | null = null;
const _venuePositions = new Map<number, VenuePosition>();
let _venueFills: VenueFill[] = [];
let _positionsSeenAt = 0;

export function getVenueAccount(): VenueAccount | null { return _venueAccount; }
export function getVenueStats(): VenueStats | null { return _venueStats; }
/** Open positions per the venue stream, newest frame wins. */
export function getVenuePositions(): VenuePosition[] { return Array.from(_venuePositions.values()); }
/** When the venue last told us the full position set (0 = never this session). */
export function getPositionsSeenAt(): number { return _positionsSeenAt; }
/** Most recent fills seen on this socket, newest first (capped). */
export function getVenueFills(): VenueFill[] { return _venueFills; }

/** The position id for an open position, or null, this is what `lp` needs. */
export function venuePositionId(marketId: number, side: 'long' | 'short'): number | null {
  for (const p of _venuePositions.values()) {
    if (p.mkt === marketId && p.side === side) return p.pid;
  }
  return null;
}

function scalePrice(mkt: number, raw: any): number {
  const c = MARKET_CONFIGS[mkt];
  return c ? num(raw) / 10 ** c.priceDecimals : num(raw);
}
function scaleSize(mkt: number, raw: any): number {
  const c = MARKET_CONFIGS[mkt];
  return c ? num(raw) / 10 ** c.sizeDecimals : num(raw);
}

/** mt:26/27 row -> VenuePosition. Null when the row is not a position. */
function mapVenuePosition(r: any): VenuePosition | null {
  if (r?.pid == null || r?.mkt == null) return null;
  return {
    pid: Number(r.pid),
    mkt: Number(r.mkt),
    side: Number(r.sd) === 2 ? 'short' : 'long',
    st: r.st,
    size: scaleSize(r.mkt, r.s),
    entryPrice: scalePrice(r.mkt, r.ep),
    collateral: Math.abs(money(r.c)),
    leverage: num(r.lv) / 100,
    deltaPnl: money(r.dpnl),
    fundingPnl: money(r.fnd),
    fee: money(r.fee) + money(r.cfee),
    at: r?.at?.t ?? Date.now(),
    seen: Date.now(),
  };
}

/** A row that means "this position is no longer open". */
const POSITION_CLOSED = (r: any): boolean => r?.r === true || Number(r?.st) === 2;

function ingestVenuePositions(rows: any[], snapshot: boolean): boolean {
  if (snapshot) _venuePositions.clear();
  let changed = snapshot;
  for (const r of rows ?? []) {
    const p = mapVenuePosition(r);
    if (!p) continue;
    if (POSITION_CLOSED(r) || p.size === 0) {
      changed = _venuePositions.delete(p.pid) || changed;
    } else {
      _venuePositions.set(p.pid, p);
      changed = true;
    }
  }
  _positionsSeenAt = Date.now();
  return changed;
}

function ingestVenueFills(rows: any[]): VenueFill[] {
  const added: VenueFill[] = [];
  for (const r of rows ?? []) {
    if (r?.mkt == null) continue;
    added.push({
      at: r?.at?.t ?? Date.now(),
      mkt: Number(r.mkt),
      oid: Number(r.oid ?? 0),
      t: Number(r.t ?? 0),
      maker: Number(r.l) === 1,
      price: scalePrice(r.mkt, r.p),
      size: scaleSize(r.mkt, r.s),
      fee: money(r.f),
      builderFee: money(r.bfa),
      seen: Date.now(),
    });
  }
  if (added.length) _venueFills = [...added.slice().reverse(), ..._venueFills].slice(0, 200);
  return added;
}

/**
 * mt:19 WalletSnapshot / mt:21 AccountUpdate.
 *
 * CORRECTION (2026-10-09): mt:19 puts the accounts in `as[]` and the lifetime
 * stats in `sts[]`. It carries no `acc` and no `d`, so the previous reader
 * (`msg.d?.acc ?? msg.acc`) never matched and the venue account was never
 * populated from a snapshot at all. The real frame, captured from Perpl:
 *   {"mt":19,"addr":"0x8b66...","as":[{"in":1,"id":161,"b":"58832054","lb":"0",
 *    "ft":0,"fr":false,"fw":true,"lfr":1791494798811}],
 *    "sts":[{"id":161,"td":"1426684006","tw":"467560000","tv":"3585346172933",
 *    "tf":"414473571","tbf":"106568","trp":"-895690918","wr":1635,"tt":8741}]}
 */
function ingestAccountFrame(msg: any): boolean {
  const accounts: any[] = Array.isArray(msg?.as) ? msg.as
    : Array.isArray(msg?.d) ? msg.d
      : [msg?.d ?? msg].filter((a) => a && (a.b != null || a.lb != null || a.id != null));
  const pick = accounts.find((a) => a && (session.accountId ? Number(a.id) === session.accountId : true))
    ?? accounts[0];
  let changed = false;
  if (pick && (pick.b != null || pick.lb != null || pick.id != null)) {
    _venueAccount = {
      id: pick.id != null ? Number(pick.id) : undefined,
      b: pick.b != null ? money(pick.b) : undefined,
      lb: pick.lb != null ? money(pick.lb) : undefined,
      ft: pick.ft,
      fr: pick.fr,
      fw: pick.fw,
      lfr: pick.lfr != null ? Number(pick.lfr) : undefined,
      at: Date.now(),
    };
    changed = true;
  }
  const stats: any[] = Array.isArray(msg?.sts) ? msg.sts : [];
  const st = stats.find((a) => (session.accountId ? Number(a.id) === session.accountId : true)) ?? stats[0];
  if (st) {
    _venueStats = {
      accountId: Number(st.id ?? 0),
      deposited: money(st.td),
      withdrawn: money(st.tw),
      volume: money(st.tv),
      fees: money(st.tf),
      builderFees: money(st.tbf),
      realizedPnl: money(st.trp),
      winRatePct: num(st.wr) / 100,
      trades: num(st.tt),
      at: Date.now(),
    };
    changed = true;
  }
  return changed;
}

/**
 * The venue told us something about our own account.
 *
 * `venue_account_event` now carries the DECODED payload, so the account store
 * can paint the change immediately and treat the next chain read as a check
 * rather than as the source. Before this, every frame only meant "go and poll
 * the chain", which is what made a fill take a whole poll interval to appear.
 */
function signalVenueChange(why: string, detail?: unknown): void {
  try {
    window.dispatchEvent(new CustomEvent('venue_account_event', { detail: { why, detail, at: Date.now() } }));
  } catch {}
}

function trackSessionMessage(e: MessageEvent): void {
  try {
    const msg = JSON.parse(e.data);
    if (msg.mt === 100) {
      if (msg.sn) { session.lastBlock = msg.sn; lastBlockAt = Date.now(); }
      return;
    }
    bumpRequestIdFrom(msg);
    if ((msg.mt === 23 || msg.mt === 24) && Array.isArray(msg.d)) {
      console.log('[order-timing] ws-frame', Date.now(),
        `mt:${msg.mt}`, msg.d.map((r: any) => `oid:${r?.oid} st:${r?.st}`).join(' '));
      if (msg.mt === 23) _openOrders.clear();
      ingestOrderRows(msg.d);
    }
    // Account value + lifetime stats: Perpl's own numbers (mt:19 / mt:21).
    if (msg.mt === 21 || msg.mt === 19) {
      if (ingestAccountFrame(msg)) {
        signalVenueChange(`account mt:${msg.mt}`, { account: _venueAccount, stats: _venueStats });
      }
    }
    // Positions (mt:26 snapshot / mt:27 update): decoded and published, so a
    // position appears the moment the frame lands. The chain read stays the
    // arbiter: accountStore cross-checks it and flags any disagreement.
    if (msg.mt === 26 || msg.mt === 27) {
      const t0 = Date.now();
      const changed = ingestVenuePositions(msg.d, msg.mt === 26);
      console.log('[venue-sync] positions', t0, `mt:${msg.mt}`,
        `rows:${Array.isArray(msg.d) ? msg.d.length : 0}`, `open:${_venuePositions.size}`);
      if (changed || msg.mt === 26) {
        signalVenueChange(`mt:${msg.mt}`, { positions: getVenuePositions(), frameAt: t0 });
      }
    }
    // Fills (mt:25): the executed trade itself. Published immediately so the
    // Terminal shows the position as "confirming" without waiting for a read.
    if (msg.mt === 25) {
      const t0 = Date.now();
      const added = ingestVenueFills(msg.d);
      console.log('[venue-sync] fills', t0, `rows:${added.length}`,
        added.map((f) => `mkt:${f.mkt} t:${f.t} ${f.size}@${f.price}`).join(' '));
      if (added.length) signalVenueChange('mt:25', { fills: added, frameAt: t0 });
    }
  } catch {}
}

export function getOpenOrders(): OpenOrder[] {
  return _ordersSnapshot;
}

export function subscribeOpenOrders(fn: () => void): () => void {
  _orderSubs.add(fn);
  return () => { _orderSubs.delete(fn); };
}

// Official Perpl reject reasons (sr), human-readable. Spec: docs.perpl.xyz.
const SR_REASONS: Record<number, string> = {
  1: 'Amount exceeds available balance.',
  13: 'Limit price would fill immediately. Set price further from market.',
  14: 'Order expired before execution (block deadline passed). Try again.',
  15: 'Order forwarding reverted on-chain. Try again.',
  20: 'Invalid expiry block (lb outside allowed window). Try again.',
  32: 'Order id too low (stale session). Reconnect and retry.',
  33: 'Perpl says this order no longer exists.',
  45: 'Perpl could not cancel this order.',
  38: 'Order size exceeds available size.',
  44: 'No liquidity at this price.',
  53: 'Market temporarily unavailable (perpetual insolvent).',
};
function srReason(sr: number | undefined): string {
  return (sr != null && SR_REASONS[sr]) || `Order rejected (reason: ${sr})`;
}

// Official Perpl order statuses (st). Terminal rejects: Canceled/Expired/Failed.
const ST_REJECTED = new Set([5, 6, 7]);
const ST_FILLED = new Set([4, 10]); // Filled, Executed

// Classify an mt:24 order row for our rq. Returns a resolution/rejection intent
// using spec `st` first, falling back to the legacy fs/r fields.
function classifyOrder(o: any): { kind: 'filled' | 'rejected' | 'placed'; reason?: string } {
  const st = o?.st;
  if ((st != null && ST_FILLED.has(st)) || o?.fs > 0) return { kind: 'filled' };
  if ((st != null && ST_REJECTED.has(st)) || o?.r) return { kind: 'rejected', reason: srReason(o?.sr) };
  return { kind: 'placed' };
}

/**
 * Step 1: Get SIWE payload from Perpl (proxied through our backend)
 */
export async function getPerplPayload(address: string) {
  const resp = await api.post('/api/auth/perpl-payload', { address });
  return resp.data;
}

/**
 * Step 2: Submit signature to Perpl (proxied through our backend)
 */
export async function connectPerpl(
  address: string,
  payload: { message: string; nonce: string; issued_at: string; mac: string },
  signature: string,
): Promise<string> {
  const resp = await api.post('/api/auth/perpl-connect', {
    chain_id: CHAIN_ID,
    address,
    message: payload.message,
    nonce: payload.nonce,
    issued_at: payload.issued_at,
    mac: payload.mac,
    signature,
  });
  session.nonce = resp.data.nonce;

  // Store for one-click trading (reuse without wallet popup)
  try {
    localStorage.setItem('perpl_nonce', resp.data.nonce);
    localStorage.setItem('perpl_address', address);
  } catch {}

  return resp.data.nonce;
}

/**
 * Try to connect without a wallet popup. Preference order:
 *  1. Enrolled Perpl API key (one-click trading, survives forever until revoked)
 *  2. Stored SIWE session nonce (legacy flow, expires)
 * Returns true if connected, false if a fresh wallet interaction is needed.
 */
let _reconnectInFlight: Promise<boolean> | null = null;
/** Single-flight: the order click, the page warm-up and the background re-warm
 *  used to each open their own socket (three live sessions for one wallet at
 *  2026-10-12 08:34 UTC). Everyone now waits on the same attempt. */
export function tryAutoReconnect(walletAddress?: string): Promise<boolean> {
  if (session.ws && session.authenticated && session.ws.readyState === WebSocket.OPEN) return Promise.resolve(true);
  if (_reconnectInFlight) return _reconnectInFlight;
  _reconnectInFlight = tryAutoReconnectOnce(walletAddress).finally(() => { _reconnectInFlight = null; });
  return _reconnectInFlight;
}

async function tryAutoReconnectOnce(walletAddress?: string): Promise<boolean> {
  const address = walletAddress || localStorage.getItem('perpl_address') || '';

  // 1) API-key sign-in: never needs the wallet
  let key = getStoredApiKey(address);
  if (!key && !walletAddress) {
    // No address known (legacy perpl_address is only set by the SIWE flow),
    // scan for any enrolled key so one-click works right after enrollment.
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k?.startsWith('perpl_apikey_')) {
          key = getStoredApiKey(k.slice('perpl_apikey_'.length));
          if (key) break;
        }
      }
    } catch {}
  }
  if (key) {
    try {
      // Connect and fetch the account id concurrently, the account lookup is
      // an on-chain read and doesn't need the WS.
      const [conn] = await Promise.allSettled([
        openTradingWsApiKey(key.address),
        fetchAccountId(key.address),
      ]);
      if (conn.status === 'rejected') throw conn.reason;
      if (session.authenticated) return true;
    } catch (e) {
      console.warn('[perpl] api-key sign-in failed, falling back:', (e as Error)?.message);
    }
  }

  // 2) Legacy stored-nonce session
  try {
    const nonce = localStorage.getItem('perpl_nonce');
    const storedAddr = localStorage.getItem('perpl_address');
    if (!nonce || !storedAddr) return false;

    await openTradingWs(nonce, storedAddr);
    await fetchAccountId(storedAddr);
    return session.authenticated;
  } catch {
    // Nonce expired or invalid: clear stored data
    localStorage.removeItem('perpl_nonce');
    localStorage.removeItem('perpl_address');
    return false;
  }
}

async function fetchAccountId(address: string): Promise<void> {
  if (session.accountId) return;
  try {
    const resp = await fetch(`/api/leaders/positions/${address}?fresh=1`);
    const data = await resp.json();
    if (data?.account_id && !session.accountId) session.accountId = data.account_id;
  } catch {}
}

// Background pre-connect: called on page load so the first order click pays
// zero connection latency. No-op without an enrolled key or stored nonce;
// never triggers a wallet popup.
let _warming = false;
export async function warmupTradingConnection(address?: string): Promise<void> {
  if (_warming) return;
  const s = getSession();
  if (s.authenticated && s.hasWs) return;
  _warming = true;
  try {
    await tryAutoReconnect(address);
  } catch {} finally {
    _warming = false;
  }
}

/**
 * Open the trading WS authenticated with the enrolled Ed25519 API key
 * (mt:29 ApiKeySignIn): no cookies, no nonce, no wallet popup.
 */
export async function openTradingWsApiKey(address: string): Promise<WebSocket> {
  const key = getStoredApiKey(address);
  if (!key) throw new Error('No Perpl API key enrolled for this wallet');
  const frame = await buildWsSignInFrame(key);
  // token/address are consumed by our proxy (JWT gate) and stripped before
  // the frame is forwarded to Perpl.
  return openWsWithFirstFrame({ ...frame, address, token: getStoredAuthToken() });
}

/**
 * Step 3: Open trading WebSocket directly to Perpl and authenticate
 * (legacy SIWE-session flow, mt:4 + nonce)
 */
export function openTradingWs(nonce: string, address?: string): Promise<WebSocket> {
  // Send auth WITH our JWT so the proxy loads Perpl cookies only for OUR wallet.
  // The backend trusts the JWT's wallet, never the client-supplied `address`.
  return openWsWithFirstFrame({
    mt: 4,
    nonce,
    chain_id: CHAIN_ID,
    address: address || '',
    token: getStoredAuthToken(),
  });
}

function openWsWithFirstFrame(firstFrame: Record<string, unknown>): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(PERPL_WS);
    let resolved = false;
    let keepAlive = 0;

    const timeout = setTimeout(() => {
      if (!resolved) {
        resolved = true;
        ws.close();
        reject(new Error('Trading WebSocket connection timeout'));
      }
    }, 10000);

    ws.onopen = () => {
      ws.send(JSON.stringify(firstFrame));
    };

    ws.onmessage = (event) => {
      if (resolved) return;
      try {
        const msg = JSON.parse(event.data);

        // Error from proxy or Perpl
        if (msg.error || msg.code === 3401) {
          resolved = true;
          clearTimeout(timeout);
          ws.close();
          reject(new Error(msg.error || 'Perpl auth failed'));
          return;
        }

        // Track block numbers from heartbeats (mt:100) - always, even before auth resolves
        if (msg.mt === 100 && msg.sn) {
          session.lastBlock = msg.sn;
          lastBlockAt = Date.now();
          return; // don't resolve auth on heartbeats
        }

        // Any non-heartbeat response after auth means we're in
        if (msg.mt) {
          resolved = true;
          clearTimeout(timeout);
          session.ws = ws;
          session.authenticated = true;
          // Persistent tracker: lfr-based rq seeding + open-orders store
          _openOrders.clear();
          notifyOrderSubs();
          ws.addEventListener('message', trackSessionMessage);
          trackSessionMessage(event);
          if (msg.d?.acc) session.accountId = msg.d.acc;
          if (msg.d?.id) session.accountId = msg.d.id;
          if (typeof msg.d === 'object' && msg.d !== null) {
            const acc = msg.d.acc || msg.d.account_id || msg.d.id;
            if (acc) session.accountId = acc;
          }
          // Docs: send a Ping (mt:1) about every 30s to keep the connection
          // open: without it Perpl goes idle and stops sending heartbeats
          // while the TCP socket stays open ("Timeout waiting for block").
          // Watchdog: if heartbeats still stop, close so onclose re-warms.
          const connectedAt = Date.now();
          keepAlive = window.setInterval(() => {
            if (ws.readyState !== WebSocket.OPEN) { clearInterval(keepAlive); return; }
            try { ws.send(JSON.stringify({ mt: 1 })); } catch {}
            if (Date.now() - Math.max(lastBlockAt, connectedAt) > 30000) ws.close();
          }, 25000);
          resolve(ws);
        }
      } catch {
        // ignore non-JSON
      }
    };

    ws.onclose = (event) => {
      if (keepAlive) clearInterval(keepAlive);
      if (!resolved) {
        resolved = true;
        clearTimeout(timeout);
        reject(new Error(event.reason || `Trading WS closed (code: ${event.code})`));
        return;
      }
      // Dropped after a successful auth (idle timeout, network blip): clear the
      // dead session and silently re-warm so the next order doesn't pay
      // reconnect latency. It used to try ONCE and stop, so after a bad minute
      // at Perpl the next click paid the whole reconnect (2026-10-12). Now it
      // keeps trying in the background, backing off 0.3 s -> 30 s.
      if (session.ws === ws) {
        session.ws = null;
        session.authenticated = false;
        rewarm(0);
      }
    };

    ws.onerror = () => {
      if (!resolved) {
        resolved = true;
        clearTimeout(timeout);
        reject(new Error('Trading WebSocket connection failed'));
      }
    };
  });
}

const REWARM_DELAYS_MS = [300, 1000, 2000, 4000, 8000, 16000, 30000];
let rewarmTimer: ReturnType<typeof setTimeout> | null = null;
function rewarm(attempt: number): void {
  if (rewarmTimer) clearTimeout(rewarmTimer);
  rewarmTimer = setTimeout(async () => {
    rewarmTimer = null;
    if (session.ws && session.authenticated) return;        // something else reconnected
    try { await warmupTradingConnection(); } catch {}
    // Bounded: 10 tries over about 2.5 minutes, then the next click reconnects.
    if (!(session.ws && session.authenticated) && attempt < 9) rewarm(attempt + 1);
  }, REWARM_DELAYS_MS[Math.min(attempt, REWARM_DELAYS_MS.length - 1)]);
}

/**
 * Step 4: Place order to copy a position
 */
export async function placeOrder(params: {
  marketId: number;
  side: 'long' | 'short';
  size: number;
  leverage: number;
  price?: number;
  sizeDecimals: number;
  priceDecimals: number;
  orderMode?: 'market' | 'limit';
  limitPrice?: number;
  postOnly?: boolean;
  slippageBps?: number;  // market orders: user-chosen bound, capped at the server cap
}): Promise<any> {
  if (!session.ws || session.ws.readyState !== WebSocket.OPEN) {
    throw new Error('Trading WebSocket not connected');
  }

  if (!session.accountId) {
    throw new Error('No Perpl exchange account. Create one at perpl.xyz first.');
  }
  // Refuse rather than guess the decimals (beta money-path fix).
  requireMarketConfig(params.marketId);

  // Always get fresh block right before sending order
  const currentBlock = await getFreshBlock();

  const rq = ++requestId;
  const isLimit = params.orderMode === 'limit' && params.limitPrice;
  const orderType = params.side === 'long' ? 1 : 2;
  // OPEN size quantization rounds DOWN (audit A9): round-to-nearest could
  // order one lot MORE than the intended size (proven: 0.0808625 ETH -> 0.081).
  // Floor is scoped to THIS call site only: close sizes (closePosition) come
  // venue-quantized and are venue-clamped (flooring would strand dust), and
  // price fields everywhere stay round-to-nearest-tick. The 1e-9 epsilon
  // absorbs float artifacts so an exactly-quantized size never loses a lot.
  const scaledSize = Math.floor(params.size * (10 ** params.sizeDecimals) + 1e-9);
  const leverageHdths = Math.round(params.leverage * 100);
  const mcfg = MARKET_CONFIGS[params.marketId];

  let scaledPrice: number;
  let flags: number;
  let lastBlock: number;
  let slipBps: number | undefined;

  if (isLimit) {
    // Limit order: GTC (fl:0) by default, rests in the book, fills as maker
    // OR taker if it crosses. With postOnly the user opts into fl:1: guaranteed
    // maker fee, but Perpl rejects it if it would cross the book on entry.
    // lb is only the forwarding deadline, NOT the resting lifetime, docs cap
    // it at head_block + order_ttl_blocks; anything higher is rejected with
    // "last exec block too high". Resting comes from the GTC/PostOnly flag.
    scaledPrice = Math.round(params.limitPrice! * (10 ** params.priceDecimals));
    flags = params.postOnly ? 1 : 0;
    lastBlock = currentBlock + (mcfg?.orderTtlBlocks || 6);
  } else {
    // Market order: IOC with slippage bounded by the server's market-slippage
    // cap (order_max_market_slippage_bps, live 100 = 1%), sent explicitly via
    // the documented `ms` field AND mirrored in the price bound.
    const capBps = mcfg?.maxMarketSlippageBps ?? 100;
    slipBps = params.slippageBps ? Math.min(Math.max(1, Math.round(params.slippageBps)), capBps) : capBps;
    const maxImpact = slipBps / 10000;
    if (params.price) {
      const slippage = params.side === 'long' ? (1 + maxImpact) : (1 - maxImpact);
      scaledPrice = Math.round(params.price * slippage * (10 ** params.priceDecimals));
    } else {
      scaledPrice = 0;
    }
    flags = 4; // IOC
    lastBlock = currentBlock + (mcfg?.orderTtlBlocks || 6);
  }

  const order: Record<string, unknown> = {
    mt: 22,
    rq,
    mkt: params.marketId,
    acc: session.accountId,
    t: orderType,
    p: scaledPrice,
    s: scaledSize,
    fl: flags,
    lv: leverageHdths,
    lb: lastBlock,
    ...(slipBps !== undefined ? { ms: slipBps } : {}),
  };

  return new Promise((resolve, reject) => {
    let rqCur = rq;          // may be re-issued (sr:32 / expired-lb retries)
    let sr32Retried = false;
    let lbRetried = false;
    const wsAtSend = session.ws;   // docs: expired-lb re-issue is only safe with NO reconnect since posting

    const onMessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.mt === 100) return; // skip heartbeats
        console.log('[perpl-order] response:', msg);

        // mt:3 = status response (code 0 = accepted, 400+ = error)
        if (msg.mt === 3 && msg.status) {
          if (msg.status.code >= 400) {
            cleanup();
            reject(new Error(msg.status.error || `Perpl error ${msg.status.code}`));
          }
          // code 0 = accepted, wait for fill/order update
          return;
        }

        // Order update (24): check if our order got filled or rejected.
        // Classified via spec `st` first, then legacy fs/r (see classifyOrder).
        if (msg.mt === 24 && msg.d) {
          const ourOrder = msg.d.find?.((o: any) => o.rq === rqCur);
          if (ourOrder) {
            // sr:32 = OrderDescIdTooLow: another tab/client raced our rq. Docs:
            // retry ONCE with a fresh rq (the old one can never have executed).
            if (ourOrder.st === 7 && ourOrder.sr === 32 && !sr32Retried) {
              sr32Retried = true;
              rqCur = ++requestId;
              order.rq = rqCur;
              console.warn('[perpl-order] rq raced (sr:32), retrying once with new rq', rqCur);
              try { session.ws?.send(JSON.stringify(order)); } catch {}
              return;
            }
            const c = classifyOrder(ourOrder);
            cleanup();
            if (c.kind === 'filled') {
              // Filled: include the real Perpl order id (oid) so callers can prove a
              // confirmed fill (copy trading requires a real id, never intended values).
              resolve({ filled: true, filledSize: ourOrder.fs, filledPrice: ourOrder.fp, orderId: ourOrder.oid });
            } else if (c.kind === 'rejected') {
              reject(new Error(c.reason));
            } else {
              // Accepted and sitting in book (not filled yet, not removed)
              resolve({ placed: true, orderId: ourOrder.oid });
            }
            return;
          }
          return;
        }

        // Account update (21) with our request ID, only resolve if no mt:24 came first
        if (msg.mt === 21 && msg.lfr === rqCur) {
          // Wait a bit for mt:24 which has the real status
          return;
        }

        // Fill (25), positions (27)
        if (msg.mt === 25 || msg.mt === 27) {
          cleanup();
          resolve(msg);
          return;
        }

        if (msg.error) {
          cleanup();
          reject(new Error(typeof msg.error === 'string' ? msg.error : JSON.stringify(msg.error)));
        }
      } catch {
        // ignore
      }
    };

    // At-most-once retry: on the first timeout, RE-SEND the identical order
    // (same rq). Perpl guarantees at-most-once execution per rq, so this cannot
    // create a duplicate: it either executes once or is a no-op, then returns
    // the real status. On the second timeout, if the order's lb has already
    // expired (head block past it), the original can never execute, the docs
    // bless ONE re-issue with a fresh rq + fresh lb. Only then do we give up.
    let retried = false;
    let timer: ReturnType<typeof setTimeout>;
    const arm = (ms: number) => setTimeout(() => {
      if (!retried) {
        retried = true;
        console.warn('[perpl-order] no response, resending same rq', rqCur);
        try { session.ws?.send(JSON.stringify(order)); } catch {}
        timer = arm(10000);
      } else if (!lbRetried && session.lastBlock > (order.lb as number) && session.ws === wsAtSend) {
        // Docs: safe ONLY if no reconnect since posting, a reconnect may have
        // swallowed the status of an order that DID execute, and a new-rq
        // re-issue would then duplicate it.
        lbRetried = true;
        rqCur = ++requestId;
        order.rq = rqCur;
        order.lb = session.lastBlock + (mcfg?.orderTtlBlocks || 6);
        console.warn('[perpl-order] lb expired with no status, re-issuing with new rq', rqCur);
        try { session.ws?.send(JSON.stringify(order)); } catch {}
        timer = arm(10000);
      } else {
        cleanup();
        reject(new Error('No confirmation from Perpl. Check your positions before retrying, the order may already be live.'));
      }
    }, ms);
    timer = arm(15000);

    const cleanup = () => {
      clearTimeout(timer);
      session.ws?.removeEventListener('message', onMessage);
    };

    console.log('[perpl-order] sending:', order);
    session.ws.addEventListener('message', onMessage);
    session.ws.send(JSON.stringify(order));
  });
}

/**
 * Close an existing position
 */
export async function closePosition(params: {
  marketId: number;
  side: 'long' | 'short';
  size: number;
  priceDecimals: number;
  sizeDecimals: number;
  markPrice: number;
}): Promise<any> {
  if (!session.ws || session.ws.readyState !== WebSocket.OPEN) {
    throw new Error('Trading WebSocket not connected');
  }
  if (!session.accountId) {
    throw new Error('No account connected');
  }
  requireMarketConfig(params.marketId);

  // Always get fresh block right before sending order
  const currentBlock = await getFreshBlock();

  const rq = ++requestId;
  // CloseLong=3, CloseShort=4
  const orderType = params.side === 'long' ? 3 : 4;
  const scaledSize = Math.round(params.size * (10 ** params.sizeDecimals));
  const closeMcfg = MARKET_CONFIGS[params.marketId];
  // Market close: slippage bounded by the server cap (ms, live 100 bps = 1%),
  // mirrored in the price bound. Longs sell at mark - slip, shorts buy at + slip.
  const closeSlipBps = closeMcfg?.maxMarketSlippageBps ?? 100;
  const maxImpact = closeSlipBps / 10000;
  const slippage = params.side === 'long' ? (1 - maxImpact) : (1 + maxImpact);
  const scaledPrice = Math.round(params.markPrice * slippage * (10 ** params.priceDecimals));

  const order: Record<string, unknown> = {
    mt: 22,
    rq,
    mkt: params.marketId,
    acc: session.accountId,
    t: orderType,
    p: scaledPrice,
    s: scaledSize,
    fl: 4, // IOC
    lv: 0,
    lb: currentBlock + (closeMcfg?.orderTtlBlocks || 6),
    ms: closeSlipBps,
  };

  return new Promise((resolve, reject) => {
    let rqCur = rq;
    let sr32Retried = false;
    let lbRetried = false;
    const wsAtSend = session.ws;   // docs: expired-lb re-issue requires NO reconnect since posting
    const onMessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.mt === 100) return;
        console.log('[perpl-close] response:', msg);

        if (msg.mt === 3 && msg.status) {
          if (msg.status.code >= 400) { cleanup(); reject(new Error(msg.status.error || `Error ${msg.status.code}`)); }
          return;
        }
        if (msg.mt === 24 && msg.d) {
          const ourOrder = msg.d.find?.((o: any) => o.rq === rqCur);
          if (ourOrder) {
            if (ourOrder.st === 7 && ourOrder.sr === 32 && !sr32Retried) {
              sr32Retried = true;
              rqCur = ++requestId;
              order.rq = rqCur;
              try { session.ws?.send(JSON.stringify(order)); } catch {}
              return;
            }
            const c = classifyOrder(ourOrder);
            cleanup();
            if (c.kind === 'rejected') {
              // A rejected close must NOT be reported as success.
              reject(new Error(c.reason));
            } else {
              // Surface the real Perpl order id so the copy-close path can prove
              // the fill (backend requires a real id, never intended values).
              resolve({ closed: true, orderId: ourOrder.oid, filledSize: ourOrder.fs, filledPrice: ourOrder.fp });
            }
            return;
          }
        }
        if (msg.mt === 25 || msg.mt === 27) { cleanup(); resolve({ closed: true, perplConfirmed: true, raw: msg }); return; }
        if (msg.error) { cleanup(); reject(new Error(typeof msg.error === 'string' ? msg.error : JSON.stringify(msg.error))); }
      } catch {}
    };

    // At-most-once retry: resend the identical rq once on timeout (safe. Perpl
    // dedupes per rq). If the lb then expires with no status, re-issue ONCE with
    // a fresh rq + lb (docs-blessed), then give up with a warning.
    let retried = false;
    let timer: ReturnType<typeof setTimeout>;
    const arm = (ms: number) => setTimeout(() => {
      if (!retried) {
        retried = true;
        try { session.ws?.send(JSON.stringify(order)); } catch {}
        timer = arm(10000);
      } else if (!lbRetried && session.lastBlock > (order.lb as number) && session.ws === wsAtSend) {
        lbRetried = true;
        rqCur = ++requestId;
        order.rq = rqCur;
        order.lb = session.lastBlock + (closeMcfg?.orderTtlBlocks || 6);
        try { session.ws?.send(JSON.stringify(order)); } catch {}
        timer = arm(10000);
      } else {
        cleanup();
        reject(new Error('No confirmation from Perpl. Check your positions before retrying, the close may already be live.'));
      }
    }, ms);
    timer = arm(15000);
    const cleanup = () => { clearTimeout(timer); session.ws?.removeEventListener('message', onMessage); };

    console.log('[perpl-close] sending:', order);
    session.ws!.addEventListener('message', onMessage);
    session.ws!.send(JSON.stringify(order));
  });
}

/**
 * Cancel a resting order (t:5). Identifies the order via oid.
 * One-click: signed session, no wallet interaction.
 */
export async function cancelOrder(params: { marketId: number; oid: number }): Promise<any> {
  if (!session.ws || session.ws.readyState !== WebSocket.OPEN) {
    throw new Error('Trading WebSocket not connected');
  }
  if (!session.accountId) throw new Error('No account connected');

  const currentBlock = await getFreshBlock();
  const rq = ++requestId;
  const order: Record<string, unknown> = {
    mt: 22,
    rq,
    mkt: params.marketId,
    acc: session.accountId,
    oid: params.oid,
    t: 5, // Cancel
    s: 0,
    fl: 0,
    lv: 0,
    // lb 0 = "use the market's maximum window" (docs) and what Perpl's own app
    // sends. head + ttl could already be behind the chain when the frame
    // arrived -> "last exec block already expired".
    lb: 0,
  };
  void currentBlock;
  // A trigger order (SL / TP, st:8) is not in the book. A bare cancel was
  // answered sr:33 OrderDoesNotExist every time - 21 of 21 in the live log up
  // to 2026-10-12. Perpl's own app cancels a trigger by sending the order's
  // own trigger fields back (tp, tpc, lp, its fl and lv) with lb 0, which is
  // what marks the request as a trigger request. Same here.
  const row: any = _openOrders.get(params.oid);
  if (row && row.tp) {
    order.tp = row.tp;
    order.tpc = row.tpc;
    if (row.lp != null) order.lp = row.lp;
    order.fl = row.fl ?? 0;
    order.lv = row.lv ?? 0;
    order.lb = 0;
  }
  const res = await sendOrderRequest(order, (o) => {
    // The venue's rejection row carries oid 0, so it can only be matched by
    // rq. Matching on oid alone left a rejected cancel waiting for 25 s.
    const mine = o.oid === params.oid || o.rq === order.rq;
    if (!mine) return null;
    if (o.st === 7) return { reject: srReason(o.sr) };
    if (o.st === 5 || (o.oid === params.oid && o.r)) return { resolve: { canceled: true, orderId: params.oid } };
    if (o.st != null && ST_REJECTED.has(o.st)) return { reject: srReason(o.sr) };
    return null;
  }, '[perpl-cancel]');
  // After the venue ack only: lets the alerts bridge unregister a TP/SL.
  try { window.dispatchEvent(new CustomEvent('perpl_order_cancelled', { detail: { oid: params.oid, marketId: params.marketId } })); } catch {}
  return res;
}

/**
 * Modify a resting order's price/size in place (t:7 Change).
 * One-click: signed session, no wallet interaction.
 */
export async function modifyOrder(params: {
  marketId: number;
  oid: number;
  newPrice: number;
  newSize: number;
  leverage: number;
  priceDecimals: number;
  sizeDecimals: number;
}): Promise<any> {
  if (!session.ws || session.ws.readyState !== WebSocket.OPEN) {
    throw new Error('Trading WebSocket not connected');
  }
  if (!session.accountId) throw new Error('No account connected');
  requireMarketConfig(params.marketId);

  const currentBlock = await getFreshBlock();
  let rq = ++requestId;
  const order = {
    mt: 22,
    rq,
    mkt: params.marketId,
    acc: session.accountId,
    oid: params.oid,
    t: 7, // Change
    p: Math.round(params.newPrice * (10 ** params.priceDecimals)),
    s: Math.round(params.newSize * (10 ** params.sizeDecimals)),
    fl: 0,
    lv: Math.round(params.leverage * 100),
    lb: currentBlock + (MARKET_CONFIGS[params.marketId]?.orderTtlBlocks || 6),
  };
  return sendOrderRequest(order, (o) => {
    if (o.oid !== params.oid && o.rq !== rq) return null;
    if (o.st != null && ST_REJECTED.has(o.st)) return { reject: srReason(o.sr) };
    return { resolve: { modified: true, orderId: o.oid, price: o.p, size: o.s } };
  }, '[perpl-modify]', (newRq) => { rq = newRq; });
}

/**
 * Shared mt:22 request/response loop with the same at-most-once retry
 * discipline as placeOrder/closePosition: resend the identical rq once on
 * timeout (Perpl dedupes per rq), then give up loudly.
 */
function sendOrderRequest(
  order: Record<string, unknown>,
  classify: (o: any) => { resolve?: any; reject?: string } | null,
  tag: string,
  onRqChange?: (newRq: number) => void,
): Promise<any> {
  return new Promise((resolve, reject) => {
    let sr32Retried = false;
    const onMessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.mt === 100) return;
        console.log(`${tag} response:`, msg);

        if (msg.mt === 3 && msg.status) {
          if (msg.status.code >= 400) { cleanup(); reject(new Error(msg.status.error || `Perpl error ${msg.status.code}`)); }
          return;
        }
        if (msg.mt === 24 && Array.isArray(msg.d)) {
          for (const o of msg.d) {
            // sr:32 rq race (multi-tab): docs say retry ONCE with a fresh rq.
            if (o.rq === order.rq && o.st === 7 && o.sr === 32 && !sr32Retried) {
              sr32Retried = true;
              const newRq = ++requestId;
              order.rq = newRq;
              onRqChange?.(newRq);
              console.warn(`${tag} rq raced (sr:32), retrying once with new rq`, newRq);
              try { session.ws?.send(JSON.stringify(order)); } catch {}
              return;
            }
            const c = classify(o);
            if (c) {
              cleanup();
              if (c.reject) reject(new Error(c.reject));
              else resolve(c.resolve);
              return;
            }
          }
          return;
        }
        if (msg.error) {
          cleanup();
          reject(new Error(typeof msg.error === 'string' ? msg.error : JSON.stringify(msg.error)));
        }
      } catch {}
    };

    let retried = false;
    let timer: ReturnType<typeof setTimeout>;
    const arm = (ms: number) => setTimeout(() => {
      if (!retried) {
        retried = true;
        console.warn(`${tag} no response, resending same rq`, (order as any).rq);
        try { session.ws?.send(JSON.stringify(order)); } catch {}
        timer = arm(10000);
      } else {
        cleanup();
        reject(new Error('No confirmation from Perpl. Check your open orders before retrying.'));
      }
    }, ms);
    timer = arm(15000);

    const cleanup = () => {
      clearTimeout(timer);
      session.ws?.removeEventListener('message', onMessage);
    };

    console.log(`${tag} sending:`, order);
    session.ws!.addEventListener('message', onMessage);
    session.ws!.send(JSON.stringify(order));
  });
}

export function getSession() {
  return {
    authenticated: session.authenticated,
    accountId: session.accountId,
    hasWs: session.ws?.readyState === WebSocket.OPEN,
  };
}

export function setAccountId(id: number) {
  session.accountId = id;
}

export function disconnectPerpl() {
  session.ws?.close();
  session = { nonce: '', ws: null, accountId: 0, authenticated: false, lastBlock: 0 };
  _venueAccount = null;
  _openOrders.clear();
  notifyOrderSubs();
  _openOrders.clear();
  notifyOrderSubs();
}

/**
 * Exchange-native SL/TP on Perpl: a server-side TRIGGER order (spec:
 * tp = trigger price, tpc = trigger condition, lb:0 = server-managed
 * lifecycle, status 8 = Untriggered while armed).
 *
 * The order itself is a close-side order (t:3 CloseLong / t:4 CloseShort)
 * that activates when the MARK price crosses the trigger (mark resists wick
 * manipulation and matches what liquidations use):
 *   long  SL: mark <= trigger (tpc 4)   long  TP: mark >= trigger (tpc 3)
 *   short SL: mark >= trigger (tpc 3)   short TP: mark <= trigger (tpc 4)
 * Execution price is the trigger with the market's max impact as slippage
 * allowance (sell below / buy above), resting GTC after activation.
 */
/**
 * Remember the trigger we just submitted, under the oid the venue acked.
 *
 * The venue's ack frame for a trigger order carries the status and ids but not
 * necessarily `t` / `tpc` / `s`, and those are what tell SL from TP and give
 * the size. Without this the freshly armed leg could not be classified (or
 * cancelled as a sibling) until the next full mt:23 snapshot arrived - the
 * same gap that showed "size-" on the owner's ETH rows. `_local` stays true
 * until a venue frame for that oid lands, so the UI can say "syncing…".
 */
function rememberSubmittedTrigger(oid: number, frame: {
  mkt: number; t: number; p: number; s: number; tp: number; tpc: number; lp?: number;
}): void {
  const prev = _openOrders.get(oid);
  _openOrders.set(oid, {
    ...(prev ?? {}),
    oid,
    mkt: frame.mkt,
    t: prev?.t ?? frame.t,
    p: prev?.p ?? frame.p,
    s: prev?.s ?? frame.s,
    tp: prev?.tp ?? frame.tp,
    tpc: prev?.tpc ?? frame.tpc,
    lp: prev?.lp ?? frame.lp,
    st: prev?.st ?? 8,
    _local: prev ? (prev._local ?? false) : true,
  } as OpenOrder);
  notifyOrderSubs();
}

/** The armed (st:8) or activated (st:9) trigger of one kind for a position. */
export function findArmedTrigger(marketId: number, side: 'long' | 'short',
                                 kind: 'sl' | 'tp'): OpenOrder | null {
  const closeT = side === 'long' ? 3 : 4;
  const isSl = (o: OpenOrder) => (o.t === 3 ? (o.tpc === 4 || o.tpc === 2) : (o.tpc === 3 || o.tpc === 1));
  const hit = getOpenOrders().find((o) => (o.st === 8 || o.st === 9) && o.mkt === marketId
    && o.t === closeT && (kind === 'sl' ? isSl(o) : !isSl(o)));
  return hit ?? null;
}

/**
 * Arm an SL or TP, REPLACING the armed leg of the same kind (2026-10-08).
 *
 * Perpl has no "modify my stop" for us to use (t:7 Change on a trigger is
 * unverified against the venue), so a replacement is cancel-then-place. Every
 * caller goes through here so that a position can never carry two stop losses
 * or two take profits, which is exactly what happened to the ETH position on
 * 2026-10-08, when the Terminal's editor armed a pair on top of the pair the
 * copy flow had already armed.
 *
 * `onlyIfMissing` is the retry mode: if a leg of this kind is already armed we
 * keep it and report its oid instead of arming a second one. Perpl guarantees
 * at-most-once execution per rq but a retry builds a NEW rq, so a retry after
 * a timeout could otherwise double-arm.
 */
export async function armTrigger(params: {
  marketId: number;
  side: 'long' | 'short';
  kind: 'sl' | 'tp';
  triggerPrice: number;
  size: number;
  priceDecimals: number;
  sizeDecimals: number;
  onlyIfMissing?: boolean;
}): Promise<{ orderId: number | null; replaced: number | null; kept: boolean }> {
  const existing = findArmedTrigger(params.marketId, params.side, params.kind);
  if (existing && params.onlyIfMissing) {
    return { orderId: existing.oid, replaced: null, kept: true };
  }
  let replaced: number | null = null;
  if (existing) {
    // Cancel first and give up on failure: leaving the old leg armed is safe,
    // arming a second one is not.
    await cancelOrder({ marketId: params.marketId, oid: existing.oid });
    replaced = existing.oid;
  }
  const r = await placeSlTpOrder({
    marketId: params.marketId, side: params.side, orderType: params.kind,
    triggerPrice: params.triggerPrice, size: params.size,
    priceDecimals: params.priceDecimals, sizeDecimals: params.sizeDecimals,
  });
  return { orderId: r?.orderId ?? null, replaced, kept: false };
}

export async function placeSlTpOrder(params: {
  marketId: number;
  side: 'long' | 'short';
  orderType: 'sl' | 'tp';
  triggerPrice: number;
  size: number;
  priceDecimals: number;
  sizeDecimals: number;
}): Promise<{ orderId: number; placed: boolean }> {
  if (!session.ws || session.ws.readyState !== WebSocket.OPEN) {
    throw new Error('Trading WebSocket not connected');
  }
  if (!session.accountId) throw new Error('No account connected');
  requireMarketConfig(params.marketId);

  // Perpl caps armed trigger orders per account (instance config, live: 16),
  // guard client-side so the user gets a clear error, not a server reject.
  const triggerCap = MARKET_CONFIGS[params.marketId]?.maxTriggerOrders ?? 16;
  const armedTriggers = getOpenOrders().filter((o: any) => o.st === 8).length;
  if (armedTriggers >= triggerCap) {
    throw new Error(`Perpl allows ${triggerCap} armed SL/TP orders per account and you have ${armedTriggers}. Cancel one in Positions first.`);
  }

  let rq = ++requestId;
  const linkedPosition = venuePositionId(params.marketId, params.side);
  const t = params.side === 'long' ? 3 : 4; // close side
  const tpc = params.side === 'long'
    ? (params.orderType === 'sl' ? 4 : 3)   // LTEMark : GTEMark
    : (params.orderType === 'sl' ? 3 : 4);  // GTEMark : LTEMark
  // Exec-price allowance once triggered (a LIMIT bound on the close, not market
  // slippage). Deliberately wider than the 1% market-order slippage cap, a
  // triggered stop must still fill through a fast move; 5% preserves the
  // long-standing behavior and is independent of order_max_market_slippage_bps.
  const SLTP_EXEC_BOUND_PCT = 5;
  const maxImpact = SLTP_EXEC_BOUND_PCT / 100;
  // Closing a long sells (accept below trigger); closing a short buys (above).
  const execMult = params.side === 'long' ? (1 - maxImpact) : (1 + maxImpact);

  const order = {
    mt: 22,
    rq,
    mkt: params.marketId,
    acc: session.accountId,
    t,
    p: Math.round(params.triggerPrice * execMult * (10 ** params.priceDecimals)),
    s: Math.round(params.size * (10 ** params.sizeDecimals)),
    fl: 0,   // GTC once triggered
    lv: 0,
    // Docs: "Trigger orders must set lb: 0 (no expiry block). The server
    // manages their lifecycle from the trigger condition." Any positive lb
    // is capped at head_block + order_ttl_blocks and a far-future value is
    // rejected with "last exec block too high".
    lb: 0,
    tp: Math.round(params.triggerPrice * (10 ** params.priceDecimals)),
    tpc,
    // Link the trigger to the position it protects, when the venue has told us
    // the position id (mt:26/27 `pid`).
    //
    // Why this matters (proof in audits/SYNC_UI_REPORT.md section 1): Perpl
    // cancels an `lp`-linked trigger ITSELF the moment the position closes or
    // inverts. In the owner's own order history, trigger oid 288791797170211
    // (lp 7316779368451) went st:8 -> st:5 sr:28 at block 111717102, the same
    // block its position closed: with txid all-zeroes, i.e. a server-side
    // cancel with no transaction of ours. Unlinked legs (the four ETH triggers,
    // which carry no `lp`) outlived their position and had to be cancelled by
    // hand.
    //
    // Omitted rather than guessed when no position frame has arrived yet: a
    // wrong `lp` would let the venue cancel the wrong leg. The client-side
    // orphan sweep stays in place either way.
    ...(linkedPosition != null ? { lp: linkedPosition } : {}),
  };

  console.log('[order-timing] sltp-submit', Date.now(),
    `${params.orderType} mkt:${params.marketId} trig:${params.triggerPrice} size:${params.size}`);
  const res = await sendOrderRequest(order, (o) => {
    if (o.rq !== rq) return null;
    if ((o.st != null && ST_REJECTED.has(o.st)) || o.r) return { reject: srReason(o.sr) };
    console.log('[order-timing] sltp-ack', Date.now(), `oid:${o.oid} st:${o.st}`);
    return { resolve: { placed: true, orderId: o.oid, row: o } };
  }, `[perpl-${params.orderType}]`, (newRq) => { rq = newRq; });
  // The venue acked an oid: record what we submitted under it, so the leg can
  // be classified (SL vs TP), sized and cancelled immediately instead of only
  // after the next mt:23 snapshot.
  if (res?.orderId != null) {
    rememberSubmittedTrigger(res.orderId, {
      mkt: order.mkt, t: order.t, p: order.p, s: order.s, tp: order.tp, tpc: order.tpc,
      lp: (order as any).lp,
    });
  }
  // After the venue ack only: lets the alerts bridge register this TP/SL so the
  // server can say which one fired when the position shrinks (no tab needed).
  try {
    window.dispatchEvent(new CustomEvent('perpl_sltp_placed', { detail: {
      oid: res?.orderId, marketId: params.marketId, side: params.side, kind: params.orderType,
      triggerPrice: params.triggerPrice, size: params.size } }));
  } catch {}
  return res;
}

// Market configs loaded dynamically from Perpl context API
export interface MarketConfig {
  sizeDecimals: number;
  priceDecimals: number;
  initialMarginHdths: number;
  maintenanceMarginHdths: number;
  maxLeverage: number;
  takerFeeBps: number;
  makerFeeBps?: number;   // post-only/limit fee (display only); optional for back-compat
  orderTtlBlocks: number;
  maxPriceImpactPct: number;
  maxMarketSlippageBps: number;  // server-side market-order slippage cap in bps (live: 100 = 1%)
  maxTriggerOrders: number;      // armed trigger orders allowed per account (live: 16)
  fundingIntervalSec: number;
}

export let MARKET_CONFIGS: Record<number, MarketConfig> = {};
let _configsLoaded = false;
let _configsInFlight: Promise<void> | null = null;

// Beta money-path fix: a failed first load used to leave MARKET_CONFIGS empty
// for the whole tab (nothing retried), and the order/close/trigger call sites
// then fell back to hard-coded decimals that are only right for BTC. Now the
// load retries 3x (1s, 2s, 4s) and the call sites refuse instead of guessing.
export async function loadMarketConfigs(): Promise<void> {
  if (_configsLoaded) return;
  if (_configsInFlight) return _configsInFlight;
  _configsInFlight = (async () => {
    const delays = [1000, 2000, 4000];
    for (let attempt = 0; attempt <= delays.length; attempt++) {
      try {
        await fetchMarketConfigsOnce();
        return;
      } catch (e) {
        console.error(`[perpl] market configs attempt ${attempt + 1} failed:`, e);
        if (attempt < delays.length) await new Promise((r) => setTimeout(r, delays[attempt]));
      }
    }
  })();
  try {
    await _configsInFlight;
  } finally {
    _configsInFlight = null;
  }
}

async function fetchMarketConfigsOnce(): Promise<void> {
    const resp = await fetch('/api/market-configs');
    if (!resp.ok) throw new Error(`/api/market-configs HTTP ${resp.status}`);
    const configs = await resp.json();
    if (!Array.isArray(configs) || configs.length === 0) throw new Error('/api/market-configs returned no markets');
    for (const c of configs) {
      if (c?.market_id == null || c.price_decimals == null || c.size_decimals == null) {
        throw new Error(`/api/market-configs row missing decimals: ${JSON.stringify(c)}`);
      }
      MARKET_CONFIGS[c.market_id] = {
        sizeDecimals: c.size_decimals,
        priceDecimals: c.price_decimals,
        initialMarginHdths: c.initial_margin,
        maintenanceMarginHdths: c.maintenance_margin,
        maxLeverage: c.max_leverage,
        takerFeeBps: c.taker_fee,
        makerFeeBps: c.maker_fee,   // display-only; real maker rate for post-only limit orders
        orderTtlBlocks: c.order_ttl_blocks,
        maxPriceImpactPct: c.max_price_impact_pct,
        maxMarketSlippageBps: c.max_market_slippage_bps ?? 100,
        maxTriggerOrders: c.max_trigger_orders ?? 16,
        fundingIntervalSec: c.funding_interval_sec,
      };
    }
    _configsLoaded = true;
  // Tell anyone who already rendered without decimals (2026-10-09): the account
  // store computes liquidation prices at read time, so a chain read that landed
  // before the configs arrived produced rows with no liquidation price and kept
  // them until the next read. Seen on /portfolio, where nothing else triggers a
  // re-read for up to 30s.
  try { window.dispatchEvent(new CustomEvent('market_configs_loaded')); } catch {}
    console.log('[perpl] loaded market configs:', Object.keys(MARKET_CONFIGS));
}

// Single source of truth for "can we build an order for this market?".
// Throws with the message the UI shows; never returns partial decimals.
export function requireMarketConfig(marketId: number): MarketConfig {
  const c = MARKET_CONFIGS[marketId];
  if (!c || c.priceDecimals == null || c.sizeDecimals == null) {
    loadMarketConfigs();   // kick a reload for the next attempt
    throw new Error('Market settings not loaded. Refresh and try again.');
  }
  return c;
}

export function isConfigsLoaded(): boolean {
  return _configsLoaded;
}
