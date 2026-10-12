// One action at a time per key (2026-10-12).
//
// Every order button used to set its "busy" flag only AFTER the reconnect wait
// and the confirm sheet. A second click during a slow reconnect therefore ran
// the whole handler again: one SL/TP edit placed two stop losses and two take
// profits on Perpl. The lock is taken synchronously on the first click and is
// module-wide, so the desktop panel and the (mounted but hidden) mobile cards
// cannot both act on the same position either.
const held = new Set<string>();

export async function oneAtATime<T>(key: string, fn: () => Promise<T>): Promise<T | undefined> {
  if (held.has(key)) return undefined;
  held.add(key);
  try {
    return await fn();
  } finally {
    held.delete(key);
  }
}
