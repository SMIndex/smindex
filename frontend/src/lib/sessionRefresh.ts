// Session refresh with a hard cap (2026-10-21). One try per page load; if it is
// refused (429, 5xx, network) it waits 30 s, 2 min, 10 min between the next
// tries and then stops: the token keeps working until it expires anyway. A 401
// means the session is over: no retry, one "Sign in again" prompt.
// No imports, so the test can run it with plain node.

export const REFRESH_BACKOFF_MS = [30_000, 120_000, 600_000];

type Schedule = (fn: () => void, ms: number) => unknown;

export interface RefresherOptions {
  post: () => Promise<any>;
  onRefreshed: (data: any) => void;
  onExpired: () => void;
  schedule?: Schedule;
}

export function createSessionRefresher({ post, onRefreshed, onExpired, schedule = setTimeout }: RefresherOptions) {
  let started = false;
  let done = false;
  let attempt = 0;

  async function run(): Promise<void> {
    if (done) return;
    try {
      const r = await post();
      done = true;
      onRefreshed(r?.data);
    } catch (e: any) {
      if (e?.response?.status === 401) {
        done = true;
        onExpired();
        return;
      }
      if (attempt >= REFRESH_BACKOFF_MS.length) {
        done = true;
        return;
      }
      schedule(() => { void run(); }, REFRESH_BACKOFF_MS[attempt++]);
    }
  }

  return {
    start(): void {
      if (started) return;
      started = true;
      void run();
    },
  };
}
