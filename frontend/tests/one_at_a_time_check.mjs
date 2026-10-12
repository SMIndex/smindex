// The one-click lock used by every order button. Run: node tests/one_at_a_time_check.mjs
import { build } from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = await build({ entryPoints: [path.join(root, 'src/lib/oneAtATime.ts')], bundle: true, write: false, format: 'esm', logLevel: 'silent' });
const { oneAtATime } = await import('data:text/javascript;base64,' + Buffer.from(out.outputFiles[0].text).toString('base64'));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, d = '') => { if (!ok) fails++; console.log(`${ok ? 'PASS' : 'FAIL'}    ${name}${d ? '  [' + d + ']' : ''}`); };

let runs = 0;
const slow = async () => { runs++; await wait(80); return 'done'; };       // a click that is still reconnecting
const a = oneAtATime('close:1', slow);
const b = oneAtATime('close:1', slow);                                       // the second click
const c = oneAtATime('close:20', slow);                                      // another market is independent
check('a second click while the first is running does nothing', (await b) === undefined && runs === 2, `handler ran ${runs - 1} time for market 1`);
check('the first click completes normally', (await a) === 'done');
check('a different position is not blocked', (await c) === 'done');
check('after it finishes the button works again', (await oneAtATime('close:1', slow)) === 'done' && runs === 3);
let threw = false;
try { await oneAtATime('close:1', async () => { throw new Error('venue rejected'); }); } catch { threw = true; }
check('an error is passed on and releases the lock', threw && (await oneAtATime('close:1', slow)) === 'done');
const five = await Promise.all([1, 2, 3, 4, 5].map(() => oneAtATime('close-all', slow)));
check('five rapid clicks = one action', five.filter((x) => x === 'done').length === 1, JSON.stringify(five));
console.log(`\nchecks: 6  FAIL: ${fails}`);
process.exit(fails ? 1 : 0);
