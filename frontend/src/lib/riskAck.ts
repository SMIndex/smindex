import api from '@/lib/api';

// One-time risk acknowledgement (beta, 2026-10-07). Any action that can put real
// money at risk (placing an order, confirming a copy trade, going Live with
// auto-copy, enabling one-click trading) calls ensureRiskAck() first. The answer
// is stored per user on the server (GET/POST /api/me/risk-ack) and cached in
// this browser. Closing a position or cancelling an order never asks: those
// only reduce risk. It gates the existing calls; order construction is unchanged.

export const RISK_ACK_VERSION = '2026-10-beta-1';
const LS_KEY = 'smindex_risk_ack';

type State = { open: boolean };
let state: State = { open: false };
let answer: ((ok: boolean) => void) | null = null;
const subs = new Set<(s: State) => void>();
const emit = () => subs.forEach((f) => f(state));

export function subscribeRiskAck(fn: (s: State) => void): () => void {
  subs.add(fn);
  fn(state);
  return () => { subs.delete(fn); };
}

function cached(): boolean {
  try { return localStorage.getItem(LS_KEY) === RISK_ACK_VERSION; } catch { return false; }
}

export async function acceptRiskAck(): Promise<void> {
  await api.post('/api/me/risk-ack', { version: RISK_ACK_VERSION });
  try { localStorage.setItem(LS_KEY, RISK_ACK_VERSION); } catch { /* private mode */ }
  state = { open: false };
  emit();
  answer?.(true);
  answer = null;
}

export function declineRiskAck(): void {
  state = { open: false };
  emit();
  answer?.(false);
  answer = null;
}

/** Resolves true once the user has acknowledged the current risk notice. */
export async function ensureRiskAck(): Promise<boolean> {
  if (cached()) return true;
  try {
    const r = await api.get('/api/me/risk-ack');
    if (r.data?.acked) {
      try { localStorage.setItem(LS_KEY, RISK_ACK_VERSION); } catch { /* private mode */ }
      return true;
    }
  } catch {
    // not signed in or API down: ask anyway; accepting needs the API and says so
  }
  if (answer) answer(false);
  state = { open: true };
  emit();
  return new Promise<boolean>((res) => { answer = res; });
}
