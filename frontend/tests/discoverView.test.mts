// node --test frontend/tests/discoverView.test.mts
// Runs on the live trader list when TRADERS_JSON points at a saved /api/traders answer.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DEFAULT_VIEW, applyView, clickSort, lastActiveIso, parseView, writeView, SMALL_PROFIT_USD } from '../src/lib/discoverView.ts';

const rows = JSON.parse(readFileSync(process.env.TRADERS_JSON!, 'utf8'));
const v = (o: object) => ({ ...DEFAULT_VIEW, ...o });

test('default is profit high to low, nothing hidden', () => {
  const r = applyView(rows, DEFAULT_VIEW);
  assert.equal(r.rows.length, rows.length);
  for (let i = 1; i < r.rows.length; i++) assert.ok(r.rows[i - 1].pnl_total >= r.rows[i].pnl_total);
});

test('header click: new column high to low, same column flips', () => {
  const a = clickSort(DEFAULT_VIEW, 'volume');
  assert.deepEqual([a.sort, a.dir], ['volume', 'desc']);
  const b = clickSort(a, 'volume');
  assert.deepEqual([b.sort, b.dir], ['volume', 'asc']);
  const r = applyView(rows, b).rows;
  for (let i = 1; i < r.length; i++) assert.ok(r[i - 1].volume <= r[i].volume);
});

test('return sort hides small accounts unless included', () => {
  const small = rows.filter((t: any) => t.pnl_total < SMALL_PROFIT_USD).length;
  const r = applyView(rows, v({ sort: 'return' }));
  assert.equal(r.smallHidden, small);
  assert.equal(r.rows.length, rows.length - small);
  for (let i = 1; i < r.rows.length; i++) assert.ok(r.rows[i - 1].roi >= r.rows[i].roi);
  assert.equal(applyView(rows, v({ sort: 'return', small: true })).rows.length, rows.length);
});

test('filters match a plain count on the same rows', () => {
  const now = Date.now();
  const r = applyView(rows, v({ minReturn: 50, minProfit: 10_000, minVolume: 1_000_000 }), now).rows;
  assert.equal(r.length, rows.filter((t: any) => t.roi >= 50 && t.pnl_total >= 10_000 && t.volume >= 1_000_000).length);
  const open = applyView(rows, v({ open: true })).rows;
  assert.equal(open.length, rows.filter((t: any) => t.has_active_positions).length);
  const act = applyView(rows, v({ active: '7d' }), now).rows;
  for (const t of act) {
    const iso = t.activity?.last_active_at || t.last_fill_at;
    assert.ok(now - Date.parse(iso.endsWith('Z') ? iso : iso + 'Z') <= 7 * 86_400_000);
  }
  console.log(`  rows ${rows.length}, open ${open.length}, active 7d ${act.length}, return>=50 profit>=10K vol>=1M ${r.length}`);
});

test('URL round trip keeps other params and drops bad values', () => {
  const p = writeView(new URLSearchParams('wallets=0xabc'), v({ sort: 'return', minReturn: 50, open: true }));
  assert.equal(p.toString(), 'wallets=0xabc&sort=return&minReturn=50&open=1');
  assert.deepEqual(parseView(p), v({ sort: 'return', minReturn: 50, open: true }));
  assert.deepEqual(parseView(new URLSearchParams('sort=bogus&minReturn=33&active=9d')), DEFAULT_VIEW);
  assert.equal(writeView(new URLSearchParams(), DEFAULT_VIEW).toString(), '');
});

test('last active: newer fill wins, day-precise snapshot gives way to a fill that day', () => {
  const r = (la: string | null, lf: string | null) => lastActiveIso({ rank: 1, pnl_total: 0, roi: 0, volume: 0, activity: { last_active_at: la }, last_fill_at: lf });
  assert.equal(r('2026-10-21T00:34:11', '2026-10-21T08:44:41'), '2026-10-21T08:44:41');
  assert.equal(r('2026-09-30T23:59:59', '2026-09-30T03:55:58'), '2026-09-30T03:55:58');
  assert.equal(r('2026-10-21T04:07:06', '2026-10-13T02:27:44'), '2026-10-21T04:07:06');
  assert.equal(r(null, '2026-09-01T03:09:00'), '2026-09-01T03:09:00');
  assert.equal(r('2026-10-20T15:53:55', null), '2026-10-20T15:53:55');
});
