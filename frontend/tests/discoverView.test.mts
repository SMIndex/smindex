// node --test frontend/tests/discoverView.test.mts
// Runs on the live trader list when TRADERS_JSON points at a saved /api/traders answer.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DEFAULT_VIEW, applyView, clickSort, parseView, writeView, SMALL_PROFIT_USD } from '../src/lib/discoverView.ts';

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
  assert.equal(open.length, rows.filter((t: any) => t.has_active_positions || t.active_positions_pending).length);
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
