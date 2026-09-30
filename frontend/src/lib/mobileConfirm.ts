// Mobile money-action confirmation (spec 4.4). Every order, close and cancel on
// a phone passes through one bottom sheet showing side / size / price /
// leverage / fees, then shows the lifecycle (submitting -> confirmed | failed)
// in the same sheet. On desktop (>820px) it resolves immediately — desktop
// behaviour is unchanged. It only gates the EXISTING calls; nothing about
// order construction changes.

export type ConfirmRow = [label: string, value: string];
export interface ConfirmRequest { title: string; rows: ConfirmRow[]; danger?: boolean; confirmLabel?: string }
export interface ConfirmHandle { ok: boolean; done: (msg?: string) => void; fail: (msg: string) => void }

type State =
  | { phase: 'idle' }
  | { phase: 'ask' | 'submitting' | 'confirmed' | 'failed'; req: ConfirmRequest; msg?: string };

let state: State = { phase: 'idle' };
let answer: ((ok: boolean) => void) | null = null;
const subs = new Set<(s: State) => void>();
const emit = () => subs.forEach((f) => f(state));

export const MOBILE_BREAKPOINT = 820;
export const isMobileViewport = () =>
  typeof window !== 'undefined' && window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`).matches;

export function subscribeConfirm(fn: (s: State) => void): () => void {
  subs.add(fn);
  fn(state);
  return () => { subs.delete(fn); };
}

export function answerConfirm(ok: boolean): void {
  const a = answer;
  answer = null;
  if (!ok) { state = { phase: 'idle' }; emit(); }
  else if (state.phase === 'ask') { state = { ...state, phase: 'submitting' }; emit(); }
  a?.(ok);
}

export function closeConfirm(): void {
  if (state.phase === 'submitting') return;      // never hide an order in flight
  state = { phase: 'idle' };
  emit();
}

const NOOP: ConfirmHandle = { ok: true, done: () => {}, fail: () => {} };

export async function mobileConfirm(req: ConfirmRequest): Promise<ConfirmHandle> {
  if (!isMobileViewport()) return NOOP;
  if (answer) answer(false);                      // one sheet at a time
  state = { phase: 'ask', req };
  emit();
  const ok = await new Promise<boolean>((res) => { answer = res; });
  if (!ok) return { ok: false, done: () => {}, fail: () => {} };
  return {
    ok: true,
    done: (msg) => { state = { phase: 'confirmed', req, msg }; emit(); setTimeout(closeConfirm, 1800); },
    fail: (msg) => { state = { phase: 'failed', req, msg }; emit(); },
  };
}
