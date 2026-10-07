import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getAutoSettings, putAutoSettings, deleteKey, type AutoCopyDefaults, type AutoCopyUserSettings } from '@/lib/copyAutoApi';

// Settings > Auto-copy (2026-09-30). Backed by GET/PUT /api/copy-auto/settings.
// The master toggle is read by the engine on every leader event: off = no new
// opens or adds, copied positions still close when the trader closes. Defaults
// pre-fill the setup sheet for every new subscription; each subscription keeps
// its own values. Every change saves immediately.

const NUM: [keyof AutoCopyDefaults, string, string, string][] = [
  ['margin_usd', 'Trade amount (margin per trade)', '$', 'Margin put into each copied open. Size = margin × leverage ÷ price.'],
  ['allocation_usd', 'Allocation (max margin in use)', '$', 'Most margin one trader may use at once (proportional sizing and adds).'],
  ['max_leverage', 'Max leverage', 'x', "Lowest of the trader's leverage, this, and Perpl's max for the market."],
  ['max_positions', 'Max open copied positions', '', 'Per trader. Extra opens are skipped.'],
  ['sl_margin_pct', 'Stop loss (% of margin)', '%', 'Protective stop at a move equal to this share of margin. Empty = no stop.'],
  ['tp_pct', 'Take profit (% gain on margin)', '%', 'Saved on every new subscription. The engine does not place a take-profit order yet; exits follow the trader and your stop.'],
  ['daily_loss_usd', 'Daily loss limit', '$', 'Copied trades losing this much today pause the subscription.'],
  ['total_loss_usd', 'Total loss limit', '$', 'Copied trades losing this much in total pause the subscription.'],
  ['drift_pct', 'Price drift limit', '%', "Skip an open when Perpl's price is this much worse than the trader's fill."],
];
const NULLABLE = new Set(['sl_margin_pct', 'tp_pct', 'daily_loss_usd', 'total_loss_usd']);

function NumField({ k, label, unit, help, value, disabled, onSave }: {
  k: string; label: string; unit: string; help: string; value: number | null; disabled: boolean; onSave: (v: number | null) => void;
}) {
  const [draft, setDraft] = useState(value == null ? '' : String(value));
  const commit = () => {
    if (draft.trim() === '') { if (NULLABLE.has(k) && value != null) onSave(null); return; }
    const n = Number(draft);
    if (Number.isFinite(n) && n !== value) onSave(n);
  };
  return (
    <div className="kv" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 4 }}>
      <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, minHeight: 44 }}>
        <span>{label}{unit ? <span className="dim"> ({unit})</span> : null}</span>
        <input className="input" style={{ width: 110, minHeight: 40 }} value={draft} disabled={disabled} inputMode="decimal"
               placeholder={NULLABLE.has(k) ? 'off' : undefined} aria-label={label}
               onChange={(e) => setDraft(e.target.value)} onBlur={commit}
               onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />
      </label>
      <div className="dim" style={{ fontSize: 11.5 }}>{help}</div>
    </div>
  );
}

export default function AutoCopySettingsB() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['copy-auto-settings'], queryFn: getAutoSettings });
  const [err, setErr] = useState<string | null>(null);
  const [confirmKey, setConfirmKey] = useState(false);
  const onOk = (d: AutoCopyUserSettings) => { setErr(null); qc.setQueryData(['copy-auto-settings'], d); qc.invalidateQueries({ queryKey: ['copy-auto-subs'] }); };
  const onErr = (e: any) => setErr(e?.response?.data?.detail || 'Saving failed. Try again.');
  const save = useMutation({ mutationFn: putAutoSettings, onSuccess: onOk, onError: onErr });
  const removeKey = useMutation({
    mutationFn: deleteKey,
    onSuccess: () => { setConfirmKey(false); qc.invalidateQueries({ queryKey: ['copy-auto-settings'] }); qc.invalidateQueries({ queryKey: ['copy-auto-subs'] }); },
    onError: onErr,
  });

  const d = q.data;
  const df = d?.defaults;
  const busy = save.isPending;
  const setDefault = (patch: Partial<AutoCopyDefaults>) => save.mutate({ defaults: patch });
  const pickPreset = (p: string) => d && setDefault({ ...(d.presets[p] as Partial<AutoCopyDefaults>) });
  const activePreset = df && d ? Object.entries(d.presets).find(([, v]) =>
    Object.entries(v).every(([k, val]) => (df as any)[k] === val))?.[0] : undefined;

  return (
    <div className="card" style={{ maxWidth: 720, marginTop: 12 }}>
      <h2>Auto-copy</h2>
      <div className="sub">
        Copies a Hyperliquid trader's opens, adds, cuts and closes onto your Perpl account. Changes save at once and apply to the next trade the trader makes.
      </div>
      {q.isError && <div className="banner short" style={{ marginTop: 8 }}>Auto-copy settings unavailable — retrying.</div>}
      {err && <div className="banner short" style={{ marginTop: 8 }}>{err}</div>}
      {q.isLoading && <div className="dim" style={{ marginTop: 10, fontSize: 12.5 }}>Loading…</div>}

      {d && df && (
        <>
          <label className="kv" style={{ cursor: 'pointer', minHeight: 48, borderTop: 0 }}>
            <span>
              <b>Auto-copy {d.enabled ? 'on' : 'off'}</b>
              <span className="dim" style={{ display: 'block', fontSize: 12 }}>
                {d.enabled ? 'Your subscriptions copy new trades.' : 'Paused: no new opens or adds. Copied positions still close when the trader closes.'}
              </span>
            </span>
            <input type="checkbox" checked={d.enabled} disabled={busy} aria-label="Auto-copy on/off"
                   style={{ width: 24, height: 24 }} onChange={() => save.mutate({ enabled: !d.enabled })} />
          </label>
          {d.platform_paused && (
            <div className="banner" style={{ marginTop: 8, borderColor: 'var(--warn, #d97706)', color: 'var(--warn, #d97706)' }}>
              Auto-copy is paused by the platform. Live subscriptions place no orders until the platform switch is back on; Shadow keeps logging what it would do.
            </div>
          )}
          {!d.allowlisted && (
            <div className="dim" style={{ fontSize: 12, marginTop: 8 }}>Auto-copy is allowlist-only for now; your defaults are saved for when it opens.</div>
          )}

          <div style={{ marginTop: 16 }}>
            <div className="dim" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>Defaults for new subscriptions</div>
            <div className="seg" style={{ marginBottom: 6 }}>
              {['careful', 'standard', 'active'].map((p) => (
                <button key={p} aria-pressed={activePreset === p} disabled={busy} onClick={() => pickPreset(p)}
                        style={{ minHeight: 44, textTransform: 'capitalize' }}>{p}</button>
              ))}
            </div>
            <div className="dim" style={{ fontSize: 11.5 }}>A preset fills the fields below; every field stays editable. Each subscription can override its own values in its setup sheet.</div>

            <div className="kv" style={{ flexWrap: 'wrap', gap: 8, minHeight: 48 }}>
              <span>Default mode</span>
              <div className="seg">
                <button aria-pressed={df.mode === 'shadow'} disabled={busy} style={{ minHeight: 44 }} onClick={() => setDefault({ mode: 'shadow' })}>Shadow</button>
                <button aria-pressed={df.mode === 'live'} disabled={busy} style={{ minHeight: 44 }} onClick={() => setDefault({ mode: 'live' })}>Live</button>
              </div>
            </div>
            <div className="dim" style={{ fontSize: 11.5 }}>
              Shadow logs what would be copied and places nothing. Live places real orders{d.key.present ? '' : ' — new subscriptions start in Shadow until you add an auto-copy key'}.
            </div>

            <div className="kv" style={{ flexWrap: 'wrap', gap: 8, minHeight: 48 }}>
              <span>Sizing</span>
              <div className="seg">
                <button aria-pressed={df.sizing === 'fixed'} disabled={busy} style={{ minHeight: 44 }} onClick={() => setDefault({ sizing: 'fixed' })}>Fixed amount</button>
                <button aria-pressed={df.sizing === 'proportional'} disabled={busy} style={{ minHeight: 44 }} onClick={() => setDefault({ sizing: 'proportional' })}>Proportional</button>
              </div>
            </div>

            {NUM.map(([k, label, unit, help]) => (
              <NumField key={`${k}:${String(df[k])}`} k={k} label={label} unit={unit} help={help}
                        value={(df[k] as number | null) ?? null} disabled={busy}
                        onSave={(v) => setDefault({ [k]: v } as Partial<AutoCopyDefaults>)} />
            ))}

            {([['mirror_adds', 'Mirror adds', 'Trader adds: add the same fraction (capped by allocation).'],
               ['mirror_reduces', 'Mirror reduces', 'Trader cuts 30%: cut 30% of yours.'],
               ['reopen_on_flip', 'Re-open on flip', 'Trader flips: close yours, then open the new side if it passes every check.']] as const).map(([k, label, help]) => (
              <label key={k} className="kv" style={{ cursor: 'pointer', minHeight: 48 }}>
                <span>{label}<span className="dim" style={{ display: 'block', fontSize: 11.5 }}>{help}</span></span>
                <input type="checkbox" checked={!!df[k]} disabled={busy} aria-label={label}
                       style={{ width: 22, height: 22 }} onChange={() => setDefault({ [k]: !df[k] } as Partial<AutoCopyDefaults>)} />
              </label>
            ))}
          </div>

          <div style={{ marginTop: 16 }}>
            <div className="dim" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>Auto-copy key</div>
            {d.key.present ? (
              <>
                <div className="kv"><span>Status</span><span className="tag long">Connected · <span className="mono">{d.key.public_key?.slice(0, 12)}…</span></span></div>
                <div className="kv"><span>Last used</span><span className="dim">{d.key.last_used_at ? new Date(d.key.last_used_at).toLocaleString() : 'not used yet'}</span></div>
                {d.key.last_error && <div className="kv"><span>Last error</span><span className="short-c" style={{ fontSize: 12.5 }}>{d.key.last_error}</span></div>}
                {!confirmKey ? (
                  <button className="btn" style={{ marginTop: 10, minHeight: 44 }} onClick={() => setConfirmKey(true)}>Remove key</button>
                ) : (
                  <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                    <span style={{ fontSize: 12.5 }}>Removing the key pauses every Live subscription now.</span>
                    <button className="btn" style={{ minHeight: 44, color: 'var(--short)' }} disabled={removeKey.isPending} onClick={() => removeKey.mutate()}>
                      {removeKey.isPending ? 'Removing…' : 'Remove key'}
                    </button>
                    <button className="btn ghost" style={{ minHeight: 44 }} onClick={() => setConfirmKey(false)}>Keep</button>
                  </div>
                )}
              </>
            ) : (
              <div className="kv" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 4 }}>
                <span><span className="tag flat">No key</span></span>
                <span className="dim" style={{ fontSize: 12 }}>Live auto-copy needs its own Perpl trade key. Create it from the Auto-copy button on any Hyperliquid trader (one wallet signature, can never withdraw).</span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
