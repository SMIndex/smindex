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

// Timestamp of the last mt:100 heartbeat — lets getFreshBlock skip the
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
    return await waitForBlock(5000);
  } catch {
    // No heartbeat means the socket is half-dead (Perpl went idle server-side
    // without closing TCP). Tear it down, re-auth once, wait for the first
    // heartbeat of the new session — instead of failing the order click.
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
// server's last-forwarded request (lfr) — otherwise error 32. Bump our
// counter whenever the server tells us its lfr.
function bumpRequestIdFrom(msg: any): void {
  const lfr = msg?.lfr ?? msg?.d?.lfr ?? msg?.d?.acc?.lfr;
  if (typeof lfr === 'number' && lfr >= requestId) requestId = lfr;
}

// ---------------------------------------------------------------------------
// Open-orders store — fed by mt:23 (snapshot) / mt:24 (updates) on the
// trading WS. Terminal statuses drop the row; resting orders stay.
// ---------------------------------------------------------------------------

export interface OpenOrder {
  oid: number;
  mkt: number;
  t: number;       // 1 OpenLong / 2 OpenShort / 3 CloseLong / 4 CloseShort
  p: number;       // scaled price
  s: number;       // scaled size
  fs?: number;     // filled size
  st?: number;     // status (8 = Untriggered — armed SL/TP trigger)
  lv?: number;
  rq?: number;
  tp?: number;     // trigger price (scaled) for trigger orders
  tpc?: number;    // trigger condition (1 GTELast/2 LTELast/3 GTEMark/4 LTEMark)
}

const ST_TERMINAL = new Set([4, 5, 6, 7, 10]); // Filled/Canceled/Expired/Failed/Executed

const _openOrders = new Map<number, OpenOrder>();
const _orderSubs = new Set<() => void>();
let _ordersSnapshot: OpenOrder[] = [];

function notifyOrderSubs() {
  _ordersSnapshot = Array.from(_openOrders.values());
  _orderSubs.forEach((fn) => { try { fn(); } catch {} });
}

function ingestOrderRows(rows: any[]): void {
  let changed = false;
  for (const o of rows) {
    if (o?.oid == null) continue;
    if (o.st != null && ST_TERMINAL.has(o.st)) {
      if (_openOrders.delete(o.oid)) changed = true;
    } else if (o.r) {
      if (_openOrders.delete(o.oid)) changed = true;
    } else {
      _openOrders.set(o.oid, o as OpenOrder);
      changed = true;
    }
  }
  if (changed) notifyOrderSubs();
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
      if (msg.mt === 23) _openOrders.clear();
      ingestOrderRows(msg.d);
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

// Official Perpl reject reasons (sr) — human-readable. Spec: docs.perpl.xyz.
const SR_REASONS: Record<number, string> = {
  1: 'Amount exceeds available balance.',
  13: 'Limit price would fill immediately. Set price further from market.',
  14: 'Order expired before execution (block deadline passed). Try again.',
  15: 'Order forwarding reverted on-chain. Try again.',
  20: 'Invalid expiry block (lb outside allowed window). Try again.',
  32: 'Order id too low (stale session). Reconnect and retry.',
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
export async function tryAutoReconnect(walletAddress?: string): Promise<boolean> {
  const address = walletAddress || localStorage.getItem('perpl_address') || '';

  // 1) API-key sign-in — never needs the wallet
  let key = getStoredApiKey(address);
  if (!key && !walletAddress) {
    // No address known (legacy perpl_address is only set by the SIWE flow) —
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
      // Connect and fetch the account id concurrently — the account lookup is
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
    // Nonce expired or invalid — clear stored data
    localStorage.removeItem('perpl_nonce');
    localStorage.removeItem('perpl_address');
    return false;
  }
}

async function fetchAccountId(address: string): Promise<void> {
  if (session.accountId) return;
  try {
    const resp = await fetch(`/api/leaders/positions/${address}`);
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
 * (mt:29 ApiKeySignIn) — no cookies, no nonce, no wallet popup.
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
          // open — without it Perpl goes idle and stops sending heartbeats
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
      // reconnect latency. A failed re-auth stops here (no loop).
      if (session.ws === ws) {
        session.ws = null;
        session.authenticated = false;
        setTimeout(() => { warmupTradingConnection().catch(() => {}); }, 1500);
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
    // Limit order: GTC (fl:0) by default — rests in the book, fills as maker
    // OR taker if it crosses. With postOnly the user opts into fl:1: guaranteed
    // maker fee, but Perpl rejects it if it would cross the book on entry.
    // lb is only the forwarding deadline, NOT the resting lifetime — docs cap
    // it at head_block + order_ttl_blocks; anything higher is rejected with
    // "last exec block too high". Resting comes from the GTC/PostOnly flag.
    scaledPrice = Math.round(params.limitPrice! * (10 ** params.priceDecimals));
    flags = params.postOnly ? 1 : 0;
    lastBlock = currentBlock + (mcfg?.orderTtlBlocks || 6);
  } else {
    // Market order: IOC with slippage bounded by the server's market-slippage
    // cap (order_max_market_slippage_bps, live 100 = 1%) — sent explicitly via
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

        // Order update (24) — check if our order got filled or rejected.
        // Classified via spec `st` first, then legacy fs/r (see classifyOrder).
        if (msg.mt === 24 && msg.d) {
          const ourOrder = msg.d.find?.((o: any) => o.rq === rqCur);
          if (ourOrder) {
            // sr:32 = OrderDescIdTooLow — another tab/client raced our rq. Docs:
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
              // Filled — include the real Perpl order id (oid) so callers can prove a
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

        // Account update (21) with our request ID — only resolve if no mt:24 came first
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
    // create a duplicate — it either executes once or is a no-op, then returns
    // the real status. On the second timeout, if the order's lb has already
    // expired (head block past it), the original can never execute — the docs
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
        // Docs: safe ONLY if no reconnect since posting — a reconnect may have
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
        reject(new Error('No confirmation from Perpl. Check your positions before retrying — the order may already be live.'));
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

    // At-most-once retry: resend the identical rq once on timeout (safe — Perpl
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
        reject(new Error('No confirmation from Perpl. Check your positions before retrying — the close may already be live.'));
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
  const order = {
    mt: 22,
    rq,
    mkt: params.marketId,
    acc: session.accountId,
    oid: params.oid,
    t: 5, // Cancel
    s: 0,
    fl: 0,
    lv: 0,
    lb: currentBlock + (MARKET_CONFIGS[params.marketId]?.orderTtlBlocks || 6),
  };
  return sendOrderRequest(order, (o) => {
    if (o.oid !== params.oid) return null;
    if (o.st === 5 || o.r) return { resolve: { canceled: true, orderId: o.oid } };
    if (o.st != null && ST_REJECTED.has(o.st) && o.st !== 5) return { reject: srReason(o.sr) };
    return null;
  }, '[perpl-cancel]');
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
  _openOrders.clear();
  notifyOrderSubs();
}

/**
 * Exchange-native SL/TP on Perpl — a server-side TRIGGER order (spec:
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

  // Perpl caps armed trigger orders per account (instance config, live: 16) —
  // guard client-side so the user gets a clear error, not a server reject.
  const triggerCap = MARKET_CONFIGS[params.marketId]?.maxTriggerOrders ?? 16;
  const armedTriggers = getOpenOrders().filter((o: any) => o.st === 8).length;
  if (armedTriggers >= triggerCap) {
    throw new Error(`Perpl allows ${triggerCap} armed SL/TP orders per account and you have ${armedTriggers}. Cancel one in Positions first.`);
  }

  let rq = ++requestId;
  const t = params.side === 'long' ? 3 : 4; // close side
  const tpc = params.side === 'long'
    ? (params.orderType === 'sl' ? 4 : 3)   // LTEMark : GTEMark
    : (params.orderType === 'sl' ? 3 : 4);  // GTEMark : LTEMark
  // Exec-price allowance once triggered (a LIMIT bound on the close, not market
  // slippage). Deliberately wider than the 1% market-order slippage cap — a
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
  };

  return sendOrderRequest(order, (o) => {
    if (o.rq !== rq) return null;
    if ((o.st != null && ST_REJECTED.has(o.st)) || o.r) return { reject: srReason(o.sr) };
    return { resolve: { placed: true, orderId: o.oid } };
  }, `[perpl-${params.orderType}]`, (newRq) => { rq = newRq; });
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

export async function loadMarketConfigs(): Promise<void> {
  if (_configsLoaded) return;
  try {
    const resp = await fetch('/api/market-configs');
    const configs = await resp.json();
    for (const c of configs) {
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
    console.log('[perpl] loaded market configs:', Object.keys(MARKET_CONFIGS));
  } catch (e) {
    console.error('[perpl] failed to load market configs:', e);
  }
}

export function isConfigsLoaded(): boolean {
  return _configsLoaded;
}
