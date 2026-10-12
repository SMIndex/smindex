import api from '@/lib/api';

// Auto-copy API (overnight Part C). Server side: /api/copy-auto/*.

export type AutoCopySettings = {
  sizing: 'fixed' | 'proportional';
  margin_usd: number;
  allocation_usd: number;
  max_leverage: number;
  max_positions: number;
  mirror_adds: boolean;
  mirror_reduces: boolean;
  reopen_on_flip: boolean;
  drift_pct: number;
  sl_margin_pct: number | null;
  tp_pct: number | null;
  daily_loss_usd: number | null;
  total_loss_usd: number | null;
  markets: number[] | null;
};

export type AutoCopySub = AutoCopySettings & {
  id: number;
  leader_wallet: string;
  mode: 'shadow' | 'live';
  status: 'active' | 'paused' | 'stopped';
  pause_reason: string | null;
  summary: string;
  pnl_today: number;
  pnl_total: number;
  open_positions: number;
  last_action: { decision: string; event: string; coin: string; reason: string; at: string } | null;
};

export type KeyStatus = { present: boolean; public_key?: string; scope?: number; label?: string;
  created_at?: string | null; last_used_at?: string | null; last_error?: string | null };

export const getAvailability = (leader: string) =>
  api.get(`/api/copy-auto/availability/${leader}`).then((r) => r.data as {
    available: boolean; reason: string; allowlisted: boolean; key: KeyStatus; live_switch_on: boolean });
export const getPresets = () => api.get('/api/copy-auto/presets').then((r) => r.data as {
  presets: Record<string, Partial<AutoCopySettings>>; defaults: AutoCopySettings;
  ranges: Record<string, [number, number]>; summaries: Record<string, string> });
export const listAutoSubs = () => api.get('/api/copy-auto/subs').then((r) => r.data.subs as AutoCopySub[]);
export const createAutoSub = (leader_wallet: string, preset: string | null, settings: Partial<AutoCopySettings>, mode?: 'shadow' | 'live') =>
  api.post('/api/copy-auto/subs', { leader_wallet, preset, settings, mode }).then((r) => r.data as AutoCopySub);

export type AutoCopyDefaults = Omit<AutoCopySettings, 'markets'> & { mode: 'shadow' | 'live' };
export type AutoCopyUserSettings = {
  enabled: boolean; defaults: AutoCopyDefaults; updated_at: string | null;
  platform_live_on: boolean; platform_paused: boolean; allowlisted: boolean; key: KeyStatus;
  presets: Record<string, Partial<AutoCopySettings>>; ranges: Record<string, [number, number]>;
};
export const getAutoSettings = () => api.get('/api/copy-auto/settings').then((r) => r.data as AutoCopyUserSettings);
export const putAutoSettings = (body: { enabled?: boolean; defaults?: Partial<AutoCopyDefaults> }) =>
  api.put('/api/copy-auto/settings', body).then((r) => r.data as AutoCopyUserSettings);
export const updateAutoSub = (id: number, body: { settings?: Partial<AutoCopySettings>; mode?: string; status?: string }) =>
  api.patch(`/api/copy-auto/subs/${id}`, body).then((r) => r.data as AutoCopySub);
export const stopAutoSub = (id: number) => api.delete(`/api/copy-auto/subs/${id}`).then((r) => r.data);
export const pauseAllAuto = () => api.post('/api/copy-auto/pause-all').then((r) => r.data as { paused: number });
export const getAutoLog = (limit = 100) => api.get(`/api/copy-auto/log?limit=${limit}`).then((r) => r.data.rows as any[]);
export const getAutoPositions = () => api.get('/api/copy-auto/positions').then((r) => r.data.positions as any[]);
export const getKeyStatus = () => api.get('/api/copy-auto/key').then((r) => r.data as KeyStatus);
export const depositKey = (body: { api_key: string; seed_hex: string; pub_hex: string; scope: number; label: string }) =>
  api.post('/api/copy-auto/key', { ...body, consent: true }).then((r) => r.data as KeyStatus);
export const deleteKey = () => api.delete('/api/copy-auto/key').then((r) => r.data);

/** Same sentence as the server's rules.summary_sentence (spec 2.11). */
export function summarySentence(s: AutoCopySettings): string {
  const size = s.sizing === 'fixed' ? `$${s.margin_usd}` : `a share of $${s.allocation_usd}`;
  const sl = s.sl_margin_pct ? `, stop at ${s.sl_margin_pct}% of margin` : ', no stop loss';
  return `Copies opens with ${size} at up to ${s.max_leverage}x, max ${s.max_positions} positions${sl}, stops for the day after -$${s.daily_loss_usd ?? 0}.`;
}
