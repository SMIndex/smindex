// Beta readiness 4.1/4.2: order payload construction vs Perpl's mt:22 spec.
// Bundles src/lib/perplTrading.ts with esbuild (import.meta.env stubbed) and runs
// it in node against a FAKE trading WebSocket that never touches the network:
// it answers sign-in, emits heartbeats (mt:100 sn) and acks every mt:22 frame
// with an mt:24 row so the promise resolves. Every frame sent is captured and
// compared field by field with the spec-derived expectation.
// Market configs = prod's live /api/market-configs (captured to JSON).
// Run: node tests/order_payload_check.mjs
import { build } from 'esbuild';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const configs = JSON.parse(readFileSync(path.join(here, 'market_configs_prod_20261007.json'), 'utf8'));
const byId = Object.fromEntries(configs.map((c) => [c.market_id, c]));

// ---- browser shims ----
const store = new Map();
globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
const winTarget = new EventTarget();
globalThis.window = Object.assign(winTarget, { location: { protocol: 'https:', host: 'smindex.xyz', hostname: 'smindex.xyz' },
  setInterval, clearInterval, setTimeout, clearTimeout, localStorage: globalThis.localStorage });
globalThis.CustomEvent = class extends Event { constructor(t, o) { super(t); this.detail = o?.detail; } };
globalThis.fetch = async (u) => ({ ok: true, status: 200, json: async () => (String(u).includes('market-configs') ? configs : {}) });

const sent = [];
let SN = 50_000_000;
let lastWS = null;
class FakeWS extends EventTarget {
  static OPEN = 1; static CONNECTING = 0; static CLOSED = 3;
  constructor(url) { super(); this.url = url; this.readyState = 1; lastWS = this;
    setTimeout(() => this.onopen?.(), 5);
    this.hb = setInterval(() => this._emit({ mt: 100, sn: ++SN }), 200); }
  _emit(obj) { const ev = new MessageEvent('message', { data: JSON.stringify(obj) }); this.onmessage?.(ev); this.dispatchEvent(ev); }
  send(raw) {
    const f = JSON.parse(raw);
    if (f.mt === 4 || f.mt === 29) { setTimeout(() => this._emit({ mt: 20, d: { acc: 777 } }), 5); return; }
    if (f.mt !== 22) return;
    sent.push(f);
    const row = { rq: f.rq, oid: 9000 + sent.length, mkt: f.mkt, s: f.s, fs: f.s, fp: f.p, st: f.t === 5 ? 5 : (f.tp ? 8 : 4) };   // a cancel is st 5 even when it carries the trigger fields
    if (f.t === 5) row.oid = f.oid;
    setTimeout(() => this._emit({ mt: 24, d: [row] }), 10);
  }
  close() { this.readyState = 3; clearInterval(this.hb); }
}
globalThis.WebSocket = FakeWS;
globalThis.MessageEvent = globalThis.MessageEvent || class extends Event { constructor(t, o) { super(t); this.data = o?.data; } };

// ---- bundle the real module ----
const out = await build({
  entryPoints: [path.join(root, 'src/lib/perplTrading.ts')], bundle: true, write: false, format: 'esm', platform: 'browser',
  alias: { '@': path.join(root, 'src') }, define: { 'import.meta.env': '{}' }, logLevel: 'silent',
  external: [],
});
const code = out.outputFiles[0].text;
const mod = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
await mod.loadMarketConfigs();
const origLog = console.log; console.log = () => {};   // silence [perpl-order] chatter
await mod.openTradingWs('nonce', '0x' + '1'.repeat(40));

const cfg = (id) => ({ pd: byId[id].price_decimals, sd: byId[id].size_decimals, ttl: byId[id].order_ttl_blocks, ms: byId[id].max_market_slippage_bps });
const results = [];
const check = (name, frame, exp) => {
  const bad = Object.entries(exp).filter(([k, v]) => frame?.[k] !== v).map(([k, v]) => `${k}: got ${frame?.[k]} want ${v}`);
  // `lp` (linked position id, 2026-10-09) is part of the spec'd trigger frame.
  const extra = Object.keys(frame || {}).filter((k) => !['mt', 'rq', 'mkt', 'acc', 't', 'p', 's', 'fl', 'lv', 'lb', 'ms', 'tp', 'tpc', 'oid', 'lp'].includes(k));
  if (extra.length) bad.push('unknown fields ' + extra.join(','));
  results.push([bad.length ? 'FAIL' : 'PASS', name, bad.join('; ')]);
};
const last = () => sent[sent.length - 1];
const lbOk = (f, ttl) => f.lb > SN - 10 && f.lb <= SN + ttl + 1;

// 1-2 market open long/short (BTC), 3-4 limit GTC / post-only (ETH), 5 MON open floors size
for (const [side, t, mult] of [['long', 1, 1.01], ['short', 2, 0.99]]) {
  const c = cfg(1);
  await mod.placeOrder({ marketId: 1, side, size: 0.00059, leverage: 5, price: 84000, sizeDecimals: c.sd, priceDecimals: c.pd, orderMode: 'market' });
  const f = last();
  check(`market open ${side} BTC`, f, { mt: 22, mkt: 1, acc: 777, t, p: Math.round(84000 * mult * 10 ** c.pd), s: 59, fl: 4, lv: 500, ms: c.ms });
  results.push([lbOk(f, c.ttl) ? 'PASS' : 'FAIL', `market open ${side} BTC lb = fresh block + ttl`, `lb ${f.lb} head ${SN}`]);
}
{ const c = cfg(20);
  await mod.placeOrder({ marketId: 20, side: 'long', size: 0.0808625, leverage: 3, price: 2600, sizeDecimals: c.sd, priceDecimals: c.pd, orderMode: 'limit', limitPrice: 2550.555 });
  check('limit GTC long ETH (size floors 0.0808625 -> 80 lots, price rounds to tick)', last(), { t: 1, p: Math.round(2550.555 * 10 ** c.pd), s: 80, fl: 0, lv: 300 });
  results.push([last().ms === undefined ? 'PASS' : 'FAIL', 'limit order carries no ms field', String(last().ms)]);
  await mod.placeOrder({ marketId: 20, side: 'short', size: 0.5, leverage: 2, price: 2600, sizeDecimals: c.sd, priceDecimals: c.pd, orderMode: 'limit', limitPrice: 2700, postOnly: true });
  check('limit post-only short ETH', last(), { t: 2, p: 2700 * 10 ** c.pd, s: 500, fl: 1, lv: 200 }); }
{ const c = cfg(10);
  await mod.placeOrder({ marketId: 10, side: 'long', size: 1000.9, leverage: 2, price: 0.0332, sizeDecimals: c.sd, priceDecimals: c.pd, orderMode: 'market' });
  check('market open long MON (sd 0: 1000.9 floors to 1000; pd 6)', last(), { t: 1, s: 1000, p: Math.round(0.0332 * 1.01 * 10 ** c.pd), fl: 4, lv: 200 }); }
// user slippage below the cap is honoured, above the cap is clamped
{ const c = cfg(1);
  await mod.placeOrder({ marketId: 1, side: 'long', size: 0.001, leverage: 5, price: 84000, sizeDecimals: c.sd, priceDecimals: c.pd, orderMode: 'market', slippageBps: 30 });
  check('market slippage 30 bps honoured', last(), { ms: 30, p: Math.round(84000 * 1.003 * 10 ** c.pd) });
  await mod.placeOrder({ marketId: 1, side: 'long', size: 0.001, leverage: 5, price: 84000, sizeDecimals: c.sd, priceDecimals: c.pd, orderMode: 'market', slippageBps: 5000 });
  check('market slippage 5000 bps clamped to server cap', last(), { ms: c.ms }); }

// 6-9 SL / TP for long and short (trigger orders: close side, lb 0, fl 0, lv 0, tp + tpc, 5% exec bound)
for (const [side, kind, t, tpc, mult] of [['long', 'sl', 3, 4, 0.95], ['long', 'tp', 3, 3, 0.95], ['short', 'sl', 4, 3, 1.05], ['short', 'tp', 4, 4, 1.05]]) {
  const c = cfg(1);
  await mod.placeSlTpOrder({ marketId: 1, side, orderType: kind, triggerPrice: 80000, size: 0.00059, priceDecimals: c.pd, sizeDecimals: c.sd });
  check(`${kind.toUpperCase()} for ${side} BTC`, last(), { t, tpc, lb: 0, fl: 0, lv: 0, s: 59, tp: 80000 * 10 ** c.pd, p: Math.round(80000 * mult * 10 ** c.pd) });
}
// 10-11 close full / partial (IOC, lv 0, ms, mark -/+ slippage)
{ const c = cfg(1);
  await mod.closePosition({ marketId: 1, side: 'long', size: 0.00059, priceDecimals: c.pd, sizeDecimals: c.sd, markPrice: 84000 });
  check('close 100% long BTC', last(), { t: 3, s: 59, fl: 4, lv: 0, ms: c.ms, p: Math.round(84000 * (1 - c.ms / 10000) * 10 ** c.pd) });
  await mod.closePosition({ marketId: 1, side: 'short', size: 0.00059 * 0.5, priceDecimals: c.pd, sizeDecimals: c.sd, markPrice: 84000 });
  check('close 50% short BTC', last(), { t: 4, s: Math.round(0.000295 * 10 ** c.sd), fl: 4, lv: 0, p: Math.round(84000 * (1 + c.ms / 10000) * 10 ** c.pd) }); }
// 12 cancel
await mod.cancelOrder({ marketId: 1, oid: 4242 });
check('cancel resting order', last(), { t: 5, oid: 4242, s: 0, fl: 0, lv: 0 });
// rq strictly increasing across every frame
const rqs = sent.map((f) => f.rq);
results.push([rqs.every((r, i) => i === 0 || r > rqs[i - 1]) ? 'PASS' : 'FAIL', `rq strictly increasing across ${rqs.length} frames`, '']);

// ---- Part C auto-cancel: the venue-frame layer PositionsPanel's OCO / sweep / resize act on ----
{
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const c = cfg(31);
  const tp = await mod.placeSlTpOrder({ marketId: 31, side: 'long', orderType: 'tp', triggerPrice: 130, size: 1, priceDecimals: c.pd, sizeDecimals: c.sd });
  const sl = await mod.placeSlTpOrder({ marketId: 31, side: 'long', orderType: 'sl', triggerPrice: 110, size: 1, priceDecimals: c.pd, sizeDecimals: c.sd });
  await wait(40);
  const armed = mod.getOpenOrders().filter((o) => o.st === 8 && o.mkt === 31).map((o) => o.oid).sort();
  results.push([armed.length === 2 && armed.includes(tp.orderId) && armed.includes(sl.orderId) ? 'PASS' : 'FAIL', 'TP + SL legs tracked as armed (st 8) in the open-orders store', JSON.stringify(armed)]);
  const events = [];
  window.addEventListener('trigger_leg_filled', (e) => events.push(e.detail));
  lastWS._emit({ mt: 24, d: [{ oid: tp.orderId, mkt: 31, st: 10, fs: 1000 }] });          // TP leg executes
  await wait(30);
  results.push([events.length === 1 && events[0].mkt === 31 && events[0].oid === tp.orderId ? 'PASS' : 'FAIL', 'OCO: TP leg executed -> one trigger_leg_filled{mkt 31}', JSON.stringify(events)]);
  const left = mod.getOpenOrders().filter((o) => o.mkt === 31).map((o) => o.oid);
  results.push([left.length === 1 && left[0] === sl.orderId ? 'PASS' : 'FAIL', 'OCO: executed leg removed, sibling SL still armed for the panel to cancel', JSON.stringify(left)]);
  lastWS._emit({ mt: 24, d: [{ oid: 55501, mkt: 1, st: 4, fs: 59 }] });                  // a plain market fill
  await wait(30);
  results.push([events.length === 1 ? 'PASS' : 'FAIL', 'plain market fill does NOT fire the OCO event', String(events.length)]);
  await mod.cancelOrder({ marketId: 31, oid: sl.orderId });
  await wait(30);
  results.push([!mod.getOpenOrders().some((o) => o.oid === sl.orderId) ? 'PASS' : 'FAIL', 'cancel (st 5) removes the sibling from the store', '']);
  lastWS._emit({ mt: 23, d: [{ oid: 70001, mkt: 40, st: 8, t: 3, s: 100, tp: 300000, tpc: 4 }, { oid: 70002, mkt: 40, st: 2, t: 1, s: 100 }] });
  await wait(30);
  const snap = mod.getOpenOrders().map((o) => o.oid).sort();
  results.push([JSON.stringify(snap) === '[70001,70002]' ? 'PASS' : 'FAIL', 'mt:23 snapshot on load replaces the store (orphan sweep input)', JSON.stringify(snap)]);
}

// ---- money-path fix 2026-10-08: configs missing => NOTHING is sent ----
// This was the NO-GO finding of 10-07: with /api/market-configs failed for a
// tab, PositionsPanel passed hard-coded fallbacks (priceDecimals ?? 1,
// sizeDecimals ?? 5) and closing 1,000 MON sent s=100000000 and p=0.
// The decimals now come from the config, and every frame-building call refuses
// when that market's config is absent - proven here by deleting market 10.
{
  const before = sent.length;
  const saved = { ...mod.MARKET_CONFIGS[10] };
  delete mod.MARKET_CONFIGS[10];
  const WANT = 'Market settings not loaded. Refresh and try again.';
  const attempts = [
    ['close 1000 MON', () => mod.closePosition({ marketId: 10, side: 'long', size: 1000, priceDecimals: 1, sizeDecimals: 5, markPrice: 0.0332 })],
    ['open 1000 MON', () => mod.placeOrder({ marketId: 10, side: 'long', size: 1000, leverage: 2, price: 0.0332, priceDecimals: 1, sizeDecimals: 5, orderMode: 'market' })],
    ['arm SL on MON', () => mod.placeSlTpOrder({ marketId: 10, side: 'long', orderType: 'sl', triggerPrice: 0.03, size: 1000, priceDecimals: 1, sizeDecimals: 5 })],
    ['modify MON order', () => mod.modifyOrder({ marketId: 10, oid: 4242, newPrice: 0.03, newSize: 1000, leverage: 2, priceDecimals: 1, sizeDecimals: 5 })],
  ];
  for (const [name, fn] of attempts) {
    let msg = null;
    try { await fn(); } catch (e) { msg = e.message; }
    results.push([msg === WANT ? 'PASS' : 'FAIL', `configs missing: ${name} refused with the UI message`, String(msg)]);
  }
  results.push([sent.length === before ? 'PASS' : 'FAIL',
    'configs missing: not one mt:22 frame reached the venue', `frames ${before} -> ${sent.length}`]);
  mod.MARKET_CONFIGS[10] = saved;
  // same close with the real config is unchanged (no regression in how orders are built)
  await mod.closePosition({ marketId: 10, side: 'long', size: 1000, priceDecimals: saved.priceDecimals, sizeDecimals: saved.sizeDecimals, markPrice: 0.0332 });
  const c = cfg(10);
  check('close 1000 MON with the real config (s in lots, p bounded)', last(),
    { t: 3, s: 1000 * 10 ** c.sd, p: Math.round(0.0332 * (1 - c.ms / 10000) * 10 ** c.pd), fl: 4, lv: 0, ms: c.ms });
}

// ---- 2026-10-08: the SHARED SL/TP path (Terminal AND Copy dashboard) ----
// components/common/SlTpEditor.tsx is used by both screens and both go through
// armTrigger(), so these checks cover the copy-dashboard TP/SL frames too.
// What is proven here: the frame is a native Perpl trigger order; arming a
// second time REPLACES (cancel t:5 of the old oid, then the new trigger)
// instead of adding a second leg; and the retry path (onlyIfMissing) sends
// NOTHING when a leg of that kind is already armed. The live bug on 10-08 was
// one ETH position carrying two stop losses and two take profits.
{
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const c = cfg(20);                      // ETH, the market it happened on
  const base = { marketId: 20, side: 'short', size: 0.007, priceDecimals: c.pd, sizeDecimals: c.sd };
  // clear anything the earlier blocks left in the store for market 20
  lastWS._emit({ mt: 23, d: [] });
  await wait(20);

  const first = await mod.armTrigger({ ...base, kind: 'sl', triggerPrice: 2545 });
  await wait(30);
  check('copy/terminal SL: native Perpl trigger frame (closes a short, GTEMark)', last(),
    { mt: 22, mkt: 20, acc: 777, t: 4, tpc: 3, tp: Math.round(2545 * 10 ** c.pd),
      p: Math.round(2545 * 1.05 * 10 ** c.pd), s: Math.round(0.007 * 10 ** c.sd), fl: 0, lv: 0, lb: 0 });
  results.push([first.replaced === null && first.kept === false ? 'PASS' : 'FAIL',
    'first SL arms with nothing to replace', JSON.stringify(first)]);

  const tp1 = await mod.armTrigger({ ...base, kind: 'tp', triggerPrice: 2528 });
  await wait(30);
  check('copy/terminal TP: native Perpl trigger frame (closes a short, LTEMark)', last(),
    { mt: 22, mkt: 20, t: 4, tpc: 4, tp: Math.round(2528 * 10 ** c.pd),
      p: Math.round(2528 * 1.05 * 10 ** c.pd), s: Math.round(0.007 * 10 ** c.sd), fl: 0, lb: 0 });

  // arm SL again at a different price -> must cancel the armed one first
  const n0 = sent.length;
  const second = await mod.armTrigger({ ...base, kind: 'sl', triggerPrice: 2542 });
  await wait(30);
  const added = sent.slice(n0);
  results.push([added.length === 2 && added[0].t === 5 && added[0].oid === first.orderId
    && added[1].tp === Math.round(2542 * 10 ** c.pd) ? 'PASS' : 'FAIL',
    'arming SL twice REPLACES: cancel(t:5) of the old oid, then the new trigger',
    added.map((f) => `t${f.t}${f.oid ? '#' + f.oid : ''}`).join(' ')]);
  results.push([second.replaced === first.orderId ? 'PASS' : 'FAIL',
    'the replaced oid is reported back to the caller', JSON.stringify(second)]);

  const armedNow = mod.getOpenOrders().filter((o) => o.st === 8 && o.mkt === 20);
  results.push([armedNow.length === 2 ? 'PASS' : 'FAIL',
    'one SL + one TP armed on the position, never two of a kind',
    armedNow.map((o) => `#${o.oid} tpc${o.tpc}`).join(' ')]);

  // the retry path: a leg is already armed -> no frame at all
  const n1 = sent.length;
  const retry = await mod.armTrigger({ ...base, kind: 'sl', triggerPrice: 2542, onlyIfMissing: true });
  results.push([sent.length === n1 && retry.kept === true && retry.orderId === second.orderId ? 'PASS' : 'FAIL',
    'retry with onlyIfMissing sends NO frame and keeps the armed leg',
    `frames ${n1} -> ${sent.length} ${JSON.stringify(retry)}`]);

  // and the size that the venue reports back is kept when a later frame omits it
  lastWS._emit({ mt: 24, d: [{ oid: second.orderId, mkt: 20, st: 8, tp: Math.round(2542 * 10 ** c.pd), tpc: 3, t: 4 }] });
  await wait(20);
  const row = mod.getOpenOrders().find((o) => o.oid === second.orderId);
  results.push([row && row.s === Math.round(0.007 * 10 ** c.sd) ? 'PASS' : 'FAIL',
    'an st:8 update WITHOUT s keeps the size already known (the "size-" bug)',
    JSON.stringify(row)]);
}

// ---- 2026-10-09: `lp` (linked position id) on the trigger frames ----
// Perpl cancels an lp-linked trigger ITSELF when the linked position closes or
// inverts. Proven on the owner's own account, not assumed: trigger oid
// 288791797170211 (lp 7316779368451) went st:8 -> st:5 sr:28 at block
// 111717102, the same block its position closed, with txid all-zeroes - a
// server-side cancel with no transaction of ours. The four ETH triggers that
// outlived their position carry no `lp` at all.
//
// `lp` is taken from the venue's own position frame (mt:26/27 `pid`) and
// OMITTED when no frame has arrived, because a wrong lp would let the venue
// cancel the wrong leg.
{
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const c = cfg(20);
  const base = { marketId: 20, side: 'short', size: 0.007, priceDecimals: c.pd, sizeDecimals: c.sd };

  // (a) no position frame yet -> no lp key in the frame
  lastWS._emit({ mt: 23, d: [] });
  lastWS._emit({ mt: 26, d: [] });
  await wait(20);
  await mod.armTrigger({ ...base, kind: 'sl', triggerPrice: 2545 });
  await wait(30);
  results.push([!('lp' in last()) ? 'PASS' : 'FAIL',
    'no position frame yet: `lp` is OMITTED rather than guessed',
    JSON.stringify({ lp: last().lp })]);

  // (b) the venue reports the position -> lp = its pid, exactly
  const PID = 7316779368451;
  lastWS._emit({ mt: 23, d: [] });
  lastWS._emit({
    mt: 26,
    at: { b: 111717102, t: 1791494835000 },
    d: [{ pid: PID, mkt: 20, acc: 777, sd: 2, st: 1, s: 7, ep: 252991, c: '-1771000', lv: 1000, dpnl: '0', fnd: '0', fee: '0', cfee: '0' }],
  });
  await wait(30);
  results.push([mod.venuePositionId(20, 'short') === PID ? 'PASS' : 'FAIL',
    'mt:26 decoded: sd 2 = short, pid readable for the link',
    String(mod.venuePositionId(20, 'short'))]);
  results.push([mod.getVenuePositions().length === 1
    && Math.abs(mod.getVenuePositions()[0].size - 0.007) < 1e-9
    && Math.abs(mod.getVenuePositions()[0].entryPrice - 2529.91) < 1e-6 ? 'PASS' : 'FAIL',
    'mt:26 scaling: s/10^3 = 0.007 ETH, ep/10^2 = 2529.91',
    JSON.stringify(mod.getVenuePositions()[0])]);

  const sl = await mod.armTrigger({ ...base, kind: 'sl', triggerPrice: 2545 });
  await wait(30);
  check('SL frame carries lp = the venue position id', last(),
    { mt: 22, mkt: 20, t: 4, tpc: 3, fl: 0, lb: 0, lp: PID, tp: Math.round(2545 * 10 ** c.pd) });
  const tp = await mod.armTrigger({ ...base, kind: 'tp', triggerPrice: 2528 });
  await wait(30);
  check('TP frame carries the same lp (both legs linked to one position)', last(),
    { mt: 22, mkt: 20, t: 4, tpc: 4, lp: PID, tp: Math.round(2528 * 10 ** c.pd) });
  results.push([sl.orderId !== tp.orderId ? 'PASS' : 'FAIL',
    'the two linked legs are distinct orders', `${sl.orderId} / ${tp.orderId}`]);

  // (c) the venue says the position closed -> our decoded set empties, so a
  //     later trigger would not be linked to a dead position
  lastWS._emit({ mt: 27, at: { b: 111717102, t: 1791494835000 }, d: [{ pid: PID, mkt: 20, sd: 2, st: 2, s: 0, ep: 252991 }] });
  await wait(30);
  results.push([mod.venuePositionId(20, 'short') === null && mod.getVenuePositions().length === 0 ? 'PASS' : 'FAIL',
    'mt:27 with st:2 removes the position, so no stale lp can be sent',
    JSON.stringify(mod.getVenuePositions())]);

  // (d) mt:25 fill decoding, the frame that paints a position instantly
  lastWS._emit({
    mt: 25,
    d: [{ at: { b: 111717102, t: 1791494835000 }, mkt: 20, acc: 777, oid: 1234, t: 2, l: 2, p: 252991, s: 7, f: '143168', bfa: '0' }],
  });
  await wait(30);
  const f = mod.getVenueFills()[0];
  results.push([f && f.mkt === 20 && f.t === 2 && Math.abs(f.price - 2529.91) < 1e-6
    && Math.abs(f.size - 0.007) < 1e-9 && Math.abs(f.fee - 0.143168) < 1e-9 ? 'PASS' : 'FAIL',
    'mt:25 decoded: p/10^2, s/10^3, f/1e6 = $0.143168',
    JSON.stringify(f)]);

  // (e) the order-size field: the venue sends `os`, we must not read `s`
  lastWS._emit({ mt: 23, d: [] });
  lastWS._emit({ mt: 24, d: [{ oid: 288788852637722, mkt: 20, st: 8, t: 4, os: 7, tp: 254200, tpc: 3, p: 266910 }] });
  await wait(20);
  const orow = mod.getOpenOrders().find((o) => o.oid === 288788852637722);
  results.push([orow && orow.s === 7 ? 'PASS' : 'FAIL',
    'an order row carrying only `os` (the venue own field name) yields a size, not NaN',
    JSON.stringify(orow)]);
}

// ---- 2026-10-12: rq above the venue's lfr, ack matching, resend safety ----
{
  const c = cfg(1);
  const order = { marketId: 1, side: 'long', size: 0.00029, leverage: 5, price: 84000, sizeDecimals: c.sd, priceDecimals: c.pd, orderMode: 'market' };
  // The owner's real snapshot: auto-copy left the venue's lfr in microseconds.
  const LFR = 1791642760804238;
  lastWS._emit({ mt: 19, addr: '0x8b66', as: [{ mt: 0, in: 1, id: 777, lfr: LFR, b: '61419199', lb: '0' }], sts: [] });
  await new Promise((r) => setTimeout(r, 20));
  let ackAt = 0;
  const emit0 = lastWS._emit.bind(lastWS);
  lastWS._emit = (o) => { if (o.mt === 24 && o.d?.[0]?.rq === LFR + 1) ackAt = performance.now(); emit0(o); };
  const r1 = await mod.placeOrder(order);
  const dt = performance.now() - ackAt;
  lastWS._emit = emit0;
  results.push([last().rq === LFR + 1 ? 'PASS' : 'FAIL', 'first order after the snapshot uses rq = venue lfr + 1 (strictly increasing)', `rq ${last().rq}`]);
  results.push([r1?.filled === true && ackAt > 0 && dt < 100 ? 'PASS' : 'FAIL', 'the ack for the sent rq resolves the order in under 100 ms', `${dt.toFixed(1)} ms, filled=${r1?.filled}`]);

  // Missed ack: the venue gets the frame but its reply is lost. The client must
  // re-send the SAME rq (Perpl executes a given rq at most once), never a new one.
  const send0 = lastWS.send.bind(lastWS);
  const got = [];
  lastWS.send = (raw) => {
    const f = JSON.parse(raw);
    if (f.mt !== 22) return send0(raw);
    sent.push(f); got.push(f.rq);
    if (got.length === 1) return;                       // reply lost
    setTimeout(() => lastWS._emit({ mt: 24, d: [{ rq: f.rq, oid: 777001, mkt: f.mkt, s: f.s, fs: f.s, fp: f.p, st: 4 }] }), 10);
  };
  const t0 = performance.now();
  const r2 = await mod.placeOrder(order);
  const took = (performance.now() - t0) / 1000;
  lastWS.send = send0;
  results.push([got.length === 2 && got[0] === got[1] && new Set(got).size === 1 ? 'PASS' : 'FAIL',
    'missed ack: one re-send, with the SAME rq, so the venue can only execute it once', `frames ${got.length}, rqs ${[...new Set(got)].join(',')}`]);
  results.push([r2?.filled === true && r2.orderId === 777001 ? 'PASS' : 'FAIL', 'missed ack: the re-send is confirmed as filled, not reported as "No confirmation"', `after ${took.toFixed(1)} s`]);
  const r3 = await mod.placeOrder(order);
  results.push([last().rq === got[0] + 1 && r3?.filled ? 'PASS' : 'FAIL', 'the next order still goes out with rq + 1', `rq ${last().rq}`]);
}

// ---- 2026-10-12: cancelling an armed SL / TP (trigger order) ----
{
  // The owner's real armed stop-loss row, as the venue sent it.
  const TRIG = { oid: 288860974940170, mkt: 1, t: 3, st: 8, sr: 35, p: 793250, fs: 0, fp: 0, tp: 835000, tpc: 4, lv: 0, fl: 0, rq: 1791642760804255, lp: 7385995804673, os: 5 };
  lastWS._emit({ mt: 24, d: [TRIG] });
  await new Promise((r) => setTimeout(r, 20));
  const send0 = lastWS.send.bind(lastWS);
  let reply = 'reject';
  lastWS.send = (raw) => {
    const f = JSON.parse(raw);
    if (f.mt !== 22) return send0(raw);
    sent.push(f);
    // Perpl's real answers: a bare cancel -> oid 0, st 7, sr 33; a good one -> the row, st 5.
    const row = reply === 'reject'
      ? { oid: 0, mkt: f.mkt, t: 5, st: 7, sr: 33, fs: 0, fp: 0, lv: 0, fl: 0, rq: f.rq, r: true }
      : { ...TRIG, st: 5, sr: 28, rq: f.rq, r: true };
    setTimeout(() => lastWS._emit({ mt: 24, d: [row] }), 10);
  };
  let t0 = performance.now(); let err = null;
  try { await mod.cancelOrder({ marketId: 1, oid: TRIG.oid }); } catch (e) { err = e; }
  const dtReject = performance.now() - t0;
  check('cancel of an armed SL carries its trigger fields, like Perpl own app (tp, tpc, lp, lb 0)', last(),
    { mt: 22, t: 5, oid: TRIG.oid, s: 0, fl: 0, lv: 0, tp: 835000, tpc: 4, lp: 7385995804673, lb: 0 });
  results.push([err && /no longer exists/.test(err.message) && dtReject < 1500 ? 'PASS' : 'FAIL',
    'a rejected cancel (venue row has oid 0) is reported at once, not after a 25 s wait', `${dtReject.toFixed(0)} ms: ${err?.message}`]);
  reply = 'ok';
  lastWS._emit({ mt: 24, d: [TRIG] });
  await new Promise((r) => setTimeout(r, 20));
  t0 = performance.now();
  const ok = await mod.cancelOrder({ marketId: 1, oid: TRIG.oid }).catch((e) => ({ error: e.message }));
  results.push([ok?.canceled === true && ok.orderId === TRIG.oid ? 'PASS' : 'FAIL',
    'an accepted cancel (st 5) resolves as cancelled', `${(performance.now() - t0).toFixed(0)} ms ${JSON.stringify(ok)}`]);
  lastWS.send = send0;
}

console.log = origLog;
for (const [r, n, d] of results) console.log(`${r.padEnd(7)} ${n}${d ? '  [' + d + ']' : ''}`);
const fails = results.filter((r) => r[0] === 'FAIL').length;
console.log(`\nframes captured: ${sent.length}  checks: ${results.length}  FAIL: ${fails}`);
process.exit(0);
