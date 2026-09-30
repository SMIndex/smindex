import { useEffect, useMemo, useState } from 'react';
import { clsx } from 'clsx';
import { useSignTypedData } from 'wagmi';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Modal from '@/components/common/Modal';
import { shortenAddress } from '@/lib/formatters';
import { enrollApiKey } from '@/lib/perplApiKey';
import {
  getPresets, getAvailability, getAutoSettings, createAutoSub, depositKey, deleteKey, summarySentence,
  type AutoCopySettings,
} from '@/lib/copyAutoApi';

// Auto-copy setup sheet (spec 2.10-2.11): preset picker -> editable form with
// plain-language help -> summary sentence -> Shadow / Live -> key status.

const HELP: Record<string, string> = {
  margin_usd: 'Margin put into each copied open. Size = margin × leverage ÷ price.',
  allocation_usd: 'Most margin this trader may use at once across all copied positions.',
  max_leverage: 'Leverage used = the lowest of the trader\'s leverage, this, and Perpl\'s max for the market.',
  max_positions: 'Copied positions open at the same time for this trader. Extra opens are skipped.',
  drift_pct: 'Skip an open if Perpl\'s price is this much worse than the trader\'s fill.',
  sl_margin_pct: 'Protective stop at a price move equal to this share of your margin (50% at 5x = a 10% move). Empty = no stop.',
  daily_loss_usd: 'When copied trades lose this much today, the subscription pauses itself.',
  total_loss_usd: 'When copied trades lose this much in total, the subscription pauses itself.',
};

const NUM_FIELDS: [keyof AutoCopySettings, string, string][] = [
  ['margin_usd', 'Margin per trade', '$'], ['allocation_usd', 'Allocation (max margin in use)', '$'],
  ['max_leverage', 'Max leverage', 'x'], ['max_positions', 'Max open copied positions', ''],
  ['drift_pct', 'Price drift limit', '%'], ['sl_margin_pct', 'Stop loss (% of margin)', '%'],
  ['daily_loss_usd', 'Daily loss limit', '$'], ['total_loss_usd', 'Total loss limit', '$'],
];

export default function AutoCopySetupModal({ isOpen, onClose, leaderWallet, leaderName, address }: {
  isOpen: boolean; onClose: () => void; leaderWallet: string; leaderName?: string; address: string;
}) {
  const qc = useQueryClient();
  const { signTypedDataAsync } = useSignTypedData();
  const presets = useQuery({ queryKey: ['copy-auto-presets'], queryFn: getPresets, enabled: isOpen });
  const avail = useQuery({ queryKey: ['copy-auto-avail', leaderWallet], queryFn: () => getAvailability(leaderWallet), enabled: isOpen });
  const userSettings = useQuery({ queryKey: ['copy-auto-settings'], queryFn: getAutoSettings, enabled: isOpen });
  const [preset, setPreset] = useState<string | null>(null);
  const [form, setForm] = useState<AutoCopySettings | null>(null);
  const [mode, setMode] = useState<'shadow' | 'live'>('shadow');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  // Pre-filled from the user's Settings > Auto-copy defaults; a preset button overrides them.
  useEffect(() => {
    if (presets.data && userSettings.data && !form) {
      const { mode: dm, ...dfl } = userSettings.data.defaults;
      setForm({ ...presets.data.defaults, ...dfl } as AutoCopySettings);
      if (dm === 'live' && userSettings.data.key.present) setMode('live');
    }
  }, [presets.data, userSettings.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (p: string) => {
    setPreset(p);
    if (presets.data) setForm({ ...presets.data.defaults, ...presets.data.presets[p] } as AutoCopySettings);
  };
  const set = (k: keyof AutoCopySettings, v: any) => form && setForm({ ...form, [k]: v });
  const summary = useMemo(() => (form ? summarySentence(form) : ''), [form]);
  const keyPresent = !!avail.data?.key?.present;

  const createKey = async () => {
    setErr(null); setBusy('key');
    try {
      const k = await enrollApiKey({ address, signTypedDataAsync, scopeMask: 2, label: 'SMINDEX auto-copy', store: false });
      await depositKey({ api_key: k.apiKey, seed_hex: k.privHex, pub_hex: k.pubHex, scope: k.scopeMask, label: k.label });
      await qc.invalidateQueries({ queryKey: ['copy-auto-avail', leaderWallet] });
    } catch (e: any) {
      setErr(e?.response?.data?.detail || e?.message || 'Key setup failed');
    } finally { setBusy(null); }
  };
  const removeKey = async () => {
    setBusy('key');
    try { await deleteKey(); await qc.invalidateQueries({ queryKey: ['copy-auto-avail', leaderWallet] }); setMode('shadow'); }
    finally { setBusy(null); }
  };
  const save = async () => {
    if (!form) return;
    setErr(null); setBusy('save');
    try {
      const sub = await createAutoSub(leaderWallet, null, form, mode);
      if (mode === 'live' && sub.mode !== 'live') throw { response: { data: { detail: 'Started in Shadow: add your auto-copy key to go Live.' } } };
      await qc.invalidateQueries({ queryKey: ['copy-auto-subs'] });
      setDone(mode === 'live' ? 'Auto-copy is LIVE for this trader.' : 'Shadow auto-copy started — no orders are placed.');
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Saving failed');
    } finally { setBusy(null); }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Auto-copy" maxWidth="max-w-lg">
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg bg-bg-secondary/60 border border-text-secondary/10">
          <div>
            <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">Auto-copying</div>
            <div className="text-sm font-semibold text-text-primary">{leaderName || shortenAddress(leaderWallet)}</div>
          </div>
          <span className={clsx('text-[10px] font-medium px-2 py-1 rounded-full border',
            mode === 'live' ? 'bg-danger/10 text-danger border-danger/30' : 'bg-accent/10 text-accent border-accent/20')}>
            {mode === 'live' ? 'Live' : 'Shadow'}
          </span>
        </div>

        {avail.data && !avail.data.available && (
          <div className="px-3 py-2 rounded-lg bg-warning/10 border border-warning/30 text-[12px] text-warning">
            Unavailable: {avail.data.reason}
          </div>
        )}
        {done && <div className="px-3 py-2 rounded-lg bg-success/10 border border-success/30 text-[12px] text-success">{done}</div>}
        {err && <div className="px-3 py-2 rounded-lg bg-danger/10 border border-danger/30 text-[12px] text-danger">{err}</div>}

        <div>
          <label className="block text-xs font-medium text-text-secondary mb-1">Preset</label>
          <div className="flex gap-2">
            {['careful', 'standard', 'active'].map((p) => (
              <button key={p} onClick={() => pick(p)}
                      className={clsx('flex-1 text-xs font-medium py-2.5 rounded-lg border capitalize min-h-[44px]',
                        preset === p ? 'bg-accent/15 text-accent border-accent/30' : 'bg-bg-secondary text-text-secondary border-text-secondary/15')}>
                {p}
              </button>
            ))}
          </div>
        </div>

        {form && (
          <>
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">Sizing</label>
              <div className="flex gap-2">
                {(['fixed', 'proportional'] as const).map((s) => (
                  <button key={s} onClick={() => set('sizing', s)}
                          className={clsx('flex-1 text-xs py-2.5 rounded-lg border min-h-[44px]',
                            form.sizing === s ? 'bg-accent/15 text-accent border-accent/30' : 'bg-bg-secondary text-text-secondary border-text-secondary/15')}>
                    {s === 'fixed' ? 'Fixed margin per trade' : 'Proportional to the trader'}
                  </button>
                ))}
              </div>
              <p className="text-[11px] text-text-secondary/70 mt-1">
                {form.sizing === 'fixed' ? 'Every copied open uses the same margin.' : 'Size = trader size × (your allocation ÷ trader account value).'}
              </p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {NUM_FIELDS.map(([k, label, unit]) => (
                <div key={k}>
                  <label className="block text-xs font-medium text-text-secondary mb-1">{label}{unit ? ` (${unit})` : ''}</label>
                  <input type="number" inputMode="decimal" className="input-field w-full min-h-[44px]"
                         value={(form[k] as any) ?? ''} placeholder={k === 'sl_margin_pct' ? 'off' : undefined}
                         onChange={(e) => set(k, e.target.value === '' ? null : Number(e.target.value))} />
                  <p className="text-[11px] text-text-secondary/70 mt-1">{HELP[k]}</p>
                </div>
              ))}
            </div>
            <div className="space-y-2">
              {([['mirror_adds', 'Mirror adds', 'When the trader adds, add the same fraction (capped by your allocation).'],
                 ['mirror_reduces', 'Mirror reduces', 'When the trader cuts 30%, cut 30% of yours.'],
                 ['reopen_on_flip', 'Re-open on flip', 'Trader flips long→short: close yours, then open the new side if it passes every check.']] as const)
                .map(([k, label, help]) => (
                  <label key={k} className="flex items-start justify-between gap-3 min-h-[44px] cursor-pointer">
                    <span><span className="text-sm text-text-primary">{label}</span>
                      <span className="block text-[11px] text-text-secondary/70">{help}</span></span>
                    <input type="checkbox" className="mt-1 w-5 h-5" checked={!!form[k]} onChange={() => set(k, !form[k])} />
                  </label>
                ))}
              <p className="text-[11px] text-text-secondary/70">Full closes are always mirrored and cancel our stop loss. Positions the trader opened before you subscribed are never copied.</p>
            </div>
            <div className="px-3 py-2.5 rounded-lg bg-bg-secondary/60 border border-text-secondary/10 text-[12.5px] text-text-primary">{summary}</div>
          </>
        )}

        <div>
          <label className="block text-xs font-medium text-text-secondary mb-1">Mode</label>
          <div className="flex gap-2">
            <button onClick={() => setMode('shadow')}
                    className={clsx('flex-1 text-xs py-2.5 rounded-lg border min-h-[44px]',
                      mode === 'shadow' ? 'bg-accent/15 text-accent border-accent/30' : 'bg-bg-secondary text-text-secondary border-text-secondary/15')}>
              Shadow — log what would be copied, place nothing
            </button>
            <button onClick={() => keyPresent && setMode('live')} disabled={!keyPresent}
                    className={clsx('flex-1 text-xs py-2.5 rounded-lg border min-h-[44px] disabled:opacity-50',
                      mode === 'live' ? 'bg-danger/15 text-danger border-danger/30' : 'bg-bg-secondary text-text-secondary border-text-secondary/15')}>
              Live — place real orders
            </button>
          </div>
          {avail.data && !avail.data.live_switch_on && (
            <p className="text-[11px] text-warning mt-1">Live auto-copy is switched off on the server right now; Live subscriptions only log until it is enabled.</p>
          )}
        </div>

        <div className="px-3 py-3 rounded-lg border border-text-secondary/15 space-y-2">
          <div className="text-xs font-semibold text-text-primary">Perpl key for auto-copy {keyPresent ? '— connected' : ''}</div>
          {keyPresent ? (
            <>
              <p className="text-[11px] text-text-secondary">Key <span className="font-mono">{avail.data?.key.public_key?.slice(0, 12)}…</span>
                {avail.data?.key.last_used_at ? ` · last used ${new Date(avail.data.key.last_used_at).toLocaleString()}` : ' · not used yet'}
                {avail.data?.key.last_error ? ` · last error: ${avail.data.key.last_error}` : ''}</p>
              <button onClick={removeKey} disabled={busy === 'key'} className="text-xs px-3 py-2 rounded-lg bg-danger/15 text-danger border border-danger/30 min-h-[44px]">
                Delete key (stops Live auto-copy immediately)
              </button>
            </>
          ) : (
            <>
              <p className="text-[11px] text-text-secondary leading-relaxed">
                Live auto-copy needs a <b>separate Perpl API key</b> with <b>trade</b> permission. It can place, change and cancel
                orders on your Perpl account. It <b>cannot withdraw or move funds</b> — Perpl never allows that with an API key; withdrawals
                need your wallet signature. The key is stored encrypted on SMINDEX servers, used only by the auto-copy worker, never shown
                again, and you can delete it here in one tap (or revoke it at app.perpl.xyz/apikeys). Your wallet signs once to create it.
              </p>
              <label className="flex items-start gap-2 text-[12px] text-text-primary min-h-[44px] cursor-pointer">
                <input type="checkbox" className="mt-0.5 w-5 h-5" checked={consent} onChange={() => setConsent(!consent)} />
                I understand this key lets SMINDEX place and cancel orders on my Perpl account for auto-copy.
              </label>
              <button onClick={createKey} disabled={!consent || busy === 'key'}
                      className="text-xs px-3 py-2 rounded-lg bg-accent/15 text-accent border border-accent/30 min-h-[44px] disabled:opacity-50">
                {busy === 'key' ? 'Waiting for wallet…' : 'Create auto-copy key'}
              </button>
            </>
          )}
        </div>

        <button onClick={save} disabled={!form || !avail.data?.available || busy === 'save'}
                className={clsx('w-full py-3 rounded-lg text-sm font-semibold min-h-[48px] disabled:opacity-50',
                  mode === 'live' ? 'bg-danger text-white' : 'bg-accent text-white')}>
          {busy === 'save' ? 'Saving…' : mode === 'live' ? 'Start LIVE auto-copy' : 'Start Shadow auto-copy'}
        </button>
      </div>
    </Modal>
  );
}
