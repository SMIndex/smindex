// node --test frontend/tests/sessionRefresh.test.mts
// Simulates the server answering /api/auth/refresh with 429 and 401.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createSessionRefresher, REFRESH_BACKOFF_MS } from '../src/lib/sessionRefresh.ts';

const refused = (status: number) => Object.assign(new Error(`HTTP ${status}`), { response: { status } });

function harness(answers: Array<number | 'net'>) {
  const calls: number[] = [];
  const waits: number[] = [];
  const queue: Array<() => void> = [];
  let clock = 0;
  let expired = 0;
  let refreshed = 0;
  const r = createSessionRefresher({
    post: async () => {
      calls.push(clock);
      const a = answers.shift() ?? 200;
      if (a === 200) return { data: { refreshed: true, token: 't2' } };
      if (a === 'net') throw new Error('Network Error');
      throw refused(a);
    },
    onRefreshed: () => { refreshed++; },
    onExpired: () => { expired++; },
    schedule: (fn, ms) => { waits.push(ms); queue.push(() => { clock += ms; fn(); }); },
  });
  const settle = () => new Promise((res) => setTimeout(res, 0));
  const drain = async () => {
    await settle();
    while (queue.length) { queue.shift()!(); await settle(); }
  };
  return { r, calls, waits, drain, get expired() { return expired; }, get refreshed() { return refreshed; } };
}

test('429 every time: 1 try, then 30 s, 2 min, 10 min, then stops for good', async () => {
  const h = harness([429, 429, 429, 429, 429, 429, 429, 429]);
  h.r.start();
  await h.drain();
  assert.deepEqual(h.waits, REFRESH_BACKOFF_MS);
  assert.deepEqual(h.waits, [30_000, 120_000, 600_000]);
  assert.equal(h.calls.length, 4);
  assert.deepEqual(h.calls, [0, 30_000, 150_000, 750_000]);
  assert.equal(h.expired, 0);
});

test('401: one try, no retry, exactly one expired prompt', async () => {
  const h = harness([401, 401, 401]);
  h.r.start();
  await h.drain();
  assert.equal(h.calls.length, 1);
  assert.equal(h.waits.length, 0);
  assert.equal(h.expired, 1);
});

test('429 then 401: backs off once, then stops on the 401 with one prompt', async () => {
  const h = harness([429, 401]);
  h.r.start();
  await h.drain();
  assert.equal(h.calls.length, 2);
  assert.deepEqual(h.waits, [30_000]);
  assert.equal(h.expired, 1);
});

test('429 then success: stops after the success', async () => {
  const h = harness([429, 200]);
  h.r.start();
  await h.drain();
  assert.equal(h.calls.length, 2);
  assert.equal(h.refreshed, 1);
});

test('network down: same cap as 429', async () => {
  const h = harness(['net', 'net', 'net', 'net', 'net']);
  h.r.start();
  await h.drain();
  assert.equal(h.calls.length, 4);
});

test('start() called by 40 components: still one request', async () => {
  const h = harness([200]);
  for (let i = 0; i < 40; i++) h.r.start();
  await h.drain();
  assert.equal(h.calls.length, 1);
});
