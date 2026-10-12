/**
 * Perpl's own authenticated account reads (2026-10-08).
 *
 * These are the only real source for: executed fills (with the REAL fee),
 * order history, position history, and account events (deposits, withdrawals,
 * funding, settlements, liquidations). We hold no equivalent of our own, our
 * tables only know about orders placed through SMINDEX.
 *
 * NO WALLET SIGNATURE is involved: each request is signed with the Ed25519
 * one-click key already enrolled in this browser (Perpl's documented canonical
 * scheme) and relayed through our backend, which only forwards the four
 * read-only targets. Perpl answers for the key's own account only.
 *
 * Docs: docs.perpl.xyz/resources/for-developers/api/rest: authed endpoints,
 * ~60 req/min, page cursor + count (default 50, max 100).
 */
import api from '@/lib/api';
import { getStoredApiKey, signRestRequest, type StoredApiKey } from '@/lib/perplApiKey';

export class NoPerplKeyError extends Error {
  constructor() {
    super('Enable one-click trading to read your Perpl history (no wallet signature needed afterwards)');
    this.name = 'NoPerplKeyError';
  }
}

function keyFor(address?: string | null): StoredApiKey {
  const k = getStoredApiKey(address);
  if (!k) throw new NoPerplKeyError();
  return k;
}

export function hasPerplKey(address?: string | null): boolean {
  return !!getStoredApiKey(address);
}

async function signedGet(address: string | null | undefined, path: string,
                         params?: Record<string, string | number | undefined>): Promise<any> {
  const key = keyFor(address);
  const qs = Object.entries(params ?? {})
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join('&');
  // The canonical string signs the request-target INCLUDING the query string.
  const target = qs ? `${path}?${qs}` : path;
  const signed = await signRestRequest(key, 'GET', target);
  const resp = await api.post('/api/auth/perpl-apikey-proxy', signed);
  return resp.data?.data;
}

/**
 * The timestamp object every row on these feeds carries.
 *
 * CORRECTION (2026-10-09): `at` is an OBJECT, not a number. Captured row:
 *   "at": {"b":111717102,"t":1791494835000,"tx":5,"txid":"ded938ac…","l":8}
 * Treating it as a number made every derived date NaN, which is why the
 * Portfolio's calendar, daily P/L and trade list stayed empty even with a
 * one-click key present. Use `atMs(row)` from perplAccountStats.
 */
export interface PerplAt {
  b: number;         // block
  t: number;         // ms timestamp
  tx?: number;
  txid?: string;     // all-zeroes for a server-side event with no transaction
  l?: number;        // log index
}

export interface PerplFill {
  at: PerplAt;
  mkt: number;       // market id
  acc: number;       // account id
  oid: number;       // order id
  t: number;         // order type 1 OpenLong / 2 OpenShort / 3 CloseLong / 4 CloseShort
  l: number;         // liquidity side 1 maker / 2 taker
  p: number;         // price (scaled by the market's price_decimals)
  s: number;         // size (scaled by the market's size_decimals)
  f: string;         // fee, micro-USD as a STRING (negative = rebate)
  bfa?: string;      // builder fee portion
}

/** A closed position as the venue records it (GET /v1/trading/position-history). */
export interface PerplPosition {
  at: PerplAt;
  mkt: number; acc: number;
  pid: number;       // position id: a trigger order's `lp` points at this
  rq: number; oid: number;
  st: number;        // position status (2 in every closed row observed)
  sr: number;
  sd: number;        // PositionType: 1 Long, 2 Short  (the CHAIN uses 0/1)
  c: string;         // collateral, micro-USD
  ep: number;        // entry price (scaled)
  epr?: number;
  s: number;         // size (scaled)
  fee: string; cfee: string;
  lv: number;        // leverage, hundredths
  cpnl: string; dpnl: string;   // realized P/L, micro-USD
  fnd: string;       // funding, micro-USD
  pay?: string;
  xp?: number;       // exit price (scaled)
  xfs?: number;
  ots?: Record<string, unknown>;
}

export interface PerplAccountEvent {
  at: PerplAt; in?: number; id?: number;
  et: number;        // AccountEventType: 1 Deposit, 2 Withdrawal, 4 Settlement,
                     // 5 Liquidation, 8 Funding (docs: api/rest)
  m?: number; r?: number; o?: number; p?: number;
  a?: string;        // amount, micro-USD as a STRING
  b?: string;        // balance after
  lb?: string;       // locked balance after
  f?: string; bfa?: string;
}

/** One page of fills, newest first. `page` is the cursor from the previous page. */
export const getPerplFills = (address: string, page?: string, count = 100) =>
  signedGet(address, '/v1/trading/fills', { page, count }) as Promise<any>;

export const getPerplOrderHistory = (address: string, page?: string, count = 100) =>
  signedGet(address, '/v1/trading/order-history', { page, count }) as Promise<any>;

export const getPerplPositionHistory = (address: string, page?: string, count = 100) =>
  signedGet(address, '/v1/trading/position-history', { page, count }) as Promise<any>;

export const getPerplAccountHistory = (address: string, page?: string, count = 100) =>
  signedGet(address, '/v1/trading/account-history', { page, count }) as Promise<any>;

/**
 * Follow the page cursor until `max` records or the end.
 *
 * CORRECTION (2026-10-09): the cursor field is **`np`**. The page shape is
 * `{ d: Row[], np: string }` (docs: api/rest, AccountHistoryPage, and every
 * captured response). We looked for `page`/`next`/`cursor`, none of which
 * exists, so paging always stopped after the first 100 rows and every
 * "all-time" number on the Portfolio was really "the newest 100 records".
 *
 * Deep paging is also genuinely fragile, a straight run of 80 pages against
 * Perpl dropped the connection with a read error partway through, so one
 * failed page is retried with a short backoff instead of truncating the
 * history silently, and `complete` says whether the end was actually reached.
 * `onProgress` lets the page show rows as they arrive rather than a spinner.
 */
export async function pageAll<T = any>(
  fetchPage: (page?: string) => Promise<any>,
  max = 400,
  opts: { onProgress?: (rows: T[], complete: boolean) => void; maxPages?: number } = {},
): Promise<{ rows: T[]; complete: boolean }> {
  const rows: T[] = [];
  const maxPages = opts.maxPages ?? Math.ceil(max / 100) + 2;
  let page: string | undefined;
  for (let i = 0; i < maxPages && rows.length < max; i++) {
    let res: any;
    let err: any;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        res = await fetchPage(page);
        err = null;
        break;
      } catch (e) {
        err = e;
        await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
      }
    }
    if (err) {
      if (!rows.length) throw err;
      return { rows, complete: false };          // partial, and we say so
    }
    const batch: T[] = Array.isArray(res) ? res : (res?.d ?? res?.data ?? res?.rows ?? []);
    rows.push(...batch);
    opts.onProgress?.(rows, false);
    const next = res?.np ?? res?.page ?? res?.next ?? res?.cursor;
    if (!next || !batch.length) {
      opts.onProgress?.(rows, true);
      return { rows, complete: true };
    }
    page = String(next);
  }
  opts.onProgress?.(rows, false);
  return { rows, complete: false };
}
