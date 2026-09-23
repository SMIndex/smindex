import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
import { getAlertPrefs, putAlertPrefs } from '@/lib/strategiesApi';
import type { AlertPref } from '@/types/strategies';
import { useAuth } from '@/hooks/useAuth';
import { useAuthStore } from '@/stores/authStore';
import { shortenAddress } from '@/lib/formatters';

// Design B Settings (spec §3.6) — presentation only. Reuses the EXACT existing
// Telegram data layer: same react-query key ['telegram-status'], same endpoints
// (GET /api/telegram/status, POST /api/telegram/generate-link-code,
// DELETE /api/telegram/unlink), same link/poll/unlink state machine as the A
// TelegramCard. No new endpoint is called.
//
// The prototype's 4 notification switches + "Send test message" have NO backing
// data layer (verified: telegram router exposes only link/status/unlink). Per
// spec §3.6 ("if a preference does not exist yet, hide that row") and wrapper
// Rule 7 (no backend) + Rule 4 (no false affordances), those rows are HIDDEN.
// Documented in DESIGN_B_FUNCTIONAL_INVENTORY.md + DESIGN_B_REPORT.md.
//
// Model alerts (doc 17 step 6): per-model Telegram switches for M1–M6 backed by
// GET/PUT /api/strategies/alerts/prefs (strat_alert_prefs; absent row = on).
// Every model message carries its [Mx] id; unticking a model mutes only that id.

const MODEL_NAMES: Record<string, string> = {
  M1: 'Sweep and reclaim', M2: 'Break of structure + order block', M3: 'Failed auction at range extreme',
  M4: 'HTF change of character', M5: 'Session liquidity run', M6: 'Weekly open reclaim',
};

function ModelAlertsCardB({ linked }: { linked: boolean }) {
  const qc = useQueryClient();
  const prefs = useQuery({ queryKey: ['strategy-alert-prefs'], queryFn: getAlertPrefs });
  const save = useMutation({
    mutationFn: (next: AlertPref[]) => putAlertPrefs(next),
    onSuccess: (data) => qc.setQueryData(['strategy-alert-prefs'], data),
  });
  const rows = prefs.data ?? [];
  const toggle = (model: string) => {
    if (save.isPending) return;
    save.mutate(rows.map((p) => (p.model === model ? { ...p, enabled: !p.enabled } : p)));
  };
  return (
    <div className="card" style={{ maxWidth: 720, marginTop: 12 }}>
      <h2>Model alerts</h2>
      <div className="sub">
        Paper models M1–M6 send a Telegram message on every setup, fill, partial and exit, each tagged with its model id. Untick a model to mute it{linked ? '' : ' — messages deliver once Telegram is linked above'}.
      </div>
      {prefs.isError && <div className="banner short" style={{ marginTop: 8 }}>Alert preferences unavailable — retrying.</div>}
      {save.isError && <div className="banner short" style={{ marginTop: 8 }}>Saving failed. Try again.</div>}
      {prefs.isLoading && !prefs.isError && <div className="dim" style={{ marginTop: 10, fontSize: 12.5 }}>Loading…</div>}
      {rows.map((p) => (
        <label key={p.model} className="kv" style={{ cursor: save.isPending ? 'progress' : 'pointer' }}>
          <span><span className="mono" style={{ marginRight: 8 }}>{p.model}</span>{MODEL_NAMES[p.model] ?? ''}</span>
          <input type="checkbox" checked={p.enabled} disabled={save.isPending} onChange={() => toggle(p.model)} aria-label={`Telegram alerts for ${p.model}`} />
        </label>
      ))}
      {rows.length > 0 && (
        <p className="dim" style={{ fontSize: 12.5, marginTop: 10 }}>
          {rows.filter((p) => p.enabled).length} of {rows.length} models alerting · saved per wallet{save.isPending ? ' · saving…' : ''}
        </p>
      )}
    </div>
  );
}

function TelegramCardB() {
  const [linkUrl, setLinkUrl] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);

  const { data: tgStatus, refetch: refetchStatus } = useQuery({
    queryKey: ['telegram-status'],
    queryFn: () => api.get('/api/telegram/status').then((r) => r.data),
    refetchInterval: linkUrl ? 3000 : false,
  });

  useEffect(() => {
    if (tgStatus?.linked && linkUrl) setLinkUrl('');
  }, [tgStatus?.linked, linkUrl]);

  const handleGenerateLink = async () => {
    setIsGenerating(true);
    try {
      const data = await api.post('/api/telegram/generate-link-code').then((r) => r.data);
      setLinkUrl(data.bot_url);
    } catch (err: any) {
      if (err?.response?.data?.detail === 'Telegram already linked. Unlink first.') {
        refetchStatus();
      }
    } finally {
      setIsGenerating(false);
    }
  };

  const handleUnlink = async () => {
    await api.delete('/api/telegram/unlink');
    setLinkUrl('');
    refetchStatus();
  };

  const linked = !!tgStatus?.linked;

  return (
    <>
    <div className="card" style={{ maxWidth: 720 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
        <div className="tgicon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 4L3 11l6 2 2 6 3-4 5 3z" /></svg>
        </div>
        <div style={{ flex: 1 }}>
          <h2>Telegram alerts</h2>
          <div className="sub">
            Get a message when a trader you follow opens or closes a trade. Reply to the message to place the same order on Perpl, you confirm each one.
          </div>

          {/* Status — from the same GET /api/telegram/status */}
          <div className="kv" style={{ borderTop: 0, paddingTop: 12 }}>
            <span>Status</span>
            {linked ? (
              <span className="tag long">Linked{tgStatus.chat_id_masked ? ` · ${tgStatus.chat_id_masked}` : ''}</span>
            ) : (
              <span className="tag flat">Not linked</span>
            )}
          </div>

          {/* Pending link URL (same flow as A) */}
          {!linked && linkUrl && (
            <div className="kv">
              <span className="dim" style={{ fontSize: 12.5 }}>Open this link, then come back</span>
              <a className="mono" href={linkUrl} target="_blank" rel="noopener noreferrer"
                 style={{ color: 'var(--accent-text)', wordBreak: 'break-all', maxWidth: 340, textAlign: 'right' }}>
                {linkUrl}
              </a>
            </div>
          )}

          {/* Actions — same handlers/endpoints as A */}
          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            {linked ? (
              <button className="btn" onClick={handleUnlink}>Unlink Telegram</button>
            ) : (
              <button className="btn primary" onClick={handleGenerateLink} disabled={isGenerating}>
                {isGenerating ? 'Generating…' : 'Link Telegram'}
              </button>
            )}
          </div>

          <p className="dim" style={{ fontSize: 12.5, marginTop: 10 }}>
            Linking opens Telegram with a one-time code. Alerts stop the moment you unlink.
          </p>
        </div>
      </div>
    </div>
    <ModelAlertsCardB linked={linked} />
    </>
  );
}

export default function SettingsB() {
  // Same auth gate as the A Settings page (Rule 0 — identical behavior): an
  // unauthenticated visitor sees a connect prompt, NOT the settings content,
  // so no telegram call fires until authenticated.
  const { login, isLoading, isAuthenticated, isConnected } = useAuth();
  const user = useAuthStore((s) => s.user);
  const addr = user?.wallet_address;

  if (!isAuthenticated && !isConnected) {
    return (
      <div className="screen">
        <div className="head"><div><h1>Settings</h1></div></div>
        <div className="card" style={{ maxWidth: 720, textAlign: 'center', padding: '40px 20px' }}>
          <h2>Connect your wallet</h2>
          <p className="sub" style={{ marginTop: 6 }}>Connect your wallet to manage your notification settings.</p>
          <button className="btn primary" style={{ marginTop: 16 }} onClick={() => login().catch(() => {})} disabled={isLoading}>
            {isLoading ? 'Connecting…' : 'Connect wallet'}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="screen">
      <div className="head">
        <div>
          <h1>Settings</h1>
          <p>
            Notifications for your wallet{' '}
            {addr ? <span className="mono">{shortenAddress(addr)}</span> : <span className="dim">connect a wallet</span>}
          </p>
        </div>
      </div>
      <TelegramCardB />
    </div>
  );
}
