// node --test frontend/tests/featuredView.test.mts
import test from 'node:test';
import assert from 'node:assert/strict';
import { FT_DEFAULT, clickFtSort, ftFiltersActive, ftQuery, hasFtParams, parseFt, writeFt, holdWords, agoWords, usd, coinsOnPerpl } from '../src/lib/featuredView.ts';

test('defaults round-trip to an empty URL', () => {
  const p = writeFt(new URLSearchParams(), FT_DEFAULT);
  assert.equal(p.toString(), '');
  assert.equal(hasFtParams(p), false);
  assert.deepEqual(parseFt(p), FT_DEFAULT);
});

test('every filter is written and read back', () => {
  const v = { ...FT_DEFAULT, venue: 'hl' as const, label: 'asymmetric' as const, minWinRate: 70, minPf: 2, minTrades: 50, minProfit: 5000, maxDd: 20,
    size: '10k-100k' as const, hold: 'swing' as const, coins: ['BTC', 'ETH'], perplOnly: true, active: 'any' as const, maxLev: 10, hideSmall: false,
    sort: 'net_pnl_90d' as const, dir: 'asc' as const };
  const p = writeFt(new URLSearchParams('wallets=0x1'), v);
  assert.equal(p.get('wallets'), '0x1');
  assert.deepEqual(parseFt(p), v);
  assert.equal(ftFiltersActive(v), 14);
});

test('unknown values fall back to defaults', () => {
  const v = parseFt(new URLSearchParams('venue=binance&minWinRate=55&sort=foo&active=99d&coins=btc,$$$'));
  assert.equal(v.venue, 'all');
  assert.equal(v.minWinRate, 0);
  assert.equal(v.sort, 'score');
  assert.equal(v.active, '14d');
  assert.deepEqual(v.coins, ['BTC']);
});

test('API query shape', () => {
  const q = ftQuery({ ...FT_DEFAULT, coins: ['SOL'], perplOnly: true });
  assert.equal(q.label, 'consistent,asymmetric');
  assert.equal(q.coins, 'SOL');
  assert.equal(q.perplOnly, 1);
  assert.equal(q.hideSmall, 1);
  assert.equal(q.active, '14d');
});

test('header click: new column high to low, drawdown low to high, same column flips', () => {
  const a = clickFtSort(FT_DEFAULT, 'win_rate_90d');
  assert.deepEqual([a.sort, a.dir], ['win_rate_90d', 'desc']);
  const b = clickFtSort(a, 'win_rate_90d');
  assert.equal(b.dir, 'asc');
  assert.equal(clickFtSort(FT_DEFAULT, 'max_drawdown_90d').dir, 'asc');
});

test('plain words', () => {
  assert.equal(holdWords(6 * 3600 + 36 * 60), '6 h 36 m');
  assert.equal(holdWords(41 * 60), '41 m');
  assert.equal(holdWords(2 * 86400 + 6 * 3600), '2 d 6 h');
  assert.equal(agoWords('2026-10-29T10:00:00', Date.parse('2026-10-29T10:12:00Z')), '12 m ago');
  assert.equal(usd(41280, true), '+$41,280');
  assert.equal(usd(-140, true), '-$140');
  assert.equal(usd(1_840_000, false, true), '$1.8M');
  assert.equal(coinsOnPerpl(['BTC', 'ETH', 'SOL'], 3), 'all on Perpl');
  assert.equal(coinsOnPerpl(['HYPE', 'SOL', 'ZEC'], 2), '2 of 3 on Perpl');
});
